"""
optarena/cases.py
──────────────
Case catalogue. A *case* is a task given to a tool plus a filesystem oracle that
decides pass/fail - the JSON schema shared with the original cline harness:

    {
      "name":           str,
      "description":    str,
      "prompts":        [str, ...],          # sent in order
      "setup_files":    {relpath: content},  # written BEFORE the run (visible
                                             # to the model - e.g. the file a
                                             # "modify" case edits)
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
                                             # workspace (inside Docker when
                                             # available; see run_check_command);
                                             # non-zero exit fails the case
      "check_command_timeout": int,          # seconds (default 60)
      "docker_image":          str,          # optional: which sandbox image this
                                             # case needs (see DOCKER_IMAGES in
                                             # drivers-adjacent docker/ dir);
                                             # defaults to DOCKER_IMAGE_DEFAULT
      "timeout":        int,                 # seconds

      # Benchmark-corpus metadata (OptArena_Benchmark_Corpus_Specification.md).
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
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

CASES_DIR = Path(__file__).parent / "cases"
DOCKER_IMAGE_DEFAULT = "optarena-tester:latest"
DOCKERFILE_DIR = Path(__file__).resolve().parent.parent / "docker"

# Registry of every sandbox image OptArena knows how to build, keyed by the
# short name used with `optarena docker build --lang <key>`. "base" is the
# original combined image (gcc + python3 + node) and stays the default for
# cases with no `docker_image` set, so the 7 original cases are unaffected.
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
}


def dockerfile_for(lang: str) -> Path:
    """Path to the Dockerfile for a `DOCKER_IMAGES` key. "base" lives directly
    under docker/ (the original combined image); every other track gets its
    own docker/<lang>/ subdirectory."""
    return DOCKERFILE_DIR / "Dockerfile" if lang == "base" else DOCKERFILE_DIR / lang / "Dockerfile"


def load_cases(names: list[str] | None = None, cases_dir: "Path | str | None" = None) -> list[dict]:
    """Load all (or the named) cases from *cases_dir*, sorted by filename."""
    directory = Path(cases_dir) if cases_dir else CASES_DIR
    cases = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(directory.glob("*.json"))
    ]
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


_docker_checked = False
_docker_ok = False
_docker_warned = False


def _docker_available() -> bool:
    """Cached check for a reachable Docker daemon (one `docker info` per process)."""
    global _docker_checked, _docker_ok
    if _docker_checked:
        return _docker_ok
    _docker_checked = True
    try:
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10,
        )
        _docker_ok = proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        _docker_ok = False
    return _docker_ok


def docker_image_available(image: str = DOCKER_IMAGE_DEFAULT) -> bool:
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True, timeout=10,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


# Keyed by Docker image tag rather than a single slot - a run whose cases
# span multiple languages (e.g. --cases includes both a Python and a Go
# case) needs one long-lived container PER distinct image, not one overall.
_active_sandboxes: dict[str, "DockerSandbox"] = {}


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
    ``with DockerSandbox(root) as sandbox:``. If Docker isn't available (or
    ``OPTARENA_NO_DOCKER=1``), ``start()`` is a no-op and callers fall back to
    running check_command on the host - unchanged from before.
    """

    def __init__(self, root: Path, image: str | None = None):
        self.root = root.resolve()
        self.image = image or os.environ.get("OPTARENA_DOCKER_IMAGE", DOCKER_IMAGE_DEFAULT)
        self.name = f"optarena-sandbox-{uuid.uuid4().hex[:12]}"
        self.active = False

    def start(self) -> bool:
        if os.environ.get("OPTARENA_NO_DOCKER") == "1" or not _docker_available():
            return False
        if not docker_image_available(self.image):
            print(
                f"[optarena] Docker image '{self.image}' not found - check_command "
                f"will run on the host. Run `optarena docker build` to build the "
                f"sandboxed test image.",
                file=sys.stderr,
            )
            return False
        try:
            subprocess.run(
                ["docker", "run", "-d", "--rm", "--name", self.name,
                 "--network", "none", "--memory", "2g", "--cpus", "2",
                 "-v", f"{self.root}:/workspace", "-w", "/workspace",
                 self.image, "sleep", "infinity"],
                capture_output=True, timeout=20, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            print(f"[optarena] could not start docker sandbox: {exc}", file=sys.stderr)
            return False
        self.active = True
        _active_sandboxes[self.image] = self
        print(f"[optarena] docker sandbox: {self.name} (image {self.image}) - "
              f"one container for this whole run")
        return True

    def stop(self) -> None:
        if self.active:
            subprocess.run(["docker", "stop", "-t", "2", self.name], capture_output=True)
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
            "docker", "exec", "-w", f"/workspace/{rel}", self.name,
            "timeout", f"{timeout}s", "sh", "-c", cmd,
        ]
        return subprocess.run(
            full_cmd, capture_output=True, text=True,
            timeout=timeout + 10, encoding="utf-8", errors="replace",
        )


