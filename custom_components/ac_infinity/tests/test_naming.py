"""Entity names come from the device's name, so a renamed device renames its entities."""

import json
from pathlib import Path

import pytest

from homeassistant.helpers import device_registry as dr

from .conftest import ADDRESS, ENTITIES

INTEGRATION = Path(__file__).parents[1]
# Entities named by translation; temperature and humidity take their device class's name
TRANSLATED = [("fan", "fan"), ("sensor", "vpd"), ("sensor", "clock_sync"), ("sensor", "clock_drift")]


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
