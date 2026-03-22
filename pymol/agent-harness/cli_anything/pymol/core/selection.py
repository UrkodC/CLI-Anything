"""PyMOL CLI - Named selection management."""

from typing import Dict, Any, Optional, List


def _next_id(project: Dict[str, Any]) -> int:
    selections = project.get("selections", [])
    if not selections:
        return 0
    return max(s.get("id", 0) for s in selections) + 1


def _unique_name(project: Dict[str, Any], name: str) -> str:
    existing = {s["name"] for s in project.get("selections", [])}
    if name not in existing:
        return name
    i = 1
    while f"{name}_{i}" in existing:
        i += 1
    return f"{name}_{i}"


# Common selection macros for convenience
SELECTION_MACROS = {
    "protein": "polymer.protein",
    "nucleic": "polymer.nucleic",
    "water": "resn HOH+WAT",
    "ions": "resn NA+CL+MG+CA+ZN+FE+MN+K+CU",
    "ligand": "organic",
    "metals": "metals",
    "backbone": "name CA+C+N+O",
    "sidechain": "not name CA+C+N+O+H and polymer.protein",
    "helix": "ss H",
    "sheet": "ss S",
    "loop": "ss L+''",
    "polar": "resn SER+THR+ASN+GLN+TYR+CYS",
    "hydrophobic": "resn ALA+VAL+LEU+ILE+PRO+PHE+TRP+MET",
    "charged": "resn ASP+GLU+LYS+ARG+HIS",
    "aromatic": "resn PHE+TYR+TRP+HIS",
}


def create_selection(
    project: Dict[str, Any],
    name: str,
    expression: str,
    enabled: bool = True,
) -> Dict[str, Any]:
    """Create a named selection.

    Args:
        project: Project dict.
        name: Selection name.
        expression: PyMOL selection expression (e.g., "chain A and resi 100-200").
        enabled: Whether the selection is enabled.

    Returns:
        The created selection dict.
    """
    if not name:
        raise ValueError("Selection name cannot be empty.")
    if not expression:
        raise ValueError("Selection expression cannot be empty.")

    name = _unique_name(project, name)

    selection = {
        "id": _next_id(project),
        "name": name,
        "expression": expression,
        "enabled": enabled,
    }

    project.setdefault("selections", []).append(selection)
    return selection


def remove_selection(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Remove a selection by index."""
    selections = project.get("selections", [])
    if not selections:
        raise ValueError("No selections defined.")
    if index < 0 or index >= len(selections):
        raise IndexError(f"Selection index {index} out of range (0-{len(selections)-1})")
    return selections.pop(index)


def update_selection(project: Dict[str, Any], index: int, expression: Optional[str] = None, enabled: Optional[bool] = None) -> Dict[str, Any]:
    """Update a selection's expression or enabled state."""
    selections = project.get("selections", [])
    if index < 0 or index >= len(selections):
        raise IndexError(f"Selection index {index} out of range (0-{len(selections)-1})")

    if expression is not None:
        if not expression:
            raise ValueError("Selection expression cannot be empty.")
        selections[index]["expression"] = expression
    if enabled is not None:
        selections[index]["enabled"] = enabled

    return selections[index]


def get_selection(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Get a selection by index."""
    selections = project.get("selections", [])
    if index < 0 or index >= len(selections):
        raise IndexError(f"Selection index {index} out of range (0-{len(selections)-1})")
    return selections[index]


def list_selections(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List all selections."""
    return [
        {
            "index": i,
            "id": s.get("id", i),
            "name": s.get("name", ""),
            "expression": s.get("expression", ""),
            "enabled": s.get("enabled", True),
        }
        for i, s in enumerate(project.get("selections", []))
    ]


def list_macros() -> List[Dict[str, str]]:
    """List available selection macros."""
    return [
        {"name": name, "expression": expr}
        for name, expr in SELECTION_MACROS.items()
    ]
