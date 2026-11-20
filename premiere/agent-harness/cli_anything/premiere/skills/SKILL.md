---
name: "cli-anything-premiere"
description: >-
  CLI for Adobe Premiere Pro project files (.prproj). Lists sequences and the
  clips on each track with timeline and source times, and reads every effect on a clip:
  parameter values, keyframes, the render order, and the LUT file of a Lumetri Color
  effect. Looks for Premiere 27 Color Mode markers (not checked on a real grade). The file
  commands work on the saved file, with no running Premiere, and never change a project. The
  CLI's live commands (doctor, live, mcp, ui, recovery) read and change a running Premiere 27
  through the MCP Bridge panel: clip lists, effect values, LUTs, still frames, Rotation keys,
  shots placed from a plan, and effects applied in render order.
---

# cli-anything-premiere

Use this tool when the user asks what is inside a Premiere project file: which sequences it
has, which clips sit where, which effects a clip has and with which values, which LUT a
Lumetri effect loads, or which clips have a Color Mode grade.

Do not use the file commands to edit. They have no write command. For edits use the live commands
(see "Live commands on the running Premiere 27" below) or another tool that drives the running app.

## Rules

- It reads the file on disk. If the user changed things in Premiere and did not save, the file is old. Say so.
- Track and clip numbers start at 0. V1 is `--track 0`.
- Use the parameter `index`, not the name. Lumetri has several parameters with the same name
  (Temperature at 14 is Basic Correction; Temperature at 101 is HSL Secondary).
- `render_position` 1 renders first. The list in `sequence clips` is in file order, which is the reverse.
- `format_warning` in `project info` means the values for this file format were not compared with
  Premiere (only format 46, Premiere 27.0.0, is fully tested). Say so when you report values.
- Color Mode is not supported yet. Detection was never checked on a real Color Mode project. Never
  tell the user "this project has no Color Mode grade" from this tool. Never describe operations or masks.
- `lumetri.layout_recognized` false means the Lumetri parameters are not where the reader expects. Then use `params` by name with care.

## Commands

```bash
cli-anything-premiere --json project info  FILE
cli-anything-premiere --json project media FILE
cli-anything-premiere --json sequence list FILE
cli-anything-premiere --json sequence clips FILE [--sequence NAME] [--kind video|audio|both] [--track N]
cli-anything-premiere --json clip effects  FILE [--sequence NAME] [--track N] --clip N [--effect NAME] [--all]
cli-anything-premiere --json colormode list FILE
cli-anything-premiere --json status
```

`--sequence` takes a name or an id. It is needed only when the project has more than one sequence.

## Output

Success: `{"ok": true, ...}`. Failure: `{"ok": false, "error": "...", "code": "..."}` and exit status 1.
Codes: `file_not_found`, `bad_file`, `bad_xml`, `not_a_project`, `too_large`, `no_sequence`,
`sequence_needed`, `sequence_not_found`, `track_not_found`, `clip_not_found`, `effect_not_found`.

`clip effects` gives, per effect: `display_name`, `match_name`, `intrinsic`, `bypass`,
`render_position`, `params` (each with `index`, `parameter_id`, `name`, `kind`, `value`,
optional `keyframes` with `media_seconds` and `value`), and for Lumetri Color a `lumetri` block
with `input_lut_path`, `temperature`, `tint`, `saturation`, `exposure`, `contrast` and more.

## Example

```bash
cli-anything-premiere clip effects film.prproj --sequence "Film v3" --clip 4 --effect "Lumetri Color"
```

prints the LUT file and the Lumetri values of the fifth clip on V1.

## Live commands on the running Premiere 27

These commands talk to the running Premiere through its MCP Bridge panel. `doctor`,
`recovery show`, `live state`, `live clip list`, `live effect get`, `live keys get` and
`ui mode get` only look. `live effect set`, `live lut set`, `live frame export`,
`live app proxies`, `live app activate`, `live keys set`, `live place`, `live effect apply` and
`ui mode set` change the running app. The commands for keys, placing and applying effects have
their own section, "Plan files on the running Premiere 27", below. `live run`, `mcp call` and
`mcp batch` run whatever you send. No recipe saves the project. Tell the owner that a change is
not saved.

