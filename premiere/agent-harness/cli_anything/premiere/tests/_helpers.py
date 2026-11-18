"""Build a small Premiere project XML for unit tests, with the object layout Premiere writes.

Nothing here comes from a real project: the names, paths and ids are made up.
"""
from __future__ import annotations

import base64
import gzip
from pathlib import Path

TICKS = 254016000000
FRAME = 10594584000          # one frame at 23.976 fps
LUT_PATH = "/luts/Example Look.cube/"


def _param(oid: int, pid: int, name: str, value: str, keyframes: str = "") -> str:
    keys = f"\n\t\t<Keyframes>{keyframes}</Keyframes>" if keyframes else ""
    return (f'\t<VideoComponentParam ObjectID="{oid}" ClassID="x" Version="10">\n\t\t<Name>{name}</Name>\n'
            f'\t\t<ParameterID>{pid}</ParameterID>\n\t\t<StartKeyframe>-91445760000000000,{value},0,0,0,0,0,0</StartKeyframe>{keys}\n'
            f'\t</VideoComponentParam>\n')


def _arb(oid: int, pid: int, name: str, raw: bytes, binary_hash: str = "", data: bool = True) -> str:
    """A binary parameter. Premiere writes the data once and repeats only the BinaryHash after that (data=False)."""
    attr = f' BinaryHash="{binary_hash}"' if binary_hash else ""
    value = (f'<StartKeyframeValue Encoding="base64"{attr}>{base64.b64encode(raw).decode()}</StartKeyframeValue>' if data
             else f'<StartKeyframeValue Encoding="base64"{attr}/>')
    return (f'\t<ArbVideoComponentParam ObjectID="{oid}" ClassID="x" Version="3">\n\t\t<Name>{name}</Name>\n\t\t<ParameterID>{pid}</ParameterID>\n'
            f'\t\t{value}\n\t</ArbVideoComponentParam>\n')


def _component(oid: int, display: str, match: str, param_ids: list[int], intrinsic: bool = False, bypass: bool = False) -> str:
    params = "".join(f'\t\t\t\t<Param Index="{i}" ObjectRef="{p}"/>\n' for i, p in enumerate(param_ids))
    return (f'\t<VideoFilterComponent ObjectID="{oid}" ClassID="x" Version="9">\n\t\t<Component Version="7">\n\t\t\t<Params Version="1">\n{params}'
            f'\t\t\t</Params>\n\t\t\t<Bypass>{"true" if bypass else "false"}</Bypass>\n\t\t\t<DisplayName>{display}</DisplayName>\n'
            f'\t\t\t<Intrinsic>{"true" if intrinsic else "false"}</Intrinsic>\n\t\t</Component>\n\t\t<MatchName>{match}</MatchName>\n\t</VideoFilterComponent>\n')


