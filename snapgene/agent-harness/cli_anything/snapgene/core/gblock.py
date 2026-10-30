"""Check a fragment before you order it as synthetic DNA.

The limits are the ones IDT publishes for gBlocks Gene Fragments: 125-3000 bp,
GC 25-75%, and no long runs of one base (10 or more A or T, 6 or more G or C).
Repeats and hairpins also make synthesis fail, and IDT says the decision is
"complex and multifactorial", so a clean report here is not a promise that the
order will be accepted. Other vendors use other limits.
"""
from __future__ import annotations

import re
from collections import Counter

from cli_anything.snapgene.core.primers import gc_percent

LENGTH_RANGE = (125, 3000)
GC_RANGE = (25.0, 75.0)
AT_RUN = 10
GC_RUN = 6
REPEAT_SIZE = 20


def runs(sequence: str) -> list[dict]:
    """Runs of one base that are long enough to worry about."""
    out = []
    for match in re.finditer(r"(.)\1*", sequence.upper()):
        size = len(match.group(0))
        base = match.group(1)
        limit = GC_RUN if base in "GC" else AT_RUN
        if size >= limit:
            out.append({"base": base, "length": size, "start": match.start() + 1})
    return out


def repeats(sequence: str, size: int = REPEAT_SIZE) -> list[dict]:
    """Stretches of `size` bases that turn up more than once."""
    sequence = sequence.upper()
    counts = Counter(sequence[i:i + size] for i in range(len(sequence) - size + 1))
    return [{"sequence": s, "times": c} for s, c in counts.most_common() if c > 1][:5]


def check(sequence: str, name: str = "fragment") -> dict:
    sequence = sequence.upper()
    low, high = LENGTH_RANGE
    gc = gc_percent(sequence)
    gc_low, gc_high = GC_RANGE
    long_runs = runs(sequence)
    repeated = repeats(sequence)
    notes = []
    if len(sequence) < low:
        notes.append(f"{name}: {len(sequence)} bp is shorter than IDT's {low} bp minimum.")
    if len(sequence) > high:
        notes.append(f"{name}: {len(sequence)} bp is longer than IDT's {high} bp gBlocks limit. "
                     "Order it as a longer gene fragment, or split it.")
    if not gc_low <= gc <= gc_high:
        notes.append(f"{name}: GC is {gc}%, outside IDT's {gc_low}-{gc_high}% range.")
    for run in long_runs:
        notes.append(f"{name}: run of {run['length']} {run['base']} at base {run['start']}.")
    if repeated:
        top = repeated[0]
        notes.append(f"{name}: {len(repeated)} repeated {REPEAT_SIZE}-base stretches, "
                     f"for example {top['sequence']} ({top['times']} times).")
    return {
        "length": len(sequence),
        "gc_percent": gc,
        "long_runs": long_runs,
        "repeats": repeated,
        "synthesis_warnings": notes,
        "sequence": sequence,
    }
