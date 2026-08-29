"""mt_clip170 driver — Click subcommands + Fiji macro runner with live streaming.

Responsibilities:
  - Load params.yaml
  - Create run directory (timestamped) containing a params snapshot
  - Invoke pipeline.ijm with stage selection via macro args
  - Stream Fiji stdout line-by-line with per-stage timeouts
  - Parse the final cells.csv and print a summary

Macro argument contract:
  Fiji receives a single `-macro pipeline.ijm "<args>"` string. We pass a
  JSON-ish string encoded as `key1=val1;key2=val2;...` which the macro parses
  with `indexOf`/`substring`. This avoids depending on any JSON library in
  macro-land.

  Required keys passed to the macro:
    image        absolute path to .nd2
    out_dir      absolute path to run directory
    params_path  absolute path to the snapshot YAML
    stages       e.g. "0-8" or "0-3" — inclusive range
"""

from __future__ import annotations

import json
import os
import shlex
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
    yaml = None  # driver will error gracefully when needed

from cli_anything.fiji.utils import fiji_backend
from cli_anything.fiji.skills.mt_clip170 import qc as qc_mod

PKG_DIR = Path(__file__).resolve().parent
PIPELINE_IJM = PKG_DIR / "pipeline.ijm"
DEFAULT_PARAMS = PKG_DIR / "params.yaml"


# ─── parameter I/O ──────────────────────────────────────────────────

def _require_yaml():
    if yaml is None:
        raise click.ClickException(
            "PyYAML is required for mt-clip170. Install with: pip install pyyaml"
        )


def load_params(path: Optional[str] = None) -> dict:
    _require_yaml()
    p = Path(path) if path else DEFAULT_PARAMS
    if not p.exists():
        raise click.ClickException(f"params file not found: {p}")
    with open(p) as f:
        return yaml.safe_load(f)


def _flatten(d: dict, prefix: str = "") -> dict:
    """Flatten nested dict into dotted keys. Leaves are scalars or None."""
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def snapshot_params(params: dict, run_dir: Path) -> Path:
    _require_yaml()
    out = run_dir / "params_used.yaml"
    with open(out, "w") as f:
        yaml.safe_dump(params, f, sort_keys=False)
    return out


# ─── run directory layout ───────────────────────────────────────────

def make_run_dir(image_path: str, base: Optional[str] = None) -> Path:
    img = Path(image_path)
    base_dir = Path(base) if base else Path.cwd() / "mt_clip170_out"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = base_dir / img.stem / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


# ─── macro arg encoding ─────────────────────────────────────────────

def encode_args(**kwargs) -> str:
    """Encode a dict into the macro arg string format: k1=v1;k2=v2."""
    parts = []
    for k, v in kwargs.items():
        if v is None:
            v = ""
        parts.append(f"{k}={v}")
    return ";".join(parts)


# ─── Fiji invocation with live streaming ────────────────────────────

def run_macro_streamed(macro_path: Path, arg_string: str, timeout: int,
                       log_path: Optional[Path] = None,
                       verbose: bool = True) -> int:
    """Run a Fiji macro with live stdout streaming and a timeout.

    Returns the subprocess exit code. Raises RuntimeError on timeout.
    """
    fiji = fiji_backend.find_fiji()
    cmd = [fiji, "--headless", "-macro", str(macro_path), arg_string]
    if verbose:
        click.echo(f"  $ {fiji} --headless -macro {macro_path.name} {shlex.quote(arg_string)}")

    start = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=os.environ.copy(),
    )

    log_fh = open(log_path, "w") if log_path else None
    macro_error = False
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                proc.kill()
                raise RuntimeError(
                    f"Fiji macro timed out after {timeout}s (log: {log_path})"
                )
            if log_fh:
                log_fh.write(line)
                log_fh.flush()
            # Fiji exits 0 even on macro errors — detect them in stdout.
            if ("Macro Error" in line or "Macro error" in line
                    or line.lstrip().startswith("FATAL:")):
                macro_error = True
            if verbose:
                stripped = line.rstrip("\n")
                if "Parsing block" in stripped:
                    continue
                click.echo(f"  │ {stripped}")
        proc.wait(timeout=max(1, timeout - (time.monotonic() - start)))
    finally:
        if log_fh:
            log_fh.close()

    if macro_error and proc.returncode == 0:
        return 2  # synthetic non-zero so the driver treats it as a failure
    return proc.returncode


# ─── stage range parsing ────────────────────────────────────────────

def parse_stages(spec: str) -> tuple[int, int]:
    """Parse '0-8' or '3-3' -> (start, end) inclusive."""
    if "-" not in spec:
        n = int(spec)
        return n, n
    a, b = spec.split("-", 1)
    return int(a), int(b)


# ─── Click group ────────────────────────────────────────────────────

@click.group("mt-clip170")
def cli():
    """Interactive MT + GFP-CLIP170 per-cell quantification pipeline."""
    pass


@cli.command("params")
@click.option("--params", "params_path", type=str, default=None,
              help="Path to params.yaml (default: bundled)")
def params_cmd(params_path):
    """Print current parameters."""
    params = load_params(params_path)
    click.echo(yaml.safe_dump(params, sort_keys=False))


@cli.command("tune")
@click.option("--image", "image_path", required=True, type=click.Path(exists=True))
@click.option("--params", "params_path", type=str, default=None)
@click.option("--stages", default="0-8", help="Stage range, e.g. 0-3 or 5-5")
@click.option("--out", "out_base", type=str, default=None,
              help="Base output directory (default: ./mt_clip170_out)")
