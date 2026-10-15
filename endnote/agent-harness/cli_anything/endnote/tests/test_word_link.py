from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

import pytest

from cli_anything.endnote.core import word_link
from cli_anything.endnote.core.word_link import MARKER_RE, merge_citations, parse_pmids
from cli_anything.endnote.errors import CliError
from cli_anything.endnote.tests._helpers import W, has, make_docx, run_for_text, visible_paragraphs


@pytest.mark.parametrize(
    "text, expected",
    [
        ("[PMID 17625570]", ["17625570"]),
        ("[PMID: 17625570]", ["17625570"]),
        ("[PMIDs 17625570; 41528114]", ["17625570", "41528114"]),
        ("[pmid 17625570, 41528114]", ["17625570", "41528114"]),
        ("[ PMID 017625570 ]", ["17625570"]),
    ],
)
def test_marker_forms_match(text, expected):
    m = MARKER_RE.search(text)
    assert m is not None and m.group(0) == text
    assert parse_pmids(m.group(1)) == expected


@pytest.mark.parametrize("text", ["[PMID]", "PMID 17625570", "[PMID abc]", "(PMID 17625570)", "[DOI 10.1/x]"])
def test_near_misses_do_not_match(text):
    assert MARKER_RE.search(text) is None


def test_merge_single_citation_is_unchanged():
    assert merge_citations(["{Soda, 2007 #18}"]) == "{Soda, 2007 #18}"


def test_merge_keeps_order_and_uses_one_pair_of_braces():
    assert merge_citations(["{Soda, 2007 #18}", "{Siegel, 2026 #2}"]) == "{Soda, 2007 #18; Siegel, 2026 #2}"


def _r(text: str, rpr: str = "") -> str:
    return f'<w:r>{rpr}<w:t xml:space="preserve">{text}</w:t></w:r>'


YELLOW = '<w:rPr><w:highlight w:val="yellow"/></w:rPr>'
BOLD = "<w:rPr><w:b/></w:rPr>"
ITALIC = "<w:rPr><w:i/></w:rPr>"
CITES = {"17625570": "{Soda, 2007 #18}", "41528114": "{Siegel, 2026 #2}"}


def _link(tmp_path, body, cites=CITES):
    src = make_docx(tmp_path / "in.docx", body)
    dst = tmp_path / "out.docx"
    report = word_link.link_docx(src, dst, cites)
    return src, dst, report


def test_marker_in_one_run(tmp_path):
    body = "<w:p>" + _r("Deaths ") + _r("[PMID 41528114]", YELLOW) + _r(".") + "</w:p>"
    _, dst, report = _link(tmp_path, body)
    assert visible_paragraphs(dst) == ["Deaths {Siegel, 2026 #2}."]
    assert not has(run_for_text(dst, "{Siegel, 2026 #2}"), "highlight")
    assert (report.markers_found, report.markers_linked, report.markers_left) == (1, 1, [])


def test_marker_split_across_three_runs_keeps_outer_formatting(tmp_path):
    body = "<w:p>" + _r("Bold [PM", BOLD) + _r("ID 1762", ITALIC) + _r("5570] tail") + "</w:p>"
    _, dst, _ = _link(tmp_path, body)
    assert visible_paragraphs(dst) == ["Bold {Soda, 2007 #18} tail"]
    assert has(run_for_text(dst, "Bold "), "b")
    assert not has(run_for_text(dst, " tail"), "b")
    # the citation takes the formatting of the first marker character
    assert has(run_for_text(dst, "{Soda, 2007 #18}"), "b")


def test_two_markers_in_one_paragraph(tmp_path):
    body = "<w:p>" + _r("A [PMID 17625570] and B [PMID 41528114; 17625570].") + "</w:p>"
    _, dst, report = _link(tmp_path, body)
    assert visible_paragraphs(dst) == ["A {Soda, 2007 #18} and B {Siegel, 2026 #2; Soda, 2007 #18}."]
    assert report.markers_linked == 2


def test_marker_inside_tracked_insertion_and_deleted_text_ignored(tmp_path):
    body = (
        "<w:p>" + _r("Deaths ")
        + '<w:del w:id="1" w:author="Claude" w:date="2026-10-15T00:00:00Z">'
        + '<w:r><w:delText xml:space="preserve">[PMID 17625570]</w:delText></w:r></w:del>'
        + '<w:ins w:id="2" w:author="Claude" w:date="2026-10-15T00:00:00Z">'
        + _r("[PMID 41528114]", YELLOW) + "</w:ins>"
        + _r(".") + "</w:p>"
    )
    _, dst, report = _link(tmp_path, body)
    assert visible_paragraphs(dst) == ["Deaths {Siegel, 2026 #2}."]
    assert report.markers_found == 1
    assert report.has_tracked_changes is True
    assert run_for_text(dst, "{Siegel, 2026 #2}").getparent().tag == f"{{{W}}}ins"


def test_unresolved_marker_is_left_unchanged_and_yellow(tmp_path):
    body = "<w:p>" + _r("X ") + _r("[PMID 17625570; 99999999]", YELLOW) + "</w:p>"
    _, dst, report = _link(tmp_path, body)
    assert visible_paragraphs(dst) == ["X [PMID 17625570; 99999999]"]
    assert has(run_for_text(dst, "[PMID 17625570; 99999999]"), "highlight")
    assert report.markers_left == ["[PMID 17625570; 99999999]"]
    assert report.markers_linked == 0


