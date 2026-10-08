"""Connection policy and shared state for one desk.

Not a DataUpdateCoordinator: the desk pushes its state, there is nothing to poll. This
class owns the TimotionDesk, decides when to connect and disconnect (the desk accepts a
single connection, so by default it is released after a short idle time for the vendor
app), and tells the entities when to refresh.
"""

import logging
import time
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from typing import Any

from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from timotion_ble import TimotionDesk, TimotionError

from .const import (
    CONF_ALWAYS_CONNECTED,
    CONF_CONNECTION_MODE,
    CONF_IDLE_TIMEOUT,
    CONF_KEEP_AWAKE_INTERVAL,
    CONF_MAX_HEIGHT,
    CONF_MIN_HEIGHT,
    CONF_PRESET_HEIGHT,
    CONF_PRESET_NAME,
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_KEEP_AWAKE_INTERVAL,
    DOMAIN,
    FALLBACK_MAX_MM,
    FALLBACK_MIN_MM,
    MODE_ALWAYS,
    MODE_KEEP_AWAKE,
    MODE_ON_DEMAND,
    PRESET_COUNT,
    RECONNECT_DELAY,
    STALE_CHECK_INTERVAL,
    STALE_TIMEOUT,
    UNREACHABLE_AFTER,
)

_LOGGER = logging.getLogger(__name__)

type TimotionConfigEntry = ConfigEntry[DeskCoordinator]

CONNECT_ERRORS = (BleakError, TimotionError, TimeoutError)
# The desk advertises about once a second while awake. After about an hour idle it goes
# into standby and stops advertising; only a handset key wakes it, nothing over BLE does.
STANDBY_SILENCE = 30  # s without advertisements before a failed connect means standby

# Why HA cannot reach an awake-looking desk, as a repair issue translation key.
ISSUE_UNREACHABLE = "unreachable"  # heard by nobody: hung Bluetooth module, or standby
ISSUE_NO_CONNECTABLE = "no_connectable_scanner"  # heard only by scanners that cannot connect


def silent_for(hass: HomeAssistant, address: str, *, connectable: bool) -> float:
    """Seconds since the desk was last heard (inf if never).

    connectable=False covers every scanner, including passive ones such as Shelly
    devices, which hear the desk but can never connect to it.
    """
    info = bluetooth.async_last_service_info(hass, address, connectable=connectable)
    return bluetooth.MONOTONIC_TIME() - info.time if info else float("inf")


def heard_only_by_passive_scanners(hass: HomeAssistant, address: str) -> bool:
    """The desk is advertising, but no scanner that can connect hears it."""
    return (
        silent_for(hass, address, connectable=True) > STANDBY_SILENCE
        and silent_for(hass, address, connectable=False) <= STANDBY_SILENCE
    )


def issue_id(entry: ConfigEntry) -> str:
    return f"unreachable_{entry.entry_id}"


