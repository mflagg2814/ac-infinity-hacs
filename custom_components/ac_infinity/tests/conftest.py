"""Shared fixtures, frames and setup helpers for the AC Infinity tests."""

# These tests drive and inspect the controller's and coordinator's internals; see README.md.
# ruff: noqa: SLF001

from collections.abc import Iterator
from contextlib import contextmanager
from typing import NamedTuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ac_infinity.const import DOMAIN
from custom_components.ac_infinity.coordinator import ACInfinityDataUpdateCoordinator
from custom_components.ac_infinity.vendor.ac_infinity_ble import ACInfinityController, DeviceInfo
from custom_components.ac_infinity.vendor.ac_infinity_ble.util import crc16
from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.components.bluetooth.passive_update_coordinator import PassiveBluetoothDataUpdateCoordinator
from homeassistant.const import CONF_ADDRESS, CONF_SERVICE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

ADDRESS = "E2:07:87:B3:86:70"

# The service data stored in the config entry when it was created
SETUP_SEED = {
    "choose_port": None,
    "fan": 5,
    "fan_state": 0,
    "fan_type": None,
    "hum": 80.0,
    "hum_state": 2,
    "is_degree": False,
    "level_off": None,
    "level_on": None,
    "name": "A-0ECGN",
    "tmp": 12.56,
    "tmp_state": 1,
    "type": 1,
    "version": 0,
    "vpd": None,
    "vpd_state": None,
    "work_type": None,
}
SEED_STATE = DeviceInfo(type=1, name="A-0ECGN", version=0)

# As registered in production: (unique id suffix, entity id, friendly name)
ENTITIES = [
    ("fan", "fan.blowymatron_fan", "Blowymatron Fan"),
    ("tmp", "sensor.blowymatron_temperature", "Blowymatron Temperature"),
    ("hum", "sensor.blowymatron_humidity", "Blowymatron Humidity"),
    ("clock_sync", "sensor.last_blowymatron_clock_sync", "Blowymatron Last Clock Sync"),
    ("clock_drift", "sensor.last_blowymatron_clock_drift", "Blowymatron Last Clock Drift"),
]

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


@pytest.fixture
def controller() -> MagicMock:
    """An ACInfinityController whose GATT poll succeeds and whose clock can't be read."""
    controller = MagicMock(address=ADDRESS)
    controller.name = "A-0ECGN"
    controller.state = SEED_STATE
    controller.update = AsyncMock()
    controller.disconnect = AsyncMock()
    controller.stop = AsyncMock()
    controller.set_clock = AsyncMock()
    controller.read_clock = AsyncMock(side_effect=TimeoutError)
    return controller


@pytest.fixture
async def seeded_device(hass: HomeAssistant) -> ACInfinityController:
    """A real controller on the setup seed; it reads the running event loop at construction."""
    return ACInfinityController(MagicMock(address=ADDRESS), SEED_STATE)


@pytest.fixture
def seeded_coordinator(hass: HomeAssistant, seeded_device: ACInfinityController) -> ACInfinityDataUpdateCoordinator:
    """A coordinator on the real seeded controller, not started."""
    return _unstarted_coordinator(hass, seeded_device)


@pytest.fixture
async def loaded_entry(hass: HomeAssistant, seeded_coordinator: ACInfinityDataUpdateCoordinator) -> MockConfigEntry:
    """The real platforms, set up on a device the user renamed to Blowymatron."""
    hass.config.components.add("bluetooth_adapters")
    entry = make_entry(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, ADDRESS)},
        name="A-0ECGN",
    )
    dr.async_get(hass).async_update_device(device.id, name_by_user="Blowymatron")
    for suffix, entity_id, _name in ENTITIES:
        domain, object_id = entity_id.split(".")
        er.async_get(hass).async_get_or_create(
            domain,
            DOMAIN,
            f"{ADDRESS}_{suffix}",
            suggested_object_id=object_id,
            config_entry=entry,
            device_id=device.id,
        )
    seeded_coordinator.async_start = MagicMock(return_value=MagicMock())
    seeded_coordinator.async_wait_ready = AsyncMock(return_value=True)
    with patched_setup(seeded_coordinator, seeded_coordinator.controller):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.fixture
def coordinator(hass: HomeAssistant, controller: MagicMock) -> ACInfinityDataUpdateCoordinator:
    """A coordinator on the controller fixture, not started."""
    return _unstarted_coordinator(hass, controller)


def _unstarted_coordinator(hass: HomeAssistant, controller: object) -> ACInfinityDataUpdateCoordinator:
    """Not started, so no Bluetooth callbacks are registered; the device isn't present."""
    with patch(
        "homeassistant.components.bluetooth.update_coordinator.async_address_present",
        return_value=False,
    ):
        return ACInfinityDataUpdateCoordinator(hass, controller)  # type: ignore[arg-type]


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


def make_entry(hass: HomeAssistant) -> MockConfigEntry:
    """The device's config entry, holding the setup seed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title="A-0ECGN",
        data={CONF_ADDRESS: ADDRESS, CONF_SERVICE_DATA: SETUP_SEED},
    )
    entry.add_to_hass(hass)
    return entry


class SetupPatches(NamedTuple):
    """The doubles async_setup_entry runs against."""

    close_stale: AsyncMock
    make_controller: MagicMock


@contextmanager
def patched_setup(
    coordinator: object, controller: object | None = None, *, ble_device: bool = True
) -> Iterator[SetupPatches]:
    """Setup with the BLE lookup, stale-connection cleanup, controller and coordinator replaced."""
    with (
        patch(
            "custom_components.ac_infinity.bluetooth.async_ble_device_from_address",
            return_value=MagicMock() if ble_device else None,
        ),
        patch("custom_components.ac_infinity.close_stale_connections_by_address", AsyncMock()) as close_stale,
        patch(
            "custom_components.ac_infinity.ACInfinityController",
            return_value=controller or MagicMock(stop=AsyncMock()),
        ) as make_controller,
        patch("custom_components.ac_infinity.ACInfinityDataUpdateCoordinator", return_value=coordinator),
    ):
        yield SetupPatches(close_stale, make_controller)
