"""qc.py — QC plot helpers for the _template FreeCAD skill.

Use to render an isometric preview PNG or compose per-stage logs into one
review-friendly summary after the headless freecadcmd pass.
"""

from __future__ import annotations

from pathlib import Path


def composite_qc(run_dir: Path) -> Path:
    """Stub: assemble per-stage outputs (logs, model.json, preview) into one PNG."""
    out = run_dir / "composite_qc.png"
    # TODO: build a 2x2 panel with PIL/matplotlib showing the STEP preview,
    # the param table, and the export log tail.
    out.touch()
    return out
