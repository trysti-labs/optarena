# OptArena — Architecture

_Last updated: 2026-07-02_

OptArena is a **local-first testing and comparison framework for AI coding
tools**. It runs the same task cases through real tools — a VS Code extension's
actual UI, a CLI agent, an SDK agent, or a raw model — against any
OpenAI/Ollama-compatible backend, records per-case metrics, and compares
scenarios side-by-side.

This document is the reference for how the system is put together: the domain
model, every component, the contracts between them, and how to extend it.

---

## 1. Goals and non-goals

### Goals

1. **UI-native testing.** Where a tool has a real UI (Cline in VS Code), drive
   *that* — real webview typing, real approval buttons — not a simulation of
   its API traffic. Bugs live in the seams the UI exercises.
2. **Comparison as the primary output.** Every question OptArena answers is an
   A/B: tool vs tool, backend vs backend, model vs model, agent vs raw-model
   baseline. Single runs are just comparison inputs.
3. **Backend-agnostic.** A backend is any HTTP endpoint speaking the OpenAI
   (`/v1/chat/completions`) or Ollama (`/api/chat`) protocol: a local model
   server, plain Ollama, a router/optimizer proxy, or a remote API.
4. **Local-first, zero infrastructure.** No database server, no build step, no
   accounts. Results are JSON files; the dashboard is one static HTML file; the
   core has **zero Python dependencies** (stdlib only).
5. **Cheap extensibility.** A new tool is one driver file. A new task is one
   JSON file. A new backend is a URL.

### Non-goals

- **Not a leaderboard/benchmark.** SWE-bench, Terminal-Bench, etc. rank models
  on large curated suites in containers. OptArena answers *your* A/B questions
  about *your* tool+backend combos on *your* machine in minutes.
- **Not observability.** LangFuse-style tracing of production LLM calls is out
  of scope; OptArena runs controlled experiments.
- **Not (yet) a CI gate.** Headed UI runs need a display. Headless operation is
  on the roadmap (§10).

---

## 2. Repository layout

```
optarena/                       repo root
├── ARCH.md                     this document
├── README.md                   user-facing quick start
├── pyproject.toml              packaging; console script `optarena`
├── .gitignore
├── optarena/                   ── the Python package (stdlib only) ──
│   ├── __init__.py
│   ├── __main__.py             python -m optarena
│   ├── cli.py                  argparse CLI: run / compare / list / serve
│   ├── scenario.py             Scenario + Backend dataclasses (JSON files)
│   ├── cases.py                case loading + the filesystem oracle
│   ├── runner.py               executes one scenario → RunRecord
│   ├── metrics.py              aggregates + per-case deltas
│   ├── store.py                results persistence + index
│   ├── compare.py              A/B comparison + terminal table
│   ├── cases/                  task catalogue (*.json)
│   └── drivers/                ── tool adapters ──
│       ├── __init__.py         registry (name → Driver, lazy imports)
│       ├── base.py             Driver interface + CaseResult
│       ├── openai_chat.py      raw-model baselines (OpenAI + Ollama protocol)
│       ├── aider_cli.py        aider CLI driver
│       ├── vscode_ui.py        VS Code extension UI driver (subprocess → ui-harness)
│       └── crewai_sdk.py       optional SDK-agent driver
├── ui-harness/                 ── Node/WebdriverIO engine for VS Code UIs ──
│   ├── package.json            wdio-vscode-service + better-sqlite3
│   ├── wdio.conf.js            VS Code launch config + profile seeding
│   ├── src/
│   │   ├── paths.js            run config from env; deterministic dirs
│   │   ├── extensions.js       per-extension descriptors (Cline/Roo/Continue)
│   │   ├── seed.js             globalState (state.vscdb) writer
│   │   └── oracle.js           filesystem oracle (JS mirror of cases.py)
│   └── test/agent.e2e.js       the generic driving spec
├── dashboard/
│   └── index.html              static dashboard (fetches ../results/*)
├── scenarios/                  example scenario JSON files
├── results/                    run records (gitignored)
│   ├── runs/<run_id>.json
│   ├── comparisons/*.json
│   └── index.json
└── legacy/                     retired first-generation harness (reference only)
```

Two languages by necessity: driving VS Code requires Node (wdio-vscode-service
is the only maintained stack that switches WebDriver context into webview
iframes). Everything else is Python. The two sides communicate only via
**environment variables in** and a **JSONL results file out** (§6.3) — no RPC.

---

## 3. Domain model

```
Case        one task + oracle          "create factorial.c; it must contain int main…"
Backend     where the model lives      {kind: ollama|openai, base_url, model, api_key}
Scenario    one configuration to test  driver + backend + case subset + timeout
Driver      how a tool is operated     run_case(case, scenario, workspace) → CaseResult
CaseResult  one case's outcome         passed, duration_s, files, failures, error, extra
RunRecord   one scenario execution     run_id, scenario, [CaseResult…], summary
Comparison  two runs, aligned by case  per-case deltas + verdict
```

