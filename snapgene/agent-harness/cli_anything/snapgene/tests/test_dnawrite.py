"""Writing .dna files: the packets, the round trip, and what is refused."""
from __future__ import annotations

import dataclasses

import pytest
from Bio import SeqIO

from cli_anything.snapgene.core import dnafile, dnawrite, history, sgxml
from cli_anything.snapgene.core.dnafile import Feature, Primer, Segment, BindingSite
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.tests._helpers import TEST_GENE, filler, write_snapgene


@pytest.fixture
def plasmid(tmp_path):
    """A small plasmid with a two-segment CDS, a primer and notes."""
    sequence = filler(300, 71) + TEST_GENE + filler(300, 72)
    gene = (301, 300 + len(TEST_GENE))
    path = write_snapgene(
        tmp_path / "source.dna", sequence,
        features=[
            {"name": "gene", "type": "CDS", "strand": "+",
             "segments": [(gene[0], gene[0] + 2), (gene[0] + 3, gene[1])],
             "qualifiers": {"codon_start": [1], "product": ["a test protein"],
                            "note": ["bigger than 5 & <b>bold</b>"]}},
            {"name": "backwards", "type": "misc_feature", "strand": "-", "segments": [(10, 60)]},
        ],
        primers=[{"name": "fwd", "sequence": TEST_GENE[:20], "description": "a primer",
                  "sites": [(300, 319, 0)]}],
        notes={"Description": "made for a test", "Organism": "synthetic"})
    return dnafile.load(path)


def reread(sg, tmp_path, name="out.dna"):
    out = tmp_path / name
    dnawrite.write(sg, out)
    return dnafile.load(out)


# --- the round trip ---------------------------------------------------------

def test_a_written_file_reads_back_the_same(plasmid, tmp_path):
    again = reread(plasmid, tmp_path)
    assert again.sequence == plasmid.sequence
    assert again.circular == plasmid.circular
    assert again.double_stranded == plasmid.double_stranded
    assert again.features == plasmid.features
    assert again.notes["description"] == plasmid.notes["description"]
    assert again.notes["organism"] == "synthetic"


def test_biopython_reads_the_same_file(plasmid, tmp_path):
    out = tmp_path / "bio.dna"
    dnawrite.write(plasmid, out)
    record = SeqIO.read(out, "snapgene")
    assert str(record.seq).upper() == plasmid.sequence
    assert record.annotations["topology"] == "circular"
    assert "gene" in [f.qualifiers.get("label", [""])[0] for f in record.features]


def test_primers_and_their_binding_sites_survive(plasmid, tmp_path):
    again = reread(plasmid, tmp_path)
    assert [p.name for p in again.primers] == ["fwd"]
    assert again.primers[0].sequence == plasmid.primers[0].sequence
    assert again.primers[0].description == "a primer"
    assert [(s.start, s.end, s.strand) for s in again.primers[0].sites] == [(301, 320, "+")]


def test_a_minus_strand_primer_site_keeps_its_strand(plasmid, tmp_path):
    sg = dataclasses.replace(plasmid, primers=[
        Primer(0, "rev", "ACGTACGTACGTACGTACGT", "", [BindingSite(100, 119, "-", 60.0)])])
    again = reread(sg, tmp_path)
    assert [(s.start, s.end, s.strand) for s in again.primers[0].sites] == [(100, 119, "-")]


def test_rich_text_in_a_qualifier_comes_back_as_words(plasmid, tmp_path):
    again = reread(plasmid, tmp_path)
    gene = [f for f in again.features if f.name == "gene"][0]
    assert gene.qualifiers["note"] == ["bigger than 5 & bold"]
    assert gene.qualifiers["codon_start"] == ["1"]


def test_several_lines_of_description_survive(plasmid, tmp_path):
    sg = dataclasses.replace(plasmid, notes={"description": "first line\nsecond line"})
    assert reread(sg, tmp_path).notes["description"] == "first line\nsecond line"


def test_the_saved_translation_is_not_written(plasmid, tmp_path):
    """SnapGene works it out from the bases, so a stale copy is left out."""
    sg = dataclasses.replace(plasmid, features=[Feature(
        0, "gene", "CDS", "+", [Segment(301, 597)], {"translation": ["MKV*"]})])
    out = tmp_path / "notrans.dna"
    dnawrite.write(sg, out)
    assert b"translation" not in out.read_bytes()


def test_feature_colours_are_kept(plasmid, tmp_path):
    """Keeps the map looking the same, so a product is not all white."""
    sg = dataclasses.replace(plasmid, features=[Feature(
        0, "gene", "CDS", "+", [Segment(301, 597, color="#ff0000")])])
    assert reread(sg, tmp_path).features[0].segments[0].color == "#ff0000"


