from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from cli_anything.snapgene.core import dnafile, history, seqtools
from cli_anything.snapgene.snapgene_cli import cli
from cli_anything.snapgene.tests._helpers import (
    ATTB1, TEST_GENE, destination_vector, entry_clone, filler, takara_vector, write_snapgene,
)

CDS = "ATGAAACCCTAA"
SEQ = CDS + "GAATTCGG" + seqtools.revcomp("ATGGGGTAG") + "AAAA"


@pytest.fixture
def plasmid(tmp_path):
    feats = [
        {"name": "fwd", "type": "CDS", "strand": "+", "segments": [(1, 12)],
         "qualifiers": {"translation": ["MKP*"]}},
        {"name": "back", "type": "CDS", "strand": "-", "segments": [(21, 29)]},
    ]
    return write_snapgene(tmp_path / "p.dna", SEQ, features=feats, notes={"Type": "Synthetic"})


def run(*args):
    result = CliRunner().invoke(cli, [str(a) for a in args])
    return result, (json.loads(result.output) if "--json" in args else None)


def test_info_json(plasmid):
    result, data = run("info", plasmid, "--json")
    assert result.exit_code == 0
    assert data["ok"] is True
    assert (data["length"], data["topology"], data["feature_count"]) == (len(SEQ), "circular", 2)


def test_group_level_json(plasmid):
    result, data = run("--json", "info", plasmid)
    assert result.exit_code == 0
    assert json.loads(result.output)["kind"] == "dna"


def test_features_human(plasmid):
    result, _ = run("features", plasmid)
    assert result.exit_code == 0
    assert "name" in result.output.splitlines()[0]
    assert "back" in result.output and "21-29" in result.output


def test_seq_feature_minus(plasmid):
    result, data = run("seq", plasmid, "--feature", "back", "--json")
    assert data["sequence"] == "ATGGGGTAG"
    assert data["strand"] == "-"
    result, data = run("seq", plasmid, "--start", 13, "--end", 18, "--revcomp", "--json")
    assert (data["sequence"], data["strand"]) == ("GAATTC", "-")


def test_seq_needs_both_ends(plasmid):
    result, data = run("seq", plasmid, "--start", 3, "--json")
    assert result.exit_code == 1
    assert data == {"ok": False, "error": "Give both --start and --end.", "code": "bad_range"}


def test_translate_checks_snapgene_translation(plasmid):
    result, data = run("translate", plasmid, "--feature", "fwd", "--json")
    assert (data["protein"], data["matches_snapgene"]) == ("MKP*", True)
    result, data = run("translate", plasmid, "--feature", "back", "--json")
    assert (data["protein"], data["matches_snapgene"]) == ("MG*", None)
    result, _ = run("translate", plasmid, "--feature", "fwd")
    assert "Matches the translation saved by SnapGene." in result.output


def test_translate_protein_file_fails(tmp_path):
    prot = write_snapgene(tmp_path / "x.prot", "MKV", kind="protein")
    result, data = run("translate", prot, "--start", 1, "--end", 3, "--json")
    assert result.exit_code == 1 and data["code"] == "not_dna"


def test_find_orfs_enzymes(plasmid):
    _, data = run("find", plasmid, "GAATTC", "--json")
    assert [(h["start"], h["end"]) for h in data["hits"]] == [(13, 18)]
    _, data = run("enzymes", plasmid, "--enzyme", "EcoRI,BamHI", "--json")
    assert {r["enzyme"]: r["positions"] for r in data["enzymes"]} == {"EcoRI": [13], "BamHI": []}
    _, data = run("orfs", plasmid, "--min-aa", 2, "--json")
    assert ("+", 1, 12) in [(o["strand"], o["start"], o["end"]) for o in data["orfs"]]


def test_export_and_output_exists(plasmid, tmp_path):
    out = tmp_path / "p.gb"
    result, data = run("export", plasmid, "--json")
    assert result.exit_code == 0 and data["output"] == str(out)
    assert out.read_text().startswith("LOCUS")
    result, data = run("export", plasmid, "--json")
    assert result.exit_code == 1 and data["code"] == "output_exists"


def test_missing_file_error(tmp_path):
    result, data = run("info", tmp_path / "nope.dna", "--json")
    assert result.exit_code == 1
    assert data["code"] == "no_file"
    result, _ = run("info", tmp_path / "nope.dna")
    assert "Error:" in result.output


