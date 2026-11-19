"""Tests for `live effect get` and `live effect set`: the recipes (core/recipes/effects.py), its script (core/scripts/effect_get.jsx)
and the command (live_recipes.py).

The command runs against a fake panel in a temp folder. The script runs in node with a stub `app`, alone
and, in one test, behind the fake panel together with the Python side.
"""
from __future__ import annotations

import contextlib
import json
import re
import subprocess

import pytest

from cli_anything.premiere import live_cli
from cli_anything.premiere.core import guard, jsx
from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.core.recipes import effects
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.premiere_cli import dispatch
from cli_anything.premiere.core import bridge
from cli_anything.premiere.tests.fakes import (  # noqa: F401
    RECIPE_STATE, EffectModel, FakePanel, bridge_dir, keep_signals, recipe_handler, script_args)
from cli_anything.premiere.tests.test_jsx import BANNED, NODE, STUB_HELPERS, needs_node

MARK = "recipe: effect_get"
FIND_MARK = "// find.jsx: target finders for recipe scripts"
LOCK = ".host-owner-lease"
TPS = 254016000000
STATE_OWN = {**RECIPE_STATE, "projects": [{"name": "Family film.prproj", "path": "/p/Family film.prproj", "active": True}]}
STATE_26 = {**RECIPE_STATE, "version": "26.0.0"}
BY_TRACK = ["--track", "0", "--clip", "0"]


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


def raw_prop(index, name, value, kind="number", keyframed=False) -> dict:
    """One property as the script returns it."""
    return {"index": index, "name": name, "value": value, "kind": kind, "keyframed": keyframed}


def raw_effect(effect_index, name, match_name, props) -> dict:
    return {"effect_index": effect_index, "name": name, "match_name": match_name, "properties": props}


def raw_block(**over) -> dict:
    block = {"node_id": "000f4243", "name": "testclip.mp4", "track_type": "video", "track": 0, "clip": 0,
             "start_ticks": "0"}
    block.update(over)
    return block


def two_effects() -> list[dict]:
    return [
        raw_effect(3, "ProcAmp", "AE.ADBE ProcAmp", [raw_prop(0, "Brightness", 7.69999980926514),
                                                     raw_prop(4, "Split Screen", False, "boolean")]),
        raw_effect(4, "Lumetri Color", "AE.ADBE Lumetri", [raw_prop(1, "Input LUT", "/luts/a.cube", "string"),
                                                           raw_prop(13, "White Balance", [255, 192, 192, 192], "color"),
                                                           raw_prop(14, "Temperature", -46.5, "number", True)])]


def script_reply(found, *, sequence_name="Main", used_active=False, **block) -> dict:
    return {"sequence": {"id": "s1", "name": sequence_name}, "used_active": used_active, "clip": raw_block(**block),
            "effects": found}


def effect_get(live, capsys, reply, *argv, state=None):  # noqa: F811
    """Run `live effect get` against a panel that answers with `reply`; returns (code, payload, panel)."""
    with panel_on(live, handler=recipe_handler({MARK: reply}, state=state)) as panel:
        code, out = run_json(capsys, ["live", "effect", "get", *argv])
    return code, out, panel


# ---------- the command and the payload ----------
def test_effect_get_lists_by_effect_index(live, capsys):
    code, out, panel = effect_get(live, capsys, script_reply(two_effects()), "--sequence", "s1", *BY_TRACK)
    assert code == 0 and out.pop("ok") is True
    assert list(out) == ["clip", "effects", "warnings"] and out["warnings"] == []
    assert out["clip"] == {"sequence_id": "s1", "track_type": "video", "track": 0, "clip": 0, "node_id": "000f4243",
                           "name": "testclip.mp4", "start_ticks": "0", "start_seconds": 0.0}
    assert list(out["clip"]) == ["sequence_id", "track_type", "track", "clip", "node_id", "name", "start_ticks",
                                 "start_seconds"]
    assert [e["effect_index"] for e in out["effects"]] == [3, 4]
    assert [(e["name"], e["match_name"]) for e in out["effects"]] == [("ProcAmp", "AE.ADBE ProcAmp"),
                                                                    ("Lumetri Color", "AE.ADBE Lumetri")]
    assert list(out["effects"][0]) == ["effect_index", "name", "match_name", "properties"]
    assert out["effects"][0]["properties"] == [
        {"index": 0, "name": "Brightness", "value": 7.69999980926514, "kind": "number", "keyframed": False},
        {"index": 4, "name": "Split Screen", "value": False, "kind": "boolean", "keyframed": False}]
    assert list(out["effects"][0]["properties"][0]) == ["index", "name", "value", "kind", "keyframed"]
    assert out["effects"][1]["properties"][1] == {"index": 13, "name": "White Balance", "value": [255, 192, 192, 192],
                                                  "kind": "color", "keyframed": False}
    assert out["effects"][1]["properties"][2]["keyframed"] is True
    assert len(panel.seen) == 2 and "playhead_ticks: playhead" in panel.seen[0]
    sent = panel.seen[1]
    assert f"// {MARK}\n" in sent and FIND_MARK in sent        # the recipe body, with find.jsx included
    assert script_args(sent) == {"sequence_id": "s1", "target": {"track_type": "video", "track": 0, "clip": 0},
                                 "effect": None, "effect_index": None}
    assert (live / LOCK).exists() is False


def test_effect_get_filter_by_name_and_index(live, capsys):
    code, out, panel = effect_get(live, capsys, script_reply([]), "--sequence", "s9", "--track", "1", "--clip", "2",
                                  "--effect", "Lumetri Color", "--effect-index", "2")
    assert code == 0
    assert script_args(panel.seen[1]) == {"sequence_id": "s9", "target": {"track_type": "video", "track": 1, "clip": 2},
                                          "effect": "Lumetri Color", "effect_index": 2}
    code, out, panel = effect_get(live, capsys, script_reply([]), *BY_TRACK, "--effect-index", "0")
    assert script_args(panel.seen[1])["effect"] is None and script_args(panel.seen[1])["effect_index"] == 0
    code, out, panel = effect_get(live, capsys, script_reply([]), *BY_TRACK, "--effect", "AE.ADBE Lumetri")
    assert script_args(panel.seen[1])["effect"] == "AE.ADBE Lumetri" and script_args(panel.seen[1])["effect_index"] is None


def test_effect_get_targets_reach_the_script(live, capsys):
    code, out, panel = effect_get(live, capsys, script_reply([]), "--node-id", "000f4250")
    assert code == 0 and script_args(panel.seen[1])["target"] == {"node_id": "000f4250"}
    assert script_args(panel.seen[1])["sequence_id"] is None
    code, out, panel = effect_get(live, capsys, script_reply([]), "--track-type", "audio", "--track", "1", "--clip", "3")
    assert script_args(panel.seen[1])["target"] == {"track_type": "audio", "track": 1, "clip": 3}
    code, out, panel = effect_get(live, capsys, script_reply([]), "--track-type", "video", "--track", "0", "--clip", "0")
    assert script_args(panel.seen[1])["target"] == {"track_type": "video", "track": 0, "clip": 0}


def test_effect_get_blank_names_survive(live, capsys):
    found = [raw_effect(2, "Lumetri Color", "AE.ADBE Lumetri", [
        raw_prop(4, " ", "/luts/a.cube", "string"), raw_prop(17, "", False, "boolean"),
        raw_prop(18, "  x ", 1), raw_prop(19, None, None, "unreadable", None)])]
    code, out, _ = effect_get(live, capsys, script_reply(found), *BY_TRACK)
    assert [p["name"] for p in out["effects"][0]["properties"]] == [" ", "", "  x ", None]


def test_effect_get_keeps_the_whole_long_value_in_json(live, capsys):
    path = "/Users/someone/My Drive/" + "luts/" * 30 + "identity.cube"
    found = [raw_effect(2, "Lumetri Color", "AE.ADBE Lumetri", [raw_prop(4, " ", path, "string")])]
    code, out, _ = effect_get(live, capsys, script_reply(found), *BY_TRACK)
    assert len(path) > 60 and out["effects"][0]["properties"][0]["value"] == path


def test_every_kind_passes_through_as_the_script_read_it(live, capsys):
    props = [raw_prop(0, "n", 5), raw_prop(1, "s", "x", "string"), raw_prop(2, "b", True, "boolean"),
             raw_prop(3, "c", [255, 1, 2, 3], "color"), raw_prop(4, "p", [0.5, 0.25], "array"),
             raw_prop(5, "u", None, "unreadable", None)]
    code, out, _ = effect_get(live, capsys, script_reply([raw_effect(0, "Kinds", "AE.Kinds", props)]), *BY_TRACK)
    got = out["effects"][0]["properties"]
    assert [(p["kind"], p["value"]) for p in got] == [("number", 5), ("string", "x"), ("boolean", True),
                                                      ("color", [255, 1, 2, 3]), ("array", [0.5, 0.25]),
                                                      ("unreadable", None)]
    assert got[5]["keyframed"] is None
    assert effects.KINDS == ("number", "string", "boolean", "color", "array", "unreadable")


def test_an_effect_whose_properties_cannot_be_read_has_null_properties(live, capsys):
    found = [raw_effect(0, "Opacity", "AE.ADBE Opacity", None), raw_effect(1, "Motion", "AE.ADBE Motion", [])]
    code, out, _ = effect_get(live, capsys, script_reply(found), *BY_TRACK)
    assert code == 0 and [e["properties"] for e in out["effects"]] == [None, []]


def test_the_clip_comes_from_the_reply_and_the_seconds_from_its_ticks(live, capsys):
    reply = script_reply([], node_id="000f4250", name="song.wav", track_type="audio", track=2, clip=5,
                         start_ticks=str(10 * TPS + 1))
    code, out, _ = effect_get(live, capsys, reply, "--node-id", "000f4250")
    assert out["clip"] == {"sequence_id": "s1", "track_type": "audio", "track": 2, "clip": 5, "node_id": "000f4250",
                           "name": "song.wav", "start_ticks": str(10 * TPS + 1), "start_seconds": 10.0}
    for ticks, seconds in ((None, None), ("12.5", None), ("-5", round(-5 / TPS, 6)), (7, round(7 / TPS, 6)), (True, None)):
        code, out, _ = effect_get(live, capsys, script_reply([], start_ticks=ticks), *BY_TRACK)
        got = out["clip"]
        assert got["start_seconds"] == seconds and (got["start_ticks"] is None or isinstance(got["start_ticks"], str))
    code, out, _ = effect_get(live, capsys, script_reply([], name=None, node_id=None), *BY_TRACK)
    assert code == 0 and out["clip"]["name"] is None and out["clip"]["node_id"] is None


def test_effect_get_without_sequence_warns(live, capsys):
    reply = script_reply(two_effects(), sequence_name="Main cut", used_active=True)
    code, out, panel = effect_get(live, capsys, reply, *BY_TRACK)
    assert code == 0 and len(out["warnings"]) == 1
    assert out["warnings"][0] == "No --sequence was given, so this reads the active sequence: Main cut (s1)."
    assert out["clip"]["sequence_id"] == "s1" and script_args(panel.seen[1])["sequence_id"] is None
    code, out, _ = effect_get(live, capsys, script_reply(two_effects()), "--sequence", "s1", *BY_TRACK)
    assert out["warnings"] == []


def test_the_guard_warnings_come_first(live, capsys):
    reply = script_reply([], sequence_name="Main cut", used_active=True)
    code, out, _ = effect_get(live, capsys, reply, *BY_TRACK, state=STATE_26)
    assert code == 0 and len(out["warnings"]) == 2
    assert "26.0.0" in out["warnings"][0] and "active sequence" in out["warnings"][1]


@pytest.mark.parametrize("text", [
    "no sequence has the ID x", "no sequence is active; pass --sequence", "no effect named Sepia on this clip",
    "effect 3 of this clip is ProcAmp, not Lumetri Color", "this clip has no effect at index 9",
    "no clip has the node ID z in sequence Main", "video track 0 has no clip 4"])
def test_a_refusal_of_the_script_is_a_precondition(live, capsys, text):
    code, out, panel = effect_get(live, capsys, Exception("Error: precondition: " + text), "--sequence", "x", *BY_TRACK)
    assert code == 1 and out["ok"] is False and out["code"] == "precondition" and out["error"] == text
    assert not (live / LOCK).exists() and len(panel.seen) == 2


def test_effect_get_is_a_read(live, capsys, sends):
    # The open project is the owner's own film, not a Claude working copy: a read does not mind.
    code, out, panel = effect_get(live, capsys, script_reply(two_effects()), "--sequence", "s1", *BY_TRACK, state=STATE_OWN)
    assert code == 0 and len(out["effects"]) == 2
    assert sends == [False, False] and len(panel.seen) == 2
    assert not (live / LOCK).exists()


# ---------- bad input: nothing is sent ----------
NAME_A_CLIP = "Name the clip with --track and --clip together, or with --node-id."
NODE_ALONE = "--node-id cannot be used with --track, --clip or --track-type."


@pytest.mark.parametrize("argv, text", [
    ([], NAME_A_CLIP), (["--track", "1"], NAME_A_CLIP), (["--clip", "1"], NAME_A_CLIP),
    (["--effect", "Motion"], NAME_A_CLIP), (["--track-type", "audio"], NAME_A_CLIP),
    (["--node-id", "000f4243", "--track", "1"], NODE_ALONE), (["--node-id", "000f4243", "--clip", "1"], NODE_ALONE),
    (["--node-id", "000f4243", "--track-type", "audio"], NODE_ALONE),
    (["--node-id", "000f4243", "--track", "1", "--clip", "1"], NODE_ALONE),
    (["--track", "-1", "--clip", "0"], "Invalid value for '--track'"),
    (["--track", "0", "--clip", "-1"], "Invalid value for '--clip'"),
    (["--track", "x", "--clip", "0"], "Invalid value for '--track'"),
    (["--track", "0", "--clip", "0", "--track-type", "sound"], "Invalid value for '--track-type'"),
    (["--track", "0", "--clip", "0", "--effect-index", "-1"], "Invalid value for '--effect-index'"),
    (["--node-id", "x", "--effect-index", "two"], "Invalid value for '--effect-index'"),
])
def test_effect_get_needs_a_clip_and_nothing_is_sent(live, capsys, argv, text):
    with panel_on(live, handler=recipe_handler({MARK: script_reply([])})) as panel:
        assert dispatch(["--json", "live", "effect", "get", *argv]) == 2
    err = " ".join(capsys.readouterr().err.split())
    assert text in err, err            # click's "No such command" is also exit 2; the text proves the command ran
    assert panel.seen == []
    assert not (live / LOCK).exists()


GOOD_TARGET = {"track": 0, "clip": 0}


