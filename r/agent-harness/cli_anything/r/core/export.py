"""R CLI - Export and render pipeline."""

import os
import tempfile
from typing import Dict, Any, Optional

PLOT_FORMATS = {
    "png": {"extension": ".png"},
    "pdf": {"extension": ".pdf"},
    "svg": {"extension": ".svg"},
    "jpeg": {"extension": ".jpg"},
    "tiff": {"extension": ".tif"},
}

DATA_FORMATS = {
    "csv": {"r_func": "write.csv", "extension": ".csv", "extra_args": "row.names=FALSE"},
    "tsv": {"r_func": "write.table", "extension": ".tsv", "extra_args": 'sep="\\t", row.names=FALSE'},
    "rds": {"r_func": "saveRDS", "extension": ".rds", "extra_args": ""},
    "xlsx": {"r_func": "writexl::write_xlsx", "extension": ".xlsx", "extra_args": ""},
}


def render_plot(
    project: Dict[str, Any],
    plot_index: int,
    output_path: str,
    format: str = "png",
    width: int = 800,
    height: int = 600,
    dpi: int = 300,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Render a plot to an image file by executing the generated R script.

    Args:
        project: The project dict.
        plot_index: Zero-based index into the plots list.
        output_path: Path for the output file.
        format: Output format (png, pdf, svg, jpeg, tiff).
        width: Image width in pixels.
        height: Image height in pixels.
        dpi: Resolution in DPI.
        overwrite: If True, overwrite existing output file.

    Returns:
        Dict with output path, format, file_size, and method.
    """
    plots = project.get("plots", [])
    if plot_index < 0 or plot_index >= len(plots):
        raise IndexError(
            f"Plot index {plot_index} out of range (0-{len(plots) - 1})."
        )

    if format not in PLOT_FORMATS:
        raise ValueError(
            f"Unsupported plot format '{format}'. "
            f"Available: {', '.join(PLOT_FORMATS.keys())}"
        )

    abs_output = os.path.abspath(output_path)
    if os.path.exists(abs_output) and not overwrite:
        raise FileExistsError(
            f"Output file already exists: {abs_output}. "
            f"Use overwrite=True to replace."
        )

    os.makedirs(os.path.dirname(abs_output) or ".", exist_ok=True)

    from cli_anything.r.core.plot import build_plot_script
    from cli_anything.r.utils import r_backend

    script = build_plot_script(
        project, plot_index, abs_output,
        format=format, width=width, height=height, dpi=dpi,
    )

    # Write script to temp file and execute
    tmp_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".R", delete=False, prefix="r_cli_export_"
        ) as f:
            f.write(script)
            tmp_file = f.name

        result = r_backend.run_script(tmp_file)

        if result["returncode"] != 0:
            raise RuntimeError(
                f"R script failed (exit {result['returncode']}).\n"
                f"stderr: {result.get('stderr', '')[-500:]}\n"
                f"stdout: {result.get('stdout', '')[-500:]}"
            )

        if not os.path.exists(abs_output):
            raise RuntimeError(
                f"Plot rendering produced no output file: {abs_output}\n"
                f"stderr: {result.get('stderr', '')[-500:]}"
            )

        file_size = os.path.getsize(abs_output)
        if file_size == 0:
            raise RuntimeError(
                f"Plot rendering produced empty file: {abs_output}"
            )

        return {
            "output": abs_output,
            "format": format,
            "file_size": file_size,
            "method": "rscript-ggsave",
        }
    finally:
        if tmp_file:
            try:
                os.unlink(tmp_file)
            except OSError:
                pass


def export_data(
    project: Dict[str, Any],
    dataset_index: int,
    output_path: str,
    format: str = "csv",
    overwrite: bool = False,
) -> Dict[str, Any]:
    """Export a dataset to a file by executing R code.

    Args:
        project: The project dict.
        dataset_index: Zero-based index of the dataset.
        output_path: Path for the output file.
        format: Output format (csv, tsv, rds, xlsx).
        overwrite: If True, overwrite existing output file.

    Returns:
        Dict with output path, format, file_size, and method.
    """
    datasets = project.get("datasets", [])
    if dataset_index < 0 or dataset_index >= len(datasets):
        raise IndexError(
            f"Dataset index {dataset_index} out of range "
            f"(0-{len(datasets) - 1})."
        )

    if format not in DATA_FORMATS:
        raise ValueError(
            f"Unsupported data format '{format}'. "
            f"Available: {', '.join(DATA_FORMATS.keys())}"
        )

    abs_output = os.path.abspath(output_path)
    if os.path.exists(abs_output) and not overwrite:
        raise FileExistsError(
            f"Output file already exists: {abs_output}. "
            f"Use overwrite=True to replace."
        )

    os.makedirs(os.path.dirname(abs_output) or ".", exist_ok=True)

    from cli_anything.r.core.data import _build_load_code, get_dataset
    from cli_anything.r.utils import r_backend

    dataset = get_dataset(project, dataset_index)
    load_code = _build_load_code(dataset)

    fmt_info = DATA_FORMATS[format]
    r_output_path = abs_output.replace("\\", "/")

    extra = fmt_info["extra_args"]
    if extra:
        write_call = f'{fmt_info["r_func"]}(.data, "{r_output_path}", {extra})'
    else:
        write_call = f'{fmt_info["r_func"]}(.data, "{r_output_path}")'

    script = f"{load_code}\n{write_call}\n"

    # Write script to temp file and execute
    tmp_file = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".R", delete=False, prefix="r_cli_export_"
        ) as f:
            f.write(script)
            tmp_file = f.name

        result = r_backend.run_script(tmp_file)

        if result["returncode"] != 0:
            raise RuntimeError(
                f"R export script failed (exit {result['returncode']}).\n"
                f"stderr: {result.get('stderr', '')[-500:]}\n"
                f"stdout: {result.get('stdout', '')[-500:]}"
            )

        if not os.path.exists(abs_output):
            raise RuntimeError(
                f"Data export produced no output file: {abs_output}\n"
                f"stderr: {result.get('stderr', '')[-500:]}"
            )

        return {
            "output": abs_output,
            "format": format,
            "file_size": os.path.getsize(abs_output),
            "method": "rscript",
        }
    finally:
        if tmp_file:
            try:
                os.unlink(tmp_file)
            except OSError:
                pass


def list_plot_formats() -> Dict[str, Any]:
    """Return available plot export formats.

    Returns:
        The PLOT_FORMATS dict.
    """
    return dict(PLOT_FORMATS)


def list_data_formats() -> Dict[str, Any]:
    """Return available data export formats.

    Returns:
        The DATA_FORMATS dict.
    """
    return dict(DATA_FORMATS)
