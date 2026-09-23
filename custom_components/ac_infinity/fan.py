"""The ac_infinity fan platform."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
import math
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature

from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import (
    int_states_in_range,
    ranged_value_to_percentage,
    percentage_to_ranged_value,
)

from .const import DOMAIN
from .coordinator import ACInfinityDataUpdateCoordinator
from .entity import device_info
from .models import ACInfinityData
from .vendor.ac_infinity_ble import ACInfinityController

SPEED_RANGE = (1, 10)


def _speed_level(percentage: int) -> int:
    """Map a percentage to the controller's 0-10 level."""
    if percentage <= 0:
        return 0
    return math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the light platform for LEDBLE."""
    data: ACInfinityData = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ACInfinityFan(data.coordinator, data.device)])


class ACInfinityFan(
    PassiveBluetoothCoordinatorEntity[ACInfinityDataUpdateCoordinator], FanEntity
):
    """Representation of AC Infinity sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = "fan"
    _attr_speed_count = int_states_in_range(SPEED_RANGE)
    _attr_supported_features = FanEntityFeature.SET_SPEED

    def __init__(
        self,
        coordinator: ACInfinityDataUpdateCoordinator,
        device: ACInfinityController,
    ) -> None:
        """Initialize an AC Infinity sensor."""
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{self._device.address}_fan"
        self._attr_device_info = device_info(device)
        self._async_update_attrs()

    @property
    def available(self) -> bool:
        """Unavailable until real fan data arrives; then while the device is reachable."""
        return self.coordinator.has_fan_data and self.coordinator.reachable

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed of the fan, as a percentage."""
        speed = _speed_level(percentage)
        await self._async_command(partial(self._device.set_speed, speed))

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        speed = None if percentage is None else _speed_level(percentage)
        await self._async_command(partial(self._device.turn_on, speed))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the fan."""
        await self._async_command(self._device.turn_off)

    async def _async_command(self, command: Callable[[], Awaitable[None]]) -> None:
        """Show the commanded state; the device only reports it in a later advertisement."""
        await self.coordinator.async_run(command)
        self._async_update_attrs()
        self.async_write_ha_state()

    @callback
    def _async_update_attrs(self) -> None:
        """Handle updating _attr values."""
        level = self._device.state.fan
        self._attr_is_on = self._device.is_on
        self._attr_percentage = (
            None if level is None else ranged_value_to_percentage(SPEED_RANGE, level)
        )

    @callback
    def _handle_coordinator_update(self, *args: Any) -> None:
        """Handle data update."""
        self._async_update_attrs()
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        self.async_on_remove(
            self._device.register_callback(self._handle_coordinator_update)
        )
        return await super().async_added_to_hass()
