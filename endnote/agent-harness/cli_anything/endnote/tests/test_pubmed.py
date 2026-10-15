from __future__ import annotations

import http.client
import urllib.error
from pathlib import Path

import pytest

from cli_anything.endnote.core import pubmed
from cli_anything.endnote.errors import CliError

FIXTURE = Path(__file__).parent / "fixtures" / "pubmed_sample.xml"


def test_parse_normal_article():
    soda = pubmed.parse_records(FIXTURE.read_bytes())[0]
    assert soda == {
        "pmid": "17625570",
        "authors": ["Soda, M.", "Choi, Y. L."],
        "title": "Identification of the transforming EML4-ALK fusion gene in non-small-cell lung cancer",
        "journal": "Nature",
        "journal_full": "Nature",
        "year": "2007",
        "volume": "448",
        "issue": "7153",
        "pages": "561-6",
        "abstract": "Improvement in the clinical outcome of lung cancer is likely to be achieved by "
                    "identification of the molecular events that underlie its pathogenesis.",
        "doi": "10.1038/nature05945",
        "pmcid": "",
        "keywords": ["Carcinoma, Non-Small-Cell Lung", "Oncogene Proteins, Fusion"],
    }


def test_parse_collective_author_structured_abstract_medline_date():
    guthe = pubmed.parse_records(FIXTURE.read_bytes())[1]
    assert guthe["authors"] == ["Güthe, S.", "Example Consortium,"]
    assert guthe["year"] == "2004"
    assert guthe["journal"] == "J Mol Biol"
    assert guthe["journal_full"] == "Journal of molecular biology"
    assert guthe["abstract"] == "BACKGROUND: First part.\rRESULTS: Second part with α-helix."
    assert guthe["doi"] == ""
    assert guthe["pmcid"] == "PMC1234567"
    assert guthe["keywords"] == []


def test_book_records_are_ignored():
    assert [r["pmid"] for r in pubmed.parse_records(FIXTURE.read_bytes())] == ["17625570", "15033360"]


def test_fetch_reports_missing_pmids_and_keeps_input_order():
    calls = []

    def fake(batch):
        calls.append(list(batch))
        return FIXTURE.read_bytes()

    records, missing = pubmed.fetch(["15033360", "99999999", "17625570"], efetch=fake)
    assert [r["pmid"] for r in records] == ["15033360", "17625570"]
    assert missing == ["99999999"]
    assert calls == [["15033360", "99999999", "17625570"]]


def test_fetch_splits_into_batches_of_200(monkeypatch):
    monkeypatch.setattr(pubmed.time, "sleep", lambda s: None)
    sizes = []

    def fake(batch):
        sizes.append(len(batch))
        return b"<PubmedArticleSet/>"

    pmids = [str(i) for i in range(1, 451)]
    records, missing = pubmed.fetch(pmids, efetch=fake)
    assert sizes == [200, 200, 50]
    assert records == [] and missing == pmids


def test_fetch_with_no_pmids_makes_no_request():
    assert pubmed.fetch([], efetch=lambda b: pytest.fail("no request expected")) == ([], [])


def test_unparseable_answer_raises_pubmed_error():
    with pytest.raises(CliError) as exc:
        pubmed.fetch(["1"], efetch=lambda b: b"<html>busy</html")
    assert exc.value.code == "pubmed_error"


def test_network_error_raises_pubmed_error(monkeypatch):
    monkeypatch.setattr(pubmed.time, "sleep", lambda s: None)

    def offline(*args, **kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", offline)
    with pytest.raises(CliError) as exc:
        pubmed.fetch(["17625570"])
    assert exc.value.code == "pubmed_error"
    assert exc.value.message == "PubMed did not answer. Check the network and run the command again."


@pytest.mark.parametrize("failure", [ConnectionResetError("reset"), http.client.IncompleteRead(b"")])
def test_dropped_connection_while_reading_raises_pubmed_error(monkeypatch, failure):
    monkeypatch.setattr(pubmed.time, "sleep", lambda s: None)

    class Broken:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            raise failure

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", lambda *a, **k: Broken())
    with pytest.raises(CliError) as exc:
        pubmed.fetch(["17625570"])
    assert exc.value.code == "pubmed_error"
    assert exc.value.message == "PubMed did not answer. Check the network and run the command again."


def test_connection_reset_on_open_raises_pubmed_error(monkeypatch):
    monkeypatch.setattr(pubmed.time, "sleep", lambda s: None)

    def reset(*args, **kwargs):
        raise ConnectionResetError("reset")

    monkeypatch.setattr(pubmed.urllib.request, "urlopen", reset)
    with pytest.raises(CliError) as exc:
        pubmed.fetch(["17625570"])
    assert exc.value.code == "pubmed_error"


@pytest.mark.network
def test_real_pubmed_soda_2007():
    records, missing = pubmed.fetch(["17625570"])
    assert missing == []
    assert records[0]["title"].startswith("Identification of the transforming EML4-ALK fusion gene")
    assert records[0]["doi"] == "10.1038/nature05945"
    assert records[0]["authors"][0] == "Soda, M."
