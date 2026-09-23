"""Fan availability on real data, commanded state, OFF packets, and connection release."""
from __future__ import annotations

from dataclasses import replace
import time
from unittest.mock import AsyncMock, MagicMock

from bleak.exc import BleakError
import pytest

from custom_components.ac_infinity.fan import ACInfinityFan
from custom_components.ac_infinity.vendor.ac_infinity_ble.const import MANUFACTURER_ID

from .conftest import MODEL_REPLY, SEED_STATE, SENSOR_PAYLOAD, ack, advertise, service_info


@pytest.fixture
def controller(seeded_device):
    """The seeded controller after its startup poll, with the BLE link replaced."""
    seeded_device._state = replace(SEED_STATE, level_on=5, level_off=0)
    seeded_device._send_command = AsyncMock(side_effect=ack)
    seeded_device.disconnect = AsyncMock()
    return seeded_device


@pytest.fixture
def fan(coordinator, controller):
    entity = ACInfinityFan(coordinator, controller)
    entity.async_write_ha_state = MagicMock()
    return entity


def _sent_payload(controller) -> bytes:
    """The parameters of the last command sent."""
    return controller._send_command.await_args.args[0][10:-2]


@pytest.mark.parametrize(
    ("percentage", "expected"), [(100, 100), (80, 80), (75, 80), (5, 10), (0, 0)]
)
async def test_set_percentage_shows_commanded_speed(fan, percentage, expected):
    await fan.async_set_percentage(percentage)
    assert fan.percentage == expected
    assert fan.is_on is (expected > 0)
    fan.async_write_ha_state.assert_called()


async def test_turn_on_with_percentage(fan, controller):
    await fan.async_turn_on(percentage=30)
    assert (fan.is_on, fan.percentage) == (True, 30)
    assert _sent_payload(controller) == bytes.fromhex("100102 120103")


async def test_turn_on_without_percentage_keeps_the_saved_level(fan, controller):
    await fan.async_turn_on()
    assert (fan.is_on, fan.percentage) == (True, 50)
    assert _sent_payload(controller) == bytes.fromhex("100102")


async def test_turn_off_sends_mode_only(fan, controller):
    """The saved ON and OFF levels survive; the 1.5.8 app turns a Controller 67 off the same way."""
    await fan.async_turn_on(percentage=30)
    await fan.async_turn_off()
    assert fan.is_on is False
    assert _sent_payload(controller) == bytes.fromhex("100101")


async def test_command_disconnects(fan, controller):
    await fan.async_set_percentage(50)
    controller.disconnect.assert_awaited_once()


async def test_failed_command_writes_nothing(fan, controller):
    controller._send_command.side_effect = BleakError("out of range")
    with pytest.raises(BleakError):
        await fan.async_set_percentage(50)
    fan.async_write_ha_state.assert_not_called()
    controller.disconnect.assert_awaited_once()


# ===========================================================================
# Availability
# ===========================================================================
def test_seed_is_unavailable_while_device_is_present(coordinator, fan):
    coordinator._available = True
    advertise(coordinator, service_info())
    assert fan.available is False


def test_available_after_sensor_advertisement(coordinator, fan):
    coordinator._available = True
    advertise(coordinator, service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}))
    assert fan.available is True


async def test_poll_alone_leaves_the_level_unknown(coordinator, controller, fan):
    """Saved levels aren't the actual output; only an advertisement or a command reports it."""
    controller._send_command.side_effect = lambda _request: MODEL_REPLY
    await coordinator._async_update()
    assert (controller.is_on, controller.state.level_on) == (True, 8)
    assert fan.available is False


def test_unavailable_when_unreachable_after_real_data(coordinator, fan):
    advertise(coordinator, service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}))
    coordinator._available = False
    coordinator._last_poll_ok = time.monotonic() - 10**6
    assert fan.available is False
