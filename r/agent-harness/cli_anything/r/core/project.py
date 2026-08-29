"""R CLI - Core project management module."""

import json
import os
from datetime import datetime
from typing import Optional, Dict, Any, List

PROJECT_VERSION = "1.0"

PROFILES = {
    "basic_analysis": {
        "description": "Basic data analysis with ggplot2 and dplyr",
        "default_packages": ["ggplot2", "dplyr"],
    },
    "bioinformatics": {
        "description": "Bioinformatics analysis pipeline",
        "default_packages": ["ggplot2", "dplyr", "tidyr", "readr", "Biostrings"],
    },
    "machine_learning": {
        "description": "Machine learning with caret and friends",
        "default_packages": ["caret", "randomForest", "glmnet", "ggplot2"],
    },
    "time_series": {
        "description": "Time series analysis",
        "default_packages": ["forecast", "tseries", "zoo", "ggplot2"],
    },
    "tidyverse": {
        "description": "Full tidyverse data science stack",
        "default_packages": ["ggplot2", "dplyr", "tidyr", "readr", "purrr", "stringr", "forcats", "tibble"],
    },
}


def create_project(name: str = "untitled", profile: Optional[str] = None) -> Dict[str, Any]:
    """Create a new R CLI project."""
    packages = []
    if profile is not None:
        if profile not in PROFILES:
            raise ValueError(
                f"Unknown profile '{profile}'. Available: {', '.join(PROFILES.keys())}"
            )
        packages = list(PROFILES[profile]["default_packages"])

    now = datetime.now().isoformat()
    return {
        "version": PROJECT_VERSION,
        "name": name,
        "datasets": [],
        "analyses": [],
        "plots": [],
        "scripts": [],
        "packages": packages,
        "metadata": {
            "created": now,
            "modified": now,
            "software": "r-cli 1.0",
        },
    }


def open_project(path: str) -> Dict[str, Any]:
    """Open an existing R CLI project from a .r-cli.json file."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Project file not found: {path}")

    with open(path, "r") as f:
        project = json.load(f)

    if "version" not in project:
        raise ValueError("Invalid project file: missing 'version' key.")
    if "datasets" not in project:
        raise ValueError("Invalid project file: missing 'datasets' key.")

    return project


def save_project(project: Dict[str, Any], path: str) -> str:
    """Save an R CLI project to disk."""
    if "metadata" not in project:
        project["metadata"] = {}
    project["metadata"]["modified"] = datetime.now().isoformat()

    with open(path, "w") as f:
        json.dump(project, f, indent=2, default=str)

    return path


def get_project_info(project: Dict[str, Any]) -> Dict[str, Any]:
    """Get summary information about a project."""
    return {
        "name": project.get("name", "untitled"),
        "version": project.get("version", "unknown"),
        "dataset_count": len(project.get("datasets", [])),
        "analysis_count": len(project.get("analyses", [])),
        "plot_count": len(project.get("plots", [])),
        "script_count": len(project.get("scripts", [])),
        "packages": project.get("packages", []),
        "metadata": project.get("metadata", {}),
    }


def list_profiles() -> List[Dict[str, Any]]:
    """List all available project profiles."""
    result = []
    for name, info in PROFILES.items():
        result.append({
            "name": name,
            "description": info["description"],
            "default_packages": info["default_packages"],
        })
    return result
