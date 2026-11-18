"""`live frame export` and `live app`: still frames of one sequence, and the two app switches they need.

The server's tool export_frame exports the ACTIVE sequence, with proxy display as it is. So the export switches
proxy display off and makes the sequence active (each a write with its own read-back), gives the lease back, runs
the exports through one MCP session, then takes the lease again and puts both back. Three rules meet here:

- No MCP call while this process holds the lease (the server takes the same lease per tool).
- A command puts back what it switched.
- No write while a recovery marker exists (a failed export leaves one). Then nothing more is written, and the
  payload's left_switched and warnings say what is still switched and how to put it back (decision 1A).

The work runs in two write sessions. Session A: target, read, checks, switches, the confirming state read.
Then no lease: one MCP session for all frames; its outcome is kept in variables. Session B (always when
something was switched and not yet put back): the put-back.
"""
from __future__ import annotations

import math
import os
import shlex
import stat
import unicodedata
from pathlib import Path

from cli_anything.premiere.core import bridge, guard, jsx
from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.core.session import DEFAULT_HARD_LIMIT_S, WriteSession, guarded_state
from cli_anything.premiere.errors import CliError

EXPORT_TIMEOUT_S = 600.0
FORMATS = ("png", "jpg", "tif")
PROXY_CHOICES = ("off", "keep")
TICKS_PER_SECOND = 254016000000
PROGRAM = "cli-anything-premiere"
PNG_ALPHA = "A PNG still can carry alpha. Composite it over black before you measure it."
GUARD_REFUSED = "guard refused"
STOPPED = "Stopped by Ctrl-C or a stop signal."


def frame_name(prefix: str, seconds: float, fmt: str) -> str:
    """The file name of the frame at `seconds`: frame_00001000ms.png for 1 s."""
    return f"{prefix}_{round(seconds * 1000):08d}ms.{fmt}"


# ---------- checks before any contact ----------
def _check_times(times) -> list[float]:
    if not times:
        raise CliError("bad_time", "Give at least one --time. Nothing was sent.")
    checked: list[float] = []
    by_ms: dict[int, float] = {}
    for value in times:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise CliError("bad_time", f"A time must be a number of seconds of 0 or more, not {value!r}. "
                                       "Nothing was sent.")
        value = float(value)
        ms = round(value * 1000)
        if ms in by_ms:
            raise CliError("bad_time", f"The times {by_ms[ms]:g} and {value:g} fall on the same millisecond, so they "
                                       "would give the same file name. Nothing was sent.")
        by_ms[ms] = value
        checked.append(value)
    return checked


def _check_options(sequence_id, prefix, fmt, proxies) -> None:
    if not isinstance(sequence_id, str) or not sequence_id:
        raise CliError("bad_args", "--sequence must name a sequence ID (see `live state`). Nothing was sent.")
    if not isinstance(prefix, str) or not prefix:
        raise CliError("bad_args", "--prefix must not be empty. Nothing was sent.")
    for sep in ("/", os.sep, os.altsep, "\x00"):
        if sep and sep in prefix:
            raise CliError("bad_args", f"--prefix must be a plain name without {sep!r}: the folder goes in --out-dir. "
                                       "Nothing was sent.")
    if fmt not in FORMATS:
        raise CliError("bad_args", f"--format must be one of {', '.join(FORMATS)}, not {fmt!r}. Nothing was sent.")
    if proxies not in PROXY_CHOICES:
        raise CliError("bad_args", f"--proxies must be off or keep, not {proxies!r}. Nothing was sent.")


def _folder_path(out_dir) -> Path:
    """The output folder as an absolute path, not made yet. A folder on a disk that is not mounted is refused, so
    no folder is made under /Volumes by mistake; a path that exists and is not a folder is refused too."""
    folder = Path(os.path.abspath(os.path.expanduser(str(out_dir))))
    parts = folder.parts
    if len(parts) >= 3 and parts[1] == "Volumes" and not os.path.isdir(os.path.join(os.sep, "Volumes", parts[2])):
        raise CliError("output_dir", f"The disk {parts[2]} is not mounted (/Volumes/{parts[2]} does not exist), so "
                                     f"the folder {folder} cannot be made. Nothing was sent.")
    if os.path.lexists(folder) and not folder.is_dir():
        raise CliError("output_dir", f"{folder} exists and is not a folder. Nothing was sent.")
    return folder


