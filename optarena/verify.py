"""
optarena/verify.py
------------------
Corpus self-verification: prove each case's oracle can both pass and fail.

Every corpus bug found to date (do-nothing refactors passing, a csproj glob
failing every correct solution, a perf budget the unoptimized code beat) was
invisible from reading the case and only surfaced by running a known-good
and a known-bad solution through the real oracle. This module makes that
protocol a command instead of a memory.

A case may declare two optional keys:

    "reference_solution": {relpath: content}
        A correct solution. Written over the workspace after setup_files;
        the FULL oracle (expected_files + test_setup_files + check_command,
        Docker-sandboxed) must PASS it.

    "broken_solutions": [{"name": str, "files": {relpath: content}}, ...]
        Deliberately wrong solutions (vacuous tests, unfixed bugs, slow
        implementations). Each must FAIL the oracle.

Additionally, for task types where doing nothing must never score a pass
(bug_fix / refactoring / performance / security), an implicit "unmodified"
variant runs the untouched setup_files through the oracle and must fail.

`optarena verify-corpus` wires this to the CLI and exits non-zero on any
violation, so it works as a CI gate on case edits.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .cases import (
    DOCKER_IMAGE_DEFAULT, DockerSandbox, changed_files, evaluate_case,
    prepare_workspace, snapshot, write_setup_files,
)

# Task types where an unmodified workspace must FAIL the oracle even without
# an explicit broken_solutions entry ("the model changed nothing" is the
# cheapest wrong answer and historically the most dangerous false pass).
MUST_FAIL_UNMODIFIED = {"bug_fix", "refactoring", "performance", "security"}


def variants_for(case: dict) -> list[tuple[str, dict | None, bool]]:
    """(variant_name, files_or_None_for_unmodified, expect_pass) per case."""
    out: list[tuple[str, dict | None, bool]] = []
    if case.get("reference_solution"):
        out.append(("reference", case["reference_solution"], True))
    for i, broken in enumerate(case.get("broken_solutions") or [], 1):
        out.append((broken.get("name") or f"broken-{i}", broken["files"], False))
    # An L3 case's starting state can come entirely from `setup_repo` with no
    # per-case `setup_files` overlay - "unmodified" must still mean something
    # there (the untouched starter repo), not be skipped outright.
    if case.get("task_type") in MUST_FAIL_UNMODIFIED and (case.get("setup_files") or case.get("setup_repo")):
        out.append(("unmodified", None, False))
    return out


def _run_variant(case: dict, files: dict | None, ws: Path) -> list[str]:
    """One variant through the real oracle; returns its failure strings."""
    ws.mkdir(parents=True, exist_ok=True)
    prepare_workspace(ws, case)
    if files is None:                    # implicit "unmodified" variant
        created: list[str] = []
    else:
        before = snapshot(ws)
        write_setup_files(ws, files)
        created = changed_files(before, ws)
    failures, _info = evaluate_case(case, created, ws)
    return failures


def verify_cases(cases: list[dict], root: Path | None = None) -> tuple[list[str], int, int]:
    """
    Verify every declared variant of *cases* against the real oracle.
    Returns (violations, variants_checked, cases_skipped). A violation is a
    reference that failed or a broken/unmodified variant that passed.
    """
    todo = [(c, variants_for(c)) for c in cases]
    skipped = sum(1 for _c, v in todo if not v)
    todo = [(c, v) for c, v in todo if v]

    root = root or Path(tempfile.mkdtemp(prefix="optarena_verify_"))
    root.mkdir(parents=True, exist_ok=True)

    # Same shared-container model as the runner: one sandbox per distinct
    # image any verified case needs, every variant execs into it.
    import os
    images = {
        c.get("docker_image") or os.environ.get("OPTARENA_DOCKER_IMAGE", DOCKER_IMAGE_DEFAULT)
        for c, _v in todo if c.get("check_command")
    }
    sandboxes = [DockerSandbox(root, image=img) for img in images]

    violations: list[str] = []
    checked = 0
    try:
        for sandbox in sandboxes:
            sandbox.start()
        for case, variants in todo:
            for name, files, expect_pass in variants:
                ws = root / case["name"] / name
                failures = _run_variant(case, files, ws)
                checked += 1
                passed = not failures
                if passed == expect_pass:
                    detail = "passed" if passed else f"failed as expected: {failures[0][:100]}"
                    print(f"  [verify] {case['name']} / {name:12} OK ({detail})")
                else:
                    what = ("reference solution FAILED the oracle: " + "; ".join(failures)[:300]
                            if expect_pass else
                            "broken variant PASSED the oracle (it must fail)")
                    violations.append(f"{case['name']} / {name}: {what}")
                    print(f"  [verify] {case['name']} / {name:12} VIOLATION - {what}")
    finally:
        for sandbox in sandboxes:
            sandbox.stop()
    return violations, checked, skipped
