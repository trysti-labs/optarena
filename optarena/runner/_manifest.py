"""Building a run's manifest: the immutable identity of WHAT a run
measured (case set, oracle version, trial count, driver/image versions)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .. import __version__
from ..cases import DOCKER_IMAGE_DEFAULT, container_engine, resolve_image
from ..drivers import get_driver_version
from ..scenario import Scenario

# P2-02: decision procedure for when to bump this, not just a promise to
# someday follow one. Bump when, and only when, a change could make the SAME
# case produce a DIFFERENT verdict than before, for a case that was already
# valid and already comparable - not every change to the surrounding code.
#
#   Bump:
#     - `evaluate_case`/`check_expected`/`run_check_command` scoring logic
#       changes (a pass becomes a fail or vice versa for unchanged input).
#     - The sandbox contract narrows what a check_command is ALLOWED to do
#       (a new resource limit, timeout, or containment rule that can abort a
#       command that previously ran to completion) - the case's OWN behavior
#       didn't change, but what "ran successfully" now requires did.
#     - Snapshot/diff semantics change what counts as a "changed file".
#
#   Do NOT bump:
#     - `schema.py` validation getting stricter in a way that only rejects
#       cases that were already exploiting a gap (a traversal path, an
#       unsafe disruption trigger) - such a case could never have produced a
#       meaningful, comparable verdict in the first place; it now fails to
#       LOAD, not fails to PASS, and no valid case's outcome changes.
#     - Redaction/logging/reporting changes - they touch what's recorded
#       ABOUT a run, not what the run measured.
#     - New optional case fields, new drivers, new backends - additive,
#       nothing existing changes meaning.
#
# History:
#   1 -> 2 (2026-08-03, P1-07): `_WorkspaceQuotaWatchdog` can now abort an
#   in-flight check_command that crosses OPTARENA_WORKSPACE_MAX_BYTES/_FILES
#   (default 4 GiB / 50,000 files) - the sandbox contract changed from
#   "workspace disk is unbounded" to "workspace disk is capped", which is a
#   real (if currently theoretical for the built-in corpus, where no case
#   approaches the default quota) change in what "the check_command ran
#   successfully" can mean for a resource-heavy case. P1-04/P1-05 (schema
#   containment/normalization hardening, same pass) do NOT bump this by the
#   rule above - both only reject cases that were already unsafe/invalid,
#   never change the verdict of a case that validates both before and after.
ORACLE_VERSION = 2


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
    repo_root = Path(__file__).resolve().parent.parent.parent
    try:
        commit = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if commit.returncode == 0:
            dirty = subprocess.run(
                ["git", "-C", str(repo_root), "status", "--porcelain"],
                capture_output=True, text=True, timeout=5,
            )
            return {
                "git_commit": commit.stdout.strip(),
                "git_dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
            }
    except (OSError, subprocess.TimeoutExpired):
        pass
    # P2-02: no live git checkout (an installed wheel has no .git dir at
    # all) - fall back to the commit CI embedded at build time, if any (see
    # scripts/embed_build_commit.py). `git_dirty` has no meaning for a wheel
    # (there is no working tree to be dirty), so it stays None rather than
    # False - False would wrongly claim "known clean".
    build_commit_file = Path(__file__).resolve().parent.parent / "_build_commit.txt"
    try:
        embedded = build_commit_file.read_text(encoding="utf-8").strip()
        if embedded:
            return {"git_commit": embedded, "git_dirty": None}
    except OSError:
        pass
    return {"git_commit": None, "git_dirty": None}


def _provider_version(backend) -> "str | None":
    """
    P2-02: best-effort backend server/provider version - a manifest recorded
    the model name and base URL, but not which build of the server actually
    answered, so a silent Ollama upgrade between two runs was invisible.
    Only Ollama exposes a real, standardized version endpoint (`GET
    /api/version`); other OpenAI-compatible servers (vLLM, LiteLLM proxies,
    hosted APIs) have no uniform equivalent to query, so this stays honestly
    `None` there rather than guessing from a header. Never raises and never
    blocks the run for more than a couple seconds - a manifest field is not
    worth a hung run over.
    """
    if backend.kind != "ollama":
        return None
    import urllib.error
    import urllib.request
    try:
        url = f"{backend.base_url.rstrip('/')}/api/version"
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read(4096))
        version = data.get("version")
        return str(version) if version else None
    except (OSError, urllib.error.URLError, ValueError, TimeoutError):
        return None


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


def _pack_info(cases_dir: "str | None") -> "dict | None":
    """
    P1-06: when a run's cases came from an installed pack (``--pack``,
    which resolves to that pack's on-disk directory before the Scenario is
    even built - see cli's ``cmd_run``), record its identity - name,
    version, content hash, and signature/trust state - so a saved result
    can answer "which pack, from whom, verified how" without the caller
    needing to remember. Detected structurally (a ``_pack.json`` install
    manifest sitting in ``cases_dir``, written by ``packs.install_pack``)
    rather than threaded as a separate parameter, so this also covers a
    scenario file that points ``cases_dir`` directly at an installed pack
    path, not just the ``--pack`` CLI shorthand. Returns ``None`` (not an
    error) for the common case of cases NOT coming from a pack at all.

    P1-10: delegates to ``packs.verify_installed_pack`` rather than reading
    ``_pack.json``'s ``verification`` field directly - that field was a
    stale, install-time-only snapshot, never re-derived from what's
    actually on disk, so a case file edited after installation still
    reported "trusted" in every subsequent run's manifest. Every run now
    gets a freshly re-hashed verdict instead.
    """
    if not cases_dir:
        return None
    from ..packs import verify_installed_pack
    return verify_installed_pack(cases_dir)


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
    # Sandboxed-real tool-use execution runs the actual reference MCP server,
    # not the in-process mock - a mock-mode run and a sandboxed-mode run over
    # the identical case set did NOT measure the same thing. The SAME
    # resolve_tool_service_mode tool_chat.py executes decides what's
    # recorded here, so the manifest can never claim a mode the run didn't
    # actually use (S-2: sandboxed is user-granted; a case can only opt
    # down). None (not "mock") when the run has no tool-use cases at all,
    # same reasoning as `images` defaulting to ["host"] only when non-empty.
    from ..cases import resolve_tool_service_mode
    tool_service_modes = sorted({
        resolve_tool_service_mode(c.get("tool_service_mode"), scenario.tool_service_mode)
        for c in cases if c.get("tool_service")
    }) or None
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
        # P2-02: effective generation settings, recorded regardless of
        # whether this run's driver honors them (see Backend.temperature) -
        # "what was configured" is part of the run's identity either way.
        "backend_temperature": scenario.backend.temperature,
        "backend_top_p": scenario.backend.top_p,
        "backend_seed": scenario.backend.seed,
        # P2-02: best-effort provider/server version - see _provider_version().
        "backend_provider_version": _provider_version(scenario.backend),
        "images": images or ["host"],
        "image_digests": _image_digests(images),
        "tool_service_modes": tool_service_modes,
        "pack": _pack_info(scenario.cases_dir),
    }
    manifest.update(_source_revision())
    manifest.update(_platform_info())
    return manifest
