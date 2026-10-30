"""Design PCR primers for a region, and check a primer against a file.

SnapGene's own primer design runs only in its window, and a licence unlocks it,
so the design here is done by Primer3 (the same engine most web primer tools
use) with its default salt and primer concentrations: 50 mM monovalent,
0.8 mM dNTP, 50 nM primer. Tm can differ from SnapGene's by about a degree.

Primer3 needs a straight piece of sequence, so a window around the target is
cut out first, wrapping the origin on a circular file, and the positions of the
primers it returns are then put back into the file's own numbering.
"""
from __future__ import annotations

from dataclasses import dataclass

import primer3

from cli_anything.snapgene.core import primers as primer_checks
from cli_anything.snapgene.core import seqtools
from cli_anything.snapgene.core.dnafile import SnapGeneFile
from cli_anything.snapgene.errors import CliError

CONTEXT = 600          # bases of template on each side of the target
PAIRS = 3
OPT_TM = 60.0
TM_RANGE = (57.0, 63.0)
GC_RANGE = (40.0, 60.0)
SIZE_RANGE = (18, 25)


@dataclass
class Window:
    """The straight piece of template handed to Primer3."""
    sequence: str
    first: int             # file position of template[0], 1-based
    target_offset: int     # 0-based offset of the target inside the template
    target_length: int


