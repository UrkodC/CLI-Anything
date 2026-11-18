"""`live effect get` and `live effect set`: the effects of one clip, and writes to their properties.

get_effects is a read: it takes the bridge lease, runs the read guards, sends core/scripts/effect_get.jsx and
shapes its answer. The script returns the raw fields (the start time as a tick string); the seconds and the
sequence ID of the clip block are made here.

set_effects writes one value, or many from a plan, inside one WriteSession: it resolves every entry in one
read (effect_resolve.jsx), refuses the whole plan before the first write when one entry may not be written,
then writes the entries in order, one session.write per attempt (effect_write.jsx), each one read back. It
stops at the first entry that is not stored.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from cli_anything.premiere.core import bridge, jsx
from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.core.recipes.clips import _seconds, _tick, _whole
from cli_anything.premiere.core.session import (DEFAULT_HARD_LIMIT_S, SOFT_TIMEOUT_S, WriteSession, guarded_state,
                                                run_script)
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
                           timeout_s=min(SOFT_TIMEOUT_S, hard_limit_s), hard_limit_s=hard_limit_s, include=("find", "values"))
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


# =====================================================================================================
# live effect set
# =====================================================================================================
VALUE_TOLERANCE = 0.001               # "stored": the value read back is this near the value written (numbers)
REFUSED_LUMETRI_INDEXES = (7, 32, 34)  # second Input LUT (a write breaks it), Look path and Look menu (no render)
LUMETRI_MATCH = "AE.ADBE Lumetri"
NOT_PROOF_LUMETRI_INDEXES = (4, 6)    # Input LUT path and menu: they read back, but only pixels prove the LUT
NOT_PROOF = "Read-back is not proof for this change."
NUMBER_LIMIT = 4294967296             # a number this big or bigger is a packed colour in Premiere, not a value
WRITABLE_KINDS = ("number", "string", "boolean")
PLAN_KEYS = ("sequence_id", "sequence_name", "writes")
ENTRY_KEYS = ("track", "clip", "node_id", "effect", "effect_index", "index", "name", "value", "expect_old")


def near_value(value: float) -> float:
    """value moved toward zero by max(abs(value) * 0.04, 0.001), rounded to 3 places (never past zero).
    The value to try when a negative value does not store twice: -2.6 gives -2.496."""
    size = abs(value)
    nearer = round(max(size - max(size * 0.04, 0.001), 0.0), 3)
    return math.copysign(nearer, value) if nearer else 0.0


def same_value(a: object, b: object) -> bool:
    """The one rule for "stored": two finite numbers within VALUE_TOLERANCE, two equal strings, two equal
    booleans. A bool is never the same as a number, and None is never the same as anything."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b
    if _number(a) and _number(b):
        try:
            return math.isfinite(a) and math.isfinite(b) and abs(a - b) <= VALUE_TOLERANCE
        except OverflowError:         # an integer too big for a float: never a value of Premiere
            return False
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return False


# ---------- the plan ----------
def _plan_error(code: str, where: str, why: str) -> CliError:
    return CliError(code, f"{where}{why} Nothing was sent.")


def _plain_value(value: object) -> bool:
    """A value this command can write: a number (not a bool), a string, or a boolean."""
    return isinstance(value, (bool, str)) or _number(value)


def _value_fault(value: object) -> str | None:
    """Why a value cannot be written in any case (code refused_parameter), or None."""
    if _number(value):
        if isinstance(value, float) and not math.isfinite(value):
            return f"the value {value} is not a finite number."
        if abs(value) >= NUMBER_LIMIT:
            return f"the value {value} is {NUMBER_LIMIT} or more in size. Premiere reads a number that big as a packed colour."
    return None


