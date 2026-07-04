#!/usr/bin/env python3
"""
Cline automation test.

Tests Cline's code-generation capability against both SelfOpt endpoints:
  - Ollama-native   : http://localhost:8001/api/chat
  - OpenAI-compat   : http://localhost:8001/v1/chat/completions

Steps per run:
  1. Configure Cline's API by writing to VS Code's extension globalState (SQLite)
  2. Kill any existing VS Code (it must be restarted with --remote-debugging-port)
  3. Launch VS Code with a fresh temp workspace + Cline auto-approve workspace settings
  4. Connect via CDP, click the Cline activity-bar icon
  5. Locate the Cline webview iframe in VS Code's DOM, click + paste the prompt
  6. Auto-click any "Approve / Save / Run" buttons that appear
  7. Wait up to CLINE_TIMEOUT seconds for code files to appear in the workspace
  8. Print pass / fail with full details; exit 0 on all pass, 1 on any failure

Usage:
    python open_cline.py                        # test both APIs sequentially
    python open_cline.py --api ollama           # Ollama only
    python open_cline.py --api openai           # OpenAI-compat only
    python open_cline.py --selfopt http://...   # custom SelfOpt URL
    python open_cline.py --timeout 180          # longer wait for slow models

Note: this script kills and restarts VS Code to attach the CDP debug port.
      Save your VS Code work before running.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Optional

# ── auto-install playwright ───────────────────────────────────────────────────

try:
    from playwright.sync_api import BrowserContext, Page, sync_playwright
except ImportError:
    print("Installing playwright...")
    subprocess.run([sys.executable, "-m", "pip", "install", "playwright", "-q"], check=True)
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    from playwright.sync_api import BrowserContext, Page, sync_playwright  # type: ignore

# ── auto-install pyautogui ────────────────────────────────────────────────────

try:
    import pyautogui  # type: ignore
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.05
except ImportError:
    print("Installing pyautogui...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pyautogui", "-q"], check=True)
    import pyautogui  # type: ignore
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.05

# ── Constants ─────────────────────────────────────────────────────────────────

CDP_URL      = "http://localhost:9222"
BASE_DIR     = Path(__file__).parent
PROMPT_FILE  = BASE_DIR / "prompt.txt"

SELFOPT_URL  = os.environ.get("SELFOPT_URL", "http://localhost:8001")
CLINE_EXT_ID = "saoudrizwan.claude-dev"
TASK_TIMEOUT = int(os.environ.get("CLINE_TIMEOUT", "120"))

CODE_EXTENSIONS = {".c", ".cpp", ".h", ".py", ".js", ".ts", ".java", ".go", ".rs", ".cs", ".rb"}

APPROVE_TEXTS = ["Approve", "Save file", "Save", "Run", "Apply", "Yes"]


# ── VS Code globalState (SQLite) ──────────────────────────────────────────────

def _vscode_state_db() -> Path:
    _roots: dict[str, Path] = {
        "win32":  Path(os.environ.get("APPDATA", "")) / "Code",
        "darwin": Path.home() / "Library" / "Application Support" / "Code",
    }
    root = _roots.get(sys.platform, Path.home() / ".config" / "Code")
    return root / "User" / "globalStorage" / "state.vscdb"


def configure_cline_api(api_mode: str, selfopt_url: str) -> list[str]:
    """
    Write Cline's API config into VS Code's extension globalState SQLite.
    VS Code must NOT be running when this is called.
    Returns a list of non-fatal warning strings.
    """
    db = _vscode_state_db()
    if not db.exists():
        return [f"globalState DB not found at {db} - configure Cline API manually."]

    prefix = f"{CLINE_EXT_ID}/"

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

    warnings: list[str] = []
    try:
        conn = sqlite3.connect(str(db), timeout=5)
        cur  = conn.cursor()
        for key, val in settings.items():
            cur.execute(
                "INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)",
                (f"{prefix}{key}", json.dumps(val)),
            )
        conn.commit()
        conn.close()
        print(f"  [config] Cline API → {api_mode}  ({selfopt_url})")
    except Exception as exc:
        warnings.append(f"globalState write failed ({exc}) - configure Cline API manually.")
    return warnings


# ── Workspace setup ───────────────────────────────────────────────────────────

def setup_workspace(ws: Path) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    vsc = ws / ".vscode"
    vsc.mkdir(exist_ok=True)
    (vsc / "settings.json").write_text(json.dumps({
        "cline.alwaysAllowWrite":   True,
        "cline.alwaysAllowRead":    True,
        "cline.alwaysAllowExecute": False,
        "cline.alwaysAllowBrowser": False,
    }, indent=2), encoding="utf-8")


def snapshot_files(ws: Path) -> set[Path]:
    return {p for p in ws.rglob("*") if p.is_file() and not p.parts[-1].startswith(".")}


def new_code_files(before: set[Path], ws: Path) -> list[Path]:
    current = {p for p in ws.rglob("*") if p.is_file()}
    return [p for p in current - before if p.suffix in CODE_EXTENSIONS]


# ── VS Code process management ────────────────────────────────────────────────

def cdp_alive() -> bool:
    try:
        urllib.request.urlopen(f"{CDP_URL}/json", timeout=2)
        return True
    except Exception:
        return False


def _vscode_running() -> bool:
    """Check if any VS Code process is running."""
    _check = {
        "win32": lambda: "Code.exe" in subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Code.exe", "/NH"],
            capture_output=True, text=True, timeout=5,
        ).stdout,
        "darwin": lambda: subprocess.run(
            ["pgrep", "-f", "Visual Studio Code"],
            capture_output=True, timeout=5,
        ).returncode == 0,
        "linux": lambda: subprocess.run(
            ["pgrep", "-f", "code"],
            capture_output=True, timeout=5,
        ).returncode == 0,
    }
    fn = _check.get(sys.platform)
    return fn() if fn else False


def _kill_vscode() -> None:
    """Kill VS Code so it can be restarted with the CDP debug port."""
    _cmds: dict[str, list[str]] = {
        "win32":  ["taskkill", "/F", "/IM", "Code.exe"],
        "darwin": ["pkill", "-f", "Visual Studio Code"],
        "linux":  ["pkill", "-f", "code"],
    }
    cmd = _cmds.get(sys.platform)
    if cmd:
        subprocess.run(cmd, capture_output=True, timeout=10)
    time.sleep(2)


def launch_vscode(workspace: Path) -> None:
    """
    Ensure VS Code is running with --remote-debugging-port=9222 pointing at workspace.

    VS Code's webview panels run as child Electron renderer processes.  Those
    processes only inherit the CDP debug port when VS Code itself was launched
    with --remote-debugging-port.  If an existing VS Code instance is already
    running (without the port), `code --new-window` opens a window inside it and
    the debug flag is silently ignored.  We must kill the existing instance first.
    """
    if cdp_alive():
        print("  VS Code CDP already active on :9222 - reusing.")
        return

    if _vscode_running():
        print("  [warn] VS Code is running WITHOUT the CDP debug port.")
        print("  Closing VS Code to restart it with --remote-debugging-port=9222 ...")
        _kill_vscode()

    print("  Launching VS Code with CDP...")
    cmd = f'code --new-window --remote-debugging-port=9222 "{workspace}"'
    subprocess.Popen(cmd, shell=True)

    print("  Waiting for CDP to start", end="", flush=True)
    for _ in range(60):
        time.sleep(1)
        print(".", end="", flush=True)
        if cdp_alive():
            print(" ready!")
            return
    print()
    raise RuntimeError("VS Code CDP did not start within 60 s.")


# ── Clipboard helpers (no extra deps beyond stdlib + PowerShell on Windows) ───

def _set_clipboard(text: str) -> None:
    """Put text on the OS clipboard (no third-party packages required)."""
    def _win(t: str) -> None:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"Set-Clipboard -Value {json.dumps(t)}"],
            capture_output=True, timeout=10,
        )
    def _mac(t: str) -> None:
        subprocess.run(["pbcopy"], input=t.encode(), check=True, timeout=5)
    def _linux(t: str) -> None:
        try:
            subprocess.run(["xclip", "-selection", "clipboard"],
                           input=t.encode(), check=True, timeout=5)
        except Exception:
            subprocess.run(["xdotool", "type", "--", t], timeout=30)

    _handlers = {"win32": _win, "darwin": _mac, "linux": _linux}
    handler = _handlers.get(sys.platform, _linux)
    handler(text)


# ── Locate Cline's webview inside VS Code's DOM ───────────────────────────────

_WEBVIEW_IFRAME_JS = """
() => {
    // VS Code renders webview panels as <iframe> elements inside the main workbench.
    // The iframe element lives in the main renderer (so Playwright can see it), even
    // though its content runs in a sandboxed sub-process.  We find the iframe and
    // return its bounding rect - pyautogui then clicks at the bottom (textarea area).
    const selectors = [
        // VS Code 1.79+: webview panels in the sidebar or editor area
        '.part.sidebar iframe',
        '.part.panel iframe',
        '.editor-container iframe',
        // generic webview ready state
        'iframe.webview.ready',
        // any sizeable iframe
        'iframe',
    ];
    for (const sel of selectors) {
        for (const el of document.querySelectorAll(sel)) {
            const r = el.getBoundingClientRect();
            if (r.width > 150 && r.height > 150) {
                return {x: r.x, y: r.y, w: r.width, h: r.height, sel: sel};
            }
        }
    }
    // Diagnostic: return counts to help debug
    const total = document.querySelectorAll('iframe').length;
    return {error: 'no-iframe', total_iframes: total};
}
"""

_WIN_POS_JS = """
() => ({
    screenX: window.screenX || window.screenLeft || 0,
    screenY: window.screenY || window.screenTop || 0,
    outerW:  window.outerWidth  || window.innerWidth  || 1920,
    outerH:  window.outerHeight || window.innerHeight || 1080,
})
"""


def _fill_textarea(main: Page, prompt: str) -> tuple[bool, str]:
    """
    Locate the Cline webview iframe in VS Code's main renderer DOM, compute its
    absolute screen position, then use pyautogui to click the textarea area and
    paste the prompt from the clipboard.

    Why not CDP into the webview directly?  VS Code's webview renderer processes
    are spawned without the --remote-debugging-port flag (they're child processes of
    the main VS Code renderer, not of the `code` launcher).  They never appear in
    /json/list and cannot be reached via WebSocket CDP from outside VS Code.

    Why not Playwright keyboard events?  Playwright's keyboard API sends events to
    the main workbench renderer.  They don't cross the process boundary into the
    Cline webview.  OS-level input (pyautogui) is the only path that reaches
    whatever window currently has OS focus.
    """
    try:
        win = main.evaluate(_WIN_POS_JS)
        rect = main.evaluate(_WEBVIEW_IFRAME_JS)
    except Exception as exc:
        return False, f"DOM eval failed: {exc}"

    if "error" in rect:
        # Fall back to bottom-left quarter of the VS Code window (sidebar area)
        print(f"  [warn] {rect} - falling back to sidebar coordinates")
        click_x = int(win["screenX"] + min(280, win["outerW"] // 4))
        click_y = int(win["screenY"] + win["outerH"] - 80)
    else:
        # Bottom-center of the webview iframe (where Cline's task textarea sits)
        click_x = int(win["screenX"] + rect["x"] + rect["w"] / 2)
        click_y = int(win["screenY"] + rect["y"] + rect["h"] - 70)
        print(f"  [webview] found via {rect['sel']!r} "
              f"rect=({int(rect['x'])},{int(rect['y'])},{int(rect['w'])}x{int(rect['h'])})")

    print(f"  [click] textarea area at screen ({click_x}, {click_y})")

    # Copy prompt to clipboard and paste - avoids typewrite encoding issues
    _set_clipboard(prompt)
    pyautogui.click(click_x, click_y)
    time.sleep(0.4)

    # Select-all then paste ensures we replace any existing text
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.4)

    # Ctrl+Enter submits the task in Cline
    pyautogui.hotkey("ctrl", "enter")
    return True, f"pasted at ({click_x}, {click_y})"


# ── Approval + error detection ────────────────────────────────────────────────

def _try_approve(ctx: BrowserContext) -> bool:
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


def _cline_error_text(ctx: BrowserContext) -> Optional[str]:
    for page in ctx.pages:
        try:
            for sel in [".codicon-error + *", "[class*='error']", "text=API Error"]:
                el = page.locator(sel)
                if el.count() > 0:
                    txt = el.first.inner_text()
                    if txt.strip():
                        return txt.strip()[:300]
        except Exception:
            pass
    return None


# ── Core test run ─────────────────────────────────────────────────────────────

class TestResult:
    def __init__(self, api_mode: str):
        self.api_mode  = api_mode
        self.passed    = False
        self.files:    list[Path] = []
        self.errors:   list[str]  = []
        self.warnings: list[str]  = []

    def fail(self, msg: str) -> "TestResult":
        self.errors.append(msg)
        return self

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines  = [f"[{status}] API={self.api_mode}"]
        if self.files:
            lines.append(f"  Files created: {[str(f) for f in self.files]}")
        for w in self.warnings:
            lines.append(f"  WARN: {w}")
        for e in self.errors:
            lines.append(f"  ERR:  {e}")
        return "\n".join(lines)


def run_test(api_mode: str, selfopt_url: str, prompt: str, timeout: int) -> TestResult:
    result = TestResult(api_mode)

    ws = Path(tempfile.mkdtemp(prefix=f"cline_{api_mode}_"))
    print(f"\n{'='*60}")
    print(f"  Cline test - API={api_mode}   workspace={ws}")
    print(f"{'='*60}")
    setup_workspace(ws)

    # 1. Configure Cline API via globalState SQLite (while VS Code is stopped)
    warns = configure_cline_api(api_mode, selfopt_url)
    result.warnings.extend(warns)

    # 2. Launch VS Code (killing any existing instance if CDP isn't already live)
    try:
        launch_vscode(ws)
    except RuntimeError as exc:
        return result.fail(str(exc))

    time.sleep(4)  # let VS Code finish rendering its workbench UI
    before = snapshot_files(ws)

    with sync_playwright() as p:
        print("  Connecting via CDP...")
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as exc:
            return result.fail(f"CDP connect failed: {exc}")

        ctx  = browser.contexts[0]
        main = ctx.pages[0]
        print(f"  Connected. Main page URL: {main.url[:70]}")

        # 3. Open the workspace folder in VS Code (may be a different folder than at launch)
        subprocess.Popen(f'code --reuse-window "{ws}"', shell=True)
        time.sleep(2)

        # 4. Click the Cline activity-bar icon
        print("  Opening Cline panel...")
        opened = False
        for selector in [
            '[aria-label="Cline"]',
            '[aria-label*="Cline"]',
            '.activity-bar [title*="Cline"]',
        ]:
            try:
                btn = main.locator(selector)
                if btn.count() > 0:
                    btn.first.click()
                    opened = True
                    print(f"  [click] Cline icon via {selector!r}")
                    break
            except Exception:
                pass

        if not opened:
            # Try command palette as last resort
            print("  [warn] Cline icon not found via aria-label; trying command palette")
            main.keyboard.press("Control+Shift+P")
            time.sleep(0.5)
            main.keyboard.type("Cline: Open In New Tab")
            time.sleep(0.3)
            main.keyboard.press("Enter")

        # Wait for Cline's webview to fully render
        print("  Waiting for Cline webview to load...", end="", flush=True)
        for _ in range(10):
            time.sleep(1)
            print(".", end="", flush=True)
        print()

        # 5. Fill Cline's textarea using pyautogui + clipboard
        #
        #    VS Code webview renderer processes are NOT accessible via CDP because they
        #    are spawned without --remote-debugging-port and thus don't appear in
        #    /json/list.  Playwright keyboard events stay in the main renderer.
        #    pyautogui sends OS-level mouse + keyboard input that reaches whichever
        #    window has screen focus - the Cline textarea in this case.
        print("  Injecting prompt into Cline textarea...")
        ok, info = _fill_textarea(main, prompt)
        if not ok:
            return result.fail(f"Could not fill Cline textarea: {info}")

        print(f"  Prompt submitted ({info}). Waiting up to {timeout}s for files...")

        # 6. Auto-approve loop
        deadline = time.time() + timeout
        while time.time() < deadline:
            files = new_code_files(before, ws)
            if files:
                result.files  = files
                result.passed = True
                print(f"  Files created: {[f.name for f in files]}")
                break

            _try_approve(ctx)

            err = _cline_error_text(ctx)
            if err:
                return result.fail(f"Cline reported an error: {err}")

            time.sleep(2)
        else:
            result.fail(f"Timed out after {timeout}s - no code files in {ws}")

        browser.close()

    # 7. Print file contents for verification
    if result.passed:
        for f in result.files:
            content = f.read_text(encoding="utf-8", errors="replace")
            print(f"\n  --- {f.name} ({len(content)} bytes) ---")
            print(content[:600])
            if len(content) > 600:
                print("  ... (truncated)")

    return result


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Cline automation test")
    parser.add_argument("--api", choices=["ollama", "openai", "both"], default="both",
                        help="Which SelfOpt endpoint to test (default: both)")
    parser.add_argument("--selfopt", default=SELFOPT_URL,
                        help=f"SelfOpt base URL (default: {SELFOPT_URL})")
    parser.add_argument("--timeout", type=int, default=TASK_TIMEOUT,
                        help=f"Seconds to wait for file creation (default: {TASK_TIMEOUT})")
    args = parser.parse_args()

    if not PROMPT_FILE.exists():
        print(f"[FAIL] {PROMPT_FILE} not found.")
        return 1
    prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()
    if not prompt:
        print("[FAIL] prompt.txt is empty.")
        return 1
    print(f"Prompt ({len(prompt)} chars): {prompt[:100]}{'...' if len(prompt) > 100 else ''}")

    modes = ["ollama", "openai"] if args.api == "both" else [args.api]
    results: list[TestResult] = []

    for mode in modes:
        r = run_test(mode, args.selfopt, prompt, args.timeout)
        results.append(r)

    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    for r in results:
        print(r)

    all_passed = all(r.passed for r in results)
    print(f"\n{'ALL TESTS PASSED' if all_passed else 'SOME TESTS FAILED'}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
