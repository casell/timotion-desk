"""TimotionDesk against a simulated desk (time scaled ~10x down)."""

import asyncio

import pytest

from timotion_ble import desk as desk_module
from timotion_ble import protocol as p
from timotion_ble.desk import TimotionDesk

TICK = 0.01
STEP_MM = 4  # per tick, like the real desk per 100 ms
WATCHDOG = 0.12  # the real desk: ~1.2 s


def status_frame(command: int, fs: int, state: int, height: int) -> bytes:
    body = bytearray([0x9D, 0x01, command, fs, state, 0x64, height >> 8, height & 0xFF])
    body += bytes(6)
    body[-1] = p.checksum(body)
    return bytes(body)


def config_frame(values: tuple[int, ...]) -> bytes:
    body = bytearray(b"\x9d\x02\x30\x00\x05\x64")
    for v in values:
        body += v.to_bytes(2)
    body += b"\x00"
    body[-1] = p.checksum(body)
    return bytes(body)


class FakeDesk:
    """Mimics the observed controller behaviour closely enough for the client logic."""

    def __init__(self, height: int = 900) -> None:
        self.height = height
        self.min, self.max = 705, 1250
        self.direction: str | None = None  # current motion
        self.target: int | None = None
        self.command = 0x30
        self.slot = 0
        self.last_command = 0.0
        self.key = False
        self.writes: list[bytes] = []
        self.notify = None
        self.is_connected = True
        self.disconnected_callback = None
        self.task: asyncio.Task | None = None

    # BleakClient surface used by TimotionDesk
    async def start_notify(self, _uuid, callback) -> None:
        self.notify = callback
        self.task = asyncio.create_task(self._run())

    async def write_gatt_char(self, _uuid, data, response=None) -> None:
        if not self.is_connected:
            raise p_error()
        frame = bytes(data)
        self.writes.append(frame)
        loop = asyncio.get_running_loop()
        if frame == p.HANDSHAKE:
            self.notify(None, bytearray(config_frame((705, 1250, 782, 1101, 1206, 650))))
            return
        if frame[2] == 0x30 and frame[3] == 0:  # stop / idle / init
            self.command, self.slot, self.direction, self.target = 0x30, 0, None, None
            return
        self.last_command = loop.time()
        self.command = frame[2]
        wanted = {0x31: "up", 0x32: "down"}.get(frame[2])
        if frame[2] == 0x30:  # go-to
            self.slot, self.target = frame[3], int.from_bytes(frame[4:6])
            wanted = (
                "up" if self.target > self.height else "down" if self.target < self.height else None
            )
        if self.direction and wanted and wanted != self.direction:
            self.direction = None  # the desk stops and ignores the reversal
            return
        self.direction = wanted

    async def disconnect(self) -> None:
        self.is_connected = False
        if self.task:
            self.task.cancel()

    def drop(self) -> None:
        self.is_connected = False
        self.disconnected_callback(self)

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            if self.direction and loop.time() - self.last_command > WATCHDOG:
                self.direction = None
            if self.direction:
                sign = 1 if self.direction == "up" else -1
                step = STEP_MM
                if self.target is not None:
                    step = min(step, abs(self.target - self.height))
                self.height = min(max(self.height + sign * step, self.min), self.max)
                if self.height in (self.min, self.max) or self.height == self.target:
                    self.direction = None
            state = 0x05 | {"up": 0x10, "down": 0x20, None: 0}[self.direction]
            state |= 0x40 if self.key else 0
            fs = self.slot | (
                0x40 if self.height == self.min else 0x20 if self.height == self.max else 0
            )
            self.notify(None, bytearray(status_frame(self.command, fs, state, self.height)))
            await asyncio.sleep(TICK)


def p_error():
    from bleak.exc import BleakError

    return BleakError("disconnected")


@pytest.fixture
def fake(monkeypatch):
    fake = FakeDesk()
    for name, value in {
        "STREAM_INTERVAL": TICK,
        "HANDSHAKE_TIMEOUT": 0.5,
        "STATUS_TIMEOUT": 0.3,
        "SETTLE_TIMEOUT": 0.3,
        "GOTO_START_TIMEOUT": 0.2,
        "MAX_MOTION": 3.0,
    }.items():
        monkeypatch.setattr(desk_module, name, value)

    async def establish_connection(_cls, _device, _name, disconnected_callback, **_kw):
        fake.disconnected_callback = disconnected_callback
        fake.is_connected = True
        return fake

    monkeypatch.setattr(desk_module, "establish_connection", establish_connection)
    yield fake
    if fake.task:
        fake.task.cancel()


