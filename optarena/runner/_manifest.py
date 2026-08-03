"""Building a run's manifest: the immutable identity of WHAT a run
measured (case set, oracle version, trial count, driver/image versions)."""

from __future__ import annotations

import hashlib
import json
import os

from .. import __version__
from ..cases import DOCKER_IMAGE_DEFAULT, container_engine, resolve_image
from ..drivers import get_driver_version
from ..scenario import Scenario

# Bump ONLY when a change to the oracle (cases.evaluate_case / check_expected /
# run_check_command, or the sandbox contract) changes what a pass/fail MEANS -
# so a comparison across that boundary is flagged non-equivalent rather than
# silently treating old and new verdicts as interchangeable (M-01/M-02).
ORACLE_VERSION = 1


def _source_revision() -> dict:
    """
    P2-04: best-effort git identity of the OptArena checkout that produced
    this run - a manifest recorded commit/oracle_version/case_set_hash but
    not which exact revision of the tool itself ran, so two results built
    from different uncommitted states of the same package version were
    indistinguishable. `dirty` matters as much as the commit: a run made
    against local edits is not reproducible from the commit hash alone.
    Never raises - a non-git install (a built wheel, a source tarball)
    reports both fields as None rather than failing the run.
    """
    import subprocess
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent
    try:
        commit = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if commit.returncode != 0:
            return {"git_commit": None, "git_dirty": None}
        dirty = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        return {
            "git_commit": commit.stdout.strip(),
            "git_dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
        }
    except (OSError, subprocess.TimeoutExpired):
        return {"git_commit": None, "git_dirty": None}


def _platform_info() -> dict:
    """P2-04: a manifest recorded no platform info at all - a Windows-vs-Linux
    or Python-version difference can change a case's outcome (line-ending
    handling, timing) but was invisible when comparing two runs later."""
    import platform
    import sys
    return {
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
    }


def _image_digests(images: list[str]) -> dict[str, str]:
    """
    Best-effort map of image tag -> repo digest (H-03): tags like `:latest`
    are mutable, so a manifest recording only the tag can't prove WHICH image
    bits actually graded a run. Digests are recorded for evidence/reproduction
    but deliberately NOT part of `manifest_compatibility` - two machines with
    byte-different local builds of the same image should warn a human, not
    hard-block a comparison. Empty entries (image not present locally,
    container engine down) record as "unknown".
    """
    import subprocess
    out: dict[str, str] = {}
    for image in images:
        if image == "host":
            continue
        try:
            proc = subprocess.run(
                [container_engine(), "image", "inspect", "--format",
                 "{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Id}}{{end}}", image],
                capture_output=True, text=True, timeout=10,
            )
            digest = proc.stdout.strip() if proc.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            digest = ""
        out[image] = digest or "unknown"
    return out


def build_manifest(scenario: Scenario, cases: list[dict], requested_trials: int,
                    runner_trials: "int | None" = None) -> dict:
    """
    Immutable identity of WHAT a run measured (M-01): the exact resolved case
    set (by name + a content hash of each case, so any edit to a case's
    prompts/oracle/setup invalidates cross-run equivalence), the oracle
    version, the trial count, and the driver/backend/images in play. Two runs
    are only strictly comparable when their (oracle_version, case_set_hash,
    trials) agree - `compare.manifest_compatibility` enforces that. Driver and
    backend intentionally do NOT gate comparability: tool-vs-tool and
    backend-vs-backend are the whole point of a run comparison.

    F-09: ``trials`` records what was REQUESTED (semantically "how many
    times was each case attempted"), not runner.py's own local loop count -
    for a caching driver, the runner hands the count TO the driver and runs
    its own per-case loop exactly once, but the run still semantically has
    N trials and must compare against another N-trial run as such.
    ``runner_trials`` (defaults to ``requested_trials`` when not given)
    separately records how many times the RUNNER's own loop executed, for
    anyone who wants to know the mechanism, not just the semantic count.
    """
    case_entries = sorted(
        (c["name"],
         hashlib.sha1(json.dumps(c, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest())
        for c in cases
    )
    case_set_hash = hashlib.sha1(
        json.dumps(case_entries, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    images = sorted({
        resolve_image(c.get("image") or os.environ.get("OPTARENA_SANDBOX_IMAGE", DOCKER_IMAGE_DEFAULT))
        for c in cases if c.get("check_command")
    })
    manifest = {
        "manifest_version": 1,
        "oracle_version": ORACLE_VERSION,
        # A-25: which build of the tool produced this result. Recorded for
        # evidence only - deliberately NOT part of `manifest_compatibility`,
        # since a patch release that doesn't touch the oracle must not
        # invalidate comparisons (that's exactly what `oracle_version` is for).
        "optarena_version": __version__,
        "case_count": len(cases),
        "case_names": [n for n, _ in case_entries],
        "case_set_hash": case_set_hash,
        "trials": requested_trials,
        "runner_trials": requested_trials if runner_trials is None else runner_trials,
        "driver": scenario.driver,
        # P2-04: the external CLI binary/SDK package version this run
        # actually drove - not part of manifest_compatibility (like
        # optarena_version above, a tool upgrade doesn't change what the
        # oracle measures), but exactly the piece that changes silently
        # underneath a comparison otherwise (an `aider` pip upgrade, an SDK
        # bump) with no other record of it happening. None for a driver
        # with no external tool/SDK (the baseline drivers), or best-effort
        # unavailable (binary not on PATH, package not installed).
        "driver_version": get_driver_version(scenario.driver),
        "backend_model": scenario.backend.model,
        "backend_base_url": scenario.backend.base_url,
        "images": images or ["host"],
        "image_digests": _image_digests(images),
    }
    manifest.update(_source_revision())
    manifest.update(_platform_info())
    return manifest
