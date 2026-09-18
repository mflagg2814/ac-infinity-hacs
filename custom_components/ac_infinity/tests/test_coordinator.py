"""Coordinator: scan window requests, sensor data, polls, and poll timing."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from ac_infinity_ble.const import MANUFACTURER_ID
from ac_infinity_ble.exceptions import CharacteristicMissingError
from bleak.exc import BleakError
import pytest

from homeassistant.components.bluetooth import BluetoothCallbackReplay, BluetoothScanningMode
from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import CoreState

from custom_components.ac_infinity import coordinator as coordinator_module
from custom_components.ac_infinity.coordinator import ACTIVE_SCAN_INTERVAL, carries_sensor_data
from custom_components.ac_infinity.polling import BACKOFF_BASE, STALL_AFTER

from .conftest import ADDRESS, advertise, service_info

SENSOR_PAYLOAD = bytes(19)


@pytest.fixture(autouse=True)
def window_open():
    """The active scan window state; closed unless a test opens it."""
    with patch.object(coordinator_module, "active_window_open", return_value=False) as mock:
        yield mock


def _polled(coordinator) -> None:
    coordinator._polls.mark_success(time.monotonic())


def _stall(coordinator) -> None:
    _polled(coordinator)
    coordinator._polls.last_heard = time.monotonic() - STALL_AFTER - 1


# ===========================================================================
# carries_sensor_data
# ===========================================================================
@pytest.mark.parametrize(
    ("manufacturer_data", "expected"),
    [
        ({}, False),
        ({MANUFACTURER_ID: bytes(18)}, False),
        ({MANUFACTURER_ID: SENSOR_PAYLOAD}, True),
        ({76: SENSOR_PAYLOAD}, False),
    ],
)
def test_carries_sensor_data(manufacturer_data, expected):
    assert carries_sensor_data(service_info(manufacturer_data)) is expected


# ===========================================================================
# Starting
# ===========================================================================
def test_start_requests_scan_windows_and_the_poll_tick(coordinator):
    """Sensor data only arrives during active scans, so ask for them often."""
    with (
        patch.object(PassiveBluetoothDataUpdateCoordinator, "async_start"),
        patch.object(coordinator_module, "async_track_time_interval") as track,
        patch.object(coordinator_module.bluetooth, "async_register_callback") as register,
    ):
        coordinator.async_start()
    (_hass, _callback, matcher, mode), kwargs = register.call_args
    assert matcher == {"address": ADDRESS, "connectable": True}
    assert mode is BluetoothScanningMode.ACTIVE
    assert kwargs == {
        "scan_interval": ACTIVE_SCAN_INTERVAL,
        "replay": BluetoothCallbackReplay.DISABLED,
    }
    assert track.call_args.args[1] == coordinator._async_poll_tick


def test_own_registration_adds_no_default_windows(coordinator):
    """A non-passive registration would add HA's default 5-minute windows too."""
    module = "homeassistant.components.bluetooth.update_coordinator"
    with (
        patch(f"{module}.async_register_callback") as register,
        patch(f"{module}.async_track_unavailable"),
    ):
        coordinator._async_start()
    assert register.call_args.args[3] is BluetoothScanningMode.PASSIVE


def test_stop_cancels_everything(coordinator):
    cancels = [MagicMock(), MagicMock(), MagicMock()]
    with (
        patch.object(PassiveBluetoothDataUpdateCoordinator, "async_start", return_value=cancels[0]),
        patch.object(coordinator_module.bluetooth, "async_register_callback", return_value=cancels[1]),
        patch.object(coordinator_module, "async_track_time_interval", return_value=cancels[2]),
    ):
        coordinator.async_start()()
    for cancel in cancels:
        cancel.assert_called_once()


# ===========================================================================
# Sensor data
# ===========================================================================
def test_no_sensor_data_before_advertisement(coordinator):
    assert coordinator.has_sensor_data is False


def test_name_only_advertisement_is_not_sensor_data(coordinator):
    advertise(coordinator, service_info())
    assert coordinator.has_sensor_data is False


def test_sensor_advertisement_is_sensor_data(coordinator):
    advertise(coordinator, service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}))
    assert coordinator.has_sensor_data is True


def test_sensor_data_flagged_before_controller_notifies(coordinator, controller):
    """Entities write their state from the controller's callbacks."""
    seen = []
    controller.set_ble_device_and_advertisement_data.side_effect = (
        lambda *_: seen.append(coordinator.has_sensor_data)
    )
    advertise(coordinator, service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}))
    assert seen == [True]


def test_no_fan_data_before_advertisement_or_poll(coordinator):
    advertise(coordinator, service_info())
    assert coordinator.has_fan_data is False


def test_sensor_advertisement_is_fan_data(coordinator):
    advertise(coordinator, service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}))
    assert coordinator.has_fan_data is True


async def test_successful_poll_is_fan_data(coordinator):
    await coordinator._async_update()
    assert coordinator.has_fan_data is True


async def test_failed_poll_is_not_fan_data(coordinator, controller):
    controller.update.side_effect = TimeoutError
    with pytest.raises(TimeoutError):
        await coordinator._async_update()
    assert coordinator.has_fan_data is False


async def test_listeners_see_fan_data_after_poll(coordinator):
    """Entities written during the poll still saw the seed, so they must hear again."""
    seen = []
    with patch.object(
        coordinator,
        "async_update_listeners",
        MagicMock(side_effect=lambda: seen.append(coordinator.has_fan_data)),
    ):
        await coordinator._async_update()
    assert seen == [True]


