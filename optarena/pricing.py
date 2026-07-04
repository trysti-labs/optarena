"""
optarena/pricing.py
────────────────
Token -> USD cost estimation, so runs can report a `cost` metric (leaderboard
column, regression delta) and not just raw token counts.

Prices are `$ per 1,000,000 tokens` as `(prompt, completion)`, substring-matched
against the model id. Local inference (Ollama / LM Studio / any localhost
backend) and unmatched models cost `0.0` - a run is only billed when it hits a
paid remote endpoint.

The built-in table is a convenience, not authoritative (providers change
prices). Override or extend it with a JSON file of the same shape, pointed at
by ``OPTARENA_PRICING`` or placed at ``~/.optarena/pricing.json``:

    { "my-model": [2.0, 8.0] }
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

# $ per 1M tokens: (prompt, completion). Keys are matched as substrings of the
# lowercased model id, longest key first, so "claude-opus-4.8" hits "claude-opus".
_DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "gpt-5.5": (5.0, 15.0),
    "gpt-5": (5.0, 15.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4o": (2.5, 10.0),
    "o3": (2.0, 8.0),
    "claude-opus": (15.0, 75.0),
    "claude-sonnet": (3.0, 15.0),
    "claude-haiku": (0.8, 4.0),
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2": (0.3, 2.5),
    "deepseek": (0.27, 1.1),
}

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")


@lru_cache(maxsize=1)
def _table() -> dict[str, tuple[float, float]]:
    table = dict(_DEFAULT_PRICES)
    path = os.environ.get("OPTARENA_PRICING") or str(Path.home() / ".optarena" / "pricing.json")
    try:
        if Path(path).exists():
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            for k, v in data.items():
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    table[str(k)] = (float(v[0]), float(v[1]))
    except (OSError, ValueError):
        pass  # a broken override file should never break a run
    return table


def is_local_backend(base_url: str | None) -> bool:
    """True for localhost-style backends, whose inference we treat as free."""
    return any(h in (base_url or "") for h in _LOCAL_HOSTS)


def price_for(model: str | None) -> tuple[float, float] | None:
    """Return `(prompt_per_1m, completion_per_1m)` for *model*, or None."""
    model_l = (model or "").lower()
    if not model_l:
        return None
    # longest key first so specific ids win over generic prefixes
    for key in sorted(_table(), key=len, reverse=True):
        if key.lower() in model_l:
            return _table()[key]
    return None


def estimate_cost(
    model: str | None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    base_url: str | None = None,
) -> float:
    """
    Estimate USD cost for one run's token usage. Returns 0.0 for local
    backends and for models with no known price (so "free" is the honest
    default, never a fabricated number).
    """
    if base_url is not None and is_local_backend(base_url):
        return 0.0
    price = price_for(model)
    if price is None:
        return 0.0
    p, c = price
    return round((prompt_tokens / 1_000_000) * p + (completion_tokens / 1_000_000) * c, 4)
