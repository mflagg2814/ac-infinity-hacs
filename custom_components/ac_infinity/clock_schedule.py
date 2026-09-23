"""When to set the controller clock."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from .backoff import backoff_delay, count_failure

SYNC_INTERVAL = timedelta(days=1)
_OFFSET_CHANGE_RESOLUTION = timedelta(seconds=1)


def next_offset_change(tz: tzinfo, start: datetime, end: datetime) -> datetime | None:
    """Within a second after tz's UTC offset first changes after start, if by end.

    Assumes at most one change between start and end.
    """
    offset = start.astimezone(tz).utcoffset()
    if end.astimezone(tz).utcoffset() == offset:
        return None
    while end - start > _OFFSET_CHANGE_RESOLUTION:
        middle = start + (end - start) / 2
        if middle.astimezone(tz).utcoffset() == offset:
            start = middle
        else:
            end = middle
    return end


@dataclass
class ClockSchedule:
    """Due at startup, a day after each sync, and when the local UTC offset changes."""

    next_due: datetime | None = None
    failures: int = 0

    def is_due(self, now: datetime) -> bool:
        return self.next_due is None or now >= self.next_due

    def mark_due(self) -> None:
        self.next_due = None

    def mark_success(self, now: datetime, tz: tzinfo) -> None:
        self.failures = 0
        end = now + SYNC_INTERVAL
        self.next_due = next_offset_change(tz, now, end) or end

    def mark_failure(self, now: datetime) -> None:
        self.failures = count_failure(self.failures)
        self.next_due = now + timedelta(seconds=backoff_delay(self.failures))
