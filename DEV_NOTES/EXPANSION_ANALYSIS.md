# OptArena Expansion Analysis

_Date: 2026-07-02. Sources: competitive research into agent benchmarks and
evaluation harnesses (Terminal-Bench, SWE-bench, aider polyglot, OpenHands
benchmarks, promptfoo) and the 2026 coding-agent landscape. Companion
document: `selfopt/FEATURE_EXPANSION_ANALYSIS.md`._

> **Implementation status (updated 2026-07-02):** the priority shortlist has
> been implemented (Status column in section 4). Everything is opt-in:
> new drivers activate by name, new oracle keys are optional per case, and
> `--trials` / `--parallel` / `--cases-dir` / matrix flags default to the
> historical behaviour. New CLI: `optarena doctor`, `optarena init`.
> Registry metadata (`kind` / `backend` / `status`) shipped, with
> fixed-backend tools clearly marked. The core remains stdlib-only; tests
> live in `tests/` (`python -m unittest discover tests`)._
>
> **2026-07-04 follow-up:** an external README/positioning review
> (`OptArena_README_Feedback.md`) prompted two more rounds of work, tracked
> as rows 15-18 below: (1) `check_command` upgraded from "keyword matching
> only" to real `test_setup_files` execution sandboxed in a single shared
> Docker container per run (not one per call - an earlier version of that
> fix briefly regressed to one container per check_command call before
> being caught and corrected); (2) `optarena regression` (named
> regressed/improved cases, CI-gate exit code); (3) richer per-case metrics
> (`diff` size, `failure_class` bucketing); (4) a README/positioning rewrite
> plus `WEBSITE.md`, a standing design brief for the optarena.com marketing
> site.

## 1. The essence (what must not be lost)

Every expansion decision below is tested against five properties that make
OptArena worth using instead of the big benchmarks:

1. **Local-first, zero infrastructure.** Stdlib-only core, JSON results,
   static dashboard. No Docker requirement, no accounts, no queue.
2. **Your tools, your backend, your machine.** Answers *your* A/B question
   in minutes, not a leaderboard's question in GPU-days.
3. **UI-native where the tool is UI-native.** Testing what users actually
   click, not a simulation of the tool's API traffic.
4. **Comparison is the product.** Single runs are inputs; the verdict
   (more accurate / faster, per-case deltas) is the output.
5. **A new tool is one driver file; a new task is one JSON file.**

The biggest expansion risk is drifting toward "small SWE-bench": curated
suites, container farms, leaderboard thinking. Everything below is scoped
to avoid that.

## 2. Tool support expansion (the main question)

The 2026 agent landscape converged on terminal-native agents, and almost
all of them now ship a **headless/non-interactive mode**. That is exactly
the shape OptArena's driver contract wants: spawn process in workspace,
pass prompt, wait, diff the filesystem. Expansion is therefore cheap where
it matters most.

### Tier 1 - headless CLI drivers (one file each, high value, fully on-essence)

| Tool | Headless invocation (shape) | Notes |
|---|---|---|
| Claude Code | `claude -p "<prompt>" --dangerously-skip-permissions` | The most-used agent CLI; supports any Anthropic-compatible backend via env. Highest-value single addition. |
| Codex CLI | `codex exec "<prompt>"` | OpenAI-compatible backends configurable; sandboxed by default (may need `--full-auto`). |
| OpenCode | `opencode run "<prompt>"` | 75+ providers incl. Ollama/OpenAI-compatible; most-starred OSS agent. |
| Goose | `goose run -t "<prompt>"` | Block's agent; strong local-model support; MCP-native. |
| Qwen Code | `qwen -p "<prompt>"` | Alibaba's CLI; pairs with open-weights Qwen3-Coder - very on-theme for local baselines. |
| Cline CLI | `cline --auto-approve` (headless) | Already on the roadmap; complements the existing cline-ui driver (UI vs CLI of the *same* tool becomes an A/B!). |
| OpenHands CLI | lightweight CLI-only package | LLM-agnostic; note its default is Docker-sandboxed - use the CLI-only mode to stay zero-infra. |
| Copilot CLI | `copilot -p "<prompt>"` | GitHub-account-bound; backend not swappable - include only if a "fixed-backend tool" driver class is acceptable (see 2.4). |
| Amp, Crush, Plandex, Kimi CLI | similar | Long tail; add on demand once the pattern is proven. |

