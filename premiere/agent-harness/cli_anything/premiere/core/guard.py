"""Safety checks that every live command runs before it talks to Premiere.

Each check looks at values that another module already read (the panel heartbeat, the panel version,
the state script's reply, the recovery marker). Nothing here talks to Premiere. A check returns a list
of warnings and raises CliError when the command must not go on. A read command warns where a write
command refuses.
"""
from __future__ import annotations

import os
from pathlib import Path

from defusedxml import ElementTree as SafeET

from cli_anything.premiere.core.bridge import HEARTBEAT_STALE_S, Heartbeat, read_recovery
from cli_anything.premiere.errors import CliError

TESTED_PREMIERE_PREFIXES = ("27.0.",)
TESTED_PANEL_VERSIONS = ("1.25.0",)
PANEL_MANIFEST = Path.home() / "Library/Application Support/Adobe/CEP/extensions/MCPBridgeCEP/CSXS/manifest.xml"

PROTOCOL_VERSION = 1
COMMAND_SCHEMA_VERSION = 1
OVERRIDE = "--allow-untested"


def panel_version(manifest: Path = PANEL_MANIFEST) -> str | None:
    """The ExtensionBundleVersion of the installed panel, or None when it cannot be read."""
    try:
        root = SafeET.parse(manifest).getroot()
    except (OSError, ValueError, SafeET.ParseError):
        return None
    return root.get("ExtensionBundleVersion") or None


def _gate(warnings: list[str], code: str, message: str, *, write: bool, allow_untested: bool,
          hint: str = "") -> None:
    """An untested-version finding: a warning to a read, a refusal to a write, a warning that names the override."""
    if not write:
        warnings.append(message)
    elif allow_untested:
        warnings.append(f"{message} Continuing because {OVERRIDE} was given.")
    else:
        raise CliError(code, f"{message} {hint}".strip())


def check_bridge(heartbeat: Heartbeat | None, panel: str | None, *, write: bool,
                 allow_untested: bool) -> list[str]:
    """Is the panel in Premiere alive, and of a version this CLI was tested with?"""
    if heartbeat is not None and heartbeat.age_s > HEARTBEAT_STALE_S:
        raise CliError("bridge_down", f"The MCP panel in Premiere does not answer: its last heartbeat is "
                                      f"{heartbeat.age_s:.0f} s old. A dialog in Premiere may block it: close the "
                                      "dialog. If no dialog is open, open Window > Extensions > MCP for Adobe "
                                      "Premiere Pro.")
    if heartbeat is None or not heartbeat.fresh:
        raise CliError("bridge_down", "Premiere is not running its MCP panel. "
                                      "Open Window > Extensions > MCP for Adobe Premiere Pro.")
    found = []
    if heartbeat.protocol_version != PROTOCOL_VERSION:
        found.append(f"protocol {heartbeat.protocol_version}")
    if heartbeat.command_schema_version != COMMAND_SCHEMA_VERSION:
        found.append(f"command schema {heartbeat.command_schema_version}")
    if panel not in TESTED_PANEL_VERSIONS:
        found.append(f"panel version {panel or 'unknown'}")
    warnings: list[str] = []
    if found:
        message = (f"The Premiere panel is untested ({', '.join(found)}). Tested: protocol {PROTOCOL_VERSION}, "
                   f"command schema {COMMAND_SCHEMA_VERSION}, panel {', '.join(TESTED_PANEL_VERSIONS)}.")
        _gate(warnings, "untested_bridge", message, write=write, allow_untested=allow_untested,
              hint=f"Use {OVERRIDE} to go on anyway.")
    return warnings


def _path(entry: dict) -> str:
    return str(entry.get("path") or "")


def project_file_name(entry: dict) -> str:
    """The file name of a project, or its name when the state has no path for it."""
    path = _path(entry)
    return os.path.basename(path) if path else str(entry.get("name") or "")


def _same(a: str | None, b: str) -> bool:
    return bool(a) and bool(b) and os.path.normpath(a) == os.path.normpath(b)


def active_project(projects: list) -> dict | None:
    """The project marked active; the only open project when none is marked; else None.

    An entry that is not an object is never the active project.
    """
    for entry in projects:
        if isinstance(entry, dict) and entry.get("active"):
            return entry
    return projects[0] if len(projects) == 1 and isinstance(projects[0], dict) else None


def check_state(state: dict, *, write: bool, project: str | None = None, any_project: bool = False,
                allow_untested: bool = False) -> list[str]:
    """Is Premiere a tested version, and is exactly the expected Claude working copy open?"""
    warnings: list[str] = []
    version = state.get("version")
    if not (isinstance(version, str) and version.startswith(TESTED_PREMIERE_PREFIXES)):
        shown = version if isinstance(version, str) and version else "unknown"
        _gate(warnings, "untested_premiere", f"Live writes support Premiere 27.0 only. Running: {shown}.",
              write=write, allow_untested=allow_untested)

    projects = state.get("projects") or []
    if not projects:
        if write:
            raise CliError("no_project", "No project is open in Premiere.")
        return warnings

    active = active_project(projects)
    active_path = _path(active) if active else ""
    problem = None
    if len(projects) > 1 and (project is None or active is None):
        if active is None:
            text = "More than one project is open and none is the active one."
        else:
            text = "More than one project is open."
        problem = ("two_projects", f"{text} Give the path of the active project with --project.")
    elif project is not None and not _same(project, active_path):
        problem = ("project_mismatch", f"The active project is {active_path or project_file_name(active)}, "
                                       f"not {project}.")
    if problem:
        if write:
            raise CliError(*problem)
        warnings.append(problem[1])
    if not write:
        return warnings

    name = project_file_name(active)
    if "claude" not in name.lower():
        if any_project and _same(project, active_path):
            warnings.append(f"project guard overridden: {name} is not a Claude working copy.")
        else:
            raise CliError("project_guard", f"This is not a Claude working copy: {name}.")
    return warnings


def check_recovery(bridge_dir: Path, *, write: bool) -> list[str]:
    """A recovery marker in the bridge folder means a command's outcome is unknown: no write until it is checked."""
    if read_recovery(bridge_dir) is None:
        return []
    message = "A recovery is open. Run: cli-anything-premiere recovery show"
    if write:
        raise CliError("recovery_required", message)
    return [message]
