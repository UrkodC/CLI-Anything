from __future__ import annotations

import pytest

from cli_anything.snapgene.core import align
from cli_anything.snapgene.errors import CliError
from cli_anything.snapgene.tests._helpers import write_snapgene


def test_pairwise_identity_skips_double_gaps():
    assert align.pairwise_identity("AC-T-", "ACGT-") == {"matches": 3, "columns": 4, "identity_pct": 75.0}


def test_duplicate_names_get_suffix(tmp_path):
    a = tmp_path / "a.fasta"
    a.write_text(">x\nACGT\n>x\nACGA\n")
    names = [s["name"] for s in align.load_sequences([a, write_snapgene(tmp_path / "x.dna", "ACGG")])]
    assert names == ["x", "x_2", "x_3"]


def test_mixed_types(tmp_path):
    dna = write_snapgene(tmp_path / "a.dna", "ACGT")
    prot = write_snapgene(tmp_path / "b.prot", "MKVLE", kind="protein")
    with pytest.raises(CliError) as exc:
        align.align([dna, prot])
    assert exc.value.code == "mixed_types"


def test_too_few_sequences(tmp_path):
    with pytest.raises(CliError) as exc:
        align.align([write_snapgene(tmp_path / "a.dna", "ACGT")])
    assert exc.value.code == "too_few_sequences"


def test_unsupported_file(tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text("ACGT")
    with pytest.raises(CliError) as exc:
        align.load_sequences([other])
    assert exc.value.code == "unsupported_file"
