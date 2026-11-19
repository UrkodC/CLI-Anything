"""Unit tests for the live commands and the lazy command groups, against a fake panel in a temp folder."""
from __future__ import annotations

import contextlib
import json
import signal
import subprocess
import sys
from pathlib import Path

import click
import pytest

from cli_anything.premiere import live_cli
from cli_anything.premiere.core import guard
from cli_anything.premiere.core.bridge import BridgeClient, mark_recovery
from cli_anything.premiere.premiere_cli import LazyGroup, cli, dispatch
from cli_anything.premiere.tests.fakes import (  # noqa: F401
    DEFAULT_HEARTBEAT, MARKER_SHAPES, FakePanel, bridge_dir, keep_signals, restore_marker_mode, signal_when,
    unreadable_marker)

ROOT = Path(__file__).resolve().parents[3]
LOCK = ".host-owner-lease"
STATE_MARK = "playhead_ticks: playhead"   # occurs in state.jsx only, not in helpers.jsx
CHECK_MARK = "VERIFY_CHECK"

STATE_OK = {"version": "27.0.0", "build": "88", "projects": [{"name": "X Claude.prproj", "path": "/p/X Claude.prproj", "active": True}],
            "active_sequence": {"id": "s1", "name": "Main"}, "playhead_ticks": "0", "selection": []}
STATE_OWN = {**STATE_OK, "projects": [{"name": "Family film.prproj", "path": "/p/Family film.prproj", "active": True}]}
STATE_26 = {**STATE_OK, "version": "26.0.0"}
BODY = "return __ok({answer: 42});\n"
CHECK_FALSE = f"// {CHECK_MARK}\nreturn __ok({{stored: false}});\n"
CHECK_TRUE = f"// {CHECK_MARK}\nreturn __ok({{stored: true}});\n"


def ok(data) -> str:
    return json.dumps({"success": True, "data": data})


def handler_for(state=STATE_OK, body_data=None, check_data=None):
    body_data = {"answer": 42} if body_data is None else body_data

    def handler(script: str) -> str:
        if STATE_MARK in script:
            return ok(state)
        if CHECK_MARK in script:
            return ok(check_data)
        return ok(body_data)
    return handler


@pytest.fixture(autouse=True)
def _keep_signal_handlers(keep_signals):  # noqa: F811
    """Every test starts with the default handlers of SIGINT, SIGTERM and SIGHUP and leaves them as found."""
    yield


@pytest.fixture(autouse=True)
def _no_real_bridge(monkeypatch, tmp_path):
    # If a test ever reaches the unpatched make_client, it finds a missing folder, not the real bridge.
    monkeypatch.setenv("PREMIERE_TEMP_DIR", str(tmp_path / "not-the-real-bridge"))


@pytest.fixture
def live(bridge_dir, monkeypatch):  # noqa: F811
    monkeypatch.setattr(live_cli, "make_client", lambda wait, **kw: BridgeClient(bridge_dir, wait, **kw))
    monkeypatch.setattr(guard, "panel_version", lambda *a, **k: "1.25.0")
    monkeypatch.setattr(guard, "server_version", lambda *a, **k: "1.25.0")
    return bridge_dir


@pytest.fixture
def sends(monkeypatch) -> list[bool]:
    """The write flag of every script sent, in order."""
    seen: list[bool] = []
    original = BridgeClient.send

    def spy(self, script, *, write, **kw):
        seen.append(write)
        return original(self, script, write=write, **kw)
    monkeypatch.setattr(BridgeClient, "send", spy)
    return seen


@contextlib.contextmanager
def panel_on(bridge_dir, **kw):  # noqa: F811
    panel = FakePanel(bridge_dir, **kw)
    panel.start()
    try:
        yield panel
    finally:
        panel.stop()


def run_json(capsys, argv) -> tuple[int, dict]:
    code = dispatch(["--json", *argv])
    out = capsys.readouterr().out
    return code, json.loads(out)


