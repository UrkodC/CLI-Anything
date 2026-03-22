"""Fiji CLI - Export module.

Handles exporting processed images via the Fiji backend.
Supports various output formats and applies processing pipeline.
"""

import os
from typing import Dict, Any, List, Optional


PRESETS = {
    "tiff": {
        "format": "tiff",
        "description": "TIFF (lossless, full quality)",
        "extension": ".tif",
        "macro_save": 'saveAs("Tiff", "{output}");',
    },
    "png": {
        "format": "png",
        "description": "PNG (lossless, web-compatible)",
        "extension": ".png",
        "macro_save": 'saveAs("PNG", "{output}");',
    },
    "jpeg": {
        "format": "jpeg",
        "description": "JPEG (lossy, small file)",
        "extension": ".jpg",
        "macro_save": 'saveAs("Jpeg", "{output}");',
    },
    "bmp": {
        "format": "bmp",
        "description": "BMP (uncompressed)",
        "extension": ".bmp",
        "macro_save": 'saveAs("BMP", "{output}");',
    },
    "avi": {
        "format": "avi",
        "description": "AVI (for stacks/time-lapse)",
        "extension": ".avi",
        "macro_save": 'run("AVI... ", "compression=JPEG frame=25 save={output}");',
    },
    "results_csv": {
        "format": "csv",
        "description": "Results table as CSV",
        "extension": ".csv",
        "macro_save": 'saveAs("Results", "{output}");',
    },
}


def list_presets() -> List[Dict[str, Any]]:
    """List export presets."""
    return [
        {
            "name": name,
            "format": p["format"],
            "description": p["description"],
            "extension": p["extension"],
        }
        for name, p in PRESETS.items()
    ]


def get_preset_info(name: str) -> Dict[str, Any]:
    """Get preset details."""
    if name not in PRESETS:
        available = ", ".join(sorted(PRESETS.keys()))
        raise ValueError(f"Unknown preset: {name}. Available: {available}")
    p = PRESETS[name]
    return {
        "name": name,
        "format": p["format"],
        "description": p["description"],
        "extension": p["extension"],
    }


def render(
    project: Dict[str, Any],
    output_path: str,
    preset: str = "tiff",
    overwrite: bool = False,
    image_index: int = 0,
) -> Dict[str, Any]:
    """Render/export an image through Fiji.

    Builds a complete macro from the project's processing pipeline,
    runs it through Fiji headless, and saves the output.

    Args:
        project: The project dict.
        output_path: Path for the output file.
        preset: Export preset name.
        overwrite: Whether to overwrite existing files.
        image_index: Which image to process.
    """
    if preset not in PRESETS:
        available = ", ".join(sorted(PRESETS.keys()))
        raise ValueError(f"Unknown preset: {preset}. Available: {available}")

    abs_output = os.path.abspath(output_path)
    if os.path.exists(abs_output) and not overwrite:
        raise FileExistsError(f"Output file already exists: {abs_output}. Use --overwrite.")

    os.makedirs(os.path.dirname(abs_output) or ".", exist_ok=True)

    # Get the image to process
    images = project.get("images", [])
    if not images:
        raise RuntimeError("No images in project. Use 'image add' to add an image first.")
    if image_index < 0 or image_index >= len(images):
        raise IndexError(f"Image index {image_index} out of range (0-{len(images) - 1})")

    image_entry = images[image_index]
    input_path = image_entry["path"]

    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Source image not found: {input_path}")

    # Build the processing macro
    from cli_anything.fiji.core.processing import build_macro
    processing_macro = build_macro(project, image_index)

    # Build save command
    save_cmd = PRESETS[preset]["macro_save"].format(output=abs_output)

    # Build complete macro with batch mode for headless
    macro = 'setBatchMode(true);\n'
    macro += f'open("{input_path}");\n'
    if processing_macro:
        macro += processing_macro + "\n"
    macro += save_cmd + "\n"
    macro += 'close();\n'
    macro += 'setBatchMode(false);\n'

    # Execute via backend
    from cli_anything.fiji.utils.fiji_backend import run_macro
    result = run_macro(macro)

    if not os.path.exists(abs_output):
        raise RuntimeError(
            f"Fiji export produced no output file.\n"
            f"  Expected: {abs_output}\n"
            f"  stderr: {result.get('stderr', '')[-500:]}\n"
            f"  stdout: {result.get('stdout', '')[-500:]}"
        )

    file_size = os.path.getsize(abs_output)
    return {
        "output": abs_output,
        "format": PRESETS[preset]["format"],
        "preset": preset,
        "method": "fiji-headless",
        "file_size": file_size,
        "processing_steps": len(project.get("processing_log", [])),
    }
