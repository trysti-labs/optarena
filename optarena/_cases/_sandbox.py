"""
Container engine resolution, the long-lived `DockerSandbox`, and safe
execution of a case's `check_command` (in a container, or - only with
explicit opt-in - on the host). Also `run_capture`/`_kill_process_tree`/the
Windows Job Object bindings: cross-platform "run this and guarantee the
whole process tree dies on timeout" used by every host-mode subprocess
(check_command on the host, and the CLI-agent drivers).

This is the single most tightly-coupled piece of the old `cases.py` - engine
health/pull-backoff caching, the active-sandbox registries, and the
check_command dispatch chain all call each other directly and share module-
level cache state - so it stays one cohesive module rather than being split
further. Tests that reset or inspect that cache state import this submodule
directly (`optarena._cases._sandbox`), not the `optarena.cases` facade -
see tests/test_optarena.py's `DockerCheckCommandTests`/`EngineStateResetTests`
and friends.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from ._constants import DOCKER_IMAGE_DEFAULT, DOCKER_IMAGES
from ._snapshot import normalize_workspace_line_endings

# A-05: shape of an acceptable container image reference - see
# validate_image_ref. Mirrors schema._IMAGE_RE (kept here as well so the
# runtime funnel has no import-time dependency on the schema module).
_IMAGE_REF_RE = re.compile(
    r"^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]+)?(@sha256:[0-9a-f]{64})?$")

# Defense-in-depth flags applied to every sandbox container that runs a
# case-defined `check_command` (both the shared DockerSandbox and the
# ephemeral per-call fallback) - the command itself, and anything it runs,
# is untrusted (case-authored, and can execute model-generated code). None
# of the corpus's 836 check_commands install packages at runtime (all
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
    still work.

    A-36: every published image now creates a matching uid 1000 account and
    exposes its toolchain caches to it (see the Dockerfiles), and the runner
    relaxes workspace permissions when this is set (`sandbox_user_configured`
    / `relax_workspace_permissions`) - the bind-mounted run root is created by
    the HOST user, so without that a non-root container uid cannot write its
    own case directory. Still opt-in rather than the default: the run root's
    ownership is a host property this project cannot guarantee across
    Docker Desktop, rootless Podman and CI, so flipping the default needs
    per-track validation on real Linux (the sandbox-nonroot CI job).
    """
    user = os.environ.get("OPTARENA_SANDBOX_USER")
    return ["--user", user, "-e", "HOME=/tmp"] if user else []


def sandbox_user_configured() -> bool:
    """True when OPTARENA_SANDBOX_USER asks for non-root sandbox execution."""
    return bool(os.environ.get("OPTARENA_SANDBOX_USER"))


def relax_workspace_permissions(path: Path) -> None:
    """
    A-36: make `path`, AND EVERYTHING ALREADY UNDER IT, writable by the
    (different) uid the sandbox container runs as. No-op unless
    OPTARENA_SANDBOX_USER is set, and no-op on Windows hosts, where the
    bind-mount layer ignores Unix permission bits anyway.

    Needed because `tempfile.mkdtemp()` deliberately creates 0700 directories
    owned by the host user: a container running as uid 1000 cannot write into
    a workspace owned by uid 501. This is the same class of problem the
    DAC_OVERRIDE capability solves for the ROOT sandbox - root can bypass the
    permission check, an unprivileged uid cannot.

    Recursive, not just the top directory: a real bug lived here too. A
    single `path.chmod()` only affects `path` itself - it does not cascade
    to files/subdirectories already inside it. Calling this before the
    workspace has any content (the original call sites) made the top-level
    directory traversable, which fixed READING files in it (644 files are
    world-readable regardless of the parent's own mode, once you can reach
    them). It did nothing for WRITING into content created afterward - a
    `setup_repo` fixture's copied subdirectories, or overwriting an existing
    `setup_files`-written script - since those inherit normal restrictive
    permissions from however they were created, not from this call. Confirmed
    live: `cases verify --language shell` under a non-root sandbox went from
    100% "Permission denied" reading hidden tests (directory-traversal bug,
    fixed by relaxing the chain) to 5 failures specifically in cases whose
    check_command WRITES into the workspace (a shell-toolkit deploy script's
    backup file, a mutation-check script overwriting a setup_files script) -
    exactly the write-after-relax gap this recursive walk closes. Callers
    must call this AFTER the workspace is fully populated (setup_repo copy +
    setup_files + solution files), not before - see verify.py's _run_variant.
    """
    if not sandbox_user_configured() or os.name != "posix":
        return
    try:
        path.chmod(0o777)
        for child in path.rglob("*"):
            try:
                child.chmod(0o777)
            except OSError:
                pass   # best-effort per-entry: one bad entry shouldn't abort the rest
    except OSError:
        pass   # best-effort: the run still works if the uids happen to match


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
    """
    Forget everything cached about the container engine's health, so the next
    `_docker_available()` call re-probes regardless of TTL. Called once per
    scenario by `runner.run_scenario` (F-16) so a transient failure early in a
    matrix/multi-scenario run doesn't suppress the sandbox for every later
    scenario even after the engine recovers.

    A-11: this function previously had ZERO callers - the runner reached past
    it into `_docker_available(force_recheck=True)` - while its docstring
    claimed the runner called it. It now really is the one entry point, and it
    also clears `_docker_warned` (A-12): that flag made the "no sandbox
    available, refusing to run check_command" explanation print at most once
    per PROCESS, so in a --matrix-drivers sweep every scenario after the first
    produced refused check_commands with no stderr line saying why.
    """
    global _docker_checked_at, _docker_warned
    _docker_checked_at = -1.0
    _docker_warned = False


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


