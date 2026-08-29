"""qc.py — QC plot helpers for the _template PyMOL skill.

Each stage in pipeline.pml can call into this module (via driver.py) to render
a small QC overlay PNG so you can inspect intermediate state without opening
the .pse session.
"""

from __future__ import annotations

from pathlib import Path


def composite_qc(run_dir: Path) -> Path:
    """Stub: assemble per-stage QC PNGs into a single composite for review."""
    out = run_dir / "composite_qc.png"
    # TODO: stitch 00_loaded.png + 01_render.png with PIL/matplotlib.
    out.touch()
    return out
