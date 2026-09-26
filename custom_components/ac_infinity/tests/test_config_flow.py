"""The bluetooth discovery and user config flow steps."""

from unittest.mock import MagicMock, patch

from bleak.exc import BleakError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ac_infinity.const import BLEAK_EXCEPTIONS, DOMAIN
from custom_components.ac_infinity.vendor.ac_infinity_ble.const import MANUFACTURER_ID
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import CONF_ADDRESS, CONF_SERVICE_DATA
from homeassistant.data_entry_flow import FlowResultType

from .conftest import ADDRESS, SENSOR_PAYLOAD

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
        return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


# ===========================================================================
# Bluetooth discovery
# ===========================================================================
async def _discover(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=discovery())


async def test_bluetooth_discovery_asks_to_confirm_the_device(hass):
    """Bluetooth discovery asks to confirm the device."""
    result = await _discover(hass)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    assert result["description_placeholders"] == {"name": "A-0ECGN"}


async def test_confirming_a_discovered_device_creates_the_entry(hass, controller):
    """Confirming a discovered device creates the entry."""
    result = await _discover(hass)
    with _patch_controller(controller):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == ADDRESS
    assert result["data"][CONF_SERVICE_DATA] == {"type": 1, "name": "A-0ECGN", "version": 0}


async def test_a_failed_confirmation_reshows_the_form(hass, controller):
    """A failed confirmation reshows the form."""
    result = await _discover(hass)
    controller.update.side_effect = BleakError("out of range")
    with _patch_controller(controller):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert (result["type"], result["step_id"]) == (FlowResultType.FORM, "bluetooth_confirm")
    assert result["errors"] == {"base": "cannot_connect"}


async def test_bluetooth_discovery_aborts_when_already_configured(hass):
    """Bluetooth discovery aborts when already configured."""
    MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS).add_to_hass(hass)

    result = await _discover(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_bluetooth_discovery_aborts_on_an_unsupported_payload(hass):
    """Bluetooth discovery aborts on an unsupported payload."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_BLUETOOTH},
        data=discovery(manufacturer_data={MANUFACTURER_ID: bytes(19)}),
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


# ===========================================================================
# The user step's device list
# ===========================================================================
async def test_user_step_lists_discovered_devices(hass):
    """User step lists discovered devices."""
    result = await _init_user_step(hass, [discovery()])

    assert result["type"] is FlowResultType.FORM
    addresses = result["data_schema"].schema[CONF_ADDRESS].container
    assert ADDRESS in addresses


async def test_user_step_skips_already_configured_addresses(hass):
    """User step skips already configured addresses."""
    MockConfigEntry(domain=DOMAIN, unique_id=ADDRESS).add_to_hass(hass)

    result = await _init_user_step(hass, [discovery(), discovery(OTHER_ADDRESS)])

    addresses = result["data_schema"].schema[CONF_ADDRESS].container
    assert ADDRESS not in addresses
    assert OTHER_ADDRESS in addresses


async def test_user_step_ignores_non_ac_infinity_devices(hass):
    """User step ignores non ac infinity devices."""
    result = await _init_user_step(
        hass,
        [
            discovery(),
            discovery(OTHER_ADDRESS, manufacturer_data={}),
            discovery("11:22:33:44:55:66", manufacturer_data={MANUFACTURER_ID: bytes(19)}),
        ],
    )

    addresses = result["data_schema"].schema[CONF_ADDRESS].container
    assert list(addresses) == [ADDRESS]


async def test_user_step_aborts_when_no_devices_found(hass):
    """User step aborts when no devices found."""
    result = await _init_user_step(hass, [])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


# ===========================================================================
# Selecting a device
# ===========================================================================
def _patch_controller(controller):
    return patch("custom_components.ac_infinity.config_flow.ACInfinityController", return_value=controller)


async def test_selecting_a_device_creates_the_entry(hass, controller):
    """Selecting a device creates the entry."""
    result = await _init_user_step(hass, [discovery()])
    with _patch_controller(controller):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: ADDRESS})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "A-0ECGN"
    assert result["data"][CONF_ADDRESS] == ADDRESS
    assert result["data"][CONF_SERVICE_DATA] == {"type": 1, "name": "A-0ECGN", "version": 0}
    controller.stop.assert_awaited_once()


@pytest.mark.parametrize("exception", BLEAK_EXCEPTIONS)
async def test_connection_failure_reshows_the_form(hass, controller, exception):
    """Connection failure reshows the form."""
    result = await _init_user_step(hass, [discovery()])
    controller.update.side_effect = exception
    with _patch_controller(controller):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: ADDRESS})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    controller.stop.assert_awaited_once()


async def test_unexpected_error_reshows_the_form(hass, controller):
    """Unexpected error reshows the form."""
    result = await _init_user_step(hass, [discovery()])
    controller.update.side_effect = ValueError("boom")
    with _patch_controller(controller):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: ADDRESS})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
