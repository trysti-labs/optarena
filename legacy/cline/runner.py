"""
cline/runner.py
───────────────
Cline-specific automation:
  - configure()   - write Cline API settings to VS Code globalState
  - open_panel()  - click activity-bar icon or use command palette
  - inject_prompt() - paste prompt into Cline textarea and submit
  - wait_for_done() - poll DOM for Cline-specific completion indicators

Extension ID: saoudrizwan.claude-dev
"""

from __future__ import annotations

import time
from typing import Optional

try:
    from playwright.sync_api import BrowserContext, Page
except ImportError:
    pass

from ..shared.vscode_utils import (
    write_globalstate,
    run_vscode_command,
    inject_text_via_clipboard,
)

CLINE_EXT_ID = "saoudrizwan.claude-dev"

# Activity-bar selectors to try in order (VS Code version differences)
_ACTIVITY_BAR_SELECTORS = [
    '[aria-label="Cline"]',
    '[aria-label*="Cline"]',
    '.activity-bar [title*="Cline"]',
    '[id*="cline"] .activity-bar-item',
]


def configure(api_mode: str, selfopt_url: str) -> list[str]:
    """
    Write Cline's API configuration into VS Code globalState SQLite.
    VS Code must NOT be running when this is called.

    api_mode: "ollama" or "openai"
    selfopt_url: base URL for SelfOpt, e.g. "http://localhost:8001"
    """
    if api_mode == "ollama":
        settings = {
            "apiProvider":   "ollama",
            "ollamaBaseUrl": selfopt_url,
            "ollamaModelId": "selfopt",
        }
    else:  # openai
        settings = {
            "apiProvider":   "openai",
            "openAiBaseUrl": f"{selfopt_url}/v1",
            "openAiApiKey":  "selfopt",
            "openAiModelId": "selfopt",
        }

    warnings = write_globalstate(CLINE_EXT_ID, settings)
    print(f"  [cline] configured API={api_mode} → {selfopt_url}")
    return warnings


def open_panel(page: Page, ctx: BrowserContext) -> bool:
    """
    Click the Cline icon in the VS Code activity bar.
    Falls back to the command palette if the icon isn't found via aria-label.

    Returns True if the panel was opened (best-effort).
    """
    # Try activity-bar aria selectors first
    for sel in _ACTIVITY_BAR_SELECTORS:
        try:
            btn = page.locator(sel)
            if btn.count() > 0:
                btn.first.click()
                print(f"  [cline] opened panel via {sel!r}")
                time.sleep(1.5)
                return True
        except Exception:
            pass

    # Fallback: command palette
    print("  [cline] activity-bar icon not found - trying command palette")
    try:
        run_vscode_command(page, "Cline: Open In New Tab")
        time.sleep(1.5)
        return True
    except Exception:
        pass

    try:
        run_vscode_command(page, "cline.openInNewTab")
        time.sleep(1.5)
        return True
    except Exception:
        pass

    print("  [cline] WARN: could not open Cline panel programmatically")
    return False


def inject_prompt(page: Page, prompt_text: str) -> tuple[bool, str]:
    """
    Wait for Cline's webview to be ready, then paste *prompt_text* and submit
    with Ctrl+Enter.  Returns (success, info_message).
    """
    # Give the webview time to render fully
    time.sleep(2)
    return inject_text_via_clipboard(page, prompt_text)


def wait_for_done(ctx: BrowserContext, timeout_hint: int = 120) -> tuple[str, str]:
    """
    Poll for Cline-specific task completion or error indicators.

    Cline shows a spinner / "thinking" state while the model is responding.
    When done, the spinner disappears and the textarea becomes active again.
    We detect completion via DOM text patterns and spinner absence.

    Returns (status, detail) where status is "done" | "error" | "timeout".

    NOTE: This is supplementary to the file-system oracle in the approve loop.
    The approve loop is the primary completion signal; this provides earlier
    signalling for non-file-creating responses.
    """
    thinking_texts  = ["thinking", "cline is", "analyzing", "reading"]
    done_texts      = [
        "task complete", "completed", "task completed",
        "i've completed", "i have completed", "the task",
    ]
    error_texts     = ["api error", "error:", "failed to", "cannot", "unable to"]

    deadline = time.monotonic() + timeout_hint

    while time.monotonic() < deadline:
        for page in ctx.pages:
            try:
                body = page.evaluate("() => document.body.innerText || ''").lower()

                if any(t in body for t in error_texts):
                    return "error", body[:200]

                if any(t in body for t in done_texts):
                    return "done", ""

                # If no "thinking" indicator is present and the page has content,
                # assume done (extension rendered without a completion banner)
                has_thinking = any(t in body for t in thinking_texts)
                if not has_thinking and len(body) > 100:
                    return "done", ""

            except Exception:
                pass

        time.sleep(1.5)

    return "timeout", f"No completion signal after {timeout_hint}s"
