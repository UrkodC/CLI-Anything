# _template — placeholder FreeCAD skill

Rename this folder to your skill slug (e.g. `parametric_bracket`) and fill in
the stages, commands, and parameter table below. The skill follows the same
structure as the Fiji `mt_clip170` reference skill.

## Stages

| # | Name | Purpose | Output |
|---|------|---------|--------|
| 0 | build  | Generate parametric geometry from params.yaml | `model.json`, `00_build.log` |
| 1 | export | Run headless FreeCAD to produce STEP/STL      | `model.step`, `model.stl` |
| 2 | qc     | Render isometric preview PNG for review       | `02_preview.png` |

## Commands

```
# Full pipeline
python3 -m cli_anything.freecad _template run --out ./out

# Override a parameter from the CLI
python3 -m cli_anything.freecad _template run --set length=120 --set holes=4 --out ./out

# Iterate on a subset of stages
python3 -m cli_anything.freecad _template tune --stages 0-0 --out ./out

# Print current parameters
python3 -m cli_anything.freecad _template params
```

Output is written to `./_template_out/<timestamp>/`.

## Dependencies

- FreeCAD with `freecadcmd` on PATH (or at the auto-discovered macOS path).
- Python: `click`, `pyyaml`

## Tuning workflow

1. Edit `params.yaml`, then `tune --stages 0` to confirm the model JSON looks right.
2. `tune --stages 1` to verify the export succeeds.
3. `run` end-to-end and inspect `02_preview.png`.
