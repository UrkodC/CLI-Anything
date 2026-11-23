# Premiere 27 mask tools through the screen route: gate results (2026-11-22)

Goal (user, 2026-11-22): give the CLI access to the Premiere 27 mask tools (Object Mask, HSL Mask, tracking) and read masks from the saved project.
Test project (a copy, never a film): `/Volumes/Samsung 2TB/Videos Premiere/Claude templates/Premiere 2027 tests/masks/Mask gate test Claude.prproj`,
sequence "Mask gate - Claude", one clip (C2489) with Lumetri. `fixture_0_no_mask.prproj` = the file before any mask.

## Measured

- Way 2 start: no saved project on the Mac or the Samsung drive holds a real Object or HSL mask (scan of the unzipped XML for AEMask, HSLMask,
  CompoundComponent). `wb_test/PP27 wb test.prproj` holds one script-applied `AE.ADBE AEMask` (default whole-frame mask).
- Gate 1 (Accessibility): System Events sees the menu bar but 0 windows of Premiere. Panels, toolbar and mask buttons are NOT exposed.
  `screencapture -x` works and shows the Program Monitor, so positions come from the picture. Display: 3024x1964 px, 2 px per point.
- Gate 2 (tool keys, from `Keyboard Shortcuts/en/Adobe Premiere Pro Defaults.kys`, context global): Object Mask tool = Cmd+Opt+O
  (`cmd.tools.object.selection`), draw mode = Cmd+Opt+L, add to mask = Cmd+=, subtract = Cmd+-, Program Monitor zoom to fit = Cmd+Shift+0,
  Color Mode: add Adjust = Shift+N, Style = Shift+B, LUT = Shift+V, clip level = Opt+Shift+S, sequence level = Opt+Shift+D.
  Commands that exist with NO default key (the user must assign one): `cmd.tools.19ellipseselection`, `20rectselection`, `21bezierselection`,
  `22hslselection`, `23luminanceselection`, `cmd.invert.mask`, `cmd.set.mask.blend.mode.to.add|intersect|subtract`,
  `cmd.change.to.effect.mask|blur.mask|opacity.mask`. No command name for mask tracking was found in the program files.
- Gate 3 (pointer): PASSED. With the clip selected and Premiere in front: `key code 31 using {command down, option down}` switches to the
  Object Mask tool (the Program Monitor shows Smooth / Sharp). CoreGraphics mouse-moved events from `osascript -l JavaScript`
  (CGEventCreateMouseEvent, kCGHIDEventTap), a few small moves then about 3 s wait, make Premiere highlight the object under the pointer in red.
  A left click made "Object Mask (01) - Smooth" under "Unassigned Masks" in Effect Controls, with Tracker buttons, Feather, Opacity, Expansion,
  Inverted and Blend Mode. The hover picked the WHOLE BOY, not the face. A face-only mask needs the draw mode (lasso) or an intersect.
- Traps: `tell application "Adobe Premiere Pro 2027" to activate` times out (-1712): use System Events `set frontmost of process`. `keystroke "o"`
  with modifiers did not select the tool and the monitor zoom went to 10 %: use `key code`. The front app changed to the terminal between two
  commands: check the front app inside the same command as the event. `live effect get` does not list an unassigned mask.
- First mask in a project opens a dialog "Object Mask Data Storage" (masks are stored in a folder next to the project file). Only the user clicks
  dialogs. For way 2 this means the mask data is NOT all inside the .prproj.

- Gate 4 (tracking): PASSED. The five Tracker buttons are found on a fresh screen capture by template matching (score 1.000; a stored
  crop of the buttons), the middle one (track forward and backward) sits at 42 % of the template width. One click: the playhead ran to the
  end, then back to 0; a second `.prmf` file grew to 1.98 MB in about 12 s for an 11.5 s 4K clip. No dialog. A blind click at remembered
  coordinates missed once because the panel had scrolled: always locate the buttons on a capture taken in the same command.
  Scroll: `CGEventCreateScrollWheelEvent2(null, 0, 1, dy, 0, 0)` after a mouse move over the panel.
- Way 2 (saved file, format 46): an Object Mask is a `VideoFilterComponent` with MatchName `AE.ADBE AEMask2`, DisplayName "Mask2",
  InstanceName "01", 36 params, held as a SubComponent of a container component (the "Unassigned Masks" list). Params by index: 4 and 5
  (no name) turn true after tracking; 7 and 8 "Tracker" (binary; 8 lists the GUIDs of the `.prmf` files in `<project name> Masks/`),
  9 "Path", 10 to 17 Transform (Position, Scale, Rotation, Anchor Point), 19 to 25 Sharpness, 27 Feather, 28 Opacity, 29 Expansion,
  32 Blend Mode, 33 Type (4 for this Object Mask), 34 "User Interactions" (binary: the clicks). The mask picture itself is in the
  `.prmf` files (binary, magic "prmf", version 4): 57 KB for the first frame, 1.98 MB after tracking. `clip effects FILE` does not list it yet.
  Fixtures next to the test project: `fixture_0_no_mask`, `fixture_1_object_mask`, `fixture_2_object_mask_tracked` (.prproj).