def _new_oracle_info(cmd: str | None) -> dict:
    return {
        "check_command": cmd,
        "ran": False,
        "sandbox": None,     # "docker" | "host" | None
        "image": None,
        "exit_code": None,
        "duration_s": None,
        "output": "",
    }


def run_check_command(case: dict, root: Path) -> tuple[list[str], dict]:
    """
    Run the case's optional ``check_command`` and return
    ``(failure_strings, oracle_info)``. Empty failure list = passed or no
    command configured. ``oracle_info`` always reports what actually
    happened (sandboxed in Docker vs run on the host, exit code, timing, and
    a tail of captured output) so callers can show it, not just the verdict.

    This is the second, behavioral oracle stage: content patterns assert
    shape, the command actually compiles/runs the code and asserts on its
    real output (see ``test_setup_files``). When an active ``DockerSandbox``
    exists for this case's required image (set up once by
    ``runner.run_scenario`` for the whole run - possibly several, one per
    distinct image a run's cases need), this execs into that ONE shared
    container - it does not start a new one per case/trial. Without a
    matching active sandbox (e.g. ``evaluate_case`` called directly, outside
    the runner), it falls back to one ephemeral ``docker run --rm`` for this
    call, or to the host (with a one-time warning) when Docker is
    unavailable or disabled via ``OPTARENA_NO_DOCKER=1``.
    """
    global _docker_warned
    cmd = case.get("check_command")
    info = _new_oracle_info(cmd)
    if not cmd:
        return [], info
    timeout = int(case.get("check_command_timeout", 60) or 60)
    docker_disabled = os.environ.get("OPTARENA_NO_DOCKER") == "1"
    image = case.get("docker_image") or os.environ.get("OPTARENA_DOCKER_IMAGE", DOCKER_IMAGE_DEFAULT)

    active = _active_sandboxes.get(image)
    if not docker_disabled and active is not None:
        return _run_check_command_sandbox(cmd, root, timeout, active, info)

    # `_docker_available()` is short-circuited away entirely when the user
    # opted out - it must not shell out to `docker info` in that case.
    use_docker = (not docker_disabled) and _docker_available()

    if use_docker and not docker_image_available(image):
        use_docker = False
        if not _docker_warned:
            print(
                f"[optarena] Docker image '{image}' not found - falling back to "
                f"running check_command on the host. Run `optarena docker build` "
                f"to build the sandboxed test image.",
                file=sys.stderr,
            )
            _docker_warned = True

    if not use_docker:
        if not docker_disabled and not _docker_ok and not _docker_warned:
            print(
                "[optarena] Docker not available - running check_command directly "
                "on the host. Install/start Docker Desktop for sandboxed, "
                "dependency-free test execution (recommended).",
                file=sys.stderr,
            )
            _docker_warned = True
        return _run_check_command_local(cmd, root, timeout, info)
    return _run_check_command_docker(cmd, root, timeout, image, info)


