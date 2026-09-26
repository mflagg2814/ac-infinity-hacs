"""Library paths upstream's own suite leaves untested."""

# These tests drive and inspect the controller's internals.
# ruff: noqa: SLF001

import asyncio
from dataclasses import replace
import logging
from unittest.mock import AsyncMock, MagicMock, Mock

from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError
from bleak_retry_connector import BleakNotFoundError
import pytest

from custom_components.ac_infinity.vendor.ac_infinity_ble import (
    ACInfinityController,
    DeviceInfo,
    ParameterValidationError,
    PortState,
    telemetry,
)
from custom_components.ac_infinity.vendor.ac_infinity_ble.capabilities import load_kind, recognized_kind
from custom_components.ac_infinity.vendor.ac_infinity_ble.commands import ParameterCodec
from custom_components.ac_infinity.vendor.ac_infinity_ble.const import MANUFACTURER_ID
from custom_components.ac_infinity.vendor.ac_infinity_ble.exceptions import CharacteristicMissingError
from custom_components.ac_infinity.vendor.ac_infinity_ble.models import PortMap
from custom_components.ac_infinity.vendor.ac_infinity_ble.protocol import Protocol, get_mode
from custom_components.ac_infinity.vendor.ac_infinity_ble.routing import resolve_profile
from custom_components.ac_infinity.vendor.ac_infinity_ble.telemetry import frame_length, parse_telemetry
from custom_components.ac_infinity.vendor.ac_infinity_ble.util import get_short

from .upstream.frames import response_frame
from .upstream.test_connection import make_client, prepare_connection
from .upstream.test_device import make_controller
from .upstream.test_families import advertisement

CLOUDCOM = DeviceInfo(type=3, name="Test", version=3)
CONTROLLER_67 = DeviceInfo(type=1, name="Test", version=0)


def telemetry_frame(length: int, width: int = 1) -> bytearray:
    """A telemetry frame of length bytes whose header declares that length."""
    data = bytearray(length)
    data[:4] = b"\x1e\xff\x02\x09"
    data[5 : 5 + width] = (length - 5 - width).to_bytes(width, "big")
    return data


def sensor_record(sensor_id: int, raw: int, accuracy: int = 1) -> bytes:
    """An AI controller's sensor record: id, accuracy flags, and a signed reading."""
    return bytes((sensor_id, accuracy << 5)) + raw.to_bytes(2, "big", signed=True)


# ===========================================================================
# Capabilities, routing and models
# ===========================================================================
def test_no_port_type_has_no_kind():
    """No port type has no kind."""
    assert load_kind(None) is None


@pytest.mark.parametrize(("load_id", "kind"), [(1500, "fan"), (40000, "switch"), (3500, None)])
def test_newer_loads_are_recognized_by_id(load_id, kind):
    """Newer loads are recognized by id, whatever their resistance."""
    assert recognized_kind(5000, load_id) == kind


@pytest.mark.parametrize("port", [True, -1, 256, "1"])
def test_invalid_ports_are_rejected(port):
    """Invalid ports are rejected."""
    with pytest.raises(ValueError, match="Invalid port"):
        resolve_profile(7, 3).port_suffix(port)


def test_single_port_models_have_no_port_1():
    """Single-port models have no port 1."""
    with pytest.raises(ValueError, match="Invalid port"):
        resolve_profile(1, 0).port_suffix(1)


def test_port_map_represents_as_a_dict():
    """Port map represents as a dict."""
    assert repr(PortMap({1: PortState(1)})) == repr({1: PortState(1)})


def test_display_name_adds_the_model():
    """Display name adds the model."""
    assert CONTROLLER_67.display_name == "Controller 67 (Test)"


@pytest.mark.parametrize(("offset", "value"), [(0, -2), (1, 0x0102)])
def test_get_short_is_signed_big_endian(offset, value):
    """Get short is signed big-endian."""
    assert get_short(b"\xff\xfe\x01\x02"[offset * 2 :], 0) == value


# ===========================================================================
# Commands and framing
# ===========================================================================
def test_sensors_take_no_output_commands():
    """Sensors take no output commands."""
    codec = ParameterCodec(CLOUDCOM.profile)
    with pytest.raises(ValueError, match="output controls"):
        codec.encode_set(0, {16: b"\x02"})
    with pytest.raises(ValueError, match="output controls"):
        codec.output_values(on=True, level=5)


@pytest.mark.parametrize("level", [True, 11, -1, "5"])
def test_output_levels_are_validated(level):
    """Output levels are validated."""
    with pytest.raises(ValueError, match="Level"):
        ParameterCodec(CONTROLLER_67.profile).output_values(on=True, level=level)


