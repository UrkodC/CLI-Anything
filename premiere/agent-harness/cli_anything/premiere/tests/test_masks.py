"""mask list: masks of a saved Premiere 27 project. The fixtures are real project files and stay local (not in git)."""
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli_anything.premiere.core import masks
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.premiere_cli import cli

LOCAL = Path(__file__).resolve().parent / "fixtures_local" / "masks"
needs_fixtures = pytest.mark.skipif(not (LOCAL / "fixture_2_object_mask_tracked.prproj").is_file(), reason="local mask fixtures are missing")


@needs_fixtures
def test_project_without_a_mask_lists_none():
    assert masks.list_masks(LOCAL / "fixture_0_no_mask.prproj")["masks"] == []


@needs_fixtures
def test_object_mask_before_tracking():
    found = masks.list_masks(LOCAL / "fixture_1_object_mask.prproj")["masks"]
    assert len(found) == 1
    mask = found[0]
    assert (mask["instance"], mask["type"], mask["type_name"], mask["tracked"]) == ("01", 4, "object", False)
    assert (mask["feather"], mask["opacity"], mask["expansion"], mask["blend_mode_name"]) == (0, 100, 0, "add")
    assert len(mask["data_ids"]) >= 1


@needs_fixtures
def test_tracking_sets_the_flag_and_adds_a_data_file():
    before = masks.list_masks(LOCAL / "fixture_1_object_mask.prproj")["masks"][0]
    after = masks.list_masks(LOCAL / "fixture_2_object_mask_tracked.prproj")["masks"][0]
    assert after["tracked"] is True
    assert len(after["data_ids"]) == len(before["data_ids"]) + 1


@needs_fixtures
def test_data_files_are_found_next_to_the_project():
    result = masks.list_masks(LOCAL / "Mask gate test Claude.prproj")
    assert result["mask_folder"].endswith("Mask gate test Claude Masks")
    files = result["masks"][0]["data_files"]
    assert len(files) == 2 and all(f["file"].endswith(".prmf") and f["bytes"] > 0 for f in files)


def test_a_file_that_is_no_project_is_refused(tmp_path):
    bad = tmp_path / "x.prproj"
    bad.write_text("not a project")
    with pytest.raises(CliError):
        masks.list_masks(bad)


@needs_fixtures
def test_command_prints_json():
    done = CliRunner().invoke(cli, ["--json", "mask", "list", str(LOCAL / "fixture_2_object_mask_tracked.prproj")])
    assert done.exit_code == 0 and '"tracked": true' in done.output
