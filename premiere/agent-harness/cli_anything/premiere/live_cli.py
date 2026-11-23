"""Commands that talk to the running Premiere through the panel's bridge folder.

premiere_cli.py registers these groups lazily, so the reader commands never import this module.
Every live command takes the bridge lease once and does all its work inside it.
"""
from __future__ import annotations

import functools
import json
import shutil
from pathlib import Path

import click

from cli_anything.premiere.core import bridge, guard
from cli_anything.premiere.core import doctor as doctor_core   # `doctor` below is the click group
from cli_anything.premiere.core import ui as ui_core      # `ui` below is the click group
from cli_anything.premiere.core import ui_mask as ui_mask_core
from cli_anything.premiere.core.bridge import BridgeClient, install_signal_handlers, restore_signal_handlers
from cli_anything.premiere.core.mcp_client import McpSession
# guarded_state and run_script live in core/session.py; these names stay importable from here.
from cli_anything.premiere.core.session import (DEFAULT_HARD_LIMIT_S, SOFT_TIMEOUT_S, guarded_state,  # noqa: F401
                                                run_script)
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.premiere_cli import _echo_json, _echo_text, _emit, _fail, _json, json_option

DEFAULT_LEASE_WAIT_S = 30.0


STOPPED_LOOK = "Stopped by Ctrl-C or a stop signal. Nothing was changed."
STOPPED_RUN = "Stopped by Ctrl-C or a stop signal. Check Premiere before another edit."


def stops_cleanly(message: str):
    """Decorate a live command: install the signal handlers for its run, and report an interrupt that
    reaches the command as {"ok": false, "code": "interrupted"} with exit 1. The old handlers come back
    when the command ends. `message` is STOPPED_LOOK for commands that only look, else STOPPED_RUN."""
    def decorate(func):
        @functools.wraps(func)
        def command(*args, **kwargs):
            ctx = click.get_current_context()
            previous = install_signal_handlers()
            try:
                return func(*args, **kwargs)
            except (KeyboardInterrupt, bridge.Interrupted):
                _fail(ctx, CliError("interrupted", message))
            finally:
                restore_signal_handlers(previous)
        return command
    return decorate


def make_client(lease_wait: float, *, reap: bool = True) -> BridgeClient:
    """A client for the real bridge folder. Tests replace this to point at a temp folder.

    reap=False: a lease held by a dead process counts as busy (for the commands that only look)."""
    return BridgeClient(lease_wait_s=lease_wait, reap=reap)


def live_options(func):
    """The options every live command shares."""
    func = click.option("--allow-untested", is_flag=True,
                        help="Go on with a Premiere, panel or MCP server version this CLI was not tested with.")(func)
    func = click.option("--timeout", "timeout", type=click.FloatRange(min=0, min_open=True),
                        default=DEFAULT_HARD_LIMIT_S, show_default=True, metavar="SECONDS",
                        help="Longest time to wait for one script while Premiere still works on it.")(func)
    func = click.option("--lease-wait", "lease_wait", type=click.FloatRange(min=0),
                        default=DEFAULT_LEASE_WAIT_S, show_default=True, metavar="SECONDS",
                        help="Longest time to wait for another program to give Premiere back.")(func)
    return func


def _client(lease_wait: float, *, reap: bool = True) -> BridgeClient:
    client = make_client(lease_wait, reap=reap)
    bridge.check_private_dir(client.bridge_dir)
    return client


def _warning_lines(warnings: list[str]) -> list[str]:
    return [f"Warning: {w}" for w in warnings]


