"""Tests for the per-entry result helper (core/recipes/results.py) and its two printers in live_recipes.py.

No Premiere, no bridge: the helper works on plain dicts, and the printers get a bare click context.
"""
from __future__ import annotations

import json
from pathlib import Path

import click
import pytest

from cli_anything.premiere import live_cli, live_recipes  # noqa: F401  (live_cli first: it imports live_recipes last)
from cli_anything.premiere.core.recipes import results
from cli_anything.premiere.errors import CliError

RECORD_KEYS = {"entry", "name", "target", "status", "skip", "reason_code", "reason", "now", "error", "warnings"}
SUMMARY_KEYS = {"results", "counts", "first_not_run", "stored", "rendered", "warnings"}
BANNED_WORDS = ("delve", "leverage", "robust", "seamless", "comprehensive", "crucial")


def done(entry=0, name="S01"):
    return results.mark_done(results.new_record(entry, {"track": 0}, name=name), {"ok": True})


def nothing_to_do(entry=1, name="S02"):
    return results.mark_skipped(results.new_record(entry, {"track": 0}, name=name), results.NOTHING_TO_DO,
                                "has_effect", "The clip has ProcAmp already.")


def refused(entry=1, name="S02"):
    return results.mark_skipped(results.new_record(entry, {"track": 0}, name=name), results.REFUSED,
                                "clip_changed", "The clip was trimmed after the plan was made.")


def failed(entry=2, name="S03", known=True):
    return results.mark_failed(results.new_record(entry, {"track": 0}, name=name), "It did not read back.",
                               known=known)


def not_run(entry=3, name="S04"):
    return results.new_record(entry, {"track": 0}, name=name)


# ---------- the records ----------
def test_new_record_is_not_run():
    rec = results.new_record(3, {"node_id": "000f4243"}, name="S04")
    assert rec == {"entry": 3, "name": "S04", "target": {"node_id": "000f4243"}, "status": "not_run", "skip": None,
                   "reason_code": None, "reason": None, "now": None, "error": None, "warnings": []}
    assert set(rec) == RECORD_KEYS
    assert results.new_record(0, {})["name"] is None
    assert results.new_record(0, {})["warnings"] is not results.new_record(0, {})["warnings"]
    assert results.STATUSES == ("done", "skipped", "failed", "not_run")


def test_mark_done_keeps_the_read_back():
    rec = results.mark_done(not_run(), {"start": 12})
    assert rec["status"] == "done" and rec["now"] == {"start": 12}
    assert rec["skip"] is None and rec["reason_code"] is None and rec["reason"] is None and rec["error"] is None
    assert rec["entry"] == 3 and rec["name"] == "S04" and rec["target"] == {"track": 0}


def test_mark_functions_change_the_record_and_return_it():
    rec = not_run()
    assert results.mark_done(rec, 1) is rec
    assert results.mark_skipped(rec, results.REFUSED, "x", "Y.") is rec
    assert results.mark_failed(rec, "z") is rec


def test_mark_skipped_has_two_kinds():
    nothing = nothing_to_do()
    assert nothing["status"] == "skipped" and nothing["skip"] == "nothing_to_do"
    assert nothing["reason_code"] == "has_effect" and nothing["reason"] == "The clip has ProcAmp already."
    assert nothing["now"] is None
    refusal = results.mark_skipped(not_run(), results.REFUSED, "clip_changed", "It changed.", {"in": 5})
    assert refusal["skip"] == "refused" and refusal["now"] == {"in": 5}
    assert (results.NOTHING_TO_DO, results.REFUSED) == ("nothing_to_do", "refused")
    with pytest.raises(ValueError):
        results.mark_skipped(not_run(), "maybe", "x", "y")


def test_mark_failed_known_and_unknown():
    known = results.mark_failed(not_run(), "The value did not land.", {"value": 3})
    assert known["status"] == "failed" and known["error"] == "The value did not land." and known["now"] == {"value": 3}
    unknown = results.mark_failed(not_run(), "No answer.", {"value": 3}, known=False)
    assert unknown["status"] == "failed" and unknown["error"] == "No answer." and unknown["now"] is None
    assert unknown["unknown"] is True and "unknown" not in known


def test_a_later_mark_replaces_the_earlier_outcome():
    rec = failed(known=False)
    results.mark_done(rec, {"ok": True})
    assert rec["status"] == "done" and rec["error"] is None and "unknown" not in rec
    results.mark_skipped(rec, results.NOTHING_TO_DO, "has_effect", "It has it.")
    assert rec["status"] == "skipped" and rec["now"] is None
    results.mark_failed(rec, "again")
    assert rec["skip"] is None and rec["reason_code"] is None and rec["reason"] is None


