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
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .schema import validate_case, validate_unique_case_names

CASES_DIR = Path(__file__).parent / "cases"
DOCKER_IMAGE_DEFAULT = "optarena-tester:latest"
DOCKERFILE_DIR = Path(__file__).resolve().parent.parent / "docker"

# Defense-in-depth flags applied to every sandbox container that runs a
# case-defined `check_command` (both the shared DockerSandbox and the
# ephemeral per-call fallback) - the command itself, and anything it runs,
# is untrusted (case-authored, and can execute model-generated code). None
# of the corpus's 510 check_commands install packages at runtime (all
# toolchains are baked into the image at build time - verified against the
# whole corpus), so a read-only rootfs + a writable /tmp scratch is
# sufficient; --user (non-root) was deliberately left out here since none of
# the images currently create a matching unprivileged account and it needs
# per-image verification this session didn't have time for.
_HARDENING_ARGS = [
    "--cap-drop", "ALL",
    # `self.root`/the run's workspace is a HOST directory (very often a
    # fresh `tempfile.mkdtemp()`, which Python deliberately creates 0700) bind-
    # mounted in as /workspace; the container still runs as root, and without
    # DAC_OVERRIDE a plain "--cap-drop ALL" root can't read/write files it
    # doesn't own once permission bits don't line up - which they routinely
    # don't, since the host side keeps creating new case/variant directories
    # throughout the run under the runner's own uid. That produced exactly
    # "Permission denied" / "could not find Cargo.toml" (a blocked directory
    # traversal looks identical to "not there" to a tool doing its own upward
    # search) - confirmed live under real Linux Docker (WSL2, GitHub Actions);
    # invisible under Windows/macOS Docker Desktop, whose bind-mount
    # translation layer ignores real Unix permission bits, which is exactly
    # why it went unnoticed through months of local testing. Restoring just
    # this one capability (root's normal, pre-hardening behavior on its own
    # bind mount) fixes the whole class at the source instead of chasing
    # individual directories with chmod; it grants no privilege beyond what
    # root already has outside a container, and every other capability -
    # including anything that could matter for container escape or host
    # interaction - stays dropped.
    "--cap-add", "DAC_OVERRIDE",
    "--security-opt", "no-new-privileges",
    "--pids-limit", "256",
    "--read-only",
    # exec: several tmpfs mount option sets default new tmpfs mounts to
    # noexec, which silently broke `go test` here - it compiles a test
    # binary INTO this tmpfs (via GOCACHE below) and then has to execute it.
    # 1g (not 256m): the Go linker writes its whole output object into this
    # tmpfs and ran out of space at 256m; 1g stays well under the 2g
    # container memory limit (tmpfs usage counts against it) while giving
    # every toolchain's build/scratch output room.
    "--tmpfs", "/tmp:rw,exec,size=1g,mode=1777",
    # go test always compiles before running and writes its build cache to
    # $HOME/.cache/go-build by default - not "installing a package", just how
    # `go test` works at all, and the one thing --read-only broke in a full
    # corpus run (confirmed: every other language's check_command needs only
    # /workspace + /tmp). Redirect it into the writable tmpfs instead of
    # adding a second tmpfs mount; harmless no-op on non-Go images.
    "-e", "GOCACHE=/tmp/go-build",
]


def _writable_cache_args(image: str) -> list[str]:
    """
    Images whose pre-warmed build cache lives on the (now read-only) rootfs and
    which the toolchain must still WRITE to at check_command time. The
    ``--read-only`` hardening otherwise hard-fails these: `cargo` aborts when it
    cannot write ``$CARGO_TARGET_DIR/debug/.cargo-build-lock`` (the rust image
    pre-warms deps at ``/opt/cargo-target``, on the rootfs). An anonymous volume
    at that path is initialized from the image - so the pre-warmed dependency
    artifacts are preserved (a bare tmpfs would hide them and force a full,
    timeout-prone recompile) - yet is writable; ``docker run --rm`` removes the
    anonymous volume when the container goes away. Maven (writes a cosmetic log
    to ``/root/.m2`` but "carries on") and dotnet (writes bin/obj into the
    writable ``/workspace``) do NOT need this - only rust does.
    """
    return ["-v", "/opt/cargo-target"] if "rust" in image.lower() else []


def _sandbox_user_args() -> list[str]:
    """
    Optional non-root sandbox execution (M-10): OPTARENA_SANDBOX_USER=uid:gid
    (e.g. "1000:1000") runs every sandbox container as that user, with HOME
    pointed at the writable tmpfs so toolchains that write dotfiles/caches
    still work. Opt-in rather than default because none of the published
    images create a matching account and each toolchain needs validation
    under a non-root uid (mvn/dotnet/cargo cache paths) - flip it on, run
    `optarena cases verify --language <x>`, and report breakage.
    """
    user = os.environ.get("OPTARENA_SANDBOX_USER")
    return ["--user", user, "-e", "HOME=/tmp"] if user else []

# Registry of every sandbox image OptArena knows how to build, keyed by the
# short name used with `optarena sandbox build --lang <key>`. "base" is the
# original combined image (gcc + python3 + node) and stays the default for
# cases with no `image` set, so the 7 original cases are unaffected.
# Per-language images add a framework's dependencies pre-fetched at build
# time (the sandbox runs with --network none, so nothing can be installed
# at check_command time - it must already be in the image).
DOCKER_IMAGES: dict[str, str] = {
    "base": DOCKER_IMAGE_DEFAULT,
    "python": "optarena-tester-python:latest",
    "node": "optarena-tester-node:latest",
    "jvm": "optarena-tester-jvm:latest",
    "go": "optarena-tester-go:latest",
    "rust": "optarena-tester-rust:latest",
    "dotnet": "optarena-tester-dotnet:latest",
    "php": "optarena-tester-php:latest",
    "ruby": "optarena-tester-ruby:latest",
}


def dockerfile_for(lang: str) -> Path:
    """Path to the Dockerfile for a `DOCKER_IMAGES` key. "base" lives directly
    under docker/ (the original combined image); every other track gets its
    own docker/<lang>/ subdirectory."""
    return DOCKERFILE_DIR / "Dockerfile" if lang == "base" else DOCKERFILE_DIR / lang / "Dockerfile"


