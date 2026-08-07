# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This starts from the current `v0.1` branch state rather than reconstructing
full project history - see `git log` for everything before this file
existed.

## [Unreleased]

### Added

- **Tool-use cases**: a second case domain alongside the original coding-case
  filesystem oracle - a case names a mock, in-process API service instead of
  `expected_files`/`check_command`, and is graded on whether the agent called
  the right tools with the right arguments (and avoided the wrong ones), not
  on files written. Two new experimental baseline drivers run the real
  request → tool_call → execute-against-mock-service → feed-result-back loop:
  `openai-tools` (`/v1/chat/completions` tool_calls) and `ollama-tools`
  (Ollama-native `/api/chat` tool_calls). Ships with six mock services -
  `task_tracker` (create/complete/list/delete, 5 cases), `git_repo` (all
  18 tools from the real MCP git-server ecosystem: status/add/reset/commit/
  diff variants/log/show/branch operations/blame/remotes/tags/push/pull,
  12 cases), `filesystem` (all 13 tools from the official MCP filesystem
  server: read/read-media/read-multiple, write, edit, create-directory,
  list/list-with-sizes, move, search, directory-tree, get-info,
  list-allowed-directories, 13 cases), `docker` (25 tools across
  containers/images/networks/volumes/system, several enforcing real
  Docker-like preconditions - can't remove a running container, can't
  remove an image/volume still referenced - 15 cases), and `kubernetes`
  (23 of the 24 tools the reference `Flux159/mcp-server-kubernetes`
  implementation exposes - kubectl get/describe/create/apply/delete/logs/
  context/scale/patch/rollout, explain/list-api-resources, port-forward/
  exec, Helm install/upgrade/uninstall/template, pod cleanup, node
  cordon/drain/uncordon, ping - several enforcing real kubectl/Helm-like
  preconditions (`kubectl_create`/`install_helm_chart` refuse a duplicate
  while `kubectl_apply`/`helm_template_apply` upsert; draining a node
  refuses without explicit confirmation), 19 cases), and `forge` (all 77
  tools the official `github/github-mcp-server` exposes across 17
  toolsets - Actions, Code Quality, Code Security, Context, Copilot,
  Dependabot, Discussions, Gists, Git, Issues, Labels, Notifications,
  Organizations, Projects, Pull Requests, Repositories, Secret Protection
  - several enforcing real GitHub-like preconditions (`issue_write`/
  `label_write`/`projects_write` create-vs-update by id presence,
  `merge_pull_request` refusing a draft/closed/unresolved-
  REQUEST_CHANGES pull request, `add_comment_to_pending_review` refusing
  without an open pending review), 47 cases) - plus a
  `tool_service_seed` case field for establishing pre-existing state (e.g.
  real commit history, pre-existing files, already-running containers)
  before the conversation starts, and nested-dict subset matching in
  `expected_final_state` (e.g. checking one specific file's exact content
  without pinning every other file the mock also has). See ARCH.md §3.5.

## [0.1.0] - 2026-08-03

First tagged release. Three external/internal review rounds against the live
source (each independently re-verified against real code and infrastructure,
not taken on trust) found and closed a real security/product-readiness gap
list before this tag - see ARCH.md §10.3 for the architectural summary.

### Fixed (August 2026 security/release hardening)

- **Publish pipeline**: image publication is now structurally gated on a
  successful vulnerability scan of the exact digest being published (it
  previously ran as a separate, unlinked workflow); each platform
  (`linux/amd64`, `linux/arm64`) is scanned independently before being
  combined into one multi-arch manifest, and `:latest` is promoted only
  after signing and attestation both succeed, not before.
- **Vulnerability baseline**: matching identity now binds to installed
  version, scan target, and package type (not just CVE + package name), so
  a version bump or a newly-available fix correctly invalidates a stale
  acceptance; a Trivy disposition-status change toward "affected"/"fixed"
  does too.
- **Secret redaction** now removes complete multiline PEM private-key
  blocks (body and footer), not just the header line.
- **Path containment** extended to every reactive-disruption trigger field
  and hardened against Windows alias forms (trailing dot/space, alternate
  data streams, reserved device names) across all case-controlled paths.
- **Case-pack authenticity**: real `ssh-keygen`-based publisher signing and
  an explicit local trust keyring, not just a content hash; signature
  verification is bound to a fixed namespace (never one the pack itself
  claims) and installed-pack trust is re-derived from disk on every run
  rather than cached at install time.
- **Workspace resource containment**: a soft disk/file quota now covers the
  agent's own execution phase, not just `check_command` - closing three
  real bypass channels (a fast writer finishing inside one poll interval,
  directory-only entries not counting toward the quota, and the driver
  phase being entirely unwrapped).
