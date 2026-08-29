"""pipeline.py — FreeCAD macro body invoked headlessly by driver.py.

Driver writes a small bootstrap that imports this file inside freecadcmd
with PARAMS / OUT_DIR / STAGES exposed via environment variables (see
driver._run_freecad). Replace each stage with the geometry and export logic
you need; macro_gen helpers from cli_anything.freecad.utils.freecad_macro_gen
(_to_3d, _normal, _emit_primitive, _placement_expr, ...) are available if
you want to generate macros instead of using the FreeCAD Python API directly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> None:
    out_dir = Path(os.environ.get("OUT_DIR", "."))
    out_dir.mkdir(parents=True, exist_ok=True)
    stages = os.environ.get("STAGES", "0-2")
    params_path = os.environ.get("PARAMS", "")
    params = {}
    if params_path and Path(params_path).exists():
        # We can't depend on pyyaml inside freecadcmd; read JSON snapshot instead.
        # driver.py converts params.yaml -> params.json before invoking us.
        try:
            with open(params_path) as f:
                params = json.load(f)
        except Exception as exc:
            print(f"[_template] params load failed: {exc}")

    print(f"[_template] stages={stages} out_dir={out_dir} params={params}")

    # --- Stage 0: build ---
    # import FreeCAD, Part
    # doc = FreeCAD.newDocument()
    # box = doc.addObject('Part::Box', 'Part')
    # box.Length, box.Width, box.Height = params['build']['length'], ...
    # doc.recompute()
    # (out_dir / 'model.json').write_text(json.dumps(params['build']))

    # --- Stage 1: export ---
    # import Mesh, ImportGui
    # if params['export']['step']: ImportGui.export([box], str(out_dir / 'model.step'))
    # if params['export']['stl']:  Mesh.export([box], str(out_dir / 'model.stl'))

    # --- Stage 2: qc ---
    # Render isometric PNG via FreeCAD.Gui (requires offscreen-capable build)
    # or call a separate matplotlib-based renderer in driver.py post-export.


if __name__ == "__main__":
    main()
