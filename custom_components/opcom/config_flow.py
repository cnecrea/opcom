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

        # Informații pentru descrierea formularului
        server_status = mgr.status

        if server_status == "licensed":
            from datetime import datetime

            tip = mgr.license_type or "necunoscut"
            status_lines = [f"✅ Licență activă ({tip})"]

            if mgr.license_key_masked:
                status_lines[0] += f" — {mgr.license_key_masked}"

            if mgr.activated_at:
                act_date = datetime.fromtimestamp(
                    mgr.activated_at
                ).strftime("%d.%m.%Y %H:%M")
                status_lines.append(f"Activată la: {act_date}")

            if mgr.license_expires_at:
                exp_date = datetime.fromtimestamp(
                    mgr.license_expires_at
                ).strftime("%d.%m.%Y %H:%M")
                status_lines.append(f"📅 Expiră la: {exp_date}")
            elif tip == "perpetual":
                status_lines.append("Valabilitate: nelimitată (perpetuă)")

            description_placeholders["license_status"] = "\n".join(
                status_lines
            )

        elif server_status == "trial":
            days = mgr.trial_days_remaining
            if is_ro:
                status_lines = [
                    f"⏳ Evaluare — {days} zile rămase",
                    "",
                    f"🛒 Obține licență: {LICENSE_PURCHASE_URL}",
                ]
            else:
                status_lines = [
                    f"⏳ Trial — {days} days remaining",
                    "",
                    f"🛒 Get a license: {LICENSE_PURCHASE_URL}",
                ]
            description_placeholders["license_status"] = "\n".join(status_lines)

        elif server_status == "expired":
            from datetime import datetime

            status_lines = ["❌ Licență expirată"]

            if mgr.activated_at:
                act_date = datetime.fromtimestamp(
                    mgr.activated_at
                ).strftime("%d.%m.%Y")
                status_lines.append(f"Activată la: {act_date}")
            if mgr.license_expires_at:
                exp_date = datetime.fromtimestamp(
                    mgr.license_expires_at
                ).strftime("%d.%m.%Y")
                status_lines.append(f"Expirată la: {exp_date}")

            status_lines.append("")
            if is_ro:
                status_lines.append(
                    f"🛒 Obține licență: {LICENSE_PURCHASE_URL}"
                )
            else:
                status_lines.append(
                    f"🛒 Get a license: {LICENSE_PURCHASE_URL}"
                )

            description_placeholders["license_status"] = "\n".join(
                status_lines
            )
        else:
            if is_ro:
                status_lines = [
                    "❌ Fără licență — funcționalitate blocată",
                    "",
                    f"🛒 Obține licență: {LICENSE_PURCHASE_URL}",
                ]
            else:
                status_lines = [
                    "❌ No license — functionality blocked",
                    "",
                    f"🛒 Get a license: {LICENSE_PURCHASE_URL}",
                ]
            description_placeholders["license_status"] = "\n".join(status_lines)

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

                    _LICENSE_TYPE_RO = {
                        "monthly": "lunară",
                        "yearly": "anuală",
                        "perpetual": "perpetuă",
                        "trial": "evaluare",
                    }
                    tip_ro = _LICENSE_TYPE_RO.get(
                        mgr.license_type, mgr.license_type or "necunoscut"
                    )

                    persistent_notification.async_create(
                        self.hass,
                        f"Licența OPCOM România a fost activată cu succes! "
                        f"Tip: {tip_ro}.",
                        title="Licență activată",
                        notification_id="opcom_license_activated",
                    )
                    return self.async_create_entry(
                        data=self.config_entry.options
                    )

                # Mapare erori API
                api_error = result.get("error", "unknown_error")
                error_map = {
                    "invalid_key": "license_key_invalid",
                    "already_used": "license_already_used",
                    "expired_key": "license_key_expired",
                    "fingerprint_mismatch": "license_fingerprint_mismatch",
                    "invalid_signature": "license_server_error",
                    "network_error": "license_network_error",
                    "server_error": "license_server_error",
                }
                errors["base"] = error_map.get(api_error, "license_server_error")

        schema = vol.Schema(
            {
                vol.Optional(CONF_LICENSE_KEY): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT,
                        suffix="OPCOM-XXXX-XXXX-XXXX-XXXX",
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
