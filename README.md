# OptArena

**Know when your AI agents actually get better.**

AI agent regression testing: the reproducible evaluation and regression
platform for AI coding agents. This is the `v0.1` branch, built around CLI,
raw API, and in-process SDK/agent-framework drivers integrating against
stable process/library contracts.

OptArena evaluates software engineering agents: headless CLI agents (aider,
Claude Code, Codex, OpenCode, Goose, Qwen Code), in-process agent-framework/
SDK agents (crewAI, OpenAI Agents SDK, smolagents, LangGraph, AutoGen,
Semantic Kernel), and raw-model baselines - all run through the same task
cases, against **any OpenAI/Ollama-compatible backend** (Ollama, LM Studio, a
router/optimizer proxy, or a remote API), and verified by actually
**compiling and running the generated code in an isolated container sandbox**
(Docker or Podman) - not keyword-matching it.

## Why?

**Which coding agent should you use? Did switching models actually help?
Did your prompt optimization make things better? Did your latest update
regress performance?**

OptArena answers these automatically - same task, same oracle, side by side:

- *tool vs tool* - aider vs Claude Code vs Goose on the same backend
- *backend vs backend* - the same tool through an optimizing proxy vs raw Ollama
- *model vs model* - gemma4:8b vs gemma4:12b through the same tool
- *agent vs no-agent* - any tool vs the raw-model baseline (what does the tool add?)
- *before vs after* - `optarena regression` names exactly which cases broke

## How it works

```
                          Same task
                              |
      +-----------+-----------+-----------+-----------+
      v           v           v           v           v
    Aider    Claude Code    Goose      crewAI       raw model
      |           |           |           |           |
      +-----------+-----------+-----------+-----------+
                              v
               Container-sandboxed verification
              (compiled, run, asserted for real)
                              v
                     Side-by-side verdict
```

The verdict comes from actually running the generated code against real
assertions in an isolated container, not from checking whether a keyword
shows up in a file.

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

