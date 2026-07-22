"""
optarena/schema.py
───────────────
Dependency-free structural validation for scenario and case files. A
malformed or fuzzed file should fail fast - before any driver invocation or
backend call, which may already cost real money and wall-clock time - with a
clear "which file, which key, what's wrong" error instead of a confusing
KeyError/TypeError three layers deep in a driver, or (worse) an incorrect
result silently computed from garbage input.

No external dependency (no ``jsonschema`` package): this project is stdlib
only, and the validation needed here - required keys, type/enum/range
checks, unknown-key rejection - doesn't need a general JSON Schema engine.
"""

from __future__ import annotations

MAX_CHECK_COMMAND_TIMEOUT = 3600   # seconds; generous ceiling, not a real per-case budget
MAX_CASE_TIMEOUT = 3600


class SchemaError(ValueError):
    """A scenario/case file failed structural validation."""


def _err(where: str, msg: str) -> SchemaError:
    return SchemaError(f"{where}: {msg}")


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate_string_map(value, where: str) -> None:
    if not isinstance(value, dict):
        raise _err(where, "must be an object of {relative_path: content}")
    for k, v in value.items():
        if not isinstance(k, str) or not k:
            raise _err(where, f"key {k!r} must be a non-empty string (a relative path)")
        if not isinstance(v, str):
            raise _err(f"{where}.{k}", "value must be a string")


def _validate_string_list(value, where: str) -> None:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise _err(where, "must be a list of strings")


# ── Scenario files ─────────────────────────────────────────────────────────

_SCENARIO_KNOWN_KEYS = {"name", "driver", "backend", "cases", "timeout", "cases_dir"}
_BACKEND_KNOWN_KEYS = {"kind", "base_url", "model", "api_key"}


def validate_scenario(data: dict, source: str = "<scenario>") -> None:
    """Raise SchemaError on a structurally invalid scenario dict. Called
    before Scenario.from_dict, which otherwise fails much later (or not at
    all - unknown keys are silently ignored by ``dict.get``) with unclear
    errors."""
    if not isinstance(data, dict):
        raise _err(source, f"must be a JSON object, got {type(data).__name__}")
    unknown = set(data) - _SCENARIO_KNOWN_KEYS
    if unknown:
        raise _err(source, f"unknown key(s): {', '.join(sorted(unknown))}")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise _err(source, "'name' must be a non-empty string")
    if not isinstance(data.get("driver"), str) or not data["driver"].strip():
        raise _err(source, "'driver' must be a non-empty string")

    backend = data.get("backend")
    if backend is not None:
        if not isinstance(backend, dict):
            raise _err(f"{source}.backend", "must be an object")
        unknown_b = set(backend) - _BACKEND_KNOWN_KEYS
        if unknown_b:
            raise _err(f"{source}.backend", f"unknown key(s): {', '.join(sorted(unknown_b))}")
        if "kind" in backend and backend["kind"] not in ("ollama", "openai"):
            raise _err(f"{source}.backend.kind", f"must be 'ollama' or 'openai', got {backend['kind']!r}")
        for key in ("base_url", "model", "api_key"):
            if key in backend and not isinstance(backend[key], str):
                raise _err(f"{source}.backend.{key}", "must be a string")

    cases = data.get("cases")
    if cases is not None and (not isinstance(cases, list) or not all(isinstance(c, str) for c in cases)):
        raise _err(source, "'cases' must be a list of strings (or omitted/null for all)")

    timeout = data.get("timeout")
    if timeout is not None and (not _is_number(timeout) or timeout <= 0):
        raise _err(source, "'timeout' must be a positive number of seconds")

    if "cases_dir" in data and data["cases_dir"] is not None and not isinstance(data["cases_dir"], str):
        raise _err(source, "'cases_dir' must be a string")


# ── Case files ───────────────────────────────────────────────────────────

_CASE_KNOWN_KEYS = {
    "name", "description", "prompts", "setup_files", "setup_repo", "git_init",
    "expected_files", "test_setup_files", "check_command", "check_command_timeout",
    "docker_image", "timeout",
    "reference_solution", "broken_solutions",
    "language", "framework", "domain", "difficulty", "task_type", "tags",
    # Oracle style, for discovery/reporting: "unit" (default), "property"
    # (randomized property-based), "metamorphic" (relations across inputs),
    # "mutation" (test-strength). Free-form string; not enforced against an enum.
    "test_kind",
}
_EXPECTED_FILE_KNOWN_KEYS = {
    "path_pattern", "content_patterns", "not_content_patterns", "regex_patterns", "min_lines",
}
_BROKEN_SOLUTION_KNOWN_KEYS = {"name", "files"}


