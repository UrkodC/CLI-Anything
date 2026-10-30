"""cli-anything-snapgene: read SnapGene files, find sites and ORFs, export, align, and open them in SnapGene."""
from __future__ import annotations

import dataclasses
import json
import re
import shlex
import sys
from pathlib import Path

import click

from cli_anything.snapgene import __version__
from cli_anything.snapgene.core import align as align_core
from cli_anything.snapgene.core import assemble as assemble_core
from cli_anything.snapgene.core import convert, cutting, dnafile
from cli_anything.snapgene.core import edit as edit_core
from cli_anything.snapgene.core import enzymes as enzyme_core
from cli_anything.snapgene.core import gateway as gateway_core
from cli_anything.snapgene.core import gblock as gblock_core
from cli_anything.snapgene.core import pcr as pcr_core
from cli_anything.snapgene.core import infusion as infusion_core
from cli_anything.snapgene.core import search as search_core, seqtools
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.utils import snapgene_backend

PROG = "cli-anything-snapgene"
REPL_COMMANDS = {
    "status": "Show the SnapGene install and its aligners",
    "info FILE": "Length, topology, counts and notes of a .dna/.rna/.prot file",
    "features FILE [--type T]": "List features",
    "primers FILE": "List primers and where they bind",
    "seq FILE [--start N --end M | --feature NAME] [--revcomp]": "Print bases",
    "translate FILE --feature NAME": "Translate a CDS (or --start/--end/--strand)",
    "orfs FILE [--min-aa 75]": "Open reading frames on both strands",
    "enzymes FILE [--enzyme EcoRI,BamHI] [--max-cuts 1]": "Restriction sites",
    "find FILE MOTIF": "Find a DNA motif (IUPAC) on both strands",
    "cut FILE --enzyme EcoRI [--enzyme XhoI]": "Cut with enzymes and show the ends",
    "infusion VECTOR --enzyme E --insert FILE": "In-Fusion primers and the product plasmid",
    "gateway DEST --entry ENTRY": "Gateway LR: swap the ccdB cassette for your insert",
    "gblock FILE [--feature NAME] [--flank5 S]": "Check a fragment before you order it",
    "edit FILE --at N --sequence ACGT": "Put bases in, take them out, or swap them",
    "annotate FILE --name N --range A-B": "Add a feature",
    "pcr FILE (--feature NAME | --range A-B)": "Design PCR primers for a region",
    "primer-check FILE PRIMER": "Where a primer binds, with Tm and GC",
    "export FILE [--format genbank|fasta] [-o OUT]": "Write GenBank or FASTA",
    "align FILE FILE ... [--tool clustalo|muscle] [-o OUT]": "Align with SnapGene's aligners",
    "search FOLDER [--name T] [--feature T] [--seq MOTIF]": "Find files in a folder",
    "open FILE": "Open a file in the SnapGene app",
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


def _json(ctx: click.Context) -> bool:
    root = ctx.find_root()
    return bool((ctx.obj or {}).get("json") or (root.obj or {}).get("json"))


def _emit(ctx: click.Context, payload: dict, human: str) -> None:
    if _json(ctx):
        click.echo(json.dumps({"ok": True, **payload}, indent=2, ensure_ascii=False))
    else:
        click.echo(human)


def _fail(ctx: click.Context, err: CliError) -> None:
    if _json(ctx):
        click.echo(json.dumps({"ok": False, "error": err.message, "code": err.code}, ensure_ascii=False))
    else:
        click.echo(f"Error: {err.message}", err=True)
    ctx.exit(1)


def _table(headers: list[str], rows: list[list]) -> str:
    cells = [headers] + [[str(c) for c in row] for row in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _range(start: int, end: int) -> str:
    return f"{start}-{end}"


@click.group(invoke_without_command=True)
@json_option
@click.version_option(__version__, prog_name=PROG)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Read SnapGene files, find sites and ORFs, export, align, and open them in SnapGene."""
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand is None:
        _run_repl(ctx)


@cli.command()
@json_option
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show the SnapGene install, its version, and its bundled aligners."""
    try:
        info = snapgene_backend.status()
    except CliError as err:
        _fail(ctx, err)
        return
    tools = ", ".join(f"{k} {'yes' if v else 'missing'}" for k, v in info["tools"].items())
    human = (f"SnapGene {info['version']} at {info['app']}\n"
             f"Running: {'yes' if info['running'] else 'no'}\nAligners: {tools}")
    _emit(ctx, info, human)


@cli.command()
@file_argument
@json_option
@click.pass_context
def info(ctx: click.Context, file: Path) -> None:
    """Show length, topology, feature and primer counts, and notes."""
    try:
        sg = dnafile.load(file)
    except CliError as err:
        _fail(ctx, err)
        return
    s = sg.summary()
    unit = "aa" if sg.kind == "protein" else "bp"
    lines = [f"{s['name']}  ({sg.kind}, {s['length']} {unit}, {s['topology']})",
             f"Features: {s['feature_count']}   Primers: {s['primer_count']}"]
    if sg.kind == "dna":
        methyl = [k for k, v in s["methylation"].items() if v]
        lines.append(f"Strands: {s['strandedness']}   Methylation: {', '.join(methyl) or 'none'}")
    lines += [f"{k}: {v}" for k, v in s["notes"].items()]
    _emit(ctx, s, "\n".join(lines))


@cli.command()
@file_argument
@click.option("--type", "ftype", default=None, help="Only features of this type (for example CDS, promoter).")
@json_option
@click.pass_context
def features(ctx: click.Context, file: Path, ftype: str | None) -> None:
    """List the features of a file."""
    try:
        sg = dnafile.load(file)
    except CliError as err:
        _fail(ctx, err)
        return
    feats = [f for f in sg.features if not ftype or f.type.casefold() == ftype.casefold()]
    rows = [[f.index, f.name, f.type, _range(f.start, f.end), f.strand, len(f.segments)] for f in feats]
    human = _table(["#", "name", "type", "range", "strand", "segments"], rows) if rows else "No features."
    _emit(ctx, {"file": str(file), "features": [f.to_dict() for f in feats]}, human)


@cli.command()
@file_argument
@json_option
@click.pass_context
def primers(ctx: click.Context, file: Path) -> None:
    """List primers and their binding sites."""
    try:
        sg = dnafile.load(file)
    except CliError as err:
        _fail(ctx, err)
        return
    rows = []
    for p in sg.primers:
        sites = ", ".join(f"{_range(s.start, s.end)}({s.strand})" for s in p.sites) or "does not bind"
        rows.append([p.index, p.name, len(p.sequence), p.sequence, sites])
    human = _table(["#", "name", "len", "sequence", "binds"], rows) if rows else "No primers."
    _emit(ctx, {"file": str(file), "primers": [p.to_dict() for p in sg.primers]}, human)


def _pick_range(sg: dnafile.SnapGeneFile, start, end, feature, feature_index):
    """Return (sequence in reading direction, start, end, strand, feature or None)."""
    if feature is not None or feature_index is not None:
        feat = dnafile.find_feature(sg, feature, feature_index)
        return seqtools.feature_sequence(sg, feat), feat.start, feat.end, feat.strand, feat
    if start is None or end is None:
        if start is None and end is None:
            return sg.sequence, 1, sg.length, "+", None
        raise CliError("bad_range", "Give both --start and --end.")
    return seqtools.subseq(sg, start, end), start, end, "+", None


@cli.command()
@file_argument
@click.option("--start", type=int, default=None, help="First base (1-based).")
@click.option("--end", type=int, default=None, help="Last base (1-based). Before --start crosses the origin.")
@click.option("--feature", default=None, help="Feature name. Gives the bases in the feature's direction.")
@click.option("--feature-index", type=int, default=None, help="Feature number from the features command.")
@click.option("--revcomp", is_flag=True, help="Reverse complement the result.")
@json_option
@click.pass_context
def seq(ctx: click.Context, file: Path, start, end, feature, feature_index, revcomp: bool) -> None:
    """Print the sequence, a range, or a feature."""
    try:
        sg = dnafile.load(file)
        bases, a, b, strand, feat = _pick_range(sg, start, end, feature, feature_index)
        if revcomp:
            if sg.kind == "protein":
                raise CliError("not_dna", f"{sg.name} is a protein file. --revcomp needs DNA or RNA.")
            bases = seqtools.revcomp(bases)
            strand = {"+": "-", "-": "+"}.get(strand, strand)
    except CliError as err:
        _fail(ctx, err)
        return
    label = f"{sg.name} {_range(a, b)} ({strand})" + (f" {feat.name}" if feat else "")
    payload = {"file": str(file), "start": a, "end": b, "strand": strand, "length": len(bases),
               "feature": feat.name if feat else None, "sequence": bases}
    _emit(ctx, payload, f">{label}\n{bases}")


@cli.command()
@file_argument
@click.option("--feature", default=None, help="CDS feature name.")
@click.option("--feature-index", type=int, default=None, help="Feature number from the features command.")
@click.option("--start", type=int, default=None, help="First base (1-based).")
@click.option("--end", type=int, default=None, help="Last base (1-based).")
@click.option("--strand", type=click.Choice(["+", "-"]), default="+", help="Strand for --start/--end.")
@click.option("--table", type=int, default=None, help="NCBI genetic code. Default: the feature's, else 1.")
@json_option
@click.pass_context
def translate(ctx: click.Context, file: Path, feature, feature_index, start, end, strand, table) -> None:
    """Translate a feature or a range to protein."""
    try:
        sg = dnafile.load(file)
        if sg.kind == "protein":
            raise CliError("not_dna", f"{sg.name} is already a protein file.")
        if feature is None and feature_index is None and (start is None or end is None):
            raise CliError("bad_range", "Give --feature, --feature-index, or both --start and --end.")
        bases, a, b, used_strand, feat = _pick_range(sg, start, end, feature, feature_index)
        if feat is not None:
            bases = seqtools.coding_sequence(sg, feat)
        elif strand == "-":
            bases, used_strand = seqtools.revcomp(bases), "-"
        code = table or seqtools.translation_table(feat)
        protein = seqtools.translate(bases.replace("U", "T"), code)
    except CliError as err:
        _fail(ctx, err)
        return
    saved = seqtools.snapgene_translation(feat) if feat else None
    payload = {"file": str(file), "feature": feat.name if feat else None, "start": a, "end": b,
               "strand": used_strand, "table": code, "length_aa": len(protein.rstrip("*")),
               "protein": protein, "matches_snapgene": (saved == protein) if saved else None}
    lines = [f">{sg.name} {_range(a, b)} ({used_strand})" + (f" {feat.name}" if feat else ""), protein]
    if saved:
        lines.append("Matches the translation saved by SnapGene." if saved == protein
                     else "Warning: differs from the translation saved by SnapGene.")
    _emit(ctx, payload, "\n".join(lines))


@cli.command()
@file_argument
@click.option("--min-aa", type=int, default=75, show_default=True, help="Shortest ORF, in amino acids.")
@click.option("--table", type=int, default=1, show_default=True, help="NCBI genetic code.")
@json_option
@click.pass_context
def orfs(ctx: click.Context, file: Path, min_aa: int, table: int) -> None:
    """Find open reading frames (ATG to stop) on both strands."""
    try:
        sg = dnafile.load(file)
        found = seqtools.find_orfs(sg, min_aa, table)
    except CliError as err:
        _fail(ctx, err)
        return
    rows = [[_range(o["start"], o["end"]), o["strand"], o["length_aa"], o["protein"][:20] + "..."] for o in found]
    human = _table(["range", "strand", "aa", "protein"], rows) if rows else f"No ORFs of {min_aa} aa or longer."
    _emit(ctx, {"file": str(file), "min_aa": min_aa, "orfs": found}, human)


@cli.command()
@file_argument
@click.option("--enzyme", "names", multiple=True, help="Enzyme name. Repeat it, or use commas: EcoRI,BamHI.")
@click.option("--max-cuts", type=int, default=1, show_default=True, help="Without --enzyme: most cuts to show.")
@click.option("--min-site", type=int, default=6, show_default=True, help="Without --enzyme: shortest site.")
@click.option("--all-suppliers", is_flag=True, help="Without --enzyme: include enzymes nobody sells.")
@json_option
@click.pass_context
def enzymes(ctx: click.Context, file: Path, names, max_cuts: int, min_site: int, all_suppliers: bool) -> None:
    """Show restriction sites. Default: unique cutters with sites of 6+ bases."""
    wanted = [n for item in names for n in item.split(",") if n.strip()]
    try:
        sg = dnafile.load(file)
        rows = enzyme_core.cut_sites(sg, wanted or None, max_cuts, min_site, not all_suppliers)
    except CliError as err:
        _fail(ctx, err)
        return
    table_rows = [[r["enzyme"], r["site"], r["cuts"], ", ".join(map(str, r["positions"])) or "-"] for r in rows]
    human = _table(["enzyme", "site", "cuts", "cut after base"], table_rows) if rows else "No enzymes found."
    _emit(ctx, {"file": str(file), "circular": sg.circular, "enzymes": rows}, human)


@cli.command("find")
@file_argument
@click.argument("motif")
@json_option
@click.pass_context
def find_command(ctx: click.Context, file: Path, motif: str) -> None:
    """Find a motif on both strands. DNA motifs may use IUPAC codes (N, R, Y ...)."""
    try:
        sg = dnafile.load(file)
        hits = seqtools.find_motif(sg, motif)
    except CliError as err:
        _fail(ctx, err)
        return
    rows = [[_range(h["start"], h["end"]), h["strand"], h["match"]] for h in hits]
    human = _table(["range", "strand", "match"], rows) if rows else f"'{motif}' not found."
    _emit(ctx, {"file": str(file), "motif": motif.upper(), "hits": hits}, human)


@cli.command()
@file_argument
@click.option("--format", "fmt", type=click.Choice(list(convert.FORMATS)), default="genbank", show_default=True)
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Output file. Default: same name and folder, new extension.")
@click.option("--overwrite", is_flag=True, help="Replace the output file if it exists.")
@json_option
@click.pass_context
def export(ctx: click.Context, file: Path, fmt: str, output: Path | None, overwrite: bool) -> None:
    """Write a SnapGene file as GenBank (with features and primers) or FASTA."""
    try:
        sg = dnafile.load(file)
        result = convert.export(sg, output or convert.default_output(file, fmt), fmt, overwrite)
    except CliError as err:
        _fail(ctx, err)
        return
    _emit(ctx, result, f"Written: {result['output']} ({result['format']}, {result['features']} features)")


@cli.command("align")
@click.argument("files", nargs=-1, required=True, type=click.Path(path_type=Path))
@click.option("--tool", type=click.Choice(list(snapgene_backend.TOOLS)), default="clustalo", show_default=True)
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="Write the aligned FASTA here.")
@click.option("--overwrite", is_flag=True, help="Replace the output file if it exists.")
@json_option
@click.pass_context
def align_command(ctx: click.Context, files, tool: str, output: Path | None, overwrite: bool) -> None:
    """Align sequences with Clustal Omega or MUSCLE from the SnapGene install."""
    try:
        result = align_core.align(list(files), tool, output, overwrite)
    except CliError as err:
        _fail(ctx, err)
        return
    rows = [[i["a"], i["b"], f"{i['identity_pct']}%", f"{i['matches']}/{i['columns']}"] for i in result["identity"]]
    lines = [f"{len(result['sequences'])} sequences aligned with {tool}. Alignment length: {result['length']}.",
             _table(["a", "b", "identity", "matches"], rows)]
    lines.append(f"Written: {result['output']}" if result["output"] else "Use -o FILE to save the alignment.")
    _emit(ctx, result, "\n".join(lines))


@cli.command("search")
@click.argument("folder", type=click.Path(path_type=Path))
@click.option("--name", default=None, help="Text in the file name.")
@click.option("--feature", default=None, help="Text in a feature or primer name, for example mCherry.")
@click.option("--seq", "motif", default=None, help="DNA motif (IUPAC) on either strand.")
@json_option
@click.pass_context
def search_command(ctx: click.Context, folder: Path, name, feature, motif) -> None:
    """Find .dna, .rna and .prot files in a folder (and its subfolders)."""
    try:
        result = search_core.search(folder, name, feature, motif)
    except CliError as err:
        _fail(ctx, err)
        return
    lines = [f"Scanned {result['files_scanned']} files. Matches: {len(result['matches'])}."]
    for m in result["matches"]:
        lines.append(f"  {m['path']}")
        for f in m.get("features", []):
            lines.append(f"      {f}")
        if "motif_hits" in m:
            spots = ", ".join(f"{h['start']}({h['strand']})" for h in m["motif_hits"][:10])
            lines.append(f"      motif at {spots}" + (" ..." if len(m["motif_hits"]) > 10 else ""))
    lines += [f"  could not read: {e['path']} ({e['error']})" for e in result["errors"]]
    _emit(ctx, result, "\n".join(lines))


@cli.command("open")
@click.argument("file", type=click.Path(path_type=Path))
@json_option
@click.pass_context
def open_command(ctx: click.Context, file: Path) -> None:
    """Open a file in the SnapGene app (.dna, .gb, .fasta, .ab1 and other formats SnapGene reads)."""
    try:
        result = snapgene_backend.open_in_app(file)
    except CliError as err:
        _fail(ctx, err)
        return
    _emit(ctx, result, f"Opened in SnapGene: {result['opened']}")


def _span(option: str, value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*(?:-|\.\.|:)\s*(\d+)\s*", value or "")
    if not match:
        raise CliError("bad_range", f"{option} must look like 100-200. Got '{value}'.")
    return int(match.group(1)), int(match.group(2))


def _enzyme_names(values) -> list[str]:
    return [n.strip() for item in values for n in item.split(",") if n.strip()]


def _write_product(product: dnafile.SnapGeneFile, output: Path, overwrite: bool) -> dict:
    return convert.export(product, output, "genbank", overwrite)


def _insert_piece(insert: Path | None, feature: str | None, feature_index: int | None,
                  rng: str | None, bases: str | None, flip: bool,
                  given_name: str | None = None) -> tuple[assemble_core.Piece, str, list[str]]:
    """The piece of DNA to put into the vector, plus its name and any notes."""
    notes: list[str] = []
    if bases:
        if insert:
            raise CliError("bad_request", "Give --insert or --insert-seq, not both.")
        cleaned = edit_core.clean_bases(bases)
        name = given_name or "insert"
        return assemble_core.literal(cleaned, name), name, notes
    if insert is None:
        raise CliError("bad_request", "Give --insert FILE or --insert-seq BASES.")
    sg = dnafile.load(insert)
    if sg.kind != "dna":
        raise CliError("not_dna", f"{sg.name} is not a DNA file.")
    if feature is not None or feature_index is not None:
        feat = dnafile.find_feature(sg, feature, feature_index)
        start, end = edit_core.feature_span(sg, feat)
        name = feat.name
        if feat.strand == "-" and not flip:
            notes.append(f"{name} is on the minus strand of {sg.name}. "
                         "Add --insert-revcomp to clone it the other way round.")
    elif rng:
        start, end = _span("--insert-range", rng)
        name = f"{sg.name} {start}-{end}"
    else:
        start, end, name = 1, sg.length, sg.name
        if sg.circular:
            notes.append(f"{sg.name} is circular, so the whole plasmid is being used as the insert.")
    name = given_name or name
    piece = assemble_core.take(sg, start, end, flip, label=name)
    return piece, name, notes


@cli.command("cut")
@file_argument
@click.option("--enzyme", "names", multiple=True, required=True,
              help="Enzyme name. Repeat it, or use commas: EcoRI,XhoI.")
@json_option
@click.pass_context
def cut_command(ctx: click.Context, file: Path, names) -> None:
    """Cut a plasmid with enzymes and show the pieces and their ends."""
    try:
        sg = dnafile.load(file)
        cuts = cutting.find_cuts(sg, _enzyme_names(names))
        pieces = cutting.fragments(sg, cuts)
    except CliError as err:
        _fail(ctx, err)
        return
    cut_rows = [[c.enzyme, _range(c.site_start, c.site_end), c.top, c.kind, c.overhang or "-"] for c in cuts]
    piece_rows = [[_range(p["from"], p["to"]), p["length"], f"{p['left_enzyme'] or '-'} ({p['left_end']})",
                   f"{p['right_enzyme'] or '-'} ({p['right_end']})"] for p in pieces]
    human = "\n".join([
        _table(["enzyme", "site", "cut after", "end", "overhang"], cut_rows) if cut_rows else "No cuts.",
        "",
        _table(["piece", "bp", "left end", "right end"], piece_rows),
    ])
    payload = {"file": str(file), "circular": sg.circular,
               "cuts": [c.to_dict() for c in cuts], "pieces": pieces}
    _emit(ctx, payload, human)


@cli.command("infusion")
@click.argument("vector", type=click.Path(path_type=Path))
@click.option("--enzyme", "names", multiple=True, help="Enzyme(s) that open the vector. One or two cuts.")
@click.option("--at", type=int, default=None, help="Open the vector after this base instead (blunt, inverse PCR).")
@click.option("--remove", default=None, help="Take these bases out and put the insert there, for example 400-900.")
@click.option("--keep", type=click.Choice(["larger", "smaller", "inside", "outside"]), default="larger",
              show_default=True, help="With two cuts: which piece is the backbone.")
@click.option("--insert", type=click.Path(path_type=Path), default=None, help="File holding the insert.")
@click.option("--insert-feature", default=None, help="Use this feature of the insert file.")
@click.option("--insert-feature-index", type=int, default=None, help="Feature number of the insert file.")
@click.option("--insert-range", default=None, help="Use these bases of the insert file, for example 100-900.")
@click.option("--insert-seq", default=None, help="Insert these bases, typed in.")
@click.option("--insert-name", default=None, help="Name for the insert, used for the feature and the primers.")
@click.option("--insert-revcomp", is_flag=True, help="Put the insert in the other way round.")
@click.option("--overlap", type=int, default=infusion_core.OVERLAP, show_default=True,
              help="Bases of homology. Takara uses 15; Gibson and HiFi use 20-25.")
@click.option("--restore-sites/--no-restore-sites", default=True, show_default=True,
              help="Add the few bases that rebuild the enzyme site at each join.")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Where to write the product as GenBank.")
@click.option("--no-product", is_flag=True, help="Only design the primers; write no product file.")
@click.option("--fragment-out", type=click.Path(path_type=Path), default=None,
              help="Write the fragment to order (the PCR product) as FASTA.")
@click.option("--overwrite", is_flag=True, help="Replace the output files if they exist.")
@json_option
@click.pass_context
def infusion_command(ctx: click.Context, vector: Path, names, at, remove, keep, insert, insert_feature,
                     insert_feature_index, insert_range, insert_seq, insert_name, insert_revcomp, overlap,
                     restore_sites, output, no_product, fragment_out, overwrite) -> None:
    """Design Takara In-Fusion primers and build the product plasmid.

    The vector is opened with enzymes (--enzyme), at one point (--at), or by
    cutting a part out (--remove). Raise --overlap to 20-25 for Gibson or HiFi.
    """
    try:
        sg = dnafile.load(vector)
        piece, insert_name, notes = _insert_piece(insert, insert_feature, insert_feature_index,
                                                  insert_range, insert_seq, insert_revcomp, insert_name)
        opened = infusion_core.open_vector(
            sg,
            _enzyme_names(names) or None,
            at,
            _span("--remove", remove) if remove else None,
            keep,
        )
        result = infusion_core.design(opened, piece, overlap, restore_sites,
                                      Path(f"{sg.name}-{insert_name}"), insert_name)
        product = result.pop("_product")
        result["warnings"] = notes + result["warnings"]
        if not no_product:
            out = output or vector.with_name(f"{vector.stem}-{insert_name}.gb")
            result["product"].update(_write_product(product, out, overwrite))
        if fragment_out:
            if fragment_out.exists() and not overwrite:
                raise CliError("output_exists", f"{fragment_out} exists. Use --overwrite to replace it.")
            if not fragment_out.parent.is_dir():
                raise CliError("bad_output", f"The folder {fragment_out.parent} does not exist.")
            fragment_out.write_text(f">{sg.name}-{insert_name} In-Fusion fragment "
                                    f"{result['fragment']['length']} bp\n{result['fragment_sequence']}\n")
            result["fragment"]["output"] = str(fragment_out)
    except CliError as err:
        _fail(ctx, err)
        return
    rows = [[p["name"], p["length"], p["tm"], f"{p['gc_percent']}%", p["sequence"]] for p in result["primers"]]
    lines = [
        f"{sg.name}: {result['opened']['how']}",
        f"Insert: {insert_name}, {result['insert']['length']} bp"
        + (" (reverse complement)" if insert_revcomp else ""),
        f"Homology: {overlap} bases. Tm is of the gene-specific part only.",
        "",
        _table(["primer", "len", "Tm", "GC", "sequence"], rows),
        "",
        f"Fragment to order: {result['fragment']['length']} bp, GC {result['fragment']['gc_percent']}%",
    ]
    if result["fragment"].get("output"):
        lines.append(f"Fragment written: {result['fragment']['output']}")
    if result["product"].get("output"):
        lines.append(f"Product: {result['product']['length']} bp, "
                     f"{result['product']['features']} features -> {result['product']['output']}")
    else:
        lines.append(f"Product would be {result['product']['length']} bp (no file written).")
    everything = result["warnings"] + result["fragment"]["synthesis_warnings"]
    lines.append("")
    lines.append("All Takara and IDT checks passed." if not everything
                 else "\n".join(f"Check: {w}" for w in everything))
    _emit(ctx, result, "\n".join(lines))


@cli.command("gateway")
@click.argument("destination", type=click.Path(path_type=Path))
@click.option("--entry", required=True, type=click.Path(path_type=Path),
              help="The entry clone, with attL1 and attL2 around the insert.")
@click.option("--name", "insert_name", default=None, help="Name for the insert in the product.")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Where to write the expression clone as GenBank.")
@click.option("--no-product", is_flag=True, help="Report the reaction only; write no file.")
@click.option("--overwrite", is_flag=True, help="Replace the output file if it exists.")
@json_option
@click.pass_context
def gateway_command(ctx: click.Context, destination: Path, entry: Path, insert_name,
                    output, no_product, overwrite) -> None:
    """Run a Gateway LR reaction: entry clone + destination vector.

    The part between attL1 and attL2 of the entry clone replaces the ccdB
    cassette between attR1 and attR2 of the destination vector, and each join
    becomes an attB site. The product keeps the destination's numbering.
    """
    try:
        dest_sg = dnafile.load(destination)
        entry_sg = dnafile.load(entry)
        name = insert_name or entry_sg.name
        result = gateway_core.lr(entry_sg, dest_sg, Path(f"{dest_sg.name}-{name}"), name)
        product = result.pop("_product")
        if not no_product:
            out = output or destination.with_name(f"{destination.stem}-{name}.gb")
            result["product"].update(_write_product(product, out, overwrite))
    except CliError as err:
        _fail(ctx, err)
        return
    rows = [[s["name"], s["from"], _range(*s["range"]), s["strand"], s["sequence"]]
            for s in result["att_sites"]]
    lines = [
        f"Entry clone: {entry_sg.name}, insert {result['insert']['length']} bp"
        + (" (read the other way round)" if result["insert"]["reversed"] else ""),
        f"Destination: {dest_sg.name}, backbone {result['destination']['backbone_bp']} bp",
        f"Taken out: {result['destination']['cassette_bp']} bp between "
        f"{result['destination']['sites'][0]['name']} and {result['destination']['sites'][1]['name']}"
        + (" (" + ", ".join(result["destination"]["cassette_features"]) + ")"
           if result["destination"]["cassette_features"] else ""),
        "",
        _table(["site", "made from", "where", "strand", "sequence"], rows),
        "",
        f"Insert lands at {_range(*result['insert']['range_in_product'])}.",
    ]
    if result["product"].get("output"):
        lines.append(f"Product: {result['product']['length']} bp, "
                     f"{result['product']['features']} features -> {result['product']['output']}")
    else:
        lines.append(f"Product would be {result['product']['length']} bp (no file written).")
    lines.append("")
    lines.append("Nothing to report." if not result["warnings"]
                 else "\n".join(f"Check: {w}" for w in result["warnings"]))
    _emit(ctx, result, "\n".join(lines))


@cli.command("gblock")
@file_argument
@click.option("--feature", default=None, help="Use this feature.")
@click.option("--feature-index", type=int, default=None, help="Feature number from the features command.")
@click.option("--range", "rng", default=None, help="Use these bases, for example 100-900.")
@click.option("--flank5", default="", help="Bases to add at the 5' end, for example an In-Fusion tail.")
@click.option("--flank3", default="", help="Bases to add at the 3' end.")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="Write the fragment as FASTA.")
@click.option("--overwrite", is_flag=True, help="Replace the output file if it exists.")
@json_option
@click.pass_context
def gblock_command(ctx: click.Context, file: Path, feature, feature_index, rng, flank5, flank3,
                   output, overwrite) -> None:
    """Check a fragment against IDT's gBlocks limits before you order it."""
    try:
        sg = dnafile.load(file)
        if feature is not None or feature_index is not None:
            feat = dnafile.find_feature(sg, feature, feature_index)
            start, end = edit_core.feature_span(sg, feat)
            name = feat.name
        elif rng:
            start, end = _span("--range", rng)
            name = f"{sg.name} {start}-{end}"
        else:
            start, end, name = 1, sg.length, sg.name
        core = seqtools.subseq(sg, start, end)
        left = edit_core.clean_bases(flank5) if flank5 else ""
        right = edit_core.clean_bases(flank3) if flank3 else ""
        result = gblock_core.check(left + core + right, name)
        result.update({"file": str(file), "name": name, "range": [start, end],
                       "flank5": left, "flank3": right})
        if output:
            if output.exists() and not overwrite:
                raise CliError("output_exists", f"{output} exists. Use --overwrite to replace it.")
            if not output.parent.is_dir():
                raise CliError("bad_output", f"The folder {output.parent} does not exist.")
            output.write_text(f">{name} {result['length']} bp\n{result['sequence']}\n")
            result["output"] = str(output)
    except CliError as err:
        _fail(ctx, err)
        return
    lines = [f"{name}: {result['length']} bp, GC {result['gc_percent']}%"]
    if result.get("output"):
        lines.append(f"Written: {result['output']}")
    lines.append("Ready to order: inside IDT's gBlocks limits." if not result["synthesis_warnings"]
                 else "\n".join(f"Check: {w}" for w in result["synthesis_warnings"]))
    _emit(ctx, result, "\n".join(lines))


@cli.command("edit")
@file_argument
@click.option("--at", type=int, default=None, help="Put the bases in after this base (0 for the very start).")
@click.option("--range", "rng", default=None, help="Replace or delete these bases, for example 400-900.")
@click.option("--feature", default=None, help="Replace or delete this feature's span.")
@click.option("--feature-index", type=int, default=None, help="Feature number from the features command.")
@click.option("--sequence", default="", help="The bases to put in. Leave it out to delete.")
@click.option("--label", default=None, help="Name a feature over the bases you put in.")
@click.option("--label-type", default="misc_feature", show_default=True, help="Type of that feature.")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="Where to write the GenBank.")
@click.option("--overwrite", is_flag=True, help="Replace the output file if it exists.")
@json_option
@click.pass_context
def edit_command(ctx: click.Context, file: Path, at, rng, feature, feature_index, sequence, label,
                 label_type, output, overwrite) -> None:
    """Put bases in, take bases out, or swap a part. Writes a new GenBank file."""
    try:
        sg = dnafile.load(file)
        start = end = None
        if feature is not None or feature_index is not None:
            feat = dnafile.find_feature(sg, feature, feature_index)
            start, end = edit_core.feature_span(sg, feat)
        elif rng:
            start, end = _span("--range", rng)
        out = output or file.with_name(f"{file.stem}-edited.gb")
        result = edit_core.edit(sg, sequence, at, start, end, label, label_type, Path(f"{sg.name}-edited"))
        product = result.pop("_product")
        result["protein_changes"] = edit_core.protein_check(sg, product)
        result.update(_write_product(product, out, overwrite))
    except CliError as err:
        _fail(ctx, err)
        return
    lines = [f"{sg.name}: {result['change']}.",
             f"Length: {result['length_before']} bp -> {result['length_after']} bp.",
             f"Written: {result['output']} ({result['features']} features, {result['primers_kept']} primers)"]
    if result["dropped_features"]:
        lines.append("Features cut in half and left out: " + ", ".join(result["dropped_features"]))
    lines += [f"Check: {w}" for w in result["protein_changes"]]
    _emit(ctx, result, "\n".join(lines))


