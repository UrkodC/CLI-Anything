"""Unit tests for `doctor`, `recovery show` and the `live` key of `status`, against a fake panel in a temp folder."""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from cli_anything.premiere import live_cli
from cli_anything.premiere.core import bridge, doctor, guard, jsx
from cli_anything.premiere.core import ui
from cli_anything.premiere.core.bridge import BridgeClient, mark_recovery
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.premiere_cli import dispatch
from cli_anything.premiere.tests.fakes import (  # noqa: F401
    MARKER_SHAPES, FakePanel, bridge_dir, cmd_files, keep_signals, restore_marker_mode, signal_when,
    unreadable_marker, write_heartbeat)

LOCK = ".host-owner-lease"
RECOVERY = ".host-recovery.json"
REAPER = ".host-owner-reaper"
STATE_MARK = "playhead_ticks: playhead"   # occurs in state.jsx only
SUMMARY_MARK = "video_clips: video.clips"  # occurs in summary.jsx only

APP = {"pid": 4242, "process_name": "Adobe Premiere Pro 2027",
       "app_path": "/Applications/Adobe Premiere Pro 2027/Adobe Premiere Pro 2027.app"}
STATE_OK = {"version": "27.0.0", "build": "88",
            "projects": [{"name": "X Claude.prproj", "path": "/p/X Claude.prproj", "active": True}],
            "active_sequence": {"id": "s1", "name": "Main"}, "playhead_ticks": "0", "selection": []}
STATE_26 = {**STATE_OK, "version": "26.5.2"}
STATE_OWN = {**STATE_OK, "projects": [{"name": "Family film.prproj", "path": "/p/Family film.prproj", "active": True}]}
SUMMARY = {"project": "X Claude.prproj", "active_sequence": {"id": "s1", "name": "Main"},
           "video_tracks": 2, "audio_tracks": 2, "video_clips": 5, "audio_clips": 3}
NAMES = ["premiere_running", "bridge_folder", "panel_heartbeat", "bridge_protocol", "panel_version", "lease",
         "recovery", "premiere_version", "projects", "project_guard", "mcp_server", "accessibility"]
STATE_CHECKS = ["premiere_version", "projects", "project_guard"]


def names(checks, status):
    return [c["name"] for c in checks if c["status"] == status]


def by_name(checks) -> dict:
    return {c["name"]: c for c in checks}


def ok(data) -> str:
    return json.dumps({"success": True, "data": data})


def handler_for(state=STATE_OK, summary=SUMMARY):
    def handler(script: str) -> str:
        if STATE_MARK in script:
            return ok(state)
        if SUMMARY_MARK in script:
            return ok(summary)
        return json.dumps({"success": False, "error": "unexpected script"})
    return handler


class Osascript:
    """Stands in for ui.run_osascript: answers the mode read, or raises."""

    def __init__(self, answer: str = "Edit\n", error: CliError | None = None) -> None:
        self.answer, self.error, self.sources = answer, error, []

    def __call__(self, source: str) -> str:
        self.sources.append(source)
        if self.error is not None:
            raise self.error
        return self.answer


def _no_real_process(*a, **k):
    raise AssertionError("a test tried to run a real process")


@pytest.fixture(autouse=True)
def _guard_the_real_world(monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "subprocess", SimpleNamespace(run=_no_real_process, TimeoutExpired=subprocess.TimeoutExpired))
    # If a test ever reaches the unpatched make_client, it finds a missing folder, not the real bridge.
    monkeypatch.setenv("PREMIERE_TEMP_DIR", str(tmp_path / "not-the-real-bridge"))
    monkeypatch.setattr(doctor, "LEASE_BUSY_WAIT_S", 0.3)
    monkeypatch.setattr(doctor, "RECORD_WAIT_S", 0.1)


@pytest.fixture(autouse=True)
def _keep_signal_handlers(keep_signals):  # noqa: F811
    """Every test starts with the default handlers of SIGINT, SIGTERM and SIGHUP and leaves them as found."""
    yield


