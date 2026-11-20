"""`live keys get`, `live keys set` and the key math that the keys commands share.

The math is plain Python on numbers, so a plan can be checked with no Premiere open:
- key_ticks puts the keys of a plan on the clip's frame grid, in ticks, and finds keys that would clash;
- static_rule and entry_mode decide, for one plan entry, whether it gets keys, one static value, or nothing.

get_keys is a read: it takes the bridge lease, runs the read guards, sends core/scripts/keys_get.jsx and shapes
its answer. The script returns the raw fields (tick values as strings); the seconds and the shape are made here.

set_keys writes a level plan (one entry per clip: Rotation keys or one static Rotation, and one static Scale) in
one WriteSession: keys_check.jsx reads every clip, every entry is checked, then each clip gets its Scale
(effects.write_entry) and its Rotation (keys_write.jsx), each read back. See set_keys.

Time of a key. Premiere stores a key in media time: the clip's in point plus the time from the clip start. A plan
and the commands use clip-relative seconds (0 is the first visible frame). The media ticks of a key are
`int(in_ticks) + ticks` of key_ticks (premiere-pro-mcp, bridge/script-builder.js, lines 1917-1943).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from cli_anything.premiere.core import bridge, jsx
from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.core.recipes import effects, results, stops
from cli_anything.premiere.core.recipes.clips import TICKS_PER_SECOND, _seconds, _tick, _whole
from cli_anything.premiere.core.session import (DEFAULT_HARD_LIMIT_S, SOFT_TIMEOUT_S, WriteSession, guarded_state,
                                                run_script)
from cli_anything.premiere.errors import CliError

KEY_TOLERANCE = 0.0001      # a value read back from a key is the value written when it is this near (keyframes.js:437)
STATIC_SPREAD = 0.75        # up to this many degrees from the lowest to the highest key, one static value does
SPEED_TOLERANCE = 0.0001    # a clip speed this near 1 is normal speed (bridge/script-builder.js, line 1935)
FAULT_CODES = ("negative_key", "key_clash")
SCRIPT_INCLUDES = ("find", "values", "keys")     # what keys_get.jsx and the later keys scripts are built with
_EPSILON = 1e-9             # float noise: 0.85 - 0.1 is 0.75 up to the last digit
_MODES = ("keys", "static", "none")


class KeyFault(CliError):
    """A key that cannot be written as planned. The code is "negative_key" or "key_clash"; the message names the key."""

    def __init__(self, code: str, message: str) -> None:
        if code not in FAULT_CODES:
            raise ValueError(f"a key fault has one of the codes {FAULT_CODES}, not {code!r}")
        super().__init__(code, message)


def _finite(value: object) -> bool:
    """A number (not a bool) that is not NaN and not an infinity."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:            # an integer too big for a float
        return False


# =====================================================================================================
# the key math
# =====================================================================================================
def key_ticks(keys: list[dict], *, length_ticks: int, frame_ticks: int) -> list[dict]:
    """The keys of one plan entry as clip-relative ticks on the frame grid.

    keys is [{"time_seconds", "value"}] in clip-relative seconds. length_ticks is the clip's length in sequence ticks
    (end minus start; the same as out minus in at speed 1). frame_ticks is one frame in ticks (seq.timebase: 10594584000
    at 23.976 fps). Returns [{"seconds", "ticks" (int, clip-relative), "value", "moved_from" (seconds or None)}].

    A key at or past the end of the clip minus half a frame moves to the end minus one frame, exact in ticks, because
    a key on the last frame or past it is not inside the clip. moved_from keeps the time the plan asked for.

    Raises KeyFault("negative_key") for a key before the clip start, and KeyFault("key_clash") when two keys fall on
    the same frame. The clash check runs after the move, so two end keys clash. Raises CliError "bad_plan" for a key
    that is not a time and a value, and "bad_args" for a clip that cannot hold a key.
    """
    if not _whole(frame_ticks, 1):
        raise CliError("bad_args", f"The frame length must be a whole number of ticks above 0, not {frame_ticks!r}.")
    if not _whole(length_ticks, frame_ticks):
        raise CliError("bad_args", f"The clip length must be a whole number of ticks, at least one frame "
                                   f"({frame_ticks} ticks), not {length_ticks!r}.")
    if not isinstance(keys, (list, tuple)):
        raise CliError("bad_plan", f"The keys must be a list, not {type(keys).__name__}.")
    out = []
    for number, key in enumerate(keys):
        if not isinstance(key, dict) or not _finite(key.get("time_seconds")):
            raise CliError("bad_plan", f"Key {number} must be an object with a time_seconds that is a finite number.")
        if not _finite(key.get("value")):
            raise CliError("bad_plan", f"Key {number} must have a value that is a finite number.")
        seconds = key["time_seconds"]
        if seconds < 0:
            raise KeyFault("negative_key", f"a key at {seconds} s is before the clip start.")
        ticks, moved = round(seconds * TICKS_PER_SECOND), None
        if 2 * ticks >= 2 * length_ticks - frame_ticks:          # at or past the end minus half a frame
            ticks, moved = length_ticks - frame_ticks, seconds    # the end minus one frame, exact in ticks
        out.append({"seconds": seconds, "ticks": ticks, "value": key["value"], "moved_from": moved})
    first_on: dict[int, dict] = {}
    for key in out:                                               # the clash check runs after the move
        frame = key["ticks"] // frame_ticks
        other = first_on.get(frame)
        if other is not None:
            note = (" A key at or past the end of the clip goes to the last frame, so two end keys clash."
                    if other["moved_from"] is not None or key["moved_from"] is not None else "")
            raise KeyFault("key_clash", f"the keys at {other['seconds']} s and {key['seconds']} s fall on frame "
                                        f"{frame} of the clip, and two keys cannot share a frame.{note}")
        first_on[frame] = key
    return out


def _check_spread(spread: object) -> float:
    if not _finite(spread) or spread < 0:
        raise CliError("bad_args", f"The static spread must be a number of 0 or more, not {spread!r}.")
    return spread


def _values(values: object, what: str) -> list:
    if not isinstance(values, (list, tuple)) or not values or not all(_finite(value) for value in values):
        raise CliError("bad_plan", f"{what} must be a list of finite numbers, with at least one.")
    return list(values)


