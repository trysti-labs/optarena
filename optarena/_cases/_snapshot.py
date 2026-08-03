"""Workspace content hashing (`snapshot`/`changed_files`) and the
expected-file assertion oracle (`check_expected`/`path_pattern_matches`)."""

from __future__ import annotations

import fnmatch
import hashlib
import re
import sys
from pathlib import Path

from ._constants import IGNORE_DIRS

# P1-06: a case's check_command and an agent's own actions have real disk
# access inside the workspace; nothing bounded how much of it snapshot()
# would walk, hash, or read into memory before this. Numbers are generous -
# no real corpus workspace comes close - not tuned to any specific case.
_SNAPSHOT_MAX_ENTRIES = 20_000          # raw rglob entries inspected, not just files kept
_SNAPSHOT_MAX_FILE_BYTES = 64 * 1024 * 1024      # per-file: beyond this, a size-only signature
_SNAPSHOT_MAX_TOTAL_BYTES = 512 * 1024 * 1024    # total bytes actually read+hashed in one call


def snapshot(root: Path) -> dict[str, str]:
    """Map each file (relative posix path) to a content signature (sha1).

    Content-based rather than size:mtime - a same-size rewrite landing within
    one filesystem-timestamp tick (routine in verify-corpus, which writes the
    setup file and the solution file back-to-back) was invisible to the old
    signature, making changed-file detection flaky on coarse-mtime
    filesystems (Windows hosts).

    P1-06: symlinks are skipped, not followed - `Path.is_file()` alone
    resolves symlinks, so a symlink an agent or check_command creates
    (pointing outside the workspace, or - inside the container sandbox - at
    a container-internal file the agent has no business reading) would
    otherwise have its TARGET's content read and hashed here, and that
    content then flows into `result.files`/security scanning/dashboard
    display as if it were the agent's own output. A symlinked DIRECTORY
    earlier in a path isn't caught by a leaf-level `is_symlink()` check, so
    every path is also fully resolved and confirmed still inside `root` -
    the same defense `copy_setup_repo` already applies, for the same reason.
    Entry count, per-file size, and total bytes hashed are all capped; a
    file beyond the per-file cap gets a size-only fallback signature
    (`changed_files` can still detect IT changed, just not hash its full
    content) instead of an unbounded read. Any truncation is reported via a
    stderr warning - visible, not silent.
    """
    root = root.resolve()
    out: dict[str, str] = {}
    total_bytes = 0
    seen = 0
    hit_entry_cap = False
    skipped_symlinks = 0
    size_only = 0
    for p in root.rglob("*"):
        seen += 1
        if seen > _SNAPSHOT_MAX_ENTRIES:
            hit_entry_cap = True
            break
        if p.is_symlink():
            skipped_symlinks += 1
            continue
        if not p.is_file():
            continue
        rel_parts = p.relative_to(root).parts
        if any(part in IGNORE_DIRS for part in rel_parts):
            continue
        # aider writes .aider.chat.history.md etc. as FILES (IGNORE_DIRS only
        # covers directories) - tool bookkeeping, not model output; keep them
        # out of the diff so they don't inflate the files/lines metrics.
        if p.name.startswith(".aider"):
            continue
        try:
            if not p.resolve().is_relative_to(root):
                skipped_symlinks += 1
                continue
        except OSError:
            continue
        rel = p.relative_to(root).as_posix()
        try:
            size = p.stat().st_size
        except OSError:
            continue        # vanished/locked mid-scan - treat as absent
        if size > _SNAPSHOT_MAX_FILE_BYTES or total_bytes + size > _SNAPSHOT_MAX_TOTAL_BYTES:
            out[rel] = f"size-only:{size}"
            size_only += 1
            continue
        try:
            digest = hashlib.sha1(p.read_bytes()).hexdigest()
        except OSError:
            continue        # vanished/locked mid-scan - treat as absent
        total_bytes += size
        out[rel] = digest
    if hit_entry_cap or skipped_symlinks or size_only:
        print(
            f"[optarena] workspace snapshot truncated under {root}: "
            + (f"stopped after {_SNAPSHOT_MAX_ENTRIES} entries; " if hit_entry_cap else "")
            + (f"{skipped_symlinks} symlink(s) skipped; " if skipped_symlinks else "")
            + (f"{size_only} file(s) too large to hash fully (size-only signature)"
               if size_only else ""),
            file=sys.stderr,
        )
    return out


def changed_files(before: dict[str, str], root: Path) -> list[str]:
    """Files that are new OR modified since *before*."""
    current = snapshot(root)
    return [rel for rel, sig in current.items() if before.get(rel) != sig]


