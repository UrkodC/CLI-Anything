# pipeline.pml — PyMOL script invoked by driver.py
#
# Receives stages, params, output dir, and structure id/path through PyMOL's
# `-d` flag from driver.py. Stage selection is parsed from the PML environment
# (see _read_stage_args in driver.py for the contract).
#
# Replace the bodies of each stage with real logic. Each stage should:
#   - read inputs from the previous stage's outputs in $OUT_DIR
#   - write its outputs and a QC PNG (00_loaded.png, 01_render.png, ...)

python
import os, json
out_dir = os.environ.get("OUT_DIR", ".")
stages = os.environ.get("STAGES", "0-2")
pdb = os.environ.get("PDB", "")
params_path = os.environ.get("PARAMS", "")
print(f"[pipeline] pdb={pdb} stages={stages} out_dir={out_dir} params={params_path}")
python end

# --- Stage 0: load ---
# fetch $PDB, async=0
# save $OUT_DIR/state.pse
# png $OUT_DIR/00_loaded.png, width=1200, height=900, dpi=150, ray=0

# --- Stage 1: render ---
# bg_color white
# as cartoon
# spectrum chain
# ray 1600, 1200
# png $OUT_DIR/01_render.png, dpi=300

# --- Stage 2: export ---
# png $OUT_DIR/final.png, dpi=300
# save $OUT_DIR/final.pse
