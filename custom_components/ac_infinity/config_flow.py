"""Config flow for ac_infinity."""

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak, async_discovered_service_info
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_SERVICE_DATA
from homeassistant.core import callback

from .const import BLEAK_EXCEPTIONS, DOMAIN
from .identity import identity_data
from .vendor.ac_infinity_ble import ACInfinityController, DeviceInfo, parse_manufacturer_data
from .vendor.ac_infinity_ble.const import MANUFACTURER_ID

_LOGGER = logging.getLogger(__name__)


def advertised_identity(discovery_info: BluetoothServiceInfoBleak) -> DeviceInfo | None:
    """The identity in a supported AC Infinity advertisement; None for any other device."""
    try:
        return parse_manufacturer_data(discovery_info.advertisement.manufacturer_data[MANUFACTURER_ID])
    except KeyError, ValueError:
        return None


async def async_read_identity(discovery_info: BluetoothServiceInfoBleak) -> DeviceInfo:
    """Read the device's settings, which proves it's controllable, then disconnect for good."""
    controller = ACInfinityController(discovery_info.device, advertisement_data=discovery_info.advertisement)
    try:
        await controller.update()
    finally:
        await controller.stop()
    return controller.state


class ACInfinityConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AC Infinity Bluetooth."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        # Kept across form re-shows, so a device briefly out of range stays listed
        self._discovered_devices: dict[str, tuple[BluetoothServiceInfoBleak, DeviceInfo]] = {}

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if (identity := advertised_identity(discovery_info)) is None:
            return self.async_abort(reason="no_devices_found")
        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {"name": identity.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm setting up the discovered device."""
        assert self._discovery_info is not None
        errors: dict[str, str] = {}
        if user_input is not None and (entry := await self._async_try_create_entry(self._discovery_info, errors)):
            return entry
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders=self.context["title_placeholders"],
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the user step to pick a discovered device."""
        errors: dict[str, str] = {}
        if user_input is not None:
            discovery_info, _identity = self._discovered_devices[user_input[CONF_ADDRESS]]
            await self.async_set_unique_id(discovery_info.address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            if entry := await self._async_try_create_entry(discovery_info, errors):
                return entry

        if not (devices := self._async_discover_devices()):
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(devices)}),
            errors=errors,
        )

    @callback
    def _async_discover_devices(self) -> dict[str, str]:
        """Labels, by address, of the unconfigured AC Infinity devices found during this flow."""
        configured = self._async_current_ids()
        for discovery_info in async_discovered_service_info(self.hass):
            if discovery_info.address not in configured and (identity := advertised_identity(discovery_info)):
                self._discovered_devices[discovery_info.address] = (discovery_info, identity)
        return {
            address: f"{identity.name} ({address})" for address, (_info, identity) in self._discovered_devices.items()
        }

    async def _async_try_create_entry(
        self, discovery_info: BluetoothServiceInfoBleak, errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        """The new entry, or None with the reason in errors."""
        try:
            identity = await async_read_identity(discovery_info)
        except BLEAK_EXCEPTIONS:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error")
            errors["base"] = "unknown"
        else:
            return self.async_create_entry(
                title=identity.name,
                data={CONF_ADDRESS: discovery_info.address, CONF_SERVICE_DATA: identity_data(identity)},
            )
        return None
