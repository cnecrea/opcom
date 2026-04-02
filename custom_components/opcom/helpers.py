# custom_components/opcom/helpers.py
# Funcții comune folosite de sensor.py și binary_sensor.py
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Optional

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import OPCOM_TIMEZONE


# ---------------------------------------------------------------------------
# Utilitare de bază
# ---------------------------------------------------------------------------

def safe_float(x: Any) -> Optional[float]:
    """Conversie sigură la float, returnează None dacă nu se poate."""
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _opcom_now(hass: HomeAssistant) -> dt.datetime:
    """
    Returnează ora curentă în timezone-ul OPCOM (CET/CEST = Europe/Berlin).
    OPCOM operează pe CET/CEST: intervalul 1 = 00:00 CET/CEST.
    """
    tz = dt_util.get_time_zone(OPCOM_TIMEZONE)
    return dt_util.now(tz)


def day_key(hass: HomeAssistant, offset_days: int) -> str:
    """Returnează cheia zilei de livrare OPCOM (YYYY-MM-DD) în CET/CEST, cu offset."""
    now_cet = _opcom_now(hass)
    day = now_cet.date() + dt.timedelta(days=offset_days)
    return day.strftime("%Y-%m-%d")


def rows_for_day_res(data: dict[str, Any], day_k: str, res: int) -> list[dict[str, Any]]:
    """Extrage lista de intervale pentru o zi și o rezoluție dată."""
    day_obj = data.get("days", {}).get(day_k)
    if not day_obj:
        return []
    res_obj = day_obj.get("resolutions", {}).get(str(res))
    if not res_obj:
        return []
    rows = res_obj.get("intervals", {}).get("rows", [])
    return rows if isinstance(rows, list) else []


