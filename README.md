# OptArena

**The arena where AI coding agents compete.**

OptArena evaluates software engineering agents: UI agents (Cline's actual
VS Code UI, Roo, Continue, Kilo), headless CLI agents (aider, Claude Code,
Codex, OpenCode, Goose, Qwen Code), SDK agents (crewAI), and raw-model
baselines - all run through the same task cases, against **any
OpenAI/Ollama-compatible backend** (Ollama, LM Studio, a router/optimizer
proxy like SelfOpt, or a remote API), and verified by actually **compiling
and running the generated code in an isolated Docker sandbox** - not
keyword-matching it.

## Why?

**Which coding agent should you use? Did switching models actually help?
Did your prompt optimization make things better? Did your latest update
regress performance?**

The arena answers these automatically - same task, same oracle, side by side:

- *tool vs tool* - Cline vs aider vs Continue on the same backend
- *backend vs backend* - Cline through an optimizing proxy vs raw Ollama
- *model vs model* - gemma4:8b vs gemma4:12b through the same tool
- *agent vs no-agent* - any tool vs the raw-model baseline (what does the tool add?)
- *before vs after* - `optarena regression` names exactly which cases broke

## How it works

```
                          Same task
                              |
      +-----------+-----------+-----------+-----------+
      v           v           v           v           v
    Cline        Roo        Aider    Claude Code   raw model
      |           |           |           |           |
      +-----------+-----------+-----------+-----------+
                              v
                Docker-sandboxed verification
              (compiled, run, asserted for real)
                              v
                     Side-by-side verdict
```

Where a tool has a real UI, OptArena drives that UI - a real VS Code window,
real webview chat, real approval buttons - rather than simulating API
traffic. And the verdict comes from actually running the generated code
against real assertions in an isolated container, not from checking whether
a keyword shows up in a file.

## See it in action

A real comparison from this repo's own case suite, not a mockup
(dashboard at `optarena serve` renders the same data):

```bash
optarena run --driver ollama-chat --name raw-gemma3-1b --model gemma3:1b
optarena run --driver ollama-chat --name agent-qwen3-coder-30b --model qwen3-coder:30b
optarena compare raw-gemma3-1b agent-qwen3-coder-30b
optarena serve   # http://localhost:8300/dashboard/
```

## What makes this different