### Before the first live command

- Run `cli-anything-premiere --json doctor` first. It only looks. Go on only when no check
  failed (`summary.fail` is 0). Each failed check says what to fix.
- The live commands need Premiere Pro 27.0.x with the MCP Bridge panel 1.25.0 running, and
  `premiere-pro-mcp` 1.25.0 installed. With another version, `live` reads warn and `live` writes
  refuse. `--allow-untested` overrides that. Use it only when the owner says so.
- Never open a film's Premiere 2026 project in Premiere 2027. Opening a 2026 project in 2027
  needs a click from the owner, converts it and writes `<name>_1.prproj`; Premiere 2026 cannot
  open the result. Test on a copy.

### Every write

- A write refuses unless the active project's file name holds "claude" (any case), such as
  `Film Claude.prproj`. Working copies made for Claude carry the word. The guard does not tell a
  test copy from a film's working copy: check the project path before a write. For another
  project the caller gives both `--any-project` and `--project PATH`, with PATH the path of the
  active project. `--project` does not switch projects. Do that only when the owner asks.
- With two projects open, a write needs `--project PATH` of the active one. Node IDs repeat
  across projects.
- Name the target. Give `--sequence ID`. Give the clip by `--track N --clip N` (from
  `live clip list`) or by `--node-id ID`. Give the effect by name, with `--effect-index N` when
  the name occurs twice on the clip. Give the property by `--index N` together with its
  `--name`. No recipe uses the selection, the playhead or the active sequence, except
  `live frame export`, `live lut set --prove`, `live place` and `live effect apply`, which switch
  the active sequence and put it back.
- `live state` shows the ID of the active sequence. `mcp call list_sequences` lists every
  sequence with its ID. Pass `--sequence` to the reads too: without it `live clip list` and
  `live effect get` read the active sequence and only warn.
- Lumetri Color has 130 properties. Temperature, Saturation and Contrast occur more than once,
  so always give the index with the name.
- Put a risky write last. A plan stops at the first write that is not stored.
- Apply effects in render order: Gyroflow (when used), then ProcAmp, then Lumetri Color. Effects
  render in the order they were applied, and ProcAmp must render before the LUT.
- Before you remove a clip (with `mcp call`, `mcp batch` or `live run`), read its ProcAmp,
  Motion and keyframes. They are lost with the clip.

### Waiting, dialogs, stops and recoveries

- The live commands share one lock with the `premiere-pro-mcp` server. The lock is not fair:
  when another session works in Premiere, a command may wait. Raise `--lease-wait SECONDS`
  (30 by default).
- Do not start a live command while one of your own `premiere-pro-mcp` calls is still
  running. Both need the same lock, so the command only waits for your own call.
- A dialog in Premiere blocks every call. Only the owner clicks it. On a timeout, or when a
  message says a dialog may be open: stop, do not retry, and tell the owner.
- An error with the code `recovery_required` means the outcome of an earlier command is
  unknown. Run `cli-anything-premiere recovery show` and tell the owner what it shows. The CLI
  never confirms a recovery, and no `live` write runs until it is closed.
- Stop a command with Ctrl-C. Never with `kill -9`: it leaves the lock held by a dead process,
  and clearing that lock opens a recovery.

### Commands without the write checks

- `mcp call`, `mcp batch`, and `live run` without `--write` run WITHOUT the CLI's write checks:
  no project guard, no version refusal, no recovery check. Use the recipes for writes when one
  fits.
- Each `mcp call` starts a new server. For several calls in a row, put them in one
  `mcp batch` file. Node IDs from one call work in the next, but the node ID tools search the
  active sequence only.
- When `--timeout` of `mcp call` or `mcp batch` runs out, the CLI stops the server, and Premiere
  may open a recovery. Raise `--timeout` for exports.
- `copy_effects_between_clips` with several effects applies them in reverse order: copy one
  effect per call, ProcAmp first.

### Proof

- `stored`: the value read back equals the value written. `rendered`: an exported frame shows
  the change. `rendered: true` means only that the frame changed between before and after. A
  change by another session to the same clip in that time (a Lumetri grade, say) also gives
  `true`. The exit status is 1 when either is `false`. `null` means not checked, or, after
  `--prove`, no verdict: the warnings say why. The exit status is then 0. Do not report the LUT
  as rendered.
