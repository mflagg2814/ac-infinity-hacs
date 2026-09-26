"""Entity names come from the device's name, so a renamed device renames its entities."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ac_infinity.const import DOMAIN
from homeassistant.const import CONF_ADDRESS, CONF_SERVICE_DATA
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import ADDRESS, SETUP_SEED

INTEGRATION = Path(__file__).parents[1]
# Entities named by translation; temperature and humidity take their device class's name
TRANSLATED = [("fan", "fan"), ("sensor", "vpd"), ("sensor", "clock_sync"), ("sensor", "clock_drift")]
# As registered in production: (unique id suffix, entity id, friendly name)
ENTITIES = [
    ("fan", "fan.blowymatron_fan", "Blowymatron Fan"),
    ("tmp", "sensor.blowymatron_temperature", "Blowymatron Temperature"),
    ("hum", "sensor.blowymatron_humidity", "Blowymatron Humidity"),
    ("clock_sync", "sensor.last_blowymatron_clock_sync", "Blowymatron Last Clock Sync"),
    ("clock_drift", "sensor.last_blowymatron_clock_drift", "Blowymatron Last Clock Drift"),
]


@pytest.fixture
async def loaded_entry(hass, coordinator, seeded_device):
    """The real platforms, set up on a device the user renamed to Blowymatron."""
    hass.config.components.add("bluetooth_adapters")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=ADDRESS,
        title="A-0ECGN",
        data={CONF_ADDRESS: ADDRESS, CONF_SERVICE_DATA: SETUP_SEED},
    )
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, ADDRESS)},
        name="A-0ECGN",
    )
    dr.async_get(hass).async_update_device(device.id, name_by_user="Blowymatron")
    for suffix, entity_id, _name in ENTITIES:
        domain, object_id = entity_id.split(".")
        er.async_get(hass).async_get_or_create(
            domain,
            DOMAIN,
            f"{ADDRESS}_{suffix}",
            suggested_object_id=object_id,
            config_entry=entry,
            device_id=device.id,
        )
    coordinator.async_start = MagicMock(return_value=MagicMock())
    coordinator.async_wait_ready = AsyncMock(return_value=True)
    with (
        patch(
            "custom_components.ac_infinity.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ),
        patch("custom_components.ac_infinity.close_stale_connections_by_address", AsyncMock()),
        patch("custom_components.ac_infinity.ACInfinityController", return_value=seeded_device),
        patch(
            "custom_components.ac_infinity.ACInfinityDataUpdateCoordinator",
            return_value=coordinator,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.mark.parametrize(("_suffix", "entity_id", "friendly_name"), ENTITIES)
async def test_entities_keep_their_ids_and_take_the_device_name(hass, loaded_entry, _suffix, entity_id, friendly_name):
    """Entities keep their ids and take the device name."""
    assert hass.states.get(entity_id).attributes["friendly_name"] == friendly_name


@pytest.mark.parametrize(
    ("entity_id", "friendly_name"),
    [
        ("fan.blowymatron_fan", "Whirlwind Fan"),
        ("sensor.last_blowymatron_clock_sync", "Whirlwind Last Clock Sync"),
        ("sensor.last_blowymatron_clock_drift", "Whirlwind Last Clock Drift"),
    ],
)
async def test_renaming_the_device_renames_its_entities(hass, loaded_entry, entity_id, friendly_name):
    """Renaming the device renames its entities."""
    registry = dr.async_get(hass)
    device = registry.async_get_device(connections={(dr.CONNECTION_BLUETOOTH, ADDRESS)})
    registry.async_update_device(device.id, name_by_user="Whirlwind")
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["friendly_name"] == friendly_name


@pytest.mark.parametrize("file", ["strings.json", "translations/en.json"])
def test_every_translation_key_has_a_name(file):
    """Every translation key has a name."""
    entity = json.loads((INTEGRATION / file).read_text())["entity"]
    for platform, key in TRANSLATED:
        assert entity[platform][key]["name"], (platform, key)