@cli.command("annotate")
@file_argument
@click.option("--name", required=True, help="Name of the new feature.")
@click.option("--range", "rng", required=True, help="Where it sits, for example 400-900.")
@click.option("--type", "ftype", default="misc_feature", show_default=True, help="Feature type, for example CDS.")
@click.option("--strand", type=click.Choice(["+", "-", "none"]), default="+", show_default=True)
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="Where to write the GenBank.")
@click.option("--overwrite", is_flag=True, help="Replace the output file if it exists.")
@json_option
@click.pass_context
def annotate_command(ctx: click.Context, file: Path, name, rng, ftype, strand, output, overwrite) -> None:
    """Add one feature to a file and write the result as GenBank."""
    try:
        sg = dnafile.load(file)
        start, end = _span("--range", rng)
        product = edit_core.annotate(sg, name, start, end, ftype, strand)
        out = output or file.with_name(f"{file.stem}-annotated.gb")
        result = _write_product(product, out, overwrite)
        result.update({"added": {"name": name, "type": ftype, "strand": strand, "range": [start, end]},
                       "feature_count": len(product.features)})
    except CliError as err:
        _fail(ctx, err)
        return
    _emit(ctx, result, f"Added {ftype} '{name}' at {_range(start, end)} ({strand}).\n"
                       f"Written: {result['output']} ({result['feature_count']} features)")


