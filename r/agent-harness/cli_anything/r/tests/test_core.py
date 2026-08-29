"""Unit tests for R CLI core modules — no external dependencies required."""

import os
import sys
import json
import pytest
import tempfile
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from cli_anything.r.core import project as proj_mod
from cli_anything.r.core.session import Session
from cli_anything.r.core import data as data_mod
from cli_anything.r.core import analysis as analysis_mod
from cli_anything.r.core import plot as plot_mod
from cli_anything.r.core import script as script_mod


@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory(prefix="r_test_") as d:
        yield d


@pytest.fixture
def project():
    from cli_anything.r.core.project import create_project
    return create_project(name="test")


@pytest.fixture
def project_with_data(project):
    from cli_anything.r.core.data import add_dataset
    add_dataset(project, "iris", dataset_type="builtin")
    return project


# ===================================================================
# Project tests
# ===================================================================

class TestProject:
    def test_create_project(self):
        proj = proj_mod.create_project()
        assert proj["version"] == "1.0"
        assert proj["name"] == "untitled"
        assert "datasets" in proj
        assert "analyses" in proj
        assert "plots" in proj
        assert "scripts" in proj
        assert "packages" in proj
        assert "metadata" in proj
        assert isinstance(proj["datasets"], list)

    def test_create_project_with_name(self):
        proj = proj_mod.create_project(name="my_analysis")
        assert proj["name"] == "my_analysis"

    def test_create_project_with_profile(self):
        proj = proj_mod.create_project(profile="basic_analysis")
        assert "ggplot2" in proj["packages"]
        assert "dplyr" in proj["packages"]

    def test_create_project_invalid_profile(self):
        with pytest.raises(ValueError, match="Unknown profile"):
            proj_mod.create_project(profile="nonexistent_profile")

    def test_save_and_open(self, tmp_dir):
        proj = proj_mod.create_project(name="save_test")
        path = os.path.join(tmp_dir, "proj.json")
        proj_mod.save_project(proj, path)
        assert os.path.exists(path)

        loaded = proj_mod.open_project(path)
        assert loaded["name"] == "save_test"
        assert loaded["version"] == "1.0"

    def test_open_nonexistent(self):
        with pytest.raises(FileNotFoundError):
            proj_mod.open_project("/nonexistent/path.json")

    def test_open_invalid(self, tmp_dir):
        path = os.path.join(tmp_dir, "bad.json")
        with open(path, "w") as f:
            json.dump({}, f)
        with pytest.raises(ValueError, match="version"):
            proj_mod.open_project(path)

    def test_project_info(self):
        proj = proj_mod.create_project(name="info_test")
        info = proj_mod.get_project_info(proj)
        assert info["name"] == "info_test"
        assert info["version"] == "1.0"
        assert "dataset_count" in info
        assert "analysis_count" in info
        assert "plot_count" in info
        assert "script_count" in info
        assert "packages" in info
        assert "metadata" in info

    def test_list_profiles(self):
        profiles = proj_mod.list_profiles()
        assert isinstance(profiles, list)
        assert len(profiles) > 0
        names = [p["name"] for p in profiles]
        assert "basic_analysis" in names


# ===================================================================
# Session tests
# ===================================================================

class TestSession:
    def test_initial_state(self):
        sess = Session()
        assert not sess.has_project()

    def test_set_project(self, project):
        sess = Session()
        sess.set_project(project)
        assert sess.has_project()
        assert sess.get_project()["name"] == "test"

    def test_get_project_no_project(self):
        sess = Session()
        with pytest.raises(RuntimeError, match="No project"):
            sess.get_project()

    def test_snapshot_undo_redo(self, project):
        sess = Session()
        sess.set_project(project)
        sess.snapshot("change name")
        project["name"] = "changed"
        assert sess.get_project()["name"] == "changed"
        desc = sess.undo()
        assert desc == "change name"
        assert sess.get_project()["name"] == "test"

    def test_redo(self, project):
        sess = Session()
        sess.set_project(project)
        sess.snapshot("change")
        project["name"] = "changed"
        sess.undo()
        assert sess.get_project()["name"] == "test"
        sess.redo()
        assert sess.get_project()["name"] == "changed"

    def test_max_undo(self, project):
        sess = Session()
        sess.set_project(project)
        for i in range(51):
            sess.snapshot(f"step {i}")
            project["name"] = f"v{i}"
        assert len(sess._undo_stack) == 50

    def test_save_session(self, project, tmp_dir):
        sess = Session()
        path = os.path.join(tmp_dir, "session.json")
        sess.set_project(project, path)
        saved = sess.save_session()
        assert saved == path
        assert os.path.exists(path)

    def test_list_history(self, project):
        sess = Session()
        sess.set_project(project)
        sess.snapshot("step 1")
        sess.snapshot("step 2")
        history = sess.list_history()
        assert len(history) == 2
        assert history[0]["description"] == "step 2"


