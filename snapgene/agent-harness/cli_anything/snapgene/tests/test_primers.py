from __future__ import annotations

from cli_anything.snapgene.core import primers, seqtools
from cli_anything.snapgene.tests._helpers import TEST_GENE


def test_gc_and_tm():
    assert primers.gc_percent("GGCC") == 100.0
    assert primers.gc_percent("ATAT") == 0.0
    assert primers.gc_percent("ATGC") == 50.0
    warm = primers.tm("GGCGGCGGCGGCGGCGGCGG")
    cool = primers.tm("ATATATATATATATATATAT")
    assert warm > cool and 50 < warm < 90


def test_longest_run_and_three_prime_gc():
    assert primers.longest_run("ATTTTTGC") == ("T", 5)
    assert primers.longest_run("") == ("", 0)
    assert primers.three_prime_gc("AAAAAGGGGG") == 5
    assert primers.three_prime_gc("GGGGGAAAAA") == 0


def test_hairpin_and_dimer_energies():
    assert primers.hairpin_dg("GGGGCCCCGGGGCCCCAAAA") < -3
    assert primers.hairpin_dg("ATATATATATATATATATAT") > -2
    assert primers.dimer_dg("GGGGCCCCGGGGCCCC") < 0
    assert primers.dimer_dg("AAAAAAAAAAAA", "AAAAAAAAAAAA") >= -5


def test_anchor_report():
    report = primers.anchor_report("ATGGTGTCTAAGGGCGAAGA")
    assert report["anchor_length"] == 20
    assert report["gc_percent"] == 50.0
    assert report["three_prime_gc"] == 2          # GAAGA holds 2 G
    assert 55 < report["tm"] < 65


def test_choose_anchors_keeps_both_in_takaras_window():
    forward, reverse = primers.choose_anchors(TEST_GENE, seqtools.revcomp(TEST_GENE))
    low, high = primers.TM_RANGE
    assert low <= forward["tm"] <= high
    assert low <= reverse["tm"] <= high
    assert abs(forward["tm"] - reverse["tm"]) <= primers.MAX_DELTA_TM
    for report in (forward, reverse):
        assert 18 <= report["anchor_length"] <= 25
        assert TEST_GENE.startswith(forward["anchor"])
        assert report["anchor"] in (TEST_GENE + seqtools.revcomp(TEST_GENE))


def test_choose_anchors_uses_what_it_has_on_a_short_insert():
    gene = "ATG" + "ACGT" * 5
    forward, reverse = primers.choose_anchors(gene, seqtools.revcomp(gene))
    assert forward["anchor_length"] <= len(gene)


def test_warnings_fire_on_a_bad_primer():
    bad = primers.anchor_report("ATATATATATATATATATAT")       # cold and no GC
    notes = primers.warnings_for("Forward primer", bad, "AAAA" + bad["anchor"])
    joined = " ".join(notes)
    assert "Tm" in joined and "GC" in joined
    good = primers.anchor_report("ATGGTGTCTAAGGGCGAAGAT")
    assert primers.warnings_for("Forward primer", good, good["anchor"]) == []


def test_pair_warnings_on_a_wide_tm_gap():
    hot = primers.anchor_report("GGCGGCGGCGGCGGCGGCGG")
    cold = primers.anchor_report("ATATATATATATATATATAT")
    notes = primers.pair_warnings(hot, cold, hot["anchor"], cold["anchor"])
    assert any("differ by" in n for n in notes)
    same = primers.anchor_report("ATGGTGTCTAAGGGCGAAGAT")
    assert primers.pair_warnings(same, same, "ACGTACGTACGTACGTAT", "ATGACGTTGCAAATGCAT") == []
