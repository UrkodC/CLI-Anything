"""Tests for core/recipes/active.py (ActiveRun): the two-session pattern for MCP tools that act on the active
sequence. The scripts are answered by the fake panel with a TimelineModel, the MCP tools by the fake MCP server."""
from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from cli_anything.premiere.core import guard, mcp_client
from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.core.mcp_client import McpSession
from cli_anything.premiere.core.recipes import active
from cli_anything.premiere.core.recipes.active import ActiveRun
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.tests.fakes import (  # noqa: F401
    TimelineModel, FakePanel, bridge_dir, fake_mcp_command, keep_signals)

PROJECT = "/p/X Claude.prproj"
LOCK = ".host-owner-lease"
MARKER_FILE = ".host-recovery.json"
LOOK_BODY = "// recipe: place_read\nreturn __ok(null);"
TEXTS: list[str] = []          # every warning and error text of ActiveRun in this file (test_messages_name_no_export)


@pytest.fixture(autouse=True)
def _keep_signal_handlers(keep_signals):  # noqa: F811
    yield


@pytest.fixture(autouse=True)
def _no_real_bridge(monkeypatch, tmp_path):
    monkeypatch.setenv("PREMIERE_TEMP_DIR", str(tmp_path / "not-the-real-bridge"))
    monkeypatch.delenv("PREMIERE_MCP_CAPABILITIES", raising=False)
    monkeypatch.setattr(mcp_client, "CLOSE_WAIT_S", 0.3)
    monkeypatch.setattr(guard, "panel_version", lambda *a, **k: "1.25.0")
    monkeypatch.setattr(guard, "server_version", lambda *a, **k: "1.25.0")


@pytest.fixture
def model(tmp_path):
    timeline = TimelineModel(tmp_path / "timeline.json")
    timeline.add_item("item1", name="C0001.MP4")
    return timeline


@pytest.fixture
def run_parts(bridge_dir, tmp_path, model):  # noqa: F811
    """(client, make_session) on the temp bridge folder with the fake MCP server and the model file."""
    command = fake_mcp_command(tmp_path, bridge_dir, timeline=model.path)
    client = BridgeClient(bridge_dir, 5.0)
    return client, (lambda c: McpSession(c, command))


@contextlib.contextmanager
def panel_on(bridge_dir, model):  # noqa: F811
    panel = FakePanel(bridge_dir, handler=model.handler())
    panel.start()
    try:
        yield panel
    finally:
        panel.stop()


def new_run(run_parts, **kw) -> ActiveRun:
    client, make_session = run_parts
    return ActiveRun(client, make_session, sequence_id="s1", **kw)


def calls(tmp_path) -> list[dict]:
    path = Path(tmp_path) / "fake_mcp_calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def no_export_words(text: str) -> None:
    lowered = text.lower()
    assert "export" not in lowered and "frame" not in lowered, text


def kept(run: ActiveRun, *errors) -> None:
    """Every warning and error text of ActiveRun: it names no export and no frame."""
    texts = list(run.warnings) + [e.message for e in errors if e is not None]
    for text in texts:
        no_export_words(text)
    TEXTS.extend(texts)


def marker(bridge_dir) -> None:  # noqa: F811
    (Path(bridge_dir) / MARKER_FILE).write_text(json.dumps({"version": 1, "reason": "unknown_command_outcome",
                                                            "nonce": "test"}), encoding="utf-8")


