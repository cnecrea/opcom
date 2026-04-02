# custom_components/opcom/sensor.py
# Senzori OPCOM: preț acum, preț următor, ferestre, intervale rămase, percentilă
# + licențiere conform STANDARD-LICENTA.md v3.5
from __future__ import annotations

import logging
from typing import Any, Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LICENSE_DATA_KEY
from .coordinator import OpcomCoordinator
from .helpers import (
    safe_float,
    day_key,
    rows_for_day_res,
    current_interval_index,
    find_row_by_interval,
    compute_windows,
    compute_percentile_rank,
    remaining_intervals_in_windows,
    format_window_str,
    format_window_dict,
    format_interval_str,
    format_interval_dict,
    extract_time,
    max_intervals_per_day,
)

_LOGGER = logging.getLogger(__name__)

UNIT_RON_MWH = "RON/MWh"
DOMAIN_NAME = "OPCOM România"


# ─── Helper verificare licență (funcție standalone, nu metodă) ────────────
def _is_license_valid(hass: HomeAssistant) -> bool:
    """Verificare real-time a licenței."""
    mgr = hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
    if mgr is None:
        return False
    return mgr.is_valid


class _OpcomBaseSensor(CoordinatorEntity[OpcomCoordinator], SensorEntity):
    """
    Senzor OPCOM — clasă de bază cu license gating.
    """

    _attr_has_entity_name = False
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: OpcomCoordinator,
        entry: ConfigEntry,
        *,
        res: int,
        day_offset: int,
        key: str,
        base_object_id: str,
        base_name_ro: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.res = int(res)
        self.day_offset = int(day_offset)
        self.key = key

        day_tag = "azi" if self.day_offset == 0 else "maine"

        self._attr_unique_id = f"{entry.entry_id}_{key}_pt{self.res}_{day_tag}"
        self._attr_suggested_object_id = f"{base_object_id}_pt{self.res}_{day_tag}"
        self._attr_name = f"[{self.res}] {base_name_ro}"
        self._attr_icon = icon

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=DOMAIN_NAME,
            manufacturer="Ciprian Nicolae (cnecrea)",
            model="OPCOM",
        )

    @property
    def _license_valid(self) -> bool:
        """Verificare real-time a licenței (nu boolean static)."""
        return _is_license_valid(self.hass)

    def _diagnostic_attrs(self) -> dict[str, Any]:
        """Secțiune de diagnostic — adăugată la final, separată vizual."""
        return {
            "Informatii actualizare": "",
            "Actualizare reusita": self.coordinator.last_update_success,
            "Versiune date": self.coordinator.data_version,
        }


class OpcomPriceNowSensor(_OpcomBaseSensor):
    """Prețul curent al intervalului activ."""
    _attr_state_class = "measurement"
    _attr_native_unit_of_measurement = UNIT_RON_MWH

    @property
    def native_value(self) -> Optional[float]:
        if not self._license_valid:
            return "Licență necesară"

        data = self.coordinator.data or {}
        dk = day_key(self.coordinator.hass, 0)
        rows = rows_for_day_res(data, dk, self.res)
        if not rows:
            _LOGGER.debug(
                "OPCOM sensor %s: 0 intervale pentru %s PT%s.",
                self._attr_unique_id, dk, self.res,
            )
            return None
        idx = current_interval_index(self.coordinator.hass, self.res)
        row = find_row_by_interval(rows, idx)
        val = safe_float(row.get("pret_lei_mwh")) if row else None
        return round(val, 2) if val is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {}

        data = self.coordinator.data or {}
        dk = day_key(self.coordinator.hass, 0)
        rows = rows_for_day_res(data, dk, self.res)
        idx = current_interval_index(self.coordinator.hass, self.res)
        row = find_row_by_interval(rows, idx)

        attrs: dict[str, Any] = {
            "Data": dk,
            "Rezolutie": f"PT{self.res}M",
            "Interval": idx,
        }
        if row:
            ora_start = extract_time(row.get("start_time"), self.coordinator.hass)
            ora_end = extract_time(row.get("end_time"), self.coordinator.hass)
            attrs["Ora"] = f"{ora_start} → {ora_end}"
            zona = row.get("zona")
            if zona:
                attrs["Zona"] = zona
        attrs.update(self._diagnostic_attrs())
        return attrs


