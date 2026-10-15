from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli_anything.endnote import endnote_cli
from cli_anything.endnote.core import library
from cli_anything.endnote.core.library import AddReport, LibraryRecord
from cli_anything.endnote.errors import CliError
from cli_anything.endnote.tests._helpers import make_docx, visible_paragraphs

SODA = LibraryRecord("17625570", 18, "{Soda, 2007 #18}", "Identification of the transforming EML4-ALK fusion gene")
SIEGEL = LibraryRecord("41528114", 2, "{Siegel, 2026 #2}", "Cancer statistics, 2026")
BODY = '<w:p><w:r><w:t xml:space="preserve">Deaths [PMID 41528114]. Fusion [PMID 17625570].</w:t></w:r></w:p>'


def run(*args: str):
    return CliRunner().invoke(endnote_cli.cli, list(args))


def fake_add(added=(), skipped=(), failed=(), calls=None):
    def _add(pmids):
        if calls is not None:
            calls.append(list(pmids))
        return AddReport(library="Test.enl", added=list(added), skipped=list(skipped), failed=list(failed))
    return _add


def test_version():
    result = run("--version")
    assert result.exit_code == 0 and "0.1.0" in result.output


def test_no_command_starts_repl(monkeypatch):
    called = []
    monkeypatch.setattr(endnote_cli, "_run_repl", lambda ctx: called.append(True))
    assert run().exit_code == 0
    assert called == [True]


def test_status_json_with_global_or_local_flag(monkeypatch):
    monkeypatch.setattr(library, "status", lambda: {"running": True, "libraries": ["Test.enl"], "record_count": 42})
    for args in (("--json", "status"), ("status", "--json")):
        assert json.loads(run(*args).output) == {"ok": True, "running": True, "libraries": ["Test.enl"], "record_count": 42}


def test_status_human_when_not_running(monkeypatch):
    monkeypatch.setattr(library, "status", lambda: {"running": False, "libraries": [], "record_count": None})
    assert "EndNote is not running." in run("status").output


def test_add_reports_added_skipped_failed(monkeypatch):
    calls = []
    failed = [{"pmid": "99999999", "reason": "not found on PubMed"}]
    monkeypatch.setattr(library, "add_pmids", fake_add([SODA], [SIEGEL], failed, calls=calls))
    result = run("add", "17625570,", "41528114", "99999999")
    assert result.exit_code == 0
    assert calls == [["17625570", "41528114", "99999999"]]
    assert "Added: 1" in result.output
    assert "Skipped (already in library): 1" in result.output
    assert "99999999  not found on PubMed" in result.output


