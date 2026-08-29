#!/usr/bin/env python3
"""R CLI — A stateful command-line interface for statistical computing.

This CLI provides data analysis, visualization, and scripting
operations using R as the backend engine.

Usage:
    # One-shot commands
    cli-anything-r project new -n "experiment_001"
    cli-anything-r data load mtcars --type builtin
    cli-anything-r analysis add ttest 0 --formula "mpg ~ am"
    cli-anything-r plot scatter 0 --x mpg --y hp

    # Interactive REPL
    cli-anything-r
"""

import sys
import os
import json
import click
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli_anything.r.core.session import Session
from cli_anything.r.core import project as proj_mod
from cli_anything.r.core import data as data_mod
from cli_anything.r.core import analysis as analysis_mod
from cli_anything.r.core import plot as plot_mod
from cli_anything.r.core import script as script_mod
from cli_anything.r.core import export as export_mod

# Global session state
_session: Optional[Session] = None
_json_output = False
_repl_mode = False


def get_session() -> Session:
    global _session
    if _session is None:
        _session = Session()
    return _session


def output(data, message: str = ""):
    if _json_output:
        click.echo(json.dumps(data, indent=2, default=str))
    else:
        if message:
            click.echo(message)
        if isinstance(data, dict):
            _print_dict(data)
        elif isinstance(data, list):
            _print_list(data)
        else:
            click.echo(str(data))


def _print_dict(d: dict, indent: int = 0):
    prefix = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            click.echo(f"{prefix}{k}:")
            _print_dict(v, indent + 1)
        elif isinstance(v, list):
            click.echo(f"{prefix}{k}:")
            _print_list(v, indent + 1)
        else:
            click.echo(f"{prefix}{k}: {v}")


def _print_list(items: list, indent: int = 0):
    prefix = "  " * indent
    for i, item in enumerate(items):
        if isinstance(item, dict):
            click.echo(f"{prefix}[{i}]")
            _print_dict(item, indent + 1)
        else:
            click.echo(f"{prefix}- {item}")


def _auto_save():
    """Auto-save project if a path is set (for CLI one-shot mode)."""
    if _repl_mode:
        return
    sess = get_session()
    if sess.has_project() and sess.project_path:
        try:
            sess.save_session()
        except Exception:
            pass


def handle_error(func):
    def wrapper(*args, **kwargs):
        try:
            result = func(*args, **kwargs)
            _auto_save()
            return result
        except FileNotFoundError as e:
            if _json_output:
                click.echo(json.dumps({"error": str(e), "type": "file_not_found"}))
            else:
                click.echo(f"Error: {e}", err=True)
            if not _repl_mode:
                sys.exit(1)
        except (ValueError, IndexError, RuntimeError) as e:
            if _json_output:
                click.echo(json.dumps({"error": str(e), "type": type(e).__name__}))
            else:
                click.echo(f"Error: {e}", err=True)
            if not _repl_mode:
                sys.exit(1)
        except FileExistsError as e:
            if _json_output:
                click.echo(json.dumps({"error": str(e), "type": "file_exists"}))
            else:
                click.echo(f"Error: {e}", err=True)
            if not _repl_mode:
                sys.exit(1)
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


# ── Main CLI Group ──────────────────────────────────────────────
@click.group(invoke_without_command=True)
@click.option("--json", "use_json", is_flag=True, help="Output as JSON")
@click.option("--project", "project_path", type=str, default=None,
              help="Path to .r-cli.json project file")
@click.pass_context
def cli(ctx, use_json, project_path):
    """R CLI — Statistical computing from the command line.

    Run without a subcommand to enter interactive REPL mode.
    """
    global _json_output
    _json_output = use_json

    if project_path:
        sess = get_session()
        if not sess.has_project():
            proj = proj_mod.open_project(project_path)
            sess.set_project(proj, project_path)

    if ctx.invoked_subcommand is None:
        ctx.invoke(repl, project_path=None)


# ── Project Commands ─────────────────────────────────────────────
@cli.group()
def project():
    """Project management commands."""
    pass