class OpcomPriceNextSensor(_OpcomBaseSensor):
    """
    Prețul intervalului următor.

    FIX 5: La ultimul interval al zilei (ex: PT15 idx=96),
    caută intervalul 1 din ziua următoare. Dacă nu sunt date
    pentru mâine, returnează None.
    """
    _attr_state_class = "measurement"
    _attr_native_unit_of_measurement = UNIT_RON_MWH

    @property
    def native_value(self) -> Optional[float]:
        if not self._license_valid:
            return "Licență necesară"

        data = self.coordinator.data or {}
        idx = current_interval_index(self.coordinator.hass, self.res)
        max_idx = max_intervals_per_day(self.res)
        next_idx = idx + 1

        if next_idx <= max_idx:
            dk = day_key(self.coordinator.hass, 0)
            rows = rows_for_day_res(data, dk, self.res)
            row = find_row_by_interval(rows, next_idx)
        else:
            dk = day_key(self.coordinator.hass, 1)
            rows = rows_for_day_res(data, dk, self.res)
            row = find_row_by_interval(rows, 1)

        val = safe_float(row.get("pret_lei_mwh")) if row else None
        return round(val, 2) if val is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {}

        data = self.coordinator.data or {}
        idx = current_interval_index(self.coordinator.hass, self.res)
        max_idx = max_intervals_per_day(self.res)
        next_idx = idx + 1

        if next_idx <= max_idx:
            dk = day_key(self.coordinator.hass, 0)
            rows = rows_for_day_res(data, dk, self.res)
            row = find_row_by_interval(rows, next_idx)
            is_tomorrow = False
        else:
            dk = day_key(self.coordinator.hass, 1)
            rows = rows_for_day_res(data, dk, self.res)
            row = find_row_by_interval(rows, 1)
            next_idx = 1
            is_tomorrow = True

        attrs: dict[str, Any] = {
            "Data": dk,
            "Rezolutie": f"PT{self.res}M",
            "Sursa": "maine" if is_tomorrow else "azi",
            "Interval urmator": next_idx,
        }
        if row:
            ora_start = extract_time(row.get("start_time"), self.coordinator.hass)
            ora_end = extract_time(row.get("end_time"), self.coordinator.hass)
            attrs["Ora"] = f"{ora_start} → {ora_end}"
            zona = row.get("zona")
            if zona:
                attrs["Zona"] = zona
        attrs.update(self._diagnostic_attrs())
        return attrs


