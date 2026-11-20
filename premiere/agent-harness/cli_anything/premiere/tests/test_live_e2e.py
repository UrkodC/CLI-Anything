"""End-to-end tests of the installed command on a RUNNING Adobe Premiere Pro 27.

These tests are opt-in. They talk to the owner's Premiere through the panel's bridge folder, click its
Window > Mode menu, and write to a working copy of the fixture sequence, so they run only when ALL of
this is true:
  - the environment variable PREMIERE_LIVE_E2E is 1;
  - the MCP Bridge panel has a fresh heartbeat;
  - `live state` shows exactly one open project, named "fixture27 Claude.prproj".
Otherwise the whole module is skipped. The variable is checked first, before any contact with the
bridge folder, the panel or the command.

The one case that is not a skip: the variable is 1 and the heartbeat is fresh, but `live state` fails or
does not answer. That is a broken setup, not a missing one, so the module FAILS at collection (pytest
shows one collection error and runs nothing).

Run:  PREMIERE_LIVE_E2E=1 python3 -m pytest cli_anything/premiere/tests/test_live_e2e.py -q -s

Every command is run as a subprocess of the installed command with --json, with a time limit.
A command that does not finish in time gets SIGTERM first (the command then gives the bridge lease
back), and the test fails with a message instead of hanging.

The write tests use the module fixture `work_copy`: it duplicates "Fixture seq" with the premiere-pro-mcp
tools, renames the copy "E2E copy", and at the end of the module (also after a failed test) makes the
fixture sequence active again, deletes the copy by its ID and checks that "Fixture seq" is the only
sequence left and that its ProcAmp Brightness did not change. No test saves the project, opens one or
closes one. Because the fixture ends after the last test of the module, a failure of its end steps is
reported as an error under the name of the last test of the file.

The tests of `live frame export`, `live app` and `live lut set` also use the module fixture `app_state`
(it depends on `work_copy`, so it ends first). It makes "Fixture seq" the active sequence when it starts (the
state `work_copy` found), keeps that and the proxy display, and at the end of the module reads both again:
what differs is put back with `live app proxies` / `live app activate`, and the fixture fails with a message
that says which was left changed (it cannot say by which test).

Tests run in file order. The write tests come after the read-only ones, `test_mcp_tool_negative_control`
is the last of them (the tool may leave a recovery marker), the Plan 3 tests (keys, place, effect apply and
three measures) come just before it, and the last test checks the bridge folder
after everything else has run, so it must stay last in the file.
"""
from __future__ import annotations

import atexit
import contextlib
import functools
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pytest

FIXTURE_NAME = "fixture27 Claude.prproj"
SKIP_REASON = ("Premiere 27 with fixture27 Claude.prproj open and PREMIERE_LIVE_E2E=1 are needed "
               "(see docs/2026-11-18-premiere-cli-plan-1-frame.md, Task 8)")
CLI_TIMEOUT_S = 120
MCP_TIMEOUT_S = 180         # the premiere-pro-mcp server has to start before it answers
PLAN_TIMEOUT_S = 300        # a plan of six writes is six round trips plus the reads between them
TERM_GRACE_S = 20           # after SIGTERM, the command gets this long to give the lease back

# The opt-in switch comes first: with it unset nothing below touches the bridge, the panel or the command.
if os.environ.get("PREMIERE_LIVE_E2E") != "1":
    pytest.skip(SKIP_REASON, allow_module_level=True)

from cli_anything.premiere.core import bridge  # noqa: E402  (only needed once the switch is on)
from cli_anything.premiere.core.recipes.lut import frame_pixels, mean_difference  # noqa: E402
from cli_anything.premiere.errors import CliError  # noqa: E402

TIMEOUTS: list[str] = []    # every command that ran out of time in this run (Premiere may be blocked by a dialog)


@functools.lru_cache(maxsize=1)
def _cli() -> tuple[str, ...]:
    """The installed command; falls back to python -m for development (same as test_full_e2e.py)."""
    name = "cli-anything-premiere"
    path = shutil.which(name)
    if path:
        return (path,)
    if os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1":
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    return (sys.executable, "-m", "cli_anything.premiere.premiere_cli")


class CliTimeout(Exception):
    """The command did not finish in time."""

    def __init__(self, argv: list[str], timeout: float) -> None:
        super().__init__(f"{' '.join(argv)} did not finish in {timeout} s")
        self.argv = argv
        self.timeout = timeout


@dataclass
class Run:
    argv: list[str]
    code: int
    stdout: str
    stderr: str
    data: dict | None       # the JSON object on stdout, or None when stdout is not one

    def describe(self) -> str:
        return (f"command: {' '.join(self.argv)}\nexit status: {self.code}\n"
                f"stdout: {self.stdout[-2000:]}\nstderr: {self.stderr[-2000:]}")


def _exec(args: list[str], timeout: float) -> Run:
    """Run `cli-anything-premiere --json ARGS`. Raises CliTimeout when it does not finish in time."""
    argv = [*_cli(), "--json", *args]
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8")
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()                    # SIGTERM: the command cleans up and gives the lease back
        try:
            proc.communicate(timeout=TERM_GRACE_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                pass
        raise CliTimeout(argv, timeout) from None
    data = None
    try:
        parsed = json.loads(out)
        data = parsed if isinstance(parsed, dict) else None
    except ValueError:
        pass
    return Run(argv, proc.returncode, out, err, data)


def run_cli(*args: str, timeout: float = CLI_TIMEOUT_S) -> Run:
    """Like _exec, but a timeout fails the test with a clear message. After a timeout nothing more is sent: the
    next commands would only pile up on a Premiere that a dialog may block."""
    if TIMEOUTS:
        pytest.fail(f"Nothing was sent: an earlier command ran out of time ({TIMEOUTS[0]}). A dialog may be open in "
                    "Premiere: click it, then run the tests again.", pytrace=False)
    try:
        return _exec(list(args), timeout)
    except CliTimeout as exc:
        late = str(exc)
    TIMEOUTS.append(late)                   # the end steps of the module send nothing more after this
    pytest.fail(f"{late}. A dialog may be open in Premiere: click it, then run the tests again.", pytrace=False)


def _ok(run: Run) -> dict:
    """The JSON object of a command that exited 0 with "ok": true."""
    assert run.code == 0 and run.data is not None and run.data.get("ok") is True, run.describe()
    return run.data


def _failed(run: Run) -> dict:
    """The JSON object of a command that exited 1 with "ok": false."""
    assert run.code == 1 and run.data is not None and run.data.get("ok") is False, run.describe()
    return run.data


def _gate() -> dict:
    """The `live state` of the running Premiere, or a skip when this is not the expected setup.

    Skips: the panel heartbeat is not fresh, or the open project is not the fixture. FAILS (at collection):
    the heartbeat is fresh, so the setup was meant to be there, but `live state` did not answer or failed.
    """
    heartbeat = bridge.read_heartbeat(bridge.default_bridge_dir())
    if heartbeat is None or not heartbeat.fresh:
        pytest.skip(f"{SKIP_REASON}; the panel heartbeat is not fresh", allow_module_level=True)
    run = None
    try:
        run = _exec(["live", "state"], CLI_TIMEOUT_S)
    except CliTimeout:
        pass                                # the failure is raised below, outside this handler (no chained trace)
    if run is None:
        pytest.fail(f"PREMIERE_LIVE_E2E=1 and the panel heartbeat is fresh, but `live state` did not answer in "
                    f"{CLI_TIMEOUT_S} s. A dialog may be open in Premiere: click it, then run the tests again.",
                    pytrace=False)
    if run.code != 0 or run.data is None or run.data.get("ok") is not True:
        pytest.fail("PREMIERE_LIVE_E2E=1 and the panel heartbeat is fresh, but `live state` failed. The setup is "
                    f"broken, so this is a failure and not a skip.\n{run.describe()}", pytrace=False)
    projects = run.data.get("projects")
    if not isinstance(projects, list) or len(projects) != 1 or projects[0].get("name") != FIXTURE_NAME:
        names = [p.get("name") for p in projects] if isinstance(projects, list) else projects
        pytest.skip(f"{SKIP_REASON}; open projects: {names}", allow_module_level=True)
    return run.data


STATE0 = _gate()                        # `live state` as it was when the module was collected
BRIDGE_DIR = bridge.default_bridge_dir()


def _cleanup_problem(action) -> str | None:
    """Run a cleanup: None when all is well, else the text of what is still wrong (a raised failure too)."""
    try:
        return action()
    except (Exception, pytest.fail.Exception) as exc:
        return str(exc) or type(exc).__name__


def _after(first: BaseException | None, action) -> None:
    """Run a cleanup (call it in a `finally`) and keep the FIRST failure.

    `action` returns None when all is well, or the text of what is still wrong; it may also raise (a
    timeout of run_cli is a pytest.fail, which is not an Exception). `first` is the failure the test is
    already carrying, or None. With a first failure, the cleanup problem is printed under it and the
    first failure goes on. Without one, the problem fails the test.
    """
    problem = _cleanup_problem(action)
    if problem:
        print(f"\nCLEANUP PROBLEM: {problem}")
        if first is None:
            pytest.fail(problem, pytrace=False)


def test_doctor_has_no_failure():
    data = _ok(run_cli("doctor"))
    assert data["summary"]["fail"] == 0, json.dumps(data["checks"], indent=2)


def test_live_state_reads_the_running_app():
    state = _ok(run_cli("live", "state"))
    assert isinstance(state["version"], str) and state["version"].startswith("27.0."), state["version"]
    assert isinstance(state["build"], str) and re.fullmatch(r"[0-9]+", state["build"]), state["build"]
    assert len(state["projects"]) == 1, state["projects"]
    seq = state["active_sequence"]
    assert isinstance(seq, dict) and seq.get("id") and seq.get("name"), seq


def test_live_run_read_script(tmp_path):
    script = tmp_path / "read_version.jsx"
    script.write_text("return __ok({v: app.version, n: ARGS.n});\n", encoding="utf-8")     # a body only, ES3
    data = _ok(run_cli("live", "run", str(script), "--args", '{"n": -2.5}'))
    assert data["data"] == {"v": STATE0["version"], "n": -2.5}, data
    assert data["stored"] is None


def test_live_run_script_error(tmp_path):
    thrown = "e2e-thrown-text-7f3a"        # distinctive: it must come back in `error`, not just any message
    script = tmp_path / "throws.jsx"
    script.write_text(f'throw new Error("{thrown}");\n', encoding="utf-8")
    data = _failed(run_cli("live", "run", str(script)))
    assert data["code"] == "script_failed", data
    assert thrown in data["error"], data
    # A script that throws is a read that failed cleanly: it must not leave a recovery marker.
    assert bridge.read_recovery(BRIDGE_DIR) is None


def test_recovery_show_is_quiet():
    data = _ok(run_cli("recovery", "show"))
    assert data["recovery_required"] is False, data


def test_mcp_ping():
    data = _ok(run_cli("mcp", "call", "ping", timeout=MCP_TIMEOUT_S))
    assert data["stored"] is None and data["rendered"] is None, data
    result = data["result"]                 # always an object: {"ok", "tool", "data": {...}}
    assert isinstance(result, dict) and result["ok"] is True, result
    ping = result["data"]
    assert ping["connected"] is True, result
    version = ping["premiereVersion"]
    assert isinstance(version, str) and version.startswith("27.0."), result
    assert ping["projectName"] == FIXTURE_NAME, result


def test_mcp_list_has_many_tools():
    data = _ok(run_cli("mcp", "list", timeout=MCP_TIMEOUT_S))
    names = [tool["name"] for tool in data["tools"]]
    assert data["count"] == len(names)
    assert data["count"] > 400, data["count"]
    assert "set_effect_property" in names


def _put_mode_back(mode: str) -> None:
    """Window > Mode back to `mode`. Does nothing when Premiere is in it already."""
    try:
        now = _exec(["ui", "mode", "get"], CLI_TIMEOUT_S)
        if now.code == 0 and now.data is not None and now.data.get("mode") == mode:
            return
    except CliTimeout:
        pass
    back = run_cli("ui", "mode", "set", mode)
    if back.code != 0 or back.data is None or back.data.get("ok") is not True:
        pytest.fail(f"Premiere was NOT put back to the {mode} mode. Set Window > Mode > {mode.capitalize()} "
                    f"by hand.\n{back.describe()}", pytrace=False)


def test_ui_mode_round_trip():
    m0 = _ok(run_cli("ui", "mode", "get"))["mode"]
    other = "edit" if m0 != "edit" else "color"
    failure = None
    try:
        changed = _ok(run_cli("ui", "mode", "set", other))
        assert changed["mode"] == other, changed
        # An independent read of the menu: `set` answers from its own reading, so this is a second opinion.
        assert _ok(run_cli("ui", "mode", "get"))["mode"] == other
        back = _ok(run_cli("ui", "mode", "set", m0))
        assert back["mode"] == m0, back
        assert _ok(run_cli("ui", "mode", "get"))["mode"] == m0
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _after(failure, lambda: _put_mode_back(m0))     # also when an assertion above failed; the first failure stays


# =====================================================================================================
# The working copy: every write below goes to "E2E copy", never to "Fixture seq"
# =====================================================================================================
FIXTURE_SEQ = "Fixture seq"
COPY_NAME = "E2E copy"
DUPLICATE_NAME = "Fixture seq Copy"     # what Premiere calls a copy of "Fixture seq" before it is renamed
PROCAMP = "ProcAmp"
PROCAMP_MATCH = "AE.ADBE ProcAmp"
LUMETRI = "Lumetri Color"
TOLERANCE = 0.001                       # the CLI's own rule for "stored"

# ExtendScript (ES3: no let, const, arrow functions, template strings or JSON.*). A `live run` script is a
# BODY: ARGS, __ok and __fail exist, and find.jsx is NOT included, so each script finds its target itself.
# FIND_PROPERTY_JS and WRITE_PROPERTY_BODY are copied from tests/gates/run_gates.py, where they ran on Premiere 27.
# ARGS: sequence_id, track, clip, match_name, index, display_name, value.
FIND_PROPERTY_JS = r"""
var project = app.project;
if (!project) return __fail("precondition: no project is open");
var seq = null;
for (var i = 0; i < project.sequences.numSequences; i++) {
  var candidate = project.sequences[i];
  if (String(candidate.sequenceID) === String(ARGS.sequence_id)) { seq = candidate; break; }
}
if (!seq) return __fail("precondition: no sequence has the ID " + ARGS.sequence_id);
if (ARGS.track >= seq.videoTracks.numTracks) return __fail("precondition: the sequence has no video track " + ARGS.track);
var track = seq.videoTracks[ARGS.track];
if (ARGS.clip >= track.clips.numItems) return __fail("precondition: video track " + ARGS.track + " has no clip " + ARGS.clip);
var clip = track.clips[ARGS.clip];
var comp = null;
for (var c = 0; c < clip.components.numItems; c++) {
  if (clip.components[c].matchName === ARGS.match_name) { comp = clip.components[c]; break; }
}
if (!comp) return __fail("precondition: the clip has no component " + ARGS.match_name);
if (ARGS.index >= comp.properties.numItems) return __fail("precondition: " + ARGS.match_name + " has no property " + ARGS.index);
var prop = comp.properties[ARGS.index];
var shown = String(prop.displayName);
if (shown !== ARGS.display_name) return __fail("precondition: property " + ARGS.index + " of " + ARGS.match_name + " is " + shown + ", not " + ARGS.display_name);
"""

WRITE_PROPERTY_BODY = FIND_PROPERTY_JS + r"""
if (typeof prop.isTimeVarying === "function") {
  try {
    if (prop.isTimeVarying()) return __fail("precondition: " + shown + " has keyframes; nothing was changed");
  } catch (eVarying) {
    return __fail("precondition: the animation state of " + shown + " could not be read; nothing was changed");
  }
}
var before = null;
try { before = prop.getValue(); } catch (eBefore) {}
var setError = null;
try { prop.setValue(ARGS.value, true); } catch (eSet) { setError = eSet.toString(); }
var after = null;
var readError = null;
try { after = prop.getValue(); } catch (eRead) { readError = eRead.toString(); }
return __ok({
  sequence_id: String(seq.sequenceID),
  clip_node_id: String(clip.nodeId),
  clip_name: String(clip.name),
  property: shown,
  written: ARGS.value,
  before: before,
  value: after,
  set_error: setError,
  read_error: readError
});
"""

# The check script: the same finder, a read of the value, and {stored: true | false}. A read that throws, or a
# value that is not a finite number, is stored: false (the check does not fail as a script).
VERIFY_PROPERTY_BODY = FIND_PROPERTY_JS + r"""
var value = null;
var readError = null;
try { value = prop.getValue(); } catch (eRead) { readError = eRead.toString(); }
var stored = false;
if (typeof value === "number" && isFinite(value)) stored = Math.abs(value - ARGS.value) <= 0.001;
return __ok({stored: stored, value: value, read_error: readError});
"""


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _near(a: object, b: object, tol: float = TOLERANCE) -> bool:
    return _is_number(a) and _is_number(b) and abs(a - b) <= tol


def _marker_open() -> bool:
    return bridge.read_recovery(BRIDGE_DIR) is not None


def _live_state() -> dict:
    return _ok(run_cli("live", "state"))


def _active_id() -> str | None:
    seq = _live_state()["active_sequence"]
    return str(seq["id"]) if isinstance(seq, dict) and seq.get("id") else None


def _tool_run(tool: str, args: dict | None = None) -> Run:
    """`mcp call TOOL --args JSON`."""
    argv = ["mcp", "call", tool]
    if args is not None:
        argv += ["--args", json.dumps(args)]
    return run_cli(*argv, timeout=MCP_TIMEOUT_S)


def _tool(tool: str, args: dict | None = None):
    """The `data` of a tool that answered ok. `mcp call` prints {"ok", "tool", "result", "stored", "rendered"}
    and `result` is the tool's own reply: {"ok": true, "tool", "data", "receipt"?}."""
    run = _tool_run(tool, args)
    data = _ok(run)
    assert data["tool"] == tool and data["stored"] is None and data["rendered"] is None, run.describe()
    result = data["result"]
    assert isinstance(result, dict) and result.get("ok") is True and "data" in result, run.describe()
    return result["data"]


def _sequences() -> list[dict]:
    """`list_sequences` as [{"name", "id"}] (the id as text), in the order Premiere lists them."""
    listed = _tool("list_sequences")
    assert isinstance(listed, list) and all(isinstance(s, dict) and isinstance(s.get("name"), str) and s.get("id")
                                            for s in listed), f"list_sequences gave an unexpected reply: {listed!r}"
    return [{"name": s["name"], "id": str(s["id"])} for s in listed]


def _effect_get(seq_id: str, effect: str, *, track: int = 0, clip: int = 0) -> dict:
    return _ok(run_cli("live", "effect", "get", "--sequence", seq_id, "--track", str(track), "--clip", str(clip),
                       "--effect", effect))


def _props(payload: dict, effect: str) -> dict[int, dict]:
    """The properties of the one effect `payload` holds, by index."""
    effects = payload["effects"]
    assert len(effects) == 1 and effects[0]["name"] == effect, \
        f"expected one effect named {effect!r}, got {[(e['effect_index'], e['name']) for e in effects]}"
    props = effects[0]["properties"]
    assert isinstance(props, list) and props, f"{effect} has no readable properties"
    by_index = {p["index"]: p for p in props}
    assert len(by_index) == len(props), f"two properties of {effect} share an index"
    return by_index


def _value(seq_id: str, effect: str, index: int, name: str, *, track: int = 0, clip: int = 0) -> float:
    """The number `live effect get` reads at effect[index], after it checked the name, the kind and the keyframes."""
    prop = _props(_effect_get(seq_id, effect, track=track, clip=clip), effect)[index]
    assert prop["name"] == name and prop["kind"] == "number" and prop["keyframed"] is False, prop
    return prop["value"]


def _snapshot(seq_id: str, *effects: str) -> dict[str, dict[int, dict]]:
    """Every property of each effect on V1 clip 0, to compare before and after a command that must change nothing."""
    return {effect: _props(_effect_get(seq_id, effect), effect) for effect in effects}


def _first_clip_node(seq_id: str) -> str:
    """The node ID of V1 clip 0 of a sequence, from `live clip list`."""
    clips = _ok(run_cli("live", "clip", "list", "--sequence", seq_id, "--track-type", "video", "--track", "0"))["clips"]
    assert clips and clips[0]["track_type"] == "video" and clips[0]["track"] == 0 and clips[0]["clip"] == 0, clips
    node = clips[0]["node_id"]
    assert isinstance(node, str) and node, clips[0]
    return node


def _set_args(copy: dict, effect: str, index: int, name: str, value: object, *, track: int = 0, clip: int = 0,
              node_id: str | None = None, expect_old: object = None, dry_run: bool = False) -> list[str]:
    """`live effect set` for one write on the copy. The value goes in as JSON: -2.6, "text", true."""
    where = ["--node-id", node_id] if node_id is not None else ["--track", str(track), "--clip", str(clip)]
    args = ["live", "effect", "set", "--sequence", copy["id"], "--sequence-name", copy["name"], *where,
            "--effect", effect, "--index", str(index), "--name", name, "--value", json.dumps(value)]
    if expect_old is not None:
        args += ["--expect-old", json.dumps(expect_old)]
    if dry_run:
        args.append("--dry-run")
    return args


def _write_ok(copy: dict, effect: str, index: int, name: str, value: object, **options) -> dict:
    """One write that must be stored. Returns its record of `results`."""
    data = _ok(run_cli(*_set_args(copy, effect, index, name, value, **options)))
    assert data["ran"] == 1 and data["total"] == 1 and data["stored"] is True, data
    assert data["rendered"] is None, data
    result = data["results"][0]
    assert result["entry"] == 0 and result["stored"] is True and result["landed"] is None, result
    assert result["requested"] == value and _near(result["value"], value), result
    return result


def _refusal(run: Run, code: str, fragment: str) -> dict:
    """A write that was refused before anything was sent: exit 1, this code, this reason in `error`, nothing
    ran, and no recovery marker."""
    data = _failed(run)
    assert data["code"] == code and fragment in data["error"], run.describe()
    assert data["ran"] == 0 and data["results"] == [], run.describe()
    assert not _marker_open(), f"a recovery marker was left by a refused write.\n{run.describe()}"
    return data


def _write_plan(tmp_path: Path, copy: dict, writes: list[dict]) -> Path:
    """A plan file. `--from` does not take --sequence, so the sequence ID and name are inside the file."""
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"sequence_id": copy["id"], "sequence_name": copy["name"], "writes": writes}),
                    encoding="utf-8")
    return path


