# TiMOTION desk BLE protocol

Reverse-engineered for a TiMOTION TC15S controller with built-in BLE module, sold as
"stand UP" with the vendor app "Stand Up Pls". Heights and values below are from the
desk used for testing.

## Device
- Advertising data: only flags and the complete local name "stand UP- XXXX" (with a
  space), e.g. `02 01 06 0f 09 "stand UP- 0000"`. The Nordic UART service UUID is only in
  the scan response, so passive scanners never see it: match on the name prefix
  "stand UP" alone (other devices advertise the NUS service too, under other names).
  Single connection at a time: the vendor app "Stand Up Pls" must be disconnected.
- Nordic UART Service 6e400001-b5a3-f393-e0a9-e50e24dcca9e
  - 6e400003-...: notify, desk -> host (status frames)
  - 6e400002-...: write-without-response, host -> desk (commands)
- The protocol was first decoded from a btsnoop capture of the vendor app (session:
  connect, up, down, go to memory 1, up, up, save preset (app-side, no traffic), go to
  memory 1 (released early), go to memory 2). The capture is not published: phone HCI
  logs contain personal data (calls, phone number, locations, other devices). Everything
  below was then verified on the desk with `tools/probe.py`.

## Protocol
Checksum, both directions: sum(frame[2:-1]) & 0x7F. Validate length AND checksum on every
received frame: corrupt notifications are common (seen: 20-byte 9d 02 with a garbage tail,
19-byte 9d 02 with wrong last bytes). Drop bad frames with a debug log, never raise.

Heights: the desk reports real floor-to-desktop mm (base height 650, set on the handset,
is the tape-measured height at the physical bottom). It stops within +1 mm of stored
values (limits, handset presets): min limit 705 -> stops at 705 or 706, max 1250 -> 1251, preset 782 -> 783.

Commands, 8 bytes: dd 00 CC SS HH HL 05 CK
- handshake: dd 01 00 00 00 00 00 00, sent every ~100 ms after enabling notify until
  any 9d frame arrives (any type, including 9d 02; on a fast reconnect a 9d frame can
  arrive before the first handshake is even sent).
- init: dd 00 30 00 00 00 15 45, sent once after handshake
- idle/stop: dd 00 30 00 00 00 05 35
- up: dd 00 31 00 00 00 05 36 (stream every ~100 ms while moving)
- down: dd 00 32 00 00 00 05 37 (stream every ~100 ms while moving)
- go to height: dd 00 30 SS HH HL 05 CK, HH HL = target mm big-endian.
  SS: ONLY EVER SEND 01 (02 also works, from the capture). Investigated on hardware:
  00 and 03 ignored; 04 and 08 are plain go-to too (they use the sent target; no BLE
  command recalls M1-M3 or [5]); 10 and 20 do nothing (10 is echoed in the 9d 02 FS
  byte); 1F and 3F are DANGEROUS: no motion, but they overwrite stored values (9d 02
  [2]-[5]) with the target height, non-deterministically (the same frame wrote M1, then
  [5], then nothing; 10 frames wrote all four). Hidden desk state is involved, so
  writing presets over BLE is not usable. The desk echoes SS & 0x1F. Travel limits were
  never touched. Never send anything but 01 from the library.
  Stream every ~100 ms; the desk decelerates and stops exactly on target by itself while
  the stream continues, then reports idle. Then send stop. Tiny moves (+1, -2, +5 mm)
  land exactly. A target beyond a travel limit: the desk stops at the limit (or doesn't
  move if already there) and goes idle with the at-limit flag set, so move_to must clamp
  the target to the limits or treat idle + at-limit as done.
Motion is hold-to-run with a controller watchdog: without further frames the desk keeps
full speed for ~1.2 s, then stops (~+50 mm). After stop frames it coasts 16-23 mm in
~0.6-0.75 s, the same after a single stop frame as after x3 (app sends x3; keep x3 since
writes are unacknowledged). Stopping a go-to mid-move coasts the same (~23 mm).
Always end a manual move with stop frames.
Reversing: switching the stream from up to down without a stop makes the desk decelerate
and stop, and it then ignores the down stream (1.2 s of down frames, no motion). Send
stop and wait for idle before starting the opposite direction.
Stream interval: 300 ms and 800 ms streams moved the desk at full speed without stutter
(watchdog ~1.2 s). 100 ms is the default; slow links (proxies) are tolerated.
Speed ~38-40 mm/s. Latency: status echoes a new command after ~0.2-0.3 s, height starts
changing after ~0.45-0.6 s. The controller enforces the travel limits by itself (a down
stream stops at the min limit with ease-out).
No keepalive needed: an idle connection held 300 s with nothing sent after init.
Standby: about 60 minutes after the last activity (a handset key, or the end of the last
BLE connection) the desk goes into standby and stops advertising entirely: no scanner
hears it, nothing can connect, and the vendor app cannot reach it either. There is no BLE
wake-up: only a handset key press brings it back (it advertises again within seconds).
Measured: a move worked 39.5 min after the last handset press; 60 min after the last BLE
disconnect the advertisements stopped. An open connection prevents standby: with a
connection held (and nothing sent) the desk was still controllable after 71 min. The
timer restarts at the end of a connection; whether a short connection without any motion
also restarts it is assumed (keep-awake mode relies on it) but not separately verified.
While connected the desk sends about 10 frames per second; a connection that looks open
but carries nothing for several seconds is dead (seen with Bluetooth proxies, which can
hold a link the host lost: the desk then stops advertising until the link is closed).
The BLE module can also hang: seen twice (both times used through ESPHome proxies, last
good connection ~45 min of keep-awake connections, around a proxy being switched off).
The handset still works, but the desk does not advertise, a handset key does not bring
it back, restarting the proxies does not either; only a power cycle does (it advertises
again within seconds of being plugged back in).
On a warm reconnect frames arrive without handshake and motion works without init;
cold state untested, so always send both (cheap).
Handset vs BLE: while a BLE go-to streams, a handset key shows in ST (key bit) but is
ignored; when the stream ends a still-held key takes over, and BLE stop frames do NOT
stop a handset-driven move. So stop() cannot stop a held handset key, and the key bit is
the signal to abort a BLE move.
Single connection: while the vendor app is connected the desk stops advertising (scan
finds nothing); while we are connected the app cannot take over.
The app's memory presets are app-side; the handset has its own presets M1-M3 stored in the
desk (see 9d 02). The desk accepts any go-to target.

