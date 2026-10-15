"""Find [PMID ...] markers in a .docx and replace them with EndNote temporary citations."""
from __future__ import annotations

import re

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

MARKER_RE = re.compile(
    r"\[\s*PMIDs?\s*:?\s*(\d{1,9}(?:\s*[;,]\s*\d{1,9})*)\s*\]",
    re.IGNORECASE,
)


def parse_pmids(group: str) -> list[str]:
    """'29079636; 40055571' -> ['29079636', '40055571']. Leading zeros are dropped."""
    return [str(int(x)) for x in re.split(r"\s*[;,]\s*", group.strip())]


def merge_citations(citations: list[str]) -> str:
    """['{Soda, 2007 #18}', '{Siegel, 2026 #2}'] -> '{Soda, 2007 #18; Siegel, 2026 #2}'."""
    inner = [c.strip().removeprefix("{").removesuffix("}").strip() for c in citations]
    return "{" + "; ".join(inner) + "}"
