"""Builds the ExtendScript text that the live route sends to the panel inside Premiere.

A script is a body (ES3 statements ending in `return __ok(...)` or `return __fail(...)`) plus JSON
arguments. `build` wraps it in a function together with the shared helper library, so nothing leaks
into Premiere's global scope. The bodies live in `core/scripts/*.jsx`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from cli_anything.premiere.errors import CliError

SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
_NAME = re.compile(r"[A-Za-z0-9_-]+")


def load(name: str) -> str:
    """Text of core/scripts/<name>.jsx. Raises CliError("script_missing") when there is no such script."""
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise CliError("script_missing", f"There is no script named {name!r}.")
    path = SCRIPTS_DIR / f"{name}.jsx"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        raise CliError("script_missing", f"The script {name}.jsx is missing from {SCRIPTS_DIR}.") from None


def build(body: str, args: dict | None = None) -> str:
    """Wrap `body` with the helpers and `ARGS` (a JSON object, ASCII only) into one script expression."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise CliError("bad_args", f"Script arguments must be a dict, not {type(args).__name__}.")
    try:
        args_json = json.dumps(args, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise CliError("bad_args", f"Script arguments cannot be written as JSON: {exc}") from None
    return ("(function () {\n"
            + load("helpers").rstrip("\n") + "\n"
            + f"var ARGS = {args_json};\n"
            + "try {\n"
            + body.rstrip("\n") + "\n"
            + "} catch (e) { return __fail(String(e)); }\n"
            + "})()")
