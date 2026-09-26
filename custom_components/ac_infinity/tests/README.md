# AC Infinity — test suite

Covers the whole integration: the bluetooth discovery and user config flow steps,
setup and unload, the coordinator's scan-window, poll and clock-sync timing rules,
sensor and fan availability, and our additions to the vendored library.

## Running

Setup and how to run these are shared with every other suite in this config — see
[`custom_components/tests/README.md`](../../tests/README.md). Coverage leaves out the
vendored library (`.coveragerc`).

## Layout

| File                        | Scope                                                                              |
| ---------------------------- | ----------------------------------------------------------------------------------- |
| `conftest.py`                | An unstarted coordinator with a mock controller, a real controller on the setup seed, frames captured from the A-0ECGN, `reply`/`ack` frame builders, `service_info`, `advertise` |
| `test_config_flow.py`        | Bluetooth discovery, the device list, and creating or re-showing the form on connection errors |
| `test_init.py`               | Setup seeding, stale-connection cleanup, unload and its time limit, the options-reload listener |
| `test_polling.py`            | `PollSchedule`: first poll, stall boundary, backoff growth and cap, reset on success (pure) |
| `test_clock_schedule.py`     | `ClockSchedule` and UTC offset change detection, including 30-minute DST (pure)   |
| `test_clock_sync.py`         | Setting the clock, reading it back, status, drift, logging, and scheduling the next sync |
| `test_scan_window.py`        | Active scan window detection, and the HA scanner attributes it relies on           |
| `test_connection_rules.py`   | Connecting controller methods run only inside operations passed to `async_run`   |
| `test_coordinator.py`        | Scan-window request, sensor-payload detection, polls and disconnects, poll and clock timing around scan windows, availability |
| `test_fan.py`                | Unavailable until the device reports its level; commands show the acknowledged state, send mode-only OFF, and release the connection |
| `test_sensor.py`             | Sensors stay unavailable on the setup seed until a sensor advertisement arrives; clock diagnostics, their translations and icons |
| `test_vendor.py`             | Clock encoding, `set_clock`/`read_clock`/`disconnect`, connecting and cache recovery, response assembly, and the captured settings reply |
| `test_manifest.py`           | No requirements, and nothing imports an installed copy of the library |

## Notes on the harness

- The coordinator, fan, sensor and vendor suites drive and inspect private state, so
  they suppress ruff's `SLF001` file-wide.
- phacc doesn't ship Home Assistant's Bluetooth injection helpers, so the coordinator
  is built directly and never started. Advertisements are delivered by calling
  `_async_handle_bluetooth_event` with the base class handler patched out.
- `test_coordinator.py` patches `coordinator.active_window_open` (closed by default),
  so poll timing tests control the scan window directly.
- Timestamps are set relative to the real `time.monotonic()` rather than patching
  it, since the event loop uses the same clock.
- The config flow and setup tests drive the real flow manager and `async_setup_entry`
  directly rather than the full config-entry lifecycle, to keep the BLE device's
  30-second ready timeout out of the suite; they patch the BLE lookup and the
  coordinator instead.

[phacc]: https://github.com/MatthewFlamm/pytest-homeassistant-custom-component
