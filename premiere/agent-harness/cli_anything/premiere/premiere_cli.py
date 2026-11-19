"""cli-anything-premiere: read Adobe Premiere Pro project files; the live commands (lazy groups) change a running Premiere 27."""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

import click

from cli_anything.premiere import __version__
from cli_anything.premiere.core import prproj
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.utils import premiere_backend

PROG = "cli-anything-premiere"
REPL_COMMANDS = {
    "status": "Show the Premiere install and the project formats this reader was tested with",
    "project info FILE": "Format version, save time, counts of sequences, media and Color Mode clips",
    "project media FILE": "List the media file paths the project points at",
    "sequence list FILE": "List the sequences with size, frame rate and length",
    "sequence clips FILE [--sequence NAME] [--kind video|audio] [--track N]": "List the clips on the tracks",
    "clip effects FILE [--sequence NAME] [--track N] --clip N [--effect NAME] [--all]": "Effects of one clip, in render order, with values and keyframes",
    "colormode list FILE": "Clips that carry a Color Mode grade",
    "live state": "Version, open projects, active sequence and selection of the running Premiere",
    "live run SCRIPT.jsx [--args JSON] [--write] [--verify CHECK.jsx] [--project PATH]": "Run a script in the running Premiere",
    "doctor": "Check the connection to the running Premiere. Changes nothing",
    "recovery show": "Show an open recovery of the bridge. Changes nothing",
    "mcp list [--query TEXT]": "List the tools of the premiere-pro-mcp server",
    "mcp describe TOOL": "What one tool of the server does and what arguments it takes",
    "mcp call TOOL [--args JSON]": "Run one tool of the server. No write check of the CLI runs",
    "mcp batch FILE": "Run the calls in FILE in one server session. No write check of the CLI runs",
    "ui mode get": "Show which mode is checked in Window > Mode",
    "ui mode set MODE": "Switch Premiere to import, edit, color or export mode",
    "live clip list --sequence ID [--track-type video|audio|both] [--track N] [--detail]": "The clips of one sequence, one line per clip",
    "live effect get --sequence ID (--track N --clip N | --node-id ID) [--effect NAME]": "The effects of one clip and their properties",
    "live effect set --sequence ID (--track N --clip N | --node-id ID) --effect NAME --index N --name NAME --value JSON [--dry-run]": "Write one effect value, read it back",
    "live effect set --from PLAN.json [--dry-run]": "Write the values of a plan file, each read back",
    "live frame export --sequence ID --time SECONDS --out-dir DIR [--overwrite]": "Export still frames of one sequence",
    "live app proxies on|off": "Switch the proxy display of Premiere",
    "live app activate --sequence ID": "Make a sequence the active one",
    "live lut set --sequence ID (--track N --clip N | --node-id ID) --lut FILE.cube [--prove --time SECONDS --out-dir DIR]": "Write a LUT into Lumetri Color, read it back",
    "live keys get --sequence ID (--track N --clip N | --node-id ID) [--effect NAME] [--property NAME]": "The keys of one property of one clip (Motion Rotation unless named)",
    "live keys set --sequence ID --from LEVEL.json [--no-scale] [--skip-refused] [--dry-run]": "Write Rotation keys and one static Scale per clip from a level plan, each read back",
    "live place --sequence ID --plan SHOTS.json [--overwrite] [--skip-refused] [--dry-run]": "Place the shots of a placement plan on a sequence, each read back",
    "live effect apply --sequence ID --track N (--clips 0,3,5 | --all) --effect NAME [--skip-refused] [--dry-run]": "Apply effects to the clips of a video track in render order, each read back",
    "help": "Show this list",
    "exit": "Leave the shell",
}