@project.command("new")
@click.option("--name", "-n", default="untitled", help="Project name")
@click.option("--profile", "-p", type=str, default=None, help="R profile")
@click.option("--output", "-o", "output_path", type=str, default=None, help="Save path")
@handle_error
def project_new(name, profile, output_path):
    """Create a new R CLI project."""
    proj = proj_mod.create_project(name=name, profile=profile)
    sess = get_session()
    sess.set_project(proj, output_path)
    if output_path:
        proj_mod.save_project(proj, output_path)
    output_data = proj_mod.get_project_info(proj)
    globals()["output"](output_data, f"Created project: {name}")


@project.command("open")
@click.argument("path")
@handle_error
def project_open(path):
    """Open an existing project."""
    proj = proj_mod.open_project(path)
    sess = get_session()
    sess.set_project(proj, path)
    info = proj_mod.get_project_info(proj)
    output(info, f"Opened: {path}")


@project.command("save")
@click.argument("path", required=False)
@handle_error
def project_save(path):
    """Save the current project."""
    sess = get_session()
    saved = sess.save_session(path)
    output({"saved": saved}, f"Saved to: {saved}")


@project.command("info")
@handle_error
def project_info():
    """Show project information."""
    sess = get_session()
    info = proj_mod.get_project_info(sess.get_project())
    output(info)


@project.command("profiles")
@handle_error
def project_profiles():
    """List available R profiles."""
    profiles = proj_mod.list_profiles()
    output(profiles, "Available profiles:")


@project.command("json")
@handle_error
def project_json():
    """Print raw project JSON."""
    sess = get_session()
    click.echo(json.dumps(sess.get_project(), indent=2, default=str))


# ── Data Commands ────────────────────────────────────────────────
@cli.group()
def data():
    """Dataset management commands."""
    pass


@data.command("load")
@click.argument("path_or_name")
@click.option("--name", "-n", default=None, help="Display name")
@click.option("--type", "-t", "dataset_type", default="auto",
              type=click.Choice(["auto", "builtin"]), help="Dataset type")
@handle_error
def data_load(path_or_name, name, dataset_type):
    """Load a dataset into the project."""
    sess = get_session()
    sess.snapshot(f"Load dataset: {path_or_name}")
    entry = data_mod.add_dataset(sess.get_project(), path_or_name,
                                 name=name, dataset_type=dataset_type)
    output(entry, f"Loaded dataset: {entry['name']}")


@data.command("list")
@handle_error
def data_list():
    """List all datasets in the project."""
    sess = get_session()
    datasets = data_mod.list_datasets(sess.get_project())
    output(datasets, "Datasets:")


@data.command("inspect")
@click.argument("index", type=int)
@handle_error
def data_inspect(index):
    """Inspect dataset structure (runs R to get column info)."""
    import tempfile
    sess = get_session()
    script_code = data_mod.build_inspect_script(sess.get_project(), index)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.R', delete=False) as f:
        f.write(script_code)
        tmp_path = f.name
    try:
        from cli_anything.r.utils.r_backend import run_script
        result = run_script(tmp_path)
        if result["returncode"] != 0:
            raise RuntimeError(f"R script failed: {result['stderr']}")
        parsed = json.loads(result["stdout"])
        output(parsed, "Dataset structure:")
    finally:
        os.unlink(tmp_path)


@data.command("summary")
@click.argument("index", type=int)
@handle_error
def data_summary(index):
    """Show summary statistics (runs R summary())."""
    import tempfile
    sess = get_session()
    script_code = data_mod.build_summary_script(sess.get_project(), index)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.R', delete=False) as f:
        f.write(script_code)
        tmp_path = f.name
    try:
        from cli_anything.r.utils.r_backend import run_script
        result = run_script(tmp_path)
        if result["returncode"] != 0:
            raise RuntimeError(f"R script failed: {result['stderr']}")
        click.echo(result["stdout"])
    finally:
        os.unlink(tmp_path)


