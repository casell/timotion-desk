import pytest

from timotion_ble import protocol as p
from timotion_ble.protocol import ConfigFrame, HandshakeReply, StatusFrame, UnknownFrame


def h(text: str) -> bytes:
    return bytes.fromhex(text)


# -- encoding ------------------------------------------------------------------------


def test_constant_commands():
    assert p.HANDSHAKE == h("dd 01 00 00 00 00 00 00")
    assert p.INIT == h("dd 00 30 00 00 00 15 45")
    assert p.STOP == h("dd 00 30 00 00 00 05 35")
    assert p.UP == h("dd 00 31 00 00 00 05 36")
    assert p.DOWN == h("dd 00 32 00 00 00 05 37")


def test_goto_uses_slot_1():
    assert p.encode_goto(796) == h("dd 00 30 01 03 1c 05 55")
    assert p.encode_command(p.Command.IDLE, 796) == p.encode_goto(796)
    assert p.encode_goto(1106)[3] == 0x01  # the app used slot 2; we never do


@pytest.mark.parametrize("target", [0, -1, 0x10000])
def test_goto_range(target):
    with pytest.raises(ValueError):
        p.encode_goto(target)


def test_target_requires_idle():
    with pytest.raises(ValueError):
        p.encode_command(p.Command.UP, 800)


def test_checksum_vectors():
    for frame in (
        "dd 00 30 01 03 1c 05 55",
        "dd 00 30 02 04 52 05 0d",
        "dd 00 30 00 03 1c 05 54",
        "9d 01 30 00 05 64 03 1c 00 00 00 00 00 38",
        "9d 02 30 00 05 64 02 c1 04 e2 03 0e 04 4d 04 b0 02 8a 64",
        "9d 00 35 12 00 02 02 8a 05 14 6e",
    ):
        assert p.checksum(h(frame)) == h(frame)[-1]


# -- parsing -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frame, expected, moving, handset",
    [
        (
            "9d 01 30 00 05 64 03 1c 00 00 00 00 00 38",
            StatusFrame(0x30, 0, None, 0x05, 796),
            None,
            False,
        ),
        (
            "9d 01 31 00 15 64 03 5f 00 00 00 00 00 0c",
            StatusFrame(0x31, 0, None, 0x15, 863),
            "up",
            False,
        ),
        (
            "9d 01 32 00 25 64 03 cb 00 00 00 00 00 09",
            StatusFrame(0x32, 0, None, 0x25, 971),
            "down",
            False,
        ),
        (
            "9d 01 30 02 15 64 04 52 00 00 00 00 00 01",
            StatusFrame(0x30, 2, None, 0x15, 1106),
            "up",
            False,
        ),
        (
            "9d 01 30 00 55 64 04 0a 00 00 00 00 00 77",
            StatusFrame(0x30, 0, None, 0x55, 1034),
            "up",
            True,
        ),
    ],
)
def test_status_vectors(frame, expected, moving, handset):
    status = p.parse_frame(h(frame))
    assert status == expected
    assert status.moving == moving
    assert status.handset_active is handset


def test_status_limit_flags():
    def status(fs: int) -> bytes:
        body = bytearray(h("9d 01 30 00 05 64 02 c2 00 00 00 00 00 00"))
        body[3] = fs
        body[-1] = p.checksum(body)
        return bytes(body)

    assert p.parse_frame(status(0x40)).at_limit == "min"
    assert p.parse_frame(status(0x20)).at_limit == "max"
    assert p.parse_frame(status(0x41)).slot == 1
    assert p.parse_frame(status(0x1F)).at_limit is None


def test_config_vectors():
    config = p.parse_frame(h("9d 02 30 00 05 64 02 c1 04 e2 03 0e 04 4d 04 b0 02 8a 64"))
    assert config == ConfigFrame(None, (705, 1250, 782, 1101, 1200, 650))
    assert (config.min_mm, config.max_mm, config.presets) == (705, 1250, (782, 1101, 1200))
    at_min = p.parse_frame(h("9d 02 30 40 05 64 02 c1 04 e2 03 0e 04 4d 04 b6 02 8a 2a"))
    assert at_min.at_limit == "min" and at_min.presets[2] == 1206
    at_max = p.parse_frame(h("9d 02 30 20 05 64 02 c1 04 e2 03 0e 04 4d 04 b6 02 8a 0a"))
    assert at_max.at_limit == "max"


def test_handshake_reply():
    reply = p.parse_frame(h("9d 00 35 12 00 02 02 8a 05 14 6e"))
    assert reply == HandshakeReply(h("35 12 00 02 02 8a 05 14"))


@pytest.mark.parametrize(
    "frame",
    [
        "",
        "9d",
        "9d 02 30 00 05 64 02 c1 04 e2 1b 3a 03 00 00 00 00 00 00 00",  # garbage tail
        "9d 02 30 00 05 64 02 c1 04 e2 03 0e 04 4d 04 b6 82 0e 03",  # wrong last bytes
        "9d 01 30 00 05 64 03 1c 00 00 00 00 00 39",  # bad checksum
        "9d 01 30 00 05 64 03 1c 00 00 00 00 38",  # short
        "dd 00 30 00 00 00 05 35",  # a command, not a notification
    ],
)
def test_invalid_frames(frame):
    assert p.parse_frame(h(frame)) is None


@pytest.mark.parametrize(
    "frame",
    [
        "9d 60 03 00 00 00 00 00 00 00 98 98 00 00 56 56",
        "99 03 00 00 00 00 00 00 00 98 98 03 03 5c 5c",
        "9d 03 00 00 00 00 00 00 00 98 98 03 03 5c 5c",
        "9d 04 00 00 00 00 00 00 00 98 98 03 03 76 76",
    ],
)
def test_unknown_frames(frame):
    assert p.parse_frame(h(frame)) == UnknownFrame(h(frame))


# -- capture replay ------------------------------------------------------------------


def test_handset_capture(handset_capture):
    statuses = [s for f in handset_capture if isinstance(s := p.parse_frame(f), StatusFrame)]
    assert len(statuses) == 61
    by_state = {s.state: (s.moving, s.handset_active) for s in statuses}
    assert by_state == {
        0x05: (None, False),
        0x25: ("down", False),
        0x45: (None, True),
        0x55: ("up", True),
        0x65: ("down", True),
    }


def test_probe_logs(probe_notifications):
    frames = [p.parse_frame(f) for f in probe_notifications]
    statuses = [f for f in frames if isinstance(f, StatusFrame)]
    configs = [f for f in frames if isinstance(f, ConfigFrame)]
    assert len(statuses) > 1000 and len(configs) > 1000
    assert all(700 <= s.height_mm <= 1260 for s in statuses)
    assert {s.at_limit for s in statuses} == {None, "min", "max"}
    assert all((c.min_mm, c.max_mm) == (705, 1250) for c in configs)
    # Every frame that failed to parse is empty or a corrupt 9d 01 / 9d 02.
    for raw, frame in zip(probe_notifications, frames):
        if frame is None:
            assert raw[:2] in (b"", b"\x9d\x01", b"\x9d\x02"), raw.hex(" ")
