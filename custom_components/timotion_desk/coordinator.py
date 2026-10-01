"""Connection policy and shared state for one desk.

Not a DataUpdateCoordinator: the desk pushes its state, there is nothing to poll. This
class owns the TimotionDesk, decides when to connect and disconnect (the desk accepts a
single connection, so by default it is released after a short idle time for the vendor
app), and tells the entities when to refresh.
"""

import logging
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any

from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later
from timotion_ble import TimotionDesk, TimotionError

from .const import (
    CONF_ALWAYS_CONNECTED,
    CONF_IDLE_TIMEOUT,
    CONF_MAX_HEIGHT,
    CONF_MIN_HEIGHT,
    CONF_PRESET_HEIGHT,
    CONF_PRESET_NAME,
    DEFAULT_IDLE_TIMEOUT,
    FALLBACK_MAX_MM,
    FALLBACK_MIN_MM,
    PRESET_COUNT,
    RECONNECT_DELAY,
)

_LOGGER = logging.getLogger(__name__)

type TimotionConfigEntry = ConfigEntry[DeskCoordinator]

CONNECT_ERRORS = (BleakError, TimotionError, TimeoutError)


class DeskCoordinator:
    """Owns the desk connection for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: TimotionConfigEntry, desk: TimotionDesk) -> None:
        self.hass = hass
        self.entry = entry
        self.desk = desk
        self.address = desk.address
        self._listeners: list[CALLBACK_TYPE] = []
        self._idle_unsub: CALLBACK_TYPE | None = None
        self._reconnect_unsub: CALLBACK_TYPE | None = None
        self._motions = 0  # running motion tasks
        self._shutdown = False
        self._connect_failed = False  # log the first failure only

    # -- options -------------------------------------------------------------------

    @property
    def always_connected(self) -> bool:
        return self.entry.options.get(CONF_ALWAYS_CONNECTED, False)

    @property
    def idle_timeout(self) -> float:
        return self.entry.options.get(CONF_IDLE_TIMEOUT, DEFAULT_IDLE_TIMEOUT)

    @property
    def min_mm(self) -> int:
        """Bottom of the cover range: option, else the desk's limit."""
        if (cm := self.entry.options.get(CONF_MIN_HEIGHT)) is not None:
            return round(cm * 10)
        return self.desk.config.min_mm if self.desk.config else FALLBACK_MIN_MM

    @property
    def max_mm(self) -> int:
        if (cm := self.entry.options.get(CONF_MAX_HEIGHT)) is not None:
            return round(cm * 10)
        return self.desk.config.max_mm if self.desk.config else FALLBACK_MAX_MM

    @property
    def presets(self) -> list[tuple[int, str, int]]:
        """(index, name, mm) of the presets defined in the options."""
        presets = []
        for i in range(1, PRESET_COUNT + 1):
            cm = self.entry.options.get(CONF_PRESET_HEIGHT.format(i))
            if cm is not None:
                name = self.entry.options.get(CONF_PRESET_NAME.format(i)) or f"Preset {i}"
                presets.append((i, name, round(cm * 10)))
        return presets

    def position_to_mm(self, position: int) -> int:
        return round(self.min_mm + (self.max_mm - self.min_mm) * position / 100)

    def mm_to_position(self, mm: int) -> int:
        span = self.max_mm - self.min_mm
        if span <= 0:
            return 0
        return min(100, max(0, round((mm - self.min_mm) * 100 / span)))

    # -- state for entities --------------------------------------------------------

    @property
    def available(self) -> bool:
        # The desk stops advertising while connected, so a live connection counts too.
        return self.desk.connected or bluetooth.async_address_present(
            self.hass, self.address, connectable=True
        )

    @callback
    def async_add_listener(self, listener: CALLBACK_TYPE) -> CALLBACK_TYPE:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # -- lifecycle -----------------------------------------------------------------

    @callback
    def async_start(self) -> None:
        entry = self.entry
        entry.async_on_unload(self.desk.register_callback(self._on_desk_update))
        entry.async_on_unload(
            bluetooth.async_register_callback(
                self.hass,
                self._on_advertisement,
                bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
                bluetooth.BluetoothScanningMode.PASSIVE,
            )
        )
        entry.async_on_unload(
            bluetooth.async_track_unavailable(
                self.hass, self._on_unavailable, self.address, connectable=True
            )
        )
        # Read height, limits and presets once; in always-connected mode this stays up.
        entry.async_create_background_task(
            self.hass, self._async_try_connect(), f"{entry.title} initial connect"
        )

    async def async_shutdown(self) -> None:
        self._shutdown = True
        self._cancel_idle()
        if self._reconnect_unsub:
            self._reconnect_unsub()
            self._reconnect_unsub = None
        await self.desk.disconnect()

    # -- callbacks -----------------------------------------------------------------

    @callback
    def _on_desk_update(self) -> None:
        if not self.desk.connected and self.always_connected:
            self._schedule_reconnect()
        self._notify()

    @callback
    def _on_advertisement(
        self, service_info: bluetooth.BluetoothServiceInfoBleak, _change: bluetooth.BluetoothChange
    ) -> None:
        # Follow the adapter or proxy that currently sees the desk best.
        self.desk.set_ble_device(service_info.device)
        if self.always_connected and not self.desk.connected:
            self._schedule_reconnect(0)
        self._notify()

    @callback
    def _on_unavailable(self, _service_info: bluetooth.BluetoothServiceInfoBleak) -> None:
        self._notify()

    # -- connection ----------------------------------------------------------------

    async def _async_try_connect(self) -> bool:
        try:
            await self.desk.connect()
        except CONNECT_ERRORS as err:
            log = _LOGGER.debug if self._connect_failed else _LOGGER.info
            log("%s: cannot connect: %s", self.entry.title, err)
            self._connect_failed = True
            return False
        if self._connect_failed:
            _LOGGER.info("%s: connected again", self.entry.title)
        self._connect_failed = False
        self._schedule_idle_disconnect()
        return True

    async def _async_ensure_connected(self) -> None:
        self._cancel_idle()
        try:
            await self.desk.connect()
        except CONNECT_ERRORS as err:
            self._schedule_idle_disconnect()
            raise HomeAssistantError(f"Cannot connect to {self.entry.title}: {err}") from err

    @callback
    def _schedule_reconnect(self, delay: float = RECONNECT_DELAY) -> None:
        if self._reconnect_unsub is None and not self._shutdown:
            self._reconnect_unsub = async_call_later(self.hass, delay, self._async_reconnect)

    async def _async_reconnect(self, _now: datetime) -> None:
        self._reconnect_unsub = None
        if self._shutdown or self.desk.connected or not self.always_connected:
            return
        if not bluetooth.async_address_present(self.hass, self.address, connectable=True):
            return  # the next advertisement schedules a new attempt
        if not await self._async_try_connect():
            self._schedule_reconnect()

    @callback
    def _schedule_idle_disconnect(self) -> None:
        self._cancel_idle()
        if not self.always_connected and not self._shutdown:
            self._idle_unsub = async_call_later(
                self.hass, self.idle_timeout, self._async_idle_timeout
            )

    @callback
    def _cancel_idle(self) -> None:
        if self._idle_unsub:
            self._idle_unsub()
            self._idle_unsub = None

    async def _async_idle_timeout(self, _now: datetime) -> None:
        self._idle_unsub = None
        if self._motions or self.desk.moving or self.desk.handset_active:
            self._schedule_idle_disconnect()  # stay connected while the desk moves
            return
        _LOGGER.debug("%s: idle, disconnecting", self.entry.title)
        await self.desk.disconnect()

    # -- commands ------------------------------------------------------------------

    async def async_move(self, motion: Callable[[], Coroutine[Any, Any, Any]]) -> None:
        """Connect (raising on failure), then run the motion in the background."""
        await self._async_ensure_connected()
        self._motions += 1
        self.entry.async_create_background_task(
            self.hass, self._async_run(motion), f"{self.entry.title} motion"
        )

    async def _async_run(self, motion: Callable[[], Coroutine[Any, Any, Any]]) -> None:
        try:
            result = await motion()
            _LOGGER.debug("%s: motion %s at %s mm", self.entry.title, result, self.desk.height_mm)
        except CONNECT_ERRORS as err:
            _LOGGER.warning("%s: motion failed: %s", self.entry.title, err)
        finally:
            self._motions -= 1
            self._schedule_idle_disconnect()

    async def async_move_to(self, mm: int) -> None:
        await self.async_move(lambda: self.desk.move_to(mm))

    async def async_stop(self) -> None:
        if not self.desk.connected:
            return
        try:
            await self.desk.stop()
        except CONNECT_ERRORS as err:
            raise HomeAssistantError(f"Cannot stop {self.entry.title}: {err}") from err
        finally:
            self._schedule_idle_disconnect()
