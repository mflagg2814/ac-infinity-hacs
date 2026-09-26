"""Controller clock parameter: local wall-clock time, without a zone."""

from datetime import datetime

from .exceptions import ParameterValidationError

CLOCK_TAG = 0x01


def encode_clock(now: datetime) -> bytes:
    """Year of century, month, day, weekday (Sunday 1 to Saturday 7), time."""
    weekday = now.isoweekday() % 7 + 1
    return bytes((now.year % 100, now.month, now.day, weekday, now.hour, now.minute, now.second))


def decode_clock(value: bytes, reference: datetime) -> datetime:
    """Decode a clock value, taking the century from reference."""
    if len(value) != 7:
        raise ParameterValidationError("Invalid clock length")
    year, month, day, _weekday, hour, minute, second = value
    try:
        return datetime(
            reference.year - reference.year % 100 + year,
            month,
            day,
            hour,
            minute,
            second,
        )
    except ValueError as ex:
        raise ParameterValidationError("Invalid clock value") from ex
