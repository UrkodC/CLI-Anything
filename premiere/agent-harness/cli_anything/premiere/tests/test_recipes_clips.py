"""Tests for `live clip list`: the recipe (core/recipes/clips.py), its script (core/scripts/clip_list.jsx)
and the command (live_recipes.py).

The command runs against a fake panel in a temp folder. The script runs in node with a stub `app`, alone
and, in one test, behind the fake panel together with the Python side.
"""
from __future__ import annotations

import contextlib
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from cli_anything.premiere import live_cli
from cli_anything.premiere.core import guard, jsx
from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.core.recipes import clips
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.premiere_cli import dispatch
from cli_anything.premiere.tests.fakes import (  # noqa: F401
    RECIPE_STATE, FakePanel, bridge_dir, keep_signals, recipe_handler, script_args)
from cli_anything.premiere.tests.test_jsx import BANNED, NODE, STUB_HELPERS, needs_node

MARK = "recipe: clip_list"
FIND_MARK = "// find.jsx: target finders for recipe scripts"
LOCK = ".host-owner-lease"
ROOT = Path(__file__).resolve().parents[3]       # the folder that holds cli_anything/
RECIPES = "cli_anything.premiere.live_recipes"
LIVE_CLI = "cli_anything.premiere.live_cli"
TPS = 254016000000
STATE_OWN = {**RECIPE_STATE, "projects": [{"name": "Family film.prproj", "path": "/p/Family film.prproj", "active": True}]}
STATE_26 = {**RECIPE_STATE, "version": "26.0.0"}
DEFAULT_ARGS = {"sequence_id": None, "track_type": "both", "track": None, "offset": 0, "limit": 400, "detail": False}


@pytest.fixture(autouse=True)
def _keep_signal_handlers(keep_signals):  # noqa: F811
    yield


@pytest.fixture(autouse=True)
def _no_real_bridge(monkeypatch, tmp_path):
    monkeypatch.setenv("PREMIERE_TEMP_DIR", str(tmp_path / "not-the-real-bridge"))


@pytest.fixture
def live(bridge_dir, monkeypatch):  # noqa: F811
    monkeypatch.setattr(live_cli, "make_client", lambda wait, **kw: BridgeClient(bridge_dir, wait, **kw))
    monkeypatch.setattr(guard, "panel_version", lambda *a, **k: "1.25.0")
    monkeypatch.setattr(guard, "server_version", lambda *a, **k: "1.25.0")
    return bridge_dir


@pytest.fixture
def sends(monkeypatch) -> list[bool]:
    """The write flag of every script sent, in order."""
    seen: list[bool] = []
    original = BridgeClient.send

    def spy(self, script, *, write, **kw):
        seen.append(write)
        return original(self, script, write=write, **kw)
    monkeypatch.setattr(BridgeClient, "send", spy)
    return seen


@contextlib.contextmanager
def panel_on(bridge_dir, **kw):  # noqa: F811
    panel = FakePanel(bridge_dir, **kw)
    panel.start()
    try:
        yield panel
    finally:
        panel.stop()


def run_json(capsys, argv) -> tuple[int, dict]:
    code = dispatch(["--json", *argv])
    return code, json.loads(capsys.readouterr().out)


def raw_clip(**over) -> dict:
    """One clip as the script returns it: raw fields only."""
    clip = {"track_type": "video", "track": 0, "clip": 0, "node_id": "000f4243", "name": "testclip.mp4",
            "start_ticks": "0", "end_ticks": "1525620096000", "in_ticks": "0", "out_ticks": "1525620096000",
            "enabled": True, "speed": 1, "adjustment_layer": False,
            "source": {"node_id": "000f4241", "name": "testclip.mp4", "path": "/media/testclip.mp4"},
            "effects": ["Opacity", "Motion", "Lumetri Color", "ASC CDL", "ProcAmp"]}
    clip.update(over)
    return clip


def script_reply(found, *, total=None, name="Main", used_active=False) -> dict:
    return {"sequence": {"id": "s1", "name": name}, "used_active": used_active,
            "total": len(found) if total is None else total, "clips": found}


def clip_list(live, capsys, reply, *argv, state=None):  # noqa: F811
    """Run `live clip list` against a panel that answers with `reply`; returns (code, payload, panel)."""
    with panel_on(live, handler=recipe_handler({MARK: reply}, state=state)) as panel:
        code, out = run_json(capsys, ["live", "clip", "list", *argv])
    return code, out, panel


# ---------- the command and the payload ----------
def test_clip_list_shapes_one_clip(live, capsys):
    code, out, panel = clip_list(live, capsys, script_reply([raw_clip()]), "--sequence", "s1")
    assert code == 0 and out.pop("ok") is True
    assert out["sequence"] == {"id": "s1", "name": "Main"}
    assert [out[k] for k in ("count", "total", "truncated", "next_offset", "warnings")] == [1, 1, False, None, []]
    assert list(out) == ["sequence", "clips", "count", "total", "truncated", "next_offset", "warnings"]
    assert out["clips"] == [{
        "track_type": "video", "track": 0, "clip": 0, "node_id": "000f4243", "name": "testclip.mp4",
        "start_ticks": "0", "end_ticks": "1525620096000", "in_ticks": "0", "out_ticks": "1525620096000",
        "start_seconds": 0.0, "end_seconds": 6.006, "enabled": True, "speed": 1, "adjustment_layer": False,
        "source": {"node_id": "000f4241", "name": "testclip.mp4", "path": "/media/testclip.mp4"},
        "effects": ["Opacity", "Motion", "Lumetri Color", "ASC CDL", "ProcAmp"],
        "render_order": ["ProcAmp", "ASC CDL", "Lumetri Color"], "procamp_before_lumetri": True}]
    assert list(out["clips"][0]) == ["track_type", "track", "clip", "node_id", "name", "start_ticks", "end_ticks", "in_ticks",
                                     "out_ticks", "start_seconds", "end_seconds", "enabled", "speed", "adjustment_layer",
                                     "source", "effects", "render_order", "procamp_before_lumetri"]
    assert len(panel.seen) == 2 and "playhead_ticks: playhead" in panel.seen[0]
    sent = panel.seen[1]
    assert f"// {MARK}\n" in sent and FIND_MARK in sent        # the recipe body, with find.jsx included
    assert (live / LOCK).exists() is False