def test_a_reading_frame_on_a_reverse_feature_survives(plasmid, tmp_path):
    sg = dataclasses.replace(plasmid, features=[Feature(
        0, "gene", "CDS", "-", [Segment(301, 597)], reading_frame=2)])
    again = reread(sg, tmp_path).features[0]
    assert (again.strand, again.reading_frame) == ("-", 2)


def test_topology_and_methylation_survive(plasmid, tmp_path):
    sg = dataclasses.replace(plasmid, circular=False,
                             methylation={"dam": True, "dcm": False, "ecoki": True})
    again = reread(sg, tmp_path)
    assert again.circular is False
    assert again.methylation == {"dam": True, "dcm": False, "ecoki": True}


# --- what it refuses --------------------------------------------------------

def test_the_name_must_end_in_dna(plasmid, tmp_path):
    with pytest.raises(CliError) as err:
        dnawrite.write(plasmid, tmp_path / "out.gb")
    assert err.value.code == "bad_output"


def test_it_will_not_write_over_its_own_input(plasmid):
    with pytest.raises(CliError) as err:
        dnawrite.write(plasmid, plasmid.path)
    assert err.value.code == "output_is_input"


def test_an_existing_file_needs_overwrite(plasmid, tmp_path):
    out = tmp_path / "twice.dna"
    dnawrite.write(plasmid, out)
    with pytest.raises(CliError) as err:
        dnawrite.write(plasmid, out)
    assert err.value.code == "output_exists"
    assert dnawrite.write(plasmid, out, overwrite=True)["output"] == str(out)


def test_a_missing_folder_is_reported(plasmid, tmp_path):
    with pytest.raises(CliError) as err:
        dnawrite.write(plasmid, tmp_path / "nowhere" / "out.dna")
    assert err.value.code == "bad_output"


def test_protein_files_are_not_written(tmp_path):
    protein = dnafile.load(write_snapgene(tmp_path / "p.prot", "MKVL", kind="protein", circular=False))
    with pytest.raises(CliError) as err:
        dnawrite.write(protein, tmp_path / "p.dna")
    assert err.value.code == "not_dna"


def test_the_report_counts_what_was_written(plasmid, tmp_path):
    out = tmp_path / "counted.dna"
    report = dnawrite.write(plasmid, out)
    assert report["format"] == "snapgene"
    assert report["length"] == plasmid.length
    assert report["features"] == 2 and report["primers"] == 1
    assert report["history_steps"] == 0
    assert report["bytes"] == out.stat().st_size


# --- the history that goes with it ------------------------------------------

def test_a_history_goes_in_and_comes_back(plasmid, tmp_path):
    vector = history.file_node(plasmid, [(1, 100)])
    top = history.made_node("product.dna", plasmid.length, True, "inFusionCloning",
                            [(vector, history.summary("replace", 0, 99))], [(1, 10)])
    sg = dataclasses.replace(plasmid, history=history.tree(top))
    again = reread(sg, tmp_path)
    assert [s["operation"] for s in history.steps(again.history)] == ["inFusionCloning", "invalid"]


def test_the_top_step_is_named_after_the_file(plasmid, tmp_path):
    """SnapGene prints that name under the product on its History view."""
    top = history.made_node("placeholder", plasmid.length, True, "inFusionCloning",
                            [(history.file_node(plasmid, [(1, 100)]), history.summary("replace", 0, 99))],
                            [(1, 10)])
    sg = dataclasses.replace(plasmid, history=history.tree(top))
    again = reread(sg, tmp_path, "named.dna")
    assert history.steps(again.history)[0]["name"] == "named.dna"


def test_a_history_of_the_wrong_length_is_refused(plasmid, tmp_path):
    top = history.made_node("product.dna", plasmid.length + 1, True, "inFusionCloning",
                            [(history.file_node(plasmid, [(1, 100)]), history.summary("replace", 0, 99))],
                            [(1, 10)])
    sg = dataclasses.replace(plasmid, history=history.tree(top))
    with pytest.raises(CliError) as err:
        dnawrite.write(sg, tmp_path / "bad.dna")
    assert err.value.code == "bad_history"


# --- the XML itself ---------------------------------------------------------

def test_rich_text_is_escaped_inside_the_attribute():
    assert sgxml.rich("a & b <c>") == "<html><body>a &amp; b &lt;c&gt;</body></html>"
    assert sgxml.rich("one\ntwo") == "<html><body>one<br>two</body></html>"


def test_every_binding_site_is_written_twice():
    """SnapGene repeats each site with simplified="1"; its reader expects both."""
    primer = Primer(0, "p", "ACGT", "", [BindingSite(5, 8, "+", 60.0)])
    text = sgxml.primers_element([primer])
    sites = text.findall(".//BindingSite")
    assert [s.get("simplified") for s in sites] == [None, "1"]
    assert all(s.get("location") == "4-7" for s in sites)
