"""Tests for `live place`: the shot math, the recipe (core/recipes/place.py), its script
(core/scripts/place_read.jsx) and the command (live_recipes.py).

The scripts are answered by a fake panel that plays a small Premiere timeline in Python (fakes.TimelineModel),
and the MCP tools by the fake MCP server of fakes.py, which changes the same model file. The script itself runs
in node with a stub `app`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli_anything.premiere.core.recipes import place
from cli_anything.premiere.errors import CliError

DATA = Path(__file__).resolve().parent / "data"
F = 24000 / 1001


def load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


# ---------- the shot math ----------
def test_golden_calls():
    fps, rows = place.resolve_shots(load("shots_v10.json"))
    golden = load("place_v10.json")
    calls = [call for row in rows for call in place.calls_for(row, fps)]
    assert len(calls) == len(golden["calls"]) == 50
    assert [c["tool"] for c in calls] == [c["tool"] for c in golden["calls"]]
    for ours, theirs in zip(calls, golden["calls"]):
        expected = {k: v for k, v in theirs["args"].items() if k != "expected_project_path"}
        assert ours["args"] == expected
    assert fps == golden["fps"]


def test_golden_data_holds_no_drive_path_or_film_name():
    for path in DATA.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "/Volumes/" not in text, path.name
    shots = load("shots_v10.json")["shots"]
    assert [s["name"] for s in shots] == [f"S{n:02d}" for n in range(1, 26)]
    assert [s["name"] for s in load("place_v10.json")["shots"]] == [f"S{n:02d}" for n in range(1, 26)]
    assert set(load("place_v10.json")) == {"fps", "shots", "calls"}
    assert all(c["args"]["expected_project_path"] == "/p/X Claude.prproj" for c in load("place_v10.json")["calls"])


def calls_of(plan: dict) -> list[dict]:
    fps, rows = place.resolve_shots(plan)
    return [call for row in rows for call in place.calls_for(row, fps)]


def pairs(*items) -> list[dict]:
    """The calls of place_plan.py as (item, in, out, start, track, audio) tuples."""
    out = []
    for item, in_s, out_s, start, track, audio in items:
        out.append({"tool": "set_item_in_out", "args": {"item_id": item, "in_seconds": in_s, "out_seconds": out_s}})
        out.append({"tool": "overwrite_clip", "args": {"item_id": item, "start_seconds": start, "track_index": track,
                                                        "audio_track_index": audio}})
    return out


def test_planner_paths():
    # The numbers below were computed by place_plan.py of the film session for the same three plans.
    chained = {"fps": "24000/1001", "shots": [
        {"name": "A", "item": "i1", "src_in": 1.25, "src_out": 3.4, "track": 0, "audio_track": 0},
        {"name": "B", "item": "i2", "src_in": 0.5, "src_out": 2.0, "track": 0, "audio_track": 1},
        {"name": "C", "item": "i3", "src_in": 10.01, "src_out": 11.5, "track": 0, "audio_track": 0}]}
    assert calls_of(chained) == pairs(("i1", 1.210542, 3.379375, 0.001, 0, 0), ("i2", 0.5015, 2.003, 2.169833, 0, 1),
                                      ("i3", 10.011, 11.470792, 3.671333, 0, 0))
    _, rows = place.resolve_shots(chained)
    assert [(r["in_f"], r["start_f"], r["end_f"], r["frames"]) for r in rows] == [(29, 0, 52, 52), (12, 52, 88, 36),
                                                                                  (240, 88, 123, 35)]
    start_end = {"fps": "23.976", "shots": [
        {"name": "A", "item": "i1", "src_in": 0.0, "start": 2.5, "end": 5.0, "track": 1, "audio_track": 2},
        {"name": "B", "item": "i2", "src_in_f": 30, "start_f": 10, "end_f": 40, "track": 1, "audio_track": 3},
        {"name": "C", "item": "i3", "src_in": 4.2, "frames": 24, "track": 1, "audio_track": 2}]}
    assert calls_of(start_end) == pairs(("i1", 0.001, 2.5035, 2.5035, 1, 2), ("i2", 1.25225, 2.5035, 0.418083, 1, 3),
                                        ("i3", 4.171833, 5.172833, 5.006, 1, 2))
    interpreted = {"fps": "24000/1001", "shots": [
        {"name": "A", "item": "i1", "cam_in_f": 72, "cam_fps": "120000/1001", "interpreted": True, "frames": 138,
         "start_f": 0, "track": 0, "audio_track": 5},
        {"name": "B", "item": "i2", "cam_in_f": 200, "cam_fps": "120000/1001", "interpreted": "120000/1001",
         "frames": 50, "track": 0, "audio_track": 5},
        {"name": "C", "item": "i3", "cam_in_f": 61, "cam_fps": 59.94, "frames": 20, "track": 0, "audio_track": 0},
        {"name": "D", "item": "i4", "src_in_f": 13, "item_fps": "30000/1001", "frames": 33, "track": 0,
         "audio_track": 0}]}
    assert calls_of(interpreted) == pairs(("i1", 3.004, 8.75975, 0.001, 0, 5), ("i2", 1.669333, 3.75475, 5.75675, 0, 5),
                                          ("i3", 1.018683, 1.85285, 7.842167, 0, 0),
                                          ("i4", 0.434767, 1.811142, 8.676333, 0, 0))
    _, rows = place.resolve_shots(interpreted)
    assert [r["item_fps"] for r in rows] == [F, 120000 / 1001, 60000 / 1001, 30000 / 1001]
    assert [r["src_in"] for r in rows] == [3.003, 1.668333, 1.017683, 0.433767]


def test_rate_and_floor_frame():
    assert place.rate("24000/1001") == F and place.rate("23.976") == F and place.rate(23.976) == F
    assert place.rate(59.94) == 60000 / 1001 and place.rate("25") == 25.0 and place.rate(30) == 30.0
    assert place.floor_frame(1.0, 24) == 24 and place.floor_frame(0.9999999999, 24) == 24
    assert place.floor_frame(0.98, 24) == 23


def bad(plan: dict) -> CliError:
    with pytest.raises(CliError) as caught:
        place.resolve_shots(plan)
    assert caught.value.code == "bad_plan"
    return caught.value


def test_plan_file_faults_in_the_math():
    shot = {"name": "S1", "item": "i1", "src_in": 0, "frames": 10}
    assert "S1" in bad({"shots": [{**shot, "item": None}]}).message
    assert "source in" in bad({"shots": [{"name": "S1", "item": "i1", "frames": 10}]}).message
    assert "S1" in bad({"shots": [{**shot, "frames": 0}]}).message
    assert "auto_empty" in bad({"shots": [{**shot, "audio_track": "auto_empty"}]}).message
    assert "S1" in bad({"shots": [{**shot, "track": 1.5}]}).message
    assert "cam_fps" in bad({"shots": [{"name": "S1", "item": "i1", "cam_in_f": 3, "frames": 10}]}).message
    bad({"shots": []})
    bad({"fps": "abc", "shots": [shot]})
    bad({"fps": 0, "shots": [shot]})
    bad({"shots": [{**shot, "frames": True}]})
    bad({"shots": [{**shot, "src_in": float("nan")}]})
    bad({"shots": [{**shot, "audio_only": "yes"}]})
    bad({"shots": [{**shot, "src_in": -1}]})
    bad({"shots": [shot, "not a shot"]})
    bad([shot])
    # A key set to null counts as not given: the next way to give the value is used.
    fps, rows = place.resolve_shots({"shots": [{**shot, "src_in": None, "src_in_f": 4, "start_f": None}]})
    assert rows[0]["in_f"] == 4 and rows[0]["start_f"] == 0
    assert "Nothing was sent." in bad({"shots": [{**shot, "frames": 0}]}).message


def test_load_shots_reads_a_file(tmp_path):
    path = tmp_path / "shots.json"
    path.write_text(json.dumps({"fps": "24000/1001", "shots": [{"item": "i1", "src_in": 0, "frames": 5}]}))
    assert place.load_shots(path)["shots"][0]["item"] == "i1"
    path.write_text("{not json")
    with pytest.raises(CliError) as caught:
        place.load_shots(path)
    assert caught.value.code == "bad_plan"
    with pytest.raises(CliError) as caught:
        place.load_shots(tmp_path / "missing.json")
    assert caught.value.code == "bad_plan"
    path.write_text('{"shots": [{"item": "i1", "src_in": NaN, "frames": 5}]}')
    with pytest.raises(CliError) as caught:
        place.load_shots(path)
    assert caught.value.code == "bad_plan"


# ---------- place_read.jsx in node ----------
PLACE_PRELUDE = r"""
function coll(key, items) { var c = {}; c[key] = items.length; for (var i = 0; i < items.length; i++) c[i] = items[i]; return c; }
function t(ticks) { return {ticks: String(ticks)}; }
function pclip(id, item, start, end, inp, out) {
  return {nodeId: id, projectItem: {nodeId: item}, start: t(start), end: t(end), inPoint: t(inp), outPoint: t(out)};
}
function trackOf(clips, transitions) { return {clips: coll("numItems", clips), transitions: coll("numItems", transitions || [])}; }
function pitem(id, name, path, video, audio) {
  return {nodeId: id, name: name, type: 1,
          getMediaPath: function () { return path; },
          isSequence: function () { return false; },
          getFootageInterpretation: function () { return {frameRate: 23.976}; },
          getInPoint: function (kind) { return {ticks: "0"}; },
          getOutPoint: function (kind) { return {ticks: (kind === 1 ? video : audio) ? "2540160000000" : "0"}; }};
}
var FR = 10594584000;
var bin = {nodeId: "b1", name: "Bin", type: 2, children: coll("numItems", [
  pitem("i3", "dup.MP4", "/m/dup2.MP4", true, true), pitem("i4", "card.PNG", "/m/card.PNG", true, false)])};
