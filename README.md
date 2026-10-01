# TiMOTION Desk for Home Assistant

Control a TiMOTION standing desk with a built-in Bluetooth module (tested with a TC15S
controller, BLE name `stand UP-…`, vendor app "Stand Up Pls") from Home Assistant.

This repository contains:

- [`custom_components/timotion_desk`](custom_components/timotion_desk): the Home
  Assistant integration (HACS)
- [`lib`](lib): [`timotion-ble`](lib/README.md), the standalone Python library and CLI it
  uses
- [`PROTOCOL.md`](PROTOCOL.md): the reverse-engineered BLE protocol
- [`tools`](tools) and [`captures`](captures): protocol tooling and the hardware test logs
  the protocol description is based on. The vendor-app capture the protocol was first
  decoded from is not published: phone Bluetooth logs contain personal data.

## Installation

With [HACS](https://hacs.xyz): *HACS → ⋮ → Custom repositories*, add
`https://github.com/casell/timotion-desk` with type *Integration*, install
"TiMOTION Desk" and restart Home Assistant.

Manually: copy `custom_components/timotion_desk` into your `config/custom_components`
folder and restart.

Either way Home Assistant installs the `timotion-ble` library from the wheel attached to
the matching GitHub release (see the manifest's `requirements`).

The desk is discovered automatically once Home Assistant's Bluetooth integration sees it.
You can also add it via *Settings → Devices & services → Add integration → TiMOTION Desk*.

## Entities

| Entity | |
|---|---|
| Cover | position 0–100 over the height range (open = highest, close = lowest), stop |
| Height sensor | cm, 1 decimal |
| Target height number | set a height in cm to move there |
| Moving binary sensor | |
| Connection binary sensor (diagnostic) | whether Home Assistant holds the connection |
| Stop button | |
| Preset buttons | up to 4 named heights from the options |
| Handset preset 1–3 buttons | go to the heights stored in the handset's memory keys |

## Options

- **Lowest / highest height**: the range of the cover and the target number. Empty means
  the travel limits configured on the desk.
- **Disconnect after idle**: seconds to keep the connection after the last move
  (default 60).
- **Always connected**: keep the connection for live height updates, also when the
  handset moves the desk.
- **Presets**: up to four named heights, one button each. The vendor app's presets live
  in the app, not in the desk, so they are not available here.

## Things to know

- **One connection at a time.** While the vendor app is connected, the desk does not
  advertise and Home Assistant shows it as unavailable. While Home Assistant is
  connected, the app cannot connect. By default Home Assistant connects when you send a
  command and releases the desk after the idle timeout. In that mode the height shown is
  the last one seen while connected; moves made with the handset afterwards appear on
  the next connection (or use *Always connected*).
- **Bluetooth proxies** must support connections: a local adapter or an ESPHome
  Bluetooth proxy with `bluetooth_proxy: active: true` works. Shelly devices cannot
  connect, even with their "active" scanning mode enabled, so they cannot control the
  desk. Home Assistant only offers the desk for setup once a connectable scanner hears
  it: in *Bluetooth → Advertisement monitor* the desk must show `connectable: true`.
- **The handset wins when held.** The desk ignores a held handset key while Home
  Assistant is moving it, but takes the key over when the move ends, and a stop from
  Home Assistant cannot stop a move driven by a held key. Pressing a handset key during
  a Home Assistant move aborts that move.
- If the connection drops mid-move, the desk keeps going for about 1.2 s (≈ 50 mm)
  before its own watchdog stops it.

## Development

```
uv venv .venv && uv pip install --python .venv/bin/python -e 'lib[test]'
cd lib && ../.venv/bin/python -m pytest

uv venv --python 3.14 .venv-ha
uv pip install --python .venv-ha/bin/python pytest-homeassistant-custom-component -e lib
.venv-ha/bin/python -m pytest          # from the repo root
```

The Home Assistant tests also need the requirements of HA's `bluetooth` and `usb`
components (see their `manifest.json`, or `.github/workflows/validate.yml`).

### Releasing

Bump the version in `lib/pyproject.toml` and in the manifest (`version` and the wheel URL
in `requirements`), commit, then publish a GitHub release tagged `vX.Y.Z`. The release
workflow checks the versions and attaches the library wheel to the release.