All of these fit the existing contract (`run_case(case, scenario,
workspace) -> CaseResult`) with the aider driver as the template: build
command, scrub env, run in workspace, let the oracle judge. Estimated cost
per driver: 50-100 lines plus a registry entry. **This is where OptArena
should spend its expansion budget.** Ten drivers at one file each is a
bigger moat than any single feature.

Practical notes for CLI drivers:

- **Backend injection differs per tool.** Claude Code wants
  `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`; Codex wants a config profile;
  OpenCode/Goose/Qwen take OpenAI-compatible env or config files. Extend the
  driver interface with a per-driver `backend_env(backend) -> dict` helper,
  mirroring how `extensions.js` seeds per-extension config.
- **Auto-approval flags are load-bearing** and each tool names them
  differently (`--dangerously-skip-permissions`, `--full-auto`, `--yes`,
  `--auto-approve`). Descriptor data, not code.
- **A driver that cannot point at the scenario backend** (Copilot CLI)
  breaks backend-vs-backend comparisons but still supports tool-vs-tool on
  its own backend. Mark such drivers `fixed-backend` in the registry and in
  the dashboard, so comparisons stay honest.

### Tier 2 - VS Code extension UI drivers (descriptor per extension)

The wdio harness is generic; each new extension is a descriptor in
`extensions.js` (install prefix, view command, config seeding, button
regexes). Candidates: **Kilo Code** (Roo/Cline fork family, same
`globalState` pattern), **Continue** (already experimental - finish it),
**Copilot Chat agent mode** (high interest, but config seeding is
account-based - likely fixed-backend). Keep Tier 2 deliberately small:
each descriptor costs real maintenance because extensions rewrite their
webviews freely. The three-extension harness already proves the
architecture; add Kilo Code (nearly free, same family) and stop until
someone asks.

### Tier 3 - explicitly out of scope

- **IDE-native agents without automation surfaces** (Cursor IDE, Windsurf
  IDE): driving a proprietary IDE's UI is a brittleness money-pit, and both
  now have CLI entry points anyway (`cursor-agent` CLI can be a Tier 1
  driver instead).
- **Cloud-only agents** (Devin, Cursor Cloud Agents, Jules): the work
  happens on someone else's VM; nothing local to diff and latency/cost are
  not comparable. Off-essence.
- **Browser-tab tools** (ChatGPT canvas, claude.ai): no workspace.

### 2.4 Driver metadata to add alongside the expansion

With 10+ drivers, the registry needs three flags the current one lacks:

- `kind`: `ui | cli | sdk | baseline` (dashboard grouping, docs)
- `backend`: `scenario | fixed` (whether backend-vs-backend is meaningful)
- `status`: already exists (`stable | experimental | optional`)

## 3. Feature inheritance from the eval ecosystem

### From promptfoo (assertion vocabulary)

1. **Richer oracle assertions, still declarative.** Today: glob +
   case-insensitive substrings. Add per-spec optional keys, all stdlib:
   `not_content_patterns` (must NOT contain), `regex_patterns`,
   `json_schema` (structural check for JSON files), `min_lines` /
   `max_files`. Directly inspired by promptfoo's assertion set, and fixes
   real false-pass risk (e.g. a file containing the right keywords inside
   an error message).
2. **Repeat counts / flake awareness.** `optarena run --trials 3` producing
   pass@1 and pass@3 with per-case variance. Agent runs are stochastic;
   single-trial verdicts overstate certainty. The compare table gains a
   "stability" column. This is the single most credibility-adding feature.

### From Terminal-Bench / SWE-bench (task realism, consumed carefully)

