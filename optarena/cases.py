"""
optarena/cases.py
──────────────
Case catalogue. A *case* is a task given to a tool plus a filesystem oracle that
decides pass/fail:

    {
      "name":           str,
      "description":    str,
      "prompts":        [str, ...],          # sent in order
      "setup_files":    {relpath: content},  # written BEFORE the run (visible
                                             # to the model - e.g. the file a
                                             # "modify" case edits)
      "setup_repo":     str,                 # L3 (repo-scale) cases: name of a
                                             # shared starter repo under repos/
                                             # copied into the workspace BEFORE
                                             # setup_files (which then overlay it)
      "git_init":       bool,                # commit the prepared workspace as a
                                             # one-commit git repo so repo-scale
                                             # edits land as a realistic diff
      "expected_files": [{
          "path_pattern":         str,       # glob vs relpath and basename
          "content_patterns":     [str],     # required substrings (case-insensitive)
          "not_content_patterns": [str],     # optional: forbidden substrings
          "regex_patterns":       [str],     # optional: required regexes
          "min_lines":            int        # optional: minimum line count
      }],
      "test_setup_files":      {relpath: content},  # written AFTER the run,
                                             # never seen by the model - real
                                             # test code (pytest/unittest/node
                                             # assert/etc) that check_command runs
      "check_command":         str,          # shell command run in the
                                             # workspace (inside a container
                                             # when available; see run_check_command);
                                             # non-zero exit fails the case
      "check_command_timeout": int,          # seconds (default 60)
      "image":                 str,          # optional: which sandbox image this
                                             # case needs (see DOCKER_IMAGES in
                                             # drivers-adjacent docker/ dir);
                                             # defaults to DOCKER_IMAGE_DEFAULT
      "timeout":        int,                 # seconds

      # Corpus self-verification (optarena verify-corpus; see verify.py).
      # Not used at run time - they prove the oracle discriminates.
      "reference_solution": {relpath: content},   # must PASS the full oracle
      "broken_solutions": [{                      # each must FAIL it
          "name":  str,                           # e.g. "vacuous-tests"
          "files": {relpath: content}
      }],

      # Benchmark-corpus metadata.
      # All optional, free-form (not validated against a fixed enum) - they
      # power `--language`/`--framework` filters and `optarena list cases`
      # columns, nothing more today.
      "language":   str,          # e.g. "python", "go", "rust"
      "framework":  str,          # e.g. "fastapi", "spring-boot", "gin"
      "domain":     str,          # e.g. "backend", "frontend", "infrastructure"
      "difficulty": int,          # 1 (easy) .. 5 (expert), per the corpus spec
      "task_type":  str,          # e.g. "feature", "bug_fix", "refactoring"
      "tags":       [str, ...]    # free-form labels, e.g. ["rest", "http"]
    }

All assertion keys beyond path_pattern/content_patterns are optional, so
existing cases behave exactly as before. content_patterns/regex_patterns are a
cheap shape check; test_setup_files + check_command is the real behavioral
test (compile/run/assert against actual output), which is what should decide
correctness for anything beyond the most trivial case.

P3-01: this module is now a thin re-export facade. The actual logic (corpus
loading, the workspace-diff oracle, sandboxed check_command execution,
setup_files/setup_repo/disruptions) lives under `optarena/_cases/`, split by
concern - see that package's own docstring for the map. Every name below
kept its original import path (`from .cases import X` / `from ..cases import
X`) so drivers, cli.py, runner.py, and verify.py needed no changes; a handful
of tests that reset the sandbox's module-level cache state import
`optarena._cases._sandbox`/`optarena._cases._snapshot` directly instead of
this facade, since that state now genuinely lives there.
"""

from __future__ import annotations

from ._cases._constants import (
    CASES_DIR, DOCKER_IMAGE_DEFAULT, DOCKERFILE_DIR, DOCKER_IMAGES, IGNORE_DIRS, REPOS_DIR,
)
from ._cases._corpus import dockerfile_for, filter_cases, load_cases
from ._cases._evaluate import diff_stats, evaluate_case, evaluate_case_isolated, trajectory_stats
from ._cases._sandbox import (
    _HARDENING_ARGS,
    GHCR_PREFIX,
    DockerSandbox,
    _WorkspaceQuotaWatchdog,
    _worker_sandboxes,
    classify_failure,
    container_engine,
    docker_image_available,
    docker_image_pull,
    ensure_image,
    reset_engine_health_cache,
    reset_pull_backoff,
    resolve_image,
    run_capture,
    run_check_command,
    sandbox_user_configured,
    relax_workspace_permissions,
    set_image_overrides,
    validate_image_ref,
)
from ._cases._snapshot import (
    baseline_incompatible,
    changed_files,
    check_expected,
    normalize_workspace_line_endings,
    path_pattern_matches,
    snapshot,
)
from ._cases._workspace_setup import (
    apply_all_disruptions,
    apply_disruptions,
    copy_setup_repo,
    git_init_workspace,
    prepare_workspace,
    write_setup_files,
)

__all__ = [
    "CASES_DIR", "DOCKER_IMAGE_DEFAULT", "DOCKERFILE_DIR", "DOCKER_IMAGES", "IGNORE_DIRS", "REPOS_DIR",
    "dockerfile_for", "filter_cases", "load_cases",
    "diff_stats", "evaluate_case", "evaluate_case_isolated", "trajectory_stats",
    "_HARDENING_ARGS", "GHCR_PREFIX", "DockerSandbox", "_WorkspaceQuotaWatchdog", "_worker_sandboxes",
    "classify_failure", "container_engine", "docker_image_available", "docker_image_pull",
    "ensure_image", "reset_engine_health_cache", "reset_pull_backoff", "resolve_image",
    "run_capture", "run_check_command", "sandbox_user_configured", "relax_workspace_permissions",
    "set_image_overrides", "validate_image_ref",
    "baseline_incompatible", "changed_files", "check_expected", "normalize_workspace_line_endings",
    "path_pattern_matches", "snapshot",
    "apply_all_disruptions", "apply_disruptions", "copy_setup_repo", "git_init_workspace",
    "prepare_workspace", "write_setup_files",
]
