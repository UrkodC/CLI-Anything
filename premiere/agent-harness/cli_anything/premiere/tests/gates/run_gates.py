"""Gate tests G1 to G5: how raw CLI scripts and the premiere-pro-mcp server live together.

This script talks to a real, running Premiere Pro. It refuses to run unless the one open project
is `fixture27 Claude.prproj`. It never saves, opens or closes a project, never acknowledges a
recovery and never sets `unsafe-script`.

Setup duplicates the fixture sequence, renames the copy to `Gate copy` and makes it active. Every
gate write goes to V1 clip 0 of that copy. Teardown (in a `finally`) deletes the copy and any
checkpoint sequence G3 made, each by ID and only after it has read the name.

Every contact with Premiere goes through two thin places of the `Io` class: `Io.raw` (one raw
script through the bridge, in its own lease) and `Io.mcp` (one tool call of an MCP session). All
decisions are small pure functions, so they can be checked offline.

Exit status (when several conditions held, the first of 6, 3, 4, 8, 5, 7, 1, 2 wins; every condition
is printed at the end): 0 done; 1 the setup failed; 2 refused (wrong project, missing tool, stale gate
copy, fixture value not a number, fixture and gate clip share a node ID); 3 a recovery marker exists;
4 Premiere did not answer in time (any timeout; a dialog may be open); 5 G1 was "bad"; 6 a write
reached the fixture sequence; 7 the teardown left a gate sequence behind, or could not show it did not;
8 stopped: interrupted (Ctrl-C, SIGTERM), a command not applied after an interrupt or give-up, an
unknown outcome of a write, or the server died during a write. On 3, 4 and 8 the teardown makes no
write and no raw read.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from cli_anything.premiere.core import bridge, guard, jsx
from cli_anything.premiere.errors import CliError

# ---------- fixed values ----------
FIXTURE_PROJECT = "fixture27 Claude.prproj"
FIXTURE_SEQ_NAME = "Fixture seq"
FIXTURE_SEQ_ID_MEASURED = "90d8aadc-3cab-4a29-8119-7a06403106cc"   # measured 2026-11-18; only compared, never trusted
GATE_NAME = "Gate copy"
CHECKPOINT_PREFIX = "[checkpoint] " + GATE_NAME
CHECKPOINT_LABEL = "gate"
PROCAMP = "AE.ADBE ProcAmp"
LUMETRI = "AE.ADBE Lumetri"
GATES = ("G1", "G2", "G3", "G4", "G5")

RESULT_PATH = Path(__file__).resolve().parent / "gates_result.json"

MCP_TIMEOUT_S = 60.0
MCP_LONG_TIMEOUT_S = 120.0
LONG_TOOLS = frozenset({"duplicate_sequence", "apply_edit_plan", "create_sequence_checkpoint"})
WRITE_TOOLS = frozenset({"duplicate_sequence", "rename_project_item", "set_active_sequence", "delete_sequence",
                         "set_effect_property", "apply_edit_plan", "create_sequence_checkpoint"})
RAW_SOFT_S = 30.0
RAW_HARD_S = 120.0
TEXT_KEPT = 2000            # characters of a tool's text kept in the evidence (full text where the brief asks)
STORE_TOLERANCE = 0.01      # "stored" means abs(read_back - written) <= this
START_TOLERANCE_S = 0.001

# Every tool this script calls, with the arguments it passes. Checked against list_tools() before any write.
REQUIRED_ARGS: dict[str, tuple[str, ...]] = {
    "duplicate_sequence": ("sequence_id",),
    "rename_project_item": ("item_id", "new_name"),
    "set_active_sequence": ("sequence_id",),
    "get_full_sequence_info": ("sequence_id",),
    "get_clip_properties": ("node_id",),
    "get_effect_properties": ("node_id", "effect_name"),
    "set_effect_property": ("node_id", "effect_name", "property_name", "property_index", "value"),
    "preview_edit_plan": ("plan",),
    "apply_edit_plan": ("plan", "confirmation_token"),
    "create_sequence_checkpoint": ("label", "sequence_id", "include_snapshot"),
    "list_sequence_checkpoints": ("sequence_id",),
    "delete_sequence": ("sequence_id",),
    "ping": (),
}

# G4: the parameters written by raw script, and the negative values in turn.
G4_PARAMS = (
    {"param": "ProcAmp Brightness", "match_name": PROCAMP, "index": 0, "display_name": "Brightness"},
    {"param": "Lumetri Temperature", "match_name": LUMETRI, "index": 14, "display_name": "Temperature"},
)
G4_NEGATIVES = (-3.7, -2.1, -2.6, -40.0, -20.0, -10.1)
G4_ROUNDS = 5
G4_POSITIVE = 5.0
G4_FAILURES_KEPT = 5

# G5
G5_REPS = 5
G5_READS = 10               # A's slow read is this many get_full_sequence_info calls in a row
G5_B_DELAY_S = 0.05         # B starts this long after A's first call began
G5_MIN_OVERLAPS = 3
G5_BEGIN_WAIT_S = 30.0
G5_JOIN_S = G5_READS * MCP_TIMEOUT_S + 60.0
G5_INFLIGHT_WAIT_S = MCP_TIMEOUT_S + 5.0    # after B timed out: the one read of A in flight, no more

STOP_MARKER = "STOP: a recovery marker exists. Run: cli-anything-premiere recovery show"

# ---------- ExtendScript bodies (ES3; values come in through ARGS only) ----------
# Sequence list for the setup, the G3 checkpoint and the teardown. Host calls follow list_sequences
# (dist/tools/discovery.js lines 90-113: project.sequences[i], seq.name, seq.sequenceID) and, for the
# project item node ID, rename_project_item (dist/tools/utility.js lines 455-458: candidate.projectItem,
# projectItem.nodeId).
SEQUENCES_BODY = r"""
var project = app.project;
if (!project) return __fail("No project is open.");
var list = [];
for (var i = 0; i < project.sequences.numSequences; i++) {
  var seq = project.sequences[i];
  var itemNodeId = null;
  try { if (seq.projectItem) itemNodeId = String(seq.projectItem.nodeId); } catch (eItem) {}
  list.push({id: String(seq.sequenceID), name: String(seq.name), item_node_id: itemNodeId});
}
return __ok(list);
"""

# One raw write of one effect property, then a read back. Host calls follow set_effect_property
# (dist/tools/keyframes.js lines 179-232): the component loop by matchName (line 183), the
# isTimeVarying refusal (line 216), prop.setValue(value, true) (line 220) and prop.getValue()
# (line 228). The sequence is found by ID as __findSequence does (dist/bridge/script-builder.js
# lines 554-564); the property by index with a display name check, as __resolveProperty does with a
# property_index (same file, lines 724-733). It never uses the active sequence, the selection or the
# playhead. Failures that stop before any write start with "precondition:".
# FIND_PROPERTY_JS is the shared finder; WRITE_PROPERTY_BODY and READ_PROPERTY_BODY add the rest.
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

# Read only: the same finder, then prop.getValue() as set_effect_property reads back
# (dist/tools/keyframes.js line 228). Used on the FIXTURE sequence by ID, to prove no write reached it.
READ_PROPERTY_BODY = FIND_PROPERTY_JS + r"""
var value = null;
var readError = null;
try { value = prop.getValue(); } catch (eRead) { readError = eRead.toString(); }
return __ok({sequence_id: String(seq.sequenceID), clip_node_id: String(clip.nodeId), property: shown,
  value: value, read_error: readError});
