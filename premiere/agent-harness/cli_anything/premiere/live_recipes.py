"""The recipe commands of the `live` group: `live clip list`, `live effect get`, `live effect set`, `live frame
export`, `live app`, `live lut set`, `live keys get`, `live keys set`, and the commands that later tasks add.

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
from cli_anything.premiere.core.recipes import apply, clips, effects, frames, keys, lut, place, results
from cli_anything.premiere.errors import CliError
from cli_anything.premiere.live_cli import (DEFAULT_LEASE_WAIT_S, STOPPED_LOOK, STOPPED_RUN, _client, _warning_lines,
                                            live, live_options, stops_cleanly)
from cli_anything.premiere.premiere_cli import _echo_json, _echo_text, _emit, _fail, _json, json_option


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
        _echo_json({"ok": False, "error": message, "code": code, **payload}, indent=2)
    else:
        lines = _set_lines(payload, requested)
        if lines:
            _echo_text("\n".join(lines))
        _echo_text(f"Error: {message}", err=True)
        for line in _warning_lines(payload.get("warnings") or []):
            _echo_text(line, err=True)
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

    A clip on an audio track is refused. A name that does not match the index is refused. An effect name that
    occurs twice on the clip needs --effect-index. A property that does not hold the --expect-old value is
    refused.

    A number counts as stored when the value read back is within 0.001 of the value wanted.

    A plan stops at the first write that is not stored. The entries after it do not run.

    A long plan gives other sessions a turn between writes. Then every target is found again and every check
    runs again on the entries not yet written, the --expect-old check too. The plan can stop midway with
    target_moved or a refusal. The error says "Nothing more was written", and the results list the entries that
    ran. The write script also reads the old value right before it writes: when it is not the --expect-old value,
    it refuses with precondition and writes nothing.

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
        _echo_json({"ok": False, "error": err.message, "code": err.code, **payload}, indent=2)
    else:
        lines = _frame_lines(payload)
        if lines:
            _echo_text("\n".join(lines))
        _echo_text(f"Error: {err.message}", err=True)
        for line in _warning_lines(payload.get("warnings") or []):
            _echo_text(line, err=True)
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

    While the command runs, the sequence is active for every session that works in Premiere, and proxy display
    is off unless you give --proxies keep. Do not run it while another session works on another sequence.

    The first frame that fails stops the run. The output lists the frames that were written. After a failed
    export Premiere may open a recovery. Then nothing more is written: the proxy display and the active sequence
    may stay switched. The output names what is still switched (left_switched and the warnings) and prints the
    commands that put it back. After the owner closes the recovery, run only the commands the output prints.

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
        _echo_json({"ok": False, "error": err.message, "code": err.code, **carried}, indent=2)
    else:
        _echo_text(f"Error: {err.message}", err=True)
        for line in _warning_lines(carried.get("warnings") or []):
            _echo_text(line, err=True)
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


# ---------- live lut set ----------
@live.group("lut")
def live_lut() -> None:
    """Write a LUT file into a Lumetri Color effect of the running Premiere."""


def _lut_lines(payload: dict) -> list[str]:
    """V1[0] testclip.mp4, Lumetri Color[2]: index 4 "\ufefe" -> "/luts/look.cube/", then stored and rendered."""
    lines = []
    clip = payload.get("clip")
    if clip:
        name = "?" if clip.get("name") is None else clip["name"]
        before, after = payload.get("path_before"), payload.get("path_after")
        slot = payload.get("slot_before")
        if before is not None:
            shown = _set_value_text(before)
        elif slot is None or slot in ("", lut.EMPTY_SLOT):
            shown = "(no LUT)"
        else:
            shown = lut.short_value(slot)           # a value that is not a .cube path, as Premiere holds it
        line = (f"V{clip['track'] + 1}[{clip['clip']}] {name}, Lumetri Color[{payload.get('effect_index')}]: "
                f"index 4 {shown}")
        lines.append(line + (f" -> {_set_value_text(after)}" if after is not None else ""))   # nothing written: no arrow
    if payload.get("dry_run"):
        lines += [f"Would export: {path}" for path in payload.get("frames") or []]
        lines += [f"Would replace: {path}" for path in payload.get("replaces") or []]
        lines.append("Dry run: nothing was written.")
        return lines
    stored = payload.get("stored")
    lines.append("Stored: " + {True: "yes", False: "no", None: "unknown"}[stored]
                 + ("" if payload.get("menu_value") is None
                    else f". Index 6 (Input LUT) reads {_set_value_text(payload['menu_value'])}."))
    lines += [f"Frame: {path}" for path in payload.get("frames") or []]
    if payload.get("difference") is not None:
        rendered = {True: "yes", False: "no", None: "cannot tell"}[payload.get("rendered")]
        lines.append(f"Rendered: {rendered} (difference {payload['difference']}, threshold {lut.PROVE_THRESHOLD}).")
    return lines


def _lut_failure(ctx: click.Context, err: CliError, payload: dict) -> None:
    """Exit 1 with the failure and what was written, as `mcp batch` does."""
    if _json(ctx):
        _echo_json({"ok": False, "error": err.message, "code": err.code, **payload}, indent=2)
    else:
        lines = _lut_lines(payload)
        if lines:
            _echo_text("\n".join(lines))
        _echo_text(f"Error: {err.message}", err=True)
        for line in _warning_lines(payload.get("warnings") or []):
            _echo_text(line, err=True)
    ctx.exit(1)


@live_lut.command("set")
@click.option("--sequence", "sequence_id", required=True, metavar="ID", help="ID of the sequence (see `live state`).")
@click.option("--sequence-name", "sequence_name", metavar="NAME",
              help="The name the sequence must have. Nothing is written when it is named otherwise.")
@click.option("--track", type=click.IntRange(min=0), metavar="N",
              help="Video track of the clip, counted from 0 (V1 is track 0). Goes with --clip.")
@click.option("--clip", type=click.IntRange(min=0), metavar="N",
              help="Number of the clip on that track, counted from 0 (see `live clip list`). Goes with --track.")
@click.option("--node-id", "node_id", metavar="ID", help="The clip by its node ID, instead of --track and --clip.")
@click.option("--lut", "lut_path", required=True, type=click.Path(path_type=Path), metavar="FILE.cube",
              help="The LUT file, as an absolute path to a .cube file.")
@click.option("--effect-index", "effect_index", type=click.IntRange(min=0), metavar="N",
              help="Index of the Lumetri Color on the clip (see `live effect get`). Needed when the clip has two.")
@click.option("--prove", is_flag=True,
              help="Export the frame at --time before and after the write, into --out-dir, and compare them. "
                   "Shows that the LUT changed the picture. It does not show that it is the right LUT. "
                   "For a film, compare with an ffmpeg lut3d render of the source.")
@click.option("--time", "time", type=float, metavar="SECONDS",
              help="Sequence time of the frame for --prove, in seconds. It must lie inside the clip.")
@click.option("--out-dir", "out_dir", type=click.Path(path_type=Path), metavar="DIR",
              help="Folder for the two frames of --prove. It is made when it does not exist.")
@click.option("--dry-run", "dry_run", is_flag=True,
              help="Find and check the clip and the Lumetri Color, and write nothing. No frame is exported.")
@click.option("--project", help="Path of the project that must be the active one.")
@click.option("--any-project", is_flag=True, help="Allow a write to a project that is not a Claude working copy. Needs --project.")
@live_options
@json_option
@click.pass_context
@stops_cleanly(STOPPED_RUN)
def live_lut_set(ctx: click.Context, sequence_id: str, sequence_name: str | None, track: int | None, clip: int | None,
                 node_id: str | None, lut_path: Path, effect_index: int | None, prove: bool, time: float | None,
                 out_dir: Path | None, dry_run: bool, project: str | None, any_project: bool, lease_wait: float,
                 timeout: float, allow_untested: bool) -> None:
    """Write a LUT file into the Input LUT slot of a Lumetri Color effect, and read it back.

    The clip must already hold a Lumetri Color effect. The command writes the full path of the .cube file plus a
    trailing / into index 4, then 1 into index 6. Index 7 is never written. Before any write it checks the layout:
    index 2 is Basic Correction, index 4 holds a string, and index 6 is Input LUT. Any other layout is refused, and
    nothing is written.

    stored: index 4 reads back the path that was written. That is not proof for a LUT: a path in the slot does not
    show that the LUT renders. Index 6 is shown, but it does not count: in some projects it reads 0 while the LUT
    renders. Index 6 = 0 does not remove a LUT.

    --prove exports the frame at --time before the write and after it, and compares the two. The clip must be
    visible at --time, and the picture must not be black or one flat color. rendered is true when the exported
    frame changed. It is false when the slot was empty before and the exported frame did not change: then check
    that the clip is visible at that time and that the LUT is not an identity LUT. It is null when the exported
    frame did not change and a LUT was already in the slot or the frame before is one flat color; and when the two
    frames were not taken under the same conditions (someone changed proxy display or the active sequence during
    the check, or that could not be read). The warnings say which. The slot counts as empty only when index 4
    holds no path (the one character U+FEFE, or nothing) and index 6 reads 0. Any other value before the write,
    such as a LUT chosen from the Input LUT menu, gives null with a warning that names it. --prove replaces its
    own two files (lut_before_... and lut_after_...) from an earlier run at the same time: it removes them
    before the first export. It needs ffmpeg on PATH. With --prove, each export waits up to 600 seconds, or up to
    --timeout when that is longer. Each export switches proxy display off and makes the sequence active, then
    puts both back, as `live frame export` does. While it runs, these switches hold for every session that works
    in Premiere.
    """
    try:
        if node_id is not None:
            if track is not None or clip is not None:
                raise CliError("bad_args", "--node-id cannot be used with --track or --clip. Nothing was sent.")
            target: dict = {"sequence_id": sequence_id, "node_id": node_id}
        elif track is None or clip is None:
            raise CliError("bad_args", "Name the clip with --track and --clip together, or with --node-id. "
                                       "Nothing was sent.")
        else:
            target = {"sequence_id": sequence_id, "track": track, "clip": clip}
        if sequence_name is not None:
            target["sequence_name"] = sequence_name
        client = _client(lease_wait)
        payload = lut.set_lut(client, live_cli.make_session, target, lut_path, effect_index=effect_index, prove=prove,
                              time=time, out_dir=out_dir, dry_run=dry_run, project=project, any_project=any_project,
                              allow_untested=allow_untested, hard_limit_s=timeout, pixels=lut.frame_pixels)
    except CliError as err:
        carried = getattr(err, "payload", None)
        if carried is None:
            _fail(ctx, err)
            return
        _lut_failure(ctx, err, carried)
        return
    _emit(ctx, payload, "\n".join(_lut_lines(payload) + _warning_lines(payload["warnings"])))


# ---------- entry results (the plan commands) ----------
def _now_text(now: object) -> str:
    """What the read-back holds, for one line: a value as `live effect set` shows it, anything else as JSON, cut
    at HUMAN_STRING_LIMIT (never in --json). None gives an empty text."""
    if now is None:
        return ""
    if isinstance(now, (bool, int, float, str)):
        return _set_value_text(now)
    text = json.dumps(now, ensure_ascii=False)
    return text if len(text) <= HUMAN_STRING_LIMIT else text[:HUMAN_STRING_LIMIT] + "..."


def _entry_detail(record: dict) -> str:
    """The part of an entry line after the bar: the read-back for done, the reason for skipped, the error for
    failed. Nothing for an entry that did not run."""
    status = record["status"]
    if status == "done":
        return _now_text(record.get("now"))
    if status == "skipped":
        kind = "refused" if record.get("skip") == results.REFUSED else "nothing to do"
        return ": ".join(part for part in (kind, record.get("reason_code"), record.get("reason")) if part)
    if status == "failed":
        return ("outcome unknown: " if record.get(results.UNKNOWN) else "") + (record.get("error") or "")
    return ""


def _entry_lines(payload: dict) -> list[str]:
    """One line per entry, then the counts:

      3 S04 done | {"start": 12}
      4 S05 skipped | refused: clip_changed: The clip was trimmed after the plan was made.
      5 S06 not run
    1 done, 1 skipped (1 refused), 0 failed, 1 not run. The first entry that did not run is 5.

    The line starts with two spaces. A warning of an entry follows its line. No entries: no lines.
    """
    records = payload.get("results") or []
    if not records:
        return []
    lines = []
    for record in records:
        head = f"  {record['entry']} " + ("" if record.get("name") is None else f"{record['name']} ")
        head += record["status"].replace("_", " ")
        detail = _entry_detail(record)
        lines.append(head + (f" | {detail}" if detail else ""))
        lines += [f"    {line}" for line in _warning_lines(record.get("warnings") or [])]
    total = payload.get("counts") or results.counts(records)
    refused = f" ({total['refused']} refused)" if total.get("refused") else ""
    line = (f"{total['done']} done, {total['skipped']} skipped{refused}, {total['failed']} failed, "
            f"{total['not_run']} not run.")
    first = payload.get("first_not_run")
    lines.append(line if first is None else f"{line} The first entry that did not run is {first}.")
    return lines


def _entries_failure(ctx: click.Context, err: CliError) -> None:
    """Exit 1 with the failure and the result of every entry, as `_frame_failure` does. The payload is
    `err.payload` (results.refused_error and results.outcome_error carry it). An error without one is a plain
    failure: `_fail`."""
    payload = getattr(err, "payload", None)
    if payload is None:
        _fail(ctx, err)
        return
    if _json(ctx):
        _echo_json({"ok": False, "error": err.message, "code": err.code, **payload}, indent=2)
    else:
        lines = _entry_lines(payload)
        if lines:
            _echo_text("\n".join(lines))
        _echo_text(f"Error: {err.message}", err=True)
        for line in _warning_lines(payload.get("warnings") or []):
            _echo_text(line, err=True)
    ctx.exit(1)


# ---------- live keys ----------
@live.group("keys")
def live_keys() -> None:
    """Look at the keyframes of a clip in the running Premiere, or write them from a level plan."""


def _keys_text(payload: dict) -> str:
    """Motion.Rotation of 000f4276 (S02 C2246): 4 keys, then one line per key:

      1.247 s  316757952000  1.01

    A property with no keys gives one line: ...: static 0.67. A key with no time in seconds shows "?".
    """
    clip, effect, prop = payload["clip"], payload["effect"], payload["property"]
    head = (f"{'?' if effect.get('name') is None else effect['name']}.{'?' if prop.get('name') is None else prop['name']} "
            f"of {clip.get('node_id') or '-'} ({'?' if clip.get('name') is None else clip['name']}): ")
    if not payload["is_time_varying"]:
        return "\n".join([head + f"static {_set_value_text(payload['value'])}"] + _warning_lines(payload["warnings"]))
    count = len(payload["keys"])
    lines = [head + f"{count} key" + ("" if count == 1 else "s")]
    lines += [f"  {_seconds_text(item['seconds'])} s  {item['media_ticks']}  {_set_value_text(item['value'])}"
              for item in payload["keys"]]
    return "\n".join(lines + _warning_lines(payload["warnings"]))


@live_keys.command("get")
@click.option("--sequence", "sequence_id", required=True, metavar="ID",
              help="ID of the sequence that holds the clip (see `live state`).")
@click.option("--track", type=click.IntRange(min=0), metavar="N",
              help="Track of the clip, counted from 0 (V1 and A1 are track 0). Goes with --clip.")
@click.option("--clip", type=click.IntRange(min=0), metavar="N",
              help="Number of the clip on that track, counted from 0 (see `live clip list`). Goes with --track.")
@click.option("--track-type", "track_type", type=click.Choice(effects.TRACK_TYPES),
              help="Video or audio track, for --track and --clip. The default is video.")
@click.option("--node-id", "node_id", metavar="ID",
              help="The clip by its node ID (see `live clip list`), instead of --track and --clip.")
@click.option("--effect", default="Motion", show_default=True, metavar="NAME",
              help="The display name or match name of the effect. Exactly one effect of the clip may have it.")
@click.option("--property", "prop", default="Rotation", show_default=True, metavar="NAME",
              help="The exact display name of the property.")
@click.option("--property-index", "property_index", type=click.IntRange(min=0), metavar="N",
              help="The place of the property in the effect (see `live effect get`). Needed when two properties "
                   "have the name: Opacity has two named Blend Mode.")
@live_options
@json_option
@click.pass_context
@stops_cleanly(STOPPED_LOOK)
def live_keys_get(ctx: click.Context, sequence_id: str, track: int | None, clip: int | None, track_type: str | None,
                  node_id: str | None, effect: str, prop: str, property_index: int | None, lease_wait: float,
                  timeout: float, allow_untested: bool) -> None:
    """List the keys of one property of one clip, or show its value when it has none. Only reads: nothing in the
    project changes.

    Name the sequence with --sequence and the clip with --track and --clip together, or with --node-id. The
    effect is Motion and the property is Rotation unless --effect and --property say otherwise.

    Each key shows its time in seconds from the clip start, its media ticks, and its value. Premiere stores a key
    in media time, which is the clip's in point plus the time in the clip. A clip with a speed change or a reverse
    has no time in seconds: the line shows ? and the media ticks stay. A property that holds a colour with keys
    cannot be read.
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
        payload = keys.get_keys(client, target, sequence_id=sequence_id, effect=effect, prop=prop,
                                property_index=property_index, allow_untested=allow_untested, hard_limit_s=timeout)
    except CliError as err:
        _fail(ctx, err)
        return
    _emit(ctx, payload, _keys_text(payload))