# ---------- prepare ----------
def test_prepare_switches_and_confirms(run_parts, bridge_dir, model, tmp_path):  # noqa: F811
    seen_at_check = []

    def check(session):
        seen_at_check.append(list(model.writes))
        return "checked"

    def steps(mcp, guard_args):
        assert guard_args == {"expected_project_path": PROJECT,
                              "expected_cep_target": {"backend": "cep", "sequence_id": "s1"}}
        reply = mcp.call("set_item_in_out", {"item_id": "item1", "in_seconds": 1.0, "out_seconds": 3.0, **guard_args},
                         timeout_s=run.call_timeout_s)
        assert reply["is_error"] is False

    run = new_run(run_parts)
    with panel_on(bridge_dir, model) as panel:
        assert run.prepare(check, switch=True) == "checked"
        assert seen_at_check == [[]]                     # check() ran before any switch
        assert model.writes == [("activate", "s1")]
        assert model.load()["active"] == "s1"
        # The confirming state read comes after the activate write.
        order = ["activate" if "recipe: app_switch" in s and '"activate"' in s else
                 "state" if "playhead_ticks: playhead" in s else "other" for s in panel.seen]
        assert "state" in order[order.index("activate") + 1:]
        assert run.sequence == {"id": "s1", "name": "Main"} and run.project_path == PROJECT
        assert run.mcp(steps) is None
        assert run.finish() is None
    assert model.writes == [("activate", "s1"), ("activate", "o1")]
    assert model.load()["active"] == "o1" and run.left_switched is None and run.stopped is False
    found = calls(tmp_path)
    assert [c["tool"] for c in found] == ["set_item_in_out"] and found[0]["lease_held"] is False
    assert model.item("item1")["in_ticks"] > 0                         # the server wrote the model file
    assert not (Path(bridge_dir) / LOCK).exists()
    kept(run)


def test_prepare_on_the_active_target_writes_nothing_and_still_confirms(run_parts, bridge_dir, model):  # noqa: F811
    model.activate("s1")
    run = new_run(run_parts)
    with panel_on(bridge_dir, model) as panel:
        run.prepare(lambda s: None, switch=True)
        states = sum("playhead_ticks: playhead" in s for s in panel.seen)
        assert run.mcp(lambda mcp, g: None) is None
        assert run.finish() is None
    assert model.writes == [] and states >= 2         # the session's guard read and the confirming read
    assert run.left_switched is None


def test_prepare_without_switch_allows_no_mcp(run_parts, bridge_dir, model):  # noqa: F811
    run = new_run(run_parts)
    touched = []
    with panel_on(bridge_dir, model):
        run.prepare(lambda s: None, switch=False)
        err = run.mcp(lambda mcp, g: touched.append(1))
        assert run.finish() is None
    assert err is not None and err.code == "recipe_error" and touched == []
    assert model.writes == []
    kept(run, err)


def test_switch_may_be_decided_by_the_check(run_parts, bridge_dir, model):  # noqa: F811
    run = new_run(run_parts)
    with panel_on(bridge_dir, model):
        assert run.prepare(lambda s: {"todo": 0}, switch=lambda result: result["todo"] > 0) == {"todo": 0}
        assert run.finish() is None
    assert model.writes == []


def test_fault_after_the_switch_is_put_back(run_parts, bridge_dir, model, monkeypatch):  # noqa: F811
    def broken(*a, **k):
        raise RuntimeError("the confirm step broke")
    monkeypatch.setattr(active, "guarded_state", broken)
    run = new_run(run_parts)
    touched = []
    with panel_on(bridge_dir, model):
        with pytest.raises(CliError) as caught:
            run.prepare(lambda s: None, switch=True)
        err = run.mcp(lambda mcp, g: touched.append(1))
        assert run.finish() is None
    assert caught.value.code == "recipe_error" and "the confirm step broke" in caught.value.message
    assert model.writes == [("activate", "s1"), ("activate", "o1")] and model.load()["active"] == "o1"
    assert err is not None and touched == []
    assert run.left_switched is None
    kept(run, caught.value, err)


def test_a_switch_that_does_not_show_is_refused_and_put_back(run_parts, bridge_dir, model, monkeypatch):  # noqa: F811
    real = active.guarded_state

    def shows_old(*a, **k):
        state, warnings = real(*a, **k)
        return {**state, "active_sequence": {"id": "o1", "name": "Old"}}, warnings
    monkeypatch.setattr(active, "guarded_state", shows_old)
    run = new_run(run_parts)
    with panel_on(bridge_dir, model):
        with pytest.raises(CliError) as caught:
            run.prepare(lambda s: None, switch=True)
        run.finish()
    assert caught.value.code == "not_switched" and "Nothing was placed or applied." in caught.value.message
    # The confirming read showed the old sequence, so (as in frames.py) there is nothing left to put back.
    assert model.writes == [("activate", "s1")] and run.left_switched is None
    kept(run, caught.value)


