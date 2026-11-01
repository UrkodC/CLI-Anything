"""The parts of the real SnapGene app this CLI uses.

SnapGene for macOS has no scripting interface. Its own command line (--convert,
--createPreview, --history) needs a licence. The CLI uses:
- the app itself, to open files for the user (`open -a SnapGene`);
- the aligners SnapGene ships in SnapGene.app/Contents/Resources/Tools
  (Clustal Omega and MUSCLE), the same programs SnapGene runs for its alignments.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path

from cli_anything.snapgene.errors import CliError

DEFAULT_APP = Path("/Applications/SnapGene.app")
TOOLS = {
    "clustalo": Path("Contents/Resources/Tools/clustalOmega/clustalo"),
    "muscle": Path("Contents/Resources/Tools/muscle/muscle"),
}
TOOL_TIMEOUT = 600


def find_app() -> Path:
    app = Path(os.environ.get("SNAPGENE_APP", DEFAULT_APP))
    if not (app / "Contents/MacOS/SnapGene").is_file():
        raise CliError(
            "snapgene_missing",
            f"SnapGene was not found at {app}. Install it from https://www.snapgene.com "
            "or set SNAPGENE_APP to the app path.",
        )
    return app


def version(app: Path) -> str:
    with open(app / "Contents/Info.plist", "rb") as fh:
        return plistlib.load(fh).get("CFBundleShortVersionString", "unknown")


def is_running() -> bool:
    result = subprocess.run(["pgrep", "-x", "SnapGene"], capture_output=True, text=True)
    return result.returncode == 0


def tool_path(name: str, app: Path | None = None) -> Path:
    if name not in TOOLS:
        raise CliError("bad_tool", f"Unknown aligner '{name}'. Use: {', '.join(TOOLS)}.")
    path = (app or find_app()) / TOOLS[name]
    if not path.is_file():
        raise CliError("tool_missing", f"This SnapGene install has no {name} at {path}.")
    return path


def status() -> dict:
    app = find_app()
    return {
        "app": str(app),
        "version": version(app),
        "running": is_running(),
        "tools": {name: (app / rel).is_file() for name, rel in TOOLS.items()},
    }


def open_in_app(path: Path) -> dict:
    app = find_app()
    if not path.exists():
        raise CliError("no_file", f"{path} does not exist.")
    result = subprocess.run(["open", "-a", str(app), str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        raise CliError("open_failed", f"SnapGene could not open {path}: {result.stderr.strip()}")
    return {"opened": str(path), "app": str(app)}


def run_aligner(name: str, fasta_in: Path, fasta_out: Path, kind: str) -> None:
    exe = tool_path(name)
    if name == "clustalo":
        seqtype = "Protein" if kind == "protein" else "DNA"
        cmd = [str(exe), "-i", str(fasta_in), "-o", str(fasta_out), "--outfmt=fasta",
               f"--seqtype={seqtype}", "--force"]
    else:
        cmd = [str(exe), "-in", str(fasta_in), "-out", str(fasta_out), "-quiet"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TOOL_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise CliError("tool_timeout", f"{name} did not finish in {TOOL_TIMEOUT} seconds.") from exc
    if result.returncode != 0 or not fasta_out.is_file():
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise CliError("tool_error", f"{name} failed: {detail[-1] if detail else 'no output'}")
