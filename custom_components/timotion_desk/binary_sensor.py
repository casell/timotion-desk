"""Moving and connection binary sensors."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TimotionConfigEntry
from .entity import TimotionEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TimotionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [TimotionMoving(coordinator, "moving"), TimotionConnected(coordinator, "connected")]
    )


class TimotionMoving(TimotionEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.MOVING

    @property
    def is_on(self) -> bool:
        return self.desk.moving is not None


class TimotionConnected(TimotionEntity, BinarySensorEntity):
    """Whether HA currently holds the desk's single BLE connection."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.desk.connected
