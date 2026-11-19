"""Tests for `live effect apply`: the recipe (core/recipes/apply.py) and the command (live_recipes.py).

The clip_list script is answered by a fake panel that plays a small Premiere timeline in Python
(fakes.TimelineModel), and apply_effect by the fake MCP server of fakes.py, which changes the same model file. As
measured, a new component lands at index 2, after Opacity and Motion and before older effects.
"""
from __future__ import annotations

import contextlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli_anything.premiere import live_cli
from cli_anything.premiere.core import guard, mcp_client
from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.core.mcp_client import McpSession
from cli_anything.premiere.core.recipes import apply
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.premiere_cli import dispatch
from cli_anything.premiere.tests.fakes import (  # noqa: F401
    FakePanel, TimelineModel, bridge_dir, fake_mcp_command, keep_signals)

PROJECT = "/p/X Claude.prproj"
GUARD = {"expected_project_path": PROJECT, "expected_cep_target": {"backend": "cep", "sequence_id": "s1"}}
LOCK = ".host-owner-lease"
MARKER_FILE = ".host-recovery.json"
BASE = ["Opacity", "Motion"]


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


class Wrapped:
    """An McpSession with a hook: before_call(name, arguments, number) runs before each call."""

    def __init__(self, inner, before_call=None) -> None:
        self.inner, self.before_call, self.count = inner, before_call, 0

    def __enter__(self):
        self.inner.__enter__()
        return self

    def __exit__(self, *exc):
        self.inner.__exit__(*exc)

    def call(self, name, arguments, timeout_s=120.0):
        self.count += 1
        if self.before_call is not None:
            self.before_call(name, arguments, self.count)
        return self.inner.call(name, arguments, timeout_s=timeout_s)


@pytest.fixture
def live(bridge_dir, monkeypatch, tmp_path, model):  # noqa: F811
    command = fake_mcp_command(tmp_path, bridge_dir, timeline=model.path)
    state = SimpleNamespace(sessions=0, wrap=None)

    def make_session(client):
        state.sessions += 1
        session = McpSession(client, command)
        return session if state.wrap is None else state.wrap(state.sessions, session)
    monkeypatch.setattr(live_cli, "make_client", lambda wait, **kw: BridgeClient(bridge_dir, wait, **kw))
    monkeypatch.setattr(live_cli, "make_session", make_session)
    return state


@contextlib.contextmanager
def panel_on(bridge_dir, model):  # noqa: F811
    panel = FakePanel(bridge_dir, handler=model.handler())
    panel.start()
    try:
        yield panel
    finally:
        panel.stop()


def mcp_calls(tmp_path) -> list[dict]:
    path = Path(tmp_path) / "fake_mcp_calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_apply(capsys, bridge_dir, model, *argv, json_out=True):  # noqa: F811
    with panel_on(bridge_dir, model):
        code = dispatch((["--json"] if json_out else []) + ["live", "effect", "apply", "--sequence", "s1", *argv])
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if json_out and captured.out.strip() else captured)


def clips_on(model, count: int, effects=None) -> None:
    for n in range(count):
        model.add_clip("item1", node_id=f"c{n}", track=0, start_f=48 * n, frames=48,
                       effects=None if effects is None else BASE + list(effects.get(n, [])))


def effects_of(model, node_id: str) -> list[str]:
    return next(c["effects"] for c in model.clips() if c["node_id"] == node_id)


def statuses(out: dict) -> list[str]:
    return [r["status"] for r in out["results"]]


TWO = ["--effect", "ProcAmp", "--effect", "Lumetri Color"]


# ---------- the runs ----------
def test_procamp_then_lumetri(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 2)
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0,1", *TWO)
    assert code == 0, out
    assert out["ok"] is True and statuses(out) == ["done"] * 4 and out["stored"] is True
    found = mcp_calls(tmp_path)
    assert [(c["tool"], c["arguments"]["node_id"], c["arguments"]["effect_name"]) for c in found] == [
        ("apply_effect", "c0", "ProcAmp"), ("apply_effect", "c0", "Lumetri Color"),
        ("apply_effect", "c1", "ProcAmp"), ("apply_effect", "c1", "Lumetri Color")]
    assert all(c["arguments"] == {"node_id": c["arguments"]["node_id"], "effect_name": c["arguments"]["effect_name"],
                                  **GUARD} for c in found)
    assert [r["effect_index"] for r in out["results"]] == [3, 2, 3, 2]
    assert [r["now"]["effect_index"] for r in out["results"]] == [3, 2, 3, 2]
    assert [r["effect"] for r in out["results"]] == ["ProcAmp", "Lumetri Color"] * 2
    assert effects_of(model, "c0") == BASE + ["Lumetri Color", "ProcAmp"]
    assert out["results"][0]["target"] == {"track": 0, "clip": 0, "node_id": "c0", "effect": "ProcAmp"}
    assert all(r["warnings"] == [] for r in out["results"])
    assert live.sessions == 1 and out["first_not_run"] is None


