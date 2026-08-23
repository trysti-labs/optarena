"""
optarena/drivers - one driver per supported tool.

A driver knows how to run one case through its tool and report a CaseResult.
Register new tools here; everything else (runner, metrics, compare, dashboard)
is driver-agnostic.

Registry metadata per driver:
- kind:        cli | sdk | baseline
- backend:     scenario (points at the scenario backend; backend-vs-backend
               comparisons are valid) | fixed (uses its own account/provider;
               only tool-vs-tool comparisons are valid)
- status:      stable | experimental | optional - doubles as the support
               tier (P2-06): `stable` is exercised by this project's own
               regular use, `experimental` and `optional` are best-effort -
               a failure there is a driver-specific integration issue, not
               treated as an optarena regression the same way `stable`
               would be.
- file_tools:  A-40: does this driver's tool actually edit files in the
               workspace (True - every `cli` driver: aider, Claude Code,
               Codex, ...), or does it only produce ONE block of text that
               the DRIVER itself writes to a single flattened path with no
               directory structure (False - every `baseline`/`sdk` driver:
               openai-chat, ollama-chat, and every SDK-agent driver, all of
               which share `openai_chat.concrete_target` for this). Used by
               `cli.cmd_run` to warn, before spending any compute, when a
               selected case cannot possibly be satisfied by a
               file_tools=False driver (see `cases.baseline_incompatible`) -
               found by tracing an unexplained 15% jvm pass rate to exactly
               this gap during a gemma4:12b corpus calibration run.
- owner:       P2-06: who to route a driver-specific bug/regression to. One
               name today (a solo-maintained project) - recorded per-driver
               anyway so this doesn't need inventing later if that changes,
               and so "who owns this" has one answer instead of "ask
               whoever's around."
- tested_with: P2-06: the last version this driver was confirmed working
               against, via a real invocation (not mocked) - `doctor`
               compares this to the actually-installed version
               (`get_driver_version`) and flags a mismatch, so "this driver
               hasn't been re-verified since it moved 3 versions" is
               visible instead of silently assumed fine forever.
"""

from __future__ import annotations

from .base import Driver, CaseResult

# Re-exported as the package's public surface for driver authors
# ("one file in optarena/drivers/ implementing Driver -> CaseResult").
__all__ = ["Driver", "CaseResult", "DRIVERS", "DRIVER_NAMES", "get_driver", "get_driver_version"]

# name -> {kind, backend, status, summary, file_tools, owner, tested_with}
# CLI, raw API, and in-process agent-framework/SDK drivers.
#
# P2-06: `tested_with` values below are real, not placeholders - each is the
# version `optarena.drivers.get_driver_version` actually reported for that
# tool/package, installed and probed live (a CLI `--version` invocation or
# `importlib.metadata.version`), during this same pass. Not "assumed
# current" - checked.
_OWNER = "arun@trysti.com"  # solo-maintained today; see SECURITY.md's contact

