"""Async client for a TiMOTION desk over BLE."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from . import protocol as p

_LOGGER = logging.getLogger(__name__)

STREAM_INTERVAL = 0.1  # command stream period; the desk tolerates up to ~0.8 s
HANDSHAKE_TIMEOUT = 5.0
STATUS_TIMEOUT = 3.0  # wait for the first status frame after init
SETTLE_TIMEOUT = 3.0  # wait for idle before starting a new motion
GOTO_START_TIMEOUT = 2.0  # a go-to that never starts moving is done (already there)
MAX_MOTION = 30.0  # hard cap for any command stream
ARRIVED_MM = 2
STOP_FRAMES = 3

Result = Literal["arrived", "timeout", "handset", "disconnected", "stopped"]


class TimotionError(Exception):
    pass


class TimotionDesk:
    """One desk. Accepts a BLEDevice so it works with local adapters and HA proxies."""

    def __init__(self, ble_device: BLEDevice) -> None:
        self._ble_device = ble_device
        self._client: BleakClient | None = None
        self._status: p.StatusFrame | None = None
        self._config: p.ConfigFrame | None = None
        self._handshake_reply: p.HandshakeReply | None = None
        self._callbacks: list[Callable[[], None]] = []
        self._connect_lock = asyncio.Lock()
        self._motion: asyncio.Task[Result] | None = None
        self._got_9d = asyncio.Event()
        self._got_status = asyncio.Event()

    # -- state ---------------------------------------------------------------------

    @property
    def address(self) -> str:
        return self._ble_device.address

    @property
    def name(self) -> str:
        return self._ble_device.name or self._ble_device.address

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    @property
    def height_mm(self) -> int | None:
        return self._status.height_mm if self._status else None

    @property
    def moving(self) -> Literal["up", "down"] | None:
        return self._status.moving if self._status else None

    @property
    def handset_active(self) -> bool:
        return bool(self._status and self._status.handset_active)

    @property
    def at_limit(self) -> p.Limit | None:
        return self._status.at_limit if self._status else None

    @property
    def config(self) -> p.ConfigFrame | None:
        """Limits, handset presets and raw stored values, once the desk has sent them."""
        return self._config

    @property
    def handshake_reply(self) -> bytes | None:
        return self._handshake_reply.payload if self._handshake_reply else None

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Replace the BLEDevice (e.g. when HA sees the desk through another adapter)."""
        self._ble_device = ble_device

    def register_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Call `callback` on every status, config or connection change. Returns unsubscribe."""
        self._callbacks.append(callback)
        return lambda: self._callbacks.remove(callback)

    def _fire(self) -> None:
        for callback in list(self._callbacks):
            try:
                callback()
            except Exception:
                _LOGGER.exception("error in desk callback")

    # -- connection ----------------------------------------------------------------

    async def connect(self) -> None:
        """Connect, enable notifications, handshake and init. No-op if connected."""
        async with self._connect_lock:
            if self.connected:
                return
            _LOGGER.debug("%s: connecting", self.name)
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                self.name,
                disconnected_callback=self._on_disconnected,
                ble_device_callback=lambda: self._ble_device,
            )
            try:
                self._got_9d.clear()
                self._got_status.clear()
                await client.start_notify(p.NOTIFY_UUID, self._on_notify)
                await self._handshake(client)
                await client.write_gatt_char(p.WRITE_UUID, p.INIT, response=False)
            except BaseException:
                await client.disconnect()
                raise
            self._client = client
            try:
                await asyncio.wait_for(self._got_status.wait(), STATUS_TIMEOUT)
            except TimeoutError:
                _LOGGER.debug("%s: no status frame after init", self.name)
            _LOGGER.debug("%s: connected at %s mm", self.name, self.height_mm)
            self._fire()

    async def _handshake(self, client: BleakClient) -> None:
        # Frames can already be flowing on a fast reconnect; any 9d frame ends the loop.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + HANDSHAKE_TIMEOUT
        while not self._got_9d.is_set():
            if loop.time() > deadline:
                raise TimotionError(f"{self.name}: no answer to handshake")
            await client.write_gatt_char(p.WRITE_UUID, p.HANDSHAKE, response=False)
            try:
                await asyncio.wait_for(self._got_9d.wait(), STREAM_INTERVAL)
            except TimeoutError:
                pass

    async def disconnect(self) -> None:
        """Stop any motion (sending stop frames) and disconnect."""
        await self._cancel_motion()
        client, self._client = self._client, None
        if client is not None:
            await client.disconnect()
            self._fire()

    def _on_disconnected(self, client: BleakClient) -> None:
        if client is self._client:
            _LOGGER.debug("%s: disconnected", self.name)
            self._client = None
            self._fire()

    def _on_notify(self, _char: object, data: bytearray) -> None:
        if data[:1] == b"\x9d":
            self._got_9d.set()
        frame = p.parse_frame(bytes(data))
        if isinstance(frame, p.StatusFrame):
            self._got_status.set()
            if frame != self._status:
                self._status = frame
                self._fire()
        elif isinstance(frame, p.ConfigFrame):
            if frame != self._config:
                self._config = frame
                self._fire()
        elif isinstance(frame, p.HandshakeReply):
            self._handshake_reply = frame
        elif frame is None:
            if data:
                _LOGGER.debug("%s: dropping invalid frame %s", self.name, data.hex(" "))
        else:
            _LOGGER.debug("%s: ignoring frame %s", self.name, data.hex(" "))

    async def _write(self, frame: bytes) -> None:
        if self._client is None:
            raise TimotionError(f"{self.name}: not connected")
        await self._client.write_gatt_char(p.WRITE_UUID, frame, response=False)

    # -- motion --------------------------------------------------------------------

    async def move_up(self, timeout: float = MAX_MOTION) -> Result:
        """Move up until stop(), a new command, a handset key press or `timeout` s."""
        return await self._run(lambda: self._stream(p.UP, timeout))

    async def move_down(self, timeout: float = MAX_MOTION) -> Result:
        return await self._run(lambda: self._stream(p.DOWN, timeout))

    async def move_to(self, target_mm: int, timeout: float = MAX_MOTION) -> Result:
        """Go to `target_mm` (clamped to the desk's limits) and stop there."""
        return await self._run(lambda: self._goto(target_mm, timeout))

    async def stop(self) -> None:
        """Cancel any motion and send stop frames. Cannot stop a held handset key."""
        if self._motion is not None and not self._motion.done():
            await self._cancel_motion()
        elif self.connected:
            await self._send_stop()

    async def _run(self, motion: Callable[[], Awaitable[Result]]) -> Result:
        """Run one motion at a time: cancel the previous one, connect, settle, run."""
        await self._cancel_motion()
        await self.connect()
        task = asyncio.create_task(self._settle_then(motion))
        self._motion = task
        try:
            return await task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise  # our caller was cancelled, not the motion
            return "stopped"

    async def _settle_then(self, motion: Callable[[], Awaitable[Result]]) -> Result:
        # The desk ignores a new direction while it is still coasting: wait for idle.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE_TIMEOUT
        while self.moving and self.connected and loop.time() < deadline:
            await asyncio.sleep(STREAM_INTERVAL)
        return await motion()

    async def _cancel_motion(self) -> None:
        task, self._motion = self._motion, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task])

    async def _stream(
        self, frame: bytes, timeout: float, done: Callable[[], bool] | None = None
    ) -> Result:
        """Send `frame` every STREAM_INTERVAL until done, timeout, key press or disconnect.

        Always ends with stop frames, also when cancelled.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + min(timeout, MAX_MOTION)
        key_was_down = self.handset_active
        try:
            while True:
                if not self.connected:
                    return "disconnected"
                key_down = self.handset_active
                if key_down and not key_was_down:
                    _LOGGER.debug("%s: handset key pressed, aborting", self.name)
                    return "handset"
                key_was_down = key_down
                if done is not None and done():
                    return "arrived"
                if loop.time() >= deadline:
                    return "timeout"
                try:
                    await self._write(frame)
                except (BleakError, TimotionError) as err:
                    _LOGGER.debug("%s: write failed: %s", self.name, err)
                    return "disconnected"
                await asyncio.sleep(STREAM_INTERVAL)
        finally:
            await self._send_stop()

    async def _goto(self, target_mm: int, timeout: float) -> Result:
        if self._config is not None:
            target_mm = min(max(target_mm, self._config.min_mm), self._config.max_mm)
        loop = asyncio.get_running_loop()
        start = loop.time()
        seen_moving = False

        def done() -> bool:
            # The desk decelerates and stops on the target (or at a limit) by itself.
            nonlocal seen_moving
            if self.moving:
                seen_moving = True
                return False
            if seen_moving:
                return True
            # Never started: already there, or the desk refused.
            return loop.time() - start > GOTO_START_TIMEOUT

        height = self.height_mm
        if not self.moving and height is not None and abs(height - target_mm) <= ARRIVED_MM:
            return "arrived"
        result = await self._stream(p.encode_goto(target_mm), timeout, done)
        _LOGGER.debug("%s: go-to %s mm: %s at %s mm", self.name, target_mm, result, self.height_mm)
        return result

    async def _send_stop(self) -> None:
        for i in range(STOP_FRAMES):
            if not self.connected:
                return
            try:
                await self._write(p.STOP)
            except (BleakError, TimotionError):
                return
            if i < STOP_FRAMES - 1:
                await asyncio.sleep(STREAM_INTERVAL)
