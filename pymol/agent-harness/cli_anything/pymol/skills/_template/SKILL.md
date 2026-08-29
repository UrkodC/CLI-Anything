# _template — placeholder PyMOL skill

Rename this folder to your skill slug (e.g. `coiled_coil_render`) and fill in
the stages, commands, and parameter table below. The skill follows the same
structure as the Fiji `mt_clip170` reference skill.

## Stages

| # | Name | Purpose | Output |
|---|------|---------|--------|
| 0 | load   | Fetch/open structure(s) and set basic view | `state.pse`, `00_loaded.png` |
| 1 | render | Apply representations, colors, lighting     | `01_render.png` |
| 2 | export | Write final figure-ready PNG + session      | `final.png`, `final.pse` |

## Commands

```
# Full pipeline on one structure
python3 -m cli_anything.pymol _template run --pdb 1UBQ

# Iterate on a subset of stages with logs
python3 -m cli_anything.pymol _template tune --pdb 1UBQ --stages 0-1

# Batch over a list of PDB IDs once params are locked
python3 -m cli_anything.pymol _template batch --pdb-list pdb_ids.txt

# Print current parameters
python3 -m cli_anything.pymol _template params
```

Output is written to `./_template_out/<pdb_id>/<timestamp>/`.

## Dependencies

- PyMOL (`pymol -cq` for headless execution)
- Python: `click`, `pyyaml`

## Tuning workflow

1. `tune --stages 0` — confirm structure loads and view is reasonable.
2. `tune --stages 1` — iterate on representation/color/lighting parameters.
3. `run` once happy with params; `batch` for production runs.
