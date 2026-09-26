"""Coordinator: scan window requests, sensor data, polls, clock syncs, and their timing."""

# These tests drive and inspect the controller's and coordinator's internals; see README.md.
# ruff: noqa: SLF001

import asyncio
from dataclasses import replace
from datetime import UTC, timedelta
import time
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.exc import BleakError
import pytest

from custom_components.ac_infinity import coordinator as coordinator_module
from custom_components.ac_infinity.backoff import BACKOFF_BASE
from custom_components.ac_infinity.coordinator import ACTIVE_SCAN_INTERVAL, carries_sensor_data
from custom_components.ac_infinity.polling import STALL_AFTER
from custom_components.ac_infinity.vendor.ac_infinity_ble import CallbackType
from custom_components.ac_infinity.vendor.ac_infinity_ble.const import MANUFACTURER_ID
from homeassistant.components.bluetooth import BluetoothCallbackReplay, BluetoothChange, BluetoothScanningMode
from homeassistant.components.bluetooth.passive_update_coordinator import PassiveBluetoothDataUpdateCoordinator
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import CoreState, Event
from homeassistant.util import dt as dt_util

from .conftest import ADDRESS, SEED_STATE, SENSOR_PAYLOAD, advertise, service_info


@pytest.fixture(autouse=True)
def window_open():
    """The active scan window state; closed unless a test opens it."""
    with patch.object(coordinator_module, "active_window_open", return_value=False) as mock:
        yield mock


@pytest.fixture(autouse=True)
def clock_synced(coordinator):
    """The clock was just synced, so ticks only poll; clock tests call mark_due."""
    coordinator.clock._schedule.mark_success(dt_util.utcnow(), UTC)


def _polled(coordinator) -> None:
    coordinator._polls.mark_success(time.monotonic())


async def _poll(coordinator) -> None:
    await coordinator._async_attempt_poll(time.monotonic())


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
        ({MANUFACTURER_ID: SENSOR_PAYLOAD[:17]}, False),
        ({MANUFACTURER_ID: SENSOR_PAYLOAD[:26]}, False),
        ({MANUFACTURER_ID: SENSOR_PAYLOAD}, True),
        ({76: SENSOR_PAYLOAD}, False),
    ],
)
def test_carries_sensor_data(manufacturer_data, expected):
    """Carries sensor data."""
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
    assert track.call_args.args[1] == coordinator._async_tick


def test_own_registration_adds_no_default_windows(coordinator):
    """A non-passive registration would add HA's default 5-minute windows too."""
    module = "homeassistant.components.bluetooth.update_coordinator"
    with (
        patch(f"{module}.async_register_callback") as register,
        patch(f"{module}.async_track_unavailable"),
    ):
        coordinator._async_start()
    assert register.call_args.args[3] is BluetoothScanningMode.PASSIVE


async def test_stop_cancels_everything(hass, coordinator, controller):
    """Stop cancels everything."""
    cancels = [MagicMock(), MagicMock(), MagicMock(), controller.register_callback.return_value]
    with (
        patch.object(PassiveBluetoothDataUpdateCoordinator, "async_start", return_value=cancels[0]),
        patch.object(coordinator_module.bluetooth, "async_register_callback", return_value=cancels[1]),
        patch.object(coordinator_module, "async_track_time_interval", return_value=cancels[2]),
    ):
        coordinator.async_start()()
    for cancel in cancels:
        cancel.assert_called_once()
    hass.bus.async_fire(EVENT_CORE_CONFIG_UPDATE, {"time_zone": "America/Chicago"})
    await hass.async_block_till_done()
    assert coordinator.clock.is_due(dt_util.utcnow()) is False


# ===========================================================================
# Sensor data
# ===========================================================================
def test_no_sensor_data_before_advertisement(coordinator):
    """No sensor data before advertisement."""
    assert coordinator.has_sensor_data is False


def test_name_only_advertisement_is_not_sensor_data(coordinator):
    """Name only advertisement is not sensor data."""
    advertise(coordinator, service_info())
    assert coordinator.has_sensor_data is False


