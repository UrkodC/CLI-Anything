"""Unit tests for PyMOL CLI core modules.

Tests use synthetic data only — no real structure files or PyMOL installation.
"""

import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cli_anything.pymol.core.project import (
    create_project, open_project, save_project, get_project_info, list_profiles,
    PROFILES,
)
from cli_anything.pymol.core.structure import (
    load_structure, remove_structure, rename_structure,
    get_structure, list_structures, set_structure_property, list_formats,
    SUPPORTED_FORMATS,
)
from cli_anything.pymol.core.selection import (
    create_selection, remove_selection, update_selection,
    get_selection, list_selections, list_macros,
    SELECTION_MACROS,
)
from cli_anything.pymol.core.representation import (
    show_representation, hide_representation, remove_representation,
    set_representation_setting, get_representation, list_representations,
    list_available, get_representation_info,
    REPRESENTATION_REGISTRY,
)
from cli_anything.pymol.core.coloring import (
    apply_color, remove_color, list_colors, get_color,
    list_named_colors, list_schemes,
    NAMED_COLORS, COLOR_SCHEMES,
)
from cli_anything.pymol.core.view import (
    set_view, get_view, set_setting, list_settings, list_view_presets,
    VIEW_PRESETS,
)
from cli_anything.pymol.core.label import (
    add_label, remove_label, clear_labels, get_label, list_labels,
    list_label_formats, LABEL_FORMATS,
)
from cli_anything.pymol.core.render import (
    set_render_settings, get_render_settings, list_render_presets,
    render_scene, RENDER_PRESETS, VALID_FORMATS,
)
from cli_anything.pymol.core.session import Session


# ── Project Tests ────────────────────────────────────────────────

class TestProject:
    def test_create_default(self):
        proj = create_project()
        assert proj["render"]["width"] == 1920
        assert proj["render"]["height"] == 1080
        assert proj["render"]["ray_trace_mode"] == 1
        assert proj["version"] == "1.0"
        assert proj["name"] == "untitled"

    def test_create_with_name(self):
        proj = create_project(name="my_session")
        assert proj["name"] == "my_session"

    def test_create_with_dimensions(self):
        proj = create_project(width=800, height=600)
        assert proj["render"]["width"] == 800
        assert proj["render"]["height"] == 600

    def test_create_with_profile_publication(self):
        proj = create_project(profile="publication")
        assert proj["render"]["width"] == 3000
        assert proj["render"]["height"] == 3000

    def test_create_with_profile_web(self):
        proj = create_project(profile="web")
        assert proj["render"]["width"] == 800
        assert proj["render"]["height"] == 600

    def test_create_with_profile_thumbnail(self):
        proj = create_project(profile="thumbnail")
        assert proj["render"]["width"] == 400
        assert proj["render"]["height"] == 400

    def test_create_with_profile_poster(self):
        proj = create_project(profile="poster")
        assert proj["render"]["width"] == 4000
        assert proj["render"]["height"] == 3000

    def test_create_with_profile_dark(self):
        proj = create_project(profile="dark")
        assert proj["settings"]["bg_color"] == [0.0, 0.0, 0.0]

    def test_create_with_profile_presentation(self):
        proj = create_project(profile="presentation")
        assert proj["settings"]["bg_color"] == [1.0, 1.0, 1.0]

    def test_create_invalid_resolution_zero(self):
        with pytest.raises(ValueError, match="must be positive"):
            create_project(width=0, height=100)

    def test_create_invalid_resolution_negative(self):
        with pytest.raises(ValueError, match="must be positive"):
            create_project(width=100, height=-1)

    def test_create_invalid_bg_color_components(self):
        with pytest.raises(ValueError, match="3 components"):
            create_project(bg_color=[1.0, 0.0])

    def test_create_invalid_bg_color_range(self):
        with pytest.raises(ValueError, match="must be 0.0-1.0"):
            create_project(bg_color=[2.0, 0.0, 0.0])

    def test_create_invalid_ray_trace_mode(self):
        with pytest.raises(ValueError, match="Invalid ray_trace_mode"):
            create_project(ray_trace_mode=5)

    def test_create_invalid_antialias(self):
        with pytest.raises(ValueError, match="Invalid antialias"):
            create_project(antialias=3)

    def test_save_and_open(self):
        proj = create_project(name="test_project")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            path = f.name
        try:
            save_project(proj, path)
            loaded = open_project(path)
            assert loaded["name"] == "test_project"
            assert loaded["render"]["width"] == 1920
        finally:
            os.unlink(path)

    def test_open_nonexistent(self):
        with pytest.raises(FileNotFoundError):
            open_project("/nonexistent/path.json")

    def test_open_invalid_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            json.dump({"foo": "bar"}, f)
            path = f.name
        try:
            with pytest.raises(ValueError, match="Invalid project file"):
                open_project(path)
        finally:
            os.unlink(path)

    def test_get_info(self):
        proj = create_project(name="info_test")
        info = get_project_info(proj)
        assert info["name"] == "info_test"
        assert info["counts"]["structures"] == 0
        assert info["counts"]["selections"] == 0
        assert "render" in info

    def test_list_profiles(self):
        profiles = list_profiles()
        assert len(profiles) > 0
        names = [p["name"] for p in profiles]
        assert "default" in names
        assert "publication" in names
        assert "presentation" in names
        assert "poster" in names

    def test_project_has_empty_lists(self):
        proj = create_project()
        assert proj["structures"] == []
        assert proj["selections"] == []
        assert proj["representations"] == []
        assert proj["colors"] == []
        assert proj["labels"] == []

    def test_project_has_settings(self):
        proj = create_project()
        assert "bg_color" in proj["settings"]
        assert "depth_cue" in proj["settings"]
        assert "orthoscopic" in proj["settings"]

    def test_project_has_view(self):
        proj = create_project()
        assert "position" in proj["view"]
        assert "orientation" in proj["view"]
        assert "zoom" in proj["view"]

    def test_project_has_metadata(self):
        proj = create_project()
        assert "created" in proj["metadata"]
        assert "modified" in proj["metadata"]
        assert "software" in proj["metadata"]

    def test_all_profiles_exist(self):
        expected = {"default", "presentation", "publication", "poster", "web",
                    "thumbnail", "transparent", "dark"}
        assert set(PROFILES.keys()) == expected


