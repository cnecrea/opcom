from __future__ import annotations

import asyncio
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

from .api import fetch_and_parse_day
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
    OPCOM_TIMEZONE,
    OpcomSettings,
)
from .helpers import parse_top_n_per_res

_LOGGER = logging.getLogger(__name__)

# Numărul maxim de reîncercări la erori de rețea, cu backoff exponențial.
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 5  # secunde


class OpcomCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """
    Coordinator OPCOM: fetch + parse centralizat, date reutilizate de entități.

    Strategia de refresh:
      1. update_interval (fallback) — polling regulat la scan_interval_minutes
      2. Timer la granița intervalului cel mai fin — sincronizare exactă cu piața
      Ambele coexistă: timer-ul asigură precizie, polling-ul asigură reziliență.

    Contor monoton (_data_version):
      Fiecare refresh reușit incrementează contorul. Entitățile îl folosesc
      pentru invalidarea cache-ului, eliminând fragilitatea id(data).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.settings = self._read_settings(entry)

        # Un singur timer — la cea mai fină rezoluție configurată.
        # Elimină timerele redundante (3 fetch-uri la aceeași oră fixă).
        self._unsub_boundary_timer: Callable | None = None
        self._finest_resolution: int = min(self.settings.resolutions)

        # Contor monoton pentru invalidarea cache-ului în entități.
        # Incrementat la fiecare refresh reușit. Nu poate produce false cache hits
        # (spre deosebire de id(data) care recicla adrese de memorie).
        self._data_version: int = 0

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{entry.entry_id}",
            # FIX 1: scan_interval_minutes FUNCȚIONEAZĂ acum ca fallback polling.
            # Dacă timer-ul de graniță eșuează, coordinatorul tot se actualizează.
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

    async def _async_update_data(self) -> dict[str, Any]:
        """
        Fetch + parse cu retry integrat (backoff exponențial).

        La erori de rețea, reîncearcă de până la _MAX_RETRIES ori
        cu delay crescător. Dacă toate eșuează, aruncă UpdateFailed
        și se bazează pe fallback polling (update_interval) pentru
        următoarea încercare.
        """
        # Verificare licență — nu fetchuim date dacă licența/trial nu e validă
        license_mgr = self.hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
        if license_mgr and not license_mgr.is_valid:
            _LOGGER.debug("[OPCOM] Licență invalidă — se omit apelurile API")
            return self.data or {}

        t0 = time.perf_counter()
        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                payload = await self._do_fetch()

                # Incrementăm contorul monoton — DOAR la succes
                self._data_version += 1

                t1 = time.perf_counter()
                _LOGGER.debug(
                    "OPCOM: actualizare completă (%.2fs, încercare %s/%s, v%s). Zile: %s",
                    t1 - t0, attempt, _MAX_RETRIES, self._data_version,
                    list(payload.get("days", {}).keys()),
                )
                return payload

            except Exception as err:
                last_error = err
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    _LOGGER.warning(
                        "OPCOM: eroare la încercare %s/%s: %s. Reîncerc în %s secunde.",
                        attempt, _MAX_RETRIES, err, delay,
                    )
                    await asyncio.sleep(delay)

        t1 = time.perf_counter()
        _LOGGER.error(
            "OPCOM: toate cele %s încercări au eșuat (%.2fs). Ultima eroare: %s: %s",
            _MAX_RETRIES, t1 - t0,
            type(last_error).__name__ if last_error else "?",
            last_error,
        )
        raise UpdateFailed(
            f"Eroare la actualizarea datelor OPCOM după {_MAX_RETRIES} încercări: "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error

    async def _do_fetch(self) -> dict[str, Any]:
        """Fetch efectiv (fără retry — apelat de _async_update_data)."""
        tz = dt_util.get_time_zone(OPCOM_TIMEZONE)
        now_cet = dt_util.now(tz)
        base_day = now_cet.date()

        _LOGGER.debug(
            "OPCOM: încep fetch. Data=%s (CET/CEST). Zile=%s, rezoluții=%s, limba='%s'.",
            base_day, self.settings.days_ahead, self.settings.resolutions, self.settings.lang,
        )

        days: dict[str, Any] = {}

        for offset in range(self.settings.days_ahead):
            day = base_day + dt.timedelta(days=offset)
            day_key = day.strftime("%Y-%m-%d")

            td0 = time.perf_counter()
            try:
                day_obj = await fetch_and_parse_day(self.hass, day, self.settings)
            except Exception as err:
                td1 = time.perf_counter()
                _LOGGER.warning(
                    "OPCOM: nu am putut descărca ziua %s (%.2fs, offset=%s): %s: %s. "
                    "Senzorii pentru această zi vor afișa Unknown.",
                    day_key, td1 - td0, offset,
                    type(err).__name__, err,
                )
                # NU adăugăm ziua în dict → rows_for_day_res() returnează []
                # → senzorii afișează None/Unknown (corect).
                continue

            days[day_key] = day_obj

            td1 = time.perf_counter()
            _LOGGER.debug(
                "OPCOM: ziua %s gata (%.2fs). Rezumat: %s",
                day_key, td1 - td0, self._extract_day_debug(day_obj),
            )

        # Dacă nicio zi nu a reușit, aruncăm UpdateFailed ca să semnalăm
        # coordinatorului că nu avem date valide (retry la următorul poll).
        if not days:
            raise RuntimeError(
                f"Niciuna din cele {self.settings.days_ahead} zile nu a putut fi descărcată."
            )

        return {
            "source": "opcom.ro",
            "generated_at": dt_util.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "base_date": base_day.strftime("%Y-%m-%d"),
            "days": days,
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

    # ------------------------------------------------------------------
    # Timer de graniță — UN SINGUR timer la cea mai fină rezoluție
    # ------------------------------------------------------------------

    def _get_next_boundary(self) -> dt.datetime:
        """
        Calculează următoarea graniță de interval pentru cea mai fină rezoluție.
        Folosește timedelta pentru siguranță la tranziții DST.
        """
        res = self._finest_resolution
        tz = dt_util.get_time_zone(OPCOM_TIMEZONE)
        now_cet = dt_util.now(tz)

        current_slot = now_cet.minute // res
        slot_start_minute = current_slot * res
        slot_start = now_cet.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=slot_start_minute)
        next_run = slot_start + timedelta(minutes=res)

        # Protecție: dacă next_run e în trecut (posibil la DST fall-back), sărim la următorul.
        if next_run <= now_cet:
            next_run += timedelta(minutes=res)

        return next_run

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
        Handler la graniță: refresh + reschedulare.

        IMPORTANT: try/finally asigură că timer-ul se reprogramează
        ÎNTOTDEAUNA, chiar dacă refresh-ul eșuează. Fără finally,
        o excepție în async_request_refresh() ar opri permanent timer-ul.
        """
        _LOGGER.debug("OPCOM: timer graniță a tras la %s.", now)

        # Timer-ul e consumat
        self._unsub_boundary_timer = None

        try:
            await self.async_request_refresh()
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
