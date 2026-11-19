"""ActiveRun: the two-session pattern of frames.py for MCP tools that act on the active sequence.

Some tools of premiere-pro-mcp work on the ACTIVE sequence only (overwrite_clip, remove_from_timeline,
set_clip_duration, apply_effect take a node ID or place on app.project.activeSequence). A command that calls them
makes its target sequence active first and puts the old one back at the end. The rules are the ones of
`live frame export` (frames.py), with two changes: the proxy display is never switched, and the MCP work may come
in more than one MCP session, with read passes between them.

    run = ActiveRun(client, make_session, sequence_id="s1")
    planned = run.prepare(check, switch=True)       # session A: target, check(session), switch, confirm
    error = run.mcp(steps)                           # no lease: one MCP session, steps(mcp, guard_args)
    reply, still_active = run.look(body, args)       # a read pass (works with a recovery marker)
    fault = run.finish()                             # session B: put back what was switched

- prepare: a WriteSession targets the sequence and reads the app (frames._read_app). check(session) runs next,
  with the lease held: the command's reads and refusals, before any switch. With switch, the target is made
  active (frames._switch with frames._activate) when another sequence is active, and then a fresh guarded state
  read (write guards) must show the target as the active sequence. That read always runs, also when the target was
  active already. A failure after the switch puts it back in the same session (frames._put_back) and is raised; a
  plain Exception there, or in check(), is raised as CliError("recipe_error").
- mcp: only after prepare(switch=True) got its confirming read. It never runs while this process holds the lease.
  guard_args is {"expected_project_path", "expected_cep_target": {"backend": "cep", "sequence_id"}}: every call
  carries both. It returns the first error and never raises (a stop: interrupted, with stopped True; a plain
  Exception: recipe_error).
- look: the lease, guarded_state(write=False), then the script with "expect" added. Returns (the reply, whether
  that state read shows the target as the active sequence).
- finish: session B, once. When this run switched the active sequence and it was not put back yet, a new write
  session reads the app: when someone else changed the active sequence during the run, it is left as it is (with
  a warning); else it is put back. With a recovery marker the session cannot open, so nothing is written. Fills
  left_switched (frames._left_switched) and adds its warning lines. Returns a fault (recipe_error) or None.

The rules for a stop and for a write whose outcome is unknown are the ones of frames.py (its module docstring):
after either, no further write is sent, and left_switched names what may still be switched.
"""
from __future__ import annotations

from typing import Callable, TypeVar

from cli_anything.premiere.core import guard
from cli_anything.premiere.core.bridge import BridgeClient
from cli_anything.premiere.core.recipes.frames import (_activate, _fault, _id, _left_switched, _new_switches,
                                                        _put_back, _read_app, _switch)
from cli_anything.premiere.core.recipes.stops import STOPPED, as_error, is_stop
from cli_anything.premiere.core.session import (DEFAULT_HARD_LIMIT_S, SOFT_TIMEOUT_S, WriteSession, guarded_state,
                                                run_script)
from cli_anything.premiere.errors import CliError

T = TypeVar("T")
NOTHING_DONE = "Nothing was placed or applied."


def _shown(ref) -> str:
    if not isinstance(ref, dict):
        return "no sequence"
    return f"{ref.get('name') or '?'} ({ref.get('id')})"


def _active_left(sw: dict) -> bool:
    """The active sequence this run switched is still switched: only one that existed before can be put back, and
    not one that someone else changed during the run."""
    return (sw["active_sent"] and not sw["active_foreign"] and sw["active_before"] is not None
            and _id(sw["active_now"]) != _id(sw["active_before"]))


def _reason(err: CliError) -> str:
    if err.code == "recovery_required":
        return "a recovery is open, so nothing was written. After the recovery (see `cli-anything-premiere " \
               "recovery show`)"
    return f"putting it back failed ({err.code}: {err.message}). When that is fixed"


def _fail_left(sw: dict, err: CliError) -> None:
    """No put-back follows: the active sequence, when it is still switched, gets err as its reason."""
    sw["tried"] = True
    if _active_left(sw) and sw["active_reason"] is None:
        sw["active_reason"] = _reason(err)


