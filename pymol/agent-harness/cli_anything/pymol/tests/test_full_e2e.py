"""End-to-end tests for PyMOL CLI.

These tests verify full workflows: project creation, manipulation, PML script
generation, project roundtrips, and CLI subprocess invocation.
No actual PyMOL installation is required for most tests.
"""

import json
import os
import sys
import tempfile
import subprocess
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cli_anything.pymol.core.project import create_project, save_project, open_project, get_project_info
from cli_anything.pymol.core.structure import load_structure, list_structures, list_formats
from cli_anything.pymol.core.selection import create_selection, list_selections, list_macros
from cli_anything.pymol.core.representation import show_representation, hide_representation, list_representations, list_available
from cli_anything.pymol.core.coloring import apply_color, list_colors, list_schemes
from cli_anything.pymol.core.label import add_label, list_labels, list_label_formats
from cli_anything.pymol.core.view import set_view, get_view, set_setting, list_settings
from cli_anything.pymol.core.render import set_render_settings, get_render_settings, render_scene, list_render_presets
from cli_anything.pymol.core.session import Session
from cli_anything.pymol.utils.pml_gen import generate_full_script


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


# ── Project Lifecycle ──────────────────────────────────────────

class TestProjectLifecycle:
    def test_create_save_open_roundtrip(self, tmp_dir):
        proj = create_project(name="roundtrip")
        path = os.path.join(tmp_dir, "session.pymol-cli.json")
        save_project(proj, path)
        loaded = open_project(path)
        assert loaded["name"] == "roundtrip"
        assert loaded["render"]["width"] == 1920

    def test_project_with_structures_roundtrip(self, tmp_dir):
        proj = create_project(name="with_structures")
        # Create a dummy PDB file so load_structure can find it
        pdb_path = os.path.join(tmp_dir, "test.pdb")
        with open(pdb_path, "w") as f:
            f.write("ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00\nEND\n")
        load_structure(proj, pdb_path, object_name="protein1")
        load_structure(proj, pdb_path, object_name="protein2")

        path = os.path.join(tmp_dir, "session.json")
        save_project(proj, path)
        loaded = open_project(path)
        assert len(loaded["structures"]) == 2
        assert loaded["structures"][0]["object_name"] == "protein1"
        assert loaded["structures"][1]["object_name"] == "protein2"

    def test_project_with_representations_roundtrip(self, tmp_dir):
        proj = create_project(name="with_reps")
        show_representation(proj, target="all", rep_type="cartoon")
        show_representation(proj, target="all", rep_type="sticks",
                            settings={"stick_radius": 0.3})

        path = os.path.join(tmp_dir, "session.json")
        save_project(proj, path)
        loaded = open_project(path)
        assert len(loaded["representations"]) == 2
        assert loaded["representations"][0]["rep_type"] == "cartoon"
        assert loaded["representations"][1]["rep_type"] == "sticks"
        assert loaded["representations"][1]["settings"]["stick_radius"] == 0.3

    def test_project_with_colors_roundtrip(self, tmp_dir):
        proj = create_project(name="with_colors")
        apply_color(proj, target="chain_A", color="red")
        apply_color(proj, target="chain_B", rgb=[0.2, 0.6, 1.0])
        apply_color(proj, target="all", scheme="by_element")

        path = os.path.join(tmp_dir, "session.json")
        save_project(proj, path)
        loaded = open_project(path)
        assert len(loaded["colors"]) == 3
        assert loaded["colors"][0]["color_type"] == "named"
        assert loaded["colors"][0]["color_name"] == "red"
        assert loaded["colors"][1]["color_type"] == "rgb"
        assert loaded["colors"][1]["color_rgb"] == [0.2, 0.6, 1.0]
        assert loaded["colors"][2]["color_type"] == "scheme"
        assert loaded["colors"][2]["scheme"] == "by_element"

    def test_project_info_completeness(self):
        proj = create_project(name="info_test")
        # Create a dummy file for structure loading
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdb", mode="w", delete=False) as f:
            f.write("ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00\nEND\n")
            pdb_path = f.name
        try:
            load_structure(proj, pdb_path, object_name="struct1")
            load_structure(proj, pdb_path, object_name="struct2")
        finally:
            os.unlink(pdb_path)
        create_selection(proj, "active_site", "resi 100-200")
        show_representation(proj, "struct1", "cartoon")
        apply_color(proj, "struct1", color="marine")
        add_label(proj, "struct1 and name CA", format_preset="residue")

        info = get_project_info(proj)
        assert info["counts"]["structures"] == 2
        assert info["counts"]["selections"] == 1
        assert info["counts"]["representations"] == 1
        assert info["counts"]["colors"] == 1
        assert info["counts"]["labels"] == 1
        assert info["name"] == "info_test"
        assert "resolution" in info["render"]

    def test_complex_project_roundtrip(self, tmp_dir):
        """Create a complex project, save, reload, verify integrity."""
        proj = create_project(name="complex", profile="publication")

        # Create dummy PDB file
        pdb_path = os.path.join(tmp_dir, "complex.pdb")
        with open(pdb_path, "w") as f:
            f.write("ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00\nEND\n")

        # Load structures
        load_structure(proj, pdb_path, object_name="receptor")
        load_structure(proj, pdb_path, object_name="ligand")

        # Add selections
        create_selection(proj, "binding_site", "receptor and resi 100-150")
        create_selection(proj, "active_site", "receptor and resi 200-250")

        # Add representations
        show_representation(proj, "receptor", "cartoon")
        show_representation(proj, "receptor", "surface",
                            settings={"transparency": 0.5})
        show_representation(proj, "ligand", "sticks")

        # Add colors
        apply_color(proj, "receptor", color="marine")
        apply_color(proj, "ligand", color="yellow")
        apply_color(proj, "binding_site", scheme="by_element")

        # Add labels
        add_label(proj, "binding_site and name CA", format_preset="residue",
                  color=[1.0, 1.0, 0.0], size=16)

        # Set view
        set_view(proj, zoom=2.0, position=[10.0, 20.0, -30.0])

        # Set render settings
        set_render_settings(proj, width=3000, height=3000, ray=True,
                            ray_trace_mode=1, dpi=300)

        # Save and reload
        path = os.path.join(tmp_dir, "complex.json")
        save_project(proj, path)
        loaded = open_project(path)

        assert len(loaded["structures"]) == 2
        assert len(loaded["selections"]) == 2
        assert len(loaded["representations"]) == 3
        assert len(loaded["colors"]) == 3
        assert len(loaded["labels"]) == 1
        assert loaded["view"]["zoom"] == 2.0
        assert loaded["view"]["position"] == [10.0, 20.0, -30.0]
        assert loaded["render"]["width"] == 3000
        assert loaded["render"]["height"] == 3000
        assert loaded["render"]["dpi"] == 300