def _planned_line(item: dict) -> str:
    """One line of a dry run: what one entry would get."""
    head = f"  {item['entry']} " + ("" if item.get("name") is None else f"{item['name']} ")
    if item.get("refusal"):
        return head + f"refused | {item['refusal']['code']}: {item['refusal']['reason']}"
    if item.get("skip"):
        return head + f"nothing to do | {item['skip']}"
    if item["mode"] == "keys":
        moved = sum(key["moved_from"] is not None for key in item["keys"])
        what = f"keys | {len(item['keys'])} key" + ("" if len(item["keys"]) == 1 else "s")
        what += f", {moved} moved to the last frame" if moved else ""
    else:
        what = f"static | Rotation {_set_value_text(item['static_value'])}"
    scale = item.get("scale") or {}
    if scale.get("new") is not None:
        what += f"; Scale {_set_value_text(scale.get('now'))} -> {_set_value_text(scale['new'])}"
    return head + what


def _keys_set_text(payload: dict) -> str:
    if payload.get("dry_run"):
        lines = [_planned_line(item) for item in payload["planned"]]
        lines.append("Dry run: nothing was written.")
    else:
        lines = _entry_lines(payload)
    return "\n".join(lines + _warning_lines(payload.get("warnings") or []))


@live_keys.command("set")
@click.option("--sequence", "sequence_id", required=True, metavar="ID",
              help="ID of the sequence that holds the clips (see `live state`).")