### 3.1 Case (`optarena/cases/*.json`)

```json
{
  "name":           "create_factorial",
  "description":    "Creates a C program that computes factorial",
  "prompts":        ["Create a C program … in a file called factorial.c …"],
  "setup_files":    {"utils.py": "def add(a, b): …"},
  "expected_files": [{"path_pattern": "factorial.c",
                      "content_patterns": ["factorial", "int main", "return"]}],
  "timeout":        120
}
```

- `prompts` are sent in order (multi-turn sessions supported).
- `setup_files` are written into the workspace before the run ("modify" cases).
- `expected_files` is the **oracle**: the case passes iff every spec matches a
  file that is *new or modified* since the pre-run snapshot, containing every
  `content_patterns` substring (case-insensitive). `path_pattern` is a glob
  matched against the relative path and the basename.

**Why a filesystem oracle:** it is tool-neutral. Whether the file appeared via
a webview approval click, an aider commit, or the baseline driver writing an
extracted code block, "the right file exists with the right content" is the
same check. Content patterns should assert the *task*, not the model's style
(lesson learned: requiring the literal `int` in a type-hints case failed a
model that correctly chose `float`).

### 3.2 Scenario (`scenarios/*.json` or CLI flags)

```json
{
  "name":    "cline-gemma12b",
  "driver":  "cline-ui",
  "backend": {"kind": "ollama", "base_url": "http://localhost:11434",
              "model": "gemma4:12b"},
  "cases":   ["create_factorial"],
  "timeout": 180
}
```

`backend.kind` selects the *protocol* (`ollama` → native `/api/chat` and
extension "Ollama" provider config; `openai` → `/v1/chat/completions` and
"OpenAI-compatible" provider config). `api_key` defaults to a placeholder for
local servers that ignore it.

### 3.3 RunRecord (`results/runs/<run_id>.json`)

```json
{
  "run_id": "20260702-094708_cline-selfopt",
  "scenario": { …scenario as above… },
  "started_at": "2026-07-02T09:47:08",
  "cases": [{"name": "…", "passed": true, "duration_s": 72.9,
             "files": ["hello.py"], "failures": [], "error": null,
             "extra": {"total_tokens": 512}}],
  "summary": {"cases": 1, "passed": 1, "failed": 0, "errors": 0,
              "pass_rate": 1.0, "total_duration_s": 72.9,
              "mean_duration_s": 72.9, "median_duration_s": 72.9,
              "total_tokens": 512}
}
```

`failures` (oracle mismatches — the tool ran but produced the wrong thing) are
deliberately distinct from `error` (infrastructure problems — tool crashed,
backend refused). A comparison where one side has `error`s is a broken
experiment, not a lost one.

---

## 4. Execution flow

```
optarena run --scenario a.json --scenario b.json
  │
  ├─ for each scenario:
  │    runner.run_scenario()
  │      ├─ load cases (all, or scenario.cases subset)
  │      ├─ driver = get_driver(scenario.driver)
  │      ├─ driver.prepare(scenario, workspace_root)        # once
  │      ├─ for each case:
  │      │    ├─ fresh workspace subdir  <root>/<case_name>/
  │      │    ├─ result = driver.run_case(case, scenario, ws)
  │      │    └─ append result
  │      ├─ driver.teardown()                               # once
  │      └─ summary = metrics.aggregate(results)
  │    store.save_run(record)   → results/runs/…json + index.json rebuilt
  │
  └─ if ≥2 scenarios: compare_runs(A, B) → print table, save comparison JSON
```

Exit code: `0` iff every executed scenario had zero failed cases — usable in
scripts even before proper CI support.

### Driver lifecycle contract

- `prepare()` / `teardown()` bracket the whole scenario. Drivers with expensive
  startup (a VS Code session) run **all cases inside `prepare()`** in one
  session and serve cached results from `run_case()` (§6.3). Per-case launch
  would dominate every timing measurement.
- `run_case()` must never raise for tool-level failure — it returns a
  CaseResult with `error` set. Raising is reserved for "the experiment cannot
  proceed at all" (missing binary, harness not installed).
- Drivers must scrub `ELECTRON_RUN_AS_NODE` and `VSCODE_*` from any subprocess
  environment (§8.1).

---

## 5. Metrics & comparison

Per-case: `passed`, `duration_s` (wall time of tool work only — workspace prep
excluded), `files` (created/changed), `failures[]`, `error`, `extra{}`
(driver-specific: token counts, stderr tails).

Aggregate (per run): pass rate, mean/median/total duration, total tokens.