@data.command("head")
@click.argument("index", type=int)
@click.option("--rows", "-n", default=6, type=int, help="Number of rows")
@handle_error
def data_head(index, rows):
    """Show first N rows of a dataset (runs R head())."""
    import tempfile
    sess = get_session()
    script_code = data_mod.build_head_script(sess.get_project(), index, n=rows)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.R', delete=False) as f:
        f.write(script_code)
        tmp_path = f.name
    try:
        from cli_anything.r.utils.r_backend import run_script
        result = run_script(tmp_path)
        if result["returncode"] != 0:
            raise RuntimeError(f"R script failed: {result['stderr']}")
        parsed = json.loads(result["stdout"])
        output(parsed, f"First {rows} rows:")
    finally:
        os.unlink(tmp_path)


@data.command("filter")
@click.argument("index", type=int)
@click.argument("expression")
@click.option("--name", "-n", default=None, help="Name for filtered dataset")
@handle_error
def data_filter(index, expression, name):
    """Create a filtered dataset from an existing one."""
    sess = get_session()
    sess.snapshot(f"Filter dataset {index}: {expression}")
    entry = data_mod.filter_dataset(sess.get_project(), index, expression, name=name)
    output(entry, f"Created filtered dataset: {entry['name']}")


@data.command("transform")
@click.argument("index", type=int)
@click.argument("expression")
@click.option("--name", "-n", default=None, help="Name for transformed dataset")
@handle_error
def data_transform(index, expression, name):
    """Create a transformed dataset from an existing one."""
    sess = get_session()
    sess.snapshot(f"Transform dataset {index}: {expression}")
    entry = data_mod.transform_dataset(sess.get_project(), index, expression, name=name)
    output(entry, f"Created transformed dataset: {entry['name']}")


@data.command("remove")
@click.argument("index", type=int)
@handle_error
def data_remove(index):
    """Remove a dataset by index."""
    sess = get_session()
    sess.snapshot(f"Remove dataset {index}")
    removed = data_mod.remove_dataset(sess.get_project(), index)
    output(removed, f"Removed dataset: {removed['name']}")


# ── Analysis Commands ────────────────────────────────────────────
@cli.group("analysis")
def analysis_group():
    """Statistical analysis commands."""
    pass


@analysis_group.command("list-types")
@click.option("--category", type=str, default=None,
              help="Filter by category (hypothesis, descriptive, modeling, multivariate)")
@handle_error
def analysis_list_types(category):
    """List available analysis types."""
    analyses = analysis_mod.list_analyses(category)
    output(analyses, "Available analysis types:")


@analysis_group.command("info")
@click.argument("name")
@handle_error
def analysis_info(name):
    """Show details about an analysis type."""
    info = analysis_mod.get_analysis_info(name)
    output(info)


@analysis_group.command("add")
@click.argument("analysis_type")
@click.argument("dataset_index", type=int)
@click.option("--formula", type=str, default=None, help="R formula (e.g., mpg ~ am)")
@click.option("--x", type=str, default=None, help="X variable name")
@click.option("--y", type=str, default=None, help="Y variable name")
@click.option("--method", type=str, default=None, help="Method (e.g., pearson, spearman)")
@click.option("--paired", is_flag=True, help="Paired test")
@click.option("--alternative", type=str, default=None,
              help="Alternative hypothesis (two.sided, less, greater)")
@click.option("--scale", is_flag=True, help="Scale data (for PCA)")
@click.option("--columns", type=str, default=None, help="Columns to include")
@handle_error
def analysis_add(analysis_type, dataset_index, formula, x, y, method,
                 paired, alternative, scale, columns):
    """Add an analysis to the project."""
    params = {}
    if formula is not None:
        params["formula"] = formula
    if x is not None:
        params["x"] = x
    if y is not None:
        params["y"] = y
    if method is not None:
        params["method"] = method
    if paired:
        params["paired"] = True
    if alternative is not None:
        params["alternative"] = alternative
    if scale:
        params["scale"] = True
    if columns is not None:
        params["columns"] = columns

    sess = get_session()
    sess.snapshot(f"Add analysis: {analysis_type}")
    entry = analysis_mod.add_analysis(
        sess.get_project(), analysis_type, dataset_index,
        params if params else None,
    )
    output(entry, f"Added analysis: {analysis_type}")


@analysis_group.command("remove")
@click.argument("index", type=int)
@handle_error
def analysis_remove(index):
    """Remove an analysis by index."""
    sess = get_session()
    sess.snapshot(f"Remove analysis {index}")
    removed = analysis_mod.remove_analysis(sess.get_project(), index)
    output(removed, f"Removed analysis {index}")


