"""
optarena/drivers - one driver per supported tool.

A driver knows how to run one case through its tool and report a CaseResult.
Register new tools here; everything else (runner, metrics, compare, dashboard)
is driver-agnostic.

Registry metadata per driver:
- kind:    ui | cli | sdk | baseline
- backend: scenario (points at the scenario backend; backend-vs-backend
           comparisons are valid) | fixed (uses its own account/provider;
           only tool-vs-tool comparisons are valid)
- status:  stable | experimental | optional
"""

from __future__ import annotations

from .base import Driver, CaseResult

# name -> {kind, backend, status, summary}
DRIVERS: dict[str, dict] = {
    "openai-chat":  {"kind": "baseline", "backend": "scenario", "status": "stable",
                     "summary": "raw model via /v1/chat/completions (no agent)"},
    "ollama-chat":  {"kind": "baseline", "backend": "scenario", "status": "stable",
                     "summary": "raw model via Ollama-native /api/chat"},
    "aider":        {"kind": "cli",      "backend": "scenario", "status": "stable",
                     "summary": "aider CLI, headless"},
    "cline-ui":     {"kind": "ui",       "backend": "scenario", "status": "stable",
                     "summary": "real Cline extension in VS Code (wdio harness)"},
    "roo-ui":       {"kind": "ui",       "backend": "scenario", "status": "experimental",
                     "summary": "Roo Code via the wdio harness"},
    "continue-ui":  {"kind": "ui",       "backend": "scenario", "status": "experimental",
                     "summary": "Continue via the wdio harness"},
    "kilo-ui":      {"kind": "ui",       "backend": "scenario", "status": "experimental",
                     "summary": "Kilo Code via the wdio harness (Roo family)"},
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
    "crewai":       {"kind": "sdk",      "backend": "scenario", "status": "optional",
                     "summary": "crewAI SDK agent (pip install optarena[crewai])"},
}

DRIVER_NAMES = list(DRIVERS.keys())

_UI_EXT = {"cline-ui": "cline", "cline": "cline",
           "roo-ui": "roo", "roo": "roo",
           "continue-ui": "continue", "continue": "continue",
           "kilo-ui": "kilo", "kilo": "kilo"}


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
    if key in _UI_EXT:
        from .vscode_ui import VSCodeUIDriver
        return VSCodeUIDriver(_UI_EXT[key])
    if key == "crewai":
        from .crewai_sdk import CrewAIDriver
        return CrewAIDriver()
    from .cli_agents import CLI_AGENTS
    if key in CLI_AGENTS:
        from .cli_agents import CLIAgentDriver
        return CLIAgentDriver(key)
    raise KeyError(
        f"Unknown driver '{name}'. Available: {', '.join(DRIVER_NAMES)}"
    )
