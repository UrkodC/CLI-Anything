"""The write session: the only door through which a recipe changes Premiere.

A session takes the bridge lease, runs the guards (panel, recovery marker, Premiere state, project
guard) and keeps the lease until it ends. Every script it sends gets `ARGS.expect`, the project and
sequence the session expects, so the script itself can refuse before its first edit when the owner
switched the project or renamed the sequence meanwhile (the lease orders programs, not his clicks).
"""
from __future__ import annotations

import contextlib

from cli_anything.premiere.core import guard, jsx
from cli_anything.premiere.core.bridge import BridgeClient, read_heartbeat
from cli_anything.premiere.errors import CliError

SOFT_TIMEOUT_S = 30.0       # after this, wait on only while the panel says the script still runs
DEFAULT_HARD_LIMIT_S = 120.0
PRECONDITION = "precondition:"


def run_script(client: BridgeClient, body: str, args: dict | None = None, *, write: bool,
               timeout_s: float = SOFT_TIMEOUT_S, hard_limit_s: float = DEFAULT_HARD_LIMIT_S,
               include: tuple[str, ...] = ()) -> object:
    """Build one script from a body, its arguments and the included scripts, and run it. The caller
    holds the lease.

    A script failure whose message starts with "precondition:" (also after the "Error: " that a thrown
    Error carries) is raised as CliError("precondition", <the rest of the message>).
    """
    script = jsx.build(body, args, include=include)
    try:
        return client.send(script, write=write, timeout_s=timeout_s, hard_limit_s=hard_limit_s)
    except CliError as err:
        if err.code != "script_failed":
            raise
        text = err.message[len("Error: "):] if err.message.startswith("Error: ") else err.message
        if not text.startswith(PRECONDITION):
            raise
        raise CliError("precondition", text[len(PRECONDITION):].strip()) from None


def guarded_state(client: BridgeClient, *, write: bool, project: str | None, any_project: bool,
                  allow_untested: bool, hard_limit_s: float = DEFAULT_HARD_LIMIT_S) -> tuple[dict, list[str]]:
    """Run the safety checks and read Premiere's state, inside the lease the caller holds.

    Order: the panel heartbeat and version, the recovery marker, the state script (always a read),
    then the checks on that state. Returns (state, warnings of all three checks in that order).
    """
    warnings = guard.check_bridge(read_heartbeat(client.bridge_dir), guard.panel_version(),
                                  write=write, allow_untested=allow_untested)
    warnings += guard.check_recovery(client.bridge_dir, write=write)
    state = run_script(client, jsx.load("state"), None, write=False,
                       timeout_s=min(SOFT_TIMEOUT_S, hard_limit_s), hard_limit_s=hard_limit_s)
    if not isinstance(state, dict):
        raise CliError("bad_state", "The state script did not return an object.")
    warnings += guard.check_state(state, write=write, project=project, any_project=any_project,
                                  allow_untested=allow_untested)
    return state, warnings


