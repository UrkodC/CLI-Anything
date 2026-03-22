# Fiji CLI — Test Plan and Results

## Test Inventory Plan

- `test_core.py`: ~45 unit tests planned
- `test_full_e2e.py`: ~15 E2E tests planned

## Unit Test Plan (`test_core.py`)

### project.py
- `test_create_project_defaults` — Default values correct
- `test_create_project_custom` — Custom dimensions/channels
- `test_create_project_with_profile` — Profile applies values
- `test_create_project_invalid_dims` — Negative dimensions raise ValueError
- `test_create_project_invalid_bit_depth` — Bad bit depth raises ValueError
- `test_open_project` — Load from JSON file
- `test_open_project_not_found` — Missing file raises FileNotFoundError
- `test_open_project_invalid` — Invalid JSON raises ValueError
- `test_save_project` — Save and verify JSON
- `test_get_project_info` — Summary dict correct
- `test_list_profiles` — All profiles listed
- Expected: ~11 tests

### session.py
- `test_session_new` — Fresh session has no project
- `test_session_set_project` — Set and get project
- `test_session_get_no_project` — Raises RuntimeError
- `test_snapshot_and_undo` — Undo restores previous state
- `test_undo_empty` — Undo with no history raises RuntimeError
- `test_redo` — Redo restores undone state
- `test_redo_empty` — Redo with no redo stack raises RuntimeError
- `test_undo_max` — Stack limited to MAX_UNDO
- `test_session_status` — Status dict correct
- `test_save_session` — Save to disk
- `test_list_history` — History entries correct
- Expected: ~11 tests

### image.py
- `test_add_image` — Add image reference
- `test_add_image_not_found` — Missing file raises FileNotFoundError
- `test_remove_image` — Remove by index
- `test_remove_image_invalid` — Bad index raises IndexError
- `test_list_images` — List all images
- Expected: ~5 tests

### processing.py
- `test_list_operations` — All operations listed
- `test_list_operations_filtered` — Category filter works
- `test_get_operation_info` — Details correct
- `test_get_operation_unknown` — Unknown raises ValueError
- `test_add_processing_step` — Step added with correct macro
- `test_add_step_with_params` — Custom params merged
- `test_remove_step` — Remove by index
- `test_list_processing_log` — Log entries correct
- `test_build_macro` — Macro string built correctly
- Expected: ~9 tests

### roi.py
- `test_add_rectangle_roi` — Rectangle ROI added
- `test_add_oval_roi` — Oval ROI
- `test_add_line_roi` — Line ROI
- `test_add_point_roi` — Point ROI
- `test_add_roi_missing_params` — Missing params raise ValueError
- `test_remove_roi` — Remove by index
- `test_list_rois` — List all ROIs
- `test_build_roi_macro` — Macro string for each ROI type
- Expected: ~8 tests

### measure.py
- `test_list_measurement_types` — Types listed
- `test_add_measurement_config` — Config stored
- `test_add_measurement_config_invalid` — Bad type raises ValueError
- `test_add_measurement_result` — Result stored
- `test_list_measurements` — Results listed
- `test_clear_measurements` — All cleared
- `test_build_analysis_macro` — Macro built
- Expected: ~7 tests

### macro.py
- `test_add_macro` — Macro stored
- `test_remove_macro` — Remove by index
- `test_list_macros` — List all
- `test_get_macro` — Get by index
- `test_build_batch_macro` — Batch macro generated
- Expected: ~5 tests

## E2E Test Plan (`test_full_e2e.py`)

### Fiji Backend Tests
- `test_find_fiji` — Fiji executable found
- `test_create_test_image` — Create test TIFF via Fiji
- `test_process_image_blur` — Apply Gaussian blur via Fiji
- `test_process_image_threshold` — Apply threshold via Fiji

### Full Pipeline Tests
- `test_full_pipeline_blur_export` — Create project → add image → blur → export
- `test_full_pipeline_threshold_watershed` — Threshold + watershed + analyze particles
- `test_batch_macro_generation` — Generate and verify batch macro

### CLI Subprocess Tests
- `test_cli_help` — --help works
- `test_cli_json_project_new` — Create project via CLI
- `test_cli_project_roundtrip` — New → save → open → info
- `test_cli_process_pipeline` — Add processing steps via CLI
- `test_cli_roi_management` — Add/list ROIs via CLI
- `test_cli_full_workflow` — Complete workflow via subprocess
- `test_cli_backend_find` — Backend find via CLI
- `test_cli_export_render` — Full export via CLI + Fiji

## Realistic Workflow Scenarios

### Scenario 1: Fluorescence Microscopy Analysis
- **Simulates**: Analyzing fluorescence micrographs
- **Operations**: Load TIFF → subtract background → Gaussian blur → auto threshold → analyze particles → export
- **Verified**: Output file exists, correct format, non-zero size

### Scenario 2: Batch Image Processing
- **Simulates**: Processing a folder of microscopy images
- **Operations**: Build processing pipeline → generate batch macro → verify macro syntax
- **Verified**: Macro contains correct commands, file iteration loop

