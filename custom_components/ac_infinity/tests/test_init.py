"""Setup, stale connections, and unload."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import custom_components.ac_infinity as integration
from custom_components.ac_infinity import async_setup_entry, async_unload_entry
from homeassistant.exceptions import ConfigEntryNotReady

from .conftest import ADDRESS, SEED_STATE, make_entry, patched_setup


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
def platform_setup(hass):
    """Skip forwarding to the real sensor/fan platforms."""
    with patch.object(hass.config_entries, "async_forward_entry_setups", AsyncMock()) as mock:
        yield mock


# ===========================================================================
# async_setup_entry
# ===========================================================================
async def test_setup_fails_fast_when_device_not_found(hass, coordinator_instance):
    """Setup fails fast when device not found."""
    entry = make_entry(hass)
    with patched_setup(coordinator_instance, ble_device=False), pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry)


async def test_setup_fails_when_device_never_becomes_ready(hass, coordinator_instance):
    """Setup fails when device never becomes ready."""
    entry = make_entry(hass)
    coordinator_instance.async_wait_ready = AsyncMock(return_value=False)
    controller = MagicMock(stop=AsyncMock())
    with patched_setup(coordinator_instance, controller), pytest.raises(ConfigEntryNotReady):
        await async_setup_entry(hass, entry)
    controller.stop.assert_awaited_once()


async def test_setup_seeds_only_the_identity(hass, coordinator_instance):
    """The saved readings are months old."""
    entry = make_entry(hass)
    with patched_setup(coordinator_instance) as patches:
        await async_setup_entry(hass, entry)

    (_ble_device, state), kwargs = patches.make_controller.call_args
    assert state == SEED_STATE
    assert callable(kwargs["ble_device_provider"])


async def test_the_controller_looks_up_the_device_on_each_connect(hass, coordinator_instance):
    """The controller asks for the current connectable device, not the one seen at setup."""
    entry = make_entry(hass)
    with patched_setup(coordinator_instance) as patches:
        await async_setup_entry(hass, entry)
    provider = patches.make_controller.call_args.kwargs["ble_device_provider"]

    current = MagicMock()
    with patch.object(integration.bluetooth, "async_ble_device_from_address", return_value=current) as lookup:
        assert provider() is current
    lookup.assert_called_once_with(hass, ADDRESS, connectable=True)


async def test_setup_closes_stale_connections(hass, coordinator_instance):
    """Setup closes stale connections."""
    entry = make_entry(hass)
    with patched_setup(coordinator_instance) as patches:
        await async_setup_entry(hass, entry)
    patches.close_stale.assert_awaited_once_with(ADDRESS)


@pytest.mark.parametrize("error", [RuntimeError("no BlueZ"), TimeoutError])
async def test_setup_survives_failing_stale_connection_cleanup(hass, coordinator_instance, error):
    """Setup survives failing stale connection cleanup."""
    entry = make_entry(hass)
    with patched_setup(coordinator_instance) as patches:
        patches.close_stale.side_effect = error
        assert await async_setup_entry(hass, entry) is True


async def test_stale_connection_cleanup_is_time_limited(hass, coordinator_instance):
    """Stale connection cleanup is time limited."""
    entry = make_entry(hass)
    with patched_setup(coordinator_instance) as patches, patch.object(integration, "STALE_CONNECTION_TIMEOUT", 0.01):
        patches.close_stale.side_effect = _hang
        assert await async_setup_entry(hass, entry) is True


async def test_setup_stores_the_coordinator_and_forwards_platforms(hass, coordinator_instance, platform_setup):
    """Setup stores the coordinator and forwards platforms."""
    entry = make_entry(hass)
    with patched_setup(coordinator_instance):
        assert await async_setup_entry(hass, entry) is True

    assert entry.runtime_data is coordinator_instance
    platform_setup.assert_awaited_once()


# ===========================================================================
# async_unload_entry
# ===========================================================================
def _loaded(hass, controller):
    entry = make_entry(hass)
    entry.runtime_data = MagicMock(controller=controller)
    return entry


async def test_unload_stops_the_controller(hass):
    """Unload stops the controller."""
    controller = MagicMock(stop=AsyncMock())
    entry = _loaded(hass, controller)
    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)):
        assert await async_unload_entry(hass, entry) is True
    controller.stop.assert_awaited_once()


async def test_unload_gives_up_on_a_wedged_stop(hass):
    """Unload gives up on a wedged stop."""
    entry = _loaded(hass, MagicMock(stop=AsyncMock(side_effect=_hang)))
    with (
        patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)),
        patch.object(integration, "STOP_TIMEOUT", 0.01),
    ):
        assert await async_unload_entry(hass, entry) is True


async def test_failed_unload_leaves_the_controller_running(hass):
    """Failed unload leaves the controller running."""
    controller = MagicMock(stop=AsyncMock())
    entry = _loaded(hass, controller)
    with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)):
        assert await async_unload_entry(hass, entry) is False
    controller.stop.assert_not_awaited()