def test_a_packed_level_needs_the_saved_maximum():
    """A packed level needs the saved maximum."""
    codec = ParameterCodec(resolve_profile(7, 8))
    with pytest.raises(ParameterValidationError, match="Read settings"):
        codec.output_values(on=True, level=5, old=PortState(0))


def test_a_settings_read_rejects_levels_above_10():
    """A settings read rejects levels above 10."""
    codec = ParameterCodec(CONTROLLER_67.profile)
    with pytest.raises(ParameterValidationError, match="level"):
        codec.decode_output({16: b"\x02", 17: b"\x00", 18: b"\x0b"}, PortState(0))


@pytest.mark.parametrize(("mode", "name"), [(1, "OFF"), (4, "TIMER ON"), (99, "")])
def test_mode_names(mode, name):
    """Mode names."""
    assert get_mode(mode) == name


def test_legacy_set_level_on_a_single_port_model():
    """Legacy set level on a single-port model."""
    frame = Protocol().set_level(1, 2, 5, 0, 1)
    assert frame[10:-2] == bytes((16, 1, 2, 18, 1, 5))


@pytest.mark.parametrize(("work_type", "level"), [(3, 5), (2, 11)])
def test_legacy_set_level_validates(work_type, level):
    """Legacy set level validates its work type and level."""
    with pytest.raises(ValueError):
        Protocol().set_level(7, work_type, level, 0, 1)


# ===========================================================================
# Telemetry
# ===========================================================================
@pytest.mark.parametrize(("version", "tmp", "hum"), [(3, 23.4, 56.0), (4, 2.3, 5.6)])
def test_sensor_telemetry(version, tmp, hum):
    """Sensor telemetry, plain before version 4 and packed after."""
    data = telemetry_frame(31)
    data[19:22] = (234).to_bytes(2, "big") + bytes((56,)) if version == 3 else bytes.fromhex("017038")
    state = parse_telemetry(data, replace(CLOUDCOM, version=version))
    assert (state.tmp, state.hum) == (tmp, hum)
    assert frame_length(bytes(2), CLOUDCOM) == 31


def test_vpd_sensor_telemetry_reads_vpd():
    """VPD sensor telemetry reads VPD."""
    data = telemetry_frame(31)
    data[11:13] = (123).to_bytes(2, "big")
    assert parse_telemetry(data, DeviceInfo(type=24, name="Test", version=3)).vpd == 1.23


def test_sensor_telemetry_must_be_31_bytes():
    """Sensor telemetry must be 31 bytes."""
    with pytest.raises(ValueError, match="sensor telemetry length"):
        telemetry._parse_sensor(bytes(30), CLOUDCOM)


def test_home_appliance_telemetry():
    """Home appliance telemetry."""
    data = telemetry_frame(28)
    data[7] = 0x52
    data[12:14] = (2150).to_bytes(2, "big")
    data[23] = 1
    state = parse_telemetry(data, DeviceInfo(type=49, name="Test", version=6))
    assert (state.fan, state.work_type, state.tmp) == (5, 1, 21.5)
    assert state.ports[0].fault


@pytest.mark.parametrize(
    ("state", "length"),
    [
        (DeviceInfo(type=49, name="Test", version=6), 27),
        (CONTROLLER_67, 19),
        (DeviceInfo(type=19, name="Test", version=19), 21),
    ],
    ids=["home", "controller", "ai"],
)
def test_truncated_telemetry_is_rejected(state, length):
    """Truncated telemetry is rejected."""
    with pytest.raises(ValueError):
        parse_telemetry(telemetry_frame(length), state)


def _ai_frame(*sensors: bytes) -> bytearray:
    """An original-header AI controller frame with no ports."""
    data = telemetry_frame(10 + 4 * len(sensors))
    data[7] = len(sensors)
    data[10:] = b"".join(sensors)
    return data


def test_ai_telemetry_readings():
    """A missing Fahrenheit probe clears the temperature; unknown sensors are skipped."""
    ai = DeviceInfo(type=19, name="Test", version=19, tmp=20.0)
    data = _ai_frame(sensor_record(0, -32768), sensor_record(2, 55), sensor_record(5, 1), sensor_record(9, 7))
    state = parse_telemetry(data, ai)
    assert (state.tmp, state.hum) == (None, 55)


def test_ai_telemetry_with_miscounted_records_is_rejected():
    """AI telemetry with miscounted records is rejected."""
    data = _ai_frame(sensor_record(2, 55), sensor_record(3, 1), sensor_record(5, 1))
    data[7] = 4
    with pytest.raises(ValueError, match="counts"):
        parse_telemetry(data, DeviceInfo(type=19, name="Test", version=19))


