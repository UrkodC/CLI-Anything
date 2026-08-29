"""mt_clip170 QC helpers — composite PNG assembly + CSV merge.

Pure Python (no Fiji needed). Pillow is used for image composition; if
unavailable, composite assembly is skipped silently.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_OK = True
except ImportError:
    PIL_OK = False


# ─── CSV merge ──────────────────────────────────────────────────────

PER_STAGE_FILES = {
    "cells_area.csv":         ["cell_area_um2", "centroid_x_px", "centroid_y_px"],
    "gfp_classification.csv": ["mean_gfp", "is_gfp_positive", "bg_median", "bg_robust_std", "gate"],
    "mt_lengths.csv":         ["mt_ridge_length_um", "mt_mask_area_um2", "mt_density", "mt_intensity_integrated"],
    "clip170_comets.csv":     ["clip170_ridge_length_um", "clip170_ridge_fg_px"],
    "coloc.csv":              ["mt_length_um", "clip170_length_um", "coloc_length_um",
                               "clip170_on_mt_fraction", "mt_with_clip170_fraction"],
}


def _read_csv_indexed(path: Path) -> dict:
    """Read a CSV; return {cell_id_str: {col: val}}."""
    out = {}
    if not path.exists():
        return out
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            cid = row.get("cell_id", "").strip()
            if cid:
                out[cid] = row
    return out


def merge_cells_csv(run_dir: Path) -> Optional[Path]:
    """Read all per-stage CSVs in run_dir and join on cell_id.

    Writes cells.csv with one row per cell containing all available columns.
    Returns the path, or None if there's nothing to merge.
    """
    by_stage = {}
    for fname, cols in PER_STAGE_FILES.items():
        rows = _read_csv_indexed(run_dir / fname)
        if rows:
            by_stage[fname] = (rows, cols)

    if not by_stage:
        return None

    # Collect all cell IDs across stages
    all_ids = set()
    for rows, _ in by_stage.values():
        all_ids.update(rows.keys())
    try:
        ordered = sorted(all_ids, key=int)
    except ValueError:
        ordered = sorted(all_ids)

    # Build column order: cell_id first, then a curated useful subset, then everything else
    primary = [
        "cell_id",
        "is_gfp_positive",
        "cell_area_um2",
        "mean_gfp",
        "mt_ridge_length_um",
        "mt_density",
        "clip170_ridge_length_um",
        "coloc_length_um",
        "clip170_on_mt_fraction",
    ]
    seen = set(primary)
    extras: list[str] = []
    for fname, (rows, cols) in by_stage.items():
        for c in cols:
            if c not in seen:
                extras.append(c)
                seen.add(c)
    header = primary + extras

    out_path = run_dir / "cells.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for cid in ordered:
            row_out = {"cell_id": cid}
            for fname, (rows, _) in by_stage.items():
                src = rows.get(cid, {})
                for k, v in src.items():
                    if k != "cell_id" and v != "" and k not in row_out:
                        row_out[k] = v
            w.writerow([row_out.get(c, "") for c in header])
    return out_path


def write_summary_csv(run_dir: Path, image_name: str) -> Optional[Path]:
    """Compute per-image aggregate stats restricted to GFP+ cells."""
    cells_path = run_dir / "cells.csv"
    if not cells_path.exists():
        return None

    rows = []
    with open(cells_path) as f:
        for r in csv.DictReader(f):
            rows.append(r)

    def numlist(rs, col):
        out = []
        for r in rs:
            v = r.get(col, "")
            if v in ("", None):
                continue
            try:
                out.append(float(v))
            except ValueError:
                pass
        return out

    pos_rows = [r for r in rows if r.get("is_gfp_positive", "") in ("1", "1.0")]

    def _mean(xs): return (sum(xs) / len(xs)) if xs else 0.0

    summary = {
        "image": image_name,
        "n_cells_total": len(rows),
        "n_gfp_positive": len(pos_rows),
        "n_gfp_negative": len(rows) - len(pos_rows),
        "mean_cell_area_um2":         _mean(numlist(pos_rows, "cell_area_um2")),
        "mean_mt_ridge_length_um":    _mean(numlist(pos_rows, "mt_ridge_length_um")),
        "mean_mt_density":            _mean(numlist(pos_rows, "mt_density")),
        "mean_clip170_length_um":     _mean(numlist(pos_rows, "clip170_ridge_length_um")),
        "mean_coloc_length_um":       _mean(numlist(pos_rows, "coloc_length_um")),
        "mean_clip170_on_mt_fraction": _mean(numlist(pos_rows, "clip170_on_mt_fraction")),
        "total_mt_ridge_length_um":   sum(numlist(pos_rows, "mt_ridge_length_um")),
        "total_clip170_length_um":    sum(numlist(pos_rows, "clip170_ridge_length_um")),
        "total_coloc_length_um":      sum(numlist(pos_rows, "coloc_length_um")),
    }

    out_path = run_dir / "summary.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary.keys()))
        w.writeheader()
        w.writerow(summary)
    return out_path


# ─── Composite QC PNG (4 panels: Stage 0/3/5/7) ─────────────────────

PANELS = [
    ("00_raw_channels.png",      "Raw channels"),
    ("03_cell_labels.png",       "Cells"),
    ("05_mt_skeleton.png",       "MT ridges"),
    ("07_colocalization.png",    "MT/CLIP170 coloc"),
]


def compose_qc(run_dir: Path, max_panel_px: int = 1024) -> Optional[Path]:
    """Build composite_qc.png by stacking the per-stage PNGs in a 2×2 grid.

    Each panel is downsampled to fit within max_panel_px on the longer side
    so the final image stays under ~10 MB.
    """
    if not PIL_OK:
        return None

    available = []
    for fname, label in PANELS:
        p = run_dir / fname
        if p.exists():
            available.append((p, label))
    if not available:
        return None

    panels = []
    for p, label in available:
        try:
            img = Image.open(p).convert("RGB")
        except Exception:
            continue
        # Downsample
        scale = max_panel_px / max(img.size)
        if scale < 1:
            new = (int(img.size[0] * scale), int(img.size[1] * scale))
            img = img.resize(new, Image.LANCZOS)
        # Add label bar
        bar_h = 32
        labelled = Image.new("RGB", (img.size[0], img.size[1] + bar_h), (0, 0, 0))
        labelled.paste(img, (0, bar_h))
        draw = ImageDraw.Draw(labelled)
        try:
            font = ImageFont.truetype("Helvetica", 22)
        except OSError:
            font = ImageFont.load_default()
        draw.text((10, 4), label, fill=(255, 255, 0), font=font)
        panels.append(labelled)

    # 2×2 grid (or 1×N if fewer than 2)
    if len(panels) >= 2:
        cols = 2
    else:
        cols = 1
    rows = (len(panels) + cols - 1) // cols
    cell_w = max(p.size[0] for p in panels)
    cell_h = max(p.size[1] for p in panels)
    composite = Image.new("RGB", (cols * cell_w, rows * cell_h), (0, 0, 0))
    for i, p in enumerate(panels):
        r, c = divmod(i, cols)
        composite.paste(p, (c * cell_w, r * cell_h))

    out_path = run_dir / "composite_qc.png"
    composite.save(out_path, "PNG", optimize=True)
    return out_path
