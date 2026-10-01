# timotion-ble

Async Python library and CLI for TiMOTION standing desks with a built-in BLE module
(tested with a TC15S controller sold as "stand UP", vendor app "Stand Up Pls").

- Height, motion state, handset activity and travel-limit flags, pushed ~10 times/s
- Move up / down, go to a height, stop
- Reads the limits and the handset memory presets stored in the desk
- Takes a bleak `BLEDevice`, so it works with local adapters and Home Assistant's
  Bluetooth stack (including ESPHome proxies with active connections)

## CLI

```
pip install timotion-ble
timotion-ble scan
timotion-ble monitor
timotion-ble goto 105        # cm
timotion-ble up 2            # seconds
timotion-ble --address AA:BB:CC:DD:EE:FF stop
```

## Library

```python
from bleak import BleakScanner
from timotion_ble import TimotionDesk

device = await BleakScanner.find_device_by_address("AA:BB:CC:DD:EE:FF")
desk = TimotionDesk(device)
desk.register_callback(lambda: print(desk.height_mm, desk.moving))
await desk.connect()
await desk.move_to(1050)  # mm; returns when the desk has stopped
await desk.disconnect()
```

`move_up()`, `move_down()` and `move_to()` return when the motion ends, with the
reason: `"arrived"`, `"timeout"`, `"handset"` (a handset key was pressed),
`"disconnected"` or `"stopped"` (`stop()` or a newer command). Every motion ends with
stop frames and is capped at 30 s.

## Notes

- The desk accepts one BLE connection. While the vendor app is connected the desk does
  not advertise; while this library is connected the app cannot connect.
- Motion is hold-to-run: the library streams commands every 100 ms. If the connection
  drops mid-move the desk keeps going for about 1.2 s (~50 mm) before its watchdog stops it.
- A held handset key overrides everything once the BLE stream ends; stop frames do not
  stop a handset-driven move.
- The handset presets can be read but not written or recalled over BLE.
