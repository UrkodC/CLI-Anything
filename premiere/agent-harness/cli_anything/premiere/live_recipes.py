"""The recipe commands of the `live` group: `live clip list`, `live effect get`, `live effect set`, and the commands
that later tasks add.

live_cli.py imports this module as its last line, so the `live` group exists when the commands attach.
The logic of each command is in core/recipes; this file holds only the click side.

To add a command: write the logic as a function in core/recipes/<name>.py that takes a BridgeClient and
returns a dict, then add a command here with the same decorator order as `live state` in live_cli.py:
the command's own options, `@live_options`, `@json_option`, `@click.pass_context`, `@stops_cleanly(...)`
(STOPPED_LOOK for a read, STOPPED_RUN for a write). Take the client with `_client(lease_wait)`, call the
recipe inside `try`, turn a CliError into `_fail(ctx, err)`, and print with `_emit(ctx, payload, text)`.
"""
from __future__ import annotations

import json
from pathlib import Path

import click

from cli_anything.premiere import live_cli
from cli_anything.premiere.core.recipes import clips, effects, frames
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.live_cli import (DEFAULT_LEASE_WAIT_S, STOPPED_LOOK, STOPPED_RUN, _client, _warning_lines,
                                            live, live_options, stops_cleanly)
from cli_anything.premiere.premiere_cli import _emit, _fail, _json, json_option


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
    """Look at the effects of a clip in the running Premiere, and write their values."""


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


# ---------- live effect set ----------
def _set_value_text(value: object) -> str:
    """A value in a line of `live effect set`: numbers plain, texts quoted (cut at 60), true and false."""
    if value is None:
        return "(unreadable)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(round(value, 6))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _quoted(value if len(value) <= HUMAN_STRING_LIMIT else value[:HUMAN_STRING_LIMIT] + "...")
    return json.dumps(value, ensure_ascii=False)


def _resolved_line(record: dict, requested: object) -> str:
    """entry 0: V1[0] testclip.mp4 at 0.000 s, ProcAmp[4].Brightness[0] = 7.7 -> -2.6"""
    clip, name = record["clip"], record.get("name")
    shown = "?" if name is None else (_quoted(name) if name.strip() == "" else name)
    effect = "?" if record.get("effect") is None else record["effect"]
    return (f"entry {record['entry']}: V{clip['track'] + 1}[{clip['clip']}] "
            f"{'?' if clip.get('name') is None else clip['name']} at {_seconds_text(clip.get('start_seconds'))} s, "
            f"{effect}[{record['effect_index']}].{shown}[{record['index']}] = "
            f"{_set_value_text(record.get('value_now'))} -> {_set_value_text(requested)}")


def _result_line(result: dict) -> str:
    """entry 0: stored -2.6 (2 attempts, zero first), or entry 1: NOT stored: ..., or the outcome unknown."""
    head = f"entry {result['entry']}: "
    if result["stored"] is None:
        return f"{head}outcome unknown after {result['attempts']} finished attempts: {result['error']}"
    if result["stored"]:
        notes = [f"{result['attempts']} attempt" + ("" if result["attempts"] == 1 else "s")]
        if result["zero_first"]:
            notes.append("zero first")
        return f"{head}stored {_set_value_text(result['value'])} ({', '.join(notes)})"
    was = _set_value_text(result.get("before"))
    if result["landed"] is not None:
        return (f"{head}NOT stored: {_set_value_text(result['landed'])} landed, "
                f"{_set_value_text(result['requested'])} did not (was {was})")
    line = f"{head}NOT stored: wanted {_set_value_text(result['requested'])}."
    if result.get("restored") is True:
        return f"{line} The old value {was} was put back."
    now = result.get("now")
    holds = (f" The property now holds {_set_value_text(now)} (was {was})." if now is not None
             else f" The property could not be read after the write (was {was}).")
    error = result.get("error") or ""
    if result.get("restored") is False:
        return line + holds + " " + effects.PUT_BACK_FAILED + error.split(effects.PUT_BACK_FAILED, 1)[-1]
    return f"{line} {error}.{holds}" if error else line + holds


