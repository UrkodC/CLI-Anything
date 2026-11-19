# Premiere Pro: analysis for the CLI harness

Checked on macOS on 2026-11-18 with Adobe Premiere Pro 27.0.0 (build 88).

## Backend: what can be driven

- **No source code.** Premiere Pro is closed software.
- **No headless mode.** The app has no command line for project work and no batch renderer
  of its own (rendering goes through Adobe Media Encoder).
- **Scripting exists, but only inside the running app.** ExtendScript (CEP panels) and UXP
  plugins run inside Premiere. They need the GUI open and a panel loaded. Other projects
  build MCP servers on that route. The file commands of this harness do not use it. The live
  commands use it through the MCP Bridge panel.
- **The project file is open to reading.** A `.prproj` file is gzip-compressed XML.

Result: the file commands of this harness read the native project file directly. They do not
start Premiere and have no render or export command, because Premiere offers no way to render
without its GUI. `live frame export` exports stills through the running app.
The real software enters through the files it writes, and the end-to-end tests check the reader
against values read back from Premiere itself.

## Data model: the `.prproj` file

Gzip, then XML with the root `<PremiereData Version="3">`. Every object is a direct child of the
root. An object has `ObjectID` (a number, local to the file) or `ObjectUID` (a GUID). References
use `ObjectRef` and `ObjectURef`. The first `<Project ObjectRef="1"/>` is only a pointer.

| Object | What it holds |
|---|---|
| `Project` (`Version`) | The project format version: 42 for Premiere 25, 46 for Premiere 27.0.0 |
| `Sequence` (UID) | `Name`; `TrackGroups/TrackGroup/Second` points at the video, audio and data track groups |
| `VideoTrackGroup` | `TrackGroup/Tracks/Track` (URef to tracks), `TrackGroup/FrameRate` (ticks per frame), `FrameRect` (0,0,width,height) |
| `VideoClipTrack`, `AudioClipTrack` | `ClipTrack/Track/IsMuted`, `IsLocked`; `ClipTrack/ClipItems/TrackItems/TrackItem` (Ref to items) |
| `VideoClipTrackItem` | `ClipTrackItem/TrackItem/Start` and `End` (timeline ticks), `SubClip` (Ref), `ComponentOwner/Components` (Ref to the effect chain) |
| `SubClip` | `Name`, `Clip` (Ref) |
| `VideoClip` | `Clip/InPoint`, `Clip/OutPoint` (source ticks; absent on an untrimmed clip), `Clip/Source` (Ref) |
| `VideoMediaSource` | `MediaSource/Media` (URef) |
| `Media` | `FilePath`, `ActualMediaFilePath` |
| `VideoComponentChain` | `ComponentChain/Components/Component` (Index, Ref): the effects of one clip |
| `VideoFilterComponent` | `Component/DisplayName`, `Bypass`, `Intrinsic`, `Component/Params/Param` (Index, Ref), `MatchName` |
| `VideoComponentParam` | `Name`, `ParameterID`, `StartKeyframe` ("ticks,value,..."), `Keyframes` ("ticks,value,...;" per key) |
| `PointComponentParam` | The same, with the value as "x:y" |
| `ArbVideoComponentParam` | Binary data in `StartKeyframeValue` (base64) |

Time is in ticks: 254016000000 ticks per second. One frame at 23.976 fps is 10594584000 ticks.

Facts measured on Premiere 27.0.0 (fixture built by script, values read back from the app):
- **Render order.** The applied effects are stored in the reverse of their render order.
  Effects applied in the order ProcAmp, ASC CDL, Lumetri Color are stored as Motion, Lumetri Color,
  ASC CDL, ProcAmp. Pixel tests showed ProcAmp renders before the Lumetri LUT.
- **Parameter index.** The position of a `Param` in the list equals the property index of the
  script API (Lumetri Temperature 14, Tint 15; ASC CDL Red Offset 1, Blue Offset 7).
  `ParameterID` is a different number (Lumetri Temperature has ParameterID 7).
- **Static value.** It is the second field of `StartKeyframe`. Premiere 27 writes no `CurrentValue` for most parameters.
- **Keyframe times** are media times, not timeline times.
- **Lumetri LUT file.** Parameter 4 (name is one space) is binary: the path as UTF-16LE text. The
  "Input LUT" menu (index 6) can read 0 in the file while the LUT renders, so the path is the truth.
