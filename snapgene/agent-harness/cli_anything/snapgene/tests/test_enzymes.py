from __future__ import annotations

import pytest

from cli_anything.snapgene.core import dnafile, enzymes
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.tests._helpers import write_snapgene


def load(tmp_path, seq, name="e.dna", **kw):
    return dnafile.load(write_snapgene(tmp_path / name, seq, **kw))


def test_position_is_last_base_before_cut(tmp_path):
    sg = load(tmp_path, "AAAAGAATTCAAAA", circular=False)  # G^AATTC, G at 5
    (row,) = enzymes.cut_sites(sg, ["EcoRI"])
    assert row == {"enzyme": "EcoRI", "site": "GAATTC", "cuts": 1, "positions": [5]}


def test_site_across_origin(tmp_path):
    sg = load(tmp_path, "ATTCAAAAAAAAGA")  # GAATTC = positions 13,14,1,2,3,4
    (row,) = enzymes.cut_sites(sg, ["ecori"])
    assert row["positions"] == [13]


def test_named_enzyme_that_does_not_cut(tmp_path):
    sg = load(tmp_path, "AAAAGAATTCAAAA", circular=False)
    rows = enzymes.cut_sites(sg, ["EcoRI", "BamHI"])
    assert {r["enzyme"]: r["cuts"] for r in rows} == {"EcoRI": 1, "BamHI": 0}


def test_unknown_enzyme(tmp_path):
    sg = load(tmp_path, "AAAA", circular=False)
    with pytest.raises(CliError) as exc:
        enzymes.cut_sites(sg, ["FooI"])
    assert exc.value.code == "unknown_enzyme"


def test_default_lists_unique_cutters_only(tmp_path):
    sg = load(tmp_path, "AAGAATTCAAGGATCCAAGGATCCAA", circular=False)
    names = {r["enzyme"] for r in enzymes.cut_sites(sg)}
    assert "EcoRI" in names
    assert "BamHI" not in names
    assert "BamHI" in {r["enzyme"] for r in enzymes.cut_sites(sg, max_cuts=2)}


def test_protein_error(tmp_path):
    with pytest.raises(CliError) as exc:
        enzymes.cut_sites(load(tmp_path, "MKV", name="p.prot", kind="protein"))
    assert exc.value.code == "not_dna"
