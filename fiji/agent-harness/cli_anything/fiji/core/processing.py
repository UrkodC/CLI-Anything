"""Fiji CLI - Image processing operations.

Each processing operation is recorded in the project's
processing_log. The actual processing is performed by Fiji
via the backend module.
"""

from datetime import datetime
from typing import Dict, Any, List, Optional


# Available processing operations and their ImageJ macro equivalents
OPERATIONS = {
    # Basic adjustments
    "brightness_contrast": {
        "category": "adjust",
        "description": "Adjust brightness and contrast",
        "macro": 'run("Brightness/Contrast...", "brightness={brightness} contrast={contrast}");',
        "params": {"brightness": 0, "contrast": 0},
    },
    "threshold": {
        "category": "adjust",
        "description": "Apply threshold to create binary image",
        "macro": 'setThreshold({lower}, {upper}); run("Convert to Mask");',
        "params": {"lower": 0, "upper": 255, "method": "Default"},
    },
    "auto_threshold": {
        "category": "adjust",
        "description": "Auto-threshold using selected method",
        "macro": 'setAutoThreshold("{method} dark"); run("Convert to Mask");',
        "params": {"method": "Otsu"},
    },
    # Filters
    "gaussian_blur": {
        "category": "filter",
        "description": "Gaussian blur",
        "macro": 'run("Gaussian Blur...", "sigma={sigma}");',
        "params": {"sigma": 2.0},
    },
    "median": {
        "category": "filter",
        "description": "Median filter",
        "macro": 'run("Median...", "radius={radius}");',
        "params": {"radius": 2.0},
    },
    "unsharp_mask": {
        "category": "filter",
        "description": "Unsharp mask for sharpening",
        "macro": 'run("Unsharp Mask...", "radius={radius} mask={mask}");',
        "params": {"radius": 1.0, "mask": 0.6},
    },
    "subtract_background": {
        "category": "filter",
        "description": "Rolling-ball background subtraction",
        "macro": 'run("Subtract Background...", "rolling={radius}");',
        "params": {"radius": 50},
    },
    # Morphological
    "erode": {
        "category": "morphology",
        "description": "Morphological erosion",
        "macro": 'run("Erode");',
        "params": {},
    },
    "dilate": {
        "category": "morphology",
        "description": "Morphological dilation",
        "macro": 'run("Dilate");',
        "params": {},
    },
    "open": {
        "category": "morphology",
        "description": "Morphological opening (erode then dilate)",
        "macro": 'run("Open");',
        "params": {},
    },
    "close": {
        "category": "morphology",
        "description": "Morphological closing (dilate then erode)",
        "macro": 'run("Close-");',
        "params": {},
    },
    "skeletonize": {
        "category": "morphology",
        "description": "Skeletonize binary image",
        "macro": 'run("Skeletonize");',
        "params": {},
    },
    "watershed": {
        "category": "morphology",
        "description": "Watershed segmentation",
        "macro": 'run("Watershed");',
        "params": {},
    },
    "fill_holes": {
        "category": "morphology",
        "description": "Fill holes in binary image",
        "macro": 'run("Fill Holes");',
        "params": {},
    },
    # Type conversions
    "to_8bit": {
        "category": "convert",
        "description": "Convert to 8-bit",
        "macro": 'run("8-bit");',
        "params": {},
    },
    "to_16bit": {
        "category": "convert",
        "description": "Convert to 16-bit",
        "macro": 'run("16-bit");',
        "params": {},
    },
    "to_32bit": {
        "category": "convert",
        "description": "Convert to 32-bit",
        "macro": 'run("32-bit");',
        "params": {},
    },
    "to_rgb": {
        "category": "convert",
        "description": "Convert to RGB color",
        "macro": 'run("RGB Color");',
        "params": {},
    },
    # Spatial
    "scale": {
        "category": "spatial",
        "description": "Scale image",
        "macro": 'run("Scale...", "x={scale_x} y={scale_y} interpolation=Bilinear create");',
        "params": {"scale_x": 1.0, "scale_y": 1.0},
    },
    "rotate": {
        "category": "spatial",
        "description": "Rotate image",
        "macro": 'run("Rotate... ", "angle={angle} grid=1 interpolation=Bilinear");',
        "params": {"angle": 0},
    },
    "flip_horizontal": {
        "category": "spatial",
        "description": "Flip horizontally",
        "macro": 'run("Flip Horizontally");',
        "params": {},
    },
    "flip_vertical": {
        "category": "spatial",
        "description": "Flip vertically",
        "macro": 'run("Flip Vertically");',
        "params": {},
    },
    "crop": {
        "category": "spatial",
        "description": "Crop to selection or specified region",
        "macro": 'makeRectangle({x}, {y}, {width}, {height}); run("Crop");',
        "params": {"x": 0, "y": 0, "width": 100, "height": 100},
    },
    # Analysis helpers
    "find_edges": {
        "category": "analysis",
        "description": "Find edges",
        "macro": 'run("Find Edges");',
        "params": {},
    },
    "enhance_contrast": {
        "category": "adjust",
        "description": "Enhance local contrast (CLAHE)",
        "macro": 'run("Enhance Contrast...", "saturated={saturated} normalize");',
        "params": {"saturated": 0.35},
    },
    # Stack operations
    "z_project_max": {
        "category": "stack",
        "description": "Z-project max intensity",
        "macro": 'run("Z Project...", "projection=[Max Intensity]");',
        "params": {},
    },
    "z_project_avg": {
        "category": "stack",
        "description": "Z-project average intensity",
        "macro": 'run("Z Project...", "projection=[Average Intensity]");',
        "params": {},
    },
    "z_project_sum": {
        "category": "stack",
        "description": "Z-project sum slices",
        "macro": 'run("Z Project...", "projection=[Sum Slices]");',
        "params": {},
    },
    "split_channels": {
        "category": "stack",
        "description": "Split multi-channel image into separate channels",
        "macro": 'run("Split Channels");',
        "params": {},
    },
}


