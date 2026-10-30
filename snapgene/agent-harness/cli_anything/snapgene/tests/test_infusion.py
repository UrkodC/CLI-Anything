"""In-Fusion design, checked against Figure 3 of Takara's manual PT5165-1.

The figure prints six finished primers for a vector opened three ways. The test
plasmid holds the same bases around the sites, so the tool's 5' tails must come
out base for base the same as Takara's.
"""
from __future__ import annotations

import pytest
from Bio.Restriction import BamHI, EcoRI, HindIII, KpnI
from Bio.Seq import Seq

from cli_anything.snapgene.core import assemble, dnafile, infusion, seqtools
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.tests._helpers import TEST_GENE, takara_vector, write_snapgene

# Takara In-Fusion HD manual PT5165-1, Figure 3: the 5' tail of each primer,
# that is the 15 bases of homology plus the bases that rebuild the site.
FIGURE_3 = {
    "A 5' overhangs": (["SalI", "HindIII"], "GAAGTTATCAGTCGAC", "ATGGTCTAGAAAGCTT"),
    "B blunt ends": (["SmaI"], "ACCGGACATATGCCCGGG", "CCTGCAGGAATTCCCGGG"),
    "C 3' overhangs": (["KpnI"], "AGTTATCAGTCGACGGTACC", "CCGGGCATATGTCCGGTACC"),
}


@pytest.fixture
def vector(tmp_path):
    return dnafile.load(takara_vector(tmp_path / "pTakara.dna"))


@pytest.fixture
def insert_file(tmp_path):
    feats = [{"name": "gene", "type": "CDS", "strand": "+", "segments": [(11, 10 + len(TEST_GENE))]}]
    return dnafile.load(write_snapgene(tmp_path / "insert.dna", "ACGTACGTAC" + TEST_GENE + "TGCATGCATG",
                                       circular=False, features=feats))


@pytest.fixture
def gene_piece():
    return assemble.literal(TEST_GENE, "gene")


def design(vector, piece, **kwargs):
    opened = infusion.open_vector(vector, **{k: v for k, v in kwargs.items() if k in
                                             ("enzyme_names", "at", "remove", "keep")})
    rest = {k: v for k, v in kwargs.items() if k in ("overlap", "restore_sites")}
    return infusion.design(opened, piece, **rest)


@pytest.mark.parametrize("case", list(FIGURE_3))
def test_primer_tails_match_the_takara_manual(vector, gene_piece, case):
    enzymes, want_forward, want_reverse = FIGURE_3[case]
    result = design(vector, gene_piece, enzyme_names=enzymes)
    forward, reverse = result["primers"]
    assert forward["homology"] + forward["site_bases"] == want_forward
    assert reverse["homology"] + reverse["site_bases"] == want_reverse
    assert forward["sequence"].startswith(want_forward)
    assert reverse["sequence"].startswith(want_reverse)
    assert forward["sequence"].endswith(forward["anchor"])


def test_the_fifteen_bases_come_from_the_vector_end(vector, gene_piece):
    result = design(vector, gene_piece, enzyme_names=["SalI", "HindIII"])
    forward, reverse = result["primers"]
    assert len(forward["homology"]) == len(reverse["homology"]) == 15
    # A 5' overhang is inside the 15 bases: the SalI overhang TCGA ends the tail.
    assert forward["homology"].endswith("GTCGA")
    assert result["opened"]["forward_end"] == "5' overhang"


def test_three_prime_overhang_bases_are_left_out(vector, gene_piece):
    result = design(vector, gene_piece, enzyme_names=["KpnI"])
    forward = result["primers"][0]
    assert result["opened"]["forward_end"] == "3' overhang"
    assert forward["homology"].endswith("GTCGACG")     # stops before the GTAC overhang
    assert "GGTACC" not in forward["homology"]


