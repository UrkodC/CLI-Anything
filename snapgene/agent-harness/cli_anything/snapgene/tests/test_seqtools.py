from __future__ import annotations

import pytest

from cli_anything.snapgene.core import dnafile, seqtools
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.tests._helpers import write_snapgene

ORF = "CC" + "ATG" + "AAA" * 3 + "TAA" + "CC"  # ATG at 3, stop ends at 17


def load(tmp_path, seq, name="s.dna", **kw):
    return dnafile.load(write_snapgene(tmp_path / name, seq, **kw))


def test_subseq_linear(tmp_path):
    sg = load(tmp_path, "AACCGGTTAC", circular=False)
    assert seqtools.subseq(sg, 2, 4) == "ACC"
    with pytest.raises(CliError) as exc:
        seqtools.subseq(sg, 9, 2)
    assert exc.value.code == "bad_range"


def test_subseq_circular_crosses_origin(tmp_path):
    sg = load(tmp_path, "AACCGGTTAC")
    assert seqtools.subseq(sg, 9, 2) == "ACAA"


@pytest.mark.parametrize("start,end", [(0, 3), (1, 11), (11, 11)])
def test_subseq_out_of_range(tmp_path, start, end):
    sg = load(tmp_path, "AACCGGTTAC")
    with pytest.raises(CliError) as exc:
        seqtools.subseq(sg, start, end)
    assert exc.value.code == "bad_range"


def test_feature_sequence_minus_and_origin(tmp_path):
    feats = [{"name": "rev", "strand": "-", "segments": [(1, 3), (5, 6)]},
             {"name": "wrap", "strand": "+", "segments": [(10, 3)]}]
    sg = load(tmp_path, "AAATTTGGGCCC", features=feats)
    rev, wrap = sg.features
    assert seqtools.feature_sequence(sg, rev) == seqtools.revcomp("AAATT")
    assert seqtools.feature_sequence(sg, wrap) == "CCCAAA"


def test_coding_sequence_frame_two(tmp_path):
    sg = load(tmp_path, "TATGAAATAG", circular=False,
              features=[{"name": "cds", "type": "CDS", "strand": "+", "reading_frame": 2, "segments": [(1, 10)]}])
    assert seqtools.coding_sequence(sg, sg.features[0]) == "ATGAAATAG"
    assert seqtools.translate(seqtools.coding_sequence(sg, sg.features[0])) == "MK*"


def test_translate_drops_partial_codon():
    assert seqtools.translate("ATGAAATAG") == "MK*"
    assert seqtools.translate("ATGAA") == "M"


def test_snapgene_translation_normalises():
    feat = dnafile.Feature(0, "x", "CDS", "+", [dnafile.Segment(1, 9)], {"translation": ["MK,?L*"]})
    assert seqtools.snapgene_translation(feat) == "MKXL*"


def test_find_motif_both_strands(tmp_path):
    sg = load(tmp_path, "AACGTT", circular=False)
    hits = seqtools.find_motif(sg, "aac")
    assert [(h["start"], h["end"], h["strand"]) for h in hits] == [(1, 3, "+"), (4, 6, "-")]


def test_find_motif_palindrome_once(tmp_path):
    sg = load(tmp_path, "GAATTCAAAAGGATCC", circular=False)
    assert [(h["start"], h["end"], h["strand"]) for h in seqtools.find_motif(sg, "GAATTC")] == [(1, 6, "+")]


def test_find_motif_iupac(tmp_path):
    sg = load(tmp_path, "GATGAC", circular=False)
    assert [h["start"] for h in seqtools.find_motif(sg, "GAN")] == [1, 4]


def test_find_motif_across_origin(tmp_path):
    circ = load(tmp_path, "GTAAAAAAAC")
    hits = seqtools.find_motif(circ, "ACG")
    assert [(h["start"], h["end"], h["strand"]) for h in hits] == [(9, 1, "+"), (10, 2, "-")]
    lin = load(tmp_path, "GTAAAAAAAC", name="lin.dna", circular=False)
    assert seqtools.find_motif(lin, "ACG") == []


def test_find_motif_protein(tmp_path):
    sg = load(tmp_path, "MKVLA", name="p.prot", kind="protein")
    assert [(h["start"], h["end"], h["strand"]) for h in seqtools.find_motif(sg, "kv")] == [(2, 3, "+")]


@pytest.mark.parametrize("motif,kind", [("ACGZ", "dna"), ("", "dna"), ("K1", "protein")])
def test_bad_motif(motif, kind):
    with pytest.raises(CliError) as exc:
        seqtools.motif_regex(motif, kind)
    assert exc.value.code == "bad_motif"


def test_orfs_plus_strand(tmp_path):
    sg = load(tmp_path, ORF, circular=False)
    orfs = seqtools.find_orfs(sg, min_codons=4)
    assert [(o["strand"], o["start"], o["end"], o["length_aa"], o["protein"]) for o in orfs] == [
        ("+", 3, 17, 4, "MKKK*")]
    assert seqtools.find_orfs(sg, min_codons=5) == []


def test_orfs_minus_strand_range_on_top_strand(tmp_path):
    sg = load(tmp_path, seqtools.revcomp(ORF), circular=False)
    orfs = seqtools.find_orfs(sg, min_codons=4)
    assert [(o["strand"], o["start"], o["end"], o["protein"]) for o in orfs] == [("-", 3, 17, "MKKK*")]
    assert seqtools.revcomp(seqtools.subseq(sg, 3, 17)).startswith("ATG")


def test_orfs_across_origin(tmp_path):
    rotated = ORF[8:] + ORF[:8]
    circ = seqtools.find_orfs(load(tmp_path, rotated), min_codons=4)
    assert ("+", 14, 9, "MKKK*") in [(o["strand"], o["start"], o["end"], o["protein"]) for o in circ]
    lin = seqtools.find_orfs(load(tmp_path, rotated, name="lin.dna", circular=False), min_codons=4)
    assert all(o["start"] != 14 for o in lin)


def test_orfs_protein_error(tmp_path):
    with pytest.raises(CliError) as exc:
        seqtools.find_orfs(load(tmp_path, "MKV", name="p.prot", kind="protein"))
    assert exc.value.code == "not_dna"
