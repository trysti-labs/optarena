"""continue_ext/test_cases.py — Continue test catalogue (reuses cline/prompts)."""
from __future__ import annotations

import json
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "cline" / "prompts"


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