# ---------- the exit rule and the counts ----------
def test_exit_rule():
    ok = [done(0), nothing_to_do(1)]
    assert results.succeeded(ok) is True and results.summary(ok, [])["stored"] is True
    bad = [done(0), refused(1)]
    assert results.succeeded(bad) is False and results.summary(bad, [])["stored"] is False
    assert results.succeeded([]) is True and results.summary([], [])["stored"] is True
    assert results.succeeded([done(0), not_run(1)]) is False
    assert results.succeeded([done(0), failed(1)]) is False
    assert results.summary([done(0), failed(1)], [])["stored"] is False


def test_unknown_outcome_gives_stored_none():
    records = [done(0), failed(1, known=False), not_run(2)]
    out = results.summary(records, [])
    assert out["stored"] is None and out["rendered"] is None
    assert results.succeeded(records) is False
    # A known failure next to an unknown one is still unknown: the run may have stored it.
    assert results.summary([failed(0), failed(1, known=False)], [])["stored"] is None


def test_counts_and_first_not_run():
    records = [done(0), failed(1), not_run(2), not_run(3)]
    assert results.counts(records) == {"done": 1, "skipped": 0, "refused": 0, "failed": 1, "not_run": 2}
    assert list(results.counts(records)) == ["done", "skipped", "refused", "failed", "not_run"]
    assert results.first_not_run(records) == 2
    assert results.first_not_run([done(0), failed(1)]) is None
    assert results.first_not_run([]) is None


def test_refused_counts_inside_skipped():
    records = [done(0), nothing_to_do(1), refused(2), refused(3)]
    assert results.counts(records) == {"done": 1, "skipped": 3, "refused": 2, "failed": 0, "not_run": 0}


def test_first_not_run_gives_the_entry_number():
    assert results.first_not_run([done(4), not_run(7), not_run(8)]) == 7


def test_summary_shape():
    records = [done(0), not_run(1)]
    warnings = ["A warning."]
    out = results.summary(records, warnings, sequence={"id": "s1"}, dry_run=False)
    assert set(out) == SUMMARY_KEYS | {"sequence", "dry_run"}
    assert out["results"] is records and out["warnings"] is warnings
    assert out["counts"] == results.counts(records) and out["first_not_run"] == 1
    assert out["sequence"] == {"id": "s1"} and out["dry_run"] is False
    assert json.loads(json.dumps(out)) == out
    for key in SUMMARY_KEYS:
        with pytest.raises(ValueError):
            results.summary(records, warnings, **{key: 1})


# ---------- the two errors ----------
def test_refused_error_names_the_first_refusal():
    records = [done(0), refused(1, "S02"), refused(2, "S03")]
    payload = results.summary(records, [])
    err = results.refused_error(records, payload)
    assert isinstance(err, CliError) and err.code == "refused"
    assert err.message == ("entry 1 (S02): clip_changed: The clip was trimmed after the plan was made. "
                           "Nothing was written. Use --skip-refused to run the other entries.")
    assert err.payload is payload


def test_refused_error_without_a_name_or_a_final_period():
    rec = results.mark_skipped(results.new_record(5, {}), results.REFUSED, "no_clip", "No clip has that ID")
    err = results.refused_error([rec], {"results": [rec]})
    assert err.message.startswith("entry 5: no_clip: No clip has that ID. Nothing was written.")
    assert "--skip-refused" in err.message


def test_refused_error_needs_a_refusal():
    with pytest.raises(ValueError):
        results.refused_error([done(0), nothing_to_do(1)], {})


def test_outcome_error_none_when_succeeded():
    ok = [done(0), nothing_to_do(1)]
    assert results.outcome_error(ok, results.summary(ok, [])) is None
    assert results.outcome_error([], {}) is None


def test_outcome_error_has_the_counts_sentence():
    records = [done(0), nothing_to_do(1), failed(2), not_run(3), not_run(4)]
    payload = results.summary(records, [])
    err = results.outcome_error(records, payload)
    assert isinstance(err, CliError) and err.code == "not_done"
    assert err.message == "1 done, 1 skipped, 1 failed, 2 not run. The first entry that did not run is 3."
    assert err.payload is payload


def test_outcome_error_leaves_out_the_last_sentence_when_all_ran():
    records = [done(0), failed(1)]
    err = results.outcome_error(records, results.summary(records, []))
    assert err.message == "1 done, 0 skipped, 1 failed, 0 not run."
    records = [done(0), refused(1)]                        # refused entries, run with --skip-refused
    assert results.outcome_error(records, {}).message == "1 done, 1 skipped, 0 failed, 0 not run."


def test_messages_hold_no_dash_and_no_banned_word():
    records = [done(0), refused(1), failed(2), not_run(3)]
    texts = [results.refused_error(records, {}).message, results.outcome_error(records, {}).message]
    source = Path(results.__file__).read_text(encoding="utf-8")
    for text in [*texts, source]:
        assert "—" not in text and "–" not in text
        assert not any(word in text.lower() for word in BANNED_WORDS)


