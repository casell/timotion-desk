"""Capture readers and spec check for the TiMOTION desk protocol.

Not part of the library. Two capture formats:
- btsnoop (Android HCI log, H4 datalink): ATT traffic of the desk connection only,
  selected by the LE Connection Complete event carrying the desk address.
- bluetoothctl notify dump: "[CHG] Attribute ... Value:" followed by hex lines.

    python tools/btsnoop.py [FILE ...]   (default: captures/handset_bluetoothctl.txt)

Files starting with the btsnoop magic are read as btsnoop, others as bluetoothctl dumps.
Phone btsnoop logs contain personal data (calls, contacts, locations): keep them out of
the repo (private/ is git-ignored).
"""

import os
import re
import struct
import sys
from collections import Counter
from pathlib import Path

# Needed to pick the desk connection out of a btsnoop file; probe.py scans by name.
DESK_ADDRESS = os.environ.get("TIMOTION_ADDRESS")
NAME_PREFIX = "stand UP"
NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
WRITE_HANDLE = 0x000E
NOTIFY_HANDLE = 0x000B

ATT_WRITE_REQ, ATT_WRITE_CMD, ATT_NOTIFY = 0x12, 0x52, 0x1B


def read_btsnoop(path, address=DESK_ADDRESS):
    """Yield (t_seconds, opcode, att_handle, value) for ATT PDUs of the desk connection.

    `address` defaults to $TIMOTION_ADDRESS. Handles every LE connection to it in the file; other devices sharing the
    log are dropped even if their ATT handles collide.
    """
    data = Path(path).read_bytes()
    magic, version, datalink = data[:8], *struct.unpack(">II", data[8:16])
    if magic != b"btsnoop\0" or datalink != 1002:
        raise ValueError(f"unsupported btsnoop file (datalink {datalink})")
    if not address:
        raise ValueError("desk address needed: set TIMOTION_ADDRESS")
    peer = bytes.fromhex(address.replace(":", ""))[::-1]  # HCI is little-endian
    desk_handles = set()
    partial = {}  # ACL handle -> (expected L2CAP length, buffer)
    t0 = None
    off = 16
    while off < len(data):
        _, incl_len, _, _, ts = struct.unpack(">IIIIq", data[off : off + 24])
        pkt = data[off + 24 : off + 24 + incl_len]
        off += 24 + incl_len
        t0 = ts if t0 is None else t0
        t = (ts - t0) / 1e6

        if pkt[0] == 0x04:  # HCI event
            code, params = pkt[1], pkt[3:]
            # LE Connection Complete (0x01) / Enhanced (0x0a): sub, status, handle, role, type, addr
            if code == 0x3E and params[0] in (0x01, 0x0A) and params[1] == 0:
                handle = struct.unpack("<H", params[2:4])[0] & 0x0FFF
                if params[6:12] == peer:
                    desk_handles.add(handle)
            elif code == 0x05 and params[0] == 0:  # Disconnection Complete
                desk_handles.discard(struct.unpack("<H", params[1:3])[0] & 0x0FFF)
            continue
        if pkt[0] != 0x02:  # ACL only
            continue

        hdr = struct.unpack("<H", pkt[1:3])[0]
        handle, pb = hdr & 0x0FFF, (hdr >> 12) & 0x3
        payload = pkt[5:]
        if pb in (0, 2):  # start of an L2CAP PDU
            partial[handle] = (struct.unpack("<H", payload[:2])[0] + 4, payload)
        elif handle in partial:  # continuation fragment
            partial[handle] = (partial[handle][0], partial[handle][1] + payload)
        else:
            continue
        expected, buf = partial[handle]
        if len(buf) < expected:
            continue
        del partial[handle]
        if handle not in desk_handles:
            continue
        cid = struct.unpack("<H", buf[2:4])[0]
        att = buf[4:expected]
        if cid != 0x0004 or len(att) < 3:
            continue
        yield t, att[0], struct.unpack("<H", att[1:3])[0], att[3:]


def read_bluetoothctl(path):
    """Yield notification values from a bluetoothctl `notify on` dump."""
    value = None
    for line in Path(path).read_text().splitlines():
        if line.startswith("[CHG] Attribute") and line.rstrip().endswith("Value:"):
            if value:
                yield bytes(value)
            value = []
        elif value is not None and (m := re.match(r"\s+((?:[0-9a-f]{2} )+)", line)):
            value += bytes.fromhex(m.group(1))
        elif value:
            yield bytes(value)
            value = None
    if value:
        yield bytes(value)


def checksum(frame):
    return sum(frame[2:-1]) & 0x7F


