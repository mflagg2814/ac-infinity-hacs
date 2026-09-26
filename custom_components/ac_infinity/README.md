# AC Infinity

Based on [hunterjm/ac-infinity-hacs](https://github.com/hunterjm/ac-infinity-hacs),
with [hunterjm/ac-infinity-ble](https://github.com/hunterjm/ac-infinity-ble) 1.0.0 vendored
in `vendor/`. Controls the A-0ECGN, a Controller 67 (type 1, protocol 0).

The tests are in [`tests/README.md`](tests/README.md).

# AI and Support

Provided without support. Maintained for my own purposes in rare intervals of spare time with AI.

## Vendored library

`vendor/ac\_infinity\_ble` is upstream commit `f6a86f8` (1.0.0, MIT, license alongside),
so the manifest has no requirements.

Our changes to it, all in `device.py` plus the new `clock.py`:

* **`set\_clock` and `read\_clock`**: see [Clock](#clock).
* **`disconnect`**: `stop` is final in upstream, but the coordinator disconnects after
every operation and must reconnect later.

Upstream's own suite passes against the vendored copy. To run it, check out upstream
and run `pytest -o asyncio\_mode=auto tests` with `PYTHONPATH` set to `vendor/`.

## Changes from upstream

* **Stale connections** (`\\\_\\\_init\\\_\\\_.py`): setup asks BlueZ to close a connection
it still holds for the device, within 5 seconds. Unload stops the controller within
10 seconds, so a wedged connect can't hold up a reload.
* **Clock sync** (`clock\\\_sync.py`, `clock\\\_schedule.py`): see [Clock](#clock).
* **60-second scan windows** (`coordinator.py`): see [Scan windows](#scan-windows).
* **Disconnect after every operation** (`ACInfinityDataUpdateCoordinator.async\\\_run`):
polls and fan commands are serialized and release the connection when done.
* **Commanded fan state**: the device reports a new level only in its next scan
response, so the fan entity shows the acknowledged level right away. Without this,
an integration saw the old speed and re-commanded every 30 seconds, keeping
the connection open indefinitely. The fan is unavailable until the device reports
its level.
* **Poll-based availability**: entities stay available for 20 minutes after a
successful poll, so missed scan windows don't flap them to unavailable. Sensors
need one sensor advertisement first.
* **Rare polls, outside scan windows** (`polling.py`, `scan\\\_window.py`): every poll
runs from a 30-second tick. There's one after startup, for the work mode and levels,
then one only when no sensor data or successful poll has arrived for 15 minutes, with
exponential backoff on failures. A due poll waits while any scanner that hears the
device is in an auto-mode active scan window.
* **Reading bands** (`sensor.py`): temperature changes only in steps of at least
0.1 °C and humidity 2%.

### Rules that keep readings flowing

Each rule has a test, so breaking it fails the suite.

|Rule|Test|
|-|-|
|Every BLE operation goes through `async\\\_run`, which disconnects|`test\\\_connection\\\_rules.py`, `test\\\_coordinator.py`|
|A clock sync never starts during an active scan window|`test\\\_coordinator.py`|
|A poll never starts during an active scan window|`test\\\_coordinator.py`, `test\\\_scan\\\_window.py`|
|Windows are requested every 60 seconds through the public API, and the coordinator's own registration adds none|`test\\\_coordinator.py`|
|The fan entity shows the commanded state right away|`test\\\_fan.py`|
|Sensors are unavailable until real sensor data arrives; the fan until the device reports its level|`test\\\_sensor.py`, `test\\\_fan.py`|

Fan commands don't wait for a window to close. Waiting up to 35 seconds would delay
consumers, while a command costs about a second of one window.

### Why polls are rare

The GATT poll response carries the work mode, levels, and auto-mode thresholds, but no
live readings. Each poll is a connection that can overlap a scan window, so polling
more often only loses readings. That was confirmed with debug logging.

