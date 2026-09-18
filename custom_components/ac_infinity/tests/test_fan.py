"""Fan availability on real data, commanded state, and connection release."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from ac_infinity_ble.const import MANUFACTURER_ID
from bleak.exc import BleakError
import pytest

from custom_components.ac_infinity.fan import ACInfinityFan

from .conftest import advertise, service_info


@pytest.fixture
def controller(seeded_device):
    """The seeded controller, with the BLE link replaced."""
    seeded_device._send_command = AsyncMock(return_value=b"")
    seeded_device.stop = AsyncMock()
    return seeded_device


@pytest.fixture
def fan(coordinator, controller):
    entity = ACInfinityFan(coordinator, controller, "A-0ECGN")
    entity.async_write_ha_state = MagicMock()
    return entity


@pytest.mark.parametrize(
    ("percentage", "expected"), [(100, 100), (80, 80), (75, 80), (5, 10), (0, 0)]
)
async def test_set_percentage_shows_commanded_speed(fan, percentage, expected):
    await fan.async_set_percentage(percentage)
    assert fan.percentage == expected
    assert fan.is_on is (expected > 0)
    fan.async_write_ha_state.assert_called_once()


async def test_turn_on_with_percentage(fan):
    await fan.async_turn_on(percentage=30)
    assert (fan.is_on, fan.percentage) == (True, 30)
    fan.async_write_ha_state.assert_called_once()


async def test_turn_off(fan):
    await fan.async_turn_on(percentage=30)
    await fan.async_turn_off()
    assert fan.is_on is False
    assert fan.async_write_ha_state.call_count == 2


async def test_command_disconnects(fan, controller):
    await fan.async_set_percentage(50)
    controller.stop.assert_awaited_once()


async def test_disconnect_after_reply_still_shows_state(fan, controller):
    controller._send_command.side_effect = EOFError
    await fan.async_set_percentage(50)
    assert fan.percentage == 50
    fan.async_write_ha_state.assert_called_once()


async def test_failed_command_writes_nothing(fan, controller):
    controller._send_command.side_effect = BleakError("out of range")
    with pytest.raises(BleakError):
        await fan.async_set_percentage(50)
    fan.async_write_ha_state.assert_not_called()
    controller.stop.assert_awaited_once()


# ===========================================================================
# Availability
# ===========================================================================
def test_seed_is_unavailable_while_device_is_present(coordinator, fan):
    coordinator._available = True
    advertise(coordinator, service_info())
    assert fan.available is False


def test_available_after_sensor_advertisement(coordinator, fan):
    coordinator._available = True
    advertise(coordinator, service_info({MANUFACTURER_ID: bytes(19)}))
    assert fan.available is True


async def test_available_after_successful_poll(coordinator, fan):
    """A poll reports the real level even before any scan response."""
    await coordinator._async_update()
    assert fan.available is True


async def test_unavailable_when_unreachable_after_real_data(coordinator, fan):
    await coordinator._async_update()
    coordinator._last_poll_ok = time.monotonic() - 10**6
    assert fan.available is False
