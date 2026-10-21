# Changelog

## 0.1.10

- **Repair notice: unplug for a few minutes.** When the desk's Bluetooth module hangs, a power cut of a few
  seconds is not always enough (seen 2026-10-21): the notice now says to unplug it for about 5 minutes, and that
  restarting the Bluetooth proxies does not help. `PROTOCOL.md` documents the new cases.

## 0.1.9

- **Always connected no longer drops the connection every minute.** An idle desk stops
  sending data about 35 s after the last command it received, while the connection stays
  healthy (measured on a local adapter and through an ESPHome proxy). The stale-connection
  check introduced in 0.1.5 took that silence for a dead link: it closed the connection
  after 15 s and reconnected, in a ~65 s loop. Now a silent connection is probed with one
  idle/stop frame (never a motion command, and never during a move) and closed only if
  no frame comes back within 5 s.
- Library: `TimotionDesk.probe()`; `PROTOCOL.md` documents the idle silence.

## Earlier versions

- 0.1.8: repair notice when the desk is heard only by Bluetooth scanners that cannot connect.
- 0.1.7: shorter keep-awake connections; *Disconnect after idle* defaults to 20 s.
- 0.1.6: repair notice when the desk goes silent; the log names the proxy of each connection.
- 0.1.5: connection modes (on demand, keep awake, always connected); stale-connection check.
- 0.1.4: the cover entity is opt-in.
- 0.1.3: integration icon.
- 0.1.2: clear error when the desk is in standby.
- 0.1.1: discovery by name (the advertisement has no service UUID).
- 0.1.0: first release.
