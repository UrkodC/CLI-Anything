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

Tests run in file order. The write tests come after the read-only ones, `test_mcp_tool_negative_control`
is the last of them (the tool may leave a recovery marker), and the last test checks the bridge folder
after everything else has run, so it must stay last in the file.
"""
from __future__ import annotations

import contextlib
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import time
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


def _after(first: BaseException | None, action) -> None:
    """Run a cleanup (call it in a `finally`) and keep the FIRST failure.

    `action` returns None when all is well, or the text of what is still wrong; it may also raise (a
    timeout of run_cli is a pytest.fail, which is not an Exception). `first` is the failure the test is
    already carrying, or None. With a first failure, the cleanup problem is printed under it and the
    first failure goes on. Without one, the problem fails the test.
    """
    try:
        problem = action()
    except (Exception, pytest.fail.Exception) as exc:
        problem = str(exc) or type(exc).__name__
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
    failure = None
    try:
        info = _make_copy(start, created)
        yield info
    except BaseException as exc:
        failure = exc
        raise
    finally:
        _after(failure, lambda: _end_check(start, created))


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