def reset_pull_backoff() -> None:
    """A-13: clear per-image pull attempt/backoff state. Called once per
    scenario by `runner.run_scenario`, for the same reason F-16 re-probes
    engine health there: attempt counts that make sense WITHIN one run
    shouldn't permanently write off an image for every later scenario of a
    matrix sweep because of one transient registry failure. Also keeps these
    dicts from growing across a long-lived process."""
    _pull_attempts.clear()
    _pull_last_attempt_at.clear()


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


def validate_image_ref(image: str) -> str:
    """
    A-05: every image reference that reaches a container command line has to
    look like an image reference. `docker run` / `podman run` parse options
    up to the FIRST non-option token, so a value beginning with "-" is
    consumed as a flag rather than as the image argument - i.e. a case's
    `image` field (untrusted: case JSON is installable from a URL), a
    scenario's `image_overrides`, or OPTARENA_SANDBOX_IMAGE could inject
    arguments into the one command that is supposed to BE the security
    boundary. `schema.validate_case` rejects this earlier for case files;
    this is the funnel every path goes through, including the env var, which
    no schema ever sees.
    """
    if not isinstance(image, str) or not _IMAGE_REF_RE.match(image):
        raise ValueError(
            f"invalid container image reference {image!r} - expected something like "
            f"'optarena-tester:latest' or 'ghcr.io/org/img@sha256:<64 hex>'"
        )
    return image


def resolve_image(image: str) -> str:
    """Apply the active run's `image_overrides` (F-15) to a resolved image
    name, if any - matched either by the exact image reference or by
    `DOCKER_IMAGES` short track name (e.g. "python"), so a scenario can pin
    either a specific unusual `image` value or a whole registered
    track without knowing every case's exact tag.

    A-05: the returned reference is always shape-validated, whatever it came
    from (case field, override, or OPTARENA_SANDBOX_IMAGE)."""
    if not _image_overrides:
        return validate_image_ref(image)
    if image in _image_overrides:
        return validate_image_ref(_image_overrides[image])
    for key, val in DOCKER_IMAGES.items():
        if val == image and key in _image_overrides:
            return validate_image_ref(_image_overrides[key])
    return validate_image_ref(image)


def ensure_image(image: str) -> bool:
    """Local image, or a successful GHCR pull tagged to the local name.

    The availability probe is retried once: right after the container
    engine's VM wakes from an idle/resource-saver state (Docker Desktop,
    `podman machine`), the first `image inspect` can exceed its timeout, and
    misreading that as "image missing" would trigger a pointless (and
    possibly slow) registry pull for an image that's already local.
    """
    # A-14: was written as `docker_image_available(image) or
    # docker_image_available(image)` - correct by accident (`or`
    # short-circuits, so the second call only happens when the first says no)
    # but indistinguishable from a copy-paste bug at a glance.
    if any(docker_image_available(image) for _ in range(2)):
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


