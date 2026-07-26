# Security

## Reporting a vulnerability

Please email **arun@trysti.com** with details (affected file/function, a
reproduction, and impact). Do not open a public issue for anything
exploitable; you'll get an acknowledgment within a few days and credit in the
fix commit unless you prefer otherwise.

## Trust model

OptArena runs three kinds of untrusted-or-semi-trusted code, each with its own
boundary:

1. **Case-defined `check_command`s and test files** (from the built-in corpus
   or any `--cases-dir` pack you load). These execute inside a hardened
   container (Docker or Podman, auto-detected -
   `OPTARENA_CONTAINER_ENGINE` forces one): `--network none`, `--cap-drop=ALL`,
   `--security-opt no-new-privileges`, `--pids-limit`, read-only rootfs with a
   bounded exec tmpfs, and memory/CPU limits. **If no container engine is
   available, OptArena refuses to run them** - host execution requires an
   explicit opt-in (`OPTARENA_DISABLE_SANDBOX=1` or `OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1`).
   All case-supplied file paths are containment-checked before anything is
   written; traversal (`../`), absolute paths, and symlinked escapes are
   rejected. Only load case packs from sources you trust enough to run in
   that sandbox.

2. **The coding agents under evaluation.** CLI agents (aider, Claude Code,
   Codex, …) run headlessly with auto-approval flags - that is the point of
   the evaluation - but receive an **explicit environment allowlist** (PATH,
   HOME, locale, temp dirs, plus exactly the provider credential the scenario
   configures), never a copy of your full shell environment. SDK/agent-
   framework drivers (crewAI, OpenAI Agents SDK, smolagents, LangGraph,
   AutoGen, Semantic Kernel) run **in-process** instead of as a subprocess,
   so the environment allowlist doesn't apply to them the same way - each
   constructs its own explicit client scoped to the scenario's own backend
   URL/key rather than reading ambient credentials, and none are given file
   or shell tools (they reply with a single code block, which the driver
   writes to disk itself). Neither CLI nor SDK agent processes are
   containerized - only the verifier is - so an agent can still do what your
   user account can do within those env/tool limits. Don't run agents you
   don't trust with prompts you don't control.

3. **Hidden tests vs. the agent.** `test_setup_files` are never visible to the
   agent while it works: **temporal** isolation - the hidden tests are
   written into the workspace only *after* the tool process has exited, so
   the agent never runs concurrently with them. For multi-prompt cases with
   disruptions, the mid-session per-step attribution check additionally
   grades a **private copy** of the workspace rather than the live one it
   will read again on its next turn. The model is graded against tests it
   could not read or watch during its run.

## Credentials

- `backend.api_key` is **never persisted**: saved runs and comparisons store
  a redacted backend (`api_key: null`, plus an `api_key_set` boolean).
- Prefer `OPTARENA_API_KEY` (environment) over `--api-key` (visible in `ps`
  and shell history).
- `optarena serve` binds `127.0.0.1` and serves only `dashboard/` and the
  results directory - never the repository root - with directory listings
  disabled.

## What OptArena results are

The built-in corpus is public, *including* reference solutions - by design,
so the oracles are auditable and self-verifying (`optarena verify-corpus`).
That makes results trustworthy as **acceptance/regression evidence for tools
you are honestly evaluating**, and unsuitable as a tamper-proof public
leaderboard: a benchmark-aware agent could look answers up. For adversarial
settings, use a private case pack via `--cases-dir`.

## Data flow

Orchestration, results, and the dashboard are local. Prompts and generated
code are sent to whatever backend URL each scenario configures - if that URL
is a remote provider, your prompts and code go there. OptArena's own code has
no telemetry, and the dashboard makes no external requests - but the
in-process SDK/agent-framework drivers each pull in a real third-party
framework, and some of those frameworks ship their own telemetry, enabled by
default, independent of anything OptArena's own `subprocess_env()`
allowlisting can reach (that mechanism only scopes subprocess environments;
these drivers run in-process). The `crewai`, `openai-agents`, and `langgraph`
drivers explicitly disable their framework's default telemetry/tracing in
`prepare()` - confirmed by reading each framework's actual tracing code, not
assumed from its docs - specifically so a scenario aimed at a fully local
backend doesn't silently phone home. If you add a new SDK-agent driver or
bump one of these frameworks to a new major version, re-verify this hasn't
regressed.