# ── PML Script Generation ─────────────────────────────────────

class TestPMLScriptGeneration:
    def test_empty_project_script(self):
        proj = create_project(name="empty")
        script = generate_full_script(proj, "/tmp/render.png")
        assert "reinitialize" in script
        assert "set" in script
        assert "quit" in script

    def test_script_with_structure_pdb_id(self):
        """PDB ID should generate 'fetch' command."""
        proj = create_project()
        # Manually add a structure with pdb_id to avoid file existence check
        proj["structures"].append({
            "id": 0,
            "name": "1abc",
            "object_name": "1abc",
            "source": "pdb",
            "path": None,
            "pdb_id": "1ABC",
            "state": 1,
            "visible": True,
            "chains": [],
            "residue_count": 0,
            "atom_count": 0,
        })
        script = generate_full_script(proj, "/tmp/render.png")
        assert "fetch 1ABC" in script
        assert "name=1abc" in script

    def test_script_with_structure_file_path(self):
        """File path should generate 'load' command."""
        proj = create_project()
        proj["structures"].append({
            "id": 0,
            "name": "myprotein",
            "object_name": "myprotein",
            "source": "pdb",
            "path": "/data/structures/myprotein.pdb",
            "pdb_id": None,
            "state": 1,
            "visible": True,
            "chains": [],
            "residue_count": 0,
            "atom_count": 0,
        })
        script = generate_full_script(proj, "/tmp/render.png")
        assert "load /data/structures/myprotein.pdb" in script
        assert "myprotein" in script

    def test_script_with_selections(self):
        proj = create_project()
        create_selection(proj, "active_site", "resi 100-200 and chain A")
        create_selection(proj, "helix_region", "ss H")
        script = generate_full_script(proj, "/tmp/render.png")
        assert "select active_site, resi 100-200 and chain A" in script
        assert "select helix_region, ss H" in script
        assert "deselect" in script

    def test_script_with_representations(self):
        proj = create_project()
        show_representation(proj, "all", "cartoon")
        show_representation(proj, "ligand", "sticks",
                            settings={"stick_radius": 0.3})
        script = generate_full_script(proj, "/tmp/render.png")
        assert "hide everything, all" in script
        assert "show cartoon, all" in script
        assert "show sticks, ligand" in script
        assert "set stick_radius, 0.3, ligand" in script

    def test_script_with_named_colors(self):
        proj = create_project()
        apply_color(proj, "chain_A", color="red")
        apply_color(proj, "chain_B", color="marine")
        script = generate_full_script(proj, "/tmp/render.png")
        assert "color red, chain_A" in script
        assert "color marine, chain_B" in script

    def test_script_with_rgb_colors(self):
        proj = create_project()
        apply_color(proj, "ligand", rgb=[0.8, 0.2, 0.5])
        script = generate_full_script(proj, "/tmp/render.png")
        assert "set_color custom_0" in script
        assert "0.8" in script
        assert "0.2" in script
        assert "0.5" in script
        assert "color custom_0, ligand" in script

    def test_script_with_color_schemes(self):
        proj = create_project()
        apply_color(proj, "protein", scheme="by_element")
        apply_color(proj, "all", scheme="rainbow")
        script = generate_full_script(proj, "/tmp/render.png")
        # by_element uses "color atomic"
        assert "atomic" in script
        # rainbow uses "spectrum count"
        assert "spectrum count" in script

    def test_script_with_labels(self):
        proj = create_project()
        add_label(proj, "name CA and resi 50", format_preset="residue",
                  color=[1.0, 1.0, 0.0], size=18)
        script = generate_full_script(proj, "/tmp/render.png")
        assert "set label_size, 18" in script
        assert "set label_color" in script
        assert "label " in script

    def test_script_with_view_settings(self):
        proj = create_project()
        # Add a structure so orient gets triggered
        proj["structures"].append({
            "id": 0, "name": "test", "object_name": "test",
            "source": "pdb", "path": "/tmp/test.pdb", "pdb_id": None,
            "state": 1, "visible": True, "chains": [], "residue_count": 0,
            "atom_count": 0,
        })
        set_view(proj, zoom=2.5)
        script = generate_full_script(proj, "/tmp/render.png")
        assert "orient" in script
        assert "zoom all, 2.5" in script

    def test_script_with_render_settings(self):
        proj = create_project()
        set_render_settings(proj, width=3000, height=3000, ray=True, dpi=300)
        script = generate_full_script(proj, "/tmp/render.png")
        assert "ray 3000, 3000" in script
        assert "png /tmp/render.png, dpi=300" in script

    def test_script_with_no_ray_uses_viewport(self):
        proj = create_project()
        set_render_settings(proj, width=800, height=600, ray=False)
        script = generate_full_script(proj, "/tmp/render.png", ray=False)
        assert "viewport 800, 600" in script
        assert "draw" in script

    def test_script_with_transparent_background(self):
        proj = create_project()
        set_render_settings(proj, transparent_background=True)
        script = generate_full_script(proj, "/tmp/render.png")
        assert "set ray_opaque_background, 0" in script

    def test_script_without_transparent_background(self):
        proj = create_project()
        set_render_settings(proj, transparent_background=False)
        script = generate_full_script(proj, "/tmp/render.png")
        assert "set ray_opaque_background, 1" in script


