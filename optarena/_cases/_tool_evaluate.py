"""
optarena/_cases/_tool_evaluate.py
──────────────────────────────
The tool-use oracle: same shape as ``_evaluate.evaluate_case`` (returns
``(failures, oracle_info)``, stashed into ``CaseResult.extra["oracle"]`` by
the driver) - grading a ``MockService``'s call log and final state instead of
a workspace diff.
"""

from __future__ import annotations

from ._mock_service import MockService


def _value_matches(actual, want) -> bool:
    """Does ``actual`` "contain" ``want`` - shared by both call-argument
    matching and expected_final_state matching, since both need the same
    "required subset, not exact match" semantics:

    - dict: every key in ``want`` must be present in ``actual`` with a
      matching (recursively-contained) value - extra keys in ``actual`` are
      fine. This is what makes filesystem's
      ``expected_final_state: {"files": {"a.py": "..."}}`` check ONLY
      ``a.py``'s content, ignoring every other file the mock filesystem
      happens to also have (pre-seeded or otherwise) - the same reasoning
      as git_repo's ``main_commit_count`` existing so a case never has to
      pin down the FULL state, only the part it actually cares about.
    - list: every element of ``want`` must appear somewhere in ``actual``
      (e.g. git_add's ``paths`` - a case asserting {"paths": ["README.md"]}
      shouldn't fail because the model reasonably staged
      ["README.md", "app.py"] in one call).
    - anything else: exact equality (task_tracker's title/task_id/status,
      and any scalar final_state field like commit_count).
    """
    if isinstance(want, dict) and isinstance(actual, dict):
        return all(k in actual and _value_matches(actual[k], v) for k, v in want.items())
    if isinstance(want, list) and isinstance(actual, list):
        return all(item in actual for item in want)
    return actual == want


def _matches(entry_arguments: dict, arguments_contains: dict) -> bool:
    """``arguments_contains`` is a required subset, not an exact match - a
    case asserting {"title": "Buy milk"} shouldn't fail because the model
    also (legitimately) passed an ``assignee``."""
    return all(
        k in entry_arguments and _value_matches(entry_arguments[k], v)
        for k, v in arguments_contains.items()
    )


def _any_call_matches(call_log: list[dict], tool: str, arguments_contains: dict) -> bool:
    return any(
        entry["tool"] == tool and _matches(entry["arguments"], arguments_contains)
        for entry in call_log
    )


def evaluate_tool_case(case: dict, service: MockService) -> tuple[list[str], dict]:
    """Full oracle for one tool-use case, after the driver's tool-calling
    loop has finished running against ``service``.

    - ``expected_calls``: each entry must match at least one logged call
      (by tool name, with ``arguments_contains`` as a required subset).
    - ``forbidden_calls``: no logged call may match.
    - ``expected_final_state``: each key/value must be *contained* in
      ``service.summary()`` (same subset semantics as ``arguments_contains``
      - a nested-dict value like ``files`` only needs to contain the
      sub-keys the case actually names).

    Every check is independent and all are evaluated (not short-circuited),
    same as the filesystem oracle's ``check_expected`` - a case with three
    problems reports three failures, not just the first one found.
    """
    call_log = service.call_log
    failures: list[str] = []

    for expected in case.get("expected_calls", []) or []:
        tool = expected["tool"]
        arguments_contains = expected.get("arguments_contains", {}) or {}
        if not _any_call_matches(call_log, tool, arguments_contains):
            failures.append(
                f"expected a call to {tool!r} with arguments containing "
                f"{arguments_contains!r} - none was made"
            )

    for forbidden in case.get("forbidden_calls", []) or []:
        tool = forbidden["tool"]
        arguments_contains = forbidden.get("arguments_contains", {}) or {}
        if _any_call_matches(call_log, tool, arguments_contains):
            failures.append(
                f"forbidden call to {tool!r} with arguments containing "
                f"{arguments_contains!r} was made"
            )

    final_state = service.summary()
    for key, want in (case.get("expected_final_state") or {}).items():
        got = final_state.get(key)
        if not _value_matches(got, want):
            failures.append(f"expected final_state[{key!r}] to contain {want!r}, got {got!r}")

    known_tools = set(service.TOOLS)
    unknown_calls = [e for e in call_log if e["tool"] not in known_tools]

    info = {
        "tool_service": case.get("tool_service"),
        "call_log": call_log,
        "final_state": final_state,
        "n_calls": len(call_log),
        "n_unknown_calls": len(unknown_calls),
    }
    return failures, info
