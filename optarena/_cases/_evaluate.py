"""Ties the assertion oracle and check_command execution together into the
one full per-case verdict (`evaluate_case`), plus the trajectory/diff-size
metrics reported alongside it."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from ._constants import IGNORE_DIRS
from ._sandbox import _new_oracle_info, classify_failure, run_check_command
from ._snapshot import check_expected, path_pattern_matches
from ._workspace_setup import write_setup_files


def diff_stats(case: dict, created: list[str], root: Path) -> dict:
    """
    Approximate size of the change for this case: files touched and lines
    changed. For "modify" cases the delta is against the known original
    content in ``setup_files`` (the pre-run snapshot stores only a content
    *hash* per file, not the content itself, so the case's own setup text is
    the best available reference); new files count their full line length.
    """
    setup_files = case.get("setup_files") or {}

    def _line_count(text: str) -> int:
        return text.count("\n") + (1 if text and not text.endswith("\n") else 0)

    lines_changed = 0
    for rel in created:
        try:
            content = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        new_lines = _line_count(content)
        original = setup_files.get(rel)
        lines_changed += abs(new_lines - _line_count(original)) or 1 if original is not None else new_lines
    return {"files_changed": len(created), "lines_changed_approx": lines_changed}


def trajectory_stats(case: dict, created: list[str], root: Path) -> dict:
    """
    Driver-agnostic *trajectory* signals - "judge the path, not just the answer".
    The oracle's pass/fail verdict says whether the agent reached the right end
    state; these say something about HOW it got there, from the one artifact
    every driver produces: the set of files it actually changed (``created``).

    - ``off_target_files``: files the agent created/modified that match NO
      expected-file pattern and aren't one of the case's own ``setup_files`` -
      i.e. edits the task never asked for. A disciplined agent (and every
      raw-model baseline) touches only expected paths, so this is 0; a value > 0
      means the agent also modified unrelated files (thrash, scratch files, or
      collateral edits) even if it still passed. Reported, never used to gate
      pass/fail - it's a quality signal, not a verdict. Paired with
      ``execution_ok`` (tool exited cleanly) it powers the ``clean_pass`` rollup
      in metrics.aggregate: passed AND no off-target edits AND clean exit - the
      "right answer via a clean path" the trace-eval literature calls for.
    """
    expected = case.get("expected_files", []) or []
    setup = set((case.get("setup_files") or {}).keys())

    def _matches_expected(rel: str) -> bool:
        # A-39: shares path_pattern_matches with check_expected (was a
        # separate, plain-fnmatch copy) - otherwise a file that now
        # correctly PASSES the oracle under a "**/"-prefixed pattern would
        # still show up here as an unexplained "off-target edit", which is
        # exactly backwards for a file the case explicitly expected.
        for spec in expected:
            pat = spec.get("path_pattern")
            if pat and path_pattern_matches(rel, str(pat)):
                return True
        return False

    off_target = [rel for rel in created if rel not in setup and not _matches_expected(rel)]
    return {
        "files_touched": len(created),
        "expected_file_count": len(expected),
        "off_target_count": len(off_target),
        # Cap the stored list so a pathological run can't bloat the JSON; the
        # count above is always exact.
        "off_target_files": sorted(off_target)[:25],
    }


def evaluate_case(case: dict, created: list[str], root: Path) -> tuple[list[str], dict]:
    """
    Full oracle for one case: write test_setup_files (real test code the
    model never saw), check expected-file specs, then (only when those pass)
    run the optional check_command. Returns ``(failure_strings, oracle_info)``
    - ``oracle_info`` is what actually ran (sandbox, image, exit code,
    duration, output, a diff-size estimate, and a best-effort failure
    classification), for callers that want to show their work rather than
    just the pass/fail verdict.
    """
    test_files = list((case.get("test_setup_files") or {}).keys())
    write_setup_files(root, case.get("test_setup_files"))
    failures = check_expected(created, case.get("expected_files", []), root)
    if failures:
        info = _new_oracle_info(case.get("check_command"))
        info["test_setup_files"] = test_files
        info["diff"] = diff_stats(case, created, root)
        info["trajectory"] = trajectory_stats(case, created, root)
        return failures, info
    failures, info = run_check_command(case, root)
    info["test_setup_files"] = test_files
    info["diff"] = diff_stats(case, created, root)
    info["trajectory"] = trajectory_stats(case, created, root)
    if failures:
        info["failure_class"] = classify_failure(info)
    return failures, info


def _copy_workspace_for_verification(src: Path, dest: Path) -> None:
    """Recursively copy ``src`` into ``dest`` (same IGNORE_DIRS/``.aider*``
    skip rules as ``snapshot``), for grading on a private copy instead of a
    live workspace. Symlinks are skipped outright - defense in depth, a
    workspace shouldn't need them for grading and following one could copy
    arbitrary host file content."""
    dest.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        if entry.is_symlink():
            continue
        if entry.is_dir():
            if entry.name in IGNORE_DIRS:
                continue
            _copy_workspace_for_verification(entry, dest / entry.name)
        elif entry.is_file():
            if entry.name.startswith(".aider"):
                continue
            shutil.copy2(entry, dest / entry.name)


def evaluate_case_isolated(case: dict, created: list[str], live_root: Path) -> tuple[list[str], dict]:
    """
    Same as ``evaluate_case``, but grades a PRIVATE COPY of ``live_root``
    instead of ``live_root`` itself (C-01: never write hidden test files into
    a workspace the tool under test might read again on its next turn).

    ``evaluate_case`` writes the case's hidden ``test_setup_files`` straight
    into whatever root it's given - fine when called once at the very end of
    a case, after the last prompt (nothing will ever read the workspace
    again). It is NOT fine for the precise per-step attribution check
    (Tier 3): that call happens BETWEEN prompts, in the SAME live workspace a
    CLI agent's next invocation runs in (``cwd=workspace``) - writing hidden
    test files straight into it would let the agent list/read them on its
    next turn, discovering exactly what its hidden test expects and
    defeating the "never seen by the model" guarantee documented in this
    module's docstring. Grading a disposable sibling copy instead (still
    under the same run root, so the existing shared container sandbox can
    reach it) keeps the live workspace read-only from grading's perspective.
    """
    live_root = live_root.resolve()
    verify_root = live_root.parent / f".optarena-verify-{uuid.uuid4().hex[:10]}"
    try:
        _copy_workspace_for_verification(live_root, verify_root)
        return evaluate_case(case, created, verify_root)
    finally:
        shutil.rmtree(verify_root, ignore_errors=True)
