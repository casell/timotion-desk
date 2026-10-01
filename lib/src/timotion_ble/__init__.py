"""Control TiMOTION standing desks with a built-in BLE module."""

from .desk import Result, TimotionDesk, TimotionError
from .protocol import (
    NUS_SERVICE_UUID,
    ConfigFrame,
    HandshakeReply,
    StatusFrame,
    UnknownFrame,
    parse_frame,
)

__all__ = [
    "NUS_SERVICE_UUID",
    "ConfigFrame",
    "HandshakeReply",
    "Result",
    "StatusFrame",
    "TimotionDesk",
    "TimotionError",
    "UnknownFrame",
    "parse_frame",
]
