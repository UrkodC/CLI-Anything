"""Read-only reader for Adobe Premiere Pro project files (.prproj).

A .prproj file is gzip-compressed XML. Every object sits directly under the root
<PremiereData> and carries either ObjectID (a number, local to the file) or
ObjectUID (a GUID). Other objects point at it with ObjectRef or ObjectURef.

This module never writes. It opens the file in binary read mode only.
"""
from __future__ import annotations

import base64
import gzip
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from defusedxml import ElementTree as SafeET

from cli_anything.premiere.errors import CliError

TICKS_PER_SECOND = 254016000000
# Project format versions. TESTED: every value was cross-checked against the app that wrote the file.
# SMOKE_READ: real files of this format were read with every command and gave no error, but the
# values were not compared with Premiere.
TESTED_PROJECT_VERSIONS = {46: "Premiere Pro 27.0.0"}
SMOKE_READ_PROJECT_VERSIONS = {38: "Premiere Pro (older)", 40: "Premiere Pro 2022", 41: "Premiere Pro (template projects shipped with 27.0.0)",
                               42: "Premiere Pro 2025", 45: "Premiere Pro 26.5.2"}
MAX_XML_BYTES = 1_500_000_000
COLOR_MODE_MATCH_NAME = "AE.ADBE CompoundComponent"
# Raw byte markers of a Color Mode grade. Counted over the whole file as a safety net, because the
# place of a Color Mode grade in the file was never checked on a real Color Mode project.
COLOR_MODE_MARKERS = (b"CompoundComponent", b"Color Mode Effect")
LUMETRI_MATCH_NAME = "AE.ADBE Lumetri"
PARAM_TAGS = ("VideoComponentParam", "PointComponentParam", "ArbVideoComponentParam", "AudioComponentParam")


def ticks_to_seconds(ticks: int | None) -> float | None:
    return None if ticks is None else ticks / TICKS_PER_SECOND


def _int(text: str | None) -> int | None:
    try:
        return int(text) if text not in (None, "") else None
    except ValueError:
        return None


def _text(elem, path: str) -> str | None:
    found = elem.find(path) if elem is not None else None
    return found.text if found is not None else None


def _value(raw: str | None) -> Any:
    """A parameter value as Premiere writes it: number, 'x:y' point, true/false, or text."""
    if raw is None:
        return None
    raw = raw.strip()
    if raw in ("true", "false"):
        return raw == "true"
    if ":" in raw:
        parts = raw.split(":")
        try:
            return [float(p) for p in parts]
        except ValueError:
            return raw
    try:
        number = float(raw)
    except ValueError:
        return raw
    return int(number) if number.is_integer() and "." not in raw and "e" not in raw.lower() else number


@dataclass
class Param:
    index: int
    parameter_id: int | None
    name: str
    kind: str                      # number, point, opaque
    value: Any = None              # the static value (the start keyframe value)
    keyframes: list[dict] = field(default_factory=list)   # media_ticks, media_seconds, value
    opaque_bytes: int | None = None
    text: str | None = None        # decoded text of an opaque parameter, when it is text

    def as_dict(self) -> dict:
        out: dict[str, Any] = {"index": self.index, "parameter_id": self.parameter_id, "name": self.name, "kind": self.kind}
        if self.kind == "opaque":
            out["opaque_bytes"] = self.opaque_bytes
            if self.text is not None:
                out["text"] = self.text
        else:
            out["value"] = self.value
        if self.keyframes:
            out["keyframes"] = self.keyframes
        return out


@dataclass
class Effect:
    file_index: int
    display_name: str
    match_name: str
    intrinsic: bool
    bypass: bool
    params: list[Param]
    render_position: int | None = None    # 1 = renders first among the applied effects
    color_mode: bool = False
    raw_summary: dict | None = None

    def as_dict(self, with_params: bool = True) -> dict:
        out: dict[str, Any] = {
            "file_index": self.file_index, "display_name": self.display_name, "match_name": self.match_name,
            "intrinsic": self.intrinsic, "bypass": self.bypass, "render_position": self.render_position,
        }
        if self.color_mode:
            out["color_mode"] = True
            out["raw_summary"] = self.raw_summary
        if with_params:
            out["params"] = [p.as_dict() for p in self.params]
        return out

    def param(self, name: str | None = None, index: int | None = None) -> Param | None:
        for p in self.params:
            if (index is not None and p.index == index) or (name is not None and index is None and p.name == name):
                return p
        return None


