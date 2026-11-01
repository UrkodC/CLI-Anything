"""Gateway LR cloning: recombine an entry clone with a destination vector.

Invitrogen's Gateway system uses the lambda integrase att sites. The LR
reaction swaps the attL1-insert-attL2 part of an entry clone with the
attR1-ccdB-attR2 cassette of a destination vector:

    attL1 x attR1 -> attB1 + attP1
    attL2 x attR2 -> attB2 + attP2

attL and attR share a core region, and the crossover happens inside it. So the
attB site in the product is the attR bases up to the end of the core plus the
attL bases after it. Inside the core the two sites read the same, so the exact
crossover point does not change the answer.

The core is found by comparing the two sites, not from a fixed offset: in
SnapGene's own annotations attR1 and attR2 are 125 bp while attR3 and attR4 are
124 bp, so a fixed offset would be one base out on half the vectors.

Checked against SnapGene's annotations: attB1 comes out as
ACAAGTTTGTACAAAAAAGCAGGCT and attB2 as ACCACTTTGTACAAGAAAGCTGGGT, which is
what SnapGene labels attB1 and attB2 in the bundled plasmids that carry them.

Everything between attR1 and attR2, which is the ccdB and CmR cassette, is left
out of the product. That is what kills the empty destination vector in a real
LR reaction. The other product of the reaction, the plasmid carrying the
cassette between two attP sites, is not built here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from cli_anything.snapgene.core import assemble, history, seqtools
from cli_anything.snapgene.core.dnafile import Feature, SnapGeneFile
from cli_anything.snapgene.errors import CliError

ATT_NAME = re.compile(r"^att\s*([BLPR])\s*(\d+)r?$", re.IGNORECASE)
MIN_CORE = 10            # the shared core is 15 bp in att1 and att2
MAX_FLANK = 10           # bases outside the core, inside the 25 bp attB site
WINDOW = 40              # how far into each site to look for the core
TAG_WINDOW = 60          # how far from the insert a tag may sit and still be reported
STOP_CODONS = ("TAA", "TAG", "TGA")
CASSETTE_MARKERS = ("ccdb", "cmr", "chloramphenicol")


def _wrap(pos: int, n: int) -> int:
    return (pos - 1) % n + 1


@dataclass
class AttSite:
    """One att site of a file, read 5' to 3' along its own strand."""
    kind: str                # B, L, P or R
    number: str              # 1, 2, 3 ...
    feature: Feature
    sequence: str

    @property
    def name(self) -> str:
        return f"att{self.kind}{self.number}"

    @property
    def start(self) -> int:
        return self.feature.start

    @property
    def end(self) -> int:
        return self.feature.end

    @property
    def strand(self) -> str:
        return self.feature.strand

    def to_dict(self) -> dict:
        return {"name": self.name, "range": [self.start, self.end], "strand": self.strand,
                "length": len(self.sequence)}


@dataclass
class Clone:
    """A file with two att sites of one kind pointing at each other."""
    sg: SnapGeneFile
    left: AttSite            # the site on the plus strand
    right: AttSite           # the site on the minus strand

    @property
    def middle(self) -> tuple[int, int]:
        """The part between the two sites, in the file's own numbering."""
        n = self.sg.length
        return _wrap(self.left.end + 1, n), _wrap(self.right.start - 1, n)

    @property
    def middle_bp(self) -> int:
        a, b = self.middle
        return (b - a) % self.sg.length + 1


def find_att(sg: SnapGeneFile, kind: str) -> list[AttSite]:
    """Every attL, attR, attB or attP feature of a file."""
    out = []
    for feat in sg.features:
        match = ATT_NAME.match(feat.name.strip())
        if not match or match.group(1).upper() != kind.upper():
            continue
        seq = seqtools.subseq(sg, feat.start, feat.end)
        if feat.strand == "-":
            seq = seqtools.revcomp(seq)
        out.append(AttSite(kind.upper(), match.group(2), feat, seq))
    return out