def _target(sg: dnafile.SnapGeneFile, feature, feature_index, rng) -> tuple[int, int, str]:
    if feature is not None or feature_index is not None:
        feat = dnafile.find_feature(sg, feature, feature_index)
        start, end = edit_core.feature_span(sg, feat)
        return start, end, feat.name
    if not rng:
        raise CliError("bad_range", "Give --feature, --feature-index or --range.")
    start, end = _span("--range", rng)
    return start, end, f"{sg.name} {start}-{end}"


@cli.command("pcr")
@file_argument
@click.option("--feature", default=None, help="Amplify this feature.")
@click.option("--feature-index", type=int, default=None, help="Feature number from the features command.")
@click.option("--range", "rng", default=None, help="Amplify these bases, for example 400-900.")
@click.option("--context", type=int, default=pcr_core.CONTEXT, show_default=True,
              help="Bases each side of the target the primers may sit in.")
@click.option("--pairs", type=int, default=pcr_core.PAIRS, show_default=True, help="How many pairs to return.")
@click.option("--opt-tm", type=float, default=pcr_core.OPT_TM, show_default=True, help="Tm to aim for.")
@click.option("--min-tm", type=float, default=pcr_core.TM_RANGE[0], show_default=True)
@click.option("--max-tm", type=float, default=pcr_core.TM_RANGE[1], show_default=True)
@click.option("--min-product", type=int, default=None, help="Shortest product, in bp.")
@click.option("--max-product", type=int, default=None, help="Longest product, in bp.")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Write a GenBank copy with the primers on it.")
@click.option("--overwrite", is_flag=True, help="Replace the output file if it exists.")
@json_option
@click.pass_context
def pcr_command(ctx: click.Context, file: Path, feature, feature_index, rng, context, pairs, opt_tm,
                min_tm, max_tm, min_product, max_product, output, overwrite) -> None:
    """Design PCR primers that amplify a feature or a range (Primer3)."""
    try:
        sg = dnafile.load(file)
        start, end, name = _target(sg, feature, feature_index, rng)
        result = pcr_core.design(sg, start, end, context, pairs, opt_tm, (min_tm, max_tm),
                                 min_product=min_product, max_product=max_product)
        result["name"] = name
        if output:
            found = [
                dnafile.Primer(i, f"{name} {side[0].upper()}{row['pair']}", row[side]["sequence"], "",
                               [dnafile.BindingSite(row[side]["start"], row[side]["end"],
                                                    row[side]["strand"], row[side]["tm"])])
                for i, (row, side) in enumerate((r, s) for r in result["pairs"] for s in ("forward", "reverse"))
            ]
            copy = dataclasses.replace(sg, primers=found)
            result.update(convert.export(copy, output, "genbank", overwrite))
    except CliError as err:
        _fail(ctx, err)
        return
    rows = []
    for row in result["pairs"]:
        for side in ("forward", "reverse"):
            p = row[side]
            rows.append([row["pair"], side[0].upper(), p["length"], p["tm"], f"{p['gc_percent']}%",
                         _range(p["start"], p["end"]), row["product_bp"], p["sequence"]])
    lines = [f"{name}: {result['target_bp']} bp at {_range(start, end)}. "
             f"{len(result['pairs'])} pair(s). Tm from Primer3."]
    lines.append(_table(["#", "dir", "len", "Tm", "GC", "binds", "product", "sequence"], rows)
                 if rows else "No pairs found.")
    if result.get("output"):
        lines.append(f"Written: {result['output']}")
    lines += [f"Check: {w}" for w in result["warnings"]]
    _emit(ctx, result, "\n".join(lines))


