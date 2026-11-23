"""Unit tests for the screen route to the mask tools. No test runs osascript, screencapture or a real sleep."""
from __future__ import annotations

from pathlib import Path

import pytest

from cli_anything.premiere.core import ui_mask
from cli_anything.premiere.errors import CliError

PROCESS = "Adobe Premiere Pro 2027"
cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


class Screen:
    """A fake Mac: records the scripts, answers the front-app question, and writes a made-up capture."""

    def __init__(self, front: str = PROCESS, shot=None) -> None:
        self.front, self.shot, self.scripts, self.pointer_args, self.slept = front, shot, [], [], 0.0

    def run(self, source: str) -> str:
        self.scripts.append(source)
        return self.front if "frontmost" in source else ""

    def run_js(self, source: str, arg: str) -> str:
        self.pointer_args.append(arg)
        return "1512"

    def capture(self, path: Path) -> None:
        cv2.imwrite(str(path), self.shot)

    def sleep(self, seconds: float) -> None:
        self.slept += seconds


def _frame() -> "np.ndarray":
    rng = np.random.default_rng(7)
    return (rng.random((270, 480, 3)) * 255).astype(np.uint8)


def _shot_with(frame, x: int, y: int, width: int) -> "np.ndarray":
    shot = np.full((1964, 3024, 3), 30, np.uint8)
    h = round(width * frame.shape[0] / frame.shape[1])
    shot[y:y + h, x:x + width] = cv2.resize(frame, (width, h), interpolation=cv2.INTER_AREA)
    return shot


def test_find_picture_finds_a_scaled_frame(tmp_path):
    frame = cv2.resize(_frame(), (1920, 1080), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(tmp_path / "f.png"), frame)
    cv2.imwrite(str(tmp_path / "s.png"), _shot_with(frame, 960, 344, 1400))
    box = ui_mask.find_picture(tmp_path / "s.png", tmp_path / "f.png")
    assert box["score"] > 0.8 and abs(box["x"] - 960) <= 6 and abs(box["y"] - 344) <= 6 and abs(box["w"] - 1400) <= 10


def test_find_tracker_finds_the_buttons(tmp_path):
    tpl = cv2.imread(str(ui_mask.TRACKER_ASSET))
    shot = np.full((1964, 3024, 3), 35, np.uint8)
    shot[448:448 + tpl.shape[0], 378:378 + tpl.shape[1]] = tpl
    cv2.imwrite(str(tmp_path / "s.png"), shot)
    box = ui_mask.find_tracker(tmp_path / "s.png")
    assert (box["x"], box["y"]) == (378, 448) and box["score"] > 0.99


def test_not_front_sends_no_event():
    screen = Screen(front="Code")
    with pytest.raises(CliError) as err:
        ui_mask.bring_to_front(PROCESS, screen.run)
    assert err.value.code == "not_front"


def test_object_mask_clicks_inside_the_found_frame_and_reports_no_data(tmp_path):
    frame = cv2.resize(_frame(), (1920, 1080), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(tmp_path / "f.png"), frame)
    screen = Screen(shot=_shot_with(frame, 960, 344, 1400))
    project = tmp_path / "Test Claude.prproj"
    result = ui_mask.object_mask(PROCESS, project, tmp_path / "f.png", (0.5, 0.25), run=screen.run, run_js=screen.run_js,
                                 capture=screen.capture, sleep=screen.sleep, display_points=1512)
    x, y = result["clicked_points"]
    assert abs(x - (960 + 700) / 2) < 6 and abs(y - (344 + 0.25 * 787.5) / 2) < 6
    assert any("key code 31 using {command down, option down}" in s for s in screen.scripts)
    assert result["made"] is False and '"click": [' in screen.pointer_args[-1]


