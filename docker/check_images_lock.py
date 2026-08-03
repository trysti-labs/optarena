#!/usr/bin/env python3
"""
F-13/F-17/P1-04: verify docker/images.lock.json stays in sync with the
Dockerfiles it describes - a base_digest edited in one place and not the
other (or a Dockerfile digest bumped without updating the lock) would
otherwise only surface as a confusing build-time mismatch, or not at all.
Checks:

  1. every image's `base_digest` matches its Dockerfile's `FROM ...@sha256:...` line
  2. every non-null `dependency_lockfile` path actually exists
  3. every `pinned_dependencies` entry's recorded version actually appears in
     the Dockerfile, on a line that mentions that dependency by name

Check 3 is a heuristic, not a real per-ecosystem parser (Go's `go get
pkg@version`, pip's `pkg==version`, a phpunit.phar URL, and a Terraform
release URL have four different pin syntaxes with no common grammar) - it
looks for the dependency's short name (last path segment) on a Dockerfile
line and requires the pinned version string to appear verbatim on that same
line. This is exactly what would have caught the real drift this check
exists for: `images.lock.json` said phpunit 11.5.56 while
`docker/php/Dockerfile` had already been bumped to 12.0.0, and nothing
before this function ever compared the two.

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
_VERSION_TOKEN_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")


def _dep_short_name(dep: str) -> str:
    """"github.com/gin-gonic/gin" -> "gin"; "phpunit" -> "phpunit"."""
    return dep.rsplit("/", 1)[-1]


def _check_pinned_dependencies(name: str, entry: dict, dockerfile_text: str) -> list[str]:
    errors: list[str] = []
    for dep, pinned_version in (entry.get("pinned_dependencies") or {}).items():
        short = _dep_short_name(dep).lower()
        lines = [ln for ln in dockerfile_text.splitlines() if short in ln.lower()]
        if not lines:
            errors.append(
                f"{name}: pinned_dependencies[{dep!r}]={pinned_version!r} but "
                f"{entry['dockerfile']} never mentions {_dep_short_name(dep)!r}"
            )
            continue
        if not any(str(pinned_version) in ln for ln in lines):
            found = sorted({v for ln in lines for v in _VERSION_TOKEN_RE.findall(ln)})
            errors.append(
                f"{name}: pinned_dependencies[{dep!r}] says {pinned_version!r} but no "
                f"{_dep_short_name(dep)!r} line in {entry['dockerfile']} contains that "
                f"exact string (version-like token(s) actually present: {found or 'none'})"
            )
    return errors


def check() -> list[str]:
    errors: list[str] = []
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    for name, entry in lock.get("images", {}).items():
        dockerfile = REPO_ROOT / entry["dockerfile"]
        if not dockerfile.is_file():
            errors.append(f"{name}: dockerfile not found: {entry['dockerfile']}")
            continue
        dockerfile_text = dockerfile.read_text(encoding="utf-8")
        m = _FROM_DIGEST_RE.search(dockerfile_text)
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
        errors.extend(_check_pinned_dependencies(name, entry, dockerfile_text))
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
