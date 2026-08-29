"""R CLI - Statistical analysis registry and script generation."""

from datetime import datetime
from typing import Dict, Any, List, Optional

ANALYSES = {
    "ttest": {
        "category": "hypothesis",
        "description": "Two-sample or paired t-test",
        "params": {"formula": "required", "paired": False, "alternative": "two.sided"},
    },
    "anova": {
        "category": "hypothesis",
        "description": "One-way or multi-factor ANOVA",
        "params": {"formula": "required"},
    },
    "regression": {
        "category": "modeling",
        "description": "Linear regression (lm)",
        "params": {"formula": "required"},
    },
    "correlation": {
        "category": "descriptive",
        "description": "Correlation test between two variables",
        "params": {"x": "required", "y": "required", "method": "pearson"},
    },
    "pca": {
        "category": "multivariate",
        "description": "Principal Component Analysis",
        "params": {"columns": "all_numeric", "scale": True},
    },
    "summary_stats": {
        "category": "descriptive",
        "description": "Descriptive statistics (mean, sd, median, quartiles)",
        "params": {"columns": "all"},
    },
    "chi_squared": {
        "category": "hypothesis",
        "description": "Chi-squared test of independence",
        "params": {"x": "required", "y": "required"},
    },
    "wilcoxon": {
        "category": "hypothesis",
        "description": "Wilcoxon rank-sum / signed-rank test",
        "params": {"formula": "required", "paired": False},
    },
}


def list_analyses(category: Optional[str] = None) -> List[Dict[str, Any]]:
    """List available analysis types, optionally filtered by category.

    Args:
        category: Filter by category (e.g., "hypothesis", "descriptive").

    Returns:
        List of analysis info dicts.
    """
    result = []
    for name, info in ANALYSES.items():
        if category is not None and info["category"] != category:
            continue
        result.append({
            "name": name,
            "category": info["category"],
            "description": info["description"],
            "params": info["params"],
        })
    return result


def get_analysis_info(name: str) -> Dict[str, Any]:
    """Get full information about an analysis type.

    Args:
        name: Analysis type name (e.g., "ttest", "regression").

    Returns:
        Analysis info dict.

    Raises:
        ValueError: If the analysis type is unknown.
    """
    if name not in ANALYSES:
        raise ValueError(
            f"Unknown analysis type '{name}'. "
            f"Available: {', '.join(ANALYSES.keys())}"
        )
    info = ANALYSES[name]
    return {
        "name": name,
        "category": info["category"],
        "description": info["description"],
        "params": info["params"],
    }