DRIVERS: dict[str, dict] = {
    "openai-chat":  {"kind": "baseline", "backend": "scenario", "status": "stable", "file_tools": False,
                     "summary": "raw model via /v1/chat/completions (no agent)",
                     "owner": _OWNER, "tested_with": None},
    "ollama-chat":  {"kind": "baseline", "backend": "scenario", "status": "stable", "file_tools": False,
                     "summary": "raw model via Ollama-native /api/chat",
                     "owner": _OWNER, "tested_with": None},
    "aider":        {"kind": "cli",      "backend": "scenario", "status": "stable", "file_tools": True,
                     "summary": "aider CLI, headless",
                     "owner": _OWNER, "tested_with": "aider.EXE 0.86.2"},
    "claude-code":  {"kind": "cli",      "backend": "fixed",    "status": "experimental", "file_tools": True,
                     "summary": "Claude Code headless (claude -p)",
                     "owner": _OWNER, "tested_with": "2.1.214 (Claude Code)"},
    "codex":        {"kind": "cli",      "backend": "fixed",    "status": "experimental", "file_tools": True,
                     "summary": "Codex CLI (codex exec --full-auto)",
                     "owner": _OWNER, "tested_with": "codex-cli 0.145.0"},
    "opencode":     {"kind": "cli",      "backend": "scenario", "status": "experimental", "file_tools": True,
                     "summary": "OpenCode (opencode run)",
                     "owner": _OWNER, "tested_with": "1.18.5"},
    "goose":        {"kind": "cli",      "backend": "scenario", "status": "experimental", "file_tools": True,
                     "summary": "Goose (goose run -t)",
                     "owner": _OWNER, "tested_with": "1.41.0"},
    "qwen-code":    {"kind": "cli",      "backend": "scenario", "status": "experimental", "file_tools": True,
                     "summary": "Qwen Code (qwen -p)",
                     "owner": _OWNER, "tested_with": "0.21.0"},
    "gemini-cli":   {"kind": "cli",      "backend": "fixed",    "status": "experimental", "file_tools": True,
                     "summary": "Gemini CLI headless (gemini -p)",
                     "owner": _OWNER, "tested_with": None},
    "crewai":            {"kind": "sdk", "backend": "scenario", "status": "optional", "file_tools": False,
                          "summary": "crewAI SDK agent (pip install optarena[crewai])",
                          "owner": _OWNER, "tested_with": "1.14.3"},
    "openai-agents":     {"kind": "sdk", "backend": "scenario", "status": "optional", "file_tools": False,
                          "summary": "OpenAI Agents SDK (pip install optarena[openai-agents])",
                          "owner": _OWNER, "tested_with": "0.18.3"},
    "smolagents":        {"kind": "sdk", "backend": "scenario", "status": "optional", "file_tools": False,
                          "summary": "HuggingFace smolagents CodeAgent (pip install optarena[smolagents])",
                          "owner": _OWNER, "tested_with": "1.24.0"},
    "langgraph":         {"kind": "sdk", "backend": "scenario", "status": "optional", "file_tools": False,
                          "summary": "LangGraph prebuilt ReAct agent (pip install optarena[langgraph])",
                          "owner": _OWNER, "tested_with": "1.2.9"},
    "autogen":           {"kind": "sdk", "backend": "scenario", "status": "optional", "file_tools": False,
                          "summary": "AutoGen/AG2 AssistantAgent (pip install optarena[autogen])",
                          "owner": _OWNER, "tested_with": "0.7.5"},
    "semantic-kernel":   {"kind": "sdk", "backend": "scenario", "status": "optional", "file_tools": False,
                          "summary": "Semantic Kernel chat agent (pip install optarena[semantic-kernel])",
                          "owner": _OWNER, "tested_with": "1.44.0"},
}

def _load_entry_point_drivers() -> dict[str, dict]:
    """
    P3-02: third-party driver discovery via the `optarena.drivers`
    entry-point group - lets a separately-installed package add a driver
    without a code change here. A package registers one with:

        [project.entry-points."optarena.drivers"]
        my-tool = "my_package.driver:MyToolDriver"

    where `MyToolDriver` is a `Driver` subclass, importable with no
    constructor arguments (the same convention every built-in driver's own
    `get_driver()` branch already follows). The entry point is resolved
    lazily, only when that driver is actually requested (`get_driver`) -
    this function itself only reads installed-package METADATA (cheap, no
    imports of the third-party code), so a broken/uninstallable third-party
    driver package doesn't prevent `optarena drivers list`/`--driver
    choices` from working, and importing it doesn't cost anything for a run
    that never uses it.
    """
    import importlib.metadata as _md
    out: dict[str, dict] = {}
    try:
        eps = _md.entry_points(group="optarena.drivers")
    except Exception:  # noqa: BLE001 - never let a broken environment break driver discovery
        return out
    for ep in eps:
        out[ep.name] = {
            "kind": "external", "backend": "scenario", "status": "external",
            "file_tools": True, "summary": f"third-party driver ({ep.value})",
            "owner": None, "tested_with": None, "_entry_point": ep,
        }
    return out


DRIVERS.update(_load_entry_point_drivers())
DRIVER_NAMES = list(DRIVERS.keys())

_SDK_DRIVERS = {
    "crewai": ("crewai_sdk", "CrewAIDriver"),
    "openai-agents": ("openai_agents_sdk", "OpenAIAgentsDriver"),
    "smolagents": ("smolagents_sdk", "SmolAgentsDriver"),
    "langgraph": ("langgraph_sdk", "LangGraphDriver"),
    "autogen": ("autogen_sdk", "AutoGenDriver"),
    "semantic-kernel": ("semantic_kernel_sdk", "SemanticKernelDriver"),
}

