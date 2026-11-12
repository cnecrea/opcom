"""
ConfigFlow și OptionsFlow pentru integrarea OPCOM România.

ConfigFlow: configurare inițială (limbă, rezoluții, ferestre, praguri).
OptionsFlow: meniu cu două opțiuni — Setări + Licență (separate).
Licența se gestionează din OptionsFlow (pasul "licenta").

NOTE (reconfigurare):
  - Câmpurile OBLIGATORII (lang, resolutions, etc.) folosesc `default=` — voluptuous
    le completează automat dacă utilizatorul nu le atinge.
  - Câmpurile OPȚIONALE/GOLABILE (praguri, ferestre per rezoluție) folosesc
    `description={"suggested_value": ...}` — pre-completează câmpul vizual,
    dar dacă utilizatorul le GOLEȘTE, string-ul gol ajunge în user_input.
    Cu `default=`, golirea e imposibilă: voluptuous pune înapoi valoarea veche.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_LANG,
    CONF_LICENSE_KEY,
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
    LICENSE_DATA_KEY,
    LICENSE_PURCHASE_URL,
)
from .helpers import parse_top_n_per_res

_LOGGER = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Helpers de parsare
# ──────────────────────────────────────────────

def _parse_resolutions(raw: str) -> list[int]:
    """
    Acceptă: "15,30,60", "15 30 60", "15;30;60"
    Returnează o listă unică, sortată, doar cu valori permise (15/30/60).
    """
    if raw is None:
        return []
    s = str(raw).strip()
    if not s:
        return []
    for sep in (";", " ", "|"):
        s = s.replace(sep, ",")
    parts = [p.strip() for p in s.split(",") if p.strip()]

    out: list[int] = []
    for p in parts:
        try:
            v = int(p)
        except ValueError:
            continue
        if v in (15, 30, 60) and v not in out:
            out.append(v)
    out.sort()
    return out


def _to_optional_float(val: str | None) -> float | None:
    """Convertește un string la float, sau returnează None dacă e gol/invalid."""
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() in ("none", "null", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _validate_opcom_settings(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Validează setările OPCOM comune ConfigFlow și OptionsFlow.

    Returnează (data_dict, errors_dict).
    """
    errors: dict[str, str] = {}

    lang = str(user_input.get(CONF_LANG, DEFAULT_LANG)).strip().lower()
    if lang not in ("ro", "en"):
        errors[CONF_LANG] = "invalid_lang"

    resolutions = _parse_resolutions(user_input.get(CONF_RESOLUTIONS, ""))
    if not resolutions:
        errors[CONF_RESOLUTIONS] = "invalid_resolutions"

    def _to_int(key: str, default: int) -> int | None:
        v = user_input.get(key, default)
        try:
            return int(v)
        except Exception:
            errors[key] = "invalid_number"
            return None

    days_ahead = _to_int(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD)
    scan_interval = _to_int(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN)
    window_minutes = _to_int(CONF_WINDOW_MINUTES, DEFAULT_WINDOW_MINUTES)
    top_n = _to_int(CONF_TOP_N_WINDOWS, DEFAULT_TOP_N_WINDOWS)

    if days_ahead is not None and not (1 <= days_ahead <= 2):
        errors[CONF_DAYS_AHEAD] = "out_of_range"
    if scan_interval is not None and not (5 <= scan_interval <= 180):
        errors[CONF_SCAN_INTERVAL] = "out_of_range"
    if window_minutes is not None and window_minutes < 15:
        errors[CONF_WINDOW_MINUTES] = "out_of_range"
    if top_n is not None and not (1 <= top_n <= 24):
        errors[CONF_TOP_N_WINDOWS] = "out_of_range"

    # Per-resolution top_n (opțional — gol = folosește valoarea globală)
    top_n_per_res_raw = str(user_input.get(CONF_TOP_N_PER_RES, "")).strip()
    # Normalizare: "None" sau "none" → gol (poate apărea din str(None))
    if top_n_per_res_raw.lower() in ("none", "null"):
        top_n_per_res_raw = ""
    top_n_per_res = parse_top_n_per_res(top_n_per_res_raw)
    for _res_k, _n_val in top_n_per_res.items():
        if not (1 <= _n_val <= 24):
            errors[CONF_TOP_N_PER_RES] = "out_of_range"
            break

    # Praguri de preț (opționale — gol = senzor dezactivat)
    threshold_low_raw = str(user_input.get(CONF_PRICE_THRESHOLD_LOW, "")).strip()
    threshold_high_raw = str(user_input.get(CONF_PRICE_THRESHOLD_HIGH, "")).strip()
    # Normalizare: "None" → gol
    if threshold_low_raw.lower() in ("none", "null"):
        threshold_low_raw = ""
    if threshold_high_raw.lower() in ("none", "null"):
        threshold_high_raw = ""

    threshold_low = _to_optional_float(threshold_low_raw)
    threshold_high = _to_optional_float(threshold_high_raw)

    if threshold_low_raw and threshold_low is None:
        errors[CONF_PRICE_THRESHOLD_LOW] = "invalid_number"
    if threshold_high_raw and threshold_high is None:
        errors[CONF_PRICE_THRESHOLD_HIGH] = "invalid_number"

    if threshold_low is not None and not (-500 <= threshold_low <= 10000):
        errors[CONF_PRICE_THRESHOLD_LOW] = "out_of_range"
    if threshold_high is not None and not (-500 <= threshold_high <= 10000):
        errors[CONF_PRICE_THRESHOLD_HIGH] = "out_of_range"

    if (
        threshold_low is not None
        and threshold_high is not None
        and CONF_PRICE_THRESHOLD_LOW not in errors
        and CONF_PRICE_THRESHOLD_HIGH not in errors
        and threshold_low >= threshold_high
    ):
        errors[CONF_PRICE_THRESHOLD_HIGH] = "threshold_low_gte_high"

    data = {
        CONF_LANG: lang,
        CONF_RESOLUTIONS: resolutions,
        CONF_DAYS_AHEAD: days_ahead,
        CONF_SCAN_INTERVAL: scan_interval,
        CONF_WINDOW_MINUTES: window_minutes,
        CONF_TOP_N_WINDOWS: top_n,
        CONF_TOP_N_PER_RES: top_n_per_res_raw,
        CONF_PRICE_THRESHOLD_LOW: threshold_low_raw,
        CONF_PRICE_THRESHOLD_HIGH: threshold_high_raw,
    }
    return data, errors


