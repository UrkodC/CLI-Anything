from __future__ import annotations

import pytest

from cli_anything.endnote.core.word_link import MARKER_RE, merge_citations, parse_pmids


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