- Read-back is not proof for a LUT, a mute or an effect order. Say which level you reached.
- `live lut set --prove` shows that the picture changed. It does not show that it is the right
  LUT. For a film, compare a frame with an ffmpeg lut3d render of the source.
- `live frame export` checks only that each file exists and is not empty. A PNG still can
  carry alpha: composite it over black before you measure it.

### live effect set

- It finds and checks every target before the first write. When one entry may not be written,
  nothing is written. That holds before the first write only. A long plan gives other sessions
  a turn between writes. Then it finds every target again and runs every check again on the
  entries not yet written, the `expect_old` check too. It can stop midway, with `target_moved`
  or a refusal. The error says "Nothing more was written", and `results` lists the entries that
  ran.
- The write script also reads the old value right before it writes. When it is not the
  `expect_old` value, the script refuses with `precondition` and writes nothing.
- It refuses: a property with keyframes; a Gyroflow effect; Lumetri Color index 7, 32 and 34; a
  property that holds an array (such as Position) or a colour; a value it cannot read; a number
  that is not finite or is 4294967296 or more; a name that does not match the index; an effect
  name that occurs twice without `--effect-index`; a clip on an audio track; a property that does
  not hold the `--expect-old` value.
- A plan stops at the first write that is not stored. The entries after it do not run.
- When a value goes from above zero to below zero, 0 is written first. A value below zero gets
  two tries, then a near value (-2.496 for -2.6). When nothing lands, the old value is put
  back. A near value that lands still counts as not stored: tell the owner which value landed.
- Run a new plan with `--dry-run` first. It checks every target and writes nothing.

### live frame export and live lut set

- While `live lut set --prove` runs, and while `live frame export` runs without `--proxies keep`,
  proxy display is off and the target sequence is active for every session. With `--proxies keep`
  only the active sequence is switched. Do not run it while another session works on another
  sequence. Tell the owner first.
- `live frame export` exports the sequence named by `--sequence`, also when another one is
  active. It switches proxy display off and makes the sequence active, and puts both back
  after, unless someone changed it meanwhile; then it is left as it is, with a warning.
  `--proxies keep` leaves proxy display as it is. It makes the `--out-dir` folder.
- After a failed export the output says what is still switched. After the owner closes the
  recovery, run only the commands the output prints (`left_switched` and the warnings name them).
- An existing frame file is refused. `--overwrite` replaces it.
- `live lut set` needs a clip that already holds a Lumetri Color effect. It writes the path
  plus a trailing `/` into index 4, then 1 into index 6. It never writes index 7.
- `--prove` needs `--time` inside the clip, `--out-dir`, and ffmpeg on PATH.
- In a project converted from Premiere 2026, Lumetri index 6 can read 0 while the LUT in the
  path slot still renders (measured by the film-edit session on 2026-11-17). `stored` follows
  index 4 only. The output shows index 6 as `menu_value` with a warning. Use `--prove` there.

### Live commands

```bash
cli-anything-premiere --json doctor
cli-anything-premiere --json recovery show
cli-anything-premiere --json live state
cli-anything-premiere --json live clip list --sequence ID [--track-type video|audio|both] [--track N] [--offset N] [--limit N] [--detail]
cli-anything-premiere --json live effect get --sequence ID (--track N --clip N | --node-id ID) [--track-type video|audio] [--effect NAME] [--effect-index N]
cli-anything-premiere --json live effect set --sequence ID [--sequence-name NAME] (--track N --clip N | --node-id ID) --effect NAME [--effect-index N] --index N --name NAME --value JSON [--expect-old JSON] [--dry-run]
cli-anything-premiere --json live effect set --from PLAN.json [--dry-run]
cli-anything-premiere --json live frame export --sequence ID [--sequence-name NAME] --time SECONDS [--time SECONDS] --out-dir DIR [--prefix NAME] [--format png|jpg|tif] [--overwrite] [--proxies off|keep] [--dry-run]
cli-anything-premiere --json live app proxies on|off [--dry-run]
cli-anything-premiere --json live app activate --sequence ID [--sequence-name NAME] [--dry-run]
cli-anything-premiere --json live lut set --sequence ID [--sequence-name NAME] (--track N --clip N | --node-id ID) --lut FILE.cube [--effect-index N] [--prove --time SECONDS --out-dir DIR] [--dry-run]
cli-anything-premiere --json live run SCRIPT.jsx [--args JSON] [--write] [--verify CHECK.jsx] [--project PATH] [--any-project]
cli-anything-premiere --json mcp list [--query TEXT]
cli-anything-premiere --json mcp describe TOOL
cli-anything-premiere --json mcp call TOOL [--args JSON] [--timeout SECONDS]
cli-anything-premiere --json mcp batch FILE [--timeout SECONDS]
cli-anything-premiere --json ui mode get
cli-anything-premiere --json ui mode set MODE
```

