# Tests for cli-anything-premiere

## Plan

| File | Needs | What it checks |
|---|---|---|
| `test_core.py` | nothing | The reader and the CLI on small hand-written project XML (`_helpers.py`). No real project. |
| `test_full_e2e.py` | a real project saved by Premiere Pro 27.0.0 | The reader against values read back from Premiere itself, and the installed command as a subprocess. |

Unit tests (24):
- loading: gzip and plain XML; missing file; broken gzip; wrong root; not XML; an entity bomb is refused;
- project: format version, counts, warning on an unknown format, name plus warning on a smoke-read format;
- sequence: size, frame rate, length, tracks; choosing one of two sequences by name or id;
- clips: timeline and source times, media paths, an untrimmed clip with no in and out;
- effects: file order, render position, bypass, intrinsic; point and boolean values; keyframes;
  the Lumetri LUT path (UTF-16 in a binary parameter); a repeated binary value resolved by its
  `BinaryHash`; duplicate names resolved by index; binary stays opaque; the Lumetri summary gives
  no value when a parameter is not where it is expected;
- Color Mode: the match name on a clip is detected and counted; markers that sit on no clip are
  reported as "found but not linked"; the "none" message says detection was never checked on a real grade;
- CLI: every command with `--json`, human tables, error codes and exit status 1;
- safety: after every command the file has the same SHA-256 and time stamp, and nothing new sits next to it.

End-to-end tests (14), on the real fixture:
- the format is 46 and gives no warning;
- sequence id, size, frame ticks and length equal Premiere's readback;
- clip start, end, in and out equal Premiere's readback;
- the render order equals the order in which the effects were applied by script;
- parameter values AND parameter indices equal the script API's (ProcAmp Brightness, ASC CDL Red and
  Blue Offset, Lumetri Temperature and Tint, VR De-Noise Noise Level, Color And Contrast Exposure);
- Rotation keyframe ticks, times and values equal Premiere's readback;
- the Lumetri LUT file name is read from the project; the media file exists on disk;
- subprocess: `--help`, `--version`, `project info`, `sequence clips`, `clip effects` (human and JSON),
  an error with exit status 1, and the project is unchanged after every command.

## The real fixture

It is local and untracked (`tests/fixtures_local/`, ignored by `.gitignore`), because a project file
stores absolute paths of the machine that made it. The tests skip when it is missing.
Set `PREMIERE_FIXTURE` to use a project at another place.

How it was made (2026-11-18, Premiere Pro 27.0.0, through a scripting bridge; any way to do the same steps works):
1. `ffmpeg -f lavfi -i "testsrc2=size=1280x720:rate=24000/1001" -f lavfi -i "sine=frequency=440:sample_rate=48000" -t 6.006 -c:v libx264 -pix_fmt yuv420p -c:a aac testclip.mp4`
   and a 2-point identity LUT `identity.cube`.
2. New project `fixture27.prproj`, import `testclip.mp4`, make the sequence "Fixture seq" from it.
3. Place frames 24 to 71 of the clip again at 6.006 s on V1 (second clip).
4. Clip 0, applied in this order: ProcAmp (Brightness 7.7), ASC CDL (Red Offset -0.0245, Blue Offset 0.0522),
   Lumetri Color (Input LUT file = `identity.cube`, Temperature -46.5, Tint 8.8). Motion Rotation keys: 1.5 at 0 s, 0.5 at 2.002 s.
5. Clip 1, applied in this order: VR De-Noise (Noise Level 0.02), Color And Contrast (Exposure 0.5), Mask.
6. Save. Read the values back from Premiere and write them to `fixture27.expected.json`
   (sequence id, clip times, effect values with their indices, key times).

The fixture has no Color Mode grade: Premiere's scripts cannot make one. Color Mode detection is
covered by the unit tests only, on hand-written XML.

## Results

2026-11-18, macOS, Python 3.14.7, pytest 9.2.0, Premiere Pro 27.0.0 fixture present,
`CLI_ANYTHING_FORCE_INSTALLED=1` (the installed `cli-anything-premiere` command was used).