def _start_stderr_drain(stream) -> "deque[str]":
    """Continuously read ``stream`` (a text-mode pipe) on a daemon thread
    into a bounded deque of lines, returning the deque immediately. Module-
    level (not a DockerSandbox method) so it can be tested against a real
    subprocess pipe without a container engine, and guarded so a test's
    fake Popen with a Mock/None stderr never spins a busy loop."""
    tail: "deque[str]" = deque(maxlen=200)
    if stream is None or not hasattr(stream, "readline"):
        return tail

    def _drain() -> None:
        try:
            for line in iter(stream.readline, ""):
                if not isinstance(line, str):   # a Mock stderr in tests
                    return
                tail.append(line.rstrip("\n"))
        except (ValueError, OSError):
            pass    # stream closed mid-read during teardown - tail keeps what it has

    threading.Thread(target=_drain, daemon=True).start()
    return tail


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

    def __init__(self, root: Path, image: str | None = None, extra_run_args: list[str] | None = None,
                 network: str = "none", register: bool = True):
        self.root = root.resolve()
        # A-05: validated here too - this constructor is also reachable with
        # an OPTARENA_SANDBOX_IMAGE value that never passed through
        # resolve_image (e.g. verify.py's own grouping).
        self.image = validate_image_ref(
            image or os.environ.get("OPTARENA_SANDBOX_IMAGE", DOCKER_IMAGE_DEFAULT))
        self.name = f"optarena-sandbox-{uuid.uuid4().hex[:12]}"
        self.active = False
        # An explicit, per-INSTANCE escape hatch from the shared
        # _HARDENING_ARGS below - e.g. a sandboxed-real service whose real
        # server needs to drop privileges via `su` at startup (CAP_SETUID/
        # CAP_SETGID, neither in the default cap set) needs this for ITS one
        # dedicated container, without loosening every other image's
        # hardening. None (the default, every existing caller) changes
        # nothing. See _sandboxed_mcp_service.SANDBOXED_SERVICES["database"].
        self._extra_run_args = list(extra_run_args or [])
        # "none" (every existing caller) is a HARD requirement for
        # check_command - untrusted model-generated code must never reach
        # the network. A sandboxed-real MCP server can have a legitimate,
        # narrow exception: one whose entire real tool surface is read-only
        # public-API lookups with no state-mutating capability at all (e.g.
        # package_registry's real server has no publish/delete tool - only
        # search/get-details/list-versions) may need real network access
        # because there's no way to point it at a local/offline substitute.
        # A DEDICATED constructor param, not an appended --network flag:
        # confirmed empirically that Docker rejects two --network flags
        # outright ("conflicting options"), so this can't be layered via
        # extra_run_args the way capabilities can.
        self._network = network
        # False for a per-case dedicated sandbox (sandboxed-real MCP - see
        # _sandboxed_mcp_service.build_sandboxed_service): the shared
        # `_active_sandboxes` map exists so `run_check_command` can route
        # each case into the one long-lived container for its image, which
        # is exactly wrong for a container that belongs to a single case -
        # and under --parallel, two same-service cases would silently
        # overwrite each other's entry (E-1 in the tool-call audit).
        self._register = register
        # Host-side `docker/podman exec -i` client processes started via
        # exec_attached() - see stop() for why these need explicit teardown.
        self._attached: list[subprocess.Popen] = []

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
                 "--network", self._network, "--memory", "2g", "--cpus", "2",
                 *_HARDENING_ARGS, *self._extra_run_args,
                 *_sandbox_user_args(), *_writable_cache_args(self.image),
                 "-v", f"{self.root}:/workspace", "-w", "/workspace",
                 self.image, "sleep", "infinity"],
                capture_output=True, timeout=20, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            print(f"[optarena] could not start {engine} sandbox: {exc}", file=sys.stderr)
            return False
        self.active = True
        if self._register:
            _active_sandboxes[self.image] = self
            print(f"[optarena] {engine} sandbox: {self.name} (image {self.image}) - "
                  f"one container for this whole run")
        else:
            print(f"[optarena] {engine} sandbox: {self.name} (image {self.image}) - "
                  f"dedicated to one case")
        return True

    def stop(self) -> None:
        # F-16: both calls are bounded so a hung engine CLI (not just a hung
        # container - the `-t 2` grace period only bounds the CONTAINER's
        # shutdown, not the `docker`/`podman stop` client process itself)
        # can't hang the whole run's cleanup. Best-effort throughout: this is
        # cleanup, called from a `finally`, and must never raise - a failed
        # stop/rm here just means a leftover `--rm` container the engine's
        # own garbage collection (or the next `sandbox build`) will reclaim.
        # exec_attached()'s Popens are the HOST-side exec client process, not
        # a child of the container's own `sleep infinity` PID 1 - stopping
        # the container kills the in-container server they were talking to,
        # but doesn't by itself reap this side of the pipe. Best-effort, same
        # as everything else in this method: called from a `finally`, must
        # never raise.
        for proc in self._attached:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
            except OSError:
                pass
        self._attached.clear()
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

    def exec_attached(self, cmd: list[str], case_root: Path,
                      env: dict[str, str] | None = None) -> subprocess.Popen:
        """Launch a long-lived process INSIDE the shared container, attached
        via piped stdin/stdout/stderr - for a process you talk to over its
        own protocol (e.g. an MCP server's stdio JSON-RPC loop), not a
        one-shot command whose output you just capture (that's ``exec()``
        above). No ``timeout`` wrapper here: the caller owns per-request
        timeouts (see ``_mcp_client.MCPStdioClient``), this method only owns
        process lifecycle. Text-mode, line-buffered, UTF-8 - what
        ``MCPStdioClient`` expects. Tracked in ``self._attached`` so
        ``stop()`` tears it down; the caller should still call ``.terminate()``/
        close its own client first for a graceful exit where possible.

        ``env`` becomes ``-e KEY=VALUE`` flags on the exec - how the host
        side passes per-launch facts (e.g. the sandbox's own name, so an
        in-container wrapper script can derive HOST-visible resource names
        the host can later reap - see start-k8s-mcp.sh).

        stderr is drained continuously into ``proc.stderr_tail`` (a bounded
        deque of lines) by a daemon thread. Draining is load-bearing, not a
        convenience: MCP's spec blesses stderr for server logging, and a
        server that writes more than the OS pipe buffer (~64KB) to an
        UNdrained pipe blocks on that write forever - the session hangs and
        the case burns its whole timeout (B-1 in the tool-call audit; kind's
        cluster-create progress goes through exactly this pipe). The tail
        doubles as the diagnostics callers surface on handshake failure.
        """
        rel = case_root.resolve().relative_to(self.root).as_posix()
        env_args: list[str] = []
        for key, value in (env or {}).items():
            env_args += ["-e", f"{key}={value}"]
        full_cmd = [container_engine(), "exec", "-i", *env_args,
                    "-w", f"/workspace/{rel}", self.name, *cmd]
        proc = subprocess.Popen(
            full_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
        )
        proc.stderr_tail = _start_stderr_drain(proc.stderr)  # type: ignore[attr-defined]
        self._attached.append(proc)
        return proc

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


