# OptArena - Architecture

_Last updated: 2026-07-02_

OptArena is a **local-first testing and comparison framework for AI coding
tools**. It runs the same task cases through real tools - a VS Code extension's
actual UI, a CLI agent, an SDK agent, or a raw model - against any
OpenAI/Ollama-compatible backend, records per-case metrics, and compares
scenarios side-by-side.

This document is the reference for how the system is put together: the domain
model, every component, the contracts between them, and how to extend it.

---

## 1. Goals and non-goals

### Goals

1. **UI-native testing.** Where a tool has a real UI (Cline in VS Code), drive
   *that* - real webview typing, real approval buttons - not a simulation of
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
│   ├── pricing.py               USD cost estimation from token usage
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
├── docker/                     ── check_command sandbox images ──
│   ├── Dockerfile               base image (gcc + python3 + node) - DOCKER_IMAGE_DEFAULT
│   ├── python/Dockerfile         + fastapi/pydantic/uvicorn/flask/django
│   ├── node/Dockerfile           + express/react/babel (global, NODE_PATH-resolved)
│   ├── jvm/Dockerfile            + maven, ~/.m2 warmed with spring-boot-starter-*
│   ├── go/Dockerfile             + go, module cache warmed with gin
│   ├── rust/Dockerfile           + cargo, registry cache warmed with axum/tokio
│   └── dotnet/Dockerfile         + dotnet SDK, NuGet cache warmed + offline.nuget.config
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
**environment variables in** and a **JSONL results file out** (§6.3) - no RPC.

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
  "language":       "c",
  "prompts":        ["Create a C program … in a file called factorial.c …"],
  "setup_files":    {"utils.py": "def add(a, b): …"},
  "expected_files": [{"path_pattern": "factorial.c",
                      "content_patterns": ["factorial", "int main", "return"]}],
  "test_setup_files": {"test_factorial.py": "import subprocess … assert '120' in out"},
  "check_command":  "python3 test_factorial.py",
  "timeout":        120
}
```

- `prompts` are sent in order (multi-turn sessions supported).
- `language` is free-form metadata (not validated against a fixed list) - it
  powers `optarena list cases --language <x>` and `optarena run --language
  <x>` (the latter resolves to the matching case names before the scenario
  is built, in `cli._scenario_from_args()`). **Caught via manual testing:**
  `load_cases(names, ...)` used `if names:` to decide "filter or load all" -
  since an empty list is falsy in Python, a `--language` filter matching
  zero cases resolved to `names=[]` and silently fell through to "no
  filter, load everything" instead of "load nothing". Fixed to
  `if names is not None:`; a run with no matching cases now correctly
  reports `cases=0` and does nothing, rather than running the whole catalogue.
- **Benchmark-corpus metadata** (`OptArena_Benchmark_Corpus_Specification.md`):
  `framework`, `domain`, `difficulty` (1 easy .. 5 expert), `task_type`, `tags`
  are all optional, free-form fields alongside `language` - not validated
  against a fixed enum, purely descriptive. `framework` gets the same
  first-class filter as `language`: `cases.filter_cases(cases, language=,
  framework=)` ANDs both together, used by both `optarena run
  --language/--framework` and `optarena list cases --language/--framework`.
  The corpus is 120 cases spanning 11 language/framework tracks (Python,
  JavaScript/TypeScript, Java, Go, Rust, C#, C/C++, SQL, Shell, Docker
  Compose, Terraform) at the exact per-track allocation the spec's own
  Phase 1 table asks for - see §10.1 for the full breakdown and what's
  explicitly deferred beyond it.
- `setup_files` are written into the workspace before the run ("modify" cases).
- `expected_files` is the **oracle**: the case passes iff every spec matches a
  file that is *new or modified* since the pre-run snapshot, containing every
  `content_patterns` substring (case-insensitive). `path_pattern` is a glob
  matched against the relative path and the basename.
- Optional per-spec assertions: `not_content_patterns` (forbidden substrings),
  `regex_patterns` (required, case-insensitive), `min_lines`.
- Optional per-case `check_command` (+ `check_command_timeout`, default 60 s):
  a shell command run in the workspace after the file checks pass; non-zero
  exit fails the case. Content patterns assert shape, the command asserts
  behavior (compile it, run the real tests). Implemented identically in the
  Python oracle (`cases.py`) and the JS mirror (`ui-harness/src/oracle.js`).
- Optional per-case `test_setup_files`: `{relpath: content}`, written into the
  workspace by `evaluate_case`/`evaluateCase` *after* the driver's run (so the
  model never sees the tests it's graded against, unlike `setup_files`), just
  before `check_command` runs. This is how a case ships real test code
  (pytest-style asserts, a Node `assert` script, a Python harness that
  compiles-and-runs a C binary and checks its stdout) instead of relying on
  substring matching for correctness.
- **`check_command` execution is sandboxed in Docker** when available, via
  **one shared container per distinct image needed by a run** (`cases.
  DockerSandbox`, keyed in the module-level `_active_sandboxes: dict[image,
  DockerSandbox]`) - not one container per check_command call, and not just
  one container overall. `runner.run_scenario()` computes `images_needed =
  {case.get("docker_image") or DOCKER_IMAGE_DEFAULT for case in cases if
  case.get("check_command")}` and starts one `DockerSandbox` per image in
  that set, so a run mixing e.g. a Python case and a Go case gets both
  toolchains live at once, each bind-mounting the run's whole temp workspace
  root at `/workspace`. Every case's `check_command` resolves its own
  required image first (`case.get("docker_image") or ... or
  DOCKER_IMAGE_DEFAULT`) and looks it up in `_active_sandboxes` before
  `docker exec`ing in; case/trial calls for the same image still share that
  one container (`-w /workspace/<case>/<trial-subdir>`), and every sandbox in
  the run is stopped in the `finally` block so cleanup happens even on error.
  This generalizes an earlier single-container fix - a fresh ephemeral
  `docker run --rm` per call once meant 7 cases x 3 trials = 21 containers
  for one run; the single shared container came first, multi-image support
  (for the benchmark-corpus expansion) came after.
  `DockerSandbox` uses `--network none`, `--memory 2g`, `--cpus 2`; each
  `docker exec` wraps its command in the container's own `timeout <N>s` so a
  hung test is killed inside its own process tree rather than needing the
  shared container itself removed. Falls back to one ephemeral `docker run
  --rm` per call (the pre-fix behavior) when `evaluate_case`/`run_check_command`
  is called with no matching active sandbox for that case's image (e.g.
  `evaluate_case` called directly, outside the runner), and to the host
  (one-time warning to stderr) when Docker is unreachable or
  `OPTARENA_NO_DOCKER=1` is set. `docker_image_available()` /
  `_docker_available()` cache their `docker` CLI probes for the process
  lifetime.
- **`DOCKER_IMAGES` registry** (`cases.py`) maps a short track name to its
  image tag: `base` (the original combined gcc+python3+node image, unchanged,
  still the default for cases with no `docker_image`), plus `python`, `node`,
  `jvm`, `go`, `rust`, `dotnet` - one per benchmark-corpus track that needed a
  language/framework toolchain the base image doesn't have. `dockerfile_for
  (lang)` resolves the Dockerfile path by convention: `docker/Dockerfile` for
  `base`, `docker/<lang>/Dockerfile` for everything else. `optarena docker
  build` defaults to `base`; `--lang <name>` builds one track, `--all` builds
  every registered image. `optarena doctor` reports build status for every
  image in the registry (advisory only - doesn't fail the exit code, since
  the host fallback exists).
- **Offline-safe per-language images.** `check_command` runs with `--network
  none`, so every dependency a track needs must already be baked into its
  image at *build* time (network available then), not installed at
  check-command time:
  - `docker/python`: pip-installs fastapi/pydantic/uvicorn/flask/django/
    sqlalchemy/typer/pyyaml/httpx/requests/pytest globally.
  - `docker/node`: npm-installs express/react/react-dom/vue/@babel/*/jest/
    typescript/ts-node/@nestjs/core/@nestjs/common/@nestjs/platform-express/
    reflect-metadata/rxjs globally and sets `NODE_PATH` so plain
    `require(...)` resolves them (NestJS cases run via `ts-node` for
    decorator support; Next.js and Angular were deliberately not added -
    both need heavier project scaffolding than fits this pattern, see §10.1).
  - `docker/jvm`: a scratch Spring Boot project (pinned to
    `spring-boot-starter-parent` 3.3.4, `spring-boot-starter-web`,
    `spring-boot-starter-test`, `spring-boot-maven-plugin`) is `mvn package`'d
    once to warm the shared `~/.m2` cache; a case's own `pom.xml` at the same
    versions then builds/tests fully offline with `mvn -o`.
  - `docker/go`: a scratch module requiring Gin AND Fiber is `go build`'t once
    to warm the module + build cache; `GOFLAGS=-mod=mod`, `GOPROXY=off`,
    `GOSUMDB=off` let a case's own `go.mod`/`go.sum` (shipped via
    `setup_files` so the model doesn't need network either) resolve and
    build fully offline.
  - `docker/rust`: a scratch crate depending on axum/actix-web/tokio/serde/
    serde_json (Actix-web built as a separate `src/bin/` target, since its
    macro can't share a binary with `#[tokio::main]`) is `cargo build`'t once
    to warm the `~/.cargo` registry (index + downloaded crates); `cargo build
    --offline` then resolves a case's own `Cargo.toml` (compatible version
    ranges, no `Cargo.lock` needed) from that cache.
  - `docker/dotnet`: a scratch minimal-API + xUnit/`WebApplicationFactory`
    test project is restored/tested once to warm the NuGet global-packages
    folder, then `offline.nuget.config` is installed machine-wide pointing
    the *only* package source at that folder (which is itself a valid v3-style
    local feed by folder layout) - `dotnet restore`/`test` on a case's own
    project at the same package versions then succeeds fully offline.
  - `docker/Dockerfile` (base, shared by SQL/Shell/Docker-Compose/Terraform
    cases alongside the original C/Python/Node ones): also carries
    `terraform` (pinned binary, `CHECKPOINT_DISABLE=1` so it never phones
    home for a version check), `pyyaml` (structural validation of YAML in
    Compose/CI-workflow cases), and `sqlite3` (CLI + Python's stdlib module,
    for the SQL track).
  Every one of these was verified by mounting a **fresh** project (not the
  warmup files, which are deleted from the image) into a container run with
  `--network none` and confirming `build`/`test` succeeds purely from cache.
