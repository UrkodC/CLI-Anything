"""R CLI - Custom script management."""

from datetime import datetime
from typing import Dict, Any, List, Optional


def add_script(
    project: Dict[str, Any],
    code: str,
    name: Optional[str] = None,
    description: str = "",
) -> Dict[str, Any]:
    """Add a custom R script to the project."""
    if "scripts" not in project:
        project["scripts"] = []

    script_id = len(project["scripts"]) + 1
    if name is None:
        name = f"script_{script_id}"

    entry = {
        "id": script_id,
        "name": name,
        "code": code,
        "description": description,
        "added": datetime.now().isoformat(),
    }
    project["scripts"].append(entry)
    return entry


def remove_script(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Remove a script by index."""
    scripts = project.get("scripts", [])
    if index < 0 or index >= len(scripts):
        raise IndexError(
            f"Script index {index} out of range (0-{len(scripts) - 1})."
        )
    return scripts.pop(index)


def list_scripts(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List all scripts with summary information."""
    result = []
    for script in project.get("scripts", []):
        result.append({
            "id": script.get("id"),
            "name": script.get("name", ""),
            "description": script.get("description", ""),
            "added": script.get("added", ""),
            "code_lines": len(script.get("code", "").splitlines()),
        })
    return result


def get_script(project: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Get the full script entry including code."""
    scripts = project.get("scripts", [])
    if index < 0 or index >= len(scripts):
        raise IndexError(
            f"Script index {index} out of range (0-{len(scripts) - 1})."
        )
    return scripts[index]