@analysis_group.command("log")
@handle_error
def analysis_log():
    """List recorded analyses."""
    sess = get_session()
    log = analysis_mod.list_analysis_log(sess.get_project())
    output(log, "Analysis log:")


@analysis_group.command("run")
@click.argument("index", type=int)
@handle_error
def analysis_run(index):
    """Run an analysis (executes R script and stores results)."""
    import tempfile
    sess = get_session()
    sess.snapshot(f"Run analysis {index}")
    script_code = analysis_mod.build_analysis_script(sess.get_project(), index)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.R', delete=False) as f:
        f.write(script_code)
        tmp_path = f.name
    try:
        from cli_anything.r.utils.r_backend import run_script
        result = run_script(tmp_path)
        if result["returncode"] != 0:
            raise RuntimeError(f"R script failed: {result['stderr']}")
        stdout = result["stdout"].strip()
        # Try to parse as JSON; some analyses output plain text
        try:
            parsed = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            parsed = {"text_output": stdout}
        # Store results in the project
        analyses = sess.get_project().get("analyses", [])
        if 0 <= index < len(analyses):
            analyses[index]["results"] = parsed
        output(parsed, f"Analysis {index} results:")
    finally:
        os.unlink(tmp_path)


# ── Plot Commands ────────────────────────────────────────────────
@cli.group("plot")
def plot_group():
    """ggplot2 plot commands."""
    pass


@plot_group.command("scatter")
@click.argument("dataset_index", type=int)
@click.option("--x", required=True, help="X variable")
@click.option("--y", required=True, help="Y variable")
@click.option("--color", default=None, help="Color variable")
@click.option("--size", default=None, help="Size variable")
@click.option("--name", "-n", default=None, help="Plot name")
@handle_error
def plot_scatter(dataset_index, x, y, color, size, name):
    """Create a scatter plot."""
    aes = {"x": x, "y": y}
    if color:
        aes["color"] = color
    if size:
        aes["size"] = size
    sess = get_session()
    sess.snapshot(f"Add scatter plot")
    entry = plot_mod.add_plot(sess.get_project(), "scatter", dataset_index, aes, name=name)
    output(entry, f"Added scatter plot: {entry['name']}")


@plot_group.command("bar")
@click.argument("dataset_index", type=int)
@click.option("--x", required=True, help="X variable")
@click.option("--fill", default=None, help="Fill variable")
@click.option("--name", "-n", default=None, help="Plot name")
@handle_error
def plot_bar(dataset_index, x, fill, name):
    """Create a bar chart."""
    aes = {"x": x}
    if fill:
        aes["fill"] = fill
    sess = get_session()
    sess.snapshot(f"Add bar plot")
    entry = plot_mod.add_plot(sess.get_project(), "bar", dataset_index, aes, name=name)
    output(entry, f"Added bar chart: {entry['name']}")


@plot_group.command("histogram")
@click.argument("dataset_index", type=int)
@click.option("--x", required=True, help="X variable")
@click.option("--fill", default=None, help="Fill variable")
@click.option("--bins", default=None, type=int, help="Number of bins")
@click.option("--name", "-n", default=None, help="Plot name")
@handle_error
def plot_histogram(dataset_index, x, fill, bins, name):
    """Create a histogram."""
    aes = {"x": x}
    if fill:
        aes["fill"] = fill
    if bins:
        aes["bins"] = bins
    sess = get_session()
    sess.snapshot(f"Add histogram")
    entry = plot_mod.add_plot(sess.get_project(), "histogram", dataset_index, aes, name=name)
    output(entry, f"Added histogram: {entry['name']}")


@plot_group.command("boxplot")
@click.argument("dataset_index", type=int)
@click.option("--x", required=True, help="X variable")
@click.option("--y", required=True, help="Y variable")
@click.option("--fill", default=None, help="Fill variable")
@click.option("--name", "-n", default=None, help="Plot name")
@handle_error
def plot_boxplot(dataset_index, x, y, fill, name):
    """Create a box plot."""
    aes = {"x": x, "y": y}
    if fill:
        aes["fill"] = fill
    sess = get_session()
    sess.snapshot(f"Add boxplot")
    entry = plot_mod.add_plot(sess.get_project(), "boxplot", dataset_index, aes, name=name)
    output(entry, f"Added boxplot: {entry['name']}")