def test_duplicate_ai_ports_are_rejected():
    """Duplicate AI ports are rejected."""
    data = telemetry_frame(48, width=2)
    data[12] = 2
    data[15] = data[26] = 2
    with pytest.raises(ValueError, match="Duplicate"):
        parse_telemetry(data, DeviceInfo(type=51, name="Test", version=20))


# ===========================================================================
# Controller state
# ===========================================================================
async def test_state_from_an_advertisement():
    """State from an advertisement."""
    ad = AdvertisementData(None, {MANUFACTURER_ID: bytes(advertisement(7))}, {}, [], None, -60, ())
    controller = ACInfinityController(MagicMock(), advertisement_data=ad)
    assert (controller.state.type, controller.name, controller.rssi) == (7, "E-ABCDE", -60)


async def test_state_or_advertisement_is_required():
    """State or advertisement is required."""
    with pytest.raises(ValueError, match="Must provide"):
        ACInfinityController(MagicMock())


async def test_a_short_advertisement_keeps_the_readings():
    """A short advertisement keeps the readings."""
    controller = make_controller()
    controller._state = replace(controller.state, tmp=21.0)
    ad = AdvertisementData(None, {MANUFACTURER_ID: bytes(advertisement(7)[:17])}, {}, [], None, -60, ())
    controller.set_ble_device_and_advertisement_data(MagicMock(), ad)
    assert controller.temperature == 21.0


async def test_the_sequence_wraps():
    """The sequence wraps."""
    controller = make_controller()
    controller._sequence = 65535
    assert controller.sequence == 1


async def test_sensors_skip_the_settings_read():
    """Sensors skip the settings read."""
    controller = make_controller()
    controller._state = CLOUDCOM
    controller._send_command = AsyncMock()
    await controller.update()
    await controller.refresh_telemetry()
    controller._send_command.assert_not_awaited()


async def test_a_port_that_changes_during_a_read_is_rejected():
    """A port that changes during a read is rejected."""
    controller = make_controller()
    controller._state = replace(controller.state, ports={1: PortState(1, kind="fan")})

    async def unplug(_command):
        controller._state = replace(controller.state, ports={1: PortState(1, connected=False)})
        return response_frame(bytes.fromhex("100102 110100 120105 ff01"), command=1)

    controller._send_command = unplug
    with pytest.raises(ValueError, match="Port changed"):
        await controller.update(1)


async def test_commands_to_a_disconnected_port_are_rejected():
    """Commands to a disconnected port are rejected."""
    with pytest.raises(ValueError, match="not connected"):
        await make_controller().set_output(1, on=True)


async def test_unregistered_callbacks_stop_hearing():
    """Unregistered callbacks stop hearing."""
    controller = make_controller()
    heard = Mock()
    controller.register_callback(heard)()
    controller._fire_callbacks(None)
    heard.assert_not_called()


# ===========================================================================
# Connecting
# ===========================================================================
async def test_a_stopped_controller_does_not_connect():
    """A stopped controller does not connect."""
    controller = make_controller()
    controller._stopped = True
    with pytest.raises(BleakError, match="stopped"):
        await controller._ensure_connected()


async def test_stopping_while_waiting_to_connect(monkeypatch):
    """Stopping while waiting to connect."""
    controller, establish = prepare_connection(monkeypatch, make_client())
    async with controller._connect_lock:
        waiting = asyncio.ensure_future(controller._ensure_connected())
        await asyncio.sleep(0)
        controller._stopped = True
    with pytest.raises(BleakError, match="stopped"):
        await waiting
    establish.assert_not_awaited()


async def test_a_connection_made_while_waiting_is_reused(monkeypatch):
    """A connection made while waiting is reused."""
    client = make_client()
    controller, establish = prepare_connection(monkeypatch)
    async with controller._connect_lock:
        waiting = asyncio.ensure_future(controller._ensure_connected())
        await asyncio.sleep(0)
        controller._client = client
    await waiting
    establish.assert_not_awaited()
    await controller.stop()


async def test_no_connectable_device(monkeypatch):
    """No connectable device."""
    controller, establish = prepare_connection(monkeypatch)
    controller._ble_device_provider = Mock(return_value=None)
    with pytest.raises(BleakError, match="No connectable"):
        await controller._ensure_connected()
    establish.assert_not_awaited()


async def test_stopping_during_connect_releases_the_client(monkeypatch):
    """Stopping during connect releases the client."""
    client = make_client()
    controller, establish = prepare_connection(monkeypatch)

    async def connect_then_stop(*_args, **_kwargs):
        controller._stopped = True
        return client

    establish.side_effect = connect_then_stop
    with pytest.raises(BleakError, match="stopped while connecting"):
        await controller._ensure_connected()
    client.disconnect.assert_awaited_once()