"""


# ---------- control flow ----------
class StopRun(BaseException):
    """Stop the whole run. A BaseException, so the per-gate `except Exception` never swallows it."""

    def __init__(self, code: int, message: str, gate_result: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.gate_result = gate_result      # the result the stopped gate is stored with; None means "inconclusive"


class SetupFailed(Exception):
    """The setup could not prepare the gate copy."""


class GateInconclusive(Exception):
    """A gate cannot go on safely (for example the gate copy is not the active sequence)."""


# Exit codes
EXIT_SETUP = 1
EXIT_REFUSED = 2
EXIT_MARKER = 3
EXIT_TIMEOUT = 4
EXIT_G1_BAD = 5
EXIT_FIXTURE_TOUCHED = 6
EXIT_LEFTOVER = 7
EXIT_STOPPED = 8
NO_TEARDOWN_CODES = (EXIT_MARKER, EXIT_TIMEOUT, EXIT_STOPPED)
# One rule for the exit status: the first code of this order that held.
EXIT_PRIORITY = (EXIT_FIXTURE_TOUCHED, EXIT_MARKER, EXIT_TIMEOUT, EXIT_STOPPED, EXIT_G1_BAD, EXIT_LEFTOVER,
                 EXIT_SETUP, EXIT_REFUSED)
# BridgeClient.send (core/bridge.py lines 358-372) raises these when it is interrupted or gives up:
# not_applied (the command was never picked up; this also swallows the KeyboardInterrupt or SIGTERM),
# unknown_outcome (a write that may have run), interrupted (a read), timeout (a read). McpSession raises
# interrupted on an interrupt and mcp_timeout when the server does not answer.
STOP_CLI_CODES = ("interrupted", "unknown_outcome", "not_applied")
TIMEOUT_CLI_CODES = ("timeout", "mcp_timeout")
TIMEOUT_LINE = "STOP: Premiere did not answer in time. A dialog may be open. Only the owner clicks it."
FIXTURE_LOUD = "A WRITE REACHED THE FIXTURE SEQUENCE"


def stop_for(err: CliError, tool: str | None = None) -> StopRun | None:
    """The StopRun a CliError calls for, or None. tool: the MCP tool of the call, None for a raw send."""
    if err.code in TIMEOUT_CLI_CODES:
        return StopRun(EXIT_TIMEOUT, f"{TIMEOUT_LINE} ({err.code}{' in ' + tool if tool else ''}: {err.message}) "
                                     "No teardown writes.")
    if err.code in STOP_CLI_CODES:
        return StopRun(EXIT_STOPPED, f"STOP: {err.code}: {err.message} No teardown writes.")
    if err.code == "mcp_died" and tool is not None and tool in WRITE_TOOLS:
        return StopRun(EXIT_STOPPED, f"STOP: the server died during the write call {tool} ({err.message}); "
                                     "its outcome is unknown. No teardown writes.")
    return None


def as_stop(exc: BaseException, tool: str | None = None) -> StopRun | None:
    """The StopRun an exception calls for: itself, an interrupt (Ctrl-C or SIGTERM), or a stopping CliError."""
    if isinstance(exc, StopRun):
        return exc
    if isinstance(exc, (KeyboardInterrupt, bridge.Interrupted)):
        return StopRun(EXIT_STOPPED, "STOP: interrupted (Ctrl-C or SIGTERM). No teardown writes.")
    if isinstance(exc, CliError):
        return stop_for(exc, tool)
    return None


def pick_exit(conditions: list[tuple[int, str]]) -> int:
    """The exit status: the first code of EXIT_PRIORITY among the conditions that held, else 0."""
    held = {code for code, _ in conditions}
    for code in EXIT_PRIORITY:
        if code in held:
            return code
    return 0


_print_lock = threading.Lock()


def say(text: str) -> None:
    with _print_lock:
        print(text, flush=True)


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def describe(exc: BaseException) -> str:
    if isinstance(exc, CliError):
        return f"{exc.code}: {exc.message}"
    return f"{type(exc).__name__}: {exc}"


def compact(value: object, limit: int = 160) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit - 3] + "..."


# ---------- pure helpers (checked offline) ----------
def payload(reply: dict | None) -> object:
    """The data of a tool reply: reply["structured"]["data"], or None."""
    if not isinstance(reply, dict):
        return None
    structured = reply.get("structured")
    if isinstance(structured, dict):
        return structured.get("data")
    return None


def brief(reply: dict, full_text: bool = False) -> dict:
    """A tool reply as evidence: error flag, text (cut unless full_text) and data."""
    text = reply.get("text") or ""
    if not full_text and len(text) > TEXT_KEPT:
        text = text[:TEXT_KEPT] + f"... [{len(reply.get('text') or '')} characters]"
    structured = reply.get("structured")
    return {"is_error": bool(reply.get("is_error")), "text": text,
            "data": payload(reply), "structured_ok": structured.get("ok") if isinstance(structured, dict) else None}


def project_file_name(entry: dict) -> str:
    path = str(entry.get("path") or "")
    return os.path.basename(path.replace("\\", "/")) if path else str(entry.get("name") or "")


def fixture_problem(state: object) -> str | None:
    """None when exactly one project is open and it is the fixture; else what is wrong."""
    if not isinstance(state, dict):
        return "The state script did not return an object."
    projects = state.get("projects") or []
    if len(projects) != 1:
        return f"Exactly one open project is needed; Premiere has {len(projects)}."
    name = project_file_name(projects[0])
    if name != FIXTURE_PROJECT:
        return f"The open project is {name!r}, not {FIXTURE_PROJECT!r}."
    return None


def tool_arg_map(tools: list[dict]) -> dict[str, set[str]]:
    """{tool name: names of its input properties} from list_tools()."""
    found: dict[str, set[str]] = {}
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
            continue
        schema = tool.get("inputSchema")
        props = schema.get("properties") if isinstance(schema, dict) else None
        found[tool["name"]] = set(props) if isinstance(props, dict) else set()
    return found


def missing_tool_args(arg_map: dict[str, set[str]], required: dict[str, tuple[str, ...]] = REQUIRED_ARGS) -> list[str]:
    """Every required tool or argument the server does not have, in words."""
    missing = []
    for tool, names in required.items():
        if tool not in arg_map:
            missing.append(f"tool {tool}")
            continue
        missing += [f"{tool}.{name}" for name in names if name not in arg_map[tool]]
    return missing


def find_new_ids(before: list[dict], after: list[dict]) -> list[str]:
    """IDs of the sequences in `after` that were not in `before`, in the order of `after`."""
    old = {str(s.get("id")) for s in before}
    return [str(s.get("id")) for s in after if str(s.get("id")) not in old]


def sequence_by_id(seqs: list[dict], seq_id: str | None) -> dict | None:
    for seq in seqs:
        if seq_id is not None and str(seq.get("id")) == str(seq_id):
            return seq
    return None


def find_v1_clip0(info: object) -> dict | None:
    """V1 clip 0 of get_full_sequence_info data, as {node_id, name, track_index, start, source_node_id}."""
    if not isinstance(info, dict):
        return None
    for track in info.get("videoTracks") or []:
        if isinstance(track, dict) and track.get("index") == 0:
            for clip in track.get("clips") or []:
                if isinstance(clip, dict) and clip.get("index") == 0 and clip.get("nodeId"):
                    return {"node_id": str(clip["nodeId"]), "name": clip.get("name"), "track_index": 0,
                            "start": clip.get("startSeconds"), "source_node_id": clip.get("sourceNodeId")}
    return None


def effect_match_names(clip_props: object) -> list[str]:
    if not isinstance(clip_props, dict):
        return []
    return [str(e.get("matchName")) for e in clip_props.get("effects") or [] if isinstance(e, dict)]


def property_value(effect_data: object, index: int, display_name: str) -> tuple[object, str | None]:
    """(value, problem) of one property in get_effect_properties data."""
    if not isinstance(effect_data, dict):
        return None, "no effect data"
    for prop in effect_data.get("properties") or []:
        if isinstance(prop, dict) and prop.get("index") == index:
            if prop.get("displayName") != display_name:
                return prop.get("value"), f"property {index} is {prop.get('displayName')!r}"
            return prop.get("value"), None
    return None, f"no property {index}"


def scan_error_text(text: str) -> dict:
    lower = (text or "").lower()
    return {"HOST_RECOVERY_REQUIRED": "host_recovery_required" in lower,
            "state_revision": "state revision" in lower or "state_revision" in lower or "staterevision" in lower,
            "recovery": "recovery" in lower}


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def close_to(value: object, wanted: float, tolerance: float = STORE_TOLERANCE) -> bool:
    return is_number(value) and abs(float(value) - wanted) <= tolerance


def cli_write_ok(cli: object, gate_node_id: str | None) -> bool:
    """The raw CLI write of G1 really stored 2 on the gate clip."""
    return (isinstance(cli, dict) and cli.get("set_error") is None and close_to(cli.get("value"), 2.0)
            and gate_node_id is not None and str(cli.get("clip_node_id")) == str(gate_node_id))


def classify_g1(first_ok: bool, first_read: object, cli: object, gate_node_id: str | None,
                second_is_error: bool | None, read_value: object, active_ok: bool, fixture_ok: bool = True) -> str:
    """good only when write 1 read back 1, the CLI write stored 2 on the gate clip, and write 2 read back 3."""
    if not fixture_ok or not first_ok or not close_to(first_read, 1.0):
        return "inconclusive"
    if not cli_write_ok(cli, gate_node_id) or second_is_error is None:
        return "inconclusive"
    if second_is_error:
        return "bad" if active_ok else "inconclusive"
    return "good" if close_to(read_value, 3.0) else "inconclusive"


def same_value(a: object, b: object) -> bool:
    """Two property values are the same (numbers within 0.0001, anything else equal)."""
    if is_number(a) and is_number(b):
        return abs(float(a) - float(b)) <= 0.0001
    return a == b


def same_clip(a_clip: dict, b_data: object) -> tuple[bool, list[str]]:
    """Does get_clip_properties data name the same clip that session A saw? (match, differences)"""
    if not isinstance(b_data, dict):
        return False, ["no clip data"]
    diffs = []
    if str(b_data.get("nodeId")) != str(a_clip.get("node_id")):
        diffs.append(f"nodeId {b_data.get('nodeId')!r} vs {a_clip.get('node_id')!r}")
    if b_data.get("name") != a_clip.get("name"):
        diffs.append(f"name {b_data.get('name')!r} vs {a_clip.get('name')!r}")
    if b_data.get("trackIndex") != a_clip.get("track_index") or b_data.get("trackType") not in (None, "video"):
        diffs.append(f"track {b_data.get('trackType')} {b_data.get('trackIndex')!r} vs video {a_clip.get('track_index')!r}")
    if not (is_number(b_data.get("start")) and is_number(a_clip.get("start"))
            and abs(float(b_data["start"]) - float(a_clip["start"])) <= START_TOLERANCE_S):
        diffs.append(f"start {b_data.get('start')!r} vs {a_clip.get('start')!r}")
    return not diffs, diffs


def classify_g2(b_is_error: bool, match: bool, active_ok: bool) -> str:
    if b_is_error:
        return "bad" if active_ok else "inconclusive"
    return "good" if match else "bad"


def build_g3_plan(info: object, gate_id: str, item_id: str) -> dict | None:
    """One insert_clip at the end of the gate copy on its first empty video track above V1.

    Inserting at the end moves no clip on any sync-locked track. None when there is no such track.
    """
    if not isinstance(info, dict) or not item_id:
        return None
    video = [t for t in info.get("videoTracks") or [] if isinstance(t, dict)]
    audio = [t for t in info.get("audioTracks") or [] if isinstance(t, dict)]

    def empty(track: dict) -> bool:
        return not (track.get("clips") or []) and not track.get("clipCount")

    free_video = sorted(t["index"] for t in video if isinstance(t.get("index"), int) and t["index"] >= 1 and empty(t))
    if not free_video:
        return None
    free_audio = sorted(t["index"] for t in audio if isinstance(t.get("index"), int) and empty(t))
    end = info.get("durationSeconds") if is_number(info.get("durationSeconds")) else 0.0
    for track in video + audio:
        for clip in track.get("clips") or []:
            if isinstance(clip, dict) and is_number(clip.get("endSeconds")):
                end = max(end, clip["endSeconds"])
    return {"sequence_id": gate_id,
            "operations": [{"type": "insert_clip", "item_id": str(item_id), "start_seconds": round(float(end), 6),
                            "video_track_index": free_video[0],
                            "audio_track_index": free_audio[0] if free_audio else 0}]}


def find_token(reply: dict | None) -> str | None:
    """The confirmation token of a preview_edit_plan reply (data.confirmationToken)."""
    data = payload(reply)
    if isinstance(data, dict) and isinstance(data.get("confirmationToken"), str) and data["confirmationToken"]:
        return data["confirmationToken"]
    return None


def pair1_outcome(token: str | None, apply_reply: dict | None, readback_clips: object = None) -> str:
    """ok only when apply passed and the target track then holds exactly one clip."""
    if token is None:
        return "no_token"
    if apply_reply is None:
        return "not_run"
    if apply_reply.get("is_error"):
        return "capability_denied" if "capabilit" in (apply_reply.get("text") or "").lower() else "failed"
    return "ok" if readback_clips == 1 else "not_verified"


def leftovers(seqs: list[dict]) -> list[dict]:
    """Gate sequences still in a sequence list: `Gate copy` or `[checkpoint] Gate copy...`."""
    return [s for s in seqs if s.get("name") == GATE_NAME or str(s.get("name", "")).startswith(CHECKPOINT_PREFIX)]


def pair2_outcome(checkpoint_id: str | None, list_reply: dict | None) -> str:
    if checkpoint_id is None:
        return "not_created"
    if list_reply is None:
        return "not_run"
    if list_reply.get("is_error"):
        return "failed"
    data = payload(list_reply)
    ids = [str(c.get("id")) for c in (data.get("checkpoints") or []) if isinstance(c, dict)] if isinstance(data, dict) else []
    return "ok" if checkpoint_id in ids else "failed"


def classify_g3(pair1: str, pair2: str) -> str:
    if "failed" in (pair1, pair2):
        return "bad"
    if pair1 == "ok" and pair2 == "ok":
        return "good"
    return "inconclusive"


def g4_schedule() -> list[tuple[float, str]]:
    """(value, kind) for one parameter: each negative value followed by +5, the six values five times round."""
    out: list[tuple[float, str]] = []
    for _ in range(G4_ROUNDS):
        for value in G4_NEGATIVES:
            out.append((value, "negative"))
            out.append((G4_POSITIVE, "positive"))
    return out


def is_stored(written: float, read_back: object) -> bool:
    return close_to(read_back, written)


def tally_g4(records: list[dict]) -> dict:
    """Counts of stored writes, negatives and +5 apart, and the first failures in full."""
    counts = {kind: {"written": 0, "stored": 0} for kind in ("negative", "positive")}
    per_param: dict[str, dict] = {}
    failures = []
    for rec in records:
        kind = rec["kind"]
        counts[kind]["written"] += 1
        bucket = per_param.setdefault(rec["param"], {"negative": [0, 0], "positive": [0, 0]})
        bucket[kind][0] += 1
        if rec["stored"]:
            counts[kind]["stored"] += 1
            bucket[kind][1] += 1
        elif len(failures) < G4_FAILURES_KEPT:
            failures.append(rec)
    errors = sorted({str(r.get("set_error") or r.get("error") or "") for r in records if not r["stored"]} - {""})
    return {"counts": counts,
            "per_param": {p: {k: f"{v[1]} of {v[0]}" for k, v in b.items()} for p, b in per_param.items()},
            "first_failures": failures,
            "failed_values": sorted({r["written"] for r in records if not r["stored"]}),
            "error_texts": errors,
            "illegal_parameter_type": any("illegal parameter type" in e.lower() for e in errors)}


def classify_g4(tally: dict, aborted: bool, expected_negatives: int = len(G4_PARAMS) * G4_ROUNDS * len(G4_NEGATIVES)) -> str:
    neg = tally["counts"]["negative"]
    if neg["written"] and neg["stored"] < neg["written"]:
        return "bad"
    if aborted or neg["written"] != expected_negatives:
        return "inconclusive"
    return "good"


def rep_overlapped(rep: dict) -> bool:
    """B's start began before A's call ended."""
    return is_number(rep.get("b_begin")) and is_number(rep.get("a_end")) and rep["b_begin"] < rep["a_end"]


def classify_g5(reps: list[dict]) -> str:
    if any(rep.get("a_error") or rep.get("b_error") for rep in reps):
        return "bad"
    if sum(1 for rep in reps if rep_overlapped(rep)) >= G5_MIN_OVERLAPS:
        return "good"
    return "inconclusive"


def plan_teardown(before: list[dict], gate_id: str | None, now: list[dict]) -> dict:
    """What the teardown deletes. Only by ID, and only after the name was seen.

    The gate copy: the sequence with gate_id, only when its name is `Gate copy`. Checkpoints: new
    sequences (not there before the setup, not the gate copy) whose name starts with
    `[checkpoint] Gate copy`. Any other new sequence is left in place and named in the notes.
    """
    notes = []
    delete_gate = False
    if gate_id is None:
        notes.append("The setup found no gate copy ID, so no gate copy is deleted.")
    else:
        seq = sequence_by_id(now, gate_id)
        if seq is None:
            notes.append(f"No sequence has the gate copy ID {gate_id}; nothing to delete there.")
        elif seq.get("name") != GATE_NAME:
            notes.append(f"The sequence {gate_id} is named {seq.get('name')!r}, not {GATE_NAME!r}; it is NOT deleted.")
        else:
            delete_gate = True
    checkpoints = []
    for seq_id in find_new_ids(before, now):
        if seq_id == gate_id:
            continue
        name = str(sequence_by_id(now, seq_id).get("name"))
        if name.startswith(CHECKPOINT_PREFIX):
            checkpoints.append({"id": seq_id, "name": name})
        else:
            notes.append(f"A new sequence {name!r} ({seq_id}) is not a gate checkpoint; it is left in place.")
    return {"delete_gate": delete_gate, "checkpoints": checkpoints, "notes": notes}


def merge_results(existing: object, started: str, premiere: dict, entries: list[dict]) -> dict:
    """The result file with these entries replacing the stored ones of the same gates."""
    kept: dict[str, dict] = {}
    if isinstance(existing, dict) and isinstance(existing.get("results"), list):
        for entry in existing["results"]:
            if isinstance(entry, dict) and isinstance(entry.get("gate"), str):
                kept[entry["gate"]] = entry
    for entry in entries:
        kept[entry["gate"]] = entry
    order = {g: n for n, g in enumerate(GATES)}
    results = sorted(kept.values(), key=lambda e: (order.get(e["gate"], 99), e["gate"]))
    return {"started": started, "premiere": premiere, "results": results}


# ---------- the two thin places that reach Premiere ----------
def pid_of(session) -> int | None:
    """The process ID of a started MCP server (McpSession keeps its Popen in _proc)."""
    proc = getattr(session, "_proc", None)
    pid = getattr(proc, "pid", None)
    return pid if isinstance(pid, int) else None


class Io:
    """Every contact with Premiere. Offline checks replace these methods."""

    def __init__(self) -> None:
        from cli_anything.premiere.core.bridge import BridgeClient
        self.raw_client = BridgeClient()

    # raw scripts through the bridge (one lease per script)
    def raw(self, body: str, args: dict | None, *, write: bool) -> object:
        """One raw script. Every interrupt and every give-up of send becomes a StopRun here (exit 4 for a
        timeout, 8 for not_applied, unknown_outcome, interrupted, Ctrl-C or SIGTERM), so no caller can turn
        it into a refusal or an "inconclusive"."""
        try:
            return self._send_raw(body, args, write=write)
        except (CliError, KeyboardInterrupt, bridge.Interrupted) as exc:
            stop = as_stop(exc)
            if stop is not None:
                raise stop from None
            raise

    def _send_raw(self, body: str, args: dict | None, *, write: bool) -> object:
        client = self.raw_client
        with client.lease():
            if write:
                guard.check_recovery(client.bridge_dir, write=True)
            return client.send(jsx.build(body, args), write=write, timeout_s=RAW_SOFT_S, hard_limit_s=RAW_HARD_S)

    def read_marker(self) -> dict | None:
        return bridge.read_recovery(self.raw_client.bridge_dir)

    def check_bridge(self) -> list[str]:
        return guard.check_bridge(bridge.read_heartbeat(self.raw_client.bridge_dir), guard.panel_version(),
                                  write=True, allow_untested=False)

    def check_private_dir(self) -> None:
        bridge.check_private_dir(self.raw_client.bridge_dir)

    # MCP sessions (each one its own BridgeClient)
    def new_session(self):
        from cli_anything.premiere.core.bridge import BridgeClient
        from cli_anything.premiere.core.mcp_client import McpSession
        return McpSession(BridgeClient())

    @contextlib.contextmanager
    def session(self, label: str):
        s = self.new_session()
        started = time.monotonic()
        try:
            s.start()
        except (CliError, KeyboardInterrupt, bridge.Interrupted) as exc:
            stop = as_stop(exc, "initialize")
            if stop is not None:
                raise stop from None
            raise
        say(f"  session {label}: started in {time.monotonic() - started:.2f} s, server pid {pid_of(s)}")
        try:
            yield s
        finally:
            s.close()
            say(f"  session {label}: closed")

    def mcp(self, session, tool: str, args: dict, *, quiet: bool = False) -> dict:
        """One tool call. Any timeout stops the run (exit 4); an interrupt, or the server dying in a write, exit 8."""
        timeout = MCP_LONG_TIMEOUT_S if tool in LONG_TOOLS else MCP_TIMEOUT_S
        if not quiet:
            say(f"  mcp {tool} {compact(args)}")
        try:
            reply = session.call(tool, args, timeout_s=timeout)
        except (CliError, KeyboardInterrupt, bridge.Interrupted) as exc:
            stop = as_stop(exc, tool)
            if stop is not None:
                raise stop from None
            raise
        if not quiet:
            say(f"    -> {'ERROR ' if reply.get('is_error') else ''}{compact(payload(reply) if not reply.get('is_error') else reply.get('text'))}")
        return reply

    def list_tools(self, session) -> list[dict]:
        return session.list_tools()


# ---------- run state ----------
class Run:
    def __init__(self, result_path: Path) -> None:
        self.result_path = result_path
        self.started = now_iso()
        self.premiere = {"version": None, "build": None}
        self.state: dict | None = None
        self.initial_active_id: str | None = None
        self.before: list[dict] = []
        self.fixture_seq_id: str | None = None
        self.gate_id: str | None = None
        self.clip: dict | None = None
        self.entries: list[dict] = []
        self.skip_teardown_writes = False
        self.fixture_brightness: object = None      # fixture V1 clip 0 ProcAmp Brightness, read in setup
        self.fixture_touched = False
        self.conditions: list[tuple[int, str]] = []    # (exit code, what held), every one is printed at the end

    def flag(self, code: int, message: str) -> None:
        """Record that an exit condition held. Idempotent per code: the first message of a code is kept."""
        if all(c != code for c, _ in self.conditions):
            self.conditions.append((code, message))
        if code in NO_TEARDOWN_CODES:
            self.skip_teardown_writes = True

    def store(self, entry: dict) -> None:
        self.entries = [e for e in self.entries if e["gate"] != entry["gate"]] + [entry]
        self.save()

    def save(self) -> None:
        try:
            existing = json.loads(self.result_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = None
        merged = merge_results(existing, self.started, self.premiere, self.entries)
        tmp = self.result_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        os.replace(tmp, self.result_path)


def note_stop(io: Io, run: Run, stop: StopRun) -> None:
    """Every stop handler calls this: flag the stop's code, and flag a recovery marker if one is on disk.

    The marker check is a file read only; it sends nothing to Premiere.
    """
    run.flag(stop.code, stop.message)
    if io.read_marker() is not None:
        run.flag(EXIT_MARKER, STOP_MARKER)


def precheck(io: Io, run: Run) -> dict:
    """Refuse (StopRun 2) unless the panel is up and exactly the fixture project is open. Returns the state."""
    marker = io.read_marker()
    if marker is not None:
        raise StopRun(3, STOP_MARKER)
    try:
        io.check_bridge()
        state = io.raw(jsx.load("state"), None, write=False)
        if not isinstance(state, dict):
            raise CliError("bad_state", "The state script did not return an object.")
        guard.check_state(state, write=True)
    except CliError as err:
        if io.read_marker() is not None:
            raise StopRun(3, STOP_MARKER) from None
        raise StopRun(2, f"Refused: {err.message}") from None
    problem = fixture_problem(state)
    if problem:
        raise StopRun(2, f"Refused: {problem}")
    run.state = state
    if run.premiere["version"] is None:
        run.premiere = {"version": state.get("version"), "build": state.get("build")}
    return state


def active_id(state: dict | None) -> str | None:
    seq = state.get("active_sequence") if isinstance(state, dict) else None
    return str(seq.get("id")) if isinstance(seq, dict) and seq.get("id") is not None else None


def read_sequences(io: Io) -> list[dict]:
    seqs = io.raw(SEQUENCES_BODY, None, write=False)
    if not isinstance(seqs, list):
        raise CliError("bad_reply", "The sequence list script did not return a list.")
    return [s for s in seqs if isinstance(s, dict)]


def require_gate_active(io: Io, run: Run, ev: dict, step: str) -> None:
    """Read the state now; raise GateInconclusive unless the gate copy is the active sequence.

    Node-ID tools (__findClip) search only the active sequence, so this runs right before each one.
    """
    state = io.raw(jsx.load("state"), None, write=False)
    found = active_id(state)
    ev.setdefault("active_checks", []).append({"step": step, "active": found})
    if found != run.gate_id:
        raise GateInconclusive(f"Before {step} the active sequence is {found}, not the gate copy {run.gate_id}; "
                               "nothing by node ID was sent.")


def ensure_active(io: Io, session, run: Run, ev: dict) -> None:
    """Make the gate copy the active sequence if the state shows another one; then check by a fresh read."""
    if active_id(run.state) != run.gate_id:
        reply = io.mcp(session, "set_active_sequence", {"sequence_id": run.gate_id})
        ev["reactivated_gate_copy"] = brief(reply, full_text=bool(reply.get("is_error")))
        if reply.get("is_error"):
            raise GateInconclusive(f"set_active_sequence on the gate copy failed: {reply.get('text')}")
    require_gate_active(io, run, ev, "the first node-ID call")


def fixture_args(run: Run) -> dict:
    return {"sequence_id": run.fixture_seq_id, "track": 0, "clip": 0, "match_name": PROCAMP, "index": 0,
            "display_name": "Brightness"}


def read_fixture_brightness(io: Io, run: Run) -> object:
    """Raw read (by sequence ID) of the fixture's V1 clip 0 ProcAmp Brightness."""
    data = io.raw(READ_PROPERTY_BODY, fixture_args(run), write=False)
    if not isinstance(data, dict) or data.get("read_error"):
        raise CliError("bad_reply", f"The fixture value could not be read: {compact(data)}")
    return data.get("value")