@click.option("--sequence-name", "sequence_name", metavar="NAME",
              help="The name the sequence must have. Nothing is written when it is named otherwise.")
@click.option("--from", "plan_path", required=True, type=click.Path(path_type=Path), metavar="LEVEL.json",
              help="The level plan: a JSON array with one entry per clip (node, src_in, src_out, scale, keys, "
                   "static, static_value).")
@click.option("--no-scale", "no_scale", is_flag=True, help="Leave Scale as it is. Only Rotation is written.")
@click.option("--static-spread", "static_spread", type=float, metavar="DEG",
              help="Decide static or keys with this spread instead of the plan's own static flag: an entry whose "
                   "keys span no more than DEG degrees gets one static Rotation (the midpoint).")
@click.option("--skip-refused", "skip_refused", is_flag=True,
              help="Write the other entries when one is refused. Without it, one refusal stops the run before "
                   "any write.")
@click.option("--dry-run", "dry_run", is_flag=True, help="Read and check every clip, show what would be written, "
                                                         "and write nothing.")
@click.option("--project", help="Path of the project that must be the active one.")
@click.option("--any-project", is_flag=True,
              help="Allow a write to a project that is not a Claude working copy. Needs --project.")
@live_options
@json_option
@click.pass_context
@stops_cleanly(STOPPED_RUN)
def live_keys_set(ctx: click.Context, sequence_id: str, sequence_name: str | None, plan_path: Path, no_scale: bool,
                  static_spread: float | None, skip_refused: bool, dry_run: bool, project: str | None,
                  any_project: bool, lease_wait: float, timeout: float, allow_untested: bool) -> None:
    """Write Motion Rotation keys and one static Motion Scale per clip, from a level plan, and read each back.

    Each entry names its clip by node ID in --sequence. The clip must show the media from src_in to src_out (within
    half a frame), run at normal speed and not be reversed, and have Motion with Scale at index 1 and Rotation at
    index 4. Scale must have no keys (unless --no-scale). Every entry is checked before the first write; one
    refusal stops the run unless --skip-refused.

    The new keys replace the old ones: Rotation keys that the clip had are removed. A key time is seconds from the
    clip start. A key at the clip end, or less than half a frame before it, moves to one frame before the clip end.
    The interpolation of a key is the default of addKey. An entry with "static": true gets one Rotation value and no
    keys. An entry with no keys and "static": false is left alone (level, no change).

    Per clip: Scale first, then Rotation. An entry is done when both read back as written. A clip that holds the
    plan already (its Rotation and, unless --no-scale, its Scale) is skipped as nothing to do (keys_match), and
    nothing is written to it. A failed entry does not stop the others. An entry refused inside its write script
    (precondition: the clip changed after the check) fails, and the run goes on; the in and out points are checked
    again in the Rotation write only, so a Scale written before it stays written, and the entry says so. When any
    other write was not sent, or its outcome is unknown, the run stops there. The project is not saved.

    Run --dry-run first: it reads and checks every clip, shows what would be written, and writes nothing. Read
    every refusal. After a real run the exit status is 0 only when every entry is done or had nothing to do.
    """
    try:
        plan = keys.load_level_plan(plan_path)
        client = _client(lease_wait)
        payload = keys.set_keys(client, plan, sequence_id=sequence_id, sequence_name=sequence_name,
                                scale=not no_scale, static_spread=static_spread, skip_refused=skip_refused,
                                dry_run=dry_run, project=project, any_project=any_project,
                                allow_untested=allow_untested, hard_limit_s=timeout)
    except CliError as err:
        _entries_failure(ctx, err)
        return
    _emit(ctx, payload, _keys_set_text(payload))


