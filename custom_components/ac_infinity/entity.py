"""Shared entity helpers."""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .vendor.ac_infinity_ble import ACInfinityController


def device_info(device: ACInfinityController) -> DeviceInfo:
    """The controller's device registry entry."""
    profile = device.state.profile
    return DeviceInfo(
        name=device.name,
        model=profile.model_name,
        model_id=profile.model_number,
        manufacturer="AC Infinity",
        sw_version=str(device.state.version),
        connections={(dr.CONNECTION_BLUETOOTH, device.address)},
    )
