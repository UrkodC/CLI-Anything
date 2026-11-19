"""Tests for `live lut set`: the recipe (core/recipes/lut.py) and the command (live_recipes.py).

The effect scripts are answered by LutModel (fakes.py), the app_switch and state scripts by AppModel
(test_recipes_frames.py), the exports by the fake MCP server or a scripted session. The pixels are a stub, so no
test needs ffmpeg except test_frame_pixels_with_real_ffmpeg, which is skipped without it.
"""
from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cli_anything.premiere import live_cli
from cli_anything.premiere.core import bridge, guard, mcp_client
from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.core.mcp_client import McpSession
from cli_anything.premiere.core.recipes import lut
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.premiere_cli import dispatch
from cli_anything.premiere.tests.fakes import (  # noqa: F401
    EMPTY_LUT_SLOT, RECIPE_STATE_MARK, FakePanel, LutModel, bridge_dir, fake_mcp_command, keep_signals,
    recipe_handler, script_args)
from cli_anything.premiere.tests.test_jsx import needs_node
from cli_anything.premiere.tests.test_recipes_frames import AppModel

SET = ["live", "lut", "set"]
LOCK = ".host-owner-lease"
WRITE = "recipe: effect_write"
RESOLVE = "recipe: effect_resolve"
NODE = "000f4243"
N = 960 * 540 * 3
NOT_PROOF = "Read-back is not proof for a LUT. Use --prove."
MENU_WARNING = "Index 6 (Input LUT) reads {}. In some projects it reads 0 while the LUT renders. Use --prove."
PICTURE = bytes(16 + i % 64 for i in range(N))        # a normal picture: values 16 to 79, not flat
SHIFTED = bytes(64 + i % 64 for i in range(N))        # the same, 48 brighter
FLAT_WARNING = ("The frame at 1 s is flat (one color). It cannot show whether a LUT renders. Choose a time with a "
                "normal picture.")
FOREIGN = ("Someone changed proxy display or the active sequence during the check. The two frames were not taken "
           "under the same conditions, so there is no verdict.")
NOT_RENDERED = ("The exported frame at 1 s did not change after the write. The path is in index 4. Check that the "
                "clip is visible at that time and that the LUT is not an identity LUT.")
SAME_PICTURE = ("The picture did not change. A LUT was already in the slot, so this run cannot show whether the new "
                "LUT renders.")


# ---------- fixtures ----------
@pytest.fixture(autouse=True)
def _keep_signal_handlers(keep_signals):  # noqa: F811
    yield


@pytest.fixture(autouse=True)
def _no_real_bridge(monkeypatch, tmp_path):
    monkeypatch.setenv("PREMIERE_TEMP_DIR", str(tmp_path / "not-the-real-bridge"))
    monkeypatch.delenv("PREMIERE_MCP_CAPABILITIES", raising=False)
    monkeypatch.setattr(mcp_client, "CLOSE_WAIT_S", 0.3)
    monkeypatch.setattr(guard, "panel_version", lambda *a, **k: "1.25.0")
    monkeypatch.setattr(guard, "server_version", lambda *a, **k: "1.25.0")


@pytest.fixture
def ffmpeg(monkeypatch):
    """ffmpeg is "on PATH" (shutil.which is patched) and the pixels come from the file name: lut_before gives
    the bytes in pixels["before"], lut_after those in pixels["after"]. pixels["seen"] lists every path."""
    pixels = {"before": PICTURE, "after": SHIFTED, "seen": []}
    real = shutil.which      # lut.shutil is the shutil module: other programs (the MCP server) keep the real lookup

    def which(name, *a, **k):
        return "/fake/bin/ffmpeg" if name == "ffmpeg" else real(name, *a, **k)

    def stub(path):
        pixels["seen"].append(str(path))
        return pixels["before"] if "lut_before" in Path(path).name else pixels["after"]
    monkeypatch.setattr(lut.shutil, "which", which)
    monkeypatch.setattr(lut, "frame_pixels", stub)
    pixels["stub"] = stub
    return pixels


@pytest.fixture
def live(bridge_dir, monkeypatch, tmp_path):  # noqa: F811
    """The live commands on a temp bridge folder, with the fake MCP server."""
    command = fake_mcp_command(tmp_path, bridge_dir)
    monkeypatch.setattr(live_cli, "make_client", lambda wait, **kw: BridgeClient(bridge_dir, wait, **kw))
    monkeypatch.setattr(live_cli, "make_session", lambda client: McpSession(client, command))
    return bridge_dir


def handler(model: LutModel, app: AppModel):
    inner = recipe_handler({**model.replies(), "recipe: app_switch": app.app_switch})

    def handle(script: str) -> str:
        if RECIPE_STATE_MARK in script:
            return json.dumps({"success": True, "data": app.state()})
        return inner(script)
    return handle


@contextlib.contextmanager
def panel_on(bridge_dir, model, app=None):  # noqa: F811
    panel = FakePanel(bridge_dir, handler=handler(model, app or AppModel()))
    panel.start()
    try:
        yield panel
    finally:
        panel.stop()


def cube(tmp_path, name="look.cube") -> Path:
    path = Path(tmp_path) / "luts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("LUT_3D_SIZE 2\n", encoding="utf-8")
    return path


def run_json(capsys, argv) -> tuple[int, dict]:
    code = dispatch(["--json", *argv])
    return code, json.loads(capsys.readouterr().out)


def lut_set(bridge_dir, capsys, model, lut_path, *argv, app=None, target=("--track", "0", "--clip", "0")):  # noqa: F811
    with panel_on(bridge_dir, model, app) as panel:
        code, out = run_json(capsys, [*SET, "--sequence", "s1", *target, "--lut", str(lut_path), *argv])
    return code, out, panel


def calls(tmp_path) -> list[dict]:
    path = Path(tmp_path) / "fake_mcp_calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def lease_free(bridge_dir) -> bool:  # noqa: F811
    return not (Path(bridge_dir) / LOCK).exists()


def written(path: Path) -> str:
    return str(path.resolve()) + "/"


def prove_args(tmp_path, time="1.0"):
    return ["--prove", "--time", time, "--out-dir", str(Path(tmp_path) / "frames")]


# ---------- the writes ----------
def test_lut_is_written_with_a_trailing_slash(live, capsys, tmp_path):
    model = LutModel()
    path = cube(tmp_path)
    code, out, panel = lut_set(live, capsys, model, path)
    assert code == 0, out
    assert model.writes == [(NODE, 2, 4, written(path)), (NODE, 2, 6, 1)]
    assert all(index != 7 for _, _, index, _ in model.writes)
    assert written(path).endswith("look.cube/")
    assert out["stored"] is True and out["rendered"] is None and out["difference"] is None
    assert out["lut"] == str(path.resolve()) and out["path_before"] is None and out["path_after"] == written(path)
    assert out["menu_value"] == 1 and out["effect_index"] == 2 and out["frames"] == []
    assert out["clip"]["node_id"] == NODE
    assert NOT_PROOF in out["warnings"]
    assert {"clip", "lut", "effect_index", "path_before", "path_after", "menu_value", "stored", "rendered",
            "difference", "frames", "warnings"} <= set(out)
    assert sum(WRITE in s for s in panel.seen) == 2
    assert lease_free(live)


def test_stored_follows_index_4_only(live, capsys, tmp_path):
    model = LutModel()
    model.readback[1] = 0                       # index 6 reads 0 after the write, as in a converted project
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path))
    assert code == 0, out
    assert out["stored"] is True and out["menu_value"] == 0
    assert MENU_WARNING.format(0) in out["warnings"] and NOT_PROOF in out["warnings"]


def test_index_4_not_stored_stops_before_index_6(live, capsys, tmp_path):
    model = LutModel()
    path = cube(tmp_path)
    model.readback[written(path)] = "/other/x.cube/"
    code, out, _ = lut_set(live, capsys, model, path)
    assert code == 1 and out["code"] == "not_stored"
    assert out["stored"] is False and out["menu_value"] is None
    assert all(index != 6 for _, _, index, _ in model.writes)
    assert out["path_after"] is not None and NOT_PROOF in out["warnings"]
    assert lease_free(live)


def test_path_with_space_and_non_ascii_travels_unchanged(live, capsys, tmp_path):
    model = LutModel()
    path = cube(tmp_path, "Looks ü/my look é.CUBE")
    code, out, _ = lut_set(live, capsys, model, path)
    assert code == 0, out
    assert model.writes[0][3] == str(path.resolve()) + "/"
    assert model.lumetri()[4]["value"] == str(path.resolve()) + "/"
    assert out["path_after"] == str(path.resolve()) + "/"


