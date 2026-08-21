# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This starts from the current `v0.1` branch state rather than reconstructing
full project history - see `git log` for everything before this file
existed.

## [Unreleased]

### Fixed

- **Flaky `PackSigningTests::test_installed_pack_modified_after_install_is_no_longer_reported_trusted`
  on Linux CI**: the test picked `next(dest.glob("*.json"))` and assumed it
  wouldn't be `_pack.json`, but `install_pack()` writes case files then
  `_pack.json` last, and directory-listing order doesn't have to match
  write order. It happened to hold on Windows/NTFS but not on Linux/ext4,
  where `_pack.json` (the install manifest, not a real case) came back
  first, so the test tampered with the manifest instead of a case file.
  Now filters it out by name explicitly instead of relying on iteration
  order.
- **`docker/vuln-baseline/*.json` re-triaged (2026-08-20)**: a wave of
  freshly-disclosed CRITICAL/HIGH CVEs against packages already in each
  baseline's documented accepted-risk categories (Debian OS-package
  security-patch lag, mainly) were failing CI across all 9 images. Checked
  every new finding's `Fixed Version` in the actual Trivy scan output
  before touching anything: 225 (across all images) have no fix published
  yet and are now accepted into the baseline, matching the existing
  precedent; 19 (`stdlib`/`golang.org/x/*` in the Go toolchain and the
  vendored Terraform binary, `org.apache.httpcomponents` in the JVM image,
  the .NET runtime, and `nanoid`/`brace-expansion`/`ip-address`/`postcss`/
  `js-yaml` in the Node image) do have a real fix and were deliberately
  left out of the baseline - those need an actual dependency/version bump,
  not baseline acceptance, and are still expected to fail CI until fixed.
  `generated_at`/`expires_at` bumped on all 9 files (re-triage cycle).
  Verified with the real `check_vuln_baseline.check()` function against
  reconstructed findings from the actual CI run's Trivy output, not
  assumed.

### Added

- **`CaseResult` carries case metadata**: `language`, `domain`, and
  `task_type` are now copied from the case definition onto every saved
  result (`optarena/runner/_execution.py`), instead of living only in
  `optarena/cases/*.json`. Lets a saved run be filtered or grouped by
  language/domain/category (e.g. in the dashboard) without cross-referencing
  the case corpus. Additive only, not a breaking change to the case or
  run-result shape (see GOVERNANCE.md).

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
