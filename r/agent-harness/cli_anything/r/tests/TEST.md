# R CLI — Test Plan

## Test Inventory Plan

| File | Type | Planned Tests |
|------|------|---------------|
| test_core.py | Unit tests | ~39 |
| test_full_e2e.py | E2E tests | ~12 |

## Unit Test Plan (test_core.py)

### TestProject (9 tests)

| # | Test | Description |
|---|------|-------------|
| 1 | test_create | Create project with default settings |
| 2 | test_create_with_name | Create project with custom name |
| 3 | test_create_with_profile | Create project with a named profile |
| 4 | test_invalid_profile | Reject unknown profile name |
| 5 | test_save_open_roundtrip | Save project then reopen, verify state preserved |
| 6 | test_open_nonexistent | Error on missing project file |
| 7 | test_open_invalid | Error on malformed project JSON |
| 8 | test_project_info | Display project metadata |
| 9 | test_list_profiles | List all available profiles |

### TestSession (8 tests)

| # | Test | Description |
|---|------|-------------|
| 1 | test_initial_state | Session starts with empty history |
| 2 | test_set_project | Attach project to session |
| 3 | test_no_project_error | Error when operating without project |
| 4 | test_snapshot_undo | Snapshot state then undo restores previous |
| 5 | test_redo | Redo after undo restores forward state |
| 6 | test_max_undo | Undo beyond history raises error |
| 7 | test_save_session | Persist session to disk |
| 8 | test_list_history | List undo/redo history entries |

### TestData (9 tests)

| # | Test | Description |
|---|------|-------------|
| 1 | test_add_builtin | Add a built-in dataset (e.g., mtcars, iris) |
| 2 | test_add_file | Add dataset from CSV file path |
| 3 | test_add_nonexistent | Error on missing file |
| 4 | test_remove | Remove dataset by name |
| 5 | test_list | List all loaded datasets |
| 6 | test_format_detection | Auto-detect CSV vs TSV vs Excel |
| 7 | test_build_load_code_builtin | Generate R code for built-in dataset |
| 8 | test_build_load_code_csv | Generate R code for CSV file load |
| 9 | test_filter | Apply filter expression to dataset |

### TestAnalysis (7 tests)

| # | Test | Description |
|---|------|-------------|
| 1 | test_list | List all available analyses |
| 2 | test_list_by_category | Filter analyses by category (test, regression, multivariate) |
| 3 | test_get_info | Get details for a specific analysis |
| 4 | test_get_invalid | Error on unknown analysis name |
| 5 | test_add | Add analysis to project |
| 6 | test_build_script | Generate R script for configured analysis |
| 7 | test_remove | Remove analysis from project |

### TestPlot (7 tests)

| # | Test | Description |
|---|------|-------------|
| 1 | test_list_types | List all available plot types |
| 2 | test_add | Add plot with required aesthetics |
| 3 | test_missing_aes | Error when required aesthetic mapping is missing |
| 4 | test_invalid_type | Error on unknown plot type |
| 5 | test_customize | Apply title, theme, labels, dimensions |
| 6 | test_build_script | Generate ggplot2 R script for configured plot |
| 7 | test_remove | Remove plot from project |

### TestScript (5 tests)

| # | Test | Description |
|---|------|-------------|
| 1 | test_add | Add custom R script to project |
| 2 | test_list | List all stored scripts |
| 3 | test_get | Retrieve script content by name |
| 4 | test_remove | Remove script by name |
| 5 | test_remove_invalid | Error on removing nonexistent script |

## E2E Test Plan (test_full_e2e.py)

### TestRBackend (4 tests)

| # | Test | Description |
|---|------|-------------|
| 1 | test_find_r | Locate Rscript executable on system |
| 2 | test_get_version | Query R version string |
| 3 | test_run_expression | Evaluate inline R expression and capture output |
| 4 | test_run_script | Execute .R script file and capture output |

### TestFullPipeline (3 tests)

