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
        container-sandboxed) must PASS it.

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

import shutil
import tempfile
from pathlib import Path

from .cases import (
    DOCKER_IMAGE_DEFAULT, DockerSandbox, apply_all_disruptions, evaluate_case,
    prepare_workspace, relax_workspace_permissions, resolve_image, write_setup_files,
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
    relax_workspace_permissions(ws)   # A-36: no-op unless OPTARENA_SANDBOX_USER is set
    prepare_workspace(ws, case)
    # Dynamic cases: apply every disruption to reach the fully-perturbed final
    # world, THEN lay the variant's solution over it - so a reference solution is
    # proven correct *through* the disruption (must hold in the changed world),
    # and a "didn't adapt" broken/unmodified variant fails there.
    apply_all_disruptions(case, ws)
    if files is None:                    # implicit "unmodified" variant
        created: list[str] = []
    else:
        # The variant's own file list, not a snapshot diff: a broken variant
        # may be byte-identical to a setup file (e.g. a slow-but-correct perf
        # variant), and it must still be treated as "the model wrote this"
        # so the real check_command judges it - not the expected-file check.
        write_setup_files(ws, files)
        created = sorted(files.keys())
    failures, _info = evaluate_case(case, created, ws)
    return failures


def cases_without_failing_variant(cases: list[dict]) -> list[str]:
    """
    A-31: cases whose verification can only ever prove the oracle can PASS.

    A case with a `reference_solution` but no `broken_solutions` - and no
    implicit "unmodified" variant, which only applies to the
    MUST_FAIL_UNMODIFIED task types - has nothing that must FAIL. Verifying it
    therefore cannot detect the failure mode this whole module exists for: an
    oracle that accepts everything (a glob that never matches, a check_command
    that exits 0 regardless, a vacuous test). `optarena cases verify --strict`
    turns this into a hard error so new cases can't be added without a
    discriminating variant.
    """
    weak = []
    for case in cases:
        variants = variants_for(case)
        if variants and not any(not expect_pass for _n, _f, expect_pass in variants):
            weak.append(case["name"])
    return weak


def verify_cases(cases: list[dict], root: Path | None = None) -> tuple[list[str], int, int]:
    """
    Verify every declared variant of *cases* against the real oracle.
    Returns (violations, variants_checked, cases_skipped). A violation is a
    reference that failed or a broken/unmodified variant that passed.
    """
    todo = [(c, variants_for(c)) for c in cases]
    skipped = sum(1 for _c, v in todo if not v)
    todo = [(c, v) for c, v in todo if v]

    # H-11: only a mkdtemp workspace WE created here is ours to clean up in
    # the finally below; a caller-supplied root is the caller's to manage.
    owns_workspace = root is None
    root = root or Path(tempfile.mkdtemp(prefix="optarena_verify_"))
    root.mkdir(parents=True, exist_ok=True)

    # One sandbox per distinct image any verified case needs, but only ONE
    # ALIVE AT A TIME (grouped by image, not all started up front) - the
    # corpus now spans 9 per-language images, each started with `--memory
    # 2g --cpus 2`; starting all 9 simultaneously for the whole verify run
    # (most of it spent on cases that don't even need most of those images)
    # requests 18 CPUs/18GiB concurrently, which is fine on a beefy dev
    # machine but reliably destabilizes a resource-constrained CI runner -
    # confirmed live: the exact cases that failed with "Permission denied"/
    # "No such container" under a full 9-image `cases verify` run passed
    # cleanly when re-run in isolation (one image, one sandbox). Grouping by
    # image keeps the original optimization (every case sharing an image
    # still execs into ONE container, not one per check_command) while
    # capping concurrent sandboxes at 1.
    import os

    def _image_for(case: dict) -> "str | None":
        # A-15: through resolve_image, like every other image-resolution site
        # (runner.build_manifest, runner.run_scenario, run_check_command) -
        # this was the one path that read the fields directly, so it saw
        # neither F-15 image_overrides nor A-05's reference validation.
        if not case.get("check_command"):
            return None
        return resolve_image(
            case.get("image") or os.environ.get("OPTARENA_SANDBOX_IMAGE", DOCKER_IMAGE_DEFAULT))

    grouped: dict[str, list] = {}
    ungrouped: list = []
    for case, variants in todo:
        image = _image_for(case)
        (grouped.setdefault(image, []) if image else ungrouped).append((case, variants))

    violations: list[str] = []
    checked = 0
    try:
        def _run_group(group: list) -> None:
            nonlocal checked
            for case, variants in group:
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

        for image in sorted(grouped):
            sandbox = DockerSandbox(root, image=image)
            try:
                sandbox.start()
                _run_group(grouped[image])
            finally:
                sandbox.stop()
        _run_group(ungrouped)
    finally:
        # H-11: the per-variant workspaces under our mkdtemp root are never
        # read again once verification reports - remove them so a full
        # verify-corpus run doesn't leak hundreds of MB of scratch per call.
        if owns_workspace:
            shutil.rmtree(root, ignore_errors=True)
    return violations, checked, skipped