@pytest.mark.parametrize("kwargs", [
    {"target": "000f4243"}, {"target": None}, {"target": {}}, {"target": {"node_id": ""}}, {"target": {"node_id": 5}},
    {"target": {"node_id": "a", "track": 0}}, {"target": {"node_id": "a", "clip": 0}},
    {"target": {"node_id": "a", "track_type": "video"}}, {"target": {"track": 0}}, {"target": {"clip": 0}},
    {"target": {"track": -1, "clip": 0}}, {"target": {"track": True, "clip": 0}}, {"target": {"track": 0, "clip": 1.5}},
    {"target": {"track": 0, "clip": "0"}}, {"target": {"track": 0, "clip": 0, "track_type": "both"}},
    {"target": {"track": 0, "clip": 0, "colour": 1}},
    {"effect": ""}, {"effect": 5}, {"effect_index": -1}, {"effect_index": "1"}, {"effect_index": True},
    {"effect_index": 1.5}, {"sequence_id": ""}, {"sequence_id": 7},
])
def test_get_effects_refuses_bad_arguments_before_sending(live, kwargs):
    client = BridgeClient(live, 0.1)
    with panel_on(live, handler=recipe_handler({MARK: script_reply([])})) as panel:
        with pytest.raises(CliError) as err:
            effects.get_effects(client, **{"target": GOOD_TARGET, **kwargs})
    assert err.value.code == "bad_args" and err.value.message.endswith("Nothing was sent.")
    assert panel.seen == [] and not (live / LOCK).exists()


def test_get_effects_fills_in_the_track_type(live):
    client = BridgeClient(live, 0.1)
    with panel_on(live, handler=recipe_handler({MARK: script_reply([])})) as panel:
        effects.get_effects(client, {"track": 2, "clip": 1})
        effects.get_effects(client, {"track_type": None, "track": 2, "clip": 1}, effect="Motion", effect_index=1)
    assert script_args(panel.seen[1]) == {"sequence_id": None, "target": {"track_type": "video", "track": 2, "clip": 1},
                                          "effect": None, "effect_index": None}
    assert script_args(panel.seen[3]) == {"sequence_id": None, "target": {"track_type": "video", "track": 2, "clip": 1},
                                          "effect": "Motion", "effect_index": 1}


def _good(**over) -> dict:
    return {**script_reply([raw_effect(0, "Opacity", "AE.ADBE Opacity", [raw_prop(0, "Opacity", 100)])]), **over}


@pytest.mark.parametrize("reply", [
    ["not", "an", "object"], _good(sequence=None), _good(sequence="s1"), _good(clip=None), _good(clip=["x"]),
    _good(effects="none"), _good(effects=None), _good(effects=["Motion"]),
    _good(effects=[raw_effect(0, "A", "B", "none")]), _good(effects=[raw_effect(0, "A", "B", [5])]),
    _good(effects=[raw_effect(0, "A", "B", [raw_prop(0, "x", 1, "pair")])]),
    _good(effects=[raw_effect(0, "A", "B", [raw_prop(0, "x", 1, None)])]),
    # a value that does not fit its kind
    _good(effects=[raw_effect(0, "A", "B", [raw_prop(0, "x", None, "color")])]),
    _good(effects=[raw_effect(0, "A", "B", [raw_prop(0, "x", [1, 2, 3], "color")])]),
    _good(effects=[raw_effect(0, "A", "B", [raw_prop(0, "x", ["a"], "array")])]),
    _good(effects=[raw_effect(0, "A", "B", [raw_prop(0, "x", "1", "number")])]),
    _good(effects=[raw_effect(0, "A", "B", [raw_prop(0, "x", True, "number")])]),
    _good(effects=[raw_effect(0, "A", "B", [raw_prop(0, "x", 1, "boolean")])]),
    _good(effects=[raw_effect(0, "A", "B", [raw_prop(0, "x", 5, "string")])]),
    _good(effects=[raw_effect(0, "A", "B", [raw_prop(0, "x", 5, "unreadable")])]),
    # an index that is not a whole number of 0 or more, a name that is not a text, a keyframed flag that is not a bool
    *[_good(effects=[raw_effect(0, "A", "B", [raw_prop(bad, "x", 1)])]) for bad in (-1, "0", True, 1.5, None)],
    *[_good(effects=[raw_effect(bad, "A", "B", [])]) for bad in (-1, "3", True, 1.5, None)],
    _good(effects=[raw_effect(0, "A", "B", [raw_prop(0, 5, 1)])]),
    _good(effects=[raw_effect(0, 5, "B", [])]),
    _good(effects=[raw_effect(0, "A", 5, [])]),
    *[_good(effects=[raw_effect(0, "A", "B", [raw_prop(0, "x", 1, keyframed=bad)])]) for bad in ("yes", 1, 0)],
])
def test_a_script_that_answers_wrongly_is_a_bad_reply(live, capsys, reply):
    code, out, _ = effect_get(live, capsys, reply, *BY_TRACK)
    assert code == 1 and out["code"] == "bad_reply" and not (live / LOCK).exists()


def test_the_constants():
    assert effects.TRACK_TYPES == ("video", "audio")
    assert effects.KINDS == ("number", "string", "boolean", "color", "array", "unreadable")


