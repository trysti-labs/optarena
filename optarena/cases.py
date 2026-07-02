"""
optarena/cases.py
──────────────
Case catalogue. A *case* is a task given to a tool plus a filesystem oracle that
decides pass/fail — the JSON schema shared with the original cline harness:

    {
      "name":           str,
      "description":    str,
      "prompts":        [str, ...],          # sent in order
      "setup_files":    {relpath: content},  # written before the run
      "expected_files": [{"path_pattern": str, "content_patterns": [str]}],
      "timeout":        int                  # seconds
    }
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

CASES_DIR = Path(__file__).parent / "cases"


def load_cases(names: list[str] | None = None, cases_dir: Path | None = None) -> list[dict]:
    """Load all (or the named) cases from *cases_dir*, sorted by filename."""
    directory = cases_dir or CASES_DIR
    cases = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(directory.glob("*.json"))
    ]
    if names:
        wanted = {n.strip() for n in names}
        cases = [c for c in cases if c["name"] in wanted]
        missing = wanted - {c["name"] for c in cases}
        if missing:
            raise FileNotFoundError(f"Unknown case(s): {', '.join(sorted(missing))}")
    return cases


# ── Filesystem oracle (shared by drivers that verify via workspace diff) ──────

IGNORE_DIRS = {".git", ".vscode", ".cline", ".aider", "node_modules", "__pycache__"}


def snapshot(root: Path) -> dict[str, str]:
    """Map each file (relative posix path) to a `size:mtime` signature."""
    out: dict[str, str] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in IGNORE_DIRS for part in p.relative_to(root).parts):
            continue
        st = p.stat()
        out[p.relative_to(root).as_posix()] = f"{st.st_size}:{st.st_mtime_ns}"
    return out


def changed_files(before: dict[str, str], root: Path) -> list[str]:
    """Files that are new OR modified since *before*."""
    current = snapshot(root)
    return [rel for rel, sig in current.items() if before.get(rel) != sig]


def check_expected(created: list[str], expected_spec: list[dict], root: Path) -> list[str]:
    """Return failure strings; empty list ⇒ the case passed."""
    failures: list[str] = []
    for spec in expected_spec or []:
        pattern = spec["path_pattern"]
        match = next(
            (rel for rel in created
             if fnmatch.fnmatch(Path(rel).name.lower(), pattern.lower())
             or fnmatch.fnmatch(rel.lower(), pattern.lower())),
            None,
        )
        if match is None:
            failures.append(
                f'expected file matching "{pattern}" not created '
                f'(got: {", ".join(created) or "none"})'
            )
            continue
        try:
            content = (root / match).read_text(encoding="utf-8", errors="replace").lower()
        except OSError as exc:
            failures.append(f'could not read "{match}": {exc}')
            continue
        for needle in spec.get("content_patterns", []):
            if str(needle).lower() not in content:
                failures.append(f'"{match}" missing expected content "{needle}"')
    return failures


def write_setup_files(root: Path, setup_files: dict[str, str] | None) -> None:
    for rel, content in (setup_files or {}).items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
