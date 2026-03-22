"""PyMOL CLI - Render settings and output."""

import os
from typing import Dict, Any, Optional, List

# Render presets
RENDER_PRESETS = {
    "quick_preview": {
        "width": 800, "height": 600, "ray": False, "antialias": 0,
        "description": "Fast preview, no ray tracing",
    },
    "standard": {
        "width": 1920, "height": 1080, "ray": True, "ray_trace_mode": 1, "antialias": 2,
        "description": "Standard quality ray-traced render",
    },
    "high_quality": {
        "width": 3000, "height": 3000, "ray": True, "ray_trace_mode": 1, "antialias": 2,
        "description": "High quality for publications",
    },
    "poster": {
        "width": 4000, "height": 3000, "ray": True, "ray_trace_mode": 1, "antialias": 2,
        "description": "Large format for posters",
    },
    "web_thumbnail": {
        "width": 400, "height": 400, "ray": False, "antialias": 1,
        "description": "Small web thumbnail",
    },
    "presentation": {
        "width": 1920, "height": 1080, "ray": True, "ray_trace_mode": 1, "antialias": 2,
        "description": "Presentation quality (16:9)",
    },
    "transparent": {
        "width": 1920, "height": 1080, "ray": True, "ray_trace_mode": 1, "antialias": 2,
        "transparent_background": True,
        "description": "Ray-traced with transparent background",
    },
}

VALID_FORMATS = {"png", "jpg", "jpeg", "bmp", "tiff", "ppm"}


def set_render_settings(
    project: Dict[str, Any],
    width: Optional[int] = None,
    height: Optional[int] = None,
    ray: Optional[bool] = None,
    ray_trace_mode: Optional[int] = None,
    ray_shadows: Optional[bool] = None,
    antialias: Optional[int] = None,
    output_format: Optional[str] = None,
    output_path: Optional[str] = None,
    dpi: Optional[int] = None,
    transparent_background: Optional[bool] = None,
    preset: Optional[str] = None,
) -> Dict[str, Any]:
    """Configure render settings."""
    render = project.get("render", {})

    if preset is not None:
        if preset not in RENDER_PRESETS:
            raise ValueError(f"Unknown render preset: {preset}. Available: {sorted(RENDER_PRESETS.keys())}")
        p = RENDER_PRESETS[preset]
        render["width"] = p["width"]
        render["height"] = p["height"]
        render["ray"] = p["ray"]
        if "ray_trace_mode" in p:
            render["ray_trace_mode"] = p["ray_trace_mode"]
        if "antialias" in p:
            render["antialias"] = p["antialias"]
        if "transparent_background" in p:
            render["transparent_background"] = p["transparent_background"]

    if width is not None:
        if width < 1:
            raise ValueError(f"Width must be positive, got {width}")
        render["width"] = width
    if height is not None:
        if height < 1:
            raise ValueError(f"Height must be positive, got {height}")
        render["height"] = height
    if ray is not None:
        render["ray"] = bool(ray)
    if ray_trace_mode is not None:
        if ray_trace_mode not in (0, 1, 2, 3):
            raise ValueError(f"Invalid ray_trace_mode: {ray_trace_mode}. Use 0, 1, 2, or 3.")
        render["ray_trace_mode"] = ray_trace_mode
    if ray_shadows is not None:
        render["ray_shadows"] = bool(ray_shadows)
    if antialias is not None:
        if antialias not in (0, 1, 2):
            raise ValueError(f"Invalid antialias: {antialias}. Use 0, 1, or 2.")
        render["antialias"] = antialias
    if output_format is not None:
        fmt = output_format.lower()
        if fmt not in VALID_FORMATS:
            raise ValueError(f"Invalid format: {fmt}. Valid: {sorted(VALID_FORMATS)}")
        render["output_format"] = fmt
    if output_path is not None:
        render["output_path"] = output_path
    if dpi is not None:
        if dpi < 1:
            raise ValueError(f"DPI must be positive, got {dpi}")
        render["dpi"] = dpi
    if transparent_background is not None:
        render["transparent_background"] = bool(transparent_background)

    project["render"] = render
    return render


def get_render_settings(project: Dict[str, Any]) -> Dict[str, Any]:
    """Get current render settings."""
    render = project.get("render", {})
    return {
        "resolution": f"{render.get('width', 1920)}x{render.get('height', 1080)}",
        "width": render.get("width", 1920),
        "height": render.get("height", 1080),
        "ray": render.get("ray", True),
        "ray_trace_mode": render.get("ray_trace_mode", 1),
        "ray_shadows": render.get("ray_shadows", True),
        "antialias": render.get("antialias", 2),
        "output_format": render.get("output_format", "png"),
        "dpi": render.get("dpi", 300),
        "transparent_background": render.get("transparent_background", False),
    }


def list_render_presets() -> List[Dict[str, Any]]:
    """List available render presets."""
    return [
        {
            "name": name,
            "description": p["description"],
            "resolution": f"{p['width']}x{p['height']}",
            "ray": p["ray"],
        }
        for name, p in RENDER_PRESETS.items()
    ]


def render_scene(
    project: Dict[str, Any],
    output_path: str,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Generate a PyMOL script (.pml) for rendering.

    Args:
        project: Project dict.
        output_path: Path for the rendered image.
        overwrite: Whether to overwrite existing files.

    Returns:
        Dict with script_path, command, and render info.
    """
    if os.path.exists(output_path) and not overwrite:
        raise FileExistsError(f"Output file already exists: {output_path}. Use --overwrite to replace.")

    # Import here to avoid circular imports
    from cli_anything.pymol.utils.pml_gen import generate_full_script

    render = project.get("render", {})
    script = generate_full_script(project, output_path)

    # Write the script
    script_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(script_dir, exist_ok=True)
    script_path = os.path.join(script_dir, "_pymol_render_script.pml")
    with open(script_path, "w") as f:
        f.write(script)

    return {
        "script_path": script_path,
        "output_path": output_path,
        "command": f"pymol -cq {script_path}",
        "width": render.get("width", 1920),
        "height": render.get("height", 1080),
        "ray": render.get("ray", True),
        "format": render.get("output_format", "png"),
    }
