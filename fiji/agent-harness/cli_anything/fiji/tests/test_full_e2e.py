"""Fiji CLI — End-to-end tests with real Fiji invocation.

These tests invoke the actual Fiji application in headless mode.
Fiji MUST be installed — tests will FAIL (not skip) if missing.
"""

import json
import os
import subprocess
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from cli_anything.fiji.utils.fiji_backend import (
    find_fiji, run_macro, process_image, create_test_image, get_version,
)
from cli_anything.fiji.core import project as proj_mod
from cli_anything.fiji.core import image as img_mod
from cli_anything.fiji.core import processing as proc_mod
from cli_anything.fiji.core import export as export_mod


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="fiji_e2e_") as d:
        yield d


# ═══════════════════════════════════════════════════════════════
# Fiji Backend Tests
# ═══════════════════════════════════════════════════════════════

class TestFijiBackend:
    def test_find_fiji(self):
        path = find_fiji()
        assert os.path.isfile(path)
        assert os.access(path, os.X_OK)
        print(f"\n  Fiji found: {path}")

    def test_get_version(self):
        ver = get_version()
        assert ver  # Non-empty
        print(f"\n  Fiji version: {ver}")

    def test_create_test_image(self, tmp_dir):
        output_path = os.path.join(tmp_dir, "test_noise.tif")
        result = create_test_image(output_path, width=128, height=128)
        assert os.path.exists(result["output"])
        assert result["file_size"] > 0
        # Verify TIFF magic bytes
        with open(result["output"], "rb") as f:
            magic = f.read(4)
            assert magic[:2] in (b"II", b"MM"), f"Not a TIFF: {magic}"
        print(f"\n  Test image: {result['output']} ({result['file_size']:,} bytes)")

    def test_process_image_blur(self, tmp_dir):
        # Create a test image first
        input_path = os.path.join(tmp_dir, "input.tif")
        create_test_image(input_path, width=128, height=128, fill="noise")

        # Apply Gaussian blur
        output_path = os.path.join(tmp_dir, "blurred.tif")
        result = process_image(
            input_path, output_path,
            macro_code='run("Gaussian Blur...", "sigma=3");',
            output_format="tiff",
        )
        assert os.path.exists(result["output"])
        assert result["file_size"] > 0
        print(f"\n  Blurred: {result['output']} ({result['file_size']:,} bytes)")

    def test_process_image_threshold(self, tmp_dir):
        input_path = os.path.join(tmp_dir, "input.tif")
        create_test_image(input_path, width=128, height=128, fill="noise")

        output_path = os.path.join(tmp_dir, "threshold.tif")
        result = process_image(
            input_path, output_path,
            macro_code='setAutoThreshold("Otsu dark"); run("Convert to Mask");',
            output_format="tiff",
        )
        assert os.path.exists(result["output"])
        assert result["file_size"] > 0
        print(f"\n  Threshold: {result['output']} ({result['file_size']:,} bytes)")

    def test_run_macro_print(self, tmp_dir):
        """Test running a simple macro that prints output."""
        result = run_macro('print("Hello from Fiji CLI test");')
        assert result["returncode"] == 0
        # Fiji prints to stdout
        assert "Hello" in result["stdout"] or "Hello" in result["stderr"]


# ═══════════════════════════════════════════════════════════════
# Full Pipeline Tests
# ═══════════════════════════════════════════════════════════════

class TestFullPipeline:
    def test_full_pipeline_blur_export(self, tmp_dir):
        """Complete workflow: create project → add image → process → export."""
        # Step 1: Create test image via Fiji
        input_path = os.path.join(tmp_dir, "source.tif")
        create_test_image(input_path, width=256, height=256, fill="noise")

        # Step 2: Create project and add image
        proj = proj_mod.create_project(name="blur_test", width=256, height=256)
        img_mod.add_image(proj, input_path, name="source")

        # Step 3: Add processing steps
        proc_mod.add_processing_step(proj, "gaussian_blur", params={"sigma": 2.0})
        proc_mod.add_processing_step(proj, "enhance_contrast", params={"saturated": 0.5})

        # Step 4: Export
        output_path = os.path.join(tmp_dir, "processed.tif")
        result = export_mod.render(proj, output_path, preset="tiff", overwrite=True)

        assert os.path.exists(result["output"])
        assert result["file_size"] > 0
        assert result["processing_steps"] == 2
        with open(result["output"], "rb") as f:
            magic = f.read(2)
            assert magic in (b"II", b"MM"), "Output is not a valid TIFF"
        print(f"\n  Pipeline output: {result['output']} ({result['file_size']:,} bytes)")

    def test_full_pipeline_png_export(self, tmp_dir):
        """Export to PNG format."""
        input_path = os.path.join(tmp_dir, "source.tif")
        create_test_image(input_path, width=128, height=128, fill="ramp")

        proj = proj_mod.create_project(name="png_test")
        img_mod.add_image(proj, input_path)
        proc_mod.add_processing_step(proj, "find_edges")

        output_path = os.path.join(tmp_dir, "output.png")
        result = export_mod.render(proj, output_path, preset="png", overwrite=True)

        assert os.path.exists(result["output"])
        assert result["file_size"] > 0
        with open(result["output"], "rb") as f:
            magic = f.read(4)
            assert magic == b"\x89PNG", "Output is not a valid PNG"
        print(f"\n  PNG output: {result['output']} ({result['file_size']:,} bytes)")


