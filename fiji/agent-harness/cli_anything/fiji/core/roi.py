"""Fiji CLI - ROI (Region of Interest) management.

Manages ROIs for measurement, cropping, and analysis.
ROIs are stored in the project and can be exported as
ImageJ ROI sets for use in the Fiji GUI.
"""

from datetime import datetime
from typing import Dict, Any, List, Optional


ROI_TYPES = {
    "rectangle": {"params": ["x", "y", "width", "height"]},
    "oval": {"params": ["x", "y", "width", "height"]},
    "line": {"params": ["x1", "y1", "x2", "y2"]},
    "polygon": {"params": ["points"]},  # points is list of [x,y]
    "freehand": {"params": ["points"]},
    "point": {"params": ["x", "y"]},
    "multi_point": {"params": ["points"]},
}


def add_roi(
    project: Dict[str, Any],
    roi_type: str,
    name: Optional[str] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Add a ROI to the project.

    Args:
        project: The project dict.
        roi_type: Type of ROI (rectangle, oval, line, polygon, point, etc.).
        name: ROI name (auto-generated if not provided).
        **kwargs: ROI-specific parameters.
    """
    if roi_type not in ROI_TYPES:
        available = ", ".join(sorted(ROI_TYPES.keys()))
        raise ValueError(f"Unknown ROI type: {roi_type}. Available: {available}")

    required = ROI_TYPES[roi_type]["params"]
    for param in required:
        if param not in kwargs:
            raise ValueError(f"Missing required parameter '{param}' for {roi_type} ROI")

    if "rois" not in project:
        project["rois"] = []

    idx = len(project["rois"])
    if name is None:
        name = f"{roi_type}_{idx}"

    roi = {
        "id": idx,
        "name": name,
        "type": roi_type,
        "params": {k: v for k, v in kwargs.items()},
        "created": datetime.now().isoformat(),
    }
    project["rois"].append(roi)
    return roi


def remove_roi(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Remove a ROI by index."""
    rois = project.get("rois", [])
    if index < 0 or index >= len(rois):
        raise IndexError(f"ROI index {index} out of range (0-{len(rois) - 1})")
    removed = rois.pop(index)
    for i, r in enumerate(rois):
        r["id"] = i
    return removed


def list_rois(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List all ROIs."""
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "type": r["type"],
            "params": r["params"],
        }
        for r in project.get("rois", [])
    ]


def get_roi(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Get a specific ROI."""
    rois = project.get("rois", [])
    if index < 0 or index >= len(rois):
        raise IndexError(f"ROI index {index} out of range (0-{len(rois) - 1})")
    return rois[index]


def build_roi_macro(roi: Dict[str, Any]) -> str:
    """Build an ImageJ macro command to create this ROI.

    Returns a macro string that creates the ROI in ImageJ.
    """
    roi_type = roi["type"]
    p = roi["params"]

    if roi_type == "rectangle":
        return f'makeRectangle({p["x"]}, {p["y"]}, {p["width"]}, {p["height"]});'
    elif roi_type == "oval":
        return f'makeOval({p["x"]}, {p["y"]}, {p["width"]}, {p["height"]});'
    elif roi_type == "line":
        return f'makeLine({p["x1"]}, {p["y1"]}, {p["x2"]}, {p["y2"]});'
    elif roi_type == "point":
        return f'makePoint({p["x"]}, {p["y"]});'
    elif roi_type in ("polygon", "freehand", "multi_point"):
        points = p["points"]
        x_arr = ", ".join(str(pt[0]) for pt in points)
        y_arr = ", ".join(str(pt[1]) for pt in points)
        if roi_type == "polygon":
            return f'makeSelection("polygon", newArray({x_arr}), newArray({y_arr}));'
        elif roi_type == "freehand":
            return f'makeSelection("freehand", newArray({x_arr}), newArray({y_arr}));'
        else:
            return f'makeSelection("point", newArray({x_arr}), newArray({y_arr}));'
    else:
        raise ValueError(f"Cannot build macro for ROI type: {roi_type}")