def _run_check_command_sandbox(cmd: str, root: Path, timeout: int, sandbox: "DockerSandbox", info: dict) -> tuple[list[str], dict]:
    info["sandbox"] = "docker"
    info["image"] = sandbox.image
    info["container"] = sandbox.name
    t0 = time.monotonic()
    try:
        proc = sandbox.exec(cmd, root, timeout)
    except subprocess.TimeoutExpired:
        info["duration_s"] = round(time.monotonic() - t0, 2)
        return [f'check_command timed out after {timeout}s (docker exec): {cmd}'], info
    except (OSError, ValueError) as exc:
        info["duration_s"] = round(time.monotonic() - t0, 2)
        return [f'check_command could not run (docker exec): {exc}'], info
    info["duration_s"] = round(time.monotonic() - t0, 2)
    info["ran"] = True
    info["exit_code"] = proc.returncode
    info["output"] = ((proc.stdout or "") + (proc.stderr or ""))[-400:].strip()
    if proc.returncode == 124:
        return [f'check_command timed out after {timeout}s (docker exec): {cmd}'], info
    if proc.returncode != 0:
        return ([f'check_command failed in docker (exit {proc.returncode}): {cmd}'
                 + (f' :: {info["output"]}' if info["output"] else "")], info)
    return [], info


def _run_check_command_local(cmd: str, root: Path, timeout: int, info: dict) -> tuple[list[str], dict]:
    info["sandbox"] = "host"
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=root,
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        info["duration_s"] = round(time.monotonic() - t0, 2)
        return [f'check_command timed out after {timeout}s: {cmd}'], info
    except OSError as exc:
        info["duration_s"] = round(time.monotonic() - t0, 2)
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
    docker_cmd = [
        "docker", "run", "--rm", "--name", name,
        "--network", "none",
        "--memory", "512m", "--cpus", "1",
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
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        info["duration_s"] = round(time.monotonic() - t0, 2)
        return [f'check_command timed out after {timeout}s (docker): {cmd}'], info
    except OSError as exc:
        info["duration_s"] = round(time.monotonic() - t0, 2)
        return [f'check_command could not run (docker): {exc}'], info
    info["duration_s"] = round(time.monotonic() - t0, 2)
    info["ran"] = True
    info["exit_code"] = proc.returncode
    info["output"] = ((proc.stdout or "") + (proc.stderr or ""))[-400:].strip()
    if proc.returncode != 0:
        return ([f'check_command failed in docker (exit {proc.returncode}): {cmd}'
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
    if not oracle_info.get("ran") or oracle_info.get("exit_code") in (None, 0):
        return None
    if "timed out" in (oracle_info.get("output") or "").lower():
        return "timeout"
    output_lower = (oracle_info.get("output") or "").lower()
    cmd_lower = (oracle_info.get("check_command") or "").lower()
    if any(s in output_lower for s in ("syntaxerror", "indentationerror", "unterminated", "unexpected token")):
        return "syntax_error"
    if "gcc" in cmd_lower and any(
        s in output_lower for s in ("error:", "undefined reference", "collect2:", "calledprocesserror")
    ):
        return "compile_error"
    if "assertionerror" in output_lower or "assert " in output_lower:
        return "assertion_failure"
    return "runtime_error"


def diff_stats(case: dict, created: list[str], root: Path) -> dict:
    """
    Approximate size of the change for this case: files touched and lines
    changed. For "modify" cases the delta is against the known original
    content in ``setup_files`` (the pre-run snapshot only stores a
    size:mtime signature, not content, so this is the best available
    reference); new files count their full line length.
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
        return failures, info
    failures, info = run_check_command(case, root)
    info["test_setup_files"] = test_files
    info["diff"] = diff_stats(case, created, root)
    if failures:
        info["failure_class"] = classify_failure(info)
    return failures, info


def write_setup_files(root: Path, setup_files: dict[str, str] | None) -> None:
    for rel, content in (setup_files or {}).items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # newline="" disables universal-newline translation - on Windows hosts,
        # write_text() would otherwise turn every "\n" in case JSON content
        # into "\r\n", which is silently tolerated by most languages but
        # corrupts POSIX shell scripts (a stray \r glued to `do`/`done`/etc.
        # breaks dash/sh parsing) once bind-mounted into the Linux sandbox.
        dest.write_text(content, encoding="utf-8", newline="")
