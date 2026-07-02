# VS Code Extension Automation Test Plan
_June 30, 2026_

> **⚠️ SUPERSEDED — see [README.md](./README.md) (OptArena).** The CDP + `pyautogui`
> approach described below could never reach Cline's webview (it runs in a separate
> renderer process) and relied on fragile screen-pixel clicking. This directory is now
> the **OptArena** framework: `optarena/` (runner/compare/CLI), `cline-ui/` (the
> working WebdriverIO UI harness with real webview frame switching), `dashboard/`.
> The legacy `cline/`, `continue_ext/`, `roo_cline/`, `shared/`, `run_all.py` are kept
> for reference only. This document remains as a record of the original constraints.

---

## Goal

Automate the manual tests in sections 20 (Cline), 21 (Continue), and 22 (Roo-Cline)
of MANUAL_TESTING.md.  The automation must:

1. Open a new VS Code window pointing at a temporary workspace.
2. Open the target extension panel (Cline / Continue / Roo-Cline).
3. Type each prompt from a prompt file into the extension's chat textarea.
4. Capture what the AI produced (response text + any files written).
5. Verify that the expected files were created and their content matches configured
   patterns.
6. Produce a structured JSON + human-readable report.

---

## Technical Constraints

### Why VS Code webview panels are hard to automate

