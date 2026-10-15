from __future__ import annotations

import subprocess

import pytest

from cli_anything.endnote.errors import CliError
from cli_anything.endnote.utils import endnote_backend as backend

ROW = '{q}\x1f<RecordID database="1" recNum="2" />\x1f41528114\x1f{{Siegel, 2026 #2}}\x1fCancer statistics, 2026'


def test_parse_lookup_output_reads_every_row():
    out = ROW.format(q="41528114") + "\x1e" + ROW.format(q="4152811")
    rows = backend.parse_lookup_output(out)
    assert rows[0] == backend.FoundRecord("41528114", 2, "41528114", "{Siegel, 2026 #2}", "Cancer statistics, 2026")
    assert rows[1].query == "4152811"


def test_parse_lookup_output_empty():
    assert backend.parse_lookup_output("") == []


def test_parse_lookup_output_rejects_unknown_record_id():
    with pytest.raises(CliError) as exc:
        backend.parse_lookup_output("1\x1fgarbage\x1f1\x1f{A, 2000 #1}\x1fT")
    assert exc.value.code == "applescript_error"


def test_lookup_with_no_pmids_does_not_call_endnote(monkeypatch):
    monkeypatch.setattr(backend, "run_applescript", lambda *a, **k: pytest.fail("no call expected"))
    assert backend.lookup([], "Test.enl") == []


def test_missing_app_raises_endnote_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(backend, "APP_PATH", tmp_path / "missing.app")
    with pytest.raises(CliError) as exc:
        backend.run_applescript("return 1")
    assert exc.value.code == "endnote_missing"
    assert exc.value.message == "EndNote 2025 was not found in /Applications."


def test_timeout_raises_endnote_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(backend, "APP_PATH", tmp_path)

    def slow(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="osascript", timeout=5)

    monkeypatch.setattr(backend.subprocess, "run", slow)
    with pytest.raises(CliError) as exc:
        backend.run_applescript("return 1", timeout=5)
    assert exc.value.code == "endnote_timeout"
    assert exc.value.message == "EndNote 2025 did not answer in 5 seconds. Check for an open dialog in EndNote."


def test_automation_denied_is_recognised(monkeypatch, tmp_path):
    monkeypatch.setattr(backend, "APP_PATH", tmp_path)
    monkeypatch.setattr(
        backend.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "execution error: Not authorized to send Apple events to EndNote 2025. (-1743)"),
    )
    with pytest.raises(CliError) as exc:
        backend.run_applescript("return 1")
    assert exc.value.code == "automation_denied"


def test_other_errors_are_applescript_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(backend, "APP_PATH", tmp_path)
    monkeypatch.setattr(
        backend.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "execution error: boom (-1701)"),
    )
    with pytest.raises(CliError) as exc:
        backend.run_applescript("return 1")
    assert exc.value.code == "applescript_error"
    assert "boom" in exc.value.message


def test_arguments_and_unicode_are_passed_to_osascript(monkeypatch, tmp_path):
    monkeypatch.setattr(backend, "APP_PATH", tmp_path)
    seen = {}

    def fake(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, "Güthe\n", "")

    monkeypatch.setattr(backend.subprocess, "run", fake)
    assert backend.run_applescript("return 1", args=["a b", "α"]) == "Güthe"
    assert seen["cmd"] == ["osascript", "-", "a b", "α"]
    assert seen["kwargs"]["encoding"] == "utf-8"
    assert seen["kwargs"]["input"] == "return 1"


def test_import_sends_library_then_xml(monkeypatch):
    sent = []
    monkeypatch.setattr(backend, "run_applescript", lambda script, args=None, timeout=300: sent.append(args) or "OK")
    backend.import_xml("<xml/>", "Test.enl")
    assert sent == [["Test.enl", "<xml/>"]]


def test_lookup_sends_library_first(monkeypatch):
    sent = []
    monkeypatch.setattr(backend, "run_applescript", lambda script, args=None, timeout=300: sent.append(args) or "")
    assert backend.lookup(["1", "2"], "Test.enl") == []
    assert sent == [["Test.enl", "1", "2"]]


@pytest.mark.parametrize("call", [lambda: backend.lookup(["1"], "Test.enl"), lambda: backend.import_xml("<x/>", "Test.enl")])
def test_library_changed_error_is_recognised(monkeypatch, tmp_path, call):
    monkeypatch.setattr(backend, "APP_PATH", tmp_path)
    monkeypatch.setattr(
        backend.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "execution error: endnote-cli: library changed (1001)"),
    )
    with pytest.raises(CliError) as exc:
        call()
    assert exc.value.code == "library_changed"
    assert exc.value.message == "The open EndNote library changed during the run. Open only Test.enl and run the command again."


def test_apple_event_timeout_is_endnote_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr(backend, "APP_PATH", tmp_path)
    monkeypatch.setattr(
        backend.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "execution error: EndNote 2025 got an error: AppleEvent timed out. (-1712)"),
    )
    with pytest.raises(CliError) as exc:
        backend.run_applescript("return 1")
    assert exc.value.code == "endnote_timeout"
    assert exc.value.message == "EndNote 2025 did not answer. Check for an open dialog in EndNote."


def test_open_libraries_parses_names(monkeypatch):
    monkeypatch.setattr(backend, "run_applescript", lambda *a, **k: "RUNNING\x1fA.enl\x1eB.enl")
    assert backend.open_libraries() == ["A.enl", "B.enl"]
    monkeypatch.setattr(backend, "run_applescript", lambda *a, **k: "RUNNING\x1f")
    assert backend.open_libraries() == []
    monkeypatch.setattr(backend, "run_applescript", lambda *a, **k: "NOT_RUNNING")
    assert backend.open_libraries() is None
