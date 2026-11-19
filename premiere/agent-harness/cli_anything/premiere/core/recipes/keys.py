"""`live keys get` and the key math that the keys commands share.

The math is plain Python on numbers, so a plan can be checked with no Premiere open:
- key_ticks puts the keys of a plan on the clip's frame grid, in ticks, and finds keys that would clash;
- static_rule and entry_mode decide, for one plan entry, whether it gets keys, one static value, or nothing.

get_keys is a read: it takes the bridge lease, runs the read guards, sends core/scripts/keys_get.jsx and shapes
its answer. The script returns the raw fields (tick values as strings); the seconds and the shape are made here.

Time of a key. Premiere stores a key in media time: the clip's in point plus the time from the clip start. A plan
and the commands use clip-relative seconds (0 is the first visible frame). The media ticks of a key are
`int(in_ticks) + ticks` of key_ticks (premiere-pro-mcp, bridge/script-builder.js, lines 1917-1943).
"""
from __future__ import annotations

import math

from cli_anything.premiere.core import jsx
from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.core.recipes import effects
from cli_anything.premiere.core.recipes.clips import TICKS_PER_SECOND, _seconds, _tick, _whole
from cli_anything.premiere.core.session import DEFAULT_HARD_LIMIT_S, SOFT_TIMEOUT_S, guarded_state, run_script
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