def _make_folder(folder: Path) -> Path:
    """Make the output folder with its parents (a real run only, before any contact with Premiere)."""
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CliError("output_dir", f"Cannot make the output folder {folder}: {exc}. Nothing was sent.") from None
    if not folder.is_dir() or not os.access(folder, os.W_OK | os.X_OK):
        raise CliError("output_dir", f"The output folder {folder} cannot be written to. Nothing was sent.")
    return folder


def _check_targets(paths: list[Path], overwrite: bool) -> None:
    for path in paths:
        if not os.path.lexists(path):
            continue
        if not overwrite:
            raise CliError("output_exists", f"{path} exists already. Use --overwrite to replace it, or another "
                                            "--prefix or --out-dir. Nothing was sent.")
        if not path.is_file():
            raise CliError("output_exists", f"{path} exists and is not a file, so it cannot be replaced. "
                                            "Nothing was sent.")


# ---------- the script and its replies ----------
def _ref(value, what: str) -> dict | None:
    """A sequence reference {id, name} from a reply, or None. Anything else is bad_reply."""
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not value["id"] \
            or not (value.get("name") is None or isinstance(value.get("name"), str)):
        raise CliError("bad_reply", f"The app_switch script gave a bad {what}: {value!r}.")
    return {"id": value["id"], "name": value.get("name")}


def _proxy_value(value, what: str):
    if value is None or (type(value) is int and value in (0, 1)):
        return value
    raise CliError("bad_reply", f"The app_switch script gave a bad {what}: {value!r}.")


def _read_app(session: WriteSession, sequence_id: str | None) -> dict:
    """action read: {"proxies": 1 | 0 | None, "active_sequence": ref | None, "sequence": {id, name, end_ticks}
    | None}. With a sequence_id, the sequence must be that one."""
    reply = session.read(jsx.load("app_switch"), {"action": "read"})
    if not isinstance(reply, dict):
        raise CliError("bad_reply", "The app_switch script did not return an object.")
    out = {"proxies": _proxy_value(reply.get("proxies"), "proxy state"),
           "active_sequence": _ref(reply.get("active_sequence"), "active sequence"), "sequence": None}
    if sequence_id is None:
        return out
    seq = reply.get("sequence")
    ref = _ref(seq, "sequence")
    if ref is None or ref["id"] != sequence_id:
        raise CliError("bad_reply", f"The app_switch script read the sequence {seq!r}, not {sequence_id}.")
    end = seq.get("end_ticks")
    if not (end is None or (isinstance(end, str) and end.isdigit() and end.isascii())):
        raise CliError("bad_reply", f"The app_switch script gave a bad end of the sequence: {end!r}.")
    out["sequence"] = {**ref, "end_ticks": end}
    return out


def _set_proxies(session: WriteSession, enabled: bool) -> dict:
    """action proxies: {"before", "after", "set_error"}; after is the read-back in the same script."""
    reply = session.write(jsx.load("app_switch"), {"action": "proxies", "enabled": enabled})
    if not isinstance(reply, dict) or not (reply.get("set_error") is None or isinstance(reply.get("set_error"), str)):
        raise CliError("bad_reply", f"The app_switch script gave a bad reply to the proxy switch: {reply!r}.")
    return {"before": _proxy_value(reply.get("before"), "proxy state"),
            "after": _proxy_value(reply.get("after"), "proxy state"), "set_error": reply.get("set_error")}


