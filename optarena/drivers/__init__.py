"""
optarena/drivers - one driver per supported tool.

A driver knows how to run one case through its tool and report a CaseResult.
Register new tools here; everything else (runner, metrics, compare, dashboard)
is driver-agnostic.

Registry metadata per driver:
- kind:    cli | sdk | baseline
- backend: scenario (points at the scenario backend; backend-vs-backend
           comparisons are valid) | fixed (uses its own account/provider;
           only tool-vs-tool comparisons are valid)
- status:  stable | experimental | optional
"""

from __future__ import annotations

from .base import Driver, CaseResult

# Re-exported as the package's public surface for driver authors
# ("one file in optarena/drivers/ implementing Driver -> CaseResult").
__all__ = ["Driver", "CaseResult", "DRIVERS", "DRIVER_NAMES", "get_driver"]

# name -> {kind, backend, status, summary}
# CLI, raw API, and in-process agent-framework/SDK drivers.
DRIVERS: dict[str, dict] = {
    "openai-chat":  {"kind": "baseline", "backend": "scenario", "status": "stable",
                     "summary": "raw model via /v1/chat/completions (no agent)"},
    "ollama-chat":  {"kind": "baseline", "backend": "scenario", "status": "stable",
                     "summary": "raw model via Ollama-native /api/chat"},
    "aider":        {"kind": "cli",      "backend": "scenario", "status": "stable",
                     "summary": "aider CLI, headless"},
    "claude-code":  {"kind": "cli",      "backend": "fixed",    "status": "experimental",
                     "summary": "Claude Code headless (claude -p)"},
    "codex":        {"kind": "cli",      "backend": "fixed",    "status": "experimental",
                     "summary": "Codex CLI (codex exec --full-auto)"},
    "opencode":     {"kind": "cli",      "backend": "scenario", "status": "experimental",
                     "summary": "OpenCode (opencode run)"},
    "goose":        {"kind": "cli",      "backend": "scenario", "status": "experimental",
                     "summary": "Goose (goose run -t)"},
    "qwen-code":    {"kind": "cli",      "backend": "scenario", "status": "experimental",
                     "summary": "Qwen Code (qwen -p)"},
    "crewai":            {"kind": "sdk", "backend": "scenario", "status": "optional",
                          "summary": "crewAI SDK agent (pip install optarena[crewai])"},
    "openai-agents":     {"kind": "sdk", "backend": "scenario", "status": "optional",
                          "summary": "OpenAI Agents SDK (pip install optarena[openai-agents])"},
    "smolagents":        {"kind": "sdk", "backend": "scenario", "status": "optional",
                          "summary": "HuggingFace smolagents CodeAgent (pip install optarena[smolagents])"},
    "langgraph":         {"kind": "sdk", "backend": "scenario", "status": "optional",
                          "summary": "LangGraph prebuilt ReAct agent (pip install optarena[langgraph])"},
    "autogen":           {"kind": "sdk", "backend": "scenario", "status": "optional",
                          "summary": "AutoGen/AG2 AssistantAgent (pip install optarena[autogen])"},
    "semantic-kernel":   {"kind": "sdk", "backend": "scenario", "status": "optional",
                          "summary": "Semantic Kernel chat agent (pip install optarena[semantic-kernel])"},
}

DRIVER_NAMES = list(DRIVERS.keys())

_SDK_DRIVERS = {
    "crewai": ("crewai_sdk", "CrewAIDriver"),
    "openai-agents": ("openai_agents_sdk", "OpenAIAgentsDriver"),
    "smolagents": ("smolagents_sdk", "SmolAgentsDriver"),
    "langgraph": ("langgraph_sdk", "LangGraphDriver"),
    "autogen": ("autogen_sdk", "AutoGenDriver"),
    "semantic-kernel": ("semantic_kernel_sdk", "SemanticKernelDriver"),
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
    raise KeyError(
        f"Unknown driver '{name}'. Available: {', '.join(DRIVER_NAMES)}"
    )
