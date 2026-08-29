"""R CLI - Dataset loading, inspection, and transformation."""

import os
from datetime import datetime
from typing import Dict, Any, List, Optional

# Format-to-R-loader mapping
LOADERS = {
    "csv": 'read.csv("{path}")',
    "tsv": 'read.delim("{path}")',
    "xlsx": 'readxl::read_excel("{path}")',
    "xls": 'readxl::read_excel("{path}")',
    "rds": 'readRDS("{path}")',
    "rda": '{{ load("{path}"); get(ls()[1]) }}',
    "json": 'jsonlite::fromJSON("{path}")',
    "sav": 'haven::read_sav("{path}")',
    "dta": 'haven::read_dta("{path}")',
}

BUILTIN_DATASETS = [
    "mtcars", "iris", "airquality", "faithful", "ToothGrowth",
    "PlantGrowth", "USArrests", "chickwts", "CO2", "sleep",
]


def add_dataset(
    project: Dict[str, Any],
    path: str,
    name: Optional[str] = None,
    dataset_type: str = "auto",
) -> Dict[str, Any]:
    """Add a dataset to the project.

    Args:
        project: The project dict.
        path: File path to the dataset, or dataset name for builtins.
        name: Display name (auto-detected from filename if None).
        dataset_type: "auto" to detect from file, or "builtin" for R built-in datasets.

    Returns:
        The created dataset entry dict.
    """
    if "datasets" not in project:
        project["datasets"] = []

    dataset_id = len(project["datasets"]) + 1

    if dataset_type == "builtin":
        if path not in BUILTIN_DATASETS:
            raise ValueError(
                f"Unknown built-in dataset '{path}'. "
                f"Available: {', '.join(BUILTIN_DATASETS)}"
            )
        entry = {
            "id": dataset_id,
            "name": name or path,
            "path": None,
            "format": "builtin",
            "added": datetime.now().isoformat(),
        }
    else:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Dataset file not found: {path}")

        ext = os.path.splitext(path)[1].lstrip(".").lower()
        if ext not in LOADERS:
            raise ValueError(
                f"Unsupported format '.{ext}'. "
                f"Supported: {', '.join(LOADERS.keys())}"
            )

        if name is None:
            name = os.path.splitext(os.path.basename(path))[0]

        entry = {
            "id": dataset_id,
            "name": name,
            "path": os.path.abspath(path),
            "format": ext,
            "added": datetime.now().isoformat(),
        }

    project["datasets"].append(entry)
    return entry