def test_the_same_lut_again_says_so(live, capsys, tmp_path):
    model = LutModel()
    path = cube(tmp_path)
    model.lumetri()[4]["value"] = written(path)
    code, out, _ = lut_set(live, capsys, model, path)
    assert code == 0 and out["path_before"] == written(path)
    assert "The same LUT was already set." in out["warnings"]


@pytest.mark.parametrize("before, expected", [
    (EMPTY_LUT_SLOT, None), ("", None), ("/luts/old.CUBE/", "/luts/old.CUBE/"), ("/luts/old.cube", None),
    ("/luts/old.txt/", None)])
def test_path_before_is_a_cube_path_with_a_slash(live, capsys, tmp_path, before, expected):
    model = LutModel()
    model.lumetri()[4]["value"] = before
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), "--dry-run")
    assert code == 0 and out["path_before"] == expected


# ---------- refusals ----------
def test_layout_change_is_refused(live, capsys, tmp_path):
    model = LutModel()
    model.lumetri()[6]["name"] = "Look"
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path))
    assert code == 1 and out["code"] == "lumetri_layout"
    assert "property 6" in out["error"] and "Look" in out["error"] and "Nothing was written" in out["error"]
    assert model.writes == [] and lease_free(live)


@pytest.mark.parametrize("change, text", [
    (lambda p: p[2].update(name="Exposure"), "property 2"),
    (lambda p: p[4].update(name="Look"), "property 4"),
    (lambda p: p[4].update(kind="number", value=0.0), "index 4 holds a number"),
    (lambda p: p[6].update(kind="string", value="x"), "index 6 holds a string"),
    (lambda p: p.__delitem__(slice(5, None)), "has no property 6"),
])
def test_other_layouts_are_refused(live, capsys, tmp_path, change, text):
    model = LutModel()
    change(model.lumetri())
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path))
    assert code == 1 and out["code"] == "lumetri_layout", out
    assert text in out["error"]
    assert model.writes == []


def test_keyframed_index_6_is_refused_before_index_4(live, capsys, tmp_path):
    model = LutModel()
    model.lumetri()[6]["keyframed"] = True
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path))
    assert code == 1 and out["code"] == "refused_parameter"
    assert model.writes == []


def test_keyframed_index_4_is_refused(live, capsys, tmp_path):
    model = LutModel()
    model.lumetri()[4]["keyframed"] = True
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path))
    assert code == 1 and out["code"] == "refused_parameter" and "index 4" in out["error"]
    assert model.writes == []


def test_own_film_is_refused(live, capsys, tmp_path):
    model, app = LutModel(), AppModel()
    app.project_name, app.project_path = "Family film.prproj", "/p/Family film.prproj"
    code, out, panel = lut_set(live, capsys, model, cube(tmp_path), app=app)
    assert code == 1 and out["code"] == "project_guard"
    assert model.writes == [] and model.resolves == 0 and not any(WRITE in s for s in panel.seen)
    assert out["stored"] is False and NOT_PROOF in out["warnings"]
    assert lease_free(live)


def test_recovery_marker_before_the_start_refuses(live, capsys, tmp_path, ffmpeg):
    (Path(live) / ".host-recovery.json").write_text('{"version":1,"reason":"unknown_command_outcome","nonce":"x"}')
    model = LutModel()
    code, out, panel = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path))
    assert code == 1 and out["code"] == "recovery_required"
    assert model.writes == [] and model.resolves == 0 and not any(WRITE in s for s in panel.seen)
    assert calls(tmp_path) == [] and lease_free(live)


def test_clip_without_lumetri(live, capsys, tmp_path):
    model = LutModel()
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), target=("--track", "0", "--clip", "1"))
    assert code == 1 and out["code"] == "precondition"
    assert "no effect named AE.ADBE Lumetri" in out["error"]
    assert model.writes == [] and lease_free(live)


def test_two_lumetri_need_the_effect_index(live, capsys, tmp_path):
    model = LutModel()
    effects = model.clips[(0, 0)]["effects"]
    effects.append({**effects[2], "properties": [dict(p) for p in effects[2]["properties"]]})
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path))
    assert code == 1 and out["code"] == "precondition" and "ambiguous" in out["error"]
    assert model.writes == []
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), "--effect-index", "5")
    assert code == 0, out
    assert [w[1] for w in model.writes] == [5, 5] and out["effect_index"] == 5


def test_wrong_sequence_name_stays_a_precondition(live, capsys, tmp_path):
    model = LutModel()
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), "--sequence-name", "Other")
    assert code == 1 and out["code"] == "precondition" and model.writes == []


def test_bad_lut_file(live, capsys, tmp_path, monkeypatch):
    model = LutModel()
    text = tmp_path / "luts" / "look.txt"
    text.parent.mkdir(parents=True)
    text.write_text("x", encoding="utf-8")
    folder = tmp_path / "luts" / "dir.cube"
    folder.mkdir()
    cube(tmp_path)
    monkeypatch.chdir(tmp_path)
    for given in (tmp_path / "missing.cube", Path("luts/look.cube"), text, folder):
        code, out, panel = lut_set(live, capsys, model, given)
        assert code == 1 and out["code"] == "bad_lut", (given, out)
        assert panel.seen == []
    assert model.writes == []


@pytest.mark.parametrize("argv, code_name", [
    (["--prove"], "bad_args"),
    (["--prove", "--time", "1"], "bad_args"),
    (["--prove", "--out-dir", "/tmp/x"], "bad_args"),
    (["--time", "1"], "bad_args"),
    (["--out-dir", "/tmp/x"], "bad_args"),
    (["--prove", "--time", "-1", "--out-dir", "/tmp/x"], "bad_time"),
    (["--prove", "--time", "nan", "--out-dir", "/tmp/x"], "bad_time"),
])
def test_bad_arguments_fail_before_any_contact(live, capsys, tmp_path, ffmpeg, argv, code_name):
    model = LutModel()
    code, out, panel = lut_set(live, capsys, model, cube(tmp_path), *argv)
    assert code == 1 and out["code"] == code_name, out
    assert panel.seen == []


def test_node_id_and_track_are_not_both_allowed(live, capsys, tmp_path):
    model = LutModel()
    code, out, panel = lut_set(live, capsys, model, cube(tmp_path), target=("--node-id", NODE, "--track", "0"))
    assert code == 1 and out["code"] == "bad_args" and panel.seen == []


def test_node_id_target(live, capsys, tmp_path):
    model = LutModel()
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), target=("--node-id", NODE))
    assert code == 0 and [w[0] for w in model.writes] == [NODE, NODE]


def test_dry_run_sends_reads_only(live, capsys, tmp_path, ffmpeg):
    model = LutModel()
    path = cube(tmp_path)
    code, out, panel = lut_set(live, capsys, model, path, "--dry-run", *prove_args(tmp_path))
    assert code == 0 and out["dry_run"] is True and out["stored"] is None
    assert model.writes == [] and not any(WRITE in s for s in panel.seen)
    assert calls(tmp_path) == [] and ffmpeg["seen"] == []
    assert out["path_after"] == written(path) and out["path_before"] is None
    assert [Path(f).name for f in out["frames"]] == ["lut_before_00001000ms.png", "lut_after_00001000ms.png"]
    assert not (tmp_path / "frames").exists()
    assert lease_free(live)


# ---------- --prove ----------
def test_prove_true_when_the_frame_changes(live, capsys, tmp_path, ffmpeg):
    model = LutModel()
    path = cube(tmp_path)
    code, out, _ = lut_set(live, capsys, model, path, *prove_args(tmp_path))
    assert code == 0, out
    assert out["stored"] is True and out["rendered"] is True and out["difference"] == 48.0
    folder = tmp_path / "frames"
    assert out["frames"] == [str(folder / "lut_before_00001000ms.png"), str(folder / "lut_after_00001000ms.png")]
    assert [Path(c["arguments"]["output_path"]).name for c in calls(tmp_path)] == [
        "lut_before_00001000ms.png", "lut_after_00001000ms.png"]
    assert all(c["lease_held"] is False for c in calls(tmp_path))
    assert model.writes == [(NODE, 2, 4, written(path)), (NODE, 2, 6, 1)]
    assert NOT_PROOF in out["warnings"]
    assert lease_free(live)


