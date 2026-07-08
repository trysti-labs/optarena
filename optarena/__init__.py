"""
OptArena - a local-first testing & comparison framework for AI coding tools.

Runs the same task cases through real coding tools (Cline's actual VS Code UI,
aider's CLI, raw chat baselines, SDK agents), against any OpenAI/Ollama-compatible
backend (plain Ollama, a router/optimizer proxy, remote), records per-case metrics, and compares
scenarios side-by-side (tool vs tool, backend vs backend, model vs model).

Entry point:  optarena  (or: python -m optarena) --help
"""

__version__ = "0.1.0"