class OpcomWindowsSensor(_OpcomBaseSensor):
    """
    Ferestre de preț pentru o zi (toate ferestrele zilei, inclusiv trecute).
    Util ca referință / istoric. Folosește algoritm greedy non-suprapus.
    """

    _attr_native_unit_of_measurement = UNIT_RON_MWH

    def __init__(
        self,
        coordinator: OpcomCoordinator,
        entry: ConfigEntry,
        *,
        res: int,
        day_offset: int,
        key: str,
        base_object_id: str,
        base_name_ro: str,
        icon: str,
        expensive: bool,
    ) -> None:
        super().__init__(
            coordinator, entry,
            res=res, day_offset=day_offset, key=key,
            base_object_id=base_object_id, base_name_ro=base_name_ro, icon=icon,
        )
        self.expensive = expensive

        # FIX 4+5: Cache cu contor monoton + day_key (invalidare la miezul nopții)
        self._cache_version: int = -1
        self._cache_day_key: str = ""
        self._cached_windows: list[dict[str, Any]] = []

    async def async_will_remove_from_hass(self) -> None:
        """Curăță cache-ul la dezinstalare."""
        self._cached_windows = []
        self._cache_version = -1
        self._cache_day_key = ""
        await super().async_will_remove_from_hass()

    def _get_windows(self) -> list[dict[str, Any]]:
        """Calculează ferestrele NON-SUPRAPUSE pentru toată ziua (cu cache)."""
        version = self.coordinator.data_version
        dk = day_key(self.coordinator.hass, self.day_offset)

        if version != self._cache_version or dk != self._cache_day_key:
            self._cache_version = version
            self._cache_day_key = dk
            rows = rows_for_day_res(self.coordinator.data or {}, dk, self.res)
            self._cached_windows = compute_windows(
                rows,
                res_minutes=self.res,
                window_minutes=self.coordinator.settings.window_minutes,
                top_n=self.coordinator.settings.get_top_n(self.res),
                expensive=self.expensive,
            )

        return self._cached_windows

    @property
    def native_value(self) -> Optional[float]:
        if not self._license_valid:
            return "Licență necesară"

        windows = self._get_windows()
        if not windows:
            return None
        val = safe_float(windows[0].get("pret_mediu_lei_mwh"))
        return round(val, 2) if val is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {}

        dk = day_key(self.coordinator.hass, self.day_offset)
        windows = self._get_windows()

        attrs: dict[str, Any] = {
            "Data": dk,
            "Rezolutie": f"PT{self.res}M",
            "Durata fereastra": f"{self.coordinator.settings.window_minutes} min",
            "Nr. ferestre": self.coordinator.settings.get_top_n(self.res),
        }

        # Separator + ferestre formatate ca one-liners
        if windows:
            attrs["Lista ferestre de pret"] = ""
            attrs.update(format_window_dict(windows, self.coordinator.hass))

        attrs.update(self._diagnostic_attrs())
        return attrs


class OpcomRemainingIntervalsSensor(_OpcomBaseSensor):
    """
    Câte intervale „bune" mai rămân azi (de acum încolo),
    bazat pe ferestre NON-SUPRAPUSE calculate doar din intervalele viitoare.
    """

    def __init__(
        self,
        coordinator: OpcomCoordinator,
        entry: ConfigEntry,
        *,
        res: int,
        day_offset: int,
        key: str,
        base_object_id: str,
        base_name_ro: str,
        icon: str,
        expensive: bool,
    ) -> None:
        super().__init__(
            coordinator, entry,
            res=res, day_offset=day_offset, key=key,
            base_object_id=base_object_id, base_name_ro=base_name_ro, icon=icon,
        )
        self.expensive = expensive

        # FIX 4+5: Cache cu contor monoton + interval curent + day_key
        self._cache_version: int = -1
        self._cache_idx: int = -1
        self._cache_day_key: str = ""
        self._cached_remaining: list[dict[str, Any]] = []

    async def async_will_remove_from_hass(self) -> None:
        """Curăță cache-ul la dezinstalare."""
        self._cached_remaining = []
        self._cache_version = -1
        self._cache_idx = -1
        self._cache_day_key = ""
        await super().async_will_remove_from_hass()

    def _get_remaining(self) -> list[dict[str, Any]]:
        """Calculează intervalele rămase — doar viitoare, ferestre non-suprapuse (cu cache)."""
        version = self.coordinator.data_version
        idx = current_interval_index(self.coordinator.hass, self.res)
        dk = day_key(self.coordinator.hass, self.day_offset)

        if version != self._cache_version or idx != self._cache_idx or dk != self._cache_day_key:
            self._cache_version = version
            self._cache_idx = idx
            self._cache_day_key = dk
            rows = rows_for_day_res(self.coordinator.data or {}, dk, self.res)

            windows = compute_windows(
                rows,
                res_minutes=self.res,
                window_minutes=self.coordinator.settings.window_minutes,
                top_n=self.coordinator.settings.get_top_n(self.res),
                expensive=self.expensive,
                min_interval=idx,
            )

            self._cached_remaining = remaining_intervals_in_windows(rows, windows, idx)

        return self._cached_remaining

    @property
    def native_value(self) -> Optional[int]:
        if not self._license_valid:
            return "Licență necesară"

        remaining = self._get_remaining()
        return len(remaining)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {}

        dk = day_key(self.coordinator.hass, self.day_offset)
        idx = current_interval_index(self.coordinator.hass, self.res)
        remaining = self._get_remaining()

        attrs: dict[str, Any] = {
            "Data": dk,
            "Rezolutie": f"PT{self.res}M",
            "Durata fereastra": f"{self.coordinator.settings.window_minutes} min",
            "Nr. ferestre": self.coordinator.settings.get_top_n(self.res),
            "Interval curent": idx,
            "Intervale ramase": len(remaining),
        }

        if remaining:
            attrs["Lista intervale ramase"] = ""
            attrs.update(format_interval_dict(remaining, self.coordinator.hass))

        attrs.update(self._diagnostic_attrs())
        return attrs