def test_prove_order_frame_a_then_writes_then_frame_b(tmp_path, bridge_dir, ffmpeg):  # noqa: F811
    """The write lands between the two exports: the scripted session records the slot at each export."""
    model = LutModel()
    path = cube(tmp_path)
    slot_at_export = []
    err_or_result, _ = run_recipe(bridge_dir, model, tmp_path, path,
                                  on_call=lambda n, a: slot_at_export.append(model.lumetri()[4]["value"]))
    assert isinstance(err_or_result, dict), err_or_result
    assert slot_at_export == [EMPTY_LUT_SLOT, written(path)]
    assert model.resolves == 2                  # once per session
    assert lease_free(bridge_dir)


def test_prove_false_when_empty_slot_and_no_change(live, capsys, tmp_path, ffmpeg):
    ffmpeg["after"] = ffmpeg["before"]
    model = LutModel()
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path))
    assert code == 1 and out["code"] == "not_rendered"
    assert out["error"] == NOT_RENDERED and "does not render" not in out["error"]
    assert out["stored"] is True and out["rendered"] is False and out["difference"] == 0.0
    assert FLAT_WARNING not in out["warnings"] and FOREIGN not in out["warnings"]
    assert out["path_before"] is None and len(model.writes) == 2
    assert lease_free(live)


def test_prove_null_when_a_path_was_there(live, capsys, tmp_path, ffmpeg):
    ffmpeg["after"] = ffmpeg["before"]
    model = LutModel()
    model.lumetri()[4]["value"] = "/luts/old.cube/"
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path))
    assert code == 0, out
    assert out["rendered"] is None and out["path_before"] == "/luts/old.cube/" and out["difference"] == 0.0
    assert SAME_PICTURE in out["warnings"]
    assert "The same LUT was already set." not in out["warnings"]


def test_prove_null_with_the_same_lut_says_both(live, capsys, tmp_path, ffmpeg):
    ffmpeg["after"] = ffmpeg["before"]
    model = LutModel()
    path = cube(tmp_path)
    model.lumetri()[4]["value"] = written(path)
    code, out, _ = lut_set(live, capsys, model, path, *prove_args(tmp_path))
    assert code == 0 and out["rendered"] is None
    assert SAME_PICTURE in out["warnings"] and "The same LUT was already set." in out["warnings"]


def test_prove_just_below_the_threshold(live, capsys, tmp_path, ffmpeg):
    ffmpeg["after"] = bytes(b + 1 for b in PICTURE[:N // 2]) + PICTURE[N // 2:]      # 0.5 per byte
    code, out, _ = lut_set(live, capsys, LutModel(), cube(tmp_path), *prove_args(tmp_path))
    assert code == 1 and out["code"] == "not_rendered" and out["difference"] == 0.5
    ffmpeg["after"] = bytes(b + 1 for b in PICTURE)                                   # exactly 1.0
    code, out, _ = lut_set(live, capsys, LutModel(), cube(tmp_path), *prove_args(tmp_path))
    assert code == 0 and out["rendered"] is True and out["difference"] == 1.0


def test_prove_needs_ffmpeg(live, capsys, tmp_path, monkeypatch):
    real = shutil.which
    monkeypatch.setattr(lut.shutil, "which", lambda name, *a, **k: None if name == "ffmpeg" else real(name, *a, **k))
    model = LutModel()
    code, out, panel = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path))
    assert code == 1 and out["code"] == "ffmpeg_missing"
    assert panel.seen == [] and calls(tmp_path) == []


def test_prove_time_outside_the_clip(live, capsys, tmp_path, ffmpeg):
    model = LutModel()
    for time in ("6.006", "7.0"):                          # clip V1[0] runs 0 to 6.006 s; the end is not inside
        code, out, panel = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path, time))
        assert code == 1 and out["code"] == "bad_time", out
        assert not any(WRITE in s for s in panel.seen)
    model.clips[(0, 0)]["start_ticks"] = str(2 * 254016000000)          # now it runs 2 to 6.006 s
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path, "1.999"))
    assert code == 1 and out["code"] == "bad_time"          # before the start
    assert model.writes == [] and calls(tmp_path) == []
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path, "2"))
    assert code == 0, out                                   # the start is inside


def test_prove_with_an_unreadable_clip_end_is_refused(live, capsys, tmp_path, ffmpeg):
    model = LutModel()
    model.clips[(0, 0)]["end_ticks"] = None
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path))
    assert code == 1 and out["code"] == "bad_state"
    assert model.writes == [] and calls(tmp_path) == []


def test_prove_replaces_its_own_two_files(live, capsys, tmp_path, ffmpeg):
    model = LutModel()
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path))
    assert code == 0
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path))
    assert code == 0, out
    assert len(calls(tmp_path)) == 4


def test_prove_refuses_a_folder_where_a_frame_goes(live, capsys, tmp_path, ffmpeg):
    (tmp_path / "frames" / "lut_after_00001000ms.png").mkdir(parents=True)
    model = LutModel()
    code, out, panel = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path))
    assert code == 1 and out["code"] == "output_exists" and panel.seen == []


def test_human_output(live, capsys, tmp_path, ffmpeg):
    model = LutModel()
    path = cube(tmp_path)
    with panel_on(live, model):
        code = dispatch([*SET, "--sequence", "s1", "--track", "0", "--clip", "0", "--lut", str(path),
                         *prove_args(tmp_path)])
    out = capsys.readouterr()
    assert code == 0
    assert "Stored: yes" in out.out and "Rendered: yes" in out.out and "48.0" in out.out
    assert "lut_after_00001000ms.png" in out.out
    assert NOT_PROOF in out.out or NOT_PROOF in out.err


def test_human_failure_output(live, capsys, tmp_path, ffmpeg):
    ffmpeg["after"] = ffmpeg["before"]
    path = cube(tmp_path)
    with panel_on(live, LutModel()):
        code = dispatch([*SET, "--sequence", "s1", "--track", "0", "--clip", "0", "--lut", str(path),
                         *prove_args(tmp_path)])
    out = capsys.readouterr()
    assert code == 1
    assert f"index 4 (no LUT) -> \"{written(path)[:60]}..." in out.out           # cut at 60 in human output
    assert "Stored: yes" in out.out and "Rendered: no (difference 0.0" in out.out
    assert "Error: " + NOT_RENDERED in out.err and NOT_PROOF in out.err
    assert "Error" not in out.out


def test_human_failure_before_a_write_shows_no_arrow(live, capsys, tmp_path, ffmpeg):
    with panel_on(live, LutModel()):
        code = dispatch([*SET, "--sequence", "s1", "--track", "0", "--clip", "0", "--lut", str(cube(tmp_path)),
                         *prove_args(tmp_path, "7")])
    out = capsys.readouterr()
    assert code == 1 and "index 4 (no LUT)" in out.out and "->" not in out.out
    assert "Stored: no" in out.out and "Error: The time 7 s is not inside the clip" in out.err


def test_help_says_what_stored_and_prove_mean():
    from click.testing import CliRunner
    result = CliRunner().invoke(live_cli.live, ["lut", "set", "--help"])
    assert result.exit_code == 0
    text = " ".join(result.output.split())
    assert "stored" in text and "--prove" in text
    assert ("Shows that the LUT changed the picture. It does not show that it is the right LUT. For a film, compare "
            "with an ffmpeg lut3d render of the source.") in text
    assert "not proof" in text
    assert "must already hold a Lumetri Color effect" in text
    assert "Index 7 is never written" in text
    assert "does not remove a LUT" in text
    assert "replaces its own two files" in text
    assert "The clip must be visible at --time, and the picture must not be black or one flat color." in text
    assert "does not render" not in text
    assert ("null when the exported frame did not change and a LUT was already in the slot or the frame before is "
            "one flat color; and when the two frames were not taken under the same conditions") in text
    assert "\u2014" not in result.output
    group = CliRunner().invoke(live_cli.live, ["lut", "--help"])
    assert group.exit_code == 0 and "\u2014" not in group.output


# ---------- the recipe, with a scripted MCP session ----------
class ScriptedSession:
    """Stands in for McpSession: each export_frame call writes a small file, unless on_call(number, arguments)
    raises or returns a reply. Records whether the lease was held at each call."""
    count = 0

    def __init__(self, client, on_call=None) -> None:
        self.client = client
        self.on_call = on_call
        self.lease_held: list[bool] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def call(self, name, arguments, timeout_s=120.0):
        assert name == "export_frame"
        ScriptedSession.count += 1
        self.lease_held.append((Path(self.client.bridge_dir) / LOCK).exists())
        if self.on_call:
            reply = self.on_call(ScriptedSession.count, arguments)
            if reply is not None:
                return reply
        Path(arguments["output_path"]).write_bytes(b"still")
        return {"is_error": False, "text": "", "structured": {"ok": True, "tool": "export_frame",
                                                               "data": {"outputPath": arguments["output_path"]}}}


