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


_DELETE_SCRIPT = f'''
on run argv
	tell application "{endnote_backend.APP_NAME}"
		repeat with p in argv
			set pmid to contents of p
			set ids to find pmid in field "Accession Number"
			repeat with i from 1 to count of ids
				set r to item i of ids
				if (field "Accession Number" of record r) is pmid then delete record r
			end repeat
		end repeat
	end tell
	return "OK"
end run
'''


def _delete_test_records(pmids: list[str]) -> None:
    """Delete records with these exact PMIDs. Refuses unless only the test library is open."""
    names = endnote_backend.open_libraries()
    if names != [TEST_LIBRARY]:
        raise RuntimeError(f"refusing to delete: open libraries are {names}, not [{TEST_LIBRARY!r}]")
    endnote_backend.run_applescript(_DELETE_SCRIPT, args=list(pmids))


def _exact(rows):
    return {r.query: r for r in rows if r.accession == r.query}


@pytest.fixture
def test_library():
    names = endnote_backend.open_libraries()
    if names != [TEST_LIBRARY]:
        pytest.skip(f"open only {TEST_LIBRARY} in EndNote (open now: {names})")
    _delete_test_records(PMIDS)
    yield
    _delete_test_records(PMIDS)


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


def test_delete_helper_refuses_when_other_libraries_are_open(monkeypatch):
    monkeypatch.setattr(endnote_backend, "open_libraries", lambda: ["DoD EML4ALK.enl"])
    monkeypatch.setattr(endnote_backend, "run_applescript", lambda *a, **k: pytest.fail("EndNote must not be called"))
    with pytest.raises(RuntimeError):
        _delete_test_records(["1"])