def test_order_rule(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 1)
    reverse = ["--effect", "Lumetri Color", "--effect", "ProcAmp"]
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", *reverse)
    assert code == 1 and out["code"] == "bad_order", out
    assert "--any-order" in out["error"] and "Nothing was sent." in out["error"]
    assert mcp_calls(tmp_path) == [] and model.writes == [] and live.sessions == 0
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", *reverse, "--any-order")
    assert code == 0, out
    assert statuses(out) == ["done", "done"]
    assert [c["arguments"]["effect_name"] for c in mcp_calls(tmp_path)] == ["Lumetri Color", "ProcAmp"]
    assert effects_of(model, "c0") == BASE + ["ProcAmp", "Lumetri Color"]


def test_order_rule_needs_no_contact():
    with pytest.raises(CliError) as caught:
        apply.apply_effects(None, None, sequence_id="s1", track=0, clips=[0], effects=["Lumetri Color", "ProcAmp"])
    assert caught.value.code == "bad_order"
    for effects in ([], ["ProcAmp", "ProcAmp"], ["ProcAmp", ""], ["ProcAmp", 3]):
        with pytest.raises(CliError) as caught:
            apply.apply_effects(None, None, sequence_id="s1", track=0, clips=[0], effects=effects)
        assert caught.value.code == "bad_args", effects
    for clips in ([0, 0], [-1], ["1"], []):
        with pytest.raises(CliError) as caught:
            apply.apply_effects(None, None, sequence_id="s1", track=0, clips=clips, effects=["ProcAmp"])
        assert caught.value.code == "bad_args", clips
    with pytest.raises(CliError) as caught:
        apply.apply_effects(None, None, sequence_id="s1", track=-1, clips=None, effects=["ProcAmp"])
    assert caught.value.code == "bad_args"
    assert apply.ORDER_RULE == ("ProcAmp", "Lumetri Color")
    assert apply.GYROFLOW_NOTE == "Gyroflow gets its defaults only. Its sliders cannot be set by a script."


def test_has_effect_is_skipped(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 1, effects={0: ["ProcAmp"]})
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", "--effect", "ProcAmp")
    assert code == 0, out
    record = out["results"][0]
    assert (record["status"], record["skip"], record["reason_code"]) == ("skipped", "nothing_to_do", "has_effect")
    assert "has effect" in record["reason"] and record["effect_index"] == 2
    assert mcp_calls(tmp_path) == [] and live.sessions == 0
    assert model.writes == []                                 # nothing to apply: the active sequence is not switched
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", "--effect", "ProcAmp",
                          "--allow-second")
    assert code == 0, out
    assert statuses(out) == ["done"] and out["results"][0]["effect_index"] == 2
    assert effects_of(model, "c0") == BASE + ["ProcAmp", "ProcAmp"]
    assert len(mcp_calls(tmp_path)) == 1


def test_rerun_finishes_a_partial_run(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 1, effects={0: ["ProcAmp"]})
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", *TWO)
    assert code == 0, out
    assert statuses(out) == ["skipped", "done"]
    assert [c["arguments"]["effect_name"] for c in mcp_calls(tmp_path)] == ["Lumetri Color"]
    # The old ProcAmp moved from index 2 to 3 when Lumetri Color landed: the index comes from the read after.
    assert [r["effect_index"] for r in out["results"]] == [3, 2]
    assert out["results"][0]["warnings"] == [] and out["results"][1]["warnings"] == []


def test_existing_effect_order_warning(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 1, effects={0: ["Lumetri Color"]})
    # The warning is there in a dry run, before anything is sent.
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", "--effect", "ProcAmp",
                          "--dry-run")
    assert code == 0 and mcp_calls(tmp_path) == []
    assert any("Lumetri Color" in w and "ProcAmp" in w for w in out["planned"][0]["warnings"])
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", "--effect", "ProcAmp")
    assert code == 0, out
    record = out["results"][0]
    assert record["status"] == "done" and record["effect_index"] == 2
    assert any("Lumetri Color" in w and "ProcAmp" in w and "render" in w for w in record["warnings"]), record