def run_recipe(bridge_dir, model, tmp_path, lut_path, *, on_call=None, app=None, time=1.0, pixels=None,  # noqa: F811
               **kw):
    ScriptedSession.count = 0
    sessions: list[ScriptedSession] = []

    def make(client):
        sessions.append(ScriptedSession(client, on_call))
        return sessions[-1]
    client = BridgeClient(bridge_dir, 0.0)
    target = {"sequence_id": "s1", "track": 0, "clip": 0}
    with panel_on(bridge_dir, model, app):
        try:
            result = lut.set_lut(client, make, target, lut_path, prove=True, time=time,
                                 out_dir=Path(tmp_path) / "frames", pixels=pixels or lut.frame_pixels, **kw)
        except CliError as err:
            return err, sessions
    return result, sessions


def test_recipe_frame_a_fails_and_nothing_is_written(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    model = LutModel()

    def fail(number, arguments):
        return {"is_error": True, "text": "fake export failure", "structured": None}
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), on_call=fail)
    assert isinstance(err, CliError) and err.code == "export_failed"
    assert model.writes == []
    assert err.payload["stored"] is False and err.payload["rendered"] is None
    assert err.payload["export"]["frames"] == [] and "left_switched" in err.payload
    assert lease_free(bridge_dir)


def test_recipe_pixels_of_frame_a_fail_and_nothing_is_written(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    model = LutModel()

    def broken(path):
        raise CliError("ffmpeg_failed", "ffmpeg could not read it")
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), pixels=broken)
    assert isinstance(err, CliError) and err.code == "ffmpeg_failed"
    assert model.writes == [] and err.payload["stored"] is False


def test_recipe_frame_b_fails_after_the_write(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    model = LutModel()

    def fail_second(number, arguments):
        if number == 2:
            return {"is_error": True, "text": "fake export failure", "structured": None}
        return None
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), on_call=fail_second)
    assert isinstance(err, CliError) and err.code == "export_failed"
    assert "The LUT was written" in err.message
    assert len(model.writes) == 2
    assert err.payload["stored"] is True and err.payload["rendered"] is None and err.payload["difference"] is None
    assert any("could not be exported" in w and "export_failed" in w for w in err.payload["warnings"])
    assert err.payload["frames"] == [str(Path(tmp_path) / "frames" / "lut_before_00001000ms.png")]
    assert lease_free(bridge_dir)


def test_recipe_pixels_of_frame_b_fail_after_the_write(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    model = LutModel()

    def half(path):
        if "lut_after" in Path(path).name:
            raise CliError("ffmpeg_failed", "ffmpeg broke")
        return b"\x10" * N
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), pixels=half)
    assert err.code == "ffmpeg_failed" and "The LUT was written" in err.message
    assert err.payload["stored"] is True and err.payload["rendered"] is None and len(model.writes) == 2


def test_recipe_frame_b_not_restored_still_compares(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    """Frame B is written, but the old active sequence is gone, so it cannot be made active again: the verdict
    is still made, and the error is not_restored with left_switched."""
    model, app = LutModel(), AppModel(proxies=1, active="o1")

    def delete_old(number, arguments):
        if number == 2:
            del app.sequences["o1"]
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), on_call=delete_old, app=app)
    assert isinstance(err, CliError) and err.code == "not_restored"
    assert err.payload["stored"] is True and err.payload["rendered"] is True and err.payload["difference"] == 48.0
    assert err.payload["left_switched"]["active_sequence"] == {"id": "s1", "name": "Main"}
    assert any("live app activate" in w for w in err.payload["warnings"])
    assert len(err.payload["frames"]) == 2 and lease_free(bridge_dir)


def test_recipe_target_moved_between_the_sessions(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    model = LutModel()

    def move():
        model.clips[(0, 0)]["node_id"] = "000f9999"
    model.moves[2] = move
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path))
    assert isinstance(err, CliError) and err.code == "target_moved"
    assert model.writes == [] and err.payload["stored"] is False
    assert lease_free(bridge_dir)


def test_recipe_slot_changed_between_the_sessions(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    model = LutModel()
    model.moves[2] = lambda: model.lumetri()[4].update(value="/luts/someone.cube/")
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path))
    assert isinstance(err, CliError) and err.code == "target_moved"
    assert model.writes == []


def test_recipe_interrupt_after_frame_a_writes_nothing(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    model = LutModel()

    def stop(path):
        raise bridge.Interrupted()
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), pixels=stop)
    assert isinstance(err, CliError) and err.code == "interrupted"
    assert model.writes == [] and err.payload["stored"] is False
    assert lease_free(bridge_dir)


def test_recipe_index_6_script_failure_keeps_stored(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    """The write of index 6 raises (no answer is not playable here, so a refusal of the write script): the error
    goes on, and the payload still says that index 4 was stored."""
    model = LutModel()
    original = model.write

    def write(script):
        if script_args(script)["index"] == 6:
            return Exception("Error: something broke")
        return original(script)
    model.write = write
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), on_call=None)
    assert isinstance(err, CliError) and err.code == "script_failed"
    assert err.payload["stored"] is True and err.payload["menu_value"] is None
    assert "index 4 was stored" in err.message
    assert lease_free(bridge_dir)


def test_recipe_set_lut_takes_the_brief_signature():
    import inspect
    params = inspect.signature(lut.set_lut).parameters
    assert list(params)[:4] == ["client", "make_session", "target", "lut"]
    assert params["prove"].default is False and params["time"].default is None
    assert params["hard_limit_s"].default == 120.0 and params["pixels"].default is lut.frame_pixels
    assert (lut.LUT_PATH_INDEX, lut.LUT_MENU_INDEX, lut.PROVE_THRESHOLD) == (4, 6, 1.0)


# ---------- pixels ----------
def test_mean_difference():
    assert lut.mean_difference(b"\x10\x20", b"\x20\x10") == 16.0
    assert lut.mean_difference(b"\x00" * 4, b"\x00" * 4) == 0.0
    with pytest.raises(CliError) as err:
        lut.mean_difference(b"\x00", b"\x00\x00")
    assert err.value.code == "ffmpeg_failed"
    with pytest.raises(CliError) as err:
        lut.mean_difference(b"", b"")
    assert err.value.code == "ffmpeg_failed"


def test_frame_pixels_without_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(lut.shutil, "which", lambda *a, **k: None)
    with pytest.raises(CliError) as err:
        lut.frame_pixels(tmp_path / "a.png")
    assert err.value.code == "ffmpeg_missing"


def test_frame_pixels_runs_the_filter_and_checks_the_output(monkeypatch, tmp_path):
    monkeypatch.setattr(lut.shutil, "which", lambda *a, **k: "/fake/bin/ffmpeg")
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0, stdout=b"\x01" * N, stderr=b"")
    monkeypatch.setattr(lut.subprocess, "run", run)
    assert lut.frame_pixels(tmp_path / "a b.png") == b"\x01" * N
    cmd = seen["cmd"]
    assert cmd[0] == "/fake/bin/ffmpeg" and str(tmp_path / "a b.png") in cmd
    assert ("color=black:s=3840x2160[b];[b][0:v]scale2ref[b2][v];[b2][v]overlay=format=auto,"
            "scale=960:540:flags=area,format=rgb24") in cmd
    assert cmd[-5:] == ["-frames:v", "1", "-f", "rawvideo", "-"]
    assert seen["kw"]["timeout"] == 60

    def short(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=b"\x01" * 10, stderr=b"")
    monkeypatch.setattr(lut.subprocess, "run", short)
    with pytest.raises(CliError) as err:
        lut.frame_pixels(tmp_path / "a.png")
    assert err.value.code == "ffmpeg_failed"

    def failing(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"line one\nNo such file\n")
    monkeypatch.setattr(lut.subprocess, "run", failing)
    with pytest.raises(CliError) as err:
        lut.frame_pixels(tmp_path / "a.png")
    assert err.value.code == "ffmpeg_failed" and "No such file" in err.value.message

    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    monkeypatch.setattr(lut.subprocess, "run", slow)
    with pytest.raises(CliError) as err:
        lut.frame_pixels(tmp_path / "a.png")
    assert err.value.code == "ffmpeg_failed"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_frame_pixels_with_real_ffmpeg(tmp_path):
    paths = []
    for name, gray in (("a.png", "0x404040"), ("b.png", "0x808080")):
        path = tmp_path / name
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", f"color=c={gray}:s=1280x720",
                        "-frames:v", "1", str(path)], check=True, timeout=60)
        paths.append(path)
    a, b = lut.frame_pixels(paths[0]), lut.frame_pixels(paths[1])
    assert len(a) == len(b) == N
    assert abs(lut.mean_difference(a, b) - 64) < 2