VS Code renders extension side-panels (like Cline's chat UI) as Electron `<webview>`
tags, which run in a **separate renderer process** from the main workbench.  That
process is _not_ launched with `--remote-debugging-port`, so it never appears in
the CDP `/json/list` endpoint.  This means:

- **Playwright keyboard events** stay in the workbench renderer and do **not** cross
  the process boundary into the extension's webview.
- **Direct CDP to the webview** is impossible from outside VS Code.
- The only reliable input path is **OS-level mouse + keyboard events** via
  `pyautogui`, which reach whichever window has OS focus.

The existing `open_cline.py` already discovered this and uses the
`pyautogui + clipboard paste` approach.  We keep and improve that approach.

### VS Code globalState for API configuration

Cline, Roo-Cline, and (to some extent) Continue store their API settings in VS
Code's extension `globalStorage` SQLite database
(`~/.config/Code/User/globalStorage/state.vscdb`).  We can write to this file
while VS Code is _not running_ to configure the extension's API provider, base URL,
and model before launching VS Code.  This avoids needing to navigate any extension
settings UI.

### CDP for workbench-level actions

While we can't reach the webview, we _can_ use Playwright CDP to:
- Click activity-bar icons to open side panels.
- Use the Command Palette (Ctrl+Shift+P) to trigger VS Code commands.
- Read the DOM of the main workbench to locate iframe bounding rects (so pyautogui
  can compute correct screen coordinates).
- Detect certain error conditions in the workbench UI.

---

## Directory Layout

```
tests/tools/
  PLAN.md                  ← this file
  run_all.py               ← run every tool, every test case; produce JSON report
  requirements.txt         ← playwright, pyautogui, pywin32 (Win), Pillow

  shared/
    __init__.py
    vscode_utils.py        ← VS Code launch/kill/CDP connect, globalState writer
    workspace.py           ← temp workspace setup, file snapshot, diff detector
    result.py              ← TestResult dataclass, JSON serializer
    selfopt.py             ← SelfOpt health check + SelfOpt server fixtures
    approvals.py           ← auto-approve loop (Approve/Save/Run buttons)

  cline/
    __init__.py
    runner.py              ← Cline-specific: configure API, open panel, inject
                             prompt, harvest response, run one test case
    test_cases.py          ← catalogue of Cline test cases (see Test Case Format)
    run_tests.py           ← entry point: python -m tests.tools.cline.run_tests
    prompts/
      create_factorial.json
      create_fibonacci.json
      modify_add_type_hints.json
      multi_prompt_session.json

  continue/
    __init__.py
    runner.py              ← Continue-specific runner
    test_cases.py
    run_tests.py
    prompts/
      inline_complete.json
      chat_explain_code.json
      generate_function.json

  roo_cline/
    __init__.py
    runner.py              ← Roo-Cline-specific runner
    test_cases.py
    run_tests.py
    prompts/
      create_class.json
      multi_turn_refactor.json

  results/                 ← written at runtime, not committed
    *.json
    *.html
```

---

## Test Case Format

Each prompt file is a JSON document:

```json
{
  "name": "create_factorial",
  "description": "Cline creates a C factorial program from scratch",
  "prompts": [
    "Create a C program for factorial in a file called factorial.c"
  ],
  "setup_files": {},
  "expected_files": [
    {
      "path_pattern": "factorial.c",
      "content_patterns": ["factorial", "int main", "return"]
    }
  ],
  "timeout": 120
}
```

Fields:

| Field | Type | Description |
|-------|------|-------------|
| `name` | str | Unique test name (used in report) |
| `description` | str | Human-readable description |
| `prompts` | list[str] | Prompts to send in order; each waits for the extension to finish before sending the next |
| `setup_files` | dict[str,str] | Files to write into the workspace before the test starts (relative path → content); used for "modify existing file" tests |
| `expected_files` | list[obj] | Files that must exist after the test |
| `expected_files[].path_pattern` | str | Glob or exact filename; matched against all new files in the workspace |
| `expected_files[].content_patterns` | list[str] | All strings must appear in the file content (case-insensitive) |
| `timeout` | int | Seconds to wait for all expected files to appear (default 120) |

---

## Shared Module Responsibilities

### `shared/vscode_utils.py`

- `vscode_state_db() → Path` — locate `state.vscdb` cross-platform
- `write_globalstate(ext_id, settings: dict)` — write extension API config while VS Code is stopped
- `cdp_alive() → bool` — check if `:9222` is responding
- `vscode_running() → bool` — check process list
- `kill_vscode()` — kill VS Code process
- `launch_vscode(workspace: Path) → None` — kill if needed, launch with `--remote-debugging-port=9222`
- `cdp_connect(playwright) → (Browser, BrowserContext, Page)` — connect via CDP, return context + main page
- `set_clipboard(text: str)` — cross-platform clipboard write (PowerShell on Win, pbcopy on Mac, xclip on Linux)
- `open_command_palette(page: Page)` — Ctrl+Shift+P
- `run_vscode_command(page: Page, command: str)` — open palette, type command, Enter

### `shared/workspace.py`

- `setup_workspace(ws: Path, setup_files: dict) → None` — create dir, write `.vscode/settings.json` with auto-approve flags, write any `setup_files`
- `snapshot(ws: Path) → set[Path]` — all files in workspace
- `new_files(before: set[Path], ws: Path) → list[Path]` — files added since snapshot
- `check_expected(new_files, expected_files_spec, ws) → list[str]` — returns list of failure messages; empty = pass

### `shared/result.py`

- `TestResult(name, tool, api_mode)` dataclass
- `.pass_()` / `.fail(msg)` / `.warn(msg)`
- `.to_dict()` → JSON-serializable
- `write_report(results: list[TestResult], out_dir: Path)` → writes `report.json` + `report.html`

### `shared/approvals.py`

- `auto_approve_loop(ctx, ws, before, expected_spec, timeout, idle_timeout=8) → (passed, new_files, error)`
  - Polls for file creation, button clicks (Approve/Save/Run/Apply/Yes), and error banners
  - Returns early when all expected files are found
  - `idle_timeout`: if no new files for this many seconds and the last approval was > idle_timeout seconds ago, declares done (even if nothing was created — the extension may have finished without creating files)

### `shared/selfopt.py`

- `selfopt_healthy(url: str) → bool` — `GET /api/tags` health check
- `require_selfopt(url: str)` — raise `SkipTest` if not reachable

---

## Per-Tool Runner Contract

Each `runner.py` exports:

```python
def configure(api_mode: str, selfopt_url: str) -> list[str]:
    """Write globalState settings for this tool+API mode. VS Code must be stopped.
    Returns non-fatal warning strings."""

def open_panel(page: Page, ctx: BrowserContext) -> bool:
    """Click the activity-bar icon / run command to open the extension panel.
    Returns True if panel was opened."""

def inject_prompt(page: Page, prompt_text: str) -> tuple[bool, str]:
    """Locate the extension's textarea, paste prompt, submit (Ctrl+Enter or Send button).
    Returns (success, info_message)."""

def wait_for_done(ctx: BrowserContext, timeout_hint: int) -> tuple[str, str]:
    """Poll the workbench DOM for extension-specific 'task complete' or 'error' indicators.
    Returns (status, detail) where status is 'done'|'error'|'timeout'."""
```

The top-level `run_test_case(tool_runner, api_mode, selfopt_url, case, ws) → TestResult`
in each `run_tests.py` orchestrates:
1. `setup_workspace(ws, case.setup_files)`
2. `configure(api_mode, selfopt_url)`
3. `launch_vscode(ws)`
4. CDP connect
5. `open_panel(page)`
6. For each prompt: `inject_prompt(page, prompt)` → `wait_for_done(ctx)`
7. `check_expected(new_files, case.expected_files, ws)`
8. Return `TestResult`

---

## Tool-Specific Notes

### Cline (`saoudrizwan.claude-dev`)

- **globalState keys**: `apiProvider`, `ollamaBaseUrl` / `openAiBaseUrl`, `openAiApiKey`, `ollamaModelId` / `openAiModelId`
- **Activity-bar aria**: `[aria-label="Cline"]` or `[aria-label*="Cline"]`
- **Panel complete detection**: look for absence of a spinner + presence of text that doesn't look like "thinking..." — or just rely on file-system polling. Cline does not have a reliable "done" DOM element that we can hook without deep webview access.
- **API config prefix**: `saoudrizwan.claude-dev/`

### Continue (`continue.continue`)

- **globalState keys**: `config.json` — Continue stores config as a JSON blob in the globalState DB. Key: `continue/config` or directly edits `~/.continue/config.json`.
- **Activity-bar aria**: `[aria-label="Continue"]`
- **Simpler interaction**: Continue's chat is a `<textarea>` in the webview; same clipboard approach applies.
- **Config**: Edit `~/.continue/config.json` directly (it's a plain file, not SQLite). Append a model entry for SelfOpt.

### Roo-Cline (`RooVeterinaryInc.roo-cline`)

- **globalState keys**: `apiProvider`, `ollamaBaseUrl`, `ollamaModelId` — same schema as Cline (`saoudrizwan.claude-dev`) but under key prefix `rooveterinaryinc.roo-cline/`
- **Activity-bar aria**: `[aria-label*="Roo"]` or `[aria-label*="roo"]`
- **Panel detection**: Same as Cline — file-system polling is the most reliable approach.

---

## Known Limitations & Mitigations

| Limitation | Mitigation |
|------------|-----------|
| Webview content inaccessible via CDP | pyautogui for input; file-system for output verification |
| pyautogui requires screen focus | Script brings VS Code to foreground via `subprocess.Popen('code --reuse-window ...')` before clicking |
| Cline shows approval dialogs that block progress | `approvals.py` polls for button text and clicks automatically |
| Extension may not be installed | `vscode_utils.check_extension_installed(ext_id)` returns warning / skip |
| VS Code startup time varies (5–30 s on slow machines) | Configurable `VSCODE_STARTUP_WAIT` env var (default 8 s) |
| Multi-monitor setups: wrong screen coordinates | Document that tests must run with VS Code on the primary monitor |
| HiDPI (retina / 200% scale) shifts pyautogui coords | `vscode_utils.get_dpi_scale()` reads DPI and adjusts rect coordinates |
| Headless CI environments (no display) | Out of scope for now; these are "headed" UI tests. Xvfb can be used on Linux CI. |
| Response content is inside the webview (inaccessible) | File content is the primary oracle. For tools with response-log features (Cline saves `.cline/history`), parse that as secondary oracle. |

---

## Response Logging

Cline saves full task history under `<workspace>/.cline/tasks/<task_id>/`.  After
each test we scan this directory for `api_conversation_history.json` which contains
the full message exchange.  This is extracted and stored in the `TestResult` as
`response_log` for diagnostics.

Continue does not write a response log file.  The only oracle is the workspace diff.

---

## Entry Points

```bash
# Run all Cline tests (both APIs)
python -m tests.tools.cline.run_tests

# Run Cline ollama only
python -m tests.tools.cline.run_tests --api ollama

# Run a specific test case
python -m tests.tools.cline.run_tests --test create_factorial

# Run all tools
python -m tests.tools.run_all

# Custom SelfOpt URL + longer timeout
python -m tests.tools.run_all --selfopt http://localhost:8001 --timeout 180

# Output report to custom dir
python -m tests.tools.run_all --output /tmp/reports
```

---

## Implementation Order

1. `shared/` modules (foundation)
2. `cline/` runner + prompts (highest-priority tool)
3. `roo_cline/` runner (same API, different ext ID + aria labels)
4. `continue/` runner (different config mechanism)
5. `run_all.py`
6. HTML report template in `result.py`
