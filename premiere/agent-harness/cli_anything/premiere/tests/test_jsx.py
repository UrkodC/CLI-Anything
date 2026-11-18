"""Unit tests for the ExtendScript builder and the helper/state scripts.

The scripts are ES3 JavaScript. Node runs them here with a stub `app` object, so no Premiere is needed.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from cli_anything.premiere.core import jsx
from cli_anything.premiere.errors import CliError

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


def run_in_node(script: str, prelude: str = "") -> dict:
    """Evaluate a built script in node (ES3 runs there) and parse the JSON string it returns."""
    code = prelude + "\nprocess.stdout.write(String(" + script + "));"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout
    return json.loads(out)


# Builds the object shape the Premiere DOM uses: a collection has numTracks/numItems plus numbered members.
STUB_HELPERS = """
function coll(key, items) { var c = {}; c[key] = items.length; for (var i = 0; i < items.length; i++) c[i] = items[i]; return c; }
function clip(id, name) { return {nodeId: id, name: name}; }
function track(clips) { return {clips: coll("numItems", clips)}; }
"""


def test_build_puts_args_in_as_ascii_json():
    s = jsx.build("return __ok(ARGS);", {"name": 'a"b\\c\né'})
    assert 'var ARGS = {"name": "a\\"b\\\\c\\n\\u00e9"};' in s


def test_build_has_the_documented_shape():
    helpers = jsx.load("helpers").rstrip("\n")
    s = jsx.build("return __ok(1);")
    assert s == ("(function () {\n" + helpers + "\nvar ARGS = {};\ntry {\nreturn __ok(1);\n"
                 "} catch (e) { return __fail(String(e)); }\n})()")


@pytest.mark.parametrize("args", [[1], "x", 5, (1, 2)])
def test_build_rejects_args_that_are_not_a_dict(args):
    with pytest.raises(CliError) as e:
        jsx.build("return __ok(1);", args)
    assert e.value.code == "bad_args"


def test_build_rejects_args_that_cannot_be_serialized():
    loop: dict = {}
    loop["self"] = loop
    for args in ({"k": object()}, {"k": {1, 2}}, {"k": float("nan")}, {"k": float("inf")}, loop):
        with pytest.raises(CliError) as e:
            jsx.build("return __ok(1);", args)
        assert e.value.code == "bad_args"


def test_build_treats_no_args_and_empty_args_alike():
    assert jsx.build("return __ok(1);") == jsx.build("return __ok(1);", {}) == jsx.build("return __ok(1);", None)


@needs_node
def test_args_round_trip_through_the_helpers():
    args = {"name": 'Film "v3" \\ é 日本\n', "n": -2.5, "flag": True, "none": None, "list": [1, "x", {"k": 0}]}
    assert run_in_node(jsx.build("return __ok(ARGS);", args)) == {"success": True, "data": args}


@needs_node
def test_a_thrown_error_becomes_a_failure_reply():
    assert run_in_node(jsx.build("throw new Error('boom');")) == {"success": False, "error": "Error: boom"}


@needs_node
def test_json_helper_edge_values():
    body = "return __ok({u: undefined, nan: 0/0, inf: 1/0, f: function(){}, ctl: '\\u0001'});"
    assert run_in_node(jsx.build(body))["data"] == {"u": None, "nan": None, "inf": None, "f": None, "ctl": "\x01"}


@needs_node
def test_json_helper_output_is_ascii_only():
    """Every character above 0x7E leaves the helpers as a \\uXXXX escape, surrogate pairs included."""
    out = subprocess.run([NODE, "-e", "process.stdout.write(String(" + jsx.build("return __ok(ARGS);", {"s": "é\x7f日🎉"}) + "));"],
                         capture_output=True, text=True, timeout=30, check=True).stdout
    assert out.isascii()
    assert json.loads(out)["data"] == {"s": "é\x7f日🎉"}
    assert "\\u007f" in out.lower() and "\\ud83c\\udf89" in out.lower()


@needs_node
def test_json_helper_skips_inherited_properties():
    body = ("function P() { this.own = 1; } P.prototype.inherited = 2;"
            " return __ok({o: new P(), a: [undefined, 1], e: {}, ea: []});")
    assert run_in_node(jsx.build(body))["data"] == {"o": {"own": 1}, "a": [None, 1], "e": {}, "ea": []}


@needs_node
def test_helpers_do_not_leak_into_the_global_scope():
    code = ("var r = " + jsx.build("return __ok(1);") + ";"
            "process.stdout.write([typeof __ok, typeof __fail, typeof __json, typeof ARGS].join());")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout
    assert out == "undefined,undefined,undefined,undefined"


@needs_node
def test_state_script_with_no_project_open():
    prelude = "var app = {version: '27.0.0', build: '88', projects: {numProjects: 0}, project: null};"
    data = run_in_node(jsx.build(jsx.load("state")), prelude)["data"]
    assert data == {"version": "27.0.0", "build": "88", "projects": [], "active_sequence": None,
                    "playhead_ticks": None, "selection": []}


STATE_PRELUDE = STUB_HELPERS + """
var video0 = clip("v0", "Intro");
var video1 = clip("v1", "Interview");
var video2 = clip("v2", "Outro");
var audio0 = clip("a0", "Music");
var seq = {
  sequenceID: "SEQ-1", name: "Main cut",
  videoTracks: coll("numTracks", [track([video0]), track([video1, video2])]),
  audioTracks: coll("numTracks", [track([audio0])]),
  getPlayerPosition: function () { return {ticks: "254016000000", seconds: 1}; },
  getSelection: function () { return [video2, audio0, video1]; }
};
var main = {name: "Film", path: "/Users/u/Film.prproj", documentID: "DOC-2", activeSequence: seq};
var other = {name: "Other", path: "/Users/u/Other.prproj", documentID: "DOC-1", activeSequence: null};
// The host hands back a new wrapper object each time, so the active project is not the same object as its list entry.
var activeWrapper = {name: "Film", path: "/Users/u/Film.prproj", documentID: "DOC-2", activeSequence: seq};
var app = {version: "27.0.0", build: "88", projects: coll("numProjects", [other, main]), project: activeWrapper};
"""


@needs_node
def test_state_script_with_a_project_open():
    data = run_in_node(jsx.build(jsx.load("state")), STATE_PRELUDE)["data"]
    assert data == {
        "version": "27.0.0", "build": "88",
        "projects": [{"name": "Other", "path": "/Users/u/Other.prproj", "active": False},
                     {"name": "Film", "path": "/Users/u/Film.prproj", "active": True}],
        "active_sequence": {"id": "SEQ-1", "name": "Main cut"},
        "playhead_ticks": "254016000000",
        "selection": [{"track_type": "video", "track": 1, "clip": 1, "node_id": "v2", "name": "Outro"},
                      {"track_type": "audio", "track": 0, "clip": 0, "node_id": "a0", "name": "Music"},
                      {"track_type": "video", "track": 1, "clip": 0, "node_id": "v1", "name": "Interview"}],
    }


@needs_node
def test_state_script_finds_the_active_project_by_path_when_there_is_no_document_id():
    prelude = STUB_HELPERS + """