# ---------- the MCP session ----------
def test_mcp_returns_a_stop_and_a_fault(run_parts, bridge_dir, model):  # noqa: F811
    run = new_run(run_parts)
    with panel_on(bridge_dir, model):
        run.prepare(lambda s: None, switch=True)

        def fault(mcp, guard_args):
            raise ValueError("a fault in the steps")
        err = run.mcp(fault)
        assert err.code == "recipe_error" and "a fault in the steps" in err.message and run.stopped is False

        def stop(mcp, guard_args):
            raise KeyboardInterrupt
        stopped = run.mcp(stop)
        assert stopped.code == "interrupted" and run.stopped is True
        assert run.finish() is None
    assert model.load()["active"] == "o1"           # a stop during the MCP session: the put-back is still tried
    kept(run, err, stopped)


# ---------- look ----------
def test_look_works_with_a_marker(run_parts, bridge_dir, model):  # noqa: F811
    run = new_run(run_parts)
    with panel_on(bridge_dir, model) as panel:
        run.prepare(lambda s: None, switch=True)
        marker(bridge_dir)
        writes = list(model.writes)
        reply, shown = run.look(LOOK_BODY, {"items": ["item1"], "spans": []})
        sent = [s for s in panel.seen if "recipe: place_read" in s]
    assert shown is True and reply["sequence"]["id"] == "s1"
    assert reply["items"][0]["node_id"] == "item1"
    assert model.writes == writes
    assert any("recovery" in w for w in run.warnings)
    assert '"expect": {"project_path": "/p/X Claude.prproj", "sequence_id": "s1"' in sent[-1]
    assert not (Path(bridge_dir) / LOCK).exists()
    kept(run)


def test_look_reports_a_moved_sequence(run_parts, bridge_dir, model):  # noqa: F811
    run = new_run(run_parts)
    with panel_on(bridge_dir, model):
        run.prepare(lambda s: None, switch=True)
        model.activate("o1")
        reply, shown = run.look(LOOK_BODY, {"items": [], "spans": []})
        assert shown is False and reply["sequence"]["id"] == "s1"
        assert run.finish() is None
    # Someone else changed the active sequence during the run: it is left as it is, with a warning.
    assert model.writes == [("activate", "s1")]
    assert run.left_switched is None
    assert any("changed during the run" in w for w in run.warnings)
    kept(run)


# ---------- finish ----------
def test_finish_with_a_marker_leaves_switched(run_parts, bridge_dir, model):  # noqa: F811
    run = new_run(run_parts)
    with panel_on(bridge_dir, model):
        run.prepare(lambda s: None, switch=True)
        marker(bridge_dir)
        assert run.finish() is None
        assert run.finish() is None                     # once only
    assert model.writes == [("activate", "s1")] and model.load()["active"] == "s1"
    assert run.left_switched == {"proxies": None, "active_sequence": {"id": "s1", "name": "Main"}}
    line = next(w for w in run.warnings if "may still be the active sequence" in w)
    assert "cli-anything-premiere live app activate --sequence o1" in line
    assert "recovery" in line
    kept(run)


def test_finish_with_nothing_switched_opens_no_session(run_parts, bridge_dir, model):  # noqa: F811
    run = new_run(run_parts)
    with panel_on(bridge_dir, model) as panel:
        run.prepare(lambda s: None, switch=False)
        before = len(panel.seen)
        assert run.finish() is None
        assert len(panel.seen) == before
    assert run.left_switched is None


def test_messages_name_no_export(run_parts, bridge_dir, model, monkeypatch):  # noqa: F811
    # Each test above checks its own texts (kept). This one runs the texts that need a fault or a marker by itself.
    run = new_run(run_parts)
    with panel_on(bridge_dir, model):
        run.prepare(lambda s: None, switch=True)
        marker(bridge_dir)
        not_now = run.mcp(lambda mcp, g: None)
        model.activate("o1")
        run.finish()
        late = run.mcp(lambda mcp, g: None)
    kept(run, not_now, late)
    (Path(bridge_dir) / MARKER_FILE).unlink()
    other = new_run(run_parts)
    with panel_on(bridge_dir, model):
        early = other.mcp(lambda mcp, g: None)
        with pytest.raises(CliError) as caught:
            other.look(LOOK_BODY, {})
    kept(other, early, caught.value)
    for text in TEXTS:
        no_export_words(text)