@dataclass
class ClipItem:
    index: int
    name: str
    start_ticks: int
    end_ticks: int
    in_ticks: int | None
    out_ticks: int | None
    media_path: str | None
    effects: list[Effect]

    def as_dict(self, with_effects: bool = False) -> dict:
        out = {
            "index": self.index, "name": self.name,
            "start_seconds": ticks_to_seconds(self.start_ticks), "end_seconds": ticks_to_seconds(self.end_ticks),
            "in_seconds": ticks_to_seconds(self.in_ticks), "out_seconds": ticks_to_seconds(self.out_ticks),
            "start_ticks": self.start_ticks, "end_ticks": self.end_ticks, "in_ticks": self.in_ticks, "out_ticks": self.out_ticks,
            "media_path": self.media_path,
            "effects": [e.display_name for e in self.effects],
            "has_color_mode": any(e.color_mode for e in self.effects),
        }
        if with_effects:
            out["effect_details"] = [e.as_dict() for e in self.effects]
        return out


@dataclass
class Track:
    kind: str          # video or audio
    index: int
    muted: bool | None
    locked: bool | None
    clips: list[ClipItem]


@dataclass
class Sequence:
    uid: str
    name: str
    width: int | None
    height: int | None
    frame_ticks: int | None
    tracks: list[Track]

    @property
    def fps(self) -> float | None:
        return TICKS_PER_SECOND / self.frame_ticks if self.frame_ticks else None

    @property
    def end_ticks(self) -> int:
        return max((c.end_ticks for t in self.tracks for c in t.clips), default=0)

    def as_dict(self) -> dict:
        return {
            "id": self.uid, "name": self.name, "width": self.width, "height": self.height,
            "fps": round(self.fps, 6) if self.fps else None, "frame_ticks": self.frame_ticks,
            "duration_seconds": ticks_to_seconds(self.end_ticks),
            "video_tracks": sum(1 for t in self.tracks if t.kind == "video"),
            "audio_tracks": sum(1 for t in self.tracks if t.kind == "audio"),
            "clips": sum(len(t.clips) for t in self.tracks),
        }


