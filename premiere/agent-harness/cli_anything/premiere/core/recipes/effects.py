"""`live effect get`: the effects of one clip and their properties, by index.

A read: it takes the bridge lease, runs the read guards, sends core/scripts/effect_get.jsx and shapes its
answer. The script returns the raw fields (the start time as a tick string); the seconds and the sequence ID
of the clip block are made here.
"""
from __future__ import annotations

import re

from cli_anything.premiere.core import jsx
from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.core.recipes.clips import _seconds, _tick, _whole
from cli_anything.premiere.core.session import DEFAULT_HARD_LIMIT_S, SOFT_TIMEOUT_S, guarded_state, run_script
from cli_anything.premiere.errors import CliError

TRACK_TYPES = ("video", "audio")
# What a property read can be: "color" is [alpha, red, green, blue], "array" is a list of finite numbers (a
# Motion Position is [0.5, 0.5]), "unreadable" has the value None.
KINDS = ("number", "string", "boolean", "color", "array", "unreadable")
_TARGET_KEYS = ("track_type", "track", "clip")
# Half of a surrogate pair. The panel writes every character above 0x7E as \uXXXX, one UTF-16 unit at a time, so a
# real character outside the BMP arrives as a pair that json.loads joins into one code point, and only a
# lone half (the raw bytes of a Lumetri Blob, say) is left in a string. A lone half cannot be written out.
_LONE_HALF = re.compile("[\ud800-\udfff]")
_REPLACEMENT = "\ufffd"


def _text(value: object) -> bool:
    return isinstance(value, str) and value != ""


def _bad(why: str) -> CliError:
    return CliError("bad_args", f"{why} Nothing was sent.")


def _check_target(target: object) -> dict:
    """The target as the script takes it: {"node_id"} or {"track_type", "track", "clip"} (video by default)."""
    if not isinstance(target, dict):
        raise _bad(f"The target must be a dict, not {type(target).__name__}.")
    if "node_id" in target:
        if set(target) != {"node_id"}:
            raise _bad("The target is a node ID, or a track and a clip, not both.")
        if not _text(target["node_id"]):
            raise _bad("The node ID must be a non-empty string.")
        return {"node_id": target["node_id"]}
    extra = sorted(set(target) - set(_TARGET_KEYS))
    if extra:
        raise _bad(f"The target has a key this recipe does not know: {extra[0]!r}.")
    if "track" not in target or "clip" not in target:
        raise _bad("The target needs a node ID, or a track and a clip.")
    track_type = target.get("track_type")
    if track_type is None:
        track_type = "video"
    if track_type not in TRACK_TYPES:
        raise _bad(f"The track type must be video or audio, not {track_type!r}.")
    for key in ("track", "clip"):
        if not _whole(target[key], 0):
            raise _bad(f"The {key} must be a whole number of 0 or more, not {target[key]!r}.")
    return {"track_type": track_type, "track": target["track"], "clip": target["clip"]}


def _check_arguments(sequence_id: object, target: object, effect: object, effect_index: object) -> dict:
    if sequence_id is not None and not _text(sequence_id):
        raise _bad("The sequence ID must be a non-empty string.")
    checked = _check_target(target)
    if effect is not None and not _text(effect):
        raise _bad("The effect must be a non-empty string.")
    if effect_index is not None and not _whole(effect_index, 0):
        raise _bad(f"The effect index must be a whole number of 0 or more, not {effect_index!r}.")
    return checked


def _name(value: object) -> object:
    """A name from Premiere (clip, effect, match name, property, sequence, node ID) with each lone surrogate
    replaced by U+FFFD. Anything that is not a string stays as it is."""
    return _LONE_HALF.sub(_REPLACEMENT, value) if isinstance(value, str) else value


def _property(raw: dict) -> dict:
    kind, value = raw.get("kind"), raw.get("value")
    if kind == "string" and _LONE_HALF.search(value):       # not text: raw binary data, such as a Lumetri Blob
        kind, value = "unreadable", None
    return {"index": raw.get("index"), "name": _name(raw.get("name")), "value": value,
            "kind": kind, "keyframed": raw.get("keyframed")}


