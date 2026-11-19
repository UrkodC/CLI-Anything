"""`live place`: place the shots of a placement plan on a sequence, frame-exact, and check each one.

The shot math is a port of the film session's planner (cinematic-story-edit, scripts/place_plan.py: rate,
floor_frame, resolve and calls_for), in this module's own code. For the plan of the v10 film it gives the same 50
calls, number for number (tests/data/shots_v10.json and place_v10.json).

A plan is {"fps": "24000/1001", "shots": [shot]}. Times are seconds unless the key ends in _f; tracks count from 0.
Per shot:
- item (or item_id): the project item, by node ID or by name (a name is resolved to a node ID before any write);
- the source in, one of: src_in (item seconds), src_in_f (item frame), cam_in_f with cam_fps (camera frame);
  interpreted (true: the sequence rate, or a rate) for an item interpreted for slow motion; item_fps (the item's
  frame grid, by default the interpreted rate, else cam_fps, else the sequence rate);
- the length, one of: frames (sequence frames), end_f or end (timeline), src_out (item seconds);
- the start, start_f or start; without it the shot follows the latest end on its track;
- track (video, default 0), audio_track (default 0), media_length, note, audio_only (true: the sound is kept and
  the picture, placed on `track` first, is removed again).
A key set to null counts as not given. Other keys pass, and the warnings name them.

See place_shots for the order of work. The rules for the active sequence and the two kinds of session are in
active.py (ActiveRun).
"""
from __future__ import annotations

import json
import math
from fractions import Fraction
from pathlib import Path

from cli_anything.premiere.core import jsx
from cli_anything.premiere.core.recipes import results, stops
from cli_anything.premiere.core.recipes.active import ActiveRun
from cli_anything.premiere.core.session import DEFAULT_HARD_LIMIT_S
from cli_anything.premiere.errors import CliError

EPS = 0.001                     # a mark just inside its frame (the planner's +0.001 s rule)
SEQ_FPS = "24000/1001"
TICKS_PER_SECOND = 254016000000
FPS_TOLERANCE = 0.001           # the plan's fps against the sequence's own rate (254016000000 / frame ticks)
SHOT_KEYS = ("name", "id", "item", "item_id", "src_in", "src_in_f", "cam_in_f", "cam_fps", "interpreted", "item_fps",
             "frames", "end_f", "end", "src_out", "start_f", "start", "track", "video_track", "audio_track",
             "audio_track_index", "media_length", "note", "audio_only")
NOT_SENT = "Nothing was sent."
STOPPED_PLACE = "Stopped by Ctrl-C or a stop signal. Check Premiere before another edit."
_NTSC = (24000 / 1001, 30000 / 1001, 60000 / 1001, 120000 / 1001)


# =====================================================================================================
# the shot math
# =====================================================================================================
def rate(value) -> float:
    """"24000/1001", "23.976", 23.976 or 59.94 -> frames per second. NTSC decimals become their exact fraction."""
    value = float(value) if isinstance(value, (int, float)) else float(Fraction(str(value)))
    for exact in _NTSC:
        if abs(value - exact) < 0.005:
            return exact
    return value


def floor_frame(seconds: float, fps: float) -> int:
    """The frame Premiere lands on for a mark at `seconds`: it floors to the media's frame grid."""
    return int(math.floor(seconds * fps + 1e-6))


def _plan_fault(why: str) -> CliError:
    return CliError("bad_plan", f"{why} {NOT_SENT}")


def _given(shot: dict, *keys: str):
    """The value of the first key that is given (present and not null), or None."""
    for key in keys:
        if shot.get(key) is not None:
            return shot[key]
    return None