def _activate(session: WriteSession, sequence_id: str) -> dict:
    """action activate: {"before", "after", "set_error"} with sequence references; after is the read-back."""
    reply = session.write(jsx.load("app_switch"), {"action": "activate", "sequence_id": sequence_id})
    if not isinstance(reply, dict) or not (reply.get("set_error") is None or isinstance(reply.get("set_error"), str)):
        raise CliError("bad_reply", f"The app_switch script gave a bad reply to the activation: {reply!r}.")
    return {"before": _ref(reply.get("before"), "active sequence"),
            "after": _ref(reply.get("after"), "active sequence"), "set_error": reply.get("set_error")}


def _flag(value) -> bool | None:
    return None if value is None else value == 1


def _id(ref) -> str | None:
    return ref.get("id") if isinstance(ref, dict) else None


def _shown(ref) -> str:
    if not isinstance(ref, dict):
        return "no sequence"
    return f"{ref.get('name') or '?'} ({ref.get('id')})"


def _confirm_active(client: BridgeClient, guards: dict, project_path: str, sequence_id: str) -> tuple[dict, list]:
    """A fresh guarded state read (write guards). Returns (state, warnings). The active project must be the one
    the session started with (project_mismatch) and the active sequence the target (not_switched)."""
    state, warnings = guarded_state(client, write=True, project=guards["project"], any_project=guards["any_project"],
                                    allow_untested=guards["allow_untested"], hard_limit_s=guards["hard_limit_s"])
    active = guard.active_project(state.get("projects") or [])
    path = active.get("path") if active else None
    if path != project_path:
        raise CliError("project_mismatch", f"The active project changed from {project_path} to {path}. Nothing was "
                                           "exported.")
    return state, warnings


# ---------- switches and the put-back ----------
def _new_switches() -> dict:
    """What the command switched. *_before: as read at the start. *_now: the last value known (None: unknown).
    *_sent: a switch write was sent. *_reason: why the put-back failed, for the warning line. *_foreign: someone
    else changed it during the export (session B's read), so it is left as it is."""
    return {"proxies_before": None, "proxies_now": None, "proxies_during": None, "proxies_sent": False,
            "proxies_reason": None,
            "active_before": None, "active_now": None, "active_during": None, "active_sent": False,
            "active_reason": None, "target": None, "tried": False, "proxies_foreign": False, "active_foreign": False}


def _proxies_left(sw: dict) -> bool:
    """Not when someone else changed the proxy display during the export: the command no longer owns it."""
    return sw["proxies_sent"] and not sw["proxies_foreign"] and sw["proxies_now"] != sw["proxies_before"]


def _active_left(sw: dict) -> bool:
    """Only an active sequence that existed before can be put back, and not one that someone else changed."""
    return (sw["active_sent"] and not sw["active_foreign"] and sw["active_before"] is not None
            and _id(sw["active_now"]) != _id(sw["active_before"]))


def _reason(err: CliError) -> str:
    if err.code == "recovery_required":
        return "a recovery is open, so nothing was written. After the recovery (see `cli-anything-premiere " \
               "recovery show`)"
    return f"putting it back failed ({err.code}: {err.message}). When that is fixed"


def _put_back(session: WriteSession, sw: dict) -> None:
    """Put back, in reverse order, each switch that was sent and does not show its old state. Each failure is kept
    in sw[*_reason]; a failure of one does not stop the other (a recovery marker stops every write by itself,
    since session.write checks it before each send). An interrupt stops the put-back."""
    sw["tried"] = True
    items = []
    if _active_left(sw):
        items.append("active")
    if _proxies_left(sw):
        items.append("proxies")
    stop: CliError | None = None
    for item in items:
        if stop is not None:
            sw[f"{item}_reason"] = _reason(stop)
            continue
        try:
            if item == "active":
                reply = _activate(session, sw["active_before"]["id"])
                sw["active_now"] = reply["after"]
                if _active_left(sw):
                    sw["active_reason"] = (f"putting it back did not take (Premiere shows {_shown(reply['after'])}"
                                           + (f": {reply['set_error']}" if reply["set_error"] else "") + ")")
            else:
                reply = _set_proxies(session, sw["proxies_before"] == 1)
                sw["proxies_now"] = reply["after"]
                if _proxies_left(sw):
                    sw["proxies_reason"] = (f"putting it back did not take (it reads {reply['after']}"
                                            + (f": {reply['set_error']}" if reply["set_error"] else "") + ")")
        except CliError as err:
            sw[f"{item}_reason"] = _reason(err)
        except RuntimeError as err:            # the write session closed (a retained lock, a failed guard)
            sw[f"{item}_reason"] = _reason(CliError("session_closed", str(err)))
        except (KeyboardInterrupt, bridge.Interrupted):
            stop = CliError("interrupted", STOPPED)
            sw[f"{item}_reason"] = _reason(stop)


