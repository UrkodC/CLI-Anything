"""Test doubles for the Premiere bridge: a fake CEP panel thread and a private temp bridge folder."""
from __future__ import annotations

import contextlib
import json
import os
import signal
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

import pytest

DEFAULT_REPLY = '{"success":true,"data":{"echo":true}}'
DEFAULT_HEARTBEAT = {"protocolVersion": 1, "commandSchemaVersion": 1, "receiptVersion": 1, "state": "running"}
HEARTBEAT_FILE = "bridge-heartbeat.json"
POLL_S = 0.02
BUSY_EVERY_S = 0.2
HEARTBEAT_EVERY_S = 0.5


def _default_handler(script: str) -> str | None:
    return DEFAULT_REPLY


class FakePanel:
    """A thread that plays the CEP panel in a temp bridge folder."""

    def __init__(self, bridge_dir: Path, handler: Callable[[str], str | None] = _default_handler,
                 delay_s: float = 0.0, busy: bool = False,
                 heartbeat: dict | None = DEFAULT_HEARTBEAT) -> None:
        self.bridge_dir = Path(bridge_dir)
        self.handler = handler
        self.delay_s = delay_s
        self.busy = busy
        self.heartbeat = heartbeat
        self.seen: list[str] = []
        self._id = uuid.uuid4().hex[:8]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="FakePanel", daemon=True)
        self._next_heartbeat = 0.0

    def start(self) -> None:
        self._beat(force=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    # ---------- panel loop ----------
    def _run(self) -> None:
        while not self._stop.is_set():
            self._beat()
            for cmd in sorted(self.bridge_dir.glob("cmd_*.jsx")):
                self._serve(cmd)
            self._stop.wait(POLL_S)

    def _beat(self, force: bool = False) -> None:
        if self.heartbeat is None or (not force and time.monotonic() < self._next_heartbeat):
            return
        tmp = self.bridge_dir / f"{HEARTBEAT_FILE}.{self._id}.tmp"
        tmp.write_text(json.dumps(self.heartbeat), encoding="utf-8")
        os.replace(tmp, self.bridge_dir / HEARTBEAT_FILE)
        self._next_heartbeat = time.monotonic() + HEARTBEAT_EVERY_S

    def _serve(self, cmd: Path) -> None:
        cmd_id = cmd.name[len("cmd_"):-len(".jsx")]
        claimed = cmd.with_name(f"{cmd.name}.{self._id}.claimed")
        try:
            os.rename(cmd, claimed)
        except FileNotFoundError:
            return  # the CLI took it back first
        script = claimed.read_text(encoding="utf-8")
        claimed.unlink()
        self.seen.append(script)
        busy_file = self.bridge_dir / f"busy_{cmd_id}.json"
        reply = self.handler(script)
        if reply is None:
            while not self._stop.is_set():  # never answer
                self._tick(busy_file)
            return
        end = time.monotonic() + self.delay_s
        while time.monotonic() < end and not self._stop.is_set():
            self._tick(busy_file)
        staged = self.bridge_dir / f"res_{cmd_id}.json.staged"
        staged.write_text(reply, encoding="utf-8")
        os.rename(staged, self.bridge_dir / f"res_{cmd_id}.json")

    def _tick(self, busy_file: Path) -> None:
        """Wait one busy period, refreshing the busy and heartbeat files."""
        if self.busy:
            busy_file.write_text("{}", encoding="utf-8")
        self._beat()
        self._stop.wait(BUSY_EVERY_S)


@pytest.fixture
def bridge_dir(tmp_path) -> Path:
    path = tmp_path / "premiere-mcp-bridge"
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


# ---------- signals ----------
SAVED_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


DEFAULT_HANDLERS = {signal.SIGINT: signal.default_int_handler, signal.SIGTERM: signal.SIG_DFL,
                    signal.SIGHUP: signal.SIG_DFL}


@pytest.fixture
def keep_signals():
    """Start the test with Python's default handlers for SIGINT, SIGTERM and SIGHUP, and put back the
    handlers it found after the test."""
    from cli_anything.premiere.core import bridge
    old = {signum: signal.getsignal(signum) for signum in SAVED_SIGNALS}
    for signum, handler in DEFAULT_HANDLERS.items():
        signal.signal(signum, handler)
    yield
    left = (bridge._Held.depth, list(bridge._Held.pending))
    bridge._Held.depth, bridge._Held.pending[:] = 0, []
    assert left == (0, []), f"a lock section or a held signal leaked out of the test: {left}"
    for signum, handler in old.items():
        signal.signal(signum, handler)


@contextlib.contextmanager
def signal_when(signum: int, ready: Callable[[], bool] = lambda: True, timeout_s: float = 10.0):
    """Send `signum` to this process once `ready()` is true and a Python handler is installed for it.

    The body runs in the main thread meanwhile. On leaving, the sender thread is stopped; `fired`
    in the yielded dict tells whether the signal was sent."""
    state = {"fired": False}
    done = threading.Event()

    def send() -> None:
        end = time.monotonic() + timeout_s
        while not done.is_set() and time.monotonic() < end:
            handler = signal.getsignal(signum)
            if callable(handler) and ready():
                state["fired"] = True
                os.kill(os.getpid(), signum)
                return
            done.wait(0.01)

    sender = threading.Thread(target=send, name="signal_when", daemon=True)
    sender.start()
    try:
        yield state
    finally:
        done.set()
        sender.join(timeout=5)


def cmd_files(bridge_dir) -> list:
    return list(Path(bridge_dir).glob("cmd_*.jsx"))


def write_heartbeat(bridge_dir, heartbeat: dict = DEFAULT_HEARTBEAT) -> None:
    """A fresh heartbeat with no panel behind it: commands are sent but never picked up."""
    (Path(bridge_dir) / HEARTBEAT_FILE).write_text(json.dumps(heartbeat), encoding="utf-8")


# ---------- recovery markers that cannot be read ----------
RECOVERY_FILE = ".host-recovery.json"
MARKER_SHAPES = ["folder", "no_permission", "not_utf8"]


def unreadable_marker(bridge_dir, shape: str) -> None:
    """Put a recovery marker in place that cannot be read: a folder, a file with no read permission,
    or a file that is not UTF-8. Call restore_marker_mode afterwards, so tmp_path can be removed."""
    path = Path(bridge_dir) / RECOVERY_FILE
    if shape == "folder":
        path.mkdir(mode=0o700)
    elif shape == "no_permission":
        path.write_text('{"version":1,"reason":"unknown_command_outcome","nonce":"x"}', encoding="utf-8")
        path.chmod(0o000)
    elif shape == "not_utf8":
        path.write_bytes(b"\xff\xfe\x00not utf-8")
    else:
        raise ValueError(shape)


def restore_marker_mode(bridge_dir) -> None:
    path = Path(bridge_dir) / RECOVERY_FILE
    if path.exists():
        path.chmod(0o700)


# ---------- fake premiere-pro-mcp server ----------
# Python source of a stdio MCP server. It answers like the real one on the points the client relies on:
# one JSON object per line, tools/list in two pages, tools/call with content, structuredContent and isError.
# It also does what a real server does that the client must put up with: it writes a lot to stderr, logs
# non-JSON lines and notifications to stdout, and sends a reply with another id before each real reply.
# It refuses tools/list and tools/call until it got notifications/initialized.
# The bridge folder comes from PREMIERE_TEMP_DIR when that is set, else from argv[1].
FAKE_MCP_SERVER = r'''
import json
import os
import sys

BRIDGE = os.environ.get("PREMIERE_TEMP_DIR") or sys.argv[1]
LEASE_SEEN = os.path.exists(os.path.join(BRIDGE, ".host-owner-lease"))

for _ in range(1000):  # about 220 KB, more than a pipe holds: the client must read stderr
    sys.stderr.write("fake server log line " + "x" * 200 + "\n")
sys.stderr.flush()

SCHEMA = {"type": "object", "properties": {}, "additionalProperties": True}
TOOLS = [
    {"name": "ping", "description": "Check the connection to Premiere.", "inputSchema": SCHEMA},
    {"name": "echo", "description": "Return the arguments.", "inputSchema": SCHEMA},
    {"name": "fail", "description": "Always report a tool error.", "inputSchema": SCHEMA},
    {"name": "lease_seen_at_start", "description": "Say whether the bridge lease existed at start.", "inputSchema": SCHEMA},
    {"name": "env", "description": "Return PREMIERE_MCP_CAPABILITIES.", "inputSchema": SCHEMA},
]
PAGES = {"": (TOOLS[:3], "page2"), "page2": (TOOLS[3:], None)}


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def reply(request_id, result):
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def fail(request_id, code, message):
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def content(data, text=None):
    return {"content": [{"type": "text", "text": json.dumps(data) if text is None else text}],
            "structuredContent": data}


# export_frame, shaped like the real reply (measures.md section 1). The file name steers it:
# FAIL: a tool error, no file. EMPTY: an empty file and a success. GUARD: the server's target guard refusal.
# MARKER: writes the recovery marker into the bridge folder, then a tool error. NOFILE: a success that writes
# nothing. OTHERPATH: a success whose outputPath is another path. SLOW: sleeps 30 s first. With SECOND in the
# name as well (FAILSECOND), the word steers the second call and the later ones only.
# GUARD also writes the recovery marker first, as the real server does (measures.md section 5).
# Modes for the reported outputPath (the file is written at the given path): NFD: the path in decomposed
# Unicode. REAL: os.path.realpath of the path. NOPATH: no outputPath. OTHER: another existing file next to it.
# Every call is appended to fake_mcp_calls.jsonl next to this file, with lease_held: whether the bridge lease
# existed at the moment of the call. The tool "calls" returns that list.
import time
import unicodedata
CALLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_mcp_calls.jsonl")
GUARD_TEXT = "Expected target guard refused: active sequence changed; nothing was changed."


def recorded_calls():
    if not os.path.exists(CALLS):
        return []
    with open(CALLS, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def tool_error(request_id, text):
    reply(request_id, {"content": [{"type": "text", "text": text}], "isError": True})


def export_frame(request_id, arguments):
    path = str(arguments.get("output_path") or "")
    held = os.path.exists(os.path.join(BRIDGE, ".host-owner-lease"))
    with open(CALLS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"arguments": arguments, "lease_held": held}) + "\n")
    base = os.path.basename(path)
    if "SECOND" in base and len(recorded_calls()) < 2:
        base = ""                   # SECOND: the words below steer the second call and later ones only
    if "SLOW" in base:
        time.sleep(30)
    if "MARKER" in base:
        with open(os.path.join(BRIDGE, ".host-recovery.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"version": 1, "reason": "unknown_command_outcome", "nonce": "fake"}))
        tool_error(request_id, "Frame export produced no file on disk.")
        return
    if "GUARD" in base:
        with open(os.path.join(BRIDGE, ".host-recovery.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"version": 1, "reason": "unknown_command_outcome", "nonce": "fake-guard"}))
        tool_error(request_id, GUARD_TEXT)
        return
    if "FAIL" in base:
        tool_error(request_id, "fake export failure")
        return
    if "NOFILE" not in base:
        with open(path, "wb") as fh:
            fh.write(b"" if "EMPTY" in base else b"\x89PNG fake still " + base.encode("utf-8"))
    reported = path
    if "OTHERPATH" in base:
        reported = path + ".elsewhere"
    elif "OTHER" in base:
        reported = path + ".other.png"
        with open(reported, "wb") as fh:
            fh.write(b"another still")
    elif "NFD" in base:
        reported = unicodedata.normalize("NFD", path)
    elif "REAL" in base:
        reported = os.path.realpath(path)
    data = {"exported": True, "outputPath": reported, "method": "qe",
            "renderVerified": False, "verificationScope": "Output file exists only; fake.", "hasAlpha": True,
            "alphaNote": "PNG stills keep the sequence's transparency as straight alpha.", "lease_held": held}
    if "NOPATH" in base:
        del data["outputPath"]
    receipt = {"version": 1, "dispatch": "dispatched", "outcome": "committed_unverified",
               "verification": {"boundary": "Output file exists only; fake.", "renderVerified": False},
               "affectedTargets": [], "partialSteps": [], "outputEvidence": None, "nextSafeAction": "none"}
    reply(request_id, content({"ok": True, "tool": "export_frame", "data": data, "receipt": receipt}))


# ---------- the timeline tools (live place, live effect apply) ----------
# They load, change and save the TimelineModel file named by argv[2] (tests/fakes.py, TimelineModel). Each call is
# logged in fake_mcp_calls.jsonl as {"tool", "arguments", "lease_held"}. The server's target guard: a call without
# expected_project_path and expected_cep_target {"backend": "cep", "sequence_id"}, or one whose sequence is not the
# active one, or whose project is not the model's, writes the recovery marker and fails (as GUARD above).
# Steering words in item_id (set_item_in_out: MARKFAIL; overwrite_clip: FAIL, MARKER, UNVERIFIED) and in
# effect_name (apply_effect: FAIL, QEADD). Quirks played as measured (measures.md, RESULTS Task 5): set_item_in_out
# floors to the item frame and reports the applied seconds, and the marks stay on the item; overwrite_clip rounds
# the start to the sequence frame and uses the item's marks; set_clip_duration rounds to a whole sequence frame and
# moves a still's in point by 3600 s. New clips get the node IDs n0001, n0002, ...
import math
MODEL = sys.argv[2] if len(sys.argv) > 2 else None
TPS = 254016000000
STILL_IN_SHIFT = 3600 * TPS


def log_call(tool, arguments):
    held = os.path.exists(os.path.join(BRIDGE, ".host-owner-lease"))
    with open(CALLS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"tool": tool, "arguments": arguments, "lease_held": held}) + "\n")


def write_marker(nonce):
    with open(os.path.join(BRIDGE, ".host-recovery.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"version": 1, "reason": "unknown_command_outcome", "nonce": nonce}))


def model_load():
    with open(MODEL, encoding="utf-8") as fh:
        return json.load(fh)


def model_save(data):
    tmp = MODEL + ".server.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, MODEL)


def result(request_id, tool, data):
    reply(request_id, content({"ok": True, "tool": tool, "data": data}))


def guarded(request_id, data, arguments):
    target = arguments.get("expected_cep_target")
    active = data.get("active")
    if (arguments.get("expected_project_path") != data["project"]["path"] or not isinstance(target, dict)
            or target.get("backend") != "cep" or active is None or target.get("sequence_id") != active):
        write_marker("fake-guard")
        tool_error(request_id, GUARD_TEXT)
        return False
    return True


def find_item(data, wanted):
    for node_id, item in data["items"].items():          # __findProjectItem: node ID or name, first in walk order
        if node_id == wanted or item["name"] == wanted:
            return node_id, item
    return None


def track_clips(data, seq, kind, track):
    found = [c for c in data["clips"] if c["sequence"] == seq and c["kind"] == kind and c["track"] == track]
    return sorted(found, key=lambda c: c["start_ticks"])


def find_clip(data, node_id):
    for clip in data["clips"]:
        if clip["sequence"] == data["active"] and clip["node_id"] == node_id:
            return clip
    return None


def new_id(data):
    number = data["next_id"]
    data["next_id"] = number + 1
    return "n%04d" % number


def unlink(data, node_id):
    for clip in data["clips"]:
        if node_id in clip["linked"]:
            clip["linked"].remove(node_id)


def clear_range(data, seq, kind, track, start, end):
    """Overwrite: what lies in [start, end) on the track is cut away."""
    for clip in track_clips(data, seq, kind, track):
        if clip["end_ticks"] <= start or clip["start_ticks"] >= end:
            continue
        if clip["start_ticks"] >= start and clip["end_ticks"] <= end:
            data["clips"].remove(clip)
            unlink(data, clip["node_id"])
        elif clip["start_ticks"] < start and clip["end_ticks"] > end:
            right = dict(clip, node_id=new_id(data), start_ticks=end, linked=[], effects=list(clip["effects"]),
                         in_ticks=clip["in_ticks"] + (end - clip["start_ticks"]))
            data["clips"].append(right)
            clip["out_ticks"] -= clip["end_ticks"] - start
            clip["end_ticks"] = start
        elif clip["start_ticks"] < start:
            clip["out_ticks"] -= clip["end_ticks"] - start
            clip["end_ticks"] = start
        else:
            clip["in_ticks"] += end - clip["start_ticks"]
            clip["start_ticks"] = end
    if kind == "video":
        data["transitions"] = [t for t in data["transitions"]
                               if not (t["sequence"] == seq and t["track"] == track and t["start_ticks"] < end
                                       and t["end_ticks"] > start)]


def placed_on(data, seq, kind, track, node_id, start, frame):
    return any(c["item"] == node_id and abs(c["start_ticks"] - start) <= frame
               for c in track_clips(data, seq, kind, track))


def set_item_in_out(request_id, arguments):
    log_call("set_item_in_out", arguments)
    data = model_load()
    if not guarded(request_id, data, arguments):
        return
    wanted = str(arguments.get("item_id"))
    if "MARKFAIL" in wanted:
        tool_error(request_id, "Premiere rejected the requested project-item in point. Original marks were restored.")
        return
    found = find_item(data, wanted)
    if found is None:
        tool_error(request_id, "Item not found")
        return
    node_id, item = found
    step = round(TPS / item["fps"])
    out = {"item": item["name"], "inSet": "in_seconds" in arguments, "outSet": "out_seconds" in arguments,
           "verified": True, "mediaType": 4}
    for key, mark, word in (("in_seconds", "in_ticks", "In"), ("out_seconds", "out_ticks", "Out")):
        if key in arguments:
            ticks = min(int(math.floor(arguments[key] * item["fps"] + 1e-9)) * step, item["length_ticks"])
            item[mark] = ticks
            out["requested" + word + "Seconds"] = arguments[key]
            out["applied" + word + "Seconds"] = ticks / TPS
    model_save(data)
    result(request_id, "set_item_in_out", out)


def overwrite_clip(request_id, arguments):
    log_call("overwrite_clip", arguments)
    data = model_load()
    if not guarded(request_id, data, arguments):
        return
    seq_id = data["active"]
    seq = data["sequences"][seq_id]
    frame = int(seq["frame_ticks"])
    track, audio = arguments.get("track_index", 0), arguments.get("audio_track_index", 0)
    if track >= seq["video_tracks"]:
        tool_error(request_id, "Video track index %d is out of range." % track)
        return
    if audio >= seq["audio_tracks"]:
        tool_error(request_id, "Audio track index %d is out of range." % audio)
        return
    wanted = str(arguments.get("item_id"))
    found = find_item(data, wanted)
    if found is None:
        tool_error(request_id, "Project item not found: " + wanted)
        return
    node_id, item = found
    if "MARKER" in wanted:
        write_marker("fake-place")
        tool_error(request_id, "overwrite_clip failed after dispatch; the outcome is unknown.")
        return
    if "FAIL" in wanted:
        tool_error(request_id, "fake overwrite failure: nothing was changed")
        return
    start = int(round(arguments.get("start_seconds", 0) * TPS / frame)) * frame
    span = item["out_ticks"] - item["in_ticks"]
    length = max(int(round(span / frame)), 1) * frame
    video, sound = item["has_video"], item["has_audio"]
    was_video = placed_on(data, seq_id, "video", track, node_id, start, frame)
    was_audio = placed_on(data, seq_id, "audio", audio, node_id, start, frame)
    if (not video or was_video) and (not sound or was_audio):
        tool_error(request_id, "overwrite_clip produced no verifiable new placement: project item %s was already "
                               "present at %ss on the requested track(s)." % (item["name"], arguments.get("start_seconds")))
        return
    made = []
    for kind, number, wanted_stream in (("video", track, video), ("audio", audio, sound and "UNVERIFIED" not in wanted)):
        if not wanted_stream:
            continue
        clear_range(data, seq_id, kind, number, start, start + length)
        clip = {"node_id": new_id(data), "sequence": seq_id, "kind": kind, "track": number, "item": node_id,
                "start_ticks": start, "end_ticks": start + length, "in_ticks": item["in_ticks"],
                "out_ticks": item["in_ticks"] + length, "linked": [],
                "effects": ["Opacity", "Motion"] if kind == "video" else ["Volume"]}
        data["clips"].append(clip)
        made.append(clip)
    for clip in made:
        clip["linked"] = [c["node_id"] for c in made if c is not clip]
    model_save(data)
    if "UNVERIFIED" in wanted and sound:
        tool_error(request_id, "overwrite_clip did not place the clip's audio on the requested video track %d / audio "
                               "track %d. Inspect the timeline before retrying. outcome committed_unverified"
                               % (track, audio))
        return
    result(request_id, "overwrite_clip", {
        "overwritten": True, "verified": True, "item": item["name"], "trackIndex": track, "audioTrackIndex": audio,
        "audioTrackAutoSelected": False, "startSeconds": arguments.get("start_seconds", 0),
        "placedOnVideoTrack": bool(video), "placedOnAudioTrack": bool(sound)})


def remove_from_timeline(request_id, arguments):
    log_call("remove_from_timeline", arguments)
    data = model_load()
    if not guarded(request_id, data, arguments):
        return
    node_id = str(arguments.get("node_id"))
    clip = find_clip(data, node_id)
    if clip is None:
        tool_error(request_id, "Clip not found: " + node_id)
        return
    if arguments.get("ripple") is True:
        tool_error(request_id, "the fake server does not play a ripple removal")
        return
    removed = [node_id]
    data["clips"].remove(clip)
    if arguments.get("include_linked") is not False:
        for other in list(data["clips"]):
            if other["node_id"] in clip["linked"]:
                data["clips"].remove(other)
                removed.append(other["node_id"])
    unlink(data, node_id)
    model_save(data)
    # Shaped like the recorded reply (measures/t5_m8_remove.json).
    result(request_id, "remove_from_timeline", {"removed": True, "clipName": data["items"].get(clip["item"], {}).get("name"),
                                                "removedClipIds": removed, "linkedPartnersRemoved": len(removed) - 1,
                                                "verified": True})


def set_clip_duration(request_id, arguments):
    log_call("set_clip_duration", arguments)
    data = model_load()
    if not guarded(request_id, data, arguments):
        return
    node_id = str(arguments.get("node_id"))
    clip = find_clip(data, node_id)
    if clip is None:
        tool_error(request_id, "Clip not found: " + node_id)
        return
    frame = int(data["sequences"][clip["sequence"]]["frame_ticks"])
    end = clip["start_ticks"] + int(round(arguments["duration_seconds"] * TPS / frame)) * frame
    if end - clip["start_ticks"] < frame:
        tool_error(request_id, "The requested end must be at least one frame after the clip start.")
        return
    for other in track_clips(data, clip["sequence"], clip["kind"], clip["track"]):
        if other is not clip and clip["start_ticks"] < other["start_ticks"] < end:
            tool_error(request_id, "Refusing to extend: the requested end would overlap the next clip. No change "
                                   "was attempted.")
            return
    delta = end - clip["end_ticks"]
    still = bool(data["items"].get(clip["item"], {}).get("still"))
    targets = [clip] + ([c for c in data["clips"] if c["node_id"] in clip["linked"]]
                        if arguments.get("include_linked") is not False else [])
    for target in targets:
        target["end_ticks"] += delta
        target["out_ticks"] += delta
        if still and target is clip:
            target["in_ticks"] += STILL_IN_SHIFT
            target["out_ticks"] += STILL_IN_SHIFT
    model_save(data)
    # Shaped like the recorded reply (measures/t5_m9_dur.json).
    result(request_id, "set_clip_duration", {
        "outcome": "verified", "verified": True, "changed": True,
        "clipName": data["items"].get(clip["item"], {}).get("name"), "trackType": clip["kind"],
        "trackIndex": clip["track"], "timelineStart": clip["start_ticks"] / TPS, "timelineEnd": clip["end_ticks"] / TPS,
        "durationSeconds": (clip["end_ticks"] - clip["start_ticks"]) / TPS,
        "previousEnd": (clip["end_ticks"] - delta) / TPS, "inPoint": clip["in_ticks"] / TPS,
        "outPoint": clip["out_ticks"] / TPS, "isStillImage": still, "requestedSeconds": arguments["duration_seconds"],
        "appliedSeconds": (clip["end_ticks"] - clip["start_ticks"]) / TPS,
        "linkedPartnersEdited": [t["node_id"] for t in targets if t is not clip]})


def apply_effect(request_id, arguments):
    log_call("apply_effect", arguments)
    data = model_load()
    if not guarded(request_id, data, arguments):
        return
    node_id = str(arguments.get("node_id"))
    clip = find_clip(data, node_id)
    if clip is None:
        tool_error(request_id, "Clip not found: " + node_id)
        return
    name = str(arguments.get("effect_name"))
    if "FAIL" in name:
        tool_error(request_id, "Premiere added no component for " + name + "; nothing was changed.")
        return
    before = len(clip["effects"])
    clip["effects"].append(name)
    model_save(data)
    if "QEADD" in name:
        tool_error(request_id, "Do not retry blindly: QE may have added the effect even when it is missing from "
                               "the DOM component list. Inspect the clip before retrying.")
        return
    result(request_id, "apply_effect", {
        "effect": name, "clipName": data["items"].get(clip["item"], {}).get("name"),
        "componentCountBefore": before, "componentCountAfter": before + 1,
        "addedComponents": [{"displayName": name, "matchName": name}], "applied": True, "verified": True,
        "outcome": "verified"})


TIMELINE_TOOLS = {"set_item_in_out": set_item_in_out, "overwrite_clip": overwrite_clip,
                  "remove_from_timeline": remove_from_timeline, "set_clip_duration": set_clip_duration,
                  "apply_effect": apply_effect}


initialized = False
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    method, rid = message.get("method"), message.get("id")
    if rid is not None:
        sys.stdout.write("not json: a log line on stdout\n")
        send({"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": "hello"}})
        send({"jsonrpc": "2.0", "id": 987654, "result": {}})
    if method == "initialize":
        reply(rid, {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-premiere-mcp", "version": "9.9.9"}})
    elif method == "notifications/initialized":
        initialized = True
    elif rid is None:
        continue
    elif not initialized:
        fail(rid, -32002, "Server not initialized")
    elif method == "tools/list":
        cursor = (message.get("params") or {}).get("cursor") or ""
        tools, next_cursor = PAGES[cursor]
        page = {"tools": tools}
        if next_cursor:
            page["nextCursor"] = next_cursor
        reply(rid, page)
    elif method == "tools/call":
        params = message.get("params") or {}
        name, arguments = params.get("name"), params.get("arguments") or {}
        if name == "ping":
            reply(rid, content({"connected": True}))
        elif name == "echo":
            reply(rid, content(arguments))
        elif name == "fail":
            reply(rid, {"content": [{"type": "text", "text": "fake failure"}], "isError": True})
        elif name == "lease_seen_at_start":
            reply(rid, content({"seen": LEASE_SEEN}))
        elif name == "env":
            value = os.environ.get("PREMIERE_MCP_CAPABILITIES")
            reply(rid, content({"value": value}, text=value or ""))
        elif name == "export_frame":    # not in TOOLS, so the tool list stays as the mcp tests pin it
            export_frame(rid, arguments)
        elif name == "calls":
            reply(rid, content({"calls": recorded_calls()}))
        elif name in TIMELINE_TOOLS and MODEL is not None:     # not in TOOLS either
            TIMELINE_TOOLS[name](rid, arguments)
        else:
            fail(rid, -32602, "Unknown tool: " + str(name))
    else:
        fail(rid, -32601, "Method not found: " + str(method))
'''


def fake_mcp_command(tmp_path, bridge_dir, timeline=None) -> list[str]:
    """Write the fake server to a file in tmp_path and return the command that starts it. timeline: the file of a
    TimelineModel, for the timeline tools (set_item_in_out, overwrite_clip, remove_from_timeline,
    set_clip_duration, apply_effect)."""
    path = Path(tmp_path) / "fake_mcp_server.py"
    path.write_text(FAKE_MCP_SERVER, encoding="utf-8")
    return [sys.executable, str(path), str(bridge_dir)] + ([] if timeline is None else [str(timeline)])


# ---------- a child that takes and gives back the lease while signals hit it ----------
# Python source of a helper process for the signal test. It loops Lease.acquire and Lease.release on a
# temp bridge folder with the CLI's signal handlers installed. After every interrupt it blocks the three
# signals (the child has one thread, so pthread_sigmask holds them all), checks the lock folder, gives a
# held lease back, writes "ack" and unblocks. The parent sends the next signal only after an ack, so one
# signal is in flight at a time. At the end it prints one JSON line: interrupts, cycles, problems.
# argv: bridge folder, stop file, time limit in seconds.
SIGNAL_CHILD = r'''
import json
import os
import signal
import sys
import time
from pathlib import Path

from cli_anything.premiere.core import bridge
from cli_anything.premiere.errors import CliError

SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
STOPS = (KeyboardInterrupt, bridge.Interrupted)


def check(lease, lock: Path) -> str | None:
    """The lock folder after an interrupt: None when it is consistent with the object. Gives a held lease back."""
    if not os.path.lexists(lock):
        return "the object says held but there is no lock folder" if lease.held else None
    try:
        pid, _token = bridge.read_owner(lock / bridge.OWNER_FILE)
    except FileNotFoundError:
        return "a lock folder without an owner record"
    except CliError as err:
        return "a lock folder with an invalid owner record: " + err.message
    if pid != os.getpid():
        return "a lock folder of another process"
    if not lease.held:
        return "a lock left by an object that says not held"
    lease.release()
    return None


def clear(lease, lock: Path) -> None:
    """After a problem: remove the broken lock by hand and forget the lease, so the loop can go on."""
    lease._token = None
    try:
        os.unlink(lock / bridge.OWNER_FILE)
    except FileNotFoundError:
        pass
    try:
        os.rmdir(lock)
    except FileNotFoundError:
        pass


def main() -> None:
    bridge_dir, stop_file, limit_s = Path(sys.argv[1]), Path(sys.argv[2]), float(sys.argv[3])
    bridge.install_signal_handlers()
    lease = bridge.Lease(bridge_dir, wait_s=5.0)
    lock = bridge_dir / bridge.LOCK_DIR
    interrupts = cycles = 0
    problems: list[str] = []
    need_check = False
    deadline = time.monotonic() + limit_s
    print("ready", flush=True)
    while True:
        try:
            while True:     # the whole loop sits in the try, back-edge included
                if need_check:
                    signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
                    problem = check(lease, lock)
                    if problem:
                        problems.append(problem)
                        clear(lease, lock)
                    need_check = False
                    sys.stdout.write("ack\n")
                    sys.stdout.flush()
                    signal.pthread_sigmask(signal.SIG_UNBLOCK, SIGNALS)
                if cycles % 32 == 0 and (stop_file.exists() or time.monotonic() > deadline):
                    break
                lease.acquire()
                lease.release()
                cycles += 1
            break
        except STOPS:
            interrupts += 1
            need_check = True
    signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    if lease.held:
        lease.release()
    print(json.dumps({"interrupts": interrupts, "cycles": cycles, "problems": problems}), flush=True)


if __name__ == "__main__":
    main()
'''


# ---------- panels for the recipe scripts ----------
RECIPE_STATE_MARK = "playhead_ticks: playhead"    # occurs in state.jsx only
RECIPE_STATE = {"version": "27.0.0", "build": "88",
                "projects": [{"name": "X Claude.prproj", "path": "/p/X Claude.prproj", "active": True}],
                "active_sequence": {"id": "s1", "name": "Main"}, "playhead_ticks": "0", "selection": []}


def script_args(script: str) -> dict:
    """The ARGS of a built script: the JSON object on its `var ARGS = ...;` line."""
    import re
    found = re.search(r"^var ARGS = (.*);$", script, re.MULTILINE)
    assert found, "this script has no ARGS line"
    return json.loads(found.group(1))


def recipe_handler(replies: dict, *, state: dict | None = None) -> Callable[[str], str]:
    """A FakePanel handler for recipe scripts. Use it as `FakePanel(bridge_dir, handler=recipe_handler({...}))`.

    `replies` maps a marker to the answer for the script that holds that marker (each recipe script has a
    first-line comment such as `// recipe: clip_list`; the marker is `"recipe: clip_list"`). The answer is
      - a data object (anything JSON): sent as {"success": true, "data": <it>};
      - a function that takes the full script text and returns the data object (or an Exception);
      - an Exception: sent as a failure, {"success": false, "error": str(it)}. The text of a refusal from a
        script is "Error: precondition: <text>", which the CLI reports with the code "precondition".
    The state script (the one that holds RECIPE_STATE_MARK) is answered with `state`, by default a valid
    Premiere 27 state with one project named "X Claude.prproj". Any other script, or a function that raises,
    gets a failure reply that says so, so the panel thread never dies. script_args(script) reads the ARGS.
    """
    shown = RECIPE_STATE if state is None else state

    def reply(data: object) -> str:
        if isinstance(data, Exception):
            return json.dumps({"success": False, "error": str(data)})
        return json.dumps({"success": True, "data": data})

    def handler(script: str) -> str:
        if RECIPE_STATE_MARK in script:
            return reply(shown)
        for marker, answer in replies.items():
            if marker in script:
                if not callable(answer):
                    return reply(answer)
                try:
                    return reply(answer(script))
                except Exception as exc:     # a panel thread that raises never answers
                    return reply(Exception(f"recipe_handler: the reply function for {marker!r} raised {exc!r}"))
        return reply(Exception(f"recipe_handler has no reply for this script: {script[:80]!r}"))
    return handler


# ---------- an effect model behind the fake panel (live effect set) ----------
class EffectModel:
    """A small Premiere for `effect_resolve.jsx` and `effect_write.jsx`, played in Python by the fake panel.

    It reads the ARGS of the script text it gets and answers as the real scripts do: the same reply shapes
    and the same "precondition:" texts for the faults the tests use. Values stay between calls.

        model = EffectModel()                       # sequence s1 "Main", V1[0] 000f4243 with ProcAmp at index 4
        model.fail(-2.6, times=2)                   # setValue(-2.6) throws "Illegal Parameter type" twice
        model.readback[3.0] = 2.5                   # a stored 3.0 reads back as 2.5
        panel = FakePanel(bridge_dir, handler=recipe_handler(model.replies()))

    clips maps (track, clip) to {"node_id", "name", "start_ticks", "effects": [{"name", "match_name",
    "properties": [{"name", "value", "kind", "keyframed"}]}]}. `writes` lists every setValue in order, as
    (node_id, effect_index, property index, value). `resolves` counts the resolve scripts, and `moves` maps a
    resolve number (1 is the first) to a function run before that resolve answers.
    """

    def __init__(self) -> None:
        self.sequence = {"id": "s1", "name": "Main"}
        procamp = {"name": "ProcAmp", "match_name": "AE.ADBE ProcAmp", "properties": [
            {"name": "Brightness", "value": 7.7, "kind": "number", "keyframed": False},
            {"name": "Contrast", "value": 100.0, "kind": "number", "keyframed": False},
            {"name": "Hue", "value": 0.0, "kind": "number", "keyframed": False},
            {"name": "Saturation", "value": 100.0, "kind": "number", "keyframed": False},
            {"name": "Split Screen", "value": False, "kind": "boolean", "keyframed": False}]}
        self.clips: dict[tuple[int, int], dict] = {
            (0, 0): {"node_id": "000f4243", "name": "testclip.mp4", "start_ticks": "0",
                     "effects": [{"name": "Opacity", "match_name": "AE.ADBE Opacity", "properties": [
                                     {"name": "Opacity", "value": 100.0, "kind": "number", "keyframed": False}]},
                                 {"name": "Motion", "match_name": "AE.ADBE Motion", "properties": [
                                     {"name": "Position", "value": [0.5, 0.5], "kind": "array", "keyframed": False}]},
                                 {"name": "Lumetri Color", "match_name": "AE.ADBE Lumetri", "properties": []},
                                 {"name": "ASC CDL", "match_name": "AE.ADBE ASC CDL", "properties": []},
                                 procamp]},
            (0, 1): {"node_id": "000f4250", "name": "second.mp4", "start_ticks": "1525620096000",
                     "effects": [{"name": "ProcAmp", "match_name": "AE.ADBE ProcAmp", "properties": [
                         {"name": "Brightness", "value": 5.0, "kind": "number", "keyframed": False}]}]},
        }
        lumetri = self.clips[(0, 0)]["effects"][2]["properties"]
        for index in range(43):
            lumetri.append({"name": " ", "value": 0.0, "kind": "number", "keyframed": False})
        lumetri[4] = {"name": " ", "value": "", "kind": "string", "keyframed": False}
        lumetri[6] = {"name": "Input LUT", "value": 0.0, "kind": "number", "keyframed": False}
        lumetri[7] = {"name": "Input LUT", "value": 0.0, "kind": "number", "keyframed": False}
        lumetri[13] = {"name": "White Balance", "value": [255, 192, 192, 192], "kind": "color", "keyframed": False}
        lumetri[14] = {"name": "Temperature", "value": 10.0, "kind": "number", "keyframed": False}
        lumetri[32] = {"name": " ", "value": "﻾", "kind": "string", "keyframed": False}
        lumetri[34] = {"name": "Look", "value": 0.0, "kind": "number", "keyframed": False}
        self.audio: dict[tuple[int, int], dict] = {}   # audio clips, found by node ID only (and refused)
        self.readback: dict = {}            # value written -> value read back
        self.failures: dict = {}            # value -> [times left, error text]
        self.writes: list[tuple] = []
        self.resolves = 0
        self.moves: dict[int, Callable[[], None]] = {}

    def fail(self, value, *, times: int, error: str = "Illegal Parameter type") -> None:
        self.failures[value] = [times, error]

    def replies(self) -> dict:
        return {"recipe: effect_resolve": self.resolve, "recipe: effect_write": self.write}

    def prop(self, track: int, clip: int, effect_index: int, index: int) -> dict:
        return self.clips[(track, clip)]["effects"][effect_index]["properties"][index]

    # ---------- the finders, with the texts of find.jsx ----------
    def _expect(self, args: dict) -> None:
        expect = args.get("expect") or {}
        if expect.get("sequence_id") != self.sequence["id"]:
            raise _Refused(f"no sequence has the ID {expect.get('sequence_id')}")
        name = expect.get("sequence_name")
        if name is not None and name != self.sequence["name"]:
            raise _Refused(f"sequence {self.sequence['id']} is named {self.sequence['name']}, not {name}")

    def _clip(self, target: dict) -> tuple[tuple[int, int], dict]:
        if target.get("node_id") is not None:
            for place, clip in sorted(self.clips.items()):
                if clip["node_id"] == target["node_id"]:
                    return place, clip
            if any(clip["node_id"] == target["node_id"] for clip in self.audio.values()):
                raise _Refused("this clip is on an audio track; effect set writes video clips only")
            raise _Refused(f"no clip has the node ID {target['node_id']} in sequence {self.sequence['name']}")
        track, number = target["track"], target["clip"]
        if not any(place[0] == track for place in self.clips):
            raise _Refused(f"the sequence has no video track {track}")
        if (track, number) not in self.clips:
            raise _Refused(f"video track {track} has no clip {number}")
        return (track, number), self.clips[(track, number)]

    @staticmethod
    def _effect(clip: dict, name: str, effect_index) -> int:
        found = [i for i, e in enumerate(clip["effects"]) if name in (e["name"], e["match_name"])]
        if effect_index is not None:
            if effect_index >= len(clip["effects"]):
                raise _Refused(f"this clip has no effect at index {effect_index}")
            if effect_index not in found:
                raise _Refused(f"effect {effect_index} of this clip is {clip['effects'][effect_index]['name']}, not {name}")
            return effect_index
        if not found:
            raise _Refused(f"no effect named {name} on this clip")
        if len(found) > 1:
            raise _Refused(f"ambiguous_effect: {len(found)} effects are named {name}; pass effect_index")
        return found[0]

    @staticmethod
    def _property(effect: dict, index: int, name: str) -> dict:
        props = effect["properties"]
        if index >= len(props):
            raise _Refused(f"{effect['name']} has no property {index}")
        if props[index]["name"] != name:
            raise _Refused(f"property {index} of {effect['name']} is named {props[index]['name']}, not {name}")
        return props[index]

    # ---------- the two scripts ----------
    def resolve(self, script: str) -> object:
        self.resolves += 1
        if self.resolves in self.moves:
            self.moves[self.resolves]()
        args = script_args(script)
        try:
            self._expect(args)
        except _Refused as exc:
            return exc.error()
        records = []
        for number, entry in enumerate(args["entries"]):
            try:
                (track, clip_number), clip = self._clip(entry["target"])
                effect_index = self._effect(clip, entry["effect"], entry.get("effect_index"))
                effect = clip["effects"][effect_index]
                prop = self._property(effect, entry["index"], entry["name"])
            except _Refused as exc:
                return exc.error(f"entry {number}: ")
            records.append({"clip": {"node_id": clip["node_id"], "name": clip["name"], "track_type": "video", "track": track,
                                     "clip": clip_number, "start_ticks": clip["start_ticks"]},
                            "effect": {"effect_index": effect_index, "name": effect["name"],
                                       "match_name": effect["match_name"]},
                            "property": {"index": entry["index"], "name": prop["name"], "value": prop["value"],
                                         "kind": prop["kind"], "keyframed": prop["keyframed"]}})
        return {"sequence": dict(self.sequence), "entries": records}

    def _set(self, node_id: str, effect_index: int, index: int, prop: dict, value) -> str | None:
        """setValue(value, true): the error text when it throws, else None."""
        self.writes.append((node_id, effect_index, index, value))
        failing = self.failures.get(value)
        if failing and failing[0] > 0:
            failing[0] -= 1
            return failing[1]
        prop["value"] = self.readback.get(value, value)
        return None

    def write(self, script: str) -> object:
        args = script_args(script)
        try:
            self._expect(args)
            (track, clip_number), clip = self._clip(args["target"])
            effect_index = self._effect(clip, args["effect"], args.get("effect_index"))
            prop = self._property(clip["effects"][effect_index], args["index"], args["name"])
            if clip["node_id"] != args["node_id"]:
                raise _Refused("the clip at this place changed")
            if effect_index != args["found_effect_index"]:
                raise _Refused("the effect at this place changed")
            if prop["keyframed"] is not False:
                raise _Refused("property is keyframed")
            # effect_write.jsx: the value before the first setValue against expect_old (first attempt only)
            from cli_anything.premiere.core.recipes.effects import _shown, same_value
            if "expect_old" in args and not same_value(args["expect_old"], prop["value"]):
                raise _Refused(f"the property holds {_shown(prop['value'])} now, not {_shown(args['expect_old'])} "
                               "as the plan expects")
        except _Refused as exc:
            return exc.error()
        before, wanted = prop["value"], args["value"]
        zero_first = False
        number = isinstance(wanted, (int, float)) and not isinstance(wanted, bool)
        if number and isinstance(before, (int, float)) and not isinstance(before, bool) and before > 0 and wanted < 0:
            zero_first = True
            error = self._set(clip["node_id"], effect_index, args["index"], prop, 0)
            back = prop["value"]
            if error is not None or abs(back) > args["tolerance"]:
                return {"before": before, "zero_first": True, "value": back, "read_error": None,
                        "set_error": f"zero step failed: {error if error is not None else f'read back {back}'}"}
        error = self._set(clip["node_id"], effect_index, args["index"], prop, wanted)
        return {"before": before, "zero_first": zero_first, "set_error": error, "value": prop["value"],
                "read_error": None}


class _Refused(Exception):
    """A finder's refusal inside EffectModel."""

    def error(self, prefix: str = "") -> Exception:
        return Exception(f"Error: precondition: {prefix}{self}")


# ---------- a Lumetri Color as Premiere 27 shows it (live lut set) ----------
LUT_TPS = 254016000000
EMPTY_LUT_SLOT = "﻾"       # what Lumetri index 4 reads with no LUT (measures.md section 4)


class LutModel(EffectModel):
    """EffectModel with the Lumetri Color layout of Premiere 27 on V1[0] (effect index 2): index 2 "Basic
    Correction" (boolean), index 4 " " holding the one character U+FEFE, index 5 the same, index 6 and 7 "Input
    LUT" (0). The clips also have an end: V1[0] runs 0 to 6.006 s, V1[1] 6.006 to 12.012 s. The resolve reply
    carries end_ticks as effect_resolve.jsx does (left out when a clip's "end_ticks" is None).

        model = LutModel()
        model.lumetri()[4]["value"]            # the LUT slot
    """

    def __init__(self) -> None:
        super().__init__()
        props = self.lumetri()
        props[2] = {"name": "Basic Correction", "value": False, "kind": "boolean", "keyframed": False}
        props[4] = {"name": " ", "value": EMPTY_LUT_SLOT, "kind": "string", "keyframed": False}
        props[5] = {"name": " ", "value": EMPTY_LUT_SLOT, "kind": "string", "keyframed": False}
        self.clips[(0, 0)]["end_ticks"] = str(round(6.006 * LUT_TPS))
        self.clips[(0, 1)]["end_ticks"] = str(round(12.012 * LUT_TPS))

    def lumetri(self, track: int = 0, clip: int = 0, effect_index: int = 2) -> list:
        return self.clips[(track, clip)]["effects"][effect_index]["properties"]

    def resolve(self, script: str) -> object:
        reply = super().resolve(script)
        if isinstance(reply, dict):
            by_node = {clip["node_id"]: clip for clip in self.clips.values()}
            for record in reply["entries"]:
                end = by_node[record["clip"]["node_id"]].get("end_ticks")
                if end is not None:
                    record["clip"]["end_ticks"] = end
        return reply


# ---------- keys: the Motion keyframes of clips (live keys get, live keys set) ----------
KEYS_TPS = 254016000000
KEYS_FRAME_TICKS = 10594584000        # one frame at 23.976 fps (seq.timebase)
KEYS_CLIP_TICKS = 79 * KEYS_FRAME_TICKS
NATIVE_FPS = 23.976023976024          # getFootageInterpretation().frameRate of a native 23.976 item (measures M6)
INTERPRETED_FPS = 23.9760000013456    # the same read on the item interpreted to 23.976 (S01, measures M6)


def float32(value: float) -> float:
    """value as Premiere stores a key or a static value: a 32-bit float (measures M4: -2.6 reads -2.59999990463257)."""
    import struct
    return struct.unpack("<f", struct.pack("<f", value))[0]


class KeysModel:
    """A small Premiere for `keys_get.jsx`, `keys_check.jsx`, `keys_write.jsx`, and for `effect_resolve.jsx` and
    `effect_write.jsx` on the Motion Scale of its clips, played in Python by the fake panel.

    It reads the ARGS of the script text it gets and answers as the real scripts do: the same reply shapes and
    the same "precondition:" texts for the faults the tests use. Values stay between calls. Sequence s1 "Main"
    is the one keys_get looks in; `sequences` maps every sequence ID to its name (add one to put clips in it).

        model = KeysModel()
        model.add_clip("000f4276", name="S02 C2246", rotation=[(0, 1.26), (316757952000, 1.01)])   # keys
        model.add_clip("000f4277", name="S03 C2248", rotation=0.67, scale=104.4)                    # static values
        panel = FakePanel(bridge_dir, handler=recipe_handler(model.replies()))

    clips maps a node ID to {"name", "sequence_id" (None: the sequence in `sequence`), "track_type", "track", "clip",
    "in_ticks", "out_ticks", "start_ticks", "end_ticks" (ints, sequence ticks for start and end, media ticks for in
    and out), "speed", "reversed", "item_fps", "effects"}. effects is the component list of Premiere: Opacity (Opacity, Blend Mode
    twice) and Motion (Position, Scale, Scale Width, Uniform Scale, Rotation, Anchor Point). A property is
    {"name", "value", "keys"}: keys is None for a property with no keys, else a list of (media ticks, value).
    add_clip takes `rotation` and `scale` as a number (static) or a list of (ticks, value) (keys).

    The writes follow the measures of Plan 3, Task 3: setTimeVarying(false) clears every key (M3b),
    setTimeVarying(true) adds none (M1), a second addKey at the same time keeps one key (M2), and values are
    stored as 32-bit floats (M4).

    Records: `calls` lists the ARGS of every keys_get script, `checks` counts the keys_check scripts, `writes`
    lists every write in order as ("scale", node ID, value) or ("rotation", node ID, mode).
    Hooks: `on_check` maps a keys_check number (1 is the first) to a function run before it answers;
    `before_write` maps a node ID to a function run (once) before keys_write for that clip answers;
    `add_key_throws` maps a node ID to the set of tick strings whose addKey throws; `drift` maps a node ID to a
    number of ticks that its first key is stored off by; a node ID in `unreadable` gets a keys_write reply whose read
    back failed (after null), with its writes done; `scale_lands` maps a node ID to the value its next Scale write
    stores instead of the one asked for (once).
    """

    def __init__(self) -> None:
        self.sequence = {"id": "s1", "name": "Main"}
        self.sequences: dict[str, str] = {"s1": "Main"}
        self.frame_ticks = KEYS_FRAME_TICKS
        self.clips: dict[str, dict] = {}
        self.calls: list[dict] = []
        self.checks = 0
        self.writes: list[tuple] = []
        self.on_check: dict[int, Callable[[], None]] = {}
        self.before_write: dict[str, Callable[[], None]] = {}
        self.add_key_throws: dict[str, set] = {}
        self.drift: dict[str, int] = {}
        self.unreadable: set = set()
        self.scale_lands: dict[str, float] = {}

    @staticmethod
    def _prop(name: str, value) -> dict:
        if isinstance(value, list) and value and all(isinstance(item, tuple) for item in value):
            return {"name": name, "value": None, "keys": [tuple(item) for item in value]}
        return {"name": name, "value": value, "keys": None}

    def add_clip(self, node_id: str, *, name: str, track: int = 0, track_type: str = "video", in_ticks: int = 0,
                 out_ticks: int | None = None, start_ticks: int = 0, end_ticks: int | None = None,
                 length_ticks: int = KEYS_CLIP_TICKS, speed: float = 1, reversed_: bool = False,
                 rotation=0.0, scale=100.0, sequence_id: str | None = None, item_fps: float = NATIVE_FPS) -> dict:
        number = sum(1 for clip in self.clips.values()
                     if (clip["sequence_id"], clip["track_type"], clip["track"]) == (sequence_id, track_type, track))
        self.clips[node_id] = {
            "name": name, "sequence_id": sequence_id, "track_type": track_type, "track": track, "clip": number,
            "in_ticks": in_ticks, "out_ticks": in_ticks + length_ticks if out_ticks is None else out_ticks,
            "start_ticks": start_ticks, "end_ticks": start_ticks + length_ticks if end_ticks is None else end_ticks,
            "speed": speed, "reversed": reversed_, "item_fps": item_fps,
            "effects": [
                {"name": "Opacity", "match_name": "AE.ADBE Opacity", "properties": [
                    self._prop("Opacity", 100.0), self._prop("Blend Mode", 0), self._prop("Blend Mode", 0)]},
                {"name": "Motion", "match_name": "AE.ADBE Motion", "properties": [
                    self._prop("Position", [0.5, 0.5]), self._prop("Scale", scale), self._prop("Scale Width", 100.0),
                    self._prop("Uniform Scale", True), self._prop("Rotation", rotation),
                    self._prop("Anchor Point", [0.5, 0.5])]}]}
        return self.clips[node_id]

    def replies(self) -> dict:
        return {"recipe: keys_get": self.keys_get, "recipe: keys_check": self.keys_check,
                "recipe: keys_write": self.keys_write, "recipe: effect_resolve": self.effect_resolve,
                "recipe: effect_write": self.effect_write}

    def prop(self, node_id: str, effect: str, name: str, nth: int = 0) -> dict:
        """The nth property of that name in that effect of the clip (the model's own record)."""
        component = next(e for e in self.clips[node_id]["effects"] if e["name"] == effect)
        return [p for p in component["properties"] if p["name"] == name][nth]

    def rotation(self, node_id: str) -> dict:
        """The Motion Rotation of the clip (index 4)."""
        return next(e for e in self.clips[node_id]["effects"] if e["name"] == "Motion")["properties"][4]

    def scale(self, node_id: str) -> dict:
        """The Motion Scale of the clip (index 1)."""
        return next(e for e in self.clips[node_id]["effects"] if e["name"] == "Motion")["properties"][1]

    # ---------- the finders, with the texts of find.jsx and keys.jsx ----------
    def _expect(self, args: dict) -> tuple[str, str]:
        """__expect of find.jsx: the sequence the session expects. Returns (ID, name)."""
        expect = args.get("expect") or {}
        wanted = expect.get("sequence_id")
        if wanted is None:
            raise _Refused("the script got no sequence ID")
        if wanted not in self.sequences:
            raise _Refused(f"no sequence has the ID {wanted}")
        name = expect.get("sequence_name")
        if name is not None and name != self.sequences[wanted]:
            raise _Refused(f"sequence {wanted} is named {self.sequences[wanted]}, not {name}")
        return wanted, self.sequences[wanted]

    def _find_clip(self, target: dict, sequence_id: str | None = None) -> tuple[str, dict]:
        sequence_id = self.sequence["id"] if sequence_id is None else sequence_id
        name = self.sequences.get(sequence_id, self.sequence["name"])
        here = {node: clip for node, clip in self.clips.items()
                if (clip["sequence_id"] or self.sequence["id"]) == sequence_id}
        if target.get("node_id") is not None:
            if target["node_id"] in here:
                return target["node_id"], here[target["node_id"]]
            raise _Refused(f"no clip has the node ID {target['node_id']} in sequence {name}")
        kind, track, number = target.get("track_type") or "video", target["track"], target["clip"]
        if not any((c["track_type"], c["track"]) == (kind, track) for c in here.values()):
            raise _Refused(f"the sequence has no {kind} track {track}")
        for node_id, clip in here.items():
            if (clip["track_type"], clip["track"], clip["clip"]) == (kind, track, number):
                return node_id, clip
        raise _Refused(f"{kind} track {track} has no clip {number}")

    @staticmethod
    def _find_effect(clip: dict, name: str) -> int:
        found = [i for i, e in enumerate(clip["effects"]) if name in (e["name"], e["match_name"])]
        if not found:
            raise _Refused(f"no effect named {name} on this clip")
        if len(found) > 1:
            raise _Refused(f"ambiguous_effect: {len(found)} effects are named {name}; pass effect_index")
        return found[0]

    @staticmethod
    def _uniform(effect: dict) -> bool:
        """__isUniformScale of keys.jsx: the effect has a Uniform Scale that holds true or 1."""
        for prop in effect["properties"]:
            if prop["name"] in ("Uniform Scale", "Escala uniforme"):
                return prop["value"] is True or prop["value"] == 1
        return False

    @classmethod
    def _name_matches(cls, actual: str, wanted: str, effect: dict) -> bool:
        """__propertyNameMatches of keys.jsx: the exact name, or Scale and Scale Height while Uniform Scale is on."""
        if actual == wanted:
            return True
        aliased = (wanted, actual) in (("Scale", "Scale Height"), ("Scale Height", "Scale"))
        return aliased and cls._uniform(effect)

    @staticmethod
    def _available(effect: dict) -> str:
        """__availablePropertyNames of keys.jsx."""
        names = [(i, p["name"]) for i, p in enumerate(effect["properties"]) if p["name"].strip()]
        counts: dict[str, int] = {}
        for _, name in names:
            counts[name] = counts.get(name, 0) + 1
        shown = [name + (f" (property_index {i})" if counts[name] > 1 else "") for i, name in names[:25]]
        summary = ", ".join(shown) if shown else "none with a non-empty display name"
        return summary + (f" (first 25 of {len(names)})" if len(names) > 25 else "")

    @classmethod
    def _find_property(cls, effect: dict, name: str, index) -> int:
        props = effect["properties"]
        if index is not None:
            if index >= len(props):
                raise _Refused(f"{effect['name']} has no property {index}")
            if props[index]["name"] != name:
                raise _Refused(f"property {index} of {effect['name']} is named {props[index]['name']}, not {name}")
            return index
        found = [i for i, p in enumerate(props) if cls._name_matches(p["name"], name, effect)]
        if not found:
            raise _Refused(f"no property named {name} in {effect['name']}. Available properties: "
                           f"{cls._available(effect)}.")
        if len(found) > 1:
            raise _Refused(f"ambiguous_property: {len(found)} properties of {effect['name']} are named {name}; "
                           "pass property_index")
        return found[0]

    @staticmethod
    def _read(prop: dict) -> dict:
        """__readKeys of keys.jsx."""
        if prop["keys"] is None:
            return {"is_time_varying": False, "keys": [], "value": prop["value"]}
        return {"is_time_varying": True, "keys": [{"ticks": str(t), "value": v} for t, v in sorted(prop["keys"])],
                "value": None}

    @staticmethod
    def _facts(node_id: str, clip: dict) -> dict:
        return {"in_ticks": str(clip["in_ticks"]), "out_ticks": str(clip["out_ticks"]),
                "start_ticks": str(clip["start_ticks"]), "end_ticks": str(clip["end_ticks"]),
                "speed": clip["speed"], "reversed": clip["reversed"], "node_id": node_id,
                "name": clip["name"], "track_type": clip["track_type"], "track": clip["track"],
                "clip": clip["clip"]}

    # ---------- keys_get ----------
    def keys_get(self, script: str) -> object:
        args = script_args(script)
        self.calls.append(args)
        try:
            if args.get("sequence_id") is None:
                raise _Refused("the script got no sequence ID")
            if args["sequence_id"] != self.sequence["id"]:
                raise _Refused(f"no sequence has the ID {args['sequence_id']}")
            node_id, clip = self._find_clip(args["target"])
            effect_index = self._find_effect(clip, args["effect"])
            effect = clip["effects"][effect_index]
            index = self._find_property(effect, args["property"], args.get("property_index"))
        except _Refused as exc:
            return exc.error()
        prop = effect["properties"][index]
        return {"sequence": dict(self.sequence), "clip": self._facts(node_id, clip),
                "effect": {"name": effect["name"], "effect_index": effect_index},
                "property": {"name": prop["name"], "index": index},
                "read": self._read(prop)}

    # ---------- keys_check ----------
    def _motion(self, clip: dict) -> tuple[dict | None, str | None]:
        try:
            effect_index = self._find_effect(clip, "Motion")
        except _Refused as exc:
            return None, str(exc)
        effect = clip["effects"][effect_index]
        props = effect["properties"]
        scale = rotation = None
        if len(props) > 1:
            prop = props[1]
            scale = {"index": 1, "name": prop["name"], "matches": self._name_matches(prop["name"], "Scale", effect),
                     "value": prop["value"], "keyframed": prop["keys"] is not None}
        if len(props) > 4:
            prop = props[4]
            read = self._read(prop)
            rotation = {"index": 4, "name": prop["name"], "keyframed": prop["keys"] is not None,
                        "keys": read["keys"], "value": read["value"]}
        return {"effect_index": effect_index, "uniform": self._uniform(effect), "scale": scale,
                "rotation": rotation}, None

    def keys_check(self, script: str) -> object:
        args = script_args(script)
        self.checks += 1
        hook = self.on_check.get(self.checks)
        if hook is not None:
            hook()
        try:
            sequence_id, _name = self._expect(args)
        except _Refused as exc:
            return exc.error()
        entries = []
        for item in args["entries"]:
            node_id = item["node_id"]
            try:
                _, clip = self._find_clip({"node_id": node_id}, sequence_id)
            except _Refused as exc:
                entries.append({"found": False, "node_id": node_id, "error": str(exc), "clip": None,
                                "interpreted": None, "item_fps": None, "motion": None, "motion_error": None})
                continue
            motion, motion_error = self._motion(clip)
            fps = clip["item_fps"]
            interpreted = None if fps is None else abs(fps - KEYS_TPS / self.frame_ticks) > 0.000001
            entries.append({"found": True, "node_id": node_id, "error": None, "clip": self._facts(node_id, clip),
                            "interpreted": interpreted, "item_fps": fps, "motion": motion,
                            "motion_error": motion_error})
        return {"frame_ticks": str(self.frame_ticks), "entries": entries}

    # ---------- keys_write ----------
    def keys_write(self, script: str) -> object:
        args = script_args(script)
        hook = self.before_write.pop(args.get("node_id"), None)
        if hook is not None:
            hook()
        try:
            sequence_id, _name = self._expect(args)
            node_id, clip = self._find_clip({"node_id": args["node_id"]}, sequence_id)
            if clip["track_type"] != "video":
                raise _Refused("this clip is on an audio track; Motion Rotation is a video property")
            expect = args["expect_clip"]
            if str(clip["in_ticks"]) != expect["in_ticks"]:
                raise _Refused(f"the in point of the clip is {clip['in_ticks']} ticks, not {expect['in_ticks']} "
                               "as the plan expects")
            if str(clip["out_ticks"]) != expect["out_ticks"]:
                raise _Refused(f"the out point of the clip is {clip['out_ticks']} ticks, not {expect['out_ticks']} "
                               "as the plan expects")
            if abs(clip["speed"] - 1) >= 0.0001:
                raise _Refused(f"the clip has a speed of {clip['speed']}; keys are written at speed 1 only")
            if clip["reversed"]:
                raise _Refused("the clip is reversed; keys are written on a clip that is not reversed only")
            effect = clip["effects"][self._find_effect(clip, "Motion")]
            if len(effect["properties"]) <= 4:
                raise _Refused("Motion has no property 4")
            if effect["properties"][4]["name"] != "Rotation":
                raise _Refused(f"property 4 of Motion is named {effect['properties'][4]['name']}, not Rotation")
        except _Refused as exc:
            return exc.error()
        prop = effect["properties"][4]
        before = {"is_time_varying": prop["keys"] is not None, "count": len(prop["keys"] or [])}
        errors: list[str] = []
        self.writes.append(("rotation", node_id, args["mode"]))
        if prop["keys"] is not None:
            prop["keys"] = None                               # setTimeVarying(false) clears every key (M3b)
        if args["mode"] == "keys":
            prop["keys"] = []                                 # setTimeVarying(true) adds none (M1)
            for number, key in enumerate(args["keys"]):
                if key["ticks"] in self.add_key_throws.get(node_id, set()):
                    errors.append(f"key {number} at {key['ticks']} ticks: addKey: Error: addKey failed")
                    continue
                ticks = int(key["ticks"]) + (self.drift.get(node_id, 0) if number == 0 else 0)
                prop["keys"] = [(t, v) for t, v in prop["keys"] if t != ticks]     # one key per time (M2)
                prop["keys"].append((ticks, float32(key["value"])))
            prop["keys"].sort()
        else:
            prop["value"] = float32(args["value"])
        if node_id in self.unreadable:
            errors.append("read back: Error: precondition: the key list could not be read")
            return {"before": before, "set_errors": errors, "after": None}
        return {"before": before, "set_errors": errors, "after": self._read(prop)}

    # ---------- effect_resolve and effect_write, for Motion Scale ----------
    def _effect_target(self, args: dict, target: dict, effect_name: str, index: int, name: str):
        sequence_id, _ = self._expect(args)
        node_id, clip = self._find_clip(target, sequence_id)
        effect_index = self._find_effect(clip, effect_name)
        effect = clip["effects"][effect_index]
        props = effect["properties"]
        if index >= len(props):
            raise _Refused(f"{effect['name']} has no property {index}")
        if props[index]["name"] != name:
            raise _Refused(f"property {index} of {effect['name']} is named {props[index]['name']}, not {name}")
        return node_id, clip, effect_index, effect, props[index]

    def effect_resolve(self, script: str) -> object:
        args = script_args(script)
        records = []
        for number, entry in enumerate(args["entries"]):
            try:
                node_id, clip, effect_index, effect, prop = self._effect_target(
                    args, entry["target"], entry["effect"], entry["index"], entry["name"])
            except _Refused as exc:
                return exc.error(f"entry {number}: ")
            records.append({"clip": {"node_id": node_id, "name": clip["name"], "track_type": clip["track_type"],
                                     "track": clip["track"], "clip": clip["clip"],
                                     "start_ticks": str(clip["start_ticks"]), "end_ticks": str(clip["end_ticks"])},
                            "effect": {"effect_index": effect_index, "name": effect["name"],
                                       "match_name": effect["match_name"]},
                            "property": {"index": entry["index"], "name": prop["name"], "value": prop["value"],
                                         "kind": "number", "keyframed": prop["keys"] is not None}})
        sequence_id = (args.get("expect") or {}).get("sequence_id")
        return {"sequence": {"id": sequence_id, "name": self.sequences.get(sequence_id)}, "entries": records}

    def effect_write(self, script: str) -> object:
        args = script_args(script)
        try:
            node_id, clip, effect_index, effect, prop = self._effect_target(
                args, args["target"], args["effect"], args["index"], args["name"])
            if node_id != args["node_id"]:
                raise _Refused("the clip at this place changed")
            if effect_index != args["found_effect_index"]:
                raise _Refused("the effect at this place changed")
            if prop["keys"] is not None:
                raise _Refused("property is keyframed")
        except _Refused as exc:
            return exc.error()
        before = prop["value"]
        self.writes.append(("scale", node_id, args["value"]))
        prop["value"] = float32(self.scale_lands.pop(node_id, args["value"]))
        return {"before": before, "zero_first": False, "set_error": None, "value": prop["value"], "read_error": None}


# ---------- a timeline behind the fake panel and the fake MCP server (live place, live effect apply) ----------
TIMELINE_TPS = 254016000000
TIMELINE_FRAME = 10594584000            # one frame at 23.976 fps (seq.timebase)
TIMELINE_PROJECT = "/p/X Claude.prproj"
STILL_EXTENSIONS = ("png", "jpg", "jpeg", "tif", "tiff", "psd", "gif", "bmp", "exr")
STILL_FPS = 29.97                       # what a still item reads as its frame rate (measure M9)
STILL_SECONDS = 4.96                    # the import length of a still (measure M9)


class TimelineModel:
    """A small Premiere timeline in a JSON file, shared by the fake panel (reads: state.jsx, app_switch.jsx,
    place_read.jsx, clip_list.jsx) and the fake MCP server (the timeline tools of FAKE_MCP_SERVER, which load,
    change and save the same file; give the file to fake_mcp_command).

        model = TimelineModel(tmp_path / "timeline.json")          # s1 "Main" (3 video, 6 audio tracks), o1 active
        model.add_item("000f4241", name="C0001.MP4")                 # picture and sound, 23.976 fps
        model.add_item("still1", name="card.png", still=True)        # a still: picture only, 4.96 s
        model.add_clip("000f4241", node_id="x1", track=0, start_f=0, frames=48)
        panel = FakePanel(bridge_dir, handler=model.handler())

    The file holds {"project": {name, path}, "active": sequence ID or None, "proxies", "sequences": {id: {name,
    frame_ticks, video_tracks, audio_tracks}}, "items": {node ID: {name, has_video, has_audio, still, fps,
    length_ticks, in_ticks, out_ticks (the item marks), path, is_sequence}}, "clips": [{node_id, sequence, kind
    ("video" or "audio"), track, item, start_ticks, end_ticks, in_ticks, out_ticks, linked, effects}],
    "transitions": [{sequence, track, start_ticks, end_ticks}], "next_id"}. Ticks are integers.

    Records: `writes` lists the app_switch writes as ("activate", id); `place_reads` counts the place_read scripts;
    `before_place_read` maps a place_read number (1 is the first) to a function run before it answers.
    """

    def __init__(self, path, *, active: str | None = "o1", video_tracks: int = 3, audio_tracks: int = 6,
                 frame_ticks: int = TIMELINE_FRAME) -> None:
        self.path = Path(path)
        self.writes: list[tuple] = []
        self.place_reads = 0
        self.before_place_read: dict[int, Callable[[], None]] = {}
        self.save({"project": {"name": "X Claude.prproj", "path": TIMELINE_PROJECT}, "active": active, "proxies": 1,
                   "sequences": {"s1": {"name": "Main", "frame_ticks": frame_ticks, "video_tracks": video_tracks,
                                        "audio_tracks": audio_tracks},
                                 "o1": {"name": "Old", "frame_ticks": frame_ticks, "video_tracks": 1,
                                        "audio_tracks": 1}},
                   "items": {}, "clips": [], "transitions": [], "next_id": 1})

    # ---------- the file ----------
    def load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict) -> None:
        tmp = self.path.with_name(self.path.name + ".panel.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.path)

    def change(self, fn: Callable[[dict], None]) -> None:
        data = self.load()
        fn(data)
        self.save(data)

    # ---------- set up ----------
    def add_item(self, node_id: str, *, name: str | None = None, video: bool = True, audio: bool = True,
                 still: bool = False, fps: float | None = None, length_seconds: float | None = None,
                 path: str | None = None) -> None:
        name = name or (f"{node_id}.png" if still else f"{node_id}.MP4")
        fps = fps or (STILL_FPS if still else 24000 / 1001)
        length = round((length_seconds or (STILL_SECONDS if still else 600.0)) * TIMELINE_TPS)

        def add(data):
            data["items"][node_id] = {"name": name, "has_video": video, "has_audio": audio and not still,
                                      "still": still, "fps": fps, "length_ticks": length, "in_ticks": 0,
                                      "out_ticks": length, "path": path or f"/media/{name}", "is_sequence": False}
        self.change(add)

    def add_clip(self, item: str, *, node_id: str, kind: str = "video", track: int = 0, start_f: int = 0,
                 frames: int = 48, in_f: int = 0, sequence: str = "s1", link: str | None = None,
                 effects: list | None = None) -> None:
        def add(data):
            frame = int(data["sequences"][sequence]["frame_ticks"])
            start, length = start_f * frame, frames * frame
            fps = data["items"][item]["fps"] if item in data["items"] else 24000 / 1001
            in_ticks = in_f * round(TIMELINE_TPS / fps)
            data["clips"].append({"node_id": node_id, "sequence": sequence, "kind": kind, "track": track,
                                  "item": item, "start_ticks": start, "end_ticks": start + length,
                                  "in_ticks": in_ticks, "out_ticks": in_ticks + length,
                                  "linked": [link] if link else [],
                                  "effects": list(effects) if effects is not None else
                                  (["Opacity", "Motion"] if kind == "video" else ["Volume"])})
            if link:
                for other in data["clips"]:
                    if other["node_id"] == link and node_id not in other["linked"]:
                        other["linked"].append(node_id)
        self.change(add)

    def add_transition(self, *, track: int = 0, start_f: int, end_f: int, sequence: str = "s1") -> None:
        def add(data):
            frame = int(data["sequences"][sequence]["frame_ticks"])
            data["transitions"].append({"sequence": sequence, "track": track, "start_ticks": start_f * frame,
                                        "end_ticks": end_f * frame})
        self.change(add)

    def activate(self, sequence_id: str | None) -> None:
        self.change(lambda data: data.__setitem__("active", sequence_id))

    def clips(self, kind: str | None = None, track: int | None = None, sequence: str = "s1") -> list[dict]:
        found = [c for c in self.load()["clips"] if c["sequence"] == sequence and (kind is None or c["kind"] == kind)
                 and (track is None or c["track"] == track)]
        return sorted(found, key=lambda c: (c["kind"], c["track"], c["start_ticks"]))

    def item(self, node_id: str) -> dict:
        return self.load()["items"][node_id]

    # ---------- the panel ----------
    def handler(self) -> Callable[[str], str]:
        inner = recipe_handler({"recipe: app_switch": self.app_switch, "recipe: place_read": self.place_read,
                                "recipe: clip_list": self.clip_list})

        def handle(script: str) -> str:
            if RECIPE_STATE_MARK in script:
                return json.dumps({"success": True, "data": self.state()})
            return inner(script)
        return handle

    @staticmethod
    def _ref(data: dict, seq_id):
        if seq_id is None or seq_id not in data["sequences"]:
            return None
        return {"id": seq_id, "name": data["sequences"][seq_id]["name"]}

    def state(self) -> dict:
        data = self.load()
        return {**RECIPE_STATE, "projects": [{**data["project"], "active": True}],
                "active_sequence": self._ref(data, data["active"])}

    @staticmethod
    def _expect(args: dict, data: dict):
        """__expect of find.jsx. Returns the expected sequence ID, or None."""
        expect = args.get("expect") or {}
        if expect.get("project_path") != data["project"]["path"]:
            raise _Refused(f"the active project is {data['project']['path']}, not {expect.get('project_path')}")
        seq_id = expect.get("sequence_id")
        if seq_id is None:
            return None
        if seq_id not in data["sequences"]:
            raise _Refused(f"no sequence has the ID {seq_id}")
        name = expect.get("sequence_name")
        if name is not None and name != data["sequences"][seq_id]["name"]:
            raise _Refused(f"sequence {seq_id} is named {data['sequences'][seq_id]['name']}, not {name}")
        return seq_id

    def app_switch(self, script: str):
        args = script_args(script)
        data = self.load()
        try:
            expected = self._expect(args, data)
        except _Refused as exc:
            return exc.error()
        action = args.get("action")
        if action == "read":
            seq = None
            if expected is not None:
                seq = {**self._ref(data, expected), "end_ticks": str(600 * TIMELINE_TPS)}
            return {"proxies": data["proxies"], "active_sequence": self._ref(data, data["active"]), "sequence": seq}
        if action == "activate":
            wanted = args.get("sequence_id")
            if wanted not in data["sequences"]:
                return Exception(f"Error: precondition: no sequence has the ID {wanted}")
            before = self._ref(data, data["active"])
            self.writes.append(("activate", wanted))
            data["active"] = wanted
            self.save(data)
            return {"before": before, "after": self._ref(data, wanted), "set_error": None}
        if action == "proxies":
            self.writes.append(("proxies", 1 if args.get("enabled") else 0))
            before = data["proxies"]
            data["proxies"] = 1 if args.get("enabled") else 0
            self.save(data)
            return {"before": before, "after": data["proxies"], "set_error": None}
        return Exception(f"Error: precondition: the action must be read, proxies or activate, not {action}")

    @staticmethod
    def _clip_record(clip: dict) -> dict:
        return {"node_id": clip["node_id"], "item": clip["item"], "start_ticks": str(clip["start_ticks"]),
                "end_ticks": str(clip["end_ticks"]), "in_ticks": str(clip["in_ticks"]),
                "out_ticks": str(clip["out_ticks"])}

    def place_read(self, script: str):
        """place_read.jsx, played in Python with the same reply."""
        self.place_reads += 1
        hook = self.before_place_read.get(self.place_reads)
        if hook is not None:
            hook()
        args = script_args(script)
        data = self.load()
        try:
            seq_id = self._expect(args, data)
            if seq_id is None:
                raise _Refused("the script got no sequence ID")
        except _Refused as exc:
            return exc.error()
        seq = data["sequences"][seq_id]
        frame = int(seq["frame_ticks"])
        room = frame / 2
        items = []
        for wanted in args["items"]:
            wanted = str(wanted)
            by_name = [node for node, item in data["items"].items() if item["name"] == wanted]
            node = wanted if wanted in data["items"] else (by_name[0] if len(by_name) == 1 else None)
            record = {"query": wanted, "found": node is not None, "node_id": node, "name": None,
                      "matches": len(by_name), "has_video": None, "has_audio": None, "still": None,
                      "is_sequence": None, "frame_rate": None}
            if node is not None:
                item = data["items"][node]
                extension = item["path"].rsplit(".", 1)[-1].lower() if "." in item["path"] else ""
                record.update(name=item["name"], has_video=item["has_video"], has_audio=item["has_audio"],
                              still=extension in STILL_EXTENSIONS, is_sequence=item["is_sequence"],
                              frame_rate=item["fps"])
            items.append(record)

        def on(kind: str, track: int, start: float, end: float):
            limit = seq["video_tracks"] if kind == "video" else seq["audio_tracks"]
            if track >= limit:
                return None
            return [self._clip_record(c) for c in data["clips"]
                    if c["sequence"] == seq_id and c["kind"] == kind and c["track"] == track
                    and c["start_ticks"] < end - room and c["end_ticks"] > start + room]
        spans = []
        for span in args["spans"]:
            start, end = float(span["start_ticks"]), float(span["end_ticks"])
            spans.append({"entry": span["entry"], "video": on("video", span["track"], start, end),
                          "audio": on("audio", span["audio_track"], start, end)})
        first = None
        v1 = sorted((c for c in data["clips"] if c["sequence"] == seq_id and c["kind"] == "video" and c["track"] == 0),
                    key=lambda c: c["start_ticks"])
        if v1:
            head = any(t["sequence"] == seq_id and t["track"] == 0
                       and t["start_ticks"] - room <= v1[0]["start_ticks"] <= t["end_ticks"] + room
                       for t in data["transitions"])
            first = {"node_id": v1[0]["node_id"], "start_ticks": str(v1[0]["start_ticks"]), "head_transition": head}
        return {"sequence": {"id": seq_id, "name": seq["name"], "frame_ticks": str(frame),
                             "video_tracks": seq["video_tracks"], "audio_tracks": seq["audio_tracks"]},
                "items": items, "spans": spans, "first_v1": first}

    def clip_list(self, script: str):
        """clip_list.jsx, played in Python (the fields a test of the timeline needs)."""
        args = script_args(script)
        data = self.load()
        seq_id = args.get("sequence_id") or data["active"]
        if seq_id not in data["sequences"]:
            return Exception(f"Error: precondition: no sequence has the ID {seq_id}")
        kinds = ("video", "audio") if args.get("track_type", "both") == "both" else (args["track_type"],)
        found = []
        for kind in kinds:
            limit = data["sequences"][seq_id]["video_tracks" if kind == "video" else "audio_tracks"]
            for track in range(limit):
                if args.get("track") is not None and args["track"] != track:
                    continue
                here = sorted((c for c in data["clips"] if c["sequence"] == seq_id and c["kind"] == kind
                               and c["track"] == track), key=lambda c: c["start_ticks"])
                for number, clip in enumerate(here):
                    item = data["items"].get(clip["item"], {})
                    record = {"track_type": kind, "track": track, "clip": number, "node_id": clip["node_id"],
                              "name": item.get("name"), "start_ticks": str(clip["start_ticks"]),
                              "end_ticks": str(clip["end_ticks"]), "in_ticks": str(clip["in_ticks"]),
                              "out_ticks": str(clip["out_ticks"]), "enabled": True, "speed": 1,
                              "adjustment_layer": False,
                              "source": {"node_id": clip["item"], "name": item.get("name"), "path": item.get("path")},
                              "effects": list(clip["effects"])}
                    if args.get("detail"):
                        record.update(interpreted_fps=item.get("fps"), linked=list(clip["linked"]), keyframed=[],
                                      transitions={"head": None, "tail": None})
                    found.append(record)
        offset, limit = args.get("offset", 0), args.get("limit", 400)
        return {"sequence": self._ref(data, seq_id), "used_active": args.get("sequence_id") is None,
                "total": len(found), "clips": found[offset:offset + limit]}