def check_fixture(io: Io, run: Run, ev: dict, step: str) -> bool:
    """True when the fixture value is still the setup value. Records it; on a change prints the loud line."""
    value = read_fixture_brightness(io, run)
    ev.setdefault("fixture_checks", []).append({"step": step, "value": value})
    if same_value(value, run.fixture_brightness):
        return True
    run.fixture_touched = True
    say(f"{FIXTURE_LOUD}: fixture Brightness was {run.fixture_brightness!r} at setup, is {value!r} after {step}")
    run.flag(EXIT_FIXTURE_TOUCHED, f"{FIXTURE_LOUD} (after {step}: {value!r}, setup {run.fixture_brightness!r})")
    return False


def active_after(io: Io, run: Run, ev: dict) -> bool:
    """Read the state after a gate; True when the gate copy is still the active sequence."""
    try:
        state = io.raw(jsx.load("state"), None, write=False)
    except CliError as err:
        ev["active_after"] = f"not read: {describe(err)}"
        return False
    found = active_id(state)
    ev["active_after"] = found
    return found == run.gate_id


# ---------- setup ----------
def setup(io: Io, run: Run) -> None:
    say("== setup")
    state = precheck(io, run)
    run.initial_active_id = active_id(state)
    say(f"  Premiere {run.premiere['version']} build {run.premiere['build']}; active sequence {run.initial_active_id}")
    run.before = read_sequences(io)
    say(f"  sequences before: {compact([(s['name'], s['id']) for s in run.before], 400)}")
    stale = [s for s in run.before if s.get("name") == GATE_NAME or str(s.get("name", "")).startswith(CHECKPOINT_PREFIX)]
    if stale:
        raise StopRun(2, f"Refused: a sequence from an earlier gate run exists: {compact(stale, 400)}. "
                         "Remove it by hand first. Nothing was changed.")
    fixtures = [s for s in run.before if s.get("name") == FIXTURE_SEQ_NAME]
    if len(fixtures) != 1:
        raise StopRun(2, f"Refused: {len(fixtures)} sequences are named {FIXTURE_SEQ_NAME!r}; exactly one is needed.")
    run.fixture_seq_id = fixtures[0]["id"]
    if run.fixture_seq_id != FIXTURE_SEQ_ID_MEASURED:
        say(f"  note: the fixture sequence ID {run.fixture_seq_id} differs from the measured {FIXTURE_SEQ_ID_MEASURED}")
    run.fixture_brightness = read_fixture_brightness(io, run)
    if not is_number(run.fixture_brightness):
        raise StopRun(EXIT_REFUSED, f"Refused: the fixture Brightness read {run.fixture_brightness!r} is not a number, "
                                    "so no write to the fixture could be detected. Nothing was changed.")
    say(f"  fixture V1 clip 0 ProcAmp Brightness at setup: {run.fixture_brightness!r}")

    with io.session("setup-1") as s1:
        missing = missing_tool_args(tool_arg_map(io.list_tools(s1)))
        if missing:
            raise StopRun(2, "Refused: the server lacks these tools or arguments: " + ", ".join(missing))
        say(f"  every tool and argument this script uses exists ({len(REQUIRED_ARGS)} tools)")
        dup = io.mcp(s1, "duplicate_sequence", {"sequence_id": run.fixture_seq_id})
    after = read_sequences(io)
    new_ids = find_new_ids(run.before, after)
    if len(new_ids) != 1:
        raise SetupFailed(f"Expected one new sequence after duplicate_sequence, found {len(new_ids)} "
                          f"({compact(new_ids)}); the reply was {compact(brief(dup), 600)}")
    run.gate_id = new_ids[0]
    copy = sequence_by_id(after, run.gate_id)
    say(f"  the copy is {copy.get('name')!r} ({run.gate_id}), project item {copy.get('item_node_id')}")
    if not copy.get("item_node_id"):
        raise SetupFailed("The copy has no readable project item node ID, so it cannot be renamed by ID.")

    with io.session("setup-2") as s2:
        reply = io.mcp(s2, "rename_project_item", {"item_id": copy["item_node_id"], "new_name": GATE_NAME})
    renamed = sequence_by_id(read_sequences(io), run.gate_id)
    if renamed is None or renamed.get("name") != GATE_NAME:
        raise SetupFailed(f"After the rename, the sequence {run.gate_id} is named "
                          f"{renamed.get('name') if renamed else None!r}; the reply was {compact(brief(reply), 600)}")

    with io.session("setup-3") as s3:
        reply = io.mcp(s3, "set_active_sequence", {"sequence_id": run.gate_id})
        if reply.get("is_error"):
            raise SetupFailed(f"set_active_sequence failed: {reply.get('text')}")
        info = io.mcp(s3, "get_full_sequence_info", {"sequence_id": run.gate_id})
        clip = find_v1_clip0(payload(info))
        if clip is None:
            raise SetupFailed(f"The gate copy has no V1 clip 0: {compact(brief(info), 600)}")
        props = io.mcp(s3, "get_clip_properties", {"node_id": clip["node_id"]})
        names = effect_match_names(payload(props))
        if props.get("is_error") or PROCAMP not in names or LUMETRI not in names:
            raise SetupFailed(f"V1 clip 0 of the gate copy does not carry ProcAmp and Lumetri Color: {compact(brief(props), 600)}")
        fixture_info = io.mcp(s3, "get_full_sequence_info", {"sequence_id": run.fixture_seq_id})
        fixture_clip = find_v1_clip0(payload(fixture_info))
    if fixture_clip is None:
        raise SetupFailed(f"The fixture sequence has no readable V1 clip 0: {compact(brief(fixture_info), 600)}")
    say(f"  fixture V1 clip 0 node ID {fixture_clip['node_id']}, gate V1 clip 0 node ID {clip['node_id']}")
    if fixture_clip["node_id"] == clip["node_id"]:
        raise StopRun(EXIT_REFUSED, f"Refused: the fixture and the gate copy share the clip node ID {clip['node_id']}, "
                                    "so a node-ID write could land on the fixture. No gate was run.")
    run.clip = clip
    say(f"  gate clip: {compact(clip, 300)}")


