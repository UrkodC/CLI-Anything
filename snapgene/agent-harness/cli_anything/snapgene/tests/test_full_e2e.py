"""End-to-end tests against the real SnapGene install and the files it ships.

SnapGene is a hard dependency: these tests fail (not skip) when it is missing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import warnings
from pathlib import Path

import pytest
from Bio import SeqIO
from Bio.Restriction import EcoRI, HindIII
from Bio.Seq import Seq

from cli_anything.snapgene.core import align, assemble, convert, cutting, dnafile, enzymes, infusion, seqtools
from cli_anything.snapgene.utils import snapgene_backend

APP = snapgene_backend.find_app()
RESOURCES = APP / "Contents/Resources"
SAMPLES = RESOURCES / "sampleData/Sample project"
PUC19 = SAMPLES / "1. Annotate and Visualize/2. Annotate/2. pUC19 Annotated.dna"
GFP = SAMPLES / "2. Experimental Design/1. PCR/1. GFP.dna"
GFP_PCR = SAMPLES / "2. Experimental Design/1. PCR/2. GFP Amplified.dna"
ALL_DNA = sorted(RESOURCES.rglob("*.dna"))


def _resolve_cli(name):
    """Resolve installed CLI command; falls back to python -m for dev.

    Set env CLI_ANYTHING_FORCE_INSTALLED=1 to require the installed command.
    """
    force = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed command: {path}")
        return [path]
    if force:
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    module = name.replace("cli-anything-", "cli_anything.") + "." + name.split("-")[-1] + "_cli"
    print(f"[_resolve_cli] Falling back to: {sys.executable} -m {module}")
    return [sys.executable, "-m", module]


def test_snapgene_ships_sample_files():
    assert len(ALL_DNA) > 100
    assert PUC19.is_file() and GFP.is_file() and GFP_PCR.is_file()


def test_every_shipped_file_matches_biopython_reader():
    warnings.simplefilter("ignore")
    for path in ALL_DNA:
        sg = dnafile.load(path)
        assert str(SeqIO.read(path, "snapgene").seq).upper() == sg.sequence, path.name


def test_every_cds_matches_snapgene_saved_translation():
    checked = 0
    for path in ALL_DNA:
        sg = dnafile.load(path)
        for feat in sg.features:
            saved = seqtools.snapgene_translation(feat) if feat.type == "CDS" else None
            if not saved:
                continue
            mine = seqtools.translate(seqtools.coding_sequence(sg, feat), seqtools.translation_table(feat))
            assert mine == saved, f"{path.name}: {feat.name}"
            checked += 1
    print(f"\n  CDS checked against SnapGene: {checked}")
    assert checked > 500  # SnapGene 8.2.3 ships 862


def test_puc19_cut_sites_match_snapgene_map():
    sg = dnafile.load(PUC19)
    rows = {r["enzyme"]: r["positions"] for r in enzymes.cut_sites(sg, ["EcoRI", "HindIII"])}
    assert rows == {"EcoRI": [396], "HindIII": [447]}


def test_puc19_genbank_export_keeps_ampr(tmp_path):
    sg = dnafile.load(PUC19)
    out = tmp_path / "pUC19.gb"
    result = convert.export(sg, out, "genbank")
    assert result["bytes"] > 5000
    rec = SeqIO.read(out, "genbank")
    assert len(rec.seq) == 2686 and rec.annotations["topology"] == "circular"
    ampr = next(f for f in rec.features if f.qualifiers["label"] == ["AmpR"])
    protein = str(ampr.extract(rec.seq).translate())
    assert protein == seqtools.snapgene_translation(dnafile.find_feature(sg, "AmpR"))
    print(f"\n  GenBank: {out} ({out.stat().st_size:,} bytes)")


@pytest.mark.parametrize("tool", ["clustalo", "muscle"])
def test_alignment_with_snapgene_aligners(tmp_path, tool):
    out = tmp_path / f"gfp_{tool}.fasta"
    result = align.align([GFP, GFP_PCR], tool, out)
    rows = list(SeqIO.parse(out, "fasta"))
    assert [r.description for r in rows] == ["1. GFP", "2. GFP Amplified"]
    assert len({len(r.seq) for r in rows}) == 1 and len(rows[0].seq) == result["length"]
    (pair,) = result["identity"]
    assert pair["identity_pct"] > 90
    print(f"\n  {tool}: {out} identity {pair['identity_pct']}%")


def test_puc19_double_digest_matches_snapgene_map():
    sg = dnafile.load(PUC19)
    cuts = cutting.find_cuts(sg, ["EcoRI", "HindIII"])
    assert [(c.enzyme, c.top, c.kind) for c in cuts] == [
        ("EcoRI", 396, "5' overhang"), ("HindIII", 447, "5' overhang")]
    assert sorted(p["length"] for p in cutting.fragments(sg, cuts)) == [51, 2635]


def test_infusion_gfp_into_puc19():
    """Clone GFP into pUC19 (EcoRI + HindIII) and check the product base by base."""
    vector, gfp = dnafile.load(PUC19), dnafile.load(GFP)
    gene = dnafile.find_feature(gfp, "GFP")
    piece = assemble.take(gfp, gene.start, gene.end, label="GFP")
    opened = infusion.open_vector(vector, ["EcoRI", "HindIII"])
    result = infusion.design(opened, piece, insert_name="GFP")
    product = result["_product"]

    backbone = seqtools.subseq(vector, 448, 396)          # HindIII cut round to the EcoRI cut
    circle = product.sequence + product.sequence          # the backbone crosses the origin
    assert backbone in circle                             # the vector is whole
    assert product.sequence.startswith(vector.sequence[:396])   # numbered like the vector
    gene_bases = seqtools.subseq(gfp, gene.start, gene.end)
    assert product.sequence.count(gene_bases) == 1
    assert product.length == len(backbone) + 4 + 1 + len(gene_bases) + 1
    assert len(EcoRI.search(Seq(product.sequence), linear=False)) == 1   # rebuilt, once each
    assert len(HindIII.search(Seq(product.sequence), linear=False)) == 1
    assert result["primers"][0]["sequence"].startswith("GACGGCCAGTGAATTC")
    assert result["primers"][1]["sequence"].startswith("TGATTACGCCAAGCTT")

    moved = {f.name: f for f in product.features}
    assert seqtools.translate(seqtools.coding_sequence(product, moved["AmpR"])) == \
        seqtools.snapgene_translation(dnafile.find_feature(vector, "AmpR"))
    assert seqtools.translate(seqtools.coding_sequence(product, moved["GFP"])) == \
        seqtools.snapgene_translation(gene)
    print(f"\n  product {product.length} bp, {len(product.features)} features")


def test_infusion_product_reads_back_as_genbank(tmp_path):
    vector, gfp = dnafile.load(PUC19), dnafile.load(GFP)
    gene = dnafile.find_feature(gfp, "GFP")
    piece = assemble.take(gfp, gene.start, gene.end, label="GFP")
    result = infusion.design(infusion.open_vector(vector, ["EcoRI", "HindIII"]), piece, insert_name="GFP")
    out = tmp_path / "pUC19-GFP.gb"
    convert.export(result["_product"], out, "genbank")
    rec = SeqIO.read(out, "genbank")
    assert len(rec.seq) == result["_product"].length
    assert rec.annotations["topology"] == "circular"
    gfp_cds = next(f for f in rec.features if f.qualifiers["label"] == ["GFP"] and f.type == "CDS")
    assert str(gfp_cds.extract(rec.seq).translate()) == seqtools.snapgene_translation(gene)
    forward = next(f for f in rec.features if f.qualifiers["label"] == ["GFP In-Fusion FOR"])
    assert str(forward.extract(rec.seq)) == result["primers"][0]["sequence"]
    reverse = next(f for f in rec.features if f.qualifiers["label"] == ["GFP In-Fusion REV"])
    assert str(reverse.extract(rec.seq)) == result["primers"][1]["sequence"]
    print(f"\n  GenBank: {out} ({out.stat().st_size:,} bytes)")


class TestCLISubprocess:
    CLI_BASE = _resolve_cli("cli-anything-snapgene")

    def _run(self, args, check=True):
        return subprocess.run(self.CLI_BASE + [str(a) for a in args], capture_output=True, text=True, check=check)

    def test_help(self):
        assert "SnapGene" in self._run(["--help"]).stdout

    def test_status_json(self):
        data = json.loads(self._run(["status", "--json"]).stdout)
        assert data["ok"] and data["tools"] == {"clustalo": True, "muscle": True}
        assert data["version"].split(".")[0].isdigit()

    def test_info_json(self):
        data = json.loads(self._run(["info", PUC19, "--json"]).stdout)
        assert (data["length"], data["feature_count"], data["topology"]) == (2686, 10, "circular")

    def test_export_align_search_workflow(self, tmp_path):
        work = tmp_path / "plasmids"
        work.mkdir()
        shutil.copy(GFP, work / "GFP.dna")
        shutil.copy(GFP_PCR, work / "GFP PCR.dna")
        gb = tmp_path / "GFP.gb"
        data = json.loads(self._run(["export", work / "GFP.dna", "-o", gb, "--json"]).stdout)
        assert data["features"] >= 1 and SeqIO.read(gb, "genbank").seq
        aln = tmp_path / "gfp.fasta"
        data = json.loads(self._run(["align", work / "GFP.dna", work / "GFP PCR.dna", "--tool", "muscle",
                                     "-o", aln, "--json"]).stdout)
        assert data["identity"][0]["identity_pct"] > 90 and aln.is_file()
        data = json.loads(self._run(["search", work, "--feature", "GFP", "--json"]).stdout)
        assert len(data["matches"]) == 2
        print(f"\n  GenBank: {gb}\n  Alignment: {aln}")

    def test_infusion_workflow(self, tmp_path):
        product, fragment = tmp_path / "pUC19-GFP.gb", tmp_path / "GFP-fragment.fasta"
        data = json.loads(self._run(["infusion", PUC19, "--enzyme", "EcoRI", "--enzyme", "HindIII",
                                     "--insert", GFP, "--insert-feature", "GFP",
                                     "-o", product, "--fragment-out", fragment, "--json"]).stdout)
        assert data["ok"] and data["opened"]["removed_bp"] == 51
        assert data["primers"][0]["sequence"].startswith("GACGGCCAGTGAATTC")
        assert data["product"]["length"] == 2686 + 717 + 6 - 51
        rec = SeqIO.read(product, "genbank")
        assert len(rec.seq) == data["product"]["length"]
        assert fragment.read_text().startswith(">")
        checks = json.loads(self._run(["gblock", GFP, "--feature", "GFP", "--json"]).stdout)
        assert checks["length"] == 717 and checks["ok"]
        print(f"\n  GenBank: {product}\n  Fragment: {fragment}")

    def test_error_json(self, tmp_path):
        result = self._run(["info", tmp_path / "missing.dna", "--json"], check=False)
        assert result.returncode == 1
        assert json.loads(result.stdout)["code"] == "no_file"


@pytest.mark.gui
def test_open_exported_genbank_in_snapgene(tmp_path):
    out = tmp_path / "pUC19_from_cli.gb"
    convert.export(dnafile.load(PUC19), out, "genbank")
    snapgene_backend.open_in_app(out)
    for _ in range(30):
        if snapgene_backend.is_running():
            break
        time.sleep(1)
    assert snapgene_backend.is_running()
    print(f"\n  Opened in SnapGene: {out}")
