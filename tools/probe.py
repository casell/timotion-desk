"""Phase 0 hardware probe for the TiMOTION desk. Not part of the library.

    python tools/probe.py monitor [SEC]           log decoded frames (default: until Ctrl-C)
    python tools/probe.py up SEC [--no-stop]      stream up for SEC s, then stop frames
    python tools/probe.py down SEC [--no-stop]
    python tools/probe.py goto MM|+MM|-MM [--slot N] [--for SEC] [--no-stop]
    python tools/probe.py reverse SEC1 SEC2       up SEC1 then down SEC2, no stop in between
    python tools/probe.py idle SEC                connect, send nothing, watch for disconnect
    python tools/probe.py slotprobe SLOT MM|+MM|-MM --write-ok [--frames N]
                                   send N go-to frames with an arbitrary slot byte and
                                   diff the 9d 02 values before stop, and after stop.
                                   Exit 3 if a travel limit ([0]/[1]) changed, 2 if any
                                   other value changed, 0 if nothing changed.

Global options (before the subcommand): --interval S (stream period, 0.05-1.0),
--stops N (stop frames after a motion, default 3), --no-handshake, --no-init,
--ignore-key (keep streaming while a handset key is pressed), --log FILE (raw frames),
--cue SEC:TEXT (repeatable: print a banner with a bell SEC s after the command starts).

After every motion the script keeps logging until the desk reports idle, and prints how
far and how long it kept moving after the last command frame. Motion streams are capped
at 30 s, abort on a handset key press unless --ignore-key, and go-to targets are limited
to +/-100 mm from the current height.
"""

import argparse
import asyncio
import struct
import sys
import time
import warnings
from pathlib import Path

from bleak import BleakClient, BleakScanner

sys.path.insert(0, str(Path(__file__).parent))
from btsnoop import DESK_ADDRESS, NAME_PREFIX, NUS_SERVICE_UUID, check_frame  # noqa: E402

NOTIFY_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
WRITE_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
HANDSHAKE = bytes.fromhex("dd01000000000000")
INTERVAL = 0.1
warnings.filterwarnings("ignore", message="Using default MTU")
MAX_MOTION = 30.0
MAX_RELATIVE_MM = 100


def command(cc, slot=0, mm=0, tail=0x05):
    body = bytes([0xDD, 0x00, cc, slot, mm >> 8, mm & 0xFF, tail])
    return body + bytes([sum(body[2:]) & 0x7F])


INIT = command(0x30, tail=0x15)
STOP = command(0x30)
UP = command(0x31)
DOWN = command(0x32)


class Probe:
    def __init__(self, raw, log_file=None):
        self.raw = raw
        self.log_file = open(log_file, "a") if log_file else None
        self.t0 = time.monotonic()
        self.status = None  # last 9d 01 frame
        self.last = {}  # frame type -> last frame, for change-only logging
        self.got_9d = asyncio.Event()
        self.config = None  # last valid 9d 02 values
        self.config_event = asyncio.Event()
        self.disconnected = asyncio.Event()

    def log(self, msg):
        print(f"{time.monotonic() - self.t0:8.3f}  {msg}", flush=True)

    @property
    def height(self):
        return (self.status[6] << 8) | self.status[7] if self.status else None

    @property
    def st(self):
        return self.status[4] if self.status else None

    def record(self, direction, frame):
        if self.log_file:
            data = frame.decode() if direction == "#" else frame.hex()
            self.log_file.write(f"{time.monotonic() - self.t0:.3f} {direction} {data}\n")
            self.log_file.flush()

    def on_notify(self, _char, data: bytearray):
        frame = bytes(data)
        self.record("<-", frame)
        if frame[:1] == b"\x9d":
            self.got_9d.set()
        if frame[:2] == b"\x9d\x01" and len(frame) == 14:
            self.status = frame
        if frame[:2] == b"\x9d\x02" and check_frame(frame)[1] is None:
            self.config = struct.unpack(">6H", frame[6:18])
            self.config_event.set()
        kind = frame[:2]
        if self.raw or self.last.get(kind) != frame:
            desc, problem = check_frame(frame)
            self.log(f"<- {desc}" + (f"  !! {problem}" if problem else ""))
        self.last[kind] = frame

    async def send(self, client, frame, quiet=False):
        await client.write_gatt_char(WRITE_UUID, frame, response=False)
        self.record("->", frame)
        if not quiet or self.raw:
            self.log(f"-> {check_frame(frame)[0]}  [{frame.hex(' ')}]")


