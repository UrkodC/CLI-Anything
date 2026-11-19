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
    (["--property", "Nope"], "no property named Nope in Motion"),
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
    assert refused(reply) == "no property named Nope in Motion"


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
    assert refused(run_script(body, {"name": ""}, KEYS_PRELUDE)[0]) == "no property named  in Motion"


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