class Project:
    """One parsed project file. Build it with `Project.load(path)`."""

    def __init__(self, path: Path, root, compressed: bool, color_mode_markers: int = 0) -> None:
        self.path = path
        self.root = root
        self.compressed = compressed
        self.color_mode_markers = color_mode_markers
        self.by_id: dict[str, Any] = {}
        self.by_uid: dict[str, Any] = {}
        for elem in root:
            oid, uid = elem.get("ObjectID"), elem.get("ObjectUID")
            if oid is not None:
                self.by_id[oid] = elem
            if uid is not None:
                self.by_uid[uid] = elem
        # Premiere writes a binary value once. Every later copy of the same value is an empty
        # element that carries only the same BinaryHash. Collect the data by hash to resolve them.
        self.binary_by_hash: dict[str, str] = {}
        for node in root.iter("StartKeyframeValue"):
            key = node.get("BinaryHash")
            if key and node.text and node.text.strip():
                self.binary_by_hash.setdefault(key, node.text)

    # ---------- loading ----------
    @classmethod
    def load(cls, path: str | os.PathLike) -> "Project":
        p = Path(path)
        if not p.is_file():
            raise CliError("file_not_found", f"No project file at {p}.")
        with open(p, "rb") as fh:          # read-only: this tool never writes a project
            head = fh.read(2)
            fh.seek(0)
            compressed = head == b"\x1f\x8b"
            try:
                data = gzip.GzipFile(fileobj=fh).read(MAX_XML_BYTES + 1) if compressed else fh.read(MAX_XML_BYTES + 1)
            except (OSError, EOFError) as exc:
                raise CliError("bad_file", f"{p.name} could not be unpacked: {exc}") from exc
        if len(data) > MAX_XML_BYTES:
            raise CliError("too_large", f"{p.name} unpacks to more than {MAX_XML_BYTES} bytes.")
        try:
            root = SafeET.fromstring(data)
        except Exception as exc:   # defusedxml raises several types; all mean "not a project we can read"
            raise CliError("bad_xml", f"{p.name} is not readable project XML: {exc}") from exc
        if root.tag != "PremiereData":
            raise CliError("not_a_project", f"{p.name} is XML, but its root is <{root.tag}>, not <PremiereData>.")
        return cls(p, root, compressed, color_mode_markers=sum(data.count(m) for m in COLOR_MODE_MARKERS))

    # ---------- references ----------
    def deref(self, elem):
        """The object a reference element points at, or None."""
        if elem is None:
            return None
        ref, uref = elem.get("ObjectRef"), elem.get("ObjectURef")
        if ref is not None:
            return self.by_id.get(ref)
        if uref is not None:
            return self.by_uid.get(uref)
        return None

    # ---------- project level ----------
    def info(self) -> dict:
        project = next((e for e in self.root if e.tag == "Project" and e.get("ObjectID") is not None), None)
        version = _int(project.get("Version")) if project is not None else None
        stat = self.path.stat()
        sequences = self.sequences()
        media = [e for e in self.root if e.tag == "Media"]
        warning = None
        if version in SMOKE_READ_PROJECT_VERSIONS:
            warning = (f"Project format version {version} was only smoke-read (no errors, values not compared with "
                       f"Premiere). Fully tested: {sorted(TESTED_PROJECT_VERSIONS)}. Check the output against Premiere.")
        elif version not in TESTED_PROJECT_VERSIONS:
            warning = (f"Project format version {version} was not tested with this reader "
                       f"(tested: {sorted(TESTED_PROJECT_VERSIONS)}). Check the output against Premiere.")
        linked = sum(1 for s in sequences for t in s.tracks for c in t.clips if any(e.color_mode for e in c.effects))
        return {
            "path": str(self.path), "compressed": self.compressed, "size_bytes": stat.st_size,
            "saved": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds"),
            "premiere_data_version": _int(self.root.get("Version")), "project_format_version": version,
            "written_by": TESTED_PROJECT_VERSIONS.get(version) or SMOKE_READ_PROJECT_VERSIONS.get(version), "format_warning": warning,
            "objects": len(self.by_id) + len(self.by_uid),
            "sequences": len(sequences), "media_files": len(media),
            "clips_with_color_mode": linked,
            "color_mode_markers": self.color_mode_markers,
            "color_mode_note": color_mode_note(linked, self.color_mode_markers),
        }

    def media_paths(self) -> list[str]:
        out = []
        for e in self.root:
            if e.tag == "Media":
                path = _text(e, "ActualMediaFilePath") or _text(e, "FilePath")
                if path:
                    out.append(path)
        return out

    # ---------- sequences ----------
    def sequences(self) -> list[Sequence]:
        return [self._sequence(e) for e in self.root if e.tag == "Sequence" and e.get("ObjectUID") is not None]

    def find_sequence(self, key: str | None) -> Sequence:
        seqs = self.sequences()
        if not seqs:
            raise CliError("no_sequence", "This project has no sequence.")
        if key is None:
            if len(seqs) == 1:
                return seqs[0]
            names = ", ".join(repr(s.name) for s in seqs)
            raise CliError("sequence_needed", f"The project has {len(seqs)} sequences. Pass --sequence with one of: {names}.")
        hits = [s for s in seqs if s.uid == key or s.name == key]
        if not hits:
            hits = [s for s in seqs if s.name.lower() == key.lower()]
        if len(hits) != 1:
            names = ", ".join(repr(s.name) for s in seqs)
            raise CliError("sequence_not_found", f"No single sequence matches {key!r}. Sequences: {names}.")
        return hits[0]

    def _sequence(self, elem) -> Sequence:
        tracks: list[Track] = []
        width = height = frame_ticks = None
        for tg in elem.findall("TrackGroups/TrackGroup"):
            group = self.deref(tg.find("Second"))
            if group is None or group.tag not in ("VideoTrackGroup", "AudioTrackGroup"):
                continue
            kind = "video" if group.tag == "VideoTrackGroup" else "audio"
            if kind == "video":
                frame_ticks = _int(_text(group, "TrackGroup/FrameRate"))
                rect = (_text(group, "FrameRect") or "").split(",")
                if len(rect) == 4:
                    width, height = _int(rect[2]), _int(rect[3])
            for ref in group.findall("TrackGroup/Tracks/Track"):
                track = self.deref(ref)
                if track is None:
                    continue
                tracks.append(Track(
                    kind=kind, index=_int(ref.get("Index")) or 0,
                    muted=(_text(track, "ClipTrack/Track/IsMuted") == "true") if _text(track, "ClipTrack/Track/IsMuted") else None,
                    locked=(_text(track, "ClipTrack/Track/IsLocked") == "true") if _text(track, "ClipTrack/Track/IsLocked") else None,
                    clips=list(self._clips(track)),
                ))
        return Sequence(uid=elem.get("ObjectUID"), name=_text(elem, "Name") or "", width=width, height=height,
                        frame_ticks=frame_ticks, tracks=tracks)

    def _clips(self, track) -> Iterator[ClipItem]:
        for ref in track.findall("ClipTrack/ClipItems/TrackItems/TrackItem"):
            item = self.deref(ref)
            if item is None:
                continue
            sub = self.deref(item.find("ClipTrackItem/SubClip"))
            clip = self.deref(sub.find("Clip")) if sub is not None else None
            source = self.deref(clip.find("Clip/Source")) if clip is not None else None
            media = self.deref(source.find("MediaSource/Media")) if source is not None else None
            chain = self.deref(item.find("ClipTrackItem/ComponentOwner/Components"))
            yield ClipItem(
                index=_int(ref.get("Index")) or 0,
                name=(_text(sub, "Name") if sub is not None else None) or "",
                start_ticks=_int(_text(item, "ClipTrackItem/TrackItem/Start")) or 0,
                end_ticks=_int(_text(item, "ClipTrackItem/TrackItem/End")) or 0,
                in_ticks=_int(_text(clip, "Clip/InPoint")) if clip is not None else None,
                out_ticks=_int(_text(clip, "Clip/OutPoint")) if clip is not None else None,
                media_path=(_text(media, "ActualMediaFilePath") or _text(media, "FilePath")) if media is not None else None,
                effects=self._effects(chain),
            )

    # ---------- effects ----------
    def _effects(self, chain) -> list[Effect]:
        if chain is None:
            return []
        effects: list[Effect] = []
        for ref in chain.findall("ComponentChain/Components/Component"):
            comp = self.deref(ref)
            if comp is None:
                continue
            match = _text(comp, "MatchName") or ""
            effect = Effect(
                file_index=_int(ref.get("Index")) or 0,
                display_name=_text(comp, "Component/DisplayName") or "",
                match_name=match,
                intrinsic=_text(comp, "Component/Intrinsic") == "true",
                bypass=_text(comp, "Component/Bypass") == "true",
                params=[self._param(p) for p in comp.findall("Component/Params/Param")],
                color_mode=match == COLOR_MODE_MATCH_NAME,
            )
            if effect.color_mode:
                effect.raw_summary = self._raw_summary(comp)
            effects.append(effect)
        # Measured on Premiere 27.0.0 with pixel tests on scripted stacks: the applied effects
        # render in the REVERSE of their order in the file (the last one in the file renders first).
        applied = [e for e in effects if not e.intrinsic]
        for position, e in enumerate(reversed(applied), start=1):
            e.render_position = position
        return effects

    def _param(self, ref) -> Param:
        index = _int(ref.get("Index")) or 0
        elem = self.deref(ref)
        if elem is None:
            return Param(index=index, parameter_id=None, name="", kind="opaque", opaque_bytes=0)
        name = _text(elem, "Name") or ""
        pid = _int(_text(elem, "ParameterID"))
        binary = elem.find("StartKeyframeValue")
        if elem.tag == "ArbVideoComponentParam" or binary is not None:
            raw = b""
            encoded = None
            if binary is not None:
                encoded = binary.text if (binary.text and binary.text.strip()) else self.binary_by_hash.get(binary.get("BinaryHash") or "")
            if encoded:
                try:
                    raw = base64.b64decode("".join(encoded.split()))
                except ValueError:
                    raw = b""
            return Param(index=index, parameter_id=pid, name=name, kind="opaque", opaque_bytes=len(raw), text=_utf16_text(raw))
        start = _text(elem, "StartKeyframe")
        value = _value(start.split(",")[1]) if start and "," in start else _value(_text(elem, "CurrentValue"))
        keys = []
        for part in (_text(elem, "Keyframes") or "").split(";"):
            fields = part.split(",")
            if len(fields) >= 2 and _int(fields[0]) is not None:
                keys.append({"media_ticks": int(fields[0]), "media_seconds": ticks_to_seconds(int(fields[0])), "value": _value(fields[1])})
        kind = "point" if isinstance(value, list) else "number"
        return Param(index=index, parameter_id=pid, name=name, kind=kind, value=value, keyframes=keys)

    def _raw_summary(self, comp) -> dict:
        """What can be said about a component whose layout this reader does not decode."""
        tags: dict[str, int] = {}
        refs = []
        for node in comp.iter():
            tags[node.tag] = tags.get(node.tag, 0) + 1
            target = self.deref(node) if (node.get("ObjectRef") or node.get("ObjectURef")) else None
            if target is not None and node is not comp:
                refs.append({"via": node.tag, "object": target.tag,
                             "name": _text(target, "Name") or _text(target, "Component/DisplayName") or _text(target, "MatchName")})
        return {"decoded": False, "note": "Color Mode layout is not decoded yet (no sample project was available).",
                "element_counts": tags, "references": refs[:200]}