1. **Docker-sandboxed real verification** - generated code is compiled/run
   against real test assertions in an isolated container (`--network none`,
   one shared container per run), not keyword-matched. A model once wrote
   valid-looking C++ into a `.c` file and matched every required substring;
   `gcc` correctly rejected it. See [Cases & the oracle](#cases--the-oracle).
2. **UI-native testing** - bugs live in the seams the UI exercises. OptArena
   clicks the same buttons your users do, not a simulation of API traffic.
3. **Comparison-first** - per-case deltas and a verdict (more-accurate,
   faster) saved as JSON and rendered in the terminal and dashboard.
4. **Regression testing** - `optarena regression <before> <after>` names the
   cases that broke, not just an aggregate percentage. See below.
5. **Backend-agnostic** - a backend is any URL speaking the OpenAI or Ollama
   protocol: plain Ollama, LM Studio, a router proxy like SelfOpt, or a
   remote API.
6. **Zero infrastructure** - stdlib-only Python core, JSON results, a
   static-HTML dashboard. No database server, no build step, no accounts.
   Docker is recommended for sandboxed verification, not required - there's
   a host fallback.
7. **Cheap extensibility** - a new tool is one driver file. A new task is one
   JSON file (`optarena init` scaffolds one). A new backend is a URL.
8. **Private by default** - prompts and generated code go only to the
   backend URL in your scenario. Results are local JSON. No telemetry.

See **[ARCH.md](./ARCH.md)** for the full architecture.

## Install

```bash
git clone <this repo> && cd optarena
pip install -e .            # provides the `optarena` command (no dependencies)

# only for VS Code UI drivers (cline-ui / roo-ui / continue-ui):
cd ui-harness && npm install
```

Requirements: Python ≥ 3.10. UI drivers additionally need Node ≥ 18 and the
extension under test installed in `~/.vscode/extensions` (first UI run also
downloads a pinned VS Code, ~280 MB).

## Quick start

```bash
optarena list drivers                 # what tools can be driven
optarena list cases                   # task catalogue

# One scenario, inline (raw-model baseline against local Ollama)
optarena run --driver ollama-chat --name baseline --model llama3.2

# A/B: two scenarios in one command → auto-compares and saves the comparison
optarena run --scenario scenarios/cline-selfopt.json \
             --scenario scenarios/cline-ollama-direct.json

# Compare any two saved runs later
optarena list runs
optarena compare cline-selfopt cline-ollama-direct

# Dashboard at http://localhost:8300/dashboard/
optarena serve

# Build the base sandbox image (gcc + python3 + node) - check_command runs inside it
optarena docker build

# Build a per-language image for the benchmark-corpus tracks (python/node/jvm/go/rust/dotnet)
optarena docker build --lang go
optarena docker build --all      # every registered image

# Preflight: which drivers/extensions/backends are ready on this machine
optarena doctor

# Stochastic-agent honesty: run each case 3 times, majority verdict + pass@k detail
# (use an odd N - a tie, e.g. 1 pass / 1 fail at --trials 2, counts as FAIL)
optarena run --driver ollama-chat --name baseline --trials 3

# Parallel fan-out for baselines/CLI drivers; matrix across drivers x models
optarena run --driver ollama-chat --name quick --parallel 4
optarena run --matrix-drivers ollama-chat,aider --matrix-models llama3.2,gemma3:1b
```

Defaults: `--base-url http://localhost:11434`, `--model llama3.2` - override
per command, or set `OPTARENA_BASE_URL` / `OPTARENA_MODEL` once.

Scenario files are small JSON documents:

```json
{
  "name":    "cline-gemma12b",
  "driver":  "cline-ui",
  "backend": { "kind": "ollama", "base_url": "http://localhost:11434", "model": "gemma4:12b" },
  "cases":   ["create_factorial", "modify_add_type_hints"]
}
```

## Drivers

| Driver | Kind | Backend | Status | What it exercises |
|---|---|---|---|---|
| `cline-ui` | ui | scenario | stable | The real Cline extension in an isolated VS Code (real webview typing, auto-approval, workspace diff) |
| `aider` | cli | scenario | stable | aider CLI, headless |
| `openai-chat` | baseline | scenario | stable | Raw model via `/v1/chat/completions` - the no-agent baseline |
| `ollama-chat` | baseline | scenario | stable | Raw model via Ollama-native `/api/chat` |
| `claude-code` | cli | fixed | experimental | Claude Code headless (`claude -p`) |
| `codex` | cli | fixed | experimental | Codex CLI (`codex exec --full-auto`) |
| `opencode` | cli | scenario | experimental | OpenCode (`opencode run`) |
| `goose` | cli | scenario | experimental | Goose (`goose run -t`) |
| `qwen-code` | cli | scenario | experimental | Qwen Code (`qwen -p`) |
| `roo-ui` | ui | scenario | experimental | Roo Code via the same VS Code harness |
| `continue-ui` | ui | scenario | experimental | Continue via the same VS Code harness |
| `kilo-ui` | ui | scenario | experimental | Kilo Code via the same VS Code harness (Roo family) |
| `crewai` | sdk | scenario | optional | crewAI SDK agent (`pip install optarena[crewai]`) |

**Backend column**: `scenario` drivers point at the backend in your scenario
file, so backend-vs-backend comparisons are valid. `fixed` drivers (Claude
Code, Codex) use their own account/provider - tool-vs-tool comparisons only.
`optarena doctor` shows which drivers can actually run on your machine.

**Baseline caveat**: the raw-model baselines (and the bare crewAI agent)
have no file tools - they write the model's single code block to the case's
*first* expected path themselves. Cases that require several files or a
project layout (e.g. the Maven-tree Java cases) are therefore effectively
agent-only: a baseline fails them by construction, which *is* part of what
"agent vs no-agent" measures, but don't read those specific failures as a
statement about the model.

Adding a driver = one file in `optarena/drivers/` implementing
`run_case(case, scenario, workspace) -> CaseResult`, plus a registry line.
Everything else - runner, metrics, compare, dashboard - is driver-agnostic.

## Cases & the oracle

Cases live in `optarena/cases/*.json`: prompts + `setup_files` +
`expected_files` (path pattern + required content substrings). The
**filesystem diff is the oracle** - a case passes when the expected files
exist with the expected content, no matter how the tool produced them.

Optional assertion keys per expected file: `not_content_patterns` (forbidden
substrings), `regex_patterns`, and `min_lines`. But content patterns are only
a shape check - keyword matching alone can't tell working code from broken
code (a model once wrote C++ into a `.c` file and still matched every
required substring). Real correctness comes from `check_command`: a shell
command, run in the workspace after the file checks pass, that compiles/runs
the generated code and asserts on its actual behavior. Pair it with
`test_setup_files` - real test code (pytest-style asserts, a Node script, a
compile-and-run harness) written into the workspace **after** the model's
run, so the model never sees what it's graded against. All 120 built-in
cases use this, spanning the Phase 1 benchmark-corpus target from
`OptArena_Benchmark_Corpus_Specification.md`: Python (20, FastAPI/Flask/
SQLAlchemy/Pydantic/Typer), JavaScript/TypeScript (20, Express/
NestJS/React/Vue/plain Node), Java (15, Spring Boot), Go (10, Gin/Fiber),
Rust (10, Axum/Actix-web), C# (10, ASP.NET Core), C/C++ (10), SQL (10),
Shell (5), Docker Compose (5, static validation only), and Terraform (5,
`fmt`/`validate` only, no cloud provider blocks). Every case covers one of
the spec's task categories (feature, bug fix, refactoring, testing,
security, performance, devops) and was hand-verified end-to-end - a correct
reference solution passes, a broken one fails - before being counted as
done. The testing-category cases (`add_tests_*`) are additionally
**mutation-checked**: the hidden oracle first runs the model's tests against
the correct implementation (they must pass), then against deliberately broken
variants of it (each must make the tests fail) - so a vacuous test file that
matches the keyword shape but asserts nothing real cannot pass. See `ARCH.md`
for exactly what's built versus explicitly deferred
(the full corpus is 100-150 cases; the rest of that range, plus
repository-scale Level 3+ benchmarks, is future work).

