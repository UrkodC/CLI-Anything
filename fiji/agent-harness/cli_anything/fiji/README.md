# cli-anything-fiji

A stateful command-line interface for **Fiji/ImageJ** — the standard platform for scientific image analysis.

## Prerequisites

- **Python 3.10+**
- **Fiji** installed ([https://fiji.sc/](https://fiji.sc/))
  - macOS: Download and copy `Fiji.app` to `/Applications/`
  - Linux: Extract to `/opt/fiji/` or `~/Fiji.app/`
  - Windows: Extract to `C:\Fiji.app\`

## Installation

```bash
cd fiji/agent-harness
pip install -e .
```

Verify:
```bash
cli-anything-fiji --help
```

## Quick Start

### One-shot commands

```bash
# Create a new project
cli-anything-fiji project new -n "experiment_001" -o project.json

# Add an image
cli-anything-fiji --project project.json image add micrograph.tif

# Add processing steps
cli-anything-fiji --project project.json process add gaussian_blur -p sigma=2.0
cli-anything-fiji --project project.json process add auto_threshold -p method=Otsu

# Add ROI for measurement
cli-anything-fiji --project project.json roi add rectangle --x 100 --y 100 -w 200 -h 200

# Export processed image
cli-anything-fiji --project project.json export render output.tif -p tiff --overwrite

# JSON output for agents
cli-anything-fiji --json project info
```

### Interactive REPL

```bash
cli-anything-fiji
```

### Batch processing

```bash
# Generate a batch macro
cli-anything-fiji --project project.json macro batch -i /input/dir -o /output/dir --pattern ".*\\.tif"
```

## Command Groups

| Group       | Description                                      |
|------------|--------------------------------------------------|
| `project`  | Create, open, save, inspect projects             |
| `image`    | Add, remove, list image files                    |
| `process`  | Build processing pipeline (blur, threshold, etc.)|
| `roi`      | Manage regions of interest                       |
| `measure`  | Configure and run measurements                   |
| `macro`    | Store and execute custom ImageJ macros           |
| `export`   | Render processed images via Fiji                 |
| `channel`  | Merge, split channels; list LUTs                 |
| `figure`   | Assemble multi-panel figures, journal presets     |
| `backend`  | Direct Fiji interaction (version, run macro)      |
| `session`  | Undo/redo, history, status                       |

## Processing Operations

Categories: `adjust`, `filter`, `morphology`, `convert`, `spatial`, `analysis`, `stack`

New in adjust: `set_scale`, `add_scale_bar`, `apply_lut`, `set_display_range`, `invert_lut`, `add_calibration_bar`

```bash
cli-anything-fiji process list-ops               # all operations
cli-anything-fiji process list-ops -c filter     # only filters
cli-anything-fiji process info gaussian_blur     # details + params
```

## How It Works

1. **Project files** (`.fiji-cli.json`) store images, processing pipeline, ROIs, and measurements
2. **Processing pipeline** builds up a sequence of ImageJ macro commands
3. **Export** generates a complete macro, runs it through Fiji headless, and saves the output
4. **The real Fiji application** does all actual image processing — this CLI is an interface, not a reimplementation

## Running Tests

```bash
cd fiji/agent-harness
pip install -e ".[dev]"
python3 -m pytest cli_anything/fiji/tests/ -v -s
```

Force installed CLI:
```bash
CLI_ANYTHING_FORCE_INSTALLED=1 python3 -m pytest cli_anything/fiji/tests/ -v -s
```
