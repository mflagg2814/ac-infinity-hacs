"""The device identity kept in the config entry."""

from collections.abc import Mapping
from typing import Any

from .vendor.ac_infinity_ble import DeviceInfo


def identity_data(state: DeviceInfo) -> dict[str, Any]:
    """The part of a device's state the config entry keeps."""
    return {"type": state.type, "name": state.name, "version": state.version}


def seed_state(service_data: Mapping[str, Any]) -> DeviceInfo:
    """The saved identity; any readings saved with it are months old, so they're dropped."""
    return DeviceInfo(type=service_data["type"], name=service_data["name"], version=service_data["version"])