def _entry(effect: str, index: int, name: str, value: object) -> dict:
    return {"track": 0, "clip": 0, "effect": effect, "index": index, "name": name, "value": value}


# ---------- the module fixture ----------
def _start_check() -> dict:
    """What Premiere holds before the module makes anything. pytest.fail when that is not the expected fixture:
    nothing was made yet, so there is nothing to clean up."""
    marker = bridge.read_recovery(BRIDGE_DIR)
    if marker is not None:
        pytest.fail(f"A recovery marker is open ({marker['reason']}). Run: cli-anything-premiere recovery show. "
                    "Nothing was changed.", pytrace=False)
    if TIMEOUTS:
        pytest.fail(f"An earlier test of this run ran out of time ({TIMEOUTS[0]}). A dialog may be open in Premiere: "
                    "click it, then run the tests again. Nothing was changed.", pytrace=False)
    state = _live_state()
    names = [p.get("name") for p in state["projects"]]
    if names != [FIXTURE_NAME]:
        pytest.fail(f"The open projects are {names}, not only {FIXTURE_NAME}. Nothing was changed.", pytrace=False)
    seqs = _sequences()
    stale = [s for s in seqs if s["name"] in (COPY_NAME, DUPLICATE_NAME)]
    if stale:
        pytest.fail("A sequence from an earlier run is in the project: "
                    + ", ".join(f"{s['name']!r} ({s['id']})" for s in stale)
                    + ". Delete it by hand in Premiere (this module deletes nothing it did not make), then run the "
                      "tests again. Nothing was changed.", pytrace=False)
    fixture = [s for s in seqs if s["name"] == FIXTURE_SEQ]
    if len(fixture) != 1 or len(seqs) != 1:
        pytest.fail(f"The project should hold only the sequence {FIXTURE_SEQ!r}, and it holds "
                    f"{[(s['name'], s['id']) for s in seqs]}. Nothing was changed.", pytrace=False)
    fixture_id = fixture[0]["id"]
    active = state["active_sequence"]
    if not isinstance(active, dict) or str(active.get("id")) != fixture_id:
        pytest.fail(f"The active sequence is {active}, not {FIXTURE_SEQ!r} ({fixture_id}). Make {FIXTURE_SEQ!r} the "
                    "active sequence, then run the tests again. Nothing was changed.", pytrace=False)
    brightness = _value(fixture_id, PROCAMP, 0, "Brightness")      # kept: it must be the same at the end
    return {"fixture_id": fixture_id, "fixture_name": FIXTURE_SEQ, "brightness": brightness}


def _make_copy(start: dict, created: dict) -> dict:
    """Duplicate the fixture sequence, rename the copy, and find it by ID. `created` gets the copy's ID and its
    current name as soon as Premiere has made it, so the end steps know what to remove if a later step fails."""
    fixture_id = start["fixture_id"]
    dup = _tool("duplicate_sequence", {"sequence_id": fixture_id})
    if isinstance(dup, dict) and dup.get("id") and isinstance(dup.get("name"), str):
        created.update(id=str(dup["id"]), name=dup["name"])
    assert isinstance(dup, dict) and dup.get("duplicated") is True and dup.get("verified") is True \
        and "id" in created, f"duplicate_sequence gave an unexpected reply: {dup!r}"
    assert str(dup.get("originalId")) == fixture_id and created["id"] != fixture_id, dup
    assert created["name"] != start["fixture_name"], dup
    renamed = _tool("rename_project_item", {"item_id": created["name"], "new_name": COPY_NAME})
    assert isinstance(renamed, dict) and renamed.get("newName") == COPY_NAME \
        and renamed.get("sequenceVerified") is True, f"rename_project_item gave an unexpected reply: {renamed!r}"
    print(f"work copy: duplicate_sequence made {created['name']!r} ({created['id']}); renamed from "
          f"{renamed.get('oldName')!r} to {COPY_NAME!r}")
    created["name"] = COPY_NAME
    named = [s for s in _sequences() if s["name"] == COPY_NAME]
    assert len(named) == 1, f"expected one sequence named {COPY_NAME!r}, got {named}"
    assert named[0]["id"] != fixture_id, named
    assert named[0]["id"] == created["id"], (f"the sequence named {COPY_NAME!r} is {named[0]['id']}, but "
                                             f"duplicate_sequence made {created['id']}")
    return {"id": created["id"], "name": COPY_NAME, "fixture_id": fixture_id, "fixture_name": start["fixture_name"]}


def _no_contact_reason() -> str | None:
    """Why the end steps must send nothing to Premiere: a recovery marker is open (a command's outcome is
    unknown), or a command timed out (a dialog may block Premiere)."""
    marker = bridge.read_recovery(BRIDGE_DIR)
    if marker is not None:
        return f"a recovery marker is open ({marker['reason']})"
    if TIMEOUTS:
        return f"a command ran out of time ({TIMEOUTS[0]})"
    return None


def _end_check(start: dict, created: dict) -> str | None:
    """The end of the module. None when Premiere is as it was found, else everything that is not."""
    fixture_id = start["fixture_id"]
    reason = _no_contact_reason()
    if reason:
        left = (f"the sequence {created['name']!r} ({created['id']})" if created.get("id")
                else "a sequence this run made may be there (its ID was not read)")
        return (f"The module cleaned up nothing, because {reason}: it sent nothing to Premiere. Left in Premiere: "
                f"{left}; the active sequence may not be {FIXTURE_SEQ!r}. Run: cli-anything-premiere recovery show")
    problems: list[str] = []

    @contextlib.contextmanager
    def step(what: str):
        try:
            yield
        except (Exception, pytest.fail.Exception) as exc:       # run_cli fails with pytest.fail on a timeout
            problems.append(f"{what} failed: {str(exc)[-1500:]}")

    with step(f"Making {FIXTURE_SEQ!r} the active sequence again"):
        active = _active_id()
        if active != fixture_id:
            reply = _tool("set_active_sequence", {"sequence_id": fixture_id})
            assert isinstance(reply, dict) and reply.get("active") is True and str(reply.get("id")) == fixture_id, reply
            active = _active_id()
            assert active == fixture_id, f"`live state` shows the active sequence {active}, not {fixture_id}"
    with step(f"Deleting {COPY_NAME!r}"):
        mine = [s for s in _sequences() if created.get("id") and s["id"] == created["id"]]
        if mine and mine[0]["name"] != COPY_NAME:
            problems.append(f"NOT deleted: the sequence {created['id']} is named {mine[0]['name']!r}, not "
                            f"{COPY_NAME!r}. It is still in Premiere: delete it by hand.")
        elif mine:
            reply = _tool("delete_sequence", {"sequence_id": created["id"]})
            assert isinstance(reply, dict) and reply.get("deleted") is True and reply.get("verified") is True \
                and str(reply.get("id")) == created["id"], reply
    with step("Reading the sequence list at the end"):
        held = [(s["name"], s["id"]) for s in _sequences()]
        if held != [(FIXTURE_SEQ, fixture_id)]:
            problems.append(f"Premiere holds {held} and should hold only {[(FIXTURE_SEQ, fixture_id)]}. "
                            "What is not the fixture is still in Premiere: delete it by hand.")
    with step(f"Reading the ProcAmp Brightness of {FIXTURE_SEQ!r} again"):
        now = _value(fixture_id, PROCAMP, 0, "Brightness")
        if not _near(now, start["brightness"]):
            problems.append(f"The ProcAmp Brightness of {FIXTURE_SEQ!r} V1 clip 0 is {now}, and it was "
                            f"{start['brightness']} at the start: a test wrote to the fixture.")
    with step("Looking at the bridge folder"):
        leftovers = _wait_clean()
        if leftovers:
            problems.append(f"left in {BRIDGE_DIR}: {leftovers}")
    return "The end of the module found a problem:\n" + "\n".join(problems) if problems else None


@pytest.fixture(scope="module")
def work_copy():
    """A copy of "Fixture seq", renamed "E2E copy": yields {"id", "name", "fixture_id", "fixture_name"}.

    Start: one project (the fixture), only "Fixture seq" in it (a leftover "E2E copy" or "Fixture seq Copy"
    fails the module and nothing is deleted), "Fixture seq" active, and its ProcAmp Brightness kept.
    End (always): see _end_check. Never saves, opens or closes a project.
    """
    start = _start_check()
    created: dict = {}
    setup_failed = None
    try:
        info = _make_copy(start, created)
    except BaseException as exc:
        # A failure of a test never enters this generator, so this is a failure of the SETUP. What it left in
        # Premiere goes into the failure message itself (the printed line alone is easy to miss).
        left = _cleanup_problem(lambda: _end_check(start, created))
        if left:
            print(f"\nCLEANUP PROBLEM: {left}")
        if not left or not isinstance(exc, (Exception, pytest.fail.Exception)):
            raise
        setup_failed = f"The setup of the working copy failed: {str(exc) or type(exc).__name__}\n\n{left}"
    if setup_failed:
        pytest.fail(setup_failed, pytrace=False)       # outside the except block: no chained trace
    try:
        yield info
    finally:
        _after(None, lambda: _end_check(start, created))