def path_pattern_matches(rel: str, pattern: str) -> bool:
    """
    Does workspace-relative path `rel` satisfy an expected-file `pattern`?
    Case-insensitive throughout (established behavior, unchanged). Two ways,
    same as before:
      - the file's basename alone matches the whole pattern (lets a bare
        pattern like "Foo.java" - no directory component at all - match a
        file at any depth, via its name)
      - the full relative path matches the pattern as written

    A-39: for a pattern with a leading "**/" component, ALSO tries the match
    with that prefix stripped - so "**/Foo.java" is satisfied by a bare
    "Foo.java" sitting at the workspace root, not only a nested one.

    `fnmatch` has no concept of "/" as a path separator - `*` matches any
    characters including "/" - so a "**/X" pattern can only ever match a
    candidate that LITERALLY CONTAINS a "/" character; a flat file at the
    root can never satisfy it under a plain `fnmatch.fnmatch` call, even
    though every other tool's globstar convention (bash's `globstar`, rsync
    exclude patterns, Python 3.13's own `glob.translate`/`pathlib` matching,
    Ant filesets) defines "**" as "zero or more directories", which
    explicitly includes the zero case. Nobody deliberately chose the
    stricter reading here - no comment anywhere in this module addressed it
    - and it was silently making the raw-model baseline / SDK-agent drivers
    (which write one flat file with no directory structure - see
    `drivers/openai_chat.py`'s `concrete_target`) fail 108 corpus cases
    whose only problem was a `**/`-prefixed pattern, even when the file's
    name and content were exactly right.

    Deliberately narrow: only an EXACT "**/" prefix is special-cased, not
    "**" appearing elsewhere in a pattern - that is the only shape used
    anywhere in the built-in corpus (verified by scanning every
    `expected_files[].path_pattern` in `optarena/cases/*.json`), so this
    covers what's actually needed rather than reimplementing a general
    glob-to-regex translator for shapes that don't exist here. A single "*"
    prefix (e.g. "*/routes/foo.ts") is a different, deliberately
    UNCHANGED case: single-star conventionally means "exactly one path
    segment", not "zero or more". `concrete_target` has no way to invent an
    arbitrary wrapper directory name for it, so that class of case stays
    genuinely unsatisfiable by a flat-file-writing driver, which is correct.
    """
    name, rel_l, pattern_l = Path(rel).name.lower(), rel.lower(), pattern.lower()
    if fnmatch.fnmatch(name, pattern_l) or fnmatch.fnmatch(rel_l, pattern_l):
        return True
    if pattern_l.startswith("**/"):
        stripped = pattern_l[3:]
        return fnmatch.fnmatch(rel_l, stripped) or fnmatch.fnmatch(name, stripped)
    return False


def baseline_incompatible(case: dict) -> "str | None":
    """
    A-40: can a flat-file-writing driver (`file_tools: False` in the
    `DRIVERS` registry - the raw-model baselines and every SDK-agent driver,
    all of which write ONE block of text to a single path with no directory
    structure) EVER satisfy this case, regardless of what the model writes?
    Returns a one-line reason if not, else None.

    Found by tracing an unexplained 15% jvm pass rate, during a gemma4:12b
    corpus calibration run, to cases the baseline driver could never have
    won in the first place - the model's output was correct, sitting right
    in the oracle's own "got:" message, just not at a path the case's
    pattern could ever match from a flattened write. Nothing warned about
    this before a run started; a user just saw a confusingly low pass rate.

    Deliberately reuses `drivers.openai_chat.concrete_target` (what path a
    flat-file driver actually writes to) and `path_pattern_matches` (whether
    that path satisfies the pattern) - the SAME two functions that decide
    the real outcome at run time - rather than a separately maintained
    heuristic. Two independent copies of this same decision silently
    disagreeing is exactly the bug A-39 fixed one function over
    (`trajectory_stats`'s `_matches_expected` had drifted from
    `check_expected`); this avoids creating a third copy.
    """
    if case.get("setup_repo"):
        return "needs a starter repo (setup_repo) - a flat-file writer starts from an empty workspace"
    expected = case.get("expected_files") or []
    if len(expected) > 1:
        return f"needs {len(expected)} separate files - a flat-file writer produces exactly one"
    if not expected:
        return None
    pattern = expected[0].get("path_pattern", "")
    if not pattern:
        return None
    # Local import: the case engine is the low-level layer every driver
    # imports FROM (drivers -> cases, never the reverse) - a module-level
    # import here would create drivers.openai_chat -> _cases._snapshot ->
    # drivers.openai_chat.
    from ..drivers.openai_chat import concrete_target
    target = concrete_target(pattern).as_posix()
    if path_pattern_matches(target, pattern):
        return None
    return (f'the only path a flat-file writer can produce for "{pattern}" is '
           f'"{target}", which does not itself satisfy the pattern')


