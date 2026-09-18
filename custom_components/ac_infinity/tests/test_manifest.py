"""Manifest requirements: Home Assistant must see them as installed, or it reinstalls every boot."""
from __future__ import annotations

from importlib.metadata import distribution
import json
from pathlib import Path

from homeassistant.util.package import is_installed, parse_requirement_safe

MANIFEST = json.loads((Path(__file__).parents[1] / "manifest.json").read_text())


def test_requirements_are_recognized_as_installed():
    for requirement in MANIFEST["requirements"]:
        assert is_installed(requirement), requirement


def test_pinned_commit_matches_installed_library():
    (requirement,) = MANIFEST["requirements"]
    name = parse_requirement_safe(requirement).name
    commit = json.loads(distribution(name).read_text("direct_url.json"))["vcs_info"]["commit_id"]
    assert f"@{commit}#" in requirement