Comparison (`compare.py`): cases aligned by name (a case present in only one
run shows as `—`), per-case pass/pass and duration delta, plus a verdict:

- `more_accurate` — higher pass rate (or `tie`)
- `faster` — lower mean case duration
- `pass_rate_delta`, `mean_duration_delta_s` — signed B−A

Comparisons are saved to `results/comparisons/` and rendered by both the
terminal table and the dashboard. **Accuracy outranks speed** in interpretation
ordering (the dashboard shows accuracy verdict first); OptArena reports both
and editorializes no further.

---

## 6. Drivers

### 6.1 Registry (`drivers/__init__.py`)

Name → class with lazy imports so optional dependencies (crewai) don't tax
everyone. Adding a driver = one module + one registry entry.

| Driver | Status | Mechanism |
|---|---|---|
| `openai-chat` | stable | `POST /v1/chat/completions`; driver writes extracted code block |
| `ollama-chat` | stable | `POST /api/chat` (Ollama native); same convention |
| `aider` | stable | `aider --message … --yes --no-git` per prompt, cwd=workspace |
| `cline-ui` | stable | subprocess → ui-harness, `EXT=cline` |
| `roo-ui` | experimental | subprocess → ui-harness, `EXT=roo` (first-run wizard quirks) |
| `continue-ui` | experimental | subprocess → ui-harness, `EXT=continue` (agent-mode selection) |
| `crewai` | optional | crewAI SDK, single coder agent, LLM → backend |

### 6.2 The raw-model baselines

`openai-chat` / `ollama-chat` ask the model for exactly one fenced code block
and write it to the first `expected_files` path themselves. They are the
**control group**: any agent tool's value-add (planning, file ops, retries,
context) is measured as the delta against this baseline on the same backend.
They also enable *model vs model* and *endpoint vs endpoint* comparisons with
no tool in the loop. Multi-turn cases feed the current file content back into
the next prompt.

### 6.3 The VS Code UI driver + ui-harness

The flagship. `vscode_ui.py` shells out to `ui-harness/` (`npm test`), which:

1. **Launches an isolated VS Code** via `wdio-vscode-service` — pinned version
   (newest with bundled locators, currently 1.123.0; `stable` rejects the
   service's ChromeDriver flags), throwaway profile under
   `ui-harness/.vscode-storage-<ext>/`, workspace under `.workspace-<ext>/`.
2. **Seeds the extension's configuration** before launch so it boots pointed
   at the scenario backend with auto-approval, no onboarding:
   - Cline/Roo: rows in the profile's `state.vscdb` (`ItemTable`, keys
     `<extId>/<key>`, JSON values) — provider, base URL, model, permissive
     auto-approval, telemetry off.
   - Continue: `config.yaml` in an isolated `CONTINUE_GLOBAL_DIR`.
3. **Drives the real webview**: opens the extension's view by command ID,
   switches the WebDriver context *into* the webview iframe, walks any
   onboarding/provider wizard, types the prompt with real key events, submits.
4. **Auto-approves**: each poll tick re-acquires the webview (handles moves/
   re-renders), clicks approve-class buttons (`Save/Approve/Run/…`), never
   reject-class, then `saveAll`.
5. **Judges via the same filesystem oracle** (JS mirror in `src/oracle.js`)
   and **appends one JSON line per case** to `RESULTS_FILE`.

**Python↔Node protocol** — environment in:

