"""`live effect apply`: apply effects to the clips of one video track, in render order, and check each one.

One entry per clip and effect: clip by clip (in the order of --clips, or track order), effects in request order.
The MCP tool apply_effect works on the active sequence only (SRV/tools/effects.js:101-105), so the run goes
through ActiveRun (active.py): session A reads and checks, then the target is made active and confirmed; one MCP
session sends apply_effect per entry; session B puts the active sequence back; a read pass decides each entry.

Order on a clip. The component list shows the effects in reverse render order, and a new component lands at index
2, after Opacity and Motion and before older effects (Plan 2 measures.md section 4). So a new effect renders after
every effect the clip holds, and the requested effects must end the clip's render order, in request order.
ProcAmp must render before Lumetri Color (ORDER_RULE): a request that names them the other way is refused before
any contact unless any_order.

Nothing is saved. A tool reply never decides an entry: the read after the run does (the server warns that QE may
have added an effect even when it reports a failure, SRV/tools/effects.js:69).
"""
from __future__ import annotations

from cli_anything.premiere.core import jsx
from cli_anything.premiere.core.recipes import clips as clip_recipe
from cli_anything.premiere.core.recipes import results, stops
from cli_anything.premiere.core.recipes.active import ActiveRun
from cli_anything.premiere.core.session import DEFAULT_HARD_LIMIT_S
from cli_anything.premiere.errors import CliError

# A request with Lumetri Color before ProcAmp is refused without --any-order.
ORDER_RULE = ("ProcAmp", "Lumetri Color")
GYROFLOW_NOTE = "Gyroflow gets its defaults only. Its sliders cannot be set by a script."
GYROFLOW = "Gyroflow"
CLIP_LIMIT = clip_recipe.CLIP_LIMIT             # 400: one clip_list read covers the whole track
NOT_SENT = "Nothing was sent."
STOPPED_APPLY = "Stopped by Ctrl-C or a stop signal. Check Premiere before another edit."
HAS_EFFECT = "has_effect"


