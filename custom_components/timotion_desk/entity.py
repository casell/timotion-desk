"""Base entity for TiMOTION Desk."""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import Entity

from .coordinator import DeskCoordinator


class TimotionEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: DeskCoordinator, key: str) -> None:
        self.coordinator = coordinator
        self.desk = coordinator.desk
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = dr.DeviceInfo(
            connections={(dr.CONNECTION_BLUETOOTH, coordinator.address)},
            manufacturer="TiMOTION",
            model="TC15S",
            name=coordinator.entry.title,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return self.coordinator.available