## Open

- Assign the mask to Lumetri (drag, or `cmd.change.to.effect.mask` with a user-assigned key), then on/off frame diff.
- Way 2: diff `fixture_0_no_mask.prproj` against the saved project with the mask, and read the external mask folder.

## Built on 2026-11-22

- `cli-anything-premiere mask list FILE` (new `core/masks.py`, tests in `tests/test_masks.py`, local fixtures in `tests/fixtures_local/masks/`):
  lists each mask with type, feather, opacity, expansion, blend mode, tracked or not, and the `.prmf` data files with sizes. Read-only.
  Not done: the clip that carries the mask is not resolved yet (prints "?"); only the Object Mask type number is measured; a mask
  assigned to an effect is not measured (only an unassigned one).
- Not built yet (way 1 as commands): `ui mask object` (tool key, hover, click at a point given in frame fractions, found on a screen
  capture by matching an exported frame) and `ui mask track` (find the Tracker buttons by template, click, wait until the data file stops
  growing). The proof scripts are in `docs/2026-11-22-mask-ui-gates/` (`mouse.js`, `scroll.js`, `tracker_tpl.png`).
- Still open for way 1: assign the mask to an effect (drag, or a user-assigned key for `cmd.change.to.effect.mask`), a face-only mask
  (the hover picks the whole person), HSL and shape tools (no default keys), and an on/off frame diff of an assigned mask.

## Keyboard Shortcuts window (user's screenshots, 2026-11-22, preset "Adobe Premiere Pro Default")

Search "mask" lists these commands under Application: Object Mask Tool, Ellipse Mask Tool, Rectangle Mask Tool, Pen Mask Tool,
HSL Mask Tool, Luminance Mask Tool, Change mask overlay color to (black & white, blue, cyan, green, magenta, none, red, yellow),
Change to blur mask, Change to opacity mask, Invert mask, Rename Mask Or Selection, **Track mask**, **Unassign mask**; under
Panels > Program Monitor Panel: Toggle mask bounding box. So tracking HAS a command ("Track mask") that can get a key: the Tracker
button click of gate 4 is then not needed. Built after gate 4: `ui mask object` and `ui mask track` (core/ui_mask.py, tests/test_ui_mask.py,
extra "screen" = numpy + opencv-python-headless); live test of the two commands not run yet.

## Live test of the commands (2026-11-22, test copy, Premiere 27.0.0)

- The owner assigned keys (read back from `~/Documents/Adobe/Premiere Pro/27.0/Profile-*/Mac/[Custom].kys`):
  Track mask = `cmd.track.bidirectional` = Ctrl+Opt+Cmd+T; HSL Mask Tool = `cmd.tools.22hslselection` = Ctrl+Opt+Cmd+H;
  Ellipse Mask Tool = `cmd.tools.19ellipseselection` = Ctrl+Opt+Cmd+E; Invert mask = `cmd.invert.mask` = Ctrl+Opt+Cmd+I.
- `ui mask object --project P --frame F.png --at 0.54,0.224`: frame found on the capture with score 0.997 (box 964,350 1392x783 px),
  click at 857.8,262.7 points (the same point as the hand-made gate), a new `.prmf` of 56,896 bytes, exit 0, no dialog (second mask).
  Without numpy and opencv in the tool environment it refused with no_picture_matching before any event.
  Install: `uv tool install --force --editable <harness> --with numpy --with opencv-python-headless`.
- `ui mask track --project P` (default `--by key`, key code 17 with control, option, command): tracked in 14 s, new data file 1,525,812 bytes.
- `mask list` on the saved project: masks 01 and 02, both object, both tracked, 2 data files each.
- Fixture kept: `fixture_3_two_object_masks_tracked.prproj`.
- Still open: assign a mask to an effect (`cmd.change.to.effect.mask` has no key yet), face-only mask, `ui mask tool` for HSL and
  ellipse (keys now exist), invert by key, an on/off frame diff of an assigned mask, the owning clip in `mask list`.

## Assigning a mask to an effect (2026-11-22, from the full command list and Adobe's help page)

