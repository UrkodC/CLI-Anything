"""Unit tests for the screen route (Window > Mode). No test runs the real osascript, ps or sleep."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from cli_anything.premiere.core import ui
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.premiere_cli import dispatch

PS = (" 7450 /Applications/Adobe Premiere Pro 2027/Adobe Premiere Pro 2027.app/Contents/MacOS/Adobe Premiere Pro 2027\n"
      "  812 /usr/sbin/cfprefsd\n")
APP_2027 = {"pid": 7450, "process_name": "Adobe Premiere Pro 2027",
            "app_path": "/Applications/Adobe Premiere Pro 2027/Adobe Premiere Pro 2027.app"}
LINE_2026 = " 6100 /Applications/Adobe Premiere Pro 2026/Adobe Premiere Pro 2026.app/Contents/MacOS/Adobe Premiere Pro 2026\n"
PROCESS = "Adobe Premiere Pro 2027"

# The reader script of the brief, tabs and all. It cannot be run in a test, so the text itself is pinned.
EXPECTED_GET = "\n".join([
    'tell application "System Events"',
    '\ttell process "Adobe Premiere Pro 2027"',
    '\t\tset mm to menu 1 of menu item "Mode" of menu 1 of menu bar item "Window" of menu bar 1',
    '\t\trepeat with n in {"Import", "Edit", "Color", "Export"}',
    '\t\t\tset mc to value of attribute "AXMenuItemMarkChar" of menu item (n as text) of mm',
    '\t\t\tif mc is not missing value then return (n as text)',
    '\t\tend repeat',
    '\tend tell',
    'end tell',
    'return "none"',
])
EXPECTED_CLICK_COLOR = ('tell application "System Events" to tell process "Adobe Premiere Pro 2027" to click menu item '
                        '"Color" of menu 1 of menu item "Mode" of menu 1 of menu bar item "Window" of menu bar 1')


def _no_real_process(*args, **kwargs):
    raise AssertionError(f"a test reached the real subprocess.run: {args!r}")


def _no_real_sleep(seconds):
    raise AssertionError(f"a test reached the real sleep({seconds!r})")


@pytest.fixture(autouse=True)
def _guard_the_real_world(monkeypatch):
    """The ui module sees stubs for subprocess and time unless a test replaces them again."""
    monkeypatch.setattr(ui, "subprocess", SimpleNamespace(run=_no_real_process, TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(ui, "time", SimpleNamespace(sleep=_no_real_sleep))


class StubOsascript:
    """Records every source. Clicks answer "" and reads answer from a queue; an empty queue is a test bug."""

    def __init__(self, reads: list[str]) -> None:
        self.reads = list(reads)
        self.sources: list[str] = []

    def __call__(self, source: str) -> str:
        self.sources.append(source)
        if "click menu item" in source:
            return ""
        assert self.reads, "the code read the mode more often than the test expected"
        return self.reads.pop(0)

    @property
    def clicks(self) -> int:
        return sum("click menu item" in s for s in self.sources)

    @property
    def read_count(self) -> int:
        return sum("AXMenuItemMarkChar" in s for s in self.sources)


class Sleeps:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# ---------- running_premiere ----------
def test_running_premiere_is_the_running_one():
    assert ui.running_premiere(PS) == APP_2027
    assert ui.running_premiere(" 812 /usr/sbin/cfprefsd\n") is None


def test_running_premiere_with_no_output_is_none():
    assert ui.running_premiere("") is None


def test_two_running_versions_are_refused():
    with pytest.raises(CliError) as err:
        ui.running_premiere(PS + LINE_2026)
    assert err.value.code == "two_premieres"
    assert "2026" in err.value.message and "2027" in err.value.message


def test_same_app_on_two_lines_counts_once():
    twice = PS + " 7451 /Applications/Adobe Premiere Pro 2027/Adobe Premiere Pro 2027.app/Contents/MacOS/Adobe Premiere Pro 2027\n"
    assert ui.running_premiere(twice) == APP_2027       # the first line gives the pid


def test_same_version_from_two_folders_is_two_premieres():
    other = " 7451 /Users/me/Adobe Premiere Pro 2027.app/Contents/MacOS/Adobe Premiere Pro 2027\n"
    with pytest.raises(CliError) as err:
        ui.running_premiere(PS + other)
    assert err.value.code == "two_premieres"


def test_running_premiere_keeps_spaces_in_the_path():
    line = "  42 /Volumes/My Disk/Apps Here/Adobe Premiere Pro 2027.app/Contents/MacOS/Adobe Premiere Pro 2027\n"
    assert ui.running_premiere(line) == {"pid": 42, "process_name": "Adobe Premiere Pro 2027",
                                         "app_path": "/Volumes/My Disk/Apps Here/Adobe Premiere Pro 2027.app"}


@pytest.mark.parametrize("line", [
    # the year of the folder and the program differ
    " 9 /Applications/Adobe Premiere Pro 2027.app/Contents/MacOS/Adobe Premiere Pro 2026\n",
    # a helper inside the bundle
    " 9 /Applications/Adobe Premiere Pro 2027.app/Contents/Frameworks/Adobe Premiere Pro Helper\n",
    " 9 /Applications/Adobe Premiere Pro 2027.app/Contents/MacOS/Adobe Premiere Pro 2027 Helper\n",
    # a path that only ends like the program
    " 9 /tmp/Adobe Premiere Pro 2027\n",
    # other Adobe programs and a line without a pid
    " 9 /Applications/Adobe After Effects 2027/Adobe After Effects 2027.app/Contents/MacOS/After Effects\n",
    "/Applications/Adobe Premiere Pro 2027.app/Contents/MacOS/Adobe Premiere Pro 2027\n",
    "abc /Applications/Adobe Premiere Pro 2027.app/Contents/MacOS/Adobe Premiere Pro 2027\n",
])
def test_lines_that_are_not_premiere_are_ignored(line):
    assert ui.running_premiere(line) is None


def test_running_premiere_reads_ps_with_a_timeout(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return SimpleNamespace(returncode=0, stdout=PS, stderr="")
    monkeypatch.setattr(ui.subprocess, "run", fake_run)
    assert ui.running_premiere() == APP_2027
    assert seen["argv"][1:] == ["-axo", "pid=,comm="]
    assert seen["kw"]["timeout"] == 10


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired("ps", 10),
    OSError("no such file"),
    SimpleNamespace(returncode=1, stdout="", stderr="ps: broken"),
])
def test_running_premiere_says_ui_failed_when_ps_fails(monkeypatch, failure):
    def fake_run(argv, **kw):
        if isinstance(failure, Exception):
            raise failure
        return failure
    monkeypatch.setattr(ui.subprocess, "run", fake_run)
    with pytest.raises(CliError) as err:
        ui.running_premiere()
    assert err.value.code == "ui_failed"


# ---------- the scripts ----------
def test_mode_get_script_is_the_one_in_the_brief():
    run = StubOsascript(["Edit\n"])
    ui.mode_get(PROCESS, run)
    assert run.sources == [EXPECTED_GET]


def test_the_script_lists_the_modes():
    assert ui.MODES == ("import", "edit", "color", "export")
    titles = ", ".join(json.dumps(mode.capitalize()) for mode in ui.MODES)
    assert "{" + titles + "}" in EXPECTED_GET


def test_click_script_is_the_one_in_the_brief():
    run = StubOsascript(["Color\n"] * 5)
    ui.mode_set(PROCESS, "color", run, Sleeps())
    assert run.sources[0] == EXPECTED_CLICK_COLOR


def test_the_process_name_goes_in_through_json_quoting():
    run = StubOsascript(["Color\n"])
    ui.mode_get('We"ird', run)
    assert 'tell process "We\\"ird"' in run.sources[0]


# ---------- mode_get ----------
def test_mode_get_lowercases_the_title():
    assert ui.mode_get(PROCESS, StubOsascript(["Color\n"])) == "color"


def test_mode_get_with_no_checked_mode_fails_plainly():
    with pytest.raises(CliError) as err:
        ui.mode_get(PROCESS, StubOsascript(["none\n"]))
    assert err.value.code == "ui_failed"
    assert "No mode is checked" in err.value.message


def test_mode_get_with_an_unknown_answer_fails():
    with pytest.raises(CliError) as err:
        ui.mode_get(PROCESS, StubOsascript(["Assembly\n"]))
    assert err.value.code == "ui_failed"
    assert "assembly" in err.value.message


# ---------- mode_set ----------
def test_mode_set_stable():
    run, sleeps = StubOsascript(["Color\n"] * 5), Sleeps()
    assert ui.mode_set(PROCESS, "color", run, sleeps) == {"mode": "color", "reverted_once": False}
    assert run.clicks == 1 and run.read_count == 5
    assert sleeps.calls == [1] * 5                      # one second before each read
    assert run.sources[0] == EXPECTED_CLICK_COLOR       # the click comes before every read


def test_mode_set_reverts_once():
    run, sleeps = StubOsascript(["Color\n", "Edit\n"] + ["Color\n"] * 5), Sleeps()
    assert ui.mode_set(PROCESS, "color", run, sleeps) == {"mode": "color", "reverted_once": True}
    assert run.clicks == 2 and run.read_count == 7
    assert sleeps.calls == [1] * 7
    kinds = ["click" if "click menu item" in s else "read" for s in run.sources]
    assert kinds == ["click", "read", "read", "click"] + ["read"] * 5   # the watch stops at the first wrong reading


def test_mode_set_reverts_twice():
    run = StubOsascript(["Color\n", "Edit\n", "Color\n", "Import\n"])
    with pytest.raises(CliError) as err:
        ui.mode_set(PROCESS, "color", run, Sleeps())
    assert err.value.code == "mode_reverted"
    assert run.clicks == 2 and run.read_count == 4      # never a third click


def test_mode_set_reverted_at_the_first_reading_twice():
    run = StubOsascript(["Edit\n", "Edit\n"])
    with pytest.raises(CliError) as err:
        ui.mode_set(PROCESS, "color", run, Sleeps())
    assert err.value.code == "mode_reverted"
    assert run.clicks == 2 and run.read_count == 2


def test_mode_set_honors_watch_s():
    run, sleeps = StubOsascript(["Export\n"] * 2), Sleeps()
    assert ui.mode_set(PROCESS, "export", run, sleeps, watch_s=2)["mode"] == "export"
    assert run.read_count == 2 and sleeps.calls == [1, 1]


@pytest.mark.parametrize("mode", ["grade", "Color", "", "none"])
def test_mode_set_rejects_unknown_mode(mode):
    run, sleeps = StubOsascript([]), Sleeps()
    with pytest.raises(CliError) as err:
        ui.mode_set(PROCESS, mode, run, sleeps)
    assert err.value.code == "bad_mode"
    assert run.sources == [] and sleeps.calls == []     # no osascript call, no wait


def test_mode_set_passes_its_runner_to_every_read():
    # A forgotten pass-through would fall to the real osascript, which the autouse guard turns into a failure.
    run = StubOsascript(["Color\n"] * 5)
    ui.mode_set(PROCESS, "color", run, Sleeps())
    assert run.read_count == 5


def test_mode_set_with_no_runner_uses_the_module_runner(monkeypatch):
    run = StubOsascript(["Color\n"] * 5)
    sleeps = Sleeps()
    monkeypatch.setattr(ui, "run_osascript", run)
    monkeypatch.setattr(ui, "time", SimpleNamespace(sleep=sleeps))
    assert ui.mode_set(PROCESS, "color")["reverted_once"] is False
    assert run.clicks == 1 and sleeps.calls == [1] * 5


# ---------- run_osascript ----------
def test_run_osascript_returns_stdout_and_sets_a_timeout(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return SimpleNamespace(returncode=0, stdout="Color\n", stderr="")
    monkeypatch.setattr(ui.subprocess, "run", fake_run)
    assert ui.run_osascript("return 1") == "Color\n"
    assert seen["argv"] == ["/usr/bin/osascript", "-e", "return 1"]
    assert seen["kw"]["timeout"] == 30


def _failing_run(stderr: str, returncode: int = 1):
    return lambda argv, **kw: SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)


def test_no_accessibility_message(monkeypatch):
    monkeypatch.setattr(ui.subprocess, "run", _failing_run(
        "execution error: System Events got an error: osascript is not allowed assistive access. (-25211)"))
    with pytest.raises(CliError) as err:
        ui.run_osascript("return 1")
    assert err.value.code == "no_accessibility"
    assert err.value.message == ("Give the terminal Accessibility permission: "
                                 "System Settings > Privacy & Security > Accessibility. osascript said: "
                                 "execution error: System Events got an error: osascript is not allowed "
                                 "assistive access. (-25211)")


@pytest.mark.parametrize("stderr", ["x (-1719)", "x (-25211)", "Not allowed assistive access."])
def test_every_accessibility_marker_counts(monkeypatch, stderr):
    monkeypatch.setattr(ui.subprocess, "run", _failing_run(stderr))
    with pytest.raises(CliError) as err:
        ui.run_osascript("return 1")
    assert err.value.code == "no_accessibility"
    assert err.value.message.endswith(f"osascript said: {stderr}")    # a wrong menu path shows its own error


def test_other_osascript_failures_are_ui_failed(monkeypatch):
    monkeypatch.setattr(ui.subprocess, "run", _failing_run("execution error: Can't get menu item. (-1728)\n"))
    with pytest.raises(CliError) as err:
        ui.run_osascript("return 1")
    assert err.value.code == "ui_failed"
    assert err.value.message == "execution error: Can't get menu item. (-1728)"


def test_a_failure_with_no_stderr_still_has_a_message(monkeypatch):
    monkeypatch.setattr(ui.subprocess, "run", _failing_run("", returncode=3))
    with pytest.raises(CliError) as err:
        ui.run_osascript("return 1")
    assert err.value.code == "ui_failed" and "3" in err.value.message


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("osascript", 30), OSError("gone")])
def test_osascript_timeout_and_start_failure_are_ui_failed(monkeypatch, failure):
    def fake_run(argv, **kw):
        raise failure
    monkeypatch.setattr(ui.subprocess, "run", fake_run)
    with pytest.raises(CliError) as err:
        ui.run_osascript("return 1")
    assert err.value.code == "ui_failed"


# ---------- the commands ----------
def _json_run(capsys, argv) -> tuple[int, dict]:
    code = dispatch(["--json", *argv])
    return code, json.loads(capsys.readouterr().out)


def test_ui_command_without_premiere(monkeypatch, capsys):
    run = StubOsascript([])
    monkeypatch.setattr(ui, "running_premiere", lambda *a, **k: None)
    monkeypatch.setattr(ui, "run_osascript", run)
    for argv in (["ui", "mode", "get"], ["ui", "mode", "set", "color"]):
        code, out = _json_run(capsys, argv)
        assert code == 1
        assert out["ok"] is False and out["code"] == "premiere_not_running"
    assert run.sources == []


def test_ui_command_with_two_premieres_passes_the_error_through(monkeypatch, capsys):
    def two(*a, **k):
        raise CliError("two_premieres", "Two versions are running.")
    monkeypatch.setattr(ui, "running_premiere", two)
    code, out = _json_run(capsys, ["ui", "mode", "get"])
    assert code == 1 and out == {"ok": False, "error": "Two versions are running.", "code": "two_premieres"}


def test_ui_mode_get_command(monkeypatch, capsys):
    run = StubOsascript(["Color\n"])
    monkeypatch.setattr(ui, "running_premiere", lambda *a, **k: APP_2027)
    monkeypatch.setattr(ui, "run_osascript", run)
    code, out = _json_run(capsys, ["ui", "mode", "get"])
    assert code == 0
    assert out == {"ok": True, "mode": "color"}
    assert run.sources == [EXPECTED_GET]


def test_ui_mode_get_command_human_and_trailing_json(monkeypatch, capsys):
    monkeypatch.setattr(ui, "running_premiere", lambda *a, **k: APP_2027)
    monkeypatch.setattr(ui, "run_osascript", StubOsascript(["Edit\n", "Edit\n"]))
    assert dispatch(["ui", "mode", "get"]) == 0
    assert "edit" in capsys.readouterr().out
    assert dispatch(["ui", "mode", "get", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "mode": "edit"}


def test_ui_mode_set_command(monkeypatch, capsys):
    run, sleeps = StubOsascript(["Color\n"] * 5), Sleeps()
    monkeypatch.setattr(ui, "running_premiere", lambda *a, **k: APP_2027)
    monkeypatch.setattr(ui, "run_osascript", run)
    monkeypatch.setattr(ui, "time", SimpleNamespace(sleep=sleeps))
    code, out = _json_run(capsys, ["ui", "mode", "set", "color"])
    assert code == 0
    assert out == {"ok": True, "mode": "color", "reverted_once": False}
    assert run.clicks == 1 and run.read_count == 5
    assert run.sources[0] == EXPECTED_CLICK_COLOR
    assert sleeps.calls == [1] * 5


def test_ui_mode_set_command_reports_a_revert(monkeypatch, capsys):
    run = StubOsascript(["Edit\n", "Color\n", "Color\n", "Color\n", "Color\n", "Color\n"])
    monkeypatch.setattr(ui, "running_premiere", lambda *a, **k: APP_2027)
    monkeypatch.setattr(ui, "run_osascript", run)
    monkeypatch.setattr(ui, "time", SimpleNamespace(sleep=Sleeps()))
    assert dispatch(["ui", "mode", "set", "color"]) == 0
    out = capsys.readouterr().out
    assert "color" in out and "back" in out
    assert run.clicks == 2


def test_ui_mode_set_command_rejects_a_bad_mode_as_json(monkeypatch, capsys):
    run = StubOsascript([])
    monkeypatch.setattr(ui, "running_premiere", lambda *a, **k: APP_2027)
    monkeypatch.setattr(ui, "run_osascript", run)
    code, out = _json_run(capsys, ["ui", "mode", "set", "grade"])
    assert code == 1
    assert out["ok"] is False and out["code"] == "bad_mode"
    assert run.sources == []


def test_ui_mode_set_command_fails_with_mode_reverted(monkeypatch, capsys):
    run = StubOsascript(["Edit\n", "Edit\n"])
    monkeypatch.setattr(ui, "running_premiere", lambda *a, **k: APP_2027)
    monkeypatch.setattr(ui, "run_osascript", run)
    monkeypatch.setattr(ui, "time", SimpleNamespace(sleep=Sleeps()))
    code, out = _json_run(capsys, ["ui", "mode", "set", "color"])
    assert code == 1 and out["code"] == "mode_reverted"


def test_ui_group_help_lists_mode(capsys):
    assert dispatch(["ui", "--help"]) == 0
    assert "mode" in capsys.readouterr().out
    assert dispatch(["ui", "mode", "--help"]) == 0
    out = capsys.readouterr().out
    assert "get" in out and "set" in out