- **Trade-off of one shared container:** all cases/trials in a run share one
  network namespace (unlike the old per-call ephemeral containers, which
  each got a fresh one). A case that binds a fixed port across repeated
  trials (`create_server_c` binds `:8080`) can occasionally collide with a
  not-yet-released binding from a prior trial - observed live as a single
  `Bind failed: Address already in use` trial-3 failure in an otherwise
  3/3-passing run. This is a pre-existing class of test flakiness (identical
  to running repeated port-binding tests on a shared host), not specific to
  Docker; case authors writing port-binding tests should prefer an ephemeral
  port or tolerate the rare collision via a bind-retry in their
  `test_setup_files` script.
- **Why content patterns alone are insufficient:** observed in practice - a
  small local model wrote valid-looking C++ (`#include <iostream>`,
  `cout`/`cin`, Unicode smart quotes) into a `factorial.c` case. It contained
  every required substring (`"factorial"`, `"int main"`, `"return"`) and
  would have passed a keyword-only oracle; `gcc factorial.c` fails outright.
  Real test execution via `check_command` is the only oracle that catches
  this class of failure.
- `evaluate_case()`/`evaluateCase()` return `(failures, oracle_info)`, not
  just `failures` - `oracle_info` is `{check_command, ran, sandbox, image,
  exit_code, duration_s, output, test_setup_files}` and is stashed by every
  driver into `CaseResult.extra["oracle"]` (trials keep a per-trial list
  under `extra["oracle_all_trials"]`). This is a CLI-first tool, so `runner.py`
  prints it by default under each case line - which sandbox ran, exit code,
  timing, and captured output - rather than collapsing everything to
  PASS/FAIL and requiring the dashboard to see what actually happened.

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