def test_sensor_advertisement_is_sensor_data(coordinator):
    """Sensor advertisement is sensor data."""
    advertise(coordinator, service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}))
    assert coordinator.has_sensor_data is True


def test_seed_has_no_fan_data(coordinator):
    """Seed has no fan data."""
    assert coordinator.has_fan_data is False


def test_a_reported_fan_level_is_fan_data(coordinator, controller):
    """A reported fan level is fan data."""
    controller.state = replace(SEED_STATE, fan=0)
    assert coordinator.has_fan_data is True


def test_unparseable_sensor_advertisement_is_ignored(coordinator, controller):
    """Unparseable sensor advertisement is ignored."""
    controller.set_ble_device_and_advertisement_data.side_effect = ValueError("bad")
    advertise(coordinator, service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}))
    assert coordinator.has_sensor_data is True


def test_name_only_advertisement_is_not_parsed(coordinator, controller):
    """Name only advertisement is not parsed."""
    advertise(coordinator, service_info())
    controller.set_ble_device_and_advertisement_data.assert_not_called()


def test_any_advertisement_makes_the_device_ready(coordinator):
    """Any advertisement makes the device ready."""
    advertise(coordinator, service_info())
    assert coordinator._ready_event.is_set()


async def test_listeners_see_reachability_after_poll(coordinator):
    """Listeners hear once, after the poll made the device reachable."""
    seen = []
    with patch.object(
        coordinator,
        "async_update_listeners",
        MagicMock(side_effect=lambda: seen.append(coordinator.reachable)),
    ):
        await _poll(coordinator)
    assert seen == [True]


# ===========================================================================
# Controller callbacks
# ===========================================================================
def _controller_callback(coordinator, controller):
    with (
        patch.object(PassiveBluetoothDataUpdateCoordinator, "async_start"),
        patch.object(coordinator_module.bluetooth, "async_register_callback"),
        patch.object(coordinator_module, "async_track_time_interval"),
    ):
        coordinator.async_start()
    return controller.register_callback.call_args.args[0]


def test_telemetry_updates_listeners(coordinator, controller):
    """Telemetry arrives only while connected, so nothing else would pass it on."""
    handle = _controller_callback(coordinator, controller)
    with patch.object(coordinator, "async_update_listeners") as listeners:
        handle(SEED_STATE, CallbackType.NOTIFICATION)
    listeners.assert_called_once()


@pytest.mark.parametrize("kind", [CallbackType.ADVERTISEMENT, CallbackType.UPDATE_RESPONSE])
def test_callbacks_already_covered_leave_listeners_alone(coordinator, controller, kind):
    """The advertisement handler, polls and commands each update listeners once themselves."""
    handle = _controller_callback(coordinator, controller)
    with patch.object(coordinator, "async_update_listeners") as listeners:
        handle(SEED_STATE, kind)
    listeners.assert_not_called()


def test_one_listener_update_per_sensor_advertisement(coordinator):
    """One listener update per sensor advertisement."""
    with patch.object(coordinator, "async_update_listeners") as listeners:
        coordinator._async_handle_bluetooth_event(
            service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}), BluetoothChange.ADVERTISEMENT
        )
    listeners.assert_called_once()


# ===========================================================================
# A single poll
# ===========================================================================
async def test_poll_success_makes_the_device_reachable(coordinator, controller):
    """Poll success makes the device reachable."""
    assert coordinator.reachable is False
    await _poll(coordinator)
    controller.update.assert_awaited_once()
    assert coordinator.reachable is True


async def test_poll_disconnects(coordinator, controller):
    """A held connection stops the sensor data a scan window would bring."""
    await _poll(coordinator)
    controller.disconnect.assert_awaited_once()


async def test_failed_disconnect_still_counts_as_success(coordinator, controller):
    """Failed disconnect still counts as success."""
    controller.disconnect.side_effect = BleakError("gone")
    await _poll(coordinator)
    assert coordinator.reachable is True