def _finite(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _whole(value) -> bool:
    return _finite(value) and float(value).is_integer()


def _a_rate(value, what: str, who: str) -> float:
    if isinstance(value, bool):
        raise _plan_fault(f"{who}: {what} must be a frame rate, not {value!r}.")
    try:
        found = rate(value)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        raise _plan_fault(f"{who}: {what} must be a frame rate such as \"24000/1001\" or 23.976, not {value!r}.") \
            from None
    if not math.isfinite(found) or found <= 0:
        raise _plan_fault(f"{who}: {what} must be a frame rate above 0, not {value!r}.")
    return found


def _seconds_value(shot: dict, key: str, who: str) -> float:
    value = shot[key]
    if not _finite(value) or value < 0:
        raise _plan_fault(f"{who}: {key} must be a number of seconds of 0 or more, not {value!r}.")
    return float(value)


def _frame_value(shot: dict, key: str, who: str, *, low: int = 0) -> int:
    value = shot[key]
    if not _whole(value) or value < low:
        raise _plan_fault(f"{who}: {key} must be a whole number of {low} or more, not {value!r}.")
    return int(value)


def _track_value(shot: dict, keys: tuple[str, str], who: str) -> int:
    value = _given(shot, *keys)
    if value is None:
        return 0
    if not _whole(value) or value < 0:
        raise _plan_fault(f"{who}: {keys[0]} must be a whole number of 0 or more (tracks count from 0), not "
                          f"{value!r}. auto_empty is not taken here: name the track.")
    return int(value)


def _resolve(shot: dict, F: float, cursors: dict, k: int) -> dict:
    """One shot -> its frame-exact numbers (the planner's resolve). cursors: track -> (end frame, end seconds)."""
    name = _given(shot, "name", "id")
    who = f"shot {k}" + ("" if name is None else f" ({name})")
    name = str(name) if name is not None else f"shot {k + 1}"
    item = _given(shot, "item", "item_id")
    if item is None:
        raise _plan_fault(f"{who}: no item (the project item's node ID or name).")
    if not isinstance(item, str) or not item.strip():
        raise _plan_fault(f"{who}: item must be a non-empty text (a node ID or a name), not {item!r}.")
    track = _track_value(shot, ("track", "video_track"), who)
    audio = _track_value(shot, ("audio_track", "audio_track_index"), who)
    audio_only = shot.get("audio_only")
    if audio_only is not None and not isinstance(audio_only, bool):
        raise _plan_fault(f"{who}: audio_only must be true or false, not {audio_only!r}.")
    interp = shot.get("interpreted")
    interp_fps = None
    if interp is True:
        interp_fps = F
    elif interp not in (None, False):
        interp_fps = _a_rate(interp, "interpreted", who)
    cam_fps = _a_rate(shot["cam_fps"], "cam_fps", who) if shot.get("cam_fps") is not None else None
    item_fps = (_a_rate(shot["item_fps"], "item_fps", who) if shot.get("item_fps") is not None
                else (interp_fps or cam_fps or F))

    if shot.get("cam_in_f") is not None:
        div = interp_fps or cam_fps
        if div is None:
            raise _plan_fault(f"{who}: cam_in_f needs cam_fps (or interpreted).")
        src_req = _frame_value(shot, "cam_in_f", who) / div
    elif shot.get("src_in_f") is not None:
        src_req = _frame_value(shot, "src_in_f", who) / item_fps
    elif shot.get("src_in") is not None:
        src_req = _seconds_value(shot, "src_in", who)
    else:
        raise _plan_fault(f"{who}: no source in (src_in, src_in_f or cam_in_f).")
    in_f = floor_frame(src_req + EPS, item_fps)
    src_in = in_f / item_fps

    explicit = shot.get("start_f") is not None or shot.get("start") is not None
    if shot.get("start_f") is not None:
        start_f = _frame_value(shot, "start_f", who)
        start_t = start_f / F
    elif shot.get("start") is not None:
        start_f = int(round(_seconds_value(shot, "start", who) * F))
        start_t = start_f / F
    else:
        start_f, start_t = cursors.get(track, (0, 0.0))

    if shot.get("frames") is not None:
        frames = _frame_value(shot, "frames", who, low=1)
        end_t = (start_f + frames) / F
    elif shot.get("end_f") is not None:
        frames = _frame_value(shot, "end_f", who) - start_f
        end_t = int(shot["end_f"]) / F
    elif shot.get("end") is not None:
        frames = int(round(_seconds_value(shot, "end", who) * F)) - start_f
        end_t = (start_f + frames) / F
    elif shot.get("src_out") is not None:
        end_t = start_t + (_seconds_value(shot, "src_out", who) - src_req)    # cumulative: rounding never drifts
        frames = int(round(end_t * F)) - start_f
    else:
        raise _plan_fault(f"{who}: no length (frames, end_f, end or src_out).")
    if frames <= 0:
        raise _plan_fault(f"{who}: the length is {frames} frames. A shot needs one frame or more.")
    end_f = start_f + frames
    if not explicit or end_f >= cursors.get(track, (0, 0.0))[0]:
        cursors[track] = (end_f, end_t)
    media_length = shot.get("media_length")
    if media_length is not None and not _finite(media_length):
        raise _plan_fault(f"{who}: media_length must be a number of seconds, not {media_length!r}.")
    note = shot.get("note")
    return {"entry": k, "name": name, "item": item, "track": track, "audio_track": audio, "item_fps": item_fps,
            "src_in_req": round(src_req, 6), "in_f": in_f, "src_in": round(src_in, 6),
            "src_out": round(src_in + frames / F, 6), "start_f": start_f, "end_f": end_f, "frames": frames,
            "media_length": media_length, "note": "" if note is None else note, "audio_only": audio_only is True}


def resolve_shots(plan: dict) -> tuple[float, list[dict]]:
    """The plan, checked, as (F, rows): F is the sequence rate of the plan (its fps, by default 24000/1001), and
    a row holds the frame-exact numbers of one shot (entry, name, item, track, audio_track, item_fps, src_in_req,
    in_f, src_in, src_out, start_f, end_f, frames, media_length, note, audio_only). Sends nothing. Raises
    CliError("bad_plan") naming the shot."""
    if not isinstance(plan, dict):
        raise _plan_fault("The plan must be a JSON object with \"fps\" and \"shots\".")
    shots = plan.get("shots")
    if not isinstance(shots, list) or not shots:
        raise _plan_fault("The plan's \"shots\" must be a list with at least one shot.")
    F = _a_rate(plan.get("fps") if plan.get("fps") is not None else SEQ_FPS, "fps", "the plan")
    cursors: dict = {}
    rows = []
    for k, shot in enumerate(shots):
        if not isinstance(shot, dict):
            raise _plan_fault(f"shot {k}: it must be a JSON object.")
        try:
            rows.append(_resolve(shot, F, cursors, k))
        except CliError:
            raise
        except (ValueError, TypeError, ZeroDivisionError, OverflowError) as exc:
            raise _plan_fault(f"shot {k}: {exc}.") from None
    return F, rows


def calls_for(row: dict, F: float) -> list[dict]:
    """The two calls of one shot, as the planner writes them (without expected_project_path):
    [{"tool": "set_item_in_out", "args"}, {"tool": "overwrite_clip", "args"}]. src_in is the row's value,
    rounded to 6 places, so it is rounded twice, as in the planner."""
    marks = {"item_id": row["item"], "in_seconds": round(row["src_in"] + EPS, 6),
             "out_seconds": round(row["src_in"] + row["frames"] / F + EPS, 6)}
    place_args = {"item_id": row["item"], "start_seconds": round(row["start_f"] / F + EPS, 6),
                  "track_index": row["track"], "audio_track_index": row["audio_track"]}
    return [{"tool": "set_item_in_out", "args": marks}, {"tool": "overwrite_clip", "args": place_args}]


def _no_constant(text: str) -> object:
    raise ValueError(f"{text} is not a JSON value")


def load_shots(path) -> dict:
    """Read a placement plan file. Raises CliError("bad_plan") when it cannot be read or is not JSON. The
    shots are checked by resolve_shots."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _plan_fault(f"Cannot read the plan {path}: {exc}.") from None
    try:
        plan = json.loads(text, parse_constant=_no_constant)
    except (ValueError, RecursionError) as exc:
        raise _plan_fault(f"The plan is not valid JSON: {exc}.") from None
    if not isinstance(plan, dict):
        raise _plan_fault("The plan must be a JSON object with \"fps\" and \"shots\".")
    return plan


# =====================================================================================================
# live place
# =====================================================================================================
PLACE, REMOVE_PICTURE, EXTEND = "place", "remove_picture", "extend"
MOVED = "the active sequence changed during the run, so the step that needs the new clip was not sent"


def _ticks_text(value) -> bool:
    return value is None or (isinstance(value, str) and value.isascii() and value.isdigit())


def _clip_ok(clip) -> bool:
    return (isinstance(clip, dict) and all(_ticks_text(clip.get(k)) for k in ("start_ticks", "end_ticks", "in_ticks",
                                                                               "out_ticks"))
            and (clip.get("node_id") is None or isinstance(clip.get("node_id"), str))
            and (clip.get("item") is None or isinstance(clip.get("item"), str)))


def _shape(reply, queries: list[str], entries: list[int]) -> dict:
    """The place_read reply, checked: {"frame": int, "video_tracks", "audio_tracks", "items": {query: record},
    "spans": {entry: {"video", "audio"}}, "first_v1"}. Raises CliError("bad_reply")."""
    bad = CliError("bad_reply", "The place_read script returned an answer in an unexpected shape.")
    if not isinstance(reply, dict) or not isinstance(reply.get("sequence"), dict):
        raise bad
    seq = reply["sequence"]
    frame = seq.get("frame_ticks")
    if not (isinstance(frame, str) and frame.isascii() and frame.isdigit() and int(frame) > 0):
        raise CliError("bad_reply", "Premiere did not report the frame length of the sequence (seq.timebase).")
    if not all(isinstance(seq.get(k), int) and not isinstance(seq.get(k), bool) for k in ("video_tracks",
                                                                                           "audio_tracks")):
        raise bad
    items, spans = reply.get("items"), reply.get("spans")
    if not isinstance(items, list) or len(items) != len(queries) or not isinstance(spans, list) \
            or len(spans) != len(entries):
        raise bad
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("found"), bool) \
                or not isinstance(item.get("matches"), int):
            raise bad
        if item["found"] and not isinstance(item.get("node_id"), str):
            raise bad
    for span in spans:
        if not isinstance(span, dict):
            raise bad
        for side in ("video", "audio"):
            clips = span.get(side)
            if clips is not None and not (isinstance(clips, list) and all(_clip_ok(c) for c in clips)):
                raise bad
    first = reply.get("first_v1")
    if first is not None and not (isinstance(first, dict) and _ticks_text(first.get("start_ticks"))
                                  and isinstance(first.get("head_transition"), bool)):
        raise bad
    return {"frame": int(frame), "video_tracks": seq["video_tracks"], "audio_tracks": seq["audio_tracks"],
            "name": seq.get("name"), "items": {q: item for q, item in zip(queries, items)},
            "spans": {e: span for e, span in zip(entries, spans)}, "first_v1": first}


def _read_args(rows: list[dict], numbers: list[int], F: float, frame: int | None, items: list[str]) -> dict:
    """The ARGS of place_read for these shots. Before the frame length is known, the plan's rate gives the ticks."""
    def ticks(f: int) -> str:
        return str(f * frame if frame is not None else round(f * TICKS_PER_SECOND / F))
    return {"items": items,
            "spans": [{"entry": k, "track": rows[k]["track"], "audio_track": rows[k]["audio_track"],
                       "start_ticks": ticks(rows[k]["start_f"]), "end_ticks": ticks(rows[k]["end_f"])}
                      for k in numbers]}


def _near(a: int, b: float, step: float) -> bool:
    """a and b are within half a step."""
    return 2 * abs(a - b) <= step


def _own(clips, node_id: str, start: int, frame: int) -> list[dict]:
    """The clips of the item that start at `start`, within half a frame."""
    return [c for c in clips or [] if c.get("item") == node_id and c.get("start_ticks") is not None
            and _near(int(c["start_ticks"]), start, frame)]


def _marks_match(clip: dict, row: dict, F: float) -> bool:
    """The clip shows the media from src_in to src_in + frames / F, each within half an item frame."""
    if clip.get("in_ticks") is None or clip.get("out_ticks") is None:
        return False
    step = TICKS_PER_SECOND / row["item_fps"]
    want_in = row["src_in"] * TICKS_PER_SECOND
    want_out = (row["src_in"] + row["frames"] / F) * TICKS_PER_SECOND
    return _near(int(clip["in_ticks"]), want_in, step) and _near(int(clip["out_ticks"]), want_out, step)


def _length(clip: dict) -> int | None:
    if clip.get("start_ticks") is None or clip.get("end_ticks") is None:
        return None
    return int(clip["end_ticks"]) - int(clip["start_ticks"])


def _seconds(ticks) -> float | None:
    return None if ticks is None else round(int(ticks) / TICKS_PER_SECOND, 6)


def _clip_now(clip: dict | None) -> dict | None:
    if clip is None:
        return None
    return {"node_id": clip.get("node_id"), "start_seconds": _seconds(clip.get("start_ticks")),
            "in_seconds": _seconds(clip.get("in_ticks")), "out_seconds": _seconds(clip.get("out_ticks"))}


def _clip_text(clip: dict, track: str) -> str:
    start, end = _seconds(clip.get("start_ticks")), _seconds(clip.get("end_ticks"))
    return f"{clip.get('node_id')} (item {clip.get('item')}, {start} s to {end} s on {track})"


def _who(row: dict) -> str:
    return f"shot {row['entry']} ({row['name']})"


def _judge(row: dict, item: dict | None, span: dict, frame: int, F: float, overwrite: bool,
           first_v1: dict | None) -> dict:
    """Session A for one shot. Returns {"node_id", "refusal": None or (code, reason), "skip": None or reason,
    "steps": [PLACE, REMOVE_PICTURE, EXTEND as needed], "warnings", "cut", "picture" (a clip of the shot that is
    there already), "now"}. Refusals in the order of the brief, after no_item, ambiguous_item, no_track and
    plan_overlap (checked by the caller)."""
    out = {"node_id": item["node_id"], "refusal": None, "skip": None, "steps": [], "warnings": [], "cut": [],
           "picture": None, "now": None}
    node = item["node_id"]
    start, length = row["start_f"] * frame, row["frames"] * frame
    pictures = _own(span["video"], node, start, frame)
    sounds = _own(span["audio"], node, start, frame)
    picture = pictures[0] if pictures else None
    sound = sounds[0] if sounds else None
    out["now"] = {"video": _clip_now(picture), "audio": _clip_now(sound)}
    has_video, has_audio = item.get("has_video") is not False, item.get("has_audio") is not False

    def refuse(code: str, reason: str) -> dict:
        out["refusal"] = (code, reason)
        return out

    if row["audio_only"]:
        if item.get("has_audio") is False:
            return refuse("no_sound", f"the shot is sound only, and item {node} has no sound.")
        if sound is not None and not _marks_match(sound, row, F):
            return refuse("placed_differently", f"the sound of item {node} starts there already with other in and "
                                                "out points.")
        if sound is not None and picture is None:
            out["skip"] = "already placed"
            return out
        if sound is not None:
            out["steps"] = [REMOVE_PICTURE]                  # half done: the picture is left
            out["picture"] = picture
            return out
        if picture is not None:
            return refuse("placed_differently", f"the picture of item {node} starts there already without its "
                                                "sound.")
        out["steps"] = [PLACE, REMOVE_PICTURE]
    elif item.get("still"):
        if picture is not None:
            got = _length(picture)
            if got is not None and _near(got, length, frame):
                out["skip"] = "already placed"
                return out
            if got is not None and got < length:
                out["steps"] = [EXTEND]                      # half done: placed but short
                out["picture"] = picture
                return out
            return refuse("placed_differently", f"the still {node} starts there already and runs "
                                                f"{_seconds(got)} s, not {round(row['frames'] / F, 6)} s.")
        out["steps"] = [PLACE, EXTEND]
    else:
        main = picture if has_video else sound
        if main is not None and not _marks_match(main, row, F):
            return refuse("placed_differently", f"item {node} starts there already with other in and out points "
                                                f"({_seconds(main.get('in_ticks'))} s to "
                                                f"{_seconds(main.get('out_ticks'))} s, the plan says "
                                                f"{row['src_in']} s to {round(row['src_in'] + row['frames'] / F, 6)} "
                                                "s). --overwrite cannot place the same item at the same start.")
        if main is not None and (not has_audio or sound is not None) and (not has_video or picture is not None):
            out["skip"] = "already placed"
            return out
        if main is not None:
            out["warnings"].append("The shot is there with only part of its streams, so it is placed again.")
        out["steps"] = [PLACE]

    own = {id(c) for c in pictures + sounds}
    foreign = []
    if has_video or row["audio_only"]:
        foreign += [_clip_text(c, f"V{row['track'] + 1}") for c in span["video"] or [] if id(c) not in own]
    if row["audio_only"] and foreign:
        # The picture goes on a spare track and is removed again: cutting a clip there would leave a hole.
        return refuse("span_not_empty", "the shot is sound only, so its picture needs an empty span on "
                      f"V{row['track'] + 1}, and these clips lie there: " + "; ".join(foreign)
                      + ". --overwrite does not cut them. Name a spare video track.")
    if has_audio and not item.get("still"):
        foreign += [_clip_text(c, f"A{row['audio_track'] + 1}") for c in span["audio"] or [] if id(c) not in own]
    if foreign:
        if not overwrite:
            return refuse("span_not_empty", "these clips lie in the span of the shot: " + "; ".join(foreign)
                          + ". Use --overwrite to cut them.")
        out["cut"] = foreign
        out["warnings"].append("These clips will be cut: " + "; ".join(foreign) + ".")
    if row["track"] == 0 and first_v1 is not None and first_v1.get("head_transition") \
            and first_v1.get("start_ticks") is not None:
        first = int(first_v1["start_ticks"])
        if start - frame / 2 <= first < start + length - frame / 2:
            out["warnings"].append(f"The shot covers the start of the first V1 clip ({first_v1.get('node_id')}), "
                                   "which has a transition at its head. Overwriting it deletes that transition.")
    return out


def _overlaps(rows: list[dict], items: list[dict | None]) -> dict[int, list[int]]:
    """The shots of the plan that overlap another shot of the plan on one video or one audio track."""
    found: dict[int, list[int]] = {}
    for a in range(len(rows)):
        for b in range(a + 1, len(rows)):
            ra, rb = rows[a], rows[b]
            if not (ra["start_f"] < rb["end_f"] and rb["start_f"] < ra["end_f"]):
                continue
            ia, ib = items[a] or {}, items[b] or {}
            video = ra["track"] == rb["track"] and ia.get("has_video") is not False and ib.get("has_video") is not False
            audio = (ra["audio_track"] == rb["audio_track"] and ia.get("has_audio") is not False
                     and ib.get("has_audio") is not False and not ia.get("still") and not ib.get("still"))
            if video or audio:
                found.setdefault(a, []).append(b)
                found.setdefault(b, []).append(a)
    return found


def _plan_all(rows: list[dict], info: dict, F: float, overwrite: bool) -> list[dict]:
    """Session A for every shot: the refusals that need the whole plan, then _judge per shot."""
    items = []
    for row in rows:
        item = info["items"][row["item"]]
        items.append(item if item["found"] else None)
    overlap = _overlaps(rows, items)
    plans = []
    for k, row in enumerate(rows):
        item = info["items"][row["item"]]
        plan = {"node_id": item.get("node_id") if item["found"] else None, "refusal": None, "skip": None,
                "steps": [], "warnings": [], "cut": [], "picture": None, "now": None}
        span = info["spans"][k]
        if not item["found"]:
            plan["refusal"] = (("ambiguous_item", f"{item['matches']} project items are named {row['item']}. Name "
                                                  "the item by its node ID.") if item["matches"] > 1 else
                               ("no_item", f"no project item has the node ID or name {row['item']}."))
        elif span["video"] is None or span["audio"] is None:
            plan["refusal"] = ("no_track", f"the sequence has {info['video_tracks']} video and {info['audio_tracks']} "
                                           f"audio tracks; the shot names V{row['track'] + 1} and "
                                           f"A{row['audio_track'] + 1}.")
        elif k in overlap:
            plan["refusal"] = ("plan_overlap", "the shot overlaps " + ", ".join(_who(rows[o]) for o in overlap[k])
                               + " of the plan on one track.")
        else:
            plan = _judge(row, item, span, info["frame"], F, overwrite, info["first_v1"])
        plans.append(plan)
    return plans


def _reply_fault(tool: str, reply: dict) -> str | None:
    """None when the tool did its step: no tool error, and data.verified is true."""
    if reply.get("is_error"):
        return f"{tool}: {reply.get('text') or 'the tool reported an error'}"
    structured = reply.get("structured")
    data = structured.get("data") if isinstance(structured, dict) else None
    if not isinstance(data, dict) or data.get("verified") is not True:
        return f"{tool}: the tool did not report its step as verified"
    return None


def _marker_open(client) -> bool:
    """A recovery marker in the bridge folder (one that cannot be read counts as open)."""
    from cli_anything.premiere.core import bridge
    try:
        return bridge.read_recovery(client.bridge_dir) is not None
    except Exception:            # noqa: BLE001 - an unreadable marker is an open recovery
        return True


def _second_targets(rows: list[dict], plans: list[dict], numbers: list[int], info: dict, F: float) -> dict:
    """The node IDs for MCP session 2, from the read between the sessions: {entry: call or None (nothing to do)
    or a fault text}."""
    frame = info["frame"]
    out: dict = {}
    for k in numbers:
        row, plan = rows[k], plans[k]
        span = info["spans"][k]
        start = row["start_f"] * frame
        pictures = _own(span["video"], plan["node_id"], start, frame)
        if not pictures:
            out[k] = "the new picture clip was not found on the track after the placement"
            continue
        picture = pictures[0]
        if REMOVE_PICTURE in plan["steps"]:
            out[k] = {"tool": "remove_from_timeline",
                      "args": {"node_id": picture["node_id"], "include_linked": False, "ripple": False}}
        else:
            got = _length(picture)
            if got is not None and not got < row["frames"] * frame - frame / 2:
                out[k] = None
            else:
                out[k] = {"tool": "set_clip_duration",
                          "args": {"node_id": picture["node_id"], "duration_seconds": row["frames"] / F}}
    return out


def _done(row: dict, plan: dict, span: dict, frame: int, F: float, item: dict) -> tuple[bool, dict, str | None]:
    """Session B for one shot: (done, now, why not)."""
    node = plan["node_id"]
    start, length = row["start_f"] * frame, row["frames"] * frame
    pictures = _own(span["video"], node, start, frame)
    sounds = _own(span["audio"], node, start, frame)
    picture, sound = (pictures[0] if pictures else None), (sounds[0] if sounds else None)
    now = {"video": _clip_now(picture), "audio": _clip_now(sound)}
    if row["audio_only"]:
        if sound is None:
            return False, now, "the sound clip is not on the audio track at the start"
        if any(c.get("item") == node for c in span["video"] or []):
            return False, now, "the picture clip of the item is still on the track"
        return True, now, None
    if item.get("has_video") is not False:
        if picture is None:
            return False, now, "no clip of the item starts on the track at the planned start"
        if item.get("still"):
            got = _length(picture)
            if got is None or not _near(got, length, frame):
                return False, now, f"the still runs {_seconds(got)} s, not {round(row['frames'] / F, 6)} s"
            return True, now, None
        if not _marks_match(picture, row, F):
            return False, now, "the clip's in and out points are not the planned ones"
    if item.get("has_audio") is True and sound is None:
        return False, now, "the sound clip is not on the audio track at the start"
    if item.get("has_video") is False and (sound is None or not _marks_match(sound, row, F)):
        return False, now, "the sound clip's in and out points are not the planned ones"
    return True, now, None


def place_shots(client, make_session, plan: dict, *, sequence_id: str, sequence_name=None, overwrite=False,
                skip_refused=False, dry_run=False, project=None, any_project=False, allow_untested=False,
                hard_limit_s=DEFAULT_HARD_LIMIT_S) -> dict:
    """Place the shots of a placement plan on the sequence with sequence_id, and check each one.

    Order:
    1. The plan is checked (resolve_shots): CliError("bad_plan") before any contact.
    2. Session A (ActiveRun.prepare, switch=not dry_run): place_read reads the sequence, every item the plan
       names, and the clips in each shot's span. The plan's fps must match the sequence (fps_mismatch). Per shot:
       no_item, ambiguous_item, no_track, plan_overlap, no_sound, placed_differently refuse; "already placed" is
       nothing to do; span_not_empty refuses unless overwrite (then a warning lists the clips that will be cut). A
       half-done shot gets only its missing step. A refusal stops the run here unless skip_refused. dry_run returns
       here with "planned".
    3. MCP session 1 (no lease): per shot set_item_in_out, then overwrite_clip, with the resolved node ID and the
       guard args. The first failure stops it (is_error, or data.verified not true).
    4. When a shot needs a second step (remove the picture of a sound-only shot, extend a still): unless the first
       session ended by a stop or left a recovery marker, a read pass finds the new clips; when it shows that the
       target is no longer the active sequence, those shots fail. Else MCP session 2 sends remove_from_timeline or
       set_clip_duration for the shots placed before any failure.
    5. Session B (ActiveRun.finish) puts the active sequence back, then a read pass checks every shot that ran.

    Returns results.summary(records, warnings, sequence=..., marks=..., left_switched=..., stopped=...) and, with
    dry_run, "dry_run": True and "planned". A record has the extras "calls" ([{"tool", "ok"}]) and "marks"
    ({"in_seconds", "out_seconds"} that set_item_in_out applied, or None). Raises a CliError that carries this
    payload: refused, fps_mismatch, the error that stopped the run, not_done, or not_restored.
    """
    F, rows = resolve_shots(plan)
    warnings: list[str] = []
    for shot, row in zip(plan["shots"], rows):
        unknown = [key for key in shot if key not in SHOT_KEYS]
        if unknown:
            warnings.append(f"{_who(row)}: this command does not read the key"
                            f"{'s' if len(unknown) > 1 else ''} {', '.join(repr(k) for k in unknown)}.")
        if row["media_length"] is not None and row["src_out"] > float(row["media_length"]) + 1e-6:
            warnings.append(f"{_who(row)}: the out point {row['src_out']} s is past the media length "
                            f"{row['media_length']} s.")
    records = []
    for row in rows:
        record = results.new_record(row["entry"], {"item": row["item"], "node_id": None, "track": row["track"],
                                                   "audio_track": row["audio_track"],
                                                   "start_seconds": round(row["start_f"] / F, 6),
                                                   "frames": row["frames"], "src_in": row["src_in"]},
                                    name=row["name"])
        record.update(calls=[], marks=None)
        records.append(record)
    marks: dict[str, dict] = {}
    run = ActiveRun(client, make_session, sequence_id=sequence_id, sequence_name=sequence_name, project=project,
                    any_project=any_project, allow_untested=allow_untested, hard_limit_s=hard_limit_s,
                    call_timeout_s=hard_limit_s)
    body = jsx.load("place_read")
    queries = list(dict.fromkeys(row["item"] for row in rows))
    state: dict = {"info": None, "plans": None, "planned": None}

    def payload(**extra) -> dict:
        notes = list(run.warnings) + [w for w in warnings if w not in run.warnings]
        return results.summary(records, notes, sequence=dict(run.sequence), marks=list(marks.values()),
                               left_switched=run.left_switched, stopped=run.stopped, **extra)

    def dry_extra() -> dict:
        return {"dry_run": True, "planned": state["planned"]} if dry_run and state["planned"] is not None else {}

    def check(session) -> list[dict]:
        reply = session.read(body, _read_args(rows, list(range(len(rows))), F, None, queries))
        info = state["info"] = _shape(reply, queries, list(range(len(rows))))
        sequence_fps = TICKS_PER_SECOND / info["frame"]
        if abs(sequence_fps - F) > FPS_TOLERANCE:
            raise CliError("fps_mismatch", f"The plan's fps is {round(F, 6)}, and the sequence runs at "
                                           f"{round(sequence_fps, 6)} fps. Nothing was written.")
        plans = state["plans"] = _plan_all(rows, info, F, overwrite)
        for k, item_plan in enumerate(plans):
            record = records[k]
            record["target"]["node_id"] = item_plan["node_id"]
            record["warnings"] = list(item_plan["warnings"])
            if item_plan["refusal"] is not None:
                results.mark_skipped(record, results.REFUSED, *item_plan["refusal"])
            elif item_plan["skip"] is not None:
                results.mark_skipped(record, results.NOTHING_TO_DO, "already_placed", item_plan["skip"],
                                     now=item_plan["now"])
        state["planned"] = [_planned(rows[k], plans[k], records[k], F, run) for k in range(len(rows))]
        if any(p["refusal"] is not None for p in plans) and not skip_refused:
            raise results.refused_error(records, payload(**dry_extra()))
        return plans

    try:
        plans = run.prepare(check, switch=not dry_run)
    except BaseException as exc:
        error = _run_error(exc)
        fault = run.finish()
        error = error or fault
        error.payload = payload(**dry_extra())
        raise error from None
    if dry_run:
        run.finish()
        return payload(**dry_extra())

    work = {"error": None, "attempted": [], "frame": state["info"]["frame"]}
    try:
        _work(run, rows, plans, records, marks, F, body, queries, state["info"], work)
    except BaseException as exc:
        if stops.is_stop(exc):
            run.stopped = True
        work["error"] = work["error"] or _run_error(exc)
    fault = run.finish()
    work["error"] = work["error"] or fault
    _check_after(run, rows, plans, records, F, body, queries, work)
    done = payload()
    error = work["error"]
    if error is None and run.stopped:
        error = CliError("interrupted", STOPPED_PLACE)
    if error is None:
        error = results.outcome_error(records, done)
    if error is None and run.left_switched is not None:
        error = CliError("not_restored", "Every shot is done, but the active sequence could not be put back. The "
                                         "warnings say how to do it.")
    if error is not None:
        error.payload = done
        raise error
    return done


def _run_error(exc: BaseException) -> CliError | None:
    """An exception that ends the run, as the error of the run."""
    if isinstance(exc, CliError):
        return exc
    if stops.is_stop(exc):
        return CliError("interrupted", STOPPED_PLACE)
    if not isinstance(exc, Exception):
        raise exc
    return CliError("recipe_error", f"{type(exc).__name__}: {exc}")


def _planned(row: dict, plan: dict, record: dict, F: float, run: ActiveRun) -> dict:
    """One shot of a dry run: what would be sent."""
    guard = run.guard_args()
    calls, after = [], []
    if plan["refusal"] is None and plan["skip"] is None:
        if PLACE in plan["steps"]:
            calls = [{"tool": c["tool"], "args": {**c["args"], **guard}}
                     for c in calls_for({**row, "item": plan["node_id"]}, F)]
        picture = (plan["picture"] or {}).get("node_id")
        if REMOVE_PICTURE in plan["steps"]:
            after.append({"tool": "remove_from_timeline",
                          "args": {"node_id": picture, "include_linked": False, "ripple": False, **guard}})
        if EXTEND in plan["steps"]:
            after.append({"tool": "set_clip_duration",
                          "args": {"node_id": picture, "duration_seconds": row["frames"] / F, **guard},
                          "when": "the placed still is shorter than the shot"})
    would = ("refused" if plan["refusal"] is not None else "nothing to do" if plan["skip"] is not None
             else "place" if PLACE in plan["steps"] else "finish")
    return {"entry": row["entry"], "name": row["name"], "node_id": plan["node_id"], "would": would,
            "steps": list(plan["steps"]), "calls": calls, "after": after,
            "refusal": None if plan["refusal"] is None else {"code": plan["refusal"][0], "reason": plan["refusal"][1]},
            "skip": plan["skip"], "cut": list(plan["cut"]), "warnings": list(record["warnings"])}


def _work(run: ActiveRun, rows, plans, records, marks, F, body, queries, info, work) -> None:
    """MCP session 1, the read between the sessions, MCP session 2. Fills work["error"] and the progress of each
    shot (work["progress"]); the records are judged after session B."""
    todo = [k for k, record in enumerate(records) if record["status"] == "not_run" and plans[k]["steps"]]
    progress = work["progress"] = {k: {"fault": None, "flight": None, "placed": False, "second": None}
                                   for k in todo}
    work["attempted"] = []
    first = [k for k in todo if PLACE in plans[k]["steps"]]
    stopped_at = None

    def steps_one(mcp, guard_args) -> None:
        nonlocal stopped_at
        for k in first:
            p = progress[k]
            work["attempted"].append(k)
            for call in calls_for({**rows[k], "item": plans[k]["node_id"]}, F):
                p["flight"] = call["tool"]
                reply = mcp.call(call["tool"], {**call["args"], **guard_args}, timeout_s=run.call_timeout_s)
                p["flight"] = None
                fault = _reply_fault(call["tool"], reply)
                records[k]["calls"].append({"tool": call["tool"], "ok": fault is None})
                if call["tool"] == "set_item_in_out" and fault is None:
                    data = reply["structured"]["data"]
                    applied = {"in_seconds": data.get("appliedInSeconds"), "out_seconds": data.get("appliedOutSeconds")}
                    records[k]["marks"] = applied
                    marks[plans[k]["node_id"]] = {"item": plans[k]["node_id"], **applied, "set_by": k}
                if fault is not None:
                    p["fault"] = fault
                    stopped_at = k
                    return
            p["placed"] = True

    error = run.mcp(steps_one) if first else None
    for k in first:
        if progress[k]["flight"] is not None:
            stopped_at = k if stopped_at is None else stopped_at
            progress[k]["fault"] = (f"{progress[k]['flight']}: the call did not end "
                                    f"({error.code if error else 'unknown'}); it may have landed")
    work["error"] = error
    # Decision 5: the second session runs for the shots placed before a failure, unless the failure was a stop or
    # left a recovery marker.
    second = [k for k in todo if (REMOVE_PICTURE in plans[k]["steps"] or EXTEND in plans[k]["steps"])
              and (stopped_at is None or k < stopped_at)
              and (PLACE not in plans[k]["steps"] or progress[k]["placed"])]
    if not second:
        return
    why_not = None
    if run.stopped or stops.is_stop(error):
        why_not = "the run was stopped before it"
    elif _marker_open(run.client):
        why_not = "a recovery is open, so it was not sent"
    if why_not is not None:
        for k in second:
            progress[k]["second"] = f"the second step did not run: {why_not}"
        return
    for k in second:
        if k not in work["attempted"]:
            work["attempted"].append(k)
    reply, shown = run.look(body, _read_args(rows, second, F, info["frame"], queries))
    now_info = _shape(reply, queries, second)
    if not shown:
        for k in second:
            progress[k]["second"] = MOVED
        return
    targets = _second_targets(rows, plans, second, now_info, F)
    calls = []
    for k in second:
        target = targets[k]
        if isinstance(target, str):
            progress[k]["second"] = target
        elif target is not None:
            calls.append((k, target))
    if not calls:
        return

    def steps_two(mcp, guard_args) -> None:
        for k, call in calls:
            p = progress[k]
            p["flight"] = call["tool"]
            reply = mcp.call(call["tool"], {**call["args"], **guard_args}, timeout_s=run.call_timeout_s)
            p["flight"] = None
            fault = _reply_fault(call["tool"], reply)
            records[k]["calls"].append({"tool": call["tool"], "ok": fault is None})
            if fault is not None:
                p["second"] = fault
                return

    error = run.mcp(steps_two)
    for k, _call in calls:
        if progress[k]["flight"] is not None:
            progress[k]["second"] = (f"{progress[k]['flight']}: the call did not end "
                                     f"({error.code if error else 'unknown'}); it may have landed")
            progress[k]["flight"] = "second"
    work["error"] = work["error"] or error


def _fault_text(progress: dict | None) -> str | None:
    """What went wrong with the steps of one shot, or None."""
    parts = [text for text in ((progress or {}).get("fault"), (progress or {}).get("second")) if text]
    return "; ".join(parts) or None


def _check_after(run: ActiveRun, rows, plans, records, F, body, queries, work) -> None:
    """Session B's read pass: each shot that ran is done or failed by what the sequence holds now."""
    progress = work.get("progress") or {}
    numbers = [k for k in work["attempted"]]
    if not numbers:
        return
    unknown_outcome = {k for k in numbers if progress[k]["flight"] is not None}
    try:
        reply, _shown = run.look(body, _read_args(rows, numbers, F, work["frame"], queries))
        info = _shape(reply, queries, numbers)
    except BaseException as exc:
        error = _run_error(exc)
        if stops.is_stop(exc):
            run.stopped = True
        work["error"] = work["error"] or error
        for k in numbers:
            results.mark_failed(records[k], _fault_text(progress[k]) or f"the shot could not be read back after the "
                                f"run ({error.code}: {error.message})", known=False)
        return
    items = info["items"]
    for k in numbers:
        row, plan = rows[k], plans[k]
        ok, now, why = _done(row, plan, info["spans"][k], info["frame"], F, items[row["item"]])
        fault = _fault_text(progress[k])
        if ok:
            results.mark_done(records[k], now)
            if fault is not None:
                records[k]["warnings"].append(f"A step reported a fault, but the read after shows the shot as "
                                              f"planned: {fault}")
        else:
            text = fault or f"the read after the run does not show the shot as planned: {why}"
            if fault is not None and why is not None:
                text += f" ({why})"
            results.mark_failed(records[k], text, now=now, known=k not in unknown_outcome)
