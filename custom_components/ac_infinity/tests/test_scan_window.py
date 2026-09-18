"""Active scan window detection, and the Home Assistant scanner API it relies on."""
from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

from habluetooth import BaseHaScanner
import pytest

from homeassistant.components.bluetooth import BluetoothScannerDevice
from homeassistant.components.bluetooth import BluetoothScanningMode as Mode

from custom_components.ac_infinity import scan_window
from custom_components.ac_infinity.scan_window import active_window_open, in_active_window

from .conftest import ADDRESS


def _scanner(requested: Mode | None, current: Mode | None) -> MagicMock:
    return MagicMock(requested_mode=requested, current_mode=current)


@pytest.mark.parametrize(
    ("requested", "current", "expected"),
    [
        (Mode.AUTO, Mode.ACTIVE, True),
        (Mode.AUTO, Mode.PASSIVE, False),
        # Always active: there's no window to wait out
        (Mode.ACTIVE, Mode.ACTIVE, False),
        (Mode.PASSIVE, Mode.PASSIVE, False),
        (None, None, False),
    ],
)
def test_in_active_window(requested, current, expected):
    assert in_active_window(_scanner(requested, current)) is expected


@pytest.mark.parametrize(
    ("modes", "expected"),
    [
        ([], False),
        ([(Mode.AUTO, Mode.PASSIVE)], False),
        ([(Mode.AUTO, Mode.PASSIVE), (Mode.AUTO, Mode.ACTIVE)], True),
    ],
)
def test_any_scanner_hearing_the_device(hass, modes, expected):
    devices = [MagicMock(scanner=_scanner(*m)) for m in modes]
    with patch.object(
        scan_window.bluetooth, "async_scanner_devices_by_address", return_value=devices
    ) as lookup:
        assert active_window_open(hass, ADDRESS) is expected
    lookup.assert_called_once_with(hass, ADDRESS, connectable=False)


# Contract with Home Assistant: fail here, not silently at runtime, if these change.
def test_scanner_exposes_modes():
    assert hasattr(BaseHaScanner, "requested_mode")
    assert hasattr(BaseHaScanner, "current_mode")


def test_scanner_device_exposes_its_scanner():
    assert "scanner" in {field.name for field in dataclasses.fields(BluetoothScannerDevice)}


def test_auto_mode_exists():
    assert Mode("auto") is Mode.AUTO
