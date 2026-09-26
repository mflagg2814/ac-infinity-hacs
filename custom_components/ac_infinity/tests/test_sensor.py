"""Sensor availability around the stale setup seed."""

# These tests drive and inspect the controller's and coordinator's internals; see README.md.
# ruff: noqa: SLF001

from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import time
from unittest.mock import MagicMock

import pytest

from custom_components.ac_infinity.clock_sync import ClockStatus
from custom_components.ac_infinity.sensor import (
    CLOCK_SENSORS,
    READINGS,
    ClockSensor,
    ReadingSensor,
    async_setup_entry,
    outside_band,
)
from custom_components.ac_infinity.vendor.ac_infinity_ble.const import MANUFACTURER_ID
from homeassistant.const import EntityCategory

from .conftest import ADDRESS, SENSOR_PAYLOAD, advertise, service_info

TEMPERATURE, HUMIDITY, VPD = READINGS
CLOCK_SYNC, CLOCK_DRIFT = CLOCK_SENSORS


@pytest.fixture
def controller(seeded_device):
    """The real controller on the setup seed, so the coordinator fixture is built on it."""
    return seeded_device


@pytest.fixture(params=READINGS, ids=lambda description: description.key)
def sensor(request, coordinator):
    """Each reading sensor."""
    return ReadingSensor(coordinator, request.param)


# ===========================================================================
# Which sensors exist
# ===========================================================================
async def _set_up_keys(coordinator) -> list[str]:
    add_entities = MagicMock()
    await async_setup_entry(coordinator.hass, MagicMock(runtime_data=coordinator), add_entities)
    return [entity.entity_description.key for entity in add_entities.call_args.args[0]]


async def test_a_controller_67_has_no_vpd_sensor(coordinator):
    """A Controller 67 has no VPD sensor."""
    assert await _set_up_keys(coordinator) == ["tmp", "hum", "clock_sync", "clock_drift"]


async def test_a_controller_69_has_a_vpd_sensor(coordinator, controller):
    """A Controller 69 has a VPD sensor."""
    controller._state = replace(controller.state, type=7, version=3)
    assert await _set_up_keys(coordinator) == ["tmp", "hum", "vpd", "clock_sync", "clock_drift"]


# ===========================================================================
# Availability
# ===========================================================================


def _present(coordinator) -> None:
    coordinator._available = True


def test_seed_is_unavailable_while_device_is_present(coordinator, sensor):
    """Seed is unavailable while device is present."""
    _present(coordinator)
    advertise(coordinator, service_info())
    assert sensor.available is False


def test_seed_is_unavailable_after_a_poll(coordinator, sensor):
    """Seed is unavailable after a poll."""
    coordinator._polls.mark_success(time.monotonic())
    assert sensor.available is False


def test_available_after_sensor_advertisement(coordinator, sensor):
    """Available after sensor advertisement."""
    _present(coordinator)
    advertise(coordinator, service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}))
    assert sensor.available is True


def test_poll_keeps_available_once_readings_arrived(coordinator, sensor):
    """Poll keeps available once readings arrived."""
    advertise(coordinator, service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}))
    coordinator._available = False
    coordinator._polls.mark_success(time.monotonic())
    assert sensor.available is True


def test_unavailable_when_absent_and_poll_is_stale(coordinator, sensor):
    """Unavailable when absent and poll is stale."""
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
    """Temperature moves in tenths."""
    sensor = ReadingSensor(coordinator, TEMPERATURE)
    assert _read(sensor, seeded_device, tmp=12.56) == 12.56
    assert _read(sensor, seeded_device, tmp=12.64) == 12.56
    assert _read(sensor, seeded_device, tmp=12.47) == 12.56
    assert _read(sensor, seeded_device, tmp=12.66) == 12.66


