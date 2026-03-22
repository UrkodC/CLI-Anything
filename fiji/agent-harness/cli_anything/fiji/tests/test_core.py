"""Fiji CLI — Unit tests for all core modules.

Tests use synthetic data only. No external dependencies on Fiji.
"""

import json
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from cli_anything.fiji.core import project as proj_mod
from cli_anything.fiji.core.session import Session
from cli_anything.fiji.core import image as img_mod
from cli_anything.fiji.core import processing as proc_mod
from cli_anything.fiji.core import roi as roi_mod
from cli_anything.fiji.core import measure as meas_mod
from cli_anything.fiji.core import macro as macro_mod
from cli_anything.fiji.core import export as export_mod


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="fiji_test_") as d:
        yield d


@pytest.fixture
def sample_project():
    return proj_mod.create_project(name="test_proj", width=256, height=256)


@pytest.fixture
def sample_image_file(tmp_dir):
    """Create a minimal dummy image file for testing."""
    path = os.path.join(tmp_dir, "test.tif")
    # Write a minimal valid-ish file (just needs to exist for unit tests)
    with open(path, "wb") as f:
        f.write(b"\x49\x49\x2a\x00" + b"\x00" * 100)  # Minimal TIFF header
    return path


# ═══════════════════════════════════════════════════════════════
# Project tests
# ═══════════════════════════════════════════════════════════════