3. **Command oracles (already on the roadmap - promote to next).** Optional
   per-case `check_command` (e.g. `python -m pytest -q`, `gcc factorial.c &&
   ./a.out`) run in the workspace after content checks. This is the single
   biggest realism upgrade and needs no infrastructure. Timeout-bounded,
   exit-code judged, output captured into failures.
4. **Case packs.** `optarena init` scaffolds a project-local `cases/`;
   `--cases-dir` accepts multiple dirs. A community pack repo
   (`optarena-cases`) can then grow without bloating the core. An importer
   for a *tiny* curated slice of SWE-bench-lite-style tasks (self-contained,
   no Docker) is a case pack, not a core feature.
5. **What NOT to inherit:** container-per-task isolation, hundreds-of-task
   suites, remote runtime farms, leaderboard publishing. That is
   Terminal-Bench/OpenHands territory; pointing users there is better than
   competing.

### From OpenHands benchmarks (operational features)

6. **Parallel execution for non-UI drivers.** Baselines/CLI drivers can
   fan out per-case with a thread pool (stdlib); UI drivers stay serial
   (one display). Cuts a 6-case 3-scenario baseline matrix from minutes to
   seconds.
7. **Headless CI mode (roadmap - keep).** Xvfb wrapper for UI drivers on
   Linux; CLI/baseline drivers already run headless. Combined with exit
   codes, OptArena becomes a nightly regression gate ("did the new Ollama
   model version regress my Cline setup?").
8. **Run matrix.** `optarena run --matrix drivers=aider,claude-code
   backends=ollama:llama3.2,selfopt:llama3.2` expanding to N scenarios and
   an N-way dashboard grid. The comparison model generalizes from pairs to
   grids without touching the domain model (Comparison stays pairwise;
   matrix view composes them).

### Cross-pollination with SelfOpt

9. **Token/cost columns everywhere.** Baseline drivers already collect
   usage; surface tokens (and $ when the backend reports pricing) in
   compare tables and the dashboard. For proxy backends like SelfOpt this
   shows the optimizer's savings per case - the killer demo for both tools.
10. **`selfopt arena` regression gate** (documented on the SelfOpt side):
    OptArena as the offline verifier for SelfOpt's online learning.

### Original ideas

11. **Trend view.** The dashboard compares two runs; add a per-scenario
    time series (pass rate and mean duration over the last N runs of the
    same scenario name) so regressions across weeks are visible. Data is
    already in `index.json`.
12. **`optarena doctor`.** Preflight that checks each installed driver:
    binary found, version, backend reachable, extension installed, VS Code
    updater mutex free. The ARCH §8 gotchas as executable checks instead of
    documentation.
13. **Case recorder (stretch).** Derive a case skeleton from a real
    session: point at a workspace diff ("these files changed") and emit
    `expected_files` with suggested content patterns. Lowers the main
    adoption barrier (writing good oracles).
14. **LLM-judge scoring (roadmap - keep opt-in).** Judge as a pluggable
    backend, never default: subjective scores dilute the filesystem
    oracle's tool-neutral credibility. Restrict to a
    `quality_score` extra, never pass/fail.

## 4. Priority shortlist

| # | Item | Effort | Impact | Status |
|---|---|---|---|---|
| 1 | Claude Code driver | S | Very high | Done (experimental; `fixed` backend - uses your account) |
| 2 | Command oracles (`check_command`) | S | Very high | Done (Python + JS mirror; upgraded to real `test_setup_files` + Docker-sandboxed execution - see below) |
| 3 | `--trials N` + pass@k | M | High | Done (majority verdict; per-trial detail in `extra`) |
| 4 | OpenCode + Qwen Code + Goose drivers | S each | High | Done (experimental; shared descriptor driver) |
| 5 | Codex CLI driver | S | High | Done (experimental; `fixed` backend flag shipped) |
| 6 | Negative/regex oracle assertions | S | Medium-high | Done (`not_content_patterns`, `regex_patterns`, `min_lines`) |
| 7 | Parallel baselines/CLI fan-out | M | Medium | Done (`--parallel N`, `parallel_safe` drivers only) |
| 8 | Run matrix + dashboard grid | M | Medium | Partial: `--matrix-drivers`/`--matrix-models` + terminal matrix table; dashboard grid deferred |
| 9 | Token/cost columns | S | Medium | Done (tokens tile in verdict shipped 2026-07-04 morning; real USD cost added same day - see row 19) |
| 10 | Trend view in dashboard | S | Medium | Done (per-scenario history under Pass rate) |
| 11 | `optarena doctor` | S | Medium | Done (found a real stuck CodeSetup updater on first run) |
| 12 | Kilo Code UI descriptor | S | Low-med | Done (experimental; Roo-family seeding, unvalidated ids) |
| 13 | Headless CI (Xvfb) | M | Medium | Not implemented (Linux-specific; next) |
| 14 | Case packs + `optarena init` | M | Medium | Done (`optarena init` + `--cases-dir` + scenario `cases_dir`) |
| 15 | Real test execution (`test_setup_files`) + Docker sandbox | M | Very high | Done (one shared container per run via `docker exec`, not one per call; host fallback) |
| 16 | `optarena regression` (named regressed/improved cases, CI gate) | S | High | Done (`compare.py`'s `regression_summary`/`format_regression`; exit 1 on regression) |
| 17 | Richer per-case metrics (diff size, failure class) | S | Medium | Done (`diff_stats`, `classify_failure` in `cases.py`); turn counts done for claude-code (row 20), retries/tool-call counts for the rest not implemented |
| 18 | README/positioning rewrite + `WEBSITE.md` brief | S | High (adoption) | Done (Arena framing, Why section, workflow diagram, real dashboard screenshot, regression section) |
| 19 | Real USD cost (`pricing.py`) + `language` case tag + filter | S | High | Done (found by auditing the v2 website's promises against the code - the leaderboard's Cost/Files-changed/Language columns had no backing data before this) |
| 20 | Agent-driver token/cost telemetry (aider, claude-code) | M | High | Done 2026-07-08 (`parse_aider_metrics`, `parse_claude_json_metrics`; `--output-format json` for claude-code); opencode/goose/qwen-code/codex still duration-only |
| 21 | Corpus self-verification (`optarena verify-corpus`, reference/broken solutions) | M | Very high | Done 2026-07-08 (see `CORPUS_EXPANSION_PLAN.md` Priority 0, `ARCH.md` §10.2); found 2 real corpus bugs on its first full run |
| 22 | Trials stability surfaced in compare/regression (flaky cases, `PASS 2/3`) | S | High | Done 2026-07-08 (`case_deltas`, `regression_summary`) |
| 23 | Publish sandbox images to a registry (GHCR) | S | Medium | Done 2026-07-08 (`ghcr.io/trysti-labs/optarena/*`, auto-pull fallback, `optarena docker pull`) - supersedes the "not implemented" note in §10.1 of `ARCH.md` |

**2026-07-04 follow-up on item 2:** content-pattern matching alone proved
insufficient in practice - a small local model wrote valid-looking C++ into a
`factorial.c` case and matched every required substring, a pure-keyword
oracle would have graded it a pass. Command oracles are now the primary
correctness signal, not an optional extra: every built-in case ships a
`test_setup_files` test script (real asserts, not substrings) plus a
`check_command` that runs it, covering three initial languages (Python, C,
Node.js). `check_command` runs inside a shared `optarena-tester` Docker image
(`docker/Dockerfile`) when Docker is available - `optarena docker build`
builds it once - so case authors/CI need no language toolchains installed
locally, and generated code never executes directly on the host. Falls back
to the host (warned) without Docker. This keeps the earlier "no
infrastructure required" framing accurate: Docker is recommended, not
mandatory.

**Item 15 detail:** the first version of the Docker sandbox started a fresh
`docker run --rm` per `check_command` call - 7 cases x 3 trials meant 21
containers for one `optarena run`. Caught via user feedback on a live run,
fixed with `DockerSandbox`: one container started once per run (only when
some loaded case needs it), every case/trial `docker exec`s into that same
container, stopped once at the end. Verified live by grepping a real run's
output for distinct container names (one) and confirming `docker ps -a`
shows nothing left behind. Trade-off: all cases/trials in a run now share a
network namespace, so a case binding a fixed port across repeated trials can
occasionally collide with a not-yet-released binding from an earlier trial
(observed once on `create_server_c`) - prefer an ephemeral port in new
port-binding cases.

**Item 16/17 detail:** `optarena regression <before> <after>` reuses
`compare_runs()` but reports named `regressed_cases`/`improved_cases` lists
instead of an aggregate table, with exit code 1 on any regression (CI-gate
usable). `classify_failure()` is a best-effort bucket from captured
check_command output (`syntax_error` / `compile_error` / `assertion_failure`
/ `runtime_error` / `timeout`) - not authoritative, not part of the oracle's
own pass/fail verdict. `diff_stats()` approximates lines changed per case
(exact for new files; a known-original-content diff for "modify" cases,
since the pre-run snapshot only stores a signature, not content). Retry
counts and tool-call counts (also requested in the feedback) are not
implemented - they need per-driver instrumentation rather than oracle-side
computation, and no driver currently surfaces that data uniformly.

**Item 19 detail:** the v2 marketing site (`website2/`) launched with a
leaderboard promising Cost, Files-changed, and Language filter columns, and
a regression example showing a cost delta - none of which the actual tool
tracked. Rather than water down the website, implemented the gaps: `pricing.py`
estimates USD cost from token usage (local backends and unpriced models are
`$0.00`, never a fabricated number; overridable via `~/.optarena/pricing.json`),
`aggregate()` now sums `total_cost_usd` and averages `mean_files_changed`,
`regression_summary`/`format_regression`/`format_table` gained a cost
delta/verdict, the dashboard gained matching tiles, and cases gained an
optional `language` tag (all seven built-in cases tagged) filterable via
`optarena list cases --language <x>` and `optarena run --language <x>`.
Verified live against a real Ollama run (cost correctly `$0.00`/"free") and
a real `--language javascript` run (correctly ran only the one JS case).

New-driver caveat: the CLI-agent invocation shapes follow each tool's
documented headless mode but are marked experimental until validated on a
machine with the tool installed - `optarena doctor` tells you which ones are
ready. LLM-judge scoring (section 3, item 14) remains deliberately
unimplemented.

S = under a day, M = a few days, at current codebase size.

The order matters: items 1-6 make OptArena's *answers* better (more tools,
more trustworthy verdicts); items 7-18 make it *bigger* or easier to adopt.
Do the first group before the second.

## Sources

- [Agent benchmarks beyond SWE-bench in 2026](https://www.birjob.com/blog/agent-benchmarks-2026)
- [Coding agent benchmarks 2026 overview](https://presenc.ai/research/coding-agent-benchmarks-2026)
- [Terminal-Bench paper](https://arxiv.org/pdf/2601.11868)
- [OpenHands evaluation harness docs](https://docs.openhands.dev/openhands/usage/developers/evaluation-harness)
- [OpenHands benchmarks repo](https://github.com/OpenHands/benchmarks)
- [OpenHands Agent SDK paper](https://arxiv.org/html/2511.03690)
- [promptfoo GitHub](https://github.com/promptfoo/promptfoo)
- [promptfoo docs](https://www.promptfoo.dev/docs/intro/)
- [awesome-cli-coding-agents directory](https://github.com/bradAGI/awesome-cli-coding-agents)
- [Every AI coding CLI in 2026 (30+ tools)](https://dev.to/soulentheo/every-ai-coding-cli-in-2026-the-complete-map-30-tools-compared-4gob)
- [Best open-source CLI coding agents 2026](https://pinggy.io/blog/best_open_source_cli_coding_agents/)
- [CLI agent comparison (DevToolLab)](https://devtoollab.com/blog/top-cli-ai-coding-agents)
- [Coding agent leaderboard by Terminal-Bench](https://www.morphllm.com/ai-coding-agent)