def static_rule(values: list[float], spread: float = STATIC_SPREAD) -> dict:
    """The rule for one entry: one static value does when the keys span no more than `spread` degrees.

    Returns {"static": bool, "value": the midpoint of the lowest and the highest, 2 places, "spread": highest minus
    lowest, 2 places}. A spread of 0 is a real limit: only keys of one value are static then.
    """
    spread = _check_spread(spread)
    values = _values(values, "The key values")
    low, high = min(values), max(values)
    return {"static": (high - low) <= spread + _EPSILON, "value": round((high + low) / 2, 2),
            "spread": round(high - low, 2)}


def _entry_values(entry: dict) -> list:
    """The values of the keys of a plan entry (an entry with no keys gives an empty list)."""
    found = entry.get("keys")
    if found is None:
        return []
    if not isinstance(found, (list, tuple)):
        raise CliError("bad_plan", f"The keys of an entry must be a list, not {type(found).__name__}.")
    values = []
    for number, key in enumerate(found):
        if not isinstance(key, dict) or not _finite(key.get("value")):
            raise CliError("bad_plan", f"Key {number} of an entry must be an object with a value that is a finite number.")
        values.append(key["value"])
    return values


def entry_mode(entry: dict, *, static_spread: float | None = None) -> dict:
    """How one plan entry is written: "keys", "static" or "none".

    entry is {"keys": [{"time_seconds", "value"}], "static": bool, "static_value": number} (the level plan of the
    film session; "static" and "static_value" may be missing).

    With static_spread None and a "static" flag in the entry, the entry is followed: static True needs a number in
    static_value (else "bad_plan") and gives mode "static" with that value; static False gives "keys", or "none"
    when the entry has no keys ("level, no change"). Otherwise static_rule decides, with STATIC_SPREAD when
    static_spread is None, else with static_spread (0 is a real value); an entry with no keys is "none".

    Returns {"mode", "value" (the static value, else None), "spread" (highest key minus lowest, 2 places, 0.0 with
    no keys), "source" ("file" when the entry's own flag decided, "rule" when static_rule did)}.
    """
    if static_spread is not None:
        static_spread = _check_spread(static_spread)
    if not isinstance(entry, dict):
        raise CliError("bad_plan", f"An entry must be an object, not {type(entry).__name__}.")
    values = _entry_values(entry)
    spread = round(max(values) - min(values), 2) if values else 0.0
    flag = entry.get("static")
    if flag is not None and not isinstance(flag, bool):
        raise CliError("bad_plan", f'The "static" of an entry must be true or false, not {flag!r}.')
    if static_spread is None and flag is not None:
        if flag:
            value = entry.get("static_value")
            if not _finite(value):
                raise CliError("bad_plan", 'An entry with "static": true needs a number in "static_value", '
                                           f"not {value!r}.")
            return {"mode": "static", "value": float(value), "spread": spread, "source": "file"}
        return {"mode": "keys" if values else "none", "value": None, "spread": spread, "source": "file"}
    if not values:
        return {"mode": "none", "value": None, "spread": spread, "source": "rule"}
    rule = static_rule(values, STATIC_SPREAD if static_spread is None else static_spread)
    if rule["static"]:
        return {"mode": "static", "value": rule["value"], "spread": rule["spread"], "source": "rule"}
    return {"mode": "keys", "value": None, "spread": rule["spread"], "source": "rule"}


# =====================================================================================================
# live keys get
# =====================================================================================================
def _check_get(sequence_id: object, target: object, effect: object, prop: object, property_index: object) -> dict:
    if not (isinstance(sequence_id, str) and sequence_id):
        raise CliError("bad_args", "The sequence ID must be a non-empty string. Nothing was sent.")
    checked = effects._check_target(target)
    if not (isinstance(effect, str) and effect):
        raise CliError("bad_args", "The effect must be a non-empty string. Nothing was sent.")
    if not (isinstance(prop, str) and prop):
        raise CliError("bad_args", "The property must be a non-empty string. Nothing was sent.")
    if property_index is not None and not _whole(property_index, 0):
        raise CliError("bad_args", f"The property index must be a whole number of 0 or more, not {property_index!r}. "
                                   "Nothing was sent.")
    return {"sequence_id": sequence_id, "target": checked, "effect": effect, "property": prop,
            "property_index": property_index}


def _ticks_ok(value: object) -> bool:
    """None, or a tick value that is a string of digits (a number is turned into one by _tick first)."""
    return value is None or _seconds(_tick(value)) is not None


def _value_ok(value: object) -> bool:
    """A static value: nothing, a finite number, a text, a boolean, or a list of numbers (Position)."""
    if value is None or isinstance(value, (bool, str)) or _finite(value):
        return True
    return isinstance(value, list) and all(_finite(item) for item in value)


def _key_ok(item: object) -> bool:
    if not isinstance(item, dict) or _seconds(_tick(item.get("ticks"))) is None:
        return False
    value = item.get("value")
    return value is None or _finite(value) or (isinstance(value, list) and all(_finite(n) for n in value))


def _well_formed_get(reply: object) -> bool:
    """True when the script's answer has the shape this recipe reads."""
    if not isinstance(reply, dict):
        return False
    sequence, clip, effect, prop, read = (reply.get(key) for key in ("sequence", "clip", "effect", "property", "read"))
    if not all(isinstance(block, dict) for block in (sequence, clip, effect, prop, read)):
        return False
    if not (effects._text_or_none(sequence.get("id")) and effects._text_or_none(sequence.get("name"))):
        return False
    if not (effects._text_or_none(clip.get("node_id")) and effects._text_or_none(clip.get("name"))):
        return False
    if clip.get("track_type") not in effects.TRACK_TYPES or not _whole(clip.get("track"), 0) or not _whole(clip.get("clip"), 0):
        return False
    if not all(_ticks_ok(clip.get(key)) for key in ("in_ticks", "out_ticks", "start_ticks", "end_ticks")):
        return False
    speed = clip.get("speed")
    if speed is not None and not _finite(speed):
        return False
    if clip.get("reversed") is not None and not isinstance(clip.get("reversed"), bool):
        return False
    if not (effects._text_or_none(effect.get("name")) and _whole(effect.get("effect_index"), 0)):
        return False
    if not (effects._text_or_none(prop.get("name")) and _whole(prop.get("index"), 0)):
        return False
    found = read.get("keys")
    if not isinstance(read.get("is_time_varying"), bool) or not isinstance(found, list):
        return False
    if not read["is_time_varying"] and found:
        return False
    return all(_key_ok(item) for item in found) and _value_ok(read.get("value"))