def list_operations(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """List available processing operations."""
    result = []
    for name, op in OPERATIONS.items():
        if category and op["category"] != category:
            continue
        result.append({
            "name": name,
            "category": op["category"],
            "description": op["description"],
            "params": op["params"],
        })
    return result


def get_operation_info(name: str) -> Dict[str, Any]:
    """Get details about a specific operation."""
    if name not in OPERATIONS:
        available = ", ".join(sorted(OPERATIONS.keys()))
        raise ValueError(f"Unknown operation: {name}. Available: {available}")
    op = OPERATIONS[name]
    return {
        "name": name,
        "category": op["category"],
        "description": op["description"],
        "macro_template": op["macro"],
        "params": op["params"],
    }


def add_processing_step(
    project: Dict[str, Any],
    operation: str,
    image_index: int = 0,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Add a processing operation to the project log.

    The operation is recorded for later execution via Fiji backend.
    """
    if operation not in OPERATIONS:
        available = ", ".join(sorted(OPERATIONS.keys()))
        raise ValueError(f"Unknown operation: {operation}. Available: {available}")

    op = OPERATIONS[operation]
    merged_params = dict(op["params"])
    if params:
        merged_params.update(params)

    # Generate the macro command with parameters
    macro_cmd = op["macro"].format(**merged_params)

    step = {
        "id": len(project.get("processing_log", [])),
        "operation": operation,
        "category": op["category"],
        "image_index": image_index,
        "params": merged_params,
        "macro": macro_cmd,
        "timestamp": datetime.now().isoformat(),
    }

    if "processing_log" not in project:
        project["processing_log"] = []
    project["processing_log"].append(step)
    return step


def remove_processing_step(project: Dict[str, Any], step_index: int) -> Dict[str, Any]:
    """Remove a processing step by index."""
    log = project.get("processing_log", [])
    if step_index < 0 or step_index >= len(log):
        raise IndexError(f"Step index {step_index} out of range (0-{len(log) - 1})")
    removed = log.pop(step_index)
    for i, s in enumerate(log):
        s["id"] = i
    return removed


def list_processing_log(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List all processing steps."""
    return [
        {
            "id": s["id"],
            "operation": s["operation"],
            "category": s["category"],
            "image_index": s.get("image_index", 0),
            "params": s["params"],
        }
        for s in project.get("processing_log", [])
    ]


def build_macro(project: Dict[str, Any], image_index: int = 0) -> str:
    """Build a complete ImageJ macro from the processing log.

    Concatenates all macro commands for the given image into
    a single executable macro string.
    """
    steps = [
        s for s in project.get("processing_log", [])
        if s.get("image_index", 0) == image_index
    ]
    lines = [s["macro"] for s in steps]
    return "\n".join(lines)
