"""The parts of the real Adobe Premiere Pro install this CLI looks at.

Premiere Pro has no headless mode and no command line for project work. This CLI
reads the native project file (.prproj) directly and never starts or drives the
app. The install is only inspected to report which Premiere wrote the files.
"""
from __future__ import annotations

import os
import plistlib
from pathlib import Path

from cli_anything.premiere.core.prproj import SMOKE_READ_PROJECT_VERSIONS, TESTED_PROJECT_VERSIONS


def find_app() -> Path | None:
    """The newest Premiere Pro in /Applications, or the app at PREMIERE_APP."""
    override = os.environ.get("PREMIERE_APP")
    if override:
        app = Path(override)
        return app if (app / "Contents/Info.plist").is_file() else None
    apps = sorted(Path("/Applications").glob("Adobe Premiere Pro */Adobe Premiere Pro *.app"))
    return apps[-1] if apps else None


def version(app: Path) -> str | None:
    try:
        with open(app / "Contents/Info.plist", "rb") as fh:
            return plistlib.load(fh).get("CFBundleShortVersionString")
    except (OSError, plistlib.InvalidFileException):
        return None


def status() -> dict:
    app = find_app()
    return {
        "app": str(app) if app else None,
        "version": version(app) if app else None,
        "tested_project_formats": {str(k): v for k, v in TESTED_PROJECT_VERSIONS.items()},
        "smoke_read_project_formats": {str(k): v for k, v in SMOKE_READ_PROJECT_VERSIONS.items()},
        "writes_projects": False,
    }
