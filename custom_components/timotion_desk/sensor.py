"""Desk height sensor."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import TimotionConfigEntry
from .entity import TimotionEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TimotionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([TimotionHeightSensor(entry.runtime_data, "height")])


class TimotionHeightSensor(TimotionEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfLength.CENTIMETERS
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        height = self.desk.height_mm
        return None if height is None else height / 10
