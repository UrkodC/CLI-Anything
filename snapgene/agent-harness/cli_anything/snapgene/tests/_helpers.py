"""Build small SnapGene files for tests, with the packet layout SnapGene writes."""
from __future__ import annotations

import random
import struct
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from cli_anything.snapgene.core.seqtools import revcomp

# The bases printed around the cut sites in Figure 3 of Takara's In-Fusion HD
# manual (PT5165-1), so the tool's primers can be checked against Takara's own.
TAKARA_MCS = "ATACATTATACGAAGTTATCAGTCGACGGTACCGGACATATGCCCGGGAATTCCTGCAGGATCCGCT"
TAKARA_DOWNSTREAM = "AAGCTTTCTAGACCATTCGTTTGGCG"
TEST_SITES = ("GTCGAC", "GGTACC", "CCCGGG", "AAGCTT", "GAATTC", "CTGCAG", "GGATCC", "TCTAGA")
# A 297 bp gene: starts with ATG, ends with a stop codon, GC near 50%.
TEST_GENE = (
    "ATGGTGTCTAAGGGCGAAGAGCTGATTAAGGAGAACATGCACATGAAGCTGTACATGGAGGGCACCGTGAACAACCACCAC"
    "TTCAAGTGCACATCCGAGGGCGAAGGCAAGCCCTACGAGGGCACCCAGACCATGAGAATCAAGGTGGTCGAGGGCGGCCCT"
    "CTCCCCTTCGCCTTCGACATCCTGGCTACCAGCTTCATGTACGGCAGCAAAACCTTCATCAACCACACCCAGGGCATCCCC"
    "GACTTCTTTAAGCAGTCCTTCCCTGAGGGCTTCACATGGGAGAGAGTCACCTAA"
)


# The Gateway att sites exactly as SnapGene annotates them in the plasmids it
# ships, each read 5' to 3' along its own strand. ATTB1 and ATTB2 are what an
# LR reaction has to produce, so they are the answer the tests check against.
ATTL1 = ("CAAATAATGATTTTATTTTGACTGATAGTGACCTGTTCGTTGCAACAAATTGATGAGCAATGCTTTTTT"
         "ATAATGCCAACTTTGTACAAAAAAGCAGGCT")
ATTL2 = ("CAAATAATGATTTTATTTTGACTGATAGTGACCTGTTCGTTGCAACAAATTGATAAGCAATGCTTTCTT"
         "ATAATGCCAACTTTGTACAAGAAAGCTGGGT")
ATTR1 = ("ACAAGTTTGTACAAAAAAGCTGAACGAGAAACGTAAAATGATATAAATATCAATATATTAAATTAGATT"
         "TTGCATAAAAAACAGACTACATAATACTGTAAAACACAACATATCCAGTCACTATG")
ATTR2 = ("ACCACTTTGTACAAGAAAGCTGAACGAGAAACGTAAAATGATATAAATATCAATATATTAAATTAGATT"
         "TTGCATAAAAAACAGACTACATAATACTGTAAAACACAACATATCCAGTCACTATG")
ATTB1 = "ACAAGTTTGTACAAAAAAGCAGGCT"
ATTB2 = "ACCACTTTGTACAAGAAAGCTGGGT"

SEQ_TYPE = {"dna": 1, "protein": 2, "rna": 7}
SEQ_PACKET = {"dna": 0x00, "protein": 0x15, "rna": 0x20}
DIRECTION = {"+": "1", "-": "2", "both": "3"}


def packet(kind: int, data: bytes) -> bytes:
    return bytes([kind]) + struct.pack(">I", len(data)) + data


def cookie(seq_type: int = 1) -> bytes:
    return packet(0x09, b"SnapGene" + struct.pack(">HHH", seq_type, 15, 19))


def features_xml(features: list[dict]) -> bytes:
    parts = ['<?xml version="1.0"?><Features nextValidID="99">']
    for f in features:
        attrs = f' name={quoteattr(f["name"])} type={quoteattr(f.get("type", "misc_feature"))}'
        if f.get("strand") in DIRECTION:
            attrs += f' directionality="{DIRECTION[f["strand"]]}"'
        if f.get("reading_frame"):
            attrs += f' readingFrame="{f["reading_frame"]}"'
        parts.append(f"<Feature{attrs}>")
        for seg in f["segments"]:
            a, b = seg[0], seg[1]
            seg_type = seg[2] if len(seg) > 2 else "standard"
            parts.append(f'<Segment range="{a}-{b}" color="#ffffff" type="{seg_type}"/>')
        for key, values in f.get("qualifiers", {}).items():
            parts.append(f"<Q name={quoteattr(key)}>")
            for v in values:
                parts.append(f"<V int={quoteattr(str(v))}/>" if isinstance(v, int) else f"<V text={quoteattr(v)}/>")
            parts.append("</Q>")
        parts.append("</Feature>")
    parts.append("</Features>")
    return "".join(parts).encode("utf-8")


