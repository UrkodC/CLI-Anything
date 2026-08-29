"""_template driver — Click subcommands + FreeCAD headless runner.

Mirrors the layout of fiji.skills.mt_clip170.driver. The freecadcmd
subprocess can't depend on pyyaml, so we materialize params.yaml ->
params.json before invoking pipeline.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import click

try:
    import yaml
except ImportError:
    yaml = None

from cli_anything.freecad.utils import freecad_backend

PKG_DIR = Path(__file__).resolve().parent
PIPELINE_PY = PKG_DIR / "pipeline.py"
DEFAULT_PARAMS = PKG_DIR / "params.yaml"
DEFAULT_OUT_ROOT = Path.cwd() / "_template_out"


def _require_yaml() -> None:
    if yaml is None:
        raise click.ClickException(
            "PyYAML is required for this skill. Install with: pip install pyyaml"
        )


def load_params(path: Optional[str] = None) -> dict:
    _require_yaml()
    p = Path(path) if path else DEFAULT_PARAMS
    if not p.exists():
        raise click.ClickException(f"params file not found: {p}")
    with open(p) as f:
        return yaml.safe_load(f)


def _apply_overrides(params: dict, overrides: Tuple[str, ...]) -> dict:
    """Apply ``--set a.b=value`` overrides onto a params dict (in place)."""
    for spec in overrides:
        if "=" not in spec:
            raise click.ClickException(f"--set expects key=value, got: {spec}")
        key, raw = spec.split("=", 1)
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        cursor = params
        parts = key.split(".")
        for piece in parts[:-1]:
            cursor = cursor.setdefault(piece, {})
        cursor[parts[-1]] = value
    return params


def _make_run_dir(out_root: Optional[Path]) -> Path:
    root = Path(out_root) if out_root else DEFAULT_OUT_ROOT
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = root / stamp
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_freecad(out_dir: Path, params_json: Path, stages: str) -> int:
    exe = freecad_backend.find_freecad()
    bootstrap = (
        f"import os, runpy\n"
        f"os.environ['OUT_DIR'] = {str(out_dir)!r}\n"
        f"os.environ['PARAMS']  = {str(params_json)!r}\n"
        f"os.environ['STAGES']  = {stages!r}\n"
        f"runpy.run_path({str(PIPELINE_PY)!r}, run_name='__main__')\n"
    )
    bootstrap_path = out_dir / "_run.FCMacro"
    bootstrap_path.write_text(bootstrap)
    cmd = [exe, str(bootstrap_path)]
    click.echo(f"[_template] $ {' '.join(cmd)}")
    t0 = time.monotonic()
    proc = subprocess.run(cmd)
    click.echo(f"[_template] freecadcmd exited {proc.returncode} in {time.monotonic()-t0:.1f}s")
    return proc.returncode


@click.group(name="_template")
def cli() -> None:
    """Placeholder FreeCAD skill. Rename me and fill in real logic."""


@cli.command()
@click.option("--out", "out_root", type=click.Path(), default=None,
              help="Output root directory (default: ./_template_out)")
@click.option("--set", "overrides", multiple=True,
              help='Override params, e.g. --set build.length=120')
@click.option("--params", "params_path", default=None,
              help="Override params.yaml")
def run(out_root: Optional[str], overrides: Tuple[str, ...], params_path: Optional[str]) -> None:
    """Run all stages."""
    _require_yaml()
    params = _apply_overrides(load_params(params_path), overrides)
    out_dir = _make_run_dir(Path(out_root) if out_root else None)
    snap_yaml = out_dir / "params_used.yaml"
    snap_json = out_dir / "params_used.json"
    with open(snap_yaml, "w") as f:
        yaml.safe_dump(params, f)
    with open(snap_json, "w") as f:
        json.dump(params, f)
    rc = _run_freecad(out_dir, snap_json, "0-2")
    if rc != 0:
        sys.exit(rc)


@cli.command()
@click.option("--stages", default="0-2")
@click.option("--out", "out_root", type=click.Path(), default=None)
@click.option("--set", "overrides", multiple=True)
def tune(stages: str, out_root: Optional[str], overrides: Tuple[str, ...]) -> None:
    """Run a subset of stages with live logs."""
    _require_yaml()
    params = _apply_overrides(load_params(), overrides)
    out_dir = _make_run_dir(Path(out_root) if out_root else None)
    snap_json = out_dir / "params_used.json"
    with open(snap_json, "w") as f:
        json.dump(params, f)
    rc = _run_freecad(out_dir, snap_json, stages)
    if rc != 0:
        sys.exit(rc)


@cli.command()
def params() -> None:
    """Print current default parameters."""
    _require_yaml()
    click.echo(yaml.safe_dump(load_params(), sort_keys=False))


if __name__ == "__main__":
    cli()
