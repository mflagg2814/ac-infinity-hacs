# AC Infinity — test suite

Covers the whole integration: the bluetooth discovery and user config flow steps,
setup and unload, the coordinator's scan-window and poll-timing rules, sensor and fan
availability, and the pinned library.

## Running

Everything is built on [`pytest-homeassistant-custom-component`][phacc]: a real Home
Assistant instance plus the HA test toolkit.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r custom_components/ac_infinity/tests/requirements_test.txt
pytest custom_components/ac_infinity/tests
```

Run from the repository root — `pytest.ini`'s `pythonpath` is relative to it. For
coverage:

```bash
pytest custom_components/ac_infinity/tests \
  --cov=custom_components.ac_infinity --cov-report=term-missing
```

## Layout

| File                        | Scope                                                                              |
| ---------------------------- | ----------------------------------------------------------------------------------- |
| `conftest.py`                | An unstarted coordinator with a mock controller, a real controller on the setup seed, `service_info`, `advertise` |
| `test_config_flow.py`        | Bluetooth discovery, the device list, and creating or re-showing the form on connection errors |
| `test_init.py`               | Setup, unload, the options-reload listener, and the bleak `get_services` compat shim |
| `test_polling.py`            | `PollSchedule`: first poll, stall boundary, backoff growth and cap, reset on success (pure) |
| `test_scan_window.py`        | Active scan window detection, and the HA scanner attributes it relies on           |
| `test_connection_rules.py`   | No code awaits a connecting controller method outside `async_run`             |
| `test_coordinator.py`        | Scan-window request, sensor-payload detection, polls and disconnects, poll timing around scan windows, availability |
| `test_fan.py`                | Unavailable until real fan data; commands show the commanded state and release the connection |
| `test_sensor.py`             | Sensors stay unavailable on the setup seed until a sensor advertisement arrives     |
| `test_manifest.py`           | HA sees the pinned library as installed; the manifest commit matches the test environment |

## Notes on the harness

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