def test_seconds_come_from_the_tick_strings_and_are_rounded(live, capsys):
    odd = raw_clip(start_ticks="12345678901", end_ticks=str(10 * TPS + 1), in_ticks="-5", out_ticks=str(TPS // 3))
    code, out, _ = clip_list(live, capsys, script_reply([odd]), "--sequence", "s1")
    got = out["clips"][0]
    assert (got["start_seconds"], got["end_seconds"]) == (0.048602, 10.0)
    assert (got["in_ticks"], got["out_ticks"]) == ("-5", str(TPS // 3))        # the ticks stay strings, as sent


@pytest.mark.parametrize("effects, order, flag", [
    (["Opacity", "Motion", "Lumetri Color", "ASC CDL", "ProcAmp"], ["ProcAmp", "ASC CDL", "Lumetri Color"], True),
    (["Opacity", "Motion", "ProcAmp", "Lumetri Color"], ["Lumetri Color", "ProcAmp"], False),
    (["Opacity", "Motion", "Lumetri Color", "ProcAmp", "Lumetri Color"], ["Lumetri Color", "ProcAmp", "Lumetri Color"], False),
    (["Opacity", "Motion", "Lumetri Color", "Lumetri Color", "ProcAmp"], ["ProcAmp", "Lumetri Color", "Lumetri Color"], True),
    (["Opacity", "Motion", "Lumetri Color"], ["Lumetri Color"], None),
    (["Opacity", "Motion", "ProcAmp"], ["ProcAmp"], None),
    (["Opacity", "Motion"], [], None),
    (["Volume", "Channel Volume", "Panner"], [], None),
    ([], [], None),
    (None, None, None),
], ids=["procamp-first", "lumetri-first", "first-occurrence-lumetri", "first-occurrence-procamp",
        "no-procamp", "no-lumetri", "intrinsic-only", "audio-intrinsic", "empty", "unreadable"])
def test_render_order_and_the_procamp_flag(live, capsys, effects, order, flag):
    code, out, _ = clip_list(live, capsys, script_reply([raw_clip(effects=effects)]), "--sequence", "s1")
    got = out["clips"][0]
    assert code == 0
    assert got["effects"] == effects                               # the component order stays as read
    assert got["render_order"] == order and got["procamp_before_lumetri"] is flag


def test_lumetri_before_procamp_is_flagged(live, capsys):
    reply = script_reply([raw_clip(effects=["Opacity", "Motion", "ProcAmp", "Lumetri Color"])])
    code, out, _ = clip_list(live, capsys, reply, "--sequence", "s1")
    assert out["clips"][0]["render_order"] == ["Lumetri Color", "ProcAmp"]
    assert out["clips"][0]["procamp_before_lumetri"] is False


def test_unreadable_fields_stay_null(live, capsys):
    blank = raw_clip(start_ticks=None, end_ticks=None, in_ticks=None, out_ticks=None, enabled=None, speed=None,
                     adjustment_layer=None, source={"node_id": None, "name": None, "path": None}, effects=None)
    code, out, _ = clip_list(live, capsys, script_reply([blank]), "--sequence", "s1")
    got = out["clips"][0]
    assert code == 0
    assert (got["start_seconds"], got["end_seconds"], got["enabled"], got["speed"], got["adjustment_layer"]) == (None,) * 5
    assert got["effects"] is None and got["render_order"] is None and got["procamp_before_lumetri"] is None


def test_clip_list_is_truncated_at_the_limit(live, capsys):
    page = [raw_clip(clip=i, node_id=f"n{i}") for i in range(400)]
    code, out, panel = clip_list(live, capsys, script_reply(page, total=950), "--sequence", "s1")
    assert code == 0
    assert (out["count"], out["total"], out["truncated"], out["next_offset"]) == (400, 950, True, 400)
    assert script_args(panel.seen[1])["limit"] == 400 and script_args(panel.seen[1])["offset"] == 0


def test_the_last_page_is_not_truncated(live, capsys):
    page = [raw_clip(clip=i, node_id=f"n{i}") for i in range(150)]
    code, out, panel = clip_list(live, capsys, script_reply(page, total=950), "--sequence", "s1", "--offset", "800")
    assert (out["count"], out["total"], out["truncated"], out["next_offset"]) == (150, 950, False, None)
    assert script_args(panel.seen[1])["offset"] == 800
    code, out, _ = clip_list(live, capsys, script_reply(page[:100], total=950), "--sequence", "s1", "--offset", "800",
                             "--limit", "100")
    assert (out["count"], out["truncated"], out["next_offset"]) == (100, True, 900)


def test_an_empty_listing(live, capsys):
    code, out, _ = clip_list(live, capsys, script_reply([]), "--sequence", "s1", "--track", "7")
    assert code == 0
    assert (out["clips"], out["count"], out["total"], out["truncated"], out["next_offset"]) == ([], 0, 0, False, None)


def test_clip_list_filters_reach_the_script(live, capsys):
    code, out, panel = clip_list(live, capsys, script_reply([]), "--sequence", "s9", "--track-type", "video", "--track", "1",
                                 "--offset", "400", "--limit", "25")
    assert code == 0
    assert script_args(panel.seen[1]) == {"sequence_id": "s9", "track_type": "video", "track": 1, "offset": 400,
                                          "limit": 25, "detail": False}
    code, out, panel = clip_list(live, capsys, script_reply([]))
    assert script_args(panel.seen[1]) == DEFAULT_ARGS


def test_clip_list_without_sequence_warns(live, capsys):
    reply = script_reply([raw_clip()], name="Main cut", used_active=True)
    code, out, panel = clip_list(live, capsys, reply)
    assert code == 0
    assert len(out["warnings"]) == 1 and "active sequence" in out["warnings"][0]
    assert "Main cut" in out["warnings"][0] and "s1" in out["warnings"][0]
    assert script_args(panel.seen[1])["sequence_id"] is None
    code, out, _ = clip_list(live, capsys, script_reply([raw_clip()], name="Main cut"), "--sequence", "s1")
    assert out["warnings"] == []


def test_the_guard_warnings_come_first(live, capsys):
    reply = script_reply([raw_clip()], name="Main cut", used_active=True)
    code, out, _ = clip_list(live, capsys, reply, state=STATE_26)
    assert code == 0 and len(out["warnings"]) == 2
    assert "26.0.0" in out["warnings"][0] and "active sequence" in out["warnings"][1]


def test_clip_list_unknown_sequence(live, capsys):
    refusal = Exception("Error: precondition: no sequence has the ID x")
    code, out, _ = clip_list(live, capsys, refusal, "--sequence", "x")
    assert code == 1 and out["ok"] is False
    assert out["code"] == "precondition" and out["error"] == "no sequence has the ID x"
    assert not (live / LOCK).exists()


def test_clip_list_no_active_sequence(live, capsys):
    refusal = Exception("Error: precondition: no sequence is active; pass --sequence")
    code, out, _ = clip_list(live, capsys, refusal)
    assert code == 1 and out["code"] == "precondition" and out["error"] == "no sequence is active; pass --sequence"


def test_clip_list_is_a_read(live, capsys, sends):
    # The open project is the owner's own film, not a Claude working copy: a read does not mind.
    code, out, panel = clip_list(live, capsys, script_reply([raw_clip()]), "--sequence", "s1", state=STATE_OWN)
    assert code == 0 and out["count"] == 1
    assert sends == [False, False] and len(panel.seen) == 2
    assert not (live / LOCK).exists()


def test_clip_list_detail_fields(live, capsys):
    extra = {"interpreted_fps": 25.0, "linked": ["000f4250"], "keyframed": ["Motion.Scale", "ProcAmp.Brightness"],
             "transitions": {"head": "Cross Dissolve", "tail": None}}
    code, out, panel = clip_list(live, capsys, script_reply([raw_clip(**extra)]), "--sequence", "s1", "--detail")
    assert script_args(panel.seen[1])["detail"] is True
    got = out["clips"][0]
    assert {k: got[k] for k in extra} == extra
    assert list(got)[-4:] == ["interpreted_fps", "linked", "keyframed", "transitions"]
    code, out, panel = clip_list(live, capsys, script_reply([raw_clip()]), "--sequence", "s1")
    assert script_args(panel.seen[1])["detail"] is False
    assert not {"interpreted_fps", "linked", "keyframed", "transitions"} & set(out["clips"][0])


@pytest.mark.parametrize("argv", [
    ["--limit", "0"], ["--limit", "401"], ["--limit", "-1"], ["--offset", "-1"], ["--track", "-1"],
    ["--track-type", "sound"], ["--limit", "ten"],
])
def test_a_bad_option_is_a_usage_error_and_nothing_is_sent(live, capsys, argv):
    with panel_on(live, handler=recipe_handler({MARK: script_reply([])})) as panel:
        assert dispatch(["--json", "live", "clip", "list", *argv]) == 2
    capsys.readouterr()
    assert panel.seen == []


@pytest.mark.parametrize("kwargs", [
    {"track_type": "sound"}, {"track": -1}, {"track": True}, {"track": 1.5}, {"offset": -1}, {"offset": "0"},
    {"limit": 0}, {"limit": clips.CLIP_LIMIT + 1}, {"limit": False}, {"sequence_id": ""}, {"sequence_id": 5},
    {"detail": True, "limit": clips.DETAIL_LIMIT + 1}, {"detail": True, "limit": clips.CLIP_LIMIT},
])
def test_list_clips_refuses_bad_arguments_before_sending(live, kwargs):
    client = BridgeClient(live, 0.1)
    with pytest.raises(CliError) as err:
        clips.list_clips(client, **kwargs)
    assert err.value.code == "bad_args"


@pytest.mark.parametrize("reply", [["not", "an", "object"], {"sequence": None, "total": 0, "clips": []},
                                    {"sequence": {"id": "s1", "name": "Main"}, "total": "many", "clips": []},
                                    {"sequence": {"id": "s1", "name": "Main"}, "total": 1, "clips": "none"}])
def test_a_script_that_answers_wrongly_is_a_bad_reply(live, capsys, reply):
    code, out, _ = clip_list(live, capsys, reply, "--sequence", "s1")
    assert code == 1 and out["code"] == "bad_reply"


def test_the_constants():
    assert clips.CLIP_LIMIT == 400 and clips.DETAIL_LIMIT == 100
    assert clips.INTRINSIC == ("Opacity", "Motion", "Volume", "Channel Volume", "Panner")


# ---------- human output and help ----------
def test_clip_list_human_lines(live, capsys):
    audio = raw_clip(track_type="audio", track=1, clip=3, node_id="000f4250", name="song.wav", enabled=False,
                     effects=["Volume", "Channel Volume", "Panner"])
    bare = raw_clip(track=2, clip=0, node_id=None, name="Title", start_ticks=None, end_ticks=None, effects=["Opacity", "Motion"])
    nameless = raw_clip(track=2, clip=1, node_id="000f4260", name=None, effects=[])
    with panel_on(live, handler=recipe_handler({MARK: script_reply([raw_clip(), audio, bare, nameless], total=9)})):
        assert dispatch(["live", "clip", "list", "--sequence", "s1"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Main (s1): 4 of 9 clips"
    assert lines[1] == "V1[0] 000f4243 0.000-6.006 testclip.mp4 | ProcAmp > ASC CDL > Lumetri Color"
    assert lines[2] == "A2[3] 000f4250 0.000-6.006 song.wav [disabled]"
    assert lines[3] == "V3[0] - ?-? Title"
    assert lines[4] == "V3[1] 000f4260 0.000-6.006 ?"          # an unreadable name is a question mark, not None
    assert any("--offset 4" in line for line in lines[5:])


def test_clip_list_human_with_nothing_to_show_and_with_warnings(live, capsys):
    reply = script_reply([], name="Main", used_active=True)
    with panel_on(live, handler=recipe_handler({MARK: reply}, state=STATE_26)):
        assert dispatch(["live", "clip", "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Main (s1): no clips match"
    assert [line.startswith("Warning: ") for line in lines[1:]] == [True, True]
    with panel_on(live, handler=recipe_handler({MARK: script_reply([], total=5)})):      # an offset past the end
        assert dispatch(["live", "clip", "list", "--sequence", "s1", "--offset", "10"]) == 0
    assert capsys.readouterr().out.splitlines() == ["Main (s1): 0 of 5 clips"]


def test_the_help_says_it_only_reads_and_how_to_page(capsys):
    assert dispatch(["live", "clip", "list", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "only reads" in text.lower()
    for word in ("--track", "--offset", "--limit", "--detail", "--sequence", "--track-type"):
        assert word in text
    assert "400" in text and "100 with --detail" in text and "at most 100" in text     # the two limits are in the help
    assert dispatch(["live", "--help"]) == 0
    assert "clip" in capsys.readouterr().out
    assert dispatch(["live", "clip", "--help"]) == 0
    assert "list" in capsys.readouterr().out


# ---------- the script, in node with a stub app ----------
def run_list(args=None, prelude="") -> tuple[dict, int]:
    """Run clip_list.jsx in node against the stub app. Returns (the parsed reply, how many clips had their
    node ID read, which counts the clips the script asked Premiere about)."""
    script = jsx.build(jsx.load("clip_list"), {**DEFAULT_ARGS, **(args or {})}, include=("find",))
    code = LIST_PRELUDE + prelude + "\nprocess.stdout.write(JSON.stringify({reply: String(" + script + "), touched: touched}));"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout
    outer = json.loads(out)
    return json.loads(outer["reply"]), outer["touched"]


FRAME = 10584000000     # 24 fps

LIST_PRELUDE = STUB_HELPERS + """
var touched = 0;
var FRAME = 10584000000;
function mprop(name, varying) {
  return {displayName: name, isTimeVarying: function () { if (varying === "throws") throw new Error("no keys"); return varying; }};
}
function mcomp(name, props) { return {displayName: name, matchName: "AE." + name, properties: coll("numItems", props)}; }
function mclip(id, name, start, end) {
  var c = {name: name, start: {ticks: String(start)}, end: {ticks: String(end)}, inPoint: {ticks: "0"},
    outPoint: {ticks: String(end - start)}, disabled: false,
    getSpeed: function () { return 1; }, isAdjustmentLayer: function () { return false; },
    projectItem: {nodeId: "P-" + id, name: name + ".mp4", getMediaPath: function () { return "/media/" + name + ".mp4"; },
                  getFootageInterpretation: function () { return {frameRate: 25}; }},
    components: coll("numItems", [mcomp("Opacity", [mprop("Opacity", false)]),
                                  mcomp("Motion", [mprop("Scale", false), mprop("Rotation", true)]),
                                  mcomp("ProcAmp", [mprop("Brightness", true), mprop("Contrast", "throws")])]),
    getLinkedItems: function () { return coll("numItems", [{nodeId: id}, {nodeId: "A-" + id}]); }};
  Object.defineProperty(c, "nodeId", {get: function () { touched++; return id; }, configurable: true});
  return c;
}
function mtrack(clips, transitions) { return {clips: coll("numItems", clips), transitions: coll("numItems", transitions || [])}; }
function span(startFrames, endFrames, name) {
  var t = {start: {ticks: String(startFrames * FRAME)}, end: {ticks: String(endFrames * FRAME)}};
  if (name === "throws") Object.defineProperty(t, "name", {get: function () { throw new Error("no name"); }});
  else t.name = name;
  return t;
}
var seqA = {sequenceID: "S-A", name: "Cut A", timebase: String(FRAME),
  videoTracks: coll("numTracks", [
    mtrack([mclip("v0c0", "Intro", 0, 100 * FRAME), mclip("v0c1", "Middle", 100 * FRAME, 200 * FRAME),
            mclip("v0c2", "End", 250 * FRAME, 300 * FRAME)],
           [span(88, 112, "Cross Dissolve"), span(250, 262, "Dip to Black")]),
    mtrack([mclip("v1c0", "Title", 0, 50 * FRAME), mclip("v1c1", "Lower third", 60 * FRAME, 90 * FRAME)])]),
  audioTracks: coll("numTracks", [mtrack([mclip("a0c0", "Music", 0, 300 * FRAME), mclip("a0c1", "Room", 300 * FRAME, 400 * FRAME)])])};
var seqB = {sequenceID: "S-B", name: "Cut B", timebase: String(FRAME),
  videoTracks: coll("numTracks", [mtrack([mclip("b0c0", "Other", 0, 10 * FRAME)])]),
  audioTracks: coll("numTracks", [])};
var app = {project: {path: "/p/X Claude.prproj", sequences: coll("numSequences", [seqA, seqB]), activeSequence: seqA}};
"""


def ids(reply: dict) -> list[str]:
    return [c["node_id"] for c in reply["data"]["clips"]]


@needs_node
def test_clip_list_script_is_es3_and_runs_in_node():
    text = jsx.load("clip_list")
    assert text.split("\n")[0] == "// recipe: clip_list"
    for pattern in BANNED + [r"\.indexOf\(", r"\bObject\.", r"\.reduce\(", r"\.some\(", r"\.every\("]:
        assert not re.search(pattern, text), pattern
    reply, touched = run_list()
    data = reply["data"]
    assert reply["success"] is True and data["used_active"] is True and data["total"] == 7
    assert data["sequence"] == {"id": "S-A", "name": "Cut A"}
    assert len(ids(reply)) == 7
    first = data["clips"][0]
    assert first == {
        "track_type": "video", "track": 0, "clip": 0, "node_id": "v0c0", "name": "Intro",
        "start_ticks": "0", "end_ticks": str(100 * FRAME), "in_ticks": "0", "out_ticks": str(100 * FRAME),
        "enabled": True, "speed": 1, "adjustment_layer": False,
        "source": {"node_id": "P-v0c0", "name": "Intro.mp4", "path": "/media/Intro.mp4"},
        "effects": ["Opacity", "Motion", "ProcAmp"]}
    assert all(isinstance(c["start_ticks"], str) and isinstance(c["end_ticks"], str) for c in data["clips"])
    assert touched == 7


@needs_node
def test_clip_list_script_orders_and_pages():
    everything = ["v0c0", "v0c1", "v0c2", "v1c0", "v1c1", "a0c0", "a0c1"]
    reply, _ = run_list()
    assert ids(reply) == everything and reply["data"]["total"] == 7
    assert [(c["track_type"], c["track"], c["clip"]) for c in reply["data"]["clips"]][2:6] == [
        ("video", 0, 2), ("video", 1, 0), ("video", 1, 1), ("audio", 0, 0)]


@needs_node
def test_clip_list_script_honors_offset_limit_and_asks_only_inside_the_window():
    reply, touched = run_list({"offset": 2, "limit": 3})
    assert ids(reply) == ["v0c2", "v1c0", "v1c1"] and reply["data"]["total"] == 7
    assert touched == 3                                  # the other four clips were counted, not read
    reply, touched = run_list({"offset": 6, "limit": 400})
    assert ids(reply) == ["a0c1"] and touched == 1
    reply, touched = run_list({"offset": 7, "limit": 5})
    assert ids(reply) == [] and reply["data"]["total"] == 7 and touched == 0
    reply, touched = run_list({"offset": 0, "limit": 1})
    assert ids(reply) == ["v0c0"] and reply["data"]["total"] == 7 and touched == 1


@needs_node
def test_clip_list_script_sends_ticks_as_strings_whatever_the_host_gives():
    # A host that hands over a number (here past 2^32) or a text that is not digits: strings or null come out.
    setup = """
(function () {
  var c = seqA.videoTracks[0].clips[0];
  c.start = {ticks: 254016000000};
  c.end = {ticks: "12.5"};
  c.inPoint = {};
  delete c.outPoint;
})();
"""
    reply, _ = run_list({"limit": 1}, prelude=setup)
    first = reply["data"]["clips"][0]
    assert (first["start_ticks"], first["end_ticks"], first["in_ticks"], first["out_ticks"]) == ("254016000000", None, None, None)


@needs_node
def test_clip_list_script_filters_by_type_and_track():
    reply, _ = run_list({"track_type": "audio"})
    assert ids(reply) == ["a0c0", "a0c1"] and reply["data"]["total"] == 2
    reply, _ = run_list({"track_type": "video", "track": 1})
    assert ids(reply) == ["v1c0", "v1c1"] and reply["data"]["total"] == 2
    reply, _ = run_list({"track": 0})                    # track 0 of both families
    assert ids(reply) == ["v0c0", "v0c1", "v0c2", "a0c0", "a0c1"] and reply["data"]["total"] == 5
    reply, _ = run_list({"track": 5})
    assert ids(reply) == [] and reply["data"]["total"] == 0
    reply, _ = run_list({"track_type": "video", "offset": 3, "limit": 1})
    assert ids(reply) == ["v1c0"] and reply["data"]["total"] == 5


@needs_node
def test_clip_list_script_by_id_and_the_refusals():
    reply, _ = run_list({"sequence_id": "S-B"})
    assert ids(reply) == ["b0c0"] and reply["data"]["used_active"] is False
    assert reply["data"]["sequence"] == {"id": "S-B", "name": "Cut B"}
    reply, _ = run_list({"sequence_id": "nope"})
    assert reply == {"success": False, "error": "Error: precondition: no sequence has the ID nope"}
    reply, _ = run_list(prelude="\napp.project.activeSequence = null;")
    assert reply["error"].startswith("Error: precondition: no sequence is active; pass --sequence")
    reply, _ = run_list({"sequence_id": "S-B"}, prelude="\napp.project.activeSequence = null;")
    assert reply["data"]["total"] == 1                   # an ID does not need an active sequence
    for bad in ({"track_type": "sound"}, {"offset": -1}, {"limit": 0}, {"track": -2}, {"track": 1.5}):
        reply, _ = run_list(bad)
        assert reply["success"] is False and reply["error"].startswith("Error: precondition: ")


@needs_node
def test_clip_list_script_enabled_follows_the_server_rule():
    cases = [("c.disabled = true;", False), ("c.disabled = false;", True), ("c.disabled = 1;", False),
             ("c.disabled = 0;", True),
             ("delete c.disabled; c.isDisabled = function () { return true; };", False),
             ("delete c.disabled; c.isDisabled = function () { return false; };", True),
             ("c.disabled = 'maybe'; c.isDisabled = function () { return true; };", False),
             ("delete c.disabled;", None),
             ("delete c.disabled; c.isDisabled = function () { throw new Error('x'); };", None),
             ("Object.defineProperty(c, 'disabled', {get: function () { throw new Error('x'); }});", None),
             ("Object.defineProperty(c, 'disabled', {get: function () { throw new Error('x'); }});"
              " c.isDisabled = function () { return true; };", False)]
    for setup, want in cases:
        reply, _ = run_list({"limit": 1}, prelude="\n(function () { var c = seqA.videoTracks[0].clips[0]; " + setup + " })();")
        assert reply["data"]["clips"][0]["enabled"] is want, setup


@needs_node
def test_clip_list_script_survives_throwing_calls():
    setup = """
(function () {
  var c = seqA.videoTracks[0].clips[0];
  c.getSpeed = function () { throw new Error("no speed"); };
  c.isAdjustmentLayer = function () { throw new Error("no adjustment"); };
  c.projectItem.getMediaPath = function () { throw new Error("no path"); };
  Object.defineProperty(c.components, "numItems", {get: function () { throw new Error("no components"); }});
  var d = seqA.videoTracks[0].clips[1];
  Object.defineProperty(d, "projectItem", {get: function () { throw new Error("no item"); }});
})();
"""
    reply, _ = run_list({"limit": 2}, prelude=setup)
    first, second = reply["data"]["clips"]
    assert reply["success"] is True and first["node_id"] == "v0c0" and second["node_id"] == "v0c1"
    assert (first["speed"], first["adjustment_layer"], first["effects"]) == (None, None, None)
    assert first["source"] == {"node_id": "P-v0c0", "name": "Intro.mp4", "path": None}
    assert second["source"] == {"node_id": None, "name": None, "path": None}
    assert second["speed"] == 1 and second["effects"] == ["Opacity", "Motion", "ProcAmp"]


@needs_node
def test_clip_list_script_has_no_detail_fields_unless_asked():
    reply, _ = run_list({"limit": 1})
    assert not {"interpreted_fps", "linked", "keyframed", "transitions"} & set(reply["data"]["clips"][0])
    reply, _ = run_list({"limit": 1, "detail": False})
    assert not {"interpreted_fps", "linked", "keyframed", "transitions"} & set(reply["data"]["clips"][0])


@needs_node
def test_clip_list_script_detail_fields():
    reply, _ = run_list({"limit": 2, "detail": True})
    first = reply["data"]["clips"][0]
    assert first["interpreted_fps"] == 25
    assert first["linked"] == ["A-v0c0"]                 # the clip itself is left out
    assert first["keyframed"] == ["Motion.Rotation", "ProcAmp.Brightness"]   # Contrast threw: skipped
    assert first["transitions"] == {"head": None, "tail": "Cross Dissolve"}


@needs_node
def test_clip_list_detail_survives_a_throwing_call():
    setup = """
(function () {
  var c = seqA.videoTracks[0].clips[0];
  c.projectItem.getFootageInterpretation = function () { throw new Error("no interpretation"); };
  c.getLinkedItems = function () { throw new Error("no links"); };
  Object.defineProperty(c.components, "numItems", {get: function () { throw new Error("no components"); }});
})();
"""
    reply, _ = run_list({"limit": 2, "detail": True}, prelude=setup)
    first, second = reply["data"]["clips"]
    assert reply["success"] is True and first["node_id"] == "v0c0"
    assert first["interpreted_fps"] is None and first["linked"] is None and first["keyframed"] is None
    assert first["transitions"] == {"head": None, "tail": "Cross Dissolve"}      # the other calls still answer
    assert second["interpreted_fps"] == 25 and second["linked"] == ["A-v0c1"]
    assert second["keyframed"] == ["Motion.Rotation", "ProcAmp.Brightness"]


@needs_node
def test_clip_list_script_transitions_by_the_span_that_covers_the_cut():
    reply, _ = run_list({"track_type": "video", "track": 0, "detail": True})
    first, middle, last = (c["transitions"] for c in reply["data"]["clips"])
    assert first == {"head": None, "tail": "Cross Dissolve"}      # a dissolve centred on the cut belongs to both clips
    assert middle == {"head": "Cross Dissolve", "tail": None}
    assert last == {"head": "Dip to Black", "tail": None}         # a transition that starts at the cut
    other, _ = run_list({"track_type": "video", "track": 1, "detail": True})
    assert [c["transitions"] for c in other["data"]["clips"]] == [{"head": None, "tail": None}] * 2   # none on this track


@needs_node
def test_clip_list_script_transition_edge_cases():
    drift = "\nseqA.videoTracks[0].transitions = coll('numItems', [span(88, 99.6, 'Near'), span(300.6, 320, 'Far')]);"
    reply, _ = run_list({"track_type": "video", "track": 0, "detail": True}, prelude=drift)
    first, middle, last = (c["transitions"] for c in reply["data"]["clips"])
    assert first["tail"] == "Near" and middle["head"] == "Near"      # 0.4 frame short of the cut still covers it
    assert last["tail"] is None                                      # 0.6 frame past the end does not
    for name in ("'throws'", "''", "5", "null"):       # a name that cannot be read, is empty, or is not a text
        unnamed = "\nseqA.videoTracks[0].transitions = coll('numItems', [span(88, 112, %s)]);" % name
        reply, _ = run_list({"track_type": "video", "track": 0, "detail": True, "limit": 3}, prelude=unnamed)
        # The presence of the transition is never lost: (unnamed) where it covers an end of the clip, null elsewhere.
        assert [c["transitions"] for c in reply["data"]["clips"]] == [
            {"head": None, "tail": "(unnamed)"}, {"head": "(unnamed)", "tail": None}, {"head": None, "tail": None}], name
    broken = "\nObject.defineProperty(seqA.videoTracks[0], 'transitions', {get: function () { throw new Error('no transitions'); }});"
    reply, _ = run_list({"track_type": "video", "track": 0, "detail": True, "limit": 2}, prelude=broken)
    assert reply["success"] is True
    assert [c["transitions"] for c in reply["data"]["clips"]] == [None, None]
    assert reply["data"]["clips"][0]["interpreted_fps"] == 25


@needs_node
def test_clip_list_runs_end_to_end_through_the_command(live, capsys):
    """The command and the real script together: the panel runs clip_list.jsx in node."""
    def in_node(script: str):
        code = LIST_PRELUDE + "\nprocess.stdout.write(String(" + script + "));"
        reply = json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout)
        assert reply["success"] is True, reply
        return reply["data"]
    with panel_on(live, handler=recipe_handler({MARK: in_node})) as panel:
        code, out = run_json(capsys, ["live", "clip", "list", "--sequence", "S-A", "--track-type", "video",
                                      "--offset", "1", "--limit", "3", "--detail"])
        assert dispatch(["live", "clip", "list", "--track", "0", "--limit", "2"]) == 0
    assert code == 0
    assert [c["node_id"] for c in out["clips"]] == ["v0c1", "v0c2", "v1c0"]
    assert (out["count"], out["total"], out["truncated"], out["next_offset"], out["warnings"]) == (3, 5, True, 4, [])
    middle = out["clips"][0]
    assert (middle["start_seconds"], middle["end_seconds"]) == (round(100 * FRAME / TPS, 6), round(200 * FRAME / TPS, 6))
    assert middle["render_order"] == ["ProcAmp"] and middle["procamp_before_lumetri"] is None
    assert middle["transitions"] == {"head": "Cross Dissolve", "tail": None} and middle["interpreted_fps"] == 25
    text = capsys.readouterr().out.splitlines()
    assert text[0] == "Cut A (S-A): 2 of 5 clips" and text[1].startswith("V1[0] v0c0 0.000-")
    assert "active sequence" in text[-1]
    assert script_args(panel.seen[-1])["track"] == 0


# ---------- fix round 1: one bad read, the detail limit, the tick boundary, a broken recipes module ----------
CORE_FIELDS = [("name", "name"), ("start", "start_ticks"), ("end", "end_ticks"), ("inPoint", "in_ticks"),
               ("outPoint", "out_ticks"), ("nodeId", "node_id")]


def throwing(field: str) -> str:
    """Stub setup: reading `field` of the second clip of the first video track throws."""
    return ("\nObject.defineProperty(seqA.videoTracks[0].clips[1], '%s', "
            "{get: function () { throw new Error('no %s'); }, configurable: true});" % (field, field))


@needs_node
@pytest.mark.parametrize("field, key", CORE_FIELDS, ids=[f for f, _ in CORE_FIELDS])
def test_one_throwing_core_read_gives_null_and_the_list_goes_on(field, key):
    base, _ = run_list({"limit": 5})
    reply, _ = run_list({"limit": 5}, prelude=throwing(field))
    assert reply["success"] is True and reply["data"]["total"] == 7
    found, expected = reply["data"]["clips"], base["data"]["clips"]
    assert len(found) == 5
    assert found[1] == {**expected[1], key: None}                  # only that one field of that one clip is null
    assert [found[i] for i in (0, 2, 3, 4)] == [expected[i] for i in (0, 2, 3, 4)]


@needs_node
@pytest.mark.parametrize("field, key", CORE_FIELDS, ids=[f for f, _ in CORE_FIELDS])
def test_one_throwing_core_read_with_detail_still_lists_every_clip(field, key):
    reply, _ = run_list({"limit": 5, "detail": True}, prelude=throwing(field))
    assert reply["success"] is True and len(reply["data"]["clips"]) == 5
    assert reply["data"]["clips"][1][key] is None
    assert reply["data"]["clips"][2]["name"] == "End" and reply["data"]["clips"][2]["linked"] == ["A-v0c2"]


def test_detail_lists_at_most_100_clips(live, capsys):
    page = script_reply([raw_clip()])
    code, out, panel = clip_list(live, capsys, page, "--sequence", "s1", "--detail")
    assert code == 0 and script_args(panel.seen[1])["limit"] == 100 and script_args(panel.seen[1])["detail"] is True
    code, out, panel = clip_list(live, capsys, page, "--sequence", "s1", "--detail", "--limit", "100")
    assert code == 0 and script_args(panel.seen[1])["limit"] == 100
    code, out, panel = clip_list(live, capsys, page, "--sequence", "s1", "--limit", "250")
    assert code == 0 and script_args(panel.seen[1])["limit"] == 250 and script_args(panel.seen[1])["detail"] is False
    code, out, panel = clip_list(live, capsys, page, "--sequence", "s1", "--detail", "--limit", "101")
    assert code == 1 and out["ok"] is False and out["code"] == "bad_args"
    assert "100" in out["error"] and "Nothing was sent" in out["error"]
    assert panel.seen == [] and not (live / LOCK).exists()


def test_list_clips_takes_its_default_limit_from_detail(live):
    client = BridgeClient(live, 2.0)
    for detail, limit in ((False, 400), (True, 100)):
        with panel_on(live, handler=recipe_handler({MARK: script_reply([], total=0)})) as panel:
            clips.list_clips(client, sequence_id="s1", detail=detail)
        assert script_args(panel.seen[1])["limit"] == limit


def test_ticks_of_a_script_that_misbehaves_are_strings_or_null(live, capsys):
    odd = raw_clip(start_ticks=254016000000, end_ticks=254016000000.0, in_ticks=True, out_ticks=[5])
    code, out, _ = clip_list(live, capsys, script_reply([odd]), "--sequence", "s1")
    got = out["clips"][0]
    assert code == 0
    assert (got["start_ticks"], got["end_ticks"], got["in_ticks"], got["out_ticks"]) == ("254016000000", "254016000000", None, None)
    assert (got["start_seconds"], got["end_seconds"]) == (1.0, 1.0)
    half = raw_clip(start_ticks=12.5, end_ticks=-3, in_ticks="7", out_ticks=None)
    code, out, _ = clip_list(live, capsys, script_reply([half]), "--sequence", "s1")
    got = out["clips"][0]
    assert (got["start_ticks"], got["end_ticks"], got["in_ticks"], got["out_ticks"]) == ("12.5", "-3", "7", None)
    assert got["start_seconds"] is None and got["end_seconds"] == round(-3 / clips.TICKS_PER_SECOND, 6)
    assert all(not isinstance(got[k], (int, float)) for k in ("start_ticks", "end_ticks", "in_ticks", "out_ticks"))


@pytest.fixture
def broken_recipes(live, monkeypatch):
    """A fresh live_cli whose import of live_recipes fails. The modules, the package attributes and the lazy
    groups of the CLI are put back after the test, so no other test sees any of it."""
    import cli_anything.premiere as package
    from cli_anything.premiere.premiere_cli import cli
    original = sys.modules[LIVE_CLI]
    monkeypatch.setitem(sys.modules, RECIPES, None)              # importing live_recipes now raises ImportError
    monkeypatch.delattr(package, "live_recipes", raising=False)  # else `from . import live_recipes` finds the attribute
    monkeypatch.setattr(package, "live_cli", original)           # the fresh import below replaces it; this restores it
    monkeypatch.delitem(sys.modules, LIVE_CLI)
    for name in ("live", "mcp", "ui", "recovery", "doctor"):     # the lazy groups keep the group they loaded first
        monkeypatch.setattr(cli.commands[name], "_target", None)
    fresh = importlib.import_module(LIVE_CLI)
    monkeypatch.setattr(fresh, "make_client", lambda wait, **kw: BridgeClient(live, wait, **kw))
    assert fresh is not original
    return fresh


def test_a_broken_recipes_module_leaves_the_other_commands_working(broken_recipes, live, capsys):
    assert broken_recipes.RECIPES_ERROR and "live_recipes" in broken_recipes.RECIPES_ERROR
    for group in ("doctor", "recovery", "mcp", "ui", "live"):
        assert dispatch([group, "--help"]) == 0, group
        assert capsys.readouterr().out.strip(), group
    with panel_on(live, handler=recipe_handler({})) as panel:
        code, out = run_json(capsys, ["live", "state"])
    assert code == 0 and out["version"] == "27.0.0" and len(panel.seen) == 1


def test_a_broken_recipes_module_shows_in_the_live_help_and_clip_list_is_a_usage_error(broken_recipes, capsys):
    assert dispatch(["live", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert f"The recipe commands are not available: {broken_recipes.RECIPES_ERROR}" in text
    assert "state" in text and "run" in text                 # the commands of live_cli are still listed
    assert "clip" not in broken_recipes.live.commands
    assert dispatch(["live", "clip", "list"]) == 2
    assert "No such command" in capsys.readouterr().err


def test_the_live_help_is_quiet_when_the_recipes_load(capsys):
    assert live_cli.RECIPES_ERROR is None
    assert dispatch(["live", "--help"]) == 0
    text = capsys.readouterr().out
    assert "not available" not in text and "clip" in text


@pytest.mark.parametrize("first", [RECIPES, LIVE_CLI, "cli_anything.premiere.core.recipes.clips"])
def test_the_live_group_has_clip_list_whichever_module_loads_first(first):
    code = (f"import importlib; importlib.import_module({first!r}); from cli_anything.premiere import live_cli; "
            "print(live_cli.RECIPES_ERROR, sorted(live_cli.live.commands['clip'].commands))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True, cwd=ROOT).stdout
    assert out.strip().splitlines()[-1] == "None ['list']"
