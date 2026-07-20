"""
optarena/store.py
──────────────
Filesystem results store. Each run is one JSON file in results/runs/; an
index.json summarises all runs so the dashboard (and `optarena compare`) can list
them without parsing every file.

Layout:
    results/
      runs/<run_id>.json      full RunRecord
      index.json              [{run_id, scenario name/driver/backend, summary}]

Location: defaults to <repo>/results (unchanged, so existing checkouts and
scripts keep working), overridable via OPTARENA_RESULTS_DIR or the CLI's
top-level --results-dir (which just sets that env var - see cli.main).
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _default_results_dir() -> Path:
    override = os.environ.get("OPTARENA_RESULTS_DIR")
    return Path(override) if override else Path(__file__).resolve().parents[1] / "results"


RESULTS_DIR = _default_results_dir()
RUNS_DIR = RESULTS_DIR / "runs"


def set_results_dir(path: "str | Path") -> None:
    """Override the results location for the rest of this process (module-
    level, not per-call, so every store.py function - and compare.py, which
    imports RESULTS_DIR directly - sees the same location consistently)."""
    global RESULTS_DIR, RUNS_DIR
    RESULTS_DIR = Path(path)
    RUNS_DIR = RESULTS_DIR / "runs"


def _write_atomic(path: Path, text: str) -> None:
    """Write via tmp + replace so `optarena serve` never reads a partial file."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def save_run(record) -> Path:
    """Persist a RunRecord and refresh the index. Returns the run file path.

    Refuses to overwrite an existing run file (run_id collision) - the
    run_id already carries a random suffix specifically to make this
    practically unreachable (see runner.run_scenario), so hitting it means
    something is actually wrong (a clock rollback, a copied results dir)
    rather than an ordinary race to paper over.
    """
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{record.run_id}.json"
    if path.exists():
        raise FileExistsError(
            f"run_id collision: {path} already exists - refusing to overwrite a saved run"
        )
    _write_atomic(path, json.dumps(record.to_dict(), indent=2))
    rebuild_index()
    return path


def load_run(ref: str) -> dict:
    """Load a run by exact id, filename, or path.

    A path (or exact filename) always wins outright. Otherwise `ref` must
    match exactly ONE run_id - a substring match against multiple runs
    raises listing the candidates instead of silently picking the newest
    (silently comparing against the wrong run is worse than refusing).
    """
    p = Path(ref)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    exact = RUNS_DIR / f"{ref}.json"
    if exact.is_file():
        return json.loads(exact.read_text(encoding="utf-8"))
    candidates = sorted(RUNS_DIR.glob(f"*{ref}*.json"))
    if not candidates:
        raise FileNotFoundError(f"No run matching '{ref}' in {RUNS_DIR}")
    if len(candidates) > 1:
        names = ", ".join(c.stem for c in candidates)
        raise ValueError(
            f"'{ref}' matches {len(candidates)} runs, not exactly one: {names} - "
            f"pass the exact run_id or a path to disambiguate"
        )
    return json.loads(candidates[0].read_text(encoding="utf-8"))


def rebuild_index() -> Path:
    """Rewrite index.json from the run files (newest first)."""
    entries = []
    for f in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        # A foreign/hand-edited JSON file in runs/ (parses, but isn't a
        # RunRecord) must not take down the whole index rebuild - and with it
        # the dashboard - with a KeyError. Skip it like a corrupt file.
        if not isinstance(rec, dict) or "run_id" not in rec:
            continue
        entries.append({
            "run_id": rec["run_id"],
            "file": f"runs/{f.name}",
            "started_at": rec.get("started_at"),
            "scenario": rec.get("scenario", {}).get("name"),
            "driver": rec.get("scenario", {}).get("driver"),
            "backend": rec.get("scenario", {}).get("backend", {}),
            "summary": rec.get("summary", {}),
        })
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    index = RESULTS_DIR / "index.json"
    _write_atomic(index, json.dumps(entries, indent=2))
    return index


def list_runs() -> list[dict]:
    index = RESULTS_DIR / "index.json"
    if not index.exists():
        rebuild_index()
    return json.loads(index.read_text(encoding="utf-8")) if index.exists() else []
