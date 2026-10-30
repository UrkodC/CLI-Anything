from __future__ import annotations

from cli_anything.snapgene.core import gblock
from cli_anything.snapgene.tests._helpers import filler


def test_a_clean_fragment_has_no_warnings():
    result = gblock.check(filler(500, 12))
    assert result["length"] == 500
    assert 25 < result["gc_percent"] < 75
    assert result["synthesis_warnings"] == []


def test_too_short_and_too_long():
    assert any("shorter" in w for w in gblock.check("ACGT" * 20)["synthesis_warnings"])
    assert any("longer" in w for w in gblock.check(filler(3200, 12))["synthesis_warnings"])


def test_gc_outside_the_range():
    low = gblock.check("AT" * 100)
    high = gblock.check("GC" * 100)
    assert any("GC is 0.0%" in w for w in low["synthesis_warnings"])
    assert any("GC is 100.0%" in w for w in high["synthesis_warnings"])


def test_run_of_six_g_is_flagged():
    seq = filler(200, 13) + "GGGGGG" + filler(200, 14)
    hits = gblock.runs(seq)
    assert {"base": "G", "length": 6, "start": 201} in hits
    assert any("run of 6 G" in w for w in gblock.check(seq)["synthesis_warnings"])


def test_short_gc_run_and_long_at_run():
    assert gblock.runs("ACGT" + "GGGGG" + "ACGT") == []          # 5 G is still allowed
    assert gblock.runs("ACGT" + "A" * 10 + "CGTA")[0]["length"] == 10
    assert gblock.runs("ACGT" + "A" * 9 + "CGTA") == []


def test_repeated_stretches():
    block = filler(40, 15)
    seq = block + filler(100, 16) + block
    found = gblock.repeats(seq)
    assert found and found[0]["times"] == 2
    assert any("repeated" in w for w in gblock.check(seq)["synthesis_warnings"])


def test_report_shape():
    result = gblock.check("ACGT" * 40, "my insert")
    assert set(result) >= {"length", "gc_percent", "long_runs", "repeats", "synthesis_warnings", "sequence"}
    assert result["sequence"] == "ACGT" * 40
    assert all(w.startswith("my insert:") for w in result["synthesis_warnings"])