# ---------- the printers of live_recipes.py ----------
def test_entry_lines_one_line_per_record_then_the_counts():
    records = [done(0, "S01"), nothing_to_do(1, "S02"), refused(2, "S03"), failed(3, "S04"), not_run(4, "S05")]
    lines = live_recipes._entry_lines(results.summary(records, []))
    assert lines == [
        '  0 S01 done | {"ok": true}',
        "  1 S02 skipped | nothing to do: has_effect: The clip has ProcAmp already.",
        "  2 S03 skipped | refused: clip_changed: The clip was trimmed after the plan was made.",
        "  3 S04 failed | It did not read back.",
        "  4 S05 not run",
        "1 done, 2 skipped (1 refused), 1 failed, 1 not run. The first entry that did not run is 4."]


def test_entry_lines_unknown_outcome_name_and_warnings():
    rec = failed(0, name=None, known=False)
    rec["warnings"].append("Look at the clip.")
    lines = live_recipes._entry_lines(results.summary([rec], []))
    assert lines == ["  0 failed | outcome unknown: It did not read back.", "    Warning: Look at the clip.",
                     "0 done, 0 skipped, 1 failed, 0 not run."]


def test_entry_lines_cut_a_long_read_back():
    rec = results.mark_done(results.new_record(0, {}, name="S01"), "x" * 200)
    assert live_recipes._entry_lines(results.summary([rec], []))[0] == '  0 S01 done | "' + "x" * 60 + '..."'
    rec = results.mark_done(results.new_record(1, {}, name="S02"), {"clips": list(range(100))})
    line = live_recipes._entry_lines(results.summary([rec], []))[0]
    assert line == "  1 S02 done | " + json.dumps({"clips": list(range(100))})[:60] + "..."
    rec = results.mark_done(results.new_record(2, {}, name="S03"), 7)
    assert live_recipes._entry_lines(results.summary([rec], []))[0] == "  2 S03 done | 7"
    rec = results.mark_done(results.new_record(3, {}, name="S04"), None)
    assert live_recipes._entry_lines(results.summary([rec], []))[0] == "  3 S04 done"


def test_entry_lines_without_records_print_nothing():
    assert live_recipes._entry_lines({}) == []
    assert live_recipes._entry_lines({"results": [], "counts": {}}) == []


def make_ctx(json_mode):
    return click.Context(click.Command("x"), obj={"json": json_mode})


def test_entries_failure_json_carries_the_payload(capsys):
    records = [done(0), failed(1), not_run(2)]
    payload = results.summary(records, ["Check Premiere."], sequence={"id": "s1"})
    err = results.outcome_error(records, payload)
    with pytest.raises(click.exceptions.Exit) as exc:
        live_recipes._entries_failure(make_ctx(True), err)
    assert exc.value.exit_code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["code"] == "not_done" and out["error"] == err.message
    assert out["results"] == records and out["counts"]["failed"] == 1 and out["first_not_run"] == 2
    assert out["stored"] is False and out["rendered"] is None and out["warnings"] == ["Check Premiere."]
    assert out["sequence"] == {"id": "s1"}


def test_entries_failure_human_prints_lines_then_error_and_warnings(capsys):
    records = [done(0), failed(1, "S02"), not_run(2, "S03")]
    payload = results.summary(records, ["Check Premiere."])
    err = results.outcome_error(records, payload)
    with pytest.raises(click.exceptions.Exit) as exc:
        live_recipes._entries_failure(make_ctx(False), err)
    assert exc.value.exit_code == 1
    out = capsys.readouterr()
    assert out.out.splitlines()[:3] == ['  0 S01 done | {"ok": true}', "  1 S02 failed | It did not read back.",
                                         "  2 S03 not run"]
    assert "Error: " + err.message in out.err and "Warning: Check Premiere." in out.err
    assert "Error" not in out.out


def test_entries_failure_without_a_payload_is_a_plain_failure(capsys):
    err = CliError("bad_args", "Give --sequence. Nothing was sent.")
    with pytest.raises(click.exceptions.Exit) as exc:
        live_recipes._entries_failure(make_ctx(True), err)
    assert exc.value.exit_code == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "Give --sequence. Nothing was sent.",
                                                   "code": "bad_args"}
    with pytest.raises(click.exceptions.Exit):
        live_recipes._entries_failure(make_ctx(False), err)
    out = capsys.readouterr()
    assert out.out == "" and "Error: Give --sequence. Nothing was sent." in out.err


def test_entries_failure_refused_payload(capsys):
    records = [done(0), refused(1)]
    err = results.refused_error(records, results.summary(records, []))
    with pytest.raises(click.exceptions.Exit):
        live_recipes._entries_failure(make_ctx(True), err)
    out = json.loads(capsys.readouterr().out)
    assert out["code"] == "refused" and out["stored"] is False and out["counts"]["refused"] == 1