class Device:
    address = "AA:BB:CC:DD:EE:FF"
    name = "stand UP-1234"


async def connected(fake) -> TimotionDesk:
    desk = TimotionDesk(Device())
    await desk.connect()
    return desk


async def test_connect(fake):
    desk = await connected(fake)
    assert fake.writes[0] == p.HANDSHAKE and fake.writes[1] == p.INIT
    assert desk.connected and desk.height_mm == 900
    assert desk.config.min_mm == 705 and desk.config.presets == (782, 1101, 1206)


async def test_callbacks_fire_on_change_only(fake):
    desk = await connected(fake)
    calls = []
    unsubscribe = desk.register_callback(lambda: calls.append(desk.height_mm))
    await asyncio.sleep(0.1)
    assert calls == []  # idle frames repeat: no change
    await desk.move_to(920)
    assert calls and calls[-1] == 920
    unsubscribe()
    n = len(calls)
    await desk.move_to(940)
    assert len(calls) == n


async def test_move_to_arrives_and_stops(fake):
    desk = await connected(fake)
    assert await desk.move_to(1000) == "arrived"
    assert desk.height_mm == 1000
    gotos = [w for w in fake.writes if w[2] == 0x30 and w[3]]
    assert gotos and all(w == p.encode_goto(1000) for w in gotos)
    assert fake.writes[-3:] == [p.STOP] * 3


async def test_move_to_already_there(fake):
    desk = await connected(fake)
    n = len(fake.writes)
    assert await desk.move_to(901) == "arrived"
    assert len(fake.writes) == n  # nothing sent


async def test_move_to_clamps_to_limits(fake):
    desk = await connected(fake)
    assert await desk.move_to(2000) == "arrived"
    assert desk.height_mm == 1250 and desk.at_limit == "max"
    assert p.encode_goto(1250) in fake.writes


async def test_move_up_for_duration(fake):
    desk = await connected(fake)
    assert await desk.move_up(0.1) == "timeout"
    assert desk.height_mm > 900
    assert fake.writes[-3:] == [p.STOP] * 3


async def test_stop_cancels_motion(fake):
    desk = await connected(fake)
    move = asyncio.create_task(desk.move_to(1200))
    await asyncio.sleep(0.1)
    await desk.stop()
    assert await move == "stopped"
    assert fake.writes[-3:] == [p.STOP] * 3
    await asyncio.sleep(0.05)
    assert 900 < desk.height_mm < 1200


async def test_new_command_replaces_previous_and_reverses(fake):
    desk = await connected(fake)
    up = asyncio.create_task(desk.move_up(2.0))
    await asyncio.sleep(0.1)
    top = desk.height_mm
    assert await desk.move_down(0.1) == "timeout"
    assert await up == "stopped"
    assert desk.height_mm < top  # the down stream actually moved the desk


async def test_handset_key_aborts(fake):
    desk = await connected(fake)
    move = asyncio.create_task(desk.move_to(1200))
    await asyncio.sleep(0.1)
    fake.key = True
    assert await move == "handset"
    assert fake.writes[-3:] == [p.STOP] * 3


async def test_disconnect_mid_move(fake):
    desk = await connected(fake)
    move = asyncio.create_task(desk.move_up(2.0))
    await asyncio.sleep(0.1)
    fake.drop()
    assert await move == "disconnected"
    assert not desk.connected


async def test_motion_connects_on_demand(fake):
    desk = TimotionDesk(Device())
    assert not desk.connected
    assert await desk.move_to(950) == "arrived"
    assert desk.connected


async def test_caller_cancellation_stops_desk(fake):
    desk = await connected(fake)
    move = asyncio.create_task(desk.move_up(2.0))
    await asyncio.sleep(0.1)
    move.cancel()
    with pytest.raises(asyncio.CancelledError):
        await move
    await asyncio.sleep(0.05)
    assert fake.writes[-3:] == [p.STOP] * 3


async def test_corrupt_frames_dropped(fake):
    desk = await connected(fake)
    desk._on_notify(
        None, bytearray.fromhex("9d 02 30 00 05 64 02 c1 04 e2 1b 3a 03 00 00 00 00 00 00 00")
    )
    desk._on_notify(None, bytearray())
    assert desk.config.values == (705, 1250, 782, 1101, 1206, 650)


async def test_disconnect(fake):
    desk = await connected(fake)
    await desk.disconnect()
    assert not desk.connected