@cli.command("primer-check")
@file_argument
@click.argument("primer")
@json_option
@click.pass_context
def primer_check_command(ctx: click.Context, file: Path, primer: str) -> None:
    """Show where a primer binds a file, with its Tm, GC and hairpin."""
    try:
        sg = dnafile.load(file)
        result = pcr_core.check_primer(sg, primer)
    except CliError as err:
        _fail(ctx, err)
        return
    sites = ", ".join(f"{_range(h['start'], h['end'])}({h['strand']})" for h in result["binding_sites"])
    lines = [f"{result['primer']} ({result['length']} bases)",
             f"Tm {result['tm']} C, GC {result['gc_percent']}%, "
             f"hairpin {result['hairpin_dg']} kcal/mol, self dimer {result['self_dimer_dg']} kcal/mol",
             f"Binds: {sites or 'nowhere'}"]
    lines += [f"Check: {w}" for w in result["warnings"]]
    _emit(ctx, result, "\n".join(lines))


@cli.command("repl")
@click.pass_context
def repl_command(ctx: click.Context) -> None:
    """Start the interactive shell (also the default with no command)."""
    _run_repl(ctx)


def _run_repl(ctx: click.Context) -> None:
    from cli_anything.snapgene.utils.repl_skin import ReplSkin

    skin = ReplSkin("snapgene", version=__version__)
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
