"""qc.py — QC helpers for the _template Zotero skill.

After ``run``/``tune``, call ``composite_qc(run_dir)`` to produce a small
markdown digest summarizing how many items came back at each stage, what
got dropped by filters, and which records lacked critical fields
(abstract / DOI / attached PDF).
"""

from __future__ import annotations

import json
from pathlib import Path


def _safe_load(path: Path) -> list:
    if not path.exists():
        return []
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return []


def composite_qc(run_dir: Path) -> Path:
    """Write a single ``composite_qc.md`` digest into ``run_dir`` and return its path."""
    raw      = _safe_load(run_dir / "00_raw_items.json")
    filtered = _safe_load(run_dir / "01_filtered.json")
    enriched = _safe_load(run_dir / "02_enriched.json")

    missing_abstract = [it for it in enriched if not (it.get("abstract") or "").strip()]
    missing_doi      = [it for it in enriched if not (it.get("DOI") or it.get("doi"))]
    missing_pdf      = [it for it in enriched if not it.get("pdf_paths")]

    out = run_dir / "composite_qc.md"
    with open(out, "w") as f:
        f.write("# _template QC\n\n")
        f.write(f"- raw items:      {len(raw)}\n")
        f.write(f"- after filter:   {len(filtered)}\n")
        f.write(f"- enriched:       {len(enriched)}\n")
        f.write(f"- missing abstract: {len(missing_abstract)}\n")
        f.write(f"- missing DOI:      {len(missing_doi)}\n")
        f.write(f"- missing PDF:      {len(missing_pdf)}\n")
    return out
