"""PyMOL CLI - View/camera management."""

from typing import Dict, Any, Optional, List


# View presets
VIEW_PRESETS = {
    "front": {
        "orientation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "description": "Front view (+Z towards viewer)",
    },
    "back": {
        "orientation": [[-1, 0, 0], [0, 1, 0], [0, 0, -1]],
        "description": "Back view (-Z towards viewer)",
    },
    "top": {
        "orientation": [[1, 0, 0], [0, 0, -1], [0, 1, 0]],
        "description": "Top view (+Y towards viewer)",
    },
    "bottom": {
        "orientation": [[1, 0, 0], [0, 0, 1], [0, -1, 0]],
        "description": "Bottom view (-Y towards viewer)",
    },
    "left": {
        "orientation": [[0, 0, -1], [0, 1, 0], [1, 0, 0]],
        "description": "Left view (-X towards viewer)",
    },
    "right": {
        "orientation": [[0, 0, 1], [0, 1, 0], [-1, 0, 0]],
        "description": "Right view (+X towards viewer)",
    },
}


def set_view(
    project: Dict[str, Any],
    preset: Optional[str] = None,
    position: Optional[List[float]] = None,
    orientation: Optional[List[List[float]]] = None,
    zoom: Optional[float] = None,
    clip_near: Optional[float] = None,
    clip_far: Optional[float] = None,
    field_of_view: Optional[float] = None,
) -> Dict[str, Any]:
    """Set the view/camera parameters.

    Args:
        project: Project dict.
        preset: View preset name (front, back, top, bottom, left, right).
        position: Camera position [x, y, z].
        orientation: 3x3 rotation matrix as list of 3 lists.
        zoom: Zoom level (positive float).
        clip_near: Near clipping plane distance.
        clip_far: Far clipping plane distance.
        field_of_view: Field of view in degrees (1.0-179.0).
    """
    view = project.get("view", {})
    old_view = dict(view)

    if preset is not None:
        if preset not in VIEW_PRESETS:
            raise ValueError(f"Unknown view preset: {preset}. Available: {sorted(VIEW_PRESETS.keys())}")
        view["orientation"] = VIEW_PRESETS[preset]["orientation"]

    if position is not None:
        if len(position) != 3:
            raise ValueError(f"Position must have 3 components, got {len(position)}")
        view["position"] = [float(x) for x in position]

    if orientation is not None:
        if len(orientation) != 3 or any(len(row) != 3 for row in orientation):
            raise ValueError("Orientation must be a 3x3 matrix")
        view["orientation"] = [[float(x) for x in row] for row in orientation]

    if zoom is not None:
        if zoom <= 0:
            raise ValueError(f"Zoom must be positive, got {zoom}")
        view["zoom"] = float(zoom)

    if clip_near is not None:
        view["clip_near"] = float(clip_near)

    if clip_far is not None:
        view["clip_far"] = float(clip_far)

    if field_of_view is not None:
        if not (1.0 <= field_of_view <= 179.0):
            raise ValueError(f"Field of view must be 1.0-179.0, got {field_of_view}")
        view["field_of_view"] = float(field_of_view)

    project["view"] = view
    return {"old_view": old_view, "new_view": view}


def get_view(project: Dict[str, Any]) -> Dict[str, Any]:
    """Get current view settings."""
    return project.get("view", {
        "position": [0.0, 0.0, -50.0],
        "orientation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "zoom": 1.0,
        "clip_near": 0.0,
        "clip_far": -10000.0,
    })


def set_setting(project: Dict[str, Any], setting: str, value) -> Dict[str, Any]:
    """Set a global PyMOL setting.

    Args:
        project: Project dict.
        setting: Setting name.
        value: Setting value.

    Returns:
        Dict with old and new values.
    """
    settings = project.get("settings", {})

    valid_settings = {
        "bg_color": {"type": "list", "length": 3},
        "depth_cue": {"type": "bool"},
        "fog": {"type": "bool"},
        "antialias": {"type": "int", "min": 0, "max": 2},
        "ray_trace_mode": {"type": "int", "min": 0, "max": 3},
        "valence": {"type": "bool"},
        "orthoscopic": {"type": "bool"},
        "field_of_view": {"type": "float", "min": 1.0, "max": 179.0},
    }

    if setting not in valid_settings:
        raise ValueError(f"Unknown setting: {setting}. Valid: {sorted(valid_settings.keys())}")

    spec = valid_settings[setting]
    old_value = settings.get(setting)

    if spec["type"] == "bool":
        if isinstance(value, str):
            value = value.lower() in ("true", "1", "yes", "on")
        value = bool(value)
    elif spec["type"] == "int":
        value = int(value)
        if value < spec["min"] or value > spec["max"]:
            raise ValueError(f"Setting '{setting}' must be {spec['min']}-{spec['max']}, got {value}")
    elif spec["type"] == "float":
        value = float(value)
        if value < spec["min"] or value > spec["max"]:
            raise ValueError(f"Setting '{setting}' must be {spec['min']}-{spec['max']}, got {value}")
    elif spec["type"] == "list":
        if not isinstance(value, list) or len(value) != spec["length"]:
            raise ValueError(f"Setting '{setting}' must be a list of {spec['length']} values")
        for v in value:
            if not (0.0 <= float(v) <= 1.0):
                raise ValueError(f"Color components must be 0.0-1.0, got {v}")
        value = [float(v) for v in value]

    settings[setting] = value
    project["settings"] = settings
    return {"setting": setting, "old_value": old_value, "new_value": value}


def list_settings(project: Dict[str, Any]) -> Dict[str, Any]:
    """List all current settings."""
    return project.get("settings", {})


def list_view_presets() -> List[Dict[str, Any]]:
    """List available view presets."""
    return [
        {"name": name, "description": info["description"]}
        for name, info in VIEW_PRESETS.items()
    ]