def read_clone(sg: SnapGeneFile, kind: str, what: str) -> Clone:
    """Check a file and find the two att sites the reaction needs."""
    if sg.kind != "dna":
        raise CliError("not_dna", f"{sg.name} is not a DNA file. Gateway cloning needs DNA.")
    if not sg.circular:
        raise CliError("not_circular", f"{sg.name} is linear. The {what} must be a circular plasmid.")
    sites = find_att(sg, kind)
    if len(sites) != 2:
        found = ", ".join(s.name for s in sites) or "none"
        raise CliError(
            "no_att",
            f"{sg.name} needs exactly two att{kind.upper()} features to be used as the {what}. "
            f"Found {len(sites)} ({found}). Add them with `annotate`, or check the file.")
    plus = [s for s in sites if s.strand == "+"]
    minus = [s for s in sites if s.strand == "-"]
    if len(plus) != 1 or len(minus) != 1:
        names = ", ".join(f"{s.name} ({s.strand})" for s in sites)
        raise CliError("att_direction",
                       f"The two att{kind.upper()} sites of {sg.name} must face each other, one on each "
                       f"strand. Found {names}.")
    return Clone(sg, plus[0], minus[0])


def _core(attl: str, attr: str) -> tuple[int, int, int] | None:
    """Where attL and attR share their core. Returns (index in attL, index in attR, length)."""
    best = None
    for i in range(max(0, len(attl) - WINDOW), len(attl)):
        for j in range(0, min(MAX_FLANK + 1, len(attr))):
            k = 0
            while i + k < len(attl) and j + k < len(attr) and attl[i + k] == attr[j + k]:
                k += 1
            if k >= MIN_CORE and len(attl) - (i + k) <= MAX_FLANK:
                if best is None or k > best[2]:
                    best = (i, j, k)
    return best


def recombine(attl: str, attr: str, names: str = "attL x attR") -> str:
    """The attB site that one attL and one attR make, read along the attR strand."""
    found = _core(attl, attr)
    if found is None:
        raise CliError("att_mismatch",
                       f"{names} do not share a core region, so they cannot recombine. "
                       "Check that the entry clone and the destination vector use the same "
                       "att numbers.")
    i, j, k = found
    return attr[:j + k] + attl[i + k:]


def _partner(entry: Clone, site: AttSite) -> AttSite:
    for candidate in (entry.left, entry.right):
        if candidate.number == site.number:
            return candidate
    have = ", ".join(s.name for s in (entry.left, entry.right))
    raise CliError("att_mismatch",
                   f"The destination vector has {site.name} but the entry clone has {have}. "
                   "Gateway only joins sites with the same number.")


def _inside(sg: SnapGeneFile, start: int, end: int) -> list[str]:
    """The names of the features that lie wholly between `start` and `end`."""
    n = sg.length
    last = (end - start) % n
    return [f.name for f in sg.features
            if (f.start - start) % n <= last and (f.end - start) % n <= last]


def _fit(gap: int) -> str:
    """How a tag that many bases away sits with the insert's reading frame."""
    off = gap % 3
    if off == 0:
        return "in frame with it."
    return f"out of frame with it, by {off} base{'s' if off > 1 else ''}."


def _tag_notes(product: SnapGeneFile, insert_start: int, insert_end: int) -> list[str]:
    """Report any tag the insert is about to be fused to, and its reading frame."""
    out = []
    for feat in product.features:
        if feat.type != "CDS" or feat.strand != "+":
            continue
        gap = insert_start - feat.end - 1
        if 0 <= gap <= TAG_WINDOW:
            out.append(f"{feat.name} ends {gap} bp before the insert, so the insert is {_fit(gap)}")
        gap = feat.start - insert_end - 1
        if 0 <= gap <= TAG_WINDOW:
            out.append(f"{feat.name} starts {gap} bp after the insert, so the insert is {_fit(gap)}")
    return out