def _cargo_clean_prefix(root: Path) -> str:
    """
    Rust cases share ONE CARGO_TARGET_DIR across every case/variant/trial in
    the sandbox (see docker/rust/Dockerfile) so the expensive axum/
    actix-web/tokio dependency graph only compiles once. That sharing hides
    a real Cargo behavior: a local path package's build-cache identity is
    (name, version, dependencies, profile) - it does NOT include the
    package's own absolute directory. Confirmed directly with
    `CARGO_LOG=cargo::core::compiler::fingerprint=trace`: a case's
    `reference_solution` and a `broken_solutions` variant (same Cargo.toml
    name/version, genuinely different source, different directories) get
    the identical metadata hash and the identical output filename. Cargo's
    freshness check then compares the output's mtime against whatever
    source path its OWN dep-info last recorded - from whichever variant
    built it FIRST - and never even looks at the CURRENT directory's actual
    file. Multiple different cases also reuse the same generic package name
    outright (confirmed: "webapp"/"conc_case"/"dedup_perf", each shared by
    2-4 cases), so this isn't limited to variants of one case. Net effect,
    confirmed live via `cases verify --language rust`: a broken_solutions
    variant silently reused an EARLIER build's already-compiled, already-
    passing test binary - an oracle-violating variant read as passing on
    10+ cases, and reference solutions got the wrong DIFFERENT case's
    symbols and failed to "compile" on 2 more.

    `cargo clean -p <name>` immediately before every check_command removes
    only that one package's own cached lib/test/fingerprint artifacts - its
    dependencies (the expensive, genuinely shared part) stay warm, so this
    costs a small crate recompile, not the dependency graph.
    """
    cargo_toml = root / "Cargo.toml"
    if not cargo_toml.is_file():
        return ""
    try:
        text = cargo_toml.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"', text)
    if not m:
        return ""
    # Cargo package names are a small charset (letters/digits/-/_) by its own
    # validation, but this ultimately comes from case-JSON content - quote
    # defensively rather than trust it. `;` not `&&`: a clean failure (there
    # is nothing plausible that would cause one here) must not preempt the
    # real check_command from running at all.
    return f"cargo clean --offline -p {shlex.quote(m.group(1))} >/dev/null 2>&1; "