def test_object_mask_refuses_when_the_frame_is_not_on_the_screen(tmp_path):
    frame = cv2.resize(_frame(), (1920, 1080), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(tmp_path / "f.png"), frame)
    screen = Screen(shot=np.full((1964, 3024, 3), 30, np.uint8))
    with pytest.raises(CliError) as err:
        ui_mask.object_mask(PROCESS, tmp_path / "Test Claude.prproj", tmp_path / "f.png", (0.5, 0.5), run=screen.run,
                            run_js=screen.run_js, capture=screen.capture, sleep=screen.sleep, display_points=1512)
    assert err.value.code == "picture_not_found" and screen.pointer_args == []
    assert not any("key code" in s for s in screen.scripts)


def test_track_waits_until_the_data_stops_growing(tmp_path):
    tpl = cv2.imread(str(ui_mask.TRACKER_ASSET))
    shot = np.full((1964, 3024, 3), 35, np.uint8)
    shot[448:448 + tpl.shape[0], 378:378 + tpl.shape[1]] = tpl
    screen = Screen(shot=shot)
    project = tmp_path / "Test Claude.prproj"
    folder = tmp_path / "Test Claude Masks"
    folder.mkdir()
    (folder / "a.prmf").write_bytes(b"x" * 10)
    ticks = {"n": 0}

    def sleep(seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] in (2, 3):
            (folder / "b.prmf").write_bytes(b"y" * 100 * ticks["n"])

    result = ui_mask.track(PROCESS, project, "both", run=screen.run, run_js=screen.run_js, capture=screen.capture, sleep=sleep,
                           display_points=1512)
    assert result["tracked"] is True and result["changed_files"] == {"b.prmf": 300}
    x, y = result["clicked_points"]
    assert abs(x - (378 + 0.408 * tpl.shape[1]) / 2) < 1 and abs(y - (448 + tpl.shape[0] / 2) / 2) < 1


def test_track_refuses_when_the_buttons_are_not_on_the_screen(tmp_path):
    screen = Screen(shot=np.full((1964, 3024, 3), 35, np.uint8))
    with pytest.raises(CliError) as err:
        ui_mask.track(PROCESS, tmp_path / "Test Claude.prproj", "both", run=screen.run, run_js=screen.run_js,
                      capture=screen.capture, sleep=screen.sleep, display_points=1512)
    assert err.value.code == "tracker_not_found" and screen.pointer_args == []


def test_track_reports_not_started_when_nothing_grows(tmp_path):
    tpl = cv2.imread(str(ui_mask.TRACKER_ASSET))
    shot = np.full((1964, 3024, 3), 35, np.uint8)
    shot[448:448 + tpl.shape[0], 378:378 + tpl.shape[1]] = tpl
    screen = Screen(shot=shot)
    with pytest.raises(CliError) as err:
        ui_mask.track(PROCESS, tmp_path / "Test Claude.prproj", "forward", run=screen.run, run_js=screen.run_js,
                      capture=screen.capture, sleep=screen.sleep, display_points=1512)
    assert err.value.code == "not_started"


def test_track_by_key_presses_the_track_mask_key_and_takes_no_capture(tmp_path):
    screen = Screen(shot=None)
    project = tmp_path / "Test Claude.prproj"
    folder = tmp_path / "Test Claude Masks"
    folder.mkdir()
    ticks = {"n": 0}

    def sleep(seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] == 2:
            (folder / "b.prmf").write_bytes(b"y" * 50)

    result = ui_mask.track(PROCESS, project, "both", by="key", run=screen.run, run_js=screen.run_js, capture=screen.capture, sleep=sleep)
    assert result["tracked"] is True and result["by"] == "key" and result["clicked_points"] is None
    assert any("key code 17 using {control down, option down, command down}" in s for s in screen.scripts)
    assert screen.pointer_args == []


def test_track_by_key_refuses_one_direction(tmp_path):
    screen = Screen()
    with pytest.raises(CliError) as err:
        ui_mask.track(PROCESS, tmp_path / "Test Claude.prproj", "forward", by="key", run=screen.run, run_js=screen.run_js,
                      capture=screen.capture, sleep=screen.sleep)
    assert err.value.code == "bad_direction" and screen.scripts == []