def _restore(client: BridgeClient, guards: dict, project_path: str, sw: dict, warnings: list) -> None:
    """Session B: a new write session for the put-back. When it cannot start (a recovery marker, the lease,
    another project), nothing is written and the reason goes to every switch that is left."""
    if sw["tried"] or not (_active_left(sw) or _proxies_left(sw)):
        return
    try:
        with WriteSession(client, **guards) as session:
            warnings += [w for w in session.warnings if w not in warnings]
            if session.expect["project_path"] != project_path:
                raise CliError("project_mismatch", f"the active project changed from {project_path} to "
                                                   f"{session.expect['project_path']} during the export")
            _check_owner(session, sw, warnings)
            _put_back(session, sw)
    except CliError as err:
        _fail_all(sw, err)
    except (KeyboardInterrupt, bridge.Interrupted):
        _fail_all(sw, CliError("interrupted", STOPPED))


def _check_owner(session: WriteSession, sw: dict, warnings: list) -> None:
    """Session B reads the app once before it writes. An item is put back only when it still holds the value this
    command set (proxies off, the target active). An item someone else changed is left as it is, with a warning;
    it is not in left_switched. When the read fails, both items count as still ours."""
    try:
        info = _read_app(session, None)
    except CliError:
        return
    target = sw["target"]
    if _proxies_left(sw) and info["proxies"] is not None and info["proxies"] != 0:
        sw["proxies_foreign"], sw["proxies_now"] = True, info["proxies"]
        warnings.append(f"Proxy display reads {'on' if info['proxies'] == 1 else info['proxies']} now: it was "
                        "changed during the export by someone else, so this command did not put it back.")
    if _active_left(sw) and _id(info["active_sequence"]) != _id(target):
        sw["active_foreign"], sw["active_now"] = True, info["active_sequence"]
        warnings.append(f"The active sequence is {_shown(info['active_sequence'])} now: it was changed during the "
                        "export by someone else, so this command did not put it back.")


def _fail_all(sw: dict, err: CliError) -> None:
    sw["tried"] = True
    for item, left in (("active", _active_left(sw)), ("proxies", _proxies_left(sw))):
        if left and sw[f"{item}_reason"] is None:
            sw[f"{item}_reason"] = _reason(err)


def _command(args: list[str], guards: dict) -> str:
    parts = [PROGRAM, "live", "app", *args]
    if guards["project"] is not None:
        parts += ["--project", guards["project"]]
    if guards["any_project"]:
        parts.append("--any-project")
    if guards["allow_untested"]:
        parts.append("--allow-untested")
    return " ".join(shlex.quote(part) for part in parts)


def _left_switched(sw: dict, guards: dict) -> tuple[dict | None, list[str]]:
    """(left_switched, the warning lines). left_switched: {"proxies": False or None, "active_sequence": the
    sequence the command made active, or None}; None when nothing is left switched."""
    lines = []
    left = {"proxies": None, "active_sequence": None}
    if _active_left(sw):
        left["active_sequence"] = sw["target"]
        old = sw["active_before"]
        lines.append(f"Sequence {_shown(sw['target'])} may still be the active sequence: "
                     f"{sw['active_reason'] or 'it was not put back'}, make {_shown(old)} active again with: "
                     + _command(["activate", "--sequence", old["id"]], guards))
    if _proxies_left(sw):
        left["proxies"] = False
        lines.append(f"Proxy display may still be off: {sw['proxies_reason'] or 'it was not put back'}, "
                     "put it back with: " + _command(["proxies", "on"], guards))
    if left == {"proxies": None, "active_sequence": None}:
        return None, []
    return left, lines


