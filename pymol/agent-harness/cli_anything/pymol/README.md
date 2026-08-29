# PyMOL CLI - Agent Harness

A stateful command-line interface for molecular visualization, following the
cli-anything methodology. Uses a JSON session format with PyMOL script (.pml)
generation for rendering.

## Installation

```bash
# From the agent-harness directory:
pip install -e .

# Verify CLI is in PATH:
which cli-anything-pymol

# Dependencies (auto-installed):
# click, prompt-toolkit

# PyMOL is only needed for rendering the generated scripts:
# conda install -c conda-forge pymol-open-source
```

## Quick Start

```bash
# Create a new session
cli-anything-pymol project new --name "MySession" -o session.json

# Load a structure (PDB ID)
cli-anything-pymol --project session.json structure load 1abc

# Show representations
cli-anything-pymol --project session.json representation show cartoon --target 1abc
cli-anything-pymol --project session.json representation show sticks --target 1abc

# Color by chain
cli-anything-pymol --project session.json color apply --target 1abc --scheme by_chain

# Add labels
cli-anything-pymol --project session.json label add "resi 100 and name CA" --format residue

# Set render settings
cli-anything-pymol --project session.json render settings --preset publication

# Generate render script
cli-anything-pymol --project session.json render execute render.png --overwrite

# Execute with PyMOL (if installed)
pymol -cq /path/to/_pymol_render_script.pml
```

## JSON Output Mode

All commands support `--json` for machine-readable output:

```bash
cli-anything-pymol --json project new -o session.json
cli-anything-pymol --json --project session.json structure list
```

## Interactive REPL

```bash
cli-anything-pymol repl
# or with existing project:
cli-anything-pymol repl --project session.json
```

## Command Groups

### Project Management
```
project new      - Create a new session
project open     - Open an existing project file
project save     - Save the current project
project info     - Show project information
project profiles - List available session profiles
project json     - Print raw project JSON
```

### Structure Management
```
structure load    - Load a molecular structure (file path or PDB ID)
structure remove  - Remove a structure by index
structure rename  - Rename a structure
structure list    - List all structures
structure get     - Get detailed structure info
structure formats - List supported file formats
```

### Selection Management
```
selection create - Create a named atom selection
selection remove - Remove a selection by index
selection update - Update a selection expression or state
selection list   - List all selections
selection get    - Get a selection by index
selection macros - List available selection macros
```

### Representation Management
```
representation show      - Show a representation on a target
representation hide      - Hide representations on a target
representation remove    - Remove a representation by index
representation set       - Set a representation setting
representation list      - List representations
representation available - List available representation types
representation info      - Show details about a representation type
```

### Color Management
```
color apply  - Apply a color or color scheme to a target
color remove - Remove a color entry by index
color list   - List all color entries
color named  - List available named colors
color schemes - List available color schemes
```

### View/Camera Management
```
view set      - Set view/camera parameters
view info     - Show current view settings
view presets  - List available view presets
view setting  - Set a global setting
view settings - List all current settings
```

### Label Management
```
label add     - Add labels to atoms/residues
label remove  - Remove a label by index
label clear   - Clear all labels
label list    - List all labels
label formats - List available label format presets
```

### Render
```
render settings - Configure render settings
render info     - Show current render settings
render presets  - List available render presets
render execute  - Render the scene (generates PyMOL script)
render script   - Print the PyMOL render script to stdout
```

### Session
```
session status  - Show session status
session undo    - Undo the last operation
session redo    - Redo the last undone operation
session history - Show undo history
```

## Running Tests

```bash
# From the agent-harness directory:

# Run all tests
python3 -m pytest cli_anything/pymol/tests/ -v

# Run unit tests only
python3 -m pytest cli_anything/pymol/tests/test_core.py -v

# Run E2E tests only
python3 -m pytest cli_anything/pymol/tests/test_full_e2e.py -v
```

## Architecture

```
cli_anything/pymol/
├── __init__.py
├── __main__.py              # python3 -m cli_anything.pymol
├── pymol_cli.py             # Main CLI entry point (Click + REPL)
├── core/
│   ├── __init__.py
│   ├── project.py           # Project create/open/save/info
│   ├── structure.py         # Molecular structure management
│   ├── selection.py         # Named atom selections
│   ├── representation.py    # Visual representations
│   ├── coloring.py          # Color management
│   ├── view.py              # View/camera settings
│   ├── label.py             # Atom/residue labels
│   ├── render.py            # Render settings and export
│   └── session.py           # Stateful session, undo/redo
├── utils/
│   ├── __init__.py
│   ├── pymol_backend.py     # PyMOL executable detection and execution
│   ├── pml_gen.py           # PyMOL script (.pml) generation
│   └── repl_skin.py         # REPL terminal interface
└── tests/
    ├── __init__.py
    ├── TEST.md              # Test plan and results
    ├── test_core.py         # Unit tests (synthetic data)
    └── test_full_e2e.py     # E2E tests (roundtrips, script gen, CLI)
```

## JSON Project Format

```json
{
  "version": "1.0",
  "name": "session_name",
  "settings": {
    "bg_color": [0.0, 0.0, 0.0],
    "orthoscopic": true,
    "ray_trace_mode": 1,
    "antialias": 2
  },
  "structures": [
    {
      "name": "1abc",
      "object_name": "1abc",
      "source": "pdb",
      "pdb_id": "1ABC",
      "visible": true
    }
  ],
  "selections": [
    {
      "name": "active_site",
      "expression": "resi 100-150 and chain A",
      "enabled": true
    }
  ],
  "representations": [
    {
      "target": "1abc",
      "rep_type": "cartoon",
      "enabled": true,
      "settings": {}
    }
  ],
  "colors": [
    {
      "target": "1abc",
      "color_type": "scheme",
      "scheme": "by_chain"
    }
  ],
  "labels": [],
  "view": {
    "position": [0, 0, -50],
    "orientation": [[1,0,0],[0,1,0],[0,0,1]],
    "zoom": 1.0
  },
  "render": {
    "width": 1920,
    "height": 1080,
    "ray": true,
    "ray_trace_mode": 1,
    "output_format": "png",
    "dpi": 300
  },
  "metadata": {
    "created": "...",
    "modified": "...",
    "software": "pymol-cli 1.0"
  }
}
```

## Rendering

This CLI uses a JSON project format and generates PyMOL scripts (.pml) for
rendering. The workflow:

1. Edit the session using CLI commands (creates/modifies JSON)
2. Generate a .pml script with `render execute` or `render script`
3. Run the script with `pymol -cq script.pml`

The generated scripts reconstruct the entire visualization state in PyMOL and
render to an image file.

## Supported Representations

| Type | Category | Description |
|------|----------|-------------|
| cartoon | backbone | Cartoon backbone trace (helices, sheets, loops) |
| sticks | atomic | Ball-and-stick bonds |
| spheres | atomic | Space-filling van der Waals spheres |
| surface | surface | Molecular surface (Connolly/SES) |
| mesh | surface | Mesh surface |
| lines | atomic | Wire-frame bonds |
| ribbon | backbone | Ribbon backbone trace |
| dots | surface | Dot surface |
| nb_spheres | atomic | Non-bonded spheres (for ions, water) |

## Supported Color Schemes

| Scheme | Description |
|--------|-------------|
| by_element | Color by chemical element (CPK) |
| by_chain | Color each chain differently |
| by_ss | Color by secondary structure |
| by_bfactor | Color by B-factor as spectrum |
| rainbow | Rainbow N to C terminus |
| chainbow | Rainbow within each chain |
| by_residue_type | Color by residue type |
