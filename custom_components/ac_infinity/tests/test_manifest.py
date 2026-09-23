"""The library is vendored, so Home Assistant installs nothing for this integration."""
from __future__ import annotations

import ast
import json
from pathlib import Path

INTEGRATION = Path(__file__).parents[1]
MANIFEST = json.loads((INTEGRATION / "manifest.json").read_text())


def _absolute_imports(source: str) -> set[str]:
    imports = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module)
    return imports


def test_no_requirements():
    assert MANIFEST["requirements"] == []


def test_nothing_imports_the_installed_library():
    """An old pinned copy may still be installed; only the vendored one is used."""
    for path in INTEGRATION.glob("**/*.py"):
        if "vendor" not in path.parts:
            for module in _absolute_imports(path.read_text()):
                assert module.split(".")[0] != "ac_infinity_ble", path
