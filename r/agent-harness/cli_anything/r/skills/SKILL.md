---
name: >-
  cli-anything-r
description: >-
  Command-line interface for R - A stateful CLI for data analysis, statistical testing, and ggplot2 visualization
---

# cli-anything-r

A stateful command-line interface for R statistical computing. Load datasets, run statistical analyses, create ggplot2 visualizations, and export results without opening RStudio.

## Installation

This CLI is installed as part of the cli-anything-r package:

```bash
pip install cli-anything-r
```

**Prerequisites:**
- Python 3.10+
- R (>= 4.0) with `Rscript` in PATH
- R packages: `jsonlite`, `ggplot2`

## Usage

### Basic Commands

```bash
# Show help
cli-anything-r --help

# Start interactive REPL mode
cli-anything-r

# Create a new project
cli-anything-r project new -o project.json

# Run with JSON output (for agent consumption)
cli-anything-r --json project info -p project.json
```

### REPL Mode

When invoked without a subcommand, the CLI enters an interactive REPL session:

```bash
cli-anything-r
# Enter commands interactively with tab-completion and history
```

## Command Groups

### Project

Project management commands.

| Command | Description |
|---------|-------------|
| `new` | Create a new R CLI project with optional profile |
| `open` | Open an existing .r-cli.json project file |
| `save` | Save current project to disk |
| `info` | Show project information |
| `profiles` | List available R profiles (basic_analysis, bioinformatics, machine_learning, time_series, tidyverse) |
| `json` | Print raw project JSON |

### Data

Dataset management commands.

| Command | Description |
|---------|-------------|
| `load` | Load a dataset from file or builtin (csv, tsv, xlsx, rds, rda, json, sav, dta) |
| `list` | List all datasets in the project |
| `inspect` | Inspect dataset structure (runs R) |
| `summary` | Show summary statistics |
| `head` | Show first N rows |
| `filter` | Create a filtered dataset from an expression |
| `transform` | Create a transformed dataset from an expression |
| `remove` | Remove a dataset by index |

### Analysis

Statistical analysis commands.

| Command | Description |
|---------|-------------|
| `list-types` | List available analysis types by category |
| `info` | Show details about an analysis type |
| `add` | Add an analysis to the project (with formula, variables, method) |
| `remove` | Remove an analysis by index |
| `log` | List recorded analyses |
| `run` | Run an analysis (executes R script) |

### Plot

ggplot2 visualization commands.

| Command | Description |
|---------|-------------|
| `scatter` | Create a scatter plot |
| `bar` | Create a bar chart |
| `histogram` | Create a histogram |
| `boxplot` | Create a box plot |
| `heatmap` | Create a heatmap |
| `line` | Create a line plot |
| `list` | List all plots |
| `remove` | Remove a plot by index |
| `customize` | Customize appearance (title, theme, facet, legend, coord_flip) |

### Script

Custom R script management.

| Command | Description |
|---------|-------------|
| `add` | Add a custom R script to the project |
| `list` | List all scripts |
| `run` | Run a script (executes via R backend) |
| `remove` | Remove a script by index |

### Export

Output generation commands.

| Command | Description |
|---------|-------------|
| `render-plot` | Render a plot to image (png, pdf, svg, jpeg, tiff) |
| `export-data` | Export a dataset to file (csv, tsv, rds, xlsx) |

### Backend

Direct R access commands.

| Command | Description |
|---------|-------------|
| `version` | Show R version |
| `find` | Show Rscript executable path |
| `run-expr` | Run an R expression directly |
| `run-script` | Run an R script file |
| `install-pkg` | Install an R package |
| `list-pkgs` | List installed R packages |

### Session

Session management commands.

| Command | Description |
|---------|-------------|
| `status` | Show session status |
| `undo` | Undo the last operation |
| `redo` | Redo the last undone operation |
| `history` | Show undo history |
| `save` | Save the current session |

## Examples

### Data Exploration

Load and explore a built-in dataset.

```bash
cli-anything-r --json project new -n "analysis" -o proj.json
cli-anything-r --json --project proj.json data load iris --type builtin
cli-anything-r --json --project proj.json data inspect 0
cli-anything-r --json --project proj.json data head 0 --rows 5
cli-anything-r --json --project proj.json data summary 0
```

### Statistical Analysis

Add and run a regression analysis.

```bash
cli-anything-r --json --project proj.json analysis add regression 0 --formula "Sepal.Length ~ Sepal.Width"
cli-anything-r --json --project proj.json analysis run 0
```

### Visualization

Create and render a scatter plot.

```bash
cli-anything-r --json --project proj.json plot scatter 0 --x Sepal.Length --y Sepal.Width --color Species
cli-anything-r --json --project proj.json plot customize 0 --title "Iris Scatter" --theme theme_minimal
cli-anything-r --json --project proj.json export render-plot 0 output.png --dpi 300
```

### Direct R Execution

Run R expressions or scripts directly.

```bash
cli-anything-r --json backend run-expr "summary(iris)"
cli-anything-r --json backend run-script analysis.R
```

## Analysis Types

| Type | Category | Description |
|------|----------|-------------|
| ttest | hypothesis | Two-sample or paired t-test |
| anova | hypothesis | One-way or multi-factor ANOVA |
| chi_squared | hypothesis | Chi-squared test |
| wilcoxon | hypothesis | Wilcoxon rank-sum test |
| correlation | descriptive | Correlation test |
| summary_stats | descriptive | Descriptive statistics |
| regression | modeling | Linear regression (lm) |
| pca | multivariate | Principal Component Analysis |

## Plot Types and Themes

**Plot types:** scatter, bar, histogram, boxplot, heatmap, line, density, violin

**Available themes:** theme_minimal, theme_bw, theme_classic, theme_dark, theme_light, theme_void, theme_gray, theme_linedraw

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
cli-anything-r project info -p project.json

# JSON output for agents
cli-anything-r --json project info -p project.json
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
