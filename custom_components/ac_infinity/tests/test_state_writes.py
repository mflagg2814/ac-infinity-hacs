"""Each update writes every entity's state once."""

# These tests drive and inspect the coordinator's internals; see README.md.
# ruff: noqa: SLF001

from unittest.mock import patch

from custom_components.ac_infinity.vendor.ac_infinity_ble.const import MANUFACTURER_ID
from homeassistant.components.bluetooth import BluetoothChange
from homeassistant.helpers.entity import Entity

from .conftest import ENTITIES, SENSOR_PAYLOAD, service_info


async def test_a_sensor_advertisement_writes_each_entity_once(hass, loaded_entry):
    """A sensor advertisement writes each entity once."""
    coordinator = loaded_entry.runtime_data
    with patch.object(Entity, "async_write_ha_state", autospec=True) as write:
        coordinator._async_handle_bluetooth_event(
            service_info({MANUFACTURER_ID: SENSOR_PAYLOAD}), BluetoothChange.ADVERTISEMENT
        )
    written = sorted(call.args[0].entity_id for call in write.call_args_list)
    assert written == sorted(entity_id for _suffix, entity_id, _name in ENTITIES)
