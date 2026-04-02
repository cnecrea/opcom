# custom_components/opcom/binary_sensor.py
# Senzori binari OPCOM: fereastră, individual, prag, percentilă, rolling
# + licențiere conform STANDARD-LICENTA.md v3.5
from __future__ import annotations

import logging
import math
from typing import Any, Optional

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LICENSE_DATA_KEY
from .coordinator import OpcomCoordinator
from .helpers import (
    current_interval_index,
    compute_windows,
    compute_top_individual_intervals,
    compute_top_remaining_intervals,
    compute_percentile_rank,
    is_price_below_threshold,
    is_price_above_threshold,
    in_any_window,
    in_top_individual,
    format_window_str,
    format_interval_str,
    format_interval_dict,
    rows_for_day_res,
    day_key,
    safe_float,
    find_row_by_interval,
    max_intervals_per_day,
)


_LOGGER = logging.getLogger(__name__)


# ─── Helper verificare licență (funcție standalone) ───────────────────────
def _is_license_valid(hass: HomeAssistant) -> bool:
    """Verificare real-time a licenței."""
    mgr = hass.data.get(DOMAIN, {}).get(LICENSE_DATA_KEY)
    if mgr is None:
        return False
    return mgr.is_valid


def _diagnostic_attrs(coordinator: OpcomCoordinator) -> dict[str, Any]:
    """Secțiune de diagnostic — adăugată la final, separată vizual."""
    return {
        "Informatii actualizare": "",
        "Actualizare reusita": coordinator.last_update_success,
        "Versiune date": coordinator.data_version,
    }


# =========================================================================
# Clasă de bază cu license gating pentru senzori binari
# =========================================================================

class _OpcomBinaryBase(CoordinatorEntity[OpcomCoordinator], BinarySensorEntity):
    """Bază comună cu _license_valid property."""

    _attr_has_entity_name = False

    @property
    def _license_valid(self) -> bool:
        """Verificare real-time a licenței (nu boolean static)."""
        return _is_license_valid(self.hass)


# =========================================================================
# 1. MOD FEREASTRĂ — "Ar trebui să încarce/exporte acum"
# =========================================================================

class OpcomBinaryWindow(_OpcomBinaryBase):
    """
    Senzor binar OPCOM (mod fereastră):
      - Selectează top_n ferestre NON-SUPRAPUSE din toată ziua
      - Calculează media prețurilor per fereastră
      - ON dacă intervalul curent se află într-o fereastră selectată
    """

    def __init__(
        self,
        coordinator: OpcomCoordinator,
        entry: ConfigEntry,
        res: int,
        *,
        key: str,
        base_name_ro: str,
        icon: str,
        expensive: bool,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.res = int(res)
        self.expensive = expensive

        self._attr_unique_id = f"{entry.entry_id}_{key}_pt{self.res}"
        self._attr_name = f"[{self.res}] {base_name_ro}"
        self._attr_suggested_object_id = f"{key}_pt{self.res}"
        self._attr_icon = icon

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="OPCOM România",
            manufacturer="Ciprian Nicolae (cnecrea)",
            model="OPCOM",
        )

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

    def _get_day_windows(self) -> list[dict[str, Any]]:
        """Calculează ferestrele NON-SUPRAPUSE doar din datele de AZI."""
        version = self.coordinator.data_version
        dk = day_key(self.coordinator.hass, 0)

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

    def _get_threshold(self) -> float | None:
        """Returnează pragul relevant: threshold_high pt export, threshold_low pt import."""
        if self.expensive:
            return self.coordinator.settings.price_threshold_high
        return self.coordinator.settings.price_threshold_low

    def _current_price(self, idx: int) -> float | None:
        """Prețul intervalului curent."""
        dk = day_key(self.coordinator.hass, 0)
        rows = rows_for_day_res(self.coordinator.data or {}, dk, self.res)
        row = find_row_by_interval(rows, idx)
        return safe_float(row.get("pret_lei_mwh")) if row else None

    def _passes_threshold(self, price: float | None, threshold: float | None) -> bool:
        """
        Verifică dacă prețul trece pragul configurat.

        - Prag None → trece întotdeauna (fără filtru, comportament original).
        - Preț None → nu trece (nu avem date, nu activăm).
        - Export (expensive=True): preț >= prag (nu exporta sub pragul minim).
        - Import (expensive=False): preț <= prag (nu importa peste pragul maxim).
        """
        if threshold is None:
            return True
        if price is None:
            return False
        if self.expensive:
            return price >= threshold
        return price <= threshold

    @property
    def is_on(self) -> bool | None:
        if not self._license_valid:
            return None

        windows = self._get_day_windows()
        if not windows:
            return False
        idx = current_interval_index(self.coordinator.hass, self.res)

        # Pasul 1: intervalul curent e într-o fereastră selectată?
        if in_any_window(idx, windows) is None:
            return False

        # Pasul 2: prețul curent trece pragul configurat?
        threshold = self._get_threshold()
        if threshold is None:
            return True  # fără prag → ON doar pe baza ferestrei
        price = self._current_price(idx)
        return self._passes_threshold(price, threshold)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {}

        dk = day_key(self.coordinator.hass, 0)
        idx = current_interval_index(self.coordinator.hass, self.res)
        windows = self._get_day_windows()
        matched = in_any_window(idx, windows)
        threshold = self._get_threshold()
        price = self._current_price(idx)

        # Determinăm de ce senzorul e OFF (dacă e OFF)
        if matched and threshold is not None and not self._passes_threshold(price, threshold):
            motiv_off = "preț sub prag export" if self.expensive else "preț peste prag import"
        else:
            motiv_off = None

        attrs: dict[str, Any] = {
            "Data": dk,
            "Rezolutie": f"PT{self.res}M",
            "Mod": "fereastra",
            "Durata fereastra": f"{self.coordinator.settings.window_minutes} min",
            "Nr. ferestre": self.coordinator.settings.get_top_n(self.res),
            "Interval curent": idx,
            "Pret curent": f"{price:.2f} RON/MWh" if price is not None else "—",
            "Prag": f"{threshold:.2f} RON/MWh" if threshold is not None else "dezactivat",
            "Fereastra activa": format_window_str(matched, self.coordinator.hass) if matched else "niciuna",
        }
        if motiv_off:
            attrs["Blocat de prag"] = motiv_off
        attrs.update(_diagnostic_attrs(self.coordinator))
        return attrs