- **Binary values are stored once.** `StartKeyframeValue` has a `BinaryHash` attribute. The first
  parameter with a given value holds the base64 data. Every later parameter with the same value is
  an empty element with the same hash (the last 8 hex digits are the byte count). A reader must
  resolve these by hash, or the LUT path is missing on every clip but the first. Found on real
  film projects (format 45), then covered by a unit test.

Not measured in a file (do not read these as facts about the format):
- **Color Mode.** Through the script API, a clip with a Color Mode grade shows a component named
  "Color Mode Effect" with the match name `AE.ADBE CompoundComponent`. The reader looks for that
  match name on a clip's component chain. No project file with a Color Mode grade was at hand, so
  the place of the grade in the file, and its inner layout, are unknown. As a safety net the reader
  counts the raw strings `CompoundComponent` and `Color Mode Effect` in the whole file.
- **Render order on formats other than 46.** The reverse-order rule was measured on 27.0.0 only.

Formats seen in a sweep of 110 real projects (2,824 video clips, 8,305 effects, 1,867 Lumetri
effects, no error): 38, 40, 41, 42, 45 (Premiere 26.5.2) and 46. In all 1,867 Lumetri effects
Temperature sits at index 14 and Tint at index 15.

## Commands

File commands: `status`, `project info`, `project media`, `sequence list`, `sequence clips`, `clip effects`,
`colormode list`. All read-only, all with `--json`. `repl` runs any command, the live ones too.

Live commands, on a running Premiere 27 through the MCP Bridge panel, all with `--json`:
- Look only: `doctor` (thirteen checks), `recovery show`, `live state`, `live clip list`,
  `live effect get`, `live keys get`, `ui mode get`, `mcp list`, `mcp describe`.
- Change the running app: `live effect set`, `live lut set`, `live frame export`,
  `live app proxies on|off`, `live app activate`, `ui mode set`, and the three plan commands
  `live keys set`, `live place` and `live effect apply`. A plan command checks every entry, writes
  many clips in one call and gives one result per entry. `live place` and `live effect apply` make
  the target sequence active for every session while they run.
- Run what the caller sends: `live run` (write checks only with `--write`), `mcp call`,
  `mcp batch` (no write checks of the CLI; the server applies its own rules).

No recipe saves the project. `mcp call`, `mcp batch` and `live run` can, because they run what the caller sends.

## Not done, and why

- Color Mode detection check and decoding. Both need one small project with an Adjust operation and a mask.
- Three recipes of milestone 2 are not built: `live effect copy` (an ordered effect chain from one
  clip to others), `live track disable-clips` (disables every clip of a track, the fix for a track
  mute that does not hold) and `live sequence duplicate`. The other four, `live keys get`,
  `live keys set`, `live place` and `live effect apply`, exist: see "Live checks of Plan 3".
- A re-run of `live keys set` writes the keys again. It reads back the same keys and exits 0, but it
  is not a re-run that writes nothing (rehearsal: 23 done again in 9.8 s). A change that skips a clip
  whose keys already match is planned.
- A re-run of `live place` with every shot already placed still goes through the switch step: when
  another sequence is active, it makes the target active and puts the old one back, though it
  places nothing and sends no tool call. A change that switches only when a shot has a step to do
  is planned. `live effect apply` already does this.
- Retimed clips (a speed change, or reversed) are not supported: `live keys set` refuses them with
  `retimed`. Most slow motion in the films is a speed change (2.5x or 5x slow), so those clips are
  refused today. How keys behave on such a clip was not measured. `premiere-pro-mcp` 1.25.0 has no
  tool that sets the speed (`speed_change` always refuses), so no test clip could be made. The
  measure (`mspeed`) needs a clip whose speed the owner sets by hand in Premiere (Clip >
  Speed/Duration).
- Not built in milestone 2: key interpolation, keys on array properties (Position, Anchor Point),
  audio keys, insert edits (placing is overwrite only), removing a clip (the only removal is the
  picture of a sound-only shot), writing Gyroflow parameters (it gets its defaults only, and
  scripted writes to its sliders are ignored, so the owner loads the shot's Gyroflow project in the
  plugin by hand after the apply), and saving.

## Gate tests, 2026-11-18

