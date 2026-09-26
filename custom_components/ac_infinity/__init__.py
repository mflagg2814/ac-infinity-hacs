"""The ac_infinity integration."""

import asyncio
import logging

from bleak.backends.device import BLEDevice
from bleak_retry_connector import close_stale_connections_by_address

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, CONF_SERVICE_DATA, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .coordinator import ACInfinityConfigEntry, ACInfinityDataUpdateCoordinator
from .identity import seed_state
from .vendor.ac_infinity_ble import ACInfinityController

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.FAN]
STALE_CONNECTION_TIMEOUT = 5
# A wedged connect can't hold up a reload longer than this.
STOP_TIMEOUT = 10

_LOGGER = logging.getLogger(__name__)


async def _async_close_stale_connections(address: str) -> None:
    """Close a connection BlueZ still holds from before a restart or reload."""
    try:
        async with asyncio.timeout(STALE_CONNECTION_TIMEOUT):
            await close_stale_connections_by_address(address)
    except Exception as ex:  # noqa: BLE001 - best effort; a stale connection only delays setup
        _LOGGER.debug("%s: Closing stale connections failed: %s", address, ex)


async def async_setup_entry(hass: HomeAssistant, entry: ACInfinityConfigEntry) -> bool:
    """Set up ac_infinity from a config entry."""
    address: str = entry.data[CONF_ADDRESS].upper()

    def connectable_device() -> BLEDevice | None:
        return bluetooth.async_ble_device_from_address(hass, address, connectable=True)

    if not (ble_device := connectable_device()):
        raise ConfigEntryNotReady(f"Could not find AC Infinity device with address {address}")

    await _async_close_stale_connections(address)
    controller = ACInfinityController(
        ble_device,
        seed_state(entry.data[CONF_SERVICE_DATA]),
        ble_device_provider=connectable_device,
    )
    coordinator = ACInfinityDataUpdateCoordinator(hass, controller)

    entry.async_on_unload(coordinator.async_start())
    if not await coordinator.async_wait_ready():
        await _async_stop(controller)
        raise ConfigEntryNotReady(f"{address} is not advertising state")

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ACInfinityConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await _async_stop(entry.runtime_data.controller)

    return unload_ok


async def _async_stop(controller: ACInfinityController) -> None:
    """Disconnect for good; a command still in flight ends without reconnecting."""
    try:
        async with asyncio.timeout(STOP_TIMEOUT):
            await controller.stop()
    except TimeoutError:
        _LOGGER.warning("%s: Gave up waiting for an operation to stop", controller.name)