def window(sg: SnapGeneFile, start: int, end: int, context: int = CONTEXT) -> Window:
    n = sg.length
    if not (1 <= start <= n and 1 <= end <= n):
        raise CliError("bad_range", f"Positions must be between 1 and {n}. Got {start}-{end}.")
    if start > end and not sg.circular:
        raise CliError("bad_range", f"{sg.name} is linear, so --start must not be after --end.")
    target_length = (end - start) % n + 1 if start > end else end - start + 1
    if sg.circular:
        room = max(0, (n - target_length) // 2)
        context = min(context, room)
        first = (start - 1 - context) % n + 1
        length = min(n, target_length + 2 * context)
        offset = context
    else:
        first = max(1, start - context)
        last = min(n, end + context)
        length = last - first + 1
        offset = start - first
    sequence = "".join(sg.sequence[(first - 1 + i) % n] for i in range(length))
    return Window(sequence, first, offset, target_length)


def _run_primer3(seq_args: dict, global_args: dict) -> dict:
    try:
        return primer3.design_primers(seq_args, global_args)
    except OSError as exc:   # Primer3 reports bad settings this way
        raise CliError("tool_error", f"Primer3 refused these settings: {exc}") from exc


def _place(win: Window, sg: SnapGeneFile, offset: int, length: int) -> tuple[int, int]:
    n = sg.length
    start = (win.first - 1 + offset) % n + 1
    end = (win.first - 1 + offset + length - 1) % n + 1
    return start, end


def design(sg: SnapGeneFile, start: int, end: int, context: int = CONTEXT, pairs: int = PAIRS,
           opt_tm: float = OPT_TM, tm_range: tuple[float, float] = TM_RANGE,
           gc_range: tuple[float, float] = GC_RANGE, min_product: int | None = None,
           max_product: int | None = None) -> dict:
    """Primer pairs that amplify `start`-`end`, with the target kept whole."""
    if not sg.is_nucleotide:
        raise CliError("not_dna", f"{sg.name} is a protein file. PCR primers need DNA or RNA.")
    win = window(sg, start, end, context)
    if len(win.sequence) < win.target_length + 2 * SIZE_RANGE[0]:
        raise CliError("bad_range", "There is not enough sequence around the target for primers. "
                                    "Give a shorter range, or a file with more flanking sequence.")
    low = min_product or win.target_length
    high = max_product or len(win.sequence)
    if low > high:
        raise CliError("bad_range", f"--min-product {low} is more than --max-product {high}.")
    if not tm_range[0] <= opt_tm <= tm_range[1]:
        raise CliError("bad_request", f"--opt-tm {opt_tm} must be between {tm_range[0]} and {tm_range[1]}.")
    result = _run_primer3(
        {"SEQUENCE_ID": sg.name[:40] or "target",
         "SEQUENCE_TEMPLATE": win.sequence.replace("U", "T"),
         "SEQUENCE_TARGET": [win.target_offset, win.target_length]},
        {"PRIMER_TASK": "generic",
         "PRIMER_NUM_RETURN": max(1, pairs),
         "PRIMER_OPT_SIZE": 20,
         "PRIMER_MIN_SIZE": SIZE_RANGE[0],
         "PRIMER_MAX_SIZE": SIZE_RANGE[1],
         "PRIMER_OPT_TM": opt_tm,
         "PRIMER_MIN_TM": tm_range[0],
         "PRIMER_MAX_TM": tm_range[1],
         "PRIMER_MIN_GC": gc_range[0],
         "PRIMER_MAX_GC": gc_range[1],
         "PRIMER_PRODUCT_SIZE_RANGE": [[low, high]]},
    )
    found = int(result.get("PRIMER_PAIR_NUM_RETURNED", 0) or 0)
    rows = []
    for i in range(found):
        left_offset, left_len = result[f"PRIMER_LEFT_{i}"]
        right_offset, right_len = result[f"PRIMER_RIGHT_{i}"]
        forward = result[f"PRIMER_LEFT_{i}_SEQUENCE"]
        reverse = result[f"PRIMER_RIGHT_{i}_SEQUENCE"]
        f_start, f_end = _place(win, sg, left_offset, left_len)
        r_start, r_end = _place(win, sg, right_offset - right_len + 1, right_len)
        rows.append({
            "pair": i + 1,
            "product_bp": int(result[f"PRIMER_PAIR_{i}_PRODUCT_SIZE"]),
            "product_start": f_start,
            "product_end": r_end,
            "penalty": round(float(result[f"PRIMER_PAIR_{i}_PENALTY"]), 3),
            "forward": {
                "sequence": forward, "length": left_len, "start": f_start, "end": f_end, "strand": "+",
                "tm": round(float(result[f"PRIMER_LEFT_{i}_TM"]), 1),
                "gc_percent": round(float(result[f"PRIMER_LEFT_{i}_GC_PERCENT"]), 1),
                "three_prime_gc": primer_checks.three_prime_gc(forward),
                "hairpin_dg": primer_checks.hairpin_dg(forward),
            },
            "reverse": {
                "sequence": reverse, "length": right_len, "start": r_start, "end": r_end, "strand": "-",
                "tm": round(float(result[f"PRIMER_RIGHT_{i}_TM"]), 1),
                "gc_percent": round(float(result[f"PRIMER_RIGHT_{i}_GC_PERCENT"]), 1),
                "three_prime_gc": primer_checks.three_prime_gc(reverse),
                "hairpin_dg": primer_checks.hairpin_dg(reverse),
            },
            "delta_tm": round(abs(float(result[f"PRIMER_LEFT_{i}_TM"])
                                  - float(result[f"PRIMER_RIGHT_{i}_TM"])), 1),
        })
    notes = []
    if not rows:
        notes.append("Primer3 found no pair inside those limits. " + str(
            result.get("PRIMER_LEFT_EXPLAIN") or result.get("PRIMER_ERROR") or "").strip())
    return {
        "file": str(sg.path),
        "target": [start, end],
        "target_bp": win.target_length,
        "template": [win.first, (win.first - 1 + len(win.sequence) - 1) % sg.length + 1],
        "tm_method": primer_checks.TM_METHOD,
        "limits": {"tm": list(tm_range), "gc": list(gc_range), "length": list(SIZE_RANGE),
                   "product_bp": [low, high]},
        "pairs": rows,
        "warnings": notes,
    }


def check_primer(sg: SnapGeneFile, sequence: str) -> dict:
    """Where a primer binds a file, and what its numbers are."""
    seq = "".join(sequence.split()).upper()
    if not seq:
        raise CliError("bad_sequence", "Give the primer bases.")
    hits = seqtools.find_motif(sg, seq)
    report = primer_checks.anchor_report(seq)
    notes = primer_checks.warnings_for("Primer", report, seq, "the primer", TM_RANGE, GC_RANGE)
    if not hits:
        notes.append(f"The primer does not match {sg.name} exactly, on either strand.")
    elif len(hits) > 1:
        notes.append(f"The primer matches {len(hits)} places, so the PCR would not be clean.")
    return {
        "file": str(sg.path),
        "primer": seq,
        "length": len(seq),
        "tm": report["tm"],
        "gc_percent": report["gc_percent"],
        "three_prime_gc": report["three_prime_gc"],
        "hairpin_dg": primer_checks.hairpin_dg(seq),
        "self_dimer_dg": primer_checks.dimer_dg(seq),
        "binding_sites": hits,
        "tm_method": primer_checks.TM_METHOD,
        "warnings": notes,
    }