# P1-07: `snapshot()` (see _snapshot.py) bounds how much of the workspace
# the POST-HOC inspection pass reads/hashes - it says nothing about how much
# an agent or check_command may WRITE while actually running. `/workspace`
# always reaches the sandbox as a Docker BIND MOUNT (`-v {root}:/workspace`
# - see DockerSandbox.start/_run_check_command_docker), and a bind mount
# cannot be size-quota'd via Docker's own `--storage-opt size=` at all -
# that flag only ever applies to a container's own writable layer (needs
# the overlay2 storage driver with pquota configured on the DAEMON host,
# outside this project's control) or a volume formatted with a
# quota-capable filesystem, never an arbitrary bind-mounted host directory.
# `/tmp`'s `--tmpfs size=1g` (_HARDENING_ARGS) works for a different reason
# - tmpfs is RAM-backed and Docker sizes it directly at mount time - and
# doesn't help here either, since check_command's real output lands in
# /workspace, not /tmp.
#
# Given that real constraint, this is host-side polling: a background
# thread periodically walks the workspace tree during execution and kills
# the in-flight command if either threshold is crossed. This has an
# inherent poll-interval's worth of detection lag - a genuinely adversarial
# writer could still transiently exceed the quota between polls before
# being killed - so it is a soft, best-effort ceiling against a runaway or
# malicious agent/check_command, not a hard kernel-enforced one. It is,
# however, portable across Windows/macOS/Linux hosts and both the host-exec
# and container-exec paths uniformly, unlike any Docker-storage-driver- or
# host-filesystem-specific alternative would be.
_WORKSPACE_MAX_BYTES = int(os.environ.get("OPTARENA_WORKSPACE_MAX_BYTES", 4 * 1024 ** 3))  # 4 GiB
_WORKSPACE_MAX_FILES = int(os.environ.get("OPTARENA_WORKSPACE_MAX_FILES", 50_000))
_WORKSPACE_QUOTA_POLL_INTERVAL_S = 2.0


def _workspace_usage(root: Path) -> "tuple[int, int]":
    """(total_bytes, entry_count) under `root`, best-effort - tolerant of
    files vanishing or changing mid-walk (the very process this polls is
    actively writing/deleting while this runs), matching `snapshot()`'s own
    OSError-tolerant walk. Symlinks are not followed (same reasoning as
    `snapshot()`: a symlink's target isn't this workspace's own disk usage
    to count against its quota).

    P1-09: `entry_count` counts DIRECTORIES too, not just regular files - a
    directory only contributed real disk usage (a Debian ext4 directory
    entry is itself several KB, and a filesystem has a hard limit on total
    inode count independent of file content) but was previously invisible
    to `_WORKSPACE_MAX_FILES` entirely; confirmed live that creating many
    empty directories reported (0 bytes, 0 files) before this fix, a real,
    reproducible bypass of the file-count quota specifically. Bytes are
    still summed from regular files only - a bare directory entry's own
    on-disk size isn't a meaningful signal for the BYTE quota the way it is
    for the file/inode-count one.
    """
    total = 0
    count = 0
    try:
        for p in root.rglob("*"):
            try:
                if p.is_symlink():
                    continue
                if p.is_dir():
                    count += 1
                    continue
                if not p.is_file():
                    continue
                total += p.stat().st_size
                count += 1
            except OSError:
                continue
    except OSError:
        pass
    return total, count