# ── Workflow Tests ─────────────────────────────────────────────

class TestWorkflows:
    def test_protein_visualization_workflow(self, tmp_dir):
        """Load structure -> cartoon + sticks -> color by chain -> add labels -> render."""
        proj = create_project(name="protein_viz", profile="presentation")

        # Load structure (manual entry since no real file needed for script gen)
        proj["structures"].append({
            "id": 0, "name": "1abc", "object_name": "1abc",
            "source": "pdb", "path": None, "pdb_id": "1ABC",
            "state": 1, "visible": True, "chains": ["A", "B"],
            "residue_count": 500, "atom_count": 4000,
        })

        # Set representations: cartoon backbone + sticks for active site
        show_representation(proj, "1abc", "cartoon")
        create_selection(proj, "active_site", "1abc and resi 100-150")
        show_representation(proj, "active_site", "sticks")

        # Color by chain
        apply_color(proj, "1abc", scheme="by_chain")

        # Add residue labels on active site
        add_label(proj, "active_site and name CA", format_preset="residue",
                  color=[1.0, 1.0, 0.0], size=14)

        # Set render
        set_render_settings(proj, preset="presentation")

        # Generate script
        output_path = os.path.join(tmp_dir, "protein_viz.png")
        result = render_scene(proj, output_path, overwrite=True)
        assert os.path.exists(result["script_path"])

        with open(result["script_path"]) as f:
            script = f.read()
        assert "fetch 1ABC" in script
        assert "show cartoon" in script
        assert "show sticks" in script
        assert "label " in script

    def test_ligand_binding_site_workflow(self, tmp_dir):
        """Load structure -> select protein/ligand -> different reps -> color -> render."""
        proj = create_project(name="binding_site")

        proj["structures"].append({
            "id": 0, "name": "3htb", "object_name": "3htb",
            "source": "pdb", "path": None, "pdb_id": "3HTB",
            "state": 1, "visible": True, "chains": ["A"],
            "residue_count": 300, "atom_count": 2500,
        })

        # Selections
        create_selection(proj, "protein", "3htb and polymer.protein")
        create_selection(proj, "ligand", "3htb and organic")
        create_selection(proj, "binding_pocket", "3htb and byres (organic around 5.0)")

        # Representations
        show_representation(proj, "protein", "cartoon")
        show_representation(proj, "ligand", "sticks",
                            settings={"stick_radius": 0.3})
        show_representation(proj, "binding_pocket", "lines")
        show_representation(proj, "protein", "surface",
                            settings={"transparency": 0.7})

        # Colors
        apply_color(proj, "protein", color="slate")
        apply_color(proj, "ligand", color="yellow")
        apply_color(proj, "binding_pocket", scheme="by_element")

        # Render
        output_path = os.path.join(tmp_dir, "binding.png")
        result = render_scene(proj, output_path, overwrite=True)
        assert os.path.exists(result["script_path"])

    def test_publication_figure_workflow(self, tmp_dir):
        """Load, set representations, white background, high quality render."""
        proj = create_project(name="pub_figure", profile="publication")

        proj["structures"].append({
            "id": 0, "name": "1ubq", "object_name": "1ubq",
            "source": "pdb", "path": None, "pdb_id": "1UBQ",
            "state": 1, "visible": True, "chains": ["A"],
            "residue_count": 76, "atom_count": 660,
        })

        # White background
        set_setting(proj, "bg_color", [1.0, 1.0, 1.0])

        # Cartoon representation
        show_representation(proj, "1ubq", "cartoon",
                            settings={"cartoon_oval_length": 1.4,
                                      "cartoon_oval_width": 0.3})

        # Color by secondary structure
        apply_color(proj, "1ubq", scheme="by_ss")

        # High quality render
        set_render_settings(proj, preset="high_quality")

        output_path = os.path.join(tmp_dir, "pub_figure.png")
        result = render_scene(proj, output_path, overwrite=True)

        with open(result["script_path"]) as f:
            script = f.read()
        assert "bg_color [1.0, 1.0, 1.0]" in script
        assert "show cartoon" in script
        assert "ray 3000, 3000" in script

    def test_multi_structure_comparison_workflow(self, tmp_dir):
        """Load 2 structures, different colors, align."""
        proj = create_project(name="comparison")

        proj["structures"].append({
            "id": 0, "name": "1abc", "object_name": "structure_A",
            "source": "pdb", "path": None, "pdb_id": "1ABC",
            "state": 1, "visible": True, "chains": ["A"],
            "residue_count": 200, "atom_count": 1500,
        })
        proj["structures"].append({
            "id": 1, "name": "2xyz", "object_name": "structure_B",
            "source": "pdb", "path": None, "pdb_id": "2XYZ",
            "state": 1, "visible": True, "chains": ["A"],
            "residue_count": 210, "atom_count": 1600,
        })

        # Representations
        show_representation(proj, "structure_A", "cartoon")
        show_representation(proj, "structure_B", "cartoon")

        # Different colors
        apply_color(proj, "structure_A", color="marine")
        apply_color(proj, "structure_B", color="salmon")

        # Save and verify
        path = os.path.join(tmp_dir, "comparison.json")
        save_project(proj, path)
        loaded = open_project(path)

        assert len(loaded["structures"]) == 2
        assert len(loaded["representations"]) == 2
        assert len(loaded["colors"]) == 2
        assert loaded["colors"][0]["color_name"] == "marine"
        assert loaded["colors"][1]["color_name"] == "salmon"

        # Generate script
        output_path = os.path.join(tmp_dir, "comparison.png")
        script = generate_full_script(loaded, output_path)
        assert "fetch 1ABC" in script
        assert "fetch 2XYZ" in script
        assert "color marine, structure_A" in script
        assert "color salmon, structure_B" in script

    def test_undo_redo_workflow(self):
        """Test undo/redo through a complex editing workflow."""
        sess = Session()
        proj = create_project(name="undo_test")
        sess.set_project(proj)

        # Step 1: Add representation
        sess.snapshot("add cartoon")
        show_representation(proj, "all", "cartoon")
        assert len(proj["representations"]) == 1

        # Step 2: Add color
        sess.snapshot("add color")
        apply_color(proj, "all", color="red")
        assert len(proj["colors"]) == 1

        # Step 3: Add selection
        sess.snapshot("add selection")
        create_selection(proj, "active", "resi 100-200")
        assert len(proj["selections"]) == 1

        # Undo step 3
        sess.undo()
        assert len(sess.get_project()["selections"]) == 0

        # Undo step 2
        sess.undo()
        assert len(sess.get_project()["colors"]) == 0

        # Redo step 2
        sess.redo()
        assert len(sess.get_project()["colors"]) == 1

        # Redo step 3
        sess.redo()
        assert len(sess.get_project()["selections"]) == 1


