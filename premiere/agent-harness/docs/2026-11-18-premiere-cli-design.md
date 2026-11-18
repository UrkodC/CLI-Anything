# Premiere 27 CLI: design

Date: 2026-11-18. Status: approved by Urko on 2026-11-18 (draft 3). Nothing is built yet.

This file lives in `premiere/agent-harness/docs/` because the root `.gitignore` of the repo
ignores `/docs/`.

Target: Adobe Premiere Pro 27.0.0 (build 88) on macOS, with `premiere-pro-mcp` 1.25.0 and its CEP
panel `MCPBridgeCEP` installed.

Home: the worktree `delCastillo_Lab/CLI_anything_premiere`, branch `feat/premiere-cli`, package
`premiere/agent-harness` (command `cli-anything-premiere`). The package exists today as a
read-only reader of `.prproj` files, built by the film-edit session `premier-pro-mcp-76` on
2026-11-18.

Sources: Urko's tool table (Appendix A); read-only tests by the design session; the answers and
written notes of the two film-edit sessions (`premier-pro-mcp-76` and `premier-pro-mcp-78`),
listed in Appendix B. Both answered the same eight questions from the same notes and agree. A
reviewer read draft 1 and its three questions are answered in this draft.

Split of work agreed with session 76: it keeps the file route (`core/prproj.py` and its tests).
New code goes in new modules.

## 1. Intent

Urko edits films with Claude through the `premiere-pro-mcp` server. The server has limits: some
tools fail, some refuse the edits he needs, and some parts of Premiere 27 are closed to it. He
wants one command-line tool that reaches every editing tool he uses or wants to use, and that
works better than the MCP tools where that is possible.

The user of the CLI is Claude during an editing session. Urko reads the results.

Where the time goes today (measured by the film-edit session):
1. Recovery after a failed or refused write. One failure blocks every later call, and
   `inspect_operation_recovery` prints every clip of the sequence (about 30k tokens on a 73-shot film).
2. Cost per call. A call alone in a message costs about 7.7 s. A recut of one film was about
   450 calls in 35 messages. A loop inside one shell command removes most of this.
3. Effect copy: one effect per call, only inside the active sequence, reversed order when several
   are copied. One recut needed 153 copy calls.
