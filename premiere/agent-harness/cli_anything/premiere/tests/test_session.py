"""Unit tests for the write session (core/session.py), against a fake panel in a temp folder."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess

import pytest

from cli_anything.premiere.core import bridge, guard, session
from cli_anything.premiere.core.bridge import BridgeClient, mark_recovery
from cli_anything.premiere.core.session import WriteSession
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.tests.fakes import (  # noqa: F401
    FakePanel, bridge_dir, keep_signals)

LOCK = ".host-owner-lease"
STATE_MARK = "playhead_ticks: playhead"   # occurs in state.jsx only
FIND_MARK = "// find.jsx: target finders for recipe scripts"
PROJECT = "/p/X Claude.prproj"
STATE_OK = {"version": "27.0.0", "build": "88", "projects": [{"name": "X Claude.prproj", "path": PROJECT, "active": True}],
            "active_sequence": {"id": "s1", "name": "Main"}, "playhead_ticks": "0", "selection": []}
STATE_OWN = {**STATE_OK, "projects": [{"name": "Family film.prproj", "path": "/p/Family film.prproj", "active": True}]}
STATE_OTHER = {**STATE_OK, "projects": [{"name": "Y Claude.prproj", "path": "/p/Y Claude.prproj", "active": True}]}
BODY = "return __ok({answer: 42});"
NODE = shutil.which("node")


def ok(data) -> str:
    return json.dumps({"success": True, "data": data})


def handler_for(state=STATE_OK, body_reply=None):
    """State script -> `state`; any other script -> `body_reply` (raw reply text; None never answers)."""
    body_reply = ok({"answer": 42}) if body_reply is None else body_reply
    states = state if isinstance(state, list) else [state]
    calls = {"n": 0}

    def handler(script: str) -> str | None:
        if STATE_MARK in script:
            current = states[min(calls["n"], len(states) - 1)]
            calls["n"] += 1
            return ok(current)
        return None if body_reply == "never" else body_reply
    return handler


@pytest.fixture(autouse=True)
def _keep_signal_handlers(keep_signals):  # noqa: F811
    yield


@pytest.fixture(autouse=True)
def _tested_versions(monkeypatch):
    monkeypatch.setattr(guard, "panel_version", lambda *a, **k: "1.25.0")
    monkeypatch.setattr(guard, "server_version", lambda *a, **k: "1.25.0")


@pytest.fixture
def client(bridge_dir):  # noqa: F811
    return BridgeClient(bridge_dir, 2.0)


@contextlib.contextmanager
def panel_on(bridge_dir, **kw):  # noqa: F811
    panel = FakePanel(bridge_dir, **kw)
    panel.start()
    try:
        yield panel
    finally:
        panel.stop()


def states_seen(panel) -> int:
    return sum(STATE_MARK in s for s in panel.seen)


def lock_held(bridge_dir) -> bool:  # noqa: F811
    return (bridge_dir / LOCK).is_dir()


def args_of(script: str) -> dict:
    line = next(x for x in script.split("\n") if x.startswith("var ARGS = "))
    return json.loads(line[len("var ARGS = "):-1])


# ---------- enter ----------
def test_enter_runs_the_guards_before_any_write(client, bridge_dir):  # noqa: F811
    with panel_on(bridge_dir, handler=handler_for(STATE_OWN)) as panel:
        with pytest.raises(CliError) as err:
            with WriteSession(client):
                raise AssertionError("the body must not run")
    assert err.value.code == "project_guard"
    assert len(panel.seen) == 1 and STATE_MARK in panel.seen[0]
    assert not lock_held(bridge_dir) and not client.lease().held


def test_enter_fills_state_and_expect_and_holds_the_lease(client, bridge_dir):  # noqa: F811
    with panel_on(bridge_dir, handler=handler_for()):
        with WriteSession(client) as s:
            assert lock_held(bridge_dir)
            assert s.state == STATE_OK and s.warnings == []
            assert s.expect == {"project_path": PROJECT, "sequence_id": None, "sequence_name": None}
            s.target("s1", "Main")
            assert s.expect == {"project_path": PROJECT, "sequence_id": "s1", "sequence_name": "Main"}
    assert not lock_held(bridge_dir)


def test_enter_without_a_heartbeat_takes_nothing(client, bridge_dir):  # noqa: F811
    with pytest.raises(CliError) as err:
        with WriteSession(client):
            pass
    assert err.value.code == "bridge_down"
    assert not lock_held(bridge_dir)


# ---------- write ----------
def test_write_refuses_on_a_marker(client, bridge_dir):  # noqa: F811
    with panel_on(bridge_dir, handler=handler_for()) as panel:
        with WriteSession(client) as s:
            mark_recovery(bridge_dir, "unknown_command_outcome")
            with pytest.raises(CliError) as err:
                s.write(BODY)
    assert err.value.code == "recovery_required"
    assert len(panel.seen) == 1 and STATE_MARK in panel.seen[0]


def test_write_adds_the_expectation(client, bridge_dir):  # noqa: F811
    with panel_on(bridge_dir, handler=handler_for()) as panel:
        with WriteSession(client) as s:
            s.target("s1", "Main")
            assert s.write(BODY, {"k": 1}) == {"answer": 42}
            assert s.read(BODY) == {"answer": 42}
    written, read = panel.seen[1], panel.seen[2]
    assert '"project_path": "/p/X Claude.prproj"' in written and '"sequence_id": "s1"' in written
    assert args_of(written) == {"k": 1, "expect": {"project_path": PROJECT, "sequence_id": "s1", "sequence_name": "Main"}}
    assert args_of(read)["expect"]["sequence_id"] == "s1"
    assert FIND_MARK in written and FIND_MARK in read


def test_write_sends_as_a_write_and_read_as_a_read(client, bridge_dir, monkeypatch):  # noqa: F811
    flags = []
    original = BridgeClient.send

    def spy(self, script, *, write, **kw):
        flags.append(write)
        return original(self, script, write=write, **kw)
    monkeypatch.setattr(BridgeClient, "send", spy)
    with panel_on(bridge_dir, handler=handler_for()):
        with WriteSession(client) as s:
            s.write(BODY)
            s.read(BODY)
    assert flags == [False, True, False]        # the state script is always a read


def test_caller_cannot_pass_its_own_expect(client, bridge_dir):  # noqa: F811
    with panel_on(bridge_dir, handler=handler_for()) as panel:
        with WriteSession(client) as s:
            for call in (s.write, s.read):
                with pytest.raises(CliError) as err:
                    call(BODY, {"expect": {"project_path": "/elsewhere.prproj"}})
                assert err.value.code == "bad_args"
            with pytest.raises(CliError) as err:
                s.write(BODY, ["not", "a", "dict"])
            assert err.value.code == "bad_args"
    assert len(panel.seen) == 1


@pytest.mark.parametrize("error", ["precondition: sequence name differs", "Error: precondition: sequence name differs"])
def test_precondition_failure_has_its_own_code(client, bridge_dir, error):  # noqa: F811
    reply = json.dumps({"success": False, "error": error})
    with panel_on(bridge_dir, handler=handler_for(body_reply=reply)):
        with WriteSession(client) as s:
            for call in (s.write, s.read):
                with pytest.raises(CliError) as err:
                    call(BODY)
                assert (err.value.code, err.value.message) == ("precondition", "sequence name differs")
    assert bridge.read_recovery(bridge_dir) is None


def test_other_script_failures_stay_script_failed(client, bridge_dir):  # noqa: F811
    reply = json.dumps({"success": False, "error": "Error: boom (line 3)"})
    with panel_on(bridge_dir, handler=handler_for(body_reply=reply)):
        with WriteSession(client) as s:
            with pytest.raises(CliError) as err:
                s.write(BODY)
    assert (err.value.code, err.value.message) == ("script_failed", "Error: boom (line 3)")


def test_run_script_works_without_a_session(client, bridge_dir):  # noqa: F811
    reply = json.dumps({"success": False, "error": "precondition: no sequence has the ID s9"})
    with panel_on(bridge_dir, handler=handler_for(body_reply=reply)) as panel:
        with client.lease():
            with pytest.raises(CliError) as err:
                session.run_script(client, BODY, {"a": 1}, write=False, include=("find",))
    assert (err.value.code, err.value.message) == ("precondition", "no sequence has the ID s9")
    assert FIND_MARK in panel.seen[0] and args_of(panel.seen[0]) == {"a": 1}


# ---------- the whole chain, with find.jsx running in node ----------
def node_handler(prelude: str):
    """A panel that answers the state script with STATE_OK and runs every other script in node."""
    def handler(script: str) -> str:
        if STATE_MARK in script:
            return ok(STATE_OK)
        code = prelude + "\nprocess.stdout.write(String(" + script + "));"
        return subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout
    return handler


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_a_switched_project_stops_the_write_inside_the_script(client, bridge_dir):  # noqa: F811
    # Premiere switched to another project after the guard read: the script refuses before any edit.
    prelude = "var edits = 0; var app = {project: {path: '/p/Other Claude.prproj', sequences: {numSequences: 0}}};"
    body = "__expect(ARGS.expect);\nedits++;\nreturn __ok(edits);"
    with panel_on(bridge_dir, handler=node_handler(prelude)):
        with WriteSession(client) as s:
            with pytest.raises(CliError) as err:
                s.write(body)
    assert err.value.code == "precondition"
    assert err.value.message == "the active project is /p/Other Claude.prproj, not /p/X Claude.prproj"
    assert bridge.read_recovery(bridge_dir) is None


# ---------- breathe ----------
def test_breathe_guards_again(client, bridge_dir, monkeypatch):  # noqa: F811
    with panel_on(bridge_dir, handler=handler_for()) as panel:
        with WriteSession(client) as s:
            assert s.breathe() is False and states_seen(panel) == 1
            monkeypatch.setattr(bridge, "MAX_HOLD_S", 0.0)
            assert s.breathe() is True
            assert states_seen(panel) == 2 and lock_held(bridge_dir)
            mark_recovery(bridge_dir, "unknown_command_outcome")
            with pytest.raises(CliError) as err:
                s.breathe()
            assert err.value.code == "recovery_required"
            assert not lock_held(bridge_dir) and not client.lease().held
            for call in (lambda: s.write(BODY), lambda: s.read(BODY), s.breathe, s.paused):
                with pytest.raises(RuntimeError):
                    call()
    assert not lock_held(bridge_dir)


def test_breathe_that_cannot_take_the_lease_again_closes_the_session(client, bridge_dir, monkeypatch):  # noqa: F811
    with panel_on(bridge_dir, handler=handler_for()):
        with WriteSession(client) as s:
            monkeypatch.setattr(bridge, "MAX_HOLD_S", 0.0)

            def busy():
                raise CliError("lease_busy", "Another program holds Premiere. Nothing was sent.")
            monkeypatch.setattr(client.lease(), "acquire", busy)
            with pytest.raises(CliError) as err:
                s.breathe()
            assert err.value.code == "lease_busy"
            with pytest.raises(RuntimeError):
                s.write(BODY)
    assert not lock_held(bridge_dir)


def test_breathe_refuses_another_active_project(client, bridge_dir, monkeypatch):  # noqa: F811
    with panel_on(bridge_dir, handler=handler_for([STATE_OK, STATE_OTHER])):
        with WriteSession(client) as s:
            monkeypatch.setattr(bridge, "MAX_HOLD_S", 0.0)
            with pytest.raises(CliError) as err:
                s.breathe()
            assert err.value.code == "project_mismatch"
            assert s.expect["project_path"] == PROJECT
    assert not lock_held(bridge_dir)


# ---------- paused ----------
def test_paused_gives_the_lease_back_and_guards_again(client, bridge_dir):  # noqa: F811
    with panel_on(bridge_dir, handler=handler_for()) as panel:
        with WriteSession(client) as s:
            with s.paused():
                assert not lock_held(bridge_dir) and not client.lease().held
                with pytest.raises(RuntimeError):
                    s.write(BODY)
            assert lock_held(bridge_dir) and states_seen(panel) == 2
            assert s.write(BODY) == {"answer": 42}
    assert not lock_held(bridge_dir)


def test_paused_with_a_marker_on_return_closes_the_session(client, bridge_dir):  # noqa: F811
    with panel_on(bridge_dir, handler=handler_for()):
        with WriteSession(client) as s:
            with pytest.raises(CliError) as err:
                with s.paused():
                    mark_recovery(bridge_dir, "unknown_command_outcome")
            assert err.value.code == "recovery_required"
            assert not lock_held(bridge_dir) and not client.lease().held
            with pytest.raises(RuntimeError):
                s.read(BODY)
    assert not lock_held(bridge_dir)


def test_paused_when_another_program_keeps_the_lease(bridge_dir):  # noqa: F811
    client = BridgeClient(bridge_dir, 0.2)
    other = bridge.Lease(bridge_dir, wait_s=1.0)
    with panel_on(bridge_dir, handler=handler_for()):
        with WriteSession(client) as s:
            with pytest.raises(CliError) as err:
                with s.paused():
                    other.acquire()
            assert err.value.code == "lease_busy"
            assert not client.lease().held
            with pytest.raises(RuntimeError):
                s.write(BODY)
    assert lock_held(bridge_dir)            # the other program's lease, untouched
    other.release()


def test_paused_body_error_does_not_take_the_lease_again(client, bridge_dir):  # noqa: F811
    with panel_on(bridge_dir, handler=handler_for()) as panel:
        with WriteSession(client) as s:
            with pytest.raises(ValueError):
                with s.paused():
                    raise ValueError("the MCP call failed")
            assert not lock_held(bridge_dir) and states_seen(panel) == 1
            with pytest.raises(RuntimeError):
                s.write(BODY)
    assert not lock_held(bridge_dir)


# ---------- signals and the lock ----------
def test_session_never_calls_send_inside_a_lock_section(client, bridge_dir, monkeypatch):  # noqa: F811
    depths = []
    original = BridgeClient.send

    def spy(self, script, *, write, **kw):
        depths.append(bridge._Held.depth)
        return original(self, script, write=write, **kw)
    monkeypatch.setattr(BridgeClient, "send", spy)
    monkeypatch.setattr(bridge, "MAX_HOLD_S", 0.0)
    with panel_on(bridge_dir, handler=handler_for()):
        with WriteSession(client) as s:
            s.write(BODY)
            s.read(BODY)
            s.breathe()
            with s.paused():
                pass
            s.write(BODY)
    assert len(depths) == 6 and set(depths) == {0}


def test_marker_write_failure_keeps_the_lock(bridge_dir, monkeypatch):  # noqa: F811
    client = BridgeClient(bridge_dir, 2.0)
    lock = bridge_dir / LOCK

    def cannot_write(folder, reason):
        raise OSError(28, "No space left on device")
    with panel_on(bridge_dir, handler=handler_for(body_reply="never")):
        with WriteSession(client, hard_limit_s=0.5) as s:
            monkeypatch.setattr(bridge, "mark_recovery", cannot_write)
            with pytest.raises(CliError) as err:
                s.write(BODY)
            with pytest.raises(RuntimeError):
                s.write(BODY)
    assert err.value.code == "unknown_outcome"
    assert "The recovery marker could not be written, so the lock is kept." in err.value.message
    assert f"Delete {lock} after you checked Premiere." in err.value.message
    assert lock.is_dir() and (lock / "owner.json").is_file()
    assert client.lease().retained
    assert bridge.read_owner(lock / "owner.json")[0] == os.getpid()
    shutil.rmtree(lock)
