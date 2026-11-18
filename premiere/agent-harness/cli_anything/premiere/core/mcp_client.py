"""A small client for the premiere-pro-mcp server, run as a child process over stdio.

The messages are JSON-RPC 2.0, one JSON object per line on the child's stdin and stdout.
The server takes the bridge lease itself for every tool it runs, so this client holds the lease
only while it starts the server (to be sure no other program is in the middle of an edit) and
gives it back before the first tool call.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections import deque

from cli_anything.premiere import __version__
from cli_anything.premiere.core.bridge import BridgeClient, Interrupted
from cli_anything.premiere.errors import CliError

PROTOCOL_VERSION = "2025-06-18"
DEFAULT_COMMAND = ["premiere-pro-mcp"]
START_TIMEOUT_S = 30.0      # longest wait for the reply to `initialize`
LIST_TIMEOUT_S = 30.0       # longest wait for one page of `tools/list`
CLOSE_WAIT_S = 5.0          # after stdin is closed, the server gets this long to leave before it is killed
CAPABILITIES_VAR = "PREMIERE_MCP_CAPABILITIES"
UNSAFE_TOKEN = "unsafe-script"
BRIDGE_VAR = "PREMIERE_TEMP_DIR"
STDERR_KEPT = 20            # lines of the server's stderr kept to explain a death
STDERR_LINE_MAX = 300


class McpSession:
    """One run of the MCP server. Use it as a context manager: `with McpSession(client) as s:`."""

    def __init__(self, client: BridgeClient, command: list[str] | None = None, env: dict | None = None) -> None:
        self.client = client
        self.command = list(command) if command else list(DEFAULT_COMMAND)
        self.env = dict(env) if env else {}
        self._proc: subprocess.Popen | None = None
        self._inbox: queue.Queue = queue.Queue()      # parsed stdout objects; None marks the end of stdout
        self._stderr: deque = deque(maxlen=STDERR_KEPT)
        self._threads: list[threading.Thread] = []
        self._next_id = 0
        self._eof = False
        self._closed = False

    # ---------- start and stop ----------
    def start(self) -> dict:
        """Start the server and say hello. Returns {"name", "version"} of the server.

        Takes the lease for the time it takes and gives it back before it returns. On any failure the
        child is killed and the lease is given back.
        """
        if self._proc is not None:
            raise RuntimeError("This MCP session was already started.")
        env = self._child_env()
        if shutil.which(self.command[0], path=env.get("PATH")) is None:
            raise CliError("mcp_missing", f"Cannot find the program {self.command[0]!r}. "
                                          "Install premiere-pro-mcp and put it on PATH. Nothing was started.")
        lease = self.client.lease()
        lease.acquire()                 # RuntimeError when this process already holds it: that lease stays as it is
        try:
            self._spawn(env)
            info = self._handshake()
            lease.release()
        except BaseException:
            try:
                self._kill()
            finally:
                lease.release()         # does nothing when it was given back already
            raise
        return info

    def close(self) -> None:
        """Close the server's stdin, give it CLOSE_WAIT_S to leave, then kill it.

        When the wait is interrupted, the server is killed before the interrupt goes on."""
        proc = self._proc
        if proc is None or self._closed:
            return
        self._closed = True
        left = False
        try:
            _close_pipe(proc.stdin)
            proc.wait(timeout=CLOSE_WAIT_S)
            left = True
        except subprocess.TimeoutExpired:
            pass
        finally:
            if left:
                self._finish()
            else:
                self._kill()

    def __enter__(self) -> McpSession:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---------- requests ----------
    def list_tools(self) -> list[dict]:
        """All tools of the server. Follows nextCursor until it is absent."""
        tools: list[dict] = []
        seen: set[str] = set()
        cursor = None
        while True:
            result = self._request("tools/list", {"cursor": cursor} if cursor else {}, LIST_TIMEOUT_S)
            page = result.get("tools")
            if not isinstance(page, list):
                raise CliError("mcp_error", "The server's tool list has no 'tools' array.")
            tools.extend(t for t in page if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor:
                return tools
            if cursor in seen:
                raise CliError("mcp_error", "The server repeated a page of its tool list without end.")
            seen.add(cursor)

    def call(self, name: str, arguments: dict, timeout_s: float = 120.0) -> dict:
        """Run one tool. Returns {"is_error", "structured", "text"}.

        A tool that fails by its own account is not an exception: it comes back with is_error True.
        """
        result = self._request("tools/call", {"name": name, "arguments": arguments}, timeout_s,
                               hint=" The tool may still be running in Premiere: check before another edit.")
        content = result.get("content")
        texts = [item["text"] for item in content if isinstance(item, dict) and item.get("type") == "text"
                 and isinstance(item.get("text"), str)] if isinstance(content, list) else []
        return {"is_error": bool(result.get("isError")), "structured": result.get("structuredContent"),
                "text": "\n".join(texts)}

    # ---------- internals ----------
    def _child_env(self) -> dict:
        """The environment of the server: ours, plus what the caller asked for, with two rules on top.

        The server must use the same bridge folder as this CLI, and it never gets the token that lets
        it run raw scripts. The server reads its tokens in lower case, so every spelling is dropped.
        """
        env = {**os.environ, **self.env}
        if CAPABILITIES_VAR in env:
            tokens = [t.strip() for t in env[CAPABILITIES_VAR].split(",")]
            tokens = [t for t in tokens if t and t.lower() != UNSAFE_TOKEN]
            if tokens:
                env[CAPABILITIES_VAR] = ",".join(tokens)
            else:
                del env[CAPABILITIES_VAR]
        env[BRIDGE_VAR] = str(self.client.bridge_dir)
        return env

    def _spawn(self, env: dict) -> None:
        try:
            proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, env=env, text=True, encoding="utf-8", errors="replace")
        except OSError as exc:
            raise CliError("mcp_missing", f"Cannot start {self.command[0]!r}: {exc}. Nothing was started.") from None
        self._proc = proc
        for target in (self._read_stdout, self._read_stderr):
            thread = threading.Thread(target=target, args=(proc,), daemon=True, name=f"mcp-{target.__name__}")
            thread.start()
            self._threads.append(thread)

    def _read_stdout(self, proc: subprocess.Popen) -> None:
        """Queue every JSON object the server prints. Other lines are ignored. None marks the end."""
        try:
            for line in proc.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    self._inbox.put(message)
        except (OSError, ValueError):
            pass
        self._inbox.put(None)

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        """Read stderr to the end so the pipe cannot fill, and keep the last lines."""
        try:
            while True:
                chunk = proc.stderr.readline(4096)
                if not chunk:
                    return
                self._stderr.append(chunk.rstrip()[:STDERR_LINE_MAX])
        except (OSError, ValueError):
            pass

    def _handshake(self) -> dict:
        result = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "cli-anything-premiere", "version": __version__}}, START_TIMEOUT_S)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        info = result.get("serverInfo")
        info = info if isinstance(info, dict) else {}
        return {"name": info.get("name"), "version": info.get("version")}

    def _send(self, message: dict) -> None:
        proc = self._proc
        try:
            proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError):
            raise self._died() from None

    def _request(self, method: str, params: dict, timeout_s: float, hint: str = "") -> dict:
        """Send one request and wait for its reply. Returns the result object."""
        if self._proc is None or self._closed:
            raise RuntimeError("This MCP session is not started.")
        if self._eof:
            raise self._died()
        self._next_id += 1
        request_id = self._next_id
        deadline = time.monotonic() + timeout_s
        try:
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            while True:
                remaining = deadline - time.monotonic()
                try:
                    if remaining <= 0:
                        raise queue.Empty
                    message = self._inbox.get(timeout=remaining)
                except queue.Empty:
                    raise CliError("mcp_timeout", f"The server did not answer {method} within {timeout_s:g} "
                                                  f"seconds.{hint}") from None
                if message is None:
                    self._eof = True
                    raise self._died()
                answered = message.get("id")
                if type(answered) is not int or answered != request_id or "method" in message:
                    continue            # a notification, a request of the server, or an old reply
                if "error" in message:
                    error = message["error"]
                    text = error.get("message") if isinstance(error, dict) else None
                    raise CliError("mcp_error", str(text or error))
                result = message.get("result")
                if not isinstance(result, dict):
                    raise CliError("mcp_error", f"The server's reply to {method} has no result object.")
                return result
        except (KeyboardInterrupt, Interrupted):
            raise CliError("interrupted", "Stopped while waiting for the server. A tool it was running may "
                                          "have finished: check Premiere before another edit.") from None

    def _died(self) -> CliError:
        """The error for a server that is gone. Says what the server last wrote to stderr."""
        for thread in self._threads:
            thread.join(timeout=1.0)
        message = "The MCP server stopped."
        try:
            status = self._proc.wait(timeout=1.0)
            message = f"The MCP server stopped (exit status {status})."
        except subprocess.TimeoutExpired:
            pass
        tail = [line for line in list(self._stderr) if line][-5:]
        if tail:
            message += " Its last output: " + " | ".join(tail)
        return CliError("mcp_died", message)

    def _kill(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            pass
        self._finish()

    def _finish(self) -> None:
        """Wait for the reader threads, then close the pipes. A pipe that a thread still reads stays open."""
        proc = self._proc
        for thread in self._threads:
            thread.join(timeout=1.0)
        if not any(thread.is_alive() for thread in self._threads):
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                _close_pipe(pipe)


def _close_pipe(pipe) -> None:
    try:
        if pipe is not None:
            pipe.close()
    except (OSError, ValueError):
        pass
