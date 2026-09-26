"""The ac_infinity sensor platform."""

from collections.abc import Callable
from dataclasses import dataclass
import math
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPressure, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .clock_sync import ClockStatus, ClockSync
from .coordinator import ACInfinityConfigEntry
from .entity import ACInfinityEntity
from .vendor.ac_infinity_ble import DeviceInfo
from .vendor.ac_infinity_ble.capabilities import has_vpd_sensor


@dataclass(frozen=True, kw_only=True)
class ReadingDescription(SensorEntityDescription):
    """A climate reading from the device's advertisements."""

    value_fn: Callable[[DeviceInfo], float | None]
    exists_fn: Callable[[DeviceInfo], bool] = lambda _state: True
    # Smallest change reported, in the native unit. Every advertisement carries a
    # reading, and each change is a recorder row.
    band: float = 0


@dataclass(frozen=True, kw_only=True)
class ClockDescription(SensorEntityDescription):
    """A diagnostic about the integration's clock syncs."""

    value_fn: Callable[[ClockSync], StateType]
    attributes_fn: Callable[[ClockSync], dict[str, Any]] = lambda _clock: {}


READINGS = (
    ReadingDescription(
        key="tmp",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.tmp,
        band=0.1,
    ),
    ReadingDescription(
        key="hum",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.hum,
        band=2,
    ),
    ReadingDescription(
        key="vpd",
        translation_key="vpd",
        device_class=SensorDeviceClass.ATMOSPHERIC_PRESSURE,
        native_unit_of_measurement=UnitOfPressure.KPA,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state: state.vpd,
        exists_fn=lambda state: has_vpd_sensor(state.type, state.version),
    ),
)

CLOCK_SENSORS = (
    ClockDescription(
        key="clock_sync",
        translation_key="clock_sync",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.ENUM,
        options=[status.value for status in ClockStatus],
        value_fn=lambda clock: clock.status,
        attributes_fn=lambda clock: {"last_attempt": clock.last_attempt},
    ),
    ClockDescription(
        key="clock_drift",
        translation_key="clock_drift",
        entity_category=EntityCategory.DIAGNOSTIC,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda clock: clock.drift,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ACInfinityConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = entry.runtime_data
    state = coordinator.controller.state
    async_add_entities(
        [
            *(ReadingSensor(coordinator, description) for description in READINGS if description.exists_fn(state)),
            *(ClockSensor(coordinator, description) for description in CLOCK_SENSORS),
        ]
    )


def outside_band(kept: float | None, reading: float | None, band: float) -> bool:
    """Whether ``reading`` has moved at least ``band`` from ``kept``."""
    if kept is None or reading is None:
        return True
    change = abs(reading - kept)
    # Float subtraction lands just under the band, e.g. 23.3 - 23.2.
    return change >= band or math.isclose(change, band)


class ReadingSensor(ACInfinityEntity, SensorEntity):
    """A climate reading, moving only by its band."""

    entity_description: ReadingDescription
    _attr_native_value: float | None = None

    @property
    def available(self) -> bool:
        """Unavailable until a sensor advertisement arrives; then while the device is reachable."""
        return self.coordinator.has_sensor_data and self.coordinator.reachable

    @callback
    def _async_update_attrs(self) -> None:
        """Take the device's reading once it clears the band."""
        reading = self.entity_description.value_fn(self._device.state)
        if outside_band(self._attr_native_value, reading, self.entity_description.band):
            self._attr_native_value = reading


class ClockSensor(ACInfinityEntity, SensorEntity):
    """Clock sync diagnostics; they describe the integration's syncs, so always available."""

    entity_description: ClockDescription

    @property
    def available(self) -> bool:
        """Always available."""
        return True

    @callback
    def _async_update_attrs(self) -> None:
        """Take the last sync's result."""
        clock = self.coordinator.clock
        self._attr_native_value = self.entity_description.value_fn(clock)
        self._attr_extra_state_attributes = self.entity_description.attributes_fn(clock)
