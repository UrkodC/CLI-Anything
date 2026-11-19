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
project: save it in Premiere when you want to keep a change.

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

While `live frame export` or `live lut set --prove` runs, proxy display is off and the target
sequence is active for every session. Do not run it while another session works on another
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
  `PREMIERE.md` ("Gate tests" and "Live checks of Plan 2").
- `live effect set` writes numbers, text and true or false, on video tracks only. It refuses
  keyframed properties, colours, arrays and Gyroflow.
- Some failure paths ran only in unit tests, not on the real app: a failed negative write, a
  failed export that leaves proxy display or the active sequence switched, and Ctrl-C during an
  export.
- In a project converted from Premiere 2026, Lumetri index 6 can read 0 while the LUT in the
  path slot still renders (measured by the film-edit session on 2026-11-17). `stored` follows
  index 4 only. The output shows index 6 as `menu_value` with a warning. Use `--prove` there.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest cli_anything/premiere/tests -v
```

The unit tests need nothing. The end-to-end tests need a real Premiere project: see `tests/TEST.md`.
