# cli-anything-premiere

A read-only command line for Adobe Premiere Pro project files (`.prproj`).
It lists sequences and clips, and it reads every effect on a clip with its values,
keyframes and render order. It never writes a project and it never starts Premiere.

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
(https://www.adobe.com/products/premiere.html). The tool itself only needs the project
files, so it also runs on a machine with no Premiere.

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

- Read-only. There is no command that changes a project.
- Color Mode (Premiere 27) is NOT supported yet. The tool looks for a Color Mode grade by the
  names the script API shows, but this was never checked on a real project with a Color Mode
  grade, and the operations and masks inside a grade are not decoded. `project info` also counts
  the raw Color Mode markers in the file, so a grade the reader cannot place still shows up.
- Audio clips are listed with times. Audio effects are not read.
- Binary parameters (mask paths, the Lumetri blob) are reported as a size only.

## Tests

```bash
pip install -e ".[dev]"
python -m pytest cli_anything/premiere/tests -v
```

The unit tests need nothing. The end-to-end tests need a real Premiere project: see `tests/TEST.md`.