| # | Test | Description |
|---|------|-------------|
| 1 | test_create_inspect_builtin | Create project, add builtin dataset, inspect summary |
| 2 | test_regression_pipeline | Load data, run linear regression, verify coefficients in output |
| 3 | test_plot_render_png | Add scatter plot, render to PNG, verify file exists and is valid PNG |

### TestCLISubprocess (5 tests)

| # | Test | Description |
|---|------|-------------|
| 1 | test_help | Run `r-cli --help` and verify exit code 0 |
| 2 | test_project_new_json | Run `r-cli project new` and verify JSON output |
| 3 | test_backend_version | Run `r-cli backend version` and verify version string |
| 4 | test_backend_run_expr | Run `r-cli backend run-expr "1+1"` and verify result |
| 5 | test_full_workflow | Create project, load dataset, add plot, render, verify PNG output |

## Realistic Workflow Scenarios

### 1. Data Exploration

```
Load CSV → inspect → summary → head → filter → export
```

User loads a CSV file, views column types and dimensions, computes summary
statistics, previews the first rows, applies a filter condition, and exports the
filtered result to a new CSV.

### 2. Statistical Analysis

```
Load data → run t-test → run regression → export results
```

User loads a dataset, performs a two-sample t-test between groups, fits a linear
regression model, and exports the test statistics and regression coefficients as
JSON.

### 3. Visualization Pipeline

```
Load data → add scatter plot → customize (title, theme) → render PNG/PDF
```

User loads a dataset, creates a ggplot2 scatter plot with x/y aesthetic mappings,
sets a title and applies a minimal theme, then renders the plot to PNG and PDF
output files.

### 4. Full Research Workflow

```
New project → load dataset → run PCA → add scatter plot of components → render → export data
```

User creates a new project, loads a multivariate dataset, runs PCA analysis,
creates a scatter plot of PC1 vs PC2 colored by group, renders the visualization,
and exports the PCA-transformed data to CSV.

## Test Results

