"""Target height: setting it moves the desk."""

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
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
    async_add_entities([TimotionTargetHeight(entry.runtime_data, "target_height")])


class TimotionTargetHeight(TimotionEntity, NumberEntity):
    _attr_device_class = NumberDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.CENTIMETERS
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX

    @property
    def native_min_value(self) -> float:
        return self.coordinator.min_mm / 10

    @property
    def native_max_value(self) -> float:
        return self.coordinator.max_mm / 10

    @property
    def native_value(self) -> float | None:
        # Shows where the desk is; setting a value starts a move there.
        height = self.desk.height_mm
        return None if height is None else height / 10

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_move_to(round(value * 10))
