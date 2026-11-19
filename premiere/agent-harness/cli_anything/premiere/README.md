# cli-anything-premiere

A command line for Adobe Premiere Pro project files (`.prproj`).
It lists sequences and clips, and it reads every effect on a clip with its values,
keyframes and render order. The file commands never write a project and never start Premiere.
The live commands change the running Premiere 27: see "Live commands" below.

Project formats:
- **46 (Premiere Pro 27.0.0): tested.** Every value was compared with Premiere's own readback.
- **38, 40, 41, 42, 45 (older versions, 45 is Premiere 26.5.2): smoke-read.** 107 real projects
  of these formats were read with every command and gave no error. The values were not compared
  with Premiere, so `project info` prints a warning.
- Any other format prints a warning too.

## Why

Premiere's scripting interfaces do not show everything. The project file does:
- the LUT file a Lumetri Color effect points at (the script API shows only a blob),
- the order in which the effects on a clip render,
- which clips carry a Color Mode grade (Premiere 27),
- values and keyframes of any effect, with no running Premiere.

## Install

Premiere Pro is the software this tool belongs to. Install it from Adobe
(https://www.adobe.com/products/premiere.html). The file commands only need the project
files, so they also run on a machine with no Premiere. The live commands need the running app.

```bash
cd premiere/agent-harness
pip install -e .
cli-anything-premiere --version
```

## Use

```bash
cli-anything-premiere status
cli-anything-premiere project info  film.prproj
cli-anything-premiere project media film.prproj
cli-anything-premiere sequence list film.prproj
cli-anything-premiere sequence clips film.prproj --sequence "Film v3" --track 0
cli-anything-premiere clip effects  film.prproj --sequence "Film v3" --track 0 --clip 4
cli-anything-premiere clip effects  film.prproj --clip 4 --effect "Lumetri Color" --all
cli-anything-premiere colormode list film.prproj
```

Every command takes `--json`. With no command the tool starts an interactive shell.
Errors print `{"ok": false, "error": "...", "code": "..."}` with exit status 1.

Things to know:
- Track and clip numbers start at 0 (V1 is track 0).
- Times are seconds. `in` and `out` are source times. An untrimmed clip has no in and out in the file.
- Parameter `index` is the position in the effect's parameter list. It is the same number
  Premiere's script API uses. Several Lumetri parameters share a name, so use the index.
- `render_position` 1 renders first. In the file the applied effects are stored in the
  reverse of their render order (measured on Premiere 27.0.0).
- Keyframe times are media times (seconds into the source), as Premiere stores them.
- Open the project file that is saved on disk. Changes that are not saved in Premiere are not in the file.

## Limits

- The file commands only read. None of them changes a project. The live commands change the
  project that is open in Premiere: see "Live commands".
- Color Mode (Premiere 27) is NOT supported yet. The tool looks for a Color Mode grade by the
  names the script API shows, but this was never checked on a real project with a Color Mode
  grade, and the operations and masks inside a grade are not decoded. `project info` also counts
  the raw Color Mode markers in the file, so a grade the reader cannot place still shows up.
- Audio clips are listed with times. Audio effects are not read.
- Binary parameters (mask paths, the Lumetri blob) are reported as a size only.

## Live commands

The live commands talk to a running Premiere Pro 27 through its MCP Bridge panel. Some of them
only look. The others change the project that is open in Premiere. No recipe below saves the
project: save it in Premiere when you want to keep a change. The four commands that work on keys,
placed shots and effect lists (`live keys get`, `live keys set`, `live place` and
`live effect apply`) are in "Plan files" below.

They need:
- Premiere Pro 27.0.x, running. A write also needs a saved project that is open and active.
- The MCP Bridge panel 1.25.0, open in Premiere (Window > Extensions > MCP for Adobe Premiere Pro).
- The `premiere-pro-mcp` server 1.25.0 on PATH. The `mcp` commands and the frame exports use it.
- Accessibility permission for the terminal, for `ui mode`.
- `ffmpeg` on PATH, for `live lut set --prove`.

With another version of Premiere, the panel or the server, the `live` commands that look print
a warning, and the `live` commands that write refuse. `--allow-untested` lets a write go on.

A write goes only to a project whose file name holds the word "claude" (any case), such as
`Film Claude.prproj`. Working copies made for Claude carry the word. The guard does not tell a
test copy from a film's working copy: check the project path before a write. For any other
project give both `--any-project` and `--project PATH`, with PATH the path of the active
project. `--project` does not switch projects. When two projects are open, a write needs
`--project PATH` of the active one.

Run `doctor` first. It makes thirteen checks (the app, the bridge folder, the panel, the lease,
an open recovery, the open projects, the server, Accessibility, and that the recipe commands
loaded) and changes nothing.

```bash
cli-anything-premiere doctor
cli-anything-premiere live state
cli-anything-premiere recovery show
```

The recipes that write name their target by sequence ID, never by the selection or the playhead.
`live state` shows the ID of the active sequence. `cli-anything-premiere mcp call list_sequences`
lists every sequence with its ID.

```bash
# the clips of one sequence, with node IDs and effects in render order
cli-anything-premiere live clip list --sequence ID
# the effects of one clip and their properties by index
cli-anything-premiere live effect get --sequence ID --track 0 --clip 0 --effect "Lumetri Color"
# one effect value, checked first with --dry-run, then written and read back
cli-anything-premiere live effect set --sequence ID --track 0 --clip 0 --effect ProcAmp --index 0 --name Brightness --value -2.6 --dry-run
cli-anything-premiere live effect set --from plan.json
# still frames of one sequence (the command makes the --out-dir folder)
cli-anything-premiere live frame export --sequence ID --time 1.0 --time 2.5 --out-dir /tmp/frames
# a LUT file into the Input LUT slot of Lumetri Color, with a picture check (it makes --out-dir)
cli-anything-premiere live lut set --sequence ID --track 0 --clip 0 --lut "/Users/me/LUTs/Look.cube" --prove --time 1.0 --out-dir /tmp/lut-check
# switch proxy display, or make a sequence active
cli-anything-premiere live app proxies on
cli-anything-premiere live app activate --sequence ID
```

After a failed export, the output says what is still switched. After the owner closes the
recovery, run only the commands the output prints (`left_switched` and the warnings name them).

While `live lut set --prove` runs, and while `live frame export` runs without `--proxies keep`,
proxy display is off and the target sequence is active for every session. With `--proxies keep`
only the active sequence is switched. Do not run it while another session works on another
sequence. Tell the owner first.

A plan file for `live effect set --from` looks like this:

```json
{"sequence_id": "ID", "writes": [
  {"track": 0, "clip": 0, "effect": "ProcAmp", "index": 0, "name": "Brightness", "value": -2.6},
  {"track": 0, "clip": 0, "effect": "Lumetri Color", "index": 14, "name": "Temperature", "value": -20}]}
```

Each recipe says how far its check reaches:
- `stored`: the value read back from Premiere equals the value written. `live effect set`,
  `live lut set` and `live app` report it.
- `rendered`: an exported frame shows the change. Only `live lut set --prove` reports it.
- A file check: `live frame export` checks that each file exists and is not empty. It does not
  check the picture.

The exit status is 1 when `stored` or `rendered` is `false`. `null` means not checked, or,
after `--prove`, no verdict: the warnings say why. The exit status is then 0. Do not report the
LUT as rendered. A value
that reads back does not always render: for a LUT the read-back is no proof, so use `--prove`.
`--prove` shows that the picture changed. It does not show that it is the right LUT.

Other live commands:
- `mcp list`, `mcp describe`, `mcp call` and `mcp batch` reach any tool of the `premiere-pro-mcp`
  server. The write checks of this CLI do not run there. The server applies its own rules.
- `live run SCRIPT.jsx` runs your own ExtendScript. Without `--write` no write check runs.
- `ui mode get` and `ui mode set MODE` read and click Window > Mode.

Stop a live command with Ctrl-C. Do not use `kill -9`: it leaves the lease held by a dead
process, and clearing it opens a recovery. After an error with the code `recovery_required`,
run `cli-anything-premiere recovery show`. The CLI never confirms a recovery.

Limits of the live commands:
- Tested on macOS with Premiere Pro 27.0.0, one small test project. The results are in
  `PREMIERE.md` ("Gate tests", "Live checks of Plan 2" and "Live checks of Plan 3").
- `live effect set` writes numbers, text and true or false, on video tracks only. It refuses
  keyframed properties, colours, arrays and Gyroflow.
- Some failure paths ran only in unit tests, not on the real app: a failed negative write, a
  failed export that leaves proxy display or the active sequence switched, and Ctrl-C during an
  export.
- In a project converted from Premiere 2026, Lumetri index 6 can read 0 while the LUT in the
  path slot still renders (measured by the film-edit session on 2026-11-17). `stored` follows
  index 4 only. The output shows index 6 as `menu_value` with a warning. Use `--prove` there.

## Plan files

Three live commands work on many clips in one call and give one result per entry:
- `live keys set` writes Motion Rotation keys and one static Motion Scale per clip, from a level plan.
- `live place` places the shots of a placement plan on a sequence, frame-exact.
- `live effect apply` applies effects to the clips of one video track, in render order.

`live keys get` only reads: it lists the keys of one property of one clip. Name the sequence by ID
with `--sequence`. These commands follow the rules of "Live commands" above. No command saves the
project: save it in Premiere when you want to keep the result.

```bash
# the keys of one clip (Motion Rotation unless you name another property)
cli-anything-premiere live keys get --sequence ID --node-id 000f4276
# keys: check the plan first, then write it
cli-anything-premiere live keys set --sequence ID --from level_plan.json --dry-run
cli-anything-premiere live keys set --sequence ID --from level_plan.json
# place: check the plan first, then place the shots
cli-anything-premiere live place --sequence ID --plan place_plan.json --dry-run
cli-anything-premiere live place --sequence ID --plan place_plan.json
# apply: ProcAmp, then Lumetri Color, on every clip of V1
cli-anything-premiere live effect apply --sequence ID --track 0 --all --effect ProcAmp --effect "Lumetri Color" --dry-run
cli-anything-premiere live effect apply --sequence ID --track 0 --all --effect ProcAmp --effect "Lumetri Color"
# apply: Gyroflow, then ProcAmp, then Lumetri Color, on the first clip of V1
cli-anything-premiere live effect apply --sequence ID --track 0 --clips 0 --effect Gyroflow --effect ProcAmp --effect "Lumetri Color" --dry-run
```

The plan files are the ones of the film planner scripts (`level_plan.py` and `place_plan.py`),
unchanged. A level plan is a JSON array with one entry per clip. `src_in` and `src_out` must match
what the clip shows now (within half a frame), or the entry is refused (`clip_changed`). Times in
`keys` are seconds from the first visible frame of the clip:

```json
[{"node": "000f4276", "name": "S02", "src_in": 0.0, "src_out": 3.295, "scale": 104.4,
  "keys": [{"time_seconds": 0.0, "value": 1.26}, {"time_seconds": 3.295, "value": 0.07}]}]
```

A placement plan names the frame rate and the shots. A shot names its project item, its source in
point, its length, its start and its tracks:

```json
{"fps": "24000/1001", "shots": [
  {"name": "S02", "item": "000f4242", "src_in_f": 0, "frames": 79, "start_f": 138, "track": 0, "audio_track": 1}]}
```

Use `--dry-run` first. It reads and checks every entry, prints what it would do and every refusal,
and writes nothing. It does not switch the active sequence. Read every refusal before the real run.

Each entry gets a status:
- `done`: written, and read back as planned.
- `skipped`: not run. There are two kinds. Nothing to do (`already_placed`, `has_effect`, or a
  level entry with no change) counts as success. Refused (a check failed) does not.
- `failed`: the write did not give the planned result, or its outcome is unknown (it may have landed).
- `not_run`: the run stopped before this entry.

A refusal stops the run before the first write: the error is `refused`, and nothing was written. With
`--skip-refused` the other entries run, and the refused ones stay `skipped`. After a real run the
exit status is 0 only when every entry is `done` or skipped for nothing to do. Otherwise it is 1. The
code is `not_done`, or the error that stopped the run, and the output still lists every entry. A dry
run exits 1 on a refusal unless `--skip-refused` is given.

What a re-run does:
- `live place` skips a shot that is already in place (`already_placed`). A re-run finishes a partial
  run, and exits 0 when everything is in place. It still goes through the switch step: when another
  sequence is active, it makes the target active and puts the old one back, though it places
  nothing.
- `live effect apply` skips a clip that holds the effect already (`has_effect`). `--allow-second`
  applies a second one.
- `live keys set` writes the keys again, and reads back the same keys. It does not skip a clip whose
  keys already match.

Things to know:
- New keys replace the old Rotation keys of the clip. A key at the clip end, or less than half a
  frame before it, moves to one frame before the end, and the output lists it.
- Slow motion clips (an item interpreted to another frame rate) are written, with a warning.
  Clips with a speed change or reversed are refused (`retimed`). Most slow motion in the films is a
  speed change (2.5x or 5x slow), so those clips are refused today. How keys behave on them was not
  measured (open measure `mspeed`: `premiere-pro-mcp` 1.25.0 has no speed setter).
- Placing is overwrite only. The marks of the project item stay as set, and the output lists them.
  A clip in the span of a shot is refused unless you give `--overwrite`.
- `live place` and `live effect apply` make the target sequence active for every session while they
  run, and put the old one back. Do not run them while another session works on another sequence.
  Tell the owner first.
- Name the effects in render order: Gyroflow (when used), then ProcAmp, then Lumetri Color. Gyroflow
  gets its defaults only. Load the shot's Gyroflow project in the plugin by hand after the apply.
  Until then the clip is not stabilized.

The live results of these commands are in `PREMIERE.md` ("Live checks of Plan 3").

## Tests

```bash
pip install -e ".[dev]"
python -m pytest cli_anything/premiere/tests -v
```

The unit tests need nothing. The end-to-end tests need a real Premiere project: see `tests/TEST.md`.
