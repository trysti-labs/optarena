# ui-harness (VS Code extension UI engine)

Drives **real VS Code AI-extension UIs** (Cline, Roo Code, Continue) against a
configurable backend, using [`wdio-vscode-service`](https://webdriver.io/docs/wdio-vscode-service/)
— which launches a clean, isolated VS Code and switches the WebDriver context
*into* the extension's webview iframe so tests type into the actual chat and
click real approval buttons.

Normally invoked by OptArena's `cline-ui` / `roo-ui` / `continue-ui` drivers
(see `../optarena/drivers/vscode_ui.py` and ARCH.md §6.3), but it runs
standalone too:

```bash
npm install                                  # once
EXT=cline BACKEND_URL=http://localhost:11434 MODEL_ID=llama3.2 npm test
```

## Environment

| Env | Default | Meaning |
|---|---|---|
| `EXT` | `cline` | `cline` \| `roo` \| `continue` |
| `BACKEND_URL` | `http://localhost:11434` | backend base URL (`SELFOPT_URL` accepted as legacy alias) |
| `API_KIND` | `ollama` | `ollama` \| `openai` provider-config flavor (`CLINE_API` legacy alias) |
| `MODEL_ID` | `llama3.2` | model to seed into the extension config |
| `CASES_DIR` | `../optarena/cases` | case catalogue |
| `CASES` / `ONLY_CASE` | all | subset filter (comma list / single name) |
| `CASE_TIMEOUT` | `150` | per-case seconds |
| `RESULTS_FILE` | — | if set, one JSON line per case is appended here |
| `VSCODE_VERSION` | `1.123.0` | pinned VS Code build (newest the service supports) |

## How it works

1. `onPrepare` (wdio.conf.js) wipes `.workspace-<ext>` + `.vscode-storage-<ext>`
   and **seeds the extension's config** for the backend: Cline/Roo via rows in
   the profile's `state.vscdb` (`<extId>/<key>` → JSON), Continue via
   `config.yaml` in an isolated `CONTINUE_GLOBAL_DIR`. Auto-approval on,
   onboarding marked done, telemetry off.
2. The config **scrubs `ELECTRON_RUN_AS_NODE` / `VSCODE_*`** — required when
   launched from a VS Code terminal (otherwise the spawned Code.exe runs as
   plain Node and rejects every Chromium flag).
3. The spec (`test/agent.e2e.js`) opens the extension view by command ID,
   advances through any onboarding/provider wizard one fresh-frame step at a
   time, types each prompt with real key events, and submits.
4. While polling the filesystem it clicks approval buttons (Save/Approve/Run…,
   never reject) inside the webview, then `saveAll`.
5. A case passes when the case's `expected_files` match new/changed workspace
   files (JS oracle in `src/oracle.js`, mirroring `../optarena/cases.py`).

Everything extension-specific (view/command IDs, config seeding, button
regexes, wizard handling) is data in `src/extensions.js` — the spec is generic.

## Gotchas this harness absorbs (details in ../ARCH.md §8)

- VS Code pinned to 1.123.0 — `stable` rejects the service's launch flags.
- Never cache a webview handle; re-acquire per interaction (view moves/SPA
  re-renders replace the iframe).
- JS `scrollIntoView()+focus()` + JS-click fallback — this Electron lacks the
  Actions-API scroll CDP command.
- A stuck VS Code auto-updater (`CodeSetup*.exe`) holds the global update
  mutex and blocks all launches → wdio reports `Connection timeout exceeded`.
- Kill orphaned `index.exe`/chromedriver after crashed runs.
