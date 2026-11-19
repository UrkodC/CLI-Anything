"""`live lut set`: write a .cube file into the Input LUT slot of a Lumetri Color effect, and with --prove show
that the picture changed.

Premiere's script API has no "apply LUT" call. The method that works (measured by the film-edit session): write
the full .cube path plus a trailing "/" into Lumetri property 4, then 1 into property 6. Index 7 is never written.
This recipe builds on the effect recipe: resolve_entries finds and checks index 2 ("Basic Correction", checked
only), 4 (" ", a string) and 6 ("Input LUT", a number) in one read, check_entries refuses what effect set refuses,
and write_entry writes 4 then 6, each read back.

stored: index 4 reads back the text written. That is not proof for a LUT. --prove (decision 2A) exports the frame
at --time before the write and after it, through export_frames, and compares the two pictures with ffmpeg.

Order with --prove: the checks (no contact), session 1 (resolve, layout, the time inside the clip), frame A, its
pixels, session 2 (resolve again, the target must not have moved, write 4 then 6), frame B, its pixels, the
verdict. No MCP call is made while this process holds the lease: export_frames runs between the sessions.

A stop by the user (stops.is_stop, in any shape and at any place after the checks) ends the run with the code
interrupted and the payload. No write follows it and no verdict is made: before or during frame A nothing was
written; after a write, "stored" says what is known (true, false, or None for unknown) and "rendered" is None. A
write that ends as unknown_outcome keeps that code; no write follows it either.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
from pathlib import Path

from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.core.recipes import effects, frames
from cli_anything.premiere.core.recipes.stops import STOPPED, is_stop
from cli_anything.premiere.core.session import DEFAULT_HARD_LIMIT_S, WriteSession
from cli_anything.premiere.errors import CliError

LUT_PATH_INDEX, LUT_MENU_INDEX = 4, 6
LUT_BASIC_INDEX = 2
BASIC_NAME = "Basic Correction"
PATH_NAME = " "                                # one space
MENU_NAME = "Input LUT"
PROVE_THRESHOLD = 1.0                          # mean absolute difference per channel, 0 to 255
FRAME_WIDTH, FRAME_HEIGHT = 960, 540
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 3
FFMPEG_TIMEOUT_S = 60
# The film session's chain (facts.md section 5): the still over black (it may carry alpha), scaled to 960x540.
FFMPEG_FILTER = ("color=black:s=3840x2160[b];[b][0:v]scale2ref[b2][v];[b2][v]overlay=format=auto,"
                 "scale=960:540:flags=area,format=rgb24")
BEFORE_PREFIX, AFTER_PREFIX = "lut_before", "lut_after"
NOT_PROOF = "Read-back is not proof for a LUT. Use --prove."
MENU_WARNING = "Index 6 (Input LUT) reads {}. In some projects it reads 0 while the LUT renders. Use --prove."
SAME_PICTURE = ("The picture did not change. A LUT was already in the slot, so this run cannot show whether the new "
                "LUT renders.")
SAME_LUT = "The same LUT was already set."
MENU_NOT_WRITTEN = "Index 6 (Input LUT) was not written: {}. Use --prove to see if the LUT renders."
FLAT_RANGE = 8                                 # a frame whose bytes span less than this is one flat color
FLAT = ("The frame at {} s is flat (one color). It cannot show whether a LUT renders. Choose a time with a normal "
        "picture.")
UNKNOWN_CONDITIONS = ("The state of proxy display and the active sequence after the check could not be read, so "
                      "there is no verdict.")
FOREIGN = ("Someone changed proxy display or the active sequence during the check. The two frames were not taken under "
           "the same conditions, so there is no verdict.")
NOT_RENDERED = ("The exported frame at {} s did not change after the write. The path is in index 4. Check that the "
                "clip is visible at that time and that the LUT is not an identity LUT.")
MENU_STOPPED = "the run was stopped before the write was sent"
TARGET_KEYS = ("sequence_id", "sequence_name", "node_id", "track", "clip")
_TICKS = re.compile(r"-?[0-9]+")
# A finder refusal of resolve_entries that means the Lumetri Color has another layout. The sequence and effect
# refusals also say "is named X, not Y", so the match is anchored on the property.
_LAYOUT = re.compile(r"entry \d+: (property \d+ of .* is named |.* has no property \d+$)", re.S)


# ---------- pixels ----------
def frame_pixels(path: Path) -> bytes:
    """The picture of a still as raw rgb24 bytes, 960x540, composited over black (ffmpeg, at most 60 s).
    Raises CliError("ffmpeg_missing") when ffmpeg is not on PATH, CliError("ffmpeg_failed") with the last line of
    its stderr when it fails, or when it returns another number of bytes than 960 * 540 * 3."""
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise CliError("ffmpeg_missing", "ffmpeg is not on PATH. --prove needs it to compare the two frames.")
    cmd = [exe, "-v", "error", "-nostdin", "-i", str(path), "-filter_complex", FFMPEG_FILTER,
           "-frames:v", "1", "-f", "rawvideo", "-"]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise CliError("ffmpeg_failed", f"ffmpeg did not finish within {FFMPEG_TIMEOUT_S} s on {path}.") from None
    except OSError as exc:
        raise CliError("ffmpeg_failed", f"ffmpeg could not be started: {exc}.") from None
    if done.returncode != 0:
        lines = [line for line in done.stderr.decode("utf-8", "replace").splitlines() if line.strip()]
        last = lines[-1].strip() if lines else f"exit status {done.returncode}"
        raise CliError("ffmpeg_failed", f"ffmpeg failed on {path}: {last}")
    if len(done.stdout) != FRAME_BYTES:
        raise CliError("ffmpeg_failed", f"ffmpeg gave {len(done.stdout)} bytes for {path}, not {FRAME_BYTES}.")
    return done.stdout


def is_flat(pixels: bytes) -> bool:
    """True when the picture is one flat color: its bytes span less than FLAT_RANGE (an empty picture too)."""
    return not pixels or max(pixels) - min(pixels) < FLAT_RANGE


def mean_difference(a: bytes, b: bytes) -> float:
    """The mean absolute difference per byte (per channel, 0 to 255). Raises CliError("ffmpeg_failed") when the
    two pictures have another length, or none."""
    if len(a) != len(b) or not a:
        raise CliError("ffmpeg_failed", f"The two pictures cannot be compared: {len(a)} and {len(b)} bytes.")
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


# ---------- checks before any contact ----------
def _bad_lut(why: str) -> CliError:
    return CliError("bad_lut", f"{why} Nothing was sent.")


def _check_lut(lut) -> tuple[Path, str]:
    """(the LUT file resolved, the text written to index 4: its path plus "/")."""
    if not isinstance(lut, (str, os.PathLike)):
        raise _bad_lut(f"--lut must be the path of a .cube file, not {lut!r}.")
    path = Path(lut)
    if not path.is_absolute():
        raise _bad_lut(f"--lut must be an absolute path, not {path}.")
    if not path.name.lower().endswith(".cube"):
        raise _bad_lut(f"--lut must name a .cube file, not {path.name}.")
    if not path.exists():
        raise _bad_lut(f"The LUT file {path} does not exist.")
    if not path.is_file():
        raise _bad_lut(f"{path} is not a file.")
    resolved = path.resolve()
    if not resolved.name.lower().endswith(".cube"):
        raise _bad_lut(f"{path} leads to {resolved}, which is not a .cube file.")
    if effects._LONE_HALF.search(str(resolved)):
        raise _bad_lut(f"The path {resolved!r} holds bytes that are not text.")
    return resolved, str(resolved) + "/"


def _entries(target, effect_index, text: str) -> tuple[dict, list[dict]]:
    """(the sequence {"id", "name"}, the three entries: index 2, 4 and 6). The target and the two writes are
    checked by effects.check_plan (code bad_args)."""
    if not isinstance(target, dict):
        raise CliError("bad_args", f"The target must be a dict, not {type(target).__name__}. Nothing was sent.")
    unknown = sorted(set(target) - set(TARGET_KEYS))
    if unknown:
        raise CliError("bad_args", f"The target has a key this command does not know: {unknown[0]!r}. Nothing was sent.")
    clip = {key: target[key] for key in ("node_id", "track", "clip") if target.get(key) is not None}
    base = {**clip, "effect": effects.LUMETRI_MATCH}
    if effect_index is not None:
        base["effect_index"] = effect_index
    plan = {"sequence_id": target.get("sequence_id"),
            "writes": [{**base, "index": LUT_PATH_INDEX, "name": PATH_NAME, "value": text},
                       {**base, "index": LUT_MENU_INDEX, "name": MENU_NAME, "value": 1}]}
    if target.get("sequence_name") is not None:
        plan["sequence_name"] = target["sequence_name"]
    checked = effects.check_plan(plan, code="bad_args")
    path_entry, menu_entry = checked["writes"]
    basic = {key: value for key, value in path_entry.items() if key not in ("index", "name", "value")}
    basic.update(index=LUT_BASIC_INDEX, name=BASIC_NAME)
    return {"id": checked["sequence_id"], "name": checked["sequence_name"]}, [basic, path_entry, menu_entry]


def _check_time(time) -> float:
    if isinstance(time, bool) or not isinstance(time, (int, float)) or not math.isfinite(time) or time < 0:
        raise CliError("bad_time", f"--time must be a number of seconds of 0 or more, not {time!r}. Nothing was sent.")
    return float(time)


def _frame_paths(out_dir, time: float) -> tuple[Path, Path]:
    """The two frame files. A path that exists must be a file (it is replaced: --prove replaces its own files)."""
    folder = frames._folder_path(out_dir)
    paths = (folder / frames.frame_name(BEFORE_PREFIX, time, "png"),
             folder / frames.frame_name(AFTER_PREFIX, time, "png"))
    for path in paths:
        if os.path.lexists(path) and (path.is_symlink() or not path.is_file()):
            raise CliError("output_exists", f"{path} exists and is not a file, so --prove cannot replace it. "
                                            "Nothing was sent.")
    return paths


# ---------- resolve and check ----------
def _layout(why: str) -> CliError:
    return CliError("lumetri_layout", f"This Lumetri Color does not have the layout this command knows (index 2 "
                                      f"{BASIC_NAME}, index 4 a string named \" \", index 6 {MENU_NAME}): {why} "
                                      "Nothing was written.")


def _resolve(session: WriteSession, entries: list[dict]) -> list[dict]:
    """resolve_entries for the three entries, the layout check, then check_entries for the two writes. Raises
    lumetri_layout for another layout, precondition for a clip without Lumetri or another project or sequence."""
    try:
        resolved = effects.resolve_entries(session, entries)
    except CliError as err:
        if err.code == "precondition" and _LAYOUT.match(err.message):
            raise _layout(err.message + ".") from None
        raise
    _basic, path, menu = resolved
    if path["kind"] != "string":
        raise _layout(f"index 4 holds a {path['kind']}, not a string.")
    if menu["kind"] != "number":
        raise _layout(f"index 6 holds a {menu['kind']}, not a number.")
    effects.check_entries(entries[1:], resolved[1:])
    return resolved


def _slot_path(value) -> str | None:
    """The LUT path that index 4 holds, or None: a path is a string that ends with ".cube/" (any case)."""
    return value if isinstance(value, str) and value.lower().endswith(".cube/") else None


def _clip(record: dict) -> dict:
    clip = record["clip"]
    return {"node_id": clip["node_id"], "name": clip["name"], "track": clip["track"], "clip": clip["clip"],
            "start_seconds": clip["start_seconds"], "end_seconds": clip["end_seconds"]}


def _check_inside(record: dict, time: float) -> None:
    """--time (sequence seconds) must lie inside the clip: start inclusive, end exclusive."""
    clip = record["clip"]
    start, end = clip["start_ticks"], clip["end_ticks"]
    if not (isinstance(start, str) and _TICKS.fullmatch(start) and isinstance(end, str) and _TICKS.fullmatch(end)):
        raise CliError("bad_state", "The start or the end of the clip cannot be read, so --time cannot be checked. "
                                    "Nothing was written.")
    ticks = round(time * frames.TICKS_PER_SECOND)
    if not int(start) <= ticks < int(end):
        raise CliError("bad_time", f"The time {time:g} s is not inside the clip, which runs from "
                                   f"{int(start) / frames.TICKS_PER_SECOND:g} s to "
                                   f"{int(end) / frames.TICKS_PER_SECOND:g} s (the end is not inside). "
                                   "Nothing was written.")


def _same_target(old: list[dict], new: list[dict]) -> None:
    """Session 2 must find what session 1 found: the same clip, the same effect, and the same value in index 4
    (frame A shows that value). Else target_moved, nothing written."""
    if old[1]["clip"]["node_id"] != new[1]["clip"]["node_id"]:
        raise CliError("target_moved", f"The clip at this place was {old[1]['clip']['node_id']} and is "
                                       f"{new[1]['clip']['node_id']} now. Nothing was written.")
    if old[1]["effect_index"] != new[1]["effect_index"]:
        raise CliError("target_moved", f"The Lumetri Color was at effect index {old[1]['effect_index']} and is at "
                                       f"{new[1]['effect_index']} now. Nothing was written.")
    if old[1]["value_now"] != new[1]["value_now"]:
        raise CliError("target_moved", "The LUT slot (index 4) changed while the frame before was exported, so that "
                                       "frame does not show what was there before the write. Nothing was written.")


# ---------- the writes ----------
def _stopped_unsent(err: CliError) -> bool:
    """A stop that came before Premiere picked up the first send of an entry: nothing of that entry was written.
    The bridge raises interrupted for a write only before the pickup, and write_entry's `partial` counts the
    attempts that got a reply: 0 means the stop hit the first send. (With an attempt counted, an earlier send of
    the same entry went out, for example before write_entry's own put-back: that is not "nothing".)"""
    partial = getattr(err, "partial", None)
    return is_stop(err) and isinstance(partial, dict) and partial.get("attempts") == 0


def _write(session: WriteSession, entries: list[dict], resolved: list[dict], out: dict) -> None:
    """Index 4, then (when it is stored) index 6. Fills out["stored"], ["path_after"], ["menu_value"] and the
    warnings. A CliError of a write is raised with the place in its message. No write follows a write that
    raised, with one exception that is not a stop: index 6 refused before it was sent (NOTHING_SENT_CODES)."""
    out["stored"] = None                        # unknown while the write is in flight (a stop keeps it so)
    try:
        result = effects.write_entry(session, entries[1], resolved[1])
    except CliError as err:
        if _stopped_unsent(err):
            out["stored"] = False
            raise CliError("interrupted", f"{STOPPED} The write of index 4 (the LUT path) was not sent: Premiere "
                                          "had not picked it up. Nothing was written.") from None
        partial = getattr(err, "partial", None)
        out["stored"] = None if partial is not None else False
        raise CliError(err.code, f"The write of index 4 (the LUT path): {err.message}") from None
    out["path_after"] = result["now"]
    out["stored"] = result["stored"] is True
    if not out["stored"]:
        out["write_error"] = result["error"]
        return
    try:
        menu = effects.write_entry(session, entries[2], resolved[2])
    except CliError as err:
        if err.code in effects.NOTHING_SENT_CODES and getattr(err, "partial", None) is None:
            why = re.sub(r"^entry \d+, attempt \d+ of up to \d+: ", "", err.message)
            out["warnings"].append(MENU_NOT_WRITTEN.format(why.rstrip().rstrip(".")))     # refused, nothing sent: go on
            return
        if _stopped_unsent(err):                # a stop is never "go on": interrupted is not in NOTHING_SENT_CODES
            out["warnings"].append(MENU_NOT_WRITTEN.format(MENU_STOPPED))
            raise CliError("interrupted", f"{STOPPED} The LUT path in index 4 was stored. The write of index 6 "
                                          "(Input LUT) was not sent: Premiere had not picked it up.") from None
        out["warnings"].append(MENU_WARNING.format("nothing: its write failed"))
        raise CliError(err.code, f"The LUT path in index 4 was stored. Then the write of index 6 (Input LUT) "
                                 f"failed: {err.message}") from None
    out["menu_value"] = menu["now"]
    if menu["stored"] is not True:
        out["warnings"].append(MENU_WARNING.format(effects._shown(menu["now"])))


# ---------- the frames ----------
def _export(client, make_session, sequence: dict, time: float, path: Path, prefix: str, guards: dict,
            hard_limit_s: float, out: dict) -> dict:
    """One frame through export_frames. Its warnings (but the alpha note: frame_pixels composites over black) and
    its left_switched go into out; a failure is raised with export_frames' payload under out["export"] (and on the
    error as `exported`). Returns export_frames' payload."""
    try:
        result = frames.export_frames(client, make_session, sequence_id=sequence["id"],
                                      sequence_name=sequence["name"], times=[time], out_dir=path.parent,
                                      prefix=prefix, fmt="png", overwrite=False, proxies="off",
                                      hard_limit_s=hard_limit_s, **guards)
    except CliError as err:
        exported = getattr(err, "payload", None) or {}
        _take_export(out, exported)
        out["export"] = exported
        err.exported = exported
        raise
    _take_export(out, result)
    return result


def _remove_own(paths) -> None:
    """Remove this run's two frame files from an earlier run, so the folder never shows a stale pair."""
    for path in paths:
        if os.path.lexists(path):
            try:
                os.unlink(path)
            except OSError as exc:
                raise CliError("output_dir", f"Cannot remove the old frame {path}: {exc}. Nothing was "
                                             "written.") from None


def _conditions(exports: list[dict]) -> tuple[bool, bool]:
    """(foreign, unknown). foreign: someone else changed proxy display or the active sequence during an export
    (changed_by_others true), or the proxy display during the two exports was not the same. unknown: an export
    could not read the state after it (changed_by_others null, or the field is missing)."""
    foreign = unknown = False
    for exported in exports:
        changed = exported.get("changed_by_others")
        if not isinstance(changed, dict):
            unknown = True
            continue
        for item in ("proxies", "active_sequence"):
            if changed.get(item) is None:
                unknown = True
            elif changed[item]:
                foreign = True
    during = [(exported.get("proxies") or {}).get("during") for exported in exports]
    if len(during) == 2 and during[0] != during[1]:
        foreign = True
    return foreign, unknown


def _stopped(err: CliError) -> bool:
    """The export ended by a stop: the error is one (is_stop), or its payload says "stopped" (another error came
    first and kept its code)."""
    exported = getattr(err, "exported", None) or {}
    return is_stop(err) or exported.get("stopped") is True


def _take_export(out: dict, exported: dict) -> None:
    for warning in exported.get("warnings") or []:
        if warning != frames.PNG_ALPHA and warning not in out["warnings"]:
            out["warnings"].append(warning)
    if exported.get("left_switched") is not None:
        out["left_switched"] = exported["left_switched"]
    for frame in exported.get("frames") or []:
        if frame["path"] not in out["frames"]:
            out["frames"].append(frame["path"])


def _said(stored) -> str:
    return {True: "true", False: "false"}.get(stored, "unknown")


# ---------- the whole command ----------
def set_lut(client: BridgeClient, make_session, target: dict, lut: Path, *, effect_index=None, prove=False, time=None,
            out_dir=None, dry_run=False, project=None, any_project=False, allow_untested=False,
            hard_limit_s=DEFAULT_HARD_LIMIT_S, pixels=frame_pixels) -> dict:
    """Write the LUT file `lut` into the Lumetri Color of one clip: index 4 = its path plus "/", then index 6 = 1.

    target: {"sequence_id", "sequence_name" (optional), and "node_id" or "track" and "clip"} (video track and clip,
    counted from 0). effect_index picks one Lumetri Color when the clip has two. make_session(client) gives an
    McpSession for the exports of --prove; pixels(path) gives the rgb24 bytes of a still (frame_pixels).

    Returns {"sequence", "clip", "lut", "effect_index", "path_before", "path_after", "menu_value", "stored",
    "rendered", "difference", "frames", "left_switched", "warnings"}; with dry_run {"dry_run": True, ...} where
    path_after is the text that would be written and frames the files that would be made. Raises CliError; one
    raised after the checks of the arguments carries `payload` (the same keys). Not stored: not_stored. With
    --prove: a frame that changed (also a flat one) gives true; the exported frame did not change, the slot was
    empty before and frame A is not flat: not_rendered; no change with a path before or a flat frame A gives null;
    a change by someone else, or a state after an export that could not be read (unknown), gives null whatever the
    difference. Frame A fails: its error, nothing written; stopped: interrupted, nothing written. Frame B fails:
    its error, with stored as it is and rendered None; stopped: interrupted, the same.

    A stop by the user at any place after the checks ends as interrupted with the payload: no write and no export
    follows it, and rendered is None. stored is False before the write of index 4 (also when the stop came before
    Premiere picked that write up), None when a stop hit that write and it is not known whether it was sent, and
    True from the moment index 4 read back. A write that ends as unknown_outcome keeps that code.
    """
    lut_path, text = _check_lut(lut)
    sequence, entries = _entries(target, effect_index, text)
    paths: tuple[Path, Path] | None = None
    if prove:
        if time is None or out_dir is None:
            raise CliError("bad_args", "--prove needs --time and --out-dir. Nothing was sent.")
        time = _check_time(time)
        paths = _frame_paths(out_dir, time)
        if shutil.which("ffmpeg") is None:
            raise CliError("ffmpeg_missing", "ffmpeg is not on PATH. --prove needs it to compare the two frames. "
                                             "Nothing was sent.")
    elif time is not None or out_dir is not None:
        raise CliError("bad_args", "--time and --out-dir go with --prove. Nothing was sent.")
    guards = {"project": project, "any_project": any_project, "allow_untested": allow_untested}
    out = {"sequence": sequence, "clip": None, "lut": str(lut_path), "effect_index": None, "path_before": None,
           "path_after": None, "menu_value": None, "stored": False, "rendered": None, "difference": None,
           "frames": [], "left_switched": None, "warnings": [NOT_PROOF]}

    def fail(err: CliError) -> CliError:
        out.pop("write_error", None)
        err.payload = out
        return err

    def found(resolved: list[dict]) -> None:
        out["sequence"] = resolved[1]["sequence"]
        out["clip"] = _clip(resolved[1])
        out["effect_index"] = resolved[1]["effect_index"]
        out["path_before"] = _slot_path(resolved[1]["value_now"])
        if out["path_before"] == text and SAME_LUT not in out["warnings"]:
            out["warnings"].append(SAME_LUT)

    def session() -> WriteSession:
        return WriteSession(client, hard_limit_s=hard_limit_s, **guards)

    def steps() -> dict:
        """The work after the checks. Every failure inside a step leaves it as a CliError that carries the payload
        (fail). A stop between two steps, or in the verdict at the end, leaves it as it came."""
        try:
            # Session 1: find and check. Without --prove the writes follow in the same session.
            with session() as first:
                out["warnings"] += [w for w in first.warnings if w not in out["warnings"]]
                first.target(sequence["id"], sequence["name"])
                resolved = _resolve(first, entries)
                found(resolved)
                if prove:
                    _check_inside(resolved[1], time)
                if dry_run:
                    out.update(dry_run=True, stored=None, path_after=text, menu_value=resolved[2]["value_now"],
                               frames=[str(p) for p in paths] if paths else [],
                               replaces=[str(p) for p in paths if os.path.lexists(p)] if paths else [])
                    return out
                if not prove:
                    _write(first, entries, resolved, out)
            if prove:
                before_path, after_path = paths
                _remove_own(paths)
                try:
                    exports = [_export(client, make_session, out["sequence"], time, before_path, BEFORE_PREFIX, guards,
                                       hard_limit_s, out)]
                except CliError as err:
                    if _stopped(err):
                        raise CliError("interrupted", f"{STOPPED} The frame before the write was being exported. "
                                                      "Nothing was written.") from None
                    raise
                before = pixels(before_path)
                flat = is_flat(before)
                if flat:
                    out["warnings"].append(FLAT.format(f"{time:g}"))
                # Session 2: find again (node IDs can change), then write.
                with session() as second:
                    out["warnings"] += [w for w in second.warnings if w not in out["warnings"]]
                    second.target(sequence["id"], sequence["name"])
                    again = _resolve(second, entries)
                    _same_target(resolved, again)
                    _write(second, entries, again, out)
        except BaseException as exc:
            if isinstance(exc, CliError):
                raise fail(exc) from None
            if is_stop(exc):
                raise fail(CliError("interrupted", f"{STOPPED} Check Premiere before another edit. Stored so far: "
                                                   f"{_said(out['stored'])}.")) from None
            raise
        if not out["stored"]:
            error = out.get("write_error") or "it did not read back"
            raise fail(CliError("not_stored", f"The LUT path was not stored in index 4: {error}. Index 6 was not "
                                              "written."))
        if not prove:
            out.pop("write_error", None)
            return out
        # Frame B and the verdict. The LUT is written by now: every failure says so.
        try:
            late: CliError | None = None
            try:
                exports.append(_export(client, make_session, out["sequence"], time, after_path, AFTER_PREFIX, guards,
                                       hard_limit_s, out))
            except CliError as err:
                if _stopped(err):
                    raise CliError("interrupted", f"{STOPPED} The LUT was written (stored: "
                                                  f"{_said(out['stored'])}). The check was stopped, so there is no "
                                                  "verdict.") from None
                if err.code != "not_restored" or str(after_path) not in out["frames"]:
                    out["warnings"].append(f"The frame after the write could not be exported ({err.code}: "
                                           f"{err.message}), so the render was not checked. The LUT was written: "
                                           f"stored is {_said(out['stored'])}.")
                    raise CliError(err.code, f"The LUT was written (stored: {_said(out['stored'])}), but the frame "
                                             f"after the write could not be exported: {err.message}") from None
                late = err                          # the frame is there, but a switch was not put back
                exports.append(getattr(err, "exported", None) or {})
            try:
                after = pixels(after_path)
                difference = mean_difference(before, after)
            except CliError as err:
                out["warnings"].append(f"The two frames could not be compared ({err.code}: {err.message}). The LUT was "
                                       f"written: stored is {_said(out['stored'])}.")
                raise CliError(err.code, f"The LUT was written (stored: {_said(out['stored'])}), but the two "
                                         f"frames could not be compared: {err.message}") from None
        except BaseException as exc:
            if isinstance(exc, CliError):
                raise fail(exc) from None
            if is_stop(exc):
                raise fail(CliError("interrupted", f"{STOPPED} The LUT was written (stored: "
                                                   f"{_said(out['stored'])}); the render was not checked.")) from None
            raise
        out["difference"] = round(difference, 4)
        foreign, unknown = _conditions(exports)
        if foreign or unknown:
            out["rendered"] = None
            out["warnings"] += ([FOREIGN] if foreign else []) + ([UNKNOWN_CONDITIONS] if unknown else [])
        elif difference >= PROVE_THRESHOLD:
            out["rendered"] = True
        elif flat or out["path_before"] is not None:
            out["rendered"] = None                  # the FLAT warning is in already
            if out["path_before"] is not None:
                out["warnings"].append(SAME_PICTURE)
        else:
            out["rendered"] = False
            if late is None:
                raise fail(CliError("not_rendered", NOT_RENDERED.format(f"{time:g}")))
        if late is not None:
            raise fail(late)
        return out

    try:
        return steps()
    except BaseException as exc:
        if getattr(exc, "payload", None) is out or not is_stop(exc):
            raise                               # a step reported it with the payload already, or it is not a stop
        # A stop that no step handled: between two steps, or while the verdict was made. No verdict.
        out["rendered"] = None
        raise fail(CliError("interrupted", f"{STOPPED} Check Premiere before another edit. Stored so far: "
                                           f"{_said(out['stored'])}.")) from None
