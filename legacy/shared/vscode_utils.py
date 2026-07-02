"""
shared/vscode_utils.py
──────────────────────
VS Code process management, CDP connection, globalState SQLite writer,
clipboard helpers, and command-palette helpers.

All paths and commands are cross-platform (Windows / macOS / Linux).
"""

from __future__ import annotations

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

try:
    from playwright.sync_api import BrowserContext, Browser, Page, sync_playwright
except ImportError:
    pass  # lazy-installed on first use

CDP_URL = os.environ.get("VSCODE_CDP_URL", "http://localhost:9222")
VSCODE_STARTUP_WAIT = int(os.environ.get("VSCODE_STARTUP_WAIT", "8"))

CODE_EXTENSIONS = {".c", ".cpp", ".h", ".py", ".js", ".ts", ".java", ".go",
                   ".rs", ".cs", ".rb", ".sh", ".md", ".json", ".yaml", ".yml"}


# ── CDP health ───────────────────────────────────────────────────────────────

def cdp_alive(port: int = 9222) -> bool:
    try:
        urllib.request.urlopen(f"http://localhost:{port}/json", timeout=2)
        return True
    except Exception:
        return False


# ── VS Code process management ────────────────────────────────────────────────

def vscode_running() -> bool:
    checks = {
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
    fn = checks.get(sys.platform)
    return fn() if fn else False


# PID of the VS Code test instance we launched (so we only kill that one).
_test_vscode_proc: Optional[subprocess.Popen] = None  # type: ignore[type-arg]


def _pid_listening_on_port(port: int) -> Optional[int]:
    """Return the PID of the process listening on *port*, or None."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            for line in out.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        return int(parts[-1])
        else:
            out = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if out:
                return int(out.splitlines()[0])
    except Exception:
        pass
    return None


def _wait_for_port_free(port: int, timeout: int = 15) -> bool:
    """Block until nothing is listening on *port*. Returns True when free."""
    for _ in range(timeout):
        if not cdp_alive(port):
            return True
        time.sleep(1)
    return False


def _kill_pid(pid: int) -> None:
    """Kill a process tree by PID (cross-platform)."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
        else:
            import signal as _signal, os as _os
            _os.killpg(_os.getpgid(pid), _signal.SIGKILL)
    except Exception:
        pass


def kill_vscode(port: int = 9222) -> None:
    """
    Kill ONLY the VS Code test instance we launched.

    Uses the stored Popen object first; falls back to finding the process
    listening on *port*.  Waits until the port is actually released before
    returning so the next launch_vscode() call starts with a clean slate.
    Never kills all Code.exe processes.
    """
    global _test_vscode_proc

    # Primary: kill the process tree we started
    if _test_vscode_proc is not None:
        _kill_pid(_test_vscode_proc.pid)
        _test_vscode_proc = None

    # Fallback: kill whatever is still listening on the CDP port
    pid = _pid_listening_on_port(port)
    if pid:
        _kill_pid(pid)

    # Wait until the OS releases the port (up to 15 s)
    freed = _wait_for_port_free(port, timeout=15)
    if not freed:
        print(f"  [vscode] WARN: port {port} still in use after kill attempt")


def _default_extensions_dir() -> Path:
    """Return the user's standard VS Code extensions directory."""
    home = Path.home()
    if sys.platform == "win32":
        return home / ".vscode" / "extensions"
    elif sys.platform == "darwin":
        return home / ".vscode" / "extensions"
    else:
        return home / ".vscode" / "extensions"


def launch_vscode(workspace: Path, port: int = 9222) -> int:
    """
    Launch an ISOLATED VS Code instance with ``--user-data-dir`` so it never
    merges into the user's existing VS Code windows.  Extensions are loaded
    from the user's real ``~/.vscode/extensions`` directory so Cline /
    Roo-Cline / Continue are available without re-installation.

    Returns the subprocess PID.  Raises RuntimeError on failure.
    """
    global _test_vscode_proc

    # If something is already on the port (e.g. a previous test left it alive),
    # kill it before we try to launch a fresh instance.
    if cdp_alive(port):
        print(f"  [vscode] CDP still alive on :{port} — killing stale instance")
        kill_vscode(port)
        if cdp_alive(port):
            raise RuntimeError(f"Cannot free CDP port {port} — kill failed")

    # Isolated user-data dir keeps test settings away from the real VS Code.
    test_user_data = Path(tempfile.gettempdir()) / f"vscode_test_userdata_{port}"
    test_user_data.mkdir(parents=True, exist_ok=True)
    ext_dir = _default_extensions_dir()

    print(f"  [vscode] launching isolated instance  port={port}  ws={workspace}")
    print(f"  [vscode] user-data={test_user_data}  extensions={ext_dir}")

    cmd = (
        f'code '
        f'--user-data-dir "{test_user_data}" '
        f'--extensions-dir "{ext_dir}" '
        f'--remote-debugging-port={port} '
        f'--new-window '
        f'"{workspace}"'
    )
    proc = subprocess.Popen(cmd, shell=True)
    _test_vscode_proc = proc

    print("  [vscode] waiting for CDP", end="", flush=True)
    for _ in range(60):
        time.sleep(1)
        print(".", end="", flush=True)
        if cdp_alive(port):
            print(" ready!")
            time.sleep(VSCODE_STARTUP_WAIT)  # let workbench UI fully render
            return proc.pid
    print()
    # Clean up the process if CDP never came up
    kill_vscode(port)
    raise RuntimeError(f"VS Code CDP did not start within 60 s on port {port}.")


def cdp_connect(playwright, port: int = 9222) -> tuple:
    """Return (browser, context, main_page) connected via CDP."""
    browser = playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
    ctx = browser.contexts[0]
    main = ctx.pages[0]
    print(f"  [cdp] connected. pages={len(ctx.pages)}  main={main.url[:60]}")
    return browser, ctx, main


# ── DPI scaling ───────────────────────────────────────────────────────────────

def get_dpi_scale() -> float:
    """
    Return the display scale factor so pyautogui screen coordinates are correct
    on HiDPI / retina displays.  CDP bounding rects are in CSS pixels; pyautogui
    uses physical pixels on some platforms.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            awareness = ctypes.c_int()
            ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(awareness))
            if awareness.value >= 1:
                hdc = ctypes.windll.user32.GetDC(0)
                dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
                ctypes.windll.user32.ReleaseDC(0, hdc)
                return dpi / 96.0
        except Exception:
            pass
    return 1.0


# ── Clipboard helpers ─────────────────────────────────────────────────────────

def set_clipboard(text: str) -> None:
    """Put *text* on the OS clipboard using only stdlib / OS builtins."""
    if sys.platform == "win32":
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"Set-Clipboard -Value {json.dumps(text)}"],
            capture_output=True, timeout=10,
        )
    elif sys.platform == "darwin":
        subprocess.run(["pbcopy"], input=text.encode(), check=True, timeout=5)
    else:
        try:
            subprocess.run(["xclip", "-selection", "clipboard"],
                           input=text.encode(), check=True, timeout=5)
        except FileNotFoundError:
            try:
                subprocess.run(["xdotool", "type", "--", text], timeout=30)
            except FileNotFoundError:
                pass  # best-effort


# ── Command palette ───────────────────────────────────────────────────────────

def open_command_palette(page: Page) -> None:
    page.keyboard.press("Control+Shift+P")
    time.sleep(0.4)


def run_vscode_command(page: Page, command: str) -> None:
    open_command_palette(page)
    page.keyboard.type(command)
    time.sleep(0.3)
    page.keyboard.press("Enter")
    time.sleep(0.5)


# ── globalState SQLite writer ─────────────────────────────────────────────────

def _state_db_path() -> Path:
    roots = {
        "win32":  Path(os.environ.get("APPDATA", "")) / "Code",
        "darwin": Path.home() / "Library" / "Application Support" / "Code",
    }
    root = roots.get(sys.platform, Path.home() / ".config" / "Code")
    return root / "User" / "globalStorage" / "state.vscdb"


def write_globalstate(ext_id: str, settings: dict) -> list[str]:
    """
    Write *settings* into VS Code's extension globalState SQLite database.
    Each key is stored as ``<ext_id>/<key>`` with a JSON-encoded value.

    VS Code must NOT be running when this is called (the DB is locked while VS
    Code holds it open).

    Returns a list of non-fatal warning strings (empty = success).
    """
    db = _state_db_path()
    if not db.exists():
        return [f"globalState DB not found at {db} — configure extension API manually."]

    warnings: list[str] = []
    try:
        conn = sqlite3.connect(str(db), timeout=5)
        cur  = conn.cursor()
        for key, val in settings.items():
            cur.execute(
                "INSERT OR REPLACE INTO ItemTable (key, value) VALUES (?, ?)",
                (f"{ext_id}/{key}", json.dumps(val)),
            )
        conn.commit()
        conn.close()
        print(f"  [globalstate] {ext_id}: wrote {list(settings.keys())}")
    except Exception as exc:
        warnings.append(f"globalState write failed ({exc})")
    return warnings


# ── Extension detection ───────────────────────────────────────────────────────

def check_extension_installed(ext_id: str) -> bool:
    """
    Return True if the extension is present in the user's extensions directory.

    We check the filesystem directly rather than running ``code --list-extensions``
    because the test VS Code uses ``--user-data-dir`` which can confuse the CLI.
    """
    ext_dir = _default_extensions_dir()
    id_lower = ext_id.lower()
    try:
        for entry in ext_dir.iterdir():
            if entry.is_dir() and entry.name.lower().startswith(id_lower.split(".")[0]):
                # e.g. saoudrizwan.claude-dev-3.x.x
                if id_lower in entry.name.lower():
                    return True
    except Exception:
        pass
    # Fallback: try CLI (may still work if extensions-dir is the default)
    try:
        result = subprocess.run(
            ["code", "--extensions-dir", str(_default_extensions_dir()),
             "--list-extensions"],
            capture_output=True, text=True, timeout=10,
        )
        installed = [e.strip().lower() for e in result.stdout.splitlines()]
        if id_lower in installed:
            return True
    except Exception:
        pass
    return False


# ── Webview iframe locator ────────────────────────────────────────────────────

_FIND_WEBVIEW_JS = """
() => {
    const selectors = [
        '.part.sidebar iframe',
        '.part.panel iframe',
        '.editor-container iframe',
        'iframe.webview.ready',
        'iframe[src*="extensionId"]',
        'iframe',
    ];
    for (const sel of selectors) {
        for (const el of document.querySelectorAll(sel)) {
            const r = el.getBoundingClientRect();
            if (r.width > 150 && r.height > 150) {
                return {x: r.x, y: r.y, w: r.width, h: r.height, sel};
            }
        }
    }
    return {error: 'no-iframe', total: document.querySelectorAll('iframe').length};
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


def get_webview_textarea_screen_pos(page: Page) -> Optional[tuple[int, int]]:
    """
    Return the screen (x, y) coordinate for the textarea area at the bottom of
    the first sizeable webview iframe in VS Code's DOM.  Returns None if no
    iframe is found.
    """
    try:
        win = page.evaluate(_WIN_POS_JS)
        rect = page.evaluate(_FIND_WEBVIEW_JS)
    except Exception as exc:
        print(f"  [webview] DOM eval failed: {exc}")
        return None

    scale = get_dpi_scale()

    if "error" in rect:
        # Fall back to bottom-left quarter of the window (sidebar region)
        print(f"  [webview] {rect} — using fallback coords")
        x = int((win["screenX"] + min(280, win["outerW"] // 4)) * scale)
        y = int((win["screenY"] + win["outerH"] - 80) * scale)
    else:
        x = int((win["screenX"] + rect["x"] + rect["w"] / 2) * scale)
        y = int((win["screenY"] + rect["y"] + rect["h"] - 70) * scale)
        print(f"  [webview] iframe via {rect['sel']!r} "
              f"@ ({int(rect['x'])},{int(rect['y'])}) {int(rect['w'])}×{int(rect['h'])} "
              f"→ screen ({x},{y})")
    return x, y


def inject_text_via_clipboard(page: Page, text: str) -> tuple[bool, str]:
    """
    Click the webview textarea area and paste *text* from the clipboard.
    Submits with Ctrl+Enter.  Returns (success, info).
    """
    import pyautogui  # type: ignore  # noqa: PLC0415
    pos = get_webview_textarea_screen_pos(page)
    if pos is None:
        return False, "could not locate webview iframe"

    x, y = pos
    set_clipboard(text)
    pyautogui.click(x, y)
    time.sleep(0.4)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.4)
    pyautogui.hotkey("ctrl", "enter")
    return True, f"pasted at screen ({x}, {y})"