@plot_group.command("heatmap")
@click.argument("dataset_index", type=int)
@click.option("--x", required=True, help="X variable")
@click.option("--y", required=True, help="Y variable")
@click.option("--fill", required=True, help="Fill variable")
@click.option("--name", "-n", default=None, help="Plot name")
@handle_error
def plot_heatmap(dataset_index, x, y, fill, name):
    """Create a heatmap."""
    aes = {"x": x, "y": y, "fill": fill}
    sess = get_session()
    sess.snapshot(f"Add heatmap")
    entry = plot_mod.add_plot(sess.get_project(), "heatmap", dataset_index, aes, name=name)
    output(entry, f"Added heatmap: {entry['name']}")


@plot_group.command("line")
@click.argument("dataset_index", type=int)
@click.option("--x", required=True, help="X variable")
@click.option("--y", required=True, help="Y variable")
@click.option("--color", default=None, help="Color variable")
@click.option("--name", "-n", default=None, help="Plot name")
@handle_error
def plot_line(dataset_index, x, y, color, name):
    """Create a line plot."""
    aes = {"x": x, "y": y}
    if color:
        aes["color"] = color
    sess = get_session()
    sess.snapshot(f"Add line plot")
    entry = plot_mod.add_plot(sess.get_project(), "line", dataset_index, aes, name=name)
    output(entry, f"Added line plot: {entry['name']}")


@plot_group.command("list")
@handle_error
def plot_list():
    """List all plots."""
    sess = get_session()
    plots = plot_mod.list_plots(sess.get_project())
    output(plots, "Plots:")


@plot_group.command("remove")
@click.argument("index", type=int)
@handle_error
def plot_remove(index):
    """Remove a plot by index."""
    sess = get_session()
    sess.snapshot(f"Remove plot {index}")
    removed = plot_mod.remove_plot(sess.get_project(), index)
    output(removed, f"Removed plot: {removed['name']}")


THEMES = [
    "theme_minimal", "theme_bw", "theme_classic", "theme_dark",
    "theme_light", "theme_void", "theme_gray", "theme_linedraw",
]


@plot_group.command("customize")
@click.argument("index", type=int)
@click.option("--title", default=None, help="Plot title")
@click.option("--subtitle", default=None, help="Plot subtitle")
@click.option("--x-label", default=None, help="X axis label")
@click.option("--y-label", default=None, help="Y axis label")
@click.option("--theme", type=click.Choice(THEMES), default=None, help="ggplot2 theme")
@click.option("--facet", default=None, help="Facet formula")
@click.option("--coord-flip", is_flag=True, help="Flip coordinates")
@click.option("--legend-position", default=None, help="Legend position")
@click.option("--caption", default=None, help="Plot caption")
@handle_error
def plot_customize(index, title, subtitle, x_label, y_label, theme,
                   facet, coord_flip, legend_position, caption):
    """Customize a plot's appearance."""
    kwargs = {}
    if title is not None:
        kwargs["title"] = title
    if subtitle is not None:
        kwargs["subtitle"] = subtitle
    if x_label is not None:
        kwargs["x_label"] = x_label
    if y_label is not None:
        kwargs["y_label"] = y_label
    if theme is not None:
        kwargs["theme"] = theme
    if facet is not None:
        kwargs["facet_formula"] = facet
    if coord_flip:
        kwargs["coord_flip"] = True
    if legend_position is not None:
        kwargs["legend_position"] = legend_position
    if caption is not None:
        kwargs["caption"] = caption

    sess = get_session()
    sess.snapshot(f"Customize plot {index}")
    entry = plot_mod.customize_plot(sess.get_project(), index, **kwargs)
    output(entry, f"Customized plot: {entry['name']}")


# ── Script Commands ──────────────────────────────────────────────
@cli.group("script")
def script_group():
    """Custom R script management."""
    pass


