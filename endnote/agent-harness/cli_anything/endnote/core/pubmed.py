"""Fetch PubMed records by PMID (NCBI E-utilities) and turn them into plain dicts."""
from __future__ import annotations

import http.client
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as DET

from cli_anything.endnote.errors import CliError

EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
BATCH_SIZE = 200
TOOL_NAME = "cli-anything-endnote"
NETWORK_MESSAGE = "PubMed did not answer. Check the network and run the command again."


def _text(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _authors(article) -> list[str]:
    out = []
    for author in article.findall("AuthorList/Author"):
        collective = _text(author.find("CollectiveName"))
        if collective:
            out.append(collective + ",")
            continue
        last = _text(author.find("LastName"))
        initials = _text(author.find("Initials"))
        if not last:
            continue
        out.append(f"{last}, " + " ".join(f"{c}." for c in initials) if initials else last)
    return out


def _title(article) -> str:
    title = _text(article.find("ArticleTitle"))
    if title.endswith(".") and not title.endswith(".."):
        title = title[:-1]
    return title


def _year(article) -> str:
    for path in ("Journal/JournalIssue/PubDate/Year", "Journal/JournalIssue/PubDate/MedlineDate", "ArticleDate/Year"):
        m = re.search(r"\d{4}", _text(article.find(path)))
        if m:
            return m.group(0)
    return ""


def _abstract(article) -> str:
    parts = []
    for node in article.findall("Abstract/AbstractText"):
        body = _text(node)
        label = node.get("Label")
        if body:
            parts.append(f"{label}: {body}" if label else body)
    return "\r".join(parts)


def _article_id(pubmed_article, article, id_type: str) -> str:
    for node in pubmed_article.findall("PubmedData/ArticleIdList/ArticleId"):
        if node.get("IdType") == id_type:
            return _text(node)
    if article is not None:
        for node in article.findall("ELocationID"):
            if node.get("EIdType") == id_type:
                return _text(node)
    return ""


def parse_records(xml: bytes | str) -> list[dict]:
    root = DET.fromstring(xml)
    records = []
    for pubmed_article in root.findall("PubmedArticle"):
        medline = pubmed_article.find("MedlineCitation")
        article = medline.find("Article") if medline is not None else None
        if article is None:
            continue
        journal = article.find("Journal")
        records.append({
            "pmid": _text(medline.find("PMID")),
            "authors": _authors(article),
            "title": _title(article),
            "journal": _text(journal.find("ISOAbbreviation"))
            or _text(medline.find("MedlineJournalInfo/MedlineTA"))
            or _text(journal.find("Title")),
            "journal_full": _text(journal.find("Title")),
            "year": _year(article),
            "volume": _text(journal.find("JournalIssue/Volume")),
            "issue": _text(journal.find("JournalIssue/Issue")),
            "pages": _text(article.find("Pagination/MedlinePgn")),
            "abstract": _abstract(article),
            "doi": _article_id(pubmed_article, article, "doi"),
            "pmcid": _article_id(pubmed_article, None, "pmc"),
            "keywords": [_text(d) for d in medline.findall("MeshHeadingList/MeshHeading/DescriptorName")],
        })
    return records


def _efetch(pmids: list[str]) -> bytes:
    params = {"db": "pubmed", "retmode": "xml", "id": ",".join(pmids), "tool": TOOL_NAME}
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key
    data = urllib.parse.urlencode(params).encode()
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(EFETCH_URL, data=data, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            last_error = exc
        time.sleep(1 + attempt)
    raise CliError("pubmed_error", NETWORK_MESSAGE) from last_error


def fetch(pmids: list[str], efetch: Callable[[list[str]], bytes] = _efetch) -> tuple[list[dict], list[str]]:
    """Return (records in the order of `pmids`, PMIDs that PubMed did not return)."""
    delay = 0.11 if os.environ.get("NCBI_API_KEY") else 0.34
    by_pmid: dict[str, dict] = {}
    for i in range(0, len(pmids), BATCH_SIZE):
        if i:
            time.sleep(delay)
        raw = efetch(pmids[i:i + BATCH_SIZE])
        try:
            parsed = parse_records(raw)
        except (DET.ParseError, DefusedXmlException) as exc:
            raise CliError("pubmed_error", "PubMed sent an answer that is not valid XML. Run the command again.") from exc
        for record in parsed:
            by_pmid[record["pmid"]] = record
    found = [by_pmid[p] for p in pmids if p in by_pmid]
    missing = [p for p in pmids if p not in by_pmid]
    return found, missing
