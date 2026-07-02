"""
cline/test_cases.py
───────────────────
Catalogue of Cline test cases.

Each entry matches the prompt JSON schema:
    {
        "name":           str,
        "description":    str,
        "prompts":        list[str],
        "setup_files":    dict[str, str],
        "expected_files": list[{"path_pattern": str, "content_patterns": list[str]}],
        "timeout":        int,
    }

The catalogue is loaded from the prompts/ directory at runtime; this module
simply lists the prompt filenames for discovery and provides a helper to load them.
"""

from __future__ import annotations

import json
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_case(name: str) -> dict:
    path = PROMPTS_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def all_cases() -> list[dict]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(PROMPTS_DIR.glob("*.json"))
    ]
