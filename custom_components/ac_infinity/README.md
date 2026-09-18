# AC Infinity

Based on [hunterjm/ac-infinity-hacs](https://github.com/hunterjm/ac-infinity-hacs),
using the [SethCalkins/ac-infinity-ble](https://github.com/SethCalkins/ac-infinity-ble)
library pinned in `manifest.json`. Controls the A-0ECGN controller.

The tests are in [`tests/README.md`](tests/README.md).

# AI and Support

Provided without support. Maintained for my own purposes in rare intervals of spare time with AI.

## Library pin

The requirement is written as `git+<url>@<commit>#ac-infinity-ble==<version>`.
Home Assistant checks only the version after `#` and reinstalls a
`name @ git+...` requirement on every boot. When changing the commit, also bump the
version, or HA keeps the old install.

## Where the state comes from

|Data|Source|
|-|-|
|Temperature, humidity|Manufacturer data (ID 2306) in scan responses, only|
|Fan level|Scan responses, and the commanded level after each command|
|Work mode, on/off levels|A GATT poll (`controller.update()`)|

### Scan windows

The device's plain advertisement carries only its name. Temperature and humidity come
back only in a scan response, which needs an active scan. Home Assistant's default
Bluetooth mode, `auto`, scans passively and turns active for a 10-second window per
requesting device. The coordinator requests a window every 60 seconds (HA's minimum)
through the public `bluetooth.async\_register\_callback` `scan\_interval` argument. Its own
advertisement registration is passive, so HA doesn't add its default 5-minute windows.
The adapter isn't switched to always-active, because every scannable device in range
would then spend battery answering scans.

While connected, the device answers scans with its name only. Any connection that
overlaps a window loses that window's readings, so every poll and command disconnects
as soon as it's done.

At setup the controller is seeded from the advertisement captured when the entry was
created, which can be months old. The temperature, humidity, and VPD sensors stay
`unavailable` until a sensor advertisement arrives, so that seed is never shown.

## Changes from upstream

* **`get\_services` shim** (`\_\_init\_\_.py`): current bleak removed
`BleakClient.get\_services()`, which the library still calls.
* **GATT cache clearing**: a `CharacteristicMissingError` clears the cached service
table so the next connection re-discovers it.
* **60-second scan windows** (`coordinator.py`): see [Scan windows](#scan-windows).
* **Disconnect after every operation** (`ACInfinityDataUpdateCoordinator.async\_run`):
polls and fan commands are serialized and release the connection when done.
* **Commanded fan state**: the device doesn't confirm a command until its next scan
response, so the fan entity shows the commanded level right away. Without this,
an integration saw the old speed and re-commanded every 30 seconds, keeping
the connection open indefinitely. The fan is unavailable until a scan response or
poll gives its real level, so the setup seed's level is never shown.
* **Poll-based availability**: entities stay available for 20 minutes after a
successful poll, so missed scan windows don't flap them to unavailable. Sensors
need one sensor advertisement first.
* **Rare polls, outside scan windows** (`polling.py`, `scan\_window.py`): every poll
runs from a 30-second tick. There's one after startup, for the work mode and levels,
then one only when no sensor data or successful poll has arrived for 15 minutes, with
exponential backoff on failures. A due poll waits while any scanner that hears the
device is in an auto-mode active scan window.

### Rules that keep readings flowing

Each rule has a test, so breaking it fails the suite.

|Rule|Test|
|-|-|
|Every BLE operation goes through `async\_run`, which disconnects|`test\_connection\_rules.py`, `test\_coordinator.py`|
|A poll never starts during an active scan window|`test\_coordinator.py`, `test\_scan\_window.py`|
|Windows are requested every 60 seconds through the public API, and the coordinator's own registration adds none|`test\_coordinator.py`|
|The fan entity shows the commanded state right away|`test\_fan.py`|
|Sensors are unavailable until real sensor data arrives; the fan until a scan response or poll gives its real level|`test\_sensor.py`, `test\_fan.py`|

Fan commands don't wait for a window to close. Waiting up to 35 seconds would delay
consumers, while a command costs about a second of one window.

### Why polls are rare

The GATT poll response carries the work mode, levels, and auto-mode thresholds, but no
live readings. Each poll is a connection that can overlap a scan window, so polling
more often only loses readings. That was confirmed with debug logging.