class OpcomPercentileSensor(_OpcomBaseSensor):
    """
    Senzor: percentila prețului curent în distribuția zilei.

    Returnează un număr între 0 și 100:
      - 0 = cel mai ieftin din zi
      - 100 = cel mai scump din zi
      - 50 = median

    Util în automatizări: „dacă percentila < 30, încarcă bateria"
    """
    _attr_state_class = "measurement"
    _attr_native_unit_of_measurement = "%"

    @property
    def native_value(self) -> Optional[float]:
        if not self._license_valid:
            return "Licență necesară"

        data = self.coordinator.data or {}
        dk = day_key(self.coordinator.hass, 0)
        rows = rows_for_day_res(data, dk, self.res)
        idx = current_interval_index(self.coordinator.hass, self.res)

        percentile, _ = compute_percentile_rank(rows, idx)
        if percentile is None:
            return None
        return round(percentile * 100, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {}

        data = self.coordinator.data or {}
        dk = day_key(self.coordinator.hass, 0)
        rows = rows_for_day_res(data, dk, self.res)
        idx = current_interval_index(self.coordinator.hass, self.res)

        percentile, price = compute_percentile_rank(rows, idx)

        attrs: dict[str, Any] = {
            "Data": dk,
            "Rezolutie": f"PT{self.res}M",
            "Interval curent": idx,
            "Pret curent": f"{price:.2f} RON/MWh" if price is not None else "—",
            "Percentila": f"{percentile * 100:.1f}%" if percentile is not None else "—",
        }
        attrs.update(self._diagnostic_attrs())
        return attrs


# =========================================================================
# Senzor „Toate prețurile" — dict HH:MM → preț, pentru azi / mâine
# =========================================================================

class OpcomAllPricesSensor(_OpcomBaseSensor):
    """
    Senzor cu TOATE prețurile unei zile, sub formă de atribute dict.

    native_value = prețul mediu al zilei (RON/MWh) sau None dacă nu sunt date.
    extra_state_attributes = {
        "00:00": 755.02,
        "00:15": 712.30,
        ...
    }

    Util pentru automatizări, template sensors, Apexcharts, Node-RED.
    Un senzor per rezoluție per zi (azi / mâine).
    """

    _attr_state_class = "measurement"
    _attr_native_unit_of_measurement = UNIT_RON_MWH

    @property
    def native_value(self) -> Optional[float]:
        if not self._license_valid:
            return "Licență necesară"

        dk = day_key(self.coordinator.hass, self.day_offset)
        rows = rows_for_day_res(self.coordinator.data or {}, dk, self.res)
        if not rows:
            return None

        prices: list[float] = []
        for r in rows:
            p = safe_float(r.get("pret_lei_mwh"))
            if p is not None:
                prices.append(p)
        if not prices:
            return None
        return round(sum(prices) / len(prices), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {}

        dk = day_key(self.coordinator.hass, self.day_offset)
        rows = rows_for_day_res(self.coordinator.data or {}, dk, self.res)

        attrs: dict[str, Any] = {
            "Data": dk,
            "Rezolutie": f"PT{self.res}M",
            "Nr. intervale": len(rows),
        }

        if rows:
            # Separator vizual
            attrs["Preturi"] = ""
            # Dict HH:MM → preț, sortat cronologic
            sorted_rows = sorted(rows, key=lambda r: int(r.get("interval", 0)))
            for r in sorted_rows:
                ora = extract_time(r.get("start_time"), self.coordinator.hass)
                pret = safe_float(r.get("pret_lei_mwh"))
                if ora and ora != "—":
                    attrs[ora] = round(pret, 2) if pret is not None else None

        attrs.update(self._diagnostic_attrs())
        return attrs


# =========================================================================
# LicentaNecesaraSensor — apare DOAR când licența nu este validă
# =========================================================================

class LicentaNecesaraSensor(SensorEntity):
    """
    Senzor care apare DOAR când licența nu este validă.
    Oferă diagnostice utile: status curent, zile trial rămase, informații de activare.
    """
    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{DOMAIN}_licenta_{entry.entry_id}"
        self._attr_name = "Licență necesară"
        self._attr_icon = "mdi:license"
        # OBLIGATORIU: entity_id explicit — pattern consistent între integrări
        self.entity_id = f"sensor.{DOMAIN}_licenta_{entry.entry_id}"

    @property
    def device_info(self) -> DeviceInfo:
        """ACELEAȘI identifiers ca senzorii normali — apare pe același device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=DOMAIN_NAME,
            manufacturer="Ciprian Nicolae (cnecrea)",
            model="OPCOM",
            entry_type=None,
        )

    @property
    def native_value(self) -> str:
        """Returnează status-ul licenței — vizibil clar pentru utilizator."""
        mgr = self.hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
        if mgr is not None:
            status = mgr.status
            if status == "expired":
                return "Licență expirată"
            if status == "trial":
                days = mgr.trial_days_remaining
                return f"Trial — {days} zile rămase" if days > 0 else "Trial expirat"
        return "Licență necesară"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Atribute de diagnostic: status, zile rămase, link achiziție."""
        attrs: dict[str, Any] = {"nr_identificare": self._entry.entry_id}
        mgr = self.hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
        if mgr is not None:
            attrs["status_licenta"] = mgr.status
            if mgr.status == "trial":
                attrs["zile_trial_ramase"] = mgr.trial_days_remaining
            attrs["informatii"] = (
                "Achizitioneaza o licenta de pe hubinteligent.org "
                "sau din Buy Me a Coffee."
            )
        return attrs


# =========================================================================
# SETUP — Înregistrarea tuturor senzorilor cu cleanup bidirecțional
# =========================================================================

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    coordinator: OpcomCoordinator = hass.data[DOMAIN][entry.entry_id]

    license_valid = _is_license_valid(hass)
    licenta_uid = f"{DOMAIN}_licenta_{entry.entry_id}"

    if not license_valid:
        # ── Licență INVALIDĂ ──
        # 1. Curăță TOȚI senzorii normali orfani din Entity Registry
        registru = er.async_get(hass)
        for entry_reg in er.async_entries_for_config_entry(registru, entry.entry_id):
            if entry_reg.domain == "sensor" and entry_reg.unique_id != licenta_uid:
                registru.async_remove(entry_reg.entity_id)

        # 2. Creează DOAR LicentaNecesaraSensor — niciun alt senzor
        async_add_entities([LicentaNecesaraSensor(entry)], update_before_add=True)
        return  # IMPORTANT: return imediat, nu continua cu senzorii normali

    # ── Licență VALIDĂ ──
    # 1. Curăță LicentaNecesaraSensor orfan (dacă exista dintr-o sesiune anterioară)
    registru = er.async_get(hass)
    entitate_licenta = registru.async_get_entity_id("sensor", DOMAIN, licenta_uid)
    if entitate_licenta is not None:
        registru.async_remove(entitate_licenta)

    # 2. Creează senzorii normali
    sensors: list[SensorEntity] = []

    for res in coordinator.settings.resolutions:
        # --- Preț acum / următor ---
        sensors.append(
            OpcomPriceNowSensor(
                coordinator, entry,
                res=res, day_offset=0,
                key="pret_acum", base_object_id="pret_acum",
                base_name_ro="Preț acum", icon="mdi:currency-eur",
            )
        )
        sensors.append(
            OpcomPriceNextSensor(
                coordinator, entry,
                res=res, day_offset=0,
                key="pret_urmator", base_object_id="pret_urmator",
                base_name_ro="Preț următor", icon="mdi:arrow-right",
            )
        )

        # --- Ferestre azi ---
        sensors.append(
            OpcomWindowsSensor(
                coordinator, entry,
                res=res, day_offset=0,
                key="cea_mai_ieftina_fereastra_azi",
                base_object_id="cea_mai_ieftina_fereastra_azi",
                base_name_ro="Cea mai ieftină fereastră azi",
                icon="mdi:battery-charging-60", expensive=False,
            )
        )
        sensors.append(
            OpcomWindowsSensor(
                coordinator, entry,
                res=res, day_offset=0,
                key="cea_mai_scumpa_fereastra_azi",
                base_object_id="cea_mai_scumpa_fereastra_azi",
                base_name_ro="Cea mai scumpă fereastră azi",
                icon="mdi:transmission-tower-export", expensive=True,
            )
        )

        # --- Ferestre mâine ---
        sensors.append(
            OpcomWindowsSensor(
                coordinator, entry,
                res=res, day_offset=1,
                key="cea_mai_ieftina_fereastra_maine",
                base_object_id="cea_mai_ieftina_fereastra_maine",
                base_name_ro="Cea mai ieftină fereastră mâine",
                icon="mdi:battery-charging-60", expensive=False,
            )
        )
        sensors.append(
            OpcomWindowsSensor(
                coordinator, entry,
                res=res, day_offset=1,
                key="cea_mai_scumpa_fereastra_maine",
                base_object_id="cea_mai_scumpa_fereastra_maine",
                base_name_ro="Cea mai scumpă fereastră mâine",
                icon="mdi:transmission-tower-export", expensive=True,
            )
        )

        # --- Intervale rămase cumpărare (doar viitoare) ---
        sensors.append(
            OpcomRemainingIntervalsSensor(
                coordinator, entry,
                res=res, day_offset=0,
                key="intervale_ramase_cumparare",
                base_object_id="intervale_ramase_cumparare",
                base_name_ro="Intervale rămase cumpărare azi",
                icon="mdi:cart-arrow-down", expensive=False,
            )
        )
        sensors.append(
            OpcomRemainingIntervalsSensor(
                coordinator, entry,
                res=res, day_offset=0,
                key="intervale_ramase_vanzare",
                base_object_id="intervale_ramase_vanzare",
                base_name_ro="Intervale rămase vânzare azi",
                icon="mdi:cart-arrow-up", expensive=True,
            )
        )

        # --- Percentilă senzor numeric ---
        sensors.append(
            OpcomPercentileSensor(
                coordinator, entry,
                res=res, day_offset=0,
                key="percentila_pret",
                base_object_id="percentila_pret",
                base_name_ro="Percentilă preț acum",
                icon="mdi:chart-bell-curve-cumulative",
            )
        )

        # --- Toate prețurile azi ---
        sensors.append(
            OpcomAllPricesSensor(
                coordinator, entry,
                res=res, day_offset=0,
                key="toate_preturile_azi",
                base_object_id="toate_preturile_azi",
                base_name_ro="Toate prețurile azi",
                icon="mdi:format-list-numbered",
            )
        )

        # --- Toate prețurile mâine ---
        sensors.append(
            OpcomAllPricesSensor(
                coordinator, entry,
                res=res, day_offset=1,
                key="toate_preturile_maine",
                base_object_id="toate_preturile_maine",
                base_name_ro="Toate prețurile mâine",
                icon="mdi:format-list-numbered",
            )
        )

    async_add_entities(sensors)