# ── CLI Subprocess Tests ───────────────────────────────────────

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
    CLI_BASE = _resolve_cli("cli-anything-pymol")

    def _run(self, args, check=True):
        return subprocess.run(
            self.CLI_BASE + args,
            capture_output=True, text=True,
            check=check,
        )

    def test_help(self):
        result = self._run(["--help"])
        assert result.returncode == 0
        assert "PyMOL CLI" in result.stdout

    def test_project_new(self, tmp_dir):
        out = os.path.join(tmp_dir, "test.json")
        result = self._run(["project", "new", "-o", out])
        assert result.returncode == 0
        assert os.path.exists(out)

    def test_project_new_json(self, tmp_dir):
        out = os.path.join(tmp_dir, "test.json")
        result = self._run(["--json", "project", "new", "-o", out])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["render"]["resolution"] == "1920x1080"

    def test_project_profiles(self):
        result = self._run(["project", "profiles"])
        assert result.returncode == 0
        assert "publication" in result.stdout

    def test_representation_available(self):
        result = self._run(["representation", "available"])
        assert result.returncode == 0
        assert "cartoon" in result.stdout

    def test_render_presets(self):
        result = self._run(["render", "presets"])
        assert result.returncode == 0
        assert "standard" in result.stdout

    def test_color_schemes(self):
        result = self._run(["color", "schemes"])
        assert result.returncode == 0
        assert "by_element" in result.stdout

    def test_selection_macros(self):
        result = self._run(["selection", "macros"])
        assert result.returncode == 0
        assert "protein" in result.stdout

    def test_structure_formats(self):
        result = self._run(["structure", "formats"])
        assert result.returncode == 0
        assert "pdb" in result.stdout

    def test_label_formats(self):
        result = self._run(["label", "formats"])
        assert result.returncode == 0
        assert "residue" in result.stdout

    def test_full_workflow_json(self, tmp_dir):
        proj_path = os.path.join(tmp_dir, "workflow.json")

        # Create project
        self._run(["--json", "project", "new", "-o", proj_path, "-n", "workflow"])
        assert os.path.exists(proj_path)
        with open(proj_path) as f:
            data = json.load(f)
        assert data["name"] == "workflow"

        # Verify the project file is loadable via CLI
        loaded_result = self._run(["--json", "--project", proj_path, "project", "info"])
        assert loaded_result.returncode == 0
        info = json.loads(loaded_result.stdout)
        assert info["name"] == "workflow"

    def test_cli_error_handling(self):
        result = self._run(["project", "open", "/nonexistent/file.json"], check=False)
        assert result.returncode != 0