def test_skipped_lumetri_and_new_procamp_is_predicted(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    # The clip holds Lumetri Color; ProcAmp lands after it in render order, so the request order is not met. It was
    # predicted in session A, so the entry is done with a warning, not failed.
    clips_on(model, 1, effects={0: ["Lumetri Color"]})
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", *TWO)
    assert code == 0, out
    assert statuses(out) == ["done", "skipped"]
    assert out["results"][0]["warnings"]


def test_unpredicted_render_order_fails(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 1, effects={0: ["Old"]})
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", "--effect", "XAPPEND")
    assert code == 1 and out["code"] == "not_done", out
    record = out["results"][0]
    assert record["status"] == "failed" and record["reason_code"] == "render_order"
    assert record["effect_index"] == 3 and "XAPPEND" in record["error"]


def test_failed_call_checked_by_read(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 2)
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", "--effect", "ProcAmp",
                          "--effect", "QEADD Fx")
    assert code == 0, out
    assert statuses(out) == ["done", "done"]
    assert any("QE may have added" in w for w in out["results"][1]["warnings"])
    # FAIL: Premiere added nothing. The read decides failed, the rest is not run, the sequence is put back.
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "1,0", "--effect", "ProcAmp",
                          "--effect", "FAIL Fx")
    assert code == 1 and out["code"] == "not_done", out
    # Entries: clip 1 ProcAmp, clip 1 FAIL Fx, clip 0 ProcAmp (has it), clip 0 FAIL Fx.
    assert statuses(out) == ["done", "failed", "skipped", "not_run"]
    assert out["first_not_run"] == 3
    assert "added no component" in out["results"][1]["error"]
    assert out["results"][1].get("unknown") is None
    assert model.load()["active"] == "o1" and out["left_switched"] is None
    assert out["stored"] is False


def test_failure_then_rerun(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 3)
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--all", "--effect", "ProcAmp",
                          "--effect", "FAIL Fx")
    assert code == 1 and statuses(out) == ["done", "failed", "not_run", "not_run", "not_run", "not_run"]
    assert out["first_not_run"] == 2
    assert model.writes == [("activate", "s1"), ("activate", "o1")]
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--all", "--effect", "ProcAmp")
    assert code == 0, out
    assert statuses(out) == ["skipped", "done", "done"]


def test_all_and_too_many(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 3)
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--all", "--effect", "ProcAmp")
    assert code == 0, out
    assert [r["target"]["clip"] for r in out["results"]] == [0, 1, 2]
    assert [r["target"]["node_id"] for r in out["results"]] == ["c0", "c1", "c2"]


def test_too_many_clips(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    def add(data):
        for n in range(401):
            data["clips"].append({"node_id": f"m{n}", "sequence": "s1", "kind": "video", "track": 1, "item": "item1",
                                  "start_ticks": n * 100, "end_ticks": n * 100 + 100, "in_ticks": 0,
                                  "out_ticks": 100, "linked": [], "effects": list(BASE)})
    model.change(add)
    code, out = run_apply(capsys, bridge_dir, model, "--track", "1", "--all", "--effect", "ProcAmp")
    assert code == 1 and out["code"] == "too_many_clips", out
    assert "Nothing was sent." in out["error"]
    assert mcp_calls(tmp_path) == [] and model.writes == [] and live.sessions == 0


def test_no_clip_is_refused(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 2)
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0,5", "--effect", "ProcAmp")
    assert code == 1 and out["code"] == "refused", out
    assert out["results"][1]["reason_code"] == "no_clip"
    assert mcp_calls(tmp_path) == [] and model.writes == []
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0,5", "--effect", "ProcAmp",
                          "--skip-refused")
    assert code == 1 and out["code"] == "not_done"
    assert statuses(out) == ["done", "skipped"] and out["counts"]["refused"] == 1


def test_skip_refused_with_nothing_left_to_apply_is_not_done(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 1, effects={0: ["ProcAmp"]})
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0,5", "--effect", "ProcAmp",
                          "--skip-refused")
    assert code == 1 and out["code"] == "not_done", out
    assert statuses(out) == ["skipped", "skipped"] and out["counts"]["refused"] == 1 and out["stored"] is False
    assert mcp_calls(tmp_path) == [] and model.writes == []


def test_unreadable_effects_are_refused(live, bridge_dir, capsys, tmp_path, model, monkeypatch):  # noqa: F811
    clips_on(model, 1)
    inner = model.clip_list

    def no_effects(script):
        reply = inner(script)
        for clip in reply["clips"]:
            clip["effects"] = None
        return reply
    monkeypatch.setattr(model, "clip_list", no_effects)
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", "--effect", "ProcAmp")
    assert code == 1 and out["code"] == "refused", out
    assert out["results"][0]["reason_code"] == "effects_unread"
    assert mcp_calls(tmp_path) == []


@pytest.mark.parametrize("argv, text", [
    (["--clips", "0", "--all"], "not both"), ([], "--clips or --all"), (["--clips", "a,b"], "--clips"),
    (["--clips", "0,-1"], "--clips")])
