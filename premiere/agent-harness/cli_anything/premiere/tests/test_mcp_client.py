"""Unit tests for the MCP client and the mcp commands, against a fake stdio server and a temp bridge folder."""
from __future__ import annotations

import json
import os
import signal
import sys
import threading

import pytest

from cli_anything.premiere import live_cli
from cli_anything.premiere.core import mcp_client
from cli_anything.premiere.core.bridge import BridgeClient, install_signal_handlers
from cli_anything.premiere.core.mcp_client import McpSession
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.premiere_cli import dispatch
from cli_anything.premiere.tests.fakes import bridge_dir, fake_mcp_command, keep_signals, signal_when  # noqa: F401

LOCK = ".host-owner-lease"

# Small servers that misbehave in one way each.
SILENT_SERVER = ["-c", "import time; time.sleep(60)"]
DYING_SERVER = ["-c", "import sys; sys.stderr.write('fatal: cannot reach Premiere\\n'); sys.exit(3)"]
INIT_ERROR_SERVER = ["-c", (
    "import json, sys\n"
    "m = json.loads(sys.stdin.readline())\n"
    "print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'error': {'code': -32000, 'message': 'no thanks'}}), flush=True)\n"
    "sys.stdin.read()\n")]
STUBBORN_SERVER = ["-c", (   # answers initialize, then stays even when its stdin closes
    "import json, sys, time\n"
    "m = json.loads(sys.stdin.readline())\n"
    "print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': {'serverInfo': {'name': 'stubborn', 'version': '0'}}}), flush=True)\n"
    "time.sleep(60)\n")]
MUTE_SERVER = ["-c", (   # answers initialize, then never answers anything
    "import json, sys, time\n"
    "for line in sys.stdin:\n"
    "    m = json.loads(line)\n"
    "    if m.get('method') == 'initialize':\n"
    "        print(json.dumps({'jsonrpc': '2.0', 'id': m['id'], 'result': {'serverInfo': {'name': 'mute', 'version': '0'}}}), flush=True)\n"
    "    elif 'id' in m:\n"
    "        time.sleep(60)\n")]


def python(args: list[str]) -> list[str]:
    return [sys.executable, *args]


@pytest.fixture(autouse=True)
def _keep_signal_handlers(keep_signals):  # noqa: F811
    """Every test starts with the default handlers of SIGINT, SIGTERM and SIGHUP and leaves them as found."""
    yield


@pytest.fixture(autouse=True)
def _safe_env(monkeypatch, tmp_path):
    # A test that reaches a real bridge folder or inherits real server settings would be wrong.
    monkeypatch.setenv("PREMIERE_TEMP_DIR", str(tmp_path / "not-the-real-bridge"))
    monkeypatch.delenv("PREMIERE_MCP_CAPABILITIES", raising=False)
    monkeypatch.setattr(mcp_client, "CLOSE_WAIT_S", 0.3)


def lease_free(bridge_dir) -> bool:  # noqa: F811
    return not (bridge_dir / LOCK).exists()


def fake_session(bridge_dir, tmp_path, **kw) -> McpSession:  # noqa: F811
    return McpSession(BridgeClient(bridge_dir), fake_mcp_command(tmp_path, bridge_dir), **kw)


# ---------- the client ----------
def test_start_holds_the_lease_and_releases_it(bridge_dir, tmp_path):
    s = fake_session(bridge_dir, tmp_path)
    assert lease_free(bridge_dir)
    with s:
        assert s.call("lease_seen_at_start", {})["structured"] == {"seen": True}
        assert not (bridge_dir / LOCK).exists()
    assert lease_free(bridge_dir)


def test_start_returns_the_server_name_and_version(bridge_dir, tmp_path):
    s = fake_session(bridge_dir, tmp_path)
    try:
        assert s.start() == {"name": "fake-premiere-mcp", "version": "9.9.9"}
    finally:
        s.close()


def test_list_tools_follows_pages(bridge_dir, tmp_path):
    with fake_session(bridge_dir, tmp_path) as s:
        tools = s.list_tools()
    assert [t["name"] for t in tools] == ["ping", "echo", "fail", "lease_seen_at_start", "env"]