# =====================================================================================================
# The tests on the copy, in the order they run
# =====================================================================================================
def test_live_run_write_verify_through_the_command(work_copy, tmp_path):
    """live run SCRIPT --write --verify CHECK: a write script sets ProcAmp Brightness to 4, a check script
    reads it back. Output keys: ok, data, stored, rendered, warnings."""
    write = tmp_path / "write_brightness.jsx"
    check = tmp_path / "check_brightness.jsx"
    write.write_text(WRITE_PROPERTY_BODY, encoding="utf-8")
    check.write_text(VERIFY_PROPERTY_BODY, encoding="utf-8")
    args = {"sequence_id": work_copy["id"], "track": 0, "clip": 0, "match_name": PROCAMP_MATCH, "index": 0,
            "display_name": "Brightness", "value": 4}
    data = _ok(run_cli("live", "run", str(write), "--write", "--verify", str(check), "--args", json.dumps(args)))
    assert data["stored"] is True and data["rendered"] is None, data
    written = data["data"]
    assert written["sequence_id"] == work_copy["id"] and written["property"] == "Brightness", written
    assert written["set_error"] is None and written["read_error"] is None, written
    assert _near(written["value"], 4), written
    assert not _marker_open()
    # An independent read through another command.
    assert _near(_value(work_copy["id"], PROCAMP, 0, "Brightness"), 4)


def test_clip_list_of_the_copy(work_copy):
    """live clip list --sequence COPY. Output keys: sequence {id, name}, clips[] (track_type, track, clip,
    node_id, name, start_seconds, end_seconds, effects, render_order, procamp_before_lumetri, source), count,
    total, truncated."""
    data = _ok(run_cli("live", "clip", "list", "--sequence", work_copy["id"]))
    assert data["sequence"] == {"id": work_copy["id"], "name": COPY_NAME}, data["sequence"]
    assert data["truncated"] is False and data["count"] == data["total"] == len(data["clips"]), data
    video = [c for c in data["clips"] if c["track_type"] == "video"]
    assert [(c["track"], c["clip"]) for c in video] == [(0, 0), (0, 1)], [(c["track"], c["clip"]) for c in video]
    nodes = [c["node_id"] for c in video]
    assert all(isinstance(n, str) and n for n in nodes) and len(set(nodes)) == 2, nodes
    fixture = _ok(run_cli("live", "clip", "list", "--sequence", work_copy["fixture_id"], "--track-type", "video"))
    fixture_nodes = [c["node_id"] for c in fixture["clips"]]
    print(f"clip list: copy V1 node IDs {nodes}, fixture V1 node IDs {fixture_nodes}")
    assert set(nodes).isdisjoint(fixture_nodes), f"the copy shares a clip node ID with the fixture: {nodes} {fixture_nodes}"
    first, second = video
    assert _near(first["start_seconds"], 0) and _near(first["end_seconds"], 6.006), first
    assert _near(second["start_seconds"], 6.006) and _near(second["end_seconds"], 8.008), second
    assert "testclip.mp4" in (first["name"], first["source"]["name"]), (first["name"], first["source"])
    order = first["render_order"]
    print(f"clip list: V1 clip 0 effects {first['effects']}, render_order {order}")
    assert order and order[0] == PROCAMP, order
    assert [n for n in order if n in (PROCAMP, "ASC CDL", LUMETRI)] == [PROCAMP, "ASC CDL", LUMETRI], order
    assert first["procamp_before_lumetri"] is True, first


def test_effect_get_matches_the_known_values(work_copy):
    """live effect get --sequence COPY --track 0 --clip 0 --effect ProcAmp | "Lumetri Color". Output keys:
    clip {sequence_id, track_type, track, clip, node_id, name}, effects[] {effect_index, name, match_name,
    properties[] {index, name, value, kind, keyframed}}. Values are those of the fixture: this runs before
    any test writes to them (only the write/verify test ran, and it wrote Brightness)."""
    copy_id = work_copy["id"]
    got = _effect_get(copy_id, PROCAMP)
    assert got["clip"]["sequence_id"] == copy_id and got["clip"]["track_type"] == "video", got["clip"]
    assert (got["clip"]["track"], got["clip"]["clip"]) == (0, 0), got["clip"]
    assert got["effects"][0]["match_name"] == PROCAMP_MATCH, got["effects"]
    print(f"effect get: ProcAmp is effect_index {got['effects'][0]['effect_index']}")
    pro = _props(got, PROCAMP)
    assert [pro[i]["name"] for i in range(4)] == ["Brightness", "Contrast", "Hue", "Saturation"], pro
    assert pro[0]["kind"] == "number", pro[0]
    assert _near(pro[1]["value"], 100) and _near(pro[3]["value"], 100), (pro[1], pro[3])
    lum_payload = _effect_get(copy_id, LUMETRI)
    assert lum_payload["effects"][0]["match_name"] == "AE.ADBE Lumetri", lum_payload["effects"][0]["match_name"]
    print(f"effect get: Lumetri Color is effect_index {lum_payload['effects'][0]['effect_index']}")
    lum = _props(lum_payload, LUMETRI)
    print(f"effect get: Lumetri Color has {len(lum)} properties")
    assert len(lum) >= 35, len(lum)                  # indexes 32 and 34 must exist for the refusal test
    names = {i: lum[i]["name"] for i in (6, 7, 14, 15, 16, 19, 20, 24)}
    assert names == {6: "Input LUT", 7: "Input LUT", 14: "Temperature", 15: "Tint", 16: "Saturation",
                     19: "Exposure", 20: "Contrast", 24: "Blacks"}, names
    all_names = [p["name"] for p in lum.values()]
    counts = {name: all_names.count(name) for name in ("Temperature", "Saturation", "Contrast")}
    print(f"effect get: Lumetri names that occur more than once: {counts}")
    assert all(count >= 2 for count in counts.values()), f"these names should not be unique in Lumetri Color: {counts}"
    assert lum[4]["name"].strip() == "" and lum[4]["kind"] == "string", lum[4]
    print(f"effect get: Lumetri index 4 is kind {lum[4]['kind']}, value {lum[4]['value']!r}")
    assert _near(lum[14]["value"], -46.5) and _near(lum[15]["value"], 8.8), (lum[14], lum[15])
    assert _near(lum[16]["value"], 100) and _near(lum[19]["value"], 0), (lum[16], lum[19])


def test_effect_get_motion_position_is_an_array(work_copy):
    props = _props(_effect_get(work_copy["id"], "Motion"), "Motion")
    positions = [p for p in props.values() if p["name"] == "Position"]
    assert len(positions) == 1, [p["name"] for p in props.values()]
    position = positions[0]
    print(f"effect get: Motion Position is {position['value']!r} (kind {position['kind']})")
    assert position["kind"] == "array", position
    assert isinstance(position["value"], list) and len(position["value"]) == 2 \
        and all(_is_number(v) for v in position["value"]), position


def test_clip_list_detail_runs(work_copy):
    data = _ok(run_cli("live", "clip", "list", "--sequence", work_copy["id"], "--detail"))
    assert data["clips"], data
    for clip in data["clips"]:
        for key in ("interpreted_fps", "linked", "keyframed", "transitions"):
            assert key in clip, (key, clip)
        label = f"{'V' if clip['track_type'] == 'video' else 'A'}{clip['track'] + 1}[{clip['clip']}]"
        print(f"clip list --detail: {label} interpreted_fps={clip['interpreted_fps']!r} linked={clip['linked']!r} "
              f"keyframed={clip['keyframed']!r} transitions={clip['transitions']!r}")


def test_effect_get_lumetri_is_fast_enough(work_copy):
    started = time.monotonic()
    got = _effect_get(work_copy["id"], LUMETRI)
    seconds = time.monotonic() - started
    count = len(got["effects"][0]["properties"])
    print(f"effect get --effect 'Lumetri Color': {seconds:.1f} s for {count} properties")
    assert seconds < 20, f"live effect get of Lumetri Color took {seconds:.1f} s ({count} properties)"


def test_effect_set_dry_run_changes_nothing(work_copy, tmp_path):
    """live effect set --dry-run. Output keys: dry_run, sequence {id, name}, resolved[] (entry, clip, effect,
    effect_index, index, name, value_now), planned[] (entry, value_now, requested, crosses_zero), warnings.
    It has no stored, results or ran."""
    copy_id = work_copy["id"]
    before = _snapshot(copy_id, PROCAMP, LUMETRI)
    brightness = before[PROCAMP][0]["value"]
    data = _ok(run_cli(*_set_args(work_copy, PROCAMP, 0, "Brightness", -2.6, dry_run=True)))
    assert data["dry_run"] is True and data["sequence"] == {"id": copy_id, "name": COPY_NAME}, data
    assert not {"results", "stored", "ran"} & set(data), data
    [found] = data["resolved"]
    assert found["entry"] == 0 and found["effect"] == PROCAMP and found["index"] == 0 \
        and found["name"] == "Brightness", found
    assert (found["clip"]["track"], found["clip"]["clip"]) == (0, 0) and found["clip"]["node_id"], found
    assert found["value_now"] == brightness, (found, brightness)
    [planned] = data["planned"]
    assert planned == {"entry": 0, "value_now": brightness, "requested": -2.6, "crosses_zero": brightness > 0}, planned
    plan = _write_plan(tmp_path, work_copy, [_entry(LUMETRI, 14, "Temperature", -20), _entry(PROCAMP, 1, "Contrast", 90)])
    data = _ok(run_cli("live", "effect", "set", "--from", str(plan), "--dry-run"))
    assert data["dry_run"] is True and len(data["resolved"]) == 2 and len(data["planned"]) == 2, data
    assert [r["effect"] for r in data["resolved"]] == [LUMETRI, PROCAMP], data["resolved"]
    assert not {"results", "stored", "ran"} & set(data), data
    after = _snapshot(copy_id, PROCAMP, LUMETRI)
    assert after == before, "a dry run changed a value"
    assert not _marker_open()


def test_effect_set_wrong_name_is_refused(work_copy):
    """Index 14 of Lumetri Color is Temperature, so the name Tint is refused: exit 1, code precondition, `error`
    names the real property, nothing ran (ran 0, results []), no recovery marker, no value changed."""
    before = _snapshot(work_copy["id"], LUMETRI)
    run = run_cli(*_set_args(work_copy, LUMETRI, 14, "Tint", 1))
    data = _refusal(run, "precondition", "named Temperature")
    print(f"wrong name: error {data['error']!r}")
    assert _snapshot(work_copy["id"], LUMETRI) == before, "a refused write changed a value"


def test_effect_set_refuses_lumetri_index_7(work_copy):
    """Lumetri Color index 7 (the second Input LUT) is never written: code refused_parameter. The write sent
    is the value index 7 holds now, so that a refusal that failed to hold would still change nothing."""
    copy_id = work_copy["id"]
    before = _snapshot(copy_id, LUMETRI)
    prop = before[LUMETRI][7]
    name = prop["name"] if isinstance(prop["name"], str) else ""
    value = prop["value"] if prop["kind"] in ("number", "string", "boolean") else 0
    print(f"index 7: name {prop['name']!r}, kind {prop['kind']}, value {value!r}")
    run = run_cli(*_set_args(work_copy, LUMETRI, 7, name, value))
    data = _refusal(run, "refused_parameter", "never written")
    assert "index 7" in data["error"], data["error"]
    assert _snapshot(copy_id, LUMETRI) == before, "a refused write changed a value"


def test_effect_set_refuses_an_audio_clip_by_node_id(work_copy):
    """A node ID can name an audio clip, and effect set writes video clips only: code precondition, `error` says
    audio track, no recovery marker."""
    audio = _ok(run_cli("live", "clip", "list", "--sequence", work_copy["id"], "--track-type", "audio"))["clips"]
    assert audio and audio[0]["track_type"] == "audio" and audio[0]["node_id"], audio
    node = audio[0]["node_id"]
    print(f"audio clip: node ID {node}, effects {audio[0]['effects']}")
    run = run_cli(*_set_args(work_copy, "Volume", 0, "Bypass", False, node_id=node))
    data = _refusal(run, "precondition", "audio track")
    print(f"audio clip: error {data['error']!r}")


def test_effect_set_one_negative_value(work_copy):
    """Brightness to 5, then -2.6 (it crosses zero). Output keys of the second write: ran, total, stored,
    rendered, results[0] (entry, requested, before, value, stored, attempts, zero_first, landed, restored,
    now, error)."""
    _write_ok(work_copy, PROCAMP, 0, "Brightness", 5)
    result = _write_ok(work_copy, PROCAMP, 0, "Brightness", -2.6)
    print(f"negative value: attempts {result['attempts']}, before {result['before']}, value {result['value']}")
    assert result["zero_first"] is True, result
    assert _near(result["before"], 5) and result["restored"] is None and result["error"] is None, result
    assert _near(_value(work_copy["id"], PROCAMP, 0, "Brightness"), -2.6)


def test_effect_set_from_a_plan(work_copy, tmp_path):
    """live effect set --from PLAN.json with six writes. Output keys: sequence, resolved[], results[], ran,
    total, stored, rendered, warnings."""
    writes = [_entry(PROCAMP, 0, "Brightness", -3.7), _entry(LUMETRI, 14, "Temperature", -20),
              _entry(LUMETRI, 15, "Tint", -5), _entry(LUMETRI, 19, "Exposure", 0.4),
              _entry(LUMETRI, 20, "Contrast", 12), _entry(LUMETRI, 24, "Blacks", -8)]
    plan = _write_plan(tmp_path, work_copy, writes)
    data = _ok(run_cli("live", "effect", "set", "--from", str(plan), timeout=PLAN_TIMEOUT_S))
    assert data["ran"] == 6 and data["total"] == 6 and data["stored"] is True and data["rendered"] is None, data
    assert data["sequence"] == {"id": work_copy["id"], "name": COPY_NAME}, data["sequence"]
    assert len(data["resolved"]) == 6 and len(data["results"]) == 6, data
    for number, (entry, result) in enumerate(zip(writes, data["results"])):
        print(f"plan entry {number}: {entry['effect']} {entry['name']} -> {result['value']} "
              f"(attempts {result['attempts']}, zero_first {result['zero_first']})")
        assert result["entry"] == number and result["stored"] is True and result["landed"] is None, result
        assert result["requested"] == entry["value"] and _near(result["value"], entry["value"]), result
    assert _near(_value(work_copy["id"], PROCAMP, 0, "Brightness"), -3.7)
    lum = _props(_effect_get(work_copy["id"], LUMETRI), LUMETRI)
    assert lum[14]["name"] == "Temperature" and _near(lum[14]["value"], -20), lum[14]
    assert lum[24]["name"] == "Blacks" and _near(lum[24]["value"], -8), lum[24]