# ── Script Validity Tests ──────────────────────────────────────

class TestScriptValidity:
    """Verify generated .pml scripts contain expected PML commands.

    PML scripts are PyMOL's scripting language, not Python, so we check for
    expected command presence rather than Python compilation.
    """

    def test_empty_project_script_has_core_commands(self):
        proj = create_project()
        script = generate_full_script(proj, "/tmp/render.png")
        # Core commands every script should have
        assert "reinitialize" in script
        assert "bg_color" in script
        assert "set depth_cue" in script
        assert "set antialias" in script
        assert "set ray_trace_mode" in script
        assert "quit" in script

    def test_full_script_has_all_sections(self):
        """Ensure a complex project generates script with all expected sections."""
        proj = create_project()

        # Add structure
        proj["structures"].append({
            "id": 0, "name": "1abc", "object_name": "1abc",
            "source": "pdb", "path": None, "pdb_id": "1ABC",
            "state": 1, "visible": True, "chains": [],
            "residue_count": 0, "atom_count": 0,
        })

        # Add selection
        create_selection(proj, "helix", "ss H")

        # Add representation
        show_representation(proj, "1abc", "cartoon")

        # Add color
        apply_color(proj, "1abc", color="red")

        # Add label
        add_label(proj, "1abc and name CA", format_preset="residue")

        # Set view with zoom
        set_view(proj, zoom=2.0)

        # Generate
        script = generate_full_script(proj, "/tmp/render.png")

        # Verify all sections present
        assert "# --- Settings ---" in script
        assert "# --- Load structures ---" in script
        assert "# --- Selections ---" in script
        assert "# --- Representations ---" in script
        assert "# --- Colors ---" in script
        assert "# --- Labels ---" in script
        assert "# --- View ---" in script
        assert "# --- Render ---" in script

    def test_script_disabled_selection_is_commented(self):
        proj = create_project()
        create_selection(proj, "test_sel", "resi 1-10")
        proj["selections"][0]["enabled"] = False
        script = generate_full_script(proj, "/tmp/render.png")
        assert "# (disabled) select test_sel" in script

    def test_script_invisible_structure_is_disabled(self):
        proj = create_project()
        proj["structures"].append({
            "id": 0, "name": "hidden", "object_name": "hidden",
            "source": "pdb", "path": None, "pdb_id": "1ABC",
            "state": 1, "visible": False, "chains": [],
            "residue_count": 0, "atom_count": 0,
        })
        script = generate_full_script(proj, "/tmp/render.png")
        assert "fetch 1ABC" in script
        assert "disable hidden" in script

    def test_script_ray_shadows_disabled(self):
        proj = create_project()
        set_render_settings(proj, ray_shadows=False)
        script = generate_full_script(proj, "/tmp/render.png")
        assert "set ray_shadows, 0" in script

    def test_script_settings_completeness(self):
        """Verify all global settings appear in the script."""
        proj = create_project()
        set_setting(proj, "depth_cue", False)
        set_setting(proj, "fog", True)
        set_setting(proj, "orthoscopic", False)
        script = generate_full_script(proj, "/tmp/render.png")
        assert "set depth_cue, 0" in script
        assert "set fog, 1" in script
        assert "set orthoscopic, off" in script

    def test_script_multiple_representations_same_target(self):
        """Multiple reps on same target should each produce a show command."""
        proj = create_project()
        show_representation(proj, "protein", "cartoon")
        show_representation(proj, "protein", "surface")
        script = generate_full_script(proj, "/tmp/render.png")
        assert "show cartoon, protein" in script
        assert "show surface, protein" in script

    def test_render_scene_creates_script_file(self, tmp_dir):
        proj = create_project()
        show_representation(proj, "all", "cartoon")
        output_path = os.path.join(tmp_dir, "render.png")
        result = render_scene(proj, output_path, overwrite=True)
        assert os.path.exists(result["script_path"])
        with open(result["script_path"]) as f:
            content = f.read()
        assert "reinitialize" in content
        assert "quit" in content


