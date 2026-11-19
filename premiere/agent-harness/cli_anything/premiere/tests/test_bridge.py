"""Unit tests for the bridge client, against a fake panel thread in a temp folder (no Premiere needed)."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import queue
import random
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import types

import pytest
from pathlib import Path

from cli_anything.premiere.core import bridge
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.tests.fakes import (  # noqa: F401
    DEFAULT_HEARTBEAT, HEARTBEAT_FILE, MARKER_SHAPES, SIGNAL_CHILD, FakePanel, bridge_dir, keep_signals, restore_marker_mode,
    signal_when, unreadable_marker)

LOCK = ".host-owner-lease"
PACKAGE_ROOT = Path(__file__).resolve().parents[3]


@contextlib.contextmanager
def running(panel: FakePanel):
    panel.start()
    try:
        yield panel
    finally:
        panel.stop()


@pytest.fixture(autouse=True)
def _keep_signal_handlers(keep_signals):  # noqa: F811
    """Every test starts with the default handlers of SIGINT, SIGTERM and SIGHUP and leaves them as found."""
    yield


def write_owner(bridge_dir, record) -> None:
    lock = bridge_dir / LOCK
    lock.mkdir(mode=0o700)
    owner = lock / "owner.json"
    fd = os.open(owner, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(record))


def dead_pid() -> int:
    proc = subprocess.Popen(["/usr/bin/true"])
    proc.wait()
    return proc.pid


def send_error(client, script="1", **kw) -> CliError:
    with client.lease():
        with pytest.raises(CliError) as err:
            client.send(script, **kw)
    return err.value


def leftovers(bridge_dir) -> list[str]:
    return sorted(p.name for p in bridge_dir.iterdir())


# ---------- folder ----------
def test_default_dir_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PREMIERE_TEMP_DIR", str(tmp_path / "x"))
    assert bridge.default_bridge_dir() == tmp_path / "x"


def test_check_private_dir(bridge_dir, tmp_path):
    bridge.check_private_dir(bridge_dir)
    with pytest.raises(CliError) as e:
        bridge.check_private_dir(tmp_path / "none")
    assert e.value.code == "bridge_missing"
    bridge_dir.chmod(0o755)
    with pytest.raises(CliError) as e:
        bridge.check_private_dir(bridge_dir)
    assert e.value.code == "bridge_unsafe"
    link = tmp_path / "link"
    link.symlink_to(tmp_path)
    with pytest.raises(CliError) as e:
        bridge.check_private_dir(link)
    assert e.value.code == "bridge_unsafe"


# ---------- lease ----------
def test_lease_files_have_the_server_format(bridge_dir, monkeypatch):
    lease = bridge.Lease(bridge_dir)
    lease.acquire()
    assert lease.held
    with pytest.raises(RuntimeError):
        lease.acquire()
    lock = bridge_dir / LOCK
    owner = lock / "owner.json"
    data = json.loads(owner.read_text())
    assert set(data) == {"version", "pid", "token"} and data["version"] == 1 and data["pid"] == os.getpid()
    assert re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", data["token"])
    assert stat.S_IMODE(lock.stat().st_mode) == 0o700 and stat.S_IMODE(owner.stat().st_mode) == 0o600
    lease.release()
    assert not lease.held
    assert not lock.exists() and bridge.read_recovery(bridge_dir) is None

    def half_write(path, value):
        path.write_text('{"version":1,')
        raise OSError("disk full")

    with monkeypatch.context() as mp:
        mp.setattr(bridge, "_create_private", half_write)
        with pytest.raises(OSError):
            lease.acquire()
    assert not lease.held and not lock.exists()


def test_lease_waits_then_gets_it(bridge_dir):
    write_owner(bridge_dir, {"version": 1, "pid": os.getpid(), "token": "other"})
    lock = bridge_dir / LOCK

    def let_go():
        (lock / "owner.json").unlink()
        lock.rmdir()

    timer = threading.Timer(0.3, let_go)
    timer.start()
    try:
        lease = bridge.Lease(bridge_dir, wait_s=5.0)
        start = time.monotonic()
        lease.acquire()
        assert time.monotonic() - start >= 0.25
        assert json.loads((lock / "owner.json").read_text())["token"] != "other"
        lease.release()
    finally:
        timer.join()
    assert bridge.read_recovery(bridge_dir) is None


def test_lease_busy_past_the_wait(bridge_dir):
    record = {"version": 1, "pid": os.getpid(), "token": "other"}
    write_owner(bridge_dir, record)
    owner = bridge_dir / LOCK / "owner.json"
    before = owner.read_text()
    with pytest.raises(CliError) as e:
        bridge.Lease(bridge_dir, wait_s=0.2).acquire()
    assert e.value.code == "lease_busy"
    assert owner.read_text() == before
    assert bridge.read_recovery(bridge_dir) is None


def test_dead_owner_is_reaped_with_a_marker(bridge_dir):
    write_owner(bridge_dir, {"version": 1, "pid": dead_pid(), "token": "gone"})
    lease = bridge.Lease(bridge_dir, wait_s=2.0)
    lease.acquire()
    assert json.loads((bridge_dir / LOCK / "owner.json").read_text())["pid"] == os.getpid()
    assert bridge.read_recovery(bridge_dir)["reason"] == "abandoned_process_lease"
    assert not (bridge_dir / ".host-owner-reaper").exists()
    lease.release()


def test_invalid_owner_record_is_refused(bridge_dir):
    write_owner(bridge_dir, {"version": 1, "pid": os.getpid(), "token": "t", "x": 1})
    with pytest.raises(CliError) as e:
        bridge.Lease(bridge_dir, wait_s=0.2).acquire()
    assert e.value.code == "lease_unsafe"
    lock = bridge_dir / LOCK
    (lock / "owner.json").unlink()
    lock.chmod(0o755)
    with pytest.raises(CliError) as e:
        bridge.Lease(bridge_dir, wait_s=0.2).acquire()
    assert e.value.code == "lease_unsafe"
    assert bridge.read_recovery(bridge_dir) is None


def test_release_detects_a_changed_owner(bridge_dir):
    lease = bridge.Lease(bridge_dir)
    lease.acquire()
    owner = bridge_dir / LOCK / "owner.json"
    owner.write_text(json.dumps({"version": 1, "pid": os.getpid(), "token": "someone-else"}))
    with pytest.raises(CliError) as e:
        lease.release()
    assert e.value.code == "lease_lost"
    assert not lease.held
    assert bridge.read_recovery(bridge_dir)["reason"] == "unknown_command_outcome"
    assert owner.exists()  # not ours: left alone


def test_recovery_marker_format(bridge_dir):
    bridge.mark_recovery(bridge_dir, "unknown_command_outcome")
    path = bridge_dir / ".host-recovery.json"
    raw = path.read_text()
    data = json.loads(raw)
    assert set(data) == {"version", "reason", "nonce"} and data["version"] == 1
    assert re.fullmatch(r"[a-f0-9-]{36}", data["nonce"]) and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert bridge.read_recovery(bridge_dir) == {"reason": "unknown_command_outcome",
                                               "revision": hashlib.sha256(raw.encode()).hexdigest()}
    bridge.mark_recovery(bridge_dir, "abandoned_process_lease")
    assert path.read_text() == raw


# ---------- send ----------
def test_send_round_trip(bridge_dir):
    client = bridge.BridgeClient(bridge_dir)
    with running(FakePanel(bridge_dir)) as panel:
        with client.lease():
            assert client.send("app.version", write=True) == {"echo": True}
    assert panel.seen == ["app.version"]
    assert leftovers(bridge_dir) == [HEARTBEAT_FILE]


def test_send_needs_the_lease(bridge_dir):
    client = bridge.BridgeClient(bridge_dir)
    with pytest.raises(RuntimeError):
        client.send("1", write=False)
    assert leftovers(bridge_dir) == []


def test_send_script_failed(bridge_dir):
    client = bridge.BridgeClient(bridge_dir)
    with running(FakePanel(bridge_dir, handler=lambda s: '{"success":false,"error":"no sequence"}')):
        err = send_error(client, write=True)
    assert err.code == "script_failed" and "no sequence" in err.message
    assert bridge.read_recovery(bridge_dir) is None
    assert leftovers(bridge_dir) == [HEARTBEAT_FILE]


def test_send_too_large(bridge_dir):
    client = bridge.BridgeClient(bridge_dir)
    with running(FakePanel(bridge_dir)) as panel:
        err = send_error(client, "x" * (bridge.MAX_SCRIPT_BYTES + 1), write=False)
    assert err.code == "script_too_large"
    assert panel.seen == [] and leftovers(bridge_dir) == [HEARTBEAT_FILE]


def test_write_refused_without_heartbeat(bridge_dir):
    client = bridge.BridgeClient(bridge_dir)
    with running(FakePanel(bridge_dir, heartbeat=None)) as panel:
        assert send_error(client, write=True).code == "bridge_down"
        heartbeat = bridge_dir / HEARTBEAT_FILE
        heartbeat.write_text(json.dumps({"protocolVersion": 1, "commandSchemaVersion": 1,
                                         "receiptVersion": 1, "state": "running"}))
        old = time.time() - 10
        os.utime(heartbeat, (old, old))
        assert send_error(client, write=True).code == "bridge_down"
    assert panel.seen == [] and leftovers(bridge_dir) == [HEARTBEAT_FILE]


def test_write_refused_on_a_waiting_heartbeat(bridge_dir):
    """A panel whose connector is stopped has a young heartbeat, but it runs no command."""
    client = bridge.BridgeClient(bridge_dir)
    with running(FakePanel(bridge_dir, heartbeat={**DEFAULT_HEARTBEAT, "state": "waiting"})) as panel:
        err = send_error(client, write=True)
    assert err.code == "bridge_down" and "Nothing was sent" in err.message
    assert "is not running" not in err.message           # the panel is open: the message must stay true
    assert panel.seen == [] and leftovers(bridge_dir) == [HEARTBEAT_FILE]
    assert bridge.read_recovery(bridge_dir) is None


def test_waiting_heartbeat_is_not_fresh():
    beat = bridge.Heartbeat(protocol_version=1, command_schema_version=1, receipt_version=1,
                            state="waiting", age_s=0.1)
    assert beat.fresh is False and beat.waiting is True


@pytest.mark.parametrize("state, age_s, fresh, waiting", [
    ("running", 0.1, True, False),
    ("waiting", 0.1, False, True),
    ("running", 3.0, True, False),
    ("waiting", 3.0, False, True),
    ("running", 3.1, False, False),
    ("waiting", 3.1, False, False),
    ("stopped", 0.1, False, False),
])
def test_heartbeat_fresh_and_waiting(state, age_s, fresh, waiting):
    beat = bridge.Heartbeat(protocol_version=1, command_schema_version=1, receipt_version=1,
                            state=state, age_s=age_s)
    assert beat.fresh is fresh and beat.waiting is waiting


def test_read_heartbeat_keeps_the_waiting_state(bridge_dir):
    with running(FakePanel(bridge_dir, heartbeat={**DEFAULT_HEARTBEAT, "state": "waiting"})):
        beat = bridge.read_heartbeat(bridge_dir)
    assert beat is not None and beat.state == "waiting" and beat.waiting and not beat.fresh


def test_read_timeout_has_no_marker_and_names_a_dialog(bridge_dir):
    client = bridge.BridgeClient(bridge_dir)
    with running(FakePanel(bridge_dir, handler=lambda s: None)) as panel:
        err = send_error(client, write=False, timeout_s=0.5)
    assert err.code == "timeout" and "dialog" in err.message
    assert panel.seen == ["1"]
    assert bridge.read_recovery(bridge_dir) is None
    assert not client.lease().held and leftovers(bridge_dir) == [HEARTBEAT_FILE]


def test_write_timeout_marks_recovery(bridge_dir):
    client = bridge.BridgeClient(bridge_dir)
    with running(FakePanel(bridge_dir, handler=lambda s: None)):
        err = send_error(client, write=True, timeout_s=0.5)
    assert err.code == "unknown_outcome" and "dialog" in err.message
    assert bridge.read_recovery(bridge_dir)["reason"] == "unknown_command_outcome"
    assert not (bridge_dir / LOCK).exists()


def test_unclaimed_command_is_not_applied(bridge_dir):
    (bridge_dir / HEARTBEAT_FILE).write_text(json.dumps({"protocolVersion": 1, "commandSchemaVersion": 1,
                                                         "receiptVersion": 1, "state": "running"}))
    client = bridge.BridgeClient(bridge_dir)
    err = send_error(client, write=True, timeout_s=0.3)
    assert err.code == "not_applied"
    stopped = _send_with_signal(client, signal.SIGINT, write=True,
                                ready=lambda: bool(list(bridge_dir.glob("cmd_*.jsx"))))
    assert stopped.code == "interrupted" and "Nothing was changed" in stopped.message
    assert bridge.read_recovery(bridge_dir) is None
    assert leftovers(bridge_dir) == [HEARTBEAT_FILE]


def test_busy_file_extends_the_wait(bridge_dir):
    client = bridge.BridgeClient(bridge_dir)
    with running(FakePanel(bridge_dir, delay_s=1.5, busy=True)):
        with client.lease():
            start = time.monotonic()
            assert client.send("1", write=True, timeout_s=0.5, hard_limit_s=5) == {"echo": True}
            assert time.monotonic() - start >= 1.4
    assert bridge.read_recovery(bridge_dir) is None


def test_hard_limit_stops_a_busy_script(bridge_dir):
    client = bridge.BridgeClient(bridge_dir)
    with running(FakePanel(bridge_dir, handler=lambda s: None, busy=True)):
        start = time.monotonic()
        err = send_error(client, write=True, timeout_s=0.3, hard_limit_s=1.0)
        elapsed = time.monotonic() - start
    assert err.code == "unknown_outcome"
    assert 1.0 <= elapsed < 3.0
    assert bridge.read_recovery(bridge_dir)["reason"] == "unknown_command_outcome"


def test_bad_reply(bridge_dir):
    replies = iter(["", "{"])
    client = bridge.BridgeClient(bridge_dir)
    with running(FakePanel(bridge_dir, handler=lambda s: next(replies))):
        assert send_error(client, write=False).code == "bad_reply"
        assert bridge.read_recovery(bridge_dir) is None
        assert send_error(client, write=True).code == "bad_reply"
        assert bridge.read_recovery(bridge_dir)["reason"] == "unknown_command_outcome"
    assert leftovers(bridge_dir) == [".host-recovery.json", HEARTBEAT_FILE]


def test_oversize_reply_is_bad(bridge_dir, monkeypatch):
    monkeypatch.setattr(bridge, "MAX_REPLY_BYTES", 64)
    big = json.dumps({"success": True, "data": "x" * 200})
    client = bridge.BridgeClient(bridge_dir)
    with running(FakePanel(bridge_dir, handler=lambda s: big)):
        assert send_error(client, write=False).code == "bad_reply"
    assert not client.lease().held and not (bridge_dir / LOCK).exists()
    assert leftovers(bridge_dir) == [HEARTBEAT_FILE]


def _send_with_signal(client, sig, *, write: bool, ready) -> CliError:
    """Send `sig` during a send that never gets an answer, once `ready()` is true (M1.5: the signal is
    armed on what the test needs, the pickup or the command file, not on a fixed delay)."""
    with client.lease():
        with signal_when(sig, ready) as sent:
            with pytest.raises(CliError) as err:
                client.send("1", write=write, timeout_s=5.0)
    assert sent["fired"]
    return err.value


def test_interrupt_during_read_and_write(bridge_dir):
    client = bridge.BridgeClient(bridge_dir)
    lock = bridge_dir / LOCK
    with running(FakePanel(bridge_dir, handler=lambda s: None)) as panel:
        err = _send_with_signal(client, signal.SIGINT, write=False, ready=lambda: bool(panel.seen))
        assert err.code == "interrupted" and "read-only script" in err.message      # after the pickup
        assert bridge.read_recovery(bridge_dir) is None and not lock.exists()
    assert panel.seen == ["1"]
    with running(FakePanel(bridge_dir, handler=lambda s: None)) as panel:
        err = _send_with_signal(client, signal.SIGINT, write=True, ready=lambda: bool(panel.seen))
        assert err.code == "unknown_outcome"
        assert bridge.read_recovery(bridge_dir)["reason"] == "unknown_command_outcome"
        assert not lock.exists()
    bridge.install_signal_handlers()                # the autouse fixture puts the old handlers back
    with running(FakePanel(bridge_dir, handler=lambda s: None)) as panel:
        err = _send_with_signal(client, signal.SIGTERM, write=False, ready=lambda: bool(panel.seen))
        assert err.code == "interrupted" and "read-only script" in err.message
        assert not lock.exists()


def test_breathe_gives_the_lease_back(bridge_dir, monkeypatch):
    client = bridge.BridgeClient(bridge_dir)
    owner = bridge_dir / LOCK / "owner.json"
    with client.lease():
        assert client.breathe() is False
        first = json.loads(owner.read_text())["token"]
        monkeypatch.setattr(bridge, "MAX_HOLD_S", 0.0)
        assert client.breathe() is True
        assert client.lease().held
        assert json.loads(owner.read_text())["token"] != first
    assert not owner.parent.exists()


# ---------- final review fixes: A3 ----------
def json_that_recurses(monkeypatch, start: str = "") -> None:
    """Make bridge's json.loads raise RecursionError on texts that begin with `start`, as Python before
    3.14 does on JSON nested too deep (3.14 parses such text without recursion)."""
    def loads(text, *a, **k):
        if text.startswith(start):
            raise RecursionError("maximum recursion depth exceeded")
        return json.loads(text, *a, **k)
    monkeypatch.setattr(bridge, "json", types.SimpleNamespace(loads=loads, dumps=json.dumps))


@pytest.mark.parametrize("shape", MARKER_SHAPES)
def test_read_recovery_never_raises(bridge_dir, shape):
    unreadable_marker(bridge_dir, shape)
    try:
        marker = bridge.read_recovery(bridge_dir)
    finally:
        restore_marker_mode(bridge_dir)
    assert marker is not None and marker["reason"] == "unreadable"
    assert set(marker) == {"reason", "revision"}


def test_read_recovery_survives_a_marker_nested_too_deep(bridge_dir, monkeypatch):
    bridge.mark_recovery(bridge_dir, "unknown_command_outcome")
    raw = (bridge_dir / ".host-recovery.json").read_bytes()
    json_that_recurses(monkeypatch)
    assert bridge.read_recovery(bridge_dir) == {"reason": "unreadable",
                                               "revision": hashlib.sha256(raw).hexdigest()}


def test_a_reply_nested_too_deep_is_a_bad_reply_with_a_marker(bridge_dir, monkeypatch):
    client = bridge.BridgeClient(bridge_dir)
    with running(FakePanel(bridge_dir)):
        json_that_recurses(monkeypatch, start='{"success"')
        assert send_error(client, write=False).code == "bad_reply"
        assert not (bridge_dir / ".host-recovery.json").exists()
        assert send_error(client, write=True).code == "bad_reply"
        monkeypatch.undo()
    assert bridge.read_recovery(bridge_dir)["reason"] == "unknown_command_outcome"
    assert not (bridge_dir / LOCK).exists()


# ---------- final review fixes: A2 ----------
def test_a_lease_that_does_not_reap_counts_a_dead_owner_as_busy(bridge_dir):
    write_owner(bridge_dir, {"version": 1, "pid": dead_pid(), "token": "gone"})
    owner = bridge_dir / LOCK / "owner.json"
    before = owner.read_bytes()
    for lease in (bridge.Lease(bridge_dir, wait_s=0.2, reap=False),
                  bridge.BridgeClient(bridge_dir, lease_wait_s=0.2, reap=False).lease()):
        assert lease.reap is False
        with pytest.raises(CliError) as e:
            lease.acquire()
        assert e.value.code == "lease_busy" and not lease.held
    assert owner.read_bytes() == before
    assert bridge.read_recovery(bridge_dir) is None
    assert not (bridge_dir / ".host-owner-reaper").exists()
    assert bridge.Lease(bridge_dir).reap is True and bridge.BridgeClient(bridge_dir).lease().reap is True


def test_owner_helpers_are_public(bridge_dir):
    write_owner(bridge_dir, {"version": 1, "pid": os.getpid(), "token": "t1"})
    assert bridge.read_owner(bridge_dir / LOCK / "owner.json") == (os.getpid(), "t1")
    assert bridge.pid_alive(os.getpid()) is True and bridge.pid_alive(dead_pid()) is False


# ---------- final review fixes: A1 (d) ----------
SIGNAL_TARGET = 600                 # interrupts to send; the test needs at least 500 to land
SIGNAL_KINDS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
SIGNAL_TEST_BUDGET_S = 50.0         # every wait of the test comes out of this one budget
SIGNAL_LOOP_S = 30.0                # the child stops by itself after this


def _lines(stream, out: queue.Queue) -> None:
    for line in stream:
        out.put(line.strip())
    out.put(None)


def test_signals_at_random_times_never_break_the_lock(bridge_dir, tmp_path):
    """Real signals at random times hit a child process that loops acquire and release on a temp
    bridge folder. No lock folder may be left without a valid owner record, and no lock may be left
    by an object that says "not held"."""
    child = tmp_path / "signal_child.py"
    child.write_text(SIGNAL_CHILD, encoding="utf-8")
    stop = tmp_path / "stop"
    env = {**os.environ, "PYTHONPATH": str(PACKAGE_ROOT)}
    seed = random.randrange(1 << 30)
    rng = random.Random(seed)
    budget_end = time.monotonic() + SIGNAL_TEST_BUDGET_S

    def left(cap: float) -> float:
        return max(0.1, min(cap, budget_end - time.monotonic()))

    proc = subprocess.Popen([sys.executable, str(child), str(bridge_dir), str(stop), str(SIGNAL_LOOP_S)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=PACKAGE_ROOT, env=env)
    lines: queue.Queue = queue.Queue()
    reader = threading.Thread(target=_lines, args=(proc.stdout, lines), daemon=True)
    reader.start()
    loop_end = time.monotonic() + SIGNAL_LOOP_S - 2.0
    sent = 0
    try:
        assert lines.get(timeout=left(10)) == "ready", f"seed {seed}"
        while sent < SIGNAL_TARGET and time.monotonic() < loop_end:
            time.sleep(rng.uniform(0, 0.003))
            os.kill(proc.pid, rng.choice(SIGNAL_KINDS))
            sent += 1
            answer = lines.get(timeout=left(5))
            assert answer == "ack", f"seed {seed}, signal {sent}: the child said {answer!r}, " \
                                    f"status {proc.poll()}"
        stop.touch()
        status = proc.wait(timeout=left(10))
        reader.join(timeout=left(2))
        stderr = proc.stderr.read()
    finally:
        if proc.poll() is None:             # only after a failure: the budget may be spent, so no wait here
            proc.kill()
    rest = []
    while not lines.empty():
        rest.append(lines.get())
    final = [line for line in rest if line]
    assert final, f"seed {seed}: no result from the child, status {proc.returncode}: {stderr[-2000:]}"
    result = json.loads(final[-1])
    assert status == 0, f"seed {seed}: {stderr[-2000:]}"
    kinds = {problem: result["problems"].count(problem) for problem in set(result["problems"])}
    assert result["problems"] == [], f"seed {seed}, {result['interrupts']} interrupts: {kinds}"
    assert result["interrupts"] == sent and sent >= 500, f"seed {seed}: {result}"
    assert not (bridge_dir / LOCK).exists() and not (bridge_dir / ".host-owner-reaper").exists()
    print(f"seed {seed}: {result['interrupts']} interrupts, {result['cycles']} cycles, no problem")


# ---------- final review fixes: A1 ----------
def test_handlers_keep_keyboard_interrupt_and_sections_hold_signals_back(keep_signals):  # noqa: F811
    previous = bridge.install_signal_handlers()
    assert set(previous) == {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
    with pytest.raises(KeyboardInterrupt):
        signal.raise_signal(signal.SIGINT)              # a plain Ctrl-C outside a section
    for signum in (signal.SIGTERM, signal.SIGHUP):
        with pytest.raises(bridge.Interrupted) as e:
            signal.raise_signal(signum)
        assert e.value.signum == signum
    reached = False
    with pytest.raises(KeyboardInterrupt):
        with bridge.hold_signals() as section:
            signal.raise_signal(signal.SIGINT)
            signal.raise_signal(signal.SIGTERM)
            assert section.signalled
            reached = True                              # the section ran to its end
    assert reached
    with bridge.hold_signals() as section:              # nothing is left over
        assert not section.signalled
    absorbed = bridge.hold_signals()
    with absorbed:
        absorbed.absorb = True
        signal.raise_signal(signal.SIGTERM)             # dropped: the code reports the stop itself
    bridge.restore_signal_handlers(previous)
    assert signal.getsignal(signal.SIGTERM) == previous[signal.SIGTERM]


def test_the_lease_works_from_a_worker_thread(bridge_dir, keep_signals):  # noqa: F811
    seen = []

    def work():
        try:
            with bridge.Lease(bridge_dir, wait_s=1.0):
                seen.append((bridge_dir / LOCK).is_dir())
            with bridge.hold_signals() as section:
                seen.append(section.signalled)
        except BaseException as exc:            # report, do not lose, any failure
            seen.append(exc)

    for install in (False, True):
        if install:
            bridge.install_signal_handlers()
        worker = threading.Thread(target=work)
        worker.start()
        worker.join(timeout=5)
    assert seen == [True, False, True, False]
    assert not (bridge_dir / LOCK).exists()


def test_a_second_signal_in_the_cleanup_does_not_skip_the_marker(bridge_dir, monkeypatch, keep_signals):  # noqa: F811
    bridge.install_signal_handlers()
    original = bridge.mark_recovery
    calls = []

    def mark_after_a_signal(folder, reason):
        calls.append(reason)
        signal.raise_signal(signal.SIGTERM)     # a second stop, just before the marker is written
        original(folder, reason)
    monkeypatch.setattr(bridge, "mark_recovery", mark_after_a_signal)
    client = bridge.BridgeClient(bridge_dir)
    with running(FakePanel(bridge_dir, handler=lambda s: None)) as panel:
        with signal_when(signal.SIGTERM, lambda: bool(panel.seen)) as sent:
            err = send_error(client, write=True, timeout_s=5.0)
        assert sent["fired"]
        assert err.code == "unknown_outcome" and "unknown" in err.message
        assert bridge.read_recovery(bridge_dir)["reason"] == "unknown_command_outcome"
    (bridge_dir / ".host-recovery.json").unlink()
    # After a timeout, the stop that arrives in the cleanup wins, and the marker is still written.
    with running(FakePanel(bridge_dir, handler=lambda s: None)):
        with pytest.raises(bridge.Interrupted):
            with client.lease():
                client.send("1", write=True, timeout_s=1.0)
    assert calls == ["unknown_command_outcome", "unknown_command_outcome"]
    assert bridge.read_recovery(bridge_dir)["reason"] == "unknown_command_outcome"
    assert not (bridge_dir / LOCK).exists() and leftovers(bridge_dir) == [".host-recovery.json", HEARTBEAT_FILE]


def test_a_signal_during_acquire_gives_the_lease_back(bridge_dir, monkeypatch, keep_signals):  # noqa: F811
    bridge.install_signal_handlers()
    original = bridge._create_private

    def claim_then_signal(path, value):
        original(path, value)
        signal.raise_signal(signal.SIGHUP)      # arrives after the record is written, inside the section
    monkeypatch.setattr(bridge, "_create_private", claim_then_signal)
    lease = bridge.Lease(bridge_dir)
    with pytest.raises(bridge.Interrupted):
        lease.acquire()
    assert not lease.held and not (bridge_dir / LOCK).exists()
    assert bridge.read_recovery(bridge_dir) is None


def test_a_signal_during_the_reap_waits_for_its_end(bridge_dir, monkeypatch, keep_signals):  # noqa: F811
    bridge.install_signal_handlers()
    write_owner(bridge_dir, {"version": 1, "pid": dead_pid(), "token": "gone"})
    original = bridge.mark_recovery

    def mark_then_signal(folder, reason):
        original(folder, reason)
        signal.raise_signal(signal.SIGTERM)
    monkeypatch.setattr(bridge, "mark_recovery", mark_then_signal)
    lease = bridge.Lease(bridge_dir, wait_s=2.0)
    with pytest.raises(bridge.Interrupted):
        lease.acquire()
    assert not lease.held
    assert not (bridge_dir / LOCK).exists() and not (bridge_dir / ".host-owner-reaper").exists()
    assert bridge.read_recovery(bridge_dir)["reason"] == "abandoned_process_lease"


# ---------- final review fixes: A4 ----------
class Waiter:
    """Another program that wants the lease: it polls every 25 ms (as the server does), holds the lease
    for hold_s once it has it, and gives it back."""

    def __init__(self, bridge_dir, hold_s: float) -> None:  # noqa: F811
        self.lease = bridge.Lease(bridge_dir, wait_s=10.0)
        self.hold_s = hold_s
        self.got_at: float | None = None
        self.error: BaseException | None = None
        self.polling = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        try:
            self.polling.set()
            self.lease.acquire()
            self.got_at = time.monotonic()
            time.sleep(self.hold_s)
            self.lease.release()
        except BaseException as exc:            # report it to the test
            self.error = exc

    def __enter__(self) -> "Waiter":
        self.thread.start()
        self.polling.wait(5)
        time.sleep(0.1)                         # it is in its 25 ms poll loop now
        return self

    def __exit__(self, *exc) -> None:
        self.thread.join(timeout=15)


def test_breathe_gives_a_waiting_program_a_turn(bridge_dir, monkeypatch):  # noqa: F811
    assert bridge.LEASE_POLL_S == 0.025
    client = bridge.BridgeClient(bridge_dir)
    monkeypatch.setattr(bridge, "MAX_HOLD_S", 0.0)
    with client.lease():
        with Waiter(bridge_dir, hold_s=0.05) as waiter:
            assert waiter.got_at is None            # it waits while this client holds the lease
            start = time.monotonic()
            assert client.breathe() is True
            end = time.monotonic()
            assert client.lease().held
        assert waiter.error is None
        assert waiter.got_at is not None and start <= waiter.got_at <= end
    assert not (bridge_dir / LOCK).exists()


def test_breathe_raises_lease_busy_and_holds_nothing_when_the_other_keeps_it(bridge_dir, monkeypatch):  # noqa: F811
    client = bridge.BridgeClient(bridge_dir, lease_wait_s=0.2)
    monkeypatch.setattr(bridge, "MAX_HOLD_S", 0.0)
    client.lease().acquire()
    with Waiter(bridge_dir, hold_s=1.0) as waiter:
        with pytest.raises(CliError) as e:
            client.breathe()
        assert e.value.code == "lease_busy"
        assert not client.lease().held
    assert waiter.error is None and waiter.got_at is not None
    assert not (bridge_dir / LOCK).exists() and bridge.read_recovery(bridge_dir) is None


def test_a_failing_cleanup_does_not_lose_the_signal(keep_signals):  # noqa: F811
    bridge.install_signal_handlers()

    def cleanup():
        raise CliError("lease_lost", "The lease changed while this program held it.")
    with pytest.raises(bridge.Interrupted) as e:
        with bridge.hold_signals(on_signal=cleanup):
            signal.raise_signal(signal.SIGTERM)
    assert isinstance(e.value.__context__, CliError)
    with bridge.hold_signals() as section:          # nothing is left over for a later section
        assert not section.signalled


# ---------- plan 2: a marker that cannot be written keeps the lock (M1.1) ----------
def _cannot_mark(monkeypatch) -> list[str]:
    calls: list[str] = []

    def cannot_write(folder, reason):
        calls.append(reason)
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(bridge, "mark_recovery", cannot_write)
    return calls


def test_an_unknown_outcome_without_a_marker_keeps_the_lock(bridge_dir, monkeypatch):
    calls = _cannot_mark(monkeypatch)
    client = bridge.BridgeClient(bridge_dir)
    lock = bridge_dir / LOCK
    with running(FakePanel(bridge_dir, handler=lambda s: None)):
        err = send_error(client, write=True, timeout_s=0.3)
    assert calls == ["unknown_command_outcome"]
    assert err.code == "unknown_outcome" and err.message.startswith(bridge.DIALOG)
    assert err.message.endswith(f" The recovery marker could not be written, so the lock is kept. "
                                f"Delete {lock} after you checked Premiere.")
    lease = client.lease()
    assert lease.retained and not lease.held
    assert lock.is_dir() and bridge.read_owner(lock / "owner.json")[0] == os.getpid()
    lease.release()                                  # does nothing
    assert lock.is_dir() and (lock / "owner.json").is_file()
    assert leftovers(bridge_dir) == [LOCK, HEARTBEAT_FILE]
    with pytest.raises(RuntimeError):
        client.send("1", write=False)


def test_a_bad_write_reply_without_a_marker_keeps_the_lock(bridge_dir, monkeypatch):
    _cannot_mark(monkeypatch)
    client = bridge.BridgeClient(bridge_dir)
    lock = bridge_dir / LOCK
    with running(FakePanel(bridge_dir, handler=lambda s: "{")):
        err = send_error(client, write=True)
    assert err.code == "bad_reply" and "so the lock is kept" in err.message
    assert client.lease().retained and lock.is_dir() and (lock / "owner.json").is_file()


def test_a_written_marker_still_frees_the_lock(bridge_dir):
    client = bridge.BridgeClient(bridge_dir)
    with running(FakePanel(bridge_dir, handler=lambda s: None)):
        err = send_error(client, write=True, timeout_s=0.3)
    assert err.code == "unknown_outcome" and "lock is kept" not in err.message
    assert not client.lease().retained and not (bridge_dir / LOCK).exists()


# ---------- final fix A3: a marker that cannot be written is a CliError ----------
def test_release_with_a_changed_owner_and_no_marker_is_a_cli_error_that_keeps_the_lock(bridge_dir, monkeypatch):
    """N7.1: the owner record changed, and the marker cannot be written. The error is lease_lost with a plain
    message (no OSError leaves release), the lease counts as retained, and the lock stays where it is."""
    lease = bridge.Lease(bridge_dir)
    lease.acquire()
    owner = bridge_dir / LOCK / "owner.json"
    owner.write_text(json.dumps({"version": 1, "pid": os.getpid(), "token": "someone-else"}))
    calls = _cannot_mark(monkeypatch)
    with pytest.raises(CliError) as e:
        lease.release()
    assert calls == ["unknown_command_outcome"]
    assert e.value.code == "lease_lost"
    assert "recovery marker could not be written" in e.value.message and "Check Premiere" in e.value.message
    assert lease.retained and not lease.held
    assert owner.exists() and bridge.read_recovery(bridge_dir) is None
    lease.release()                                  # does nothing now
    assert owner.exists()


def test_a_dead_owner_without_a_marker_is_a_cli_error_and_is_not_reaped(bridge_dir, monkeypatch):
    """N2.2: the lock of a dead process is cleared only after the marker is written. When the marker cannot be
    written, acquire raises a CliError with a plain message, and the dead owner's lock stays."""
    write_owner(bridge_dir, {"version": 1, "pid": dead_pid(), "token": "gone"})
    calls = _cannot_mark(monkeypatch)
    lease = bridge.Lease(bridge_dir, wait_s=2.0)
    with pytest.raises(CliError) as e:
        lease.acquire()
    assert calls == ["abandoned_process_lease"]
    assert e.value.code == "lease_busy"
    assert "recovery marker could not be written" in e.value.message and "Nothing was sent." in e.value.message
    assert not lease.held
    assert json.loads((bridge_dir / LOCK / "owner.json").read_text())["token"] == "gone"
    assert not (bridge_dir / ".host-owner-reaper").exists()
