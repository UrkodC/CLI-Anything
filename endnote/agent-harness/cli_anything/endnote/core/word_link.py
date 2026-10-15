"""Find [PMID ...] markers in a .docx and replace them with EndNote temporary citations."""
from __future__ import annotations

import copy
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from cli_anything.endnote.errors import CliError

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
DOCUMENT_PART = "word/document.xml"

MARKER_RE = re.compile(
    r"\[\s*PMIDs?\s*:?\s*(\d{1,9}(?:\s*[;,]\s*\d{1,9})*)\s*\]",
    re.IGNORECASE,
)

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)


def _w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


_HIDDEN = {_w("del"), _w("moveFrom")}
_WRAPPERS = {_w("ins"), _w("hyperlink"), _w("smartTag"), _w("moveTo")}
_TRACKED = (_w("ins"), _w("del"), _w("moveFrom"), _w("moveTo"))


def parse_pmids(group: str) -> list[str]:
    """'29079636; 40055571' -> ['29079636', '40055571']. Leading zeros are dropped."""
    return [str(int(x)) for x in re.split(r"\s*[;,]\s*", group.strip())]


def merge_citations(citations: list[str]) -> str:
    """['{Soda, 2007 #18}', '{Siegel, 2026 #2}'] -> '{Soda, 2007 #18; Siegel, 2026 #2}'."""
    inner = [c.strip().removeprefix("{").removesuffix("}").strip() for c in citations]
    return "{" + "; ".join(inner) + "}"


@dataclass
class ScanResult:
    markers: list[list[str]]
    texts: list[str]
    has_tracked_changes: bool

    @property
    def pmids(self) -> list[str]:
        seen: dict[str, None] = {}
        for marker in self.markers:
            for pmid in marker:
                seen.setdefault(pmid, None)
        return list(seen)


@dataclass
class LinkReport:
    markers_found: int = 0
    markers_linked: int = 0
    markers_left: list[str] = field(default_factory=list)
    has_tracked_changes: bool = False


# ── reading ────────────────────────────────────────────────────────────────

def _read_document(path: Path):
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read(DOCUMENT_PART)
    except (zipfile.BadZipFile, KeyError, FileNotFoundError, IsADirectoryError) as exc:
        raise CliError("bad_docx", f"{path} is not a Word .docx file.") from exc
    try:
        return etree.fromstring(xml, _PARSER)
    except etree.XMLSyntaxError as exc:
        raise CliError("bad_docx", f"{path} is not a Word .docx file.") from exc


def _has_tracked_changes(root) -> bool:
    return any(root.find(f".//{tag}") is not None for tag in _TRACKED)


def _paragraph_texts(p) -> list:
    """Visible w:t nodes of paragraph p, in order.

    Skips text in deletions, and text that belongs to a nested paragraph
    (text boxes have their own paragraphs, processed on their own).
    """
    out = []
    for t in p.iter(_w("t")):
        anc = t.getparent()
        hidden = False
        while anc is not None and anc.tag != _w("p"):
            if anc.tag in _HIDDEN:
                hidden = True
            anc = anc.getparent()
        if anc is p and not hidden:
            out.append(t)
    return out


def _spans(ts) -> list[tuple[int, int]]:
    pos, spans = 0, []
    for t in ts:
        n = len(t.text or "")
        spans.append((pos, pos + n))
        pos += n
    return spans


# ── run surgery ────────────────────────────────────────────────────────────

def _set_text(t, text: str) -> None:
    t.text = text
    t.set(XML_SPACE, "preserve")


def _has_content(run) -> bool:
    return any(child.tag != _w("rPr") for child in run)


def _empty_copy(run):
    """A copy of `run` with the same properties and no content."""
    new = copy.deepcopy(run)
    for child in list(new):
        if child.tag != _w("rPr"):
            new.remove(child)
    return new


