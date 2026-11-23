"""The screen route to the Premiere 27 mask tools: Object Mask by a click in the Program Monitor, and mask tracking.

Premiere gives scripts no call for these tools and hides its panels from Accessibility, so this module does what a
person does: it presses the tool key, moves and clicks the pointer (CoreGraphics events from osascript's JavaScript
mode), and finds things on a screen capture by picture matching. Measured on Premiere 27.0.0, macOS, one display at
2 pixels per point (docs/2026-11-22-premiere27-mask-ui-gates.md).

Rules built in: Premiere must be the front app right before every event; nothing is clicked at a remembered place, only
at a place found on a capture taken in the same call; the result is read from the mask data folder, not from the eye.
A dialog is never clicked: the first mask of a project opens one, and only the owner clicks it.

Picture matching needs the extra packages numpy and opencv-python-headless (`pip install cli-anything-premiere[screen]`).
Every function that runs a program or waits takes the runner as an argument, so a test can replace it.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

from cli_anything.premiere.core import ui
from cli_anything.premiere.errors import CliError

KEY_O, KEY_V = 31, 9                                  # virtual key codes: O (Object Mask tool with Cmd+Opt), V (pointer tool)
TRACKER_ASSET = Path(__file__).parent / "assets" / "tracker_buttons_2x.png"
TRACKER_BUTTONS = {"back1": 0.085, "backward": 0.245, "both": 0.408, "forward": 0.568, "forward1": 0.728}   # centre, share of width
PICTURE_MIN_SCORE, TRACKER_MIN_SCORE = 0.80, 0.95
SCREENCAPTURE = "/usr/sbin/screencapture"

_FRONT = ('tell application "System Events"\n\tset frontmost of process <PROCESS> to true\n\tdelay 0.6\n'
          '\treturn name of first process whose frontmost is true\nend tell')
_KEY = 'tell application "System Events" to key code <CODE> using {<MODS>}'
_KEY_PLAIN = 'tell application "System Events" to key code <CODE>'
_POINTER_JS = '''ObjC.import('CoreGraphics');
function run(argv) {
  var a = JSON.parse(argv[0]), tap = $.kCGHIDEventTap;
  for (var i = 0; i < a.moves.length; i++) {
    var p = $.CGPointMake(a.moves[i][0], a.moves[i][1]);
    $.CGEventPost(tap, $.CGEventCreateMouseEvent(null, $.kCGEventMouseMoved, p, 0)); delay(a.step);
  }
  delay(a.wait);
  if (a.click) {
    var q = $.CGPointMake(a.click[0], a.click[1]);
    $.CGEventPost(tap, $.CGEventCreateMouseEvent(null, $.kCGEventLeftMouseDown, q, 0)); delay(0.08);
    $.CGEventPost(tap, $.CGEventCreateMouseEvent(null, $.kCGEventLeftMouseUp, q, 0));
  }
  return String($.CGDisplayPixelsWide($.CGMainDisplayID()));
}'''


def run_jxa(source: str, arg: str) -> str:
    """Run one JavaScript-for-Automation script with one argument and return what it prints."""
    try:
        done = subprocess.run([ui.OSASCRIPT, "-l", "JavaScript", "-e", source, arg], capture_output=True, encoding="utf-8",
                              errors="replace", timeout=ui.OSASCRIPT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise CliError("ui_failed", f"osascript did not answer within {ui.OSASCRIPT_TIMEOUT_S} seconds.") from None
    except OSError as exc:
        raise CliError("ui_failed", f"Cannot run osascript: {exc}.") from None
    if done.returncode != 0:
        raise CliError("ui_failed", (done.stderr or "").strip() or f"osascript failed with status {done.returncode}.")
    return done.stdout.strip()


def run_capture(path: Path) -> None:
    """Write a capture of the main display to `path` (PNG). Needs the Screen Recording permission."""
    try:
        done = subprocess.run([SCREENCAPTURE, "-x", str(path)], capture_output=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise CliError("ui_failed", f"Cannot capture the screen: {exc}.") from None
    if done.returncode != 0 or not Path(path).is_file() or Path(path).stat().st_size == 0:
        raise CliError("no_screen_capture", "The screen capture failed. Give the terminal the Screen Recording permission: "
                                            "System Settings > Privacy & Security > Screen Recording.")


def _cv():
    try:
        import cv2
        import numpy
    except ImportError:
        raise CliError("no_picture_matching", "Picture matching needs numpy and opencv-python-headless in the environment of this "
                                              "CLI (the extra 'screen').") from None
    return cv2, numpy


def bring_to_front(process_name: str, run: Callable[[str], str] | None = None) -> None:
    """Make Premiere the front app and check it. Raises not_front: then no event may be sent."""
    run = ui.run_osascript if run is None else run
    front = run(_FRONT.replace("<PROCESS>", json.dumps(process_name))).strip()
    if front != process_name:
        raise CliError("not_front", f"Premiere is not the front app ({front!r} is). Nothing was pressed or clicked.")


def press(code: int, modifiers: tuple[str, ...] = (), run: Callable[[str], str] | None = None) -> None:
    """Press one key by its key code. `keystroke` with a letter did not reach the tool commands; key codes do."""
    run = ui.run_osascript if run is None else run
    if modifiers:
        run(_KEY.replace("<CODE>", str(int(code))).replace("<MODS>", ", ".join(f"{m} down" for m in modifiers)))
    else:
        run(_KEY_PLAIN.replace("<CODE>", str(int(code))))


def pointer(moves: list[tuple[float, float]], click: tuple[float, float] | None, wait_s: float,
            run: Callable[[str, str], str] | None = None) -> int:
    """Move the pointer through `moves` (points), wait, then click at `click` when given. Returns the display width in points."""
    run = run_jxa if run is None else run
    arg = json.dumps({"moves": [list(m) for m in moves], "click": list(click) if click else None, "wait": wait_s, "step": 0.25})
    answer = run(_POINTER_JS, arg)
    return int(answer) if answer.isdecimal() else 0


def find_picture(capture_png: Path, frame_png: Path) -> dict:
    """Where the frame shows on the capture: {"x", "y", "w", "h", "score"} in capture pixels. Tries many sizes."""
    cv2, np = _cv()
    shot, frame = cv2.imread(str(capture_png), cv2.IMREAD_GRAYSCALE), cv2.imread(str(frame_png), cv2.IMREAD_GRAYSCALE)
    if shot is None or frame is None:
        raise CliError("ui_failed", "Cannot read the capture or the frame picture.")
    k = 4                                                     # search on a quarter-size capture, then refine at full size
    small = cv2.resize(shot, (shot.shape[1] // k, shot.shape[0] // k), interpolation=cv2.INTER_AREA)
    best = (-1.0, 0, 0, 0)
    for width in range(max(40, small.shape[1] // 8), small.shape[1], 4):
        height = round(width * frame.shape[0] / frame.shape[1])
        if height >= small.shape[0]:
            break
        tpl = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(small, tpl, cv2.TM_CCOEFF_NORMED))
        if score > best[0]:
            best = (float(score), loc[0], loc[1], width)
    score, x, y, width = best
    refined = (score, x * k, y * k, width * k)
    for w in range(max(16, width * k - 2 * k), width * k + 2 * k + 1):
        h = round(w * frame.shape[0] / frame.shape[1])
        x0, y0 = max(0, x * k - 3 * k), max(0, y * k - 3 * k)
        region = shot[y0:y0 + h + 6 * k, x0:x0 + w + 6 * k]
        if region.shape[0] < h or region.shape[1] < w:
            continue
        tpl = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        _, s, _, loc = cv2.minMaxLoc(cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED))
        if s > refined[0]:
            refined = (float(s), x0 + loc[0], y0 + loc[1], w)
    score, x, y, w = refined
    return {"x": int(x), "y": int(y), "w": int(w), "h": int(round(w * frame.shape[0] / frame.shape[1])), "score": round(score, 3)}


def find_tracker(capture_png: Path, template_png: Path = TRACKER_ASSET) -> dict:
    """Where the five Tracker buttons show on the capture: {"x", "y", "w", "h", "score"} in capture pixels."""
    cv2, _ = _cv()
    shot, tpl = cv2.imread(str(capture_png)), cv2.imread(str(template_png))
    if shot is None or tpl is None:
        raise CliError("ui_failed", "Cannot read the capture or the Tracker picture.")
    _, score, _, loc = cv2.minMaxLoc(cv2.matchTemplate(shot, tpl, cv2.TM_CCOEFF_NORMED))
    return {"x": int(loc[0]), "y": int(loc[1]), "w": int(tpl.shape[1]), "h": int(tpl.shape[0]), "score": round(float(score), 3)}


def mask_data(project_path: Path) -> dict[str, int]:
    """The mask data files of a project: {file name: bytes}. Empty when the folder does not exist."""
    folder = Path(project_path).with_name(Path(project_path).stem + " Masks")
    return {p.name: p.stat().st_size for p in sorted(folder.glob("*.prmf"))} if folder.is_dir() else {}


def _scale(capture_png: Path, display_points: int) -> float:
    cv2, _ = _cv()
    shot = cv2.imread(str(capture_png), cv2.IMREAD_GRAYSCALE)
    return shot.shape[1] / display_points if display_points else 2.0


def object_mask(process_name: str, project_path: Path, frame_png: Path, at: tuple[float, float], *, hover_s: float = 3.0,
                settle_s: float = 6.0, run: Callable[[str], str] | None = None, run_js: Callable[[str, str], str] | None = None,
                capture: Callable[[Path], None] | None = None, sleep: Callable[[float], None] | None = None,
                display_points: int | None = None) -> dict:
    """Make an Object Mask on the selected clip: tool key, hover at `at` (x, y as shares of the frame), click.

    `frame_png` is the frame the Program Monitor shows now (export it first); it is found on a screen capture, so the
    monitor can have any size and place. The clip must be selected and the playhead on that frame. Returns the place
    that was clicked and the mask data files that are new or changed. When no file changed, a dialog may be open
    (the first mask of a project): the caller must stop and tell the owner.
    """
    if not (0.0 <= at[0] <= 1.0 and 0.0 <= at[1] <= 1.0):
        raise CliError("bad_point", "The point must be two shares of the frame between 0 and 1, as x,y.")
    capture, sleep = run_capture if capture is None else capture, time.sleep if sleep is None else sleep
    before = mask_data(project_path)
    with tempfile.TemporaryDirectory() as tmp:
        shot = Path(tmp) / "screen.png"
        bring_to_front(process_name, run)
        capture(shot)
        box = find_picture(shot, frame_png)
        if box["score"] < PICTURE_MIN_SCORE:
            raise CliError("picture_not_found", f"The frame was not found in the Program Monitor (best match {box['score']}). "
                                                "Nothing was pressed or clicked.")
        points = display_points if display_points is not None else pointer([], None, 0.0, run_js)
        scale = _scale(shot, points)
        x, y = (box["x"] + at[0] * box["w"]) / scale, (box["y"] + at[1] * box["h"]) / scale
        bring_to_front(process_name, run)
        press(KEY_O, ("command", "option"), run)
        sleep(1.0)
        pointer([(x - 6, y - 3), (x - 3, y - 1), (x - 1, y), (x, y)], (x, y), hover_s, run_js)
    sleep(settle_s)
    after = mask_data(project_path)
    changed = sorted(n for n, size in after.items() if before.get(n) != size)
    return {"clicked_points": [round(x, 1), round(y, 1)], "picture": box, "scale": round(scale, 3), "new_or_changed_files": changed,
            "made": bool(changed)}


def track(process_name: str, project_path: Path, direction: str = "both", *, quiet_s: float = 4.0, limit_s: float = 300.0,
          run: Callable[[str], str] | None = None, run_js: Callable[[str, str], str] | None = None,
          capture: Callable[[Path], None] | None = None, sleep: Callable[[float], None] | None = None,
          display_points: int | None = None) -> dict:
    """Click one Tracker button of the selected mask and wait until the mask data stops growing.

    The buttons must show in Effect Controls (the mask is selected and the panel is scrolled to it). They are found on
    a screen capture; a match under 0.95 clicks nothing. Done means: a mask data file grew, then did not change for
    `quiet_s` seconds. Nothing grew within 15 seconds: not_started.
    """
    if direction not in TRACKER_BUTTONS:
        raise CliError("bad_direction", f"{direction!r} is not a direction. Use one of: {', '.join(TRACKER_BUTTONS)}.")
    capture, sleep = run_capture if capture is None else capture, time.sleep if sleep is None else sleep
    before = mask_data(project_path)
    with tempfile.TemporaryDirectory() as tmp:
        shot = Path(tmp) / "screen.png"
        bring_to_front(process_name, run)
        capture(shot)
        box = find_tracker(shot)
        if box["score"] < TRACKER_MIN_SCORE:
            raise CliError("tracker_not_found", f"The Tracker buttons were not found on the screen (best match {box['score']}). "
                                                "Select the mask and scroll Effect Controls to it. Nothing was clicked.")
        points = display_points if display_points is not None else pointer([], None, 0.0, run_js)
        scale = _scale(shot, points)
        x, y = (box["x"] + TRACKER_BUTTONS[direction] * box["w"]) / scale, (box["y"] + box["h"] / 2) / scale
        bring_to_front(process_name, run)
        pointer([(x - 2, y), (x, y)], (x, y), 0.4, run_js)
    waited, grew, last, still = 0.0, False, before, 0.0
    while waited < limit_s:
        sleep(1.0)
        waited += 1.0
        now = mask_data(project_path)
        if now != last:
            grew, last, still = True, now, 0.0
        else:
            still += 1.0
        if grew and still >= quiet_s:
            break
        if not grew and waited >= 15.0:
            raise CliError("not_started", "The Tracker button was clicked but no mask data changed within 15 seconds. "
                                          "Look at Premiere: a dialog may be open, or no mask is selected.")
    else:
        raise CliError("tracking_slow", f"The mask data still changed after {int(limit_s)} seconds. Tracking may still run.")
    changed = {n: size for n, size in last.items() if before.get(n) != size}
    return {"direction": direction, "clicked_points": [round(x, 1), round(y, 1)], "match": box["score"], "seconds": waited,
            "changed_files": changed, "tracked": True}
