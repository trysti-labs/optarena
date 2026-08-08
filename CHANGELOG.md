# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This starts from the current `v0.1` branch state rather than reconstructing
full project history - see `git log` for everything before this file
existed.

## [Unreleased]

### Security (tool-call subsystem audit, August 2026)

A focused audit of the tool-use/sandboxed-real MCP subsystem (17 findings,
each verified against code or live behavior) was fixed in full, same
cycle. User-visible behavior changes:

- **`tool_service_seed` paths are now contained** like every other
  case-controlled path field: `files`/`media_files`/`directories` keys go
  through the same traversal/absolute/`.git`/Windows-alias rejection as
  `setup_files` at schema time, plus a resolved-path containment re-check
  at write time in sandboxed mode - previously a malicious case pack
  running sandboxed could write attacker-controlled content to arbitrary
  host paths via `../` or absolute seed keys.
- **Sandboxed execution is user-granted, never case-granted**: a case's
  own `tool_service_mode` can opt DOWN to `mock` but no longer opts UP to
  `sandboxed` - only `--tool-service-mode sandboxed`/the scenario field
  enables it, so an installed third-party case pack can no longer
  self-escalate into container launches. A downgraded case records
  `extra["sandboxed_downgraded"]`; the run manifest uses the same shared
  precedence rule (`cases.resolve_tool_service_mode`) so record and
  behavior can't disagree.
- **`docker`/`kubernetes` sandboxed services now require
  `OPTARENA_ALLOW_HOST_DOCKER=1`**: their real servers reach the actual
  host Docker daemon through the mounted socket, and the effective
  principal is the model under test - that capability is now an explicit
  per-environment opt-in (the `OPTARENA_ALLOW_UNSAFE_HOST_EXEC` pattern),
  refused with a clear message otherwise.
- **The sandboxed MCP container now bind-mounts only the case's own
  directory**, not the shared run root - other cases' workspaces and
  mid-run hidden test files are no longer visible to the (third-party)
  server process, and `cases verify --sandboxed` no longer mounts the
  system temp directory.

### Fixed (same audit)

- **Attached MCP server stderr is now drained** into a bounded tail (an
  undrained pipe deadlocks any server logging >64KB - kind's cluster
  creation writes exactly there) and surfaced in handshake-failure errors,
  which is where every real startup failure actually needed diagnosing.
- **kind cluster teardown no longer leaks host containers**: `close()`
  waits for the server's own graceful `kind delete cluster` trap, then a
  host-side reaper `docker rm -f`s the node container by its derived name
  (clusters are now named after the sandbox, passed via exec env) -
  `docker stop` only ever signals PID 1, so the trap alone provably
  leaked privileged kind nodes on non-graceful paths.
- **Argument-level drift detection**, in two tiers separated by what they
  actually predict: an `arguments_contains` key the real server doesn't
  accept can never match, so it now BLOCKS the case exactly like a missing
  tool; arguments the real server merely newly *requires* are advisory,
  because subset matching plus showing the model the real schema means
  they break nothing (measured: the required-args check fires on all 12
  git_repo tools and predicted zero failures, while the asserted-argument
  check found the single real breakage in the corpus).
- **`git_repo` mock fidelity**: `git_add` took `paths` where the real
  official `mcp-server-git` takes `files` - a divergence from this
  domain's own mirror-the-real-implementation principle, found by diffing
  mock against real. The schema now advertises `files` (`paths` still
  resolves, so out-of-tree cases keep working) and every tool
  accepts-and-ignores the real server's required `repo_path`, so a model
  that supplies it isn't punished for being right. Two shipped cases
  updated; one moved from blocked to sandboxed-ready.
- **Sandboxed-readiness is now reported per service**
  (`8/12 case(s) sandboxed-ready, 4 blocked`). Measured today:
  `filesystem` 13/13, `git_repo` 8/12, `build_tools` 5/13 - the blocked
  remainder is real capability gaps (the official git server has no
  push/pull/blame/tag tools; nx-mcp registers its CI/IDE tools only under
  Nx Cloud or an IDE), not fixable corpus defects.