class _WorkspaceQuotaWatchdog:
    """Background poller for the duration of one check_command/driver call.
    Calls ``on_exceeded(reason)`` at most once, the first time the
    workspace crosses either quota, then stops polling - the caller is
    expected to have killed/aborted the in-flight command by the time
    ``on_exceeded`` returns (or promptly afterward); this class does not
    track how many times the same case ends up over quota, only whether it
    ever was.

    P1-09: polling alone has an inherent blind spot - a command that
    crosses the quota and exits before the NEXT poll tick is never caught,
    confirmed live as a real, reproducible bypass. ``check_final()`` closes
    that gap: call it once, synchronously, immediately after the wrapped
    call returns (regardless of how), in addition to - not instead of -
    the background poll. This does not make the quota hard-enforced (a
    command can still transiently exceed it and exit before EITHER the
    poll or the final check would have caught it if it's fast enough - no
    userspace poller can close that to zero), but it removes the
    poll-interval-sized window that made a fast writer's bypass trivial
    and reliable rather than a narrow race.
    """

    def __init__(self, root: Path, on_exceeded,
                 max_bytes: "int | None" = None, max_files: "int | None" = None,
                 interval: "float | None" = None):
        # Module globals read HERE (at instantiation), not bound as mutable
        # default argument values at class-definition time - the latter
        # would freeze whatever OPTARENA_WORKSPACE_MAX_BYTES/etc. resolved
        # to at import time forever, making both the env-var override and
        # `mock.patch`-ing these constants in tests silently no-ops for any
        # watchdog constructed without explicitly passing every argument.
        self._root = root
        self._on_exceeded = on_exceeded
        self._max_bytes = max_bytes if max_bytes is not None else _WORKSPACE_MAX_BYTES
        self._max_files = max_files if max_files is not None else _WORKSPACE_MAX_FILES
        self._interval = interval if interval is not None else _WORKSPACE_QUOTA_POLL_INTERVAL_S
        self._stop = threading.Event()
        self._triggered_reason: "str | None" = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "_WorkspaceQuotaWatchdog":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval + 5)

    @property
    def triggered_reason(self) -> "str | None":
        """None if the quota was never exceeded during this watchdog's
        lifetime; otherwise the human-readable reason it fired."""
        return self._triggered_reason

    def _check_usage(self) -> "str | None":
        """One usage check against both thresholds - the shared logic
        behind both the background poll and ``check_final()``. Returns a
        reason string (also the same one ``triggered_reason`` will report)
        if either threshold is crossed, else None."""
        total_bytes, total_entries = _workspace_usage(self._root)
        if total_bytes > self._max_bytes:
            return f"workspace exceeded {self._max_bytes} byte(s) (was {total_bytes})"
        if total_entries > self._max_files:
            return f"workspace exceeded {self._max_files} file(s)/director{'y' if total_entries == 1 else 'ies'} (was {total_entries})"
        return None

    def check_final(self) -> "str | None":
        """P1-09: one last synchronous check, meant to be called right
        after the wrapped call returns - closes the poll-interval-sized
        timing gap a fast writer could otherwise exploit. A no-op (returns
        the existing reason immediately) if the background poll already
        caught a violation; never calls ``on_exceeded`` a second time (the
        wrapped process has already finished by the time this runs, so
        there's nothing left to kill - only the verdict needs to change).
        """
        if self._triggered_reason is not None:
            return self._triggered_reason
        reason = self._check_usage()
        if reason is not None:
            self._triggered_reason = reason
        return reason

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            reason = self._check_usage()
            if reason is None:
                continue
            self._triggered_reason = reason
            self._on_exceeded(reason)
            return


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
    normalize_workspace_line_endings(root)
    # See _cargo_clean_prefix: a no-op for every non-Rust case (no
    # Cargo.toml in the workspace), so this is safe to apply unconditionally
    # rather than needing `language == "rust"` anywhere.
    cmd = _cargo_clean_prefix(root) + cmd
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
    # P1-07: on_exceeded reuses `sandbox.reap()` - the exact same
    # kill-every-process-in-the-container mechanism the timeout path below
    # already uses - so a quota breach unblocks the in-flight `exec` call
    # the same way a timeout does, rather than needing a second kill path.
    watchdog = _WorkspaceQuotaWatchdog(root, on_exceeded=lambda _reason: sandbox.reap()).start()
    try:
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
    finally:
        watchdog.stop()
        # P1-09: a fast writer that crosses the quota and exits inside one
        # poll interval would otherwise never be caught - this closes that
        # gap without waiting for another poll tick.
        watchdog.check_final()
    # P1-07: checked AFTER the call, regardless of how it returned - a
    # quota-triggered reap() makes the exec's own exit code/timeout status
    # unreliable as a signal (it looks like an ordinary killed process), so
    # the watchdog's own state is what actually decides this, not proc.
    if watchdog.triggered_reason:
        info["duration_s"] = round(time.monotonic() - t0, 2)
        info["workspace_quota_exceeded"] = True
        return [f'check_command aborted: {watchdog.triggered_reason}'], info
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


if os.name != "posix":
    import ctypes
    from ctypes import wintypes

    # P0-03: raw Win32 Job Object bindings via ctypes (stdlib-only - this
    # project takes no external dependency for the whole rest of this module
    # either, see schema.py's own docstring on that). Only defined/used on
    # Windows; harmless to import ctypes here even in a POSIX process that
    # never touches this branch.
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JobObjectExtendedLimitInformation = 9
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_SET_QUOTA = 0x0100

    class _WindowsJob:
        """
        P0-03: a kernel Job Object with KILL_ON_JOB_CLOSE, used in place of
        (well - alongside, as a fallback-of-last-resort) `taskkill /T`.

        `taskkill /T` walks the process tree via each process's own recorded
        parent PID - a link the kernel does NOT keep intact if the parent
        already exited (its PID can be REUSED by an unrelated process by the
        time cleanup runs) or a child explicitly detaches. This is a known,
        documented failure mode for exactly the workloads this sandbox runs:
        a check_command that forks Maven/npm/etc., which themselves fork
        further JVMs/workers over a run that can last minutes - real PID
        churn, not a hypothetical.

        Job Object membership is tracked by the kernel at process-creation
        time instead: every process this job's member creates automatically
        joins the same job (unless it explicitly requests breakaway, which
        this job's LimitFlags do not permit), independent of whether its
        immediate parent is still alive by kill time. `TerminateJobObject`
        kills every current member atomically and is not bounded by an
        external command's own timeout the way shelling out to `taskkill`
        is. It's also self-healing on a hard crash of THIS process: Windows
        closes every handle a process holds when it exits for any reason,
        so KILL_ON_JOB_CLOSE fires even if optarena itself dies uncleanly -
        `taskkill` has no equivalent (it requires this process to still be
        alive to run a separate command).
        """

        def __init__(self):
            self.handle = _kernel32.CreateJobObjectW(None, None)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = _kernel32.SetInformationJobObject(
                self.handle, _JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info))
            if not ok:
                err = ctypes.get_last_error()
                self.close()
                raise ctypes.WinError(err)

        def assign(self, pid: int) -> bool:
            """Add *pid* (and everything it later spawns) to this job.
            False on failure - the process may have already exited, or
            this environment restricts job-object access (some sandboxed/
            nested-container CI runners do); callers fall back to
            `taskkill` in that case rather than assuming coverage."""
            hproc = _kernel32.OpenProcess(
                _PROCESS_TERMINATE | _PROCESS_SET_QUOTA, False, pid)
            if not hproc:
                return False
            try:
                return bool(_kernel32.AssignProcessToJobObject(self.handle, hproc))
            finally:
                _kernel32.CloseHandle(hproc)

        def terminate(self) -> bool:
            if not self.handle:
                return False
            return bool(_kernel32.TerminateJobObject(self.handle, 1))

        def close(self) -> None:
            if self.handle:
                _kernel32.CloseHandle(self.handle)
                self.handle = None