# ---------- effect_resolve.jsx: the end of the clip ----------
@needs_node
def test_effect_resolve_script_returns_the_end_only_when_readable():
    from cli_anything.premiere.tests.test_recipes_effects import RESOLVE_ENTRY, run_resolve
    reply, seen = run_resolve([RESOLVE_ENTRY], "seqA.videoTracks[0].clips[0].end = {ticks: '1525620096000'};")
    assert reply["success"] is True and seen["writes"] == []
    assert reply["data"]["entries"][0]["clip"]["end_ticks"] == "1525620096000"
    reply, _ = run_resolve([RESOLVE_ENTRY], "seqA.videoTracks[0].clips[0].end = {ticks: 'x'};")
    assert "end_ticks" not in reply["data"]["entries"][0]["clip"]
    reply, _ = run_resolve([RESOLVE_ENTRY])                     # no end at all
    assert "end_ticks" not in reply["data"]["entries"][0]["clip"]
    reply, _ = run_resolve([RESOLVE_ENTRY], "Object.defineProperty(seqA.videoTracks[0].clips[0], 'end', "
                                            "{get: function () { throw new Error('host'); }});")
    assert reply["success"] is True and "end_ticks" not in reply["data"]["entries"][0]["clip"]


# ---------- fix round 1: what the verdict may claim ----------
def test_is_flat():
    assert lut.FLAT_RANGE == 8
    assert lut.is_flat(b"\x10" * 100) and lut.is_flat(bytes([10, 17])) and lut.is_flat(b"")
    assert not lut.is_flat(bytes([10, 18])) and not lut.is_flat(PICTURE)


def test_flat_frame_a_and_a_small_difference_gives_null(live, capsys, tmp_path, ffmpeg):
    ffmpeg["before"] = b"\x00" * N                     # black
    ffmpeg["after"] = b"\x00" * (N // 2) + b"\x01" * (N - N // 2)
    model = LutModel()
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path))
    assert code == 0, out
    assert out["rendered"] is None and out["difference"] == 0.5 and out["stored"] is True
    assert FLAT_WARNING in out["warnings"] and SAME_PICTURE not in out["warnings"]
    assert len(model.writes) == 2 and len(calls(tmp_path)) == 2           # the write and frame B still happen


def test_flat_frame_a_and_a_large_difference_gives_true(live, capsys, tmp_path, ffmpeg):
    ffmpeg["before"], ffmpeg["after"] = b"\x00" * N, b"\x40" * N
    code, out, _ = lut_set(live, capsys, LutModel(), cube(tmp_path), *prove_args(tmp_path))
    assert code == 0 and out["rendered"] is True and out["difference"] == 64.0
    assert FLAT_WARNING in out["warnings"]


def test_flat_frame_a_with_a_path_before_gives_both_warnings(live, capsys, tmp_path, ffmpeg):
    ffmpeg["before"] = ffmpeg["after"] = b"\x00" * N
    model = LutModel()
    model.lumetri()[4]["value"] = "/luts/old.cube/"
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path))
    assert code == 0 and out["rendered"] is None
    assert FLAT_WARNING in out["warnings"] and SAME_PICTURE in out["warnings"]


def _someone_else(app, number_to_act, what):
    app.sequences["t3"] = {"name": "Third", "end_ticks": str(60 * 254016000000)}

    def on_call(number, arguments):
        if number == number_to_act:
            if what == "active":
                app.active = "t3"
            else:
                app.proxies = 1
        return None
    return on_call


@pytest.mark.parametrize("number, what", [(1, "active"), (2, "active"), (1, "proxies"), (2, "proxies")])
def test_a_change_by_others_during_an_export_gives_no_verdict(bridge_dir, tmp_path, ffmpeg, number, what):  # noqa: F811
    model, app = LutModel(), AppModel(proxies=1, active="o1")
    result, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), app=app,
                           on_call=_someone_else(app, number, what))
    assert isinstance(result, dict), result
    assert result["rendered"] is None and result["difference"] == 48.0
    assert FOREIGN in result["warnings"]
    assert len(model.writes) == 2 and lease_free(bridge_dir)


def test_a_change_by_others_overrides_false(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    ffmpeg["after"] = ffmpeg["before"]
    model, app = LutModel(), AppModel(proxies=1, active="o1")
    result, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), app=app,
                           on_call=_someone_else(app, 2, "proxies"))
    assert isinstance(result, dict), result
    assert result["rendered"] is None and FOREIGN in result["warnings"] and result["difference"] == 0.0


def test_conditions_rules():
    clean = {"changed_by_others": {"proxies": False, "active_sequence": False}, "proxies": {"during": False}}
    assert lut._conditions([clean, clean]) == (False, False)
    assert lut._conditions([clean, {**clean, "changed_by_others": {"proxies": False, "active_sequence": True}}]) \
        == (True, False)
    assert lut._conditions([{**clean, "changed_by_others": {"proxies": True, "active_sequence": False}}, clean]) \
        == (True, False)
    assert lut._conditions([clean, {**clean, "proxies": {"during": True}}]) == (True, False)   # one export only
    assert lut._conditions([clean, {**clean, "changed_by_others": {"proxies": None, "active_sequence": None}}]) \
        == (False, True)
    assert lut._conditions([clean, {"proxies": {"during": False}}]) == (False, True)           # field missing


def test_index_6_refused_with_nothing_sent_goes_on(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    model = LutModel()
    original = model.write

    def write(script):
        if script_args(script)["index"] == 6:
            return Exception("Error: precondition: property is keyframed")
        return original(script)
    model.write = write
    result, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path))
    assert isinstance(result, dict), result
    assert result["stored"] is True and result["menu_value"] is None and result["rendered"] is True
    assert ("Index 6 (Input LUT) was not written: property is keyframed. Use --prove to see if the LUT renders."
            in result["warnings"])
    assert len(result["frames"]) == 2 and ScriptedSession.count == 2


def test_interrupt_during_frame_b(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    def stop(number, arguments):
        if number == 2:
            raise KeyboardInterrupt
        return None
    model = LutModel()
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), on_call=stop)
    assert isinstance(err, CliError) and err.code == "interrupted"
    assert "The LUT was written (stored: true)" in err.message and "check was stopped" in err.message
    assert err.payload["rendered"] is None and err.payload["difference"] is None
    assert [Path(p).name for p in ffmpeg["seen"]] == ["lut_before_00001000ms.png"]
    assert len(model.writes) == 2 and lease_free(bridge_dir)


def test_interrupt_during_the_put_back_of_frame_b(bridge_dir, tmp_path, ffmpeg, monkeypatch):  # noqa: F811
    """Ctrl-C during the read of frame B's put-back session: export_frames ends as not_restored with the frame
    there. No verdict: interrupted."""
    from cli_anything.premiere.core.recipes import frames
    original = frames._read_app
    reads = []

    def read(session, sequence_id):
        if sequence_id is None:
            reads.append(1)
            if len(reads) == 2:                 # frame A's put-back read is the first
                raise CliError("interrupted", "Stopped while Premiere ran a read-only script.")
        return original(session, sequence_id)
    monkeypatch.setattr(frames, "_read_app", read)
    err, _ = run_recipe(bridge_dir, LutModel(), tmp_path, cube(tmp_path))
    assert isinstance(err, CliError) and err.code == "interrupted", err
    assert err.payload["rendered"] is None and err.payload["stored"] is True
    assert err.payload["left_switched"] is not None
    assert [Path(p).name for p in ffmpeg["seen"]] == ["lut_before_00001000ms.png"]


