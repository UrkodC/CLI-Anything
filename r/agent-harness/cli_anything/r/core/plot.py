"""R CLI - ggplot2 plot configuration and script generation."""

from datetime import datetime
from typing import Dict, Any, List, Optional

PLOT_TYPES = {
    "scatter": {
        "geom": "geom_point",
        "required_aes": ["x", "y"],
        "optional_aes": ["color", "size", "shape", "alpha"],
        "description": "Scatter plot",
    },
    "bar": {
        "geom": "geom_bar",
        "required_aes": ["x"],
        "optional_aes": ["fill", "weight", "color"],
        "description": "Bar chart",
    },
    "histogram": {
        "geom": "geom_histogram",
        "required_aes": ["x"],
        "optional_aes": ["fill", "bins", "binwidth", "color"],
        "description": "Histogram",
    },
    "boxplot": {
        "geom": "geom_boxplot",
        "required_aes": ["x", "y"],
        "optional_aes": ["fill", "color", "alpha"],
        "description": "Box plot",
    },
    "heatmap": {
        "geom": "geom_tile",
        "required_aes": ["x", "y", "fill"],
        "optional_aes": ["color", "alpha"],
        "description": "Heatmap",
    },
    "line": {
        "geom": "geom_line",
        "required_aes": ["x", "y"],
        "optional_aes": ["color", "linetype", "group", "alpha"],
        "description": "Line plot",
    },
    "density": {
        "geom": "geom_density",
        "required_aes": ["x"],
        "optional_aes": ["fill", "color", "alpha"],
        "description": "Density plot",
    },
    "violin": {
        "geom": "geom_violin",
        "required_aes": ["x", "y"],
        "optional_aes": ["fill", "color", "alpha"],
        "description": "Violin plot",
    },
}

THEMES = [
    "theme_minimal", "theme_bw", "theme_classic", "theme_dark",
    "theme_light", "theme_void", "theme_gray", "theme_linedraw",
]

# Aesthetic keys that are mapped inside aes() vs passed as geom arguments
_AES_KEYS = {"x", "y", "color", "fill", "size", "shape", "alpha",
             "linetype", "group", "weight"}
# Geom-level parameters (not in aes())
_GEOM_PARAMS = {"bins", "binwidth"}


