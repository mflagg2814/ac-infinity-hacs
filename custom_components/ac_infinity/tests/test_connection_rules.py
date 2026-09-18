"""No code awaits a connecting controller method outside coordinator.async_run.

async_run disconnects afterward; a held connection stops the device's sensor data.
"""
from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

INTEGRATION = Path(__file__).parents[1]
CONNECTING_METHODS = frozenset({"update", "set_speed", "turn_on", "turn_off", "_send_command"})
CONTROLLER_NAMES = frozenset({"controller", "device", "_device"})
# Runs before any coordinator exists and stops the controller itself
EXEMPT = frozenset({"config_flow.py"})


def _receiver_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def direct_connecting_calls(source: str) -> Iterator[int]:
    """Line numbers of calls like `controller.update()` or `self._device.set_speed(x)`."""
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in CONNECTING_METHODS
            and _receiver_name(node.func.value) in CONTROLLER_NAMES
        ):
            yield node.lineno


@pytest.mark.parametrize(
    "path",
    [p for p in sorted(INTEGRATION.glob("*.py")) if p.name not in EXEMPT],
    ids=lambda p: p.name,
)
def test_no_direct_connecting_calls(path):
    assert list(direct_connecting_calls(path.read_text())) == []


@pytest.mark.parametrize(
    "source",
    [
        "async def f(self):\n    await self._device.set_speed(1)\n",
        "async def f(self):\n    await self.controller.update()\n",
        "async def f(controller):\n    await controller.turn_off()\n",
    ],
)
def test_detects_direct_calls(source):
    assert list(direct_connecting_calls(source)) == [2]


def test_passing_a_method_to_async_run_is_allowed():
    source = "async def f(self):\n    await self.coordinator.async_run(self._device.turn_off)\n"
    assert list(direct_connecting_calls(source)) == []


def test_exempt_config_flow_still_disconnects():
    assert "await controller.stop()" in (INTEGRATION / "config_flow.py").read_text()
