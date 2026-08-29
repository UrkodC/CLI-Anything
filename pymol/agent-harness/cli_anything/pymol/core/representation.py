"""PyMOL CLI - Representation management."""

from typing import Dict, Any, Optional, List


# Representation registry with settings
REPRESENTATION_REGISTRY = {
    "cartoon": {
        "description": "Cartoon backbone trace (helices, sheets, loops)",
        "category": "backbone",
        "settings": {
            "cartoon_oval_length": {"type": "float", "default": 1.2, "min": 0.1, "max": 5.0},
            "cartoon_oval_width": {"type": "float", "default": 0.25, "min": 0.01, "max": 2.0},
            "cartoon_loop_radius": {"type": "float", "default": 0.2, "min": 0.01, "max": 2.0},
            "cartoon_rect_length": {"type": "float", "default": 1.4, "min": 0.1, "max": 5.0},
            "cartoon_rect_width": {"type": "float", "default": 0.4, "min": 0.01, "max": 2.0},
            "cartoon_transparency": {"type": "float", "default": 0.0, "min": 0.0, "max": 1.0},
        },
    },
    "sticks": {
        "description": "Ball-and-stick bonds",
        "category": "atomic",
        "settings": {
            "stick_radius": {"type": "float", "default": 0.2, "min": 0.01, "max": 2.0},
            "stick_transparency": {"type": "float", "default": 0.0, "min": 0.0, "max": 1.0},
        },
    },
    "spheres": {
        "description": "Space-filling van der Waals spheres",
        "category": "atomic",
        "settings": {
            "sphere_scale": {"type": "float", "default": 1.0, "min": 0.01, "max": 5.0},
            "sphere_transparency": {"type": "float", "default": 0.0, "min": 0.0, "max": 1.0},
        },
    },
    "surface": {
        "description": "Molecular surface (Connolly/SES)",
        "category": "surface",
        "settings": {
            "surface_type": {"type": "int", "default": 0, "min": 0, "max": 3},
            "surface_quality": {"type": "int", "default": 0, "min": -1, "max": 4},
            "transparency": {"type": "float", "default": 0.0, "min": 0.0, "max": 1.0},
            "surface_solvent": {"type": "float", "default": 1.4, "min": 0.1, "max": 5.0},
        },
    },
    "mesh": {
        "description": "Mesh surface",
        "category": "surface",
        "settings": {
            "mesh_width": {"type": "float", "default": 0.5, "min": 0.1, "max": 5.0},
            "mesh_quality": {"type": "int", "default": 2, "min": 0, "max": 4},
        },
    },
    "lines": {
        "description": "Wire-frame bonds",
        "category": "atomic",
        "settings": {
            "line_width": {"type": "float", "default": 1.5, "min": 0.1, "max": 10.0},
        },
    },
    "ribbon": {
        "description": "Ribbon backbone trace",
        "category": "backbone",
        "settings": {
            "ribbon_width": {"type": "float", "default": 3.0, "min": 0.1, "max": 10.0},
        },
    },
    "dots": {
        "description": "Dot surface",
        "category": "surface",
        "settings": {
            "dot_width": {"type": "float", "default": 2.0, "min": 0.1, "max": 10.0},
            "dot_density": {"type": "int", "default": 2, "min": 1, "max": 4},
        },
    },
    "nb_spheres": {
        "description": "Non-bonded spheres (for ions, water)",
        "category": "atomic",
        "settings": {
            "nb_spheres_size": {"type": "float", "default": 0.25, "min": 0.01, "max": 2.0},
        },
    },
}


def _next_id(project: Dict[str, Any]) -> int:
    reps = project.get("representations", [])
    if not reps:
        return 0
    return max(r.get("id", 0) for r in reps) + 1