# ---------- gates ----------
def raw_write_args(run: Run, param: dict, value: float) -> dict:
    return {"sequence_id": run.gate_id, "track": 0, "clip": 0, "match_name": param["match_name"],
            "index": param["index"], "display_name": param["display_name"], "value": value}


def gate_g1(io: Io, run: Run, ev: dict) -> str:
    """Session A writes, the CLI writes raw while A is open and idle, A writes again and reads.

    Before each node-ID write a fresh state read must show the gate copy active; after each one the
    fixture's Brightness must still be its setup value.
    """
    node = run.clip["node_id"]
    target = {"node_id": node, "effect_name": "ProcAmp", "property_name": "Brightness", "property_index": 0}
    first_ok = False
    first_read = None
    cli = None
    second_is_error = None
    value = None
    fixture_ok = True
    ev["fixture_brightness_setup"] = run.fixture_brightness
    with io.session("G1-A") as a:
        ev["a_pid"] = pid_of(a)
        ensure_active(io, a, run, ev)
        r1 = io.mcp(a, "set_effect_property", {**target, "value": 1})
        ev["a_write_1"] = brief(r1)
        first_ok = not r1.get("is_error")
        fixture_ok = check_fixture(io, run, ev, "MCP write 1")
        if first_ok and fixture_ok:
            r1b = io.mcp(a, "get_effect_properties", {"node_id": node, "effect_name": "ProcAmp"})
            first_read, problem = property_value(payload(r1b), 0, "Brightness")
            ev["a_read_1"] = {"value": first_read, "problem": problem, "is_error": bool(r1b.get("is_error"))}
        if first_ok and fixture_ok and close_to(first_read, 1.0):
            say("  cli raw write Brightness = 2 (lease only for this send)")
            cli = io.raw(WRITE_PROPERTY_BODY, raw_write_args(run, G4_PARAMS[0], 2.0), write=True)
            ev["cli_write"] = cli
            ev["cli_write_ok"] = cli_write_ok(cli, node)
            say(f"    -> {compact(cli)}")
            if ev["cli_write_ok"]:
                require_gate_active(io, run, ev, "MCP write 2")
                r2 = io.mcp(a, "set_effect_property", {**target, "value": 3})
                ev["a_write_2"] = brief(r2, full_text=True)
                ev["a_write_2_flags"] = scan_error_text(r2.get("text") or "")
                second_is_error = bool(r2.get("is_error"))
                fixture_ok = check_fixture(io, run, ev, "MCP write 2")
                r3 = io.mcp(a, "get_effect_properties", {"node_id": node, "effect_name": "ProcAmp"})
                value, problem = property_value(payload(r3), 0, "Brightness")
                ev["a_read"] = {"value": value, "problem": problem, "is_error": bool(r3.get("is_error")),
                                "text": None if not r3.get("is_error") else r3.get("text")}
    ev["recovery_marker_after"] = io.read_marker()
    fixture_ok = check_fixture(io, run, ev, "the end of G1") and fixture_ok
    ev["fixture_brightness_end_of_g1"] = ev["fixture_checks"][-1]["value"] if ev.get("fixture_checks") else None
    if not fixture_ok:
        ev["fixture_touched"] = True
    ok = active_after(io, run, ev)
    return classify_g1(first_ok, first_read, cli, node, second_is_error, value, ok, fixture_ok)


