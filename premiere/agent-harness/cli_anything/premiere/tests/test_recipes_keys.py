"""Tests for `live keys get` and the key math: core/recipes/keys.py, the scripts core/scripts/keys.jsx and
core/scripts/keys_get.jsx, and the command (live_recipes.py).

The math runs on plain numbers and on the plan data in tests/data/level_v10.json. The command runs against a
fake panel in a temp folder, answered by KeysModel. The script runs in node with a stub `app`, alone and as a
shape check against KeysModel.
"""
from __future__ import annotations

import contextlib
import json
import math
import re
import subprocess
from pathlib import Path

import pytest

from cli_anything.premiere import live_cli
from cli_anything.premiere.core import guard, jsx
from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.core.recipes import keys
from cli_anything.premiere.core.recipes.clips import TICKS_PER_SECOND
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.premiere_cli import dispatch
from cli_anything.premiere.tests.fakes import (  # noqa: F401
    KEYS_CLIP_TICKS, KEYS_FRAME_TICKS, RECIPE_STATE, FakePanel, KeysModel, bridge_dir, keep_signals, recipe_handler,
    script_args)
from cli_anything.premiere.tests.test_jsx import BANNED, NODE, STUB_HELPERS, needs_node, refused

MARK = "recipe: keys_get"
FIND_MARK = "// find.jsx: target finders for recipe scripts"
LOCK = ".host-owner-lease"
TPS = 254016000000
DATA = Path(__file__).resolve().parent / "data"
LENGTH = 836972136000          # 79 frames at 23.976 fps
FRAME = 10594584000
assert (KEYS_CLIP_TICKS, KEYS_FRAME_TICKS) == (LENGTH, FRAME)
STATE_OWN = {**RECIPE_STATE, "projects": [{"name": "Family film.prproj", "path": "/p/Family film.prproj", "active": True}]}
NODE_ARGS = ["--sequence", "s1", "--node-id", "000f4276"]
ARGS_BY_NODE = {"sequence_id": "s1", "target": {"node_id": "000f4276"}, "effect": "Motion", "property": "Rotation",
                "property_index": None}


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


def level() -> list[dict]:
    return json.loads((DATA / "level_v10.json").read_text(encoding="utf-8"))


def kt(*pairs, length=LENGTH, frame=FRAME) -> list[dict]:
    return keys.key_ticks([{"time_seconds": s, "value": v} for s, v in pairs], length_ticks=length, frame_ticks=frame)


# =====================================================================================================
# key_ticks
# =====================================================================================================
def test_the_constants_are_the_brief_values():
    assert keys.KEY_TOLERANCE == 0.0001 and keys.STATIC_SPREAD == 0.75
    assert issubclass(keys.KeyFault, CliError)


def test_key_ticks_moves_the_end_key():
    got = kt((1.247, 1.01), (3.295, 0.07))
    assert got[0] == {"seconds": 1.247, "ticks": 316757952000, "value": 1.01, "moved_from": None}
    assert got[1] == {"seconds": 3.295, "ticks": 826377552000, "value": 0.07, "moved_from": 3.295}
    assert got[1]["ticks"] == LENGTH - FRAME                      # the end minus one frame, exact in ticks
    assert all(isinstance(k["ticks"], int) for k in got)


def test_key_ticks_half_frame_line():
    # The line is the end minus half a frame: 3.274104... s for this clip.
    assert kt((3.2742, 0.5))[0]["moved_from"] == 3.2742
    assert kt((3.2742, 0.5))[0]["ticks"] == LENGTH - FRAME
    assert kt((3.2741, 0.5))[0]["moved_from"] is None
    assert kt((3.2741, 0.5))[0]["ticks"] == round(3.2741 * TICKS_PER_SECOND)


def test_key_ticks_clash_after_the_move():
    with pytest.raises(keys.KeyFault) as err:
        kt((1.01, 0.1), (1.02, 0.2))                              # both on frame 24
    assert err.value.code == "key_clash" and "1.01" in err.value.message and "1.02" in err.value.message
    with pytest.raises(keys.KeyFault) as err:
        kt((3.28, 0.1), (3.295, 0.2))                             # both moved to frame 78
    assert err.value.code == "key_clash" and "3.28" in err.value.message and "3.295" in err.value.message
    assert isinstance(err.value, CliError)


def test_key_ticks_negative():
    with pytest.raises(keys.KeyFault) as err:
        kt((0.5, 0.1), (-0.001, 0.2))
    assert err.value.code == "negative_key" and "-0.001" in err.value.message


def test_key_ticks_first_key_at_zero_and_no_keys():
    assert kt((0.0, 1.26)) == [{"seconds": 0.0, "ticks": 0, "value": 1.26, "moved_from": None}]
    assert kt() == []


