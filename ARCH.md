# OptArena - Architecture

_Last updated: 2026-08-03 (v0.1 branch)_

OptArena is a **local-first testing and comparison framework for AI coding
tools**. It runs the same task cases through real tools - a headless CLI
agent, an in-process agent-framework/SDK agent, or a raw model - against any
OpenAI/Ollama-compatible backend, records per-case metrics, and compares
scenarios side-by-side.

This document is the reference for how the system is put together: the domain
model, every component, the contracts between them, and how to extend it.

This is the `v0.1` branch, built around CLI, raw-API, and in-process
SDK/agent-framework drivers integrating against stable process/library
contracts.

---

## 1. Goals and non-goals

### Goals

1. **Same harness, every driver.** Headless CLI tools and in-process
   agent-framework SDKs run through the exact same case set, oracle, and
   comparison output, so tool-vs-tool numbers are apples-to-apples.
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

---

## 2. Repository layout

```
optarena/                       repo root
├── ARCH.md                     this document
├── README.md                   user-facing quick start
├── GOVERNANCE.md                maintainers, decision process, versioning/deprecation policy
├── CONTRIBUTING.md              setup, checks, adding a driver/case, PR flow
├── SECURITY.md                  vulnerability reporting + the real trust-boundary model
├── CODE_OF_CONDUCT.md
├── CHANGELOG.md                 Keep-a-Changelog format
├── pyproject.toml              packaging; console script `optarena`
├── .gitignore
├── optarena/                   ── the Python package (stdlib only) ──
│   ├── __init__.py
│   ├── __main__.py             python -m optarena
│   ├── cli/                    argparse CLI: run / compare / list / serve
│   │   ├── __init__.py           argparse wiring (main) + re-exports
│   │   ├── _run.py                run / compare / regression
│   │   ├── _cases_cmds.py         cases list/show/init/validate/verify/pack/install/trust-publisher
│   │   ├── _runs_cmds.py          runs show/rebuild-index/prune/scrub-secrets
│   │   ├── _doctor.py             preflight checks (drivers, sandbox, source-checkout assets, version drift)
│   │   ├── _sandbox_cmds.py       sandbox build/pull/status
│   │   ├── _serve.py              results dashboard HTTP server
│   │   └── _scan_report.py        scan / report (JUnit/HTML/SARIF)
│   ├── scenario.py             Scenario + Backend dataclasses (JSON files; Backend carries
│   │                            temperature/top_p/seed alongside kind/base_url/model/api_key)
│   ├── cases.py                case-engine facade (see _cases/ for the implementation)
│   ├── _cases/                 case loading + the filesystem oracle, split by concern
│   │   ├── _corpus.py             case loading/filtering
│   │   ├── _snapshot.py           workspace hashing + expected-file assertions
│   │   ├── _sandbox.py            container engine, DockerSandbox, check_command exec,
│   │   │                          workspace-quota watchdog (§3.1)
│   │   ├── _workspace_setup.py    setup_files/setup_repo/git_init/disruptions
│   │   ├── _evaluate.py           ties the assertion oracle + check_command together
│   │   ├── _mock_service.py       tool-use cases: in-process mock API + tool registry (§3.5)
│   │   ├── _tool_evaluate.py      tool-use cases: the call-log/final-state oracle (§3.5)
│   │   ├── _mcp_client.py         sandboxed-real: minimal MCP stdio JSON-RPC client (§3.6)
│   │   ├── _mcp_schema.py         sandboxed-real: MCP tools/list → OpenAI-function-schema (§3.6)
│   │   └── _sandboxed_mcp_service.py  sandboxed-real: SandboxedMCPService + per-service registry (§3.6)
│   ├── runner/                 executes one scenario → RunRecord
│   │   ├── _manifest.py           manifest-building (case hashes, ORACLE_VERSION, driver/
│   │   │                          provider versions, generation params, pack identity, build commit)
│   │   ├── _results.py            RunRecord, trial-merging, console formatting
│   │   └── _execution.py          case execution, --parallel worker pool, run_scenario,
│   │                              capability-exclusion + workspace-quota wiring per case
│   ├── metrics.py              aggregates + per-case deltas (raw, adjusted, and eligible pass rates)
│   ├── store.py                results persistence + index
│   ├── compare.py              A/B comparison + terminal table + common-eligible-case-set
│   ├── packs.py                case packs: build/sign/verify/install/trust (§3.4)
│   ├── security.py             secret/injection static scanning + redaction (§9.1)
│   ├── report.py               JUnit XML / self-contained HTML / SARIF report generation
│   ├── events.py               RunEvents - the print()/--json-events output abstraction
│   ├── verify.py               `optarena cases verify` - corpus self-verification (§10.2)
│   ├── cases/                  task catalogue (*.json) - NOT the same as _cases/ above
│   ├── pricing.py               USD cost estimation from token usage
│   └── drivers/                ── tool adapters ──
│       ├── __init__.py         registry (name → Driver, lazy imports)
│       ├── base.py             Driver interface + CaseResult + subprocess_env()
│       ├── openai_chat.py      raw-model baselines (OpenAI + Ollama protocol)
│       ├── tool_chat.py        raw tool-calling baselines (§3.5; OpenAI + Ollama protocol)
│       ├── aider_cli.py        aider CLI driver
│       ├── cli_agents.py       generic headless-CLI driver (Claude Code/Codex/OpenCode/Goose/Qwen Code)
│       ├── sdk_base.py         shared case loop (deadline, disruptions, telemetry) for all 6 SDK drivers
│       ├── crewai_sdk.py       optional SDK-agent driver
│       ├── openai_agents_sdk.py   optional SDK-agent driver (OpenAI Agents SDK)
│       ├── smolagents_sdk.py      optional SDK-agent driver (smolagents)
│       ├── langgraph_sdk.py       optional SDK-agent driver (LangGraph)
│       ├── autogen_sdk.py         optional SDK-agent driver (AutoGen/AG2)
│       └── semantic_kernel_sdk.py optional SDK-agent driver (Semantic Kernel)
├── dashboard/
│   └── index.html              static dashboard (fetches ../results/*)
├── docker/                     ── check_command sandbox images ──
│   ├── Dockerfile               base image (gcc + python3 + node) - DOCKER_IMAGE_DEFAULT
│   ├── python/Dockerfile         + fastapi/pydantic/uvicorn/flask/django
│   ├── node/Dockerfile           + express/react/babel (global, NODE_PATH-resolved)
│   ├── jvm/Dockerfile            + maven, ~/.m2 warmed with spring-boot-starter-*
│   ├── go/Dockerfile             + go, module cache warmed with gin
│   ├── rust/Dockerfile           + cargo, registry cache warmed with axum/tokio
│   ├── dotnet/Dockerfile         + dotnet SDK, NuGet cache warmed + offline.nuget.config
│   ├── check_vuln_baseline.py    Trivy-scan-vs-accepted-baseline gate (§9.2)
│   ├── check_images_lock.py      dependency-lock validation for all 9 images
│   ├── vuln-baseline/*.json      per-image accepted-vulnerability baselines
│   ├── mcp-filesystem/           sandboxed-real: real @modelcontextprotocol/server-filesystem (§3.6)
│   ├── mcp-git-repo/             sandboxed-real: real official mcp-server-git (§3.6)
│   ├── mcp-code-intel/           sandboxed-real: real mcp-language-server + pyright (§3.6)
│   ├── mcp-build-tools/          sandboxed-real: real official nx-mcp (§3.6)
│   ├── mcp-database/             sandboxed-real: real postgres-mcp + throwaway Postgres (§3.6)
│   ├── mcp-observability/        sandboxed-real: real grafana/mcp-grafana + throwaway Grafana (§3.6)
│   ├── mcp-package-registry/     sandboxed-real: real package-registry-mcp, real network (§3.6)
│   ├── mcp-cloud-infra/          sandboxed-real: real official awslabs.aws-iac-mcp-server (§3.6)
│   ├── mcp-docker/               sandboxed-real: real mcp-server-docker, docker-outside-of-docker (§3.6)
│   └── mcp-kubernetes/           sandboxed-real: real mcp-server-kubernetes + real kind cluster (§3.6)
├── .github/workflows/           CI, scheduled integration smoke, signed image publishing
│   ├── ci.yml                    unit tests + coverage, corpus verify, image-vuln-scan, wheel-smoke
│   ├── publish-images.yml        per-platform build → scan → combine → attest → sign → :latest (§9.2)
│   ├── integration-smoke.yml     weekly live CLI+SDK driver smoke against local Ollama
│   └── codeql.yml
├── scripts/
│   ├── embed_build_commit.py     writes the git commit into the wheel at build time (§3.3)
│   └── check_no_infra_errors.py  CI helper for integration-smoke.yml
├── repos/                       starter repos for setup_repo (L3) cases - source-checkout only
├── scenarios/                  example scenario JSON files
└── results/                    run records (gitignored)
    ├── runs/<run_id>.json
    ├── comparisons/*.json
    └── index.json
```

Pure Python, stdlib-only core; SDK drivers each pull in exactly one optional
framework package (`pip install optarena[<extra>]`), lazily imported so an
uninstalled framework never breaks the drivers you do have. `docker/`,
`repos/`, and `dashboard/` are source-checkout-only assets, not packaged into
the wheel - see README's "Source-checkout install only" note and
`optarena doctor`.

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
- **Benchmark-corpus metadata**:
  `framework`, `domain`, `difficulty` (1 easy .. 5 expert), `task_type`, `tags`
  are all optional, free-form fields alongside `language` - not validated
  against a fixed enum, purely descriptive. `framework` gets the same
  first-class filter as `language`: `cases.filter_cases(cases, language=,
  framework=)` ANDs both together, used by both `optarena run
  --language/--framework` and `optarena list cases --language/--framework`.
  The corpus is 836 cases spanning 18 languages/frameworks (Python,
  JavaScript/TypeScript, Java, Kotlin, Go, Rust, C#, C/C++, PHP, Ruby, SQL,
  Shell, YAML, Terraform, Dockerfile, Makefile) at the per-track allocation the spec's own
  Phase 1 table asks for - see §10.1 for the full breakdown and what's
  explicitly deferred beyond it.
