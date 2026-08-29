---
name: >-
  cli-anything-pymol
description: >-
  Command-line interface for PyMOL - A stateful CLI for molecular visualization, structure management, and publication-quality rendering
---

# cli-anything-pymol

A stateful command-line interface for PyMOL molecular visualization. Load structures, apply representations and colors, configure views, add labels, and render publication-quality images from the command line.

## Installation

This CLI is installed as part of the cli-anything-pymol package:

```bash
pip install cli-anything-pymol
```

**Prerequisites:**
- Python 3.10+
- PyMOL must be installed on your system

## Usage

### Basic Commands

```bash
# Show help
cli-anything-pymol --help

# Start interactive REPL mode
cli-anything-pymol

# Create a new project
cli-anything-pymol project new -o project.json

# Run with JSON output (for agent consumption)
cli-anything-pymol --json project info -p project.json
```

### REPL Mode

When invoked without a subcommand, the CLI enters an interactive REPL session:

```bash
cli-anything-pymol
# Enter commands interactively with tab-completion and history
```

## Command Groups

### Project

Project and session management commands.

| Command | Description |
|---------|-------------|
| `new` | Create a new session with optional profile (presentation, publication, poster, web, etc.) |
| `open` | Open an existing .pymol-cli.json project file |
| `save` | Save the current project to disk |
| `info` | Display project summary (structure counts, render settings) |
| `profiles` | List available session profiles with resolutions |
| `json` | Print raw project JSON |

### Structure

Molecular structure management commands.

| Command | Description |
|---------|-------------|
| `load` | Load a structure from file or PDB ID (supports pdb, cif, sdf, mol2, xyz, pdbqt, mae, pse) |
| `remove` | Remove a structure and its associated representations, colors, labels |
| `rename` | Rename a structure's object name (updates all references) |
| `list` | List all loaded structures with metadata |
| `get` | Get detailed information about a specific structure |
| `formats` | List supported file formats |

### Selection

Named atom selection management commands.

| Command | Description |
|---------|-------------|
| `create` | Create a named selection (e.g., "chain A", "resi 100-150", "polymer.protein") |
| `remove` | Remove a selection by index |
| `update` | Update selection expression or enabled state |
| `list` | List all named selections |
| `get` | Get a specific selection by index |
| `macros` | List predefined selection macros (protein, nucleic, water, ligand, backbone, etc.) |

### Representation

Visual representation management commands.

| Command | Description |
|---------|-------------|
| `show` | Show a representation (cartoon, sticks, spheres, surface, mesh, lines, ribbon, dots, nb_spheres) |
| `hide` | Hide representation(s) on a target |
| `remove` | Remove a representation by index |
| `set` | Set a representation setting (e.g., stick_radius, cartoon_transparency) |
| `list` | List all representations |
| `available` | List available representation types (backbone, atomic, surface categories) |
| `info` | Show detailed settings for a representation type |

### Color

Color management commands.

| Command | Description |
|---------|-------------|
| `apply` | Apply color by name, RGB values, or color scheme |
| `remove` | Remove a color entry by index |
| `list` | List all color entries |
| `named` | List all 40+ available named colors |
| `schemes` | List color schemes (by_element, by_chain, by_ss, by_bfactor, rainbow, spectrum, etc.) |

### View

Camera and view management commands.

| Command | Description |
|---------|-------------|
| `set` | Set view parameters (preset, zoom, FOV, position) |
| `info` | Display current view settings |
| `presets` | List view presets (front, back, top, bottom, left, right) |
| `setting` | Set a global PyMOL setting (bg_color, depth_cue, fog, antialias, ray_trace_mode, etc.) |
| `settings` | List all current global settings |

### Label

Label management commands.

| Command | Description |
|---------|-------------|
| `add` | Add labels with format presets (residue, atom, chain_residue, element, bfactor, one_letter) |
| `remove` | Remove a label by index |
| `clear` | Clear all labels or those on a specific target |
| `list` | List all labels |
| `formats` | List available label format presets |

### Render

Render settings and output commands.

| Command | Description |
|---------|-------------|
| `settings` | Configure render parameters (resolution, ray tracing, format, DPI, presets) |
| `info` | Display current render settings |
| `presets` | List render presets (quick_preview, standard, high_quality, poster, presentation, etc.) |
| `execute` | Generate PyMOL script (.pml) and render |
| `script` | Print the complete PyMOL render script to stdout |