- **`cases verify --sandboxed` now works for every service**: it handed
  each real server a freshly-created EMPTY probe directory, which
  `mcp-server-git` refuses to start against ("`.` is not a valid Git
  repository") and `nx-mcp` can't treat as a workspace - so the check had
  been silently broken for `git_repo` since it shipped, reporting only an
  opaque handshake timeout. The stderr fix above surfaced the real cause
  on its first use; a per-service probe-setup step (git init / minimal nx
  workspace, throwaway probe directory only) fixes it.
- **MCP client**: answers server-initiated JSON-RPC requests with a
  spec-correct method-not-found error instead of silently dropping them
  (a blocking server would deadlock the session); records the server's
  negotiated `protocolVersion`/`serverInfo` into `CaseResult.extra`;
  response queue is bounded. `tool_service_mode` now requires
  `tool_service` at schema time; per-case sandboxes no longer register in
  the shared check_command routing map; sandboxed `summary()` caps
  per-file/total content with truncation recorded, never silent.

### Added

- **`--tool-service`/`--tags`/`-k`/`--like` case selectors, and `optarena
  cases groups`**: `--language`/`--framework` only ever covered the
  filesystem-oracle case domain, leaving the tool-use domain's 14 mock
  services (266 cases) selectable only by exact `--cases name,name` -
  you had to already know case names. `cases.filter_cases()` gained three
  new keyword-only params, wired identically into `run`, `cases list`,
  and `cases verify` (the same three places `--language`/`--framework`
  already were): `--tool-service` (comma-separated, OR'd within itself -
  e.g. `--tool-service build_tools,observability`), `--tags` (a pytest
  `-m`-style boolean expression over a case's free-form `tags` array -
  `and`/`or`/`not`/parens, e.g. `--tags "tool-use and observability"`,
  parsed by a new small hand-written expression language,
  `_cases/_tag_expr.py`), and `-k`/`--like` (pytest's `-k`, a
  case-insensitive substring match on the case name). `optarena cases
  groups` is new: prints case counts per `tool_service`/`language`/`tags`
  value so a filter can be aimed at something real without guessing. A
  malformed `--tags` expression now reads as a clean one-line CLI error
  (F-04), not a traceback, everywhere `filter_cases` is called - including
  `cmd_verify_corpus`'s call to it, which had no error handling at all
  before this pass.
- **Tool-use cases**: a second case domain alongside the original coding-case
  filesystem oracle - a case names a mock, in-process API service instead of
  `expected_files`/`check_command`, and is graded on whether the agent called
  the right tools with the right arguments (and avoided the wrong ones), not
  on files written. Two new experimental baseline drivers run the real
  request → tool_call → execute-against-mock-service → feed-result-back loop:
  `openai-tools` (`/v1/chat/completions` tool_calls) and `ollama-tools`
  (Ollama-native `/api/chat` tool_calls). Ships with nine mock services -
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
  without an open pending review), 47 cases), and `package_registry` (all
  38 tools the real npm-mcp reference implementation registers server-side
  - install/uninstall/update/outdated/ls/prune/dedupe/fund/explain/sbom/
  query/run-script/audit/doctor/ping/whoami/token/access/owner/dist-tag/
  profile/config/cache/publish/unpublish/deprecate/version/pack/view/
  search/bugs/repo/docs/diff/init/pkg/ci/link - several enforcing real
  npm-like preconditions (`install` refuses a package never published to
  the registry, `ci` refuses without a lockfile, `publish` refuses
  overwriting an already-published version), 35 cases), `terraform`
  (all 55 tools the official `hashicorp/terraform-mcp-server` registers
  across orgs/projects/teams/workspaces/variables/variable sets/policy
  sets/runs/plans/applies/state versions/stacks/no-code workspaces/
  Sentinel mocks and public+private registry search - several enforcing
  real Terraform Cloud-like preconditions (`create_run` refuses a locked
  workspace and locks it on success, `action_run("apply")` refuses a run
  not in a plannable-to-apply state and records a new state version,
  `delete_workspace_safely`/`force_unlock_workspace`/`delete_project`
  each enforce their own real precondition), 31 cases), and `database`
  (all 9 tools the real `crystaldba/postgres-mcp` ("Postgres MCP Pro")
  reference implementation registers - schema/object introspection,
  `explain_query` (with optional hypothetical indexes), `execute_sql`,
  workload- and query-level index tuning, health checks, top-query
  reporting - `execute_sql` refuses write statements in restricted-
  access-mode sessions, `explain_query` refuses combining `analyze` with
  `hypothetical_indexes`, 8 cases), and `ci_pipeline` (all 13 tools the
  official `CircleCI-Public/mcp-server-circleci` registers - followed
  projects, pipeline status, build failure logs, test results with
  pass/fail filtering, flaky tests, artifacts, config validation,
  pipeline triggers, workflow reruns, component rollbacks,
  component-version discovery, usage-API reporting - several enforcing
  real preconditions (`run_pipeline` refuses a multi-pipeline-definition
  project without naming which one, `run_rollback_pipeline` refuses a
  project with no rollback pipeline configured,
  `find_underused_resource_classes` refuses a CSV path never produced by
  `download_usage_api_data`), 13 cases), and `build_tools` (all 13 tools
  the official `nrwl/nx-console`'s bundled `nx-mcp` server registers -
  docs search, plugin listing, project-graph/nx.json introspection,
  per-project configuration and dependencies, generator discovery and
  schemas, project/task-graph visualization, running-task monitoring, and
  Nx Cloud CI pipeline status/logs/self-healing-fix management - several
  enforcing real preconditions (`nx_visualize_graph`'s type-dependent
  required parameters straight from the source, `update_self_healing_fix`
  resolving a fix via ID/short-link/branch and refusing if none resolve),
  13 cases), `code_intel` (all 6 tools the real `isaacphi/
  mcp-language-server` registers - definition/reference lookup,
  diagnostics, hover, rename_symbol, edit_file - several enforcing real
  preconditions (definition/references refuse an unknown symbol,
  hover/rename_symbol refuse a position with no known symbol, edit_file
  refuses an out-of-range line edit), 9 cases), and `observability` (all
  105 tools the official `grafana/mcp-grafana` registers across 30
  category files - by far the largest service in this domain, bigger
  than `forge`'s 77 - dashboards, alerting, datasources, annotations,
  folders, snapshots, plugins, provisioning, incidents, on-call, Sift
  investigations, admin/RBAC, assertions, navigation, rendering, config
  generation, panel-query execution, a generic API passthrough, Agent
  Observability, the Assistant transport, and query connectors for
  Prometheus/Loki/Pyroscope/Elasticsearch/InfluxDB/Graphite/Quickwit/
  CloudWatch/Athena/ClickHouse/Snowflake - roughly 22 categories with
  real source-verified precondition logic (dashboard update's
  full-JSON-vs-JSON-patch mutual exclusion, datasource create/update's
  two-step schema-review confirmation, plugin install's two-step
  version-confirmation flow, and more) plus a shared datasource-type
  check across the ~12 structurally-similar query-connector categories,
  37 cases), and `cloud_infra` (all 9 tools the official `awslabs/
  aws-iac-mcp-server` registers - CloudFormation template validation and
  compliance checking, deployment troubleshooting, pre-deploy validation
  guidance, CDK/CloudFormation documentation and code-sample search, CDK
  best practices, and full-page documentation reads - several enforcing
  real preconditions (template validation refuses malformed JSON or a
  missing Resources section, compliance checking flags
  publicly-accessible resources and wildcard-IAM policy statements,
  deployment troubleshooting refuses an unseeded stack), 9 cases) - plus a
  `tool_service_seed` case field for establishing pre-existing state (e.g.
  real commit history, pre-existing files, already-running containers)
  before the conversation starts, and nested-dict subset matching in
  `expected_final_state` (e.g. checking one specific file's exact content
  without pinning every other file the mock also has). See ARCH.md §3.5.
