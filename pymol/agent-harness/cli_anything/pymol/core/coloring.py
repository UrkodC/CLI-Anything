"""PyMOL CLI - Color management for molecular structures."""

from typing import Dict, Any, Optional, List

# Named colors available in PyMOL
NAMED_COLORS = {
    "red": [1.0, 0.0, 0.0],
    "green": [0.0, 1.0, 0.0],
    "blue": [0.0, 0.0, 1.0],
    "yellow": [1.0, 1.0, 0.0],
    "cyan": [0.0, 1.0, 1.0],
    "magenta": [1.0, 0.0, 1.0],
    "white": [1.0, 1.0, 1.0],
    "black": [0.0, 0.0, 0.0],
    "orange": [1.0, 0.5, 0.0],
    "pink": [1.0, 0.65, 0.85],
    "purple": [0.5, 0.0, 0.5],
    "gray": [0.5, 0.5, 0.5],
    "lightgray": [0.75, 0.75, 0.75],
    "darkgray": [0.25, 0.25, 0.25],
    "salmon": [1.0, 0.6, 0.6],
    "teal": [0.0, 0.5, 0.5],
    "olive": [0.5, 0.5, 0.0],
    "brown": [0.65, 0.32, 0.17],
    "slate": [0.5, 0.5, 1.0],
    "forest": [0.2, 0.6, 0.2],
    "firebrick": [0.7, 0.13, 0.13],
    "marine": [0.0, 0.5, 1.0],
    "hotpink": [1.0, 0.41, 0.71],
    "chocolate": [0.55, 0.27, 0.07],
    "tv_red": [1.0, 0.2, 0.2],
    "tv_green": [0.2, 1.0, 0.2],
    "tv_blue": [0.3, 0.3, 1.0],
    "tv_yellow": [1.0, 1.0, 0.2],
    "tv_orange": [1.0, 0.55, 0.15],
    "smudge": [0.55, 0.7, 0.4],
    "deepblue": [0.25, 0.25, 0.65],
    "deepteal": [0.1, 0.6, 0.6],
    "limon": [0.75, 1.0, 0.0],
    "sand": [0.72, 0.55, 0.3],
    "wheat": [0.99, 0.82, 0.65],
}

# Color schemes
COLOR_SCHEMES = {
    "by_element": {
        "description": "Color by chemical element (CPK coloring)",
        "pymol_command": "color atomic",
    },
    "by_chain": {
        "description": "Color each chain a different color",
        "pymol_command": "util.cbc",
    },
    "by_ss": {
        "description": "Color by secondary structure (helix=red, sheet=yellow, loop=green)",
        "pymol_command": "util.cbss",
    },
    "by_bfactor": {
        "description": "Color by B-factor (temperature factor) as spectrum",
        "pymol_command": "spectrum b",
    },
    "by_residue_type": {
        "description": "Color by residue type (hydrophobic, polar, charged)",
        "pymol_command": "util.cbag",
    },
    "rainbow": {
        "description": "Rainbow coloring from N to C terminus",
        "pymol_command": "spectrum count",
    },
    "chainbow": {
        "description": "Rainbow coloring within each chain",
        "pymol_command": "util.chainbow",
    },
    "spectrum_blue_red": {
        "description": "Blue to red spectrum by residue index",
        "pymol_command": "spectrum count, blue_red",
    },
    "spectrum_blue_white_red": {
        "description": "Blue to white to red spectrum",
        "pymol_command": "spectrum b, blue_white_red",
    },
    "green_carbon": {
        "description": "Green carbons, standard other elements",
        "pymol_command": "util.cbag",
    },
}


def _next_id(project: Dict[str, Any]) -> int:
    colors = project.get("colors", [])
    if not colors:
        return 0
    return max(c.get("id", 0) for c in colors) + 1


def apply_color(
    project: Dict[str, Any],
    target: str,
    color: Optional[str] = None,
    rgb: Optional[List[float]] = None,
    scheme: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply a color or color scheme to a target.

    Exactly one of color, rgb, or scheme must be provided.

    Args:
        project: Project dict.
        target: Object name or selection expression.
        color: Named color (e.g., "red", "marine").
        rgb: RGB values [r, g, b] each 0.0-1.0.
        scheme: Color scheme name (e.g., "by_element", "by_chain").

    Returns:
        The color entry dict.
    """
    provided = sum(x is not None for x in [color, rgb, scheme])
    if provided != 1:
        raise ValueError("Exactly one of color, rgb, or scheme must be provided.")

    if color is not None:
        if color not in NAMED_COLORS:
            raise ValueError(f"Unknown color: {color}. Available: {sorted(NAMED_COLORS.keys())}")
        color_rgb = NAMED_COLORS[color]
        color_type = "named"
    elif rgb is not None:
        if len(rgb) != 3:
            raise ValueError(f"RGB must have 3 components, got {len(rgb)}")
        for c in rgb:
            if not (0.0 <= c <= 1.0):
                raise ValueError(f"RGB components must be 0.0-1.0, got {c}")
        color_rgb = list(rgb)
        color_type = "rgb"
        color = None
    else:
        if scheme not in COLOR_SCHEMES:
            raise ValueError(f"Unknown scheme: {scheme}. Available: {sorted(COLOR_SCHEMES.keys())}")
        color_rgb = None
        color_type = "scheme"

    entry = {
        "id": _next_id(project),
        "target": target,
        "color_type": color_type,
        "color_name": color,
        "color_rgb": color_rgb,
        "scheme": scheme,
    }

    project.setdefault("colors", []).append(entry)
    return entry


def remove_color(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Remove a color entry by index."""
    colors = project.get("colors", [])
    if not colors:
        raise ValueError("No color entries.")
    if index < 0 or index >= len(colors):
        raise IndexError(f"Color index {index} out of range (0-{len(colors)-1})")
    return colors.pop(index)


def list_colors(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List all color entries."""
    return [
        {
            "index": i,
            "id": c.get("id", i),
            "target": c.get("target", ""),
            "color_type": c.get("color_type", ""),
            "color_name": c.get("color_name"),
            "color_rgb": c.get("color_rgb"),
            "scheme": c.get("scheme"),
        }
        for i, c in enumerate(project.get("colors", []))
    ]


def get_color(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Get a color entry by index."""
    colors = project.get("colors", [])
    if index < 0 or index >= len(colors):
        raise IndexError(f"Color index {index} out of range (0-{len(colors)-1})")
    return colors[index]


def list_named_colors() -> List[Dict[str, Any]]:
    """List all named colors."""
    return [
        {"name": name, "rgb": rgb}
        for name, rgb in sorted(NAMED_COLORS.items())
    ]


def list_schemes() -> List[Dict[str, Any]]:
    """List all color schemes."""
    return [
        {"name": name, "description": info["description"]}
        for name, info in COLOR_SCHEMES.items()
    ]
