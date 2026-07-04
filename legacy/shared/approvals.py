"""
shared/approvals.py
───────────────────
Auto-approve loop: click Approve/Save/Run buttons, detect errors, poll for
expected files.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

try:
    from playwright.sync_api import BrowserContext
except ImportError:
    pass

from .workspace import new_files, check_expected

APPROVE_TEXTS = [
    "Approve", "Save file", "Save", "Run command", "Run",
    "Apply", "Yes", "Proceed", "Execute",
]


def try_approve(ctx: BrowserContext) -> bool:
    """Click the first visible approval button found across all pages."""
    for page in ctx.pages:
        for text in APPROVE_TEXTS:
            try:
                btn = page.get_by_role("button", name=text, exact=False)
                if btn.count() > 0 and btn.first.is_visible():
                    btn.first.click()
                    print(f"  [approve] clicked '{text}'")
                    return True
            except Exception:
                pass
    return False


def detect_error(ctx: BrowserContext) -> Optional[str]:
    """Return an error string if an error banner is visible, else None."""
    error_selectors = [
        ".codicon-error + *",
        "[class*='error']",
        "text=API Error",
        "text=Error",
        "[class*='alert'][class*='error']",
    ]
    for page in ctx.pages:
        for sel in error_selectors:
            try:
                el = page.locator(sel)
                if el.count() > 0:
                    txt = el.first.inner_text(timeout=500)
                    if txt.strip() and "error" in txt.lower():
                        return txt.strip()[:400]
            except Exception:
                pass
    return None


def detect_task_done(ctx: BrowserContext) -> bool:
    """
    Heuristic: the Cline / Roo-Cline panel shows a "Task completed" message
    or the send button becomes re-enabled after the task finishes.

    We look for common completion indicators:
    - Text matching "task complete" / "completed" / "done"
    - The input textarea becoming enabled again

    This is best-effort; the file-system oracle is the primary completion signal.
    """
    completion_texts = [
        "task complete", "task completed", "completed", "done",
        "i've completed", "i have completed", "finished",
    ]
    for page in ctx.pages:
        try:
            body = page.evaluate("() => document.body.innerText || ''")
            body_lower = body.lower()
            if any(t in body_lower for t in completion_texts):
                return True
        except Exception:
            pass
    return False


def auto_approve_loop(
    ctx: BrowserContext,
    ws: Path,
    before: set[Path],
    expected_spec: list[dict],
    timeout: int,
    poll_interval: float = 2.0,
    idle_done_timeout: int = 15,
) -> tuple[bool, list[Path], Optional[str]]:
    """
    Poll until all expected files are created or *timeout* seconds elapse.

    The loop:
    1. Checks for new files matching expected_spec.
    2. If all expected files are found → return (True, new_files, None).
    3. Clicks any approval buttons that appear.
    4. Checks for error banners → return (False, [], error_text).
    5. If the task appears "done" and no new files have appeared for
       *idle_done_timeout* seconds → return (False, new_files_so_far, "idle timeout").

    Returns (passed, new_files, error_message).
    """
    deadline         = time.monotonic() + timeout
    last_new_file_t  = time.monotonic()
    seen_files: list[Path] = []

    while time.monotonic() < deadline:
        created = new_files(before, ws)
        if created:
            last_new_file_t = time.monotonic()
        seen_files = created

        failures = check_expected(created, expected_spec, ws)
        if not failures:
            return True, created, None

        try_approve(ctx)

        err = detect_error(ctx)
        if err:
            return False, created, f"Extension error: {err}"

        # If task appears done but files haven't appeared for idle_done_timeout
        idle = time.monotonic() - last_new_file_t
        if detect_task_done(ctx) and idle > idle_done_timeout:
            return False, created, (
                f"Task appears done but expected files not found after {idle:.0f}s idle. "
                f"Created so far: {[f.name for f in created]}"
            )

        time.sleep(poll_interval)

    remaining = time.monotonic() - deadline + timeout
    return False, seen_files, (
        f"Timed out after {timeout}s - expected files not found. "
        f"Created so far: {[f.name for f in seen_files]}"
    )
