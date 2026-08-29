"""_template driver — Click subcommands + PyMOL pipeline runner.

Mirrors the layout of fiji.skills.mt_clip170.driver. Replace stub bodies with
real logic when you turn this into a concrete skill.

Macro argument contract:
  We invoke `pymol -cq pipeline.pml` with an environment carrying:
    PDB         PDB id or path to local .cif/.pdb
    OUT_DIR     absolute path to run directory
    PARAMS      absolute path to params snapshot YAML
    STAGES      e.g. "0-2" or "0" — inclusive range
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import click

try:
    import yaml
except ImportError:
    yaml = None

PKG_DIR = Path(__file__).resolve().parent
PIPELINE_PML = PKG_DIR / "pipeline.pml"
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


def _make_run_dir(pdb: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = pdb.replace("/", "_").replace(".", "_")
    d = DEFAULT_OUT_ROOT / safe / stamp
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_pymol() -> str:
    exe = shutil.which("pymol")
    if not exe:
        raise click.ClickException("pymol not found on PATH")
    return exe


def _run_pymol(pdb: str, out_dir: Path, params_path: Path, stages: str) -> int:
    env = os.environ.copy()
    env.update(
        {
            "PDB": pdb,
            "OUT_DIR": str(out_dir),
            "PARAMS": str(params_path),
            "STAGES": stages,
        }
    )
    cmd = [_find_pymol(), "-cq", str(PIPELINE_PML)]
    click.echo(f"[_template] $ {' '.join(cmd)}")
    t0 = time.monotonic()
    proc = subprocess.run(cmd, env=env)
    click.echo(f"[_template] pymol exited {proc.returncode} in {time.monotonic()-t0:.1f}s")
    return proc.returncode


@click.group(name="_template")
def cli() -> None:
    """Placeholder PyMOL skill. Rename me and fill in real logic."""


@cli.command()
@click.option("--pdb", required=True, help="PDB id or path to local structure")
@click.option("--params", "params_path", default=None, help="Override params.yaml")
def run(pdb: str, params_path: Optional[str]) -> None:
    """Run all stages on a single structure."""
    _require_yaml()
    params = load_params(params_path)
    out_dir = _make_run_dir(pdb)
    snap = out_dir / "params_used.yaml"
    with open(snap, "w") as f:
        yaml.safe_dump(params, f)
    rc = _run_pymol(pdb, out_dir, snap, "0-2")
    if rc != 0:
        sys.exit(rc)


@cli.command()
@click.option("--pdb", required=True)
@click.option("--stages", default="0-2", help='Inclusive range, e.g. "0-1"')
@click.option("--params", "params_path", default=None)
def tune(pdb: str, stages: str, params_path: Optional[str]) -> None:
    """Iterate on a subset of stages (live logs to stdout)."""
    _require_yaml()
    params = load_params(params_path)
    out_dir = _make_run_dir(pdb)
    snap = out_dir / "params_used.yaml"
    with open(snap, "w") as f:
        yaml.safe_dump(params, f)
    rc = _run_pymol(pdb, out_dir, snap, stages)
    if rc != 0:
        sys.exit(rc)


@cli.command()
@click.option("--pdb-list", required=True, type=click.Path(exists=True))
def batch(pdb_list: str) -> None:
    """Run the full pipeline over a newline-separated PDB list."""
    _require_yaml()
    params = load_params()
    with open(pdb_list) as f:
        ids = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    for pdb in ids:
        out_dir = _make_run_dir(pdb)
        snap = out_dir / "params_used.yaml"
        with open(snap, "w") as fh:
            yaml.safe_dump(params, fh)
        rc = _run_pymol(pdb, out_dir, snap, "0-2")
        click.echo(f"[_template] {pdb}: rc={rc}")


@cli.command()
def params() -> None:
    """Print current default parameters."""
    _require_yaml()
    p = load_params()
    click.echo(yaml.safe_dump(p, sort_keys=False))


if __name__ == "__main__":
    cli()