def _story(entry_sg: SnapGeneFile, dest_sg: SnapGeneFile, dest: Clone, partners: tuple[AttSite, AttSite],
           insert: tuple[int, int], flipped: bool, backbone: tuple[int, int], product: SnapGeneFile,
           new: tuple[int, int], cassette: list[str]) -> tuple[str, str]:
    """The history tree and the written story of an LR product."""
    left_partner, right_partner = partners
    destination = history.file_node(dest_sg, [backbone])
    entry = history.file_node(entry_sg, [insert])
    top = history.made_node(
        f"{product.name}.dna", product.length, True, "gatewayLRCloning",
        [(destination, history.summary("replace", dest.left.start, dest.right.end,
                                       dest.left.name, dest.right.name)),
         (entry, history.summary("insert", left_partner.start, right_partner.end,
                                 left_partner.name, right_partner.name))],
        [new])
    insert_bp = (insert[1] - insert[0]) % entry_sg.length + 1
    taken = (dest.right.end - dest.left.start) % dest_sg.length + 1
    sites = [f for f in product.features if f.name in (f"attB{dest.left.number}", f"attB{dest.right.number}")]
    text = "\n".join([
        f"Made by Gateway LR cloning (Invitrogen) with cli-anything-snapgene on {date.today().isoformat()}.",
        f"Destination vector: {dest_sg.path.name}; {taken} bp from {dest.left.name} to {dest.right.name} "
        f"taken out" + (f" ({', '.join(cassette)})" if cassette else "") + ".",
        f"Entry clone: {entry_sg.path.name}; {insert_bp} bp between {left_partner.name} and "
        f"{right_partner.name} put in" + (", read the other way round" if flipped else "") + ".",
        "New sites: " + ", ".join(f"{f.name} {f.start}-{f.end}" for f in sites) + ".",
    ])
    return history.tree(top), text


