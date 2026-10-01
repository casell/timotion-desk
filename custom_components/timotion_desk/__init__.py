"""The TiMOTION Desk integration."""

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from timotion_ble import TimotionDesk

from .coordinator import DeskCoordinator, TimotionConfigEntry

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
        # Not advertising: out of range, or the vendor app holds the connection.
        raise ConfigEntryNotReady(f"Desk {address} not found by any connectable adapter")

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
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_shutdown()
        bluetooth.async_rediscover_address(hass, entry.runtime_data.address)
    return unload_ok
