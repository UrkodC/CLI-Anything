"""`live clip list`: a compact map of one sequence, one record per clip.

A read: it takes the bridge lease, runs the read guards, sends core/scripts/clip_list.jsx and shapes its
answer. The script returns the raw fields (tick values as strings); the seconds, the render order and the
ProcAmp flag are made here.
"""
from __future__ import annotations

import re

from cli_anything.premiere.core import jsx
from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.core.session import DEFAULT_HARD_LIMIT_S, SOFT_TIMEOUT_S, guarded_state, run_script
from cli_anything.premiere.errors import CliError

CLIP_LIMIT = 400
# With detail the limit is lower: the keyframed list of a clip has no bound, and a reply must stay under 1 MB.
DETAIL_LIMIT = 100
# Components every clip carries from the start: they are not effects someone applied.
INTRINSIC = ("Opacity", "Motion", "Volume", "Channel Volume", "Panner")
TRACK_TYPES = ("video", "audio", "both")
TICKS_PER_SECOND = 254016000000
DETAIL_FIELDS = ("interpreted_fps", "linked", "keyframed", "transitions")
_TICKS = re.compile(r"-?[0-9]+")


def _whole(value: object, low: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= low


def _tick(value: object) -> str | None:
    """A tick value of the script's reply as a string, or None. A number never passes through as a number."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value)


def _seconds(ticks: object) -> float | None:
    """Seconds of a tick string, rounded to 6 places; None when the script could not read the time."""
    if isinstance(ticks, str) and _TICKS.fullmatch(ticks):
        return round(int(ticks) / TICKS_PER_SECOND, 6)
    return None


def _render_order(effects: object) -> list | None:
    """The effects as they render: the component list reversed, without the built-in components. None
    when the effects could not be read."""
    if not isinstance(effects, list):
        return None
    return [name for name in reversed(effects) if name not in INTRINSIC]


def _procamp_before_lumetri(order: list | None) -> bool | None:
    """True when ProcAmp renders before Lumetri Color (first of each); None unless both are on the clip."""
    if order is None or "ProcAmp" not in order or "Lumetri Color" not in order:
        return None
    return order.index("ProcAmp") < order.index("Lumetri Color")


def _shape(raw: dict) -> dict:
    order = _render_order(raw.get("effects"))
    start, end, in_ticks, out_ticks = (_tick(raw.get(key)) for key in ("start_ticks", "end_ticks", "in_ticks", "out_ticks"))
    shaped = {
        "track_type": raw.get("track_type"), "track": raw.get("track"), "clip": raw.get("clip"),
        "node_id": raw.get("node_id"), "name": raw.get("name"),
        "start_ticks": start, "end_ticks": end, "in_ticks": in_ticks, "out_ticks": out_ticks,
        "start_seconds": _seconds(start), "end_seconds": _seconds(end),
        "enabled": raw.get("enabled"), "speed": raw.get("speed"), "adjustment_layer": raw.get("adjustment_layer"),
        "source": raw.get("source"), "effects": raw.get("effects"),
        "render_order": order, "procamp_before_lumetri": _procamp_before_lumetri(order),
    }
    for field in DETAIL_FIELDS:      # only present when the script was asked for the detail
        if field in raw:
            shaped[field] = raw[field]
    return shaped


def _check_arguments(sequence_id: object, track_type: object, track: object, offset: object, limit: object,
                     detail: bool) -> None:
    if sequence_id is not None and not (isinstance(sequence_id, str) and sequence_id):
        raise CliError("bad_args", "The sequence ID must be a non-empty string. Nothing was sent.")
    if track_type not in TRACK_TYPES:
        raise CliError("bad_args", f"The track type must be video, audio or both, not {track_type!r}. Nothing was sent.")
    if track is not None and not _whole(track, 0):
        raise CliError("bad_args", f"The track must be a whole number of 0 or more, not {track!r}. Nothing was sent.")
    if not _whole(offset, 0):
        raise CliError("bad_args", f"The offset must be a whole number of 0 or more, not {offset!r}. Nothing was sent.")
    most = DETAIL_LIMIT if detail else CLIP_LIMIT
    if not (_whole(limit, 1) and limit <= most):
        why = " With detail, a reply must stay under 1 MB." if detail else ""
        raise CliError("bad_args", f"The limit must be a whole number from 1 to {most}, not {limit!r}.{why} Nothing was sent.")


def list_clips(client: BridgeClient, *, sequence_id: str | None = None, track_type: str = "both",
               track: int | None = None, offset: int = 0, limit: int | None = None, detail: bool = False,
               allow_untested: bool = False, hard_limit_s: float = DEFAULT_HARD_LIMIT_S) -> dict:
    """List the clips of one sequence. Takes the lease itself; changes nothing in Premiere.

    sequence_id None lists the active sequence (the warnings say so). Order: video tracks by index, then audio
    tracks by index, clips by index. `track` keeps one track index of the chosen types; `offset` and `limit`
    cut the list; limit None means CLIP_LIMIT, or DETAIL_LIMIT with detail. detail=True adds interpreted_fps,
    linked, keyframed and transitions to each clip (more calls to Premiere) and allows at most DETAIL_LIMIT clips.

    Returns {"sequence": {"id", "name"}, "clips": [...], "count", "total" (every clip that matches the
    filters), "truncated", "next_offset" (None when nothing follows), "warnings"}. Raises CliError.
    """
    detail = bool(detail)
    if limit is None:
        limit = DETAIL_LIMIT if detail else CLIP_LIMIT
    _check_arguments(sequence_id, track_type, track, offset, limit, detail)
    args = {"sequence_id": sequence_id, "track_type": track_type, "track": track, "offset": offset,
            "limit": limit, "detail": detail}
    with client.lease():
        _state, warnings = guarded_state(client, write=False, project=None, any_project=False,
                                         allow_untested=allow_untested, hard_limit_s=hard_limit_s)
        reply = run_script(client, jsx.load("clip_list"), args, write=False,
                           timeout_s=min(SOFT_TIMEOUT_S, hard_limit_s), hard_limit_s=hard_limit_s, include=("find",))
    sequence = reply.get("sequence") if isinstance(reply, dict) else None
    found = reply.get("clips") if isinstance(reply, dict) else None
    total = reply.get("total") if isinstance(reply, dict) else None
    if (not isinstance(sequence, dict) or not isinstance(found, list) or not _whole(total, 0)
            or not all(isinstance(item, dict) for item in found)):
        raise CliError("bad_reply", "The clip list script returned an answer in an unexpected shape.")
    warnings = list(warnings)
    if reply.get("used_active"):
        warnings.append(f"No --sequence was given, so this lists the active sequence: "
                        f"{sequence.get('name')} ({sequence.get('id')}).")
    shown = [_shape(item) for item in found]
    truncated = bool(shown) and offset + len(shown) < total
    return {"sequence": {"id": sequence.get("id"), "name": sequence.get("name")}, "clips": shown,
            "count": len(shown), "total": total, "truncated": truncated,
            "next_offset": offset + len(shown) if truncated else None, "warnings": warnings}
