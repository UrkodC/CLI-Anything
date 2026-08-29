"""Fiji CLI - Core project management module.

A Fiji CLI project is a JSON document that tracks images, ROIs,
measurements, processing history, and macro operations. The real
Fiji/ImageJ application is invoked for actual image processing
and analysis.
"""

import json
import os
from datetime import datetime
from typing import Optional, Dict, Any, List


# Default image profiles for scientific imaging
PROFILES = {
    "confocal_512": {"width": 512, "height": 512, "bit_depth": 16, "channels": 1},
    "confocal_1024": {"width": 1024, "height": 1024, "bit_depth": 16, "channels": 1},
    "widefield_2048": {"width": 2048, "height": 2048, "bit_depth": 16, "channels": 1},
    "rgb_1024": {"width": 1024, "height": 1024, "bit_depth": 8, "channels": 3},
    "timelapse_512": {"width": 512, "height": 512, "bit_depth": 16, "channels": 1, "slices": 1, "frames": 100},
    "zstack_512": {"width": 512, "height": 512, "bit_depth": 16, "channels": 1, "slices": 20, "frames": 1},
    "hyperstack": {"width": 512, "height": 512, "bit_depth": 16, "channels": 3, "slices": 10, "frames": 50},
    "electron_4096": {"width": 4096, "height": 4096, "bit_depth": 8, "channels": 1},
    "plate_2160": {"width": 2160, "height": 2160, "bit_depth": 16, "channels": 4},
}

PROJECT_VERSION = "1.0"


def create_project(
    width: int = 512,
    height: int = 512,
    bit_depth: int = 8,
    channels: int = 1,
    slices: int = 1,
    frames: int = 1,
    image_type: str = "8-bit",
    name: str = "untitled",
    profile: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new Fiji CLI project.

    Args:
        width: Image width in pixels.
        height: Image height in pixels.
        bit_depth: Bits per channel (8 or 16).
        channels: Number of channels.
        slices: Number of Z-slices (for stacks).
        frames: Number of time frames (for time-lapse).
        image_type: ImageJ type string (8-bit, 16-bit, 32-bit, RGB).
        name: Project name.
        profile: Named profile to use.
    """
    if profile and profile in PROFILES:
        p = PROFILES[profile]
        width = p["width"]
        height = p["height"]
        bit_depth = p["bit_depth"]
        channels = p["channels"]
        slices = p.get("slices", 1)
        frames = p.get("frames", 1)

    if width < 1 or height < 1:
        raise ValueError(f"Image dimensions must be positive: {width}x{height}")
    if bit_depth not in (8, 16, 32):
        raise ValueError(f"Invalid bit depth: {bit_depth}. Use 8, 16, or 32.")
    if channels < 1:
        raise ValueError(f"Channels must be positive: {channels}")
    if slices < 1:
        raise ValueError(f"Slices must be positive: {slices}")
    if frames < 1:
        raise ValueError(f"Frames must be positive: {frames}")

    # Derive ImageJ type
    if image_type == "auto":
        if channels == 3 and bit_depth == 8:
            image_type = "RGB"
        elif bit_depth == 8:
            image_type = "8-bit"
        elif bit_depth == 16:
            image_type = "16-bit"
        else:
            image_type = "32-bit"

    project = {
        "version": PROJECT_VERSION,
        "name": name,
        "image": {
            "width": width,
            "height": height,
            "bit_depth": bit_depth,
            "channels": channels,
            "slices": slices,
            "frames": frames,
            "image_type": image_type,
        },
        "images": [],
        "rois": [],
        "measurements": [],
        "processing_log": [],
        "macros": [],
        "metadata": {
            "created": datetime.now().isoformat(),
            "modified": datetime.now().isoformat(),
            "software": "fiji-cli 1.0",
        },
    }
    return project


def open_project(path: str) -> Dict[str, Any]:
    """Open a .fiji-cli.json project file."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"Project file not found: {path}")
    with open(path, "r") as f:
        project = json.load(f)
    if "version" not in project or "image" not in project:
        raise ValueError(f"Invalid project file: {path}")
    return project


def save_project(project: Dict[str, Any], path: str) -> str:
    """Save project to a .fiji-cli.json file."""
    project["metadata"]["modified"] = datetime.now().isoformat()
    with open(path, "w") as f:
        json.dump(project, f, indent=2, default=str)
    return path


def get_project_info(project: Dict[str, Any]) -> Dict[str, Any]:
    """Get summary information about the project."""
    image = project["image"]
    return {
        "name": project.get("name", "untitled"),
        "version": project.get("version", "unknown"),
        "image": {
            "width": image["width"],
            "height": image["height"],
            "bit_depth": image.get("bit_depth", 8),
            "channels": image.get("channels", 1),
            "slices": image.get("slices", 1),
            "frames": image.get("frames", 1),
            "image_type": image.get("image_type", "8-bit"),
        },
        "image_count": len(project.get("images", [])),
        "roi_count": len(project.get("rois", [])),
        "measurement_count": len(project.get("measurements", [])),
        "processing_steps": len(project.get("processing_log", [])),
        "metadata": project.get("metadata", {}),
    }


def list_profiles() -> List[Dict[str, Any]]:
    """List all available image profiles."""
    result = []
    for name, p in PROFILES.items():
        result.append({
            "name": name,
            "width": p["width"],
            "height": p["height"],
            "bit_depth": p["bit_depth"],
            "channels": p["channels"],
            "slices": p.get("slices", 1),
            "frames": p.get("frames", 1),
        })
    return result
