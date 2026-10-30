from __future__ import annotations

import pytest

from cli_anything.snapgene.core import dnafile, pcr, seqtools
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.tests._helpers import TEST_GENE, filler, write_snapgene


@pytest.fixture
def plasmid(tmp_path):
    sequence = filler(800, 21) + TEST_GENE + filler(800, 22)
    feats = [{"name": "gene", "type": "CDS", "strand": "+", "segments": [(801, 800 + len(TEST_GENE))]}]
    return dnafile.load(write_snapgene(tmp_path / "p.dna", sequence, features=feats))


def test_window_keeps_the_target_whole(plasmid):
    win = pcr.window(plasmid, 801, 1097, context=200)
    assert win.target_length == 297
    assert win.first == 601
    assert len(win.sequence) == 297 + 400
    assert win.sequence[win.target_offset:win.target_offset + 297] == TEST_GENE


def test_window_wraps_the_origin(plasmid):
    win = pcr.window(plasmid, plasmid.length - 50, 50, context=100)
    assert win.target_length == 101
    assert win.sequence[win.target_offset:win.target_offset + 101] == \
        seqtools.subseq(plasmid, plasmid.length - 50, 50)


def test_window_on_a_linear_file_is_clipped(tmp_path):
    sg = dnafile.load(write_snapgene(tmp_path / "l.dna", filler(500, 23), circular=False))
    win = pcr.window(sg, 10, 100, context=100)
    assert win.first == 1 and win.target_offset == 9
    assert len(win.sequence) == 200


def test_design_finds_pairs_that_flank_the_target(plasmid):
    result = pcr.design(plasmid, 801, 1097, context=400)
    assert result["pairs"], result["warnings"]
    assert result["target_bp"] == 297
    for pair in result["pairs"]:
        forward, reverse = pair["forward"], pair["reverse"]
        assert forward["end"] < 801 and reverse["start"] > 1097     # outside the target
        assert pair["product_bp"] >= 297
        assert 57 <= forward["tm"] <= 63 and 57 <= reverse["tm"] <= 63
        assert plasmid.sequence[forward["start"] - 1:forward["end"]] == forward["sequence"]
        assert seqtools.revcomp(plasmid.sequence[reverse["start"] - 1:reverse["end"]]) == reverse["sequence"]


def test_design_product_size_limits(plasmid):
    result = pcr.design(plasmid, 801, 1097, context=400, max_product=500)
    assert all(p["product_bp"] <= 500 for p in result["pairs"])
    with pytest.raises(CliError) as err:
        pcr.design(plasmid, 801, 1097, min_product=900, max_product=500)
    assert err.value.code == "bad_range"


def test_design_reports_when_nothing_fits(plasmid):
    result = pcr.design(plasmid, 801, 1097, context=400, opt_tm=82.5, tm_range=(80.0, 85.0))
    assert result["pairs"] == []
    assert any("no pair" in w for w in result["warnings"])


def test_design_refuses_an_opt_tm_outside_the_window(plasmid):
    with pytest.raises(CliError) as err:
        pcr.design(plasmid, 801, 1097, opt_tm=72.0, tm_range=(57.0, 63.0))
    assert err.value.code == "bad_request"


def test_design_errors(tmp_path, plasmid):
    protein = dnafile.load(write_snapgene(tmp_path / "x.prot", "MKV", kind="protein", circular=False))
    with pytest.raises(CliError) as err:
        pcr.design(protein, 1, 3)
    assert err.value.code == "not_dna"
    with pytest.raises(CliError) as err:
        pcr.design(plasmid, 1, 99999)
    assert err.value.code == "bad_range"
    short = dnafile.load(write_snapgene(tmp_path / "s.dna", TEST_GENE, circular=False))
    with pytest.raises(CliError) as err:
        pcr.design(short, 1, len(TEST_GENE))
    assert err.value.code == "bad_range"


def test_check_primer_finds_the_site(plasmid):
    primer = plasmid.sequence[100:120]
    result = pcr.check_primer(plasmid, primer)
    assert result["binding_sites"] == [{"start": 101, "end": 120, "strand": "+", "match": primer}]
    assert result["length"] == 20
    assert result["tm"] > 0


def test_check_primer_on_the_minus_strand(plasmid):
    primer = seqtools.revcomp(plasmid.sequence[200:220])
    (site,) = pcr.check_primer(plasmid, primer)["binding_sites"]
    assert (site["start"], site["end"], site["strand"]) == (201, 220, "-")


def test_check_primer_warns_when_it_does_not_bind_or_binds_twice(plasmid, tmp_path):
    result = pcr.check_primer(plasmid, "ACGTACGTACGTTTTTTTTT")
    assert any("does not match" in w for w in result["warnings"])
    repeat = filler(60, 24)
    twice = dnafile.load(write_snapgene(tmp_path / "r.dna", repeat + filler(100, 25) + repeat))
    result = pcr.check_primer(twice, repeat[:20])
    assert any("2 places" in w for w in result["warnings"])


def test_check_primer_refuses_nothing(plasmid):
    with pytest.raises(CliError) as err:
        pcr.check_primer(plasmid, "   ")
    assert err.value.code == "bad_sequence"