async def connect(probe, address, handshake=True, init=True):
    if address:
        probe.log(f"scanning for {address}")
        device = await BleakScanner.find_device_by_address(address, timeout=15)
    else:
        probe.log(f"scanning for a '{NAME_PREFIX}' desk (set --address or TIMOTION_ADDRESS)")
        device = await BleakScanner.find_device_by_filter(
            lambda _d, adv: (adv.local_name or "").startswith(NAME_PREFIX)
            and NUS_SERVICE_UUID in adv.service_uuids,
            timeout=15,
        )
    if device is None:
        sys.exit("desk not found (vendor app still connected?)")
    client = BleakClient(device, disconnected_callback=lambda _: probe.disconnected.set())
    await client.connect()
    probe.log(f"connected, mtu {client.mtu_size}")
    await client.start_notify(NOTIFY_UUID, probe.on_notify)

    if handshake:
        # Handshake until the first 9d frame.
        for i in range(50):
            await probe.send(client, HANDSHAKE, quiet=i > 0)
            try:
                await asyncio.wait_for(probe.got_9d.wait(), 0.1)
                break
            except TimeoutError:
                pass
        else:
            sys.exit("no 9d frame after 5 s of handshakes")
        probe.log(f"handshake answered after {i + 1} frame(s)")
    else:
        try:
            await asyncio.wait_for(probe.got_9d.wait(), 5)
            probe.log("9d frames arrive without handshake")
        except TimeoutError:
            probe.log("NO 9d frame within 5 s without handshake")
    if init:
        await probe.send(client, INIT)
    else:
        probe.log("init skipped")
    return client


async def wait_status(probe, timeout=3):
    end = time.monotonic() + timeout
    while probe.status is None and time.monotonic() < end:
        await asyncio.sleep(0.05)
    if probe.status is None:
        sys.exit("no status frame")


async def stream(probe, client, frame, seconds, until=None, ignore_key=False):
    """Send `frame` every INTERVAL for `seconds` (capped), or until `until()` is true."""
    seconds = min(seconds, MAX_MOTION)
    end = time.monotonic() + seconds
    key_seen = bool(probe.st & 0x40)
    reason = "time"
    first = True
    while time.monotonic() < end:
        if probe.disconnected.is_set():
            reason = "disconnect"
            break
        if probe.st & 0x40 and not key_seen and not ignore_key:
            reason = "handset key"
            break
        if until and until():
            reason = "arrived"
            break
        await probe.send(client, frame, quiet=not first)
        first = False
        await asyncio.sleep(INTERVAL)
    probe.log(f"stream ended: {reason}")
    return reason


async def settle(probe, client, stops):
    """Send `stops` stop frames (the app sends 3), then measure the coast until idle."""
    t_last, h_last = time.monotonic(), probe.height
    for _ in range(stops):
        await probe.send(client, STOP, quiet=True)
        await asyncio.sleep(INTERVAL)
    probe.log(f"-> stop/idle x{stops}")
    end = t_last + 10
    while time.monotonic() < end and probe.st & 0x30:
        await asyncio.sleep(0.05)
    moving = bool(probe.st & 0x30)
    probe.log(
        f"after last motion frame: {'STILL MOVING after' if moving else 'idle after'} "
        f"{time.monotonic() - t_last:.2f} s, height {h_last} -> {probe.height} mm "
        f"({probe.height - h_last:+d} mm)"
    )


CONFIG_NAMES = ("min", "max", "M1", "M2", "M3", "[5]")


async def fresh_config(probe, timeout=5):
    """Wait for the next valid 9d 02 frame; None on timeout."""
    probe.config_event.clear()
    try:
        await asyncio.wait_for(probe.config_event.wait(), timeout)
    except TimeoutError:
        return None
    return probe.config


def config_diff(before, after, target):
    if after is None:
        return "no config frame received"
    changes = [
        f"{n}: {b} -> {a}" + (" (= target)" if a == target else "")
        for n, b, a in zip(CONFIG_NAMES, before, after)
        if a != b
    ]
    return ", ".join(changes) or "no change"


