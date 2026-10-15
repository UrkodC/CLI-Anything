"""Switches for tests that need the network or the real EndNote."""
from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "network: needs PubMed over the internet (set PUBMED_NETWORK=1)")
    config.addinivalue_line("markers", "endnote: needs EndNote 2025 with only EndNoteCLI_Test.enl open (set ENDNOTE_E2E=1)")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.get_closest_marker("network") and os.environ.get("PUBMED_NETWORK") != "1":
            item.add_marker(pytest.mark.skip(reason="set PUBMED_NETWORK=1 to run"))
        if item.get_closest_marker("endnote") and os.environ.get("ENDNOTE_E2E") != "1":
            item.add_marker(pytest.mark.skip(reason="set ENDNOTE_E2E=1 to run"))