def test_add_reads_file_and_ignores_comment_lines(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(library, "add_pmids", fake_add([SODA], calls=calls))
    pmid_file = tmp_path / "PMIDS.txt"
    pmid_file.write_text("# list made 2026-10-15\n17625570\n")
    assert run("add", "--file", str(pmid_file)).exit_code == 0
    assert calls == [["17625570"]]


def test_add_exits_1_when_every_pmid_fails(monkeypatch):
    monkeypatch.setattr(library, "add_pmids", fake_add(failed=[{"pmid": "1", "reason": "not found on PubMed"}]))
    assert run("add", "1").exit_code == 1


def test_add_without_pmids_is_an_error():
    result = run("--json", "add")
    assert result.exit_code == 1
    assert json.loads(result.output) == {"ok": False, "error": "Give at least one PMID, or use --file.", "code": "no_pmids"}


def test_library_errors_become_json_errors(monkeypatch):
    def boom(pmids):
        raise CliError("many_libraries", "Close all libraries except one: A.enl, B.enl.")

    monkeypatch.setattr(library, "add_pmids", boom)
    result = run("--json", "add", "17625570")
    assert result.exit_code == 1
    assert json.loads(result.output) == {
        "ok": False, "error": "Close all libraries except one: A.enl, B.enl.", "code": "many_libraries",
    }


def test_find_shows_not_found(monkeypatch):
    monkeypatch.setattr(library, "find_pmids", lambda pmids: ({"17625570": SODA, "1": None}, []))
    output = run("find", "17625570", "1").output
    assert "17625570  {Soda, 2007 #18}" in output
    assert "1  not found" in output


def test_link_writes_linked_copy(monkeypatch, tmp_path):
    src = make_docx(tmp_path / "grant.docx", BODY)
    calls = []
    monkeypatch.setattr(library, "add_pmids", fake_add([SODA], [SIEGEL], calls=calls))
    result = run("--json", "link", str(src))
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    out = tmp_path / "grant_LINKED.docx"
    assert data["output"] == str(out)
    assert (data["markers_found"], data["markers_linked"], data["markers_left"]) == (2, 2, [])
    assert data["pmids_added"] == ["17625570"] and data["pmids_already_in_library"] == ["41528114"]
    assert calls == [["41528114", "17625570"]]
    assert visible_paragraphs(out) == ["Deaths {Siegel, 2026 #2}. Fusion {Soda, 2007 #18}."]


def test_link_human_output_tells_the_next_step(monkeypatch, tmp_path):
    src = make_docx(tmp_path / "grant.docx", BODY)
    monkeypatch.setattr(library, "add_pmids", fake_add([SODA], [SIEGEL]))
    output = run("link", str(src)).output
    assert "Update Citations and Bibliography" in output
    assert "Test.enl" in output


def test_link_refuses_existing_output_before_touching_endnote(monkeypatch, tmp_path):
    src = make_docx(tmp_path / "grant.docx", BODY)
    (tmp_path / "grant_LINKED.docx").write_bytes(b"old")
    monkeypatch.setattr(library, "add_pmids", lambda pmids: pytest.fail("EndNote must not be called"))
    result = run("--json", "link", str(src))
    assert result.exit_code == 1
    assert json.loads(result.output)["code"] == "output_exists"


def test_link_overwrite_replaces_output(monkeypatch, tmp_path):
    src = make_docx(tmp_path / "grant.docx", BODY)
    (tmp_path / "grant_LINKED.docx").write_bytes(b"old")
    monkeypatch.setattr(library, "add_pmids", fake_add([SODA], [SIEGEL]))
    assert run("link", str(src), "--overwrite").exit_code == 0
    assert visible_paragraphs(tmp_path / "grant_LINKED.docx")[0].startswith("Deaths {Siegel")


def test_link_output_cannot_be_the_input(tmp_path):
    src = make_docx(tmp_path / "grant.docx", BODY)
    result = run("--json", "link", str(src), "--output", str(src), "--overwrite")
    assert result.exit_code == 1
    assert json.loads(result.output)["code"] == "output_is_input"


def test_link_output_differing_only_in_case_is_the_input(monkeypatch, tmp_path):
    src = make_docx(tmp_path / "grant.docx", BODY)
    if not Path(str(src).upper()).exists():
        pytest.skip("case-sensitive filesystem")
    before = src.read_bytes()
    monkeypatch.setattr(library, "add_pmids", lambda pmids: pytest.fail("EndNote must not be called"))
    result = run("--json", "link", str(src), "--output", str(tmp_path / "GRANT.DOCX"), "--overwrite")
    assert result.exit_code == 1
    assert json.loads(result.output)["code"] == "output_is_input"
    assert src.read_bytes() == before


def test_link_output_folder_must_exist(monkeypatch, tmp_path):
    src = make_docx(tmp_path / "grant.docx", BODY)
    monkeypatch.setattr(library, "add_pmids", lambda pmids: pytest.fail("EndNote must not be called"))
    result = run("--json", "link", str(src), "--output", str(tmp_path / "missing" / "x.docx"))
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["code"] == "bad_output"
    assert data["error"] == f"The folder {tmp_path / 'missing'} does not exist."


def test_link_without_markers_writes_nothing(monkeypatch, tmp_path):
    src = make_docx(tmp_path / "plain.docx", "<w:p><w:r><w:t>No citations here.</w:t></w:r></w:p>")
    monkeypatch.setattr(library, "add_pmids", lambda pmids: pytest.fail("EndNote must not be called"))
    result = run("link", str(src))
    assert result.exit_code == 0
    assert "No [PMID ...] markers found" in result.output
    assert not (tmp_path / "plain_LINKED.docx").exists()


def test_link_warns_about_tracked_changes(monkeypatch, tmp_path):
    body = ('<w:p><w:ins w:id="1" w:author="C" w:date="2026-10-15T00:00:00Z">'
            '<w:r><w:t>[PMID 17625570]</w:t></w:r></w:ins></w:p>')
    src = make_docx(tmp_path / "tracked.docx", body)
    monkeypatch.setattr(library, "add_pmids", fake_add([SODA]))
    assert endnote_cli.TRACKED_WARNING in run("link", str(src)).output


def test_link_when_every_pmid_fails_writes_nothing_and_exits_1(monkeypatch, tmp_path):
    src = make_docx(tmp_path / "grant.docx", BODY)
    failed = [{"pmid": "41528114", "reason": "x"}, {"pmid": "17625570", "reason": "x"}]
    monkeypatch.setattr(library, "add_pmids", fake_add(failed=failed))
    result = run("--json", "link", str(src))
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["output"] is None
    assert data["markers_left"] == ["[PMID 41528114]", "[PMID 17625570]"]
    assert not (tmp_path / "grant_LINKED.docx").exists()


def test_link_bad_docx(tmp_path):
    bad = tmp_path / "x.docx"
    bad.write_text("not a zip")
    result = run("--json", "link", str(bad))
    assert result.exit_code == 1
    assert json.loads(result.output)["code"] == "bad_docx"
