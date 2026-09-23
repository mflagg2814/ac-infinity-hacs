"""The ac_infinity integration models."""
from __future__ import annotations

from dataclasses import dataclass

from .coordinator import ACInfinityDataUpdateCoordinator
from .vendor.ac_infinity_ble import ACInfinityController


@dataclass
class ACInfinityData:
    """Data for the AC Infinity integration."""

    device: ACInfinityController
    coordinator: ACInfinityDataUpdateCoordinator
