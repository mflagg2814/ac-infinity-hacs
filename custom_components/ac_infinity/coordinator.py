"""AC Infinity Coordinator."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from datetime import datetime, timedelta
import logging
import time

from bleak.backends.device import BLEDevice

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothDataUpdateCoordinator,
)
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE
from homeassistant.core import CALLBACK_TYPE, CoreState, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .clock_sync import ClockSync
from .polling import STALL_AFTER, PollSchedule
from .scan_window import active_window_open
from .vendor.ac_infinity_ble import ACInfinityController
from .vendor.ac_infinity_ble.const import MANUFACTURER_ID

DEVICE_STARTUP_TIMEOUT = 30

# Sensor data only arrives during HA's active scan windows, requested this often.
ACTIVE_SCAN_INTERVAL = 60.0
# Entities stay available this long after a successful poll, without advertisements.
POLL_AVAILABLE_WINDOW = STALL_AFTER + 300
TICK_INTERVAL = timedelta(seconds=30)
# The library reads climate and fan level only from the extended payload
SENSOR_PAYLOAD_LENGTH = 27

_LOGGER = logging.getLogger(__name__)


@callback
def _ignore_advertisement(
    service_info: bluetooth.BluetoothServiceInfoBleak, change: bluetooth.BluetoothChange
) -> None:
    """The coordinator's own callback handles advertisements."""


def carries_sensor_data(service_info: bluetooth.BluetoothServiceInfoBleak) -> bool:
    """Whether an advertisement includes the temperature and humidity payload."""
    payload = service_info.manufacturer_data.get(MANUFACTURER_ID)
    return payload is not None and len(payload) == SENSOR_PAYLOAD_LENGTH


