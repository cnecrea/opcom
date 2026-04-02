# custom_components/opcom/diagnostics.py
# Export diagnostic complet pentru integrarea OPCOM România.
# Accesibil din: Setări → Integrări → OPCOM → ⋮ → Descarcă diagnosticul.
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, LICENSE_DATA_KEY
from .coordinator import OpcomCoordinator


def _summarize_day(day_key: str, day_obj: Any) -> dict[str, Any]:
    """
    Rezumat citibil pentru o singură zi de date OPCOM.

    Nu exportă toate rândurile brute (ar fi sute de linii),
    ci extrage esența: câte intervale, prețuri min/max/mediu,
    și dacă datele arată complete.
    """
    if not isinstance(day_obj, dict):
        return {"Zi": day_key, "Stare": "date invalide sau lipsa"}

    title = day_obj.get("title", "—")
    resolutions = day_obj.get("resolutions", {})

    if not isinstance(resolutions, dict) or not resolutions:
        return {
            "Zi": day_key,
            "Titlu": title,
            "Stare": "fara rezolutii disponibile",
        }

    result: dict[str, Any] = {
        "Zi": day_key,
        "Titlu": title,
    }

    for res_key, res_obj in sorted(resolutions.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
        if not isinstance(res_obj, dict):
            result[f"PT{res_key}M"] = "date invalide"
            continue

        intervals = res_obj.get("intervals", {})
        rows = intervals.get("rows", []) if isinstance(intervals, dict) else []

        if not isinstance(rows, list):
            rows = []

        nr_intervale = len(rows)

        # Extrage prețuri pentru min/max/medie
        preturi: list[float] = []
        for r in rows:
            try:
                p = float(r.get("pret_lei_mwh"))
                preturi.append(p)
            except (TypeError, ValueError):
                continue

        if preturi:
            pret_min = min(preturi)
            pret_max = max(preturi)
            pret_mediu = sum(preturi) / len(preturi)
            res_info = (
                f"{nr_intervale} intervale"
                f" · min {pret_min:.2f}"
                f" · max {pret_max:.2f}"
                f" · mediu {pret_mediu:.2f} RON/MWh"
            )
        elif nr_intervale > 0:
            res_info = f"{nr_intervale} intervale (preturi indisponibile)"
        else:
            res_info = "fara intervale"

        # Verifică completitudinea
        expected = 24 * 60 // int(res_key) if res_key.isdigit() and int(res_key) > 0 else None
        if expected and nr_intervale < expected:
            res_info += f" (lipsa {expected - nr_intervale} din {expected})"

        # Summary din CSV (dacă există)
        summary = res_obj.get("summary")
        if isinstance(summary, dict) and summary:
            res_info += " · summar CSV: da"

        result[f"PT{res_key}M"] = res_info

    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """
    Export diagnostic complet pentru integrarea OPCOM.

    Structura:
      1. Configurare integrare — setările active
      2. Stare coordinator — versiune date, ultima actualizare
      3. Rezumat date per zi — câte intervale, prețuri min/max/mediu
      4. Dispozitive si entitati inregistrate
      5. Date brute complete (pentru debugging avansat)
    """
    coordinator: OpcomCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    # --- 1. Configurare integrare ---
    settings_section: dict[str, Any] = {
        "Entry ID": entry.entry_id,
        "Titlu": entry.title,
    }

    if coordinator:
        s = coordinator.settings
        settings_section.update({
            "Limba": s.lang,
            "Rezolutii active": ", ".join(f"PT{r}M" for r in s.resolutions),
            "Zile in avans": s.days_ahead,
            "Interval scanare (minute)": s.scan_interval_minutes,
            "Durata fereastra (minute)": s.window_minutes,
            "Nr. ferestre (global)": s.top_n_windows,
            "Nr. ferestre per rezolutie": (
                ", ".join(f"PT{r}M: {n}" for r, n in sorted(s.top_n_per_res.items()))
                if s.top_n_per_res else "nesetat (foloseste valoarea globala)"
            ),
            "Prag pret scazut (RON/MWh)": (
                f"{s.price_threshold_low:.2f}" if s.price_threshold_low is not None
                else "neconfigurat"
            ),
            "Prag pret ridicat (RON/MWh)": (
                f"{s.price_threshold_high:.2f}" if s.price_threshold_high is not None
                else "neconfigurat"
            ),
        })
    else:
        settings_section["Stare"] = "coordinator indisponibil"

    # --- 2. Stare coordinator ---
    coordinator_section: dict[str, Any] = {}
    if coordinator:
        coordinator_section = {
            "Ultima actualizare reusita": coordinator.last_update_success,
            "Versiune date": coordinator.data_version,
            "Ultima eroare": (
                str(coordinator.last_exception)
                if coordinator.last_exception else "niciuna"
            ),
        }
    else:
        coordinator_section["Stare"] = "coordinator indisponibil"

    # --- 3. Rezumat date per zi ---
    data_summary: list[dict[str, Any]] = []
    if coordinator and coordinator.data:
        days = coordinator.data.get("days", {})
        if isinstance(days, dict):
            for day_key in sorted(days.keys()):
                data_summary.append(_summarize_day(day_key, days[day_key]))
        else:
            data_summary.append({"Stare": "structura 'days' invalida"})

        # Info despre sursa și momentul generării
        generated_at = coordinator.data.get("generated_at", "necunoscut")
        base_date = coordinator.data.get("base_date", "necunoscut")
    else:
        data_summary.append({"Stare": "fara date disponibile"})
        generated_at = "necunoscut"
        base_date = "necunoscut"

    # --- 4. Dispozitive și entități ---
    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)

    devices = dr.async_entries_for_config_entry(device_reg, entry.entry_id)
    entities = er.async_entries_for_config_entry(entity_reg, entry.entry_id)

    devices_section = [
        {
            "Nume": d.name or "—",
            "Model": d.model or "—",
            "Producator": d.manufacturer or "—",
        }
        for d in devices
    ]

    entities_section = [
        {
            "Entity ID": e.entity_id,
            "Nume original": e.original_name or "—",
            "Dezactivat de": str(e.disabled_by) if e.disabled_by else "activ",
        }
        for e in entities
    ]

    # --- 5. Informații licență (acces direct la proprietăți, nu prin as_dict) ---
    license_section: dict[str, Any] = {}
    license_mgr = hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
    if license_mgr:
        license_section = {
            "fingerprint": license_mgr.fingerprint,
            "status": license_mgr.status,
            "license_key": license_mgr.license_key_masked,
            "is_valid": license_mgr.is_valid,
            "license_type": license_mgr.license_type,
        }
    else:
        license_section["Stare"] = "LicenseManager indisponibil"

    # --- 6. Asamblare finală ---
    diag: dict[str, Any] = {
        "Configurare integrare": settings_section,
        "Informatii licenta": license_section,
        "Stare coordinator": coordinator_section,
        "Generat la (UTC)": generated_at,
        "Data de baza (CET)": base_date,
        "Rezumat date pe zile": data_summary,
        "Dispozitive inregistrate": devices_section if devices_section else "niciunul",
        "Entitati inregistrate": {
            "Total": len(entities_section),
            "Lista": entities_section,
        },
        "Date brute (pentru debugging)": coordinator.data if coordinator and coordinator.data else "indisponibile",
        "Configurare bruta (entry.data)": dict(entry.data),
        "Optiuni brute (entry.options)": dict(entry.options),
    }

    return diag