# ---------- live place ----------
def _place_planned_line(item: dict) -> str:
    """One line of a dry run: what one shot would get."""
    head = f"  {item['entry']} " + ("" if item.get("name") is None else f"{item['name']} ")
    if item.get("refusal"):
        return head + f"refused | {item['refusal']['code']}: {item['refusal']['reason']}"
    if item.get("skip"):
        return head + f"nothing to do | {item['skip']}"
    what = [call["tool"] for call in item.get("calls") or []] + [call["tool"] for call in item.get("after") or []]
    return head + f"{item['would']} | {item.get('node_id')}: " + ", ".join(what)


def _marks_lines(payload: dict) -> list[str]:
    """Marks: 000f4241 in 3.003 s, out 8.75875 s (set by shot 0). The item marks stay as set."""
    return [f"Marks: {m['item']} in {_seconds_text(m.get('in_seconds'))} s, out {_seconds_text(m.get('out_seconds'))} "
            f"s (set by shot {m.get('set_by')})" for m in payload.get("marks") or []]


def _place_text(payload: dict) -> str:
    if payload.get("dry_run"):
        lines = [_place_planned_line(item) for item in payload["planned"]]
        lines.append("Dry run: nothing was written.")
    else:
        lines = _entry_lines(payload) + _marks_lines(payload)
    return "\n".join(lines + _warning_lines(payload.get("warnings") or []))


