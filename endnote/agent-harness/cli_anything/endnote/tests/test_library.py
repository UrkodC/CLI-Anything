from __future__ import annotations

import re

import pytest

from cli_anything.endnote.core import library
from cli_anything.endnote.errors import CliError
from cli_anything.endnote.utils.endnote_backend import FoundRecord

SIEGEL = ("41528114", 2, "{Siegel, 2026 #2}", "Cancer statistics, 2026")


class FakeBackend:
    """Stands in for endnote_backend. Records are (accession, rec_number, citation, title)."""

    def __init__(self, libraries=("Test.enl",), records=()):
        self.libraries = None if libraries is None else list(libraries)
        self.records = [tuple(r) for r in records]
        self.imports: list[str] = []
        self.import_drops: set[str] = set()
        self.next_number = 100
        self.current = None  # name of the library EndNote has open right now; None means libraries[0]

    def open_libraries(self):
        return self.libraries

    def record_count(self):
        return len(self.records)

    def _check(self, library_name):
        current = self.current if self.current is not None else (self.libraries or [None])[0]
        if current != library_name:
            raise CliError("library_changed", "The open EndNote library changed during the run.")

    def lookup(self, pmids, library_name):
        self._check(library_name)
        return [
            FoundRecord(q, num, acc, cite, title)
            for q in pmids
            for (acc, num, cite, title) in self.records
            if q in acc
        ]

    def import_xml(self, xml, library_name):
        self._check(library_name)
        self.imports.append(xml)
        for pmid in re.findall(r"<accession-num><style[^>]*>(\d+)</style>", xml):
            if pmid in self.import_drops:
                continue
            self.next_number += 1
            self.records.append((pmid, self.next_number, f"{{New, 2020 #{self.next_number}}}", f"Title {pmid}"))


def fake_fetch(known):
    def fetch(pmids):
        found = [{"pmid": p, "authors": ["New, A."], "title": f"Title {p}", "journal": "J"} for p in pmids if p in known]
        return found, [p for p in pmids if p not in known]
    return fetch


def test_collect_pmids_dedupes_and_skips_comment_lines():
    text = "# made 2026-10-15\n15033360\n17625570\n"
    assert library.collect_pmids(["17625570,", "41528114"], text) == ["17625570", "41528114", "15033360"]


@pytest.mark.parametrize("libraries, code", [(None, "no_library"), ([], "no_library"), (["A.enl", "B.enl"], "many_libraries")])
def test_require_single_library(libraries, code):
    with pytest.raises(CliError) as exc:
        library.require_single_library(FakeBackend(libraries=libraries))
    assert exc.value.code == code


def test_error_messages_match_the_spec():
    with pytest.raises(CliError) as exc:
        library.require_single_library(FakeBackend(libraries=["A.enl", "B.enl"]))
    assert exc.value.message == "Close all libraries except one: A.enl, B.enl."
    with pytest.raises(CliError) as exc:
        library.require_single_library(FakeBackend(libraries=None))
    assert exc.value.message == "Open EndNote and your library, then run the command again."


def test_add_skips_existing_adds_new_and_reports_unknown():
    be = FakeBackend(records=[SIEGEL])
    report = library.add_pmids(["41528114", "17625570", "99999999"], backend=be, fetch=fake_fetch({"17625570"}))
    assert report.library == "Test.enl"
    assert [(r.pmid, r.rec_number) for r in report.skipped] == [("41528114", 2)]
    assert [(r.pmid, r.rec_number, r.citation) for r in report.added] == [("17625570", 101, "{New, 2020 #101}")]
    assert report.failed == [{"pmid": "99999999", "reason": "not found on PubMed"}]
    assert len(be.imports) == 1


def test_add_twice_adds_nothing_the_second_time():
    be = FakeBackend()
    fetch = fake_fetch({"17625570"})
    library.add_pmids(["17625570"], backend=be, fetch=fetch)
    second = library.add_pmids(["17625570"], backend=be, fetch=fetch)
    assert second.added == []
    assert [r.pmid for r in second.skipped] == ["17625570"]
    assert len(be.imports) == 1


def test_substring_hit_is_not_a_match():
    be = FakeBackend(records=[SIEGEL])
    report = library.add_pmids(["4152811"], backend=be, fetch=fake_fetch(set()))
    assert report.skipped == []
    assert report.failed == [{"pmid": "4152811", "reason": "not found on PubMed"}]


def test_duplicate_pmid_uses_lowest_record_and_warns():
    be = FakeBackend(records=[("17625570", 30, "{Soda, 2007 #30}", "t"), ("17625570", 18, "{Soda, 2007 #18}", "t")])
    found, warnings = library.find_pmids(["17625570"], backend=be)
    assert found["17625570"].rec_number == 18
    assert warnings == ["duplicate PMID in library: 17625570 (records 18, 30); using #18"]


def test_import_that_does_not_appear_is_a_failure():
    be = FakeBackend()
    be.import_drops = {"17625570"}
    report = library.add_pmids(["17625570"], backend=be, fetch=fake_fetch({"17625570"}))
    assert report.added == []
    assert report.failed == [{"pmid": "17625570", "reason": "import did not appear in the library"}]


def test_imports_are_sent_in_chunks_of_25():
    pmids = [str(1000 + i) for i in range(30)]
    be = FakeBackend()
    report = library.add_pmids(pmids, backend=be, fetch=fake_fetch(set(pmids)))
    assert len(be.imports) == 2
    assert len(report.added) == 30


def test_add_propagates_library_changed_and_imports_nothing_elsewhere():
    be = FakeBackend()

    def fetch(pmids):
        be.current = "Other.enl"   # the user switched libraries during the PubMed fetch
        return fake_fetch(set(pmids))(pmids)

    with pytest.raises(CliError) as exc:
        library.add_pmids(["17625570"], backend=be, fetch=fetch)
    assert exc.value.code == "library_changed"
    assert be.imports == [] and be.records == []


def test_add_checks_the_library_before_anything_else():
    with pytest.raises(CliError):
        library.add_pmids(["1"], backend=FakeBackend(libraries=[]), fetch=lambda p: pytest.fail("no fetch expected"))


def test_status():
    assert library.status(FakeBackend(libraries=None)) == {"running": False, "libraries": [], "record_count": None}
    assert library.status(FakeBackend(records=[SIEGEL])) == {"running": True, "libraries": ["Test.enl"], "record_count": 1}
    assert library.status(FakeBackend(libraries=["A.enl", "B.enl"]))["record_count"] is None


def test_find_returns_none_for_missing():
    found, _ = library.find_pmids(["41528114", "1"], backend=FakeBackend(records=[SIEGEL]))
    assert found["41528114"].citation == "{Siegel, 2026 #2}"
    assert found["1"] is None
