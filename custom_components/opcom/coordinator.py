from __future__ import annotations

import datetime as dt
import logging
import time
from datetime import timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.helpers.event import async_track_point_in_time

from .api import OpcomFetchError, create_opcom_session, fetch_and_parse_day
from .const import (
    DOMAIN,
    LICENSE_DATA_KEY,
    CONF_LANG,
    CONF_RESOLUTIONS,
    CONF_DAYS_AHEAD,
    CONF_SCAN_INTERVAL,
    CONF_WINDOW_MINUTES,
    CONF_TOP_N_WINDOWS,
    CONF_TOP_N_PER_RES,
    CONF_PRICE_THRESHOLD_LOW,
    CONF_PRICE_THRESHOLD_HIGH,
    DEFAULT_LANG,
    DEFAULT_RESOLUTIONS,
    DEFAULT_DAYS_AHEAD,
    DEFAULT_SCAN_INTERVAL_MIN,
    DEFAULT_WINDOW_MINUTES,
    DEFAULT_TOP_N_WINDOWS,
    DEFAULT_PRICE_THRESHOLD_LOW,
    DEFAULT_PRICE_THRESHOLD_HIGH,
    OPCOM_CSV_URL,
    OPCOM_TIMEZONE,
    OpcomSettings,
)
from .helpers import parse_top_n_per_res

_LOGGER = logging.getLogger(__name__)

# Rezultatele PZU pentru ziua următoare apar după licitația SDAC (închidere
# 12:00 CET, publicare în jur de 13:00). Înainte de ora asta nu are rost să cerem
# ziua de mâine — OPCOM răspunde cu un fișier gol.
_ORA_PUBLICARE_MAINE_CET = 12

# Când serverul OPCOM refuză clientul (403 / 429 — de obicei IP-ul rețelei blocat de WAF), nu mai trimitem
# cereri o vreme: 30 min după primul refuz, apoi 60 min. Fără pauză, reîncercările de pornire ale Home
# Assistant (la cel mult 80 s, la nesfârșit) loveau WAF-ul de mii de ori pe zi și puteau prelungi blocarea.
_PAUZE_BLOCARE = (timedelta(minutes=30), timedelta(minutes=60))
_BLOCARE_KEY = f"{DOMAIN}_blocare"


def stare_blocare(hass: HomeAssistant) -> dict[str, Any]:
    """Starea refuzului, comună domeniului: supraviețuiește reîncercărilor de pornire (alt coordinator)."""
    return hass.data.setdefault(
        _BLOCARE_KEY, {"pana_la": None, "episoade": 0, "status": None, "detail": None}
    )


def _rezolutie_completa(res_obj: Any) -> bool:
    """True dacă rezoluția are intervale și toate au preț."""
    if not isinstance(res_obj, dict):
        return False
    rows = (res_obj.get("intervals") or {}).get("rows")
    if not isinstance(rows, list) or not rows:
        return False
    return all(r.get("pret_lei_mwh") is not None for r in rows)


class OpcomCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Coordinator OPCOM: fetch + parse centralizat, date reutilizate de entități.

    Strategia de refresh:
      1. update_interval — la scan_interval_minutes descarcă DOAR ce lipsește
         (ziua de mâine până la publicare, rezoluții eșuate). Prețurile PZU ale
         unei zile publicate nu se mai schimbă, deci o zi completă se descarcă o
         singură dată (înainte: 6 CSV-uri la fiecare 15 minute, chiar de două ori).
      2. Timer la granița intervalului cel mai fin — actualizează doar stările
         entităților (intervalul curent), fără rețea, dacă azi e deja descărcat.

    La erori de rețea se păstrează zilele deja descărcate (înainte: toate
    entitățile deveneau indisponibile).

    Contor monoton (_data_version):
      Incrementat când datele se schimbă. Entitățile îl folosesc pentru
      invalidarea cache-ului, eliminând fragilitatea id(data).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.settings = self._read_settings(entry)

        # Un singur timer — la cea mai fină rezoluție configurată.
        # Elimină timerele redundante (3 fetch-uri la aceeași oră fixă).
        self._unsub_boundary_timer: Callable | None = None
        self._finest_resolution: int = min(self.settings.resolutions)

        # Contor monoton pentru invalidarea cache-ului în entități.
        # Incrementat când datele se schimbă. Nu poate produce false cache hits
        # (spre deosebire de id(data) care recicla adrese de memorie).
        self._data_version: int = 0

        # Zilele descărcate (cheie YYYY-MM-DD, CET) — vezi _async_update_data
        self._zile: dict[str, dict[str, Any]] = {}
        self._indisponibil_raportat = False

        # Sesiune proprie, fără cookie-uri (WAF-ul OPCOM — vezi create_opcom_session)
        self._session = create_opcom_session(hass)

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}:{entry.entry_id}",
            # La fiecare scan_interval_minutes se descarcă doar datele lipsă
            # (vezi _async_update_data); fără rețea când zilele sunt complete.
            update_interval=timedelta(minutes=self.settings.scan_interval_minutes),
        )

        _LOGGER.debug(
            "OPCOM: coordinator pornit (entry=%s). Fallback polling=%s min, "
            "timer graniță=%s min. Setări: %s",
            entry.entry_id,
            self.settings.scan_interval_minutes,
            self._finest_resolution,
            self.settings,
        )

        # NU pornim timer-ul de graniță aici — __init__.py îl pornește DUPĂ first_refresh.

    @property
    def data_version(self) -> int:
        """Contor monoton incrementat la fiecare refresh reușit."""
        return self._data_version

    def _read_settings(self, entry: ConfigEntry) -> OpcomSettings:
        """Citește setările din ConfigEntry (options > data > defaults)."""
        def _get(key: str, default: Any) -> Any:
            return entry.options.get(key, entry.data.get(key, default))

        def _src(key: str) -> str:
            if key in entry.options:
                return "options"
            if key in entry.data:
                return "data"
            return "default"

        lang_raw = _get(CONF_LANG, DEFAULT_LANG)
        lang = str(lang_raw).strip().lower() or DEFAULT_LANG

        resolutions_raw = _get(CONF_RESOLUTIONS, DEFAULT_RESOLUTIONS)
        if isinstance(resolutions_raw, list):
            resolutions: list[int] = []
            for x in resolutions_raw:
                try:
                    v = int(x)
                except Exception:
                    continue
                if v in (15, 30, 60):
                    resolutions.append(v)
        else:
            resolutions = DEFAULT_RESOLUTIONS.copy()

        resolutions = sorted(set(resolutions)) or DEFAULT_RESOLUTIONS.copy()

        def _to_int(val: Any, default: int) -> int:
            try:
                return int(val)
            except Exception:
                return int(default)

        def _to_float(val: Any, default: float | None) -> float | None:
            if val is None:
                return default
            s = str(val).strip()
            if s == "" or s.lower() in ("none", "null"):
                return default
            try:
                return float(s)
            except Exception:
                return default

        days_ahead = _to_int(_get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD), DEFAULT_DAYS_AHEAD)
        scan_interval = _to_int(_get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN), DEFAULT_SCAN_INTERVAL_MIN)
        window_minutes = _to_int(_get(CONF_WINDOW_MINUTES, DEFAULT_WINDOW_MINUTES), DEFAULT_WINDOW_MINUTES)
        top_n = _to_int(_get(CONF_TOP_N_WINDOWS, DEFAULT_TOP_N_WINDOWS), DEFAULT_TOP_N_WINDOWS)

        price_threshold_low = _to_float(
            _get(CONF_PRICE_THRESHOLD_LOW, DEFAULT_PRICE_THRESHOLD_LOW),
            DEFAULT_PRICE_THRESHOLD_LOW,
        )
        price_threshold_high = _to_float(
            _get(CONF_PRICE_THRESHOLD_HIGH, DEFAULT_PRICE_THRESHOLD_HIGH),
            DEFAULT_PRICE_THRESHOLD_HIGH,
        )

        # Clamp defensiv
        if lang not in ("ro", "en"):
            _LOGGER.debug("OPCOM: limba '%s' nu pare validă; revin la '%s'.", lang, DEFAULT_LANG)
            lang = DEFAULT_LANG

        days_ahead = max(1, min(days_ahead, 2))
        scan_interval = max(5, min(scan_interval, 180))
        window_minutes = max(15, window_minutes)
        top_n = max(1, min(top_n, 24))

        # Per-resolution top_n (opțional, string „15:4,30:6,60:2")
        top_n_per_res_raw = _get(CONF_TOP_N_PER_RES, "")
        top_n_per_res = parse_top_n_per_res(top_n_per_res_raw)
        top_n_per_res = {
            r: max(1, min(n, 24)) for r, n in top_n_per_res.items()
        }

        settings = OpcomSettings(
            lang=lang,
            resolutions=resolutions,
            days_ahead=days_ahead,
            scan_interval_minutes=scan_interval,
            window_minutes=window_minutes,
            top_n_windows=top_n,
            top_n_per_res=top_n_per_res,
            price_threshold_low=price_threshold_low,
            price_threshold_high=price_threshold_high,
        )

        _LOGGER.debug(
            "OPCOM: setări citite (surse: lang=%s, res=%s, days=%s, scan=%s, window=%s, "
            "top_n=%s, top_n_per_res=%s, prag_low=%s, prag_high=%s). "
            "Rezultat: %s",
            _src(CONF_LANG), _src(CONF_RESOLUTIONS), _src(CONF_DAYS_AHEAD),
            _src(CONF_SCAN_INTERVAL), _src(CONF_WINDOW_MINUTES),
            _src(CONF_TOP_N_WINDOWS), _src(CONF_TOP_N_PER_RES),
            _src(CONF_PRICE_THRESHOLD_LOW), _src(CONF_PRICE_THRESHOLD_HIGH),
            settings,
        )

        return settings

    def _extract_day_debug(self, day_obj: dict[str, Any]) -> dict[str, Any]:
        """Extrage rezumat debug compact pentru o zi."""
        out: dict[str, Any] = {}
        if not isinstance(day_obj, dict):
            return {"_invalid_day_obj": True}

        if "title" in day_obj:
            out["title"] = day_obj.get("title")

        res_map = day_obj.get("resolutions")
        if not isinstance(res_map, dict):
            out["resolutions"] = "lipsă/invalid"
            return out

        res_debug: dict[str, Any] = {}
        for res_k, res_obj in res_map.items():
            if not isinstance(res_obj, dict):
                res_debug[str(res_k)] = {"_invalid_res_obj": True}
                continue

            intervals = res_obj.get("intervals", {})
            count = None
            if isinstance(intervals, dict):
                count = intervals.get("count")
                if count is None:
                    rows = intervals.get("rows")
                    if isinstance(rows, list):
                        count = len(rows)

            has_summary = isinstance(res_obj.get("summary"), dict) and bool(res_obj.get("summary"))
            res_debug[str(res_k)] = {"count": count, "has_summary": has_summary}

        out["resolutions"] = res_debug
        return out

    def _rezolutii_lipsa(self, cheie_zi: str) -> list[int]:
        """Rezoluțiile configurate care nu sunt (complet) descărcate pentru ziua dată."""
        day_obj = self._zile.get(cheie_zi) or {}
        res_map = day_obj.get("resolutions") or {}
        return [
            r for r in self.settings.resolutions
            if not _rezolutie_completa(res_map.get(str(r)))
        ]

    def _integreaza_zi(self, cheie_zi: str, day_obj: dict[str, Any]) -> bool:
        """Adaugă rezoluțiile nou descărcate în cache. Returnează True dacă s-a schimbat ceva."""
        noi = {
            k: v for k, v in (day_obj.get("resolutions") or {}).items()
            if (v.get("intervals") or {}).get("count")
        }
        if not noi:
            return False  # fișier gol — ziua nu e încă publicată
        zi = self._zile.setdefault(cheie_zi, {"date": cheie_zi, "resolutions": {}})
        zi["resolutions"].update(noi)
        if day_obj.get("title"):
            zi["title"] = day_obj["title"]
        zi["fetched_at"] = dt_util.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        return True

    async def _async_update_data(self) -> dict[str, Any]:
        """
        Descarcă doar ce lipsește și întoarce datele din cache.

        UpdateFailed numai dacă nu există nicio zi descărcată; altfel, la erori,
        se păstrează datele existente (prețurile unei zile publicate sunt fixe).
        HA reîncearcă la următorul update_interval (la pornire: ConfigEntryNotReady).
        """
        # Verificare licență — nu fetchuim date dacă licența/trial nu e validă
        license_mgr = self.hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
        if license_mgr and not license_mgr.is_valid:
            _LOGGER.debug("[OPCOM] Licență invalidă — se omit apelurile API")
            return self.data or {}

        t0 = time.perf_counter()
        tz = dt_util.get_time_zone(OPCOM_TIMEZONE)
        now_cet = dt_util.now(tz)
        base_day = now_cet.date()
        zile = [base_day + dt.timedelta(days=o) for o in range(self.settings.days_ahead)]
        chei = [d.strftime("%Y-%m-%d") for d in zile]

        # Zilele trecute ies din cache
        for cheie in list(self._zile):
            if cheie not in chei:
                del self._zile[cheie]

        erori: list[str] = []
        schimbat = False
        descarcate: list[str] = []
        blocare = stare_blocare(self.hass)
        blocat = blocare["pana_la"] is not None and dt_util.utcnow() < blocare["pana_la"]
        if blocat:
            _LOGGER.debug(
                "OPCOM: cereri suspendate până la %s (serverul a refuzat cu HTTP %s).",
                dt_util.as_local(blocare["pana_la"]).strftime("%H:%M"), blocare["status"],
            )

        for offset, (day, cheie) in enumerate(zip(zile, chei)):
            lipsa = self._rezolutii_lipsa(cheie)
            if not lipsa:
                continue
            if offset > 0 and now_cet.hour < _ORA_PUBLICARE_MAINE_CET:
                continue  # rezultatele pentru mâine nu sunt încă publicate
            if blocat:
                continue  # serverul ne refuză: nu trimitem nicio cerere până la sfârșitul pauzei
            try:
                day_obj = await fetch_and_parse_day(self._session, day, self.settings, lipsa)
            except OpcomFetchError as err:
                erori.append(f"{cheie}: {err}")
                if err.blocked:
                    self._inregistreaza_blocare(err, day)
                    blocat = True  # nici zilele următoare nu le mai cerem
                continue
            if blocare["episoade"]:
                _LOGGER.info(
                    "OPCOM: serverul opcom.ro acceptă din nou cererile (după %d %s).",
                    blocare["episoade"], "refuz" if blocare["episoade"] == 1 else "refuzuri",
                )
                blocare.update(pana_la=None, episoade=0, status=None, detail=None)
            if self._integreaza_zi(cheie, day_obj):
                schimbat = True
                descarcate.append(cheie)
                _LOGGER.debug(
                    "OPCOM: ziua %s descărcată. Rezumat: %s",
                    cheie, self._extract_day_debug(self._zile[cheie]),
                )

        if blocat and not self._zile:
            # Fără date în cache: actualizarea eșuează, dar fără nicio cerere (și fără ERROR repetat —
            # refuzul a fost raportat o dată, cu detalii, în _inregistreaza_blocare)
            raise UpdateFailed(
                f"opcom.ro refuză temporar cererile (HTTP {blocare['status']}) — reîncerc după "
                f"{dt_util.as_local(blocare['pana_la']).strftime('%H:%M')}"
            )
        if blocat:
            pass  # refuzul e deja raportat (o dată, cu detalii); datele descărcate anterior rămân
        elif erori:
            if not self._zile:
                if not self._indisponibil_raportat:
                    _LOGGER.error("OPCOM: nu am putut descărca prețurile: %s", "; ".join(erori))
                    self._indisponibil_raportat = True
                raise UpdateFailed(f"OPCOM indisponibil: {'; '.join(erori)}")
            if not self._indisponibil_raportat:
                _LOGGER.warning(
                    "OPCOM: descărcare eșuată (%s) — păstrez datele descărcate "
                    "anterior; reîncerc la următoarea actualizare.",
                    "; ".join(erori),
                )
                self._indisponibil_raportat = True
        elif self._indisponibil_raportat:
            _LOGGER.info("OPCOM: descărcarea funcționează din nou.")
            self._indisponibil_raportat = False

        if schimbat or self.data is None:
            self._data_version += 1

        _LOGGER.debug(
            "OPCOM: actualizare (%.2fs, v%s). Descărcate acum: %s. În cache: %s",
            time.perf_counter() - t0, self._data_version, descarcate or "nimic",
            sorted(self._zile),
        )

        return {
            "source": "opcom.ro",
            "generated_at": dt_util.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "base_date": base_day.strftime("%Y-%m-%d"),
            "days": {k: self._zile[k] for k in chei if k in self._zile},
            "settings": {
                "lang": self.settings.lang,
                "resolutions": self.settings.resolutions,
                "days_ahead": self.settings.days_ahead,
                "scan_interval_minutes": self.settings.scan_interval_minutes,
                "window_minutes": self.settings.window_minutes,
                "top_n_windows": self.settings.top_n_windows,
                "top_n_per_res": self.settings.top_n_per_res,
            },
        }

    def _inregistreaza_blocare(self, err: OpcomFetchError, day: dt.date) -> None:
        """Serverul refuză clientul (403 / 429): pauză de cereri și un mesaj clar, o dată pe episod."""
        blocare = stare_blocare(self.hass)
        pauza = _PAUZE_BLOCARE[min(blocare["episoade"], len(_PAUZE_BLOCARE) - 1)]
        blocare["episoade"] += 1
        blocare.update(pana_la=dt_util.utcnow() + pauza, status=err.status, detail=err.detail)
        ora = dt_util.as_local(blocare["pana_la"]).strftime("%H:%M")
        minute = int(pauza.total_seconds() // 60)
        if blocare["episoade"] == 1:
            url = OPCOM_CSV_URL.format(
                dd=day.day, mm=day.month, yyyy=day.year, lang=self.settings.lang, res=self.settings.resolutions[0]
            )
            _LOGGER.warning(
                "OPCOM: serverul opcom.ro refuză cererile acestui Home Assistant (HTTP %s; %s). De obicei "
                "înseamnă că adresa IP a rețelei e blocată temporar de firewall-ul OPCOM — integrarea nu poate "
                "ocoli asta. Nu mai trimit cereri până la %s (pauză %d min). Ca să verifici, deschide în "
                "browser, din aceeași rețea: %s",
                err.status, err.detail or "fără detalii", ora, minute, url,
            )
        else:
            _LOGGER.info(
                "OPCOM: opcom.ro refuză în continuare cererile (HTTP %s, refuzul nr. %d) — următoarea "
                "încercare la %s (pauză %d min).",
                err.status, blocare["episoade"], ora, minute,
            )

    # ------------------------------------------------------------------
    # Timer de graniță — UN SINGUR timer la cea mai fină rezoluție
    # ------------------------------------------------------------------

    def _get_next_boundary(self) -> dt.datetime:
        """
        Calculează următoarea graniță de interval pentru cea mai fină rezoluție.

        Miezul nopții CET/CEST cade mereu la o oră fixă UTC, iar rezoluțiile
        (15/30/60) divid ora → granițele sunt multipli de rezoluție în timpul UTC.
        Calculul în UTC nu sare intervale la schimbarea orei (înainte: ora
        repetată din octombrie nu avea granițe).
        """
        step = self._finest_resolution * 60
        now_ts = dt_util.utcnow().timestamp()
        return dt_util.utc_from_timestamp((int(now_ts // step) + 1) * step)

    def schedule_boundary_timer(self) -> None:
        """
        Pornește (sau repornește) timer-ul de graniță. Punct unic de apel.
        FIX 2: Un singur timer la cea mai fină rezoluție — nu câte unul per rezoluție.
        """
        self._cancel_boundary_timer()

        next_run = self._get_next_boundary()
        self._unsub_boundary_timer = async_track_point_in_time(
            self.hass,
            self._handle_boundary_refresh,
            next_run,
        )
        _LOGGER.debug(
            "OPCOM: timer graniță programat la %s (rezoluție=%s min).",
            next_run, self._finest_resolution,
        )

    def _cancel_boundary_timer(self) -> None:
        """Anulează timer-ul de graniță dacă există."""
        if self._unsub_boundary_timer:
            self._unsub_boundary_timer()
            self._unsub_boundary_timer = None

    async def _handle_boundary_refresh(self, now: dt.datetime) -> None:
        """
        Handler la graniță: actualizează stările + reschedulare.

        Dacă ziua curentă e deja descărcată, doar intervalul curent s-a schimbat
        → entitățile se recalculează fără niciun apel la OPCOM. Altfel (ex. după
        miezul nopții cu days_ahead=1, sau după o eroare) → refresh.

        IMPORTANT: try/finally asigură că timer-ul se reprogramează
        ÎNTOTDEAUNA, chiar dacă refresh-ul eșuează. Fără finally,
        o excepție în async_request_refresh() ar opri permanent timer-ul.
        """
        _LOGGER.debug("OPCOM: timer graniță a tras la %s.", now)

        # Timer-ul e consumat
        self._unsub_boundary_timer = None

        try:
            azi = dt_util.now(dt_util.get_time_zone(OPCOM_TIMEZONE)).strftime("%Y-%m-%d")
            if self._rezolutii_lipsa(azi):
                await self.async_request_refresh()
            else:
                self.async_update_listeners()
        except Exception as err:
            _LOGGER.error("OPCOM: refresh la graniță a eșuat: %s: %s", type(err).__name__, err)
        finally:
            # Re-programăm ÎNTOTDEAUNA, indiferent de succes/eșec
            self.schedule_boundary_timer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_shutdown(self) -> None:
        """
        FIX 7: Shutdown corect — anulăm timer-ul propriu + apelăm super().
        Aceasta asigură că HA curăță și listeners-ii interni ai DataUpdateCoordinator.
        """
        _LOGGER.debug("OPCOM: shutdown coordinator.")
        self._cancel_boundary_timer()
        await super().async_shutdown()
