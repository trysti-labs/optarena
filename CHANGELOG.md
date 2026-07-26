# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
OptArena hasn't cut a versioned release yet (pre-PyPI-publish, still
`0.1.0`), so this starts from the current `v0.1` branch state rather than
reconstructing full project history - see `git log` for everything before
this file existed.

## [Unreleased]

### Changed

- Dropped all VS Code/IDE UI-automation drivers (Cline, Roo Code, Continue,
  Kilo Code) and the `ui-harness/`/`legacy/` support code. IDE automation
  drove an evolving third-party UI (selector drift, onboarding-wizard
  churn) - a maintenance surface disproportionate to the value it added.
  The project now focuses on CLI, raw API, and in-process SDK/agent-framework
  drivers only.
- Repo root trimmed to `ARCH.md`/`CHANGELOG.md`/`CONTRIBUTING.md`/`README.md`/
  `SECURITY.md`; working notes and planning docs moved out of git tracking
  entirely (no longer published to either remote).
- Renamed Docker-specific and otherwise-confusing user-facing names now that
  Podman is a first-class engine, no back-compat aliases (pre-release, no
  versioned release has shipped yet): the case-schema field `docker_image` is
  now `image` (renamed across all 510 built-in cases); env vars
  `OPTARENA_DOCKER_IMAGE` -> `OPTARENA_SANDBOX_IMAGE`, `OPTARENA_NO_DOCKER` ->
  `OPTARENA_DISABLE_SANDBOX`, `OPTARENA_NO_PULL` -> `OPTARENA_DISABLE_PULL`
  (the last two also drop the double-negative-prone `NO_` prefix in favor of
  a verb that matches what they actually do). Update any local scenario
  files, case packs, or CI env vars using the old names.

### Added

- Five new in-process SDK/agent-framework drivers: OpenAI Agents SDK,
  smolagents, LangGraph, AutoGen, and Semantic Kernel (alongside the
  existing crewAI driver) - each its own `pip install optarena[<extra>]`.
- `--num-ctx` flag / `backend.num_ctx` scenario field for overriding
  Ollama's context length on the `ollama-chat` driver.
- Podman support alongside Docker: every sandbox command auto-detects
  which container engine is on `PATH` (`OPTARENA_CONTAINER_ENGINE=docker`
  or `=podman` to force one).

### Fixed

- `subprocess_env()`'s allowlist now matches environment-variable names
  case-insensitively and includes `SystemDrive` - both were silently
  dropping Windows env vars some Node-based tools need, causing hard
  crashes or workspace pollution rather than a clean error.
- `openai-agents`, `crewai`, and `langgraph` SDK drivers no longer send
  prompts/telemetry to their frameworks' own hosted tracing endpoints by
  default - a real, quiet leak against the "local-first" design.
- `opencode` driver no longer leaves a temp file with the backend's API key
  behind after every run.
- Docker sandbox images now pin exact dependency versions instead of
  floating tags; the PHPUnit/Composer installers are verified (GPG
  signature / SHA-384) before being executed.
- Three cases' performance-oracle timing budgets
  (`optimize_python_dict_merge_loop`,
  `optimize_java_fib_memoized_recursion`,
  `optimize_csharp_regex_compiled_per_call`) were too tight on fast
  hardware; widened by increasing input size rather than the budget alone.
- Both GitHub Actions workflows only triggered on push to `main`; added
  `v0.1` to their trigger lists so CI and the GHCR image-publish job keep
  running now that `v0.1` is the active branch.