def script_file(tmp_path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def lease_free(bridge_dir) -> bool:  # noqa: F811
    return not (bridge_dir / LOCK).exists()


# ---------- lazy registration ----------
def test_importing_the_cli_loads_no_live_module():
    # `status` is not in this check: Task 7 lets it load core.bridge inside a try block.
    code = ("import sys; import cli_anything.premiere.premiere_cli as c; c.dispatch(['--help']); c.dispatch(['sequence','--help']);"
            "bad=[m for m in sys.modules if m.startswith('cli_anything.premiere.') and m.split('.')[-1] in"
            " ('bridge','jsx','guard','mcp_client','ui','doctor','live_cli')]; print(bad)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT).stdout
    assert out.strip().splitlines()[-1] == "[]"


def test_a_broken_live_module_does_not_break_the_reader(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "cli_anything.premiere.live_cli", None)      # import raises ImportError
    monkeypatch.setattr(cli.commands["live"], "_target", None)                    # forget a group loaded earlier
    assert dispatch(["--json", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    code, out = run_json(capsys, ["live", "state"])
    assert code == 1
    assert out["ok"] is False and out["code"] == "live_unavailable"
    assert dispatch(["live", "state", "--json"]) == 1                             # a trailing --json works too
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["code"] == "live_unavailable"
    assert dispatch(["--json", "status"]) == 0


def test_root_help_lists_the_new_groups(capsys):
    assert dispatch(["--help"]) == 0
    out = capsys.readouterr().out
    assert "Read Premiere Pro project files" in out
    for name in ("live", "mcp", "ui", "recovery", "doctor"):
        assert f"\n  {name} " in out
    assert "Read and change the running Premiere 27 with scripts." in out
    assert "Check the connection" in out   # click shortens long help lines to the width


def test_live_help_lists_state_and_run(capsys):
    assert dispatch(["live", "--help"]) == 0
    out = capsys.readouterr().out
    assert "state" in out and "run" in out


def test_allow_untested_help_names_the_server(capsys):
    assert dispatch(["live", "state", "--help"]) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "Premiere, panel or MCP server version" in out and "\u2014" not in out


@click.group(invoke_without_command=True)
@click.option("--level", default="low")
@click.pass_context
def toy(ctx: click.Context, level: str) -> None:
    """Toy group for the lazy loader tests."""
    if ctx.invoked_subcommand is None:
        click.echo(f"toy ran level={level}")


@toy.command("sub")
def toy_sub() -> None:
    """Toy subcommand."""
    click.echo("toy sub ran")


def _toy_root() -> click.Group:
    @click.group()
    def root() -> None:
        """Toy root."""
    root.add_command(LazyGroup(name="toy", import_path=f"{__name__}:toy", help="Static toy help."))
    return root


@pytest.mark.parametrize("argv, expected", [
    (["toy"], "toy ran level=low"),
    (["toy", "--level", "high"], "toy ran level=high"),
    (["toy", "sub"], "toy sub ran"),
])
def test_lazy_group_passes_through_to_the_target(argv, expected, capsys):
    result = _toy_root().main(args=argv, prog_name="root", standalone_mode=False)
    assert result is None
    assert capsys.readouterr().out.strip() == expected


def test_lazy_group_help_is_the_target_help(capsys):
    assert _toy_root().main(args=["toy", "--help"], prog_name="root", standalone_mode=False) == 0
    out = capsys.readouterr().out
    assert "Toy group for the lazy loader tests." in out
    assert "--level" in out and "sub" in out
    assert "Usage: root toy" in out


def test_lazy_group_short_help_is_static(capsys):
    root = _toy_root()
    lazy = root.get_command(None, "toy")
    assert lazy.get_short_help_str() == "Static toy help."


def test_lazy_group_with_a_bad_target_fails_cleanly(capsys):
    @click.group()
    def root() -> None:
        """Toy root."""
    root.add_command(LazyGroup(name="gone", import_path="cli_anything.premiere.no_such_module:x", help="Gone."))
    assert root.main(args=["gone"], prog_name="root", standalone_mode=False) == 1
    assert "Error: The gone commands cannot load" in capsys.readouterr().err


# ---------- live state ----------
def test_live_state(live, capsys):
    with panel_on(live, handler=handler_for()) as panel:
        code, out = run_json(capsys, ["live", "state"])
    assert code == 0
    assert out.pop("ok") is True
    assert out == {**STATE_OK, "warnings": []}
    assert len(panel.seen) == 1 and STATE_MARK in panel.seen[0]
    assert lease_free(live)


def test_live_state_human(live, capsys):
    with panel_on(live, handler=handler_for()):
        assert dispatch(["live", "state"]) == 0
    out = capsys.readouterr().out
    assert "27.0.0" in out and "88" in out
    assert "* X Claude.prproj" in out
    assert "Main" in out
    assert "Selected clips: 0" in out


def test_live_state_with_no_project(live, capsys):
    with panel_on(live, handler=handler_for(state={**STATE_OK, "projects": [], "active_sequence": None})):
        code, out = run_json(capsys, ["live", "state"])
    assert code == 0
    assert out["projects"] == [] and out["warnings"] == []


def test_live_state_warns_on_premiere_26(live, capsys):
    with panel_on(live, handler=handler_for(state=STATE_26)):
        code, out = run_json(capsys, ["live", "state"])
    assert code == 0
    assert out["warnings"] and "26.0.0" in out["warnings"][0]


def test_live_state_bridge_missing(monkeypatch, tmp_path, capsys):
    gone = tmp_path / "no-bridge"
    monkeypatch.setattr(live_cli, "make_client", lambda wait, **kw: BridgeClient(gone, wait, **kw))
    monkeypatch.setattr(guard, "panel_version", lambda *a, **k: "1.25.0")
    monkeypatch.setattr(guard, "server_version", lambda *a, **k: "1.25.0")
    code, out = run_json(capsys, ["live", "state"])
    assert code == 1 and out["code"] == "bridge_missing"
    assert not gone.exists()


def test_live_state_bridge_down(live, capsys):
    code, out = run_json(capsys, ["live", "state", "--lease-wait", "0"])
    assert code == 1 and out["code"] == "bridge_down"
    assert lease_free(live)


def test_live_state_with_a_waiting_panel_is_bridge_down(live, capsys):
    with panel_on(live, handler=handler_for(), heartbeat={**DEFAULT_HEARTBEAT, "state": "waiting"}) as panel:
        code, out = run_json(capsys, ["live", "state", "--lease-wait", "0"])
    assert code == 1 and out["code"] == "bridge_down" and "Start Bridge" in out["error"]
    assert panel.seen == [] and lease_free(live)


def test_live_state_warns_on_an_untested_server(live, monkeypatch, capsys):
    monkeypatch.setattr(guard, "server_version", lambda *a, **k: "1.26.0")
    with panel_on(live, handler=handler_for()) as panel:
        code, out = run_json(capsys, ["live", "state"])
    assert code == 0 and len(out["warnings"]) == 1 and "1.26.0" in out["warnings"][0]
    assert len(panel.seen) == 1


# ---------- live run ----------
def test_write_flags_sent(live, tmp_path, capsys, sends):
    body = script_file(tmp_path, "body.jsx", BODY)
    check = script_file(tmp_path, "check.jsx", CHECK_TRUE)
    with panel_on(live, handler=handler_for(check_data={"stored": True})):
        assert dispatch(["--json", "live", "state"]) == 0
        assert sends == [False]
        sends.clear()
        assert dispatch(["--json", "live", "run", body]) == 0
        assert sends == [False, False]
        sends.clear()
        assert dispatch(["--json", "live", "run", body, "--write", "--verify", check]) == 0
        assert sends == [False, True, False]
        sends.clear()
        assert dispatch(["--json", "live", "run", body, "--verify", check]) == 0
        assert sends == [False, False, False]
    capsys.readouterr()


def test_check_failure_says_the_script_ran(live, tmp_path, capsys):
    body = script_file(tmp_path, "body.jsx", BODY)
    check = script_file(tmp_path, "check.jsx", CHECK_TRUE)

    def handler(script):
        if CHECK_MARK in script:
            return json.dumps({"success": False, "error": "boom"})
        return handler_for()(script)
    with panel_on(live, handler=handler):
        code, out = run_json(capsys, ["live", "run", body, "--write", "--verify", check])
    assert code == 1 and out["code"] == "script_failed"
    assert out["error"].startswith("SCRIPT ran; the check script failed:") and "boom" in out["error"]
    assert lease_free(live)


def test_live_run_read(live, tmp_path, capsys):
    body = script_file(tmp_path, "body.jsx", BODY)
    with panel_on(live, handler=handler_for()) as panel:
        code, out = run_json(capsys, ["live", "run", body, "--args", '{"n": 1}'])
    assert code == 0
    assert out == {"ok": True, "data": {"answer": 42}, "stored": None, "rendered": None, "warnings": []}
    assert len(panel.seen) == 2
    assert STATE_MARK in panel.seen[0]
    assert "answer: 42" in panel.seen[1] and 'var ARGS = {"n": 1};' in panel.seen[1]
    assert lease_free(live)


def test_live_run_write_ok(live, tmp_path, capsys):
    body = script_file(tmp_path, "body.jsx", BODY)
    check = script_file(tmp_path, "check.jsx", CHECK_TRUE)
    with panel_on(live, handler=handler_for(check_data={"stored": True})) as panel:
        code, out = run_json(capsys, ["live", "run", body, "--write", "--verify", check])
    assert code == 0
    assert out["stored"] is True and out["data"] == {"answer": 42}
    assert len(panel.seen) == 3 and CHECK_MARK in panel.seen[2]
    assert lease_free(live)


def test_live_run_write_refused_on_own_project(live, tmp_path, capsys):
    body = script_file(tmp_path, "body.jsx", BODY)
    with panel_on(live, handler=handler_for(state=STATE_OWN)) as panel:
        code, out = run_json(capsys, ["live", "run", body, "--write"])
    assert code == 1
    assert out["ok"] is False and out["code"] == "project_guard"
    assert len(panel.seen) == 1 and STATE_MARK in panel.seen[0]
    assert lease_free(live)


def test_live_run_write_refused_on_premiere_26(live, tmp_path, capsys):
    body = script_file(tmp_path, "body.jsx", BODY)
    with panel_on(live, handler=handler_for(state=STATE_26)) as panel:
        code, out = run_json(capsys, ["live", "run", body, "--write"])
    assert code == 1 and out["code"] == "untested_premiere"
    assert len(panel.seen) == 1 and STATE_MARK in panel.seen[0]
    assert lease_free(live)


def test_live_run_write_refused_on_an_untested_server(live, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(guard, "server_version", lambda *a, **k: None)
    body = script_file(tmp_path, "body.jsx", BODY)
    with panel_on(live, handler=handler_for()) as panel:
        code, out = run_json(capsys, ["live", "run", body, "--write"])
        assert code == 1 and out["code"] == "untested_bridge" and "unknown" in out["error"]
        assert panel.seen == []
        code, out = run_json(capsys, ["live", "run", body, "--write", "--allow-untested"])
    assert code == 0 and out["data"] == {"answer": 42}
    assert any("--allow-untested" in w for w in out["warnings"])
    assert lease_free(live)


def test_live_run_write_refused_with_recovery(live, tmp_path, capsys):
    mark_recovery(live, "unknown_command_outcome")
    body = script_file(tmp_path, "body.jsx", BODY)
    with panel_on(live, handler=handler_for()) as panel:
        code, out = run_json(capsys, ["live", "run", body, "--write"])
    assert code == 1 and out["code"] == "recovery_required"
    assert panel.seen == []
    assert lease_free(live)


def test_live_run_verify_false_exits_1(live, tmp_path, capsys):
    body = script_file(tmp_path, "body.jsx", BODY)
    check = script_file(tmp_path, "check.jsx", CHECK_FALSE)
    with panel_on(live, handler=handler_for(check_data={"stored": False})) as panel:
        code, out = run_json(capsys, ["live", "run", body, "--write", "--verify", check])
    assert code == 1
    assert out["ok"] is False and out["code"] == "not_stored"
    assert out["stored"] is False and out["data"] == {"answer": 42} and out["rendered"] is None
    assert len(panel.seen) == 3
    assert lease_free(live)


def test_live_run_verify_false_human(live, tmp_path, capsys):
    body = script_file(tmp_path, "body.jsx", BODY)
    check = script_file(tmp_path, "check.jsx", CHECK_FALSE)
    with panel_on(live, handler=handler_for(check_data={"stored": False})):
        assert dispatch(["live", "run", body, "--verify", check]) == 1
    assert "not stored" in capsys.readouterr().err
    assert lease_free(live)


def test_live_run_bad_verify(live, tmp_path, capsys):
    body = script_file(tmp_path, "body.jsx", BODY)
    check = script_file(tmp_path, "check.jsx", CHECK_TRUE)
    with panel_on(live, handler=handler_for(check_data={"stored": "yes"})):
        code, out = run_json(capsys, ["live", "run", body, "--verify", check])
    assert code == 1 and out["code"] == "bad_verify"
    assert lease_free(live)


@pytest.mark.parametrize("args", ["[1]", "not json", '"text"'])
def test_live_run_bad_args(live, tmp_path, capsys, args):
    body = script_file(tmp_path, "body.jsx", BODY)
    with panel_on(live, handler=handler_for()) as panel:
        code, out = run_json(capsys, ["live", "run", body, "--args", args])
    assert code == 1 and out["code"] == "bad_args"
    assert panel.seen == []
    assert lease_free(live)


def test_live_run_missing_verify_file_sends_nothing(live, tmp_path, capsys):
    body = script_file(tmp_path, "body.jsx", BODY)
    with panel_on(live, handler=handler_for()) as panel:
        code, out = run_json(capsys, ["live", "run", body, "--write", "--verify", str(tmp_path / "none.jsx")])
    assert code == 1 and out["code"] == "script_unreadable"
    assert panel.seen == []
    assert lease_free(live)


def test_lease_is_free_after_every_command(live, tmp_path, capsys):
    body = script_file(tmp_path, "body.jsx", BODY)
    check = script_file(tmp_path, "check.jsx", CHECK_FALSE)
    cases = [
        (handler_for(), ["live", "state"], 0),
        (handler_for(), ["live", "run", body, "--write"], 0),
        (handler_for(state=STATE_OWN), ["live", "run", body, "--write"], 1),
        (handler_for(state=STATE_26), ["live", "run", body, "--write"], 1),
        (handler_for(check_data={"stored": False}), ["live", "run", body, "--write", "--verify", check], 1),
        (handler_for(), ["live", "run", body, "--args", "[1]"], 1),
    ]
    for handler, argv, expected in cases:
        with panel_on(live, handler=handler):
            assert dispatch(["--json", *argv]) == expected, argv
        capsys.readouterr()
        assert lease_free(live), argv
    mark_recovery(live, "unknown_command_outcome")
    with panel_on(live, handler=handler_for()):
        assert dispatch(["--json", "live", "run", body, "--write"]) == 1
    assert lease_free(live)


# ---------- final review fixes: A3 ----------
@pytest.mark.parametrize("shape", MARKER_SHAPES)
def test_a_marker_that_cannot_be_read_refuses_a_write(live, tmp_path, capsys, shape):
    unreadable_marker(live, shape)
    body = script_file(tmp_path, "body.jsx", BODY)
    try:
        with panel_on(live, handler=handler_for()) as panel:
            code, out = run_json(capsys, ["live", "run", body, "--write"])
    finally:
        restore_marker_mode(live)
    assert code == 1 and out["code"] == "recovery_required"
    assert panel.seen == []
    assert lease_free(live)


# ---------- final review fixes: A1 ----------
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_an_interrupt_while_waiting_for_the_lease_is_reported(live, tmp_path, capsys, keep_signals, signum):  # noqa: F811
    import time
    body = script_file(tmp_path, "body.jsx", BODY)
    for argv in (["live", "state", "--lease-wait", "5"], ["live", "run", body, "--lease-wait", "5"]):
        before = signal.getsignal(signum)
        with BridgeClient(live).lease():
            owner = (live / LOCK / "owner.json").read_bytes()
            start = time.monotonic()
            with signal_when(signum, lambda: signal.getsignal(signum) is not before) as sent:
                code, out = run_json(capsys, argv)
            assert sent["fired"], argv
            assert time.monotonic() - start < 4.0, argv
            assert (live / LOCK / "owner.json").read_bytes() == owner     # the other holder's lease is untouched
        assert code == 1, argv
        assert out == {"ok": False, "code": "interrupted", "error": out["error"]}, argv
        assert signal.getsignal(signum) is before        # the command put the old handler back
    assert lease_free(live)


# ---------- final review fixes: A5 ----------
def test_live_run_help_says_what_runs_without_write(capsys):
    assert dispatch(["live", "run", "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "\u2014" not in text
    assert "Without --write, no write check runs" in text
    assert "a script that hangs leaves no recovery marker" in text
    assert "--write is your statement that it does" in text
    assert "A read script run after SCRIPT, with the same ARGS." in text


def test_root_help_and_version_are_kept(capsys):
    assert dispatch(["--help"]) == 0
    assert "Read Premiere Pro project files" in capsys.readouterr().out
    assert dispatch(["--version"]) == 0
    assert "0.1.0" in capsys.readouterr().out


# ---------- plan 2, task 10: documents and help text ----------
STATUS_KEYS_BEFORE = ["ok", "app", "version", "tested_project_formats", "smoke_read_project_formats",
                      "writes_projects", "live"]
LIVE_GROUPS = ("live", "mcp", "ui", "recovery", "doctor")


def _help_text(capsys, argv) -> str:
    assert dispatch([*argv, "--help"]) == 0, argv
    return " ".join(capsys.readouterr().out.split())


def _command_paths(command: click.Command, path: list[str]):
    yield path
    if isinstance(command, click.Group):
        for name in command.list_commands(None):
            yield from _command_paths(command.get_command(None, name), [*path, name])


def test_root_help_says_what_writes(capsys):
    text = _help_text(capsys, [])
    assert "Read Premiere Pro project files" in text
    assert ("Read Premiere Pro project files: sequences, clips and effects. The file commands never write. "
            "The live commands change the running Premiere 27.") in text
    assert "effects and Color Mode grades" not in text      # the colormode group keeps its own line
    assert "The file commands never write." in text
    assert "The live commands change the running Premiere 27." in text
    assert "Never writes." not in text


def test_status_keeps_keys_and_adds_live_commands(capsys):
    code, data = run_json(capsys, ["status"])
    assert code == 0
    for key in STATUS_KEYS_BEFORE:
        assert key in data, key
    assert data["live_commands"] is True
    assert data["writes_projects"] is False
    assert dispatch(["status"]) == 0
    out = capsys.readouterr().out
    assert ("The file commands only read project files and need no running Premiere. "
            "The live commands need Premiere 27 with the MCP Bridge panel.") in out
    assert "This tool only reads project files." not in out


def test_every_recipe_has_help_with_its_proof_level(capsys):
    assert "stored" in _help_text(capsys, ["live", "effect", "set"])
    lut = _help_text(capsys, ["live", "lut", "set"])
    assert "stored" in lut and "--prove" in lut
    assert "file check" in _help_text(capsys, ["live", "frame", "export"])


def test_no_em_dash_in_any_help_text(capsys):
    seen = 0
    for group in LIVE_GROUPS:
        target = getattr(live_cli, group)
        for path in _command_paths(target, [group]):
            text = _help_text(capsys, path)
            assert "\u2014" not in text and "\u2013" not in text, path
            seen += 1
    assert seen >= 25          # every group, subgroup and command of the five groups
    root = _help_text(capsys, [])
    assert "\u2014" not in root and "\u2013" not in root


def test_repl_lists_the_new_commands():
    from cli_anything.premiere.premiere_cli import REPL_COMMANDS
    for start in ("doctor", "recovery show", "mcp call", "ui mode", "live clip list", "live effect set",
                  "live frame export", "live lut set"):
        assert any(key.startswith(start) for key in REPL_COMMANDS), start


# ---------- plan 3, task 9: documents and help text ----------
PLAN_COMMANDS = (["live", "keys", "set"], ["live", "place"], ["live", "effect", "apply"])
STYLE_BANNED = ("delve", "leverage", "robust", "seamless", "comprehensive", "crucial")


def test_plan_commands_have_help(capsys):
    for path in PLAN_COMMANDS:
        text = _help_text(capsys, path)
        for needle in ("--dry-run", "--skip-refused", "not saved"):
            assert needle in text, (path, needle)
        # the first step and the exit rule, in words
        assert "Run --dry-run first" in text, path
        assert "exit status is 0 only when every entry is done or had nothing to do" in text, path


def test_effect_set_help_is_as_complete_as_the_skill(capsys):
    text = _help_text(capsys, ["live", "effect", "set"])
    for needle in ("A clip on an audio track is refused.", "--effect-index", "does not match the index",
                   "target_moved", "Nothing more was written", "precondition", "--expect-old"):
        assert needle in text, needle


def test_frame_export_help_says_proxies_off_only_for_proxies_off(capsys):
    text = _help_text(capsys, ["live", "frame", "export"])
    assert "proxy display is off unless you give --proxies keep" in text
    assert "While the command runs, proxy display is off and" not in text


def test_no_em_dash_or_banned_word_in_any_help_text_with_the_plan_commands(capsys):
    paths = [path for group in LIVE_GROUPS for path in _command_paths(getattr(live_cli, group), [group])]
    for needed in (["live", "keys"], ["live", "keys", "get"], ["live", "keys", "set"], ["live", "place"],
                   ["live", "effect", "apply"]):
        assert needed in paths, needed
    for path in paths:
        text = _help_text(capsys, path)
        assert "\u2014" not in text and "\u2013" not in text, path
        assert not [word for word in STYLE_BANNED if word in text.lower()], path


def test_repl_lists_the_plan_commands():
    from cli_anything.premiere.premiere_cli import REPL_COMMANDS
    for start in ("live keys get", "live keys set", "live place", "live effect apply"):
        lines = [key for key in REPL_COMMANDS if key.startswith(start)]
        assert len(lines) == 1, (start, lines)
        assert REPL_COMMANDS[lines[0]].strip() and "\u2014" not in REPL_COMMANDS[lines[0]], start
    for start in ("status", "project info", "sequence clips", "clip effects", "live effect set", "live lut set"):
        assert any(key.startswith(start) for key in REPL_COMMANDS), start     # the older lines stay


def test_root_help_and_status_unchanged_by_the_plan_commands(capsys):
    assert dispatch(["--help"]) == 0
    assert "Read Premiere Pro project files" in capsys.readouterr().out
    assert dispatch(["--version"]) == 0
    assert "0.1.0" in capsys.readouterr().out
    code, data = run_json(capsys, ["status"])
    assert code == 0 and data["writes_projects"] is False
    for key in STATUS_KEYS_BEFORE:
        assert key in data, key


def test_repl_commands_are_real_commands(capsys):
    from cli_anything.premiere.premiere_cli import REPL_COMMANDS
    for key in REPL_COMMANDS:
        if key in ("help", "exit"):
            continue
        words = []
        for word in key.split():
            if word.startswith(("[", "-")) or word.isupper() or "|" in word:
                break
            words.append(word)
        assert dispatch([*words, "--help"]) == 0, key
        capsys.readouterr()


# ---------- plan 2, task 10: output that any text survives ----------
def test_emit_and_fail_print_a_lone_surrogate_as_valid_json(capsys):
    from cli_anything.premiere.errors import CliError
    from cli_anything.premiere.premiere_cli import _emit, _fail

    @click.command()
    @click.pass_context
    def emit_cmd(ctx):
        ctx.ensure_object(dict)["json"] = True
        _emit(ctx, {"name": "bad \ud800 name"}, "unused")

    @click.command()
    @click.pass_context
    def fail_cmd(ctx):
        ctx.ensure_object(dict)["json"] = True
        _fail(ctx, CliError("script_failed", "bad \udcff text"))

    emit_cmd.main(args=[], standalone_mode=False)
    assert json.loads(capsys.readouterr().out) == {"ok": True, "name": "bad \ud800 name"}
    assert fail_cmd.main(args=[], standalone_mode=False) == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "bad \udcff text", "code": "script_failed"}


def test_emit_and_fail_print_a_lone_surrogate_in_human_text(capsys):
    from cli_anything.premiere.errors import CliError
    from cli_anything.premiere.premiere_cli import _emit, _fail

    @click.command()
    @click.pass_context
    def emit_cmd(ctx):
        _emit(ctx, {}, "Name: bad \ud800 name")

    @click.command()
    @click.pass_context
    def fail_cmd(ctx):
        _fail(ctx, CliError("script_failed", "bad \udcff text"))

    emit_cmd.main(args=[], standalone_mode=False)
    out = capsys.readouterr().out
    assert out.startswith("Name: bad ") and out.rstrip().endswith(" name")
    assert fail_cmd.main(args=[], standalone_mode=False) == 1
    err = capsys.readouterr().err
    assert err.startswith("Error: bad ") and err.rstrip().endswith(" text")


def test_emit_and_fail_keep_non_ascii_letters(capsys):
    from cli_anything.premiere.errors import CliError
    from cli_anything.premiere.premiere_cli import _emit, _fail

    @click.command()
    @click.pass_context
    def emit_cmd(ctx):
        ctx.ensure_object(dict)["json"] = True
        _emit(ctx, {"name": "Película ñ"}, "unused")

    @click.command()
    @click.pass_context
    def fail_cmd(ctx):
        _fail(ctx, CliError("x", "Película ñ"))

    emit_cmd.main(args=[], standalone_mode=False)
    assert '"name": "Película ñ"' in capsys.readouterr().out
    assert fail_cmd.main(args=[], standalone_mode=False) == 1
    assert capsys.readouterr().err == "Error: Película ñ\n"


def test_live_run_not_stored_with_a_lone_surrogate_prints_valid_json(live, tmp_path, capsys):
    body = script_file(tmp_path, "body.jsx", BODY)
    check = script_file(tmp_path, "check.jsx", CHECK_FALSE)
    handler = handler_for(body_data={"name": "bad \ud800 name"}, check_data={"stored": False})
    with panel_on(live, handler=handler):
        code, out = run_json(capsys, ["live", "run", body, "--write", "--verify", check])
    assert code == 1 and out["code"] == "not_stored"
    assert out["data"] == {"name": "bad \ud800 name"}