def test_call_returns_structured_and_text(bridge_dir, tmp_path):
    with fake_session(bridge_dir, tmp_path) as s:
        reply = s.call("echo", {"a": 1})
        assert reply == {"is_error": False, "structured": {"a": 1}, "text": '{"a": 1}'}
        assert s.call("ping", {})["structured"] == {"connected": True}


def test_tool_error_is_reported(bridge_dir, tmp_path):
    with fake_session(bridge_dir, tmp_path) as s:
        assert s.call("fail", {}) == {"is_error": True, "structured": None, "text": "fake failure"}
        assert s.call("ping", {})["is_error"] is False      # the session still works


def test_a_json_rpc_error_is_mcp_error(bridge_dir, tmp_path):
    with fake_session(bridge_dir, tmp_path) as s:
        with pytest.raises(CliError) as err:
            s.call("no_such_tool", {})
    assert err.value.code == "mcp_error" and "Unknown tool: no_such_tool" in err.value.message


@pytest.mark.parametrize("given, expected", [
    ("inspect,unsafe-script,edit", "inspect,edit"),
    (" inspect , Unsafe-Script ,edit ", "inspect,edit"),
    ("unsafe-script", None),                                  # nothing left: the variable is removed
    ("inspect,edit", "inspect,edit"),
])
def test_unsafe_script_is_never_passed_on(bridge_dir, tmp_path, given, expected):
    with fake_session(bridge_dir, tmp_path, env={"PREMIERE_MCP_CAPABILITIES": given}) as s:
        assert s.call("env", {})["structured"] == {"value": expected}