| Env | Meaning |
|---|---|
| `EXT` | `cline` \| `roo` \| `continue` |
| `BACKEND_URL` | backend base URL |
| `API_KIND` | `ollama` \| `openai` (provider config flavor) |
| `MODEL_ID` | model name to seed |
| `CASES_DIR` | case catalogue directory (the Python package's `cases/`) |
| `CASES` | comma-separated case subset |
| `CASE_TIMEOUT` | per-case seconds |
| `RESULTS_FILE` | where to append JSONL case records |

JSONL out (one line per case):

```json
{"name": "create_hello_world", "passed": true, "duration_s": 68.4,
 "files": ["hello.py"], "failures": [], "error": null}
```

The subprocess boundary is deliberate: wdio owns its own event loop, VS Code
download cache, and crash cleanup; Python stays dependency-free; and a hung UI
run is bounded by a subprocess timeout.

### 6.4 Extension descriptors (`ui-harness/src/extensions.js`)

Everything extension-specific is data, not code: install-dir prefix, view/
new-task/focus-input command IDs, approve/reject button regexes, config
mechanism (`globalState` vs config file) and its seed content, optional
onboarding-wizard flag, optional agent-mode hint. The driving spec
(`agent.e2e.js`) is generic across extensions.

---

## 7. Results store & dashboard

### 7.1 Store (`store.py`)

Flat JSON files; `index.json` (newest-first digest of every run) is rebuilt on
each save so consumers never parse all runs. Run references in the CLI accept
an id, filename, path, or unique substring (most recent match wins).

### 7.2 Dashboard (`dashboard/index.html`)

One static file, vanilla JS, no build step. Served by `optarena serve` (stdlib
`http.server` rooted at the repo root, so `/dashboard/` and `/results/` share
an origin) — or any static server.

- Fetches `../results/index.json`, lazily fetches run files on selection,
  computes comparisons client-side (same alignment rules as `compare.py`).
- Views: Run A/B pickers → verdict tiles, per-case grouped duration bars,
  per-case results table (also the accessibility relief view), pass-rate-by-run
  chart, run summary tiles.
- Charting follows a validated spec: series colors are CVD-checked against the
  dark surface (blue `#3987e5` / aqua `#199e70`, ΔE 69.8, ≥3:1 contrast); bars
  are thin with rounded data-ends and 2px surface gaps; pass/fail always ships
  icon+label, never color alone; every chart has a hover tooltip and a table
  equivalent.

---

## 8. Cross-platform & environment notes (hard-won)

### 8.1 Launching VS Code from inside VS Code

Any terminal inside VS Code leaks `ELECTRON_RUN_AS_NODE=1` and `VSCODE_*` into
children. An inherited `ELECTRON_RUN_AS_NODE` makes a spawned `Code.exe` run as
plain Node (rejecting every Chromium flag: `bad option: --no-sandbox`);
`VSCODE_IPC_HOOK` routes it into the parent instance. **Both the Python drivers
and `wdio.conf.js` scrub these** — the double scrub is intentional (either side
may be entered directly).

### 8.2 The stuck-updater mutex

A pending VS Code auto-update (`CodeSetup*.exe … /verysilent
/nocloseapplications`) holds the global `vscode-updating` mutex while waiting
for all VS Code windows to close. Every new `Code.exe` then waits 30 s and
aborts ("Code is currently being updated"), which surfaces as the wdio proxy's
`Connection timeout exceeded`. Diagnosis: profile `main.log` says
`checkInnoSetupMutex … giving up`. Remedy: kill `CodeSetup*` processes (the
update re-attempts on next VS Code restart).

### 8.3 Windows specifics

- `tempfile.mkstemp` returns an **open** fd — close it before any later
  `unlink` (WinError 32).
- Always pass `encoding="utf-8", errors="replace"` to subprocesses; npm and VS
  Code emit UTF-8 that cp1252 consoles cannot decode.
- Console prints stick to ASCII-safe glyphs (cp1252 lacks `→`, `⚠`, `…`).
- Kill orphaned `index.exe`/`chromedriver` (wdio's shim) after crashed runs —
  they linger and destabilize subsequent sessions.

### 8.4 Webview driving rules (from the Cline port)

- **Never cache a webview handle** — moving the view or an SPA re-render
  replaces the iframe. Re-acquire per interaction.
- This Electron lacks the Actions-API scroll CDP command
  (`Browser.getWindowForTarget`): use JS `scrollIntoView()+focus()` and a JS
  click fallback, never rely on native scroll-into-view.
- Onboarding/wizards navigate the SPA and transiently blank the frame: advance
  **one step per freshly-acquired frame** in a loop, not a linear script.
- Type with real key events (`browser.keys`) so React registers input;
  `setValue` alone is unreliable in webviews.

---

## 9. Security & privacy

Everything is local: prompts and generated code go only to the backend URL in
the scenario; results are local JSON; the dashboard server binds `127.0.0.1`.
No telemetry. Seeded extension profiles are throwaway directories inside the
repo (gitignored) and never touch the user's real VS Code profile — with one
deliberate exception: the ui-harness *reads* the user's installed extensions
directory to load the extension under test.

---

## 10. Roadmap

Near-term:
- **Headless CI mode** — Xvfb on Linux for UI drivers; baselines/CLI drivers
  already run headless.
- **More drivers** — Cline CLI (headless `cline --auto-approve`), OpenHands,
  Continue CLI, Copilot agent mode when automatable.
- **Richer oracles** — optional per-case build/test command (compile the C
  file, run pytest) on top of content patterns; optional LLM-judge scoring
  with the judge itself a pluggable backend.
- **Run matrix** — `optarena run --matrix` (drivers × backends) with a matrix
  dashboard view.
- **Parallelism** — baselines/CLI drivers can fan out per-case; UI drivers
  stay serial (one display).

Structural:
- Publish to PyPI (`pip install optarena`); ui-harness fetched on first UI run.
- Per-project case packs (`optarena init` scaffolding a local `cases/`).
- Optional SQLite index if run counts outgrow index.json (schema unchanged).
