# Premiere CLI, Plan 3: milestone 2 (keys, placing and effects from plan files)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the three loops of single MCP calls that cost the film session the most work into one command each, every one reading the session's own plan file: `live keys set` (Rotation keys and one static Scale per clip), `live place` (shots from a placement plan), `live effect apply` (effects in render order). Add `live keys get`, and fix N12.1.

**Architecture:** Every write goes through `WriteSession` (Plan 2). Keys use the CLI's own ExtendScript: one script per clip writes all its keys and reads every key back, and finds the clip by sequence ID, so the sequence need not be active. Placing and applying effects use the MCP tools that had no failure in v10, wrapped in the two-session pattern of `live frame export`: session A checks and makes the sequence active, one MCP session runs the tool calls without the lease, session B puts the active sequence back, and an own read checks what landed. A shared result helper gives every entry one of four statuses and one exit rule.

**Tech Stack:** Python 3 (click), ExtendScript ES3 through the file bridge, `premiere-pro-mcp` 1.25.0 over stdio (`set_item_in_out`, `overwrite_clip`, `remove_from_timeline`, `set_clip_duration`, `apply_effect`), pytest with the fake panel and the fake MCP server.

**Spec:** `premiere/agent-harness/docs/2026-11-19-premiere-cli-milestone-2-design.md` (approved 2026-11-19 by the film-edit session). It builds on `2026-11-18-premiere-cli-design.md` and Plans 1 and 2.