# P2-04: driver key -> upstream PyPI distribution name, from pyproject.toml's
# own optional-dependency extras (the actual `pip install` each one runs) -
# NOT always the same as the Python import name (`import_names` in each
# driver class): `pip install openai-agents` installs the `agents` module,
# and `importlib.metadata.version()` needs the distribution name, not the
# import name. Confirmed live: `importlib.metadata.packages_distributions()`
# doesn't reliably reverse-map `agents` back to `openai-agents` either (that
# package doesn't declare the mapping), so import_names alone silently
# under-reported this one driver's version - this explicit table is the fix.
_SDK_PIP_NAMES = {
    "crewai": "crewai",
    "openai-agents": "openai-agents",
    "smolagents": "smolagents",
    "langgraph": "langgraph",
    "autogen": "autogen-agentchat",
    "semantic-kernel": "semantic-kernel",
}


def get_driver(name: str) -> Driver:
    """Instantiate a driver by registry name (lazy imports keep deps optional)."""
    key = name.lower().replace("_", "-")
    if key in ("openai-chat", "openai"):
        from .openai_chat import OpenAIChatDriver
        return OpenAIChatDriver()
    if key in ("ollama-chat", "ollama"):
        from .openai_chat import OllamaChatDriver
        return OllamaChatDriver()
    if key == "aider":
        from .aider_cli import AiderDriver
        return AiderDriver()
    if key in _SDK_DRIVERS:
        import importlib
        module_name, class_name = _SDK_DRIVERS[key]
        module = importlib.import_module(f".{module_name}", __name__)
        return getattr(module, class_name)()
    from .cli_agents import CLI_AGENTS
    if key in CLI_AGENTS:
        from .cli_agents import CLIAgentDriver
        return CLIAgentDriver(key)
    entry_point = DRIVERS.get(key, {}).get("_entry_point")
    if entry_point is not None:
        try:
            return entry_point.load()()
        except Exception as exc:
            raise RuntimeError(f"could not load third-party driver '{key}' "
                               f"({entry_point.value}): {exc}") from exc
    raise KeyError(
        f"Unknown driver '{name}'. Available: {', '.join(DRIVER_NAMES)}"
    )


def get_driver_version(name: str) -> "str | None":
    """
    P2-04: best-effort external tool/SDK version, for `runner.build_manifest`
    - a run's manifest recorded ITS OWN version (`optarena_version`) but not
    the external CLI binary or SDK package version it actually drove, which
    is exactly the piece that changes silently underneath a comparison (an
    `aider` pip upgrade, an SDK bump) with no other record of it happening.

    Local metadata lookups and, for CLI tools, one bounded `--version`
    subprocess call - never a live network call or anything that talks to a
    model/provider. Every failure mode (binary not on PATH, package not
    installed, `--version` hangs, errors, or prints something unparseable)
    returns `None` rather than raising: this is reproducibility evidence,
    not something a run should ever fail over.
    """
    key = name.lower().replace("_", "-")
    info = DRIVERS.get(key)
    if info is None:
        return None
    try:
        if info["kind"] == "sdk":
            from importlib.metadata import PackageNotFoundError, version
            pip_name = _SDK_PIP_NAMES.get(key)
            if not pip_name:
                return None
            try:
                return version(pip_name)
            except PackageNotFoundError:
                return None
        if info["kind"] == "cli":
            import shutil
            import subprocess
            binary = None
            if key == "aider":
                from .aider_cli import find_aider
                binary = find_aider()
            else:
                from .cli_agents import CLI_AGENTS
                spec = CLI_AGENTS.get(key)
                if spec:
                    for candidate in spec["binaries"]:
                        binary = shutil.which(candidate)
                        if binary:
                            break
            if not binary:
                return None
            proc = subprocess.run([binary, "--version"], capture_output=True,
                                  text=True, timeout=10)
            first_line = next(
                (ln.strip() for ln in (proc.stdout or proc.stderr or "").splitlines() if ln.strip()),
                None)
            return first_line[:200] if first_line else None
    except Exception:  # noqa: BLE001 - manifest metadata, never worth failing a run over
        return None
    return None   # "baseline" drivers have no external tool/SDK to version
