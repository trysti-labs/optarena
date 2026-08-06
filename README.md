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

- **Container-sandboxed real verification** - generated code is compiled/run
  against real assertions, not keyword-matched (a model once wrote valid C++
  into a `.c` file and matched every required substring; `gcc` disagreed).
- **Same harness for CLI and SDK agents** - identical case set, oracle, and
  telemetry for every driver, so tool-vs-tool numbers are apples-to-apples.
- **Comparison-first** - per-case deltas and a verdict, not just an aggregate
  pass rate, saved as JSON and rendered in the terminal and dashboard.
- **Regression testing** - `optarena regression <before> <after>` names the
  cases that broke, not just an aggregate percentage. See below.
- **Backend-agnostic and zero infrastructure** - any URL speaking the
  OpenAI/Ollama protocol; stdlib-only core, JSON results, a static-HTML
  dashboard, no database or accounts.
- **Private by default** - prompts and code go only to your backend URL.
  Results are local JSON. No telemetry.

See **[ARCH.md](./ARCH.md)** for the full architecture and design rationale.

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

**Source-checkout install only.** `docker/`, `dashboard/`, and `repos/` are
not packaged into the wheel, so `optarena sandbox build`, `optarena serve`,
and `setup_repo` cases need the git checkout kept around, not just `pip
install`. Results are written to `<repo>/results` from a checkout, or
`~/.optarena/results` otherwise (never inside `site-packages`). `optarena
doctor` reports exactly what's present and where results are going.

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

# Preflight: which drivers/extensions/backends are ready on this machine
optarena doctor
```

Defaults: `--base-url http://localhost:11434`, `--model llama3.2` - override
per command, or set `OPTARENA_BASE_URL` / `OPTARENA_MODEL` once. Every
subcommand has its own `--help`; sandbox image management
(`optarena sandbox pull/build`), corpus self-verification
(`optarena cases verify`), results housekeeping (`optarena runs
prune/rebuild-index/scrub-secrets`), `--trials N` for stochastic-agent
honesty, and `--parallel N`/`--matrix-drivers`/`--matrix-models` are all
there - see `optarena <command> --help` or [ARCH.md](./ARCH.md).

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
| `openai-tools` | baseline | scenario | experimental | Raw tool-calling loop via `/v1/chat/completions` - for [tool-use cases](#tool-use-cases) |
| `ollama-tools` | baseline | scenario | experimental | Raw tool-calling loop via Ollama-native `/api/chat` - for [tool-use cases](#tool-use-cases) |
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
layout are therefore effectively agent-only - part of what "agent vs
no-agent" measures, not a statement about the model.

See [CONTRIBUTING.md](./CONTRIBUTING.md) for how to add a driver or a case.

## Cases & the oracle

Cases live in `optarena/cases/*.json`: prompts + `setup_files` +
`expected_files` (path pattern + required content substrings). The
**filesystem diff is the oracle** - a case passes when the expected files
exist with the expected content, no matter how the tool produced them.
Content-pattern matching is only a shape check, though - real correctness
comes from `check_command`: a shell command, run in the workspace after the
file checks pass, that actually compiles/runs the generated code and asserts
on its behavior inside an isolated container (`--network none`), paired with
`test_setup_files` (real test code written into the workspace *after* the
model's run, so it never sees what it's graded against).

The built-in catalogue is **836 cases across 18 languages and frameworks**
(Python, JavaScript/TypeScript, Java, Kotlin, Go, Rust, C#, C/C++, PHP,
Ruby, SQL, Shell, YAML, HCL/Terraform, Dockerfile, Makefile), spanning ten
task categories (feature, bug fix, refactoring, testing, security,
performance, devops, data engineering, documentation, dependency upgrade).
Every case was verified end-to-end through the real sandboxed oracle before
being counted: each carries a `reference_solution` that must PASS, and
something that must FAIL (an explicit `broken_solutions` variant, or an
auto-checked unmodified-workspace case) - `optarena cases verify --strict`
keeps this true as the corpus grows, not just true today.

Real output, not a mockup - what actually happened per case, not just
pass/fail:

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
`Hello, Wall` instead of `Hello, World!`, which a keyword oracle checking
for `"Hello"` would have missed entirely.)

**Trust model - what these public cases are (and are not).** The corpus
ships in the open, including every case's hidden tests and
`reference_solution` - that openness is what lets `optarena cases verify`
prove each oracle can pass and fail, and lets you audit exactly what a PASS
means. The flip side: a benchmark-aware agent could in principle look the
answers up, so treat OptArena results as **acceptance and regression
evidence for tools you're honestly evaluating**, not a tamper-resistant
public leaderboard - point `--cases-dir` at your own private cases for
adversarial-grade benchmarking. See [SECURITY.md](./SECURITY.md) for the
full trust-boundary write-up, and [ARCH.md](./ARCH.md) §3.1 for the
container-sandbox mechanics, per-case metrics, and language/framework
filtering in full detail.

`optarena init` scaffolds a project-local `cases/` directory with a sample
case (`test_setup_files` + `check_command` included) to start writing your
own.

## Tool-use cases

A second, parallel case domain: instead of grading file output, a tool-use
case grades whether the model called the right tools (function/tool-calling)
against a mock, in-process API, with the right arguments - closer to what
BFCL/tau-bench evaluate for tool-calling agents, authored just as cheaply as
a coding case (one JSON file, no live server or container needed).

```bash
optarena run --driver ollama-tools --model qwen3-coder:30b \
  --cases tool_create_task,tool_create_and_complete_task
```

Ships with one mock service (`task_tracker`: create/complete/list/delete)
and five example cases (`tool_*` in the catalogue). See [ARCH.md](./ARCH.md)
§3.5 for the schema and oracle mechanics, and what's still deferred (agent-
driver support beyond the raw baselines, more mock services).

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

## Community

[CONTRIBUTING.md](./CONTRIBUTING.md) for how to send a PR, [SECURITY.md](./SECURITY.md)
for reporting a vulnerability, [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) for
the ground rules, and [GOVERNANCE.md](./GOVERNANCE.md) for who maintains this,
how decisions get made, and the project's version/deprecation policies.

## License

Apache License 2.0 - see [LICENSE](./LICENSE) and [NOTICE](./NOTICE).
Copyright © 2026 Trysti Labs and contributors. An open-source project by
[Trysti Labs](https://trysti.com/labs); contributions welcome under the same
license.
