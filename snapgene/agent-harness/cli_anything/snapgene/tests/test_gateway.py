from __future__ import annotations

import pytest

from cli_anything.snapgene.core import dnafile, gateway, seqtools
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.tests._helpers import (
    ATTB1, ATTB2, ATTL1, ATTL2, ATTR1, ATTR2, TEST_GENE, destination_vector, entry_clone,
    filler, write_snapgene,
)


@pytest.fixture
def entry(tmp_path):
    return dnafile.load(entry_clone(tmp_path / "pENTR-gene.dna"))


@pytest.fixture
def dest(tmp_path):
    return dnafile.load(destination_vector(tmp_path / "pDEST-test.dna"))


# --- the recombination rule ------------------------------------------------

def test_attb1_and_attb2_match_snapgenes_own_annotation():
    assert gateway.recombine(ATTL1, ATTR1) == ATTB1
    assert gateway.recombine(ATTL2, ATTR2) == ATTB2


def test_attb_is_the_attr_head_and_the_attl_tail():
    attb = gateway.recombine(ATTL1, ATTR1)
    assert attb.startswith(ATTR1[:20])     # the destination side
    assert attb.endswith(ATTL1[-5:])       # the entry side
    assert len(attb) == 25


def test_the_shared_core_is_found_in_both_sites():
    i, j, k = gateway._core(ATTL1, ATTR1)
    assert k >= gateway.MIN_CORE
    assert ATTL1[i:i + k] == ATTR1[j:j + k]
    assert len(ATTL1) - (i + k) <= gateway.MAX_FLANK


def test_sites_that_share_no_core_are_refused():
    with pytest.raises(CliError) as err:
        gateway.recombine(ATTL1, filler(120, 61))
    assert err.value.code == "att_mismatch"


def test_att1_does_not_recombine_with_att2():
    with pytest.raises(CliError) as err:
        gateway.recombine(ATTL1, ATTR2)
    assert err.value.code == "att_mismatch"


# --- reading the two files -------------------------------------------------

def test_read_clone_finds_the_pair(entry, dest):
    clone = gateway.read_clone(entry, "L", "entry clone")
    assert (clone.left.name, clone.left.strand) == ("attL1", "+")
    assert (clone.right.name, clone.right.strand) == ("attL2", "-")
    assert clone.middle_bp == len(TEST_GENE)
    assert gateway.read_clone(dest, "R", "destination vector").left.name == "attR1"


def test_a_file_without_two_attl_is_refused(tmp_path):
    plain = dnafile.load(write_snapgene(tmp_path / "plain.dna", filler(900, 62)))
    with pytest.raises(CliError) as err:
        gateway.read_clone(plain, "L", "entry clone")
    assert err.value.code == "no_att"
    assert "Found 0" in err.value.message


def test_two_sites_on_the_same_strand_are_refused(tmp_path):
    sequence = filler(200, 63) + ATTL1 + TEST_GENE + ATTL2 + filler(200, 64)
    a = (201, 200 + len(ATTL1))
    b = (a[1] + len(TEST_GENE) + 1, a[1] + len(TEST_GENE) + len(ATTL2))
    sg = dnafile.load(write_snapgene(
        tmp_path / "same.dna", sequence,
        features=[{"name": "attL1", "strand": "+", "segments": [a]},
                  {"name": "attL2", "strand": "+", "segments": [b]}]))
    with pytest.raises(CliError) as err:
        gateway.read_clone(sg, "L", "entry clone")
    assert err.value.code == "att_direction"


def test_linear_and_protein_files_are_refused(tmp_path):
    linear = dnafile.load(write_snapgene(tmp_path / "l.dna", filler(900, 65), circular=False))
    with pytest.raises(CliError) as err:
        gateway.read_clone(linear, "L", "entry clone")
    assert err.value.code == "not_circular"
    protein = dnafile.load(write_snapgene(tmp_path / "p.prot", "MKV", kind="protein", circular=False))
    with pytest.raises(CliError) as err:
        gateway.read_clone(protein, "L", "entry clone")
    assert err.value.code == "not_dna"


# --- the LR reaction -------------------------------------------------------