@live.command("place")
@click.option("--sequence", "sequence_id", required=True, metavar="ID",
              help="ID of the sequence to place the shots on (see `live state`).")
@click.option("--sequence-name", "sequence_name", metavar="NAME",
              help="The name the sequence must have. Nothing is written when it is named otherwise.")
@click.option("--plan", "plan_path", required=True, type=click.Path(path_type=Path), metavar="SHOTS.json",
              help="The placement plan: {\"fps\", \"shots\": [...]}, as the film planner reads it.")
@click.option("--overwrite", is_flag=True,
              help="Place a shot over clips that lie in its span: they are cut. Without it, such a shot is refused.")
@click.option("--skip-refused", "skip_refused", is_flag=True,
              help="Place the other shots when one is refused. Without it, one refusal stops the run before any "
                   "write.")
@click.option("--dry-run", "dry_run", is_flag=True,
              help="Read and check every shot, show the exact calls, and write nothing. The active sequence is not "
                   "switched.")
@click.option("--project", help="Path of the project that must be the active one.")
@click.option("--any-project", is_flag=True,
              help="Allow a write to a project that is not a Claude working copy. Needs --project.")
@live_options
@json_option
@click.pass_context
@stops_cleanly(STOPPED_RUN)
def live_place(ctx: click.Context, sequence_id: str, sequence_name: str | None, plan_path: Path, overwrite: bool,
               skip_refused: bool, dry_run: bool, project: str | None, any_project: bool, lease_wait: float,
               timeout: float, allow_untested: bool) -> None:
    """Place the shots of a placement plan on a sequence, frame-exact, and check each one.

    Per shot: set_item_in_out marks the part of the project item, then overwrite_clip places it at its start on
    its video and audio track. Overwrite only, never insert. The item marks stay as set, and the output lists
    them. A shot with "audio_only": true is placed with its picture on its track, then the picture is removed and
    the sound kept. A still that comes in shorter than its shot is extended to the planned length.

    Every shot is checked before the first write: the item (by node ID, or by a name only one item has), the
    tracks, overlaps inside the plan, and the clips already in the span. A shot that is already in place is
    skipped, so a re-run finishes a partial run. A clip in the span is refused unless --overwrite. A clip of the
    same item that starts within one frame of the shot's start, but not on it, is refused, also with --overwrite.
    The plan's fps must match the sequence.

    The tools work on the active sequence, so the command makes this sequence active and puts the old one back at
    the end. It does so only when some shot has a step to do: a re-run with every shot in place writes nothing.
    While it runs, the sequence is active for every session that works in Premiere. The first failed
    step stops the run. At the end each shot is read back: start to the frame, in and out to the item frame. The
    project is not saved.

    Run --dry-run first: it reads and checks every shot, shows the exact calls, and writes nothing. Read every
    refusal. After a real run the exit status is 0 only when every entry is done or had nothing to do.
    """
    try:
        plan = place.load_shots(plan_path)
        client = _client(lease_wait)
        payload = place.place_shots(client, live_cli.make_session, plan, sequence_id=sequence_id,
                                    sequence_name=sequence_name, overwrite=overwrite, skip_refused=skip_refused,
                                    dry_run=dry_run, project=project, any_project=any_project,
                                    allow_untested=allow_untested, hard_limit_s=timeout)
    except CliError as err:
        _entries_failure(ctx, err)
        return
    _emit(ctx, payload, _place_text(payload))