def _set_lines(payload: dict, requested: list) -> list[str]:
    lines = [_resolved_line(record, requested[record["entry"]]) for record in payload.get("resolved") or []]
    lines += [_result_line(result) for result in payload.get("results") or []]
    return lines


def _json_value(text: str, flag: str) -> object:
    """--value and --expect-old: one JSON number, string or boolean."""
    try:
        value = json.loads(text, parse_constant=effects._no_constant)
    except (ValueError, RecursionError) as exc:
        raise CliError("bad_args", f"{flag} is not valid JSON: {exc}. Give a number such as -2.6, a text in double "
                                   "quotes such as '\"/luts/a.cube/\"', or true or false. Nothing was sent.") from None
    if value is None or isinstance(value, (list, dict)):
        raise CliError("bad_args", f"{flag} must be a number, a text in double quotes, or true or false, not "
                                   f"{text}. Nothing was sent.")
    return value


def _one_write_plan(options: dict) -> dict:
    """The plan of one write, from the options of the command line."""
    if options["sequence_id"] is None:
        raise CliError("bad_args", "--sequence is missing. Give the sequence ID (see `live state`), or give a plan "
                                   "with --from. Nothing was sent.")
    if options["node_id"] is not None:
        if options["track"] is not None or options["clip"] is not None:
            raise CliError("bad_args", "--node-id cannot be used with --track or --clip. Nothing was sent.")
        entry: dict = {"node_id": options["node_id"]}
    elif options["track"] is None or options["clip"] is None:
        raise CliError("bad_args", "Name the clip with --track and --clip together, or with --node-id. Nothing was sent.")
    else:
        entry = {"track": options["track"], "clip": options["clip"]}
    for key, flag in (("effect", "--effect"), ("index", "--index"), ("name", "--name"), ("value", "--value")):
        if options[key] is None:
            raise CliError("bad_args", f"{flag} is missing. One write needs --sequence, the clip, --effect, --index, "
                                       "--name and --value. Nothing was sent.")
    entry.update(effect=options["effect"], index=options["index"], name=options["name"],
                 value=_json_value(options["value"], "--value"))
    if options["effect_index"] is not None:
        entry["effect_index"] = options["effect_index"]
    if options["expect_old"] is not None:
        entry["expect_old"] = _json_value(options["expect_old"], "--expect-old")
    plan = {"sequence_id": options["sequence_id"], "writes": [entry]}
    if options["sequence_name"] is not None:
        plan["sequence_name"] = options["sequence_name"]
    return effects.check_plan(plan, code="bad_args")


SINGLE_FLAGS = (("sequence_id", "--sequence"), ("sequence_name", "--sequence-name"), ("track", "--track"),
                ("clip", "--clip"), ("node_id", "--node-id"), ("effect", "--effect"),
                ("effect_index", "--effect-index"), ("index", "--index"), ("name", "--name"), ("value", "--value"),
                ("expect_old", "--expect-old"))


def _set_failure(ctx: click.Context, code: str, message: str, payload: dict, requested: list) -> None:
    """Exit 1 with the failure and what ran so far, as `mcp batch` does."""
    if _json(ctx):
        click.echo(json.dumps({"ok": False, "error": message, "code": code, **payload}, indent=2, ensure_ascii=False))
    else:
        lines = _set_lines(payload, requested)
        if lines:
            click.echo("\n".join(lines))
        click.echo(f"Error: {message}", err=True)
        for line in _warning_lines(payload.get("warnings") or []):
            click.echo(line, err=True)
    ctx.exit(1)


@live_effect.command("set")
@click.option("--from", "plan_path", type=click.Path(path_type=Path), metavar="PLAN.json",
              help="Write the entries of this plan file, in order. Not with the options of one write.")
@click.option("--sequence", "sequence_id", metavar="ID", help="ID of the sequence (see `live state`).")
@click.option("--sequence-name", "sequence_name", metavar="NAME",
              help="The name the sequence must have. Nothing is written when it is named otherwise.")