`check_command` runs inside a Docker sandbox whenever Docker is available -
the shared `optarena-tester` base image (gcc + python3 + node,
`docker/Dockerfile`) for the original cases, or a per-language image
(`docker/<lang>/Dockerfile`, tagged via a case's `"docker_image"` field) for
cases that need a real framework toolchain pre-installed (FastAPI, Express,
Spring Boot, Gin, Axum, ASP.NET Core). Build what you need with `optarena
docker build` (base), `--lang <name>` (one track), or `--all` (everything) so
case authors and CI need no language toolchains on the host, and generated
code never executes directly there. Falls back to the host (with a warning)
when Docker is unavailable, or with `OPTARENA_NO_DOCKER=1`.

**One container per image needed, not one per check_command call.**
`optarena run` starts one Docker container per distinct image its loaded
cases actually need (only for cases with a `check_command`) - a run mixing a
Python case and a Go case gets both toolchains live at once - and every case/
trial needing a given image `docker exec`s into that same shared container;
every sandbox started is stopped once when the run finishes. 7 cases x 3
trials sharing one image means 21 `check_command` invocations against one
container, not 21 containers.

This is a CLI-first tool, so the run output says exactly what happened per
case - not just pass/fail, but which sandbox ran the test, its exit code and
timing, a diff-size estimate, a best-effort failure classification
(`syntax_error` / `compile_error` / `assertion_failure` / `runtime_error` /
`timeout`), and (on a pass) the test script's own output as proof it
actually executed rather than just returning 0:

```
  [case] create_fibonacci ... PASS (26.8s)
         test files (hidden from the model): test_fibonacci.py
         diff: 1 file(s), ~8 line(s) changed
         check_command via docker:optarena-tester:latest, exit 0, 0.53s: python3 test_fibonacci.py
         output: PASS
  [case] create_hello_world ... FAIL (0.4s) - check_command failed in docker (exit 1): python3 test_hello.py :: AssertionError: expected "Hello, World!" in stdout, got 'Hello, Wall\n'
         test files (hidden from the model): test_hello.py
         diff: 1 file(s), ~1 line(s) changed
         check_command via docker:optarena-tester:latest, exit 1 [assertion_failure], 0.33s: python3 test_hello.py
```

(That second one is a real run against a small local model - it printed
`Hello, Wall` instead of `Hello, World!`, which a keyword oracle checking for
`"Hello"` would have missed entirely.) With `--trials N`, each trial's
sandbox/exit/timing is listed individually.

`optarena init` scaffolds a project-local `cases/` directory with a sample
case (test_setup_files + check_command included) - use it via `--cases-dir`
or the scenario's `cases_dir` field to add your own prompts and real tests.

Per-case metrics: pass/fail, failure reasons and class, wall time, files
created/changed, an approximate diff size, tokens and USD cost (where the
backend reports usage - local Ollama/LM Studio backends are always free,
paid models are priced from a built-in table, `optarena/pricing.py`,
overridable via `~/.optarena/pricing.json`). `compare` adds per-case deltas
and a verdict (more-accurate / faster / cheaper).

Cases can also declare `"language"`/`"framework"` tags, plus free-form
benchmark-corpus metadata (`domain`, `difficulty`, `task_type`, `tags` - see
the built-in catalogue for examples). `optarena list cases --language
python --framework fastapi` and `optarena run --language go --framework gin`
filter by either or both (ANDed) - useful once you have cases spanning
several languages and frameworks.

## Regression testing

The most concrete real-world use case: did upgrading a model, tool, or
prompt actually help? `optarena regression <before> <after>` names exactly
which cases broke and which improved, not just an aggregate delta - and
exits non-zero if anything regressed, so it's usable as a CI gate:

```bash
$ optarena regression raw-gemma3-1b agent-qwen3-coder-30b

  raw-gemma3-1b  ->  agent-qwen3-coder-30b

  accuracy    14% -> 100%  (+85.7pp)
  mean time   1.4s -> 5.2s  (+3.8s)
  tokens      1704 -> 1399  (-305, -17.9%)

  regressed cases (passed in A, failed in B): 0
    (none)

  improved cases (failed in A, passed in B): 6
    - create_factorial
    - create_hello_world
    - create_reverse_string_js
    - create_server_c
    - modify_add_type_hints
    - multi_prompt_session
```

That's real output from the two runs above - swap the argument order and it
correctly reports 6 regressions with exit code 1. Both runs here were free
local Ollama backends, so no `cost` line appears; a `cost $X.XX -> $Y.YY`
line is added automatically whenever both runs priced a paid backend.

## Notes for UI runs

The VS Code UI drivers launch a **visible** editor window - don't touch mouse
or keyboard during a run. If a UI run fails with `Connection timeout
exceeded`, check for a stuck VS Code auto-updater (`CodeSetup*.exe`) holding
the global update mutex - see ARCH.md §8 for this and the other
environment gotchas the harness absorbs.

`legacy/` contains the retired first-generation (CDP + pyautogui) harness,
kept for reference only.
