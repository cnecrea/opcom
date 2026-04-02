from __future__ import annotations

import asyncio
import csv
import datetime as dt
import logging
import re
from typing import Any, Optional, Tuple

from aiohttp import ClientError, ClientTimeout
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import OPCOM_CSV_URL, OpcomSettings

_LOGGER = logging.getLogger(__name__)

_USER_AGENT = "HomeAssistant-OPCOM/0.1 (+https://github.com/cnecrea/opcom)"
_TIMEOUT = ClientTimeout(total=30)


# ----------------------------
# Helpers (convert / normalize)
# ----------------------------

def _safe_get(row: list[str], idx: int, default: str = "") -> str:
    """Acces sigur la o celulă din rând CSV — evită IndexError."""
    if 0 <= idx < len(row):
        return row[idx]
    return default

def iso_date(d: dt.date) -> str:
    return d.strftime("%Y-%m-%d")


def to_float(s: Any) -> Optional[float]:
    if s is None:
        return None
    s = str(s).strip()
    if s == "" or s == "-" or s.lower() == "null":
        return None

    s = s.replace(" ", "")

    # allow 1.234,56 and 1234.56 etc.
    if "," in s and "." in s:
        # assume "." thousands and "," decimal
        s = s.replace(".", "").replace(",", ".")
    else:
        # assume "," decimal or nothing
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def to_int(s: Any) -> Optional[int]:
    if s is None:
        return None
    s = str(s).strip()
    if s == "" or s == "-" or s.lower() == "null":
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def normalize_cell(c: Any) -> str:
    if c is None:
        return ""
    c = str(c).strip()
    if len(c) >= 2 and c[0] == '"' and c[-1] == '"':
        c = c[1:-1]
    return c.strip()


def normalize_row(row: list[Any]) -> list[str]:
    return [normalize_cell(c) for c in row]


def normalize_header(h: str) -> str:
    return normalize_cell(h).lower()


# ----------------------------
# Domain-specific helpers
# ----------------------------

def parse_title_from_csv(text: str) -> Optional[str]:
    # Example: "PIP si volum tranzactionat pentru ziua de livrare: 20/2/2026"
    m = re.search(r"(PIP.*?livrare:\s*\d{1,2}/\d{1,2}/\d{4})", text, flags=re.IGNORECASE)
    return m.group(1).strip() if m else None


def interval_times(day: dt.date, interval: int, res_minutes: int) -> Tuple[str, str]:
    start = dt.datetime.combine(day, dt.time(0, 0)) + dt.timedelta(minutes=(interval - 1) * res_minutes)
    end = start + dt.timedelta(minutes=res_minutes)
    return start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M")


# ----------------------------
# Fetch (async)
# ----------------------------

