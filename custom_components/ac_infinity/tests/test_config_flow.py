"""The bluetooth discovery and user config flow steps."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from ac_infinity_ble.const import MANUFACTURER_ID
from ac_infinity_ble.protocol import parse_manufacturer_data
import pytest

from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS, CONF_SERVICE_DATA
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ac_infinity.const import DOMAIN, BLEAK_EXCEPTIONS

from .conftest import ADDRESS
from pytest_homeassistant_custom_component.common import MockConfigEntry

# All zero, but long enough for parse_manufacturer_data to read a real DeviceInfo.
SENSOR_PAYLOAD = bytes(19)
OTHER_ADDRESS = "AA:BB:CC:DD:EE:FF"


def discovery(address: str = ADDRESS, *, manufacturer_data: dict[int, bytes] | None = None) -> MagicMock:
    """A BluetoothServiceInfoBleak double for the config flow's discovery steps."""
    info = MagicMock()
    info.address = address
    info.advertisement.manufacturer_data = (
        manufacturer_data if manufacturer_data is not None else {MANUFACTURER_ID: SENSOR_PAYLOAD}
    )
    return info


async def _init_user_step(hass, devices):
    with patch(
        "custom_components.ac_infinity.config_flow.async_discovered_service_info",
        return_value=devices,
    ):
        return await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )


# ===========================================================================
# Bluetooth discovery
# ===========================================================================
async def test_bluetooth_discovery_proceeds_to_user_step(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=discovery()
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


async def test_bluetooth_discovery_aborts_when_already_configured(hass):
    MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=discovery()
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ===========================================================================
# The user step's device list
# ===========================================================================
async def test_user_step_lists_discovered_devices(hass):
    result = await _init_user_step(hass, [discovery()])

    assert result["type"] is FlowResultType.FORM
    addresses = result["data_schema"].schema[CONF_ADDRESS].container
    assert ADDRESS in addresses


async def test_user_step_skips_already_configured_addresses(hass):
    MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS).add_to_hass(hass)

    result = await _init_user_step(
        hass, [discovery(), discovery(OTHER_ADDRESS)]
    )

    addresses = result["data_schema"].schema[CONF_ADDRESS].container
    assert ADDRESS not in addresses
    assert OTHER_ADDRESS in addresses


async def test_user_step_ignores_non_ac_infinity_devices(hass):
    result = await _init_user_step(
        hass,
        [discovery(), discovery(OTHER_ADDRESS, manufacturer_data={})],
    )

    addresses = result["data_schema"].schema[CONF_ADDRESS].container
    assert list(addresses) == [ADDRESS]


async def test_user_step_aborts_when_no_devices_found(hass):
    result = await _init_user_step(hass, [])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


# ===========================================================================
# Selecting a device
# ===========================================================================
@pytest.fixture
def controller():
    controller = MagicMock()
    controller.name = "A-0ECGN"
    controller.update = AsyncMock()
    controller.stop = AsyncMock()
    return controller


async def test_selecting_a_device_creates_the_entry(hass, controller):
    result = await _init_user_step(hass, [discovery()])
    with patch(
        "custom_components.ac_infinity.config_flow.ACInfinityController",
        return_value=controller,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "A-0ECGN"
    assert result["data"][CONF_ADDRESS] == ADDRESS
    assert result["data"][CONF_SERVICE_DATA] == parse_manufacturer_data(SENSOR_PAYLOAD)
    controller.stop.assert_awaited_once()


@pytest.mark.parametrize("exception", BLEAK_EXCEPTIONS)
async def test_connection_failure_reshows_the_form(hass, controller, exception):
    result = await _init_user_step(hass, [discovery()])
    controller.update.side_effect = exception
    with patch(
        "custom_components.ac_infinity.config_flow.ACInfinityController",
        return_value=controller,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_unexpected_error_reshows_the_form(hass, controller):
    result = await _init_user_step(hass, [discovery()])
    controller.update.side_effect = ValueError("boom")
    with patch(
        "custom_components.ac_infinity.config_flow.ACInfinityController",
        return_value=controller,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_ADDRESS: ADDRESS}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