1. **Container-sandboxed real verification** - generated code is compiled/run
   against real test assertions in an isolated container (`--network none`,
   one shared container per run), not keyword-matched. A model once wrote
   valid-looking C++ into a `.c` file and matched every required substring;
   `gcc` correctly rejected it. See [Cases & the oracle](#cases--the-oracle).
2. **Same harness for CLI and SDK agents** - headless CLI tools and
   in-process agent-framework SDKs run through the exact same case set,
   oracle, per-case deadline, mid-session disruptions, per-step trajectory
   records and cost/token accounting, so tool-vs-tool numbers are
   apples-to-apples rather than "whatever each driver happened to implement".
3. **Comparison-first** - per-case deltas and a verdict (more-accurate,
   faster) saved as JSON and rendered in the terminal and dashboard.
4. **Regression testing** - `optarena regression <before> <after>` names the
   cases that broke, not just an aggregate percentage. See below.
5. **Backend-agnostic** - a backend is any URL speaking the OpenAI or Ollama
   protocol: plain Ollama, LM Studio, a router/optimizer proxy, or a
   remote API.
6. **Zero infrastructure** - stdlib-only Python core, JSON results, a
   static-HTML dashboard. No database server, no build step, no accounts.
   A container engine (Docker or Podman, auto-detected) is expected for
   verification: without one, OptArena **fails closed** rather than running
   untrusted test commands on your machine (host execution is an explicit
   opt-in - `OPTARENA_DISABLE_SANDBOX=1` or `OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1`).
7. **Cheap extensibility** - a new tool is one driver file. A new task is one
   JSON file (`optarena init` scaffolds one). A new backend is a URL.
8. **Private by default** - prompts and generated code go only to the
   backend URL in your scenario. Results are local JSON. No telemetry.

See **[ARCH.md](./ARCH.md)** for the full architecture.

## Install

```bash
git clone https://github.com/trysti-labs/optarena.git && cd optarena
pip install -e .            # provides the `optarena` command (no dependencies)

# only for SDK/agent-framework drivers, one extra per framework you want:
pip install -e ".[crewai]"           # or openai-agents / smolagents /
                                      # langgraph / autogen / semantic-kernel
```

Requirements: Python ≥ 3.10. CLI drivers (aider, Claude Code, Codex,
OpenCode, Goose, Qwen Code) each need their own binary installed and on
`PATH` - `optarena doctor` reports what's missing.

**Source-checkout install only** - `pyproject.toml` only packages the
`optarena` Python package itself (plus the built-in `cases/*.json`), which is
all the CLI, baseline, and SDK drivers plus the case-content oracle logic
need. But `docker/` (sandbox Dockerfiles), `dashboard/` (the results UI), and
`repos/` (L3 `setup_repo` starter projects) are separate top-level
directories, not bundled into the package - a wheel built and installed
elsewhere (`pip install` from a copied/published wheel rather than a
checkout) won't have them, and `optarena sandbox build`, `optarena serve`, and
`setup_repo` cases will fail with a clear "not found" error rather than a
working degraded mode. `optarena doctor` reports which of the three are
present. There is no supported "everything bundled in one wheel" install
today - keep the git checkout around next to wherever `optarena` is installed
from it.

**Where results are stored**: a source checkout writes to `<repo>/results`
(unchanged). Any other install writes to `~/.optarena/results`, alongside the
pack registry and pricing overrides - never inside `site-packages`, where a
`pip install -U` would be entitled to delete your run history. Override with
`--results-dir` or `OPTARENA_RESULTS_DIR`.

## Quick start

```bash
optarena drivers list                 # what tools can be driven
optarena cases list                   # task catalogue (also: cases show <name>)

# One scenario, inline (raw-model baseline against local Ollama)
optarena run --driver ollama-chat --name baseline --model llama3.2

# A/B: two scenarios in one command → auto-compares and saves the comparison
optarena run --scenario scenarios/aider-proxy.json \
             --scenario scenarios/aider-ollama-direct.json

# Compare any two saved runs later
optarena runs list
optarena compare aider-proxy aider-ollama-direct

# Dashboard at http://localhost:8300/dashboard/
optarena serve

# Sandbox images - check_command runs inside them. Pull the published images
# (ghcr.io/trysti-labs/optarena/*, minutes) or build locally (~30 min):
optarena sandbox pull --all      # every registered image, from GHCR
optarena sandbox build           # base image (gcc + python3 + node), locally
optarena sandbox build --lang go # one per-language track
optarena sandbox build --all     # everything, locally
# (a run also auto-pulls a missing image on first use; OPTARENA_DISABLE_PULL=1 disables)

# Corpus self-verification (CI gate): reference solutions must PASS the real
# oracle, broken/unmodified variants must FAIL it
optarena cases verify            # (legacy alias: verify-corpus)
optarena cases verify --strict   # also fail on cases with nothing that must FAIL

# Housekeeping for the results store
optarena runs prune --keep 50 --yes   # drop old runs
optarena runs rebuild-index           # repair a corrupt/out-of-sync index.json
optarena runs scrub-secrets --yes     # redact api_key from pre-redaction records

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
  "name":    "aider-gemma12b",
  "driver":  "aider",
  "backend": { "kind": "ollama", "base_url": "http://localhost:11434", "model": "gemma4:12b" },
  "cases":   ["create_factorial", "modify_add_type_hints"]
}
```

## Drivers

| Driver | Kind | Backend | Status | What it exercises |
|---|---|---|---|---|
| `aider` | cli | scenario | stable | aider CLI, headless |
| `openai-chat` | baseline | scenario | stable | Raw model via `/v1/chat/completions` - the no-agent baseline |
| `ollama-chat` | baseline | scenario | stable | Raw model via Ollama-native `/api/chat` |
| `claude-code` | cli | fixed | experimental | Claude Code headless (`claude -p`) |
| `codex` | cli | fixed | experimental | Codex CLI (`codex exec --full-auto`) |
| `opencode` | cli | scenario | experimental | OpenCode (`opencode run`) |
| `goose` | cli | scenario | experimental | Goose (`goose run -t`) |
| `qwen-code` | cli | scenario | experimental | Qwen Code (`qwen -p`) |
| `crewai` | sdk | scenario | optional | crewAI SDK agent (`pip install optarena[crewai]`) |
| `openai-agents` | sdk | scenario | optional | OpenAI Agents SDK agent (`pip install optarena[openai-agents]`) |
| `smolagents` | sdk | scenario | optional | smolagents `ToolCallingAgent` (`pip install optarena[smolagents]`) |
| `langgraph` | sdk | scenario | optional | LangGraph `create_react_agent` (`pip install optarena[langgraph]`) |
| `autogen` | sdk | scenario | optional | AutoGen/AG2 `AssistantAgent` (`pip install optarena[autogen]`) |
| `semantic-kernel` | sdk | scenario | optional | Semantic Kernel `ChatCompletionAgent` (`pip install optarena[semantic-kernel]`) |

**Backend column**: `scenario` drivers point at the backend in your scenario
file, so backend-vs-backend comparisons are valid. `fixed` drivers (Claude
Code, Codex) use their own account/provider - tool-vs-tool comparisons only.
`optarena doctor` shows which drivers can actually run on your machine.

**Baseline caveat**: the raw-model baselines and the SDK agents have no file
tools - they write the model's single code block to the case's *first*
expected path themselves. Cases that require several files or a project
layout (e.g. the Maven-tree Java cases) are therefore effectively agent-only:
a baseline fails them by construction, which *is* part of what "agent vs
no-agent" measures, but don't read those specific failures as a statement
about the model.

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
run, so the model never sees what it's graded against. The built-in
catalogue is **836 cases across 18 languages and frameworks**:
Python (150, FastAPI/Flask/Django/
SQLAlchemy/Pydantic/Typer/pandas), JavaScript (81) and TypeScript (49,
Express/NestJS/React/Vue/plain Node), Java (56, Spring Boot/plain),
Kotlin (35, Spring Boot/plain), Go (56, Gin/stdlib), Rust (49, Axum/
Actix-web/stdlib), C# (49, ASP.NET Core/plain), C (25) and C++ (19), PHP
(37) and Ruby (38), SQL (43), Shell (29), YAML (50, Docker Compose/
Kubernetes/GitHub Actions), HCL/Terraform (25), Dockerfile (25), and
Makefile (20). Every case covers one of ten task categories - feature, bug
fix, refactoring, testing, security, performance, devops, data
engineering, documentation, dependency upgrade. Every case was verified
end-to-end through the real `--network none` container sandbox before being
counted as done: all 836 carry a `reference_solution` that must PASS the
oracle, and every one of them also carries something that must FAIL it (an
explicit `broken_solutions` variant, or the implicit "unmodified workspace"
check that bug_fix/refactoring/performance/security cases get) - `optarena
cases verify --strict` fails the build if a future case is ever added
without one, so this stays true rather than just being true today. The testing-category cases (`add_tests_*`) are additionally
**mutation-checked**: the hidden oracle first runs the model's tests against
the correct implementation (they must pass), then against deliberately
broken variants of it (each must make the tests fail) - so a vacuous test
file that matches the keyword shape but asserts nothing real cannot pass.
See `ARCH.md` for what's built versus explicitly deferred (frontier stacks
like Next.js/Svelte/Deno-Bun, moat hardening, and further repository-scale
Level 3+ starter repos beyond the two already live - `fastapi-tasktracker`
and `express-ts-shortlink`).

