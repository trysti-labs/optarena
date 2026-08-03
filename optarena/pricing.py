"""
optarena/pricing.py
────────────────
Token -> USD cost estimation, so runs can report a `cost` metric (leaderboard
column, regression delta) and not just raw token counts.

Prices are `$ per 1,000,000 tokens` as `(prompt, completion)`, substring-matched
against the model id. Local inference (Ollama / LM Studio / any localhost
backend) is `0.0` - genuinely free, confirmed by the backend URL. A remote
model this table has no price for is `None` - unknown, not free (P2-05: an
unmatched remote model used to also report `0.0`, which read as "this run
cost nothing" when the honest answer was "we don't know what this cost").

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
from urllib.parse import urlparse

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


def _pricing_path() -> str:
    return os.environ.get("OPTARENA_PRICING") or str(Path.home() / ".optarena" / "pricing.json")


@lru_cache(maxsize=4)
def _table_for(path: str) -> dict[str, tuple[float, float]]:
    table = dict(_DEFAULT_PRICES)
    try:
        if Path(path).exists():
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            for k, v in data.items():
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    table[str(k)] = (float(v[0]), float(v[1]))
    except (OSError, ValueError):
        pass  # a broken override file should never break a run
    return table


def _table() -> dict[str, tuple[float, float]]:
    """A-21: keyed on the RESOLVED override path rather than cached once for
    the whole process, so changing OPTARENA_PRICING (a matrix run pricing two
    backends differently, or a test) actually takes effect. The file's
    contents are still read at most once per path - this is not a per-call
    disk hit."""
    return _table_for(_pricing_path())


def clear_pricing_cache() -> None:
    """Drop the parsed override table(s) - for tests and for a long-lived
    process that edits ~/.optarena/pricing.json in place."""
    _table_for.cache_clear()


def is_local_backend(base_url: str | None) -> bool:
    """True for localhost-style backends, whose inference we treat as free.

    H-07: this used to be plain substring containment (`h in base_url`), so
    `http://localhost.attacker.example` matched "localhost" and was wrongly
    treated as free/local. Parse the actual hostname and compare it exactly.
    """
    if not base_url:
        return False
    hostname = urlparse(base_url).hostname
    if hostname is None:
        # No scheme (e.g. "localhost:11434") - urlparse can't find a netloc
        # to pull a hostname from; reparse as if it were one.
        hostname = urlparse(f"//{base_url}").hostname
    return (hostname or "").lower() in _LOCAL_HOSTS


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
) -> "float | None":
    """
    Estimate USD cost for one run's token usage.

    P2-05: `0.0` and "unknown" are not the same claim, and returning `0.0`
    for both let a run against an unpriced remote model silently report as
    free. Only a confirmed-local backend returns `0.0` here; a remote model
    this table has no price for returns `None` so callers (metrics.aggregate,
    the dashboard) can render "N/A" instead of a fabricated "$0.00" and can
    tell a genuinely free run apart from an incompletely-priced one.
    """
    if base_url is not None and is_local_backend(base_url):
        return 0.0
    price = price_for(model)
    if price is None:
        return None
    p, c = price
    return round((prompt_tokens / 1_000_000) * p + (completion_tokens / 1_000_000) * c, 4)
