"""PyMOL CLI - Label management."""

from typing import Dict, Any, Optional, List


# Label format presets
LABEL_FORMATS = {
    "residue": {
        "expression": '"%s-%s" % (resn, resi)',
        "description": "Residue name and number (e.g., ALA-42)",
    },
    "atom": {
        "expression": "name",
        "description": "Atom name (e.g., CA, CB, N)",
    },
    "chain_residue": {
        "expression": '"%s/%s%s" % (chain, resn, resi)',
        "description": "Chain/Residue (e.g., A/ALA42)",
    },
    "element": {
        "expression": "elem",
        "description": "Element symbol (e.g., C, N, O)",
    },
    "bfactor": {
        "expression": '"%4.1f" % b',
        "description": "B-factor value",
    },
    "charge": {
        "expression": '"%+.2f" % partial_charge',
        "description": "Partial charge",
    },
    "residue_number": {
        "expression": "resi",
        "description": "Residue number only",
    },
    "one_letter": {
        "expression": "oneletter",
        "description": "One-letter amino acid code",
    },
}


def _next_id(project: Dict[str, Any]) -> int:
    labels = project.get("labels", [])
    if not labels:
        return 0
    return max(l.get("id", 0) for l in labels) + 1


def add_label(
    project: Dict[str, Any],
    target: str,
    format_preset: Optional[str] = None,
    expression: Optional[str] = None,
    color: Optional[List[float]] = None,
    size: int = 14,
) -> Dict[str, Any]:
    """Add labels to atoms/residues.

    Args:
        project: Project dict.
        target: PyMOL selection expression for atoms to label.
        format_preset: Label format preset name.
        expression: Custom PyMOL label expression (overrides format_preset).
        color: Label color [r, g, b] each 0.0-1.0. Default white.
        size: Label font size (8-72).

    Returns:
        The created label dict.
    """
    if format_preset is None and expression is None:
        format_preset = "residue"

    if expression is None:
        if format_preset not in LABEL_FORMATS:
            raise ValueError(
                f"Unknown label format: {format_preset}. "
                f"Available: {sorted(LABEL_FORMATS.keys())}"
            )
        expression = LABEL_FORMATS[format_preset]["expression"]

    if color is not None:
        if len(color) != 3:
            raise ValueError(f"Color must have 3 components (RGB), got {len(color)}")
        for c in color:
            if not (0.0 <= c <= 1.0):
                raise ValueError(f"Color components must be 0.0-1.0, got {c}")
    else:
        color = [1.0, 1.0, 1.0]

    if not (8 <= size <= 72):
        raise ValueError(f"Label size must be 8-72, got {size}")

    label = {
        "id": _next_id(project),
        "target": target,
        "format_preset": format_preset,
        "expression": expression,
        "color": color,
        "size": size,
    }

    project.setdefault("labels", []).append(label)
    return label


def remove_label(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Remove a label by index."""
    labels = project.get("labels", [])
    if not labels:
        raise ValueError("No labels defined.")
    if index < 0 or index >= len(labels):
        raise IndexError(f"Label index {index} out of range (0-{len(labels)-1})")
    return labels.pop(index)


def clear_labels(project: Dict[str, Any], target: Optional[str] = None) -> int:
    """Clear labels, optionally filtered by target.

    Returns:
        Number of labels removed.
    """
    labels = project.get("labels", [])
    if target is None:
        count = len(labels)
        labels.clear()
        return count

    original = len(labels)
    project["labels"] = [l for l in labels if l.get("target") != target]
    return original - len(project["labels"])


def get_label(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Get a label by index."""
    labels = project.get("labels", [])
    if index < 0 or index >= len(labels):
        raise IndexError(f"Label index {index} out of range (0-{len(labels)-1})")
    return labels[index]


def list_labels(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List all labels."""
    return [
        {
            "index": i,
            "id": l.get("id", i),
            "target": l.get("target", ""),
            "expression": l.get("expression", ""),
            "format_preset": l.get("format_preset"),
            "color": l.get("color", [1, 1, 1]),
            "size": l.get("size", 14),
        }
        for i, l in enumerate(project.get("labels", []))
    ]


def list_label_formats() -> List[Dict[str, Any]]:
    """List available label format presets."""
    return [
        {"name": name, "expression": info["expression"], "description": info["description"]}
        for name, info in LABEL_FORMATS.items()
    ]