def _opcom_settings_schema(
    defaults: dict[str, Any] | None = None,
    suggested: dict[str, Any] | None = None,
) -> vol.Schema:
    """Schema voluptuous pentru setările OPCOM.

    `defaults` = câmpuri obligatorii (vol.Optional + default → nu pot fi golite).
    `suggested` = câmpuri opționale/golabile (vol.Optional + suggested_value → pot fi golite).
    """
    d = defaults or {}
    s = suggested or {}

    return vol.Schema(
        {
            # ── Câmpuri obligatorii (default) ──
            vol.Optional(
                CONF_LANG,
                default=d.get(CONF_LANG, DEFAULT_LANG),
            ): str,
            vol.Optional(
                CONF_RESOLUTIONS,
                default=d.get(
                    CONF_RESOLUTIONS,
                    ",".join(str(x) for x in DEFAULT_RESOLUTIONS),
                ),
            ): str,
            vol.Optional(
                CONF_DAYS_AHEAD,
                default=d.get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD),
            ): int,
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=d.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN),
            ): int,
            vol.Optional(
                CONF_WINDOW_MINUTES,
                default=d.get(CONF_WINDOW_MINUTES, DEFAULT_WINDOW_MINUTES),
            ): int,
            vol.Optional(
                CONF_TOP_N_WINDOWS,
                default=d.get(CONF_TOP_N_WINDOWS, DEFAULT_TOP_N_WINDOWS),
            ): int,

            # ── Câmpuri opționale/golabile (suggested_value) ──
            # Utilizatorul poate goli câmpul → se salvează "" → funcție dezactivată.
            vol.Optional(
                CONF_TOP_N_PER_RES,
                description={"suggested_value": s.get(CONF_TOP_N_PER_RES, "")},
            ): str,
            vol.Optional(
                CONF_PRICE_THRESHOLD_LOW,
                description={"suggested_value": s.get(CONF_PRICE_THRESHOLD_LOW, "")},
            ): str,
            vol.Optional(
                CONF_PRICE_THRESHOLD_HIGH,
                description={"suggested_value": s.get(CONF_PRICE_THRESHOLD_HIGH, "")},
            ): str,
        }
    )