# =========================================================================
# 2. MOD INDIVIDUAL — "Interval ieftin/scump acum"
# =========================================================================

class OpcomBinaryIndividual(_OpcomBinaryBase):
    """
    Senzor binar OPCOM — selecție per interval individual din TOATĂ ziua.
    """

    def __init__(
        self,
        coordinator: OpcomCoordinator,
        entry: ConfigEntry,
        res: int,
        *,
        key: str,
        base_name_ro: str,
        icon: str,
        expensive: bool,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.res = int(res)
        self.expensive = expensive

        self._attr_unique_id = f"{entry.entry_id}_{key}_pt{self.res}"
        self._attr_name = f"[{self.res}] {base_name_ro}"
        self._attr_suggested_object_id = f"{key}_pt{self.res}"
        self._attr_icon = icon

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="OPCOM România",
            manufacturer="Ciprian Nicolae (cnecrea)",
            model="OPCOM",
        )

        # FIX 4+5: Cache cu contor monoton + day_key
        self._cache_version: int = -1
        self._cache_day_key: str = ""
        self._cached_top: list[dict[str, Any]] = []

    async def async_will_remove_from_hass(self) -> None:
        """Curăță cache-ul la dezinstalare."""
        self._cached_top = []
        self._cache_version = -1
        self._cache_day_key = ""
        await super().async_will_remove_from_hass()

    def _total_slots(self) -> int:
        """Câte intervale individuale să selecteze (acoperire identică cu ferestrele)."""
        slots_per_window = max(1, int(math.ceil(
            self.coordinator.settings.window_minutes / self.res
        )))
        return self.coordinator.settings.get_top_n(self.res) * slots_per_window

    def _get_top_intervals(self) -> list[dict[str, Any]]:
        """Selectează top N intervale individuale doar din datele de AZI."""
        version = self.coordinator.data_version
        dk = day_key(self.coordinator.hass, 0)

        if version != self._cache_version or dk != self._cache_day_key:
            self._cache_version = version
            self._cache_day_key = dk
            rows = rows_for_day_res(self.coordinator.data or {}, dk, self.res)
            self._cached_top = compute_top_individual_intervals(
                rows,
                total_slots=self._total_slots(),
                expensive=self.expensive,
            )

        return self._cached_top

    @property
    def is_on(self) -> bool | None:
        if not self._license_valid:
            return None

        top = self._get_top_intervals()
        if not top:
            return False
        idx = current_interval_index(self.coordinator.hass, self.res)
        return in_top_individual(idx, top) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {}

        dk = day_key(self.coordinator.hass, 0)
        idx = current_interval_index(self.coordinator.hass, self.res)
        top = self._get_top_intervals()
        matched = in_top_individual(idx, top)

        attrs: dict[str, Any] = {
            "Data": dk,
            "Rezolutie": f"PT{self.res}M",
            "Mod": "individual",
            "Intervale selectate": self._total_slots(),
            "Interval curent": idx,
            "Interval activ": format_interval_str(matched, self.coordinator.hass) if matched else "niciunul",
        }

        if top:
            attrs["Top intervale din toata ziua"] = ""
            attrs.update(format_interval_dict(top, self.coordinator.hass))

        attrs.update(_diagnostic_attrs(self.coordinator))
        return attrs


