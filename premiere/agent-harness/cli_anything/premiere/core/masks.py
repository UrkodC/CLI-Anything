"""Read the masks of a saved Premiere 27 project (.prproj). Read-only.

A mask made with the Premiere 27 mask tools (Object, Ellipse, Rectangle, Pen, HSL, Luminance) is stored as a
component with the match name AE.ADBE AEMask2. Its picture is not in the project file: Premiere writes .prmf files
into the folder "<project name> Masks" next to the project. This module lists the masks, their settings, whether
they were tracked, and the mask data files. Measured on format 46 (Premiere 27.0.0) with one Object Mask, before and
after tracking (docs/2026-11-22-premiere27-mask-ui-gates.md). Other mask types are listed with their type number.
"""
from __future__ import annotations

import base64
import gzip
import re
import xml.etree.ElementTree as ET  # types only; parsing goes through defusedxml
from defusedxml import ElementTree as SafeET
from pathlib import Path

from cli_anything.premiere.errors import CliError

MASK_MATCH_NAME = "AE.ADBE AEMask2"
TYPE_NAMES = {1: "ellipse", 4: "object"}        # measured: Ellipse Mask 1, Object Mask 4. Other numbers are not measured yet.
BLEND_NAMES = {0: "add"}                         # measured: the default "Add" is 0.
TRACKED_PARAMS = (4, 5)                          # two unnamed flags that turn true after tracking
_GUID = re.compile(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_NAMED = {"Feather": "feather", "Opacity": "opacity", "Expansion": "expansion", "Blend Mode": "blend_mode", "Type": "type"}


def _root(file: Path) -> ET.Element:
    try:
        with gzip.open(file, "rb") as handle:
            return SafeET.parse(handle).getroot()
    except (OSError, EOFError, ET.ParseError) as exc:
        raise CliError("bad_project", f"Cannot read {file} as a Premiere project: {exc}.") from None


def _start_value(param: ET.Element):
    """The value at the start of a parameter: the second field of StartKeyframe ("ticks,value,...")."""
    text = param.findtext("StartKeyframe")
    if not text or "," not in text:
        return None
    raw = text.split(",")[1]
    if raw in ("true", "false"):
        return raw == "true"
    try:
        number = float(raw)
    except ValueError:
        return raw
    return int(number) if number.is_integer() else number


def _blob(param: ET.Element, by_hash: dict[str, bytes]) -> bytes | None:
    """The binary value of an Arb parameter. A repeated value is stored once and found by its BinaryHash."""
    for elem in param.iter():
        if elem.get("Encoding") == "base64":
            text = (elem.text or "").strip()
            if text:
                return base64.b64decode(text)
            return by_hash.get(elem.get("BinaryHash", ""))
    return None


def list_masks(file: Path) -> dict:
    """All masks of the project, with the clip that carries each one when it can be found."""
    file = Path(file)
    root = _root(file)
    objects = {e.get("ObjectID"): e for e in root if e.get("ObjectID")}
    by_hash: dict[str, bytes] = {}
    for elem in root.iter():
        if elem.get("Encoding") == "base64" and elem.get("BinaryHash") and (elem.text or "").strip():
            by_hash.setdefault(elem.get("BinaryHash"), base64.b64decode(elem.text.strip()))
    referrers: dict[str, list[ET.Element]] = {}
    for top in objects.values():
        for elem in top.iter():
            ref = elem.get("ObjectRef")
            if ref:
                referrers.setdefault(ref, []).append(top)

    def owner(object_id: str) -> tuple[str | None, str | None]:
        """Walk up the references to the first object that has a name: (clip name, name of the holder component)."""
        holder, seen, queue = None, set(), [object_id]
        for _ in range(12):
            nxt = []
            for oid in queue:
                for parent in referrers.get(oid, []):
                    pid = parent.get("ObjectID")
                    if pid in seen:
                        continue
                    seen.add(pid)
                    if holder is None and parent.find(".//MatchName") is not None:
                        holder = parent.findtext(".//MatchName")
                    name = parent.findtext("Name") or parent.findtext(".//MasterClip/Name") or parent.findtext(".//Clip/Name")
                    if name and parent.tag not in ("VideoFilterComponent",):
                        return name, holder
                    nxt.append(pid)
            if not nxt:
                break
            queue = nxt
        return None, holder

    folder = file.with_name(file.stem + " Masks")
    masks = []
    for oid, comp in objects.items():
        if comp.findtext(".//MatchName") != MASK_MATCH_NAME or comp.find(".//InstanceName") is None:
            continue
        entry: dict = {"object_id": int(oid), "instance": comp.findtext(".//InstanceName")}
        values: dict[int, object] = {}
        files: list[str] = []
        for ref in comp.iter("Param"):
            index, param = int(ref.get("Index")), objects.get(ref.get("ObjectRef"))
            if param is None:
                continue
            name = param.findtext("Name") or ""
            values[index] = _start_value(param)
            if name in _NAMED:
                entry[_NAMED[name]] = values[index]
            if name == "Tracker":
                blob = _blob(param, by_hash) or b""
                for guid in _GUID.findall(blob):
                    text = guid.decode()
                    if text not in files:
                        files.append(text)
        entry["type_name"] = TYPE_NAMES.get(entry.get("type"), f"type {entry.get('type')}")
        entry["blend_mode_name"] = BLEND_NAMES.get(entry.get("blend_mode"), f"mode {entry.get('blend_mode')}")
        entry["tracked"] = any(values.get(i) is True for i in TRACKED_PARAMS)
        data = []
        for guid in files:
            path = folder / f"{guid}.prmf"
            if path.is_file():
                data.append({"file": path.name, "bytes": path.stat().st_size})
        entry["data_files"] = data
        entry["data_ids"] = files
        entry["clip"], entry["holder"] = owner(oid)
        masks.append(entry)
    masks.sort(key=lambda m: m["object_id"])
    return {"file": str(file), "mask_folder": str(folder) if folder.is_dir() else None, "masks": masks,
            "note": "Measured on one Object Mask of Premiere 27.0.0. The mask picture is in the .prmf files and is not decoded."}
