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
        else:
            fail(rid, -32602, "Unknown tool: " + str(name))
    else:
        fail(rid, -32601, "Method not found: " + str(method))
'''


def fake_mcp_command(tmp_path, bridge_dir) -> list[str]:
    """Write the fake server to a file in tmp_path and return the command that starts it."""
    path = Path(tmp_path) / "fake_mcp_server.py"
    path.write_text(FAKE_MCP_SERVER, encoding="utf-8")
    return [sys.executable, str(path), str(bridge_dir)]


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