# =========================================================================
# 3. PRAG DE PREȚ — "Preț sub/peste prag acum"
# =========================================================================

class OpcomBinaryThreshold(_OpcomBinaryBase):
    """
    Senzor binar OPCOM — prag de preț configurabil.
    ON dacă prețul curent este sub/peste pragul configurat.
    """

    def __init__(
        self,
        coordinator: OpcomCoordinator,
        entry: ConfigEntry,
        res: int,
        *,
        key: str,
        base_name_ro: str,
        icon: str,
        is_low: bool,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.res = int(res)
        self.is_low = is_low

        self._attr_unique_id = f"{entry.entry_id}_{key}_pt{self.res}"
        self._attr_name = f"[{self.res}] {base_name_ro}"
        self._attr_suggested_object_id = f"{key}_pt{self.res}"
        self._attr_icon = icon

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="OPCOM România",
            manufacturer="Ciprian Nicolae (cnecrea)",
            model="OPCOM",
        )

    def _get_threshold(self) -> float | None:
        """Returnează pragul configurat (low sau high)."""
        if self.is_low:
            return self.coordinator.settings.price_threshold_low
        return self.coordinator.settings.price_threshold_high

    @property
    def is_on(self) -> bool | None:
        if not self._license_valid:
            return None

        threshold = self._get_threshold()
        if threshold is None:
            return False

        dk = day_key(self.coordinator.hass, 0)
        rows = rows_for_day_res(self.coordinator.data or {}, dk, self.res)
        idx = current_interval_index(self.coordinator.hass, self.res)

        if self.is_low:
            result, _ = is_price_below_threshold(rows, idx, threshold)
        else:
            result, _ = is_price_above_threshold(rows, idx, threshold)
        return result

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {}

        dk = day_key(self.coordinator.hass, 0)
        idx = current_interval_index(self.coordinator.hass, self.res)
        threshold = self._get_threshold()
        rows = rows_for_day_res(self.coordinator.data or {}, dk, self.res)

        row = find_row_by_interval(rows, idx)
        price = safe_float(row.get("pret_lei_mwh")) if row else None

        attrs: dict[str, Any] = {
            "Data": dk,
            "Rezolutie": f"PT{self.res}M",
            "Mod": "prag",
            "Interval curent": idx,
            "Prag configurat": f"{threshold:.2f} RON/MWh" if threshold is not None else "neconfigurat",
            "Pret curent": f"{price:.2f} RON/MWh" if price is not None else "—",
            "Senzor prag": "activ" if threshold is not None else "dezactivat (prag neconfigurat)",
        }
        attrs.update(_diagnostic_attrs(self.coordinator))
        return attrs


# =========================================================================
# 4. PERCENTILĂ — "Preț în bottom/top X% al zilei"
# =========================================================================