# ---------- human output and help ----------
def test_effect_get_human_lines(live, capsys):
    lumetri = raw_effect(3, "Lumetri Color", "AE.ADBE Lumetri", [
        raw_prop(0, "Blob", "﻾", "string"),
        raw_prop(4, " ", "x" * 70, "string"),
        raw_prop(5, "Sixty", "y" * 60, "string"),
        raw_prop(6, "Sixty one", "z" * 61, "string"),
        raw_prop(13, "White Balance", [255, 192, 192, 192], "color"),
        raw_prop(14, "Temperature", -46.5, "number", True),
        raw_prop(15, "Saturation", 100),
        raw_prop(17, "", False, "boolean"),
        raw_prop(18, None, None, "unreadable", None),
        raw_prop(19, "Position", [0.5, 0.5], "array"),
        raw_prop(20, "Note", 'say "hi"\\\n', "string")])
    single = raw_effect(4, "ProcAmp", "AE.ADBE ProcAmp", [raw_prop(0, "Brightness", 7.5)])
    unread = raw_effect(5, "Mystery", "AE.Mystery", None)
    bare = raw_effect(6, "Mask", "AE.ADBE Mask", [])
    reply = script_reply([lumetri, single, unread, bare], track=2, clip=1, start_ticks=str(TPS * 3 // 2))
    with panel_on(live, handler=recipe_handler({MARK: reply})):
        assert dispatch(["live", "effect", "get", "--sequence", "s1", "--track", "2", "--clip", "1"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "V3[1] 000f4243 testclip.mp4, sequence s1, starts at 1.500 s",
        "[3] Lumetri Color (AE.ADBE Lumetri), 11 properties",
        '  0 Blob = "\\ufefe"',
        '  4 " " = "' + "x" * 60 + '..."',
        '  5 Sixty = "' + "y" * 60 + '"',
        '  6 Sixty one = "' + "z" * 60 + '..."',
        "  13 White Balance = argb(255, 192, 192, 192)",
        "  14 Temperature = -46.5 (keyframed)",
        "  15 Saturation = 100",
        '  17 "" = false',
        "  18 ? = (unreadable)",
        "  19 Position = [0.5, 0.5]",
        '  20 Note = "say \\"hi\\"\\\\\\u000a"',
        "[4] ProcAmp (AE.ADBE ProcAmp), 1 property",
        "  0 Brightness = 7.5",
        "[5] Mystery (AE.Mystery), properties could not be read",
        "[6] Mask (AE.ADBE Mask), 0 properties"]


def test_effect_get_human_header_for_audio_and_unreadable_clip_fields(live, capsys):
    reply = script_reply([], track_type="audio", track=0, clip=3, node_id=None, name=None, start_ticks=None)
    with panel_on(live, handler=recipe_handler({MARK: reply}, state=STATE_26)):
        assert dispatch(["live", "effect", "get", "--sequence", "s1", "--node-id", "x"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "A1[3] - ?, sequence s1, starts at ? s"
    assert lines[1] == "This clip has no effects."
    assert lines[2].startswith("Warning: ") and "26.0.0" in lines[2]


def test_effect_get_human_warning_for_the_active_sequence(live, capsys):
    with panel_on(live, handler=recipe_handler({MARK: script_reply(two_effects(), sequence_name="Main cut", used_active=True)})):
        assert dispatch(["live", "effect", "get", *BY_TRACK]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == "Warning: No --sequence was given, so this reads the active sequence: Main cut (s1)."


def test_the_help_says_it_only_reads_and_how_to_name_the_clip(capsys):
    assert dispatch(["live", "effect", "get", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "only reads" in text.lower()
    for word in ("--sequence", "--track", "--clip", "--track-type", "--node-id", "--effect", "--effect-index"):
        assert word in text
    assert "default is video" in text and "live clip list" in text
    assert dispatch(["live", "--help"]) == 0
    assert "effect" in capsys.readouterr().out
    assert dispatch(["live", "effect", "--help"]) == 0
    assert "get" in capsys.readouterr().out


# ---------- the script, in node with a stub app ----------
GET_PRELUDE = STUB_HELPERS + """
var writes = 0;             // every call that would change something in Premiere
var valueReads = [];        // the arguments of every getValue call
var colorReads = 0;         // the calls of getColorValue
function mprop(name, value, extra) {
  extra = extra || {};
  var p = {};
  if (extra.nameThrows) Object.defineProperty(p, "displayName", {get: function () { throw new Error("no name"); }});
  else p.displayName = name;
  p.getValue = function (t, s) {
    valueReads.push([t, s]);
    if (extra.valueThrows) throw new Error("no value");
    return value;
  };
  p.isTimeVarying = function () { if (extra.varying === "throws") throw new Error("no keys"); return extra.varying === true; };
  if (extra.color) p.getColorValue = function () { colorReads++; return extra.color; };
  else if (extra.colorApi !== "none") p.getColorValue = function () { colorReads++; throw new Error("not a colour"); };
  p.setValue = function () { writes++; };
  p.setColorValue = function () { writes++; };
  p.setTimeVarying = function () { writes++; };
  p.addKey = function () { writes++; };
  p.removeKey = function () { writes++; };
  return p;
}
function mcomp(name, match, props) { return {displayName: name, matchName: match, properties: coll("numItems", props)}; }
function mclip(id, name, start, comps) {
  return {nodeId: id, name: name, start: {ticks: String(start)}, end: {ticks: String(start + 100)}, components: coll("numItems", comps)};
}
var PACKED = 18446744073709551615;
var kinds = mcomp("Kinds", "AE.Kinds", [
  mprop("Number", 7.7),                                                   // 0
  mprop("Zero", 0),                                                       // 1
  mprop("String", "abc"),                                                 // 2
  mprop("Empty string", ""),                                              // 3
  mprop("Boolean", true),                                                 // 4
  mprop("False", false),                                                  // 5
  mprop("Color", PACKED, {color: [255, 192, 192, 192]}),                  // 6
  mprop("Pair", [0.5, 0.25]),                                             // 7
  mprop("Mixed", [0.5, "x"]),                                             // 8
  mprop("NaN", NaN),                                                      // 9
  mprop("Infinity", Infinity),                                            // 10
  mprop("Throws", 1, {valueThrows: true}),                                // 11
  mprop("Undefined", undefined),                                          // 12
  mprop("Null", null),                                                    // 13
  mprop("Object", {a: 1}),                                                // 14
  mprop("Empty array", []),                                               // 15
  mprop("Short color", 5, {color: [255, 0, 0]}),                          // 16
  mprop("NaN color", 7, {color: [255, NaN, 0, 0]}),                       // 17
  mprop("No color api", 3, {colorApi: "none"}),                           // 18
  mprop("Big number", PACKED),                                            // 19
  mprop("Varying throws", 1, {varying: "throws"}),                        // 20
  mprop("Varying", 2, {varying: true}),                                   // 21
  mprop(" ", true),                                                       // 22
  mprop("", false),                                                       // 23
  mprop("unused", 1, {nameThrows: true}),                                 // 24
  mprop("Array NaN", [1, NaN]),                                           // 25
  mprop("Triple", [1, 2, 3]),                                             // 26
  mprop("Below limit", 4294967295),                                       // 27
  mprop("At limit", 4294967296),                                          // 28
  mprop("Negative big", -PACKED)                                          // 29
]);
var seqA = {sequenceID: "S-A", name: "Cut A",
  videoTracks: coll("numTracks", [
    {clips: coll("numItems", [
      mclip("v0c0", "Intro", 0, [
        mcomp("Opacity", "AE.ADBE Opacity", [mprop("Opacity", 100)]),
        mcomp("Motion", "AE.ADBE Motion", [mprop("Position", [0.5, 0.5]), mprop("Scale", 100), mprop("Uniform Scale", true),
                                           mprop("Rotation", 0, {varying: true})]),
        mcomp("Lumetri Color", "AE.ADBE Lumetri", [mprop("Blob", "\\ufefe"), mprop(" ", false), mprop("", 0),
                                                   mprop("White Balance", PACKED, {color: [255, 192, 192, 192]}),
                                                   mprop("Temperature", -46.5)]),
        mcomp("ProcAmp", "AE.ADBE ProcAmp", [mprop("Brightness", 7.69999980926514), mprop("Contrast", 100)]),
        mcomp("Lumetri Color", "AE.ADBE Lumetri", [mprop("Temperature", 5)])]),
      mclip("v0c1", "Middle", 254016000000, [mcomp("Opacity", "AE.ADBE Opacity", [mprop("Opacity", 100)])])])},
    {clips: coll("numItems", [
      mclip("v1c0", "Title", 0, [mcomp("Opacity", "AE.ADBE Opacity", [mprop("Opacity", 100)]), kinds])])}]),
  audioTracks: coll("numTracks", [
    {clips: coll("numItems", [
      mclip("a0c0", "Music", 0, [mcomp("Volume", "AE.ADBE Volume", [mprop("Level", -3)]),
                                 mcomp("Panner", "AE.ADBE Panner", [mprop("Balance", 0)])])])}])};
var seqB = {sequenceID: "S-B", name: "Cut B",
  videoTracks: coll("numTracks", [{clips: coll("numItems", [mclip("b0c0", "Other", 0, [mcomp("Opacity", "AE.ADBE Opacity", [mprop("Opacity", 50)])])])}]),
  audioTracks: coll("numTracks", [])};
var app = {project: {path: "/p/X Claude.prproj", sequences: coll("numSequences", [seqA, seqB]), activeSequence: seqA}};
"""
INTRO = {"track_type": "video", "track": 0, "clip": 0}
TITLE = {"track_type": "video", "track": 1, "clip": 0}


def run_get(target=INTRO, *, sequence_id="S-A", effect=None, effect_index=None, prelude="") -> tuple[dict, dict]:
    """Run effect_get.jsx in node against the stub app. Returns (the parsed reply, what the stub saw)."""
    args = {"sequence_id": sequence_id, "target": target, "effect": effect, "effect_index": effect_index}
    script = jsx.build(jsx.load("effect_get"), args, include=("find", "values"))
    code = (GET_PRELUDE + prelude + "\nprocess.stdout.write(JSON.stringify({reply: String(" + script
            + "), writes: writes, reads: valueReads, colors: colorReads}));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout
    outer = json.loads(out)
    return json.loads(outer["reply"]), outer


def refusal(**kw) -> str:
    reply, _ = run_get(**kw)
    assert reply["success"] is False, reply
    return reply["error"]


def effect_names(reply: dict) -> list[tuple]:
    return [(e["effect_index"], e["name"]) for e in reply["data"]["effects"]]


@needs_node
def test_effect_get_script_is_es3_and_lists_the_effects_of_a_clip():
    text = jsx.load("effect_get")
    assert text.split("\n")[0] == "// recipe: effect_get"
    for pattern in BANNED + [r"\.indexOf\(", r"\.reduce\(", r"\.some\(", r"\.every\(", r"\bArray\.", r"\.isArray"]:
        assert not re.search(pattern, text), pattern
    reply, seen = run_get()
    data = reply["data"]
    assert reply["success"] is True and data["used_active"] is False
    assert data["sequence"] == {"id": "S-A", "name": "Cut A"}
    assert data["clip"] == {"node_id": "v0c0", "name": "Intro", "track_type": "video", "track": 0, "clip": 0, "start_ticks": "0"}
    assert [(e["effect_index"], e["name"], e["match_name"]) for e in data["effects"]] == [
        (0, "Opacity", "AE.ADBE Opacity"), (1, "Motion", "AE.ADBE Motion"), (2, "Lumetri Color", "AE.ADBE Lumetri"),
        (3, "ProcAmp", "AE.ADBE ProcAmp"), (4, "Lumetri Color", "AE.ADBE Lumetri")]
    assert [len(e["properties"]) for e in data["effects"]] == [1, 4, 5, 2, 1]
    assert data["effects"][3]["properties"] == [
        {"index": 0, "name": "Brightness", "value": 7.69999980926514, "kind": "number", "keyframed": False},
        {"index": 1, "name": "Contrast", "value": 100, "kind": "number", "keyframed": False}]
    assert data["effects"][1]["properties"][3] == {"index": 3, "name": "Rotation", "value": 0, "kind": "number", "keyframed": True}
    assert data["effects"][1]["properties"][0] == {"index": 0, "name": "Position", "value": [0.5, 0.5], "kind": "array",
                                                   "keyframed": False}


@needs_node
def test_effect_get_script_reads_kinds_in_node():
    reply, seen = run_get(TITLE, effect="Kinds")
    assert reply["success"] is True
    props = reply["data"]["effects"][0]["properties"]
    assert [p["index"] for p in props] == list(range(30))
    got = [(p["name"], p["kind"], p["value"], p["keyframed"]) for p in props]
    assert got == [
        ("Number", "number", 7.7, False), ("Zero", "number", 0, False), ("String", "string", "abc", False),
        ("Empty string", "string", "", False), ("Boolean", "boolean", True, False), ("False", "boolean", False, False),
        ("Color", "color", [255, 192, 192, 192], False),                  # the colour read, not the packed number
        ("Pair", "array", [0.5, 0.25], False),                           # an array of finite numbers
        ("Mixed", "unreadable", None, False),                            # an array with a text in it
        ("NaN", "unreadable", None, False), ("Infinity", "unreadable", None, False),      # not a finite number
        ("Throws", "unreadable", None, False),                           # getValue throws
        ("Undefined", "unreadable", None, False), ("Null", "unreadable", None, False),
        ("Object", "unreadable", None, False),
        ("Empty array", "array", [], False),
        ("Short color", "number", 5, False),                             # getColorValue gave 3 channels: not a colour
        ("NaN color", "number", 7, False),                               # a channel that is not finite: not a colour
        ("No color api", "number", 3, False),                            # no getColorValue at all
        ("Big number", "unreadable", None, False),                       # 2^32 or more and no colour read: lossy, not a number
        ("Varying throws", "number", 1, None),                           # isTimeVarying throws: null, the value stays
        ("Varying", "number", 2, True),
        (" ", "boolean", True, False), ("", "boolean", False, False),    # blank names are not trimmed
        (None, "number", 1, False),                                      # a name that throws is null
        ("Array NaN", "unreadable", None, False), ("Triple", "array", [1, 2, 3], False),
        ("Below limit", "number", 4294967295, False),                    # 2^32 minus 1 is still exact
        ("At limit", "unreadable", None, False),                         # the server's own bound: 4294967296 or more
        ("Negative big", "number", -18446744073709552000, False)]        # only the positive packed form is a colour
    # Read as the server reads: the colour first, getValue(0, 0) after it, and never getValue for a colour.
    assert seen["reads"] and all(read == [0, 0] for read in seen["reads"])
    assert len(seen["reads"]) == 30 - 1 and seen["colors"] == 30 - 1      # every property but the one with no colour API


@needs_node
def test_effect_get_script_changes_nothing():
    reply, seen = run_get(TITLE)
    assert reply["success"] is True and len(reply["data"]["effects"]) == 2
    reply, seen = run_get(INTRO)
    assert reply["success"] is True and seen["writes"] == 0


@needs_node
def test_effect_get_script_filters_by_name_match_name_and_index():
    reply, _ = run_get(effect="Lumetri Color")
    assert effect_names(reply) == [(2, "Lumetri Color"), (4, "Lumetri Color")]        # a read lists every one
    assert [len(e["properties"]) for e in reply["data"]["effects"]] == [5, 1]
    reply, _ = run_get(effect="AE.ADBE ProcAmp")
    assert effect_names(reply) == [(3, "ProcAmp")]
    reply, _ = run_get(effect_index=1)
    assert effect_names(reply) == [(1, "Motion")]
    reply, _ = run_get(effect_index=4)
    assert effect_names(reply) == [(4, "Lumetri Color")] and reply["data"]["effects"][0]["properties"][0]["value"] == 5
    reply, _ = run_get(effect="Lumetri Color", effect_index=4)
    assert effect_names(reply) == [(4, "Lumetri Color")]
    reply, _ = run_get(effect="AE.ADBE Lumetri", effect_index=2)
    assert effect_names(reply) == [(2, "Lumetri Color")]


@needs_node
def test_effect_get_script_asks_only_about_the_effects_it_lists():
    _, seen = run_get(effect_index=1)                      # Motion: four properties
    assert len(seen["reads"]) == 4
    _, seen = run_get(effect="ProcAmp")
    assert len(seen["reads"]) == 2
    _, seen = run_get()                                    # every effect: 1 + 4 + 5 + 2 + 1, less the colour
    assert len(seen["reads"]) == 12


@needs_node
def test_effect_get_script_refusals_of_the_effect_filter():
    assert refusal(effect="Lumetri Color", effect_index=3) == "Error: precondition: effect 3 of this clip is ProcAmp, not Lumetri Color"
    assert refusal(effect="Sepia") == "Error: precondition: no effect named Sepia on this clip"
    assert refusal(effect="lumetri color") == "Error: precondition: no effect named lumetri color on this clip"
    assert refusal(effect="Motion", effect_index=9) == "Error: precondition: this clip has no effect at index 9"
    assert refusal(effect_index=9) == "Error: precondition: this clip has no effect at index 9"
    assert refusal(effect_index=5) == "Error: precondition: this clip has no effect at index 5"
    for bad, shown in ((1.5, "1.5"), (-1, "-1"), ("2", "2"), (True, "true")):
        assert refusal(effect_index=bad) == f"Error: precondition: effect_index must be a whole number of 0 or more, not {shown}"
    assert refusal(effect=5) == "Error: precondition: effect must be a text, not 5"


@needs_node
def test_effect_get_script_finds_the_clip_by_track_by_node_id_and_in_audio():
    reply, _ = run_get({"track_type": "video", "track": 0, "clip": 1})
    assert reply["data"]["clip"] == {"node_id": "v0c1", "name": "Middle", "track_type": "video", "track": 0, "clip": 1,
                                     "start_ticks": "254016000000"}
    assert effect_names(reply) == [(0, "Opacity")]
    reply, _ = run_get({"track_type": "audio", "track": 0, "clip": 0})
    assert reply["data"]["clip"]["node_id"] == "a0c0" and reply["data"]["clip"]["track_type"] == "audio"
    assert effect_names(reply) == [(0, "Volume"), (1, "Panner")]
    reply, _ = run_get({"node_id": "v1c0"})                # the track and clip come from where the node was found
    assert reply["data"]["clip"] == {"node_id": "v1c0", "name": "Title", "track_type": "video", "track": 1, "clip": 0,
                                     "start_ticks": "0"}
    reply, _ = run_get({"node_id": "a0c0"})
    assert (reply["data"]["clip"]["track_type"], reply["data"]["clip"]["track"], reply["data"]["clip"]["clip"]) == ("audio", 0, 0)
    assert refusal(target={"node_id": "zzz"}) == "Error: precondition: no clip has the node ID zzz in sequence Cut A"
    assert refusal(target={"node_id": "b0c0"}) == "Error: precondition: no clip has the node ID b0c0 in sequence Cut A"
    assert refusal(target={"track": 0, "clip": 9}) == "Error: precondition: video track 0 has no clip 9"
    assert refusal(target={"track": 7, "clip": 0}) == "Error: precondition: the sequence has no video track 7"


@needs_node
def test_effect_get_script_takes_the_sequence_by_id_or_the_active_one():
    reply, _ = run_get({"node_id": "b0c0"}, sequence_id="S-B")
    assert reply["data"]["sequence"] == {"id": "S-B", "name": "Cut B"} and reply["data"]["used_active"] is False
    assert reply["data"]["clip"]["node_id"] == "b0c0"
    reply, _ = run_get(sequence_id=None)
    assert reply["data"]["used_active"] is True and reply["data"]["sequence"] == {"id": "S-A", "name": "Cut A"}
    assert reply["data"]["clip"]["node_id"] == "v0c0"
    assert refusal(sequence_id="nope") == "Error: precondition: no sequence has the ID nope"
    assert refusal(sequence_id=None, prelude="app.project.activeSequence = null;") == (
        "Error: precondition: no sequence is active; pass --sequence")
    assert refusal(sequence_id=None, prelude="app.project = null;") == "Error: precondition: no sequence is active; pass --sequence"


@needs_node
def test_effect_get_script_one_bad_read_does_not_fail_the_clip():
    base, _ = run_get()
    lumetri = "seqA.videoTracks[0].clips[0].components[2]"
    # the property list of one effect cannot be read
    prelude = f"Object.defineProperty({lumetri}, 'properties', {{get: function () {{ throw new Error('no list'); }}}});"
    reply, _ = run_get(prelude=prelude)
    assert reply["success"] is True
    found, expected = reply["data"]["effects"], base["data"]["effects"]
    assert found[2] == {**expected[2], "properties": None}
    assert [found[i] for i in (0, 1, 3, 4)] == [expected[i] for i in (0, 1, 3, 4)]
    # one property object cannot be fetched
    prelude = f"Object.defineProperty({lumetri}.properties, '1', {{get: function () {{ throw new Error('no prop'); }}}});"
    reply, _ = run_get(prelude=prelude)
    found = reply["data"]["effects"][2]["properties"]
    assert found[1] == {"index": 1, "name": None, "value": None, "kind": "unreadable", "keyframed": None}
    assert [found[i] for i in (0, 2, 3, 4)] == [base["data"]["effects"][2]["properties"][i] for i in (0, 2, 3, 4)]
    # the name of an effect throws, and the clip's own fields
    prelude = (f"Object.defineProperty({lumetri}, 'displayName', {{get: function () {{ throw new Error('no name'); }}}});"
               "Object.defineProperty(seqA.videoTracks[0].clips[0], 'name', {get: function () { throw new Error('no clip name'); }});"
               "Object.defineProperty(seqA.videoTracks[0].clips[0], 'start', {get: function () { throw new Error('no start'); }});")
    reply, _ = run_get(prelude=prelude)
    assert reply["success"] is True
    assert reply["data"]["clip"]["name"] is None and reply["data"]["clip"]["start_ticks"] is None
    assert reply["data"]["clip"]["node_id"] == "v0c0"
    assert reply["data"]["effects"][2]["name"] is None and reply["data"]["effects"][2]["match_name"] == "AE.ADBE Lumetri"
    assert len(reply["data"]["effects"][2]["properties"]) == 5


@needs_node
def test_effect_get_script_sends_ticks_as_strings_or_null():
    prelude = "seqA.videoTracks[0].clips[0].start = {ticks: 254016000000};"
    reply, _ = run_get(prelude=prelude)
    assert reply["data"]["clip"]["start_ticks"] == "254016000000"
    reply, _ = run_get(prelude="seqA.videoTracks[0].clips[0].start = {ticks: '12.5'};")
    assert reply["data"]["clip"]["start_ticks"] is None
    reply, _ = run_get(prelude="delete seqA.videoTracks[0].clips[0].start;")
    assert reply["data"]["clip"]["start_ticks"] is None


@needs_node
def test_effect_get_runs_end_to_end_through_the_command(live, capsys):
    """The command and the real script together: the panel runs effect_get.jsx in node."""
    def in_node(script: str):
        code = GET_PRELUDE + "\nprocess.stdout.write(String(" + script + "));"
        reply = json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout)
        assert reply["success"] is True, reply
        return reply["data"]
    with panel_on(live, handler=recipe_handler({MARK: in_node})) as panel:
        code, out = run_json(capsys, ["live", "effect", "get", "--sequence", "S-A", "--node-id", "v1c0", "--effect", "Kinds"])
        assert dispatch(["live", "effect", "get", "--track", "0", "--clip", "0", "--effect-index", "1"]) == 0
        human = capsys.readouterr().out.splitlines()
        assert dispatch(["live", "effect", "get", "--sequence", "S-A", "--track", "0", "--clip", "0", "--effect-index", "9"]) == 1
    assert code == 0 and out["clip"] == {"sequence_id": "S-A", "track_type": "video", "track": 1, "clip": 0, "node_id": "v1c0",
                                         "name": "Title", "start_ticks": "0", "start_seconds": 0.0}
    assert out["warnings"] == [] and [e["name"] for e in out["effects"]] == ["Kinds"]
    props = {p["name"]: p for p in out["effects"][0]["properties"] if p["name"]}
    assert (props["Pair"]["kind"], props["Pair"]["value"]) == ("array", [0.5, 0.25])           # a finite number array
    assert (props["Mixed"]["kind"], props["Mixed"]["value"]) == ("unreadable", None)           # an array with a non-number
    assert (props["NaN"]["kind"], props["NaN"]["value"]) == ("unreadable", None)               # a number that is NaN
    assert (props["Infinity"]["kind"], props["Infinity"]["value"]) == ("unreadable", None)
    assert (props["Color"]["kind"], props["Color"]["value"]) == ("color", [255, 192, 192, 192])
    assert props["Varying"]["keyframed"] is True and props["Varying throws"]["keyframed"] is None
    assert human[0] == "V1[0] v0c0 Intro, sequence S-A, starts at 0.000 s"
    assert human[1] == "[1] Motion (AE.ADBE Motion), 4 properties"
    assert human[2:6] == ["  0 Position = [0.5, 0.5]", "  1 Scale = 100", "  2 Uniform Scale = true",
                          "  3 Rotation = 0 (keyframed)"]
    assert "Warning: No --sequence was given, so this reads the active sequence: Cut A (S-A)." in human[-1]
    assert script_args(panel.seen[-1])["effect_index"] == 9


# ---------- fix round 1: a lone surrogate must not reach the output ----------
HIGH, LOW, CLAPPER = "\ud800", "\udc00", "\U0001F3AC"       # a lone high half, a lone low half, a real U+1F3AC


def on_the_wire(reply) -> str:
    """The text the fake panel writes for this reply: Python's json escapes every character above 0x7E, as the real panel does."""
    return recipe_handler({MARK: reply})("// " + MARK)


def test_a_string_value_with_a_lone_surrogate_is_unreadable(live, capsys):
    props = [raw_prop(0, "Blob", "ab" + HIGH + "cd", "string"), raw_prop(1, "Other", "x" + LOW, "string"),
             raw_prop(2, "Fine", "plain", "string"), raw_prop(3, "Number", 5),
             raw_prop(4, "Half a pair", CLAPPER + HIGH, "string"), raw_prop(5, "Kept", CLAPPER, "string")]
    reply = script_reply([raw_effect(2, "Lumetri Color", "AE.ADBE Lumetri", props)])
    wire = on_the_wire(reply)
    assert "\\ud800" in wire and "\\udc00" in wire and HIGH not in wire and LOW not in wire      # escapes, as the real panel sends
    code, out, _ = effect_get(live, capsys, reply, *BY_TRACK)                                       # --json: must parse, exit 0
    assert code == 0 and out["ok"] is True
    assert [(p["name"], p["kind"], p["value"]) for p in out["effects"][0]["properties"]] == [
        ("Blob", "unreadable", None), ("Other", "unreadable", None), ("Fine", "string", "plain"), ("Number", "number", 5),
        ("Half a pair", "unreadable", None), ("Kept", "string", CLAPPER)]
    assert all(p["keyframed"] is False for p in out["effects"][0]["properties"])
    with panel_on(live, handler=recipe_handler({MARK: reply})):
        assert dispatch(["live", "effect", "get", "--sequence", "s1", *BY_TRACK]) == 0
    assert capsys.readouterr().out.splitlines()[2:] == [
        "  0 Blob = (unreadable)", "  1 Other = (unreadable)", '  2 Fine = "plain"', "  3 Number = 5",
        "  4 Half a pair = (unreadable)", f'  5 Kept = "{CLAPPER}"']


def test_a_name_with_a_lone_surrogate_gets_a_replacement_character(live, capsys):
    fffd = "\ufffd"
    props = [raw_prop(0, "p" + HIGH + HIGH + "q", 1), raw_prop(1, LOW + HIGH, 2), raw_prop(2, "ok", 3),
             raw_prop(3, CLAPPER + HIGH, 4), raw_prop(4, LOW + CLAPPER, 5)]
    reply = script_reply([raw_effect(2, "Lum" + HIGH, "AE" + LOW + ".Lum", props)], sequence_name="Cut" + HIGH,
                         used_active=True, name="bad" + LOW + "name", node_id="n" + HIGH)
    assert "\\ud800" in on_the_wire(reply) and HIGH not in on_the_wire(reply)
    code, out, _ = effect_get(live, capsys, reply, *BY_TRACK)
    assert code == 0 and out["ok"] is True
    assert out["clip"]["name"] == "bad" + fffd + "name" and out["clip"]["node_id"] == "n" + fffd
    effect = out["effects"][0]
    assert (effect["name"], effect["match_name"]) == ("Lum" + fffd, "AE" + fffd + ".Lum")
    assert [p["name"] for p in effect["properties"]] == ["p" + fffd * 2 + "q", fffd * 2, "ok", CLAPPER + fffd, fffd + CLAPPER]
    assert out["warnings"] == [f"No --sequence was given, so this reads the active sequence: Cut{fffd} (s1)."]
    with panel_on(live, handler=recipe_handler({MARK: reply})):
        assert dispatch(["live", "effect", "get", *BY_TRACK]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"V1[0] n{fffd} bad{fffd}name, sequence s1, starts at 0.000 s"
    assert lines[1] == f"[2] Lum{fffd} (AE{fffd}.Lum), 5 properties"
    assert lines[2:7] == [f"  0 p{fffd}{fffd}q = 1", f"  1 {fffd}{fffd} = 2", "  2 ok = 3", f"  3 {CLAPPER}{fffd} = 4",
                          f"  4 {fffd}{CLAPPER} = 5"]


def test_a_real_character_outside_the_bmp_passes_unchanged(live, capsys):
    props = [raw_prop(0, CLAPPER, "take " + CLAPPER + " one", "string"), raw_prop(1, "Clap" + CLAPPER + "per", 1)]
    reply = script_reply([raw_effect(0, "Title " + CLAPPER, "AE." + CLAPPER, props)], name="Scene " + CLAPPER)
    assert "\\ud83c\\udfac" in on_the_wire(reply)          # on the wire it is a pair of escapes; json.loads joins it
    code, out, _ = effect_get(live, capsys, reply, *BY_TRACK)
    assert code == 0 and out["ok"] is True
    assert out["clip"]["name"] == "Scene " + CLAPPER
    effect = out["effects"][0]
    assert (effect["name"], effect["match_name"]) == ("Title " + CLAPPER, "AE." + CLAPPER)
    assert [(p["name"], p["kind"], p["value"]) for p in effect["properties"]] == [
        (CLAPPER, "string", "take " + CLAPPER + " one"), ("Clap" + CLAPPER + "per", "number", 1)]
    with panel_on(live, handler=recipe_handler({MARK: reply})):
        assert dispatch(["live", "effect", "get", "--sequence", "s1", *BY_TRACK]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("V1[0] 000f4243 Scene " + CLAPPER + ", sequence s1")
    assert lines[1] == "[0] Title " + CLAPPER + " (AE." + CLAPPER + "), 2 properties"
    assert lines[2:4] == [f'  0 {CLAPPER} = "take {CLAPPER} one"', f"  1 Clap{CLAPPER}per = 1"]


# =====================================================================================================
# live effect set
# =====================================================================================================
RESOLVE = "recipe: effect_resolve"
WRITE = "recipe: effect_write"
RECOVERY = ".host-recovery.json"
SET = ["live", "effect", "set"]
BRIGHTNESS = ["--sequence", "s1", "--track", "0", "--clip", "0", "--effect", "ProcAmp", "--index", "0",
              "--name", "Brightness"]
EXPECT_S1 = {"project_path": "/p/X Claude.prproj", "sequence_id": "s1", "sequence_name": None}
NOT_PROOF = "Read-back is not proof for this change."


def effect_set(live, capsys, model, *argv, state=None, extra=None):  # noqa: F811
    """Run `live effect set` against a panel that plays `model`; returns (code, payload, panel)."""
    replies = model.replies()
    replies.update(extra or {})
    with panel_on(live, handler=recipe_handler(replies, state=state)) as panel:
        code, out = run_json(capsys, [*SET, *argv])
    return code, out, panel


def scripts(panel, mark) -> list[dict]:
    """The ARGS of every script with this marker that the panel got."""
    return [script_args(text) for text in panel.seen if mark in text]


def plan_file(tmp_path, plan, name="plan.json"):
    path = tmp_path / name
    path.write_text(plan if isinstance(plan, str) else json.dumps(plan), encoding="utf-8")
    return str(path)


def write_of(track, clip, effect, index, name, value, **more) -> dict:
    return {"track": track, "clip": clip, "effect": effect, "index": index, "name": name, "value": value, **more}


def nothing_written(live, model, panel) -> None:
    assert model.writes == [] and not scripts(panel, WRITE)
    assert not (live / RECOVERY).exists() and not (live / LOCK).exists()


# ---------- the brief's tests ----------
def test_one_write_is_stored(live, capsys, sends):
    model = EffectModel()
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "3.0")
    assert code == 0 and out.pop("ok") is True
    assert list(out) == ["sequence", "resolved", "results", "ran", "total", "stored", "rendered", "warnings"]
    assert out["sequence"] == {"id": "s1", "name": "Main"}
    assert out["resolved"] == [{"entry": 0, "clip": {"node_id": "000f4243", "name": "testclip.mp4", "track": 0, "clip": 0,
                                                     "start_seconds": 0.0},
                                "effect": "ProcAmp", "effect_index": 4, "index": 0, "name": "Brightness", "value_now": 7.7}]
    assert out["results"] == [{"entry": 0, "requested": 3.0, "before": 7.7, "value": 3.0, "stored": True, "attempts": 1,
                               "zero_first": False, "landed": None, "restored": None, "now": 3.0, "error": None}]
    assert (out["ran"], out["total"], out["stored"], out["rendered"], out["warnings"]) == (1, 1, True, None, [])
    assert model.prop(0, 0, 4, 0)["value"] == 3.0 and model.writes == [("000f4243", 4, 0, 3.0)]
    assert len(panel.seen) == 3 and sends == [False, False, True]          # state, resolve, write
    assert scripts(panel, RESOLVE) == [{"sequence_id": "s1", "entries": [
        {"target": {"track_type": "video", "track": 0, "clip": 0}, "effect": "ProcAmp", "effect_index": None,
         "index": 0, "name": "Brightness"}], "expect": EXPECT_S1}]
    assert scripts(panel, WRITE) == [{"sequence_id": "s1", "target": {"track_type": "video", "track": 0, "clip": 0},
                                      "node_id": "000f4243", "effect": "ProcAmp", "effect_index": None,
                                      "found_effect_index": 4, "index": 0, "name": "Brightness", "value": 3.0,
                                      "tolerance": 0.001, "expect": EXPECT_S1}]
    for text in panel.seen[1:]:
        assert FIND_MARK in text
    assert "// values.jsx" in panel.seen[1] and "// values.jsx" not in panel.seen[2]
    assert not (live / RECOVERY).exists() and not (live / LOCK).exists()


def test_readback_uses_the_tolerance(live, capsys):
    model = EffectModel()
    model.prop(0, 0, 4, 0)["value"] = 1.0
    model.readback[7.7] = 7.69999980926514
    code, out, _ = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "7.7")
    assert code == 0 and out["stored"] is True
    assert out["results"][0]["value"] == 7.69999980926514 and out["results"][0]["stored"] is True


def test_same_value_is_the_one_rule():
    same = effects.same_value
    assert same(3.0, 3.0009) and same(3, 3.0) and same(-2.6, -2.6004) and same(0, -0.0)
    assert not same(3.0, 3.0011) and not same(3.0, 2.5)
    assert same("a", "a") and not same("a", "a ") and not same("", " ")
    assert same(True, True) and same(False, False) and not same(True, False)
    assert not same(True, 1) and not same(1, True) and not same(0, False) and not same("1", 1)
    assert not same(None, None) and not same(None, 0) and not same([1], [1])
    assert not same(float("nan"), float("nan")) and not same(float("inf"), float("inf"))
    assert effects.VALUE_TOLERANCE == 0.001
    assert effects.REFUSED_LUMETRI_INDEXES == (7, 32, 34) and effects.LUMETRI_MATCH == "AE.ADBE Lumetri"


def test_zero_is_written_first_when_crossing(live, capsys):
    model = EffectModel()
    model.prop(0, 0, 4, 0)["value"] = 5.0
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "-2.6")
    assert code == 0 and out["stored"] is True
    assert [args["value"] for args in scripts(panel, WRITE)] == [-2.6]      # the script does the zero step itself
    assert model.writes == [("000f4243", 4, 0, 0), ("000f4243", 4, 0, -2.6)]
    assert out["results"][0] == {"entry": 0, "requested": -2.6, "before": 5.0, "value": -2.6, "stored": True,
                                 "attempts": 1, "zero_first": True, "landed": None, "restored": None, "now": -2.6,
                                 "error": None}


def test_negative_fault_retries_then_lands_near(live, capsys):
    model = EffectModel()
    model.fail(-2.6, times=2)
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "-2.6")
    assert code == 1 and out["ok"] is False and out["code"] == "not_stored"
    assert [args["value"] for args in scripts(panel, WRITE)] == [-2.6, -2.6, -2.496]
    result = out["results"][0]
    assert (result["attempts"], result["landed"], result["stored"], result["value"]) == (3, -2.496, False, -2.496)
    assert result["zero_first"] is True and result["before"] == 7.7 and result["requested"] == -2.6
    assert "Illegal Parameter type" in result["error"] and "-2.496" in result["error"]
    assert (result["restored"], result["now"]) == (None, -2.496)                 # a near value that landed is kept
    assert len(model.writes) == 4 and model.writes[-1][3] == -2.496             # 0, -2.6, -2.6, -2.496: no put-back
    assert (out["ran"], out["total"], out["stored"]) == (1, 1, False)
    assert "entry 0" in out["error"] and "not stored" in out["error"]
    assert not (live / RECOVERY).exists() and not (live / LOCK).exists()


def test_negative_fault_that_never_lands(live, capsys):
    model = EffectModel()
    model.fail(-2.6, times=2)
    model.fail(-2.496, times=1, error="Illegal Parameter type, again")
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "-2.6")
    assert code == 1 and out["code"] == "not_stored"
    result = out["results"][0]
    assert (result["attempts"], result["landed"], result["stored"]) == (3, None, False)
    assert "Illegal Parameter type" in result["error"]
    # the zero step left 0 there, so the old 7.7 is put back with a fourth write
    assert [args["value"] for args in scripts(panel, WRITE)] == [-2.6, -2.6, -2.496, 7.7]
    assert (result["value"], result["restored"], result["now"]) == (0, True, 7.7)
    assert model.prop(0, 0, 4, 0)["value"] == 7.7


def test_positive_failure_gets_one_attempt(live, capsys):
    model = EffectModel()
    model.readback[3.0] = 2.5
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "3.0")
    assert code == 1 and out["code"] == "not_stored"
    result = out["results"][0]
    assert (result["attempts"], result["stored"], result["value"], result["landed"]) == (1, False, 2.5, None)
    assert result["error"] == "wanted 3.0, read back 2.5"
    assert [args["value"] for args in scripts(panel, WRITE)] == [3.0, 7.7]          # one attempt, then the put-back
    assert (result["restored"], result["now"]) == (True, 7.7)


def test_a_negative_that_reads_back_wrong_also_gets_the_near_value(live, capsys):
    model = EffectModel()
    model.readback[-2.6] = -2.0
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "-2.6")
    assert [args["value"] for args in scripts(panel, WRITE)] == [-2.6, -2.6, -2.496]
    assert out["results"][0]["landed"] == -2.496 and out["results"][0]["stored"] is False


def test_batch_stops_at_the_first_failure(live, capsys, tmp_path):
    model = EffectModel()
    model.readback[4.0] = 1.0
    plan = {"sequence_id": "s1", "writes": [write_of(0, 0, "ProcAmp", 0, "Brightness", 3.0),
                                            write_of(0, 1, "ProcAmp", 0, "Brightness", 4.0),
                                            write_of(0, 0, "ProcAmp", 1, "Contrast", 50.0)]}
    code, out, panel = effect_set(live, capsys, model, "--from", plan_file(tmp_path, plan))
    assert code == 1 and out["code"] == "not_stored"
    assert (out["ran"], out["total"], out["stored"]) == (2, 3, False)
    assert [r["stored"] for r in out["results"]] == [True, False]
    assert [args["value"] for args in scripts(panel, WRITE)] == [3.0, 4.0, 5.0]     # 5.0 is the put-back; the third never sent
    assert model.prop(0, 0, 4, 1)["value"] == 100.0
    assert len(out["resolved"]) == 3 and len(scripts(panel, RESOLVE)) == 1          # one read for every entry
    assert "entry 1" in out["error"]


def test_two_lumetri_need_effect_index(live, capsys):
    model = EffectModel()
    clip = model.clips[(0, 0)]
    clip["effects"].append({"name": "Lumetri Color", "match_name": "AE.ADBE Lumetri", "properties": [
        {"name": "Temperature", "value": 3.0, "kind": "number", "keyframed": False}]})
    lumetri = ["--sequence", "s1", "--track", "0", "--clip", "0", "--effect", "Lumetri Color", "--index", "14",
               "--name", "Temperature", "--value", "-20"]
    code, out, panel = effect_set(live, capsys, model, *lumetri)
    assert code == 1 and out["code"] == "precondition"
    assert out["error"] == "entry 0: ambiguous_effect: 2 effects are named Lumetri Color; pass effect_index"
    assert (out["ran"], out["results"]) == (0, [])
    nothing_written(live, model, panel)
    code, out, panel = effect_set(live, capsys, model, *lumetri, "--effect-index", "2")
    assert code == 0 and out["stored"] is True and out["resolved"][0]["effect_index"] == 2
    assert model.writes == [("000f4243", 2, 14, 0), ("000f4243", 2, 14, -20)]        # 10 -> -20 crosses zero
    assert scripts(panel, WRITE)[0]["effect_index"] == 2 and scripts(panel, WRITE)[0]["found_effect_index"] == 2


def test_missing_clip_index_fails_before_any_write(live, capsys, tmp_path):
    model = EffectModel()
    plan = {"sequence_id": "s1", "writes": [write_of(0, 0, "ProcAmp", 0, "Brightness", 3.0),
                                            write_of(0, 9, "ProcAmp", 0, "Brightness", 3.0)]}
    code, out, panel = effect_set(live, capsys, model, "--from", plan_file(tmp_path, plan))
    assert code == 1 and out["code"] == "precondition"
    assert out["error"] == "entry 1: video track 0 has no clip 9"
    assert (out["ran"], out["total"], out["results"], out["resolved"]) == (0, 2, [], [])
    nothing_written(live, model, panel)


def _lumetri(index, name, value):
    return ["--sequence", "s1", "--track", "0", "--clip", "0", "--effect", "Lumetri Color", "--index", str(index),
            "--name", name, "--value", value]


def _keyframed(model):
    model.prop(0, 0, 4, 0)["keyframed"] = True


def _unknown_keys(model):
    model.prop(0, 0, 4, 0)["keyframed"] = None


def _gyroflow(model):
    model.clips[(0, 0)]["effects"].append({"name": "Gyroflow Stabilizer", "match_name": "Gyroflow.Stab", "properties": [
        {"name": "Smoothness", "value": 0.5, "kind": "number", "keyframed": False}]})


def _gyroflow_by_match(model):
    model.clips[(0, 0)]["effects"].append({"name": "Stabilizer", "match_name": "com.gyroflow.toolbox", "properties": [
        {"name": "Smoothness", "value": 0.5, "kind": "number", "keyframed": False}]})


def _unreadable(model):
    model.prop(0, 0, 4, 0).update(kind="unreadable", value=None)


@pytest.mark.parametrize("setup, argv, words", [
    (_keyframed, [*BRIGHTNESS, "--value", "3"], "keyframes"),
    (_unknown_keys, [*BRIGHTNESS, "--value", "3"], "keyframes"),
    (_gyroflow, ["--sequence", "s1", "--node-id", "000f4243", "--effect", "Gyroflow Stabilizer", "--index", "0",
                 "--name", "Smoothness", "--value", "0.2"], "Gyroflow"),
    (_gyroflow_by_match, ["--sequence", "s1", "--node-id", "000f4243", "--effect", "Stabilizer", "--index", "0",
                          "--name", "Smoothness", "--value", "0.2"], "Gyroflow"),
    (None, _lumetri(7, "Input LUT", "1"), "index 7"),
    (None, _lumetri(32, " ", '"/luts/a.cube/"'), "index 32"),
    (None, _lumetri(34, "Look", "1"), "index 34"),
    (None, [*BRIGHTNESS, "--value", '"bright"'], "number"),
    (None, [*BRIGHTNESS, "--value", "true"], "number"),
    (None, _lumetri(4, " ", "3"), "string"),
    (None, ["--sequence", "s1", "--track", "0", "--clip", "0", "--effect", "ProcAmp", "--index", "4",
            "--name", "Split Screen", "--value", "1"], "boolean"),
    (None, _lumetri(13, "White Balance", "5"), "color"),
    (None, ["--sequence", "s1", "--track", "0", "--clip", "0", "--effect", "Motion", "--index", "0",
            "--name", "Position", "--value", "0.5"], "array"),
    (_unreadable, [*BRIGHTNESS, "--value", "3"], "unreadable"),
])
def test_refused_parameters(live, capsys, setup, argv, words):
    model = EffectModel()
    if setup:
        setup(model)
    code, out, panel = effect_set(live, capsys, model, *argv)
    assert code == 1 and out["code"] == "refused_parameter", out
    assert out["error"].startswith("entry 0: ") and words in out["error"] and "Nothing was changed." in out["error"]
    assert (out["ran"], out["results"]) == (0, [])
    nothing_written(live, model, panel)


def test_a_kind_refusal_names_what_can_be_written(live, capsys):
    code, out, _ = effect_set(live, capsys, EffectModel(), *_lumetri(13, "White Balance", "5"))
    assert "only numbers, strings and booleans can be written" in out["error"]


def test_a_refusal_anywhere_in_the_plan_stops_it_before_the_first_write(live, capsys, tmp_path):
    model = EffectModel()
    plan = {"sequence_id": "s1", "writes": [write_of(0, 0, "ProcAmp", 0, "Brightness", 3.0),
                                            write_of(0, 0, "Lumetri Color", 7, "Input LUT", 1)]}
    code, out, panel = effect_set(live, capsys, model, "--from", plan_file(tmp_path, plan))
    assert code == 1 and out["code"] == "refused_parameter" and out["error"].startswith("entry 1: ")
    nothing_written(live, model, panel)


def test_expect_old_mismatch(live, capsys, tmp_path):
    model = EffectModel()
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "3", "--expect-old", "1.0")
    assert code == 1 and out["code"] == "precondition"
    assert out["error"].startswith("entry 0: ") and "1.0" in out["error"] and "7.7" in out["error"]
    nothing_written(live, model, panel)
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "3", "--expect-old", "7.7004")
    assert code == 0 and out["stored"] is True
    plan = {"sequence_id": "s1", "writes": [write_of(0, 0, "ProcAmp", 0, "Brightness", 4, expect_old=3)]}
    code, out, panel = effect_set(live, capsys, model, "--from", plan_file(tmp_path, plan))
    assert code == 0 and out["stored"] is True
    plan = {"sequence_id": "s1", "writes": [write_of(0, 0, "ProcAmp", 0, "Brightness", 5, expect_old="4")]}
    code, out, panel = effect_set(live, capsys, model, "--from", plan_file(tmp_path, plan))
    assert code == 1 and out["code"] == "precondition"


def test_dry_run_sends_reads_only(live, capsys, sends):
    model = EffectModel()
    model.prop(0, 0, 4, 0)["value"] = 5.0
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "-2.6", "--dry-run")
    assert code == 0 and out["dry_run"] is True
    assert sends and not any(sends) and model.writes == [] and not scripts(panel, WRITE)
    assert out["resolved"][0]["value_now"] == 5.0
    assert out["planned"] == [{"entry": 0, "value_now": 5.0, "requested": -2.6, "crosses_zero": True}]
    assert out["sequence"] == {"id": "s1", "name": "Main"} and out["warnings"] == []
    code, out, panel = effect_set(live, capsys, model, *_lumetri(7, "Input LUT", "1"), "--dry-run")
    assert code == 1 and out["code"] == "refused_parameter"                 # a dry run refuses what a write would