- **Reproducibility**: run manifests now record effective generation
  parameters (temperature/top_p/seed, honored by both baseline drivers and
  all six SDK drivers), a best-effort backend/provider version, and a
  build-commit fallback for wheel installs with no `.git` directory.
  `ORACLE_VERSION` gained a concrete bump-vs-don't-bump decision procedure.
- **Live CI coverage** extended to all six optional SDK drivers (a
  zero-secret local-Ollama smoke job), plus a hard version-drift gate for
  every driver pinned to a tested version.
- Precise remediation messages (not a bare "not found") when a
  source-checkout-only feature (`setup_repo`, `optarena sandbox build`) is
  used from a wheel install.
- Raw vs. eligible/adjusted pass-rate denominators are now shown in the CLI
  console, HTML reports, and comparisons (previously dashboard-only),
  including a real "common eligible case set" computation across two runs.

### Added

- `GOVERNANCE.md`: maintainers, decision process, security-response
  ownership, support channels, supported branches, and the deprecation/
  compatibility policy for the case schema, drivers, results, and oracle
  versions.

### Fixed (second internal audit - historical findings archived in DEV_NOTES/, not part of this public repo)

- **`--parallel` no longer loses completed work.** Checkpoints during a
  parallel run serialized zero cases (the completion callback closed over a
  list the parallel runner never appended to), so interrupting one discarded
  every finished case - the exact scenario checkpointing exists for.
- **A parallel worker exception no longer hangs the run forever.** An
  exception inside a worker killed that thread without queuing a result,
  leaving the collector blocked on a fixed-count `get()` with no timeout. It
  is now reported as that case's error, and the collector fails loudly if
  every worker dies with results outstanding.
- **`optarena regression` no longer invents regressions.** A case present in
  the baseline but absent from the candidate run (a `--cases`/`--language`
  subset) was counted as a regression and failed the CI gate; only-in-one-run
  cases are now reported as their own category.
- **The six SDK drivers reached parity with the CLI/baseline drivers.** They
  had no timeout of any kind (a hung backend hung the run indefinitely), never
  fired mid-session `disruptions` (so dynamic cases silently graded an easier
  task than the same case under a CLI driver), and recorded no per-step,
  token, or cost telemetry. The shared loop now lives in
  `drivers/sdk_base.py`; each driver file is just "how do I ask this SDK for
  one completion".
- `setup_repo` is containment-checked against `repos/` (an untrusted case
  could previously copy an arbitrary host directory into the agent-visible
  workspace), and a case's `image` is validated as an image reference so it
  cannot smuggle flags onto the container engine's command line.
- The `aider` driver passes the backend key through the environment instead of
  `--openai-api-key` in argv, where any local user could read it.
- Legacy command aliases work again behind a global flag
  (`optarena --debug list runs`), and `cases verify` reports an unknown
  `--cases` name as a clean error instead of a traceback.
- Results no longer default into `site-packages` for a non-editable install;
  they go to `~/.optarena/results` unless run from a source checkout.
- Smaller: report HTML escapes after truncating (no half-written entities),
  `index.lock` is only deleted by the process that acquired it, engine-health
  and image-pull backoff state reset per scenario, and the pricing table
  reloads when `OPTARENA_PRICING` changes.

### Added

- `optarena --version`, and the tool version in every run manifest.
- `optarena runs rebuild-index` (referenced by three docstrings but never
  implemented), `optarena runs prune`, and `optarena runs scrub-secrets` for
  records written before backend redaction existed.
- `optarena doctor --json`, plus a check for the three directories a wheel
  install does not carry (`dashboard/`, `docker/`, `repos/`).
- `optarena cases verify --strict`: fails on cases that declare nothing which
  must FAIL, i.e. whose oracle is never proven to discriminate.
- `case_sensitive: true` on an expected-file spec - content was lowercased and
  matched case-insensitively, so a case could not require `class UserDTO` over
  `class userdto`.
- `optarena serve --host`, with a warning when it is not localhost, and a
  clean error on a port already in use.
- `optarena report --out-dir`; `--out` now means exactly one file.
- Every sandbox image ships an unprivileged uid 1000 account with readable
  toolchain caches, so `OPTARENA_SANDBOX_USER=1000:1000` works (validated for
  the base, JVM, .NET and Rust tracks; a CI job exercises it on Linux).
- `tests/test_cli.py` (CLI exit-code contracts) and `tests/test_conformance.py`
  (the Python/JS cross-oracle suite CI already claimed to run - it did not
  exist). Suite: 240 -> 350+ tests.

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
