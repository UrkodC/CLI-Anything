"""In-Fusion cloning: open a vector, design the two primers, build the product.

The rules come from Takara's In-Fusion HD manual (PT5165-1, section IV.C and
Figures 2 and 3), the same rules Takara's own web tool uses:

- each primer carries 15 bases at its 5' end that match the end of the opened
  vector, and 18-25 gene-specific bases at its 3' end
- the 15 bases are the bases complementary to the first 15 bases at the 5' end
  of one vector strand. Bases of a 5' overhang are therefore inside the 15;
  bases of a 3' overhang are left out
- extra bases may follow the 15 to rebuild the restriction site

The same machinery covers Gibson and HiFi assembly: raise `overlap` (20-25).

Takara's web tool is a web page, so it cannot be called from here. This module
follows the printed rules instead, and the arithmetic was checked against every
primer in Figure 3 of the manual.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cli_anything.snapgene.core import assemble, gblock, primers, seqtools
from cli_anything.snapgene.core.cutting import Cut, find_cuts
from cli_anything.snapgene.core.dnafile import BindingSite, Primer, SnapGeneFile
from cli_anything.snapgene.errors import CliError

OVERLAP = 15
OVERLAP_RANGE = (12, 21)
STOP_CODONS = ("TAA", "TAG", "TGA")


def _wrap(pos: int, n: int) -> int:
    return (pos - 1) % n + 1


def _site(start: int, length: int, total: int) -> tuple[int, int]:
    """A span on a circular product, from a 0-based start, as 1-based (start, end)."""
    return start % total + 1, (start + length - 1) % total + 1


@dataclass
class OpenVector:
    """A vector opened ready for an insert, in the vector's own coordinates.

    `before` holds the vector pieces that come in front of the insert and
    `after` the ones behind it, so the product keeps the vector's own numbering:
    base 1 of the vector stays base 1 of the product. `backbone` is the same
    bases in one string, starting at the opened end, which is what the two 15
    base homology arms are taken from.
    """
    sg: SnapGeneFile
    first_top: int            # first top-strand base kept
    last_top: int             # last top-strand base kept
    last_bottom: int          # last base the bottom strand reaches at that end
    end_kind: str             # the end the forward primer joins: 5'/3' overhang or blunt
    overhang: int             # how many bases that overhang is
    restore_before: str       # site bases the reverse primer adds back
    restore_after: str        # site bases the forward primer adds back
    backbone: str
    before: list[assemble.Piece] = field(default_factory=list)
    after: list[assemble.Piece] = field(default_factory=list)
    cuts: list[Cut] = field(default_factory=list)
    removed: tuple[int, int] | None = None
    removed_bp: int = 0
    how: str = ""

    @property
    def before_length(self) -> int:
        return sum(len(p.sequence) for p in self.before)

    def to_dict(self) -> dict:
        return {
            "vector": str(self.sg.path),
            "how": self.how,
            "enzymes": [c.to_dict() for c in self.cuts],
            "kept": [self.first_top, self.last_top],
            "backbone_bp": len(self.backbone),
            "removed": list(self.removed) if self.removed else None,
            "removed_bp": self.removed_bp,
            "forward_end": self.end_kind,
            "restores": {"before_insert": self.restore_after, "after_insert": self.restore_before},
        }


def open_vector(sg: SnapGeneFile, enzyme_names: list[str] | None = None, at: int | None = None,
                remove: tuple[int, int] | None = None, keep: str = "larger") -> OpenVector:
    """Open a circular vector with enzymes, at one point, or by cutting a part out."""
    if sg.kind != "dna":
        raise CliError("not_dna", f"{sg.name} is not a DNA file. Cloning needs DNA.")
    if not sg.circular:
        raise CliError("not_circular", f"{sg.name} is linear. Give a circular vector: the product is a plasmid.")
    chosen = [x for x in (enzyme_names, at, remove) if x]
    if len(chosen) != 1:
        raise CliError("bad_request", "Open the vector in one way only: --enzyme, --at or --remove.")
    n = sg.length
    cuts: list[Cut] = []
    removed = None
    removed_bp = 0
    restore_before = restore_after = ""
    end_kind, overhang = "blunt", 0

    if at is not None:
        if not 1 <= at <= n:
            raise CliError("bad_range", f"--at must be between 1 and {n}. Got {at}.")
        first_top, last_top, last_bottom = _wrap(at + 1, n), at, at
        how = f"opened between base {at} and base {_wrap(at + 1, n)} (blunt, as by inverse PCR)"
    elif remove is not None:
        a, b = remove
        if not (1 <= a <= n and 1 <= b <= n):
            raise CliError("bad_range", f"The part to remove must be inside 1-{n}. Got {a}-{b}.")
        removed, removed_bp = (a, b), (b - a) % n + 1
        if removed_bp >= n - 2 * OVERLAP:
            raise CliError("bad_range", f"Removing {removed_bp} bp leaves too little vector to join to.")
        first_top, last_top, last_bottom = _wrap(b + 1, n), _wrap(a - 1, n), _wrap(a - 1, n)
        how = f"bases {a}-{b} ({removed_bp} bp) taken out, blunt ends (as by inverse PCR)"
    else:
        cuts = find_cuts(sg, list(enzyme_names or []))
        if not cuts:
            raise CliError("no_cut", f"{', '.join(enzyme_names or [])} does not cut {sg.name}.")
        if len(cuts) > 2:
            spots = ", ".join(f"{c.enzyme} after {c.top}" for c in cuts)
            raise CliError("too_many_cuts", f"{sg.name} is cut {len(cuts)} times ({spots}). Use enzymes that cut once.")
        if len(cuts) == 1:
            cut = cuts[0]
            first_top, last_top, last_bottom = _wrap(cut.top + 1, n), cut.top, cut.bottom
            restore_before, restore_after = cut.restore_before, cut.restore_after
            end_kind, overhang = cut.kind, len(cut.overhang)
            how = f"linearized with {cut.enzyme} (cut after base {cut.top}, {cut.kind})"
        else:
            low, high = cuts
            inside = (high.top - low.top) % n
            outside = n - inside
            if keep == "inside" or (keep == "larger" and inside >= outside) or (keep == "smaller" and inside < outside):
                start_cut, end_cut = low, high
                removed, removed_bp = (_wrap(high.top + 1, n), low.top), outside
            else:
                start_cut, end_cut = high, low
                removed, removed_bp = (_wrap(low.top + 1, n), high.top), inside
            first_top, last_top, last_bottom = _wrap(start_cut.top + 1, n), end_cut.top, end_cut.bottom
            restore_before, restore_after = start_cut.restore_before, end_cut.restore_after
            end_kind, overhang = end_cut.kind, len(end_cut.overhang)
            how = (f"cut with {low.enzyme} (after {low.top}) and {high.enzyme} (after {high.top}); "
                   f"{removed_bp} bp taken out")

    top_length = (last_top - first_top) % n + 1
    if end_kind == "5' overhang":
        length = top_length + overhang       # the overhang bases join the product too
    elif end_kind == "3' overhang":
        length = top_length - overhang       # the sticking-out top bases are left behind
    else:
        length = top_length
    if length < 2 * OVERLAP:
        raise CliError("bad_range", f"The opened vector is only {length} bp. That is too short to join.")
    # Split the backbone at the vector's origin, so the product is numbered like
    # the vector instead of starting at the cut.
    end_pos = first_top - 1 + length
    if end_pos <= n:
        before = [assemble.take_arc(sg, first_top, length)]
        after: list[assemble.Piece] = []
    else:
        head = end_pos - n
        before = [assemble.take_arc(sg, 1, head)]
        after = [assemble.take_arc(sg, first_top, length - head)]
    backbone = "".join(p.sequence for p in after + before)
    return OpenVector(sg=sg, first_top=first_top, last_top=last_top, last_bottom=last_bottom,
                      end_kind=end_kind, overhang=overhang, restore_before=restore_before,
                      restore_after=restore_after, backbone=backbone, before=before, after=after,
                      cuts=cuts, removed=removed, removed_bp=removed_bp, how=how)


def design(opened: OpenVector, insert: assemble.Piece, overlap: int = OVERLAP,
           restore_sites: bool = True, product_path: Path | None = None,
           insert_name: str = "insert") -> dict:
    """Design the two primers and build the product plasmid."""
    gene = insert.sequence
    if len(gene) < 40:
        raise CliError("bad_insert", f"The insert is only {len(gene)} bp. That is too short for two primers.")
    if overlap < 8:
        raise CliError("bad_overlap", f"--overlap must be at least 8. Got {overlap}.")
    backbone = opened.backbone
    if overlap > len(backbone) // 2:
        raise CliError("bad_overlap", f"--overlap {overlap} is too long for a {len(backbone)} bp backbone.")
    after = opened.restore_after if restore_sites else ""
    before = opened.restore_before if restore_sites else ""

    forward_homology = backbone[-overlap:]
    reverse_homology = backbone[:overlap]
    anchor_f, anchor_r = primers.choose_anchors(gene, seqtools.revcomp(gene))
    forward_seq = forward_homology + after + anchor_f["anchor"]
    reverse_seq = seqtools.revcomp(reverse_homology) + seqtools.revcomp(before) + anchor_r["anchor"]
    fragment = forward_homology + after + gene + before + reverse_homology

    pieces = opened.before + [assemble.literal(after), insert, assemble.literal(before)] + opened.after
    path = product_path or Path(f"{opened.sg.name}-{insert_name}")
    product, dropped = assemble.assemble([p for p in pieces if p.sequence], path, circular=True)
    total = product.length
    head = opened.before_length
    product.primers = [
        Primer(0, f"{insert_name} In-Fusion FOR", forward_seq, "In-Fusion forward primer (cli-anything-snapgene)",
               [BindingSite(*_site(head - overlap, len(forward_seq), total), "+", anchor_f["tm"])]),
        Primer(1, f"{insert_name} In-Fusion REV", reverse_seq, "In-Fusion reverse primer (cli-anything-snapgene)",
               [BindingSite(*_site(head + len(after) + len(gene) - len(anchor_r["anchor"]),
                                   len(reverse_seq), total), "-", anchor_r["tm"])]),
    ]

    notes = []
    low, high = OVERLAP_RANGE
    if not low <= overlap <= high:
        notes.append(f"Overlap is {overlap} bases. Takara recommends {low}-{high} for In-Fusion "
                     f"(20-25 is normal for Gibson or HiFi assembly).")
    notes += primers.warnings_for("Forward primer", anchor_f, forward_seq)
    notes += primers.warnings_for("Reverse primer", anchor_r, reverse_seq)
    notes += primers.pair_warnings(anchor_f, anchor_r, forward_seq, reverse_seq)
    if len(gene) % 3:
        notes.append(f"The insert is {len(gene)} bp, which is not a multiple of 3. "
                     "For a fusion protein the frame after it would shift.")
    elif gene[-3:] in STOP_CODONS:
        notes.append(f"The insert ends with the stop codon {gene[-3:]}. Anything after it in the "
                     "plasmid is not part of the protein. Leave the last 3 bases out "
                     "(--insert-range) to make a fusion.")
    if not restore_sites and opened.end_kind != "blunt" and not opened.removed:
        notes.append(f"--no-restore-sites with a {opened.end_kind} end repeats the {opened.overhang} "
                     "overhang bases in the product. Leave the sites on unless you know you want this.")
    if dropped:
        notes.append("Features cut in half, left out of the product: " + ", ".join(sorted(set(dropped))) + ".")
    checks = gblock.check(fragment, "fragment")

    return {
        "method": "In-Fusion (Takara)",
        "tm_method": primers.TM_METHOD,
        "opened": opened.to_dict(),
        "insert": {"name": insert_name, "length": len(gene),
                   "source": str(insert.source.path) if insert.source else None,
                   "reversed": insert.flipped},
        "overlap": overlap,
        "restore_sites": restore_sites,
        "primers": [
            {"name": product.primers[0].name, "sequence": forward_seq, "length": len(forward_seq),
             "homology": forward_homology, "site_bases": after, **anchor_f},
            {"name": product.primers[1].name, "sequence": reverse_seq, "length": len(reverse_seq),
             "homology": seqtools.revcomp(reverse_homology), "site_bases": seqtools.revcomp(before), **anchor_r},
        ],
        "delta_tm": round(abs(anchor_f["tm"] - anchor_r["tm"]), 1),
        "fragment": {k: v for k, v in checks.items() if k != "sequence"},
        "fragment_sequence": fragment,
        "product": {"name": product.name, "length": total, "features": len(product.features),
                    "dropped_features": sorted(set(dropped))},
        "warnings": notes,
        "_product": product,
    }