def check_frame(frame):
    """Return (description, problem) per the spec in PROTOCOL.md; problem is None if OK."""
    h = frame.hex(" ")
    if len(frame) < 3:
        return "empty", None if not frame else f"short frame {h}"
    ck_ok = checksum(frame) == frame[-1]

    if frame[0] == 0xDD:
        if len(frame) != 8 or not ck_ok:
            return "cmd?", f"bad length/checksum: {h}"
        if frame[1] == 0x01:
            return "handshake", None if frame[2:] == bytes(6) else f"handshake body: {h}"
        cc, ss, target, tail = frame[2], frame[3], (frame[4] << 8) | frame[5], frame[6]
        if cc == 0x30 and tail == 0x15 and not (ss or target):
            return "init", None
        if tail != 0x05:
            return "cmd?", f"byte 6 != 05: {h}"
        if cc == 0x30:
            return ("stop/idle", None) if not (ss or target) else (f"goto slot {ss} {target} mm", None)
        if cc in (0x31, 0x32):
            desc = "up" if cc == 0x31 else "down"
            return desc, None if not (ss or target) else f"{desc} with nonzero args: {h}"
        return "cmd?", f"unknown command 0x{cc:02x}: {h}"

    if frame[0] == 0x9D and frame[1] == 0x01:
        if len(frame) != 14 or not ck_ok:
            return "status?", f"bad length/checksum: {h}"
        cc, ss, st, height = frame[2], frame[3], frame[4], (frame[6] << 8) | frame[7]
        problems = []
        if frame[5] != 0x64:
            problems.append("byte 5 != 64")
        if any(frame[8:13]):
            problems.append("bytes 8-12 nonzero")
        if st & 0x0F != 0x05 or st & 0x80:
            problems.append(f"ST 0x{st:02x} outside 0x05|0x10|0x20|0x40")
        flags = [n for bit, n in ((0x10, "up"), (0x20, "down"), (0x40, "key")) if st & bit]
        # byte 3: 0x40 at min limit, 0x20 at max limit, low 5 bits slot echo
        limit = " AT-MIN" if ss & 0x40 else " AT-MAX" if ss & 0x20 else ""
        desc = (
            f"status cc={cc:02x} slot={ss & 0x1F} st={st:02x}[{','.join(flags) or 'idle'}]"
            f" {height} mm{limit}"
        )
        return desc, "; ".join(problems) + f": {h}" if problems else None

    if frame[0] == 0x9D and frame[1] == 0x02:
        if len(frame) != 19 or not ck_ok:
            return "config?", f"bad length/checksum: {h}"
        values = struct.unpack(">6H", frame[6:18])
        ok = frame[2] == 0x30 and frame[3] & ~0x60 == 0 and frame[4:6] == b"\x05\x64"
        limit = " AT-MIN" if frame[3] & 0x40 else " AT-MAX" if frame[3] & 0x20 else ""
        return f"config {values}{limit}", None if ok else f"header: {h}"

    if frame[0] == 0x9D and frame[1] == 0x00:
        if not ck_ok:
            return "handshake reply?", f"bad checksum: {h}"
        return f"handshake reply {frame[2:-1].hex(' ')}", None

    # 9d 03 / 9d 60 / 99 03 etc.: different trailer, ignored by spec
    return f"other (ignored) {h}", None


def timeline(events):
    """Print a timeline, collapsing runs of identical frames."""
    run = None  # [t_first, t_last, direction, frame, count]

    def flush():
        if run:
            t_first, t_last, direction, frame, n = run
            desc, _ = check_frame(frame)
            span = f" x{n} over {t_last - t_first:.1f}s" if n > 1 else ""
            print(f"{t_first:9.3f}  {direction}  {desc}{span}")

    for t, direction, frame in events:
        if run and run[2] == direction and run[3] == frame:
            run[1], run[4] = t, run[4] + 1
            continue
        flush()
        run = [t, t, direction, frame, 1]
    flush()


def report(title, frames):
    counts, problems = Counter(), []
    for direction, frame in frames:
        desc, problem = check_frame(frame)
        counts[(direction, desc.split(" ")[0])] += 1
        if problem:
            problems.append(problem)
    print(f"\n== {title}: {sum(counts.values())} frames")
    for (direction, kind), n in sorted(counts.items()):
        print(f"  {direction} {kind:<16} {n}")
    print(f"  problems: {len(problems)}")
    for p in problems:
        print(f"    {p}")


def main(argv):
    for path in argv[1:] or ["captures/handset_bluetoothctl.txt"]:
        if Path(path).read_bytes()[:8] == b"btsnoop\0":
            check_btsnoop(path)
        else:
            check_bluetoothctl(path)


def check_btsnoop(snoop):
    events, other_att = [], Counter()
    for t, op, handle, value in read_btsnoop(snoop):
        if op in (ATT_WRITE_CMD, ATT_WRITE_REQ) and handle == WRITE_HANDLE:
            events.append((t, "->", value))
        elif op == ATT_NOTIFY and handle == NOTIFY_HANDLE:
            events.append((t, "<-", value))
        else:
            other_att[(f"0x{op:02x}", f"0x{handle:04x}", value.hex())] += 1

    print(f"== {snoop}: timeline (desk connection only)")
    timeline(events)
    print("\nother ATT PDUs on the desk connection (GATT discovery, CCCD):")
    for (op, handle, value), n in sorted(other_att.items()):
        print(f"  op {op} handle {handle} {value[:40]} x{n}")
    report(snoop, [(d, f) for _, d, f in events])


def check_bluetoothctl(dump):
    handset = list(read_bluetoothctl(dump))
    print(f"\n== {dump}: status values seen")
    st = Counter((f[2], f[4]) for f in handset if f[:2] == b"\x9d\x01" and len(f) == 14)
    for (cc, s), n in sorted(st.items()):
        print(f"  cc=0x{cc:02x} st=0x{s:02x}  x{n}")
    report(dump, [("<-", f) for f in handset])


if __name__ == "__main__":
    main(sys.argv)
