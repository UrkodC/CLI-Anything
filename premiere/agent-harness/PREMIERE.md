# Premiere Pro: analysis for the CLI harness

Checked on macOS on 2026-11-18 with Adobe Premiere Pro 27.0.0 (build 88).

## Backend: what can be driven

- **No source code.** Premiere Pro is closed software.
- **No headless mode.** The app has no command line for project work and no batch renderer
  of its own (rendering goes through Adobe Media Encoder).
- **Scripting exists, but only inside the running app.** ExtendScript (CEP panels) and UXP
  plugins run inside Premiere. They need the GUI open and a panel loaded. Other projects
  build MCP servers on that route. This harness does not use it.
- **The project file is open to reading.** A `.prproj` file is gzip-compressed XML.

Result: this harness reads the native project file directly. It does not start Premiere, and it
has no render or export command, because Premiere offers no way to render without its GUI.
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

`status`, `project info`, `project media`, `sequence list`, `sequence clips`, `clip effects`,
`colormode list`, `repl`. All read-only, all with `--json`.

## Not done, and why

- No write commands. An undocumented format and a live editor make writes risky. A later version
  can add them on copies only, with the app closed.
- No preview or export. Premiere cannot render without its GUI.
- Color Mode detection check and decoding. Both need one small project with an Adjust operation and a mask.

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
