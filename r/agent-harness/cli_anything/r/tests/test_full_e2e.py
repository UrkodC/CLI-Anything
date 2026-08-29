"""E2E tests for R CLI — requires R/Rscript installed."""

import os
import sys
import json
import pytest
import subprocess
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from cli_anything.r.utils.r_backend import (
    find_r, get_version, run_expression, run_script,
)
from cli_anything.r.core import project as proj_mod
from cli_anything.r.core import data as data_mod
from cli_anything.r.core import analysis as analysis_mod
from cli_anything.r.core import plot as plot_mod


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="r_e2e_") as d:
        yield d


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


# ===================================================================
# R Backend Tests
# ===================================================================

class TestRBackend:
    def test_find_r(self):
        path = find_r()
        assert isinstance(path, str)
        assert len(path) > 0
        print(f"\n  Rscript found: {path}")

    def test_get_version(self):
        ver = get_version()
        assert ver  # Non-empty
        # R version output typically contains "R" or "Rscript" or "version"
        lower = ver.lower()
        assert "r" in lower or "version" in lower or "rscript" in lower
        print(f"\n  R version: {ver}")

    def test_run_expression(self):
        result = run_expression("cat(1+1)")
        assert result["returncode"] == 0
        assert "2" in result["stdout"]

    def test_run_script(self, tmp_dir):
        script_path = os.path.join(tmp_dir, "test.R")
        with open(script_path, "w") as f:
            f.write('cat("hello\\n")\n')
        result = run_script(script_path)
        assert result["returncode"] == 0
        assert "hello" in result["stdout"]


# ===================================================================
# Full Pipeline Tests
# ===================================================================

class TestFullPipeline:
    def test_create_and_inspect_builtin(self, tmp_dir):
        """Create project, add iris, build inspect script, run, parse JSON, verify 150 rows."""
        proj = proj_mod.create_project(name="inspect_test")
        data_mod.add_dataset(proj, "iris", dataset_type="builtin")
        script_code = data_mod.build_inspect_script(proj, 0)

        script_path = os.path.join(tmp_dir, "inspect.R")
        with open(script_path, "w") as f:
            f.write(script_code)

        result = run_script(script_path)
        assert result["returncode"] == 0, f"Script failed: {result['stderr']}"

        data = json.loads(result["stdout"])
        assert data["nrow"] == 150

    def test_regression_pipeline(self, tmp_dir):
        """Create project, add iris, add regression, build script, run, verify r.squared."""
        proj = proj_mod.create_project(name="regression_test")
        data_mod.add_dataset(proj, "iris", dataset_type="builtin")
        analysis_mod.add_analysis(
            proj, "regression", 0,
            params={"formula": "Sepal.Length ~ Sepal.Width"}
        )
        script_code = analysis_mod.build_analysis_script(proj, 0)

        script_path = os.path.join(tmp_dir, "regression.R")
        with open(script_path, "w") as f:
            f.write(script_code)

        result = run_script(script_path)
        assert result["returncode"] == 0, f"Script failed: {result['stderr']}"

        data = json.loads(result["stdout"])
        assert "r.squared" in data
        assert isinstance(data["r.squared"], float)

    def test_plot_render(self, tmp_dir):
        """Create project, add iris, add scatter, build plot script, run, verify PNG."""
        proj = proj_mod.create_project(name="plot_test")
        data_mod.add_dataset(proj, "iris", dataset_type="builtin")
        plot_mod.add_plot(
            proj, "scatter", 0,
            aes_mapping={"x": "Sepal.Length", "y": "Sepal.Width"}
        )

        output_path = os.path.join(tmp_dir, "plot.png")
        script_code = plot_mod.build_plot_script(proj, 0, output_path)

        script_path = os.path.join(tmp_dir, "plot_script.R")
        with open(script_path, "w") as f:
            f.write(script_code)

        result = run_script(script_path)
        assert result["returncode"] == 0, f"Script failed: {result['stderr']}"
        assert os.path.exists(output_path), "Plot output file not created"

        # Verify PNG magic bytes
        with open(output_path, "rb") as f:
            magic = f.read(4)
            assert magic == b"\x89PNG", f"Not a PNG: {magic}"


# ===================================================================
# CLI Subprocess Tests
# ===================================================================

class TestCLISubprocess:
    CLI_BASE = _resolve_cli("cli-anything-r")

    def _run(self, args, check=True):
        return subprocess.run(
            self.CLI_BASE + args,
            capture_output=True, text=True, check=check,
        )

    def test_help(self):
        result = self._run(["--help"])
        assert result.returncode == 0

    def test_project_new_json(self, tmp_dir):
        out = os.path.join(tmp_dir, "test.json")
        result = self._run(["--json", "project", "new", "-n", "test", "-o", out])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["name"] == "test"

    def test_backend_version(self):
        result = self._run(["--json", "backend", "version"])
        assert result.returncode == 0
        output = result.stdout
        assert "version" in output.lower() or "r" in output.lower()

    def test_backend_run_expr(self):
        result = self._run(["--json", "backend", "run-expr", "cat(42)"])
        assert result.returncode == 0
        assert "42" in result.stdout

    def test_full_workflow(self, tmp_dir):
        """Create project -> load iris -> add scatter plot -> render -> verify PNG."""
        proj_path = os.path.join(tmp_dir, "workflow.json")
        plot_output = os.path.join(tmp_dir, "output.png")

        # Create project
        self._run(["project", "new", "-n", "workflow_test", "-o", proj_path])

        # Load iris builtin dataset
        self._run(["--project", proj_path, "data", "load", "iris", "--type", "builtin"])

        # Add scatter plot
        self._run([
            "--project", proj_path, "plot", "scatter", "0",
            "--x", "Sepal.Length", "--y", "Sepal.Width",
        ])

        # Render plot
        result = self._run([
            "--json", "--project", proj_path, "export", "render-plot",
            "0", plot_output, "--overwrite",
        ])
        assert result.returncode == 0

        # Verify PNG exists and has correct magic bytes
        assert os.path.exists(plot_output), "Plot output file not created"
        with open(plot_output, "rb") as f:
            magic = f.read(4)
            assert magic == b"\x89PNG", f"Not a PNG: {magic}"