def test_product_keeps_the_backbone_and_rebuilds_both_sites(vector, gene_piece):
    result = design(vector, gene_piece, enzyme_names=["SalI", "HindIII"])
    product = result["_product"]
    circle = product.sequence + product.sequence[:30]
    assert product.circular
    assert product.sequence.count(TEST_GENE) == 1
    assert "GTCGAC" + TEST_GENE[:6] in circle          # SalI site rebuilt before the insert
    assert TEST_GENE[-6:] + "AAGCTT" in circle         # HindIII site rebuilt after it
    backbone = result["opened"]["backbone_bp"]
    assert product.length == backbone + 1 + len(TEST_GENE) + 1


def test_one_enzyme_gives_vector_plus_insert_plus_one_site(tmp_path, vector, gene_piece):
    result = design(vector, gene_piece, enzyme_names=["BamHI"])
    product = result["_product"]
    assert product.length == vector.length + len(TEST_GENE) + 6
    assert len(BamHI.search(Seq(product.sequence), linear=False)) == 2


def test_no_restore_sites_leaves_the_site_out(vector, gene_piece):
    result = design(vector, gene_piece, enzyme_names=["SalI", "HindIII"], restore_sites=False)
    product = result["_product"]
    assert "GTCGAC" + TEST_GENE[:6] not in product.sequence
    assert product.length == result["opened"]["backbone_bp"] + len(TEST_GENE)
    assert not any("no-restore-sites" in w for w in result["warnings"])


def test_no_restore_sites_on_one_cut_warns_about_repeated_bases(vector, gene_piece):
    """One cut with an overhang needs the site bases, or 4 bases come out twice."""
    result = design(vector, gene_piece, enzyme_names=["BamHI"], restore_sites=False)
    assert any("no-restore-sites" in w for w in result["warnings"])
    assert result["_product"].length == vector.length + len(TEST_GENE) + 4


def test_open_at_a_point(vector, gene_piece):
    result = design(vector, gene_piece, at=400)
    product = result["_product"]
    assert product.length == vector.length + len(TEST_GENE)
    assert seqtools.subseq(vector, 386, 400) + TEST_GENE[:5] in product.sequence
    assert result["primers"][0]["homology"] == seqtools.subseq(vector, 386, 400)


def test_open_by_removing_a_part(vector, gene_piece):
    result = design(vector, gene_piece, remove=(400, 500))
    assert result["opened"]["removed_bp"] == 101
    assert result["_product"].length == vector.length - 101 + len(TEST_GENE)


def test_two_cuts_keep_the_larger_piece_unless_told_otherwise(vector, gene_piece):
    big = design(vector, gene_piece, enzyme_names=["SalI", "HindIII"])
    small = design(vector, gene_piece, enzyme_names=["SalI", "HindIII"], keep="smaller")
    assert big["opened"]["backbone_bp"] > small["opened"]["backbone_bp"]
    assert big["opened"]["removed_bp"] < small["opened"]["removed_bp"]
    assert big["opened"]["removed_bp"] + small["opened"]["removed_bp"] == vector.length


def test_insert_from_a_file_brings_its_features(vector, insert_file):
    piece = assemble.take(insert_file, 11, 10 + len(TEST_GENE), label="gene")
    result = infusion.design(infusion.open_vector(vector, ["SalI", "HindIII"]), piece, insert_name="gene")
    product = result["_product"]
    gene = next(f for f in product.features if f.name == "gene" and f.type == "CDS")
    assert gene.strand == "+"
    assert seqtools.feature_sequence(product, gene) == TEST_GENE


def test_reversed_insert_flips_its_features(vector, insert_file):
    piece = assemble.take(insert_file, 11, 10 + len(TEST_GENE), flip=True, label="gene")
    result = infusion.design(infusion.open_vector(vector, ["SalI", "HindIII"]), piece, insert_name="gene")
    product = result["_product"]
    gene = next(f for f in product.features if f.name == "gene" and f.type == "CDS")
    assert gene.strand == "-"
    assert seqtools.feature_sequence(product, gene) == TEST_GENE
    assert seqtools.revcomp(TEST_GENE) in product.sequence