def _check_entry(entry: object, number: int, code: str) -> dict:
    where = f"The plan: entry {number}: "
    if not isinstance(entry, dict):
        raise _plan_error(code, where, "it must be a JSON object.")
    if "track_type" in entry:
        raise _plan_error(code, where, 'a plan writes to video tracks only, so it has no "track_type".')
    unknown = [key for key in entry if key not in ENTRY_KEYS]
    if unknown:
        raise _plan_error(code, where, f"{unknown[0]!r} is not a key this command knows. It knows: {', '.join(ENTRY_KEYS)}.")
    out: dict = {}
    if "node_id" in entry:
        if "track" in entry or "clip" in entry:
            raise _plan_error(code, where, 'name the clip with "node_id", or with "track" and "clip", not both.')
        if not _text(entry["node_id"]) or _LONE_HALF.search(entry["node_id"]):
            raise _plan_error(code, where, '"node_id" must be a non-empty string.')
        out["node_id"] = entry["node_id"]
    else:
        for key in ("track", "clip"):
            if key not in entry:
                raise _plan_error(code, where, f'"{key}" is missing. Name the clip with "track" and "clip", or with "node_id".')
            if not _whole(entry[key], 0):
                raise _plan_error(code, where, f'"{key}" must be a whole number of 0 or more, not {entry[key]!r}.')
            out[key] = entry[key]
    if not _text(entry.get("effect")) or _LONE_HALF.search(entry["effect"]):
        raise _plan_error(code, where, '"effect" must be a non-empty string: the display name or the match name.')
    out["effect"] = entry["effect"]
    if entry.get("effect_index") is not None:
        if not _whole(entry["effect_index"], 0):
            raise _plan_error(code, where, f'"effect_index" must be a whole number of 0 or more, not {entry["effect_index"]!r}.')
        out["effect_index"] = entry["effect_index"]
    if "index" not in entry or not _whole(entry["index"], 0):
        raise _plan_error(code, where, f'"index" must be a whole number of 0 or more, not {entry.get("index")!r}.')
    out["index"] = entry["index"]
    if not isinstance(entry.get("name"), str) or _LONE_HALF.search(entry["name"]):
        raise _plan_error(code, where, '"name" must be a string: the exact name of the property at that index '
                                       '(it may be " " or "" in Lumetri Color).')
    out["name"] = entry["name"]
    for key in ("value", "expect_old"):
        if key not in entry:
            if key == "value":
                raise _plan_error(code, where, '"value" is missing.')
            continue
        value = entry[key]
        if not _plain_value(value) or (isinstance(value, str) and _LONE_HALF.search(value)):
            raise _plan_error(code, where, f'"{key}" must be a number, a string or true or false, not {json.dumps(value)}.')
        fault = _value_fault(value)
        if fault:
            raise CliError("refused_parameter" if key == "value" else code,
                           f"{where}{fault} Nothing was sent.")
        out[key] = value
    return out


def check_plan(plan: object, *, code: str = "bad_plan") -> dict:
    """The plan in the shape set_effects uses: {"sequence_id", "sequence_name" (or None), "writes": [entry]}.
    An entry has "track" and "clip" (video tracks, counted from 0) or "node_id", then "effect", "index", "name",
    "value", and when given "effect_index" and "expect_old". Raises CliError(code) with the place of the fault,
    or CliError("refused_parameter") for a number that can never be written. Sends nothing."""
    if not isinstance(plan, dict):
        raise _plan_error(code, "The plan: ", 'it must be a JSON object: {"sequence_id": ..., "writes": [...]}.')
    unknown = [key for key in plan if key not in PLAN_KEYS]
    if unknown:
        raise _plan_error(code, "The plan: ", f"{unknown[0]!r} is not a key this command knows. It knows: {', '.join(PLAN_KEYS)}.")
    if not _text(plan.get("sequence_id")) or _LONE_HALF.search(plan["sequence_id"]):
        raise _plan_error(code, "The plan: ", '"sequence_id" must be a non-empty string (see `live state`).')
    name = plan.get("sequence_name")
    if name is not None and (not _text(name) or _LONE_HALF.search(name)):
        raise _plan_error(code, "The plan: ", '"sequence_name" must be a non-empty string when it is given.')
    writes = plan.get("writes")
    if not isinstance(writes, list) or not writes:
        raise _plan_error(code, "The plan: ", '"writes" must be a list with at least one entry.')
    return {"sequence_id": plan["sequence_id"], "sequence_name": name,
            "writes": [_check_entry(entry, number, code) for number, entry in enumerate(writes)]}


def _no_constant(text: str) -> object:
    raise ValueError(f"{text} is not a JSON value")