def gate_g2(io: Io, run: Run, ev: dict) -> str:
    """A reads the node ID of V1 clip 0 and closes; a new session B reads the clip by that node ID."""
    with io.session("G2-A") as a:
        ev["a_pid"] = pid_of(a)
        ensure_active(io, a, run, ev)
        info = io.mcp(a, "get_full_sequence_info", {"sequence_id": run.gate_id})
        clip = find_v1_clip0(payload(info))
        ev["a_clip"] = clip
        if clip is None:
            ev["a_reply"] = brief(info)
            return "inconclusive"
    with io.session("G2-B") as b:
        ev["b_pid"] = pid_of(b)
        require_gate_active(io, run, ev, "session B's get_clip_properties")
        reply = io.mcp(b, "get_clip_properties", {"node_id": clip["node_id"]})
    ev["b_reply"] = brief(reply, full_text=bool(reply.get("is_error")))
    match, diffs = same_clip(clip, payload(reply))
    ev["same_clip"] = match
    ev["differences"] = diffs
    ok = active_after(io, run, ev)
    return classify_g2(bool(reply.get("is_error")), match, ok)


def gate_g3(io: Io, run: Run, ev: dict) -> str:
    """Preview in A, apply in B; checkpoint in A2, list in B2. Each session is a new server start."""
    token = None
    apply_reply = None
    with io.session("G3-A") as a:
        info = io.mcp(a, "get_full_sequence_info", {"sequence_id": run.gate_id})
        plan = build_g3_plan(payload(info), run.gate_id, (run.clip or {}).get("source_node_id"))
        ev["plan"] = plan
        if plan is not None:
            preview = io.mcp(a, "preview_edit_plan", {"plan": plan})
            token = find_token(preview)
            ev["preview"] = brief(preview, full_text=token is None)
            ev["token_found"] = token is not None
        else:
            ev["plan_problem"] = "the gate copy has no empty video track above V1"
    if token is not None:
        with io.session("G3-B") as b:
            apply_reply = io.mcp(b, "apply_edit_plan", {"plan": plan, "confirmation_token": token})
            ev["apply"] = brief(apply_reply, full_text=True)
            check = io.mcp(b, "get_full_sequence_info", {"sequence_id": run.gate_id})
            track = plan["operations"][0]["video_track_index"]
            tracks = (payload(check) or {}).get("videoTracks") or [] if isinstance(payload(check), dict) else []
            ev["apply_readback_clips_on_target_track"] = next(
                (len(t.get("clips") or []) for t in tracks if isinstance(t, dict) and t.get("index") == track), None)
    ev["pair1"] = pair1_outcome(token, apply_reply, ev.get("apply_readback_clips_on_target_track"))
    say(f"  pair 1 (preview, restart, apply): {ev['pair1']}")

    before_cp = read_sequences(io)
    with io.session("G3-A2") as a2:
        created = io.mcp(a2, "create_sequence_checkpoint",
                         {"label": CHECKPOINT_LABEL, "sequence_id": run.gate_id, "include_snapshot": False})
        ev["checkpoint_create"] = brief(created, full_text=bool(created.get("is_error")))
    after_cp = read_sequences(io)
    new_cp = [i for i in find_new_ids(before_cp, after_cp)
              if str(sequence_by_id(after_cp, i).get("name")).startswith(CHECKPOINT_PREFIX)]
    ev["checkpoint_new_ids"] = new_cp
    data = payload(created)
    ev["checkpoint_id_in_reply"] = (data.get("checkpoint") or {}).get("id") if isinstance(data, dict) else None
    checkpoint_id = new_cp[0] if len(new_cp) == 1 else None
    list_reply = None
    if checkpoint_id is not None:
        with io.session("G3-B2") as b2:
            list_reply = io.mcp(b2, "list_sequence_checkpoints", {"sequence_id": run.gate_id})
            ev["checkpoint_list"] = brief(list_reply)
    ev["pair2"] = pair2_outcome(checkpoint_id, list_reply)
    say(f"  pair 2 (checkpoint, restart, list): {ev['pair2']}")
    return classify_g3(ev["pair1"], ev["pair2"])


