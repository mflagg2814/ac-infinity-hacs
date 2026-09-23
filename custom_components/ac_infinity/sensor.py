"""The ac_infinity sensor platform."""
from __future__ import annotations
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)

from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .clock_sync import ClockStatus
from .const import DOMAIN
from .coordinator import ACInfinityDataUpdateCoordinator
from .entity import device_info
from .models import ACInfinityData
from .vendor.ac_infinity_ble import ACInfinityController


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the light platform for LEDBLE."""
    data: ACInfinityData = hass.data[DOMAIN][entry.entry_id]
    entities = [
        TemperatureSensor(data.coordinator, data.device),
        HumiditySensor(data.coordinator, data.device),
    ]
    if data.device.state.version >= 3 and data.device.state.type in [7, 9, 11, 12]:
        entities.append(VpdSensor(data.coordinator, data.device))
    entities += [
        ClockSyncSensor(data.coordinator, data.device),
        ClockDriftSensor(data.coordinator, data.device),
    ]
    async_add_entities(entities)


class ACInfinitySensor(
    PassiveBluetoothCoordinatorEntity[ACInfinityDataUpdateCoordinator], SensorEntity
):
    """Representation of AC Infinity sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ACInfinityDataUpdateCoordinator,
        device: ACInfinityController,
    ) -> None:
        """Initialize an AC Infinity sensor."""
        super().__init__(coordinator)
        self._device = device
        self._attr_device_info = device_info(device)
        self._async_update_attrs()

    @property
    def available(self) -> bool:
        """Unavailable until a sensor advertisement arrives; then while the device is reachable."""
        return self.coordinator.has_sensor_data and self.coordinator.reachable

    @callback
    def _async_update_attrs(self) -> None:
        """Handle updating _attr values."""
        raise NotImplementedError("Not yet implemented.")

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


class TemperatureSensor(ACInfinitySensor):
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        """Return a unique, Home Assistant friendly identifier for this entity."""
        return f"{self._device.address}_tmp"

    @callback
    def _async_update_attrs(self) -> None:
        """Handle updating _attr values."""
        self._attr_native_value = self._device.temperature


class HumiditySensor(ACInfinitySensor):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        """Return a unique, Home Assistant friendly identifier for this entity."""
        return f"{self._device.address}_hum"

    @callback
    def _async_update_attrs(self) -> None:
        """Handle updating _attr values."""
        self._attr_native_value = self._device.humidity


class VpdSensor(ACInfinitySensor):
    _attr_translation_key = "vpd"
    _attr_native_unit_of_measurement = UnitOfPressure.KPA
    _attr_device_class = SensorDeviceClass.ATMOSPHERIC_PRESSURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def unique_id(self) -> str:
        """Return a unique, Home Assistant friendly identifier for this entity."""
        return f"{self._device.address}_vpd"

    @callback
    def _async_update_attrs(self) -> None:
        """Handle updating _attr values."""
        self._attr_native_value = self._device.vpd


class ClockSensor(
    PassiveBluetoothCoordinatorEntity[ACInfinityDataUpdateCoordinator], SensorEntity
):
    """Clock sync diagnostics; they describe the integration's syncs, so always available."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _key: str

    def __init__(
        self,
        coordinator: ACInfinityDataUpdateCoordinator,
        device: ACInfinityController,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{device.address}_{self._key}"
        self._attr_translation_key = self._key
        self._attr_device_info = device_info(device)

    @property
    def available(self) -> bool:
        return True


class ClockSyncSensor(ClockSensor):
    _key = "clock_sync"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [status.value for status in ClockStatus]

    @property
    def native_value(self) -> str | None:
        status = self.coordinator.clock.status
        return None if status is None else status.value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_attempt": self.coordinator.clock.last_attempt}


class ClockDriftSensor(ClockSensor):
    """Seconds the clock was ahead of local time, read before the last sync."""

    _key = "clock_drift"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        return self.coordinator.clock.drift