**Trust model - what these public cases are (and are not).** The corpus ships
in the open, *including* every case's hidden tests and `reference_solution`
(all 836 cases carry one), plus `broken_solutions` on most cases - that
openness is what lets `verify-corpus` prove each oracle can pass (the reference
solution, for every case) and fail (a broken/unmodified variant), and lets you
audit exactly what a PASS means.
The flip side: a benchmark-aware agent (or one you prompt to cheat) could in
principle look the answers up. So treat OptArena results as **acceptance and
regression evidence for tools you're honestly evaluating** - the A/B and
before/after workflows above - not as a tamper-resistant public leaderboard.
Adversarial-grade benchmarking needs private case packs, which the built-in
`--cases-dir` already supports: point it at a directory of your own unpublished
cases and nothing about them ever leaves your machine. See
[SECURITY.md](./SECURITY.md) for the full trust-boundary write-up.

`check_command` runs inside a container whenever a container engine is
available - Docker or Podman, auto-detected (`OPTARENA_CONTAINER_ENGINE`
forces one) - using the shared `optarena-tester` base image (gcc + python3 +
node, `docker/Dockerfile`) for the original cases, or a per-language image
(`docker/<lang>/Dockerfile`, tagged via a case's `"image"` field) for
cases that need a real framework toolchain pre-installed (FastAPI, Express,
Spring Boot, Gin, Axum, ASP.NET Core). Build what you need with `optarena
docker build` (base), `--lang <name>` (one track), or `--all` (everything) so
case authors and CI need no language toolchains on the host, and generated
code never executes directly there. When no container engine is available,
OptArena **refuses to run `check_command` on the host** (a clear non-zero
error) - host execution is an explicit opt-in via `OPTARENA_DISABLE_SANDBOX=1`
(container sandbox deliberately disabled, e.g. this repo's own unit-test CI)
or `OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1` (a sandbox is wanted but
missing/broken and you accept running untrusted commands directly on this
machine).

**One container per image needed, not one per check_command call.**
`optarena run` starts one container per distinct image its loaded
cases actually need (only for cases with a `check_command`) - a run mixing a
Python case and a Go case gets both toolchains live at once - and every case/
trial needing a given image `exec`s into that same shared container;
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
overridable via `~/.optarena/pricing.json`). The aider and claude-code
drivers report real token/cost figures parsed from their own output
(claude-code adds `turns`), so agent-vs-agent cost comparisons don't
silently degrade to duration-only. `compare` adds per-case deltas and a
verdict (more-accurate / faster / cheaper), p95 duration, and - with
`--trials N` - a per-case stability marker (`PASS 2/3`) plus a flaky-case
list, so a majority verdict with dissenting trials is never presented as a
unanimous one.

Cases can also declare a `reference_solution` (must PASS the full oracle)
and `broken_solutions` (each must FAIL it); `optarena verify-corpus` replays
them through the real sandboxed oracle and exits non-zero on any violation -
the CI gate that keeps the corpus honest as cases evolve. For bug_fix/
refactoring/performance/security cases it also auto-checks that an untouched
workspace fails ("the model changed nothing" must never score a pass).

Cases can also declare `"language"`/`"framework"` tags, plus free-form
benchmark-corpus metadata (`domain`, `difficulty`, `task_type`, `tags` - see
the built-in catalogue for examples). `optarena cases list --language
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

## License

Apache License 2.0 - see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
Copyright © 2026 Trysti Labs and contributors. An open-source project by
[Trysti Labs](https://trysti.com/labs); contributions welcome under the same
license.