# ---------- the exports ----------
def _same_path(reported: str, requested: Path) -> bool:
    """The path the tool reports names the file asked for. The reply holds dest.fsName, which on macOS can be in
    decomposed Unicode (NFD) or go through a resolved symlink (/private/tmp for /tmp). When the reported path
    exists, it must be the same file; otherwise the NFC forms of both real paths must be equal."""
    try:
        if os.path.exists(reported):
            return os.path.exists(requested) and os.path.samefile(reported, requested)
    except (OSError, ValueError):
        pass
    try:
        return (unicodedata.normalize("NFC", os.path.realpath(reported))
                == unicodedata.normalize("NFC", os.path.realpath(str(requested))))
    except (OSError, ValueError):
        return False


def _stat(path: Path):
    try:
        return os.stat(path)
    except OSError:
        return None


def _frame_fault(reply: dict, path: Path, before) -> tuple[str | None, int]:
    """(the fault, the size). The tool must not report an error, its outputPath must name the file asked for
    (_same_path), and the file must exist, be a file and hold more than 0 bytes. With --overwrite the old file must
    have been replaced (another inode or another time)."""
    if reply.get("is_error"):
        text = reply.get("text") or "the tool reported an error"
        if GUARD_REFUSED in text:
            return (f"the active sequence or the project changed during the export, so the server refused it "
                    f"({text}). A recovery is now open: run `cli-anything-premiere recovery show`.", 0)
        return f"the tool failed: {text}", 0
    structured = reply.get("structured")
    data = structured.get("data") if isinstance(structured, dict) else None
    reported = data.get("outputPath") if isinstance(data, dict) else None
    if not isinstance(reported, str) or not reported:
        return "the tool's reply names no outputPath, so it does not say which file it wrote", 0
    if not _same_path(reported, path):
        return f"the tool says it wrote {reported}, not {path}", 0
    st = _stat(path)
    if st is None:
        return f"there is no file at {path} after the export", 0
    if not stat.S_ISREG(st.st_mode):
        return f"{path} is not a file after the export", 0
    if st.st_size <= 0:
        return f"the file {path} is empty", 0
    if before is not None and (st.st_ino, st.st_mtime_ns) == (before.st_ino, before.st_mtime_ns):
        return f"the old file {path} was not replaced", 0
    return None, st.st_size


def _export_all(client: BridgeClient, make_session, times: list[float], paths: list[Path], sequence_id: str,
                project_path: str, timeout_s: float, frames: list) -> CliError | None:
    """One MCP session, one export_frame call per time. Called without the lease. Never raises: the first
    failure is returned (export_failed, or the session's own code: mcp_missing, mcp_timeout, mcp_died,
    mcp_error, lease_busy, interrupted)."""
    total = len(times)
    try:
        with make_session(client) as mcp:
            for number, (seconds, path) in enumerate(zip(times, paths), 1):
                where = f"Frame {number} of {total} ({seconds:.3f} s)"
                before = _stat(path)
                arguments = {"output_path": str(path), "time_seconds": seconds,
                             "expected_project_path": project_path,
                             "expected_cep_target": {"backend": "cep", "sequence_id": sequence_id}}
                try:
                    reply = mcp.call("export_frame", arguments, timeout_s=timeout_s)
                except CliError as err:
                    return CliError(err.code, f"{where}: {err.message}")
                fault, size = _frame_fault(reply, path, before)
                if fault is not None:
                    return CliError("export_failed", f"{where}: {fault}")
                frames.append({"time_seconds": seconds, "path": str(path), "bytes": size})
    except CliError as err:                     # the session did not start, or did not close
        return err
    except (KeyboardInterrupt, bridge.Interrupted):
        return CliError("interrupted", f"{STOPPED} The export that was running may have finished: check Premiere "
                                       "before another edit.")
    except Exception as exc:                    # a fault of this program: still put back what was switched
        return CliError("export_failed", f"Unexpected error during the export: {exc!r}")
    return None


