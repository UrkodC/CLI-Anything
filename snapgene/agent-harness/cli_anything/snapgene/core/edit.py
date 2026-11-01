"""Change a sequence: put bases in, take bases out, swap a part, add a feature.

SnapGene does this in its own window and saves a .dna file. This tool never
writes .dna, so the result is a new sequence in memory that core.convert writes
as GenBank. The input file is never touched.

A feature that the change cuts in half is left out of the result and reported.
"""
from __future__ import annotations

import dataclasses
from datetime import date
from pathlib import Path

from cli_anything.snapgene.core import assemble, seqtools
from cli_anything.snapgene.core.dnafile import Feature, Segment, SnapGeneFile
from cli_anything.snapgene.errors import CliError

DNA_LETTERS = set("ACGTURYSWKMBDHVN")


def clean_bases(sequence: str, kind: str = "dna") -> str:
    """Accept bases with spaces or line breaks in them; refuse anything else."""
    cleaned = "".join(sequence.split()).upper()
    if not cleaned:
        raise CliError("bad_sequence", "Give the bases to put in.")
    bad = sorted(set(cleaned) - DNA_LETTERS)
    if bad and kind != "protein":
        raise CliError("bad_sequence", f"These are not DNA letters: {''.join(bad)}.")
    return cleaned


def edit(sg: SnapGeneFile, sequence: str = "", at: int | None = None,
         start: int | None = None, end: int | None = None,
         label: str | None = None, label_type: str = "misc_feature",
         output: Path | None = None) -> dict:
    """Put bases in after `at`, or replace `start`-`end` with `sequence`.

    An empty `sequence` with `start`-`end` deletes that part.
    """
    n = sg.length
    new = clean_bases(sequence, sg.kind) if sequence else ""
    if at is not None:
        if start is not None or end is not None:
            raise CliError("bad_request", "Use --at on its own, or --start with --end.")
        if not 0 <= at <= n:
            raise CliError("bad_range", f"--at must be between 0 and {n}. Got {at}.")
        if not new:
            raise CliError("bad_sequence", "Give --sequence with --at.")
        pieces = []
        if at >= 1:
            pieces.append(assemble.take(sg, 1, at))
        pieces.append(assemble.literal(new, label, label_type))
        if at < n:
            pieces.append(assemble.take(sg, at + 1, n))
        what = f"{len(new)} bp put in after base {at}"
        removed = 0
    else:
        if start is None or end is None:
            raise CliError("bad_range", "Give --at, or both --start and --end.")
        if not (1 <= start <= n and 1 <= end <= n):
            raise CliError("bad_range", f"Positions must be between 1 and {n}. Got {start}-{end}.")
        if start > end and not sg.circular:
            raise CliError("bad_range", f"{sg.name} is linear, so --start must not be after --end.")
        removed = (end - start) % n + 1 if start > end else end - start + 1
        if removed >= n - 10:
            raise CliError("bad_range", f"That would take out {removed} of {n} bases. Too little would be left.")
        if start <= end:
            pieces = []
            if start > 1:
                pieces.append(assemble.take(sg, 1, start - 1))
            if new:
                pieces.append(assemble.literal(new, label, label_type))
            if end < n:
                pieces.append(assemble.take(sg, end + 1, n))
        else:
            pieces = [assemble.take(sg, end + 1, start - 1)]
            if new:
                pieces.append(assemble.literal(new, label, label_type))
        what = (f"bases {start}-{end} ({removed} bp) taken out"
                if not new else f"bases {start}-{end} ({removed} bp) swapped for {len(new)} bp")
    product, dropped = assemble.assemble(pieces, output or sg.path.with_suffix(".gb"),
                                         circular=sg.circular, notes=dict(sg.notes))
    assemble.relocate_primers(product, sg.primers)
    # The history tree describes the old sequence, so it is not carried on.
    # The written story is, with this edit added to it.
    line = f"Edited with cli-anything-snapgene on {date.today().isoformat()}: {sg.path.name}, {what}."
    earlier = product.notes.get("description")
    product.notes["description"] = f"{earlier}\n{line}" if earlier else line
    return {
        "input": str(sg.path),
        "change": what,
        "length_before": n,
        "length_after": product.length,
        "removed_bp": removed,
        "added_bp": len(new),
        "features": len(product.features),
        "dropped_features": sorted(set(dropped)),
        "primers_kept": len(product.primers),
        "_product": product,
    }


def annotate(sg: SnapGeneFile, name: str, start: int, end: int, ftype: str = "misc_feature",
             strand: str = "+", qualifiers: dict | None = None) -> SnapGeneFile:
    """Add one feature. Returns a copy of the file with the feature in it."""
    n = sg.length
    if not (1 <= start <= n and 1 <= end <= n):
        raise CliError("bad_range", f"Positions must be between 1 and {n}. Got {start}-{end}.")
    if start > end and not sg.circular:
        raise CliError("bad_range", f"{sg.name} is linear, so --start must not be after --end.")
    feature = Feature(
        index=len(sg.features),
        name=name,
        type=ftype,
        strand=strand,
        segments=[Segment(start, end)],
        qualifiers=qualifiers or {},
    )
    features = sorted(sg.features + [feature], key=lambda f: (f.start, f.end))
    for i, feat in enumerate(features):
        feat.index = i
    return dataclasses.replace(sg, features=features)


def feature_span(sg: SnapGeneFile, feat: Feature) -> tuple[int, int]:
    """The whole span of a feature, origin crossing included."""
    if len(feat.segments) == 1:
        return feat.segments[0].start, feat.segments[0].end
    return feat.segments[0].start, feat.segments[-1].end


def protein_check(sg: SnapGeneFile, product: SnapGeneFile) -> list[str]:
    """Report CDS features whose protein changed, so a frame shift cannot hide."""
    before = {}
    for feat in sg.features:
        if feat.type == "CDS":
            before.setdefault(feat.name, seqtools.translate(seqtools.coding_sequence(sg, feat),
                                                            seqtools.translation_table(feat)))
    notes = []
    for feat in product.features:
        if feat.type != "CDS" or feat.name not in before:
            continue
        after = seqtools.translate(seqtools.coding_sequence(product, feat), seqtools.translation_table(feat))
        if after != before[feat.name]:
            notes.append(f"The protein of {feat.name} changed ({len(before[feat.name])} aa -> {len(after)} aa).")
    return notes