@script_group.command("add")
@click.argument("code")
@click.option("--name", "-n", default=None, help="Script name")
@click.option("--description", "-d", default="", help="Description")
@handle_error
def script_add(code, name, description):
    """Add a custom R script to the project."""
    sess = get_session()
    sess.snapshot("Add script")
    entry = script_mod.add_script(sess.get_project(), code, name, description)
    output(entry, f"Added script: {entry['name']}")


@script_group.command("list")
@handle_error
def script_list():
    """List all scripts."""
    sess = get_session()
    scripts = script_mod.list_scripts(sess.get_project())
    output(scripts, "Scripts:")


@script_group.command("run")
@click.argument("index", type=int)
@handle_error
def script_run(index):
    """Run a script (executes via R backend)."""
    import tempfile
    sess = get_session()
    script_entry = script_mod.get_script(sess.get_project(), index)
    code = script_entry["code"]
    with tempfile.NamedTemporaryFile(mode='w', suffix='.R', delete=False) as f:
        f.write(code)
        tmp_path = f.name
    try:
        from cli_anything.r.utils.r_backend import run_script
        result = run_script(tmp_path)
        if result["returncode"] != 0:
            raise RuntimeError(f"R script failed: {result['stderr']}")
        if result["stdout"].strip():
            click.echo(result["stdout"])
        output({"script": script_entry["name"], "status": "completed"},
               f"Script '{script_entry['name']}' completed.")
    finally:
        os.unlink(tmp_path)


@script_group.command("remove")
@click.argument("index", type=int)
@handle_error
def script_remove(index):
    """Remove a script by index."""
    sess = get_session()
    sess.snapshot(f"Remove script {index}")
    removed = script_mod.remove_script(sess.get_project(), index)
    output(removed, f"Removed script: {removed['name']}")


# ── Export Commands ──────────────────────────────────────────────
@cli.group("export")
def export_group():
    """Export/render commands."""
    pass


@export_group.command("render-plot")
@click.argument("plot_index", type=int)
@click.argument("output_path")
@click.option("--format", "-f", "fmt", default="png",
              type=click.Choice(["png", "pdf", "svg", "jpeg", "tiff"]),
              help="Output format")
@click.option("--width", type=int, default=800, help="Image width in pixels")
@click.option("--height", type=int, default=600, help="Image height in pixels")
@click.option("--dpi", type=int, default=300, help="Resolution in DPI")
@click.option("--overwrite", is_flag=True, help="Overwrite existing file")
@handle_error
def export_render_plot(plot_index, output_path, fmt, width, height, dpi, overwrite):
    """Render a plot to an image file."""
    sess = get_session()
    result = export_mod.render_plot(
        sess.get_project(), plot_index, output_path,
        format=fmt, width=width, height=height, dpi=dpi, overwrite=overwrite,
    )
    globals()["output"](result, f"Rendered to: {output_path}")


@export_group.command("export-data")
@click.argument("dataset_index", type=int)
@click.argument("output_path")
@click.option("--format", "-f", "fmt", default="csv",
              type=click.Choice(["csv", "tsv", "rds", "xlsx"]),
              help="Output format")
@click.option("--overwrite", is_flag=True, help="Overwrite existing file")
@handle_error
def export_export_data(dataset_index, output_path, fmt, overwrite):
    """Export a dataset to a file."""
    sess = get_session()
    result = export_mod.export_data(
        sess.get_project(), dataset_index, output_path,
        format=fmt, overwrite=overwrite,
    )
    globals()["output"](result, f"Exported to: {output_path}")


# ── Backend Commands ─────────────────────────────────────────────
@cli.group()
def backend():
    """R backend commands."""
    pass


@backend.command("version")
@handle_error
def backend_version():
    """Show R version."""
    from cli_anything.r.utils.r_backend import get_version
    ver = get_version()
    output({"version": ver}, ver)


@backend.command("find")
@handle_error
def backend_find():
    """Show Rscript executable path."""
    from cli_anything.r.utils.r_backend import find_r
    path = find_r()
    output({"path": path}, f"Rscript: {path}")


@backend.command("run-expr")
@click.argument("expr")
@click.option("--timeout", type=int, default=60)
@handle_error
def backend_run_expr(expr, timeout):
    """Run an R expression directly."""
    from cli_anything.r.utils.r_backend import run_expression
    result = run_expression(expr, timeout=timeout)
    output(result)