```
cli_anything/premiere/tests/test_core.py::test_loads_gzip_and_plain_xml PASSED
cli_anything/premiere/tests/test_core.py::test_missing_file_is_a_clear_error PASSED
cli_anything/premiere/tests/test_core.py::test_garbage_and_wrong_root_are_refused PASSED
cli_anything/premiere/tests/test_core.py::test_entity_bomb_is_refused PASSED
cli_anything/premiere/tests/test_core.py::test_info_reports_version_and_counts PASSED
cli_anything/premiere/tests/test_core.py::test_unknown_format_version_warns PASSED
cli_anything/premiere/tests/test_core.py::test_smoke_read_format_is_named_and_still_warns PASSED
cli_anything/premiere/tests/test_core.py::test_sequence_size_rate_and_length PASSED
cli_anything/premiere/tests/test_core.py::test_sequence_must_be_named_when_there_are_two PASSED
cli_anything/premiere/tests/test_core.py::test_clip_times_and_media PASSED
cli_anything/premiere/tests/test_core.py::test_effect_order_values_and_render_position PASSED
cli_anything/premiere/tests/test_core.py::test_point_bool_and_keyframes PASSED
cli_anything/premiere/tests/test_core.py::test_lumetri_lut_path_and_duplicate_names_by_index PASSED
cli_anything/premiere/tests/test_core.py::test_color_mode_is_detected_and_not_decoded PASSED
cli_anything/premiere/tests/test_core.py::test_repeated_binary_value_is_resolved_by_its_hash PASSED
cli_anything/premiere/tests/test_core.py::test_lumetri_summary_gives_nothing_when_the_layout_moved PASSED
cli_anything/premiere/tests/test_core.py::test_color_mode_note_never_says_none_as_a_fact PASSED
cli_anything/premiere/tests/test_core.py::test_cli_project_info_json PASSED
cli_anything/premiere/tests/test_core.py::test_cli_sequence_list_and_clips PASSED
cli_anything/premiere/tests/test_core.py::test_cli_clip_effects_render_order_and_filter PASSED
cli_anything/premiere/tests/test_core.py::test_cli_errors_are_json_and_exit_1 PASSED
cli_anything/premiere/tests/test_core.py::test_cli_colormode_list_and_media PASSED
cli_anything/premiere/tests/test_core.py::test_status_reports_read_only PASSED
cli_anything/premiere/tests/test_core.py::test_no_command_ever_changes_the_file PASSED
cli_anything/premiere/tests/test_full_e2e.py::TestReaderAgainstPremiere::test_format_is_the_tested_one PASSED
cli_anything/premiere/tests/test_full_e2e.py::TestReaderAgainstPremiere::test_sequence_matches_premiere PASSED
cli_anything/premiere/tests/test_full_e2e.py::TestReaderAgainstPremiere::test_clip_times_match_premiere PASSED
cli_anything/premiere/tests/test_full_e2e.py::TestReaderAgainstPremiere::test_render_order_is_the_order_the_effects_were_applied PASSED
cli_anything/premiere/tests/test_full_e2e.py::TestReaderAgainstPremiere::test_parameter_values_and_indices_match_premiere PASSED
cli_anything/premiere/tests/test_full_e2e.py::TestReaderAgainstPremiere::test_keyframes_match_premiere PASSED
cli_anything/premiere/tests/test_full_e2e.py::TestReaderAgainstPremiere::test_lumetri_lut_file_is_read_from_the_project PASSED
cli_anything/premiere/tests/test_full_e2e.py::TestReaderAgainstPremiere::test_media_exists_on_disk PASSED
cli_anything/premiere/tests/test_full_e2e.py::TestCLISubprocess::test_help_and_version PASSED
cli_anything/premiere/tests/test_full_e2e.py::TestCLISubprocess::test_project_info_json PASSED
cli_anything/premiere/tests/test_full_e2e.py::TestCLISubprocess::test_sequence_clips_json PASSED
cli_anything/premiere/tests/test_full_e2e.py::TestCLISubprocess::test_clip_effects_human_and_json PASSED
cli_anything/premiere/tests/test_full_e2e.py::TestCLISubprocess::test_errors_exit_1_with_json PASSED
cli_anything/premiere/tests/test_full_e2e.py::TestCLISubprocess::test_reading_never_changes_the_project PASSED
============================== 38 passed in 0.67s ==============================
```

Sweep on real projects (not part of the suite, run by hand): every command, with `--json`, on 110
project files of this machine (auto-save copies left out), `clip effects --all` on each of 2,824
video clips. Formats 38 (2 files), 40 (3), 41 (3), 42 (62), 45 (37), 46 (3). 569 sequences,
2,328 audio clips, 8,305 effects, 1,867 Lumetri effects. Result: no error and no traceback.
The sweep proves "reads with no error". It does not prove the values, except on format 46.

Spot check on format 45 (Premiere 26.5.2): four finished films. The LUT file the reader reports
for each film is the LUT the editing notes name for it. The first run of this check found a fault
(the LUT path was missing on real films, see `BinaryHash` in `PREMIERE.md`). The fault is fixed
and has a unit test.

The interactive shell was started once by hand with piped input (`help`, `status`, `project info`, `exit`) and worked.

## Not covered

- A real Color Mode grade (no sample project yet). Detection is unverified.
- Values on formats other than 46 (only read without error, plus the LUT spot check on 45).
- Audio effects, transitions, nested sequences, multicam, adjustment layers, titles.
- Very large projects (speed and memory).