@click.option("--track", type=click.IntRange(min=0), metavar="N",
              help="Video track of the clip, counted from 0 (V1 is track 0). Goes with --clip.")
@click.option("--clip", type=click.IntRange(min=0), metavar="N",
              help="Number of the clip on that track, counted from 0 (see `live clip list`). Goes with --track.")
@click.option("--node-id", "node_id", metavar="ID", help="The clip by its node ID, instead of --track and --clip.")
@click.option("--effect", metavar="NAME", help="Display name or match name of the effect.")
@click.option("--effect-index", "effect_index", type=click.IntRange(min=0), metavar="N",
              help="Index of the effect on the clip (see `live effect get`). Needed when two effects have the name.")
@click.option("--index", type=click.IntRange(min=0), metavar="N", help="Index of the property in the effect.")
@click.option("--name", metavar="NAME",
              help="The exact name of the property at --index. Nothing is written when it is named otherwise.")
@click.option("--value", metavar="JSON", help="The value as JSON: -2.6, a text in double quotes, true or false.")
@click.option("--expect-old", "expect_old", metavar="JSON",
              help="The value the property must hold now, as JSON. Nothing is written when it holds another one.")
@click.option("--dry-run", "dry_run", is_flag=True, help="Find and check every target, and write nothing.")
@click.option("--project", help="Path of the project that must be the active one.")
@click.option("--any-project", is_flag=True, help="Allow a write to a project that is not a Claude working copy. Needs --project.")
@live_options
@json_option
@click.pass_context
@stops_cleanly(STOPPED_RUN)
def live_effect_set(ctx: click.Context, plan_path: Path | None, dry_run: bool, project: str | None, any_project: bool,
                    lease_wait: float, timeout: float, allow_untested: bool, **single: object) -> None:
    """Write effect property values in the running Premiere. Each value is read back.

    One write: --sequence, the clip (--track and --clip, or --node-id), --effect, --index, --name and --value.
    Many writes: --from PLAN.json, a file such as {"sequence_id": "...", "writes": [{"track": 0, "clip": 0,
    "effect": "ProcAmp", "index": 0, "name": "Brightness", "value": -2.6}]}. A write may add "node_id",
    "effect_index" and "expect_old". Video tracks only.

    stored: the value read back from Premiere equals the value written. It is not a render check.

    Every target is found and checked before the first write. When one entry may not be written, nothing is
    written. A property with keyframes is refused. A Gyroflow effect is refused, because Gyroflow ignores
    scripted writes. Lumetri Color index 7, 32 and 34 are refused: 7 is the second Input LUT, and 32 and 34 are
    Look slots that do not render a LUT. A property that holds an array (such as Position) or a colour is
    refused, because only numbers, strings and booleans can be written. A property whose value cannot be read
    is refused. A number that is not finite, or is 4294967296 or more in size, is refused.

    A number counts as stored when the value read back is within 0.001 of the value wanted.

    A plan stops at the first write that is not stored. The entries after it do not run.

    A number below zero gets two tries. Then the near value is tried (-2.496 for -2.6). When it lands, the
    output says so, and the entry is still not stored. When nothing landed and the property holds another value
    than before, the old value is written back once, and the output says whether that worked. Use --dry-run to
    see the targets first.
    """
    try:
        given = [flag for key, flag in SINGLE_FLAGS if single[key] is not None]
        if plan_path is not None:
            if given:
                raise CliError("bad_args", f"--from cannot be used with {', '.join(given)}: the plan file names the "
                                           "sequence and every write. Nothing was sent.")
            plan = effects.load_plan(plan_path)
        elif not given:
            raise CliError("bad_args", "Give a plan with --from PLAN.json, or one write with --sequence, the clip, "
                                       "--effect, --index, --name and --value. Nothing was sent.")
        else:
            plan = _one_write_plan(single)
        requested = [entry["value"] for entry in plan["writes"]]
        client = _client(lease_wait)
        payload = effects.set_effects(client, plan, dry_run=dry_run, project=project, any_project=any_project,
                                      allow_untested=allow_untested, hard_limit_s=timeout)
    except CliError as err:
        carried = getattr(err, "payload", None)
        if carried is None:
            _fail(ctx, err)
            return
        _set_failure(ctx, err.code, err.message, carried, requested)
        return
    if payload.get("dry_run"):
        lines = _set_lines(payload, requested) + ["Dry run: nothing was written."] + _warning_lines(payload["warnings"])
        _emit(ctx, payload, "\n".join(lines))
        return
    if not payload["stored"]:
        failed = next(r for r in payload["results"] if not r["stored"])
        message = (f"The write of entry {failed['entry']} was not stored. The plan stops at the first write that is "
                   f"not stored: {payload['ran']} of {payload['total']} entries ran.")
        _set_failure(ctx, "not_stored", message, payload, requested)
        return
    lines = _set_lines(payload, requested) + [f"Stored: {payload['ran']} of {payload['total']} entries."]
    _emit(ctx, payload, "\n".join(lines + _warning_lines(payload["warnings"])))


