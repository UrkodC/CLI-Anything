"""Fiji CLI - Measurement and analysis module.

Manages measurement configurations and results. Actual
measurements are performed by Fiji via the backend.
"""

from datetime import datetime
from typing import Dict, Any, List, Optional


# Standard ImageJ measurement types
MEASUREMENT_TYPES = {
    "area": "Area of the selection or particle",
    "mean": "Mean gray value",
    "stddev": "Standard deviation of gray values",
    "min_max": "Minimum and maximum gray values",
    "centroid": "Center of mass (X, Y)",
    "perimeter": "Perimeter of the selection",
    "integrated_density": "Sum of pixel values (area * mean)",
    "skewness": "Third order moment of gray value distribution",
    "kurtosis": "Fourth order moment of gray value distribution",
    "median": "Median gray value",
    "circularity": "4*pi*area/perimeter^2 (1.0 = perfect circle)",
    "feret": "Feret diameter (max caliper distance)",
    "shape_descriptors": "Aspect ratio, roundness, solidity",
}

# Standard analysis commands
ANALYSIS_COMMANDS = {
    "measure": {
        "description": "Measure current selection or image",
        "macro": 'run("Measure");',
    },
    "analyze_particles": {
        "description": "Analyze particles in binary image",
        "macro": 'run("Analyze Particles...", "size={min_size}-{max_size} circularity={min_circ}-{max_circ} show=Outlines display clear summarize");',
        "params": {"min_size": 0, "max_size": "Infinity", "min_circ": 0.0, "max_circ": 1.0},
    },
    "histogram": {
        "description": "Get image histogram",
        "macro": 'run("Histogram");',
    },
    "plot_profile": {
        "description": "Plot intensity profile along a line",
        "macro": 'run("Plot Profile");',
    },
    "set_measurements": {
        "description": "Configure which measurements to include",
        "macro": 'run("Set Measurements...", "{measurements}");',
        "params": {"measurements": "area mean standard min centroid perimeter shape feret's integrated median display redirect=None decimal=3"},
    },
    "clear_results": {
        "description": "Clear results table",
        "macro": 'run("Clear Results");',
    },
    "set_scale": {
        "description": "Set spatial calibration",
        "macro": 'run("Set Scale...", "distance={distance} known={known} unit={unit}");',
        "params": {"distance": 1, "known": 1, "unit": "pixel"},
    },
}


def list_measurement_types() -> List[Dict[str, str]]:
    """List available measurement types."""
    return [
        {"name": name, "description": desc}
        for name, desc in MEASUREMENT_TYPES.items()
    ]


def list_analysis_commands() -> List[Dict[str, Any]]:
    """List available analysis commands."""
    return [
        {
            "name": name,
            "description": cmd["description"],
            "params": cmd.get("params", {}),
        }
        for name, cmd in ANALYSIS_COMMANDS.items()
    ]


def add_measurement_config(
    project: Dict[str, Any],
    measurements: Optional[List[str]] = None,
    scale_distance: float = 1.0,
    scale_known: float = 1.0,
    scale_unit: str = "pixel",
) -> Dict[str, Any]:
    """Configure measurements for the project.

    Args:
        project: The project dict.
        measurements: List of measurement types to include.
        scale_distance: Pixels per known distance.
        scale_known: Known distance value.
        scale_unit: Unit of measurement.
    """
    if measurements is None:
        measurements = ["area", "mean", "stddev", "min_max", "centroid"]

    for m in measurements:
        if m not in MEASUREMENT_TYPES:
            available = ", ".join(sorted(MEASUREMENT_TYPES.keys()))
            raise ValueError(f"Unknown measurement: {m}. Available: {available}")

    config = {
        "measurements": measurements,
        "scale": {
            "distance": scale_distance,
            "known": scale_known,
            "unit": scale_unit,
        },
        "configured": datetime.now().isoformat(),
    }
    project["measurement_config"] = config
    return config


def add_measurement_result(
    project: Dict[str, Any],
    label: str,
    values: Dict[str, Any],
    roi_index: Optional[int] = None,
    image_index: int = 0,
) -> Dict[str, Any]:
    """Record a measurement result.

    Args:
        project: The project dict.
        label: Label for this measurement.
        values: Dict of measurement name -> value pairs.
        roi_index: ROI used for measurement (if any).
        image_index: Image measured.
    """
    result = {
        "id": len(project.get("measurements", [])),
        "label": label,
        "values": values,
        "roi_index": roi_index,
        "image_index": image_index,
        "timestamp": datetime.now().isoformat(),
    }

    if "measurements" not in project:
        project["measurements"] = []
    project["measurements"].append(result)
    return result


def list_measurements(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List all measurement results."""
    return project.get("measurements", [])


def clear_measurements(project: Dict[str, Any]) -> int:
    """Clear all measurements. Returns count of cleared entries."""
    count = len(project.get("measurements", []))
    project["measurements"] = []
    return count


def build_analysis_macro(
    command: str,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    """Build an ImageJ macro for an analysis command."""
    if command not in ANALYSIS_COMMANDS:
        available = ", ".join(sorted(ANALYSIS_COMMANDS.keys()))
        raise ValueError(f"Unknown analysis command: {command}. Available: {available}")

    cmd = ANALYSIS_COMMANDS[command]
    merged = dict(cmd.get("params", {}))
    if params:
        merged.update(params)

    return cmd["macro"].format(**merged)
