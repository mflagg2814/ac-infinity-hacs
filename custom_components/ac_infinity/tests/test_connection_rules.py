"""Connecting controller methods run only inside operations passed to async_run.

async_run disconnects afterward; a held connection stops the device's sensor data.
"""
from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

INTEGRATION = Path(__file__).parents[1]
CONNECTING_METHODS = frozenset(
    {
        "update",
        "refresh_telemetry",
        "set_output",
        "set_speed",
        "turn_on",
        "turn_off",
        "set_clock",
        "read_clock",
        "_send_command",
    }
)
CONTROLLER_NAMES = frozenset({"controller", "device", "_device", "_controller"})
# Runs before any coordinator exists and stops the controller itself
EXEMPT = frozenset({"config_flow.py"})


def _receiver_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _called_names(node: ast.AST) -> set[str | None]:
    return {
        _receiver_name(call.func) for call in ast.walk(node) if isinstance(call, ast.Call)
    }


def _operation_nodes(tree: ast.Module) -> set[int]:
    """Ids of every node inside a function passed to async_run, or called from one."""
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    pending = [
        _receiver_name(arg)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _receiver_name(node.func) == "async_run"
        for arg in node.args
    ]
    operations: set[str] = set()
    while pending:
        name = pending.pop()
        if name in functions and name not in operations:
            operations.add(name)
            pending.extend(_called_names(functions[name]))
    return {id(inner) for name in operations for inner in ast.walk(functions[name])}


def direct_connecting_calls(source: str) -> Iterator[int]:
    """Line numbers of calls like `controller.update()` outside an async_run operation."""
    tree = ast.parse(source)
    inside_operations = _operation_nodes(tree)
    for node in ast.walk(tree):
        if (
            id(node) not in inside_operations
            and isinstance(node, ast.Call)
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


OPERATION = "async def op(self):\n    await self._controller.set_clock(now)\n"


def test_calls_inside_an_operation_passed_to_async_run_are_allowed():
    source = OPERATION + "async def f(self, async_run):\n    await async_run(self.op)\n"
    assert list(direct_connecting_calls(source)) == []


def test_helpers_called_from_an_operation_are_allowed():
    source = (
        "async def helper(self):\n    await self._controller.read_clock(now)\n"
        "async def op(self):\n    await self.helper()\n"
        "async def f(self, async_run):\n    await async_run(self.op)\n"
    )
    assert list(direct_connecting_calls(source)) == []


def test_calls_inside_a_function_never_passed_to_async_run_are_flagged():
    assert list(direct_connecting_calls(OPERATION)) == [2]


def test_exempt_config_flow_still_disconnects():
    assert "await controller.stop()" in (INTEGRATION / "config_flow.py").read_text()