### Scenario 3: ROI-Based Measurement
- **Simulates**: Measuring specific regions in an image
- **Operations**: Load image → add ROIs → configure measurements → generate analysis macro
- **Verified**: ROI macros correct, measurement config stored

---

## Test Results

```
CLI_ANYTHING_FORCE_INSTALLED=1 python3 -m pytest cli_anything/fiji/tests/ -v --tb=no

============================= test session starts ==============================
platform darwin -- Python 3.12.2, pytest-9.0.2, pluggy-1.5.0
[_resolve_cli] Using installed command: /usr/local/Caskroom/miniconda/base/bin/cli-anything-fiji
collected 72 items

cli_anything/fiji/tests/test_core.py::TestProject::test_create_project_defaults PASSED [  1%]
cli_anything/fiji/tests/test_core.py::TestProject::test_create_project_custom PASSED [  2%]
cli_anything/fiji/tests/test_core.py::TestProject::test_create_project_with_profile PASSED [  4%]
cli_anything/fiji/tests/test_core.py::TestProject::test_create_project_invalid_dims PASSED [  5%]
cli_anything/fiji/tests/test_core.py::TestProject::test_create_project_invalid_bit_depth PASSED [  6%]
cli_anything/fiji/tests/test_core.py::TestProject::test_save_and_open_project PASSED [  8%]
cli_anything/fiji/tests/test_core.py::TestProject::test_open_project_not_found PASSED [  9%]
cli_anything/fiji/tests/test_core.py::TestProject::test_open_project_invalid PASSED [ 11%]
cli_anything/fiji/tests/test_core.py::TestProject::test_get_project_info PASSED [ 12%]
cli_anything/fiji/tests/test_core.py::TestProject::test_list_profiles PASSED [ 13%]
cli_anything/fiji/tests/test_core.py::TestSession::test_session_new PASSED [ 15%]
cli_anything/fiji/tests/test_core.py::TestSession::test_session_set_project PASSED [ 16%]
cli_anything/fiji/tests/test_core.py::TestSession::test_session_get_no_project PASSED [ 18%]
cli_anything/fiji/tests/test_core.py::TestSession::test_snapshot_and_undo PASSED [ 19%]
cli_anything/fiji/tests/test_core.py::TestSession::test_undo_empty PASSED [ 20%]
cli_anything/fiji/tests/test_core.py::TestSession::test_redo PASSED      [ 22%]
cli_anything/fiji/tests/test_core.py::TestSession::test_redo_empty PASSED [ 23%]
cli_anything/fiji/tests/test_core.py::TestSession::test_undo_max PASSED  [ 25%]
cli_anything/fiji/tests/test_core.py::TestSession::test_session_status PASSED [ 26%]
cli_anything/fiji/tests/test_core.py::TestSession::test_save_session PASSED [ 27%]
cli_anything/fiji/tests/test_core.py::TestSession::test_list_history PASSED [ 29%]
cli_anything/fiji/tests/test_core.py::TestImage::test_add_image PASSED   [ 30%]
cli_anything/fiji/tests/test_core.py::TestImage::test_add_image_not_found PASSED [ 31%]
cli_anything/fiji/tests/test_core.py::TestImage::test_remove_image PASSED [ 33%]
cli_anything/fiji/tests/test_core.py::TestImage::test_remove_image_invalid PASSED [ 34%]
cli_anything/fiji/tests/test_core.py::TestImage::test_list_images PASSED [ 36%]
cli_anything/fiji/tests/test_core.py::TestProcessing::test_list_operations PASSED [ 37%]
cli_anything/fiji/tests/test_core.py::TestProcessing::test_list_operations_filtered PASSED [ 38%]
cli_anything/fiji/tests/test_core.py::TestProcessing::test_get_operation_info PASSED [ 40%]
cli_anything/fiji/tests/test_core.py::TestProcessing::test_get_operation_unknown PASSED [ 41%]
cli_anything/fiji/tests/test_core.py::TestProcessing::test_add_processing_step PASSED [ 43%]
cli_anything/fiji/tests/test_core.py::TestProcessing::test_add_step_with_params PASSED [ 44%]
cli_anything/fiji/tests/test_core.py::TestProcessing::test_remove_step PASSED [ 45%]
cli_anything/fiji/tests/test_core.py::TestProcessing::test_list_processing_log PASSED [ 47%]
cli_anything/fiji/tests/test_core.py::TestProcessing::test_build_macro PASSED [ 48%]
cli_anything/fiji/tests/test_core.py::TestROI::test_add_rectangle_roi PASSED [ 50%]
cli_anything/fiji/tests/test_core.py::TestROI::test_add_oval_roi PASSED  [ 51%]
cli_anything/fiji/tests/test_core.py::TestROI::test_add_line_roi PASSED  [ 52%]
cli_anything/fiji/tests/test_core.py::TestROI::test_add_point_roi PASSED [ 54%]
cli_anything/fiji/tests/test_core.py::TestROI::test_add_roi_missing_params PASSED [ 55%]
cli_anything/fiji/tests/test_core.py::TestROI::test_remove_roi PASSED    [ 56%]
cli_anything/fiji/tests/test_core.py::TestROI::test_list_rois PASSED     [ 58%]
cli_anything/fiji/tests/test_core.py::TestROI::test_build_roi_macro PASSED [ 59%]
cli_anything/fiji/tests/test_core.py::TestMeasure::test_list_measurement_types PASSED [ 61%]
cli_anything/fiji/tests/test_core.py::TestMeasure::test_add_measurement_config PASSED [ 62%]
cli_anything/fiji/tests/test_core.py::TestMeasure::test_add_measurement_config_invalid PASSED [ 63%]
cli_anything/fiji/tests/test_core.py::TestMeasure::test_add_measurement_result PASSED [ 65%]
cli_anything/fiji/tests/test_core.py::TestMeasure::test_list_measurements PASSED [ 66%]
cli_anything/fiji/tests/test_core.py::TestMeasure::test_clear_measurements PASSED [ 68%]
cli_anything/fiji/tests/test_core.py::TestMeasure::test_build_analysis_macro PASSED [ 69%]
cli_anything/fiji/tests/test_core.py::TestMacro::test_add_macro PASSED   [ 70%]
cli_anything/fiji/tests/test_core.py::TestMacro::test_remove_macro PASSED [ 72%]
cli_anything/fiji/tests/test_core.py::TestMacro::test_list_macros PASSED [ 73%]
cli_anything/fiji/tests/test_core.py::TestMacro::test_get_macro PASSED   [ 75%]
cli_anything/fiji/tests/test_core.py::TestMacro::test_build_batch_macro PASSED [ 76%]
cli_anything/fiji/tests/test_full_e2e.py::TestFijiBackend::test_find_fiji PASSED [ 77%]
cli_anything/fiji/tests/test_full_e2e.py::TestFijiBackend::test_get_version PASSED [ 79%]
cli_anything/fiji/tests/test_full_e2e.py::TestFijiBackend::test_create_test_image PASSED [ 80%]
cli_anything/fiji/tests/test_full_e2e.py::TestFijiBackend::test_process_image_blur PASSED [ 81%]
cli_anything/fiji/tests/test_full_e2e.py::TestFijiBackend::test_process_image_threshold PASSED [ 83%]
cli_anything/fiji/tests/test_full_e2e.py::TestFijiBackend::test_run_macro_print PASSED [ 84%]
cli_anything/fiji/tests/test_full_e2e.py::TestFullPipeline::test_full_pipeline_blur_export PASSED [ 86%]
cli_anything/fiji/tests/test_full_e2e.py::TestFullPipeline::test_full_pipeline_png_export PASSED [ 87%]
cli_anything/fiji/tests/test_full_e2e.py::TestCLISubprocess::test_help PASSED [ 88%]
cli_anything/fiji/tests/test_full_e2e.py::TestCLISubprocess::test_json_project_new PASSED [ 90%]
cli_anything/fiji/tests/test_full_e2e.py::TestCLISubprocess::test_project_roundtrip PASSED [ 91%]
cli_anything/fiji/tests/test_full_e2e.py::TestCLISubprocess::test_process_pipeline PASSED [ 93%]
cli_anything/fiji/tests/test_full_e2e.py::TestCLISubprocess::test_roi_management PASSED [ 94%]
cli_anything/fiji/tests/test_full_e2e.py::TestCLISubprocess::test_backend_find PASSED [ 95%]
cli_anything/fiji/tests/test_full_e2e.py::TestCLISubprocess::test_list_operations PASSED [ 97%]
cli_anything/fiji/tests/test_full_e2e.py::TestCLISubprocess::test_export_presets PASSED [ 98%]
cli_anything/fiji/tests/test_full_e2e.py::TestCLISubprocess::test_full_workflow_with_fiji PASSED [100%]

======================== 72 passed in 119.24s (0:01:59) ========================
```

### Summary Statistics

- **Total tests**: 72
- **Passed**: 72 (100%)
- **Failed**: 0
- **Execution time**: 119.24s (~2 minutes)
- **Unit tests**: 55 (test_core.py)
- **E2E tests**: 17 (test_full_e2e.py)
- **Subprocess tests**: 10 (using installed `cli-anything-fiji` command)
- **Real Fiji invocations**: 7 tests (create image, blur, threshold, pipeline blur, pipeline PNG, macro print, full workflow)

### Coverage Notes

- All core modules fully covered (project, session, image, processing, roi, measure, macro)
- E2E tests verify real TIFF and PNG output with magic byte validation
- Subprocess tests use `_resolve_cli()` and confirm installed command path
- Full workflow test: create project → add image → process → export → verify output format
- Fiji headless mode confirmed working with `setBatchMode(true)` and `System.exit(0)` for clean shutdown
