"""Restriction sites, with enzyme data from REBASE (through Biopython).

SnapGene's own enzyme database is compiled into the app and cannot be read, so
this module uses REBASE, the same source SnapGene takes its enzymes from.
A position is the last top-strand base before the cut, the number SnapGene
shows next to the enzyme name on the map (EcoRI (396) in pUC19).
Methylation blocking (Dam, Dcm) is not applied.
"""
from __future__ import annotations

from Bio.Restriction import AllEnzymes, Analysis, CommOnly
from Bio.Seq import Seq

from cli_anything.snapgene.core.dnafile import SnapGeneFile
from cli_anything.snapgene.errors import CliError


def lookup(names: list[str]) -> list:
    """Enzyme objects for names like EcoRI. Case matters in REBASE, so match case-insensitively."""
    index = {str(e).casefold(): e for e in AllEnzymes}
    found, unknown = [], []
    for name in names:
        enzyme = index.get(name.strip().casefold())
        (found if enzyme else unknown).append(enzyme or name)
    if unknown:
        raise CliError("unknown_enzyme", f"Unknown enzyme name: {', '.join(unknown)}.")
    return found


def cut_sites(
    sg: SnapGeneFile,
    names: list[str] | None = None,
    max_cuts: int | None = 1,
    min_site: int = 6,
    commercial_only: bool = True,
) -> list[dict]:
    """Enzymes that cut sg.

    With `names`, report exactly those enzymes (also when they do not cut).
    Without `names`, report enzymes from the commercial (or full) REBASE set whose
    recognition site is at least `min_site` bases and that cut at most `max_cuts`
    times. The defaults (min_site 6, max_cuts 1) match SnapGene's
    "Unique 6+ Cutters" view.
    """
    if sg.kind != "dna":
        raise CliError("not_dna", f"{sg.name} is not a DNA file. Restriction sites need DNA.")
    seq = Seq(sg.sequence)
    linear = not sg.circular
    n = sg.length
    if names:
        enzymes = lookup(names)
    else:
        pool = CommOnly if commercial_only else AllEnzymes
        enzymes = [e for e in pool if e.size >= min_site]
    hits = Analysis(enzymes, seq, linear=linear).full()
    rows = []
    for enzyme in enzymes:
        cuts = sorted({(p - 2) % n + 1 for p in hits.get(enzyme, [])})
        if not names and (not cuts or (max_cuts is not None and len(cuts) > max_cuts)):
            continue
        rows.append({
            "enzyme": str(enzyme),
            "site": str(enzyme.site),
            "cuts": len(cuts),
            "positions": cuts,
        })
    rows.sort(key=lambda r: (r["positions"][0] if r["positions"] else n + 1, r["enzyme"]))
    return rows
