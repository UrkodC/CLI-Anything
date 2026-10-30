"""Cut a DNA file with restriction enzymes and describe the ends.

SnapGene shows this in its Enzymes view and its cloning dialogs. Neither can be
driven from outside the app, so the cuts are worked out here from REBASE data
(through Biopython), the same source SnapGene uses.

Positions are 1-based. A cut records the last base before the cut on each
strand, in top-strand coordinates:

    top    = last top-strand base before the cut
    bottom = last position covered by the bottom strand before the cut

EcoRI (G^AATT_C) on a site at 10-15 cuts top after 10 and bottom after 14, so
the left piece carries a 4-base 5' overhang on the bottom strand. KpnI
(G_GTAC^C) on the same site cuts top after 14 and bottom after 10: a 3'
overhang on the top strand.

`restore_before` and `restore_after` are the site bases that an In-Fusion primer
must add back to rebuild the recognition site at a junction (Takara In-Fusion HD
manual PT5165-1, Figure 3).
"""
from __future__ import annotations

from dataclasses import dataclass

from Bio.Seq import Seq

from cli_anything.snapgene.core import seqtools
from cli_anything.snapgene.core.dnafile import SnapGeneFile
from cli_anything.snapgene.core.enzymes import lookup
from cli_anything.snapgene.errors import CliError


@dataclass
class Cut:
    enzyme: str
    site: str                 # the recognition pattern, for example GAATTC
    site_start: int           # first base of the site on the top strand
    site_end: int             # last base of the site on the top strand
    site_seq: str             # the real bases at the site
    top: int                  # last top-strand base before the cut
    bottom: int               # last bottom-strand position before the cut
    kind: str                 # "5' overhang", "3' overhang" or "blunt"
    overhang: str             # the single-stranded bases, top-strand reading
    restore_before: str       # site bases to the left of the top cut
    restore_after: str        # site bases to the right of the bottom cut

    def to_dict(self) -> dict:
        return {
            "enzyme": self.enzyme,
            "site": self.site,
            "site_range": [self.site_start, self.site_end],
            "cut_after_top": self.top,
            "cut_after_bottom": self.bottom,
            "end": self.kind,
            "overhang": self.overhang,
        }


def _wrap(pos: int, n: int) -> int:
    return (pos - 1) % n + 1


def find_cuts(sg: SnapGeneFile, names: list[str]) -> list[Cut]:
    """Every cut the named enzymes make, in top-strand order."""
    if sg.kind != "dna":
        raise CliError("not_dna", f"{sg.name} is not a DNA file. Cutting needs DNA.")
    seq = Seq(sg.sequence)
    n = sg.length
    cuts = []
    for enzyme in lookup(names):
        for p in enzyme.search(seq, linear=not sg.circular):
            top = _wrap(p - 1, n)
            bottom = _wrap(top - enzyme.ovhg, n)
            site_start = _wrap(p - enzyme.fst5, n)
            site_end = _wrap(site_start + enzyme.size - 1, n)
            site_seq = seqtools.subseq(sg, site_start, site_end)
            # Where each cut sits inside the recognition site. An enzyme that cuts
            # outside its site (BsaI and other Golden Gate enzymes) destroys the
            # site, so there is nothing to rebuild and both strings stay empty.
            top_off = enzyme.fst5 if 0 <= enzyme.fst5 <= enzyme.size else None
            bottom_off = enzyme.fst5 - enzyme.ovhg
            bottom_off = bottom_off if 0 <= bottom_off <= enzyme.size else None
            if enzyme.ovhg < 0:
                kind, overhang = "5' overhang", seqtools.subseq(sg, _wrap(top + 1, n), bottom)
            elif enzyme.ovhg > 0:
                kind, overhang = "3' overhang", seqtools.subseq(sg, _wrap(bottom + 1, n), top)
            else:
                kind, overhang = "blunt", ""
            cuts.append(Cut(
                enzyme=str(enzyme),
                site=str(enzyme.site),
                site_start=site_start,
                site_end=site_end,
                site_seq=site_seq,
                top=top,
                bottom=bottom,
                kind=kind,
                overhang=overhang,
                restore_before=site_seq[:top_off] if top_off is not None else "",
                restore_after=site_seq[bottom_off:] if bottom_off is not None else "",
            ))
    cuts.sort(key=lambda c: (c.top, c.enzyme))
    return cuts


def fragments(sg: SnapGeneFile, cuts: list[Cut]) -> list[dict]:
    """The pieces the cuts make, described by their top-strand span and ends.

    A circular file with one cut gives one linear piece. A linear file with one
    cut gives two pieces, and its own ends are reported as "original".
    """
    if not cuts:
        return [{
            "from": 1, "to": sg.length, "length": sg.length,
            "left_end": "original", "right_end": "original",
            "left_enzyme": None, "right_enzyme": None,
        }]
    tops = [c.top for c in cuts]
    pieces = []
    if sg.circular:
        for i, cut in enumerate(cuts):
            nxt = cuts[(i + 1) % len(cuts)]
            start, end = _wrap(cut.top + 1, sg.length), nxt.top
            length = (end - start) % sg.length + 1 if len(cuts) > 1 else sg.length
            pieces.append((start, end, length, cut, nxt))
    else:
        bounds = [0] + tops + [sg.length]
        for i in range(len(bounds) - 1):
            start, end = bounds[i] + 1, bounds[i + 1]
            if start > end:
                continue
            left = cuts[i - 1] if i > 0 else None
            right = cuts[i] if i < len(cuts) else None
            pieces.append((start, end, end - start + 1, left, right))
    rows = []
    for start, end, length, left, right in pieces:
        rows.append({
            "from": start,
            "to": end,
            "length": length,
            "left_end": left.kind if left else "original",
            "right_end": right.kind if right else "original",
            "left_enzyme": left.enzyme if left else None,
            "right_enzyme": right.enzyme if right else None,
        })
    return rows
