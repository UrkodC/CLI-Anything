"""Read SnapGene sequence files (.dna, .rna, .prot).

A SnapGene file is a list of packets. Each packet is 1 byte of type, a 4-byte
big-endian length, then the data. The first packet is the cookie ("SnapGene"
plus the sequence type). The history tree is kept as text, so a product can
carry its parents' history on. Packets this module does not need (enzyme
display settings, traces, the snapshots of parent files) are skipped.

Coordinates in this module are 1-based and inclusive, as in the SnapGene window.
"""
from __future__ import annotations

import html
import lzma
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from defusedxml import ElementTree

from cli_anything.snapgene.errors import CliError

PACKET_DNA = 0x00
PACKET_PRIMERS = 0x05
PACKET_NOTES = 0x06
PACKET_HISTORY = 0x07
PACKET_COOKIE = 0x09
PACKET_FEATURES = 0x0A
PACKET_PROTEIN = 0x15
PACKET_RNA = 0x20

# Sequence type in the cookie packet -> (kind, packet that holds the residues)
SEQUENCE_TYPES = {1: ("dna", PACKET_DNA), 2: ("protein", PACKET_PROTEIN), 7: ("rna", PACKET_RNA)}

DIRECTIONALITY = {"1": "+", "2": "-", "3": "both"}

NOTE_FIELDS = {
    "Type": "type",
    "Description": "description",
    "Created": "created",
    "LastModified": "modified",
    "CreatedBy": "created_by",
    "Organism": "organism",
    "TransformedInto": "transformed_into",
    "Comments": "comments",
    "AccessionNumber": "accession",
    "CustomMapLabel": "map_label",
}


@dataclass
class Segment:
    start: int
    end: int
    name: str | None = None
    translated: bool = False
    color: str | None = None   # kept so a written .dna draws the feature as before

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "name": self.name, "translated": self.translated}


@dataclass
class Feature:
    index: int
    name: str
    type: str
    strand: str
    segments: list[Segment]
    qualifiers: dict[str, list[str]] = field(default_factory=dict)
    reading_frame: int = 1  # 1, 2 or 3: where the first codon starts, counted from the feature's 5' end

    @property
    def start(self) -> int:
        return self.segments[0].start

    @property
    def end(self) -> int:
        return self.segments[-1].end

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "type": self.type,
            "strand": self.strand,
            "start": self.start,
            "end": self.end,
            "segments": [s.to_dict() for s in self.segments],
            "reading_frame": self.reading_frame,
            "qualifiers": self.qualifiers,
        }


@dataclass
class BindingSite:
    start: int
    end: int
    strand: str
    tm: float | None

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "strand": self.strand, "tm": self.tm}


@dataclass
class Primer:
    index: int
    name: str
    sequence: str
    description: str
    sites: list[BindingSite]

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "sequence": self.sequence,
            "length": len(self.sequence),
            "description": self.description,
            "binding_sites": [s.to_dict() for s in self.sites],
        }


@dataclass
class SnapGeneFile:
    path: Path
    kind: str
    sequence: str
    circular: bool
    double_stranded: bool
    methylation: dict[str, bool]
    features: list[Feature]
    primers: list[Primer]
    notes: dict[str, str]
    history: str | None = None   # the HistoryTree XML, if the file has one

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def length(self) -> int:
        return len(self.sequence)

    @property
    def is_nucleotide(self) -> bool:
        return self.kind in ("dna", "rna")

    def summary(self) -> dict:
        return {
            "path": str(self.path),
            "name": self.name,
            "kind": self.kind,
            "length": self.length,
            "topology": "circular" if self.circular else "linear",
            "strandedness": "double" if self.double_stranded else "single",
            "methylation": self.methylation,
            "feature_count": len(self.features),
            "primer_count": len(self.primers),
            "notes": self.notes,
        }


def plain_text(value: str | None) -> str:
    """SnapGene stores rich text as <html><body>...</body></html>. Return the words only."""
    if not value:
        return ""
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def read_packets(data: bytes, path: Path) -> list[tuple[int, bytes]]:
    packets = []
    i = 0
    while i < len(data):
        if i + 5 > len(data):
            raise CliError("bad_file", f"{path} is damaged: the last packet is cut short.")
        kind = data[i]
        (size,) = struct.unpack(">I", data[i + 1:i + 5])
        if i + 5 + size > len(data):
            raise CliError("bad_file", f"{path} is damaged: the last packet is cut short.")
        packets.append((kind, data[i + 5:i + 5 + size]))
        i += 5 + size
    return packets


def _xml(blob: bytes, path: Path, what: str):
    try:
        return ElementTree.fromstring(blob)
    except ElementTree.ParseError as exc:
        raise CliError("bad_file", f"{path} has unreadable {what} data: {exc}") from exc


