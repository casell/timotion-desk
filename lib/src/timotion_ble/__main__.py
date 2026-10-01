"""Command line interface: python -m timotion_ble {scan,monitor,up,down,goto,stop}."""

import argparse
import asyncio
import logging
import sys

from bleak import BleakScanner
from bleak.backends.device import BLEDevice

from .desk import MAX_MOTION, TimotionDesk

NAME_PREFIX = "stand UP"


async def _scan(timeout: float) -> list[tuple[BLEDevice, int]]:
    # By name: the advertisement has only flags and the name (the NUS UUID is in the scan
    # response), and other devices advertise the Nordic UART service too.
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    return [
        (device, adv.rssi)
        for device, adv in found.values()
        if (adv.local_name or "").startswith(NAME_PREFIX)
    ]


async def _find(address: str | None) -> BLEDevice:
    if address:
        device = await BleakScanner.find_device_by_address(address, timeout=15)
    else:
        found = await _scan(10)
        device = max(found, key=lambda f: f[1])[0] if found else None
    if device is None:
        sys.exit("desk not found (is another app connected to it?)")
    return device


def _print_state(desk: TimotionDesk) -> None:
    flags = [f"moving {desk.moving}" if desk.moving else "idle"]
    if desk.handset_active:
        flags.append("handset")
    if desk.at_limit:
        flags.append(f"at {desk.at_limit} limit")
    print(f"{desk.height_mm} mm, {', '.join(flags)}", flush=True)


async def _run(args: argparse.Namespace) -> None:
    if args.command == "scan":
        for device, rssi in await _scan(args.timeout):
            print(f"{device.address}  {device.name}  rssi {rssi}")
        return

    desk = TimotionDesk(await _find(args.address))
    await desk.connect()

    def state() -> tuple:
        return (desk.height_mm, desk.moving, desk.handset_active, desk.at_limit)

    last = state()

    def on_change() -> None:
        nonlocal last
        if state() != last:
            last = state()
            _print_state(desk)

    desk.register_callback(on_change)
    try:
        if desk.config:
            c = desk.config
            print(f"limits {c.min_mm}-{c.max_mm} mm, handset presets {c.presets}, raw {c.values}")
        _print_state(desk)
        if args.command == "monitor":
            await asyncio.sleep(args.seconds if args.seconds else float("inf"))
        elif args.command in ("up", "down"):
            move = desk.move_up if args.command == "up" else desk.move_down
            print(f"result: {await move(args.seconds)}")
        elif args.command == "goto":
            print(f"result: {await desk.move_to(round(args.cm * 10))}")
        elif args.command == "stop":
            await desk.stop()
        if args.command != "monitor":
            await asyncio.sleep(1)  # let the coast show up in the output
    finally:
        await desk.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(prog="timotion-ble", description=__doc__)
    parser.add_argument("--address", help="desk MAC address (default: strongest found)")
    parser.add_argument("--debug", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="list desks in range")
    scan.add_argument("--timeout", type=float, default=5)
    monitor = sub.add_parser("monitor", help="print height and state changes")
    monitor.add_argument("seconds", nargs="?", type=float)
    for name in ("up", "down"):
        move = sub.add_parser(name, help=f"move {name} for SECONDS (max {MAX_MOTION:.0f})")
        move.add_argument("seconds", type=float)
    goto = sub.add_parser("goto", help="go to a height in cm")
    goto.add_argument("cm", type=float)
    sub.add_parser("stop", help="send stop frames")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING)
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