def test_search(plasmid, tmp_path):
    (tmp_path / "sub").mkdir()
    write_snapgene(tmp_path / "sub" / "other.dna", "CCCCCC", features=[{"name": "mCherry", "segments": [(1, 6)]}])
    (tmp_path / "bad.dna").write_text("not snapgene")
    _, data = run("search", tmp_path, "--feature", "cherry", "--json")
    assert [m["name"] for m in data["matches"]] == ["other"]
    assert data["files_scanned"] == 3 and len(data["errors"]) == 1
    _, data = run("search", tmp_path, "--seq", "GAATTC", "--json")
    assert [m["name"] for m in data["matches"]] == ["p"]
    result, data = run("search", tmp_path, "--json")
    assert data["code"] == "no_criteria"


# --- cloning and editing ---------------------------------------------------

@pytest.fixture
def vector(tmp_path):
    return takara_vector(tmp_path / "pTakara.dna")


@pytest.fixture
def insert(tmp_path):
    feats = [{"name": "gene", "type": "CDS", "strand": "+", "segments": [(1, len(TEST_GENE))]}]
    return write_snapgene(tmp_path / "gene.dna", TEST_GENE, circular=False, features=feats)


def test_cut_json_and_human(vector):
    _, data = run("cut", vector, "--enzyme", "SalI,HindIII", "--json")
    assert [c["enzyme"] for c in data["cuts"]] == ["SalI", "HindIII"]
    assert data["cuts"][0]["end"] == "5' overhang"
    assert len(data["pieces"]) == 2
    result, _ = run("cut", vector, "--enzyme", "SmaI")
    assert "blunt" in result.output


def test_infusion_json(vector, insert, tmp_path):
    out = tmp_path / "product.gb"
    _, data = run("infusion", vector, "--enzyme", "SalI", "--enzyme", "HindIII",
                  "--insert", insert, "--insert-feature", "gene", "-o", out, "--json")
    assert data["ok"] is True
    assert data["primers"][0]["sequence"].startswith("GAAGTTATCAGTCGAC")
    assert data["primers"][1]["sequence"].startswith("ATGGTCTAGAAAGCTT")
    assert data["fragment"]["length"] == len(TEST_GENE) + 32
    assert out.read_text().startswith("LOCUS")
    assert data["product"]["output"] == str(out)


def test_infusion_will_not_overwrite(vector, insert, tmp_path):
    out = tmp_path / "product.gb"
    out.write_text("keep me")
    _, data = run("infusion", vector, "--at", 400, "--insert", insert, "-o", out, "--json")
    assert data["code"] == "output_exists"
    assert out.read_text() == "keep me"


def test_infusion_needs_an_insert(vector):
    _, data = run("infusion", vector, "--at", 400, "--json")
    assert data["code"] == "bad_request"


def test_gateway_json(tmp_path):
    entry = entry_clone(tmp_path / "pENTR-gene.dna")
    dest = destination_vector(tmp_path / "pDEST-x.dna")
    out = tmp_path / "expression.gb"
    _, data = run("gateway", dest, "--entry", entry, "-o", out, "--json")
    assert data["ok"] is True
    assert [s["name"] for s in data["att_sites"]] == ["attB1", "attB2"]
    assert data["att_sites"][0]["sequence"] == ATTB1
    assert data["insert"]["length"] == len(TEST_GENE)
    assert "ccdB" in data["destination"]["cassette_features"]
    written = out.read_text()
    assert written.startswith("LOCUS") and '/label="attB1"' in written and '/label="ccdB"' not in written
    assert data["product"]["output"] == str(out)


def test_gateway_human_and_errors(tmp_path, vector):
    entry = entry_clone(tmp_path / "pENTR-gene.dna")
    dest = destination_vector(tmp_path / "pDEST-x.dna")
    result, _ = run("gateway", dest, "--entry", entry, "--no-product")
    assert result.exit_code == 0
    assert "attB1" in result.output and "no file written" in result.output
    _, data = run("gateway", vector, "--entry", entry, "--json")
    assert data["code"] == "no_att"


def test_gblock_json(insert, tmp_path):
    out = tmp_path / "order.fasta"
    _, data = run("gblock", insert, "--feature", "gene", "--flank5", "acgt", "-o", out, "--json")
    assert data["length"] == len(TEST_GENE) + 4
    assert data["sequence"].startswith("ACGT" + TEST_GENE[:10])
    assert out.read_text().startswith(">gene")


def test_edit_json(vector, tmp_path):
    out = tmp_path / "edited.gb"
    _, data = run("edit", vector, "--at", 400, "--sequence", "GGCGGC", "--label", "linker",
                  "-o", out, "--json")
    assert data["length_after"] == data["length_before"] + 6
    assert "linker" in out.read_text()
    _, data = run("edit", vector, "--range", "400-99999", "--json")
    assert data["code"] == "bad_range"


