#!/usr/bin/env python3
"""Fiji CLI — A stateful command-line interface for scientific image processing.

This CLI provides image analysis, processing, measurement, and batch
operations using Fiji/ImageJ as the backend engine.

Usage:
    # One-shot commands
    cli-anything-fiji project new -n "experiment_001"
    cli-anything-fiji image add micrograph.tif
    cli-anything-fiji process add gaussian_blur -p sigma=2.0
    cli-anything-fiji measure run analyze_particles

    # Interactive REPL
    cli-anything-fiji
"""

import sys
import os
import json
import click
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli_anything.fiji.core.session import Session
from cli_anything.fiji.core import project as proj_mod
from cli_anything.fiji.core import image as img_mod
from cli_anything.fiji.core import processing as proc_mod
from cli_anything.fiji.core import roi as roi_mod
from cli_anything.fiji.core import measure as meas_mod
from cli_anything.fiji.core import macro as macro_mod
from cli_anything.fiji.core import export as export_mod

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
              help="Path to .fiji-cli.json project file")
@click.pass_context
def cli(ctx, use_json, project_path):
    """Fiji CLI — Scientific image processing from the command line.

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
@click.option("--width", "-w", type=int, default=512, help="Image width")
@click.option("--height", "-h", type=int, default=512, help="Image height")
@click.option("--bit-depth", type=click.Choice(["8", "16", "32"]), default="8")
@click.option("--channels", "-c", type=int, default=1, help="Number of channels")
@click.option("--slices", "-z", type=int, default=1, help="Number of Z-slices")
@click.option("--frames", "-t", type=int, default=1, help="Number of time frames")
@click.option("--type", "image_type", default="auto",
              type=click.Choice(["auto", "8-bit", "16-bit", "32-bit", "RGB"]))
@click.option("--name", "-n", default="untitled", help="Project name")
@click.option("--profile", "-p", type=str, default=None, help="Image profile")
@click.option("--output", "-o", "output_path", type=str, default=None, help="Save path")
@handle_error
def project_new(width, height, bit_depth, channels, slices, frames,
                image_type, name, profile, output_path):
    """Create a new Fiji CLI project."""
    proj = proj_mod.create_project(
        width=width, height=height, bit_depth=int(bit_depth),
        channels=channels, slices=slices, frames=frames,
        image_type=image_type, name=name, profile=profile,
    )
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
    """List available image profiles."""
    profiles = proj_mod.list_profiles()
    output(profiles, "Available profiles:")


@project.command("json")
@handle_error
def project_json():
    """Print raw project JSON."""
    sess = get_session()
    click.echo(json.dumps(sess.get_project(), indent=2, default=str))


# ── Image Commands ───────────────────────────────────────────────
@cli.group()
def image():
    """Image management commands."""
    pass


@image.command("add")
@click.argument("path")
@click.option("--name", "-n", default=None, help="Display name")
@handle_error
def image_add(path, name):
    """Add an image file to the project."""
    sess = get_session()
    sess.snapshot(f"Add image: {path}")
    entry = img_mod.add_image(sess.get_project(), path, name=name)
    output(entry, f"Added image: {entry['name']}")


@image.command("remove")
@click.argument("index", type=int)
@handle_error
def image_remove(index):
    """Remove an image by index."""
    sess = get_session()
    sess.snapshot(f"Remove image {index}")
    removed = img_mod.remove_image(sess.get_project(), index)
    output(removed, f"Removed image: {removed['name']}")


@image.command("list")
@handle_error
def image_list():
    """List all images in the project."""
    sess = get_session()
    images = img_mod.list_images(sess.get_project())
    output(images, "Images:")


@image.command("info")
@click.argument("index", type=int)
@handle_error
def image_info(index):
    """Show details about an image."""
    sess = get_session()
    entry = img_mod.get_image(sess.get_project(), index)
    output(entry)


# ── Processing Commands ──────────────────────────────────────────
@cli.group("process")
def process_group():
    """Image processing operations."""
    pass


@process_group.command("list-ops")
@click.option("--category", "-c", type=str, default=None,
              help="Filter by category: adjust, filter, morphology, convert, spatial, analysis, stack")
@handle_error
def process_list_ops(category):
    """List available processing operations."""
    ops = proc_mod.list_operations(category)
    output(ops, "Available operations:")


@process_group.command("info")
@click.argument("name")
@handle_error
def process_info(name):
    """Show details about a processing operation."""
    info = proc_mod.get_operation_info(name)
    output(info)


@process_group.command("add")
@click.argument("operation")
@click.option("--image", "-i", "image_index", type=int, default=0, help="Image index")
@click.option("--param", "-p", multiple=True, help="Parameter: key=value")
@handle_error
def process_add(operation, image_index, param):
    """Add a processing step to the pipeline."""
    params = {}
    for p in param:
        if "=" not in p:
            raise ValueError(f"Invalid param format: '{p}'. Use key=value.")
        k, v = p.split("=", 1)
        try:
            v = float(v) if "." in v else int(v)
        except ValueError:
            pass
        params[k] = v

    sess = get_session()
    sess.snapshot(f"Add processing: {operation}")
    step = proc_mod.add_processing_step(
        sess.get_project(), operation, image_index, params
    )
    output(step, f"Added: {operation}")


@process_group.command("remove")
@click.argument("step_index", type=int)
@handle_error
def process_remove(step_index):
    """Remove a processing step."""
    sess = get_session()
    sess.snapshot(f"Remove processing step {step_index}")
    removed = proc_mod.remove_processing_step(sess.get_project(), step_index)
    output(removed, f"Removed step {step_index}")


@process_group.command("log")
@handle_error
def process_log():
    """Show the processing pipeline."""
    sess = get_session()
    log = proc_mod.list_processing_log(sess.get_project())
    output(log, "Processing pipeline:")


@process_group.command("macro")
@click.option("--image", "-i", "image_index", type=int, default=0)
@handle_error
def process_macro(image_index):
    """Show the generated ImageJ macro for the pipeline."""
    sess = get_session()
    macro = proc_mod.build_macro(sess.get_project(), image_index)
    if macro:
        click.echo(macro)
    else:
        click.echo("No processing steps defined.")


# ── ROI Commands ─────────────────────────────────────────────────
@cli.group()
def roi():
    """ROI (Region of Interest) commands."""
    pass


@roi.command("add")
@click.argument("roi_type", type=click.Choice(
    ["rectangle", "oval", "line", "polygon", "point"]))
@click.option("--name", "-n", default=None, help="ROI name")
@click.option("--x", type=int, default=None)
@click.option("--y", type=int, default=None)
@click.option("--width", "-w", type=int, default=None)
@click.option("--height", "-h", type=int, default=None)
@click.option("--x1", type=int, default=None)
@click.option("--y1", type=int, default=None)
@click.option("--x2", type=int, default=None)
@click.option("--y2", type=int, default=None)
@handle_error
def roi_add(roi_type, name, x, y, width, height, x1, y1, x2, y2):
    """Add a ROI to the project."""
    kwargs = {}
    if roi_type in ("rectangle", "oval"):
        if x is None or y is None or width is None or height is None:
            raise ValueError(f"{roi_type} ROI requires --x, --y, --width, --height")
        kwargs = {"x": x, "y": y, "width": width, "height": height}
    elif roi_type == "line":
        if x1 is None or y1 is None or x2 is None or y2 is None:
            raise ValueError("line ROI requires --x1, --y1, --x2, --y2")
        kwargs = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
    elif roi_type == "point":
        if x is None or y is None:
            raise ValueError("point ROI requires --x, --y")
        kwargs = {"x": x, "y": y}

    sess = get_session()
    sess.snapshot(f"Add ROI: {roi_type}")
    r = roi_mod.add_roi(sess.get_project(), roi_type, name=name, **kwargs)
    output(r, f"Added ROI: {r['name']}")


@roi.command("remove")
@click.argument("index", type=int)
@handle_error
def roi_remove(index):
    """Remove a ROI by index."""
    sess = get_session()
    sess.snapshot(f"Remove ROI {index}")
    removed = roi_mod.remove_roi(sess.get_project(), index)
    output(removed, f"Removed ROI: {removed['name']}")


@roi.command("list")
@handle_error
def roi_list():
    """List all ROIs."""
    sess = get_session()
    rois = roi_mod.list_rois(sess.get_project())
    output(rois, "ROIs:")


@roi.command("macro")
@click.argument("index", type=int)
@handle_error
def roi_macro(index):
    """Show the ImageJ macro to create a ROI."""
    sess = get_session()
    r = roi_mod.get_roi(sess.get_project(), index)
    macro = roi_mod.build_roi_macro(r)
    click.echo(macro)


# ── Measure Commands ─────────────────────────────────────────────
@cli.group()
def measure():
    """Measurement and analysis commands."""
    pass


@measure.command("types")
@handle_error
def measure_types():
    """List available measurement types."""
    types = meas_mod.list_measurement_types()
    output(types, "Measurement types:")


@measure.command("commands")
@handle_error
def measure_commands():
    """List available analysis commands."""
    cmds = meas_mod.list_analysis_commands()
    output(cmds, "Analysis commands:")


@measure.command("configure")
@click.option("--measurements", "-m", multiple=True,
              help="Measurement types to include")
@click.option("--scale-distance", type=float, default=1.0)
@click.option("--scale-known", type=float, default=1.0)
@click.option("--scale-unit", default="pixel")
@handle_error
def measure_configure(measurements, scale_distance, scale_known, scale_unit):
    """Configure measurement parameters."""
    sess = get_session()
    sess.snapshot("Configure measurements")
    mlist = list(measurements) if measurements else None
    config = meas_mod.add_measurement_config(
        sess.get_project(), mlist, scale_distance, scale_known, scale_unit
    )
    output(config, "Measurement configuration updated")


@measure.command("run")
@click.argument("command")
@click.option("--param", "-p", multiple=True, help="Parameter: key=value")
@handle_error
def measure_run(command, param):
    """Run an analysis command (builds macro for Fiji execution)."""
    params = {}
    for p in param:
        if "=" not in p:
            raise ValueError(f"Invalid param format: '{p}'. Use key=value.")
        k, v = p.split("=", 1)
        try:
            v = float(v) if "." in v else int(v)
        except ValueError:
            pass
        params[k] = v

    macro = meas_mod.build_analysis_macro(command, params if params else None)
    output({"command": command, "macro": macro}, f"Analysis macro for '{command}':")


@measure.command("results")
@handle_error
def measure_results():
    """Show measurement results."""
    sess = get_session()
    results = meas_mod.list_measurements(sess.get_project())
    output(results, "Measurement results:")


@measure.command("clear")
@handle_error
def measure_clear():
    """Clear all measurement results."""
    sess = get_session()
    sess.snapshot("Clear measurements")
    count = meas_mod.clear_measurements(sess.get_project())
    output({"cleared": count}, f"Cleared {count} measurement(s)")


# ── Macro Commands ───────────────────────────────────────────────
@cli.group("macro")
def macro_group():
    """Custom macro management."""
    pass


@macro_group.command("add")
@click.option("--code", "-c", required=True, help="ImageJ macro code")
@click.option("--name", "-n", default=None, help="Macro name")
@click.option("--description", "-d", default="", help="Description")
@handle_error
def macro_add(code, name, description):
    """Add a custom macro to the project."""
    sess = get_session()
    sess.snapshot("Add macro")
    entry = macro_mod.add_macro(sess.get_project(), code, name, description)
    output(entry, f"Added macro: {entry['name']}")


@macro_group.command("add-file")
@click.argument("path")
@click.option("--name", "-n", default=None, help="Macro name")
@click.option("--description", "-d", default="", help="Description")
@handle_error
def macro_add_file(path, name, description):
    """Add a macro from a file."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Macro file not found: {path}")
    with open(path, "r") as f:
        code = f.read()
    if name is None:
        name = os.path.splitext(os.path.basename(path))[0]
    sess = get_session()
    sess.snapshot(f"Add macro from: {path}")
    entry = macro_mod.add_macro(sess.get_project(), code, name, description)
    output(entry, f"Added macro: {entry['name']}")