@pytest.fixture
def stubs():
    return {"premiere": lambda: APP, "panel": lambda: "1.25.0", "which": lambda name: "/usr/local/bin/" + name,
            "osascript": Osascript()}


@pytest.fixture
def panel(bridge_dir):  # noqa: F811
    p = FakePanel(bridge_dir, handler_for())
    p.start()
    yield p
    p.stop()


@pytest.fixture
def cli_stubs(bridge_dir, monkeypatch, stubs):  # noqa: F811
    """Point the commands at the temp bridge folder and the stubs."""
    monkeypatch.setattr(live_cli, "make_client", lambda wait, **kw: BridgeClient(bridge_dir, wait, **kw))
    monkeypatch.setattr(ui, "running_premiere", stubs["premiere"])
    monkeypatch.setattr(ui, "run_osascript", stubs["osascript"])
    monkeypatch.setattr(guard, "panel_version", stubs["panel"])
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/local/bin/" + name)
    return stubs


def look(bridge_dir) -> BridgeClient:  # noqa: F811
    """A client as doctor and recovery show build it: one lease attempt, never a reap."""
    return BridgeClient(bridge_dir, lease_wait_s=0.0, reap=False)


def run_json(capsys, argv) -> tuple[int, dict]:
    code = dispatch(["--json", *argv])
    return code, json.loads(capsys.readouterr().out)