Every `live` command also takes `--lease-wait SECONDS`, `--timeout SECONDS` and
`--allow-untested`. The `live` commands that change the app (and `live run`) take
`--project PATH` and `--any-project`. The `mcp` commands take `--lease-wait SECONDS`.
`--lut` is an absolute path to a `.cube` file.

A plan file for `live effect set --from`:

```json
{"sequence_id": "ID", "sequence_name": "Film v3", "writes": [
  {"track": 0, "clip": 0, "effect": "ProcAmp", "index": 0, "name": "Brightness", "value": -2.6},
  {"track": 0, "clip": 0, "effect": "Lumetri Color", "index": 14, "name": "Temperature", "value": -20}]}
```

An entry may also give `node_id`, `effect_index` and `expect_old`.

Output: `{"ok": true, ..., "warnings": [...]}`. A failure is `{"ok": false, "error": "...",
"code": "..."}` with exit status 1; a write that ran in part also carries what it wrote. Codes
to know: `bridge_down` (the panel does not answer), `untested_bridge` and `untested_premiere`
(a version was not tested), `project_guard`, `two_projects`, `project_mismatch`,
`recovery_required`, `lease_busy` (the lock stayed busy for `--lease-wait`), `timeout`,
`unknown_outcome` (a write may or may not have landed: a recovery is open), `not_stored`,
`not_rendered`, `interrupted`, `doctor_failed`, `bad_args`.

Codes of the recipes:
- `precondition`: a target, a name or an `expect_old` value did not match. The write that
  refused wrote nothing.
- `refused_parameter`: the property or the value may not be written. `bad_plan`: the plan file
  is not valid.
- `target_moved`: a clip or an effect moved while other sessions had a turn. Nothing more was
  written.
- `not_applied`: Premiere did not pick up one command, often because a dialog is open. That
  command changed nothing. An earlier command of the same run may have landed (the second or third
  try of a write, say): read the output for what ran. Tell the owner.
- `not_restored`: the work was done (frames exported, shots placed or effects applied), but a
  switch was not put back. Run the put-back commands that the warnings print.
- `not_switched`: proxy display or the active sequence did not switch. Nothing was exported,
  placed or applied.
- `export_failed`: a frame was not exported, or its file check failed. The output says what is
  still switched.
- `output_exists`, `output_dir`: a frame file exists, or the folder cannot be used. No frame was
  exported and nothing was written to the project.
- `lumetri_layout`: the Lumetri Color does not have the layout `live lut set` knows. Nothing
  was written.
- `ffmpeg_missing`, `ffmpeg_failed`: `--prove` needs ffmpeg, or ffmpeg could not read a frame.
- `recipe_error`: a fault in this CLI. After an export, a placing or an apply, the output still
  names what is switched.

Codes of the plan commands (`live keys set`, `live place`, `live effect apply`). The `code` of a
failed run is one of these (`bad_plan`, `bad_args`, `not_restored`, `interrupted`,
`unknown_outcome` and `recipe_error` are explained above):
- `refused`: a check refused an entry, and `--skip-refused` was not given. The message names the
  first refused entry and its reason code. Nothing was written. A dry run gives it too.
- `not_done`: the run ended, and not every entry is `done` or skipped for nothing to do. The
  message gives the counts (done, skipped, failed, not run) and the first entry that did not run.
- `fps_mismatch` (`live place`): the plan's `fps` is not the frame rate of the sequence. Nothing
  was written.