def test_effect_set_expect_old(work_copy):
    """A wrong --expect-old is refused (precondition, `error` says "as the plan expects") and changes nothing; the
    right one passes."""
    copy_id = work_copy["id"]
    _write_ok(work_copy, PROCAMP, 0, "Brightness", 1.5)
    before = _snapshot(copy_id, PROCAMP)
    run = run_cli(*_set_args(work_copy, PROCAMP, 0, "Brightness", 2.5, expect_old=9.5))
    data = _refusal(run, "precondition", "as the plan expects")
    print(f"expect-old: error {data['error']!r}")
    assert _snapshot(copy_id, PROCAMP) == before, "a refused write changed a value"
    result = _write_ok(work_copy, PROCAMP, 0, "Brightness", 2.5, expect_old=1.5)
    assert _near(result["before"], 1.5), result
    assert _near(_value(copy_id, PROCAMP, 0, "Brightness"), 2.5)


def test_effect_get_and_readback_agree(work_copy):
    """The write reads its value back with getValue() and `live effect get` reads with getValue(0, 0): after
    Lumetri Tint (15) to 3.3 and ProcAmp Contrast (1) to 97.5, both reads must give the same number."""
    copy_id = work_copy["id"]
    tint = _write_ok(work_copy, LUMETRI, 15, "Tint", 3.3)
    contrast = _write_ok(work_copy, PROCAMP, 1, "Contrast", 97.5)
    got_tint = _value(copy_id, LUMETRI, 15, "Tint")
    got_contrast = _value(copy_id, PROCAMP, 1, "Contrast")
    print(f"readback: Tint write read {tint['value']}, effect get {got_tint}; "
          f"Contrast write read {contrast['value']}, effect get {got_contrast}")
    assert _near(tint["value"], got_tint) and _near(contrast["value"], got_contrast), (tint, contrast)
    assert _near(got_tint, 3.3) and _near(got_contrast, 97.5), (got_tint, got_contrast)


# =====================================================================================================
# Frame export, the app switches and lut set. They switch proxy display and the active sequence, so they
# ask for `app_state`, which checks at the end of the module that Premiere is as it was found.
# =====================================================================================================
FRAME_TIMEOUT_S = 2400              # one frame export or one `lut set --prove`. Each frame may wait up to 600 s
                                    # (frames.EXPORT_TIMEOUT_S), the largest command here exports 3 frames
                                    # (test_frame_export_three_times; --prove exports 2), so 3 x 600 = 1800 s, plus two
                                    # MCP server starts (up to 180 s each) and the sessions around them. A SIGTERM on a
                                    # slow but healthy command would block every later send and put-back of the module
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FRAME_SIZE = (1280, 720)            # the size of "Fixture seq"
MIN_FRAME_BYTES = 10000            # a frame of the first clip (the colour test pattern is 100 kB and more)
MIN_ANY_FRAME_BYTES = 1000         # any frame at all: a valid 1280x720 PNG of one colour is about 8 kB
FIRST_CLIP_END_S = 6.006           # V1 clip 0 ends here; clip 1 (Mask, Color And Contrast, VR De-Noise) follows
FIXTURES_LOCAL = Path(__file__).resolve().parent / "fixtures_local"
# A 2-point LUT that halves every channel (red changes fastest). It has the layout of the known-good identity.cube
# next to it (the same header lines in the same order, the same number format, one row per line), and only the
# eight value rows differ: a LUT that Premiere cannot parse may open a modal dialog.
HALVE_CUBE = ('TITLE "halve"\nLUT_3D_SIZE 2\n0 0 0\n0.5 0 0\n0 0.5 0\n0.5 0.5 0\n'
              '0 0 0.5\n0.5 0 0.5\n0 0.5 0.5\n0.5 0.5 0.5\n')


@functools.lru_cache(maxsize=1)
def _proxy_script() -> Path:
    """The read script for proxy display, in a folder of its own that goes away when the run ends."""
    folder = Path(tempfile.mkdtemp(prefix="premiere-e2e-proxies-"))
    atexit.register(shutil.rmtree, folder, ignore_errors=True)
    script = folder / "read_proxies.jsx"
    script.write_text("return __ok({proxies: app.getEnableProxies()});\n", encoding="utf-8")    # a body only, ES3
    return script


def _proxies_now() -> int:
    """Proxy display as `app.getEnableProxies()` reads it: 1 on, 0 off (measures.md section 2)."""
    run = run_cli("live", "run", str(_proxy_script()))
    data = _ok(run)["data"]
    value = data.get("proxies") if isinstance(data, dict) else None
    assert _is_number(value) and value in (0, 1), f"app.getEnableProxies() gave {value!r}\n{run.describe()}"
    return int(value)


def _on_off(value: int) -> str:
    return "on" if value == 1 else "off"


def _app_now() -> tuple[int, str | None]:
    """(proxy display, ID of the active sequence), read by two separate commands."""
    return _proxies_now(), _active_id()


def _proxies_back(value: int) -> str | None:
    """Put proxy display back to `value` when it reads otherwise. None, or what is wrong."""
    if _proxies_now() == value:
        return None
    data = _ok(run_cli("live", "app", "proxies", _on_off(value)))
    now = _proxies_now()
    if data["stored"] is not True or now != value:
        return (f"proxy display was NOT put back {_on_off(value)}: it reads {now}. Run: "
                f"cli-anything-premiere live app proxies {_on_off(value)}")
    return None


def _activate_back(seq_id: str, seq_name: str | None) -> str | None:
    """Make a sequence the active one again when it is not. None, or what is wrong."""
    if _active_id() == seq_id:
        return None
    args = ["live", "app", "activate", "--sequence", seq_id]
    if seq_name:
        args += ["--sequence-name", seq_name]
    data = _ok(run_cli(*args))
    now = _active_id()
    if data["stored"] is not True or now != seq_id:
        return (f"the sequence {seq_id} was NOT made active again: `live state` shows {now}. Run: "
                f"cli-anything-premiere live app activate --sequence {seq_id}")
    return None


@contextlib.contextmanager
def _step(problems: list[str], what: str):
    """A step of an end check: what raises (a timeout of run_cli included) is kept as a problem, not thrown."""
    try:
        yield
    except (Exception, pytest.fail.Exception) as exc:
        problems.append(f"{what} failed: {str(exc)[-1500:]}")


def _app_state_end(start: dict) -> str | None:
    """The end of the module for `app_state`. None when proxy display and the active sequence are as they were at the
    start, else what was found (what differed is put back first)."""
    reason = _no_contact_reason()
    if reason:
        return (f"The app state was not checked at the end, because {reason}: nothing was sent to Premiere. At the "
                f"start proxy display was {_on_off(start['proxies'])} and {start['active_name']!r} was active; a "
                "test may have left them changed. Run: cli-anything-premiere recovery show")
    problems: list[str] = []
    with _step(problems, "Checking proxy display at the end"):
        now = _proxies_now()
        if now != start["proxies"]:
            back = _proxies_back(start["proxies"])
            problems.append(f"a test left proxy display changed; it was put back to {_on_off(start['proxies'])} "
                            f"(it read {_on_off(now)} at the end)" if back is None else
                            f"a test left proxy display {_on_off(now)} and it was {_on_off(start['proxies'])} at "
                            f"the start; {back}")
    with _step(problems, "Checking the active sequence at the end"):
        now = _active_id()
        if now != start["active"]:
            back = _activate_back(start["active"], start["active_name"])
            problems.append(f"a test left the active sequence changed; it was put back to {start['active_name']!r} "
                            f"({start['active']}) (`live state` showed {now} at the end)" if back is None else
                            f"a test left the active sequence at {now} and it was {start['active']} at the start; "
                            f"{back}")
    return "The end of app_state found a problem:\n" + "\n".join(problems) if problems else None


@pytest.fixture(scope="module")
def app_state(work_copy):
    """Proxy display and the active sequence as the app tests found them: yields {"proxies", "active",
    "active_name"}.

    Start: reads proxy display (a `live run` read script) and the active sequence (`live state`). `duplicate_sequence`
    left the COPY active, and `test_mcp_tool_negative_control` (after the app tests, so before this fixture ends)
    leaves "Fixture seq" active, so the start state is made the one `work_copy` found: "Fixture seq" active (by the
    server's set_active_sequence, which ran live before). A start with the copy active would read as a change at the
    end of the module. Every export of a test then has to switch the active sequence, which is the real use.
    End (always, before `work_copy` ends): see _app_state_end.
    """
    proxies = _proxies_now()
    active = _active_id()
    fixture_id = work_copy["fixture_id"]
    if active != fixture_id:
        problem = _fixture_active_again(work_copy)
        if problem:
            pytest.fail(f"app_state could not make {FIXTURE_SEQ!r} the active sequence: {problem}", pytrace=False)
        print(f"app_state: the active sequence was {active}; {FIXTURE_SEQ!r} ({fixture_id}) is active now")
    start = {"proxies": proxies, "active": fixture_id, "active_name": FIXTURE_SEQ}
    print(f"app_state: proxy display is {_on_off(proxies)}, the active sequence is {FIXTURE_SEQ!r} ({fixture_id})")
    try:
        yield start
    finally:
        _after(None, lambda: _app_state_end(start))


# ---------- helpers of these tests ----------
def _alert(run: Run) -> None:
    """Fails with the WHOLE output when a frame or LUT command left something switched (left_switched is not null,
    also inside `export` of a lut failure) or a recovery is open (recovery_required, not_restored). Run.describe()
    cuts the output, and the controller has to see what is still switched."""
    data = run.data or {}
    exported = data.get("export")
    left = data.get("left_switched")
    if left is None and isinstance(exported, dict):
        left = exported.get("left_switched")
    if left is not None or data.get("code") in ("recovery_required", "not_restored"):
        pytest.fail("This command left Premiere switched, or a recovery is open. left_switched and warnings below say "
                    "what is still switched and how to put it back; then run: cli-anything-premiere recovery show\n"
                    f"command: {' '.join(run.argv)}\nexit status: {run.code}\nstdout: {run.stdout}\n"
                    f"stderr: {run.stderr}", pytrace=False)


def _switch_ok(run: Run) -> dict:
    """The JSON of a frame or LUT command that must have succeeded (see _alert)."""
    _alert(run)
    return _ok(run)


def _switch_failed(run: Run) -> dict:
    """The JSON of a frame or LUT command that must have exited 1 (see _alert)."""
    _alert(run)
    return _failed(run)


def _png_size(path: Path) -> tuple[int, int]:
    """(width, height) of a PNG: bytes 16 to 24 of the file, after the signature and the IHDR header."""
    with open(path, "rb") as handle:
        head = handle.read(24)
    assert head[:8] == PNG_SIGNATURE and head[12:16] == b"IHDR", f"{path} is not a PNG: {head!r}"
    return struct.unpack(">II", head[16:24])


def _export_args(seq_id: str, seq_name: str, out_dir: Path | str, *times: float, prefix: str | None = None,
                 overwrite: bool = False, dry_run: bool = False) -> list[str]:
    args = ["live", "frame", "export", "--sequence", seq_id, "--sequence-name", seq_name, "--out-dir", str(out_dir)]
    for seconds in times:
        args += ["--time", repr(float(seconds))]
    if prefix is not None:
        args += ["--prefix", prefix]
    if overwrite:
        args.append("--overwrite")
    if dry_run:
        args.append("--dry-run")
    return args


def _frame_file(folder: Path, prefix: str, seconds: float) -> Path:
    """The file `live frame export` makes for a frame: PREFIX_00001000ms.png for 1 s."""
    return folder / f"{prefix}_{round(seconds * 1000):08d}ms.png"


def _export(seq_id: str, seq_name: str, folder: Path, *times: float, prefix: str = "frame", **options) -> dict:
    """One `live frame export` that must succeed. Returns its JSON."""
    return _switch_ok(run_cli(*_export_args(seq_id, seq_name, folder, *times, prefix=prefix, **options),
                              timeout=FRAME_TIMEOUT_S))


def _check_frames(data: dict, folder: Path, prefix: str, times: tuple[float, ...], *, only: bool = True) -> list[Path]:
    """The frames of an export: the times and paths in the payload, each file with the bytes the payload says and a
    PNG of 1280x720, and a size floor that depends on the time. A frame inside the first clip (before 6.006 s) holds
    the colour test pattern and must be above 10000 bytes. A frame inside the second clip (6.006 s to the end) renders
    almost fully black on 27.0.0 (measured: 7951 and 7952 bytes at 7.9 s and 7.0 s), and a black PNG of this size is
    about 8 kB, so it only has to be above 1000 bytes. With `only`, the folder holds these files and no other."""
    frames = data["frames"]
    assert data["exported"] == len(times) == data["total"] == len(frames), data
    assert [f["time_seconds"] for f in frames] == list(times), frames
    paths = []
    for entry, seconds in zip(frames, times):
        path = _frame_file(folder, prefix, seconds)
        assert entry["path"] == str(path), (entry, str(path))
        assert path.is_file(), f"there is no file at {path}"
        size = path.stat().st_size
        floor = MIN_FRAME_BYTES if seconds < FIRST_CLIP_END_S else MIN_ANY_FRAME_BYTES
        assert size > floor, (f"{path} has {size} bytes, not more than {floor} (the floor for a frame at "
                              f"{seconds:g} s, which is {'inside' if seconds < FIRST_CLIP_END_S else 'after'} the first clip)")
        assert entry["bytes"] == size, (entry, size)
        assert _png_size(path) == FRAME_SIZE, f"{path} is a PNG of {_png_size(path)}, not {FRAME_SIZE}"
        paths.append(path)
    if only:
        held = sorted(os.listdir(folder))
        assert held == sorted(p.name for p in paths), f"{folder} holds {held}"
    return paths


def _need_ffmpeg(what: str) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip(f"ffmpeg is not on PATH: {what}")


def _difference(a: Path, b: Path) -> float:
    """The mean absolute difference of two frames (frame_pixels: over black, 960x540 rgb24; 0 to 255)."""
    try:
        return mean_difference(frame_pixels(a), frame_pixels(b))
    except CliError as exc:
        pytest.fail(f"The pictures {a} and {b} could not be compared: {exc.code}: {exc.message}", pytrace=False)


def _halve_cube() -> Path:
    """tests/fixtures_local/halve.cube (local, not in git): written when it is missing, and rewritten when it holds
    anything but HALVE_CUBE (a file of an earlier attempt)."""
    path = FIXTURES_LOCAL / "halve.cube"
    wanted = HALVE_CUBE.encode("ascii")
    if not path.is_file() or path.read_bytes() != wanted:
        FIXTURES_LOCAL.mkdir(parents=True, exist_ok=True)
        path.write_bytes(wanted)
        print(f"halve.cube: written to {path}")
    return path


def _lut_args(copy: dict, lut_file: Path | str, *, clip: int = 0, prove: bool = False, time_s: float | None = None,
              out_dir: Path | str | None = None, dry_run: bool = False) -> list[str]:
    """`live lut set` on V1 clip `clip` of the copy; no --effect-index (clip 0 holds one Lumetri Color)."""
    args = ["live", "lut", "set", "--sequence", copy["id"], "--sequence-name", copy["name"], "--track", "0",
            "--clip", str(clip), "--lut", str(lut_file)]
    if prove:
        args += ["--prove", "--time", repr(float(time_s)), "--out-dir", str(out_dir)]
    if dry_run:
        args.append("--dry-run")
    return args


def _lut_slots(seq_id: str) -> tuple[object, object]:
    """(index 4, index 6) of the Lumetri Color on V1 clip 0 as `live effect get` reads them: the LUT path with a
    trailing "/" (a string), and the Input LUT menu (a number)."""
    props = _props(_effect_get(seq_id, LUMETRI), LUMETRI)
    assert props[4]["kind"] == "string" and props[6]["name"] == "Input LUT" and props[6]["kind"] == "number", \
        (props[4], props[6])
    return props[4]["value"], props[6]["value"]