def remove_dataset(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Remove a dataset by index.

    Args:
        project: The project dict.
        index: Zero-based index into the datasets list.

    Returns:
        The removed dataset entry.
    """
    datasets = project.get("datasets", [])
    if index < 0 or index >= len(datasets):
        raise IndexError(
            f"Dataset index {index} out of range (0-{len(datasets) - 1})."
        )
    return datasets.pop(index)


def list_datasets(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List all datasets with summary information.

    Returns:
        List of dataset summary dicts.
    """
    result = []
    for ds in project.get("datasets", []):
        result.append({
            "id": ds.get("id"),
            "name": ds.get("name", ""),
            "format": ds.get("format", ""),
            "path": ds.get("path"),
            "added": ds.get("added", ""),
        })
    return result


def get_dataset(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Get the full dataset entry by index.

    Args:
        project: The project dict.
        index: Zero-based index into the datasets list.

    Returns:
        The dataset entry dict.
    """
    datasets = project.get("datasets", [])
    if index < 0 or index >= len(datasets):
        raise IndexError(
            f"Dataset index {index} out of range (0-{len(datasets) - 1})."
        )
    return datasets[index]


def _build_load_code(dataset: Dict[str, Any]) -> str:
    """Generate R code to load a dataset into the .data variable.

    Args:
        dataset: A dataset entry dict.

    Returns:
        R code string that assigns the dataset to .data.
    """
    fmt = dataset.get("format", "")

    if fmt == "builtin":
        name = dataset["name"]
        return f"data({name}); .data <- {name}"

    if fmt == "derived":
        # Derived datasets carry their own R expression
        source_code = dataset.get("source_load_code", "")
        expression = dataset.get("expression", "")
        derive_type = dataset.get("derive_type", "filter")
        if derive_type == "filter":
            return f"{source_code}\n.data <- subset(.data, {expression})"
        else:
            return f"{source_code}\n.data <- transform(.data, {expression})"

    path = dataset.get("path", "")
    r_path = path.replace("\\", "/")

    if fmt not in LOADERS:
        raise ValueError(f"No loader available for format '{fmt}'.")

    loader = LOADERS[fmt].format(path=r_path)
    return f".data <- {loader}"


def build_inspect_script(project: Dict[str, Any], index: int) -> str:
    """Generate an R script that outputs dataset structure as JSON.

    Args:
        project: The project dict.
        index: Zero-based index of the dataset.

    Returns:
        R script string.
    """
    dataset = get_dataset(project, index)
    load_code = _build_load_code(dataset)

    return f"""library(jsonlite)
{load_code}
result <- list(
    class = class(.data),
    nrow = nrow(.data),
    ncol = ncol(.data),
    colnames = colnames(.data),
    coltypes = sapply(.data, class)
)
cat(toJSON(result, auto_unbox=TRUE, pretty=TRUE))
"""


def build_summary_script(project: Dict[str, Any], index: int) -> str:
    """Generate an R script that outputs summary() as text.

    Args:
        project: The project dict.
        index: Zero-based index of the dataset.

    Returns:
        R script string.
    """
    dataset = get_dataset(project, index)
    load_code = _build_load_code(dataset)

    return f"""{load_code}
cat(paste(capture.output(summary(.data)), collapse="\\n"))
"""


def build_head_script(
    project: Dict[str, Any], index: int, n: int = 6
) -> str:
    """Generate an R script that outputs head() as JSON.

    Args:
        project: The project dict.
        index: Zero-based index of the dataset.
        n: Number of rows to show.

    Returns:
        R script string.
    """
    dataset = get_dataset(project, index)
    load_code = _build_load_code(dataset)

    return f"""library(jsonlite)
{load_code}
cat(toJSON(head(.data, {n}), auto_unbox=TRUE, pretty=TRUE))
"""


def filter_dataset(
    project: Dict[str, Any],
    source_index: int,
    expression: str,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a derived dataset created by filtering.

    Does NOT execute R. Records the filter expression and source for
    later script generation.

    Args:
        project: The project dict.
        source_index: Index of the source dataset.
        expression: R filter expression (e.g., "mpg > 20").
        name: Name for the derived dataset.

    Returns:
        The created dataset entry.
    """
    if "datasets" not in project:
        project["datasets"] = []

    source = get_dataset(project, source_index)
    source_load_code = _build_load_code(source)

    dataset_id = len(project["datasets"]) + 1
    if name is None:
        name = f"{source['name']}_filtered"

    entry = {
        "id": dataset_id,
        "name": name,
        "path": None,
        "format": "derived",
        "derive_type": "filter",
        "source_index": source_index,
        "expression": expression,
        "source_load_code": source_load_code,
        "added": datetime.now().isoformat(),
    }
    project["datasets"].append(entry)
    return entry


def transform_dataset(
    project: Dict[str, Any],
    source_index: int,
    expression: str,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a derived dataset created by transformation.

    Does NOT execute R. Records the transform expression and source for
    later script generation.

    Args:
        project: The project dict.
        source_index: Index of the source dataset.
        expression: R transform expression (e.g., "mpg_per_cyl = mpg / cyl").
        name: Name for the derived dataset.

    Returns:
        The created dataset entry.
    """
    if "datasets" not in project:
        project["datasets"] = []

    source = get_dataset(project, source_index)
    source_load_code = _build_load_code(source)

    dataset_id = len(project["datasets"]) + 1
    if name is None:
        name = f"{source['name']}_transformed"

    entry = {
        "id": dataset_id,
        "name": name,
        "path": None,
        "format": "derived",
        "derive_type": "transform",
        "source_index": source_index,
        "expression": expression,
        "source_load_code": source_load_code,
        "added": datetime.now().isoformat(),
    }
    project["datasets"].append(entry)
    return entry
