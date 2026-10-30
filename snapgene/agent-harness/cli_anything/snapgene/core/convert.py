"""Write a loaded SnapGene file as GenBank or FASTA.

SnapGene's own exporter runs only from its menus, so this module builds the
records with Biopython. SnapGene opens the GenBank output with all features.
"""
from __future__ import annotations

import re
from pathlib import Path

from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import CompoundLocation, SimpleLocation, SeqFeature
from Bio.SeqRecord import SeqRecord

from cli_anything.snapgene.core.dnafile import SnapGeneFile
from cli_anything.snapgene.errors import CliError

FORMATS = {"genbank": ("genbank", ".gb"), "fasta": ("fasta", ".fasta")}
STRANDS = {"+": 1, "-": -1}
MOLECULE = {"dna": "DNA", "rna": "RNA", "protein": "protein"}


def _parts(start: int, end: int, n: int, strand: int | None) -> list[SimpleLocation]:
    if start <= end:
        return [SimpleLocation(start - 1, end, strand=strand)]
    return [SimpleLocation(start - 1, n, strand=strand), SimpleLocation(0, end, strand=strand)]


def _location(ranges: list[tuple[int, int]], n: int, strand: int | None):
    parts = [p for a, b in ranges for p in _parts(a, b, n, strand)]
    if strand == -1:
        parts.reverse()  # Biopython keeps compound parts in reading order
    return parts[0] if len(parts) == 1 else CompoundLocation(parts)


def _genbank_date(value: str | None) -> str | None:
    """SnapGene notes dates look like 2022.9.2. GenBank wants 02-SEP-2022."""
    m = re.fullmatch(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", value or "")
    if not m or not 1 <= int(m.group(2)) <= 12:
        return None
    months = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
    return f"{int(m.group(3)):02d}-{months[int(m.group(2)) - 1]}-{m.group(1)}"


def to_record(sg: SnapGeneFile, include_primers: bool = True) -> SeqRecord:
    locus = re.sub(r"[^A-Za-z0-9_.-]", "_", sg.name)[:16] or "sequence"
    record = SeqRecord(
        Seq(sg.sequence),
        id=locus,
        name=locus,
        description=sg.notes.get("description") or sg.name,
    )
    record.annotations["molecule_type"] = MOLECULE[sg.kind]
    record.annotations["topology"] = "circular" if sg.circular else "linear"
    record.annotations["data_file_division"] = "SYN"
    date = _genbank_date(sg.notes.get("modified") or sg.notes.get("created"))
    if date:
        record.annotations["date"] = date
    n = sg.length
    for feat in sg.features:
        strand = STRANDS.get(feat.strand)
        qualifiers = {"label": [feat.name]}
        for key, values in feat.qualifiers.items():
            if key and key != "label" and values:
                qualifiers[key] = values
        if feat.type == "CDS" and feat.reading_frame != 1:
            qualifiers["codon_start"] = [str(feat.reading_frame)]
        record.features.append(SeqFeature(
            _location([(s.start, s.end) for s in feat.segments], n, strand),
            type=feat.type,
            qualifiers=qualifiers,
        ))
    if include_primers:
        for primer in sg.primers:
            for site in primer.sites:
                record.features.append(SeqFeature(
                    _location([(site.start, site.end)], n, STRANDS[site.strand]),
                    type="primer_bind",
                    qualifiers={"label": [primer.name], "note": [f"sequence: {primer.sequence}"]},
                ))
    return record


def default_output(source: Path, fmt: str) -> Path:
    return source.with_suffix(FORMATS[fmt][1])


def export(sg: SnapGeneFile, output: Path, fmt: str, overwrite: bool = False) -> dict:
    if fmt not in FORMATS:
        raise CliError("bad_format", f"Unknown format '{fmt}'. Use: {', '.join(FORMATS)}.")
    if output.resolve() == sg.path.resolve():
        raise CliError("output_is_input", "The output file must be different from the input file.")
    if output.exists() and not overwrite:
        raise CliError("output_exists", f"{output} exists. Use --overwrite to replace it.")
    if not output.parent.is_dir():
        raise CliError("bad_output", f"The folder {output.parent} does not exist.")
    record = to_record(sg)
    if fmt == "fasta":
        record.description = f"{sg.name} {record.annotations['topology']} {sg.length} {'aa' if sg.kind == 'protein' else 'bp'}"
        record.features = []
    SeqIO.write(record, output, FORMATS[fmt][0])
    return {
        "input": str(sg.path),
        "output": str(output),
        "format": fmt,
        "length": sg.length,
        "features": len(record.features),
        "bytes": output.stat().st_size,
    }