def _kill_process_tree(pid: int, job: "_WindowsJob | None" = None) -> bool:
    """
    Kill an entire process tree started by ``run_capture`` (H-11). A check
    or agent that spawned children (a test that starts a server, an agent
    that shells out) leaves those children ORPHANED when only its direct
    child is killed - they keep holding ports/CPU and cascade failures into
    every later case on this host. On POSIX the child is its own session
    leader (``start_new_session``), so signalling the negative pgid hits the
    whole group. On Windows, ``job`` (a ``_WindowsJob`` the caller assigned
    the process to right after spawning it - see ``run_capture``) is the
    primary, kernel-enforced mechanism (P0-03); ``taskkill /T`` still runs
    unconditionally afterward as a best-effort backstop (a process that
    somehow wasn't successfully assigned to the job, or predates this
    change's rollout in some odd caller).

    Returns True if cleanup is believed to have succeeded (best-effort
    signal, not a hard guarantee) - callers use this to report a cleanup
    failure explicitly instead of silently treating an unreaped tree as a
    normal timeout.
    """
    if os.name == "posix":
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            return True
        except (ProcessLookupError, OSError):
            return False
    job_ok = job.terminate() if job is not None else False
    # No timeout here previously: if taskkill itself hangs (a stuck
    # child, a permissions snag, a complex tree from an agent spawning
    # Maven/Java subprocesses), this blocked run_capture - and the whole
    # case - for an UNBOUNDED time, defeating the timeout this function
    # exists to enforce (observed: a 300s case timeout, a 2222s actual
    # duration). Best-effort like the proc.wait(timeout=5) right below -
    # if taskkill itself won't finish, there's nothing more we can do.
    try:
        result = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                capture_output=True, timeout=10)
        taskkill_ok = result.returncode == 0
    except subprocess.TimeoutExpired:
        taskkill_ok = False
    return job_ok or taskkill_ok


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