# ---------- live effect apply ----------
def _apply_planned_line(item: dict) -> str:
    """One line of a dry run: what one entry would get."""
    head = f"  {item['entry']} {item['name']} "
    if item.get("refusal"):
        return head + f"refused | {item['refusal']['code']}: {item['refusal']['reason']}"
    if item.get("call") is None:
        return head + f"nothing to do | {item.get('skip')}"
    return head + f"apply | apply_effect on {item['node_id']}"


def _index_lines(payload: dict) -> list[str]:
    """V1 clip 0: ProcAmp at effect index 3. One line per entry whose effect is on the clip."""
    return [f"{r['name']} at effect index {r['effect_index']}" for r in payload.get("results") or []
            if r.get("effect_index") is not None and r["status"] in ("done", "skipped")]


def _apply_text(payload: dict) -> str:
    if payload.get("dry_run"):
        lines = []
        for item in payload["planned"]:
            lines.append(_apply_planned_line(item))
            lines += [f"    {line}" for line in _warning_lines(item.get("warnings") or [])]
        lines.append("Dry run: nothing was written.")
    else:
        lines = _entry_lines(payload) + _index_lines(payload)
    return "\n".join(lines + _warning_lines(payload.get("warnings") or []))


def _clip_numbers(text: str | None) -> list[int] | None:
    """--clips 0,3,5 -> [0, 3, 5]. A usage error when a part is not a whole number of 0 or more."""
    if text is None:
        return None
    parts = [part.strip() for part in text.split(",")]
    if not parts or not all(part.isascii() and part.isdigit() for part in parts):
        raise click.UsageError(f"--clips takes clip numbers of 0 or more, split by commas (0,3,5), not {text!r}.")
    return [int(part) for part in parts]