@pytest.mark.parametrize("kwargs", [
    {"enzyme_names": ["SalI", "HindIII"]},
    {"enzyme_names": ["BamHI"]},
    {"at": 400},
    {"remove": (400, 500)},
])
def test_product_keeps_the_vectors_numbering(vector, gene_piece, kwargs):
    """Base 1 of the vector stays base 1 of the product, so the map is not renumbered."""
    product = design(vector, gene_piece, **kwargs)["_product"]
    assert product.sequence.startswith(vector.sequence[:300])


def test_fragment_is_the_pcr_product(vector, gene_piece):
    result = design(vector, gene_piece, enzyme_names=["SalI", "HindIII"])
    forward, reverse = result["primers"]
    fragment = result["fragment_sequence"]
    assert fragment.startswith(forward["sequence"])
    assert fragment.endswith(seqtools.revcomp(reverse["sequence"]))
    assert result["fragment"]["length"] == len(fragment) == 32 + len(TEST_GENE)


def test_insert_frame_is_checked(vector):
    """A stop codon or a length that is not a multiple of 3 breaks a fusion."""
    with_stop = design(vector, assemble.literal(TEST_GENE), enzyme_names=["SalI", "HindIII"])
    assert any("stop codon TAA" in w for w in with_stop["warnings"])
    no_stop = design(vector, assemble.literal(TEST_GENE[:-3]), enzyme_names=["SalI", "HindIII"])
    assert not any("stop codon" in w for w in no_stop["warnings"])
    odd = design(vector, assemble.literal(TEST_GENE[:-1]), enzyme_names=["SalI", "HindIII"])
    assert any("not a multiple of 3" in w for w in odd["warnings"])


def test_overlap_outside_takaras_range_still_works_but_warns(vector, gene_piece):
    result = design(vector, gene_piece, enzyme_names=["SalI", "HindIII"], overlap=25)
    assert len(result["primers"][0]["homology"]) == 25
    assert any("Overlap is 25" in w for w in result["warnings"])


@pytest.mark.parametrize("kwargs,code", [
    ({"enzyme_names": ["XbaI", "SalI", "HindIII"]}, "too_many_cuts"),
    ({"enzyme_names": ["NotI"]}, "no_cut"),
    ({"enzyme_names": ["NotReal"]}, "unknown_enzyme"),
    ({"at": 99999}, "bad_range"),
    ({"remove": (1, 1630)}, "bad_range"),
    ({}, "bad_request"),
])
def test_open_vector_errors(vector, kwargs, code):
    with pytest.raises(CliError) as err:
        infusion.open_vector(vector, **{"enzyme_names": kwargs.get("enzyme_names"),
                                        "at": kwargs.get("at"), "remove": kwargs.get("remove")})
    assert err.value.code == code


def test_linear_vector_is_refused(tmp_path, gene_piece):
    linear = dnafile.load(write_snapgene(tmp_path / "lin.dna", TEST_GENE * 2, circular=False))
    with pytest.raises(CliError) as err:
        infusion.open_vector(linear, at=10)
    assert err.value.code == "not_circular"


def test_short_insert_and_silly_overlap_are_refused(vector, gene_piece):
    opened = infusion.open_vector(vector, ["SalI", "HindIII"])
    with pytest.raises(CliError) as err:
        infusion.design(opened, assemble.literal("ATGAAACCC"))
    assert err.value.code == "bad_insert"
    with pytest.raises(CliError) as err:
        infusion.design(opened, gene_piece, overlap=2)
    assert err.value.code == "bad_overlap"


def test_product_sites_can_be_counted_on_the_real_enzymes(vector, gene_piece):
    result = design(vector, gene_piece, enzyme_names=["EcoRI", "KpnI"], keep="larger")
    product = Seq(result["_product"].sequence)
    assert len(EcoRI.search(product, linear=False)) == 1
    assert len(KpnI.search(product, linear=False)) == 1
    assert len(HindIII.search(product, linear=False)) == 1