def _same_app(before: tuple[int, str | None], what: str) -> None:
    """Proxy display and the active sequence read as they did before the command."""
    after = _app_now()
    assert after == before, (f"{what} changed Premiere: (proxy display, active sequence) was {before} and is "
                             f"{after}")


# ---------- live frame export and live app ----------
def test_frame_export_dry_run_makes_no_folder(work_copy, app_state, tmp_path):
    """live frame export --dry-run. Output keys: dry_run, sequence {id, name}, planned[] {time_seconds, path}, total,
    out_dir {path, exists}, proxies {before, switch}, active_sequence {before, switch}, warnings. No frames,
    exported or left_switched."""
    copy_id = work_copy["id"]
    folder = tmp_path / "frames not made"
    before = _app_now()
    data = _switch_ok(run_cli(*_export_args(copy_id, COPY_NAME, folder, 1.0, dry_run=True)))
    print(f"frame export dry run: proxies {data['proxies']}, active_sequence {data['active_sequence']}, "
          f"out_dir {data['out_dir']}")
    assert data["dry_run"] is True and data["sequence"] == {"id": copy_id, "name": COPY_NAME}, data
    assert not {"frames", "exported", "left_switched"} & set(data), data
    assert data["planned"] == [{"time_seconds": 1.0, "path": str(_frame_file(folder, "frame", 1.0))}], data["planned"]
    assert data["total"] == 1, data
    assert data["out_dir"] == {"path": str(folder), "exists": False}, data["out_dir"]
    assert not os.path.lexists(folder), f"a dry run made the folder {folder}"
    assert data["proxies"] == {"before": before[0] == 1, "switch": before[0] == 1}, (data["proxies"], before)
    assert data["active_sequence"] == {"before": before[1], "switch": before[1] != copy_id}, (data["active_sequence"], before)
    _same_app(before, "a dry run")
    assert not _marker_open()


def test_frame_export_three_times(work_copy, app_state, tmp_path):
    """live frame export --time 1.0 --time 3.0 --time 7.0. Output keys: sequence, frames[] {time_seconds, path,
    bytes}, exported, total, proxies {before, during, after} (booleans), active_sequence {before, after} (IDs),
    left_switched, changed_by_others, stored, rendered, warnings."""
    copy_id = work_copy["id"]
    folder = tmp_path / "frames"
    times = (1.0, 3.0, 7.0)
    before = _app_now()
    started = time.monotonic()
    run = run_cli(*_export_args(copy_id, COPY_NAME, folder, *times), timeout=FRAME_TIMEOUT_S)
    seconds = time.monotonic() - started
    data = _switch_ok(run)
    print(f"frame export: {len(times)} frames in {seconds:.1f} s; proxies {data['proxies']}, "
          f"active_sequence {data['active_sequence']}, warnings {data['warnings']}")
    assert data["sequence"] == {"id": copy_id, "name": COPY_NAME}, data["sequence"]
    assert data["stored"] is None and data["rendered"] is None, data
    assert data["left_switched"] is None, data
    assert data["changed_by_others"] == {"proxies": False, "active_sequence": False}, data
    paths = _check_frames(data, folder, "frame", times)
    print(f"frame export: sizes {[p.stat().st_size for p in paths]} bytes, each {_png_size(paths[0])}")
    assert data["proxies"]["before"] is (before[0] == 1), (data["proxies"], before)
    assert data["proxies"]["during"] is False, data["proxies"]         # proxy display was off for the export
    assert data["proxies"]["after"] is data["proxies"]["before"], data["proxies"]
    print(f"frame export: proxy display at the start: {_on_off(before[0])}")
    if before[0] == 1:
        assert data["proxies"] == {"before": True, "during": False, "after": True}, \
            f"proxy display was on at the start: it must be off during the export and on again after: {data['proxies']}"
    else:
        print("frame export: proxy display was off at the start, so the proxy switch was NOT exercised by this test")
    assert data["active_sequence"] == {"before": before[1], "after": before[1]}, (data["active_sequence"], before)
    assert before[1] != copy_id                                     # so this run switched the active sequence and back
    _same_app(before, "the export")                                 # separate reads: `live state` and the proxy script
    assert not _marker_open()


def test_frame_export_follows_the_target_sequence(work_copy, app_state, tmp_path):
    """A frame is of the NAMED sequence, not of the active one. The copy gets ProcAmp Brightness -60, "Fixture seq"
    is made active (live app activate), then time 1.0 is exported from the copy and from "Fixture seq": the pictures
    differ (mean difference above 5), and "Fixture seq" is still the active sequence after both."""
    copy_id, fixture_id = work_copy["id"], work_copy["fixture_id"]
    folder = tmp_path / "frames"
    old = _value(copy_id, PROCAMP, 0, "Brightness")
    before = _app_now()
    failure = None
    try:
        _write_ok(work_copy, PROCAMP, 0, "Brightness", -60)
        made = _ok(run_cli("live", "app", "activate", "--sequence", fixture_id, "--sequence-name", FIXTURE_SEQ))
        assert made["stored"] is True and made["active_sequence"]["after"]["id"] == fixture_id, made
        assert _active_id() == fixture_id, "`live state` does not show the fixture sequence as the active one"
        from_copy = _export(copy_id, COPY_NAME, folder, 1.0, prefix="from_copy")
        from_fixture = _export(fixture_id, FIXTURE_SEQ, folder, 1.0, prefix="from_fixture")
        print(f"follows the target: copy export {from_copy['active_sequence']}, fixture export "
              f"{from_fixture['active_sequence']}")
        assert from_copy["sequence"] == {"id": copy_id, "name": COPY_NAME}, from_copy["sequence"]
        assert from_fixture["sequence"] == {"id": fixture_id, "name": FIXTURE_SEQ}, from_fixture["sequence"]
        assert from_copy["active_sequence"] == {"before": fixture_id, "after": fixture_id}, from_copy["active_sequence"]
        assert from_fixture["active_sequence"] == {"before": fixture_id, "after": fixture_id}, \
            from_fixture["active_sequence"]
        [copy_frame] = _check_frames(from_copy, folder, "from_copy", (1.0,), only=False)
        [fixture_frame] = _check_frames(from_fixture, folder, "from_fixture", (1.0,), only=False)
        assert _active_id() == fixture_id, "`live state`: the fixture sequence is not the active one after the exports"
        _same_app(before, "the two exports")
    except BaseException as exc:
        failure = exc
        raise
    finally:
        def restore() -> str | None:
            _write_ok(work_copy, PROCAMP, 0, "Brightness", old)
            now = _value(copy_id, PROCAMP, 0, "Brightness")
            return None if _near(now, old) else f"The ProcAmp Brightness of the copy is {now}, and it was {old} before the test."
        _after(failure, restore)
    _need_ffmpeg("the two pictures cannot be compared")
    difference = _difference(copy_frame, fixture_frame)
    print(f"follows the target: mean difference between the copy (Brightness -60) and Fixture seq at 1.0 s: {difference:.2f}")
    assert difference > 5, (f"the frame of the copy and the frame of {FIXTURE_SEQ!r} differ by only {difference:.2f}: "
                            f"the export may have taken the wrong sequence ({copy_frame}, {fixture_frame})")


def test_frame_export_refuses_an_existing_file_and_overwrites(work_copy, app_state, tmp_path):
    """The second run of the same command: exit 1, code output_exists (no payload: ok, error, code), nothing
    switched, the file as it was. With --overwrite: exit 0 and the file is a new one (inode or time changed)."""
    copy_id = work_copy["id"]
    folder = tmp_path / "frames"
    first = _export(copy_id, COPY_NAME, folder, 1.0, prefix="again")
    [path] = _check_frames(first, folder, "again", (1.0,))
    old = os.stat(path)
    before = _app_now()
    refused = _switch_failed(run_cli(*_export_args(copy_id, COPY_NAME, folder, 1.0, prefix="again"),
                                     timeout=FRAME_TIMEOUT_S))
    print(f"existing file: error {refused['error']!r}")
    assert refused["code"] == "output_exists" and "exists already" in refused["error"], refused
    _same_app(before, "a refused export")
    kept = os.stat(path)
    assert (kept.st_ino, kept.st_mtime_ns, kept.st_size) == (old.st_ino, old.st_mtime_ns, old.st_size), \
        "the refused export changed the file"
    assert not _marker_open()
    again = _export(copy_id, COPY_NAME, folder, 1.0, prefix="again", overwrite=True)
    _check_frames(again, folder, "again", (1.0,))
    new = os.stat(path)
    print(f"existing file: inode {old.st_ino} -> {new.st_ino}, mtime_ns {old.st_mtime_ns} -> {new.st_mtime_ns}")
    assert (new.st_ino, new.st_mtime_ns) != (old.st_ino, old.st_mtime_ns), "--overwrite did not replace the file"
    _same_app(before, "the export with --overwrite")


def test_frame_export_name_with_space_quote_and_umlaut_under_tmp(work_copy, app_state):
    """A folder with a space and an umlaut under the real /tmp (a link to /private/tmp on macOS) and a prefix with
    quotes and an umlaut: the file is at the path asked for, and the payload says so."""
    copy_id = work_copy["id"]
    top = Path(f"/tmp/premiere-cli-e2e-{os.getpid()}")
    folder = top / "frames ü"
    prefix = 'a "b" ü'
    requested = _frame_file(folder, prefix, 1.0)
    before = _app_now()
    try:
        data = _export(copy_id, COPY_NAME, folder, 1.0, prefix=prefix)
        print(f"odd name: requested {ascii(str(requested))}, payload {ascii(data['frames'][0]['path'])}, "
              f"folder holds {ascii(os.listdir(folder))}")
        assert data["exported"] == 1 and data["frames"][0]["path"] == str(requested), (data["frames"], str(requested))
        assert os.path.isfile(requested), f"there is no file at {ascii(str(requested))}"
        assert os.path.getsize(requested) > MIN_FRAME_BYTES, os.path.getsize(requested)
        assert len(os.listdir(folder)) == 1, os.listdir(folder)
        _same_app(before, "the export")
    finally:
        shutil.rmtree(top, ignore_errors=True)


def test_frame_export_time_at_the_end_is_refused(work_copy, app_state, tmp_path):
    """The sequence ends at 8.008 s: that time is refused (code bad_time, with the payload of a failed export:
    exported 0, nothing switched), and 7.9 exports."""
    copy_id = work_copy["id"]
    folder = tmp_path / "frames"
    before = _app_now()
    refused = _switch_failed(run_cli(*_export_args(copy_id, COPY_NAME, folder, 8.008), timeout=FRAME_TIMEOUT_S))
    print(f"time at the end: error {refused['error']!r}")
    assert refused["code"] == "bad_time" and "Nothing was switched" in refused["error"], refused
    assert refused["exported"] == 0 and refused["frames"] == [] and refused["left_switched"] is None, refused
    assert refused["proxies"]["after"] is refused["proxies"]["before"], refused["proxies"]
    assert refused["proxies"]["during"] is refused["proxies"]["before"], refused["proxies"]
    assert refused["active_sequence"]["after"] == refused["active_sequence"]["before"] == before[1], \
        refused["active_sequence"]
    _same_app(before, "the refused export")
    assert not os.path.isdir(folder) or os.listdir(folder) == [], f"the refused export made files: {os.listdir(folder)}"
    assert not _marker_open()
    done = _export(copy_id, COPY_NAME, folder, 7.9)
    _check_frames(done, folder, "frame", (7.9,))
    _same_app(before, "the export of 7.9")


def test_app_proxies_off_and_on(work_copy, app_state):
    """live app proxies off|on. Output keys: proxies {before, after} (booleans), stored, rendered, warnings. The
    proxy script (app.getEnableProxies) reads 1 on and 0 off."""
    start = _proxies_now()
    flipped, original = ("off", "on") if start == 1 else ("on", "off")
    failure = None
    try:
        data = _ok(run_cli("live", "app", "proxies", flipped))
        print(f"proxies {flipped}: {data['proxies']}, warnings {data['warnings']}")
        assert data["stored"] is True and data["rendered"] is None, data
        assert data["proxies"] == {"before": start == 1, "after": flipped == "on"}, data["proxies"]
        assert _proxies_now() == (1 if flipped == "on" else 0)
        back = _ok(run_cli("live", "app", "proxies", original))
        print(f"proxies {original}: {back['proxies']}")
        assert back["stored"] is True and back["rendered"] is None, back
        assert back["proxies"] == {"before": flipped == "on", "after": start == 1}, back["proxies"]
        assert _proxies_now() == start
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _after(failure, lambda: _proxies_back(start))


def test_app_activate(work_copy, app_state):
    """live app activate --sequence ID --sequence-name NAME. Output keys: sequence {id, name}, active_sequence
    {before, after} (each {id, name}), stored, rendered, warnings. `live state` must agree."""
    copy_id, fixture_id = work_copy["id"], work_copy["fixture_id"]
    start = _active_id()
    failure = None
    try:
        data = _ok(run_cli("live", "app", "activate", "--sequence", copy_id, "--sequence-name", COPY_NAME))
        print(f"activate: copy -> {data['active_sequence']}")
        assert data["stored"] is True and data["rendered"] is None, data
        assert data["sequence"] == {"id": copy_id, "name": COPY_NAME}, data["sequence"]
        assert (data["active_sequence"]["before"] or {}).get("id") == start, (data["active_sequence"], start)
        assert data["active_sequence"]["after"] == {"id": copy_id, "name": COPY_NAME}, data["active_sequence"]
        assert _active_id() == copy_id, "`live state` does not show the copy as the active sequence"
        data = _ok(run_cli("live", "app", "activate", "--sequence", fixture_id, "--sequence-name", FIXTURE_SEQ))
        print(f"activate: fixture -> {data['active_sequence']}")
        assert data["stored"] is True and data["rendered"] is None, data
        assert data["sequence"] == {"id": fixture_id, "name": FIXTURE_SEQ}, data["sequence"]
        assert (data["active_sequence"]["before"] or {}).get("id") == copy_id, data["active_sequence"]
        assert data["active_sequence"]["after"] == {"id": fixture_id, "name": FIXTURE_SEQ}, data["active_sequence"]
        assert _active_id() == fixture_id, "`live state` does not show the fixture sequence as the active one"
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _after(failure, lambda: _activate_back(app_state["active"], app_state["active_name"]))


# ---------- live lut set ----------
def test_baseline_two_exports_of_one_frame_are_equal(work_copy, app_state, tmp_path):
    """The noise floor of `lut set --prove`: two exports of time 2.0, in two commands, give the same picture (mean
    difference below 0.5)."""
    copy_id = work_copy["id"]
    folder = tmp_path / "frames"
    before = _app_now()
    first = _export(copy_id, COPY_NAME, folder, 2.0, prefix="base_a")
    second = _export(copy_id, COPY_NAME, folder, 2.0, prefix="base_b")
    [path_a] = _check_frames(first, folder, "base_a", (2.0,), only=False)
    [path_b] = _check_frames(second, folder, "base_b", (2.0,), only=False)
    _same_app(before, "the two exports")
    _need_ffmpeg("the two frames cannot be compared")
    difference = _difference(path_a, path_b)
    print(f"baseline: mean difference of two exports of one frame (2.0 s): {difference:.4f}")
    assert difference < 0.5, f"two exports of one frame differ by {difference:.4f}: --prove has no noise floor"


