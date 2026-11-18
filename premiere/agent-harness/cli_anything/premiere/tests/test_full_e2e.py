"""End-to-end tests on a REAL project saved by Premiere Pro 27.0.0.

The fixture is local and untracked (it stores absolute paths of the machine that made it).
Default place: tests/fixtures_local/fixture27.prproj with fixture27.expected.json next to it,
or set PREMIERE_FIXTURE to another project that has the same expected.json layout.
How the fixture is made: see tests/TEST.md. Tests skip when it is missing.

The expected values were read back from Premiere itself through its scripting bridge, so
these tests check the reader against the real software's own answers.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cli_anything.premiere.core import prproj

DEFAULT = Path(__file__).parent / "fixtures_local" / "fixture27.prproj"
FIXTURE = Path(os.environ.get("PREMIERE_FIXTURE", DEFAULT))
EXPECTED = FIXTURE.with_name(FIXTURE.stem + ".expected.json")
pytestmark = pytest.mark.skipif(not (FIXTURE.is_file() and EXPECTED.is_file()),
                                reason=f"real Premiere fixture not found at {FIXTURE} (see tests/TEST.md)")


def _resolve_cli(name):
    """Resolve installed CLI command; falls back to python -m for dev.

    Set env CLI_ANYTHING_FORCE_INSTALLED=1 to require the installed command.
    """
    force = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed command: {path}")
        return [path]
    if force:
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    module = name.replace("cli-anything-", "cli_anything.") + "." + name.split("-")[-1] + "_cli"
    print(f"[_resolve_cli] Falling back to: {sys.executable} -m {module}")
    return [sys.executable, "-m", module]


@pytest.fixture(scope="module")
def expected():
    return json.loads(EXPECTED.read_text())


@pytest.fixture(scope="module")
def project():
    return prproj.Project.load(FIXTURE)


def _sha():
    return hashlib.sha256(FIXTURE.read_bytes()).hexdigest()


class TestReaderAgainstPremiere:
    def test_format_is_the_tested_one(self, project):
        info = project.info()
        print(f"\n  fixture: {FIXTURE} ({info['size_bytes']} bytes), format {info['project_format_version']}, saved {info['saved']}")
        assert info["compressed"] is True and info["project_format_version"] == 46 and info["format_warning"] is None

    def test_sequence_matches_premiere(self, project, expected):
        seq = project.find_sequence(expected["sequence"]["name"])
        exp = expected["sequence"]
        assert seq.uid == exp["id"] and (seq.width, seq.height) == (exp["width"], exp["height"])
        assert seq.frame_ticks == exp["frame_ticks"]
        assert prproj.ticks_to_seconds(seq.end_ticks) == pytest.approx(exp["duration_seconds"], abs=1e-9)

    def test_clip_times_match_premiere(self, project, expected):
        clips = project.find_sequence(expected["sequence"]["name"]).tracks[0].clips
        assert len(clips) == len(expected["clips"])
        for clip, exp in zip(clips, expected["clips"]):
            got = [prproj.ticks_to_seconds(v) for v in (clip.start_ticks, clip.end_ticks, clip.in_ticks, clip.out_ticks)]
            assert got == pytest.approx([exp["start"], exp["end"], exp["in"], exp["out"]], abs=1e-9)

    def test_render_order_is_the_order_the_effects_were_applied(self, project, expected):
        clips = project.find_sequence(expected["sequence"]["name"]).tracks[0].clips
        for clip, exp in zip(clips, expected["clips"]):
            applied = sorted((e for e in clip.effects if not e.intrinsic), key=lambda e: e.render_position)
            assert [e.display_name for e in applied] == exp["applied_in_order"]

    def test_parameter_values_and_indices_match_premiere(self, project, expected):
        clips = project.find_sequence(expected["sequence"]["name"]).tracks[0].clips
        for clip, exp in zip(clips, expected["clips"]):
            by = {e.display_name: e for e in clip.effects}
            for effect, index, name, value in exp["values"]:
                p = by[effect].param(index=index)
                assert p is not None and p.name == name, f"{effect}[{index}] is {p.name if p else None!r}, Premiere says {name!r}"
                assert p.value == pytest.approx(value, rel=1e-6, abs=1e-9)

    def test_keyframes_match_premiere(self, project, expected):
        clip = project.find_sequence(expected["sequence"]["name"]).tracks[0].clips[0]
        rot = next(e for e in clip.effects if e.display_name == "Motion").param("Rotation")
        exp = expected["clips"][0]
        assert [k["media_ticks"] for k in rot.keyframes] == exp["rotation_key_ticks"]
        assert [(k["media_seconds"], k["value"]) for k in rot.keyframes] == [pytest.approx(pair) for pair in exp["rotation_keys"]]

    def test_lumetri_lut_file_is_read_from_the_project(self, project, expected):
        clip = project.find_sequence(expected["sequence"]["name"]).tracks[0].clips[0]
        lum = next(e for e in clip.effects if e.match_name == prproj.LUMETRI_MATCH_NAME)
        path = prproj.lumetri_summary(lum)["input_lut_path"]
        assert path is not None and Path(path).name == expected["clips"][0]["lut_file"]

    def test_media_exists_on_disk(self, project):
        paths = project.media_paths()
        assert len(paths) == 1 and Path(paths[0]).is_file()


class TestCLISubprocess:
    CLI_BASE = _resolve_cli("cli-anything-premiere")

    def _run(self, *args, check=True):
        return subprocess.run(self.CLI_BASE + list(args), capture_output=True, text=True, check=check, timeout=120)

    def test_help_and_version(self):
        assert "Read Premiere Pro project files" in self._run("--help").stdout
        assert "0.1.0" in self._run("--version").stdout

    def test_project_info_json(self):
        data = json.loads(self._run("--json", "project", "info", str(FIXTURE)).stdout)
        assert data["ok"] is True and data["project"]["sequences"] == 1 and data["project"]["written_by"] == "Premiere Pro 27.0.0"

    def test_sequence_clips_json(self, expected):
        data = json.loads(self._run("sequence", "clips", str(FIXTURE), "--json").stdout)
        clips = data["tracks"][0]["clips"]
        assert [round(c["start_seconds"], 6) for c in clips] == [c["start"] for c in expected["clips"]]
        assert clips[0]["effects"] == ["Motion", "Lumetri Color", "ASC CDL", "ProcAmp"]

    def test_clip_effects_human_and_json(self, expected):
        text = self._run("clip", "effects", str(FIXTURE), "--clip", "0").stdout
        assert text.index("ProcAmp") < text.index("ASC CDL") < text.index("Lumetri Color")     # printed in render order
        assert "Input LUT file:" in text and "identity.cube" in text
        data = json.loads(self._run("--json", "clip", "effects", str(FIXTURE), "--clip", "1", "--effect", "VR De-Noise").stdout)
        level = next(p for p in data["effects"][0]["params"] if p["name"] == "Noise Level")
        assert level["index"] == 5 and level["value"] == pytest.approx(0.02)

    def test_errors_exit_1_with_json(self):
        result = self._run("--json", "clip", "effects", str(FIXTURE), "--clip", "7", check=False)
        assert result.returncode == 1 and json.loads(result.stdout)["code"] == "clip_not_found"

    def test_reading_never_changes_the_project(self):
        before = (_sha(), FIXTURE.stat().st_mtime_ns, sorted(p.name for p in FIXTURE.parent.iterdir()))
        for args in (["project", "info"], ["project", "media"], ["sequence", "list"], ["sequence", "clips"], ["colormode", "list"]):
            self._run("--json", *args, str(FIXTURE))
        self._run("clip", "effects", str(FIXTURE), "--clip", "0", "--all")
        after = (_sha(), FIXTURE.stat().st_mtime_ns, sorted(p.name for p in FIXTURE.parent.iterdir()))
        assert after == before
        print(f"\n  unchanged: {FIXTURE.name} sha256 {before[0][:16]}...")