def test_unsafe_script_is_removed_from_the_inherited_environment(bridge_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("PREMIERE_MCP_CAPABILITIES", "export,unsafe-script")
    with fake_session(bridge_dir, tmp_path) as s:
        assert s.call("env", {})["structured"] == {"value": "export"}


def test_an_unset_capability_variable_stays_unset(bridge_dir, tmp_path):
    with fake_session(bridge_dir, tmp_path) as s:
        assert s.call("env", {})["structured"] == {"value": None}


def test_the_child_gets_the_bridge_folder_in_its_environment(bridge_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("PREMIERE_TEMP_DIR", str(tmp_path / "other-a"))
    decoy = tmp_path / "decoy"      # argv names a folder with no lease in it: only the variable can find the lease
    decoy.mkdir()
    command = fake_mcp_command(tmp_path, decoy)
    with McpSession(BridgeClient(bridge_dir), command, env={"PREMIERE_TEMP_DIR": str(tmp_path / "other-b")}) as s:
        assert s.call("lease_seen_at_start", {})["structured"] == {"seen": True}


def test_missing_command(bridge_dir):
    s = McpSession(BridgeClient(bridge_dir), ["no-such-premiere-mcp"])
    with pytest.raises(CliError) as err:
        s.start()
    assert err.value.code == "mcp_missing"
    assert lease_free(bridge_dir)


def test_server_death_is_reported(bridge_dir, tmp_path):
    with fake_session(bridge_dir, tmp_path) as s:
        s._proc.kill()
        s._proc.wait(timeout=5)
        with pytest.raises(CliError) as err:
            s.call("ping", {})
        assert err.value.code == "mcp_died"
        with pytest.raises(CliError) as again:
            s.list_tools()
        assert again.value.code == "mcp_died"


def test_a_server_that_dies_at_start_is_reported_with_its_stderr(bridge_dir):
    s = McpSession(BridgeClient(bridge_dir), python(DYING_SERVER))
    with pytest.raises(CliError) as err:
        s.start()
    assert err.value.code == "mcp_died"
    assert "fatal: cannot reach Premiere" in err.value.message
    assert s._proc.poll() is not None
    assert lease_free(bridge_dir)


def test_start_times_out_and_cleans_up(bridge_dir, monkeypatch):
    monkeypatch.setattr(mcp_client, "START_TIMEOUT_S", 0.5)
    s = McpSession(BridgeClient(bridge_dir), python(SILENT_SERVER))
    with pytest.raises(CliError) as err:
        s.start()
    assert err.value.code == "mcp_timeout"
    assert s._proc.poll() is not None           # the child was killed
    assert lease_free(bridge_dir)


def test_a_json_rpc_error_on_initialize_is_mcp_error(bridge_dir):
    s = McpSession(BridgeClient(bridge_dir), python(INIT_ERROR_SERVER))
    with pytest.raises(CliError) as err:
        s.start()
    assert err.value.code == "mcp_error" and err.value.message == "no thanks"
    assert s._proc.poll() is not None
    assert lease_free(bridge_dir)


def test_call_times_out(bridge_dir):
    with McpSession(BridgeClient(bridge_dir), python(MUTE_SERVER)) as s:
        with pytest.raises(CliError) as err:
            s.call("echo", {}, timeout_s=0.3)
    assert err.value.code == "mcp_timeout"


def test_sigterm_while_starting_kills_the_child_and_frees_the_lease(bridge_dir, monkeypatch):
    monkeypatch.setattr(mcp_client, "START_TIMEOUT_S", 20.0)
    s = McpSession(BridgeClient(bridge_dir), python(SILENT_SERVER))
    install_signal_handlers()
    timer = threading.Timer(0.5, os.kill, (os.getpid(), signal.SIGTERM))
    timer.start()
    try:
        with pytest.raises(CliError) as err:
            s.start()
    finally:
        timer.cancel()
    assert err.value.code == "interrupted"
    assert s._proc.poll() is not None
    assert lease_free(bridge_dir)


def test_start_refuses_when_the_lease_is_held(bridge_dir, tmp_path):
    client = BridgeClient(bridge_dir)
    s = McpSession(client, fake_mcp_command(tmp_path, bridge_dir))
    with client.lease():
        with pytest.raises(RuntimeError):
            s.start()
        assert client.lease().held and (bridge_dir / LOCK).exists()     # the caller's lease is left alone
    assert lease_free(bridge_dir)


def test_start_waits_for_the_lease_then_gives_up(bridge_dir, tmp_path):
    other = BridgeClient(bridge_dir)
    s = McpSession(BridgeClient(bridge_dir, lease_wait_s=0), fake_mcp_command(tmp_path, bridge_dir))
    with other.lease():
        with pytest.raises(CliError) as err:
            s.start()
    assert err.value.code == "lease_busy"
    assert s._proc is None                      # no child was started


def test_close_is_safe_to_repeat_and_before_start(bridge_dir, tmp_path):
    s = fake_session(bridge_dir, tmp_path)
    s.close()
    s.start()
    s.close()
    s.close()
    assert s._proc.poll() is not None


# ---------- the mcp commands ----------
@pytest.fixture
def mcp_cli(bridge_dir, tmp_path, monkeypatch):  # noqa: F811
    """The mcp commands on the fake server. `sessions` lists every session the commands built."""
    command = fake_mcp_command(tmp_path, bridge_dir)
    sessions: list[McpSession] = []

    def make_session(client):
        sessions.append(McpSession(client, command))
        return sessions[-1]

    monkeypatch.setattr(live_cli, "make_client", lambda wait, **kw: BridgeClient(bridge_dir, wait, **kw))
    monkeypatch.setattr(live_cli, "make_session", make_session)
    return sessions


def run_json(capsys, argv) -> tuple[int, dict]:
    code = dispatch(["--json", *argv])
    return code, json.loads(capsys.readouterr().out)


def test_mcp_call_command(mcp_cli, bridge_dir, capsys):
    code, out = run_json(capsys, ["mcp", "call", "echo", "--args", '{"a":1}'])
    assert code == 0
    assert out == {"ok": True, "tool": "echo", "result": {"a": 1}, "stored": None, "rendered": None}
    assert lease_free(bridge_dir)


def test_mcp_call_without_args_sends_an_empty_object(mcp_cli, capsys):
    code, out = run_json(capsys, ["mcp", "call", "echo"])
    assert code == 0 and out["result"] == {}


def test_mcp_call_human_output(mcp_cli, capsys):
    assert dispatch(["mcp", "call", "echo", "--args", '{"a": 1}']) == 0
    assert json.loads(capsys.readouterr().out) == {"a": 1}


def test_the_result_falls_back_to_the_text():
    assert live_cli._result_value({"structured": None, "text": "plain words"}) == "plain words"
    assert live_cli._result_value({"structured": {"a": 1}, "text": "ignored"}) == {"a": 1}


def test_mcp_call_tool_failure_exits_1(mcp_cli, bridge_dir, capsys):
    code, out = run_json(capsys, ["mcp", "call", "fail"])
    assert code == 1
    assert out == {"ok": False, "error": "fake failure", "code": "tool_failed"}
    assert lease_free(bridge_dir)


def test_mcp_call_tool_failure_human_output(mcp_cli, capsys):
    assert dispatch(["mcp", "call", "fail"]) == 1
    assert "Error: fake failure" in capsys.readouterr().err


@pytest.mark.parametrize("args", ["[1]", "not json", '"text"'])
def test_mcp_call_bad_args_starts_no_server(mcp_cli, bridge_dir, capsys, args):
    code, out = run_json(capsys, ["mcp", "call", "echo", "--args", args])
    assert code == 1 and out["code"] == "bad_args"
    assert mcp_cli == [] and lease_free(bridge_dir)


def test_mcp_call_unknown_tool_is_an_mcp_error(mcp_cli, bridge_dir, capsys):
    code, out = run_json(capsys, ["mcp", "call", "no_such_tool"])
    assert code == 1 and out["code"] == "mcp_error" and "no_such_tool" in out["error"]
    assert lease_free(bridge_dir)


def test_mcp_commands_report_a_missing_server(bridge_dir, monkeypatch, capsys):
    monkeypatch.setattr(live_cli, "make_client", lambda wait, **kw: BridgeClient(bridge_dir, wait, **kw))
    monkeypatch.setattr(live_cli, "make_session", lambda client: McpSession(client, ["no-such-premiere-mcp"]))
    code, out = run_json(capsys, ["mcp", "list"])
    assert code == 1 and out["code"] == "mcp_missing"
    assert lease_free(bridge_dir)


def test_mcp_commands_report_a_missing_bridge_folder(tmp_path, monkeypatch, capsys):
    gone = tmp_path / "no-bridge"
    monkeypatch.setattr(live_cli, "make_client", lambda wait, **kw: BridgeClient(gone, wait, **kw))
    code, out = run_json(capsys, ["mcp", "list"])
    assert code == 1 and out["code"] == "bridge_missing"
    assert not gone.exists()


def test_mcp_commands_report_a_busy_lease(mcp_cli, bridge_dir, capsys):
    with BridgeClient(bridge_dir).lease():
        code, out = run_json(capsys, ["mcp", "list", "--lease-wait", "0"])
        assert code == 1 and out["code"] == "lease_busy"
        assert (bridge_dir / LOCK).exists()     # still the other program's
    assert lease_free(bridge_dir)


def write_batch(tmp_path, value) -> str:
    path = tmp_path / "batch.json"
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return str(path)


def test_mcp_batch_runs_every_call_in_one_session(mcp_cli, bridge_dir, tmp_path, capsys):
    batch = write_batch(tmp_path, [{"tool": "echo", "args": {"n": 1}}, {"tool": "ping"}, {"tool": "echo", "args": {"n": 2}}])
    code, out = run_json(capsys, ["mcp", "batch", batch])
    assert code == 0
    assert out == {"ok": True, "ran": 3, "total": 3, "results": [
        {"tool": "echo", "ok": True, "result": {"n": 1}},
        {"tool": "ping", "ok": True, "result": {"connected": True}},
        {"tool": "echo", "ok": True, "result": {"n": 2}},
    ]}
    assert len(mcp_cli) == 1
    assert lease_free(bridge_dir)


def test_mcp_batch_human_output(mcp_cli, tmp_path, capsys):
    batch = write_batch(tmp_path, [{"tool": "echo", "args": {"n": 1}}, {"tool": "ping"}])
    assert dispatch(["mcp", "batch", batch]) == 0
    out = capsys.readouterr().out
    assert '1. echo: {"n": 1}' in out and '2. ping: {"connected": true}' in out


def test_mcp_batch_stops_at_first_failure(mcp_cli, bridge_dir, tmp_path, capsys):
    batch = write_batch(tmp_path, [{"tool": "echo", "args": {"n": 1}}, {"tool": "fail"}, {"tool": "echo", "args": {"n": 2}}])
    code, out = run_json(capsys, ["mcp", "batch", batch])
    assert code == 1
    assert out["ok"] is False and out["code"] == "tool_failed"
    assert out["ran"] == 2 and out["total"] == 3
    assert "fake failure" in out["error"] and "Call 2 of 3 (fail)" in out["error"]
    assert out["results"] == [{"tool": "echo", "ok": True, "result": {"n": 1}},
                              {"tool": "fail", "ok": False, "result": "fake failure"}]
    assert lease_free(bridge_dir)


def test_mcp_batch_stops_when_a_call_gets_no_answer(bridge_dir, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(live_cli, "make_client", lambda wait, **kw: BridgeClient(bridge_dir, wait, **kw))
    monkeypatch.setattr(live_cli, "make_session", lambda client: McpSession(client, python(MUTE_SERVER)))
    batch = write_batch(tmp_path, [{"tool": "echo"}, {"tool": "ping"}])
    code, out = run_json(capsys, ["mcp", "batch", batch, "--timeout", "0.3"])
    assert code == 1 and out["code"] == "mcp_timeout"
    assert out["ran"] == 0 and out["total"] == 2 and out["results"] == []
    assert "Call 1 of 2 (echo)" in out["error"]
    assert lease_free(bridge_dir)


def test_mcp_batch_failure_human_output(mcp_cli, tmp_path, capsys):
    batch = write_batch(tmp_path, [{"tool": "echo"}, {"tool": "fail"}])
    assert dispatch(["mcp", "batch", batch]) == 1
    err = capsys.readouterr().err
    assert "Error: Call 2 of 2 (fail): fake failure" in err and "Ran 2 of 2 calls." in err


@pytest.mark.parametrize("content", [
    "{}", "not json", '["echo"]', '[{"args": {}}]', '[{"tool": 5}]', '[{"tool": ""}]',
    '[{"tool": "echo", "args": [1]}]', '[{"tool": "echo", "args": null}]',
    '[{"tool": "echo"}, {"tool": "ping", "arguments": {}}]',      # a misspelled key must not drop the arguments quietly
])
def test_mcp_batch_bad_file_starts_nothing(mcp_cli, bridge_dir, tmp_path, capsys, content):
    code, out = run_json(capsys, ["mcp", "batch", write_batch(tmp_path, content)])
    assert code == 1 and out["code"] == "bad_batch"
    assert mcp_cli == [] and lease_free(bridge_dir)


def test_mcp_batch_with_no_calls_runs_nothing(mcp_cli, bridge_dir, tmp_path, capsys):
    code, out = run_json(capsys, ["mcp", "batch", write_batch(tmp_path, [])])
    assert code == 0 and out == {"ok": True, "results": [], "ran": 0, "total": 0}
    assert mcp_cli == [] and lease_free(bridge_dir)


def test_mcp_batch_missing_file(mcp_cli, tmp_path, capsys):
    code, out = run_json(capsys, ["mcp", "batch", str(tmp_path / "none.json")])
    assert code == 1 and out["code"] == "bad_batch" and mcp_cli == []


def test_mcp_list_query_and_describe(mcp_cli, bridge_dir, capsys):
    code, out = run_json(capsys, ["mcp", "list"])
    assert code == 0 and out["count"] == 5
    assert out["tools"][1] == {"name": "echo", "description": "Return the arguments."}
    code, out = run_json(capsys, ["mcp", "list", "--query", "PING"])
    assert code == 0 and out["count"] == 1 and out["tools"][0]["name"] == "ping"
    code, out = run_json(capsys, ["mcp", "list", "--query", "returns NOTHING"])
    assert code == 0 and out == {"ok": True, "tools": [], "count": 0}
    code, out = run_json(capsys, ["mcp", "list", "--query", "ARGUMENTS"])      # a match in the description only
    assert out["count"] == 1 and out["tools"][0]["name"] == "echo"
    code, out = run_json(capsys, ["mcp", "describe", "echo"])
    assert code == 0
    assert out["name"] == "echo" and out["description"] == "Return the arguments."
    assert out["input_schema"]["type"] == "object"
    code, out = run_json(capsys, ["mcp", "describe", "nope"])
    assert code == 1 and out["code"] == "tool_not_found"
    assert lease_free(bridge_dir)


def test_mcp_list_and_describe_human_output(mcp_cli, capsys):
    assert dispatch(["mcp", "list"]) == 0
    out = capsys.readouterr().out
    assert "ping  Check the connection to Premiere." in out and "5 tools" in out
    assert dispatch(["mcp", "describe", "echo"]) == 0
    out = capsys.readouterr().out
    assert "echo" in out and "Return the arguments." in out and '"type": "object"' in out


def test_mcp_help_lists_the_commands(capsys):
    assert dispatch(["mcp", "--help"]) == 0
    out = capsys.readouterr().out
    for name in ("list", "describe", "call", "batch"):
        assert f"\n  {name} " in out


def test_every_mcp_command_has_lease_wait_and_json(capsys):
    for name in ("list", "describe", "call", "batch"):
        assert dispatch(["mcp", name, "--help"]) == 0
        out = capsys.readouterr().out
        assert "--lease-wait" in out and "--json" in out, name


# ---------- final review fixes: A1 ----------
def test_an_interrupt_in_close_kills_the_server(bridge_dir, monkeypatch, keep_signals):  # noqa: F811
    from cli_anything.premiere.core import bridge
    monkeypatch.setattr(mcp_client, "CLOSE_WAIT_S", 10.0)
    s = McpSession(BridgeClient(bridge_dir), python(STUBBORN_SERVER))
    install_signal_handlers()
    s.start()
    with signal_when(signal.SIGTERM, lambda: s._closed) as sent:
        with pytest.raises(bridge.Interrupted):
            s.close()
    assert sent["fired"]
    assert s._proc.poll() is not None           # the child was killed, not left running
    assert lease_free(bridge_dir)


@pytest.mark.parametrize("argv", [["mcp", "list"], ["mcp", "describe", "echo"], ["mcp", "call", "echo"],
                                  ["mcp", "batch", "BATCH"]])
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
def test_an_interrupt_before_the_server_starts_is_reported(mcp_cli, bridge_dir, tmp_path, capsys, keep_signals,  # noqa: F811
                                                         argv, signum):
    argv = [write_batch(tmp_path, [{"tool": "ping"}]) if a == "BATCH" else a for a in argv]
    before = signal.getsignal(signum)
    with BridgeClient(bridge_dir).lease():
        with signal_when(signum, lambda: signal.getsignal(signum) is not before) as sent:
            code, out = run_json(capsys, [*argv, "--lease-wait", "5"])
        assert (bridge_dir / LOCK).exists()
    assert sent["fired"]
    assert code == 1 and out["ok"] is False and out["code"] == "interrupted"
    assert mcp_cli and all(session._proc is None for session in mcp_cli)    # no server was started
    assert lease_free(bridge_dir)


# ---------- final review fixes: A5 ----------
BANNED_WORDS = ("delve", "leverage", "robust", "seamless", "comprehensive", "crucial")


def help_text(capsys, argv) -> str:
    assert dispatch([*argv, "--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())        # click wraps lines: compare on single spaces
    assert "\u2014" not in text and not any(word in text.lower() for word in BANNED_WORDS)
    return text


@pytest.mark.parametrize("argv", [["mcp"], ["mcp", "call"], ["mcp", "batch"]])
def test_mcp_help_says_the_write_checks_do_not_run(capsys, argv):
    text = help_text(capsys, argv)
    assert "The write checks of `live run --write` do not run here." in text
    assert "The server applies its own rules." in text


@pytest.mark.parametrize("argv", [["mcp", "call"], ["mcp", "batch"]])
def test_mcp_timeout_help_says_when_to_raise_it_and_what_happens(capsys, argv):
    text = help_text(capsys, argv)
    assert "Raise it for exports." in text
    assert "the CLI stops the server, even in the middle of its tool" in text
    assert "may find an open recovery" in text
