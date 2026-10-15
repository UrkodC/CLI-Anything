"""End-to-end tests against the real EndNote 2025, the real PubMed, and the installed command.

EndNote tests need ENDNOTE_E2E=1 and EndNote open with ONLY EndNoteCLI_Test.enl.
Network tests need PUBMED_NETWORK=1.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cli_anything.endnote.core import enxml, library, pubmed
from cli_anything.endnote.tests._helpers import make_docx, visible_paragraphs
from cli_anything.endnote.utils import endnote_backend

TEST_LIBRARY = "EndNoteCLI_Test.enl"
PMIDS = ["17625570", "15033360", "41528114"]   # Soda 2007, Güthe 2004 (non-ASCII), Siegel 2026


_DELETE_SCRIPT = f'''
on run argv
	tell application "{endnote_backend.APP_NAME}"
		if (count of documents) is not 1 or name of document 1 is not (item 1 of argv) then error "endnote-cli: library changed" number 1001
		repeat with p in rest of argv
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
    endnote_backend.run_applescript(_DELETE_SCRIPT, args=[TEST_LIBRARY, *pmids])


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
    endnote_backend.import_xml(enxml.to_endnote_xml(records), TEST_LIBRARY)
    found = _exact(endnote_backend.lookup(PMIDS, TEST_LIBRARY))
    assert set(found) == set(PMIDS)
    assert found["17625570"].citation.startswith("{Soda, 2007 #")
    assert found["15033360"].citation.startswith("{Güthe, 2004 #")
    assert found["41528114"].title == "Cancer statistics, 2026"


def test_delete_helper_refuses_when_other_libraries_are_open(monkeypatch):
    monkeypatch.setattr(endnote_backend, "open_libraries", lambda: ["DoD EML4ALK.enl"])
    monkeypatch.setattr(endnote_backend, "run_applescript", lambda *a, **k: pytest.fail("EndNote must not be called"))
    with pytest.raises(RuntimeError):
        _delete_test_records(["1"])


def _resolve_cli() -> list[str]:
    exe = shutil.which("cli-anything-endnote") or str(Path(sys.executable).with_name("cli-anything-endnote"))
    if not Path(exe).exists():
        pytest.fail("cli-anything-endnote is not installed. Run: pip install -e endnote/agent-harness")
    return [exe]


def _cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([*_resolve_cli(), *args], capture_output=True, text=True, encoding="utf-8")


def test_installed_command_help_lists_commands():
    proc = _cli("--help")
    assert proc.returncode == 0
    for command in ("status", "add", "find", "link", "repl"):
        assert command in proc.stdout


def test_installed_command_version():
    assert "0.1.0" in _cli("--version").stdout


def test_python_m_entry_point():
    proc = subprocess.run([sys.executable, "-m", "cli_anything.endnote", "--help"], capture_output=True, text=True)
    assert proc.returncode == 0 and "link" in proc.stdout


def test_installed_command_reports_bad_docx_as_json(tmp_path):
    bad = tmp_path / "x.docx"
    bad.write_text("no")
    proc = _cli("--json", "link", str(bad))
    assert proc.returncode == 1
    assert json.loads(proc.stdout)["code"] == "bad_docx"


@pytest.mark.endnote
def test_installed_command_status(test_library):
    data = json.loads(_cli("--json", "status").stdout)
    assert data["libraries"] == [TEST_LIBRARY]
    assert isinstance(data["record_count"], int)


@pytest.mark.endnote
@pytest.mark.network
def test_add_twice_then_find(test_library):
    first = library.add_pmids(PMIDS)
    assert sorted(r.pmid for r in first.added) == sorted(PMIDS)
    assert first.failed == []
    second = library.add_pmids(PMIDS)
    assert second.added == []
    assert sorted(r.pmid for r in second.skipped) == sorted(PMIDS)
    found, _ = library.find_pmids(PMIDS)
    assert {p: r.rec_number for p, r in found.items()} == {r.pmid: r.rec_number for r in first.added}


@pytest.mark.endnote
@pytest.mark.network
def test_link_command_end_to_end(test_library, tmp_path):
    src = make_docx(
        tmp_path / "grant.docx",
        '<w:p><w:r><w:t xml:space="preserve">Deaths [PMID 41528114]. Fusion [PMID 17625570; 15033360].</w:t></w:r></w:p>',
    )
    proc = _cli("--json", "link", str(src))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout)
    assert data["markers_linked"] == 2 and data["markers_left"] == []
    found, _ = library.find_pmids(PMIDS)
    n = {p: r.rec_number for p, r in found.items()}
    text = visible_paragraphs(Path(data["output"]))[0]
    expected = (
        rf"Deaths \{{Siegel, 2026 #{n['41528114']}\}}\. "
        rf"Fusion \{{Soda, 2007 #{n['17625570']}; Güthe, 2004 #{n['15033360']}\}}\."
    )
    assert re.fullmatch(expected, text), text
