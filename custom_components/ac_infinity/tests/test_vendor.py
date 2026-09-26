"""Our additions to the vendored library, and its handling of frames captured from the A-0ECGN."""

# These tests drive and inspect the controller's and coordinator's internals; see README.md.
# ruff: noqa: SLF001

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from bleak.exc import BleakError
import pytest

from custom_components.ac_infinity.vendor.ac_infinity_ble import CommandRejectedError, ParameterValidationError
from custom_components.ac_infinity.vendor.ac_infinity_ble.clock import decode_clock, encode_clock
from custom_components.ac_infinity.vendor.ac_infinity_ble.const import (
    POSSIBLE_READ_CHARACTERISTIC_UUIDS,
    POSSIBLE_WRITE_CHARACTERISTIC_UUIDS,
)
from custom_components.ac_infinity.vendor.ac_infinity_ble.exceptions import CharacteristicMissingError

from .conftest import MODEL_REPLY, ack, reply

# A Wednesday
NOW = datetime(2026, 9, 23, 10, 30, 5)
NOW_PAYLOAD = bytes.fromhex("1a0917040a1e05")
DEVICE_MODULE = "custom_components.ac_infinity.vendor.ac_infinity_ble.device"


@pytest.fixture
def controller(seeded_device):
    """The seeded controller, already connected and acknowledging every command."""
    seeded_device._ensure_connected = AsyncMock()
    seeded_device._send_command = AsyncMock(side_effect=ack)
    return seeded_device


def _sent(controller) -> bytes:
    return controller._send_command.await_args.args[0]


# ===========================================================================
# Clock encoding
# ===========================================================================
def test_encodes_year_of_century_and_fields():
    """Encodes year of century and fields."""
    assert encode_clock(NOW) == NOW_PAYLOAD


@pytest.mark.parametrize(("day", "weekday"), [(20, 1), (21, 2), (23, 4), (26, 7)])
def test_weekday_runs_sunday_1_to_saturday_7(day, weekday):
    """Weekday runs sunday 1 to saturday 7."""
    assert encode_clock(NOW.replace(day=day))[3] == weekday


def test_decodes_with_the_reference_century():
    """Decodes with the reference century."""
    assert decode_clock(NOW_PAYLOAD, datetime(2026, 1, 1)) == NOW


@pytest.mark.parametrize("value", [NOW_PAYLOAD[:6], bytes.fromhex("1a0d17040a1e05"), bytes.fromhex("1a021e040a1e05")])
def test_rejects_malformed_clock_values(value):
    """Rejects malformed clock values."""
    with pytest.raises(ParameterValidationError):
        decode_clock(value, NOW)


# ===========================================================================
# set_clock and read_clock
# ===========================================================================
async def test_set_clock_writes_parameter_1(controller):
    """Set clock writes parameter 1."""
    await controller.set_clock(lambda: NOW)
    command = _sent(controller)
    assert command[8:10] == b"\x00\x03"
    assert command[10:-2] == b"\x01\x07" + NOW_PAYLOAD


async def test_set_clock_reads_the_time_after_connecting(controller):
    """Set clock reads the time after connecting."""
    order = []
    controller._ensure_connected.side_effect = lambda: order.append("connect")

    def now() -> datetime:
        order.append("now")
        return NOW

    await controller.set_clock(now)
    assert order == ["connect", "now"]


async def test_set_clock_raises_when_rejected(controller):
    """Set clock raises when rejected."""
    controller._send_command.side_effect = lambda request: reply(request, b"\x01\x01")
    with pytest.raises(CommandRejectedError):
        await controller.set_clock(lambda: NOW)


async def test_read_clock(controller):
    """Read clock."""
    controller._send_command.side_effect = lambda request: reply(request, b"\x01\x07" + NOW_PAYLOAD)
    assert await controller.read_clock(datetime(2026, 1, 1)) == NOW
    command = _sent(controller)
    assert (command[8:10], command[10:-2]) == (b"\x00\x01", b"\x01")


async def test_read_clock_without_a_clock_in_the_reply(controller):
    """Read clock without a clock in the reply."""
    controller._send_command.side_effect = lambda request: reply(request, b"")
    with pytest.raises(ParameterValidationError):
        await controller.read_clock(NOW)


# ===========================================================================
# disconnect
# ===========================================================================
async def test_disconnect_is_not_final(controller):
    """Disconnect is not final."""
    controller._execute_disconnect = AsyncMock()
    await controller.disconnect()
    controller._execute_disconnect.assert_awaited_once()
    assert controller._stopped is False


# ===========================================================================
# Captured frames
# ===========================================================================
async def test_parses_the_captured_settings_reply(controller):
    """Parses the captured settings reply."""
    controller._send_command.side_effect = lambda _request: MODEL_REPLY
    await controller.update()
    assert _sent(controller)[10:-2] == bytes(range(0x10, 0x18))
    state = controller.state
    assert (state.work_type, state.level_off, state.level_on) == (2, 0, 8)