def validate_case(data: dict, source: str = "<case>") -> None:
    """Raise SchemaError on a structurally invalid case dict - see the schema
    documented at the top of cases.py for the full field list."""
    if not isinstance(data, dict):
        raise _err(source, f"must be a JSON object, got {type(data).__name__}")
    unknown = set(data) - _CASE_KNOWN_KEYS
    if unknown:
        raise _err(source, f"unknown key(s): {', '.join(sorted(unknown))}")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        raise _err(source, "'name' must be a non-empty string")

    if "prompts" in data:
        _validate_string_list(data["prompts"], f"{source}.prompts")

    for key in ("setup_files", "test_setup_files"):
        if key in data and data[key] is not None:
            _validate_string_map(data[key], f"{source}.{key}")

    if "setup_repo" in data and data["setup_repo"] is not None and not isinstance(data["setup_repo"], str):
        raise _err(source, "'setup_repo' must be a string")
    if "git_init" in data and not isinstance(data["git_init"], bool):
        raise _err(source, "'git_init' must be a boolean")
    if "check_command" in data and data["check_command"] is not None and not isinstance(data["check_command"], str):
        raise _err(source, "'check_command' must be a string")

    if "check_command_timeout" in data:
        t = data["check_command_timeout"]
        if not _is_number(t) or t <= 0 or t > MAX_CHECK_COMMAND_TIMEOUT:
            raise _err(source, f"'check_command_timeout' must be a number in (0, {MAX_CHECK_COMMAND_TIMEOUT}]")
    if "timeout" in data:
        t = data["timeout"]
        if not _is_number(t) or t <= 0 or t > MAX_CASE_TIMEOUT:
            raise _err(source, f"'timeout' must be a number in (0, {MAX_CASE_TIMEOUT}]")

    if "docker_image" in data and data["docker_image"] is not None and not isinstance(data["docker_image"], str):
        raise _err(source, "'docker_image' must be a string")

    if "difficulty" in data:
        d = data["difficulty"]
        if not isinstance(d, int) or isinstance(d, bool) or not (1 <= d <= 5):
            raise _err(source, "'difficulty' must be an integer 1-5")
    for key in ("language", "framework", "domain", "task_type", "test_kind"):
        if key in data and data[key] is not None and not isinstance(data[key], str):
            raise _err(source, f"'{key}' must be a string")
    if "tags" in data:
        _validate_string_list(data["tags"], f"{source}.tags")

    if "expected_files" in data:
        specs = data["expected_files"]
        if not isinstance(specs, list):
            raise _err(source, "'expected_files' must be a list")
        for i, spec in enumerate(specs):
            where = f"{source}.expected_files[{i}]"
            if not isinstance(spec, dict):
                raise _err(where, "must be an object")
            unknown_e = set(spec) - _EXPECTED_FILE_KNOWN_KEYS
            if unknown_e:
                raise _err(where, f"unknown key(s): {', '.join(sorted(unknown_e))}")
            if not isinstance(spec.get("path_pattern"), str) or not spec["path_pattern"]:
                raise _err(where, "'path_pattern' must be a non-empty string")
            for list_key in ("content_patterns", "not_content_patterns", "regex_patterns"):
                if list_key in spec:
                    _validate_string_list(spec[list_key], f"{where}.{list_key}")
            if "min_lines" in spec:
                ml = spec["min_lines"]
                if not isinstance(ml, int) or isinstance(ml, bool) or ml < 0:
                    raise _err(where, "'min_lines' must be a non-negative integer")

    if "reference_solution" in data and data["reference_solution"] is not None:
        _validate_string_map(data["reference_solution"], f"{source}.reference_solution")

    if "broken_solutions" in data:
        variants = data["broken_solutions"]
        if not isinstance(variants, list):
            raise _err(source, "'broken_solutions' must be a list")
        for i, variant in enumerate(variants):
            where = f"{source}.broken_solutions[{i}]"
            if not isinstance(variant, dict):
                raise _err(where, "must be an object")
            unknown_v = set(variant) - _BROKEN_SOLUTION_KNOWN_KEYS
            if unknown_v:
                raise _err(where, f"unknown key(s): {', '.join(sorted(unknown_v))}")
            if not isinstance(variant.get("name"), str) or not variant["name"]:
                raise _err(where, "'name' must be a non-empty string")
            _validate_string_map(variant.get("files", {}), f"{where}.files")


def validate_unique_case_names(cases: list[dict], source: str = "<cases>") -> None:
    """Duplicate case names would silently shadow each other in load_cases'
    filter-by-name lookup and in results/index bookkeeping - reject outright
    rather than let one case become unreachable without warning."""
    seen: set[str] = set()
    for c in cases:
        name = c.get("name")
        if name in seen:
            raise SchemaError(f"{source}: duplicate case name {name!r}")
        seen.add(name)
