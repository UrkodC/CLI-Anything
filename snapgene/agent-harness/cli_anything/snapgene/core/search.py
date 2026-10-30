"""Find SnapGene files in a folder by file name, feature or primer name, or sequence."""
from __future__ import annotations

from pathlib import Path

from cli_anything.snapgene.core import dnafile, seqtools
from cli_anything.snapgene.errors import CliError

SUFFIXES = {".dna", ".rna", ".prot"}


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in SUFFIXES or path.name.startswith("."):
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts[:-1]):
            continue
        if path.is_file():
            yield path


def search(root: Path, name: str | None = None, feature: str | None = None, motif: str | None = None) -> dict:
    if not root.is_dir():
        raise CliError("no_folder", f"{root} is not a folder.")
    if not (name or feature or motif):
        raise CliError("no_criteria", "Give at least one of --name, --feature or --seq.")
    if motif:
        seqtools.motif_regex(motif, "dna")  # fail early on a bad motif
    scanned = 0
    matches, errors = [], []
    for path in iter_files(root):
        if name and name.casefold() not in path.stem.casefold():
            continue
        scanned += 1
        try:
            sg = dnafile.load(path)
        except CliError as err:
            errors.append({"path": str(path), "error": err.message})
            continue
        hit = {"path": str(path), "name": sg.name, "kind": sg.kind, "length": sg.length,
               "topology": "circular" if sg.circular else "linear"}
        if feature:
            wanted = feature.casefold()
            found = [f"{f.name} ({f.type} {f.start}-{f.end})" for f in sg.features if wanted in f.name.casefold()]
            found += [f"{p.name} (primer)" for p in sg.primers if wanted in p.name.casefold()]
            if not found:
                continue
            hit["features"] = found
        if motif:
            if not sg.is_nucleotide:
                continue
            hits = seqtools.find_motif(sg, motif)
            if not hits:
                continue
            hit["motif_hits"] = [{"start": h["start"], "end": h["end"], "strand": h["strand"]} for h in hits]
        matches.append(hit)
    return {"folder": str(root), "files_scanned": scanned, "matches": matches, "errors": errors}