# ---------- live frame export ----------
def _frame_options(func):
    """The options of live_options, with a longer --timeout: here it is the longest wait for one frame."""
    func = click.option("--allow-untested", is_flag=True,
                        help="Go on with a Premiere, panel or MCP server version this CLI was not tested with.")(func)
    func = click.option("--timeout", "timeout", type=click.FloatRange(min=0, min_open=True),
                        default=frames.EXPORT_TIMEOUT_S, show_default=True, metavar="SECONDS",
                        help="Longest time to wait for one frame. When it runs out, the run stops, and the next "
                             "program that talks to Premiere may find an open recovery.")(func)
    func = click.option("--lease-wait", "lease_wait", type=click.FloatRange(min=0),
                        default=DEFAULT_LEASE_WAIT_S, show_default=True, metavar="SECONDS",
                        help="Longest time to wait for another program to give Premiere back.")(func)
    return func


@live.group("frame")
def live_frame() -> None:
    """Export still frames of a sequence from the running Premiere."""


def _frame_lines(payload: dict) -> list[str]:
    """1.000 s -> /path/frame_00001000ms.png (204050 bytes), then the switches that were put back."""
    lines = [f"{f['time_seconds']:.3f} s -> {f['path']} ({f['bytes']} bytes)" for f in payload.get("frames") or []]
    if payload.get("left_switched") is not None:
        return lines
    proxies = payload.get("proxies") or {}
    if proxies.get("before") is True and proxies.get("during") is False and proxies.get("after") is True:
        lines.append("Proxy display: off for the export, then on again.")
    active = payload.get("active_sequence") or {}
    seq = payload.get("sequence") or {}
    if active.get("before") is not None and active.get("before") != seq.get("id") \
            and active.get("after") == active.get("before"):
        lines.append(f"Active sequence: {seq.get('name')} ({seq.get('id')}) for the export, then "
                     f"{active['before']} again.")
    return lines


def _frame_failure(ctx: click.Context, err: CliError, payload: dict) -> None:
    """Exit 1 with the failure and what was written, as `mcp batch` does."""
    if _json(ctx):
        click.echo(json.dumps({"ok": False, "error": err.message, "code": err.code, **payload}, indent=2,
                              ensure_ascii=False))
    else:
        lines = _frame_lines(payload)
        if lines:
            click.echo("\n".join(lines))
        click.echo(f"Error: {err.message}", err=True)
        for line in _warning_lines(payload.get("warnings") or []):
            click.echo(line, err=True)
    ctx.exit(1)


@live_frame.command("export")
@click.option("--sequence", "sequence_id", required=True, metavar="ID", help="ID of the sequence (see `live state`).")
@click.option("--sequence-name", "sequence_name", metavar="NAME",
              help="The name the sequence must have. Nothing is switched or exported when it is named otherwise.")
@click.option("--time", "times", type=float, multiple=True, metavar="SECONDS",
              help="Sequence time of a frame, in seconds. Give it once per frame.")