def add_plot(
    project: Dict[str, Any],
    plot_type: str,
    dataset_index: int,
    aes_mapping: Dict[str, str],
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a plot configuration to the project.

    Args:
        project: The project dict.
        plot_type: Type of plot (must be in PLOT_TYPES).
        dataset_index: Index of the dataset to plot.
        aes_mapping: Aesthetic mapping dict (e.g., {"x": "mpg", "y": "hp"}).
        name: Display name for the plot.

    Returns:
        The created plot entry.

    Raises:
        ValueError: If plot_type is invalid or required aesthetics are missing.
    """
    if plot_type not in PLOT_TYPES:
        raise ValueError(
            f"Unknown plot type '{plot_type}'. "
            f"Available: {', '.join(PLOT_TYPES.keys())}"
        )

    datasets = project.get("datasets", [])
    if dataset_index < 0 or dataset_index >= len(datasets):
        raise IndexError(
            f"Dataset index {dataset_index} out of range "
            f"(0-{len(datasets) - 1})."
        )

    info = PLOT_TYPES[plot_type]
    missing = [a for a in info["required_aes"] if a not in aes_mapping]
    if missing:
        raise ValueError(
            f"Missing required aesthetics for '{plot_type}': {', '.join(missing)}. "
            f"Required: {', '.join(info['required_aes'])}"
        )

    if "plots" not in project:
        project["plots"] = []

    plot_id = len(project["plots"]) + 1
    if name is None:
        ds_name = datasets[dataset_index].get("name", "data")
        name = f"{plot_type}_{ds_name}"

    entry = {
        "id": plot_id,
        "type": plot_type,
        "dataset_index": dataset_index,
        "aes": dict(aes_mapping),
        "name": name,
        "customization": {},
        "added": datetime.now().isoformat(),
    }
    project["plots"].append(entry)
    return entry


def remove_plot(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Remove a plot by index.

    Args:
        project: The project dict.
        index: Zero-based index into the plots list.

    Returns:
        The removed plot entry.
    """
    plots = project.get("plots", [])
    if index < 0 or index >= len(plots):
        raise IndexError(
            f"Plot index {index} out of range (0-{len(plots) - 1})."
        )
    return plots.pop(index)


def list_plots(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List all plots with summary information.

    Returns:
        List of plot summary dicts.
    """
    result = []
    for plot in project.get("plots", []):
        result.append({
            "id": plot.get("id"),
            "name": plot.get("name", ""),
            "type": plot.get("type", ""),
            "dataset_index": plot.get("dataset_index"),
            "aes": plot.get("aes", {}),
            "added": plot.get("added", ""),
        })
    return result


def list_plot_types() -> List[Dict[str, Any]]:
    """List all available plot types.

    Returns:
        List of plot type info dicts.
    """
    result = []
    for name, info in PLOT_TYPES.items():
        result.append({
            "name": name,
            "geom": info["geom"],
            "description": info["description"],
            "required_aes": info["required_aes"],
            "optional_aes": info["optional_aes"],
        })
    return result


def customize_plot(
    project: Dict[str, Any], plot_index: int, **kwargs: Any
) -> Dict[str, Any]:
    """Customize a plot's appearance and labels.

    Valid keys: title, subtitle, x_label, y_label, theme, facet_formula,
    coord_flip, legend_position, caption.

    Args:
        project: The project dict.
        plot_index: Zero-based index into the plots list.
        **kwargs: Customization key-value pairs.

    Returns:
        The updated plot entry.
    """
    valid_keys = {
        "title", "subtitle", "x_label", "y_label", "theme",
        "facet_formula", "coord_flip", "legend_position", "caption",
    }

    plots = project.get("plots", [])
    if plot_index < 0 or plot_index >= len(plots):
        raise IndexError(
            f"Plot index {plot_index} out of range (0-{len(plots) - 1})."
        )

    invalid = set(kwargs.keys()) - valid_keys
    if invalid:
        raise ValueError(
            f"Invalid customization keys: {', '.join(invalid)}. "
            f"Valid: {', '.join(sorted(valid_keys))}"
        )

    plot_entry = plots[plot_index]
    plot_entry["customization"].update(kwargs)
    return plot_entry


def build_plot_script(
    project: Dict[str, Any],
    plot_index: int,
    output_path: str,
    format: str = "png",
    width: int = 800,
    height: int = 600,
    dpi: int = 300,
) -> str:
    """Generate a complete ggplot2 R script for a plot.

    Args:
        project: The project dict.
        plot_index: Zero-based index into the plots list.
        output_path: Path for the output image file.
        format: Output format (png, pdf, svg, jpeg, tiff).
        width: Image width in pixels.
        height: Image height in pixels.
        dpi: Resolution in DPI.

    Returns:
        Complete R script string.
    """
    plots = project.get("plots", [])
    if plot_index < 0 or plot_index >= len(plots):
        raise IndexError(
            f"Plot index {plot_index} out of range (0-{len(plots) - 1})."
        )

    plot_entry = plots[plot_index]
    plot_type = plot_entry["type"]
    aes_mapping = plot_entry.get("aes", {})
    customization = plot_entry.get("customization", {})
    info = PLOT_TYPES[plot_type]

    from cli_anything.r.core.data import _build_load_code, get_dataset

    dataset = get_dataset(project, plot_entry["dataset_index"])
    load_code = _build_load_code(dataset)

    # Build aes() string — only include keys that belong in aes()
    aes_parts = []
    geom_params = {}
    for key, val in aes_mapping.items():
        if key in _GEOM_PARAMS:
            geom_params[key] = val
        elif key in _AES_KEYS:
            aes_parts.append(f"{key}={val}")

    aes_str = ", ".join(aes_parts)

    # Build geom call with any extra parameters
    geom_name = info["geom"]
    geom_args = ", ".join(f"{k}={v}" for k, v in geom_params.items())
    geom_call = f"{geom_name}({geom_args})" if geom_args else f"{geom_name}()"

    # Build labs()
    labs_parts = []
    if "title" in customization:
        labs_parts.append(f'title="{customization["title"]}"')
    if "subtitle" in customization:
        labs_parts.append(f'subtitle="{customization["subtitle"]}"')
    if "x_label" in customization:
        labs_parts.append(f'x="{customization["x_label"]}"')
    if "y_label" in customization:
        labs_parts.append(f'y="{customization["y_label"]}"')
    if "caption" in customization:
        labs_parts.append(f'caption="{customization["caption"]}"')

    # Build layers
    layers = [geom_call]

    if labs_parts:
        layers.append(f"labs({', '.join(labs_parts)})")

    # Theme
    theme = customization.get("theme", "theme_minimal")
    if theme not in THEMES:
        theme = "theme_minimal"
    layers.append(f"{theme}()")

    # Faceting
    if "facet_formula" in customization:
        layers.append(f'facet_wrap(~{customization["facet_formula"]})')

    # Coord flip
    if customization.get("coord_flip"):
        layers.append("coord_flip()")

    # Legend position
    if "legend_position" in customization:
        pos = customization["legend_position"]
        layers.append(f'theme(legend.position="{pos}")')

    layers_str = " +\n  ".join(layers)

    # Compute dimensions for ggsave
    # For raster formats, convert pixels to inches using dpi
    # For vector formats, convert pixels to inches using 72 ppi
    if format.lower() in ("pdf", "svg"):
        w_inches = width / 72
        h_inches = height / 72
    else:
        w_inches = width / dpi
        h_inches = height / dpi

    r_output_path = output_path.replace("\\", "/")

    script = f"""library(ggplot2)
{load_code}

p <- ggplot(.data, aes({aes_str})) +
  {layers_str}

ggsave("{r_output_path}", p, width={w_inches:.4f}, height={h_inches:.4f}, dpi={dpi})
"""
    return script
