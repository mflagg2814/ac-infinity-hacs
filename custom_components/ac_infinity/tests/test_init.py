"""Setup, stale connections, and unload."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.const import CONF_ADDRESS, CONF_SERVICE_DATA
from homeassistant.exceptions import ConfigEntryNotReady

import custom_components.ac_infinity as integration
from custom_components.ac_infinity import async_setup_entry, async_unload_entry
from custom_components.ac_infinity.const import DOMAIN
from custom_components.ac_infinity.models import ACInfinityData

from .conftest import ADDRESS, SEED_STATE, SETUP_SEED
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


async def _hang(*_args) -> None:
    await asyncio.sleep(3600)


@pytest.fixture(autouse=True)
def close_stale():
    with patch.object(integration, "close_stale_connections_by_address", AsyncMock()) as mock:
        yield mock


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
    controller = MagicMock(stop=AsyncMock())
    patch_ble, patch_coordinator = _patch_setup(coordinator_instance)
    with patch_ble, patch_coordinator, patch.object(
        integration, "ACInfinityController", return_value=controller
    ):
        with pytest.raises(ConfigEntryNotReady):
            await async_setup_entry(hass, entry)
    controller.stop.assert_awaited_once()


async def test_setup_seeds_only_the_identity(hass, coordinator_instance):
    """The saved readings are months old."""
    entry = _entry(hass)
    patch_ble, patch_coordinator = _patch_setup(coordinator_instance)
    with patch_ble, patch_coordinator, patch.object(
        integration, "ACInfinityController"
    ) as make_controller:
        await async_setup_entry(hass, entry)

    (_ble_device, state), kwargs = make_controller.call_args
    assert state == SEED_STATE
    assert callable(kwargs["ble_device_provider"])


async def test_setup_closes_stale_connections(hass, coordinator_instance, close_stale):
    entry = _entry(hass)
    patch_ble, patch_coordinator = _patch_setup(coordinator_instance)
    with patch_ble, patch_coordinator:
        await async_setup_entry(hass, entry)
    close_stale.assert_awaited_once_with(ADDRESS)


@pytest.mark.parametrize("error", [RuntimeError("no BlueZ"), TimeoutError])
async def test_setup_survives_failing_stale_connection_cleanup(
    hass, coordinator_instance, close_stale, error
):
    close_stale.side_effect = error
    entry = _entry(hass)
    patch_ble, patch_coordinator = _patch_setup(coordinator_instance)
    with patch_ble, patch_coordinator:
        assert await async_setup_entry(hass, entry) is True


async def test_stale_connection_cleanup_is_time_limited(hass, coordinator_instance, close_stale):
    close_stale.side_effect = _hang
    entry = _entry(hass)
    patch_ble, patch_coordinator = _patch_setup(coordinator_instance)
    with (
        patch_ble,
        patch_coordinator,
        patch.object(integration, "STALE_CONNECTION_TIMEOUT", 0.01),
    ):
        assert await async_setup_entry(hass, entry) is True


async def test_setup_stores_data_and_forwards_platforms(hass, coordinator_instance, platform_setup):
    entry = _entry(hass)
    patch_ble, patch_coordinator = _patch_setup(coordinator_instance)
    with patch_ble, patch_coordinator:
        assert await async_setup_entry(hass, entry) is True

    data: ACInfinityData = hass.data[DOMAIN][entry.entry_id]
    assert data.coordinator is coordinator_instance
    platform_setup.assert_awaited_once()


# ===========================================================================
# async_unload_entry
# ===========================================================================
async def test_unload_pops_data_and_stops_the_controller(hass):
    entry = _entry(hass)
    controller = MagicMock(stop=AsyncMock())
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ACInfinityData(controller, MagicMock())
    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)):
        assert await async_unload_entry(hass, entry) is True
    assert entry.entry_id not in hass.data[DOMAIN]
    controller.stop.assert_awaited_once()


async def test_unload_gives_up_on_a_wedged_stop(hass):
    entry = _entry(hass)
    controller = MagicMock(stop=AsyncMock(side_effect=_hang))
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ACInfinityData(controller, MagicMock())
    with (
        patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)),
        patch.object(integration, "STOP_TIMEOUT", 0.01),
    ):
        assert await async_unload_entry(hass, entry) is True


async def test_unload_keeps_data_on_failure(hass):
    entry = _entry(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = ACInfinityData(MagicMock(), MagicMock())
    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)):
        assert await async_unload_entry(hass, entry) is False
    assert entry.entry_id in hass.data[DOMAIN]