# ═══════════════════════════════════════════════════════════════
# CLI Subprocess Tests
# ═══════════════════════════════════════════════════════════════

def _resolve_cli(name):
    """Resolve installed CLI command; falls back to python -m for dev.

    Set env CLI_ANYTHING_FORCE_INSTALLED=1 to require the installed command.
    """
    import shutil
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


class TestCLISubprocess:
    CLI_BASE = _resolve_cli("cli-anything-fiji")

    def _run(self, args, check=True):
        return subprocess.run(
            self.CLI_BASE + args,
            capture_output=True, text=True,
            check=check,
        )

    def test_help(self):
        result = self._run(["--help"])
        assert result.returncode == 0
        assert "Fiji CLI" in result.stdout

    def test_json_project_new(self, tmp_dir):
        out = os.path.join(tmp_dir, "test.json")
        result = self._run(["--json", "project", "new", "-n", "cli_test", "-o", out])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["name"] == "cli_test"
        assert os.path.exists(out)

    def test_project_roundtrip(self, tmp_dir):
        proj_path = os.path.join(tmp_dir, "rt.json")
        # Create
        self._run(["--json", "project", "new", "-n", "roundtrip", "-o", proj_path])
        # Open and info
        result = self._run(["--json", "--project", proj_path, "project", "info"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["name"] == "roundtrip"

    def test_process_pipeline(self, tmp_dir):
        proj_path = os.path.join(tmp_dir, "proc.json")
        self._run(["project", "new", "-n", "proc_test", "-o", proj_path])

        # Add processing steps
        self._run(["--project", proj_path, "process", "add", "gaussian_blur", "-p", "sigma=3.0"])
        self._run(["--project", proj_path, "process", "add", "auto_threshold"])

        # Check log
        result = self._run(["--json", "--project", proj_path, "process", "log"])
        data = json.loads(result.stdout)
        assert len(data) == 2

    def test_roi_management(self, tmp_dir):
        proj_path = os.path.join(tmp_dir, "roi.json")
        self._run(["project", "new", "-n", "roi_test", "-o", proj_path])

        # Add ROIs
        self._run(["--project", proj_path, "roi", "add", "rectangle",
                    "--x", "10", "--y", "20", "-w", "100", "-h", "50"])
        self._run(["--project", proj_path, "roi", "add", "oval",
                    "--x", "0", "--y", "0", "-w", "30", "-h", "30"])

        # List
        result = self._run(["--json", "--project", proj_path, "roi", "list"])
        data = json.loads(result.stdout)
        assert len(data) == 2

    def test_backend_find(self):
        result = self._run(["--json", "backend", "find"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "path" in data

    def test_list_operations(self):
        result = self._run(["--json", "process", "list-ops", "-c", "filter"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert len(data) > 0
        assert all(d["category"] == "filter" for d in data)

    def test_export_presets(self):
        result = self._run(["--json", "export", "presets"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        names = [p["name"] for p in data]
        assert "tiff" in names
        assert "png" in names

    def test_full_workflow_with_fiji(self, tmp_dir):
        """Complete E2E: create project → add real image → process → export via Fiji."""
        proj_path = os.path.join(tmp_dir, "workflow.json")
        input_path = os.path.join(tmp_dir, "input.tif")
        output_path = os.path.join(tmp_dir, "output.tif")

        # Create test image via Fiji backend directly
        create_test_image(input_path, width=128, height=128, fill="noise")

        # CLI workflow
        self._run(["project", "new", "-n", "workflow_test", "-o", proj_path])
        self._run(["--project", proj_path, "image", "add", input_path])
        self._run(["--project", proj_path, "process", "add", "gaussian_blur", "-p", "sigma=2.0"])
        self._run(["--project", proj_path, "project", "save", proj_path])

        result = self._run([
            "--json", "--project", proj_path, "export", "render",
            output_path, "-p", "tiff", "--overwrite"
        ])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert os.path.exists(data["output"])
        assert data["file_size"] > 0

        # Verify TIFF format
        with open(data["output"], "rb") as f:
            magic = f.read(2)
            assert magic in (b"II", b"MM"), "Output is not a valid TIFF"

        print(f"\n  Full workflow output: {data['output']} ({data['file_size']:,} bytes)")
