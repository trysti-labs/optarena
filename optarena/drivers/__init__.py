"""
optarena/drivers — one driver per supported tool.

A driver knows how to run one case through its tool and report a CaseResult.
Register new tools here; everything else (runner, metrics, compare, dashboard)
is driver-agnostic.

Driver status:
    openai-chat   stable        raw-model baseline (no agent), any OpenAI-compat API
    ollama-chat   stable        raw-model baseline via Ollama-native /api/chat
    aider         stable        aider CLI, headless
    cline-ui      stable        real Cline extension in VS Code (wdio harness)
    roo-ui        experimental  Roo Code via the same wdio harness (first-run wizard quirks)
    continue-ui   experimental  Continue via the same wdio harness (agent-mode selection)
    crewai        optional      crewAI SDK agent (needs `pip install crewai`)
"""

from __future__ import annotations

from .base import Driver, CaseResult


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
    if key in ("cline-ui", "cline"):
        from .vscode_ui import VSCodeUIDriver
        return VSCodeUIDriver("cline")
    if key in ("roo-ui", "roo"):
        from .vscode_ui import VSCodeUIDriver
        return VSCodeUIDriver("roo")
    if key in ("continue-ui", "continue"):
        from .vscode_ui import VSCodeUIDriver
        return VSCodeUIDriver("continue")
    if key == "crewai":
        from .crewai_sdk import CrewAIDriver
        return CrewAIDriver()
    raise KeyError(
        f"Unknown driver '{name}'. Available: openai-chat, ollama-chat, aider, "
        f"cline-ui, roo-ui, continue-ui, crewai"
    )


DRIVER_NAMES = [
    "openai-chat", "ollama-chat", "aider", "cline-ui", "roo-ui", "continue-ui", "crewai",
]