def test_own_film_is_refused(live, capsys):
    model = EffectModel()
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "3", state=STATE_OWN)
    assert code == 1 and out["code"] == "project_guard"
    assert len(panel.seen) == 1 and "playhead_ticks: playhead" in panel.seen[0]
    nothing_written(live, model, panel)


GOOD_ENTRY = {"track": 0, "clip": 0, "effect": "ProcAmp", "index": 0, "name": "Brightness", "value": 3}


@pytest.mark.parametrize("plan, words", [
    ("{not json", "not valid JSON"),
    ("[]", "JSON object"),
    ('{"sequence_id": "s1", "writes": [{"track": 0, "clip": 0, "effect": "ProcAmp", "index": 0, '
     '"name": "Brightness", "value": NaN}]}', "not valid JSON"),
    ({"sequence_id": "s1", "writes": [GOOD_ENTRY], "save": True}, "save"),
    ({"sequence_id": "s1", "writes": []}, "writes"),
    ({"sequence_id": "s1"}, "writes"),
    ({"writes": [GOOD_ENTRY]}, "sequence_id"),
    ({"sequence_id": "", "writes": [GOOD_ENTRY]}, "sequence_id"),
    ({"sequence_id": "s1", "sequence_name": 5, "writes": [GOOD_ENTRY]}, "sequence_name"),
    ({"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "track_type": "audio"}]}, "video"),
    ({"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "speed": 2}]}, "speed"),
    ({"sequence_id": "s1", "writes": [{k: v for k, v in GOOD_ENTRY.items() if k != "name"}]}, "name"),
    ({"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "name": 5}]}, "name"),
    ({"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "value": None}]}, "value"),
    ({"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "value": [1, 2]}]}, "value"),
    ({"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "value": {"a": 1}}]}, "value"),
    ({"sequence_id": "s1", "writes": [{k: v for k, v in GOOD_ENTRY.items() if k != "value"}]}, "value"),
    ({"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "expect_old": None}]}, "expect_old"),
    ({"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "index": -1}]}, "index"),
    ({"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "index": True}]}, "index"),
    ({"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "effect_index": 1.5}]}, "effect_index"),
    ({"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "effect": ""}]}, "effect"),
    ({"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "clip": "0"}]}, "clip"),
    ({"sequence_id": "s1", "writes": [{k: v for k, v in GOOD_ENTRY.items() if k != "clip"}]}, "clip"),
    ({"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "node_id": "000f4243"}]}, "node_id"),
    ({"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "value": "a\ud800b"}]}, "value"),
    ({"sequence_id": "s1", "writes": ["not an object"]}, "entry 0"),
])
def test_plan_file_faults(live, capsys, tmp_path, plan, words):
    model = EffectModel()
    code, out, panel = effect_set(live, capsys, model, "--from", plan_file(tmp_path, plan))
    assert code == 1 and out["code"] == "bad_plan", out
    assert words in out["error"] and "Nothing was sent." in out["error"]
    assert panel.seen == [] and model.writes == []


def test_a_plan_fault_names_the_entry(live, capsys, tmp_path):
    plan = {"sequence_id": "s1", "writes": [GOOD_ENTRY, {**GOOD_ENTRY, "value": None}]}
    code, out, panel = effect_set(live, capsys, EffectModel(), "--from", plan_file(tmp_path, plan))
    assert out["code"] == "bad_plan" and "entry 1" in out["error"]
    code, out, panel = effect_set(live, capsys, EffectModel(), "--from", str(tmp_path / "missing.json"))
    assert out["code"] == "bad_plan" and panel.seen == []


@pytest.mark.parametrize("value", [1e400, -1e400, 4294967296, -4294967296.0, 5e12])
def test_a_number_out_of_range_is_refused_before_anything_is_sent(live, capsys, tmp_path, value):
    plan = '{"sequence_id": "s1", "writes": [{"track": 0, "clip": 0, "effect": "ProcAmp", "index": 0, ' \
           '"name": "Brightness", "value": %s}]}' % ("1e400" if value == 1e400 else "-1e400" if value == -1e400 else repr(value))
    model = EffectModel()
    code, out, panel = effect_set(live, capsys, model, "--from", plan_file(tmp_path, plan))
    assert code == 1 and out["code"] == "refused_parameter" and "Nothing was sent." in out["error"]
    assert panel.seen == []
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "4294967296")
    assert out["code"] == "refused_parameter" and panel.seen == []


def test_lut_path_write_warns(live, capsys):
    model = EffectModel()
    code, out, panel = effect_set(live, capsys, model, *_lumetri(4, " ", '"/luts/Example Look.cube/"'))
    assert code == 0 and out["stored"] is True
    assert model.prop(0, 0, 2, 4)["value"] == "/luts/Example Look.cube/"
    assert len(out["warnings"]) == 1 and NOT_PROOF in out["warnings"][0] and "entry 0" in out["warnings"][0]
    code, out, panel = effect_set(live, capsys, model, *_lumetri(6, "Input LUT", "1"))
    assert code == 0 and NOT_PROOF in out["warnings"][0]
    code, out, panel = effect_set(live, capsys, model, *_lumetri(14, "Temperature", "12"))
    assert code == 0 and out["warnings"] == []


def test_breathe_then_moved_target(live, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "MAX_HOLD_S", 0.0)
    model = EffectModel()

    def moved():
        model.clips[(0, 1)]["node_id"] = "000f9999"
    model.moves[2] = moved
    plan = {"sequence_id": "s1", "writes": [write_of(0, 0, "ProcAmp", 0, "Brightness", 3.0),
                                            write_of(0, 1, "ProcAmp", 0, "Brightness", 4.0)]}
    code, out, panel = effect_set(live, capsys, model, "--from", plan_file(tmp_path, plan))
    assert code == 1 and out["code"] == "target_moved"
    assert "entry 1" in out["error"] and "000f4250" in out["error"] and "000f9999" in out["error"]
    assert (out["ran"], out["total"]) == (1, 2) and [r["stored"] for r in out["results"]] == [True]
    assert model.writes == [("000f4243", 4, 0, 3.0)] and model.resolves == 2
    assert sum("playhead_ticks: playhead" in text for text in panel.seen) == 2       # the guards ran again


def test_breathe_then_moved_effect(live, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "MAX_HOLD_S", 0.0)
    model = EffectModel()

    def moved():
        model.clips[(0, 1)]["effects"].insert(0, {"name": "Opacity", "match_name": "AE.ADBE Opacity", "properties": []})
    model.moves[2] = moved
    plan = {"sequence_id": "s1", "writes": [write_of(0, 0, "ProcAmp", 0, "Brightness", 3.0),
                                            write_of(0, 1, "ProcAmp", 0, "Brightness", 4.0)]}
    code, out, panel = effect_set(live, capsys, model, "--from", plan_file(tmp_path, plan))
    assert code == 1 and out["code"] == "target_moved" and "index 0 and is at 1" in out["error"]
    assert model.writes == [("000f4243", 4, 0, 3.0)]


def test_breathe_with_the_same_targets_goes_on(live, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "MAX_HOLD_S", 0.0)
    model = EffectModel()
    plan = {"sequence_id": "s1", "sequence_name": "Main",
            "writes": [write_of(0, 0, "ProcAmp", 0, "Brightness", 3.0), {"node_id": "000f4250", "effect": "ProcAmp",
                                                                         "index": 0, "name": "Brightness", "value": 4.0}]}
    code, out, panel = effect_set(live, capsys, model, "--from", plan_file(tmp_path, plan))
    assert code == 0 and out["stored"] is True and model.resolves == 2
    assert scripts(panel, WRITE)[1]["target"] == {"node_id": "000f4250"}
    assert scripts(panel, RESOLVE)[0]["expect"]["sequence_name"] == "Main"


def test_near_value():
    assert effects.near_value(-2.6) == -2.496
    assert effects.near_value(-40) == -38.4
    assert effects.near_value(-0.077) == -0.074
    assert effects.near_value(-0.002) == -0.001
    assert effects.near_value(-3.7) == -3.552


# ---------- failures in the middle of a batch ----------
def _two_entries(tmp_path) -> str:
    return plan_file(tmp_path, {"sequence_id": "s1", "writes": [write_of(0, 0, "ProcAmp", 0, "Brightness", 3.0),
                                                                write_of(0, 1, "ProcAmp", 0, "Brightness", 4.0)]})


def test_a_precondition_of_the_write_script_stops_the_batch_with_what_ran(live, capsys, tmp_path):
    model = EffectModel()
    calls = []

    def write(script):
        calls.append(script)
        if len(calls) == 2:
            return Exception("Error: precondition: the clip at this place changed")
        return model.write(script)
    code, out, panel = effect_set(live, capsys, model, "--from", _two_entries(tmp_path), extra={WRITE: write})
    assert code == 1 and out["code"] == "precondition" and out["ok"] is False
    assert "the clip at this place changed" in out["error"] and "entry 1" in out["error"]
    assert "may have landed" not in out["error"]                     # attempt 1 refused: nothing was changed
    assert (out["ran"], out["total"], len(out["resolved"])) == (1, 2, 2)
    assert out["results"][0]["stored"] is True and len(out["results"]) == 1
    assert not (live / RECOVERY).exists()


@pytest.mark.parametrize("reply", [
    "not a dict",
    {"before": 1, "zero_first": False, "set_error": None, "value": 3.0},                      # no read_error
    {"before": 1, "zero_first": "no", "set_error": None, "value": 3.0, "read_error": None},
    {"before": 1, "zero_first": False, "set_error": 5, "value": 3.0, "read_error": None},
    {"before": 1, "zero_first": False, "set_error": None, "value": 3.0, "read_error": []},
    {"before": 1, "zero_first": False, "set_error": None, "value": {"a": 1}, "read_error": None},
    {"before": [1], "zero_first": False, "set_error": None, "value": 3.0, "read_error": None},
])
def test_a_write_reply_of_the_wrong_shape_is_bad_reply(live, capsys, tmp_path, reply):
    model = EffectModel()
    calls = []

    def write(script):
        calls.append(script)
        return reply if len(calls) == 2 else model.write(script)
    code, out, panel = effect_set(live, capsys, model, "--from", _two_entries(tmp_path), extra={WRITE: write})
    assert code == 1 and out["code"] == "bad_reply"
    assert "entry 1" in out["error"] and "Check" in out["error"]
    assert out["ran"] == 1 and [r["entry"] for r in out["results"]] == [0, 1]   # entry 1 is not claimed as not stored
    unknown = out["results"][1]
    assert (unknown["stored"], unknown["now"], unknown["restored"], unknown["attempts"]) == (None, None, None, 0)
    assert unknown["error"].startswith("bad_reply: entry 1, attempt 1")


def test_a_write_that_gets_no_answer_stops_the_batch(live, capsys, tmp_path):
    model = EffectModel()
    calls = []

    def write(script):
        calls.append(script)
        return model.write(script) if len(calls) == 1 else None
    replies = {**model.replies(), WRITE: write}

    def handler(script):
        if WRITE in script and len(calls) >= 1:
            calls.append(script)
            return None
        return recipe_handler(replies)(script)
    with panel_on(live, handler=handler):
        code, out = run_json(capsys, [*SET, "--from", _two_entries(tmp_path), "--timeout", "0.5"])
    assert code == 1 and out["code"] == "unknown_outcome"
    assert out["ran"] == 1 and out["results"][0]["stored"] is True and "entry 1" in out["error"]
    assert (live / RECOVERY).exists()


def test_a_resolve_reply_of_the_wrong_shape_is_bad_reply(live, capsys):
    model = EffectModel()
    good = model.resolve

    def resolve(script):
        reply = good(script)
        reply["entries"][0]["property"]["name"] = "Contrast"                 # not the property that was asked for
        return reply
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "3", extra={RESOLVE: resolve})
    assert code == 1 and out["code"] == "bad_reply"
    nothing_written(live, model, panel)

    def no_node(script):
        reply = good(script)
        reply["entries"][0]["clip"]["node_id"] = None
        return reply
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "3", extra={RESOLVE: no_node})
    assert code == 1 and out["code"] == "bad_reply"
    nothing_written(live, model, panel)

    def other_effect(script):
        reply = good(script)
        reply["entries"][0]["effect"]["name"] = "Lumetri Color"
        reply["entries"][0]["effect"]["match_name"] = "AE.ADBE Lumetri"
        return reply
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "3", extra={RESOLVE: other_effect})
    assert code == 1 and out["code"] == "bad_reply"
    nothing_written(live, model, panel)

    def other_clip(script):
        reply = good(script)
        reply["entries"][0]["clip"]["clip"] = 1
        return reply
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "3", extra={RESOLVE: other_clip})
    assert code == 1 and out["code"] == "bad_reply"
    nothing_written(live, model, panel)

    def short(script):
        reply = good(script)
        reply["entries"] = []
        return reply
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "3", extra={RESOLVE: short})
    assert code == 1 and out["code"] == "bad_reply"
    nothing_written(live, model, panel)


def test_a_lone_surrogate_from_premiere_never_reaches_the_output(live, capsys):
    model = EffectModel()
    model.clips[(0, 0)]["name"] = "clip\ud800"
    model.readback["abc"] = "ab\udc00"
    code, out, panel = effect_set(live, capsys, model, *_lumetri(4, " ", '"abc"'))
    assert code == 1 and out["code"] == "not_stored"
    assert out["resolved"][0]["clip"]["name"] == "clip�"
    assert out["results"][0]["value"] == "ab�" and out["results"][0]["stored"] is False
    with panel_on(live, handler=recipe_handler(model.replies())):
        assert dispatch([*SET, *_lumetri(4, " ", '"abc"')]) == 1
    capsys.readouterr()


# ---------- the command line ----------
@pytest.mark.parametrize("argv, words", [
    (["--from", "p.json", "--sequence", "s1"], "--from"),
    (["--from", "p.json", "--value", "3"], "--from"),
    (["--from", "p.json", "--track", "0"], "--from"),
    (["--from", "p.json", "--expect-old", "3"], "--from"),
    (["--sequence", "s1", "--track", "0", "--clip", "0", "--effect", "ProcAmp", "--index", "0", "--name", "Brightness"],
     "--value"),
    (["--track", "0", "--clip", "0", "--effect", "ProcAmp", "--index", "0", "--name", "Brightness", "--value", "3"],
     "--sequence"),
    (["--sequence", "s1", "--effect", "ProcAmp", "--index", "0", "--name", "Brightness", "--value", "3"], "--node-id"),
    (["--sequence", "s1", "--track", "0", "--effect", "ProcAmp", "--index", "0", "--name", "Brightness", "--value", "3"],
     "--node-id"),
    (["--sequence", "s1", "--node-id", "n", "--track", "0", "--clip", "0", "--effect", "ProcAmp", "--index", "0",
      "--name", "Brightness", "--value", "3"], "--node-id"),
    (["--sequence", "s1", "--track", "0", "--clip", "0", "--index", "0", "--name", "Brightness", "--value", "3"], "--effect"),
    (["--sequence", "s1", "--track", "0", "--clip", "0", "--effect", "ProcAmp", "--name", "Brightness", "--value", "3"],
     "--index"),
    (["--sequence", "s1", "--track", "0", "--clip", "0", "--effect", "ProcAmp", "--index", "0", "--value", "3"], "--name"),
    ([*BRIGHTNESS, "--value", "abc"], "--value"),
    ([*BRIGHTNESS, "--value", "null"], "--value"),
    ([*BRIGHTNESS, "--value", "[1, 2]"], "--value"),
    ([*BRIGHTNESS, "--value", '{"a": 1}'], "--value"),
    ([*BRIGHTNESS, "--value", "NaN"], "--value"),
    ([*BRIGHTNESS, "--value", "3", "--expect-old", "null"], "--expect-old"),
    ([*BRIGHTNESS, "--value", "3", "--expect-old", "x"], "--expect-old"),
    ([], "--from"),
])
def test_effect_set_bad_command_lines_send_nothing(live, capsys, argv, words):
    model = EffectModel()
    code, out, panel = effect_set(live, capsys, model, *argv)
    assert code == 1 and out["code"] == "bad_args", out
    assert words in out["error"] and "Nothing was sent." in out["error"]
    assert panel.seen == []


def test_effect_set_takes_the_node_id_and_the_sequence_name(live, capsys):
    model = EffectModel()
    code, out, panel = effect_set(live, capsys, model, "--sequence", "s1", "--sequence-name", "Main", "--node-id",
                                  "000f4250", "--effect", "AE.ADBE ProcAmp", "--index", "0", "--name", "Brightness",
                                  "--value", "-1")
    assert code == 0 and out["stored"] is True
    assert out["resolved"][0]["clip"] == {"node_id": "000f4250", "name": "second.mp4", "track": 0, "clip": 1,
                                          "start_seconds": 6.006}
    assert scripts(panel, RESOLVE)[0]["entries"][0]["target"] == {"node_id": "000f4250"}
    assert scripts(panel, RESOLVE)[0]["expect"]["sequence_name"] == "Main"
    code, out, panel = effect_set(live, capsys, model, "--sequence", "s1", "--sequence-name", "Other", "--node-id",
                                  "000f4250", "--effect", "ProcAmp", "--index", "0", "--name", "Brightness", "--value", "2")
    assert code == 1 and out["code"] == "precondition" and "is named Main, not Other" in out["error"]


def test_effect_set_human_lines(live, capsys, tmp_path):
    model = EffectModel()
    model.prop(0, 0, 4, 0)["value"] = 5.0
    model.fail(-2.6, times=1)
    model.readback[4.0] = 1.0
    with panel_on(live, handler=recipe_handler(model.replies())):
        assert dispatch([*SET, *BRIGHTNESS, "--value", "-2.6"]) == 0
        captured = capsys.readouterr()
        assert captured.out.splitlines() == [
            "entry 0: V1[0] testclip.mp4 at 0.000 s, ProcAmp[4].Brightness[0] = 5.0 -> -2.6",
            "entry 0: stored -2.6 (2 attempts, zero first)",
            "Stored: 1 of 1 entries."]
        plan = {"sequence_id": "s1", "writes": [write_of(0, 0, "ProcAmp", 1, "Contrast", 50),
                                                write_of(0, 1, "ProcAmp", 0, "Brightness", 4.0)]}
        assert dispatch([*SET, "--from", plan_file(tmp_path, plan)]) == 1
        captured = capsys.readouterr()
        assert captured.out.splitlines() == [
            "entry 0: V1[0] testclip.mp4 at 0.000 s, ProcAmp[4].Contrast[1] = 100.0 -> 50",
            "entry 1: V1[1] second.mp4 at 6.006 s, ProcAmp[0].Brightness[0] = 5.0 -> 4.0",
            "entry 0: stored 50 (1 attempt)",
            "entry 1: NOT stored: wanted 4.0. The old value 5.0 was put back."]
        assert "Error: " in captured.err and "not stored" in captured.err
        model.fail(-2.6, times=2)
        model.prop(0, 0, 4, 0)["value"] = -1.0
        assert dispatch([*SET, *BRIGHTNESS, "--value", "-2.6"]) == 1
        lines = capsys.readouterr().out.splitlines()
        assert lines[-1] == "entry 0: NOT stored: -2.496 landed, -2.6 did not (was -1.0)"
        assert dispatch([*SET, *_lumetri(4, " ", '"/luts/a.cube/"'), "--dry-run"]) == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines == ['entry 0: V1[0] testclip.mp4 at 0.000 s, Lumetri Color[2]." "[4] = "" -> "/luts/a.cube/"',
                         "Dry run: nothing was written."]


def test_effect_set_help(capsys):
    assert dispatch(["live", "effect", "set", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "stored: the value read back from Premiere equals the value written. It is not a render check." in text
    assert "A property whose value cannot be read is refused." in text
    assert "A number that is not finite, or is 4294967296 or more in size, is refused." in text
    assert "within 0.001 of the value wanted" in text and "the old value is written back once" in text
    for word in ("keyframe", "Gyroflow", "Lumetri", "7, 32", "34", "array", "colour", "--from", "--dry-run",
                 "first write that is not stored", "--expect-old", "--project", "--any-project"):
        assert word in text, word
    assert "—" not in text
    assert dispatch(["live", "effect", "--help"]) == 0
    assert "set" in capsys.readouterr().out


def test_set_effects_checks_the_plan_before_the_lease(live):
    client = BridgeClient(live, 0.0)
    with pytest.raises(CliError) as err:
        effects.set_effects(client, {"sequence_id": "s1", "writes": []})
    assert err.value.code == "bad_plan" and not (live / LOCK).exists()


def test_load_plan_returns_the_checked_plan(tmp_path):
    plan = effects.load_plan(plan_file(tmp_path, {"sequence_id": "s1", "writes": [{**GOOD_ENTRY, "effect_index": 4}]}))
    assert plan == {"sequence_id": "s1", "sequence_name": None,
                    "writes": [{"track": 0, "clip": 0, "effect": "ProcAmp", "effect_index": 4, "index": 0,
                                "name": "Brightness", "value": 3}]}


# ---------- the scripts, in node with a stub app ----------
SET_PRELUDE = STUB_HELPERS + """
var writes = [];            // [property name, value, the second argument] of every setValue
var readsBack = 0;          // getValue() calls without arguments
function wprop(name, value, extra) {
  extra = extra || {};
  var p = {displayName: name};
  p.getValue = function (t, s) {
    if (arguments.length === 0) { readsBack++; if (extra.readThrows) throw new Error("no read"); }
    return value;
  };
  p.getColorValue = function () { if (extra.color) return extra.color; throw new Error("not a colour"); };
  p.isTimeVarying = function () { if (extra.varying === "throws") throw new Error("no keys"); return extra.varying === true; };
  p.setValue = function (v, notify) {
    writes.push([name, v, notify]);
    if (extra.fails && extra.fails(v)) throw new Error("Illegal Parameter type");
    value = extra.store ? extra.store(v) : v;
  };
  p.setColorValue = function () { writes.push([name, "colour"]); };
  return p;
}
function wcomp(name, match, props) { return {displayName: name, matchName: match, properties: coll("numItems", props)}; }
function wclip(id, name, comps) { return {nodeId: id, name: name, start: {ticks: "0"}, components: coll("numItems", comps)}; }
var seqA = {sequenceID: "S-A", name: "Cut A",
  videoTracks: coll("numTracks", [{clips: coll("numItems", [
    wclip("v0c0", "Intro", [wcomp("Opacity", "AE.ADBE Opacity", [wprop("Opacity", 100)]),
                            wcomp("ProcAmp", "AE.ADBE ProcAmp", [wprop("Brightness", 7.7), wprop("Contrast", 100)]),
                            wcomp("Lumetri Color", "AE.ADBE Lumetri", [wprop(" ", "", {}), wprop("Temperature", 10),
                                                                       wprop("White Balance", 0, {color: [255, 1, 2, 3]})]),
                            wcomp("Lumetri Color", "AE.ADBE Lumetri", [wprop(" ", ""), wprop("Temperature", 5)])]),
    wclip("v0c1", "Middle", [wcomp("ProcAmp", "AE.ADBE ProcAmp", [wprop("Brightness", 5)])])])}]),
  audioTracks: coll("numTracks", [{clips: coll("numItems", [
    wclip("a0c0", "Music", [wcomp("Volume", "AE.ADBE Volume", [wprop("Level", -3)])])])}])};
var seqB = {sequenceID: "S-B", name: "Cut B",
  videoTracks: coll("numTracks", [{clips: coll("numItems", [
    wclip("v0c0", "Other", [wcomp("Opacity", "AE.ADBE Opacity", [wprop("Opacity", 100)]),
                            wcomp("ProcAmp", "AE.ADBE ProcAmp", [wprop("Brightness", 1)])])])}]),
  audioTracks: coll("numTracks", [])};
var app = {project: {path: "/p/X Claude.prproj", sequences: coll("numSequences", [seqA, seqB]), activeSequence: seqB}};
"""
PROCAMP = "seqA.videoTracks[0].clips[0].components[1]"
SET_EXPECT = {"project_path": "/p/X Claude.prproj", "sequence_id": "S-A", "sequence_name": "Cut A"}
WRITE_ARGS = {"sequence_id": "S-A", "target": {"track_type": "video", "track": 0, "clip": 0}, "node_id": "v0c0",
              "effect": "ProcAmp", "effect_index": None, "found_effect_index": 1, "index": 0, "name": "Brightness",
              "value": 3, "tolerance": 0.001, "expect": SET_EXPECT}


def run_set_script(name: str, args: dict, prelude: str = "") -> tuple[dict, dict]:
    """Run effect_resolve.jsx or effect_write.jsx in node. Returns (the parsed reply, {writes, readsBack})."""
    include = ("find", "values") if name == "effect_resolve" else ("find",)
    script = jsx.build(jsx.load(name), args, include=include)
    code = (SET_PRELUDE + prelude + "\nvar __reply = String(" + script + ");\n"
            + "process.stdout.write(JSON.stringify({reply: __reply, writes: writes, readsBack: readsBack}));")
    out = json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout)
    return json.loads(out["reply"]), out


def run_write(prelude: str = "", **over) -> tuple[dict, dict]:
    return run_set_script("effect_write", {**WRITE_ARGS, **over}, prelude)


def brightness(value, extra: str = "{}") -> str:
    return f"{PROCAMP}.properties[0] = wprop('Brightness', {value}, {extra});"


@needs_node
def test_effect_scripts_are_es3_and_run_in_node():
    for name in ("effect_resolve", "effect_write", "values"):
        text = jsx.load(name)
        for pattern in BANNED + [r"\.indexOf\(", r"\.reduce\(", r"\.some\(", r"\.every\(", r"\bArray\.", r"\.isArray"]:
            assert not re.search(pattern, text), (name, pattern)
    assert jsx.load("effect_resolve").split("\n")[0] == "// recipe: effect_resolve"
    assert jsx.load("effect_write").split("\n")[0] == "// recipe: effect_write"
    assert "recipe:" not in jsx.load("values") and "recipe:" not in jsx.load("find")
    # a plain write: set, read back, no zero step
    reply, seen = run_write()
    assert reply == {"success": True, "data": {"before": 7.7, "zero_first": False, "set_error": None, "value": 3,
                                               "read_error": None}}
    assert seen["writes"] == [["Brightness", 3, True]] and seen["readsBack"] == 2      # before and after
    # crossing zero: 0 first, then the value, in one round trip
    reply, seen = run_write(brightness(5), value=-2.6)
    assert reply["data"] == {"before": 5, "zero_first": True, "set_error": None, "value": -2.6, "read_error": None}
    assert seen["writes"] == [["Brightness", 0, True], ["Brightness", -2.6, True]]
    # a setValue that throws is caught into set_error, and the read back still comes
    reply, seen = run_write(brightness(-1, "{fails: function (v) { return v === -2.6; }}"), value=-2.6)
    assert reply["success"] is True
    assert reply["data"] == {"before": -1, "zero_first": False, "set_error": "Error: Illegal Parameter type",
                             "value": -1, "read_error": None}


@needs_node
def test_effect_write_script_zero_step_that_fails_stops_there():
    reply, seen = run_write(brightness(5, "{fails: function (v) { return v === 0; }}"), value=-2.6)
    assert reply["data"]["zero_first"] is True and reply["data"]["set_error"] == "zero step failed: Error: Illegal Parameter type"
    assert seen["writes"] == [["Brightness", 0, True]]                     # the value itself was never written
    reply, seen = run_write(brightness(5, "{store: function (v) { return v === 0 ? 0.5 : v; }}"), value=-2.6)
    assert reply["data"]["set_error"] == "zero step failed: read back 0.5" and reply["data"]["value"] == 0.5
    assert seen["writes"] == [["Brightness", 0, True]]
    reply, seen = run_write(brightness(5, "{store: function (v) { return v === 0 ? 0.0004 : v; }}"), value=-2.6)
    assert reply["data"]["set_error"] is None and seen["writes"][-1] == ["Brightness", -2.6, True]   # within the tolerance
    reply, seen = run_write(brightness(0, "{}"), value=-2.6)
    assert reply["data"]["zero_first"] is False and seen["writes"] == [["Brightness", -2.6, True]]  # 0 does not cross


@needs_node
def test_effect_write_script_read_errors():
    reply, seen = run_write(brightness(7.7, "{readThrows: true}"))
    assert reply["data"]["before"] is None and reply["data"]["read_error"] == "Error: no read"
    assert reply["data"]["value"] is None and seen["writes"] == [["Brightness", 3, True]]
    reply, seen = run_write(brightness(5, "{readThrows: true}"), value=-2.6)        # before unknown: no zero step
    assert reply["data"]["zero_first"] is False


@needs_node
def test_effect_write_script_strings_and_booleans():
    reply, seen = run_write(f"{PROCAMP}.properties[1] = wprop('Contrast', true);", index=1, name="Contrast", value=False)
    assert reply["data"]["value"] is False and seen["writes"] == [["Contrast", False, True]]
    reply, seen = run_write(effect="Lumetri Color", effect_index=2, found_effect_index=2, index=0, name=" ",
                            value="/luts/a.cube/")
    assert reply["data"] == {"before": "", "zero_first": False, "set_error": None, "value": "/luts/a.cube/",
                             "read_error": None}


@pytest.mark.parametrize("prelude, over, text", [
    ("app.project.path = '/p/Family film.prproj';", {},
     "the active project is /p/Family film.prproj, not /p/X Claude.prproj"),
    ("seqA.name = 'Renamed';", {}, "sequence S-A is named Renamed, not Cut A"),
    ("", {"node_id": "v0c1"}, "the clip at this place changed"),
    ("seqA.videoTracks[0].clips[0].nodeId = 'zz';", {}, "the clip at this place changed"),
    ("", {"found_effect_index": 2}, "the effect at this place changed"),
    ("", {"effect": "Lumetri Color", "found_effect_index": 2, "index": 1, "name": "Temperature"},
     "ambiguous_effect: 2 effects are named Lumetri Color; pass effect_index"),
    ("", {"name": "Contrast"}, "property 0 of ProcAmp is named Brightness, not Contrast"),
    ("", {"target": {"track_type": "video", "track": 0, "clip": 5}}, "video track 0 has no clip 5"),
    (brightness(7.7, "{varying: true}"), {}, "property is keyframed"),
    (brightness(7.7, "{varying: 'throws'}"), {}, "the animation state of the property could not be read"),
    (f"{PROCAMP}.properties[0].isTimeVarying = undefined;", {}, "the animation state of the property could not be read"),
    ("", {"sequence_id": "S-B"}, "the script was sent for sequence S-B, but it expects S-A"),
    ("", {"value": [1, 2]}, "the value must be a number, a string or a boolean"),
    ("", {"value": None}, "the value must be a number, a string or a boolean"),
])
@needs_node
def test_effect_write_script_refuses_before_any_write(prelude, over, text):
    reply, seen = run_write(prelude, **over)
    assert reply["success"] is False and reply["error"] == "Error: precondition: " + text
    assert seen["writes"] == []


@needs_node
def test_effect_write_script_takes_the_given_sequence_not_the_active_one():
    reply, seen = run_write()
    assert seen["writes"] == [["Brightness", 3, True]]
    reply, _ = run_set_script("effect_write", {**WRITE_ARGS, "sequence_id": "S-B", "found_effect_index": 1,
                                               "expect": {**SET_EXPECT, "sequence_id": "S-B", "sequence_name": "Cut B"}},
                              "")
    assert reply["data"]["before"] == 1                                    # S-B's own v0c0, read by ID


RESOLVE_ENTRY = {"target": {"track_type": "video", "track": 0, "clip": 0}, "effect": "ProcAmp", "effect_index": None,
                 "index": 0, "name": "Brightness"}


def run_resolve(entries, prelude: str = "", **over) -> tuple[dict, dict]:
    return run_set_script("effect_resolve", {"sequence_id": "S-A", "entries": entries, "expect": SET_EXPECT, **over},
                          prelude)


@needs_node
def test_effect_resolve_script_resolves_every_entry_in_one_read():
    entries = [RESOLVE_ENTRY,
               {"target": {"node_id": "v0c1"}, "effect": "AE.ADBE ProcAmp", "effect_index": None, "index": 0,
                "name": "Brightness"},
               {"target": {"track_type": "video", "track": 0, "clip": 0}, "effect": "Lumetri Color", "effect_index": 2,
                "index": 2, "name": "White Balance"},
               {"target": {"track_type": "video", "track": 0, "clip": 0}, "effect": "Lumetri Color", "effect_index": 3,
                "index": 0, "name": " "}]
    reply, seen = run_resolve(entries, brightness(7.7, "{varying: true}"))
    assert reply["success"] is True and seen["writes"] == []
    data = reply["data"]
    assert data["sequence"] == {"id": "S-A", "name": "Cut A"}
    assert data["entries"] == [
        {"clip": {"node_id": "v0c0", "name": "Intro", "track_type": "video", "track": 0, "clip": 0, "start_ticks": "0"},
         "effect": {"effect_index": 1, "name": "ProcAmp", "match_name": "AE.ADBE ProcAmp"},
         "property": {"index": 0, "name": "Brightness", "value": 7.7, "kind": "number", "keyframed": True}},
        {"clip": {"node_id": "v0c1", "name": "Middle", "track_type": "video", "track": 0, "clip": 1, "start_ticks": "0"},
         "effect": {"effect_index": 0, "name": "ProcAmp", "match_name": "AE.ADBE ProcAmp"},
         "property": {"index": 0, "name": "Brightness", "value": 5, "kind": "number", "keyframed": False}},
        {"clip": {"node_id": "v0c0", "name": "Intro", "track_type": "video", "track": 0, "clip": 0, "start_ticks": "0"},
         "effect": {"effect_index": 2, "name": "Lumetri Color", "match_name": "AE.ADBE Lumetri"},
         "property": {"index": 2, "name": "White Balance", "value": [255, 1, 2, 3], "kind": "color", "keyframed": False}},
        {"clip": {"node_id": "v0c0", "name": "Intro", "track_type": "video", "track": 0, "clip": 0, "start_ticks": "0"},
         "effect": {"effect_index": 3, "name": "Lumetri Color", "match_name": "AE.ADBE Lumetri"},
         "property": {"index": 0, "name": " ", "value": "", "kind": "string", "keyframed": False}}]
    reply, _ = run_resolve([RESOLVE_ENTRY], f"{PROCAMP}.properties[0].isTimeVarying = function () {{ throw new Error('x'); }};")
    assert reply["data"]["entries"][0]["property"]["keyframed"] is None


@needs_node
def test_effect_resolve_script_fails_whole_on_the_first_bad_entry():
    bad = {**RESOLVE_ENTRY, "target": {"track_type": "video", "track": 0, "clip": 9}}
    reply, _ = run_resolve([RESOLVE_ENTRY, bad, {**RESOLVE_ENTRY, "name": "Nope"}])
    assert reply == {"success": False, "error": "Error: precondition: entry 1: video track 0 has no clip 9"}
    reply, _ = run_resolve([{**RESOLVE_ENTRY, "effect": "Lumetri Color", "index": 1, "name": "Temperature"}])
    assert reply["error"] == "Error: precondition: entry 0: ambiguous_effect: 2 effects are named Lumetri Color; pass effect_index"
    reply, _ = run_resolve([RESOLVE_ENTRY, {**RESOLVE_ENTRY, "name": "Hue"}])
    assert reply["error"] == "Error: precondition: entry 1: property 0 of ProcAmp is named Brightness, not Hue"
    reply, _ = run_resolve([RESOLVE_ENTRY], "app.project.path = '/p/Other.prproj';")
    assert reply["error"] == "Error: precondition: the active project is /p/Other.prproj, not /p/X Claude.prproj"
    reply, _ = run_resolve([RESOLVE_ENTRY], sequence_id="S-B")
    assert reply["error"] == "Error: precondition: the script was sent for sequence S-B, but it expects S-A"
    # a host call that throws is not a precondition, but it still names the entry
    reply, _ = run_resolve([RESOLVE_ENTRY], "Object.defineProperty(seqA.videoTracks[0].clips[0], 'components', "
                                            "{get: function () { throw new Error('host'); }});")
    assert reply["success"] is False and reply["error"].startswith("Error: entry 0: Error: host")


@needs_node
def test_effect_set_runs_end_to_end_through_the_command(live, capsys):
    """The command and the real scripts together: the panel runs effect_resolve.jsx and effect_write.jsx in node
    against a fresh stub each time (a single write needs no memory between the two)."""
    state = {**RECIPE_STATE, "active_sequence": {"id": "S-B", "name": "Cut B"}}

    def in_node(script: str):
        code = SET_PRELUDE + "\nprocess.stdout.write(String(" + script + "));"
        reply = json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout)
        return reply["data"] if reply["success"] else Exception(reply["error"])
    with panel_on(live, handler=recipe_handler({RESOLVE: in_node, WRITE: in_node}, state=state)) as panel:
        code, out = run_json(capsys, [*SET, "--sequence", "S-A", "--track", "0", "--clip", "0", "--effect", "ProcAmp",
                                      "--index", "0", "--name", "Brightness", "--value", "-2.6"])
        code2, out2 = run_json(capsys, [*SET, "--sequence", "S-A", "--track", "0", "--clip", "0", "--effect", "ProcAmp",
                                        "--index", "1", "--name", "Brightness", "--value", "3"])
    assert code == 0 and out["stored"] is True
    assert out["results"][0] == {"entry": 0, "requested": -2.6, "before": 7.7, "value": -2.6, "stored": True,
                                 "attempts": 1, "zero_first": True, "landed": None, "restored": None, "now": -2.6,
                                 "error": None}
    assert out["resolved"][0]["clip"]["node_id"] == "v0c0" and out["resolved"][0]["effect_index"] == 1
    assert code2 == 1 and out2["code"] == "precondition"
    assert out2["error"] == "entry 0: property 1 of ProcAmp is named Contrast, not Brightness"
    assert sum(WRITE in text for text in panel.seen) == 1


# ---------- fix round 1 ----------
RESERVED = ("abstract", "boolean", "byte", "char", "class", "const", "default", "double", "enum", "export", "extends",
            "final", "float", "goto", "implements", "import", "int", "interface", "long", "native", "package",
            "private", "protected", "public", "short", "static", "super", "synchronized", "throws", "transient",
            "volatile", "delete", "in", "new", "this", "typeof", "void", "with")
UNQUOTED_RESERVED_KEY = r"[{,]\s*(%s)\s*:" % "|".join(RESERVED)


@pytest.mark.parametrize("name", ["effect_resolve", "effect_write", "values", "effect_get", "find"])
def test_recipe_scripts_have_no_reserved_word_as_an_unquoted_key(name):
    code = "\n".join(line for line in jsx.load(name).splitlines() if not line.lstrip().startswith("//"))
    assert not re.search(UNQUOTED_RESERVED_KEY, code), name
    assert not re.search(r"\.(%s)\b" % "|".join(RESERVED), code), name


# I5.1: video clips only, also by node ID
@needs_node
def test_both_scripts_refuse_an_audio_clip_named_by_node_id():
    text = "Error: precondition: entry 0: this clip is on an audio track; effect set writes video clips only"
    reply, seen = run_resolve([{"target": {"node_id": "a0c0"}, "effect": "Volume", "effect_index": None, "index": 0,
                                "name": "Level"}])
    assert reply == {"success": False, "error": text} and seen["writes"] == []
    reply, seen = run_write(target={"node_id": "a0c0"}, node_id="a0c0", effect="Volume", found_effect_index=0,
                            name="Level", value=-6)
    assert reply == {"success": False, "error": text.replace("entry 0: ", "")} and seen["writes"] == []


def test_an_audio_clip_by_node_id_is_refused_before_any_write(live, capsys):
    model = EffectModel()
    model.audio[(0, 0)] = {"node_id": "000f4300", "name": "music.wav"}
    code, out, panel = effect_set(live, capsys, model, "--sequence", "s1", "--node-id", "000f4300", "--effect", "Volume",
                                  "--index", "0", "--name", "Level", "--value", "-6")
    assert code == 1 and out["code"] == "precondition"
    assert out["error"] == "entry 0: this clip is on an audio track; effect set writes video clips only"
    nothing_written(live, model, panel)

    def audio_record(script):              # a resolve that does not refuse: Python still requires "video"
        reply = model.resolve(script)
        reply["entries"][0]["clip"]["track_type"] = "audio"
        return reply
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "3", extra={RESOLVE: audio_record})
    assert code == 1 and out["code"] == "bad_reply"
    nothing_written(live, model, panel)


# Q13: the old value is put back when nothing landed
def test_nothing_lands_and_the_put_back_fails(live, capsys):
    model = EffectModel()
    model.fail(-2.6, times=2)
    model.fail(-2.496, times=1)
    model.fail(7.7, times=1, error="Illegal Parameter type, put back")
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "-2.6")
    assert code == 1 and out["code"] == "not_stored"
    result = out["results"][0]
    assert (result["stored"], result["restored"], result["now"], result["before"]) == (False, False, 0, 7.7)
    assert "Putting the old value back failed: Illegal Parameter type, put back" in result["error"]
    assert [args["value"] for args in scripts(panel, WRITE)] == [-2.6, -2.6, -2.496, 7.7]      # one put-back only
    assert model.prop(0, 0, 4, 0)["value"] == 0
    with panel_on(live, handler=recipe_handler(model.replies())):
        model.prop(0, 0, 4, 0)["value"] = 5.0
        model.fail(-2.6, times=2)
        model.fail(-2.496, times=1)
        model.fail(5.0, times=1, error="no")
        assert dispatch([*SET, *BRIGHTNESS, "--value", "-2.6"]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == (
        "entry 0: NOT stored: wanted -2.6. The property now holds 0 (was 5.0). Putting the old value back failed: no")


def test_nothing_lands_and_the_old_value_is_put_back_in_human_lines(live, capsys):
    model = EffectModel()
    model.prop(0, 0, 4, 0)["value"] = 5.0
    model.fail(-2.6, times=2)
    model.fail(-2.496, times=1)
    with panel_on(live, handler=recipe_handler(model.replies())):
        assert dispatch([*SET, *BRIGHTNESS, "--value", "-2.6"]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == "entry 0: NOT stored: wanted -2.6. The old value 5.0 was put back."
    assert model.prop(0, 0, 4, 0)["value"] == 5.0


def test_a_failure_that_left_the_old_value_needs_no_put_back(live, capsys):
    model = EffectModel()
    model.fail(3.0, times=1)                       # setValue throws; the property still holds 7.7
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "3.0")
    result = out["results"][0]
    assert code == 1 and (result["stored"], result["restored"], result["now"], result["value"]) == (False, None, 7.7, 7.7)
    assert [args["value"] for args in scripts(panel, WRITE)] == [3.0]
    with panel_on(live, handler=recipe_handler(model.replies())):
        model.fail(3.0, times=1)
        assert dispatch([*SET, *BRIGHTNESS, "--value", "3.0"]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == (
        "entry 0: NOT stored: wanted 3.0. Illegal Parameter type. The property now holds 7.7 (was 7.7).")


def test_a_read_back_that_is_near_the_old_value_needs_no_put_back(live, capsys):
    model = EffectModel()
    model.readback[3.0] = 7.7004                   # not 3.0, but the same as before by the tolerance
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "3.0")
    assert out["results"][0]["restored"] is None and len(scripts(panel, WRITE)) == 1


# a CliError in the middle of an entry
def test_an_error_in_the_middle_of_an_entry_keeps_what_is_known(live, capsys):
    model = EffectModel()
    model.fail(-2.6, times=2)
    calls = []

    def write(script):
        calls.append(script)
        if len(calls) == 2:
            return Exception("Error: precondition: the clip at this place changed")
        return model.write(script)
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "-2.6", extra={WRITE: write})
    assert code == 1 and out["code"] == "precondition"
    assert "entry 0, attempt 2" in out["error"] and "may have landed" in out["error"]
    assert out["ran"] == 0 and len(out["results"]) == 1
    assert out["results"][0] == {"entry": 0, "requested": -2.6, "before": 7.7, "value": 0, "stored": None, "attempts": 1,
                                 "zero_first": True, "landed": None, "restored": None, "now": None,
                                 "error": out["results"][0]["error"]}
    assert out["results"][0]["error"].startswith("precondition: entry 0, attempt 2")
    assert len(calls) == 2                          # no put-back after an error
    with panel_on(live, handler=recipe_handler({**model.replies(), WRITE: write})):
        calls.clear()
        model.prop(0, 0, 4, 0)["value"] = 7.7
        model.fail(-2.6, times=2)
        assert dispatch([*SET, *BRIGHTNESS, "--value", "-2.6"]) == 1
    assert capsys.readouterr().out.splitlines()[-1].startswith(
        "entry 0: outcome unknown after 1 finished attempts: precondition: entry 0, attempt 2")


def test_an_error_while_putting_back_reports_the_entry_as_not_stored(live, capsys):
    model = EffectModel()
    model.readback[3.0] = 2.5
    calls = []

    def write(script):
        calls.append(script)
        return Exception("Error: precondition: property is keyframed") if len(calls) == 2 else model.write(script)
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "3.0", extra={WRITE: write})
    assert code == 1 and out["code"] == "precondition" and "putting the old value back" in out["error"]
    result = out["results"][0]
    assert (result["stored"], result["restored"], result["now"], result["value"]) == (False, None, None, 2.5)
    assert "Putting the old value back failed: precondition" in result["error"] and out["ran"] == 1


def test_a_failure_payload_keeps_the_warnings(live, capsys, tmp_path):
    model = EffectModel()
    calls = []

    def write(script):
        calls.append(script)
        return Exception("Error: precondition: the clip at this place changed") if len(calls) == 2 else model.write(script)
    plan = {"sequence_id": "s1", "writes": [write_of(0, 0, "Lumetri Color", 4, " ", "/luts/a.cube/"),
                                            write_of(0, 1, "ProcAmp", 0, "Brightness", 4.0)]}
    code, out, panel = effect_set(live, capsys, model, "--from", plan_file(tmp_path, plan), "--allow-untested",
                                  extra={WRITE: write}, state=STATE_26)
    assert code == 1 and out["code"] == "precondition"
    assert any("27.0" in w for w in out["warnings"])                       # the guard's warning
    assert any(NOT_PROOF in w for w in out["warnings"])                    # the Lumetri warning of entry 0


def test_a_huge_integer_is_refused_without_a_crash(live, capsys, tmp_path):
    big = "1" + "0" * 400
    plan = ('{"sequence_id": "s1", "writes": [{"track": 0, "clip": 0, "effect": "ProcAmp", "index": 0, '
            '"name": "Brightness", "value": %s}]}' % big)
    model = EffectModel()
    code, out, panel = effect_set(live, capsys, model, "--from", plan_file(tmp_path, plan))
    assert code == 1 and out["code"] == "refused_parameter" and panel.seen == []
    plan = plan.replace('"value": %s' % big, '"value": 3, "expect_old": -%s' % big)
    code, out, panel = effect_set(live, capsys, model, "--from", plan_file(tmp_path, plan))
    assert code == 1 and out["code"] == "bad_plan" and panel.seen == []
    code, out, panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", big)
    assert code == 1 and out["code"] == "refused_parameter" and panel.seen == []
    assert effects.same_value(10 ** 400, 1.0) is False and effects.same_value(10 ** 400, 10 ** 400) is False


# ---------- task 10 fix round 1: a failure payload with a lone surrogate ----------
def test_a_lone_surrogate_in_a_failure_prints_valid_json_and_human_text(live, capsys, monkeypatch):
    model = EffectModel()
    model.fail(-2.6, times=2)
    model.fail(-2.496, times=1)
    code, out, _panel = effect_set(live, capsys, model, *BRIGHTNESS, "--value", "-2.6")
    assert code == 1 and out["code"] == "not_stored"
    payload = {k: v for k, v in out.items() if k not in ("ok", "error", "code")}
    payload["results"][0]["error"] = "Illegal \ud800 type"           # Premiere can send such text

    def failing(*_a, **_k):
        err = CliError("not_stored", "bad \udcff text")
        err.payload = payload
        raise err
    monkeypatch.setattr(effects, "set_effects", failing)
    code, out = run_json(capsys, [*SET, *BRIGHTNESS, "--value", "-2.6"])
    assert code == 1 and out["error"] == "bad \udcff text"
    assert out["results"][0]["error"] == "Illegal \ud800 type"       # nothing lost: the JSON escapes it
    assert dispatch([*SET, *BRIGHTNESS, "--value", "-2.6"]) == 1
    captured = capsys.readouterr()
    assert captured.err.startswith("Error: bad ") and "\udcff" not in captured.err
