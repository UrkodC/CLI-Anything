# cli-anything-r

A stateful CLI for R — statistical computing and data visualization from the command line.

## Prerequisites

- **R** (>= 4.0): https://www.r-project.org/
- **R packages**: `jsonlite`, `ggplot2` (install with `install.packages(c("jsonlite", "ggplot2"))`)
- **Python** >= 3.10

## Installation

```bash
cd r/agent-harness
pip install -e .
```

Verify:
```bash
cli-anything-r --help
cli-anything-r --json backend version
```

## Quick Start

```bash
# Create a project
cli-anything-r project new -n "iris_analysis" -o iris.r-cli.json

# Load a built-in dataset
cli-anything-r --project iris.r-cli.json data load iris --type builtin

# Inspect the data
cli-anything-r --project iris.r-cli.json data inspect 0

# Add a scatter plot
cli-anything-r --project iris.r-cli.json plot scatter 0 --x Sepal.Length --y Sepal.Width --color Species

# Render the plot
cli-anything-r --project iris.r-cli.json export render-plot 0 scatter.png

# Run a statistical analysis
cli-anything-r --project iris.r-cli.json analysis add ttest 0 --formula "Sepal.Length ~ Species"
cli-anything-r --project iris.r-cli.json analysis run 0
```

## JSON Output Mode

Add `--json` for machine-readable output:
```bash
cli-anything-r --json backend version
cli-anything-r --json --project iris.r-cli.json data list
```

## Interactive REPL

```bash
cli-anything-r
```

## Command Groups

| Group | Commands | Purpose |
|-------|----------|---------|
| project | new, open, save, info, profiles, json | Project management |
| data | load, list, inspect, summary, head, filter, transform, remove | Dataset operations |
| analysis | list-types, info, add, remove, log, run | Statistical analysis |
| plot | scatter, bar, histogram, boxplot, heatmap, line, list, remove, customize | Visualization |
| script | add, list, run, remove | Custom R scripts |
| export | render-plot, export-data | Output generation |
| backend | version, find, run-expr, run-script, install-pkg, list-pkgs | Direct R access |
| session | status, undo, redo, history, save | State management |

## Running Tests

```bash
pytest cli_anything/r/tests/ -v -s
```