def test_humidity_moves_in_two_percent_steps(coordinator, seeded_device):
    """Humidity moves in two percent steps."""
    sensor = ReadingSensor(coordinator, HUMIDITY)
    assert _read(sensor, seeded_device, hum=80.0) == 80.0
    assert _read(sensor, seeded_device, hum=81.0) == 80.0
    assert _read(sensor, seeded_device, hum=78.0) == 78.0


def test_vpd_reports_every_reading(coordinator, seeded_device):
    """VPD reports every reading."""
    sensor = ReadingSensor(coordinator, VPD)
    assert _read(sensor, seeded_device, vpd=1.01) == 1.01
    assert _read(sensor, seeded_device, vpd=1.02) == 1.02


def test_missing_reading_resets_the_band(coordinator, seeded_device):
    """Missing reading resets the band."""
    sensor = ReadingSensor(coordinator, TEMPERATURE)
    _read(sensor, seeded_device, tmp=12.56)
    assert _read(sensor, seeded_device, tmp=None) is None
    assert _read(sensor, seeded_device, tmp=12.57) == 12.57


@pytest.mark.parametrize(
    ("kept", "reading", "moved"),
    [(None, 1.0, True), (1.0, None, True), (23.2, 23.3, True), (23.2, 23.29, False)],
)
def test_outside_band(kept, reading, moved):
    """Outside band."""
    assert outside_band(kept, reading, 0.1) is moved


# ===========================================================================
# Clock diagnostics
# ===========================================================================
INTEGRATION = Path(__file__).parents[1]


@pytest.fixture(params=CLOCK_SENSORS, ids=lambda description: description.key)
def clock_sensor(request, coordinator):
    """Each clock sync sensor."""
    return ClockSensor(coordinator, request.param)


def test_clock_sensors_are_diagnostics_on_the_device(clock_sensor):
    """Clock sensors are diagnostics on the device."""
    assert clock_sensor.entity_category is EntityCategory.DIAGNOSTIC
    assert clock_sensor.unique_id == f"{ADDRESS}_{clock_sensor.translation_key}"
    assert clock_sensor.device_info["model"] == "Controller 67"


def test_clock_sensors_stay_available_while_the_device_is_absent(coordinator, clock_sensor):
    """Clock sensors stay available while the device is absent."""
    coordinator._available = False
    assert clock_sensor.available is True


def test_clock_sensors_are_unknown_before_the_first_sync(clock_sensor):
    """Clock sensors are unknown before the first sync."""
    assert clock_sensor.native_value is None


def test_clock_sync_sensor_shows_status_and_last_attempt(coordinator):
    """Clock sync sensor shows status and last attempt."""
    sensor = ClockSensor(coordinator, CLOCK_SYNC)
    attempt = datetime(2026, 9, 23, 16, 0, tzinfo=UTC)
    coordinator.clock.status = ClockStatus.MISMATCH
    coordinator.clock.last_attempt = attempt
    sensor._async_update_attrs()
    assert sensor.native_value == "mismatch"
    assert sensor.extra_state_attributes == {"last_attempt": attempt}
    assert sensor.options == [status.value for status in ClockStatus]


def test_clock_drift_sensor_shows_drift(coordinator):
    """Clock drift sensor shows drift."""
    sensor = ClockSensor(coordinator, CLOCK_DRIFT)
    coordinator.clock.drift = -12.0
    sensor._async_update_attrs()
    assert sensor.native_value == -12.0


@pytest.mark.parametrize("file", ["strings.json", "translations/en.json"])
def test_every_status_is_translated(file):
    """Every status is translated."""
    states = json.loads((INTEGRATION / file).read_text())["entity"]["sensor"]["clock_sync"]
    assert set(states["state"]) == {status.value for status in ClockStatus}


def test_every_status_has_an_icon():
    """Every status has an icon."""
    icons = json.loads((INTEGRATION / "icons.json").read_text())["entity"]["sensor"]
    assert set(icons["clock_sync"]["state"]) == {status.value for status in ClockStatus}
    assert "default" in icons["clock_drift"]