# ---------- the whole command ----------
def export_frames(client: BridgeClient, make_session, *, sequence_id, sequence_name=None, times: list[float],
                  out_dir: Path, prefix="frame", fmt="png", overwrite=False, proxies="off", dry_run=False,
                  project=None, any_project=False, allow_untested=False, hard_limit_s=EXPORT_TIMEOUT_S) -> dict:
    """Export one still per time of one sequence. See the module docstring for the order of work.

    make_session(client) returns an McpSession (not started). hard_limit_s is the longest wait for one frame;
    the scripts of the write sessions wait at most DEFAULT_HARD_LIMIT_S (or hard_limit_s when that is lower).

    Returns {"sequence", "frames", "exported", "total", "proxies": {"before", "during", "after"},
    "active_sequence": {"before", "after"}, "left_switched", "stored": None, "rendered": None, "warnings"}; with
    dry_run {"dry_run": True, "sequence", "planned", "total", "out_dir": {"path", "exists"},
    "proxies": {"before", "switch"},
    "active_sequence": {"before", "switch"}, "warnings"}. A CliError raised after the checks of the arguments
    carries `payload` (the same keys as the return). When every frame was written but a switch could not be put
    back, the error is not_restored.
    """
    _check_options(sequence_id, prefix, fmt, proxies)
    times = _check_times(times)
    folder = _folder_path(out_dir)
    paths = [folder / frame_name(prefix, seconds, fmt) for seconds in times]
    _check_targets(paths, overwrite)
    folder_existed = folder.is_dir()
    if not dry_run:
        _make_folder(folder)
    guards = {"project": project, "any_project": any_project, "allow_untested": allow_untested,
              "hard_limit_s": min(hard_limit_s, DEFAULT_HARD_LIMIT_S)}
    sw = _new_switches()
    frames: list[dict] = []
    warnings: list[str] = []
    sequence = {"id": sequence_id, "name": sequence_name}
    project_path = None
    error: CliError | None = None

    def payload() -> dict:
        left, lines = _left_switched(sw, guards)
        return {"sequence": sequence, "frames": frames, "exported": len(frames), "total": len(times),
                "proxies": {"before": _flag(sw["proxies_before"]), "during": _flag(sw["proxies_during"]),
                            "after": _flag(sw["proxies_now"])},
                "active_sequence": {"before": _id(sw["active_before"]), "after": _id(sw["active_now"])},
                "left_switched": left, "stored": None, "rendered": None, "warnings": warnings + lines}

    # Session A: read, check, switch, confirm.
    try:
        with WriteSession(client, **guards) as session:
            warnings += session.warnings
            session.target(sequence_id, sequence_name)
            info = _read_app(session, sequence_id)
            sequence = {"id": info["sequence"]["id"], "name": info["sequence"]["name"]}
            sw["target"] = dict(sequence)
            sw["proxies_before"] = sw["proxies_now"] = sw["proxies_during"] = info["proxies"]
            sw["active_before"] = sw["active_now"] = sw["active_during"] = info["active_sequence"]
            end = info["sequence"]["end_ticks"]
            if end is None:
                raise CliError("bad_state", f"The end of sequence {_shown(sequence)} cannot be read, so the times "
                                            "cannot be checked. Nothing was switched.")
            for seconds in times:
                if round(seconds * TICKS_PER_SECOND) >= int(end):
                    raise CliError("bad_time", f"The time {seconds:g} s is at or after the end of sequence "
                                               f"{_shown(sequence)}, which ends at {int(end) / TICKS_PER_SECOND:g} s. "
                                               "Nothing was switched.")
            switch_proxies = proxies == "off" and info["proxies"] == 1
            switch_active = _id(info["active_sequence"]) != sequence_id
            if proxies == "off" and info["proxies"] is None:
                raise CliError("not_switched", "The proxy display cannot be read, so it cannot be switched off and "
                                               "put back. Use --proxies keep to export with the display as it is. "
                                               "Nothing was switched.")
            if dry_run:
                planned = [{"time_seconds": s, "path": str(p)} for s, p in zip(times, paths)]
                return {"dry_run": True, "sequence": sequence, "planned": planned, "total": len(times),
                        "out_dir": {"path": str(folder), "exists": folder_existed},
                        "proxies": {"before": _flag(info["proxies"]), "switch": switch_proxies},
                        "active_sequence": {"before": _id(info["active_sequence"]), "switch": switch_active},
                        "warnings": warnings + ([PNG_ALPHA] if fmt == "png" else [])}
            try:
                if switch_proxies:
                    sw["proxies_sent"], sw["proxies_now"] = True, None
                    reply = _set_proxies(session, False)
                    sw["proxies_now"] = reply["after"]
                    if reply["after"] != 0:
                        raise CliError("not_switched", f"The proxy display did not switch off: it reads "
                                                       f"{reply['after']} after the write"
                                       + (f" ({reply['set_error']})" if reply["set_error"] else "")
                                       + ". Nothing was exported.")
                if switch_active:
                    sw["active_sent"], sw["active_now"] = True, None
                    reply = _activate(session, sequence_id)
                    sw["active_now"] = reply["after"]
                    if _id(reply["after"]) != sequence_id:
                        raise CliError("not_switched", f"Sequence {_shown(sequence)} did not become active: Premiere "
                                                       f"shows {_shown(reply['after'])} after the write"
                                       + (f" ({reply['set_error']})" if reply["set_error"] else "")
                                       + ". Nothing was exported.")
                # The last state read before the export: nothing comes between it and the MCP session except
                # giving the lease back.
                project_path = session.expect["project_path"]
                state, more = _confirm_active(client, guards, project_path, sequence_id)
                warnings += [w for w in more if w not in warnings]
                shown = state.get("active_sequence")
                if switch_active:
                    sw["active_now"] = shown if isinstance(shown, dict) else None
                if _id(shown) != sequence_id:
                    raise CliError("not_switched", f"Premiere's state shows {_shown(shown)} as the active sequence, "
                                                   f"not {_shown(sequence)}. Nothing was exported.")
                sw["proxies_during"], sw["active_during"] = sw["proxies_now"], sw["active_now"]
            except CliError:
                _put_back(session, sw)
                raise
            except Exception as exc:            # a fault of this program: put back, then report it with the payload
                _put_back(session, sw)
                raise CliError("recipe_error", f"{type(exc).__name__}: {exc}") from exc
            except BaseException:               # interrupts keep their own path
                _put_back(session, sw)
                raise
    except CliError as err:
        error = err
    except (KeyboardInterrupt, bridge.Interrupted):
        error = CliError("interrupted", f"{STOPPED} Check Premiere before another edit.")

    # No lease: the exports.
    if error is None:
        error = _export_all(client, make_session, times, paths, sequence["id"], project_path, hard_limit_s, frames)

    # Session B: the put-back (when something is still switched and the put-back was not tried in session A).
    _restore(client, guards, project_path, sw, warnings)
    if fmt == "png" and frames:
        warnings.append(PNG_ALPHA)
    result = payload()
    if sw["active_sent"] and sw["active_before"] is None:
        if _id(sw["active_now"]) == sequence["id"]:
            result["warnings"].append(f"No sequence was active before the export, so {_shown(sequence)} stays active.")
        elif sw["active_now"] is None:
            result["warnings"].append(f"The active sequence may have been switched to {_shown(sequence)}: the outcome "
                                      "of that write is unknown. No sequence was active before, so there is nothing "
                                      "to put back.")
    if error is None and result["left_switched"] is not None:
        error = CliError("not_restored", f"Every frame was exported ({len(frames)} of {len(times)}), but not "
                                         "everything that was switched could be put back. The warnings say how to "
                                         "do it.")
    if error is not None:
        error.payload = result
        raise error
    return result