def test_pcr_json(tmp_path):
    sequence = filler(700, 31)
    path = write_snapgene(tmp_path / "t.dna", sequence[:350] + TEST_GENE + sequence[350:],
                          features=[{"name": "gene", "type": "CDS", "strand": "+",
                                     "segments": [(351, 350 + len(TEST_GENE))]}])
    out = tmp_path / "with-primers.gb"
    _, data = run("pcr", path, "--feature", "gene", "--pairs", 2, "-o", out, "--json")
    assert data["ok"] and data["target_bp"] == len(TEST_GENE)
    assert 1 <= len(data["pairs"]) <= 2
    assert "primer_bind" in out.read_text()
    _, data = run("pcr", path, "--json")
    assert data["code"] == "bad_range"


def test_primer_check_json(vector):
    sg_bases = write_snapgene(vector.parent / "known.dna", "ACGT" * 50 + "GTCAGTCAGGATCCATGACC" + "TTGA" * 40)
    _, data = run("primer-check", sg_bases, "GTCAGTCAGGATCCATGACC", "--json")
    assert data["binding_sites"][0]["start"] == 201
    assert data["tm"] > 40
    _, data = run("primer-check", sg_bases, "  ", "--json")
    assert data["code"] == "bad_sequence"


def test_annotate_json(vector, tmp_path):
    out = tmp_path / "annotated.gb"
    _, data = run("annotate", vector, "--name", "my site", "--range", "400-420",
                  "--type", "promoter", "-o", out, "--json")
    assert data["added"]["range"] == [400, 420]
    assert "my site" in out.read_text()
    _, data = run("annotate", vector, "--name", "x", "--range", "nonsense", "--json")
    assert data["code"] == "bad_range"


# --- what the commands write by default ------------------------------------

def test_infusion_writes_a_snapgene_file_by_default(vector, insert, tmp_path):
    _, data = run("infusion", vector, "--at", 400, "--insert", insert, "--json")
    out = tmp_path / "pTakara-gene.dna"
    assert data["product"]["output"] == str(out)
    assert data["product"]["format"] == "snapgene"
    again = dnafile.load(out)
    assert again.length == data["product"]["length"]
    assert "In-Fusion cloning (Takara)" in again.notes["description"]
    assert [s["operation"] for s in history.steps(again.history)][0] == "inFusionCloning"


def test_a_gb_name_still_gives_genbank(vector, insert, tmp_path):
    out = tmp_path / "product.gb"
    _, data = run("infusion", vector, "--at", 400, "--insert", insert, "-o", out, "--json")
    assert data["product"]["format"] == "genbank"
    assert out.read_text().startswith("LOCUS")


def test_gateway_writes_a_snapgene_file_by_default(tmp_path):
    entry = entry_clone(tmp_path / "pENTR-gene.dna")
    dest = destination_vector(tmp_path / "pDEST-x.dna")
    _, data = run("gateway", dest, "--entry", entry, "--name", "gene", "--json")
    out = tmp_path / "pDEST-x-gene.dna"
    assert data["product"]["output"] == str(out)
    again = dnafile.load(out)
    assert "Gateway LR cloning" in again.notes["description"]
    assert [s["name"] for s in history.steps(again.history)][1:] == ["pDEST-x.dna", "pENTR-gene.dna"]


def test_edit_and_annotate_write_snapgene_files(vector, tmp_path):
    _, data = run("edit", vector, "--at", 400, "--sequence", "GGCGGC", "--label", "linker", "--json")
    assert data["output"] == str(tmp_path / "pTakara-edited.dna")
    assert "linker" in [f.name for f in dnafile.load(data["output"]).features]
    _, data = run("annotate", vector, "--name", "my site", "--range", "400-420", "--json")
    assert data["output"] == str(tmp_path / "pTakara-annotated.dna")
    assert "my site" in [f.name for f in dnafile.load(data["output"]).features]


def test_a_product_may_not_replace_a_file_it_was_made_from(vector, insert):
    """Even with --overwrite: losing the vector you cloned from is not recoverable."""
    for target in (vector, insert):
        _, data = run("infusion", vector, "--at", 400, "--insert", insert,
                      "-o", target, "--overwrite", "--json")
        assert data["code"] == "output_is_input"
    assert dnafile.load(insert).sequence == TEST_GENE


def test_an_entry_clone_is_safe_from_its_own_product(tmp_path):
    entry = entry_clone(tmp_path / "pENTR-gene.dna")
    dest = destination_vector(tmp_path / "pDEST-x.dna")
    _, data = run("gateway", dest, "--entry", entry, "-o", entry, "--overwrite", "--json")
    assert data["code"] == "output_is_input"
    assert dnafile.load(entry).length > 0