def load_plan(path) -> dict:
    """Read and check a plan file. Raises CliError("bad_plan") with the place of the fault."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CliError("bad_plan", f"Cannot read the plan file {path}: {exc}. Nothing was sent.") from None
    try:
        plan = json.loads(text, parse_constant=_no_constant)
    except (ValueError, RecursionError) as exc:
        raise CliError("bad_plan", f"The plan file is not valid JSON: {exc}. Nothing was sent.") from None
    return check_plan(plan)


# ---------- resolve and check ----------
def _target(entry: dict) -> dict:
    if "node_id" in entry:
        return {"node_id": entry["node_id"]}
    return {"track_type": "video", "track": entry["track"], "clip": entry["clip"]}


def _record_ok(record: object, entry: dict) -> bool:
    """One record of effect_resolve.jsx: the blocks, a node ID that is plain text, the index and name that were
    asked for, and a property that passes the checks of effect get."""
    if not isinstance(record, dict):
        return False
    clip, effect, prop = record.get("clip"), record.get("effect"), record.get("property")
    if not (isinstance(clip, dict) and isinstance(effect, dict) and isinstance(prop, dict)):
        return False
    node_id = clip.get("node_id")
    if not _text(node_id) or _LONE_HALF.search(node_id):
        return False
    if not (_text_or_none(clip.get("name")) and _whole(clip.get("track"), 0) and _whole(clip.get("clip"), 0)):
        return False
    if clip.get("track_type") != "video":          # effect set writes video clips only
        return False
    if not (_whole(effect.get("effect_index"), 0) and _text_or_none(effect.get("name"))
            and _text_or_none(effect.get("match_name"))):
        return False
    if "effect_index" in entry and effect["effect_index"] != entry["effect_index"]:
        return False
    if entry["effect"] not in (effect.get("name"), effect.get("match_name")):
        return False
    if "track" in entry and (clip["track"], clip["clip"]) != (entry["track"], entry["clip"]):
        return False
    return _property_ok(prop) and prop.get("index") == entry["index"] and prop.get("name") == entry["name"]


def resolve_entries(session: WriteSession, entries: list[dict]) -> list[dict]:
    """Find the target of every entry in one read (effect_resolve.jsx). session.target() must have been called.

    entries are checked plan entries (check_plan). Returns one record per entry, in order:
    {"entry", "clip": {"node_id", "name", "track_type", "track", "clip", "start_ticks", "start_seconds"}, "effect",
    "effect_index", "match_name", "index", "name", "value_now", "kind", "keyframed"}, plus the key "sequence"
    ({"id", "name"}) on every record. The first entry that cannot be found raises CliError("precondition",
    "entry <n>: ..."); a reply of the wrong shape raises CliError("bad_reply"). Changes nothing."""
    sequence_id = session.expect.get("sequence_id")
    if not _text(sequence_id):
        raise CliError("bad_args", "resolve_entries needs session.target(sequence_id) first. Nothing was sent.")
    args = {"sequence_id": sequence_id,
            "entries": [{"target": _target(entry), "effect": entry["effect"], "effect_index": entry.get("effect_index"),
                         "index": entry["index"], "name": entry["name"]} for entry in entries]}
    reply = session.read(jsx.load("effect_resolve"), args, include=("find", "values"))
    found = reply.get("entries") if isinstance(reply, dict) else None
    sequence = reply.get("sequence") if isinstance(reply, dict) else None
    if (not isinstance(sequence, dict) or not isinstance(found, list) or len(found) != len(entries)
            or not all(_record_ok(record, entry) for record, entry in zip(found, entries))):
        raise CliError("bad_reply", "The resolve script returned an answer in an unexpected shape. Nothing was written.")
    resolved = []
    for number, record in enumerate(found):
        clip, effect, prop = record["clip"], record["effect"], _property(record["property"])
        start = _tick(clip.get("start_ticks"))
        resolved.append({"entry": number,
                         "clip": {"node_id": clip["node_id"], "name": _name(clip.get("name")),
                                  "track_type": clip["track_type"], "track": clip["track"],
                                  "clip": clip["clip"], "start_ticks": start, "start_seconds": _seconds(start)},
                         "effect": _name(effect.get("name")), "effect_index": effect["effect_index"],
                         "match_name": _name(effect.get("match_name")), "index": prop["index"], "name": prop["name"],
                         "value_now": prop["value"], "kind": prop["kind"], "keyframed": prop["keyframed"],
                         "sequence": {"id": _name(sequence.get("id")), "name": _name(sequence.get("name"))}})
    return resolved


def _refused(number: int, why: str) -> CliError:
    return CliError("refused_parameter", f"entry {number}: {why} Nothing was changed.")


def _gyroflow(*names: object) -> bool:
    return any(isinstance(name, str) and "gyroflow" in name.lower() for name in names)


def check_entries(entries: list[dict], resolved: list[dict]) -> None:
    """Refuse the whole plan, before any write, when one entry may not be written. Raises
    CliError("refused_parameter") naming the entry, or CliError("precondition") for an expect_old that does not
    match the value now. The checks use what Premiere said (the resolved names), not the plan's words."""
    for number, (entry, found) in enumerate(zip(entries, resolved)):
        where = f"{found['effect']} index {found['index']}"
        if found["keyframed"] is not False:
            raise _refused(number, f"{where} has keyframes, or its keyframes could not be read. This command "
                                   "writes only properties without keyframes.")
        if _gyroflow(found["effect"], found["match_name"], entry["effect"]):
            raise _refused(number, f"{where} belongs to Gyroflow. Gyroflow ignores scripted writes, and a write to it "
                                   "can leave a recovery.")
        if found["match_name"] == LUMETRI_MATCH and found["index"] in REFUSED_LUMETRI_INDEXES:
            raise _refused(number, f"Lumetri Color index {found['index']} is never written (indexes "
                                   f"{', '.join(str(i) for i in REFUSED_LUMETRI_INDEXES)}): index 7 is the second "
                                   "Input LUT, and 32 and 34 are the Look slots, which do not render a LUT.")
        kind, value = found["kind"], entry["value"]
        if kind not in WRITABLE_KINDS:
            raise _refused(number, f"{where} holds a value of kind {kind}, and only numbers, strings and booleans "
                                   "can be written. Arrays (such as Position) and colours are refused, and so is a "
                                   "value that could not be read.")
        fits = {"number": _number(value) and not isinstance(value, bool), "string": isinstance(value, str),
                "boolean": isinstance(value, bool)}[kind]
        if not fits:
            raise _refused(number, f"{where} holds a {kind}, and the value {json.dumps(value, ensure_ascii=False)} "
                                   f"is not a {kind}.")
        fault = _value_fault(value)
        if fault:
            raise _refused(number, fault)
        if "expect_old" in entry and not same_value(entry["expect_old"], found["value_now"]):
            raise CliError("precondition", f"entry {number}: {where} is {_shown(found['value_now'])} now, not "
                                           f"{_shown(entry['expect_old'])} as the plan expects. Nothing was changed.")


