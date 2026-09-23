"""ClockSync: setting, reading back, logging, and scheduling the controller clock."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from homeassistant.util import dt as dt_util

from custom_components.ac_infinity.backoff import backoff_delay
from custom_components.ac_infinity.clock_schedule import SYNC_INTERVAL
from custom_components.ac_infinity.clock_sync import (
    CONFIRM_TOLERANCE,
    ClockStatus,
    ClockSync,
    confirmation_status,
    local_now,
)

# 05:00 local in the test instance's US/Pacific zone
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
LOCAL_NOW = datetime(2026, 6, 1, 5, 0)


async def run(operation) -> None:
    """async_run without a connection."""
    await operation()


@pytest.fixture(autouse=True)
def frozen(freezer):
    freezer.move_to(NOW)
    return freezer


@pytest.fixture
def controller() -> MagicMock:
    controller = MagicMock()
    controller.name = "A-0ECGN"
    controller.set_clock = AsyncMock()
    controller.read_clock = AsyncMock(return_value=LOCAL_NOW)
    return controller


@pytest.fixture
def clock(controller) -> ClockSync:
    return ClockSync(controller)


def test_local_now_is_naive_local_time(hass):
    assert local_now() == LOCAL_NOW


async def test_sets_the_clock_to_local_time(hass, clock, controller):
    await clock.async_attempt(run)
    controller.set_clock.assert_awaited_once_with(local_now)


async def test_logs_the_offset_it_corrected(hass, clock, controller, caplog):
    controller.read_clock.side_effect = [LOCAL_NOW + timedelta(minutes=3), LOCAL_NOW]
    with caplog.at_level(logging.INFO):
        await clock.async_attempt(run)
    assert "Clock set; it was +180 s off" in caplog.text
    controller.read_clock.assert_awaited_with(LOCAL_NOW)


async def test_warns_when_the_clock_reads_back_wrong(hass, clock, controller, caplog):
    controller.read_clock.side_effect = [LOCAL_NOW, LOCAL_NOW - timedelta(hours=1)]
    await clock.async_attempt(run)
    assert "reads back -3600 s off" in caplog.text


async def test_unreadable_clock_is_still_set_and_not_read_again(hass, clock, controller, caplog):
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
    await clock.async_attempt(run)
    assert clock.is_due(NOW + SYNC_INTERVAL - timedelta(seconds=1)) is False
    assert clock.is_due(NOW + SYNC_INTERVAL) is True


async def test_next_sync_when_daylight_saving_time_ends(hass, clock, frozen):
    frozen.move_to(datetime(2026, 11, 1, 8, 0, tzinfo=UTC))
    await clock.async_attempt(run)
    change = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    assert clock.is_due(change - timedelta(seconds=1)) is False
    assert clock.is_due(change + timedelta(seconds=1)) is True


async def test_failed_set_backs_off(hass, clock, controller):
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
    assert confirmation_status(offset) is status


def test_nothing_known_before_the_first_attempt(clock):
    assert (clock.status, clock.drift, clock.last_attempt) == (None, None, None)


async def test_confirmed_sync_records_drift_and_attempt(hass, clock, controller):
    controller.read_clock.side_effect = [LOCAL_NOW - timedelta(seconds=40), LOCAL_NOW]
    await clock.async_attempt(run)
    assert (clock.status, clock.drift, clock.last_attempt) == (ClockStatus.CONFIRMED, -40, NOW)


async def test_unreadable_clock_is_unconfirmed_without_drift(hass, clock, controller):
    controller.read_clock.side_effect = TimeoutError
    await clock.async_attempt(run)
    assert (clock.status, clock.drift) == (ClockStatus.UNCONFIRMED, None)


async def test_wrong_read_back_is_a_mismatch(hass, clock, controller):
    controller.read_clock.side_effect = [LOCAL_NOW, LOCAL_NOW + timedelta(minutes=1)]
    await clock.async_attempt(run)
    assert clock.status is ClockStatus.MISMATCH


async def test_failed_write_keeps_the_drift_it_read(hass, clock, controller):
    controller.read_clock.return_value = LOCAL_NOW + timedelta(seconds=90)
    controller.set_clock.side_effect = TimeoutError
    await clock.async_attempt(run)
    assert (clock.status, clock.drift) == (ClockStatus.FAILED, 90)


async def test_later_success_replaces_a_failure(hass, clock, controller):
    controller.set_clock.side_effect = [TimeoutError, None]
    await clock.async_attempt(run)
    await clock.async_attempt(run)
    assert clock.status is ClockStatus.CONFIRMED