def color_mode_note(linked_clips: int, markers: int) -> str:
    """An honest sentence about Color Mode: detection was never checked on a real Color Mode project."""
    if linked_clips:
        return (f"{linked_clips} clip(s) carry a Color Mode grade. Its operations and masks are not decoded yet.")
    if markers:
        return (f"The file holds {markers} Color Mode marker(s), but no timeline clip is linked to one. The file has "
                "Color Mode data that this reader does not find on a clip: do not trust 'no grade' here.")
    return ("No Color Mode marker in the file. Detection was written from the script API's names and was "
            "never checked on a real Color Mode project.")


def _utf16_text(raw: bytes) -> str | None:
    """Premiere stores some string parameters (the Lumetri LUT paths) as UTF-16LE. Return the text, or None."""
    if len(raw) < 2 or len(raw) % 2:
        return None
    try:
        text = raw.decode("utf-16-le").rstrip("\x00")
    except UnicodeDecodeError:
        return None
    if not text or any(ord(ch) < 32 and ch not in "\t\n\r" for ch in text):
        return None
    printable = sum(1 for ch in text if ch.isprintable())
    return text if printable / len(text) > 0.95 else None


def lumetri_summary(effect: Effect) -> dict | None:
    """The parts of a Lumetri Color effect that scripts care about, read from the file."""
    if effect.match_name != LUMETRI_MATCH_NAME:
        return None
    # The layout is the one of Premiere 27.0.0 (also 26.5.2 through the script API). A value is
    # returned only when the parameter at that index has the expected name; if another format moved
    # the parameters, the field is None instead of a wrong number.
    def num(index: int, name: str):
        p = effect.param(index=index)
        return p.value if p is not None and p.kind == "number" and p.name == name else None
    def path(index: int):
        p = effect.param(index=index)
        if p is None or p.kind != "opaque" or p.name.strip():
            return None
        return (p.text or "").rstrip("/") or None
    names = {14: "Temperature", 15: "Tint", 16: "Saturation", 19: "Exposure", 20: "Contrast",
             21: "Highlights", 22: "Shadows", 23: "Whites", 24: "Blacks", 42: "Vibrance"}
    recognized = all((effect.param(index=i) is not None and effect.param(index=i).name == n) for i, n in ((14, "Temperature"), (15, "Tint")))
    out = {"layout_recognized": recognized, "input_lut_path": path(4), "look_path": path(32)}
    out.update({n.lower(): num(i, n) for i, n in names.items()})
    return out