- **`--tool-service`/`--tags`/`--like` (v0.2, CLI redesign)**: `language`/
  `framework` only ever covered the filesystem-oracle domain - the §3.5
  tool-use domain's 14 mock services (266 cases) had no selector at all
  beyond exact `--cases name,name` (you had to already know case names).
  Researched prior art before designing this (lm-evaluation-harness's
  `--tasks` group-expansion, BFCL's `--test-category`, pytest's `-k`
  substring vs. `-m` marker-expression split, Inspect AI's task-bundles-
  dataset+solver+scorer shape) and landed on the same "one small selection
  vocabulary reused everywhere" principle every one of them uses, instead
  of inventing flags ad hoc per subcommand the way `--language`/
  `--framework` had drifted into being duplicated three times (`run`,
  `cases list`, `cases verify`) with slightly different wiring each time.
  `cases.filter_cases()` gained three new keyword-only params, all
  optional and AND'd against the existing ones: `tool_service` (comma-
  separated, OR'd within itself - `--tool-service build_tools,
  observability`), `tags` (a pytest `-m`-style boolean expression -
  `--tags "tool-use and observability"` - over the case's free-form
  `tags` array, parsed by the new `_cases/_tag_expr.py` mini-language:
  `and`/`or`/`not`/parens, standard precedence, case-insensitive tag
  matching, a small hand-written recursive-descent parser rather than a
  dependency), and `like` (`-k`/`--like`, a case-insensitive substring
  match on the case name, pytest's `-k` by another name). All three are
  wired identically into `run`, `cases list`, and `cases verify` - the
  same three places `--language`/`--framework` already were - plus a new
  `optarena cases groups` command (mirroring `--tasks list`'s group
  discovery) that prints case counts per `tool_service`/`language`/`tags`
  value, so a filter can be aimed at something real without guessing.
  `TagExpressionError` (a `ValueError` subclass) is caught explicitly
  wherever `filter_cases` is now called directly outside `run`'s existing
  `_EXPECTED_RUN_ERRORS` catch (`cmd_list`, and `cmd_verify_corpus`'s
  previously-unguarded call to it, fixed in the same pass) so a malformed
  `--tags` expression reads as a clean one-line error (F-04), never a raw
  traceback. Deliberately NOT built in this pass, despite motivating the
  research: an `--environment`/`--matrix-environments` axis for a future
  live-MCP-server-A/B-testing capability (see
  `DEV_NOTES/LIVE_MCP_AB_TESTING_EXPLORATION.md`, not committed) - not
  decided, not scheduled, and adding an inert placeholder flag for an
  unbuilt feature would violate this project's own "don't design for
  hypothetical future requirements" rule. The naming was chosen so it
  won't collide with that if/when it lands: `--tool-service`/`--tags`/
  `--like` all narrow *which cases run*, orthogonal to *what a tool-use
  case's tools actually talk to*, which is what `--environment` would
  need to mean.
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
  behavior (compile it, run the real tests) - implemented once, in the
  Python oracle (`cases.py`), same SHA-1 content snapshots, sandbox
  hardening flags, diff stats, and failure classes for every driver.
- Optional per-case `test_setup_files`: `{relpath: content}`, written by
  `evaluate_case` *after* the driver's run (so the model never sees the
  tests it's graded against, unlike `setup_files`), just before
  `check_command` runs. This is how a case ships real test code
  (pytest-style asserts, a Node `assert` script, a Python harness that
  compiles-and-runs a C binary and checks its stdout) instead of relying on
  substring matching for correctness.
- **`check_command` execution is sandboxed in a container** (Docker or
  Podman - see `cases.container_engine()` below) when available, via
  **one shared container per distinct image needed by a run** (`cases.
  DockerSandbox`, keyed in the module-level `_active_sandboxes: dict[image,
  DockerSandbox]`) - not one container per check_command call, and not just
  one container overall. `runner.run_scenario()` computes `images_needed =
  {case.get("image") or DOCKER_IMAGE_DEFAULT for case in cases if
  case.get("check_command")}` and starts one `DockerSandbox` per image in
  that set, so a run mixing e.g. a Python case and a Go case gets both
  toolchains live at once, each bind-mounting the run's whole temp workspace
  root at `/workspace`. Every case's `check_command` resolves its own
  required image first (`case.get("image") or ... or
  DOCKER_IMAGE_DEFAULT`) and looks it up in `_active_sandboxes` before
  `exec`ing in; case/trial calls for the same image still share that
  one container (`-w /workspace/<case>/<trial-subdir>`), and every sandbox in
  the run is stopped in the `finally` block so cleanup happens even on error.
  This generalizes an earlier single-container fix - a fresh ephemeral
  `docker run --rm` per call once meant 7 cases x 3 trials = 21 containers
  for one run; the single shared container came first, multi-image support
  (for the benchmark-corpus expansion) came after.
  `DockerSandbox` uses `--network none`, `--memory 2g`, `--cpus 2`; each
  `exec` wraps its command in the container's own `timeout <N>s` so a
  hung test is killed inside its own process tree rather than needing the
  shared container itself removed. Falls back to one ephemeral `run
  --rm` per call (the pre-fix behavior) when `evaluate_case`/`run_check_command`
  is called with no matching active sandbox for that case's image (e.g.
  `evaluate_case` called directly, outside the runner), and to the host
  (one-time warning to stderr) when no container engine is reachable or
  `OPTARENA_DISABLE_SANDBOX=1` is set. `docker_image_available()` /
  `_docker_available()` cache their engine CLI probes for the process
  lifetime.
- **`container_engine()`** (`cases.py`) resolves which binary every one of
  the above calls actually shells out to: `OPTARENA_CONTAINER_ENGINE=docker`
  or `=podman` forces one, otherwise it auto-detects via `shutil.which`
  (docker preferred if both are on PATH), cached for the process. Podman
  implements the same CLI surface (`run`/`exec`/`pull`/`tag`/`stop`/`rm` and
  every hardening flag below) - verified directly against the published
  sandbox images, not just read off Podman's docs.
- **`DOCKER_IMAGES` registry** (`cases.py`) maps a short track name to its
  image tag: `base` (the original combined gcc+python3+node image, unchanged,
  still the default for cases with no `image`), plus `python`, `node`,
  `jvm`, `go`, `rust`, `dotnet` - one per benchmark-corpus track that needed a
  language/framework toolchain the base image doesn't have. `dockerfile_for
  (lang)` resolves the Dockerfile path by convention: `docker/Dockerfile` for
  `base`, `docker/<lang>/Dockerfile` for everything else. `optarena sandbox
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
- **Workspace disk/file quota.** The container's own storage-driver flags
  (`--storage-opt size=`) can't quota a bind mount at all - `/workspace` is
  always a host bind mount, never the container's own writable layer - so
  quota enforcement is host-side polling instead: `_WorkspaceQuotaWatchdog`
  (`_sandbox.py`) polls the workspace's real usage every 2s (default 4 GiB /
  50,000 files/directories, `OPTARENA_WORKSPACE_MAX_BYTES`/`_FILES`) for the
  duration of BOTH the driver/agent's own execution and the `check_command`
  call, killing the in-flight process/container on a breach where a kill
  handle exists (host exec, ephemeral container, shared-sandbox `reap()`) and
  always running one final synchronous check immediately after the call
  returns (closing the gap a poll interval alone would miss for a fast
  writer). A driver-phase breach has no generic kill handle across every
  driver kind, so it can't be stopped early there - but the result is never
  silently trusted: `CaseResult.extra["workspace_quota_exceeded"]` and a
  forced failure either way. A soft, best-effort ceiling, not a
  kernel-enforced one - portable across Windows/macOS/Linux and every
  execution path uniformly, which no storage-driver-specific alternative
  would be.
- **Trade-off of one shared container:** all cases/trials in a run share one
  network namespace (unlike the old per-call ephemeral containers, which
  each got a fresh one). A case that binds a fixed port across repeated
  trials (`create_server_c` binds `:8080`) can occasionally collide with a
  not-yet-released binding from a prior trial - observed live as a single
  `Bind failed: Address already in use` trial-3 failure in an otherwise
  3/3-passing run. This is a pre-existing class of test flakiness (identical
  to running repeated port-binding tests on a shared host), not specific to
  the container engine; case authors writing port-binding tests should prefer an ephemeral
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
  "name":    "aider-gemma12b",
  "driver":  "aider",
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
  "run_id": "20260702-094708_aider-selfopt",
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