# ===================================================================
# Data tests
# ===================================================================

class TestData:
    def test_add_dataset_builtin(self, project):
        entry = data_mod.add_dataset(project, "iris", dataset_type="builtin")
        assert entry["name"] == "iris"
        assert entry["format"] == "builtin"
        assert len(project["datasets"]) == 1

    def test_add_dataset_file(self, project, tmp_dir):
        path = os.path.join(tmp_dir, "data.csv")
        with open(path, "w") as f:
            f.write("a,b\n1,2\n3,4\n")
        entry = data_mod.add_dataset(project, path)
        assert entry["format"] == "csv"
        assert entry["name"] == "data"
        assert len(project["datasets"]) == 1

    def test_add_nonexistent_file(self, project):
        with pytest.raises(FileNotFoundError):
            data_mod.add_dataset(project, "/nonexistent/file.csv")

    def test_remove_dataset(self, project_with_data):
        removed = data_mod.remove_dataset(project_with_data, 0)
        assert removed["name"] == "iris"
        assert len(project_with_data["datasets"]) == 0

    def test_list_datasets(self, project_with_data):
        datasets = data_mod.list_datasets(project_with_data)
        assert len(datasets) == 1
        assert datasets[0]["name"] == "iris"

    def test_format_detection(self, tmp_dir, project):
        for ext, expected in [(".csv", "csv"), (".tsv", "tsv"), (".xlsx", "xlsx"), (".rds", "rds")]:
            path = os.path.join(tmp_dir, f"data{ext}")
            with open(path, "w") as f:
                f.write("dummy")
            # For xlsx and rds we just verify the extension is detected
            entry = data_mod.add_dataset(project, path)
            assert entry["format"] == expected

    def test_build_load_code_builtin(self, project_with_data):
        dataset = data_mod.get_dataset(project_with_data, 0)
        code = data_mod._build_load_code(dataset)
        assert "data(iris)" in code

    def test_build_load_code_csv(self, project, tmp_dir):
        path = os.path.join(tmp_dir, "test.csv")
        with open(path, "w") as f:
            f.write("x,y\n1,2\n")
        data_mod.add_dataset(project, path)
        dataset = data_mod.get_dataset(project, 0)
        code = data_mod._build_load_code(dataset)
        assert "read.csv" in code

    def test_filter_dataset(self, project_with_data):
        entry = data_mod.filter_dataset(
            project_with_data, 0, "Species == 'setosa'"
        )
        assert entry["format"] == "derived"
        assert entry["derive_type"] == "filter"
        assert len(project_with_data["datasets"]) == 2


# ===================================================================
# Analysis tests
# ===================================================================

class TestAnalysis:
    def test_list_analyses(self):
        analyses = analysis_mod.list_analyses()
        assert isinstance(analyses, list)
        assert len(analyses) > 0
        for a in analyses:
            assert "name" in a
            assert "category" in a
            assert "description" in a

    def test_list_analyses_by_category(self):
        analyses = analysis_mod.list_analyses(category="hypothesis")
        assert len(analyses) > 0
        assert all(a["category"] == "hypothesis" for a in analyses)

    def test_get_analysis_info(self):
        info = analysis_mod.get_analysis_info("ttest")
        assert info["name"] == "ttest"
        assert info["category"] == "hypothesis"

    def test_get_analysis_info_invalid(self):
        with pytest.raises(ValueError, match="Unknown analysis"):
            analysis_mod.get_analysis_info("nonexistent_analysis")

    def test_add_analysis(self, project_with_data):
        entry = analysis_mod.add_analysis(
            project_with_data, "ttest", 0,
            params={"formula": "Sepal.Length ~ Species"}
        )
        assert entry["type"] == "ttest"
        assert entry["dataset_index"] == 0
        assert len(project_with_data["analyses"]) == 1

    def test_build_analysis_script(self, project_with_data):
        analysis_mod.add_analysis(
            project_with_data, "regression", 0,
            params={"formula": "Sepal.Length ~ Sepal.Width"}
        )
        script = analysis_mod.build_analysis_script(project_with_data, 0)
        assert "lm(" in script
        assert "toJSON" in script

    def test_remove_analysis(self, project_with_data):
        analysis_mod.add_analysis(
            project_with_data, "ttest", 0,
            params={"formula": "Sepal.Length ~ Species"}
        )
        removed = analysis_mod.remove_analysis(project_with_data, 0)
        assert removed["type"] == "ttest"
        assert len(project_with_data["analyses"]) == 0