@macro_group.command("remove")
@click.argument("index", type=int)
@handle_error
def macro_remove(index):
    """Remove a macro by index."""
    sess = get_session()
    sess.snapshot(f"Remove macro {index}")
    removed = macro_mod.remove_macro(sess.get_project(), index)
    output(removed, f"Removed macro: {removed['name']}")


@macro_group.command("list")
@handle_error
def macro_list():
    """List all macros."""
    sess = get_session()
    macros = macro_mod.list_macros(sess.get_project())
    output(macros, "Macros:")


@macro_group.command("show")
@click.argument("index", type=int)
@handle_error
def macro_show(index):
    """Show a macro's code."""
    sess = get_session()
    m = macro_mod.get_macro(sess.get_project(), index)
    click.echo(f"# {m['name']}")
    click.echo(m["code"])


@macro_group.command("batch")
@click.option("--input-dir", "-i", required=True, help="Input directory")
@click.option("--output-dir", "-o", required=True, help="Output directory")
@click.option("--pattern", default=".*\\.tif", help="File regex pattern")
@click.option("--macro-index", "-m", type=int, default=None,
              help="Use stored macro (index)")
@handle_error
def macro_batch(input_dir, output_dir, pattern, macro_index):
    """Generate a batch processing macro."""
    sess = get_session()
    macro = macro_mod.build_batch_macro(
        sess.get_project(), input_dir, output_dir, pattern, macro_index
    )
    click.echo(macro)


