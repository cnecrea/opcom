# custom_components/opcom/__init__.py
# Integrare OPCOM România — inițializare cu licențiere conform STANDARD-LICENTA.md v3.5
"""Inițializarea integrării OPCOM România.

Arhitectura: UN SINGUR coordinator per config_entry.
Licențiere: conform STANDARD-LICENTA.md v3.3 — server-side, Ed25519, grace period.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components import persistent_notification
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, LICENSE_DATA_KEY, LICENSE_PURCHASE_URL
from .coordinator import OpcomCoordinator
from .license import LicenseManager, LICENSE_API_URL, INTEGRATION

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor", "binary_sensor"]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict):
    """Configurează integrarea globală OPCOM România."""
    return True



def _update_license_notifications(hass: HomeAssistant, mgr: LicenseManager) -> None:
    """Creează sau șterge notificările de expirare licență/trial."""
    if mgr.is_valid:
        ir.async_delete_issue(hass, DOMAIN, "trial_expired")
        ir.async_delete_issue(hass, DOMAIN, "license_expired")
        persistent_notification.async_dismiss(hass, "opcom_license_expired")
        return

    has_token = bool(mgr._data.get("activation_token"))

    if has_token:
        issue_id = "license_expired"
        notif_title = "OPCOM România — Licența a expirat"
        notif_message = (
            "Licența pentru integrarea **OPCOM România** a expirat.\n\n"
            "Senzorii sunt dezactivați până la reînnoirea licenței.\n\n"
            f"[Reînnoiește licența]({LICENSE_PURCHASE_URL})"
        )
    else:
        issue_id = "trial_expired"
        notif_title = "OPCOM România — Licența de probă a expirat"
        notif_message = (
            "Perioada de evaluare gratuită pentru integrarea **OPCOM România** s-a încheiat.\n\n"
            "Senzorii sunt dezactivați până la obținerea unei licențe.\n\n"
            f"[Obține o licență acum]({LICENSE_PURCHASE_URL})"
        )

    other_id = "license_expired" if issue_id == "trial_expired" else "trial_expired"
    ir.async_delete_issue(hass, DOMAIN, other_id)

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        is_persistent=True,
        learn_more_url=LICENSE_PURCHASE_URL,
        severity=ir.IssueSeverity.WARNING,
        translation_key=issue_id,
        translation_placeholders={"learn_more_url": LICENSE_PURCHASE_URL},
    )

    persistent_notification.async_create(
        hass,
        notif_message,
        title=notif_title,
        notification_id="opcom_license_expired",
    )

    _LOGGER.debug("[OPCOM] Notificare expirare creată: %s", issue_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Configurează integrarea OPCOM România dintr-un ConfigEntry."""
    _LOGGER.info("Se configurează integrarea %s (entry_id=%s).", DOMAIN, entry.entry_id)

    hass.data.setdefault(DOMAIN, {})

    # ══════════════════════════════════════════════
    # Inițializare License Manager (o singură instanță per domeniu)
    # Conform STANDARD-LICENTA.md §3.5
    # ══════════════════════════════════════════════
    if LICENSE_DATA_KEY not in hass.data.get(DOMAIN, {}):
        _LOGGER.debug("[OPCOM] Inițializez LicenseManager (prima entry)")
        license_mgr = LicenseManager(hass)
        # IMPORTANT: setăm referința ÎNAINTE de async_load() pentru a preveni
        # race condition-ul: async_load() face await HTTP, ceea ce cedează
        # event loop-ul. Fără această ordine, alte entry-uri concurente ar vedea
        # LICENSE_DATA_KEY ca lipsă și ar crea câte un LicenseManager duplicat,
        # generând N request-uri /check simultane (câte unul per entry).
        hass.data[DOMAIN][LICENSE_DATA_KEY] = license_mgr
        await license_mgr.async_load()
        _LOGGER.debug(
            "[OPCOM] LicenseManager: status=%s, valid=%s, fingerprint=%s...",
            license_mgr.status,
            license_mgr.is_valid,
            license_mgr.fingerprint[:16],
        )

        # ── Heartbeat periodic — intervalul vine de la server (via valid_until) ──
        from homeassistant.helpers.event import (
            async_track_point_in_time,
            async_track_time_interval,
        )
        from homeassistant.util import dt as dt_util

        interval_sec = license_mgr.check_interval_seconds
        _LOGGER.debug(
            "[OPCOM] Programez heartbeat periodic la fiecare %d secunde (%d ore)",
            interval_sec,
            interval_sec // 3600,
        )

        async def _heartbeat_periodic(_now) -> None:
            """Verifică statusul la server dacă cache-ul a expirat.

            Logică:
            1. Captează is_valid ÎNAINTE de heartbeat
            2. Dacă cache expirat → contactează serverul
            3. Captează is_valid DUPĂ heartbeat
            4. Dacă starea s-a schimbat → reload entries (tranziție curată)
            5. Reprogramează heartbeat-ul la intervalul actualizat de server
            """
            mgr: LicenseManager | None = hass.data.get(DOMAIN, {}).get(
                LICENSE_DATA_KEY
            )
            if not mgr:
                _LOGGER.debug("[OPCOM] Heartbeat: LicenseManager nu există, skip")
                return

            # Captează starea ÎNAINTE de heartbeat
            was_valid = mgr.is_valid

            if mgr.needs_heartbeat:
                _LOGGER.debug("[OPCOM] Heartbeat: cache expirat, verific la server")
                await mgr.async_heartbeat()

                # Captează starea DUPĂ heartbeat
                now_valid = mgr.is_valid

                # Detectează tranziții pe care async_check_status nu le-a prins
                # (ex: server inaccesibil + cache expirat → is_valid devine False)
                if was_valid and not now_valid:
                    _LOGGER.warning(
                        "[OPCOM] Licența a devenit invalidă — reîncarc senzorii"
                    )
                    _update_license_notifications(hass, mgr)
                    await mgr._async_reload_entries()
                elif not was_valid and now_valid:
                    _LOGGER.info(
                        "[OPCOM] Licența a redevenit validă — reîncarc senzorii"
                    )
                    _update_license_notifications(hass, mgr)
                    await mgr._async_reload_entries()

                # Reprogramează heartbeat-ul la intervalul actualizat de server
                new_interval = mgr.check_interval_seconds
                _LOGGER.debug(
                    "[OPCOM] Heartbeat: reprogramez la %d secunde (%d min)",
                    new_interval,
                    new_interval // 60,
                )
                # Oprește vechiul timer
                cancel_old = hass.data.get(DOMAIN, {}).get("_cancel_heartbeat")
                if cancel_old:
                    cancel_old()
                # Programează noul timer cu intervalul actualizat
                cancel_new = async_track_time_interval(
                    hass,
                    _heartbeat_periodic,
                    timedelta(seconds=new_interval),
                )
                hass.data[DOMAIN]["_cancel_heartbeat"] = cancel_new
            else:
                _LOGGER.debug("[OPCOM] Heartbeat: cache valid, nu e nevoie de verificare")

        cancel_heartbeat = async_track_time_interval(
            hass,
            _heartbeat_periodic,
            timedelta(seconds=interval_sec),
        )
        hass.data[DOMAIN]["_cancel_heartbeat"] = cancel_heartbeat
        _LOGGER.debug("[OPCOM] Heartbeat programat și stocat în hass.data")

        # ── Timer precis la valid_until (zero gap la expirare cache) ──
        # Conform STANDARD-LICENTA.md §3.5 / §6.2
        def _schedule_cache_expiry_check(mgr_ref: LicenseManager) -> None:
            """Programează un check EXACT la momentul expirării cache-ului."""
            # Anulează timer-ul anterior (dacă există)
            cancel_prev = hass.data.get(DOMAIN, {}).pop(
                "_cancel_cache_expiry", None
            )
            if cancel_prev:
                cancel_prev()

            valid_until = (mgr_ref._status_token or {}).get("valid_until")
            if not valid_until or valid_until <= 0:
                return

            expiry_dt = dt_util.utc_from_timestamp(valid_until)
            # Adaugă 2 secunde ca marjă (evită race condition cu cache check)
            expiry_dt = expiry_dt + timedelta(seconds=2)

            async def _on_cache_expiry(_now) -> None:
                """Callback executat EXACT la expirarea cache-ului."""
                mgr_now: LicenseManager | None = hass.data.get(
                    DOMAIN, {}
                ).get(LICENSE_DATA_KEY)
                if not mgr_now:
                    return

                was_valid = mgr_now.is_valid
                _LOGGER.debug(
                    "[OPCOM] Cache expirat — verific imediat la server"
                )
                await mgr_now.async_check_status()
                now_valid = mgr_now.is_valid

                if was_valid != now_valid:
                    if now_valid:
                        _LOGGER.info(
                            "[OPCOM] Licența a redevenit validă — reîncarc"
                        )
                    else:
                        _LOGGER.warning(
                            "[OPCOM] Licența a devenit invalidă — reîncarc"
                        )
                    _update_license_notifications(hass, mgr_now)
                    await mgr_now._async_reload_entries()

                # Programează următorul check (dacă serverul a dat valid_until nou)
                _schedule_cache_expiry_check(mgr_now)

            cancel_expiry = async_track_point_in_time(
                hass, _on_cache_expiry, expiry_dt
            )
            hass.data[DOMAIN]["_cancel_cache_expiry"] = cancel_expiry

            _LOGGER.debug(
                "[OPCOM] Cache expiry timer programat la %s",
                expiry_dt.isoformat(),
            )

        _schedule_cache_expiry_check(license_mgr)

        # ── Notificare re-enable (dacă a fost dezactivată anterior) ──
        was_disabled = hass.data.pop(f"{DOMAIN}_was_disabled", False)
        if was_disabled:
            await license_mgr.async_notify_event("integration_enabled")

        if not license_mgr.is_valid:
            _LOGGER.warning(
                "[OPCOM] Integrarea nu are licență validă. "
                "Senzorii vor afișa 'Licență necesară'."
            )
        elif license_mgr.is_trial_valid:
            _LOGGER.info(
                "[OPCOM] Perioadă de evaluare — %d zile rămase",
                license_mgr.trial_days_remaining,
            )
        else:
            _LOGGER.info(
                "[OPCOM] Licență activă — tip: %s",
                license_mgr.license_type,
            )

        # ── Verificare inițială notificări expirare licență/trial ──
        _update_license_notifications(hass, license_mgr)
    else:
        _LOGGER.debug(
            "[OPCOM] LicenseManager există deja (entry suplimentară)"
        )

    # ══════════════════════════════════════════════
    # Coordinator OPCOM (per entry)
    # ══════════════════════════════════════════════
    coordinator = OpcomCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    # Pornește timer-ul de graniță DUPĂ first_refresh
    coordinator.schedule_boundary_timer()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # ── Încărcăm platformele NECONDIȚIONAT (gating-ul e în sensor.py) ──
    # Conform STANDARD-LICENTA.md §3.5
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listener pentru modificarea opțiunilor
    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    _LOGGER.info(
        "Integrarea %s configurată (entry_id=%s).",
        DOMAIN, entry.entry_id,
    )
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reîncarcă integrarea când opțiunile se schimbă."""
    _LOGGER.info(
        "Opțiunile integrării %s s-au schimbat (entry_id=%s). Se reîncarcă...",
        DOMAIN, entry.entry_id,
    )
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Descărcarea intrării din config_entries."""
    _LOGGER.info(
        "[OPCOM] ── async_unload_entry ── entry_id=%s",
        entry.entry_id,
    )

    # Shutdown coordinator (oprește boundary timer)
    coordinator: OpcomCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator:
        await coordinator.async_shutdown()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    _LOGGER.debug("[OPCOM] Unload platforme: %s", "OK" if unload_ok else "EȘUAT")

    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)

        # Verifică dacă mai sunt entry-uri active
        remaining_entries = hass.config_entries.async_entries(DOMAIN)
        entry_ids_ramase = {e.entry_id for e in remaining_entries if e.entry_id != entry.entry_id}

        _LOGGER.debug(
            "[OPCOM] Entry-uri rămase după unload: %d (%s)",
            len(entry_ids_ramase),
            entry_ids_ramase or "niciuna",
        )

        if not entry_ids_ramase:
            _LOGGER.info("[OPCOM] Ultima entry descărcată — curăț domeniul complet")

            # ── Notificare lifecycle (înainte de cleanup!) ──
            mgr = hass.data[DOMAIN].get(LICENSE_DATA_KEY)
            if mgr and not hass.is_stopping:
                if entry.disabled_by:
                    await mgr.async_notify_event("integration_disabled")
                    hass.data[f"{DOMAIN}_was_disabled"] = True
                else:
                    hass.data.setdefault(f"{DOMAIN}_notify", {}).update({
                        "fingerprint": mgr.fingerprint,
                        "license_key": mgr._data.get("license_key", ""),
                    })
                    _LOGGER.debug(
                        "[OPCOM] Fingerprint salvat pentru async_remove_entry"
                    )

            # Oprește heartbeat-ul periodic
            cancel_hb = hass.data[DOMAIN].pop("_cancel_heartbeat", None)
            if cancel_hb:
                cancel_hb()
                _LOGGER.debug("[OPCOM] Heartbeat periodic oprit")

            # Oprește timer-ul de cache expiry
            cancel_ce = hass.data[DOMAIN].pop("_cancel_cache_expiry", None)
            if cancel_ce:
                cancel_ce()
                _LOGGER.debug("[OPCOM] Cache expiry timer oprit")

            # Elimină LicenseManager
            hass.data[DOMAIN].pop(LICENSE_DATA_KEY, None)
            _LOGGER.debug("[OPCOM] LicenseManager eliminat")

            # Elimină domeniul complet
            hass.data.pop(DOMAIN, None)
            _LOGGER.debug("[OPCOM] hass.data[%s] eliminat complet", DOMAIN)

            _LOGGER.info("[OPCOM] Cleanup complet — domeniul %s descărcat", DOMAIN)
    else:
        _LOGGER.error("[OPCOM] Unload EȘUAT pentru entry_id=%s", entry.entry_id)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Notifică serverul când integrarea e complet eliminată (ștearsă)."""
    _LOGGER.debug(
        "[OPCOM] ── async_remove_entry ── entry_id=%s",
        entry.entry_id,
    )

    remaining = hass.config_entries.async_entries(DOMAIN)
    if not remaining:
        notify_data = hass.data.pop(f"{DOMAIN}_notify", None)
        if notify_data and notify_data.get("fingerprint"):
            await _send_lifecycle_event(
                hass,
                notify_data["fingerprint"],
                notify_data.get("license_key", ""),
                "integration_removed",
            )


async def _send_lifecycle_event(
    hass: HomeAssistant, fingerprint: str, license_key: str, action: str
) -> None:
    """Trimite un eveniment lifecycle direct (fără LicenseManager).

    Folosit în async_remove_entry când LicenseManager nu mai există.
    Conform STANDARD-LICENTA.md §3.5 — sesiune partajată, nu aiohttp.ClientSession() nouă.
    """
    import hashlib
    import hmac as hmac_lib
    import json
    import time

    import aiohttp

    timestamp = int(time.time())
    payload = {
        "fingerprint": fingerprint,
        "timestamp": timestamp,
        "action": action,
        "license_key": license_key,
        "integration": INTEGRATION,
    }
    data = {k: v for k, v in payload.items() if k != "hmac"}
    msg = json.dumps(data, sort_keys=True).encode()
    payload["hmac"] = hmac_lib.new(
        fingerprint.encode(), msg, hashlib.sha256
    ).hexdigest()

    try:
        session = async_get_clientsession(hass)
        async with session.post(
            f"{LICENSE_API_URL}/notify",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "OPCOM-HA-Integration/1.0",
            },
        ) as resp:
            if resp.status == 200:
                result = await resp.json()
                if not result.get("success"):
                    _LOGGER.warning(
                        "[OPCOM] Server a refuzat '%s': %s",
                        action, result.get("error"),
                    )
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("[OPCOM] Nu s-a putut raporta '%s': %s", action, err)
