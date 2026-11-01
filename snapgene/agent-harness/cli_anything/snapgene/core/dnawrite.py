"""Write a product as a SnapGene .dna file, with its history tree.

The packets are the ones SnapGene 8 writes, in the same order: cookie, bases,
features, primers, notes, history. Enzyme sets, display settings and the
snapshots of parent files are left out; SnapGene opens files without them.
core.dnafile reads the result back, so a product can go straight into the
next command.

This module only writes new files. It refuses to replace the file a product
was read from, and the CLI also refuses every input of the command.
"""
from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from cli_anything.snapgene.core import history, sgxml
from cli_anything.snapgene.core.dnafile import (
    PACKET_COOKIE, PACKET_DNA, PACKET_FEATURES, PACKET_HISTORY, PACKET_NOTES, PACKET_PRIMERS, SnapGeneFile,
)
from cli_anything.snapgene.errors import CliError

# "SnapGene", sequence type 1 (DNA), export version 15, import version 19:
# the values in the sample files SnapGene 8.2.3 ships.
COOKIE = b"SnapGene" + struct.pack(">HHH", 1, 15, 19)


def _packet(kind: int, body: bytes) -> bytes:
    return bytes([kind]) + struct.pack(">I", len(body)) + body


def _xml(element: ET.Element) -> bytes:
    return b'<?xml version="1.0"?>' + ET.tostring(element, encoding="utf-8", xml_declaration=False)


def to_bytes(sg: SnapGeneFile, name: str) -> bytes:
    """The whole file. `name` is the file name the history's top step gets."""
    if sg.kind != "dna":
        raise CliError("not_dna", f"{sg.name} is not DNA. Only DNA is written as .dna.")
    flags = (0x01 if sg.circular else 0) | (0x02 if sg.double_stranded else 0)
    flags |= 0x04 if sg.methylation.get("dam") else 0
    flags |= 0x08 if sg.methylation.get("dcm") else 0
    flags |= 0x10 if sg.methylation.get("ecoki") else 0
    blob = _packet(PACKET_COOKIE, COOKIE)
    blob += _packet(PACKET_DNA, bytes([flags]) + sg.sequence.lower().encode("ascii"))
    blob += _packet(PACKET_FEATURES, _xml(sgxml.features_element(sg.features)))
    if sg.primers:
        blob += _packet(PACKET_PRIMERS, _xml(sgxml.primers_element(sg.primers)))
    blob += _packet(PACKET_NOTES, ET.tostring(sgxml.notes_element(sg.notes), encoding="utf-8",
                                              xml_declaration=False))
    if sg.history:
        blob += _packet(PACKET_HISTORY, history.finish(sg.history, name, sg.length))
    return blob


def write(sg: SnapGeneFile, output: Path, overwrite: bool = False) -> dict:
    output = Path(output)
    if output.suffix.lower() != ".dna":
        raise CliError("bad_output", f"A SnapGene file must end in .dna. Got '{output.name}'.")
    if output.resolve() == sg.path.resolve():
        raise CliError("output_is_input", "The output file must be different from the input file.")
    if output.exists() and not overwrite:
        raise CliError("output_exists", f"{output} exists. Use --overwrite to replace it.")
    if not output.parent.is_dir():
        raise CliError("bad_output", f"The folder {output.parent} does not exist.")
    blob = to_bytes(sg, output.name)
    output.write_bytes(blob)
    return {
        "input": str(sg.path),
        "output": str(output),
        "format": "snapgene",
        "length": sg.length,
        "features": len(sg.features),
        "primers": len(sg.primers),
        "history_steps": len(history.steps(sg.history)),
        "bytes": len(blob),
    }