async def test_failed_poll_disconnects(coordinator, controller):
    """Failed poll disconnects."""
    controller.update.side_effect = TimeoutError
    await _poll(coordinator)
    controller.disconnect.assert_awaited_once()
    assert coordinator.reachable is False


async def test_a_failed_operation_still_raises_after_disconnecting(coordinator, controller):
    """A failed operation still raises after disconnecting."""
    with pytest.raises(BleakError):
        await coordinator.async_run(AsyncMock(side_effect=BleakError("gone")))
    controller.disconnect.assert_awaited_once()


# ===========================================================================
# Poll timing
# ===========================================================================
async def test_first_tick_polls_once(coordinator, controller):
    """First tick polls once."""
    await coordinator._async_tick()
    await coordinator._async_tick()
    controller.update.assert_awaited_once()


async def test_no_poll_while_starting(hass, coordinator, controller):
    """No poll while starting."""
    hass.set_state(CoreState.starting)
    await coordinator._async_tick()
    controller.update.assert_not_awaited()


async def test_no_poll_during_active_scan_window(hass, coordinator, controller, window_open):
    """No poll during active scan window."""
    window_open.return_value = True
    await coordinator._async_tick()
    controller.update.assert_not_awaited()
    window_open.assert_called_with(hass, ADDRESS)

    window_open.return_value = False
    await coordinator._async_tick()
    controller.update.assert_awaited_once()


async def test_deferred_poll_is_not_a_failure(coordinator, window_open):
    """Deferred poll is not a failure."""
    window_open.return_value = True
    await coordinator._async_tick()
    assert coordinator._polls.failures == 0
    assert coordinator._polls.last_attempt is None


async def test_stalled_poll_waits_for_window_to_close(coordinator, controller, window_open):
    """Stalled poll waits for window to close."""
    _stall(coordinator)
    window_open.return_value = True
    await coordinator._async_tick()
    controller.update.assert_not_awaited()


async def test_window_not_checked_when_no_poll_is_due(coordinator, window_open):
    """Window not checked when no poll is due."""
    _polled(coordinator)
    await coordinator._async_tick()
    window_open.assert_not_called()


async def test_no_poll_during_an_operation(coordinator, controller):
    """No poll during an operation."""
    release = asyncio.Event()
    operation = asyncio.ensure_future(coordinator.async_run(release.wait))
    await asyncio.sleep(0)
    await coordinator._async_tick()
    release.set()
    await operation
    controller.update.assert_not_awaited()


async def test_no_poll_while_device_is_heard(coordinator, controller):
    """No poll while device is heard."""
    _polled(coordinator)
    await coordinator._async_tick()
    controller.update.assert_not_awaited()


async def test_sensor_advertisement_postpones_stall_poll(coordinator, controller):
    """Sensor advertisement postpones stall poll."""
    _stall(coordinator)
    advertise(coordinator, service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}))
    await coordinator._async_tick()
    controller.update.assert_not_awaited()


async def test_name_only_advertisement_does_not_postpone_stall_poll(coordinator, controller):
    """While connected the device advertises only its name."""
    _stall(coordinator)
    advertise(coordinator, service_info())
    await coordinator._async_tick()
    controller.update.assert_awaited_once()


async def test_polls_once_per_stall(coordinator, controller):
    """Polls once per stall."""
    _stall(coordinator)
    await coordinator._async_tick()
    await coordinator._async_tick()
    controller.update.assert_awaited_once()
    assert coordinator.reachable is True


async def test_failed_poll_backs_off(coordinator, controller):
    """Failed poll backs off."""
    _stall(coordinator)
    controller.update.side_effect = TimeoutError
    await coordinator._async_tick()
    assert controller.update.await_count == 1

    await coordinator._async_tick()
    assert controller.update.await_count == 1

    coordinator._polls.last_attempt = time.monotonic() - BACKOFF_BASE * 2 - 1
    await coordinator._async_tick()
    assert controller.update.await_count == 2