def test_ellipse_drags_from_the_top_left_to_the_bottom_right_of_the_grown_box(tmp_path):
    frame = cv2.resize(_frame(), (1920, 1080), interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(tmp_path / "f.png"), frame)
    screen = Screen(shot=_shot_with(frame, 960, 344, 1400))
    result = ui_mask.ellipse_mask(PROCESS, tmp_path / "f.png", (0.5, 0.2, 0.1, 0.2), grow=(0.0, 0.1), run=screen.run, run_js=screen.run_js,
                                  capture=screen.capture, sleep=screen.sleep, display_points=1512)
    x0, y0, x1, y1 = result["dragged_points"]
    assert abs(x0 - (960 + 0.5 * 1400) / 2) < 6 and abs(x1 - (960 + 0.6 * 1400) / 2) < 6
    assert abs(y0 - (344 + 0.18 * 787.5) / 2) < 6 and abs(y1 - (344 + 0.42 * 787.5) / 2) < 6
    assert any("key code 14 using {control down, option down, command down}" in s for s in screen.scripts)
    assert '"from": [' in screen.pointer_args[-1] and '"to": [' in screen.pointer_args[-1]


def test_ellipse_refuses_a_box_outside_the_frame(tmp_path):
    screen = Screen()
    with pytest.raises(CliError) as err:
        ui_mask.ellipse_mask(PROCESS, tmp_path / "f.png", (0.95, 0.2, 0.2, 0.2), run=screen.run, run_js=screen.run_js,
                             capture=screen.capture, sleep=screen.sleep, display_points=1512)
    assert err.value.code == "bad_box" and screen.scripts == []


def _shot_with_feather(rows):
    tpl = cv2.imread(str(ui_mask.FEATHER_ASSET))
    shot = np.full((1964, 3024, 3), 35, np.uint8)
    for x, y in rows:
        shot[y:y + tpl.shape[0], x:x + tpl.shape[1]] = tpl
    return shot


def test_feather_clicks_the_value_and_types_the_number():
    screen = Screen(shot=_shot_with_feather([(186, 572)]))
    result = ui_mask.mask_feather(PROCESS, 50, run=screen.run, run_js=screen.run_js, capture=screen.capture, sleep=screen.sleep,
                                  display_points=1512)
    assert result["typed"] == "50" and result["clicked_points"] == [(186 + 207) / 2, (572 + 20) / 2]
    assert any('keystroke "50"' in s for s in screen.scripts) and any(s.endswith("key code 36") for s in screen.scripts)


def test_feather_refuses_when_two_rows_show():
    screen = Screen(shot=_shot_with_feather([(186, 572), (186, 900)]))
    with pytest.raises(CliError) as err:
        ui_mask.mask_feather(PROCESS, 50, run=screen.run, run_js=screen.run_js, capture=screen.capture, sleep=screen.sleep,
                             display_points=1512)
    assert err.value.code == "feather_not_found" and screen.pointer_args == []
    assert not any("keystroke" in s for s in screen.scripts)


def test_feather_refuses_a_value_out_of_range():
    screen = Screen()
    with pytest.raises(CliError) as err:
        ui_mask.mask_feather(PROCESS, 5000, run=screen.run, run_js=screen.run_js, capture=screen.capture, sleep=screen.sleep)
    assert err.value.code == "bad_value" and screen.scripts == []


def test_track_done_by_playhead_waits_until_it_stops(tmp_path):
    screen = Screen()
    positions = iter(["10", "20", "30"] + ["30"] * 20)
    result = ui_mask.track(PROCESS, tmp_path / "Test Claude.prproj", "both", by="key", run=screen.run, run_js=screen.run_js,
                           capture=screen.capture, sleep=screen.sleep, playhead=lambda: next(positions))
    assert result["tracked"] is True and result["done_by"] == "playhead" and result["playhead"] == "30"


def test_track_done_by_playhead_reports_not_started_when_it_never_moves(tmp_path):
    screen = Screen()
    with pytest.raises(CliError) as err:
        ui_mask.track(PROCESS, tmp_path / "Test Claude.prproj", "both", by="key", run=screen.run, run_js=screen.run_js,
                      capture=screen.capture, sleep=screen.sleep, playhead=lambda: "500")
    assert err.value.code == "not_started"