@click.option("--out-dir", "out_dir", required=True, type=click.Path(path_type=Path), metavar="DIR",
              help="Folder for the frames. It is made when it does not exist.")
@click.option("--prefix", default="frame", show_default=True, metavar="NAME",
              help="Start of each file name: NAME_00001000ms.png for 1 s. No folder separator.")
@click.option("--format", "fmt", type=click.Choice(frames.FORMATS), default="png", show_default=True,
              help="File format. The extension picks it.")
@click.option("--overwrite", is_flag=True, help="Replace frames that exist already. Without it, they are refused.")
@click.option("--proxies", type=click.Choice(frames.PROXY_CHOICES), default="off", show_default=True,
              help="off: switch the proxy display off for the export and put it back after. keep: leave it as it is.")
@click.option("--dry-run", "dry_run", is_flag=True,
              help="Check the times and the files, read what would be switched, and change nothing: no folder is "
                   "made, nothing is switched or exported.")
@click.option("--project", help="Path of the project that must be the active one.")
@click.option("--any-project", is_flag=True, help="Allow a project that is not a Claude working copy. Needs --project.")
@_frame_options
@json_option
@click.pass_context
@stops_cleanly(STOPPED_RUN)
def live_frame_export(ctx: click.Context, sequence_id: str, sequence_name: str | None, times: tuple, out_dir: Path,
                      prefix: str, fmt: str, overwrite: bool, proxies: str, dry_run: bool, project: str | None,
                      any_project: bool, lease_wait: float, timeout: float, allow_untested: bool) -> None:
    """Export still frames of one sequence, one file per --time, in one call.

    The command makes the output folder. It switches proxy display off and puts it back after the export
    (--proxies keep leaves it alone). The export works on the active sequence only, so when another sequence is
    active, the command makes this one active and puts the old one back after.

    file check: each file exists and is not empty. It is not a check of the picture.

    The first frame that fails stops the run. The output lists the frames that were written. After a failed
    export Premiere may open a recovery. Then nothing more is written: the proxy display and the active sequence
    may stay switched, and the output says how to put them back after the recovery, with
    `cli-anything-premiere live app proxies on` and `cli-anything-premiere live app activate --sequence ID`.

    A time at or after the end of the sequence is refused. --timeout is the longest wait for one frame (600
    seconds by default). A PNG still can carry alpha: composite it over black before you measure it.
    """
    try:
        client = _client(lease_wait)
        payload = frames.export_frames(client, live_cli.make_session, sequence_id=sequence_id,
                                       sequence_name=sequence_name, times=list(times), out_dir=out_dir, prefix=prefix,
                                       fmt=fmt, overwrite=overwrite, proxies=proxies, dry_run=dry_run, project=project,
                                       any_project=any_project, allow_untested=allow_untested, hard_limit_s=timeout)
    except CliError as err:
        carried = getattr(err, "payload", None)
        if carried is None:
            _fail(ctx, err)
            return
        _frame_failure(ctx, err, carried)
        return
    if payload.get("dry_run"):
        lines = [f"{p['time_seconds']:.3f} s -> {p['path']}" for p in payload["planned"]]
        if not payload["out_dir"]["exists"]:
            lines.append(f"The folder {payload['out_dir']['path']} does not exist yet. The real run will make it.")
        if payload["proxies"]["switch"]:
            lines.append("Proxy display: would be switched off for the export.")
        if payload["active_sequence"]["switch"]:
            lines.append(f"Active sequence: would be switched from {payload['active_sequence']['before'] or 'none'} "
                         f"to {payload['sequence']['id']} for the export.")
        lines.append("Dry run: nothing was switched or exported.")
        _emit(ctx, payload, "\n".join(lines + _warning_lines(payload["warnings"])))
        return
    _emit(ctx, payload, "\n".join(_frame_lines(payload) + _warning_lines(payload["warnings"])))


# ---------- live app ----------
@live.group("app")
def live_app() -> None:
    """Switch the proxy display and the active sequence of the running Premiere."""