# ---------- live app ----------
def set_proxies(client: BridgeClient, enabled: bool, *, dry_run=False, project=None, any_project=False,
                allow_untested=False, hard_limit_s=DEFAULT_HARD_LIMIT_S) -> dict:
    """Switch the proxy display on or off and read it back. Returns {"proxies": {"before", "after"}, "stored",
    "rendered": None, "warnings"} (stored: the read-back shows the wanted state); with dry_run {"dry_run": True,
    "proxies": {"before"}, "warnings"}. A switch that did not take raises not_stored with that payload."""
    with WriteSession(client, project=project, any_project=any_project, allow_untested=allow_untested,
                      hard_limit_s=hard_limit_s) as session:
        warnings = list(session.warnings)
        if dry_run:
            info = _read_app(session, None)
            return {"dry_run": True, "proxies": {"before": _flag(info["proxies"])}, "warnings": warnings}
        reply = _set_proxies(session, enabled)
    stored = reply["after"] == (1 if enabled else 0)
    result = {"proxies": {"before": _flag(reply["before"]), "after": _flag(reply["after"])}, "stored": stored,
              "rendered": None, "warnings": warnings}
    if not stored:
        err = CliError("not_stored", f"The proxy display did not switch {'on' if enabled else 'off'}: it reads "
                                     f"{reply['after']} after the write"
                       + (f" ({reply['set_error']})." if reply["set_error"] else "."))
        err.payload = result
        raise err
    return result