class WriteSession:
    """`with WriteSession(client) as s:` holds the lease and passes the write guards for the whole block.

    s.read and s.write send scripts (find.jsx included by default) with ARGS.expect added; s.write checks
    the recovery marker right before each send. s.breathe gives other programs a turn, s.paused gives
    the lease back for calls to the MCP server; both run the guards again after taking the lease back.

    When a guard after a breath or a pause fails, when the lease cannot be taken back, or when a write's
    recovery marker could not be written (the lock is then kept), the session closes: the lease is not
    held by it any more, read, write, breathe and paused raise RuntimeError, and leaving the with block
    does nothing.
    """

    def __init__(self, client: BridgeClient, *, project: str | None = None, any_project: bool = False,
                 allow_untested: bool = False, hard_limit_s: float = DEFAULT_HARD_LIMIT_S) -> None:
        self.client = client
        self.project = project
        self.any_project = any_project
        self.allow_untested = allow_untested
        self.hard_limit_s = hard_limit_s
        self.state: dict = {}
        self.warnings: list[str] = []
        self.expect: dict = {"project_path": None, "sequence_id": None, "sequence_name": None}
        self._entered = False
        self._closed = False
        self._paused = False

    # ---------- life cycle ----------
    def __enter__(self) -> WriteSession:
        if self._entered:
            raise RuntimeError("A write session can be entered only once.")
        self._entered = True
        lease = self.client.lease()
        lease.acquire()
        try:
            self._guard(first=True)
        except BaseException:
            self._closed = True
            lease.release()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        if self._closed:
            return
        self._closed = True
        self.client.lease().release()

    def _guard(self, *, first: bool) -> None:
        """Run guarded_state(write=True). The first run fixes the expected project; a later run must find
        the same active project."""
        state, warnings = guarded_state(self.client, write=True, project=self.project, any_project=self.any_project,
                                        allow_untested=self.allow_untested, hard_limit_s=self.hard_limit_s)
        active = guard.active_project(state.get("projects") or [])
        path = active.get("path") if active else None
        if not isinstance(path, str) or not path:
            raise CliError("bad_state", "The active project has no path. Save it in Premiere first.")
        if first:
            self.expect["project_path"] = path
        elif path != self.expect["project_path"]:
            raise CliError("project_mismatch", f"The active project changed from {self.expect['project_path']} "
                                               f"to {path} while this program gave Premiere back. Nothing more was sent.")
        self.state, self.warnings = state, warnings

    def _close(self) -> None:
        """Close after a failure: give the lease back if it is still held (a no-op when it is not)."""
        self._closed = True
        self.client.lease().release()

    def _need_open(self) -> None:
        if not self._entered or self._closed:
            raise RuntimeError("This write session is not open.")
        if self._paused:
            raise RuntimeError("This write session is paused: it does not hold the lease.")

    # ---------- targets and scripts ----------
    def target(self, sequence_id: str, sequence_name: str | None = None) -> None:
        """The sequence every later script expects (checked inside the script by __expect)."""
        self.expect["sequence_id"] = sequence_id
        self.expect["sequence_name"] = sequence_name

    def _args(self, args: dict | None) -> dict:
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise CliError("bad_args", f"Script arguments must be a dict, not {type(args).__name__}. Nothing was sent.")
        if "expect" in args:
            raise CliError("bad_args", "Script arguments may not hold the key 'expect': the session sets it. "
                                       "Nothing was sent.")
        return {**args, "expect": dict(self.expect)}

    def _timeouts(self) -> dict:
        return {"timeout_s": min(SOFT_TIMEOUT_S, self.hard_limit_s), "hard_limit_s": self.hard_limit_s}

    def read(self, body: str, args: dict | None = None, *, include: tuple[str, ...] = ("find",)) -> object:
        """Run a read-only script. A precondition failure raises CliError("precondition")."""
        self._need_open()
        return run_script(self.client, body, self._args(args), write=False, include=include, **self._timeouts())

    def write(self, body: str, args: dict | None = None, *, include: tuple[str, ...] = ("find",)) -> object:
        """Run a script that changes the project. The recovery marker is checked right before the send."""
        self._need_open()
        full = self._args(args)
        guard.check_recovery(self.client.bridge_dir, write=True)
        try:
            return run_script(self.client, body, full, write=True, include=include, **self._timeouts())
        except CliError:
            if self.client.lease().retained:      # the marker could not be written: the lock stays
                self._closed = True
            raise

    # ---------- giving the lease back ----------
    def breathe(self) -> bool:
        """client.breathe(); when it gave the lease back and took it again, run the guards again."""
        self._need_open()
        try:
            breathed = self.client.breathe()
            if breathed:
                self._guard(first=False)
        except BaseException:
            self._close()
            raise
        return breathed

    def paused(self):
        """`with s.paused():` gives the lease back for the block (for calls to the MCP server, which takes
        the same lease), then takes it again and runs the guards again.

        When the block raises, the lease is not taken again: the session closes and the error goes on."""
        self._need_open()
        return self._pause()

    @contextlib.contextmanager
    def _pause(self):
        lease = self.client.lease()
        try:
            lease.release()
        except BaseException:
            self._closed = True
            raise
        self._paused = True
        try:
            yield self
        except BaseException:
            self._paused = False
            self._closed = True
            raise
        self._paused = False
        try:
            lease.acquire()
            self._guard(first=False)
        except BaseException:
            self._close()
            raise
