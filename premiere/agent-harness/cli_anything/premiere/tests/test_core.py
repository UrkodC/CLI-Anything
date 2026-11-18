"""Unit tests on small hand-written project XML (no real project, no Premiere needed)."""
from __future__ import annotations

import hashlib
import json

import pytest

from cli_anything.premiere.core import prproj
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.premiere_cli import dispatch
from cli_anything.premiere.tests import _helpers as H


@pytest.fixture()
def proj_file(tmp_path):
    return H.write_project(tmp_path / "unit.prproj")


def run(capsys, *argv):
    code = dispatch(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def run_json(capsys, *argv):
    code, out, _ = run(capsys, "--json", *argv)
    return code, json.loads(out)


# ---------- loading ----------
def test_loads_gzip_and_plain_xml(tmp_path):
    a = prproj.Project.load(H.write_project(tmp_path / "a.prproj", compressed=True))
    b = prproj.Project.load(H.write_project(tmp_path / "b.prproj", compressed=False))
    assert a.compressed and not b.compressed
    assert [s.name for s in a.sequences()] == [s.name for s in b.sequences()] == ["Main"]


def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(CliError) as err:
        prproj.Project.load(tmp_path / "nope.prproj")
    assert err.value.code == "file_not_found"


def test_garbage_and_wrong_root_are_refused(tmp_path):
    bad = tmp_path / "bad.prproj"
    bad.write_bytes(b"\x1f\x8b not really gzip")
    with pytest.raises(CliError) as err:
        prproj.Project.load(bad)
    assert err.value.code == "bad_file"
    other = tmp_path / "other.prproj"
    other.write_text("<Something/>")
    with pytest.raises(CliError) as err:
        prproj.Project.load(other)
    assert err.value.code == "not_a_project"
    notxml = tmp_path / "notxml.prproj"
    notxml.write_text("hello")
    with pytest.raises(CliError) as err:
        prproj.Project.load(notxml)
    assert err.value.code == "bad_xml"


def test_entity_bomb_is_refused(tmp_path):
    bomb = tmp_path / "bomb.prproj"
    bomb.write_text('<?xml version="1.0"?><!DOCTYPE b [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><PremiereData>&b;</PremiereData>')
    with pytest.raises(CliError) as err:
        prproj.Project.load(bomb)
    assert err.value.code == "bad_xml"


# ---------- project and sequence ----------
def test_info_reports_version_and_counts(proj_file):
    info = prproj.Project.load(proj_file).info()
    assert info["project_format_version"] == 46 and info["written_by"] == "Premiere Pro 27.0.0"
    assert info["format_warning"] is None
    assert info["sequences"] == 1 and info["media_files"] == 2 and info["clips_with_color_mode"] == 1


def test_unknown_format_version_warns(tmp_path):
    info = prproj.Project.load(H.write_project(tmp_path / "v99.prproj", project_version=99)).info()
    assert info["written_by"] is None and "99" in info["format_warning"]


def test_smoke_read_format_is_named_and_still_warns(tmp_path):
    info = prproj.Project.load(H.write_project(tmp_path / "v45.prproj", project_version=45)).info()
    assert info["written_by"] == "Premiere Pro 26.5.2" and "smoke-read" in info["format_warning"]


def test_sequence_size_rate_and_length(proj_file):
    seq = prproj.Project.load(proj_file).find_sequence(None)
    assert (seq.width, seq.height) == (3840, 2160)
    assert seq.fps == pytest.approx(24000 / 1001, rel=1e-9)
    assert prproj.ticks_to_seconds(seq.end_ticks) == pytest.approx(144 * 1001 / 24000)
    kinds = [(t.kind, t.index, t.muted) for t in seq.tracks]
    assert kinds == [("video", 0, False), ("audio", 0, True)]


def test_sequence_must_be_named_when_there_are_two(tmp_path):
    proj = prproj.Project.load(H.write_project(tmp_path / "two.prproj", two_sequences=True))
    with pytest.raises(CliError) as err:
        proj.find_sequence(None)
    assert err.value.code == "sequence_needed"
    assert proj.find_sequence("other").name == "Other"       # case-insensitive name
    assert proj.find_sequence("seq-main").name == "Main"     # id
    with pytest.raises(CliError) as err:
        proj.find_sequence("missing")
    assert err.value.code == "sequence_not_found"


# ---------- clips ----------
def test_clip_times_and_media(proj_file):
    clips = prproj.Project.load(proj_file).find_sequence("Main").tracks[0].clips
    a, b = clips
    assert (a.name, b.name) == ("shot_a.mp4", "shot_b.mp4")
    assert (a.start_ticks, a.end_ticks) == (0, 96 * H.FRAME)
    assert (a.in_ticks, a.out_ticks) == (24 * H.FRAME, 120 * H.FRAME)
    assert (b.in_ticks, b.out_ticks) == (None, None)            # an untrimmed clip has no in/out in the file
    assert a.media_path == "/footage/shot_a.mp4" and b.media_path == "/footage/shot_b.mp4"


# ---------- effects ----------
def test_effect_order_values_and_render_position(proj_file):
    a = prproj.Project.load(proj_file).find_sequence("Main").tracks[0].clips[0]
    assert [e.display_name for e in a.effects] == ["Motion", "Lumetri Color", "ProcAmp"]      # file order
    by = {e.display_name: e for e in a.effects}
    assert by["Motion"].intrinsic and by["Motion"].render_position is None
    assert by["ProcAmp"].render_position == 1 and by["Lumetri Color"].render_position == 2  # last in file renders first
    assert by["ProcAmp"].bypass is True and by["Lumetri Color"].bypass is False
    assert by["ProcAmp"].param(index=0).value == pytest.approx(7.699999809265)


def test_point_bool_and_keyframes(proj_file):
    motion = prproj.Project.load(proj_file).find_sequence("Main").tracks[0].clips[0].effects[0]
    assert motion.param("Position").kind == "point" and motion.param("Position").value == [0.5, 0.5]
    rot = motion.param("Rotation")
    assert rot.value == 0
    assert [(k["media_ticks"], k["value"]) for k in rot.keyframes] == [(24 * H.FRAME, 1.5), (72 * H.FRAME, -0.5)]
    assert rot.keyframes[0]["media_seconds"] == pytest.approx(1.001)


def test_lumetri_lut_path_and_duplicate_names_by_index(proj_file):
    lum = prproj.Project.load(proj_file).find_sequence("Main").tracks[0].clips[0].effects[1]
    summary = prproj.lumetri_summary(lum)
    assert summary["input_lut_path"] == H.LUT_PATH.rstrip("/")
    assert summary["temperature"] == -46.5 and summary["tint"] == pytest.approx(8.8)
    blob = lum.param(index=0)
    assert blob.kind == "opaque" and blob.opaque_bytes == 39 and blob.text is None   # binary stays opaque
    assert lum.param(index=3).value is True and lum.param(index=1).value is False
    assert prproj.lumetri_summary(prproj.Project.load(proj_file).find_sequence("Main").tracks[0].clips[0].effects[2]) is None


def test_color_mode_is_detected_and_not_decoded(proj_file, tmp_path):
    b = prproj.Project.load(proj_file).find_sequence("Main").tracks[0].clips[1]
    cm = [e for e in b.effects if e.color_mode]
    assert len(cm) == 1 and cm[0].raw_summary["decoded"] is False
    assert b.as_dict()["has_color_mode"] is True
    plain = prproj.Project.load(H.write_project(tmp_path / "nocm.prproj", with_color_mode=False))
    assert plain.info()["clips_with_color_mode"] == 0


def test_repeated_binary_value_is_resolved_by_its_hash(proj_file):
    # Premiere stores a binary value once; later copies carry only the BinaryHash.
    second = prproj.Project.load(proj_file).find_sequence("Main").tracks[0].clips[1]
    lum = next(e for e in second.effects if e.match_name == prproj.LUMETRI_MATCH_NAME)
    assert lum.param(index=4).opaque_bytes == len(H.LUT_PATH.encode("utf-16-le"))
    assert prproj.lumetri_summary(lum)["input_lut_path"] == H.LUT_PATH.rstrip("/")


def test_lumetri_summary_gives_nothing_when_the_layout_moved(proj_file):
    lum = prproj.Project.load(proj_file).find_sequence("Main").tracks[0].clips[0].effects[1]
    lum.params[14].name = "Something else"
    summary = prproj.lumetri_summary(lum)
    assert summary["layout_recognized"] is False and summary["temperature"] is None and summary["tint"] == pytest.approx(8.8)


def test_color_mode_note_never_says_none_as_a_fact(tmp_path):
    plain = prproj.Project.load(H.write_project(tmp_path / "nocm.prproj", with_color_mode=False))
    assert plain.color_mode_markers == 0 and "never checked on a real Color Mode project" in plain.info()["color_mode_note"]
    # markers in the file, but on no timeline clip: the reader must say so, not "none"
    xml = H.project_xml(with_color_mode=False).replace("</PremiereData>", "\t<Orphan ObjectID=\"900\"><MatchName>AE.ADBE CompoundComponent</MatchName></Orphan>\n</PremiereData>")
    odd = tmp_path / "odd.prproj"
    odd.write_text(xml)
    info = prproj.Project.load(odd).info()
    assert info["clips_with_color_mode"] == 0 and info["color_mode_markers"] == 1 and "do not trust" in info["color_mode_note"]


# ---------- CLI ----------
def test_cli_project_info_json(capsys, proj_file):
    code, data = run_json(capsys, "project", "info", str(proj_file))
    assert code == 0 and data["ok"] is True
    assert data["project"]["sequences"] == 1 and data["project"]["project_format_version"] == 46


def test_cli_sequence_list_and_clips(capsys, proj_file):
    code, data = run_json(capsys, "sequence", "list", str(proj_file))
    assert code == 0 and data["sequences"][0]["name"] == "Main" and data["sequences"][0]["clips"] == 2
    code, data = run_json(capsys, "sequence", "clips", str(proj_file))
    assert code == 0 and [c["name"] for c in data["tracks"][0]["clips"]] == ["shot_a.mp4", "shot_b.mp4"]
    assert data["tracks"][0]["clips"][0]["in_seconds"] == pytest.approx(1.001)
    code, out, _ = run(capsys, "sequence", "clips", str(proj_file))
    assert code == 0 and "[Color Mode]" in out and "V1" in out


def test_cli_clip_effects_render_order_and_filter(capsys, proj_file):
    code, data = run_json(capsys, "clip", "effects", str(proj_file), "--clip", "0")
    assert code == 0
    assert [e["display_name"] for e in data["effects"]] == ["ProcAmp", "Lumetri Color", "Motion"]   # render order, intrinsic last
    lum = data["effects"][1]
    assert lum["lumetri"]["input_lut_path"] == H.LUT_PATH.rstrip("/")
    assert all(p["name"].strip() and p["kind"] != "opaque" for p in lum["params"])                 # default hides binary and unnamed
    code, data = run_json(capsys, "clip", "effects", str(proj_file), "--clip", "0", "--effect", "AE.ADBE Lumetri", "--all")
    assert code == 0 and len(data["effects"]) == 1 and any(p["kind"] == "opaque" for p in data["effects"][0]["params"])


def test_cli_errors_are_json_and_exit_1(capsys, proj_file, tmp_path):
    code, data = run_json(capsys, "clip", "effects", str(proj_file), "--clip", "9")
    assert code == 1 and data == {"ok": False, "error": data["error"], "code": "clip_not_found"}
    code, data = run_json(capsys, "clip", "effects", str(proj_file), "--clip", "0", "--effect", "Nope")
    assert code == 1 and data["code"] == "effect_not_found"
    code, data = run_json(capsys, "project", "info", str(tmp_path / "missing.prproj"))
    assert code == 1 and data["code"] == "file_not_found"
    code, _, err = run(capsys, "sequence", "clips", str(proj_file), "--sequence", "Nope")
    assert code == 1 and "No single sequence" in err


def test_cli_colormode_list_and_media(capsys, proj_file):
    code, data = run_json(capsys, "colormode", "list", str(proj_file))
    assert code == 0 and [(c["sequence"], c["clip"], c["name"]) for c in data["clips"]] == [("Main", 1, "shot_b.mp4")]
    code, data = run_json(capsys, "project", "media", str(proj_file))
    assert code == 0 and data["media"] == ["/footage/shot_a.mp4", "/footage/shot_b.mp4"]


def test_status_reports_read_only(capsys):
    code, data = run_json(capsys, "status")
    assert code == 0 and data["writes_projects"] is False and "46" in data["tested_project_formats"]
    assert "45" in data["smoke_read_project_formats"] and "45" not in data["tested_project_formats"]


def test_no_command_ever_changes_the_file(capsys, proj_file):
    before = (hashlib.sha256(proj_file.read_bytes()).hexdigest(), proj_file.stat().st_mtime_ns)
    for argv in (["project", "info"], ["project", "media"], ["sequence", "list"], ["sequence", "clips"], ["colormode", "list"]):
        assert dispatch(["--json", *argv, str(proj_file)]) == 0
    assert dispatch(["clip", "effects", str(proj_file), "--clip", "0", "--all"]) == 0
    capsys.readouterr()
    assert (hashlib.sha256(proj_file.read_bytes()).hexdigest(), proj_file.stat().st_mtime_ns) == before
    assert sorted(p.name for p in proj_file.parent.iterdir()) == ["unit.prproj"]      # and it leaves nothing next to it