def load_cases(names: list[str] | None = None, cases_dir: "Path | str | None" = None) -> list[dict]:
    """Load all (or the named) cases from *cases_dir*, sorted by filename.

    Every case is structurally validated (schema.validate_case) as it's
    loaded - a malformed/fuzzed case file fails fast here, with a file+key
    error, rather than surfacing later as an unclear KeyError/TypeError deep
    inside a driver (after a real backend call may already have run).
    """
    directory = Path(cases_dir) if cases_dir else CASES_DIR
    cases = []
    for p in sorted(directory.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        validate_case(data, source=str(p))
        cases.append(data)
    validate_unique_case_names(cases, source=str(directory))
    if names is not None:
        # `names == []` (e.g. a --language filter that matched nothing) must
        # mean "run none of them" - not "no filter" (an empty list is falsy
        # in Python, so `if names:` would silently fall through to "all").
        wanted = {n.strip() for n in names}
        cases = [c for c in cases if c["name"] in wanted]
        missing = wanted - {c["name"] for c in cases}
        if missing:
            raise FileNotFoundError(f"Unknown case(s): {', '.join(sorted(missing))}")
    return cases


def filter_cases(cases: list[dict], *, language: str | None = None, framework: str | None = None) -> list[dict]:
    """Narrow a loaded case list by `language`/`framework` tags (AND'd together)."""
    out = cases
    if language is not None:
        out = [c for c in out if c.get("language") == language]
    if framework is not None:
        out = [c for c in out if c.get("framework") == framework]
    return out


# ── Filesystem oracle (shared by drivers that verify via workspace diff) ──────

IGNORE_DIRS = {".git", ".vscode", ".aider", "node_modules", "__pycache__"}


def snapshot(root: Path) -> dict[str, str]:
    """Map each file (relative posix path) to a content signature (sha1).

    Content-based rather than size:mtime - a same-size rewrite landing within
    one filesystem-timestamp tick (routine in verify-corpus, which writes the
    setup file and the solution file back-to-back) was invisible to the old
    signature, making changed-file detection flaky on coarse-mtime
    filesystems (Windows hosts).
    """
    out: dict[str, str] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in IGNORE_DIRS for part in p.relative_to(root).parts):
            continue
        # aider writes .aider.chat.history.md etc. as FILES (IGNORE_DIRS only
        # covers directories) - tool bookkeeping, not model output; keep them
        # out of the diff so they don't inflate the files/lines metrics.
        if p.name.startswith(".aider"):
            continue
        try:
            digest = hashlib.sha1(p.read_bytes()).hexdigest()
        except OSError:
            continue        # vanished/locked mid-scan - treat as absent
        out[p.relative_to(root).as_posix()] = digest
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
        for needle in spec.get("not_content_patterns", []):
            if str(needle).lower() in content:
                failures.append(f'"{match}" contains forbidden content "{needle}"')
        for pattern_re in spec.get("regex_patterns", []):
            try:
                if not re.search(pattern_re, content, re.IGNORECASE):
                    failures.append(f'"{match}" does not match regex "{pattern_re}"')
            except re.error as exc:
                failures.append(f'invalid regex "{pattern_re}": {exc}')
        min_lines = spec.get("min_lines")
        if isinstance(min_lines, int) and min_lines > 0:
            n_lines = len(content.splitlines())
            if n_lines < min_lines:
                failures.append(f'"{match}" has {n_lines} line(s), expected >= {min_lines}')
    return failures


_engine_checked = False
_engine_bin = "docker"


def container_engine() -> str:
    """
    Resolve which container engine binary every sandbox call shells out to.
    Podman implements the same CLI surface this project relies on (`run`,
    `exec`, `pull`, `tag`, `stop`, `rm`, and every hardening flag in
    ``_HARDENING_ARGS``) - verified directly against the published sandbox
    images, not just read off Podman's docs.

    ``OPTARENA_CONTAINER_ENGINE=docker|podman`` forces a specific binary;
    otherwise this auto-detects, preferring `docker` when both are on PATH
    (matches what most of this project's own testing has exercised). Cached
    for the process, like ``_docker_available()``.
    """
    global _engine_checked, _engine_bin
    if _engine_checked:
        return _engine_bin
    _engine_checked = True
    override = os.environ.get("OPTARENA_CONTAINER_ENGINE")
    if override in ("docker", "podman"):
        _engine_bin = override
        return _engine_bin
    for candidate in ("docker", "podman"):
        if shutil.which(candidate):
            _engine_bin = candidate
            return _engine_bin
    _engine_bin = "docker"  # nothing on PATH; keep prior (docker-branded) error text accurate
    return _engine_bin


_docker_checked_at: float = -1.0  # monotonic timestamp of the last probe; -1 = never probed
_docker_ok = False
_docker_warned = False
# F-16: a bare per-process boolean cache meant one transient failure (Docker
# Desktop/`podman machine` still waking up) marked the engine "unavailable"
# for the rest of the process - every later scenario in a --matrix-drivers
# run would then skip the sandbox entirely. A short TTL lets a later probe
# recover once the engine actually comes up, without re-probing on every
# single case (`docker info` is not free).
_ENGINE_HEALTH_TTL_S = 20.0


def _docker_available(*, force_recheck: bool = False) -> bool:
    """Cached (TTL'd) check for a reachable container engine daemon.

    ``force_recheck=True`` bypasses the TTL - used at the start of each
    scenario in a multi-scenario run so a matrix run doesn't stay convinced
    the engine is down for its whole duration just because it was still
    starting up when scenario 1 probed it.
    """
    global _docker_checked_at, _docker_ok
    now = time.monotonic()
    if not force_recheck and _docker_checked_at >= 0 and (now - _docker_checked_at) < _ENGINE_HEALTH_TTL_S:
        return _docker_ok
    _docker_checked_at = now
    try:
        proc = subprocess.run(
            [container_engine(), "info"], capture_output=True, timeout=10,
        )
        _docker_ok = proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        _docker_ok = False
    return _docker_ok


