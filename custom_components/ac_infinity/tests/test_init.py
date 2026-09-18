"""Setup, unload, the options-reload listener, and the bleak get_services compat shim."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from bleak import BleakClient
import pytest

from homeassistant.const import CONF_ADDRESS, CONF_SERVICE_DATA
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.ac_infinity import (
    _async_update_listener,
    _ensure_get_services_compat,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.ac_infinity.const import DOMAIN
from custom_components.ac_infinity.models import ACInfinityData

from .conftest import ADDRESS, SETUP_SEED
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _entry(hass, **data_overrides):
    data = {CONF_ADDRESS: ADDRESS, CONF_SERVICE_DATA: SETUP_SEED, **data_overrides}
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def coordinator_instance():
    """A coordinator double that starts cleanly and is immediately ready."""
    instance = MagicMock()
    instance.async_start = MagicMock(return_value=MagicMock())
    instance.async_wait_ready = AsyncMock(return_value=True)
    return instance


@pytest.fixture(autouse=True)
def platform_setup(hass):
    """Skip forwarding to the real sensor/fan platforms."""
    with patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()) as mock:
        yield mock


def _patch_setup(coordinator_instance, *, ble_device=True):
    return (
        patch(
            "custom_components.ac_infinity.bluetooth.async_ble_device_from_address",
            return_value=MagicMock() if ble_device else None,
        ),
        patch(
            "custom_components.ac_infinity.ACInfinityDataUpdateCoordinator",
            return_value=coordinator_instance,
        ),
    )


# ===========================================================================
# get_services compat shim
# ===========================================================================
def test_adds_get_services_when_missing():
    original = BleakClient.get_services
    del BleakClient.get_services
    try:
        _ensure_get_services_compat()
        assert hasattr(BleakClient, "get_services")
    finally:
        BleakClient.get_services = original


async def test_get_services_shim_returns_cached_services():
    original = BleakClient.get_services
    del BleakClient.get_services
    try:
        _ensure_get_services_compat()
        client = MagicMock(services="cached")
        assert await BleakClient.get_services(client) == "cached"
    finally:
        BleakClient.get_services = original


def test_leaves_an_existing_get_services_alone():
    sentinel = object()
    original = BleakClient.get_services
    BleakClient.get_services = sentinel
    try:
        _ensure_get_services_compat()
        assert BleakClient.get_services is sentinel
    finally:
        BleakClient.get_services = original


# ===========================================================================
# async_setup_entry
# ===========================================================================
async def test_setup_fails_fast_when_device_not_found(hass, coordinator_instance):
    entry = _entry(hass)
    patch_ble, patch_coordinator = _patch_setup(coordinator_instance, ble_device=False)
    with patch_ble, patch_coordinator:
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)


async def test_setup_fails_when_device_never_becomes_ready(hass, coordinator_instance):
    entry = _entry(hass)
    coordinator_instance.async_wait_ready = AsyncMock(return_value=False)
    patch_ble, patch_coordinator = _patch_setup(coordinator_instance)
    with patch_ble, patch_coordinator:
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)


async def test_setup_converts_dict_service_data_to_device_info(hass, coordinator_instance):
    entry = _entry(hass, **{CONF_SERVICE_DATA: vars(SETUP_SEED)})
    captured = {}

    def _make_controller(ble_device, device_info):
        captured["device_info"] = device_info
        return MagicMock()

    patch_ble, patch_coordinator = _patch_setup(coordinator_instance)
    with patch_ble, patch_coordinator, patch(
        "custom_components.ac_infinity.ACInfinityController", side_effect=_make_controller
    ):
        await async_setup_entry(hass, entry)

    assert captured["device_info"] == SETUP_SEED


async def test_setup_stores_data_and_forwards_platforms(hass, coordinator_instance, platform_setup):
    entry = _entry(hass)
    patch_ble, patch_coordinator = _patch_setup(coordinator_instance)
    with patch_ble, patch_coordinator:
        assert await async_setup_entry(hass, entry) is True

    data: ACInfinityData = hass.data[DOMAIN][entry.entry_id]
    assert data.title == entry.title
    assert data.coordinator is coordinator_instance
    platform_setup.assert_awaited_once()


# ===========================================================================
# The options-reload listener
# ===========================================================================
async def test_update_listener_reloads_on_title_change(hass):
    entry = _entry(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ACInfinityData(
        "Old title", MagicMock(), MagicMock()
    )
    hass.config_entries.async_update_entry(entry, title="New title")

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        await _async_update_listener(hass, entry)
    reload.assert_awaited_once_with(entry.entry_id)


async def test_update_listener_skips_reload_when_title_unchanged(hass):
    entry = _entry(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ACInfinityData(
        entry.title, MagicMock(), MagicMock()
    )

    with patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload:
        await _async_update_listener(hass, entry)
    reload.assert_not_awaited()


# ===========================================================================
# async_unload_entry
# ===========================================================================
async def test_unload_pops_data_on_success(hass):
    entry = _entry(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ACInfinityData(
        entry.title, MagicMock(), MagicMock()
    )
    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)):
        assert await async_unload_entry(hass, entry) is True
    assert entry.entry_id not in hass.data[DOMAIN]


async def test_unload_keeps_data_on_failure(hass):
    entry = _entry(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ACInfinityData(
        entry.title, MagicMock(), MagicMock()
    )
    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)):
        assert await async_unload_entry(hass, entry) is False
    assert entry.entry_id in hass.data[DOMAIN]
