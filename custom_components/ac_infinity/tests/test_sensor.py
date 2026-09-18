"""Sensor availability around the stale setup seed."""
from __future__ import annotations

import time

from ac_infinity_ble.const import MANUFACTURER_ID
import pytest

from custom_components.ac_infinity.sensor import (
    HumiditySensor,
    TemperatureSensor,
    VpdSensor,
)

from .conftest import advertise, service_info

@pytest.fixture(params=[TemperatureSensor, HumiditySensor, VpdSensor])
def sensor(request, coordinator, seeded_device):
    return request.param(coordinator, seeded_device, "A-0ECGN")


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
    advertise(coordinator, service_info({MANUFACTURER_ID: bytes(19)}))
    assert sensor.available is True


def test_poll_keeps_available_once_readings_arrived(coordinator, sensor):
    advertise(coordinator, service_info({MANUFACTURER_ID: bytes(19)}))
    coordinator._available = False
    coordinator._last_poll_ok = time.monotonic()
    assert sensor.available is True


def test_unavailable_when_absent_and_poll_is_stale(coordinator, sensor):
    advertise(coordinator, service_info({MANUFACTURER_ID: bytes(19)}))
    coordinator._available = False
    assert sensor.available is False
