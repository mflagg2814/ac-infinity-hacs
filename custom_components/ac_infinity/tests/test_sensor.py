"""Sensor availability around the stale setup seed."""
from __future__ import annotations

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
