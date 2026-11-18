"""Client for the file bridge of the installed Premiere panel.

The panel runs `cmd_<id>.jsx` files from a private temp folder and writes `res_<id>.json`.
Other programs (the premiere-pro-mcp server) share that folder, so this client follows the
same turn-taking lease and recovery-marker rules as `dist/bridge/host-lease.js`, and writes
those files in exactly the format the server checks.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from cli_anything.premiere.errors import CliError

BRIDGE_DIR_NAME = "premiere-mcp-bridge"
HEARTBEAT_STALE_S = 3.0
MAX_SCRIPT_BYTES = 500 * 1024
MAX_REPLY_BYTES = 1_048_576
BUSY_FRESH_S = 6.0          # a busy file younger than this means the script still runs
MAX_HOLD_S = 15.0           # a batch gives the lease back at least this often

HEARTBEAT_FILE = "bridge-heartbeat.json"
RECOVERY_FILE = ".host-recovery.json"
LOCK_DIR = ".host-owner-lease"
OWNER_FILE = "owner.json"
REAPER_DIR = ".host-owner-reaper"
RECOVERY_REASONS = ("unknown_command_outcome", "abandoned_process_lease")
MAX_RECORD_BYTES = 4096
LEASE_POLL_S = 0.025
BREATHE_PAUSE_S = 0.1       # breathe() waits this long between giving the lease back and taking it again:
                            # four polls of a program that waits for it (the server and this CLI poll every 25 ms)
REPLY_POLL_S = 0.025
DIALOG = "Premiere does not answer, a dialog may be open."
_TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,128}")


def default_bridge_dir() -> Path:
    """The bridge folder the panel uses: $PREMIERE_TEMP_DIR, else the per-user temp folder."""
    configured = os.environ.get("PREMIERE_TEMP_DIR")
    if configured:
        return Path(configured)
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["/usr/bin/getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True,
                                 text=True, timeout=5, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        if out and os.path.isabs(out):
            return Path(out) / BRIDGE_DIR_NAME
    return Path(tempfile.gettempdir()) / BRIDGE_DIR_NAME


def _private(st: os.stat_result) -> bool:
    return (st.st_mode & 0o077) == 0 and st.st_uid == os.getuid()


def check_private_dir(path: Path) -> None:
    """Check the bridge folder is a real folder only this user can use. Never creates it."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise CliError("bridge_missing", f"The bridge folder {path} does not exist. "
                                         "Open the MCP Bridge panel in Premiere.") from None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or not _private(st):
        raise CliError("bridge_unsafe", f"The bridge folder {path} is not a private folder of this user.")


# ---------- heartbeat ----------
@dataclass(frozen=True)
class Heartbeat:
    protocol_version: int
    command_schema_version: int
    receipt_version: int
    state: str
    age_s: float

    @property
    def fresh(self) -> bool:
        """The connector runs and the heartbeat is young: the panel takes commands."""
        return self.age_s <= HEARTBEAT_STALE_S and self.state == "running"

    @property
    def waiting(self) -> bool:
        """The panel is open and its heartbeat is young, but its connector is stopped: it takes no command."""
        return self.age_s <= HEARTBEAT_STALE_S and self.state == "waiting"


def _int(value: object) -> int | None:
    return value if type(value) is int else None


def read_heartbeat(bridge_dir: Path) -> Heartbeat | None:
    """The panel's heartbeat, or None when it is absent or unreadable."""
    path = Path(bridge_dir) / HEARTBEAT_FILE
    try:
        st = os.stat(path)
        if st.st_size > MAX_RECORD_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or _int(value.get("protocolVersion")) is None \
            or not isinstance(value.get("state"), str):
        return None
    return Heartbeat(protocol_version=value["protocolVersion"],
                     command_schema_version=_int(value.get("commandSchemaVersion")) or 0,
                     receipt_version=_int(value.get("receiptVersion")) or 0,
                     state=value["state"], age_s=max(0.0, time.time() - st.st_mtime))