def _set_json(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if value:
        ctx.ensure_object(dict)["json"] = True


json_option = click.option(
    "--json", "json_flag", is_flag=True, expose_value=False, callback=_set_json,
    help="Print machine-readable JSON.",
)
file_argument = click.argument("file", type=click.Path(path_type=Path))
sequence_option = click.option("--sequence", "sequence_key", help="Sequence name or id. Not needed when the project has one sequence.")


def _json(ctx: click.Context) -> bool:
    root = ctx.find_root()
    return bool((ctx.obj or {}).get("json") or (root.obj or {}).get("json"))


def _echo_json(value: dict, indent: int | None = None) -> None:
    """Print JSON. Text that the output cannot encode (a lone surrogate from Premiere) is escaped, so the
    output stays valid JSON and loses nothing."""
    try:
        click.echo(json.dumps(value, indent=indent, ensure_ascii=False))
    except UnicodeEncodeError:
        click.echo(json.dumps(value, indent=indent, ensure_ascii=True))


def _echo_text(text: str, err: bool = False) -> None:
    """Print text. What the output cannot encode is replaced."""
    try:
        click.echo(text, err=err)
    except UnicodeEncodeError:
        encoding = getattr(sys.stderr if err else sys.stdout, "encoding", None) or "utf-8"
        click.echo(text.encode(encoding, "replace").decode(encoding), err=err)


def _emit(ctx: click.Context, payload: dict, human: str) -> None:
    if _json(ctx):
        _echo_json({"ok": True, **payload}, indent=2)
    else:
        _echo_text(human)


def _fail(ctx: click.Context, err: CliError) -> None:
    if _json(ctx):
        _echo_json({"ok": False, "error": err.message, "code": err.code})
    else:
        _echo_text(f"Error: {err.message}", err=True)
    ctx.exit(1)


def _table(headers: list[str], rows: list[list]) -> str:
    cells = [headers] + [[str(c) for c in row] for row in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _sec(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _load(ctx: click.Context, file: Path) -> prproj.Project:
    try:
        return prproj.Project.load(file)
    except CliError as err:
        _fail(ctx, err)
        raise  # unreachable: _fail exits


@click.group(invoke_without_command=True)
@json_option
@click.version_option(__version__, prog_name=PROG)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Read Premiere Pro project files: sequences, clips and effects. The file commands never write. The live commands change the running Premiere 27."""
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand is None:
        _run_repl(ctx)


@cli.command()
@json_option
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show the Premiere install and the project formats this reader was tested with."""
    info = premiere_backend.status()
    lines = [f"Premiere: {info['app'] or 'not found'}" + (f" (version {info['version']})" if info["version"] else "")]
    lines.append("Tested project formats: " + ", ".join(f"{k} ({v})" for k, v in info["tested_project_formats"].items()))
    lines.append("Smoke-read only (no errors, values not compared with Premiere): " + ", ".join(f"{k} ({v})" for k, v in info["smoke_read_project_formats"].items()))
    lines.append("The file commands only read project files and need no running Premiere. "
                 "The live commands need Premiere 27 with the MCP Bridge panel.")
    try:  # the live panel, from its heartbeat file only: no lease, no script
        import importlib

        live_bridge = importlib.import_module("cli_anything.premiere.core.bridge")
        bridge_dir = live_bridge.default_bridge_dir()
        heartbeat = live_bridge.read_heartbeat(bridge_dir)
        live = {"panel_heartbeat": "absent" if heartbeat is None else ("fresh" if heartbeat.fresh else "waiting" if heartbeat.waiting else "stale"),
                "bridge_dir": str(bridge_dir)}
        lines.append(f"Live panel heartbeat: {live['panel_heartbeat']} ({live['bridge_dir']})")
    except Exception as exc:
        live = {"error": str(exc)}
        lines.append(f"Live panel heartbeat: cannot be checked ({exc})")
    _emit(ctx, {**info, "live": live}, "\n".join(lines))


@cli.group()
def project() -> None:
    """Project-level facts."""


@project.command("info")
@file_argument
@json_option
@click.pass_context
def project_info(ctx: click.Context, file: Path) -> None:
    """Format version, save time, and counts of sequences, media and Color Mode clips."""
    info = _load(ctx, file).info()
    lines = [
        f"File: {info['path']} ({info['size_bytes']} bytes, {'gzip' if info['compressed'] else 'plain XML'})",
        f"Saved: {info['saved']}",
        f"Project format version: {info['project_format_version']}" + (f" ({info['written_by']})" if info["written_by"] else ""),
        f"Sequences: {info['sequences']}   Media files: {info['media_files']}   Clips with a Color Mode grade: {info['clips_with_color_mode']}",
    ]
    lines.append("Color Mode: " + info["color_mode_note"])
    if info["format_warning"]:
        lines.append("Warning: " + info["format_warning"])
    _emit(ctx, {"project": info}, "\n".join(lines))


@project.command("media")
@file_argument
@json_option
@click.pass_context
def project_media(ctx: click.Context, file: Path) -> None:
    """List the media file paths the project points at."""
    paths = _load(ctx, file).media_paths()
    _emit(ctx, {"media": paths}, "\n".join(paths) if paths else "No media.")


@cli.group()
def sequence() -> None:
    """Sequences and their clips."""


@sequence.command("list")
@file_argument
@json_option
@click.pass_context
def sequence_list(ctx: click.Context, file: Path) -> None:
    """List the sequences with size, frame rate and length."""
    seqs = [s.as_dict() for s in _load(ctx, file).sequences()]
    rows = [[s["name"], f"{s['width']}x{s['height']}", s["fps"], _sec(s["duration_seconds"]), s["video_tracks"], s["audio_tracks"], s["clips"], s["id"]] for s in seqs]
    _emit(ctx, {"sequences": seqs}, _table(["name", "size", "fps", "seconds", "V", "A", "clips", "id"], rows) if rows else "No sequence.")


@sequence.command("clips")
@file_argument
@sequence_option
@click.option("--kind", type=click.Choice(["video", "audio", "both"]), default="video", show_default=True)
@click.option("--track", "track_index", type=int, help="Zero-based track index (V1 = 0).")
@json_option
@click.pass_context
def sequence_clips(ctx: click.Context, file: Path, sequence_key: str | None, kind: str, track_index: int | None) -> None:
    """List the clips on the tracks, with timeline and source times and effect names."""
    proj = _load(ctx, file)
    try:
        seq = proj.find_sequence(sequence_key)
    except CliError as err:
        _fail(ctx, err)
        return
    out, rows = [], []
    for t in seq.tracks:
        if (kind != "both" and t.kind != kind) or (track_index is not None and t.index != track_index):
            continue
        clips = [c.as_dict() for c in t.clips]
        out.append({"kind": t.kind, "index": t.index, "muted": t.muted, "locked": t.locked, "clips": clips})
        for c in clips:
            rows.append([f"{'V' if t.kind == 'video' else 'A'}{t.index + 1}", c["index"], c["name"], _sec(c["start_seconds"]), _sec(c["end_seconds"]),
                         _sec(c["in_seconds"]), _sec(c["out_seconds"]), ", ".join(c["effects"]) + ("  [Color Mode]" if c["has_color_mode"] else "")])
    human = _table(["track", "clip", "name", "start", "end", "in", "out", "effects (file order)"], rows) if rows else "No clips."
    _emit(ctx, {"sequence": seq.as_dict(), "tracks": out}, human)


@cli.group()
def clip() -> None:
    """One clip on the timeline."""


@clip.command("effects")
@file_argument
@sequence_option
@click.option("--track", "track_index", type=int, default=0, show_default=True, help="Zero-based video track index (V1 = 0).")
@click.option("--clip", "clip_index", type=int, required=True, help="Zero-based clip index on the track.")
@click.option("--effect", "effect_name", help="Only this effect (display name or match name).")
@click.option("--all", "show_all", is_flag=True, help="Also print unnamed and binary parameters.")
@json_option
@click.pass_context
def clip_effects(ctx: click.Context, file: Path, sequence_key: str | None, track_index: int, clip_index: int,
                 effect_name: str | None, show_all: bool) -> None:
    """Effects of one clip in render order, with parameter values and keyframes."""
    proj = _load(ctx, file)
    try:
        seq = proj.find_sequence(sequence_key)
        track = next((t for t in seq.tracks if t.kind == "video" and t.index == track_index), None)
        if track is None:
            raise CliError("track_not_found", f"Sequence {seq.name!r} has no video track with index {track_index}.")
        item = next((c for c in track.clips if c.index == clip_index), None)
        if item is None:
            raise CliError("clip_not_found", f"Track V{track_index + 1} has no clip with index {clip_index} (it has {len(track.clips)}).")
    except CliError as err:
        _fail(ctx, err)
        return
    effects = item.effects
    if effect_name:
        effects = [e for e in effects if effect_name.lower() in (e.display_name.lower(), e.match_name.lower())]
        if not effects:
            _fail(ctx, CliError("effect_not_found", f"The clip has no effect named {effect_name!r}. It has: {', '.join(e.display_name for e in item.effects)}."))
            return
    ordered = sorted(effects, key=lambda e: (e.render_position is None, e.render_position or 0, e.file_index))
    payload = {"clip": item.as_dict(), "render_order_note": "render_position 1 renders first; applied effects render in the reverse of their file order (measured on Premiere 27.0.0). Intrinsic effects (Motion, Opacity) have no position.",
               "effects": []}
    lines = [f"{item.name}  timeline {_sec(prproj.ticks_to_seconds(item.start_ticks))}-{_sec(prproj.ticks_to_seconds(item.end_ticks))} s"]
    for e in ordered:
        d = e.as_dict()
        lumetri = prproj.lumetri_summary(e)
        if lumetri:
            d["lumetri"] = lumetri
        if not show_all:
            d["params"] = [p for p in d["params"] if p["name"].strip() and p["kind"] != "opaque"]
        payload["effects"].append(d)
        head = f"\n[{'intrinsic' if e.intrinsic else 'render ' + str(e.render_position)}] {e.display_name}  ({e.match_name})" + ("  BYPASSED" if e.bypass else "")
        lines.append(head)
        if e.color_mode:
            lines.append("  Color Mode grade. Its layout is not decoded yet; use --json for the raw summary.")
        if lumetri and lumetri["input_lut_path"]:
            lines.append(f"  Input LUT file: {lumetri['input_lut_path']}")
        for p in d["params"]:
            if p["kind"] == "opaque":
                lines.append(f"  {p['index']:>3}  {p['name'] or '(no name)'}: binary, {p['opaque_bytes']} bytes" + (f", text {p['text']!r}" if p.get("text") else ""))
                continue
            keys = p.get("keyframes") or []
            key_text = ("  keys: " + ", ".join(f"{_sec(k['media_seconds'])} s = {k['value']}" for k in keys[:8]) + (" ..." if len(keys) > 8 else "")) if keys else ""
            lines.append(f"  {p['index']:>3}  {p['name']}: {p['value']}{key_text}")
    _emit(ctx, payload, "\n".join(lines))


@cli.group()
def colormode() -> None:
    """Color Mode grades (Premiere 27)."""


@colormode.command("list")
@file_argument
@json_option
@click.pass_context
def colormode_list(ctx: click.Context, file: Path) -> None:
    """List the clips that carry a Color Mode grade."""
    proj = _load(ctx, file)
    found, rows = [], []
    for seq in proj.sequences():
        for t in seq.tracks:
            for c in t.clips:
                for e in c.effects:
                    if e.color_mode:
                        found.append({"sequence": seq.name, "track_kind": t.kind, "track": t.index, "clip": c.index, "name": c.name,
                                      "start_seconds": prproj.ticks_to_seconds(c.start_ticks), "bypass": e.bypass, "raw_summary": e.raw_summary})
                        rows.append([seq.name, f"V{t.index + 1}", c.index, c.name, _sec(prproj.ticks_to_seconds(c.start_ticks))])
    note = prproj.color_mode_note(len(found), proj.color_mode_markers)
    human = (_table(["sequence", "track", "clip", "name", "start"], rows) + "\n" + note) if rows else note
    _emit(ctx, {"clips": found, "color_mode_markers": proj.color_mode_markers, "note": note}, human)


@cli.command("repl")
@json_option
@click.pass_context
def repl_command(ctx: click.Context) -> None:
    """Start the interactive shell (also the default with no command)."""
    _run_repl(ctx)


class LazyGroup(click.Group):
    """A group whose commands live in another module that is imported on first use."""

    def __init__(self, *args, import_path: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.import_path = import_path
        self._target: click.Group | None = None

    def _load(self) -> click.Group:
        if self._target is None:
            import importlib

            module_name, _, attr = self.import_path.partition(":")
            target = getattr(importlib.import_module(module_name), attr)
            if not isinstance(target, click.Group):
                raise TypeError(f"{self.import_path} is not a click group")
            self._target = target
        return self._target

    def _unavailable(self, ctx: click.Context, exc: Exception) -> None:
        _fail(ctx, CliError("live_unavailable", f"The {self.name} commands cannot load: {type(exc).__name__}: {exc}"))

    def make_context(self, info_name, args, parent=None, **extra):
        # Hand the whole call to the real group, so its options, callback and help apply.
        try:
            target = self._load()
        except Exception as exc:
            ctx = click.Context(self, info_name=info_name, parent=parent)
            if "--json" in args:  # the subcommand's own --json is never parsed here
                ctx.ensure_object(dict)["json"] = True
            self._unavailable(ctx, exc)
            raise
        return target.make_context(info_name, args, parent=parent, **extra)

    def list_commands(self, ctx):
        try:
            return self._load().list_commands(ctx)
        except Exception as exc:
            self._unavailable(ctx, exc)
            raise

    def get_command(self, ctx, cmd_name):
        try:
            return self._load().get_command(ctx, cmd_name)
        except Exception as exc:
            self._unavailable(ctx, exc)
            raise


cli.add_command(LazyGroup(name="live",     import_path="cli_anything.premiere.live_cli:live",     help="Read and change the running Premiere 27 with scripts."))
cli.add_command(LazyGroup(name="mcp",      import_path="cli_anything.premiere.live_cli:mcp",      help="Call any tool of the premiere-pro-mcp server."))
cli.add_command(LazyGroup(name="ui",       import_path="cli_anything.premiere.live_cli:ui",       help="Click Premiere menus (Window > Mode)."))
cli.add_command(LazyGroup(name="recovery", import_path="cli_anything.premiere.live_cli:recovery", help="Show an open recovery of the bridge."))
cli.add_command(LazyGroup(name="doctor",   import_path="cli_anything.premiere.live_cli:doctor",   help="Check the connection to the running Premiere. Changes nothing."))


def _run_repl(ctx: click.Context) -> None:
    from cli_anything.premiere.utils.repl_skin import ReplSkin

    skin = ReplSkin("premiere", version=__version__)
    skin.print_banner()
    session = skin.create_prompt_session()
    prefix = ["--json"] if _json(ctx) else []
    while True:
        try:
            line = skin.get_input(session)
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line in ("exit", "quit"):
            break
        if line == "help":
            skin.help(REPL_COMMANDS)
            continue
        try:
            argv = shlex.split(line)
        except ValueError as exc:
            skin.error(f"parse error: {exc}")
            continue
        code = dispatch(prefix + argv)
        if code:
            skin.warning(f"command exited with status {code}")
    skin.print_goodbye()


def dispatch(argv: list[str] | None = None, prog_name: str | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        result = cli.main(args=args, prog_name=prog_name or PROG, standalone_mode=False)
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    except click.exceptions.Abort:
        return 1
    except click.ClickException as exc:
        exc.show()
        return int(exc.exit_code)
    return int(result or 0)


def entrypoint(argv: list[str] | None = None) -> int:
    return dispatch(argv, prog_name=PROG)


if __name__ == "__main__":
    raise SystemExit(entrypoint())
