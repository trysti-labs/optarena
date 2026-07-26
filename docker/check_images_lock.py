#!/usr/bin/env python3
"""
F-13/F-17: verify docker/images.lock.json stays in sync with the Dockerfiles
it describes - a base_digest edited in one place and not the other (or a
Dockerfile digest bumped without updating the lock) would otherwise only
surface as a confusing build-time mismatch, or not at all. Checks:

  1. every image's `base_digest` matches its Dockerfile's `FROM ...@sha256:...` line
  2. every non-null `dependency_lockfile` path actually exists

Exits non-zero (with a list of every mismatch, not just the first) on failure,
so this belongs in CI as a fast, no-Docker-required lint step.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = REPO_ROOT / "docker" / "images.lock.json"
_FROM_DIGEST_RE = re.compile(r"^FROM\s+\S+@(sha256:[0-9a-f]{64})\s*$", re.MULTILINE)


def check() -> list[str]:
    errors: list[str] = []
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    for name, entry in lock.get("images", {}).items():
        dockerfile = REPO_ROOT / entry["dockerfile"]
        if not dockerfile.is_file():
            errors.append(f"{name}: dockerfile not found: {entry['dockerfile']}")
            continue
        m = _FROM_DIGEST_RE.search(dockerfile.read_text(encoding="utf-8"))
        if not m:
            errors.append(f"{name}: {entry['dockerfile']} has no digest-pinned FROM line")
        elif m.group(1) != entry["base_digest"]:
            errors.append(
                f"{name}: images.lock.json base_digest ({entry['base_digest']}) != "
                f"{entry['dockerfile']}'s FROM digest ({m.group(1)})"
            )
        lockfile = entry.get("dependency_lockfile")
        if lockfile and not (REPO_ROOT / lockfile).is_file():
            errors.append(f"{name}: dependency_lockfile declared but missing: {lockfile}")
    return errors


def main() -> int:
    errors = check()
    if errors:
        print(f"images.lock.json is out of sync ({len(errors)} problem(s)):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"images.lock.json OK ({len(json.loads(LOCK_FILE.read_text(encoding='utf-8'))['images'])} images checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