def run_capture(cmd, *, timeout: int, pid_callback=None, **kwargs) -> subprocess.CompletedProcess:
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

    P1-07: ``pid_callback(pid, job)``, if given, is invoked once right after
    the process is spawned and (on Windows) assigned to its job object -
    before this function's own blocking wait. This is the hook
    ``_run_check_command_local``'s workspace-quota watchdog uses to kill the
    tree EARLY (via the same ``_kill_process_tree`` this function's own
    timeout path uses) without needing this function's main wait loop to
    know anything about quotas itself - an external kill just makes
    ``proc.wait(timeout=timeout)`` below return early, exactly as if the
    process had exited on its own; the caller distinguishes "killed for
    quota" from "exited normally" via its own watchdog state, not via
    anything this function reports.
    """
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.PIPE)
    job = None
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
        # P0-03: create the job BEFORE spawning, so there is no window where
        # the child could start (and start spawning its own children) before
        # something is watching it. See _WindowsJob's docstring for why this
        # replaced taskkill /T as the primary mechanism.
        try:
            job = _WindowsJob()
        except OSError:
            job = None  # some restricted/sandboxed hosts deny job-object access; taskkill still runs
    proc = subprocess.Popen(cmd, **kwargs)
    if job is not None:
        job.assign(proc.pid)  # best-effort; _kill_process_tree's taskkill fallback covers a False here too
    if pid_callback is not None:
        pid_callback(proc.pid, job)

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
    cleanup_failed = False
    if timed_out:
        cleanup_failed = not _kill_process_tree(proc.pid, job)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass  # best-effort reap; raising regardless
    if job is not None:
        job.close()  # also fires KILL_ON_JOB_CLOSE as a last backstop if terminate() itself failed above

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
        # P0-03: a cleanup failure must be visible, not silently indistin-
        # guishable from an ordinary timeout - both taskkill AND the job
        # object (when one was created) failing to confirm the kill is rare
        # but not impossible, and a case's own timeout message being wrong
        # about whether the tree is actually gone is worse than surfacing it.
        msg = cmd if not cleanup_failed else (
            f"{cmd} [WARNING: process-tree cleanup could not be confirmed - "
            f"a descendant may still be running]")
        raise subprocess.TimeoutExpired(msg, timeout, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _run_check_command_local(cmd: str, root: Path, timeout: int, info: dict) -> tuple[list[str], dict]:
    info["sandbox"] = "host"
    t0 = time.monotonic()
    # P1-07: run_capture's pid_callback hands us the just-spawned pid/job -
    # captured here so the watchdog can kill the tree directly via the
    # SAME _kill_process_tree the timeout path already uses, without
    # run_capture's own wait loop needing to know anything about quotas.
    spawned: dict = {}

    def _on_start(pid, job):
        spawned["pid"], spawned["job"] = pid, job

    def _on_exceeded(_reason):
        if "pid" in spawned:
            _kill_process_tree(spawned["pid"], spawned.get("job"))

    watchdog = _WorkspaceQuotaWatchdog(root, on_exceeded=_on_exceeded).start()
    try:
        try:
            proc = run_capture(
                cmd, shell=True, cwd=root, timeout=timeout,
                text=True, encoding="utf-8", errors="replace",
                pid_callback=_on_start,
            )
        except subprocess.TimeoutExpired:
            info["duration_s"] = round(time.monotonic() - t0, 2)
            info["timed_out"] = True
            return [f'check_command timed out after {timeout}s: {cmd}'], info
        except OSError as exc:
            info["duration_s"] = round(time.monotonic() - t0, 2)
            info["infrastructure_error"] = True  # F-10: couldn't even launch the command - not a test failure
            return [f'check_command could not run: {exc}'], info
    finally:
        watchdog.stop()
        # P1-09: a fast writer that crosses the quota and exits inside one
        # poll interval would otherwise never be caught - this closes that
        # gap without waiting for another poll tick.
        watchdog.check_final()
    # P1-07: checked regardless of how run_capture returned - a quota kill
    # makes proc.returncode look like an ordinary killed process (e.g. -9),
    # not distinguishably "aborted for cause", so the watchdog's own state
    # is the actual signal, not the raw exit code.
    if watchdog.triggered_reason:
        info["duration_s"] = round(time.monotonic() - t0, 2)
        info["workspace_quota_exceeded"] = True
        return [f'check_command aborted: {watchdog.triggered_reason}'], info
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

    def _kill_ephemeral_container(_reason):
        # P1-07: `docker rm -f` immediately SIGKILLs and removes the
        # container - the same cleanup the timeout path below already runs,
        # just triggered by a quota breach instead of a timeout, and early
        # enough to unblock the `subprocess.run` below rather than waiting
        # out its own (much longer) timeout+15 ceiling.
        try:
            subprocess.run([engine, "rm", "-f", name], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass  # best-effort; the engine's own GC reclaims a leftover container eventually

    watchdog = _WorkspaceQuotaWatchdog(root, on_exceeded=_kill_ephemeral_container).start()
    try:
        try:
            proc = subprocess.run(
                docker_cmd, capture_output=True, text=True,
                timeout=timeout + 15, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            _kill_ephemeral_container("timeout")
            info["duration_s"] = round(time.monotonic() - t0, 2)
            info["timed_out"] = True
            return [f'check_command timed out after {timeout}s ({engine}): {cmd}'], info
        except OSError as exc:
            info["duration_s"] = round(time.monotonic() - t0, 2)
            info["infrastructure_error"] = True  # F-10: couldn't even start the container - not a test failure
            return [f'check_command could not run ({engine}): {exc}'], info
    finally:
        watchdog.stop()
        # P1-09: a fast writer that crosses the quota and exits inside one
        # poll interval would otherwise never be caught - this closes that
        # gap without waiting for another poll tick.
        watchdog.check_final()
    if watchdog.triggered_reason:
        info["duration_s"] = round(time.monotonic() - t0, 2)
        info["workspace_quota_exceeded"] = True
        return [f'check_command aborted: {watchdog.triggered_reason}'], info
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
