"""ClockSchedule and UTC offset change detection (pure)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.ac_infinity.backoff import MAX_BACKOFF, backoff_delay
from custom_components.ac_infinity.clock_schedule import SYNC_INTERVAL, ClockSchedule, next_offset_change

CHICAGO = ZoneInfo("America/Chicago")
# Changes by 30 minutes, at 02:00 local
LORD_HOWE = ZoneInfo("Australia/Lord_Howe")


def utc(*args: int) -> datetime:
    """A UTC datetime."""
    return datetime(*args, tzinfo=UTC)


# ===========================================================================
# next_offset_change
# ===========================================================================
@pytest.mark.parametrize(
    ("tz", "start", "change"),
    [
        (CHICAGO, utc(2026, 3, 7, 12), utc(2026, 3, 8, 8)),
        (CHICAGO, utc(2026, 10, 31, 12), utc(2026, 11, 1, 7)),
        (LORD_HOWE, utc(2026, 4, 4, 0), utc(2026, 4, 4, 15)),
        (LORD_HOWE, utc(2026, 10, 3, 0), utc(2026, 10, 3, 15, 30)),
    ],
)
def test_finds_the_change_to_the_second(tz, start, change):
    """Finds the change to the second."""
    found = next_offset_change(tz, start, start + SYNC_INTERVAL)
    assert change <= found <= change + timedelta(seconds=1)
    assert found.astimezone(tz).utcoffset() != start.astimezone(tz).utcoffset()


def test_no_change_in_the_window():
    """No change in the window."""
    start = utc(2026, 6, 1)
    assert next_offset_change(CHICAGO, start, start + SYNC_INTERVAL) is None


def test_zone_without_changes():
    """Zone without changes."""
    start = utc(2026, 3, 7, 12)
    assert next_offset_change(UTC, start, start + SYNC_INTERVAL) is None


def test_change_after_the_window_is_ignored():
    """Change after the window is ignored."""
    start = utc(2026, 3, 6, 12)
    assert next_offset_change(CHICAGO, start, start + SYNC_INTERVAL) is None


# ===========================================================================
# ClockSchedule
# ===========================================================================
def test_due_at_startup():
    """Due at startup."""
    assert ClockSchedule().is_due(utc(2026, 6, 1)) is True


def test_next_due_a_day_after_a_sync():
    """Next due a day after a sync."""
    schedule = ClockSchedule()
    now = utc(2026, 6, 1)
    schedule.mark_success(now, CHICAGO)
    assert schedule.is_due(now + SYNC_INTERVAL - timedelta(seconds=1)) is False
    assert schedule.is_due(now + SYNC_INTERVAL) is True


def test_due_when_the_offset_changes_before_the_day_is_up():
    """Due when the offset changes before the day is up."""
    schedule = ClockSchedule()
    schedule.mark_success(utc(2026, 11, 1, 0), CHICAGO)
    assert schedule.is_due(utc(2026, 11, 1, 6, 59, 59)) is False
    assert schedule.is_due(utc(2026, 11, 1, 7, 0, 1)) is True


def test_failures_back_off_exponentially_up_to_the_cap():
    """Failures back off exponentially up to the cap."""
    schedule = ClockSchedule()
    now = utc(2026, 6, 1)
    schedule.mark_failure(now)
    assert schedule.next_due == now + timedelta(seconds=backoff_delay(1))
    for _ in range(10):
        schedule.mark_failure(now)
    assert schedule.next_due == now + timedelta(seconds=MAX_BACKOFF)


def test_success_resets_failures():
    """Success resets failures."""
    schedule = ClockSchedule()
    now = utc(2026, 6, 1)
    schedule.mark_failure(now)
    schedule.mark_success(now, CHICAGO)
    assert schedule.failures == 0
    assert schedule.next_due == now + SYNC_INTERVAL


def test_mark_due_overrides_a_pending_sync_or_backoff():
    """Mark due overrides a pending sync or backoff."""
    schedule = ClockSchedule()
    now = utc(2026, 6, 1)
    schedule.mark_failure(now)
    schedule.mark_due()
    assert schedule.is_due(now) is True
