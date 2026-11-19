---
name: "cli-anything-premiere"
description: >-
  CLI for Adobe Premiere Pro project files (.prproj). Lists sequences and the
  clips on each track with timeline and source times, and reads every effect on a clip:
  parameter values, keyframes, the render order, and the LUT file of a Lumetri Color
  effect. Looks for Premiere 27 Color Mode markers (not checked on a real grade). The file
  commands work on the saved file, with no running Premiere, and never change a project. The
  CLI's live commands (doctor, live, mcp, ui, recovery) read and change a running Premiere 27
  through the MCP Bridge panel: clip lists, effect values, LUTs, still frames.
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
`recovery show`, `live state`, `live clip list`, `live effect get` and `ui mode get` only look.
`live effect set`, `live lut set`, `live frame export`, `live app proxies`, `live app activate`
and `ui mode set` change the running app. `live run`, `mcp call` and `mcp batch` run whatever
you send. No recipe saves the project. Tell the owner that a change is not saved.

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
  `--name`. No recipe uses the selection, the playhead or the active sequence.
- `live state` shows the ID of the active sequence. `mcp call list_sequences` lists every
  sequence with its ID. Pass `--sequence` to the reads too: without it `live clip list` and
  `live effect get` read the active sequence and only warn.
- Lumetri Color has 130 properties. Temperature, Saturation and Contrast occur more than once,
  so always give the index with the name.
- Put a risky write last. A plan stops at the first write that is not stored.
- Apply ProcAmp before Lumetri Color. Effects render in the order they were applied, and
  ProcAmp must render before the LUT.
- Before you remove a clip (through `mcp call`), read its ProcAmp, Motion and keyframes. They
  are lost with the clip.

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
  the change. The exit status is 1 when either is `false`. `null` means not checked, or, after
  `--prove`, no verdict: the warnings say why. The exit status is then 0. Do not report the LUT
  as rendered.
- Read-back is not proof for a LUT, a mute or an effect order. Say which level you reached.
- `live lut set --prove` shows that the picture changed. It does not show that it is the right
  LUT. For a film, compare a frame with an ffmpeg lut3d render of the source.
- `live frame export` checks only that each file exists and is not empty. A PNG still can
  carry alpha: composite it over black before you measure it.

### live effect set

- It finds and checks every target before the first write. When one entry may not be written,
  nothing is written. That holds before the first write only: a long plan gives other sessions
  a turn between writes, checks its targets again, and can stop midway with `target_moved`.
- It refuses: a property with keyframes; a Gyroflow effect; Lumetri Color index 7, 32 and 34; a
  property that holds an array (such as Position) or a colour; a value it cannot read; a number
  that is not finite or is 4294967296 or more; a name that does not match the index; an effect
  name that occurs twice without `--effect-index`; a clip on an audio track; a property that does
  not hold the `--expect-old` value.
- A plan stops at the first write that is not stored. The entries after it do not run.
- When a value crosses zero, 0 is written first. A value below zero gets two tries, then a
  near value (-2.496 for -2.6). When nothing lands, the old value is put back. A near value that
  lands still counts as not stored: tell the owner which value landed.
- Run a new plan with `--dry-run` first. It checks every target and writes nothing.

### live frame export and live lut set

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
