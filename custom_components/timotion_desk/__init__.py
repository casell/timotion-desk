"""The TiMOTION Desk integration."""

import time

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from timotion_ble import TimotionDesk

from .const import DOMAIN, UNREACHABLE_AFTER
from .coordinator import (
    ISSUE_NO_CONNECTABLE,
    DeskCoordinator,
    TimotionConfigEntry,
    async_report_unreachable,
    heard_only_by_passive_scanners,
    issue_id,
)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.NUMBER,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: TimotionConfigEntry) -> bool:
    address = entry.data[CONF_ADDRESS].upper()
    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        # Setup retries on its own. Only when the desk is awake but heard exclusively by
        # scanners that cannot connect, and has been for a while, tell the user.
        failing_since = hass.data.setdefault(DOMAIN, {}).setdefault(
            entry.entry_id, time.monotonic()
        )
        if heard_only_by_passive_scanners(hass, address):
            if time.monotonic() - failing_since >= UNREACHABLE_AFTER:
                async_report_unreachable(hass, entry, ISSUE_NO_CONNECTABLE)
            raise ConfigEntryNotReady(
                f"Desk {address} is only heard by Bluetooth scanners that cannot connect"
            )
        # Not advertising: out of range, in standby, or the vendor app holds the connection.
        raise ConfigEntryNotReady(f"Desk {address} not found by any connectable adapter")
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    ir.async_delete_issue(hass, DOMAIN, issue_id(entry))

    coordinator = DeskCoordinator(hass, entry, TimotionDesk(ble_device))
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start()

    async def _async_stop(_event: Event) -> None:
        await coordinator.async_shutdown()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop))
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: TimotionConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TimotionConfigEntry) -> bool:
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
        bluetooth.async_rediscover_address(hass, entry.runtime_data.address)
    return unload_ok