def test_stale_frames_are_removed_before_frame_a(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    folder = Path(tmp_path) / "frames"
    folder.mkdir()
    for name in ("lut_before_00001000ms.png", "lut_after_00001000ms.png"):
        (folder / name).write_bytes(b"old")

    def fail(number, arguments):
        return {"is_error": True, "text": "fake export failure", "structured": None}
    err, _ = run_recipe(bridge_dir, LutModel(), tmp_path, cube(tmp_path), on_call=fail)
    assert err.code == "export_failed"
    assert not (folder / "lut_after_00001000ms.png").exists()
    assert not (folder / "lut_before_00001000ms.png").exists()


def test_dry_run_removes_nothing_and_names_the_files(live, capsys, tmp_path, ffmpeg):
    folder = Path(tmp_path) / "frames"
    folder.mkdir()
    old = folder / "lut_after_00001000ms.png"
    old.write_bytes(b"old")
    code, out, _ = lut_set(live, capsys, LutModel(), cube(tmp_path), "--dry-run", *prove_args(tmp_path))
    assert code == 0 and out["replaces"] == [str(old)] and old.read_bytes() == b"old"
    with panel_on(live, LutModel()):
        dispatch([*SET, "--sequence", "s1", "--track", "0", "--clip", "0", "--lut", str(cube(tmp_path)), "--dry-run",
                  *prove_args(tmp_path)])
    assert f"Would replace: {old}" in capsys.readouterr().out and old.exists()


# ---------- fix round 2: the blind start state ----------
UNKNOWN = ("The state of proxy display and the active sequence after the check could not be read, so there is no "
           "verdict.")


def test_index_6_refusal_ending_in_a_period_has_one_period(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    model = LutModel()
    original = model.write

    def write(script):
        if script_args(script)["index"] == 6:
            return Exception("Error: precondition: the property is locked.")
        return original(script)
    model.write = write
    result, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path))
    assert ("Index 6 (Input LUT) was not written: the property is locked. Use --prove to see if the LUT renders."
            in result["warnings"])
    assert not any(".." in w for w in result["warnings"])


@pytest.mark.parametrize("number, what", [(1, "proxies"), (2, "proxies"), (1, "active"), (2, "active")])
def test_blind_start_change_by_others_gives_no_verdict(bridge_dir, tmp_path, ffmpeg, number, what):  # noqa: F811
    """Proxies already off and the target already active: the exports switch nothing, and a change by someone else
    must still be seen."""
    model, app = LutModel(), AppModel(proxies=0, active="s1")
    result, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), app=app,
                           on_call=_someone_else(app, number, what))
    assert isinstance(result, dict), result
    assert result["rendered"] is None and result["difference"] == 48.0
    assert FOREIGN in result["warnings"] and UNKNOWN not in result["warnings"]
    assert len(model.writes) == 2
    if number == 2:
        assert app.writes == []                 # nothing was switched; the change after frame B is left alone
    else:                                       # frame B's export switched the changed item back and put it back
        assert app.writes == ([("proxies", 0), ("proxies", 1)] if what == "proxies"
                              else [("activate", "s1"), ("activate", "t3")])


def test_blind_start_without_a_change_gives_the_verdict(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    model, app = LutModel(), AppModel(proxies=0, active="s1")
    result, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), app=app)
    assert result["rendered"] is True and FOREIGN not in result["warnings"] and UNKNOWN not in result["warnings"]
    assert app.writes == [] and app.reads == 4          # session A and the read after, per export


def test_unknown_conditions_give_no_verdict(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    """A recovery marker appears during frame B's export: its read after cannot run, so the conditions are unknown."""
    model, app = LutModel(), AppModel(proxies=0, active="s1")

    def marker(number, arguments):
        if number == 2:
            (Path(bridge_dir) / ".host-recovery.json").write_text(
                '{"version":1,"reason":"unknown_command_outcome","nonce":"x"}')
        return None
    result, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), app=app, on_call=marker)
    assert isinstance(result, dict), result
    assert result["rendered"] is None and result["difference"] == 48.0
    assert UNKNOWN in result["warnings"] and FOREIGN not in result["warnings"]


# ---------- fix round 3: an interrupt in an export's session B ----------
def _stop_nth_export_session(monkeypatch, at: int):
    """Ctrl-C at the entry of the at-th WriteSession that export_frames opens (frame A: 1 and 2, frame B: 3 and 4)."""
    from cli_anything.premiere.core.recipes import frames
    from cli_anything.premiere.core.session import WriteSession
    seen = []

    class Stopping(WriteSession):
        def __enter__(self):
            seen.append(1)
            if len(seen) == at:
                raise KeyboardInterrupt
            return super().__enter__()
    monkeypatch.setattr(frames, "WriteSession", Stopping)


def test_blind_start_interrupt_in_frame_a_session_b_writes_nothing(bridge_dir, tmp_path, ffmpeg, monkeypatch):  # noqa: F811
    _stop_nth_export_session(monkeypatch, 2)
    model, app = LutModel(), AppModel(proxies=0, active="s1")
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), app=app)
    assert isinstance(err, CliError) and err.code == "interrupted", err
    assert "Nothing was written" in err.message
    assert model.writes == [] and model.resolves == 1 and err.payload["stored"] is False
    assert lease_free(bridge_dir)


def test_blind_start_interrupt_in_frame_b_session_b(bridge_dir, tmp_path, ffmpeg, monkeypatch):  # noqa: F811
    _stop_nth_export_session(monkeypatch, 4)
    model, app = LutModel(), AppModel(proxies=0, active="s1")
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), app=app)
    assert isinstance(err, CliError) and err.code == "interrupted", err
    assert "The LUT was written (stored: true)" in err.message
    assert err.payload["stored"] is True and err.payload["rendered"] is None and err.payload["difference"] is None
    assert len(model.writes) == 2 and lease_free(bridge_dir)


# ---------- fix round 4: one rule for every stop ----------
# One case per place where a stop can arrive in set_lut, per shape. Each names the exact effect writes the model
# must have received, what "stored" must say, and how many export calls ran (so no frame B after a stop).
from cli_anything.premiere.core import session as session_module  # noqa: E402
from cli_anything.premiere.core.recipes import effects, frames  # noqa: E402
from cli_anything.premiere.tests.test_recipes_frames import (  # noqa: E402
    NOT_PICKED_UP, RAW, READ_STOPPED, THREE, _write_shapes, hook_sessions, raise_at_call, stop_of)

BOTH_SWITCHED = [("proxies", 0), ("activate", "s1"), ("activate", "o1"), ("proxies", 1)]   # one export, put back


class LutStop:
    """One row of the stop table of set_lut. written: the Lumetri indexes the model received, in order. calls:
    the export calls that ran. app: "blind" (proxies off, s1 active: an export switches nothing) or "both"."""

    def __init__(self, point, where, shape, when, how, *, written, stored, calls, code="interrupted", app="blind",
                 app_writes=(), says="", prove=True, difference=None) -> None:
        self.point, self.where, self.shape, self.when, self.how = point, where, shape, when, how
        self.written, self.stored, self.calls, self.code, self.app = written, stored, calls, code, app
        self.app_writes, self.says, self.prove, self.difference = list(app_writes), says, prove, difference

    @property
    def id(self) -> str:
        return f"{self.point}-{self.shape}" + (f"-{self.when}" if self.when else "")


