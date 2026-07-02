"""
optarena/store.py
──────────────
Filesystem results store. Each run is one JSON file in results/runs/; an
index.json summarises all runs so the dashboard (and `arena compare`) can list
them without parsing every file.

Layout:
    results/
      runs/<run_id>.json      full RunRecord
      index.json              [{run_id, scenario name/driver/backend, summary}]
"""

from __future__ import annotations

import json
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"
RUNS_DIR = RESULTS_DIR / "runs"


def save_run(record) -> Path:
    """Persist a RunRecord and refresh the index. Returns the run file path."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{record.run_id}.json"
    path.write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")
    rebuild_index()
    return path


def load_run(ref: str) -> dict:
    """Load a run by id, filename, or path (most-recent match wins)."""
    p = Path(ref)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8"))
    candidates = sorted(RUNS_DIR.glob(f"*{ref}*.json"))
    if not candidates:
        raise FileNotFoundError(f"No run matching '{ref}' in {RUNS_DIR}")
    return json.loads(candidates[-1].read_text(encoding="utf-8"))


def rebuild_index() -> Path:
    """Rewrite index.json from the run files (newest first)."""
    entries = []
    for f in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
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
    index.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return index


def list_runs() -> list[dict]:
    index = RESULTS_DIR / "index.json"
    if not index.exists():
        rebuild_index()
    return json.loads(index.read_text(encoding="utf-8")) if index.exists() else []