def test_lr_builds_the_expression_clone(entry, dest, tmp_path):
    result = gateway.lr(entry, dest, tmp_path / "exp.gb")
    product = result["_product"]
    backbone_bp = result["destination"]["backbone_bp"]
    assert product.length == backbone_bp + len(ATTB1) + len(TEST_GENE) + len(ATTB2)
    # The sequence is read twice over so a hit that crosses the origin is seen.
    # One copy on the circle therefore shows up twice here.
    circle = product.sequence + product.sequence
    assert circle.count(TEST_GENE) == 2
    assert circle.count(ATTB1) == 2
    assert circle.count(seqtools.revcomp(ATTB2)) == 2


def test_the_ccdb_cassette_is_gone(entry, dest, tmp_path):
    result = gateway.lr(entry, dest, tmp_path / "exp.gb")
    product = result["_product"]
    assert "ccdB" in result["destination"]["cassette_features"]
    assert not [f for f in product.features if "ccdb" in f.name.lower()]
    assert ATTR1 not in product.sequence + product.sequence
    assert "The product still carries" not in " ".join(result["warnings"])


def test_the_product_keeps_the_destination_numbering(entry, dest, tmp_path):
    product = gateway.lr(entry, dest, tmp_path / "exp.gb")["_product"]
    head = product.sequence[:150]
    assert dest.sequence.startswith(head)


def test_the_attb_sites_are_annotated_on_the_right_strands(entry, dest, tmp_path):
    result = gateway.lr(entry, dest, tmp_path / "exp.gb")
    product = result["_product"]
    sites = {f.name: f for f in product.features if f.name.startswith("attB")}
    assert set(sites) == {"attB1", "attB2"}
    assert (sites["attB1"].strand, sites["attB2"].strand) == ("+", "-")
    assert seqtools.subseq(product, sites["attB1"].start, sites["attB1"].end) == ATTB1
    assert seqtools.revcomp(seqtools.subseq(product, sites["attB2"].start, sites["attB2"].end)) == ATTB2
    first, last = result["insert"]["range_in_product"]
    assert seqtools.subseq(product, first, last) == TEST_GENE
    assert sites["attB1"].end == first - 1 and sites["attB2"].start == last + 1


def test_the_insert_features_come_across(entry, dest, tmp_path):
    product = gateway.lr(entry, dest, tmp_path / "exp.gb")["_product"]
    names = [f.name for f in product.features]
    assert "gene" in names                     # from the entry clone
    assert "N tag" in names and "C tag" in names   # from the destination backbone


def test_an_entry_clone_drawn_backwards_still_gives_a_forward_insert(tmp_path, dest):
    entry = dnafile.load(entry_clone(tmp_path / "flipped.dna", backwards=True))
    result = gateway.lr(entry, dest, tmp_path / "exp.gb")
    assert result["insert"]["reversed"] is True
    product = result["_product"]
    assert (product.sequence + product.sequence).count(TEST_GENE) == 2


# --- what it warns about ---------------------------------------------------

def test_a_vector_without_ccdb_is_flagged(tmp_path, entry):
    dest = dnafile.load(destination_vector(tmp_path / "notdest.dna", cassette_named=False))
    result = gateway.lr(entry, dest, tmp_path / "exp.gb")
    assert any("not a destination vector" in w or "No ccdB" in w for w in result["warnings"])


def test_tags_in_frame_are_reported(entry, dest, tmp_path):
    result = gateway.lr(entry, dest, tmp_path / "exp.gb")
    said = " ".join(result["warnings"])
    assert "N tag ends 27 bp before the insert, so the insert is in frame with it." in said
    assert "C tag starts 27 bp after the insert, so the insert is in frame with it." in said


def test_tags_out_of_frame_are_reported(tmp_path, entry):
    dest = dnafile.load(destination_vector(tmp_path / "shift.dna", spacer="GCA"))
    said = " ".join(gateway.lr(entry, dest, tmp_path / "exp.gb")["warnings"])
    assert "N tag ends 28 bp before the insert, so the insert is out of frame with it, by 1 base." in said


def test_an_insert_that_is_not_a_multiple_of_three_is_flagged(tmp_path, dest):
    entry = dnafile.load(entry_clone(tmp_path / "odd.dna", insert=TEST_GENE[:-1]))
    said = " ".join(gateway.lr(entry, dest, tmp_path / "exp.gb")["warnings"])
    assert "not a multiple of 3" in said


def test_an_insert_that_keeps_its_stop_codon_is_flagged(entry, dest, tmp_path):
    said = " ".join(gateway.lr(entry, dest, tmp_path / "exp.gb")["warnings"])
    assert "ends with the stop codon TAA" in said
