"""PyMOL CLI - Molecular structure management."""

from typing import Dict, Any, Optional, List
from datetime import datetime

# Supported file formats
SUPPORTED_FORMATS = {
    "pdb": {"extensions": [".pdb"], "description": "Protein Data Bank"},
    "cif": {"extensions": [".cif", ".mmcif"], "description": "Crystallographic Information File"},
    "sdf": {"extensions": [".sdf", ".mol"], "description": "Structure Data File"},
    "mol2": {"extensions": [".mol2"], "description": "Tripos Mol2"},
    "xyz": {"extensions": [".xyz"], "description": "XYZ coordinates"},
    "pdbqt": {"extensions": [".pdbqt"], "description": "PDBQT (AutoDock)"},
    "mae": {"extensions": [".mae", ".maegz"], "description": "Maestro"},
    "pse": {"extensions": [".pse"], "description": "PyMOL Session"},
}


def _next_id(project: Dict[str, Any]) -> int:
    structures = project.get("structures", [])
    if not structures:
        return 0
    return max(s.get("id", 0) for s in structures) + 1


def _unique_name(project: Dict[str, Any], name: str) -> str:
    existing = {s["object_name"] for s in project.get("structures", [])}
    if name not in existing:
        return name
    i = 1
    while f"{name}.{i:03d}" in existing:
        i += 1
    return f"{name}.{i:03d}"


def load_structure(
    project: Dict[str, Any],
    path: str,
    object_name: Optional[str] = None,
    source_format: Optional[str] = None,
    state: int = 1,
) -> Dict[str, Any]:
    """Load a molecular structure into the project.

    Args:
        project: Project dict.
        path: Path to the structure file, or a PDB ID (4 characters).
        object_name: Name for the loaded object. Defaults to filename without extension.
        source_format: File format override. Auto-detected from extension if not provided.
        state: State number for multi-model files (default 1).

    Returns:
        The loaded structure dict.
    """
    import os

    # Detect if path is a PDB ID (4 chars, starts with digit)
    is_pdb_id = len(path) == 4 and path[0].isdigit()

    if is_pdb_id:
        fmt = "pdb"
        base_name = path.lower()
        actual_path = None  # Will be fetched by PyMOL
    else:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Structure file not found: {path}")
        _, ext = os.path.splitext(path)
        ext = ext.lower()

        # Auto-detect format
        if source_format:
            fmt = source_format
        else:
            fmt = None
            for format_name, info in SUPPORTED_FORMATS.items():
                if ext in info["extensions"]:
                    fmt = format_name
                    break
            if fmt is None:
                raise ValueError(f"Unsupported file format: {ext}. Supported: {list(SUPPORTED_FORMATS.keys())}")

        base_name = os.path.splitext(os.path.basename(path))[0]
        actual_path = os.path.abspath(path)

    if object_name is None:
        object_name = base_name

    object_name = _unique_name(project, object_name)

    if state < 1:
        raise ValueError(f"State must be >= 1, got {state}")

    structure = {
        "id": _next_id(project),
        "name": base_name,
        "object_name": object_name,
        "source": fmt,
        "path": actual_path,
        "pdb_id": path.upper() if is_pdb_id else None,
        "state": state,
        "visible": True,
        "chains": [],
        "residue_count": 0,
        "atom_count": 0,
    }

    project.setdefault("structures", []).append(structure)
    return structure


def remove_structure(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Remove a structure by index."""
    structures = project.get("structures", [])
    if not structures:
        raise ValueError("No structures loaded.")
    if index < 0 or index >= len(structures):
        raise IndexError(f"Structure index {index} out of range (0-{len(structures)-1})")

    removed = structures.pop(index)

    # Also remove associated representations, colors, and labels
    obj_name = removed["object_name"]
    project["representations"] = [
        r for r in project.get("representations", []) if r.get("target") != obj_name
    ]
    project["colors"] = [
        c for c in project.get("colors", []) if c.get("target") != obj_name
    ]

    return removed


def rename_structure(project: Dict[str, Any], index: int, new_name: str) -> Dict[str, Any]:
    """Rename a structure's object name."""
    structures = project.get("structures", [])
    if index < 0 or index >= len(structures):
        raise IndexError(f"Structure index {index} out of range (0-{len(structures)-1})")

    old_name = structures[index]["object_name"]
    new_name = _unique_name(project, new_name)
    structures[index]["object_name"] = new_name

    # Update references in representations, colors, labels
    for rep in project.get("representations", []):
        if rep.get("target") == old_name:
            rep["target"] = new_name
    for col in project.get("colors", []):
        if col.get("target") == old_name:
            col["target"] = new_name

    return structures[index]


def get_structure(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Get detailed info about a structure."""
    structures = project.get("structures", [])
    if index < 0 or index >= len(structures):
        raise IndexError(f"Structure index {index} out of range (0-{len(structures)-1})")
    return structures[index]


def list_structures(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List all structures."""
    return [
        {
            "index": i,
            "id": s.get("id", i),
            "name": s.get("name", ""),
            "object_name": s.get("object_name", ""),
            "source": s.get("source", ""),
            "visible": s.get("visible", True),
            "chains": s.get("chains", []),
        }
        for i, s in enumerate(project.get("structures", []))
    ]


def set_structure_property(project: Dict[str, Any], index: int, prop: str, value) -> None:
    """Set a property on a structure."""
    structures = project.get("structures", [])
    if index < 0 or index >= len(structures):
        raise IndexError(f"Structure index {index} out of range (0-{len(structures)-1})")

    valid_props = {"visible", "object_name", "chains", "residue_count", "atom_count"}
    if prop not in valid_props:
        raise ValueError(f"Unknown property: {prop}. Valid: {sorted(valid_props)}")

    if prop == "visible":
        value = str(value).lower() in ("true", "1", "yes") if isinstance(value, str) else bool(value)

    structures[index][prop] = value


def list_formats() -> List[Dict[str, Any]]:
    """List supported file formats."""
    return [
        {"format": name, "extensions": info["extensions"], "description": info["description"]}
        for name, info in SUPPORTED_FORMATS.items()
    ]
