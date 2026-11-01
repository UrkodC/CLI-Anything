"""Build the XML SnapGene keeps inside a .dna file: features, primers, notes.

SnapGene does not publish this format. The elements and attributes here copy
the files SnapGene 8 writes, and core.dnafile reads them back. Features are
1-based and inclusive, primer binding sites 0-based, as SnapGene keeps them.

Rich text (descriptions, qualifier values with < or &) is stored the way
SnapGene stores it: as <html><body>...</body></html>, escaped inside the
attribute. dnafile.plain_text turns it back into words.
"""
from __future__ import annotations

import html
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from cli_anything.snapgene.core.dnafile import Feature, Primer

STRAND_CODES = {"+": "1", "-": "2", "both": "3"}
INT_QUALIFIERS = {"codon_start", "transl_table"}
# SnapGene works the translation out again from the bases. A saved one could
# be stale after an edit, so it is not written.
SKIP_QUALIFIERS = {"translation"}
NOTE_TAGS = {"organism": "Organism", "transformed_into": "TransformedInto",
             "comments": "Comments", "created_by": "CreatedBy"}


def rich(text: str) -> str:
    """Plain text as SnapGene's rich text. New lines become line breaks."""
    return "<html><body>" + html.escape(text).replace("\n", "<br>") + "</body></html>"


def _text_value(value: str) -> str:
    return rich(value) if any(c in value for c in "<>&\n") else value


def features_element(features: list[Feature]) -> ET.Element:
    root = ET.Element("Features", nextValidID=str(len(features)))
    for i, feat in enumerate(features):
        attrs = {"recentID": str(i), "name": feat.name}
        if feat.strand in STRAND_CODES:
            attrs["directionality"] = STRAND_CODES[feat.strand]
        if feat.reading_frame != 1:
            frame = feat.reading_frame
            attrs["readingFrame"] = str(-frame if feat.strand == "-" else frame)
        attrs.update({"type": feat.type, "allowSegmentOverlaps": "0", "consecutiveTranslationNumbering": "1"})
        node = ET.SubElement(root, "Feature", attrs)
        for seg in feat.segments:
            seg_attrs = {}
            if seg.name:
                seg_attrs["name"] = seg.name
            seg_attrs["range"] = f"{seg.start}-{seg.end}"
            if seg.color:
                seg_attrs["color"] = seg.color
            seg_attrs["type"] = "standard"
            if seg.translated:
                seg_attrs["translated"] = "1"
            ET.SubElement(node, "Segment", seg_attrs)
        for key, values in feat.qualifiers.items():
            if not key or key in SKIP_QUALIFIERS or not values:
                continue
            q = ET.SubElement(node, "Q", name=key)
            for value in values:
                number = value.strip()
                if key in INT_QUALIFIERS and number.lstrip("-").isdigit():
                    ET.SubElement(q, "V", int=number)
                else:
                    ET.SubElement(q, "V", text=_text_value(value))
    return root


def primers_element(primers: list[Primer]) -> ET.Element:
    root = ET.Element("Primers", nextValidID=str(len(primers)))
    ET.SubElement(root, "HybridizationParams", minContinuousMatchLen="10", allowMismatch="1",
                  minMeltingTemperature="40", showAdditionalFivePrimeMatches="1",
                  minimumFivePrimeAnnealing="15")
    for i, primer in enumerate(primers):
        attrs = {"recentID": str(i), "name": primer.name, "sequence": primer.sequence}
        if primer.description:
            attrs["description"] = rich(primer.description)
        node = ET.SubElement(root, "Primer", attrs)
        # SnapGene writes each site twice, the second time marked simplified.
        for simplified in (False, True):
            for site in primer.sites:
                where = f"{site.start - 1}-{site.end - 1}"
                site_attrs = {"simplified": "1"} if simplified else {}
                site_attrs.update({"location": where, "boundStrand": "1" if site.strand == "-" else "0",
                                   "annealedBases": primer.sequence})
                if site.tm is not None:
                    site_attrs["meltingTemperature"] = str(round(site.tm))
                bound = ET.SubElement(node, "BindingSite", site_attrs)
                ET.SubElement(bound, "Component", hybridizedRange=where, bases=primer.sequence)
    return root


def _stamp(element: ET.Element, when: datetime) -> None:
    element.set("UTC", f"{when.hour}:{when.minute}:{when.second}")
    element.text = f"{when.year}.{when.month}.{when.day}"


def notes_element(notes: dict[str, str], when: datetime | None = None) -> ET.Element:
    """The file's notes. Created and LastModified are the time of writing."""
    when = when or datetime.now(timezone.utc)
    root = ET.Element("Notes")
    ET.SubElement(root, "Type").text = notes.get("type") or "Synthetic"
    ET.SubElement(root, "ConfirmedExperimentally").text = "0"
    _stamp(ET.SubElement(root, "Created"), when)
    _stamp(ET.SubElement(root, "LastModified"), when)
    if notes.get("description"):
        ET.SubElement(root, "Description").text = rich(notes["description"])
    for key, tag in NOTE_TAGS.items():
        if notes.get(key):
            ET.SubElement(root, tag).text = notes[key]
    return root
