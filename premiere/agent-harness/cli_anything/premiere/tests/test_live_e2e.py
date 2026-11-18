"""End-to-end tests of the installed command on a RUNNING Adobe Premiere Pro 27.

These tests are opt-in. They talk to the owner's Premiere through the panel's bridge folder and
click its Window > Mode menu, so they run only when ALL of this is true:
  - the environment variable PREMIERE_LIVE_E2E is 1;
  - the MCP Bridge panel has a fresh heartbeat;
  - `live state` shows exactly one open project, named "fixture27 Claude.prproj".
Otherwise the whole module is skipped. The variable is checked first, before any contact with the
bridge folder, the panel or the command.

Run:  PREMIERE_LIVE_E2E=1 python3 -m pytest cli_anything/premiere/tests/test_live_e2e.py -q

Every command is run as a subprocess of the installed command with --json, with a time limit.
A command that does not finish in time gets SIGTERM first (the command then gives the bridge lease
back), and the test fails with a message instead of hanging.

The last test checks the bridge folder after everything else has run, so it must stay last in the file.
"""
from __future__ import annotations

import functools
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

import pytest

FIXTURE_NAME = "fixture27 Claude.prproj"
SKIP_REASON = ("Premiere 27 with fixture27 Claude.prproj open and PREMIERE_LIVE_E2E=1 are needed "
               "(see docs/2026-11-18-premiere-cli-plan-1-frame.md, Task 8)")
CLI_TIMEOUT_S = 120
MCP_TIMEOUT_S = 180         # the premiere-pro-mcp server has to start before it answers
TERM_GRACE_S = 20           # after SIGTERM, the command gets this long to give the lease back

# The opt-in switch comes first: with it unset nothing below touches the bridge, the panel or the command.
if os.environ.get("PREMIERE_LIVE_E2E") != "1":
    pytest.skip(SKIP_REASON, allow_module_level=True)

from cli_anything.premiere.core import bridge  # noqa: E402  (only needed once the switch is on)


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
    """Like _exec, but a timeout fails the test with a clear message."""
    try:
        return _exec(list(args), timeout)
    except CliTimeout as exc:
        pytest.fail(f"{exc}. A dialog may be open in Premiere: click it, then run the tests again.", pytrace=False)


def _ok(run: Run) -> dict:
    """The JSON object of a command that exited 0 with "ok": true."""
    assert run.code == 0 and run.data is not None and run.data.get("ok") is True, run.describe()
    return run.data


def _failed(run: Run) -> dict:
    """The JSON object of a command that exited 1 with "ok": false."""
    assert run.code == 1 and run.data is not None and run.data.get("ok") is False, run.describe()
    return run.data


def _gate() -> dict:
    """The `live state` of the running Premiere, or a skip when this is not the expected setup."""
    heartbeat = bridge.read_heartbeat(bridge.default_bridge_dir())
    if heartbeat is None or not heartbeat.fresh:
        pytest.skip(f"{SKIP_REASON}; the panel heartbeat is not fresh", allow_module_level=True)
    try:
        run = _exec(["live", "state"], CLI_TIMEOUT_S)
    except CliTimeout:
        pytest.skip(f"{SKIP_REASON}; `live state` did not answer in {CLI_TIMEOUT_S} s", allow_module_level=True)
    if run.code != 0 or run.data is None or run.data.get("ok") is not True:
        why = f"{run.data.get('code')}: {run.data.get('error')}" if run.data else run.stderr.strip()[-200:]
        pytest.skip(f"{SKIP_REASON}; `live state` failed (exit status {run.code}, {why})", allow_module_level=True)
    projects = run.data.get("projects")
    if not isinstance(projects, list) or len(projects) != 1 or projects[0].get("name") != FIXTURE_NAME:
        names = [p.get("name") for p in projects] if isinstance(projects, list) else projects
        pytest.skip(f"{SKIP_REASON}; open projects: {names}", allow_module_level=True)
    return run.data


STATE0 = _gate()                        # `live state` as it was when the module was collected
BRIDGE_DIR = bridge.default_bridge_dir()


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
    script = tmp_path / "throws.jsx"
    script.write_text('throw new Error("x");\n', encoding="utf-8")
    data = _failed(run_cli("live", "run", str(script)))
    assert data["code"] == "script_failed", data
    # A script that throws is a read that failed cleanly: it must not leave a recovery marker.
    assert not (BRIDGE_DIR / ".host-recovery.json").exists()


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
    try:
        changed = _ok(run_cli("ui", "mode", "set", other))
        assert changed["mode"] == other, changed
        back = _ok(run_cli("ui", "mode", "set", m0))
        assert back["mode"] == m0, back
        assert _ok(run_cli("ui", "mode", "get"))["mode"] == m0
    finally:
        _put_mode_back(m0)              # also when an assertion above failed


def _leftovers() -> list[str]:
    """Names in the bridge folder that a finished command must not leave: its files and the lease."""
    names = sorted(p.name for p in BRIDGE_DIR.iterdir())
    return [n for n in names if n.startswith(("cmd_", "res_", "busy_")) or n == ".host-owner-lease"]


# Keep this test LAST: pytest runs the file in order, and it looks at what all the tests above left.
def test_bridge_folder_is_clean_after_the_run():
    left = _leftovers()
    deadline = time.monotonic() + 3         # a little time for a child process that is still leaving
    while left and time.monotonic() < deadline:
        time.sleep(0.25)
        left = _leftovers()
    assert not left, f"left in {BRIDGE_DIR}: {left}"