class ActiveRun:
    """The two-session pattern of frames.py for MCP tools that act on the active sequence. Proxies are never
    switched. See the module docstring."""

    def __init__(self, client: BridgeClient, make_session, *, sequence_id: str, sequence_name: str | None = None,
                 project=None, any_project=False, allow_untested=False, hard_limit_s=DEFAULT_HARD_LIMIT_S,
                 call_timeout_s=DEFAULT_HARD_LIMIT_S) -> None:
        if not isinstance(sequence_id, str) or not sequence_id:
            raise CliError("bad_args", "--sequence must name a sequence ID (see `live state`). Nothing was sent.")
        self.client = client
        self.make_session = make_session
        self.call_timeout_s = call_timeout_s
        self.guards = {"project": project, "any_project": any_project, "allow_untested": allow_untested,
                       "hard_limit_s": hard_limit_s}
        self.sequence: dict = {"id": sequence_id, "name": sequence_name}
        self.project_path: str | None = None
        self.warnings: list[str] = []
        self.stopped = False
        self.left_switched: dict | None = None
        self._name = sequence_name
        self._sw = _new_switches()
        self._prepared = False
        self._confirmed = False
        self._finished = False

    def _note(self, lines) -> None:
        self.warnings.extend(line for line in lines if line not in self.warnings)

    # ---------- session A ----------
    def _confirm(self) -> dict:
        """A fresh guarded state read with the write guards (the lease is held). The active project must be the
        one session A started with."""
        state, warnings = guarded_state(self.client, write=True, project=self.guards["project"],
                                        any_project=self.guards["any_project"],
                                        allow_untested=self.guards["allow_untested"],
                                        hard_limit_s=self.guards["hard_limit_s"])
        self._note(warnings)
        found = guard.active_project(state.get("projects") or [])
        path = found.get("path") if found else None
        if path != self.project_path:
            raise CliError("project_mismatch", f"The active project changed from {self.project_path} to {path}. "
                                               f"{NOTHING_DONE}")
        return state

    def prepare(self, check: Callable[[WriteSession], T], *, switch) -> T:
        """Session A. switch is a bool, or a function of check's result that says whether to switch. Returns what
        check returned."""
        if self._prepared:
            raise RuntimeError("ActiveRun.prepare runs once.")
        self._prepared = True
        sw = self._sw
        seq_id = self.sequence["id"]
        try:
            with WriteSession(self.client, **self.guards) as session:
                self._note(session.warnings)
                session.target(seq_id, self._name)
                info = _read_app(session, seq_id)
                self.sequence = {"id": info["sequence"]["id"], "name": info["sequence"]["name"]}
                sw["target"] = dict(self.sequence)
                sw["active_before"] = sw["active_now"] = sw["active_during"] = info["active_sequence"]
                self.project_path = session.expect["project_path"]
                result = check(session)
                if not (switch(result) if callable(switch) else switch):
                    return result
                try:
                    if _id(info["active_sequence"]) != seq_id:
                        reply = _switch(session, sw, "active", lambda: _activate(session, seq_id))
                        sw["active_unknown"] = False
                        sw["active_now"] = reply["after"]
                        if _id(reply["after"]) != seq_id:
                            raise CliError("not_switched", f"Sequence {_shown(self.sequence)} did not become active: "
                                                           f"Premiere shows {_shown(reply['after'])} after the write"
                                           + (f" ({reply['set_error']})" if reply["set_error"] else "")
                                           + f". {NOTHING_DONE}")
                    state = self._confirm()
                    shown = state.get("active_sequence")
                    if sw["active_sent"]:
                        sw["active_now"] = shown if isinstance(shown, dict) else None
                    if _id(shown) != seq_id:
                        raise CliError("not_switched", f"Premiere's state shows {_shown(shown)} as the active "
                                                       f"sequence, not {_shown(self.sequence)}. {NOTHING_DONE}")
                    sw["active_during"] = sw["active_now"]
                    self._confirmed = True
                except BaseException as exc:
                    # Any failure after the switch, a stop too: one put-back in this session (it writes nothing
                    # when a write ended every write of the run).
                    _put_back(session, sw)
                    if isinstance(exc, CliError) or is_stop(exc) or not isinstance(exc, Exception):
                        raise
                    raise _fault(exc) from exc
                return result
        except BaseException as exc:
            if sw["stopped"] or is_stop(exc):
                self.stopped = sw["stopped"] = True
            if isinstance(exc, CliError) or is_stop(exc) or not isinstance(exc, Exception):
                raise
            raise _fault(exc) from exc

    # ---------- no lease: the MCP session ----------
    def guard_args(self) -> dict:
        return {"expected_project_path": self.project_path,
                "expected_cep_target": {"backend": "cep", "sequence_id": self.sequence["id"]}}

    def mcp(self, steps: Callable) -> CliError | None:
        """One MCP session; steps(mcp, guard_args). Returns the first error, or None. Never raises."""
        if not self._confirmed or self._finished or self._sw["halt"] is not None:
            return CliError("recipe_error", "The target sequence was not made active and confirmed by a fresh "
                                            "state read, so no MCP tool was called.")
        if self.client.lease().held:
            return CliError("recipe_error", "This program holds the lease, so no MCP tool was called (the server "
                                            "takes the same lease).")
        try:
            with self.make_session(self.client) as session:
                steps(session, self.guard_args())
        except BaseException as exc:
            if is_stop(exc):
                self.stopped = self._sw["stopped"] = True
                return as_error(exc, f"{STOPPED} The MCP call that was running may have finished: check Premiere "
                                     "before another edit.")
            if isinstance(exc, CliError):
                return exc
            if not isinstance(exc, Exception):
                raise
            return _fault(exc)
        return None

    # ---------- a read pass ----------
    def look(self, body: str, args: dict, *, include=("find",)) -> tuple[object, bool]:
        """A read: the lease, guarded_state(write=False), then the script with "expect" added. Works with a
        recovery marker. Returns (the reply, whether the state read shows the target as the active sequence).
        Raises CliError."""
        if self.project_path is None:
            raise CliError("recipe_error", "ActiveRun.look needs prepare first.")
        if not isinstance(args, dict) or "expect" in args:
            raise CliError("bad_args", "Script arguments must be a dict without the key 'expect'.")
        hard = self.guards["hard_limit_s"]
        with self.client.lease():
            state, warnings = guarded_state(self.client, write=False, project=self.guards["project"],
                                            any_project=self.guards["any_project"],
                                            allow_untested=self.guards["allow_untested"], hard_limit_s=hard)
            self._note(warnings)
            found = guard.active_project(state.get("projects") or [])
            path = found.get("path") if found else None
            if path != self.project_path:
                raise CliError("project_mismatch", f"The active project changed from {self.project_path} to {path} "
                                                   "during the run.")
            expect = {"project_path": self.project_path, "sequence_id": self.sequence["id"],
                      "sequence_name": self._name}
            reply = run_script(self.client, body, {**args, "expect": expect}, write=False, include=include,
                               timeout_s=min(SOFT_TIMEOUT_S, hard), hard_limit_s=hard)
        return reply, _id(state.get("active_sequence")) == self.sequence["id"]

    # ---------- session B ----------
    def _check_after(self, session: WriteSession) -> None:
        """Session B reads the app once, before any write. When the active sequence differs from the one the run
        worked under, someone else changed it: one this run switched is left as it is (not put back, not in
        left_switched); either way a warning says so. When the read fails, the put-back runs as before. A stop
        during the read is raised."""
        sw = self._sw
        try:
            info = _read_app(session, None)
        except CliError as err:
            if is_stop(err):
                raise
            self._note([f"The state after the run could not be read ({err.code}: {err.message})."])
            return
        shown = info["active_sequence"]
        if _id(shown) == _id(sw["active_during"]):
            return
        if _active_left(sw):
            sw["active_foreign"] = True
            self._note([f"The active sequence is {_shown(shown)} now: it was changed during the run by someone "
                        "else, so this command did not put it back."])
        else:
            self._note([f"Someone changed the active sequence during the run. It now holds {_shown(shown)}."])
        sw["active_now"] = shown

    def finish(self) -> CliError | None:
        """Session B (once): put back the active sequence when this run switched it and it is still switched."""
        if self._finished:
            return None
        self._finished = True
        sw = self._sw
        fault = None
        try:
            if sw["halt"] is not None:
                _fail_left(sw, sw["halt"])
            elif _active_left(sw) and not sw["tried"]:
                with WriteSession(self.client, **self.guards) as session:
                    self._note(session.warnings)
                    if session.expect["project_path"] != self.project_path:
                        raise CliError("project_mismatch", f"the active project changed from {self.project_path} to "
                                                           f"{session.expect['project_path']} during the run")
                    self._check_after(session)
                    if _active_left(sw):
                        _put_back(session, sw)
        except BaseException as exc:
            if is_stop(exc):
                sw["stopped"] = True
            elif not isinstance(exc, CliError):
                if not isinstance(exc, Exception):
                    raise
                exc = fault = _fault(exc)
            _fail_left(sw, as_error(exc))
        if sw["stopped"]:
            self.stopped = True
        left, lines = _left_switched(sw, self.guards)
        self.left_switched = left
        self._note(lines)
        if sw["active_sent"] and sw["active_before"] is None:
            if _id(sw["active_now"]) == self.sequence["id"]:
                self._note([f"No sequence was active before the run, so {_shown(self.sequence)} stays active."])
            elif sw["active_unknown"]:
                self._note([f"The active sequence may have been switched to {_shown(self.sequence)}: the outcome of "
                            "that write is unknown. No sequence was active before, so there is nothing to put back."])
        return fault