Five tests that decide how the live commands may work. They ran on Premiere Pro 27.0.0 (build 88),
panel 1.25.0, `premiere-pro-mcp` 1.25.0, on the test project `fixture27 Claude.prproj`. All writes
went to a duplicate sequence (`Gate copy`), which the run deleted at its end. The project was closed
without saving. Script: `cli_anything/premiere/tests/gates/run_gates.py --gate all`. The full
evidence is in `gates_result.json` next to the script (local file, not in the repo).

| Gate | Question | Result | Evidence | What it means for the next plan |
|---|---|---|---|---|
| G1 | After a CLI script write, does a write from an MCP server that was already running pass? | good | Server A wrote ProcAmp Brightness 1 and read 1. The CLI wrote 2 through a raw script under the lease and read 2. Server A then wrote 3 and read 3. No `HOST_RECOVERY_REQUIRED`, no recovery marker. | The script route and a running MCP server can share Premiere. The transport of the design stays. |
| G2 | Does a node ID from one server start resolve in the next start? | good | Node ID `000f4250` from server A (pid 70095) gave the same clip in server B (pid 70107). | `mcp call` can be used with node IDs from an earlier call. One rule stays: the node ID tools search the active sequence only, so the right sequence must be active. |
| G3 | Do preview and apply, and checkpoint create and list, work across two starts? | good | A preview token from one start was accepted by `apply_edit_plan` in the next start, and the inserted clip was read back. A checkpoint made in one start was listed in the next. | Preview and apply can be two separate `mcp call` commands. |
| G4 | Does a negative value written by a raw `setValue` fail with "Illegal Parameter type"? | good | 120 writes, one command each: 30 negative and 30 positive on ProcAmp Brightness, the same on Lumetri Temperature (index 14). Values -3.7, -2.1, -2.6, -40, -20, -10.1, each followed by +5. 120 of 120 read back. No error text. | The raw script showed no fault on this clip. This does not show where the fault of the MCP tool comes from: no control ran (see the limits), and every fault on record is on Premiere 26.5.2. On 27.0.0 no fault was seen through either route. The recipe keeps the "write 0 first" rule of the design and reads back every write. |
| G5 | Does anything collide when a second server starts while another server is in use? | good | 5 repetitions. Server A did 10 reads in a row. A second server started during those reads and answered `ping` each time. No error, no timeout. The second start took 1.8 to 2.6 s (0.7 s when Premiere is idle), because it waits for the lease. | `mcp call` can run while a film session's server works. It waits its turn. |

Limits of these results:

- One machine, one Premiere build, one small project (one sequence, two clips). A long timeline was not tested.
- G4 crossed zero from +5 only. Other start values were not tested.
- G4 had no control: no negative value was sent through the MCP tool `set_effect_property` on the same clip in the same run. So the run does not show that the MCP tool fails where the raw script passes. Lumetri Tint, Vibrance and Blacks were not tested. The control is the first live check of the next plan.
- The five gates ran before the fix wave of the final review, which changed the lease and signal code. After it, the nine live tests ran again and passed. They send reads only. No live write has gone through the fixed code yet, and `live run --write --verify` has not run on the real app.
- G5 used read calls in server A. A second server that starts during a write of server A was not tested.
- The lease is not fair. In 3 of 5 repetitions of G5 the second start most likely waited for the whole group of ten reads of server A. This is derived from the time stamps (the second start ended about 0.6 s after server A's last read, and an idle start takes about 0.6 s); no time stamp of the lease take exists.
- The server leaves a file `edit-plan-token-<hex>.claimed` in the bridge folder after an applied edit plan.

## Live checks of Plan 2

### Round 1, 2026-11-18: write path, clip list, effect get, effect set

Premiere Pro 27.0.0 (build 88), panel 1.25.0, `premiere-pro-mcp` 1.25.0, project `fixture27 Claude.prproj`. All writes went to a duplicate sequence (`E2E copy`), which the run deleted at its end. The project was closed without saving. Command: `PREMIERE_LIVE_E2E=1 python -m pytest cli_anything/premiere/tests/test_live_e2e.py -q -s`. Result: 24 passed in 48 s. No recovery marker. The fixture sequence was unchanged at the end (ProcAmp Brightness 7.7 before and after).

What the run showed:

| Check | Result |
|---|---|
| `live run --write --verify` through the command | The write stored and the check script confirmed it. This is the first live write through the lease and signal code of the final review of Plan 1. |
| `live clip list` | Two video clips, node IDs of the copy differ from the fixture's, render order ProcAmp, ASC CDL, Lumetri Color. With `--detail`: interpreted frame rate 23.976, the linked clip's node ID, `Motion.Rotation` as keyframed. |
| `live effect get` | Lumetri Color: 130 properties in 0.4 s. Temperature, Saturation and Contrast occur more than once, so a write needs the index and the name. Motion Position reads as an array `[0.5, 0.5]`. |
| `live effect set`, one negative value | ProcAmp Brightness from 5 to -2.6: zero was written first, stored at the first attempt. |
| `live effect set --from PLAN.json` | 6 of 6 stored at the first attempt: ProcAmp Brightness -3.7, Lumetri Temperature -20, Tint -5, Exposure 0.4, Contrast 12, Blacks -8. |
| Refusals | A wrong name at an index, Lumetri index 7, an audio clip by node ID, and a wrong `--expect-old` were each refused before any write. None left a recovery marker. |
| `--dry-run` | The values were the same before and after. |
| The two reads agree | The read-back of a write and a later `live effect get` gave the same values (Tint 3.3, ProcAmp Contrast 97.5). |
| Control through the MCP tool | `set_effect_property` on the same clip: ProcAmp Brightness 5 then -2.6, Lumetri Temperature 5 then -40. All four verified. |

What this means for the negative-value rule: on Premiere 27.0.0 no fault ("Illegal Parameter type") was seen through the CLI's own script or through the MCP tool. Every fault on record is on Premiere 26.5.2. The sample on 27.0.0 is small (about 130 raw writes in the gate run, 5 here, 4 through the tool here and 5 by the film session). The recipe keeps the rule of the design: zero first when a value crosses zero, a second attempt, then a near value; when nothing lands, the old value is put back.

Measured for the next recipes:

- `export_frame` (MCP tool) exports the active sequence only. With `expected_project_path` and `expected_cep_target` it took 0.85 s for one PNG, and a file name with a space worked.
- `app.getEnableProxies()` returns the number 1 or 0. `app.setEnableProxies()` from a script switches proxy display for the whole app and reads back at once.
- `app.project.activeSequence = seq` from a script switches the active sequence, and a later state read agrees. The MCP tool `duplicate_sequence` makes the new copy active.
- A fresh Lumetri Color reads the single character U+FEFE at index 4 (an empty LUT slot). A slot that holds a LUT reads the full path with a trailing slash, while index 6 reads 0.

Not shown by this run:

- The name of a transition in `live clip list --detail` (the fixture has no transition).
- A failed negative write on the real app, so the near-value step and the put-back have run only in unit tests.
- A plan long enough to pass the 15 s lease limit (the breath between writes has run only in unit tests).

### Round 2, 2026-11-18: frame export, the app switches, lut set

Same setup as round 1. Command: `PREMIERE_LIVE_E2E=1 python -m pytest cli_anything/premiere/tests/test_live_e2e.py -q -s`. Result: 39 passed in 117 s (the 24 tests of round 1 and 15 new ones). No recovery marker. At the end proxy display was on as before, the fixture sequence was active and unchanged, and the project was closed without saving.

A first run of this round had 2 failures. Both came from a wrong number in the tests (a size floor of 10 kB per frame). The second clip of the fixture renders black, and a black frame of 1280x720 is about 8 kB. The export was right. The tests now use the floor only for the first clip.

What the run showed:

| Check | Result |
|---|---|
| `live frame export`, three times in one call | 3 PNG files of 1280x720 in about 3 s. Proxy display was on, went off for the export, and was on again after it. The active sequence was the same before and after. |
| The frame is of the named sequence | With the fixture sequence active, an export of the copy (ProcAmp Brightness -60) and an export of the fixture sequence at the same time differ by a mean of 80.8 of 255 per channel. |
| File names | An output folder under `/tmp` and a name with a space, a quote and a non-ASCII letter work. The path in the tool's reply is the requested path. |
| An existing file | Refused before anything is sent. With `--overwrite` the file is replaced (new inode and time stamp). |
| A time at the end of the sequence | 8.008 s is refused before any switch. 7.9 s exports. |
| `live app proxies off`, `on` and `live app activate` | Each stored, and a separate read agreed. |
| Noise floor of the picture check | Two exports of the same frame are identical: mean difference 0.0000. |
| `live lut set --prove`, the same LUT again | The slot already held `identity.cube`. `stored` true, `rendered` null with the reason, exit status 0. |
| `live lut set --prove`, a LUT that halves every channel | `stored` true, `rendered` true, mean difference 57.0. Index 6 read 1 after the write. A frame exported later equals the frame taken right after the write (0.0000), so the LUT was loaded when the write returned. |
| A LUT path with non-ASCII letters | Index 4 read back the identical text. |
| Refusals of `lut set` | A time at the clip end (6.006 s) and a clip without Lumetri Color were refused before any write. No recovery marker. |
| Writes to a sequence that is not active | `live effect set` and `live lut set` wrote to the copy while the fixture sequence was active. Targets are found by sequence ID. |
| Control through the MCP tool | ProcAmp Brightness -2.6 and Lumetri Temperature -40 verified again. |

Not shown by this run:

- A failed export on the real app: the path that leaves proxy display or the active sequence switched and prints the two commands that put them back has run only in unit tests.
- A stop by Ctrl-C on the real app during an export or a put-back. Every stop point has a unit test with the real bridge code and a fake panel.
- A change by another person during a `--prove` check.
- A LUT in a converted project (where index 6 can read 0 while the LUT renders).
- A clip with two Lumetri Color effects.

## Live checks of Plan 3

Plan 3 added `live keys get`, `live keys set`, `live place` and `live effect apply`. Every check ran on Premiere Pro 27.0.0 (build 88), panel 1.25.0, `premiere-pro-mcp` 1.25.0, on 2026-11-19. The measures came first, because they decide how the recipes write. The two live rounds came after the code was built. No run saved a project.

### Key writing measures

Setup: the test project `fixture27 Claude.prproj`, with the writes on a duplicate sequence (`M2 measures`) that the run deleted at its end, and a ditto copy of the v10 edit project of the film session (the copy's name holds the word Claude). Only the copy was open. Both projects were closed without saving. The SHA-1 of the v10 original is the same before and after, and the original was never opened. The raw replies are in the measures folder of the plan (local files, not in the repo).

| Measure | Question | Result |
|---|---|---|
| mx | Does `isTimeVarying()` throw on a property that cannot be keyframed? | No call threw on Opacity Blend Mode (both), Motion Uniform Scale and Motion Rotation. `areKeyframesSupported` was true on all four and `isTimeVarying` false on all four. |
| M1 | Does `setTimeVarying(true)` add a key by itself? | No. The count was 0 before and after. |
| M2 | What does `addKey` do at a time that already holds a key? | It gives one key. `setValueAtKey` updates it (1, then 2). |
| M3a | What does `removeKey` on every key leave? | 0 keys. `isTimeVarying` stays true and `getValue` reads 0. |
| M3b | What does `setTimeVarying(false)` do with keys present? | It clears every key. `setTimeVarying(true)` after it adds none. So the write script clears the old keys with false, then turns keys on and adds the new ones. |
| M4 | Are negative Rotation keys exact? | They are stored as 32-bit floats: -2.6 reads -2.59999990463257 and -0.07 reads -0.07000000029802, within 0.0001. A static `setValue(-2.6, true)` reads -2.59999990463257. |
| route | Does the CLI's own write script store keys exactly on the fixture clip? | Yes. Keys at 0 s (1.26) and 1.247 s (-0.07), and a key planned at the clip end (6.006 s, value 0.33) moved to 5.964292 s, media ticks 1515025512000. Read back: exact ticks, values within 0.0001, and `live keys get` agrees. |
| M5 | Does the script write the whole v10 level plan? | On the copy, 24 of 24 shots were mapped to clips (23 by the strict rule, one by item and in point, because that shot was trimmed after leveling and is left out of the write). 23 written: 23 done, 0 not exact, 0 failed. Against the plan: pass. Against the keys of v10 itself: no shared key at another tick, and the count differs on exactly the 6 shots where v10 lacks one planned key. The static values of 8 shots differ by 0.01 to 0.02 degrees, because v10 typed them rounded. The fallback (keys through the MCP tool `add_keyframe`) was not needed. |
| M6 | Do keys land right on a clip interpreted to another frame rate (slow motion)? | Yes. The interpreted item reads a frame rate of 23.9760000013456, a native one 23.976023976024, and the project metadata reads "23.98 fps" against "23.976 fps". These were the only differences found, and the CLI tells an interpreted item by the frame rate (a difference above 0.000001 from the sequence rate). The own script and the MCP tool `add_keyframe` wrote keys at the same media ticks (762810048000 and 1270842048000 for 0 s and 2.0 s). Exported frames at 0.5, 1.0 and 2.0 s showed a small tilt, then about 10 degrees at 2.0 s, as expected. Slow motion is proven, so `live keys set` writes such clips and warns. |
| mspeed | On which time base do keys sit on a retimed clip? | Not run. `premiere-pro-mcp` 1.25.0 has no tool that sets the speed (`speed_change` always refuses). The measure is open. `live keys set` refuses retimed clips until it is made. |

One read after the M6 write timed out once with "a dialog may be open". `doctor` was clean, no recovery marker existed, no dialog was seen, and the retry passed.

### Placing measures

Setup: the same fixture, with the duplicate sequence `M2 measures` made active (the run deleted it at its end). The project was closed without saving, and the fixture file was unchanged.

| Measure | Question | Result |
|---|---|---|
| Snapping | Where do the marks and the start land? | `set_item_in_out` floors a mark to the item's frame grid (in 1.0 s gave 0.959292 s, 23 frames; out 3.0 s gave 2.961292 s). `overwrite_clip` rounds the start to the sequence frame (9.0 s gave 9.009, 12.0 gave 12.012, 20.0 gave 20.02). The recipe compares to the frame. |
| M7 | Can a placed clip be found by node ID between two MCP sessions? | Yes. After `set_item_in_out` and `overwrite_clip` onto V2 the new clip had a node ID that was unique in the sequence and the same on two reads, and `live effect get --node-id` found it twice. Placing a second clip changed no existing node ID. A node ID holds for one open of a project only (the V1 clips were `000f4248` and `000f4249` in one copy and `000f4249` and `000f424a` in another), so never reuse a node ID from an earlier open. |
| M8 | Can the picture of a shot be removed and the sound kept? | Yes. `overwrite_clip` with `track_index` 2 and `audio_track_index` 1 put the picture on V3 and the sound on A2, linked, with the same start and the same in and out ticks. `remove_from_timeline` with `include_linked` false and `ripple` false removed the picture and kept the sound at the same start, in and out. The sound's `linked` list was empty after. `add_track` worked through its QE fallback (verified). |
| M9 | How is a still told from a video item, and how long does it come in? | A still reads `isSequence` false and a frame rate of 29.97 (the default for stills; video items read their real rate). Its audio out point reads 0, and a video-only clip reads 0 there too, so no single read tells a still for sure. The recipe uses the media file extension (png, jpg, jpeg, tif, tiff, psd, gif, bmp, exr). `set_clip_duration` says `isStillImage` true in its own reply. A still placed with marks 0 to 4.96 s came in 4.9633 s long. `set_clip_duration` to 6.0 s gave 6.006 s (one frame more), kept the start and verified. A still's media in point reads about 3600 s after that change, so compare the length, not the in point. |

### Round 1, 2026-11-19: live tests on the fixture

Project `fixture27 Claude.prproj`. All writes went to a duplicate sequence (`E2E copy`), which the run deleted at its end. The project was closed without saving. Command: `PREMIERE_LIVE_E2E=1 python -m pytest cli_anything/premiere/tests/test_live_e2e.py -q -s`. Result: 50 passed in 191 s (the 39 tests of Plan 2 and 11 new ones). No recovery marker. At the end the fixture sequence was the only one left, the project file was unchanged and `doctor` was clean.

| Check | Result |
|---|---|
| `live keys get` | The fixture clip reads two Rotation keys (1.5 at 0 s, 0.5 at 2.002 s). The same clip in the copy reads the same keys. |
| `live keys set`, keys | The two keys were replaced by three: 1.26 at 0 s, -0.07 at 1.247 s and 0.33 at 5.964292 s. The key planned at the clip end moved to one frame before it. Read back by `live keys set` and by a later `live keys get`: exact ticks and the same values. Scale read 101.5. |
| `live keys set`, static | One static Rotation (-1.49) replaced the keys. A second run wrote the same again and read the same. |
| `clip_changed` | A plan that says the clip starts at 0.5 s, when it shows 0.0 s to 6.006 s, was refused with `clip_changed`. Nothing was written. |
| `live place` | Two shots on a spare video track, placed in 3.4 s, each read back to the frame (start) and to the item frame (in and out). The output listed the item marks (in 2.961292 s, out 4.963292 s for the second shot). |
| `live place` re-run | A shot already in place: `skipped`, `already_placed`, no call sent, exit 0. |
| `live place`, sound only | `audio_only`: `set_item_in_out`, `overwrite_clip` and `remove_from_timeline` ran. The sound stayed (start 21.688333 s, in 1.001 s, out 3.003 s) and the picture was gone. |
| `live effect apply` | ProcAmp then Lumetri Color on a V1 clip: both `done` in 3.0 s, at effect index 3 and 2. The render order ended with ProcAmp, Lumetri Color. A re-run skipped both (`has_effect`). |
| Effect names | The requested names equal the display names Premiere reports (`ProcAmp`, `Lumetri Color`). The match names are `AE.ADBE ProcAmp` and `AE.ADBE Lumetri`. The effect list holds 152 effects, and Gyroflow is listed as `Gyroflow`. |
| Three components | Gyroflow, ProcAmp and Lumetri Color applied in that order to one clip (9, 10 and 11 components before each). Each one landed at component index 2 and each one renders last. The record said `done` and the effect index 2 each time. No Gyroflow dialog opened. |
| A still | A PNG imported as an item. `set_item_in_out` accepted an out mark of 6.0 s (applied 5.9726 s on the item's frame grid, verified). `live place` placed it 6.006 s long (144 frames) with the two calls `set_item_in_out` and `overwrite_clip`. The extend step was not needed here. |
| Control through the MCP tool | `set_effect_property` on the same clip: ProcAmp Brightness 5 then -2.6, Lumetri Temperature 5 then -40. All four verified. |

Residue of the live tests: the still test imports a PNG as a project item, and the place tests change the item marks of `testclip.mp4` and of that PNG. Nothing removes the PNG item or restores the marks, and the end check of the tests does not compare project items. They stay only in the unsaved project, which is closed without saving. A second run in the same Premiere session adds a second PNG item.

### Round 2, 2026-11-19: rehearsal on a copy of the v10 project

The copy was made fresh with `ditto`. Its name holds the word Claude. The SHA-1 of the file is the same before and after the run, and the original was never opened. Only the copy was open. A new empty sequence (`M2 rehearsal Claude`) was made from one clip, the seed clip and its sound were removed, and the sequence had 5 video and 8 audio tracks. The item IDs of the plan matched the copy's items by their camera files (S01 to S25). The copy was closed without saving, `doctor` was clean and no recovery marker existed.

| Command | Dry run | Run | Re-run |
|---|---|---|---|
| `live place` (25 shots) | 25 planned, 50 calls, 0 refusals | 25 done in 12.2 s, exit 0, `stored` true | 25 skipped (`already_placed`), 0 calls, exit 0, 1.0 s |
| `live keys set` (23 entries) | 0.6 s | 23 done in 9.8 s | 23 done in 9.8 s, exit 0: it writes the same keys again |
| `live effect apply` (V1, `--all`, ProcAmp then Lumetri Color) | not run | 50 done in 11.9 s | 50 skipped (`has_effect`) in 0.6 s, exit 0 |

The shot S25 is not in the 23 entries of `live keys set`: it was trimmed after leveling, so its plan no longer fits the clip. The re-run of `live keys set` is a rewrite, not a no-write run (see "Not done, and why").

Not shown by these runs:

- A stop by Ctrl-C during `live place` or `live effect apply`, and a failed put-back of the active sequence (`not_restored`). Neither ran on the real app.
- `live place --overwrite` cutting clips, and a shot that covers the head transition of the first V1 clip. Neither ran on the real app.
- The extend step for a still that comes in shorter than its shot. The placing measures ran `set_clip_duration` by hand, but the live still of round 1 needed no extend call.
- A retimed clip (see "Not done, and why").
- A run that holds the lease past the limit of 15 s (`MAX_HOLD_S`), so the breath between writes was not seen for these commands. The run that holds the lease longest is `live keys set` (23 entries in 9.8 s). `live place` took 12.2 s, but it does not hold the lease throughout.
- A picture check of an applied effect. `live effect apply` checks the effect list that it reads back, and its `rendered` is always null.