**Facts for the builders (local, not in git):** `.superpowers/sdd/milestone-2-facts.md` (server and CLI facts with file and line; "SRV/", "CLI/", "WS2/" are defined at its top), `.superpowers/sdd/milestone-2-notes-76.md` (the film session's practice), `.superpowers/sdd/2026-11-18-premiere-cli-plan-2-recipes/measures.md` (Plan 2 measures). This plan's workspace: `.superpowers/sdd/2026-11-19-premiere-cli-plan-3-milestone-2/` (measure scripts and `measures.md` with the results of Tasks 3 and 5).

## Decisions this plan takes (the design leaves them to the builder)

1. Keys write order: the route of the server's `paste_clip_attributes` (`SRV/tools/clipboard.js:253-258`: `setTimeVarying(false)`, `setTimeVarying(true)`, `addKey`, `setValueAtKey`), changed only where M1 to M3 say so (Task 3 has the decision table). The read-back decides `done` in any case.
2. In keys set, a clip whose read-back fails is `failed` and the run goes on to the next clip. Only an error whose outcome is unknown, or a stop, ends the run. Placing and effects stop at the first failure (design 8 and 9).
3. `--static-spread DEG` makes the command apply the rule to every entry and ignore the file's `static` and `static_value`; each entry where that changes the outcome gets a warning.
4. Placing: an `item` that is a name is resolved to a node ID in session A (two items with that name: refused `ambiguous_item`). The calls send the node ID. Two shots of the plan that overlap on the same track are refused `plan_overlap`. The plan's `fps` must match the sequence frame rate (`fps_mismatch`). The same item at the same start with another in or out is refused `placed_differently` (the server refuses it as well, and `--overwrite` cannot help).
5. Placing: after a failure in the first MCP session, the second session (picture removal, still extension) still runs for the shots placed before the failure, unless the failure was a stop or left a recovery marker. A re-run finishes a half-done sound-only or still shot (it does only the missing step).
6. Effects: entries run clip by clip (clip 0 ProcAmp, clip 0 Lumetri Color, clip 1 ProcAmp, ...), so a stop leaves whole clips behind it. `--all` on a track with more than 400 clips is refused `too_many_clips`.
7. One pattern for MCP calls in a recipe: two write sessions, built from the helpers of `frames.py`. `WriteSession.paused()` stays as it is (unused, with its own tests); removing it is parked.
8. Unknown keys in the plan files pass (the formats belong to the film session's scripts) and are named in `warnings`. Test data: shot names of `shots_v10.json` become "S01" to "S25"; the level file's names ("S02 C2246": shot number and camera file) stay.

## Global Constraints

- All constraints of Plans 1 and 2 still hold, except the three replaced below: no push without Urko's word; the files of session `premier-pro-mcp-76` (`core/prproj.py`, `tests/test_core.py`, `tests/test_full_e2e.py`, `tests/_helpers.py`) are not edited; `premiere_cli.py` imports no new module at import time; a fault in new code must not break `status`, `project`, `sequence`, `clip`, `colormode`; `tests/gates/run_gates.py` is not touched and not re-run.
- Pinned strings stay: the root help contains "Read Premiere Pro project files"; the version is `0.1.0`; `status` keeps `"writes_projects": false` and every other key.
- Output contract: `--json` on every command; success `{"ok": true, ...}`; failure `{"ok": false, "error", "code"}` plus the payload, exit status 1; `CliError(code, message)`.
- Replaced (Plan 2 "exit 1 when stored or rendered is false"): the three plan commands follow design section 6. Every payload has `results`, `counts`, `first_not_run`, `stored`, `rendered` (always `null`) and `warnings`. Exit 0 only when every entry is `done` or `skipped` for nothing to do. `stored` is `true` then, `null` when an entry's outcome is unknown, else `false`. `live keys get` is a read.
- Replaced (Plan 2 "No recipe calls an MCP tool that takes a node ID"): a node-ID tool (`apply_effect`, `remove_from_timeline`, `set_clip_duration`) is called only inside `ActiveRun` (Task 6), after the target sequence was made active and confirmed by a fresh state read, and every call carries `expected_project_path` and `expected_cep_target: {"backend": "cep", "sequence_id": <id>}`.
- Replaced (Plan 2 "`--save` comes with milestone 2"): no command saves the project (design section 3).
- Every write recipe takes `--sequence ID` (required), `--sequence-name NAME`, `--dry-run`, `--skip-refused`, `--project PATH`, `--any-project`, `--allow-untested`, `--lease-wait`, `--timeout`. Every entry is checked before the first write; a refusal stops the run before any write unless `--skip-refused`.
- No write recipe reads the selection, the playhead or the active sequence to find its target. Never call the MCP server while this process holds the lease. `send` is never called inside a `hold_signals()` section.
- "Nothing was sent" is decided only by `stops.not_sent` / `stops.NOT_SENT_CODES` (`core/recipes/stops.py`), never by a local list of codes (Plan 2 final review, recommendation 2).
- An Exception that is neither a CliError nor a stop, after the active sequence was switched, becomes `CliError("recipe_error")` and takes the normal path: put back, then the payload with `left_switched` (recommendation 3).
- A value a plan states about the target (the clip's in and out ticks for keys) is checked again inside the write script, before its first host write (recommendation 1).
- The CLI never sets `unsafe-script` and never acknowledges a recovery. ES3 rules of Plan 2 (no `let`, `const`, arrow functions, template strings, `JSON.*`, `Object.keys`, `forEach/map/filter/indexOf`, `trim`). Arguments enter a script through `jsx.build(body, args)` only. Ticks cross as strings; Python computes them as integers. Every host call in a `.jsx` file follows a call of the server's code, cited by file and line in the script's head comment (the facts file names them).
- Tests never contact the real Premiere, never start the real `premiere-pro-mcp`, never run `osascript`, never read `/Volumes/`. Test data lives in `tests/data/` and holds no drive path and no film name. Live tests stay in `tests/test_live_e2e.py` behind `PREMIERE_LIVE_E2E=1`.
- Live work (Tasks 3, 5, 8): the controller only, with Urko's word in its own window; sessions 76 and 78 told before and after; only copies with "Claude" in the name; the v10 film project itself is never opened; only Urko clicks dialogs; close without saving; `doctor` and `recovery show` clean at the end. A subagent may write measure scripts and tests, and runs nothing live.
- Python: no `multiprocessing`, `Pool`, `ProcessPoolExecutor`. Every wait has a limit.
- Writing rules for prose in help texts, messages, documents and this plan's outputs: the user's global writing rules (CLAUDE.md, "Writing Style"); plain words, short sentences, no em-dashes.
- Test command: `uv run --no-project --with pytest --with click --with prompt-toolkit --with defusedxml --python /opt/homebrew/bin/python3 python -m pytest <args> -p no:cacheprovider`. Unit suite: `cli_anything/premiere/tests -q` (1400 passed, 1 skipped at the start of this plan).
- Commits: the controller commits each finished and reviewed task on `feat/premiere-cli`. Implementers do not commit.

## Review Focus

1. A key at the clip end, or within half a frame of it, on a clip whose length is a whole number of frames that is not a whole number of key steps. Expected: moved to the end minus one frame, exact in ticks, listed with old and new time; one tick earlier than the half-frame line is not moved; two keys in one frame after the move: `key_clash`. Tests in Task 2.
2. A level plan made before the clip was trimmed or slipped, or a node ID that exists only in another sequence or another open project. Expected: `clip_changed` or `no_clip` before any write; the same check again inside the write script (in and out ticks), so an edit between the check and the write refuses with `precondition` and changes nothing. Tests in Task 4.
3. The same project item placed twice by one plan at different starts (S22 and S23 share an item, as do S24 and S25). Expected: the second `set_item_in_out` sets new marks after the first shot was placed; session B matches each shot by item, track and start, never by item alone; a re-run after a stop between S22 and S23 skips S22 (`already placed`) and places S23. Test in Task 6.
4. A run that fails in the middle: a tool error at shot 13 of 25. Expected: the active sequence is put back; shots 0 to 12 are `done` by the read-back, 13 is `done` or `failed` by what the read finds (never by the tool's word alone), 14 to 24 are `not_run`, `first_not_run` is 14, exit 1; a re-run of the same plan skips 0 to 12 and exits 0 when the rest lands. With a recovery marker the put-back is not written and `left_switched` names the sequence. Test in Task 6, the same shape for effects in Task 7.
5. A clip that holds an effect of the requested name already, or a request with Lumetri Color before ProcAmp. Expected: `skipped` with "has effect" (exit 0 when that is the only reason), a second one only with `--allow-second`; the order request refused `bad_order` before any contact unless `--any-order`; an existing Lumetri that makes a new ProcAmp render after it gives a warning on that entry. Tests in Task 7.

---

## File structure

| File | Responsibility |
|---|---|
| `core/recipes/results.py` (new) | Per-entry records, statuses, exit rule, failure payload |
| `core/recipes/keys.py` (new) | Key math, level plan check, `get_keys`, `set_keys` |
| `core/recipes/active.py` (new) | `ActiveRun`: session A, MCP sessions, read passes, session B, built on `frames.py` helpers |
| `core/recipes/place.py` (new) | Shot math (port of the film planner), `place_shots` |
| `core/recipes/apply.py` (new) | `apply_effects` |
| `core/recipes/lut.py` (modify) | N12.1 |
| `core/scripts/keys.jsx` (new, include) | Key helpers: time object, property by name, key read |
| `core/scripts/keys_get.jsx`, `keys_check.jsx`, `keys_write.jsx`, `place_read.jsx` (new) | One script per step |
| `live_recipes.py` (modify) | `live keys get`, `live keys set`, `live place`, `live effect apply`; shared entry printers |
| `tests/fakes.py` (add only) | `KeysModel`; `TimelineModel` shared with the fake MCP server; new fake tools |
| `tests/data/` (new) | `level_v10.json`, `shots_v10.json`, `place_v10.json`, cleaned copies |
| `tests/test_results.py`, `test_recipes_keys.py`, `test_active.py`, `test_recipes_place.py`, `test_recipes_apply.py` (new) | Unit tests |
| `tests/test_live_e2e.py`, `README.md`, `skills/SKILL.md`, `tests/TEST.md`, `PREMIERE.md`, `premiere_cli.py` (`REPL_COMMANDS` only) (modify) | Live tests and documents |

All paths below are relative to `premiere/agent-harness/cli_anything/premiere/` unless they start with `.superpowers/` or `premiere/`.

One record per entry, in every plan command (Task 1 makes it):

```
{"entry": int, "name": str | None, "target": {...}, "status": "done" | "skipped" | "failed" | "not_run",
 "skip": null | "nothing_to_do" | "refused", "reason_code": str | null, "reason": str | null,
 "now": <what the read-back holds> | null, "error": str | null, "warnings": [str]}
```

Recipes add their own keys to a record (keys set: `mode`, `moved`; place: `calls`, `marks`, `clips`; apply: `effect`, `effect_index`).

---

### Task 1: N12.1 and the shared result helper

**Files:**
- Create: `core/recipes/results.py`, `tests/test_results.py`
- Modify: `core/recipes/lut.py`, `tests/test_recipes_lut.py`, `live_recipes.py`

**Interfaces:**
- Consumes: `CliError`.
- Produces:

```python
# core/recipes/results.py
STATUSES = ("done", "skipped", "failed", "not_run")
NOTHING_TO_DO, REFUSED = "nothing_to_do", "refused"
def new_record(entry: int, target: dict, *, name: str | None = None) -> dict          # status "not_run"
def mark_done(record: dict, now: object) -> dict
def mark_skipped(record: dict, kind: str, code: str, reason: str, now: object = None) -> dict   # kind NOTHING_TO_DO or REFUSED
def mark_failed(record: dict, error: str, now: object = None, *, known: bool = True) -> dict
    # known False: the outcome is unknown (now stays None); summary() then gives stored None
def counts(records: list[dict]) -> dict          # {"done", "skipped", "refused", "failed", "not_run"}; refused counts skipped-refused only
def succeeded(records: list[dict]) -> bool       # every record done, or skipped with NOTHING_TO_DO
def first_not_run(records: list[dict]) -> int | None
def summary(records: list[dict], warnings: list[str], **extra) -> dict
    # {"results", "counts", "first_not_run", "stored", "rendered": None, "warnings", **extra}
def refused_error(records: list[dict], payload: dict) -> CliError
    # code "refused"; names the first refused entry: "entry 3 (S04): clip_changed: <reason> Nothing was written.
    # Use --skip-refused to run the other entries." Carries payload.
def outcome_error(records: list[dict], payload: dict) -> CliError | None
    # None when succeeded(); else CliError("not_done", "<n> done, <n> skipped, <n> failed, <n> not run. The first
    # entry that did not run is <n>.") with payload (the last sentence only when first_not_run is not None)

# live_recipes.py
def _entry_lines(payload: dict) -> list[str]     # "  3 S04 done | ..." one line per record, then the counts
def _entries_failure(ctx, err: CliError) -> None # prints the payload as _frame_failure does, then _fail
```

- N12.1 (design 11): in `set_lut`, when `--prove` ran and `out["rendered"]` is `True` or `False`, remove `NOT_PROOF` from `out["warnings"]` right after the verdict, before `fail(...)` is raised for `not_rendered`. It stays without `--prove`, and with `rendered` `null` (no verdict: the read-back is still all there is).

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_results.py
def test_new_record_is_not_run():                 # status "not_run", skip None, warnings []
def test_exit_rule():                             # [done, skipped nothing_to_do] -> succeeded True, stored True;
                                                  # [done, skipped refused] -> False, stored False; [] -> True
def test_unknown_outcome_gives_stored_none():     # mark_failed(known=False) -> summary stored None, rendered None
def test_counts_and_first_not_run():              # done, failed, not_run, not_run -> counts 1/0/0/1/2, first_not_run 2
def test_refused_error_names_the_first_refusal(): # code "refused", "entry 1 (S02): clip_changed:" and "--skip-refused" in message, payload attached
def test_outcome_error_none_when_succeeded():     # None; else code "not_done" with the counts sentence
# tests/test_recipes_lut.py
def test_prove_true_drops_the_use_prove_warning(...):   # rendered True -> NOT_PROOF not in warnings
def test_prove_false_drops_the_use_prove_warning(...):  # rendered False, exit 1 not_rendered -> NOT_PROOF not in the payload warnings
def test_prove_null_keeps_the_warning(...):             # rendered None -> NOT_PROOF in warnings
def test_no_prove_keeps_the_warning(...):               # without --prove -> NOT_PROOF in warnings
```

  Existing tests that pin `NOT_PROOF` on a run with a verdict flip to `not in`: check lines 163, 176, 187, 268, 377, 493 and 506 of `tests/test_recipes_lut.py` (506 is a `rendered` false run; 377 and 493 are verdict runs if their setup has `--prove`). The others stay.
- [ ] **Step 2: Run and see them fail.** Test command with `cli_anything/premiere/tests/test_results.py cli_anything/premiere/tests/test_recipes_lut.py -q`. Expected: FAIL (no module `results`; the warning still there).
- [ ] **Step 3: Implement.** `live effect set` keeps its own payload; it is not moved to the helper.
- [ ] **Step 4: Run the unit suite.** Expected: all pass.
- [ ] **Step 5: Controller commits:** `fix(premiere): no "Use --prove" warning after a verdict; shared entry results`.

### Task 2: `live keys get` and the key math

**Files:**
- Create: `core/recipes/keys.py`, `core/scripts/keys.jsx`, `core/scripts/keys_get.jsx`, `tests/test_recipes_keys.py`, `tests/data/level_v10.json`
- Modify: `live_recipes.py` (group `keys`, command `get`), `tests/fakes.py` (`KeysModel`)

**Interfaces:**
- Consumes: `guarded_state`, `run_script`, `effects._check_target`, `clips._tick`, `clips._seconds`, `clips.TICKS_PER_SECOND`.
- Produces:

```python
# core/recipes/keys.py
KEY_TOLERANCE = 0.0001
STATIC_SPREAD = 0.75
class KeyFault(CliError)        # code "negative_key" or "key_clash"; message names the key
def key_ticks(keys: list[dict], *, length_ticks: int, frame_ticks: int) -> list[dict]
    # keys: [{"time_seconds", "value"}] -> [{"seconds", "ticks" (int, clip-relative), "value", "moved_from" (seconds or None)}]
def static_rule(values: list[float], spread: float = STATIC_SPREAD) -> dict
    # {"static": spread_now <= spread, "value": round((max + min) / 2, 2), "spread": round(max - min, 2)}
def entry_mode(entry: dict, *, static_spread: float | None = None) -> dict
    # {"mode": "keys" | "static" | "none", "value": float | None, "spread": float, "source": "file" | "rule"}
def get_keys(client, target: dict, *, sequence_id: str, effect: str = "Motion", prop: str = "Rotation",
             property_index: int | None = None, allow_untested=False, hard_limit_s=DEFAULT_HARD_LIMIT_S) -> dict
```

- `key_ticks` (an algorithm the tests do not fully pin, so the body is given):

```python
out = []
for key in keys:
    seconds = key["time_seconds"]
    if seconds < 0:
        raise KeyFault("negative_key", f"a key at {seconds} s is before the clip start.")
    ticks, moved = round(seconds * TICKS_PER_SECOND), None
    if 2 * ticks >= 2 * length_ticks - frame_ticks:          # at or past the end minus half a frame
        ticks, moved = length_ticks - frame_ticks, seconds    # the end minus one frame, exact in ticks
    out.append({"seconds": seconds, "ticks": ticks, "value": key["value"], "moved_from": moved})
frames = [k["ticks"] // frame_ticks for k in out]             # the clash check runs after the move
if len(set(frames)) != len(frames): raise KeyFault("key_clash", ...)   # names both keys
```

  `length_ticks` is the clip's `end_ticks - start_ticks` (sequence ticks; equal to `out - in` at speed 1). `frame_ticks` is `int(seq.timebase)` (10594584000 at 23.976). The media ticks of a key are `int(in_ticks) + ticks` (`SRV/bridge/script-builder.js:1917-1943`).
- `entry_mode`: `static_spread` None and the entry has `static` (bool): follow it (`static` True needs a number in `static_value`, else `bad_plan`); `keys` empty and `static` False: mode "none" ("level, no change"). Otherwise `static_rule` over the key values with `STATIC_SPREAD` when `static_spread is None`, else `static_spread` (0 is a real value); no keys gives "none". When the file is followed, its `static_value` is used. On the 11 keyed v10 entries `key_ticks` gives no clash and moves 11 end keys (checked).
- `keys.jsx` (function definitions only): `__keyTime(ticks)` (`var t = new Time(); t.ticks = String(ticks)`, `script-builder.js:1983-1987`); `__propertyNamed(component, name)` (exactly one property with that display name, else `precondition: no property ...` or `precondition: ambiguous_property ...`; Opacity has two "Blend Mode"); `__readKeys(prop)` gives `{is_time_varying, keys: [{ticks, value}], value}` with `isTimeVarying` (`SRV/tools/keyframes.js:320`), `getKeys` (`:329`), `getValueAtKey` (`:335`), and `getValue()` when it is not time-varying; a colour value is `precondition: keyframed colour`; `__clipFacts(clip)` gives `{in_ticks, out_ticks, start_ticks, end_ticks, speed, reversed}` (speed and reversed as `script-builder.js:1935-1937` reads them).
- `keys_get.jsx`: first line `// recipe: keys_get`. ARGS `{sequence_id, target, effect, property, property_index}`. Finds the clip with `__clip`, the effect with `__effect`, the property with `__property` (index given) or `__propertyNamed`. Reply `{sequence, clip: {...facts, node_id, name, track_type, track, clip}, effect: {name, effect_index}, property: {name, index}, read: <__readKeys>}`.
- `get_keys` payload: `{"sequence", "clip": {"node_id", "name", "track_type", "track", "clip", "in_ticks", "out_ticks", "speed"}, "effect", "property", "is_time_varying", "value" (static value or None), "keys": [{"seconds", "media_ticks" (string), "value"}], "warnings"}`. `seconds` = (media - in) / 254016000000, 6 places; `None` when speed is not 1 or the clip is reversed (`SRV/tools/keyframes.js:327-337`).
- Command: `live keys get --sequence ID (--node-id ID | --track N --clip N) [--track-type video|audio] [--effect NAME] [--property NAME] [--property-index N]`, defaults Motion and Rotation. A read, `STOPPED_LOOK`. Human: `Motion.Rotation of 000f4276 (S02 C2246): 4 keys` then `  1.247 s  316757952000  1.01` per key, or `static 0.67`.
- `tests/data/level_v10.json`: a copy of `level_v10.json` from the film session's plan folder (path on line 3 of `.superpowers/sdd/milestone-2-notes-76.md`), unchanged. It holds 24 entries: 11 keyed with 60 keys in all, 13 static (S10 has spread 0.73, S07 0.85). Take counts from the file.
- `KeysModel` (fakes.py): clips by node ID with `in_ticks`, `out_ticks`, `start_ticks`, `end_ticks`, `speed`, Motion Scale and Rotation (`keys` as `[(ticks, value)]` or a static value); answers `keys_get` (and in Task 4 `keys_check`, `keys_write`) by the ARGS, with the same reply shapes and "precondition:" texts.

- [ ] **Step 1: Write the failing tests.**

```python
def test_key_ticks_moves_the_end_key():          # length 836972136000 (79 frames), frame 10594584000: key 3.295 ->
                                                 # ticks 826377552000, moved_from 3.295; key 1.247 -> 316757952000, not moved
def test_key_ticks_half_frame_line():            # 3.2742 moved, 3.2741 not moved (same length and frame)
def test_key_ticks_clash_after_the_move():       # 1.01 and 1.02 -> key_clash (both frame 24); 3.28 and 3.295 -> key_clash (both moved to 78)
def test_key_ticks_negative():                   # -0.001 -> negative_key
def test_level_v10_modes_from_the_file():        # 11 "keys" (60 keys in all), 13 "static", 0 "none"
def test_level_v10_rule_matches_the_file():      # static_spread 0.75: the same 13 / 11 split; value within 0.01 of static_value for
                                                 # all 24 (7 differ by 0.01: the planner takes the midpoint of unrounded values)
def test_static_spread_override():               # static_spread 0.9: S07 (spread 0.85) becomes static, value -3.32 from the rule; source "rule"
def test_no_keys_and_not_static_is_none():       # {"keys": [], "static": false} -> mode "none"
def test_static_without_value_is_bad_plan():     # {"static": true, "static_value": null} -> bad_plan
def test_keys_get_shapes_the_reply(...):         # KeysModel clip in 0, 3 keys -> seconds from media ticks, media_ticks strings
def test_keys_get_retimed_clip_has_no_seconds(...):   # speed 2 -> seconds None, media_ticks kept
def test_keys_get_static_property(...):          # is_time_varying False, value 0.67, keys []
def test_keys_get_needs_a_sequence(...):         # no --sequence -> click usage error, nothing sent
def test_keys_get_is_a_read(...):                # own film open works; every send has write False
def test_keys_get_script_in_node(node):          # run_in_node with a stub Time, a prop with getKeys/getValueAtKey; two "Blend Mode" -> ambiguous_property
def test_keys_scripts_are_es3():                 # the denylist of test_jsx.py on keys.jsx and keys_get.jsx
def test_test_data_has_no_drive_path():          # no file in tests/data holds "/Volumes/"
```

- [ ] **Step 2: Run and see them fail.** Test command with `cli_anything/premiere/tests/test_recipes_keys.py -q`. Expected: FAIL (no module `keys`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the unit suite.** Expected: all pass.
- [ ] **Step 5: Controller commits:** `feat(premiere): live keys get and the key math`.

### Task 3: Live measures M1 to M6 (the controller runs them)

No product code. A subagent may write the measure scripts below into the workspace `.superpowers/sdd/2026-11-19-premiere-cli-plan-3-milestone-2/measures/` and runs nothing. The controller runs every command, with Urko's word, after telling sessions 76 and 78.

**Files (workspace, not in git):** `measures/m1_m4.jsx`, `m5_write.jsx` (the draft of `keys_write.jsx`), `m5_map.py`, `m5_read.py`, `m5_write.py`, `m5_compare.py`, `m6_interp.jsx`; results in `measures.md`.

Each `.jsx` carries its own copies of `__sequenceById` and `__clip` from `find.jsx` (a `live run` script gets the helpers only). `m5_write.jsx` takes `{sequence_id, node_id, in_ticks, out_ticks, mode: "keys" | "static", keys: [{ticks (media, string), value}], value}`, refuses with `precondition:` when the clip's in or out ticks differ, writes Motion Rotation (index 4) by the route of decision 1, and reads every key back. The Python drivers import `cli_anything.premiere.core.recipes.keys` (Task 2) for `key_ticks` and `entry_mode`, call the CLI with `subprocess.run([...], timeout=120)`, and write JSON files into the workspace.

- [ ] **Step 1: Write the scripts** (subagent allowed).
- [ ] **Step 2: M1 to M4 on the fixture.** Open only `tests/fixtures_local/fixture27 Claude.prproj`. Duplicate `Fixture seq` and rename the copy `M2 measures`, as the `work_copy` fixture of `tests/test_live_e2e.py` does. For each step run `cli-anything-premiere live run measures/m1_m4.jsx --write --args '{"step": "<s>", "sequence_id": "<id>", "track": 0, "clip": <c>}' --project "<fixture path>" --json` and keep the reply:
  - `m1` (clip 1, Rotation without keys): `isTimeVarying()`, `getKeys()` length, then `setTimeVarying(true)`, `getKeys()` length and ticks.
  - `m2` (clip 1): `addKey` twice at the same media time with `setValueAtKey` 1.0 then 2.0: key count and value.
  - `m3a` (clip 1, keys present): `removeKey` for each key: `isTimeVarying()` and `getValue()` after. `m3b` (clip 1, keys present again): `setTimeVarying(false)`: `getValue()`, then `setTimeVarying(true)` and `getKeys()` length.
  - `m4` (clip 1): keys -2.6 and -0.07, `getValueAtKey` of each; then a static `setValue(-2.6, true)` and `getValue()`.
  - `route` (clip 0, which holds Rotation keys): `m5_write.jsx` with three keys (0 s 1.26, 1.247 s -0.07, end key moved), then `live keys get`: exact ticks and values?
  Then delete `M2 measures` by ID and close the fixture without saving.
- [ ] **Step 3: Write the M1 to M4 answers and the route into `measures.md`.** Decision table for the body of `keys_write.jsx` (Task 4 reads it):
  - M3b clears every key and M1 adds none: the route of decision 1 as drafted.
  - M3b does not clear: before `setTimeVarying(false)`, remove each key of `getKeys()` with `removeKey` (`SRV/tools/keyframes.js:582-585`).
  - M1 adds a key: after the adds, remove every key whose ticks are not planned. When such a key falls on a planned time, M2 must show "update"; else stop and report to Urko (not planned).
  - The `route` step must read back exactly. If it does not, fix `m5_write.jsx` before Step 4.
- [ ] **Step 4: M5 on a copy of v10.** Copy the v10 project file (design section 10 names it; it sits in the parent of the plan folder of notes line 3) with `ditto` into the copy folder of design section 10, with "M2 copy Claude" in the new name. Open only the copy; `live state` must show one project, the copy. Then:
  1. `cli-anything-premiere live clip list --sequence <v10 sequence id> --track-type video --track 0 --json > measures/m5_clips.json`; `python3 measures/m5_map.py` writes `measures/level_v10_copy.json`: each entry's `node` is the copy's node ID of the clip whose in and out ticks match `src_in` and `src_out` within half a frame. 23 of 24 must map, else stop. S25 is the known exception: it was trimmed after leveling (out 8.300959 to 7.382375 s; one key now lies past its visible end), so it maps by its source item and in ticks only. S25 is not written in step 3; its v10 keys inside the visible clip are read for the time-base check only. (The copy also holds Gyroflow on S03, S06, S16, S17; it does not touch keys.)
  2. `python3 measures/m5_read.py before` (one `live keys get --json` per entry) -> `m5_before.json`.
  3. `python3 measures/m5_write.py` -> one `live run measures/m5_write.jsx --write --project "<copy path>"` per entry except S25; mode and ticks from `entry_mode` and `key_ticks`.
  4. `python3 measures/m5_read.py after` -> `m5_after.json`; `python3 measures/m5_compare.py` prints both tests of design section 10 M5 (against the plan: every key at the exact media ticks, value within 0.0001, static value within 0.0001; against v10: every key that v10 and the plan share, matched by frame, at the same ticks and value within 0.0001; v10's last keys in the same frame; static values within 0.01; every count difference listed). v10 lacks exactly 6 planned keys, one on each of S03 2.407, S04 0.746, S05 0.62, S07 1.488, S08 0.204, S17 2.12 (the plan holds 60 keys on the 11 keyed shots; v10 got 54 `add_keyframe` calls; the regenerated file changed no key). Any other count difference fails the check.
  **This decides fallback B:** a failed test against the plan, or a shared key at another time, means fallback B (Task 4, Step 6).
- [ ] **Step 5: M6 on the same copy (S01, V1 clip 0, interpreted to 23.976).**
  1. Detection: `live run measures/m6_interp.jsx --args '{...}'` on the items of S01 and S02: every key of `getFootageInterpretation()` (a for-in loop) and the lines of `getProjectMetadata()` that hold "Rate" or "Timebase". Record which value tells the interpreted item apart, or "none found".
  2. Own script: `m5_write.jsx` mode keys on S01 with 0.0 at 0 s and 10.0 at 2.0 s; `live keys get` -> media ticks A.
  3. MCP: `live app activate --sequence <id>`; `m5_write.jsx` mode static 0; `mcp call add_keyframe` twice (node S01, Motion, Rotation, the same times and values, with `expected_project_path`); `live keys get` -> media ticks B; `live app activate` back.
  4. `live frame export --sequence <id> --time <S01 start + 0.5> --time <+1.0> --time <+2.0> --out-dir measures/m6`. The controller looks at the three frames: about 2.5, 5 and 10 degrees.
  Proven when A equals B and the frames match. Write `SLOW_MOTION_PROVEN = True | False` and the detection read into `measures.md`.
- [ ] **Step 6: Close the copy without saving.** `doctor`, `recovery show` (none), proxies as before. Tell sessions 76 and 78. Report to Urko: M1 to M6 in plain words, the route, fallback B or not, slow motion proven or not.

### Task 4: `live keys set`

**Files:**
- Create: `core/scripts/keys_check.jsx`, `core/scripts/keys_write.jsx`
- Modify: `core/recipes/keys.py`, `live_recipes.py`, `tests/fakes.py` (`KeysModel` answers both scripts), `tests/test_recipes_keys.py`

**Interfaces:**
- Consumes: `results.*` (Task 1), `key_ticks`, `entry_mode` (Task 2), `effects.resolve_entries`, `effects.write_entry`, `WriteSession`, `stops.not_sent`, `stops.ends_writes`, `measures.md` of Task 3.
- Produces:

```python
SLOW_MOTION_PROVEN = <True | False, from measures.md, M6>
MOTION, SCALE, ROTATION = "Motion", (1, "Scale"), (4, "Rotation")
LEVEL_KEYS = ("node", "src_in", "src_out", "name", "scale", "max_deg", "clamped", "keys", "spread_deg", "static",
              "static_value")      # the keys this command reads; others pass and are named in warnings
def check_level_plan(plan: object) -> list[dict]       # CliError("bad_plan") with "entry <n>: ..." and "Nothing was sent."
def load_level_plan(path) -> list[dict]
def set_keys(client, plan: list[dict], *, sequence_id: str, sequence_name: str | None = None, scale: bool = True,
             static_spread: float | None = None, skip_refused: bool = False, dry_run: bool = False, project=None,
             any_project=False, allow_untested=False, hard_limit_s=DEFAULT_HARD_LIMIT_S) -> dict
```

- Plan check (nothing sent): a JSON array, at least one entry; `node` a non-empty string; `src_in`, `src_out` finite with `src_out > src_in`; `keys` a list of `{time_seconds, value}` finite numbers; `scale` a finite number above 0 (or `null`, which needs `--no-scale` or mode "none"); `static`, `static_value` as `entry_mode` reads them.
- `keys_check.jsx` (`// recipe: keys_check`, a read, never throws for one entry): ARGS `{entries: [{node_id}]}`. Per entry `{found, clip: <__clipFacts> plus node_id and name, interpreted: <the M6 read, or null>, motion: {effect_index, scale: {index, name, value, keyframed}, rotation: {index, name, keyframed, keys}}}`, plus `frame_ticks` (`seq.timebase`, `SRV/tools/sequence.js:485-486`).
- Checks per entry, in this order, each a refusal with this `reason_code`: `no_clip` (not in `--sequence`); `clip_changed` (in or out ticks differ from `round(src_in * TPS)` or `round(src_out * TPS)` by more than half a frame); `retimed` (speed not 1, or reversed); `slow_motion_unproven` (interpreted and not `SLOW_MOTION_PROVEN`; when proven, the entry gets a warning that it is interpreted); `motion_layout` (no Motion, or index 1 not "Scale", or index 4 not "Rotation"); `keyed` (Scale has keys, unless `--no-scale`); `negative_key`, `key_clash` (from `key_ticks`). Mode "none" is `skipped` nothing to do, reason "level, no change", and no read of Scale or Rotation is needed.
- `keys_write.jsx` (`// recipe: keys_write`, one `session.write` per clip): ARGS `{node_id, expect_clip: {in_ticks, out_ticks}, mode, keys: [{ticks, value}], value}`. Before its first host write: `__expect`, `__clip` by node ID, in and out ticks equal to `expect_clip` as strings, speed 1 and not reversed, Motion and Rotation at index 4 by name; any miss is `precondition:`. Then the body that passed in Task 3 (`measures.md`, the decision table); mode "static" ends with `setTimeVarying(false)` and `setValue(value, true)`. From the first host write on nothing throws: each write sits in its own try, errors go to `set_errors`, and the script always reads back. Reply `{before: {is_time_varying, count}, set_errors: [str], after: <__readKeys>}`.
- Order of work, in one `WriteSession`: target; `keys_check` for all entries; one `effects.resolve_entries` for the Scale entries (`{"node_id", "effect": "Motion", "index": 1, "name": "Scale", "value": scale}`); all checks; refusals stop the run (`results.refused_error`) unless `--skip-refused`. `--dry-run` stops here with the plan: per entry mode, keys `{seconds, ticks, media_ticks, value, moved_from}`, static value, Scale now and new, refusals. Then per entry, in plan order: Scale with `effects.write_entry` (unless `--no-scale` or mode "none"), then Rotation with `keys_write.jsx`. Between entries `session.breathe()`; when it returns True, `keys_check` runs again for the next entry, and a refusal then makes it `skipped` refused, and stops the run unless `--skip-refused`.
- `done`: Scale stored, and for mode keys: `after.is_time_varying` true, the same number of keys, every tick string equal, every value within `KEY_TOLERANCE`; for mode static: not time-varying and the value within `KEY_TOLERANCE`. Else `failed` with `now` = the read-back (decision 2: the run goes on). Send errors: `precondition` belongs to this entry only (`failed`, known, the run goes on). Any other code of `stops.NOT_SENT_CODES` (`not_applied`, `recovery_required`, `bridge_down`, ...) ends the run: this entry and the rest stay `not_run` (a dialog in Premiere must not make every clip wait a full timeout). Any other send error: `failed` with `known=False`, and the run ends; the rest `not_run`. A Scale written before a failed Rotation write stays written and shows in `now`.
- Record extras: `mode`, `moved: [{"from_seconds", "to_seconds"}]`, `interpreted` (bool or None); `now = {"scale", "rotation": {"is_time_varying", "keys": [{"seconds", "media_ticks", "value"}], "value"}}`.
- Command: `live keys set --sequence ID [--sequence-name NAME] --from LEVEL.json [--no-scale] [--static-spread DEG] [--skip-refused] [--dry-run] [--project PATH] [--any-project]` plus `live_options`, `STOPPED_RUN`. Help says: keys replace the old ones; the last key moves to one frame before the clip end; interpolation is the default of `addKey`; the project is not saved.

- [ ] **Step 1: Write the failing tests** (KeysModel; level data from `tests/data/level_v10.json` with its nodes put in the model):

```python
def test_level_v10_dry_run(...):                 # 24 entries: 11 keys, 13 static; S02 end key 3.295 moved to 3.25325 s; nothing written
def test_level_v10_run(...):                     # all 24 done, exit 0, stored True; 60 keys written; static S11 -2.15
def test_rerun_is_safe(...):                     # the same plan again: 24 done, the same keys, exit 0
def test_scale_before_rotation(...):             # order of writes per clip: effect_write (Scale) then keys_write
def test_no_scale(...):                          # --no-scale: no effect_write sent
def test_clip_changed_is_refused(...):           # model in ticks + 1 frame -> refused clip_changed, nothing written, code "refused"
def test_skip_refused_runs_the_rest(...):        # same with --skip-refused -> entry skipped refused, others done, exit 1 "not_done"
def test_node_in_another_sequence(...):          # node exists only in sequence s2 -> no_clip
def test_retimed_and_slow_motion(...):           # speed 2 -> retimed; interpreted True with SLOW_MOTION_PROVEN False -> slow_motion_unproven
def test_scale_keyed_is_refused(...):            # Scale keyframed -> keyed; --no-scale lets it pass
def test_level_no_change_is_skipped(...):        # keys [] static false -> skipped nothing_to_do "level, no change", no write, exit 0
def test_in_script_refusal_after_the_check(...): # model changes in ticks between check and write -> precondition from keys_write,
                                                 # entry failed known, Rotation unchanged, the new Scale shown in now; next entry runs
def test_readback_mismatch_is_failed(...):       # model stores one key 1 tick off -> failed with now; next entry still runs (decision 2)
def test_not_applied_ends_the_run(...):          # keys_write of entry 3 gives not_applied -> entries 3 and later not_run, first_not_run 3, exit 1
def test_unknown_outcome_stops(...):             # keys_write times out (unknown_outcome) -> failed known False, rest not_run, stored None
def test_breathe_then_check_again(...):          # MAX_HOLD_S 0, model trims entry 2 during the breath -> skipped refused, run stops
def test_static_spread_override_warns(...):      # --static-spread 0.9 -> S07 static, warning names it
def test_plan_file_faults(...):                  # not JSON; an object; entry without node; key with a string time -> bad_plan, nothing sent
def test_unknown_keys_pass_with_a_warning(...):  # an entry with "note" -> runs; warnings name "note"
def test_own_film_is_refused(...):               # project_guard; only the state script seen
def test_keys_write_in_node(node):               # stub prop built from the measured M1 to M3 answers: keys replaced, a throwing addKey -> set_errors, read back still done
def test_keys_check_in_node(node):               # missing node -> found false, no throw
```

- [ ] **Step 2: Run and see them fail.** Expected: FAIL (`set_keys` missing).
- [ ] **Step 3: Implement.** Keep the Rotation write in one function, `_write_rotation(session, entry, check) -> dict`, so fallback B can replace it.
- [ ] **Step 4: Run the unit suite.** Expected: all pass.
- [ ] **Step 5: Controller commits:** `feat(premiere): live keys set from a level plan`.
- [ ] **Step 6: Fallback B, a decision point only.** If `measures.md` says M5 failed, the controller stops after Step 4 and writes a follow-up task (4b): `_write_rotation` replaced by `add_keyframe` calls (one per key, after `remove_keyframe_range`), run inside `ActiveRun` (Task 6, which 4b then follows), with the same checks, read-back, command line and output. Task 4b is not planned here.

### Task 5: Live measures M7 to M9 (the controller runs them)

No product code. Same rules as Task 3. On the fixture only, on a duplicate `M2 measures` of `Fixture seq` made active with `live app activate`.

- [ ] **Step 1: M7.** `mcp call set_item_in_out` then `mcp call overwrite_clip` with a fixture item onto V2 at a start after the last clip, both with `expected_project_path`. Right after: `live clip list --sequence <id> --track-type video --track 1 --json`. Does the new clip have a node ID, unique in the sequence, that `live effect get --node-id` finds? Twice, to see it is not lazy.
- [ ] **Step 2: M8.** `overwrite_clip` of the same item with `track_index` 2 (a spare video track; add it with `mcp call add_track` if needed) and `audio_track_index` 1. Read V3 and A2 with `live clip list --detail` (start, in, linked). `mcp call remove_from_timeline` with the picture clip's node ID, `include_linked: false`, `ripple: false`. Read again: the sound clip kept, its start and in point unchanged, no picture.
- [ ] **Step 3: M9.** Import a still (a PNG frame exported by `live frame export`) with `mcp call import_media`. With `live run` read on the still item and a video item: `getFootageInterpretation().frameRate`, `isSequence()`, the media path, `getOutPoint(1).seconds` and `getOutPoint(4).seconds`. Record which read tells the still apart (the media path extension is the fallback). Place the still with `set_item_in_out` and `overwrite_clip`, read its length (4.96 s expected), `mcp call set_clip_duration` to 6.0 s, read again.
- [ ] **Step 4: Results into `measures.md`.** For Task 6: the still read, whether M7 holds (if not: the read between the MCP sessions retries for up to 5 s), M8 as measured. Delete `M2 measures`, close without saving, `doctor`, `recovery show`, tell sessions 76 and 78, report to Urko.

### Task 6: `live place`

**Files:**
- Create: `core/recipes/active.py`, `core/recipes/place.py`, `core/scripts/place_read.jsx`, `tests/test_active.py`, `tests/test_recipes_place.py`, `tests/data/shots_v10.json`, `tests/data/place_v10.json`
- Modify: `live_recipes.py`, `tests/fakes.py` (`TimelineModel`, new fake MCP tools)

**Interfaces:**
- Consumes: `results.*`, `WriteSession`, `guarded_state`, `run_script`, `McpSession.call(name, arguments, timeout_s) -> {"is_error", "structured", "text"}`, `live_cli.make_session`, and from `frames.py` only the helpers whose texts name no export: `_new_switches`, `_switch`, `_activate`, `_put_back`, `_left_switched`, `_fault`, `_read_app`, `_id`. `_confirm_active`, `_restore` and `_check_after` say "export" and "frames" in their messages, so `ActiveRun` has its own confirm, put-back session and read-after with the same logic and its own words ("during the run", "Nothing was placed or applied"). `frames.py` itself is not changed.
- Produces:

```python
# core/recipes/active.py
class ActiveRun:
    """The two-session pattern of frames.py for MCP tools that act on the active sequence. Proxies are never switched."""
    def __init__(self, client, make_session, *, sequence_id: str, sequence_name: str | None = None, project=None,
                 any_project=False, allow_untested=False, hard_limit_s=DEFAULT_HARD_LIMIT_S,
                 call_timeout_s=DEFAULT_HARD_LIMIT_S)
    sequence: dict; project_path: str | None; warnings: list[str]; stopped: bool; left_switched: dict | None
    def prepare(self, check: Callable[[WriteSession], T], *, switch: bool) -> T
        # Session A: target, check(session) (reads and refusals, before any switch); with switch: _switch(_activate),
        # then a fresh guarded state read must show the target active. A failure after the switch: _put_back, then
        # raise (a plain Exception as recipe_error).
    def mcp(self, steps: Callable[[McpSession, dict], None]) -> CliError | None
        # No lease. One session; steps(mcp, guard_args), guard_args = {"expected_project_path",
        # "expected_cep_target": {"backend": "cep", "sequence_id"}}. Returns the first error (a stop: interrupted,
        # stopped True; a plain Exception: recipe_error); never raises.
    def look(self, body: str, args: dict, *, include=("find",)) -> tuple[object, bool]
        # A read pass: lease, guarded_state(write=False), run_script with "expect" added. Works with a recovery marker.
        # Returns (the reply, whether the state read shows the target as the active sequence).
    def finish(self) -> CliError | None
        # Session B: put back the active sequence (_put_back) unless someone else changed it during the run, as
        # frames._restore and _check_after decide, with this module's words; with a marker nothing is written.
        # Fills left_switched (frames._left_switched). Returns a fault (recipe_error) or None.

# core/recipes/place.py
EPS = 0.001
SEQ_FPS = "24000/1001"
SHOT_KEYS = ("name", "id", "item", "item_id", "src_in", "src_in_f", "cam_in_f", "cam_fps", "interpreted", "item_fps",
             "frames", "end_f", "end", "src_out", "start_f", "start", "track", "video_track", "audio_track",
             "audio_track_index", "media_length", "note", "audio_only")
def rate(value) -> float
def floor_frame(seconds: float, fps: float) -> int
def resolve_shots(plan: dict) -> tuple[float, list[dict]]   # (F, rows); CliError("bad_plan") naming the shot
def calls_for(row: dict, F: float) -> list[dict]             # [{"tool": "set_item_in_out", "args"}, {"tool": "overwrite_clip", "args"}]
def load_shots(path) -> dict
def place_shots(client, make_session, plan: dict, *, sequence_id: str, sequence_name=None, overwrite=False,
                skip_refused=False, dry_run=False, project=None, any_project=False, allow_untested=False,
                hard_limit_s=DEFAULT_HARD_LIMIT_S) -> dict
```

- Shot math, a port of the film planner's `rate`, `floor_frame`, `resolve` and `calls_for` (`~/.claude/skills/cinematic-story-edit/scripts/place_plan.py`, read it; no import of skill files). The body, in this order:

```python
def rate(v):                                   # "24000/1001", "23.976", 23.976 -> float; NTSC decimals snap
    v = float(v) if isinstance(v, (int, float)) else float(Fraction(str(v)))
    for exact in (24000 / 1001, 30000 / 1001, 60000 / 1001, 120000 / 1001):
        if abs(v - exact) < 0.005: return exact
    return v
def floor_frame(seconds, fps): return int(math.floor(seconds * fps + 1e-6))
# per shot k, with cursors = {} over the whole plan (track -> (end frame, end seconds)):
#   interp_fps = F if interpreted is True else rate(interpreted) when interpreted not in (None, False), else None
#   cam_fps = rate(cam_fps) or None; item_fps = rate(item_fps) if given else (interp_fps or cam_fps or F)
#   src_req = cam_in_f / (interp_fps or cam_fps)  |  src_in_f / item_fps  |  float(src_in)   (first that is given)
#   in_f = floor_frame(src_req + EPS, item_fps); src_in = round(in_f / item_fps, 6)   # the row value calls_for reads
#   start: start_f -> (start_f, start_f / F); start -> f = int(round(start * F)), (f, f / F); else cursors.get(track, (0, 0.0))
#   length: frames | end_f - start_f | int(round(end * F)) - start_f
#           | src_out: end_t = start_t + (src_out - src_req); frames = int(round(end_t * F)) - start_f
#   frames <= 0 -> bad_plan; end_f = start_f + frames
#   cursors[track] = (end_f, end_t) when the start was not given, or when end_f >= the cursor's end frame
# calls_for (args without expected_project_path):
#   {"item_id": item, "in_seconds": round(src_in + EPS, 6), "out_seconds": round(src_in + frames / F + EPS, 6)}
#   {"item_id": item, "start_seconds": round(start_f / F + EPS, 6), "track_index": track, "audio_track_index": audio}
```

  `src_in` in `calls_for` is the row's value, `round(in_f / item_fps, 6)`, so it is rounded twice, as in the planner (checked on the v10 data: this gives 50 of 50 calls; the unrounded value gives 45, with `out_seconds` 1e-6 off on S09, S16 and S23). `track` and `audio_track` must be whole numbers here (`auto_empty` is `bad_plan`).
- `tests/data/shots_v10.json` and `place_v10.json`: copies from the film session's plan folder (notes line 3), made once by the implementer with a short Python script: shot names become "S01" to "S25" in both files (in order), and every `note` becomes ""; `place_v10.json` keeps only `fps`, `shots` and `calls`, with every `expected_project_path` set to `/p/X Claude.prproj` (the project of `RECIPE_STATE`). After the copy: `grep -rl "/Volumes/" tests/data` finds nothing. The v10 shots hold no `audio_only` and no still, so those cases get synthetic plans.
- `place_read.jsx` (`// recipe: place_read`, a read): ARGS `{items: [id or name], spans: [{entry, track, audio_track, start_ticks, end_ticks}]}`. Reply: `sequence: {id, name, frame_ticks, video_tracks, audio_tracks}`; per item `{found, node_id, name, matches (count of items with that name), has_video, has_audio (item.getOutPoint(1 or 2) as SRV/tools/advanced.js:1049-1055), still (the M9 read)}`; per span the clips on video track `track` and audio track `audio_track` that overlap it with half a frame of room: `{node_id, item, start_ticks, end_ticks, in_ticks, out_ticks}`; and `first_v1: {node_id, start_ticks, head_transition: bool}` (the transition read of `clip_list.jsx`). Project items are walked as `__findProjectItem` walks them (`SRV/bridge/script-builder.js:951-967`), but a name that matches two items is reported, not taken.
- Session A (`ActiveRun.prepare`, `switch=not dry_run`): plan `fps` must equal `254016000000 / frame_ticks` within 0.001 (`fps_mismatch`, whole plan). Per shot, as a record: `no_item`, `ambiguous_item`, `no_track`, `plan_overlap` (two shots of the plan overlap on one video or audio track), `placed_differently`; nothing to do: `already placed` (a clip of the item on the track at the start within half a frame, in and out within half an item frame; for a sound-only shot: the sound clip there and no picture clip of the item on `track`); `span_not_empty` (a clip the plan did not name), or with `--overwrite` a warning that lists the clips that will be cut. A shot on V1 over the first V1 clip with a head transition gets a warning (overwriting it deletes the transition). A half-done shot (sound-only with its picture left, a still placed but short) is planned with the missing step only.
- MCP session 1: per shot to place, `set_item_in_out` then `overwrite_clip`, each with the guard args; the item is the resolved node ID. A step failed when `is_error`, or the reply's `data.verified` is not true. The marks the server applied (`data.appliedInSeconds` and `data.appliedOutSeconds` of `set_item_in_out`, `SRV/tools/track-targeting.js:763-772`) go into the record's `marks` and the payload's `marks`. Stop at the first failure.
- Between the sessions: `ActiveRun.look(place_read)` finds the new clips (item, track, start), and its state read must still show the target as the active sequence (the lease was free; a server guard refusal would cost a recovery, Plan 2 `measures.md` section 5). When it moved, MCP session 2 does not run and its shots are `failed` with reason "the active sequence changed". Otherwise their node IDs go to MCP session 2: `remove_from_timeline {node_id: picture clip, include_linked: false, ripple: false}` for sound-only shots, and `set_clip_duration {node_id, duration_seconds: frames / F}` for stills longer than their placed length (decision 5 says when this session runs after a failure).
- Session B: `ActiveRun.finish()`, then `ActiveRun.look(place_read)` (works with a marker). Per shot `done` when the picture clip (not for sound-only) starts at `start_f` to the frame and its in and out are `src_in` and `src_in + frames / F` to the item frame; the sound clip on `audio_track` (when the item has sound) starts there too; a sound-only shot has no picture clip on `track`; a still runs `frames / F`. `now = {"video": {node_id, start_seconds, in_seconds, out_seconds} | None, "audio": {...} | None}`.
- Payload: `results.summary(...)` plus `sequence`, `marks`, `left_switched`, and with `--dry-run` `planned`: per shot the exact calls with `expected_project_path` and `expected_cep_target`, and what would happen. Errors: `refused`, `not_done`, `not_restored` (every shot done, the active sequence not put back), stop codes.
- Fakes: `TimelineModel` (fakes.py) keeps the sequence, its tracks and clips, the project items (node ID, name, streams, still, length) and the marks in a JSON file in the test's tmp folder. The panel handler loads it for each script and answers `place_read` (and `clip_list` for Task 7). The fake MCP server gets the tools `set_item_in_out`, `overwrite_clip`, `remove_from_timeline`, `set_clip_duration`, `apply_effect`, which load, change and save the same file (its path as the server's second argument), log each call with `lease_held` as `export_frame` does, and return replies shaped like the server's (`SRV/tools/advanced.js:1124-1134`, `track-targeting.js:763-772`, `effects.js:49-62`). New clips get node IDs `n0001`, `n0002`, .... Steering words in `item_id` or `effect_name`: `FAIL` (tool error, nothing changed), `MARKER` (writes the recovery marker, tool error), `UNVERIFIED` (picture placed, sound missing, `committed_unverified`), `QEADD` (tool error, but the effect was added).

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_recipes_place.py
def test_golden_calls():                          # resolve_shots + calls_for on tests/data/shots_v10.json == the 50 calls of
                                                  # place_v10.json: tool names in order, and item_id, in_seconds, out_seconds,
                                                  # start_seconds, track_index, audio_track_index equal (expected_project_path aside)
def test_golden_dry_run(...):                     # live place --dry-run on the same data (items in the TimelineModel): planned calls
                                                  # equal place_v10.json, each with expected_project_path "/p/X Claude.prproj" and
                                                  # expected_cep_target {"backend": "cep", "sequence_id": "s1"}; no MCP call made
def test_planner_paths():                         # src_in, src_out chained, start and end, interpreted 120000/1001 on 23.976:
                                                  # numbers equal to place_plan.py for three small plans written into the test
def test_place_runs_and_checks(...):              # 3 shots: 6 MCP calls, all done, node IDs in now, exit 0, active sequence put back
def test_no_mcp_call_under_the_lease(...):        # lease_held False for every recorded call
def test_rerun_skips_placed_shots(...):           # the same plan again: 3 skipped "already placed", exit 0, no MCP call
def test_same_item_twice(...):                    # S22/S23 shape: stop after the first; re-run places the second only
def test_failure_in_the_middle(...):              # FAIL at shot 13 of 25: 0-12 done, 13 failed, 14-24 not_run, first_not_run 14, exit 1, put back
def test_marker_after_failure(...):               # MARKER: no put-back write, left_switched names the sequence, read pass still reports
def test_span_not_empty(...):                     # a foreign clip in the span -> refused, nothing sent; --overwrite -> runs, warning lists it
def test_placed_differently(...):                 # same item, same start, other in -> refused placed_differently
def test_plan_overlap_and_fps(...):               # two shots overlap on V1 -> plan_overlap; fps 25 on a 23.976 sequence -> fps_mismatch
def test_ambiguous_item_name(...):                # two items named "C0001.MP4" -> ambiguous_item; one -> its node ID is sent
def test_sound_only_shot(...):                    # audio_only: picture on V3 then remove_from_timeline (include_linked false); done; sound kept
def test_sound_only_half_done(...):               # picture left from an earlier run -> only the removal is sent
def test_still_is_extended(...):                  # still 4.96 s, planned 6.0 s -> set_clip_duration 6.0; done
def test_unverified_overwrite_is_failed(...):     # UNVERIFIED -> failed, run stops
def test_head_transition_warning(...):            # first V1 clip with a head transition under shot 0 -> warning
def test_unknown_plan_keys_pass(...):             # a shot with "lens": "35mm" -> warning names it
def test_plan_file_faults(...):                   # no item; no source in; frames 0; audio_track "auto_empty" -> bad_plan, nothing sent
def test_place_read_in_node(node): ...
# tests/test_active.py
def test_prepare_switches_and_confirms(...)       # another sequence active -> activate write, then a state read
def test_fault_after_the_switch_is_put_back(...)  # check() passes, a RuntimeError in the confirm step -> recipe_error, put back
def test_look_works_with_a_marker(...)            # marker present -> look reads, no write
def test_finish_with_a_marker_leaves_switched(...)# left_switched {"proxies": None, "active_sequence": {...}} and the put-back command line
def test_messages_name_no_export(...)             # every warning and error text of ActiveRun in these tests: no "export", "frame"
def test_look_reports_a_moved_sequence(...)       # another sequence active at the look -> (reply, False); place skips MCP session 2
```

- [ ] **Step 2: Run and see them fail.** Expected: FAIL (no module `place`).
- [ ] **Step 3: Implement.** Command: `live place --sequence ID [--sequence-name NAME] --plan SHOTS.json [--overwrite] [--skip-refused] [--dry-run] [--project PATH] [--any-project]` plus `live_options`, `STOPPED_RUN`. Help says: overwrite only, never insert; the item marks stay as set and are listed; the sequence is active for every session while it runs.
- [ ] **Step 4: Run the unit suite.** Expected: all pass.
- [ ] **Step 5: Controller commits:** `feat(premiere): live place from a placement plan`.

### Task 7: `live effect apply`

**Files:**
- Create: `core/recipes/apply.py`, `tests/test_recipes_apply.py`
- Modify: `live_recipes.py`, `tests/fakes.py` (`apply_effect` already in Task 6; `clip_list` answers from `TimelineModel`)

**Interfaces:**
- Consumes: `ActiveRun`, `results.*`, `clip_list.jsx` (with `track_type` "video", `track`, `limit` 400, through `ActiveRun.look` and `session.read`), `clips.INTRINSIC`, `clips._render_order`.
- Produces:

```python
ORDER_RULE = ("ProcAmp", "Lumetri Color")       # a request with Lumetri Color before ProcAmp is refused without --any-order
GYROFLOW_NOTE = "Gyroflow gets its defaults only. Its sliders cannot be set by a script."
def apply_effects(client, make_session, *, sequence_id: str, sequence_name=None, track: int, clips: list[int] | None,
                  effects: list[str], any_order=False, allow_second=False, skip_refused=False, dry_run=False,
                  project=None, any_project=False, allow_untested=False, hard_limit_s=DEFAULT_HARD_LIMIT_S) -> dict
```

- Before any contact: at least one `--effect`, no name twice (`bad_args`); the order rule (`bad_order`); `--clips` or `--all`, not both.
- Session A (`prepare`): read the track (`too_many_clips` above 400); entries clip by clip in the order of `--clips` (or track order), effects in request order. Per entry: `no_clip`; `skipped` nothing to do "has effect" when the clip holds a component of that display name, unless `--allow-second`; a warning when an existing effect makes the render order differ from the request (for example an old Lumetri Color under a new ProcAmp).
- MCP session: `apply_effect {node_id, effect_name}` with the guard args, per entry. A reply with `is_error` or an outcome other than `verified` ends the session; the read in session B decides that entry (the server warns that QE may have added it anyway, `SRV/tools/effects.js:69`). The rest `not_run`.
- Session B: `finish`, then one `clip_list` read pass. An entry is `done` when the clip holds one more component of that name than before. Its `effect_index` is the first component of that name in component order (a new effect lands at index 2, before older ones: Plan 2 `measures.md` section 4). The requested effects of a clip must be the last ones of its `render_order`, in request order; else the entry gets a warning (predicted in session A) or is `failed` with `reason_code` "render_order" (not predicted). Gyroflow entries get `GYROFLOW_NOTE`.
- Command: `live effect apply --sequence ID [--sequence-name NAME] --track N (--clips 0,3,5 | --all) --effect NAME [--effect NAME ...] [--any-order] [--allow-second] [--skip-refused] [--dry-run] [--project PATH] [--any-project]`. Track N counts from 0 (V1 is 0). The output gives each new `effect_index` for `live effect set` and `live lut set`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_procamp_then_lumetri(...):            # 2 clips x 2 effects: 4 apply_effect calls in clip order, all done, effect_index 3 and 2, exit 0
def test_order_rule(...):                      # --effect "Lumetri Color" --effect ProcAmp -> bad_order, nothing sent; --any-order runs
def test_has_effect_is_skipped(...):           # clip 0 holds ProcAmp -> skipped nothing_to_do, exit 0; --allow-second applies a second one
def test_rerun_finishes_a_partial_run(...):    # clip 0 has ProcAmp only -> only Lumetri Color sent
def test_existing_effect_order_warning(...):   # clip holds Lumetri Color, request ProcAmp -> warning on that entry
def test_failed_call_checked_by_read(...):     # QEADD -> the read finds it: done with a warning; FAIL -> failed, rest not_run, exit 1
def test_all_and_too_many(...):                # --all on 3 clips -> 3 entries; 401 clips -> too_many_clips, nothing sent
def test_clips_and_all_together(...):          # usage error, nothing sent
def test_gyroflow_note(...):                   # --effect Gyroflow -> note in the entry warnings
def test_put_back_and_no_lease_in_calls(...):  # active sequence put back; lease_held False on every call
def test_dry_run(...):                         # reads only, no MCP session
def test_own_film_is_refused(...)
```

- [ ] **Step 2: Run and see them fail.** **Step 3: Implement.** **Step 4: Run the unit suite.**
- [ ] **Step 5: Controller commits:** `feat(premiere): live effect apply in render order`.

### Task 8: Live tests (the controller runs them)

Like Plan 2 Task 9. A subagent writes the tests and runs nothing live.

**Files:**
- Modify: `tests/test_live_e2e.py`, `PREMIERE.md` (section "Live checks of Plan 3")

- [ ] **Step 1: Write the live tests** (on `E2E copy` from `work_copy`; V1 clip 0 of the fixture holds Rotation keys):

```python
def test_keys_get_of_the_fixture(work_copy):            # V1 clip 0: is_time_varying True, keys with media_ticks
def test_keys_set_replaces_keys(work_copy, tmp_path):    # a level plan for V1 clip 0 (src_in/src_out from live clip list): 3 keys
                                                          # with the end key moved, done; keys get shows exactly those 3
def test_keys_set_static_and_rerun(work_copy, tmp_path): # static -1.49: done; the same plan again: done, exit 0
def test_keys_set_clip_changed(work_copy, tmp_path):     # src_in off by 0.5 s -> refused, nothing changed
def test_place_on_a_spare_track(work_copy, tmp_path):    # 2 fixture shots onto V2 after the end: done, node IDs; active sequence put back
def test_place_rerun_is_already_placed(work_copy, tmp_path)  # exit 0, no MCP call
def test_place_sound_only(work_copy, tmp_path):          # audio_only shot: sound on A2, picture removed from V3
def test_effect_apply_procamp_then_lumetri(work_copy):   # V1 clip 1: done; render_order ends "ProcAmp > Lumetri Color"; re-run: skipped, exit 0
```

  Also N11.9: raise `FRAME_TIMEOUT_S` in `test_live_e2e.py` above the 600 s that `--prove` now waits per frame, and fix its comment.
- [ ] **Step 2: The controller runs** `PREMIERE_LIVE_E2E=1 <test command> cli_anything/premiere/tests/test_live_e2e.py -q`. Expected: all pass. Rules of Task 3 (fixture only, no save, `doctor` and `recovery show` clean).
- [ ] **Step 3: Film rehearsal on a fresh copy of v10** (as in Task 3, Step 4; design section 1 success test). Make the empty sequence on the copy as session 76 did for v10: `mcp call create_sequence_from_clips` (name with "Claude", `item_ids` one normal 4K 23.976 item, as C2246; never an interpreted item such as C2245: it gives a wrong timebase), then `mcp call remove_from_timeline` on the seed video clip with `include_linked: true` (its sound goes too), then `mcp call add_track` until there are 5 video and 8 audio tracks (as v10). Then, with `--project "<copy path>"`: map `shots_v10.json` items to the copy's node IDs by item name if they differ; `live place --dry-run`, then `live place`; write a level plan with the new clips' node IDs (from the place output, by shot) and run `live keys set --dry-run`, then `live keys set`; `live effect apply --track 0 --all --effect ProcAmp --effect "Lumetri Color"`. Each re-run must exit 0 with every entry skipped or done. Record the time of each command and the counts. Close without saving.
- [ ] **Step 4: Results into `PREMIERE.md`.** Controller commits: `test(premiere): live tests of keys, place and effect apply`. Report to Urko.

### Task 9: Documents

**Files:**
- Modify: `README.md`, `skills/SKILL.md`, `tests/TEST.md`, `PREMIERE.md` ("Commands", "Not done, and why"), `premiere_cli.py` (`REPL_COMMANDS` only), `live_recipes.py` (help text of `live effect set`, N11.7)
- Test: `tests/test_live_cli.py`

Session 76 owns the reader text: its sections stay word for word. New text goes into new sections, and session 76 (the film session) reviews them before Urko signs off.

- `README.md`, section "Plan files": the three commands with one example each, the statuses, the exit rule, `--dry-run` first, `--skip-refused`, what a re-run does.
- `skills/SKILL.md`, section "Plan files on the running Premiere 27": the rules for an agent: run `--dry-run` first and read every refusal; name the sequence by ID; the plan formats are those of `level_plan.py` and `place_plan.py`, unchanged; keys replace the old keys and the last key moves; slow-motion clips are refused unless M6 proved them (say which); placing is overwrite only, the item marks stay as set; `live place` and `live effect apply` make the sequence active for every session while they run; ProcAmp before Lumetri Color; Gyroflow defaults only; nothing is saved; after `recovery_required` run `recovery show` and tell Urko. Also N11.3 and N11.4: the codes list gets the new codes (`refused`, `not_done`, `clip_changed`, `retimed`, `slow_motion_unproven`, `key_clash`, `span_not_empty`, `placed_differently`, `plan_overlap`, `fps_mismatch`, `bad_order`, `too_many_clips`, `recipe_error`) with true texts, and "Proxy display is off" is said only for `--proxies off`.
- `tests/TEST.md`: what the new fakes cover; the live results of Task 8. `REPL_COMMANDS`: one line per new command.

- [ ] **Step 1: Write the failing help tests.**

```python
def test_plan_commands_have_help(...):          # keys set, place, effect apply: help names "--dry-run", "--skip-refused", "not saved"
def test_no_em_dash_in_any_help_text(...):      # the walk of Plan 2, now with the keys group and the new commands
def test_repl_lists_the_new_commands(...):      # keys starting "live keys get", "live keys set", "live place", "live effect apply"
def test_root_help_and_status_unchanged(...):   # the pinned strings and keys of Plan 2
```

- [ ] **Step 2: Write the documents.** **Step 3: Run the full suite.** **Step 4: Send session 76 the README and SKILL sections; take its notes.**
- [ ] **Step 5: Controller commits:** `docs(premiere): plan file commands in the documents and help text`. **Step 6: Report to Urko.**

---

## Not in this plan

- Fallback B in full (Task 4, Step 6 is only the decision point).
- Key interpolation, array keys (Position, Anchor Point), audio keys, insert edits, removing clips other than the picture of a sound-only shot, Gyroflow parameter writes, saving (design sections 3 and 4).
- `live effect copy`, `live sequence duplicate`, `live track disable-clips` (recommendations 3 and 7 of the Plan 2 final review apply when they come). Recommendation 8 (600 s waits for export and import) has no new export or import recipe here.
- Removing `WriteSession.paused()` (decision 7).
- Parked minors of Plan 2's last fix wave: N11.1 (one property twice with one `expect_old`), N11.2 ("Nothing more was written" after a script refusal mid-plan), N11.5 (lut set goes on to frame B after index 6 `not_applied`), N11.6 (`slot_before` reads session 1 only), N11.8 (`expect_old` on strings and booleans not tested live), and group B of the Plan 2 triage. N11.3, N11.4, N11.7 are in Task 9; N11.9 in Task 8.

## Self-review notes

- Spec coverage: goals 1 and 2 (Tasks 2 and 4), 3 (Task 6), 4 (Task 7), 5 (Task 1); section 6 (Task 1 helper, Global Constraints, each task's refusals and re-runs); section 7 steps 1 to 8 and the static rule (Tasks 2 and 4); section 8 including sound-only shots, stills, the second MCP session and the transition warning (Task 6); section 9 (Task 7); section 10, M1 to M6 (Task 3) and M7 to M9 (Task 5); section 11 (Task 1); section 12 order kept: 1 N12.1 and helper, 2 keys get, 3 M1 to M6, 4 keys set, 5 M7 to M9, 6 place, 7 effect apply, 8 live tests, 9 documents. No task was split or merged. `ActiveRun` lands in Task 6, its first user; Task 7 reuses it, and fallback B would need it too.
- Plan 2 final review: recommendation 1 (in-script check of the clip, Task 4), 2 and 3 (Global Constraints, `ActiveRun`), 4 (decision 7), 5 (Task 1), 6 (Task 6 reads node IDs again after the MCP sessions; rule 7 dropped with saving), 7 and 8 (not in this plan).
- The key counts in the tests come from `tests/data/level_v10.json` (11 keyed shots with 60 keys, 13 static). The design's 53 is the count of v10's own `add_keyframe` calls, not of the plan file.
