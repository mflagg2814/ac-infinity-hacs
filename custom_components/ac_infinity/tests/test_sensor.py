"""Sensor availability around the stale setup seed."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import time

import pytest

from homeassistant.const import EntityCategory

from custom_components.ac_infinity.clock_sync import ClockStatus
from custom_components.ac_infinity.sensor import (
    ClockDriftSensor,
    ClockSyncSensor,
    HumiditySensor,
    TemperatureSensor,
    VpdSensor,
    outside_band,
)

from custom_components.ac_infinity.vendor.ac_infinity_ble.const import MANUFACTURER_ID

from .conftest import ADDRESS, SENSOR_PAYLOAD, advertise, service_info

@pytest.fixture(params=[TemperatureSensor, HumiditySensor, VpdSensor])
def sensor(request, coordinator, seeded_device):
    return request.param(coordinator, seeded_device)


def _present(coordinator) -> None:
    coordinator._available = True


def test_seed_is_unavailable_while_device_is_present(coordinator, sensor):
    _present(coordinator)
    advertise(coordinator, service_info())
    assert sensor.available is False


def test_seed_is_unavailable_after_a_poll(coordinator, sensor):
    coordinator._last_poll_ok = time.monotonic()
    assert sensor.available is False


def test_available_after_sensor_advertisement(coordinator, sensor):
    _present(coordinator)
    advertise(coordinator, service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}))
    assert sensor.available is True


def test_poll_keeps_available_once_readings_arrived(coordinator, sensor):
    advertise(coordinator, service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}))
    coordinator._available = False
    coordinator._last_poll_ok = time.monotonic()
    assert sensor.available is True


def test_unavailable_when_absent_and_poll_is_stale(coordinator, sensor):
    advertise(coordinator, service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}))
    coordinator._available = False
    assert sensor.available is False


# ===========================================================================
# Bands
# ===========================================================================
def _read(sensor, device, **state) -> float | None:
    device._state = replace(device._state, **state)
    sensor._async_update_attrs()
    return sensor.native_value


def test_temperature_moves_in_tenths(coordinator, seeded_device):
    sensor = TemperatureSensor(coordinator, seeded_device)
    assert _read(sensor, seeded_device, tmp=12.56) == 12.56
    assert _read(sensor, seeded_device, tmp=12.64) == 12.56
    assert _read(sensor, seeded_device, tmp=12.47) == 12.56
    assert _read(sensor, seeded_device, tmp=12.66) == 12.66


def test_humidity_moves_in_two_percent_steps(coordinator, seeded_device):
    sensor = HumiditySensor(coordinator, seeded_device)
    assert _read(sensor, seeded_device, hum=80.0) == 80.0
    assert _read(sensor, seeded_device, hum=81.0) == 80.0
    assert _read(sensor, seeded_device, hum=78.0) == 78.0


def test_vpd_reports_every_reading(coordinator, seeded_device):
    sensor = VpdSensor(coordinator, seeded_device)
    assert _read(sensor, seeded_device, vpd=1.01) == 1.01
    assert _read(sensor, seeded_device, vpd=1.02) == 1.02


def test_missing_reading_resets_the_band(coordinator, seeded_device):
    sensor = TemperatureSensor(coordinator, seeded_device)
    _read(sensor, seeded_device, tmp=12.56)
    assert _read(sensor, seeded_device, tmp=None) is None
    assert _read(sensor, seeded_device, tmp=12.57) == 12.57


@pytest.mark.parametrize(
    ("kept", "reading", "moved"),
    [(None, 1.0, True), (1.0, None, True), (23.2, 23.3, True), (23.2, 23.29, False)],
)
def test_outside_band(kept, reading, moved):
    assert outside_band(kept, reading, 0.1) is moved


# ===========================================================================
# Clock diagnostics
# ===========================================================================
INTEGRATION = Path(__file__).parents[1]


@pytest.fixture(params=[ClockSyncSensor, ClockDriftSensor])
def clock_sensor(request, coordinator, seeded_device):
    return request.param(coordinator, seeded_device)


def test_clock_sensors_are_diagnostics_on_the_device(clock_sensor):
    assert clock_sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert clock_sensor.unique_id == f"{ADDRESS}_{clock_sensor.translation_key}"
    assert clock_sensor.device_info["model"] == "Controller 67"


def test_clock_sensors_stay_available_while_the_device_is_absent(coordinator, clock_sensor):
    coordinator._available = False
    assert clock_sensor.available is True


def test_clock_sensors_are_unknown_before_the_first_sync(clock_sensor):
    assert clock_sensor.native_value is None


def test_clock_sync_sensor_shows_status_and_last_attempt(coordinator, seeded_device):
    sensor = ClockSyncSensor(coordinator, seeded_device)
    attempt = datetime(2026, 9, 23, 16, 0, tzinfo=UTC)
    coordinator.clock.status = ClockStatus.MISMATCH
    coordinator.clock.last_attempt = attempt
    assert sensor.native_value == "mismatch"
    assert sensor.extra_state_attributes == {"last_attempt": attempt}
    assert sensor.options == [status.value for status in ClockStatus]


def test_clock_drift_sensor_shows_drift(coordinator, seeded_device):
    sensor = ClockDriftSensor(coordinator, seeded_device)
    coordinator.clock.drift = -12.0
    assert sensor.native_value == -12.0


@pytest.mark.parametrize("file", ["strings.json", "translations/en.json"])
def test_every_status_is_translated(file):
    states = json.loads((INTEGRATION / file).read_text())["entity"]["sensor"]["clock_sync"]
    assert set(states["state"]) == {status.value for status in ClockStatus}


def test_every_status_has_an_icon():
    icons = json.loads((INTEGRATION / "icons.json").read_text())["entity"]["sensor"]
    assert set(icons["clock_sync"]["state"]) == {status.value for status in ClockStatus}
    assert "default" in icons["clock_drift"]