- **Sandboxed-real tool-use execution** (`--tool-service-mode sandboxed`):
  a tool-use case can now run against the ACTUAL open-source reference MCP
  server, launched inside a disposable, hardened container, instead of only
  the in-process mock - `SandboxedMCPService` (`_cases/_sandboxed_mcp_service.py`)
  satisfies the exact same `MockService` contract (`dispatch`/`call_log`/
  `seed`/`summary`), so `tool_chat.py`'s driver loop and the oracle work
  against it unmodified. Mode is selected case > `--tool-service-mode`/
  scenario field > `"mock"` default, same precedence as the `image` field;
  the manifest records `tool_service_modes` so a mock-mode and a
  sandboxed-mode run are never silently compared as equivalent.
  `DockerSandbox` gained a long-lived attached-process capability
  (`exec_attached`, for a stdio session rather than a one-shot captured
  command) and two narrow, per-image opt-out escape hatches from the
  shared hardening (`extra_run_args`, `network`) - every image not using
  them is completely unaffected. `optarena cases verify --sandboxed` is a
  no-model-call, CI-friendly pre-flight that checks matched cases' tool
  names against the real server's live `tools/list` (one sandbox per
  distinct `tool_service`, not per case). Ten of the fourteen mock
  services now have a real, proven sandboxed counterpart - `filesystem`
  (`@modelcontextprotocol/server-filesystem`), `git_repo` (official
  `mcp-server-git`), `code_intel` (`mcp-language-server` + real `pyright`),
  `build_tools` (official `nx-mcp`), `database` (`postgres-mcp` + a real,
  throwaway Postgres started in-container), `observability`
  (`grafana/mcp-grafana` + a real, throwaway Grafana), `package_registry`
  (`package-registry-mcp`, real network access - the one service with no
  offline substitute, but also no publish/mutate tool at all), `cloud_infra`
  (official `awslabs.aws-iac-mcp-server`, fully offline - no LocalStack
  needed, contrary to the original plan), `docker` and `kubernetes` (both
  via docker-outside-of-docker: the real MCP server talks to the HOST's
  actual Docker daemon through a mounted socket, not a nested/privileged
  inner daemon - `kubernetes` additionally spins up a real, throwaway
  `kind` cluster per case). `task_tracker` has no real reference
  implementation (stays mock-only); `forge`/`ci_pipeline`/`terraform` are
  inherently third-party SaaS and belong to a separate, not-yet-built
  live/bring-your-own-server mode, not this sandboxed one.
  - **Real, live-verified schema drift, not assumed**: every sandboxed
    service's actual tool surface was diffed against its mock - `git_repo`
    (12 real vs 18 mock; every real tool additionally requires a
    `repo_path` argument the mock never modeled), `observability` (65 real
    vs 105 mock - the mock significantly over-approximates the OSS
    server), `build_tools` (7 real vs 13 mock - `nx-mcp` defaults
    `--minimal=true`, hiding most workspace-analysis tools unless
    disabled), `docker` (14/19 overlap), `kubernetes` (20/23 overlap),
    `cloud_infra` (8/9, the closest match found), `database` (9/9 exact),
    `code_intel` (6/6 exact). `package_registry` is total drift (0/38
    overlap) - the mock models an npm-CLI-command-style server; no
    credible real MCP server of that shape exists, only registry-metadata-
    lookup servers, itself a real, useful finding.
  - **Real bugs found and fixed via this work, not hypothetical**: a
    Windows-only `\n`→`\r\n` corruption in `SandboxedMCPService.seed()`
    (fixed with `newline=""`); `mcp-server-git`'s own `mcp>=1.0.0`
    constraint resolves to `mcp==2.0.0`, which breaks it outright (pinned
    `mcp==1.9.4`); Postgres needs `CAP_SETUID`/`CAP_SETGID` to drop from
    root (the default hardened capability set has neither) and a
    `unix_socket_directories` override (its default isn't writable
    under `--read-only`); Docker rejects two `--network` flags outright,
    so an override must replace, not append; a kind cluster name keyed by
    shell PID (`$$`) collided across two sandboxed containers sharing one
    host Docker daemon (fixed with the container's own hostname); Docker
    also refuses `network connect` on a container started in `--network
    none` mode outright, so `kubernetes` needs `network: "bridge"` from
    the start, not just a runtime docker-socket mount.
- **`optarena model` and `optarena agent`**: two opinionated front doors
  onto `run`, so what a comparison actually varies is visible in the
  command instead of inferred from which of ~15 driver names plus
  `--tool-service-mode` you picked. `optarena model --coding|--tool-call
  qwen3-coder:30b gemma4:12b` compares raw models with no agent in the loop
  (it resolves the one baseline driver `--coding`/`--tool-call` + `--kind`
  imply, so a driver name is never typed); `optarena agent --coding
  aider@gemma4:12b goose@qwen3-coder:30b` compares driver+model pairs,
  ZIPPED not crossed, so repeating the driver token is the
  same-agent-different-model case and `--matrix-drivers`'s cross product is
  never implied. Both build a scenario list and hand it to the exact same
  execution/save/auto-compare tail `run` uses - they are not a second
  execution path, and `run` keeps every capability it had (scenario files,
  matrix sweeps, SDK drivers, packs).
- **Tool-use cases can now run through a REAL agent** (`optarena agent
  --tool-call goose@<model> claude-code@<model>`), which makes "does an
  agent help at tool calling?" answerable for the first time - previously
  only `openai-tools`/`ollama-tools` had a tool-calling path at all, and
  the CLI refused `--tool-call` outright. A transparent logging MCP proxy
  sits between the agent and the real sandboxed server, so the existing
  oracle grades the agent's calls unmodified. Sandboxed-only by
  construction (there is no in-process mock an external agent could connect
  to), and limited to agents with verified headless flags that point them
  at exactly one server while ignoring the user's own configured MCP
  servers - `claude-code` and `goose` today; any other driver is refused by
  name. Two real bugs were found by testing this rather than assuming it:
  a message-ordering race that intermittently lost the final tool call
  (the same case graded PASS or "made no calls" run to run), and a Windows
  path-splitting bug that silently mangled a container-engine override.
- **Fixed: the dashboard's comparability check had silently fallen a field
  behind the Python original.** `dashboard/index.html` hand-mirrors
  `compare.manifest_compatibility` in JS, and was missing the
  `tool_service_modes` check - so a mock-mode vs sandboxed-mode run pair
  was correctly blocked in the CLI while the dashboard rendered a clean,
  unsuppressed verdict for the same two runs. A test now asserts the two
  field lists agree, so the mirror can't drift again unnoticed.
- **Comparison output now states what actually differed.** `compare`'s
  table and `regression`'s report (and the dashboard) list the
  evidence-only axes that changed between two runs - driver, driver
  version, model, backend - above the existing compatibility banner.
  These never suppress a verdict (they are what a comparison is FOR), but
  previously a verdict appeared with nothing on screen saying B was a
  different model or agent.

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