# ---------- write ----------
def _shown(value: object) -> str:
    """A value in a message: JSON for strings and booleans, plain for numbers, "nothing" for None."""
    if value is None:
        return "nothing"
    if _number(value):
        return repr(round(value, 6)) if isinstance(value, float) else str(value)
    return json.dumps(_name(value) if isinstance(value, str) else value, ensure_ascii=False)


def _reply_ok(reply: object) -> bool:
    """The shape of an effect_write.jsx reply, with the types this recipe trusts."""
    if not isinstance(reply, dict) or not all(key in reply for key in ("before", "zero_first", "set_error", "value",
                                                                      "read_error")):
        return False
    plain = all(reply[key] is None or _plain_value(reply[key]) for key in ("before", "value"))
    texts = all(reply[key] is None or isinstance(reply[key], str) for key in ("set_error", "read_error"))
    return plain and texts and isinstance(reply["zero_first"], bool)


def _attempt_error(reply: dict, wanted: object) -> str | None:
    """None when this attempt stored `wanted`, else why not."""
    if reply["set_error"] is not None:
        return _name(reply["set_error"])
    if reply["read_error"] is not None:
        return f"the read back failed: {_name(reply['read_error'])}"
    if not same_value(reply["value"], wanted):
        return f"wanted {_shown(wanted)}, read back {_shown(reply['value'])}"
    return None


# Codes of a session.write failure that mean the script was not run, or refused before its first write.
NOTHING_SENT_CODES = ("precondition", "recovery_required", "bridge_down", "script_too_large", "bad_args")
PUT_BACK_FAILED = "Putting the old value back failed: "


def _send(session: WriteSession, body: str, args: dict, value: object, where: str, wrote: bool) -> dict:
    """One session.write of effect_write.jsx with this value, its reply checked. A CliError is raised again with
    `where` in front; "the write may have landed" is added when a write may have gone out (this send, or an
    earlier one of the same entry: wrote)."""
    try:
        reply = session.write(body, {**args, "value": value})
    except CliError as err:
        landed = wrote or err.code not in NOTHING_SENT_CODES
        tail = " Check this property in Premiere: the write may have landed." if landed else ""
        raise CliError(err.code, f"{where}: {err.message}{tail}") from None
    if not _reply_ok(reply):
        raise CliError("bad_reply", f"{where}: the write script returned an answer in an unexpected shape. "
                                    "Check this property in Premiere: the write may have landed.")
    return reply