# ---------- recovery marker ----------
def read_recovery(bridge_dir: Path) -> dict | None:
    """None when there is no recovery marker; else its reason and revision (sha256 of the raw bytes).

    Never raises. A marker that cannot be read (a folder, no permission, not UTF-8, not JSON) is an
    open recovery with the reason "unreadable", as the server treats a corrupt record. Its revision is
    None when not even the bytes can be read.
    """
    try:
        raw = (Path(bridge_dir) / RECOVERY_FILE).read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        return {"reason": "unreadable", "revision": None}
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):        # UnicodeDecodeError is a ValueError
        value = None
    reason = value.get("reason") if isinstance(value, dict) else None
    return {"reason": reason if isinstance(reason, str) else "unreadable",
            "revision": hashlib.sha256(raw).hexdigest()}


def _create_private(path: Path, value: dict) -> None:
    """Write a 0600 file that must not exist yet (raises FileExistsError if it does)."""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(value, separators=(",", ":")))


def mark_recovery(bridge_dir: Path, reason: str) -> None:
    """Leave the marker that tells every program to check Premiere before the next edit."""
    if reason not in RECOVERY_REASONS:
        raise ValueError(f"unknown recovery reason {reason!r}")
    try:
        _create_private(Path(bridge_dir) / RECOVERY_FILE,
                        {"version": 1, "reason": reason, "nonce": str(uuid.uuid4())})
    except FileExistsError:
        pass


# ---------- signals ----------
HANDLED_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class Interrupted(Exception):
    """SIGTERM or SIGHUP arrived. (SIGINT raises KeyboardInterrupt, as Python does by default.)"""

    def __init__(self, signum: int = signal.SIGTERM) -> None:
        super().__init__(signal.Signals(signum).name)
        self.signum = signum


class _Held:
    """The signals that the main thread's lock sections hold back.

    Only the main thread changes `depth`; the handler runs in the main thread too, so it never sees a
    half-made change. No lock is taken here: the handler can run while the main thread holds any lock.
    """
    depth = 0
    pending: list[int] = []


def _stop_for(signum: int) -> BaseException:
    return KeyboardInterrupt() if signum == signal.SIGINT else Interrupted(signum)


def _on_signal(signum: int, frame: object) -> None:
    if _Held.depth > 0:
        _Held.pending.append(signum)       # a lock section runs: it raises this when it ends
        return
    raise _stop_for(signum)


class _Section:
    """A lock section: SIGINT, SIGTERM and SIGHUP wait until it ends, then the first one is raised.

    Sections nest; the outermost one raises. In a thread that is not the main thread a section does
    nothing (Python runs signal handlers in the main thread only), and without the handlers of
    install_signal_handlers it holds nothing back either.

    on_signal: called inside the section, before it ends, when a signal is waiting (to undo a lease
    just taken). absorb: set to True by code whose own error already reports the stop; the waiting
    signal is then dropped instead of raised. When another exception is already on its way out, the
    waiting signal replaces it (unless absorb is set).
    """

    def __init__(self, on_signal=None) -> None:
        self._on_signal = on_signal
        self._active = False
        self.absorb = False

    def __enter__(self) -> _Section:
        if threading.current_thread() is threading.main_thread():
            self._active = True
            _Held.depth += 1
        return self

    @property
    def signalled(self) -> bool:
        """True when a signal waits for this section to end."""
        return self._active and bool(_Held.pending)

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not self._active:
            return False
        try:
            if _Held.pending and self._on_signal is not None:
                self._on_signal()
        finally:
            _Held.depth -= 1
            if _Held.depth == 0 and _Held.pending:
                signum = _Held.pending[0]
                _Held.pending.clear()
                if not self.absorb:
                    raise _stop_for(signum)      # also when on_signal failed: the stop is never lost
        return False