def test_key_ticks_one_frame_apart_is_not_a_clash():
    got = kt((1.0, 0.1), (1.0 + FRAME / TPS, 0.2))                # frame 23 and frame 24
    assert [k["ticks"] // FRAME for k in got] == [23, 24]


@pytest.mark.parametrize("pair", [("1.0", 0.1), (None, 0.1), (True, 0.1), (float("nan"), 0.1), (float("inf"), 0.1)])
def test_key_ticks_refuses_a_time_that_is_not_a_finite_number(pair):
    with pytest.raises(CliError) as err:
        kt(pair)
    assert err.value.code == "bad_plan"


@pytest.mark.parametrize("length, frame", [(LENGTH, 0), (LENGTH, -1), (FRAME - 1, FRAME), (0, FRAME), (LENGTH, 1.5)])
def test_key_ticks_refuses_a_clip_that_cannot_hold_a_key(length, frame):
    with pytest.raises(CliError) as err:
        kt((0.0, 0.1), length=length, frame=frame)
    assert err.value.code == "bad_args"


# =====================================================================================================
# static_rule and entry_mode
# =====================================================================================================
def test_static_rule_is_the_midpoint_and_the_spread():
    assert keys.static_rule([1.26, 1.01, 1.1, 0.07]) == {"static": False, "value": 0.67, "spread": 1.19}
    assert keys.static_rule([-2.0, -2.1, -2.7]) == {"static": True, "value": -2.35, "spread": 0.7}


def test_static_rule_boundary_is_inclusive():
    assert keys.static_rule([0.0, 0.75])["static"] is True
    assert keys.static_rule([0.0, 0.7501])["static"] is False
    assert keys.static_rule([0.1, 0.85])["static"] is True        # 0.85 - 0.1 is 0.75 up to float noise
    assert keys.static_rule([0.0, 0.9], 0.9)["static"] is True
    assert keys.static_rule([0.0, 0.0], 0)["static"] is True      # a spread of 0 is a real value
    assert keys.static_rule([0.0, 0.01], 0)["static"] is False


def test_static_rule_needs_numbers():
    for values in ([], [1.0, "2"], [1.0, None], [True], [float("nan")]):
        with pytest.raises(CliError) as err:
            keys.static_rule(values)
        assert err.value.code == "bad_plan"


def test_level_v10_modes_from_the_file():
    data = level()
    assert len(data) == 24
    modes = [keys.entry_mode(entry) for entry in data]
    kinds = [mode["mode"] for mode in modes]
    assert (kinds.count("keys"), kinds.count("static"), kinds.count("none")) == (11, 13, 0)
    assert sum(len(entry["keys"]) for entry, mode in zip(data, modes) if mode["mode"] == "keys") == 60
    for entry, mode in zip(data, modes):
        assert set(mode) == {"mode", "value", "spread", "source"} and mode["source"] == "file"
        assert mode["spread"] == round(max(k["value"] for k in entry["keys"]) - min(k["value"] for k in entry["keys"]), 2)
        if mode["mode"] == "static":
            assert entry["static"] is True and mode["value"] == entry["static_value"]
        else:
            assert entry["static"] is False and mode["value"] is None


def test_level_v10_rule_matches_the_file():
    data = level()
    from_file = [keys.entry_mode(entry) for entry in data]
    from_rule = [keys.entry_mode(entry, static_spread=keys.STATIC_SPREAD) for entry in data]
    assert [m["mode"] for m in from_rule] == [m["mode"] for m in from_file]
    assert all(m["source"] == "rule" for m in from_rule)
    off = 0
    for entry, mode in zip(data, from_rule):
        rule = keys.static_rule([k["value"] for k in entry["keys"]])
        gap = abs(rule["value"] - entry["static_value"])
        assert gap <= 0.01 + 1e-9, entry["name"]
        off += gap > 1e-9
        if mode["mode"] == "static":
            assert mode["value"] == rule["value"]
    assert off == 7                      # the planner takes the midpoint of the unrounded values


def test_level_v10_keyed_entries_have_no_clash_and_11_end_keys_move():
    moved = 0
    keyed = [entry for entry in level() if not entry["static"]]
    assert len(keyed) == 11
    for entry in keyed:
        frames = round((entry["src_out"] - entry["src_in"]) / (FRAME / TPS))
        got = keys.key_ticks(entry["keys"], length_ticks=frames * FRAME, frame_ticks=FRAME)     # raises on a clash
        assert len(got) == len(entry["keys"])
        moved += sum(k["moved_from"] is not None for k in got)
    assert moved == 11


def test_static_spread_override():
    s07 = next(entry for entry in level() if entry["name"].startswith("S07"))
    assert keys.entry_mode(s07)["mode"] == "keys"
    assert keys.entry_mode(s07, static_spread=keys.STATIC_SPREAD)["mode"] == "keys"        # spread 0.85
    over = keys.entry_mode(s07, static_spread=0.9)
    assert over == {"mode": "static", "value": -3.32, "spread": 0.85, "source": "rule"}
    s10 = next(entry for entry in level() if entry["name"].startswith("S10"))
    assert keys.entry_mode(s10, static_spread=0.7)["mode"] == "keys"                        # spread 0.73
    assert keys.entry_mode(s10, static_spread=0.73)["mode"] == "static"


def test_static_spread_zero_is_a_real_value():
    flat = {"keys": [{"time_seconds": 0.0, "value": 1.0}, {"time_seconds": 1.0, "value": 1.0}]}
    moving = {"keys": [{"time_seconds": 0.0, "value": 1.0}, {"time_seconds": 1.0, "value": 1.01}]}
    assert keys.entry_mode(flat, static_spread=0)["mode"] == "static"
    assert keys.entry_mode(moving, static_spread=0)["mode"] == "keys"
    assert keys.entry_mode(moving)["mode"] == "static"                                       # the default 0.75


def test_no_keys_and_not_static_is_none():
    assert keys.entry_mode({"keys": [], "static": False}) == {"mode": "none", "value": None, "spread": 0.0,
                                                              "source": "file"}
    assert keys.entry_mode({"keys": []}) == {"mode": "none", "value": None, "spread": 0.0, "source": "rule"}
    assert keys.entry_mode({"static": False}) == {"mode": "none", "value": None, "spread": 0.0, "source": "file"}
    assert keys.entry_mode({"keys": [], "static": False}, static_spread=0.5)["mode"] == "none"


def test_static_without_value_is_bad_plan():
    for entry in ({"keys": [], "static": True, "static_value": None}, {"keys": [], "static": True},
                  {"keys": [{"time_seconds": 0, "value": 1.0}], "static": True, "static_value": "1.0"},
                  {"keys": [], "static": True, "static_value": True},
                  {"keys": [], "static": True, "static_value": float("nan")}):
        with pytest.raises(CliError) as err:
            keys.entry_mode(entry)
        assert err.value.code == "bad_plan"


def test_a_static_entry_follows_the_file_unless_a_spread_is_given():
    entry = {"keys": [{"time_seconds": 0.0, "value": 3.0}, {"time_seconds": 2.0, "value": -3.0}],
             "static": True, "static_value": 1.25}
    assert keys.entry_mode(entry) == {"mode": "static", "value": 1.25, "spread": 6.0, "source": "file"}
    assert keys.entry_mode(entry, static_spread=0.75)["mode"] == "keys"                     # the rule, not the file
    only_value = {"keys": [], "static": True, "static_value": 2}
    assert keys.entry_mode(only_value) == {"mode": "static", "value": 2.0, "spread": 0.0, "source": "file"}


@pytest.mark.parametrize("entry", [
    {"keys": "x"}, {"keys": [5]}, {"keys": [{"time_seconds": 0.0}]}, {"keys": [{"time_seconds": 0.0, "value": "1"}]},
    {"keys": [{"time_seconds": 0.0, "value": True}]}, {"static": "yes", "keys": []}, "entry", None])
def test_entry_mode_refuses_a_malformed_entry(entry):
    with pytest.raises(CliError) as err:
        keys.entry_mode(entry)
    assert err.value.code == "bad_plan"


@pytest.mark.parametrize("spread", [-0.1, "0.5", True, float("nan"), float("inf")])
def test_entry_mode_refuses_a_bad_spread(spread):
    with pytest.raises(CliError) as err:
        keys.entry_mode({"keys": []}, static_spread=spread)
    assert err.value.code == "bad_args"


# =====================================================================================================
# the command
# =====================================================================================================
def model_with(rotation, **over) -> KeysModel:
    model = KeysModel()
    model.add_clip("000f4276", name="S02 C2246", rotation=rotation, **over)
    return model


THREE_KEYS = [(0, 1.26), (316757952000, 1.01), (826377552000, 0.07)]


def keys_get(live, capsys, model, *argv, state=None):  # noqa: F811
    """Run `live keys get` against a panel played by `model`; returns (code, payload, panel)."""
    with panel_on(live, handler=recipe_handler(model.replies(), state=state)) as panel:
        code, out = run_json(capsys, ["live", "keys", "get", *argv])
    return code, out, panel


def test_keys_get_shapes_the_reply(live, capsys):
    code, out, panel = keys_get(live, capsys, model_with(THREE_KEYS), *NODE_ARGS)
    assert code == 0 and out.pop("ok") is True
    assert list(out) == ["sequence", "clip", "effect", "property", "is_time_varying", "value", "keys", "warnings"]
    assert out["sequence"] == {"id": "s1", "name": "Main"}
    assert out["clip"] == {"node_id": "000f4276", "name": "S02 C2246", "track_type": "video", "track": 0, "clip": 0,
                           "in_ticks": "0", "out_ticks": str(LENGTH), "speed": 1}
    assert out["effect"] == {"name": "Motion", "effect_index": 1}
    assert out["property"] == {"name": "Rotation", "index": 4}
    assert out["is_time_varying"] is True and out["value"] is None and out["warnings"] == []
    assert out["keys"] == [{"seconds": 0.0, "media_ticks": "0", "value": 1.26},
                           {"seconds": 1.247, "media_ticks": "316757952000", "value": 1.01},
                           {"seconds": 3.25325, "media_ticks": "826377552000", "value": 0.07}]
    assert all(isinstance(k["media_ticks"], str) and list(k) == ["seconds", "media_ticks", "value"] for k in out["keys"])
    assert len(panel.seen) == 2 and "playhead_ticks: playhead" in panel.seen[0]
    sent = panel.seen[1]
    assert f"// {MARK}\n" in sent and FIND_MARK in sent
    assert script_args(sent) == ARGS_BY_NODE
    assert (live / LOCK).exists() is False


def test_keys_get_seconds_count_from_the_in_point(live, capsys):
    in_ticks = 5 * TPS
    shifted = [(in_ticks + t, v) for t, v in THREE_KEYS]
    code, out, _ = keys_get(live, capsys, model_with(shifted, in_ticks=in_ticks), *NODE_ARGS)
    assert code == 0
    assert [k["seconds"] for k in out["keys"]] == [0.0, 1.247, 3.25325]
    assert [k["media_ticks"] for k in out["keys"]] == [str(in_ticks + t) for t, _ in THREE_KEYS]
    assert out["clip"]["in_ticks"] == str(in_ticks) and out["clip"]["out_ticks"] == str(in_ticks + LENGTH)


def test_keys_get_retimed_clip_has_no_seconds(live, capsys):
    code, out, _ = keys_get(live, capsys, model_with(THREE_KEYS, speed=2), *NODE_ARGS)
    assert code == 0 and out["clip"]["speed"] == 2
    assert [k["seconds"] for k in out["keys"]] == [None, None, None]
    assert [k["media_ticks"] for k in out["keys"]] == ["0", "316757952000", "826377552000"]
    assert len(out["warnings"]) == 1 and "speed" in out["warnings"][0] and "media_ticks" in out["warnings"][0]


def test_keys_get_reversed_clip_has_no_seconds(live, capsys):
    code, out, _ = keys_get(live, capsys, model_with(THREE_KEYS, reversed_=True), *NODE_ARGS)
    assert code == 0 and [k["seconds"] for k in out["keys"]] == [None, None, None]
    assert len(out["warnings"]) == 1 and "reversed" in out["warnings"][0]


def test_keys_get_static_property(live, capsys):
    code, out, _ = keys_get(live, capsys, model_with(0.67), *NODE_ARGS)
    assert code == 0 and out["is_time_varying"] is False and out["value"] == 0.67 and out["keys"] == []
    assert out["warnings"] == []


def test_keys_get_an_array_value_passes_through(live, capsys):
    code, out, panel = keys_get(live, capsys, model_with(0.0), *NODE_ARGS, "--property", "Position")
    assert code == 0 and out["value"] == [0.5, 0.5] and out["property"] == {"name": "Position", "index": 0}
    assert script_args(panel.seen[1])["property"] == "Position"


def test_keys_get_by_track_and_clip_and_the_other_options(live, capsys):
    model = model_with(THREE_KEYS)
    code, out, panel = keys_get(live, capsys, model, "--sequence", "s1", "--track", "0", "--clip", "0")
    assert code == 0 and out["clip"]["node_id"] == "000f4276"
    assert script_args(panel.seen[1])["target"] == {"track_type": "video", "track": 0, "clip": 0}
    code, out, panel = keys_get(live, capsys, model, "--sequence", "s1", "--track-type", "video", "--track", "0",
                                "--clip", "0", "--effect", "Motion", "--property", "Rotation", "--property-index", "4")
    assert code == 0 and out["property"]["index"] == 4
    assert script_args(panel.seen[1]) == {"sequence_id": "s1", "target": {"track_type": "video", "track": 0, "clip": 0},
                                          "effect": "Motion", "property": "Rotation", "property_index": 4}
    code, out, panel = keys_get(live, capsys, model, *NODE_ARGS, "--effect", "AE.ADBE Motion", "--property", "Scale")
    assert code == 0 and out["effect"]["name"] == "Motion" and out["property"] == {"name": "Scale", "index": 1}
    assert out["value"] == 100.0


def test_keys_get_audio_track_is_asked_for_as_audio(live, capsys):
    model = KeysModel()
    model.add_clip("000f9999", name="Room", track_type="audio")
    code, out, panel = keys_get(live, capsys, model, "--sequence", "s1", "--track-type", "audio", "--track", "0",
                                "--clip", "0", "--effect", "Opacity", "--property", "Opacity")
    assert code == 0 and out["clip"]["track_type"] == "audio"
    assert script_args(panel.seen[1])["target"] == {"track_type": "audio", "track": 0, "clip": 0}


@pytest.mark.parametrize("argv, text", [
    (["--effect", "Opacity", "--property", "Blend Mode"], "ambiguous_property: 2 properties of Opacity are named Blend Mode"),
    (["--property", "Nope"], "no property named Nope in Motion. Available properties: Position, Scale, Scale Width, "
                             "Uniform Scale, Rotation, Anchor Point."),
    (["--property", "Scale", "--property-index", "4"], "property 4 of Motion is named Rotation, not Scale"),
    (["--property-index", "99"], "Motion has no property 99"),
    (["--effect", "Lumetri Color"], "no effect named Lumetri Color on this clip"),
])
def test_a_refusal_of_the_script_is_a_precondition(live, capsys, argv, text):
    code, out, panel = keys_get(live, capsys, model_with(THREE_KEYS), *NODE_ARGS, *argv)
    assert code == 1 and out["ok"] is False and out["code"] == "precondition" and out["error"].startswith(text)
    assert not (live / LOCK).exists() and len(panel.seen) == 2


@pytest.mark.parametrize("argv, text", [
    (["--sequence", "s9", "--node-id", "000f4276"], "no sequence has the ID s9"),
    (["--sequence", "s1", "--node-id", "zzz"], "no clip has the node ID zzz in sequence Main"),
    (["--sequence", "s1", "--track", "3", "--clip", "0"], "the sequence has no video track 3"),
    (["--sequence", "s1", "--track", "0", "--clip", "4"], "video track 0 has no clip 4"),
])
def test_a_clip_that_is_not_there_is_a_precondition(live, capsys, argv, text):
    code, out, _ = keys_get(live, capsys, model_with(THREE_KEYS), *argv)
    assert code == 1 and out["code"] == "precondition" and out["error"] == text


def test_keys_get_needs_a_sequence(live, capsys):
    with panel_on(live, handler=recipe_handler(model_with(THREE_KEYS).replies())) as panel:
        assert dispatch(["--json", "live", "keys", "get", "--node-id", "000f4276"]) == 2
    err = " ".join(capsys.readouterr().err.split())
    assert "Missing option '--sequence'" in err, err          # click's own usage error: the command ran
    assert panel.seen == [] and not (live / LOCK).exists()


NAME_A_CLIP = "Name the clip with --track and --clip together, or with --node-id."
NODE_ALONE = "--node-id cannot be used with --track, --clip or --track-type."


@pytest.mark.parametrize("argv, text", [
    (["--sequence", "s1"], NAME_A_CLIP), (["--sequence", "s1", "--track", "1"], NAME_A_CLIP),
    (["--sequence", "s1", "--clip", "1"], NAME_A_CLIP), (["--sequence", "s1", "--track-type", "audio"], NAME_A_CLIP),
    (["--sequence", "s1", "--node-id", "x", "--track", "1"], NODE_ALONE),
    (["--sequence", "s1", "--node-id", "x", "--clip", "1"], NODE_ALONE),
    (["--sequence", "s1", "--node-id", "x", "--track-type", "audio"], NODE_ALONE),
    (["--sequence", "s1", "--track", "-1", "--clip", "0"], "Invalid value for '--track'"),
    (["--sequence", "s1", "--track", "0", "--clip", "x"], "Invalid value for '--clip'"),
    (["--sequence", "s1", "--track", "0", "--clip", "0", "--track-type", "sound"], "Invalid value for '--track-type'"),
    (["--sequence", "s1", "--node-id", "x", "--property-index", "-1"], "Invalid value for '--property-index'"),
    (["--sequence", "s1", "--node-id", "x", "--property-index", "two"], "Invalid value for '--property-index'"),
])
def test_keys_get_needs_a_clip_and_nothing_is_sent(live, capsys, argv, text):
    with panel_on(live, handler=recipe_handler(model_with(THREE_KEYS).replies())) as panel:
        assert dispatch(["--json", "live", "keys", "get", *argv]) == 2
    err = " ".join(capsys.readouterr().err.split())
    assert text in err, err
    assert panel.seen == [] and not (live / LOCK).exists()


def test_keys_get_is_a_read(live, capsys, sends):
    # The open project is the owner's own film, not a Claude working copy: a read does not mind.
    code, out, panel = keys_get(live, capsys, model_with(THREE_KEYS), *NODE_ARGS, state=STATE_OWN)
    assert code == 0 and len(out["keys"]) == 3
    assert sends == [False, False] and len(panel.seen) == 2
    assert not (live / LOCK).exists()


GOOD = {
    "sequence": {"id": "s1", "name": "Main"},
    "clip": {"in_ticks": "0", "out_ticks": str(LENGTH), "start_ticks": "0", "end_ticks": str(LENGTH), "speed": 1,
             "reversed": False, "node_id": "000f4276", "name": "S02 C2246", "track_type": "video", "track": 0, "clip": 0},
    "effect": {"name": "Motion", "effect_index": 1},
    "property": {"name": "Rotation", "index": 4},
    "read": {"is_time_varying": True, "keys": [{"ticks": "0", "value": 1.26}], "value": None},
}


DROP = object()


def reply_with(**change) -> dict:
    out = json.loads(json.dumps(GOOD))
    for path, value in change.items():
        node = out
        parts = path.split("__")
        for part in parts[:-1]:
            node = node[part]
        if value is DROP:
            del node[parts[-1]]
        else:
            node[parts[-1]] = value
    return out


def get_with_reply(live, capsys, reply):  # noqa: F811
    with panel_on(live, handler=recipe_handler({MARK: reply})):
        return run_json(capsys, ["live", "keys", "get", *NODE_ARGS])


def test_the_good_reply_is_accepted(live, capsys):
    code, out = get_with_reply(live, capsys, GOOD)
    assert code == 0 and out["keys"] == [{"seconds": 0.0, "media_ticks": "0", "value": 1.26}]


@pytest.mark.parametrize("change", [
    {"read": DROP}, {"read": []}, {"read__keys": "x"}, {"read__keys": [5]}, {"read__keys": [{"ticks": "abc", "value": 1}]},
    {"read__keys": [{"ticks": "1.5", "value": 1}]}, {"read__keys": [{"value": 1.0}]}, {"read__is_time_varying": "yes"},
    {"read__is_time_varying": DROP}, {"clip": DROP}, {"clip": "x"}, {"clip__in_ticks": "soon"}, {"clip__speed": "fast"},
    {"clip__node_id": 7}, {"sequence": DROP}, {"effect": DROP}, {"property": "Rotation"}, {"effect__effect_index": "1"},
    {"property__index": -1}, {"read__keys": [{"ticks": "0", "value": "x"}]}])
def test_a_reply_in_the_wrong_shape_is_bad_reply(live, capsys, change):
    code, out = get_with_reply(live, capsys, reply_with(**change))
    assert code == 1 and out["code"] == "bad_reply" and out["ok"] is False


def test_numbers_for_ticks_come_out_as_strings(live, capsys):
    reply = reply_with(read__keys=[{"ticks": 316757952000, "value": 1.0}], clip__in_ticks=0)
    code, out = get_with_reply(live, capsys, reply)
    assert code == 0 and out["keys"][0]["media_ticks"] == "316757952000" and out["clip"]["in_ticks"] == "0"
    assert out["keys"][0]["seconds"] == 1.247


def test_an_unreadable_in_point_gives_no_seconds(live, capsys):
    code, out = get_with_reply(live, capsys, reply_with(clip__in_ticks=None))
    assert code == 0 and out["keys"][0]["seconds"] is None and out["clip"]["in_ticks"] is None
    assert len(out["warnings"]) == 1


def test_an_unreadable_speed_gives_no_seconds(live, capsys):
    code, out = get_with_reply(live, capsys, reply_with(clip__speed=None, clip__reversed=None))
    assert code == 0 and out["keys"][0]["seconds"] is None and out["clip"]["speed"] is None
    assert len(out["warnings"]) == 1


GOOD_TARGET = {"node_id": "000f4276"}


@pytest.mark.parametrize("kwargs", [
    {"sequence_id": ""}, {"sequence_id": None}, {"sequence_id": 7},
    {"target": "000f4276"}, {"target": None}, {"target": {}}, {"target": {"node_id": ""}},
    {"target": {"node_id": "a", "track": 0}}, {"target": {"track": 0}}, {"target": {"track": -1, "clip": 0}},
    {"target": {"track": 0, "clip": 0, "track_type": "both"}},
    {"effect": ""}, {"effect": None}, {"effect": 5}, {"prop": ""}, {"prop": None}, {"prop": 3},
    {"property_index": -1}, {"property_index": "1"}, {"property_index": True}, {"property_index": 1.5}])
def test_get_keys_refuses_bad_arguments_before_sending(live, kwargs):
    client = BridgeClient(live, 0.1)
    with panel_on(live, handler=recipe_handler(model_with(THREE_KEYS).replies())) as panel:
        with pytest.raises(CliError) as err:
            keys.get_keys(client, **{"target": GOOD_TARGET, "sequence_id": "s1", **kwargs})
    assert err.value.code == "bad_args" and err.value.message.endswith("Nothing was sent.")
    assert panel.seen == [] and not (live / LOCK).exists()


def test_get_keys_defaults_are_motion_and_rotation(live):
    client = BridgeClient(live, 0.1)
    with panel_on(live, handler=recipe_handler(model_with(THREE_KEYS).replies())) as panel:
        payload = keys.get_keys(client, GOOD_TARGET, sequence_id="s1")
    assert (payload["effect"]["name"], payload["property"]["name"]) == ("Motion", "Rotation")
    assert script_args(panel.seen[1]) == ARGS_BY_NODE


def test_the_script_includes_are_find_values_and_keys():
    assert keys.SCRIPT_INCLUDES == ("find", "values", "keys")


# ---------- the text ----------
def human(live, capsys, model, *argv) -> tuple[int, str, str]:  # noqa: F811
    with panel_on(live, handler=recipe_handler(model.replies())):
        code = dispatch(["live", "keys", "get", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_human_output_for_keys(live, capsys):
    code, out, _ = human(live, capsys, model_with(THREE_KEYS), *NODE_ARGS)
    assert code == 0
    assert out.splitlines() == ["Motion.Rotation of 000f4276 (S02 C2246): 3 keys",
                                "  0.000 s  0  1.26",
                                "  1.247 s  316757952000  1.01",
                                "  3.253 s  826377552000  0.07"]


def test_human_output_for_one_key_and_none(live, capsys):
    code, out, _ = human(live, capsys, model_with([(0, 1.0)]), *NODE_ARGS)
    assert out.splitlines()[0] == "Motion.Rotation of 000f4276 (S02 C2246): 1 key"
    reply = reply_with(read__keys=[])
    with panel_on(live, handler=recipe_handler({MARK: reply})):
        assert dispatch(["live", "keys", "get", *NODE_ARGS]) == 0
    assert capsys.readouterr().out.splitlines() == ["Motion.Rotation of 000f4276 (S02 C2246): 0 keys"]


def test_human_output_for_a_static_value(live, capsys):
    code, out, _ = human(live, capsys, model_with(0.67), *NODE_ARGS)
    assert code == 0 and out.splitlines() == ["Motion.Rotation of 000f4276 (S02 C2246): static 0.67"]
    code, out, _ = human(live, capsys, model_with(0.0), *NODE_ARGS, "--property", "Position")
    assert out.splitlines() == ["Motion.Position of 000f4276 (S02 C2246): static [0.5, 0.5]"]


def test_human_output_for_a_retimed_clip_and_its_warning(live, capsys):
    code, out, _ = human(live, capsys, model_with(THREE_KEYS, speed=2), *NODE_ARGS)
    lines = out.splitlines()
    assert lines[1] == "  ? s  0  1.26" and lines[2] == "  ? s  316757952000  1.01"
    assert lines[-1].startswith("Warning: ") and "speed" in lines[-1]


def test_the_help_says_it_only_reads(capsys):
    assert dispatch(["live", "keys", "get", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "only reads" in text.lower()
    for word in ("--sequence", "--node-id", "--track", "--clip", "--track-type", "--effect", "--property",
                 "--property-index", "Motion", "Rotation", "live clip list"):
        assert word in text, word
    assert dispatch(["live", "--help"]) == 0
    assert "keys" in capsys.readouterr().out
    assert dispatch(["live", "keys", "--help"]) == 0
    assert "get" in capsys.readouterr().out


# =====================================================================================================
# the scripts, in node with a stub app
# =====================================================================================================
INCLUDES = keys.SCRIPT_INCLUDES
IN = 5 * TPS
KEY_A, KEY_B = str(IN), str(IN + 316757952000)
ROT_KEYS = [(KEY_A, 1.26), (KEY_B, 1.01)]

KEYS_PRELUDE = STUB_HELPERS + """
var writes = 0;                 // every call that would change something in Premiere
function Time() { this.ticks = "0"; }
function mkProp(name, o) {
  o = o || {};
  var p = {displayName: name};
  p.isTimeVarying = function () { if (o.varyingThrows) throw new Error("no answer"); return !!o.keys; };
  p.getKeys = function () {
    if (o.keysAs === "zero") return 0;
    if (o.keysAs === "undefined") return undefined;
    if (o.keysAs === "null") return null;
    var out = [];
    for (var i = 0; i < o.keys.length; i++) {
      var t = new Time();
      t.ticks = o.keys[i][0];
      if (o.badTick) t.ticks = "soon";
      out.push(t);
    }
    return out;
  };
  p.getValueAtKey = function (t) {
    if (o.valueThrows) throw new Error("no value");
    for (var i = 0; i < o.keys.length; i++) if (String(t.ticks) === o.keys[i][0]) return o.keys[i][1];
    return null;
  };
  p.getValue = function () { return o.value; };
  if (o.color) p.getColorValue = function () { return o.color; };
  p.setValue = p.setValueAtKey = p.addKey = p.removeKey = p.setTimeVarying = p.setInterpolationTypeAtKey = function () { writes++; };
  return p;
}
function mkComp(display, match, props) { return {displayName: display, matchName: match, properties: coll("numItems", props)}; }
function mkClip(id, name, comps, o) {
  o = o || {};
  var c = {nodeId: id, name: name, inPoint: {ticks: o.inT}, outPoint: {ticks: o.outT}, start: {ticks: "0"},
           end: {ticks: o.endT}, components: coll("numItems", comps)};
  c.getSpeed = function () { if (o.speedThrows) throw new Error("no speed"); return o.speed === undefined ? 1 : o.speed; };
  c.isSpeedReversed = function () { if (o.reverseThrows) throw new Error("no state"); return !!o.reversed; };
  return c;
}
var rotation = mkProp("Rotation", {keys: %(rot)s});
var motion = mkComp("Motion", "AE.ADBE Motion", [mkProp("Position", {value: [0.5, 0.5]}), mkProp("Scale", {value: 100}), rotation]);
var opacity = mkComp("Opacity", "AE.ADBE Opacity", [mkProp("Opacity", {value: 100}), mkProp("Blend Mode", {value: 0}),
                                                    mkProp("Blend Mode", {value: 0})]);
var graded = mkClip("N1", "Graded", [opacity, motion], {inT: "%(in)s", outT: "%(out)s", endT: "%(len)s"});
var otherMotion = mkComp("Motion", "AE.ADBE Motion", [mkProp("Position", {value: [0.5, 0.5]}), mkProp("Scale", {value: 77}),
                                                      mkProp("Rotation", {value: 9})]);
var other = mkClip("N1", "Other clip", [mkComp("Opacity", "AE.ADBE Opacity", []), otherMotion], {inT: "0", outT: "10", endT: "10"});
var seqA = {sequenceID: "S-A", name: "Cut A", videoTracks: coll("numTracks", [track([graded])]), audioTracks: coll("numTracks", [])};
var seqB = {sequenceID: "S-B", name: "Cut B", videoTracks: coll("numTracks", [track([other])]), audioTracks: coll("numTracks", [])};
var app = {project: {path: "/p/X Claude.prproj", sequences: coll("numSequences", [seqA, seqB]), activeSequence: seqB}};
""" % {"rot": json.dumps(ROT_KEYS), "in": str(IN), "out": str(IN + LENGTH), "len": str(LENGTH)}


def run_script(body: str, args: dict | None = None, prelude: str = KEYS_PRELUDE, include=INCLUDES) -> tuple[dict, int]:
    """Run a built script in node. Returns (the reply, how many calls would have changed Premiere)."""
    script = jsx.build(body, args, include=include)
    code = (prelude + "\nvar __out = " + script
            + ";\nprocess.stdout.write(JSON.stringify({reply: JSON.parse(__out), writes: writes}));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout
    found = json.loads(out)
    return found["reply"], found["writes"]


def get_script(**over) -> tuple[dict, int]:
    args = {"sequence_id": "S-A", "target": {"node_id": "N1"}, "effect": "Motion", "property": "Rotation",
            "property_index": None, **over}
    return run_script(jsx.load("keys_get"), args)


def shape(value):
    """The kinds of a reply, to compare two replies that hold other values."""
    if isinstance(value, dict):
        return {key: shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return "number" if isinstance(value, (int, float)) and not isinstance(value, bool) else type(value).__name__


@needs_node
def test_keys_get_script_in_node():
    reply, writes = get_script()
    assert reply["success"] is True and writes == 0
    assert reply["data"] == {
        "sequence": {"id": "S-A", "name": "Cut A"},
        "clip": {"in_ticks": str(IN), "out_ticks": str(IN + LENGTH), "start_ticks": "0", "end_ticks": str(LENGTH),
                 "speed": 1, "reversed": False, "node_id": "N1", "name": "Graded", "track_type": "video", "track": 0,
                 "clip": 0},
        "effect": {"name": "Motion", "effect_index": 1},
        "property": {"name": "Rotation", "index": 2},
        "read": {"is_time_varying": True, "keys": [{"ticks": KEY_A, "value": 1.26}, {"ticks": KEY_B, "value": 1.01}],
                 "value": None}}


@needs_node
def test_keys_get_script_finds_the_property_by_name_and_by_index():
    by_name, _ = get_script()
    by_index, _ = get_script(property_index=2)
    assert by_index == by_name
    reply, _ = get_script(property_index=1)
    assert refused(reply) == "property 1 of Motion is named Scale, not Rotation"
    reply, _ = get_script(property_index=3)
    assert refused(reply) == "Motion has no property 3"
    reply, _ = get_script(property="Nope")
    assert refused(reply) == "no property named Nope in Motion. Available properties: Position, Scale, Rotation."


@needs_node
def test_keys_get_script_reads_a_static_property():
    reply, writes = get_script(property="Scale")
    assert reply["data"]["read"] == {"is_time_varying": False, "keys": [], "value": 100} and writes == 0
    assert reply["data"]["property"] == {"name": "Scale", "index": 1}
    reply, _ = get_script(property="Position")
    assert reply["data"]["read"]["value"] == [0.5, 0.5]


@needs_node
def test_keys_get_script_two_blend_modes_are_ambiguous():
    reply, _ = get_script(effect="Opacity", property="Blend Mode")
    assert refused(reply) == "ambiguous_property: 2 properties of Opacity are named Blend Mode; pass property_index"
    reply, _ = get_script(effect="Opacity", property="Blend Mode", property_index=2)
    assert reply["data"]["property"] == {"name": "Blend Mode", "index": 2}
    reply, _ = get_script(effect="Opacity", property="Opacity")
    assert reply["data"]["read"]["value"] == 100


@needs_node
def test_keys_get_script_looks_in_the_given_sequence_not_the_active_one():
    # Both sequences hold a clip with the node ID N1; the active one is S-B.
    in_a, _ = get_script(sequence_id="S-A")
    in_b, _ = get_script(sequence_id="S-B")
    assert in_a["data"]["clip"]["name"] == "Graded" and in_b["data"]["clip"]["name"] == "Other clip"
    assert in_b["data"]["read"] == {"is_time_varying": False, "keys": [], "value": 9}
    assert refused(get_script(sequence_id="S-Z")[0]) == "no sequence has the ID S-Z"
    assert refused(get_script(sequence_id=None)[0]) == "the script got no sequence ID"       # no active-sequence fallback
    assert refused(get_script(target={"node_id": "N9"})[0]) == "no clip has the node ID N9 in sequence Cut A"
    by_place = get_script(target={"track": 0, "clip": 0})[0]
    assert by_place["data"]["clip"]["node_id"] == "N1"
    assert refused(get_script(target={"track": 0, "clip": 1})[0]) == "video track 0 has no clip 1"


@needs_node
def test_keys_get_script_refusals_about_the_effect():
    assert refused(get_script(effect="Lumetri Color")[0]) == "no effect named Lumetri Color on this clip"
    assert get_script(effect="AE.ADBE Motion")[0]["data"]["effect"] == {"name": "Motion", "effect_index": 1}


SCRIPT_ARGS = {"sequence_id": "S-A", "target": {"node_id": "N1"}, "effect": "Motion", "property": "Rotation",
               "property_index": None}


@needs_node
def test_keys_get_script_empty_key_lists_and_unreadable_values():
    for answer in ("0", "undefined"):          # a host that has keys and says 0 or nothing: an empty list
        reply, _ = run_script(jsx.load("keys_get"), SCRIPT_ARGS,
                              KEYS_PRELUDE + "rotation.getKeys = function () { return %s; };" % answer)
        assert reply["data"]["read"] == {"is_time_varying": True, "keys": [], "value": None}, answer
    prelude = KEYS_PRELUDE + 'rotation.getValueAtKey = function () { throw new Error("no value"); };'
    reply, _ = run_script(jsx.load("keys_get"), SCRIPT_ARGS, prelude)
    assert [k["value"] for k in reply["data"]["read"]["keys"]] == [None, None]
    assert [k["ticks"] for k in reply["data"]["read"]["keys"]] == [KEY_A, KEY_B]
    prelude = KEYS_PRELUDE + "rotation.getValueAtKey = function () { return [0.1, 0.2]; };"
    assert run_script(jsx.load("keys_get"), SCRIPT_ARGS, prelude)[0]["data"]["read"]["keys"][0]["value"] == [0.1, 0.2]
    prelude = KEYS_PRELUDE + "rotation.getValueAtKey = function () { return NaN; };"
    assert run_script(jsx.load("keys_get"), SCRIPT_ARGS, prelude)[0]["data"]["read"]["keys"][0]["value"] is None


@needs_node
def test_keys_get_script_refuses_what_it_cannot_read_truthfully():
    args = SCRIPT_ARGS
    cases = {
        "rotation.isTimeVarying = function () { throw new Error('no'); };": "Premiere would not say",
        "rotation.getKeys = function () { return null; };": "the key list could not be read",
        "rotation.getKeys = function () { return {length: 1, 0: {ticks: 'soon'}}; };": "a key time could not be read",
        "rotation.getColorValue = function () { return [255, 1, 2, 3]; };": "keyframed colour",
    }
    for patch, text in cases.items():
        reply, writes = run_script(jsx.load("keys_get"), args, KEYS_PRELUDE + patch)
        assert refused(reply).startswith(text), (patch, reply)
        assert writes == 0


@needs_node
def test_keys_get_script_reports_speed_and_reverse_as_the_server_reads_them():
    patches = {
        "": (1, False),
        "graded.getSpeed = function () { return 2; };": (2, False),
        "graded.isSpeedReversed = function () { return 1; };": (1, True),
        "graded.isSpeedReversed = function () { return 0; };": (1, False),
        "graded.getSpeed = function () { throw new Error('no'); };": (None, False),
        "graded.getSpeed = function () { return NaN; };": (None, False),
        "graded.isSpeedReversed = function () { throw new Error('no'); };": (1, None),
        "graded.isSpeedReversed = function () { return 'maybe'; };": (1, None),
    }
    args = SCRIPT_ARGS
    for patch, (speed, reversed_) in patches.items():
        reply, _ = run_script(jsx.load("keys_get"), args, KEYS_PRELUDE + patch)
        clip = reply["data"]["clip"]
        assert (clip["speed"], clip["reversed"]) == (speed, reversed_), patch


@needs_node
def test_keys_get_script_a_clip_time_that_does_not_read_is_null():
    prelude = KEYS_PRELUDE + "graded.inPoint = null; graded.end = {ticks: 'x'};"
    reply, _ = run_script(jsx.load("keys_get"), SCRIPT_ARGS, prelude)
    clip = reply["data"]["clip"]
    assert clip["in_ticks"] is None and clip["end_ticks"] is None and clip["out_ticks"] == str(IN + LENGTH)


@needs_node
def test_keys_get_script_changes_nothing():
    for kwargs in ({}, {"property": "Scale"}, {"effect": "Opacity", "property": "Blend Mode", "property_index": 1}):
        reply, writes = get_script(**kwargs)
        assert reply["success"] is True and writes == 0


@needs_node
def test_keys_get_script_matches_the_shape_of_keys_model():
    model = KeysModel()
    model.add_clip("N1", name="Graded", in_ticks=IN, out_ticks=IN + LENGTH, start_ticks=0, end_ticks=LENGTH,
                   rotation=[(int(t), v) for t, v in ROT_KEYS])
    model.sequence = {"id": "S-A", "name": "Cut A"}

    def modelled(over: dict):
        return model.keys_get(jsx.build(jsx.load("keys_get"), {**SCRIPT_ARGS, **over}, include=INCLUDES))

    # the same reply: the same keys, the same kinds of value (the model's Motion has more properties, so the
    # property index is not compared by value)
    for over in ({}, {"property": "Scale"}, {"property": "Position"}, {"effect": "Opacity", "property": "Opacity"},
                 {"target": {"track": 0, "clip": 0}}):
        node_reply, _ = get_script(**over)
        assert node_reply["success"] is True, over
        assert shape(node_reply["data"]) == shape(modelled(over)), over
    # the same refusals, word for word
    for over in ({"effect": "Opacity", "property": "Blend Mode"}, {"property": "Nope"}, {"effect": "Lumetri Color"},
                 {"sequence_id": "S-Z"}, {"target": {"node_id": "N9"}}, {"sequence_id": None},
                 {"target": {"track": 0, "clip": 1}}, {"property": "Scale", "property_index": 0}):
        node_reply, _ = get_script(**over)
        said = modelled(over)
        assert isinstance(said, Exception) and node_reply["success"] is False, over
        if over == {"property": "Nope"}:          # the model's Motion has more properties, so its list is longer
            head = "Error: precondition: no property named Nope in Motion. Available properties: Position, Scale, "
            assert str(said).startswith(head) and node_reply["error"].startswith(head)
            continue
        assert str(said) == node_reply["error"], over


@needs_node
def test_keys_helpers_in_node():
    body = ("var t = __keyTime(ARGS.ticks);\n"
            "var u = __keyTime(254016000000);\n"
            "return __ok([typeof t.ticks, t.ticks, t instanceof Time, u.ticks]);")
    reply, writes = run_script(body, {"ticks": "1586837952000"}, KEYS_PRELUDE)
    assert reply["data"] == ["string", "1586837952000", True, "254016000000"] and writes == 0
    body = ("var found = __propertyNamed(__clip(__sequenceById('S-A'), {node_id: 'N1'}).clip.components[1], ARGS.name);\n"
            "return __ok([found.index, found.property.displayName]);")
    assert run_script(body, {"name": "Scale"}, KEYS_PRELUDE)[0]["data"] == [1, "Scale"]
    assert refused(run_script(body, {"name": ""}, KEYS_PRELUDE)[0]) == ("no property named  in Motion. Available "
                                                                        "properties: Position, Scale, Rotation.")


@needs_node
def test_keys_scripts_run_nothing_when_they_load():
    body = "return __ok([typeof __keyTime, typeof __propertyNamed, typeof __readKeys, typeof __clipFacts]);"
    reply, writes = run_script(body, None, "var writes = 0;")        # no app, no Time: only definitions
    assert reply["data"] == ["function"] * 4 and writes == 0


def test_keys_get_starts_with_its_marker_and_keys_jsx_has_none():
    assert jsx.load("keys_get").split("\n")[0] == "// recipe: keys_get"
    assert "recipe:" not in jsx.load("keys")                     # a handler matches by substring over the whole script
    assert jsx.load("keys").split("\n")[0].startswith("// keys.jsx")


def test_keys_scripts_are_es3():
    for name in ("keys", "keys_get"):
        text = jsx.load(name)
        for pattern in BANNED + [r"\.indexOf\(", r"\bObject\.(?!prototype\.toString\.call)", r"\.reduce\(", r"\.some\(",
                                 r"\.every\("]:
            assert not re.search(pattern, text), f"{name}.jsx: {pattern}"


def test_keys_get_script_calls_no_writer_of_premiere():
    # keys.jsx will also hold the write helpers of the later tasks, so only the read script is lint-checked here;
    # the node tests count the calls of every writer while keys_get runs.
    text = jsx.load("keys_get")
    for writer in (r"\.addKey\(", r"\.setValue\(", r"\.setValueAtKey\(", r"\.removeKey\(", r"\.setTimeVarying\(",
                   r"\.setInterpolationTypeAtKey\(", r"\.setColorValue\(", r"__keyTime\("):
        assert not re.search(writer, text), f"keys_get.jsx: {writer}"


def head_comment(name: str) -> str:
    """The comment lines at the top of a script, before its first statement."""
    lines = []
    for line in jsx.load(name).split("\n"):
        if not line.startswith("//"):
            break
        lines.append(line)
    return "\n".join(lines)


def test_every_host_call_in_the_keys_scripts_is_cited_in_the_head_comment():
    head = head_comment("keys")
    for call in ("new Time", "isTimeVarying", "getKeys", "getValueAtKey", "getValue", "getSpeed", "isSpeedReversed",
                 "inPoint", "outPoint"):
        assert call in head, f"keys.jsx: the head comment does not cite {call}"
    assert "script-builder.js, lines 1983-1987" in head and "keyframes.js, line 320" in head
    assert "keyframes.js, line 329" in head and "keyframes.js, line 335" in head
    assert "script-builder.js, lines 1922-1943" in head
    head = head_comment("keys_get")
    for name in ("find.jsx", "keys.jsx", "values.jsx", "ARGS", "Reply"):
        assert name in head, f"keys_get.jsx: the head comment does not name {name}"


# =====================================================================================================
# the test data
# =====================================================================================================
def test_test_data_has_no_drive_path():
    files = [path for path in DATA.rglob("*") if path.is_file()]
    assert files, "tests/data holds no file"
    for path in files:
        raw = path.read_bytes()
        assert b"/Volumes" not in raw and b"/Users/" not in raw and b":\\" not in raw, path.name


def test_level_v10_holds_24_entries_in_the_planner_format():
    data = level()
    assert len(data) == 24
    for entry in data:
        assert {"node", "src_in", "src_out", "name", "scale", "max_deg", "clamped", "keys"} <= set(entry)
        assert all(set(key) == {"time_seconds", "value"} for key in entry["keys"])
        assert all(math.isfinite(key["time_seconds"]) and key["time_seconds"] >= 0 for key in entry["keys"])


# =====================================================================================================
# Task 4: live keys set
# =====================================================================================================
from cli_anything.premiere.core import bridge  # noqa: E402
from cli_anything.premiere.core.recipes import results  # noqa: E402
from cli_anything.premiere.core.session import WriteSession  # noqa: E402
from cli_anything.premiere.tests.fakes import INTERPRETED_FPS, float32  # noqa: E402

RECORD_KEYS = {"entry", "name", "target", "status", "skip", "reason_code", "reason", "now", "error", "warnings",
               "mode", "moved", "interpreted"}
CHECK_MARK, WRITE_MARK = "recipe: keys_check", "recipe: keys_write"
RESOLVE_MARK, EFFECT_WRITE_MARK = "recipe: effect_resolve", "recipe: effect_write"


def test_key_ticks_half_frame_line_to_the_tick():
    # Ruling 3: the line is the end minus half a frame, 831674844000 ticks for this clip.
    assert LENGTH - FRAME // 2 == 831674844000
    below = kt((831674843999 / TICKS_PER_SECOND, 0.5))[0]
    assert below["moved_from"] is None and below["ticks"] == 831674843999
    on = kt((831674844000 / TICKS_PER_SECOND, 0.5))[0]
    assert on["moved_from"] == 831674844000 / TICKS_PER_SECOND and on["ticks"] == LENGTH - FRAME


def frames_of(entry: dict) -> int:
    return round((entry["src_out"] - entry["src_in"]) / (FRAME / TPS))


def level_model(data: list[dict] | None = None) -> KeysModel:
    """A model with one clip per plan entry, in plan order on V1, in and out where the plan says."""
    model = KeysModel()
    start = 0
    for entry in level() if data is None else data:
        length = frames_of(entry) * FRAME
        model.add_clip(entry["node"], name=entry["name"], in_ticks=round(entry["src_in"] * TPS), start_ticks=start,
                       length_ticks=length)
        start += length
    return model


def plan_file(tmp_path, data) -> str:
    path = tmp_path / "level.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(path)


def keys_set(live, capsys, tmp_path, model, data=None, *argv, state=None):  # noqa: F811
    """Run `live keys set --sequence s1 --from <plan>` against a panel played by `model`; returns (code,
    payload, panel)."""
    path = plan_file(tmp_path, level() if data is None else data)
    with panel_on(live, handler=recipe_handler(model.replies(), state=state)) as panel:
        code, out = run_json(capsys, ["live", "keys", "set", "--sequence", "s1", "--from", path, *argv])
    return code, out, panel


def seen(panel, mark: str) -> list[dict]:
    return [script_args(text) for text in panel.seen if mark in text]


def rotation_keys(model, node: str) -> list | None:
    return model.rotation(node)["keys"]


def by_name(out: dict, prefix: str) -> dict:
    return next(r for r in out["results"] if r["name"].startswith(prefix))


def test_set_constants_and_names():
    assert keys.SLOW_MOTION_PROVEN is True                         # measures.md, M6
    assert (keys.MOTION, keys.SCALE, keys.ROTATION) == ("Motion", (1, "Scale"), (4, "Rotation"))
    assert keys.LEVEL_KEYS == ("node", "src_in", "src_out", "name", "scale", "max_deg", "clamped", "keys",
                               "spread_deg", "static", "static_value")


def test_level_v10_dry_run(live, capsys, tmp_path, sends):
    model = level_model()
    code, out, panel = keys_set(live, capsys, tmp_path, model, None, "--dry-run")
    assert code == 0 and out["ok"] is True and out["dry_run"] is True
    assert model.writes == [] and not any(sends)
    planned = out["planned"]
    assert len(planned) == 24
    assert [p["mode"] for p in planned].count("keys") == 11 and [p["mode"] for p in planned].count("static") == 13
    s02 = planned[0]
    assert s02["mode"] == "keys" and len(s02["keys"]) == 4
    end = s02["keys"][-1]
    assert end["moved_from"] == 3.295 and end["seconds"] == 3.25325
    assert end["ticks"] == str(LENGTH - FRAME) and end["media_ticks"] == str(LENGTH - FRAME)  # S02 starts at 0
    assert s02["scale"] == {"now": 100.0, "new": 104.4}
    s11 = by_name({"results": planned}, "S11")
    assert s11["mode"] == "static" and s11["static_value"] == -2.15 and s11["keys"] == []
    assert all(r["status"] == "not_run" for r in out["results"])
    assert seen(panel, WRITE_MARK) == [] and seen(panel, EFFECT_WRITE_MARK) == []


def test_level_v10_run(live, capsys, tmp_path):
    model = level_model()
    code, out, panel = keys_set(live, capsys, tmp_path, model)
    assert code == 0 and out["ok"] is True and out["stored"] is True and out["rendered"] is None
    assert out["counts"] == {"done": 24, "skipped": 0, "refused": 0, "failed": 0, "not_run": 0}
    assert out["first_not_run"] is None
    data = level()
    written = 0
    for entry, record in zip(data, out["results"]):
        assert set(record) == RECORD_KEYS, record
        assert record["status"] == "done" and record["interpreted"] is False
        assert model.scale(entry["node"])["value"] == float32(entry["scale"])
        assert record["now"]["scale"] == float32(entry["scale"])
        stored = rotation_keys(model, entry["node"])
        if entry["static"]:
            assert stored is None and record["mode"] == "static"
            assert record["now"]["rotation"]["is_time_varying"] is False
        else:
            assert len(stored) == len(entry["keys"]) and record["mode"] == "keys"
            written += len(stored)
            assert len(record["moved"]) == 1
    assert written == 60
    s11 = by_name(out, "S11")
    assert s11["now"]["rotation"]["value"] == float32(-2.15)
    assert abs(model.rotation(s11["target"]["node_id"])["value"] - (-2.15)) <= keys.KEY_TOLERANCE
    s02 = by_name(out, "S02")
    assert s02["moved"] == [{"from_seconds": 3.295, "to_seconds": 3.25325}]
    assert [k["seconds"] for k in s02["now"]["rotation"]["keys"]][-1] == 3.25325


def test_rerun_is_safe(live, capsys, tmp_path):
    # D1: a re-run of a finished plan writes nothing. Every entry is nothing to do with keys_match.
    model = level_model()
    code, first, _ = keys_set(live, capsys, tmp_path, model)
    assert code == 0
    snapshot = {node: (list(c["effects"][1]["properties"][4]["keys"] or []), c["effects"][1]["properties"][4]["value"])
                for node, c in model.clips.items()}
    writes = list(model.writes)
    code, out, panel = keys_set(live, capsys, tmp_path, model)
    assert code == 0 and out["stored"] is True
    assert out["counts"] == {"done": 0, "skipped": 24, "refused": 0, "failed": 0, "not_run": 0}
    assert {(r["skip"], r["reason_code"]) for r in out["results"]} == {("nothing_to_do", "keys_match")}
    assert model.writes == writes
    assert seen(panel, WRITE_MARK) == [] and seen(panel, EFFECT_WRITE_MARK) == [] and seen(panel, RESOLVE_MARK) == []
    again = {node: (list(c["effects"][1]["properties"][4]["keys"] or []), c["effects"][1]["properties"][4]["value"])
             for node, c in model.clips.items()}
    assert again == snapshot


def test_rerun_dry_run_says_keys_match(live, capsys, tmp_path):
    data = level()[:2]
    model = level_model(data)
    assert keys_set(live, capsys, tmp_path, model, data)[0] == 0
    code, out, _ = keys_set(live, capsys, tmp_path, model, data, "--dry-run")
    assert code == 0 and [p["skip"] for p in out["planned"]] == [keys.MATCH_TEXT] * 2
    assert all(p["scale"]["new"] is None for p in out["planned"])
    code, text, _err = human_set(live, capsys, tmp_path, model, data, "--dry-run")
    assert code == 0 and "nothing to do" in text


def test_rerun_with_one_part_changed_writes_that_entry(live, capsys, tmp_path):
    data = level()[:3]
    model = level_model(data)
    assert keys_set(live, capsys, tmp_path, model, data)[0] == 0
    first_key = model.rotation(data[0]["node"])["keys"][0]
    model.rotation(data[0]["node"])["keys"][0] = (first_key[0], first_key[1] + 0.01)    # a key value off
    model.scale(data[1]["node"])["value"] = float32(data[1]["scale"] + 0.5)             # Scale off
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 0 and [r["status"] for r in out["results"]] == ["done", "done", "skipped"]
    assert out["results"][2]["reason_code"] == "keys_match"
    # --no-scale: Scale is not compared, so only Rotation counts.
    model.scale(data[1]["node"])["value"] = 100.0
    code, out, _ = keys_set(live, capsys, tmp_path, model, data, "--no-scale")
    assert code == 0 and [r["reason_code"] for r in out["results"]] == ["keys_match"] * 3


def test_breathe_then_check_again_finds_keys_match(live, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "MAX_HOLD_S", 0.0)
    data = level()[:2]
    model = level_model(data)
    assert keys_set(live, capsys, tmp_path, model, data)[0] == 0
    for entry in data:                                       # both differ at the first check
        prop = model.rotation(entry["node"])
        prop["keys"][0] = (prop["keys"][0][0], prop["keys"][0][1] + 0.01)
    node = data[1]["node"]
    good = model.rotation(node)["keys"][0][1] - 0.01

    def put_back():
        prop = model.rotation(node)
        prop["keys"][0] = (prop["keys"][0][0], good)
    model.on_check[model.checks + 2] = put_back              # the check again before entry 1
    writes = len(model.writes)
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 0 and [r["status"] for r in out["results"]] == ["done", "skipped"]
    assert out["results"][1]["reason_code"] == "keys_match"
    assert node not in {w[1] for w in model.writes[writes:]}


def _check_record(*, keyframed=True, keys_read=None, value=None, scale_value=None, scale_keyframed=False) -> dict:
    return {"found": True, "item_fps": 23.976023976024, "interpreted": False,
            "clip": {"in_ticks": "0", "out_ticks": str(LENGTH), "start_ticks": "0", "end_ticks": str(LENGTH),
                     "speed": 1, "reversed": False, "track_type": "video"},
            "motion": {"effect_index": 1,
                       "scale": {"name": "Scale", "matches": True, "value": scale_value, "keyframed": scale_keyframed},
                       "rotation": {"name": "Rotation", "keyframed": keyframed, "keys": keys_read, "value": value}}}


MATCH_ENTRY = {"node": "n1", "src_in": 0.0, "src_out": LENGTH / TPS, "scale": 104.4, "static": False,
               "keys": [{"time_seconds": 0.0, "value": 1.26}, {"time_seconds": 1.0, "value": 1.01}]}
MATCH_KEYS = [{"ticks": "0", "value": float32(1.26)}, {"ticks": str(TPS), "value": float32(1.01)}]


def _match(entry=None, *, scale=True, **record) -> dict:
    entry = dict(MATCH_ENTRY if entry is None else entry)
    record = {"keys_read": MATCH_KEYS, "scale_value": float32(104.4), **record}
    return keys._plan_entry(0, entry, keys.entry_mode(entry), _check_record(**record), FRAME, scale=scale)


def test_plan_entry_keys_match():
    check = _match()
    assert check["skip"] == ("keys_match", keys.MATCH_TEXT) and check["refusal"] is None
    assert check["write_scale"] is False


@pytest.mark.parametrize("record, scale", [
    ({"keys_read": [{"ticks": "0", "value": float32(1.26)}, {"ticks": str(TPS), "value": 1.0115}]}, True),
    ({"keys_read": [{"ticks": "0", "value": float32(1.26)}, {"ticks": str(TPS + 1), "value": float32(1.01)}]}, True),
    ({"keys_read": MATCH_KEYS[:1]}, True),
    ({"keys_read": MATCH_KEYS + [{"ticks": str(2 * TPS), "value": 0.0}]}, True),
    ({"keyframed": None}, True), ({"keyframed": False}, True), ({"keys_read": None}, True),
    ({"scale_value": float32(104.5)}, True), ({"scale_value": None}, True), ({"scale_value": "104.4"}, True)])
def test_plan_entry_keys_do_not_match(record, scale):
    check = _match(**record, scale=scale)
    assert check["skip"] is None and check["refusal"] is None and check["write_scale"] is True


def test_plan_entry_keys_match_whatever_the_order_of_the_plan():
    entry = {**MATCH_ENTRY, "keys": list(reversed(MATCH_ENTRY["keys"]))}
    assert _match(entry)["skip"][0] == "keys_match"


def test_plan_entry_keys_match_without_scale_ignores_scale():
    check = _match(scale=False, scale_value=None)
    assert check["skip"] == ("keys_match", keys.MATCH_TEXT_NO_SCALE)
    assert _match(scale=False, keyframed=None)["skip"] is None


def test_plan_entry_static_match():
    entry = {**MATCH_ENTRY, "static": True, "static_value": -2.15}
    assert _match(entry, keyframed=False, keys_read=[], value=float32(-2.15))["skip"][0] == "keys_match"
    assert _match(entry, keyframed=False, keys_read=[], value=float32(-2.16))["skip"] is None
    assert _match(entry, keyframed=True, keys_read=MATCH_KEYS, value=float32(-2.15))["skip"] is None
    assert _match(entry, keyframed=None, keys_read=[], value=float32(-2.15))["skip"] is None
    assert _match(entry, keyframed=False, keys_read=[], value=None)["skip"] is None


def test_plan_entry_no_change_is_a_code_and_a_reason():
    entry = {**MATCH_ENTRY, "keys": []}
    assert _match(entry)["skip"] == ("no_change", keys.NO_CHANGE)


def test_scale_before_rotation(live, capsys, tmp_path):
    data = level()[:3]
    model = level_model(data)
    code, out, panel = keys_set(live, capsys, tmp_path, model, data)
    assert code == 0
    order = [(kind, node) for kind, node, _ in model.writes]
    expected = []
    for entry in data:
        expected += [("scale", entry["node"]), ("rotation", entry["node"])]
    assert order == expected
    assert len(seen(panel, RESOLVE_MARK)) == 1 and len(seen(panel, CHECK_MARK)) == 1


def test_no_scale(live, capsys, tmp_path):
    data = level()[:3]
    model = level_model(data)
    code, out, panel = keys_set(live, capsys, tmp_path, model, data, "--no-scale")
    assert code == 0 and out["counts"]["done"] == 3
    assert [w[0] for w in model.writes] == ["rotation"] * 3
    assert seen(panel, EFFECT_WRITE_MARK) == [] and seen(panel, RESOLVE_MARK) == []
    assert model.scale(data[0]["node"])["value"] == 100.0 and out["results"][0]["now"]["scale"] == 100.0


def test_clip_changed_is_refused(live, capsys, tmp_path):
    data = level()[:4]
    model = level_model(data)
    clip = model.clips[data[2]["node"]]
    clip["in_ticks"] += FRAME
    clip["out_ticks"] += FRAME
    code, out, panel = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["ok"] is False and out["code"] == "refused"
    assert "entry 2" in out["error"] and "clip_changed" in out["error"] and "Nothing was written" in out["error"]
    record = out["results"][2]
    assert (record["status"], record["skip"], record["reason_code"]) == ("skipped", "refused", "clip_changed")
    assert model.writes == [] and seen(panel, WRITE_MARK) == []
    assert out["stored"] is False and out["counts"]["not_run"] == 3


def test_skip_refused_runs_the_rest(live, capsys, tmp_path):
    data = level()[:4]
    model = level_model(data)
    model.clips[data[2]["node"]]["in_ticks"] += FRAME
    code, out, panel = keys_set(live, capsys, tmp_path, model, data, "--skip-refused")
    assert code == 1 and out["code"] == "not_done"
    assert [r["status"] for r in out["results"]] == ["done", "done", "skipped", "done"]
    assert out["results"][2]["reason_code"] == "clip_changed"
    assert data[2]["node"] not in {w[1] for w in model.writes}
    assert out["counts"] == {"done": 3, "skipped": 1, "refused": 1, "failed": 0, "not_run": 0}


def test_node_in_another_sequence(live, capsys, tmp_path):
    data = level()[:2]
    model = level_model(data)
    model.sequences["s2"] = "Other"
    model.clips[data[1]["node"]]["sequence_id"] = "s2"
    code, out, panel = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["code"] == "refused"
    assert out["results"][1]["reason_code"] == "no_clip" and "Main" in out["results"][1]["reason"]
    assert model.writes == []


def test_retimed_and_slow_motion(live, capsys, tmp_path, monkeypatch):
    data = level()[:3]
    model = level_model(data)
    model.clips[data[0]["node"]]["speed"] = 2.0
    model.clips[data[1]["node"]]["reversed"] = True
    model.clips[data[2]["node"]]["item_fps"] = INTERPRETED_FPS
    code, out, _ = keys_set(live, capsys, tmp_path, model, data, "--skip-refused")
    assert [r["reason_code"] for r in out["results"][:2]] == ["retimed", "retimed"]
    third = out["results"][2]
    assert third["status"] == "done" and third["interpreted"] is True                  # SLOW_MOTION_PROVEN
    # M8: the read compares the item's rate with the sequence rate; it does not know the item is interpreted.
    assert any("runs at" in w and "not the sequence rate" in w for w in third["warnings"])
    assert not any("is interpreted" in w for w in third["warnings"])
    monkeypatch.setattr(keys, "SLOW_MOTION_PROVEN", False)
    model = level_model(data)
    model.clips[data[2]["node"]]["item_fps"] = INTERPRETED_FPS
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["code"] == "refused"
    assert out["results"][2]["reason_code"] == "slow_motion_unproven" and out["results"][2]["interpreted"] is True
    assert model.writes == []


def test_scale_keyed_is_refused(live, capsys, tmp_path):
    data = level()[:2]
    model = level_model(data)
    model.scale(data[1]["node"])["keys"] = [(0, 100.0), (FRAME, 110.0)]
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["results"][1]["reason_code"] == "keyed" and model.writes == []
    code, out, _ = keys_set(live, capsys, tmp_path, model, data, "--no-scale")
    assert code == 0 and out["counts"]["done"] == 2


def test_motion_layout_is_refused(live, capsys, tmp_path):
    data = level()[:2]
    model = level_model(data)
    model.rotation(data[1]["node"])["name"] = "Anchor Point"
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["results"][1]["reason_code"] == "motion_layout" and model.writes == []


def test_scale_height_with_uniform_scale_on_is_the_scale(live, capsys, tmp_path):
    # Ruling 1: Premiere renames Scale to Scale Height; while Uniform Scale is on it is the same scale.
    data = level()[:1]
    model = level_model(data)
    model.scale(data[0]["node"])["name"] = "Scale Height"
    code, out, panel = keys_set(live, capsys, tmp_path, model, data)
    assert code == 0 and model.scale(data[0]["node"])["value"] == float32(data[0]["scale"])
    assert seen(panel, RESOLVE_MARK)[0]["entries"][0]["name"] == "Scale Height"
    model = level_model(data)
    model.scale(data[0]["node"])["name"] = "Scale Height"
    model.prop(data[0]["node"], "Motion", "Uniform Scale")["value"] = False
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["results"][0]["reason_code"] == "motion_layout"


def test_keys_get_finds_scale_height_by_the_name_scale(live, capsys):
    model = model_with(THREE_KEYS)
    model.prop("000f4276", "Motion", "Scale")["name"] = "Scale Height"
    code, out, _ = keys_get(live, capsys, model, *NODE_ARGS, "--property", "Scale")
    assert code == 0 and out["property"] == {"name": "Scale Height", "index": 1}
    model.prop("000f4276", "Motion", "Uniform Scale")["value"] = False
    code, out, _ = keys_get(live, capsys, model, *NODE_ARGS, "--property", "Scale")
    assert code == 1 and "Available properties: Position, Scale Height, Scale Width" in out["error"]


def test_a_length_that_is_not_whole_frames_is_refused(live, capsys, tmp_path):
    # Ruling 2: a keyed entry whose clip length (end minus start) is not a whole number of frames.
    data = level()[:2]
    model = level_model(data)
    model.clips[data[0]["node"]]["end_ticks"] += 1
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["code"] == "refused"
    record = out["results"][0]
    assert record["reason_code"] == "bad_length" and "frame" in record["reason"] and model.writes == []


def test_negative_key_and_key_clash(live, capsys, tmp_path):
    data = level()[:2]
    data[0]["keys"][1]["time_seconds"] = -0.5
    data[1]["keys"].append({"time_seconds": data[1]["keys"][-1]["time_seconds"] + 0.001, "value": 0.1})
    model = level_model(data)
    code, out, _ = keys_set(live, capsys, tmp_path, model, data, "--skip-refused")
    assert [r["reason_code"] for r in out["results"]] == ["negative_key", "key_clash"]
    assert model.writes == []


def test_level_no_change_is_skipped(live, capsys, tmp_path):
    data = level()[:2]
    data[1]["keys"], data[1]["static"] = [], False
    model = level_model(data)
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 0 and out["stored"] is True
    record = out["results"][1]
    assert (record["status"], record["skip"], record["reason"]) == ("skipped", "nothing_to_do", "level, no change")
    assert record["mode"] == "none" and data[1]["node"] not in {w[1] for w in model.writes}


def test_in_script_refusal_after_the_check(live, capsys, tmp_path):
    data = level()[:3]
    model = level_model(data)
    node = data[1]["node"]
    old = rotation_keys(model, node), model.rotation(node)["value"]

    def trim():
        model.clips[node]["in_ticks"] += FRAME
    model.before_write[node] = trim
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["code"] == "not_done"
    record = out["results"][1]
    assert record["status"] == "failed" and results.UNKNOWN not in record and "in point" in record["error"]
    assert (rotation_keys(model, node), model.rotation(node)["value"]) == old            # Rotation unchanged
    assert record["now"]["scale"] == float32(data[1]["scale"])                         # the new Scale stays
    assert any("Scale was written" in w for w in record["warnings"])                   # M4
    assert out["results"][2]["status"] == "done" and out["stored"] is False


def test_readback_mismatch_is_failed(live, capsys, tmp_path):
    data = level()[:3]
    model = level_model(data)
    model.drift[data[0]["node"]] = 1
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1
    record = out["results"][0]
    assert record["status"] == "failed" and results.UNKNOWN not in record
    assert record["now"]["rotation"]["is_time_varying"] is True and "ticks" in record["error"]
    assert [r["status"] for r in out["results"][1:]] == ["done", "done"]


def fail_keys_write(monkeypatch, node: str, error: CliError) -> None:
    original = WriteSession.write

    def write(self, body, args=None, **kw):
        if WRITE_MARK in body and (args or {}).get("node_id") == node:
            raise error
        return original(self, body, args, **kw)
    monkeypatch.setattr(WriteSession, "write", write)


def test_not_applied_ends_the_run(live, capsys, tmp_path, monkeypatch):
    data = level()[:6]
    model = level_model(data)
    fail_keys_write(monkeypatch, data[3]["node"], CliError("not_applied", "Premiere did not pick up the command."))
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["code"] == "not_applied"                                  # I2: the error that ended it
    assert "results" in out and out["counts"]["done"] == 3
    assert [r["status"] for r in out["results"]] == ["done"] * 3 + ["not_run"] * 3
    assert out["first_not_run"] == 3 and out["stored"] is False
    assert any("Scale" in w for w in out["results"][3]["warnings"])                   # Scale of entry 3 was written
    assert ("scale", data[3]["node"], data[3]["scale"]) in model.writes
    assert data[4]["node"] not in {w[1] for w in model.writes}


def test_unknown_outcome_stops(live, capsys, tmp_path, monkeypatch):
    data = level()[:4]
    model = level_model(data)
    fail_keys_write(monkeypatch, data[1]["node"], CliError("unknown_outcome", "The panel did not answer in time."))
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["stored"] is None and out["code"] == "unknown_outcome"
    record = out["results"][1]
    assert record["status"] == "failed" and record[results.UNKNOWN] is True and record["now"] is None
    assert set(record) == RECORD_KEYS | {results.UNKNOWN}
    assert [r["status"] for r in out["results"][2:]] == ["not_run", "not_run"] and out["first_not_run"] == 2


def test_unreadable_read_back_is_unknown(live, capsys, tmp_path):
    data = level()[:3]
    model = level_model(data)
    model.unreadable.add(data[1]["node"])
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["stored"] is None and out["code"] == "not_done"          # no error ended it
    record = out["results"][1]
    assert record["status"] == "failed" and record[results.UNKNOWN] is True and "read back" in record["error"]
    assert out["results"][2]["status"] == "not_run" and out["first_not_run"] == 2


def test_recovery_required_ends_the_run_with_its_code(live, capsys, tmp_path, monkeypatch):
    data = level()[:3]
    model = level_model(data)
    fail_keys_write(monkeypatch, data[1]["node"], CliError("recovery_required", "A recovery is open."))
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["code"] == "recovery_required" and out["error"] == "A recovery is open."
    assert [r["status"] for r in out["results"]] == ["done", "not_run", "not_run"] and out["stored"] is False
    assert any("not sent" in w for w in out["warnings"])


def test_a_partial_scale_write_ends_with_unknown_outcome(live, capsys, tmp_path, monkeypatch):
    data = level()[:2]
    model = level_model(data)

    def write_entry(session, entry, found):
        err = CliError("not_applied", "the put-back was not picked up")
        err.partial = {"attempts": 1}
        raise err
    monkeypatch.setattr(keys.effects, "write_entry", write_entry)
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["code"] == "unknown_outcome" and out["stored"] is None
    assert out["results"][0][results.UNKNOWN] is True and out["results"][1]["status"] == "not_run"


def test_interrupted_rotation_write_ends_the_run(live, capsys, tmp_path, monkeypatch):
    data = level()[:3]
    model = level_model(data)
    fail_keys_write(monkeypatch, data[1]["node"], CliError("interrupted", "Stopped before Premiere picked it up."))
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["code"] == "interrupted" and "results" in out
    assert [r["status"] for r in out["results"]] == ["done", "not_run", "not_run"]
    assert any("Scale was written" in w for w in out["results"][1]["warnings"])
    assert out["results"][1]["now"]["scale"] == float32(data[1]["scale"])


def test_a_stop_during_a_write_is_an_unknown_outcome(live, capsys, tmp_path, monkeypatch):
    data = level()[:3]
    model = level_model(data)
    fail_keys_write(monkeypatch, data[1]["node"], KeyboardInterrupt())
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["code"] == "interrupted" and out["stored"] is None
    record = out["results"][1]
    assert record["status"] == "failed" and record[results.UNKNOWN] is True
    assert out["results"][2]["status"] == "not_run"


def test_scale_not_stored_fails_the_entry_and_skips_its_rotation(live, capsys, tmp_path):
    data = level()[:3]
    model = level_model(data)
    model.scale_lands[data[0]["node"]] = 50.0
    code, out, panel = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["stored"] is False
    record = out["results"][0]
    assert record["status"] == "failed" and results.UNKNOWN not in record and record["error"].startswith("Scale")
    assert record["now"]["scale"] == 100.0                                             # put back
    assert data[0]["node"] not in [args["node_id"] for args in seen(panel, WRITE_MARK)]
    assert [r["status"] for r in out["results"][1:]] == ["done", "done"]


def test_precondition_of_the_scale_write_is_this_entry_only(live, capsys, tmp_path, monkeypatch):
    data = level()[:3]
    model = level_model(data)
    original = WriteSession.write

    def write(self, body, args=None, **kw):
        if EFFECT_WRITE_MARK in body and (args or {}).get("node_id") == data[0]["node"]:
            raise CliError("precondition", "the clip at this place changed")
        return original(self, body, args, **kw)
    monkeypatch.setattr(WriteSession, "write", write)
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert [r["status"] for r in out["results"]] == ["failed", "done", "done"]
    assert results.UNKNOWN not in out["results"][0] and code == 1
    assert ("rotation", data[0]["node"], "keys") not in model.writes


def test_breathe_then_check_again(live, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "MAX_HOLD_S", 0.0)
    data = level()[:5]
    model = level_model(data)

    def trim():
        model.clips[data[2]["node"]]["in_ticks"] += FRAME
    model.on_check[3] = trim               # check 1 is for all; 2 before entry 1; 3 before entry 2
    code, out, panel = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["code"] == "not_done"
    assert [r["status"] for r in out["results"]] == ["done", "done", "skipped", "not_run", "not_run"]
    assert out["results"][2]["reason_code"] == "clip_changed" and out["first_not_run"] == 3
    assert data[2]["node"] not in {w[1] for w in model.writes}
    code, out, _ = keys_set(live, capsys, tmp_path, level_model(data), data)
    assert code == 0                                                     # breathing alone changes nothing


def test_breathe_then_refused_goes_on_with_skip_refused(live, capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(bridge, "MAX_HOLD_S", 0.0)
    data = level()[:4]
    model = level_model(data)

    def trim():
        model.clips[data[2]["node"]]["in_ticks"] += FRAME
    model.on_check[3] = trim
    code, out, _ = keys_set(live, capsys, tmp_path, model, data, "--skip-refused")
    assert [r["status"] for r in out["results"]] == ["done", "done", "skipped", "done"]


def test_dry_run_with_a_refusal_carries_the_plan(live, capsys, tmp_path, sends):
    # M5: as place and apply, a refused dry run still shows what would be written.
    data = level()[:3]
    model = level_model(data)
    model.clips[data[1]["node"]]["in_ticks"] += FRAME
    code, out, _ = keys_set(live, capsys, tmp_path, model, data, "--dry-run")
    assert code == 1 and out["code"] == "refused" and out["dry_run"] is True
    assert len(out["planned"]) == 3 and out["planned"][1]["refusal"]["code"] == "clip_changed"
    assert out["planned"][0]["refusal"] is None and out["planned"][0]["mode"] == "keys"
    assert model.writes == [] and not any(sends)
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["code"] == "refused" and "planned" not in out and "dry_run" not in out


def test_an_unexpected_fault_is_a_recipe_error_with_the_payload(live, capsys, tmp_path, monkeypatch):
    # M6: an Exception that is neither a CliError nor a stop.
    data = level()[:2]
    model = level_model(data)

    def broken(*a, **k):
        raise ValueError("a fault in the check")
    monkeypatch.setattr(keys, "_plan_entry", broken)
    code, out, _ = keys_set(live, capsys, tmp_path, model, data)
    assert code == 1 and out["code"] == "recipe_error" and "ValueError: a fault in the check" in out["error"]
    assert [r["status"] for r in out["results"]] == ["not_run", "not_run"] and model.writes == []


def test_static_spread_override_warns(live, capsys, tmp_path):
    model = level_model()
    code, out, _ = keys_set(live, capsys, tmp_path, model, None, "--static-spread", "0.9")
    assert code == 0
    s07 = by_name(out, "S07")
    assert s07["mode"] == "static" and rotation_keys(model, s07["target"]["node_id"]) is None
    assert model.rotation(s07["target"]["node_id"])["value"] == float32(-3.32)
    assert any("S07" in w and "0.9" in w for w in out["warnings"])


@pytest.mark.parametrize("text, where", [
    ("not json", "not valid JSON"),
    ('{"node": "x"}', "array"),
    ("[]", "at least one"),
    ('[{"src_in": 0, "src_out": 1, "scale": 100, "keys": []}]', "entry 0"),
    ('[{"node": "x", "src_in": 0, "src_out": 1, "scale": 100, "keys": [{"time_seconds": "1", "value": 0}]}]', "entry 0"),
    ('[{"node": "x", "src_in": 1, "src_out": 1, "scale": 100, "keys": []}]', "src_out"),
    ('[{"node": "x", "src_in": 0, "src_out": 1, "scale": 0, "keys": []}]', "scale"),
    ('[{"node": "x", "src_in": 0, "src_out": 1, "scale": 100, "keys": [], "static": true}]', "static_value"),
    ('[{"node": "x", "src_in": 0, "src_out": NaN, "scale": 100, "keys": []}]', "not valid JSON"),
    ('[{"node": "x", "src_in": 1e300, "src_out": 2e300, "scale": 100, "keys": []}]', "src_in"),
    ('[{"node": "x", "src_in": -1e300, "src_out": 1, "scale": 100, "keys": []}]', "src_in"),
    ('[{"node": "x", "src_in": 0, "src_out": 1, "scale": 100, "keys": [{"time_seconds": 1e300, "value": 0}]}]',
     "key 0"),
    ('[{"node": "x", "src_in": 0, "src_out": 1, "scale": 100, "keys": [{"time_seconds": -1e300, "value": 0}]}]',
     "key 0"),
])
def test_plan_file_faults(live, capsys, tmp_path, sends, text, where):
    code, out, panel = keys_set(live, capsys, tmp_path, KeysModel(), text)
    assert code == 1 and out["code"] == "bad_plan" and where in out["error"] and "Nothing was sent" in out["error"]
    assert sends == [] and panel.seen == []


def test_null_scale_needs_no_scale(live, capsys, tmp_path, sends):
    data = level()[:1]
    data[0]["scale"] = None
    code, out, _ = keys_set(live, capsys, tmp_path, level_model(data), data)
    assert code == 1 and out["code"] == "bad_plan" and "--no-scale" in out["error"] and sends == []
    code, out, _ = keys_set(live, capsys, tmp_path, level_model(data), data, "--no-scale")
    assert code == 0


def test_check_level_plan_and_load_level_plan(tmp_path):
    data = level()
    assert keys.check_level_plan(data) == data
    assert keys.load_level_plan(plan_file(tmp_path, data)) == data
    with pytest.raises(CliError) as err:
        keys.load_level_plan(tmp_path / "missing.json")
    assert err.value.code == "bad_plan"


def test_unknown_keys_pass_with_a_warning(live, capsys, tmp_path):
    data = level()[:2]
    data[1]["note"] = "handheld"
    code, out, _ = keys_set(live, capsys, tmp_path, level_model(data), data)
    assert code == 0 and any("note" in w and "entry 1" in w for w in out["warnings"])


def test_own_film_is_refused(live, capsys, tmp_path):
    data = level()[:2]
    model = level_model(data)
    code, out, panel = keys_set(live, capsys, tmp_path, model, data, state=STATE_OWN)
    assert code == 1 and out["code"] == "project_guard" and model.writes == []
    assert len(panel.seen) == 1 and "playhead_ticks: playhead" in panel.seen[0]


def test_the_sequence_name_is_checked(live, capsys, tmp_path):
    data = level()[:1]
    code, out, panel = keys_set(live, capsys, tmp_path, level_model(data), data, "--sequence-name", "Other")
    assert code == 1 and out["code"] == "precondition" and "named Main, not Other" in out["error"]


def test_the_write_arguments(live, capsys, tmp_path):
    data = level()[:2]
    data[1]["static"] = True
    model = level_model(data)
    code, out, panel = keys_set(live, capsys, tmp_path, model, data)
    assert code == 0
    first, second = seen(panel, WRITE_MARK)
    clip = model.clips[data[0]["node"]]
    assert first["node_id"] == data[0]["node"] and first["mode"] == "keys"
    assert first["expect_clip"] == {"in_ticks": str(clip["in_ticks"]), "out_ticks": str(clip["out_ticks"])}
    assert all(isinstance(k["ticks"], str) for k in first["keys"])
    assert first["keys"][0]["ticks"] == str(clip["in_ticks"])
    assert second["mode"] == "static" and second["value"] == data[1]["static_value"]
    assert first["expect"]["sequence_id"] == "s1"


def human_set(live, capsys, tmp_path, model, data, *argv):  # noqa: F811
    path = plan_file(tmp_path, data)
    with panel_on(live, handler=recipe_handler(model.replies())):
        code = dispatch(["live", "keys", "set", "--sequence", "s1", "--from", path, *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_human_output_of_keys_set(live, capsys, tmp_path):
    data = level()[:2]
    code, out, err = human_set(live, capsys, tmp_path, level_model(data), data)
    assert code == 0 and "S02 C2246 done" in out and "2 done, 0 skipped, 0 failed, 0 not run." in out
    code, out, err = human_set(live, capsys, tmp_path, level_model(data), data, "--dry-run")
    assert code == 0 and "S02 C2246" in out and "keys" in out


def test_keys_set_help(capsys):
    assert dispatch(["live", "keys", "set", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    for word in ("--sequence", "--sequence-name", "--from", "--no-scale", "--static-spread", "--skip-refused",
                 "--dry-run", "--project", "--any-project", "--lease-wait", "--timeout", "--allow-untested"):
        assert word in text, word
    lowered = text.lower()
    assert "replace the old" in lowered and "one frame before the clip end" in lowered
    assert "addkey" in lowered and "not saved" in lowered
    # M7: a precondition fails that entry only; D1: a matching clip is skipped.
    assert "when a write was not sent" not in lowered
    assert "refused inside its write script" in lowered and "keys_match" in lowered


# ---------- the two new scripts, in node ----------
WRITE_IN = 5 * TPS
WRITE_PRELUDE = STUB_HELPERS + """
var writes = 0;
function Time() { this.ticks = "0"; }
function mkProp(name, value) {
  var p = {displayName: name, value: value};
  p.isTimeVarying = function () { return false; };
  p.getValue = function () { return p.value; };
  p.getKeys = function () { return undefined; };
  p.setValue = p.setValueAtKey = p.addKey = p.removeKey = p.setTimeVarying = function () { writes++; };
  return p;
}
// A Rotation that behaves as measured on Premiere 27: M1 (true adds no key), M2 (one key per time),
// M3b (false clears every key), M4 (values stored as 32-bit floats).
function mkLive(name, keys, value) {
  var p = {displayName: name, keys: keys, value: value, log: [], addThrows: {}};
  var find = function (ticks) {
    for (var i = 0; i < p.keys.length; i++) if (p.keys[i][0] === String(ticks)) return i;
    return -1;
  };
  p.isTimeVarying = function () { return p.keys !== null; };
  p.setTimeVarying = function (on) { writes++; p.log.push("tv " + on); if (!on) p.keys = null; else if (p.keys === null) p.keys = []; };
  p.addKey = function (t) {
    writes++; p.log.push("add " + t.ticks);
    if (p.addThrows[String(t.ticks)]) throw new Error("addKey failed");
    if (find(t.ticks) < 0) p.keys.push([String(t.ticks), 0]);
    p.keys.sort(function (a, b) { return parseFloat(a[0]) - parseFloat(b[0]); });
  };
  p.setValueAtKey = function (t, v) { writes++; p.log.push("set " + t.ticks); var i = find(t.ticks); if (i >= 0) p.keys[i][1] = Math.fround(v); };
  p.setValue = function (v) { writes++; p.log.push("value " + v); p.value = Math.fround(v); };
  p.getValue = function () { return p.value; };
  p.getKeys = function () {
    if (p.keys === null) return undefined;
    var out = [];
    for (var i = 0; i < p.keys.length; i++) { var t = new Time(); t.ticks = p.keys[i][0]; out.push(t); }
    return out;
  };
  p.getValueAtKey = function (t) { var i = find(t.ticks); return i < 0 ? null : p.keys[i][1]; };
  return p;
}
function mkComp(display, match, props) { return {displayName: display, matchName: match, properties: coll("numItems", props)}; }
var rot = mkLive("Rotation", [["%(old)s", 3.0]], 0);
var scaleProp = mkProp("Scale", 100);
var motion = mkComp("Motion", "AE.ADBE Motion", [mkProp("Position", [0.5, 0.5]), scaleProp, mkProp("Scale Width", 100),
                                                 mkProp("Uniform Scale", true), rot, mkProp("Anchor Point", [0.5, 0.5])]);
var item = {nodeId: "I1", name: "C2246.MP4", getFootageInterpretation: function () { return {frameRate: 23.976023976024}; }};
var w1 = {nodeId: "W1", name: "S02 C2246", inPoint: {ticks: "%(in)s"}, outPoint: {ticks: "%(out)s"}, start: {ticks: "0"},
          end: {ticks: "%(len)s"}, projectItem: item, components: coll("numItems", [mkComp("Opacity", "AE.ADBE Opacity", []), motion])};
w1.getSpeed = function () { return 1; };
w1.isSpeedReversed = function () { return false; };
var a1 = {nodeId: "A1", name: "Sound", inPoint: {ticks: "0"}, outPoint: {ticks: "10"}, start: {ticks: "0"}, end: {ticks: "10"},
          components: coll("numItems", [mkComp("Volume", "AE.ADBE Volume", [])])};
a1.getSpeed = function () { return 1; };
a1.isSpeedReversed = function () { return false; };
var seqA = {sequenceID: "S-A", name: "Cut A", timebase: "%(frame)s", videoTracks: coll("numTracks", [track([w1])]),
            audioTracks: coll("numTracks", [track([a1])])};
var app = {project: {path: "/p/X Claude.prproj", sequences: coll("numSequences", [seqA]), activeSequence: null}};
""" % {"old": str(WRITE_IN + 5 * FRAME), "in": str(WRITE_IN), "out": str(WRITE_IN + LENGTH), "len": str(LENGTH),
       "frame": str(FRAME)}
EXPECT = {"project_path": "/p/X Claude.prproj", "sequence_id": "S-A", "sequence_name": None}
KEY_1 = str(WRITE_IN + 316757952000)


def write_args(**over) -> dict:
    args = {"node_id": "W1", "expect_clip": {"in_ticks": str(WRITE_IN), "out_ticks": str(WRITE_IN + LENGTH)},
            "mode": "keys", "keys": [{"ticks": str(WRITE_IN), "value": 1.26}, {"ticks": KEY_1, "value": -0.07}],
            "value": None, "expect": EXPECT}
    args.update(over)
    return args


def write_script(prelude_patch: str = "", **over) -> tuple[dict, int]:
    body = jsx.load("keys_write") + "\n"
    return run_script(body, write_args(**over), WRITE_PRELUDE + prelude_patch)


def log_script(prelude_patch: str = "", **over) -> list:
    body = jsx.load("keys_write")
    script = jsx.build(body, write_args(**over), include=INCLUDES)
    code = (WRITE_PRELUDE + prelude_patch + "\nvar __out = " + script
            + ";\nprocess.stdout.write(JSON.stringify(rot.log));")
    return json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout)


@needs_node
def test_keys_write_in_node():
    reply, writes = write_script()
    assert reply["success"] is True, reply
    data = reply["data"]
    assert data["before"] == {"is_time_varying": True, "count": 1} and data["set_errors"] == []
    assert data["after"]["is_time_varying"] is True
    assert [k["ticks"] for k in data["after"]["keys"]] == [str(WRITE_IN), KEY_1]       # the old key is gone
    assert [k["value"] for k in data["after"]["keys"]] == [float32(1.26), float32(-0.07)]
    assert log_script() == ["tv false", "tv true", "add " + str(WRITE_IN), "set " + str(WRITE_IN), "add " + KEY_1,
                            "set " + KEY_1]
    # static: false clears the keys, then the value
    reply, _ = write_script(mode="static", keys=None, value=-2.6)
    assert reply["data"]["after"] == {"is_time_varying": False, "keys": [], "value": float32(-2.6)}
    assert log_script(mode="static", keys=None, value=-2.6) == ["tv false", "value -2.6"]
    # a property with no keys: no false first
    assert log_script("rot.keys = null;")[0] == "tv true"
    # a throwing addKey: the error is in set_errors, the rest is written and read back
    reply, _ = write_script('rot.addThrows["%s"] = true;' % KEY_1)
    assert reply["success"] is True and len(reply["data"]["set_errors"]) == 1
    assert KEY_1 in reply["data"]["set_errors"][0] and "addKey failed" in reply["data"]["set_errors"][0]
    assert [k["ticks"] for k in reply["data"]["after"]["keys"]] == [str(WRITE_IN)]
    # a read back that throws after the writes: no precondition, after is null
    reply, _ = write_script("rot.getKeys = function () { throw new Error('gone'); };")
    assert reply["success"] is True and reply["data"]["after"] is None
    assert any("read back" in e for e in reply["data"]["set_errors"])


WRITE_REFUSALS = [
    ({"expect_clip": {"in_ticks": str(WRITE_IN + 1), "out_ticks": str(WRITE_IN + LENGTH)}}, "",
     "the in point of the clip is %d ticks, not %d as the plan expects" % (WRITE_IN, WRITE_IN + 1)),
    ({"expect_clip": {"in_ticks": str(WRITE_IN), "out_ticks": "1"}}, "",
     "the out point of the clip is %d ticks, not 1 as the plan expects" % (WRITE_IN + LENGTH)),
    ({}, "w1.getSpeed = function () { return 2; };", "the clip has a speed of 2; keys are written at speed 1 only"),
    ({}, "w1.isSpeedReversed = function () { return true; };",
     "the clip is reversed; keys are written on a clip that is not reversed only"),
    ({}, 'rot.displayName = "Anchor";', "property 4 of Motion is named Anchor, not Rotation"),
    ({"node_id": "W9"}, "", "no clip has the node ID W9 in sequence Cut A"),
    ({"node_id": "A1"}, "", "this clip is on an audio track; Motion Rotation is a video property"),
    ({"expect": {**EXPECT, "sequence_name": "Cut B"}}, "", "sequence S-A is named Cut A, not Cut B"),
]


@needs_node
@pytest.mark.parametrize("over, patch, text", WRITE_REFUSALS)
def test_keys_write_refuses_before_its_first_write(over, patch, text):
    reply, writes = write_script(patch, **over)
    assert refused(reply) == text and writes == 0


@needs_node
@pytest.mark.parametrize("over", [{"mode": "loop"}, {"keys": []}, {"keys": [{"ticks": 5, "value": 1}]},
                                  {"keys": [{"ticks": "5", "value": "1"}]},
                                  {"keys": [{"ticks": "5", "value": 1}, {"ticks": "5", "value": 2}]},
                                  {"mode": "static", "keys": None, "value": None}, {"node_id": ""},
                                  {"expect_clip": {"in_ticks": 1, "out_ticks": "2"}}])
def test_keys_write_refuses_bad_arguments(over):
    reply, writes = write_script(**over)
    assert reply["success"] is False and "precondition: " in reply["error"] and writes == 0


def write_model() -> KeysModel:
    model = KeysModel()
    model.sequences = {"S-A": "Cut A"}
    model.add_clip("W1", name="S02 C2246", in_ticks=WRITE_IN, length_ticks=LENGTH, sequence_id="S-A",
                   rotation=[(WRITE_IN + 5 * FRAME, 3.0)])
    model.add_clip("A1", name="Sound", track_type="audio", sequence_id="S-A")
    return model


MODEL_PATCHES = {"": lambda m: None,
                 "w1.getSpeed = function () { return 2; };": lambda m: m.clips["W1"].update(speed=2),
                 "w1.isSpeedReversed = function () { return true; };": lambda m: m.clips["W1"].update(reversed=True),
                 'rot.displayName = "Anchor";': lambda m: m.rotation("W1").update(name="Anchor")}


@needs_node
def test_keys_write_matches_keys_model():
    model = write_model()
    node_reply, _ = write_script()
    said = model.keys_write(jsx.build(jsx.load("keys_write"), write_args(), include=INCLUDES))
    assert said == node_reply["data"]
    for over, patch, text in WRITE_REFUSALS:
        model = write_model()
        MODEL_PATCHES[patch](model)
        said = model.keys_write(jsx.build(jsx.load("keys_write"), write_args(**over), include=INCLUDES))
        node_reply, _ = write_script(patch, **over)
        assert isinstance(said, Exception) and str(said) == node_reply["error"], over


@needs_node
def test_keys_check_in_node():
    args = {"entries": [{"node_id": "W1"}, {"node_id": "NOPE"}, {"node_id": "A1"}], "expect": EXPECT}
    reply, writes = run_script(jsx.load("keys_check"), args, WRITE_PRELUDE)
    assert reply["success"] is True and writes == 0
    data = reply["data"]
    assert data["frame_ticks"] == str(FRAME)
    first, missing, audio = data["entries"]
    assert first["found"] is True and first["item_fps"] == 23.976023976024 and first["error"] is None
    assert first["interpreted"] is False
    assert first["clip"]["in_ticks"] == str(WRITE_IN) and first["clip"]["node_id"] == "W1"
    assert first["clip"]["track_type"] == "video" and first["clip"]["speed"] == 1
    assert first["motion"]["scale"] == {"index": 1, "name": "Scale", "matches": True, "value": 100, "keyframed": False}
    assert first["motion"]["rotation"]["name"] == "Rotation" and first["motion"]["rotation"]["keyframed"] is True
    assert first["motion"]["uniform"] is True
    assert missing == {"found": False, "node_id": "NOPE", "error": "no clip has the node ID NOPE in sequence Cut A",
                       "clip": None, "interpreted": None, "item_fps": None, "motion": None, "motion_error": None}
    assert audio["found"] is True and audio["motion"] is None
    assert audio["motion_error"] == "no effect named Motion on this clip"
    reply, _ = run_script(jsx.load("keys_check"), args,
                          WRITE_PRELUDE + "item.getFootageInterpretation = function () { throw new Error('no'); };")
    assert reply["data"]["entries"][0]["item_fps"] is None and reply["data"]["entries"][0]["interpreted"] is None
    patch = "item.getFootageInterpretation = function () { return {frameRate: %r}; };" % INTERPRETED_FPS
    reply, _ = run_script(jsx.load("keys_check"), args, WRITE_PRELUDE + patch)
    assert reply["data"]["entries"][0]["interpreted"] is True                          # the M6 read
    reply, _ = run_script(jsx.load("keys_check"), args, WRITE_PRELUDE + 'seqA.timebase = "x";')
    assert reply["data"]["frame_ticks"] is None and reply["data"]["entries"][0]["interpreted"] is None
    reply, _ = run_script(jsx.load("keys_check"), {**args, "expect": {**EXPECT, "sequence_id": "S-Z"}}, WRITE_PRELUDE)
    assert refused(reply) == "no sequence has the ID S-Z"
    # shape parity with KeysModel
    model = KeysModel()
    model.sequences = {"S-A": "Cut A"}
    model.add_clip("W1", name="S02 C2246", in_ticks=WRITE_IN, length_ticks=LENGTH, sequence_id="S-A",
                   rotation=[(WRITE_IN + 5 * FRAME, 3.0)])
    model.add_clip("A1", name="Sound", track_type="audio", sequence_id="S-A")
    model.clips["A1"]["effects"] = [{"name": "Volume", "match_name": "AE.ADBE Volume", "properties": []}]
    said = model.keys_check(jsx.build(jsx.load("keys_check"), {**args}, include=INCLUDES))
    node_reply, _ = run_script(jsx.load("keys_check"), args, WRITE_PRELUDE)
    assert shape(said) == shape(node_reply["data"])


@needs_node
def test_property_named_scale_follows_uniform_scale_in_node():
    body = ("var found = __propertyNamed(__clip(__sequenceById('S-A'), {node_id: 'W1'}).clip.components[1], ARGS.name);\n"
            "return __ok([found.index, String(found.property.displayName)]);")
    assert run_script(body, {"name": "Scale"}, WRITE_PRELUDE)[0]["data"] == [1, "Scale"]
    renamed = WRITE_PRELUDE + 'scaleProp.displayName = "Scale Height";'
    assert run_script(body, {"name": "Scale"}, renamed)[0]["data"] == [1, "Scale Height"]
    assert run_script(body, {"name": "Scale Height"}, WRITE_PRELUDE)[0]["data"] == [1, "Scale"]
    off = renamed + "motion.properties[3].value = false;"
    assert refused(run_script(body, {"name": "Scale"}, off)[0]) == (
        "no property named Scale in Motion. Available properties: Position, Scale Height, Scale Width, Uniform Scale, "
        "Rotation, Anchor Point.")


def test_new_keys_scripts_start_with_their_markers():
    for name in ("keys_check", "keys_write"):
        assert jsx.load(name).split("\n")[0] == f"// recipe: {name}"
    for name in ("keys", "keys_get", "keys_check", "keys_write", "find", "values"):
        text = jsx.load(name)
        for other in ("keys_get", "keys_check", "keys_write", "effect_resolve", "effect_write"):
            if other != name:
                assert f"recipe: {other}" not in text, (name, other)


def test_new_keys_scripts_are_es3():
    for name in ("keys_check", "keys_write"):
        text = jsx.load(name)
        for pattern in BANNED + [r"\.indexOf\(", r"\bObject\.(?!prototype\.toString\.call)", r"\.reduce\(",
                                 r"\.some\(", r"\.every\("]:
            assert not re.search(pattern, text), f"{name}.jsx: {pattern}"


def test_keys_check_calls_no_writer_of_premiere():
    text = jsx.load("keys_check")
    for writer in (r"\.addKey\(", r"\.setValue\(", r"\.setValueAtKey\(", r"\.removeKey\(", r"\.setTimeVarying\(",
                   r"__keyTime\("):
        assert not re.search(writer, text), f"keys_check.jsx: {writer}"


def test_every_host_call_in_the_new_keys_scripts_is_cited():
    head = head_comment("keys_write")
    for call in ("setTimeVarying", "addKey", "setValueAtKey", "setValue", "isTimeVarying", "getKeys", "new Time",
                 "clipboard.js, lines 253-258", "keyframes.js, line 220", "find.jsx", "keys.jsx", "ARGS", "Reply"):
        assert call in head, f"keys_write.jsx: {call}"
    head = head_comment("keys_check")
    for call in ("getFootageInterpretation", "frameRate", "timebase", "sequence.js, lines 485-486", "projectItem",
                 "isTimeVarying", "find.jsx", "keys.jsx", "ARGS", "Reply"):
        assert call in head, f"keys_check.jsx: {call}"
    head = head_comment("keys")
    for call in ("__isUniformScale", "script-builder.js, lines 694-699", "lines 701-722"):
        assert call in head, f"keys.jsx: {call}"
