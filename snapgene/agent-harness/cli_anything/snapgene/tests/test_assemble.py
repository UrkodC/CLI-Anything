from __future__ import annotations

from cli_anything.snapgene.core import assemble, dnafile, seqtools
from cli_anything.snapgene.tests._helpers import write_snapgene

SEQ = "ATGAAACCCGGGTTTAAACCCGGGTTTAAACCCGGG"      # 36 bp


def source(tmp_path, features=None, primers=None, circular=True):
    return dnafile.load(write_snapgene(tmp_path / "s.dna", SEQ, circular=circular,
                                       features=features, primers=primers))


def test_feature_inside_a_piece_moves_and_keeps_its_qualifiers(tmp_path):
    sg = source(tmp_path, [{"name": "gene", "type": "CDS", "strand": "+", "segments": [(7, 15)],
                            "qualifiers": {"note": ["keep me"]}}])
    piece = assemble.take(sg, 4, 20)
    product, dropped = assemble.assemble([piece], tmp_path / "p", circular=False)
    (feat,) = product.features
    assert (feat.start, feat.end) == (4, 12)         # 7-15 minus the 3 bases before it
    assert feat.qualifiers["note"] == ["keep me"]
    assert dropped == []
    assert seqtools.feature_sequence(product, feat) == SEQ[6:15]


def test_feature_cut_in_half_is_dropped_and_reported(tmp_path):
    sg = source(tmp_path, [{"name": "half", "type": "CDS", "strand": "+", "segments": [(1, 20)]}])
    product, dropped = assemble.assemble([assemble.take(sg, 10, 30)], tmp_path / "p", circular=False)
    assert product.features == []
    assert dropped == ["s: half"]


def test_feature_that_stayed_behind_is_not_reported(tmp_path):
    sg = source(tmp_path, [{"name": "away", "type": "CDS", "strand": "+", "segments": [(1, 5)]}])
    product, dropped = assemble.assemble([assemble.take(sg, 10, 30)], tmp_path / "p", circular=False)
    assert (product.features, dropped) == ([], [])


def test_flipped_piece_turns_the_strand_round_and_keeps_the_protein(tmp_path):
    sg = source(tmp_path, [{"name": "gene", "type": "CDS", "strand": "+", "segments": [(1, 9)]}])
    before = seqtools.feature_sequence(sg, sg.features[0])
    product, _ = assemble.assemble([assemble.take(sg, 1, 36, flip=True)], tmp_path / "p", circular=False)
    (feat,) = product.features
    assert feat.strand == "-"
    assert (feat.start, feat.end) == (28, 36)
    assert seqtools.feature_sequence(product, feat) == before


def test_feature_across_the_origin(tmp_path):
    sg = source(tmp_path, [{"name": "wrap", "type": "misc_feature", "strand": "+", "segments": [(34, 3)]}])
    product, dropped = assemble.assemble([assemble.take(sg, 20, 19)], tmp_path / "p", circular=True)
    (feat,) = product.features
    assert dropped == []
    assert seqtools.feature_sequence(product, feat) == SEQ[33:] + SEQ[:3]


def test_piece_longer_than_the_file_keeps_the_first_copy(tmp_path):
    sg = source(tmp_path, [{"name": "start", "type": "misc_feature", "strand": "+", "segments": [(1, 4)]}])
    piece = assemble.take_arc(sg, 1, 40)              # 4 bases used twice
    product, dropped = assemble.assemble([piece], tmp_path / "p", circular=True)
    assert product.length == 40
    assert product.sequence == SEQ + SEQ[:4]
    (feat,) = product.features
    assert (feat.start, feat.end) == (1, 4)
    assert dropped == []


def test_label_marks_new_bases(tmp_path):
    sg = source(tmp_path)
    pieces = [assemble.take(sg, 1, 10), assemble.literal("GGCGGC", "linker", "misc_feature")]
    product, _ = assemble.assemble(pieces, tmp_path / "p", circular=False)
    (feat,) = product.features
    assert (feat.name, feat.start, feat.end, feat.type) == ("linker", 11, 16, "misc_feature")


def test_relocate_primers_keeps_the_ones_that_still_bind(tmp_path):
    sg = source(tmp_path, primers=[
        {"name": "stays", "sequence": SEQ[2:14], "sites": [(2, 13, 0)]},
        {"name": "goes", "sequence": "TTTTTTTTTTTTGGG", "sites": []},
    ])
    product, _ = assemble.assemble([assemble.take(sg, 1, 36)], tmp_path / "p", circular=True)
    assemble.relocate_primers(product, sg.primers)
    assert [p.name for p in product.primers] == ["stays"]
    (site,) = product.primers[0].sites
    assert (site.start, site.end, site.strand) == (3, 14, "+")
