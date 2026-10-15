from __future__ import annotations

from pathlib import Path

from defusedxml import ElementTree as DET

from cli_anything.endnote.core import enxml, pubmed

FIXTURE = Path(__file__).parent / "fixtures" / "pubmed_sample.xml"

# Child order of <record> in a real EndNote 2025 export (record #2 of DoD EML4ALK.enl),
# limited to the elements this CLI writes.
EXPORT_ORDER = [
    "ref-type", "contributors", "titles", "periodical", "pages", "volume", "number",
    "keywords", "dates", "accession-num", "abstract", "urls", "custom2",
    "electronic-resource-num", "remote-database-name", "remote-database-provider",
]


def _records():
    return pubmed.parse_records(FIXTURE.read_bytes())


def _parse(xml: str):
    return DET.fromstring(xml.encode("utf-8"))


def _value(el, path):
    node = el.find(path)
    return "".join(node.itertext()) if node is not None else None


def test_record_follows_endnote_export_layout():
    rec = _parse(enxml.to_endnote_xml(_records()[:1])).find("records/record")
    assert [child.tag for child in rec] == [t for t in EXPORT_ORDER if t != "custom2"]
    assert rec.find("ref-type").get("name") == "Journal Article"
    assert rec.find("ref-type").text == "17"
    assert ["".join(a.itertext()) for a in rec.findall("contributors/authors/author")] == ["Soda, M.", "Choi, Y. L."]
    assert _value(rec, "titles/title").startswith("Identification of the transforming EML4-ALK")
    assert _value(rec, "titles/secondary-title") == "Nature"
    assert _value(rec, "periodical/full-title") == "Nature"
    assert _value(rec, "dates/year") == "2007"
    assert _value(rec, "accession-num") == "17625570"
    assert _value(rec, "electronic-resource-num") == "10.1038/nature05945"
    assert _value(rec, "urls/related-urls/url") == "https://www.ncbi.nlm.nih.gov/pubmed/17625570"
    assert _value(rec, "remote-database-name") == "Medline"
    assert _value(rec, "remote-database-provider") == "NLM"
    assert rec.find("accession-num/style").attrib == {"face": "normal", "font": "default", "size": "100%"}


def test_non_ascii_and_line_breaks_survive():
    xml = enxml.to_endnote_xml(_records()[1:2])
    assert "&#xD;" in xml
    rec = _parse(xml).find("records/record")
    assert "".join(rec.find("contributors/authors/author").itertext()) == "Güthe, S."
    assert _value(rec, "abstract") == "BACKGROUND: First part.\rRESULTS: Second part with α-helix."
    assert _value(rec, "custom2") == "PMC1234567"
    assert rec.find("electronic-resource-num") is None


def test_special_characters_are_escaped_and_control_characters_dropped():
    xml = enxml.to_endnote_xml([{"pmid": "1", "title": 'A <b> & "C"\x0b', "journal": "J"}])
    rec = _parse(xml).find("records/record")
    assert _value(rec, "titles/title") == 'A <b> & "C"'
    assert rec.find("contributors") is None
    assert rec.find("keywords") is None
    assert rec.find("dates") is None


def test_two_records_in_one_document():
    assert len(_parse(enxml.to_endnote_xml(_records())).findall("records/record")) == 2