def _why_no_seconds(clip: dict) -> str | None:
    """None when the keys of this clip have a clip-relative time, else the reason, as a sentence."""
    if clip.get("reversed") is True:
        return "This clip is reversed"
    speed = clip.get("speed")
    if speed is not None and abs(speed - 1) >= SPEED_TOLERANCE:
        return f"This clip has a speed change ({speed})"
    if speed is None or clip.get("reversed") is None:
        return "Premiere did not report the speed or the reverse state of this clip"
    if _tick(clip.get("in_ticks")) is None:
        return "Premiere did not report the in point of this clip"
    return None


def get_keys(client: BridgeClient, target: dict, *, sequence_id: str, effect: str = "Motion", prop: str = "Rotation",
             property_index: int | None = None, allow_untested: bool = False,
             hard_limit_s: float = DEFAULT_HARD_LIMIT_S) -> dict:
    """Read the keys of one property of one clip. Takes the lease itself; changes nothing.

    target is {"node_id": ID} or {"track": N, "clip": N} with an optional "track_type" ("video" by default, or
    "audio"); the numbers count from 0. The clip is looked for in the sequence with sequence_id, never in the active
    one. effect is a display name or match name that exactly one effect of the clip has. prop is the exact display
    name of the property, and property_index names it by place when two properties share a name (Opacity has two
    "Blend Mode").

    Returns {"sequence": {"id", "name"}, "clip": {"node_id", "name", "track_type", "track", "clip", "in_ticks",
    "out_ticks", "speed"}, "effect": {"name", "effect_index"}, "property": {"name", "index"}, "is_time_varying",
    "value" (the static value of a property with no keys, else None), "keys": [{"seconds", "media_ticks" (a string),
    "value"}], "warnings"}. seconds is (media ticks minus the in point) / 254016000000, 6 places. It is None when the
    clip's speed is not 1, the clip is reversed, or Premiere did not say (the server does not support key times on
    such a clip, tools/keyframes.js lines 327-337); media_ticks is always there. Raises CliError.
    """
    args = _check_get(sequence_id, target, effect, prop, property_index)
    with client.lease():
        _state, warnings = guarded_state(client, write=False, project=None, any_project=False,
                                         allow_untested=allow_untested, hard_limit_s=hard_limit_s)
        reply = run_script(client, jsx.load("keys_get"), args, write=False,
                           timeout_s=min(SOFT_TIMEOUT_S, hard_limit_s), hard_limit_s=hard_limit_s,
                           include=SCRIPT_INCLUDES)
    if not _well_formed_get(reply):
        raise CliError("bad_reply", "The keys script returned an answer in an unexpected shape.")
    sequence, raw, read = reply["sequence"], reply["clip"], reply["read"]
    in_ticks, out_ticks = _tick(raw.get("in_ticks")), _tick(raw.get("out_ticks"))
    why = _why_no_seconds(raw)
    warnings = list(warnings)
    if why is not None and read["keys"]:
        warnings.append(f"{why}, so the keys have no time in seconds. media_ticks are given.")
    found = []
    for item in read["keys"]:
        media = _tick(item["ticks"])
        seconds = None if why is not None else _seconds(str(int(media) - int(in_ticks)))
        found.append({"seconds": seconds, "media_ticks": media, "value": item.get("value")})
    varying = read["is_time_varying"]
    return {"sequence": {"id": effects._name(sequence.get("id")), "name": effects._name(sequence.get("name"))},
            "clip": {"node_id": effects._name(raw.get("node_id")), "name": effects._name(raw.get("name")),
                     "track_type": raw.get("track_type"), "track": raw.get("track"), "clip": raw.get("clip"),
                     "in_ticks": in_ticks, "out_ticks": out_ticks, "speed": raw.get("speed")},
            "effect": {"name": effects._name(reply["effect"].get("name")), "effect_index": reply["effect"]["effect_index"]},
            "property": {"name": effects._name(reply["property"].get("name")), "index": reply["property"]["index"]},
            "is_time_varying": varying, "value": None if varying else read.get("value"), "keys": found,
            "warnings": warnings}


# =====================================================================================================
# live keys set
# =====================================================================================================
# Keys on a clip whose item is interpreted to another frame rate (slow motion) land where they should: measure M6
# of Plan 3, Task 3 (measures.md) wrote them and saw the frames. With False, such a clip is refused.
SLOW_MOTION_PROVEN = True
MOTION, SCALE, ROTATION = "Motion", (1, "Scale"), (4, "Rotation")
# The keys of a level plan entry that this command reads. Others pass, and the warnings name them.
LEVEL_KEYS = ("node", "src_in", "src_out", "name", "scale", "max_deg", "clamped", "keys", "spread_deg", "static",
              "static_value")
FPS_TOLERANCE = 1e-6        # an item frame rate this far from the sequence rate is an interpreted item (measure M6)
NO_CHANGE = "level, no change"
MATCH_TEXT = "Rotation and Scale already hold the plan's values"
MATCH_TEXT_NO_SCALE = "Rotation already holds the plan's values"
MAX_PLAN_SECONDS = 86400.0   # a time or a source mark of a plan is at most one day from 0 (no clip is longer)
NOT_SENT = "Nothing was sent."
STOPPED_SET = "Stopped by Ctrl-C or a stop signal. Check Premiere before another edit."
_SCRIPT_TICKS = re.compile(r"[0-9]+")


def _plan_fault(why: str) -> CliError:
    return CliError("bad_plan", f"{why} {NOT_SENT}")