async def fetch_csv(hass: HomeAssistant, day: dt.date, resolution: int, lang: str) -> str:
    url = OPCOM_CSV_URL.format(dd=day.day, mm=day.month, yyyy=day.year, lang=lang, res=resolution)
    session = async_get_clientsession(hass)
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/plain,text/csv,*/*",
    }

    _LOGGER.debug("OPCOM: descarc CSV (data=%s, res=%s, lang=%s).", iso_date(day), resolution, lang)

    try:
        async with session.get(url, headers=headers, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            text = await resp.text()

            # Mic sanity-check ca să prinzi rapid “am primit altceva decât CSV”
            if not text.strip():
                _LOGGER.debug("OPCOM: CSV gol (data=%s, res=%s).", iso_date(day), resolution)
            else:
                first_line = text.splitlines()[0] if text.splitlines() else ""
                _LOGGER.debug(
                    "OPCOM: CSV ok (data=%s, res=%s). Prima linie: %s",
                    iso_date(day),
                    resolution,
                    first_line[:160],
                )

            return text

    except (ClientError, asyncio.TimeoutError) as e:
        raise RuntimeError(
            f"Nu pot descărca CSV de la OPCOM (rezoluție={resolution}, dată={iso_date(day)}, lang={lang}): {e}"
        ) from e


# ----------------------------
# CSV parsing
# ----------------------------

def split_sections(rows: list[list[str]]) -> tuple[list[list[str]], list[list[str]]]:
    """
    Returns (summary_rows, interval_rows).
    Detectăm switch când întâlnim header-ul tabelului de intervale:
      - PT15: "Zona de tranzactionare" ...
      - PT30/60: "Interval" ...
    """
    summary: list[list[str]] = []
    intervals: list[list[str]] = []
    mode = "summary"

    for row in rows:
        if not row or all((c or "").strip() == "" for c in row):
            continue
        r0 = (row[0] or "").strip().lower()

        if mode == "summary":
            if r0 in ("zona de tranzactionare", "interval"):
                mode = "intervals"
                intervals.append(row)
            else:
                summary.append(row)
        else:
            intervals.append(row)

    return summary, intervals


def parse_summary(summary_rows: list[list[str]]) -> dict[str, dict[str, Any]]:
    """
    Best-effort: așteaptă rânduri de forma:
      ["ROPEX_DAM_Base (1-24)", "516.19", "62925.5", "PT15M"]
    Sare peste rânduri de tip header.
    """
    out: dict[str, dict[str, Any]] = {}
    for raw in summary_rows:
        row = normalize_row(raw)
        if not row:
            continue
        key = (row[0] or "").strip()
        if not key:
            continue

        key_lc = key.lower()
        if "pret mediu" in key_lc or "volum" in key_lc or key_lc == "rezolutie":
            continue

        pret = to_float(_safe_get(row, 1))
        volum = to_float(_safe_get(row, 2))
        rez = _safe_get(row, 3).strip() or None

        out[key] = {
            "pret_mediu_lei_mwh": pret,
            "volum_mwh": volum,
            "rezolutie": rez,
        }
    return out


def header_to_map(headers: list[str]) -> dict[str, int]:
    """
    Mapează coloane cunoscute la index, în funcție de export.
    Required: interval + pret
    Optional: zona, volum, cumparare, vanzare, rezolutie
    """
    hm: dict[str, int] = {}
    for i, h in enumerate(headers):
        hh = normalize_header(h)

        if hh == "interval":
            hm["interval"] = i
        elif "zona" in hh:
            hm["zona"] = i
        elif "pret" in hh:
            hm["pret"] = i
        elif "volum tranzactionat pe cumparare" in hh or "volum tranzacționat pe cumpărare" in hh:
            hm["volum_cumparare"] = i
        elif "volum tranzactionat pe vanzare" in hh or "volum tranzacționat pe vânzare" in hh:
            hm["volum_vanzare"] = i
        elif "volum tranzactionat" in hh or "volum tranzacționat" in hh:
            hm["volum"] = i
        elif "rezolutie" in hh or "rezoluție" in hh:
            hm["rezolutie"] = i

    if "interval" not in hm or "pret" not in hm:
        raise ValueError(f"Header CSV neașteptat / incomplet (lipsesc Interval/Pret): {headers}")

    return hm


def build_interval_row(
    day: dt.date,
    interval_idx: int,
    res_minutes: int,
    hm: dict[str, int],
    row: list[str],
) -> dict[str, Any]:
    start_time, end_time = interval_times(day, interval_idx, res_minutes)

    out: dict[str, Any] = {
        "interval": interval_idx,
        "start_time": start_time,
        "end_time": end_time,
        "pret_lei_mwh": to_float(_safe_get(row, hm["pret"])),
        "rezolutie": None,
    }

    # rezolutie: din fișier dacă există, altfel fallback
    if "rezolutie" in hm:
        out["rezolutie"] = _safe_get(row, hm["rezolutie"]).strip() or None
    if not out["rezolutie"]:
        out["rezolutie"] = f"PT{res_minutes}M"

    # Optional columns — _safe_get returnează "" dacă indexul e invalid
    if "zona" in hm:
        z = _safe_get(row, hm["zona"]).strip()
        out["zona"] = z or None

    if "volum" in hm:
        out["volum"] = to_float(_safe_get(row, hm["volum"]))

    if "volum_cumparare" in hm:
        out["volum_cumparare"] = to_float(_safe_get(row, hm["volum_cumparare"]))

    if "volum_vanzare" in hm:
        out["volum_vanzare"] = to_float(_safe_get(row, hm["volum_vanzare"]))

    return out


def parse_intervals(interval_rows: list[list[str]], day: dt.date, resolution: int) -> dict[str, Any]:
    if not interval_rows:
        return {"count": 0, "rows": []}

    header = normalize_row(interval_rows[0])

    try:
        hm = header_to_map(header)
    except Exception as e:
        # În log vrem să vedem header-ul exact, fiindcă aici se rupe tot.
        _LOGGER.debug(
            "OPCOM: header de intervale neașteptat (data=%s, res=%s). Header=%s. Eroare=%s",
            iso_date(day),
            resolution,
            header,
            e,
        )
        raise

    rows_out: list[dict[str, Any]] = []
    skipped_no_interval = 0
    skipped_no_price = 0

    for raw in interval_rows[1:]:
        row = normalize_row(raw)
        if not row or all(c.strip() == "" for c in row):
            continue

        # skip header repeats
        first = (row[0] or "").strip().lower()
        if first in ("interval", "zona de tranzactionare"):
            continue

        interval_idx: Optional[int] = None
        interval_idx = to_int(_safe_get(row, hm["interval"]))

        if not interval_idx or interval_idx <= 0:
            skipped_no_interval += 1
            continue

        built = build_interval_row(day, interval_idx, resolution, hm, row)

        # Debug util: dacă lipsește prețul, nu aruncăm; doar contorizăm.
        if built.get("pret_lei_mwh") is None:
            skipped_no_price += 1

        rows_out.append(built)

    if not rows_out:
        _LOGGER.debug("OPCOM: nu am scos niciun interval (data=%s, res=%s).", iso_date(day), resolution)
    else:
        _LOGGER.debug(
            "OPCOM: intervale parse-uite (data=%s, res=%s): count=%s, fara_interval=%s, fara_pret=%s.",
            iso_date(day),
            resolution,
            len(rows_out),
            skipped_no_interval,
            skipped_no_price,
        )

    return {"count": len(rows_out), "rows": rows_out}


def parse_opcom_csv(text: str, day: dt.date, resolution: int) -> dict[str, Any]:
    rows: list[list[str]] = []
    reader = csv.reader(text.splitlines())
    for r in reader:
        rows.append(r)

    summary_rows, interval_rows = split_sections(rows)
    summary = parse_summary(summary_rows) if summary_rows else {}
    intervals = parse_intervals(interval_rows, day, resolution)

    out: dict[str, Any] = {
        "date": iso_date(day),
        "resolution_minutes": resolution,
        "intervals": intervals,
    }

    if summary:
        out["summary"] = summary

    # Mic rezumat în debug: te ajută să vezi imediat dacă exportul e “ciudat”.
    _LOGGER.debug(
        "OPCOM: parse final (data=%s, res=%s). Are summary=%s. Intervale=%s.",
        iso_date(day),
        resolution,
        "da" if bool(summary) else "nu",
        intervals.get("count", 0),
    )

    return out


# ----------------------------
# Public API used by coordinator
# ----------------------------

async def fetch_and_parse_day(hass: HomeAssistant, day: dt.date, settings: OpcomSettings) -> dict[str, Any]:
    """
    Descarcă și parsează pentru o zi, pe toate rezoluțiile cerute.
    Returnează:
      {
        "date": "YYYY-MM-DD",
        "title": "...", (opțional)
        "resolutions": {
           "15": { ...parsed... },
           "30": { ...parsed... },
           ...
        }
      }
    """
    resolutions = settings.resolutions
    lang = settings.lang

    day_obj: dict[str, Any] = {
        "date": iso_date(day),
        "resolutions": {},
    }

    preferred_for_title = 15 if 15 in resolutions else resolutions[0]
    title: Optional[str] = None

    csv_text_by_res: dict[int, str] = {}

    _LOGGER.debug(
        "OPCOM: încep ziua %s (rezoluții=%s, lang=%s).",
        iso_date(day),
        resolutions,
        lang,
    )

    failed_resolutions: list[int] = []

    for res in resolutions:
        try:
            csv_text = await fetch_csv(hass, day, res, lang)
        except Exception as err:
            _LOGGER.warning(
                "OPCOM: fetch CSV eșuat (data=%s, res=%s): %s: %s. "
                "Rezoluția va lipsi din date.",
                iso_date(day), res, type(err).__name__, err,
            )
            failed_resolutions.append(res)
            continue

        csv_text_by_res[res] = csv_text

        if res == preferred_for_title and title is None:
            title = parse_title_from_csv(csv_text)

        try:
            parsed = parse_opcom_csv(csv_text, day, res)
        except Exception as err:
            _LOGGER.warning(
                "OPCOM: parse CSV eșuat (data=%s, res=%s): %s: %s. "
                "Rezoluția va lipsi din date.",
                iso_date(day), res, type(err).__name__, err,
            )
            failed_resolutions.append(res)
            continue

        day_obj["resolutions"][str(res)] = parsed

    # Dacă TOATE rezoluțiile au eșuat, aruncăm eroare ca să semnalăm eșecul
    # complet al zilei. coordinator._do_fetch va face continue (izolare per-zi).
    if len(failed_resolutions) == len(resolutions):
        raise RuntimeError(
            f"Toate rezoluțiile ({resolutions}) au eșuat pentru ziua {iso_date(day)}."
        )

    if failed_resolutions:
        _LOGGER.warning(
            "OPCOM: ziua %s — rezoluții eșuate: %s, reușite: %s.",
            iso_date(day),
            failed_resolutions,
            [r for r in resolutions if r not in failed_resolutions],
        )

    # Fallback titlu: dacă nu l-am găsit pe rezoluția preferată, încercăm restul
    if title is None:
        for res, txt in csv_text_by_res.items():
            title = parse_title_from_csv(txt)
            if title:
                _LOGGER.debug(
                    "OPCOM: titlul nu a ieșit pe rezoluția preferată, dar l-am găsit pe res=%s.",
                    res,
                )
                break

    if title:
        day_obj["title"] = title
        _LOGGER.debug("OPCOM: titlu zi=%s: %s", iso_date(day), title)
    else:
        _LOGGER.debug("OPCOM: nu am găsit titlu pentru ziua %s (ok, nu e critic).", iso_date(day))

    # Rezumat final (fără să îngropăm logul în payload)
    rez_counts: dict[str, Any] = {}
    for res_k, res_obj in day_obj["resolutions"].items():
        intervals = res_obj.get("intervals", {})
        rez_counts[res_k] = intervals.get("count")

    _LOGGER.debug(
        "OPCOM: ziua %s e gata. Rezumat intervale pe rezoluții: %s",
        iso_date(day),
        rez_counts,
    )

    return day_obj