def gate_g4(io: Io, run: Run, ev: dict) -> str:
    """Raw script, one send per write: negatives each followed by +5, on two parameters; read back each."""
    records: list[dict] = []
    aborted = None
    schedule = g4_schedule()
    for param in G4_PARAMS:
        for number, (value, kind) in enumerate(schedule, 1):
            rec = {"param": param["param"], "n": number, "kind": kind, "written": value}
            try:
                data = io.raw(WRITE_PROPERTY_BODY, raw_write_args(run, param, value), write=True)
            except CliError as err:
                if err.code == "script_failed" and err.message.startswith("precondition:"):
                    aborted = f"{param['param']} write {number}: {err.message}"
                    break
                if err.code != "script_failed":
                    ev.update(tally_g4(records))
                    ev["records_done"] = len(records)
                    raise
                rec.update({"read": None, "set_error": None, "error": err.message, "stored": False})
            else:
                data = data if isinstance(data, dict) else {}
                rec.update({"read": data.get("value"), "before": data.get("before"),
                            "set_error": data.get("set_error"), "read_error": data.get("read_error")})
                rec["stored"] = rec["set_error"] is None and is_stored(value, rec["read"])
                if run.clip and data.get("clip_node_id") not in (None, run.clip["node_id"]):
                    ev.setdefault("target_node_ids_seen", sorted({data.get("clip_node_id")}))
            records.append(rec)
            say(f"  G4 {param['param']} {number}/{len(schedule)} wrote {value:g} read {rec.get('read')!r} "
                f"{'stored' if rec['stored'] else 'NOT STORED'}"
                + (f" error {rec.get('set_error') or rec.get('error')}" if not rec["stored"] else ""))
        if aborted:
            break
    tally = tally_g4(records)
    ev.update(tally)
    ev["records_done"] = len(records)
    if aborted:
        ev["aborted"] = aborted
    return classify_g4(tally, aborted is not None)