- `bad_order` (`live effect apply`): the request names Lumetri Color before ProcAmp. It is refused
  before any contact, unless `--any-order` is given.
- `too_many_clips` (`live effect apply`): the track holds more than 400 clips. The command read
  the track and applied nothing.
- `bad_reply`: a script answered in a shape this CLI does not know. After a write, check the clip
  in Premiere.

The `reason_code` of one entry, in `results`. A refusal is `skipped` and is not success. A reason
marked "nothing to do" is `skipped` and counts as success. `render_order` belongs to a `failed`
entry:
- `no_clip`: the clip is not there. `live keys set`: no clip with that node ID in the sequence.
  `live effect apply`: no clip with that number on the track.
- `clip_changed` (`live keys set`): the clip shows other media than the plan's `src_in` to
  `src_out` (more than half a frame apart), so it was trimmed or slipped after the plan was made.
  Also given when Premiere did not report the in or out point.
- `retimed` (`live keys set`): the clip has a speed change or is reversed, or Premiere did not
  report either. Keys are written on clips at normal speed only.
- `slow_motion_unproven` (`live keys set`): only given when `SLOW_MOTION_PROVEN` is false in the
  code. Measure M6 proved slow motion, so it is true and this build never gives the code. A clip
  whose item frame rate Premiere did not report gets a warning instead.
- `motion_layout` (`live keys set`): no Motion effect, or Scale is not at Motion index 1, or
  Rotation is not at index 4.
- `keyed` (`live keys set`): Motion Scale has keys, or Premiere would not say. Give `--no-scale`
  to leave Scale as it is.
- `bad_length` (`live keys set`): the length of the clip in the sequence is not a whole number of
  frames, so keys cannot go on the frame grid.
- `negative_key` (`live keys set`): a key time is before the clip start.
- `key_clash` (`live keys set`): two keys fall on the same frame of the clip. A key at the end
  moves to the last frame, so two end keys clash.
- `no_change` (nothing to do, `live keys set`): the entry has no keys and is not `"static": true`.
- `keys_match` (nothing to do, `live keys set`): the clip holds the plan already, so nothing is
  written: its Rotation keys (the same media ticks, values within 0.0001) or its static Rotation,
  and its Scale unless `--no-scale`. Interpolation is not compared; a key set to Bezier by hand is kept on a re-run.
- `no_item`, `ambiguous_item` (`live place`): no project item has that node ID or name, or more
  than one has that name. Name the item by its node ID.
- `no_track` (`live place`): the sequence has fewer video or audio tracks than the shot names.
- `plan_overlap` (`live place`): the shot overlaps another shot of the plan on one track.
- `no_sound` (`live place`): the shot is sound only (`audio_only`), and the item has no sound.
- `placed_differently` (`live place`): the item is already at that start, but not as planned: with
  other in and out points, as a picture without the sound of a sound-only shot, as a still that is
  longer than planned, or as a clip that starts within one frame of the start but not on it
  (`overwrite_clip` would count it as placed and fail). `--overwrite` cannot place the same item at
  the same start.
- `span_not_empty` (`live place`): clips lie in the span of the shot. Give `--overwrite` to cut
  them. A sound-only shot needs an empty picture span on its video track, also with `--overwrite`:
  name a spare track.
- `already_placed` (nothing to do, `live place`): the shot is in place already.
- `effects_unread` (`live effect apply`): Premiere did not report the effects of the clip, so it
  is not known whether it holds the effect.
- `has_effect` (nothing to do, `live effect apply`): the clip holds an effect of that name.
  `--allow-second` applies another.
- `render_order` (`live effect apply`, failed): the effect landed, but the requested effects do
  not end the render order of the clip in request order. The message says where it landed.

## Plan files on the running Premiere 27

`live keys set`, `live place` and `live effect apply` check every entry of a plan before the first
write and give one result per entry. `live keys get` only looks. They follow every rule of "Live
commands on the running Premiere 27" above: `doctor` first, the project guard, one lock, no saving.
The target is always the sequence you name by ID, as before. Two of them also switch the active
sequence while they run (see below).