def test_clips_and_all_together(live, bridge_dir, capsys, tmp_path, model, argv, text):  # noqa: F811
    clips_on(model, 1)
    with panel_on(bridge_dir, model) as panel:
        code = dispatch(["--json", "live", "effect", "apply", "--sequence", "s1", "--track", "0", *argv,
                         "--effect", "ProcAmp"])
    assert code == 2
    err = " ".join(capsys.readouterr().err.split())
    assert text in err, err
    assert panel.seen == [] and mcp_calls(tmp_path) == [] and model.writes == []


def test_gyroflow_note(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 1)
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", "--effect", "Gyroflow")
    assert code == 0, out
    assert apply.GYROFLOW_NOTE in out["results"][0]["warnings"]


def test_put_back_and_no_lease_in_calls(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 2)
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--all", *TWO)
    assert code == 0, out
    found = mcp_calls(tmp_path)
    assert len(found) == 4 and all(c["lease_held"] is False for c in found)
    assert model.writes == [("activate", "s1"), ("activate", "o1")] and model.load()["active"] == "o1"
    assert out["left_switched"] is None and out["sequence"] == {"id": "s1", "name": "Main"}
    assert not (Path(bridge_dir) / LOCK).exists()


def test_a_stop_is_put_back_and_read(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 2)

    def stop_at_second(name, arguments, number):
        if number == 2:
            raise KeyboardInterrupt
    live.wrap = lambda n, session: Wrapped(session, before_call=stop_at_second)
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--all", "--effect", "ProcAmp")
    assert code == 1 and out["code"] == "interrupted" and out["stopped"] is True
    assert statuses(out)[0] == "done" and statuses(out)[1] == "failed"
    assert out["results"][1]["unknown"] is True and out["stored"] is None
    assert model.load()["active"] == "o1"


def test_a_fault_in_the_run_is_put_back(live, bridge_dir, capsys, tmp_path, model, monkeypatch):  # noqa: F811
    clips_on(model, 1)

    def broken(reply):
        raise ValueError("a fault in the run")
    monkeypatch.setattr(apply, "_reply_fault", broken)
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", "--effect", "ProcAmp")
    assert code == 1 and out["code"] == "recipe_error" and "a fault in the run" in out["error"]
    assert model.load()["active"] == "o1" and out["left_switched"] is None
    assert statuses(out) == ["done"]                    # the effect landed before the fault: the read decides


def test_dry_run(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 2, effects={1: ["ProcAmp"]})
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--all", *TWO, "--dry-run")
    assert code == 0, out
    assert out["dry_run"] is True and statuses(out) == ["not_run", "not_run", "skipped", "not_run"]
    planned = out["planned"]
    assert [p["would"] for p in planned] == ["apply", "apply", "nothing to do", "apply"]
    assert planned[0]["call"] == {"tool": "apply_effect", "args": {"node_id": "c0", "effect_name": "ProcAmp",
                                                                   **GUARD}}
    assert planned[2]["call"] is None
    assert mcp_calls(tmp_path) == [] and live.sessions == 0 and model.writes == []
    assert effects_of(model, "c0") == BASE


def test_own_film_is_refused(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 1)
    model.change(lambda data: data["project"].update(name="Film v10.prproj", path="/p/Film v10.prproj"))
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", "--effect", "ProcAmp")
    assert code == 1 and out["code"] == "project_guard", out
    assert mcp_calls(tmp_path) == [] and model.writes == []


def test_marker_after_failure_leaves_switched(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 2)

    def marker(name, arguments, number):
        if number == 2:
            (Path(bridge_dir) / MARKER_FILE).write_text(json.dumps({"version": 1, "reason": "unknown_command_outcome",
                                                                     "nonce": "t"}))
    live.wrap = lambda n, session: Wrapped(session, before_call=marker)
    code, out = run_apply(capsys, bridge_dir, model, "--track", "0", "--all", "--effect", "ProcAmp")
    assert code == 1 and out["code"] == "not_restored", out
    assert model.load()["active"] == "s1"
    assert out["left_switched"]["active_sequence"] == {"id": "s1", "name": "Main"}
    assert out["results"][0]["status"] == "done"


def test_human_output(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    clips_on(model, 1)
    code, captured = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", *TWO, "--dry-run",
                               json_out=False)
    assert code == 0 and "Dry run: nothing was written." in captured.out
    code, captured = run_apply(capsys, bridge_dir, model, "--track", "0", "--clips", "0", *TWO, json_out=False)
    assert code == 0
    assert "2 done, 0 skipped, 0 failed, 0 not run." in captured.out
    assert "effect index 3" in captured.out and "effect index 2" in captured.out


def test_apply_help_names_the_rules(capsys):
    assert dispatch(["live", "effect", "apply", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "V1 is 0" in text and "--any-order" in text and "not saved" in text
    assert "active for every session" in text