class OpcomBinaryPercentile(_OpcomBinaryBase):
    """
    Senzor binar OPCOM — percentilă zilnică.
    ON dacă prețul curent se află în bottom 25% (ieftin) sau top 25% (scump).
    """

    PERCENTILE_THRESHOLD = 0.25

    def __init__(
        self,
        coordinator: OpcomCoordinator,
        entry: ConfigEntry,
        res: int,
        *,
        key: str,
        base_name_ro: str,
        icon: str,
        expensive: bool,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.res = int(res)
        self.expensive = expensive

        self._attr_unique_id = f"{entry.entry_id}_{key}_pt{self.res}"
        self._attr_name = f"[{self.res}] {base_name_ro}"
        self._attr_suggested_object_id = f"{key}_pt{self.res}"
        self._attr_icon = icon

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="OPCOM România",
            manufacturer="Ciprian Nicolae (cnecrea)",
            model="OPCOM",
        )

    @property
    def is_on(self) -> bool | None:
        if not self._license_valid:
            return None

        dk = day_key(self.coordinator.hass, 0)
        rows = rows_for_day_res(self.coordinator.data or {}, dk, self.res)
        idx = current_interval_index(self.coordinator.hass, self.res)

        percentile, _ = compute_percentile_rank(rows, idx)
        if percentile is None:
            return False

        if self.expensive:
            return percentile >= (1.0 - self.PERCENTILE_THRESHOLD)
        else:
            return percentile <= self.PERCENTILE_THRESHOLD

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {}

        dk = day_key(self.coordinator.hass, 0)
        idx = current_interval_index(self.coordinator.hass, self.res)
        rows = rows_for_day_res(self.coordinator.data or {}, dk, self.res)

        percentile, price = compute_percentile_rank(rows, idx)

        attrs: dict[str, Any] = {
            "Data": dk,
            "Rezolutie": f"PT{self.res}M",
            "Mod": "percentila",
            "Interval curent": idx,
            "Prag percentila": f"{'top' if self.expensive else 'bottom'} {int(self.PERCENTILE_THRESHOLD * 100)}%",
            "Percentila curenta": f"{percentile * 100:.1f}%" if percentile is not None else "—",
            "Pret curent": f"{price:.2f} RON/MWh" if price is not None else "—",
        }
        attrs.update(_diagnostic_attrs(self.coordinator))
        return attrs


# =========================================================================
# 5. ROLLING WINDOW — "Ieftin/scump din ce a mai rămas"
# =========================================================================

class OpcomBinaryRolling(_OpcomBinaryBase):
    """
    Senzor binar OPCOM — rolling window (intervale rămase).
    Selectează top N din intervalele RĂMASE (de acum înainte).
    """

    def __init__(
        self,
        coordinator: OpcomCoordinator,
        entry: ConfigEntry,
        res: int,
        *,
        key: str,
        base_name_ro: str,
        icon: str,
        expensive: bool,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.res = int(res)
        self.expensive = expensive

        self._attr_unique_id = f"{entry.entry_id}_{key}_pt{self.res}"
        self._attr_name = f"[{self.res}] {base_name_ro}"
        self._attr_suggested_object_id = f"{key}_pt{self.res}"
        self._attr_icon = icon

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="OPCOM România",
            manufacturer="Ciprian Nicolae (cnecrea)",
            model="OPCOM",
        )

        # Cache: depinde de data_version + interval curent + day_key
        self._cache_version: int = -1
        self._cache_idx: int = -1
        self._cache_day_key: str = ""
        self._cached_top: list[dict[str, Any]] = []

    async def async_will_remove_from_hass(self) -> None:
        """Curăță cache-ul la dezinstalare."""
        self._cached_top = []
        self._cache_version = -1
        self._cache_idx = -1
        self._cache_day_key = ""
        await super().async_will_remove_from_hass()

    def _total_slots(self) -> int:
        """Câte intervale individuale să selecteze."""
        slots_per_window = max(1, int(math.ceil(
            self.coordinator.settings.window_minutes / self.res
        )))
        return self.coordinator.settings.get_top_n(self.res) * slots_per_window

    def _get_top_remaining(self) -> list[dict[str, Any]]:
        """Selectează top N din intervalele rămase (inclusiv cel curent)."""
        version = self.coordinator.data_version
        idx = current_interval_index(self.coordinator.hass, self.res)
        dk = day_key(self.coordinator.hass, 0)

        if version != self._cache_version or idx != self._cache_idx or dk != self._cache_day_key:
            self._cache_version = version
            self._cache_idx = idx
            self._cache_day_key = dk
            rows = rows_for_day_res(self.coordinator.data or {}, dk, self.res)
            self._cached_top = compute_top_remaining_intervals(
                rows,
                current_idx=idx,
                total_slots=self._total_slots(),
                expensive=self.expensive,
            )

        return self._cached_top

    @property
    def is_on(self) -> bool | None:
        if not self._license_valid:
            return None

        top = self._get_top_remaining()
        if not top:
            return False
        idx = current_interval_index(self.coordinator.hass, self.res)
        return in_top_individual(idx, top) is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if not self._license_valid:
            return {}

        dk = day_key(self.coordinator.hass, 0)
        idx = current_interval_index(self.coordinator.hass, self.res)
        top = self._get_top_remaining()
        matched = in_top_individual(idx, top)

        remaining_count = max_intervals_per_day(self.res) - idx + 1

        attrs: dict[str, Any] = {
            "Data": dk,
            "Rezolutie": f"PT{self.res}M",
            "Mod": "rolling",
            "Intervale selectate": self._total_slots(),
            "Intervale ramase in zi": remaining_count,
            "Interval curent": idx,
            "Interval activ": format_interval_str(matched, self.coordinator.hass) if matched else "niciunul",
        }

        if top:
            attrs["Top intervale din cele ramase"] = ""
            attrs.update(format_interval_dict(top, self.coordinator.hass))

        attrs.update(_diagnostic_attrs(self.coordinator))
        return attrs


