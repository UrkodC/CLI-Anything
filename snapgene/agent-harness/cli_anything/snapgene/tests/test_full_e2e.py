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

from cli_anything.snapgene.core import (
    align, assemble, convert, cutting, dnafile, dnawrite, enzymes, gateway, history, infusion, seqtools,
)
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.tests._helpers import ATTB1, ATTB2, entry_clone
from cli_anything.snapgene.utils import snapgene_backend

APP = snapgene_backend.find_app()
RESOURCES = APP / "Contents/Resources"
SAMPLES = RESOURCES / "sampleData/Sample project"
PUC19 = SAMPLES / "1. Annotate and Visualize/2. Annotate/2. pUC19 Annotated.dna"
GFP = SAMPLES / "2. Experimental Design/1. PCR/1. GFP.dna"
GFP_PCR = SAMPLES / "2. Experimental Design/1. PCR/2. GFP Amplified.dna"
ALL_DNA = sorted(RESOURCES.rglob("*.dna"))
GATEWAY_DESTS = sorted((RESOURCES / "Plasmids/Gateway Destination Vectors").glob("*.dna"))
PDEST14 = RESOURCES / "Plasmids/Gateway Destination Vectors/pDEST14.dna"


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


def _gfp_entry_clone(tmp_path) -> tuple:
    """An entry clone carrying SnapGene's GFP between the real attL1 and attL2."""
    gfp = dnafile.load(GFP)
    gene = dnafile.find_feature(gfp, "GFP")
    bases = seqtools.subseq(gfp, gene.start, gene.end)
    entry = dnafile.load(entry_clone(tmp_path / "pENTR-GFP.dna", insert=bases, name="GFP"))
    return entry, gene, bases


def test_gateway_lr_runs_on_every_bundled_destination_vector(tmp_path):
    """The 77 Invitrogen destination vectors SnapGene ships, through one LR each."""
    entry, _, insert = _gfp_entry_clone(tmp_path)
    done, refused = 0, []
    for path in GATEWAY_DESTS:
        dest = dnafile.load(path)
        if len(gateway.find_att(dest, "R")) != 2:
            continue
        try:
            result = gateway.lr(entry, dest, tmp_path / "product.gb")
        except CliError as exc:
            assert exc.code in {"att_mismatch", "att_direction"}, (path.name, exc.code)
            refused.append(path.name)
            continue
        sites = {s["name"]: s["sequence"] for s in result["att_sites"]}
        assert sites == {"attB1": ATTB1, "attB2": ATTB2}, path.name
        product = result["_product"]
        assert product.length == result["destination"]["backbone_bp"] + 50 + len(insert), path.name
        assert not [f for f in product.features if "ccdb" in f.name.lower()], path.name
        done += 1
    assert done >= 70 and refused
    print(f"\n  {done} destination vectors recombined; {len(refused)} refused for att numbers "
          f"that do not pair ({', '.join(refused)})")


def test_gateway_product_is_a_clean_expression_clone(tmp_path):
    """pDEST14 x an entry clone: backbone whole, cassette gone, proteins unchanged."""
    entry, gene, insert = _gfp_entry_clone(tmp_path)
    dest = dnafile.load(PDEST14)
    result = gateway.lr(entry, dest, tmp_path / "pDEST14-GFP.gb")
    product = result["_product"]

    backbone = seqtools.subseq(dest, 1913, 66)            # attR2 cut round to attR1
    circle = product.sequence + product.sequence          # the backbone crosses the origin
    assert backbone in circle                             # the vector is whole
    assert product.length == len(backbone) + 50 + len(insert)     # and used once
    assert product.sequence.startswith(dest.sequence[:66])    # numbered like the vector
    assert set(result["destination"]["cassette_features"]) >= {"ccdB", "CmR"}
    assert circle.count(ATTB1) == 2
    assert circle.count(seqtools.revcomp(ATTB2)) == 2
    assert circle.count(insert) == 2

    moved = {f.name: f for f in product.features}
    assert "T7 promoter" in moved and "ccdB" not in moved
    assert seqtools.translate(seqtools.coding_sequence(product, moved["AmpR"])) == \
        seqtools.snapgene_translation(dnafile.find_feature(dest, "AmpR"))
    assert seqtools.translate(seqtools.coding_sequence(product, moved["GFP"])) == \
        seqtools.snapgene_translation(gene)
    print(f"\n  product {product.length} bp, {len(product.features)} features")