def check_level_plan(plan: object) -> list[dict]:
    """The level plan, checked. Sends nothing.

    A plan is a JSON array with at least one entry. An entry has "node" (the node ID of the clip, a non-empty
    string), "src_in" and "src_out" (finite seconds of the media, src_out after src_in), "keys" (a list of
    {"time_seconds", "value"}, finite numbers, clip-relative seconds), "scale" (a finite number above 0, or null),
    and "static" and "static_value" as entry_mode reads them. "name" is a text when it is given. Any other key
    passes. A time or a source mark is at most MAX_PLAN_SECONDS from 0, so the tick math cannot overflow. Raises
    CliError("bad_plan") with "entry <n>: ..." and "Nothing was sent." Returns the entries.
    """
    if not isinstance(plan, list) or not plan:
        raise _plan_fault("The level plan must be a JSON array with at least one entry.")
    checked = []
    for number, entry in enumerate(plan):
        where = f"entry {number}: "
        if not isinstance(entry, dict):
            raise _plan_fault(where + "it must be a JSON object.")
        node = entry.get("node")
        if not isinstance(node, str) or not node.strip():
            raise _plan_fault(where + '"node" must be a non-empty string: the node ID of the clip.')
        name = entry.get("name")
        if name is not None and not isinstance(name, str):
            raise _plan_fault(where + '"name" must be a text when it is given.')
        for key in ("src_in", "src_out"):
            if not _finite(entry.get(key)) or abs(entry[key]) > MAX_PLAN_SECONDS:
                raise _plan_fault(where + f'"{key}" must be a finite number of seconds, at most {MAX_PLAN_SECONDS:g} '
                                          "from 0.")
        if not entry["src_out"] > entry["src_in"]:
            raise _plan_fault(where + '"src_out" must be after "src_in".')
        found = entry.get("keys")
        if not isinstance(found, list):
            raise _plan_fault(where + '"keys" must be a list. It may be empty.')
        for index, key in enumerate(found):
            if not isinstance(key, dict) or not _finite(key.get("time_seconds")) or not _finite(key.get("value")):
                raise _plan_fault(where + f'key {index} must be {{"time_seconds": ..., "value": ...}} with finite '
                                          "numbers.")
            if abs(key["time_seconds"]) > MAX_PLAN_SECONDS:
                raise _plan_fault(where + f"key {index}: time_seconds must be at most {MAX_PLAN_SECONDS:g} from 0.")
        scale = entry.get("scale")
        if scale is not None and not (_finite(scale) and scale > 0):
            raise _plan_fault(where + f'"scale" must be a number above 0, or null, not {scale!r}.')
        try:
            entry_mode(entry)
        except CliError as err:
            raise _plan_fault(where + err.message) from None
        checked.append(dict(entry))
    return checked


def _no_constant(text: str) -> object:
    raise ValueError(f"{text} is not a JSON value")