# =========================================================================
# SETUP — Înregistrarea tuturor senzorilor binari cu cleanup bidirecțional
# =========================================================================

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    coordinator: OpcomCoordinator = hass.data[DOMAIN][entry.entry_id]

    license_valid = _is_license_valid(hass)

    if not license_valid:
        # Licență invalidă — curăță toți senzorii binari normali orfani
        registru = er.async_get(hass)
        for entry_reg in er.async_entries_for_config_entry(registru, entry.entry_id):
            if entry_reg.domain == "binary_sensor":
                registru.async_remove(entry_reg.entity_id)
        # Nu creăm LicentaNecesaraSensor pentru binary_sensor — e doar în sensor.py
        return

    # Licență validă — creează senzorii normali
    entities: list[BinarySensorEntity] = []
    for res in coordinator.settings.resolutions:

        # --- 1. MOD FEREASTRĂ ---
        entities.append(
            OpcomBinaryWindow(
                coordinator, entry, res=res,
                key="ar_trebui_sa_incarce_acum",
                base_name_ro="Ar trebui să încarce acum",
                icon="mdi:battery-charging",
                expensive=False,
            )
        )
        entities.append(
            OpcomBinaryWindow(
                coordinator, entry, res=res,
                key="ar_trebui_sa_exporte_acum",
                base_name_ro="Ar trebui să exporte acum",
                icon="mdi:transmission-tower-export",
                expensive=True,
            )
        )

        # --- 2. MOD INDIVIDUAL ---
        entities.append(
            OpcomBinaryIndividual(
                coordinator, entry, res=res,
                key="interval_ieftin_acum",
                base_name_ro="Interval ieftin acum",
                icon="mdi:arrow-down-bold-circle",
                expensive=False,
            )
        )
        entities.append(
            OpcomBinaryIndividual(
                coordinator, entry, res=res,
                key="interval_scump_acum",
                base_name_ro="Interval scump acum",
                icon="mdi:arrow-up-bold-circle",
                expensive=True,
            )
        )

        # --- 3. PRAG DE PREȚ ---
        entities.append(
            OpcomBinaryThreshold(
                coordinator, entry, res=res,
                key="pret_sub_prag",
                base_name_ro="Preț sub prag acum",
                icon="mdi:arrow-collapse-down",
                is_low=True,
            )
        )
        entities.append(
            OpcomBinaryThreshold(
                coordinator, entry, res=res,
                key="pret_peste_prag",
                base_name_ro="Preț peste prag acum",
                icon="mdi:arrow-collapse-up",
                is_low=False,
            )
        )

        # --- 4. PERCENTILĂ ---
        entities.append(
            OpcomBinaryPercentile(
                coordinator, entry, res=res,
                key="pret_ieftin_percentila",
                base_name_ro="Preț ieftin azi (bottom 25%)",
                icon="mdi:chart-bell-curve-cumulative",
                expensive=False,
            )
        )
        entities.append(
            OpcomBinaryPercentile(
                coordinator, entry, res=res,
                key="pret_scump_percentila",
                base_name_ro="Preț scump azi (top 25%)",
                icon="mdi:chart-bell-curve",
                expensive=True,
            )
        )

        # --- 5. ROLLING WINDOW ---
        entities.append(
            OpcomBinaryRolling(
                coordinator, entry, res=res,
                key="ieftin_din_ramase",
                base_name_ro="Ieftin din intervalele rămase",
                icon="mdi:history",
                expensive=False,
            )
        )
        entities.append(
            OpcomBinaryRolling(
                coordinator, entry, res=res,
                key="scump_din_ramase",
                base_name_ro="Scump din intervalele rămase",
                icon="mdi:update",
                expensive=True,
            )
        )

    async_add_entities(entities)