def test_lut_set_dry_run_changes_nothing(work_copy, app_state):
    """live lut set --dry-run. Output keys: dry_run, sequence, clip, lut, effect_index, path_before, path_after (the
    text that would be written), menu_value (index 6 now), stored None, rendered None, frames [], replaces []."""
    copy_id = work_copy["id"]
    halve = _halve_cube()
    before = _snapshot(copy_id, LUMETRI)
    slot4, slot6 = before[LUMETRI][4]["value"], before[LUMETRI][6]["value"]
    data = _switch_ok(run_cli(*_lut_args(work_copy, halve, dry_run=True)))
    print(f"lut dry run: effect_index {data['effect_index']}, path_before {data['path_before']!r}, "
          f"path_after {data['path_after']!r}, menu_value {data['menu_value']!r}")
    assert data["dry_run"] is True and data["stored"] is None and data["rendered"] is None, data
    assert data["sequence"] == {"id": copy_id, "name": COPY_NAME}, data["sequence"]
    assert (data["clip"]["track"], data["clip"]["clip"]) == (0, 0), data["clip"]
    assert data["lut"] == str(halve) and data["path_after"] == str(halve) + "/", data
    assert data["frames"] == [] and data["left_switched"] is None, data
    assert data["menu_value"] == slot6, (data["menu_value"], slot6)
    assert data["path_before"] == (slot4 if isinstance(slot4, str) and slot4.lower().endswith(".cube/") else None), \
        (data["path_before"], slot4)
    assert _snapshot(copy_id, LUMETRI) == before, "a dry run changed a Lumetri Color value"
    assert _lut_slots(copy_id) == (slot4, slot6)
    assert not _marker_open()


def test_lut_set_same_identity_gives_no_verdict(work_copy, app_state, tmp_path):
    """Runs BEFORE any other LUT write: the clip holds identity.cube already, so writing identity.cube again must not
    change the picture (difference below 1.0) and the verdict is null (rendered None) with a warning that a LUT was
    in the slot. Output keys: stored, rendered, difference, path_before, path_after, menu_value, frames[] (paths),
    warnings."""
    _need_ffmpeg("lut set --prove needs it")
    identity = FIXTURES_LOCAL / "identity.cube"
    assert identity.is_file(), f"{identity} is missing: it is the LUT the fixture holds"
    folder = tmp_path / "frames"
    before = _app_now()
    started = time.monotonic()
    run = run_cli(*_lut_args(work_copy, identity, prove=True, time_s=2.0, out_dir=folder), timeout=FRAME_TIMEOUT_S)
    seconds = time.monotonic() - started
    data = _switch_ok(run)
    print(f"identity again: {seconds:.1f} s; path_before {data['path_before']!r}, menu_value {data['menu_value']!r}, "
          f"difference {data['difference']!r}, rendered {data['rendered']!r}")
    print(f"identity again: warnings {data['warnings']}")
    assert data["stored"] is True, data
    assert data["rendered"] is None, data
    assert data["difference"] is not None and data["difference"] < 1.0, data["difference"]
    assert any("A LUT was already in the slot" in w for w in data["warnings"]), data["warnings"]
    assert data["path_before"] is not None and data["path_after"] == str(identity.resolve()) + "/", data
    assert data["left_switched"] is None, data
    names = sorted(os.path.basename(p) for p in data["frames"])
    assert names == ["lut_after_00002000ms.png", "lut_before_00002000ms.png"], data["frames"]
    assert all(os.path.isfile(p) for p in data["frames"]), data["frames"]
    _same_app(before, "lut set --prove")


def test_lut_set_with_prove_changes_the_picture(work_copy, app_state, tmp_path):
    """halve.cube with --prove at 2.0 s: stored true, rendered true, difference above 10, index 4 reads the path plus
    "/". Then a plain `live frame export` of 2.0 s must equal the frame taken after the write (difference below
    0.5: the LUT was fully loaded when that frame was taken)."""
    _need_ffmpeg("lut set --prove needs it")
    copy_id = work_copy["id"]
    halve = _halve_cube()
    folder = tmp_path / "frames"
    before = _app_now()
    started = time.monotonic()
    run = run_cli(*_lut_args(work_copy, halve, prove=True, time_s=2.0, out_dir=folder), timeout=FRAME_TIMEOUT_S)
    seconds = time.monotonic() - started
    data = _switch_ok(run)
    slot4, slot6 = _lut_slots(copy_id)
    print(f"halve with --prove: {seconds:.1f} s; stored {data['stored']!r}, rendered {data['rendered']!r}, "
          f"difference {data['difference']!r}, menu_value {data['menu_value']!r}, index 6 reads {slot6!r}")
    print(f"halve with --prove: warnings {data['warnings']}")
    assert data["stored"] is True and data["rendered"] is True, data
    assert data["difference"] is not None and data["difference"] > 10, data["difference"]
    assert data["lut"] == str(halve) and data["path_after"] == str(halve) + "/", data
    assert slot4 == str(halve) + "/", f"index 4 reads {slot4!r}, not {str(halve) + '/'!r}"
    assert data["left_switched"] is None, data
    before_frame, after_frame = (folder / "lut_before_00002000ms.png", folder / "lut_after_00002000ms.png")
    assert sorted(data["frames"]) == sorted([str(before_frame), str(after_frame)]), data["frames"]
    for path in (before_frame, after_frame):
        assert path.is_file() and path.stat().st_size > MIN_FRAME_BYTES, f"{path} is missing or small"
    _same_app(before, "lut set --prove")
    again = _export(copy_id, COPY_NAME, folder, 2.0, prefix="lut_again")
    [again_frame] = _check_frames(again, folder, "lut_again", (2.0,), only=False)
    difference = _difference(after_frame, again_frame)
    print(f"halve with --prove: mean difference of the frame taken after the write and a later export: {difference:.4f}")
    assert difference < 0.5, (f"the frame taken right after the write differs by {difference:.4f} from a later export "
                              "of the same time: the LUT was not fully loaded when frame B was taken")
    _same_app(before, "the later export")


def test_lut_set_time_at_the_clip_end_is_refused(work_copy, app_state, tmp_path):
    """--prove --time 6.006 is the end of V1 clip 0, which is not inside it: code bad_time, nothing written (index 4
    and 6 as before), no frame, no folder made by lut set."""
    _need_ffmpeg("lut set --prove needs it")
    copy_id = work_copy["id"]
    halve = _halve_cube()
    folder = tmp_path / "frames"
    slots = _lut_slots(copy_id)
    before = _app_now()
    data = _switch_failed(run_cli(*_lut_args(work_copy, halve, prove=True, time_s=6.006, out_dir=folder),
                                  timeout=FRAME_TIMEOUT_S))
    print(f"lut time at the clip end: error {data['error']!r}")
    assert data["code"] == "bad_time", data
    assert data["stored"] is False and data["rendered"] is None and data["path_after"] is None, data
    assert data["frames"] == [] and data["difference"] is None and data["left_switched"] is None, data
    assert _lut_slots(copy_id) == slots, "a refused lut set changed index 4 or 6"
    assert not os.path.isdir(folder) or os.listdir(folder) == [], f"a refused lut set made files: {os.listdir(folder)}"
    _same_app(before, "a refused lut set")
    assert not _marker_open()


def test_lut_set_non_ascii_path(work_copy, app_state, tmp_path):
    """A LUT file named with an a-umlaut and a u-umlaut: stored true, and index 4 reads back the path plus "/" exactly.
    A different Unicode form from Premiere would fail here with both strings: that is a finding."""
    copy_id = work_copy["id"]
    lut_file = tmp_path / "lüt ä.cube"
    shutil.copyfile(_halve_cube(), lut_file)
    expected = str(lut_file.resolve()) + "/"
    run = run_cli(*_lut_args(work_copy, lut_file))
    data = run.data or {}
    slot4, slot6 = _lut_slots(copy_id)
    print(f"non-ASCII LUT path: stored {data.get('stored')!r}, menu_value {data.get('menu_value')!r}, "
          f"index 4 reads back {ascii(slot4)}")
    shown = (f"sent {ascii(expected)}, index 4 reads {ascii(slot4)}; "
             f"NFC equal {unicodedata.normalize('NFC', str(slot4)) == expected}")
    assert run.code == 0 and data.get("ok") is True and data.get("stored") is True, f"{shown}\n{run.describe()}"
    assert data["path_after"] == expected and data["lut"] == expected[:-1], f"{shown}\n{data}"
    assert slot4 == expected, shown
    assert not _marker_open()


def test_lut_set_refuses_a_clip_without_lumetri(work_copy, app_state):
    """V1 clip 1 has no Lumetri Color: exit 1, code precondition, nothing written, no recovery marker."""
    copy_id = work_copy["id"]
    halve = _halve_cube()
    slots = _lut_slots(copy_id)
    data = _switch_failed(run_cli(*_lut_args(work_copy, halve, clip=1)))
    print(f"lut on a clip without Lumetri: error {data['error']!r}")
    assert data["code"] == "precondition" and "Lumetri" in data["error"], data
    assert data["stored"] is False and data["path_after"] is None and data["frames"] == [], data
    assert not _marker_open(), "a refusal left a recovery marker"
    assert _lut_slots(copy_id) == slots, "a refused lut set changed index 4 or 6 of clip 0"


# =====================================================================================================
# Plan 3: live keys get / set, live place, live effect apply, and three measures for the film session.
# All on "E2E copy". Each test cleans up the clips it placed (a tool that acts on the active sequence is sent
# only while the copy is active, then "Fixture seq" is made active again). What stays on the copy until
# work_copy deletes it: the keys and Scale of V1 clip 0, the effects applied to V1 clip 1, an audio track that
# test_place_sound_only may add. What stays in the unsaved project: the item marks of testclip.mp4 and the PNG
# item of the still test (_end_check compares neither; the controller closes the project without saving).
# =====================================================================================================
PLAN_CMD_TIMEOUT_S = 900    # keys set, place, effect apply: session A, one or two MCP sessions (each may start the
                            # server, up to 180 s, and each call has the command's own 120 s limit), read passes and
                            # session B. A SIGTERM on a slow but healthy run would block every later send
TICKS_PER_SECOND = 254016000000
FRAME_TICKS = 10594584000   # one frame of "Fixture seq" (23.976 fps): seq.timebase
PLAN_FPS = "24000/1001"
KEY_TOLERANCE = 0.0001      # key values are stored as float32 (measures.md, M4)
GYROFLOW = "Gyroflow"
PROJECT_PATH = STATE0["projects"][0].get("path") or None


def _project_guard() -> dict:
    """expected_project_path for a direct MCP write, when `live state` gave the path."""
    return {"expected_project_path": PROJECT_PATH} if PROJECT_PATH else {}


def _copy_guard(copy: dict) -> dict:
    """The guard args of a direct MCP write to the copy (as active.py sends them)."""
    return {**_project_guard(), "expected_cep_target": {"backend": "cep", "sequence_id": copy["id"]}}


def _on_copy(copy: dict, action):
    """Run action() while the copy is the active sequence (confirmed by `live state`), then make "Fixture seq"
    active again, also after a failure. For the tools that act on the active sequence only (add_track,
    remove_from_timeline): sent while the fixture is active, they would change the fixture."""
    failure = None
    try:
        reply = _tool("set_active_sequence", {"sequence_id": copy["id"]})
        assert isinstance(reply, dict) and reply.get("active") is True and str(reply.get("id")) == copy["id"], reply
        active = _active_id()
        assert active == copy["id"], f"`live state` shows the active sequence {active}, not the copy {copy['id']}"
        return action()
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _after(failure, lambda: _fixture_active_again(copy))


def _clips(seq_id: str, track_type: str = "both", track: int | None = None) -> list[dict]:
    args = ["live", "clip", "list", "--sequence", seq_id, "--track-type", track_type]
    if track is not None:
        args += ["--track", str(track)]
    data = _ok(run_cli(*args))
    assert data["truncated"] is False, data
    return data["clips"]


def _node_ids(seq_id: str) -> set[str]:
    return {c["node_id"] for c in _clips(seq_id)}


def _clip_on(seq_id: str, track: int, clip: int) -> dict:
    """The `live clip list` record of video track `track`, clip `clip`."""
    found = [c for c in _clips(seq_id, "video", track) if c["clip"] == clip]
    assert len(found) == 1, f"V{track + 1} of {seq_id} has no clip {clip}: {[c['clip'] for c in _clips(seq_id, 'video', track)]}"
    return found[0]


def _remove_new(copy: dict, before: set[str]) -> str | None:
    """Remove from the copy every clip that is not in `before` (with its linked partner), then check that none is
    left. None, or what is still wrong."""
    reason = _no_contact_reason()
    if reason:
        return f"The clips this test placed on {COPY_NAME!r} were NOT removed, because {reason}."
    now = _clips(copy["id"])
    new = [c["node_id"] for c in sorted(now, key=lambda c: c["track_type"] != "video") if c["node_id"] not in before]
    if not new:
        return None

    def remove() -> None:
        for node in new:
            if node not in _node_ids(copy["id"]):
                continue                                    # it went with its linked partner
            reply = _tool("remove_from_timeline", {"node_id": node, "include_linked": True, "ripple": False,
                                                   **_copy_guard(copy)})
            assert isinstance(reply, dict) and reply.get("removed") is True and reply.get("verified") is True, \
                f"remove_from_timeline {node} gave an unexpected reply: {reply!r}"

    _on_copy(copy, remove)
    left = sorted(_node_ids(copy["id"]) - before)
    print(f"cleanup: removed {new} from {COPY_NAME!r}")
    return None if not left else f"These clips are still on {COPY_NAME!r}: {left}. Remove them by hand."


def _write_json(tmp_path: Path, name: str, value: object) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _plan_failed(run: Run, code: str) -> dict:
    """A plan command (keys set, place, effect apply) that failed: exit 1, ok false, this code, the payload with
    results and counts, and no recovery marker."""
    data = _failed(run)
    assert data["code"] == code and isinstance(data.get("results"), list) and isinstance(data.get("counts"), dict), \
        run.describe()
    assert not _marker_open(), f"a recovery marker is open after a refused plan command.\n{run.describe()}"
    return data


def _counts(done: int = 0, skipped: int = 0, refused: int = 0, failed: int = 0, not_run: int = 0) -> dict:
    return {"done": done, "skipped": skipped, "refused": refused, "failed": failed, "not_run": not_run}


# ---------- live keys ----------
def _keys_get(seq_id: str, *, track: int = 0, clip: int = 0) -> dict:
    return _ok(run_cli("live", "keys", "get", "--sequence", seq_id, "--track", str(track), "--clip", str(clip)))


def _keys_now(seq_id: str) -> dict:
    """What V1 clip 0's Rotation holds, to compare before and after a command that must change nothing."""
    got = _keys_get(seq_id)
    return {"is_time_varying": got["is_time_varying"], "value": got["value"],
            "keys": [(k["media_ticks"], k["value"]) for k in got["keys"]]}


def _level_entry(clip: dict, **fields) -> dict:
    """A level plan entry for a clip of `live clip list`: node, src_in and src_out from its in and out ticks."""
    entry = {"node": clip["node_id"], "name": "e2e", "src_in": int(clip["in_ticks"]) / TICKS_PER_SECOND,
             "src_out": int(clip["out_ticks"]) / TICKS_PER_SECOND, "scale": None, "keys": []}
    entry.update(fields)
    return entry


def _keys_set_args(copy: dict, plan: Path, *extra: str) -> list[str]:
    return ["live", "keys", "set", "--sequence", copy["id"], "--sequence-name", copy["name"], "--from", str(plan),
            *extra]