async def cues(probe, specs):
    """Print operator cues at fixed times after the command starts."""
    start = time.monotonic()
    for sec, text in sorted((float(a), b) for a, b in (c.split(":", 1) for c in specs)):
        await asyncio.sleep(max(0, start + sec - time.monotonic()))
        print("\a", end="")
        probe.log(f">>>>>>>>>>  {text}  <<<<<<<<<<")
        probe.record("#", text.encode())


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--address", default=DESK_ADDRESS)
    p.add_argument("--raw", action="store_true", help="log every frame, not only changes")
    p.add_argument("--interval", type=float, default=0.1)
    p.add_argument("--stops", type=int, default=3)
    p.add_argument("--no-handshake", action="store_true")
    p.add_argument("--no-init", action="store_true")
    p.add_argument("--ignore-key", action="store_true")
    p.add_argument("--log", help="append raw frames to this file")
    p.add_argument("--cue", action="append", default=[], help="SEC:TEXT")
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("monitor")
    m.add_argument("seconds", nargs="?", type=float)
    for name in ("up", "down"):
        s = sub.add_parser(name)
        s.add_argument("seconds", type=float)
        s.add_argument("--no-stop", action="store_true")
    g = sub.add_parser("goto")
    g.add_argument("target", help="absolute mm, or +N / -N relative to current height")
    g.add_argument("--slot", type=int, default=0)
    g.add_argument("--for", dest="duration", type=float, default=MAX_MOTION,
                   help="stop streaming after SEC even if not arrived")
    g.add_argument("--no-stop", action="store_true")
    g.add_argument("--recall-test", action="store_true",
                   help="allow slots 4 and 8 (start a move, never wrote anything)")
    r = sub.add_parser("reverse")
    r.add_argument("up_seconds", type=float)
    r.add_argument("down_seconds", type=float)
    i = sub.add_parser("idle")
    i.add_argument("seconds", type=float)
    sp = sub.add_parser("slotprobe")
    sp.add_argument("slot", type=lambda v: int(v, 0))
    sp.add_argument("target", help="absolute mm, or +N / -N relative to current height")
    sp.add_argument("--frames", type=int, default=1)
    sp.add_argument("--write-ok", action="store_true", help="required: may write to the desk")
    args = p.parse_args()
    global INTERVAL
    INTERVAL = min(max(args.interval, 0.05), 1.0)
    stops = 0 if getattr(args, "no_stop", False) else args.stops
    key = args.ignore_key

    probe = Probe(args.raw, args.log)
    if probe.log_file:
        probe.log_file.write(f"# {time.strftime('%F %T')} {' '.join(sys.argv[1:])}\n")
    client = await connect(probe, args.address, not args.no_handshake, not args.no_init)
    cue_task = None
    try:
        await wait_status(probe)
        probe.log(f"current height {probe.height} mm")
        cue_task = asyncio.create_task(cues(probe, args.cue)) if args.cue else None

        if args.cmd in ("monitor", "idle"):
            wait = args.seconds if args.seconds else None
            try:
                await asyncio.wait_for(probe.disconnected.wait(), wait)
                probe.log("DESK DISCONNECTED")
            except TimeoutError:
                probe.log(f"still connected after {wait:.0f} s")

        elif args.cmd in ("up", "down"):
            frame = UP if args.cmd == "up" else DOWN
            await stream(probe, client, frame, args.seconds, ignore_key=key)
            await settle(probe, client, stops)

        elif args.cmd == "reverse":
            await stream(probe, client, UP, args.up_seconds, ignore_key=key)
            probe.log("reversing without stop")
            await stream(probe, client, DOWN, args.down_seconds, ignore_key=key)
            await settle(probe, client, stops)

        elif args.cmd == "goto":
            relative = args.target[0] in "+-"
            target = probe.height + int(args.target) if relative else int(args.target)
            if args.slot not in (1, 2) and not (args.recall_test and args.slot in (4, 8)):
                sys.exit("refusing: only slots 1 and 2 are safe (slot 63 overwrote presets)")
            if abs(target - probe.height) > MAX_RELATIVE_MM:
                sys.exit(f"refusing: {target} mm is more than {MAX_RELATIVE_MM} mm away")
            probe.log(f"goto {target} mm, slot {args.slot}")
            t_start = time.monotonic()
            await stream(
                probe,
                client,
                command(0x30, args.slot, target),
                args.duration,
                until=lambda: time.monotonic() - t_start > 1
                and not probe.st & 0x30
                and abs(probe.height - target) <= 2,
                ignore_key=key,
            )
            await settle(probe, client, stops)
            probe.log(f"final {probe.height} mm, target {target} mm")

        elif args.cmd == "slotprobe":
            if not args.write_ok:
                sys.exit("refusing: slotprobe may overwrite stored desk values, pass --write-ok")
            if not 0 <= args.slot <= 0xFF or not 1 <= args.frames <= 50:
                sys.exit("refusing: slot 0-255, frames 1-50")
            relative = args.target[0] in "+-"
            target = probe.height + int(args.target) if relative else int(args.target)
            if abs(target - probe.height) > MAX_RELATIVE_MM:
                sys.exit(f"refusing: {target} mm is more than {MAX_RELATIVE_MM} mm away")
            before = await fresh_config(probe)
            if before is None:
                sys.exit("no config frame before the test")
            probe.log(f"before: {dict(zip(CONFIG_NAMES, before))}")
            frame = command(0x30, args.slot, target)
            probe.log(f"slot 0x{args.slot:02x}, target {target} mm, {args.frames} frame(s)")
            h0 = probe.height
            for n in range(args.frames):
                await probe.send(client, frame, quiet=n > 0)
                await asyncio.sleep(INTERVAL)
            # Writes show up ~0.9 s later: wait before reading, both without and with stop.
            await asyncio.sleep(1.5)
            mid = await fresh_config(probe, 4)
            probe.log(f"1.5 s after frames, before stop: {config_diff(before, mid, target)}")
            await settle(probe, client, args.stops)
            await asyncio.sleep(1.5)
            after = await fresh_config(probe, 4)
            probe.log(f"after stop:  {config_diff(before, after, target)}")
            probe.log(f"height {h0} -> {probe.height} mm")
            final = after or mid or before
            if final[:2] != before[:2] or (mid and mid[:2] != before[:2]):
                probe.log("!!!!!!!!!! TRAVEL LIMITS CHANGED !!!!!!!!!!")
                sys.exit(3)
            if final != before or (mid and mid != before):
                sys.exit(2)
    finally:
        if cue_task:
            cue_task.cancel()
        if client.is_connected:
            await client.write_gatt_char(WRITE_UUID, STOP, response=False)
            await client.disconnect()
        probe.log("disconnected")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
