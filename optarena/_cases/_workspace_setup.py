"""Populating a case workspace: setup_files, setup_repo, git_init, and
mid-session disruptions."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from ..schema import reject_unsafe_relpath
from ._constants import REPOS_DIR


def write_setup_files(root: Path, setup_files: dict[str, str] | None) -> None:
    # H-01: `rel` comes straight from case JSON (`setup_files`/`test_setup_files`
    # keys) - an absolute path or a "../" traversal there would write outside
    # the sandboxed workspace, onto the host. `root / rel` alone doesn't catch
    # this: pathlib silently discards `root` entirely when `rel` is absolute,
    # and ".." components resolve upward without error. Resolve and confirm
    # containment before ever touching disk.
    root = root.resolve()
    for rel, content in (setup_files or {}).items():
        # P0-01/P0-02: schema.validate_case already rejects '..' and '.git'
        # segments before a case is even loaded; this re-check is the
        # load-bearing one for any caller that reaches here with content
        # that skipped that gate (verify.py's reference_solution/
        # broken_solutions variants, disruption write_files via
        # _fire_disruption below) - the actual write happens here, so this
        # is the last point that can still refuse it.
        reject_unsafe_relpath(rel, "setup file")
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


def copy_setup_repo(root: Path, repo_name: str) -> None:
    """Copy every file from repos/<repo_name>/ into the workspace root. Used
    by L3 cases (`setup_repo`) so a 20-100 file starter app lives once on
    disk instead of being inlined into every case JSON that shares it.

    A-04: the SOURCE is containment-checked against REPOS_DIR, not just the
    destination. `repo_name` comes straight from case JSON - which is
    untrusted input, since `optarena cases install <url>` will happily
    install a pack authored by anyone - so a value like "../.." used to walk
    out of repos/ and recursively copy an arbitrary host directory INTO the
    case workspace, where the agent reads it as context and the baseline/SDK
    drivers feed it back to whatever backend URL the scenario configures.
    That is an arbitrary-host-file-read-to-remote-endpoint primitive driven
    by case content, and it contradicted SECURITY.md's containment guarantee.
    `schema.validate_case` additionally rejects separators up front; this is
    the load-bearing check.
    """
    repos_root = REPOS_DIR.resolve()
    src = (repos_root / repo_name).resolve()
    if not src.is_relative_to(repos_root) or src == repos_root:
        raise ValueError(
            f'setup_repo "{repo_name}" escapes the starter-repo directory ({repos_root})'
        )
    if not src.is_dir():
        raise FileNotFoundError(f'setup_repo "{repo_name}" not found under {REPOS_DIR}')
    root = root.resolve()
    for p in src.rglob("*"):
        # Symlinks are skipped outright (defense in depth): `p.is_file()`
        # follows a symlink, so a starter repo containing one could copy
        # arbitrary host file content into the workspace - including from
        # outside repos/, which the source check above would otherwise not
        # see. Starter repos don't need symlinks regardless.
        if p.is_symlink() or not p.is_file():
            continue
        # A symlinked *directory* anywhere above this file would put its real
        # content outside src even though `p` itself isn't a symlink - check
        # the fully-resolved source too, not just the entry we walked to.
        if not p.resolve().is_relative_to(src):
            continue
        rel = p.relative_to(src)
        # P0-01: a starter repo is a maintained, trusted asset today, but
        # `setup_repo` names a directory chosen by case JSON - defense in
        # depth against a future/untrusted repos/ entry containing its own
        # .git/, which git_init_workspace would otherwise pick up verbatim.
        reject_unsafe_relpath(str(rel), "setup_repo file")
        dest = (root / rel).resolve()
        if not dest.is_relative_to(root):
            raise ValueError(f"setup_repo file path escapes workspace root: {p!r}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(p.read_bytes())


def git_init_workspace(root: Path) -> None:
    """Commit the freshly-prepared workspace as a one-commit git repo, so an
    L3 case's model edits land as a realistic diff against a checked-in
    baseline instead of an untracked directory. Best-effort: a sandbox image
    without `git` on PATH just skips it silently - no case's oracle depends
    on the repo actually existing, only on the files being there.

    P0-01: by the time this runs, `write_setup_files`/`copy_setup_repo`/
    `_fire_disruption` have already refused any case-controlled path under
    `.git/` (schema.reject_unsafe_relpath, both at validate_case time and
    again right before each write) - so there is nothing under `root/.git`
    for `git add -A` to pick up except what `git init` itself just created.
    The environment below is a second, independent layer: instead of
    inheriting the full host environment (which previously included
    whatever HOME/global gitconfig the operator has, and every other
    variable in their shell), git gets a minimal explicit env and an empty
    scratch HOME/USERPROFILE it cannot have written to - so even a gap in
    the path check above could only reach a config git never reads.
    """
    with tempfile.TemporaryDirectory(prefix="optarena_gitenv_") as isolated_home:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "GIT_AUTHOR_NAME": "optarena", "GIT_AUTHOR_EMAIL": "optarena@local",
            "GIT_COMMITTER_NAME": "optarena", "GIT_COMMITTER_EMAIL": "optarena@local",
            "GIT_CONFIG_NOSYSTEM": "1",   # skip /etc/gitconfig
            "HOME": isolated_home, "USERPROFILE": isolated_home,  # skip ~/.gitconfig
            "GIT_TERMINAL_PROMPT": "0",
        }
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
        reject_unsafe_relpath(rel, "disruption delete_files")
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
