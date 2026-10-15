"""Talk to EndNote 2025 through AppleScript (osascript). The only module that touches the app."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cli_anything.endnote.errors import CliError

APP_NAME = "EndNote 2025"
APP_PATH = Path("/Applications/EndNote 2025/EndNote 2025.app")
FIELD_SEP = "\x1f"
ROW_SEP = "\x1e"
_RECNUM_RE = re.compile(r'recNum="(\d+)"')


@dataclass(frozen=True)
class FoundRecord:
    query: str          # the PMID that was searched
    rec_number: int     # EndNote record number, the N in "#N"
    accession: str      # the record's Accession Number field
    citation: str       # EndNote's own temporary citation, e.g. "{Soda, 2007 #18}"
    title: str


def run_applescript(script: str, args: list[str] | None = None, timeout: int = 300) -> str:
    """Run `script` with osascript. `args` reach the script's `on run argv` handler."""
    if not APP_PATH.exists():
        raise CliError("endnote_missing", f"{APP_NAME} was not found in /Applications.")
    try:
        proc = subprocess.run(
            ["osascript", "-", *(args or [])],
            input=script,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CliError(
            "endnote_timeout",
            f"{APP_NAME} did not answer in {timeout} seconds. Check for an open dialog in EndNote.",
        ) from exc
    if proc.returncode != 0:
        err = proc.stderr.strip()
        if "-1743" in err or "Not authorized" in err:
            raise CliError(
                "automation_denied",
                "Allow your terminal to control EndNote in System Settings > Privacy & Security > Automation.",
            )
        raise CliError("applescript_error", f"{APP_NAME} returned an error: {err}")
    return proc.stdout.rstrip("\n")


_LIBRARIES_SCRIPT = f'''
if application "{APP_NAME}" is running then
	tell application "{APP_NAME}" to set libraryNames to name of every document
	set AppleScript's text item delimiters to character id 30
	return "RUNNING" & (character id 31) & (libraryNames as text)
end if
return "NOT_RUNNING"
'''

_LOOKUP_SCRIPT = f'''
on run argv
	set US to character id 31
	set RS to character id 30
	set out to {{}}
	tell application "{APP_NAME}"
		repeat with p in argv
			set pmid to contents of p
			set ids to find pmid in field "Accession Number"
			repeat with i from 1 to count of ids
				set r to item i of ids
				set acc to field "Accession Number" of record r
				set u to unformatted record r
				set t to field "Title" of record r
				set end of out to pmid & US & (r as text) & US & acc & US & u & US & t
			end repeat
		end repeat
	end tell
	set AppleScript's text item delimiters to RS
	return out as text
end run
'''

_IMPORT_SCRIPT = f'''
on run argv
	tell application "{APP_NAME}" to import (item 1 of argv) into document 1
	return "OK"
end run
'''


def open_libraries() -> list[str] | None:
    """Names of the open libraries, or None when EndNote is not running."""
    out = run_applescript(_LIBRARIES_SCRIPT)
    if out == "NOT_RUNNING":
        return None
    names = out.split(FIELD_SEP, 1)[1] if FIELD_SEP in out else ""
    return [name for name in names.split(ROW_SEP) if name]


def record_count() -> int:
    return int(run_applescript(f'tell application "{APP_NAME}" to count (retrieve "all" records in document 1)'))


def parse_lookup_output(out: str) -> list[FoundRecord]:
    rows = []
    for line in out.split(ROW_SEP):
        if not line.strip():
            continue
        query, record_id, accession, citation, title = line.split(FIELD_SEP, 4)
        m = _RECNUM_RE.search(record_id)
        if not m:
            raise CliError("applescript_error", f"{APP_NAME} returned an error: unexpected record ID {record_id!r}")
        rows.append(FoundRecord(query.strip(), int(m.group(1)), accession.strip(), citation.strip(), title.strip()))
    return rows


def lookup(pmids: list[str]) -> list[FoundRecord]:
    """Records whose Accession Number contains one of `pmids`. Includes substring hits."""
    if not pmids:
        return []
    return parse_lookup_output(run_applescript(_LOOKUP_SCRIPT, args=list(pmids)))


def import_xml(xml: str) -> None:
    """Import EndNote XML into the open library (document 1)."""
    run_applescript(_IMPORT_SCRIPT, args=[xml])
