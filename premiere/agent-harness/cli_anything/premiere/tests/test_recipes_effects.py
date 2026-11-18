"""Tests for `live effect get`: the recipe (core/recipes/effects.py), its script (core/scripts/effect_get.jsx)
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
from cli_anything.premiere.tests.fakes import (  # noqa: F401
    RECIPE_STATE, FakePanel, bridge_dir, keep_signals, recipe_handler, script_args)
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
    script = jsx.build(jsx.load("effect_get"), args, include=("find",))
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
