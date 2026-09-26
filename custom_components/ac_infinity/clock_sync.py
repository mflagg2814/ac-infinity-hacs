"""Keeps the controller clock on Home Assistant's local time. See README.md."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from enum import StrEnum
import logging

from homeassistant.util import dt as dt_util

from .clock_schedule import ClockSchedule
from .vendor.ac_infinity_ble import ACInfinityController

# Largest offset, in seconds, that still confirms a sync; the clock has 1 s resolution.
CONFIRM_TOLERANCE = 2

_LOGGER = logging.getLogger(__name__)

Operation = Callable[[], Awaitable[None]]


class ClockStatus(StrEnum):
    """How the last sync went."""

    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"
    MISMATCH = "mismatch"
    FAILED = "failed"


def confirmation_status(offset_after: float | None) -> ClockStatus:
    """The status a successful write earns from the offset read back after it."""
    if offset_after is None:
        return ClockStatus.UNCONFIRMED
    if abs(offset_after) > CONFIRM_TOLERANCE:
        return ClockStatus.MISMATCH
    return ClockStatus.CONFIRMED


def local_now() -> datetime:
    """Home Assistant's local wall-clock time, which the controller keeps."""
    return dt_util.now().replace(tzinfo=None)


class ClockSync:
    """Sets the clock when due, reading it before and after when the controller allows."""

    def __init__(self, controller: ACInfinityController) -> None:
        """Start due, so the first tick syncs."""
        self._controller = controller
        self._schedule = ClockSchedule()
        self._can_read = True
        self.status: ClockStatus | None = None
        # Seconds the controller was ahead of local time, read before the last write
        self.drift: float | None = None
        self.last_attempt: datetime | None = None

    def is_due(self, now: datetime) -> bool:
        """Whether a sync should run now."""
        return self._schedule.is_due(now)

    def mark_due(self) -> None:
        """Make a sync due at once."""
        self._schedule.mark_due()

    async def async_attempt(self, async_run: Callable[[Operation], Awaitable[None]]) -> None:
        """Sync through async_run, which owns the connection."""
        self.last_attempt = dt_util.utcnow()
        try:
            await async_run(self._async_sync)
        except Exception as ex:  # noqa: BLE001 - any failure backs off and retries
            self.status = ClockStatus.FAILED
            self._schedule.mark_failure(dt_util.utcnow())
            _LOGGER.debug(
                "%s: Clock sync failed (%d consecutive): %s",
                self._controller.name,
                self._schedule.failures,
                ex,
            )
        else:
            self._schedule.mark_success(dt_util.utcnow(), dt_util.get_default_time_zone())

    async def _async_sync(self) -> None:
        before = await self._async_read_offset()
        if before is not None:
            self.drift = before
        await self._controller.set_clock(local_now)
        after = await self._async_read_offset()
        self.status = confirmation_status(after)
        self._log_sync(before, after)

    def _log_sync(self, before: float | None, after: float | None) -> None:
        if self.status is ClockStatus.MISMATCH:
            _LOGGER.warning("%s: Clock set, but reads back %+.0f s off", self._controller.name, after)
        elif before is None:
            _LOGGER.info("%s: Clock set", self._controller.name)
        else:
            _LOGGER.info("%s: Clock set; it was %+.0f s off", self._controller.name, before)

    async def _async_read_offset(self) -> float | None:
        """Seconds the controller is ahead of local time; None once a read has failed."""
        if not self._can_read:
            return None
        try:
            clock = await self._controller.read_clock(local_now())
        except Exception as ex:  # noqa: BLE001 - reading is optional; the write goes ahead
            self._can_read = False
            _LOGGER.info(
                "%s: Clock can't be read back, so syncs won't be confirmed: %r",
                self._controller.name,
                ex,
            )
            return None
        return (clock - local_now()).total_seconds()