## Live unit tests

These test the live commands with no Premiere. Each file needs nothing but Python, and `node`
for the script tests (they skip without it).

| File | Tests | What it checks |
|---|---|---|
| `test_bridge.py` | 52 | The bridge client: folder checks, lease, heartbeat, command and reply, timeouts, recovery marker, signals. |
| `test_guard.py` | 99 | The version checks, the project guard, two projects, the recovery check. |
| `test_jsx.py` | 69 | The script builder and the helper and state scripts, run in node with a stub `app`. |
| `test_session.py` | 21 | The write session: one lease, the checks before a write, the breath between writes. |
| `test_live_cli.py` | 53 | `live state`, `live run`, the lazy command groups, the help texts, the output of any text. |
| `test_mcp_client.py` | 74 | The MCP client and `mcp list`, `describe`, `call`, `batch`. |
| `test_ui.py` | 53 | `ui mode get` and `set`. No test runs the real osascript, ps or sleep. |
| `test_doctor.py` | 54 | `doctor` (its thirteen checks), `recovery show`, the `live` key of `status`. |
| `test_recipes_clips.py` | 87 | `live clip list`. |
| `test_recipes_effects.py` | 255 | `live effect get` and `live effect set`: refusals, plans, negative values, put-back. |
| `test_recipes_frames.py` | 356 | `live frame export` and `live app`: switches, put-back, failed exports, stops. |
| `test_recipes_lut.py` | 153 | `live lut set` and `--prove`. |

What the fakes cover (`fakes.py`, unless named):
- `FakePanel`: a thread that plays the MCP Bridge panel in a private temp bridge folder. It writes
  the heartbeat, picks up each command, and answers through a handler. It can be busy or never
  answer. The live commands run their real bridge, lease and signal code against it.
- A fake `premiere-pro-mcp` server on stdio, with a few tools (`ping`, `echo`, `fail`, `env`, a
  lease probe, and `export_frame`, which writes a small file and can fail on request).
- `EffectModel` and `LutModel`, and `AppModel` in `test_recipes_frames.py`: small Premieres in
  Python that answer the recipe scripts (clips, effects, proxy display, the active sequence).
- The picture check of `--prove` uses a stub. Only `test_frame_pixels_with_real_ffmpeg` needs
  ffmpeg, and it skips without it.
- Most files have an autouse fixture that points `PREMIERE_TEMP_DIR` at a missing folder, so a
  test that misses its fake does not reach the real bridge.

The fakes do not show how Premiere itself behaves. That is what the end-to-end tests are for.

Last run of the whole unit suite (2026-11-19, macOS, Python 3.14.7, pytest 9.2.0):

```
1364 passed, 1 skipped in 171.72s (0:02:51)
```

The one skip is `test_live_e2e.py`, which needs the variable below.

## Live end-to-end tests

`test_live_e2e.py` runs the installed command on the RUNNING Premiere. It runs only when all of
this is true:
- the variable `PREMIERE_LIVE_E2E` is 1;
- the MCP Bridge panel has a fresh heartbeat;
- `live state` shows exactly one open project, named `fixture27 Claude.prproj`.

Otherwise the module is skipped. When the variable is 1 and the heartbeat is fresh but
`live state` fails, the module fails at collection: that is a broken setup.

```bash
PREMIERE_LIVE_E2E=1 python -m pytest cli_anything/premiere/tests/test_live_e2e.py -q -s
```

The write tests work on a duplicate of "Fixture seq" named "E2E copy". The run deletes it at the
end, makes "Fixture seq" active again and puts proxy display back. No test saves the project.
Close the project without saving after the run.

Last real results (Premiere Pro 27.0.0 build 88, panel 1.25.0, `premiere-pro-mcp` 1.25.0; the
details are in `PREMIERE.md`, "Live checks of Plan 2"):
- Round 1, 2026-11-18 (write path, `live clip list`, `live effect get`, `live effect set`):
  24 passed in 48 s. No recovery marker.
- Round 2, 2026-11-18 (`live frame export`, `live app`, `live lut set`): 39 passed in 117 s
  (the 24 tests of round 1 and 15 new ones). No recovery marker. A first run of this round had 2
  failures from a wrong size floor in the tests, not from the CLI.

Not shown on the real app (they ran in unit tests only): a failed negative write, so the near
value and the put-back; a plan long enough to pass the 15 s lease limit; a failed export that
leaves proxy display or the active sequence switched; Ctrl-C during an export or a put-back; a
LUT in a converted project; a clip with two Lumetri Color effects.

The five gate tests that came before the recipes are in `PREMIERE.md`, "Gate tests, 2026-11-18".
