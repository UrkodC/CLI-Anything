"""The screen route: read and switch the Window > Mode menu of the running Premiere.

It drives the menu through osascript and System Events (Accessibility). Nothing here talks to the
bridge folder. The functions that run osascript or wait take the runner and the sleeper as
arguments, and look the real ones up when they are called, so a test can replace them.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Callable

from cli_anything.premiere.errors import CliError

MODES = ("import", "edit", "color", "export")          # the menu titles are these words capitalized

PS_COMMAND = ["/bin/ps", "-axo", "pid=,comm="]
PS_TIMEOUT_S = 10
OSASCRIPT = "/usr/bin/osascript"
OSASCRIPT_TIMEOUT_S = 30
ACCESSIBILITY_MARKERS = ("assistive access", "-1719", "-25211")
ACCESSIBILITY_MESSAGE = ("Give the terminal Accessibility permission: "
                         "System Settings > Privacy & Security > Accessibility.")

# The program path ends with .../Adobe Premiere Pro <year>.app/Contents/MacOS/Adobe Premiere Pro <year>.
_PREMIERE_PATH = re.compile(r"(?P<app_path>.*/Adobe Premiere Pro (?P<year>[0-9]{4})\.app)"
                            r"/Contents/MacOS/Adobe Premiere Pro (?P=year)")

_GET_SCRIPT = '''tell application "System Events"
\ttell process <PROCESS>
\t\tset mm to menu 1 of menu item "Mode" of menu 1 of menu bar item "Window" of menu bar 1
\t\trepeat with n in {"Import", "Edit", "Color", "Export"}
\t\t\tset mc to value of attribute "AXMenuItemMarkChar" of menu item (n as text) of mm
\t\t\tif mc is not missing value then return (n as text)
\t\tend repeat
\tend tell
end tell
return "none"'''

_CLICK_SCRIPT = ('tell application "System Events" to tell process <PROCESS> to click menu item <TITLE> '
                 'of menu 1 of menu item "Mode" of menu 1 of menu bar item "Window" of menu bar 1')


def running_premiere(ps_output: str | None = None) -> dict | None:
    """The Premiere Pro that runs now, as {"pid", "process_name", "app_path"}, or None when none runs.

    `ps_output` is the text of `ps -axo pid=,comm=`; None runs ps. Two lines for the same app count as
    one. Two different app paths are two Premieres, which this CLI cannot tell apart: two_premieres.
    """
    text = _read_ps() if ps_output is None else ps_output
    found: dict[str, dict] = {}                 # app_path -> the first matching process
    for line in text.splitlines():
        parts = line.strip().split(None, 1)     # the path may hold spaces: only the pid is split off
        if len(parts) != 2 or not parts[0].isdecimal():
            continue
        match = _PREMIERE_PATH.fullmatch(parts[1].strip())
        if match is None or match["app_path"] in found:
            continue
        found[match["app_path"]] = {"pid": int(parts[0]), "process_name": f"Adobe Premiere Pro {match['year']}",
                                    "app_path": match["app_path"]}
    if len(found) > 1:
        names = " and ".join(f"{app['process_name']} ({path})" for path, app in found.items())
        raise CliError("two_premieres", f"Two Premiere Pro apps are running: {names}. Quit one and try again.")
    return next(iter(found.values()), None)


def _read_ps() -> str:
    try:
        done = subprocess.run(PS_COMMAND, capture_output=True, encoding="utf-8", errors="replace",
                              timeout=PS_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise CliError("ui_failed", f"The list of running programs did not come back within {PS_TIMEOUT_S} seconds.") from None
    except OSError as exc:
        raise CliError("ui_failed", f"Cannot list the running programs: {exc}.") from None
    if done.returncode != 0:
        raise CliError("ui_failed", (done.stderr or "").strip() or f"ps failed with status {done.returncode}.")
    return done.stdout


def run_osascript(source: str) -> str:
    """Run one AppleScript and return what it prints. Raises no_accessibility or ui_failed."""
    try:
        done = subprocess.run([OSASCRIPT, "-e", source], capture_output=True, encoding="utf-8", errors="replace",
                              timeout=OSASCRIPT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise CliError("ui_failed", f"osascript did not answer within {OSASCRIPT_TIMEOUT_S} seconds.") from None
    except OSError as exc:
        raise CliError("ui_failed", f"Cannot run osascript: {exc}.") from None
    if done.returncode != 0:
        stderr = (done.stderr or "").strip()
        if any(marker in stderr.lower() for marker in ACCESSIBILITY_MARKERS):
            # The osascript text comes too: a wrong menu path must not look like a missing permission.
            raise CliError("no_accessibility", f"{ACCESSIBILITY_MESSAGE} osascript said: {stderr}")
        raise CliError("ui_failed", stderr or f"osascript failed with status {done.returncode}.")
    return done.stdout


def mode_get(process_name: str, run: Callable[[str], str] | None = None) -> str:
    """The checked mode of Window > Mode, in lower case. Raises ui_failed when no mode is checked."""
    run = run_osascript if run is None else run
    answer = run(_GET_SCRIPT.replace("<PROCESS>", json.dumps(process_name))).strip().lower()
    if answer not in MODES:
        if answer == "none":
            raise CliError("ui_failed", "No mode is checked in the Window > Mode menu of Premiere.")
        raise CliError("ui_failed", f"The Window > Mode menu gave an answer that is not a mode: {answer!r}.")
    return answer


def mode_set(process_name: str, mode: str, run: Callable[[str], str] | None = None,
             sleep: Callable[[float], None] | None = None, watch_s: int = 5) -> dict:
    """Click Window > Mode > MODE and watch that Premiere keeps it. Returns {"mode", "reverted_once"}.

    After a click the mode is read once a second for watch_s seconds. If a reading is not MODE, the item
    is clicked once more and watched again. A second wrong reading is mode_reverted. At most two clicks.
    """
    if mode not in MODES:
        raise CliError("bad_mode", f"{mode!r} is not a mode. Use one of: {', '.join(MODES)}.")
    run = run_osascript if run is None else run
    sleep = time.sleep if sleep is None else sleep
    click = _CLICK_SCRIPT.replace("<PROCESS>", json.dumps(process_name)).replace("<TITLE>", json.dumps(mode.capitalize()))
    run(click)
    seen = _watch(process_name, mode, run, sleep, watch_s)
    if seen is None:
        return {"mode": mode, "reverted_once": False}
    run(click)
    seen = _watch(process_name, mode, run, sleep, watch_s)
    if seen is None:
        return {"mode": mode, "reverted_once": True}
    raise CliError("mode_reverted", f"Premiere went back to {seen} after the click, twice. "
                                    f"The mode is not {mode}. Nothing more was tried.")


def _watch(process_name: str, mode: str, run: Callable[[str], str], sleep: Callable[[float], None],
           watch_s: int) -> str | None:
    """Read the mode once a second for watch_s seconds. The first reading that is not `mode`, or None."""
    for _ in range(watch_s):
        sleep(1)                        # a reading right after a click can still show the old mode
        reading = mode_get(process_name, run)
        if reading != mode:
            return reading
    return None