- The owner pasted the full list of the Keyboard Shortcuts window (Copy To Clipboard). It has NO "Change to effect mask" command
  (the name `cmd.change.to.effect.mask` in the .kys file is not offered). Mask commands that can get keys: the six mask tools,
  Change Draw Mode, Add to object, Subtract from object, Add / Subtract / Intersect (blend modes), Invert mask, Track mask,
  Unassign mask, Change to blur mask, Change to opacity mask, Rename Mask Or Selection, Input to this Effect / Source (Color Managed)
  (HSL key source), Toggle mask bounding box, the overlay colours. Color Mode: Add Adjust / Style / LUT Operation, Target Clip Level.
- Adobe help "Apply effects to existing unassigned masks" (read 2026-11-22): "When you eventually apply an effect, all unassigned masks
  on the clip are automatically moved to that effect and used to constrain it." And: with an effect selected in Effect Controls, a new
  mask goes to that effect.
- Plan to test (not run yet): make and track the mask, THEN apply the face effect by script (`live effect apply`, for example a second
  Lumetri Color). If a script-applied effect takes the unassigned masks, no key and no drag is needed. Prove with `mask list` and an
  on/off frame diff. Open question: the test clip got its Lumetri BEFORE the masks, and they stayed unassigned, as the page says.
- Shortcuts are stored PER MODE. The owner pasted the list twice: in Mode "Edit" the four new keys show (Track mask, HSL Mask Tool,
  Ellipse Mask Tool, Invert mask: Ctrl+Opt+Cmd+T/H/E/I); in Mode "Color" the same commands show no key, and the Color Mode keys
  (Add Adjust Operation Shift+N, Activate Exposure W, ...) show only there. So `ui mask track --by key` works in Edit mode only,
  until the keys are also given in Color mode. "Object Mask Tool" shows no key in either list, yet Cmd+Opt+O switched the tool in
  Edit mode in three live runs (the defaults file has it under context "global"). Neither list has a command that assigns a mask to an effect.

## Assigning by applying the effect after the masks: PROVEN for a script (2026-11-22)

Test copy, clip with Lumetri #1 and two tracked, unassigned Object Masks. `apply_effect` "Lumetri Color" through the MCP server added a
second Lumetri (verified). Exposure +1.5 on one of the two Lumetri effects changed 100 % of the frame; the same on the other changed
16.0 %, 16.0 % and 15.9 % of the frame at 0, 5 and 10 s, inside a box around the boy (x 1463-2679, y 219-2159 at 0 s; the box moves
with him). So a script-applied effect takes the unassigned masks of the clip, as Adobe's help says for hand use, and the tracked mask
follows through the clip. No key, no drag. In `live effect get` the masked Lumetri was the one listed first (effect index 2) and the
old, unmasked one was index 3: find the masked one by a frame diff, not by its place in the list. Exposure was set back to 0 on both.
Fixture: `fixture_4_masks_assigned_to_lumetri.prproj`.

Full chain for a film shot, all by command: select the clip, playhead on a frame, `live frame export`, `ui mask object`, `ui mask track`,
`apply_effect` (the face effect), `live effect set` on the masked effect, on/off frame diff, `mask list` after a save.
Limits: Object Mask takes the whole person (a face-only mask is still open); Edit mode only; Premiere in front and hands off while it runs.

## Automatic face-only mask: tracked Ellipse Mask from a face detector box (2026-11-22, test copy)

- Face box from YuNet on the exported frame (brightened S-Log3): x 0.513 y 0.142 w 0.087 h 0.202 of the frame at 0 s.
- Ellipse Mask Tool by the owner's key (key code 14 with control, option, command), then a mouse DRAG (down, 14 dragged events, up) from
  the top-left to the bottom-right of the box grown by 10 % of its width and 12 % of its height: the ellipse sat on the face.
- Track mask key: the playhead ran from 0 to the clip end (11.47 s) and stopped; an ellipse mask writes no .prmf file, so "done" is
  "the playhead stopped moving", not "the data file stopped growing".
- Lumetri applied after it took the mask. Exposure +1.5 on and off, frame diff against a fresh face box at 0, 2.5, 5, 7.5, 10 s:
  face covered 96, 96, 94, 96, 97 %; centre offset at most 9 px at 4K; 15 to 22 % of the mask lies outside the face box (hair, a rim
  of sky): the edge is hard (Feather 0).
- `mask list` shows it as Type 1 (now named "ellipse"), tracked, no data files, holder Lumetri.
- NOT solved: Feather. The mask's Feather, Opacity and Expansion are not in the effect's script properties (Lumetri shows only its own
  88 Show Mask, 90 Invert Mask, 113 Feather of HSL Secondary and Vignette). A soft edge needs a UI step (type into the Feather field
  found on a capture) or a smaller ellipse. Until then the Track Matte route gives the better edge (skin shape, feathered).
- Trap: during this test Premiere was found in Color mode (the owner may have switched it). The four mask keys exist in Edit mode only:
  check `ui mode get` before sending a mask key, and stop when the mode is not "edit".
