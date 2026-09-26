"""Upstream's library tests need no Home Assistant instance."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations():
    """Override the parent suite's fixture, which starts Home Assistant."""