def _split_after(t, offset: int) -> None:
    """Move t.text[offset:] and every run child after t into a new run right after t's run."""
    run = t.getparent()
    text = t.text or ""
    new_run = _empty_copy(run)
    if text[offset:]:
        new_t = copy.deepcopy(t)
        _set_text(new_t, text[offset:])
        new_run.append(new_t)
    for sib in list(t.itersiblings()):
        new_run.append(sib)
    _set_text(t, text[:offset])
    if _has_content(new_run):
        run.addnext(new_run)


def _split_before(t) -> None:
    """Move every run child before t (except rPr) into a new run right before t's run."""
    run = t.getparent()
    before = [c for c in t.itersiblings(preceding=True) if c.tag != _w("rPr")]
    if not before:
        return
    new_run = _empty_copy(run)
    for child in reversed(before):
        new_run.append(child)
    run.addprevious(new_run)


def _cut_at(p, pos: int) -> None:
    """Make sure a w:t boundary exists at visible-text position `pos`."""
    ts = _paragraph_texts(p)
    for t, (start, end) in zip(ts, _spans(ts)):
        if start < pos < end:
            _split_after(t, pos - start)
            return


def _remove_run_if_empty(run) -> None:
    if _has_content(run):
        return
    parent = run.getparent()
    parent.remove(run)
    if parent.tag in _WRAPPERS and len(parent) == 0:
        parent.getparent().remove(parent)


def _replace_span(p, start: int, end: int, new_text: str) -> None:
    """Replace visible text [start, end) of paragraph p with new_text, without highlight."""
    _cut_at(p, end)
    _cut_at(p, start)
    ts = _paragraph_texts(p)
    covered = [t for t, (s, e) in zip(ts, _spans(ts)) if start <= s and e <= end and e > s]
    first, rest = covered[0], covered[1:]
    _split_before(first)
    _split_after(first, len(first.text or ""))
    _set_text(first, new_text)
    rpr = first.getparent().find(_w("rPr"))
    if rpr is not None:
        for highlight in rpr.findall(_w("highlight")):
            rpr.remove(highlight)
    for t in rest:
        run = t.getparent()
        run.remove(t)
        _remove_run_if_empty(run)


def _link_paragraph(p, citations: dict[str, str], report: LinkReport) -> None:
    text = "".join(t.text or "" for t in _paragraph_texts(p))
    left: list[str] = []
    for m in reversed(list(MARKER_RE.finditer(text))):
        report.markers_found += 1
        pmids = parse_pmids(m.group(1))
        if all(pmid in citations for pmid in pmids):
            _replace_span(p, m.start(), m.end(), merge_citations([citations[pmid] for pmid in pmids]))
            report.markers_linked += 1
        else:
            left.append(m.group(0))
    report.markers_left.extend(reversed(left))


# ── public API ─────────────────────────────────────────────────────────────

def scan_docx(path: Path) -> ScanResult:
    root = _read_document(Path(path))
    markers: list[list[str]] = []
    texts: list[str] = []
    for p in root.iter(_w("p")):
        text = "".join(t.text or "" for t in _paragraph_texts(p))
        for m in MARKER_RE.finditer(text):
            markers.append(parse_pmids(m.group(1)))
            texts.append(m.group(0))
    return ScanResult(markers=markers, texts=texts, has_tracked_changes=_has_tracked_changes(root))


def link_docx(src: Path, dst: Path, citations: dict[str, str]) -> LinkReport:
    src, dst = Path(src), Path(dst)
    if dst.resolve() == src.resolve() or (dst.exists() and os.path.samefile(src, dst)):
        raise CliError("output_is_input", "The output file must be different from the input file.")
    root = _read_document(src)
    report = LinkReport(has_tracked_changes=_has_tracked_changes(root))
    for p in list(root.iter(_w("p"))):
        _link_paragraph(p, citations, report)
    xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    _write_docx(src, dst, xml)
    return report


def _write_docx(src: Path, dst: Path, document_xml: bytes) -> None:
    """Copy every part of src into dst, replacing word/document.xml. Writes through a temp file."""
    tmp = dst.with_name(dst.name + ".tmp")
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for info in zin.infolist():
            data = document_xml if info.filename == DOCUMENT_PART else zin.read(info.filename)
            zout.writestr(info, data)
    tmp.replace(dst)
