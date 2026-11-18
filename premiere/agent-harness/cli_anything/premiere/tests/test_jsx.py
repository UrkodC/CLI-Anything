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


def catch(before: int) -> str:
    """The catch line of a built script whose body starts after `before` lines."""
    return ('} catch (e) { return __fail(String(e) + (e && e.line > %d ? " (line " + (e.line - %d) + ")" : "")); }'
            % (before, before))

# ExtendScript gives an Error a `line`, counted in the evaluated script. Node does not, so this stub adds a getter
# that reads the line from the stack and takes off the lines of the prelude that node counts too.
LINE_STUB = r"""
Object.defineProperty(Error.prototype, "line", {configurable: true, get: function () {
  var m = /\[eval\]:(\d+):\d+/.exec(String(this.stack));
  return m ? Number(m[1]) - PRELUDE_LINES : undefined;
}});
"""


def line_prelude() -> str:
    return LINE_STUB.replace("PRELUDE_LINES", str(LINE_STUB.count("\n") + 1))


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
    prefix = "(function () {\n" + helpers + "\nvar ARGS = {};\ntry {\n"
    assert s == prefix + "return __ok(1);\n" + catch(prefix.count("\n")) + "\n})()"


@pytest.fixture
def scripts_dir(tmp_path, monkeypatch):
    """A scripts folder of its own: the real helpers plus a `find` and a `two` script."""
    (tmp_path / "helpers.jsx").write_text(jsx.load("helpers"), encoding="utf-8")
    (tmp_path / "find.jsx").write_text("function __find(n) { return n + 1; }\n", encoding="utf-8")
    (tmp_path / "two.jsx").write_text("var __two = 2;", encoding="utf-8")      # no newline at the end
    monkeypatch.setattr(jsx, "SCRIPTS_DIR", tmp_path)
    return tmp_path


def test_build_includes_named_scripts(scripts_dir):
    helpers = jsx.load("helpers").rstrip("\n")
    body = "return __ok(__find(__two));"
    s = jsx.build(body, {"k": 1}, include=("find", "two"))
    prefix = ("(function () {\n" + helpers + "\nfunction __find(n) { return n + 1; }\nvar __two = 2;\n"
              'var ARGS = {"k": 1};\ntry {\n')
    assert s == prefix + body + "\n" + catch(prefix.count("\n")) + "\n})()"
    assert s.index("function __find") < s.index("var ARGS")
    assert jsx.build(body, include=()) == jsx.build(body)


@needs_node
def test_included_scripts_run_inside_the_wrapper(scripts_dir):
    reply = run_in_node(jsx.build("return __ok(__find(__two));", include=["find", "two"]))
    assert reply == {"success": True, "data": 3}


@pytest.mark.parametrize("name", ["nope", "../helpers", "a/b", "", "find.jsx"])
def test_build_include_of_a_missing_name_is_script_missing(scripts_dir, name):
    with pytest.raises(CliError) as e:
        jsx.build("return __ok(1);", include=("find", name))
    assert e.value.code == "script_missing"


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
def test_script_failure_carries_the_line_of_the_body():
    script = jsx.build('var a = 1;\nthrow new Error("x");')
    assert run_in_node(script, line_prelude()) == {"success": False, "error": "Error: x (line 2)"}
    first = jsx.build('throw new Error("y");', {"k": "a\nb"})
    assert run_in_node(first, line_prelude()) == {"success": False, "error": "Error: y (line 1)"}


@needs_node
def test_a_failure_thrown_before_the_body_has_no_line(scripts_dir):
    (scripts_dir / "thrower.jsx").write_text('function __throw() {\n  throw new Error("in an include");\n}\n',
                                              encoding="utf-8")
    script = jsx.build("var a = 1;\n__throw();", include=("thrower",))
    assert run_in_node(script, line_prelude()) == {"success": False, "error": "Error: in an include"}


@needs_node
@pytest.mark.parametrize("thrown, text", [("'plain'", "plain"), ("null", "null"), ("undefined", "undefined"),
                                          ("{message: 'm'}", "[object Object]")])
def test_a_failure_without_a_line_has_no_suffix(thrown, text):
    reply = run_in_node(jsx.build(f"throw {thrown};"), line_prelude())
    assert reply == {"success": False, "error": text}


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


