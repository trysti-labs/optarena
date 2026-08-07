"""Case-file loading, filtering, and Dockerfile path resolution."""

from __future__ import annotations

import json
from pathlib import Path

from ..schema import validate_case, validate_unique_case_names
from ._constants import CASES_DIR, DOCKERFILE_DIR
from ._tag_expr import TagExpressionError, compile_tag_expression


def dockerfile_for(lang: str) -> Path:
    """Path to the Dockerfile for a `DOCKER_IMAGES` key. "base" lives directly
    under docker/ (the original combined image); every other track gets its
    own docker/<lang>/ subdirectory."""
    return DOCKERFILE_DIR / "Dockerfile" if lang == "base" else DOCKERFILE_DIR / lang / "Dockerfile"


def load_cases(names: list[str] | None = None, cases_dir: "Path | str | None" = None) -> list[dict]:
    """Load all (or the named) cases from *cases_dir*, sorted by filename.

    Every case is structurally validated (schema.validate_case) as it's
    loaded - a malformed/fuzzed case file fails fast here, with a file+key
    error, rather than surfacing later as an unclear KeyError/TypeError deep
    inside a driver (after a real backend call may already have run).
    """
    directory = Path(cases_dir) if cases_dir else CASES_DIR
    cases = []
    for p in sorted(directory.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        validate_case(data, source=str(p))
        cases.append(data)
    validate_unique_case_names(cases, source=str(directory))
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


def filter_cases(cases: list[dict], *, language: str | None = None, framework: str | None = None,
                  tool_service: str | None = None, tags: str | None = None,
                  like: str | None = None) -> list[dict]:
    """Narrow a loaded case list. Every filter that's given must match
    (AND'd together); a filter that's `None` is skipped entirely.

    - `language`/`framework`: exact match against those case fields
      (filesystem-oracle-domain cases only).
    - `tool_service`: comma-separated `tool_service` values - a case
      matches if its `tool_service` is any one of them (OR within this
      one filter, e.g. `tool_service="build_tools,observability"`;
      tool-use-domain cases only). See `optarena cases groups` for the
      real values in the built-in catalogue.
    - `tags`: a pytest `-m`-style boolean expression over the case's
      `tags` array (see `_tag_expr.py`), e.g. `"tool-use and
      observability"` or `"not slow"`. Raises `TagExpressionError` on a
      malformed expression.
    - `like`: case-insensitive substring match against the case name -
      for ad hoc selection ("everything starting with tool_bt_") without
      knowing exact names.
    """
    out = cases
    if language is not None:
        out = [c for c in out if c.get("language") == language]
    if framework is not None:
        out = [c for c in out if c.get("framework") == framework]
    if tool_service is not None:
        wanted = {s.strip() for s in tool_service.split(",") if s.strip()}
        out = [c for c in out if c.get("tool_service") in wanted]
    if tags is not None:
        predicate = compile_tag_expression(tags)
        out = [c for c in out if predicate({t.lower() for t in c.get("tags") or []})]
    if like is not None:
        needle = like.lower()
        out = [c for c in out if needle in c["name"].lower()]
    return out