def reset_engine_health_cache() -> None:
    """Force the next `_docker_available()` call to re-probe regardless of
    TTL. Called once per scenario by `runner.run_scenario` (F-16) so a
    transient failure early in a matrix/multi-scenario run doesn't suppress
    the sandbox for every later scenario even after the engine recovers."""
    global _docker_checked_at
    _docker_checked_at = -1.0


def docker_image_available(image: str = DOCKER_IMAGE_DEFAULT) -> bool:
    try:
        proc = subprocess.run(
            [container_engine(), "image", "inspect", image],
            capture_output=True, timeout=10,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# Published copies of the sandbox images, so first-run users pull in minutes
# instead of building ~7GB of toolchains locally. Local tags stay the plain
# `optarena-tester*` names; the pull tags the remote image back to that name.
#
# `image` here already carries whatever tag the caller asked for - the
# default is the mutable `:latest`, but publish-images.yml also pushes an
# immutable `:<git-sha>` tag for every build (Phase 2.3 - content-addressed
# images). Pin to a known-good build with e.g.
# OPTARENA_SANDBOX_IMAGE=optarena-tester:<sha> (or an `image` field in a
# case JSON) - no code change needed, this already pulls whatever tag it's given.
GHCR_PREFIX = "ghcr.io/trysti-labs/optarena/"
# F-16: previously a plain add-only set - one failed pull (a blip, not a
# permanent registry problem) meant "never try again this process," even for
# a transient network error. Track attempt count + last-attempt time per
# image instead, so a *bounded* number of retries with exponential backoff
# can recover from a blip while still giving up for real on a genuinely
# unpublished/unreachable image rather than retrying forever.
_PULL_MAX_ATTEMPTS = 3
_pull_attempts: dict[str, int] = {}
_pull_last_attempt_at: dict[str, float] = {}


def docker_image_pull(image: str) -> bool:
    """Pull GHCR_PREFIX+image and tag it as the local name. Up to
    ``_PULL_MAX_ATTEMPTS`` attempts per image per process with exponential
    backoff between them; disable entirely with OPTARENA_DISABLE_PULL=1."""
    if os.environ.get("OPTARENA_DISABLE_PULL") == "1":
        return False
    attempts = _pull_attempts.get(image, 0)
    if attempts >= _PULL_MAX_ATTEMPTS:
        return False
    if attempts > 0:
        backoff = min(2 ** attempts, 30)  # 2s, 4s, ... capped at 30s
        elapsed = time.monotonic() - _pull_last_attempt_at.get(image, 0.0)
        if elapsed < backoff:
            return False  # still within this image's backoff window
    _pull_attempts[image] = attempts + 1
    _pull_last_attempt_at[image] = time.monotonic()
    remote = GHCR_PREFIX + image
    engine = container_engine()
    print(f"[optarena] image '{image}' not built locally - trying `{engine} pull {remote}` "
          f"(attempt {attempts + 1}/{_PULL_MAX_ATTEMPTS}) ...", file=sys.stderr)
    try:
        # 5-minute cap: this is a first-run convenience, not a build step. A
        # registry that can't serve the image by then (unpublished repo, slow
        # link, waking the engine's VM) must not stall the whole run - explicit
        # `optarena sandbox pull` / `optarena sandbox build` remain available.
        proc = subprocess.run([engine, "pull", remote], capture_output=True, timeout=300)
        if proc.returncode != 0:
            tail = (proc.stderr or b"").decode(errors="replace").strip()[-200:]
            print(f"[optarena] pull failed ({tail})"
                  + (" - retrying" if attempts + 1 < _PULL_MAX_ATTEMPTS else
                     " - build locally with `optarena sandbox build`"),
                  file=sys.stderr)
            return False
        tag_proc = subprocess.run([engine, "tag", remote, image], capture_output=True, timeout=30)
        if tag_proc.returncode != 0:
            tail = (tag_proc.stderr or b"").decode(errors="replace").strip()[-200:]
            print(f"[optarena] pulled {remote} but `{engine} tag` failed ({tail})", file=sys.stderr)
            return False
        print(f"[optarena] pulled {remote} -> {image}", file=sys.stderr)
        return True
    except (OSError, subprocess.TimeoutExpired):
        print(f"[optarena] pull of {remote} timed out"
              + (" - retrying" if attempts + 1 < _PULL_MAX_ATTEMPTS else
                 " - build locally with `optarena sandbox build`"),
              file=sys.stderr)
        return False


# F-15: run-scoped image-pinning override, mirroring `_active_sandboxes`'
# own run-scoped-global pattern - set once by `runner.run_scenario()` so
# every driver's evaluate_case()->run_check_command() call picks it up
# without threading a new parameter through every driver module.
_image_overrides: dict[str, str] = {}


def set_image_overrides(overrides: "dict[str, str] | None") -> None:
    global _image_overrides
    _image_overrides = dict(overrides) if overrides else {}


def resolve_image(image: str) -> str:
    """Apply the active run's `image_overrides` (F-15) to a resolved image
    name, if any - matched either by the exact image reference or by
    `DOCKER_IMAGES` short track name (e.g. "python"), so a scenario can pin
    either a specific unusual `image` value or a whole registered
    track without knowing every case's exact tag."""
    if not _image_overrides:
        return image
    if image in _image_overrides:
        return _image_overrides[image]
    for key, val in DOCKER_IMAGES.items():
        if val == image and key in _image_overrides:
            return _image_overrides[key]
    return image


def ensure_image(image: str) -> bool:
    """Local image, or a successful GHCR pull tagged to the local name.

    The availability probe is retried once: right after the container
    engine's VM wakes from an idle/resource-saver state (Docker Desktop,
    `podman machine`), the first `image inspect` can exceed its timeout, and
    misreading that as "image missing" would trigger a pointless (and
    possibly slow) registry pull for an image that's already local.
    """
    if docker_image_available(image) or docker_image_available(image):
        return True
    return docker_image_pull(image) and docker_image_available(image)


# Keyed by Docker image tag rather than a single slot - a run whose cases
# span multiple languages (e.g. --cases includes both a Python and a Go
# case) needs one long-lived container PER distinct image, not one overall.
# This is the SERIAL-execution registry (one sandbox per image, shared by
# every case). F-06's --parallel worker pool uses a separate, per-thread
# registry instead (`_worker_sandboxes` below) so concurrent workers never
# `exec` into the same container.
_active_sandboxes: dict[str, "DockerSandbox"] = {}

# F-06: per-worker-thread sandbox routing for --parallel. Each parallel
# worker thread binds its OWN dedicated {image: DockerSandbox} map here at
# the start of its work (see runner.py's worker-pool implementation) so
# `run_check_command` routes each case to a container that only THAT thread
# ever execs into - concurrent `exec` calls into one shared container would
# otherwise collide in its process table/network namespace, and a
# timeout-triggered `reap()` (`kill -9 -1`) would kill every other worker's
# in-flight process too, not just the one that actually timed out.
_worker_sandboxes = threading.local()


def _active_sandbox_for(image: str) -> "DockerSandbox | None":
    """The sandbox `run_check_command` should use for `image`: this
    thread's own worker-pool binding if one is set (--parallel), else the
    single shared serial-run registry."""
    worker_map = getattr(_worker_sandboxes, "map", None)
    if worker_map is not None:
        return worker_map.get(image)
    return _active_sandboxes.get(image)


class DockerSandbox:
    """
    One long-lived container for an entire ``optarena run`` - every case and
    every trial execs into the SAME container via ``docker exec``, instead of
    a fresh ``docker run`` per check_command (that was the bug: 7 cases x 3
    trials meant 21 containers started and torn down for one run). ``root``
    (the run's whole temp workspace, parent of every case/trial subdirectory)
    is bind-mounted once at container start; each call execs with the
    working directory set to that case's subdirectory under the same mount.

    A run may need several of these at once - one per distinct ``image`` -
    when its cases span more than one language/framework track; each one
    registers itself in ``_active_sandboxes`` under its own image tag so
    ``run_check_command`` can route each case to the container that actually
    has its toolchain.

    Used as a context manager around the whole run (see ``runner.py``):
    ``with DockerSandbox(root) as sandbox:``. If no container engine is
    available (or ``OPTARENA_DISABLE_SANDBOX=1``), ``start()`` is a no-op and
    callers fall back to running check_command on the host - unchanged from before.
    """

    def __init__(self, root: Path, image: str | None = None):
        self.root = root.resolve()
        self.image = image or os.environ.get("OPTARENA_SANDBOX_IMAGE", DOCKER_IMAGE_DEFAULT)
        self.name = f"optarena-sandbox-{uuid.uuid4().hex[:12]}"
        self.active = False

    def start(self) -> bool:
        if os.environ.get("OPTARENA_DISABLE_SANDBOX") == "1" or not _docker_available():
            return False
        if not ensure_image(self.image):
            print(
                f"[optarena] Container image '{self.image}' not found - check_command "
                f"will run on the host. Run `optarena sandbox build` to build the "
                f"sandboxed test image.",
                file=sys.stderr,
            )
            return False
        engine = container_engine()
        try:
            subprocess.run(
                [engine, "run", "-d", "--rm", "--name", self.name,
                 "--network", "none", "--memory", "2g", "--cpus", "2",
                 *_HARDENING_ARGS, *_sandbox_user_args(), *_writable_cache_args(self.image),
                 "-v", f"{self.root}:/workspace", "-w", "/workspace",
                 self.image, "sleep", "infinity"],
                capture_output=True, timeout=20, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            print(f"[optarena] could not start {engine} sandbox: {exc}", file=sys.stderr)
            return False
        self.active = True
        _active_sandboxes[self.image] = self
        print(f"[optarena] {engine} sandbox: {self.name} (image {self.image}) - "
              f"one container for this whole run")
        return True

    def stop(self) -> None:
        # F-16: both calls are bounded so a hung engine CLI (not just a hung
        # container - the `-t 2` grace period only bounds the CONTAINER's
        # shutdown, not the `docker`/`podman stop` client process itself)
        # can't hang the whole run's cleanup. Best-effort throughout: this is
        # cleanup, called from a `finally`, and must never raise - a failed
        # stop/rm here just means a leftover `--rm` container the engine's
        # own garbage collection (or the next `sandbox build`) will reclaim.
        if self.active:
            engine = container_engine()
            try:
                subprocess.run([engine, "stop", "-t", "2", self.name],
                                capture_output=True, timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    subprocess.run([engine, "rm", "-f", self.name],
                                    capture_output=True, timeout=15)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self.active = False
        if _active_sandboxes.get(self.image) is self:
            del _active_sandboxes[self.image]

    def __enter__(self) -> "DockerSandbox":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()

    def exec(self, cmd: str, case_root: Path, timeout: int) -> subprocess.CompletedProcess:
        rel = case_root.resolve().relative_to(self.root).as_posix()
        # `timeout` runs *inside* the container (coreutils, always present in
        # the base image) so a slow/hung test is killed in its own namespace
        # rather than leaving an orphaned process for the shared container to
        # carry into the next case.
        full_cmd = [
            container_engine(), "exec", "-w", f"/workspace/{rel}", self.name,
            "timeout", f"{timeout}s", "sh", "-c", cmd,
        ]
        return subprocess.run(
            full_cmd, capture_output=True, text=True,
            timeout=timeout + 10, encoding="utf-8", errors="replace",
        )

    def reap(self) -> None:
        """
        Kill every stray process left in the shared container (except its
        PID-1 `sleep infinity`). Needed after a check_command timeout:
        `timeout` TERMs the `sh`/test process, but SIGTERM does NOT run a
        Python test script's `finally:` block, so a server it Popen'd (java
        -jar, a cargo binary...) survives and keeps holding its port - which
        would cascade failures into every later case that reuses the port in
        this same long-lived container. On Linux, `kill -9 -1` signals every
        process the caller may signal except PID 1 and itself.
        """
        if self.active:
            subprocess.run(
                [container_engine(), "exec", self.name, "sh", "-c", "kill -9 -1 2>/dev/null; true"],
                capture_output=True, timeout=10,
            )


def _new_oracle_info(cmd: str | None) -> dict:
    return {
        "check_command": cmd,
        "ran": False,
        "sandbox": None,     # "docker" | "host" | None ("docker" covers Podman too - see "engine")
        "engine": None,      # the actual container engine binary used, e.g. "docker" or "podman"
        "image": None,
        "exit_code": None,
        "duration_s": None,
        "output": "",
        # F-10: True when the environment (not the model/tool's code) is why
        # this failed - the container engine couldn't exec into it, no
        # engine/image was available at all, etc. A real nonzero-exit test
        # failure is NOT an infrastructure error even though it's still a
        # failure - this only marks the case where "failed" would otherwise
        # be indistinguishable from "the model's code was wrong," which
        # silently drags down a measured pass rate for reasons that have
        # nothing to do with what's being evaluated. See metrics.aggregate's
        # `infrastructure_errors`/`adjusted_pass_rate`.
        "infrastructure_error": False,
    }


def _unsafe_host_exec_allowed() -> bool:
    """
    Two distinct, both explicit, opt-ins to running an untrusted
    ``check_command`` directly on the host:

    - ``OPTARENA_DISABLE_SANDBOX=1`` - "I am deliberately disabling the container
      sandbox", already an explicit choice (this project's own test suite
      and CI use it on hosts with no Docker/Podman daemon at all).
    - ``OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1`` - covers the C-02 gap: the
      sandbox was never explicitly disabled, it's just not installed/running, or an
      image is missing. Previously that case *silently* fell through to
      host execution with only a stderr warning - "a warning is not an
      adequate control for arbitrary code execution" (case-defined
      check_command and model-generated code are both untrusted input).
      Fail closed instead unless this is set.
    """
    return (os.environ.get("OPTARENA_DISABLE_SANDBOX") == "1"
            or os.environ.get("OPTARENA_ALLOW_UNSAFE_HOST_EXEC") == "1")


def run_check_command(case: dict, root: Path) -> tuple[list[str], dict]:
    """
    Run the case's optional ``check_command`` and return
    ``(failure_strings, oracle_info)``. Empty failure list = passed or no
    command configured. ``oracle_info`` always reports what actually
    happened (sandboxed in a container vs run on the host, exit code, timing,
    and a tail of captured output) so callers can show it, not just the verdict.

    This is the second, behavioral oracle stage: content patterns assert
    shape, the command actually compiles/runs the code and asserts on its
    real output (see ``test_setup_files``). When an active ``DockerSandbox``
    exists for this case's required image (set up once by
    ``runner.run_scenario`` for the whole run - possibly several, one per
    distinct image a run's cases need), this execs into that ONE shared
    container - it does not start a new one per case/trial. Without a
    matching active sandbox (e.g. ``evaluate_case`` called directly, outside
    the runner), it falls back to one ephemeral ``run --rm`` for this
    call. When no container engine is available or an image is missing, this
    now FAILS CLOSED (C-02) unless the caller has explicitly opted into host execution
    via ``OPTARENA_DISABLE_SANDBOX=1`` or ``OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1`` -
    see ``_unsafe_host_exec_allowed``.
    """
    global _docker_warned
    cmd = case.get("check_command")
    info = _new_oracle_info(cmd)
    if not cmd:
        return [], info
    timeout = int(case.get("check_command_timeout", 60) or 60)
    docker_disabled = os.environ.get("OPTARENA_DISABLE_SANDBOX") == "1"
    unsafe_ok = _unsafe_host_exec_allowed()
    image = resolve_image(case.get("image") or os.environ.get("OPTARENA_SANDBOX_IMAGE", DOCKER_IMAGE_DEFAULT))

    active = _active_sandbox_for(image)
    if not docker_disabled and active is not None:
        return _run_check_command_sandbox(cmd, root, timeout, active, info)

    # `_docker_available()` is short-circuited away entirely when the user
    # opted out - it must not shell out to `<engine> info` in that case.
    use_docker = (not docker_disabled) and _docker_available()
    engine = container_engine()

    if use_docker and not ensure_image(image):
        use_docker = False
        if not _docker_warned:
            print(
                f"[optarena] Container image '{image}' not found - "
                + ("falling back to running check_command on the host (unsafe "
                   "host exec explicitly allowed)."
                   if unsafe_ok else
                   "refusing to run check_command on the host. Run "
                   "`optarena sandbox build` to build the sandboxed test image, "
                   "or set OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1 to run this "
                   "untrusted command directly on this machine anyway."),
                file=sys.stderr,
            )
            _docker_warned = True

    if not use_docker:
        if not docker_disabled and not _docker_ok and not _docker_warned:
            print(
                f"[optarena] {engine} not available - "
                + ("running check_command directly on the host (unsafe host "
                   "exec explicitly allowed)."
                   if unsafe_ok else
                   "refusing to run check_command on the host. Install/start "
                   "Docker or Podman for sandboxed execution, or set "
                   "OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1 to run this untrusted "
                   "command directly on this machine anyway."),
                file=sys.stderr,
            )
            _docker_warned = True
        if not unsafe_ok:
            info["sandbox"] = "refused"
            info["infrastructure_error"] = True
            return ([f'check_command refused: no container sandbox available and host '
                     f'execution was not explicitly allowed (see stderr): {cmd}'], info)
        return _run_check_command_local(cmd, root, timeout, info)
    return _run_check_command_docker(cmd, root, timeout, image, info)


def _run_check_command_sandbox(cmd: str, root: Path, timeout: int, sandbox: "DockerSandbox", info: dict) -> tuple[list[str], dict]:
    info["sandbox"] = "docker"
    info["image"] = sandbox.image
    info["container"] = sandbox.name
    engine = container_engine()
    info["engine"] = engine
    t0 = time.monotonic()
    try:
        proc = sandbox.exec(cmd, root, timeout)
    except subprocess.TimeoutExpired:
        info["duration_s"] = round(time.monotonic() - t0, 2)
        info["timed_out"] = True
        sandbox.reap()
        return [f'check_command timed out after {timeout}s ({engine} exec): {cmd}'], info
    except (OSError, ValueError) as exc:
        info["duration_s"] = round(time.monotonic() - t0, 2)
        info["infrastructure_error"] = True  # F-10: the engine/container couldn't even exec, not a test failure
        return [f'check_command could not run ({engine} exec): {exc}'], info
    info["duration_s"] = round(time.monotonic() - t0, 2)
    info["ran"] = True
    info["exit_code"] = proc.returncode
    info["output"] = ((proc.stdout or "") + (proc.stderr or ""))[-400:].strip()
    if proc.returncode == 124:
        info["timed_out"] = True
        sandbox.reap()
        return [f'check_command timed out after {timeout}s ({engine} exec): {cmd}'], info
    if proc.returncode != 0:
        return ([f'check_command failed in {engine} (exit {proc.returncode}): {cmd}'
                 + (f' :: {info["output"]}' if info["output"] else "")], info)
    return [], info


def _kill_process_tree(pid: int) -> None:
    """
    Kill an entire process tree started by ``run_capture`` (H-11). A check
    or agent that spawned children (a test that starts a server, an agent
    that shells out) leaves those children ORPHANED when only its direct
    child is killed - they keep holding ports/CPU and cascade failures into
    every later case on this host. On POSIX the child is its own session
    leader (``start_new_session``), so signalling the negative pgid hits the
    whole group; on Windows ``taskkill /T`` walks and kills the tree.
    """
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
    else:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True)


# F-03: `Popen.communicate()` buffers a stream's ENTIRE output in memory
# before any truncation happens - only a few hundred chars of any output are
# ever actually shown/stored downstream (see the `[-400:]`/`[-800:]` slices
# throughout this module and the drivers), so there's no reason to let a
# pathological or adversarial agent/check_command hold gigabytes in memory
# on the way to being thrown away.
_MAX_CAPTURE_BYTES = 256 * 1024  # generous headroom over any tail actually used


def _drain_bounded(stream, max_keep: int = _MAX_CAPTURE_BYTES):
    """Read `stream` to EOF (so the child is never blocked on a full pipe
    buffer) while keeping only the trailing `max_keep` bytes/chars in
    memory. Works for both binary and text-mode streams - `chunk[:0]` yields
    the correctly-typed empty value (``b""`` or ``""``) to join against."""
    chunks: list = []
    kept = 0
    while True:
        chunk = stream.read(65536)
        if not chunk:
            break
        chunks.append(chunk)
        kept += len(chunk)
        # Compact only once well past budget, so a stream with many small
        # writes doesn't re-join its whole buffer on every single read.
        if kept > max_keep * 2:
            empty = chunk[:0]
            joined = empty.join(chunks)[-max_keep:]
            chunks = [joined]
            kept = len(joined)
    if not chunks:
        return ""
    empty = chunks[0][:0]
    return empty.join(chunks)[-max_keep:]


def run_capture(cmd, *, timeout: int, **kwargs) -> subprocess.CompletedProcess:
    """
    Like ``subprocess.run(..., capture_output=True, timeout=timeout)`` but
    (a) on timeout kills the whole process TREE, not just the direct child
    (``subprocess.run`` kills only the immediate process even with a new
    session), and (b) bounds captured stdout/stderr to a trailing window
    (F-03) rather than buffering everything a child ever writes. Raises
    ``subprocess.TimeoutExpired`` after the tree is reaped, so existing call
    sites that catch it are unchanged. Used for every HOST-mode subprocess
    (check_command on the host, and the CLI-agent drivers) - container paths
    don't need it, the container boundary already is the process-group
    boundary (see ``DockerSandbox.reap``).
    """
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.PIPE)
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(cmd, **kwargs)

    # Two reader threads, not proc.communicate() - stdout/stderr must be
    # drained CONCURRENTLY (not one-then-the-other) or the child can deadlock
    # the moment the not-yet-read stream's OS pipe buffer fills up.
    captured: dict[str, object] = {}

    def _reader(key: str, stream) -> None:
        captured[key] = _drain_bounded(stream) if stream is not None else None

    t_out = threading.Thread(target=_reader, args=("stdout", proc.stdout), daemon=True)
    t_err = threading.Thread(target=_reader, args=("stderr", proc.stderr), daemon=True)
    t_out.start()
    t_err.start()
    try:
        proc.wait(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        timed_out = True

    # Kill BEFORE joining the reader threads, not after: on timeout the
    # child is still alive and its pipes are still open, so a reader thread
    # blocked in stream.read() won't see EOF - and won't return - until
    # something closes those pipes. Joining first (the original bug here)
    # meant waiting out the FULL join timeout on each of two threads before
    # the tree-kill even ran, letting the child comfortably outlive the
    # timeout it was supposed to enforce.
    if timed_out:
        _kill_process_tree(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass  # best-effort reap; raising regardless

    # NOW the process (and its pipes) are closed - normally, or forcibly by
    # the kill above - so EOF arrives promptly and these joins are bounded
    # in practice, not just in theory.
    t_out.join(timeout=10)
    t_err.join(timeout=10)
    stdout, stderr = captured.get("stdout"), captured.get("stderr")
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
    if timed_out:
        raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _run_check_command_local(cmd: str, root: Path, timeout: int, info: dict) -> tuple[list[str], dict]:
    info["sandbox"] = "host"
    t0 = time.monotonic()
    try:
        proc = run_capture(
            cmd, shell=True, cwd=root, timeout=timeout,
            text=True, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        info["duration_s"] = round(time.monotonic() - t0, 2)
        info["timed_out"] = True
        return [f'check_command timed out after {timeout}s: {cmd}'], info
    except OSError as exc:
        info["duration_s"] = round(time.monotonic() - t0, 2)
        info["infrastructure_error"] = True  # F-10: couldn't even launch the command - not a test failure
        return [f'check_command could not run: {exc}'], info
    info["duration_s"] = round(time.monotonic() - t0, 2)
    info["ran"] = True
    info["exit_code"] = proc.returncode
    info["output"] = ((proc.stdout or "") + (proc.stderr or ""))[-400:].strip()
    if proc.returncode != 0:
        return ([f'check_command failed (exit {proc.returncode}): {cmd}'
                 + (f' :: {info["output"]}' if info["output"] else "")], info)
    return [], info


def _run_check_command_docker(cmd: str, root: Path, timeout: int, image: str, info: dict) -> tuple[list[str], dict]:
    info["sandbox"] = "docker"
    info["image"] = image
    name = f"optarena-check-{uuid.uuid4().hex[:12]}"
    info["container"] = name
    engine = container_engine()
    info["engine"] = engine
    docker_cmd = [
        engine, "run", "--rm", "--name", name,
        "--network", "none",
        # Same resources as the shared DockerSandbox - a Spring Boot app under
        # 512m would OOM here but pass in the shared container, and vice versa.
        "--memory", "2g", "--cpus", "2",
        *_HARDENING_ARGS, *_sandbox_user_args(), *_writable_cache_args(image),
        "-v", f"{root.resolve()}:/workspace",
        "-w", "/workspace",
        image,
        "sh", "-c", cmd,
    ]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            docker_cmd, capture_output=True, text=True,
            timeout=timeout + 15, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        try:
            subprocess.run([engine, "rm", "-f", name], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass  # best-effort; the engine's own GC reclaims a leftover container eventually
        info["duration_s"] = round(time.monotonic() - t0, 2)
        info["timed_out"] = True
        return [f'check_command timed out after {timeout}s ({engine}): {cmd}'], info
    except OSError as exc:
        info["duration_s"] = round(time.monotonic() - t0, 2)
        info["infrastructure_error"] = True  # F-10: couldn't even start the container - not a test failure
        return [f'check_command could not run ({engine}): {exc}'], info
    info["duration_s"] = round(time.monotonic() - t0, 2)
    info["ran"] = True
    info["exit_code"] = proc.returncode
    info["output"] = ((proc.stdout or "") + (proc.stderr or ""))[-400:].strip()
    if proc.returncode != 0:
        return ([f'check_command failed in {engine} (exit {proc.returncode}): {cmd}'
                 + (f' :: {info["output"]}' if info["output"] else "")], info)
    return [], info


def classify_failure(oracle_info: dict) -> str | None:
    """
    Best-effort bucket for a check_command failure, from its exit code and
    captured output - "was this a syntax error, a compile error, a failed
    assertion, or a timeout" at a glance. Not authoritative and not used by
    the oracle's own pass/fail verdict - just a richer metric surfaced in
    ``extra["oracle"]["failure_class"]`` for the CLI/dashboard/compare table.
    """
    # Timeouts first: the runners record an explicit `timed_out` marker (a
    # host timeout never even sets ran/exit_code, and the container's own
    # `timeout` wrapper exits 124) - the command's own captured output almost never contains
    # the words "timed out", so text-sniffing alone made this class
    # effectively unreachable.
    if oracle_info.get("timed_out") or oracle_info.get("exit_code") == 124:
        return "timeout"
    if not oracle_info.get("ran") or oracle_info.get("exit_code") in (None, 0):
        return None
    if "timed out" in (oracle_info.get("output") or "").lower():
        return "timeout"
    output_lower = (oracle_info.get("output") or "").lower()
    cmd_lower = (oracle_info.get("check_command") or "").lower()
    if any(s in output_lower for s in ("syntaxerror", "indentationerror", "unterminated", "unexpected token")):
        return "syntax_error"
    # Compiler signatures across the toolchains the corpus actually uses -
    # not just gcc: rustc ("error[E0308]"), csc ("error CS1002"), javac via
    # maven ("compilation error" / "cannot find symbol"), go, and linkers.
    if any(s in output_lower for s in (
        "undefined reference", "collect2:", "compilation error",
        "cannot find symbol", "error cs", "error[e",
        "undefined:",              # go compiler
        "could not compile",       # cargo's summary line
    )):
        return "compile_error"
    if any(t in cmd_lower for t in ("gcc", "g++", "cc ")) and any(
        s in output_lower for s in ("error:", "calledprocesserror")
    ):
        return "compile_error"
    if "assertionerror" in output_lower or "assert " in output_lower:
        return "assertion_failure"
    return "runtime_error"


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
        name = Path(rel).name.lower()
        for spec in expected:
            pat = str(spec.get("path_pattern", "")).lower()
            if not pat:
                continue
            if fnmatch.fnmatch(name, pat) or fnmatch.fnmatch(rel.lower(), pat):
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


def write_setup_files(root: Path, setup_files: dict[str, str] | None) -> None:
    # H-01: `rel` comes straight from case JSON (`setup_files`/`test_setup_files`
    # keys) - an absolute path or a "../" traversal there would write outside
    # the sandboxed workspace, onto the host. `root / rel` alone doesn't catch
    # this: pathlib silently discards `root` entirely when `rel` is absolute,
    # and ".." components resolve upward without error. Resolve and confirm
    # containment before ever touching disk.
    root = root.resolve()
    for rel, content in (setup_files or {}).items():
        dest = (root / rel).resolve()
        if not dest.is_relative_to(root):
            raise ValueError(f"setup file path escapes workspace root: {rel!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        # newline="" disables universal-newline translation - on Windows hosts,
        # write_text() would otherwise turn every "\n" in case JSON content
        # into "\r\n", which is silently tolerated by most languages but
        # corrupts POSIX shell scripts (a stray \r glued to `do`/`done`/etc.
        # breaks dash/sh parsing) once bind-mounted into the Linux sandbox.
        dest.write_text(content, encoding="utf-8", newline="")


# ── L3 (repo-scale) cases: a shared starter repo copied in, not inlined ────────

REPOS_DIR = Path(__file__).resolve().parent.parent / "repos"


def copy_setup_repo(root: Path, repo_name: str) -> None:
    """Copy every file from repos/<repo_name>/ into the workspace root. Used
    by L3 cases (`setup_repo`) so a 20-100 file starter app lives once on
    disk instead of being inlined into every case JSON that shares it."""
    src = REPOS_DIR / repo_name
    if not src.is_dir():
        raise FileNotFoundError(f'setup_repo "{repo_name}" not found under {REPOS_DIR}')
    root = root.resolve()
    for p in src.rglob("*"):
        # Symlinks are skipped outright (defense in depth): `p.is_file()`
        # follows a symlink, so a starter repo containing one could copy
        # arbitrary host file content into the workspace. `repo_name` itself
        # is trusted (a project-controlled directory name, not case JSON),
        # but starter repos shouldn't need symlinks regardless.
        if p.is_symlink() or not p.is_file():
            continue
        dest = (root / p.relative_to(src)).resolve()
        if not dest.is_relative_to(root):
            raise ValueError(f"setup_repo file path escapes workspace root: {p!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(p.read_bytes())


def git_init_workspace(root: Path) -> None:
    """Commit the freshly-prepared workspace as a one-commit git repo, so an
    L3 case's model edits land as a realistic diff against a checked-in
    baseline instead of an untracked directory. Best-effort: a sandbox image
    without `git` on PATH just skips it silently - no case's oracle depends
    on the repo actually existing, only on the files being there."""
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "optarena", "GIT_AUTHOR_EMAIL": "optarena@local",
           "GIT_COMMITTER_NAME": "optarena", "GIT_COMMITTER_EMAIL": "optarena@local"}
    try:
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "commit", "-q", "-m", "initial"]):
            subprocess.run(cmd, cwd=root, capture_output=True, timeout=15, env=env)
    except (OSError, subprocess.TimeoutExpired):
        pass


# ── Dynamic evaluation: mid-session disruptions (RoadmapBench/REALM-Bench-style) ──


def _disruption_ready(dis: dict, root: Path, after_index: int) -> bool:
    """
    Has this disruption's trigger fired at this prompt boundary? Two styles
    (schema.py enforces exactly one is present):

    - ``after_prompt``: fixed - fires when ``after_index`` equals it.
    - ``when``: REACTIVE/state-conditioned - fires the first prompt boundary
      where the *workspace* satisfies a condition, not a hardcoded step count.
      ``file_exists``: a path now exists (e.g. the agent finally created the
      file the task asked for, and NOW the rug gets pulled). ``file_contains``:
      a path exists and its text contains a substring (e.g. the agent's own
      output reveals it read a specific stale value). This is what lets a
      disruption respond to what the agent actually did instead of assuming a
      fixed turn count - REALM-Bench-style disruptions are keyed off plan
      state, not a clock.
    """
    if "after_prompt" in dis:
        return dis["after_prompt"] == after_index
    when = dis.get("when") or {}
    if "file_exists" in when:
        return (root / when["file_exists"]).exists()
    if "file_contains" in when:
        fc = when["file_contains"]
        target = (root / fc["path"]).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return False
        try:
            return fc["pattern"] in target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
    return False


def _fire_disruption(dis: dict, root: Path, label: str) -> str:
    """Apply one disruption's write/delete effects (containment-checked, like
    ``write_setup_files``/``copy_setup_repo``) and return its description."""
    write_setup_files(root, dis.get("write_files"))
    for rel in dis.get("delete_files") or []:
        dest = (root / rel).resolve()
        if not dest.is_relative_to(root):
            raise ValueError(f"disruption delete path escapes workspace: {rel!r}")
        if dest.is_file() or dest.is_symlink():
            dest.unlink(missing_ok=True)
        elif dest.is_dir():
            shutil.rmtree(dest, ignore_errors=True)
    return dis.get("description") or label


def apply_disruptions(case: dict, root: Path, after_index: int,
                       fired_indices: "set[int] | None" = None) -> list[str]:
    """
    Fire every not-yet-fired disruption whose trigger is satisfied at this
    prompt boundary (1-based ``after_index``): a mid-session environment change
    the agent must adapt to on its NEXT prompt - a file rewritten (a
    config/dependency that changed under it) or deleted (a reverted edit).
    Drivers call this between prompts. Returns a short description of each
    disruption that fired (for the per-step trajectory record).

    ``fired_indices`` is a ``set`` the CALLER owns and passes back in on every
    call across one case run (fresh per run - never reused across cases/trials):
    it's what makes a reactive ``when`` trigger fire exactly ONCE even though
    its condition can stay true across several later prompt boundaries (e.g.
    a file that, once created, stays created). A fixed ``after_prompt`` trigger
    doesn't strictly need this (each ``after_index`` value is only ever seen
    once in a normal ascending prompt loop) but is tracked the same way for
    uniformity and defense-in-depth. Omitting it (``None``) reproduces the old
    stateless behavior for ``after_prompt``-only cases.

    This is what makes OptArena a *dynamic* coding-agent evaluator: the frontier
    (REALM-Bench, PlanBench-XL, CostBench) shows agents lose ~40% when the
    environment shifts mid-task; a static final-state oracle can't see that.
    """
    root = root.resolve()
    fired: list[str] = []
    seen = set() if fired_indices is None else fired_indices
    for idx, dis in enumerate(case.get("disruptions") or []):
        if idx in seen:
            continue
        if not _disruption_ready(dis, root, after_index):
            continue
        fired.append(_fire_disruption(dis, root, f"disruption after prompt {after_index}"))
        seen.add(idx)
    return fired


def apply_all_disruptions(case: dict, root: Path) -> None:
    """Force-apply EVERY disruption in declaration order, regardless of its
    trigger (fixed or reactive) - the fully-perturbed final world. Used by
    ``verify-corpus`` so the reference solution is validated *through* every
    disruption (the correct answer must hold in the worst-case, fully-changed
    world), not just against the pristine setup. A reactive ``when`` trigger's
    condition generally depends on the AGENT's edits (which verify-corpus does
    not simulate - it lays down a whole solution at once), so "would it have
    fired during a real run" isn't decidable here; forcing it is the
    conservative choice; a case author who needs the un-perturbed world checked
    too can add an explicit ``broken_solutions`` variant for that.

    Order: fixed (``after_prompt``) disruptions apply in ascending temporal
    order (2 then 1 in declaration order still ends with 2's effect last, as
    a compounding case's LAST write is the one that should stick); reactive
    (``when``) ones - which have no inherent order - apply after all fixed
    ones, in declaration order among themselves."""
    root = root.resolve()
    indexed = list(enumerate(case.get("disruptions") or []))
    indexed.sort(key=lambda pair: (pair[1].get("after_prompt", float("inf")), pair[0]))
    for idx, dis in indexed:
        _fire_disruption(dis, root, f"disruption[{idx}]")


def prepare_workspace(root: Path, case: dict) -> None:
    """Populate a case's workspace: an optional shared starter repo
    (`setup_repo`) copied in first - unchanged L1/L2 behavior when it's
    absent - then this case's own `setup_files` written over it, then an
    optional `git_init` commit of that combined starting state."""
    if case.get("setup_repo"):
        copy_setup_repo(root, case["setup_repo"])
    write_setup_files(root, case.get("setup_files"))
    if case.get("git_init"):
        git_init_workspace(root)
