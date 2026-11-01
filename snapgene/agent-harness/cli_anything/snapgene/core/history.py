"""Build the history tree that SnapGene draws in its History view.

A .dna file can keep the story of how it was made: the file itself at the top,
the files it was made from below it, and their own stories below them. Each
step (a Node) names its operation (inFusionCloning, gatewayLRCloning,
amplifyFragment ...; "invalid" means "not made here"), lists PCR primers as
Oligo elements, and has one InputSummary per input saying what was taken from
that input.

SnapGene does not publish this format. These rules come from the sample files
SnapGene ships and from test files opened in SnapGene 8.2.3:

- A step needs exactly one InputSummary per input. With fewer, SnapGene quits
  as it opens the file, with no message. check() refuses such a tree.
- InputSummary values that come with names (name1, name2) are positions shown
  next to those names: "Replace BamHI (444) - HindIII (474)". Values without
  names are 0-based and shown one higher: val1=0, val2=737 shows "1 .. 738".
- HistoryColors paint bases: red for new, black for used. Ranges are 0-based.
- SnapGene also keeps a snapshot of each parent (packet 0x0B), marked
  resurrectable="1". This tool writes no snapshots, so the mark is removed.
  SnapGene opens such a file and draws the tree; a parent just cannot be
  reopened from it.
"""
from __future__ import annotations

import copy
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as SafeET

from cli_anything.snapgene.core import sgxml
from cli_anything.snapgene.core.dnafile import Feature, SnapGeneFile
from cli_anything.snapgene.errors import CliError

NOT_MADE_HERE = "invalid"


def _node(name: str, length: int, circular: bool, operation: str) -> ET.Element:
    attrs = {"name": name, "type": "DNA", "seqLen": str(length), "strandedness": "double",
             "ID": "0", "circular": "1" if circular else "0"}
    if not circular:
        attrs.update({"upstreamModification": "Unmodified", "downstreamModification": "Unmodified"})
    attrs["operation"] = operation
    return ET.Element("Node", attrs)


def _ranges(spans: list[tuple[int, int]], length: int) -> list[tuple[int, int]]:
    """1-based inclusive spans, maybe wrapping the origin, as 0-based ranges."""
    out = []
    for start, end in spans:
        if start <= end:
            out.append((start - 1, end - 1))
        else:
            out += [(start - 1, length - 1), (0, end - 1)]
    return out


def _strand_colors(kind: str, spans: list[tuple[int, int]], length: int, color: str) -> ET.Element:
    colors = ET.Element("StrandColors", type=kind)
    for strand in ("TopStrand", "BottomStrand"):
        holder = ET.SubElement(colors, strand)
        for a, b in _ranges(spans, length):
            ET.SubElement(holder, "ColorRange", range=f"{a}..{b}", colors=color)
    return colors


def _colors_holder(node: ET.Element) -> ET.Element:
    """The node's HistoryColors, made if missing, placed before its summaries and inputs."""
    holder = node.find("HistoryColors")
    if holder is None:
        holder = ET.Element("HistoryColors")
        place = next((i for i, child in enumerate(node) if child.tag in ("InputSummary", "Node")), len(node))
        node.insert(place, holder)
    return holder


def summary(manipulation: str, val1: int, val2: int,
            name1: str | None = None, name2: str | None = None) -> ET.Element:
    attrs = {"manipulation": manipulation}
    if name1 or name2:
        attrs.update({"name1": name1 or "", "name2": name2 or ""})
    attrs.update({"val1": str(val1), "val2": str(val2)})
    if name1 or name2:
        attrs.update({"siteCount1": "1", "siteCount2": "1"})
    return ET.Element("InputSummary", attrs)


def _strip_snapshots(node: ET.Element) -> None:
    for item in node.iter("Node"):
        item.attrib.pop("resurrectable", None)


def _own_tree(sg: SnapGeneFile) -> ET.Element | None:
    """The top step of the file's own history, when it is usable."""
    if not sg.history:
        return None
    try:
        root = SafeET.fromstring(sg.history.encode("utf-8"))
    except SafeET.ParseError:
        return None
    top = root.find("Node") if root.tag == "HistoryTree" else None
    if top is None or top.get("seqLen") != str(sg.length) or not list(top.iter("Node"))[1:]:
        return None
    top = copy.deepcopy(top)
    try:
        _check_steps(top)
    except CliError:
        return None
    return top


