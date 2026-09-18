"""Whether Home Assistant has an active scan window open for a device."""
from __future__ import annotations

from habluetooth import BaseHaScanner

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback


def in_active_window(scanner: BaseHaScanner) -> bool:
    """An auto-mode scanner that is scanning actively is inside a window."""
    return (
        scanner.requested_mode is bluetooth.BluetoothScanningMode.AUTO
        and scanner.current_mode is bluetooth.BluetoothScanningMode.ACTIVE
    )


@callback
def active_window_open(hass: HomeAssistant, address: str) -> bool:
    """Whether any scanner that hears the device is inside an active scan window."""
    return any(
        in_active_window(device.scanner)
        for device in bluetooth.async_scanner_devices_by_address(
            hass, address, connectable=False
        )
    )
