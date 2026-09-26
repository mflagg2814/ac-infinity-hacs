"""The ac_infinity fan platform."""

from collections.abc import Awaitable, Callable
from functools import partial
import math
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityDescription, FanEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import int_states_in_range, percentage_to_ranged_value, ranged_value_to_percentage

from .coordinator import ACInfinityConfigEntry
from .entity import ACInfinityEntity

SPEED_RANGE = (1, 10)
FAN = FanEntityDescription(key="fan", translation_key="fan")


def _speed_level(percentage: int) -> int:
    """Map a percentage to the controller's 0-10 level."""
    if percentage <= 0:
        return 0
    return math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ACInfinityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the fan platform."""
    async_add_entities([ACInfinityFan(entry.runtime_data, FAN)])


class ACInfinityFan(ACInfinityEntity, FanEntity):
    """The controller's fan output."""

    _attr_speed_count = int_states_in_range(SPEED_RANGE)
    _attr_supported_features = FanEntityFeature.SET_SPEED | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

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
        """Take the output's state and level."""
        level = self._device.state.fan
        self._attr_is_on = self._device.is_on
        self._attr_percentage = None if level is None else ranged_value_to_percentage(SPEED_RANGE, level)