def file_node(sg: SnapGeneFile, used: list[tuple[int, int]]) -> ET.Element:
    """A file that went into the product. `used` is what was taken, 1-based.

    When the file has its own history, that story is kept under it, so the
    product's tree reaches all the way back.
    """
    node = _own_tree(sg)
    if node is None:
        node = _node(sg.path.name, sg.length, sg.circular, NOT_MADE_HERE)
    else:
        node.set("name", sg.path.name)
        _strip_snapshots(node)
        for old in node.findall("Features") + node.findall("Primers"):
            node.remove(old)
    node.insert(0, sgxml.features_element(sg.features))
    _colors_holder(node).append(_strand_colors("Input", used, sg.length, "black"))
    return node


def leaf(name: str, length: int, circular: bool, features: list[Feature]) -> ET.Element:
    """A part with no file behind it, for example typed-in bases."""
    node = _node(name, length, circular, NOT_MADE_HERE)
    node.append(sgxml.features_element(features))
    return node


def made_node(name: str, length: int, circular: bool, operation: str,
              inputs: list[tuple[ET.Element, ET.Element]], new: list[tuple[int, int]],
              features: list[Feature] | None = None, oligos: list[tuple[str, str]] = ()) -> ET.Element:
    """A step that made something: its inputs, each with its InputSummary.

    `new` lists the bases this step made (1-based), drawn red. `features` is
    given for a step in the middle of the tree; the top step has its features
    in the file itself.
    """
    node = _node(name, length, circular, operation)
    if features is not None:
        node.append(sgxml.features_element(features))
    for oligo_name, sequence in oligos:
        ET.SubElement(node, "Oligo", name=oligo_name, sequence=sequence)
    ET.SubElement(node, "HistoryColors").append(_strand_colors("Product", new, length, "red"))
    for _, row in inputs:
        node.append(row)
    for child, _ in inputs:
        node.append(child)
    return node


def mark_used(node: ET.Element, used: list[tuple[int, int]]) -> None:
    """Paint what the next step took from this one."""
    length = int(node.get("seqLen", "0"))
    _colors_holder(node).append(_strand_colors("Input", used, length, "black"))


def _check_steps(top: ET.Element) -> None:
    for step in top.iter("Node"):
        inputs = step.findall("Node")
        rows = step.findall("InputSummary")
        if inputs and len(rows) != len(inputs):
            raise CliError("bad_history",
                           f"The history step '{step.get('name')}' has {len(inputs)} inputs but "
                           f"{len(rows)} input summaries. SnapGene would quit on this file.")


def check(tree: ET.Element, length: int) -> None:
    """Refuse a tree SnapGene would not open."""
    tops = tree.findall("Node")
    if tree.tag != "HistoryTree" or len(tops) != 1:
        raise CliError("bad_history", "A history tree must hold exactly one top step.")
    if tops[0].get("seqLen") != str(length):
        raise CliError("bad_history", f"The history's top step is {tops[0].get('seqLen')} bp, "
                                      f"but the file is {length} bp.")
    _check_steps(tops[0])
    ids = [step.get("ID") for step in tree.iter("Node")]
    if len(ids) != len(set(ids)):
        raise CliError("bad_history", "Two history steps share one ID.")


def tree(top: ET.Element) -> str:
    """The finished tree as text. Steps are numbered inputs first, top last."""
    holder = ET.Element("HistoryTree")
    holder.append(top)
    number = 0

    def walk(node: ET.Element) -> None:
        nonlocal number
        for child in node.findall("Node"):
            walk(child)
        node.set("ID", str(number))
        number += 1

    walk(top)
    check(holder, int(top.get("seqLen", "0")))
    return ET.tostring(holder, encoding="unicode")


def finish(text: str, name: str, length: int) -> bytes:
    """The tree to write into a file called `name`, checked again before it goes out."""
    root = SafeET.fromstring(text.encode("utf-8"))
    check(root, length)
    root.find("Node").set("name", name)
    _strip_snapshots(root)
    return b'<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="utf-8", xml_declaration=False)


def steps(text: str | None) -> list[dict]:
    """The history as a flat list, top step first: name, operation, depth."""
    if not text:
        return []
    root = SafeET.fromstring(text.encode("utf-8"))
    out = []

    def walk(node: ET.Element, depth: int) -> None:
        out.append({"name": node.get("name"), "operation": node.get("operation"), "depth": depth,
                    "length": int(node.get("seqLen", "0")),
                    "oligos": [o.get("name") for o in node.findall("Oligo")]})
        for child in node.findall("Node"):
            walk(child, depth + 1)

    for top in root.findall("Node"):
        walk(top, 0)
    return out