# ── Export Commands ──────────────────────────────────────────────
@cli.group("export")
def export_group():
    """Export/render commands."""
    pass


@export_group.command("presets")
@handle_error
def export_presets():
    """List export presets."""
    presets = export_mod.list_presets()
    output(presets, "Export presets:")


@export_group.command("preset-info")
@click.argument("name")
@handle_error
def export_preset_info(name):
    """Show preset details."""
    info = export_mod.get_preset_info(name)
    output(info)


@export_group.command("render")
@click.argument("output_path")
@click.option("--preset", "-p", default="tiff", help="Export preset")
@click.option("--overwrite", is_flag=True, help="Overwrite existing file")
@click.option("--image", "-i", "image_index", type=int, default=0, help="Image index")
@handle_error
def export_render(output_path, preset, overwrite, image_index):
    """Render/export an image through Fiji with applied processing."""
    sess = get_session()
    result = export_mod.render(
        sess.get_project(), output_path,
        preset=preset, overwrite=overwrite, image_index=image_index,
    )
    globals()["output"](result, f"Rendered to: {output_path}")


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


# ── Backend Commands ─────────────────────────────────────────────
@cli.group()
def backend():
    """Fiji backend commands."""
    pass


@backend.command("version")
@handle_error
def backend_version():
    """Show Fiji version."""
    from cli_anything.fiji.utils.fiji_backend import get_version
    ver = get_version()
    output({"version": ver}, ver)