def add_analysis(
    project: Dict[str, Any],
    analysis_type: str,
    dataset_index: int,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Add an analysis to the project.

    Args:
        project: The project dict.
        analysis_type: Type of analysis (must be in ANALYSES).
        dataset_index: Index of the dataset to analyze.
        params: Override default parameters.

    Returns:
        The created analysis entry.
    """
    if analysis_type not in ANALYSES:
        raise ValueError(
            f"Unknown analysis type '{analysis_type}'. "
            f"Available: {', '.join(ANALYSES.keys())}"
        )

    datasets = project.get("datasets", [])
    if dataset_index < 0 or dataset_index >= len(datasets):
        raise IndexError(
            f"Dataset index {dataset_index} out of range (0-{len(datasets) - 1})."
        )

    if "analyses" not in project:
        project["analyses"] = []

    # Merge default params with provided overrides
    merged_params = dict(ANALYSES[analysis_type]["params"])
    if params:
        merged_params.update(params)

    analysis_id = len(project["analyses"]) + 1
    entry = {
        "id": analysis_id,
        "type": analysis_type,
        "dataset_index": dataset_index,
        "params": merged_params,
        "results": None,
        "added": datetime.now().isoformat(),
    }
    project["analyses"].append(entry)
    return entry


def remove_analysis(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Remove an analysis by index.

    Args:
        project: The project dict.
        index: Zero-based index into the analyses list.

    Returns:
        The removed analysis entry.
    """
    analyses = project.get("analyses", [])
    if index < 0 or index >= len(analyses):
        raise IndexError(
            f"Analysis index {index} out of range (0-{len(analyses) - 1})."
        )
    return analyses.pop(index)


def list_analysis_log(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List all analyses with summary information.

    Returns:
        List of analysis summary dicts.
    """
    result = []
    for analysis in project.get("analyses", []):
        result.append({
            "id": analysis.get("id"),
            "type": analysis.get("type", ""),
            "dataset_index": analysis.get("dataset_index"),
            "params": analysis.get("params", {}),
            "has_results": analysis.get("results") is not None,
            "added": analysis.get("added", ""),
        })
    return result


def build_analysis_script(
    project: Dict[str, Any], analysis_index: int
) -> str:
    """Generate an R script for a registered analysis.

    Args:
        project: The project dict.
        analysis_index: Zero-based index into the analyses list.

    Returns:
        Complete R script string ready for execution.
    """
    analyses = project.get("analyses", [])
    if analysis_index < 0 or analysis_index >= len(analyses):
        raise IndexError(
            f"Analysis index {analysis_index} out of range "
            f"(0-{len(analyses) - 1})."
        )

    analysis = analyses[analysis_index]
    dataset_index = analysis["dataset_index"]
    params = analysis.get("params", {})
    analysis_type = analysis["type"]

    # Import here to avoid circular imports at module level
    from cli_anything.r.core.data import _build_load_code, get_dataset

    dataset = get_dataset(project, dataset_index)
    load_code = _build_load_code(dataset)

    lines = ["library(jsonlite)", load_code, ""]

    if analysis_type == "ttest":
        formula = params["formula"]
        paired = str(params.get("paired", False)).upper()
        alt = params.get("alternative", "two.sided")
        lines.append(
            f'result <- t.test({formula}, data=.data, '
            f'paired={paired}, alternative="{alt}")'
        )
        lines.append(_extract_hypothesis_test())

    elif analysis_type == "anova":
        formula = params["formula"]
        lines.append(f"result <- summary(aov({formula}, data=.data))")
        lines.append(
            'output <- list(\n'
            '    table = as.data.frame(result[[1]]),\n'
            '    method = "ANOVA"\n'
            ')\n'
            'cat(toJSON(output, auto_unbox=TRUE, pretty=TRUE))'
        )

    elif analysis_type == "regression":
        formula = params["formula"]
        lines.append(f"model <- lm({formula}, data=.data)")
        lines.append("result <- summary(model)")
        lines.append(
            'output <- list(\n'
            '    coefficients = as.data.frame(result$coefficients),\n'
            '    r.squared = result$r.squared,\n'
            '    adj.r.squared = result$adj.r.squared,\n'
            '    f.statistic = result$fstatistic[1],\n'
            '    p.value = pf(result$fstatistic[1], result$fstatistic[2], '
            'result$fstatistic[3], lower.tail=FALSE)\n'
            ')\n'
            'cat(toJSON(output, auto_unbox=TRUE, pretty=TRUE))'
        )

    elif analysis_type == "correlation":
        x = params["x"]
        y = params["y"]
        method = params.get("method", "pearson")
        lines.append(
            f'result <- cor.test(.data${x}, .data${y}, method="{method}")'
        )
        lines.append(_extract_hypothesis_test())

    elif analysis_type == "pca":
        scale = str(params.get("scale", True)).upper()
        lines.append(
            f"result <- prcomp(.data[, sapply(.data, is.numeric)], "
            f"scale.={scale})"
        )
        lines.append(
            'output <- list(\n'
            '    sdev = result$sdev,\n'
            '    rotation = as.data.frame(result$rotation),\n'
            '    proportion_of_variance = (result$sdev^2) / '
            'sum(result$sdev^2)\n'
            ')\n'
            'cat(toJSON(output, auto_unbox=TRUE, pretty=TRUE))'
        )

    elif analysis_type == "summary_stats":
        lines.append("result <- summary(.data)")
        lines.append(
            'cat(paste(capture.output(result), collapse="\\n"))'
        )

    elif analysis_type == "chi_squared":
        x = params["x"]
        y = params["y"]
        lines.append(
            f"result <- chisq.test(table(.data${x}, .data${y}))"
        )
        lines.append(_extract_hypothesis_test())

    elif analysis_type == "wilcoxon":
        formula = params["formula"]
        paired = str(params.get("paired", False)).upper()
        lines.append(
            f"result <- wilcox.test({formula}, data=.data, "
            f"paired={paired})"
        )
        lines.append(_extract_hypothesis_test())

    else:
        raise ValueError(f"No script generator for analysis type '{analysis_type}'.")

    return "\n".join(lines) + "\n"


def _extract_hypothesis_test() -> str:
    """Return R code to extract and output standard hypothesis test results."""
    return (
        'output <- list(\n'
        '    statistic = as.numeric(result$statistic),\n'
        '    p.value = result$p.value,\n'
        '    conf.int = if (!is.null(result$conf.int)) as.numeric(result$conf.int) '
        'else NULL,\n'
        '    method = result$method,\n'
        '    estimate = if (!is.null(result$estimate)) as.numeric(result$estimate) '
        'else NULL\n'
        ')\n'
        'cat(toJSON(output, auto_unbox=TRUE, pretty=TRUE))'
    )