def lut_stop_cases() -> list[LutStop]:
    cases: list[LutStop] = []

    def add(point, where, shape, when, how, **expected):
        cases.append(LutStop(point, where, shape, when, how, **expected))

    def read_when(shape):
        return "before" if shape == "CLI" else "after"

    nothing = "Stored so far: false"
    # ----- session 1 -----
    for shape in RAW:
        add("L1", "session 1: the lease wait at entry", shape, "", ("session", "enter", 1, "before"),
            written=[], stored=False, calls=0, says=nothing)
        add("L4", "session 1: as the lease is given back", shape, "", ("session", "exit", 1, "after"),
            written=[], stored=False, calls=0, says=nothing)
    for shape in THREE:
        add("L2", "session 1: the guard at entry (state read)", shape, "", ("guard", 1),
            written=[], stored=False, calls=0)
        add("L3", "session 1: the resolve read", shape, "", ("session", "read", 1, read_when(shape)),
            written=[], stored=False, calls=0)
    # ----- frame A -----
    for shape in THREE:
        add("L5", "frame A: during the export call", shape, "", ("on_call", 1), written=[], stored=False, calls=1,
            app="both", app_writes=BOTH_SWITCHED, says="Nothing was written")
    for shape in RAW:
        add("L6", "frame A: the ffmpeg read", shape, "", ("pixels", "lut_before"), written=[], stored=False, calls=1,
            says=nothing)
    # ----- session 2, before index 4 -----
    for shape in RAW:
        add("L7", "session 2: the lease wait at entry", shape, "", ("session", "enter", 2, "before"),
            written=[], stored=False, calls=1, says=nothing)
    for shape in THREE:
        add("L8", "session 2: the guard at entry (state read)", shape, "", ("guard", 4),
            written=[], stored=False, calls=1)
        add("L9", "session 2: the resolve read, before index 4", shape, "", ("session", "read", 2, read_when(shape)),
            written=[], stored=False, calls=1)
    # ----- session 2, the writes -----
    for shape, when in _write_shapes():
        how = ("session", "write", 1, when)
        where = "session 2: during the write of index 4"
        if shape == "CLI":                           # not picked up: nothing was written, and stored says so
            add("L10", where, shape, when, how, written=[], stored=False, calls=1, says="Nothing was written")
        elif shape == "UNKNOWN":
            add("L10", where, shape, when, how, written=[4], stored=None, calls=1, code="unknown_outcome")
        else:                                        # the recipe cannot know whether it was sent: unknown
            add("L10", where, shape, when, how, written=[4] if when == "after" else [], stored=None, calls=1,
                says="Stored so far: unknown")
    for shape in RAW:
        add("L11", "session 2: between the writes of index 4 and index 6", shape, "", ("between_writes",),
            written=[4], stored=True, calls=1, says="Stored so far: true")
    for shape, when in _write_shapes():
        how = ("session", "write", 2, when)
        where = "session 2: during the write of index 6"
        written = [4, 6] if when == "after" else [4]
        if shape == "CLI":
            add("L12", where, shape, when, how, written=written, stored=True, calls=1,
                says="The LUT path in index 4 was stored. The write of index 6 (Input LUT) was not sent")
        elif shape == "UNKNOWN":
            add("L12", where, shape, when, how, written=written, stored=True, calls=1, code="unknown_outcome",
                says="The LUT path in index 4 was stored")
        else:
            add("L12", where, shape, when, how, written=written, stored=True, calls=1, says="Stored so far: true")
    for shape in RAW:
        add("L13", "session 2: as the lease is given back", shape, "", ("session", "exit", 2, "after"),
            written=[4, 6], stored=True, calls=1, says="Stored so far: true")
    # ----- frame B and the verdict -----
    for shape in THREE:
        add("L14", "frame B: during the export call", shape, "", ("on_call", 2), written=[4, 6], stored=True,
            calls=2, app="both", app_writes=BOTH_SWITCHED + BOTH_SWITCHED,
            says="The LUT was written (stored: true). The check was stopped, so there is no verdict.")
    for shape in RAW:
        add("L15", "frame B: the ffmpeg read", shape, "", ("pixels", "lut_after"), written=[4, 6], stored=True,
            calls=2, says="The LUT was written (stored: true); the render was not checked.")
        add("L16", "after frame B, while the verdict is made", shape, "", ("verdict",), written=[4, 6], stored=True,
            calls=2, says="Stored so far: true", difference=48.0)
    # ----- without --prove: the writes run in session 1 -----
    for shape, when in _write_shapes():
        how = ("session", "write", 1, when)
        where = "without --prove: during the write of index 4"
        if shape == "CLI":
            add("N1", where, shape, when, how, written=[], stored=False, calls=0, prove=False)
        elif shape == "UNKNOWN":
            add("N1", where, shape, when, how, written=[4], stored=None, calls=0, prove=False, code="unknown_outcome")
        else:
            add("N1", where, shape, when, how, written=[4] if when == "after" else [], stored=None, calls=0,
                prove=False)
    for shape, when in _write_shapes():
        add("N2", "without --prove: during the write of index 6", shape, when, ("session", "write", 2, when),
            written=[4, 6] if when == "after" else [4], stored=True, calls=0, prove=False,
            code="unknown_outcome" if shape == "UNKNOWN" else "interrupted")
    for shape in RAW:
        add("N3", "without --prove: as the lease is given back", shape, "", ("session", "exit", 1, "after"),
            written=[4, 6], stored=True, calls=0, prove=False, says="Stored so far: true")
    return cases


def run_lut_stop(case: LutStop, bridge_dir, tmp_path, monkeypatch, ffmpeg):  # noqa: F811
    """Run set_lut with the stop of `case` placed. Returns (the error, the effect model, the app model, how often
    the stop was raised)."""
    model = LutModel()
    app = AppModel() if case.app == "both" else AppModel(proxies=0, active="s1")
    text = NOT_PICKED_UP if case.how[0] == "session" and case.how[1] == "write" else READ_STOPPED
    exc = stop_of(case.shape, text)
    kind = case.how[0]
    count = {"hits": 0}
    on_call = None
    pixels = None

    def hit() -> None:
        count["hits"] += 1
        raise exc
    if kind == "session":                    # the sessions of lut.py only: an export's own sessions do not count
        count = hook_sessions(monkeypatch, lut, case.how[1], case.how[2], case.how[3], exc)
    elif kind == "guard":                    # every write session, the exports' too: 1 is session 1, 4 session 2
        count = raise_at_call(monkeypatch, session_module, "guarded_state", case.how[1], exc)
    elif kind == "between_writes":           # the second write_entry is index 6: raised before it does anything
        count = raise_at_call(monkeypatch, effects, "write_entry", 2, exc)
    elif kind == "verdict":
        count = raise_at_call(monkeypatch, lut, "_conditions", 1, exc)
    elif kind == "on_call":
        def on_call(number, arguments):
            if number == case.how[1]:
                hit()
    elif kind == "pixels":
        def pixels(path):
            if case.how[1] in Path(path).name:
                hit()
            return ffmpeg["stub"](path)
    else:
        raise AssertionError(kind)
    ScriptedSession.count = 0
    client = BridgeClient(bridge_dir, 0.0)
    extra = {"prove": True, "time": 1.0, "out_dir": Path(tmp_path) / "frames",
             "pixels": pixels or lut.frame_pixels} if case.prove else {}
    with panel_on(bridge_dir, model, app):
        with pytest.raises(CliError) as caught:
            lut.set_lut(client, lambda c: ScriptedSession(c, on_call), {"sequence_id": "s1", "track": 0, "clip": 0},
                        cube(tmp_path), **extra)
    return caught.value, model, app, count["hits"]


LUT_STOPS = lut_stop_cases()


@pytest.mark.parametrize("case", LUT_STOPS, ids=[case.id for case in LUT_STOPS])
def test_stop_table_of_set_lut(bridge_dir, tmp_path, monkeypatch, ffmpeg, case):  # noqa: F811
    """Every row of the stop table: what was written (exactly), what "stored" says, no verdict, the code, and
    that nothing ran after the stop (no later write, no later export)."""
    err, model, app, hits = run_lut_stop(case, bridge_dir, tmp_path, monkeypatch, ffmpeg)
    assert hits == 1, "the stop was not raised at the place the case names"
    assert [write[2] for write in model.writes] == case.written
    assert 7 not in [write[2] for write in model.writes]
    assert err.code == case.code, err
    payload = err.payload
    assert payload["stored"] is case.stored
    assert payload["rendered"] is None and payload["difference"] == case.difference
    assert ScriptedSession.count == case.calls
    assert app.writes == case.app_writes
    assert case.says in err.message, err.message
    assert "write_error" not in payload and lease_free(bridge_dir)
    assert not (Path(bridge_dir) / ".host-recovery.json").exists()


def test_stop_table_of_set_lut_covers_every_place_and_shape():
    by_point: dict = {}
    for case in LUT_STOPS:
        by_point.setdefault(case.point, set()).add((case.shape, case.when))
    assert sorted(by_point, key=lambda p: (p[0], int(p[1:]))) == [f"L{n}" for n in range(1, 17)] + ["N1", "N2", "N3"]
    for point in ("L10", "L12", "N1", "N2"):                  # a write: every shape, before and as it returns
        assert by_point[point] == set(_write_shapes()), point
    for point in ("L2", "L3", "L5", "L8", "L9", "L14"):       # a read or an export: all three shapes
        assert {shape for shape, _ in by_point[point]} == set(THREE), point
    assert len(LUT_STOPS) == len({case.id for case in LUT_STOPS})


def test_stop_before_the_pickup_of_index_6_does_not_go_on_to_frame_b(bridge_dir, tmp_path, monkeypatch, ffmpeg):  # noqa: F811
    """Index 6 refused before it was sent is the one case that goes on to frame B (a precondition, a recovery).
    A stop before the pickup of index 6 is not that case: no frame B, no switch write, and the warning says why
    index 6 was not written."""
    model, app = LutModel(), AppModel()
    hook_sessions(monkeypatch, lut, "write", 2, "before", CliError("interrupted", NOT_PICKED_UP))
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), app=app)
    assert isinstance(err, CliError) and err.code == "interrupted"
    assert ScriptedSession.count == 1 and app.writes == BOTH_SWITCHED          # frame A only
    assert ("Index 6 (Input LUT) was not written: the run was stopped before the write was sent. Use --prove to see "
            "if the LUT renders." in err.payload["warnings"])
    assert err.payload["stored"] is True and err.payload["rendered"] is None and err.payload["menu_value"] is None