def load_level_plan(path) -> list[dict]:
    """Read and check a level plan file. Raises CliError("bad_plan")."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _plan_fault(f"Cannot read the level plan {path}: {exc}.") from None
    try:
        plan = json.loads(text, parse_constant=_no_constant)
    except (ValueError, RecursionError) as exc:
        raise _plan_fault(f"The level plan is not valid JSON: {exc}.") from None
    return check_level_plan(plan)


def _who(number: int, entry: dict) -> str:
    name = entry.get("name")
    return f"entry {number}" + ("" if not name else f" ({name})")


def _mode_text(mode: dict) -> str:
    if mode["mode"] == "static":
        return f"static at {mode['value']}"
    return "keys" if mode["mode"] == "keys" else "no change"


# ---------- the check ----------
def _ticks_or_none(value: object) -> bool:
    return value is None or (isinstance(value, str) and bool(_SCRIPT_TICKS.fullmatch(value)))


def _check_record_ok(record: object) -> bool:
    """One record of keys_check.jsx, with the types this recipe trusts."""
    if not isinstance(record, dict) or not isinstance(record.get("found"), bool):
        return False
    if not record["found"]:
        return isinstance(record.get("error"), str)
    clip = record.get("clip")
    if not isinstance(clip, dict) or not all(_ticks_or_none(clip.get(key))
                                             for key in ("in_ticks", "out_ticks", "start_ticks", "end_ticks")):
        return False
    if clip.get("speed") is not None and not _finite(clip.get("speed")):
        return False
    if clip.get("reversed") is not None and not isinstance(clip.get("reversed"), bool):
        return False
    if clip.get("track_type") not in effects.TRACK_TYPES:
        return False
    if record.get("item_fps") is not None and not _finite(record.get("item_fps")):
        return False
    if record.get("interpreted") is not None and not isinstance(record.get("interpreted"), bool):
        return False
    motion = record.get("motion")
    if motion is None:
        return True
    if not isinstance(motion, dict) or not _whole(motion.get("effect_index"), 0):
        return False
    scale, rotation = motion.get("scale"), motion.get("rotation")
    if scale is not None and not (isinstance(scale, dict) and isinstance(scale.get("matches"), bool)
                                  and effects._text_or_none(scale.get("name"))
                                  and (scale.get("keyframed") is None or isinstance(scale.get("keyframed"), bool))):
        return False
    if rotation is not None and not (isinstance(rotation, dict) and effects._text_or_none(rotation.get("name"))
                                     and (rotation.get("keys") is None or (isinstance(rotation.get("keys"), list)
                                          and all(_key_ok(item) for item in rotation["keys"])))):
        return False
    return True


def _check(session: WriteSession, entries: list[dict]) -> tuple[int, list[dict]]:
    """keys_check.jsx for these entries, in one read. Returns (frame_ticks, records). Raises CliError("bad_reply")
    for a reply of the wrong shape or a sequence that does not say its frame length."""
    args = {"entries": [{"node_id": entry["node"]} for entry in entries]}
    reply = session.read(jsx.load("keys_check"), args, include=SCRIPT_INCLUDES)
    found = reply.get("entries") if isinstance(reply, dict) else None
    if (not isinstance(found, list) or len(found) != len(entries) or not _ticks_or_none(reply.get("frame_ticks"))
            or not all(_check_record_ok(record) for record in found)):
        raise CliError("bad_reply", "The keys check script returned an answer in an unexpected shape. Nothing was "
                                    "written by this check.")
    frame = reply.get("frame_ticks")
    if frame is None or int(frame) <= 0:
        raise CliError("bad_reply", "Premiere did not report the frame length of the sequence (seq.timebase). "
                                    "Nothing was written by this check.")
    return int(frame), found


def _rotation_now(rotation: dict | None, in_ticks: str | None) -> dict | None:
    """What a Rotation read holds, as a record shows it: {"is_time_varying", "keys": [{"seconds", "media_ticks",
    "value"}], "value"}."""
    if not isinstance(rotation, dict):
        return None
    found = rotation.get("keys")
    varying = rotation.get("is_time_varying")
    if varying is None:
        varying = rotation.get("keyframed")
    keys_now = None
    if isinstance(found, list):
        keys_now = []
        for item in found:
            media = _tick(item.get("ticks"))
            seconds = None if in_ticks is None or media is None else _seconds(str(int(media) - int(in_ticks)))
            keys_now.append({"seconds": seconds, "media_ticks": media, "value": item.get("value")})
    return {"is_time_varying": varying, "keys": keys_now, "value": rotation.get("value")}


def _frames_apart(a: int, b: int, frame: int) -> bool:
    """True when a and b are more than half a frame apart."""
    return 2 * abs(a - b) > frame


def _plan_entry(number: int, entry: dict, mode: dict, record: dict, frame: int, *, scale: bool) -> dict:
    """The checks of one entry, in the order of the brief, and what would be written.

    Returns {"refusal": None or (code, reason), "skip": None or (code, reason) ("no_change" with NO_CHANGE, or
    "keys_match" when the clip holds the plan already: see _matches), "interpreted", "warnings", "in_ticks",
    "out_ticks", "keys" ([{"seconds", "ticks", "media_ticks", "value", "moved_from"}]), "moved", "value" (static),
    "scale_name", "scale_now", "rotation_now", "write_scale"}."""
    out = {"refusal": None, "skip": None, "interpreted": None, "warnings": [], "in_ticks": None, "out_ticks": None,
           "keys": [], "moved": [], "value": mode["value"] if mode["mode"] == "static" else None, "scale_name": None,
           "scale_now": None, "rotation_now": None, "write_scale": False, "mode": mode["mode"], "node_id": entry["node"]}
    if not record["found"]:
        out["refusal"] = ("no_clip", f"{record['error']}.")
        return out
    clip, motion = record["clip"], record.get("motion")
    out["in_ticks"], out["out_ticks"] = clip.get("in_ticks"), clip.get("out_ticks")
    if isinstance(motion, dict):
        if isinstance(motion.get("scale"), dict):
            out["scale_name"], out["scale_now"] = motion["scale"].get("name"), motion["scale"].get("value")
        out["rotation_now"] = _rotation_now(motion.get("rotation"), out["in_ticks"])
    if mode["mode"] == "none":
        out["skip"] = ("no_change", NO_CHANGE)
        return out

    def refuse(code: str, reason: str) -> dict:
        out["refusal"] = (code, reason)
        return out

    in_ticks, out_ticks = clip.get("in_ticks"), clip.get("out_ticks")
    if in_ticks is None or out_ticks is None:
        return refuse("clip_changed", "Premiere did not report the in point or the out point of the clip.")
    plan_in, plan_out = round(entry["src_in"] * TICKS_PER_SECOND), round(entry["src_out"] * TICKS_PER_SECOND)
    if _frames_apart(int(in_ticks), plan_in, frame) or _frames_apart(int(out_ticks), plan_out, frame):
        return refuse("clip_changed", f"the clip shows its media from {_seconds(in_ticks)} s to {_seconds(out_ticks)} "
                                      f"s, and the plan says {entry['src_in']} s to {entry['src_out']} s, more than "
                                      "half a frame apart. It was trimmed or slipped after the plan was made.")
    speed, reversed_ = clip.get("speed"), clip.get("reversed")
    if speed is None or reversed_ is None:
        return refuse("retimed", "Premiere did not report the speed or the reverse state of the clip.")
    if reversed_:
        return refuse("retimed", "the clip is reversed. Keys are written on clips at normal speed only.")
    if abs(speed - 1) >= SPEED_TOLERANCE:
        return refuse("retimed", f"the clip has a speed change ({speed}). Keys are written on clips at normal "
                                 "speed only.")
    item_fps, sequence_fps = record.get("item_fps"), TICKS_PER_SECOND / frame
    out["interpreted"] = record.get("interpreted")          # the M6 read of keys_check.jsx (FPS_TOLERANCE)
    if out["interpreted"] is not False and not SLOW_MOTION_PROVEN:
        what = ("Premiere did not report the frame rate of the clip's item" if item_fps is None else
                f"the clip's item runs at {item_fps} fps, the sequence at {round(sequence_fps, 6)} fps")
        return refuse("slow_motion_unproven", f"{what}. Keys on an interpreted (slow motion) clip are not proven.")
    if out["interpreted"]:
        out["warnings"].append(f"The clip's item runs at {item_fps} fps, not the sequence rate "
                               f"({round(sequence_fps, 6)} fps). Its keys are written in media time, as measure M6 "
                               "proved for an item interpreted for slow motion.")
    elif out["interpreted"] is None:
        out["warnings"].append("Premiere did not report the frame rate of the clip's item, so it is not known "
                               "whether the item is interpreted.")
    if not isinstance(motion, dict):
        return refuse("motion_layout", f"{record.get('motion_error') or 'the clip has no Motion effect'}.")
    scale_prop, rotation = motion.get("scale"), motion.get("rotation")
    if not isinstance(scale_prop, dict) or not scale_prop["matches"]:
        name = None if not isinstance(scale_prop, dict) else scale_prop.get("name")
        return refuse("motion_layout", f"Motion index {SCALE[0]} is {name!r}, not {SCALE[1]!r}.")
    if not isinstance(rotation, dict) or rotation.get("name") != ROTATION[1]:
        name = None if not isinstance(rotation, dict) else rotation.get("name")
        return refuse("motion_layout", f"Motion index {ROTATION[0]} is {name!r}, not {ROTATION[1]!r}.")
    if scale and scale_prop.get("keyframed") is not False:
        return refuse("keyed", "Motion Scale has keys, or Premiere would not say. This command writes one static "
                               "Scale. Use --no-scale to leave Scale as it is.")
    out["write_scale"] = scale
    if mode["mode"] == "keys":
        start, end = clip.get("start_ticks"), clip.get("end_ticks")
        length = None if start is None or end is None else int(end) - int(start)
        if length is None or length < frame or length % frame:
            return refuse("bad_length", f"the clip is {length} ticks long in the sequence, which is not a whole "
                                        f"number of frames ({frame} ticks each). Keys go on the frame grid, so they "
                                        "cannot be placed.")
        try:
            placed = key_ticks(entry["keys"], length_ticks=length, frame_ticks=frame)
        except KeyFault as fault:
            return refuse(fault.code, fault.message)
        for key in placed:
            seconds = _seconds(str(key["ticks"]))
            out["keys"].append({"seconds": seconds, "ticks": str(key["ticks"]),
                                "media_ticks": str(int(in_ticks) + key["ticks"]), "value": key["value"],
                                "moved_from": key["moved_from"]})
            if key["moved_from"] is not None:
                out["moved"].append({"from_seconds": key["moved_from"], "to_seconds": seconds})
    if _matches(out, entry, rotation, scale_prop, scale=scale):
        out["skip"] = ("keys_match", MATCH_TEXT if scale else MATCH_TEXT_NO_SCALE)
        out["write_scale"] = False
    return out


def _matches(out: dict, entry: dict, rotation: dict, scale_prop: dict, *, scale: bool) -> bool:
    """True when the clip holds what this entry would write (D1): then nothing is written.

    Keys: Rotation is time-varying, and its keys are the planned ones (the same media ticks as strings, the values
    within KEY_TOLERANCE; reads are 32-bit floats). Static: Rotation is not time-varying, and its value is within
    KEY_TOLERANCE. With scale, Scale reads the plan's value too (effects.same_value). A null keyframed or keys, or a
    Scale that cannot be read, is no match."""
    if scale and not effects.same_value(scale_prop.get("value"), entry.get("scale")):
        return False
    varying = rotation.get("keyframed")
    if out["mode"] == "static":
        value = rotation.get("value")
        return varying is False and _finite(value) and abs(value - out["value"]) <= KEY_TOLERANCE
    found = rotation.get("keys")
    if varying is not True or not isinstance(found, list) or len(found) != len(out["keys"]):
        return False
    read = {_tick(item.get("ticks")): item.get("value") for item in found}     # by ticks, as _rotation_error
    for planned in out["keys"]:
        value = read.get(planned["media_ticks"])
        if planned["media_ticks"] not in read or not _finite(value) or abs(value - planned["value"]) > KEY_TOLERANCE:
            return False
    return len(read) == len(out["keys"])


def _scale_entry(entry: dict, plan: dict) -> dict:
    """The Scale write of one entry, as effects.write_entry takes it. The name is the one Premiere shows at
    index 1 (Scale, or Scale Height while Uniform Scale is on)."""
    return {"node_id": entry["node"], "effect": MOTION, "index": SCALE[0], "name": plan["scale_name"],
            "value": entry["scale"]}


def _resolve(session: WriteSession, numbers: list[int], entries: list[dict], plans: list[dict]) -> dict[int, dict]:
    """effects.resolve_entries for the Scale writes of these entries, in one read. Returns {entry number:
    resolved record}, with "entry" set to the plan's number."""
    if not numbers:
        return {}
    found = effects.resolve_entries(session, [_scale_entry(entries[n], plans[n]) for n in numbers])
    out = {}
    for number, record in zip(numbers, found):
        record["entry"] = number
        out[number] = record
    return out


