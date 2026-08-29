"""_template driver — Click subcommands for a Zotero pipeline skill.

Mirrors fiji.skills.mt_clip170.driver. Each stage is a Python function that
takes the working directory and the params dict, reads the previous stage's
output JSON from disk, and writes its own. No external macro language is
needed — Zotero is queried directly via the parent CLI's local API client.

Replace the stub bodies (``raise NotImplementedError``) with real calls into
``cli_anything.zotero.core`` / ``utils`` when you turn this into a concrete
skill.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import click

try:
    import yaml
except ImportError:
    yaml = None

PKG_DIR = Path(__file__).resolve().parent
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


def _safe_slug(s: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in s).strip("_") or "query"


def _make_run_dir(label: str, out_root: Optional[Path]) -> Path:
    root = Path(out_root) if out_root else DEFAULT_OUT_ROOT
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = root / _safe_slug(label) / stamp
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─── stages ─────────────────────────────────────────────────────────


def stage_fetch(run_dir: Path, params: dict, query: dict) -> Path:
    """Stage 0: pull raw items from Zotero. Writes 00_raw_items.json."""
    # TODO: from cli_anything.zotero.core import collections, items
    #       resolve query (collection / search / tag) -> list of item dicts
    raise NotImplementedError("Implement stage_fetch using cli_anything.zotero.core")


def stage_filter(run_dir: Path, params: dict) -> Path:
    """Stage 1: filter + dedupe. Reads 00_raw_items.json, writes 01_filtered.json."""
    raise NotImplementedError("Implement stage_filter")


def stage_extract(run_dir: Path, params: dict) -> Path:
    """Stage 2: enrich with abstracts/notes/PDF paths. Writes 02_enriched.json."""
    raise NotImplementedError("Implement stage_extract")


def stage_export(run_dir: Path, params: dict) -> None:
    """Stage 3: render CSV / BibTeX / markdown summary."""
    raise NotImplementedError("Implement stage_export")


_STAGES = [
    ("fetch",   stage_fetch),
    ("filter",  stage_filter),
    ("extract", stage_extract),
    ("export",  stage_export),
]


def _parse_range(spec: str) -> range:
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return range(int(lo), int(hi) + 1)
    n = int(spec)
    return range(n, n + 1)


def _run_stages(run_dir: Path, params: dict, query: dict, stages: str) -> None:
    indices = list(_parse_range(stages))
    for idx in indices:
        if idx >= len(_STAGES):
            raise click.ClickException(f"Stage {idx} out of range (max {len(_STAGES)-1})")
        name, fn = _STAGES[idx]
        click.echo(f"[_template] === stage {idx}: {name} ===")
        if idx == 0:
            fn(run_dir, params, query)
        else:
            fn(run_dir, params)


# ─── CLI ────────────────────────────────────────────────────────────


@click.group(name="_template")
def cli() -> None:
    """Placeholder Zotero skill. Rename me and fill in real logic."""


def _query_from_options(collection, search, tag) -> dict:
    chosen = [(k, v) for k, v in (("collection", collection), ("search", search), ("tag", tag)) if v]
    if not chosen:
        raise click.ClickException(
            "Provide exactly one of --collection / --search / --tag."
        )
    if len(chosen) > 1:
        raise click.ClickException("Provide only one of --collection / --search / --tag.")
    key, value = chosen[0]
    return {"kind": key, "value": value}


@cli.command()
@click.option("--collection", default=None, help="Zotero collection name")
@click.option("--search", default=None, help="Zotero saved-search key")
@click.option("--tag", default=None, help="Zotero tag")
@click.option("--out", "out_root", type=click.Path(), default=None)
@click.option("--set", "overrides", multiple=True,
              help='Override params, e.g. --set filter.year_min=2018')
@click.option("--params", "params_path", default=None)
def run(collection, search, tag, out_root, overrides, params_path):
    """Run all stages on the chosen query."""
    _require_yaml()
    params = _apply_overrides(load_params(params_path), overrides)
    query = _query_from_options(collection, search, tag)
    run_dir = _make_run_dir(query["value"], Path(out_root) if out_root else None)
    with open(run_dir / "params_used.yaml", "w") as f:
        yaml.safe_dump(params, f)
    try:
        _run_stages(run_dir, params, query, "0-3")
    except NotImplementedError as exc:
        click.echo(f"[_template] STOP — stub not yet implemented: {exc}", err=True)
        sys.exit(2)


@cli.command()
@click.option("--collection", default=None)
@click.option("--search", default=None)
@click.option("--tag", default=None)
@click.option("--stages", default="0-3", help='Inclusive range, e.g. "0-1"')
@click.option("--out", "out_root", type=click.Path(), default=None)
@click.option("--set", "overrides", multiple=True)
@click.option("--params", "params_path", default=None)
def tune(collection, search, tag, stages, out_root, overrides, params_path):
    """Run a subset of stages with live logs."""
    _require_yaml()
    params = _apply_overrides(load_params(params_path), overrides)
    query = _query_from_options(collection, search, tag)
    run_dir = _make_run_dir(query["value"], Path(out_root) if out_root else None)
    with open(run_dir / "params_used.yaml", "w") as f:
        yaml.safe_dump(params, f)
    try:
        _run_stages(run_dir, params, query, stages)
    except NotImplementedError as exc:
        click.echo(f"[_template] STOP — stub not yet implemented: {exc}", err=True)
        sys.exit(2)


@cli.command()
def params() -> None:
    """Print current default parameters."""
    _require_yaml()
    click.echo(yaml.safe_dump(load_params(), sort_keys=False))


if __name__ == "__main__":
    cli()