def test_gateway_product_reads_back_as_genbank(tmp_path):
    entry, gene, _ = _gfp_entry_clone(tmp_path)
    result = gateway.lr(entry, dnafile.load(PDEST14), Path("pDEST14-GFP"))
    out = tmp_path / "pDEST14-GFP.gb"
    convert.export(result["_product"], out, "genbank")
    rec = SeqIO.read(out, "genbank")
    assert len(rec.seq) == result["_product"].length
    assert rec.annotations["topology"] == "circular"
    b1 = next(f for f in rec.features if f.qualifiers["label"] == ["attB1"])
    assert str(b1.extract(rec.seq)) == ATTB1
    b2 = next(f for f in rec.features if f.qualifiers["label"] == ["attB2"])
    assert str(b2.extract(rec.seq)) == ATTB2          # extract reverse complements a minus feature
    gfp_cds = next(f for f in rec.features if f.qualifiers["label"] == ["GFP"] and f.type == "CDS")
    assert str(gfp_cds.extract(rec.seq).translate()) == seqtools.snapgene_translation(gene)
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

    def test_gateway_workflow(self, tmp_path):
        entry, _, insert = _gfp_entry_clone(tmp_path)
        product = tmp_path / "pDEST14-GFP.gb"
        data = json.loads(self._run(["gateway", PDEST14, "--entry", entry.path,
                                     "-o", product, "--json"]).stdout)
        assert data["ok"]
        assert [s["sequence"] for s in data["att_sites"]] == [ATTB1, ATTB2]
        assert data["destination"]["cassette_bp"] == 1846
        assert data["product"]["length"] == 6422 - 1846 + 50 + len(insert)
        rec = SeqIO.read(product, "genbank")
        assert len(rec.seq) == data["product"]["length"]
        assert not [f for f in rec.features if f.qualifiers.get("label") == ["ccdB"]]
        print(f"\n  GenBank: {product}")

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


# --- writing .dna files back out -------------------------------------------

def test_every_shipped_file_survives_a_write_and_a_reread(tmp_path):
    """Write all 200+ files SnapGene ships, read them back, compare."""
    out = tmp_path / "again.dna"
    checked = 0
    for path in ALL_DNA:
        sg = dnafile.load(path)
        out.unlink(missing_ok=True)
        dnawrite.write(sg, out)
        again = dnafile.load(out)
        assert again.sequence == sg.sequence, path
        assert again.circular == sg.circular, path
        assert [f.name for f in again.features] == [f.name for f in sg.features], path
        assert [(f.start, f.end, f.strand) for f in again.features] == \
               [(f.start, f.end, f.strand) for f in sg.features], path
        assert [p.sequence for p in again.primers] == [p.sequence for p in sg.primers], path
        checked += 1
    assert checked == len(ALL_DNA)
    print(f"\n  {checked} files written and read back unchanged")


def test_biopython_reads_every_file_we_write(tmp_path):
    out = tmp_path / "bio.dna"
    for path in ALL_DNA:
        sg = dnafile.load(path)
        out.unlink(missing_ok=True)
        dnawrite.write(sg, out)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            record = SeqIO.read(out, "snapgene")
        assert str(record.seq).upper() == sg.sequence, path


def test_a_shipped_history_is_carried_into_a_product(tmp_path):
    """GFP Amplified was made by PCR in SnapGene; its product keeps that step."""
    amplified = dnafile.load(GFP_PCR)
    assert history.steps(amplified.history)[0]["operation"] == "amplifyFragment"
    insert = assemble.take(amplified, 1, amplified.length, label="GFP")
    opened = infusion.open_vector(dnafile.load(PUC19), ["EcoRI", "HindIII"])
    product = infusion.design(opened, insert, insert_name="GFP")["_product"]
    names = [(s["operation"], s["depth"]) for s in history.steps(product.history)]
    assert names[:3] == [("inFusionCloning", 0), ("invalid", 1), ("amplifyFragment", 1)]
    assert ("amplifyFragment", 2) in names      # the PCR SnapGene itself recorded
    out = tmp_path / "pUC19-GFP.dna"
    dnawrite.write(product, out)
    assert len(history.steps(dnafile.load(out).history)) == len(names)


def test_gateway_product_reads_back_as_a_snapgene_file(tmp_path):
    entry, gene, _ = _gfp_entry_clone(tmp_path)
    result = gateway.lr(entry, dnafile.load(PDEST14), Path("pDEST14-GFP"))
    out = tmp_path / "pDEST14-GFP.dna"
    report = dnawrite.write(result["_product"], out)
    again = dnafile.load(out)
    assert again.length == result["_product"].length and again.circular
    assert seqtools.subseq(again, *result["att_sites"][0]["range"]) == ATTB1
    gfp = dnafile.find_feature(again, "GFP")
    # The protein still matches the one SnapGene saved in its own GFP file.
    assert seqtools.translate(seqtools.subseq(again, gfp.start, gfp.end)) == \
           seqtools.snapgene_translation(gene)
    assert report["history_steps"] == 3
    print(f"\n  SnapGene file: {out} ({report['bytes']:,} bytes, {report['history_steps']} history steps)")


@pytest.mark.gui
def test_snapgene_opens_a_product_we_wrote(tmp_path):
    """The real check: SnapGene 8 quits on a malformed history, with no message."""
    entry, _, _ = _gfp_entry_clone(tmp_path)
    product = gateway.lr(entry, dnafile.load(PDEST14), Path("pDEST14-GFP"))["_product"]
    out = tmp_path / "pDEST14-GFP.dna"
    dnawrite.write(product, out)

    snapgene_backend.open_in_app(PUC19)          # a known-good file, so the app is up
    for _ in range(30):
        if snapgene_backend.is_running():
            break
        time.sleep(1)
    assert snapgene_backend.is_running(), "SnapGene did not start"

    snapgene_backend.open_in_app(out)
    time.sleep(8)
    assert snapgene_backend.is_running(), f"SnapGene quit on {out.name}"
    print(f"\n  SnapGene opened {out.name} and stayed up")