# ---------- the write ----------
def _write_reply_ok(reply: object) -> bool:
    """The shape of a keys_write.jsx reply, with the types this recipe trusts."""
    if not isinstance(reply, dict) or not isinstance(reply.get("before"), dict):
        return False
    errors = reply.get("set_errors")
    if not isinstance(errors, list) or not all(isinstance(item, str) for item in errors):
        return False
    after = reply.get("after")
    if after is None:
        return True
    if not isinstance(after, dict) or not isinstance(after.get("is_time_varying"), bool):
        return False
    found = after.get("keys")
    return isinstance(found, list) and all(_key_ok(item) for item in found) and _value_ok(after.get("value"))


def _write_rotation(session: WriteSession, entry: dict, check: dict) -> dict:
    """Write the Rotation of one entry with keys_write.jsx (one session.write) and return its reply. check is
    the entry's plan (_plan_entry): the in and out ticks the check read, the mode, the keys in media ticks, the
    static value. Raises the CliError of session.write, or CliError("bad_reply") for a reply of the wrong shape
    (the write may have landed then)."""
    args = {"node_id": entry["node"], "expect_clip": {"in_ticks": check["in_ticks"], "out_ticks": check["out_ticks"]},
            "mode": check["mode"],
            "keys": ([{"ticks": key["media_ticks"], "value": key["value"]} for key in check["keys"]]
                     if check["mode"] == "keys" else None),
            "value": check["value"] if check["mode"] == "static" else None}
    reply = session.write(jsx.load("keys_write"), args, include=SCRIPT_INCLUDES)
    if not _write_reply_ok(reply):
        raise CliError("bad_reply", "The keys write script returned an answer in an unexpected shape. Check this "
                                    "clip in Premiere: the write may have landed.")
    return reply


def _rotation_error(check: dict, reply: dict) -> str | None:
    """None when the read back holds what was written, else why not."""
    after = reply["after"]
    if check["mode"] == "keys":
        if not after["is_time_varying"]:
            return "Rotation has no keys after the write"
        planned = check["keys"]
        if len(after["keys"]) != len(planned):
            return f"{len(planned)} keys were written and {len(after['keys'])} read back"
        got = {item["ticks"]: item["value"] for item in after["keys"]}
        for key in planned:
            if key["media_ticks"] not in got:
                return (f"no key at {key['media_ticks']} ticks after the write (read back at "
                        f"{', '.join(item['ticks'] for item in after['keys'])} ticks)")
            value = got[key["media_ticks"]]
            if not _finite(value) or abs(value - key["value"]) > KEY_TOLERANCE:
                return f"the key at {key['media_ticks']} ticks reads {value}, not {key['value']}"
        return None
    if after["is_time_varying"]:
        return "Rotation still has keys after the static write"
    value = after.get("value")
    if not _finite(value) or abs(value - check["value"]) > KEY_TOLERANCE:
        return f"Rotation reads {value}, not {check['value']}"
    return None


def _now(scale_now: object, rotation_now: dict | None) -> dict:
    return {"scale": scale_now, "rotation": rotation_now}


def _after_now(check: dict, reply: dict) -> dict | None:
    after = reply["after"]
    if after is None:
        return None
    return _rotation_now(after, check["in_ticks"])


def _planned(number: int, entry: dict, check: dict, *, scale: bool) -> dict:
    """One entry of a dry run: what would be written."""
    refusal = check["refusal"]
    return {"entry": number, "name": entry.get("name"), "node_id": entry["node"], "mode": check["mode"],
            "keys": [dict(key) for key in check["keys"]], "static_value": check["value"],
            "scale": {"now": check["scale_now"],
                      "new": entry.get("scale") if scale and check["skip"] is None else None},
            "refusal": None if refusal is None else {"code": refusal[0], "reason": refusal[1]},
            "skip": None if check["skip"] is None else check["skip"][1]}


