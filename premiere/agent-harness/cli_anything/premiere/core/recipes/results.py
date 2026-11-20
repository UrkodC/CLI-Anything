"""One result record per entry, for the commands that work from a plan file (keys set, place, effect apply).

A record says what happened to one entry of the plan:

    {"entry": int, "name": str | None, "target": {...}, "status": "done" | "skipped" | "failed" | "not_run",
     "skip": None | "nothing_to_do" | "refused", "reason_code": str | None, "reason": str | None,
     "now": <what the read-back holds> | None, "error": str | None, "warnings": [str]}

A recipe adds its own keys to a record (keys set: mode, moved, interpreted; place: calls, marks, removed_picture;
apply: effect, effect_index). It starts every entry with new_record (status "not_run"), then marks it done, skipped or failed.
The mark functions change the record they get and return it. A later mark replaces the earlier outcome.

Two kinds of "skipped":
- NOTHING_TO_DO ("already placed", "has effect", "level, no change", keys that match the plan) counts as success;
- REFUSED (a check failed, and --skip-refused let the run go on) does not.

The exit rule is succeeded(): exit status 0 only when every entry is done, or skipped for nothing to do. So a
re-run of a finished plan exits 0.

summary() builds the payload that every plan command prints, on success and on failure: results, counts,
first_not_run, stored, rendered (always None for these commands) and warnings. stored is True when the run
succeeded, None when an entry's outcome is unknown (mark_failed with known=False: the write may have landed), and
False otherwise. refused_error and outcome_error make the two errors a plan command raises, with the payload on
the error as `payload`.
"""
from __future__ import annotations

from cli_anything.premiere.errors import CliError

STATUSES = ("done", "skipped", "failed", "not_run")
NOTHING_TO_DO, REFUSED = "nothing_to_do", "refused"
SKIP_KINDS = (NOTHING_TO_DO, REFUSED)
SUMMARY_KEYS = ("results", "counts", "first_not_run", "stored", "rendered", "warnings")
UNKNOWN = "unknown"                 # the record key that mark_failed(known=False) sets (to True); no other mark keeps it


def new_record(entry: int, target: dict, *, name: str | None = None) -> dict:
    """The record of an entry that did not run (yet): status "not_run", nothing else set."""
    return {"entry": entry, "name": name, "target": target, "status": "not_run", "skip": None, "reason_code": None,
            "reason": None, "now": None, "error": None, "warnings": []}


def _outcome(record: dict, status: str, *, skip: str | None = None, code: str | None = None,
             reason: str | None = None, now: object = None, error: str | None = None) -> dict:
    """Set the outcome fields together, so an earlier outcome leaves nothing behind."""
    record.update(status=status, skip=skip, reason_code=code, reason=reason, now=now, error=error)
    record.pop(UNKNOWN, None)
    return record


def mark_done(record: dict, now: object) -> dict:
    """The entry is done. `now` is what the read-back holds."""
    return _outcome(record, "done", now=now)


def mark_skipped(record: dict, kind: str, code: str, reason: str, now: object = None) -> dict:
    """The entry was not run. kind is NOTHING_TO_DO (success) or REFUSED (not success). `code` is a short stable
    word (clip_changed), `reason` a sentence for the user."""
    if kind not in SKIP_KINDS:
        raise ValueError(f"skip kind must be one of {SKIP_KINDS}, not {kind!r}")
    return _outcome(record, "skipped", skip=kind, code=code, reason=reason, now=now)


def mark_failed(record: dict, error: str, now: object = None, *, known: bool = True) -> dict:
    """The entry failed. `now` is what the read-back holds, when it could be read. known=False: the outcome is
    not known (a write may have landed). `now` stays None then, and summary() gives stored None."""
    _outcome(record, "failed", now=now if known else None, error=error)
    if not known:
        record[UNKNOWN] = True
    return record


def counts(records: list[dict]) -> dict:
    """{"done", "skipped", "refused", "failed", "not_run"}. skipped holds every skipped entry; refused is the part
    of them that was refused (the others were nothing to do)."""
    by_status = {status: sum(1 for r in records if r["status"] == status) for status in STATUSES}
    return {"done": by_status["done"], "skipped": by_status["skipped"],
            "refused": sum(1 for r in records if r["status"] == "skipped" and r["skip"] == REFUSED),
            "failed": by_status["failed"], "not_run": by_status["not_run"]}


def succeeded(records: list[dict]) -> bool:
    """True when every record is done, or skipped for nothing to do. No records: True."""
    return all(r["status"] == "done" or (r["status"] == "skipped" and r["skip"] == NOTHING_TO_DO) for r in records)


def first_not_run(records: list[dict]) -> int | None:
    """The entry number of the first record that did not run, or None when none is left."""
    for record in records:
        if record["status"] == "not_run":
            return record["entry"]
    return None


def _stored(records: list[dict]) -> bool | None:
    if succeeded(records):
        return True
    return None if any(r.get(UNKNOWN) for r in records) else False


def summary(records: list[dict], warnings: list[str], /, **extra) -> dict:
    """The payload of a plan command: {"results", "counts", "first_not_run", "stored", "rendered": None,
    "warnings", **extra}. `extra` cannot take one of these names. records and warnings are positional only, so
    an extra key called "warnings" reaches this check and raises ValueError."""
    clash = [key for key in extra if key in SUMMARY_KEYS]
    if clash:
        raise ValueError(f"summary() already sets {', '.join(clash)}")
    return {"results": records, "counts": counts(records), "first_not_run": first_not_run(records),
            "stored": _stored(records), "rendered": None, "warnings": warnings, **extra}


def _sentence(text: str | None) -> str:
    text = (text or "").strip()
    return text if not text or text[-1] in ".!?" else text + "."


def refused_error(records: list[dict], payload: dict) -> CliError:
    """The error that stops a run before any write when an entry was refused (and --skip-refused was not given).
    It names the first refused entry and carries `payload`."""
    first = next((r for r in records if r["status"] == "skipped" and r["skip"] == REFUSED), None)
    if first is None:
        raise ValueError("refused_error needs a record that is skipped as refused")
    who = f"entry {first['entry']}" + ("" if first["name"] is None else f" ({first['name']})")
    why = ": ".join(part for part in (first["reason_code"], _sentence(first["reason"])) if part)
    err = CliError("refused", f"{who}: {why} Nothing was written. Use --skip-refused to run the other entries.")
    err.payload = payload
    return err


def outcome_error(records: list[dict], payload: dict) -> CliError | None:
    """None when the run succeeded. Else the error "not_done" with the counts and the first entry that did not
    run, carrying `payload`."""
    if succeeded(records):
        return None
    total = counts(records)
    message = f"{total['done']} done, {total['skipped']} skipped, {total['failed']} failed, {total['not_run']} not run."
    first = first_not_run(records)
    if first is not None:
        message += f" The first entry that did not run is {first}."
    err = CliError("not_done", message)
    err.payload = payload
    return err