`failures` (oracle mismatches - the tool ran but produced the wrong thing) are
deliberately distinct from `error` (infrastructure problems - tool crashed,
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

Exit code: `0` iff every executed scenario had zero failed cases - usable in
scripts even before proper CI support.

Two opt-in runner modes (defaults preserve the single-trial serial behaviour):

- `--trials N` - each case runs N times in fresh workspaces; `passed` is the
  majority verdict and per-trial detail lands in `extra` (`trials`, `passes`,
  `pass_rate_trials`, `durations_s`). Ignored for drivers that execute all
  cases inside `prepare()` (the UI drivers), which set `caches_results`.
- `--parallel N` - cases fan out over N worker threads for drivers marked
  `parallel_safe` (baselines, CLI agents). UI drivers stay serial (one
  display).

### Driver lifecycle contract

- `prepare()` / `teardown()` bracket the whole scenario. Drivers with expensive
  startup (a VS Code session) run **all cases inside `prepare()`** in one
  session and serve cached results from `run_case()` (§6.3). Per-case launch
  would dominate every timing measurement.
- `run_case()` must never raise for tool-level failure - it returns a
  CaseResult with `error` set. Raising is reserved for "the experiment cannot
  proceed at all" (missing binary, harness not installed).
- Drivers must scrub `ELECTRON_RUN_AS_NODE` and `VSCODE_*` from any subprocess
  environment (§8.1).

---

## 5. Metrics & comparison

Per-case: `passed`, `duration_s` (wall time of tool work only - workspace prep
excluded), `files` (created/changed), `failures[]`, `error`, `extra{}`
(driver-specific: token counts, stderr tails, and `extra["oracle"]` - the
dict returned alongside failures by `evaluate_case()` (§3.1): `check_command`,
`ran`, `sandbox`, `image`, `container`, `exit_code`, `duration_s`, `output`,
`test_setup_files`, `diff{files_changed, lines_changed_approx}`, and (on
failure) `failure_class` - one of `syntax_error`, `compile_error`,
`assertion_failure`, `timeout`, `runtime_error`, from `classify_failure()` in
`cases.py`; a best-effort bucket from the captured output, not authoritative
and not part of the pass/fail verdict itself).

`diff_stats()` (`cases.py`) approximates change size: new files count their
full line length; "modify" cases (with `setup_files`) diff against the known
original content (the pre-run snapshot only stores a `size:mtime` signature,
not content, so this is the best available reference) - an edit that
happens to produce the same line count still counts as >= 1 changed line
(`abs(delta) or 1`), so a real edit is never reported as zero.

`pricing.py` estimates USD cost from token usage: `estimate_cost(model,
prompt_tokens, completion_tokens, base_url)` returns `0.0` for any localhost
backend (`is_local_backend()`) and for models with no entry in its
substring-matched price table (longest key wins, so `gpt-4o-mini` doesn't
fall through to the pricier `gpt-4o` entry) - "free"/unpriced is always the
honest default, never a fabricated number. The built-in table is overridable
via `OPTARENA_PRICING` or `~/.optarena/pricing.json` (`{"model": [prompt_per_1m,
completion_per_1m]}`). The baseline drivers (`openai_chat.py`) compute
`extra["cost_usd"]` per case once token usage is known; agent drivers (CLI,
UI, SDK) don't currently report token usage from their underlying tool, so
their `cost_usd` is absent, same as their `total_tokens` today.

Aggregate (per run): pass rate, mean/median/total duration, total tokens,
`total_cost_usd` (sum of per-case `cost_usd`, `None` when every case was
free/unpriced so the CLI/dashboard render "free" rather than "$0.00"), and
`mean_files_changed` (mean of each case's `len(files)`).

Comparison (`compare.py`): cases aligned by name (a case present in only one
run shows as `-`), per-case pass/pass and duration delta, plus a verdict:

- `more_accurate` - higher pass rate (or `tie`)
- `faster` - lower mean case duration
- `cheaper` - lower `total_cost_usd` (`None` when neither run had a priced cost)
- `pass_rate_delta`, `mean_duration_delta_s` - signed B-A

Comparisons are saved to `results/comparisons/` and rendered by both the
terminal table and the dashboard. **Accuracy outranks speed** in interpretation
ordering (the dashboard shows accuracy verdict first); OptArena reports both
and editorializes no further.

### 5.1 Regression testing (`optarena regression`)

`regression_summary(cmp)` / `format_regression()` (`compare.py`) build on the
same `compare_runs()` output but answer a narrower, more actionable question
than the pairwise table: **which named cases flipped between A and B**, not
just an aggregate delta. `regressed_cases` = passed in A, failed in B;
`improved_cases` = the reverse. `cmd_regression` (`cli.py`) prints the
accuracy/mean-time/cost/token deltas plus both name lists, and returns exit
code `1` if `regressed_cases` is non-empty - `optarena regression <before>
<after>` is meant to be usable as a CI gate on a model/tool/prompt upgrade,
not just a human-readable report. The cost line is only printed when both
runs have a priced `total_cost_usd` (`format_regression`'s `cost_a`/`cost_b`
check) - two free local runs correctly show no cost line at all.

---

## 6. Drivers

### 6.1 Registry (`drivers/__init__.py`)

Name → class with lazy imports so optional dependencies (crewai) don't tax
everyone. Adding a driver = one module + one registry entry. Each entry
carries metadata surfaced by `optarena list drivers`:

- `kind`: `ui | cli | sdk | baseline`
- `backend`: `scenario` (obeys the scenario backend; backend-vs-backend is
  valid) or `fixed` (own account/provider; tool-vs-tool only)
- `status`: `stable | experimental | optional`

The headless terminal agents (Claude Code, Codex, OpenCode, Goose, Qwen Code)
share one generic driver (`drivers/cli_agents.py`) specialized by per-tool
descriptors - binary name, prompt/auto-approve flags, backend-injection env -
the same data-not-code pattern as `ui-harness/src/extensions.js`.

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

1. **Launches an isolated VS Code** via `wdio-vscode-service` - pinned version
   (newest with bundled locators, currently 1.123.0; `stable` rejects the
   service's ChromeDriver flags), throwaway profile under
   `ui-harness/.vscode-storage-<ext>/`, workspace under `.workspace-<ext>/`.
2. **Seeds the extension's configuration** before launch so it boots pointed
   at the scenario backend with auto-approval, no onboarding:
   - Cline/Roo: rows in the profile's `state.vscdb` (`ItemTable`, keys
     `<extId>/<key>`, JSON values) - provider, base URL, model, permissive
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

**Python↔Node protocol** - environment in:

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
an origin) - or any static server.

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
and `wdio.conf.js` scrub these** - the double scrub is intentional (either side
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

- `tempfile.mkstemp` returns an **open** fd - close it before any later
  `unlink` (WinError 32).
- Always pass `encoding="utf-8", errors="replace"` to subprocesses; npm and VS
  Code emit UTF-8 that cp1252 consoles cannot decode.
- Console prints stick to ASCII-safe glyphs (cp1252 lacks `→`, `⚠`, `…`).
- Kill orphaned `index.exe`/`chromedriver` (wdio's shim) after crashed runs -
  they linger and destabilize subsequent sessions.

### 8.4 Webview driving rules (from the Cline port)

- **Never cache a webview handle** - moving the view or an SPA re-render
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
repo (gitignored) and never touch the user's real VS Code profile - with one
deliberate exception: the ui-harness *reads* the user's installed extensions
directory to load the extension under test.

---

## 10. Roadmap

Near-term:
- **Headless CI mode** - Xvfb on Linux for UI drivers; baselines/CLI drivers
  already run headless.
- **More drivers** - Cline CLI (headless `cline --auto-approve`), OpenHands,
  Continue CLI, Copilot agent mode when automatable.
- **Richer oracles** - optional per-case build/test command (compile the C
  file, run pytest) on top of content patterns; optional LLM-judge scoring
  with the judge itself a pluggable backend.
- **Run matrix** - `optarena run --matrix` (drivers × backends) with a matrix
  dashboard view.
- **Parallelism** - baselines/CLI drivers can fan out per-case; UI drivers
  stay serial (one display).

Structural:
- Publish to PyPI (`pip install optarena`); ui-harness fetched on first UI run.
- Per-project case packs (`optarena init` scaffolding a local `cases/`).
- Optional SQLite index if run counts outgrow index.json (schema unchanged).

### 10.1 Benchmark corpus status (vs `OptArena_Benchmark_Corpus_Specification.md`)

The corpus stands at **120 cases** - the exact sum of the spec's own Phase 1
per-track allocation (Python 20, JavaScript/TypeScript 20, Java 15, Go 10,
Rust 10, C# 10, C/C++ 10, SQL 10, Shell 5, Docker Compose 5, Terraform 5),
sitting at the low end of the spec's stated 100-150 target range. Every case
was hand-verified end-to-end before being counted: a correct reference
solution passes, a broken/unfixed/unchanged one fails, run through the real
oracle (`evaluate_case`/`DockerSandbox`), not just claimed.

**Built:**
- Case schema extended with `framework`/`domain`/`difficulty`/`task_type`/
  `tags`/`docker_image` (§3.1), plus a `--framework` filter mirroring
  `--language` everywhere it appears.
- Multi-image `DockerSandbox` (one shared container per distinct image a
  run's cases need, not just one overall).
- Six new sandbox images, one per newly-supported framework family: Python
  (FastAPI/Flask/Django/SQLAlchemy/Pydantic/Typer), Node (Express/NestJS/
  React/Vue), JVM (Spring Boot), Go (Gin/Fiber), Rust (Axum/Actix-web),
  .NET (ASP.NET Core) - all pre-warmed at build time and verified to
  build/test **fully offline** (`--network none`, matching real
  `check_command` conditions). The base image gained Terraform CLI
  (`CHECKPOINT_DISABLE=1`, no network phone-home) and pyyaml/sqlite3 for the
  SQL/Shell/Docker-Compose/Terraform tracks, none of which need a
  per-language toolchain image.
- 120 cases across all 11 tracks, each tagged with the spec's task
  categories (feature/bug_fix/refactoring/testing/security/performance/
  devops) and difficulty 1-2 (the easiest two tiers).
- **A corpus-wide oracle bug found and fixed during verification**: 6 of the
  corpus's 11 initial "refactoring" cases (spanning Python, Java, C#, Rust, C,
  and Shell - not concentrated in one track or one authoring pass) would
  incorrectly PASS a model that changed nothing at all. Their hidden tests
  only asserted that observable behavior stayed correct, and unmodified
  "duplicated but already-working" code trivially satisfies that; the
  `expected_files` structural check was too weak to independently catch "no
  refactor happened" (e.g. a bare `def\s+\w+\s*\(` regex matches any
  function, including ones already in the untouched original). Caught by
  running every case's own unmodified `setup_files` through the real oracle
  and checking whether it *should* have failed - it's the same class of
  failure ARCH.md already documents for keyword-only oracles (§3.1, "why
  content patterns alone are insufficient"), just surfaced in a new category.
  Fixed by adding a structural assertion to each hidden test that counts
  occurrences of a distinctive literal from the duplicated logic in the
  model's final source and requires it collapsed to at most once, proving
  real consolidation happened rather than just "nothing broke." All 15
  refactoring-category cases in the final corpus (11 fixed/verified plus 4
  written afterward, already following the pattern) were independently
  re-confirmed corpus-wide. The 34 bug_fix/security/performance cases and all
  8 devops cases were also swept for the same class of bug and found solid.

**Explicitly deferred** (not attempted, not partially built):
- The remainder of the spec's 100-150 range beyond the exact 120-case
  per-track allocation - 120 is every case the spec's own table asks for at
  Phase 1, not a stretch goal beyond it.
- Repository-scale Level 3-5 benchmarks (20-500+ file repos, auth/async/
  migrations/architecture) - everything shipped is Level 1-2 (single or
  few-file, simple feature/fix/refactor), the easiest tiers in the spec's
  difficulty ladder.
- Docker Compose cases are **static-validation-only**: `docker-compose.yml`
  is parsed with `yaml.safe_load` and checked structurally (service names,
  required keys, no hardcoded secrets); nothing is ever `docker compose up`'d,
  since the sandbox container has no Docker socket/daemon access
  (Docker-in-Docker is out of scope).
- Terraform cases are **provider-free**: only `variable`/`output`/`locals`
  blocks, verified with `terraform fmt -check`/`terraform validate`, both of
  which work fully offline for provider-free HCL. No `resource` block is
  ever used, since any real provider requires `terraform init` to download a
  plugin, which needs network `check_command` doesn't have.
- Angular and Next.js were dropped from the JavaScript/TypeScript track's
  framework list (the spec names 7 possible JS/TS frameworks; this corpus
  covers Express, NestJS, React, Vue, and plain Node) - both need heavier
  project scaffolding (a real build step, in Next.js's case) that wasn't
  practical to verify reliably offline within this pass; the 20-case target
  was still met by concentrating on the other 5.
- Suite groupings (Arena Lite/Standard/Extended/Enterprise) - not meaningful
  until the corpus is large enough to fill them.
- Publishing any of the six new images to a registry (Docker Hub) - they
  build and run locally; `docker build --lang <x>`/`--all` exist, `docker
  push` does not.