def _whole(value: object, low: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= low


def _check_request(track, clips, effects, any_order: bool) -> None:
    """The checks before any contact. Raises CliError("bad_args") or CliError("bad_order")."""
    if not _whole(track):
        raise CliError("bad_args", f"--track must be a whole number of 0 or more (V1 is 0), not {track!r}. {NOT_SENT}")
    if clips is not None:
        if not isinstance(clips, list) or not clips or not all(_whole(c) for c in clips):
            raise CliError("bad_args", f"--clips must list clip numbers of 0 or more, not {clips!r}. {NOT_SENT}")
        twice = sorted({c for c in clips if clips.count(c) > 1})
        if twice:
            raise CliError("bad_args", f"--clips names clip {', '.join(map(str, twice))} more than once. {NOT_SENT}")
    if not isinstance(effects, list) or not effects:
        raise CliError("bad_args", f"Name at least one effect with --effect. {NOT_SENT}")
    for name in effects:
        if not isinstance(name, str) or not name.strip():
            raise CliError("bad_args", f"An effect name must be a non-empty text, not {name!r}. {NOT_SENT}")
    twice = sorted({name for name in effects if effects.count(name) > 1})
    if twice:
        raise CliError("bad_args", f"--effect names {', '.join(twice)} more than once. One run applies an effect "
                                   f"once per clip. {NOT_SENT}")
    if not any_order and _rule_broken(effects):
        first, second = ORDER_RULE
        raise CliError("bad_order", f"The request puts {second} before {first}. {first} must render first, so name "
                                    f"it first, or use --any-order to apply them as named. {NOT_SENT}")


def _rule_broken(order: list[str]) -> bool:
    """True when some ORDER_RULE[1] renders before some ORDER_RULE[0] in `order` (a render order or a request)."""
    first, second = ORDER_RULE
    if first not in order or second not in order:
        return False
    return order.index(second) < len(order) - 1 - order[::-1].index(first)


def _tail_ok(render_order: list[str], wanted: list[str]) -> bool:
    """The wanted names end the render order, in that order."""
    return not wanted or render_order[-len(wanted):] == wanted


def _first_index(effects: list[str], name: str) -> int | None:
    """The first component of that display name, in component order (the index `live effect set` takes)."""
    return effects.index(name) if name in effects else None


def _shape(reply, sequence_id: str) -> dict:
    """The clip_list reply, checked: {"clips": [shaped clip], "total"}. Raises CliError("bad_reply")."""
    bad = CliError("bad_reply", "The clip list script returned an answer in an unexpected shape.")
    if not isinstance(reply, dict) or not isinstance(reply.get("sequence"), dict):
        raise bad
    found, total = reply.get("clips"), reply.get("total")
    if not isinstance(found, list) or not _whole(total) or not all(isinstance(c, dict) for c in found):
        raise bad
    if reply.get("used_active") or reply["sequence"].get("id") != sequence_id:
        raise CliError("bad_reply", f"The clip list did not read sequence {sequence_id}.")
    shaped = []
    for raw in found:
        clip = clip_recipe._shape(raw)
        effects = clip.get("effects")
        if not isinstance(clip.get("node_id"), str) or not (
                effects is None or (isinstance(effects, list) and all(isinstance(e, str) for e in effects))):
            raise bad
        shaped.append(clip)
    return {"clips": shaped, "total": total}


def _read_args(sequence_id: str, track: int) -> dict:
    return {"sequence_id": sequence_id, "track_type": "video", "track": track, "offset": 0, "limit": CLIP_LIMIT,
            "detail": False}


def _reply_fault(reply: dict) -> str | None:
    """None when apply_effect reports the outcome verified. A fault text otherwise (the read decides)."""
    structured = reply.get("structured")
    data = structured.get("data") if isinstance(structured, dict) else None
    if reply.get("is_error"):
        return f"apply_effect: {reply.get('text') or 'the tool reported an error'}"
    outcome = data.get("outcome") if isinstance(data, dict) else None
    if outcome != "verified":
        return f"apply_effect: the tool reported the outcome {outcome!r}, not verified"
    return None


def _order_text(order: list[str]) -> str:
    return " > ".join(order) if order else "(no effects)"


def apply_effects(client, make_session, *, sequence_id: str, sequence_name=None, track: int, clips: list[int] | None,
                  effects: list[str], any_order=False, allow_second=False, skip_refused=False, dry_run=False,
                  project=None, any_project=False, allow_untested=False, hard_limit_s=DEFAULT_HARD_LIMIT_S) -> dict:
    """Apply `effects` to the clips of video track `track` (from 0) of the sequence with sequence_id.

    clips None means every clip of the track. Order:
    1. Before any contact: at least one effect, no name twice, no clip twice (bad_args); the order rule (bad_order).
    2. Session A (ActiveRun.prepare): clip_list reads the track (too_many_clips above 400). Per entry: no_clip and
       effects_unread refuse; "has effect" is nothing to do unless allow_second; a warning when the clip's render
       order after the run will not end with the request, or will break ORDER_RULE (unless any_order). Gyroflow
       entries get GYROFLOW_NOTE. A refusal stops the run here unless skip_refused. dry_run returns here with
       "planned". The target is made active only when an entry is left to apply.
    3. MCP session (no lease): apply_effect {node_id, effect_name} with the guard args, per entry. A reply with
       is_error or an outcome other than verified ends the session; the rest stays not_run.
    4. Session B (ActiveRun.finish), then one clip_list read. An entry is done when the clip holds one more
       component of that name than before. Its effect_index is the first component of that name. The requested
       effects on a clip must end its render order in request order: else a warning (predicted in step 2) or
       failed with reason_code "render_order".

    Returns results.summary(records, warnings, sequence=..., left_switched=..., stopped=...) and, with dry_run,
    "dry_run": True and "planned". A record has the extras "effect" and "effect_index". Raises a CliError that
    carries this payload: refused, too_many_clips, the error that stopped the run, not_done, or not_restored.
    """
    _check_request(track, clips, effects, any_order)
    run = ActiveRun(client, make_session, sequence_id=sequence_id, sequence_name=sequence_name, project=project,
                    any_project=any_project, allow_untested=allow_untested, hard_limit_s=hard_limit_s,
                    call_timeout_s=hard_limit_s)
    body = jsx.load("clip_list")
    records: list[dict] = []
    warnings: list[str] = []
    state: dict = {"planned": None, "entries": None, "clips": None}

    def payload(**extra) -> dict:
        notes = list(run.warnings) + [w for w in warnings if w not in run.warnings]
        return results.summary(records, notes, sequence=dict(run.sequence), left_switched=run.left_switched,
                               stopped=run.stopped, **extra)

    def dry_extra() -> dict:
        return {"dry_run": True, "planned": state["planned"]} if dry_run and state["planned"] is not None else {}

    def check(session) -> list[int]:
        info = _shape(session.read(body, _read_args(run.sequence["id"], track)), run.sequence["id"])
        if info["total"] > CLIP_LIMIT:
            raise CliError("too_many_clips", f"Track V{track + 1} holds {info['total']} clips; this command reads "
                                             f"at most {CLIP_LIMIT}. Nothing was written.")
        on_track = info["clips"]
        numbers = list(range(len(on_track))) if clips is None else list(clips)
        if not numbers:
            warnings.append(f"Track V{track + 1} holds no clips, so there is nothing to apply.")
        entries, per_clip = _plan(numbers, on_track, track, effects, any_order, allow_second, records)
        state["entries"], state["clips"] = entries, per_clip
        state["planned"] = [_planned(e, records[e["entry"]], run) for e in entries]
        if any(r["status"] == "skipped" and r["skip"] == results.REFUSED for r in records) and not skip_refused:
            raise results.refused_error(records, payload(**dry_extra()))
        return [e["entry"] for e in entries if records[e["entry"]]["status"] == "not_run"]

    try:
        todo = run.prepare(check, switch=lambda left: not dry_run and bool(left))
    except BaseException as exc:
        error = _run_error(exc)
        fault = run.finish()
        error = error or fault
        error.payload = payload(**dry_extra())
        raise error from None
    if dry_run or not todo:
        run.finish()
        if dry_run:
            return payload(**dry_extra())
        done = payload()
        error = results.outcome_error(records, done)   # with --skip-refused, a refused entry is not success
        if error is not None:
            raise error
        return done

    entries = state["entries"]
    work: dict = {"error": None, "attempted": [], "faults": {}, "flight": None}
    try:
        _send(run, entries, todo, work)
    except BaseException as exc:
        if stops.is_stop(exc):
            run.stopped = True
        work["error"] = work["error"] or _run_error(exc)
    fault = run.finish()
    work["error"] = work["error"] or fault
    _check_after(run, body, track, entries, state["clips"], records, work)
    done = payload()
    error = work["error"]
    if error is None and run.stopped:
        error = CliError("interrupted", STOPPED_APPLY)
    if error is None:
        error = results.outcome_error(records, done)
    if error is None and run.left_switched is not None:
        error = CliError("not_restored", "Every entry is done, but the active sequence could not be put back. The "
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
        return CliError("interrupted", STOPPED_APPLY)
    if not isinstance(exc, Exception):
        raise exc
    return CliError("recipe_error", f"{type(exc).__name__}: {exc}")


def _plan(numbers, on_track, track, effects, any_order, allow_second, records) -> tuple[list[dict], dict]:
    """Session A: one record and one entry per clip and effect. Returns (entries, per clip number: {"node_id",
    "before" (the component list), "tail_ok" (whether the predicted render order after the run ends with the
    request)})."""
    entries: list[dict] = []
    per_clip: dict = {}
    for number in numbers:
        clip = on_track[number] if number < len(on_track) else None
        node_id = clip["node_id"] if clip is not None else None
        before = clip.get("effects") if clip is not None else None
        mine = []
        for name in effects:
            k = len(records)
            record = results.new_record(k, {"track": track, "clip": number, "node_id": node_id, "effect": name},
                                        name=f"V{track + 1} clip {number}: {name}")
            record.update(effect=name, effect_index=None)
            records.append(record)
            entry = {"entry": k, "clip": number, "node_id": node_id, "effect": name, "apply": False}
            entries.append(entry)
            mine.append(entry)
            if clip is None:
                results.mark_skipped(record, results.REFUSED, "no_clip",
                                     f"track V{track + 1} holds {len(on_track)} clips; there is no clip {number}.")
            elif before is None:
                results.mark_skipped(record, results.REFUSED, "effects_unread",
                                     f"Premiere did not report the effects of clip {number} ({node_id}), so it is "
                                     "not known whether it holds this effect already.")
            elif name in before and not allow_second:
                index = _first_index(before, name)
                record["effect_index"] = index
                results.mark_skipped(record, results.NOTHING_TO_DO, HAS_EFFECT,
                                     f"has effect: the clip holds {name} at effect index {index}. Use "
                                     "--allow-second to apply a second one.", now={"effect_index": index})
            else:
                entry["apply"] = True
            if name == GYROFLOW and record["skip"] != results.REFUSED:
                record["warnings"].append(GYROFLOW_NOTE)
        if clip is None or before is None:
            continue
        order = clip_recipe._render_order(before) or []
        predicted = order + [e["effect"] for e in mine if e["apply"]]
        wanted = [name for name in effects if name in predicted]
        tail_ok = _tail_ok(predicted, wanted)
        per_clip[number] = {"node_id": node_id, "before": list(before), "tail_ok": tail_ok}
        lines = []
        if not tail_ok:
            lines.append(f"After the run the clip will render {_order_text(predicted)}, so the requested effects "
                         f"will not be the last ones in request order ({_order_text(wanted)}).")
        if not any_order and _rule_broken(predicted) and not _rule_broken(order):
            first, second = ORDER_RULE
            lines.append(f"After the run the clip will render {_order_text(predicted)}: an existing {second} "
                         f"renders before the new {first}.")
        for entry in mine:
            if entry["apply"]:
                records[entry["entry"]]["warnings"].extend(lines)
    return entries, per_clip


def _planned(entry: dict, record: dict, run: ActiveRun) -> dict:
    """One entry of a dry run: what would be sent."""
    call = None
    if entry["apply"]:
        call = {"tool": "apply_effect", "args": {"node_id": entry["node_id"], "effect_name": entry["effect"],
                                                 **run.guard_args()}}
    refused = record["status"] == "skipped" and record["skip"] == results.REFUSED
    would = "refused" if refused else "apply" if entry["apply"] else "nothing to do"
    return {"entry": entry["entry"], "name": record["name"], "clip": entry["clip"], "node_id": entry["node_id"],
            "effect": entry["effect"], "would": would, "call": call,
            "refusal": {"code": record["reason_code"], "reason": record["reason"]} if refused else None,
            "skip": record["reason"] if would == "nothing to do" else None, "effect_index": record["effect_index"],
            "warnings": list(record["warnings"])}


def _send(run: ActiveRun, entries: list[dict], todo: list[int], work: dict) -> None:
    """The MCP session: apply_effect per entry, in order. The first fault ends it."""
    by_entry = {e["entry"]: e for e in entries}

    def steps(mcp, guard_args) -> None:
        for k in todo:
            entry = by_entry[k]
            work["attempted"].append(k)
            work["flight"] = k
            reply = mcp.call("apply_effect", {"node_id": entry["node_id"], "effect_name": entry["effect"],
                                              **guard_args}, timeout_s=run.call_timeout_s)
            work["flight"] = None
            fault = _reply_fault(reply)
            if fault is not None:
                work["faults"][k] = fault
                return

    error = run.mcp(steps)
    work["error"] = error
    k = work["flight"]
    if k is not None:
        # The call did not end. The MCP session raises none of stops.NOT_SENT_CODES (its "interrupted" can come
        # after the request went out), so the call may have landed: its outcome is unknown, as in place.py.
        work["faults"][k] = (f"apply_effect: the call did not end ({error.code if error else 'unknown'}); it may "
                             "have landed")
        work["unknown"] = {k}


def _check_after(run: ActiveRun, body: str, track: int, entries: list[dict], per_clip: dict, records: list[dict],
                 work: dict) -> None:
    """Session B's read pass: each entry that ran is done or failed by what the track holds now; then the render
    order of each clip."""
    attempted = list(work["attempted"])
    if not attempted:
        return
    unknown = work.get("unknown") or set()
    faults = work["faults"]
    try:
        reply, _shown = run.look(body, _read_args(run.sequence["id"], track))
        info = _shape(reply, run.sequence["id"])
    except BaseException as exc:
        error = _run_error(exc)
        if stops.is_stop(exc):
            run.stopped = True
        work["error"] = work["error"] or error
        for k in attempted:
            results.mark_failed(records[k], faults.get(k) or f"the clip could not be read back after the run "
                                f"({error.code}: {error.message})", known=False)
        return
    by_node = {c["node_id"]: c for c in info["clips"]}
    read_clips = {entries[k]["clip"] for k in attempted}
    for entry in entries:
        k = entry["entry"]
        record = records[k]
        clip = by_node.get(entry["node_id"])
        now_effects = clip.get("effects") if clip is not None else None
        if entry["clip"] in read_clips and isinstance(now_effects, list) and record["skip"] == results.NOTHING_TO_DO:
            index = _first_index(now_effects, entry["effect"])
            record["effect_index"] = index
            record["now"] = {"effect_index": index}
        if k not in attempted:
            continue
        fault = faults.get(k)
        if not isinstance(now_effects, list):
            why = ("the clip is not on the track after the run" if clip is None
                   else "Premiere did not report the clip's effects after the run")
            results.mark_failed(record, f"{fault} ({why})" if fault else why, known=k not in unknown)
            continue
        had = per_clip[entry["clip"]]["before"].count(entry["effect"])
        has = now_effects.count(entry["effect"])
        index = _first_index(now_effects, entry["effect"])
        if has == had + 1:
            record["effect_index"] = index
            results.mark_done(record, {"effect_index": index})
            if fault is not None:
                record["warnings"].append(f"The tool reported a fault, but the read after shows the new "
                                          f"{entry['effect']} on the clip: {fault}")
            continue
        why = (f"the read after the run shows {has} {entry['effect']} on the clip, it held {had} before"
               + ("" if has > had else ": nothing was added"))
        results.mark_failed(record, f"{fault} ({why})" if fault else why,
                            now={"effect_index": index}, known=k not in unknown)
    _check_order(entries, per_clip, by_node, records)


def _check_order(entries: list[dict], per_clip: dict, by_node: dict, records: list[dict]) -> None:
    """The requested effects on a clip must end its render order, in request order."""
    for number, plan in per_clip.items():
        mine = [e for e in entries if e["clip"] == number]
        done = [e for e in mine if records[e["entry"]]["status"] == "done"]
        if not done:
            continue
        clip = by_node.get(plan["node_id"])
        order = clip_recipe._render_order(clip.get("effects") if clip is not None else None)
        if order is None:
            continue
        wanted = [e["effect"] for e in mine if e["effect"] in order]
        if _tail_ok(order, wanted):
            continue
        text = (f"The clip renders {_order_text(order)}: the requested effects are not the last ones in request "
                f"order ({_order_text(wanted)}).")
        for entry in done:
            record = records[entry["entry"]]
            if not plan["tail_ok"]:
                if text not in record["warnings"]:
                    record["warnings"].append(text)
                continue
            index, now = record["effect_index"], record["now"]
            results.mark_failed(record, f"{entry['effect']} landed at effect index {index}, but {text[0].lower()}"
                                        f"{text[1:]}", now=now)
            record["reason_code"] = "render_order"