def lr(entry_sg: SnapGeneFile, dest_sg: SnapGeneFile, product_path: Path | None = None,
       insert_name: str | None = None) -> dict:
    """Run an LR reaction on paper: entry clone + destination vector -> expression clone."""
    entry = read_clone(entry_sg, "L", "entry clone")
    dest = read_clone(dest_sg, "R", "destination vector")
    left_partner = _partner(entry, dest.left)
    right_partner = _partner(entry, dest.right)
    if left_partner is right_partner:
        raise CliError("att_mismatch",
                       f"The destination vector carries {dest.left.name} and {dest.right.name}, which "
                       f"are the same number. It cannot pair with {entry_sg.name}.")

    attb_left = recombine(left_partner.sequence, dest.left.sequence,
                          f"{left_partner.name} x {dest.left.name}")
    attb_right_own = recombine(right_partner.sequence, dest.right.sequence,
                               f"{right_partner.name} x {dest.right.name}")
    attb_right = seqtools.revcomp(attb_right_own)

    # The insert runs on from the 3' end of the attL that pairs with the
    # destination's plus-strand site, in that site's own direction.
    n_entry = entry_sg.length
    if left_partner.strand == "+":
        first, last, flip = _wrap(left_partner.end + 1, n_entry), _wrap(right_partner.start - 1, n_entry), False
    else:
        first, last, flip = _wrap(right_partner.end + 1, n_entry), _wrap(left_partner.start - 1, n_entry), True
    insert_bp = (last - first) % n_entry + 1
    if insert_bp >= n_entry - len(left_partner.sequence) - len(right_partner.sequence):
        raise CliError("bad_entry",
                       f"The two attL sites of {entry_sg.name} leave no room for an insert between them.")
    insert = assemble.take(entry_sg, first, last, flip=flip)

    # Keep the destination's numbering: split the backbone at its origin.
    n_dest = dest_sg.length
    backbone_first = _wrap(dest.right.end + 1, n_dest)
    backbone_bp = (dest.left.start - 1 - backbone_first) % n_dest + 1
    end_pos = backbone_first - 1 + backbone_bp
    if end_pos <= n_dest:
        before = [assemble.take_arc(dest_sg, backbone_first, backbone_bp)]
        after: list[assemble.Piece] = []
    else:
        head = end_pos - n_dest
        before = [assemble.take_arc(dest_sg, 1, head)]
        after = [assemble.take_arc(dest_sg, backbone_first, backbone_bp - head)]

    name = insert_name or entry_sg.name
    pieces = before + [
        assemble.literal(attb_left, f"attB{dest.left.number}", "protein_bind"),
        insert,
        assemble.literal(attb_right, f"attB{dest.right.number}", "protein_bind", flipped=True),
    ] + after
    path = product_path or Path(f"{dest_sg.name}-{name}")
    product, dropped = assemble.assemble([p for p in pieces if p.sequence], path, circular=True)
    assemble.relocate_primers(product, list(entry_sg.primers) + list(dest_sg.primers))

    head_bp = sum(len(p.sequence) for p in before)
    insert_start = head_bp + len(attb_left) + 1
    insert_end = insert_start + insert_bp - 1

    att_names = {dest.left.feature.name, dest.right.feature.name}
    cassette_names = [n for n in _inside(dest_sg, dest.left.start, dest.right.end) if n not in att_names]
    notes = []
    if not any(m in nm.lower() for nm in cassette_names for m in CASSETTE_MARKERS):
        notes.append(f"No ccdB or CmR feature sits between {dest.left.name} and {dest.right.name} in "
                     f"{dest_sg.name}. Check that this is a destination vector and not something else.")
    left_over = [f.name for f in product.features
                 if any(m in f.name.lower() for m in CASSETTE_MARKERS)]
    if left_over:
        notes.append("The product still carries " + ", ".join(sorted(set(left_over)))
                     + ". That should not happen; do not use this file.")
    if insert_bp % 3:
        notes.append(f"The insert is {insert_bp} bp, which is not a multiple of 3. "
                     "A tag after it would be out of frame.")
    elif insert.sequence[-3:].upper() in STOP_CODONS:
        notes.append(f"The insert ends with the stop codon {insert.sequence[-3:].upper()}. "
                     "A tag after it in the vector would not be made.")
    product.history, product.notes["description"] = _story(
        entry_sg, dest_sg, dest, (left_partner, right_partner), (first, last), flip,
        (backbone_first, _wrap(dest.left.start - 1, n_dest)), product, (insert_start, insert_end),
        cassette_names)
    notes += _tag_notes(product, insert_start, insert_end)
    if dropped:
        notes.append("Features cut in half, left out of the product: " + ", ".join(sorted(set(dropped))) + ".")

    return {
        "method": "Gateway LR (Invitrogen)",
        "entry": {"file": str(entry_sg.path), "name": entry_sg.name, "length": n_entry,
                  "sites": [left_partner.to_dict(), right_partner.to_dict()]},
        "destination": {"file": str(dest_sg.path), "name": dest_sg.name, "length": n_dest,
                        "sites": [dest.left.to_dict(), dest.right.to_dict()],
                        "backbone_bp": backbone_bp,
                        "cassette": [_wrap(dest.left.start, n_dest), _wrap(dest.right.end, n_dest)],
                        "cassette_bp": n_dest - backbone_bp,
                        "cassette_features": cassette_names},
        "insert": {"name": name, "length": insert_bp, "from": [first, last],
                   "reversed": flip, "range_in_product": [insert_start, insert_end]},
        "att_sites": [
            {"name": f"attB{dest.left.number}", "sequence": attb_left,
             "from": f"{dest.left.name} x {left_partner.name}",
             "range": [head_bp + 1, head_bp + len(attb_left)], "strand": "+"},
            {"name": f"attB{dest.right.number}", "sequence": attb_right_own,
             "from": f"{dest.right.name} x {right_partner.name}",
             "range": [insert_end + 1, insert_end + len(attb_right)], "strand": "-"},
        ],
        "product": {"name": product.name, "length": product.length,
                    "features": len(product.features), "primers": len(product.primers),
                    "dropped_features": sorted(set(dropped))},
        "warnings": notes,
        "_product": product,
    }