```bash
cli-anything-premiere --json live keys get --sequence ID (--track N --clip N | --node-id ID) [--track-type video|audio] [--effect NAME] [--property NAME] [--property-index N]
cli-anything-premiere --json live keys set --sequence ID [--sequence-name NAME] --from LEVEL.json [--no-scale] [--static-spread DEG] [--skip-refused] [--dry-run]
cli-anything-premiere --json live place --sequence ID [--sequence-name NAME] --plan SHOTS.json [--overwrite] [--skip-refused] [--dry-run]
cli-anything-premiere --json live effect apply --sequence ID [--sequence-name NAME] --track N (--clips 0,3,5 | --all) --effect NAME [--effect NAME] [--any-order] [--allow-second] [--skip-refused] [--dry-run]
```

The three writers also take `--project PATH`, `--any-project`, `--lease-wait`, `--timeout` and
`--allow-untested`, as the other writing recipes do.

### Rules for an agent

- Run `--dry-run` first and read every refusal. A dry run reads and checks every entry, writes
  nothing and does not switch the active sequence. When an entry is refused, find out why and tell
  the owner. Do not add `--skip-refused` to get past a refusal you have not understood: it runs
  the other entries and leaves the refused ones `skipped`.
- Name the sequence by ID (`--sequence ID`, from `live state` or `mcp call list_sequences`).
  A level plan holds the node ID of each clip. A placement plan holds the node ID or the name of
  each project item. A node ID holds for one open of a project only, and node IDs repeat across
  open projects. After a project was opened again, read them again (`live clip list` for clips).
  Never reuse IDs from an earlier open.
- The plan formats are those of the film planner scripts, unchanged: `level_plan.py` (its output
  is the level plan of `live keys set`) and `place_plan.py` (its input is the placement plan of
  `live place`). Do not convert them. A key the command does not read passes, and a warning names
  it.
- Keys replace the old Rotation keys of the clip. A key time is seconds from the first visible
  frame of the clip. A key at the clip end, or less than half a frame before it, moves to one frame
  before the end, and the entry lists the move. The interpolation is the default of `addKey`; it is
  never set. One static Scale is written first (`--no-scale` leaves Scale as it is). An entry with
  `"static": true` gets one Rotation value and no keys. `--static-spread DEG` decides static or
  keys by the spread of the keys instead of the plan's flag, and a warning says when that changes
  an entry.
- The Rotation write script checks the clip's in and out points again before it writes; the Scale
  write does not. When the clip was trimmed after the check, Scale is written, Rotation is refused
  (`precondition`), that entry fails and the run goes on. Its warnings say Scale was written: tell
  the owner.
- Slow motion: measure M6 proved the time base of keys on a clip whose item is interpreted to
  another frame rate (`SLOW_MOTION_PROVEN` is true). A clip whose item runs at another rate than
  the sequence is written, with a warning that names the item's rate and says its keys are in
  media time. A retimed clip (speed not 1, or reversed) is refused (`retimed`).
  Do not work round that. Most slow motion in the films is a speed change (2.5x or 5x slow), and
  those clips are refused today. How keys behave on them was not measured (open measure `mspeed`:
  `premiere-pro-mcp` 1.25.0 has no speed setter).
- Placing is overwrite only: a shot goes over what lies in its span, and nothing is inserted or
  rippled. `set_item_in_out` changes the marks of the project item, and they stay as set. The
  output lists them (`marks`): tell the owner that the item marks changed. A clip in the span is
  refused unless you give `--overwrite`, and then it is cut, with a warning. A sound-only shot
  (`audio_only`) puts its picture on its video track first and removes it again, so that track
  must be empty in the span, also with `--overwrite`: name a spare track. A still is told by its
  file extension, and it is extended to its planned length when it comes in shorter.
- `place_read.jsx` sees a clip of the same item only when it overlaps the span by more than
  half a frame. A 1-frame clip of that item that only touches the span edge (one frame before or
  after the start) is not seen, and `overwrite_clip` may then stop the run with a server error
  ("no verifiable new placement"). The run stops at that step and the read after judges the shot.
  Nothing is corrupted.
- `live place` and `live effect apply` make the target sequence active for every session while
  they run, and put the old one back. Tell the owner first. Do not run them while another session
  works on another sequence. Both switch only when an entry has a step to do: a re-run with
  nothing to do switches nothing and writes nothing. `live keys set` and every dry run never
  switch. When the target is active already, nothing is
  switched. After a stop or a recovery the active sequence
  may stay switched: `left_switched` and the warnings name what to put back.