@pytest.mark.parametrize("error", [BleakError, NotImplementedError])
async def test_a_cache_that_cant_be_cleared(monkeypatch, error):
    """A cache that can't be cleared."""
    client = make_client()
    client.services.get_characteristic.side_effect = lambda _uuid: None
    client.clear_cache.side_effect = error
    controller, _ = prepare_connection(monkeypatch, client)
    with pytest.raises(CharacteristicMissingError):
        await controller._ensure_connected()


# ===========================================================================
# Device information
# ===========================================================================
def _information_client(read: AsyncMock) -> MagicMock:
    client = make_client()
    lookup = client.services.get_characteristic.side_effect
    client.services.get_characteristic.side_effect = lambda uuid: uuid if uuid.startswith("00002a2") else lookup(uuid)
    client.read_gatt_char = read
    return client


async def test_a_failed_read_that_drops_the_connection(monkeypatch):
    """A failed read that drops the connection."""

    async def drop(_characteristic):
        client.is_connected = False
        raise BleakError("gone")

    client = _information_client(AsyncMock(side_effect=drop))
    controller, _ = prepare_connection(monkeypatch, client)
    with pytest.raises(BleakError, match="gone"):
        await controller._ensure_connected()


async def test_unprintable_revisions_are_skipped(monkeypatch):
    """Unprintable revisions are skipped, and still count as read."""
    client = _information_client(AsyncMock(return_value=b"\xff"))
    controller, _ = prepare_connection(monkeypatch, client)
    await controller._ensure_connected()
    assert controller.state.device_information.firmware_revision is None
    assert controller._device_information_read
    await controller.stop()


async def test_disconnecting_while_reading_revisions(monkeypatch):
    """Disconnecting while reading revisions."""

    async def read_then_drop(_characteristic):
        client.is_connected = False
        return b"1.0"

    client = _information_client(AsyncMock(side_effect=read_then_drop))
    controller, _ = prepare_connection(monkeypatch, client)
    with pytest.raises(BleakError, match="reading device information"):
        await controller._ensure_connected()


# ===========================================================================
# Notifications and disconnects
# ===========================================================================
async def test_invalid_telemetry_is_ignored(caplog):
    """Invalid telemetry is ignored."""
    controller = make_controller()
    data = telemetry_frame(19)
    with caplog.at_level(logging.DEBUG):
        controller._notification_handler(0, data)
    assert "Ignoring telemetry" in caplog.text
    assert not controller._telemetry_buffer


@pytest.mark.parametrize("expected", [True, False])
async def test_a_disconnect_without_a_pending_command(caplog, expected):
    """A disconnect without a pending command logs by whether it was expected."""
    controller = make_controller()
    client = controller._client
    controller._expected_disconnect = expected
    with caplog.at_level(logging.DEBUG):
        controller._disconnected(client)
    assert controller._client is None
    assert ("unexpectedly" in caplog.text) is not expected


async def test_a_disconnect_after_the_reply():
    """A disconnect after the reply leaves the result alone."""
    controller = make_controller()
    controller._notify_future = controller.loop.create_future()
    controller._notify_future.set_result(b"")
    controller._disconnected(controller._client)
    assert controller._notify_future.result() == b""


async def test_one_timed_disconnect_at_a_time():
    """One timed disconnect at a time."""
    controller = make_controller()
    release = asyncio.Event()
    controller._execute_timed_disconnect = release.wait
    controller._disconnect()
    task = controller._disconnect_task
    controller._disconnect()
    assert controller._disconnect_task is task
    release.set()
    await task


# ===========================================================================
# Command failures
# ===========================================================================
@pytest.mark.parametrize(
    ("error", "level"),
    [(BleakNotFoundError("gone"), logging.ERROR), (CharacteristicMissingError("no chars"), logging.DEBUG)],
)
async def test_command_failures_are_logged_and_raised(caplog, error, level):
    """Command failures are logged and raised."""
    controller = make_controller()
    controller._client = None
    controller._send_command_locked = AsyncMock(side_effect=error)
    with caplog.at_level(logging.DEBUG), pytest.raises(type(error)):
        await controller._send_command(b"")
    assert any(record.levelno == level for record in caplog.records)


@pytest.mark.parametrize("missing", ["_read_char", "_write_char"])
async def test_commands_need_both_characteristics(missing):
    """Commands need both characteristics."""
    controller = make_controller()
    setattr(controller, missing, None)
    with pytest.raises(CharacteristicMissingError):
        await controller._execute_command_locked(b"")