def test_keys_get_of_the_fixture(work_copy):
    """live keys get on V1 clip 0 of "Fixture seq" (a read): Motion Rotation has keys, each with media_ticks (digits)
    and seconds = (media_ticks - in) / 254016000000. Output keys: sequence, clip, effect, property, is_time_varying,
    value, keys[] {seconds, media_ticks, value}, warnings. The copy is read too and printed (the duplicate should
    hold the same keys)."""
    fixture_id = work_copy["fixture_id"]
    data = _keys_get(fixture_id)
    print(f"keys get (fixture): clip {data['clip']}, keys {data['keys']}, warnings {data['warnings']}")
    assert data["sequence"]["id"] == fixture_id, data["sequence"]
    clip = data["clip"]
    assert clip["track_type"] == "video" and (clip["track"], clip["clip"]) == (0, 0) and clip["node_id"], clip
    assert data["effect"]["name"] == "Motion" and data["property"] == {"name": "Rotation", "index": 4}, data
    assert data["is_time_varying"] is True and data["value"] is None, data
    assert data["keys"], "V1 clip 0 of the fixture should hold Rotation keys"
    in_ticks = clip["in_ticks"]
    assert isinstance(in_ticks, str) and in_ticks.isdigit(), clip
    for key in data["keys"]:
        media = key["media_ticks"]
        assert isinstance(media, str) and media.isdigit(), key
        assert _is_number(key["value"]), key
        assert _near(key["seconds"], (int(media) - int(in_ticks)) / TICKS_PER_SECOND, 1e-6), (key, in_ticks)
    copy = _keys_get(work_copy["id"])
    same = [(k["media_ticks"], k["value"]) for k in copy["keys"]] == [(k["media_ticks"], k["value"]) for k in data["keys"]]
    print(f"keys get (copy): node {copy['clip']['node_id']}, keys {copy['keys']}; same keys as the fixture: {same}")


def test_keys_set_replaces_keys(work_copy, tmp_path):
    """A level plan for V1 clip 0 of the copy (src_in and src_out from `live clip list`): 3 keys, the last one at the
    clip end (it moves to one frame before the end), and Scale 1.5 above what it is. Done; `live keys get` shows
    exactly those 3 keys (media ticks exact, values within 0.0001); Scale reads the new value."""
    copy_id = work_copy["id"]
    clip = _clip_on(copy_id, 0, 0)
    in_ticks, length = int(clip["in_ticks"]), int(clip["end_ticks"]) - int(clip["start_ticks"])
    assert length % FRAME_TICKS == 0, f"V1 clip 0 is {length} ticks long, not whole frames of {FRAME_TICKS}"
    scale = _props(_effect_get(copy_id, "Motion"), "Motion")[1]
    assert scale["name"] in ("Scale", "Scale Height") and scale["kind"] == "number" and scale["keyframed"] is False, scale
    new_scale = round(scale["value"] + 1.5, 3)
    end_s = length / TICKS_PER_SECOND
    keys = [{"time_seconds": 0.0, "value": 1.26}, {"time_seconds": 1.247, "value": -0.07},
            {"time_seconds": end_s, "value": 0.33}]
    plan = _write_json(tmp_path, "level.json", [_level_entry(clip, scale=new_scale, keys=keys, static=False)])
    data = _ok(run_cli(*_keys_set_args(work_copy, plan), timeout=PLAN_CMD_TIMEOUT_S))
    [record] = data["results"]
    print(f"keys set: record {record}")
    assert data["counts"] == _counts(done=1) and data["stored"] is True, data
    assert data["sequence"] == {"id": copy_id, "name": COPY_NAME}, data["sequence"]
    assert record["status"] == "done" and record["mode"] == "keys", record
    [moved] = record["moved"]
    assert _near(moved["from_seconds"], end_s, 1e-6), moved
    assert _near(moved["to_seconds"], round((length - FRAME_TICKS) / TICKS_PER_SECOND, 6), 1e-6), moved
    want = [str(in_ticks + round(0.0 * TICKS_PER_SECOND)), str(in_ticks + round(1.247 * TICKS_PER_SECOND)),
            str(in_ticks + length - FRAME_TICKS)]
    got = _keys_get(copy_id)
    print(f"keys set: keys get after {got['keys']}")
    assert got["is_time_varying"] is True, got
    assert [k["media_ticks"] for k in got["keys"]] == want, (got["keys"], want)
    for key, planned in zip(got["keys"], keys):
        assert _near(key["value"], planned["value"], KEY_TOLERANCE), (key, planned)
    assert _near(_value(copy_id, "Motion", 1, scale["name"]), new_scale), f"Scale is not {new_scale}"


def test_keys_set_static_and_rerun(work_copy, tmp_path):
    """"static": true with static_value -1.49 and --no-scale: done, Rotation has no keys and reads -1.49. The same
    plan again: skipped as nothing to do (reason_code keys_match), exit 0, nothing written. The skip on the re-run
    was added after the live round of 2026-11-19 (which saw "done" again) and is unit-tested only so far."""
    copy_id = work_copy["id"]
    clip = _clip_on(copy_id, 0, 0)
    plan = _write_json(tmp_path, "level_static.json", [_level_entry(clip, static=True, static_value=-1.49)])
    for attempt in ("first", "again"):
        data = _ok(run_cli(*_keys_set_args(work_copy, plan, "--no-scale"), timeout=PLAN_CMD_TIMEOUT_S))
        [record] = data["results"]
        print(f"keys set static ({attempt}): record {record}")
        if attempt == "first":
            assert data["counts"] == _counts(done=1) and data["stored"] is True, data
            assert record["status"] == "done" and record["mode"] == "static", record
        else:
            assert data["counts"] == _counts(skipped=1) and data["stored"] is True, data
            assert record["skip"] == "nothing_to_do" and record["reason_code"] == "keys_match", record
        got = _keys_get(copy_id)
        print(f"keys set static ({attempt}): is_time_varying {got['is_time_varying']}, value {got['value']}")
        assert got["is_time_varying"] is False and got["keys"] == [], got
        assert _near(got["value"], -1.49, KEY_TOLERANCE), got


def test_keys_set_clip_changed(work_copy, tmp_path):
    """A plan whose src_in is 0.5 s off the clip: refused before any write (exit 1, code refused, the record skipped
    as refused with reason_code clip_changed), no recovery marker, and Rotation as before."""
    copy_id = work_copy["id"]
    clip = _clip_on(copy_id, 0, 0)
    entry = _level_entry(clip, keys=[{"time_seconds": 0.5, "value": 2.0}], static=False)
    entry["src_in"] += 0.5
    plan = _write_json(tmp_path, "level_changed.json", [entry])
    before = _keys_now(copy_id)
    data = _plan_failed(run_cli(*_keys_set_args(work_copy, plan, "--no-scale"), timeout=PLAN_CMD_TIMEOUT_S), "refused")
    [record] = data["results"]
    print(f"clip changed: error {data['error']!r}; record reason {record['reason']!r}")
    assert record["status"] == "skipped" and record["skip"] == "refused" and record["reason_code"] == "clip_changed", record
    assert data["counts"] == _counts(skipped=1, refused=1), data["counts"]
    assert _keys_now(copy_id) == before, "a refused keys set changed Rotation"


# ---------- live place ----------
def _testclip_item(seq_id: str) -> str:
    """The node ID of the project item testclip.mp4, from V1 clip 0 of the sequence."""
    source = _clip_on(seq_id, 0, 0)["source"]
    assert isinstance(source, dict) and source.get("name") == "testclip.mp4" and source.get("node_id"), source
    return source["node_id"]


def _shot(name: str, item: str, src_in: float, start_f: int, *, frames: int | None = 48, track: int = 1,
          audio_track: int = 0, **extra) -> dict:
    shot = {"name": name, "item": item, "src_in": src_in, "start_f": start_f, "track": track,
            "audio_track": audio_track}
    if frames is not None:
        shot["frames"] = frames
    shot.update(extra)
    return shot


def _place_args(copy: dict, plan: Path, *extra: str) -> list[str]:
    return ["live", "place", "--sequence", copy["id"], "--sequence-name", copy["name"], "--plan", str(plan), *extra]


def _place_plan(tmp_path: Path, name: str, *shots: dict) -> Path:
    return _write_json(tmp_path, name, {"fps": PLAN_FPS, "shots": list(shots)})


def _starts_at(clip: dict, start_f: int) -> bool:
    return 2 * abs(int(clip["start_ticks"]) - start_f * FRAME_TICKS) <= FRAME_TICKS


def test_place_on_a_spare_track(work_copy, app_state, tmp_path):
    """Two shots of testclip.mp4 onto V2 (sound on A1) after the end of the sequence (frames 240 and 300, 48 frames
    each): both done, each with set_item_in_out and overwrite_clip, new node IDs for picture and sound, and the
    active sequence put back. `live clip list` shows the clips at their starts. The clips are removed at the end.
    Output keys: results[] (calls, marks, now {video, audio}), counts, marks, left_switched, stopped, sequence."""
    copy_id = work_copy["id"]
    item = _testclip_item(copy_id)
    before = _node_ids(copy_id)
    active = _active_id()
    starts = (240, 300)
    plan = _place_plan(tmp_path, "shots.json", _shot("e2e A", item, 1.0, starts[0]), _shot("e2e B", item, 3.0, starts[1]))
    failure = None
    try:
        started = time.monotonic()
        data = _ok(run_cli(*_place_args(work_copy, plan), timeout=PLAN_CMD_TIMEOUT_S))
        print(f"place: {time.monotonic() - started:.1f} s; marks {data['marks']}; warnings {data['warnings']}")
        assert data["counts"] == _counts(done=2) and data["stored"] is True, data
        assert data["left_switched"] is None and data["stopped"] is False, data
        nodes = []
        for record in data["results"]:
            print(f"place: {record['name']}: now {record['now']}, calls {record['calls']}, marks {record['marks']}")
            assert record["status"] == "done" and record["target"]["node_id"] == item, record
            assert record["calls"] == [{"tool": "set_item_in_out", "ok": True}, {"tool": "overwrite_clip", "ok": True}], record
            video, audio = record["now"]["video"], record["now"]["audio"]
            assert video and audio and video["node_id"] and audio["node_id"], record["now"]
            nodes += [video["node_id"], audio["node_id"]]
        assert len(set(nodes)) == 4 and not set(nodes) & before, (nodes, before)
        assert _active_id() == active, "the active sequence was not put back"
        on_v2 = {c["node_id"]: c for c in _clips(copy_id, "video", 1)}
        for record, start_f in zip(data["results"], starts):
            clip = on_v2.get(record["now"]["video"]["node_id"])
            assert clip is not None and _starts_at(clip, start_f), (clip, start_f)
            assert clip["source"]["node_id"] == item, clip["source"]
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _after(failure, lambda: _remove_new(work_copy, before))


def test_place_rerun_is_already_placed(work_copy, app_state, tmp_path):
    """One shot onto V2 at frame 400; the same command again: exit 0, the shot skipped as nothing to do
    (reason_code already_placed), no MCP call (calls is []), the clips on the copy unchanged. Since P-T6-1 was
    fixed, a re-run with nothing to do does not switch the active sequence; that is unit-tested only
    (test_recipes_place.py) and not asserted here."""
    copy_id = work_copy["id"]
    item = _testclip_item(copy_id)
    before = _node_ids(copy_id)
    plan = _place_plan(tmp_path, "shots_rerun.json", _shot("e2e rerun", item, 2.0, 400))
    failure = None
    try:
        first = _ok(run_cli(*_place_args(work_copy, plan), timeout=PLAN_CMD_TIMEOUT_S))
        assert first["counts"] == _counts(done=1), first
        placed = _node_ids(copy_id)
        again = _ok(run_cli(*_place_args(work_copy, plan), timeout=PLAN_CMD_TIMEOUT_S))
        [record] = again["results"]
        print(f"place re-run: record {record}")
        assert again["counts"] == _counts(skipped=1) and again["stored"] is True, again
        assert record["status"] == "skipped" and record["skip"] == "nothing_to_do", record
        assert record["reason_code"] == "already_placed" and record["calls"] == [], record
        assert again["left_switched"] is None, again
        assert _node_ids(copy_id) == placed, "the re-run changed the clips of the copy"
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _after(failure, lambda: _remove_new(work_copy, before))


def test_place_sound_only(work_copy, app_state, tmp_path):
    """A shot with "audio_only": true, picture track V3, sound track A2, at frame 520: done with set_item_in_out,
    overwrite_clip and remove_from_timeline; the sound is on A2 at the start, no clip of it is left on V3. A dry run
    first: when it refuses with no_track (the copy has one audio track), one audio track is added to the copy (it is
    left there; the copy is deleted at the end)."""
    copy_id = work_copy["id"]
    item = _testclip_item(copy_id)
    before = _node_ids(copy_id)
    plan = _place_plan(tmp_path, "shots_sound.json",
                       _shot("e2e sound", item, 1.0, 520, track=2, audio_track=1, audio_only=True))
    dry = run_cli(*_place_args(work_copy, plan, "--dry-run"), timeout=PLAN_CMD_TIMEOUT_S)
    if dry.code != 0:
        refused = _plan_failed(dry, "refused")
        [record] = refused["results"]
        assert record["reason_code"] == "no_track", dry.describe()
        print(f"sound only: dry run refused ({record['reason']}); adding one audio track to the copy")
        added = _on_copy(work_copy, lambda: _tool("add_track", {"track_type": "audio", "count": 1,
                                                                **_copy_guard(work_copy)}))
        print(f"sound only: add_track replied {added!r}")
        dry = run_cli(*_place_args(work_copy, plan, "--dry-run"), timeout=PLAN_CMD_TIMEOUT_S)
    planned = _ok(dry)["planned"]
    print(f"sound only: dry run {planned}")
    assert planned[0]["steps"] == ["place", "remove_picture"], planned
    failure = None
    try:
        data = _ok(run_cli(*_place_args(work_copy, plan), timeout=PLAN_CMD_TIMEOUT_S))
        [record] = data["results"]
        print(f"sound only: record {record}")
        assert data["counts"] == _counts(done=1) and data["left_switched"] is None, data
        assert [c["tool"] for c in record["calls"]] == ["set_item_in_out", "overwrite_clip", "remove_from_timeline"], record
        assert all(c["ok"] for c in record["calls"]), record
        assert record["now"]["video"] is None and record["now"]["audio"]["node_id"], record["now"]
        sound = record["now"]["audio"]["node_id"]
        on_a2 = [c for c in _clips(copy_id, "audio", 1) if c["node_id"] == sound]
        assert len(on_a2) == 1 and _starts_at(on_a2[0], 520), on_a2
        left_on_v3 = [c for c in _clips(copy_id, "video", 2) if c["node_id"] not in before]
        assert left_on_v3 == [], f"a picture clip is left on V3: {left_on_v3}"
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _after(failure, lambda: _remove_new(work_copy, before))


# ---------- live effect apply ----------
def _apply_args(copy: dict, track: int, clips: str, *effects: str, allow_second: bool = False,
                any_order: bool = False) -> list[str]:
    args = ["live", "effect", "apply", "--sequence", copy["id"], "--sequence-name", copy["name"], "--track", str(track),
            "--clips", clips]
    for name in effects:
        args += ["--effect", name]
    if allow_second:
        args.append("--allow-second")
    if any_order:
        args.append("--any-order")
    return args


def _added(before: list[str], after: list[str]) -> list[str]:
    """The component names that `after` holds more often than `before`."""
    extra = []
    for name in dict.fromkeys(after):
        extra += [name] * max(0, after.count(name) - before.count(name))
    return extra


def _landing(before: list[str], after: list[str]) -> tuple[int, str] | None:
    """(index, name) of the one component that `after` has more than `before`, or None."""
    if len(after) != len(before) + 1:
        return None
    for index in range(len(after)):
        if after[:index] + after[index + 1:] == before:
            return index, after[index]
    return None