# ===================================================================
# Plot tests
# ===================================================================

class TestPlot:
    def test_list_plot_types(self):
        types = plot_mod.list_plot_types()
        assert isinstance(types, list)
        assert len(types) > 0

    def test_add_plot(self, project_with_data):
        entry = plot_mod.add_plot(
            project_with_data, "scatter", 0,
            aes_mapping={"x": "Sepal.Length", "y": "Sepal.Width"}
        )
        assert entry["type"] == "scatter"
        assert entry["aes"]["x"] == "Sepal.Length"
        assert len(project_with_data["plots"]) == 1

    def test_add_plot_missing_aes(self, project_with_data):
        with pytest.raises(ValueError, match="Missing required"):
            plot_mod.add_plot(
                project_with_data, "scatter", 0,
                aes_mapping={"x": "Sepal.Length"}
            )

    def test_add_plot_invalid_type(self, project_with_data):
        with pytest.raises(ValueError, match="Unknown plot type"):
            plot_mod.add_plot(
                project_with_data, "nonexistent_plot", 0,
                aes_mapping={"x": "a", "y": "b"}
            )

    def test_customize_plot(self, project_with_data):
        plot_mod.add_plot(
            project_with_data, "scatter", 0,
            aes_mapping={"x": "Sepal.Length", "y": "Sepal.Width"}
        )
        updated = plot_mod.customize_plot(
            project_with_data, 0,
            title="My Plot", theme="theme_bw"
        )
        assert updated["customization"]["title"] == "My Plot"
        assert updated["customization"]["theme"] == "theme_bw"

    def test_build_plot_script(self, project_with_data, tmp_dir):
        plot_mod.add_plot(
            project_with_data, "scatter", 0,
            aes_mapping={"x": "Sepal.Length", "y": "Sepal.Width"}
        )
        output_path = os.path.join(tmp_dir, "plot.png")
        script = plot_mod.build_plot_script(
            project_with_data, 0, output_path
        )
        assert "ggplot" in script
        assert "geom_point" in script
        assert "ggsave" in script

    def test_remove_plot(self, project_with_data):
        plot_mod.add_plot(
            project_with_data, "scatter", 0,
            aes_mapping={"x": "Sepal.Length", "y": "Sepal.Width"}
        )
        removed = plot_mod.remove_plot(project_with_data, 0)
        assert removed["type"] == "scatter"
        assert len(project_with_data["plots"]) == 0


# ===================================================================
# Script tests
# ===================================================================

class TestScript:
    def test_add_script(self, project):
        entry = script_mod.add_script(project, "print('hello')", name="greet")
        assert entry["name"] == "greet"
        assert entry["code"] == "print('hello')"
        assert len(project["scripts"]) == 1

    def test_list_scripts(self, project):
        script_mod.add_script(project, "x <- 1", name="s1")
        script_mod.add_script(project, "y <- 2", name="s2")
        scripts = script_mod.list_scripts(project)
        assert len(scripts) == 2

    def test_get_script(self, project):
        script_mod.add_script(project, "cat('test')", name="s1")
        s = script_mod.get_script(project, 0)
        assert s["code"] == "cat('test')"

    def test_remove_script(self, project):
        script_mod.add_script(project, "code", name="s1")
        removed = script_mod.remove_script(project, 0)
        assert removed["name"] == "s1"
        assert len(project["scripts"]) == 0

    def test_remove_invalid_index(self, project):
        with pytest.raises(IndexError):
            script_mod.remove_script(project, 0)
