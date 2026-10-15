"""End-to-end tests against the real EndNote 2025, the real PubMed, and the installed command.

EndNote tests need ENDNOTE_E2E=1 and EndNote open with ONLY EndNoteCLI_Test.enl.
Network tests need PUBMED_NETWORK=1.
"""
from __future__ import annotations

import pytest

from cli_anything.endnote.core import enxml, pubmed
from cli_anything.endnote.utils import endnote_backend

TEST_LIBRARY = "EndNoteCLI_Test.enl"
PMIDS = ["17625570", "15033360", "41528114"]   # Soda 2007, Güthe 2004 (non-ASCII), Siegel 2026


def _exact(rows):
    return {r.query: r for r in rows if r.accession == r.query}


@pytest.fixture
def test_library():
    names = endnote_backend.open_libraries()
    if names != [TEST_LIBRARY]:
        pytest.skip(f"open only {TEST_LIBRARY} in EndNote (open now: {names})")
    endnote_backend.delete_pmids(PMIDS)
    yield
    endnote_backend.delete_pmids(PMIDS)


@pytest.mark.endnote
@pytest.mark.network
def test_import_round_trip(test_library):
    records, missing = pubmed.fetch(PMIDS)
    assert missing == []
    endnote_backend.import_xml(enxml.to_endnote_xml(records))
    found = _exact(endnote_backend.lookup(PMIDS))
    assert set(found) == set(PMIDS)
    assert found["17625570"].citation.startswith("{Soda, 2007 #")
    assert found["15033360"].citation.startswith("{Güthe, 2004 #")
    assert found["41528114"].title == "Cancer statistics, 2026"