Status frames (notify), ~10 Hz while connected:
- type 01, 14 bytes: 9d 01 CC FS ST 64 HH HL 00 00 00 00 00 CK
  CC echoes the current BLE command (30 for handset moves and after stop);
  FS = flags|slot: slot = FS & 0x1F (echoes go-to slot), FS & 0x40 = at min limit,
  FS & 0x20 = at max limit (set on reaching the limit, cleared once the desk moves off);
  HH HL = height mm big-endian;
  ST: 0x05 idle base, |0x10 moving up, |0x20 moving down, |0x40 handset key pressed.
  ST 0x45 = key held but not moving (preset reached, or saving a preset).
  A handset preset recall looks like a held up/down key (cc=30, key bit, direction bit);
  the first frame can report the wrong direction.
- type 02, 19 bytes: 9d 02 30 FS 05 64 + six big-endian uint16 + CK, FS as above.
  Values: [0] min limit (705, confirmed), [1] max limit (1250, confirmed), [2] [3] [4]
  handset presets M1 M2 M3 (confirmed: saving on the handset updates the value and the
  frame), [5] unknown (650 originally, 1110 since the slot 1F/3F writes; re-setting the
  handset base height to 650 did not change it, and the reported height never shifted).
  Expose it raw only. The handset has up, down and memory keys 1-3; min, max and base
  height are set through it. 9d 02 frames pause while a command stream is running.
- type 00, 11 bytes: 9d 00 35 12 00 02 02 8a 05 14 6e, handshake reply. Not guaranteed
  (absent on fast reconnects). Parse and expose raw bytes; 02 8a = 650 may be the base
  height (to verify).
- other frames (9d 03, 9d 04, 9d 60, 99 03 ...; they end in a duplicated byte pair and
  don't use the checksum above): log at debug, ignore. Empty notifications also occur.

Test vectors (capture unless noted):
  dd 00 30 01 03 1c 05 55   go to 796 mm, slot 1
  dd 00 30 02 04 52 05 0d   go to 1106 mm, slot 2
  dd 00 30 00 03 1c 05 54   go to 796 mm, slot 0 (valid frame, ignored by the desk)
  9d 01 30 00 05 64 03 1c 00 00 00 00 00 38   idle, 796 mm
  9d 01 31 00 15 64 03 5f 00 00 00 00 00 0c   up, 863 mm
  9d 01 32 00 25 64 03 cb 00 00 00 00 00 09   down, 971 mm
  9d 01 30 02 15 64 04 52 00 00 00 00 00 01   goto slot 2, up, 1106 mm
  9d 01 30 00 55 64 04 0a 00 00 00 00 00 77   handset up key, 1034 mm (handset dump)
  9d 02 30 00 05 64 02 c1 04 e2 03 0e 04 4d 04 b0 02 8a 64
  9d 02 30 40 05 64 02 c1 04 e2 03 0e 04 4d 04 b6 02 8a 2a   at min limit, M3=1206 (hardware)
  9d 02 30 20 05 64 02 c1 04 e2 03 0e 04 4d 04 b6 02 8a 0a   at max limit (hardware)
  9d 02 30 00 05 64 02 c1 04 e2 04 b8 04 b8 04 b6 04 b8 30   after the slot 0x3F write
  9d 00 35 12 00 02 02 8a 05 14 6e   handshake reply
  bad (drop): 9d 02 30 00 05 64 02 c1 04 e2 1b 3a 03 00 00 00 00 00 00 00
  bad (drop): 9d 02 30 00 05 64 02 c1 04 e2 03 0e 04 4d 04 b6 82 0e 03
Fixtures: captures/handset_bluetoothctl.txt
(bluetoothctl notify dump, handset only), and captures/probe_*.log from tools/probe.py
(--log: "t direction hex" lines).
