"""Turn PubMed record dicts into EndNote XML that EndNote's AppleScript `import` accepts.

Layout copied from a real EndNote 2025 export (AppleScript `export ... as "RSXML"`).
"""
from __future__ import annotations

import re
from xml.sax.saxutils import escape

STYLE_OPEN = '<style face="normal" font="default" size="100%">'
STYLE_CLOSE = "</style>"
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _styled(tag: str, value: str) -> str:
    value = _CONTROL.sub("", value or "")
    if not value:
        return ""
    body = escape(value).replace("\r\n", "&#xD;").replace("\r", "&#xD;").replace("\n", "&#xD;")
    return f"<{tag}>{STYLE_OPEN}{body}{STYLE_CLOSE}</{tag}>"


def _wrap(tag: str, inner: str) -> str:
    return f"<{tag}>{inner}</{tag}>" if inner else ""


def record_xml(rec: dict) -> str:
    pmid = rec["pmid"]
    authors = "".join(_styled("author", a) for a in rec.get("authors", []))
    keywords = "".join(_styled("keyword", k) for k in rec.get("keywords", []))
    parts = [
        '<ref-type name="Journal Article">17</ref-type>',
        _wrap("contributors", _wrap("authors", authors)),
        _wrap("titles", _styled("title", rec.get("title", "")) + _styled("secondary-title", rec.get("journal", ""))),
        _wrap("periodical", _styled("full-title", rec.get("journal", ""))),
        _styled("pages", rec.get("pages", "")),
        _styled("volume", rec.get("volume", "")),
        _styled("number", rec.get("issue", "")),
        _wrap("keywords", keywords),
        _wrap("dates", _styled("year", rec.get("year", ""))),
        _styled("accession-num", pmid),
        _styled("abstract", rec.get("abstract", "")),
        _wrap("urls", _wrap("related-urls", _styled("url", f"https://www.ncbi.nlm.nih.gov/pubmed/{pmid}"))),
        _styled("custom2", rec.get("pmcid", "")),
        _styled("electronic-resource-num", rec.get("doi", "")),
        _styled("remote-database-name", "Medline"),
        _styled("remote-database-provider", "NLM"),
    ]
    return "<record>" + "".join(parts) + "</record>"


def to_endnote_xml(records: list[dict]) -> str:
    body = "".join(record_xml(r) for r in records)
    return f'<?xml version="1.0" encoding="UTF-8" ?><xml><records>{body}</records></xml>'
