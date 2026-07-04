"""
roo_cline/runner.py
────────────────────
Roo-Cline automation - same flow as cline/runner.py but with:
  - Extension ID: RooVeterinaryInc.roo-cline
  - Activity-bar aria labels: *Roo*
  - globalState prefix: rooveterinaryinc.roo-cline
"""

from __future__ import annotations

import time

try:
    from playwright.sync_api import BrowserContext, Page
except ImportError:
    pass

from ..shared.vscode_utils import (
    write_globalstate,
    run_vscode_command,
    inject_text_via_clipboard,
)

ROO_EXT_ID = "RooVeterinaryInc.roo-cline"

_ACTIVITY_BAR_SELECTORS = [
    '[aria-label="Roo Cline"]',
    '[aria-label*="Roo"]',
    '.activity-bar [title*="Roo"]',
]


def configure(api_mode: str, selfopt_url: str) -> list[str]:
    """Write Roo-Cline API config to globalState. VS Code must NOT be running."""
    if api_mode == "ollama":
        settings = {
            "apiProvider":   "ollama",
            "ollamaBaseUrl": selfopt_url,
            "ollamaModelId": "selfopt",
        }
    else:
        settings = {
            "apiProvider":   "openai",
            "openAiBaseUrl": f"{selfopt_url}/v1",
            "openAiApiKey":  "selfopt",
            "openAiModelId": "selfopt",
        }
    warnings = write_globalstate(ROO_EXT_ID, settings)
    print(f"  [roo-cline] configured API={api_mode} → {selfopt_url}")
    return warnings


def open_panel(page: Page, ctx: BrowserContext) -> bool:
    for sel in _ACTIVITY_BAR_SELECTORS:
        try:
            btn = page.locator(sel)
            if btn.count() > 0:
                btn.first.click()
                print(f"  [roo-cline] opened panel via {sel!r}")
                time.sleep(1.5)
                return True
        except Exception:
            pass

    print("  [roo-cline] activity-bar icon not found - trying command palette")
    for cmd in ["Roo Cline: Open In New Tab", "roo-cline.openInNewTab"]:
        try:
            run_vscode_command(page, cmd)
            time.sleep(1.5)
            return True
        except Exception:
            pass

    print("  [roo-cline] WARN: could not open Roo-Cline panel programmatically")
    return False


def inject_prompt(page: Page, prompt_text: str) -> tuple[bool, str]:
    time.sleep(2)
    return inject_text_via_clipboard(page, prompt_text)


def wait_for_done(ctx: BrowserContext, timeout_hint: int = 120) -> tuple[str, str]:
    """Same heuristics as cline/runner.py wait_for_done."""
    thinking_texts = ["thinking", "analyzing", "reading"]
    done_texts     = ["task complete", "completed", "i've completed", "finished"]
    error_texts    = ["api error", "error:", "failed to", "cannot"]

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
