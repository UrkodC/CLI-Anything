"""The recipe commands of the `live` group: `live clip list`, `live effect get`, and the commands that later tasks add.

live_cli.py imports this module as its last line, so the `live` group exists when the commands attach.
The logic of each command is in core/recipes; this file holds only the click side.

To add a command: write the logic as a function in core/recipes/<name>.py that takes a BridgeClient and
returns a dict, then add a command here with the same decorator order as `live state` in live_cli.py:
the command's own options, `@live_options`, `@json_option`, `@click.pass_context`, `@stops_cleanly(...)`
(STOPPED_LOOK for a read, STOPPED_RUN for a write). Take the client with `_client(lease_wait)`, call the
recipe inside `try`, turn a CliError into `_fail(ctx, err)`, and print with `_emit(ctx, payload, text)`.
"""
from __future__ import annotations

import click

from cli_anything.premiere.core.recipes import clips, effects
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.live_cli import STOPPED_LOOK, _client, _warning_lines, live, live_options, stops_cleanly
from cli_anything.premiere.premiere_cli import _emit, _fail, json_option


@live.group("clip")
def live_clip() -> None:
    """Look at the clips of a sequence in the running Premiere."""


def _seconds_text(value: object) -> str:
    return "?" if value is None else f"{value:.3f}"


def _clip_line(clip: dict) -> str:
    """V1[0] 000f4243 0.000-6.006 testclip.mp4 | ProcAmp > ASC CDL > Lumetri Color"""
    kind = "V" if clip.get("track_type") == "video" else "A"
    track = clip.get("track")
    label = f"{kind}{track + 1}" if isinstance(track, int) else f"{kind}?"
    line = (f"{label}[{clip.get('clip')}] {clip.get('node_id') or '-'} "
            f"{_seconds_text(clip.get('start_seconds'))}-{_seconds_text(clip.get('end_seconds'))} "
            f"{'?' if clip.get('name') is None else clip.get('name')}")
    if clip.get("enabled") is False:
        line += " [disabled]"
    order = clip.get("render_order")
    if order:
        line += " | " + " > ".join(str(name) for name in order)
    return line


def _clip_text(payload: dict) -> str:
    seq = payload["sequence"]
    head = f"{seq['name']} ({seq['id']}): "
    lines = [head + (f"{payload['count']} of {payload['total']} clips" if payload["total"] else "no clips match")]
    lines += [_clip_line(clip) for clip in payload["clips"]]
    if payload["truncated"]:
        lines.append(f"More clips follow. Use --offset {payload['next_offset']} to see them, or --track to look at one track.")
    lines += _warning_lines(payload["warnings"])
    return "\n".join(lines)


@live_clip.command("list")
@click.option("--sequence", "sequence_id", metavar="ID",
              help="ID of the sequence (see `live state`). Without it, the active sequence is listed and the warnings say so.")
@click.option("--track-type", "track_type", type=click.Choice(clips.TRACK_TYPES), default="both", show_default=True,
              help="Which tracks to list.")
@click.option("--track", type=click.IntRange(min=0), metavar="N",
              help="Only the track with this index, counted from 0 (V1 and A1 are track 0).")
@click.option("--offset", type=click.IntRange(min=0), default=0, show_default=True, metavar="N",
              help="Skip this many clips. The clips run video tracks first, then audio tracks.")
@click.option("--limit", type=click.IntRange(min=1, max=clips.CLIP_LIMIT), metavar="N",
              help=f"Most clips to list. The default is {clips.CLIP_LIMIT}, or {clips.DETAIL_LIMIT} with --detail.")
@click.option("--detail", is_flag=True,
              help="Also show the interpreted frame rate, the linked clips, the keyframed properties and the "
                   "transitions of each clip. This costs more calls to Premiere, so --limit is at most "
                   f"{clips.DETAIL_LIMIT} with it (a reply must stay under 1 MB).")
@live_options
@json_option
@click.pass_context
@stops_cleanly(STOPPED_LOOK)
def live_clip_list(ctx: click.Context, sequence_id: str | None, track_type: str, track: int | None, offset: int,
                   limit: int | None, detail: bool, lease_wait: float, timeout: float, allow_untested: bool) -> None:
    """List the clips of one sequence, one line per clip. Only reads: nothing in the project changes.

    The clips come in this order: video tracks, then audio tracks, each by track and clip number. A
    line shows the track (V1, A1: the first track is 0 for --track), the clip number on it, the node ID,
    the start and end in seconds, the name and the effects in the order they render.

    A long timeline is cut at --limit clips. Use --track to look at one track, or --offset to go on
    from where the last page ended.
    """
    try:
        client = _client(lease_wait)
        payload = clips.list_clips(client, sequence_id=sequence_id, track_type=track_type, track=track,
                                   offset=offset, limit=limit, detail=detail, allow_untested=allow_untested,
                                   hard_limit_s=timeout)
    except CliError as err:
        _fail(ctx, err)
        return
    _emit(ctx, payload, _clip_text(payload))


# ---------- live effect ----------
HUMAN_STRING_LIMIT = 60       # a text longer than this is cut in the human output (never in --json)


@live.group("effect")
def live_effect() -> None:
    """Look at the effects of a clip in the running Premiere."""


