"""The recipe commands of the `live` group: `live clip list`, and the commands that later tasks add.

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

from cli_anything.premiere.core.recipes import clips
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
