"""Fiji CLI - Figure assembly module.

Builds multi-panel figures from individual images using
ImageJ montage and annotation tools.
"""

from typing import Dict, Any, List, Optional


FIGURE_PRESETS = {
    "nature_single": {
        "name": "nature_single",
        "description": "Nature single column (89 mm)",
        "width_mm": 89,
        "dpi": 300,
        "width_px": 1051,
        "font": "Arial",
        "min_font_pt": 7,
    },
    "nature_double": {
        "name": "nature_double",
        "description": "Nature double column (183 mm)",
        "width_mm": 183,
        "dpi": 300,
        "width_px": 2161,
        "font": "Arial",
        "min_font_pt": 7,
    },
    "science_single": {
        "name": "science_single",
        "description": "Science single column (55 mm)",
        "width_mm": 55,
        "dpi": 300,
        "width_px": 650,
        "font": "Helvetica",
        "min_font_pt": 6,
    },
    "science_double": {
        "name": "science_double",
        "description": "Science double column (175 mm)",
        "width_mm": 175,
        "dpi": 300,
        "width_px": 2067,
        "font": "Helvetica",
        "min_font_pt": 6,
    },
    "cell_single": {
        "name": "cell_single",
        "description": "Cell single column (85 mm)",
        "width_mm": 85,
        "dpi": 300,
        "width_px": 1004,
        "font": "Arial",
        "min_font_pt": 6,
    },
    "cell_double": {
        "name": "cell_double",
        "description": "Cell double column (178 mm)",
        "width_mm": 178,
        "dpi": 300,
        "width_px": 2102,
        "font": "Arial",
        "min_font_pt": 6,
    },
}


def list_figure_presets() -> List[Dict[str, Any]]:
    """List available figure presets for journals."""
    return [
        {
            "name": p["name"],
            "description": p["description"],
            "width_mm": p["width_mm"],
            "dpi": p["dpi"],
        }
        for p in FIGURE_PRESETS.values()
    ]


def get_figure_preset(name: str) -> Dict[str, Any]:
    """Get a specific figure preset."""
    if name not in FIGURE_PRESETS:
        available = ", ".join(sorted(FIGURE_PRESETS.keys()))
        raise ValueError(f"Unknown preset: {name}. Available: {available}")
    return dict(FIGURE_PRESETS[name])


def build_montage_macro(
    panel_paths: List[str],
    columns: int = 2,
    rows: int = 2,
    border: int = 2,
    labels: Optional[List[str]] = None,
    scale_bar_width: Optional[int] = None,
    scale_bar_color: str = "White",
    scale_bar_height: int = 4,
    scale_bar_font: int = 14,
    flatten: bool = False,
    output_path: Optional[str] = None,
) -> str:
    """Build an ImageJ macro to assemble a multi-panel figure."""
    lines = ['setBatchMode(true);']

    for i, path in enumerate(panel_paths):
        lines.append(f'open("{path}");')
        title = f"panel_{i}"
        lines.append(f'rename("{title}");')

        if scale_bar_width is not None:
            lines.append(
                f'run("Scale Bar...", "width={scale_bar_width} height={scale_bar_height} '
                f'font={scale_bar_font} color={scale_bar_color} background=None '
                f'location=[Lower Right] bold overlay");'
            )

        if flatten:
            lines.append('run("Flatten");')

    lines.append('run("Images to Stack", "use");')
    lines.append(
        f'run("Make Montage...", "columns={columns} rows={rows} '
        f'scale=1 border={border}");'
    )

    if output_path:
        lines.append(f'saveAs("Tiff", "{output_path}");')

    lines.append('setBatchMode(false);')
    return "\n".join(lines)
