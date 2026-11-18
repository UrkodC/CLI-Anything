---
name: "cli-anything-premiere"
description: >-
  Read-only CLI for Adobe Premiere Pro project files (.prproj). Lists sequences and the
  clips on each track with timeline and source times, and reads every effect on a clip:
  parameter values, keyframes, the render order, and the LUT file of a Lumetri Color
  effect. Finds clips that carry a Premiere 27 Color Mode grade. Works on the saved file,
  with no running Premiere, and never changes a project.
---

# cli-anything-premiere

Use this tool when the user asks what is inside a Premiere project file: which sequences it
has, which clips sit where, which effects a clip has and with which values, which LUT a
Lumetri effect loads, or which clips have a Color Mode grade.

Do not use it to edit. It has no write command. For edits use a tool that drives the running app.

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
