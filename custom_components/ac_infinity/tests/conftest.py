"""Shared fixtures for the AC Infinity coordinator tests."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import HomeAssistant

from custom_components.ac_infinity.coordinator import ACInfinityDataUpdateCoordinator
from custom_components.ac_infinity.vendor.ac_infinity_ble import (
    ACInfinityController,
    DeviceInfo,
)
from custom_components.ac_infinity.vendor.ac_infinity_ble.util import crc16

ADDRESS = "E2:07:87:B3:86:70"

# The service data stored in the config entry when it was created
SETUP_SEED = {
    "choose_port": None, "fan": 5, "fan_state": 0, "fan_type": None, "hum": 80.0,
    "hum_state": 2, "is_degree": False, "level_off": None, "level_on": None,
    "name": "A-0ECGN", "tmp": 12.56, "tmp_state": 1, "type": 1, "version": 0,
    "vpd": None, "vpd_state": None, "work_type": None,
}
SEED_STATE = DeviceInfo(type=1, name="A-0ECGN", version=0)

# A scan response captured from the A-0ECGN: 22.35 °C, 54 %, fan level 8
SENSOR_PAYLOAD = bytes.fromhex("e20787b38670304543474e00010008bb1518080000000000000000")

# The A-0ECGN's reply to the 0x10-0x17 settings read: ON, OFF level 0, ON level 8
MODEL_REPLY = bytes.fromhex(
    "a510002e000215e6000110010211010012010813070f5a20380d50321404000007081504000007"
    "0816080000070800000708170409000f00139a"
)


def reply(request: bytes, payload: bytes) -> bytes:
    """The controller's reply frame to a request, as the A-0ECGN frames it."""
    body = bytes(request[8:10]) + payload
    header = bytes((0xA5, 0x10)) + len(payload).to_bytes(2, "big") + request[4:6]
    return header + bytes(crc16(list(header))) + body + bytes(crc16(list(body)))


def ack(request: bytes) -> bytes:
    """A successful acknowledgement naming every parameter a SET request wrote."""
    payload, tags = request[10:-2], []
    while payload:
        tags.append(payload[0])
        payload = payload[2 + payload[1] :]
    return reply(request, bytes(b for tag in tags for b in (tag, 0)))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the custom integration under the `hass` fixture for every test."""
    yield


@pytest.fixture
def controller() -> MagicMock:
    """An ACInfinityController whose GATT poll succeeds."""
    controller = MagicMock()
    controller.name = "A-0ECGN"
    controller.state = SEED_STATE
    controller.update = AsyncMock()
    controller.disconnect = AsyncMock()
    controller.set_clock = AsyncMock()
    controller.read_clock = AsyncMock(side_effect=TimeoutError)
    return controller


@pytest.fixture
async def seeded_device(hass: HomeAssistant) -> ACInfinityController:
    """A real controller on the setup seed; it reads the running event loop at construction."""
    return ACInfinityController(MagicMock(address=ADDRESS), SEED_STATE)


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
