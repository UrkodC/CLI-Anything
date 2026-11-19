# Premiere CLI, milestone 2: keys, placing and effects from plan files

Status: APPROVED on 2026-11-19 by the film-edit session, which Urko named as the approver ("the approval
has to come from premieremcp"), after two review rounds (its changes are in).
Date: 2026-11-19. Builds on `2026-11-18-premiere-cli-design.md` (the "main design") and its Plan 1 and Plan 2.
Route chosen by Urko (relayed by the film-edit session on 2026-11-19, to confirm in this window): A, "mixed".

## 1. Why

The film-edit session built Fall Afternoon v10 with the MCP tools and the CLI. The CLI parts had no failure.
Three loops of single MCP calls cost the most work:

- Rotation keys for horizon leveling: 53 `add_keyframe` calls, one per key.
- Placing 25 shots: 50 calls (`set_item_in_out` then `overwrite_clip` per shot).
- Applying ProcAmp, Lumetri Color and Gyroflow clip by clip.

Milestone 2 turns each loop into one CLI command that reads the session's own plan file, with a dry run first
and a result per entry that says what landed. It also fixes one fault found in real use.

Success: v11 of a film can be leveled, placed and given its effects with three commands, each with an exact
check of what Premiere now holds, and a partial run that is easy to finish.

## 2. Goals

1. `live keys set --sequence ID --from LEVEL.json`: Rotation keys and one static Scale per clip, from the
   output of the film session's `level_plan.py`, unchanged.
2. `live keys get --sequence ID (--node-id N | --track T --clip C) [--effect Motion --property Rotation]`:
   read the keys of one property (times in clip seconds, media ticks, values).
3. `live place --sequence ID --plan SHOTS.json`: place shots from the input of the film session's
   `place_plan.py`, unchanged.
4. `live effect apply --sequence ID --track T (--clips 0,3,5 | --all) --effect NAME [--effect NAME ...]`:
   add effects in the given render order.
5. Fix N12.1: `live lut set --prove` with a verdict prints no "Use --prove" warning.

## 3. Non-goals

- Position, Anchor Point or any array key. Number properties only.
- Setting key interpolation (the default of `addKey` is kept, as in v10).
- Insert edits (ripple). Placing is overwrite only, as in the film's rules.
- Removing clips, transitions, audio keys, Color Mode.
- Gyroflow parameter writes (ignored by the plugin; index 14 opens a recovery).
- Saving the project. The owner or the film session saves.

## 4. Anti-goals (a command that does these is wrong even when it "works")

- Keys written at wrong times without a word. This includes slow-motion clips.
- A shot placed over another clip that the plan did not name, without a refusal.
- The active sequence or proxy display left switched without saying so.
- A write in a project whose name has no "Claude" without `--any-project` (the existing guard).
- A result that says "done" for an entry that the read-back did not confirm.

## 5. Approaches

- A, mixed (chosen). Keys by the CLI's own ExtendScript: one round trip per clip writes all its keys and reads
  every key back; it finds the clip by sequence ID, so the sequence need not be active. Placing and effects by
  the MCP tools that had no failure in v10, wrapped like `live frame export`: make the sequence active, run the
  tool calls without the lease, put the active sequence back, then check with an own read.
- B, all MCP. Proven for keys (53 of 53), but one tool call per key stays inside, the sequence must be active
  for every key, and the read-back is per key.
- C, all own scripts. Rejected: no script route is known to add an effect without QE, and `overwrite_clip`'s
  logic (marks, stream checks, audio track) is large and works.

Fallback: when the live measures of section 9 fail for own key writes, `live keys set` uses route B for keys.
The command line and the output stay the same.

## 6. Shared rules (all three commands)

- Every write goes through `WriteSession` (Plan 2). Guards as today: lease, heartbeat and versions, recovery
  marker, project guard, `__expect` inside every script.
- `--sequence ID` is required. The plan files hold node IDs, and node IDs repeat across open projects.
- `--dry-run` reads and checks everything, prints the full plan with every refusal, and writes nothing.
- Order: check every entry before the first write. A refused entry stops the run before any write, unless
  `--skip-refused` is given; then it is reported as `skipped` with its reason.
- Result per entry: `status` one of `done`, `skipped`, `failed`, `not_run`; the target; what the read-back
  holds now; for `skipped`, a `reason`. A stop names the first entry that did not run.
- Two kinds of `skipped`. "Nothing to do" (`already placed`, `has effect`, `level, no change`) counts as success.
  "Refused" (a check failed, only with `--skip-refused`) does not. Exit status 0 when every entry is `done` or
  skipped for nothing to do; else 1. So a re-run of a finished plan exits 0, and a re-run of a partial run
  finishes it.
- Re-running the same plan is safe: keys replace, placing skips a shot that is already in place, effects skip a
  clip that already has that effect.
- A failure payload carries `stored` and `rendered` (rendered is always null here) and the per-entry list.
- An unexpected error after a switch puts back what was switched and ends as `recipe_error` (rule A3 of the
  Plan 2 final review).
- One "not sent" rule (`stops.py`) for every new recipe.

## 7. `live keys set`

Input, as written by `level_plan.py` (an array, one entry per clip):
`{node, src_in, src_out, name, scale, max_deg, clamped, keys: [{time_seconds, value}]}`.
Times are clip seconds: 0 is the first visible frame.

Per entry, the command:

1. Finds the clip by `node` in the sequence `--sequence`. Refuses `no_clip` when it is not there.
2. Checks the clip is the one the plan was made for: its source in and out equal `src_in` and `src_out` within
   half a frame. Else refuses `clip_changed` (the clip was trimmed or slipped after planning).
3. Checks the clip speed is 1 and not reversed. Else refuses `retimed`.
4. Slow motion (an item interpreted to another frame rate): refused with `slow_motion_unproven` until measure M6
   proves the time base on the real app. After that it is allowed, and the output says which clips are
   interpreted.
5. Key times: a key at or past the clip end minus half a frame moves to the clip end minus one frame, exact in
   ticks (the film session's rule). Every moved key is listed in the entry result with its old and new time.
   A key at a negative time is refused. Two keys that land on the same frame are refused `key_clash`.
6. Scale: written as a static value with the Plan 2 write rules, before the keys. Refused `keyed` when Scale
   has keys. `--no-scale` leaves Scale as it is.
7. Rotation: all existing Rotation keys are replaced. One script per clip removes the old keys, adds every new
   key at media ticks = clip in point + seconds (the server's rule), sets each value, and reads every key back.
   `done` needs the same number of keys, every time exact to the tick, every value within 0.0001.
8. Between clips the session breathes (Plan 2 rule), then checks the next clip again.

A clip that fails in step 7 is `failed` with the keys it holds now. A re-run of the plan writes it again.

Static shots (the film session's rule from v10). A shot is static when the spread of its planned key values
(largest minus smallest) is 0.75 deg or less. Its static Rotation is the midpoint (smallest + largest) / 2, so
the worst error stays at 0.375 deg or less. This rule gives the 13 static and 11 keyed shots of v10 (S07 has a
spread of 0.85 and stays keyed). Since 2026-11-19 `level_plan.py` writes `"spread_deg"`, and `"static": true|false` and
`"static_value"` per entry (`level_v10.json` was regenerated with them). The command follows those fields when they are there, and else computes the rule
above. `--static-spread DEG` overrides 0.75. A static shot gets its old Rotation keys removed and one static
value written, with a read-back.
An entry with no keys and `"static": false` means "level enough, do nothing": the command writes neither
Rotation nor Scale and keeps any existing keys. Its status is `skipped` with the reason "level, no change".

`live keys get` reads one property and prints `{is_time_varying, keys: [{seconds, media_ticks, value}]}`. It
is the tool for the comparison in M5 and for any later check.

## 8. `live place`

Input, as read by `place_plan.py`: `{fps, shots: [{name, item, src_in | src_in_f | cam_in_f + cam_fps +
interpreted, frames | end_f | end | src_out, start_f | start, track, audio_track, item_fps, media_length,
note}]}`.

- The CLI computes the same `set_item_in_out` and `overwrite_clip` arguments as `place_plan.py`, in its own
  code (no import of skill files). The test for this is exact: for `shots_v10.json` the dry run gives the 50
  calls of `place_v10.json`, number for number.
- Session A (lease held): guards; reads the sequence; for every shot reads what lies in its span on its video
  track and its audio track. A span that holds a clip is refused `span_not_empty` unless `--overwrite` is given;
  with `--overwrite` the clips that will be cut are listed. The same item at the same start is reported
  `already placed` and skipped (the server would refuse it and open a recovery). Then it makes the sequence
  active and reads that back.
- MCP session (no lease, like `live frame export`): per shot `set_item_in_out` then `overwrite_clip`, both with
  `expected_project_path` and `expected_cep_target`. The run stops at the first failure.
- Session B: puts the active sequence back; reads the sequence; for each shot finds the new clip (same item, on
  the track, start within half a frame) and checks start, in and out to the frame. The entry result gives the
  new clip's node ID, so the next command (keys, effects, `effect set`) can use it.
- Item marks: `set_item_in_out` changes the marks of the project item. They stay as set, and the output lists
  them. (No put-back: placing uses the marks only at the moment of the call.)
- Sound only: a shot with `"audio_only": true` is placed with its picture on the shot's `track` (a spare video
  track; its span must be empty), then the picture clip is removed with `remove_from_timeline`
  (`include_linked: false`). The result names both clips: the sound clip kept, the picture clip removed. (v10
  had 4 such shots.)
- Stills: a still imports with a length of 4.96 s. A still shot longer than that is placed, then extended with
  `set_clip_duration` to its planned length. How the command knows an item is a still is measure M9.
- The steps that need a placed clip's node ID (removing a picture, extending a still) run in a second MCP
  session after a read of the new clips. The sequence stays active until the last step, then it is put back.
- A first V1 clip with a start transition gets a warning (overwriting it deletes the transition).

## 9. `live effect apply`

- Order given = render order. Each new effect appends in render order, so ProcAmp then Lumetri Color renders
  ProcAmp first. The command refuses a request with Lumetri before ProcAmp unless `--any-order`.
- One entry is one (clip, effect) pair. A clip that got ProcAmp but not Lumetri Color is finished by a re-run.
- Session A: guards; reads the effects of each clip; a clip that already has an effect of that name is
  `skipped: has effect` (two Lumetri on one clip break index-based writes); `--allow-second` adds another one
  anyway; makes the sequence active.
- When an existing effect makes the render order differ from the request, the entry carries a warning.
- MCP session: `apply_effect` per clip per effect (not `batch_apply_effect`, which also hits stills and cards).
  A failed call is checked by a read: the server warns that QE may have added the effect anyway. The run stops
  there.
- Session B: puts the active sequence back; reads every clip; checks the new effects are there in the asked
  render order; gives each new effect's `effect_index` for `live effect set` and `live lut set`.
- Gyroflow gets its defaults only. The output says so. Gyroflow may come before or after Lumetri Color (it moves
  the picture, it does not change color), so it has no order rule.

## 10. Live measures before the key code is built (the controller runs them, with Urko's word)

On a copy of the fixture, then on a COPY of `20261115 Fall v10 Claude.prproj` (the film session's condition).
The copy goes to `/Volumes/Samsung 2TB/Videos Premiere/Claude templates/Premiere 2027 tests/cli-milestone-2/`
with "Claude" in its name. The original is never opened. Only Urko clicks dialogs.

- M1: does `setTimeVarying(true)` add a key by itself?
- M2: `addKey` at a time that already holds a key: update or second key?
- M3: the way to remove all keys of a property (`removeKey` per key, or `setTimeVarying(false)`), and what value
  the property holds after.
- M4: negative Rotation keys (-2.6, -0.07) store exactly.
- M5 (the film session's condition 1). Write all 24 shots of `level_v10.json` with the own script on the copy,
  then read them back with `live keys get`.
  - Against the plan (`level_v10.json`), the pass test: keyed shots have every planned key, at the exact media
    ticks, value within 0.0001 (a moved last key at clip end minus one frame); static shots hold the planned
    `static_value` within 0.0001.
  - Against v10 (read before the write), to confirm the time base: every key that v10 and the plan share must
    have the same media ticks and a value within 0.0001. v10's last keys were sent as the rounded plan time
    minus 0.042 s, so they need only the same frame. v10 lacks one planned key on 6 shots (S03 2.407, S04 0.746,
    S05 0.62, S07 1.488, S08 0.204, S17 2.12; a typing slip in v10, not a fault), so the count differs there by one.
    S25 was trimmed after leveling: it is not written, and only its keys inside the visible clip are compared. v10's static
    values were typed rounded: they must agree within 0.01 deg or less.
  - A failed pass test, or a shared key at another time, means fallback B.
- M6 (condition 2): slow motion on S01 (C2245, 120 fps interpreted to 23.976). Write two keys with the own
  script and with `add_keyframe` on the copy; compare ticks; export two frames to see the rotation at the key
  times. Proven: allowed. Not proven: refused with `slow_motion_unproven`, as in section 7.
- M7: a placed clip's node ID is readable by `live clip list` right after `overwrite_clip`.
- M8: `remove_from_timeline` with `include_linked: false` on the picture of a sound-only shot keeps the sound
  clip, its start and its in point.
- M9: an own read that tells a still item from a video item, and `set_clip_duration` on a placed still.

## 11. Fix N12.1

`live lut set --prove`: when `--prove` ran and gave `rendered` true or false, drop the warning "Read-back is
not proof for a LUT. Use --prove." Keep it when `--prove` was not given.

## 12. Left to the builder

- File and function names, the click wiring (model: `live effect set`), the exact JSON key names of the result
  beyond those named here.
- Splitting the work into plan tasks. Order set by the film session: N12.1 and the shared result helper;
  `live keys get`; the measures M1 to M6; `live keys set`; the measures M7 to M9; `live place` (with sound-only
  shots and stills); `live effect apply`; live tests; documents.
- Documents: new sections in `README.md` and `skills/SKILL.md`, reviewed by the film session.

## 13. Constraints carried from the main design and Plans 1 and 2

- Never `unsafe-script`; never acknowledge a recovery; ES3 scripts; every finder before the first host write.
- The file reader commands and their tests stay as they are (another session owns them).
- Live runs: the controller only, with Urko's word in its own window; peers told before and after; only copies
  with "Claude" in the name; close without saving.
- No push without Urko's word.
