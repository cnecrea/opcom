from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Optional

DOMAIN = "opcom"

CONF_LICENSE_KEY: Final = "license_key"
LICENSE_DATA_KEY: Final = "opcom_license_manager"
LICENSE_PURCHASE_URL = "https://hubinteligent.org/licenta/opcom"

CONF_LANG = "lang"
CONF_RESOLUTIONS = "resolutions"
CONF_DAYS_AHEAD = "days_ahead"
CONF_SCAN_INTERVAL = "scan_interval_minutes"
CONF_WINDOW_MINUTES = "window_minutes"
CONF_TOP_N_WINDOWS = "top_n_windows"
CONF_TOP_N_PER_RES = "top_n_per_resolution"
CONF_PRICE_THRESHOLD_LOW = "price_threshold_low"
CONF_PRICE_THRESHOLD_HIGH = "price_threshold_high"

DEFAULT_LANG = "ro"
DEFAULT_RESOLUTIONS = [15, 30, 60]
DEFAULT_DAYS_AHEAD = 2
DEFAULT_SCAN_INTERVAL_MIN = 15
DEFAULT_WINDOW_MINUTES = 60
DEFAULT_TOP_N_WINDOWS = 6
DEFAULT_PRICE_THRESHOLD_LOW: float | None = None   # None = dezactivat
DEFAULT_PRICE_THRESHOLD_HIGH: float | None = None   # None = dezactivat

OPCOM_CSV_URL = "https://www.opcom.ro/rapoarte-pzu-raportPIP-export-csv/{dd}/{mm}/{yyyy}/{lang}?resolution={res}"

# OPCOM operează pe timezone-ul Central European (CET iarna / CEST vara).
# "Europe/Berlin" respectă ambele — CET (UTC+1) și CEST (UTC+2).
# Intervalul 1 = 00:00 CET/CEST, ultimul interval = 23:45/23:30/23:00 CET/CEST.
# Sursa: OPCOM — „toate aspectele legate de PZU se raportează la ore CET."
# Notă: în piețele europene de energie, „CET" este shorthand pentru CET/CEST.
OPCOM_TIMEZONE = "Europe/Berlin"


@dataclass(frozen=True)
class OpcomSettings:
    """Setările integrării OPCOM, folosite de coordinator și entități."""

    lang: str
    resolutions: list[int]
    days_ahead: int
    scan_interval_minutes: int
    window_minutes: int
    top_n_windows: int
    top_n_per_res: dict[int, int] = field(default_factory=dict)
    price_threshold_low: float | None = None
    price_threshold_high: float | None = None

    def get_top_n(self, res: int) -> int:
        """
        Returnează top_n pentru o rezoluție specifică.
        Dacă există o valoare per-rezoluție > 0, o folosește.
        Altfel, revine la valoarea globală top_n_windows.
        """
        val = self.top_n_per_res.get(res, 0)
        return val if val > 0 else self.top_n_windows