### Session

Session management commands.

| Command | Description |
|---------|-------------|
| `status` | Show session state (project loaded, modified, undo/redo counts) |
| `undo` | Undo the last operation |
| `redo` | Redo the last undone operation |
| `history` | Show undo history with timestamps |

## Examples

### Load and Visualize a Protein

Load a PDB structure, apply cartoon representation, and color by secondary structure.

```bash
cli-anything-pymol --json project new -o proj.json
cli-anything-pymol --json --project proj.json structure load 1ubq
cli-anything-pymol --json --project proj.json representation show cartoon -t 1ubq
cli-anything-pymol --json --project proj.json color apply -t 1ubq -s by_ss
cli-anything-pymol --json --project proj.json render execute output.png
```

### Highlight a Binding Site

Create selections and show specific representations for a ligand binding site.

```bash
cli-anything-pymol --json --project proj.json selection create protein "polymer.protein"
cli-anything-pymol --json --project proj.json selection create ligand "organic"
cli-anything-pymol --json --project proj.json representation show cartoon -t protein
cli-anything-pymol --json --project proj.json representation show sticks -t ligand
cli-anything-pymol --json --project proj.json color apply -t ligand -s by_element
```

### Publication-Quality Render

Configure high-quality rendering with ray tracing.

```bash
cli-anything-pymol --json --project proj.json render settings --preset high_quality --transparent
cli-anything-pymol --json --project proj.json view set --preset front --zoom 1.5
cli-anything-pymol --json --project proj.json render execute figure.png
```

### Add Labels to Key Residues

Label active site residues for a figure.

```bash
cli-anything-pymol --json --project proj.json label add "resi 48+51+63" --format residue --size 16 --color 1.0,1.0,1.0
```

## Representation Types

| Type | Category | Description |
|------|----------|-------------|
| cartoon | backbone | Helices, sheets, loops with configurable geometry |
| sticks | atomic | Ball-and-stick bonds |
| spheres | atomic | Van der Waals space-filling |
| surface | surface | Molecular surface with quality/solvent settings |
| mesh | surface | Mesh visualization |
| lines | atomic | Wire-frame bonds |
| ribbon | backbone | Continuous backbone ribbon |
| dots | surface | Dot surface with density control |
| nb_spheres | atomic | Non-bonded sphere representation |

## Color Schemes

| Scheme | Description |
|--------|-------------|
| by_element | CPK coloring |
| by_chain | Each chain a different color |
| by_ss | Helix=red, sheet=yellow, loop=green |
| by_bfactor | Spectrum by temperature factor |
| by_residue_type | Hydrophobic/polar/charged |
| rainbow | N-to-C gradient |
| chainbow | Rainbow per chain |
| spectrum_blue_red | Blue-to-red spectrum |

## Render Presets

| Preset | Resolution | Ray Tracing |
|--------|-----------|-------------|
| quick_preview | 800x600 | No |
| standard | 1920x1080 | Yes |
| high_quality | 3000x3000 | Yes |
| poster | 4000x3000 | Yes |
| web_thumbnail | 400x400 | No |
| presentation | 1920x1080 | Yes |
| transparent | 1920x1080 | Yes (transparent bg) |

## State Management

The CLI maintains session state with:

- **Undo/Redo**: Up to 50 levels of history
- **Project persistence**: Save/load project state as JSON
- **Session tracking**: Track modifications and changes

## Output Formats

All commands support dual output modes:

- **Human-readable** (default): Tables, colors, formatted text
- **Machine-readable** (`--json` flag): Structured JSON for agent consumption

```bash
# Human output
cli-anything-pymol project info -p project.json

# JSON output for agents
cli-anything-pymol --json project info -p project.json
```

## For AI Agents

When using this CLI programmatically:

1. **Always use `--json` flag** for parseable output
2. **Check return codes** - 0 for success, non-zero for errors
3. **Parse stderr** for error messages on failure
4. **Use absolute paths** for all file operations
5. **Verify outputs exist** after export operations

## More Information

- Full documentation: See README.md in the package
- Test coverage: See TEST.md in the package
- Methodology: See HARNESS.md in the cli-anything-plugin

## Version

1.0.0