def _app_failure(ctx: click.Context, err: CliError) -> None:
    carried = getattr(err, "payload", None)
    if carried is None:
        _fail(ctx, err)
        return
    if _json(ctx):
        click.echo(json.dumps({"ok": False, "error": err.message, "code": err.code, **carried}, indent=2,
                              ensure_ascii=False))
    else:
        click.echo(f"Error: {err.message}", err=True)
        for line in _warning_lines(carried.get("warnings") or []):
            click.echo(line, err=True)
    ctx.exit(1)


def _on_off(value) -> str:
    return "unknown" if value is None else ("on" if value else "off")


@live_app.command("proxies")
@click.argument("state", type=click.Choice(["on", "off"]))
@click.option("--dry-run", "dry_run", is_flag=True, help="Read the proxy display and change nothing.")
@click.option("--project", help="Path of the project that must be the active one.")
@click.option("--any-project", is_flag=True, help="Allow a project that is not a Claude working copy. Needs --project.")
@live_options
@json_option
@click.pass_context
@stops_cleanly(STOPPED_RUN)
def live_app_proxies(ctx: click.Context, state: str, dry_run: bool, project: str | None, any_project: bool,
                     lease_wait: float, timeout: float, allow_untested: bool) -> None:
    """Switch the proxy display of Premiere on or off, and read it back.

    stored: the proxy display reads back as wanted. The proxy display is app-wide: it holds for every project.
    """
    try:
        client = _client(lease_wait)
        payload = frames.set_proxies(client, state == "on", dry_run=dry_run, project=project, any_project=any_project,
                                     allow_untested=allow_untested, hard_limit_s=timeout)
    except CliError as err:
        _app_failure(ctx, err)
        return
    if payload.get("dry_run"):
        lines = [f"Proxy display: {_on_off(payload['proxies']['before'])}. Dry run: nothing was switched."]
    else:
        lines = [f"Proxy display: {_on_off(payload['proxies']['after'])} (was {_on_off(payload['proxies']['before'])}). "
                 "Stored: yes"]
    _emit(ctx, payload, "\n".join(lines + _warning_lines(payload["warnings"])))


def _sequence_text(ref) -> str:
    return "none" if not isinstance(ref, dict) else f"{ref.get('name') or '?'} ({ref.get('id')})"


@live_app.command("activate")
@click.option("--sequence", "sequence_id", required=True, metavar="ID", help="ID of the sequence (see `live state`).")
@click.option("--sequence-name", "sequence_name", metavar="NAME",
              help="The name the sequence must have. Nothing is switched when it is named otherwise.")
@click.option("--dry-run", "dry_run", is_flag=True, help="Find the sequence, read the active one, and change nothing.")
@click.option("--project", help="Path of the project that must be the active one.")
@click.option("--any-project", is_flag=True, help="Allow a project that is not a Claude working copy. Needs --project.")
@live_options
@json_option
@click.pass_context
@stops_cleanly(STOPPED_RUN)
def live_app_activate(ctx: click.Context, sequence_id: str, sequence_name: str | None, dry_run: bool,
                      project: str | None, any_project: bool, lease_wait: float, timeout: float,
                      allow_untested: bool) -> None:
    """Make a sequence the active one in Premiere.

    stored: the script reads the sequence back as active, and a fresh read of Premiere's state shows it too.
    """
    try:
        client = _client(lease_wait)
        payload = frames.activate_sequence(client, sequence_id, sequence_name, dry_run=dry_run, project=project,
                                           any_project=any_project, allow_untested=allow_untested,
                                           hard_limit_s=timeout)
    except CliError as err:
        _app_failure(ctx, err)
        return
    before = payload["active_sequence"]["before"]
    if payload.get("dry_run"):
        lines = [f"Active sequence: {_sequence_text(before)}. Dry run: nothing was switched."]
    else:
        lines = [f"Active sequence: {_sequence_text(payload['active_sequence']['after'])} "
                 f"(was {_sequence_text(before)}). Stored: yes"]
    _emit(ctx, payload, "\n".join(lines + _warning_lines(payload["warnings"])))