var root = {children: coll("numItems", [pitem("i1", "C0001.MP4", "/m/C0001.MP4", true, true),
                                        pitem("i2", "dup.MP4", "/m/dup.MP4", true, true), bin])};
var seqA = {sequenceID: "S1", name: "Main", timebase: String(FR),
  videoTracks: coll("numTracks", [
    trackOf([pclip("v0", "i1", 0, 48 * FR, 0, 48 * FR), pclip("v1", "i2", 48 * FR, 96 * FR, 0, 48 * FR)],
            [{start: t(0), end: t(12 * FR)}]),
    trackOf([])]),
  audioTracks: coll("numTracks", [trackOf([pclip("a0", "i1", 0, 48 * FR, 0, 48 * FR)])])};
var seqB = {sequenceID: "S2", name: "Other", timebase: String(FR),
  videoTracks: coll("numTracks", [trackOf([pclip("w0", "i2", 0, 10 * FR, 0, 10 * FR)])]),
  audioTracks: coll("numTracks", [])};
var app = {project: {path: "/p/X Claude.prproj", rootItem: root, sequences: coll("numSequences", [seqA, seqB]),
                     activeSequence: seqB}};
"""
PLACE_FR = 10594584000


def place_read_in_node(args: dict) -> dict:
    from cli_anything.premiere.core import jsx
    from cli_anything.premiere.tests.test_jsx import run_in_node
    full = {**args, "expect": {"project_path": "/p/X Claude.prproj", "sequence_id": "S1", "sequence_name": None}}
    return run_in_node(jsx.build(jsx.load("place_read"), full, include=("find",)), PLACE_PRELUDE)


def test_place_read_script_is_es3_and_cites_the_server():
    import re
    from cli_anything.premiere.core import jsx
    from cli_anything.premiere.tests.test_jsx import BANNED
    text = jsx.load("place_read")
    assert text.split("\n")[0] == "// recipe: place_read"
    for pattern in BANNED + [r"\.indexOf\(", r"\bObject\.", r"\.reduce\(", r"\.some\(", r"\.every\("]:
        assert not re.search(pattern, text), pattern
    assert "activeSequence" not in text                       # the sequence comes from __expect only
    for cite in ("script-builder.js", "advanced.js", "media.js", "utility.js", "transitions.js", "inspection.js"):
        assert cite in text, cite


def test_place_read_in_node():
    from cli_anything.premiere.tests.test_jsx import NODE
    if NODE is None:
        pytest.skip("node not installed")
    spans = [{"entry": 0, "track": 0, "audio_track": 0, "start_ticks": str(40 * PLACE_FR), "end_ticks": str(60 * PLACE_FR)},
             {"entry": 1, "track": 0, "audio_track": 0, "start_ticks": str(48 * PLACE_FR), "end_ticks": str(50 * PLACE_FR)},
             {"entry": 2, "track": 5, "audio_track": 3, "start_ticks": "0", "end_ticks": str(PLACE_FR)}]
    reply = place_read_in_node({"items": ["i1", "dup.MP4", "card.PNG", "C0001.MP4", "zzz"], "spans": spans})
    assert reply["success"] is True, reply
    data = reply["data"]
    assert data["sequence"] == {"id": "S1", "name": "Main", "frame_ticks": str(PLACE_FR), "video_tracks": 2,
                                "audio_tracks": 1}
    items = {item["query"]: item for item in data["items"]}
    assert items["i1"] == {"query": "i1", "found": True, "node_id": "i1", "name": "C0001.MP4", "matches": 0,
                           "has_video": True, "has_audio": True, "still": False, "is_sequence": False,
                           "frame_rate": 23.976}
    assert items["dup.MP4"]["found"] is False and items["dup.MP4"]["matches"] == 2 and items["dup.MP4"]["node_id"] is None
    assert items["card.PNG"]["node_id"] == "i4" and items["card.PNG"]["still"] is True
    assert items["card.PNG"]["has_audio"] is False and items["card.PNG"]["matches"] == 1
    assert items["C0001.MP4"]["node_id"] == "i1" and items["C0001.MP4"]["matches"] == 1
    assert items["zzz"] == {"query": "zzz", "found": False, "node_id": None, "name": None, "matches": 0,
                            "has_video": None, "has_audio": None, "still": None, "is_sequence": None,
                            "frame_rate": None}
    first, touch, missing = data["spans"]
    assert first["entry"] == 0
    assert [c["node_id"] for c in first["video"]] == ["v0", "v1"] and [c["node_id"] for c in first["audio"]] == ["a0"]
    assert first["video"][1] == {"node_id": "v1", "item": "i2", "start_ticks": str(48 * PLACE_FR),
                                 "end_ticks": str(96 * PLACE_FR), "in_ticks": "0", "out_ticks": str(48 * PLACE_FR)}
    assert [c["node_id"] for c in touch["video"]] == ["v1"] and touch["audio"] == []   # v0 and a0 only touch it
    assert missing["video"] is None and missing["audio"] is None
    assert data["first_v1"] == {"node_id": "v0", "start_ticks": "0", "head_transition": True}


def test_place_read_in_node_refuses_another_project():
    from cli_anything.premiere.core import jsx
    from cli_anything.premiere.tests.test_jsx import NODE, run_in_node
    if NODE is None:
        pytest.skip("node not installed")
    args = {"items": [], "spans": [], "expect": {"project_path": "/p/Other.prproj", "sequence_id": "S1",
                                                  "sequence_name": None}}
    reply = run_in_node(jsx.build(jsx.load("place_read"), args, include=("find",)), PLACE_PRELUDE)
    assert reply["success"] is False and "precondition" in reply["error"]


# =====================================================================================================
# live place, end to end with the fake panel (TimelineModel) and the fake MCP server
# =====================================================================================================
import contextlib  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from cli_anything.premiere import live_cli  # noqa: E402
from cli_anything.premiere.core import guard, mcp_client  # noqa: E402
from cli_anything.premiere.core.bridge import BridgeClient  # noqa: E402
from cli_anything.premiere.core.mcp_client import McpSession  # noqa: E402
from cli_anything.premiere.premiere_cli import dispatch  # noqa: E402
from cli_anything.premiere.tests.fakes import (  # noqa: E402,F401
    FakePanel, TimelineModel, bridge_dir, fake_mcp_command, keep_signals)

PROJECT = "/p/X Claude.prproj"
TPS = 254016000000
FRAME = 10594584000
GUARD = {"expected_project_path": PROJECT, "expected_cep_target": {"backend": "cep", "sequence_id": "s1"}}
MARKER_FILE = ".host-recovery.json"


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
    return TimelineModel(tmp_path / "timeline.json")


class Wrapped:
    """An McpSession with hooks: before_call(name, arguments, number) runs before each call (number 1 is the first
    call of this session); after_exit() runs when the session closes."""

    def __init__(self, inner, before_call=None, after_exit=None) -> None:
        self.inner, self.before_call, self.after_exit, self.count = inner, before_call, after_exit, 0

    def __enter__(self):
        self.inner.__enter__()
        return self

    def __exit__(self, *exc):
        self.inner.__exit__(*exc)
        if self.after_exit is not None:
            self.after_exit()

    def call(self, name, arguments, timeout_s=120.0):
        self.count += 1
        if self.before_call is not None:
            self.before_call(name, arguments, self.count)
        return self.inner.call(name, arguments, timeout_s=timeout_s)


@pytest.fixture
def live(bridge_dir, monkeypatch, tmp_path, model):  # noqa: F811
    """The live commands on a temp bridge folder, with the fake MCP server on the model file. live.wrap: a function
    (session number, McpSession) -> session, to put hooks on a session; live.sessions counts the sessions."""
    command = fake_mcp_command(tmp_path, bridge_dir, timeline=model.path)
    state = SimpleNamespace(sessions=0, wrap=None, tmp=tmp_path)

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


def run_place(capsys, bridge_dir, model, tmp_path, plan: dict, *argv):  # noqa: F811
    path = Path(tmp_path) / "shots.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with panel_on(bridge_dir, model):
        code = dispatch(["--json", "live", "place", "--sequence", "s1", "--plan", str(path), *argv])
    return code, json.loads(capsys.readouterr().out)


def shot(item, start_f, frames=48, *, src_in_f=10, track=0, audio_track=0, **extra) -> dict:
    return {"name": f"{item}@{start_f}", "item": item, "src_in_f": src_in_f, "start_f": start_f, "frames": frames,
            "track": track, "audio_track": audio_track, **extra}


def three(model, items=("item1", "item2", "item3")) -> dict:
    for item in items:
        model.add_item(item)
    return {"fps": "24000/1001", "shots": [shot(item, 48 * n) for n, item in enumerate(items)]}


def statuses(out: dict) -> list[str]:
    return [r["status"] for r in out["results"]]


def test_golden_dry_run(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    plan = load("shots_v10.json")
    for item in dict.fromkeys(s["item"] for s in plan["shots"]):
        model.add_item(item)
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan, "--dry-run")
    assert code == 0, out
    assert out["dry_run"] is True
    planned = [call for entry in out["planned"] for call in entry["calls"]]
    golden = load("place_v10.json")["calls"]
    assert len(planned) == 50
    for ours, theirs in zip(planned, golden):
        assert ours == {"tool": theirs["tool"], "args": {**theirs["args"], **GUARD}}
    assert mcp_calls(tmp_path) == [] and live.sessions == 0
    assert model.writes == [] and model.clips() == []
    assert statuses(out) == ["not_run"] * 25


def test_place_runs_and_checks(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    plan = three(model)
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 0, out
    assert out["ok"] is True and statuses(out) == ["done"] * 3 and out["stored"] is True
    found = mcp_calls(tmp_path)
    assert [c["tool"] for c in found] == ["set_item_in_out", "overwrite_clip"] * 3
    assert all(c["arguments"]["expected_project_path"] == PROJECT for c in found)
    assert all(c["arguments"]["expected_cep_target"] == GUARD["expected_cep_target"] for c in found)
    assert found[1]["arguments"]["item_id"] == "item1" and found[1]["arguments"]["start_seconds"] == 0.001
    nows = [r["now"] for r in out["results"]]
    assert [n["video"]["node_id"] for n in nows] == ["n0001", "n0003", "n0005"]
    assert [n["audio"]["node_id"] for n in nows] == ["n0002", "n0004", "n0006"]
    assert nows[1]["video"]["start_seconds"] == round(48 * FRAME / TPS, 6)
    assert model.writes == [("activate", "s1"), ("activate", "o1")] and model.load()["active"] == "o1"
    assert out["left_switched"] is None and out["sequence"] == {"id": "s1", "name": "Main"}
    assert [m["item"] for m in out["marks"]] == ["item1", "item2", "item3"]
    assert out["results"][0]["marks"] == {"in_seconds": pytest.approx(10 * FRAME / TPS),     # as the server says
                                          "out_seconds": pytest.approx(58 * FRAME / TPS)}
    assert [c["start_ticks"] for c in model.clips("video", 0)] == [0, 48 * FRAME, 96 * FRAME]


def test_no_mcp_call_under_the_lease(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    code, out = run_place(capsys, bridge_dir, model, tmp_path, three(model))
    assert code == 0
    found = mcp_calls(tmp_path)
    assert found and all(c["lease_held"] is False for c in found)
    assert not (Path(bridge_dir) / ".host-owner-lease").exists()


def test_rerun_skips_placed_shots(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    plan = three(model)
    assert run_place(capsys, bridge_dir, model, tmp_path, plan)[0] == 0
    before = len(mcp_calls(tmp_path))
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 0, out
    assert statuses(out) == ["skipped"] * 3
    assert [(r["skip"], r["reason_code"]) for r in out["results"]] == [("nothing_to_do", "already_placed")] * 3
    assert out["results"][0]["now"]["video"]["node_id"] == "n0001"
    assert len(mcp_calls(tmp_path)) == before and live.sessions == 1


def test_rerun_with_nothing_to_do_does_not_switch(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    # D2: the active sequence is switched only when some shot has a step to do.
    plan = three(model)
    assert run_place(capsys, bridge_dir, model, tmp_path, plan)[0] == 0
    writes = list(model.writes)
    assert writes == [("activate", "s1"), ("activate", "o1")]
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 0 and out["ok"] is True and out["stored"] is True, out
    assert model.writes == writes and model.load()["active"] == "o1"
    assert out["left_switched"] is None


def test_same_item_twice(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    model.add_item("000f4257")
    plan = {"fps": "24000/1001", "shots": [shot("000f4257", 0, 20, src_in_f=3, audio_track=1),
                                           shot("000f4257", 20, 13, src_in_f=61, audio_track=0)]}

    def stop_at_third(name, arguments, number):
        if number == 3:
            raise KeyboardInterrupt
    live.wrap = lambda n, session: Wrapped(session, before_call=stop_at_third)
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 1 and out["code"] == "interrupted" and out["stopped"] is True
    assert statuses(out)[0] == "done" and statuses(out)[1] != "done"
    assert model.load()["active"] == "o1"                      # put back after the stop
    live.wrap = None
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 0, out
    assert statuses(out) == ["skipped", "done"] and out["results"][0]["reason_code"] == "already_placed"
    second = [c for c in mcp_calls(tmp_path)][2:]
    assert [c["tool"] for c in second] == ["set_item_in_out", "overwrite_clip"]
    assert second[1]["arguments"]["start_seconds"] == round(20 * FRAME / TPS + 0.001, 6)
    # Each shot is matched by item, track and start: both are there now.
    starts = sorted(c["start_ticks"] for c in model.clips("video", 0))
    assert starts == [0, 20 * FRAME]


def v10_with(model, replace: dict) -> dict:
    plan = load("shots_v10.json")
    for number, item in replace.items():
        plan["shots"][number]["item"] = item
    for item in dict.fromkeys(s["item"] for s in plan["shots"]):
        model.add_item(item)
    return plan


def test_failure_in_the_middle(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    plan = v10_with(model, {13: "FAIL0013"})
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 1 and out["code"] == "not_done", out
    assert statuses(out) == ["done"] * 13 + ["failed"] + ["not_run"] * 11
    assert out["first_not_run"] == 14 and out["counts"]["done"] == 13
    assert "fake overwrite failure" in out["results"][13]["error"]
    assert out["results"][13]["now"] == {"video": None, "audio": None}
    assert model.load()["active"] == "o1" and out["left_switched"] is None
    tools = [c["tool"] for c in mcp_calls(tmp_path)]
    assert len(tools) == 28                                       # 13 shots, then the marks and the failed overwrite
    # A re-run with the item fixed: the placed shots are skipped (their neighbours only touch their spans), the
    # rest is placed.
    plan["shots"][13]["item"] = "ok0013"
    model.add_item("ok0013")
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 0, out
    assert statuses(out) == ["skipped"] * 13 + ["done"] * 12


def test_marker_after_failure(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    plan = three(model, ("item1", "MARKER1", "item3"))
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 1, out
    assert statuses(out) == ["done", "failed", "not_run"]
    assert (Path(bridge_dir) / MARKER_FILE).exists()
    assert model.writes == [("activate", "s1")] and model.load()["active"] == "s1"      # no put-back write
    assert out["left_switched"] == {"proxies": None, "active_sequence": {"id": "s1", "name": "Main"}}
    assert any("live app activate --sequence o1" in w for w in out["warnings"])
    assert out["results"][0]["now"]["video"]["node_id"] == "n0001"                    # the read pass still reports


def test_span_not_empty(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    plan = three(model)
    model.add_item("other")
    model.add_clip("other", node_id="x1", track=0, start_f=60, frames=10)
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 1 and out["code"] == "refused", out
    assert out["results"][1]["reason_code"] == "span_not_empty" and "x1" in out["results"][1]["reason"]
    assert mcp_calls(tmp_path) == [] and model.writes == []
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan, "--overwrite")
    assert code == 0, out
    assert any("x1" in w for w in out["results"][1]["warnings"])
    assert "x1" not in [c["node_id"] for c in model.clips("video", 0)]


def test_placed_differently(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    plan = three(model)
    model.add_clip("item2", node_id="x2", track=0, start_f=48, frames=48, in_f=99)
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 1 and out["code"] == "refused"
    assert out["results"][1]["reason_code"] == "placed_differently"
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan, "--overwrite")
    assert code == 1 and out["results"][1]["reason_code"] == "placed_differently"
    assert mcp_calls(tmp_path) == []


@pytest.mark.parametrize("offset_ticks, refused", [
    (FRAME, True), (FRAME // 2 + 1, True), (-FRAME, True), (FRAME // 2, False), (FRAME + 1, False)])
def test_same_item_within_one_frame_is_placed_differently(live, bridge_dir, capsys, tmp_path, model,  # noqa: F811
                                                          offset_ticks, refused):
    # I3: the server's check counts a clip of the item within one frame of the start as placed already, so
    # overwrite_clip would fail. Refused also with --overwrite, and nothing is sent.
    model.add_item("item1")
    model.add_clip("item1", node_id="near", track=0, start_f=0, frames=48, in_f=10)
    model.change(lambda data: [c.__setitem__("start_ticks", 24 * FRAME + offset_ticks)
                               for c in data["clips"] if c["node_id"] == "near"])
    model.change(lambda data: [c.__setitem__("end_ticks", c["start_ticks"] + 48 * FRAME)
                               for c in data["clips"] if c["node_id"] == "near"])
    plan = {"fps": "24000/1001", "shots": [shot("item1", 24)]}
    if refused:                                              # a real run: nothing is sent and nothing switched
        code, out = run_place(capsys, bridge_dir, model, tmp_path, plan, "--overwrite")
        record = out["results"][0]
        assert code == 1 and out["code"] == "refused", out
        assert record["reason_code"] == "placed_differently" and "within one frame" in record["reason"]
        assert mcp_calls(tmp_path) == [] and model.writes == [] and live.sessions == 0
    else:
        code, out = run_place(capsys, bridge_dir, model, tmp_path, plan, "--overwrite", "--dry-run")
        assert out["results"][0]["reason_code"] != "placed_differently", out


def test_plan_overlap_and_fps(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    plan = three(model)
    plan["shots"][2]["start_f"] = 90
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 1 and out["code"] == "refused"
    assert [r["reason_code"] for r in out["results"]] == [None, "plan_overlap", "plan_overlap"]
    plan = three(model)
    plan["fps"] = 25
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 1 and out["code"] == "fps_mismatch" and "Nothing was written." in out["error"]
    assert statuses(out) == ["not_run"] * 3
    assert mcp_calls(tmp_path) == [] and model.writes == []


def test_ambiguous_item_name(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    model.add_item("000f4300", name="C0001.MP4")
    model.add_item("000f4301", name="C0001.MP4")
    plan = {"fps": "24000/1001", "shots": [shot("C0001.MP4", 0)]}
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 1 and out["results"][0]["reason_code"] == "ambiguous_item"
    model.change(lambda data: data["items"]["000f4301"].__setitem__("name", "C0002.MP4"))
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 0, out
    assert {c["arguments"]["item_id"] for c in mcp_calls(tmp_path)} == {"000f4300"}
    assert out["results"][0]["target"]["node_id"] == "000f4300"


def test_no_item(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    plan = three(model)
    plan["shots"][1]["item"] = "missing"
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan, "--skip-refused")
    assert code == 1 and out["code"] == "not_done"
    assert statuses(out) == ["done", "skipped", "done"] and out["results"][1]["reason_code"] == "no_item"
    assert out["counts"]["refused"] == 1


def test_sound_only_shot(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    model.add_item("item1")
    plan = {"fps": "24000/1001", "shots": [shot("item1", 0, track=2, audio_track=1, audio_only=True)]}
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 0, out
    found = mcp_calls(tmp_path)
    assert [c["tool"] for c in found] == ["set_item_in_out", "overwrite_clip", "remove_from_timeline"]
    assert found[2]["arguments"] == {"node_id": "n0001", "include_linked": False, "ripple": False, **GUARD}
    assert model.clips("video", 2) == [] and [c["node_id"] for c in model.clips("audio", 1)] == ["n0002"]
    assert all(c["lease_held"] is False for c in found)
    assert out["results"][0]["now"]["video"] is None and out["results"][0]["now"]["audio"]["node_id"] == "n0002"
    assert live.sessions == 2
    assert model.load()["active"] == "o1"


def test_sound_only_records_both_clips(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    # M9 (design section 8): the result names the sound clip kept and the picture clip removed.
    model.add_item("item1")
    plan = {"fps": "24000/1001", "shots": [shot("item1", 0, track=2, audio_track=1, audio_only=True)]}
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 0, out
    record = out["results"][0]
    assert record["removed_picture"] == "n0001" and record["now"]["audio"]["node_id"] == "n0002"
    three_out = run_place(capsys, bridge_dir, model, tmp_path, three(model, ("item7", "item8", "item9")))[1]
    assert all(r["removed_picture"] is None for r in three_out["results"])


def test_sound_only_names_the_removed_picture_when_the_call_said_no(live, bridge_dir, capsys, tmp_path,  # noqa: F811
                                                                    model):
    # The remove call reports a fault, but the read after shows the shot done: the record still names the
    # picture clip that was removed.
    model.add_item("item1")
    plan = {"fps": "24000/1001", "shots": [shot("item1", 0, track=2, audio_track=1, audio_only=True)]}

    class Unverified:
        def __init__(self, inner):
            self.inner = inner

        def __enter__(self):
            self.inner.__enter__()
            return self

        def __exit__(self, *exc):
            return self.inner.__exit__(*exc)

        def call(self, name, arguments, timeout_s=120.0):
            reply = self.inner.call(name, arguments, timeout_s=timeout_s)
            if name == "remove_from_timeline":
                reply = {**reply, "structured": {"data": {"verified": False}}}
            return reply
    live.wrap = lambda n, session: Unverified(session) if n == 2 else session
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    record = out["results"][0]
    assert record["status"] == "done" and record["removed_picture"] == "n0001", out
    assert any("reported a fault" in w for w in record["warnings"])


def test_sound_only_done_needs_the_planned_marks():
    # I1: a sound-only shot is done only when the sound clip shows the planned media.
    row = {"audio_only": True, "start_f": 0, "frames": 48, "src_in": 10 / F, "item_fps": F}
    sound = {"node_id": "a1", "item": "i1", "start_ticks": "0", "end_ticks": str(48 * FRAME),
             "in_ticks": str(10 * FRAME), "out_ticks": str(58 * FRAME)}
    plan = {"node_id": "i1"}
    ok, _now, why = place._done(row, plan, {"video": [], "audio": [sound]}, FRAME, F, {})
    assert ok is True and why is None
    wrong = {**sound, "in_ticks": str(99 * FRAME), "out_ticks": str(147 * FRAME)}
    ok, now, why = place._done(row, plan, {"video": [], "audio": [wrong]}, FRAME, F, {})
    assert ok is False and why == "the sound clip's in and out points are not the planned ones"
    assert now["audio"]["node_id"] == "a1"


def test_sound_only_half_done(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    model.add_item("item1")
    model.add_clip("item1", node_id="p1", kind="video", track=2, start_f=0, frames=48, in_f=10)
    model.add_clip("item1", node_id="a1", kind="audio", track=1, start_f=0, frames=48, in_f=10, link="p1")
    plan = {"fps": "24000/1001", "shots": [shot("item1", 0, track=2, audio_track=1, audio_only=True)]}
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 0, out
    assert [c["tool"] for c in mcp_calls(tmp_path)] == ["remove_from_timeline"]
    assert mcp_calls(tmp_path)[0]["arguments"]["node_id"] == "p1"
    assert [c["node_id"] for c in model.clips("audio", 1)] == ["a1"] and model.clips("video", 2) == []


def test_still_is_extended(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    model.add_item("card1", name="card.png", still=True)
    plan = {"fps": "24000/1001", "shots": [{"name": "card", "item": "card1", "src_in": 0, "start_f": 0,
                                            "frames": 144, "track": 0, "audio_track": 0}]}
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 0, out
    found = mcp_calls(tmp_path)
    assert [c["tool"] for c in found] == ["set_item_in_out", "overwrite_clip", "set_clip_duration"]
    assert found[2]["arguments"] == {"node_id": "n0001", "duration_seconds": 144 / F, **GUARD}
    assert all(c["lease_held"] is False for c in found)
    clip = model.clips("video", 0)[0]
    assert clip["end_ticks"] - clip["start_ticks"] == 144 * FRAME
    assert out["results"][0]["status"] == "done"
    # A re-run: the still runs its planned length, so it is placed.
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 0 and out["results"][0]["reason_code"] == "already_placed"


def test_still_half_done(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    model.add_item("card1", name="card.png", still=True)
    model.add_clip("card1", node_id="s1c", track=0, start_f=0, frames=119)
    plan = {"fps": "24000/1001", "shots": [{"name": "card", "item": "card1", "src_in": 0, "start_f": 0,
                                            "frames": 144, "track": 0, "audio_track": 0}]}
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 0, out
    assert [c["tool"] for c in mcp_calls(tmp_path)] == ["set_clip_duration"]


def test_unverified_overwrite_is_failed(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    plan = three(model, ("UNVERIFIED1", "item2", "item3"))
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 1, out
    assert statuses(out) == ["failed", "not_run", "not_run"]
    assert out["results"][0]["now"]["video"] is not None and out["results"][0]["now"]["audio"] is None
    assert len(mcp_calls(tmp_path)) == 2


def test_head_transition_warning(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    plan = three(model)
    model.add_item("old")
    model.add_clip("old", node_id="x0", track=0, start_f=0, frames=24)
    model.add_transition(track=0, start_f=0, end_f=12)
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan, "--overwrite")
    assert code == 0, out
    assert any("transition" in w for w in out["results"][0]["warnings"])
    assert not any("transition" in w for w in out["results"][1]["warnings"])


def test_unknown_plan_keys_pass(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    plan = three(model)
    plan["shots"][0]["lens"] = "35mm"
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan, "--dry-run")
    assert code == 0, out
    assert any("'lens'" in w for w in out["warnings"])


@pytest.mark.parametrize("change, words", [
    ({"item": None}, "no item"), ({"src_in_f": None}, "no source in"), ({"frames": 0}, "frames"),
    ({"audio_track": "auto_empty"}, "auto_empty")])
def test_plan_file_faults(live, bridge_dir, capsys, tmp_path, model, change, words):  # noqa: F811
    plan = three(model)
    plan["shots"][1].update(change)
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 1 and out["code"] == "bad_plan" and words in out["error"]
    assert "Nothing was sent." in out["error"]
    assert mcp_calls(tmp_path) == [] and model.writes == []


def test_moved_sequence_skips_the_second_session(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    model.add_item("item1")
    plan = {"fps": "24000/1001", "shots": [shot("item1", 0, track=2, audio_track=1, audio_only=True)]}
    live.wrap = lambda n, session: Wrapped(session, after_exit=(lambda: model.activate("o1")) if n == 1 else None)
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 1, out
    assert [c["tool"] for c in mcp_calls(tmp_path)] == ["set_item_in_out", "overwrite_clip"]
    assert live.sessions == 1
    assert statuses(out) == ["failed"] and "the active sequence changed" in out["results"][0]["error"]


def test_a_fault_between_the_sessions_still_puts_back(live, bridge_dir, capsys, tmp_path, model,  # noqa: F811
                                                       monkeypatch):
    model.add_item("item1")
    plan = {"fps": "24000/1001", "shots": [shot("item1", 0, track=2, audio_track=1, audio_only=True)]}

    def broken(*a, **k):
        raise ValueError("a fault between the sessions")
    monkeypatch.setattr(place, "_second_targets", broken)
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 1 and out["code"] == "recipe_error" and "a fault between the sessions" in out["error"]
    assert model.load()["active"] == "o1" and out["left_switched"] is None
    assert statuses(out) == ["failed"]


def test_human_output(live, bridge_dir, capsys, tmp_path, model):  # noqa: F811
    plan = three(model)
    path = Path(tmp_path) / "shots.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    with panel_on(bridge_dir, model):
        assert dispatch(["live", "place", "--sequence", "s1", "--plan", str(path), "--dry-run"]) == 0
        text = capsys.readouterr().out
        assert "Dry run: nothing was written." in text and "item1@0" in text
        assert dispatch(["live", "place", "--sequence", "s1", "--plan", str(path)]) == 0
        text = capsys.readouterr().out
    assert "3 done, 0 skipped, 0 failed, 0 not run." in text and "Marks:" in text


def test_place_help_names_the_rules(capsys):
    assert dispatch(["live", "place", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "never insert" in text and "marks stay as set" in text
    assert "active for every session" in text


def test_a_stop_between_the_sessions_is_reported_and_put_back(live, bridge_dir, capsys, tmp_path, model,  # noqa: F811
                                                               monkeypatch):
    model.add_item("item1")
    plan = {"fps": "24000/1001", "shots": [shot("item1", 0, track=2, audio_track=1, audio_only=True)]}

    def stop(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(place, "_second_targets", stop)
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan)
    assert code == 1 and out["code"] == "interrupted" and out["stopped"] is True
    assert model.load()["active"] == "o1" and out["left_switched"] is None
    assert [c["tool"] for c in mcp_calls(tmp_path)] == ["set_item_in_out", "overwrite_clip"]


def test_sound_only_needs_an_empty_picture_span_even_with_overwrite(live, bridge_dir, capsys, tmp_path,  # noqa: F811
                                                                     model):
    # The picture of a sound-only shot goes on a spare track and is removed again, so cutting a clip there would
    # leave a hole: the span must be empty, whatever --overwrite says (design section 8).
    model.add_item("item1")
    model.add_item("other")
    model.add_clip("other", node_id="x3", track=2, start_f=10, frames=10)
    plan = {"fps": "24000/1001", "shots": [shot("item1", 0, track=2, audio_track=1, audio_only=True)]}
    code, out = run_place(capsys, bridge_dir, model, tmp_path, plan, "--overwrite")
    assert code == 1 and out["code"] == "refused", out
    assert out["results"][0]["reason_code"] == "span_not_empty" and "x3" in out["results"][0]["reason"]
    assert mcp_calls(tmp_path) == [] and model.writes == []