def show_representation(
    project: Dict[str, Any],
    target: str,
    rep_type: str,
    settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Show a representation on a target (structure or selection).

    Args:
        project: Project dict.
        target: Object name or selection name to apply representation to.
        rep_type: Representation type (cartoon, sticks, surface, etc.)
        settings: Optional settings dict for the representation.

    Returns:
        The created/updated representation dict.
    """
    if rep_type not in REPRESENTATION_REGISTRY:
        raise ValueError(
            f"Unknown representation: {rep_type}. "
            f"Available: {sorted(REPRESENTATION_REGISTRY.keys())}"
        )

    # Validate settings
    validated = {}
    if settings:
        reg_settings = REPRESENTATION_REGISTRY[rep_type]["settings"]
        for key, value in settings.items():
            if key not in reg_settings:
                raise ValueError(
                    f"Unknown setting '{key}' for {rep_type}. "
                    f"Valid: {sorted(reg_settings.keys())}"
                )
            spec = reg_settings[key]
            if spec["type"] == "float":
                value = float(value)
            elif spec["type"] == "int":
                value = int(value)
            if value < spec["min"] or value > spec["max"]:
                raise ValueError(
                    f"Setting '{key}' value {value} out of range "
                    f"({spec['min']}-{spec['max']})"
                )
            validated[key] = value

    # Check if this rep already exists for this target
    for rep in project.get("representations", []):
        if rep["target"] == target and rep["rep_type"] == rep_type:
            rep["enabled"] = True
            if validated:
                rep["settings"].update(validated)
            return rep

    # Create new representation
    rep = {
        "id": _next_id(project),
        "target": target,
        "rep_type": rep_type,
        "enabled": True,
        "settings": validated,
    }
    project.setdefault("representations", []).append(rep)
    return rep


def hide_representation(
    project: Dict[str, Any],
    target: str,
    rep_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Hide a representation on a target.

    If rep_type is None, hides all representations on the target.

    Returns:
        List of hidden representations.
    """
    hidden = []
    for rep in project.get("representations", []):
        if rep["target"] == target:
            if rep_type is None or rep["rep_type"] == rep_type:
                rep["enabled"] = False
                hidden.append(rep)

    if not hidden:
        target_str = f"{rep_type} on " if rep_type else ""
        raise ValueError(f"No {target_str}representation found for target: {target}")

    return hidden


def remove_representation(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Remove a representation by index."""
    reps = project.get("representations", [])
    if not reps:
        raise ValueError("No representations defined.")
    if index < 0 or index >= len(reps):
        raise IndexError(f"Representation index {index} out of range (0-{len(reps)-1})")
    return reps.pop(index)


def set_representation_setting(
    project: Dict[str, Any],
    index: int,
    setting: str,
    value,
) -> Dict[str, Any]:
    """Set a setting on a representation."""
    reps = project.get("representations", [])
    if index < 0 or index >= len(reps):
        raise IndexError(f"Representation index {index} out of range (0-{len(reps)-1})")

    rep = reps[index]
    rep_type = rep["rep_type"]
    reg_settings = REPRESENTATION_REGISTRY[rep_type]["settings"]

    if setting not in reg_settings:
        raise ValueError(
            f"Unknown setting '{setting}' for {rep_type}. "
            f"Valid: {sorted(reg_settings.keys())}"
        )

    spec = reg_settings[setting]
    if spec["type"] == "float":
        value = float(value)
    elif spec["type"] == "int":
        value = int(value)

    if value < spec["min"] or value > spec["max"]:
        raise ValueError(
            f"Setting '{setting}' value {value} out of range ({spec['min']}-{spec['max']})"
        )

    rep["settings"][setting] = value
    return rep


def get_representation(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Get a representation by index."""
    reps = project.get("representations", [])
    if index < 0 or index >= len(reps):
        raise IndexError(f"Representation index {index} out of range (0-{len(reps)-1})")
    return reps[index]


def list_representations(project: Dict[str, Any], target: Optional[str] = None) -> List[Dict[str, Any]]:
    """List representations, optionally filtered by target."""
    result = []
    for i, rep in enumerate(project.get("representations", [])):
        if target and rep.get("target") != target:
            continue
        result.append({
            "index": i,
            "id": rep.get("id", i),
            "target": rep.get("target", ""),
            "rep_type": rep.get("rep_type", ""),
            "enabled": rep.get("enabled", True),
            "settings": rep.get("settings", {}),
        })
    return result


def list_available(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """List available representation types."""
    result = []
    for name, spec in REPRESENTATION_REGISTRY.items():
        if category and spec["category"] != category:
            continue
        result.append({
            "name": name,
            "description": spec["description"],
            "category": spec["category"],
            "settings": {k: {"default": v["default"], "range": f"{v['min']}-{v['max']}"}
                        for k, v in spec["settings"].items()},
        })
    return result


def get_representation_info(name: str) -> Dict[str, Any]:
    """Get detailed info about a representation type."""
    if name not in REPRESENTATION_REGISTRY:
        raise ValueError(f"Unknown representation: {name}. Available: {sorted(REPRESENTATION_REGISTRY.keys())}")
    spec = REPRESENTATION_REGISTRY[name]
    return {
        "name": name,
        "description": spec["description"],
        "category": spec["category"],
        "settings": spec["settings"],
    }