def _parse_features(blob: bytes, path: Path) -> list[Feature]:
    root = _xml(blob, path, "feature")
    features = []
    for index, node in enumerate(root.findall("Feature")):
        segments = []
        for seg in node.findall("Segment"):
            if seg.get("type") == "gap":
                continue
            start, end = (int(x) for x in seg.get("range", "0-0").split("-"))
            segments.append(Segment(start, end, seg.get("name"), seg.get("translated") == "1",
                                    seg.get("color")))
        if not segments:
            continue
        qualifiers: dict[str, list[str]] = {}
        for q in node.findall("Q"):
            values = []
            for v in q.findall("V"):
                for attr in ("text", "int", "predef"):
                    if v.get(attr) is not None:
                        values.append(plain_text(v.get(attr)))
                        break
            qualifiers[q.get("name", "")] = values
        # readingFrame is signed by strand in SnapGene (-1 = first frame of a reverse feature)
        try:
            frame = abs(int(node.get("readingFrame", "1"))) or 1
        except ValueError:
            frame = 1
        features.append(Feature(
            index=index,
            name=node.get("name", ""),
            type=node.get("type", "misc_feature"),
            strand=DIRECTIONALITY.get(node.get("directionality", ""), "none"),
            segments=segments,
            qualifiers=qualifiers,
            reading_frame=frame if frame in (1, 2, 3) else 1,
        ))
    return features


def _parse_primers(blob: bytes, path: Path) -> list[Primer]:
    root = _xml(blob, path, "primer")
    primers = []
    for index, node in enumerate(root.findall("Primer")):
        sites = []
        seen = set()
        for site in node.findall("BindingSite"):
            if site.get("simplified") == "1":
                continue
            a, b = (int(x) for x in site.get("location", "0-0").split("-"))
            strand = "-" if site.get("boundStrand") == "1" else "+"
            if (a, b, strand) in seen:
                continue
            seen.add((a, b, strand))
            tm = site.get("meltingTemperature")
            sites.append(BindingSite(a + 1, b + 1, strand, float(tm) if tm else None))
        primers.append(Primer(
            index=index,
            name=node.get("name", ""),
            sequence=node.get("sequence", "").upper(),
            description=plain_text(node.get("description")),
            sites=sites,
        ))
    return primers


def _parse_notes(blob: bytes, path: Path) -> dict[str, str]:
    root = _xml(blob, path, "notes")
    notes = {}
    for tag, key in NOTE_FIELDS.items():
        node = root.find(tag)
        if node is not None and node.text:
            notes[key] = plain_text(node.text)
    return notes


def _history(blob: bytes) -> str | None:
    """The history tree as text. SnapGene writes it plain or xz-compressed.

    A history this module cannot read is left out rather than failing the
    whole file: the sequence and features are still good without it.
    """
    try:
        text = blob if blob[:1] == b"<" else lzma.decompress(blob)
        return text.decode("utf-8")
    except (lzma.LZMAError, UnicodeDecodeError):
        return None


def load(path: str | Path) -> SnapGeneFile:
    path = Path(path)
    if not path.is_file():
        raise CliError("no_file", f"{path} does not exist.")
    data = path.read_bytes()
    if len(data) < 19 or data[0] != PACKET_COOKIE or data[5:13] != b"SnapGene":
        raise CliError("bad_file", f"{path} is not a SnapGene file.")
    packets = read_packets(data, path)
    (seq_type,) = struct.unpack(">H", packets[0][1][8:10])
    if seq_type not in SEQUENCE_TYPES:
        raise CliError(
            "unsupported_file",
            f"{path} is a SnapGene file of a type this tool does not read. Use a .dna, .rna or .prot file.",
        )
    kind, seq_packet = SEQUENCE_TYPES[seq_type]
    by_type: dict[int, bytes] = {}
    for kind_byte, blob in packets[1:]:
        by_type.setdefault(kind_byte, blob)
    if seq_packet not in by_type:
        raise CliError("bad_file", f"{path} has no sequence data.")
    raw = by_type[seq_packet]
    flags = raw[0] if raw else 0
    sequence = raw[1:].decode("ascii", errors="replace").upper()
    is_dna = kind == "dna"
    return SnapGeneFile(
        path=path,
        kind=kind,
        sequence=sequence,
        circular=bool(flags & 0x01) and kind != "protein",
        double_stranded=bool(flags & 0x02) and is_dna,
        methylation={
            "dam": bool(flags & 0x04) and is_dna,
            "dcm": bool(flags & 0x08) and is_dna,
            "ecoki": bool(flags & 0x10) and is_dna,
        },
        features=_parse_features(by_type[PACKET_FEATURES], path) if PACKET_FEATURES in by_type else [],
        primers=_parse_primers(by_type[PACKET_PRIMERS], path) if PACKET_PRIMERS in by_type else [],
        notes=_parse_notes(by_type[PACKET_NOTES], path) if PACKET_NOTES in by_type else {},
        history=_history(by_type[PACKET_HISTORY]) if PACKET_HISTORY in by_type else None,
    )


def find_feature(sg: SnapGeneFile, name: str | None = None, index: int | None = None) -> Feature:
    """Pick one feature by exact name (case-insensitive) or by its index in the file."""
    if index is not None:
        for feat in sg.features:
            if feat.index == index:
                return feat
        raise CliError("feature_not_found", f"{sg.name} has no feature with index {index}.")
    wanted = (name or "").casefold()
    hits = [f for f in sg.features if f.name.casefold() == wanted]
    if not hits:
        names = ", ".join(sorted({f.name for f in sg.features})) or "none"
        raise CliError("feature_not_found", f"{sg.name} has no feature named '{name}'. Features: {names}.")
    if len(hits) > 1:
        listed = ", ".join(f"{f.index} ({f.start}-{f.end})" for f in hits)
        raise CliError(
            "ambiguous_feature",
            f"{sg.name} has {len(hits)} features named '{name}'. Use --feature-index with one of: {listed}.",
        )
    return hits[0]