Every `RunRecord` also carries a `manifest` (`runner/_manifest.py`): the
immutable identity of *what* a run measured, not just its outcome -
`oracle_version` (bumped only when a change to the oracle/sandbox contract
can flip a pass/fail verdict; see the constant's own docstring for the exact
decision procedure), `case_set_hash`, `trials`, `driver_version` and
`backend_provider_version` (best-effort, e.g. Ollama's `/api/version`),
`backend_temperature`/`top_p`/`seed` (recorded regardless of whether the
driver honors them - see §6.2's generation-parameter note), platform/Python
version, and `git_commit`/`git_dirty` (falls back to a build-time-embedded
commit - `scripts/embed_build_commit.py` - for a wheel install with no
`.git` directory). `compare.manifest_compatibility` hard-gates a comparison's
aggregate verdict on `(oracle_version, case_set_hash, trials)` matching;
driver/backend deliberately do NOT gate it, since tool-vs-tool and
backend-vs-backend comparisons are the whole point.

### 3.4 Case packs (`packs.py`)

A pack is one self-describing JSON file bundling a directory of cases with a
name, semantic version, and a sha256 content hash - `optarena cases pack`
builds one, `optarena cases install` (local file, or `https://` URL - plain
`http://` refused unless explicitly opted into) installs it to
`~/.optarena/packs/<name>@<version>/`, and `optarena run --pack <name>`
resolves it as `cases_dir`.

- **Integrity**: the content hash detects corruption/tampering in transit,
  checked on every load.
- **Authenticity**: `optarena cases pack --sign-key <ssh-key>` signs the
  pack's identity (name/version/hash/case_count) with `ssh-keygen -Y sign`
  (real Ed25519/RSA/ECDSA - stdlib-only, so this shells out rather than
  vendoring crypto, the same pattern the image-signing pipeline below uses).
  Verification (`verify_pack_signature`) always checks against the fixed
  `optarena-pack` namespace - never one the pack itself claims, which would
  let a signature made for an unrelated purpose verify here. A signer must
  be added to a local, explicit trusted-publisher keyring
  (`optarena cases trust-publisher`) before a pack from them is `trusted`;
  an unsigned or untrusted-signer REMOTE pack is refused unless
  `--allow-unsigned` is passed. A signature that's present but fails
  cryptographic verification (`tampered`) is refused unconditionally, even
  for a local file with `--allow-unsigned`.
- **Trust is re-derived every run, not cached at install time**:
  `verify_installed_pack` re-hashes the case files actually on disk each
  time a run resolves an installed pack, so editing a file after
  installation is caught (`tampered: True`) rather than the run's manifest
  replaying a stale "trusted" snapshot from install time.

### 3.5 Tool-use cases (`tool_service`, `openai-tools`/`ollama-tools`)

A second, parallel case domain alongside §3.1's filesystem oracle - same
`Case`/`Driver`/`CaseResult`/`Comparison` machinery, a different ground
truth. Where a coding case asks "did the right file end up with the right
content", a tool-use case asks "did the agent call the right tools, with the
right arguments, and avoid the wrong ones" - closer to what BFCL/tau-bench
evaluate for tool-calling agents, but authored as cheaply as a coding case
(one JSON file) and run through the same comparison/regression pipeline
everything else here uses. A case is one domain or the other, never both -
`tool_service` present means the coding oracle's fields
(`expected_files`/`check_command`) are absent and vice versa.

```json
{
  "name":        "tool_create_task",
  "tool_service": "task_tracker",
  "tools":        ["create_task", "complete_task", "list_tasks", "delete_task"],
  "prompts":      ["Create a new task titled 'Buy milk' and assign it to alice."],
  "expected_calls": [
    {"tool": "create_task", "arguments_contains": {"title": "Buy milk", "assignee": "alice"}}
  ],
  "expected_final_state": {"task_count": 1, "open_count": 1}
}
```

- **`tool_service`** names a registered mock service (`_cases/_mock_service.py`
  → `MOCK_SERVICES`); **`tools`** is which of that service's tools the model
  sees for this case (defaults to all of them - an explicit subset is how a
  case tests tool *selection*, e.g. exposing `create_task`/`delete_task`
  together and checking the model picks the right one). Adding a service is
  one class + one registry entry, the same shape as adding a driver or a
  sandbox track elsewhere in this doc.
- **The oracle** (`_cases/_tool_evaluate.py`, `evaluate_tool_case`) checks
  three independent things, all evaluated (not short-circuited, same as
  `check_expected`): every `expected_calls` entry matched at least one
  logged call (`arguments_contains` is a required *subset*, not an exact
  match - an extra legitimate argument doesn't fail a case); no
  `forbidden_calls` entry matched any logged call; every
  `expected_final_state` key/value matches the mock service's own
  `summary()`. Returns the same `(failures, oracle_info)` shape
  `evaluate_case` does, stashed into `CaseResult.extra["oracle"]`
  identically - the dashboard/CLI output code needed no changes.
- **A fresh mock-service instance per case run**, never shared across cases
  or trials - the same isolation a fresh workspace gives the filesystem
  oracle. `MockService.dispatch()` never raises: an unknown tool name or a
  tool call with wrong/missing arguments both become a normal
  `{"error": ...}` result (what a real function-calling loop would hand the
  model back), logged to `call_log` like any other call - a hallucinated or
  malformed call is data the oracle can assert against
  (`forbidden_calls`/`n_unknown_calls`), not a driver crash.
- **`tool_service_seed`** establishes state BEFORE the conversation starts -
  a git repo case needs real pre-existing history (a prior commit to diff
  against, a multi-commit log to search) that no tool in a git-only case
  can create (git has no "write file content" tool - see the `git_repo`
  bullet below), the same way a coding case's `setup_files` exist before
  the model's first turn. The driver calls `service.seed(case.get
  ("tool_service_seed", {}))` once, right after construction; each
  service defines its own spec shape (no shared schema across services,
  matching how tool arguments already aren't shared) and the calls it
  makes to build that state are never logged to `call_log` - the oracle
  only ever grades what the AGENT did, not test-fixture setup.
- **The driver loop** (`drivers/tool_chat.py`, shared by both variants -
  only the wire protocol differs, same split as `openai_chat.py`'s two
  baselines): send the conversation with `tools` attached → if the response
  has `tool_calls`, dispatch each against the mock service and append a
  `{"role": "tool", ...}` result message, loop → if the response has no
  tool_calls, the model is done with this prompt. Bounded by `max_tool_turns`
  (case-level override, default 6) so a model that never stops calling tools
  can't consume a case's whole timeout budget one turn at a time -
  `CaseResult.extra["hit_turn_limit"]` records when that ceiling was hit.
  Full call trajectory is recorded to `CaseResult.extra["tool_calls"]`,
  matching the "show your work, not just pass/fail" precedent §3.1's
  `extra["oracle"]` sets. Like the raw-model baselines (§6.2), this is a
  no-agent, no-file-tools driver (`file_tools: False` in the registry) -
  it measures the backend's own tool-calling behavior, not a framework's
  agent loop on top of it.
- **Why a mock-service oracle, not a live HTTP server in a sandbox**: the
  entire interaction (request → tool_call → dispatch → result) is
  driver-side Python, so an in-process object with a call log is a
  sufficient, dependency-free ground truth - no container, no network, no
  `check_command` needed for this domain. Matches the project's stdlib-only
  core the same way the filesystem oracle does.
- **`git_repo`** (`_cases/_mock_service.py`, `GitRepoService`) - the second
  mock service, covering all 18 tools catalogued from the real MCP git-
  server ecosystem in `DEV_NOTES/TOOL_CATALOG_COMPLETE.md` §1 (status, add,
  reset, commit, the three diff variants, log, show, branch listing/create/
  checkout, blame, remotes, tags list/create, push, pull), across 12 example
  cases. Deliberately scoped to coding/dev-specific tools rather than more
  generic CRUD services like `task_tracker` - see
  `DEV_NOTES/TOOL_USE_EXPANSION_PLAN.md` for why (BFCL/tau-bench already own
  generic tool-calling; git workflow judgment doesn't compete with anything
  else in the landscape). Two design points specific to this service:
  - **The working tree is fixed at `seed()` time and never changes during a
    case** - no git tool authors file content (that's a filesystem
    service's job, not built yet), so every one of these 18 tools is
    purely testing git WORKFLOW judgment (stage the right things, commit at
    the right granularity, branch before editing, pull before push,
    tag/blame correctly) - never content authorship.
  - **Assertions favor final repository state over exact call arguments**
    where the agent has a free choice the case can't predict (e.g. a
    feature-branch name) - `summary()` exposes `main_commit_count`
    specifically so a case can prove "the commit landed off main" without
    needing to know what the model named the branch it used to get there.
    `_tool_evaluate.py`'s matcher also does list-subset containment for
    list-valued arguments (`git_add`'s `paths`), not exact-list equality -
    a case asserting `{"paths": ["a.py"]}` shouldn't fail because the model
    reasonably staged `["a.py", "b.py"]` in one call.
  - **Live-verified finding, not a harness bug**: repeated live runs
    against a local model (`qwen3-coder:30b` via Ollama) surfaced a real,
    reproducible pattern - the model intermittently emits a tool call
    (`git_status`, `git_branch`, and - confirmed again while building the
    `filesystem` service below - argument-bearing calls like `write_file`
    too) as literal malformed text
    (`<function=git_status>\n</function>\n</tool_call>`, or the named-
    parameter variant `<function=write_file>\n<parameter=path>...`) instead
    of using Ollama's structured `tool_calls` field, on both `/api/chat` and
    `/v1/chat/completions`. Confirmed non-deterministic (retries flipped
    some of these to PASS) and confirmed NOT a case-design issue (every
    case's hand-built ideal trajectory passes, and a realistic wrong
    trajectory fails, per each service's own dry-run test coverage) -
    genuine benchmark signal about this backend's tool-calling reliability,
    left as-is rather than curve-fit around.
- **`filesystem`** (`_cases/_mock_service.py`, `FilesystemService`) - the
  third mock service, all 13 tools from the official MCP filesystem server
  (`DEV_NOTES/TOOL_CATALOG_COMPLETE.md` §2: read/read-media/read-multiple,
  write, edit, create-directory, list/list-with-sizes, move, search,
  directory-tree, get-info, list-allowed-directories), across 13 example
  cases. The inverse scope decision from `git_repo`: git tools never author
  content, so its working tree is fixed at seed time; filesystem tools ARE
  content authorship (`write_file`/`edit_file`/`move_file`/
  `create_directory` all actively mutate state mid-conversation), which is
  the entire point of this domain - does the agent read before answering,
  edit surgically instead of blind-overwriting, check existence before
  clobbering.
  - **`expected_final_state` gained nested-dict subset matching** (shared
    with `arguments_contains` via the same `_value_matches` helper) so a
    case can assert `{"files": {"config.py": "..."}}` and have it check
    only that ONE file's exact content, ignoring every other file the mock
    filesystem happens to also have - the same "only pin down what you
    actually care about" reasoning behind `git_repo`'s `main_commit_count`.
  - **A real case-design bug, caught live and fixed, not worked around**:
    the first live run failed two cases because the model reasonably wrote
    a path with a trailing slash (`"src/"`) where a case's
    `arguments_contains` required exactly `"src"` - semantically identical,
    failed for a reason that had nothing to do with agent correctness.
    Fixed by normalizing a trailing slash off `path`/`source`/`destination`
    arguments in `FilesystemService.dispatch()` itself, before logging -
    not by loosening the case's assertion, since the normalization is
    correct for every future case in this service too, not just the two
    that first exposed it.
- **`docker`** (`_cases/_mock_service.py`, `DockerService`) - the fourth
  mock service, 25 tools across containers/images/networks/volumes/system
  from the "comprehensive" tier catalogued in
  `DEV_NOTES/TOOL_CATALOG_COMPLETE.md` §4, across 15 example cases. Unlike
  `git_repo`/`filesystem`, several tools enforce real Docker-like
  PRECONDITIONS rather than always succeeding: `remove_container` refuses
  a running container (must `stop_container` first, or pass `force`),
  `remove_image`/`remove_volume` refuse while any container still
  references them, `create_container` refuses an image/volume that hasn't
  been pulled/built/created yet - "does the agent respect these
  preconditions instead of forcing past them" is the workflow-discipline
  skill this service is built to test, the same role `git_repo`'s "no
  commit without staging" plays there.
  - **A design gap caught during manual verification, not live testing**:
    the first implementation had no way for a container to actually mount
    a volume (`create_container` had no `volumes` parameter), which made
    `remove_volume`'s in-use refusal and `prune_volumes`'s discrimination
    permanently untestable dead code - a volume could never actually
    become "in use" through any real tool call. Fixed by adding a
    `volumes` parameter to `create_container` and wiring it into the same
    attachment tracking `connect_network`/`disconnect_network` already use
    for networks, before any case was written against it.
  - **Three distinct real findings from live verification against
    `qwen3-coder:30b`**, each root-caused individually rather than assumed
    to be the same issue: (1) the already-known malformed-tool-call-as-text
    quirk, reproduced again here; (2) a case that got valid, correctly-
    sequenced structured tool calls but with **hallucinated argument
    values** (`disconnect_network(network="app-network",
    container="app-container")` instead of the actual seeded `"bridge"`/
    `"app"`) - the model never called a discovery tool to check the real
    names first, it guessed plausible-sounding ones; (3) a case where the
    model talked itself out of using a tool that was right there
    (`tag_image`) with a confused explanation that the available tools
    "do not support the actual tagging operation" - a real, if surprising,
    capability gap (incorrectly concluding a needed tool doesn't exist),
    not a schema clarity problem (the tool's description directly says
    "give an existing image an additional tag"). All three are genuine
    benchmark signal, none are case-design bugs (every case's dry-run
    ideal/wrong-trajectory pair in `tests/test_docker_cases.py` behaves
    correctly).
- **`kubernetes`** (`_cases/_mock_service.py`, `KubernetesService`) - the
  fifth mock service, 23 of the 24 tools the reference
  `Flux159/mcp-server-kubernetes` implementation exposes
  (`DEV_NOTES/TOOL_CATALOG_COMPLETE.md` §5, an "~16+" estimate later
  confirmed low by reading the actual source tree this session): kubectl
  get/describe/create/apply/delete/logs/context/scale/patch/rollout,
  explain/list-api-resources, port-forward/stop-port-forward/exec, Helm
  install/upgrade/uninstall/template-apply/template-uninstall, pod
  cleanup, node cordon/drain/uncordon, and ping, across 19 example cases.
  `kubectl_generic` (an arbitrary kubectl command string with no fixed
  shape) is the one deliberate omission - mocking it honestly would mean
  either parsing arbitrary CLI syntax or silently no-op'ing it, neither of
  which tests anything, the same reasoning that keeps a raw-shell-exec
  tool out of `filesystem`/`docker`.
  - Shares `git_repo`/`docker`'s workflow-discipline skill under test, plus
    two dimensions neither prior service had: a `namespace` scopes nearly
    every call (so operating on the wrong namespace is itself gradable),
    and `kubectl_apply`/`helm_template_apply` UPSERT while
    `kubectl_create`/`install_helm_chart` REFUSE a duplicate - a real,
    sharp distinction an agent can get wrong by reaching for the
    non-idempotent tool a second time. Manifests/patches are modeled as
    explicit keyword fields (kind, name, namespace, replicas, image,
    labels) rather than a raw YAML/JSON blob, the same "explicit params
    over an opaque blob" choice `DockerService` made for
    `create_container`. Deleting a namespace refuses while it still
    contains resources or Helm releases, the same in-use precondition
    `remove_image`/`remove_volume` enforce, rather than a real-k8s-style
    silent cascade.
  - **A real mock-realism bug, caught live and fixed, not worked
    around**: the first live run showed a model investigating a failing
    pod call only `kubectl_describe`, never `kubectl_logs` - not a
    discipline lapse, `kubectl_describe`'s result included the pod's raw
    log lines, which no real `kubectl describe` ever surfaces (logs are
    streamed live from the kubelet, never part of the resource object,
    only `kubectl logs` returns them). Fixed by stripping `logs` from both
    `kubectl_get`'s and `kubectl_describe`'s output at the source, not by
    forcing the case to require a call that had become genuinely
    redundant.
  - **A real case-design bug, caught the same way**: a "deploy, then tear
    it down" case phrased the teardown as conditional on future user
    confirmation ("once I confirm it's done, remove it") - the model
    correctly waited for that confirmation rather than assuming it,
    replying "Let me know when you're ready to remove it." and stopping.
    Fixed by rewording the prompt to state the confirmation had already
    happened, the same pattern already used correctly in the
    port-forward-then-stop case.
  - **A new live-verified finding, not a bug**: given `kubectl_patch`'s
    schema description with an explicit flat-shape example
    (`{"replicas": 3, "image": "app:v2"}`), the model sometimes reached
    instead for the real Kubernetes Deployment patch shape
    (`{"spec": {"template": {"spec": {"containers": [{"image": ...}]}}}}`)
    from its own training knowledge - substituting real-world API
    structure for the tool's own documented (simplified) contract.
    Confirmed non-deterministic (retries produced the flat form too) and
    left as genuine benchmark signal, alongside a fourth reproduction of
    the already-known malformed-tool-call-as-text quirk.
- **`forge`** (`_cases/_mock_service.py`, `ForgeService`) - the sixth mock
  service and by far the largest, all 77 tools the official
  `github/github-mcp-server` exposes across its 17 toolsets
  (`DEV_NOTES/TOOL_CATALOG_COMPLETE.md` §3: Actions, Code Quality, Code
  Security, Context, Copilot, Dependabot, Discussions, Gists, Git, Issues,
  Labels, Notifications, Organizations, Projects, Pull Requests,
  Repositories, Secret Protection), across 47 example cases. Every
  repo-scoped tool takes explicit `owner`/`repo` params, same as the real
  server - "did the agent operate on the right repo" is itself gradable,
  the same role `namespace` plays for `KubernetesService`.
  - **Two sharp create-vs-update/refuse-vs-upsert distinctions drive most
    of the discipline testing here**: `issue_write`/`label_write`/
    `projects_write` create when their id param is omitted and update (or
    error on an unknown id) when it's given - a single tool covering both
    shapes, unlike `kubectl_create` vs `kubectl_apply`'s two-tool split.
    `merge_pull_request` refuses a draft, an already-merged/closed PR, or
    one whose most recent review is an unresolved REQUEST_CHANGES not yet
    superseded by an APPROVE. `pull_request_review_write` models GitHub's
    real two-shape review flow: no `event` starts/resumes a pending
    review that `add_comment_to_pending_review` can attach line comments
    to (refusing if none is open); a real `event` submits it (or creates
    one directly in one shot if none was pending) - the same workflow-
    discipline skill `git_repo`'s "no commit without staging" tests.
  - **Scope decision**: the same "explicit params over an opaque blob"
    choice `DockerService` made for `create_container` applies to every
    write tool here - manifests/file batches/patches are explicit keyword
    fields, not raw JSON blobs, so `expected_calls` can usefully assert
    against them. This catalog has no `kubectl_generic`-style catch-all
    tool to exclude.
  - **A real seeding-robustness bug, caught before any case was run
    live**: the first implementation required a case to seed `repos`
    explicitly even when it also seeded `issues`/`pull_requests`/etc. for
    that same repo, silently making every repo-scoped tool refuse with
    "no such repository" against state the case clearly intended to
    exist. Fixed by having every repo-scoped seed section (`files`,
    `branches`, `labels`, `tags`, `releases`, `commits`, `issues`,
    `pull_requests`, `discussions`, `actions_runs`, and all three alert
    kinds) auto-register a bare repo entry via a shared `_ensure_repo`
    helper - the same "referencing it is enough to seed it" convenience
    `DockerService`'s container seeding already gives images - rather
    than requiring every case author to remember a redundant `repos` key.
  - **A second real case-design bug, caught the same live run**: a
    "security posture review" case required calling both
    `list_code_scanning_alerts`/`list_dependabot_alerts`/
    `list_secret_scanning_alerts` AND the matching single-item
    `get_*_alert` tools - but this mock's list endpoints already return
    each alert's full detail (rule, severity, state), the same as GitHub's
    real list endpoints do, making a same-breath follow-up `get_*_alert`
    call genuinely redundant, not a discipline lapse. Fixed by dropping
    those three `get_*_alert` requirements from the case and adding a new
    one (`tool_forge_investigate_specific_alert`) where the alert numbers
    are already known up front (e.g. from a ping) - the realistic scenario
    where a direct single-item lookup, not a list-then-get chain, is the
    right call.
  - **A third real case-design bug, same run**: `tool_forge_sub_issue_
    breakdown`'s prompt never named a repo at all - the model reasonably
    invented placeholder-looking values (`github/example-repo`) rather
    than the seeded `acme/webapp`. Fixed by naming the repo explicitly in
    the prompt, the most basic instance of the same "give the agent what
    it needs to succeed" principle behind every other seeding/prompt fix
    in this domain.
  - Live-verified against `qwen3-coder:30b`: 39/46 on the run that
    surfaced the two bugs above; after fixing both, the remaining
    failures all trace to the already-known malformed-tool-call-as-text
    quirk (confirmed via direct replay: 2/3 retries reproduced it
    verbatim) plus one additional non-deterministic-but-benign pattern -
    a multi-step prompt ("trigger it, then check its status") where the
    model sometimes treats the first action as task-complete and offers
    to check status "later" rather than continuing immediately; 3/3
    direct retries completed both steps correctly, confirming genuine
    sampling variance rather than a systematic gap.
- **`package_registry`** (`_cases/_mock_service.py`, `PackageRegistryService`) -
  the seventh mock service, all 38 tools the real `npm-mcp`
  (`mikusnuz/npm-mcp`) reference implementation registers, extracted
  directly from its source this session rather than trusted from its
  README (`DEV_NOTES/TOOL_CATALOG_COMPLETE.md` §8 cited a lower "32
  tools" figure from the README; the actual `server.tool()` call count is
  38 - the same "the real count runs higher than the survey estimate"
  pattern already hit once for the whole catalog and again for
  kubernetes), across 35 example cases.
  - Single-project design, the same "one thing at a time" scope
    `git_repo`/`filesystem` use (not multi-repo like `kubernetes`/
    `forge`) - npm itself always operates against one project directory,
    so there's no `owner`/`repo`/`namespace` scoping concept here at all.
  - **Real npm preconditions enforced rather than always succeeding**: a
    package is only installable once the mock registry has at least one
    published version of it (installing something never published is a
    real 404, the same "must exist before you can act on it" precondition
    `create_container`'s image check enforces); `ci` refuses without a
    lockfile present (matches real `npm ci`); `publish` refuses a version
    that's already published (matches npm's real immutable-version rule -
    you can only deprecate or unpublish, never overwrite); `run-script`/
    `explain`/`uninstall`/`unpublish`/`deprecate`/`owner`/`dist-tag`/
    `view`/`bugs`/`repo`/`docs` all refuse a script/package/version that
    doesn't exist rather than silently no-op'ing.
  - Two of the real tool names are hyphenated (`dist-tag`, `run-script`) -
    invalid as Python identifiers, so `TOOLS` maps them to `dist_tag`/
    `run_script` methods explicitly rather than via the identity
    comprehension every other tool uses, the schema `name` field still
    carrying the real hyphenated string the model sees.
  - **A live-verified schema-ambiguity finding, fixed at the source**:
    `pkg`'s `value` field is deliberately untyped (a package.json field
    can hold a string, bool, number, object, or array) - a model
    reasonably sent the string `"true"` for a boolean-looking field, the
    same way real npm's CLI-style `pkg set field=value` takes it, not a
    literal JSON boolean. Fixed the same way `FilesystemService`'s
    trailing-slash issue was: an overridden `dispatch()` normalizes an
    exact `"true"`/`"false"` string in `pkg`'s `value` argument to a real
    boolean before logging, so the oracle sees a consistent
    representation regardless of which one a model sends.
  - **Two real case-design bugs, same live run, same fix pattern as
    forge's `sub_issue_breakdown`**: two publish-related cases seeded a
    project named `widget-lib` but never said so in the prompt, so the
    model reasonably invented a plausible name instead (`"my-project"` -
    likely just a common placeholder from training data, not anything
    derived from this mock). Fixed by having one case require checking
    the name via `pkg(operation="get", field="name")` first (the
    "discover it, don't guess" pattern already used elsewhere) and the
    other by naming the project directly in its prompt (the "already
    told directly" pattern `tool_forge_quick_copilot_assign` uses) - kept
    deliberately different so the two cases don't just duplicate the same
    lesson.
  - Live-verified against `qwen3-coder:30b` across two runs. The first
    (31/35, before the fixes above) surfaced the three findings just
    described plus one empty-call-log failure. After fixing all three, a
    second confirmatory run (30/35) showed every failure as an empty call
    log matching the already-known malformed-tool-call-as-text quirk
    (confirmed via direct replay at 2/3 retries - a sixth reproduction of
    the same finding first surfaced by `git_repo`) - no new findings.
- **`terraform`** (`_cases/_mock_service.py`, `TerraformService`) - the
  eighth mock service, all 55 tools the official `hashicorp/terraform-mcp-
  server` registers, extracted directly from its source this session
  rather than trusted from `DEV_NOTES/TOOL_CATALOG_COMPLETE.md`'s earlier
  "~10 tools" estimate - the same "the real count runs higher than the
  survey estimate" pattern hit for every category surveyed so far. Models
  a mock Terraform Cloud/Enterprise plus the public registry: orgs,
  projects, teams, workspaces, workspace/variable-set variables, policy
  sets, runs/plans/applies, state versions, stacks, no-code workspaces,
  Sentinel mocks, and both public and private registry search across
  providers/modules/policies, across 31 example cases.
  - Real workflow-discipline preconditions enforced: `create_run` refuses
    a locked workspace and locks it on success; `action_run("apply")`
    refuses a run not in a plannable-to-apply state, records a new state
    version, and unlocks the workspace (discard/cancel also unlock);
    `delete_workspace_safely` refuses a locked workspace;
    `force_unlock_workspace` refuses one that isn't locked; `delete_project`
    refuses while workspaces still reference it - the same "must satisfy
    the real precondition, not just exist" discipline every other service
    in this domain enforces.
  - **A new class of case-design bug, first seen in this service**: unlike
    prior services where the human-facing identifier IS the API identifier
    (a docker image tag, a k8s object name), Terraform Cloud workspaces/
    variable-sets/policy-sets have opaque IDs distinct from their
    human-readable names. ~17 of the first 31 cases seeded these with IDs
    that differed from the prompted name with no reliable discovery path,
    so a model reasonably used the name where an ID was required - not a
    discipline lapse, a fair case-design bug. Fixed by a consistent rule
    applied across every affected case: seeded `id` equals `name`
    wherever ID-discovery isn't the case's own teaching point; only kept
    distinct IDs (requiring a `list_*` call first) when that discovery
    genuinely is the lesson.
  - **A search-realism bug, same live run**: `search_providers`/
    `search_modules`/`search_policies`/`search_private_modules`/
    `search_private_providers` used naive whole-string substring matching
    (`query in target`), which fails for a natural multi-word model query
    ("tag enforcement", "internal platform") against a single hyphenated
    registry key (`hashicorp/require-tags`). Fixed via a shared
    `_matches_query()` helper that tokenizes the query and matches if any
    word is a substring of the target, both lowercased - a single reusable
    fix rather than five one-off patches.
  - **A redundant-confirmation-call bug, same fix pattern as forge's
    alert-lookup finding**: `create_and_tag_workspace` required a
    follow-up `read_workspace_tags` after `create_workspace_tags`, whose
    own response already shows the current tag set. Fixed by dropping the
    redundant requirement.
  - **A genuine model-behavior finding, left as signal, not a bug**:
    `tool_tf_inspect_stack` has the model call `list_stacks` (which
    reveals the correct id `stack-1` in its result) and then call
    `get_stack_details` with the human NAME (`platform-stack`) anyway,
    ignoring the id it was just shown - reproduced identically across two
    live runs. Left as-is: a real instance of a model conflating a
    human-readable field with an opaque-ID field even when the correct id
    is visible in a prior tool result in the same trajectory.
  - Live-verified against `qwen3-coder:30b` across two runs. The first
    (14/31, before the id/search fixes above) surfaced the systemic
    id-vs-name bug; after fixing it plus the search-tokenization and
    redundant-confirmation bugs, a second run reached 29/31, with the two
    remaining failures individually root-caused: one an over-tight query
    assertion (loosened), the other the `inspect_stack` finding described
    above (kept as genuine signal).
- **`database`** (`_cases/_mock_service.py`, `DatabaseService`) - the
  ninth mock service, all 9 tools the real `crystaldba/postgres-mcp`
  ("Postgres MCP Pro") registers, extracted directly from its source this
  session (`DEV_NOTES/TOOL_CATALOG_COMPLETE.md`'s "~5-8 core tools"
  estimate undercounted by one, the same pattern hit for every other
  category surveyed). Models a single mock PostgreSQL instance: schema/
  object introspection, `EXPLAIN` (with optional hypothetical indexes),
  raw SQL execution, workload- and query-level index recommendations,
  general health checks, and top-query reporting, across 8 example cases.
  - **The one interesting design decision this mock exists to test**:
    `execute_sql` is gated by a whole-session `access_mode`
    ("unrestricted" or "restricted", matching the real server's
    `SafeSqlDriver`) that no other tool exposes a way to discover in
    advance - in restricted mode, write statements (`insert`, `update`,
    `delete`, `drop`, `alter`, `create`, `truncate`, `grant`, `revoke`)
    are refused. Unlike every other precondition in this domain, there's
    nothing for an agent to discover first, so this isn't modeled as a
    `tool_db_*` case; it's a correctness property of the service itself,
    covered by direct unit tests instead (`tests/test_database_cases.py`).
  - `explain_query` refuses combining `analyze` with `hypothetical_indexes`
    (a real `EXPLAIN ANALYZE` actually executes the query, which makes no
    sense against indexes that don't exist); `analyze_query_indexes`
    refuses an empty or >10-item query list.
  - Live-verified against `qwen3-coder:30b`: 7/8 passed; the one failure
    (`tool_db_inspect_table`) is the already-known malformed-tool-call-
    as-text quirk (confirmed via direct replay, reproduced 4/5 times on
    this single-tool case) - no case-design bugs surfaced, the smallest,
    most self-contained service built this session.
- **`ci_pipeline`** (`_cases/_mock_service.py`, `CIPipelineService`) - the
  tenth mock service, all 13 tools the official `CircleCI-Public/
  mcp-server-circleci` registers (`CCI_TOOLS`/`CCI_HANDLERS`), extracted
  directly from its source. Jenkins was the originally planned category
  for this slot, but no Jenkins MCP server has real traction (best is 29
  stars, most are single digits) - the same "one authoritative real
  implementation" rule that picked `forge` (official GitHub server) and
  `terraform` (official HashiCorp server) picked CircleCI's official,
  vendor-published server instead once the research turned up the gap.
  Models a mock CircleCI instance: followed projects, pipeline status,
  build failure logs, test results (with pass/fail filtering), flaky
  tests, artifacts, config validation, pipeline triggers, workflow
  reruns, component rollbacks, component-version discovery, and usage-API
  reporting, across 13 example cases.
  - **The real design decision this mock exists to test**: most
    project-scoped tools accept THREE mutually exclusive ways to
    identify a project - `projectSlug`+`branch`, a `projectURL` to parse,
    or `workspaceRoot`+`gitRemoteURL`(+`branch`) for local-checkout
    detection - and the real server's own docs recommend calling
    `list_followed_projects` first to get the exact slug. Unlike
    Terraform Cloud's workspaces, none of these are opaque IDs, so the
    discipline being tested is "supplied a complete identification
    method", not "used the right identifier" - a deliberately different
    precondition shape than `terraform`'s.
  - Real preconditions enforced: `run_pipeline` refuses (and lists the
    options) when a project has multiple pipeline definitions and no
    `pipelineChoiceName` was given; `run_rollback_pipeline` refuses a
    project with no rollback pipeline configured;
    `find_underused_resource_classes` refuses a CSV path that was never
    produced by `download_usage_api_data`; `list_component_versions`
    progressively discloses environments, then components, then versions,
    only when the narrower ID isn't yet supplied.
  - **A genuine, 100%-reproducible model-behavior finding, left as
    signal, not a bug**: `tool_ci_explore_component_versions` gives
    `list_component_versions` a real discovery path (call it with just
    `projectSlug` to list environments, then add `environmentID` to list
    components, then add `componentID` for versions) for two opaque IDs
    the prompt never states (`env-prod`, `comp-fe` - only their
    human-readable names "production"/"frontend" appear in the prompt).
    The model never used the discovery path at all: in 5/5 runs
    (the original live pass plus 4 direct retries) it went straight to
    `list_component_versions` with `environmentID="production"`,
    `componentID="frontend"` - guessing the human names directly into
    the ID fields on the very first call. A cleaner, more deterministic
    instance of the same conflation `terraform`'s `inspect_stack` finding
    surfaced (there, the model ignored a correct ID it had just been
    shown; here, it never asks for the ID at all).
  - Live-verified against `qwen3-coder:30b`: 12/13 passed, the one
    failure being the `explore_component_versions` finding above.
- **`build_tools`** (`_cases/_mock_service.py`, `BuildToolsService`) - the
  eleventh mock service, all 13 tools the official `nrwl/nx-console`'s
  bundled `nx-mcp` server registers (the real constants in its
  `tool-names.ts`), extracted directly from its source. No build-tool
  ecosystem surveyed this session (Gradle, Maven, Bazel, Cargo, CMake,
  Python packaging, Composer, NuGet, Go modules, sbt) had an official or
  genuinely dominant real implementation - the closest was an unofficial
  56-star Gradle server. Nx was the one outlier: official (Nrwl is the
  company behind Nx), 1,409 stars on the server's parent repo (29,200 on
  Nx itself) - two orders of magnitude more adopted than anything else
  found (`DEV_NOTES/MCP_IMPLEMENTATION_GAPS.md` has the full survey).
  Models a mock Nx workspace plus Nx Cloud: docs search, plugin listing,
  project-graph/nx.json introspection, per-project configuration and
  dependencies, generator discovery and schemas, project/task-graph
  visualization, running-task monitoring, and Nx Cloud CI pipeline
  status/logs/self-healing-fix management, across 13 example cases.
  - **A genuinely different shape of "build tool" than every other real
    implementation surveyed**: Nx's real tool surface skews toward
    monorepo workspace *introspection* (what exists, how it's
    configured, what's currently running) and Nx Cloud's CI
    self-healing, not toward directly triggering a build/test/publish
    the way Gradle's or npm's tools do - there is no `run_build`-style
    tool in the real server at all, and the mock deliberately doesn't
    invent one.
  - Real preconditions enforced: `nx_project_details`/`nx_generator_schema`
    refuse an unknown project/generator; `nx_visualize_graph` enforces
    real type-dependent required parameters straight from the source
    (`project` needs `projectName`; `project-task` needs both
    `projectName` and `taskName`; `full-project-graph` needs neither);
    `update_self_healing_fix` resolves a fix via `aiFixId`, `shortLink`,
    or `branch` (defaulting to the current branch) and refuses if none
    resolve - the same "identify via any of several channels" precondition
    shape `ci_pipeline`'s project identification uses, applied to a
    single tool instead of many.
  - **A live-verified case-design bug, fixed at the source**: the real
    `ci_information`'s `branch` parameter is designed to be *omitted* -
    the real server auto-detects the current git branch locally when
    it's not given. A case's prompt said "my CI run on this branch
    failed" without naming the branch, and a model has no way to know
    what "current branch" means without being told (unlike the real
    server, which can inspect the actual local checkout) - so in 5/5 runs
    it guessed the common default `"main"` instead of omitting the
    parameter, a real precondition miss traced to the case never giving
    the model what it needed to succeed. Fixed by naming the branch
    explicitly in the prompt, the same "already told directly" pattern
    used for forge's and package_registry's similar naming bugs.
  - Live-verified against `qwen3-coder:30b` across two runs. First
    (11/13, before the fix) surfaced the finding above plus one
    empty-call-log failure; after the fix, a second confirmatory run
    reached 13/13 - the empty-call-log failure did not reproduce on the
    second run and was independently confirmed via direct replay (4/5
    total across both runs and retries) as the already-known
    malformed-tool-call-as-text quirk, not a new finding.
- **`code_intel`** (`_cases/_mock_service.py`, `CodeIntelService`) - the
  twelfth mock service, all 6 tools the real `isaacphi/mcp-language-server`
  (1,572 stars, by far the dominant real implementation surveyed this
  session - next best found was 192) actually registers in its
  `tools.go`, not the earlier catalog survey's "~4 core tools" estimate
  (undercounted by 2, the same pattern hit for every category surveyed).
  Two more tools (`get_codelens`, `execute_codelens`) exist in the source
  but are commented out and never registered - correctly excluded here.
  Models a mock language server: symbol definition/reference lookup,
  file diagnostics, position-based hover info, symbol rename (with
  cross-file reference updates), and line-range text edits, across 9
  example cases.
  - **A structural difference from every other service in this domain**:
    the real server has no file-reading tool of its own at all -
    `hover`/`rename_symbol`/`edit_file` all take a `line`/`column` the
    calling agent is expected to already know from separate file-reading
    (normally the client's own file tools, out of scope for this
    single-service-per-case domain - see
    `DEV_NOTES/TOOL_USE_EXPANSION_PLAN.md` §4's still-open cross-service
    question). Cases therefore state the relevant file/line directly in
    the prompt, the same "already told directly" pattern used elsewhere
    when discovery isn't the case's own teaching point.
  - Real preconditions enforced: `definition`/`references` refuse an
    unknown symbol; `diagnostics`/`edit_file` refuse an unknown file;
    `hover`/`rename_symbol` refuse a position with no known symbol;
    `edit_file` refuses an out-of-range line edit.
  - Live-verified against `qwen3-coder:30b`: 5/9 passed, all four
    failures independently confirmed via direct replay (3/3) as the
    already-known malformed-tool-call-as-text quirk - no case-design
    bugs found. Notably higher failure rate than every other service
    built this session; the failures cluster on the tools with the
    shortest, simplest single-string-argument schemas (`definition`,
    `hover`), suggesting (not confirmed) the quirk may correlate with
    schema simplicity rather than being uniformly random across tools -
    left as an open observation, not chased further.
- **`observability`** (`_cases/_mock_service.py`, `ObservabilityService`) -
  the thirteenth mock service and by far the largest, all 105 unique
  tools the official `grafana/mcp-grafana` (3,332 stars) actually
  registers across its 30 tool-category source files - bigger than
  `forge`'s 77, and confirmed via a bulk local clone + systematic
  file-by-file extraction rather than a quick grep, given the scale.
  Six tools (`alerting_manage_rules`, `agento11y_manage_evaluators`,
  `agento11y_manage_eval_rules`, `agento11y_manage_eval_collections`,
  `grafana_api_request`, `generate_deeplink`) are registered twice in the
  real server under the same name - a read-only vs. read-write variant
  selected at startup by an `enableWriteTools` flag - and this mock
  models the full read-write variant of each, the same "model the
  complete capability" choice every other service in this domain makes.
  Models a mock Grafana instance: dashboards, alerting, datasources,
  annotations, folders, snapshots, plugins, provisioning, incidents,
  on-call, Sift investigations, admin/RBAC, assertions, navigation,
  rendering, config generation, panel-query execution, a generic API
  passthrough, Agent Observability, the Assistant transport, and query
  connectors for Prometheus/Loki/Pyroscope/Elasticsearch/InfluxDB/
  Graphite/Quickwit/CloudWatch/Athena/ClickHouse/Snowflake, across 37
  example cases.
  - **A deliberate, user-confirmed scope decision before building**: no
    build-tool-style single winner exists here - Grafana genuinely is
    this large. Presented the choice directly rather than silently
    picking a subset: ship the full 105-tool real surface (matching
    forge's "mirror it completely" precedent), or scope down to a
    curated core. Chose the former.
  - **A deliberate two-tier depth design, new to this service**: roughly
    22 "interactive" categories get real, source-verified precondition
    logic (dashboard update's mutual-exclusion between full-JSON and
    JSON-patch modes; datasource create/update's two-step schema-review
    confirmation flow; alert-rule operations' per-operation required
    fields; annotation creation's format-dependent required-text switch;
    snapshot creation's paired-field requirement for external storage;
    plugin install's two-step version-confirmation flow; provisioning's
    path-traversal-style slug/path validation; Sift's UUID validation;
    deeplink/panel-image generation's XOR between a stored dashboard and
    a provisioning preview; Agent Observability's operation-gated
    required fields across six sub-tools). The other ~12 categories are
    all the same underlying skill repeated across vendors - "query this
    specific datasource type" - and share the one real precondition
    every one of them enforces in the actual source: the resolved
    datasource must actually be of the expected plugin type, or the
    call is refused (`query_prometheus` against a Loki datasource,
    `query_cloudwatch` against a Prometheus datasource, etc.), plus each
    tool's own specific required-field checks (PromQL range queries need
    a step size, Pyroscope tools need a strictly-ordered time range).
  - **Extraction method, given the scale**: a background research agent
    read all 34 real category source files from a local shallow clone
    and produced a structured tool-by-tool extraction (name, real
    description, full parameter list, and source-verified precondition
    notes) before any mock code was written - the same "extract from
    actual registrations, not README estimates" discipline used
    throughout this session, scaled up for a service an order of
    magnitude larger than anything built before it. Schemas were then
    generated programmatically from the mock's own real Python method
    signatures rather than hand-transcribed, guaranteeing the advertised
    schema can never drift from the dispatch logic that actually
    enforces it.
  - Live-verified against `qwen3-coder:30b`: 36/37 passed on the first
    run - an unusually high pass rate for a first live pass in this
    domain, and the best first-run result of any service built this
    session. The one failure (`tool_obs_check_agent_catalog`) was
    confirmed via direct replay (4/4 retries passed) as genuine sampling
    variance, not a case-design bug: the model reliably first attempts
    an invalid `operation: "read"` (a reasonable but wrong guess at the
    real enum, which correctly refuses `list`/`get`/
    `list_versions`/`list_version_scores` only) and then usually
    self-corrects to `get`, but on the original run it recovered via
    `list`+`name_prefix` instead - a different, non-matching resolution
    path. No case fix was needed.
- **`cloud_infra`** (`_cases/_mock_service.py`, `CloudInfraService`) - the
  fourteenth and final mock service built this session, all 9 tools the
  official `awslabs/aws-iac-mcp-server` registers (8 static `@mcp.tool()`
  functions plus one dynamically-proxied `read_iac_documentation_page`,
  wired up from a remote AWS knowledge endpoint at server startup -
  extracted directly from `server.py`). Models a mock AWS
  Infrastructure-as-Code assistant: CloudFormation template validation
  and compliance checking, deployment troubleshooting, pre-deploy
  validation guidance, CDK/CloudFormation documentation and code-sample
  search, CDK best practices, and full-page documentation reads, across
  9 example cases.
  - **AWS's real MCP landscape breaks the pattern every other category
    this session fit**: not one server but ~59 separate ones under one
    `awslabs/mcp` monorepo, with no single dominant implementation.
    Three real candidates were checked: `aws-api-mcp-server` (a thin
    ~3-tool generic AWS-CLI-string passthrough - `call_aws`,
    `suggest_aws_commands`, `get_execution_plan` - a poor fit for this
    domain's structured-tool methodology); `ccapi-mcp-server` (rich
    resource CRUD across 1,100+ AWS resource types with genuine
    token-enforced workflow security - explain before create, deletion
    double-confirmation, IAM wildcard-policy blocking - exactly the
    precondition-rich design this domain favors, but explicitly marked
    deprecated in its own source in favor of the one below); and
    `aws-iac-mcp-server` (the current, actively-maintained official
    replacement, CloudFormation/CDK authoring-and-validation focused
    rather than live resource management). Also checked and ruled out:
    the "most official" `aws/agent-toolkit-for-aws` (2,255 stars, the
    true `aws` org) turned out to be a thin wrapper around a
    closed-source hosted MCP endpoint - its actual tool implementations
    aren't inspectable the way every other service's source has been
    this session, so it couldn't be source-verified at all.
  - **A deliberate, user-confirmed scope decision**: presented the
    tradeoff directly - ship the thinner-but-current `aws-iac-mcp-server`
    (consistent with never having picked a deprecated implementation as
    "the" real one anywhere else this session), or ship the richer but
    deprecated `ccapi-mcp-server` anyway for its precondition depth. The
    user chose the current implementation.
  - Real preconditions enforced: `validate_cloudformation_template`
    refuses malformed JSON or a template missing its `Resources` section
    and flags resources with an invalid/missing `Type`;
    `check_cloudformation_template_compliance` flags publicly-accessible
    resources and wildcard-`Action`/wildcard-`Resource` `Allow` IAM
    policy statements - real, if simplified, analogues of the actual
    server's cfn-lint/cfn-guard checks; `troubleshoot_cloudformation_
    deployment` refuses a stack that was never seeded (matching every
    other "must exist before you can act on it" precondition in this
    domain); `search_cdk_samples_and_constructs` refuses an unsupported
    language; `read_iac_documentation_page` refuses an unseeded URL,
    with a case (`tool_cloud_search_then_read_full_page`) mirroring
    Terraform's "search, then follow up on a specific result" pattern.
  - Live-verified against `qwen3-coder:30b`: 8/9 passed. The one failure
    (`tool_cloud_validate_template`) was confirmed via direct replay
    (2/4 retries reproduced it) as the already-known malformed-tool-
    call-as-text quirk - non-deterministic, not a case-design bug.
- **What's explicitly deferred, not attempted**: only `openai-tools`/
  `ollama-tools` (raw baselines) drive tool-use cases today - no CLI/SDK
  agent driver has a tool-calling code path yet (they all write files, not
  call functions - §6's `file_tools` split), so an agent-vs-baseline
  comparison isn't possible in this domain yet, only backend-vs-backend and
  model-vs-model. `optarena cases verify --strict` (§10.2) doesn't cover
  this domain either - there's no `reference_solution`/`broken_solutions`
  equivalent for a tool-calling trajectory yet, so the discriminating-oracle
  guarantee is enforced by unit tests (`tests/test_tool_use_cases.py`,
  `tests/test_git_repo_cases.py`, `tests/test_filesystem_cases.py`,
  `tests/test_docker_cases.py`, `tests/test_kubernetes_cases.py`,
  `tests/test_forge_cases.py`, `tests/test_package_registry_cases.py`)
  today, not by a corpus-wide verify command.
  A case can only name ONE
  `tool_service` - a realistic workflow spanning two services (e.g. list a
  directory, then commit what's found with git) isn't expressible yet; see
  `DEV_NOTES/TOOL_USE_EXPANSION_PLAN.md` §4 for the design question this
  raises, deliberately left open rather than decided
  ahead of actually needing it.

### 3.6 Sandboxed-real execution (`--tool-service-mode sandboxed`)

§3.5's mock services are hand-written approximations - safe and fast, but
still approximations, maintained separately from the real reference
implementations they mirror. Sandboxed-real mode runs the ACTUAL open-source
MCP server binary inside a disposable, hardened container instead: same
safety guarantee as a mock (isolated, no real credentials, no third-party
blast radius for most services - see the two explicit exceptions below), the
real server's real behavior instead of an approximation of it.

- **`SandboxedMCPService`** (`_cases/_sandboxed_mcp_service.py`) satisfies
  `MockService`'s exact contract (`dispatch`/`call_log`/`seed`/`summary`),
  so `tool_chat.py`'s driver loop and `_tool_evaluate.py`'s oracle work
  against it completely unmodified - the only new branch point is
  `run_case()` resolving which kind of service a case gets. `dispatch()` is
  a full override (there's no per-tool Python method here, every call
  funnels through one live `MCPStdioClient.call_tool()`), but still logs to
  `call_log` in the identical shape and never raises.
- **`MCPStdioClient`** (`_cases/_mcp_client.py`) is a minimal MCP stdio
  JSON-RPC client - newline-delimited messages per the spec, a background
  reader thread feeding a queue so `_recv` can bound-wait without relying on
  `select` on pipes (unreliable on Windows). A JSON-RPC protocol error and a
  tool-execution error (`isError: true`) both collapse to the same
  `{"error": ...}` shape `MockService.dispatch()` already uses, so nothing
  downstream needs to tell them apart.
- **`DockerSandbox.exec_attached()`** (`_sandbox.py`) is the one new
  container-lifecycle capability this needed: every existing sandbox
  operation (`exec()`, used by `check_command`) is one-shot - spawn, capture
  output, return. An MCP server is a long-lived process you hold a live
  stdio session with, so `exec_attached` does `<engine> exec -i` with
  attached (not captured) pipes and no `timeout` wrapper (the MCP client
  owns per-request timeouts; this method only owns process lifecycle).
  Tracked separately from `check_command`'s shared-container pool -
  sandboxed tool-use cases get ONE DEDICATED container each (via
  `build_sandboxed_service()`), never shared, since mixing a long-lived
  attached process into the pool that `--parallel` workers also `exec` into
  would let a hung MCP server collide with an unrelated case's compile-and-
  test run.
- **Mode selection**: sandboxed execution is granted by the USER
  (`--tool-service-mode sandboxed` or the scenario field), never by case
  content - a case's own `tool_service_mode` may opt itself DOWN to mock
  (it knows it doesn't work sandboxed) but can never opt UP (S-2 in the
  tool-call audit: case packs install from URLs; content that can
  self-escalate into container launches - and for docker/kubernetes, the
  host Docker socket - inverts the project's fail-closed posture). This
  deliberately breaks symmetry with `image`'s case-wins precedence:
  `image` selects among equally-trusted local images, this field changes
  what the run may touch. `cases.resolve_tool_service_mode` is the one
  shared rule - `tool_chat.run_case` (what happens) and the run manifest
  (what's recorded) both call it, so they can never disagree; a case that
  asked for sandboxed and was downgraded gets
  `extra["sandboxed_downgraded"]` rather than a silent substitution. The
  manifest records `tool_service_modes`; `compare.manifest_compatibility`
  treats a mismatch the same as a different oracle version or case set - a
  mock-mode and a sandboxed-mode run are never silently compared as
  equivalent.
- **Schema-drift pre-flight**: `diff_case_tools_against_live()` checks a
  case's `tools`/`expected_calls`/`forbidden_calls` tool names against the
  real server's live `tools/list` before the conversation loop starts -
  turned into a clean `CaseResult.error`, not a confusing "every
  expected_calls assertion just happens to fail." Name drift isn't the
  drift that bites, though (git_repo's real server keeps every overlapping
  NAME but adds a required `repo_path` argument to all of them, confirmed
  live) - so two argument-level checks sit alongside it, and measuring
  them against the real corpus sharply separated their value:
  `diff_case_asserted_arguments()` reports an `arguments_contains` key the
  real tool doesn't accept, which can NEVER match and so blocks the case
  exactly like a missing tool; `diff_case_required_arguments()` reports
  arguments the real server newly requires, which turns out to predict
  nothing (subset matching means extra arguments are ignored, and the
  model is shown the real schema) and is therefore advisory - it fires on
  all 12 git_repo tools while breaking zero cases, whereas the asserted-
  argument check found the single real breakage in the whole corpus. The
  result also records `extra["mcp_server_info"]`/`["mcp_protocol_version"]`
  - what the real server actually negotiated, so a future genuine protocol
  incompatibility is a recorded fact, not a mystery hang. `optarena cases
  verify --sandboxed` runs all of this as a batch, no-model-call
  pre-flight (one sandbox per DISTINCT `tool_service` among matched cases,
  not per case) - still not the discriminating-oracle proof §3.5's "what's
  explicitly deferred" note says this domain lacks. Its throwaway probe
  directory goes through `PROBE_SETUP` first for the services whose real
  server won't start against an empty one (`git_repo` needs a real repo,
  `build_tools` a real workspace) - a real run never needs this, it has a
  real case workspace. Each service's summary line reports
  sandboxed-readiness directly (`8/12 case(s) sandboxed-ready, 4
  blocked`), and blocked cases fail the command while advisory warnings
  don't.
- **Sandboxed-readiness of the shipped corpus**, measured rather than
  assumed - `filesystem` 13/13, `git_repo` 8/12, `build_tools` 5/13. What
  blocks the rest is worth stating precisely, because the two causes have
  opposite implications:
  - **Real capability gaps** (all 4 git_repo, all 8 build_tools):
    the official `mcp-server-git` registers no blame/push/pull/remotes/tag
    tools at all, so those cases are unrunnable sandboxed by construction,
    not by a fixable corpus defect - they remain valid mock-mode cases
    testing real git workflow judgment. `build_tools` is subtler and the
    mock is NOT wrong: `nx_visualize_graph`/`ci_*`/`update_self_healing_fix`
    all exist in the real `nx-mcp` binary (confirmed by grepping it) but
    register only under an IDE context or a live Nx Cloud connection, so
    "absent" here means "not registered in this sandbox's configuration".
  - **One genuine mock fidelity bug**, found by this diff and fixed: the
    mock's `git_add` took `paths` where the real official server takes
    `files` - a straight violation of this domain's own stated principle
    (mirror the reference implementation's ACTUAL registrations). The mock
    schema now advertises `files` (with `paths` kept as a silent alias so
    out-of-tree cases don't break), which moved a case from blocked to
    ready. `GitRepoService` also now accepts-and-ignores `repo_path` on
    every tool: the real server requires it (one server, many repos), this
    mock models one, and a model that correctly supplies it must not be
    punished with an invalid-arguments error for being right.
- **Containment and teardown, hardened by audit** (the tool-call audit's
  S-1/S-4/B-1/B-2, each verified live before fixing):
  - `tool_service_seed`'s `files`/`media_files`/`directories` paths get
    the same `reject_unsafe_relpath` schema gate as every other
    case-controlled path field, plus a resolved-containment re-check in
    `SandboxedMCPService._contained()` at write time - in this mode those
    paths are REAL host disk writes, and a `..`/absolute path would
    otherwise escape (pathlib's `/` operator replaces the base outright on
    an absolute right-hand side).
  - The sandbox bind-mounts the CASE directory itself, never its parent -
    the parent is the shared run root (other cases' workspaces, hidden
    test files), and containment must come from the mount, not from
    trusting the third-party server's own path checks.
  - `exec_attached` drains the server's stderr continuously into a bounded
    tail (a server writing more than the OS pipe buffer to an undrained
    pipe blocks forever - kind's cluster-create progress goes through
    exactly that pipe); the tail is appended to handshake-failure errors,
    which is also where every real startup failure this cycle actually
    needed diagnosing.
  - Teardown is layered for state that outlives the container BY DESIGN:
    `close()` sends stdin EOF, waits `shutdown_grace` for the server's own
    graceful exit (kubernetes' in-container `kind delete cluster` trap runs
    here), stops the container, then runs a host-side reaper -
    `docker rm -f` on the kind node container, whose name is derivable
    because the cluster is named after the sandbox itself (passed in via
    `exec_attached`'s env support). `docker stop` signals only PID 1,
    never exec'd scripts, so the trap alone provably leaks privileged kind
    nodes on non-graceful paths - observed, not theorized.
- **Two narrow, per-image hardening exceptions** - `DockerSandbox` gained
  `extra_run_args`/`network` constructor params (default `None`/`"none"`,
  every pre-existing caller unaffected) rather than loosening the shared
  `_HARDENING_ARGS` globally:
  - **`database`**: real Postgres refuses outright to run as uid 0 (no flag
    overrides this), so its startup script must `su postgres` -
    `CAP_SETUID`/`CAP_SETGID`, neither in the default cap set (confirmed
    empirically: `su: cannot set groups: Operation not permitted` without
    it).
  - **`package_registry`**: the real server (`package-registry-mcp`) has no
    offline-registry config option at all - real network access
    (`network: "bridge"`), judged acceptable because its entire tool
    surface, confirmed from its own tool list, is read-only public-package
    metadata lookups (search/get-details/list-versions) with no publish/
    delete tool at all.
  - **`docker`/`kubernetes`**: docker-outside-of-docker (a mounted HOST
    `docker.sock`), not a nested/privileged inner daemon - `docker`'s real
    MCP server (`ckreiling/mcp-server-docker`) and `kubernetes`'s (a real,
    throwaway `kind` cluster whose node containers are created as SIBLINGS
    on the host daemon) both talk to the ACTUAL host Docker daemon. The
    effective principal is the MODEL UNDER TEST - its tool calls flow
    unfiltered through the real server to the real daemon
    (`create_container` with volume binds reaches host root; kind nodes
    are themselves privileged containers, and the real kubernetes server
    exposes `exec_in_pod`/`kubectl_generic`) - which suspends the
    harness's usual "the backend may be arbitrarily bad" premise. So
    these two services require `OPTARENA_ALLOW_HOST_DOCKER=1` on top of
    `--tool-service-mode sandboxed` (S-3 in the tool-call audit; the same
    fail-closed pattern as `OPTARENA_ALLOW_UNSAFE_HOST_EXEC`), enforced
    in `build_sandboxed_service` via the registry's `host_docker_socket`
    flag - and a registry tripwire test asserts the flag and the actual
    socket mount can never disagree. This trust boundary is live-verified,
    not theoretical: `docker`'s own `list_containers` call returns the
    sandbox's OWN container. Chosen over full privileged DinD after
    explicit user sign-off; `kubernetes` additionally needs
    `network: "bridge"` (Docker refuses `network connect` on a container
    started in `--network none` mode at all) and a longer
    initial-handshake timeout (`kind create cluster` alone runs 30-40s+,
    confirmed empirically, before the MCP server has even started
    responding - every other service's real server starts in low single
    digit seconds or instantly, which the client's default timeout was
    tuned against).
- **Ten of the fourteen §3.5 mock services have a proven sandboxed-real
  counterpart** - `filesystem`, `git_repo`, `code_intel`, `build_tools`,
  `database`, `observability`, `package_registry`, `cloud_infra`, `docker`,
  `kubernetes` - each built against its real, currently-published reference
  server and live-verified end to end against actual Docker (real
  container start, real handshake, real tool calls, clean teardown - not
  simulated). `task_tracker` has no real reference implementation to sandbox
  (stays mock-only, nothing to build). `forge`/`ci_pipeline`/`terraform` are
  inherently third-party SaaS (GitHub, CircleCI, Terraform Cloud) with no
  disposable local substitute - they belong to a separate, not-yet-built
  live/bring-your-own-server mode (point at a real, already-running external
  server with the user's own credentials), not this sandboxed one.
- **Real, live-verified schema drift** between each mock and its real
  counterpart, not assumed: `git_repo` (12 real tools vs 18 mock - every
  real tool additionally requires a `repo_path` argument the mock never
  modeled, a required-argument mismatch the current drift check doesn't
  catch, only missing tool NAMES), `observability` (65 real vs 105 mock -
  the mock significantly over-approximates the real OSS server's surface),
  `build_tools` (7 real vs 13 mock - `nx-mcp` defaults `--minimal=true`,
  hiding most workspace-analysis tools unless explicitly disabled),
  `docker` (14/19 tool-name overlap), `kubernetes` (20/23), `cloud_infra`
  (8/9, the closest match found, and fully offline - no LocalStack needed
  at all, contrary to the original plan's assumption), `database` (9/9
  exact), `code_intel` (6/6 exact), `filesystem` (14 real vs 13 mock - the
  real server additionally has `read_file`). `package_registry` is total
  drift (0/38 tool-name overlap) - the mock models an npm-CLI-command-style
  server (`install`/`publish`/`ci`/...); no credible real MCP server of
  that shape exists in the current ecosystem, only registry-metadata-lookup
  servers (search/get-details/list-versions) - itself a real, useful
  finding about what's actually published, not a gap in this search.

#### Agent-mode tool-use (a real agent as the MCP client)

§3.5/§3.6 grade tool-use cases by having the HARNESS be the MCP client -
`tool_chat.py` drives the request loop and records every `tools/call` into
`call_log` as it makes it. That answers "how good is this model at tool
calling" but not "does putting a real agent in front of it help", because a
real agent (Claude Code, goose) is itself the MCP client: the harness is no
longer in that conversation and has nothing to grade.

- **A transparent logging proxy** (`_cases/_mcp_proxy.py`) is what closes
  that gap. The agent is told to launch it as its MCP server; it execs the
  REAL server inside the already-running sandbox container (a second,
  independent `docker exec -i` session) and relays every message verbatim,
  while parsing `tools/call` request/response pairs into a JSONL log.
  `_cases/_agent_tool_use.py` reads that back after the agent exits and
  rebuilds the same `{"tool", "arguments", "result"}` shape
  `MockService`/`SandboxedMCPService` produce - so `evaluate_tool_case`
  runs completely unmodified regardless of which side made the calls.
- **Logging happens BEFORE forwarding**, in both directions, and the
  ordering is load-bearing rather than stylistic: forwarding first loses
  calls to a race that was observed intermittently against real goose (the
  same case grading PASS or "made no calls" run to run). Server->agent, the
  agent can exit and take the proxy down the instant it has its final
  response, before a queued write happens; agent->server, the response can
  arrive before the request's own correlation entry is recorded. A unit
  test pins the ordering, and was checked to actually fail against the old
  one.
- **Sandboxed-only, structurally.** The 14 mock services are in-process
  Python objects satisfying an internal `dispatch()` contract, not MCP
  endpoints - an external agent process cannot connect to one at all, so
  `--tool-call` implies `tool_service_mode: sandboxed` and a case that
  resolves to `mock` is refused with that reason rather than silently
  producing an empty log the oracle would grade as "made no calls".
- **Only agents with verified headless MCP-client isolation qualify** -
  `CLI_AGENTS[...]["mcp_client"]`, today `claude-code` (`--mcp-config
  <file> --strict-mcp-config`) and `goose` (`--with-extension <cmd>
  --no-profile`), both confirmed against the installed binaries' own
  `--help`, not from documentation. Both flag sets were chosen for the same
  property: they point the agent at exactly ONE server while ignoring the
  user's own configured MCP servers entirely, so the model sees only what
  the case granted. `optarena agent --tool-call` refuses any other driver
  by name and lists the ones that work.
- **The tool namespace the model sees is part of the measurement.** goose
  derives its extension name (and therefore the `<ext>__<tool>` names the
  model is shown) from the launch command's binary - invoked directly,
  every tool reached the model as `python_exe__list_allowed_directories`,
  observed live. A generated launcher named after the service fixes this to
  `filesystem__...`. The oracle was never affected (the proxy logs the raw
  MCP tool name, not the agent's alias), but a model shown a nonsense
  namespace may call tools less readily, which would have scored as a worse
  MODEL where the real cause was our own plumbing.
- **Known limitation, deliberately not papered over**:
  `expected_final_state` can only be graded as far as `{"n_calls": N}`.
  That assertion inspects a `MockService`'s own `summary()` - domain
  business logic ("does this folder now exist") with no generic
  reconstruction from an observed call log against a real server. A case
  relying on more will report an honest oracle mismatch, never a silent
  skip or false pass.


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
  `pass_rate_trials`, `durations_s`). A driver with expensive per-scenario
  startup can opt out by running all cases inside `prepare()` and setting
  `caches_results` to serve them from `run_case()`.
- `--parallel N` - cases fan out over N worker threads for drivers marked
  `parallel_safe` (baselines, CLI agents, SDK agents - see each driver's
  `parallel_safe` flag).

### Driver lifecycle contract

- `prepare()` / `teardown()` bracket the whole scenario; most drivers use
  `prepare()` only to check the tool/package is installed and raise early if
  not.
- `run_case()` must never raise for tool-level failure - it returns a
  CaseResult with `error` set. Raising is reserved for "the experiment cannot
  proceed at all" (missing binary, package not installed).
- Drivers spawning subprocesses build the child environment via
  `subprocess_env()` (`drivers/base.py`) - an explicit allowlist, not the
  full parent environment, so a scenario's backend key/config never leaks
  host secrets into an untrusted agent subprocess (§8.2).

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
original content (the pre-run snapshot stores a per-file content hash, not
the bytes themselves, so the case's setup text is the best available
reference) - an edit that
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

Name → class with lazy imports so optional dependencies (crewai,
openai-agents, smolagents, langgraph, autogen, semantic-kernel) don't tax
everyone. Adding a driver = one module + one registry entry. Each entry
carries metadata surfaced by `optarena list drivers`:

- `kind`: `cli | sdk | baseline`
- `backend`: `scenario` (obeys the scenario backend; backend-vs-backend is
  valid) or `fixed` (own account/provider; tool-vs-tool only)
- `status`: `stable | experimental | optional`

The headless terminal agents (Claude Code, Codex, OpenCode, Goose, Qwen Code)
share one generic driver (`drivers/cli_agents.py`) specialized by per-tool
descriptors - binary name, prompt/auto-approve flags, backend-injection env.
The six SDK-agent drivers share one *pattern* (§6.3) but not one module, since
each framework's agent-construction API differs.

| Driver | Status | Mechanism |
|---|---|---|
| `openai-chat` | stable | `POST /v1/chat/completions`; driver writes extracted code block |
| `ollama-chat` | stable | `POST /api/chat` (Ollama native); same convention |
| `openai-tools` | experimental | `POST /v1/chat/completions` with `tools`; real tool-call loop against a mock service (§3.5) |
| `ollama-tools` | experimental | `POST /api/chat` with `tools` (Ollama native); same loop, native tool_calls format |
| `aider` | stable | `aider --message … --yes --no-git` per prompt, cwd=workspace |
| `claude-code` | experimental | `claude -p`, `--output-format json` |
| `codex` | experimental | `codex exec --full-auto` |
| `opencode` | experimental | `opencode run` |
| `goose` | experimental | `goose run -t` |
| `qwen-code` | experimental | `qwen -p` |
| `crewai` | optional | crewAI SDK, single coder agent, LLM → backend |
| `openai-agents` | optional | OpenAI Agents SDK, single `Agent` + `OpenAIChatCompletionsModel` |
| `smolagents` | optional | smolagents `ToolCallingAgent` + `OpenAIServerModel` |
| `langgraph` | optional | LangGraph `create_react_agent` + `ChatOpenAI` |
| `autogen` | optional | AutoGen/AG2 `AssistantAgent` + `OpenAIChatCompletionClient` |
| `semantic-kernel` | optional | Semantic Kernel `ChatCompletionAgent` + `OpenAIChatCompletion` |

### 6.2 The raw-model baselines

`openai-chat` / `ollama-chat` ask the model for exactly one fenced code block
and write it to the first `expected_files` path themselves. They are the
**control group**: any agent tool's value-add (planning, file ops, retries,
context) is measured as the delta against this baseline on the same backend.
They also enable *model vs model* and *endpoint vs endpoint* comparisons with
no tool in the loop. Multi-turn cases feed the current file content back into
the next prompt.

**Generation parameters.** `Backend.temperature`/`top_p`/`seed` are honored
by both raw-model baselines (in the actual request body/`options`) and all
six SDK drivers below (each confirmed to forward them into its underlying
completion call - `crewai.LLM`, `ChatOpenAI`, `agents.ModelSettings`,
autogen's `create_args`, semantic-kernel's execution settings, smolagents'
`OpenAIServerModel` kwargs). CLI-agent drivers (aider, Claude Code, Codex,
OpenCode, Goose, Qwen Code) are external binaries with their own sampling
settings and no per-request override this harness controls - the fields are
still recorded in every run's manifest regardless of driver, since "what was
configured" is worth knowing even for a driver that doesn't act on it.

### 6.3 SDK-agent drivers

All six (`crewai_sdk.py`, `openai_agents_sdk.py`, `smolagents_sdk.py`,
`langgraph_sdk.py`, `autogen_sdk.py`, `semantic_kernel_sdk.py`) follow one
shape, established by `crewai_sdk.py` and repeated deliberately rather than
factored into a shared base class (each framework's agent-construction API
differs enough - some sync, some async, different constructor shapes - that a
shared abstraction would be thinner than just reading five short files):

1. **No file/shell tools are given to the agent.** Each is a single
   agent/LLM object pointed at the scenario backend's OpenAI-compatible
   endpoint (`backend.openai_base`, `backend.api_key`, `backend.model`) and
   instructed to reply with exactly one fenced code block containing the
   complete file - the driver parses that block
   (`_CODE_BLOCK = re.compile(r"```(?:\w+[^\n]*)?\n(.*?)```", re.DOTALL)`)
   and writes it to `concrete_target(...)` itself. This measures the SDK's
   own orchestration/prompting stack on top of the backend, not a
   tool-using agent - the same "baseline caveat" as the raw-model drivers
   (§6.2): multi-file cases are effectively agent-only.
2. **Same oracle plumbing as every other driver**: `prepare_workspace`,
   `snapshot`, `changed_files`, `evaluate_case` from `cases.py` - a case
   passes or fails by the identical filesystem oracle regardless of which
   framework produced the file.
3. **Async frameworks (AutoGen, Semantic Kernel) are driven via
   `asyncio.run()`** inside the synchronous `run_case()`, with an explicit
   `await client.close()` in a `finally` block - without it, the
   framework's `AsyncOpenAI`/httpx client tries to close its connections
   after `asyncio.run()` has already torn down the event loop, raising
   `RuntimeError: Event loop is closed` on interpreter exit (a real bug hit
   and fixed while building the Semantic Kernel driver).
4. **Local/proxied models aren't in a framework's built-in capability
   table** (only hosted-provider model names are), so drivers that validate
   against one must supply it explicitly - e.g. AutoGen's
   `OpenAIChatCompletionClient(..., model_info={"vision": False,
   "function_calling": False, "json_output": False, "family": "unknown",
   "structured_output": False})`, all `False`/`unknown` because no tools are
   given to the agent in the first place.

Each driver's `prepare()` does an `import <package>` and raises a clear
`RuntimeError` naming the missing pip extra if it's not installed, so a
scenario naming an uninstalled SDK driver fails fast with actionable text
rather than an ImportError traceback.

---

## 7. Results store & dashboard

### 7.1 Store (`store.py`)

Flat JSON files; `index.json` (newest-first digest of every run) is rebuilt on
each save so consumers never parse all runs. Run references in the CLI accept
an id, filename, path, or a substring that matches exactly one run (an
ambiguous substring is refused with the candidate list - silently picking
the newest was how the wrong baseline got compared).

### 7.2 Dashboard (`dashboard/index.html`)

One static file, vanilla JS, no build step, no external requests. Served by
`optarena serve` - a scoped stdlib `http.server` bound to `127.0.0.1` that
serves **only** `dashboard/` and the results directory (never the repository
root), with directory listings disabled, so `/dashboard/` and `/results/`
share an origin - or any static server you point at those two directories.

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

### 8.1 Windows specifics

- `tempfile.mkstemp` returns an **open** fd - close it before any later
  `unlink` (WinError 32).
- Always pass `encoding="utf-8", errors="replace"` to subprocesses; agent CLI
  tools emit UTF-8 that cp1252 consoles cannot decode.
- Console prints stick to ASCII-safe glyphs (cp1252 lacks `→`, `⚠`, `…`).

### 8.2 `subprocess_env()` allowlist matching must be case-insensitive

`drivers/base.py`'s `subprocess_env()` builds a minimal, explicit child
environment from an allowlist (plus an optional passthrough set) rather than
forwarding the whole parent environment - deliberate, so a scenario's
backend key never leaks host secrets into an untrusted agent subprocess. On
this machine `os.environ` surfaces `SYSTEMROOT`/`WINDIR`/`COMSPEC` in
**uppercase** (inherited via Git Bash/MSYS2), not the `SystemRoot`/`windir`/
`ComSpec` casing Python docs and CPython examples conventionally use. An
allowlist/passthrough match done case-sensitively silently dropped
`SystemRoot` - which Node needs on Windows to locate `bcrypt.dll` during its
crypto init (`ncrypto::CSPRNG`). Missing it isn't a clean error: Node
hard-crashes (SIGABRT / exit 134) deep inside native code, which looked like
a broken driver rather than a missing env var. Fixed by uppercasing both
sides of the comparison in `subprocess_env()`; any driver spawning a
subprocess is exposed to this class of bug if it adds its own passthrough
list, so new passthrough names should be added to the allowlist rather than
compared ad hoc.

---

## 9. Security & privacy

Everything is local: prompts and generated code go only to the backend URL in
the scenario; results are local JSON; the dashboard server binds `127.0.0.1`.
No telemetry. Subprocess-based drivers (CLI agents) build the child
environment via `subprocess_env()`'s explicit allowlist (§8.2), not the full
parent environment, so a scenario's backend key never leaks host secrets into
an untrusted agent subprocess. See SECURITY.md for the complete trust-boundary
write-up (what a case-defined `check_command` can and can't touch, the
container hardening flags, the host-execution confirmation gate for CLI
agents) and how to report a vulnerability.

### 9.1 Secret/injection scanning and redaction (`security.py`)

`optarena scan`/`--security-scan` statically scans changed files for
hardcoded secrets and common injection patterns (`os.system`, `shell=True`,
SQL f-strings, etc.) per-language, feeding `optarena report --format sarif`
for GitHub code-scanning integration. `redact_secret_patterns()` is applied
before anything is persisted (saved run records, indexes, stderr, exception
text, reports) - including a whole-block redaction for multiline PEM private
keys specifically (a single-line, header-only pattern can't remove a key
body spanning many lines; `scan_text()`'s own line-by-line finding-detection
and the whole-text redaction path are deliberately two different code paths
for this reason).

### 9.2 Vulnerability gate and signed image publishing

`docker/check_vuln_baseline.py` compares a fresh Trivy scan against a
per-image checked-in baseline (`docker/vuln-baseline/<image>.json`) of
already-triaged findings, keyed on `(target, package type, package,
installed version, CVE ID)` - a version bump, a newly-available fix, a
severity increase, or a Trivy disposition-status change toward
"affected"/"fixed" all invalidate an old acceptance and fail the gate, even
though the rest of the key still matches. `ci.yml` runs this as a blocking
PR check; `publish-images.yml` runs the same script as the actual release
gate: each platform (`linux/amd64`, `linux/arm64`) is built, pushed, and
scanned **independently** (a combined multi-arch scan silently resolves to
only the scanner's native platform - confirmed live, not assumed), both
must pass before the two digests are combined into one real multi-arch
manifest, which is then attested (`actions/attest-build-provenance`) and
signed (`cosign sign`, keyless via Sigstore/Fulcio) - and only THEN is
`:latest` promoted to it, so a signing/attestation failure never leaves
`:latest` pointing at a digest the pipeline didn't finish vetting.

---

## 10. Roadmap

Near-term:
- **More CLI/SDK drivers** - OpenHands, Copilot CLI when automatable.
  `opencode`/`goose`/`qwen-code`/`codex` descriptors shipped (experimental);
  all six planned SDK-agent frameworks (crewAI, OpenAI Agents SDK,
  smolagents, LangGraph, AutoGen, Semantic Kernel) shipped as of this branch.
- **IDE UI automation** - not part of this branch; still a possible future
  driver category (VS Code/JetBrains extension automation) alongside the
  CLI/raw-API/SDK drivers this branch ships.
- **Remaining driver telemetry** - `aider` and `claude-code` report real
  token/cost (and `claude-code` turns) as of §10.2; `opencode`/`goose`/
  `qwen-code`/`codex` still report duration only pending a `parse_metrics`
  hook per tool (same descriptor pattern, see `cli_agents.py`).
- **Dashboard matrix grid** - `--matrix-drivers`/`--matrix-models` (below)
  print a terminal matrix; the dashboard still renders pairs only.
- **LLM-judge scoring** - deliberately deferred; would dilute the filesystem
  oracle's tool-neutral credibility. Opt-in only if ever added, never the
  default pass/fail signal.

Done (moved out of "near-term" as of the dates noted):
- **Tool-use cases** (2026-08-06) - a second case domain (§3.5): a mock-
  service oracle and two new baseline drivers (`openai-tools`/`ollama-tools`)
  for grading tool-calling correctness instead of file output. One service
  (`task_tracker`) and five example cases ship; agent-driver support (not
  just the raw baselines) and a corpus-verification story for this domain
  are the natural next steps, not yet started.
- **Richer oracles** (2026-07-04) - `test_setup_files` + `check_command`,
  container-sandboxed, real compile/run/assert instead of content-pattern-only.
- **Run matrix** (2026-07-04, terminal only) - `--matrix-drivers`/
  `--matrix-models` expand to N scenarios with a compact pass-rate/time/
  tokens table; no dashboard grid yet (see above).
- **Parallelism** (2026-07-04) - `--parallel N` fans baselines/CLI drivers
  out over a thread pool.
- **Corpus self-verification** (2026-07-08, §10.2) - `optarena verify-corpus`
  replays reference/broken solutions through the real oracle; wired into CI.

Structural:
- Publish to PyPI (`pip install optarena`). Not implemented.
- Per-project case packs (`optarena init` scaffolding a local `cases/`) - done.
- Optional SQLite index if run counts outgrow index.json (schema unchanged).
  Not implemented; `index.json` has been sufficient at current run volumes.
- Sandbox images published to GHCR (§10.2) - done; PyPI publish is the
  remaining "make it installable without cloning" gap.

### 10.1 Benchmark corpus status

The corpus stands at **836 cases** across 18 languages/frameworks (the
original 120-case Phase 1 allocation has since been expanded through several
later bands). Every case was verified end-to-end before being counted, run
through the real oracle (`evaluate_case`/`DockerSandbox`), not just claimed.
All 836 ship an explicit `reference_solution` (proven to PASS), and every one
also ships something proven to FAIL - an explicit `broken_solutions` variant,
or the implicit "unmodified workspace" check that bug_fix/refactoring/
performance/security cases receive. `optarena cases verify --strict` fails
the build if a case is ever added without a discriminating variant, so this
is a standing invariant, not a one-time count.

**Built:**
- Case schema extended with `framework`/`domain`/`difficulty`/`task_type`/
  `tags`/`image` (§3.1), plus a `--framework` filter mirroring
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
- 836 cases across all 18 language tracks, each tagged with the spec's task
  categories (feature/bug_fix/refactoring/testing/security/performance/
  devops/data_engineering/documentation/dependency_upgrade) and difficulty 1-3.
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

(Registry publishing of the sandbox images - listed as deferred above through
2026-07-04 - shipped 2026-07-08; see §10.2.)

### 10.2 The 0.1 cut (2026-07-08): corpus integrity, driver telemetry, GHCR

A second audit pass (post-120-case corpus) found and fixed a class of bug
distinct from §10.1's: not "the corpus is too small" but "the oracle's
pass/fail boundary was wrong" - a Spring port collision across 7 cases
sharing one container, three .NET cases whose `app.csproj` glob-compiled
`tests/*.cs` into the app itself (no correct solution could ever pass), a
C# perf budget the *unoptimized* code beat outright, and two over-constrained
content patterns that rejected valid solutions. None were visible from
reading the case JSON; all surfaced only by running a known-good and a
known-bad solution through the real oracle. That protocol is now a command,
not a memory:

**`optarena verify-corpus`** - two new optional case-schema keys:
`reference_solution` (a correct solution; must PASS the full container-sandboxed
oracle) and `broken_solutions` (deliberately wrong variants; each must FAIL
it). `bug_fix`/`refactoring`/`performance`/`security` cases also auto-check
that an *unmodified* workspace fails, without needing an explicit broken
variant. 16 cases backfilled with proven solutions from the audit; the
first full corpus run (91 variants / 77 cases) caught three of its own
backfilled references failing their case's shape check, and separately
surfaced a genuine `__pycache__` staleness race in the mutation-testing
runners added alongside the 13 `add_tests_*` cases: a same-byte-length
Python mutant written within the same filesystem-mtime second as the
original ran the *stale compiled bytecode* of the unmodified source, so the
mutant was silently never exercised. Fixed by clearing `__pycache__` before
every mutation-runner test invocation; re-verified clean 5/5 under repeated
runs before landing. Wired into CI (`.github/workflows/ci.yml`) as a gate
on every push to `main`/`v0.1` and every pull request.

**Driver telemetry.** Per-case cost/token reporting previously existed only
for the two raw-model baselines; every agent driver reported duration only,
which silently degrades any cost-based comparison ("agent vs no-agent",
"which tool is cheapest") back to a timing comparison. `aider` now parses
its own `Tokens: ... sent, ... received. Cost: $... session.` report lines
(handling both the `4.5k` and `4,500` formats it prints across versions);
`claude-code` now runs with `--output-format json` and reports usage tokens
(including cache-read tokens separately), `total_cost_usd`, and `num_turns`
from the structured result object. A generic `parse_metrics` hook was added
to the `CLI_AGENTS` descriptor shape in `cli_agents.py` so the remaining
CLI drivers (`opencode`/`goose`/`qwen-code`/`codex`) can adopt the same
pattern without new plumbing - not done in this pass (see §10 roadmap).

**Comparison trust.** `--trials N` computed a majority verdict but never
surfaced *how* unanimous it was; `optarena compare`'s table now shows a
`PASS 2/3` marker per case for trial runs, `optarena regression` lists
non-unanimous cases by name under a `flaky_cases` block, and both
`aggregate()` outputs gained `p95_duration_s` alongside the existing
mean/median.

**GHCR.** The six per-language sandbox images (~7GB combined) previously
had to be built locally (~30 min cold). They now publish to
`ghcr.io/trysti-labs/optarena/optarena-tester-*:latest` via
`.github/workflows/publish-images.yml` on every `docker/**` change; the
runtime's `DockerSandbox.start()`/`run_check_command()` paths call a new
`ensure_image()` that pulls-and-tags a missing image before falling back to
"build it yourself" guidance (`OPTARENA_DISABLE_PULL=1` opts out; capped at 5
minutes since this is a first-run convenience, not a build step - a slow or
unreachable registry must not stall a whole `optarena run`). `optarena
docker pull [--lang X|--all]` exists for explicit prefetch. One related
robustness fix: the local-image availability probe (`docker image inspect`)
is retried once before falling through to a pull attempt, since Docker
Desktop's resource-saver wake-up made the first probe time out on an image
that was, in fact, already present - misreading that as "missing" would
have triggered a pointless network pull every time.

### 10.3 August 2026 security/product remediation (pre-`v0.1.0` release hardening)

Several review rounds (an internal pass, then two independent external
reviews of the live source, each re-verified against real code/infra rather
than taken on trust) found and closed a real security/product-readiness gap
list before the `v0.1.0` release tag. Highlights, each verified live against
real infrastructure, not just reviewed:

- **Git-controlled command execution, host-path escapes, and Windows
  process-tree leaks** (the original P0 set) - closed; 23 regression tests.
- **Publish pipeline**: the vulnerability scan is now structurally in the
  same dependency chain as publication (§9.2), each platform scanned
  independently, `:latest` promoted only after signing/attestation succeed.
- **Vulnerability baseline**: matching identity bound to installed
  version/target/package-type, a newly-available fix or severity/status
  increase invalidates an old acceptance (§9.2).
- **Complete secret redaction**: multiline PEM blocks, not just headers
  (§9.1).
- **Path containment**: every case-controlled path field (reactive
  disruption triggers included) rejects traversal, absolute paths, `.git`
  segments, Windows alias forms (trailing dot/space, ADS, reserved device
  names) at schema-validation time, before a driver/model is ever invoked.
- **Pack authenticity**: real `ssh-keygen`-based signing/trust (§3.4),
  fixed-namespace verification, and per-run re-derived (not install-time
  cached) trust state.
- **Workspace resource containment**: a soft disk/file quota covering both
  the agent's own execution and `check_command` (§3.1 above).
- **Reproducibility metadata**: generation parameters, provider version,
  build-commit fallback for wheel installs, and a concrete
  `ORACLE_VERSION` bump policy (§3.3/§6.2).
- **Live CI coverage** extended to all optional SDK drivers via a
  zero-secret local-Ollama smoke job (`integration-smoke.yml`), plus a hard
  version-drift gate for the drivers that pin an exact tested version.
  `claude-code`/`codex` (real paid accounts) remain explicitly out of scope
  for the OSS `v0.1` release.
- **Governance**: `GOVERNANCE.md` (maintainers, decision process,
  deprecation/version policy for the case schema/drivers/results/oracle).

Full detail and per-finding evidence lived in `issues.md` during this pass;
that tracker is retired in dev notes once the branch history was compressed
for the `v0.1.0` release tag, since it served as a working log, not a
permanent record - the code and this document are the source of truth going
forward.

### 10.4 Sandboxed-real tool-use execution (2026-08-07)

§3.6's `SandboxedMCPService`/`--tool-service-mode sandboxed` pilot, built
and live-verified against real Docker rather than designed and left
untested. Sequenced as generic plumbing (`_mcp_client.py`,
`DockerSandbox.exec_attached`) first, proven on one service
(`filesystem`), then extended service by service - each one a real image
built, a real handshake against the real published package, and a real
smoke test, not a paper design. See §3.6 for the mechanism and the full
per-service schema-drift findings; the real bugs this surfaced (not
design decisions - actual defects caught by actually running the thing):

- A Windows-only `\n` → `\r\n` corruption in `seed()`'s file-writing
  (Python's default text-mode `write_text`), invisible until compared
  against what the real server actually wrote to disk - fixed with
  `newline=""` on both the write and read sides.
- `mcp-server-git`'s own declared dependency (`mcp>=1.0.0`) resolves to a
  real, current, breaking release (`mcp==2.0.0`) that removes the
  `Server.list_tools()` API the package's source still calls - a live,
  present-day compatibility break in the published ecosystem, not
  something reproducible from reading either package's docs alone.
- Real Postgres refuses to run as root outright, with no override flag -
  its startup script needs `su`, which needs `CAP_SETUID`/`CAP_SETGID`,
  neither in this project's default hardened capability set.
- Docker rejects two `--network` flags on one `docker run` outright
  ("conflicting options") - confirmed by trying it, not assumed - so a
  per-service network override had to be a dedicated constructor
  parameter, not something layered onto the existing hardening args list.
- A `kind` cluster name keyed by shell PID collided across two different
  sandboxed containers sharing one host Docker daemon (each container's
  PID namespace restarts fresh, so low PIDs repeat) - fixed with the
  container's own Docker-assigned hostname, which doesn't.
- Docker also refuses to `network connect` a container that was started
  in `--network none` mode at all (a different failure from the one
  above), discovered only after the PID-collision fix, requiring
  `kubernetes`'s sandbox to start on the `bridge` network from the outset
  rather than acquire more access at runtime.

Ten of fourteen mock services now have a live-verified sandboxed
counterpart (§3.6 lists all ten and what didn't match). `docker`/
`kubernetes` specifically required user sign-off before building, since
their real servers need docker-outside-of-docker (a mounted host
`docker.sock`) - a materially bigger trust boundary than every other
service's narrow, single-purpose hardening exception, granted after the
trade-off was presented explicitly rather than assumed. `task_tracker`
(no real reference implementation) and `forge`/`ci_pipeline`/`terraform`
(third-party SaaS, no disposable local substitute - belong to a separate,
not-yet-built live/bring-your-own-server mode) remain mock-only, by
design rather than remaining scope.

A same-cycle security/correctness audit of this subsystem (17 findings,
each verified against code or live behavior rather than pattern-matched)
was then fixed in full - the two P0s were both new-input-paths that had
bypassed EXISTING controls (`tool_service_seed` paths skipping the §10.3
path-containment gate; case content able to self-select sandboxed
execution against the project's fail-closed posture), and the P1s were
the whole-run-root bind mount, the undrained stderr pipe, the
model-as-principal framing of the host-socket services (now gated behind
`OPTARENA_ALLOW_HOST_DOCKER=1`), and the kind-node teardown leak. The
recurring pattern - new code not routed through existing, sound controls
- is worth remembering more than any individual finding. §3.6 describes
the post-fix behavior; the audit document itself lives in dev notes.
