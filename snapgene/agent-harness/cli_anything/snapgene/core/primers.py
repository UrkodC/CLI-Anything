"""Primer numbers: Tm, GC, 3'-end bases, hairpins and dimers.

SnapGene shows a Tm in its primer view, but the app cannot be called from
outside, so the numbers here come from Primer3 (SantaLucia 1998 nearest
neighbour, Primer3 default salt and primer concentrations: 50 mM monovalent,
0.8 mM dNTP, 50 nM primer). They will differ from SnapGene's value by a degree
or so.

The windows are Takara's, for In-Fusion primers (In-Fusion HD manual PT5165-1,
section IV.C):

- the gene-specific 3' portion is 18-25 bases, GC 40-60%, Tm 58-65 C
- the forward and reverse Tm must be within 4 C of each other
- the last five bases of the primer must hold no more than two G or C
- avoid runs of the same base, hairpins and primer dimers

Tm is measured on the gene-specific portion only, never on the whole primer.
"""
from __future__ import annotations

import re

import primer3

TM_RANGE = (58.0, 65.0)
GC_RANGE = (40.0, 60.0)
ANCHOR_LENGTHS = range(18, 26)
MAX_DELTA_TM = 4.0
MAX_3PRIME_GC = 2
MAX_RUN = 4
HAIRPIN_DG = -5.0      # kcal/mol, rule of thumb, not a Takara number
DIMER_DG = -12.0       # kcal/mol, rule of thumb, not a Takara number
LONG_PRIMER = 45
TM_METHOD = "Primer3, SantaLucia 1998, 50 mM Na+, 50 nM primer"


def gc_percent(seq: str) -> float:
    seq = seq.upper()
    return round(100 * sum(c in "GC" for c in seq) / len(seq), 1) if seq else 0.0


def tm(seq: str) -> float:
    return round(primer3.calc_tm(seq.upper()), 1) if len(seq) >= 8 else 0.0


def longest_run(seq: str) -> tuple[str, int]:
    """The longest run of one base, for example ('A', 5)."""
    best = max(re.finditer(r"(.)\1*", seq.upper()), key=lambda m: len(m.group(0)), default=None)
    return (best.group(1), len(best.group(0))) if best else ("", 0)


def three_prime_gc(seq: str, window: int = 5) -> int:
    return sum(c in "GC" for c in seq.upper()[-window:])


def hairpin_dg(seq: str) -> float:
    """Free energy of the best hairpin, in kcal/mol. 0.0 means none found."""
    return round(primer3.calc_hairpin(seq.upper()).dg / 1000, 2)


def dimer_dg(a: str, b: str | None = None) -> float:
    """Free energy of the strongest dimer, in kcal/mol. 0.0 means none found."""
    result = primer3.calc_homodimer(a.upper()) if b is None else primer3.calc_heterodimer(a.upper(), b.upper())
    return round(result.dg / 1000, 2)


def anchor_report(anchor: str) -> dict:
    """Numbers for one gene-specific portion."""
    base, run = longest_run(anchor)
    return {
        "anchor": anchor,
        "anchor_length": len(anchor),
        "tm": tm(anchor),
        "gc_percent": gc_percent(anchor),
        "three_prime_gc": three_prime_gc(anchor),
        "longest_run": run,
        "longest_run_base": base,
    }


def _penalty(report: dict) -> float:
    low, high = TM_RANGE
    score = 10 * (max(0.0, low - report["tm"]) + max(0.0, report["tm"] - high))
    gc_low, gc_high = GC_RANGE
    score += 2 * (max(0.0, gc_low - report["gc_percent"]) + max(0.0, report["gc_percent"] - gc_high))
    score += 8 * max(0, report["three_prime_gc"] - MAX_3PRIME_GC)
    score += 5 * max(0, report["longest_run"] - MAX_RUN)
    return score


def choose_anchors(forward_template: str, reverse_template: str) -> tuple[dict, dict]:
    """Pick the gene-specific portion of each primer.

    `forward_template` is the insert read 5'->3' from its start; the forward
    anchor is its first bases. `reverse_template` is the insert's other strand
    read 5'->3' from its own start (the reverse complement of the insert's end);
    the reverse anchor is its first bases.
    """
    forward = [anchor_report(forward_template[:n]) for n in ANCHOR_LENGTHS if len(forward_template) >= n]
    reverse = [anchor_report(reverse_template[:n]) for n in ANCHOR_LENGTHS if len(reverse_template) >= n]
    if not forward:
        forward = [anchor_report(forward_template)]
    if not reverse:
        reverse = [anchor_report(reverse_template)]
    best = None
    for f in forward:
        for r in reverse:
            delta = abs(f["tm"] - r["tm"])
            score = (_penalty(f) + _penalty(r) + 5 * max(0.0, delta - MAX_DELTA_TM),
                     delta, f["anchor_length"] + r["anchor_length"])
            if best is None or score < best[0]:
                best = (score, f, r)
    return best[1], best[2]


def warnings_for(name: str, report: dict, sequence: str, what: str = "the gene-specific part",
                 tm_range: tuple[float, float] = TM_RANGE,
                 gc_range: tuple[float, float] = GC_RANGE) -> list[str]:
    """Plain warnings for one primer. An empty list means every rule passed."""
    out = []
    low, high = tm_range
    if not low <= report["tm"] <= high:
        out.append(f"{name}: Tm of {what} is {report['tm']} C, outside {low}-{high} C.")
    gc_low, gc_high = gc_range
    if not gc_low <= report["gc_percent"] <= gc_high:
        out.append(f"{name}: GC of {what} is {report['gc_percent']}%, outside {gc_low}-{gc_high}%.")
    if report["three_prime_gc"] > MAX_3PRIME_GC:
        out.append(f"{name}: the last 5 bases hold {report['three_prime_gc']} G or C. "
                   f"More than {MAX_3PRIME_GC} makes the 3' end stick where it should not.")
    if report["longest_run"] > MAX_RUN:
        out.append(f"{name}: run of {report['longest_run']} {report['longest_run_base']} in {what}.")
    dg = hairpin_dg(sequence)
    if dg < HAIRPIN_DG:
        out.append(f"{name}: may fold on itself (hairpin {dg} kcal/mol).")
    self_dg = dimer_dg(sequence)
    if self_dg < DIMER_DG:
        out.append(f"{name}: may pair with itself (self dimer {self_dg} kcal/mol).")
    if len(sequence) > LONG_PRIMER:
        out.append(f"{name}: {len(sequence)} bases. Over {LONG_PRIMER}, Takara suggests PAGE purification.")
    return out


def pair_warnings(forward: dict, reverse: dict, fwd_seq: str, rev_seq: str) -> list[str]:
    out = []
    delta = round(abs(forward["tm"] - reverse["tm"]), 1)
    if delta > MAX_DELTA_TM:
        out.append(f"The two Tm values differ by {delta} C. Takara allows {MAX_DELTA_TM} C.")
    dg = dimer_dg(fwd_seq, rev_seq)
    if dg < DIMER_DG:
        out.append(f"The two primers may pair with each other (dimer {dg} kcal/mol).")
    return out