@backend.command("run-script")
@click.argument("script_path")
@click.option("--timeout", type=int, default=300)
@handle_error
def backend_run_script(script_path, timeout):
    """Run an R script file."""
    from cli_anything.r.utils.r_backend import run_script
    result = run_script(script_path, timeout=timeout)
    output(result)


@backend.command("install-pkg")
@click.argument("package_name")
@handle_error
def backend_install_pkg(package_name):
    """Install an R package."""
    from cli_anything.r.utils.r_backend import install_package
    result = install_package(package_name)
    output(result, f"Installed: {package_name}")


@backend.command("list-pkgs")
@handle_error
def backend_list_pkgs():
    """List installed R packages."""
    from cli_anything.r.utils.r_backend import list_packages
    pkgs = list_packages()
    output(pkgs, "Installed R packages:")


# ── Session Commands ─────────────────────────────────────────────
@cli.group()
def session():
    """Session management commands."""
    pass


@session.command("status")
@handle_error
def session_status():
    """Show session status."""
    sess = get_session()
    output(sess.status())


@session.command("undo")
@handle_error
def session_undo():
    """Undo the last operation."""
    sess = get_session()
    desc = sess.undo()
    output({"undone": desc}, f"Undone: {desc}")


@session.command("redo")
@handle_error
def session_redo():
    """Redo the last undone operation."""
    sess = get_session()
    desc = sess.redo()
    output({"redone": desc}, f"Redone: {desc}")


@session.command("history")
@handle_error
def session_history():
    """Show undo history."""
    sess = get_session()
    history = sess.list_history()
    output(history, "Undo history:")


@session.command("save")
@click.argument("path", required=False)
@handle_error
def session_save(path):
    """Save the current session."""
    sess = get_session()
    saved = sess.save_session(path)
    output({"saved": saved}, f"Saved to: {saved}")


# ── REPL ─────────────────────────────────────────────────────────
@cli.command()
@click.option("--project", "project_path", type=str, default=None)
@handle_error
def repl(project_path):
    """Start interactive REPL session."""
    from cli_anything.r.utils.repl_skin import ReplSkin

    global _repl_mode
    _repl_mode = True

    skin = ReplSkin("r", version="1.0.0")

    if project_path:
        sess = get_session()
        proj = proj_mod.open_project(project_path)
        sess.set_project(proj, project_path)

    skin.print_banner()

    pt_session = skin.create_prompt_session()

    _repl_commands = {
        "project":  "new|open|save|info|profiles|json",
        "data":     "load|list|inspect|summary|head|filter|transform|remove",
        "analysis": "list-types|info|add|remove|log|run",
        "plot":     "scatter|bar|histogram|boxplot|heatmap|line|list|remove|customize",
        "script":   "add|list|run|remove",
        "export":   "render-plot|export-data",
        "backend":  "version|find|run-expr|run-script|install-pkg|list-pkgs",
        "session":  "status|undo|redo|history|save",
        "help":     "Show this help",
        "quit":     "Exit REPL",
    }

    while True:
        try:
            try:
                sess = get_session()
                proj_name = ""
                if sess.has_project():
                    p = sess.get_project()
                    proj_name = p.get("name", "") if isinstance(p, dict) else ""
            except Exception:
                proj_name = ""

            line = skin.get_input(pt_session, project_name=proj_name, modified=False)
            if not line:
                continue
            if line.lower() in ("quit", "exit", "q"):
                skin.print_goodbye()
                break
            if line.lower() == "help":
                skin.help(_repl_commands)
                continue

            args = line.split()
            try:
                cli.main(args, standalone_mode=False)
            except SystemExit:
                pass
            except click.exceptions.UsageError as e:
                skin.warning(f"Usage error: {e}")
            except Exception as e:
                skin.error(f"{e}")

        except (EOFError, KeyboardInterrupt):
            skin.print_goodbye()
            break

    _repl_mode = False


# ── Entry Point ──────────────────────────────────────────────────
def main():
    cli()


if __name__ == "__main__":
    main()
