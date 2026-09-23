"""Our additions to the vendored library, and its handling of frames captured from the A-0ECGN."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from custom_components.ac_infinity.vendor.ac_infinity_ble import (
    CommandRejectedError,
    ParameterValidationError,
)
from custom_components.ac_infinity.vendor.ac_infinity_ble.clock import (
    decode_clock,
    encode_clock,
)

from .conftest import MODEL_REPLY, ack, reply

# A Wednesday
NOW = datetime(2026, 9, 23, 10, 30, 5)
NOW_PAYLOAD = bytes.fromhex("1a0917040a1e05")


@pytest.fixture
def controller(seeded_device):
    seeded_device._ensure_connected = AsyncMock()
    seeded_device._send_command = AsyncMock(side_effect=ack)
    return seeded_device


def _sent(controller) -> bytes:
    return controller._send_command.await_args.args[0]


# ===========================================================================
# Clock encoding
# ===========================================================================
def test_encodes_year_of_century_and_fields():
    assert encode_clock(NOW) == NOW_PAYLOAD


@pytest.mark.parametrize(
    ("day", "weekday"), [(20, 1), (21, 2), (23, 4), (26, 7)]
)
def test_weekday_runs_sunday_1_to_saturday_7(day, weekday):
    assert encode_clock(NOW.replace(day=day))[3] == weekday


def test_decodes_with_the_reference_century():
    assert decode_clock(NOW_PAYLOAD, datetime(2026, 1, 1)) == NOW


@pytest.mark.parametrize(
    "value", [NOW_PAYLOAD[:6], bytes.fromhex("1a0d17040a1e05"), bytes.fromhex("1a021e040a1e05")]
)
def test_rejects_malformed_clock_values(value):
    with pytest.raises(ParameterValidationError):
        decode_clock(value, NOW)


# ===========================================================================
# set_clock and read_clock
# ===========================================================================
async def test_set_clock_writes_parameter_1(controller):
    await controller.set_clock(lambda: NOW)
    command = _sent(controller)
    assert command[8:10] == b"\x00\x03"
    assert command[10:-2] == b"\x01\x07" + NOW_PAYLOAD


async def test_set_clock_reads_the_time_after_connecting(controller):
    order = []
    controller._ensure_connected.side_effect = lambda: order.append("connect")

    def now() -> datetime:
        order.append("now")
        return NOW

    await controller.set_clock(now)
    assert order == ["connect", "now"]


async def test_set_clock_raises_when_rejected(controller):
    controller._send_command.side_effect = lambda request: reply(request, b"\x01\x01")
    with pytest.raises(CommandRejectedError):
        await controller.set_clock(lambda: NOW)


async def test_read_clock(controller):
    controller._send_command.side_effect = lambda request: reply(
        request, b"\x01\x07" + NOW_PAYLOAD
    )
    assert await controller.read_clock(datetime(2026, 1, 1)) == NOW
    command = _sent(controller)
    assert (command[8:10], command[10:-2]) == (b"\x00\x01", b"\x01")


async def test_read_clock_without_a_clock_in_the_reply(controller):
    controller._send_command.side_effect = lambda request: reply(request, b"")
    with pytest.raises(ParameterValidationError):
        await controller.read_clock(NOW)


# ===========================================================================
# disconnect
# ===========================================================================
async def test_disconnect_is_not_final(controller):
    controller._execute_disconnect = AsyncMock()
    await controller.disconnect()
    controller._execute_disconnect.assert_awaited_once()
    assert controller._stopped is False


# ===========================================================================
# Captured frames
# ===========================================================================
async def test_parses_the_captured_settings_reply(controller):
    controller._send_command.side_effect = lambda _request: MODEL_REPLY
    await controller.update()
    assert _sent(controller)[10:-2] == bytes(range(0x10, 0x18))
    state = controller.state
    assert (state.work_type, state.level_off, state.level_on) == (2, 0, 8)


async def test_telemetry_never_completes_a_pending_command(controller):
    controller._notify_future = controller.loop.create_future()
    controller._pending_sequence = int.from_bytes(MODEL_REPLY[4:6], "big")
    controller._pending_command = 1
    telemetry = bytes.fromhex("1eff0209000c") + bytes(12)

    controller._notification_handler(0, bytearray(telemetry))
    assert not controller._notify_future.done()

    controller._notification_handler(0, bytearray(MODEL_REPLY))
    assert controller._notify_future.result() == MODEL_REPLY
