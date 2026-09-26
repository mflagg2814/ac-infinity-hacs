"""When to poll the device."""

from dataclasses import dataclass

from .backoff import backoff_delay, count_failure

# Seconds without sensor advertisements or a successful poll; spans many scan windows
STALL_AFTER = 900


@dataclass
class PollSchedule:
    """One poll after startup, then only while sensor data has stalled; monotonic seconds."""

    last_heard: float
    polled: bool = False
    failures: int = 0
    last_attempt: float | None = None

    def is_stalled(self, now: float) -> bool:
        """Whether nothing has been heard from the device for STALL_AFTER."""
        return now - self.last_heard >= STALL_AFTER

    def is_due(self, now: float) -> bool:
        """Never polled or stalled, and any backoff from failed polls has elapsed."""
        if self.polled and not self.is_stalled(now):
            return False
        if self.failures and self.last_attempt is not None:
            return now - self.last_attempt >= self.backoff
        return True

    @property
    def backoff(self) -> float:
        """Seconds to wait after the last failed poll."""
        return backoff_delay(self.failures)

    def mark_heard(self, now: float) -> None:
        """Record sensor data arriving."""
        self.last_heard = now

    def mark_attempt(self, now: float) -> None:
        """Record a poll starting."""
        self.last_attempt = now

    def mark_success(self, now: float) -> None:
        """Record a poll succeeding."""
        self.polled = True
        self.last_heard = now
        self.failures = 0

    def mark_failure(self) -> None:
        """Record a poll failing."""
        self.failures = count_failure(self.failures)