def primers_xml(primers: list[dict]) -> bytes:
    parts = ['<?xml version="1.0"?><Primers nextValidID="9"><HybridizationParams minContinuousMatchLen="10"/>']
    for p in primers:
        parts.append(f'<Primer name={quoteattr(p["name"])} sequence={quoteattr(p["sequence"])} '
                     f'description={quoteattr(p.get("description", ""))}>')
        for a, b, strand, *rest in p.get("sites", []):
            simplified = ' simplified="1"' if rest and rest[0] else ""
            parts.append(f'<BindingSite{simplified} location="{a}-{b}" boundStrand="{strand}" meltingTemperature="60"/>')
        parts.append("</Primer>")
    parts.append("</Primers>")
    return "".join(parts).encode("utf-8")


def notes_xml(notes: dict[str, str]) -> bytes:
    body = "".join(f"<{k}>{escape(v)}</{k}>" for k, v in notes.items())
    return f"<Notes>{body}</Notes>".encode("utf-8")


def write_snapgene(
    path: Path,
    sequence: str,
    kind: str = "dna",
    circular: bool = True,
    flags: int | None = None,
    features: list[dict] | None = None,
    primers: list[dict] | None = None,
    notes: dict[str, str] | None = None,
    seq_type: int | None = None,
) -> Path:
    if flags is None:
        flags = (0x01 if circular else 0) | (0x02 if kind == "dna" else 0)
    data = cookie(seq_type if seq_type is not None else SEQ_TYPE[kind])
    data += packet(SEQ_PACKET[kind], bytes([flags]) + sequence.encode("ascii"))
    if features:
        data += packet(0x0A, features_xml(features))
    if primers:
        data += packet(0x05, primers_xml(primers))
    if notes:
        data += packet(0x06, notes_xml(notes))
    path.write_bytes(data)
    return path


def filler(size: int, seed: int = 0) -> str:
    """Random bases with none of the test enzyme sites in them."""
    rng = random.Random(seed)
    while True:
        out = "".join(rng.choice("ACGT") for _ in range(size))
        if not any(site in out for site in TEST_SITES):
            return out


def entry_clone(path: Path, insert: str = TEST_GENE, name: str = "gene",
                backwards: bool = False) -> Path:
    """A pENTR-style entry clone: attL1, the insert, then attL2 facing back.

    With `backwards` the same plasmid is drawn the other way round, so attL2
    is the site on the plus strand. The insert must still come out forwards.
    """
    head, tail = filler(300, 41), filler(400, 42)
    first, second = (ATTL1, ATTL2) if not backwards else (ATTL2, ATTL1)
    middle = insert if not backwards else revcomp(insert)
    sequence = head + first + middle + revcomp(second) + tail
    a = (len(head) + 1, len(head) + len(first))
    b = (a[1] + 1, a[1] + len(middle))
    c = (b[1] + 1, b[1] + len(second))
    features = [
        {"name": "attL1" if not backwards else "attL2", "type": "protein_bind",
         "strand": "+", "segments": [a]},
        {"name": name, "type": "CDS", "strand": "+" if not backwards else "-", "segments": [b]},
        {"name": "attL2" if not backwards else "attL1", "type": "protein_bind",
         "strand": "-", "segments": [c]},
    ]
    return write_snapgene(path, sequence, circular=True, features=features)


def destination_vector(path: Path, cassette_named: bool = True, spacer: str = "GC") -> Path:
    """A pDEST-style vector: a tag, attR1, the ccdB cassette, attR2, a tag.

    The spacer on each side is two bases, so with the 25 bp attB site between
    them both tags end up in frame with the insert.
    """
    head, tail = filler(200, 51), filler(500, 56)
    ntag = "ATG" + filler(60, 53)
    cassette = filler(330, 54)
    ctag = filler(45, 55) + "TAA"
    sequence = (head + ntag + spacer + ATTR1 + cassette + revcomp(ATTR2)
                + spacer + ctag + tail)
    n = (len(head) + 1, len(head) + len(ntag))
    r1 = (n[1] + len(spacer) + 1, n[1] + len(spacer) + len(ATTR1))
    cas = (r1[1] + 1, r1[1] + len(cassette))
    r2 = (cas[1] + 1, cas[1] + len(ATTR2))
    c = (r2[1] + len(spacer) + 1, r2[1] + len(spacer) + len(ctag))
    features = [
        {"name": "N tag", "type": "CDS", "strand": "+", "segments": [n]},
        {"name": "attR1", "type": "protein_bind", "strand": "+", "segments": [r1]},
        {"name": "ccdB" if cassette_named else "stuffer", "type": "CDS", "strand": "+",
         "segments": [(cas[0] + 30, cas[1] - 30)]},
        {"name": "attR2", "type": "protein_bind", "strand": "-", "segments": [r2]},
        {"name": "C tag", "type": "CDS", "strand": "+", "segments": [c]},
    ]
    return write_snapgene(path, sequence, circular=True, features=features)


def takara_vector(path: Path, features: list[dict] | None = None) -> Path:
    """A circular test plasmid holding the bases from Figure 3 of the manual.

    Cut once by SalI, KpnI, SmaI, EcoRI, PstI, BamHI, XbaI and HindIII.
    """
    sequence = filler(600, 1) + TAKARA_MCS + filler(40, 2) + TAKARA_DOWNSTREAM + filler(900, 3)
    return write_snapgene(path, sequence, circular=True, features=features)
