"""Error type shared by every part of cli-anything-snapgene."""
from __future__ import annotations


class CliError(Exception):
    """A failure the user can fix. `code` is stable for --json consumers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
