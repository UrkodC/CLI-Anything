"""Switch for tests that open windows in the SnapGene app."""
from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "gui: opens a file in the SnapGene app (set SNAPGENE_GUI=1)")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.get_closest_marker("gui") and os.environ.get("SNAPGENE_GUI") != "1":
            item.add_marker(pytest.mark.skip(reason="set SNAPGENE_GUI=1 to run"))
