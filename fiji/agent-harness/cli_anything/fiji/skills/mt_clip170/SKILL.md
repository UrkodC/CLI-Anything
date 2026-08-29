# mt_clip170 — Interactive MT + GFP-CLIP170 per-cell quantification

Pipeline for 3-channel microscopy images of transfected cells:
- **C1** α-tubulin (microtubule network)
- **C2** GFP-CLIP170 variant (comets; also transfection marker)
- **C3** DAPI (nuclei)

Produces per-cell measurements (microtubule total length, CLIP170 comet length,
CLIP170–MT co-localization) restricted to GFP-positive cells.

## Stages

| # | Name | Purpose | Output |
|---|------|---------|--------|
| 0 | load_split | Bio-Formats open, split channels, dump calibration | `ch_tubulin.tif`, `ch_gfp.tif`, `ch_dapi.tif`, `calibration.json`, `00_raw_channels.png` |
| 1 | nuclei | DAPI → nuclei seed labels | `nuclei_labels.tif`, `01_nuclei_labels.png` |
| 2 | cell_mask | Tubulin → whole-cell binary mask | `cell_mask.tif`, `02_tubulin_mask.png` |
| 3 | cells | Marker-controlled watershed → per-cell ROIs | `cell_labels.tif`, `cell_rois.zip`, `03_cell_labels.png` |
| 4 | gfp_class | Classify cells as GFP+ / GFP- | `gfp_classification.csv`, `04_gfp_classification.png` |
| 5 | mt_length | Tubeness → skel → per-cell total MT length | `mt_lengths.csv`, `mt_skeleton.tif`, `05_mt_skeleton.png` |
| 6 | clip_comets | GFP comets → skel → per-cell total CLIP170 length | `clip170_comets.csv`, `clip170_mask.tif`, `06_clip170_comets.png` |
| 7 | coloc | MT-dilated mask ∩ CLIP170 → per-cell fraction | `coloc.csv`, `07_colocalization.png` |
| 8 | finalize | Merge per-stage CSVs, summary, composite QC | `cells.csv`, `summary.csv`, `composite_qc.png`, `params_used.yaml` |

## Commands

```
# Full pipeline on one image
python3 -m cli_anything.fiji mt-clip170 run --image IMG.nd2

# Iterate on a subset of stages (with live stdout + per-stage logs)
python3 -m cli_anything.fiji mt-clip170 tune --image IMG.nd2 --stages 0-3

# Batch over a folder once params are locked
python3 -m cli_anything.fiji mt-clip170 batch --input-dir DIR

# Print current parameters
python3 -m cli_anything.fiji mt-clip170 params
```

Output is written to `./mt_clip170_out/<image_name>/<timestamp>/`.

## Dependencies

- Fiji at `/Applications/Fiji.app/` (macOS) with plugins:
  - **Bio-Formats** (ships with Fiji)
  - **Tubeness** (ships with Fiji via VIB-lib)
  - **Skeletonize3D / AnalyzeSkeleton** (ships with Fiji)
  - **MorphoLibJ** 1.6.5+ (`plugins/MorphoLibJ_-1.6.5.jar`)
- Python: `click`, `pyyaml`

## Tuning workflow

1. Run `tune --stages 0-3` and open `03_cell_labels.png`. Adjust:
   - `dapi.threshold_method`, `dapi.min_nucleus_area_um2`
   - `tubulin_mask.threshold_method`, `tubulin_mask.bg_rolling_ball_um`
   - `cells.min_area_um2`, `cells.max_area_um2`
2. Run `tune --stages 4-4` and open `04_gfp_classification.png`. Adjust `gfp_classification.fold_over_bg`.
3. Run `tune --stages 5-7` and inspect skeleton + coloc overlays. Adjust `mt.tubeness_sigma_um`, `clip170.*`.
4. Run full pipeline with `run`, then `batch` when satisfied.