def g5_finish(rep: dict, t0: float) -> dict:
    """Overlap flag, and timestamps as seconds since the gate began."""
    rep["overlap"] = rep_overlapped(rep)
    for key in ("a_start", "a_end", "b_begin", "b_started", "b_ping_end"):
        if is_number(rep.get(key)):
            rep[key] = round(rep[key] - t0, 4)
    return rep


def g5_timeout_result(rep: dict) -> tuple[str, str]:
    """(result, why) of G5 when B timed out.

    "bad" only when A had already finished all its reads with no error at the moment B's timeout was
    decided, and the repetition overlapped (B's start began before A's call ended). Else "inconclusive".
    """
    if not rep.get("a_done_when_b_timed_out"):
        return "inconclusive", "A was still reading when B timed out, and A was stopped, so A's side is not known"
    if rep.get("a_reads") != G5_READS or rep.get("a_error"):
        return "inconclusive", "A did not answer all its reads either, so the timeout cannot be put on B"
    if not rep.get("overlap"):
        return "inconclusive", "B's start began after A's reads ended, so this was no overlap"
    return "bad", ("A had answered all its reads, the repetition overlapped, and B's " + str(rep.get("b_phase"))
                   + " timed out (collision evidence)")


def g5_rep(io: Io, run: Run, a, pool: ThreadPoolExecutor, number: int, t0: float, halt: threading.Event,
           reps: list) -> dict:
    """One repetition: A's slow read in the worker thread; B starts and pings in this thread.

    `halt` is set as soon as anything stops the run; the worker checks it before each read, so it sends
    no further read after a stop.
    """
    began = threading.Event()
    rep: dict = {"rep": number}
    b_stop: StopRun | None = None

    def worker() -> dict:
        out: dict = {"a_start": time.monotonic()}
        began.set()
        reads = 0
        for _ in range(G5_READS):
            if halt.is_set():
                out["halted"] = True
                break
            try:
                reply = io.mcp(a, "get_full_sequence_info", {"sequence_id": run.gate_id}, quiet=True)
            except CliError as err:
                out["a_error"] = describe(err)
                break
            if reply.get("is_error"):
                out["a_error"] = (reply.get("text") or "tool error")[:TEXT_KEPT]
                break
            reads += 1
        out["a_end"] = time.monotonic()
        out["a_reads"] = reads
        return out

    future = pool.submit(worker)
    try:
        if not began.wait(G5_BEGIN_WAIT_S):
            rep["a_error"] = "the worker thread did not begin"
        else:
            # B's start begins G5_B_DELAY_S after A's first call began. a_start is set before began.
            time.sleep(G5_B_DELAY_S)
            rep["b_begin"] = time.monotonic()
            b = io.new_session()
            phase = "initialize"
            try:
                b.start()
                rep["b_started"] = time.monotonic()
                rep["b_pid"] = pid_of(b)
                phase = "ping"
                reply = io.mcp(b, "ping", {}, quiet=True)
                rep["b_ping_end"] = time.monotonic()
                if reply.get("is_error"):
                    rep["b_error"] = (reply.get("text") or "tool error")[:TEXT_KEPT]
                    rep["b_phase"] = "ping"
            except BaseException as exc:
                stop = as_stop(exc, phase)
                if stop is None and isinstance(exc, CliError):
                    # A failure of B that is not a stop (for example the server died in initialize or ping):
                    # recorded, and the repetition goes on.
                    rep["b_error"] = describe(exc)
                    rep["b_phase"] = phase
                elif stop is not None and stop.code == EXIT_TIMEOUT:
                    # R16: a timeout stops the run. Halt A at once (no new read), then keep the repetition:
                    # B's start or ping hanging while A reads may be the very collision G5 looks for.
                    halt.set()
                    rep["a_done_when_b_timed_out"] = future.done()
                    b_stop = stop
                    rep["b_error"] = describe(exc) if isinstance(exc, CliError) else stop.message
                    rep["b_phase"] = phase
                elif stop is not None:
                    raise stop from None
                else:
                    raise
            finally:
                b.close()
        # After B's timeout only the one read in flight is waited for (A was halted).
        wait_s = G5_INFLIGHT_WAIT_S if b_stop is not None else G5_JOIN_S
        interrupt: StopRun | None = None
        try:
            rep.update(future.result(timeout=wait_s))
        except FutureTimeout:
            rep["a_error"] = f"session A's read did not end within {wait_s:g} s"
        except (KeyboardInterrupt, bridge.Interrupted) as exc:
            if b_stop is None:
                raise
            interrupt = as_stop(exc)
            rep["a_error"] = "interrupted while waiting for A's read in flight"
        except (StopRun, CliError) as exc:
            if b_stop is None:
                raise
            rep["a_error"] = exc.message if isinstance(exc, StopRun) else describe(exc)
        if b_stop is not None:
            reps.append(g5_finish(rep, t0))
            result, why = g5_timeout_result(rep)
            b_stop.gate_result = result
            say(f"  G5 rep {number}: B's {rep['b_phase']} timed out; G5 is stored as {result!r} because {why}. "
                f"A {rep.get('a_start')}..{rep.get('a_end')} ({rep.get('a_reads')} reads), B start {rep.get('b_begin')}")
            if interrupt is not None:
                run.flag(b_stop.code, b_stop.message)          # both conditions held: the timeout and the interrupt
                interrupt.gate_result = result
                raise interrupt
            raise b_stop
    except BaseException as exc:
        halt.set()                     # the worker sends no further read
        stop = as_stop(exc)
        if stop is not None:
            raise stop from None
        raise
    return g5_finish(rep, t0)


def gate_g5(io: Io, run: Run, ev: dict) -> str:
    """Five times: A runs a slow read in a thread while B starts and pings."""
    reps = []
    t0 = time.monotonic()
    ev["reps"] = reps
    halt = threading.Event()
    with io.session("G5-A") as a:
        ev["a_pid"] = pid_of(a)
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                for number in range(1, G5_REPS + 1):
                    rep = g5_rep(io, run, a, pool, number, t0, halt, reps)
                    reps.append(rep)
                    say(f"  G5 rep {number}: A {rep.get('a_start')}..{rep.get('a_end')} ({rep.get('a_reads')} reads), "
                        f"B start {rep.get('b_begin')}..{rep.get('b_started')}, ping end {rep.get('b_ping_end')}, "
                        f"overlap {rep['overlap']}, B pid {rep.get('b_pid')}"
                        + (f", A error {rep['a_error']}" if rep.get("a_error") else "")
                        + (f", B error {rep['b_error']}" if rep.get("b_error") else ""))
            except BaseException:
                halt.set()
                raise
    ev["overlaps"] = sum(1 for r in reps if r["overlap"])
    return classify_g5(reps)


GATE_FUNCS = {"G1": gate_g1, "G2": gate_g2, "G3": gate_g3, "G4": gate_g4, "G5": gate_g5}


def run_gate(io: Io, run: Run, name: str) -> dict:
    """Run one gate with its checks before and after. Stores the entry; raises StopRun to stop the run.

    A StopRun, an interrupt (Ctrl-C, SIGTERM) or a stopping CliError stops the run. Any other error makes
    this gate "inconclusive" and the run goes on.
    """
    say(f"== {name}")
    ev: dict = {"run_started": run.started}
    entry = {"gate": name, "result": "inconclusive", "evidence": ev}
    try:
        precheck(io, run)
        entry["result"] = GATE_FUNCS[name](io, run, ev)
    except BaseException as exc:
        stop = as_stop(exc)
        if stop is None and not isinstance(exc, Exception):
            raise                                   # SystemExit and the like
        ev["error"] = describe(exc)
        entry["result"] = "inconclusive"
        if stop is not None:
            entry["result"] = stop.gate_result or "inconclusive"
            ev["stopped"] = stop.message
            marker = io.read_marker()
            if marker is not None:
                ev["recovery_marker"] = marker
            note_stop(io, run, stop)
            run.store(entry)
            raise stop from None
    marker = io.read_marker()
    if marker is not None:
        ev["recovery_marker"] = marker
        run.store(entry)
        say(f"RESULT {name}: {entry['result']} (recovery marker after the gate)")
        raise StopRun(EXIT_MARKER, STOP_MARKER)
    run.store(entry)
    say(f"RESULT {name}: {entry['result']} {compact(ev, 300)}")
    return entry