@live_effect.command("apply")
@click.option("--sequence", "sequence_id", required=True, metavar="ID", help="ID of the sequence (see `live state`).")
@click.option("--sequence-name", "sequence_name", metavar="NAME",
              help="The name the sequence must have. Nothing is written when it is named otherwise.")
@click.option("--track", type=click.IntRange(min=0), required=True, metavar="N",
              help="Video track, counted from 0 (V1 is 0).")
@click.option("--clips", "clips_text", metavar="0,3,5",
              help="Numbers of the clips on the track, counted from 0 (see `live clip list`). Not with --all.")
@click.option("--all", "all_clips", is_flag=True, help="Every clip of the track (at most 400). Not with --clips.")
@click.option("--effect", "effect_names", multiple=True, metavar="NAME",
              help="Display name of an effect, such as ProcAmp or \"Lumetri Color\". Repeat it for more effects; "
                   "they are applied in the order named.")
@click.option("--any-order", "any_order", is_flag=True,
              help="Apply Lumetri Color before ProcAmp when they are named that way. Without it such a request is "
                   "refused.")
@click.option("--allow-second", "allow_second", is_flag=True,
              help="Apply an effect to a clip that holds one of that name already. Without it the entry is skipped.")
@click.option("--skip-refused", "skip_refused", is_flag=True,
              help="Apply the other entries when one is refused. Without it, one refusal stops the run before any "
                   "write.")