def test_marker_split_by_proofing_and_bookmark_elements(tmp_path):
    body = (
        "<w:p>" + _r("See [PMID ")
        + '<w:proofErr w:type="spellStart"/><w:bookmarkStart w:id="0" w:name="_x"/>'
        + _r("17625570")
        + '<w:bookmarkEnd w:id="0"/><w:proofErr w:type="spellEnd"/>'
        + _r("].") + "</w:p>"
    )
    _, dst, _ = _link(tmp_path, body)
    assert visible_paragraphs(dst) == ["See {Soda, 2007 #18}."]


def test_marker_inside_hyperlink_and_table_cell(tmp_path):
    body = (
        "<w:tbl><w:tr><w:tc><w:p>" + _r("Cell ")
        + '<w:hyperlink w:anchor="x">' + _r("[PMID 17625570]") + "</w:hyperlink>"
        + "</w:p></w:tc></w:tr></w:tbl>"
    )
    _, dst, report = _link(tmp_path, body)
    assert visible_paragraphs(dst) == ["Cell {Soda, 2007 #18}"]
    assert report.markers_linked == 1


def test_marker_split_into_a_hyperlink_leaves_no_empty_wrapper(tmp_path):
    body = (
        "<w:p>" + _r("See [PMID 1762")
        + '<w:hyperlink w:anchor="x">' + _r("5570]") + "</w:hyperlink>"
        + _r(" end") + "</w:p>"
    )
    _, dst, _ = _link(tmp_path, body)
    assert visible_paragraphs(dst) == ["See {Soda, 2007 #18} end"]
    with zipfile.ZipFile(dst) as zf:
        assert b"<w:hyperlink" not in zf.read("word/document.xml")


def test_text_box_paragraph_is_processed_once(tmp_path):
    body = (
        "<w:p>" + _r("Outer [PMID 17625570] ")
        + "<w:r><w:txbxContent><w:p>" + _r("Inner [PMID 41528114]") + "</w:p></w:txbxContent></w:r>"
        + "</w:p>"
    )
    _, dst, report = _link(tmp_path, body)
    assert report.markers_found == 2
    assert visible_paragraphs(dst) == ["Outer {Soda, 2007 #18} ", "Inner {Siegel, 2026 #2}"]


def test_output_keeps_every_part_and_no_new_namespace_prefixes(tmp_path):
    body = "<w:p>" + _r("A [PM", BOLD) + _r("ID 17625570]") + "</w:p>"
    src, dst, _ = _link(tmp_path, body)
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(dst) as b:
        assert a.namelist() == b.namelist()
        assert b.testzip() is None
        xml = b.read("word/document.xml")
    assert b"ns0:" not in xml
    assert xml.startswith(b"<?xml")


def test_input_file_is_not_modified(tmp_path):
    src = make_docx(tmp_path / "in.docx", "<w:p>" + _r("[PMID 17625570]") + "</w:p>")
    before = src.read_bytes()
    word_link.link_docx(src, tmp_path / "out.docx", CITES)
    assert src.read_bytes() == before


def test_scan_lists_markers_texts_and_unique_pmids_in_order(tmp_path):
    body = (
        "<w:p>" + _r("[PMID 41528114] x [PMID 17625570; 41528114]") + "</w:p>"
        + "<w:p>" + _r("[pmid 22222222]") + "</w:p>"
    )
    scan = word_link.scan_docx(make_docx(tmp_path / "in.docx", body))
    assert scan.markers == [["41528114"], ["17625570", "41528114"], ["22222222"]]
    assert scan.texts == ["[PMID 41528114]", "[PMID 17625570; 41528114]", "[pmid 22222222]"]
    assert scan.pmids == ["41528114", "17625570", "22222222"]
    assert scan.has_tracked_changes is False


def test_not_a_docx_raises_bad_docx(tmp_path):
    bad = tmp_path / "notes.docx"
    bad.write_text("hello")
    with pytest.raises(CliError) as exc:
        word_link.scan_docx(bad)
    assert exc.value.code == "bad_docx"


REAL_DOCX = os.environ.get("ENDNOTE_REAL_DOCX", "")


@pytest.mark.skipif(not REAL_DOCX or not Path(REAL_DOCX).exists(), reason="set ENDNOTE_REAL_DOCX to a real .docx with markers")
def test_real_document(tmp_path):
    src = tmp_path / "real.docx"
    shutil.copy(REAL_DOCX, src)
    scan = word_link.scan_docx(src)
    cites = {p: f"{{Test, 2000 #{p}}}" for p in scan.pmids}
    dst = tmp_path / "real_LINKED.docx"
    report = word_link.link_docx(src, dst, cites)
    assert report.markers_found == len(scan.markers) > 0
    assert report.markers_linked == report.markers_found and report.markers_left == []
    expected = [
        word_link.MARKER_RE.sub(
            lambda m: word_link.merge_citations([cites[p] for p in word_link.parse_pmids(m.group(1))]), text
        )
        for text in visible_paragraphs(src)
    ]
    assert visible_paragraphs(dst) == expected
    with zipfile.ZipFile(dst) as zf:
        assert zf.testzip() is None