# ---------- find.jsx: the target finders of the recipe scripts ----------
# Two sequences hold a clip with the same node id "N1"; the active sequence is the second one, so a finder
# that walked the active sequence instead of the given one would give the wrong clip.
FIND_PRELUDE = STUB_HELPERS + """
function comp(display, match, tag, props) {
  return {displayName: display, matchName: match, tag: tag, properties: coll("numItems", props || [])};
}
function prop(name, tag) { return {displayName: name, tag: tag}; }
function fclip(id, name, comps) { return {nodeId: id, name: name, components: coll("numItems", comps || [])}; }
var lumetriProps = [];
for (var i = 0; i < 20; i++) lumetriProps.push(prop("P" + i, "p" + i));
lumetriProps[4] = prop(" ", "lut");
lumetriProps[14] = prop("Tint", "tint");
var graded = fclip("N1", "Graded", [comp("Opacity", "AE.ADBE Opacity", "c0"), comp("Motion", "AE.ADBE Motion", "c1"),
  comp("Lumetri Color", "AE.ADBE Lumetri", "c2", lumetriProps), comp("Lumetri Color", "AE.ADBE Lumetri", "c3", lumetriProps)]);
var seqA = {sequenceID: "S-A", name: "Cut A",
  videoTracks: coll("numTracks", [track([clip("V0", "Intro")]), track([clip("V1", "Title"), graded])]),
  audioTracks: coll("numTracks", [track([clip("A0", "Music"), clip("A9", "Room tone")])])};
var seqB = {sequenceID: "S-B", name: "Cut B",
  videoTracks: coll("numTracks", [track([fclip("N1", "Other clip")])]),
  audioTracks: coll("numTracks", [])};
var app = {project: {path: "/p/X Claude.prproj", sequences: coll("numSequences", [seqA, seqB]), activeSequence: seqB}};
"""
EXPECT = {"project_path": "/p/X Claude.prproj", "sequence_id": "S-A", "sequence_name": "Cut A"}


def run_find(body: str, args: dict | None = None, prelude: str = FIND_PRELUDE) -> dict:
    return run_in_node(jsx.build(body, args, include=("find",)), prelude)


def refused(reply: dict) -> str:
    """The text of a precondition refusal, without the "Error: " that String(e) puts in front."""
    assert reply["success"] is False, reply
    assert reply["error"].startswith("Error: precondition: "), reply
    return reply["error"][len("Error: precondition: "):]


def test_find_starts_with_its_comment_line():
    assert jsx.load("find").split("\n")[0] == "// find.jsx: target finders for recipe scripts"


@needs_node
def test_find_runs_nothing_when_it_loads():
    # Only definitions: with no app at all the script still loads and the body runs.
    assert run_in_node(jsx.build("return __ok(typeof __expect);", include=("find",))) == {"success": True, "data": "function"}


@needs_node
def test_expect_returns_the_sequence_or_null():
    body = "var seq = __expect(ARGS.expect);\nreturn __ok(seq ? seq.name : null);"
    assert run_find(body, {"expect": EXPECT})["data"] == "Cut A"
    assert run_find(body, {"expect": {**EXPECT, "sequence_name": None}})["data"] == "Cut A"
    assert run_find(body, {"expect": {**EXPECT, "sequence_id": None, "sequence_name": None}})["data"] is None


@needs_node
def test_expect_refuses_another_project():
    body = "__expect(ARGS.expect);\nreturn __ok(1);"
    other = {**EXPECT, "project_path": "/p/Y Claude.prproj"}
    assert refused(run_find(body, {"expect": other})) == "the active project is /p/X Claude.prproj, not /p/Y Claude.prproj"
    no_project = "var app = {project: null};"
    assert refused(run_find(body, {"expect": EXPECT}, no_project)) == "no saved project is active"
    assert refused(run_find(body, {})) == "the script got no expectation"


@needs_node
def test_expect_refuses_a_renamed_sequence():
    body = "__expect(ARGS.expect);\nreturn __ok(1);"
    renamed = {**EXPECT, "sequence_name": "Cut A old"}
    assert refused(run_find(body, {"expect": renamed})) == "sequence S-A is named Cut A, not Cut A old"
    gone = {**EXPECT, "sequence_id": "S-Z"}
    assert refused(run_find(body, {"expect": gone})) == "no sequence has the ID S-Z"


@needs_node
def test_sequence_by_id():
    body = "var s = __sequenceById(ARGS.id);\nreturn __ok(s ? s.name : null);"
    assert run_find(body, {"id": "S-B"})["data"] == "Cut B"
    assert run_find(body, {"id": "S-Z"})["data"] is None


@needs_node
def test_clip_by_index_and_by_node_id():
    body = ("var seq = __sequenceById(ARGS.seq);\nvar f = __clip(seq, ARGS.target);\n"
            "return __ok({name: f.clip.name, track_type: f.track_type, track: f.track, index: f.index});")
    assert run_find(body, {"seq": "S-A", "target": {"track": 1, "clip": 1}})["data"] == \
        {"name": "Graded", "track_type": "video", "track": 1, "index": 1}
    assert run_find(body, {"seq": "S-A", "target": {"track_type": "audio", "track": 0, "clip": 1}})["data"] == \
        {"name": "Room tone", "track_type": "audio", "track": 0, "index": 1}
    assert run_find(body, {"seq": "S-A", "target": {"node_id": "N1"}})["data"] == \
        {"name": "Graded", "track_type": "video", "track": 1, "index": 1}
    assert run_find(body, {"seq": "S-A", "target": {"node_id": "A9"}})["data"] == \
        {"name": "Room tone", "track_type": "audio", "track": 0, "index": 1}
    assert run_find(body, {"seq": "S-B", "target": {"node_id": "N1"}})["data"]["name"] == "Other clip"
    assert refused(run_find(body, {"seq": "S-B", "target": {"node_id": "V0"}})) == \
        "no clip has the node ID V0 in sequence Cut B"