def project_xml(project_version: int = 46, with_color_mode: bool = True, two_sequences: bool = False) -> str:
    """One sequence 'Main', one video track, two clips. Clip 0: Motion, Lumetri, ProcAmp. Clip 1: Motion (+ Color Mode)."""
    lut_raw = LUT_PATH.encode("utf-16-le")
    blob = bytes(range(1, 40))
    parts = [
        '<?xml version="1.0" encoding="UTF-8" ?>\n<PremiereData Version="3">\n\t<Project ObjectRef="1"/>\n',
        f'\t<Project ObjectID="1" ClassID="x" Version="{project_version}">\n\t</Project>\n',
        # sequence -> track groups
        '\t<Sequence ObjectUID="seq-main" ClassID="x" Version="11">\n\t\t<TrackGroups Version="1">\n'
        '\t\t\t<TrackGroup Version="1" Index="0">\n\t\t\t\t<First>v</First>\n\t\t\t\t<Second ObjectRef="10"/>\n\t\t\t</TrackGroup>\n'
        '\t\t\t<TrackGroup Version="1" Index="1">\n\t\t\t\t<First>a</First>\n\t\t\t\t<Second ObjectRef="11"/>\n\t\t\t</TrackGroup>\n'
        '\t\t</TrackGroups>\n\t\t<Name>Main</Name>\n\t</Sequence>\n',
        f'\t<VideoTrackGroup ObjectID="10" ClassID="x" Version="13">\n\t\t<TrackGroup Version="1">\n\t\t\t<Tracks Version="1">\n'
        f'\t\t\t\t<Track Index="0" ObjectURef="vtrack-1"/>\n\t\t\t</Tracks>\n\t\t\t<FrameRate>{FRAME}</FrameRate>\n\t\t</TrackGroup>\n'
        f'\t\t<FrameRect>0,0,3840,2160</FrameRect>\n\t</VideoTrackGroup>\n',
        '\t<AudioTrackGroup ObjectID="11" ClassID="x" Version="13">\n\t\t<TrackGroup Version="1">\n\t\t\t<Tracks Version="1">\n'
        '\t\t\t\t<Track Index="0" ObjectURef="atrack-1"/>\n\t\t\t</Tracks>\n\t\t</TrackGroup>\n\t</AudioTrackGroup>\n',
        '\t<VideoClipTrack ObjectUID="vtrack-1" ClassID="x" Version="1">\n\t\t<ClipTrack Version="2">\n\t\t\t<Track Version="3">\n'
        '\t\t\t\t<IsLocked>false</IsLocked>\n\t\t\t\t<Index>0</Index>\n\t\t\t\t<IsMuted>false</IsMuted>\n\t\t\t</Track>\n'
        '\t\t\t<ClipItems Version="3">\n\t\t\t\t<TrackItems Version="1">\n\t\t\t\t\t<TrackItem Index="0" ObjectRef="20"/>\n'
        '\t\t\t\t\t<TrackItem Index="1" ObjectRef="21"/>\n\t\t\t\t</TrackItems>\n\t\t\t</ClipItems>\n\t\t</ClipTrack>\n\t</VideoClipTrack>\n',
        '\t<AudioClipTrack ObjectUID="atrack-1" ClassID="x" Version="1">\n\t\t<ClipTrack Version="2">\n\t\t\t<Track Version="3">\n'
        '\t\t\t\t<IsLocked>false</IsLocked>\n\t\t\t\t<IsMuted>true</IsMuted>\n\t\t\t</Track>\n\t\t\t<ClipItems Version="3">\n'
        '\t\t\t\t<TrackItems Version="1">\n\t\t\t\t</TrackItems>\n\t\t\t</ClipItems>\n\t\t</ClipTrack>\n\t</AudioClipTrack>\n',
    ]
    for oid, start, end, chain, sub in ((20, 0, 96 * FRAME, 30, 40), (21, 96 * FRAME, 144 * FRAME, 31, 41)):
        parts.append(f'\t<VideoClipTrackItem ObjectID="{oid}" ClassID="x" Version="6">\n\t\t<ClipTrackItem Version="8">\n'
                     f'\t\t\t<ComponentOwner Version="1">\n\t\t\t\t<Components ObjectRef="{chain}"/>\n\t\t\t</ComponentOwner>\n'
                     f'\t\t\t<TrackItem Version="3">\n\t\t\t\t<Start>{start}</Start>\n\t\t\t\t<End>{end}</End>\n\t\t\t</TrackItem>\n'
                     f'\t\t\t<SubClip ObjectRef="{sub}"/>\n\t\t</ClipTrackItem>\n\t</VideoClipTrackItem>\n')
    parts += [
        '\t<SubClip ObjectID="40" ClassID="x" Version="5">\n\t\t<Clip ObjectRef="50"/>\n\t\t<Name>shot_a.mp4</Name>\n\t</SubClip>\n',
        '\t<SubClip ObjectID="41" ClassID="x" Version="5">\n\t\t<Clip ObjectRef="51"/>\n\t\t<Name>shot_b.mp4</Name>\n\t</SubClip>\n',
        f'\t<VideoClip ObjectID="50" ClassID="x" Version="11">\n\t\t<Clip Version="18">\n\t\t\t<Source ObjectRef="60"/>\n'
        f'\t\t\t<InPoint>{24 * FRAME}</InPoint>\n\t\t\t<OutPoint>{120 * FRAME}</OutPoint>\n\t\t</Clip>\n\t</VideoClip>\n',
        '\t<VideoClip ObjectID="51" ClassID="x" Version="11">\n\t\t<Clip Version="18">\n\t\t\t<Source ObjectRef="61"/>\n\t\t</Clip>\n\t</VideoClip>\n',
        '\t<VideoMediaSource ObjectID="60" ClassID="x" Version="2">\n\t\t<MediaSource Version="4">\n\t\t\t<Media ObjectURef="media-a"/>\n\t\t</MediaSource>\n\t</VideoMediaSource>\n',
        '\t<VideoMediaSource ObjectID="61" ClassID="x" Version="2">\n\t\t<MediaSource Version="4">\n\t\t\t<Media ObjectURef="media-b"/>\n\t\t</MediaSource>\n\t</VideoMediaSource>\n',
        '\t<Media ObjectUID="media-a" ClassID="x" Version="28">\n\t\t<FilePath>/footage/shot_a.mp4</FilePath>\n\t\t<ActualMediaFilePath>/footage/shot_a.mp4</ActualMediaFilePath>\n\t</Media>\n',
        '\t<Media ObjectUID="media-b" ClassID="x" Version="28">\n\t\t<FilePath>/footage/shot_b.mp4</FilePath>\n\t</Media>\n',
        # clip 0 chain, in FILE order: Motion, Lumetri Color, ProcAmp (ProcAmp was applied first, so it renders first)
        '\t<VideoComponentChain ObjectID="30" ClassID="x" Version="3">\n\t\t<ComponentChain Version="3">\n\t\t\t<Components Version="1">\n'
        '\t\t\t\t<Component Index="0" ObjectRef="70"/>\n\t\t\t\t<Component Index="1" ObjectRef="71"/>\n\t\t\t\t<Component Index="2" ObjectRef="72"/>\n'
        '\t\t\t</Components>\n\t\t</ComponentChain>\n\t</VideoComponentChain>\n',
        _component(70, "Motion", "AE.ADBE Motion", [100, 101], intrinsic=True),
        '\t<PointComponentParam ObjectID="100" ClassID="x" Version="3">\n\t\t<Name>Position</Name>\n\t\t<ParameterID>1</ParameterID>\n'
        '\t\t<StartKeyframe>-91445760000000000,0.5:0.5,0,0,0,0,0,0,5,4,0,0,0,0</StartKeyframe>\n\t</PointComponentParam>\n',
        _param(101, 5, "Rotation", "0.", f"{24 * FRAME},1.5,0,0,0,0,0,0;{72 * FRAME},-0.5,0,0,0,0,0,0;"),
        _component(71, "Lumetri Color", "AE.ADBE Lumetri", [110, 111, 112, 113, 114] + [115] * 9 + [116, 117]),
        _arb(110, 0, "Blob", blob), _param(111, 1, "High Dynamic Range", "false"), _param(112, 2, "Basic Correction", "false"),
        _param(113, 3, "Active", "true"), _arb(114, 4, " ", lut_raw, binary_hash="hash-lut"), _param(115, 9, "", "0"),
        _param(116, 7, "Temperature", "-46.5"), _param(117, 8, "Tint", "8.8000000000000007"),
        _component(72, "ProcAmp", "AE.ADBE ProcAmp", [120], bypass=True),
        _param(120, 1, "Brightness", "7.699999809265"),
    ]
    clip1 = ('\t\t\t\t<Component Index="0" ObjectRef="73"/>\n' + ('\t\t\t\t<Component Index="1" ObjectRef="74"/>\n' if with_color_mode else "")
             + '\t\t\t\t<Component Index="2" ObjectRef="75"/>\n')
    parts += [
        '\t<VideoComponentChain ObjectID="31" ClassID="x" Version="3">\n\t\t<ComponentChain Version="3">\n\t\t\t<Components Version="1">\n'
        + clip1 + '\t\t\t</Components>\n\t\t</ComponentChain>\n\t</VideoComponentChain>\n',
        _component(73, "Motion", "AE.ADBE Motion", [], intrinsic=True),
    ]
    if with_color_mode:
        parts.append(_component(74, "Color Mode Effect", "AE.ADBE CompoundComponent", []))
    # clip 1 has a second Lumetri with the SAME LUT: the file repeats only the hash, with no data
    parts += [_component(75, "Lumetri Color", "AE.ADBE Lumetri", [110, 111, 112, 113, 130] + [115] * 9 + [116, 117]),
              _arb(130, 4, " ", b"", binary_hash="hash-lut", data=False)]
    if two_sequences:
        parts.append('\t<Sequence ObjectUID="seq-other" ClassID="x" Version="11">\n\t\t<TrackGroups Version="1">\n\t\t</TrackGroups>\n\t\t<Name>Other</Name>\n\t</Sequence>\n')
    parts.append("</PremiereData>\n")
    return "".join(parts)


def write_project(path: Path, compressed: bool = True, **kwargs) -> Path:
    data = project_xml(**kwargs).encode("utf-8")
    path.write_bytes(gzip.compress(data) if compressed else data)
    return path
