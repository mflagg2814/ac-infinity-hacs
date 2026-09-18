"""PollSchedule: first poll, stall detection, and backoff."""
from __future__ import annotations

from custom_components.ac_infinity.polling import (
    BACKOFF_BASE,
    MAX_BACKOFF,
    MAX_BACKOFF_EXPONENT,
    STALL_AFTER,
    PollSchedule,
)


def test_first_poll_is_due_immediately():
    assert PollSchedule(last_heard=0).is_due(0)


def test_not_due_after_first_poll_until_stalled():
    schedule = PollSchedule(last_heard=0)
    schedule.mark_success(0)
    assert not schedule.is_due(STALL_AFTER - 1)
    assert schedule.is_due(STALL_AFTER)


def test_stall_boundary():
    schedule = PollSchedule(last_heard=1000)
    assert not schedule.is_stalled(1000 + STALL_AFTER - 1)
    assert schedule.is_stalled(1000 + STALL_AFTER)


def test_hearing_the_device_resets_the_stall():
    schedule = PollSchedule(last_heard=0, polled=True)
    schedule.mark_heard(STALL_AFTER)
    assert not schedule.is_due(STALL_AFTER + 1)


def test_failed_first_poll_backs_off():
    schedule = PollSchedule(last_heard=0)
    schedule.mark_attempt(0)
    schedule.mark_failure()
    assert not schedule.is_due(schedule.backoff - 1)
    assert schedule.is_due(schedule.backoff)


def test_backoff_after_failure():
    schedule = PollSchedule(last_heard=0, polled=True)
    now = STALL_AFTER
    schedule.mark_attempt(now)
    schedule.mark_failure()
    assert schedule.backoff == BACKOFF_BASE * 2
    assert not schedule.is_due(now + schedule.backoff - 1)
    assert schedule.is_due(now + schedule.backoff)


def test_backoff_is_capped():
    schedule = PollSchedule(last_heard=0)
    for _ in range(MAX_BACKOFF_EXPONENT + 3):
        schedule.mark_failure()
    assert schedule.failures == MAX_BACKOFF_EXPONENT
    assert schedule.backoff == MAX_BACKOFF


def test_success_clears_failures_and_stall():
    schedule = PollSchedule(last_heard=0, failures=3, last_attempt=STALL_AFTER)
    schedule.mark_success(STALL_AFTER + 5)
    assert schedule.failures == 0
    assert schedule.polled
    assert not schedule.is_stalled(STALL_AFTER + 6)
