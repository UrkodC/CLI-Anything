"""Fiji CLI - Channel operations module.

Handles channel merging, splitting, and LUT assignment
for multi-channel microscopy images.
"""

from typing import Dict, Any, List, Optional


# ImageJ channel color assignments
# c1=Red, c2=Green, c3=Blue, c4=Gray, c5=Cyan, c6=Magenta, c7=Yellow
COLOR_TO_CHANNEL = {
    "Red": "c1",
    "Green": "c2",
    "Blue": "c3",
    "Gray": "c4",
    "Cyan": "c5",
    "Magenta": "c6",
    "Yellow": "c7",
}

AVAILABLE_LUTS = [
    {"name": "Grays", "description": "Grayscale (best for single channels)", "colorblind_safe": True},
    {"name": "Green", "description": "Green channel", "colorblind_safe": True},
    {"name": "Magenta", "description": "Magenta (colorblind-safe alternative to Red)", "colorblind_safe": True},
    {"name": "Cyan", "description": "Cyan channel", "colorblind_safe": True},
    {"name": "Yellow", "description": "Yellow channel", "colorblind_safe": True},
    {"name": "Blue", "description": "Blue channel", "colorblind_safe": True},
    {"name": "Red", "description": "Red channel (avoid for red/green pairs)", "colorblind_safe": False},
    {"name": "Fire", "description": "Fire LUT (perceptually uniform)", "colorblind_safe": True},
    {"name": "Ice", "description": "Ice LUT", "colorblind_safe": True},
    {"name": "mpl-viridis", "description": "Viridis (perceptually uniform)", "colorblind_safe": True},
    {"name": "mpl-inferno", "description": "Inferno (perceptually uniform)", "colorblind_safe": True},
    {"name": "HiLo", "description": "HiLo (QC: highlights saturated/zero pixels)", "colorblind_safe": True},
]


def list_luts() -> List[Dict[str, Any]]:
    """List available lookup tables."""
    return AVAILABLE_LUTS


def build_merge_macro(
    channels: List[Dict[str, str]],
    create_composite: bool = True,
) -> str:
    """Build an ImageJ macro to merge multiple channel images.

    Args:
        channels: List of dicts with 'path' and 'color' keys.
            color must be one of: Red, Green, Blue, Gray, Cyan, Magenta, Yellow.
        create_composite: If True, create a composite (recommended). If False, create RGB.
    """
    lines = ['setBatchMode(true);']

    titles = []
    for i, ch in enumerate(channels):
        title = f"ch{i}"
        lines.append(f'open("{ch["path"]}");')
        lines.append(f'rename("{title}");')
        titles.append(title)

    merge_args = []
    for i, ch in enumerate(channels):
        color = ch["color"]
        if color not in COLOR_TO_CHANNEL:
            raise ValueError(f"Unknown color: {color}. Use one of: {', '.join(COLOR_TO_CHANNEL.keys())}")
        channel_key = COLOR_TO_CHANNEL[color]
        merge_args.append(f"{channel_key}={titles[i]}")

    composite_flag = " create" if create_composite else ""
    lines.append(f'run("Merge Channels...", "{" ".join(merge_args)}{composite_flag}");')
    lines.append('setBatchMode(false);')

    return "\n".join(lines)


def build_split_macro(
    input_path: str,
    output_dir: str,
) -> str:
    """Build an ImageJ macro to split a composite image into channels."""
    lines = [
        'setBatchMode(true);',
        f'open("{input_path}");',
        'title = getTitle();',
        'run("Split Channels");',
        'list = getList("image.titles");',
        'for (i = 0; i < list.length; i++) {',
        '    selectWindow(list[i]);',
        f'    saveAs("Tiff", "{output_dir}" + File.separator + list[i] + ".tif");',
        '    close();',
        '}',
        'setBatchMode(false);',
    ]
    return "\n".join(lines)