async def test_listeners_reevaluated_while_stalled(coordinator, controller):
    """Availability can lapse during backoff, so listeners hear about every tick."""
    _stall(coordinator)
    coordinator._polls.failures = 1
    coordinator._polls.last_attempt = time.monotonic()
    with patch.object(coordinator, "async_update_listeners", MagicMock()) as listeners:
        await coordinator._async_tick()
    controller.update.assert_not_awaited()
    listeners.assert_called_once()


# ===========================================================================
# Clock sync timing
# ===========================================================================
async def test_due_clock_syncs_on_the_tick(coordinator, controller):
    """Due clock syncs on the tick."""
    _polled(coordinator)
    coordinator.clock.mark_due()
    await coordinator._async_tick()
    controller.set_clock.assert_awaited_once()
    controller.disconnect.assert_awaited_once()
    assert coordinator.clock.is_due(dt_util.utcnow()) is False


async def test_listeners_hear_each_clock_sync(coordinator):
    """Listeners hear each clock sync."""
    _polled(coordinator)
    coordinator.clock.mark_due()
    with patch.object(coordinator, "async_update_listeners", MagicMock()) as listeners:
        await coordinator._async_tick()
    listeners.assert_called_once()


async def test_first_tick_polls_and_syncs_the_clock(coordinator, controller):
    """First tick polls and syncs the clock."""
    coordinator.clock.mark_due()
    await coordinator._async_tick()
    controller.update.assert_awaited_once()
    controller.set_clock.assert_awaited_once()


async def test_no_clock_sync_while_starting(hass, coordinator, controller):
    """No clock sync while starting."""
    hass.set_state(CoreState.starting)
    coordinator.clock.mark_due()
    await coordinator._async_tick()
    controller.set_clock.assert_not_awaited()


async def test_clock_sync_waits_for_window_to_close(coordinator, controller, window_open):
    """Clock sync waits for window to close."""
    _polled(coordinator)
    coordinator.clock.mark_due()
    window_open.return_value = True
    await coordinator._async_tick()
    controller.set_clock.assert_not_awaited()

    window_open.return_value = False
    await coordinator._async_tick()
    controller.set_clock.assert_awaited_once()


async def test_failed_clock_sync_retries_after_backoff(coordinator, controller):
    """Failed clock sync retries after backoff."""
    _polled(coordinator)
    coordinator.clock.mark_due()
    controller.set_clock.side_effect = TimeoutError
    await coordinator._async_tick()
    await coordinator._async_tick()
    assert controller.set_clock.await_count == 1
    assert coordinator.clock.is_due(dt_util.utcnow() + timedelta(seconds=BACKOFF_BASE * 2))


async def test_time_zone_change_makes_the_clock_due(hass, coordinator):
    """Time zone change makes the clock due."""
    with (
        patch.object(PassiveBluetoothDataUpdateCoordinator, "async_start"),
        patch.object(coordinator_module.bluetooth, "async_register_callback"),
        patch.object(coordinator_module, "async_track_time_interval"),
    ):
        coordinator.async_start()
    hass.bus.async_fire(EVENT_CORE_CONFIG_UPDATE, {"time_zone": "America/Chicago"})
    await hass.async_block_till_done()
    assert coordinator.clock.is_due(dt_util.utcnow()) is True


def test_other_config_changes_leave_the_clock_alone(coordinator):
    """Other config changes leave the clock alone."""
    coordinator._async_core_config_updated(Event(EVENT_CORE_CONFIG_UPDATE, {"elevation": 286}))
    assert coordinator.clock.is_due(dt_util.utcnow()) is False


# ===========================================================================
# Startup
# ===========================================================================
async def test_ready_once_the_device_advertises(coordinator):
    """Ready once the device advertises."""
    advertise(coordinator, service_info())
    assert await coordinator.async_wait_ready() is True


async def test_not_ready_without_an_advertisement(coordinator):
    """Not ready without an advertisement."""
    with patch.object(coordinator_module, "DEVICE_STARTUP_TIMEOUT", 0.01):
        assert await coordinator.async_wait_ready() is False
