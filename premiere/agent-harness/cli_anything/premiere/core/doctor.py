"""The health check behind `doctor` and the read behind `recovery show`. Both only look.

Neither ever reaps a dead lease or writes a recovery marker: they read the lease owner record
themselves, and take the lease only when it is free (waiting at most LEASE_BUSY_WAIT_S for a live
holder to give it back). They need a client built with reap=False, so even a holder that dies between
the look and the acquire counts as busy. The one script they may send is a read.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Callable

from cli_anything.premiere.core import bridge, guard, jsx, ui
from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.errors import CliError

LEASE_BUSY_WAIT_S = 3.0      # longest wait for a live holder to give the lease back
RECORD_WAIT_S = 1.0          # longest wait for owner.json in a lock folder that has none yet
POLL_S = 0.05
READ_TIMEOUT_S = 10.0        # the state read: after this, wait on only while the panel says it still runs
READ_HARD_LIMIT_S = 30.0
MCP_SERVER = "premiere-pro-mcp"
RECOVERY_HINT = "run: cli-anything-premiere recovery show"
NEXT_OPEN = ("Check the state above. Then inspect and acknowledge with the MCP tools "
             "inspect_operation_recovery and acknowledge_operation_recovery.")
NEXT_NONE = "No recovery is open."
STATE_CHECKS = ("premiere_version", "projects", "project_guard")


def _check(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


# ---------- lease, read without touching ----------
def _owner_once(bridge_dir: Path) -> tuple[str, int | None]:
    """One look at the lease: ("free"|"live"|"dead"|"invalid"|"missing_record", pid)."""
    lock = Path(bridge_dir) / bridge.LOCK_DIR
    if not os.path.lexists(lock):
        return "free", None
    try:
        pid, _token = bridge.read_owner(lock / bridge.OWNER_FILE)
    except (OSError, CliError):          # absent, being written, or not a valid record
        return "missing_record", None
    return ("live" if bridge.pid_alive(pid) else "dead"), pid


def lease_holder(bridge_dir: Path) -> tuple[str, int | None]:
    """The lease as ("free"|"live"|"dead"|"invalid", pid). Waits up to RECORD_WAIT_S for a record."""
    deadline = time.monotonic() + RECORD_WAIT_S
    while True:
        status, pid = _owner_once(bridge_dir)
        if status != "missing_record":
            return status, pid
        if time.monotonic() >= deadline:
            return "invalid", None
        time.sleep(POLL_S)


def _wait_while_live(bridge_dir: Path) -> tuple[str, int | None, int | None]:
    """(final status, final pid, pid of the live holder first seen). Waits up to LEASE_BUSY_WAIT_S."""
    status, pid = lease_holder(bridge_dir)
    first_live = pid if status == "live" else None
    deadline = time.monotonic() + LEASE_BUSY_WAIT_S
    while status == "live" and time.monotonic() < deadline:
        time.sleep(POLL_S)
        status, pid = lease_holder(bridge_dir)
    return status, pid, first_live


SKIP_REASONS = {"live": "not checked, lease busy",
                "dead": "not checked, lease held by a dead process",
                "invalid": "not checked, lease record invalid"}


def _require_look_only(client: BridgeClient) -> None:
    if client.lease().reap:
        raise RuntimeError("doctor and recovery show need a client built with reap=False.")


def _read(client: BridgeClient, script: str, lease: str) -> tuple[object, str | None]:
    """Run one read script under the lease when `lease` is "free". (data, None) or (None, why not)."""
    if lease != "free":
        return None, SKIP_REASONS[lease]
    try:
        with client.lease():
            data = client.send(jsx.build(jsx.load(script)), write=False,
                               timeout_s=READ_TIMEOUT_S, hard_limit_s=READ_HARD_LIMIT_S)
    except CliError as err:
        if err.code == "interrupted":
            raise                    # stopped by a signal: the command stops, it does not go on
        return None, f"not checked: {err.message}"
    except OSError as err:           # Interrupted and KeyboardInterrupt pass through
        return None, f"not checked: {err}"
    return data, None


# ---------- the state checks ----------
def _state_checks(state: object) -> list[dict]:
    if not isinstance(state, dict):
        return [_check(n, "warn", "not checked: the state script did not return an object.") for n in STATE_CHECKS]
    out = []
    version = state.get("version")
    shown = version if isinstance(version, str) and version else "unknown"
    if isinstance(version, str) and version.startswith(guard.TESTED_PREMIERE_PREFIXES):
        out.append(_check("premiere_version", "pass", f"Premiere {shown}"))
    else:
        out.append(_check("premiere_version", "warn", f"Premiere {shown} is untested: read only."))
    projects = state.get("projects") if isinstance(state.get("projects"), list) else []
    names = [str(p.get("name") or "") if isinstance(p, dict) else "?" for p in projects]
    if len(projects) == 1:
        out.append(_check("projects", "pass", f"one project open: {names[0]}"))
    elif not projects:
        out.append(_check("projects", "warn", "no project is open."))
    else:
        out.append(_check("projects", "warn", f"{len(projects)} projects are open: {', '.join(names)}."))
    active = guard.active_project(projects)
    if active is None:
        out.append(_check("project_guard", "warn", "no active project: writes need --any-project and --project."))
    else:
        name = guard.project_file_name(active)
        if "claude" in name.lower():
            out.append(_check("project_guard", "pass", f"{name} is a Claude working copy."))
        else:
            out.append(_check("project_guard", "warn", f"{name} is not a Claude working copy: "
                                                       "writes need --any-project and --project."))
    return out


# ---------- run_checks ----------
def run_checks(client: BridgeClient, *, premiere: Callable[[], dict | None] = ui.running_premiere,
               panel: Callable[[], str | None] = guard.panel_version,
               which: Callable[[str], str | None] = shutil.which,
               server: Callable[[], str | None] = guard.server_version,
               osascript: Callable[[str], str] = ui.run_osascript,
               recipes_error: str | None = None) -> list[dict]:
    """All thirteen checks, in a fixed order. Never changes anything. The only CliError it raises is
    "interrupted", when a signal stops the state read; Interrupted and KeyboardInterrupt pass through.

    `recipes_error` is live_cli.RECIPES_ERROR: why the recipe commands did not load, or None.

    `client` must be built with reap=False (RuntimeError otherwise)."""
    _require_look_only(client)
    found: dict[str, dict] = {}

    # premiere_running
    app = None
    try:
        app = premiere()
    except CliError as err:
        found["premiere_running"] = _check("premiere_running", "fail", err.message)
    else:
        if app is None:
            found["premiere_running"] = _check("premiere_running", "fail", "Premiere Pro is not running.")
        else:
            found["premiere_running"] = _check("premiere_running", "pass",
                                               f"{app['process_name']} (pid {app['pid']})")

    # bridge_folder
    bridge_dir = client.bridge_dir
    try:
        bridge.check_private_dir(bridge_dir)
        bridge_ok = True
        found["bridge_folder"] = _check("bridge_folder", "pass", str(bridge_dir))
    except CliError as err:
        bridge_ok = False
        found["bridge_folder"] = _check("bridge_folder", "fail", err.message)

    # panel_version (needs no bridge)
    version = panel()
    heartbeat = bridge.read_heartbeat(bridge_dir) if bridge_ok else None
    if version in guard.TESTED_PANEL_VERSIONS:
        found["panel_version"] = _check("panel_version", "pass", f"panel {version}")
    elif version is None:
        found["panel_version"] = _check("panel_version", "warn", "the panel manifest was not found.")
    else:
        found["panel_version"] = _check("panel_version", "warn", f"panel {version} is untested. Tested: "
                                                                 f"{', '.join(guard.TESTED_PANEL_VERSIONS)}.")

    state_skip: str | None = None
    if not bridge_ok:
        found["panel_heartbeat"] = _check("panel_heartbeat", "fail", "not checked, the bridge folder failed.")
        for name in ("bridge_protocol", "lease", "recovery"):
            found[name] = _check(name, "warn", "not checked, the bridge folder failed.")
        state_skip = "not checked, the bridge folder failed."
    else:
        # panel_heartbeat: the fix sentence comes from the guard itself
        if heartbeat is not None and heartbeat.fresh:
            found["panel_heartbeat"] = _check("panel_heartbeat", "pass", f"fresh, {heartbeat.age_s:.1f} s old")
        else:
            try:
                guard.check_bridge(heartbeat, version, write=False, allow_untested=True)
                fix = ""
            except CliError as err:
                fix = err.message
            if heartbeat is None:
                what = "absent"
            elif heartbeat.waiting:
                what = f"waiting ({heartbeat.age_s:.1f} s old)"
            else:
                what = f"stale ({heartbeat.age_s:.1f} s old, state {heartbeat.state})"
            found["panel_heartbeat"] = _check("panel_heartbeat", "fail", f"{what}. {fix}".strip())
            state_skip = "not checked, the panel heartbeat failed."

        # bridge_protocol
        if heartbeat is None:
            found["bridge_protocol"] = _check("bridge_protocol", "warn", "not checked, no heartbeat.")
        else:
            versions = (f"protocol {heartbeat.protocol_version}, "
                        f"command schema {heartbeat.command_schema_version}")
            same = (heartbeat.protocol_version == guard.PROTOCOL_VERSION
                    and heartbeat.command_schema_version == guard.COMMAND_SCHEMA_VERSION)
            found["bridge_protocol"] = _check("bridge_protocol", "pass" if same else "warn",
                                              versions if same else f"{versions} is untested.")

        # recovery
        marker = bridge.read_recovery(bridge_dir)
        if marker is None:
            found["recovery"] = _check("recovery", "pass", "none")
        elif marker["reason"] == "unreadable":
            found["recovery"] = _check("recovery", "fail", f"a recovery is open; its marker cannot be read. "
                                                           f"{RECOVERY_HINT}")
        else:
            found["recovery"] = _check("recovery", "fail", f"a recovery is open ({marker['reason']}). {RECOVERY_HINT}")

        # lease (never taken while it is held; a dead holder is not reaped)
        status, pid, first_live = _wait_while_live(bridge_dir)
        if status == "free":
            note = f" (pid {first_live} gave it back)" if first_live is not None else ""
            found["lease"] = _check("lease", "pass", f"free{note}")
        elif status == "live":
            found["lease"] = _check("lease", "warn", f"held by pid {pid}, still held after {LEASE_BUSY_WAIT_S:g} s.")
        elif status == "dead":
            found["lease"] = _check("lease", "fail", f"held by pid {pid}, which is not running. "
                                                     "Another program must clear it; this check changes nothing.")
        else:
            found["lease"] = _check("lease", "fail", "the lease folder has no valid owner record, so every program "
                                                     "waits and none clears it. Make sure no CLI command and no "
                                                     "premiere-pro-mcp server runs, then delete the folder "
                                                     f"{Path(bridge_dir) / bridge.LOCK_DIR} by hand.")

        if state_skip is None:
            state, why = _read(client, "state", status)
            if why is None:
                for entry in _state_checks(state):
                    found[entry["name"]] = entry
            else:
                state_skip = why

    if state_skip is not None:
        for name in STATE_CHECKS:
            found[name] = _check(name, "warn", state_skip)

    # mcp_server
    if not which(MCP_SERVER):
        found["mcp_server"] = _check("mcp_server", "warn", f"{MCP_SERVER} is not on PATH: the mcp commands will not work.")
    else:
        version = server()
        if version in guard.TESTED_SERVER_VERSIONS:
            found["mcp_server"] = _check("mcp_server", "pass", f"{MCP_SERVER} {version}")
        elif version is None:
            found["mcp_server"] = _check("mcp_server", "warn", f"{MCP_SERVER} is on PATH but its version cannot be "
                                                               "read, so it is untested: read only.")
        else:
            found["mcp_server"] = _check("mcp_server", "warn", f"{MCP_SERVER} {version} is untested: read only.")

    # accessibility
    if app is None:
        found["accessibility"] = _check("accessibility", "warn", "not checked, Premiere is not running")
    else:
        try:
            mode = ui.mode_get(app["process_name"], run=osascript)
            found["accessibility"] = _check("accessibility", "pass", f"Window > Mode reads {mode}.")
        except CliError as err:
            found["accessibility"] = _check("accessibility", "fail" if err.code == "no_accessibility" else "warn",
                                            err.message)

    # recipes
    if recipes_error is None:
        found["recipes"] = _check("recipes", "pass", "the recipe commands are loaded")
    else:
        found["recipes"] = _check("recipes", "fail", recipes_error)

    order = ("premiere_running", "bridge_folder", "panel_heartbeat", "bridge_protocol", "panel_version", "lease",
             "recovery", *STATE_CHECKS, "mcp_server", "accessibility", "recipes")
    return [found[name] for name in order]


def summarize(checks: list[dict]) -> dict:
    return {s: sum(c["status"] == s for c in checks) for s in ("pass", "warn", "fail")}


# ---------- recovery_view ----------
def recovery_view(client: BridgeClient) -> dict:
    """The open recovery, if any, and the summary state when it can be read without touching the lease.

    `client` must be built with reap=False (RuntimeError otherwise)."""
    _require_look_only(client)
    marker = bridge.read_recovery(client.bridge_dir)
    state = None
    heartbeat = bridge.read_heartbeat(client.bridge_dir)
    if heartbeat is not None and heartbeat.fresh:
        status, _pid, _first = _wait_while_live(client.bridge_dir)
        data, why = _read(client, "summary", status)
        state = data if why is None and isinstance(data, dict) else None
    return {"recovery_required": marker is not None,
            "reason": marker["reason"] if marker else None,
            "revision": marker["revision"] if marker else None,
            "state": state,
            "next": NEXT_OPEN if marker else NEXT_NONE}