def set_keys(client: BridgeClient, plan: list[dict], *, sequence_id: str, sequence_name: str | None = None,
             scale: bool = True, static_spread: float | None = None, skip_refused: bool = False,
             dry_run: bool = False, project=None, any_project=False, allow_untested=False,
             hard_limit_s=DEFAULT_HARD_LIMIT_S) -> dict:
    """Write a level plan: for each entry, one static Scale and the Rotation (keys, or one static value) of one
    clip in the sequence with sequence_id. The project is not saved.

    Order, in one WriteSession: target the sequence; keys_check.jsx reads every clip; one effects.resolve_entries
    for the Scale writes; every entry is checked (in this order: no_clip, clip_changed, retimed,
    slow_motion_unproven, motion_layout, keyed, bad_length, negative_key, key_clash). An entry with no change
    ("no_change") or whose clip holds the plan already ("keys_match", see _matches) is skipped (nothing to do). A
    refusal stops the run before any write (results.refused_error, with "dry_run" and "planned" when dry_run)
    unless skip_refused. dry_run stops here and returns the plan ("planned"). Then per entry, in plan order: the
    Scale with
    effects.write_entry (unless scale is False or the mode is "none"), then the Rotation with keys_write.jsx.
    Between entries the session breathes; when it gave Premiere back, the next entry is checked again, and a
    refusal then skips it and stops the run unless skip_refused.

    An entry is done when its Scale is stored and its Rotation reads back as written (the same ticks, values
    within KEY_TOLERANCE). Else it failed, with what the read back holds in "now", and the run goes on. A send
    error "precondition" fails this entry only (nothing of that send landed; a Scale written before a refused
    Rotation stays written, and the entry's warnings say so). Any other code of stops.NOT_SENT_CODES ends the run:
    this entry and the rest stay not_run. Any other send error fails the entry with an unknown outcome and ends
    the run. The error that ended the run is the error raised (with the payload); a run that ended without one
    (a read back that failed, a refusal after a breathe) or that went on to the end raises
    results.outcome_error when not every entry succeeded.

    Returns results.summary(records, warnings, sequence=...) (with dry_run, also "dry_run": True and "planned").
    A record has the extras "mode", "moved" ([{"from_seconds", "to_seconds"}]) and "interpreted". Raises
    CliError("bad_plan") before anything is sent, and after that a CliError that carries the payload
    (results.refused_error, the error that stopped the run, "interrupted", "recipe_error" for an Exception that is
    neither, or results.outcome_error).
    """
    entries = check_level_plan(plan)
    if not (isinstance(sequence_id, str) and sequence_id):
        raise CliError("bad_args", f"The sequence ID must be a non-empty string. {NOT_SENT}")
    if static_spread is not None:
        try:
            _check_spread(static_spread)
        except CliError as err:
            raise CliError(err.code, f"{err.message} {NOT_SENT}") from None
    warnings: list[str] = []
    for number, entry in enumerate(entries):
        unknown = [key for key in entry if key not in LEVEL_KEYS]
        if unknown:
            warnings.append(f"{_who(number, entry)}: this command does not read the key"
                            f"{'s' if len(unknown) > 1 else ''} {', '.join(repr(k) for k in unknown)}.")
    modes = []
    for number, entry in enumerate(entries):
        mode = entry_mode(entry, static_spread=static_spread)
        if static_spread is not None:
            from_file = entry_mode(entry)
            if (from_file["mode"], from_file["value"]) != (mode["mode"], mode["value"]):
                warnings.append(f"{_who(number, entry)}: --static-spread {static_spread} makes it "
                                f"{_mode_text(mode)}; the plan file says {_mode_text(from_file)}.")
        if scale and mode["mode"] != "none" and entry.get("scale") is None:
            raise CliError("bad_plan", f'entry {number}: "scale" is null. Use --no-scale to leave Scale as it is. '
                                       f"{NOT_SENT}")
        modes.append(mode)
    records = []
    for number, entry in enumerate(entries):
        record = results.new_record(number, {"node_id": entry["node"]}, name=entry.get("name"))
        record.update(mode=modes[number]["mode"], moved=[], interpreted=None)
        records.append(record)
    sequence = {"id": sequence_id, "name": sequence_name}
    state: dict = {"planned": None}

    def payload(**extra) -> dict:
        return results.summary(records, warnings, sequence=sequence, **extra)

    def dry_extra() -> dict:
        return {"dry_run": True, "planned": state["planned"]} if dry_run and state["planned"] is not None else {}

    def apply(number: int, check: dict) -> None:
        """Put the check of one entry on its record: interpreted, moved, warnings, and a skip."""
        record = records[number]
        record["interpreted"] = check["interpreted"]
        record["moved"] = list(check["moved"])
        record["warnings"] = list(check["warnings"])
        if check["skip"] is not None:
            results.mark_skipped(record, results.NOTHING_TO_DO, *check["skip"])
        elif check["refusal"] is not None:
            results.mark_skipped(record, results.REFUSED, check["refusal"][0], check["refusal"][1])

    try:
        with WriteSession(client, project=project, any_project=any_project, allow_untested=allow_untested,
                          hard_limit_s=hard_limit_s) as session:
            warnings += session.warnings
            session.target(sequence_id, sequence_name)
            frame, found = _check(session, entries)
            checks = [_plan_entry(n, entry, modes[n], found[n], frame, scale=scale) for n, entry in enumerate(entries)]
            for number, check in enumerate(checks):
                apply(number, check)
            resolved = _resolve(session, [n for n, c in enumerate(checks) if c["write_scale"] and c["refusal"] is None
                                          and c["skip"] is None], entries, checks)
            state["planned"] = [_planned(n, e, c, scale=scale) for n, (e, c) in enumerate(zip(entries, checks))]
            refused = any(c["refusal"] is not None and c["skip"] is None for c in checks)
            if refused and not skip_refused:
                raise results.refused_error(records, payload(**dry_extra()))
            if dry_run:
                return payload(**dry_extra())
            ended = _run(session, entries, checks, resolved, records, warnings, modes, scale=scale,
                         skip_refused=skip_refused)
    except CliError as err:
        if getattr(err, "payload", None) is None:
            err.payload = payload(**dry_extra())
        raise
    except (KeyboardInterrupt, bridge.Interrupted):
        err = CliError("interrupted", STOPPED_SET)
        err.payload = payload(**dry_extra())
        raise err from None
    except Exception as exc:          # noqa: BLE001 - a fault in this code still reports every entry
        err = CliError("recipe_error", f"{type(exc).__name__}: {exc}")
        err.payload = payload(**dry_extra())
        raise err from None
    done = payload()
    error = ended or results.outcome_error(records, done)
    if error is not None:
        error.payload = done
        raise error
    return done