class TestProject:
    def test_create_project_defaults(self):
        proj = proj_mod.create_project()
        assert proj["name"] == "untitled"
        assert proj["image"]["width"] == 512
        assert proj["image"]["height"] == 512
        assert proj["image"]["bit_depth"] == 8
        assert proj["image"]["channels"] == 1
        assert proj["version"] == "1.0"
        assert "metadata" in proj

    def test_create_project_custom(self):
        proj = proj_mod.create_project(
            width=1024, height=768, bit_depth=16, channels=3,
            slices=10, frames=50, name="experiment"
        )
        assert proj["image"]["width"] == 1024
        assert proj["image"]["height"] == 768
        assert proj["image"]["bit_depth"] == 16
        assert proj["image"]["channels"] == 3
        assert proj["image"]["slices"] == 10
        assert proj["image"]["frames"] == 50
        assert proj["name"] == "experiment"

    def test_create_project_with_profile(self):
        proj = proj_mod.create_project(profile="confocal_1024")
        assert proj["image"]["width"] == 1024
        assert proj["image"]["height"] == 1024
        assert proj["image"]["bit_depth"] == 16

    def test_create_project_invalid_dims(self):
        with pytest.raises(ValueError, match="positive"):
            proj_mod.create_project(width=0, height=100)

    def test_create_project_invalid_bit_depth(self):
        with pytest.raises(ValueError, match="bit depth"):
            proj_mod.create_project(bit_depth=24)

    def test_save_and_open_project(self, tmp_dir):
        proj = proj_mod.create_project(name="save_test")
        path = os.path.join(tmp_dir, "proj.json")
        proj_mod.save_project(proj, path)
        assert os.path.exists(path)

        loaded = proj_mod.open_project(path)
        assert loaded["name"] == "save_test"
        assert loaded["image"]["width"] == 512

    def test_open_project_not_found(self):
        with pytest.raises(FileNotFoundError):
            proj_mod.open_project("/nonexistent/path.json")

    def test_open_project_invalid(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad.json")
        with open(path, "w") as f:
            json.dump({"foo": "bar"}, f)
        with pytest.raises(ValueError, match="Invalid"):
            proj_mod.open_project(path)

    def test_get_project_info(self, sample_project):
        info = proj_mod.get_project_info(sample_project)
        assert info["name"] == "test_proj"
        assert info["image"]["width"] == 256
        assert info["image_count"] == 0
        assert info["roi_count"] == 0

    def test_list_profiles(self):
        profiles = proj_mod.list_profiles()
        assert len(profiles) > 0
        names = [p["name"] for p in profiles]
        assert "confocal_512" in names
        assert "hyperstack" in names


# ═══════════════════════════════════════════════════════════════
# Session tests
# ═══════════════════════════════════════════════════════════════

class TestSession:
    def test_session_new(self):
        sess = Session()
        assert not sess.has_project()

    def test_session_set_project(self, sample_project):
        sess = Session()
        sess.set_project(sample_project)
        assert sess.has_project()
        assert sess.get_project()["name"] == "test_proj"

    def test_session_get_no_project(self):
        sess = Session()
        with pytest.raises(RuntimeError, match="No project"):
            sess.get_project()

    def test_snapshot_and_undo(self, sample_project):
        sess = Session()
        sess.set_project(sample_project)
        sess.snapshot("change name")
        sample_project["name"] = "changed"
        assert sess.get_project()["name"] == "changed"
        desc = sess.undo()
        assert desc == "change name"
        assert sess.get_project()["name"] == "test_proj"

    def test_undo_empty(self, sample_project):
        sess = Session()
        sess.set_project(sample_project)
        with pytest.raises(RuntimeError, match="Nothing to undo"):
            sess.undo()

    def test_redo(self, sample_project):
        sess = Session()
        sess.set_project(sample_project)
        sess.snapshot("change")
        sample_project["name"] = "changed"
        sess.undo()
        assert sess.get_project()["name"] == "test_proj"
        sess.redo()
        assert sess.get_project()["name"] == "changed"

    def test_redo_empty(self, sample_project):
        sess = Session()
        sess.set_project(sample_project)
        with pytest.raises(RuntimeError, match="Nothing to redo"):
            sess.redo()

    def test_undo_max(self, sample_project):
        sess = Session()
        sess.set_project(sample_project)
        for i in range(60):
            sess.snapshot(f"step {i}")
            sample_project["name"] = f"v{i}"
        assert len(sess._undo_stack) == Session.MAX_UNDO

    def test_session_status(self, sample_project):
        sess = Session()
        sess.set_project(sample_project, "/tmp/test.json")
        status = sess.status()
        assert status["has_project"] is True
        assert status["project_path"] == "/tmp/test.json"
        assert status["undo_count"] == 0

    def test_save_session(self, sample_project, tmp_dir):
        sess = Session()
        path = os.path.join(tmp_dir, "session.json")
        sess.set_project(sample_project, path)
        saved = sess.save_session()
        assert saved == path
        assert os.path.exists(path)

    def test_list_history(self, sample_project):
        sess = Session()
        sess.set_project(sample_project)
        sess.snapshot("step 1")
        sess.snapshot("step 2")
        history = sess.list_history()
        assert len(history) == 2
        assert history[0]["description"] == "step 2"

    def test_save_session_no_metadata(self):
        """save_session should not crash if project has no metadata key."""
        sess = Session()
        proj = {"name": "bare_project", "images": []}
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            sess.set_project(proj, path)
            saved = sess.save_session()
            assert os.path.exists(saved)
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════
# Image tests
# ═══════════════════════════════════════════════════════════════

class TestImage:
    def test_add_image(self, sample_project, sample_image_file):
        entry = img_mod.add_image(sample_project, sample_image_file, name="test_img")
        assert entry["name"] == "test_img"
        assert entry["id"] == 0
        assert entry["format"] == "tif"
        assert len(sample_project["images"]) == 1

    def test_add_image_not_found(self, sample_project):
        with pytest.raises(FileNotFoundError):
            img_mod.add_image(sample_project, "/nonexistent/image.tif")

    def test_remove_image(self, sample_project, sample_image_file):
        img_mod.add_image(sample_project, sample_image_file)
        removed = img_mod.remove_image(sample_project, 0)
        assert removed["id"] == 0
        assert len(sample_project["images"]) == 0

    def test_remove_image_invalid(self, sample_project):
        with pytest.raises(IndexError):
            img_mod.remove_image(sample_project, 0)

    def test_list_images(self, sample_project, sample_image_file):
        img_mod.add_image(sample_project, sample_image_file, name="img1")
        images = img_mod.list_images(sample_project)
        assert len(images) == 1
        assert images[0]["name"] == "img1"


# ═══════════════════════════════════════════════════════════════
# Processing tests
# ═══════════════════════════════════════════════════════════════

class TestProcessing:
    def test_list_operations(self):
        ops = proc_mod.list_operations()
        assert len(ops) > 10
        names = [o["name"] for o in ops]
        assert "gaussian_blur" in names
        assert "watershed" in names

    def test_list_operations_filtered(self):
        ops = proc_mod.list_operations(category="morphology")
        assert all(o["category"] == "morphology" for o in ops)
        names = [o["name"] for o in ops]
        assert "erode" in names

    def test_get_operation_info(self):
        info = proc_mod.get_operation_info("gaussian_blur")
        assert info["category"] == "filter"
        assert "sigma" in info["params"]
        assert "macro_template" in info

    def test_get_operation_unknown(self):
        with pytest.raises(ValueError, match="Unknown operation"):
            proc_mod.get_operation_info("nonexistent_op")

    def test_add_processing_step(self, sample_project):
        step = proc_mod.add_processing_step(sample_project, "gaussian_blur")
        assert step["operation"] == "gaussian_blur"
        assert step["id"] == 0
        assert "sigma" in step["params"]
        assert 'Gaussian Blur' in step["macro"]

    def test_add_step_with_params(self, sample_project):
        step = proc_mod.add_processing_step(
            sample_project, "gaussian_blur", params={"sigma": 5.0}
        )
        assert step["params"]["sigma"] == 5.0
        assert "5.0" in step["macro"]

    def test_remove_step(self, sample_project):
        proc_mod.add_processing_step(sample_project, "gaussian_blur")
        removed = proc_mod.remove_processing_step(sample_project, 0)
        assert removed["operation"] == "gaussian_blur"
        assert len(sample_project["processing_log"]) == 0

    def test_list_processing_log(self, sample_project):
        proc_mod.add_processing_step(sample_project, "gaussian_blur")
        proc_mod.add_processing_step(sample_project, "auto_threshold")
        log = proc_mod.list_processing_log(sample_project)
        assert len(log) == 2

    def test_build_macro(self, sample_project):
        proc_mod.add_processing_step(sample_project, "gaussian_blur", params={"sigma": 3.0})
        proc_mod.add_processing_step(sample_project, "auto_threshold", params={"method": "Otsu"})
        macro = proc_mod.build_macro(sample_project)
        assert 'Gaussian Blur' in macro
        assert 'Otsu' in macro

    def test_add_scale_bar(self):
        proj = proj_mod.create_project()
        step = proc_mod.add_processing_step(proj, "add_scale_bar", params={
            "width": 10, "height": 4, "font": 14,
            "color": "White", "location": "Lower Right"
        })
        assert step["operation"] == "add_scale_bar"
        assert "Scale Bar" in step["macro"]

    def test_set_scale(self):
        proj = proj_mod.create_project()
        step = proc_mod.add_processing_step(proj, "set_scale", params={
            "distance": 100, "known": 10, "unit": "um"
        })
        assert "Set Scale" in step["macro"]

    def test_apply_lut(self):
        proj = proj_mod.create_project()
        step = proc_mod.add_processing_step(proj, "apply_lut", params={"lut": "Fire"})
        assert "Fire" in step["macro"]

    def test_apply_lut_grays(self):
        proj = proj_mod.create_project()
        step = proc_mod.add_processing_step(proj, "apply_lut", params={"lut": "Grays"})
        assert "Grays" in step["macro"]

    def test_set_display_range(self):
        proj = proj_mod.create_project()
        step = proc_mod.add_processing_step(proj, "set_display_range", params={"min_val": 100, "max_val": 4000})
        assert "setMinAndMax" in step["macro"]

    def test_invert_lut(self):
        proj = proj_mod.create_project()
        step = proc_mod.add_processing_step(proj, "invert_lut")
        assert "Invert LUT" in step["macro"]

    def test_add_calibration_bar(self):
        proj = proj_mod.create_project()
        step = proc_mod.add_processing_step(proj, "add_calibration_bar", params={
            "location": "Upper Right", "label_color": "White", "font": 12
        })
        assert "Calibration Bar" in step["macro"]


# ═══════════════════════════════════════════════════════════════
# ROI tests
# ═══════════════════════════════════════════════════════════════

class TestROI:
    def test_add_rectangle_roi(self, sample_project):
        r = roi_mod.add_roi(sample_project, "rectangle", x=10, y=20, width=100, height=50)
        assert r["type"] == "rectangle"
        assert r["params"]["x"] == 10

    def test_add_oval_roi(self, sample_project):
        r = roi_mod.add_roi(sample_project, "oval", x=0, y=0, width=50, height=50)
        assert r["type"] == "oval"

    def test_add_line_roi(self, sample_project):
        r = roi_mod.add_roi(sample_project, "line", x1=0, y1=0, x2=100, y2=100)
        assert r["type"] == "line"

    def test_add_point_roi(self, sample_project):
        r = roi_mod.add_roi(sample_project, "point", x=50, y=50)
        assert r["type"] == "point"

    def test_add_roi_missing_params(self, sample_project):
        with pytest.raises(ValueError, match="Missing required"):
            roi_mod.add_roi(sample_project, "rectangle", x=10)

    def test_remove_roi(self, sample_project):
        roi_mod.add_roi(sample_project, "rectangle", x=0, y=0, width=10, height=10)
        removed = roi_mod.remove_roi(sample_project, 0)
        assert removed["type"] == "rectangle"
        assert len(sample_project["rois"]) == 0

    def test_list_rois(self, sample_project):
        roi_mod.add_roi(sample_project, "rectangle", x=0, y=0, width=10, height=10, name="r1")
        roi_mod.add_roi(sample_project, "oval", x=0, y=0, width=20, height=20, name="o1")
        rois = roi_mod.list_rois(sample_project)
        assert len(rois) == 2
        assert rois[0]["name"] == "r1"

    def test_build_roi_macro(self, sample_project):
        roi_mod.add_roi(sample_project, "rectangle", x=10, y=20, width=100, height=50)
        r = roi_mod.get_roi(sample_project, 0)
        macro = roi_mod.build_roi_macro(r)
        assert "makeRectangle(10, 20, 100, 50)" in macro


# ═══════════════════════════════════════════════════════════════
# Measure tests
# ═══════════════════════════════════════════════════════════════

class TestMeasure:
    def test_list_measurement_types(self):
        types = meas_mod.list_measurement_types()
        assert len(types) > 5
        names = [t["name"] for t in types]
        assert "area" in names
        assert "mean" in names

    def test_add_measurement_config(self, sample_project):
        config = meas_mod.add_measurement_config(
            sample_project, measurements=["area", "mean"]
        )
        assert "area" in config["measurements"]
        assert config["scale"]["unit"] == "pixel"

    def test_add_measurement_config_invalid(self, sample_project):
        with pytest.raises(ValueError, match="Unknown measurement"):
            meas_mod.add_measurement_config(
                sample_project, measurements=["nonexistent"]
            )

    def test_add_measurement_result(self, sample_project):
        result = meas_mod.add_measurement_result(
            sample_project, "cell_1", {"area": 1500.5, "mean": 128.3}
        )
        assert result["label"] == "cell_1"
        assert result["values"]["area"] == 1500.5

    def test_list_measurements(self, sample_project):
        meas_mod.add_measurement_result(sample_project, "r1", {"area": 100})
        results = meas_mod.list_measurements(sample_project)
        assert len(results) == 1

    def test_clear_measurements(self, sample_project):
        meas_mod.add_measurement_result(sample_project, "r1", {"area": 100})
        meas_mod.add_measurement_result(sample_project, "r2", {"area": 200})
        count = meas_mod.clear_measurements(sample_project)
        assert count == 2
        assert len(sample_project["measurements"]) == 0

    def test_build_analysis_macro(self):
        macro = meas_mod.build_analysis_macro("analyze_particles", {
            "min_size": 50, "max_size": 1000
        })
        assert "Analyze Particles" in macro
        assert "50" in macro


# ═══════════════════════════════════════════════════════════════
# Macro tests
# ═══════════════════════════════════════════════════════════════

class TestMacro:
    def test_add_macro(self, sample_project):
        entry = macro_mod.add_macro(
            sample_project, 'run("Gaussian Blur...", "sigma=2");',
            name="blur", description="Apply blur"
        )
        assert entry["name"] == "blur"
        assert entry["id"] == 0

    def test_remove_macro(self, sample_project):
        macro_mod.add_macro(sample_project, "code", name="m1")
        removed = macro_mod.remove_macro(sample_project, 0)
        assert removed["name"] == "m1"
        assert len(sample_project["macros"]) == 0

    def test_list_macros(self, sample_project):
        macro_mod.add_macro(sample_project, "code1", name="m1")
        macro_mod.add_macro(sample_project, "code2", name="m2")
        macros = macro_mod.list_macros(sample_project)
        assert len(macros) == 2
        assert macros[1]["name"] == "m2"

    def test_get_macro(self, sample_project):
        macro_mod.add_macro(sample_project, "test_code", name="m1")
        m = macro_mod.get_macro(sample_project, 0)
        assert m["code"] == "test_code"

    def test_build_batch_macro(self, sample_project):
        proc_mod.add_processing_step(sample_project, "gaussian_blur")
        macro = macro_mod.build_batch_macro(
            sample_project, "/input", "/output", ".*\\.tif"
        )
        assert "setBatchMode(true)" in macro
        assert "Gaussian Blur" in macro
        assert "/input" in macro
        assert "/output" in macro
