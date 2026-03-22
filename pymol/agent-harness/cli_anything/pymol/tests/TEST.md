# PyMOL CLI - Test Plan & Results

## Test Plan

### Unit Tests (`test_core.py`)
Synthetic data tests — no real structure files or PyMOL installation required.

| Module | Tests | Coverage |
|--------|-------|----------|
| Project | 15 | create, profiles, save/open, info, validation |
| Structure | 20 | load (PDB ID + file), remove, rename, properties, formats |
| Selection | 12 | create, remove, update, macros, validation |
| Representation | 20 | show, hide, remove, settings, registry, categories |
| Coloring | 14 | named colors, RGB, schemes, validation |
| View | 14 | presets, zoom, FOV, position, settings |
| Label | 16 | add, remove, clear, formats, validation |
| Render | 20 | settings, presets, formats, script generation, validation |
| Session | 20 | undo/redo, history, save, max_undo, modified flag |

### E2E Tests (`test_full_e2e.py`)
Full workflow tests — roundtrips, script generation, CLI subprocess.

| Category | Tests | Description |
|----------|-------|-------------|
| Project Lifecycle | 6 | Create-save-open roundtrips with structures/reps/colors |
| PML Script Gen | 12 | Script content validation for all features |
| Workflows | 5 | Protein viz, ligand binding, publication, multi-structure |
| CLI Subprocess | 12 | CLI help, project, representations, colors, error handling |
| Script Validity | 3 | Script structure verification |
| PyMOL Backend | 2 | Requires PyMOL installed |
| PyMOL Render E2E | 5 | Requires PyMOL installed |

## Test Results

```
cli_anything/pymol/tests/test_core.py: 223 passed in 0.30s
cli_anything/pymol/tests/test_full_e2e.py: 45 passed, 7 failed in 2.83s

Total: 268 passed, 7 failed (PyMOL-backend tests — require PyMOL installation)
```

### Failed Tests (Expected — require PyMOL installation)
- `TestPyMOLBackend::test_pymol_is_installed`
- `TestPyMOLBackend::test_pymol_version`
- `TestPyMOLRenderE2E::test_render_empty_scene`
- `TestPyMOLRenderE2E::test_render_fetched_structure`
- `TestPyMOLRenderE2E::test_render_with_representations_and_colors`
- `TestPyMOLRenderE2E::test_render_publication_quality`
- `TestPyMOLRenderScriptE2E::test_run_minimal_pml_script`

All 7 failures are expected — they require `pymol` to be installed.
Install with: `conda install -c conda-forge pymol-open-source`