- Effects: name them in render order: Gyroflow (when used), then ProcAmp, then Lumetri Color. A
  new effect renders after every effect the clip holds. A request with Lumetri Color before
  ProcAmp is refused (`bad_order`) unless you give `--any-order`. The order rule does not check
  where Gyroflow stands. A warning says when a Lumetri Color that the clip holds already would
  render before the new ProcAmp. A clip that holds an effect of that name is skipped
  (`has_effect`); `--allow-second` applies a second one. Gyroflow gets its defaults only, because
  a script cannot set its sliders, and its entry carries a warning. Load the shot's Gyroflow
  project in the plugin by hand after the apply. Until then the clip is not stabilized. The output
  gives the effect index of each effect, for `live effect set` and `live lut set`.
- Nothing is saved. Tell the owner.
- After an error with the code `recovery_required`, run `cli-anything-premiere recovery show` and
  tell the owner what it shows. The CLI never confirms a recovery. After `unknown_outcome`, or
  when `stored` is null or an entry says the write "may have landed" or that its outcome "is not
  known", the write may have landed: look at the clip in Premiere before you repeat it.

### Reading the output

One line per entry with its status, then the counts. With `--json`: `results` (per entry: `entry`,
`name`, `target`, `status`, `skip`, `reason_code`, `reason`, `now`, `error`, `warnings`), `counts`
(`done`, `skipped`, `refused`, `failed`, `not_run`), `first_not_run`, `stored`, `rendered` and
`warnings`. `stored` is true when the run succeeded, null when an entry's outcome is unknown, and
false otherwise. A dry run writes nothing and marks no entry `done`, so its `stored` is false
unless every entry has nothing to do: it is the verdict of the entries, not of a write.
`rendered` is always null: nothing here checks a picture. A record whose outcome is unknown also
has the key `unknown` set to true (its `now` is null); no other record has that key. Treat it as
"the write may have landed".

Other keys of the payload: `sequence` (all three commands), `left_switched` and `stopped`
(`live place` and `live effect apply`), `marks` (`live place`: the item marks it set), and in a
dry run `dry_run` (true) and `planned` (what each entry would get; also on a refused dry run). A
record also carries extras: `mode`, `moved` and `interpreted` (`live keys set`), `calls`, `marks`
and `removed_picture` (`live place`: the node ID of the picture clip removed for a sound-only
shot, else null; the sound clip kept is in `now`), `effect` and `effect_index` (`live effect
apply`).

- `done`: written, and read back as planned.
- `skipped` with `skip` set to `nothing_to_do`: success (`already_placed`, `has_effect`,
  `keys_match`, `no_change`). With `skip` set to `refused`: a check failed, and the entry is not success.
- `failed`: the write did not give the planned result, or its outcome is unknown.
- `not_run`: the run stopped before this entry.

The exit status is 0 only when every entry is `done` or skipped for nothing to do. A real run with
any other entry gives exit status 1, with the code `not_done` or the error that stopped the run. A
refusal without `--skip-refused` stops the run before the first write (the code `refused`). A dry
run exits 1 on a refusal unless `--skip-refused` is given. The codes are in "Codes of the plan
commands" above.

### What a re-run does

- `live place`: a shot already in place is skipped (`already_placed`), a partial run is finished,
  and a finished plan exits 0. With nothing to do it does not switch the active sequence and
  writes nothing.
- `live effect apply`: a clip that holds the effect is skipped (`has_effect`), and a finished run
  exits 0.
- `live keys set`: a clip that holds the plan already is skipped (`keys_match`) and nothing is
  written to it: the same Rotation keys (media ticks, values within 0.0001) or the same static
  Rotation, and the same Scale unless `--no-scale`. A clip that differs in any of these gets its
  entry written again. Interpolation is not compared; a key set to Bezier by hand is kept on a re-run.

The two no-write re-runs (`keys_match`, and `live place` with nothing to do) came after the live
rounds and are unit-tested only.

The live results are in `PREMIERE.md` ("Live checks of Plan 3").
