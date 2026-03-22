"""Fiji CLI - Image management module.

Manages images within a project. Each image entry tracks
a file path or generated image with metadata.
"""

import os
from datetime import datetime
from typing import Dict, Any, List, Optional


def add_image(
    project: Dict[str, Any],
    path: str,
    name: Optional[str] = None,
    image_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Add an image reference to the project.

    Args:
        project: The project dict.
        path: Path to the image file.
        name: Display name (defaults to filename).
        image_type: Override image type detection.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Image file not found: {path}")

    abs_path = os.path.abspath(path)
    if name is None:
        name = os.path.basename(path)

    file_size = os.path.getsize(abs_path)
    ext = os.path.splitext(path)[1].lower()

    entry = {
        "id": len(project.get("images", [])),
        "name": name,
        "path": abs_path,
        "format": ext.lstrip("."),
        "file_size": file_size,
        "image_type": image_type or _guess_type(ext),
        "added": datetime.now().isoformat(),
    }

    if "images" not in project:
        project["images"] = []
    project["images"].append(entry)
    return entry


def remove_image(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Remove an image by index."""
    images = project.get("images", [])
    if index < 0 or index >= len(images):
        raise IndexError(f"Image index {index} out of range (0-{len(images) - 1})")
    removed = images.pop(index)
    # Re-index
    for i, img in enumerate(images):
        img["id"] = i
    return removed


def list_images(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List all images in the project."""
    return [
        {
            "id": img["id"],
            "name": img["name"],
            "path": img["path"],
            "format": img["format"],
            "file_size": img["file_size"],
            "image_type": img.get("image_type", "unknown"),
        }
        for img in project.get("images", [])
    ]


def get_image(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Get a specific image entry."""
    images = project.get("images", [])
    if index < 0 or index >= len(images):
        raise IndexError(f"Image index {index} out of range (0-{len(images) - 1})")
    return images[index]


def _guess_type(ext: str) -> str:
    """Guess ImageJ-compatible type from file extension."""
    multi_format = {".tif", ".tiff", ".nd2", ".lif", ".czi", ".oib", ".ome.tif"}
    if ext in multi_format:
        return "multi-series"
    return "single"