@needs_node
@pytest.mark.parametrize("target, text", [
    ({"track": 2, "clip": 0}, "the sequence has no video track 2"),
    ({"track": 0, "clip": 1}, "video track 0 has no clip 1"),
    ({"track_type": "audio", "track": 1, "clip": 0}, "the sequence has no audio track 1"),
    ({"track_type": "titles", "track": 0, "clip": 0}, "track_type must be video or audio, not titles"),
    ({"track": -1, "clip": 0}, "track must be a whole number of 0 or more, not -1"),
    ({"track": 0, "clip": 0.5}, "clip must be a whole number of 0 or more, not 0.5"),
    ({"track": "0", "clip": 0}, "track must be a whole number of 0 or more, not 0"),
])
def test_clip_refuses_a_target_that_is_not_there(target, text):
    body = "__clip(__sequenceById('S-A'), ARGS.target);\nreturn __ok(1);"
    assert refused(run_find(body, {"target": target})) == text


@needs_node
def test_effect_twice_needs_effect_index():
    body = ("var clip = __clip(__sequenceById('S-A'), {track: 1, clip: 1}).clip;\n"
            "var e = __effect(clip, ARGS.name, ARGS.index);\nreturn __ok({index: e.index, tag: e.component.tag});")
    assert refused(run_find(body, {"name": "Lumetri Color", "index": None})) == \
        "ambiguous_effect: 2 effects are named Lumetri Color; pass effect_index"
    assert refused(run_find(body, {"name": "AE.ADBE Lumetri", "index": None})) == \
        "ambiguous_effect: 2 effects are named AE.ADBE Lumetri; pass effect_index"
    assert run_find(body, {"name": "Lumetri Color", "index": 3})["data"] == {"index": 3, "tag": "c3"}
    assert run_find(body, {"name": "AE.ADBE Lumetri", "index": 2})["data"] == {"index": 2, "tag": "c2"}
    assert run_find(body, {"name": "Motion", "index": None})["data"] == {"index": 1, "tag": "c1"}
    assert run_find(body, {"name": "AE.ADBE Opacity"})["data"] == {"index": 0, "tag": "c0"}
    assert refused(run_find(body, {"name": "Lumetri Color", "index": 1})) == \
        "effect 1 of this clip is Motion, not Lumetri Color"
    assert refused(run_find(body, {"name": "Lumetri Color", "index": 4})) == "this clip has no effect at index 4"
    assert refused(run_find(body, {"name": "ProcAmp", "index": None})) == "no effect named ProcAmp on this clip"
    assert refused(run_find(body, {"name": "lumetri color", "index": None})) == \
        "no effect named lumetri color on this clip"


@needs_node
def test_property_checks_the_name_at_the_index():
    body = ("var clip = __clip(__sequenceById('S-A'), {track: 1, clip: 1}).clip;\n"
            "var comp = __effect(clip, 'Lumetri Color', 2).component;\n"
            "return __ok(__property(comp, ARGS.index, ARGS.name).tag);")
    assert refused(run_find(body, {"index": 14, "name": "Temperature"})) == \
        "property 14 of Lumetri Color is named Tint, not Temperature"
    assert run_find(body, {"index": 14, "name": "Tint"})["data"] == "tint"
    assert run_find(body, {"index": 4, "name": " "})["data"] == "lut"           # a blank name, not trimmed
    assert refused(run_find(body, {"index": 4, "name": ""})) == "property 4 of Lumetri Color is named  , not "
    assert refused(run_find(body, {"index": 20, "name": "P20"})) == "Lumetri Color has no property 20"
    assert refused(run_find(body, {"index": 1.5, "name": "P1"})) == \
        "property index must be a whole number of 0 or more, not 1.5"


@needs_node
def test_ticks_is_a_string():
    body = "return __ok([__ticks({ticks: 254016000000}), __ticks({ticks: '10594584000'}), __ticks(null), __ticks({ticks: 'x'})]);"
    assert run_find(body)["data"] == ["254016000000", "10594584000", None, None]


def test_find_is_es3():
    text = jsx.load("find")
    for pattern in BANNED + [r"\.indexOf\(", r"\bObject\.", r"\.reduce\(", r"\.some\(", r"\.every\("]:
        assert not re.search(pattern, text), pattern