def current_interval_index(hass: HomeAssistant, res_minutes: int) -> int:
    """
    Calculează indexul intervalului curent (1-based) pe baza orei CET/CEST.

    Clamped la [1, max_intervals_per_day] — protecție la edge-case
    (ex: microsecundele de la miezul nopții înainte de tick la ziua nouă).
    """
    now_cet = _opcom_now(hass)
    minutes = now_cet.hour * 60 + now_cet.minute
    idx = int((minutes // res_minutes) + 1)
    return min(idx, max_intervals_per_day(res_minutes))


def max_intervals_per_day(res_minutes: int) -> int:
    """Returnează numărul total de intervale pe zi pentru o rezoluție dată."""
    return 24 * 60 // res_minutes


def find_row_by_interval(
    rows: list[dict[str, Any]], interval_idx: int
) -> Optional[dict[str, Any]]:
    """Găsește rândul cu intervalul specificat."""
    for r in rows:
        try:
            if int(r.get("interval")) == int(interval_idx):
                return r
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Conversie CET → ora locală HA (pentru afișare în atribute)
# ---------------------------------------------------------------------------

def cet_to_local(hass: HomeAssistant, cet_dt_str: str) -> str:
    """
    Convertește 'YYYY-MM-DD HH:MM' din CET/CEST în timezone-ul local al HA.
    Calculele interne rămân CET — asta e doar pentru afișarea user-friendly.
    """
    if not cet_dt_str:
        return cet_dt_str
    try:
        cet_tz = dt_util.get_time_zone(OPCOM_TIMEZONE)
        local_tz = dt_util.get_time_zone(hass.config.time_zone)
        naive = dt.datetime.strptime(str(cet_dt_str).strip(), "%Y-%m-%d %H:%M")
        aware = naive.replace(tzinfo=cet_tz)
        local = aware.astimezone(local_tz)
        return local.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return cet_dt_str


# ---------------------------------------------------------------------------
# Formatare ore — extrage HH:MM din "YYYY-MM-DD HH:MM"
# ---------------------------------------------------------------------------

def extract_time(dt_str: Any, hass: HomeAssistant = None) -> str:
    """
    Extrage 'HH:MM' din 'YYYY-MM-DD HH:MM'.
    Dacă hass e furnizat, convertește mai întâi din CET/CEST în ora locală HA.
    """
    if not dt_str:
        return "—"
    s = str(dt_str).strip()
    if hass:
        s = cet_to_local(hass, s)
    if " " in s:
        return s.split(" ", 1)[1]
    return s


# ---------------------------------------------------------------------------
# Parser pentru top_n per rezoluție (folosit de config_flow + coordinator)
# ---------------------------------------------------------------------------

def parse_top_n_per_res(raw: Any) -> dict[int, int]:
    """
    Parsează un string de forma „15:4, 30:6, 60:2" într-un dict {15: 4, 30: 6, 60: 2}.

    Reguli:
      - Separatori acceptați: virgulă, punct-virgulă, spațiu, pipe
      - Fiecare pereche: „rezoluție:valoare" (ex: 15:4)
      - Rezoluții valide: 15, 30, 60
      - Valori ≤ 0 sau non-numerice sunt ignorate
      - String gol sau None → dict gol (toate rezoluțiile folosesc valoarea globală)
    """
    if raw is None:
        return {}
    s = str(raw).strip()
    if not s:
        return {}

    for sep in (";", " ", "|"):
        s = s.replace(sep, ",")

    result: dict[int, int] = {}
    for part in s.split(","):
        part = part.strip()
        if ":" not in part:
            continue
        try:
            res_str, n_str = part.split(":", 1)
            res_val = int(res_str.strip())
            n_val = int(n_str.strip())
            if res_val in (15, 30, 60) and n_val > 0:
                result[res_val] = n_val
        except ValueError:
            continue

    return result


# ---------------------------------------------------------------------------
# Ferestre de preț — algoritm GREEDY (non-suprapuse)
# ---------------------------------------------------------------------------

def _build_all_candidate_windows(
    rows: list[dict[str, Any]],
    res_minutes: int,
    window_minutes: int,
    *,
    min_interval: Optional[int] = None,
) -> list[dict[str, Any]]:
    """
    Generează TOATE ferestrele candidate (sliding window).
    Dacă min_interval e setat, exclude ferestrele care încep înainte de el.
    """
    if not rows:
        return []

    slots = max(1, int(math.ceil(window_minutes / res_minutes)))

    by_i: dict[int, dict[str, Any]] = {}
    max_i = 0
    for r in rows:
        try:
            i = int(r.get("interval"))
            by_i[i] = r
            max_i = max(max_i, i)
        except Exception:
            continue

    candidates: list[dict[str, Any]] = []
    for start_i in range(1, max_i - slots + 2):
        if min_interval is not None and start_i < min_interval:
            continue

        start_row = by_i.get(start_i)
        end_row = by_i.get(start_i + slots - 1)
        if not start_row or not end_row:
            continue

        parts: list[float] = []
        ok = True
        for i in range(start_i, start_i + slots):
            r = by_i.get(i)
            if not r:
                ok = False
                break
            p = safe_float(r.get("pret_lei_mwh"))
            if p is None:
                ok = False
                break
            parts.append(p)

        if not ok or not parts:
            continue

        avg_price = sum(parts) / len(parts)
        candidates.append(
            {
                "interval_inceput": start_i,
                "interval_sfarsit": start_i + slots - 1,
                "ora_inceput": start_row.get("start_time"),
                "ora_sfarsit": end_row.get("end_time"),
                "pret_mediu_lei_mwh": round(avg_price, 2),
            }
        )

    return candidates


def _greedy_select_non_overlapping(
    candidates: list[dict[str, Any]],
    top_n: int,
    *,
    expensive: bool,
) -> list[dict[str, Any]]:
    """
    Selectează top_n ferestre NON-SUPRAPUSE dintr-o listă de candidați.

    Algoritm greedy:
      1. Sortează candidații după preț (crescător/descrescător)
      2. Alege cel mai bun candidat
      3. Marchează intervalele ca ocupate
      4. Repetă până ai top_n sau nu mai sunt candidați valizi
    """
    sorted_candidates = sorted(
        candidates,
        key=lambda w: w["pret_mediu_lei_mwh"],
        reverse=expensive,
    )

    selected: list[dict[str, Any]] = []
    occupied: set[int] = set()

    for w in sorted_candidates:
        if len(selected) >= top_n:
            break

        start = int(w["interval_inceput"])
        end = int(w["interval_sfarsit"])
        window_indices = set(range(start, end + 1))

        if window_indices & occupied:
            continue

        selected.append(w)
        occupied.update(window_indices)

    return selected


def compute_windows(
    rows: list[dict[str, Any]],
    res_minutes: int,
    window_minutes: int,
    top_n: int,
    *,
    expensive: bool,
    min_interval: Optional[int] = None,
) -> list[dict[str, Any]]:
    """
    Calculează ferestrele de preț NON-SUPRAPUSE (ieftine sau scumpe).

    Returnează o listă de maxim top_n ferestre, sortate după preț.
    """
    candidates = _build_all_candidate_windows(
        rows, res_minutes, window_minutes, min_interval=min_interval
    )
    return _greedy_select_non_overlapping(candidates, top_n, expensive=expensive)


def in_any_window(
    interval_idx: int, windows: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """Verifică dacă intervalul curent se află într-una din ferestrele date."""
    for w in windows:
        try:
            if int(w["interval_inceput"]) <= interval_idx <= int(w["interval_sfarsit"]):
                return w
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Selecție individuală per interval (fără medie pe fereastră)
# ---------------------------------------------------------------------------

def compute_top_individual_intervals(
    rows: list[dict[str, Any]],
    total_slots: int,
    *,
    expensive: bool,
) -> list[dict[str, Any]]:
    """
    Selectează top N intervale individuale sortate după preț propriu.

    Fiecare interval e selectat exclusiv pe baza prețului propriu,
    fără a fi „tras" în sus sau în jos de vecinii din fereastră.
    """
    valid: list[dict[str, Any]] = []
    for r in rows:
        p = safe_float(r.get("pret_lei_mwh"))
        if p is None:
            continue
        try:
            idx = int(r.get("interval"))
        except Exception:
            continue
        valid.append({
            "interval": idx,
            "ora_inceput": r.get("start_time"),
            "ora_sfarsit": r.get("end_time"),
            "pret_lei_mwh": p,
        })

    valid.sort(key=lambda x: x["pret_lei_mwh"], reverse=expensive)
    return valid[:total_slots]


def in_top_individual(
    interval_idx: int, top_intervals: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """Verifică dacă intervalul curent e în lista de top intervale individuale."""
    for t in top_intervals:
        try:
            if int(t["interval"]) == int(interval_idx):
                return t
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# DESIGN NOU: Verificare prag de preț (threshold)
# ---------------------------------------------------------------------------

def is_price_below_threshold(
    rows: list[dict[str, Any]],
    interval_idx: int,
    threshold: float,
) -> tuple[bool, Optional[float]]:
    """
    Verifică dacă prețul intervalului curent este SUB pragul dat.

    Returnează (is_below, price_or_none).
    Senzor simplu, fără calcule de fereastră — doar preț curent vs prag.
    """
    row = find_row_by_interval(rows, interval_idx)
    if not row:
        return False, None
    price = safe_float(row.get("pret_lei_mwh"))
    if price is None:
        return False, None
    return price <= threshold, price


def is_price_above_threshold(
    rows: list[dict[str, Any]],
    interval_idx: int,
    threshold: float,
) -> tuple[bool, Optional[float]]:
    """
    Verifică dacă prețul intervalului curent este PESTE pragul dat.

    Returnează (is_above, price_or_none).
    """
    row = find_row_by_interval(rows, interval_idx)
    if not row:
        return False, None
    price = safe_float(row.get("pret_lei_mwh"))
    if price is None:
        return False, None
    return price >= threshold, price


# ---------------------------------------------------------------------------
# DESIGN NOU: Percentilă zilnică
# ---------------------------------------------------------------------------

def compute_percentile_rank(
    rows: list[dict[str, Any]],
    interval_idx: int,
) -> tuple[Optional[float], Optional[float]]:
    """
    Calculează percentila prețului curent relativ la toată ziua.

    Returnează (percentile_rank, current_price).
    percentile_rank: 0.0 = cel mai ieftin din zi, 1.0 = cel mai scump.
    Formula: (nr. intervale cu preț < preț_curent) / (total - 1)

    Exemplu: dacă prețul curent e al 10-lea cel mai mic din 96,
    percentila ≈ 9/95 ≈ 0.095 (9.5%).
    """
    current_row = find_row_by_interval(rows, interval_idx)
    if not current_row:
        return None, None

    current_price = safe_float(current_row.get("pret_lei_mwh"))
    if current_price is None:
        return None, None

    # Colectăm toate prețurile valide din zi
    all_prices: list[float] = []
    for r in rows:
        p = safe_float(r.get("pret_lei_mwh"))
        if p is not None:
            all_prices.append(p)

    if len(all_prices) <= 1:
        return 0.5, current_price  # Un singur interval → 50%

    count_below = sum(1 for p in all_prices if p < current_price)
    percentile = count_below / (len(all_prices) - 1)

    return round(percentile, 4), current_price


# ---------------------------------------------------------------------------
# DESIGN NOU: Rolling window — top din intervalele RĂMASE (nu toată ziua)
# ---------------------------------------------------------------------------

def compute_top_remaining_intervals(
    rows: list[dict[str, Any]],
    current_idx: int,
    total_slots: int,
    *,
    expensive: bool,
) -> list[dict[str, Any]]:
    """
    Selectează top N intervale individuale doar din cele RĂMASE (>= current_idx).

    Diferența față de compute_top_individual_intervals:
    - Acel algoritm selectează din TOATĂ ziua (inclusiv trecut)
    - Acesta selectează doar din intervalele care URMEAZĂ

    Utilitate: un senzor care spune „acum e un moment BUN să cumperi/vinzi
    din ceea ce a mai rămas din zi", nu „acum e un moment bun comparativ cu
    toată ziua inclusiv orele trecute".
    """
    valid: list[dict[str, Any]] = []
    for r in rows:
        p = safe_float(r.get("pret_lei_mwh"))
        if p is None:
            continue
        try:
            idx = int(r.get("interval"))
        except Exception:
            continue
        # Filtrăm doar intervalele viitoare (inclusiv cel curent)
        if idx < current_idx:
            continue
        valid.append({
            "interval": idx,
            "ora_inceput": r.get("start_time"),
            "ora_sfarsit": r.get("end_time"),
            "pret_lei_mwh": p,
        })

    valid.sort(key=lambda x: x["pret_lei_mwh"], reverse=expensive)
    return valid[:total_slots]


# ---------------------------------------------------------------------------
# Intervale rămase în ferestre (folosit de senzorul de count)
# ---------------------------------------------------------------------------

def remaining_intervals_in_windows(
    rows: list[dict[str, Any]],
    windows: list[dict[str, Any]],
    current_idx: int,
) -> list[dict[str, Any]]:
    """
    Returnează lista intervalelor individuale care:
      1. Fac parte din cel puțin una din ferestrele date
      2. Sunt în viitor (interval >= current_idx)
    """
    window_indices: set[int] = set()
    for w in windows:
        try:
            start = int(w["interval_inceput"])
            end = int(w["interval_sfarsit"])
            for i in range(start, end + 1):
                window_indices.add(i)
        except Exception:
            continue

    by_i: dict[int, dict[str, Any]] = {}
    for r in rows:
        try:
            by_i[int(r.get("interval"))] = r
        except Exception:
            continue

    result: list[dict[str, Any]] = []
    for idx in sorted(window_indices):
        if idx < current_idx:
            continue
        row = by_i.get(idx)
        if not row:
            continue
        result.append(
            {
                "interval": idx,
                "ora_inceput": row.get("start_time"),
                "ora_sfarsit": row.get("end_time"),
                "pret_lei_mwh": safe_float(row.get("pret_lei_mwh")),
            }
        )

    return result


# ---------------------------------------------------------------------------
# Formatare „human-friendly" pentru atribute HA
# ---------------------------------------------------------------------------
# Toate funcțiile de formatare returnează STRING-uri one-liner, nu dict-uri.
# Asta face atributele din HA mult mai citibile — fără nesting.
# ---------------------------------------------------------------------------

_SEP = " · "  # separator vizual între câmpuri


def format_window_str(w: dict[str, Any], hass: HomeAssistant = None) -> str:
    """
    Formatează o fereastră ca un string one-liner.

    Exemplu multi-interval: "20:30 → 21:30 · medie 1007.60 RON/MWh · int. 79–82"
    Exemplu interval unic:  "20:30 → 20:45 · 623.02 RON/MWh · int. 79"
    """
    ora_start = extract_time(w.get("ora_inceput"), hass)
    ora_end = extract_time(w.get("ora_sfarsit"), hass)
    pret = w.get("pret_mediu_lei_mwh")
    i_start = w.get("interval_inceput", "?")
    i_end = w.get("interval_sfarsit", "?")

    single_interval = (i_start == i_end)

    if pret is not None:
        pret_str = f"{pret:.2f} RON/MWh" if single_interval else f"medie {pret:.2f} RON/MWh"
    else:
        pret_str = "—"

    int_str = f"int. {i_start}" if single_interval else f"int. {i_start}–{i_end}"

    return f"{ora_start} → {ora_end}{_SEP}{pret_str}{_SEP}{int_str}"


def format_window_dict(
    windows: list[dict[str, Any]], hass: HomeAssistant = None
) -> dict[str, str]:
    """
    Formatează o listă de ferestre ca dict cu chei numerotate.
    Sortare CRONOLOGICĂ (după interval_inceput) — mai ușor de scanat vizual.

    Exemplu:
      {"Fereastra 1": "08:30 → 09:30 · ...", "Fereastra 2": "14:00 → 15:00 · ..."}
    """
    sorted_windows = sorted(
        windows,
        key=lambda w: int(w.get("interval_inceput", 0)),
    )
    result: dict[str, str] = {}
    for i, w in enumerate(sorted_windows, 1):
        result[f"Fereastra {i}"] = format_window_str(w, hass)
    return result


def format_interval_str(r: dict[str, Any], hass: HomeAssistant = None) -> str:
    """
    Formatează un interval ca un string one-liner.

    Exemplu: "int. 96 · 00:45 → 01:00 · 623.02 RON/MWh"
    """
    idx = r.get("interval", "?")
    ora_start = extract_time(r.get("ora_inceput"), hass)
    ora_end = extract_time(r.get("ora_sfarsit"), hass)
    pret = r.get("pret_lei_mwh")
    pret_str = f"{pret:.2f} RON/MWh" if pret is not None else "—"
    return f"int. {idx}{_SEP}{ora_start} → {ora_end}{_SEP}{pret_str}"


def format_interval_dict(
    intervals: list[dict[str, Any]], hass: HomeAssistant = None
) -> dict[str, str]:
    """
    Formatează o listă de intervale ca dict cu chei numerotate.
    Sortare CRONOLOGICĂ (după interval) — mai ușor de scanat vizual.

    Exemplu:
      {"Pozitia 1": "int. 12 · 02:45 → 03:00 · 623.02 RON/MWh", ...}
    """
    sorted_intervals = sorted(
        intervals,
        key=lambda r: int(r.get("interval", 0)),
    )
    result: dict[str, str] = {}
    for i, r in enumerate(sorted_intervals, 1):
        result[f"Pozitia {i}"] = format_interval_str(r, hass)
    return result


# Backward-compat aliases (deprecate in viitor)
format_window = format_window_str
format_remaining_interval = format_interval_str
format_window_list = format_window_dict
format_remaining_list = format_interval_dict