@callback
def async_report_unreachable(hass: HomeAssistant, entry: ConfigEntry, key: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id(entry),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=key,
        translation_placeholders={"name": entry.title},
    )


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
        self._was_connected = False
        self._started = time.monotonic()
        self._issue_key: str | None = None  # repair issue raised, by cause
        self._last_command = 0.0  # monotonic time of the last user command

    # -- options -------------------------------------------------------------------

    @property
    def connection_mode(self) -> str:
        if mode := self.entry.options.get(CONF_CONNECTION_MODE):
            return mode
        # Options saved before 0.1.5 had a boolean instead.
        return MODE_ALWAYS if self.entry.options.get(CONF_ALWAYS_CONNECTED) else MODE_ON_DEMAND

    @property
    def always_connected(self) -> bool:
        return self.connection_mode == MODE_ALWAYS

    @property
    def keep_awake_interval(self) -> timedelta:
        return timedelta(
            minutes=self.entry.options.get(CONF_KEEP_AWAKE_INTERVAL, DEFAULT_KEEP_AWAKE_INTERVAL)
        )

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
        entry.async_on_unload(
            async_track_time_interval(
                self.hass, self._async_check_stale, timedelta(seconds=STALE_CHECK_INTERVAL)
            )
        )
        if self.connection_mode == MODE_KEEP_AWAKE:
            entry.async_on_unload(
                async_track_time_interval(
                    self.hass, self._async_keep_awake, self.keep_awake_interval
                )
            )
        # Read height, limits and presets once; in always-connected mode this stays up.
        entry.async_create_background_task(
            self.hass, self._async_try_connect(), f"{entry.title} initial connect"
        )

    async def async_shutdown(self) -> None:
        self._shutdown = True
        ir.async_delete_issue(self.hass, DOMAIN, issue_id(self.entry))
        self._cancel_idle()
        if self._reconnect_unsub:
            self._reconnect_unsub()
            self._reconnect_unsub = None
        await self.desk.disconnect()

    # -- callbacks -----------------------------------------------------------------

    @callback
    def _on_desk_update(self) -> None:
        if self.desk.connected != self._was_connected:
            self._was_connected = self.desk.connected
            if self._was_connected:
                _LOGGER.debug("%s: connected via %s", self.entry.title, self._via())
                self._clear_unreachable()
            else:
                _LOGGER.debug("%s: disconnected", self.entry.title)
        if not self.desk.connected and self.always_connected:
            self._schedule_reconnect()
        self._notify()

    def _via(self) -> str:
        """The adapter or proxy that last heard the desk (connections go through it)."""
        info = bluetooth.async_last_service_info(self.hass, self.address, connectable=True)
        if info is None:
            return "unknown scanner"
        scanner = bluetooth.async_scanner_by_source(self.hass, info.source)
        name = scanner.name if scanner else info.source
        return f"{name} (rssi {info.rssi})"

    @callback
    def _on_advertisement(
        self, service_info: bluetooth.BluetoothServiceInfoBleak, _change: bluetooth.BluetoothChange
    ) -> None:
        # Follow the adapter or proxy that currently sees the desk best.
        self.desk.set_ble_device(service_info.device)
        self._clear_unreachable()
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
            if heard_only_by_passive_scanners(self.hass, self.address):
                raise HomeAssistantError(
                    f"{self.entry.title} is only heard by Bluetooth scanners that cannot "
                    "connect (such as Shelly devices). Check that a Bluetooth adapter or "
                    "ESPHome proxy with active connections is on near the desk."
                ) from err
            if self._silent_for() > STANDBY_SILENCE:
                raise HomeAssistantError(
                    f"{self.entry.title} is not advertising, probably in standby (it sleeps "
                    "after about an hour idle). Press any key on the handset, then retry."
                ) from err
            raise HomeAssistantError(f"Cannot connect to {self.entry.title}: {err}") from err

    def _silent_for(self) -> float:
        """Seconds since the last advertisement from the desk (inf if never seen)."""
        return silent_for(self.hass, self.address, connectable=True)

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

    async def _async_keep_awake(self, _now: datetime) -> None:
        """Connect briefly so the desk's standby timer (~60 min) restarts."""
        if self._shutdown or self.desk.connected or self._motions:
            return
        if not bluetooth.async_address_present(self.hass, self.address, connectable=True):
            # Reported by _check_unreachable once the silence lasts.
            _LOGGER.info("%s: keep awake skipped: desk not advertising", self.entry.title)
            return
        _LOGGER.debug("%s: keep awake", self.entry.title)
        started = time.monotonic()
        try:
            await self.desk.connect()  # returns once the first status frame arrived
        except CONNECT_ERRORS as err:
            _LOGGER.info("%s: keep awake failed: %s", self.entry.title, err)
            return
        if self._last_command >= started:
            # A command arrived meanwhile: keep the connection for its idle window.
            self._schedule_idle_disconnect()
            return
        # The connection itself restarted the standby timer: release the desk at once.
        await self.desk.disconnect()

    async def _async_check_stale(self, _now: datetime) -> None:
        """Close a dead connection; report a desk that went silent when it should not."""
        self._check_unreachable()
        age = self.desk.last_frame_age
        if self._shutdown or not self.desk.connected or age is None or age < STALE_TIMEOUT:
            return
        _LOGGER.info("%s: no data for %.0f s, closing the stale connection", self.entry.title, age)
        await self.desk.disconnect()  # always-connected mode reconnects from the callback

    @callback
    def _clear_unreachable(self) -> None:
        if self._issue_key:
            self._issue_key = None
            _LOGGER.info("%s: reachable again", self.entry.title)
            ir.async_delete_issue(self.hass, DOMAIN, issue_id(self.entry))

    @callback
    def _check_unreachable(self) -> None:
        if self.desk.connected or self._shutdown:
            return
        if time.monotonic() - self._started < UNREACHABLE_AFTER:
            return  # give the scanners time to hear the desk after startup
        silent = self._silent_for()
        if silent < UNREACHABLE_AFTER:
            return
        if heard_only_by_passive_scanners(self.hass, self.address):
            key = ISSUE_NO_CONNECTABLE  # never normal, whatever the connection mode
        elif self.connection_mode != MODE_ON_DEMAND:
            key = ISSUE_UNREACHABLE  # on demand, silence after an hour idle is standby
        else:
            return
        if key == self._issue_key:
            return
        self._issue_key = key
        _LOGGER.warning(
            "%s: not heard by any Bluetooth adapter or proxy that can connect for %.0f min%s",
            self.entry.title,
            min(silent, 10**6) / 60,
            " (only by scanners that cannot connect)" if key == ISSUE_NO_CONNECTABLE else "",
        )
        async_report_unreachable(self.hass, self.entry, key)

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
        self._last_command = time.monotonic()
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
        self._last_command = time.monotonic()
        if not self.desk.connected:
            return
        try:
            await self.desk.stop()
        except CONNECT_ERRORS as err:
            raise HomeAssistantError(f"Cannot stop {self.entry.title}: {err}") from err
        finally:
            self._schedule_idle_disconnect()
