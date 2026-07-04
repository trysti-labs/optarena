"""
continue_ext/runner.py
───────────────────────
Continue extension automation.

Key differences vs Cline/Roo-Cline:
- Config lives in ~/.continue/config.json (plain JSON, not SQLite globalState).
- Continue supports both a sidebar panel and a full editor tab.
- The activity-bar icon aria-label is "Continue".
- The slash-command / model field layout differs from Cline.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

try:
    from playwright.sync_api import BrowserContext, Page
except ImportError:
    pass

from ..shared.vscode_utils import (
    run_vscode_command,
    inject_text_via_clipboard,
)

CONTINUE_EXT_ID = "continue.continue"

# ~/.continue/config.json path
def _continue_config_path() -> Path:
    import os
    home = Path(os.path.expanduser("~"))
    return home / ".continue" / "config.json"

_ACTIVITY_BAR_SELECTORS = [
    '[aria-label="Continue"]',
    '[aria-label*="Continue"]',
    '.activity-bar [title*="Continue"]',
]

_DEFAULT_CONTINUE_CONFIG: dict = {
    "models": [],
    "tabAutocompleteModel": None,
    "embeddingsProvider": {"provider": "transformers.js"},
    "slashCommands": [],
    "customCommands": [],
}


def configure(api_mode: str, selfopt_url: str) -> list[str]:
    """
    Add (or update) a SelfOpt model entry in ~/.continue/config.json.
    VS Code can be running; Continue hot-reloads the config file.
    """
    config_path = _continue_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing config or start from default
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            config = dict(_DEFAULT_CONTINUE_CONFIG)
    else:
        config = dict(_DEFAULT_CONTINUE_CONFIG)

    if "models" not in config or not isinstance(config["models"], list):
        config["models"] = []

    # Build the SelfOpt model entry
    if api_mode == "ollama":
        selfopt_model = {
            "title":    "SelfOpt",
            "provider": "ollama",
            "model":    "selfopt",
            "apiBase":  selfopt_url,
        }
    else:  # openai
        selfopt_model = {
            "title":    "SelfOpt",
            "provider": "openai",
            "model":    "selfopt",
            "apiKey":   "selfopt",
            "apiBase":  f"{selfopt_url}/v1",
        }

    # Replace any existing SelfOpt entry
    config["models"] = [
        m for m in config["models"]
        if m.get("title", "").lower() != "selfopt"
    ]
    config["models"].insert(0, selfopt_model)

    config_path.write_text(
        json.dumps(config, indent=2),
        encoding="utf-8",
    )
    print(f"  [continue] wrote config to {config_path}")
    return []


def open_panel(page: Page, ctx: BrowserContext) -> bool:
    for sel in _ACTIVITY_BAR_SELECTORS:
        try:
            btn = page.locator(sel)
            if btn.count() > 0:
                btn.first.click()
                print(f"  [continue] opened panel via {sel!r}")
                time.sleep(1.5)
                return True
        except Exception:
            pass

    print("  [continue] activity-bar icon not found - trying command palette")
    for cmd in ["Continue: Open GUI", "continue.openGUI", "Continue: Focus on Continue View"]:
        try:
            run_vscode_command(page, cmd)
            time.sleep(1.5)
            return True
        except Exception:
            pass

    print("  [continue] WARN: could not open Continue panel programmatically")
    return False


def inject_prompt(page: Page, prompt_text: str) -> tuple[bool, str]:
    time.sleep(3)  # Continue's webview takes slightly longer
    return inject_text_via_clipboard(page, prompt_text)


def wait_for_done(ctx: BrowserContext, timeout_hint: int = 120) -> tuple[str, str]:
    """Poll for Continue-specific completion indicators."""
    thinking_texts = ["thinking", "generating", "loading"]
    done_texts     = ["done", "complete", "finished"]
    error_texts    = ["error", "failed", "cannot connect"]

    deadline = time.monotonic() + timeout_hint
    while time.monotonic() < deadline:
        for page in ctx.pages:
            try:
                body = page.evaluate("() => document.body.innerText || ''").lower()
                if any(t in body for t in error_texts):
                    return "error", body[:200]
                if any(t in body for t in done_texts):
                    return "done", ""
                has_thinking = any(t in body for t in thinking_texts)
                if not has_thinking and len(body) > 100:
                    return "done", ""
            except Exception:
                pass
        time.sleep(1.5)

    return "timeout", f"No completion signal after {timeout_hint}s"
