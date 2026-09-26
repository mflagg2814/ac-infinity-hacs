"""ClockSync: setting, reading back, logging, and scheduling the controller clock."""

from datetime import UTC, datetime, timedelta
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.ac_infinity.backoff import backoff_delay
from custom_components.ac_infinity.clock_schedule import SYNC_INTERVAL
from custom_components.ac_infinity.clock_sync import (
    CONFIRM_TOLERANCE,
    ClockStatus,
    ClockSync,
    confirmation_status,
    local_now,
)
from homeassistant.util import dt as dt_util

# 05:00 local in the test instance's US/Pacific zone
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
LOCAL_NOW = datetime(2026, 6, 1, 5, 0)


async def run(operation) -> None:
    """async_run without a connection."""
    await operation()


@pytest.fixture(autouse=True)
def frozen(freezer):
    """Freeze time at NOW."""
    freezer.move_to(NOW)
    return freezer


@pytest.fixture
def controller() -> MagicMock:
    """A controller whose clock reads back as local time."""
    controller = MagicMock()
    controller.name = "A-0ECGN"
    controller.set_clock = AsyncMock()
    controller.read_clock = AsyncMock(return_value=LOCAL_NOW)
    return controller


@pytest.fixture
def clock(controller) -> ClockSync:
    """A ClockSync on the mock controller."""
    return ClockSync(controller)


def test_local_now_is_naive_local_time(hass):
    """Local now is naive local time."""
    assert local_now() == LOCAL_NOW


async def test_sets_the_clock_to_local_time(hass, clock, controller):
    """Sets the clock to local time."""
    await clock.async_attempt(run)
    controller.set_clock.assert_awaited_once_with(local_now)


async def test_logs_the_offset_it_corrected(hass, clock, controller, caplog):
    """Logs the offset it corrected."""
    controller.read_clock.side_effect = [LOCAL_NOW + timedelta(minutes=3), LOCAL_NOW]
    with caplog.at_level(logging.INFO):
        await clock.async_attempt(run)
    assert "Clock set; it was +180 s off" in caplog.text
    controller.read_clock.assert_awaited_with(LOCAL_NOW)


async def test_warns_when_the_clock_reads_back_wrong(hass, clock, controller, caplog):
    """Warns when the clock reads back wrong."""
    controller.read_clock.side_effect = [LOCAL_NOW, LOCAL_NOW - timedelta(hours=1)]
    await clock.async_attempt(run)
    assert "reads back -3600 s off" in caplog.text


async def test_unreadable_clock_is_still_set_and_not_read_again(hass, clock, controller, caplog):
    """Unreadable clock is still set and not read again."""
    controller.read_clock.side_effect = TimeoutError
    with caplog.at_level(logging.INFO):
        await clock.async_attempt(run)
        clock.mark_due()
        await clock.async_attempt(run)
    assert controller.set_clock.await_count == 2
    controller.read_clock.assert_awaited_once()
    assert "Clock can't be read back" in caplog.text
    assert clock.is_due(dt_util.utcnow()) is False


async def test_next_sync_a_day_later(hass, clock):
    """Next sync a day later."""
    await clock.async_attempt(run)
    assert clock.is_due(NOW + SYNC_INTERVAL - timedelta(seconds=1)) is False
    assert clock.is_due(NOW + SYNC_INTERVAL) is True


async def test_next_sync_when_daylight_saving_time_ends(hass, clock, frozen):
    """Next sync when daylight saving time ends."""
    frozen.move_to(datetime(2026, 11, 1, 8, 0, tzinfo=UTC))
    await clock.async_attempt(run)
    change = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    assert clock.is_due(change - timedelta(seconds=1)) is False
    assert clock.is_due(change + timedelta(seconds=1)) is True


async def test_failed_set_backs_off(hass, clock, controller):
    """Failed set backs off."""
    controller.set_clock.side_effect = TimeoutError
    await clock.async_attempt(run)
    assert clock.is_due(NOW + timedelta(seconds=backoff_delay(1) - 1)) is False
    assert clock.is_due(NOW + timedelta(seconds=backoff_delay(1))) is True


# ===========================================================================
# Status and drift
# ===========================================================================
@pytest.mark.parametrize(
    ("offset", "status"),
    [
        (None, ClockStatus.UNCONFIRMED),
        (0.0, ClockStatus.CONFIRMED),
        (-CONFIRM_TOLERANCE, ClockStatus.CONFIRMED),
        (CONFIRM_TOLERANCE + 1, ClockStatus.MISMATCH),
        (-CONFIRM_TOLERANCE - 1, ClockStatus.MISMATCH),
    ],
)
def test_confirmation_status(offset, status):
    """Confirmation status."""
    assert confirmation_status(offset) is status


def test_nothing_known_before_the_first_attempt(clock):
    """Nothing known before the first attempt."""
    assert (clock.status, clock.drift, clock.last_attempt) == (None, None, None)


async def test_confirmed_sync_records_drift_and_attempt(hass, clock, controller):
    """Confirmed sync records drift and attempt."""
    controller.read_clock.side_effect = [LOCAL_NOW - timedelta(seconds=40), LOCAL_NOW]
    await clock.async_attempt(run)
    assert (clock.status, clock.drift, clock.last_attempt) == (ClockStatus.CONFIRMED, -40, NOW)


async def test_unreadable_clock_is_unconfirmed_without_drift(hass, clock, controller):
    """Unreadable clock is unconfirmed without drift."""
    controller.read_clock.side_effect = TimeoutError
    await clock.async_attempt(run)
    assert (clock.status, clock.drift) == (ClockStatus.UNCONFIRMED, None)


async def test_wrong_read_back_is_a_mismatch(hass, clock, controller):
    """Wrong read back is a mismatch."""
    controller.read_clock.side_effect = [LOCAL_NOW, LOCAL_NOW + timedelta(minutes=1)]
    await clock.async_attempt(run)
    assert clock.status is ClockStatus.MISMATCH


async def test_failed_write_keeps_the_drift_it_read(hass, clock, controller):
    """Failed write keeps the drift it read."""
    controller.read_clock.return_value = LOCAL_NOW + timedelta(seconds=90)
    controller.set_clock.side_effect = TimeoutError
    await clock.async_attempt(run)
    assert (clock.status, clock.drift) == (ClockStatus.FAILED, 90)


async def test_later_success_replaces_a_failure(hass, clock, controller):
    """Later success replaces a failure."""
    controller.set_clock.side_effect = [TimeoutError, None]
    await clock.async_attempt(run)
    await clock.async_attempt(run)
    assert clock.status is ClockStatus.CONFIRMED
