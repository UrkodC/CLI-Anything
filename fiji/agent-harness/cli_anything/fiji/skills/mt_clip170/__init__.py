"""mt_clip170 — Interactive MT + GFP-CLIP170 per-cell quantification pipeline.

Stages:
  0  Bio-Formats load + channel split + calibration dump
  1  DAPI nuclei seeds
  2  Tubulin whole-cell mask
  3  Marker-controlled watershed cell isolation
  4  GFP+ classification
  5  MT Tubeness -> skeleton -> per-cell total length
  6  CLIP170 comets -> skeleton -> per-cell total length
  7  MT-CLIP170 co-localization
  8  Merge + summary + composite QC

Entry point: cli_anything.fiji.skills.mt_clip170.driver:cli
"""

__all__ = ["driver"]
