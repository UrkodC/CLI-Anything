from __future__ import annotations

import pytest
from Bio import SeqIO

from cli_anything.snapgene.core import convert, dnafile, seqtools
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.tests._helpers import write_snapgene

SEQ = "ATGAAATAA" + "CCGGTACCAGTCAAGTTCGA" + "TCA"  # 32 bp, circular
FEATURES = [
    {"name": "orf", "type": "CDS", "strand": "+", "segments": [(1, 9)]},
    {"name": "rev", "type": "misc_feature", "strand": "-", "segments": [(11, 15), (18, 22)]},
    {"name": "wrap", "type": "misc_feature", "strand": "+", "segments": [(30, 3)]},
    {"name": "partial", "type": "CDS", "strand": "+", "reading_frame": 2, "segments": [(10, 21)]},
]
PRIMERS = [{"name": "P1", "sequence": "ATGAAATAAC", "sites": [(0, 9, 0)]}]


@pytest.fixture
def plasmid(tmp_path):
    path = write_snapgene(tmp_path / "plasmid one.dna", SEQ, features=FEATURES, primers=PRIMERS,
                          notes={"LastModified": "2022.9.2", "Description": "test vector"})
    return dnafile.load(path)


def test_genbank_round_trip(plasmid, tmp_path):
    out = tmp_path / "out.gb"
    result = convert.export(plasmid, out, "genbank")
    assert result["features"] == 5
    rec = SeqIO.read(out, "genbank")
    assert str(rec.seq) == SEQ
    assert rec.annotations["topology"] == "circular"
    assert rec.annotations["molecule_type"] == "DNA"
    assert rec.annotations["date"] == "02-SEP-2022"
    assert rec.name == "plasmid_one"
    assert rec.description == "test vector"
    labels = [f.qualifiers["label"][0] for f in rec.features]
    assert labels == ["orf", "rev", "wrap", "partial", "P1"]
    assert rec.features[4].type == "primer_bind"


def test_genbank_locations_extract_same_bases(plasmid, tmp_path):
    out = tmp_path / "out.gb"
    convert.export(plasmid, out, "genbank")
    rec = SeqIO.read(out, "genbank")
    for gb_feat, feat in zip(rec.features, plasmid.features):
        assert str(gb_feat.extract(rec.seq)) == seqtools.feature_sequence(plasmid, feat), feat.name


def test_codon_start_for_frame_two(plasmid):
    rec = convert.to_record(plasmid)
    assert rec.features[3].qualifiers["codon_start"] == ["2"]
    assert "codon_start" not in rec.features[0].qualifiers


def test_fasta(plasmid, tmp_path):
    out = tmp_path / "out.fasta"
    convert.export(plasmid, out, "fasta")
    lines = out.read_text().splitlines()
    assert lines[0] == ">plasmid_one plasmid one circular 32 bp"
    assert "".join(lines[1:]) == SEQ


def test_protein_genbank(tmp_path):
    sg = dnafile.load(write_snapgene(tmp_path / "p.prot", "MKVLA", kind="protein"))
    out = tmp_path / "p.gb"
    convert.export(sg, out, "genbank")
    rec = SeqIO.read(out, "genbank")
    assert str(rec.seq) == "MKVLA"
    assert rec.annotations["molecule_type"] == "protein"


def test_output_exists(plasmid, tmp_path):
    out = tmp_path / "out.gb"
    out.write_text("x")
    with pytest.raises(CliError) as exc:
        convert.export(plasmid, out, "genbank")
    assert exc.value.code == "output_exists"
    convert.export(plasmid, out, "genbank", overwrite=True)
    assert out.read_text().startswith("LOCUS")


def test_output_is_input(plasmid):
    with pytest.raises(CliError) as exc:
        convert.export(plasmid, plasmid.path, "genbank", overwrite=True)
    assert exc.value.code == "output_is_input"


def test_bad_output_folder(plasmid, tmp_path):
    with pytest.raises(CliError) as exc:
        convert.export(plasmid, tmp_path / "missing" / "out.gb", "genbank")
    assert exc.value.code == "bad_output"


@pytest.mark.parametrize("value,expected", [("2022.9.2", "02-SEP-2022"), ("2012.12.31", "31-DEC-2012"),
                                            ("2022.13.1", None), ("", None), (None, None)])
def test_genbank_date(value, expected):
    assert convert._genbank_date(value) == expected