def _effect(raw: dict) -> dict:
    props = raw.get("properties")
    return {"effect_index": raw.get("effect_index"), "name": _name(raw.get("name")),
            "match_name": _name(raw.get("match_name")),
            "properties": None if props is None else [_property(item) for item in props]}


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fits(prop: dict) -> bool:
    """True when the value of a property is the sort of value its kind promises."""
    kind, value = prop.get("kind"), prop.get("value")
    if kind == "unreadable":
        return value is None
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "number":
        return _number(value)
    if not isinstance(value, list) or not all(_number(item) for item in value):
        return False
    return len(value) == 4 if kind == "color" else kind == "array"


def _text_or_none(value: object) -> bool:
    return value is None or isinstance(value, str)


def _property_ok(prop: object) -> bool:
    """A property record with a whole-number index, a name that is a text or None, a keyframed flag that is
    a bool or None, and a value that fits its kind."""
    return (isinstance(prop, dict) and prop.get("kind") in KINDS and _fits(prop) and _whole(prop.get("index"), 0)
            and _text_or_none(prop.get("name"))
            and (prop.get("keyframed") is None or isinstance(prop.get("keyframed"), bool)))


def _well_formed(reply: object) -> bool:
    """True when the script's answer has the shape this recipe reads: the blocks, whole-number indexes, names
    that are texts (or None where Premiere would not say), and a value that fits the kind of each property."""
    if not isinstance(reply, dict) or not isinstance(reply.get("sequence"), dict) or not isinstance(reply.get("clip"), dict):
        return False
    found = reply.get("effects")
    if not isinstance(found, list) or not all(isinstance(item, dict) for item in found):
        return False
    for item in found:
        if (not _whole(item.get("effect_index"), 0) or not _text_or_none(item.get("name"))
                or not _text_or_none(item.get("match_name"))):
            return False
        props = item.get("properties")
        if props is not None and (not isinstance(props, list) or not all(_property_ok(prop) for prop in props)):
            return False
    return True


def get_effects(client: BridgeClient, target: dict, *, effect: str | None = None, effect_index: int | None = None,
                sequence_id: str | None = None, allow_untested: bool = False,
                hard_limit_s: float = DEFAULT_HARD_LIMIT_S) -> dict:
    """Read the effects of one clip, with every property of each effect. Takes the lease itself; changes nothing.

    target is {"node_id": ID} or {"track": N, "clip": N} with an optional "track_type" ("video" by default,
    or "audio"); the numbers count from 0. sequence_id None reads the active sequence (the warnings say so).
    effect keeps the effects whose display name or match name is that (all of them when it occurs twice);
    effect_index keeps the effect at that index of the clip's effect list; with both, the name at that index
    must match or the script refuses with a "precondition" error.

    Returns {"clip": {"sequence_id", "track_type", "track", "clip", "node_id", "name", "start_ticks",
    "start_seconds"}, "effects": [{"effect_index", "name", "match_name", "properties": [{"index", "name",
    "value", "kind", "keyframed"}] or None}], "warnings"}. Raises CliError.

    The output is always writable as text: a lone surrogate in a string value (raw binary data) makes the
    property "unreadable" with value None, and one in a name becomes U+FFFD.
    """
    args = {"sequence_id": sequence_id, "target": _check_arguments(sequence_id, target, effect, effect_index),
            "effect": effect, "effect_index": effect_index}
    with client.lease():
        _state, warnings = guarded_state(client, write=False, project=None, any_project=False,
                                         allow_untested=allow_untested, hard_limit_s=hard_limit_s)
        reply = run_script(client, jsx.load("effect_get"), args, write=False,
                           timeout_s=min(SOFT_TIMEOUT_S, hard_limit_s), hard_limit_s=hard_limit_s, include=("find",))
    if not _well_formed(reply):
        raise CliError("bad_reply", "The effect script returned an answer in an unexpected shape.")
    sequence, raw = reply["sequence"], reply["clip"]
    warnings = list(warnings)
    if reply.get("used_active"):
        warnings.append(f"No --sequence was given, so this reads the active sequence: "
                        f"{_name(sequence.get('name'))} ({_name(sequence.get('id'))}).")
    start = _tick(raw.get("start_ticks"))
    return {"clip": {"sequence_id": _name(sequence.get("id")), "track_type": raw.get("track_type"),
                     "track": raw.get("track"), "clip": raw.get("clip"), "node_id": _name(raw.get("node_id")),
                     "name": _name(raw.get("name")),
                     "start_ticks": start, "start_seconds": _seconds(start)},
            "effects": [_effect(item) for item in reply["effects"]], "warnings": warnings}