# ---------- teardown ----------
def teardown(io: Io, run: Run, keep: bool) -> None:
    """Delete the gate copy and gate checkpoints by ID, after reading their names. Flags its conditions on run.

    After a stop (3, 4, 8), before it or during it, it makes no write and no raw read at all: a raw read
    could reap a lease that a killed server held, and send a command into a Premiere that may be blocked.
    """
    say("== teardown")
    try:
        _teardown(io, run, keep)
    except BaseException as exc:
        stop = as_stop(exc)
        if stop is None:
            raise
        say(f"  teardown stopped: {stop.message}")
        note_stop(io, run, stop)
        say("Sequences in Premiere now: not read (the teardown stopped)")


def _reraise_stop(exc: BaseException) -> None:
    stop = as_stop(exc)
    if stop is not None:
        raise stop from None


def _teardown(io: Io, run: Run, keep: bool) -> None:
    if run.skip_teardown_writes:
        say("  teardown skipped: no writes and no raw read, because the run stopped "
            "(recovery marker, timeout, interruption, unknown outcome, or a server that died in a write)")
        if io.read_marker() is not None:
            say(STOP_MARKER)
            run.flag(EXIT_MARKER, STOP_MARKER)
        say("Sequences in Premiere now: not read (the run stopped)")
        return
    deleted_ok = None          # None: nothing was to be deleted; True: every delete replied ok; False: not
    if keep:
        say("  --keep: the gate copy and its checkpoints stay")
    elif run.gate_id is None and not run.before:
        say("  nothing to remove: the setup made no sequence")
    else:
        deleted_ok = False
        try:
            precheck(io, run)
            now = read_sequences(io)
            plan = plan_teardown(run.before, run.gate_id, now)
            for note in plan["notes"]:
                say(f"  note: {note}")
            replies = []
            if plan["delete_gate"] or plan["checkpoints"]:
                with io.session("teardown") as s:
                    restore = run.initial_active_id
                    if restore and restore != run.gate_id and sequence_by_id(now, restore) is not None:
                        io.mcp(s, "set_active_sequence", {"sequence_id": restore})
                    for cp in plan["checkpoints"]:
                        replies.append(io.mcp(s, "delete_sequence", {"sequence_id": cp["id"]}))
                    if plan["delete_gate"]:
                        replies.append(io.mcp(s, "delete_sequence", {"sequence_id": run.gate_id}))
            deleted_ok = all(not r.get("is_error") for r in replies)
        except StopRun as stop:
            if stop.code in NO_TEARDOWN_CODES:
                raise
            say(f"  teardown refused: {stop.message}")
        except Exception as exc:
            _reraise_stop(exc)
            say(f"  teardown failed: {describe(exc)}")
    if io.read_marker() is not None:
        say(STOP_MARKER)
        run.flag(EXIT_MARKER, STOP_MARKER)
        say("Sequences in Premiere now: not read (a recovery marker exists)")
        return
    if run.fixture_seq_id is not None and is_number(run.fixture_brightness):
        try:
            value = read_fixture_brightness(io, run)
            say(f"  fixture Brightness at the end of the run: {value!r} (setup: {run.fixture_brightness!r})")
            changed = not same_value(value, run.fixture_brightness)
            for entry in run.entries:
                if entry["gate"] == "G1":
                    entry["evidence"]["fixture_brightness_end_of_run"] = value
                    if changed and entry["result"] == "good":
                        entry["result"] = "inconclusive"
                        entry["evidence"]["downgraded"] = ("G1 was good, but the fixture Brightness changed by the "
                                                           f"end of the run ({run.fixture_brightness!r} to {value!r})")
                    run.save()
            if changed:
                run.fixture_touched = True
                say(f"{FIXTURE_LOUD}: fixture Brightness was {run.fixture_brightness!r} at setup, "
                    f"is {value!r} at the end of the run")
                run.flag(EXIT_FIXTURE_TOUCHED, f"{FIXTURE_LOUD} (end of run: {value!r}, setup {run.fixture_brightness!r})")
        except Exception as exc:
            _reraise_stop(exc)
            say(f"  fixture Brightness at the end of the run: not read ({describe(exc)})")
    try:
        seqs = read_sequences(io)
        say("Sequences in Premiere now:")
        for seq in seqs:
            say(f"  {seq.get('name')}  {seq.get('id')}")
        left = leftovers(seqs)
        if left and not keep:
            message = f"TEARDOWN LEFT GATE SEQUENCES BEHIND: {compact([(x.get('name'), x.get('id')) for x in left], 400)}"
            say(message)
            run.flag(EXIT_LEFTOVER, message)
    except Exception as exc:
        _reraise_stop(exc)
        say(f"Sequences in Premiere now: not read ({describe(exc)})")
        if not keep and deleted_ok is False:
            message = "The teardown did not delete everything and the final sequence list could not be read."
            say(message)
            run.flag(EXIT_LEFTOVER, message)


# ---------- main ----------
def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_gates.py",
        description="Gate tests G1 to G5 on the running Premiere (fixture27 Claude.prproj only). "
                    "Writes gates_result.json next to this script.",
        epilog="Exit status: 0 done; 1 setup failed; 2 refused; 3 a recovery marker exists "
               "(run: cli-anything-premiere recovery show); 4 Premiere did not answer in time (a dialog may be "
               "open); 5 G1 was bad (the plan stops; the transport choice goes back to Urko); 6 a write reached "
               "the fixture sequence; 7 the teardown left a gate sequence behind; 8 stopped (interrupted, "
               "command not applied, unknown outcome, or the server died in a write). Priority: 6, 3, 4, 8, 5, "
               "7, 1, 2. On 3, 4 and 8 the teardown makes no write and no raw read.")
    parser.add_argument("--gate", required=True, choices=list(GATES) + ["all"], help="Which gate to run.")
    parser.add_argument("--keep", action="store_true", help="Do not delete the gate copy at the end.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, io: Io | None = None, result_path: Path | None = None) -> int:
    args = parse_args(argv)
    gates = list(GATES) if args.gate == "all" else [args.gate]
    bridge.install_signal_handlers()
    io = io or Io()
    run = Run(result_path or RESULT_PATH)
    say(f"Gate run {run.started}: {', '.join(gates)}")
    try:
        try:
            io.check_private_dir()
            setup(io, run)
            for name in gates:
                entry = run_gate(io, run, name)
                if run.fixture_touched:
                    say(f"{FIXTURE_LOUD}. The run stops here (exit 6); no further gates.")
                    break
                if name == "G1" and entry["result"] == "bad":
                    message = "G1 is bad: the plan stops here. Report to Urko: the transport choice goes back to him."
                    say(message)
                    run.flag(EXIT_G1_BAD, message)
                    break
        except BaseException as exc:
            stop = as_stop(exc)
            if stop is not None:
                say(stop.message)
                note_stop(io, run, stop)               # 3, 4 and 8 also set skip_teardown_writes
            elif isinstance(exc, SetupFailed):
                say(f"SETUP FAILED: {exc}")
                run.flag(EXIT_SETUP, f"setup failed: {exc}")
            elif isinstance(exc, Exception):
                say(f"FAILED: {describe(exc)}")
                traceback.print_exc()
                run.flag(EXIT_SETUP, f"failed: {describe(exc)}")
            else:
                raise
        teardown(io, run, args.keep)
    except BaseException as exc:
        # An interrupt that came after the teardown began, outside its own handling.
        stop = as_stop(exc)
        if stop is None:
            raise
        say(stop.message)
        note_stop(io, run, stop)
    code = pick_exit(run.conditions)
    say(f"Results: {run.result_path}" + ("" if run.entries else " (no gate result was stored)"))
    for entry in run.entries:
        say(f"  {entry['gate']}: {entry['result']}")
    chosen = next((m for c, m in run.conditions if c == code), "done")
    say(f"EXIT {code}: {chosen}")
    for c, m in run.conditions:
        if (c, m) != (code, chosen):
            say(f"  also: exit {c}: {m}")
    return code


if __name__ == "__main__":
    sys.exit(main())