```
============================= test session starts ==============================
platform darwin -- Python 3.12.2, pytest-9.0.2, pluggy-1.5.0

cli_anything/r/tests/test_core.py::TestProject::test_create_project PASSED
cli_anything/r/tests/test_core.py::TestProject::test_create_project_with_name PASSED
cli_anything/r/tests/test_core.py::TestProject::test_create_project_with_profile PASSED
cli_anything/r/tests/test_core.py::TestProject::test_create_project_invalid_profile PASSED
cli_anything/r/tests/test_core.py::TestProject::test_save_and_open PASSED
cli_anything/r/tests/test_core.py::TestProject::test_open_nonexistent PASSED
cli_anything/r/tests/test_core.py::TestProject::test_open_invalid PASSED
cli_anything/r/tests/test_core.py::TestProject::test_project_info PASSED
cli_anything/r/tests/test_core.py::TestProject::test_list_profiles PASSED
cli_anything/r/tests/test_core.py::TestSession::test_initial_state PASSED
cli_anything/r/tests/test_core.py::TestSession::test_set_project PASSED
cli_anything/r/tests/test_core.py::TestSession::test_get_project_no_project PASSED
cli_anything/r/tests/test_core.py::TestSession::test_snapshot_undo_redo PASSED
cli_anything/r/tests/test_core.py::TestSession::test_redo PASSED
cli_anything/r/tests/test_core.py::TestSession::test_max_undo PASSED
cli_anything/r/tests/test_core.py::TestSession::test_save_session PASSED
cli_anything/r/tests/test_core.py::TestSession::test_list_history PASSED
cli_anything/r/tests/test_core.py::TestData::test_add_dataset_builtin PASSED
cli_anything/r/tests/test_core.py::TestData::test_add_dataset_file PASSED
cli_anything/r/tests/test_core.py::TestData::test_add_nonexistent_file PASSED
cli_anything/r/tests/test_core.py::TestData::test_remove_dataset PASSED
cli_anything/r/tests/test_core.py::TestData::test_list_datasets PASSED
cli_anything/r/tests/test_core.py::TestData::test_format_detection PASSED
cli_anything/r/tests/test_core.py::TestData::test_build_load_code_builtin PASSED
cli_anything/r/tests/test_core.py::TestData::test_build_load_code_csv PASSED
cli_anything/r/tests/test_core.py::TestData::test_filter_dataset PASSED
cli_anything/r/tests/test_core.py::TestAnalysis::test_list_analyses PASSED
cli_anything/r/tests/test_core.py::TestAnalysis::test_list_analyses_by_category PASSED
cli_anything/r/tests/test_core.py::TestAnalysis::test_get_analysis_info PASSED
cli_anything/r/tests/test_core.py::TestAnalysis::test_get_analysis_info_invalid PASSED
cli_anything/r/tests/test_core.py::TestAnalysis::test_add_analysis PASSED
cli_anything/r/tests/test_core.py::TestAnalysis::test_build_analysis_script PASSED
cli_anything/r/tests/test_core.py::TestAnalysis::test_remove_analysis PASSED
cli_anything/r/tests/test_core.py::TestPlot::test_list_plot_types PASSED
cli_anything/r/tests/test_core.py::TestPlot::test_add_plot PASSED
cli_anything/r/tests/test_core.py::TestPlot::test_add_plot_missing_aes PASSED
cli_anything/r/tests/test_core.py::TestPlot::test_add_plot_invalid_type PASSED
cli_anything/r/tests/test_core.py::TestPlot::test_customize_plot PASSED
cli_anything/r/tests/test_core.py::TestPlot::test_build_plot_script PASSED
cli_anything/r/tests/test_core.py::TestPlot::test_remove_plot PASSED
cli_anything/r/tests/test_core.py::TestScript::test_add_script PASSED
cli_anything/r/tests/test_core.py::TestScript::test_list_scripts PASSED
cli_anything/r/tests/test_core.py::TestScript::test_get_script PASSED
cli_anything/r/tests/test_core.py::TestScript::test_remove_script PASSED
cli_anything/r/tests/test_core.py::TestScript::test_remove_invalid_index PASSED
cli_anything/r/tests/test_full_e2e.py::TestRBackend::test_find_r PASSED
cli_anything/r/tests/test_full_e2e.py::TestRBackend::test_get_version PASSED
cli_anything/r/tests/test_full_e2e.py::TestRBackend::test_run_expression PASSED
cli_anything/r/tests/test_full_e2e.py::TestRBackend::test_run_script PASSED
cli_anything/r/tests/test_full_e2e.py::TestFullPipeline::test_create_and_inspect_builtin PASSED
cli_anything/r/tests/test_full_e2e.py::TestFullPipeline::test_regression_pipeline PASSED
cli_anything/r/tests/test_full_e2e.py::TestFullPipeline::test_plot_render PASSED
cli_anything/r/tests/test_full_e2e.py::TestCLISubprocess::test_help PASSED
cli_anything/r/tests/test_full_e2e.py::TestCLISubprocess::test_project_new_json PASSED
cli_anything/r/tests/test_full_e2e.py::TestCLISubprocess::test_backend_version PASSED
cli_anything/r/tests/test_full_e2e.py::TestCLISubprocess::test_backend_run_expr PASSED
cli_anything/r/tests/test_full_e2e.py::TestCLISubprocess::test_full_workflow PASSED

============================== 57 passed in 6.32s ==============================
```

### Summary

| Metric | Value |
|--------|-------|
| Total tests | 57 |
| Passed | 57 |
| Failed | 0 |
| Pass rate | 100% |
| Execution time | 6.32s |

### Coverage Notes

- Unit tests cover all core modules with synthetic data (no R required)
- E2E tests verify real R execution, JSON parsing, and PNG output
- CLI subprocess tests verify the installed `cli-anything-r` command works end-to-end
- Full workflow test covers: project creation → data loading → plot creation → render → output verification