var a = {name: "A", path: "C:\\\\Work\\\\A.prproj"};
var b = {name: "B", path: "C:\\\\Work\\\\B.prproj"};
var app = {version: "27.0.0", build: "88", projects: coll("numProjects", [a, b]),
           project: {name: "B", path: "c:/work/b.prproj", activeSequence: null}};
"""
    data = run_in_node(jsx.build(jsx.load("state")), prelude)["data"]
    assert [p["active"] for p in data["projects"]] == [False, True]
    assert data["active_sequence"] is None and data["playhead_ticks"] is None and data["selection"] == []


@needs_node
def test_state_script_survives_an_unreadable_playhead_and_selection():
    prelude = STATE_PRELUDE + """
seq.getPlayerPosition = function () { throw new Error("no player"); };
seq.getSelection = function () { throw new Error("no selection"); };
"""
    data = run_in_node(jsx.build(jsx.load("state")), prelude)["data"]
    assert data["active_sequence"] == {"id": "SEQ-1", "name": "Main cut"}
    assert data["playhead_ticks"] is None and data["selection"] == []


@needs_node
@pytest.mark.parametrize("ticks", ["soon", "", "-5", "1.5", "12ab", "2.5e+25"])
def test_state_script_reports_a_playhead_that_is_not_digits_as_null(ticks):
    prelude = STATE_PRELUDE + "seq.getPlayerPosition = function () { return {ticks: %s}; };" % json.dumps(ticks)
    assert run_in_node(jsx.build(jsx.load("state")), prelude)["data"]["playhead_ticks"] is None


@needs_node
def test_summary_script_counts_tracks_and_clips_without_listing_them():
    prelude = STUB_HELPERS + """
var seq = {sequenceID: "SEQ-9", name: "Cut",
  videoTracks: coll("numTracks", [track([clip("1", "a"), clip("2", "b"), clip("3", "c")]), track([clip("4", "d")])]),
  audioTracks: coll("numTracks", [track([clip("5", "e"), clip("6", "f")])])};
var app = {project: {name: "Film", activeSequence: seq}};
"""
    assert run_in_node(jsx.build(jsx.load("summary")), prelude)["data"] == {
        "project": "Film", "active_sequence": {"id": "SEQ-9", "name": "Cut"},
        "video_tracks": 2, "audio_tracks": 1, "video_clips": 4, "audio_clips": 2}


@needs_node
def test_summary_script_with_no_project_and_with_no_active_sequence():
    empty = {"project": None, "active_sequence": None, "video_tracks": 0, "audio_tracks": 0,
             "video_clips": 0, "audio_clips": 0}
    assert run_in_node(jsx.build(jsx.load("summary")), "var app = {project: null};")["data"] == empty
    no_seq = run_in_node(jsx.build(jsx.load("summary")), "var app = {project: {name: 'Film', activeSequence: null}};")
    assert no_seq["data"] == {**empty, "project": "Film"}


BANNED = [r"\blet\s", r"\bconst\s", r"=>", r"`", r"\bJSON\.", r"Object\.keys", r"\.forEach\(", r"\.map\(",
          r"\.filter\(", r"\.trim\(\)", r"\bclass\s", r"\.\.\."]


@pytest.mark.parametrize("path", sorted(jsx.SCRIPTS_DIR.glob("*.jsx")), ids=lambda p: p.name)
def test_scripts_use_es3_only(path):
    text = path.read_text(encoding="utf-8")
    for pattern in BANNED:
        assert not re.search(pattern, text), f"{path.name}: {pattern}"


def test_load_missing_script():
    with pytest.raises(CliError) as e:
        jsx.load("nope")
    assert e.value.code == "script_missing"


def test_load_refuses_names_that_leave_the_scripts_folder():
    for name in ("../jsx", "a/b", "", "state.jsx", ".."):
        with pytest.raises(CliError) as e:
            jsx.load(name)
        assert e.value.code == "script_missing"
