"""Sequence operations on a loaded SnapGene file: ranges, motifs, translation, ORFs.

All positions are 1-based and inclusive. On a circular sequence a range with
start > end crosses the origin.
"""
from __future__ import annotations

import re

from Bio.Data.CodonTable import TranslationError
from Bio.Seq import Seq

from cli_anything.snapgene.core.dnafile import Feature, SnapGeneFile
from cli_anything.snapgene.errors import CliError

IUPAC = {
    "A": "A", "C": "C", "G": "G", "T": "T", "U": "T",
    "R": "[AG]", "Y": "[CT]", "S": "[CG]", "W": "[AT]", "K": "[GT]", "M": "[AC]",
    "B": "[CGT]", "D": "[AGT]", "H": "[ACT]", "V": "[ACG]", "N": "[ACGT]",
}
STOPS = {"TAA", "TAG", "TGA"}


def revcomp(seq: str) -> str:
    return str(Seq(seq).reverse_complement()).replace("U", "T")


def subseq(sg: SnapGeneFile, start: int, end: int) -> str:
    n = sg.length
    if not (1 <= start <= n and 1 <= end <= n):
        raise CliError("bad_range", f"Positions must be between 1 and {n}. Got {start}-{end}.")
    if start <= end:
        return sg.sequence[start - 1:end]
    if not sg.circular:
        raise CliError("bad_range", f"{sg.name} is linear, so start ({start}) must not be after end ({end}).")
    return sg.sequence[start - 1:] + sg.sequence[:end]


def feature_sequence(sg: SnapGeneFile, feat: Feature) -> str:
    """The feature's bases in reading direction (reverse complement for '-' features)."""
    seq = "".join(subseq(sg, s.start, s.end) for s in feat.segments)
    return revcomp(seq) if feat.strand == "-" else seq


def coding_sequence(sg: SnapGeneFile, feat: Feature) -> str:
    """The feature's bases from its first full codon (skips 1 or 2 bases for frame 2 or 3)."""
    return feature_sequence(sg, feat)[feat.reading_frame - 1:].replace("U", "T")


def translation_table(feat: Feature | None) -> int:
    if feat and feat.qualifiers.get("transl_table"):
        try:
            return int(feat.qualifiers["transl_table"][0])
        except ValueError:
            pass
    return 1


def translate(dna: str, table: int = 1) -> str:
    usable = len(dna) - len(dna) % 3
    try:
        return str(Seq(dna[:usable]).translate(table=table))
    except TranslationError as exc:
        raise CliError("bad_sequence", f"Cannot translate this sequence: {exc}") from exc


def snapgene_translation(feat: Feature) -> str | None:
    """The protein SnapGene saved for this feature, without its segment separators.

    SnapGene writes '?' for a codon with an ambiguous base. Biopython writes 'X'.
    """
    values = feat.qualifiers.get("translation")
    if not values:
        return None
    return values[0].replace(",", "").replace(" ", "").replace("?", "X")


def motif_regex(motif: str, kind: str) -> re.Pattern:
    motif = motif.strip().upper()
    if not motif:
        raise CliError("bad_motif", "Give a motif to search for.")
    if kind == "protein":
        if not re.fullmatch(r"[A-Z*]+", motif):
            raise CliError("bad_motif", f"'{motif}' is not a protein motif.")
        return re.compile(f"(?=({re.escape(motif)}))")
    bad = sorted(set(motif) - set(IUPAC))
    if bad:
        raise CliError("bad_motif", f"'{motif}' has letters that are not IUPAC DNA codes: {''.join(bad)}.")
    return re.compile("(?=(" + "".join(IUPAC[c] for c in motif) + "))")


def find_motif(sg: SnapGeneFile, motif: str) -> list[dict]:
    """Every match on both strands. A palindromic motif is reported once, on '+'."""
    pattern = motif_regex(motif, sg.kind)
    size = len(motif.strip())
    seq = sg.sequence.replace("U", "T") if sg.kind == "rna" else sg.sequence
    n = len(seq)
    search_in = seq + seq[:size - 1] if sg.circular else seq
    hits = []

    def scan(text: str, strand: str) -> None:
        for m in pattern.finditer(text):
            i = m.start()
            if i >= n:
                continue
            if strand == "+":
                start = i + 1
            else:
                start = n - (i + size) + 1
                if start < 1:
                    start += n
            end = (start + size - 2) % n + 1
            hits.append({"start": start, "end": end, "strand": strand, "match": m.group(1)})

    scan(search_in, "+")
    if sg.kind != "protein":
        rc = revcomp(seq)
        rc_motif = revcomp(motif.strip().upper())
        if rc_motif != motif.strip().upper().replace("U", "T"):
            scan(rc + rc[:size - 1] if sg.circular else rc, "-")
    hits.sort(key=lambda h: (h["start"], h["strand"]))
    return hits


def find_orfs(sg: SnapGeneFile, min_codons: int = 75, table: int = 1) -> list[dict]:
    """Open reading frames from ATG to a stop codon, on both strands.

    min_codons counts amino acids, not including the stop (SnapGene's default is 75).
    On a circular sequence an ORF may cross the origin; then end < start.
    """
    if sg.kind == "protein":
        raise CliError("not_dna", f"{sg.name} is a protein file. ORFs need a DNA or RNA sequence.")
    seq = sg.sequence.replace("U", "T")
    n = len(seq)
    orfs = []
    for strand, text in (("+", seq), ("-", revcomp(seq))):
        # On a circle, scan three copies and keep ORFs that start in the middle copy.
        # The first copy gives the real upstream context, the last lets ORFs run past the origin.
        work = text * 3 if sg.circular else text
        offset = n if sg.circular else 0
        for frame in range(3):
            start = None
            for i in range(frame, len(work) - 2, 3):
                codon = work[i:i + 3]
                if start is None and codon == "ATG":
                    start = i
                elif start is not None and codon in STOPS:
                    if offset <= start < offset + n and (i + 3 - start) <= n:
                        aa = (i - start) // 3
                        if aa >= min_codons:
                            orfs.append(_orf_record(work, start, i + 3, n, offset, strand, table))
                    start = None
                if start is not None and i - start >= n:
                    start = None
    orfs.sort(key=lambda o: (o["start"], o["strand"]))
    return orfs


def _orf_record(work: str, a: int, b: int, n: int, offset: int, strand: str, table: int) -> dict:
    dna = work[a:b]
    p0, p1 = a - offset, b - offset - 1          # 0-based, inclusive, on the scanned strand
    # start/end are a range on the top strand, like a feature: the ATG of a '-' ORF is at end.
    if strand == "+":
        start, end = p0 % n + 1, p1 % n + 1
    else:
        start, end = n - (p1 % n), n - (p0 % n)
    protein = translate(dna, table)
    return {
        "strand": strand,
        "start": start,
        "end": end,
        "length_bp": len(dna),
        "length_aa": len(protein.rstrip("*")),
        "protein": protein,
    }