# ── True Backend E2E Tests (requires PyMOL installed) ──────────

class TestPyMOLBackend:
    """Tests that verify PyMOL is installed and accessible."""

    def test_pymol_is_installed(self):
        from cli_anything.pymol.utils.pymol_backend import find_pymol
        path = find_pymol()
        assert os.path.exists(path)
        print(f"\n  PyMOL binary: {path}")

    def test_pymol_version(self):
        from cli_anything.pymol.utils.pymol_backend import get_version
        version = get_version()
        assert "PyMOL" in version
        print(f"\n  PyMOL version: {version}")


class TestPyMOLRenderE2E:
    """True E2E tests: generate project -> PML script -> pymol -cq -> verify output."""

    def test_render_empty_scene(self, tmp_dir):
        """Render an empty scene with PyMOL."""
        from cli_anything.pymol.utils.pymol_backend import render_scene_headless

        proj = create_project(name="empty")
        set_render_settings(proj, width=320, height=240, ray=False)

        output_path = os.path.join(tmp_dir, "empty.png")
        script = generate_full_script(proj, output_path)
        result = render_scene_headless(script, output_path, timeout=120)

        assert os.path.exists(result["output"])
        assert result["file_size"] > 0
        print(f"\n  Rendered empty scene: {result['output']} ({result['file_size']:,} bytes)")

    def test_render_fetched_structure(self, tmp_dir):
        """Render a fetched PDB structure."""
        from cli_anything.pymol.utils.pymol_backend import render_scene_headless

        proj = create_project(name="fetch_test")
        proj["structures"].append({
            "id": 0, "name": "1ubq", "object_name": "1ubq",
            "source": "pdb", "path": None, "pdb_id": "1UBQ",
            "state": 1, "visible": True, "chains": ["A"],
            "residue_count": 76, "atom_count": 660,
        })
        show_representation(proj, "1ubq", "cartoon")
        apply_color(proj, "1ubq", scheme="by_ss")
        set_render_settings(proj, width=320, height=240, ray=False)

        output_path = os.path.join(tmp_dir, "ubiquitin.png")
        script = generate_full_script(proj, output_path)
        result = render_scene_headless(script, output_path, timeout=120)

        assert os.path.exists(result["output"])
        assert result["file_size"] > 100
        print(f"\n  Rendered ubiquitin: {result['output']} ({result['file_size']:,} bytes)")

    def test_render_with_representations_and_colors(self, tmp_dir):
        """Render with cartoon + sticks + colors."""
        from cli_anything.pymol.utils.pymol_backend import render_scene_headless

        proj = create_project(name="styled")
        proj["structures"].append({
            "id": 0, "name": "1ubq", "object_name": "1ubq",
            "source": "pdb", "path": None, "pdb_id": "1UBQ",
            "state": 1, "visible": True, "chains": ["A"],
            "residue_count": 76, "atom_count": 660,
        })

        show_representation(proj, "1ubq", "cartoon")
        create_selection(proj, "active", "1ubq and resi 1-20")
        show_representation(proj, "active", "sticks")
        apply_color(proj, "1ubq", color="marine")
        apply_color(proj, "active", color="yellow")

        set_render_settings(proj, width=320, height=240, ray=False)

        output_path = os.path.join(tmp_dir, "styled.png")
        script = generate_full_script(proj, output_path)
        result = render_scene_headless(script, output_path, timeout=120)

        assert os.path.exists(result["output"])
        assert result["file_size"] > 100
        print(f"\n  Rendered styled: {result['output']} ({result['file_size']:,} bytes)")

    def test_render_publication_quality(self, tmp_dir):
        """Render publication-quality image with ray tracing."""
        from cli_anything.pymol.utils.pymol_backend import render_scene_headless

        proj = create_project(name="publication", profile="publication")
        proj["structures"].append({
            "id": 0, "name": "1ubq", "object_name": "1ubq",
            "source": "pdb", "path": None, "pdb_id": "1UBQ",
            "state": 1, "visible": True, "chains": ["A"],
            "residue_count": 76, "atom_count": 660,
        })

        set_setting(proj, "bg_color", [1.0, 1.0, 1.0])
        show_representation(proj, "1ubq", "cartoon")
        apply_color(proj, "1ubq", scheme="by_chain")
        set_render_settings(proj, width=640, height=480, ray=True, dpi=150)

        output_path = os.path.join(tmp_dir, "publication.png")
        script = generate_full_script(proj, output_path)
        result = render_scene_headless(script, output_path, timeout=180)

        assert os.path.exists(result["output"])
        assert result["file_size"] > 100
        print(f"\n  Rendered publication: {result['output']} ({result['file_size']:,} bytes)")


class TestPyMOLRenderScriptE2E:
    """Test the render_script function directly."""

    def test_run_minimal_pml_script(self, tmp_dir):
        """Run a minimal PML script through PyMOL."""
        from cli_anything.pymol.utils.pymol_backend import render_script

        script_path = os.path.join(tmp_dir, "test_script.pml")
        output_path = os.path.join(tmp_dir, "minimal.png")

        script_content = f"""reinitialize
bg_color [1.0, 1.0, 1.0]
viewport 160, 120
draw
png {output_path}, dpi=72
quit
"""
        with open(script_path, "w") as f:
            f.write(script_content)

        result = render_script(script_path, timeout=120)
        assert result["returncode"] == 0, f"PyMOL failed: {result['stderr'][-500:]}"

        assert os.path.exists(output_path), (
            f"No output file found. stdout: {result['stdout'][-500:]}"
        )
        size = os.path.getsize(output_path)
        assert size > 0
        print(f"\n  Minimal render: {output_path} ({size:,} bytes)")