@backend.command("find")
@handle_error
def backend_find():
    """Show Fiji executable path."""
    from cli_anything.fiji.utils.fiji_backend import find_fiji
    path = find_fiji()
    output({"path": path}, f"Fiji: {path}")


@backend.command("run-macro")
@click.argument("macro_code")
@click.option("--timeout", type=int, default=300)
@handle_error
def backend_run_macro(macro_code, timeout):
    """Run an ImageJ macro directly."""
    from cli_anything.fiji.utils.fiji_backend import run_macro
    result = run_macro(macro_code, timeout=timeout)
    output(result)


@backend.command("run-script")
@click.argument("script_path")
@click.option("--timeout", type=int, default=300)
@handle_error
def backend_run_script(script_path, timeout):
    """Run a script file in Fiji."""
    from cli_anything.fiji.utils.fiji_backend import run_script
    result = run_script(script_path, timeout=timeout)
    output(result)


# ── REPL ─────────────────────────────────────────────────────────
@cli.command()
@click.option("--project", "project_path", type=str, default=None)
@handle_error
def repl(project_path):
    """Start interactive REPL session."""
    from cli_anything.fiji.utils.repl_skin import ReplSkin

    global _repl_mode
    _repl_mode = True

    skin = ReplSkin("fiji", version="1.0.0")

    if project_path:
        sess = get_session()
        proj = proj_mod.open_project(project_path)
        sess.set_project(proj, project_path)

    skin.print_banner()

    pt_session = skin.create_prompt_session()

    _repl_commands = {
        "project":  "new|open|save|info|profiles|json",
        "image":    "add|remove|list|info",
        "process":  "list-ops|info|add|remove|log|macro",
        "roi":      "add|remove|list|macro",
        "measure":  "types|commands|configure|run|results|clear",
        "macro":    "add|add-file|remove|list|show|batch",
        "export":   "presets|preset-info|render",
        "backend":  "version|find|run-macro|run-script",
        "session":  "status|undo|redo|history",
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
