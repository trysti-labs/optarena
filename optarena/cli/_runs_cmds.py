"""Saved-run maintenance commands: show/rebuild-index/prune/scrub-secrets."""

from __future__ import annotations

import json
import sys

from .. import store


def cmd_run_show(args) -> int:
    """Summary + per-case table for one saved run."""
    try:
        run = store.load_run(args.run_ref)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    s = run.get("summary", {})
    sc = run.get("scenario", {})
    b = sc.get("backend", {})
    print(f"\n  {run['run_id']}")
    print(f"  driver={sc.get('driver')}  backend={b.get('model')}@{b.get('base_url')}"
          f"  started={run.get('started_at')}")
    man = run.get("manifest") or {}
    if man:
        print(f"  manifest: {man.get('case_count')} case(s) hash {man.get('case_set_hash')}, "
              f"oracle v{man.get('oracle_version')}, trials={man.get('trials')}")
    print(f"  {s.get('passed', '?')}/{s.get('cases', '?')} passed "
          f"(mean {s.get('mean_duration_s', '?')}s"
          + (f", ${s['total_cost_usd']:.2f}" if s.get("total_cost_usd") is not None else "")
          # P2-05: flag a partial total instead of letting it read as complete.
          + (f" (+{s['unpriced_cases']} unpriced)" if s.get("unpriced_cases") else "")
          + ")\n")
    for c in run.get("cases", []):
        status = "PASS" if c.get("passed") else ("ERROR" if c.get("error") else "FAIL")
        detail = "" if c.get("passed") else \
            f"  - {c.get('error') or '; '.join((c.get('failures') or [])[:1])}"
        print(f"  {status:5} {c['name']:40} {c.get('duration_s', 0):6.1f}s{detail}")
    return 0


def cmd_runs_rebuild_index(args) -> int:
    """
    Rebuild results/index.json from the run files on disk.

    A-23: three separate docstrings in store.py already pointed users here
    ("recoverable via `optarena runs rebuild-index`") for a corrupt or
    out-of-sync index - but the command did not exist, so the documented
    recovery path was unreachable. `store.rebuild_index` has always been
    there; this exposes it.
    """
    path = store.rebuild_index()
    entries = json.loads(path.read_text(encoding="utf-8"))
    print(f"  rebuilt {path} from {len(entries)} run file(s)")
    return 0


def cmd_runs_prune(args) -> int:
    """Delete saved runs, oldest first, keeping the newest --keep (or only
    those matching --before). Results accumulate one JSON per run forever;
    there was no supported way to clear them out short of `rm`."""
    runs = sorted(store.RUNS_DIR.glob("*.json")) if store.RUNS_DIR.is_dir() else []
    if not runs:
        print(f"  no runs in {store.RUNS_DIR}")
        return 0
    # run_id starts with YYYYmmdd-HHMMSS, so filename order is time order.
    doomed = runs[:-args.keep] if args.keep > 0 else list(runs)
    if args.before:
        doomed = [p for p in doomed if p.name < args.before]
    if not doomed:
        print(f"  nothing to prune ({len(runs)} run(s), keeping newest {args.keep})")
        return 0
    if not args.yes:
        print(f"  would delete {len(doomed)} of {len(runs)} run(s), keeping the newest {args.keep}:")
        for p in doomed[:10]:
            print(f"    - {p.name}")
        if len(doomed) > 10:
            print(f"    ... and {len(doomed) - 10} more")
        print("  re-run with --yes to actually delete them")
        return 0
    for p in doomed:
        p.unlink(missing_ok=True)
    store.rebuild_index()
    print(f"  deleted {len(doomed)} run(s); index rebuilt")
    return 0


def cmd_runs_scrub_secrets(args) -> int:
    """
    Redact `backend.api_key` from run records written before redaction
    existed (A-24), AND (P1-01) any pattern-shaped secret - an AWS key, a
    GitHub token, a provider `sk-...` key, a PEM private-key header - found
    ANYWHERE in a saved run record, recursively. `backend.api_key` needed
    special-cased handling before this existed because a key doesn't always
    match one of the shaped patterns (a bare hex string, a JWT, a custom
    format); everything else - a driver's captured stderr or exception text,
    in particular - gets the same recursive pattern pass a live run's own
    redaction (`drivers/cli_agents.py`, `aider_cli.py`) applies going
    forward, for records that predate it or that a pattern it didn't
    anticipate slipped through.

    Saved runs have stored a redacted backend for a while now, but records
    from before that still carry the raw key on disk - and `optarena serve`
    publishes the whole results directory over HTTP to every process on the
    machine. There was no supported way to clean them.
    """
    from ..security import redact_secrets_recursive

    runs = sorted(store.RUNS_DIR.glob("*.json")) if store.RUNS_DIR.is_dir() else []
    scrubbed = []
    for path in runs:
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        backend = (rec.get("scenario") or {}).get("backend")
        has_raw_key = isinstance(backend, dict) and backend.get("api_key") is not None
        redacted = redact_secrets_recursive(rec)
        has_pattern_secret = redacted != rec
        if not has_raw_key and not has_pattern_secret:
            continue
        if not args.yes:
            scrubbed.append(path)
            continue
        if has_raw_key:
            redacted_backend = (redacted.get("scenario") or {}).get("backend")
            redacted_backend["api_key_set"] = (
                bool(backend.get("api_key")) and backend["api_key"] != "optarena")
            redacted_backend["api_key"] = None
        store._write_atomic(path, json.dumps(redacted, indent=2))
        scrubbed.append(path)
    if not scrubbed:
        print(f"  no run records under {store.RUNS_DIR} carry an api_key or a pattern-shaped secret")
        return 0
    if not args.yes:
        print(f"  {len(scrubbed)} run record(s) still contain an api_key or a pattern-shaped secret:")
        for p in scrubbed[:10]:
            print(f"    - {p.name}")
        if len(scrubbed) > 10:
            print(f"    ... and {len(scrubbed) - 10} more")
        print("  re-run with --yes to redact them in place")
        return 1
    store.rebuild_index()
    print(f"  redacted {len(scrubbed)} run record(s)")
    return 0
