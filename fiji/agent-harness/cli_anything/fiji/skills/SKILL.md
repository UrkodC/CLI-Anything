---
name: >-
  cli-anything-fiji
description: >-
  Command-line interface for Fiji/ImageJ - A stateful CLI for scientific image processing, microscopy analysis, and figure assembly
---

# cli-anything-fiji

A stateful command-line interface for Fiji/ImageJ headless image processing. Build processing pipelines, manage ROIs, run measurements, and assemble publication-ready figures from the command line.

## Installation

This CLI is installed as part of the cli-anything-fiji package:

```bash
pip install cli-anything-fiji
```

**Prerequisites:**
- Python 3.10+
- Fiji/ImageJ must be installed on your system with headless mode support
- Java runtime (bundled with Fiji)

## Usage

### Basic Commands

```bash
# Show help
cli-anything-fiji --help

# Start interactive REPL mode
cli-anything-fiji

# Create a new project
cli-anything-fiji project new -o project.json

# Run with JSON output (for agent consumption)
cli-anything-fiji --json project info -p project.json
```

### REPL Mode

When invoked without a subcommand, the CLI enters an interactive REPL session:

```bash
cli-anything-fiji
# Enter commands interactively with tab-completion and history
```

## Command Groups

### Project

Project management commands.

| Command | Description |
|---------|-------------|
| `new` | Create a new project with image dimensions and metadata |
| `open` | Open an existing .fiji-cli.json project file |
| `save` | Save current project to disk |
| `info` | Display project information |
| `profiles` | List available image profiles (confocal, widefield, hyperstack, etc.) |
| `json` | Print raw project JSON |

### Image

Image file management commands.

| Command | Description |
|---------|-------------|
| `add` | Add an image file reference to the project |
| `remove` | Remove an image by index |
| `list` | List all images with metadata |
| `info` | Show detailed information about a specific image |

### Process

Image processing pipeline commands.

| Command | Description |
|---------|-------------|
| `list-ops` | List available operations (adjust, filter, morphology, convert, spatial, analysis, stack) |
| `info` | Show details about a specific operation |
| `add` | Add a processing step to the pipeline |
| `remove` | Remove a processing step by index |
| `log` | Display the complete processing pipeline |
| `macro` | Show the generated ImageJ macro for the pipeline |

### ROI

Region of interest management commands.

| Command | Description |
|---------|-------------|
| `add` | Add a ROI (rectangle, oval, line, polygon, point) |
| `remove` | Remove a ROI by index |
| `list` | List all ROIs with details |
| `macro` | Generate ImageJ macro code for a ROI |

### Channel

Multi-channel operations.

| Command | Description |
|---------|-------------|
| `luts` | List available lookup tables (Grays, Green, Magenta, Cyan, Fire, etc.) |
| `merge` | Merge multiple channel images into a composite |
| `split` | Split a composite image into individual channels |

### Measure

Measurement and analysis commands.

| Command | Description |
|---------|-------------|
| `types` | List available measurement types (area, mean, centroid, etc.) |
| `commands` | List available analysis commands (measure, analyze_particles, histogram, etc.) |
| `configure` | Configure measurement parameters and scale |
| `run` | Generate ImageJ macro for an analysis command |
| `results` | Display all measurement results |
| `clear` | Clear all measurement results |

### Macro

Custom ImageJ macro management.

| Command | Description |
|---------|-------------|
| `add` | Add a custom ImageJ macro |
| `add-file` | Load and add a macro from a file |
| `remove` | Remove a macro by index |
| `list` | List all stored macros |
| `show` | Display a macro's full code |
| `batch` | Generate a batch processing macro for a directory |

### Export

Render and export commands.

| Command | Description |
|---------|-------------|
| `presets` | List available export format presets |
| `preset-info` | Show details about a specific export preset |
| `render` | Render and save processed image through Fiji headless |

### Figure

Multi-panel figure assembly commands.

| Command | Description |
|---------|-------------|
| `presets` | List available journal figure presets (Nature, Science, Cell) |
| `preset-info` | Show details about a figure preset |
| `montage` | Assemble multi-panel figure from individual images |

### Session

Session management commands.

| Command | Description |
|---------|-------------|
| `status` | Show session status |
| `undo` | Undo the last operation |
| `redo` | Redo the last undone operation |
| `history` | Show undo history with timestamps |

### Backend

Direct Fiji interaction commands.

| Command | Description |
|---------|-------------|
| `version` | Display installed Fiji version |
| `find` | Show path to Fiji executable |
| `run-macro` | Execute raw ImageJ macro code directly |
| `run-script` | Execute a script file in Fiji |

## Examples

### Microscopy Image Processing

Open an image, apply processing, and export.

```bash
cli-anything-fiji --json project new -o proj.json
cli-anything-fiji --json --project proj.json image add micrograph.tif
cli-anything-fiji --json --project proj.json process add gaussian_blur -p sigma=2.0
cli-anything-fiji --json --project proj.json process add auto_threshold -p method=Otsu
cli-anything-fiji --json --project proj.json export render output.tif -i 0
```

### Multi-Channel Composite

Merge fluorescence channels into a composite image.

```bash
cli-anything-fiji --json --project proj.json channel merge dapi.tif gfp.tif -c Blue,Green -o composite.tif
```

### Publication Figure Assembly

Assemble panels into a journal-ready figure.

```bash
cli-anything-fiji --json --project proj.json figure montage panel_a.tif panel_b.tif panel_c.tif panel_d.tif -c 2 -r 2 --scale-bar 50 -o figure1.tif
```

### Batch Processing

Apply a macro to all images in a directory.

```bash
cli-anything-fiji --json --project proj.json macro add -c "run('Gaussian Blur...', 'sigma=2');" -n "blur"
cli-anything-fiji --json --project proj.json macro batch -i /path/to/input -o /path/to/output -m 0
```

## Processing Operations

| Category | Operations |
|----------|-----------|
| Adjust | brightness_contrast, threshold, auto_threshold, enhance_contrast, set_scale, add_scale_bar, apply_lut, set_display_range, invert_lut, add_calibration_bar |
| Filter | gaussian_blur, median, unsharp_mask, subtract_background |
| Morphology | erode, dilate, open, close, skeletonize, watershed, fill_holes |
| Convert | to_8bit, to_16bit, to_32bit, to_rgb |
| Spatial | scale, rotate, flip_horizontal, flip_vertical, crop |
| Analysis | find_edges |
| Stack | z_project_max, z_project_avg, z_project_sum, split_channels |

## Journal Figure Presets

| Preset | Width | DPI |
|--------|-------|-----|
| nature_single | 89 mm (1051 px) | 300 |
| nature_double | 183 mm (2161 px) | 300 |
| science_single | 55 mm (650 px) | 300 |
| science_double | 175 mm (2067 px) | 300 |
| cell_single | 85 mm (1004 px) | 300 |
| cell_double | 178 mm (2102 px) | 300 |

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
cli-anything-fiji project info --project project.json

# JSON output for agents
cli-anything-fiji --json project info --project project.json
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