def write_owner(bridge_dir, pid: int) -> bytes:  # noqa: F811
    lock = bridge_dir / LOCK
    lock.mkdir(mode=0o700)
    fd = os.open(lock / "owner.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps({"version": 1, "pid": pid, "token": "other"}))
    return (lock / "owner.json").read_bytes()


def dead_pid() -> int:
    proc = subprocess.Popen(["/usr/bin/true"])
    proc.wait()
    return proc.pid


def assert_untouched(bridge_dir, before: bytes) -> None:  # noqa: F811
    assert (bridge_dir / LOCK).is_dir()
    assert (bridge_dir / LOCK / "owner.json").read_bytes() == before
    assert not (bridge_dir / RECOVERY).exists()
    assert not (bridge_dir / REAPER).exists()


# ---------- run_checks ----------
def test_all_pass(bridge_dir, panel, stubs, capsys, cli_stubs):  # noqa: F811
    checks = doctor.run_checks(look(bridge_dir), **stubs)
    assert [c["name"] for c in checks] == NAMES
    assert names(checks, "pass") == NAMES, checks
    found = by_name(checks)
    assert "Adobe Premiere Pro 2027" in found["premiere_running"]["detail"]
    assert "X Claude.prproj" in found["projects"]["detail"]
    assert re.search(r"[0-9]", found["panel_heartbeat"]["detail"])
    code, out = run_json(capsys, ["doctor"])
    assert code == 0 and out["ok"] is True
    assert out["summary"] == {"pass": 12, "warn": 0, "fail": 0}
    assert [c["name"] for c in out["checks"]] == NAMES
    assert dispatch(["doctor"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert sum(line.startswith("PASS") for line in lines) == 12


def test_no_panel(bridge_dir, stubs, capsys, cli_stubs):  # noqa: F811
    p = FakePanel(bridge_dir, handler_for(), heartbeat=None)
    p.start()
    try:
        checks = doctor.run_checks(look(bridge_dir), **stubs)
        code, out = run_json(capsys, ["doctor"])
    finally:
        p.stop()
    found = by_name(checks)
    assert found["panel_heartbeat"]["status"] == "fail"
    assert "Window > Extensions" in found["panel_heartbeat"]["detail"]
    for name in STATE_CHECKS:
        assert found[name]["status"] == "warn" and "not checked" in found[name]["detail"]
    assert code == 1 and out["ok"] is False and out["code"] == "doctor_failed"
    assert out["summary"]["fail"] >= 1 and [c["name"] for c in out["checks"]] == NAMES
    assert p.seen == []


def test_premiere_26_is_a_warning(bridge_dir, stubs):  # noqa: F811
    p = FakePanel(bridge_dir, handler_for(state=STATE_26))
    p.start()
    try:
        checks = doctor.run_checks(look(bridge_dir), **stubs)
    finally:
        p.stop()
    found = by_name(checks)
    assert found["premiere_version"]["status"] == "warn"
    assert "26.5.2" in found["premiere_version"]["detail"] and "read only" in found["premiere_version"]["detail"]
    assert names(checks, "fail") == []


def test_own_project_is_a_warning(bridge_dir, stubs):  # noqa: F811
    p = FakePanel(bridge_dir, handler_for(state=STATE_OWN))
    p.start()
    try:
        checks = doctor.run_checks(look(bridge_dir), **stubs)
    finally:
        p.stop()
    found = by_name(checks)
    assert found["project_guard"]["status"] == "warn"
    assert "writes need --any-project and --project" in found["project_guard"]["detail"]
    assert found["projects"]["status"] == "pass"


def test_two_projects_and_no_project_are_warnings(bridge_dir, stubs):  # noqa: F811
    two = {**STATE_OK, "projects": STATE_OK["projects"] + [{"name": "B.prproj", "path": "/p/B.prproj", "active": False}]}
    for state in (two, {**STATE_OK, "projects": []}):
        p = FakePanel(bridge_dir, handler_for(state=state))
        p.start()
        try:
            found = by_name(doctor.run_checks(look(bridge_dir), **stubs))
        finally:
            p.stop()
        assert found["projects"]["status"] == "warn"


def test_dead_lease_is_reported_and_not_touched(bridge_dir, panel, stubs, capsys, cli_stubs):  # noqa: F811
    before = write_owner(bridge_dir, dead_pid())
    checks = doctor.run_checks(look(bridge_dir), **stubs)
    found = by_name(checks)
    assert found["lease"]["status"] == "fail"
    for name in STATE_CHECKS:
        assert found[name] == {"name": name, "status": "warn", "detail": "not checked, lease held by a dead process"}
    assert_untouched(bridge_dir, before)
    code, out = run_json(capsys, ["doctor"])
    assert code == 1 and out["ok"] is False
    view = doctor.recovery_view(look(bridge_dir))
    assert view["state"] is None and view["recovery_required"] is False
    assert_untouched(bridge_dir, before)
    assert panel.seen == []


def test_empty_lock_folder_is_an_invalid_record(bridge_dir, panel, stubs):  # noqa: F811
    (bridge_dir / LOCK).mkdir(mode=0o700)
    found = by_name(doctor.run_checks(look(bridge_dir), **stubs))
    assert found["lease"]["status"] == "fail"
    assert found["projects"]["detail"] == "not checked, lease record invalid"
    assert (bridge_dir / LOCK).is_dir() and list((bridge_dir / LOCK).iterdir()) == []
    assert not (bridge_dir / RECOVERY).exists()
    assert panel.seen == []


def test_live_lease_holder_skips_state_checks(bridge_dir, panel, stubs):  # noqa: F811
    before = write_owner(bridge_dir, os.getpid())
    checks = doctor.run_checks(look(bridge_dir), **stubs)
    found = by_name(checks)
    assert found["lease"]["status"] == "warn" and str(os.getpid()) in found["lease"]["detail"]
    for name in STATE_CHECKS:
        assert found[name] == {"name": name, "status": "warn", "detail": "not checked, lease busy"}
    assert_untouched(bridge_dir, before)
    assert doctor.recovery_view(look(bridge_dir))["state"] is None
    assert_untouched(bridge_dir, before)
    assert panel.seen == []


def test_lease_given_back_in_time_is_read(bridge_dir, panel, stubs, monkeypatch):  # noqa: F811
    monkeypatch.setattr(doctor, "LEASE_BUSY_WAIT_S", 3.0)
    write_owner(bridge_dir, os.getpid())
    lock = bridge_dir / LOCK

    def let_go():
        (lock / "owner.json").unlink()
        lock.rmdir()
    timer = threading.Timer(0.3, let_go)
    timer.start()
    try:
        checks = doctor.run_checks(look(bridge_dir), **stubs)
    finally:
        timer.join()
    assert names(checks, "pass") == NAMES, checks
    assert len(panel.seen) == 1 and STATE_MARK in panel.seen[0]
    assert not lock.exists() and not (bridge_dir / RECOVERY).exists()


def test_open_recovery_fails(bridge_dir, panel, stubs):  # noqa: F811
    mark_recovery(bridge_dir, "unknown_command_outcome")
    found = by_name(doctor.run_checks(look(bridge_dir), **stubs))
    assert found["recovery"]["status"] == "fail"
    assert "unknown_command_outcome" in found["recovery"]["detail"]
    assert "run: cli-anything-premiere recovery show" in found["recovery"]["detail"]


def test_unreadable_recovery_marker_fails(bridge_dir, panel, stubs):  # noqa: F811
    (bridge_dir / RECOVERY).write_text("{not json", encoding="utf-8")
    found = by_name(doctor.run_checks(look(bridge_dir), **stubs))
    assert found["recovery"]["status"] == "fail" and "cannot be read" in found["recovery"]["detail"]
    view = doctor.recovery_view(look(bridge_dir))
    assert view["recovery_required"] is True and view["reason"] == "unreadable"


def test_no_accessibility(bridge_dir, panel, stubs):  # noqa: F811
    stubs["osascript"] = Osascript(error=CliError("no_accessibility", ui.ACCESSIBILITY_MESSAGE))
    found = by_name(doctor.run_checks(look(bridge_dir), **stubs))
    assert found["accessibility"]["status"] == "fail"
    assert "Privacy & Security > Accessibility" in found["accessibility"]["detail"]
    assert '"Adobe Premiere Pro 2027"' in stubs["osascript"].sources[0]


def test_other_osascript_failure_is_a_warning(bridge_dir, panel, stubs):  # noqa: F811
    stubs["osascript"] = Osascript(error=CliError("ui_failed", "menu gone"))
    found = by_name(doctor.run_checks(look(bridge_dir), **stubs))
    assert found["accessibility"] == {"name": "accessibility", "status": "warn", "detail": "menu gone"}


def test_no_premiere_and_two_premieres(bridge_dir, panel, stubs):  # noqa: F811
    stubs["premiere"] = lambda: None
    found = by_name(doctor.run_checks(look(bridge_dir), **stubs))
    assert found["premiere_running"]["status"] == "fail"
    assert found["accessibility"] == {"name": "accessibility", "status": "warn",
                                      "detail": "not checked, Premiere is not running"}

    def two():
        raise CliError("two_premieres", "Two Premiere Pro apps are running.")
    stubs["premiere"] = two
    found = by_name(doctor.run_checks(look(bridge_dir), **stubs))
    assert found["premiere_running"] == {"name": "premiere_running", "status": "fail",
                                         "detail": "Two Premiere Pro apps are running."}
    assert stubs["osascript"].sources == []


def test_untested_panel_protocol_and_no_mcp_server(bridge_dir, stubs):  # noqa: F811
    p = FakePanel(bridge_dir, handler_for(), heartbeat={"protocolVersion": 2, "commandSchemaVersion": 1,
                                                        "receiptVersion": 1, "state": "running"})
    p.start()
    stubs["panel"] = lambda: None
    stubs["which"] = lambda name: None
    try:
        found = by_name(doctor.run_checks(look(bridge_dir), **stubs))
    finally:
        p.stop()
    assert found["bridge_protocol"]["status"] == "warn"
    assert found["panel_version"]["status"] == "warn"
    assert found["mcp_server"]["status"] == "warn" and "the mcp commands will not work" in found["mcp_server"]["detail"]


def test_missing_bridge_folder_never_raises(tmp_path, stubs):
    checks = doctor.run_checks(look(tmp_path / "missing"), **stubs)
    assert [c["name"] for c in checks] == NAMES
    found = by_name(checks)
    assert found["bridge_folder"]["status"] == "fail"
    assert found["panel_heartbeat"]["status"] == "fail"
    for name in ["bridge_protocol", "lease", "recovery"] + STATE_CHECKS:
        assert found[name]["status"] == "warn" and "not checked" in found[name]["detail"]
    assert not (tmp_path / "missing").exists()


def test_a_failed_state_read_is_a_warning(bridge_dir, stubs):  # noqa: F811
    p = FakePanel(bridge_dir, lambda script: json.dumps({"success": False, "error": "boom"}))
    p.start()
    try:
        found = by_name(doctor.run_checks(look(bridge_dir), **stubs))
        view = doctor.recovery_view(look(bridge_dir))
    finally:
        p.stop()
    for name in STATE_CHECKS:
        assert found[name]["status"] == "warn" and found[name]["detail"] == "not checked: boom"
    assert view["state"] is None
    assert not (bridge_dir / RECOVERY).exists() and not (bridge_dir / LOCK).exists()


# ---------- recovery show ----------
def test_recovery_show_without_recovery(bridge_dir, panel, capsys, cli_stubs):  # noqa: F811
    view = doctor.recovery_view(look(bridge_dir))
    assert view["recovery_required"] is False and view["reason"] is None and view["revision"] is None
    assert view["next"] == "No recovery is open."
    code, out = run_json(capsys, ["recovery", "show"])
    assert code == 0 and out["ok"] is True and out["recovery_required"] is False
    assert dispatch(["recovery", "show"]) == 0
    assert "No recovery is open." in capsys.readouterr().out


def test_recovery_show_with_recovery(bridge_dir, panel, capsys, cli_stubs):  # noqa: F811
    mark_recovery(bridge_dir, "abandoned_process_lease")
    marker = bridge_dir / RECOVERY
    before = (marker.read_bytes(), marker.stat().st_mtime_ns)
    code, out = run_json(capsys, ["recovery", "show"])
    assert code == 0 and out["ok"] is True
    assert out["recovery_required"] is True and out["reason"] == "abandoned_process_lease"
    assert re.fullmatch(r"[0-9a-f]{64}", out["revision"])
    assert out["state"]["video_clips"] == 5
    assert out["next"] == ("Check the state above. Then inspect and acknowledge with the MCP tools "
                           "inspect_operation_recovery and acknowledge_operation_recovery.")
    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before
    assert any(SUMMARY_MARK in s for s in panel.seen)
    assert not (bridge_dir / LOCK).exists()


def test_recovery_show_without_fresh_heartbeat_reads_nothing(bridge_dir, capsys, cli_stubs):  # noqa: F811
    p = FakePanel(bridge_dir, handler_for(), heartbeat=None)
    p.start()
    try:
        mark_recovery(bridge_dir, "unknown_command_outcome")
        view = doctor.recovery_view(look(bridge_dir))
    finally:
        p.stop()
    assert view["recovery_required"] is True and view["state"] is None
    assert p.seen == []


def test_the_scripts_are_the_shipped_ones():
    assert STATE_MARK in jsx.load("state") and SUMMARY_MARK in jsx.load("summary")
    assert STATE_MARK not in jsx.load("helpers") and SUMMARY_MARK not in jsx.load("helpers")


# ---------- status ----------
def test_status_keeps_its_keys_and_adds_live(capsys):
    code, data = run_json(capsys, ["status"])
    assert code == 0 and data["writes_projects"] is False and "46" in data["tested_project_formats"]
    assert "live" in data
    assert data["live"]["panel_heartbeat"] == "absent"
    assert data["live"]["bridge_dir"].endswith("not-the-real-bridge")
    assert dispatch(["status"]) == 0
    assert "heartbeat" in capsys.readouterr().out


def test_status_reports_a_fresh_heartbeat(bridge_dir, monkeypatch, capsys):  # noqa: F811
    monkeypatch.setenv("PREMIERE_TEMP_DIR", str(bridge_dir))
    p = FakePanel(bridge_dir, handler_for())
    p.start()
    try:
        code, data = run_json(capsys, ["status"])
    finally:
        p.stop()
    assert code == 0 and data["live"] == {"panel_heartbeat": "fresh", "bridge_dir": str(bridge_dir)}
    assert p.seen == [] and not (bridge_dir / LOCK).exists()


def test_status_survives_a_broken_bridge_module(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "cli_anything.premiere.core.bridge", None)
    code, data = run_json(capsys, ["status"])
    assert code == 0 and "error" in data["live"] and data["writes_projects"] is False


# ---------- fix round 1 ----------
def test_read_makes_one_lease_attempt(bridge_dir, panel, stubs, monkeypatch):  # noqa: F811
    # The look says free, but a live holder takes the lease before the acquire.
    before = write_owner(bridge_dir, os.getpid())
    monkeypatch.setattr(doctor, "_wait_while_live", lambda d: ("free", None, None))
    client = look(bridge_dir)
    start = time.monotonic()
    found = by_name(doctor.run_checks(client, **stubs))
    view = doctor.recovery_view(client)
    assert time.monotonic() - start < 1.0
    for name in STATE_CHECKS:
        assert found[name]["status"] == "warn"
        assert found[name]["detail"] == "not checked: Another program holds Premiere. Nothing was sent."
    assert view["state"] is None
    assert not client.lease().held
    assert_untouched(bridge_dir, before)
    assert panel.seen == []


def test_an_os_error_in_the_read_is_a_warning(bridge_dir, panel, stubs, monkeypatch):  # noqa: F811
    def broken(self, script, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(BridgeClient, "send", broken)
    found = by_name(doctor.run_checks(look(bridge_dir), **stubs))
    view = doctor.recovery_view(look(bridge_dir))
    for name in STATE_CHECKS:
        assert found[name] == {"name": name, "status": "warn", "detail": "not checked: disk full"}
    assert view["state"] is None
    assert not (bridge_dir / LOCK).exists() and not (bridge_dir / RECOVERY).exists()


@pytest.mark.parametrize("stop", [KeyboardInterrupt, bridge.Interrupted])
def test_an_interrupt_in_the_read_passes_through(bridge_dir, panel, stubs, monkeypatch, stop):  # noqa: F811
    def interrupted(self, script, **kw):
        raise stop()
    monkeypatch.setattr(BridgeClient, "send", interrupted)
    with pytest.raises(stop):
        doctor.run_checks(look(bridge_dir), **stubs)
    assert not (bridge_dir / LOCK).exists()


# ---------- final review fixes: A3 ----------
@pytest.mark.parametrize("shape", MARKER_SHAPES)
def test_a_marker_that_cannot_be_read_fails_doctor_and_shows_in_recovery_show(
        bridge_dir, panel, stubs, capsys, cli_stubs, shape):  # noqa: F811
    unreadable_marker(bridge_dir, shape)
    try:
        found = by_name(doctor.run_checks(look(bridge_dir), **stubs))
        code, out = run_json(capsys, ["doctor"])
        show_code, show = run_json(capsys, ["recovery", "show"])
    finally:
        restore_marker_mode(bridge_dir)
    assert found["recovery"]["status"] == "fail" and "cannot be read" in found["recovery"]["detail"]
    assert code == 1 and out["code"] == "doctor_failed"
    assert show_code == 0 and show["recovery_required"] is True and show["reason"] == "unreadable"


# ---------- final review fixes: A2 ----------
def test_a_holder_that_died_after_the_look_is_not_reaped(bridge_dir, panel, capsys, cli_stubs, monkeypatch):  # noqa: F811
    # The look says free; at the moment of the acquire a dead process holds the lease.
    before = write_owner(bridge_dir, dead_pid())
    monkeypatch.setattr(doctor, "_wait_while_live", lambda d: ("free", None, None))
    code, out = run_json(capsys, ["doctor"])
    found = by_name(out["checks"])
    for name in STATE_CHECKS:
        assert found[name]["status"] == "warn" and found[name]["detail"].startswith("not checked")
    assert_untouched(bridge_dir, before)
    code, view = run_json(capsys, ["recovery", "show"])
    assert code == 0 and view["state"] is None and view["recovery_required"] is False
    assert_untouched(bridge_dir, before)
    assert panel.seen == []


def test_doctor_refuses_a_client_that_could_reap(bridge_dir, stubs):  # noqa: F811
    reaping = BridgeClient(bridge_dir, lease_wait_s=0.0)
    with pytest.raises(RuntimeError):
        doctor.run_checks(reaping, **stubs)
    with pytest.raises(RuntimeError):
        doctor.recovery_view(reaping)
    assert not (bridge_dir / LOCK).exists()


def test_doctor_uses_the_public_helpers():
    source = open(doctor.__file__, encoding="utf-8").read()
    for private in ("bridge._read_owner", "bridge._pid_alive", "def _active", "def _file_name", "wait_s"):
        assert private not in source, private


# ---------- final review fixes: A1 ----------
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_an_interrupt_in_the_busy_wait_is_reported(bridge_dir, panel, capsys, cli_stubs, monkeypatch,  # noqa: F811
                                                   keep_signals, signum):
    monkeypatch.setattr(doctor, "LEASE_BUSY_WAIT_S", 5.0)
    before_owner = write_owner(bridge_dir, os.getpid())
    for argv in (["doctor"], ["recovery", "show"]):
        before = signal.getsignal(signum)
        start = time.monotonic()
        with signal_when(signum, lambda: signal.getsignal(signum) is not before) as sent:
            code, out = run_json(capsys, argv)
        assert sent["fired"], argv
        assert time.monotonic() - start < 4.0, argv
        assert code == 1 and out == {"ok": False, "code": "interrupted", "error": out["error"]}, argv
        assert "Nothing was changed" in out["error"]
    assert cli_stubs["osascript"].sources == []          # doctor stopped; it did not go on to osascript
    assert_untouched(bridge_dir, before_owner)
    assert panel.seen == []


@pytest.mark.parametrize("argv", [["doctor"], ["recovery", "show"]])
def test_an_interrupt_before_pickup_stops_the_read(bridge_dir, capsys, cli_stubs, keep_signals, argv):  # noqa: F811
    write_heartbeat(bridge_dir)                 # a fresh heartbeat and no panel: the read is never picked up
    with signal_when(signal.SIGTERM, lambda: bool(cmd_files(bridge_dir))) as sent:
        code, out = run_json(capsys, argv)
    assert sent["fired"]
    assert code == 1 and out["code"] == "interrupted"
    assert "Nothing was changed" in out["error"]
    assert cli_stubs["osascript"].sources == []
    assert cmd_files(bridge_dir) == [] and not (bridge_dir / LOCK).exists()
    assert not (bridge_dir / RECOVERY).exists()


def test_the_read_helper_stops_on_interrupted(bridge_dir, panel, monkeypatch):  # noqa: F811
    def stopped(self, script, **kw):
        raise CliError("interrupted", "Stopped before Premiere picked up the command. Nothing was changed.")
    monkeypatch.setattr(BridgeClient, "send", stopped)
    with pytest.raises(CliError) as e:
        doctor._read(look(bridge_dir), "state", "free")
    assert e.value.code == "interrupted"
    assert not (bridge_dir / LOCK).exists()


def test_an_ownerless_lock_says_what_to_do(bridge_dir, panel, stubs):  # noqa: F811
    (bridge_dir / LOCK).mkdir(mode=0o700)
    found = by_name(doctor.run_checks(look(bridge_dir), **stubs))
    detail = found["lease"]["detail"]
    assert found["lease"]["status"] == "fail"
    assert "delete" in detail and str(bridge_dir / LOCK) in detail
    assert (bridge_dir / LOCK).is_dir()
