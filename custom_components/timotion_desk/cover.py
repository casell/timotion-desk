"""Desk as a cover: position 0-100 over the configured height range.

Opt-in (option "Show as cover"): actions on all covers of an area or of the whole home
would otherwise move the desk together with blinds and shutters.
"""

from typing import Any

from homeassistant.components.cover import ATTR_POSITION, CoverEntity, CoverEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_EXPOSE_COVER
from .coordinator import TimotionConfigEntry
from .entity import TimotionEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TimotionConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    cover = TimotionCover(entry.runtime_data, "desk")
    if entry.options.get(CONF_EXPOSE_COVER, False):
        async_add_entities([cover])
        return
    # Option off: drop a cover created earlier instead of leaving an orphaned entity.
    registry = er.async_get(hass)
    if entity_id := registry.async_get_entity_id("cover", entry.domain, cover.unique_id):
        registry.async_remove(entity_id)


class TimotionCover(TimotionEntity, CoverEntity):
    _attr_name = None
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.STOP
        | CoverEntityFeature.SET_POSITION
    )

    @property
    def current_cover_position(self) -> int | None:
        height = self.desk.height_mm
        return None if height is None else self.coordinator.mm_to_position(height)

    @property
    def is_closed(self) -> bool | None:
        position = self.current_cover_position
        return None if position is None else position == 0

    @property
    def is_opening(self) -> bool:
        return self.desk.moving == "up"

    @property
    def is_closing(self) -> bool:
        return self.desk.moving == "down"

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_move_to(self.coordinator.max_mm)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_move_to(self.coordinator.min_mm)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self.coordinator.async_move_to(self.coordinator.position_to_mm(kwargs[ATTR_POSITION]))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        await self.coordinator.async_stop()