def activate_sequence(client: BridgeClient, sequence_id: str, sequence_name: str | None = None, *, dry_run=False,
                      project=None, any_project=False, allow_untested=False,
                      hard_limit_s=DEFAULT_HARD_LIMIT_S) -> dict:
    """Make a sequence the active one. Confirmed twice: the script's read-back and a fresh state read. Returns
    {"sequence", "active_sequence": {"before", "after"}, "stored", "rendered": None, "warnings"}; with dry_run
    {"dry_run": True, "sequence", "active_sequence": {"before"}, "warnings"}."""
    if not isinstance(sequence_id, str) or not sequence_id:
        raise CliError("bad_args", "--sequence must name a sequence ID (see `live state`). Nothing was sent.")
    guards = {"project": project, "any_project": any_project, "allow_untested": allow_untested,
              "hard_limit_s": hard_limit_s}
    with WriteSession(client, **guards) as session:
        warnings = list(session.warnings)
        session.target(sequence_id, sequence_name)
        info = _read_app(session, sequence_id)
        sequence = {"id": info["sequence"]["id"], "name": info["sequence"]["name"]}
        if dry_run:
            return {"dry_run": True, "sequence": sequence, "active_sequence": {"before": info["active_sequence"]},
                    "warnings": warnings}
        reply = _activate(session, sequence_id)
        state, more = _confirm_active(client, guards, session.expect["project_path"], sequence_id)
        warnings += [w for w in more if w not in warnings]
    shown = state.get("active_sequence")
    after = _ref(shown, "active sequence") if isinstance(shown, dict) and isinstance(shown.get("id"), str) \
        and shown.get("id") else None
    stored = _id(reply["after"]) == sequence_id and _id(after) == sequence_id
    result = {"sequence": sequence, "active_sequence": {"before": reply["before"], "after": after},
              "stored": stored, "rendered": None, "warnings": warnings}
    if not stored:
        err = CliError("not_stored", f"Sequence {_shown(sequence)} did not become active: the script reads "
                                     f"{_shown(reply['after'])} and Premiere's state shows {_shown(after)}"
                       + (f" ({reply['set_error']})." if reply["set_error"] else "."))
        err.payload = result
        raise err
    return result
