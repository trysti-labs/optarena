"""
Write optarena/_build_commit.txt from the current git HEAD, so a wheel built
from this checkout can report its exact source commit even after installation
(no .git directory travels with a wheel - see runner/_manifest.py's
_source_revision(), P2-02). Run this BEFORE `python -m build`; the file is
gitignored and must never be committed - it would shadow the live checkout's
own git state for every other install method that doesn't need a fallback.

Usage: python scripts/embed_build_commit.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "optarena" / "_build_commit.txt"


def main() -> int:
    try:
        commit = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"error: could not determine git HEAD: {exc}", file=sys.stderr)
        return 1
    dirty = bool(subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip())
    if dirty:
        print("warning: building a wheel from a dirty working tree - the "
              "embedded commit will not exactly match the packaged content",
              file=sys.stderr)
    TARGET.write_text(commit + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {TARGET} ({commit})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
