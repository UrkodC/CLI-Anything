# Premiere CLI, Plan 2: the five recipes and the documents

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the five live recipes of milestone 1 (`live clip list`, `live effect get`, `live effect set`, `live frame export`, `live lut set`) on the frame of Plan 1, and change the documents from "reads only" to "the file commands read only; the live commands change the running app".

**Architecture:** Every write goes through one write session (`core/session.py`): it takes the lease, runs the guards, and is the only door to `send(write=True)`. Every recipe script finds its target by sequence ID and checks the expected project and sequence inside the script, before its first host call. A recipe uses its own script wherever one script can do the step and the read-back, because a failed own script leaves no recovery marker and a failed MCP tool does. Only the frame export itself goes through the MCP server.

**Tech Stack:** Python 3 (click), ExtendScript ES3 through the file bridge, `premiere-pro-mcp` 1.25.0 over stdio for `export_frame`, `ffmpeg` on PATH for `--prove` only, pytest with the fake panel and the fake MCP server.

**Spec:** `premiere/agent-harness/docs/2026-11-18-premiere-cli-design.md` (approved 2026-11-18). This plan covers section 7, Milestone 1, items 8 and 9, with sections 5.5 and 5.6. It also takes the recommendations of the final review of Plan 1.

**Facts for the builders (local, not in git):** `.superpowers/sdd/2026-11-18-premiere-cli-plan-2-recipes/facts.md` (frame API with file and line, how the server's code calls Premiere, sample replies) and `notes-76.md` (how the film-edit session uses these operations).

## Decisions for Urko before the build

**Decided by Urko on 2026-11-18: 1A, 2A, 3A.** The options are kept below as the record.

Each has two options. The plan is written for option A of each.

1. **Put-back after a failed export.** `live frame export` switches proxy display off and may switch the active sequence. Write rule 4b says a command puts back what it switched. Section 5.1 says no write may run while a recovery marker exists, and a failed export leaves a marker.
   - Option A (my pick): with a marker, the command does not write. It prints what is still switched and the two commands that put it back after the recovery.
   - Option B: the command puts both back even with a marker.
2. **What `--prove` of `live lut set` means.** The design says "exports the frame with and without the LUT and compares". That has no meaning when the slot already holds a LUT.
   - Option A (my pick): compare the frame before the write with the frame after it. `rendered: true` when they differ. `rendered: false` only when the slot was empty before and the frame did not change. In every other case `rendered: null` with the reason.
   - Option B: compare the Premiere frame with an `ffmpeg lut3d` render of the source clip (the film session's method). Stronger proof, but it needs the source time and the color settings of each camera.
3. **Git commits.** You approved one commit (252521d).
   - Option A (my pick): one commit per finished and reviewed task on `feat/premiere-cli`, no push.
   - Option B: no commit until you say so.

## Global Constraints

- All constraints of Plan 1 still hold: no push; the files of session `premier-pro-mcp-76` (`core/prproj.py`, `tests/test_core.py`, `tests/test_full_e2e.py`, `tests/_helpers.py`) are not edited; `premiere_cli.py` imports no new module at import time; a fault in new code must not break `status`, `project`, `sequence`, `clip`, `colormode`.
- Pinned strings stay: the root help contains "Read Premiere Pro project files"; the version is `0.1.0`; `status` keeps `"writes_projects": false` (it is about project files) and every other key.
- Output contract: `--json` on every command; success `{"ok": true, ...}`; failure `{"ok": false, "error": "...", "code": "..."}` and exit status 1; `CliError(code, message)`.
- Every write recipe reports `stored` and `rendered` (`true`, `false` or `null`). Exit status 1 when either is `false`.
- Every write recipe takes `--dry-run` (sends reads only, prints the resolved targets and the planned change), `--project PATH`, `--any-project`, `--allow-untested`, `--lease-wait`, `--timeout`.
- No write recipe reads the selection, the playhead or the active sequence to find its target. A write names its sequence with `--sequence ID`.
- No recipe calls an MCP tool that takes a node ID. Those tools search the active sequence only.
- Never call the MCP server while this process holds the lease.
- `send` is never called inside a `hold_signals()` section of the caller (the signal that `send` absorbs would be raised by the outer section).
- No recipe of this plan saves the project. `--save` comes with the batch recipes of milestone 2.
- The CLI never sets `unsafe-script` and never acknowledges a recovery by itself.
- ExtendScript is ES3: no `let`, `const`, arrow functions, template strings, `JSON.*`, `Object.keys`, `Array.prototype.forEach/map/filter/indexOf`, `String.prototype.trim`. Arguments enter a script through `jsx.build(body, args)` only. Times cross as ticks in strings.
- For every host call in a `.jsx` file, find the same call in the server's code (`facts.md` section 3 names the file and line) and follow it. No Premiere API call from memory.
- Tests never contact the real Premiere, never start the real `premiere-pro-mcp`, never run `osascript`. Live tests stay in `tests/test_live_e2e.py` behind `PREMIERE_LIVE_E2E=1`. The controller runs every live command, not a subagent. `tests/gates/run_gates.py` is not touched and not re-run.
- Python: no `multiprocessing`, `Pool`, `ProcessPoolExecutor`. Every wait has a limit.
- Prose in help texts, messages and documents: plain words, short sentences, no em-dashes.
- Test command: `uv run --no-project --with pytest --with click --with prompt-toolkit --with defusedxml --python /opt/homebrew/bin/python3 python -m pytest <args> -p no:cacheprovider`. Unit suite: `cli_anything/premiere/tests -q` (389 passed, 1 skipped at the start of this plan).

## Review Focus

1. A clip with two Lumetri Color effects. Expected: a write without `effect_index` refuses with `ambiguous_effect` and sends no write. Test in Task 5.
2. A plan file whose clip index no longer exists, because the timeline changed. Expected: the resolve step fails before any write; `ran` is 0. Test in Task 5.
3. Urko switches the project or the sequence name changes between the guard and the write. Expected: the script refuses with `precondition`, nothing is changed, no marker. Test in Task 2.
4. An output folder on a volume that is not mounted, or an output name with a space, a quote or a non-ASCII letter. Expected: the first gives `output_dir` before any contact with Premiere; the second exports and the file check passes. Tests in Task 7.
5. A long timeline. Expected: `live clip list` returns at most 400 clips, says `truncated: true` and gives the next `--offset`; it never runs into the 1 MB reply limit. Test in Task 3.

---

## File structure

| File | Responsibility |
|---|---|
| `cli_anything/premiere/core/guard.py` (modify) | Add the version check of the server package |
| `cli_anything/premiere/core/bridge.py` (modify) | Heartbeat state `waiting`; keep the lock when a marker cannot be written |
| `cli_anything/premiere/core/jsx.py` (modify) | `include=` for shared script files; line number in a script failure |
| `cli_anything/premiere/core/session.py` (new) | `guarded_state`, `WriteSession`: the only door for writes |
| `cli_anything/premiere/core/scripts/find.jsx` (new) | ES3 finders: expectation check, sequence by ID, clip, effect, property |
| `cli_anything/premiere/core/scripts/clip_list.jsx`, `effect_get.jsx`, `effect_resolve.jsx`, `effect_write.jsx`, `app_switch.jsx` (new) | One script per step |
| `cli_anything/premiere/core/recipes/__init__.py`, `clips.py`, `effects.py`, `frames.py`, `lut.py` (new) | Recipe logic, no click code |
| `cli_anything/premiere/live_recipes.py` (new) | click commands of the recipes, attached to the `live` group |
| `cli_anything/premiere/live_cli.py` (modify) | Import `live_recipes` at its end; use `core/session.py`; show the server version in `doctor` |
| `cli_anything/premiere/tests/fakes.py` (add only) | Fake MCP tool `export_frame`; a panel handler for recipe scripts |
| `cli_anything/premiere/tests/test_session.py`, `test_recipes_clips.py`, `test_recipes_effects.py`, `test_recipes_frames.py`, `test_recipes_lut.py` (new) | Unit tests |
| `cli_anything/premiere/tests/test_live_e2e.py` (modify) | Live tests of the write path and the recipes |
| `README.md`, `skills/SKILL.md`, `tests/TEST.md`, `PREMIERE.md`, `premiere_cli.py`, `setup.py`, `utils/premiere_backend.py` (modify) | Documents and help text |

One numbering is used everywhere for the effects of a clip: the index in `clip.components` (0 is the first component Premiere lists). It is called `effect_index`. Premiere lists applied effects in reverse render order; `render_order` is a derived field of `live clip list` only.

Target of a clip, in every recipe: `{"sequence_id": str, "track_type": "video" | "audio", "track": int, "clip": int}` or `{"sequence_id": str, "node_id": str}`. A node ID is looked up inside that sequence only.

---

### Task 1: Frame hardening (server version, `waiting`, line numbers)

**Files:**
- Modify: `core/guard.py`, `core/bridge.py`, `core/jsx.py`, `core/doctor.py`, `live_cli.py`
- Test: `tests/test_guard.py`, `tests/test_bridge.py`, `tests/test_jsx.py`, `tests/test_doctor.py`

**Interfaces:**
- Produces:

```python
# core/guard.py
TESTED_SERVER_VERSIONS = ("1.25.0",)
def server_version(which=shutil.which) -> str | None
    # realpath of the `premiere-pro-mcp` binary on PATH, then `package.json` two folders up; "version" of it.
    # None when the binary, the file or the key is missing, or the name in the file is not "premiere-pro-mcp".
def check_bridge(heartbeat, panel, *, write, allow_untested, server=UNSET) -> list[str]
    # `server` is the string from server_version() or None. A version outside TESTED_SERVER_VERSIONS (or None):
    # read warns, write raises CliError("untested_bridge") unless allow_untested. UNSET means: call server_version().

# core/bridge.py
Heartbeat.fresh          # True only for state "running" and age <= 3 s
Heartbeat.waiting        # True for state "waiting" and age <= 3 s
```

- `check_bridge` on a `waiting` heartbeat: `CliError("bridge_down", "The panel is open but its connector is stopped. Click Start Bridge in the MCP Bridge panel.")` for reads and writes.
- `jsx.build`: the catch line becomes `} catch (e) { return __fail(String(e) + (e && e.line ? " (line " + e.line + ")" : "")); }`.
- `jsx.build(body, args=None, include=())`: each name in `include` is loaded with `jsx.load(name)` and placed after the helpers and before `var ARGS`.
- `doctor`: the check `mcp_server` shows the version: pass "premiere-pro-mcp 1.25.0", warn for another version ("untested: read only") or when not found.

- [ ] **Step 1: Write the failing tests.**

```python
def test_server_version_reads_package_json(tmp_path):      # fake bin -> symlink -> dist/index.js, package.json two up -> "1.25.0"
def test_server_version_missing_parts_give_none(tmp_path): # no binary; no package.json; other package name
def test_untested_server_warns_a_read_and_refuses_a_write():  # server="1.26.0": read -> warning names it; write -> "untested_bridge"
def test_missing_server_refuses_a_write():                  # server=None, write -> "untested_bridge"; allow_untested -> passes with a warning
def test_waiting_heartbeat_is_bridge_down():                # state "waiting": read and write -> "bridge_down", message has "Start Bridge"
def test_waiting_heartbeat_is_not_fresh():                  # Heartbeat(..., state="waiting", age_s=0.1).fresh is False, .waiting is True
def test_script_failure_carries_the_line(node):             # body 'var a = 1;\nthrow new Error("x");' -> error ends with "(line N)" in node with a stub
def test_build_includes_named_scripts():                    # include=("find",) -> text of find.jsx sits before "var ARGS"
def test_doctor_shows_the_server_version(...):              # pass detail "premiere-pro-mcp 1.25.0"; "1.26.0" -> warn
```

- [ ] **Step 2: Run and see them fail.**
- [ ] **Step 3: Implement.** Every caller of `check_bridge` passes through unchanged (`UNSET` default). Existing tests that pin the build shape of `jsx.build` are updated to the new catch line.
- [ ] **Step 4: Run the unit suite.** Expected: all pass.
- [ ] **Step 5: Commit** (if decision 3 is option A): `feat(premiere): check the server package version, treat a stopped connector as down`.

### Task 2: The write session and the script finders

**Files:**
- Create: `core/session.py`, `core/scripts/find.jsx`, `tests/test_session.py`
- Modify: `core/bridge.py` (keep the lock when a marker write fails), `live_cli.py` (use `session.guarded_state`, keep the name `live_cli.guarded_state` as an alias)
- Test: `tests/test_session.py`, `tests/test_bridge.py`, `tests/test_jsx.py`

**Interfaces:**
- Consumes: `BridgeClient`, `guard.*`, `jsx.build`.
- Produces:

```python
# core/session.py
def guarded_state(client, *, write, project, any_project, allow_untested, hard_limit_s=120.0) -> tuple[dict, list[str]]
    # moved from live_cli.py, same behavior and order

class WriteSession:
    def __init__(self, client, *, project=None, any_project=False, allow_untested=False, hard_limit_s=120.0)
    state: dict            # the guarded state of the last guard run
    warnings: list[str]
    expect: dict           # {"project_path": <active project path>, "sequence_id": None, "sequence_name": None}
    def __enter__(self) -> "WriteSession"      # takes the lease, runs guarded_state(write=True), fills state and expect
    def __exit__(self, *exc) -> None           # gives the lease back
    def target(self, sequence_id: str, sequence_name: str | None = None) -> None   # sets expect.sequence_*
    def read(self, body: str, args: dict | None = None, *, include=("find",)) -> object
    def write(self, body: str, args: dict | None = None, *, include=("find",)) -> object
        # check_recovery(write=True), then send(write=True). args gets the key "expect" (a caller's own "expect" is refused).
    def breathe(self) -> bool                  # client.breathe(); when it returned True: run guarded_state(write=True) again
    def paused(self)                           # context manager: gives the lease back for MCP calls; on exit takes it
                                               # again and runs guarded_state(write=True) again
```

- A script failure whose message starts with `precondition:` is raised as `CliError("precondition", <message without the prefix>)` by `read` and `write`.
- `find.jsx` (ES3) provides: `__expect(ARGS.expect)` (throws `precondition: ...` when the active project path differs, or when `sequence_id` is given and no such sequence exists, or its name differs from `sequence_name`; returns the sequence or null); `__sequenceById(id)`; `__clip(seq, target)` (by `track_type`, `track`, `clip`, or by `node_id` inside `seq`; returns `{clip, track_type, track, index}`); `__effect(clip, name, effectIndex)` (matches display name or match name; with two matches and no `effectIndex` throws `precondition: ambiguous_effect ...`; with `effectIndex` checks the name at that index); `__property(component, index, name)` (throws `precondition: ...` when the display name at `index` is not exactly `name`); `__ticks(time)` (string). Every write body calls `__expect` first.
- `bridge.send`: when the marker of an unknown outcome cannot be written, the lease is kept (lock folder and owner record stay; `Lease.retained` is True and `release()` does nothing), and the error is `CliError("unknown_outcome", ...)` with the added sentence "The recovery marker could not be written, so the lock is kept. Delete <lock path> after you checked Premiere."

- [ ] **Step 1: Write the failing tests** (FakePanel handlers as in `tests/test_live_cli.py`):

```python
def test_enter_runs_the_guards_before_any_write(...):        # own project -> "project_guard"; panel saw only the state script
def test_write_refuses_on_a_marker(...):                     # marker put in place after enter -> "recovery_required"; no write seen
def test_write_adds_the_expectation(...):                    # the script text holds "project_path" and the target sequence id
def test_caller_cannot_pass_its_own_expect(...):             # args={"expect": ...} -> CliError("bad_args")
def test_precondition_failure_has_its_own_code(...):         # reply error "precondition: sequence name differs" -> code "precondition"; no marker
def test_breathe_guards_again(...):                          # MAX_HOLD_S patched to 0: breathe() True and a second state script seen;
                                                             # a marker that appeared meanwhile -> "recovery_required"
def test_paused_gives_the_lease_back_and_guards_again(...):  # inside paused(): no lock folder; after: lock held, second state script seen
def test_session_never_calls_send_inside_a_lock_section(...):# bridge._Held.depth is 0 at the moment send is entered (spy)
def test_marker_write_failure_keeps_the_lock(...):           # mark_recovery patched to raise OSError on an unknown outcome:
                                                             # code "unknown_outcome", lock folder and owner.json still there after the with block
# find.jsx in node with a stub app (as test_jsx.py does):
def test_expect_refuses_another_project(node): ...
def test_expect_refuses_a_renamed_sequence(node): ...
def test_clip_by_index_and_by_node_id(node): ...             # node id found only inside the given sequence
def test_effect_twice_needs_effect_index(node): ...          # two "Lumetri Color": no index -> "ambiguous_effect"; index 3 -> that one
def test_property_checks_the_name_at_the_index(node): ...    # index 14 named "Tint" in the stub, wanted "Temperature" -> precondition
def test_find_is_es3(): ...                                  # the denylist of test_jsx.py on find.jsx
```

- [ ] **Step 2: Run and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the unit suite.** Expected: all pass, the Plan 1 tests of `live state` and `live run` unchanged.
- [ ] **Step 5: Commit:** `feat(premiere): write session and script finders`.

### Task 3: `live clip list`

**Files:**
- Create: `core/recipes/__init__.py`, `core/recipes/clips.py`, `core/scripts/clip_list.jsx`, `live_recipes.py`, `tests/test_recipes_clips.py`
- Modify: `live_cli.py` (last line: `from . import live_recipes  # noqa: F401`), `tests/fakes.py` (helper `recipe_handler(replies: dict[str, object])`: picks the reply by a marker string that each recipe script holds in a first-line comment)

**Interfaces:**
- Produces:

```python
# core/recipes/clips.py
CLIP_LIMIT = 400
INTRINSIC = ("Opacity", "Motion", "Volume", "Channel Volume", "Panner")
def list_clips(client, *, sequence_id=None, track_type="both", track=None, offset=0, limit=CLIP_LIMIT,
               allow_untested=False, hard_limit_s=120.0) -> dict
```

- Command: `live clip list [--sequence ID] [--track-type video|audio|both] [--track N] [--offset N] [--limit N]`. A read: no project guard. Without `--sequence` it lists the active sequence and says so in `warnings`.
- Payload: `{"sequence": {"id", "name"}, "clips": [...], "count": n, "total": n, "truncated": bool, "next_offset": int | null, "warnings": [...]}`. One clip: `{"track_type", "track", "clip", "node_id", "name", "start_ticks", "end_ticks", "in_ticks", "out_ticks", "start_seconds", "end_seconds", "enabled", "speed", "adjustment_layer", "source": {"node_id", "name", "path"}, "effects": [names, component order], "render_order": [names], "procamp_before_lumetri": true | false | null}`.
- `render_order` is `effects` without the names in `INTRINSIC`, reversed. `procamp_before_lumetri` is `null` unless both "ProcAmp" and "Lumetri Color" are on the clip.
- Seconds are made in Python from the tick strings (254016000000 ticks per second), rounded to 6 places.
- Human output: one line per clip: `V1[0] 000f4243 0.000-6.006 testclip.mp4 | ProcAmp > ASC CDL > Lumetri Color`.
- `--detail` adds four fields per clip (session 76 asked for them; they cost more host calls, so they are off by default): `"interpreted_fps"` (number or null, from `projectItem.getFootageInterpretation().frameRate`, server file `tools/utility.js:626`), `"linked"` (node IDs from `clip.getLinkedItems()`, `tools/timeline.js:1418`), `"keyframed"` (a list of `"<effect>.<property>"` names where `prop.isTimeVarying()` is true, `tools/keyframes.js:98`), `"transitions"` (`{"head": name or null, "tail": name or null}`, from `track.transitions`, `tools/transitions.js:13-14`, matched by the clip's start and end ticks). Each host call sits in its own `try`; a call that throws gives `null` for that field.

- [ ] **Step 1: Write the failing tests.**

```python
def test_clip_list_shapes_one_clip(...):            # panel reply with ticks as strings -> seconds 6.006, render_order ["ProcAmp","ASC CDL","Lumetri Color"], procamp_before_lumetri True
def test_lumetri_before_procamp_is_flagged(...):    # effects [..., "ProcAmp", "Lumetri Color"] -> False
def test_clip_list_is_truncated_at_the_limit(...):  # script reply total 950, 400 clips -> truncated True, next_offset 400; the script got limit 400
def test_clip_list_filters_reach_the_script(...):   # --track-type video --track 1 --offset 400 are in ARGS
def test_clip_list_without_sequence_warns(...):     # warning names the active sequence
def test_clip_list_unknown_sequence(...):           # reply "precondition: no sequence has the ID x" -> code "precondition"
def test_clip_list_is_a_read(...):                  # own film open: works; send called with write False only
def test_clip_list_detail_fields(...):              # --detail reaches ARGS; reply fields interpreted_fps, linked, keyframed, transitions pass through; without --detail they are absent
def test_clip_list_detail_survives_a_throwing_call(node):   # stub getFootageInterpretation throws -> interpreted_fps null, the clip is still listed
def test_clip_list_script_is_es3_and_runs_in_node(node): ...   # stub app with 2 tracks; offset and limit honored; ticks are strings
```

- [ ] **Step 2: Run and see them fail.** **Step 3: Implement.** **Step 4: Run the unit suite.**
- [ ] **Step 5: Commit:** `feat(premiere): live clip list`.

### Task 4: `live effect get`

**Files:**
- Create: `core/recipes/effects.py`, `core/scripts/effect_get.jsx`, `tests/test_recipes_effects.py`
- Modify: `live_recipes.py`

**Interfaces:**
- Produces: `get_effects(client, target: dict, *, effect=None, effect_index=None, allow_untested=False, hard_limit_s=120.0) -> dict`.
- Command: `live effect get [--sequence ID] (--track N --clip N [--track-type video|audio] | --node-id ID) [--effect NAME] [--effect-index N]`. A read. Without `--sequence`: the active sequence, with a warning.
- Payload: `{"clip": {"sequence_id", "track_type", "track", "clip", "node_id", "name", "start_seconds"}, "effects": [{"effect_index", "name", "match_name", "properties": [{"index", "name", "value", "kind", "keyframed"}]}], "warnings": []}`.
- `kind` is `"number"`, `"string"`, `"boolean"`, `"color"` (value `[a, r, g, b]`), or `"unreadable"` (value `null`). The script reads a value as the server's `get_effect_properties` does (`facts.md` section 3).

- [ ] **Step 1: Write the failing tests.**

```python
def test_effect_get_lists_by_effect_index(...):      # two effects -> effect_index 3 and 4, properties with index, name, value, kind
def test_effect_get_filter_by_name_and_index(...):   # --effect "Lumetri Color" --effect-index 2 reach ARGS
def test_effect_get_blank_names_survive(...):        # name " " stays " " in JSON
def test_effect_get_needs_a_clip(...):               # neither --clip nor --node-id -> click usage error, nothing sent
def test_effect_get_script_reads_kinds_in_node(node):# stub: number, string, boolean, color, a getValue that throws -> "unreadable"
```

- [ ] **Step 2 to 4** as in Task 3. **Step 5: Commit:** `feat(premiere): live effect get`.

### Task 5: `live effect set`

**Files:**
- Create: `core/scripts/effect_resolve.jsx`, `core/scripts/effect_write.jsx`
- Modify: `core/recipes/effects.py`, `live_recipes.py`, `tests/test_recipes_effects.py`

**Interfaces:**
- Consumes: `WriteSession` (Task 2).
- Produces:

```python
VALUE_TOLERANCE = 0.001
REFUSED_LUMETRI_INDEXES = (7, 32, 34)
LUMETRI_MATCH = "AE.ADBE Lumetri"
def near_value(value: float) -> float      # value moved toward zero by max(abs(value) * 0.04, 0.001), rounded to 3 places
def same_value(a, b) -> bool               # numbers within VALUE_TOLERANCE; strings and booleans equal
def load_plan(path) -> dict                # CliError("bad_plan") with the place of the fault
def resolve_entries(session, entries: list[dict]) -> list[dict]   # one read; one resolved record per entry (Task 8 uses it)
def check_entries(entries, resolved) -> None                       # the refusals of step 3 below
def write_entry(session, entry: dict, resolved: dict) -> dict      # one result record; holds the negative rule (Task 8 uses it)
def set_effects(client, plan: dict, *, dry_run=False, project=None, any_project=False,
                allow_untested=False, hard_limit_s=120.0) -> dict
```

- Command: `live effect set --sequence ID [--sequence-name NAME] (--track N --clip N | --node-id ID) --effect NAME [--effect-index N] --index N --name NAME --value JSON [--expect-old JSON]`, or `live effect set --from PLAN.json`. Both take `--dry-run` and the guard options. A single write is a plan with one entry.
- Plan file: one JSON object `{"sequence_id": str, "sequence_name": str (optional), "writes": [{"track": int, "clip": int} or {"node_id": str}, plus "effect": str, "effect_index": int (optional), "index": int, "name": str, "value": number | string | boolean, "expect_old": (optional)}]}`. Video tracks only. Unknown keys, an empty `writes` list and a missing `name` are `bad_plan`.
- Order of work, all in one `WriteSession`:
  1. `session.target(sequence_id, sequence_name)`.
  2. Resolve: one read with `effect_resolve.jsx` for all entries. Per entry it returns the clip (`node_id`, `name`, `start_ticks`), the effect (`effect_index`, `name`, `match_name`), the property (`name` at `index`, current `value`, `kind`, `keyframed`).
  3. Refuse the whole plan, before any write, with `CliError("refused_parameter", ...)` naming the entry, when: the property is keyframed; the effect name contains "Gyroflow"; the effect is Lumetri and the index is in `REFUSED_LUMETRI_INDEXES`; the value type does not fit `kind` (a number for "number", a string for "string", a boolean for "boolean"; "color" and "unreadable" are refused); `expect_old` is given and `same_value(expect_old, value_now)` is false (code `precondition`).
  4. `--dry-run` stops here: `{"dry_run": true, "resolved": [...], "planned": [...]}`.
  5. Write: one `session.write` per attempt with `effect_write.jsx`, in plan order. Between entries `session.breathe()`; when it returns True, run the resolve again and stop with `CliError("target_moved", ...)` if a clip node ID changed.
- `effect_write.jsx` in one round trip: `__expect`, find the property, read `before`; when `before > 0` and the wanted value `< 0` ("crosses zero") write 0 first; write the value with `setValue(value, true)`; read back. It returns `{"before", "zero_first", "set_error", "value", "read_error"}`. A `setValue` that throws is caught into `set_error`.
- A write attempt has failed when `set_error` is set or `same_value(read back, wanted)` is false.
- Negative rule, for a number below zero only: attempt 1; on failure attempt 2 (the same write again); on a second failure one more write with `near_value(value)`. When the near value reads back, the entry has `"landed": <near value>` and `"stored": false`.
- Any other value: one attempt.
- The batch stops at the first entry that is not stored. Entries after it do not run.
- Payload: `{"sequence": {"id", "name"}, "resolved": [{"entry", "clip": {"node_id", "name", "start_seconds"}, "effect", "effect_index", "index", "name", "value_now"}], "results": [{"entry", "requested", "before", "value", "stored", "attempts", "zero_first", "landed", "error"}], "ran": n, "total": n, "stored": true | false, "rendered": null, "warnings": [...]}`. When `stored` is false: exit 1, code `not_stored`, the payload included.
- A write to Lumetri index 4 or 6 adds the warning "Read-back is not proof for this change."

- [ ] **Step 1: Write the failing tests** (a FakePanel that keeps a small effect model and answers the three scripts; make it reusable in `fakes.py`):

```python
def test_one_write_is_stored(...):                      # ProcAmp Brightness 7.7 -> 3.0: stored True, attempts 1, exit 0
def test_readback_uses_the_tolerance(...):              # wanted 7.7, read back 7.69999980926514 -> stored True
def test_zero_is_written_first_when_crossing(...):      # before 5, wanted -2.6 -> the write script got value -2.6; reply zero_first True is reported
def test_negative_fault_retries_then_lands_near(...):   # panel answers set_error "Illegal Parameter type" twice for -2.6, accepts -2.496:
                                                        # attempts 3, landed -2.496, stored False, exit 1, code "not_stored"
def test_negative_fault_that_never_lands(...):          # three failures -> stored False, landed None, error holds the text
def test_positive_failure_gets_one_attempt(...):        # read-back differs for 3.0 -> attempts 1, stored False
def test_batch_stops_at_the_first_failure(...):         # 3 entries, second fails -> ran 2, total 3, third never sent
def test_two_lumetri_need_effect_index(...):            # resolve reply "precondition: ambiguous_effect" -> code "precondition", no write sent
def test_missing_clip_index_fails_before_any_write(...):# plan entry clip 9 of 2 -> code "precondition", ran 0
def test_refused_parameters(...):                       # keyframed; "Gyroflow Stabilizer"; Lumetri 7, 32, 34; a string for a number -> "refused_parameter", no write
def test_expect_old_mismatch(...):                      # expect_old 1.0, value now 7.7 -> "precondition", no write
def test_dry_run_sends_reads_only(...):                 # sends recorded: every write flag False; payload has dry_run True and planned
def test_own_film_is_refused(...):                      # "project_guard"; only the state script seen
def test_plan_file_faults(...):                         # not JSON; a list; unknown key; empty writes; audio track -> "bad_plan", nothing sent
def test_lut_path_write_warns(...):                     # Lumetri index 4, a string -> stored True and the warning
def test_breathe_then_moved_target(...):                # MAX_HOLD_S 0, second resolve gives another node id -> "target_moved"
def test_near_value(...):                               # -2.6 -> -2.496; -40 -> -38.4; -0.077 -> -0.074; -0.002 -> -0.001
def test_effect_scripts_are_es3_and_run_in_node(node):  # write body on a stub: zero first, set, read back; a throwing setValue -> set_error
```

- [ ] **Step 2 to 4** as before. **Step 5: Commit:** `feat(premiere): live effect set with plan files`.

### Task 6: Live checkpoint 1 (the controller runs it)

This task touches the running Premiere. The controller messages sessions 76 and 78 before and after, opens only `tests/fixtures_local/fixture27 Claude.prproj`, and closes it without saving. A subagent writes the tests and runs nothing live.

**Files:**
- Modify: `tests/test_live_e2e.py`

**Interfaces:**
- Produces: a module fixture `work_copy` (scope module): with `mcp call`, duplicates `Fixture seq`, renames the copy to `E2E copy`, reads its ID from `mcp call list_sequences`, yields `{"id", "name"}`, and at the end deletes it by ID after reading its name again. It also keeps the ProcAmp Brightness of `Fixture seq` (read with `live effect get --sequence <fixture id>`) and asserts at the end that it did not change.

- [ ] **Step 1: Fix the parked test gaps** (M8.1 to M8.4): the script-error test checks that the thrown text is in `error`; an independent `ui mode get` follows the set; the `finally` keeps the first failure; with `PREMIERE_LIVE_E2E=1` a failing `live state` fails the module, it does not skip it.
- [ ] **Step 2: Write the live tests** (all on `E2E copy`):

```python
def test_live_run_write_verify_through_the_command(work_copy, tmp_path):   # a write script sets ProcAmp Brightness 4, a check script reads it: stored True
def test_clip_list_of_the_copy(work_copy):                 # 2 video clips on V1, node ids differ from the fixture's, render_order of clip 0 starts with "ProcAmp"
def test_effect_get_matches_the_known_values(work_copy):   # ProcAmp index 0 is "Brightness"; Lumetri index 14 is "Temperature"
def test_effect_set_one_negative_value(work_copy):         # Brightness -2.6: stored True; effect get reads -2.6
def test_effect_set_from_a_plan(work_copy, tmp_path):      # 6 writes (ProcAmp Brightness, Lumetri 14, 15, 19, 20, 24), two negative: ran 6, stored True
def test_effect_set_dry_run_changes_nothing(work_copy):    # values before and after are equal
def test_effect_set_wrong_name_is_refused(work_copy):      # index 14 with name "Tint" -> code "precondition"; no recovery marker
def test_mcp_tool_negative_control(work_copy):             # see below
```

- The control: make `E2E copy` active with `mcp call set_active_sequence`, confirm with `live state`, then `mcp call set_effect_property` on V1 clip 0 of the copy with ProcAmp Brightness -2.6 from +5, and Lumetri Temperature (index 14) -40 from +5. Record pass or fault. It is a record, not a gate: session 76 saw 5 of 5 negative writes pass through the tool on 27.0.0. If a write faults, the server leaves a recovery marker. Then the controller stops, runs `recovery show`, and acknowledges through the MCP tools only when the fixture copy is the one open project; else it goes to Urko.
- [ ] **Step 3: The controller runs** `PREMIERE_LIVE_E2E=1 ... pytest cli_anything/premiere/tests/test_live_e2e.py -q`. Expected: all pass. Then `doctor`, `recovery show`, the sequence list (only `Fixture seq`), close without saving.
- [ ] **Step 4: Measure, for Tasks 7 and 8** (the controller, on the fixture copy, before the close). Write the results into the plan workspace:
  1. The reply of `mcp call export_frame` with `output_path`, `time_seconds`, `expected_project_path`: the `data` and `receipt` keys.
  2. An own script that reads `app.getEnableProxies()`, sets it with `app.setEnableProxies(0 | 1)` and reads it back: does it behave like `manage_proxies` (`facts.md` section 6)?
  3. An own script that makes a sequence active, as the server's `set_active_sequence` does, confirmed by `live state`.
  4. What Lumetri index 4 reads on a clip with no LUT (apply a fresh Lumetri Color to V1 clip 1 of the copy with `mcp call apply_effect` if both clips hold a path).
  5. From the server code, no live run: what `export_frame` returns and whether a marker is written when `expected_cep_target` refuses.
- [ ] **Step 5: Write the result** of the control and the measures into `PREMIERE.md` (section "Live checks of Plan 2"). **Commit:** `test(premiere): live tests of the write path and effect set`.
- [ ] **Step 6: Report to Urko** in plain words: what passed, the control result, anything that changes Tasks 7 and 8. Then continue.

### Task 7: `live frame export`

**Files:**
- Create: `core/recipes/frames.py`, `core/scripts/app_switch.jsx`, `tests/test_recipes_frames.py`
- Modify: `live_recipes.py`, `tests/fakes.py` (the fake MCP server gets `export_frame`: it writes a small file at `output_path`, or fails when the name holds "FAIL", or writes an empty file when it holds "EMPTY")

**Interfaces:**
- Consumes: `WriteSession`, `McpSession`, the measures of Task 6.
- Produces:

```python
EXPORT_TIMEOUT_S = 600.0
FORMATS = ("png", "jpg", "tif")
def frame_name(prefix: str, seconds: float, fmt: str) -> str          # f"{prefix}_{round(seconds * 1000):08d}ms.{fmt}"
def export_frames(client, make_session, *, sequence_id, sequence_name=None, times: list[float], out_dir: Path,
                  prefix="frame", fmt="png", overwrite=False, proxies="off", dry_run=False,
                  project=None, any_project=False, allow_untested=False, hard_limit_s=EXPORT_TIMEOUT_S) -> dict
```

- Command: `live frame export --sequence ID [--sequence-name NAME] --time SECONDS (repeatable) --out-dir DIR [--prefix NAME] [--format png|jpg|tif] [--overwrite] [--proxies off|keep]`, plus `--dry-run` and the guard options. `--timeout` defaults to 600 here.
- Order of work:
  1. Before any contact with Premiere: at least one time, each `>= 0` (`bad_time`); `out_dir` is made with its parents (`output_dir` when that fails); each output file must not exist unless `--overwrite` (`output_exists`, naming the file).
  2. In a `WriteSession`: target the sequence; read its end, the proxy state and the active sequence (`app_switch.jsx`, action `read`); refuse a time at or after the end (`bad_time`). `--dry-run` stops here.
  3. Still in the session: with `--proxies off` and proxies on, switch them off; when another sequence is active, make the target active. Each switch is a write with its own read-back, and the active sequence is confirmed with a second state read (`not_switched` when it did not take).
  4. Give the lease back (`session.paused()`), start one MCP session, and call `export_frame` once per time with `output_path`, `time_seconds`, `expected_project_path` and `expected_cep_target: {"backend": "cep", "sequence_id": <id>}`. After each call the file must exist with a size above 0.
  5. Put back, in a `finally`: under the lease, the proxy state and the active sequence that step 2 read. When a recovery marker exists, no write is sent (decision 1, option A): the payload gets `"left_switched": {"proxies": false, "active_sequence": {"id", "name"}}` and `warnings` holds the two `live run` lines that put them back after the recovery.
- The first export that fails stops the run: exit 1, code `export_failed`, with the frames that were written.
- Payload: `{"sequence": {"id", "name"}, "frames": [{"time_seconds", "path", "bytes"}], "exported": n, "total": n, "proxies": {"before": bool, "during": bool, "after": bool}, "active_sequence": {"before": id, "after": id}, "left_switched": null, "stored": null, "rendered": null, "warnings": [...]}`. A PNG adds the warning "A PNG still can carry alpha. Composite it over black before you measure it."

- [ ] **Step 1: Write the failing tests** (fake panel for the scripts, fake MCP server for the export):

```python
def test_frames_are_exported_and_checked(...):            # 3 times -> 3 files, bytes > 0, names frame_00001000ms.png ...
def test_output_folder_is_made(...):                      # nested new folder exists after the run
def test_unmounted_volume_fails_before_any_contact(...):  # out_dir "/Volumes/NoSuchDisk/x" -> "output_dir"; panel saw nothing
def test_existing_file_is_refused(...):                   # -> "output_exists"; panel saw nothing; --overwrite passes
def test_name_with_space_quote_and_umlaut(...):           # prefix 'a "b" ü' -> file exists, path travels unchanged
def test_time_after_the_end_is_refused(...):              # sequence end 8.008, time 9 -> "bad_time", no switch written
def test_proxies_are_switched_off_and_put_back(...):      # proxies before True: writes seen: off, on; payload proxies before True, during False, after True
def test_proxies_keep(...):                               # --proxies keep: no proxy write
def test_other_active_sequence_is_switched_and_put_back(...)
def test_switch_that_does_not_take(...):                  # state still shows the old sequence -> "not_switched", no export call
def test_no_mcp_call_under_the_lease(...):                # the fake server's lease_seen flag is False for every export call
def test_failed_export_stops_and_puts_back(...):          # second file "FAIL": exit 1 "export_failed", 1 frame listed, proxies put back
def test_empty_file_is_a_failure(...):                    # "EMPTY" -> "export_failed"
def test_marker_blocks_the_put_back(...):                 # marker appears during the export: no write after it; left_switched filled; two warning lines
def test_dry_run_exports_nothing(...):                    # reads only; no MCP session started
def test_own_film_is_refused(...)
def test_app_switch_script_is_es3_and_runs_in_node(node)
```

- [ ] **Step 2 to 4** as before. **Step 5: Commit:** `feat(premiere): live frame export`.

### Task 8: `live lut set`

**Files:**
- Create: `core/recipes/lut.py`, `tests/test_recipes_lut.py`
- Modify: `live_recipes.py`

**Interfaces:**
- Consumes: `resolve_entries` and `write_entry` (Task 5), `export_frames` (Task 7), `WriteSession` (Task 2).
- Produces:

```python
LUT_PATH_INDEX, LUT_MENU_INDEX = 4, 6
PROVE_THRESHOLD = 1.0                      # mean absolute difference per channel, 0 to 255
def frame_pixels(path: Path) -> bytes      # ffmpeg: composite over black, scale to 960x540, rgb24; CliError("ffmpeg_missing") / ("ffmpeg_failed")
def mean_difference(a: bytes, b: bytes) -> float
def set_lut(client, make_session, target: dict, lut: Path, *, effect_index=None, prove=False, time=None, out_dir=None,
            dry_run=False, project=None, any_project=False, allow_untested=False, hard_limit_s=120.0,
            pixels=frame_pixels) -> dict
```

- Command: `live lut set --sequence ID [--sequence-name NAME] (--track N --clip N | --node-id ID) --lut FILE.cube [--effect-index N] [--prove --time SECONDS --out-dir DIR]`, plus `--dry-run` and the guard options.
- Before any contact: the LUT file exists, is absolute and ends with `.cube` (`bad_lut`); `--prove` needs `--time` and `--out-dir` (`bad_args`) and `ffmpeg` on PATH (`ffmpeg_missing`).
- The clip must hold a Lumetri Color effect (match name `AE.ADBE Lumetri`). The layout is checked in the resolve step: index 2 is named "Basic Correction", index 4 is a string, index 6 is named "Input LUT". Any other layout: `CliError("lumetri_layout", ...)`, nothing written.
- Writes, in this order: index 4 = the path plus a trailing `/`; index 6 = 1. Index 7 is never written.
- `stored` is true when index 4 reads back the written text. The read-back of index 6 is reported as `menu_value` and does not change `stored` (in a converted project it reads 0 while the LUT renders). The payload always holds the warning "Read-back is not proof for a LUT. Use --prove."
- `--prove` (decision 2, option A): export the frame at `--time` before the writes (`<out-dir>/lut_before_<ms>ms.png`), do the writes, export again (`lut_after_...`), compare with `pixels` and `mean_difference`. `rendered` is `true` when the difference is `>= PROVE_THRESHOLD`; `false` when it is below and index 4 held no path before; `null` when it is below and a path was there before, with the reason in `warnings`. The time must lie inside the clip (`bad_time`). The help text of `--prove` says: "Shows that the LUT changed the picture. It does not show that it is the right LUT. For a film, compare with an ffmpeg lut3d render of the source."
- Payload: `{"clip": {...}, "lut": str, "effect_index": int, "path_before": str | null, "path_after": str, "menu_value": number, "stored": bool, "rendered": true | false | null, "difference": float | null, "frames": [paths], "warnings": [...]}`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_lut_is_written_with_a_trailing_slash(...):     # write to index 4 holds ".../look.cube/"; then index 6 gets 1; never index 7
def test_stored_follows_index_4_only(...):              # index 6 reads back 0 -> stored True, menu_value 0
def test_layout_change_is_refused(...):                 # index 6 named "Look" -> "lumetri_layout", no write
def test_clip_without_lumetri(...):                     # -> "precondition", no write
def test_bad_lut_file(...):                             # missing; relative; ".txt" -> "bad_lut", panel saw nothing
def test_prove_true_when_the_frame_changes(...):        # pixels stub: before b"\x10"*n, after b"\x40"*n -> rendered True, difference 48.0
def test_prove_false_when_empty_slot_and_no_change(...):# path_before None, equal frames -> rendered False, exit 1
def test_prove_null_when_a_path_was_there(...):         # path_before set, equal frames -> rendered None, exit 0, warning gives the reason
def test_prove_needs_ffmpeg(...):                       # which -> None -> "ffmpeg_missing", nothing sent
def test_prove_time_outside_the_clip(...):              # -> "bad_time"
def test_dry_run_sends_reads_only(...)
def test_frame_pixels_with_real_ffmpeg(tmp_path):       # skipped without ffmpeg: two PNGs made by ffmpeg lavfi (gray 0x40, gray 0x80) -> difference near 64
```

- [ ] **Step 2 to 4** as before. **Step 5: Commit:** `feat(premiere): live lut set with a render check`.

### Task 9: Live checkpoint 2 (the controller runs it)

**Files:**
- Modify: `tests/test_live_e2e.py`
- Create (local, not in git): `tests/fixtures_local/halve.cube` (a 2-point LUT that halves every channel), written by the test when it is missing

- [ ] **Step 1: Write the live tests** (on `E2E copy`, frames into a pytest temp folder):

```python
def test_frame_export_three_times(work_copy, tmp_path):      # 3 PNG files, each above 10 kB; proxies after == before; active sequence after == before
def test_frame_export_refuses_an_existing_file(work_copy, tmp_path)
def test_lut_set_with_prove(work_copy, tmp_path):            # halve.cube on V1 clip 1: stored True, rendered True, difference above 10
def test_lut_set_identity_gives_no_proof(work_copy, tmp_path):  # identity.cube on the same clip again: rendered is None or False, never True
```

- [ ] **Step 2: The controller runs the live module**, with the same rules as Task 6 (messages to 76 and 78, fixture copy only, no save, fixture value unchanged, `doctor` and `recovery show` clean at the end).
- [ ] **Step 3: Results into `PREMIERE.md`.** **Commit:** `test(premiere): live tests of frame export and lut set`.

### Task 10: Documents and help text

**Files:**
- Modify: `cli_anything/premiere/README.md`, `skills/SKILL.md`, `tests/TEST.md`, `PREMIERE.md`, `premiere_cli.py` (root help, `status` line, `REPL_COMMANDS`), `utils/premiere_backend.py` (docstring and one added `status` key), `setup.py` (description)
- Test: `tests/test_live_cli.py` (help assertions)

Session 76 owns the reader text. Its sections stay word for word (Color Mode "not supported yet"; formats: 46 tested, 38, 40, 41, 42, 45 smoke-read). New text goes into new sections. Session 76 reviews the three documents before Urko signs off.

- Root help becomes: "Read Premiere Pro project files: sequences, clips, effects and Color Mode grades. The file commands never write. The live commands change the running Premiere 27."
- `status` human line becomes: "The file commands only read project files and need no running Premiere. The live commands need Premiere 27 with the MCP Bridge panel." `status` gets the key `"live_commands": true`. `writes_projects` stays `false`.
- `REPL_COMMANDS` gets one line per new command of Plan 1 and Plan 2.
- `README.md`: new section "Live commands" (what they need; `doctor` first; the recipes with one example each; proof levels `stored` and `rendered`). The four read-only sentences are changed to say "the file commands".
- `skills/SKILL.md`: new section "Live commands on the running Premiere 27" with the rules for an agent: run `doctor` first; a write needs a project whose file name holds "claude", or `--any-project` with `--project`; name the sequence by ID; put a risky write last; ProcAmp before Lumetri; read-back is not proof for a LUT, a mute or an effect order; `mcp call`, `mcp batch` and `live run` without `--write` run without the CLI write checks; the lease is not fair, so raise `--lease-wait` when a film session works; a dialog in Premiere blocks every call and only Urko clicks it; never open a film's 2026 project in 2027; stop a command with Ctrl-C, never with `kill -9`; after an error with `recovery_required` run `recovery show` and tell Urko.
- `tests/TEST.md`: new sections "Live unit tests" (what the fakes cover) and "Live end-to-end tests" (how to run them, the last real results of Tasks 6 and 9).
- `PREMIERE.md`: "Commands" lists the live commands; "Not done, and why" drops the two lines that are no longer true and names what milestone 2 brings.

- [ ] **Step 1: Write the failing help tests.**

```python
def test_root_help_says_what_writes(...):         # has "Read Premiere Pro project files", "The file commands never write.", "The live commands change the running Premiere 27."; no "Never writes."
def test_status_keeps_keys_and_adds_live_commands(...):   # every key of premiere_backend.status() before this task is still there; live_commands True; writes_projects False
def test_every_recipe_has_help_with_its_proof_level(...): # effect set: "stored"; lut set: "stored" and "--prove"; frame export: "file check"
def test_no_em_dash_in_any_help_text(...):        # walk all commands of the `live`, `mcp`, `ui`, `recovery`, `doctor` groups
def test_repl_lists_the_new_commands(...):        # REPL_COMMANDS has a key that starts with each of: "doctor", "recovery show", "mcp call", "ui mode", "live clip list", "live effect set", "live frame export", "live lut set"
```

- [ ] **Step 2: Write the documents.** **Step 3: Run the full suite** (the reader's 38 tests included). **Step 4: Send session 76 the three documents for review; take its notes.**
- [ ] **Step 5: Commit:** `docs(premiere): live commands in the documents and help text`.
- [ ] **Step 6: Stop and report to Urko.** Plan 3 (milestone 2) gets its own short design first, as the spec says.

---

## Not in this plan

- The rest of the parked list of Plan 1: M2.4, M2.6 (a parser-based ES3 check), M3.4, M4.1, M6.1, M7.6, m5, F2 to F7, and the fixes of `run_gates.py`. None of them blocks a recipe. They stay in the ledger of Plan 1.
- Audio effects in `live effect set`, colors, and keyframes.
- `--save`, `live place`, `live effect copy`, `live track disable-clips`, `live sequence duplicate` (milestone 2).
- The proof of a LUT against an `ffmpeg lut3d` render (decision 2, option B).

## Self-review notes

- Spec coverage: section 7 item 8, all five recipes (Tasks 3, 4, 5, 7, 8); item 9, documents (Task 10); 5.5 proof levels (Tasks 5, 7, 8); 5.6 rules 1 and 2 (the write session, Task 2), rule 3 (targets, Tasks 2 and 5), rule 4 (Task 5), rule 4b (Task 7), rule 5 (Tasks 7 and 8), rule 6 (Task 5, after a breath); rule 7 is met by not saving. Section 5.1: the server version check that Plan 1 left out (Task 1).
- Final review of Plan 1: recommendations 1 to 5 and 9 are in the Global Constraints and Tasks 2, 5, 7, 10; 6 is Task 1; 7 partly (ticks as strings, the line number; no parser check); 8 is Task 6.
- Differences from the spec, all stated above: `--prove` compares before and after (decision 2); the put-back does not run against a marker (decision 1); `live clip list` has a limit of 400 clips and fewer fields than session 76 asked for; `live effect set` takes video tracks only; the version stays 0.1.0.