class ACInfinityDataUpdateCoordinator(PassiveBluetoothDataUpdateCoordinator):
    """Scan responses carry the readings; polls fill in the rest; also syncs the clock. See README.md."""

    def __init__(
        self,
        hass: HomeAssistant,
        logger: logging.Logger,
        ble_device: BLEDevice,
        controller: ACInfinityController,
    ) -> None:
        # Passive, so HA adds no default 5-minute windows; async_start requests 60s ones.
        super().__init__(
            hass,
            logger,
            ble_device.address,
            bluetooth.BluetoothScanningMode.PASSIVE,
            connectable=True,
        )
        self.ble_device = ble_device
        self.controller = controller
        self._ready_event = asyncio.Event()
        self._was_unavailable = True
        self._operation_lock = asyncio.Lock()
        self._last_poll_ok: float | None = None
        self._sensor_data_received = False
        self._polls = PollSchedule(last_heard=time.monotonic())
        self.clock = ClockSync(controller)

    @property
    def has_sensor_data(self) -> bool:
        """Return True once a sensor advertisement has replaced the stale setup seed."""
        return self._sensor_data_received

    @property
    def has_fan_data(self) -> bool:
        """Return True once the device has reported its fan level; the seed has none."""
        return self.controller.state.fan is not None

    @property
    def reachable(self) -> bool:
        """Return True while the device is heard or a poll succeeded recently."""
        return self.available or self.poll_fresh

    @property
    def poll_fresh(self) -> bool:
        """Return True if a poll succeeded recently enough to trust the device."""
        return (
            self._last_poll_ok is not None
            and time.monotonic() - self._last_poll_ok < POLL_AVAILABLE_WINDOW
        )

    @callback
    def async_start(self) -> CALLBACK_TYPE:
        """Start advertisement tracking, active scan windows, and the tick."""
        cancels = (
            super().async_start(),
            self._async_request_scan_windows(),
            async_track_time_interval(self.hass, self._async_tick, TICK_INTERVAL),
            self.hass.bus.async_listen(
                EVENT_CORE_CONFIG_UPDATE, self._async_core_config_updated
            ),
        )

        @callback
        def _cancel() -> None:
            for cancel in cancels:
                cancel()

        return _cancel

    @callback
    def _async_request_scan_windows(self) -> CALLBACK_TYPE:
        """Ask HA's auto scanning mode for an active scan window every ACTIVE_SCAN_INTERVAL."""
        return bluetooth.async_register_callback(
            self.hass,
            _ignore_advertisement,
            bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=True),
            bluetooth.BluetoothScanningMode.ACTIVE,
            scan_interval=ACTIVE_SCAN_INTERVAL,
            replay=bluetooth.BluetoothCallbackReplay.DISABLED,
        )

    @callback
    def _async_core_config_updated(self, event: Event) -> None:
        """The controller keeps local time, so a new time zone needs a sync."""
        if "time_zone" in event.data:
            self.clock.mark_due()

    async def async_run(self, operation: Callable[[], Awaitable[None]]) -> None:
        """Run one controller operation, then disconnect; a connected device sends no sensor data."""
        async with self._operation_lock:
            try:
                await operation()
            finally:
                await self._async_disconnect()

    async def _async_disconnect(self) -> None:
        try:
            await self.controller.disconnect()
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.debug("%s: Disconnect failed: %s", self.ble_device.name, ex)

    async def _async_update(self) -> None:
        """Poll the device once for the work mode and levels."""
        await self.async_run(self.controller.update)
        now = time.monotonic()
        self._last_poll_ok = now
        self._polls.mark_success(now)
        # The controller notified entities before the poll made the device reachable.
        self.async_update_listeners()

    async def _async_tick(self, _now: datetime | None = None) -> None:
        """Poll and sync the clock when due, but never while an active scan window may bring sensor data."""
        if self.hass.state is not CoreState.running or self._operation_lock.locked():
            return
        now = time.monotonic()
        poll_due = self._polls.is_due(now)
        clock_due = self.clock.is_due(dt_util.utcnow())
        if poll_due or clock_due:
            if active_window_open(self.hass, self.address):
                _LOGGER.debug(
                    "%s: Deferred; active scan window open", self.ble_device.name
                )
            else:
                if poll_due:
                    await self._async_attempt_poll(now)
                if clock_due:
                    await self.clock.async_attempt(self.async_run)
                    self.async_update_listeners()
        if self._polls.is_stalled(now):
            # Re-evaluate availability even when no state callback fired.
            self.async_update_listeners()

    async def _async_attempt_poll(self, now: float) -> None:
        self._polls.mark_attempt(now)
        try:
            await self._async_update()
        except Exception as ex:  # pylint: disable=broad-except
            self._polls.mark_failure()
            _LOGGER.debug(
                "%s: Poll failed (%d consecutive): %s",
                self.ble_device.name,
                self._polls.failures,
                ex,
            )
        else:
            _LOGGER.debug("%s: Poll succeeded", self.ble_device.name)

    @callback
    def _async_handle_unavailable(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Handle the device going unavailable."""
        super()._async_handle_unavailable(service_info)
        self._was_unavailable = True

    @callback
    def _async_handle_bluetooth_event(
        self,
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle a Bluetooth event."""
        self.ble_device = service_info.device
        if carries_sensor_data(service_info):
            self._async_parse_sensor_data(service_info)
        self._ready_event.set()
        self.logger.debug(
            "%s: AC Infinity data: %s", self.ble_device.address, self.controller.state
        )
        self._was_unavailable = False
        super()._async_handle_bluetooth_event(service_info, change)

    @callback
    def _async_parse_sensor_data(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        # Before parsing: the controller notifies entities while it parses.
        self._sensor_data_received = True
        self._polls.mark_heard(time.monotonic())
        try:
            self.controller.set_ble_device_and_advertisement_data(
                service_info.device, service_info.advertisement
            )
        except ValueError as ex:
            _LOGGER.debug("%s: Ignored advertisement: %s", self.ble_device.name, ex)

    async def async_wait_ready(self) -> bool:
        """Wait for the device to be ready."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(DEVICE_STARTUP_TIMEOUT):
                await self._ready_event.wait()
                return True
        return False
