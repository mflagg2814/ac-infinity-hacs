"""Exponential backoff for failed device operations."""

BACKOFF_BASE = 120  # seconds
MAX_BACKOFF = 1800  # seconds
MAX_BACKOFF_EXPONENT = 6


def backoff_delay(failures: int) -> float:
    """Seconds to wait after this many consecutive failures."""
    return min(BACKOFF_BASE * 2.0**failures, MAX_BACKOFF)


def count_failure(failures: int) -> int:
    """The failure count after one more failure, capped so it stops growing."""
    return min(failures + 1, MAX_BACKOFF_EXPONENT)
