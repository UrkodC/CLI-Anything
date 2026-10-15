"""Add and find PubMed papers in the open EndNote library."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from cli_anything.endnote.core import enxml, pubmed
from cli_anything.endnote.errors import CliError
from cli_anything.endnote.utils import endnote_backend

IMPORT_CHUNK = 25


@dataclass(frozen=True)
class LibraryRecord:
    pmid: str
    rec_number: int
    citation: str
    title: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AddReport:
    library: str = ""
    added: list[LibraryRecord] = field(default_factory=list)
    skipped: list[LibraryRecord] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "library": self.library,
            "added": [r.to_dict() for r in self.added],
            "skipped": [r.to_dict() for r in self.skipped],
            "failed": list(self.failed),
            "warnings": list(self.warnings),
        }


def collect_pmids(args: list[str], file_text: str = "") -> list[str]:
    """Every run of digits in `args` and in `file_text` (lines starting with # ignored), unique, in order."""
    chunks = list(args) + [line for line in file_text.splitlines() if not line.lstrip().startswith("#")]
    seen: dict[str, None] = {}
    for chunk in chunks:
        for digits in re.findall(r"\d+", chunk):
            seen.setdefault(str(int(digits)), None)
    return list(seen)


def require_single_library(backend=endnote_backend) -> str:
    names = backend.open_libraries()
    if not names:
        raise CliError("no_library", "Open EndNote and your library, then run the command again.")
    if len(names) > 1:
        raise CliError("many_libraries", "Close all libraries except one: " + ", ".join(names) + ".")
    return names[0]


def _lookup_exact(pmids: list[str], backend) -> tuple[dict[str, LibraryRecord | None], list[str]]:
    hits: dict[str, list] = {p: [] for p in pmids}
    for row in backend.lookup(pmids):
        if row.query in hits and row.accession.strip() == row.query:
            hits[row.query].append(row)
    result: dict[str, LibraryRecord | None] = {}
    warnings: list[str] = []
    for pmid, rows in hits.items():
        if not rows:
            result[pmid] = None
            continue
        rows.sort(key=lambda r: r.rec_number)
        if len({r.rec_number for r in rows}) > 1:
            numbers = ", ".join(str(r.rec_number) for r in rows)
            warnings.append(f"duplicate PMID in library: {pmid} (records {numbers}); using #{rows[0].rec_number}")
        best = rows[0]
        result[pmid] = LibraryRecord(pmid, best.rec_number, best.citation, best.title)
    return result, warnings


def find_pmids(pmids: list[str], backend=endnote_backend) -> tuple[dict[str, LibraryRecord | None], list[str]]:
    require_single_library(backend)
    return _lookup_exact(pmids, backend)


def add_pmids(pmids: list[str], backend=endnote_backend, fetch=pubmed.fetch) -> AddReport:
    report = AddReport(library=require_single_library(backend))
    existing, warnings = _lookup_exact(pmids, backend)
    report.warnings.extend(warnings)
    report.skipped = [existing[p] for p in pmids if existing[p] is not None]
    missing = [p for p in pmids if existing[p] is None]
    if not missing:
        return report
    records, not_found = fetch(missing)
    report.failed.extend({"pmid": p, "reason": "not found on PubMed"} for p in not_found)
    for i in range(0, len(records), IMPORT_CHUNK):
        backend.import_xml(enxml.to_endnote_xml(records[i:i + IMPORT_CHUNK]))
    imported = [r["pmid"] for r in records]
    after, warnings = _lookup_exact(imported, backend)
    report.warnings.extend(warnings)
    for pmid in imported:
        if after[pmid] is None:
            report.failed.append({"pmid": pmid, "reason": "import did not appear in the library"})
        else:
            report.added.append(after[pmid])
    return report


def status(backend=endnote_backend) -> dict:
    names = backend.open_libraries()
    if names is None:
        return {"running": False, "libraries": [], "record_count": None}
    count = backend.record_count() if len(names) == 1 else None
    return {"running": True, "libraries": names, "record_count": count}
