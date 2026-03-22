#!/usr/bin/env python3
"""PyMOL CLI — A stateful command-line interface for molecular visualization.

This CLI provides full molecular visualization management using a JSON
session format, with PyMOL script (.pml) generation for rendering.

Usage:
    # One-shot commands
    cli-anything-pymol project new --name "MySession"
    cli-anything-pymol structure load 1abc
    cli-anything-pymol representation show cartoon --target 1abc

    # Interactive REPL
    cli-anything-pymol repl
"""

import sys
import os
import json
import click
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli_anything.pymol.core.session import Session
from cli_anything.pymol.core import project as proj_mod
from cli_anything.pymol.core import structure as struct_mod
from cli_anything.pymol.core import selection as sel_mod
from cli_anything.pymol.core import representation as rep_mod
from cli_anything.pymol.core import coloring as color_mod
from cli_anything.pymol.core import view as view_mod
from cli_anything.pymol.core import label as label_mod
from cli_anything.pymol.core import render as render_mod

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


def handle_error(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
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
              help="Path to .pymol-cli.json project file")
@click.pass_context
def cli(ctx, use_json, project_path):
    """PyMOL CLI — Stateful molecular visualization from the command line.

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


# ── Project Commands ────────────────────────────────────────────
@cli.group()
def project():
    """Project/session management commands."""
    pass


@project.command("new")
@click.option("--name", "-n", default="untitled", help="Session name")
@click.option("--profile", "-p", type=str, default=None, help="Session profile")
@click.option("--width", "-w", type=int, default=1920, help="Render width")
@click.option("--height", "-h", type=int, default=1080, help="Render height")
@click.option("--bg-color", type=str, default=None, help="Background color R,G,B (0.0-1.0)")
@click.option("--output", "-o", type=str, default=None, help="Save path")
@handle_error
def project_new(name, profile, width, height, bg_color, output):
    """Create a new session."""
    bg = [float(x) for x in bg_color.split(",")] if bg_color else None
    proj = proj_mod.create_project(
        name=name, profile=profile, width=width, height=height, bg_color=bg,
    )
    sess = get_session()
    sess.set_project(proj, output)
    if output:
        proj_mod.save_project(proj, output)
    output_data = proj_mod.get_project_info(proj)
    globals()["output"](output_data, f"Created session: {name}")


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
    """List available session profiles."""
    profiles = proj_mod.list_profiles()
    output(profiles, "Available profiles:")


@project.command("json")
@handle_error
def project_json():
    """Print raw project JSON."""
    sess = get_session()
    click.echo(json.dumps(sess.get_project(), indent=2, default=str))


# ── Structure Commands ──────────────────────────────────────────
@cli.group()
def structure():
    """Molecular structure management commands."""
    pass


@structure.command("load")
@click.argument("path")
@click.option("--name", "-n", default=None, help="Object name")
@click.option("--format", "source_format", default=None, help="File format override")
@click.option("--state", "-s", type=int, default=1, help="State number for multi-model files")
@handle_error
def structure_load(path, name, source_format, state):
    """Load a molecular structure (file path or PDB ID)."""
    sess = get_session()
    sess.snapshot(f"Load structure: {path}")
    s = struct_mod.load_structure(
        sess.get_project(), path=path, object_name=name,
        source_format=source_format, state=state,
    )
    output(s, f"Loaded: {s['object_name']} ({s['source']})")


@structure.command("remove")
@click.argument("index", type=int)
@handle_error
def structure_remove(index):
    """Remove a structure by index."""
    sess = get_session()
    sess.snapshot(f"Remove structure {index}")
    removed = struct_mod.remove_structure(sess.get_project(), index)
    output(removed, f"Removed: {removed.get('object_name', '')}")


@structure.command("rename")
@click.argument("index", type=int)
@click.argument("new_name")
@handle_error
def structure_rename(index, new_name):
    """Rename a structure."""
    sess = get_session()
    sess.snapshot(f"Rename structure {index}")
    s = struct_mod.rename_structure(sess.get_project(), index, new_name)
    output(s, f"Renamed to: {s['object_name']}")


@structure.command("list")
@handle_error
def structure_list():
    """List all structures."""
    sess = get_session()
    structures = struct_mod.list_structures(sess.get_project())
    output(structures, "Structures:")


@structure.command("get")
@click.argument("index", type=int)
@handle_error
def structure_get(index):
    """Get detailed info about a structure."""
    sess = get_session()
    s = struct_mod.get_structure(sess.get_project(), index)
    output(s)


@structure.command("formats")
@handle_error
def structure_formats():
    """List supported file formats."""
    formats = struct_mod.list_formats()
    output(formats, "Supported formats:")


# ── Selection Commands ──────────────────────────────────────────
@cli.group()
def selection():
    """Named atom selection commands."""
    pass


@selection.command("create")
@click.argument("name")
@click.argument("expression")
@handle_error
def selection_create(name, expression):
    """Create a named selection (e.g., 'active_site' 'resi 100-150 and chain A')."""
    sess = get_session()
    sess.snapshot(f"Create selection: {name}")
    sel = sel_mod.create_selection(sess.get_project(), name=name, expression=expression)
    output(sel, f"Created selection: {sel['name']}")


@selection.command("remove")
@click.argument("index", type=int)
@handle_error
def selection_remove(index):
    """Remove a selection by index."""
    sess = get_session()
    sess.snapshot(f"Remove selection {index}")
    removed = sel_mod.remove_selection(sess.get_project(), index)
    output(removed, f"Removed: {removed.get('name', '')}")


@selection.command("update")
@click.argument("index", type=int)
@click.option("--expression", "-e", default=None, help="New selection expression")
@click.option("--enable/--disable", default=None, help="Enable or disable")
@handle_error
def selection_update(index, expression, enable):
    """Update a selection."""
    sess = get_session()
    sess.snapshot(f"Update selection {index}")
    sel = sel_mod.update_selection(sess.get_project(), index, expression=expression, enabled=enable)
    output(sel, f"Updated selection: {sel['name']}")


@selection.command("list")
@handle_error
def selection_list():
    """List all selections."""
    sess = get_session()
    selections = sel_mod.list_selections(sess.get_project())
    output(selections, "Selections:")


@selection.command("get")
@click.argument("index", type=int)
@handle_error
def selection_get(index):
    """Get a selection by index."""
    sess = get_session()
    sel = sel_mod.get_selection(sess.get_project(), index)
    output(sel)


@selection.command("macros")
@handle_error
def selection_macros():
    """List available selection macros."""
    macros = sel_mod.list_macros()
    output(macros, "Selection macros:")


# ── Representation Commands ─────────────────────────────────────
@cli.group("representation")
def representation_group():
    """Visual representation commands."""
    pass


@representation_group.command("show")
@click.argument("rep_type", type=click.Choice(sorted(rep_mod.REPRESENTATION_REGISTRY.keys())))
@click.option("--target", "-t", required=True, help="Object or selection name")
@click.option("--setting", "-s", multiple=True, help="Setting: key=value")
@handle_error
def representation_show(rep_type, target, setting):
    """Show a representation on a target."""
    settings = {}
    for s in setting:
        if "=" not in s:
            raise ValueError(f"Invalid setting format: '{s}'. Use key=value.")
        k, v = s.split("=", 1)
        try:
            v = float(v) if "." in v else int(v)
        except ValueError:
            pass
        settings[k] = v

    sess = get_session()
    sess.snapshot(f"Show {rep_type} on {target}")
    rep = rep_mod.show_representation(
        sess.get_project(), target=target, rep_type=rep_type,
        settings=settings if settings else None,
    )
    output(rep, f"Showing {rep_type} on {target}")


@representation_group.command("hide")
@click.option("--target", "-t", required=True, help="Object or selection name")
@click.option("--type", "rep_type", default=None, help="Specific rep type to hide (or all)")
@handle_error
def representation_hide(target, rep_type):
    """Hide representations on a target."""
    sess = get_session()
    sess.snapshot(f"Hide {rep_type or 'all'} on {target}")
    hidden = rep_mod.hide_representation(sess.get_project(), target=target, rep_type=rep_type)
    output(hidden, f"Hidden {len(hidden)} representation(s)")


@representation_group.command("remove")
@click.argument("index", type=int)
@handle_error
def representation_remove(index):
    """Remove a representation by index."""
    sess = get_session()
    sess.snapshot(f"Remove representation {index}")
    removed = rep_mod.remove_representation(sess.get_project(), index)
    output(removed, f"Removed representation {index}")


@representation_group.command("set")
@click.argument("index", type=int)
@click.argument("setting")
@click.argument("value")
@handle_error
def representation_set(index, setting, value):
    """Set a representation setting."""
    try:
        value = float(value) if "." in str(value) else int(value)
    except ValueError:
        pass
    sess = get_session()
    sess.snapshot(f"Set representation {index} {setting}={value}")
    rep = rep_mod.set_representation_setting(sess.get_project(), index, setting, value)
    output(rep, f"Set {setting} = {value}")


@representation_group.command("list")
@click.option("--target", "-t", default=None, help="Filter by target")
@handle_error
def representation_list(target):
    """List representations."""
    sess = get_session()
    reps = rep_mod.list_representations(sess.get_project(), target=target)
    output(reps, "Representations:")


@representation_group.command("available")
@click.option("--category", "-c", default=None, help="Filter by category")
@handle_error
def representation_available(category):
    """List available representation types."""
    reps = rep_mod.list_available(category=category)
    output(reps, "Available representations:")


@representation_group.command("info")
@click.argument("name")
@handle_error
def representation_info(name):
    """Show details about a representation type."""
    info = rep_mod.get_representation_info(name)
    output(info)


# ── Color Commands ──────────────────────────────────────────────
@cli.group()
def color():
    """Color management commands."""
    pass


@color.command("apply")
@click.option("--target", "-t", required=True, help="Object or selection name")
@click.option("--color", "-c", "color_name", default=None, help="Named color (e.g., red, marine)")
@click.option("--rgb", default=None, help="RGB values R,G,B (0.0-1.0)")
@click.option("--scheme", "-s", default=None, help="Color scheme (e.g., by_element, by_chain)")
@handle_error
def color_apply(target, color_name, rgb, scheme):
    """Apply a color or color scheme to a target."""
    rgb_list = [float(x) for x in rgb.split(",")] if rgb else None
    sess = get_session()
    sess.snapshot(f"Color {target}")
    entry = color_mod.apply_color(
        sess.get_project(), target=target,
        color=color_name, rgb=rgb_list, scheme=scheme,
    )
    output(entry, f"Applied color to {target}")


@color.command("remove")
@click.argument("index", type=int)
@handle_error
def color_remove(index):
    """Remove a color entry by index."""
    sess = get_session()
    sess.snapshot(f"Remove color {index}")
    removed = color_mod.remove_color(sess.get_project(), index)
    output(removed, f"Removed color {index}")


@color.command("list")
@handle_error
def color_list():
    """List all color entries."""
    sess = get_session()
    colors = color_mod.list_colors(sess.get_project())
    output(colors, "Colors:")


@color.command("named")
@handle_error
def color_named():
    """List available named colors."""
    colors = color_mod.list_named_colors()
    output(colors, "Named colors:")


@color.command("schemes")
@handle_error
def color_schemes():
    """List available color schemes."""
    schemes = color_mod.list_schemes()
    output(schemes, "Color schemes:")


# ── View Commands ───────────────────────────────────────────────
@cli.group()
def view():
    """View/camera management commands."""
    pass


@view.command("set")
@click.option("--preset", "-p", default=None, help="View preset (front, back, top, etc.)")
@click.option("--zoom", "-z", type=float, default=None, help="Zoom level")
@click.option("--fov", type=float, default=None, help="Field of view (degrees)")
@click.option("--position", default=None, help="Camera position x,y,z")
@handle_error
def view_set(preset, zoom, fov, position):
    """Set view/camera parameters."""
    pos = [float(x) for x in position.split(",")] if position else None
    sess = get_session()
    sess.snapshot("Set view")
    result = view_mod.set_view(
        sess.get_project(), preset=preset, zoom=zoom,
        field_of_view=fov, position=pos,
    )
    output(result, "View updated")


@view.command("info")
@handle_error
def view_info():
    """Show current view settings."""
    sess = get_session()
    info = view_mod.get_view(sess.get_project())
    output(info)


@view.command("presets")
@handle_error
def view_presets():
    """List available view presets."""
    presets = view_mod.list_view_presets()
    output(presets, "View presets:")


@view.command("setting")
@click.argument("setting_name")
@click.argument("value")
@handle_error
def view_setting(setting_name, value):
    """Set a global setting (bg_color, depth_cue, fog, etc.)."""
    # Handle list values
    if "," in value:
        value = [float(x) for x in value.split(",")]
    sess = get_session()
    sess.snapshot(f"Set {setting_name}")
    result = view_mod.set_setting(sess.get_project(), setting_name, value)
    output(result, f"Set {setting_name}")


@view.command("settings")
@handle_error
def view_settings():
    """List all current settings."""
    sess = get_session()
    settings = view_mod.list_settings(sess.get_project())
    output(settings, "Settings:")


# ── Label Commands ──────────────────────────────────────────────
@cli.group()
def label():
    """Label management commands."""
    pass


@label.command("add")
@click.argument("target")
@click.option("--format", "format_preset", default=None, help="Label format preset")
@click.option("--expression", "-e", default=None, help="Custom label expression")
@click.option("--color", "-c", default=None, help="Label color R,G,B (0.0-1.0)")
@click.option("--size", "-s", type=int, default=14, help="Font size (8-72)")
@handle_error
def label_add(target, format_preset, expression, color, size):
    """Add labels to atoms/residues."""
    col = [float(x) for x in color.split(",")] if color else None
    sess = get_session()
    sess.snapshot(f"Add labels to {target}")
    lbl = label_mod.add_label(
        sess.get_project(), target=target,
        format_preset=format_preset, expression=expression,
        color=col, size=size,
    )
    output(lbl, f"Added labels to {target}")


@label.command("remove")
@click.argument("index", type=int)
@handle_error
def label_remove(index):
    """Remove a label by index."""
    sess = get_session()
    sess.snapshot(f"Remove label {index}")
    removed = label_mod.remove_label(sess.get_project(), index)
    output(removed, f"Removed label {index}")


@label.command("clear")
@click.option("--target", "-t", default=None, help="Clear labels for specific target only")
@handle_error
def label_clear(target):
    """Clear all labels."""
    sess = get_session()
    sess.snapshot("Clear labels")
    count = label_mod.clear_labels(sess.get_project(), target=target)
    output({"removed": count}, f"Cleared {count} label(s)")


@label.command("list")
@handle_error
def label_list():
    """List all labels."""
    sess = get_session()
    labels = label_mod.list_labels(sess.get_project())
    output(labels, "Labels:")


@label.command("formats")
@handle_error
def label_formats():
    """List available label format presets."""
    formats = label_mod.list_label_formats()
    output(formats, "Label formats:")


# ── Render Commands ─────────────────────────────────────────────
@cli.group("render")
def render_group():
    """Render settings and output commands."""
    pass


@render_group.command("settings")
@click.option("--width", "-w", type=int, default=None)
@click.option("--height", "-h", type=int, default=None)
@click.option("--ray/--no-ray", default=None, help="Enable/disable ray tracing")
@click.option("--ray-trace-mode", type=int, default=None, help="Ray trace mode (0-3)")
@click.option("--antialias", type=int, default=None, help="Antialias level (0-2)")
@click.option("--format", "output_format", default=None, help="Output format")
@click.option("--dpi", type=int, default=None, help="DPI for output")
@click.option("--transparent/--no-transparent", default=None, help="Transparent background")
@click.option("--preset", default=None, help="Apply render preset")
@handle_error
def render_settings(width, height, ray, ray_trace_mode, antialias,
                    output_format, dpi, transparent, preset):
    """Configure render settings."""
    sess = get_session()
    sess.snapshot("Update render settings")
    result = render_mod.set_render_settings(
        sess.get_project(),
        width=width, height=height, ray=ray,
        ray_trace_mode=ray_trace_mode, antialias=antialias,
        output_format=output_format, dpi=dpi,
        transparent_background=transparent, preset=preset,
    )
    output(result, "Render settings updated")


@render_group.command("info")
@handle_error
def render_info():
    """Show current render settings."""
    sess = get_session()
    info = render_mod.get_render_settings(sess.get_project())
    output(info)


@render_group.command("presets")
@handle_error
def render_presets():
    """List available render presets."""
    presets = render_mod.list_render_presets()
    output(presets, "Render presets:")


@render_group.command("execute")
@click.argument("output_path")
@click.option("--overwrite", is_flag=True, help="Overwrite existing file")
@handle_error
def render_execute(output_path, overwrite):
    """Render the scene (generates PyMOL script)."""
    sess = get_session()
    result = render_mod.render_scene(
        sess.get_project(), output_path, overwrite=overwrite,
    )
    output(result, f"Render script generated: {result['script_path']}")


@render_group.command("script")
@handle_error
def render_script():
    """Print the PyMOL render script to stdout."""
    from cli_anything.pymol.utils.pml_gen import generate_full_script
    sess = get_session()
    script = generate_full_script(sess.get_project(), "/tmp/output.png")
    click.echo(script)


# ── Session Commands ────────────────────────────────────────────
@cli.group("session")
def session_group():
    """Session management commands."""
    pass


@session_group.command("status")
@handle_error
def session_status():
    """Show session status."""
    sess = get_session()
    output(sess.status())


@session_group.command("undo")
@handle_error
def session_undo():
    """Undo the last operation."""
    sess = get_session()
    desc = sess.undo()
    output({"undone": desc}, f"Undone: {desc}")


@session_group.command("redo")
@handle_error
def session_redo():
    """Redo the last undone operation."""
    sess = get_session()
    desc = sess.redo()
    output({"redone": desc}, f"Redone: {desc}")


@session_group.command("history")
@handle_error
def session_history():
    """Show undo history."""
    sess = get_session()
    history = sess.list_history()
    output(history, "Undo history:")


# ── REPL ────────────────────────────────────────────────────────
@cli.command()
@click.option("--project", "project_path", type=str, default=None)
@handle_error
def repl(project_path):
    """Start interactive REPL session."""
    from cli_anything.pymol.utils.repl_skin import ReplSkin

    global _repl_mode
    _repl_mode = True

    skin = ReplSkin("pymol", version="1.0.0")

    if project_path:
        sess = get_session()
        proj = proj_mod.open_project(project_path)
        sess.set_project(proj, project_path)

    skin.print_banner()

    pt_session = skin.create_prompt_session()

    _repl_commands = {
        "project":        "new|open|save|info|profiles|json",
        "structure":      "load|remove|rename|list|get|formats",
        "selection":      "create|remove|update|list|get|macros",
        "representation": "show|hide|remove|set|list|available|info",
        "color":          "apply|remove|list|named|schemes",
        "view":           "set|info|presets|setting|settings",
        "label":          "add|remove|clear|list|formats",
        "render":         "settings|info|presets|execute|script",
        "session":        "status|undo|redo|history",
        "help":           "show this help",
        "quit":           "exit REPL",
    }

    while True:
        try:
            sess = get_session()
            project_name = ""
            modified = False
            if sess.has_project():
                if sess.project_path:
                    project_name = os.path.basename(sess.project_path)
                else:
                    info = sess.get_project()
                    project_name = info.get("name", "")
                modified = sess._modified

            line = skin.get_input(pt_session, project_name=project_name, modified=modified).strip()
            if not line:
                continue
            if line.lower() in ("quit", "exit", "q"):
                skin.print_goodbye()
                break
            if line.lower() == "help":
                skin.help(_repl_commands)
                continue

            # Parse and execute command
            args = line.split()
            try:
                cli.main(args, standalone_mode=False)
            except SystemExit:
                pass
            except click.exceptions.UsageError as e:
                skin.warning(f"Usage error: {e}")
            except Exception as e:
                skin.error(str(e))

        except (EOFError, KeyboardInterrupt):
            skin.print_goodbye()
            break

    _repl_mode = False


# ── Entry Point ─────────────────────────────────────────────────
def main():
    cli()


if __name__ == "__main__":
    main()