def _run(session: WriteSession, entries: list[dict], checks: list[dict], resolved: dict[int, dict],
         records: list[dict], warnings: list[str], modes: list[dict], *, scale: bool,
         skip_refused: bool) -> CliError | None:
    """The writes of set_keys, in plan order. Marks the records. Returns the CliError that ended the run, or None
    when the run went to the end or ended without one. A stop is raised, not returned: a stop in a write marks its
    entry first, a stop elsewhere leaves the entry as it was."""
    started = False
    for number, entry in enumerate(entries):
        record = records[number]
        if record["status"] != "not_run":
            continue
        if started and session.breathe():
            warnings += [w for w in session.warnings if w not in warnings]
            frame, found = _check(session, [entry])
            check = _plan_entry(number, entry, modes[number], found[0], frame, scale=scale)
            checks[number] = check
            record["interpreted"], record["moved"], record["warnings"] = (check["interpreted"], list(check["moved"]),
                                                                          list(check["warnings"]))
            if check["skip"] is not None:
                results.mark_skipped(record, results.NOTHING_TO_DO, *check["skip"])
                continue
            if check["refusal"] is not None:
                results.mark_skipped(record, results.REFUSED, check["refusal"][0], check["refusal"][1])
                if skip_refused:
                    continue
                warnings.append(f"The run stopped at {_who(number, entry)}: it was checked again after Premiere was "
                                "given back to other programs, and refused. The entries after it did not run.")
                return None
            if check["write_scale"]:
                resolved.update(_resolve(session, [number], entries, checks))
        started = True
        go_on, error = _write_entry(session, number, entry, checks[number], resolved.get(number), record, warnings)
        if not go_on:
            return error
    return None


def _write_entry(session: WriteSession, number: int, entry: dict, check: dict, found: dict | None, record: dict,
                 warnings: list[str]) -> tuple[bool, CliError | None]:
    """Scale, then Rotation, of one entry; marks its record. Returns (go on, the CliError that ended the run or
    None)."""
    scale_now, scale_written = check["scale_now"], False
    where = _who(number, entry)
    try:
        if check["write_scale"]:
            try:
                result = effects.write_entry(session, _scale_entry(entry, check), found)
            except CliError as err:
                if getattr(err, "partial", None) is not None and not stops.is_stop(err):
                    # write_entry sets partial when the outcome of this Scale is not known: a send after an
                    # earlier one failed (a later attempt, or the put-back), or the first send failed with a code
                    # outside stops.NOT_SENT_CODES (unknown_outcome, bad_reply). What Scale holds is not known,
                    # whatever the code of the failed send says.
                    return _send_failed(CliError("unknown_outcome", err.message), "Scale", where, record, warnings,
                                        check, scale_now, scale_written=True)
                return _send_failed(err, "Scale", where, record, warnings, check, scale_now, scale_written=False)
            scale_now, scale_written = result["now"], result["attempts"] > 0
            if not result["stored"]:
                results.mark_failed(record, f"Scale: {result['error']}. Rotation was not written.",
                                    now=_now(scale_now, check["rotation_now"]))
                return True, None
        try:
            reply = _write_rotation(session, entry, check)
        except CliError as err:
            return _send_failed(err, "Rotation", where, record, warnings, check, scale_now,
                                scale_written=scale_written)
    except (KeyboardInterrupt, bridge.Interrupted):
        results.mark_failed(record, "Stopped by Ctrl-C or a stop signal during a write of this entry.", known=False)
        raise
    if reply["after"] is None:
        results.mark_failed(record, "Rotation: the read back failed after the writes: "
                                    + "; ".join(reply["set_errors"]), known=False)
        warnings.append(f"The run stopped at {where}: Rotation was written but could not be read back, so what it "
                        "holds is not known. Check this clip in Premiere. The entries after it did not run.")
        return False, None
    now = _now(scale_now, _after_now(check, reply))
    error = _rotation_error(check, reply)
    if error is None:
        results.mark_done(record, now)
        if reply["set_errors"]:
            record["warnings"].append("Premiere gave errors that did not change the result: "
                                      + "; ".join(reply["set_errors"]))
    else:
        detail = f" Errors: {'; '.join(reply['set_errors'])}." if reply["set_errors"] else ""
        results.mark_failed(record, f"Rotation: {error}.{detail}", now=now)
    return True, None


def _send_failed(err: CliError, what: str, where: str, record: dict, warnings: list[str], check: dict,
                 scale_now: object, *, scale_written: bool) -> tuple[bool, CliError | None]:
    """A send of this entry failed with err. Marks the record. Returns (go on, err when it ends the run). A stop
    is raised."""
    text = f"{what}: {err.code}: {err.message}"
    written = (f"Scale was written ({scale_now}) before the {what} write failed; it stays written."
               if scale_written and what == "Rotation" else None)
    if stops.is_stop(err):
        if written:
            record["warnings"].append(written)
            record["now"] = _now(scale_now, check["rotation_now"])
        raise err
    if err.code == "precondition":
        results.mark_failed(record, f"{text} Nothing of this {what} write was changed.",
                            now=_now(scale_now, check["rotation_now"]))
        if written:
            record["warnings"].append(written)
        return True, None
    if err.code in stops.NOT_SENT_CODES:
        if written:
            record["warnings"].append(written)
            record["now"] = _now(scale_now, check["rotation_now"])
        warnings.append(f"The run stopped at {where}: the {what} write was not sent ({err.code}: {err.message}). "
                        "This entry and the ones after it did not run.")
        return False, err
    results.mark_failed(record, text, known=False)
    warnings.append(f"The run stopped at {where}: the outcome of the {what} write is not known ({err.code}). Check "
                    "this clip in Premiere. The entries after it did not run.")
    return False, err