@click.option("--dry-run", "dry_run", is_flag=True,
              help="Read and check every entry, show the exact calls, and write nothing. The active sequence is not "
                   "switched.")
@click.option("--project", help="Path of the project that must be the active one.")
@click.option("--any-project", is_flag=True,
              help="Allow a write to a project that is not a Claude working copy. Needs --project.")
@live_options
@json_option
@click.pass_context
@stops_cleanly(STOPPED_RUN)
def live_effect_apply(ctx: click.Context, sequence_id: str, sequence_name: str | None, track: int,
                      clips_text: str | None, all_clips: bool, effect_names: tuple, any_order: bool,
                      allow_second: bool, skip_refused: bool, dry_run: bool, project: str | None, any_project: bool,
                      lease_wait: float, timeout: float, allow_untested: bool) -> None:
    """Apply effects to clips of one video track, in render order, and check each one.

    One entry per clip and effect: clip by clip (in the order of --clips, or track order), effects in the order
    named. Track N counts from 0 (V1 is 0). A new effect renders after every effect the clip holds, so name them
    in render order: ProcAmp before Lumetri Color. A request with Lumetri Color first is refused unless
    --any-order. A clip that holds an effect of that name already is skipped (has effect), so a re-run finishes a
    partial run; --allow-second applies a second one. When an effect the clip holds already will change the order
    (an old Lumetri Color before a new ProcAmp), the entry gets a warning. Gyroflow gets its defaults only.

    The tool works on the active sequence, so the command makes this sequence active and puts the old one back at
    the end. While it runs, the sequence is active for every session that works in Premiere. The first failed
    call stops the run. At the end each clip is read back: an entry is done when the clip holds one more effect of
    that name. The output gives the effect index of each effect, for `live effect set` and `live lut set`. The
    project is not saved.

    Run --dry-run first: it reads and checks every entry, shows the exact calls, and writes nothing. Read every
    refusal. After a real run the exit status is 0 only when every entry is done or had nothing to do.
    """
    if clips_text is not None and all_clips:
        raise click.UsageError("Name the clips with --clips or --all, not both.")
    if clips_text is None and not all_clips:
        raise click.UsageError("Name the clips with --clips or --all.")
    clip_numbers = _clip_numbers(clips_text)
    try:
        client = _client(lease_wait)
        payload = apply.apply_effects(client, live_cli.make_session, sequence_id=sequence_id,
                                      sequence_name=sequence_name, track=track, clips=clip_numbers,
                                      effects=list(effect_names), any_order=any_order, allow_second=allow_second,
                                      skip_refused=skip_refused, dry_run=dry_run, project=project,
                                      any_project=any_project, allow_untested=allow_untested, hard_limit_s=timeout)
    except CliError as err:
        _entries_failure(ctx, err)
        return
    _emit(ctx, payload, _apply_text(payload))
