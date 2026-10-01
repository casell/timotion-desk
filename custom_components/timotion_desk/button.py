"""Stop, configured presets and the handset presets stored in the desk."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DeskCoordinator, TimotionConfigEntry
from .entity import TimotionEntity

HANDSET_PRESETS = 3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TimotionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = [TimotionStop(coordinator, "stop")]
    entities += [
        TimotionPreset(coordinator, index, name, mm) for index, name, mm in coordinator.presets
    ]
    entities += [TimotionHandsetPreset(coordinator, n) for n in range(1, HANDSET_PRESETS + 1)]
    async_add_entities(entities)


class TimotionStop(TimotionEntity, ButtonEntity):
    async def async_press(self) -> None:
        await self.coordinator.async_stop()


class TimotionPreset(TimotionEntity, ButtonEntity):
    """A named height from the options (the vendor app's presets are app-side only)."""

    def __init__(self, coordinator: DeskCoordinator, index: int, name: str, mm: int) -> None:
        super().__init__(coordinator, f"preset_{index}")
        self._attr_translation_key = None
        self._attr_name = name
        self._mm = mm

    async def async_press(self) -> None:
        await self.coordinator.async_move_to(self._mm)


class TimotionHandsetPreset(TimotionEntity, ButtonEntity):
    """Go to handset memory N. Its height is read from the desk, recall is not possible."""

    def __init__(self, coordinator: DeskCoordinator, number: int) -> None:
        super().__init__(coordinator, f"handset_preset_{number}")
        self._attr_translation_key = "handset_preset"
        self._attr_translation_placeholders = {"number": str(number)}
        self._index = number - 1

    @property
    def available(self) -> bool:
        return super().available and self.desk.config is not None

    async def async_press(self) -> None:
        await self.coordinator.async_move_to(self.desk.config.presets[self._index])
