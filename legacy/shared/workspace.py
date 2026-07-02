"""
shared/workspace.py
───────────────────
Temporary workspace setup, file snapshot/diff, and expected-file validation.
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Optional

CODE_EXTENSIONS = {
    ".c", ".cpp", ".h", ".py", ".js", ".ts", ".java", ".go",
    ".rs", ".cs", ".rb", ".sh", ".md", ".json", ".yaml", ".yml",
    ".html", ".css", ".sql",
}

# Directories to ignore when scanning for new files
_IGNORE_DIRS = {".vscode", ".cline", ".git", "__pycache__", "node_modules"}


def setup_workspace(ws: Path, setup_files: Optional[dict] = None) -> None:
    """
    Create the workspace directory and write:
    - `.vscode/settings.json` with all auto-approve flags enabled
    - Any files in *setup_files* (relative path → content string)
    """
    ws.mkdir(parents=True, exist_ok=True)
    vsc = ws / ".vscode"
    vsc.mkdir(exist_ok=True)
    (vsc / "settings.json").write_text(json.dumps({
        # Cline / Roo-Cline auto-approve
        "cline.alwaysAllowWrite":         True,
        "cline.alwaysAllowRead":          True,
        "cline.alwaysAllowExecute":       False,
        "cline.alwaysAllowBrowser":       False,
        # Continue auto-approve (not needed by default, but harmless)
        "continue.enableTabAutocomplete": True,
    }, indent=2), encoding="utf-8")

    if setup_files:
        for rel_path, content in setup_files.items():
            target = ws / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            print(f"  [workspace] wrote setup file: {rel_path}")


def snapshot(ws: Path) -> set[Path]:
    """Return the set of all files currently in *ws* (recursively)."""
    result: set[Path] = set()
    for p in ws.rglob("*"):
        if p.is_file() and not any(part in _IGNORE_DIRS for part in p.parts):
            result.add(p)
    return result


def new_files(before: set[Path], ws: Path) -> list[Path]:
    """Return files that are new since *before* was taken."""
    current = {
        p for p in ws.rglob("*")
        if p.is_file() and not any(part in _IGNORE_DIRS for part in p.parts)
    }
    return list(current - before)


def new_code_files(before: set[Path], ws: Path) -> list[Path]:
    """Like new_files() but filtered to recognised code extensions."""
    return [p for p in new_files(before, ws) if p.suffix in CODE_EXTENSIONS]


def check_expected(
    created: list[Path],
    expected_spec: list[dict],
    ws: Path,
) -> list[str]:
    """
    Validate *created* against *expected_spec*.

    Each spec entry:
        {
            "path_pattern": "factorial.c",        # glob or exact filename
            "content_patterns": ["factorial", "int main"]  # all must appear
        }

    Returns a list of failure messages.  Empty list = all expectations met.
    """
    failures: list[str] = []

    for spec in expected_spec:
        pattern = spec.get("path_pattern", "*")
        content_patterns = [p.lower() for p in spec.get("content_patterns", [])]

        # Find a matching file
        matched: Optional[Path] = None
        for f in created:
            rel = str(f.relative_to(ws)).replace("\\", "/")
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(f.name, pattern):
                matched = f
                break

        if matched is None:
            failures.append(
                f"Expected file matching '{pattern}' was not created. "
                f"Created files: {[f.name for f in created]}"
            )
            continue

        if content_patterns:
            content = matched.read_text(encoding="utf-8", errors="replace").lower()
            for pat in content_patterns:
                if pat not in content:
                    failures.append(
                        f"File '{matched.name}' does not contain expected pattern "
                        f"'{pat}' (checked case-insensitively)."
                    )
    return failures


def find_cline_history(ws: Path) -> Optional[Path]:
    """
    Return the most recent Cline task conversation history JSON file, or None.
    Cline writes to <workspace>/.cline/tasks/<uuid>/api_conversation_history.json
    """
    cline_dir = ws / ".cline" / "tasks"
    if not cline_dir.exists():
        return None
    history_files = sorted(
        cline_dir.rglob("api_conversation_history.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return history_files[0] if history_files else None


def read_cline_last_response(ws: Path) -> Optional[str]:
    """
    Read the last assistant message from Cline's task history, if available.
    Returns up to 2000 characters of the response text.
    """
    hist = find_cline_history(ws)
    if hist is None:
        return None
    try:
        data = json.loads(hist.read_text(encoding="utf-8", errors="replace"))
        messages = data if isinstance(data, list) else data.get("messages", [])
        # Walk backwards to find last assistant message
        for msg in reversed(messages):
            role = msg.get("role", "")
            if role == "assistant":
                content = msg.get("content", "")
                if isinstance(content, list):
                    # Anthropic-style content blocks
                    text_parts = [
                        blk.get("text", "") for blk in content
                        if isinstance(blk, dict) and blk.get("type") == "text"
                    ]
                    content = " ".join(text_parts)
                return str(content)[:2000]
    except Exception:
        pass
    return None