def check_expected(created: list[str], expected_spec: list[dict], root: Path) -> list[str]:
    """Return failure strings; empty list ⇒ the case passed."""
    failures: list[str] = []
    for spec in expected_spec or []:
        pattern = spec["path_pattern"]
        match = next(
            (rel for rel in created if path_pattern_matches(rel, pattern)),
            None,
        )
        if match is None:
            failures.append(
                f'expected file matching "{pattern}" not created '
                f'(got: {", ".join(created) or "none"})'
            )
            continue
        try:
            target = root / match
            # P1-06: content_patterns/regex_patterns below read the WHOLE
            # file into memory - `snapshot`'s own size cap only protects
            # its own hash, not this separate read of an already-created
            # file. A real expected file is always small source code;
            # anything past this is either a pathological check-command
            # side effect or an adversarial oracle probe, not a legitimate
            # case - reported as a failure (visible), not read regardless.
            if target.stat().st_size > _SNAPSHOT_MAX_FILE_BYTES:
                failures.append(
                    f'"{match}" is too large to check content against '
                    f'({target.stat().st_size} bytes > {_SNAPSHOT_MAX_FILE_BYTES})')
                continue
            raw = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            failures.append(f'could not read "{match}": {exc}')
            continue
        content = raw.lower()
        # A-17: `case_sensitive: true` opts this spec's assertions out of the
        # historical lowercase-everything behaviour. Content was lowercased
        # AND then matched with re.IGNORECASE, so a case could never require
        # `class UserDTO` over `class userdto`, `SELECT` over `select`, or a
        # Go exported identifier over an unexported one - not even with an
        # explicitly case-sensitive regex. Default stays False so every
        # existing case behaves exactly as before.
        strict = bool(spec.get("case_sensitive"))
        haystack = raw if strict else content
        flags = 0 if strict else re.IGNORECASE
        for needle in spec.get("content_patterns", []):
            if (str(needle) if strict else str(needle).lower()) not in haystack:
                failures.append(f'"{match}" missing expected content "{needle}"')
        for needle in spec.get("not_content_patterns", []):
            if (str(needle) if strict else str(needle).lower()) in haystack:
                failures.append(f'"{match}" contains forbidden content "{needle}"')
        for pattern_re in spec.get("regex_patterns", []):
            try:
                if not re.search(pattern_re, haystack, flags):
                    failures.append(f'"{match}" does not match regex "{pattern_re}"')
            except re.error as exc:
                failures.append(f'invalid regex "{pattern_re}": {exc}')
        min_lines = spec.get("min_lines")
        if isinstance(min_lines, int) and min_lines > 0:
            n_lines = len(content.splitlines())
            if n_lines < min_lines:
                failures.append(f'"{match}" has {n_lines} line(s), expected >= {min_lines}')
    return failures


def normalize_workspace_line_endings(root: Path) -> None:
    """
    Best-effort CRLF/CR -> LF normalization for every text file in the case
    workspace, run once right before ``check_command`` executes.

    On a Windows host, a workspace file can end up CRLF-terminated through
    TWO paths this project doesn't otherwise control: (1) any driver's own
    ``write_text(..., encoding="utf-8")`` without ``newline=""`` - Python's
    universal-newline translation on write, the same issue already handled
    for case-authored setup content above - and (2) a third-party CLI tool
    (aider, opencode, goose, ...) doing its OWN file I/O directly in the
    workspace, which optarena has no write call to patch at all. A stray
    ``\\r`` glued to `do`/`done`/`then`/etc. breaks dash/sh parsing once
    bind-mounted into the Linux sandbox - found live via a qwen3-coder+aider
    run where a syntactically-correct, semantically-correct generated shell
    script failed with `Syntax error: end of file unexpected (expecting
    "then")`, purely from CRLF corruption never touching the actual logic.
    Normalizing the whole workspace right before the one place that actually
    executes inside Linux covers both sources in a single spot, regardless
    of which driver or tool produced the file.

    Skips anything that looks binary (a NUL byte in the first 8KB) so this
    never corrupts a real binary fixture, and skips symlinks (matching
    ``copy_setup_repo``'s own defense-in-depth). Best-effort per file - one
    unreadable/locked file must not abort the whole check.
    """
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\r" not in data:
            continue
        if b"\x00" in data[:8192]:
            continue  # looks binary, leave it alone
        normalized = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        if normalized != data:
            try:
                path.write_bytes(normalized)
            except OSError:
                continue
