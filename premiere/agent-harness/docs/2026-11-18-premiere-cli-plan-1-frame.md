# Premiere CLI, Plan 1: the frame and the gate tests

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `cli-anything-premiere` a live connection to the running Premiere 27 (own scripts, MCP pass-through, menu clicks), with `doctor` and `recovery show`, and run the five gate tests that decide how the recipes are built.

**Architecture:** New modules sit next to the reader and are loaded only when a new command runs. `core/bridge.py` speaks the file protocol of the installed CEP panel under the server's own lease rules. `core/mcp_client.py` runs `premiere-pro-mcp` over stdio. `core/ui.py` clicks menus with `osascript`. Python holds the lease and runs every loop; each script sent to Premiere does one small thing.

**Tech Stack:** Python 3.10+, click, pytest, ExtendScript (ES3), `osascript`, Node (only to unit-test the script helpers; tests skip without it).

**Spec:** `premiere/agent-harness/docs/2026-11-18-premiere-cli-design.md` (approved 2026-11-18). Read it first.

**Scope:** This plan covers milestone 1 of the spec up to the gate tests. The five recipes and the document updates are Plan 2, written after the gate results, because gate G1 can send the transport choice back to Urko and G2 and G4 change how the recipes work. One order differs from the spec: the MCP client is built before the gates, because G1, G2, G3 and G5 need a server process that stays open.

All paths below are relative to `premiere/agent-harness/`. Run every command from that folder.

## Global Constraints

- No `git commit` and no `git push`. Urko has not asked for either. Each task ends with a full test run in place of a commit.
- The file route belongs to session `premier-pro-mcp-76`. Do not change `core/prproj.py`, `tests/test_core.py`, `tests/test_full_e2e.py`, `tests/_helpers.py`. The 24 unit tests and 14 end-to-end tests of the reader stay green and unchanged.
- The command is installed editable and session 76 uses it while you work. `premiere_cli.py` must import no new module at import time. A fault in new code must not break `status`, `project`, `sequence`, `clip`, `colormode`.
- The version stays `0.1.0` and the root help keeps the sentence "Read Premiere Pro project files" (existing tests assert both). `status` keeps every key it has; add keys only.
- Output contract: `--json` on every command; success `{"ok": true, ...}`; failure `{"ok": false, "error": "...", "code": "..."}` and exit status 1. Use `CliError(code, message)` from `errors.py`.
- Live commands support Premiere 27.0.x only. A write refuses on any other running version.
- The CLI never sets `unsafe-script` and never acknowledges a recovery by itself.
- Never run an MCP call while this process holds the lease. The server it starts waits for the same lease.
- ExtendScript is ES3: no `let`, `const`, arrow functions, template strings, `JSON.*`, `Object.keys`, `Array.prototype.forEach/map/filter/indexOf`, `String.prototype.trim`.
- Arguments enter a script as one JSON literal from `json.dumps(args, ensure_ascii=True)`. No string joins.
- A script sent to the panel is at most 500 KB (`MAX_BRIDGE_SCRIPT_BYTES = 500 * 1024` in the server).
- Python: no `multiprocessing`, `Pool` or `ProcessPoolExecutor`. Threads only.
- Prose in help texts and documents: plain words, no em-dashes.
- For every host call in a `.jsx` file, find the same call in the server's own tool code under `/opt/homebrew/lib/node_modules/premiere-pro-mcp/dist/tools/` (`grep -rl "<tool_name>" dist/tools`) and follow it. Do not write Premiere API calls from memory.

## Review Focus

1. A project or sequence name with quotes, a backslash, a newline or non-ASCII letters. Expected: it travels through `ARGS` and back through the reply unchanged. Test in Task 2.
2. Premiere 26.5.2 is the running app (two versions are installed). Expected: read commands warn, write commands refuse with `untested_premiere`, and the version comes from the running app, not from the newest folder in `/Applications`. Test in Task 3.
3. No project open, or no active sequence. Expected: `live state` succeeds with empty values; a write refuses with `no_project`. Tests in Tasks 3 and 4.
4. A dialog is open in Premiere, so a script never answers. Expected: the message "Premiere does not answer, a dialog may be open"; no recovery marker for a read; no stale lease. Test in Task 1.
5. A reply file that is empty, cut off, or larger than 1 MB. Expected: `bad_reply`, lease released, own files removed. Test in Task 1.

---

## File structure

| File | Responsibility |
|---|---|
| `cli_anything/premiere/core/bridge.py` (new) | Bridge folder checks, heartbeat, lease, recovery marker, send and wait |
| `cli_anything/premiere/core/jsx.py` (new) | Build a script from a body and arguments; load `.jsx` files |
| `cli_anything/premiere/core/scripts/helpers.jsx` (new) | ES3 helpers: `__json`, `__ok`, `__fail` |
| `cli_anything/premiere/core/scripts/state.jsx`, `summary.jsx` (new) | Read-only state scripts |
| `cli_anything/premiere/core/guard.py` (new) | Version guard and write rules 1 and 2 of the spec |
| `cli_anything/premiere/core/mcp_client.py` (new) | One `premiere-pro-mcp` process over stdio |
| `cli_anything/premiere/core/ui.py` (new) | Running-app lookup, `Window > Mode` get and set |
| `cli_anything/premiere/core/doctor.py` (new) | The checks of `doctor` and the data of `recovery show` |
| `cli_anything/premiere/live_cli.py` (new) | click groups `doctor`, `recovery`, `live`, `mcp`, `ui` |
| `cli_anything/premiere/premiere_cli.py` (modify) | Register the new groups lazily; one added key in `status` |
| `cli_anything/premiere/tests/fakes.py` (new) | `FakePanel` thread and the fake MCP server script |
| `cli_anything/premiere/tests/test_bridge.py`, `test_jsx.py`, `test_guard.py`, `test_mcp_client.py`, `test_ui.py`, `test_doctor.py`, `test_live_cli.py` (new) | Unit tests, no Premiere |
| `cli_anything/premiere/tests/test_live_e2e.py` (new) | End-to-end tests on the running Premiere 27 |
| `cli_anything/premiere/tests/gates/run_gates.py` (new) | The five gate tests; prints a result table |
| `setup.py` (modify) | Add `core/scripts/*.jsx` to `package_data` |

Test command for unit tests: `python3 -m pytest cli_anything/premiere/tests -q --ignore=cli_anything/premiere/tests/test_live_e2e.py`
Full suite: `python3 -m pytest cli_anything/premiere/tests -q`

---

### Task 1: Bridge client

**Files:**
- Create: `cli_anything/premiere/core/bridge.py`
- Create: `cli_anything/premiere/tests/fakes.py`
- Test: `cli_anything/premiere/tests/test_bridge.py`

**Interfaces:**
- Consumes: `CliError` from `cli_anything.premiere.errors`.
- Produces:

```python
BRIDGE_DIR_NAME = "premiere-mcp-bridge"
HEARTBEAT_STALE_S = 3.0
MAX_SCRIPT_BYTES = 500 * 1024
MAX_REPLY_BYTES = 1_048_576
BUSY_FRESH_S = 6.0          # a busy file younger than this means the script still runs
MAX_HOLD_S = 15.0           # a batch gives the lease back at least this often

def default_bridge_dir() -> Path
    # $PREMIERE_TEMP_DIR if set; on macOS `/usr/bin/getconf DARWIN_USER_TEMP_DIR` + BRIDGE_DIR_NAME;
    # else tempfile.gettempdir() + BRIDGE_DIR_NAME

def check_private_dir(path: Path) -> None
    # never creates. CliError("bridge_missing") if absent; CliError("bridge_unsafe") if it is a
    # symlink, not a directory, has any group/other permission bit, or another owner.

@dataclass(frozen=True)
class Heartbeat:
    protocol_version: int; command_schema_version: int; receipt_version: int; state: str; age_s: float
    @property
    def fresh(self) -> bool      # age_s <= HEARTBEAT_STALE_S and state in ("running", "waiting")

def read_heartbeat(bridge_dir: Path) -> Heartbeat | None     # None when absent or unreadable

def read_recovery(bridge_dir: Path) -> dict | None
    # None when no `.host-recovery.json`; else {"reason": str, "revision": sha256 hex of the raw file text}

def mark_recovery(bridge_dir: Path, reason: str) -> None
    # reason in ("unknown_command_outcome", "abandoned_process_lease"); no change if the file exists

class Lease:
    def __init__(self, bridge_dir: Path, wait_s: float = 30.0) -> None
    held: bool
    held_for_s: float
    def acquire(self) -> None     # CliError("lease_busy") after wait_s; RuntimeError if already held by this object
    def release(self) -> None     # CliError("lease_lost") plus recovery marker if owner.json changed
    def __enter__ / __exit__      # acquire / release

class Interrupted(Exception): ...
def install_signal_handlers() -> None      # SIGTERM raises Interrupted in the main thread

class BridgeClient:
    def __init__(self, bridge_dir: Path | None = None, lease_wait_s: float = 30.0) -> None
    bridge_dir: Path
    def lease(self) -> Lease                 # one Lease object per client; `with client.lease(): ...`
    def breathe(self) -> bool                # if held longer than MAX_HOLD_S: release, acquire again, return True
    def send(self, script: str, *, write: bool, timeout_s: float = 30.0, hard_limit_s: float = 120.0) -> object
        # needs the lease held (RuntimeError otherwise). Returns the "data" value of a success reply.
```

- `fakes.py` produces:

```python
class FakePanel:
    """A thread that plays the CEP panel in a temp bridge folder."""
    def __init__(self, bridge_dir: Path, handler: Callable[[str], str | None] = ..., delay_s: float = 0.0,
                 busy: bool = False, heartbeat: dict | None = ...) -> None
        # handler gets the script text and returns the reply text; None means "never answer".
        # Default handler returns '{"success":true,"data":{"echo":true}}'.
        # busy=True writes busy_<id>.json every 0.2 s while it waits delay_s.
        # heartbeat defaults to {"protocolVersion":1,"commandSchemaVersion":1,"receiptVersion":1,"state":"running"}
        # and is rewritten every 0.5 s; pass None for no heartbeat.
    seen: list[str]                 # scripts claimed, in order
    def start(self) -> None; def stop(self) -> None

@pytest.fixture
def bridge_dir(tmp_path) -> Path    # tmp_path / "premiere-mcp-bridge", created with mode 0o700
```

File formats the server validates. A file that fails these blocks every session:

| File | Exact content | Mode | Created with |
|---|---|---|---|
| `.host-owner-lease/` | directory | 0700 | `os.mkdir` (fails if it exists) |
| `.host-owner-lease/owner.json` | `{"version":1,"pid":<int 1..2147483647>,"token":"<1..128 chars of A-Z a-z 0-9 . _ : ->"}`, these three keys only | 0600 | exclusive create |
| `.host-recovery.json` | `{"version":1,"reason":"unknown_command_outcome" or "abandoned_process_lease","nonce":"<36 chars of a-f 0-9 ->"}`, these three keys only, at most 4096 bytes | 0600 | exclusive create |
| `.host-owner-reaper/` | directory, held only while a dead lease is removed | 0700 | `os.mkdir` |
| `cmd_<uuid4>.jsx` | the script, UTF-8 | default | write `cmd_<uuid4>.jsx.staged`, then `os.rename` |
| `res_<uuid4>.json` | written by the panel: `{"success":true,"data":...}` or `{"success":false,"error":"..."}` | | |
| `busy_<uuid4>.json` | written by the panel every 2 s after the first 2 s | | |

Use `str(uuid.uuid4())` for the token and the nonce (lower-case hex with dashes fits both patterns).

Lease rules (`Lease.acquire`), same as `dist/bridge/host-lease.js` lines 86 to 148:
1. `os.mkdir(lock, 0o700)`. On success write `owner.json`; if that write fails, remove the folder and raise.
2. On `FileExistsError`: the lock must be a real directory, mode without group/other bits, owned by this user, else `CliError("lease_unsafe")`. Read `owner.json`. If it vanished, try again. If it is invalid by the table above, `CliError("lease_unsafe")`.
3. If the owner pid is alive (`os.kill(pid, 0)` does not raise `ProcessLookupError`): sleep 25 ms and try again until `wait_s`, then `CliError("lease_busy", "Another program holds Premiere. Nothing was sent.")`.
4. If the owner pid is dead: `mark_recovery(dir, "abandoned_process_lease")`; `os.mkdir(reaper, 0o700)`; read `owner.json` again; if token and pid are the same and the pid is still dead, delete `owner.json` and the lock folder; remove the reaper folder in `finally`; try again. If the reaper folder exists, another program reaps: try again.

`Lease.release`: read `owner.json`; if it is missing or its token or pid is not ours, `mark_recovery(dir, "unknown_command_outcome")` and raise `CliError("lease_lost")`; else delete `owner.json` and the lock folder.

`send` rules:

| Case | Result |
|---|---|
| Script larger than `MAX_SCRIPT_BYTES` | `CliError("script_too_large")`, nothing written |
| `write=True` and the heartbeat is absent or not fresh (read again inside `send`) | `CliError("bridge_down")`, nothing written |
| Reply arrives, `success` true | return `data` |
| Reply arrives, `success` false | `CliError("script_failed", <error text>)`, no marker |
| Reply is not JSON, is empty, or exceeds `MAX_REPLY_BYTES` | `CliError("bad_reply")`; marker if `write` |
| No reply at `timeout_s` and no `busy_` file younger than `BUSY_FRESH_S` | give up (next rows) |
| `busy_` file fresh | keep waiting until `hard_limit_s`, then give up |
| Give up or `KeyboardInterrupt`/`Interrupted`, and `os.unlink(cmd file)` succeeds | the panel never claimed it: `CliError("not_applied")`, no marker |
| Give up, command was claimed, `write=False` | `CliError("timeout", "Premiere does not answer, a dialog may be open.")`, no marker |
| Give up, command was claimed, `write=True` | `mark_recovery(dir, "unknown_command_outcome")`, `CliError("unknown_outcome", same dialog sentence)` |
| Interrupt, command was claimed | `CliError("interrupted")` for a read; marker plus `CliError("unknown_outcome")` for a write |

In every case `send` removes its own `.staged`, `cmd_`, `res_` and `busy_` files in `finally`. It never touches files of another id. The "not applied" row is an addition to the spec: a command file that the CLI can still delete was never run, so no marker is needed (a marker costs a film session about 30k tokens).

- [ ] **Step 1: Write `fakes.py`** with `FakePanel` and the `bridge_dir` fixture as specified above. The panel claims a command the way the real one does: `os.rename(cmd, cmd + ".<id>.claimed")`, read, delete, wait `delay_s`, write `res_<id>.json.staged`, rename to `res_<id>.json`.

- [ ] **Step 2: Write the failing tests** in `test_bridge.py`:

```python
def test_default_dir_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PREMIERE_TEMP_DIR", str(tmp_path / "x"))
    assert bridge.default_bridge_dir() == tmp_path / "x"

def test_check_private_dir(bridge_dir, tmp_path):
    bridge.check_private_dir(bridge_dir)                       # passes
    with pytest.raises(CliError) as e: bridge.check_private_dir(tmp_path / "none")
    assert e.value.code == "bridge_missing"
    bridge_dir.chmod(0o755)
    with pytest.raises(CliError) as e: bridge.check_private_dir(bridge_dir)
    assert e.value.code == "bridge_unsafe"
    link = tmp_path / "link"; link.symlink_to(tmp_path)
    with pytest.raises(CliError) as e: bridge.check_private_dir(link)
    assert e.value.code == "bridge_unsafe"

def test_lease_files_have_the_server_format(bridge_dir):
    lease = bridge.Lease(bridge_dir)
    lease.acquire()
    lock = bridge_dir / ".host-owner-lease"; owner = lock / "owner.json"
    data = json.loads(owner.read_text())
    assert set(data) == {"version", "pid", "token"} and data["version"] == 1 and data["pid"] == os.getpid()
    assert re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", data["token"])
    assert stat.S_IMODE(lock.stat().st_mode) == 0o700 and stat.S_IMODE(owner.stat().st_mode) == 0o600
    lease.release()
    assert not lock.exists() and bridge.read_recovery(bridge_dir) is None

def test_lease_waits_then_gets_it(bridge_dir):          # another live owner releases after 0.3 s
def test_lease_busy_past_the_wait(bridge_dir):          # code "lease_busy", lock untouched, no marker
def test_dead_owner_is_reaped_with_a_marker(bridge_dir):
    # owner.json with the pid of a finished subprocess -> acquire succeeds,
    # read_recovery(...)["reason"] == "abandoned_process_lease", no ".host-owner-reaper" left
def test_invalid_owner_record_is_refused(bridge_dir):   # extra key "x" -> code "lease_unsafe"
def test_release_detects_a_changed_owner(bridge_dir):   # overwrite the token -> "lease_lost" + marker

def test_recovery_marker_format(bridge_dir):
    bridge.mark_recovery(bridge_dir, "unknown_command_outcome")
    path = bridge_dir / ".host-recovery.json"; raw = path.read_text(); data = json.loads(raw)
    assert set(data) == {"version", "reason", "nonce"} and data["version"] == 1
    assert re.fullmatch(r"[a-f0-9-]{36}", data["nonce"]) and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert bridge.read_recovery(bridge_dir) == {"reason": "unknown_command_outcome",
                                               "revision": hashlib.sha256(raw.encode()).hexdigest()}
    bridge.mark_recovery(bridge_dir, "abandoned_process_lease")      # second call changes nothing
    assert path.read_text() == raw

def test_send_round_trip(bridge_dir):                   # FakePanel default handler -> {"echo": True}; folder holds only the heartbeat after
def test_send_needs_the_lease(bridge_dir):              # RuntimeError
def test_send_script_failed(bridge_dir):                # handler returns success false -> "script_failed", no marker
def test_send_too_large(bridge_dir):                    # "script_too_large", FakePanel.seen == []
def test_write_refused_without_heartbeat(bridge_dir):   # FakePanel(heartbeat=None) -> "bridge_down", seen == []
def test_read_timeout_has_no_marker_and_names_a_dialog(bridge_dir):
    # handler returns None, timeout_s=0.5 -> code "timeout", "dialog" in message, read_recovery is None, lease released
def test_write_timeout_marks_recovery(bridge_dir):      # same with write=True -> "unknown_outcome" + marker
def test_unclaimed_command_is_not_applied(bridge_dir):  # no FakePanel thread, only a heartbeat file; write=True, timeout_s=0.3 -> "not_applied", no marker
def test_busy_file_extends_the_wait(bridge_dir):        # delay_s=1.5, busy=True, timeout_s=0.5, hard_limit_s=5 -> succeeds
def test_hard_limit_stops_a_busy_script(bridge_dir):    # handler None, busy=True, timeout_s=0.3, hard_limit_s=1.0, write=True -> "unknown_outcome"
def test_bad_reply(bridge_dir):                         # handler returns "" and then "{" -> "bad_reply"
def test_interrupt_during_read_and_write(bridge_dir):
    # a thread calls signal.raise_signal(SIGINT) after 0.2 s while the handler never answers:
    # read -> "interrupted", no marker; write -> "unknown_outcome" + marker; lock folder gone in both
def test_breathe_gives_the_lease_back(bridge_dir, monkeypatch):
    # monkeypatch bridge.MAX_HOLD_S to 0.0 -> breathe() is True and the token in owner.json changed
```

- [ ] **Step 3: Run the tests and see them fail**

Run: `python3 -m pytest cli_anything/premiere/tests/test_bridge.py -q`
Expected: errors, `No module named 'cli_anything.premiere.core.bridge'`

- [ ] **Step 4: Implement `core/bridge.py`** to the interfaces and tables above.

- [ ] **Step 5: Run the tests and see them pass**

Run: `python3 -m pytest cli_anything/premiere/tests/test_bridge.py -q`
Expected: all pass

- [ ] **Step 6: Run the unit suite**

Run: `python3 -m pytest cli_anything/premiere/tests -q --ignore=cli_anything/premiere/tests/test_live_e2e.py`
Expected: all pass, the reader's tests included

---

### Task 2: Script builder and ES3 helpers

**Files:**
- Create: `cli_anything/premiere/core/jsx.py`
- Create: `cli_anything/premiere/core/scripts/helpers.jsx`, `state.jsx`, `summary.jsx`
- Modify: `setup.py` (`package_data` gets `"core/scripts/*.jsx"`)
- Test: `cli_anything/premiere/tests/test_jsx.py`

**Interfaces:**
- Produces:

```python
SCRIPTS_DIR: Path                                   # core/scripts
def load(name: str) -> str                          # text of core/scripts/<name>.jsx; CliError("script_missing")
def build(body: str, args: dict | None = None) -> str
```

