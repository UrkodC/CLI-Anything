"""Tests for `live frame export` and `live app`: the recipe (core/recipes/frames.py), its script
(core/scripts/app_switch.jsx) and the commands (live_recipes.py).

The scripts are answered by a fake panel that plays a small Premiere in Python (AppModel), and the exports by
the fake MCP server of fakes.py (or, for the faults it cannot play, by a scripted session object). The script
itself runs in node with a stub `app`.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from cli_anything.premiere import live_cli
from cli_anything.premiere.core import bridge, guard, jsx, mcp_client
from cli_anything.premiere.core.bridge import BridgeClient, Lease
from cli_anything.premiere.core.mcp_client import McpSession
from cli_anything.premiere.core.recipes import frames
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.premiere_cli import dispatch
from cli_anything.premiere.tests.fakes import (  # noqa: F401
    RECIPE_STATE, RECIPE_STATE_MARK, FakePanel, bridge_dir, fake_mcp_command, keep_signals, recipe_handler,
    script_args)
from cli_anything.premiere.tests.test_jsx import BANNED, NODE, STUB_HELPERS, needs_node

MARK = "recipe: app_switch"
LOCK = ".host-owner-lease"
MARKER_FILE = ".host-recovery.json"
TPS = 254016000000
PROJECT = "/p/X Claude.prproj"
EXPORT = ["live", "frame", "export"]
PNG_WARNING = "A PNG still can carry alpha. Composite it over black before you measure it."
RESERVED = ("abstract", "boolean", "byte", "char", "class", "const", "default", "double", "enum", "export", "extends",
            "final", "float", "goto", "implements", "import", "int", "interface", "long", "native", "package",
            "private", "protected", "public", "short", "static", "super", "synchronized", "throws", "transient",
            "volatile", "delete", "in", "new", "this", "typeof", "void", "with")


# ---------- a small Premiere behind the fake panel ----------
class AppModel:
    """Plays app_switch.jsx and state.jsx. Values stay between calls.

    proxies: 1, 0 or None (unreadable). active: the ID of the active sequence or None. writes lists every
    switch the scripts made, in order: ("proxies", 1 or 0) or ("activate", id). activate_takes=False: setting
    the active sequence changes nothing. state_active: what state.jsx shows as active, when set (else the
    real one). project_path: the path of the active project (state.jsx and __expect use it).
    """

    def __init__(self, proxies=1, active="o1") -> None:
        self.proxies = proxies
        self.active = active
        self.sequences = {"s1": {"name": "Main", "end_ticks": str(round(8.008 * TPS))},
                          "o1": {"name": "Old", "end_ticks": str(round(60 * TPS))}}
        self.writes: list[tuple] = []
        self.reads = 0
        self.activate_takes = True
        self.state_active: dict | None = None
        self.project_name = "X Claude.prproj"
        self.project_path = PROJECT
        self.state_version = "27.0.0"

    def ref(self, seq_id):
        if seq_id is None:
            return None
        return {"id": seq_id, "name": self.sequences[seq_id]["name"]}

    def state(self) -> dict:
        active = self.state_active if self.state_active is not None else self.ref(self.active)
        return {**RECIPE_STATE, "version": self.state_version,
                "projects": [{"name": self.project_name, "path": self.project_path, "active": True}],
                "active_sequence": active}

    def _expect(self, args: dict):
        expect = args.get("expect") or {}
        if expect.get("project_path") != self.project_path:
            raise Exception(f"Error: precondition: the active project is {self.project_path}, not "
                            f"{expect.get('project_path')}")
        seq_id = expect.get("sequence_id")
        if seq_id is None:
            return None
        if seq_id not in self.sequences:
            raise Exception(f"Error: precondition: no sequence has the ID {seq_id}")
        name = expect.get("sequence_name")
        if name is not None and name != self.sequences[seq_id]["name"]:
            raise Exception(f"Error: precondition: sequence {seq_id} is named {self.sequences[seq_id]['name']}, not {name}")
        return seq_id

    def app_switch(self, script: str):
        args = script_args(script)
        try:
            expected = self._expect(args)
        except Exception as exc:
            return exc
        action = args.get("action")
        if action == "read":
            self.reads += 1
            seq = None
            if expected is not None:
                seq = {**self.ref(expected), "end_ticks": self.sequences[expected]["end_ticks"]}
            return {"proxies": self.proxies, "active_sequence": self.ref(self.active), "sequence": seq}
        if action == "proxies":
            before = self.proxies
            self.writes.append(("proxies", 1 if args["enabled"] else 0))
            self.proxies = 1 if args["enabled"] else 0
            return {"before": before, "after": self.proxies, "set_error": None}
        if action == "activate":
            wanted = args.get("sequence_id")
            if wanted not in self.sequences:
                return Exception(f"Error: precondition: no sequence has the ID {wanted}")
            before = self.ref(self.active)
            self.writes.append(("activate", wanted))
            if self.activate_takes:
                self.active = wanted
            return {"before": before, "after": self.ref(self.active), "set_error": None}
        return Exception(f"Error: precondition: the action must be read, proxies or activate, not {action}")

    def handler(self):
        inner = recipe_handler({MARK: self.app_switch})

        def handle(script: str) -> str:
            if RECIPE_STATE_MARK in script:
                return json.dumps({"success": True, "data": self.state()})
            return inner(script)
        return handle


# ---------- fixtures ----------
@pytest.fixture(autouse=True)
def _keep_signal_handlers(keep_signals):  # noqa: F811
    yield


@pytest.fixture(autouse=True)
def _no_real_bridge(monkeypatch, tmp_path):
    monkeypatch.setenv("PREMIERE_TEMP_DIR", str(tmp_path / "not-the-real-bridge"))
    monkeypatch.delenv("PREMIERE_MCP_CAPABILITIES", raising=False)
    monkeypatch.setattr(mcp_client, "CLOSE_WAIT_S", 0.3)


@pytest.fixture
def live(bridge_dir, monkeypatch, tmp_path):  # noqa: F811
    """The live commands on a temp bridge folder, with the fake MCP server. `live.sessions` lists every MCP
    session the commands built."""
    command = fake_mcp_command(tmp_path, bridge_dir)
    sessions: list = []

    def make_session(client):
        sessions.append(McpSession(client, command))
        return sessions[-1]
    monkeypatch.setattr(live_cli, "make_client", lambda wait, **kw: BridgeClient(bridge_dir, wait, **kw))
    monkeypatch.setattr(live_cli, "make_session", make_session)
    monkeypatch.setattr(guard, "panel_version", lambda *a, **k: "1.25.0")
    monkeypatch.setattr(guard, "server_version", lambda *a, **k: "1.25.0")
    return sessions


@contextlib.contextmanager
def panel_on(bridge_dir, model):  # noqa: F811
    panel = FakePanel(bridge_dir, handler=model.handler())
    panel.start()
    try:
        yield panel
    finally:
        panel.stop()


def run_json(capsys, argv) -> tuple[int, dict]:
    code = dispatch(["--json", *argv])
    return code, json.loads(capsys.readouterr().out)


def calls(tmp_path) -> list[dict]:
    """Every export_frame call the fake server got: {"arguments", "lease_held"}."""
    path = Path(tmp_path) / "fake_mcp_calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def lease_free(bridge_dir) -> bool:  # noqa: F811
    return not (Path(bridge_dir) / LOCK).exists()


def export(bridge_dir, capsys, model, out_dir, *argv, sequence="s1"):  # noqa: F811
    with panel_on(bridge_dir, model) as panel:
        code, out = run_json(capsys, [*EXPORT, "--sequence", sequence, "--out-dir", str(out_dir), *argv])
    return code, out, panel


def app_scripts(panel) -> list[dict]:
    return [script_args(s) for s in panel.seen if MARK in s]


# ---------- the export ----------
def test_frames_are_exported_and_checked(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel()
    out_dir = tmp_path / "out"
    code, out, panel = export(bridge_dir, capsys, model, out_dir, "--time", "1", "--time", "2.5", "--time", "4")
    assert code == 0, out
    assert out.pop("ok") is True
    assert list(out) == ["sequence", "frames", "exported", "total", "proxies", "active_sequence", "left_switched",
                         "stored", "rendered", "warnings"]
    names = ["frame_00001000ms.png", "frame_00002500ms.png", "frame_00004000ms.png"]
    assert [Path(f["path"]).name for f in out["frames"]] == names
    for frame, seconds in zip(out["frames"], (1.0, 2.5, 4.0)):
        assert frame["time_seconds"] == seconds
        assert Path(frame["path"]).parent == out_dir
        assert frame["bytes"] == os.path.getsize(frame["path"]) and frame["bytes"] > 0
    assert out["exported"] == 3 and out["total"] == 3
    assert out["sequence"] == {"id": "s1", "name": "Main"}
    assert out["stored"] is None and out["rendered"] is None and out["left_switched"] is None
    assert PNG_WARNING in out["warnings"]
    seen = calls(tmp_path)
    assert [c["arguments"] for c in seen] == [
        {"output_path": str(out_dir / name), "time_seconds": seconds, "expected_project_path": PROJECT,
         "expected_cep_target": {"backend": "cep", "sequence_id": "s1"}}
        for name, seconds in zip(names, (1.0, 2.5, 4.0))]
    assert len(live) == 1                       # one MCP session for all frames
    assert lease_free(bridge_dir)


def test_output_folder_is_made(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    out_dir = tmp_path / "new" / "deeper" / "stills"
    code, out, _ = export(bridge_dir, capsys, AppModel(), out_dir, "--time", "1")
    assert code == 0, out
    assert out_dir.is_dir() and (out_dir / "frame_00001000ms.png").is_file()


def test_relative_out_dir_becomes_absolute(live, bridge_dir, capsys, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.chdir(tmp_path)
    code, out, _ = export(bridge_dir, capsys, AppModel(), "rel", "--time", "1")
    assert code == 0, out
    assert out["frames"][0]["path"] == str(tmp_path / "rel" / "frame_00001000ms.png")
    assert calls(tmp_path)[0]["arguments"]["output_path"] == str(tmp_path / "rel" / "frame_00001000ms.png")


def test_unmounted_volume_fails_before_any_contact(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    code, out, panel = export(bridge_dir, capsys, AppModel(), "/Volumes/NoSuchDisk/x", "--time", "1")
    assert code == 1 and out["code"] == "output_dir"
    assert "The disk NoSuchDisk is not mounted" in out["error"]
    assert panel.seen == [] and live == [] and calls(tmp_path) == []
    assert not Path("/Volumes/NoSuchDisk").exists()


def test_out_dir_that_is_a_file_is_refused(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    blocker = tmp_path / "file"
    blocker.write_text("x")
    code, out, panel = export(bridge_dir, capsys, AppModel(), blocker / "sub", "--time", "1")
    assert code == 1 and out["code"] == "output_dir"
    assert panel.seen == [] and live == []


def test_existing_file_is_refused(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    old = out_dir / "frame_00002000ms.png"
    old.write_bytes(b"old still")
    code, out, panel = export(bridge_dir, capsys, AppModel(), out_dir, "--time", "1", "--time", "2")
    assert code == 1 and out["code"] == "output_exists"
    assert str(old) in out["error"] and "--overwrite" in out["error"]
    assert panel.seen == [] and live == [] and calls(tmp_path) == []
    assert not (out_dir / "frame_00001000ms.png").exists()
    code, out, _ = export(bridge_dir, capsys, AppModel(), out_dir, "--time", "1", "--time", "2", "--overwrite")
    assert code == 0, out
    assert old.read_bytes() != b"old still" and out["exported"] == 2


def test_overwrite_of_a_file_the_tool_did_not_replace_is_a_failure(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "NOFILE_00001000ms.png").write_bytes(b"an old still")
    code, out, _ = export(bridge_dir, capsys, AppModel(), out_dir, "--time", "1", "--prefix", "NOFILE", "--overwrite")
    assert code == 1 and out["code"] == "export_failed"
    assert "not replaced" in out["error"] and out["frames"] == []


def test_name_with_space_quote_and_umlaut(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    out_dir = tmp_path / "Stills für \"Film\" 1"
    prefix = 'a "b" ü'
    code, out, _ = export(bridge_dir, capsys, AppModel(), out_dir, "--time", "1", "--prefix", prefix)
    assert code == 0, out
    wanted = str(out_dir / f"{prefix}_00001000ms.png")
    assert out["frames"][0]["path"] == wanted
    assert calls(tmp_path)[0]["arguments"]["output_path"] == wanted
    assert Path(wanted).is_file()


@pytest.mark.parametrize("fmt", ["jpg", "tif"])
def test_format_picks_the_extension(live, bridge_dir, capsys, tmp_path, fmt):  # noqa: F811
    code, out, _ = export(bridge_dir, capsys, AppModel(), tmp_path / "o", "--time", "1", "--format", fmt)
    assert code == 0, out
    assert out["frames"][0]["path"].endswith(f"frame_00001000ms.{fmt}")
    assert PNG_WARNING not in out["warnings"]


def test_time_after_the_end_is_refused(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel()
    code, out, panel = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1", "--time", "9")
    assert code == 1 and out["code"] == "bad_time"
    assert "9" in out["error"] and "8.008" in out["error"]
    assert model.writes == [] and calls(tmp_path) == [] and live == []
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "8.008")    # the end itself
    assert code == 1 and out["code"] == "bad_time" and model.writes == []
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "8.0079")   # the last frame before it
    assert code == 0, out


@pytest.mark.parametrize("argv, code_name", [
    ([], "bad_time"),
    (["--time", "-1"], "bad_time"),
    (["--time", "nan"], "bad_time"),
    (["--time", "inf"], "bad_time"),
    (["--time", "1", "--time", "1.0004"], "bad_time"),
    (["--time", "1", "--prefix", "a/b"], "bad_args"),
    (["--time", "1", "--prefix", ""], "bad_args"),
])
def test_bad_arguments_fail_before_any_contact(live, bridge_dir, capsys, tmp_path, argv, code_name):  # noqa: F811
    code, out, panel = export(bridge_dir, capsys, AppModel(), tmp_path / "o", *argv)
    assert code == 1 and out["code"] == code_name, out
    assert panel.seen == [] and live == []


def test_duplicate_times_name_both(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    code, out, _ = export(bridge_dir, capsys, AppModel(), tmp_path / "o", "--time", "2", "--time", "2.0001")
    assert code == 1 and out["code"] == "bad_time" and "same file name" in out["error"]


def test_proxies_are_switched_off_and_put_back(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=1, active="s1")
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 0, out
    assert model.writes == [("proxies", 0), ("proxies", 1)]
    assert out["proxies"] == {"before": True, "during": False, "after": True}
    assert out["active_sequence"] == {"before": "s1", "after": "s1"}
    assert model.proxies == 1


def test_proxies_already_off_are_left_alone(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=0, active="s1")
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 0, out
    assert model.writes == []
    assert out["proxies"] == {"before": False, "during": False, "after": False}


def test_proxies_keep(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=1, active="s1")
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1", "--proxies", "keep")
    assert code == 0, out
    assert model.writes == []
    assert out["proxies"] == {"before": True, "during": True, "after": True}


def test_unreadable_proxies_are_refused_unless_kept(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=None, active="o1")
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 1 and out["code"] == "not_switched" and "--proxies keep" in out["error"]
    assert model.writes == [] and calls(tmp_path) == []
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1", "--proxies", "keep")
    assert code == 0, out
    assert out["proxies"] == {"before": None, "during": None, "after": None}


def test_other_active_sequence_is_switched_and_put_back(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=0, active="o1")
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 0, out
    assert model.writes == [("activate", "s1"), ("activate", "o1")]
    assert out["active_sequence"] == {"before": "o1", "after": "o1"}
    assert model.active == "o1"


def test_both_switches_are_put_back_in_reverse_order(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=1, active="o1")
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 0, out
    assert model.writes == [("proxies", 0), ("activate", "s1"), ("activate", "o1"), ("proxies", 1)]
    assert out["proxies"] == {"before": True, "during": False, "after": True}


def test_no_active_sequence_before_stays_on_the_target(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=0, active=None)
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 0, out
    assert model.writes == [("activate", "s1")]
    assert out["active_sequence"] == {"before": None, "after": "s1"}
    assert any("No sequence was active" in w for w in out["warnings"])
    assert out["left_switched"] is None


def test_switch_that_does_not_take(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=1, active="o1")
    model.activate_takes = False
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 1 and out["code"] == "not_switched"
    assert calls(tmp_path) == [] and live == []
    assert model.writes == [("proxies", 0), ("activate", "s1"), ("proxies", 1)]     # proxies put back at once
    assert out["proxies"]["after"] is True and out["left_switched"] is None
    assert lease_free(bridge_dir)


def test_switch_that_the_state_does_not_confirm(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=1, active="o1")
    model.state_active = {"id": "o1", "name": "Old"}        # the script says s1, the state read says o1
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 1 and out["code"] == "not_switched"
    assert "Old" in out["error"] or "o1" in out["error"]
    assert calls(tmp_path) == [] and live == []
    assert model.writes[:2] == [("proxies", 0), ("activate", "s1")]
    assert ("proxies", 1) in model.writes[2:]
    assert model.proxies == 1


def test_activation_whose_script_read_back_differs(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    """The script reads back the old sequence while the state read would show the target: the script's read-back
    alone stops the export."""
    model = AppModel(proxies=0, active="o1")
    model.activate_takes = False
    model.state_active = {"id": "s1", "name": "Main"}
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 1 and out["code"] == "not_switched"
    assert "did not become active" in out["error"]
    assert calls(tmp_path) == [] and live == []


def test_proxies_that_do_not_switch_off(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=1, active="o1")
    original = model.app_switch

    def stuck(script):
        reply = original(script)
        if isinstance(reply, dict) and "set_error" in reply and script_args(script)["action"] == "proxies":
            model.proxies = 1
            reply = {**reply, "after": 1, "set_error": "Illegal Parameter type"}
        return reply
    model.app_switch = stuck
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 1 and out["code"] == "not_switched"
    assert "did not switch off" in out["error"] and "Illegal Parameter type" in out["error"]
    assert model.writes == [("proxies", 0)]           # no activation, and nothing to put back
    assert calls(tmp_path) == [] and live == [] and out["left_switched"] is None


def test_no_mcp_call_under_the_lease(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    code, out, _ = export(bridge_dir, capsys, AppModel(), tmp_path / "o", "--time", "1", "--time", "2", "--time", "3")
    assert code == 0, out
    seen = calls(tmp_path)
    assert len(seen) == 3 and [c["lease_held"] for c in seen] == [False, False, False]


def test_failed_export_stops_and_puts_back(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=1, active="o1")
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1", "--time", "2", "--time", "3",
                          "--prefix", "FAILSECOND")
    assert code == 1 and out["code"] == "export_failed"
    assert "fake export failure" in out["error"] and "Frame 2 of 3" in out["error"]
    assert len(out["frames"]) == 1 and out["exported"] == 1 and out["total"] == 3
    assert len(calls(tmp_path)) == 2                      # the third was not tried
    assert model.writes == [("proxies", 0), ("activate", "s1"), ("activate", "o1"), ("proxies", 1)]
    assert out["proxies"]["after"] is True and out["active_sequence"]["after"] == "o1"
    assert out["left_switched"] is None
    assert lease_free(bridge_dir)


def test_failed_export_human_output(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=1, active="o1")
    with panel_on(bridge_dir, model):
        code = dispatch([*EXPORT, "--sequence", "s1", "--out-dir", str(tmp_path / "o"), "--time", "1", "--time", "2",
                         "--prefix", "FAILSECOND"])
    captured = capsys.readouterr()
    assert code == 1
    assert f"1.000 s -> {tmp_path / 'o' / 'FAILSECOND_00001000ms.png'} (" in captured.out
    assert "Error: Frame 2 of 2" in captured.err


@pytest.mark.parametrize("prefix, text", [("EMPTY", "empty"), ("NOFILE", "no file"), ("OTHERPATH", "elsewhere")])
def test_file_check_failures(live, bridge_dir, capsys, tmp_path, prefix, text):  # noqa: F811
    model = AppModel(proxies=1, active="s1")
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1", "--prefix", prefix)
    assert code == 1 and out["code"] == "export_failed"
    assert text in out["error"]
    assert out["frames"] == [] and model.writes == [("proxies", 0), ("proxies", 1)]


def test_empty_file_is_a_failure(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    code, out, _ = export(bridge_dir, capsys, AppModel(), tmp_path / "o", "--time", "1", "--prefix", "EMPTY")
    assert code == 1 and out["code"] == "export_failed" and out["frames"] == []


def test_guard_refusal_says_what_changed(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    """Like the real server, the fake writes a recovery marker with the guard refusal: nothing is put back."""
    model = AppModel(proxies=1, active="o1")
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1", "--prefix", "GUARD")
    assert code == 1 and out["code"] == "export_failed"
    assert "active sequence or the project changed during the export" in out["error"]
    assert "A recovery is now open" in out["error"] and "Expected target guard refused" in out["error"]
    assert (bridge_dir / MARKER_FILE).exists()
    assert model.writes == [("proxies", 0), ("activate", "s1")]          # no write after the refusal
    assert out["left_switched"] == {"proxies": False, "active_sequence": {"id": "s1", "name": "Main"}}
    lines = [w for w in out["warnings"] if "cli-anything-premiere live app" in w]
    assert len(lines) == 2
    assert lines[0].endswith("live app activate --sequence o1") and lines[1].endswith("live app proxies on")


def test_marker_blocks_the_put_back(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=1, active="o1")
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1", "--prefix", "MARKER")
    assert code == 1 and out["code"] == "export_failed"
    assert (bridge_dir / MARKER_FILE).exists()
    assert model.writes == [("proxies", 0), ("activate", "s1")]          # nothing written after the marker
    assert out["left_switched"] == {"proxies": False, "active_sequence": {"id": "s1", "name": "Main"}}
    assert out["proxies"] == {"before": True, "during": False, "after": False}
    assert out["active_sequence"] == {"before": "o1", "after": "s1"}
    lines = [w for w in out["warnings"] if "cli-anything-premiere live app" in w]
    assert len(lines) == 2
    assert lines[0].endswith("cli-anything-premiere live app activate --sequence o1")
    assert lines[1].endswith("cli-anything-premiere live app proxies on")
    assert all("recovery" in line for line in lines)
    assert lease_free(bridge_dir)


def test_marker_human_output_shows_the_lines(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=1, active="o1")
    with panel_on(bridge_dir, model):
        code = dispatch([*EXPORT, "--sequence", "s1", "--out-dir", str(tmp_path / "o"), "--time", "1",
                         "--prefix", "MARKER"])
    err = capsys.readouterr().err
    assert code == 1
    assert "Warning: " in err and "cli-anything-premiere live app proxies on" in err
    assert "cli-anything-premiere live app activate --sequence o1" in err


def test_the_put_back_lines_carry_the_guard_options(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=1, active="o1")
    model.project_name, model.project_path = "Family film.prproj", "/p/Family film.prproj"
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1", "--prefix", "MARKER",
                          "--project", "/p/Family film.prproj", "--any-project")
    assert code == 1 and out["code"] == "export_failed"
    lines = [w for w in out["warnings"] if "cli-anything-premiere live app" in w]
    assert lines[1].endswith("live app proxies on --project '/p/Family film.prproj' --any-project")


def test_mcp_timeout_stops_the_run_and_puts_back(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=1, active="o1")
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1", "--time", "2", "--prefix", "SLOW",
                          "--timeout", "1")
    assert code == 1 and out["code"] == "mcp_timeout"
    assert out["frames"] == [] and len(calls(tmp_path)) == 1
    assert model.writes == [("proxies", 0), ("activate", "s1"), ("activate", "o1"), ("proxies", 1)]
    assert lease_free(bridge_dir)


def test_dry_run_exports_nothing(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=1, active="o1")
    code, out, panel = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1", "--time", "2", "--dry-run")
    assert code == 0, out
    assert out["dry_run"] is True
    assert [Path(p["path"]).name for p in out["planned"]] == ["frame_00001000ms.png", "frame_00002000ms.png"]
    assert out["proxies"] == {"before": True, "switch": True}
    assert out["active_sequence"] == {"before": "o1", "switch": True}
    assert model.writes == [] and live == [] and calls(tmp_path) == []
    assert all(a["action"] == "read" for a in app_scripts(panel))
    assert not (tmp_path / "o").exists()                    # a dry run makes no folder
    assert out["out_dir"] == {"path": str(tmp_path / "o"), "exists": False}
    assert lease_free(bridge_dir)


def test_dry_run_with_an_existing_folder_still_refuses_existing_files(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    out_dir = tmp_path / "o"
    out_dir.mkdir()
    code, out, _ = export(bridge_dir, capsys, AppModel(), out_dir, "--time", "1", "--dry-run")
    assert code == 0 and out["out_dir"] == {"path": str(out_dir), "exists": True}
    (out_dir / "frame_00001000ms.png").write_bytes(b"old")
    code, out, panel = export(bridge_dir, capsys, AppModel(), out_dir, "--time", "1", "--dry-run")
    assert code == 1 and out["code"] == "output_exists" and panel.seen == []


def test_dry_run_human_output(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    with panel_on(bridge_dir, AppModel()):
        code = dispatch([*EXPORT, "--sequence", "s1", "--out-dir", str(tmp_path / "o"), "--time", "1", "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Dry run: nothing was switched or exported." in out
    assert "1.000 s -> " in out
    assert f"The folder {tmp_path / 'o'} does not exist yet. The real run will make it." in out
    assert not (tmp_path / "o").exists()


def test_own_film_is_refused(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel()
    model.project_name, model.project_path = "Family film.prproj", "/p/Family film.prproj"
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 1 and out["code"] == "project_guard"
    assert model.writes == [] and model.reads == 0 and live == [] and calls(tmp_path) == []


def test_recovery_marker_before_the_start_refuses(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    (bridge_dir / MARKER_FILE).write_text('{"version":1,"reason":"unknown_command_outcome","nonce":"x"}')
    model = AppModel()
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 1 and out["code"] == "recovery_required"
    assert model.writes == [] and live == []


def test_wrong_sequence_name_is_refused(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel()
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1", "--sequence-name", "Other")
    assert code == 1 and out["code"] == "precondition"
    assert out["error"] == "sequence s1 is named Main, not Other"
    assert model.writes == [] and live == []


def test_human_output(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel(proxies=1, active="o1")
    with panel_on(bridge_dir, model):
        code = dispatch([*EXPORT, "--sequence", "s1", "--out-dir", str(tmp_path / "o"), "--time", "1", "--time", "2.5"])
    out = capsys.readouterr().out
    assert code == 0
    lines = out.strip().splitlines()
    size = os.path.getsize(tmp_path / "o" / "frame_00001000ms.png")
    assert lines[0] == f"1.000 s -> {tmp_path / 'o' / 'frame_00001000ms.png'} ({size} bytes)"
    assert lines[1].startswith("2.500 s -> ")
    assert lines[2] == "Proxy display: off for the export, then on again."
    assert lines[3] == "Active sequence: Main (s1) for the export, then o1 again."
    assert lines[4] == f"Warning: {PNG_WARNING}"


def test_help_says_what_it_proves_and_switches():
    from click.testing import CliRunner
    from cli_anything.premiere.live_cli import live as live_group
    result = CliRunner().invoke(live_group, ["frame", "export", "--help"])
    text = " ".join(result.output.split())
    assert result.exit_code == 0
    assert "file check: each file exists and is not empty. It is not a check of the picture." in text
    assert "makes the output folder" in text
    assert "proxy display off" in text and "puts it back" in text
    assert "active" in text and "puts the old one back" in text
    assert "live app proxies on" in text
    assert "600" in text
    assert "\u2014" not in result.output
    for name in ("proxies", "activate"):
        sub = CliRunner().invoke(live_group, ["app", name, "--help"])
        assert sub.exit_code == 0 and "\u2014" not in sub.output


# ---------- the recipe, with a scripted MCP session ----------
class ScriptedSession:
    """Stands in for McpSession. Each call writes a small file (unless `on_call` says otherwise) and returns the
    reply shape of McpSession.call. on_call(number, arguments) may raise or return a reply. on_exit runs when the
    session closes."""

    def __init__(self, client, on_call=None, on_exit=None) -> None:
        self.client = client
        self.on_call = on_call
        self.on_exit = on_exit
        self.calls: list[dict] = []
        self.lease_held: list[bool] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.on_exit:
            self.on_exit()

    def call(self, name, arguments, timeout_s=120.0):
        assert name == "export_frame"
        self.calls.append(arguments)
        self.lease_held.append((Path(self.client.bridge_dir) / LOCK).exists())
        if self.on_call:
            reply = self.on_call(len(self.calls), arguments)
            if reply is not None:
                return reply
        Path(arguments["output_path"]).write_bytes(b"still")
        return {"is_error": False, "text": "", "structured": {"ok": True, "tool": "export_frame",
                                                               "data": {"outputPath": arguments["output_path"]}}}


def run_recipe(bridge_dir, model, tmp_path, *, on_call=None, on_exit=None, times=(1.0, 2.0), lease_wait=0.0,  # noqa: F811
               **kw):
    sessions: list[ScriptedSession] = []

    def make(client):
        sessions.append(ScriptedSession(client, on_call, on_exit))
        return sessions[-1]
    client = BridgeClient(bridge_dir, lease_wait)
    with panel_on(bridge_dir, model):
        try:
            result = frames.export_frames(client, make, sequence_id="s1", times=list(times), out_dir=tmp_path / "o",
                                          **kw)
        except CliError as err:
            return err, sessions
    return result, sessions


@pytest.fixture
def recipe(bridge_dir, monkeypatch):  # noqa: F811
    monkeypatch.setattr(guard, "panel_version", lambda *a, **k: "1.25.0")
    return bridge_dir


def test_recipe_interrupt_during_the_export_still_puts_back(recipe, tmp_path):
    model = AppModel(proxies=1, active="o1")

    def stop(number, arguments):
        raise KeyboardInterrupt
    err, sessions = run_recipe(recipe, model, tmp_path, on_call=stop)
    assert isinstance(err, CliError) and err.code == "interrupted"
    assert model.writes == [("proxies", 0), ("activate", "s1"), ("activate", "o1"), ("proxies", 1)]
    assert err.payload["frames"] == [] and err.payload["left_switched"] is None
    assert lease_free(recipe)


def test_recipe_bridge_interrupt_when_the_session_starts(recipe, tmp_path):
    model = AppModel(proxies=1, active="o1")

    def make(client):
        raise bridge.Interrupted()
    client = BridgeClient(recipe, 0.0)
    with panel_on(recipe, model):
        with pytest.raises(CliError) as err:
            frames.export_frames(client, make, sequence_id="s1", times=[1.0], out_dir=tmp_path / "o")
    assert err.value.code == "interrupted"
    assert model.writes == [("proxies", 0), ("activate", "s1"), ("activate", "o1"), ("proxies", 1)]
    assert lease_free(recipe)


def test_recipe_mcp_died(recipe, tmp_path):
    model = AppModel(proxies=1, active="o1")

    def died(number, arguments):
        raise CliError("mcp_died", "The MCP server stopped.")
    err, _ = run_recipe(recipe, model, tmp_path, on_call=died)
    assert err.code == "mcp_died"
    assert model.writes[-2:] == [("activate", "o1"), ("proxies", 1)]
    assert err.payload["left_switched"] is None


def test_recipe_scripted_session_never_sees_the_lease(recipe, tmp_path):
    result, sessions = run_recipe(recipe, AppModel(), tmp_path, times=(1.0, 2.0, 3.0))
    assert result["exported"] == 3
    assert sessions[0].lease_held == [False, False, False]


def test_recipe_put_back_lease_busy(recipe, tmp_path):
    """Another program takes Premiere during the export and keeps it: nothing is put back, the command says so."""
    model = AppModel(proxies=1, active="o1")
    other = Lease(recipe, wait_s=0.0)
    try:
        result, _ = run_recipe(recipe, model, tmp_path, on_exit=other.acquire)
    finally:
        other.release()
    assert isinstance(result, CliError) and result.code == "not_restored"
    assert result.payload["exported"] == 2
    assert model.writes == [("proxies", 0), ("activate", "s1")]
    assert result.payload["left_switched"] == {"proxies": False, "active_sequence": {"id": "s1", "name": "Main"}}
    lines = [w for w in result.payload["warnings"] if "cli-anything-premiere live app" in w]
    assert len(lines) == 2 and all("lease_busy" in line for line in lines)


def test_recipe_put_back_precondition(recipe, tmp_path):
    """The old sequence was deleted during the export: the proxies go back, the active sequence cannot."""
    model = AppModel(proxies=1, active="o1")

    def delete_old():
        del model.sequences["o1"]
    result, _ = run_recipe(recipe, model, tmp_path, on_exit=delete_old)
    assert isinstance(result, CliError) and result.code == "not_restored"
    assert ("proxies", 1) in model.writes
    assert result.payload["left_switched"] == {"proxies": None, "active_sequence": {"id": "s1", "name": "Main"}}
    lines = [w for w in result.payload["warnings"] if "cli-anything-premiere live app" in w]
    assert len(lines) == 1 and "precondition" in lines[0] and "--sequence o1" in lines[0]


def test_recipe_export_failed_and_put_back_failed_carries_both(recipe, tmp_path):
    model = AppModel(proxies=1, active="o1")

    def fail(number, arguments):
        return {"is_error": True, "text": "fake export failure", "structured": None}

    def delete_old():
        del model.sequences["o1"]
    err, _ = run_recipe(recipe, model, tmp_path, on_call=fail, on_exit=delete_old)
    assert err.code == "export_failed"
    assert err.payload["left_switched"]["active_sequence"] == {"id": "s1", "name": "Main"}
    assert any("could not be put back" in w or "precondition" in w for w in err.payload["warnings"])


def test_recipe_project_changed_during_the_export(recipe, tmp_path):
    """Another Claude project became active meanwhile: session B finds another project and writes nothing."""
    model = AppModel(proxies=1, active="o1")

    def switch_project():
        model.project_name, model.project_path = "Y Claude.prproj", "/p/Y Claude.prproj"
    result, _ = run_recipe(recipe, model, tmp_path, on_exit=switch_project)
    assert isinstance(result, CliError) and result.code == "not_restored"
    assert model.writes == [("proxies", 0), ("activate", "s1")]
    assert result.payload["left_switched"] == {"proxies": False, "active_sequence": {"id": "s1", "name": "Main"}}
    assert any("project_mismatch" in w for w in result.payload["warnings"])


def test_recipe_unknown_outcome_on_a_switch_is_put_back_or_left(recipe, tmp_path):
    """The activate write gets no answer: the outcome is unknown and a marker is written. The proxies are not
    put back (the marker blocks it), and both show in left_switched."""
    model = AppModel(proxies=1, active="o1")
    handler = model.handler()

    def hang(script):
        if MARK in script and script_args(script).get("action") == "activate":
            return None             # never answer
        return handler(script)
    client = BridgeClient(recipe, 0.0)
    panel = FakePanel(recipe, handler=hang)
    panel.start()
    try:
        with pytest.raises(CliError) as err:
            frames.export_frames(client, lambda c: ScriptedSession(c), sequence_id="s1", times=[1.0],
                                 out_dir=tmp_path / "o", hard_limit_s=0.5)
    finally:
        panel.stop()
    assert err.value.code == "unknown_outcome"
    assert (recipe / MARKER_FILE).exists()
    assert model.writes == [("proxies", 0)]
    left = err.value.payload["left_switched"]
    assert left["proxies"] is False and left["active_sequence"] is not None
    assert lease_free(recipe)


def test_export_frames_takes_the_brief_signature():
    import inspect
    params = inspect.signature(frames.export_frames).parameters
    assert list(params)[:2] == ["client", "make_session"]
    assert params["prefix"].default == "frame" and params["fmt"].default == "png"
    assert params["overwrite"].default is False and params["proxies"].default == "off"
    assert params["hard_limit_s"].default == frames.EXPORT_TIMEOUT_S == 600.0
    assert frames.FORMATS == ("png", "jpg", "tif")
    assert frames.frame_name("frame", 1.0, "png") == "frame_00001000ms.png"
    assert frames.frame_name("a b", 12.3456, "jpg") == "a b_00012346ms.jpg"


# ---------- live app ----------
def test_app_proxies_off_and_on(live, bridge_dir, capsys):  # noqa: F811
    model = AppModel(proxies=1)
    with panel_on(bridge_dir, model):
        code, out = run_json(capsys, ["live", "app", "proxies", "off"])
        code2, out2 = run_json(capsys, ["live", "app", "proxies", "on"])
    assert code == 0 and out["proxies"] == {"before": True, "after": False} and out["stored"] is True
    assert out["rendered"] is None
    assert code2 == 0 and out2["proxies"] == {"before": False, "after": True}
    assert model.writes == [("proxies", 0), ("proxies", 1)]
    assert lease_free(bridge_dir)


def test_app_proxies_dry_run_and_marker(live, bridge_dir, capsys):  # noqa: F811
    model = AppModel(proxies=1)
    with panel_on(bridge_dir, model):
        code, out = run_json(capsys, ["live", "app", "proxies", "off", "--dry-run"])
        (bridge_dir / MARKER_FILE).write_text('{"version":1,"reason":"unknown_command_outcome","nonce":"x"}')
        code2, out2 = run_json(capsys, ["live", "app", "proxies", "on"])
    assert code == 0 and out["dry_run"] is True and out["proxies"] == {"before": True}
    assert code2 == 1 and out2["code"] == "recovery_required"
    assert model.writes == []


def test_app_proxies_that_do_not_switch(live, bridge_dir, capsys, monkeypatch):  # noqa: F811
    model = AppModel(proxies=1)
    original = model.app_switch

    def stuck(script):
        reply = original(script)
        if isinstance(reply, dict) and "set_error" in reply:
            model.proxies = 1
            reply = {**reply, "after": 1, "set_error": "Illegal Parameter type"}
        return reply
    model.app_switch = stuck
    with panel_on(bridge_dir, model):
        code, out = run_json(capsys, ["live", "app", "proxies", "off"])
    assert code == 1 and out["code"] == "not_stored"
    assert out["proxies"] == {"before": True, "after": True} and out["stored"] is False
    assert "Illegal Parameter type" in out["error"]


def test_app_activate(live, bridge_dir, capsys):  # noqa: F811
    model = AppModel(active="s1")
    with panel_on(bridge_dir, model):
        code, out = run_json(capsys, ["live", "app", "activate", "--sequence", "o1", "--sequence-name", "Old"])
    assert code == 0, out
    assert out["sequence"] == {"id": "o1", "name": "Old"}
    assert out["active_sequence"] == {"before": {"id": "s1", "name": "Main"}, "after": {"id": "o1", "name": "Old"}}
    assert out["stored"] is True and model.writes == [("activate", "o1")]


def test_app_activate_refusals(live, bridge_dir, capsys):  # noqa: F811
    model = AppModel(active="s1")
    with panel_on(bridge_dir, model):
        code, out = run_json(capsys, ["live", "app", "activate", "--sequence", "o1", "--sequence-name", "Wrong"])
        code2, out2 = run_json(capsys, ["live", "app", "activate", "--sequence", "zz"])
        code3, out3 = run_json(capsys, ["live", "app", "activate", "--sequence", "o1", "--dry-run"])
        model.activate_takes = False
        code4, out4 = run_json(capsys, ["live", "app", "activate", "--sequence", "o1"])
    assert code == 1 and out["code"] == "precondition"
    assert out["error"] == "sequence o1 is named Old, not Wrong"
    assert code2 == 1 and out2["code"] == "precondition"
    assert code3 == 0 and out3["dry_run"] is True and out3["active_sequence"] == {"before": {"id": "s1", "name": "Main"}}
    assert code4 == 1 and out4["code"] == "not_stored" and out4["stored"] is False
    assert model.writes == [("activate", "o1")]       # only the one that did not take


def test_app_activate_state_must_confirm(live, bridge_dir, capsys):  # noqa: F811
    model = AppModel(active="s1")
    model.state_active = {"id": "s1", "name": "Main"}
    with panel_on(bridge_dir, model):
        code, out = run_json(capsys, ["live", "app", "activate", "--sequence", "o1"])
    assert code == 1 and out["code"] == "not_stored"


def test_app_commands_refuse_own_film(live, bridge_dir, capsys):  # noqa: F811
    model = AppModel()
    model.project_name, model.project_path = "Family film.prproj", "/p/Family film.prproj"
    with panel_on(bridge_dir, model):
        code, out = run_json(capsys, ["live", "app", "proxies", "on"])
        code2, out2 = run_json(capsys, ["live", "app", "activate", "--sequence", "s1"])
    assert out["code"] == out2["code"] == "project_guard" and code == code2 == 1
    assert model.writes == []


def test_app_human_output(live, bridge_dir, capsys):  # noqa: F811
    model = AppModel(proxies=0, active="s1")
    with panel_on(bridge_dir, model):
        assert dispatch(["live", "app", "proxies", "on"]) == 0
        first = capsys.readouterr().out
        assert dispatch(["live", "app", "activate", "--sequence", "o1"]) == 0
        second = capsys.readouterr().out
    assert first.splitlines()[0] == "Proxy display: on (was off). Stored: yes"
    assert second.splitlines()[0] == "Active sequence: Old (o1) (was Main (s1)). Stored: yes"


# ---------- app_switch.jsx in node ----------
APP_PRELUDE = STUB_HELPERS + """
var calls = [];
var proxyValue = 1;
var seqA = {sequenceID: "S-A", name: "Cut A", end: "2034048096000"};
var seqB = {sequenceID: "S-B", name: "Cut B", end: "15240960000000"};
var app = {
  getEnableProxies: function () { calls.push("get"); return proxyValue; },
  setEnableProxies: function (v) { calls.push("set " + v); proxyValue = v; },
  project: {path: "/p/X Claude.prproj", sequences: coll("numSequences", [seqA, seqB])}
};
var activeSeq = seqB;
Object.defineProperty(app.project, "activeSequence", {configurable: true,
  get: function () { return activeSeq; },
  set: function (s) { calls.push("activate " + s.sequenceID); activeSeq = s; }
});
"""
APP_EXPECT = {"project_path": "/p/X Claude.prproj", "sequence_id": "S-A", "sequence_name": "Cut A"}


def run_app(args: dict, prelude: str = "") -> tuple[dict, list]:
    script = jsx.build(jsx.load("app_switch"), args, include=("find",))
    code = (APP_PRELUDE + prelude + "\nvar __reply = String(" + script + ");\n"
            + "process.stdout.write(JSON.stringify({reply: __reply, calls: calls}));")
    out = json.loads(subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout)
    return json.loads(out["reply"]), out["calls"]


def test_app_switch_starts_with_its_marker():
    assert jsx.load("app_switch").split("\n")[0] == "// recipe: app_switch"


def test_app_switch_script_is_es3():
    text = jsx.load("app_switch")
    for pattern in BANNED + [r"\.indexOf\(", r"\bObject\.", r"\.reduce\(", r"\.some\(", r"\.every\("]:
        assert not re.search(pattern, text), pattern
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
    assert not re.search(r"[{,]\s*(%s)\s*:" % "|".join(RESERVED), code)
    assert not re.search(r"\.(%s)\b" % "|".join(RESERVED), code)


@needs_node
def test_app_switch_script_is_es3_and_runs_in_node():
    reply, seen = run_app({"action": "read", "expect": APP_EXPECT})
    assert reply == {"success": True, "data": {"proxies": 1, "active_sequence": {"id": "S-B", "name": "Cut B"},
                                               "sequence": {"id": "S-A", "name": "Cut A", "end_ticks": "2034048096000"}}}
    assert seen == ["get"]
    reply, seen = run_app({"action": "proxies", "enabled": False, "expect": APP_EXPECT})
    assert reply["data"] == {"before": 1, "after": 0, "set_error": None} and seen == ["get", "set 0", "get"]
    reply, seen = run_app({"action": "proxies", "enabled": True, "expect": {**APP_EXPECT, "sequence_id": None}},
                          "proxyValue = 0;")
    assert reply["data"] == {"before": 0, "after": 1, "set_error": None}
    reply, seen = run_app({"action": "activate", "sequence_id": "S-A", "expect": APP_EXPECT})
    assert reply["data"] == {"before": {"id": "S-B", "name": "Cut B"}, "after": {"id": "S-A", "name": "Cut A"},
                             "set_error": None}
    assert seen == ["activate S-A"]


@needs_node
def test_app_switch_read_without_a_target_and_with_odd_values():
    reply, _ = run_app({"action": "read", "expect": {**APP_EXPECT, "sequence_id": None, "sequence_name": None}},
                       "proxyValue = true; seqA.end = 'x';")
    assert reply["data"] == {"proxies": 1, "active_sequence": {"id": "S-B", "name": "Cut B"}, "sequence": None}
    reply, _ = run_app({"action": "read", "expect": APP_EXPECT},
                       "app.getEnableProxies = function () { throw new Error('no'); }; seqA.end = '12.5';")
    assert reply["data"]["proxies"] is None and reply["data"]["sequence"]["end_ticks"] is None
    reply, _ = run_app({"action": "read", "expect": APP_EXPECT}, "proxyValue = 7; activeSeq = null;")
    assert reply["data"]["proxies"] is None and reply["data"]["active_sequence"] is None
    # an active sequence that cannot be read is a failure, not "no active sequence"
    reply, _ = run_app({"action": "read", "expect": APP_EXPECT},
                       "Object.defineProperty(app.project, 'activeSequence', {get: function () { throw new Error('busy'); }});")
    assert reply["success"] is False and "busy" in reply["error"]


@needs_node
def test_app_switch_write_errors_are_caught_after_the_write():
    reply, seen = run_app({"action": "proxies", "enabled": False, "expect": APP_EXPECT},
                          "app.setEnableProxies = function () { calls.push('set'); throw new Error('Illegal Parameter type'); };")
    assert reply["success"] is True
    assert reply["data"] == {"before": 1, "after": 1, "set_error": "Error: Illegal Parameter type"}
    reply, seen = run_app({"action": "activate", "sequence_id": "S-A", "expect": APP_EXPECT},
                          "Object.defineProperty(app.project, 'activeSequence', {get: function () { return activeSeq; },"
                          " set: function (s) { calls.push('try'); throw new Error('locked'); }});")
    assert reply["success"] is True
    assert reply["data"] == {"before": {"id": "S-B", "name": "Cut B"}, "after": {"id": "S-B", "name": "Cut B"},
                             "set_error": "Error: locked"}


@needs_node
@pytest.mark.parametrize("args, prelude, text", [
    ({"action": "proxies", "enabled": 1}, "", "enabled must be true or false, not 1"),
    ({"action": "proxies"}, "", "enabled must be true or false, not undefined"),
    ({"action": "toggle"}, "", "the action must be read, proxies or activate, not toggle"),
    ({"action": "activate"}, "", "the script got no sequence_id to make active"),
    ({"action": "activate", "sequence_id": "S-Z"}, "", "no sequence has the ID S-Z"),
    ({"action": "proxies", "enabled": False}, "app.project.path = '/p/Other.prproj';",
     "the active project is /p/Other.prproj, not /p/X Claude.prproj"),
    ({"action": "activate", "sequence_id": "S-B"}, "seqA.name = 'Renamed';", "sequence S-A is named Renamed, not Cut A"),
])
def test_app_switch_refusals_write_nothing(args, prelude, text):
    reply, seen = run_app({**args, "expect": APP_EXPECT}, prelude)
    assert reply == {"success": False, "error": "Error: precondition: " + text}
    assert [c for c in seen if c.startswith("set") or c.startswith("activate")] == []


# ---------- more exit paths ----------
def test_recipe_interrupt_in_session_a_after_the_switches_puts_back(recipe, tmp_path, monkeypatch):
    """Ctrl-C at the confirming state read, after both switches: session A puts both back before it stops."""
    model = AppModel(proxies=1, active="o1")

    def stop(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(frames, "guarded_state", stop)      # only the confirming read uses this name
    err, sessions = run_recipe(recipe, model, tmp_path)
    assert isinstance(err, CliError) and err.code == "interrupted"
    assert model.writes == [("proxies", 0), ("activate", "s1"), ("activate", "o1"), ("proxies", 1)]
    assert err.payload["left_switched"] is None and err.payload["frames"] == []
    assert sessions == [] and lease_free(recipe)


def test_recipe_project_changed_before_the_export(recipe, tmp_path, monkeypatch):
    """The confirming state read shows another active project: no export, both switches put back in session A."""
    model = AppModel(proxies=1, active="o1")
    original = frames.guarded_state

    def other_project(*args, **kwargs):
        state, warnings = original(*args, **kwargs)
        return {**state, "projects": [{"name": "Y Claude.prproj", "path": "/p/Y Claude.prproj", "active": True}]}, warnings
    monkeypatch.setattr(frames, "guarded_state", other_project)
    err, sessions = run_recipe(recipe, model, tmp_path)
    assert isinstance(err, CliError) and err.code == "project_mismatch"
    assert sessions == []
    assert model.writes == [("proxies", 0), ("activate", "s1"), ("activate", "o1"), ("proxies", 1)]


def test_unreadable_end_is_refused(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel()
    model.sequences["s1"]["end_ticks"] = None
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 1 and out["code"] == "bad_state" and "cannot be read" in out["error"]
    assert model.writes == [] and live == []


def test_bad_read_reply_is_refused(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    model = AppModel()
    original = model.app_switch

    def odd(script):
        reply = original(script)
        return {**reply, "proxies": True} if isinstance(reply, dict) and "sequence" in reply else reply
    model.app_switch = odd
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 1 and out["code"] == "bad_reply"
    assert model.writes == [] and live == []


def test_put_back_that_does_not_take_in_session_b(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    """The old sequence does not become active again: the proxies still go back, the active sequence is left."""
    model = AppModel(proxies=1, active="o1")
    original = model.app_switch

    def sticky(script):
        if script_args(script).get("action") == "activate" and script_args(script).get("sequence_id") == "o1":
            model.activate_takes = False
        reply = original(script)
        model.activate_takes = True
        return reply
    model.app_switch = sticky
    code, out, _ = export(bridge_dir, capsys, model, tmp_path / "o", "--time", "1")
    assert code == 1 and out["code"] == "not_restored"
    assert out["exported"] == 1
    assert model.writes == [("proxies", 0), ("activate", "s1"), ("activate", "o1"), ("proxies", 1)]
    assert out["left_switched"] == {"proxies": None, "active_sequence": {"id": "s1", "name": "Main"}}
    lines = [w for w in out["warnings"] if "cli-anything-premiere live app" in w]
    assert len(lines) == 1 and "did not take" in lines[0] and lines[0].endswith("live app activate --sequence o1")


# ---------- fix round 1 ----------
def test_reported_path_in_decomposed_unicode_is_the_same_file(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    prefix = "NFD f\u00fcr"
    code, out, _ = export(bridge_dir, capsys, AppModel(), tmp_path / "o", "--time", "1", "--prefix", prefix)
    assert code == 0, out
    wanted = str(tmp_path / "o" / f"{prefix}_00001000ms.png")
    assert out["frames"][0]["path"] == wanted                 # the payload keeps the requested path


def test_reported_path_through_a_resolved_symlink_is_the_same_file(live, bridge_dir, capsys, tmp_path):  # noqa: F811
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    code, out, _ = export(bridge_dir, capsys, AppModel(), link, "--time", "1", "--prefix", "REAL")
    assert code == 0, out
    assert out["frames"][0]["path"] == str(link / "REAL_00001000ms.png")
    assert (real / "REAL_00001000ms.png").is_file()


@pytest.mark.parametrize("prefix, text", [("NOPATH", "names no outputPath"), ("OTHER", "other.png")])
def test_reported_path_missing_or_another_file_is_a_failure(live, bridge_dir, capsys, tmp_path, prefix, text):  # noqa: F811
    code, out, _ = export(bridge_dir, capsys, AppModel(), tmp_path / "o", "--time", "1", "--prefix", prefix)
    assert code == 1 and out["code"] == "export_failed" and text in out["error"]
    assert out["frames"] == []


def test_same_path_rules(tmp_path):
    folder = tmp_path / "f\u00fcr"
    folder.mkdir()
    one = folder / "a.png"
    one.write_bytes(b"x")
    other = folder / "b.png"
    other.write_bytes(b"y")
    import unicodedata
    assert frames._same_path(unicodedata.normalize("NFD", str(one)), one)
    assert frames._same_path(os.path.realpath(one), one)
    assert not frames._same_path(str(other), one)
    assert not frames._same_path(str(folder / "missing.png"), one)
    assert frames._same_path(unicodedata.normalize("NFD", str(folder / "gone.png")), folder / "gone.png")


def test_recipe_active_sequence_changed_by_someone_else_is_left(recipe, tmp_path):
    model = AppModel(proxies=1, active="o1")
    model.sequences["t3"] = {"name": "Third", "end_ticks": str(60 * TPS)}

    def someone_else():
        model.active = "t3"
    result, _ = run_recipe(recipe, model, tmp_path, on_exit=someone_else)
    assert isinstance(result, dict), result
    assert model.writes == [("proxies", 0), ("activate", "s1"), ("proxies", 1)]     # no activation of o1
    assert model.active == "t3" and result["left_switched"] is None
    assert result["active_sequence"] == {"before": "o1", "after": "t3"}
    assert any("The active sequence is Third (t3) now" in w and "did not put it back" in w
               for w in result["warnings"])


def test_recipe_proxies_switched_on_by_someone_else_are_left(recipe, tmp_path):
    model = AppModel(proxies=1, active="o1")

    def someone_else():
        model.proxies = 1
    result, _ = run_recipe(recipe, model, tmp_path, on_exit=someone_else)
    assert isinstance(result, dict), result
    assert model.writes == [("proxies", 0), ("activate", "s1"), ("activate", "o1")]  # no proxy write
    assert result["left_switched"] is None and result["proxies"]["after"] is True
    assert any("Proxy display reads on now" in w and "did not put it back" in w for w in result["warnings"])


def test_recipe_session_b_reads_before_it_writes(recipe, tmp_path):
    model = AppModel(proxies=1, active="o1")
    order: list[str] = []
    original = model.app_switch

    def spy(script):
        order.append(script_args(script)["action"])
        return original(script)
    model.app_switch = spy
    result, _ = run_recipe(recipe, model, tmp_path)
    assert isinstance(result, dict), result
    assert order == ["read", "proxies", "activate", "read", "activate", "proxies"]
    assert model.writes == [("proxies", 0), ("activate", "s1"), ("activate", "o1"), ("proxies", 1)]


def test_recipe_unreadable_state_in_session_b_counts_as_ours(recipe, tmp_path):
    model = AppModel(proxies=1, active="o1")
    original = model.app_switch
    reads = []

    def failing_second_read(script):
        if script_args(script)["action"] == "read":
            reads.append(1)
            if len(reads) == 2:
                return Exception("Error: host busy")
        return original(script)
    model.app_switch = failing_second_read
    result, _ = run_recipe(recipe, model, tmp_path)
    assert isinstance(result, dict), result
    assert model.writes == [("proxies", 0), ("activate", "s1"), ("activate", "o1"), ("proxies", 1)]


def test_recipe_unknown_activation_with_no_active_before_says_so(recipe, tmp_path):
    model = AppModel(proxies=0, active=None)
    handler = model.handler()

    def hang(script):
        if MARK in script and script_args(script).get("action") == "activate":
            return None
        return handler(script)
    client = BridgeClient(recipe, 0.0)
    panel = FakePanel(recipe, handler=hang)
    panel.start()
    try:
        with pytest.raises(CliError) as err:
            frames.export_frames(client, lambda c: ScriptedSession(c), sequence_id="s1", times=[1.0],
                                 out_dir=tmp_path / "o", hard_limit_s=0.5)
    finally:
        panel.stop()
    assert err.value.code == "unknown_outcome"
    assert err.value.payload["left_switched"] is None
    assert any("may have been switched to Main (s1)" in w and "No sequence was active before" in w
               for w in err.value.payload["warnings"])


def test_recipe_program_fault_in_session_a_puts_back_and_carries_the_payload(recipe, tmp_path, monkeypatch):
    model = AppModel(proxies=1, active="o1")

    def broken(*args, **kwargs):
        raise ValueError("boom")
    monkeypatch.setattr(frames, "guarded_state", broken)
    err, sessions = run_recipe(recipe, model, tmp_path)
    assert isinstance(err, CliError) and err.code == "recipe_error"
    assert err.message == "ValueError: boom"
    assert model.writes == [("proxies", 0), ("activate", "s1"), ("activate", "o1"), ("proxies", 1)]
    assert err.payload["left_switched"] is None and err.payload["frames"] == [] and sessions == []
    assert lease_free(recipe)
