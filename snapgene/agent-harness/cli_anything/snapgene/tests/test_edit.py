from __future__ import annotations

import pytest

from cli_anything.snapgene.core import dnafile, edit as edit_core, seqtools
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.tests._helpers import write_snapgene

SEQ = "ATGAAACCCGGGTTTAAACCCGGGTTTAAACCCGGG"      # 36 bp
FEATS = [
    {"name": "early", "type": "misc_feature", "strand": "+", "segments": [(1, 6)]},
    {"name": "late", "type": "misc_feature", "strand": "+", "segments": [(25, 30)]},
    {"name": "over", "type": "misc_feature", "strand": "+", "segments": [(10, 20)]},
]


@pytest.fixture
def plasmid(tmp_path):
    return dnafile.load(write_snapgene(tmp_path / "p.dna", SEQ, features=FEATS,
                                       primers=[{"name": "pr", "sequence": SEQ[0:12], "sites": [(0, 11, 0)]}]))


def names(product):
    return {f.name: (f.start, f.end) for f in product.features}


def test_put_bases_in(plasmid):
    result = edit_core.edit(plasmid, "TTTT", at=6)
    product = result["_product"]
    assert product.length == 40
    assert product.sequence == SEQ[:6] + "TTTT" + SEQ[6:]
    assert names(product)["early"] == (1, 6)         # before the change, unmoved
    assert names(product)["late"] == (29, 34)        # after it, moved by 4
    assert result["dropped_features"] == []


def test_put_bases_in_at_the_very_start_and_end(plasmid):
    first = edit_core.edit(plasmid, "GG", at=0)["_product"]
    assert first.sequence.startswith("GG" + SEQ[:5])
    last = edit_core.edit(plasmid, "GG", at=plasmid.length)["_product"]
    assert last.sequence.endswith(SEQ[-5:] + "GG")


def test_delete_a_part(plasmid):
    result = edit_core.edit(plasmid, "", start=7, end=12)
    product = result["_product"]
    assert product.sequence == SEQ[:6] + SEQ[12:]
    assert (result["removed_bp"], result["added_bp"]) == (6, 0)
    assert names(product)["late"] == (19, 24)
    assert result["dropped_features"] == ["p: over"]      # 10-20 was cut in half


def test_swap_a_part(plasmid):
    result = edit_core.edit(plasmid, "AAAAAAAAAA", start=10, end=20, label="new bit")
    product = result["_product"]
    assert product.length == 35
    assert names(product)["new bit"] == (10, 19)
    assert result["change"].endswith("swapped for 10 bp")


def test_delete_across_the_origin(plasmid):
    result = edit_core.edit(plasmid, "", start=34, end=3)
    product = result["_product"]
    assert product.length == 30
    assert product.sequence == SEQ[3:33]
    assert result["removed_bp"] == 6


def test_primer_follows_the_sequence(plasmid):
    result = edit_core.edit(plasmid, "TTTT", at=30)
    assert result["primers_kept"] == 1
    result = edit_core.edit(plasmid, "", start=1, end=12)
    assert result["primers_kept"] == 0


def test_protein_check_sees_a_frame_shift(tmp_path):
    cds = "ATG" + "AAACCCGGG" * 4 + "TAA"
    sg = dnafile.load(write_snapgene(tmp_path / "c.dna", cds + "TTTTTTTTT", circular=False, features=[
        {"name": "orf", "type": "CDS", "strand": "+", "segments": [(1, len(cds))]}]))
    same = edit_core.edit(sg, "AAA", at=len(cds) + 3)
    assert edit_core.protein_check(sg, same["_product"]) == []
    shifted = edit_core.edit(sg, "A", at=5)
    assert any("protein of orf changed" in w for w in edit_core.protein_check(sg, shifted["_product"]))


def test_annotate_adds_one_feature(plasmid):
    product = edit_core.annotate(plasmid, "my site", 5, 15, "promoter", "-")
    added = next(f for f in product.features if f.name == "my site")
    assert (added.type, added.strand, added.start, added.end) == ("promoter", "-", 5, 15)
    assert len(product.features) == len(plasmid.features) + 1
    assert seqtools.feature_sequence(product, added) == seqtools.revcomp(SEQ[4:15])


def test_annotate_checks_the_range(plasmid):
    with pytest.raises(CliError) as err:
        edit_core.annotate(plasmid, "x", 1, 999)
    assert err.value.code == "bad_range"


def test_bad_bases_and_bad_ranges(plasmid):
    for bases in ("ACGZ", "   "):
        with pytest.raises(CliError) as err:
            edit_core.clean_bases(bases)
        assert err.value.code == "bad_sequence"
    assert edit_core.clean_bases("acg t\nacgt") == "ACGTACGT"
    for kwargs in ({"at": 99}, {"start": 1, "end": 99}, {"start": 1, "end": 35}, {}):
        with pytest.raises(CliError):
            edit_core.edit(plasmid, "AAAA", **kwargs)


def test_linear_file_cannot_wrap(tmp_path):
    sg = dnafile.load(write_snapgene(tmp_path / "l.dna", SEQ, circular=False))
    with pytest.raises(CliError) as err:
        edit_core.edit(sg, "", start=30, end=3)
    assert err.value.code == "bad_range"