`build` returns exactly this shape (helpers inside the function, so nothing leaks into Premiere's global scope):

```
(function () {
<text of helpers.jsx>
var ARGS = <json.dumps(args or {}, ensure_ascii=True)>;
try {
<body>
} catch (e) { return __fail(String(e)); }
})()
```

`helpers.jsx` defines, in ES3:
- `__json(value)`: strings (escape `"`, `\`, control characters below 0x20 as `\uXXXX`, and every character above 0x7E as `\uXXXX`), finite numbers, `true`/`false`, `null` (also for `undefined`, `NaN`, infinities and functions), arrays, plain objects (own properties only).
- `__ok(data)`: returns `__json({success: true, data: data})`.
- `__fail(message)`: returns `__json({success: false, error: String(message)})`.

A body ends with `return __ok(...)` or `return __fail(...)`.

`state.jsx` (read-only) returns:

```
{"version": "27.0.0", "build": "88",
 "projects": [{"name": "...", "path": "...", "active": true}],
 "active_sequence": {"id": "...", "name": "..."} or null,
 "playhead_ticks": "<digits>" or null,
 "selection": [{"track_type": "video"|"audio", "track": 0, "clip": 0, "node_id": "...", "name": "..."}]}
```

With no project open: `projects` is `[]`, the rest `null` and `[]`. Follow the server's `get_premiere_state` tool for the host calls.

`summary.jsx` (read-only) returns `{"project": name or null, "active_sequence": {"id","name"} or null, "video_tracks": n, "audio_tracks": n, "video_clips": n, "audio_clips": n}` and never lists clips.

- [ ] **Step 1: Write the failing tests** in `test_jsx.py`:

```python
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")

def run_in_node(script: str, prelude: str = "") -> dict:
    """Evaluate a built script in node (ES3 runs there) and parse the JSON string it returns."""
    code = prelude + "\nprocess.stdout.write(String(" + script + "));"
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout
    return json.loads(out)

def test_build_puts_args_in_as_ascii_json():
    s = jsx.build("return __ok(ARGS);", {"name": 'a"b\\c\né'})
    assert 'var ARGS = {"name": "a\\"b\\\\c\\n\\u00e9"};' in s

@needs_node
def test_args_round_trip_through_the_helpers():
    args = {"name": 'Film "v3" \\ é 日本\n', "n": -2.5, "flag": True, "none": None, "list": [1, "x", {"k": 0}]}
    assert run_in_node(jsx.build("return __ok(ARGS);", args)) == {"success": True, "data": args}

@needs_node
def test_a_thrown_error_becomes_a_failure_reply():
    assert run_in_node(jsx.build("throw new Error('boom');")) == {"success": False, "error": "Error: boom"}

@needs_node
def test_json_helper_edge_values():
    body = "return __ok({u: undefined, nan: 0/0, inf: 1/0, f: function(){}, ctl: '\\u0001'});"
    assert run_in_node(jsx.build(body))["data"] == {"u": None, "nan": None, "inf": None, "f": None, "ctl": "\x01"}

@needs_node
def test_state_script_with_no_project_open():
    prelude = "var app = {version: '27.0.0', build: '88', projects: {numProjects: 0}, project: null};"
    data = run_in_node(jsx.build(jsx.load("state")), prelude)["data"]
    assert data == {"version": "27.0.0", "build": "88", "projects": [], "active_sequence": None,
                    "playhead_ticks": None, "selection": []}

BANNED = [r"\blet\s", r"\bconst\s", r"=>", r"`", r"\bJSON\.", r"Object\.keys", r"\.forEach\(", r"\.map\(",
          r"\.filter\(", r"\.trim\(\)", r"\bclass\s", r"\.\.\."]
@pytest.mark.parametrize("path", sorted(jsx.SCRIPTS_DIR.glob("*.jsx")), ids=lambda p: p.name)
def test_scripts_use_es3_only(path):
    text = path.read_text(encoding="utf-8")
    for pattern in BANNED:
        assert not re.search(pattern, text), f"{path.name}: {pattern}"

def test_load_missing_script():
    with pytest.raises(CliError) as e: jsx.load("nope")
    assert e.value.code == "script_missing"
```

- [ ] **Step 2: Run and see them fail**

Run: `python3 -m pytest cli_anything/premiere/tests/test_jsx.py -q`
Expected: errors, `No module named 'cli_anything.premiere.core.jsx'`

- [ ] **Step 3: Implement `jsx.py`, the three `.jsx` files, and the `setup.py` line.**

- [ ] **Step 4: Run and see them pass**

Run: `python3 -m pytest cli_anything/premiere/tests/test_jsx.py -q`
Expected: all pass (node tests run; node v26 is installed)

- [ ] **Step 5: Run the unit suite** (command above). Expected: all pass.

---

### Task 3: Guards

**Files:**
- Create: `cli_anything/premiere/core/guard.py`
- Test: `cli_anything/premiere/tests/test_guard.py`

**Interfaces:**
- Consumes: `Heartbeat`, `read_recovery` from Task 1; the state dict of `state.jsx` from Task 2.
- Produces:

```python
TESTED_PREMIERE_PREFIXES = ("27.0.",)
TESTED_PANEL_VERSIONS = ("1.25.0",)
PANEL_MANIFEST = Path.home() / "Library/Application Support/Adobe/CEP/extensions/MCPBridgeCEP/CSXS/manifest.xml"

def panel_version(manifest: Path = PANEL_MANIFEST) -> str | None     # the ExtensionBundleVersion attribute

def check_bridge(heartbeat: Heartbeat | None, panel: str | None, *, write: bool, allow_untested: bool) -> list[str]
def check_state(state: dict, *, write: bool, project: str | None = None, any_project: bool = False,
                allow_untested: bool = False) -> list[str]
def check_recovery(bridge_dir: Path, *, write: bool) -> list[str]
```

Each returns warnings (strings) and raises `CliError` for a refusal.

| Function | Condition | Read command | Write command |
|---|---|---|---|
| `check_bridge` | heartbeat `None` or not fresh | `CliError("bridge_down", "Premiere is not running its MCP panel. Open Window > Extensions > MCP for Adobe Premiere Pro.")` | same |
| `check_bridge` | `protocol_version != 1` or `command_schema_version != 1`, or panel version not in `TESTED_PANEL_VERSIONS` (also `None`) | warning | `CliError("untested_bridge")`, or a warning that names the override when `allow_untested` |
| `check_state` | `state["version"]` does not start with a tested prefix | warning | `CliError("untested_premiere", "Live writes support Premiere 27.0 only. Running: <version>.")`, or warning with `allow_untested` |
| `check_state` | `projects == []` | no warning | `CliError("no_project")` |
| `check_state` | more than one project and (`project` is `None` or differs from the active project's path) | warning | `CliError("two_projects")` when `project` is `None`; `CliError("project_mismatch")` when it differs |
| `check_state` | one project, `project` given and differs from the active path | warning | `CliError("project_mismatch")` |
| `check_state` | active project file name does not contain "claude" (case-insensitive) | no warning | `CliError("project_guard", "This is not a Claude working copy: <name>.")` unless `any_project` is true AND `project` equals the active path; then a warning "project guard overridden" |
| `check_recovery` | `.host-recovery.json` exists | warning | `CliError("recovery_required", "A recovery is open. Run: cli-anything-premiere recovery show")` |

Paths are compared after `os.path.normpath`.

- [ ] **Step 1: Write the failing tests** in `test_guard.py`. One test per row and column of the table, with these fixed inputs:

```python
def state(version="27.0.0", projects=(("Fall Claude.prproj", "/p/Fall Claude.prproj", True),)):
    return {"version": version, "build": "88",
            "projects": [{"name": n, "path": p, "active": a} for n, p, a in projects],
            "active_sequence": None, "playhead_ticks": None, "selection": []}

def test_write_on_premiere_26_is_refused():
    with pytest.raises(CliError) as e: guard.check_state(state(version="26.5.2"), write=True)
    assert e.value.code == "untested_premiere" and "26.5.2" in e.value.message
    assert guard.check_state(state(version="26.5.2"), write=False)            # one warning, no raise

@pytest.mark.parametrize("name", ["20251227 Sno Hamptons Claude 2.prproj", "20260523 Claude tests.prproj",
                                  "20261115 Fall Claude_1.prproj", "20261006 Ander home dinner claude.prproj"])
def test_working_copy_names_pass_the_project_guard(name):
    assert guard.check_state(state(projects=((name, "/p/" + name, True),)), write=True) == []

def test_own_project_is_refused_and_needs_both_flags():
    s = state(projects=(("Family film.prproj", "/p/Family film.prproj", True),))
    for kwargs in ({}, {"any_project": True}, {"project": "/p/Family film.prproj"}):
        with pytest.raises(CliError) as e: guard.check_state(s, write=True, **kwargs)
        assert e.value.code == "project_guard"
    w = guard.check_state(s, write=True, any_project=True, project="/p/Family film.prproj")
    assert any("overridden" in x for x in w)

def test_two_projects_need_the_project_path(): ...        # "two_projects", then "project_mismatch", then pass
def test_no_project_refuses_a_write_only(): ...           # "no_project"; read returns []
def test_bridge_down(): ...                               # None and a Heartbeat with age_s=10 -> "bridge_down" for read and write
def test_untested_panel(): ...                            # panel "1.26.0": read warns, write "untested_bridge", allow_untested warns
def test_recovery_blocks_writes(bridge_dir): ...          # after mark_recovery: write "recovery_required", read one warning
def test_panel_version_reads_the_manifest(tmp_path):      # a manifest with ExtensionBundleVersion="1.25.0" -> "1.25.0"; missing file -> None
```

- [ ] **Step 2: Run and see them fail.** Run: `python3 -m pytest cli_anything/premiere/tests/test_guard.py -q`. Expected: import error.
- [ ] **Step 3: Implement `core/guard.py`.**
- [ ] **Step 4: Run and see them pass.** Same command. Expected: all pass.
- [ ] **Step 5: Run the unit suite.** Expected: all pass.

---

### Task 4: `live state`, `live run`, and lazy registration

**Files:**
- Create: `cli_anything/premiere/live_cli.py`
- Modify: `cli_anything/premiere/premiere_cli.py` (add `LazyGroup`, register five groups, add `REPL_COMMANDS` lines; nothing else)
- Test: `cli_anything/premiere/tests/test_live_cli.py`

**Interfaces:**
- Consumes: `BridgeClient`, `install_signal_handlers` (Task 1); `jsx.build`, `jsx.load` (Task 2); `guard.*` (Task 3); `_emit`, `_fail`, `json_option` from `premiere_cli.py` (import them inside `live_cli.py`; they exist today).
- Produces in `premiere_cli.py`:

```python
class LazyGroup(click.Group):
    """A group whose commands live in another module that is imported on first use."""
    def __init__(self, *args, import_path: str, **kwargs)     # "cli_anything.premiere.live_cli:live"
    # list_commands and get_command import the target group and delegate to it

cli.add_command(LazyGroup(name="live",     import_path="cli_anything.premiere.live_cli:live",     help="Read and change the running Premiere 27 with scripts."))
cli.add_command(LazyGroup(name="mcp",      import_path="cli_anything.premiere.live_cli:mcp",      help="Call any tool of the premiere-pro-mcp server."))
cli.add_command(LazyGroup(name="ui",       import_path="cli_anything.premiere.live_cli:ui",       help="Click Premiere menus (Window > Mode)."))
cli.add_command(LazyGroup(name="recovery", import_path="cli_anything.premiere.live_cli:recovery", help="Show an open recovery of the bridge."))
cli.add_command(LazyGroup(name="doctor",   import_path="cli_anything.premiere.live_cli:doctor",   help="Check the connection to the running Premiere. Changes nothing."))
```

`doctor` is a group with `invoke_without_command=True` so that `cli-anything-premiere doctor` runs the checks. In this task `mcp`, `ui`, `recovery` and `doctor` exist in `live_cli.py` as empty groups; Tasks 5 to 7 fill them.

- Produces in `live_cli.py`:

```python
def make_client(lease_wait: float) -> BridgeClient           # tests monkeypatch this to point at a temp folder
def run_script(client, body: str, args: dict | None, *, write: bool, timeout_s: float, hard_limit_s: float) -> object
    # jsx.build + client.send; the caller holds the lease
def guarded_state(client, *, write, project, any_project, allow_untested) -> tuple[dict, list[str]]
    # inside the lease: check_bridge, check_recovery, run state.jsx (a read), check_state; returns (state, warnings)
```

Shared options for every `live` command: `--lease-wait SECONDS` (default 30), `--timeout SECONDS` (default 120, the hard limit), `--allow-untested`.

`live state`: takes the lease, runs `guarded_state(write=False)`, prints the state dict plus `"warnings": [...]`.

`live run SCRIPT.jsx [--args JSON] [--write] [--verify CHECK.jsx] [--project PATH] [--any-project]`:
- `SCRIPT.jsx` holds a body (it ends with `return __ok(...)`). `--args` is a JSON object; anything else gives `CliError("bad_args")`.
- Without `--write`: `guarded_state(write=False)`, then the script with `write=False`.
- With `--write`: `guarded_state(write=True, ...)`, then the script with `write=True`.
- With `--verify`: after the first script, in the same lease, run the check script as a read. Its data must be an object with a boolean `stored`; else `CliError("bad_verify")`.
- Output: `{"ok": true, "data": <script data>, "stored": null | true | false, "rendered": null, "warnings": [...]}`. Exit status 1 when `stored` is false (print the payload with `"ok": false, "code": "not_stored"`).
- `--verify` without `--write` is allowed.

- [ ] **Step 1: Write the failing tests** in `test_live_cli.py` (use `FakePanel` with a handler that inspects the script text, and `monkeypatch.setattr(live_cli, "make_client", lambda wait: BridgeClient(bridge_dir, wait))`; also monkeypatch `guard.panel_version` to return `"1.25.0"`):

```python
STATE_OK = {"version": "27.0.0", "build": "88", "projects": [{"name": "X Claude.prproj", "path": "/p/X Claude.prproj", "active": True}],
            "active_sequence": {"id": "s1", "name": "Main"}, "playhead_ticks": "0", "selection": []}

def test_importing_the_cli_loads_no_live_module():
    # `status` is not in this check: Task 7 lets it load core.bridge inside a try block.
    code = ("import sys; import cli_anything.premiere.premiere_cli as c; c.dispatch(['--help']); c.dispatch(['sequence','--help']);"
            "bad=[m for m in sys.modules if m.startswith('cli_anything.premiere.') and m.split('.')[-1] in"
            " ('bridge','jsx','guard','mcp_client','ui','doctor','live_cli')]; print(bad)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip().splitlines()[-1] == "[]"

def test_a_broken_live_module_does_not_break_the_reader(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "cli_anything.premiere.live_cli", None)      # import raises ImportError
    assert dispatch(["--json", "status"]) == 0

def test_root_help_lists_the_new_groups(capsys):          # "live", "mcp", "ui", "recovery", "doctor" in the output, and "Read Premiere Pro project files"
def test_live_state(...):                                 # payload equals STATE_OK plus "warnings": []
def test_live_state_with_no_project(...):                 # projects [] -> exit 0
def test_live_run_read(...):                              # data from the handler, stored None, rendered None; two scripts seen (state, body)
def test_live_run_write_refused_on_own_project(...):      # state with "Family film.prproj" -> exit 1, code "project_guard", only the state script was seen
def test_live_run_write_refused_on_premiere_26(...):      # code "untested_premiere"
def test_live_run_write_refused_with_recovery(...):       # code "recovery_required", nothing seen at all
def test_live_run_verify_false_exits_1(...):              # check returns {"stored": false} -> exit 1, code "not_stored"
def test_live_run_bad_args(...):                          # --args '[1]' -> "bad_args", nothing seen
def test_lease_is_free_after_every_command(...):          # no ".host-owner-lease" after success and after each refusal
```

- [ ] **Step 2: Run and see them fail.** Run: `python3 -m pytest cli_anything/premiere/tests/test_live_cli.py -q`. Expected: failures (no `live` command).
- [ ] **Step 3: Implement `LazyGroup` and the registrations in `premiere_cli.py`, and `live_cli.py`.** Call `install_signal_handlers()` at the start of each live command.
- [ ] **Step 4: Run and see them pass.** Same command. Expected: all pass.
- [ ] **Step 5: Run the full suite** (`python3 -m pytest cli_anything/premiere/tests -q`). Expected: all pass, the reader's 38 included.

---

### Task 5: MCP client and the `mcp` commands

**Files:**
- Create: `cli_anything/premiere/core/mcp_client.py`
- Modify: `cli_anything/premiere/live_cli.py` (fill the `mcp` group), `cli_anything/premiere/tests/fakes.py` (add the fake server)
- Test: `cli_anything/premiere/tests/test_mcp_client.py`

**Interfaces:**
- Consumes: `BridgeClient` (Task 1).
- Produces:

```python
PROTOCOL_VERSION = "2025-06-18"

class McpSession:
    def __init__(self, client: BridgeClient, command: list[str] | None = None, env: dict | None = None) -> None
        # command defaults to ["premiere-pro-mcp"]; CliError("mcp_missing") from start() when it is not on PATH
    def start(self) -> dict                  # {"name": ..., "version": ...}; takes the lease, spawns, waits for the
                                             # initialize reply, sends notifications/initialized, releases the lease
    def list_tools(self) -> list[dict]       # follows nextCursor until it is absent
    def call(self, name: str, arguments: dict, timeout_s: float = 120.0) -> dict
        # {"is_error": bool, "structured": <structuredContent or None>, "text": <joined text content>}
    def close(self) -> None                  # closes stdin, waits 5 s, kills
    def __enter__ / __exit__                 # start / close
```

Rules:
- Messages are JSON-RPC 2.0, one JSON object per line on stdin and stdout. Ignore stdout lines that are not JSON or carry another id. Read stderr in a thread so the pipe cannot fill.
- The child environment is `os.environ` plus `env`. If `PREMIERE_MCP_CAPABILITIES` contains the token `unsafe-script`, remove that token.
- `start()` raises `RuntimeError` if this process already holds the lease.
- `call` raises `CliError("mcp_timeout")` at `timeout_s`, `CliError("mcp_died")` when the process ends, `CliError("mcp_error", <message>)` for a JSON-RPC error.

Fake server in `fakes.py`: `FAKE_MCP_SERVER: str`, Python source that speaks the same protocol. On start it checks that `<bridge>/.host-owner-lease` exists and stores the answer; tools: `ping` (returns `{"connected": true}` in `structuredContent`), `echo` (returns its arguments), `fail` (returns `isError: true`), `lease_seen_at_start` (returns the stored answer), `env` (returns `PREMIERE_MCP_CAPABILITIES`). `tools/list` answers in two pages. Helper `fake_mcp_command(tmp_path, bridge_dir) -> list[str]` writes the source to a file and returns `[sys.executable, path, str(bridge_dir)]`.

`mcp` commands (each has `--lease-wait`):

| Command | Output payload | Failure |
|---|---|---|
| `mcp list [--query TEXT]` | `{"tools": [{"name", "description"}], "count": n}`; `--query` keeps tools whose name or description contains the text, case-insensitive | |
| `mcp describe TOOL` | `{"name", "description", "input_schema"}` | `CliError("tool_not_found")` |
| `mcp call TOOL [--args JSON] [--timeout SECONDS]` | `{"tool", "result": structured or text, "stored": null, "rendered": null}` | tool `is_error` -> exit 1, code `tool_failed`, the tool's text in `error` |
| `mcp batch FILE [--timeout SECONDS]` | FILE is a JSON array of `{"tool": str, "args": object}`. Payload `{"results": [...one per call that ran...], "ran": n, "total": n}` | stops at the first `is_error`: exit 1, code `tool_failed`, payload also holds `results`, `ran`, `total`; a bad file gives `CliError("bad_batch")` and nothing runs |

`make_session(client) -> McpSession` in `live_cli.py` builds the session; tests monkeypatch it to use the fake server.

- [ ] **Step 1: Add the fake server to `fakes.py`.**
- [ ] **Step 2: Write the failing tests** in `test_mcp_client.py`:

```python
def test_start_holds_the_lease_and_releases_it(bridge_dir, tmp_path):
    with McpSession(BridgeClient(bridge_dir), fake_mcp_command(tmp_path, bridge_dir)) as s:
        assert s.call("lease_seen_at_start", {})["structured"] == {"seen": True}
        assert not (bridge_dir / ".host-owner-lease").exists()

def test_list_tools_follows_pages(...):            # 5 tool names
def test_call_returns_structured_and_text(...):    # echo {"a": 1} -> structured {"a": 1}, is_error False
def test_tool_error_is_reported(...):              # fail -> is_error True
def test_unsafe_script_is_never_passed_on(...):    # env {"PREMIERE_MCP_CAPABILITIES": "inspect,unsafe-script,edit"} -> tool env returns "inspect,edit"
def test_missing_command(...):                     # command ["no-such-premiere-mcp"] -> "mcp_missing"
def test_server_death_is_reported(...):            # kill the child, then call -> "mcp_died"
def test_start_refuses_when_the_lease_is_held(...):# RuntimeError
def test_mcp_call_command(...):                    # dispatch(["--json","mcp","call","echo","--args",'{"a":1}']) -> ok, result {"a":1}, stored None
def test_mcp_call_tool_failure_exits_1(...):       # code "tool_failed"
def test_mcp_batch_stops_at_first_failure(...):    # [echo, fail, echo] -> exit 1, ran 2, total 3
def test_mcp_batch_bad_file(...):                  # "{}" -> "bad_batch"
def test_mcp_list_query_and_describe(...):         # --query PING -> 1 tool; describe nope -> "tool_not_found"
```

- [ ] **Step 3: Run and see them fail.** Run: `python3 -m pytest cli_anything/premiere/tests/test_mcp_client.py -q`. Expected: import error.
- [ ] **Step 4: Implement `core/mcp_client.py` and the `mcp` group.**
- [ ] **Step 5: Run and see them pass.** Same command. Expected: all pass.
- [ ] **Step 6: Run the unit suite.** Expected: all pass.

---

### Task 6: Screen route (`ui mode`)

**Files:**
- Create: `cli_anything/premiere/core/ui.py`
- Modify: `cli_anything/premiere/live_cli.py` (fill the `ui` group)
- Test: `cli_anything/premiere/tests/test_ui.py`

**Interfaces:**
- Produces:

```python
MODES = ("import", "edit", "color", "export")          # menu titles are these words capitalized

def running_premiere(ps_output: str | None = None) -> dict | None
    # parses `ps -axo pid=,comm=`; matches a path that ends with
    # "/Adobe Premiere Pro <year>.app/Contents/MacOS/Adobe Premiere Pro <year>";
    # returns {"pid": int, "process_name": "Adobe Premiere Pro 2027", "app_path": ".../Adobe Premiere Pro 2027.app"};
    # None when none runs; CliError("two_premieres") when two versions run

def mode_get(process_name: str, run: Callable[[str], str] = run_osascript) -> str
def mode_set(process_name: str, mode: str, run=run_osascript, sleep=time.sleep, watch_s: int = 5) -> dict
    # {"mode": mode, "reverted_once": bool}
def run_osascript(source: str) -> str                 # CliError("no_accessibility") / CliError("ui_failed")
```

AppleScript for `mode_get` (returns the title of the checked item, or `none`):

```applescript
tell application "System Events"
	tell process "<process_name>"
		set mm to menu 1 of menu item "Mode" of menu 1 of menu bar item "Window" of menu bar 1
		repeat with n in {"Import", "Edit", "Color", "Export"}
			set mc to value of attribute "AXMenuItemMarkChar" of menu item (n as text) of mm
			if mc is not missing value then return (n as text)
		end repeat
	end tell
end tell
return "none"
```

AppleScript for the click:

```applescript
tell application "System Events" to tell process "<process_name>" to click menu item "<Title>" of menu 1 of menu item "Mode" of menu 1 of menu bar item "Window" of menu bar 1
```

The process name goes into the source through `json.dumps` (AppleScript string syntax accepts it for these names).

`mode_set` rule: click; read the mode once per second for `watch_s` seconds; if a reading differs from `mode`, click once more, set `reverted_once`, and watch `watch_s` seconds again; if a reading differs again, `CliError("mode_reverted")`.

`run_osascript`: runs `/usr/bin/osascript -e <source>` with a 30 s timeout. `CliError("no_accessibility", "Give the terminal Accessibility permission: System Settings > Privacy & Security > Accessibility.")` when stderr holds "assistive access", "-1719" or "-25211"; any other non-zero exit is `CliError("ui_failed", <stderr>)`.

Commands: `ui mode get` -> `{"mode": "color"}`; `ui mode set MODE` -> `{"mode": "color", "reverted_once": false}`; no running Premiere -> `CliError("premiere_not_running")`.

- [ ] **Step 1: Write the failing tests** in `test_ui.py` (a stub `run` that records sources and returns queued answers; a stub `sleep`):

```python
PS = " 7450 /Applications/Adobe Premiere Pro 2027/Adobe Premiere Pro 2027.app/Contents/MacOS/Adobe Premiere Pro 2027\n  812 /usr/sbin/cfprefsd\n"
def test_running_premiere_is_the_running_one():
    assert ui.running_premiere(PS) == {"pid": 7450, "process_name": "Adobe Premiere Pro 2027",
        "app_path": "/Applications/Adobe Premiere Pro 2027/Adobe Premiere Pro 2027.app"}
    assert ui.running_premiere(" 812 /usr/sbin/cfprefsd\n") is None
def test_two_running_versions_are_refused(): ...     # 2026 and 2027 lines -> "two_premieres"
def test_mode_get_lowercases_the_title(): ...         # run returns "Color\n" -> "color"
def test_mode_set_stable(): ...                       # 1 click, 5 reads of "Color" -> reverted_once False
def test_mode_set_reverts_once(): ...                 # reads Color, Edit, then Color x5 -> 2 clicks, reverted_once True
def test_mode_set_reverts_twice(): ...                # -> "mode_reverted"
def test_mode_set_rejects_unknown_mode(): ...         # "grade" -> CliError("bad_mode"), no osascript call
def test_no_accessibility_message(monkeypatch): ...   # subprocess.run stub: returncode 1, stderr "... not allowed assistive access. (-25211)" -> "no_accessibility"
def test_ui_command_without_premiere(monkeypatch, capsys): ...   # running_premiere -> None: exit 1, code "premiere_not_running"
```

- [ ] **Step 2: Run and see them fail.** Run: `python3 -m pytest cli_anything/premiere/tests/test_ui.py -q`. Expected: import error.
- [ ] **Step 3: Implement `core/ui.py` and the `ui` group.**
- [ ] **Step 4: Run and see them pass.** Expected: all pass.
- [ ] **Step 5: Run the unit suite.** Expected: all pass.

---

### Task 7: `doctor`, `recovery show`, and the added key in `status`

**Files:**
- Create: `cli_anything/premiere/core/doctor.py`
- Modify: `cli_anything/premiere/live_cli.py` (fill `doctor` and `recovery`), `cli_anything/premiere/premiere_cli.py` (`status` only)
- Test: `cli_anything/premiere/tests/test_doctor.py`

**Interfaces:**
- Consumes: everything from Tasks 1 to 6.
- Produces:

```python
def run_checks(client: BridgeClient, *, premiere=ui.running_premiere, panel=guard.panel_version,
               which=shutil.which, osascript=ui.run_osascript) -> list[dict]
    # each {"name": str, "status": "pass" | "warn" | "fail", "detail": str}
def recovery_view(client: BridgeClient) -> dict
```

Checks, in this order:

| name | pass | warn | fail |
|---|---|---|---|
| `premiere_running` | one running app; detail names it | | none runs, or two run |
| `bridge_folder` | `check_private_dir` passes | | missing or unsafe |
| `panel_heartbeat` | fresh; detail has the age | | absent or stale; detail has the fix sentence of `check_bridge` |
| `bridge_protocol` | both versions are 1 | other values | |
| `panel_version` | in `TESTED_PANEL_VERSIONS` | another version, or manifest not found | |
| `lease` | free | held by a live pid (detail has the pid) | held by a dead pid, or record invalid |
| `recovery` | none | | open; detail has the reason and "run: cli-anything-premiere recovery show" |
| `premiere_version` | starts with a tested prefix (from `state.jsx`) | another version: "read only" | |
| `projects` | one project open; detail has its name | none open; more than one open | |
| `project_guard` | active project name contains "claude" | it does not: "writes need --any-project and --project" | |
| `mcp_server` | `premiere-pro-mcp` on PATH | not found: "the mcp commands will not work" | |
| `accessibility` | a `mode_get` call works | | `no_accessibility`: detail has the fix |

`doctor` never takes the lease when `lease` is held by a live pid for more than 3 s: it then marks `premiere_version`, `projects` and `project_guard` as `warn` with the detail "not checked, lease busy". It never reaps a dead lease and never writes a marker (it builds its own read of `owner.json`; it does not call `Lease.acquire` when the owner is dead). The three state checks are skipped with "not checked" when `panel_heartbeat` failed.

`doctor` output: `{"checks": [...], "summary": {"pass": n, "warn": n, "fail": n}}`; exit status 1 when any check failed. Human output: one line per check, `PASS`, `WARN` or `FAIL` first.

`recovery_view`: `{"recovery_required": bool, "reason": str | None, "revision": str | None, "state": <summary.jsx data> | None, "next": str}`. `state` is filled by a read under the lease when the heartbeat is fresh. `next` is the sentence: "Check the state above. Then inspect and acknowledge with the MCP tools inspect_operation_recovery and acknowledge_operation_recovery." or "No recovery is open." The command `recovery show` prints it and exits 0 in both cases.

`status`: add one key `live` to the payload and one human line. Inside a `try`, import `core.bridge` lazily and set `live = {"panel_heartbeat": "fresh" | "stale" | "absent", "bridge_dir": str}`; on any exception set `live = {"error": str(exc)}`. No lease, no script.

- [ ] **Step 1: Write the failing tests** in `test_doctor.py` (FakePanel whose handler returns a state reply; stubs for `premiere`, `panel`, `which`, `osascript`):

```python
def names(checks, status): return [c["name"] for c in checks if c["status"] == status]

def test_all_pass(...):                               # every check "pass"; exit 0
def test_no_panel(...):                               # FakePanel(heartbeat=None): panel_heartbeat fail; the three state checks warn "not checked"; exit 1; no script seen
def test_premiere_26_is_a_warning(...):               # state version "26.5.2" -> premiere_version warn
def test_own_project_is_a_warning(...):               # project_guard warn
def test_dead_lease_is_reported_and_not_touched(...): # lease fail; lock folder and owner.json still there; no recovery marker written
def test_live_lease_holder_skips_state_checks(...):   # lease warn with the pid; state checks "not checked, lease busy"
def test_open_recovery_fails(...):                    # recovery fail, detail names "recovery show"
def test_no_accessibility(...):                       # osascript stub raises CliError("no_accessibility") -> accessibility fail
def test_recovery_show_without_recovery(...):         # recovery_required False, exit 0
def test_recovery_show_with_recovery(...):            # reason and 64-hex revision; state has "video_clips"; exit 0; marker file unchanged
def test_status_keeps_its_keys_and_adds_live(capsys): # writes_projects is False, tested_project_formats has "46", "live" in data
def test_status_survives_a_broken_bridge_module(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "cli_anything.premiere.core.bridge", None)
    # exit 0, data["live"] has "error", data["writes_projects"] is False
```

- [ ] **Step 2: Run and see them fail.** Run: `python3 -m pytest cli_anything/premiere/tests/test_doctor.py -q`. Expected: import error.
- [ ] **Step 3: Implement `core/doctor.py`, the two groups, and the `status` key.**
- [ ] **Step 4: Run and see them pass.** Expected: all pass.
- [ ] **Step 5: Run the full suite.** Run: `python3 -m pytest cli_anything/premiere/tests -q`. Expected: all pass, the reader's 38 included.

---

### Task 8: Live fixture and end-to-end tests

This task touches the running Premiere. Before it starts, send one message to `premier-pro-mcp-76` and one to `premier-pro-mcp-78`: "The Premiere CLI tests start now. They open `fixture27 Claude.prproj` in Premiere 27 and write to a duplicate sequence. Please do not use Premiere until I say it is free." Send a second message when Task 9 is done.

**Files:**
- Create: `cli_anything/premiere/tests/test_live_e2e.py`
- Create (local, untracked): `cli_anything/premiere/tests/fixtures_local/fixture27 Claude.prproj`

**Interfaces:**
- Consumes: the installed command `cli-anything-premiere` (editable install, already in place).
- Produces: the test project that Task 9 uses.

- [ ] **Step 1: Make the fixture copy.** Check that Premiere has no project open (`cli-anything-premiere --json live state` shows `"projects": []`). Then:

```bash
cp "cli_anything/premiere/tests/fixtures_local/fixture27.prproj" "cli_anything/premiere/tests/fixtures_local/fixture27 Claude.prproj"
```

- [ ] **Step 2: Run `doctor`.**

Run: `cli-anything-premiere doctor`
Expected: no `FAIL` line. `projects` is `WARN` (none open).

- [ ] **Step 3: Open the copy.**

Run: `cli-anything-premiere --json mcp call open_project --args '{"project_path": "<absolute path of the copy>"}' --timeout 180` (check the argument name first with `cli-anything-premiere mcp describe open_project`).
Expected: `"ok": true`. If the call does not return, a dialog is open in Premiere: stop and ask Urko to click it. Then `cli-anything-premiere --json live state` shows one project named `fixture27 Claude.prproj`.

- [ ] **Step 4: Write the tests** in `test_live_e2e.py`. Module-level skip unless all are true: the heartbeat is fresh; `live state` shows exactly one project; its name is `fixture27 Claude.prproj`. Skip reason: "Premiere 27 with fixture27 Claude.prproj open is needed (see docs/2026-11-18-premiere-cli-plan-1-frame.md, Task 8)". Run every command as a subprocess of the installed command with `--json`, as `test_full_e2e.py` does.

```python
def test_doctor_has_no_failure():                  # exit 0; summary["fail"] == 0
def test_live_state_reads_the_running_app():       # version starts with "27.0."; build is digits; one project; active_sequence has id and name
def test_live_run_read_script(tmp_path):           # body 'return __ok({v: app.version, n: ARGS.n});' --args '{"n": -2.5}' -> data == {"v": <same version>, "n": -2.5}; stored None
def test_live_run_script_error(tmp_path):          # body 'throw new Error("x");' -> exit 1, code "script_failed", no ".host-recovery.json" in the bridge folder
def test_recovery_show_is_quiet():                 # recovery_required False
def test_mcp_ping():                               # mcp call ping -> ok; result has connected true and premiereVersion "27.0.0"
def test_mcp_list_has_many_tools():                # count > 400; "set_effect_property" in the names
def test_ui_mode_round_trip():                     # get -> m0; set "edit" if m0 != "edit" else "color" -> ok; set back to m0 -> ok; get == m0
def test_bridge_folder_is_clean_after_the_run():   # no cmd_, res_, busy_ file and no ".host-owner-lease"
```

- [ ] **Step 5: Run them.**

Run: `python3 -m pytest cli_anything/premiere/tests/test_live_e2e.py -q`
Expected: 9 passed. A skip means the fixture is not open: go back to Step 3.

- [ ] **Step 6: Run the full suite.** Expected: all pass.

---

### Task 9: Gate tests G1 to G5

**Files:**
- Create: `cli_anything/premiere/tests/gates/run_gates.py`
- Modify: `PREMIERE.md` (add a section "Gate tests, 2026-11-18" with the result table)

**Interfaces:**
- Consumes: `BridgeClient`, `McpSession`, `jsx.build`, `guard.check_state`, and the open fixture of Task 8.
- Produces: `gates_result.json` next to the script (untracked) and the table in `PREMIERE.md`.

`run_gates.py` is a real file with `if __name__ == "__main__":`. It takes `--gate G1|G2|G3|G4|G5|all`. It refuses to run unless the one open project is `fixture27 Claude.prproj`. It finds the argument names of every MCP tool it calls from `list_tools()` and stops with a clear message if a tool or argument is missing. For each gate it prints and stores `{"gate", "result": "good" | "bad" | "inconclusive", "evidence": {...}}`.

Setup, once: with one `McpSession` call `duplicate_sequence` on the fixture sequence, rename the copy to `Gate copy`, make it active, and read its V1 clip 0 (the fixture clip that carries ProcAmp and Lumetri Color). All gate writes go to that clip of the copy. Teardown: delete the sequence `Gate copy` with `delete_sequence`; do not save the project.

| Gate | Steps | "good" means | "bad" means |
|---|---|---|---|
| G1 | Session A stays open. A: `set_effect_property` ProcAmp Brightness = 1. CLI: lease, raw script sets Brightness = 2 and reads it back, release. A: `set_effect_property` Brightness = 3. A: read the value. | The second MCP write passes and reads 3 | The second MCP write fails; store the full error text (look for `HOST_RECOVERY_REQUIRED` and any "state revision" wording) |
| G2 | Session A: list the clips of `Gate copy`, keep the node ID of V1 clip 0, close A. Session B: read that clip's properties by the node ID. | B resolves the same clip | B reports an unknown node ID |
| G3 | Session A: `preview_edit_plan` with a plan of one harmless step; close A. Session B: `apply_edit_plan` with what the preview returned. Then A2: `create_sequence_checkpoint`; close; B2: `list_sequence_checkpoints`. | Both pairs work across two starts | Either pair fails; store which |
| G4 | CLI raw script, one write per command, 30 writes each on ProcAmp Brightness and Lumetri Temperature (index 14; check the name at the index first): the values -3.7, -2.1, -2.6, -40, -20, -10.1 in turn, each followed by a write of +5 so that every negative write crosses zero. Read back after each write. | 60 of 60 stored | Any failure; store the count, the values and the error text (look for "Illegal Parameter type") |
| G5 | Session A runs a slow read (`get_full_sequence_info` on `Gate copy`) in a thread (`ThreadPoolExecutor`, one worker). While it runs, the main thread starts session B and calls `ping`. Repeat 5 times. | A and B both answer every time | Any error or timeout; store it |

Rules for the run:
- Before the gates: `cli-anything-premiere recovery show` must report no recovery.
- If any gate leaves `.host-recovery.json`: stop the run. Run `cli-anything-premiere recovery show`. Look at the state. Then, in one `McpSession`, call `inspect_operation_recovery` and `acknowledge_operation_recovery` with the revisions the inspection returned and `reviewed: true`. Write what happened into the evidence of that gate. Then continue with the next gate.
- G1 "bad" stops this plan. Report to Urko: the spec says the transport choice goes back to him.
- A dialog in Premiere blocks everything. Only Urko clicks it.

- [ ] **Step 1: Write `run_gates.py`** to the table and rules above.
- [ ] **Step 2: Dry check.** Run: `python3 cli_anything/premiere/tests/gates/run_gates.py --gate G2`. Expected: one result line for G2 and the sequence `Gate copy` gone afterward (`cli-anything-premiere --json mcp call list_sequences` shows the fixture sequence only).
- [ ] **Step 3: Run all gates.** Run: `python3 cli_anything/premiere/tests/gates/run_gates.py --gate all`. Expected: five result lines and `gates_result.json`.
- [ ] **Step 4: Check that Premiere is clean.** Run: `cli-anything-premiere doctor` and `cli-anything-premiere recovery show`. Expected: no `FAIL`, no open recovery, no `Gate copy` sequence.
- [ ] **Step 5: Write the results into `PREMIERE.md`**: one table with gate, result, and the evidence in one line each, plus what each result means for Plan 2 (the "If the answer is bad" column of the spec, section 7).
- [ ] **Step 6: Close the fixture and free Premiere.** Run: `cli-anything-premiere --json mcp call close_project --args '{"save_first": false}'` (check the argument name with `mcp describe close_project`). Tell sessions 76 and 78 that Premiere is free.
- [ ] **Step 7: Run the full suite.** Expected: all pass (the end-to-end tests of Task 8 skip now, because the fixture is closed).
- [ ] **Step 8: Stop and report to Urko.** Give him the five results in plain words. Plan 2 (the five recipes and the documents) is written only after he has seen them.

---

## Self-review notes

- Spec coverage: sections 5.1 (Task 1, 3, 4), 5.2 (Task 5), 5.3 (Task 6), 5.5 for `live run` and `mcp call` (Tasks 4, 5), 5.6 rules 1 and 2 (Task 3), section 6 `doctor`, `recovery show`, `live state`, `live run`, `mcp`, `ui mode` (Tasks 4 to 7), gates (Task 9), section 8 unit and end-to-end tests (all tasks). Left for Plan 2: the five recipes, `--prove`, write rules 3 to 7, `MAX_HOLD_S` use in batches (the method `breathe` exists from Task 1), and the documents (`SKILL.md`, `README.md`, `TEST.md`, help text).
- Differences from the spec, all stated above: the MCP client comes before the gates; the "not applied" case in `send`; `doctor` does not reap a dead lease; the version stays 0.1.0; the running-app lookup uses the process list, not `status`.
