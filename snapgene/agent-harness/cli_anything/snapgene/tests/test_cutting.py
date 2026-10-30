from __future__ import annotations

import pytest

from cli_anything.snapgene.core import cutting, dnafile
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.tests._helpers import write_snapgene


def plasmid(tmp_path, sequence, circular=True, name="p.dna"):
    return dnafile.load(write_snapgene(tmp_path / name, sequence, circular=circular))


def one_cut(tmp_path, site, enzyme, flank="AAAATTTTAAAA"):
    sg = plasmid(tmp_path, flank + site + flank)
    (cut,) = cutting.find_cuts(sg, [enzyme])
    return cut


def test_five_prime_overhang(tmp_path):
    cut = one_cut(tmp_path, "GAATTC", "EcoRI")
    assert (cut.site_start, cut.site_end) == (13, 18)
    assert (cut.top, cut.bottom) == (13, 17)          # G^AATT_C
    assert (cut.kind, cut.overhang) == ("5' overhang", "AATT")
    assert (cut.restore_before, cut.restore_after) == ("G", "C")


def test_three_prime_overhang(tmp_path):
    cut = one_cut(tmp_path, "GGTACC", "KpnI")
    assert (cut.top, cut.bottom) == (17, 13)          # G_GTAC^C
    assert (cut.kind, cut.overhang) == ("3' overhang", "GTAC")
    assert (cut.restore_before, cut.restore_after) == ("GGTAC", "GTACC")


def test_blunt(tmp_path):
    cut = one_cut(tmp_path, "CCCGGG", "SmaI")
    assert (cut.top, cut.bottom) == (15, 15)          # CCC^GGG
    assert (cut.kind, cut.overhang) == ("blunt", "")
    assert (cut.restore_before, cut.restore_after) == ("CCC", "GGG")


def test_enzyme_that_cuts_outside_its_site_restores_nothing(tmp_path):
    cut = one_cut(tmp_path, "GGTCTC", "BsaI")
    assert cut.kind == "5' overhang"
    assert (cut.restore_before, cut.restore_after) == ("", "")


def test_site_across_the_origin(tmp_path):
    # TTC at the end and GAA at the start make one EcoRI site over the origin.
    sg = plasmid(tmp_path, "ATTCAAAATTTTAAAAGGGTTTGGGTTTGA")
    (cut,) = cutting.find_cuts(sg, ["EcoRI"])
    assert (cut.site_start, cut.site_end) == (29, 4)
    assert cut.site_seq == "GAATTC"
    assert (cut.top, cut.bottom) == (29, 3)


def test_double_digest_pieces(tmp_path):
    sg = plasmid(tmp_path, "AAAA" + "GAATTC" + "TTTTTTTTTT" + "AAGCTT" + "AAAAAAAA")
    cuts = cutting.find_cuts(sg, ["EcoRI", "HindIII"])
    assert [(c.enzyme, c.top) for c in cuts] == [("EcoRI", 5), ("HindIII", 21)]
    pieces = cutting.fragments(sg, cuts)
    assert [(p["from"], p["to"], p["length"]) for p in pieces] == [(6, 21, 16), (22, 5, 18)]
    assert pieces[0]["left_enzyme"] == "EcoRI" and pieces[0]["right_enzyme"] == "HindIII"


def test_linear_file_keeps_its_own_ends(tmp_path):
    sg = plasmid(tmp_path, "AAAA" + "GAATTC" + "AAAA", circular=False)
    pieces = cutting.fragments(sg, cutting.find_cuts(sg, ["EcoRI"]))
    assert [p["length"] for p in pieces] == [5, 9]
    assert pieces[0]["left_end"] == "original" and pieces[1]["right_end"] == "original"


def test_unknown_enzyme_and_protein(tmp_path):
    sg = plasmid(tmp_path, "AAAAGAATTCAAAA")
    with pytest.raises(CliError) as err:
        cutting.find_cuts(sg, ["NotAnEnzyme"])
    assert err.value.code == "unknown_enzyme"
    protein = dnafile.load(write_snapgene(tmp_path / "x.prot", "MKV", kind="protein", circular=False))
    with pytest.raises(CliError) as err:
        cutting.find_cuts(protein, ["EcoRI"])
    assert err.value.code == "not_dna"