4. Missing operations that force a re-place of the clip (5 to 8 calls each): no remove or move
   of a transition, no audio transitions, no speed change, no adjustment layer. Slow-motion
   clips (59.94 read as 23.976, all of Urko's films now) make `slip_edit`, `roll_edit` and
   `set_clip_duration` refuse a late window.
5. Node IDs. After `duplicate_sequence` the clips get new IDs in pieces, so every page of the
   sequence must be read before a write. Addresses by track and clip index remove this.
6. Closed boxes that only an exported frame can prove (LUT path, Gyroflow, Color Mode, masks).

## 2. Goals, non-goals, anti-goals

Goals
1. One command reaches any tool of the MCP server by name (all tools from day one).
2. The CLI can send its own scripts to Premiere, with no dependence on the MCP tool list.
3. Many writes run in one command, in one turn at the bridge, with a check after each write.
4. Film steps with known problems become single commands that carry the fix inside.
5. Each row of the tool table that is "Cannot use" gets a real test and an honest status.
6. The reader of saved project files keeps working as it does now.

Non-goals
- No replacement of the MCP server. It stays installed and in use.
- No Windows.
- No new panel inside Premiere (CEP or UXP) in milestone 1.
- No write to a `.prproj` file in milestones 1 to 3.
- No Premiere 26.5.2 for the live commands. Urko asked for a CLI for Premiere 27.0 in the design
  session, and session 76 reports that he chose "27 only" there on 2026-11-18. Old films
  (26.5.2 projects, format 45) stay with the MCP tools. A write command refuses with a clear
  message when the running app is not 27.0.x. The file route still reads format 45 with its warning.

Anti-goals (these make the work a failure even if the code runs)
- A command reports success and Premiere did not change. See section 5.5 for the two levels of proof.
- A live command breaks the MCP server's turn-taking, so the server refuses edits afterward.
- A live command changes Urko's own project.
- The reader loses its promise that it never changes a project file.
- A "Cannot use" row is marked as solved with no measured proof.

## 3. Facts measured on 2026-11-17 and 2026-11-18

| Fact | How it was measured |
|---|---|
| The CEP panel runs every `cmd_<id>.jsx` it finds in the bridge folder and writes `res_<id>.json`. | Read `cep-plugin/main.js` and `dist/bridge/file-bridge.js`. |
| A program outside the server can use that folder. One script that returns `app.version` got the reply `{"success":true,"data":{"version":"27.0.0","build":"88"}}` in 0.07 s. | Probe script, under the lease, read-only. |
| The MCP server starts from the shell and answers. `initialize` 0.69 s, `tools/list` 439 tools, `ping` 0.07 s, total 0.78 s per start. | Probe script over stdio. |
| `execute_extendscript` and `evaluate_expression` are not in the list under the default authority. They need `unsafe-script`. | Same probe. |
| Every start of the MCP server deletes all `cmd_`, `res_` and `busy_` files in the bridge folder. It does not take the lease and does not check the age of the files. | Read `cleanupTempDir` in `dist/bridge/file-bridge.js` and its call in `dist/index.js`. |
| Premiere 27 has `Window > Mode > Import, Edit, Color, Export`. A menu click from `osascript` switches the mode with Premiere in the background. | Three round trips, mode mark and panel list read back. |
| One time the mode went back to Edit a few seconds after a switch to Color. It did not repeat. Cause unknown. | Seen once in 4 switches. |
| Premiere 27 has no useful AppleScript dictionary (two commands: `capture`, `editoriginal`). | Read `AdobePremierePro.sdef`. |
| The reader handles project formats 38 to 46. Color Mode detection in a file was never checked on a real grade. | `PREMIERE.md` in the package. |
| No project on the Mac or the Samsung drive holds a Color Mode grade. The one test project was deleted on 2026-11-17. | Scan by the film-edit session, 2026-11-18. |

Bridge protocol (internal to `premiere-pro-mcp` 1.25.0, not a public API):
- Folder: `<user temp dir>/premiere-mcp-bridge`, mode 0700, owned by the user.
- Command: write `cmd_<uuid>.jsx.staged`, then rename to `cmd_<uuid>.jsx`. The panel polls every 200 ms,
  claims the file by rename, runs it with `evalScript`, and writes `res_<uuid>.json` by rename.
- The script must return a JSON string. `{"success":false,"error":...}` is a failure. An empty
  return is a failure. While a script runs longer than 2 s the panel writes `busy_<uuid>.json`.
- Liveness: `bridge-heartbeat.json` holds `protocolVersion`, `commandSchemaVersion`,
  `receiptVersion`, `state`.
- Turn-taking: a lock folder `.host-owner-lease` (0700) with `owner.json`
  `{"version":1,"pid":<pid>,"token":<string>}` (0600). A program takes the lease with `mkdir`,
  and removes `owner.json` and the folder at the end. If the owner's pid is dead, the next
  program writes `.host-recovery.json`, and the server refuses edits until the recovery is
  inspected and acknowledged.

## 4. Approaches

| Approach | What it is | Trade-off |
|---|---|---|
| A. One CLI with four routes (picked) | Extend `cli-anything-premiere` with a script route, an MCP pass-through, a screen route, and the file route it has now. | More work. Built in milestones, each one usable. Can go past the MCP limits. |
| B. Pass-through only | The CLI only calls MCP tools by name. | About one session of work. Keeps every limit. Gains: less context, calls can be chained in scripts. |
| C. Own panel in Premiere | A new CEP or UXP panel with its own protocol. | Most work. No new power over A, because A can already run any script through the panel that exists. |

Urko picked A on 2026-11-18. Reason: his goal is to get past the limits, and B keeps them all.
C was dropped by Claude for the reason in the table.

## 5. The four routes

| Route | Reaches | Needs | Ceiling |
|---|---|---|---|
| File | Saved `.prproj` files: sequences, clips, effects, LUT paths | Nothing. Premiere can be closed. | Reads only. Unsaved changes are not seen. |
| Script | Everything ExtendScript and the QE DOM can do in the running app | Premiere open, panel running | Data that Adobe hides from scripts stays hidden (Color Mode grade, masks, Lumetri curves and wheels). |
| Pass-through | Every tool of `premiere-pro-mcp` | Premiere open, panel running, `premiere-pro-mcp` on PATH | The same limits as the MCP tools. |
| Screen | Menu items of Premiere | macOS Accessibility permission for the terminal | Menu items only in milestone 1. Values inside panels are unproven. |

### 5.1 Script route: transport (Claude's call)

The CLI writes command files to the bridge folder itself, under the lease, with its own small
helper library. It does not go through the MCP server with `unsafe-script`.

Reasons: 0.07 s against 0.78 s per call, no change to the server's authority setting, and the
CLI controls its own scripts and read-backs.

This call depends on gate test G1 in milestone 1. If a CLI write makes a running MCP server
demand a recovery on its next write, the two cannot share one editing session, and the choice
goes back to Urko before more is built.

Rules:
- The protocol is internal. Before every live command the CLI checks `protocolVersion == 1`,
  `commandSchemaVersion == 1`, the `premiere-pro-mcp` package version against a tested list
  (1.25.0), and the Premiere version against a tested list. On a mismatch, read commands warn
  and write commands refuse. `--allow-untested` overrides, and the output says so.
- The lease is held for one command: one script plus its read-back, or one whole batch. It is
  released in a `finally` block and on SIGINT and SIGTERM.
- When another program holds the lease, the CLI waits and tries again (default 30 s,
  `--lease-wait SECONDS`). The film-edit session sends groups of about 15 calls, and the CLI
  must fit between them, not fail.
- The CLI implements the same lease checks as the server (folder mode, owner, no symlink).
- The heartbeat is read again right before each write. An old reading is not used.
- Timeouts. A recovery marker costs a film-edit session about 30k tokens, so the CLI does not
  give up early. A short script has 30 s. While a fresh `busy_` file shows that the script still
  runs, the CLI keeps waiting, up to a hard limit per command (`--timeout`, default 10 minutes
  for export and import recipes, 2 minutes for the others).

Failure rules (Claude's call: the CLI behaves as one more cooperating server process):

| Case | What the CLI does | Exit and code |
|---|---|---|
| Read-only script: no reply before the timeout, or interrupted | Removes its own files, releases the lease. No recovery marker. If no `busy_` file was seen, the message says "Premiere does not answer, a dialog may be open". | 1, `timeout` or `interrupted` |
| Write script: published, then no reply before the timeout, or interrupted | Writes `.host-recovery.json` in the server's format with the reason `unknown_command_outcome`, removes its own files, releases the lease. Same dialog message. | 1, `unknown_outcome` |
| Write script: reply says failure | Releases the lease. No marker. | 1, `script_failed` |
| Lease held by a live pid for longer than the wait | Sends nothing. | 1, `lease_busy` |
| Lease held by a dead pid | Follows the server's protocol: writes the marker with the reason `abandoned_process_lease`, removes the dead lease under the `.host-owner-reaper` lock, then continues for a read command and refuses for a write command. | write: 1, `recovery_required` |
| `.host-recovery.json` exists | Read commands run. Write commands refuse and point to `recovery show`. | write: 1, `recovery_required` |

The CLI never acknowledges a recovery by itself. `recovery show` only reads.

Script rules:
- ExtendScript is ES3. The helper library provides JSON output, `ok(data)` and `fail(message)`.
  It is the CLI's own code and does not use the server's `helpers_*.jsx`.
- Arguments enter a script as one JSON literal built with `json.dumps`. No string joins.
- Every script is wrapped in a function with try and catch, so a thrown error comes back as
  `{"success":false,...}` and not as an empty reply.
- Time values cross the boundary as ticks in strings (254016000000 ticks per second), as in the reader.
- Effect parameters are addressed by index, and the script checks that the name at that index is
  the expected one. "Temperature", "Saturation" and "Contrast" exist twice in Lumetri.

### 5.2 Pass-through

- `mcp call TOOL --args JSON` starts `premiere-pro-mcp` over stdio, calls one tool, prints the
  result, and stops the server.
- `mcp batch FILE` runs several calls in one server process, in order, and stops at the first failure.
- `mcp list [--query TEXT]` and `mcp describe TOOL` print names, descriptions and argument schemas.
- The server keeps its default authority. The CLI never sets `unsafe-script`.
- Start-up rule: a new server deletes every command and reply file in the bridge folder, also the
  files of a command that another session has in flight. So the CLI takes the lease first, starts
  the server, waits for the reply to `initialize` (the cleanup runs before it), releases the
  lease, and only then calls the tool.
- The result of an MCP tool is printed as the tool gave it, with `stored: null` and
  `rendered: null`. The CLI does not sort the 439 tools into read and write.
- The film-edit notes hold the trap "two Premiere MCP servers must not run at once (shared
  bridge folder and panel name)". Two servers ran side by side on 2026-11-18 (sessions 76 and
  78), and the lease exists for that case. The one collision found is the start-up cleanup
  above. Gate test G5 looks for others.
