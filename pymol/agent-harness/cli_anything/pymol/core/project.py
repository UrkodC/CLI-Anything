"""PyMOL CLI - Project/session management module."""

import json
import os
from datetime import datetime
from typing import Optional, Dict, Any, List

# Session profiles (common setups)
PROFILES = {
    "default": {
        "width": 1920, "height": 1080,
        "bg_color": [0.0, 0.0, 0.0],
        "ray_trace_mode": 1, "antialias": 2,
    },
    "presentation": {
        "width": 1920, "height": 1080,
        "bg_color": [1.0, 1.0, 1.0],
        "ray_trace_mode": 1, "antialias": 2,
    },
    "publication": {
        "width": 3000, "height": 3000,
        "bg_color": [1.0, 1.0, 1.0],
        "ray_trace_mode": 1, "antialias": 2,
    },
    "poster": {
        "width": 4000, "height": 3000,
        "bg_color": [1.0, 1.0, 1.0],
        "ray_trace_mode": 1, "antialias": 2,
    },
    "web": {
        "width": 800, "height": 600,
        "bg_color": [1.0, 1.0, 1.0],
        "ray_trace_mode": 0, "antialias": 1,
    },
    "thumbnail": {
        "width": 400, "height": 400,
        "bg_color": [1.0, 1.0, 1.0],
        "ray_trace_mode": 0, "antialias": 1,
    },
    "transparent": {
        "width": 1920, "height": 1080,
        "bg_color": [0.0, 0.0, 0.0],
        "ray_trace_mode": 1, "antialias": 2,
    },
    "dark": {
        "width": 1920, "height": 1080,
        "bg_color": [0.0, 0.0, 0.0],
        "ray_trace_mode": 1, "antialias": 2,
    },
}

PROJECT_VERSION = "1.0"


def create_project(
    name: str = "untitled",
    profile: Optional[str] = None,
    width: int = 1920,
    height: int = 1080,
    bg_color: Optional[List[float]] = None,
    ray_trace_mode: int = 1,
    antialias: int = 2,
) -> Dict[str, Any]:
    """Create a new PyMOL session project."""
    if profile and profile in PROFILES:
        p = PROFILES[profile]
        width = p["width"]
        height = p["height"]
        bg_color = p["bg_color"]
        ray_trace_mode = p["ray_trace_mode"]
        antialias = p["antialias"]

    if bg_color is None:
        bg_color = [0.0, 0.0, 0.0]

    if width < 1 or height < 1:
        raise ValueError(f"Resolution must be positive: {width}x{height}")
    if len(bg_color) != 3:
        raise ValueError(f"Background color must have 3 components (RGB), got {len(bg_color)}")
    for c in bg_color:
        if not (0.0 <= c <= 1.0):
            raise ValueError(f"Color components must be 0.0-1.0, got {c}")
    if ray_trace_mode not in (0, 1, 2, 3):
        raise ValueError(f"Invalid ray_trace_mode: {ray_trace_mode}. Use 0, 1, 2, or 3.")
    if antialias not in (0, 1, 2):
        raise ValueError(f"Invalid antialias: {antialias}. Use 0, 1, or 2.")

    project = {
        "version": PROJECT_VERSION,
        "name": name,
        "settings": {
            "bg_color": bg_color,
            "depth_cue": True,
            "fog": False,
            "antialias": antialias,
            "ray_trace_mode": ray_trace_mode,
            "valence": True,
            "orthoscopic": True,
            "field_of_view": 20.0,
        },
        "structures": [],
        "selections": [],
        "representations": [],
        "colors": [],
        "labels": [],
        "view": {
            "position": [0.0, 0.0, -50.0],
            "orientation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "zoom": 1.0,
            "clip_near": 0.0,
            "clip_far": -10000.0,
        },
        "render": {
            "width": width,
            "height": height,
            "ray": True,
            "ray_trace_mode": ray_trace_mode,
            "ray_shadows": True,
            "antialias": antialias,
            "output_format": "png",
            "output_path": "./render/",
            "dpi": 300,
            "transparent_background": False,
        },
        "metadata": {
            "created": datetime.now().isoformat(),
            "modified": datetime.now().isoformat(),
            "software": "pymol-cli 1.0",
        },
    }
    return project


def open_project(path: str) -> Dict[str, Any]:
    """Open a .pymol-cli.json project file."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Project file not found: {path}")
    with open(path, "r") as f:
        project = json.load(f)
    if "version" not in project or "structures" not in project:
        raise ValueError(f"Invalid project file: {path}")
    return project


def save_project(project: Dict[str, Any], path: str) -> str:
    """Save project to a .pymol-cli.json file."""
    project["metadata"]["modified"] = datetime.now().isoformat()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(project, f, indent=2, default=str)
    return path


def get_project_info(project: Dict[str, Any]) -> Dict[str, Any]:
    """Get summary information about the project."""
    render = project.get("render", {})
    return {
        "name": project.get("name", "untitled"),
        "version": project.get("version", "unknown"),
        "settings": {
            "bg_color": project.get("settings", {}).get("bg_color", [0, 0, 0]),
            "orthoscopic": project.get("settings", {}).get("orthoscopic", True),
        },
        "render": {
            "resolution": f"{render.get('width', 1920)}x{render.get('height', 1080)}",
            "ray_trace_mode": render.get("ray_trace_mode", 1),
            "output_format": render.get("output_format", "png"),
            "dpi": render.get("dpi", 300),
        },
        "counts": {
            "structures": len(project.get("structures", [])),
            "selections": len(project.get("selections", [])),
            "representations": len(project.get("representations", [])),
            "colors": len(project.get("colors", [])),
            "labels": len(project.get("labels", [])),
        },
        "structures": [
            {
                "id": s.get("id", i),
                "name": s.get("name", f"Structure {i}"),
                "object_name": s.get("object_name", ""),
                "source": s.get("source", "unknown"),
                "visible": s.get("visible", True),
            }
            for i, s in enumerate(project.get("structures", []))
        ],
        "metadata": project.get("metadata", {}),
    }


def list_profiles() -> List[Dict[str, Any]]:
    """List all available session profiles."""
    result = []
    for name, p in PROFILES.items():
        result.append({
            "name": name,
            "resolution": f"{p['width']}x{p['height']}",
            "bg_color": p["bg_color"],
            "ray_trace_mode": p["ray_trace_mode"],
            "antialias": p["antialias"],
        })
    return result
