"""Multiple alignment with the aligners bundled in SnapGene.

Inputs can be SnapGene files or FASTA/GenBank files. Sequences get short
temporary IDs before the aligner runs, so names with spaces survive.
"""
from __future__ import annotations

import itertools
import tempfile
from pathlib import Path

from Bio import SeqIO

from cli_anything.snapgene.core import dnafile
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.utils import snapgene_backend

SNAPGENE_SUFFIXES = {".dna", ".rna", ".prot"}
BIO_FORMATS = {
    ".fa": "fasta", ".fasta": "fasta", ".fna": "fasta", ".faa": "fasta", ".fas": "fasta",
    ".gb": "genbank", ".gbk": "genbank", ".genbank": "genbank",
}
NUCLEOTIDES = set("ACGTUNRYSWKMBDHV-")


def _kind(seq: str) -> str:
    return "dna" if set(seq.upper()) <= NUCLEOTIDES else "protein"


def load_sequences(paths: list[Path]) -> list[dict]:
    out = []
    for path in paths:
        if not path.is_file():
            raise CliError("no_file", f"{path} does not exist.")
        suffix = path.suffix.lower()
        if suffix in SNAPGENE_SUFFIXES:
            sg = dnafile.load(path)
            kind = "protein" if sg.kind == "protein" else "dna"
            out.append({"name": sg.name, "sequence": sg.sequence.replace("U", "T"), "kind": kind})
        elif suffix in BIO_FORMATS:
            records = list(SeqIO.parse(path, BIO_FORMATS[suffix]))
            if not records:
                raise CliError("bad_file", f"{path} has no sequences.")
            for rec in records:
                seq = str(rec.seq).upper()
                out.append({"name": rec.id or path.stem, "sequence": seq, "kind": _kind(seq)})
        else:
            raise CliError(
                "unsupported_file",
                f"{path.name}: use .dna, .rna, .prot, FASTA (.fa, .fasta) or GenBank (.gb, .gbk) files.",
            )
    seen: dict[str, int] = {}
    for item in out:
        count = seen.get(item["name"], 0) + 1
        seen[item["name"]] = count
        if count > 1:
            item["name"] = f"{item['name']}_{count}"
    return out


def pairwise_identity(a: str, b: str) -> dict:
    columns = matches = 0
    for x, y in zip(a, b):
        if x == "-" and y == "-":
            continue
        columns += 1
        matches += x == y
    return {
        "matches": matches,
        "columns": columns,
        "identity_pct": round(100 * matches / columns, 2) if columns else 0.0,
    }


def align(paths: list[Path], tool: str = "clustalo", output: Path | None = None, overwrite: bool = False) -> dict:
    seqs = load_sequences(paths)
    if len(seqs) < 2:
        raise CliError("too_few_sequences", "Give at least two sequences to align.")
    kinds = {s["kind"] for s in seqs}
    if len(kinds) > 1:
        raise CliError("mixed_types", "Do not mix DNA and protein sequences in one alignment.")
    if output is not None:
        if output.exists() and not overwrite:
            raise CliError("output_exists", f"{output} exists. Use --overwrite to replace it.")
        if not output.parent.is_dir():
            raise CliError("bad_output", f"The folder {output.parent} does not exist.")
    kind = kinds.pop()
    with tempfile.TemporaryDirectory(prefix="snapgene-align-") as tmp:
        fasta_in, fasta_out = Path(tmp) / "in.fasta", Path(tmp) / "out.fasta"
        fasta_in.write_text("".join(f">s{i}\n{s['sequence']}\n" for i, s in enumerate(seqs)))
        snapgene_backend.run_aligner(tool, fasta_in, fasta_out, kind)
        aligned = {rec.id: str(rec.seq).upper() for rec in SeqIO.parse(fasta_out, "fasta")}
    if len(aligned) != len(seqs):
        raise CliError("tool_error", f"{tool} returned {len(aligned)} sequences, expected {len(seqs)}.")
    rows = [{"name": s["name"], "aligned": aligned[f"s{i}"]} for i, s in enumerate(seqs)]
    identity = []
    for x, y in itertools.combinations(rows, 2):
        identity.append({"a": x["name"], "b": y["name"], **pairwise_identity(x["aligned"], y["aligned"])})
    if output is not None:
        output.write_text("".join(f">{r['name']}\n{r['aligned']}\n" for r in rows))
    return {
        "tool": tool,
        "kind": kind,
        "length": len(rows[0]["aligned"]),
        "sequences": rows,
        "identity": identity,
        "output": str(output) if output else None,
    }
