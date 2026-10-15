"""Small .docx builders and readers for tests."""
from __future__ import annotations

import zipfile
from pathlib import Path

from lxml import etree

from cli_anything.endnote.core import word_link

W = word_link.W_NS

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)


def make_docx(path: Path, body_xml: str) -> Path:
    """Write a minimal valid .docx whose body is `body_xml` (w: prefix is declared)."""
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{body_xml}</w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("word/document.xml", doc)
    return Path(path)


def document_root(path: Path):
    with zipfile.ZipFile(path) as zf:
        return etree.fromstring(zf.read("word/document.xml"))


def visible_paragraphs(path: Path) -> list[str]:
    """Visible text of every paragraph, in document order (deleted text excluded)."""
    root = document_root(path)
    return ["".join(t.text or "" for t in word_link._paragraph_texts(p)) for p in root.iter(f"{{{W}}}p")]


def run_for_text(path: Path, text: str):
    """The w:r whose w:t text equals `text` exactly."""
    for t in document_root(path).iter(f"{{{W}}}t"):
        if t.text == text:
            return t.getparent()
    raise AssertionError(f"no run with text {text!r}")


def has(run, tag: str) -> bool:
    """True when the run's properties contain w:<tag>."""
    rpr = run.find(f"{{{W}}}rPr")
    return rpr is not None and rpr.find(f"{{{W}}}{tag}") is not None
