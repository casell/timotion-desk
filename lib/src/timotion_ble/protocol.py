"""TiMOTION desk BLE protocol: frame encoding and parsing. Pure functions, no I/O.

Commands (host -> desk) are 8 bytes: dd 00 CC SS HH HL TT CK.
Frames from the desk start with 9d; status (type 01) and config (type 02) frames carry
the checksum below. The checksum is 7 bits, so parsed frames are also checked against
their fixed bytes to reject corrupt notifications, which the desk sends regularly.
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Literal

NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

HANDSHAKE = bytes.fromhex("dd01000000000000")

# Go-to must use slot 01: 00 and 03 are ignored, and 1F / 3F overwrite stored presets.
_GOTO_SLOT = 0x01
_TAIL = 0x05
_INIT_TAIL = 0x15


class Command(IntEnum):
    IDLE = 0x30  # also stop, and go-to when a target height is given
    UP = 0x31
    DOWN = 0x32


def checksum(frame: bytes) -> int:
    """Checksum over everything but the two header bytes and the checksum itself."""
    return sum(frame[2:-1]) & 0x7F


def encode_command(command: Command, target_mm: int | None = None) -> bytes:
    """Encode a command frame. A target height is only valid with Command.IDLE (go-to)."""
    if target_mm is None:
        slot, height = 0, 0
    elif command is not Command.IDLE:
        raise ValueError("a target height requires Command.IDLE")
    elif not 0 < target_mm <= 0xFFFF:
        raise ValueError(f"target out of range: {target_mm}")
    else:
        slot, height = _GOTO_SLOT, target_mm
    return _frame(command, slot, height, _TAIL)


def encode_goto(target_mm: int) -> bytes:
    return encode_command(Command.IDLE, target_mm)


def _frame(command: int, slot: int, height: int, tail: int) -> bytes:
    body = bytes([0xDD, 0x00, command, slot, height >> 8, height & 0xFF, tail, 0])
    return body[:-1] + bytes([checksum(body)])


INIT = _frame(Command.IDLE, 0, 0, _INIT_TAIL)
STOP = encode_command(Command.IDLE)
UP = encode_command(Command.UP)
DOWN = encode_command(Command.DOWN)

# Status byte (ST) bits and the flags/slot byte (FS) shared by status and config frames.
_ST_UP, _ST_DOWN, _ST_KEY = 0x10, 0x20, 0x40
_FS_AT_MAX, _FS_AT_MIN, _FS_SLOT = 0x20, 0x40, 0x1F

Limit = Literal["min", "max"]


def _at_limit(fs: int) -> Limit | None:
    if fs & _FS_AT_MIN:
        return "min"
    if fs & _FS_AT_MAX:
        return "max"
    return None


@dataclass(frozen=True, slots=True)
class StatusFrame:
    """9d 01: current command echo, slot echo, limit flag, motion state and height."""

    command: int
    slot: int
    at_limit: Limit | None
    state: int
    height_mm: int

    @property
    def moving(self) -> Literal["up", "down"] | None:
        if self.state & _ST_UP:
            return "up"
        if self.state & _ST_DOWN:
            return "down"
        return None

    @property
    def handset_active(self) -> bool:
        return bool(self.state & _ST_KEY)


@dataclass(frozen=True, slots=True)
class ConfigFrame:
    """9d 02: values stored in the desk.

    values: min limit, max limit, handset presets M1-M3, and one value of unknown
    meaning (650 on a factory desk).
    """

    at_limit: Limit | None
    values: tuple[int, int, int, int, int, int]

    @property
    def min_mm(self) -> int:
        return self.values[0]

    @property
    def max_mm(self) -> int:
        return self.values[1]

    @property
    def presets(self) -> tuple[int, int, int]:
        return self.values[2:5]


@dataclass(frozen=True, slots=True)
class HandshakeReply:
    """9d 00: sent after a handshake (not always). Meaning unknown."""

    payload: bytes


@dataclass(frozen=True, slots=True)
class UnknownFrame:
    """Any other 9d / 99 frame (9d 03, 9d 04, 9d 60, 99 03...). Not checksummed."""

    data: bytes


Frame = StatusFrame | ConfigFrame | HandshakeReply | UnknownFrame


def parse_frame(data: bytes) -> Frame | None:
    """Parse one notification. Returns None for empty, corrupt or foreign data."""
    if len(data) < 3 or data[0] not in (0x9D, 0x99):
        return None
    kind = data[1] if data[0] == 0x9D else None
    valid = checksum(data) == data[-1]

    if kind == 0x01:
        if len(data) != 14 or not valid or data[5] != 0x64 or any(data[8:13]):
            return None
        return StatusFrame(
            command=data[2],
            slot=data[3] & _FS_SLOT,
            at_limit=_at_limit(data[3]),
            state=data[4],
            height_mm=int.from_bytes(data[6:8]),
        )
    if kind == 0x02:
        if len(data) != 19 or not valid or data[2] != 0x30 or data[4:6] != b"\x05\x64":
            return None
        values = tuple(int.from_bytes(data[i : i + 2]) for i in range(6, 18, 2))
        return ConfigFrame(at_limit=_at_limit(data[3]), values=values)
    if kind == 0x00:
        if len(data) != 11 or not valid:
            return None
        return HandshakeReply(payload=bytes(data[2:-1]))
    return UnknownFrame(data=bytes(data))