# ===========================================================================
# A single poll
# ===========================================================================
async def test_poll_success_marks_fresh(coordinator, controller):
    assert coordinator.poll_fresh is False
    await coordinator._async_update()
    controller.update.assert_awaited_once()
    assert coordinator.poll_fresh is True


async def test_poll_disconnects(coordinator, controller):
    """A held connection stops the sensor data a scan window would bring."""
    await coordinator._async_update()
    controller.stop.assert_awaited_once()


async def test_failed_disconnect_still_counts_as_success(coordinator, controller):
    controller.stop.side_effect = BleakError("gone")
    await coordinator._async_update()
    assert coordinator.poll_fresh is True


async def test_failed_poll_disconnects(coordinator, controller):
    controller.update.side_effect = TimeoutError
    with pytest.raises(TimeoutError):
        await coordinator._async_update()
    controller.stop.assert_awaited_once()
    assert coordinator.poll_fresh is False


async def test_disconnect_during_poll_counts_as_success(coordinator, controller):
    controller.update.side_effect = EOFError
    await coordinator._async_update()
    assert coordinator.poll_fresh is True


async def test_missing_characteristic_clears_cache_and_raises(coordinator, controller):
    controller.update.side_effect = CharacteristicMissingError("gone")
    with (
        patch.object(coordinator, "async_clear_service_cache", AsyncMock()) as clear,
        pytest.raises(CharacteristicMissingError),
    ):
        await coordinator._async_update()
    clear.assert_awaited_once()
    controller.stop.assert_awaited_once()
    assert coordinator.poll_fresh is False


def test_poll_fresh_expires(coordinator):
    coordinator._last_poll_ok = time.monotonic() - coordinator_module.POLL_AVAILABLE_WINDOW - 1
    assert coordinator.poll_fresh is False


# ===========================================================================
# Poll timing
# ===========================================================================
async def test_first_tick_polls_once(coordinator, controller):
    await coordinator._async_poll_tick()
    await coordinator._async_poll_tick()
    controller.update.assert_awaited_once()


async def test_no_poll_while_starting(hass, coordinator, controller):
    hass.set_state(CoreState.starting)
    await coordinator._async_poll_tick()
    controller.update.assert_not_awaited()


async def test_no_poll_during_active_scan_window(hass, coordinator, controller, window_open):
    window_open.return_value = True
    await coordinator._async_poll_tick()
    controller.update.assert_not_awaited()
    window_open.assert_called_with(hass, ADDRESS)

    window_open.return_value = False
    await coordinator._async_poll_tick()
    controller.update.assert_awaited_once()


async def test_deferred_poll_is_not_a_failure(coordinator, window_open):
    window_open.return_value = True
    await coordinator._async_poll_tick()
    assert coordinator._polls.failures == 0
    assert coordinator._polls.last_attempt is None


async def test_stalled_poll_waits_for_window_to_close(coordinator, controller, window_open):
    _stall(coordinator)
    window_open.return_value = True
    await coordinator._async_poll_tick()
    controller.update.assert_not_awaited()


async def test_window_not_checked_when_no_poll_is_due(coordinator, window_open):
    _polled(coordinator)
    await coordinator._async_poll_tick()
    window_open.assert_not_called()


async def test_no_poll_during_an_operation(coordinator, controller):
    release = asyncio.Event()
    operation = asyncio.ensure_future(coordinator.async_run(release.wait))
    await asyncio.sleep(0)
    await coordinator._async_poll_tick()
    release.set()
    await operation
    controller.update.assert_not_awaited()


async def test_no_poll_while_device_is_heard(coordinator, controller):
    _polled(coordinator)
    await coordinator._async_poll_tick()
    controller.update.assert_not_awaited()


async def test_sensor_advertisement_postpones_stall_poll(coordinator, controller):
    _stall(coordinator)
    advertise(coordinator, service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}))
    await coordinator._async_poll_tick()
    controller.update.assert_not_awaited()


async def test_name_only_advertisement_does_not_postpone_stall_poll(coordinator, controller):
    """While connected the device advertises only its name."""
    _stall(coordinator)
    advertise(coordinator, service_info())
    await coordinator._async_poll_tick()
    controller.update.assert_awaited_once()


async def test_polls_once_per_stall(coordinator, controller):
    _stall(coordinator)
    await coordinator._async_poll_tick()
    await coordinator._async_poll_tick()
    controller.update.assert_awaited_once()
    assert coordinator.poll_fresh is True


async def test_failed_poll_backs_off(coordinator, controller):
    _stall(coordinator)
    controller.update.side_effect = TimeoutError
    await coordinator._async_poll_tick()
    assert controller.update.await_count == 1

    await coordinator._async_poll_tick()
    assert controller.update.await_count == 1

    coordinator._polls.last_attempt = time.monotonic() - BACKOFF_BASE * 2 - 1
    await coordinator._async_poll_tick()
    assert controller.update.await_count == 2


async def test_listeners_reevaluated_while_stalled(coordinator, controller):
    """Availability can lapse during backoff, so listeners hear about every tick."""
    _stall(coordinator)
    coordinator._polls.failures = 1
    coordinator._polls.last_attempt = time.monotonic()
    with patch.object(coordinator, "async_update_listeners", MagicMock()) as listeners:
        await coordinator._async_poll_tick()
    controller.update.assert_not_awaited()
    listeners.assert_called_once()