def _read_script(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CliError("script_unreadable", f"Cannot read the script {path}: {exc}. Nothing was sent.") from None


def _parse_args(text: str | None) -> dict | None:
    if text is None:
        return None
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise CliError("bad_args", f"--args is not valid JSON: {exc}. Nothing was sent.") from None
    if not isinstance(value, dict):
        raise CliError("bad_args", "--args must be a JSON object, such as '{\"name\": \"x\"}'. Nothing was sent.")
    return value


@click.group()
def live() -> None:
    """Read and change the running Premiere 27 with scripts."""


@live.command("state")
@live_options
@json_option
@click.pass_context
@stops_cleanly(STOPPED_LOOK)
def live_state(ctx: click.Context, lease_wait: float, timeout: float, allow_untested: bool) -> None:
    """Show the Premiere version, the open projects, the active sequence and the selection."""
    try:
        client = _client(lease_wait)
        with client.lease():
            state, warnings = guarded_state(client, write=False, project=None, any_project=False,
                                            allow_untested=allow_untested, hard_limit_s=timeout)
    except CliError as err:
        _fail(ctx, err)
        return
    lines = [f"Premiere {state.get('version') or 'unknown'} (build {state.get('build') or 'unknown'})"]
    projects = state.get("projects") or []
    if projects:
        lines.append("Open projects (* = active):")
        for p in projects:
            lines.append(f"  {'*' if p.get('active') else ' '} {p.get('name')}  {p.get('path') or ''}".rstrip())
    else:
        lines.append("No project is open.")
    seq = state.get("active_sequence")
    lines.append(f"Active sequence: {seq.get('name')} ({seq.get('id')})" if isinstance(seq, dict) else "Active sequence: none")
    lines.append(f"Selected clips: {len(state.get('selection') or [])}")
    lines += _warning_lines(warnings)
    _emit(ctx, {**state, "warnings": warnings}, "\n".join(lines))


@live.command("run")
@click.argument("script", type=click.Path(path_type=Path))
@click.option("--args", "args_json", metavar="JSON", help="Arguments for the script, as a JSON object. The script reads them as ARGS.")
@click.option("--write", is_flag=True, help="Say that the script changes the project. Runs the write checks first.")
@click.option("--verify", "verify_path", type=click.Path(path_type=Path), metavar="CHECK.jsx",
              help="A read script run after SCRIPT, with the same ARGS. It must return an object with a "
                   "boolean 'stored'.")
@click.option("--project", help="Path of the project that must be the active one.")
@click.option("--any-project", is_flag=True, help="Allow a write to a project that is not a Claude working copy. Needs --project.")
@live_options
@json_option
@click.pass_context
@stops_cleanly(STOPPED_RUN)
def live_run(ctx: click.Context, script: Path, args_json: str | None, write: bool, verify_path: Path | None,
             project: str | None, any_project: bool, lease_wait: float, timeout: float, allow_untested: bool) -> None:
    """Run SCRIPT.jsx (a body that ends with `return __ok(...)`) in Premiere and print its data.

    Without --write, no write check runs, and a script that hangs leaves no recovery marker. The CLI
    cannot tell whether a script changes the project: --write is your statement that it does.
    """
    soft = min(SOFT_TIMEOUT_S, timeout)
    try:
        args = _parse_args(args_json)
        body = _read_script(script)
        check = _read_script(verify_path) if verify_path is not None else None
        client = _client(lease_wait)
        stored = None
        with client.lease():
            _state, warnings = guarded_state(client, write=write, project=project, any_project=any_project,
                                             allow_untested=allow_untested, hard_limit_s=timeout)
            data = run_script(client, body, args, write=write, timeout_s=soft, hard_limit_s=timeout)
            if check is not None:
                try:
                    result = run_script(client, check, args, write=False, timeout_s=soft, hard_limit_s=timeout)
                except CliError as err:
                    raise CliError(err.code, f"SCRIPT ran; the check script failed: {err.message}") from None
                if not isinstance(result, dict) or not isinstance(result.get("stored"), bool):
                    raise CliError("bad_verify", "SCRIPT ran; the check script must return an object with a boolean 'stored'.")
                stored = result["stored"]
    except CliError as err:
        _fail(ctx, err)
        return
    payload = {"data": data, "stored": stored, "rendered": None, "warnings": warnings}
    if stored is False:
        message = "The check script says the change was not stored."
        if _json(ctx):
            _echo_json({"ok": False, "error": message, "code": "not_stored", **payload}, indent=2)
        else:
            _echo_text(f"Error: {message}", err=True)
            for line in _warning_lines(warnings):
                _echo_text(line, err=True)
        ctx.exit(1)
    lines = [json.dumps(data, indent=2, ensure_ascii=False)]
    lines.append("Stored: " + ("not checked" if stored is None else "yes"))
    lines += _warning_lines(warnings)
    _emit(ctx, payload, "\n".join(lines))


MCP_CALL_TIMEOUT_S = 120.0
MCP_DESCRIPTION_CHARS = 100


def make_session(client: BridgeClient) -> McpSession:
    """A session on the real premiere-pro-mcp server. Tests replace this to use a fake server."""
    return McpSession(client)


def _mcp_lease_wait_option(func):
    return click.option("--lease-wait", "lease_wait", type=click.FloatRange(min=0),
                        default=DEFAULT_LEASE_WAIT_S, show_default=True, metavar="SECONDS",
                        help="Longest time to wait for another program to give Premiere back before the server starts.")(func)


def _mcp_timeout_option(func):
    return click.option("--timeout", "timeout", type=click.FloatRange(min=0, min_open=True),
                        default=MCP_CALL_TIMEOUT_S, show_default=True, metavar="SECONDS",
                        help="Longest time to wait for the server to answer one call. Raise it for exports. "
                             "When it runs out, the CLI stops the server, even in the middle of its tool, and "
                             "the next program that talks to Premiere may find an open recovery.")(func)


def _open_session(lease_wait: float) -> McpSession:
    """A session for one command, not started yet. `with` starts it and closes it."""
    return make_session(_client(lease_wait))


def _result_value(reply: dict) -> object:
    """What a tool returned: its structured data when it has some, else its text."""
    return reply["structured"] if reply["structured"] is not None else reply["text"]


def _show(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)


def _first_line(text: str) -> str:
    lines = text.strip().splitlines()
    line = lines[0].strip() if lines else ""
    return line if len(line) <= MCP_DESCRIPTION_CHARS else line[:MCP_DESCRIPTION_CHARS - 3] + "..."


def _read_batch(path: Path) -> list[tuple[str, dict]]:
    """The calls of a batch file as (tool, args). Raises bad_batch before anything is started."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CliError("bad_batch", f"Cannot read the batch file {path}: {exc}. Nothing was sent.") from None
    try:
        entries = json.loads(text)
    except ValueError as exc:
        raise CliError("bad_batch", f"The batch file is not valid JSON: {exc}. Nothing was sent.") from None
    if not isinstance(entries, list):
        raise CliError("bad_batch", 'The batch file must be a JSON array of calls, such as '
                                    '[{"tool": "ping", "args": {}}]. Nothing was sent.')
    calls = []
    for number, entry in enumerate(entries, 1):
        if not isinstance(entry, dict) or not isinstance(entry.get("tool"), str) or not entry["tool"] \
                or not isinstance(entry.get("args", {}), dict) or set(entry) - {"tool", "args"}:
            raise CliError("bad_batch", f'Entry {number} of the batch file must be an object with a text "tool" and '
                                        'an optional object "args", and no other keys. Nothing was sent.')
        calls.append((entry["tool"], entry.get("args", {})))
    return calls


@click.group()
def mcp() -> None:
    """Call any tool of the premiere-pro-mcp server.

    The write checks of `live run --write` do not run here. The server applies its own rules.
    """


@mcp.command("list")
@click.option("--query", metavar="TEXT", help="Keep only the tools whose name or description contains TEXT (any case).")
@_mcp_lease_wait_option
@json_option
@click.pass_context
@stops_cleanly(STOPPED_LOOK)
def mcp_list(ctx: click.Context, query: str | None, lease_wait: float) -> None:
    """List the tools of the server."""
    try:
        with _open_session(lease_wait) as session:
            tools = session.list_tools()
    except CliError as err:
        _fail(ctx, err)
        return
    if query:
        needle = query.lower()
        tools = [t for t in tools if needle in str(t.get("name") or "").lower()
                 or needle in str(t.get("description") or "").lower()]
    items = [{"name": t.get("name"), "description": t.get("description") or ""} for t in tools]
    lines = [f"{item['name']}  {_first_line(item['description'])}".rstrip() for item in items]
    lines.append(f"{len(items)} tool" + ("" if len(items) == 1 else "s"))
    _emit(ctx, {"tools": items, "count": len(items)}, "\n".join(lines))


@mcp.command("describe")
@click.argument("tool")
@_mcp_lease_wait_option
@json_option
@click.pass_context
@stops_cleanly(STOPPED_LOOK)
def mcp_describe(ctx: click.Context, tool: str, lease_wait: float) -> None:
    """Show what one tool does and what arguments it takes."""
    try:
        with _open_session(lease_wait) as session:
            tools = session.list_tools()
        found = next((t for t in tools if t.get("name") == tool), None)
        if found is None:
            raise CliError("tool_not_found", f"The server has no tool named {tool!r}. Run `mcp list` to see the tools.")
    except CliError as err:
        _fail(ctx, err)
        return
    description = found.get("description") or ""
    schema = found.get("inputSchema")
    human = f"{tool}\n\n{description}\n\nInput schema:\n{json.dumps(schema, indent=2, ensure_ascii=False)}"
    _emit(ctx, {"name": tool, "description": description, "input_schema": schema}, human)


@mcp.command("call")
@click.argument("tool")
@click.option("--args", "args_json", metavar="JSON", help="Arguments for the tool, as a JSON object.")
@_mcp_timeout_option
@_mcp_lease_wait_option
@json_option
@click.pass_context
@stops_cleanly(STOPPED_RUN)
def mcp_call(ctx: click.Context, tool: str, args_json: str | None, timeout: float, lease_wait: float) -> None:
    """Run one tool of the server and print what it returns.

    The write checks of `live run --write` do not run here. The server applies its own rules.
    """
    try:
        args = _parse_args(args_json) or {}
        with _open_session(lease_wait) as session:
            reply = session.call(tool, args, timeout_s=timeout)
    except CliError as err:
        _fail(ctx, err)
        return
    if reply["is_error"]:
        _fail(ctx, CliError("tool_failed", reply["text"] or "The tool reported an error."))
        return
    result = _result_value(reply)
    _emit(ctx, {"tool": tool, "result": result, "stored": None, "rendered": None}, _show(result))


@mcp.command("batch")
@click.argument("file", type=click.Path(path_type=Path))
@_mcp_timeout_option
@_mcp_lease_wait_option
@json_option
@click.pass_context
@stops_cleanly(STOPPED_RUN)
def mcp_batch(ctx: click.Context, file: Path, timeout: float, lease_wait: float) -> None:
    """Run the calls in FILE, one after another in one server session. Stops at the first failure.

    FILE is a JSON array such as [{"tool": "ping", "args": {}}]. The write checks of
    `live run --write` do not run here. The server applies its own rules.
    """
    try:
        calls = _read_batch(file)
    except CliError as err:
        _fail(ctx, err)
        return
    if not calls:                 # nothing to run, so no server is started
        _emit(ctx, {"results": [], "ran": 0, "total": 0}, "The batch file has no calls.")
        return
    results: list[dict] = []
    failure: tuple[str, str] | None = None      # (code, message)
    try:
        with _open_session(lease_wait) as session:
            for number, (tool, args) in enumerate(calls, 1):
                where = f"Call {number} of {len(calls)} ({tool})"
                try:
                    reply = session.call(tool, args, timeout_s=timeout)
                except CliError as err:
                    failure = (err.code, f"{where}: {err.message}")
                    break
                results.append({"tool": tool, "ok": not reply["is_error"], "result": _result_value(reply)})
                if reply["is_error"]:
                    failure = ("tool_failed", f"{where}: {reply['text'] or 'The tool reported an error.'}")
                    break
    except CliError as err:       # the server did not start
        failure = (err.code, err.message)
    summary = {"results": results, "ran": len(results), "total": len(calls)}
    if failure is None:
        lines = [f"{n}. {r['tool']}: {json.dumps(r['result'], ensure_ascii=False)}" for n, r in enumerate(results, 1)]
        _emit(ctx, summary, "\n".join(lines))
        return
    code, message = failure
    if _json(ctx):
        _echo_json({"ok": False, "error": message, "code": code, **summary}, indent=2)
    else:
        _echo_text(f"Error: {message}", err=True)
        _echo_text(f"Ran {summary['ran']} of {summary['total']} calls.", err=True)
    ctx.exit(1)


@click.group()
def ui() -> None:
    """Click Premiere menus (Window > Mode) and use the mask tools on the screen."""


@ui.group("mode")
def ui_mode() -> None:
    """Read and switch the Window > Mode menu (import, edit, color, export)."""


def _running_app() -> dict:
    """The running Premiere. The core functions are looked up on the module at each call, so tests can replace them."""
    app = ui_core.running_premiere()
    if app is None:
        raise CliError("premiere_not_running", "Premiere Pro is not running. Open it and try again.")
    return app


@ui_mode.command("get")
@json_option
@click.pass_context
def ui_mode_get(ctx: click.Context) -> None:
    """Show which mode is checked in Window > Mode. Changes nothing."""
    try:
        app = _running_app()
        mode = ui_core.mode_get(app["process_name"])
    except CliError as err:
        _fail(ctx, err)
        return
    _emit(ctx, {"mode": mode}, f"Mode: {mode}")


@ui_mode.command("set")
@click.argument("mode")
@json_option
@click.pass_context
def ui_mode_set(ctx: click.Context, mode: str) -> None:
    """Switch Premiere to MODE (import, edit, color or export) in Window > Mode.

    Clicks the menu item, then checks for 5 seconds that Premiere keeps the mode. If Premiere
    switches back, it clicks once more.
    """
    try:
        app = _running_app()
        result = ui_core.mode_set(app["process_name"], mode)
    except CliError as err:
        _fail(ctx, err)
        return
    human = f"Mode set to {result['mode']}."
    if result["reverted_once"]:
        human += " Premiere switched back once, so the menu item was clicked a second time."
    _emit(ctx, result, human)


@ui.group("mask")
def ui_mask() -> None:
    """Premiere 27 mask tools through the screen: Object Mask and tracking."""


def _checked_project(project: str, lease_wait: float, timeout: float, allow_untested: bool) -> None:
    """Pass the write guards: the project at PATH is the active one and is a Claude working copy."""
    client = _client(lease_wait)
    with client.lease():
        guarded_state(client, write=True, project=project, any_project=False, allow_untested=allow_untested, hard_limit_s=timeout)


@ui_mask.command("object")
@click.option("--project", required=True, help="Path of the project that must be the active one (a Claude working copy).")
@click.option("--frame", "frame_png", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="The frame the Program Monitor shows now (from `live frame export`). It is found on the screen.")
@click.option("--at", "at_text", required=True, help="Where to click, as two shares of the frame: x,y (0.5,0.5 is the centre).")
@click.option("--hover", "hover_s", type=float, default=3.0, show_default=True, help="Seconds to hover before the click.")
@live_options
@json_option
@click.pass_context
def ui_mask_object(ctx: click.Context, project: str, frame_png: Path, at_text: str, hover_s: float, lease_wait: float,
                   timeout: float, allow_untested: bool) -> None:
    """Make an Object Mask on the selected clip with one click in the Program Monitor.

    Before: select the clip, put the playhead on the frame, export that frame and give it as --frame. The command
    brings Premiere to the front, finds the frame on a screen capture, presses Cmd+Opt+O (Object Mask tool), hovers
    and clicks. Do not use the mouse or the keyboard while it runs.

    made: a mask data file (.prmf) in "<project name> Masks" is new or changed. It is not a check of the mask shape:
    Premiere picks the whole object under the pointer. When nothing changed, look at Premiere: the first mask of a
    project opens a dialog that only you may click. The project is not saved.
    """
    try:
        parts = [float(v) for v in at_text.split(",")]
        if len(parts) != 2:
            raise ValueError
    except ValueError:
        _fail(ctx, CliError("bad_point", "Give --at as two numbers: x,y."))
        return
    try:
        _checked_project(project, lease_wait, timeout, allow_untested)
        app = _running_app()
        result = ui_mask_core.object_mask(app["process_name"], Path(project), frame_png, (parts[0], parts[1]), hover_s=hover_s)
    except CliError as err:
        _fail(ctx, err)
        return
    human = (f"Clicked at {result['clicked_points']} (frame match {result['picture']['score']}). "
             + (f"Mask data changed: {', '.join(result['new_or_changed_files'])}." if result["made"]
                else "No mask data changed. Look at Premiere: a dialog may be open."))
    _emit(ctx, result, human)
    if not result["made"]:
        ctx.exit(1)


@ui_mask.command("track")
@click.option("--project", required=True, help="Path of the project that must be the active one (a Claude working copy).")
@click.option("--direction", type=click.Choice(list(ui_mask_core.TRACKER_BUTTONS)), default="both", show_default=True,
              help="Which Tracker button to click.")
@click.option("--limit", "limit_s", type=float, default=300.0, show_default=True, help="Longest wait for the tracking, in seconds.")
@click.option("--by", type=click.Choice(["key", "button"]), default="key", show_default=True,
              help="key: press Ctrl+Opt+Cmd+T, the key you gave to 'Track mask' (both directions). button: click a Tracker button.")
@live_options
@json_option
@click.pass_context
def ui_mask_track(ctx: click.Context, project: str, direction: str, limit_s: float, by: str, lease_wait: float, timeout: float,
                  allow_untested: bool) -> None:
    """Track the selected mask and wait until the mask data stops growing.

    --by key (default) presses Ctrl+Opt+Cmd+T. Premiere has no default key for "Track mask": assign it once in
    Premiere > Keyboard Shortcuts. --by button clicks a Tracker button in Effect Controls instead. For that route the
    Tracker buttons must show on the screen (select the mask; scroll Effect Controls to it). They are found on a
    screen capture; when they are not found, nothing is clicked. Do not use the mouse while it runs.

    tracked: a mask data file grew and then stayed the same for 4 seconds. It is not a check that the mask stayed on
    the subject: check frames at several times. The project is not saved.
    """
    try:
        _checked_project(project, lease_wait, timeout, allow_untested)
        app = _running_app()
        result = ui_mask_core.track(app["process_name"], Path(project), direction, by=by, limit_s=limit_s)
    except CliError as err:
        _fail(ctx, err)
        return
    total = sum(result["changed_files"].values())
    _emit(ctx, result, f"Tracked ({direction}) in {int(result['seconds'])} s. Mask data: {total} bytes in {len(result['changed_files'])} file(s).")


@click.group()
def recovery() -> None:
    """Show an open recovery of the bridge."""


@recovery.command("show")
@json_option
@click.pass_context
@stops_cleanly(STOPPED_LOOK)
def recovery_show(ctx: click.Context) -> None:
    """Show the open recovery, if any, and the active sequence counts. Changes nothing.

    It never acknowledges a recovery. Exits 0 whether a recovery is open or not.
    """
    try:
        client = _client(0.0, reap=False)    # one lease attempt, never a reap: the busy wait is spent in recovery_view
        view = doctor_core.recovery_view(client)
    except CliError as err:                  # a missing bridge folder, or interrupted
        _fail(ctx, err)
        return
    if view["recovery_required"]:
        lines = [f"A recovery is open: {view['reason']}", f"Revision: {view['revision'] or 'cannot be read'}"]
    else:
        lines = ["No recovery marker."]
    state = view["state"]
    if isinstance(state, dict):
        seq = state.get("active_sequence")
        lines.append(f"Project: {state.get('project') or 'none'}")
        lines.append(f"Active sequence: {seq.get('name')} ({seq.get('id')})" if isinstance(seq, dict) else "Active sequence: none")
        lines.append(f"Video: {state.get('video_tracks')} tracks, {state.get('video_clips')} clips. "
                     f"Audio: {state.get('audio_tracks')} tracks, {state.get('audio_clips')} clips.")
    else:
        lines.append("State: not read.")
    lines.append(view["next"])
    _emit(ctx, view, "\n".join(lines))


@click.group(invoke_without_command=True)
@json_option
@click.pass_context
@stops_cleanly(STOPPED_LOOK)
def doctor(ctx: click.Context) -> None:
    """Check the connection to the running Premiere. Changes nothing."""
    if ctx.invoked_subcommand is not None:
        return
    client = make_client(0.0, reap=False)    # one lease attempt, never a reap: the busy wait is spent in run_checks
    # The helpers are looked up here, at each call, so tests can replace them on their modules.
    try:
        checks = doctor_core.run_checks(client, premiere=ui_core.running_premiere, panel=guard.panel_version,
                                        which=shutil.which, server=guard.server_version,
                                        osascript=ui_core.run_osascript, recipes_error=RECIPES_ERROR)
    except CliError as err:                  # interrupted: the only CliError run_checks lets through
        _fail(ctx, err)
        return
    summary = doctor_core.summarize(checks)
    payload = {"checks": checks, "summary": summary}
    lines = [f"{c['status'].upper()}  {c['name']}: {c['detail']}" for c in checks]
    lines.append(f"{summary['pass']} passed, {summary['warn']} warnings, {summary['fail']} failed.")
    if not summary["fail"]:
        _emit(ctx, payload, "\n".join(lines))
        return
    failed = [c["name"] for c in checks if c["status"] == "fail"]
    message = f"{len(failed)} check{'' if len(failed) == 1 else 's'} failed: {', '.join(failed)}."
    if _json(ctx):
        _echo_json({"ok": False, "error": message, "code": "doctor_failed", **payload}, indent=2)
    else:
        _echo_text("\n".join(lines))
        _echo_text(f"Error: {message}", err=True)
    ctx.exit(1)

# The recipe commands (live_recipes.py) attach to the `live` group above. A fault in them must not take the
# other groups down: the import is guarded, and `live --help` says why the recipe commands are missing.
RECIPES_ERROR = None
try:
    from . import live_recipes  # noqa: F401,E402
except Exception as exc:
    RECIPES_ERROR = f"{type(exc).__name__}: {exc}"
    live.epilog = f"The recipe commands are not available: {RECIPES_ERROR}"