- Gate test G2 (milestone 1) decides how useful `mcp call` is: the film-edit session's notes say
  node IDs "are handed out when a call first lists those clips". If that map lives in the server
  process, a node ID from one start does not resolve in the next, and almost all work needs
  `mcp batch`. The help text then says so.

### 5.3 Screen route

- `ui mode get` reads the checked item of `Window > Mode`.
- `ui mode set import|edit|color|export` clicks the item, then reads the mode once per second
  for 5 s. If the mode changed back, it clicks once more and reports `reverted_once: true`.
  If it changes back again, the command fails.
- The process name comes from the Premiere app that runs (the process list), not from the newest
  app in `/Applications`: two versions are installed. No fixed "2027" string.
- A missing Accessibility permission gives the error code `no_accessibility` with the fix.

### 5.4 File route

No change in behavior. The group keeps the promise "never writes a project file", and the tests
keep the SHA-256 and time-stamp check after every command.

### 5.5 Two levels of proof

A value that reads back is not always a value that renders. Five measured cases where the
read-back was fine and Premiere was not: a LUT path in the Look slot (index 32); the Input LUT
menu (index 6); `mute_track`; `adjust_audio_levels` on a clip with Level keyframes;
`set_active_sequence` by ID. Also: several copied effects landed in reverse order.

Every write command reports two fields:
- `stored`: the value read back from Premiere equals the value written. `true`, `false`, or
  `null` (not checked).
- `rendered`: an exported frame or exported audio shows the change. `true`, `false`, or `null`
  (not checked).

Exit status 1 when either field is `false`. Each recipe's help text says which level it reaches.
For the LUT, effect order and mute, `stored: true` alone is printed with the warning "read-back
is not proof for this change". The option `--prove` adds the render check where a recipe has one.

`live run` and `mcp call` print both fields as `null`. `live run` accepts `--verify CHECK.jsx`:
that script runs in the same lease after the first one, and its reply
`{"success":true,"data":{"stored":true|false}}` sets `stored` and the exit status.