def _quoted(text: str) -> str:
    """A string between double quotes, with the quote, the backslash and every character that does not
    print (such as the U+FEFE that Lumetri Color holds) written as an escape, so the line stays readable."""
    out = []
    for char in text:
        if char in '"\\':
            out.append("\\" + char)
        elif char.isprintable():
            out.append(char)
        else:
            out.append(f"\\u{ord(char):04x}")
    return '"' + "".join(out) + '"'


def _value_text(prop: dict) -> str:
    kind, value = prop.get("kind"), prop.get("value")
    if kind == "unreadable":
        return "(unreadable)"
    if kind == "boolean":
        return "true" if value else "false"
    if kind == "color":
        return "argb(" + ", ".join(str(channel) for channel in value) + ")"
    if kind == "array":
        return "[" + ", ".join(str(item) for item in value) + "]"
    if kind == "string":
        return _quoted(value if len(value) <= HUMAN_STRING_LIMIT else value[:HUMAN_STRING_LIMIT] + "...")
    return str(value)


def _property_line(prop: dict) -> str:
    """  14 Temperature = -46.5 (keyframed). A blank name is shown in quotes so that " " and "" can be told apart."""
    name = prop.get("name")
    shown = "?" if name is None else (_quoted(name) if name.strip() == "" else name)
    return f"  {prop.get('index')} {shown} = {_value_text(prop)}" + (" (keyframed)" if prop.get("keyframed") is True else "")


def _effect_head(item: dict) -> str:
    """[3] Lumetri Color (AE.ADBE Lumetri), 130 properties"""
    props = item.get("properties")
    if props is None:
        count = "properties could not be read"
    else:
        count = f"{len(props)} propert" + ("y" if len(props) == 1 else "ies")
    name = "?" if item.get("name") is None else item.get("name")
    match = "?" if item.get("match_name") is None else item.get("match_name")
    return f"[{item.get('effect_index')}] {name} ({match}), {count}"


def _effect_text(payload: dict) -> str:
    clip = payload["clip"]
    kind = "V" if clip.get("track_type") == "video" else "A"
    track = clip.get("track")
    label = f"{kind}{track + 1}" if isinstance(track, int) else f"{kind}?"
    lines = [f"{label}[{clip.get('clip')}] {clip.get('node_id') or '-'} "
             f"{'?' if clip.get('name') is None else clip.get('name')}, sequence {clip.get('sequence_id')}, "
             f"starts at {_seconds_text(clip.get('start_seconds'))} s"]
    if not payload["effects"]:
        lines.append("This clip has no effects.")
    for item in payload["effects"]:
        lines.append(_effect_head(item))
        lines += [_property_line(prop) for prop in item.get("properties") or []]
    lines += _warning_lines(payload["warnings"])
    return "\n".join(lines)


@live_effect.command("get")
@click.option("--sequence", "sequence_id", metavar="ID",
              help="ID of the sequence (see `live state`). Without it, the active sequence is read and the warnings say so.")
@click.option("--track", type=click.IntRange(min=0), metavar="N",
              help="Track of the clip, counted from 0 (V1 and A1 are track 0). Goes with --clip.")
@click.option("--clip", type=click.IntRange(min=0), metavar="N",
              help="Number of the clip on that track, counted from 0 (see `live clip list`). Goes with --track.")
@click.option("--track-type", "track_type", type=click.Choice(effects.TRACK_TYPES),
              help="Video or audio track, for --track and --clip. The default is video.")
@click.option("--node-id", "node_id", metavar="ID",
              help="The clip by its node ID (see `live clip list`), instead of --track and --clip.")
@click.option("--effect", metavar="NAME",
              help="Only the effects with this display name or match name. All of them when the name occurs twice.")
@click.option("--effect-index", "effect_index", type=click.IntRange(min=0), metavar="N",
              help="Only the effect at this index of the clip's effect list, the number in square brackets in the output. "
                   "With --effect, the name at that index must match.")
@live_options
@json_option
@click.pass_context
@stops_cleanly(STOPPED_LOOK)
def live_effect_get(ctx: click.Context, sequence_id: str | None, track: int | None, clip: int | None,
                    track_type: str | None, node_id: str | None, effect: str | None, effect_index: int | None,
                    lease_wait: float, timeout: float, allow_untested: bool) -> None:
    """List the effects of one clip and the properties of each. Only reads: nothing in the project changes.

    Name the clip with --track and --clip together, or with --node-id. An effect line shows the effect's
    index on the clip, its name and its match name. A property line shows the property's index, its name and
    its value, and says (keyframed) when the property has keyframes. Lumetri Color has 130 properties,
    many with a blank name: use --effect or --effect-index to look at one effect.

    In the human output a text longer than 60 characters is cut. --json holds every value in full.
    """
    if node_id is not None:
        if track is not None or clip is not None or track_type is not None:
            raise click.UsageError("--node-id cannot be used with --track, --clip or --track-type.")
        target = {"node_id": node_id}
    elif track is None or clip is None:
        raise click.UsageError("Name the clip with --track and --clip together, or with --node-id.")
    else:
        target = {"track_type": track_type or "video", "track": track, "clip": clip}
    try:
        client = _client(lease_wait)
        payload = effects.get_effects(client, target, effect=effect, effect_index=effect_index,
                                      sequence_id=sequence_id, allow_untested=allow_untested, hard_limit_s=timeout)
    except CliError as err:
        _fail(ctx, err)
        return
    _emit(ctx, payload, _effect_text(payload))