def test_effect_apply_procamp_then_lumetri(work_copy, app_state):
    """V1 clip 1 (Mask, Color And Contrast, VR De-Noise; no ProcAmp, no Lumetri Color): --effect ProcAmp --effect
    "Lumetri Color" are both done, render_order ends "ProcAmp > Lumetri Color", each record's effect_index names its
    effect, the active sequence is put back. Re-run: exit 0, both skipped (has_effect) with the same effect_index.
    Runs before the two measure tests below, which add more effects to this clip."""
    copy_id = work_copy["id"]
    first = _clip_on(copy_id, 0, 1)
    assert PROCAMP not in first["effects"] and LUMETRI not in first["effects"], \
        f"V1 clip 1 of the copy holds {first['effects']} already: this test needs it without ProcAmp and Lumetri Color"
    active = _active_id()
    args = _apply_args(work_copy, 0, "1", PROCAMP, LUMETRI)
    started = time.monotonic()
    data = _ok(run_cli(*args, timeout=PLAN_CMD_TIMEOUT_S))
    print(f"effect apply: {time.monotonic() - started:.1f} s; results "
          f"{[(r['effect'], r['status'], r['effect_index']) for r in data['results']]}; warnings {data['warnings']}")
    assert data["counts"] == _counts(done=2) and data["stored"] is True, data
    assert data["left_switched"] is None and data["stopped"] is False, data
    assert [r["effect"] for r in data["results"]] == [PROCAMP, LUMETRI], data["results"]
    after = _clip_on(copy_id, 0, 1)
    print(f"effect apply: components {after['effects']}, render_order {after['render_order']}")
    for record in data["results"]:
        assert record["status"] == "done" and isinstance(record["effect_index"], int), record
        assert after["effects"][record["effect_index"]] == record["effect"], (record, after["effects"])
    assert after["render_order"][-2:] == [PROCAMP, LUMETRI], after["render_order"]
    assert after["procamp_before_lumetri"] is True, after
    assert _active_id() == active, "the active sequence was not put back"
    again = _ok(run_cli(*args, timeout=PLAN_CMD_TIMEOUT_S))
    print(f"effect apply re-run: {[(r['effect'], r['status'], r['reason_code'], r['effect_index']) for r in again['results']]}")
    assert again["counts"] == _counts(skipped=2) and again["stored"] is True, again
    for record, done in zip(again["results"], data["results"]):
        assert record["status"] == "skipped" and record["skip"] == "nothing_to_do", record
        assert record["reason_code"] == "has_effect" and record["effect_index"] == done["effect_index"], (record, done)
    assert _clip_on(copy_id, 0, 1)["effects"] == after["effects"], "the re-run changed the clip"


def test_effect_names_qe_vs_display(work_copy, app_state):
    """MEASURE: the name apply_effect takes (QE) against the display name the clip then shows. ProcAmp and Lumetri
    Color on V1 clip 1 (--allow-second, so it does not matter what the clip holds already): the new components must
    carry exactly the requested names, or `effect apply` cannot count them as done. The match names are printed.
    Gyroflow is not in the fixture: test_new_procamp_index_with_three_components records it, or skips naming it."""
    copy_id = work_copy["id"]
    before = _clip_on(copy_id, 0, 1)["effects"]
    run = run_cli(*_apply_args(work_copy, 0, "1", PROCAMP, LUMETRI, allow_second=True), timeout=PLAN_CMD_TIMEOUT_S)
    after = _clip_on(copy_id, 0, 1)["effects"]
    added = _added(before, after)
    facts = f"requested {[PROCAMP, LUMETRI]}; the clip got the components {added} (before {before}, after {after})"
    print(f"effect names: {facts}; exit {run.code}, code {(run.data or {}).get('code')}")
    assert not _marker_open(), f"a recovery marker is open after effect apply.\n{run.describe()}"
    for name in (PROCAMP, LUMETRI):
        shown = _effect_get(copy_id, name, clip=1)["effects"]
        print(f"effect names: {name!r} -> {[(e['effect_index'], e['name'], e['match_name']) for e in shown]}")
    assert sorted(added) == sorted([PROCAMP, LUMETRI]), f"the display names differ from the requested names: {facts}"
    data = _ok(run)
    assert data["counts"] == _counts(done=2), data


def test_new_procamp_index_with_three_components(work_copy, app_state):
    """MEASURE, in the film's order: Gyroflow, then ProcAmp, then Lumetri Color, one `effect apply` each
    (--allow-second) on V1 clip 1, which holds the fixture's own effects (3 or more components). For each step it
    prints where the new component lands (its index in the component list, found by comparing the list before and
    after), the record's effect_index (the first component of that name) and the render_order.

    Gyroflow is applied only when list_available_effects names it. When it is not listed, or apply_effect does not
    add it, ProcAmp and Lumetri Color are still recorded, then the test SKIPS with a reason that names Gyroflow and
    holds what was recorded. A recovery marker after any step fails the test."""
    copy_id = work_copy["id"]
    listed = _tool("list_available_effects")
    entries = listed if isinstance(listed, list) else (listed or {}).get("effects") if isinstance(listed, dict) else None
    names = [e.get("name") for e in entries or [] if isinstance(e, dict) and isinstance(e.get("name"), str)]
    partial = any("partial" in str(e.get("source", "")) for e in entries or [] if isinstance(e, dict))
    gyro = [n for n in names if "gyroflow" in n.lower()]
    print(f"effect list: {len(names)} effects (partial list: {partial}); Gyroflow names: {gyro}")
    skip_reason = None
    steps = [PROCAMP, LUMETRI]
    if gyro:
        steps.insert(0, GYROFLOW if GYROFLOW in gyro else gyro[0])
    else:
        skip_reason = (f"Gyroflow is not in list_available_effects ({len(names)} effects listed, partial list: "
                       f"{partial}), so it was not applied")
    lines = []
    for name in steps:
        before = _clip_on(copy_id, 0, 1)["effects"]
        run = run_cli(*_apply_args(work_copy, 0, "1", name, allow_second=True), timeout=PLAN_CMD_TIMEOUT_S)
        clip = _clip_on(copy_id, 0, 1)
        landed = _landing(before, clip["effects"])
        record = ((run.data or {}).get("results") or [{}])[0]
        line = (f"{name}: {len(before)} components before; lands at component index "
                f"{None if landed is None else landed[0]} as {None if landed is None else landed[1]!r}; record "
                f"effect_index {record.get('effect_index')}, status {record.get('status')}; render_order "
                f"{clip['render_order']}; exit {run.code}")
        lines.append(line)
        print(f"three components: {line}")
        if _marker_open():
            pytest.fail(f"A recovery marker is open after `effect apply --effect {name}`. Run: cli-anything-premiere "
                        f"recovery show\n{line}\n{run.describe()}", pytrace=False)
        if name in gyro and landed is None:
            skip_reason = (f"Gyroflow ({name!r}) is listed, but apply_effect did not add it: "
                           f"{(run.data or {}).get('error')}")
            continue
        if name == PROCAMP:
            assert len(before) >= 3, f"the clip has only {len(before)} components before ProcAmp: {before}"
        assert landed is not None and landed[1] == name, f"no new component named {name!r}: {line}"
        assert run.code == 0 and run.data is not None and run.data.get("ok") is True, f"{line}\n{run.describe()}"
    if skip_reason:
        pytest.skip(f"{skip_reason}. Recorded anyway: " + " | ".join(lines))


# ---------- a still with an out mark past its import length ----------
def test_still_out_mark_past_import_length(work_copy, app_state, tmp_path):
    """MEASURE. A PNG frame of the copy (live frame export) is imported (import_media, suppress_ui), then
    set_item_in_out sends in 0 and out 6.0 s, past the 4.96 s a still gets at import: the reply (applied out,
    verified) is printed, and only an answer is asserted. Then `live place` places the still for 6.0 s (144 frames
    on V3 at frame 700; its own set_item_in_out sends out 6.007 s): it must be done, and V3 shows it 144 frames
    long. The placed clip is removed at the end; the PNG item stays in the unsaved project."""
    copy_id = work_copy["id"]
    folder = tmp_path / "still"
    prefix = f"e2e_still_{os.getpid()}_{int(time.time())}"
    exported = _export(copy_id, COPY_NAME, folder, 1.0, prefix=prefix)
    [png] = _check_frames(exported, folder, prefix, (1.0,))
    imported = _tool("import_media", {"file_paths": [str(png)], "suppress_ui": True, **_project_guard()})
    items = imported.get("items") if isinstance(imported, dict) else None
    assert isinstance(imported, dict) and imported.get("verified") is True and isinstance(items, list) \
        and len(items) == 1 \
        and items[0].get("nodeId"), f"import_media gave an unexpected reply: {imported!r}"
    item = str(items[0]["nodeId"])
    print(f"still: imported {png.name} as item {item}")
    marks_run = _tool_run("set_item_in_out", {"item_id": item, "in_seconds": 0.0, "out_seconds": 6.0,
                                              **_project_guard()})
    reply = (marks_run.data or {}).get("result")
    data = reply.get("data") if isinstance(reply, dict) else None
    marks = {"exit": marks_run.code, "code": (marks_run.data or {}).get("code"),
             "error": (marks_run.data or {}).get("error"),
             "applied_out": data.get("appliedOutSeconds") if isinstance(data, dict) else None,
             "verified": data.get("verified") if isinstance(data, dict) else None}
    print(f"still: set_item_in_out out 6.0 s -> {marks}")
    assert marks_run.data is not None, f"set_item_in_out gave no JSON answer.\n{marks_run.describe()}"
    before = _node_ids(copy_id)
    plan = _place_plan(tmp_path, "shots_still.json",
                       _shot("e2e still", item, 0.0, 700, frames=None, track=2, src_out=6.0))
    failure = None
    try:
        run = run_cli(*_place_args(work_copy, plan), timeout=PLAN_CMD_TIMEOUT_S)
        result = run.data or {}
        record = (result.get("results") or [{}])[0]
        print(f"still: place exit {run.code}, record {record}")
        if not (run.code == 0 and result.get("ok") is True and record.get("status") == "done"):
            pytest.fail("`live place` did NOT place the still for 6.0 s. Its out mark (6.007 s) is past the 4.96 s "
                        "the still got at import: the mark may have been refused. "
                        f"Direct set_item_in_out out 6.0 s: {marks}. Place record: status {record.get('status')}, "
                        f"error {record.get('error')!r}, calls {record.get('calls')}, marks {record.get('marks')}."
                        f"\n{run.describe()}", pytrace=False)
        node = record["now"]["video"]["node_id"]
        [clip] = [c for c in _clips(copy_id, "video", 2) if c["node_id"] == node]
        length = int(clip["end_ticks"]) - int(clip["start_ticks"])
        print(f"still: placed {node}, {length / TICKS_PER_SECOND:.6f} s long ({length / FRAME_TICKS:.3f} frames), "
              f"calls {record['calls']}, marks {record['marks']}")
        assert _starts_at(clip, 700) and 2 * abs(length - 144 * FRAME_TICKS) <= FRAME_TICKS, clip
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _after(failure, lambda: _remove_new(work_copy, before))


CONTROL_WRITES = (
    (PROCAMP, "Brightness", 0, 5),
    (PROCAMP, "Brightness", 0, -2.6),
    (LUMETRI, "Temperature", 14, 5),
    (LUMETRI, "Temperature", 14, -40),
)
MARKER_WARNING = "A recovery marker may exist now. Run: cli-anything-premiere recovery show"


def _fixture_active_again(copy: dict) -> str | None:
    """Make the fixture sequence active again and confirm it with `live state`. None, or what is wrong."""
    reason = _no_contact_reason()
    if reason:
        return (f"The fixture sequence was NOT made active again, because {reason}: nothing was sent to Premiere. "
                f"The active sequence may be {COPY_NAME!r}.")
    reply = _tool("set_active_sequence", {"sequence_id": copy["fixture_id"]})
    if not (isinstance(reply, dict) and reply.get("active") is True and str(reply.get("id")) == copy["fixture_id"]):
        return f"set_active_sequence of the fixture gave an unexpected reply: {reply!r}"
    active = _active_id()
    if active != copy["fixture_id"]:
        return f"`live state` shows the active sequence {active}, not {FIXTURE_SEQ!r} ({copy['fixture_id']})."
    return None


def test_mcp_tool_negative_control(work_copy):
    """A RECORD, not a gate. Session 76 saw 5 of 5 negative writes pass through the set_effect_property tool on
    27.0.0; this writes ProcAmp Brightness 5 then -2.6 and Lumetri Temperature (index 14) 5 then -40 through the
    tool, on V1 clip 0 of the copy by node ID, and prints one line per call. A call that fails stops the test, and
    the tool may have left a recovery marker. This is the last of the write tests for that reason."""
    copy_id, fixture_id = work_copy["id"], work_copy["fixture_id"]
    failure = None
    try:
        reply = _tool("set_active_sequence", {"sequence_id": copy_id})
        assert isinstance(reply, dict) and reply.get("active") is True and str(reply.get("id")) == copy_id, reply
        active = _active_id()
        if active != copy_id:
            pytest.skip(f"`live state` shows the active sequence {active}, not the copy {copy_id}: a write by "
                        "node ID would hit the wrong sequence")
        node, fixture_node = _first_clip_node(copy_id), _first_clip_node(fixture_id)
        if node == fixture_node:
            pytest.skip(f"V1 clip 0 of the copy and of the fixture share the node ID {node}: a write by node ID "
                        "could reach the fixture")
        print(f"negative control: copy V1 clip 0 node ID {node}, fixture {fixture_node}")
        unverified = []
        for effect, name, index, value in CONTROL_WRITES:
            run = _tool_run("set_effect_property", {"node_id": node, "effect_name": effect, "property_name": name,
                                                    "property_index": index, "value": value})
            if run.code != 0 or run.data is None or run.data.get("ok") is not True:
                error = run.data.get("error") if run.data else run.describe()
                print(f"negative control: {effect} {name} {value}: FAULT ({error})")
                pytest.fail(f"mcp call set_effect_property {effect} {name} {value} failed "
                            f"(code {run.data.get('code') if run.data else None}): {error}\n{MARKER_WARNING}",
                            pytrace=False)
            result = run.data["result"]
            reply = result.get("data") if isinstance(result, dict) else None
            passed = (isinstance(result, dict) and result.get("ok") is True and isinstance(reply, dict)
                      and reply.get("verified") is True and reply.get("outcome") == "verified"
                      and _near(reply.get("appliedValue"), value))
            applied = reply.get("appliedValue") if isinstance(reply, dict) else None
            print(f"negative control: {effect} {name} {value}: {'passed' if passed else 'NOT VERIFIED'} "
                  f"(applied {applied!r}, outcome {reply.get('outcome') if isinstance(reply, dict) else None!r})")
            if not passed:
                unverified.append((effect, name, value, result))
        assert not unverified, f"the tool answered, but the read back did not confirm: {unverified}"
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _after(failure, lambda: _fixture_active_again(work_copy))


def _leftovers() -> list[str]:
    """Names in the bridge folder that a finished command must not leave: its files and the lease."""
    names = sorted(p.name for p in BRIDGE_DIR.iterdir())
    return [n for n in names if n.startswith(("cmd_", "res_", "busy_")) or n == ".host-owner-lease"]


def _wait_clean() -> list[str]:
    left = _leftovers()
    deadline = time.monotonic() + 3         # a little time for a child process that is still leaving
    while left and time.monotonic() < deadline:
        time.sleep(0.25)
        left = _leftovers()
    return left


# Keep this test LAST: pytest runs the file in order, and it looks at what all the tests above left.
def test_bridge_folder_is_clean_after_the_run():
    left = _wait_clean()
    assert not left, f"left in {BRIDGE_DIR}: {left}"
