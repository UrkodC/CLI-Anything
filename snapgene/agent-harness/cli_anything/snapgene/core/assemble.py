"""Build a new sequence out of pieces, and carry the features across.

SnapGene writes its own .dna files and keeps a history tree inside them. This
tool never writes .dna, so a product is built here in memory as a SnapGeneFile
and then written as GenBank by core.convert.

Every base of a piece remembers where it came from. A feature moves to the
product when all of its bases made it across in one run, and it stretches over
new bases that landed inside it, the way SnapGene stretches a feature when you
type bases into the middle of it. A feature that lost bases is dropped and
reported.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cli_anything.snapgene.core import seqtools
from cli_anything.snapgene.core.dnafile import BindingSite, Feature, Primer, Segment, SnapGeneFile


@dataclass
class Piece:
    sequence: str
    coords: list[int] = field(default_factory=list)   # source position of each base, empty for new bases
    source: SnapGeneFile | None = None
    flipped: bool = False
    label: str | None = None
    label_type: str = "misc_feature"


def take(sg: SnapGeneFile, start: int, end: int, flip: bool = False,
         label: str | None = None, label_type: str = "misc_feature") -> Piece:
    """A piece cut out of `sg` from `start` to `end` (1-based, inclusive).

    On a circular file `start` after `end` wraps past the origin. With `flip`
    the piece goes in reverse complement, and its features flip strand.
    """
    bases = seqtools.subseq(sg, start, end)
    n = sg.length
    coords = [(start - 1 + i) % n + 1 for i in range(len(bases))]
    if flip:
        bases = seqtools.revcomp(bases)
        coords.reverse()
    return Piece(bases, coords, sg, flip, label, label_type)


def take_arc(sg: SnapGeneFile, start: int, length: int, flip: bool = False,
             label: str | None = None, label_type: str = "misc_feature") -> Piece:
    """A piece of `length` bases from `start`, going forward around a circle.

    `length` may be more than the file is long. That happens when a vector is
    opened inside one enzyme site: the overhang bases are then used twice, once
    at each end of the opened vector.
    """
    n = sg.length
    coords = [(start - 1 + i) % n + 1 for i in range(length)]
    bases = "".join(sg.sequence[p - 1] for p in coords)
    if flip:
        bases = seqtools.revcomp(bases)
        coords.reverse()
    return Piece(bases, coords, sg, flip, label, label_type)


def literal(sequence: str, label: str | None = None, label_type: str = "misc_feature",
            flipped: bool = False) -> Piece:
    """A piece of new bases that came from nowhere, for example a primer tail.

    `flipped` only says which strand the label goes on, because new bases have
    no source file to be read backwards from.
    """
    return Piece(sequence.upper(), [], None, flipped, label, label_type)


def _segment_positions(seg: Segment, n: int) -> list[int]:
    if seg.start <= seg.end:
        return list(range(seg.start, seg.end + 1))
    return list(range(seg.start, n + 1)) + list(range(1, seg.end + 1))


def _joined(a: int, b: int, step: int, total: int, novel: list[bool]) -> bool:
    """True when product places `a` and `b` still make one run.

    They do when they are next to each other, or when only new bases lie
    between them, so a feature stretches over bases typed into its middle.
    """
    if b == (a + step) % total:
        return True
    if step == 1 and a < b:
        return all(novel[i] for i in range(a + 1, b))
    if step == -1 and b < a:
        return all(novel[i] for i in range(b + 1, a))
    return False


def _map_feature(feat: Feature, index: dict[int, int], source_n: int, total: int,
                 flipped: bool, novel: list[bool]) -> tuple[Feature | None, bool]:
    """Move one feature into the product.

    Returns (feature, touched). `touched` is True when some of the feature's
    bases are in the product, so a feature that lost bases can be reported
    while a feature that simply stayed behind is not.
    """
    step = -1 if flipped else 1
    segments = []
    touched = False
    for seg in feat.segments:
        places = [index.get(p) for p in _segment_positions(seg, source_n)]
        touched = touched or any(p is not None for p in places)
        if any(p is None for p in places):
            return None, touched
        for a, b in zip(places, places[1:]):
            if not _joined(a, b, step, total, novel):
                return None, True
        first, last = places[0], places[-1]
        lo, hi = (last, first) if flipped else (first, last)
        segments.append(Segment(lo + 1, hi + 1, seg.name, seg.translated))
    if flipped:
        segments.reverse()
    if all(s.start <= s.end for s in segments):
        segments.sort(key=lambda s: s.start)
    strand = {"+": "-", "-": "+"}.get(feat.strand, feat.strand) if flipped else feat.strand
    return Feature(
        index=feat.index,
        name=feat.name,
        type=feat.type,
        strand=strand,
        segments=segments,
        qualifiers=dict(feat.qualifiers),
        reading_frame=feat.reading_frame,
    ), True


def relocate_primers(product: SnapGeneFile, source_primers: list) -> None:
    """Keep the primers that still bind the product, with their new positions."""
    kept = []
    for primer in source_primers:
        if not primer.sequence:
            continue
        try:
            hits = seqtools.find_motif(product, primer.sequence)
        except Exception:
            continue
        sites = [BindingSite(h["start"], h["end"], h["strand"], None) for h in hits]
        if sites:
            kept.append(Primer(len(kept), primer.name, primer.sequence, primer.description, sites))
    product.primers = kept


def assemble(pieces: list[Piece], path: Path, circular: bool = True,
             notes: dict | None = None) -> tuple[SnapGeneFile, list[str]]:
    """Join the pieces. Returns the product and the names of dropped features."""
    sequence = "".join(p.sequence for p in pieces)
    total = len(sequence)
    novel = [True] * total
    sources: dict[tuple[int, bool], tuple[SnapGeneFile, dict[int, int]]] = {}
    labels: list[Feature] = []
    offset = 0
    for piece in pieces:
        size = len(piece.sequence)
        if piece.source is not None and size:
            key = (id(piece.source), piece.flipped)
            index = sources.setdefault(key, (piece.source, {}))[1]
            for i, pos in enumerate(piece.coords):
                novel[offset + i] = False
                index.setdefault(pos, offset + i)   # a base used twice keeps its first place
        if piece.label and size:
            labels.append(Feature(
                index=0,
                name=piece.label,
                type=piece.label_type,
                strand="-" if piece.flipped else "+",
                segments=[Segment(offset + 1, offset + size)],
            ))
        offset += size

    features: list[Feature] = []
    dropped: list[str] = []
    for (_, flipped), (source, index) in sources.items():
        for feat in source.features:
            moved, touched = _map_feature(feat, index, source.length, total, flipped, novel)
            if moved is not None:
                features.append(moved)
            elif touched:
                dropped.append(f"{source.name}: {feat.name}")
    known = {(f.name, f.start, f.end) for f in features}
    features += [f for f in labels if (f.name, f.start, f.end) not in known]
    features.sort(key=lambda f: (f.start, f.end))
    for i, feat in enumerate(features):
        feat.index = i

    product = SnapGeneFile(
        path=Path(path),
        kind="dna",
        sequence=sequence,
        circular=circular,
        double_stranded=True,
        methylation={"dam": False, "dcm": False, "ecoki": False},
        features=features,
        primers=[],
        notes=notes or {},
    )
    return product, dropped
