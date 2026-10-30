from __future__ import annotations

import pytest

from cli_anything.snapgene.core import dnafile
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.tests._helpers import cookie, packet, write_snapgene


def test_sequence_topology_and_flags(tmp_path):
    path = write_snapgene(tmp_path / "p.dna", "acgtACGT", flags=0x1F)
    sg = dnafile.load(path)
    assert sg.kind == "dna"
    assert sg.sequence == "ACGTACGT"
    assert sg.circular and sg.double_stranded
    assert sg.methylation == {"dam": True, "dcm": True, "ecoki": True}
    assert sg.name == "p"


def test_linear_single_strand(tmp_path):
    sg = dnafile.load(write_snapgene(tmp_path / "l.dna", "ACGT", flags=0x00))
    assert not sg.circular and not sg.double_stranded
    assert sg.summary()["topology"] == "linear"


def test_features_segments_qualifiers_and_frame(tmp_path):
    feats = [
        {"name": "geneA", "type": "CDS", "strand": "-", "reading_frame": -2,
         "segments": [(1, 10), (11, 12, "gap"), (13, 30)],
         "qualifiers": {"note": ["<html><body>has <i>italic</i> &amp; more</body></html>"], "codon_start": [1]}},
        {"name": "site", "segments": [(5, 8)]},
    ]
    sg = dnafile.load(write_snapgene(tmp_path / "f.dna", "A" * 40, features=feats))
    a, b = sg.features
    assert (a.name, a.type, a.strand, a.reading_frame) == ("geneA", "CDS", "-", 2)
    assert [(s.start, s.end) for s in a.segments] == [(1, 10), (13, 30)]
    assert (a.start, a.end) == (1, 30)
    assert a.qualifiers["note"] == ["has italic & more"]
    assert a.qualifiers["codon_start"] == ["1"]
    assert (b.strand, b.type, b.reading_frame) == ("none", "misc_feature", 1)


def test_primers_one_based_and_simplified_dropped(tmp_path):
    primers = [{"name": "P1", "sequence": "acgtacgtac", "description": "<html><body>fwd</body></html>",
                "sites": [(0, 9, 0), (0, 9, 0, True), (20, 29, 1)]}]
    sg = dnafile.load(write_snapgene(tmp_path / "p.dna", "A" * 40, primers=primers))
    (p,) = sg.primers
    assert p.sequence == "ACGTACGTAC"
    assert p.description == "fwd"
    assert [(s.start, s.end, s.strand) for s in p.sites] == [(1, 10, "+"), (21, 30, "-")]


def test_notes(tmp_path):
    notes = {"Type": "Synthetic", "Description": "<html><body>A <b>vector</b></body></html>",
             "LastModified": "2022.9.2", "Unknown": "x"}
    sg = dnafile.load(write_snapgene(tmp_path / "n.dna", "ACGT", notes=notes))
    assert sg.notes == {"type": "Synthetic", "description": "A vector", "modified": "2022.9.2"}


def test_protein_and_rna(tmp_path):
    prot = dnafile.load(write_snapgene(tmp_path / "x.prot", "MKV", kind="protein"))
    assert (prot.kind, prot.circular, prot.sequence) == ("protein", False, "MKV")
    rna = dnafile.load(write_snapgene(tmp_path / "x.rna", "ACGU", kind="rna", circular=False))
    assert (rna.kind, rna.sequence, rna.is_nucleotide) == ("rna", "ACGU", True)


def test_not_a_snapgene_file(tmp_path):
    path = tmp_path / "x.dna"
    path.write_text(">fasta\nACGT\n")
    with pytest.raises(CliError) as exc:
        dnafile.load(path)
    assert exc.value.code == "bad_file"


def test_cut_short_file(tmp_path):
    path = tmp_path / "x.dna"
    path.write_bytes(cookie() + packet(0x00, b"\x03ACGTACGT")[:-3])
    with pytest.raises(CliError) as exc:
        dnafile.load(path)
    assert exc.value.code == "bad_file"


def test_unsupported_type(tmp_path):
    path = write_snapgene(tmp_path / "x.dnaln", "ACGT", seq_type=3)
    with pytest.raises(CliError) as exc:
        dnafile.load(path)
    assert exc.value.code == "unsupported_file"


def test_no_sequence_packet(tmp_path):
    path = tmp_path / "x.dna"
    path.write_bytes(cookie() + packet(0x06, b"<Notes/>"))
    with pytest.raises(CliError) as exc:
        dnafile.load(path)
    assert exc.value.code == "bad_file"


def test_missing_file(tmp_path):
    with pytest.raises(CliError) as exc:
        dnafile.load(tmp_path / "nope.dna")
    assert exc.value.code == "no_file"


def test_find_feature(tmp_path):
    feats = [{"name": "GFP", "segments": [(1, 5)]}, {"name": "dup", "segments": [(1, 5)]},
             {"name": "Dup", "segments": [(6, 9)]}]
    sg = dnafile.load(write_snapgene(tmp_path / "f.dna", "A" * 20, features=feats))
    assert dnafile.find_feature(sg, "gfp").name == "GFP"
    assert dnafile.find_feature(sg, index=2).start == 6
    with pytest.raises(CliError) as exc:
        dnafile.find_feature(sg, "dup")
    assert exc.value.code == "ambiguous_feature"
    assert "1 (1-5)" in exc.value.message and "2 (6-9)" in exc.value.message
    with pytest.raises(CliError) as exc:
        dnafile.find_feature(sg, "mCherry")
    assert exc.value.code == "feature_not_found"
    with pytest.raises(CliError) as exc:
        dnafile.find_feature(sg, index=7)
    assert exc.value.code == "feature_not_found"