def test_stop_in_a_later_send_of_index_4_is_not_called_nothing(bridge_dir, tmp_path, monkeypatch, ffmpeg):  # noqa: F811
    """Index 4 is written and does not read back; write_entry then sends its own put-back, and the stop arrives
    before that one is picked up. Something was sent, so the payload must not say that nothing was written."""
    model = LutModel()
    model.readback[str(cube(tmp_path).resolve()) + "/"] = "/another/path.cube/"      # index 4 reads back another text
    hook_sessions(monkeypatch, lut, "write", 2, "before", CliError("interrupted", NOT_PICKED_UP))
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), app=AppModel(proxies=0, active="s1"))
    assert isinstance(err, CliError) and err.code == "interrupted"
    assert [write[2] for write in model.writes] == [4]
    assert err.payload["stored"] is None and "Nothing was written" not in err.message
    assert ScriptedSession.count == 1


@pytest.mark.parametrize("frame", ["A", "B"])
def test_export_error_with_a_stopped_payload_counts_as_stopped(bridge_dir, tmp_path, monkeypatch, ffmpeg, frame):  # noqa: F811
    """An export fails first and the stop arrives in its put-back: export_frames keeps the export's code and says
    "stopped": true in the payload. set_lut treats that as a stop: frame A, nothing is written; frame B, no
    verdict. The stopped put-back wrote nothing more."""
    model, app = LutModel(), AppModel()
    wanted = 1 if frame == "A" else 2

    def fail(number, arguments):
        if number == wanted:
            return {"is_error": True, "text": "fake export failure", "structured": None}
        return None
    # The put-back of that export is the 3rd app write of frame A, or the 7th of the run for frame B.
    hook_sessions(monkeypatch, frames, "write", 3 if frame == "A" else 7, "before",
                  CliError("interrupted", NOT_PICKED_UP))
    err, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), app=app, on_call=fail)
    assert isinstance(err, CliError) and err.code == "interrupted", err
    assert err.payload["export"]["stopped"] is True
    assert err.payload["left_switched"] == {"proxies": False, "active_sequence": {"id": "s1", "name": "Main"}}
    assert err.payload["rendered"] is None and err.payload["difference"] is None
    if frame == "A":
        assert model.writes == [] and err.payload["stored"] is False and "Nothing was written" in err.message
        assert app.writes == [("proxies", 0), ("activate", "s1")]
    else:
        assert len(model.writes) == 2 and err.payload["stored"] is True and "no verdict" in err.message
        assert app.writes == BOTH_SWITCHED + [("proxies", 0), ("activate", "s1")]
    assert ScriptedSession.count == wanted and lease_free(bridge_dir)


# ---------- final fix A2: one "nothing was sent" rule ----------
def test_index_6_that_premiere_did_not_pick_up_goes_on_to_frame_b(bridge_dir, tmp_path, monkeypatch, ffmpeg):  # noqa: F811
    """not_applied means the CLI deleted its own command file: index 6 was not sent. That is a refusal, not a
    stop: the run goes on to frame B and the verdict, and the warning says why index 6 was not written."""
    model, app = LutModel(), AppModel()
    hook_sessions(monkeypatch, lut, "write", 2, "before",
                  CliError("not_applied", "Premiere did not pick up the command. Nothing was changed."))
    result, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), app=app)
    assert isinstance(result, dict), result
    assert ScriptedSession.count == 2 and result["rendered"] is True and result["stored"] is True
    assert [write[2] for write in model.writes] == [4]
    assert ("Index 6 (Input LUT) was not written: Premiere did not pick up the command. Nothing was changed. Use "
            "--prove to see if the LUT renders." in result["warnings"])


# ---------- final fix A4: rendered false only for an empty slot; the --prove wait ----------
def _not_empty(index_4: str, index_6) -> str:
    return (f"The picture did not change, but the LUT slot was not empty before the write (index 4 held {index_4}, "
            f"index 6 read {index_6}), so this run cannot show whether the new LUT renders.")


@pytest.mark.parametrize("index_4, index_6, shown_4, shown_6", [
    (EMPTY_LUT_SLOT, 2.0, '"<U+FEFE>"', "2.0"),              # a LUT chosen from the Input LUT menu
    ("Look A", 0.0, '"Look A"', "0.0"),                      # a value that is not a .cube path
    ("/luts/old.cube", 0.0, '"/luts/old.cube"', "0.0"),      # a path without the trailing slash
    ("x" * 80, 0.0, '"' + "x" * 57 + '..."', "0.0"),         # a long value, cut at 60 characters
])
def test_prove_null_when_the_slot_was_not_empty(live, capsys, tmp_path, ffmpeg, index_4, index_6, shown_4, shown_6):
    """I4: rendered false needs an empty slot before the write: index 4 held U+FEFE or nothing, and index 6 read 0.
    Any other value gives null, with a warning that names what index 4 held."""
    ffmpeg["after"] = ffmpeg["before"]
    model = LutModel()
    model.lumetri()[4]["value"] = index_4
    model.lumetri()[6]["value"] = index_6
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path))
    assert code == 0, out
    assert out["rendered"] is None and out["difference"] == 0.0 and out["stored"] is True
    assert out["path_before"] is None and out["slot_before"] == index_4
    assert _not_empty(shown_4, shown_6) in out["warnings"]
    assert SAME_PICTURE not in out["warnings"]


@pytest.mark.parametrize("index_4", [EMPTY_LUT_SLOT, ""])
def test_prove_false_needs_both_index_4_empty_and_index_6_zero(live, capsys, tmp_path, ffmpeg, index_4):
    ffmpeg["after"] = ffmpeg["before"]
    model = LutModel()
    model.lumetri()[4]["value"] = index_4
    code, out, _ = lut_set(live, capsys, model, cube(tmp_path), *prove_args(tmp_path))
    assert code == 1 and out["code"] == "not_rendered" and out["rendered"] is False
    assert out["slot_before"] == index_4 and not any("was not empty" in w for w in out["warnings"])


def test_prove_null_when_index_6_changed_before_the_write(bridge_dir, tmp_path, ffmpeg):  # noqa: F811
    """Session 1 reads an empty slot; while frame A is exported someone sets index 6 to 1. The write follows session
    2, so the slot was not empty right before it: null."""
    ffmpeg["after"] = ffmpeg["before"]
    model = LutModel()
    model.moves[2] = lambda: model.lumetri()[6].update(value=1.0)
    result, _ = run_recipe(bridge_dir, model, tmp_path, cube(tmp_path), pixels=ffmpeg["stub"])
    assert isinstance(result, dict), result
    assert result["rendered"] is None and _not_empty('"<U+FEFE>"', "1.0") in result["warnings"]


def test_human_line_shows_a_slot_value_that_is_not_a_path(live, capsys, tmp_path, ffmpeg):
    model = LutModel()
    model.lumetri()[4]["value"] = "Look A"
    path = cube(tmp_path)
    with panel_on(live, model):
        assert dispatch([*SET, "--sequence", "s1", "--track", "0", "--clip", "0", "--lut", str(path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert 'index 4 "Look A" -> ' in out and "(no LUT)" not in out


def test_prove_exports_wait_at_least_600_seconds(bridge_dir, tmp_path, ffmpeg, monkeypatch):  # noqa: F811
    """M2: each frame of --prove waits max(--timeout, 600) s (design 5.1: a timeout kills the server mid-export).
    The scripts of the write sessions keep --timeout."""
    seen = []
    real = lut.frames.export_frames

    def spy(*args, **kwargs):
        seen.append((kwargs["hard_limit_s"], kwargs["script_limit_s"]))
        return real(*args, **kwargs)
    monkeypatch.setattr(lut.frames, "export_frames", spy)
    result, sessions = run_recipe(bridge_dir, LutModel(), tmp_path, cube(tmp_path), pixels=ffmpeg["stub"])
    assert isinstance(result, dict), result
    assert seen == [(600.0, 120.0), (600.0, 120.0)]
    seen.clear()
    result, _ = run_recipe(bridge_dir, LutModel(), tmp_path, cube(tmp_path), pixels=ffmpeg["stub"], hard_limit_s=30.0)
    assert seen == [(600.0, 30.0), (600.0, 30.0)]
    seen.clear()
    result, _ = run_recipe(bridge_dir, LutModel(), tmp_path, cube(tmp_path), pixels=ffmpeg["stub"], hard_limit_s=900.0)
    assert seen == [(900.0, 900.0), (900.0, 900.0)]


def test_lut_help_says_how_long_prove_waits():
    from click.testing import CliRunner
    result = CliRunner().invoke(live_cli.live, ["lut", "set", "--help"])
    text = " ".join(result.output.split())
    assert "With --prove, each export waits up to 600 seconds, or up to --timeout when that is longer." in text
    assert ("The slot counts as empty only when index 4 holds no path (the one character U+FEFE, or nothing) and "
            "index 6 reads 0.") in text