async def test_telemetry_never_completes_a_pending_command(controller):
    """Telemetry never completes a pending command."""
    controller._notify_future = controller.loop.create_future()
    controller._pending_sequence = int.from_bytes(MODEL_REPLY[4:6], "big")
    controller._pending_command = 1
    telemetry = bytes.fromhex("1eff0209000c") + bytes(12)

    controller._notification_handler(0, bytearray(telemetry))
    assert not controller._notify_future.done()

    controller._notification_handler(0, bytearray(MODEL_REPLY))
    assert controller._notify_future.result() == MODEL_REPLY


# ===========================================================================
# Connecting
# ===========================================================================
READ_UUID, WRITE_UUID = POSSIBLE_READ_CHARACTERISTIC_UUIDS[0], POSSIBLE_WRITE_CHARACTERISTIC_UUIDS[0]


@pytest.fixture
def client() -> MagicMock:
    """A connected GATT client exposing the controller's read and write characteristics."""
    client = MagicMock(is_connected=True)
    characteristics = {READ_UUID: MagicMock(uuid=READ_UUID), WRITE_UUID: MagicMock(uuid=WRITE_UUID)}
    client.services.get_characteristic.side_effect = characteristics.get
    client.clear_cache = AsyncMock(return_value=True)
    client.start_notify = AsyncMock()
    client.stop_notify = AsyncMock()
    client.disconnect = AsyncMock()
    return client


@pytest.fixture
def connecting(seeded_device, client):
    """The seeded controller, whose connection attempts return client."""
    with patch(f"{DEVICE_MODULE}.establish_connection", AsyncMock(return_value=client)):
        yield seeded_device
    if seeded_device._disconnect_timer:
        seeded_device._disconnect_timer.cancel()


def _without_characteristics(client: MagicMock) -> None:
    client.services.get_characteristic.side_effect = lambda _uuid: None


async def test_connecting_subscribes_to_the_read_characteristic(connecting, client):
    """Connecting subscribes to the read characteristic."""
    await connecting._ensure_connected()
    assert connecting._client is client
    assert client.start_notify.await_args.args[0].uuid == READ_UUID
    assert connecting._disconnect_timer is not None


async def test_missing_characteristics_clear_the_cache_and_ask_for_a_reconnect(connecting, client):
    """Missing characteristics clear the cache and ask for a reconnect."""
    _without_characteristics(client)
    with pytest.raises(BleakError, match="reconnect required"):
        await connecting._ensure_connected()
    client.clear_cache.assert_awaited_once()
    client.disconnect.assert_awaited_once()
    assert connecting._client is None


async def test_characteristics_still_missing_after_a_cache_clear(connecting, client):
    """Characteristics still missing after a cache clear."""
    _without_characteristics(client)
    connecting._cache_recovery_attempted = True
    with pytest.raises(CharacteristicMissingError):
        await connecting._ensure_connected()
    client.clear_cache.assert_not_awaited()
    client.disconnect.assert_awaited_once()


async def test_characteristics_missing_when_the_cache_cannot_be_cleared(connecting, client):
    """Characteristics missing when the cache cannot be cleared."""
    _without_characteristics(client)
    client.clear_cache.return_value = False
    with pytest.raises(CharacteristicMissingError):
        await connecting._ensure_connected()
    client.disconnect.assert_awaited_once()


async def test_disconnecting_while_subscribing(connecting, client):
    """Disconnecting while subscribing."""

    def drop(*_args) -> None:
        client.is_connected = False

    client.start_notify.side_effect = drop
    with pytest.raises(BleakError, match="subscribing"):
        await connecting._ensure_connected()
    assert connecting._client is None
    client.disconnect.assert_awaited_once()


# ===========================================================================
# Assembling responses
# ===========================================================================
@pytest.fixture
def pending(controller):
    """The controller, waiting for its reply to the captured settings read."""
    controller._notify_future = controller.loop.create_future()
    controller._pending_sequence = int.from_bytes(MODEL_REPLY[4:6], "big")
    controller._pending_command = 1
    return controller


@pytest.mark.parametrize("split", [1, 8, 20])
def test_a_split_response_completes_once_whole(pending, split):
    """A split response completes once whole."""
    pending._notification_handler(0, bytearray(MODEL_REPLY[:split]))
    assert not pending._notify_future.done()
    pending._notification_handler(0, bytearray(MODEL_REPLY[split:]))
    assert pending._notify_future.result() == MODEL_REPLY


def test_a_fragment_without_a_header_is_dropped(pending):
    """A fragment without a header is dropped."""
    pending._notification_handler(0, bytearray(b"\x01\x02"))
    assert not pending._response_buffer
    pending._notification_handler(0, bytearray(MODEL_REPLY))
    assert pending._notify_future.result() == MODEL_REPLY
