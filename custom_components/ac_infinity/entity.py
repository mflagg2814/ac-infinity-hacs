"""The base entity for the controller's device."""

from homeassistant.components.bluetooth.passive_update_coordinator import PassiveBluetoothCoordinatorEntity
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import EntityDescription

from .coordinator import ACInfinityDataUpdateCoordinator


class ACInfinityEntity(PassiveBluetoothCoordinatorEntity[ACInfinityDataUpdateCoordinator]):
    """An entity on the controller's device, refreshed on each coordinator update."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ACInfinityDataUpdateCoordinator, description: EntityDescription) -> None:
        """Initialize the entity from the device's current state."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device = coordinator.controller
        profile = self._device.state.profile
        self._attr_unique_id = f"{self._device.address}_{description.key}"
        self._attr_device_info = dr.DeviceInfo(
            name=self._device.name,
            model=profile.model_name,
            model_id=profile.model_number,
            manufacturer="AC Infinity",
            sw_version=str(self._device.state.version),
            connections={(dr.CONNECTION_BLUETOOTH, self._device.address)},
        )
        self._async_update_attrs()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh the _attr values, then write the state."""
        self._async_update_attrs()
        super()._handle_coordinator_update()

    @callback
    def _async_update_attrs(self) -> None:
        """Refresh the _attr values from the device's state."""