### 5.6 Rules for every write (from the film-edit session's practice)

Enforced by the CLI:
1. Project guard. A write refuses unless the file name of the active project contains the word
   "claude" in any case. Working copies on the drive have names such as
   `... Claude 2.prproj`, `... Claude_1.prproj` (Premiere's name for a converted copy) and
   `... claude.prproj`, so the end of the name is not checked. Urko's own projects do not carry
   the word. For any other project the caller must give both `--any-project` and
   `--project PATH` equal to the active project, and the output says that the guard was
   overridden. `--project` alone does not switch the guard off, because rule 2 uses it too.
2. Two projects open. Node IDs repeat across projects. A write refuses when more than one
   project is open, unless `--project PATH` is given and equals the active project.
3. Targets are explicit. A clip is named by sequence ID plus track and clip index (as in the
   reader), or by node ID. No write command reads the selection, the playhead or the active
   sequence to find its target. A sequence is named by `--sequence ID`, and the script checks
   that its name is the expected one when `--sequence-name` is given. A batch resolves all its
   targets first, inside the lease, and prints the name and start of each resolved clip.
4. A batch stops at the first failure and reports what landed and what did not.
4b. A command puts back what it switched (proxy display, active sequence).
5. Checks before the write: the output folder exists (or the recipe makes it), the output file
   does not exist unless `--overwrite`, the source has the frames the edit needs.
6. Node IDs are read again after an overwrite, a duplicate or a remove. A batch that needs new
   IDs gets them inside the same lease.
7. A batch saves the project at its end only with `--save`.

Left to the caller, and written in `SKILL.md`: put a risky write last; ProcAmp before Lumetri;
read ProcAmp, Motion and keyframes before removing a clip; a dialog in Premiere blocks every
call and only Urko can click it; never open a film's 2026 project in 2027.

## 6. Commands

```
cli-anything-premiere status                      (exists; add panel, bridge and MCP facts)
cli-anything-premiere doctor                      (new)
cli-anything-premiere recovery show               (new)
cli-anything-premiere project|sequence|clip|colormode ... FILE   (exist, file route)
cli-anything-premiere mcp list|describe|call|batch               (new, pass-through)
cli-anything-premiere live state                                 (new, script route)
cli-anything-premiere live run SCRIPT.jsx [--args JSON] [--write] [--verify CHECK.jsx]
cli-anything-premiere live <recipe> ...                          (new, script route)
cli-anything-premiere ui mode get|set                            (new, screen route)
```

Shared rules, as in the package today: `--json` on every command; success is `{"ok": true, ...}`;
failure is `{"ok": false, "error": "...", "code": "..."}` with exit status 1; track and clip
numbers start at 0.

`doctor` checks, each with pass, warn or fail: Premiere app and version (two versions are
installed, so it names the one that runs); panel heartbeat and its age; protocol versions; lease
state (free, held by a live pid, stale); recovery file; open projects and the project guard;
`premiere-pro-mcp` on PATH and its version; Accessibility permission. It changes nothing.

`recovery show` prints the reason and the revision of `.host-recovery.json`, and a short state
of Premiere: project, active sequence, number of tracks and clips. It never prints the clip
list. It changes nothing.

`live run` is the open door for any script. It is read-only by default. `--write` marks the
script as a change, which turns on the version guard, the recovery check and the write rules 1
and 2. The CLI cannot inspect what a script does, so this flag is a declaration by the caller.
`live run` has no `--dry-run`.

Write recipes in `live` take `--dry-run` (prints the resolved target and the planned change,
sends nothing).

## 7. Milestones

This is several projects. Milestone 1 is specified here. Milestones 2 to 4 are an ordered list
and each gets its own short design before it is built.

### Milestone 1: the frame (specified)

Order of work:

1. Bridge client: folder checks, lease with wait, version guard, command and reply, timeout,
   busy file, the failure rules of 5.1.
2. Helper library for scripts (ES3).
3. `doctor`, `recovery show`, and the new facts in `status`.
4. `live state` (projects, active sequence, playhead, selection) and `live run`.
5. Gate tests. They run on a test project named `... Claude.prproj`, on a duplicate sequence,
   before any recipe is built. Results go into `PREMIERE.md`.

| Gate | Question | If the answer is bad |
|---|---|---|
| G1 | After a CLI script write, does a write from an MCP server that was already running pass, or does it demand a recovery? | Stop. The transport choice of 5.1 goes back to Urko. |
| G2 | Does a node ID from one server start resolve in the next start? | `mcp call` is for tools with no node ID. Work with node IDs uses `mcp batch`. |
| G3 | Does a preview and apply pair (`preview_edit_plan`, `apply_edit_plan`) work across two starts? Do `create_sequence_checkpoint` and `list_sequence_checkpoints`? | The same as G2 for those tools. |
| G4 | Does a negative value written by the CLI's own script (raw `setValue`) fail with "Illegal Parameter type" as it does through the MCP tool? Test ProcAmp Brightness and Lumetri Temperature, 30 writes each. | The fault is in Premiere, not in the server's helper. The recipe keeps the full rule set below. |
| G5 | Does anything else collide when a second server starts while a film-edit session's server is in use? Run `mcp call ping` during a read command of that session. | List the collision and the rule for it before `mcp call` is released. |

6. `mcp list`, `mcp describe`, `mcp call`, `mcp batch`.
7. `ui mode get` and `ui mode set`.
8. Five recipes. Both film-edit sessions asked for many writes in one call first.

| Recipe | What it does and the fix inside | Proof level |
|---|---|---|
| `live clip list` | A compact map of one sequence: track, clip index, node ID, start and end, source item, effect names. One line per clip. | read only |
| `live effect get` | Lists the effects of a clip and their parameters by index, as the reader does. | read only |
| `live effect set` | One write, or many with `--from PLAN.json` (many clips, many parameters: ProcAmp, ASC CDL, Lumetri, the LUT path) in one lease. Each write is read back. Stops at the first failure. For a negative value: writes 0 first when the value crosses zero; after two failures writes a near value (for example -2.5 for -2.6) and reports which value landed. | `stored` |
| `live lut set` | Writes the `.cube` path plus a trailing `/` into Lumetri parameter 4, then sets parameter 6 to 1. Never writes parameter 7. With `--prove`: exports the frame with and without the LUT and compares. | `stored` (not proof); `rendered` with `--prove` |
| `live frame export` | One or many times in one call. Makes the output folder. Turns proxy display off, exports, and puts proxy display back to its earlier value. Checks that each file exists and is not empty. | file check |

9. Documents: `SKILL.md`, `PREMIERE.md`, `README.md`, `TEST.md` and the help text change from
   "reads only" to "the file commands read only; the live commands change the running app".

Rule for recipes in all milestones: a recipe uses its own script when one script can do the step
and the read-back in one round trip. It calls the MCP tool (through the pass-through code) when
the tool's logic is large and works, for example export and import.

### Milestone 2: the film-edit session's most frequent writes, then the fixable rows

Recipes, in the order the film-edit session asked for:

| Recipe | What it does | Known traps to cover |
|---|---|---|
| `live place --plan FILE` | `set in and out` plus overwrite for a list of shots, frame-exact | Overwriting the first V1 clip deletes its start transition. The same item at the same start fails. Linked sound needs a target audio track. On 27.0.0 an in mark at interpreted frame N starts on camera frame N - 1 for slow-motion items. |
| `live effect copy` | An ordered chain (Gyroflow, ProcAmp, Lumetri Color) from clip A to clips B..N in one call, across sequences if a script can | Several effects in one copy land in reverse order. Proof needs a render-order test. |
| `live track disable-clips` | Disables every clip of a track. The real fix for the mute row. | Proof is the loudness of an export. |
| `live sequence duplicate` | Duplicates, renames, makes the copy active, returns the full clip list mapped by index and source item | The copy is named "<old> Copy". Rename works by name. `set_active_sequence` once reported verified and did not switch. |

Rows to test. The status of every row today is "unproven".

| Row | Known today | Route to try | Test that decides |
|---|---|---|---|
| Mute a track for an export | `mute_track` passed with no error, the track later read `isMuted` false, the export had the sound | Script: `track.setMute()`, then QE DOM | Mute state read back after a few seconds and after the export; export of 2 s of audio measured with ffmpeg |
| Edit plans in one call | `apply_extended_edit_plan` refuses linked clips and effect steps; the other two do insert edits only | `live place --plan` | Clip starts, ends, in and out points read back equal the plan; later clips did not move |
| Paste attributes | One line in the notes: "it can change effect order". Not known if measured. | Script: copy the whole effect where one has binary parameters (Lumetri blob, curves); copy values one by one only for plain parameters | Every parameter equals the source, and a brightness step test shows the render order (before the LUT, dark and bright pixels move by different amounts) |
| Lumetri LUT menu | The menus (index 6, max 12; index 34, max 79) list only the top LUT folder. Urko's Phantom LUTs sit in a subfolder. | Solved by `live lut set` if its `--prove` passes | Exported frame against a no-LUT frame |
| Adjustment layer | The MCP tool reports that neither `qeSeq.addAdjustmentLayer` nor `qe.project.newAdjustmentLayer` exists. The proven route is `import_sequences` from the template project. | Keep the template import as a recipe. Screen route as a test only: it needs Project panel focus, a dialog, and the right active sequence. | A project item with `isAdjustmentLayer` true exists and covers the frame |
| Gyroflow sliders | Writes are ignored. A write to index 14 triggers a recovery. | Read the parameter list; if the values are binary, stop | A changed value reads back and the exported frame moves. Gyroflow renders are not repeatable between exports, so compare with a tolerance. |
| Missing operations | No clip speed change (the tools are stubs), no remove or retime of a transition, no audio transitions | Script and QE DOM | Each one read back and seen in an exported frame or exported audio |

### Milestone 3: tools never used

Recipes for the "Never used" rows of the table, in the order Urko chooses. Default order:
markers, scopes and quality checks, loudness, captions and transcripts, find cuts, remove
silence, undo, bins and labels, nest and speed, crop and opacity, render queue, exports to other
apps, multicam, auto reframe. Most of these start as pass-through calls and become recipes only
where a fix is needed.

### Milestone 4: rows that Adobe hides from scripts

| Row | Known today | Route to try | Test that decides | Blocked by |
|---|---|---|---|---|
| Color Mode grade (Adjust, Style, LUT) | The tools see one component "Color Mode Effect" with no properties. A copy of it fails. `duplicate_sequence` keeps the grade and the tracked mask. It renders after a Lumetri effect on the same clip. | File route: find and decode the grade in a saved project | Values in the file equal the values Urko set by hand | A fixture project from Urko |
| Masks | "HSL Mask" and "Mask" effects hide their shape and range data | File route, same fixture | Mask type and points found in the file | The same fixture |
| Lumetri curves and color wheels | The real Lumetri state is the blob at parameter 0 (XML). A copy of the whole effect inside one sequence renders exactly (0.0/255). | File route: decode the blob. Script route: copy the whole effect from a template clip. | Exported frame equals the frame of the template clip | Nothing |
| Color Mode by screen | Only the mode switch is proven | Screen route: read the accessibility tree of the Color Mode panels | A slider value can be read and set, and the exported frame changes | Nothing |
| Premiere AI Assistant | Only Urko can run it. When he ran it, it set a Color Mode Exposure and made a tracked Object mask, so Adobe has an internal route. Its skill files in the app are encrypted and stay untouched. | None planned | | Stays manual |

A write to a project file, if milestone 4 leads there, works on a copy with Premiere closed and
needs its own design.

## 8. Tests

- Unit tests need no Premiere. A fake panel (a thread that watches a temp bridge folder and
  answers command files) covers the bridge client. Expected results are the rows of the failure
  table in 5.1: lease taken and released; lease busy then free inside the wait; lease busy past
  the wait; dead-pid lease; recovery file; version mismatch; read timeout (no marker); write
  timeout (marker written); empty reply; error reply; SIGINT during a read (no marker) and
  during a write (marker written).
- Pass-through unit tests use a small fake MCP server script over stdio. One test checks that the
  lease is held while the server starts.
- Screen route unit tests replace the `osascript` call with a stub.
- Write-rule unit tests: the project guard, the two-project rule, stop at the first failure.
- End-to-end tests need Premiere 27 running with a copy of the fixture named
  `fixture27 Claude.prproj` open. They are skipped with a clear message when the heartbeat is
  absent, and when the open project is not the fixture (film sessions share the one running
  Premiere). Every end-to-end write test works on a duplicate of the fixture sequence and deletes
  it at the end.
- No frame comparison on a Gyroflow shot without a tolerance.
- The existing 24 unit tests and 14 end-to-end tests of the reader stay green and unchanged.

## 9. Needed from Urko

1. One small Premiere 27 project with a Color Mode grade (one Adjust operation) and one mask.
   Nobody has one. This unblocks milestone 4 and the open item in `PREMIERE.md`.
2. The order of milestones 3 and 4 after milestone 2.

Decided: Premiere 27.0.x only for the live commands (see section 2).

## 10. Left to the builder

- Module layout inside `cli_anything/premiere` (suggested: `core/bridge.py`, `core/jsx/`,
  `core/mcp_client.py`, `core/ui.py`, `core/recipes/`). The file route (`core/prproj.py` and its
  tests) belongs to the film-edit session; change it only for the document updates of milestone 1.
- The command is installed editable from this worktree and the film-edit session uses the
  reader. `premiere_cli.py` imports the new modules lazily, inside the commands, so a fault in
  new code cannot break the reader.
- Keyframe times: the script API takes times relative to the clip start, and the project file
  stores media time. Each recipe says which one it takes.
- The exact error codes for the new commands, in the style of the ones that exist.
- The version number (suggested 0.2.0).
- How the REPL lists the new groups.
- Whether `mcp batch` and `live effect set --from` read JSON lines or one JSON array.
- The tolerance of the frame comparison in `--prove` (the notes report 0.3/255 for a LUT against
  ffmpeg `lut3d`).

## 11. Risks

- `premiere-pro-mcp` changes its bridge format in an update. The version guard catches it; the
  tested list then needs a new entry after a re-test.
- The panel runs one command at a time. A long script blocks the MCP server for that time.
- The menu route depends on English menu names. Other languages are out of scope.
- The cause of the one mode revert is unknown. The read-back in `ui mode set` covers it.
- An MCP server that another program starts (a new Claude session, for example) still deletes
  the files of a CLI command in flight. The CLI then reports `timeout` or `unknown_outcome`. The
  CLI cannot prevent this.
- The negative-value fault may sit in Premiere's `setValue`. Then the CLI's own script fails the
  same way, and the gain is only the handling. Gate G4 measures it. On 27.0.0 five of five
  negative writes passed through the MCP tool, so the fault may be gone there.
- The server keeps a host state revision. A CLI write changes Premiere behind the server. Gate
  G1 measures what the server does next.

## Appendix A: Urko's tool table (2026-11-18, as he gave it)

Rows that goal 5 covers: every row with the status "Cannot use" or "Cannot use well". Milestones
2 and 4 list them all except three: "Premiere AI Assistant" (stays manual), "Warp Stabilizer"
(not used by Urko's rule) and the two rows marked "Tested, not used in a film" (no limit to fix).

| Task | Tool | Status | Note |
|---|---|---|---|
| Check that Premiere answers | `ping`, `get_premiere_state`, `get_bridge_telemetry` | I do it | Before every group of writes |
| Make, open, save, close a project | `create_project`, `open_project`, `save_project`, `close_project` | I do it | Only the user clicks dialogs |
| Import clips and sequences | `import_media`, `import_sequences` | I do it | |
| Proxies | `manage_proxies` | I do it | |
| Slow motion | `set_override_frame_rate` | I do it | 60 or 120 fps clip read as 23.976 |
| Choose the part of a clip and place it | `set_item_in_out`, `overwrite_clip`, `create_sequence_from_clips` | I do it | Overwrite only, no insert |
| Trim, slip, roll a cut | `trim_clip`, `slip_edit`, `roll_edit`, `set_clip_duration` | I do it | Slip moves keyframes |
| New version of a film | `duplicate_sequence`, `set_active_sequence` | I do it | I read back the active sequence |
| Turn clips on and off | `batch_enable_disable`, `enable_disable_clip` | I do it | Also used for a dialogue-only export |
| Scale, rotate, level a shot | `set_clip_scale`, `set_clip_rotation`, `set_clip_properties_batch`, `add_keyframe` | I do it | Roll comes from the camera sensor data |
| Exposure before the LUT | `apply_effect` (ProcAmp), `set_effect_property` | I do it | Negative values sometimes fail once, I retry |
| White balance | `apply_effect` (ASC CDL, Lumetri Color), `set_effect_property` | I do it | Values come from my script `wb_methods.py` |
| Film look (LUT) | `set_effect_property` on Lumetri (path in index 4) | I do it | Live, no render |
| Saturation, vibrance, contrast | `set_effect_property` on Lumetri | I do it | Sliders only |
| Copy a grade to other clips | `copy_effects_between_clips` | I do it | One effect at a time |
| Stabilize | `apply_effect` (Gyroflow) | I do it, with limits | I cannot change its sliders by script |
| Read effect values and keyframes | `get_effect_properties`, `get_keyframes`, `list_available_effects` | I do it | |
| Sound levels and fades | `add_audio_keyframes`, `adjust_audio_levels`, `apply_audio_effect`, `add_transition_to_clip` | I do it | Crossfades with overlap on two tracks |
| Look at frames to check my work | `export_frame`, `export_sequence_clip_review_frames` | I do it | The folder must exist first |
| Export the film | `export_sequence` | I do it | Media Encoder presets |
| Fix a failed call | `inspect_operation_recovery`, `acknowledge_operation_recovery` | I do it | |
| Score, title cards, skin check, loudness | My Python and ffmpeg scripts | I do it, outside Premiere | Result is imported as a file |
| Animated title | After Effects by script | I do it, outside Premiere | |
| Read a saved project file | `cli-anything-premiere` | I do it (new) | LUT file per clip, values, effect order |
| Adjustment layer | `add_adjustment_layer` | Cannot use | Fails on 26 and 27. I import a template project |
| Color Mode grade (Adjust, Style, LUT) | Premiere 27 Color Mode | Cannot use | No script access. I cannot read it back |
| Masks (object, shape, pen, HSL) | Color Mode masks, `Mask`, `HSL Mask` effects | Cannot use | Mask data is hidden from scripts |
| Lumetri curves and color wheels | Lumetri binary parameters | Cannot use | Stored as binary data |
| Lumetri LUT menu | `apply_lut` | Cannot use well | Menu limits. I write the path by script |
| Edit plans in one call | `apply_edit_plan`, `apply_extended_edit_plan`, `add_to_timeline_batch` | Cannot use | Insert edits only, or they refuse linked clips |
| Paste attributes | `paste_clip_attributes` | Cannot use | Not safe in my tests |
| Mute a track for an export | `mute_track` | Cannot use | Did not work. I disable the clips |
| Premiere AI Assistant | Inside Premiere 27 | Cannot use | Only you can run it |
| Warp Stabilizer | `stabilize_clip` | Not used (your rule) | You saw artifacts. Gyroflow only |
| Noise reduction | `VR De-Noise` effect | Tested, not used in a film | Safe only at Noise Level 0.02 |
| Color tools as effects | `Color And Contrast`, `Color Space Transform`, `LUT Transform` | Tested, not used in a film | New in Premiere 27 |
| Captions and transcripts | `create_caption_track`, `read_sequence_captions`, `prepare_transcript_workflow` | Never used | |
| Titles inside Premiere | `add_title`, `add_text_overlay`, `import_mogrt` | Never used | I make titles in After Effects |
| Multicam | `plan_multicam_angle_switches`, `apply_stacked_camera_edit` | Never used | |
| Auto reframe | `auto_reframe_sequence`, `plan_active_speaker_reframe` | Never used | |
| Find cuts in a clip | `scene_edit_detection`, `detect_scene_edits` | Never used | |
| Remove silence and filler words | `detect_silence`, `plan_filler_word_removal`, `plan_pause_tightening` | Never used | |
| Shot matching | `plan_shot_match`, `color_correct` | Never used | I match with my own measurements |
| Scopes | `read_video_scopes`, `inspect_rendered_video_scopes` | Never used | I measure exported frames with Python |
| Loudness | `analyze_loudness`, `normalize_loudness_file` | Never used | I use my script `print_loudness.py` |
| Quality checks | `analyze_video_qc`, `audit_timeline_health`, `validate_project_for_export`, `verify_delivery_file` | Never used | |
| Markers | `add_marker`, `list_markers`, `export_review_markers` | Never used | |
| Bins, labels, metadata | `create_bin`, `set_color_label`, `set_metadata` | Never used | |
| Nest, freeze frame, reverse, speed | `nest_clips`, `freeze_frame`, `reverse_clip`, `speed_change` | Never used | For speed I use the frame rate method |
| Crop, opacity, blend mode | `crop_clip`, `set_clip_opacity`, `set_blend_mode` | Never used | |
| Undo | `undo`, `multiple_undo`, `redo` | Never used | I work on a new sequence copy |
| Render queue and batch encode | `add_to_render_queue`, `start_batch_encode`, `encode_file` | Never used | |
| Send to other apps | `export_sequence_edl`, `export_as_fcp_xml`, `export_aaf`, `export_omf` | Never used | |
| Second Premiere connector | Higgsfield `pr_*` tools | Never used | |
| Newer plugin route | UXP backend | Never used | I use the CEP route |

Corrections to this table from session 76 (2026-11-18):
- "Negative values sometimes fail once, I retry" is wrong. A retry of the same value often fails
  again. See Appendix B.
- `speed_change`, `set_clip_speed_qe` and `reverse_clip` are not "never used". They are stubs in
  the server and never reach Premiere. Real Optical Flow slow motion is closed for that reason.
- `create_source_freeze_frame` is in use.

## Appendix B: where the working calls are written down

The film-edit session's notes hold the call, the value format and the measured proof for each
known fix. The builder reads them before building a recipe.

- `~/.claude/skills/cinematic-story-edit/references/premiere-recipes.md` (lines 1 to 115): recovery,
  grouping, project safety, frame-exact placement, transitions, effects, the Lumetri index map,
  the adjustment layer template, proxies, export.
- `~/.claude/skills/cinematic-story-edit/references/color-mode.md` (sections 9 to 11).
- `/Volumes/Samsung 2TB/Videos Premiere/Claude templates/Premiere 2027 tests/results.md`:
  numbers and scripts of the 27.0.0 tests.
- The memory notes of the Premiere MCP project: `premiere-mcp-connector-quirks.md`,
  `premiere-2027-tests.md`, `color-mode-premiere-27.md`, `live-grading-workflow.md`.

Facts the milestone 1 recipes need:

| Fact | Value |
|---|---|
| Lumetri index map (26.5.2, unchanged on 27.0.0) | 2 Basic Correction header, 4 hidden Input LUT path, 6 Input LUT menu, 14 Temperature, 15 Tint, 16 Saturation, 19 Exposure, 20 Contrast, 21 Highlights, 22 Shadows, 23 Whites, 24 Blacks, 32 hidden Look path, 34 Look menu, 38 Creative Intensity, 40 Faded Film, 42 Vibrance, 110 Vignette Amount |
| Live LUT | Lumetri Color applied after ProcAmp. Index 4 = full `.cube` path plus a trailing `/` (a string), then index 6 = 1. Never index 7. Pixel-verified on 26.5.2 against ffmpeg `lut3d` (0.29/255); works on 27.0.0. In a converted project index 6 reads 0 while the LUT renders. Index 6 = 0 does not remove a LUT that came in by copy: remove the effect and apply a new one. |
| LUT menus | Index 6 (max 12) and index 34 (max 79) list only files directly in `~/Library/Application Support/Adobe/Common/LUTs/Technical` and `.../Creative`, read at launch, no subfolders |
| Negative values | Error "Illegal Parameter type". Seen on ProcAmp Brightness and Lumetri Temperature, Tint, Vibrance, Blacks. Positive and zero writes never failed (over 100). A retry of the same value often failed again. |
| Two effects with the same name on one clip | The MCP tools reach only the first one |
| Proxies | The MCP tool `manage_proxies` (action toggle) returns `previousEnabled`; restore that value |
| Frame-exact times | in = in_f/F + 0.001, out = (in_f + dur_f)/F + 0.001 (out is exclusive), start = tl_f/F + 0.001, F = sequence fps |
| Adjustment layer template | `/Volumes/Samsung 2TB/Videos Premiere/Claude templates/Adjustment layer template.prproj`, sequence `f2b069f6-9aa0-4ad5-8efc-8a31a6585cfc` |
