"""
optarena/security.py
────────────────────
A dependency-free static scanner over the files an agent actually changed, so a
run can answer "did the tool introduce a secret / injection / unsafe call?" -
not just "is it correct?". This is the miss that matters most for the
security-conscious, local-model audience: as agents write more production code,
"passed the tests but hardcoded an API key / built a shell string from input" is
exactly the failure a correctness oracle can't see.

Opt-in via ``optarena run --security-scan`` (attached to each case's
``extra.security``) and standalone via ``optarena scan <dir>``. Findings feed
``optarena report --format sarif`` so GitHub code-scanning shows them on the PR.

Intentionally regex-based and stdlib-only (matching the core's zero-dep ethos) -
high-signal rules, `note`/`warning`/`error` levels, and placeholder-aware secret
detection to keep false positives down. Not a replacement for semgrep/bandit;
a fast, always-available first pass.
"""

from __future__ import annotations

import re
from pathlib import Path

# Files/dirs never worth scanning (tests are graded separately; vendored deps
# aren't the agent's output).
_SKIP_PARTS = {".git", "node_modules", "__pycache__", "target", "vendor", "dist", "build"}

# Obvious non-secrets, so `password = "changeme"` in a template doesn't cry wolf.
_PLACEHOLDER = re.compile(
    r"^(?:changeme|example|placeholder|your[_-]?\w*|xx+|test|dummy|secret|password|"
    r"redacted|\.{3}|<[^>]+>|\$\{[^}]+\}|%\([^)]+\)s|process\.env|os\.environ)",
    re.IGNORECASE,
)


class Rule:
    def __init__(self, rule_id, title, level, pattern, exts=None, is_secret=False):
        self.id = rule_id
        self.title = title
        self.level = level                 # "error" | "warning" | "note"
        self.re = re.compile(pattern)
        self.exts = exts                   # set of extensions, or None = all
        self.is_secret = is_secret


_PY = {".py"}
_JS = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}

RULES: list[Rule] = [
    # ── secrets (any file type) ──────────────────────────────────────────────
    Rule("aws-access-key", "AWS access key ID committed", "error",
         r"\bAKIA[0-9A-Z]{16}\b", is_secret=True),
    Rule("private-key", "Private key committed", "error",
         r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", is_secret=True),
    Rule("github-token", "GitHub token committed", "error",
         r"\bgh[pousr]_[A-Za-z0-9]{36,}\b", is_secret=True),
    Rule("provider-api-key", "Provider API key committed", "error",
         r"\bsk-[A-Za-z0-9_-]{20,}\b", is_secret=True),
    Rule("hardcoded-secret", "Hardcoded credential", "warning",
         r"""(?i)(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"""
         r"""\s*[:=]\s*['"]([^'"\s]{6,})['"]""", is_secret=True),

    # ── dangerous execution / injection (Python) ─────────────────────────────
    Rule("py-shell-true", "subprocess with shell=True (command-injection risk)", "warning",
         r"subprocess\.(?:run|call|Popen|check_output|check_call)\([^)]*shell\s*=\s*True", exts=_PY),
    Rule("py-os-system", "os.system() (command-injection risk)", "warning",
         r"\bos\.system\s*\(", exts=_PY),
    Rule("py-eval-exec", "eval()/exec() on dynamic input", "warning",
         r"\b(?:eval|exec)\s*\(\s*(?!['\"])", exts=_PY),
    Rule("py-sql-fstring", "SQL built by string formatting (SQL-injection risk)", "error",
         r"""(?i)(?:execute|executemany)\s*\(\s*(?:f['"]|['"][^'"]*['"]\s*[%+]|['"][^'"]*['"]\.format)""",
         exts=_PY),
    Rule("py-yaml-load", "yaml.load without a safe Loader", "warning",
         r"yaml\.load\s*\((?![^)]*Loader\s*=\s*yaml\.(?:Safe|C?Safe)Loader)", exts=_PY),
    Rule("py-pickle-loads", "pickle.loads on external data (deserialization risk)", "note",
         r"\bpickle\.loads?\s*\(", exts=_PY),

    # ── dangerous execution / injection (JS/TS) ──────────────────────────────
    Rule("js-child-exec", "child_process.exec with interpolation (command-injection risk)", "warning",
         r"""(?:child_process\.)?exec(?:Sync)?\s*\(\s*[`'"][^`'"]*(?:\$\{|['"]\s*\+)""", exts=_JS),
    Rule("js-eval", "eval() on dynamic input", "warning",
         r"\beval\s*\(\s*(?!['\"])", exts=_JS),
    Rule("js-innerhtml", "innerHTML assigned from a variable (XSS risk)", "note",
         r"\.innerHTML\s*=\s*(?!['\"`])", exts=_JS),
]


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER.match(value.strip()))


def scan_text(text: str, rel_path: str) -> list[dict]:
    """Findings for one file's contents. ``rel_path`` sets the extension filter
    and is reported as the finding location."""
    ext = Path(rel_path).suffix.lower()
    active = [r for r in RULES if r.exts is None or ext in r.exts]
    if not active:
        return []
    findings = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if len(line) > 4000:              # skip minified/vendored megalines
            continue
        for rule in active:
            m = rule.re.search(line)
            if not m:
                continue
            if rule.is_secret:
                captured = m.group(1) if m.groups() else m.group(0)
                if _is_placeholder(captured):
                    continue
            findings.append({
                "rule": rule.id, "title": rule.title, "level": rule.level,
                "message": rule.title, "file": rel_path, "line": lineno,
                "snippet": line.strip()[:160],
            })
    return findings


def scan_workspace(files: list[str], root: Path) -> dict:
    """Scan the agent's changed ``files`` (relative paths under ``root``).
    Returns ``{"findings": [...], "counts": {level: n}, "total": n}`` - always a
    dict, so callers can store it unconditionally."""
    root = Path(root)
    findings: list[dict] = []
    for rel in files:
        if any(part in _SKIP_PARTS for part in Path(rel).parts):
            continue
        p = root / rel
        try:
            if not p.is_file() or p.stat().st_size > 1_000_000:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        findings.extend(scan_text(text, rel))
    counts = {"error": 0, "warning": 0, "note": 0}
    for f in findings:
        counts[f["level"]] = counts.get(f["level"], 0) + 1
    return {"findings": findings, "counts": counts, "total": len(findings)}