# ──────────────────────────────────────────────
# ConfigFlow — configurare inițială
# ──────────────────────────────────────────────

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """ConfigFlow — configurare inițială OPCOM România."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pasul 1: Configurare parametri OPCOM (fără licență)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data, errors = _validate_opcom_settings(user_input)

            if not errors:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="OPCOM România", data=data)

        # Prima configurare — câmpuri opționale goale
        schema = _opcom_settings_schema()
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OpcomOptionsFlow:
        return OpcomOptionsFlow()


# ──────────────────────────────────────────────
# OptionsFlow — meniu: Setări + Licență
# ──────────────────────────────────────────────

# ── Textele licenței: aceleași în toate integrările hubinteligent (standardizate pe 12.11.2026) ──

_TIP_LICENTA = {
    # Tipurile emise de WebAPI (LICENSE_DURATION)
    "perpetual": ("permanentă", "permanent"),
    "annual": ("anuală", "annual"),
    "semi_annual": ("semestrială", "semi-annual"),
    "monthly": ("lunară", "monthly"),
}


def _tip_licenta(tip: str | None, is_ro: bool) -> str:
    """Eticheta tipului de licență primit de la server."""
    if not tip:
        return "necunoscut" if is_ro else "unknown"
    ro, en = _TIP_LICENTA.get(tip, (tip, tip))
    return ro if is_ro else en


def _zile_ramase(zile: int, is_ro: bool) -> str:
    """„o zi rămasă”, „5 zile rămase”, „20 de zile rămase” — „1 day left”, „5 days left”."""
    if zile <= 0:
        return "ultima zi" if is_ro else "last day"
    if not is_ro:
        return "1 day left" if zile == 1 else f"{zile} days left"
    if zile == 1:
        return "o zi rămasă"
    return f"{zile} {'de ' if zile % 100 >= 20 or zile % 100 == 0 else ''}zile rămase"


def _status_licenta(mgr, is_ro: bool) -> str:
    """Starea licenței, afișată la pasul „Licență”, deasupra câmpului „Cheie de licență”."""
    from homeassistant.util import dt as dt_util

    def data(moment: float) -> str:
        return dt_util.as_local(dt_util.utc_from_timestamp(moment)).strftime("%d.%m.%Y")

    obtine = f"🛒 {'Obține o licență' if is_ro else 'Get a license'}: {LICENSE_PURCHASE_URL}"
    if mgr.status == "licensed":
        linii = [f"✅ {'Licență activă' if is_ro else 'Active license'} ({_tip_licenta(mgr.license_type, is_ro)})"]
        if mgr.license_key_masked:
            linii[0] += f" — {mgr.license_key_masked}"
        if mgr.activated_at:
            linii.append(f"{'Activată la' if is_ro else 'Activated on'}: {data(mgr.activated_at)}")
        if mgr.license_expires_at:
            linii.append(f"📅 {'Expiră la' if is_ro else 'Expires on'}: {data(mgr.license_expires_at)}")
        else:
            linii.append("Valabilitate: nelimitată" if is_ro else "Validity: unlimited")
        return "\n".join(linii)
    if mgr.status == "trial":
        perioada = "Perioadă de test" if is_ro else "Free trial"
        return "\n".join([f"⏳ {perioada}: {_zile_ramase(mgr.trial_days_remaining, is_ro)}", "", obtine])
    if mgr.status == "expired" and mgr.has_license:
        linii = ["❌ Licență expirată" if is_ro else "❌ License expired"]
        if mgr.license_expires_at:
            linii.append(f"{'Expirată la' if is_ro else 'Expired on'}: {data(mgr.license_expires_at)}")
        return "\n".join([*linii, "", obtine])
    if mgr.status == "expired":
        return "\n".join(["❌ Perioada de test s-a încheiat" if is_ro else "❌ The free trial has ended", "", obtine])
    stare = "❌ Fără licență: senzorii nu se actualizează." if is_ro else "❌ No license: sensors do not update."
    return "\n".join([stare, "", obtine])


class OpcomOptionsFlow(config_entries.OptionsFlow):
    """OptionsFlow — meniu cu Setări și Licență separate."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Afișează meniul principal cu opțiunile disponibile."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "settings",
                "licenta",
            ],
        )

    # ── Pasul: Setări OPCOM ──────────────────────

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Modificare setări integrare OPCOM România."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data, errors = _validate_opcom_settings(user_input)

            if not errors:
                return self.async_create_entry(title="", data=data)

        # Valori curente (options > data > defaults)
        def _get(key: str, fallback):
            return self.config_entry.options.get(
                key, self.config_entry.data.get(key, fallback)
            )

        # Câmpuri obligatorii → default
        defaults = {
            CONF_LANG: _get(CONF_LANG, DEFAULT_LANG),
            CONF_RESOLUTIONS: ",".join(
                str(x)
                for x in _get(CONF_RESOLUTIONS, DEFAULT_RESOLUTIONS)
            ),
            CONF_DAYS_AHEAD: _get(CONF_DAYS_AHEAD, DEFAULT_DAYS_AHEAD),
            CONF_SCAN_INTERVAL: _get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN),
            CONF_WINDOW_MINUTES: _get(CONF_WINDOW_MINUTES, DEFAULT_WINDOW_MINUTES),
            CONF_TOP_N_WINDOWS: _get(CONF_TOP_N_WINDOWS, DEFAULT_TOP_N_WINDOWS),
        }

        # Câmpuri opționale → suggested_value (pot fi golite)
        # Normalizare: None → "" pentru afișare corectă
        raw_per_res = _get(CONF_TOP_N_PER_RES, "")
        raw_thr_low = _get(CONF_PRICE_THRESHOLD_LOW, "")
        raw_thr_high = _get(CONF_PRICE_THRESHOLD_HIGH, "")

        suggested = {
            CONF_TOP_N_PER_RES: str(raw_per_res) if raw_per_res and str(raw_per_res).lower() not in ("none", "") else "",
            CONF_PRICE_THRESHOLD_LOW: str(raw_thr_low) if raw_thr_low and str(raw_thr_low).lower() not in ("none", "") else "",
            CONF_PRICE_THRESHOLD_HIGH: str(raw_thr_high) if raw_thr_high and str(raw_thr_high).lower() not in ("none", "") else "",
        }

        schema = _opcom_settings_schema(defaults=defaults, suggested=suggested)
        return self.async_show_form(
            step_id="settings", data_schema=schema, errors=errors
        )

    # ── Pasul: Licență ──────────────────────────

    async def async_step_licenta(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Formular pentru activarea / vizualizarea licenței OPCOM România."""
        from .license import LicenseManager

        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}

        is_ro = self.hass.config.language == "ro"

        # Obține LicenseManager
        mgr: LicenseManager | None = self.hass.data.get(DOMAIN, {}).get(
            LICENSE_DATA_KEY
        )
        if mgr is None:
            mgr = LicenseManager(self.hass)
            await mgr.async_load()

        description_placeholders["license_status"] = _status_licenta(mgr, is_ro)

        if user_input is not None:
            cheie = user_input.get(CONF_LICENSE_KEY, "").strip()

            if not cheie:
                errors["base"] = "license_key_empty"
            elif len(cheie) < 10:
                errors["base"] = "license_key_invalid"
            else:
                # Activare prin API
                result = await mgr.async_activate(cheie)

                if result.get("success"):
                    from homeassistant.components import (
                        persistent_notification,
                    )

                    persistent_notification.async_create(
                        self.hass,
                        f"Licența OPCOM PZU România este activă ({_tip_licenta(mgr.license_type, True)}).",
                        title="Licență activată",
                        notification_id="opcom_license_activated",
                    )
                    return self.async_create_entry(
                        data=self.config_entry.options
                    )

                # Mapare erori API
                api_error = result.get("error", "unknown_error")
                # Codurile WebAPI /activate + erorile locale
                error_map = {
                    "invalid_key": "license_key_invalid",
                    "already_used": "license_already_used",
                    "expired_key": "license_key_expired",
                    "fingerprint_mismatch": "license_fingerprint_mismatch",
                    "network_error": "license_network_error",
                    "http_502": "license_network_error",
                    "http_503": "license_network_error",  # server temporar ocupat
                    "http_504": "license_network_error",
                }
                errors["base"] = error_map.get(api_error, "license_server_error")

        schema = vol.Schema(
            {
                vol.Optional(CONF_LICENSE_KEY): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                        suffix="OPCM-XXXX-XXXX-XXXX-XXXX",
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="licenta",
            data_schema=schema,
            errors=errors,
            description_placeholders=description_placeholders,
        )
