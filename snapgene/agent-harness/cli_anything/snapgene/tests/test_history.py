"""The cloning history: the tree, the rules SnapGene needs, and what products carry.

The rule the checks exist for: SnapGene 8.2.3 quits as it opens a file whose
history step has inputs but no matching InputSummary, with no error message.
It was found by opening test files; see core/history.py.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from cli_anything.snapgene.core import assemble, dnafile, gateway, history, infusion
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.tests._helpers import (
    TEST_GENE, destination_vector, entry_clone, filler, takara_vector, write_snapgene,
)


@pytest.fixture
def parent(tmp_path):
    return dnafile.load(write_snapgene(
        tmp_path / "parent.dna", filler(900, 81),
        features=[{"name": "thing", "type": "CDS", "strand": "+", "segments": [(100, 300)]}]))


def built(parent, operation="inFusionCloning", rows=None, length=900):
    inputs = [(history.file_node(parent, [(1, 100)]),
               rows if rows is not None else history.summary("replace", 0, 99))]
    return history.made_node("product.dna", length, True, operation, inputs, [(1, 20)])


# --- the shape of a tree ----------------------------------------------------

def test_a_tree_holds_the_product_over_its_parent(parent):
    text = history.tree(built(parent))
    steps = history.steps(text)
    assert [(s["name"], s["operation"], s["depth"]) for s in steps] == [
        ("product.dna", "inFusionCloning", 0),
        ("parent.dna", history.NOT_MADE_HERE, 1),
    ]


def test_every_step_gets_its_own_number(parent):
    root = ET.fromstring(history.tree(built(parent)))
    ids = [node.get("ID") for node in root.iter("Node")]
    assert sorted(ids) == ["0", "1"] and len(set(ids)) == 2


def test_the_parent_is_painted_as_used_and_the_product_as_new(parent):
    root = ET.fromstring(history.tree(built(parent)))
    kinds = [c.get("type") for c in root.iter("StrandColors")]
    assert sorted(set(kinds)) == ["Input", "Product"]
    colours = {c.get("colors") for c in root.iter("ColorRange")}
    assert colours == {"black", "red"}


def test_a_range_that_crosses_the_origin_is_split(parent):
    """A colour range cannot wrap, so the two arcs are written separately."""
    node = history.file_node(parent, [(850, 50)])
    ranges = [c.get("range") for c in node.iter("ColorRange")]
    assert ranges[:2] == ["849..899", "0..49"]


def test_the_parents_own_history_is_carried_on(tmp_path, parent):
    """So a product of a product still shows the whole story."""
    grandparent = history.file_node(parent, [(1, 50)])
    older = history.made_node("middle.dna", parent.length, True, "inFusionCloning",
                              [(grandparent, history.summary("replace", 0, 49))], [(1, 10)],
                              features=parent.features)
    middle = dnafile.load(write_snapgene(tmp_path / "middle.dna", parent.sequence))
    middle.history = history.tree(older)
    names = [s["name"] for s in history.steps(history.tree(built(middle)))]
    assert names == ["product.dna", "middle.dna", "parent.dna"]


def test_an_unusable_parent_history_is_dropped_not_passed_on(tmp_path, parent):
    """A tree that would make SnapGene quit never reaches the product."""
    parent.history = ('<?xml version="1.0"?><HistoryTree>'
                      f'<Node name="x.dna" seqLen="{parent.length}" ID="1" operation="inFusionCloning">'
                      '<Node name="y.dna" seqLen="100" ID="0" operation="invalid"/>'
                      '</Node></HistoryTree>')       # an input, but no InputSummary
    assert [s["name"] for s in history.steps(history.tree(built(parent)))] == ["product.dna", "parent.dna"]


# --- the rules that keep SnapGene alive -------------------------------------

def test_a_step_with_an_input_but_no_summary_is_refused(parent):
    with pytest.raises(CliError) as err:
        history.tree(built(parent, rows=ET.Element("NotASummary")))
    assert err.value.code == "bad_history"
    assert "input summaries" in err.value.message


def test_a_tree_of_the_wrong_length_is_refused(parent):
    with pytest.raises(CliError) as err:
        history.check(ET.fromstring(history.tree(built(parent))), 901)
    assert err.value.code == "bad_history"


def test_a_tree_needs_exactly_one_top_step(parent):
    holder = ET.fromstring(history.tree(built(parent)))
    holder.append(ET.fromstring(history.tree(built(parent))).find("Node"))
    with pytest.raises(CliError) as err:
        history.check(holder, 900)
    assert err.value.code == "bad_history"


def test_a_summary_with_names_carries_its_site_counts():
    """SnapGene prints 'Replace BamHI (444) - HindIII (474)' from these."""
    row = history.summary("replace", 444, 474, "BamHI", "HindIII")
    assert row.get("name1") == "BamHI" and row.get("val2") == "474"
    assert row.get("siteCount1") == "1" and row.get("siteCount2") == "1"
    assert history.summary("insert", 0, 737).get("siteCount1") is None


def test_steps_of_nothing_is_an_empty_list():
    assert history.steps(None) == [] and history.steps("") == []


# --- what the cloning commands put in ---------------------------------------

def test_an_infusion_product_tells_its_whole_story(tmp_path):
    vector = dnafile.load(takara_vector(tmp_path / "vector.dna"))
    source = dnafile.load(write_snapgene(tmp_path / "gene.dna", filler(200, 83) + TEST_GENE))
    insert = assemble.take(source, 201, 200 + len(TEST_GENE), label="gene")
    opened = infusion.open_vector(vector, ["EcoRI", "HindIII"])
    result = infusion.design(opened, insert, insert_name="gene")
    steps = history.steps(result["_product"].history)
    assert [(s["name"], s["operation"]) for s in steps] == [
        (f"{result['_product'].name}.dna", "inFusionCloning"),
        ("vector.dna", history.NOT_MADE_HERE),
        ("gene In-Fusion PCR.dna", "amplifyFragment"),
        ("gene.dna", history.NOT_MADE_HERE),
    ]
    assert steps[2]["oligos"] == ["gene In-Fusion FOR", "gene In-Fusion REV"]
    # The PCR step is the fragment to order: the gene plus both homology arms.
    assert steps[2]["length"] == result["fragment"]["length"]


def test_an_infusion_product_says_in_words_what_was_done(tmp_path):
    vector = dnafile.load(takara_vector(tmp_path / "vector.dna"))
    source = dnafile.load(write_snapgene(tmp_path / "gene.dna", filler(200, 84) + TEST_GENE))
    insert = assemble.take(source, 201, 200 + len(TEST_GENE), label="gene")
    opened = infusion.open_vector(vector, ["EcoRI", "HindIII"])
    said = infusion.design(opened, insert, insert_name="gene")["_product"].notes["description"]
    assert "In-Fusion cloning (Takara)" in said
    assert "Vector: vector.dna" in said
    assert f"Insert: gene, {len(TEST_GENE)} bp, from gene.dna, bases 201-497" in said
    assert "gene In-Fusion FOR" in said


def test_typed_in_bases_have_no_pcr_step(tmp_path):
    """There is no template to amplify, so the fragment sits there on its own."""
    vector = dnafile.load(takara_vector(tmp_path / "vector.dna"))
    opened = infusion.open_vector(vector, ["EcoRI", "HindIII"])
    product = infusion.design(opened, assemble.literal(TEST_GENE, "gene"), insert_name="gene")["_product"]
    assert [(s["name"], s["operation"]) for s in history.steps(product.history)][2:] == [
        ("gene In-Fusion fragment.dna", history.NOT_MADE_HERE)]
    assert "typed in" in product.notes["description"]


def test_a_gateway_product_tells_its_story(tmp_path):
    entry = dnafile.load(entry_clone(tmp_path / "pENTR-gene.dna"))
    dest = dnafile.load(destination_vector(tmp_path / "pDEST-x.dna"))
    product = gateway.lr(entry, dest, tmp_path / "exp.dna")["_product"]
    steps = history.steps(product.history)
    assert [(s["name"], s["operation"]) for s in steps] == [
        (f"{product.name}.dna", "gatewayLRCloning"),
        ("pDEST-x.dna", history.NOT_MADE_HERE),
        ("pENTR-gene.dna", history.NOT_MADE_HERE),
    ]
    said = product.notes["description"]
    assert "Gateway LR cloning (Invitrogen)" in said
    assert "Destination vector: pDEST-x.dna" in said and "ccdB" in said
    assert "New sites: attB1" in said


def test_the_gateway_summaries_name_the_att_sites(tmp_path):
    """SnapGene prints those names on its History view."""
    entry = dnafile.load(entry_clone(tmp_path / "pENTR-gene.dna"))
    dest = dnafile.load(destination_vector(tmp_path / "pDEST-x.dna"))
    product = gateway.lr(entry, dest, tmp_path / "exp.dna")["_product"]
    rows = ET.fromstring(product.history).find("Node").findall("InputSummary")
    assert [(r.get("name1"), r.get("name2")) for r in rows] == [("attR1", "attR2"), ("attL1", "attL2")]


def test_an_edit_keeps_the_words_but_not_the_tree(tmp_path):
    """The tree describes the old bases, so only the written story carries on."""
    from cli_anything.snapgene.core import edit as edit_core
    source = dnafile.load(write_snapgene(tmp_path / "plasmid.dna", filler(900, 85),
                                         notes={"Description": "bought from a catalogue"}))
    source.history = history.tree(built(source))
    product = edit_core.edit(source, "GGGG", at=100)["_product"]
    assert product.history is None
    assert product.notes["description"].startswith("bought from a catalogue")
    assert "Edited with cli-anything-snapgene" in product.notes["description"]