def hold_signals(on_signal=None) -> _Section:
    """`with hold_signals():` runs a block that a signal must not cut in two. See _Section."""
    return _Section(on_signal)


def install_signal_handlers() -> dict:
    """Make SIGINT raise KeyboardInterrupt and SIGTERM and SIGHUP raise Interrupted, in the main thread.

    Inside a lock section the signal waits until the section ends. Returns the handlers it replaced,
    for restore_signal_handlers.
    """
    return {signum: signal.signal(signum, _on_signal) for signum in HANDLED_SIGNALS}


def restore_signal_handlers(previous: dict) -> None:
    """Put back the handlers that install_signal_handlers replaced."""
    for signum, handler in previous.items():
        if handler is not None:
            signal.signal(signum, handler)


# ---------- lease ----------
def _unsafe(message: str) -> CliError:
    return CliError("lease_unsafe", f"{message} Nothing was sent.")


def read_owner(path: Path) -> tuple[int, str]:
    """(pid, token) of a valid owner.json. FileNotFoundError if absent, lease_unsafe if invalid."""
    st = os.lstat(path)
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_RECORD_BYTES or not _private(st):
        raise _unsafe("The lease owner record is unsafe.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise _unsafe("The lease owner record is unreadable.") from None
    if not isinstance(value, dict) or set(value) != {"version", "pid", "token"} or value["version"] != 1 \
            or type(value["version"]) is not int:
        raise _unsafe("The lease owner record is invalid.")
    pid, token = value["pid"], value["token"]
    if type(pid) is not int or not 0 < pid <= 0x7FFFFFFF or not isinstance(token, str) \
            or not _TOKEN.fullmatch(token):
        raise _unsafe("The lease owner record is invalid.")
    return pid, token


def pid_alive(pid: int) -> bool:
    """True when a process with this pid runs (also when it belongs to another user)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Lease:
    """The turn to talk to Premiere, shared with the premiere-pro-mcp server.

    With reap=False a lease held by a dead process counts as busy: this lease never clears it and never
    writes a recovery marker. Commands that only look (doctor, recovery show) use that mode.
    """

    def __init__(self, bridge_dir: Path, wait_s: float = 30.0, reap: bool = True) -> None:
        self.bridge_dir = Path(bridge_dir)
        self.wait_s = wait_s
        self.reap = reap
        self._lock = self.bridge_dir / LOCK_DIR
        self._owner = self._lock / OWNER_FILE
        self._token: str | None = None
        self._pid = 0
        self._since = 0.0

    @property
    def held(self) -> bool:
        return self._token is not None

    @property
    def held_for_s(self) -> float:
        return time.monotonic() - self._since if self.held else 0.0

    def acquire(self) -> None:
        """Take the lease, waiting up to wait_s. A signal that arrives while the lock folder and its
        record are made waits until both exist; the lease is then given back and the signal raised."""
        if self.held:
            raise RuntimeError("This lease is already held.")
        deadline = time.monotonic() + self.wait_s
        attempted = False
        while True:
            if attempted and time.monotonic() >= deadline:
                raise CliError("lease_busy", "Another program holds Premiere. Nothing was sent.")
            attempted = True
            try:
                with _Section(on_signal=self.release):
                    try:
                        os.mkdir(self._lock, 0o700)
                    except FileExistsError:
                        made = False
                    else:
                        self._claim()
                        made = True
            except (KeyboardInterrupt, Interrupted):
                if self.held:       # the signal came after on_signal looked: give the lease back now
                    with _Section():
                        self.release()
                raise
            if made:
                return
            # The waits are outside any section, so a signal stops them at once.
            if not self._clear_dead_owner() and time.monotonic() < deadline:
                time.sleep(LEASE_POLL_S)

    def _claim(self) -> None:
        """Write owner.json into the lock folder just made; on any failure remove what was made."""
        token, pid = str(uuid.uuid4()), os.getpid()
        try:
            _create_private(self._owner, {"version": 1, "pid": pid, "token": token})
            self._token, self._pid, self._since = token, pid, time.monotonic()
        except BaseException:
            # The lock folder is ours, so a half-written owner.json is ours too.
            self._token = None
            _unlink(self._owner)
            os.rmdir(self._lock)
            raise

    def _clear_dead_owner(self) -> bool:
        """Look at the lock another program holds. True to try again at once, False to wait."""
        try:
            st = os.lstat(self._lock)
        except FileNotFoundError:
            return True
        if not stat.S_ISDIR(st.st_mode) or not _private(st):
            raise _unsafe("The lease folder is unsafe.")
        try:
            owner = read_owner(self._owner)
        except FileNotFoundError:
            return False  # owner still writing it, or just gone
        if pid_alive(owner[0]) or not self.reap:
            return False          # a live holder, or a dead one that this lease may not clear: wait
        with _Section():          # from the marker to the reaper folder's removal
            mark_recovery(self.bridge_dir, "abandoned_process_lease")
            reaper = self.bridge_dir / REAPER_DIR
            try:
                os.mkdir(reaper, 0o700)
            except FileExistsError:
                return False  # another program reaps
            try:
                if read_owner(self._owner) == owner and not pid_alive(owner[0]):
                    os.unlink(self._owner)
                    os.rmdir(self._lock)
            except FileNotFoundError:
                pass
            finally:
                os.rmdir(reaper)
        return True

    def release(self) -> None:
        """Give the lease back. lease_lost (and a marker) if someone else changed owner.json.

        A signal that arrives meanwhile waits until the record and the lock folder are gone."""
        with _Section():
            if not self.held:
                return
            mine = (self._pid, self._token)
            self._token = None
            try:
                owner = read_owner(self._owner)
            except (OSError, CliError):
                owner = None
            if owner != mine:
                mark_recovery(self.bridge_dir, "unknown_command_outcome")
                raise CliError("lease_lost", "The lease changed while this program held it. "
                                             "Check Premiere before another edit.")
            os.unlink(self._owner)
            os.rmdir(self._lock)

    def __enter__(self) -> Lease:
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


# ---------- client ----------
class _GiveUp(Exception):
    """No reply in time."""


class _Stopped(Exception):
    """A signal waits: the send stops."""


def _unlink(path: Path) -> bool:
    try:
        os.unlink(path)
    except FileNotFoundError:
        return False
    return True


class BridgeClient:
    """Sends scripts to the Premiere panel through the bridge folder."""

    def __init__(self, bridge_dir: Path | None = None, lease_wait_s: float = 30.0, reap: bool = True) -> None:
        self.bridge_dir = Path(bridge_dir) if bridge_dir is not None else default_bridge_dir()
        self._lease = Lease(self.bridge_dir, wait_s=lease_wait_s, reap=reap)

    def lease(self) -> Lease:
        return self._lease

    def breathe(self) -> bool:
        """Give another program a turn when the lease was held MAX_HOLD_S or longer.

        Returns False, and does nothing, when the lease is not held or was held for less than
        MAX_HOLD_S. Otherwise it gives the lease back, waits BREATHE_PAUSE_S so that a waiting program
        can take it, takes it again (waiting up to the lease wait) and returns True: the lease is then
        held. When another program keeps it past the lease wait, it raises lease_busy and the lease is
        not held. The lease is not fair: a program that holds it again and again can make this wait.
        """
        if not self._lease.held or self._lease.held_for_s < MAX_HOLD_S:
            return False
        self._lease.release()
        time.sleep(BREATHE_PAUSE_S)
        self._lease.acquire()
        return True

    def send(self, script: str, *, write: bool, timeout_s: float = 30.0, hard_limit_s: float = 120.0) -> object:
        """Run one script in Premiere and return the "data" value of its reply."""
        if not self._lease.held:
            raise RuntimeError("send needs the lease: use `with client.lease():`.")
        body = script.encode("utf-8")
        if len(body) > MAX_SCRIPT_BYTES:
            raise CliError("script_too_large", f"The script has {len(body)} bytes, the limit is "
                                               f"{MAX_SCRIPT_BYTES}. Nothing was sent.")
        if write:
            heartbeat = read_heartbeat(self.bridge_dir)
            if heartbeat is None or not heartbeat.fresh:
                raise CliError("bridge_down", "The Premiere panel is not ready (no fresh heartbeat from a running "
                                              "connector). Nothing was sent.")
        cmd_id = str(uuid.uuid4())
        cmd = self.bridge_dir / f"cmd_{cmd_id}.jsx"
        staged = self.bridge_dir / f"cmd_{cmd_id}.jsx.staged"
        res = self.bridge_dir / f"res_{cmd_id}.json"
        busy = self.bridge_dir / f"busy_{cmd_id}.json"
        started = False
        # Signals are held for the whole send: the wait looks for one every poll and stops, and the
        # cleanup (delete the command file, or write the marker) runs to its end before any signal.
        with _Section() as section:
            try:
                try:
                    if section.signalled:
                        raise _Stopped()
                    staged.write_bytes(body)
                    started = True
                    os.rename(staged, cmd)
                    return self._wait(res, busy, write, timeout_s, hard_limit_s, section)
                except _GiveUp:
                    interrupted = False
                except (KeyboardInterrupt, Interrupted, _Stopped):
                    interrupted = True
                section.absorb = interrupted        # the error below reports the stop
                # A command file this client can still delete was never run.
                if not started or _unlink(staged) or _unlink(cmd):
                    if interrupted:
                        raise CliError("interrupted", "Stopped before Premiere picked up the command. "
                                                      "Nothing was changed.")
                    raise CliError("not_applied", "Premiere did not pick up the command. Nothing was changed.")
                if write:
                    mark_recovery(self.bridge_dir, "unknown_command_outcome")
                    message = ("Stopped while Premiere ran the edit; its result is unknown. "
                               "Check Premiere before another edit." if interrupted else DIALOG)
                    raise CliError("unknown_outcome", message)
                if interrupted:
                    raise CliError("interrupted", "Stopped while Premiere ran a read-only script.")
                raise CliError("timeout", DIALOG)
            finally:
                for path in (staged, cmd, res, busy):
                    _unlink(path)

    def _wait(self, res: Path, busy: Path, write: bool, timeout_s: float, hard_limit_s: float,
              section: _Section) -> object:
        start = time.monotonic()
        while True:
            reply = self._read_reply(res, write)
            if reply is not None:
                if reply["success"]:
                    return reply.get("data")
                raise CliError("script_failed", str(reply.get("error", "")))
            if section.signalled:
                raise _Stopped()
            elapsed = time.monotonic() - start
            if elapsed >= timeout_s and not (elapsed < hard_limit_s and _busy_fresh(busy)):
                raise _GiveUp()
            time.sleep(REPLY_POLL_S)

    def _read_reply(self, res: Path, write: bool) -> dict | None:
        """The parsed reply, None while there is none. bad_reply (and a marker for a write) if invalid."""
        try:
            size = os.stat(res).st_size
        except FileNotFoundError:
            return None
        reply = None
        if size <= MAX_REPLY_BYTES:
            try:
                reply = json.loads(res.read_text(encoding="utf-8"))
            except (OSError, ValueError, RecursionError):
                reply = None
        if not isinstance(reply, dict) or not isinstance(reply.get("success"), bool):
            if write:
                mark_recovery(self.bridge_dir, "unknown_command_outcome")
            raise CliError("bad_reply", "Premiere sent a reply that is not valid JSON or is too large.")
        return reply


def _busy_fresh(busy: Path) -> bool:
    try:
        return time.time() - os.stat(busy).st_mtime < BUSY_FRESH_S
    except FileNotFoundError:
        return False
