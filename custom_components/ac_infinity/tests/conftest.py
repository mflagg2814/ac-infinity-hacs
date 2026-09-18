"""Shared fixtures for the AC Infinity coordinator tests."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from ac_infinity_ble import ACInfinityController, DeviceInfo
import pytest

from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import HomeAssistant

from custom_components.ac_infinity.coordinator import ACInfinityDataUpdateCoordinator

ADDRESS = "E2:07:87:B3:86:70"

# The advertisement stored in the config entry when it was created
SETUP_SEED = DeviceInfo(name="A-0ECGN", type=1, version=0, tmp=12.56, hum=80.0, fan=5)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the custom integration under the `hass` fixture for every test."""
    yield


@pytest.fixture
def controller() -> MagicMock:
    """An ACInfinityController whose GATT poll succeeds."""
    controller = MagicMock()
    controller.name = "A-0ECGN"
    controller.update = AsyncMock()
    controller.stop = AsyncMock()
    return controller


@pytest.fixture
async def seeded_device(hass: HomeAssistant) -> ACInfinityController:
    """A real controller on the setup seed; it reads the running event loop at construction."""
    return ACInfinityController(MagicMock(address=ADDRESS), SETUP_SEED)


@pytest.fixture
def coordinator(hass: HomeAssistant, controller: MagicMock) -> ACInfinityDataUpdateCoordinator:
    """A coordinator that isn't started, so no Bluetooth callbacks are registered."""
    ble_device = MagicMock(address=ADDRESS)
    ble_device.name = "ACI-UniversalController"
    with patch(
        "homeassistant.components.bluetooth.update_coordinator.async_address_present",
        return_value=False,
    ):
        return ACInfinityDataUpdateCoordinator(
            hass, logging.getLogger(__name__), ble_device, controller
        )


def service_info(manufacturer_data: dict[int, bytes] | None = None) -> MagicMock:
    """A BluetoothServiceInfoBleak for the device."""
    info = MagicMock()
    info.device.address = ADDRESS
    info.manufacturer_data = manufacturer_data or {}
    info.advertisement.manufacturer_data = info.manufacturer_data
    return info


def advertise(coordinator: ACInfinityDataUpdateCoordinator, info: MagicMock) -> None:
    """Deliver an advertisement without the Bluetooth manager."""
    with patch.object(PassiveBluetoothDataUpdateCoordinator, "_async_handle_bluetooth_event"):
        coordinator._async_handle_bluetooth_event(info, BluetoothChange.ADVERTISEMENT)
