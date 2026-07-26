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
import time
import uuid
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
    """Write via tmp + replace so `optarena serve` never reads a partial
    file. The tmp filename carries a random suffix (F-07) - two processes
    racing to write the SAME target path previously both targeted the
    identical ``<path>.json.tmp``, risking one process's write interleaving
    with the other's before either could atomically replace."""
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class _IndexLock:
    """
    Coarse-grained inter-process mutex around index.json updates (F-07), via
    exclusive file creation - portable without reaching for `fcntl`/`msvcrt`
    (this project stays stdlib-only, but doesn't need a real flock for
    something this short-lived: an index upsert is a read-modify-atomic-write
    of a small JSON file, not a long critical section). A lock file older
    than STALE_AFTER_S is assumed to be left over from a killed process and
    is broken rather than honored forever; a lock that still can't be
    acquired after the wait deadline is proceeded past unlocked rather than
    hanging the whole run - a lost update to index.json is recoverable via
    `optarena runs rebuild-index`, a hung run is not acceptable collateral.
    """
    STALE_AFTER_S = 30.0
    WAIT_DEADLINE_S = 10.0

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self) -> "_IndexLock":
        deadline = time.monotonic() + self.WAIT_DEADLINE_S
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > self.STALE_AFTER_S:
                        self.path.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    return self
                time.sleep(0.05)

    def __exit__(self, *exc_info) -> None:
        self.path.unlink(missing_ok=True)


def _index_entry(rec: dict, path: Path) -> dict:
    return {
        "run_id": rec["run_id"],
        "file": f"runs/{path.name}",
        "started_at": rec.get("started_at"),
        "scenario": rec.get("scenario", {}).get("name"),
        "driver": rec.get("scenario", {}).get("driver"),
        "backend": rec.get("scenario", {}).get("backend", {}),
        "summary": rec.get("summary", {}),
        # F-02: "running" | "completed" | "interrupted" | "error" | None
        # (older runs saved before this field existed).
        "status": rec.get("status"),
    }


def _upsert_index_entry(entry: dict) -> None:
    """Incrementally update index.json with one entry (F-07) - replacing any
    existing entry for the same run_id (a checkpoint re-save) or prepending
    a new one (index is newest-first) - instead of re-parsing every
    historical run file on every single save. Lock-guarded so two processes
    saving around the same moment don't race on the same tmp path."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    index_path = RESULTS_DIR / "index.json"
    lock_path = RESULTS_DIR / "index.lock"
    with _IndexLock(lock_path):
        try:
            entries = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
            if not isinstance(entries, list):
                entries = []
        except (OSError, json.JSONDecodeError):
            # A corrupt/mid-write index - rebuilding from just this one entry
            # is safe (never worse than what was there); `rebuild-index`
            # recovers the rest.
            entries = []
        entries = [e for e in entries if e.get("run_id") != entry["run_id"]]
        entries.insert(0, entry)
        _write_atomic(index_path, json.dumps(entries, indent=2))


def save_checkpoint(record) -> Path:
    """Persist a RunRecord IN PROGRESS (F-02, ``record.status == "running"``).
    Unlike ``save_run``, overwriting the same run_id repeatedly is expected -
    called once per completed case so an interrupted run doesn't lose
    everything finished before the interruption. Uses the incremental index
    upsert, not a full rebuild, so per-case checkpointing doesn't reintroduce
    F-07's O(n) cost on every single case."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{record.run_id}.json"
    _write_atomic(path, json.dumps(record.to_dict(), indent=2))
    _upsert_index_entry(_index_entry(record.to_dict(), path))
    return path


def save_run(record) -> Path:
    """Persist a FINAL RunRecord and update the index. Returns the run file path.

    Refuses to overwrite an existing run file UNLESS that existing file is
    this same run's own in-progress checkpoint (F-02: ``status == "running"``,
    written by ``save_checkpoint`` during this same run) - that's not a
    collision, it's this run's record being finalized. Anything else at that
    path (run_id already carries a random suffix specifically to make a
    genuine collision practically unreachable - see runner.run_scenario)
    means something is actually wrong (a clock rollback, a copied results
    dir), not an ordinary race to paper over.
    """
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{record.run_id}.json"
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
        if not isinstance(existing, dict) or existing.get("status") != "running":
            raise FileExistsError(
                f"run_id collision: {path} already exists - refusing to overwrite a saved run"
            )
    _write_atomic(path, json.dumps(record.to_dict(), indent=2))
    _upsert_index_entry(_index_entry(record.to_dict(), path))
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
    """Rewrite index.json from the run files (newest first) - full O(n)
    rebuild. No longer called on every save (F-07 - see `_upsert_index_entry`
    for the normal incremental path); kept as an explicit repair command
    (`optarena runs rebuild-index`) for recovering from a corrupted or
    out-of-sync index."""
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
        entries.append(_index_entry(rec, f))
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    index = RESULTS_DIR / "index.json"
    _write_atomic(index, json.dumps(entries, indent=2))
    return index


def list_runs() -> list[dict]:
    index = RESULTS_DIR / "index.json"
    if not index.exists():
        rebuild_index()
    return json.loads(index.read_text(encoding="utf-8")) if index.exists() else []
