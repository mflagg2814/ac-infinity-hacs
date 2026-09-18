"""The ac_infinity integration."""
from __future__ import annotations

import logging

from ac_infinity_ble import ACInfinityController, DeviceInfo
from bleak import BleakClient

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ADDRESS,
    CONF_SERVICE_DATA,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN
from .coordinator import ACInfinityDataUpdateCoordinator
from .models import ACInfinityData

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.FAN]

_LOGGER = logging.getLogger(__name__)


def _ensure_get_services_compat() -> None:
    """Restore BleakClient.get_services() for the ac_infinity_ble library.

    bleak removed the deprecated ``BleakClient.get_services()`` coroutine in
    favor of the ``BleakClient.services`` property. The ac_infinity_ble library
    still calls ``await client.get_services()`` in ``Device._ensure_connected``,
    which raises ``AttributeError`` on current Home Assistant builds and breaks
    every command (e.g. ``fan.set_percentage``). Re-add a thin async shim that
    returns the cached services collection so the library works unchanged.
    """
    if hasattr(BleakClient, "get_services"):
        return

    async def get_services(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return self.services

    BleakClient.get_services = get_services


_ensure_get_services_compat()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ac_infinity from a config entry."""
    address: str = entry.data[CONF_ADDRESS]
    ble_device = bluetooth.async_ble_device_from_address(hass, address.upper(), True)
    if not ble_device:
        raise ConfigEntryNotReady(
            f"Could not find AC Infinity device with address {address}"
        )

    device_info: DeviceInfo | dict = entry.data[CONF_SERVICE_DATA]
    if type(device_info) is dict:
        device_info = DeviceInfo(**entry.data[CONF_SERVICE_DATA])
    controller = ACInfinityController(ble_device, device_info)
    coordinator = ACInfinityDataUpdateCoordinator(hass, _LOGGER, ble_device, controller)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ACInfinityData(
        entry.title, controller, coordinator
    )

    entry.async_on_unload(coordinator.async_start())
    if not await coordinator.async_wait_ready():
        raise ConfigEntryNotReady(f"{address} is not advertising state")

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    data: ACInfinityData = hass.data[DOMAIN][entry.entry_id]
    if entry.title != data.title:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