# ── Structure Tests ──────────────────────────────────────────────

class TestStructure:
    def _make_project(self):
        return create_project()

    def _make_temp_pdb(self):
        """Create a temporary PDB file with dummy content."""
        f = tempfile.NamedTemporaryFile(suffix=".pdb", delete=False, mode="w")
        f.write("ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00  0.00\nEND\n")
        f.close()
        return f.name

    def test_load_structure_from_file(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            struct = load_structure(proj, path)
            assert struct["source"] == "pdb"
            assert struct["visible"] is True
            assert len(proj["structures"]) == 1
        finally:
            os.unlink(path)

    def test_load_structure_object_name_from_filename(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            struct = load_structure(proj, path)
            basename = os.path.splitext(os.path.basename(path))[0]
            assert struct["object_name"] == basename
        finally:
            os.unlink(path)

    def test_load_structure_custom_object_name(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            struct = load_structure(proj, path, object_name="my_protein")
            assert struct["object_name"] == "my_protein"
        finally:
            os.unlink(path)

    def test_load_structure_pdb_id(self):
        proj = self._make_project()
        struct = load_structure(proj, "1abc")
        assert struct["pdb_id"] == "1ABC"
        assert struct["source"] == "pdb"
        assert struct["path"] is None

    def test_load_structure_pdb_id_name(self):
        proj = self._make_project()
        struct = load_structure(proj, "4hhb")
        assert struct["name"] == "4hhb"
        assert struct["object_name"] == "4hhb"

    def test_load_structure_nonexistent_file(self):
        proj = self._make_project()
        with pytest.raises(FileNotFoundError, match="Structure file not found"):
            load_structure(proj, "/nonexistent/file.pdb")

    def test_load_structure_unsupported_format(self):
        proj = self._make_project()
        f = tempfile.NamedTemporaryFile(suffix=".unsupported", delete=False, mode="w")
        f.write("dummy")
        f.close()
        try:
            with pytest.raises(ValueError, match="Unsupported file format"):
                load_structure(proj, f.name)
        finally:
            os.unlink(f.name)

    def test_load_structure_invalid_state(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            with pytest.raises(ValueError, match="State must be >= 1"):
                load_structure(proj, path, state=0)
        finally:
            os.unlink(path)

    def test_load_structure_format_override(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            struct = load_structure(proj, path, source_format="cif")
            assert struct["source"] == "cif"
        finally:
            os.unlink(path)

    def test_load_structure_unique_names(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            s1 = load_structure(proj, path, object_name="protein")
            s2 = load_structure(proj, path, object_name="protein")
            assert s1["object_name"] != s2["object_name"]
            assert s2["object_name"] == "protein.001"
        finally:
            os.unlink(path)

    def test_load_structure_unique_ids(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            s1 = load_structure(proj, path)
            s2 = load_structure(proj, path)
            assert s1["id"] != s2["id"]
        finally:
            os.unlink(path)

    def test_remove_structure(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            load_structure(proj, path, object_name="A")
            load_structure(proj, path, object_name="B")
            removed = remove_structure(proj, 0)
            assert removed["object_name"] == "A"
            assert len(proj["structures"]) == 1
        finally:
            os.unlink(path)

    def test_remove_structure_empty(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="No structures"):
            remove_structure(proj, 0)

    def test_remove_structure_invalid_index(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            load_structure(proj, path)
            with pytest.raises(IndexError):
                remove_structure(proj, 5)
        finally:
            os.unlink(path)

    def test_rename_structure(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            load_structure(proj, path, object_name="old_name")
            result = rename_structure(proj, 0, "new_name")
            assert result["object_name"] == "new_name"
        finally:
            os.unlink(path)

    def test_rename_structure_updates_representations(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            struct = load_structure(proj, path, object_name="protein")
            show_representation(proj, "protein", "cartoon")
            rename_structure(proj, 0, "renamed")
            assert proj["representations"][0]["target"] == "renamed"
        finally:
            os.unlink(path)

    def test_get_structure(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            load_structure(proj, path, object_name="test")
            struct = get_structure(proj, 0)
            assert struct["object_name"] == "test"
        finally:
            os.unlink(path)

    def test_get_structure_invalid_index(self):
        proj = self._make_project()
        with pytest.raises(IndexError):
            get_structure(proj, 0)

    def test_list_structures(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            load_structure(proj, path, object_name="A")
            load_structure(proj, path, object_name="B")
            result = list_structures(proj)
            assert len(result) == 2
        finally:
            os.unlink(path)

    def test_set_structure_property_visible(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            load_structure(proj, path)
            set_structure_property(proj, 0, "visible", False)
            assert proj["structures"][0]["visible"] is False
        finally:
            os.unlink(path)

    def test_set_structure_property_visible_string(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            load_structure(proj, path)
            set_structure_property(proj, 0, "visible", "false")
            assert proj["structures"][0]["visible"] is False
        finally:
            os.unlink(path)

    def test_set_structure_property_atom_count(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            load_structure(proj, path)
            set_structure_property(proj, 0, "atom_count", 500)
            assert proj["structures"][0]["atom_count"] == 500
        finally:
            os.unlink(path)

    def test_set_structure_property_invalid(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            load_structure(proj, path)
            with pytest.raises(ValueError, match="Unknown property"):
                set_structure_property(proj, 0, "bogus", "value")
        finally:
            os.unlink(path)

    def test_list_formats(self):
        formats = list_formats()
        assert len(formats) >= 8
        names = [f["format"] for f in formats]
        assert "pdb" in names
        assert "cif" in names
        assert "sdf" in names

    def test_supported_formats_dict(self):
        assert "pdb" in SUPPORTED_FORMATS
        assert "cif" in SUPPORTED_FORMATS
        assert ".pdb" in SUPPORTED_FORMATS["pdb"]["extensions"]

    def test_load_sdf_file(self):
        proj = self._make_project()
        f = tempfile.NamedTemporaryFile(suffix=".sdf", delete=False, mode="w")
        f.write("dummy sdf content\n$$$$\n")
        f.close()
        try:
            struct = load_structure(proj, f.name)
            assert struct["source"] == "sdf"
        finally:
            os.unlink(f.name)

    def test_load_cif_file(self):
        proj = self._make_project()
        f = tempfile.NamedTemporaryFile(suffix=".cif", delete=False, mode="w")
        f.write("data_dummy\n")
        f.close()
        try:
            struct = load_structure(proj, f.name)
            assert struct["source"] == "cif"
        finally:
            os.unlink(f.name)

    def test_remove_structure_cleans_representations(self):
        proj = self._make_project()
        path = self._make_temp_pdb()
        try:
            load_structure(proj, path, object_name="protein")
            show_representation(proj, "protein", "cartoon")
            assert len(proj["representations"]) == 1
            remove_structure(proj, 0)
            assert len(proj["representations"]) == 0
        finally:
            os.unlink(path)


# ── Selection Tests ──────────────────────────────────────────────

class TestSelection:
    def _make_project(self):
        return create_project()

    def test_create_selection(self):
        proj = self._make_project()
        sel = create_selection(proj, "active_site", "resi 100-200")
        assert sel["name"] == "active_site"
        assert sel["expression"] == "resi 100-200"
        assert sel["enabled"] is True
        assert len(proj["selections"]) == 1

    def test_create_selection_disabled(self):
        proj = self._make_project()
        sel = create_selection(proj, "inactive", "chain A", enabled=False)
        assert sel["enabled"] is False

    def test_create_selection_empty_name(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="cannot be empty"):
            create_selection(proj, "", "chain A")

    def test_create_selection_empty_expression(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="cannot be empty"):
            create_selection(proj, "test", "")

    def test_create_selection_unique_names(self):
        proj = self._make_project()
        s1 = create_selection(proj, "sel", "chain A")
        s2 = create_selection(proj, "sel", "chain B")
        assert s1["name"] != s2["name"]
        assert s2["name"] == "sel_1"

    def test_create_selection_unique_ids(self):
        proj = self._make_project()
        s1 = create_selection(proj, "a", "chain A")
        s2 = create_selection(proj, "b", "chain B")
        assert s1["id"] != s2["id"]

    def test_remove_selection(self):
        proj = self._make_project()
        create_selection(proj, "A", "chain A")
        create_selection(proj, "B", "chain B")
        removed = remove_selection(proj, 0)
        assert removed["name"] == "A"
        assert len(proj["selections"]) == 1

    def test_remove_selection_empty(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="No selections"):
            remove_selection(proj, 0)

    def test_remove_selection_invalid_index(self):
        proj = self._make_project()
        create_selection(proj, "test", "chain A")
        with pytest.raises(IndexError):
            remove_selection(proj, 5)

    def test_update_selection_expression(self):
        proj = self._make_project()
        create_selection(proj, "test", "chain A")
        updated = update_selection(proj, 0, expression="chain B")
        assert updated["expression"] == "chain B"

    def test_update_selection_enabled(self):
        proj = self._make_project()
        create_selection(proj, "test", "chain A")
        updated = update_selection(proj, 0, enabled=False)
        assert updated["enabled"] is False

    def test_update_selection_empty_expression(self):
        proj = self._make_project()
        create_selection(proj, "test", "chain A")
        with pytest.raises(ValueError, match="cannot be empty"):
            update_selection(proj, 0, expression="")

    def test_update_selection_invalid_index(self):
        proj = self._make_project()
        with pytest.raises(IndexError):
            update_selection(proj, 0, expression="chain A")

    def test_get_selection(self):
        proj = self._make_project()
        create_selection(proj, "test", "chain A")
        sel = get_selection(proj, 0)
        assert sel["name"] == "test"

    def test_get_selection_invalid_index(self):
        proj = self._make_project()
        with pytest.raises(IndexError):
            get_selection(proj, 0)

    def test_list_selections(self):
        proj = self._make_project()
        create_selection(proj, "A", "chain A")
        create_selection(proj, "B", "chain B")
        result = list_selections(proj)
        assert len(result) == 2

    def test_list_macros(self):
        macros = list_macros()
        assert len(macros) > 0
        names = [m["name"] for m in macros]
        assert "protein" in names
        assert "water" in names
        assert "backbone" in names

    def test_selection_macros_dict(self):
        assert "protein" in SELECTION_MACROS
        assert "nucleic" in SELECTION_MACROS
        assert "ligand" in SELECTION_MACROS
        assert "ions" in SELECTION_MACROS

    def test_create_multiple_unique_names(self):
        proj = self._make_project()
        s1 = create_selection(proj, "sel", "chain A")
        s2 = create_selection(proj, "sel", "chain B")
        s3 = create_selection(proj, "sel", "chain C")
        names = {s1["name"], s2["name"], s3["name"]}
        assert len(names) == 3


# ── Representation Tests ─────────────────────────────────────────

class TestRepresentation:
    def _make_project(self):
        return create_project()

    def test_show_cartoon(self):
        proj = self._make_project()
        rep = show_representation(proj, "protein", "cartoon")
        assert rep["rep_type"] == "cartoon"
        assert rep["target"] == "protein"
        assert rep["enabled"] is True
        assert len(proj["representations"]) == 1

    def test_show_sticks(self):
        proj = self._make_project()
        rep = show_representation(proj, "ligand", "sticks")
        assert rep["rep_type"] == "sticks"

    def test_show_with_settings(self):
        proj = self._make_project()
        rep = show_representation(proj, "protein", "cartoon",
                                  settings={"cartoon_transparency": 0.5})
        assert rep["settings"]["cartoon_transparency"] == 0.5

    def test_show_invalid_representation(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Unknown representation"):
            show_representation(proj, "protein", "nonexistent")

    def test_show_invalid_setting(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Unknown setting"):
            show_representation(proj, "protein", "cartoon",
                                settings={"bogus": 1.0})

    def test_show_setting_out_of_range(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="out of range"):
            show_representation(proj, "protein", "cartoon",
                                settings={"cartoon_transparency": 2.0})

    def test_show_existing_re_enables(self):
        proj = self._make_project()
        rep1 = show_representation(proj, "protein", "cartoon")
        hide_representation(proj, "protein", "cartoon")
        rep2 = show_representation(proj, "protein", "cartoon")
        assert rep2["enabled"] is True
        assert len(proj["representations"]) == 1

    def test_hide_representation(self):
        proj = self._make_project()
        show_representation(proj, "protein", "cartoon")
        hidden = hide_representation(proj, "protein", "cartoon")
        assert len(hidden) == 1
        assert hidden[0]["enabled"] is False

    def test_hide_all_representations(self):
        proj = self._make_project()
        show_representation(proj, "protein", "cartoon")
        show_representation(proj, "protein", "sticks")
        hidden = hide_representation(proj, "protein")
        assert len(hidden) == 2

    def test_hide_representation_not_found(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="No .*representation found"):
            hide_representation(proj, "nonexistent")

    def test_remove_representation(self):
        proj = self._make_project()
        show_representation(proj, "protein", "cartoon")
        removed = remove_representation(proj, 0)
        assert removed["rep_type"] == "cartoon"
        assert len(proj["representations"]) == 0

    def test_remove_representation_empty(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="No representations"):
            remove_representation(proj, 0)

    def test_remove_representation_invalid_index(self):
        proj = self._make_project()
        show_representation(proj, "protein", "cartoon")
        with pytest.raises(IndexError):
            remove_representation(proj, 5)

    def test_set_representation_setting(self):
        proj = self._make_project()
        show_representation(proj, "protein", "sticks")
        rep = set_representation_setting(proj, 0, "stick_radius", 0.5)
        assert rep["settings"]["stick_radius"] == 0.5

    def test_set_representation_setting_invalid(self):
        proj = self._make_project()
        show_representation(proj, "protein", "sticks")
        with pytest.raises(ValueError, match="Unknown setting"):
            set_representation_setting(proj, 0, "bogus", 1.0)

    def test_set_representation_setting_out_of_range(self):
        proj = self._make_project()
        show_representation(proj, "protein", "sticks")
        with pytest.raises(ValueError, match="out of range"):
            set_representation_setting(proj, 0, "stick_radius", 99.0)

    def test_get_representation(self):
        proj = self._make_project()
        show_representation(proj, "protein", "cartoon")
        rep = get_representation(proj, 0)
        assert rep["rep_type"] == "cartoon"

    def test_get_representation_invalid_index(self):
        proj = self._make_project()
        with pytest.raises(IndexError):
            get_representation(proj, 0)

    def test_list_representations(self):
        proj = self._make_project()
        show_representation(proj, "protein", "cartoon")
        show_representation(proj, "ligand", "sticks")
        result = list_representations(proj)
        assert len(result) == 2

    def test_list_representations_filtered(self):
        proj = self._make_project()
        show_representation(proj, "protein", "cartoon")
        show_representation(proj, "ligand", "sticks")
        result = list_representations(proj, target="protein")
        assert len(result) == 1
        assert result[0]["target"] == "protein"

    def test_list_available(self):
        reps = list_available()
        assert len(reps) >= 9
        names = [r["name"] for r in reps]
        assert "cartoon" in names
        assert "sticks" in names
        assert "surface" in names

    def test_list_available_by_category(self):
        backbone = list_available(category="backbone")
        assert all(r["category"] == "backbone" for r in backbone)
        assert len(backbone) >= 2

    def test_list_available_by_category_atomic(self):
        atomic = list_available(category="atomic")
        assert all(r["category"] == "atomic" for r in atomic)

    def test_get_representation_info(self):
        info = get_representation_info("cartoon")
        assert info["name"] == "cartoon"
        assert "cartoon_oval_length" in info["settings"]
        assert info["category"] == "backbone"

    def test_get_representation_info_unknown(self):
        with pytest.raises(ValueError, match="Unknown representation"):
            get_representation_info("nonexistent")

    def test_all_registry_entries_have_category(self):
        for name, spec in REPRESENTATION_REGISTRY.items():
            assert "category" in spec, f"Representation '{name}' missing category"
            assert "description" in spec, f"Representation '{name}' missing description"
            assert "settings" in spec, f"Representation '{name}' missing settings"

    def test_show_all_representation_types(self):
        proj = self._make_project()
        for rep_type in REPRESENTATION_REGISTRY:
            rep = show_representation(proj, "protein", rep_type)
            assert rep["rep_type"] == rep_type
        assert len(proj["representations"]) == len(REPRESENTATION_REGISTRY)

    def test_surface_settings(self):
        proj = self._make_project()
        rep = show_representation(proj, "protein", "surface",
                                  settings={"transparency": 0.5, "surface_quality": 2})
        assert rep["settings"]["transparency"] == 0.5
        assert rep["settings"]["surface_quality"] == 2


# ── Coloring Tests ───────────────────────────────────────────────

class TestColoring:
    def _make_project(self):
        return create_project()

    def test_apply_named_color(self):
        proj = self._make_project()
        entry = apply_color(proj, "protein", color="red")
        assert entry["color_type"] == "named"
        assert entry["color_name"] == "red"
        assert entry["color_rgb"] == [1.0, 0.0, 0.0]
        assert len(proj["colors"]) == 1

    def test_apply_rgb_color(self):
        proj = self._make_project()
        entry = apply_color(proj, "protein", rgb=[0.5, 0.3, 0.8])
        assert entry["color_type"] == "rgb"
        assert entry["color_rgb"] == [0.5, 0.3, 0.8]

    def test_apply_color_scheme(self):
        proj = self._make_project()
        entry = apply_color(proj, "protein", scheme="by_element")
        assert entry["color_type"] == "scheme"
        assert entry["scheme"] == "by_element"
        assert entry["color_rgb"] is None

    def test_apply_color_no_args(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Exactly one"):
            apply_color(proj, "protein")

    def test_apply_color_multiple_args(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Exactly one"):
            apply_color(proj, "protein", color="red", rgb=[1.0, 0.0, 0.0])

    def test_apply_color_unknown_name(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Unknown color"):
            apply_color(proj, "protein", color="nonexistent")

    def test_apply_color_invalid_rgb_length(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="3 components"):
            apply_color(proj, "protein", rgb=[1.0, 0.0])

    def test_apply_color_invalid_rgb_range(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="must be 0.0-1.0"):
            apply_color(proj, "protein", rgb=[2.0, 0.0, 0.0])

    def test_apply_color_unknown_scheme(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Unknown scheme"):
            apply_color(proj, "protein", scheme="nonexistent")

    def test_remove_color(self):
        proj = self._make_project()
        apply_color(proj, "protein", color="red")
        removed = remove_color(proj, 0)
        assert removed["color_name"] == "red"
        assert len(proj["colors"]) == 0

    def test_remove_color_empty(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="No color entries"):
            remove_color(proj, 0)

    def test_remove_color_invalid_index(self):
        proj = self._make_project()
        apply_color(proj, "protein", color="red")
        with pytest.raises(IndexError):
            remove_color(proj, 5)

    def test_list_colors(self):
        proj = self._make_project()
        apply_color(proj, "protein", color="red")
        apply_color(proj, "ligand", color="blue")
        result = list_colors(proj)
        assert len(result) == 2

    def test_get_color(self):
        proj = self._make_project()
        apply_color(proj, "protein", color="green")
        entry = get_color(proj, 0)
        assert entry["color_name"] == "green"

    def test_get_color_invalid_index(self):
        proj = self._make_project()
        with pytest.raises(IndexError):
            get_color(proj, 0)

    def test_list_named_colors(self):
        colors = list_named_colors()
        assert len(colors) > 0
        names = [c["name"] for c in colors]
        assert "red" in names
        assert "blue" in names
        assert "green" in names

    def test_list_schemes(self):
        schemes = list_schemes()
        assert len(schemes) > 0
        names = [s["name"] for s in schemes]
        assert "by_element" in names
        assert "by_chain" in names
        assert "rainbow" in names

    def test_named_colors_dict(self):
        assert "red" in NAMED_COLORS
        assert NAMED_COLORS["red"] == [1.0, 0.0, 0.0]
        assert NAMED_COLORS["green"] == [0.0, 1.0, 0.0]
        assert NAMED_COLORS["blue"] == [0.0, 0.0, 1.0]

    def test_color_schemes_dict(self):
        assert "by_element" in COLOR_SCHEMES
        assert "by_chain" in COLOR_SCHEMES
        assert "description" in COLOR_SCHEMES["by_element"]
        assert "pymol_command" in COLOR_SCHEMES["by_element"]

    def test_apply_all_named_colors(self):
        proj = self._make_project()
        for name in NAMED_COLORS:
            entry = apply_color(proj, "protein", color=name)
            assert entry["color_name"] == name

    def test_apply_all_schemes(self):
        proj = self._make_project()
        for scheme_name in COLOR_SCHEMES:
            entry = apply_color(proj, "protein", scheme=scheme_name)
            assert entry["scheme"] == scheme_name


# ── View Tests ───────────────────────────────────────────────────

class TestView:
    def _make_project(self):
        return create_project()

    def test_set_view_preset_front(self):
        proj = self._make_project()
        result = set_view(proj, preset="front")
        assert proj["view"]["orientation"] == [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

    def test_set_view_preset_back(self):
        proj = self._make_project()
        result = set_view(proj, preset="back")
        assert proj["view"]["orientation"] == [[-1, 0, 0], [0, 1, 0], [0, 0, -1]]

    def test_set_view_invalid_preset(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Unknown view preset"):
            set_view(proj, preset="nonexistent")

    def test_set_view_position(self):
        proj = self._make_project()
        set_view(proj, position=[10.0, 20.0, 30.0])
        assert proj["view"]["position"] == [10.0, 20.0, 30.0]

    def test_set_view_invalid_position(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="3 components"):
            set_view(proj, position=[1.0, 2.0])

    def test_set_view_orientation(self):
        proj = self._make_project()
        ori = [[0, 1, 0], [1, 0, 0], [0, 0, -1]]
        set_view(proj, orientation=ori)
        assert proj["view"]["orientation"] == [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, -1.0]]

    def test_set_view_invalid_orientation(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="3x3 matrix"):
            set_view(proj, orientation=[[1, 0], [0, 1]])

    def test_set_view_zoom(self):
        proj = self._make_project()
        set_view(proj, zoom=2.5)
        assert proj["view"]["zoom"] == 2.5

    def test_set_view_zoom_invalid(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Zoom must be positive"):
            set_view(proj, zoom=0)

    def test_set_view_zoom_negative(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Zoom must be positive"):
            set_view(proj, zoom=-1.0)

    def test_set_view_clip(self):
        proj = self._make_project()
        set_view(proj, clip_near=5.0, clip_far=-100.0)
        assert proj["view"]["clip_near"] == 5.0
        assert proj["view"]["clip_far"] == -100.0

    def test_set_view_field_of_view(self):
        proj = self._make_project()
        set_view(proj, field_of_view=45.0)
        assert proj["view"]["field_of_view"] == 45.0

    def test_set_view_field_of_view_invalid_low(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Field of view must be 1.0-179.0"):
            set_view(proj, field_of_view=0.5)

    def test_set_view_field_of_view_invalid_high(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Field of view must be 1.0-179.0"):
            set_view(proj, field_of_view=180.0)

    def test_get_view(self):
        proj = self._make_project()
        view = get_view(proj)
        assert "position" in view
        assert "orientation" in view
        assert "zoom" in view

    def test_set_setting_bg_color(self):
        proj = self._make_project()
        result = set_setting(proj, "bg_color", [1.0, 1.0, 1.0])
        assert proj["settings"]["bg_color"] == [1.0, 1.0, 1.0]
        assert result["new_value"] == [1.0, 1.0, 1.0]

    def test_set_setting_depth_cue(self):
        proj = self._make_project()
        result = set_setting(proj, "depth_cue", False)
        assert proj["settings"]["depth_cue"] is False

    def test_set_setting_bool_string(self):
        proj = self._make_project()
        set_setting(proj, "fog", "true")
        assert proj["settings"]["fog"] is True

    def test_set_setting_antialias(self):
        proj = self._make_project()
        set_setting(proj, "antialias", 1)
        assert proj["settings"]["antialias"] == 1

    def test_set_setting_antialias_out_of_range(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="must be"):
            set_setting(proj, "antialias", 5)

    def test_set_setting_ray_trace_mode(self):
        proj = self._make_project()
        set_setting(proj, "ray_trace_mode", 2)
        assert proj["settings"]["ray_trace_mode"] == 2

    def test_set_setting_ray_trace_mode_out_of_range(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="must be"):
            set_setting(proj, "ray_trace_mode", 5)

    def test_set_setting_field_of_view(self):
        proj = self._make_project()
        set_setting(proj, "field_of_view", 45.0)
        assert proj["settings"]["field_of_view"] == 45.0

    def test_set_setting_field_of_view_out_of_range(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="must be"):
            set_setting(proj, "field_of_view", 200.0)

    def test_set_setting_invalid_name(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Unknown setting"):
            set_setting(proj, "bogus", "value")

    def test_set_setting_bg_color_invalid(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="list of 3"):
            set_setting(proj, "bg_color", [1.0, 1.0])

    def test_set_setting_bg_color_range(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="must be 0.0-1.0"):
            set_setting(proj, "bg_color", [2.0, 0.0, 0.0])

    def test_list_settings(self):
        proj = self._make_project()
        settings = list_settings(proj)
        assert "bg_color" in settings
        assert "depth_cue" in settings

    def test_list_view_presets(self):
        presets = list_view_presets()
        assert len(presets) >= 6
        names = [p["name"] for p in presets]
        assert "front" in names
        assert "back" in names
        assert "top" in names
        assert "bottom" in names
        assert "left" in names
        assert "right" in names

    def test_view_presets_dict(self):
        expected = {"front", "back", "top", "bottom", "left", "right"}
        assert set(VIEW_PRESETS.keys()) == expected

    def test_set_view_returns_old_and_new(self):
        proj = self._make_project()
        result = set_view(proj, zoom=3.0)
        assert "old_view" in result
        assert "new_view" in result
        assert result["new_view"]["zoom"] == 3.0


# ── Label Tests ──────────────────────────────────────────────────

class TestLabel:
    def _make_project(self):
        return create_project()

    def test_add_label_default(self):
        proj = self._make_project()
        label = add_label(proj, "chain A and name CA")
        assert label["target"] == "chain A and name CA"
        assert label["format_preset"] == "residue"
        assert label["color"] == [1.0, 1.0, 1.0]
        assert label["size"] == 14
        assert len(proj["labels"]) == 1

    def test_add_label_with_preset(self):
        proj = self._make_project()
        label = add_label(proj, "all", format_preset="atom")
        assert label["format_preset"] == "atom"
        assert label["expression"] == "name"

    def test_add_label_with_custom_expression(self):
        proj = self._make_project()
        label = add_label(proj, "all", expression="custom_expr")
        assert label["expression"] == "custom_expr"

    def test_add_label_with_color(self):
        proj = self._make_project()
        label = add_label(proj, "all", color=[1.0, 0.0, 0.0])
        assert label["color"] == [1.0, 0.0, 0.0]

    def test_add_label_with_size(self):
        proj = self._make_project()
        label = add_label(proj, "all", size=24)
        assert label["size"] == 24

    def test_add_label_invalid_preset(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Unknown label format"):
            add_label(proj, "all", format_preset="nonexistent")

    def test_add_label_invalid_color_length(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="3 components"):
            add_label(proj, "all", color=[1.0, 0.0])

    def test_add_label_invalid_color_range(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="must be 0.0-1.0"):
            add_label(proj, "all", color=[2.0, 0.0, 0.0])

    def test_add_label_invalid_size_low(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="must be 8-72"):
            add_label(proj, "all", size=4)

    def test_add_label_invalid_size_high(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="must be 8-72"):
            add_label(proj, "all", size=100)

    def test_remove_label(self):
        proj = self._make_project()
        add_label(proj, "chain A")
        removed = remove_label(proj, 0)
        assert removed["target"] == "chain A"
        assert len(proj["labels"]) == 0

    def test_remove_label_empty(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="No labels"):
            remove_label(proj, 0)

    def test_remove_label_invalid_index(self):
        proj = self._make_project()
        add_label(proj, "all")
        with pytest.raises(IndexError):
            remove_label(proj, 5)

    def test_clear_labels_all(self):
        proj = self._make_project()
        add_label(proj, "chain A")
        add_label(proj, "chain B")
        count = clear_labels(proj)
        assert count == 2
        assert len(proj["labels"]) == 0

    def test_clear_labels_by_target(self):
        proj = self._make_project()
        add_label(proj, "chain A")
        add_label(proj, "chain B")
        add_label(proj, "chain A")
        count = clear_labels(proj, target="chain A")
        assert count == 2
        assert len(proj["labels"]) == 1

    def test_get_label(self):
        proj = self._make_project()
        add_label(proj, "all")
        label = get_label(proj, 0)
        assert label["target"] == "all"

    def test_get_label_invalid_index(self):
        proj = self._make_project()
        with pytest.raises(IndexError):
            get_label(proj, 0)

    def test_list_labels(self):
        proj = self._make_project()
        add_label(proj, "chain A")
        add_label(proj, "chain B")
        result = list_labels(proj)
        assert len(result) == 2

    def test_list_label_formats(self):
        formats = list_label_formats()
        assert len(formats) >= 8
        names = [f["name"] for f in formats]
        assert "residue" in names
        assert "atom" in names
        assert "bfactor" in names
        assert "one_letter" in names

    def test_label_formats_dict(self):
        expected = {"residue", "atom", "chain_residue", "element", "bfactor",
                    "charge", "residue_number", "one_letter"}
        assert set(LABEL_FORMATS.keys()) == expected

    def test_add_label_all_presets(self):
        proj = self._make_project()
        for preset_name in LABEL_FORMATS:
            label = add_label(proj, "all", format_preset=preset_name)
            assert label["format_preset"] == preset_name
            assert label["expression"] == LABEL_FORMATS[preset_name]["expression"]


# ── Render Tests ─────────────────────────────────────────────────

class TestRender:
    def _make_project(self):
        return create_project()

    def test_set_render_settings_resolution(self):
        proj = self._make_project()
        set_render_settings(proj, width=3840, height=2160)
        assert proj["render"]["width"] == 3840
        assert proj["render"]["height"] == 2160

    def test_set_render_settings_ray(self):
        proj = self._make_project()
        set_render_settings(proj, ray=False)
        assert proj["render"]["ray"] is False

    def test_set_render_settings_ray_trace_mode(self):
        proj = self._make_project()
        set_render_settings(proj, ray_trace_mode=2)
        assert proj["render"]["ray_trace_mode"] == 2

    def test_set_render_settings_antialias(self):
        proj = self._make_project()
        set_render_settings(proj, antialias=0)
        assert proj["render"]["antialias"] == 0

    def test_set_render_settings_format(self):
        proj = self._make_project()
        set_render_settings(proj, output_format="jpg")
        assert proj["render"]["output_format"] == "jpg"

    def test_set_render_settings_dpi(self):
        proj = self._make_project()
        set_render_settings(proj, dpi=600)
        assert proj["render"]["dpi"] == 600

    def test_set_render_settings_transparent(self):
        proj = self._make_project()
        set_render_settings(proj, transparent_background=True)
        assert proj["render"]["transparent_background"] is True

    def test_set_render_settings_invalid_width(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="must be positive"):
            set_render_settings(proj, width=0)

    def test_set_render_settings_invalid_height(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="must be positive"):
            set_render_settings(proj, height=-1)

    def test_set_render_settings_invalid_ray_trace_mode(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Invalid ray_trace_mode"):
            set_render_settings(proj, ray_trace_mode=5)

    def test_set_render_settings_invalid_antialias(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Invalid antialias"):
            set_render_settings(proj, antialias=5)

    def test_set_render_settings_invalid_format(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Invalid format"):
            set_render_settings(proj, output_format="INVALID")

    def test_set_render_settings_invalid_dpi(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="DPI must be positive"):
            set_render_settings(proj, dpi=0)

    def test_set_render_settings_preset_standard(self):
        proj = self._make_project()
        set_render_settings(proj, preset="standard")
        assert proj["render"]["width"] == 1920
        assert proj["render"]["height"] == 1080
        assert proj["render"]["ray"] is True

    def test_set_render_settings_preset_high_quality(self):
        proj = self._make_project()
        set_render_settings(proj, preset="high_quality")
        assert proj["render"]["width"] == 3000
        assert proj["render"]["height"] == 3000

    def test_set_render_settings_preset_quick_preview(self):
        proj = self._make_project()
        set_render_settings(proj, preset="quick_preview")
        assert proj["render"]["ray"] is False

    def test_set_render_settings_preset_transparent(self):
        proj = self._make_project()
        set_render_settings(proj, preset="transparent")
        assert proj["render"]["transparent_background"] is True

    def test_set_render_settings_invalid_preset(self):
        proj = self._make_project()
        with pytest.raises(ValueError, match="Unknown render preset"):
            set_render_settings(proj, preset="nonexistent")

    def test_get_render_settings(self):
        proj = self._make_project()
        info = get_render_settings(proj)
        assert "resolution" in info
        assert info["width"] == 1920
        assert info["height"] == 1080
        assert info["ray"] is True
        assert info["output_format"] == "png"

    def test_list_render_presets(self):
        presets = list_render_presets()
        assert len(presets) >= 7
        names = [p["name"] for p in presets]
        assert "quick_preview" in names
        assert "standard" in names
        assert "high_quality" in names
        assert "poster" in names

    def test_render_presets_dict(self):
        expected = {"quick_preview", "standard", "high_quality", "poster",
                    "web_thumbnail", "presentation", "transparent"}
        assert set(RENDER_PRESETS.keys()) == expected

    def test_valid_formats(self):
        assert "png" in VALID_FORMATS
        assert "jpg" in VALID_FORMATS
        assert "jpeg" in VALID_FORMATS
        assert "bmp" in VALID_FORMATS
        assert "tiff" in VALID_FORMATS
        assert "ppm" in VALID_FORMATS

    def test_render_scene_overwrite_protection(self):
        proj = self._make_project()
        with tempfile.TemporaryDirectory() as tmp:
            output_path = os.path.join(tmp, "render.png")
            with open(output_path, "w") as f:
                f.write("existing")
            with pytest.raises(FileExistsError):
                render_scene(proj, output_path, overwrite=False)

    def test_set_render_settings_output_path(self):
        proj = self._make_project()
        set_render_settings(proj, output_path="/tmp/renders/")
        assert proj["render"]["output_path"] == "/tmp/renders/"

    def test_set_render_settings_ray_shadows(self):
        proj = self._make_project()
        set_render_settings(proj, ray_shadows=False)
        assert proj["render"]["ray_shadows"] is False

    def test_set_render_all_valid_formats(self):
        proj = self._make_project()
        for fmt in VALID_FORMATS:
            set_render_settings(proj, output_format=fmt)
            assert proj["render"]["output_format"] == fmt


# ── Session Tests ────────────────────────────────────────────────

class TestSession:
    def test_create_session(self):
        sess = Session()
        assert not sess.has_project()

    def test_set_project(self):
        sess = Session()
        proj = create_project()
        sess.set_project(proj)
        assert sess.has_project()

    def test_get_project_no_project(self):
        sess = Session()
        with pytest.raises(RuntimeError, match="No session loaded"):
            sess.get_project()

    def test_get_project(self):
        sess = Session()
        proj = create_project(name="test")
        sess.set_project(proj)
        assert sess.get_project()["name"] == "test"

    def test_undo_redo(self):
        sess = Session()
        proj = create_project(name="original")
        sess.set_project(proj)

        sess.snapshot("change name")
        proj["name"] = "modified"

        assert proj["name"] == "modified"
        sess.undo()
        assert sess.get_project()["name"] == "original"
        sess.redo()
        assert sess.get_project()["name"] == "modified"

    def test_undo_empty(self):
        sess = Session()
        sess.set_project(create_project())
        with pytest.raises(RuntimeError, match="Nothing to undo"):
            sess.undo()

    def test_redo_empty(self):
        sess = Session()
        sess.set_project(create_project())
        with pytest.raises(RuntimeError, match="Nothing to redo"):
            sess.redo()

    def test_snapshot_clears_redo(self):
        sess = Session()
        proj = create_project(name="v1")
        sess.set_project(proj)

        sess.snapshot("v2")
        proj["name"] = "v2"

        sess.undo()
        assert sess.get_project()["name"] == "v1"

        # New snapshot should clear redo stack
        sess.snapshot("v3")
        sess.get_project()["name"] = "v3"

        with pytest.raises(RuntimeError, match="Nothing to redo"):
            sess.redo()

    def test_status(self):
        sess = Session()
        proj = create_project(name="test")
        sess.set_project(proj, "/tmp/test.json")
        status = sess.status()
        assert status["has_project"] is True
        assert status["project_path"] == "/tmp/test.json"
        assert status["undo_count"] == 0

    def test_status_no_project(self):
        sess = Session()
        status = sess.status()
        assert status["has_project"] is False
        assert status["session_name"] is None

    def test_save_session(self):
        sess = Session()
        proj = create_project(name="save_test")
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            sess.set_project(proj, path)
            saved = sess.save_session()
            assert os.path.exists(saved)
            with open(saved) as f:
                loaded = json.load(f)
            assert loaded["name"] == "save_test"
        finally:
            os.unlink(path)

    def test_save_session_no_project(self):
        sess = Session()
        with pytest.raises(RuntimeError, match="No session to save"):
            sess.save_session()

    def test_save_session_no_path(self):
        sess = Session()
        sess.set_project(create_project())
        with pytest.raises(ValueError, match="No save path"):
            sess.save_session()

    def test_save_session_custom_path(self):
        sess = Session()
        proj = create_project(name="custom_path_test")
        sess.set_project(proj)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            saved = sess.save_session(path)
            assert os.path.exists(saved)
            assert sess.project_path == path
        finally:
            os.unlink(path)

    def test_list_history(self):
        sess = Session()
        proj = create_project()
        sess.set_project(proj)
        sess.snapshot("action 1")
        sess.snapshot("action 2")
        history = sess.list_history()
        assert len(history) == 2
        assert history[0]["description"] == "action 2"

    def test_list_history_empty(self):
        sess = Session()
        proj = create_project()
        sess.set_project(proj)
        history = sess.list_history()
        assert len(history) == 0

    def test_max_undo(self):
        sess = Session()
        sess.MAX_UNDO = 5
        proj = create_project()
        sess.set_project(proj)
        for i in range(10):
            sess.snapshot(f"action {i}")
        assert len(sess._undo_stack) == 5

    def test_undo_structure_add(self):
        sess = Session()
        proj = create_project()
        sess.set_project(proj)

        sess.snapshot("add selection")
        create_selection(proj, "test", "chain A")
        assert len(proj["selections"]) == 1

        sess.undo()
        assert len(sess.get_project()["selections"]) == 0

    def test_undo_representation_add(self):
        sess = Session()
        proj = create_project()
        sess.set_project(proj)

        sess.snapshot("add representation")
        show_representation(proj, "protein", "cartoon")
        assert len(proj["representations"]) == 1

        sess.undo()
        assert len(sess.get_project()["representations"]) == 0

    def test_set_project_clears_history(self):
        sess = Session()
        proj1 = create_project(name="first")
        sess.set_project(proj1)
        sess.snapshot("action")

        proj2 = create_project(name="second")
        sess.set_project(proj2)
        assert len(sess._undo_stack) == 0
        assert len(sess._redo_stack) == 0

    def test_modified_flag(self):
        sess = Session()
        proj = create_project()
        sess.set_project(proj)
        assert sess._modified is False

        sess.snapshot("change")
        assert sess._modified is True

    def test_save_clears_modified(self):
        sess = Session()
        proj = create_project()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            sess.set_project(proj, path)
            sess.snapshot("change")
            assert sess._modified is True
            sess.save_session()
            assert sess._modified is False
        finally:
            os.unlink(path)

    def test_multiple_undo(self):
        sess = Session()
        proj = create_project(name="v1")
        sess.set_project(proj)

        sess.snapshot("to v2")
        proj["name"] = "v2"

        sess.snapshot("to v3")
        proj["name"] = "v3"

        sess.undo()
        assert sess.get_project()["name"] == "v2"
        sess.undo()
        assert sess.get_project()["name"] == "v1"

    def test_multiple_redo(self):
        sess = Session()
        proj = create_project(name="v1")
        sess.set_project(proj)

        sess.snapshot("to v2")
        proj["name"] = "v2"

        sess.snapshot("to v3")
        proj["name"] = "v3"

        sess.undo()
        sess.undo()
        assert sess.get_project()["name"] == "v1"

        sess.redo()
        assert sess.get_project()["name"] == "v2"
        sess.redo()
        assert sess.get_project()["name"] == "v3"
