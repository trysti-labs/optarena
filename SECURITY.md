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
   or any `--cases-dir` pack you load). These execute inside a hardened Docker
   container: `--network none`, `--cap-drop=ALL`,
   `--security-opt no-new-privileges`, `--pids-limit`, read-only rootfs with a
   bounded exec tmpfs, and memory/CPU limits. **If Docker is unavailable,
   OptArena refuses to run them** - host execution requires an explicit
   opt-in (`OPTARENA_NO_DOCKER=1` or `OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1`).
   All case-supplied file paths are containment-checked before anything is
   written; traversal (`../`), absolute paths, and symlinked escapes are
   rejected. Only load case packs from sources you trust enough to run in
   that sandbox.

2. **The coding agents under evaluation.** CLI agents (aider, Claude Code,
   Codex, …) run headlessly with auto-approval flags - that is the point of
   the evaluation - but receive an **explicit environment allowlist** (PATH,
   HOME, locale, temp dirs, plus exactly the provider credential the scenario
   configures), never a copy of your full shell environment. The VS Code UI
   harness gets the same allowlist plus the display/session variables VS Code
   needs to launch. UI extensions are seeded with *workspace-scoped*
   auto-approval: reads/edits inside the workspace and command execution are
   auto-approved; reads/edits **outside** the workspace, the browser, and MCP
   are not. UI workspaces live under the system temp directory, outside any
   repository checkout. The agent process itself is *not* containerized -
   only the verifier is - so an agent can still do what your user account can
   do within those env/approval limits. Don't run agents you don't trust with
   prompts you don't control.

3. **Hidden tests vs. the agent.** `test_setup_files` are never visible to the
   agent while it works, by one of two mechanisms depending on the driver.
   *UI drivers* (VS Code harness): **spatial** isolation - grading runs on a
   private copy of the workspace in a separate directory, mounted into the
   sandbox at a separate bind mount (`/verify`), which the agent's workspace
   never sees. *CLI and baseline drivers*: **temporal** isolation - the hidden
   tests are written into the workspace only *after* the tool process has
   exited, so the agent never runs concurrently with them. In both cases the
   model is graded against tests it could not read or watch during its run.

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
is a remote provider, your prompts and code go there. Nothing else leaves the
machine; there is no telemetry, and the dashboard makes no external requests.