@click.option("--reuse", "reuse_dir", type=click.Path(), default=None,
              help="Reuse an existing run directory (skip creating a new timestamped one). "
                   "Useful for iterating on later stages without re-running Stage 0.")
@click.option("--timeout", type=int, default=None,
              help="Override per-run timeout (seconds)")
def tune_cmd(image_path, params_path, stages, out_base, reuse_dir, timeout):
    """Run pipeline on one image with stage selection (for iterative tuning)."""
    _run_once(image_path, params_path, stages, out_base, timeout, reuse_dir=reuse_dir)


@cli.command("run")
@click.option("--image", "image_path", required=True, type=click.Path(exists=True))
@click.option("--params", "params_path", type=str, default=None)
@click.option("--out", "out_base", type=str, default=None)
@click.option("--timeout", type=int, default=None)
def run_cmd(image_path, params_path, out_base, timeout):
    """Run all stages on one image."""
    _run_once(image_path, params_path, "0-8", out_base, timeout)


@cli.command("batch")
@click.option("--input-dir", "input_dir", required=True, type=click.Path(exists=True))
@click.option("--params", "params_path", type=str, default=None)
@click.option("--out", "out_base", type=str, default=None)
@click.option("--glob", "glob_pat", default="*.nd2")
@click.option("--timeout", type=int, default=None)
def batch_cmd(input_dir, params_path, out_base, glob_pat, timeout):
    """Batch-process every image in a folder."""
    images = sorted(Path(input_dir).glob(glob_pat))
    if not images:
        raise click.ClickException(f"No images matched {glob_pat} in {input_dir}")
    click.echo(f"Batch: {len(images)} images")
    for i, img in enumerate(images, 1):
        click.echo(f"\n[{i}/{len(images)}] {img.name}")
        try:
            _run_once(str(img), params_path, "0-8", out_base, timeout)
        except Exception as e:
            click.echo(f"  ! {img.name}: {e}", err=True)


# ─── core run ───────────────────────────────────────────────────────

def _run_once(image_path: str, params_path: Optional[str], stages_spec: str,
              out_base: Optional[str], timeout: Optional[int],
              reuse_dir: Optional[str] = None) -> Path:
    params = load_params(params_path)
    if reuse_dir:
        run_dir = Path(reuse_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        click.echo(f"(reusing run dir: {run_dir})")
    else:
        run_dir = make_run_dir(image_path, out_base)
    snapshot_params(params, run_dir)

    s_start, s_end = parse_stages(stages_spec)
    to = timeout or params.get("runtime", {}).get("stage_timeout_s", 300)
    verbose = params.get("runtime", {}).get("verbose", True)

    if not PIPELINE_IJM.exists():
        raise click.ClickException(f"pipeline.ijm missing at {PIPELINE_IJM}")

    # Flatten params into dotted keys so the macro JSON-lite parser has
    # globally-unique key names (no collisions across sections).
    flat = _flatten(params)
    params_json_path = run_dir / "_params.json"
    with open(params_json_path, "w") as f:
        json.dump(flat, f, indent=2)

    arg_string = encode_args(
        image=os.path.abspath(image_path),
        out_dir=str(run_dir),
        params_path=str(params_json_path),
        stages=f"{s_start}-{s_end}",
    )

    click.echo(f"Image   : {image_path}")
    click.echo(f"Run dir : {run_dir}")
    click.echo(f"Stages  : {s_start}-{s_end}")
    click.echo(f"Timeout : {to}s")
    click.echo("")

    log_path = run_dir / "fiji_stdout.log"
    t0 = time.monotonic()
    rc = run_macro_streamed(PIPELINE_IJM, arg_string, timeout=to,
                            log_path=log_path, verbose=verbose)
    dt = time.monotonic() - t0

    click.echo("")
    if rc != 0:
        click.echo(f"✗ Fiji exited with code {rc} after {dt:.1f}s", err=True)
        click.echo(f"   log: {log_path}", err=True)
        raise click.ClickException("pipeline failed — see log")
    click.echo(f"✓ Pipeline completed in {dt:.1f}s")

    # ── Stage 8 (Python-side): merge per-stage CSVs + summary + composite QC ──
    try:
        cells_csv = qc_mod.merge_cells_csv(run_dir)
        if cells_csv:
            click.echo(f"  merged → {cells_csv.name}")
        summary_csv = qc_mod.write_summary_csv(run_dir, Path(image_path).name)
        if summary_csv:
            click.echo(f"  summary → {summary_csv.name}")
        composite = qc_mod.compose_qc(run_dir)
        if composite:
            click.echo(f"  composite → {composite.name}")
        elif not qc_mod.PIL_OK:
            click.echo("  (composite skipped — install Pillow to enable)")
    except Exception as e:
        click.echo(f"  ! finalize step failed: {e}", err=True)

    # Print cells.csv summary if it exists
    cells_csv_path = run_dir / "cells.csv"
    if cells_csv_path.exists():
        _print_csv_summary(cells_csv_path)

    click.echo(f"\nOutput: {run_dir}")
    return run_dir


def _print_csv_summary(path: Path):
    """Print a one-line-per-cell summary of cells.csv."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    if not lines:
        return
    click.echo(f"\n── {path.name} ({len(lines) - 1} rows) ──")
    for line in lines[:20]:
        click.echo(f"  {line}")
    if len(lines) > 20:
        click.echo(f"  ... ({len(lines) - 20} more rows)")


if __name__ == "__main__":
    cli()