def write_entry(session: WriteSession, entry: dict, resolved: dict) -> dict:
    """Write one checked entry to the target that resolve_entries found for it, and read it back. One
    session.write per attempt. A number below zero gets up to three attempts: the value, the value again, then
    near_value(value); a near value that lands is reported as "landed", and the entry is not stored. Any other
    value gets one attempt. An attempt failed when the write threw (set_error, also a zero step that did not
    take) or when the read back is not same_value as the value written.

    When nothing landed and the last read back shows that the property no longer holds its value from before
    the first attempt, that old value is written back once (one more session.write with the same script; it
    counts as put back only when it reads back). No put-back after a near value that landed.

    Returns {"entry", "requested", "before", "value", "stored", "attempts", "zero_first", "landed", "restored",
    "now", "error"}. value is the last read back of the attempts, now the last value read from the property
    (after a put-back, its read back). restored is True or False when a put-back ran, else None.

    A CliError of session.write, or a reply of the wrong shape (bad_reply), is raised with the entry and the
    attempt in its message. It carries `partial`: a record with what is known and "stored": None (the outcome is
    unknown), or None when the first attempt was refused before anything was sent. No put-back runs after it."""
    number, wanted = resolved["entry"], entry["value"]
    tries = [wanted, wanted, near_value(wanted)] if _number(wanted) and wanted < 0 else [wanted]
    args = {"sequence_id": session.expect.get("sequence_id"), "target": _target(entry),
            "node_id": resolved["clip"]["node_id"], "effect": entry["effect"],
            "effect_index": entry.get("effect_index"), "found_effect_index": resolved["effect_index"],
            "index": entry["index"], "name": entry["name"], "tolerance": VALUE_TOLERANCE}
    body = jsx.load("effect_write")
    result = {"entry": number, "requested": wanted, "before": None, "value": None, "stored": False, "attempts": 0,
              "zero_first": False, "landed": None, "restored": None, "now": None, "error": None}
    errors: list[str] = []
    raw_before = raw_value = None

    def unknown(err: CliError, sent: bool) -> CliError:
        if sent or err.code not in NOTHING_SENT_CODES:
            err.partial = {**result, "stored": None, "landed": None, "restored": None, "now": None,
                           "error": f"{err.code}: {err.message}"}
        else:
            err.partial = None
        return err

    for attempt, value in enumerate(tries, 1):
        try:
            reply = _send(session, body, args, value, f"entry {number}, attempt {attempt} of up to {len(tries)}",
                          wrote=attempt > 1)
        except CliError as err:
            raise unknown(err, attempt > 1) from None
        result["attempts"] = attempt
        if attempt == 1:
            raw_before = reply["before"]
            result["before"] = _name(raw_before)
        raw_value = reply["value"]
        result["zero_first"] = result["zero_first"] or reply["zero_first"]
        result["value"] = result["now"] = _name(raw_value)
        error = _attempt_error(reply, value)
        if error is None:
            if attempt < 3:
                result["stored"] = True
                result["error"] = None
            else:
                result["landed"] = value
                result["error"] = (f"{_shown(wanted)} did not store ({'; '.join(errors)}); the near value "
                                   f"{_shown(value)} landed.")
            return result
        errors.append(error)
        result["error"] = "; ".join(errors)
    # Nothing landed. Put the old value back when the property shows another one now.
    if raw_before is None or raw_value is None or same_value(raw_value, raw_before):
        return result
    fault = _value_fault(raw_before)
    if fault:
        result["restored"] = False
        result["error"] += f". {PUT_BACK_FAILED}{fault}"
        return result
    try:
        reply = _send(session, body, args, raw_before, f"entry {number}, putting the old value back", wrote=True)
    except CliError as err:
        err.partial = {**result, "stored": False, "restored": None, "now": None,
                       "error": f"{result['error']}. {PUT_BACK_FAILED}{err.code}: {err.message}"}
        raise err from None
    result["now"] = _name(reply["value"])
    back_error = _attempt_error(reply, raw_before)
    result["restored"] = back_error is None
    if back_error is not None:
        result["error"] += f". {PUT_BACK_FAILED}{back_error}"
    return result


# ---------- the whole command ----------
def _public(record: dict) -> dict:
    """A resolved record as the payload shows it."""
    clip = record["clip"]
    return {"entry": record["entry"],
            "clip": {"node_id": clip["node_id"], "name": clip["name"], "track": clip["track"], "clip": clip["clip"],
                     "start_seconds": clip["start_seconds"]},
            "effect": record["effect"], "effect_index": record["effect_index"], "index": record["index"],
            "name": record["name"], "value_now": record["value_now"]}


