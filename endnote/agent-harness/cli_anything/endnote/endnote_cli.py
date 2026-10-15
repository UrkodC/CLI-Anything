"""cli-anything-endnote: add PubMed papers to EndNote 2025 and link [PMID ...] markers in Word files."""
from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

import click

from cli_anything.endnote import __version__
from cli_anything.endnote.core import library, word_link
from cli_anything.endnote.errors import CliError

PROG = "cli-anything-endnote"
TRACKED_WARNING = "This file has tracked changes. Accept all changes in Word before you click Update."
REPL_COMMANDS = {
    "status": "Show EndNote and the open library",
    "add PMID ... | add --file FILE": "Add papers by PMID",
    "find PMID ...": "Show the EndNote record for each PMID",
    "link FILE.docx [--output F] [--overwrite]": "Turn [PMID ...] markers into EndNote citations",
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


def _json(ctx: click.Context) -> bool:
    return bool((ctx.obj or {}).get("json"))


def _emit(ctx: click.Context, payload: dict, human: str) -> None:
    if _json(ctx):
        click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        click.echo(human)


def _fail(ctx: click.Context, err: CliError) -> None:
    if _json(ctx):
        click.echo(json.dumps({"ok": False, "error": err.message, "code": err.code}, ensure_ascii=False))
    else:
        click.echo(f"Error: {err.message}", err=True)
    ctx.exit(1)


@click.group(invoke_without_command=True)
@json_option
@click.version_option(__version__, prog_name=PROG)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Add PubMed papers to EndNote 2025 and link [PMID ...] markers in Word files."""
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand is None:
        _run_repl(ctx)


@cli.command()
@json_option
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show whether EndNote is running and which library is open."""
    try:
        info = library.status()
    except CliError as err:
        _fail(ctx, err)
        return
    if not info["running"]:
        human = "EndNote is not running."
    elif not info["libraries"]:
        human = "EndNote is running. No library is open."
    else:
        human = "Open libraries: " + ", ".join(info["libraries"])
        if info["record_count"] is not None:
            human += f"\nReferences: {info['record_count']}"
        if len(info["libraries"]) > 1:
            human += "\nClose all libraries except one before you run add or link."
    _emit(ctx, {"ok": True, **info}, human)


def _format_add(report: library.AddReport) -> str:
    lines = [
        f"Library: {report.library}",
        f"Added: {len(report.added)}   Skipped (already in library): {len(report.skipped)}   Failed: {len(report.failed)}",
    ]
    lines += [f"  + {r.pmid}  {r.citation}  {r.title}" for r in report.added]
    lines += [f"  ! {f['pmid']}  {f['reason']}" for f in report.failed]
    lines += [f"Warning: {w}" for w in report.warnings]
    return "\n".join(lines)


@cli.command()
@click.argument("pmids", nargs=-1)
@click.option("--file", "pmid_file", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None,
              help="Text file with PMIDs. Lines that start with # are ignored.")
@json_option
@click.pass_context
def add(ctx: click.Context, pmids: tuple[str, ...], pmid_file: Path | None) -> None:
    """Add papers to the open EndNote library by PMID."""
    try:
        file_text = pmid_file.read_text(encoding="utf-8") if pmid_file else ""
        wanted = library.collect_pmids(list(pmids), file_text)
        if not wanted:
            raise CliError("no_pmids", "Give at least one PMID, or use --file.")
        report = library.add_pmids(wanted)
    except CliError as err:
        _fail(ctx, err)
        return
    _emit(ctx, {"ok": True, **report.to_dict()}, _format_add(report))
    if not report.added and not report.skipped:
        ctx.exit(1)


@cli.command("find")
@click.argument("pmids", nargs=-1, required=True)
@json_option
@click.pass_context
def find_command(ctx: click.Context, pmids: tuple[str, ...]) -> None:
    """Show the EndNote record for each PMID."""
    try:
        found, warnings = library.find_pmids(library.collect_pmids(list(pmids)))
    except CliError as err:
        _fail(ctx, err)
        return
    payload = {"ok": True, "records": {p: (r.to_dict() if r else None) for p, r in found.items()}, "warnings": warnings}
    lines = [f"{p}  {r.citation}  {r.title}" if r else f"{p}  not found" for p, r in found.items()]
    lines += [f"Warning: {w}" for w in warnings]
    _emit(ctx, payload, "\n".join(lines))


def _format_link(p: dict) -> str:
    lines = [
        f"Markers: {p['markers_found']} found, {p['markers_linked']} linked, {len(p['markers_left'])} left (still yellow).",
        f"PMIDs: {len(p['pmids_added'])} added to {p['library']}, "
        f"{len(p['pmids_already_in_library'])} already there, {len(p['failed'])} failed.",
    ]
    lines += [f"  ! {f['pmid']}  {f['reason']}" for f in p["failed"]]
    lines += [f"  left: {m}" for m in p["markers_left"]]
    lines += [f"Warning: {w}" for w in p["warnings"]]
    if p["output"]:
        lines.append(f"Written: {p['output']}")
        lines.append(
            f"Next: open it in Word, keep only {p['library']} open in EndNote, "
            "and click EndNote > Update Citations and Bibliography."
        )
    else:
        lines.append("No PMID could be added. No file written.")
    return "\n".join(lines)


@cli.command()
@click.argument("docx", type=click.Path(path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Output file. Default: <name>_LINKED.docx next to the input.")
@click.option("--overwrite", is_flag=True, help="Replace the output file if it exists.")
@json_option
@click.pass_context
def link(ctx: click.Context, docx: Path, output: Path | None, overwrite: bool) -> None:
    """Turn [PMID ...] markers in a Word file into EndNote temporary citations."""
    src = docx
    dst = output or src.with_name(f"{src.stem}_LINKED.docx")
    try:
        if not src.is_file():
            raise CliError("bad_docx", f"{src} is not a Word .docx file.")
        if dst.resolve() == src.resolve() or (dst.exists() and os.path.samefile(src, dst)):
            raise CliError("output_is_input", "The output file must be different from the input file.")
        if dst.exists() and not overwrite:
            raise CliError("output_exists", f"{dst} exists. Use --overwrite to replace it.")
        if not dst.parent.is_dir():
            raise CliError("bad_output", f"The folder {dst.parent} does not exist.")
        scan = word_link.scan_docx(src)
        if not scan.markers:
            _emit(ctx, {"ok": True, "input": str(src), "output": None, "markers_found": 0},
                  f"No [PMID ...] markers found in {src.name}. No file written.")
            return
        report = library.add_pmids(scan.pmids)
        citations = {r.pmid: r.citation for r in report.added + report.skipped}
        result = word_link.link_docx(src, dst, citations) if citations else None
    except CliError as err:
        _fail(ctx, err)
        return
    warnings = list(report.warnings)
    if scan.has_tracked_changes:
        warnings.append(TRACKED_WARNING)
    payload = {
        "ok": True,
        "library": report.library,
        "input": str(src),
        "output": str(dst) if result else None,
        "markers_found": len(scan.markers),
        "markers_linked": result.markers_linked if result else 0,
        "markers_left": result.markers_left if result else list(scan.texts),
        "pmids_added": [r.pmid for r in report.added],
        "pmids_already_in_library": [r.pmid for r in report.skipped],
        "failed": report.failed,
        "warnings": warnings,
    }
    _emit(ctx, payload, _format_link(payload))
    if result is None:
        ctx.exit(1)


@cli.command("repl")
@click.pass_context
def repl_command(ctx: click.Context) -> None:
    """Start the interactive shell (also the default with no command)."""
    _run_repl(ctx)


def _run_repl(ctx: click.Context) -> None:
    from cli_anything.endnote.utils.repl_skin import ReplSkin

    skin = ReplSkin("endnote", version=__version__)
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