def _with_payload(err: CliError, payload: dict) -> CliError:
    """The error, carrying what ran so far (results, ran, total, resolved) for the failure output."""
    err.payload = payload
    return err


def set_effects(client: BridgeClient, plan: dict, *, dry_run: bool = False, project: str | None = None,
                any_project: bool = False, allow_untested: bool = False,
                hard_limit_s: float = DEFAULT_HARD_LIMIT_S) -> dict:
    """Write the plan's values in one WriteSession. See check_plan for the plan.

    Order: check the plan (nothing sent), take the session (lease and write guards), target the sequence,
    resolve every entry in one read, refuse the whole plan when one entry may not be written, then (unless
    dry_run) write the entries in order. Between entries the session breathes; when it gave Premiere back, every
    entry is resolved again and a clip whose node ID changed stops the batch with target_moved. The batch stops
    at the first entry that is not stored.

    Returns {"sequence", "resolved", "results", "ran", "total", "stored", "rendered": None, "warnings"}; with
    dry_run {"dry_run": True, "sequence", "resolved", "planned", "warnings"}. "stored" is True only when every
    entry ran and every entry is stored. A CliError raised after the plan check carries `payload` (results, ran,
    total, resolved) so the caller sees what landed.
    """
    plan = check_plan(plan)
    entries = plan["writes"]
    total = len(entries)
    resolved: list[dict] = []
    results: list[dict] = []
    warnings: list[str] = []

    def progress() -> dict:
        return {"results": results, "ran": sum(r["stored"] is not None for r in results), "total": total,
                "resolved": [_public(r) for r in resolved], "warnings": warnings}

    try:
        with WriteSession(client, project=project, any_project=any_project, allow_untested=allow_untested,
                          hard_limit_s=hard_limit_s) as session:
            warnings += session.warnings
            session.target(plan["sequence_id"], plan["sequence_name"])
            resolved = resolve_entries(session, entries)
            check_entries(entries, resolved)
            sequence = resolved[0]["sequence"]
            if dry_run:
                planned = [{"entry": r["entry"], "value_now": r["value_now"], "requested": e["value"],
                            "crosses_zero": _number(r["value_now"]) and r["value_now"] > 0
                            and _number(e["value"]) and e["value"] < 0}
                           for e, r in zip(entries, resolved)]
                return {"dry_run": True, "sequence": sequence, "resolved": [_public(r) for r in resolved],
                        "planned": planned, "warnings": warnings}
            for number, entry in enumerate(entries):
                if number and session.breathe():
                    warnings += [w for w in session.warnings if w not in warnings]
                    again = resolve_entries(session, entries)
                    for old, new in zip(resolved, again):
                        if old["clip"]["node_id"] != new["clip"]["node_id"]:
                            raise CliError("target_moved", f"entry {old['entry']}: the clip at this place was "
                                                           f"{old['clip']['node_id']} and is {new['clip']['node_id']} "
                                                           "now. Nothing more was written.")
                        if old["effect_index"] != new["effect_index"]:
                            raise CliError("target_moved", f"entry {old['entry']}: the effect was at index "
                                                           f"{old['effect_index']} and is at {new['effect_index']} "
                                                           "now. Nothing more was written.")
                lut = resolved[number]["match_name"] == LUMETRI_MATCH and entry["index"] in NOT_PROOF_LUMETRI_INDEXES
                try:
                    result = write_entry(session, entry, resolved[number])
                except CliError as err:
                    partial = getattr(err, "partial", None)
                    if partial is not None:
                        results.append(partial)
                        if lut:
                            warnings.append(f"entry {number} (Lumetri Color index {entry['index']}): {NOT_PROOF}")
                    raise
                results.append(result)
                if lut:
                    warnings.append(f"entry {number} (Lumetri Color index {entry['index']}): {NOT_PROOF}")
                if not result["stored"]:
                    break
    except CliError as err:
        raise _with_payload(err, progress()) from None
    except (KeyboardInterrupt, bridge.Interrupted):
        raise _with_payload(CliError("interrupted", "Stopped by Ctrl-C or a stop signal. Check Premiere before "
                                                    "another edit."), progress()) from None
    stored = len(results) == total and all(r["stored"] is True for r in results)
    return {"sequence": sequence, "resolved": [_public(r) for r in resolved], "results": results, "ran": len(results),
            "total": total, "stored": stored, "rendered": None, "warnings": warnings}
