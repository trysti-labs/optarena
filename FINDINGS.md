# OptArena Repository Findings and Improvement Plan

_Audit date: 2026-07-26_

## Scope

This document records the repository architecture review and the findings for:

- reliability and failure recovery;
- performance and scalability;
- Docker and Podman behavior;
- build reproducibility;
- packaging and distribution;
- testing and CI;
- maintainability and operational visibility.

The cybersecurity and API-key assessment is intentionally deferred at the
request of the project owner. Security-specific findings are not included in
this document.

## Executive summary

OptArena has a strong core design for a local-first coding-agent evaluation
tool. Its best qualities are the driver abstraction, real behavioral oracle,
large self-verifying case corpus, run manifests, comparison-validity checks,
and dependency-free Python core.

The primary work needed for production robustness is not a redesign. It is
hardening the lifecycle around the existing architecture:

1. Preserve partial results and always clean up resources after failures.
2. Bound subprocess and HTTP output so one case cannot exhaust memory.
3. Return clean CLI errors for expected user and infrastructure problems.
4. Replace per-check container startup under parallelism with worker pools.
5. Make the result store safe for concurrent and long-running usage.
6. Make sandbox images reproducible and multi-architecture.
7. Package the dashboard, Docker contexts, and starter repositories so normal
   wheel installations contain the complete application.

## Architecture overview

```text
CLI flags / scenario JSON
            |
            v
Scenario and case validation
            |
            v
Driver registry and driver implementation
  |             |                 |
  |             |                 +-- optional in-process SDK drivers
  |             +-- host CLI-agent drivers
  +-- raw OpenAI/Ollama baseline drivers
            |
            v
Per-case temporary workspace
            |
            v
Filesystem checks + behavioral check_command
            |
            v
Docker/Podman language sandbox
            |
            v
RunRecord -> JSON store -> compare/report/dashboard
```

Important components:

- [`optarena/cli.py`](optarena/cli.py): CLI parsing and command dispatch.
- [`optarena/scenario.py`](optarena/scenario.py): scenario and backend models.
- [`optarena/schema.py`](optarena/schema.py): dependency-free validation.
- [`optarena/drivers/`](optarena/drivers/): tool and model integrations.
- [`optarena/cases.py`](optarena/cases.py): workspace preparation, oracle, and
  container execution.
- [`optarena/runner.py`](optarena/runner.py): scenario and trial lifecycle.
- [`optarena/metrics.py`](optarena/metrics.py): aggregate metrics.
- [`optarena/store.py`](optarena/store.py): filesystem result persistence.
- [`optarena/compare.py`](optarena/compare.py): A/B validity and comparison.
- [`optarena/packs.py`](optarena/packs.py): versioned case-pack handling.
- [`dashboard/index.html`](dashboard/index.html): static local dashboard.
- [`docker/`](docker/): offline sandbox images for supported language tracks.

## What is already working well

- The Python core has no mandatory third-party runtime dependencies.
- Driver implementations share one `Driver -> CaseResult` contract.
- Optional SDK dependencies are imported lazily.
- The 510-case corpus is structurally validated.
- Reference and broken solutions provide corpus self-verification.
- Behavioral checks compile and execute generated code rather than relying
  only on content matching.
- Run manifests identify the resolved case set, oracle version, trial count,
  backend, driver, and container images.
- Invalid A/B comparisons suppress aggregate winner claims.
- Serial runs reuse one sandbox container per required language image.
- Docker and Podman use the same container-engine abstraction.
- Base container images are digest-pinned.
- The unit suite covers Windows, macOS, Linux, and Python 3.10-3.12 in CI.

## Prioritized findings

### F-01: Driver preparation is outside the cleanup lifecycle

**Priority:** High  
**Area:** Reliability and cleanup  
**Location:** `optarena/runner.py`, around `driver.prepare()` and the following
`try/finally`

`driver.prepare()` runs before the `try/finally` that stops sandboxes, calls
driver teardown, and deletes the temporary workspace. A missing CLI, missing
SDK package, or preparation exception can therefore leave temporary files
behind and produce a traceback instead of a controlled run failure.

**Recommended change**

- Start the outer `try/finally` immediately after workspace creation.
- Include driver preparation and sandbox startup inside it.
- Make sandbox stop and driver teardown individually best-effort so one cleanup
  error does not prevent the remaining cleanup.
- Add tests for preparation failure, sandbox-start failure, teardown failure,
  and `KeyboardInterrupt`.

### F-02: Completed cases are lost when a run is interrupted

**Priority:** High  
**Area:** Result durability  
**Location:** `optarena/runner.py`, `optarena/cli.py`, and `optarena/store.py`

Case results are assigned to the final `RunRecord` only after the scenario loop
finishes, and the record is saved only after `run_scenario()` returns. An
unexpected failure late in a large or paid run can lose all earlier results.

**Recommended change**

- Create an in-progress run record before the first case.
- Atomically checkpoint after every completed case.
- Record a lifecycle status such as `running`, `completed`, `interrupted`, or
  `infrastructure_error`.
- On success, atomically promote the checkpoint to the final run record.
- Add a `runs recover` or `runs show` path for interrupted records.

### F-03: Subprocess and HTTP response capture is unbounded

**Priority:** High  
**Area:** Memory robustness  
**Location:** `optarena/cases.py`, `optarena/drivers/openai_chat.py`,
`optarena/drivers/cli_agents.py`, and `optarena/packs.py`

Agent output, test output, container output, backend responses, and downloaded
case packs are read fully into memory. The saved run normally retains only a
small tail, but the process has already allocated the complete output.

**Recommended change**

- Stream process output to a bounded buffer or temporary file.
- Retain a configurable head and tail, for example 64 KiB each.
- Store `output_truncated: true` and the original byte count.
- Enforce a maximum backend-response size.
- Enforce a maximum downloaded case-pack size.
- Add tests using a process and HTTP fixture that return oversized output.

### F-04: Expected CLI errors escape as raw tracebacks

**Priority:** High  
**Area:** CLI usability and automation  
**Location:** `optarena/cli.py`

The run command catches `ValueError` around scenario execution, but other
expected exceptions are not normalized. Confirmed examples include an unknown
case name and an invalid `--matrix-drivers` entry.

**Recommended change**

- Validate matrix driver names before creating scenarios.
- Validate inline scenarios using the same rules as JSON scenarios.
- Convert `FileNotFoundError`, `KeyError`, driver availability errors, and
  expected container errors into concise messages and exit code 2.
- Reserve tracebacks for `--debug` mode or truly unexpected defects.
- Add CLI integration tests that assert exit codes and stderr.

### F-05: No whole-case deadline exists

**Priority:** Medium-High  
**Area:** Runtime predictability  
**Location:** driver prompt loops

The documented case timeout is applied separately to each prompt. A case with
three prompts can consume roughly three times the configured timeout, plus
oracle time.

**Recommended change**

- Establish one monotonic deadline per case.
- Pass the remaining budget to every prompt and verification step.
- Optionally expose separate `agent_timeout` and `oracle_timeout` settings.
- Record `agent_duration_s`, `oracle_duration_s`, and total wall duration.

### F-06: Parallel verification loses the shared-container optimization

**Priority:** Medium-High  
**Area:** Container performance  
**Location:** `optarena/runner.py` and `optarena/cases.py`

Serial built-in runs reuse one container per image. Parallel and custom-pack
runs use one ephemeral container per check. With many cases or trials,
container startup and writable-cache initialization may dominate evaluation
time.

**Recommended change**

- Create a bounded pool of worker containers per image.
- Assign one check to a worker container at a time.
- Recycle a worker after timeout or failed health validation.
- Keep custom-pack workspaces isolated while still allowing reuse of the
  container process.
- Measure and report container startup time separately from test time.

### F-07: Result indexing is O(number of historical runs) per save

**Priority:** Medium-High  
**Area:** Storage scalability  
**Location:** `optarena/store.py`

Saving one run rebuilds `index.json` by reading and parsing every historical
run. This becomes progressively slower as the results directory grows.

The atomic writer also uses a predictable `.json.tmp` path, creating a race
between concurrent OptArena processes.

**Recommended change**

Preferred option:

- Use SQLite with WAL mode for runs, cases, summaries, and comparisons.
- Keep the existing JSON export format for portability.

Smaller interim option:

- Incrementally prepend/update one index entry.
- Use unique temporary filenames.
- Use an inter-process lock around index updates.
- Provide `runs rebuild-index` as an explicit repair command.

### F-08: Comparison files can overwrite each other

**Priority:** Medium  
**Area:** Result durability  
**Location:** `optarena/compare.py`

Comparison filenames contain second-resolution time and scenario labels but no
random identifier. Identical comparisons created in the same second can target
the same file.

**Recommended change**

- Add a UUID suffix, as run IDs already do.
- Refuse to overwrite an existing comparison.
- Use the same unique atomic-write helper as run persistence.

### F-09: Trial identity and duration metrics are inconsistent

**Priority:** Medium  
**Area:** Metrics correctness  
**Location:** `optarena/runner.py` and `optarena/metrics.py`

For a future caching driver, the requested trial count is handed to the driver
and then the runner-local count is reset to one before the manifest is built.
The run manifest can therefore claim one trial even when the driver performed
several.

For normal trial runs, the merged case duration is the mean of the trials.
`total_duration_s` then sums those means, so it is not the total amount of work
performed.

**Recommended change**

- Preserve `requested_trials` separately from `runner_trials`.
- Require caching drivers to report the number of trials actually completed.
- Store both mean trial duration and summed trial work.
- Clarify wall-clock duration versus cumulative worker duration under
  parallelism.

### F-10: Infrastructure failures can look like model failures

**Priority:** Medium  
**Area:** Benchmark validity  
**Location:** oracle result handling

An unavailable verification environment or failure to execute the oracle can
be represented as an ordinary case failure. This can incorrectly reduce the
measured pass rate of a driver or model.

**Recommended change**

- Introduce explicit statuses: `pass`, `oracle_fail`, `driver_error`, and
  `infrastructure_error`.
- Exclude infrastructure errors from model accuracy or report both raw and
  adjusted denominators.
- Make regression gates fail separately when infrastructure errors exist.

### F-11: Case-pack installation is not fully transactional

**Priority:** Medium  
**Area:** Pack reliability  
**Location:** `optarena/packs.py`

Pack versions are described as semantic versions but are sorted as ordinary
strings. This can select `1.9.0` over `1.10.0`.

A forced reinstall deletes and rewrites files in place. A crash can leave a
partial mixture of the old and new pack.

**Recommended change**

- Validate and compare semantic versions properly.
- Validate `case_count`, the `cases` object, filenames, and filename
  normalization collisions.
- Install into a temporary sibling directory.
- Validate the staged directory using `load_cases()`.
- Atomically rename the staged directory into place.

### F-12: A wheel does not contain the complete application

**Priority:** Medium  
**Area:** Packaging  
**Location:** `pyproject.toml`

The wheel intentionally includes the Python package and built-in case JSON
files but excludes the dashboard, Docker contexts, and starter repositories.
Commands that depend on those assets therefore require a source checkout.

**Recommended change**

- Access packaged assets through `importlib.resources`.
- Bundle the dashboard and small Docker contexts as package data, or publish a
  separately versioned `optarena-assets` package.
- Decide whether starter repositories belong in package data or downloadable
  versioned packs.
- Add a wheel-install smoke test in a clean virtual environment.
- Replace the deprecated license table with an SPDX license expression.

### F-13: Image builds are not fully reproducible

**Priority:** Medium  
**Area:** Container builds  
**Location:** `docker/`

Base images are digest-pinned and many direct dependencies are pinned, but
several sources of drift remain:

- `pyyaml` is unpinned in the base image;
- apt repositories are not snapshot-pinned;
- npm direct versions do not lock the transitive dependency graph;
- the Rust warmup does not ship a `Cargo.lock`;
- some downloaded tools are versioned but not represented in a shared image
  lock manifest.

**Recommended change**

- Add language lockfiles to every image context.
- Pin the remaining direct packages.
- Generate an `images.lock.json` containing image tags, base digests, package
  versions, and expected published digests.
- Include that lock identity in run manifests.

### F-14: Published sandbox images are effectively AMD64-only

**Priority:** Medium  
**Area:** Docker/Podman portability  
**Location:** `.github/workflows/publish-images.yml` and `docker/Dockerfile`

The image publishing workflow uses ordinary `docker build` on an AMD64 GitHub
runner. The base Dockerfile also downloads the
`terraform_*_linux_amd64.zip` artifact explicitly.

**Recommended change**

- Use Docker Buildx with `linux/amd64,linux/arm64`.
- Select downloads using `TARGETARCH`.
- Test at least the base, Python, Node, and Go images on ARM64.
- Publish one multi-platform manifest for each image tag.

### F-15: Mutable image tags remain the normal execution path

**Priority:** Medium  
**Area:** Reproducible evaluations  
**Location:** `optarena/cases.py` and built-in case definitions

The default runtime tags use `:latest`. Run manifests record the resolved
digest after execution, which is useful evidence, but users cannot easily lock
every per-language image before a mixed-language run.

**Recommended change**

- Support an image map in a scenario or lock file:

  ```json
  {
    "base": "optarena-tester:<immutable-tag>",
    "python": "optarena-tester-python:<immutable-tag>",
    "node": "optarena-tester-node:<immutable-tag>"
  }
  ```

- Resolve all case image aliases through that map.
- Store both requested image reference and resolved digest.

### F-16: Container-engine health is cached too aggressively

**Priority:** Medium-Low  
**Area:** Docker/Podman recovery  
**Location:** `optarena/cases.py`

Container-engine availability is cached for the entire process. A transient
Docker Desktop or Podman-machine startup failure affects every later scenario
in the same matrix run. A failed image pull is also attempted only once per
process.

**Recommended change**

- Cache health checks for a short TTL.
- Reset health state between scenarios.
- Retry transient engine and registry failures with bounded exponential
  backoff.
- Check and report the result of image tagging.
- Add timeouts to container stop and forced removal.

### F-17: CI does not exercise the complete distribution

**Priority:** Medium  
**Area:** CI coverage  
**Location:** `.github/workflows/`

Current CI has good unit-test OS coverage and a corpus verification job, but it
does not cover:

- installation and execution from the built wheel;
- dashboard JavaScript behavior;
- Dockerfile linting;
- rootless Podman;
- ARM64 images;
- container image build cache behavior;
- current Python versions newer than 3.12 while the package declares only a
  lower bound.

**Recommended change**

- Add a wheel-install smoke-test job.
- Add a lightweight dashboard browser test.
- Add Dockerfile lint/build checks for changed image contexts.
- Add one Linux rootless-Podman job.
- Add Python 3.13 and later supported versions.
- Use Buildx cache exports/imports in the image publishing workflow.

### F-18: Operational visibility is mostly console output

**Priority:** Medium-Low  
**Area:** Observability and automation  
**Location:** CLI and runner

The CLI prints useful human-readable status, but there is no structured event
stream, log level, quiet mode, or machine-readable live progress.

**Recommended change**

- Add `--log-level`, `--quiet`, and `--json-events`.
- Emit lifecycle events such as `run_started`, `case_started`,
  `case_completed`, `checkpoint_saved`, and `run_completed`.
- Record agent, oracle, container-startup, and persistence time separately.
- Keep human console output as the default.

## Performance observations

Measured on the audit machine:

- 510 built-in cases.
- Approximately 2.43 MiB of case JSON.
- Full corpus loading and structural validation averaged about 70 ms.
- 501 cases have one prompt; seven have two prompts; two have three prompts.

Case JSON parsing is not a meaningful performance bottleneck at the current
scale. The likely sources of perceived lag are:

1. model or tool execution;
2. container startup in parallel/custom-pack mode;
3. heavyweight language warmups and writable cache initialization;
4. repeated full-workspace hashing after prompts;
5. copying a whole workspace for mid-session behavioral checks;
6. rebuilding the complete results index after every run.

Optimization should focus on those areas before adding complexity to case
loading.

## Docker and Podman improvement plan

### Runtime

- Keep the existing shared-container optimization for serial built-in runs.
- Introduce isolated worker-container pools for parallel runs.
- Add engine-health TTLs and bounded retries.
- Add cleanup timeouts and post-cleanup verification.
- Record container startup, execution, and cleanup durations.
- Add a configurable Podman SELinux mount mode for Linux environments that
  require relabeling.

### Images

- Build multi-platform images with Buildx.
- Use `TARGETARCH` for downloaded binaries.
- Add dependency lockfiles and an image lock manifest.
- Add GitHub Actions layer caching.
- Publish immutable version and commit tags alongside `latest`.
- Allow scenarios to select an immutable tag for every language image.

### Distribution

OptArena is a local CLI rather than a multi-service server, so Docker Compose
is not required for its core operation. If a containerized OptArena CLI is
published later, document how it accesses:

- the user-provided scenario and case directories;
- the local results directory;
- the model backend;
- the host Docker or Podman engine used for nested verification.

## Recommended implementation sequence

### Phase 1: Failure safety

1. Move preparation into the cleanup lifecycle.
2. Add per-case run checkpoints.
3. Normalize expected CLI exceptions.
4. Add cleanup timeouts.
5. Add explicit infrastructure-error statuses.

### Phase 2: Resource control and performance

1. Bound process and HTTP output.
2. Add whole-case deadlines.
3. Implement container worker pools.
4. Separate agent, oracle, startup, and persistence timing.
5. Make result indexing incremental and concurrency-safe.

### Phase 3: Reproducible containers

1. Add image and dependency lockfiles.
2. Add multi-architecture builds.
3. Add Buildx cache support.
4. Add per-language immutable image selection.
5. Add rootless-Podman CI.

### Phase 4: Distribution and maintainability

1. Package all required assets.
2. Add wheel-install smoke tests.
3. Add dashboard tests.
4. Add structured progress events.
5. Update architecture documentation and packaging metadata.

## Verification completed during the audit

- `201/201` unit tests passed with the repository's CI-equivalent
  `OPTARENA_NO_DOCKER=1` configuration.
- All 510 built-in cases passed structural validation.
- Python bytecode compilation completed successfully.
- Wheel and source-distribution builds completed successfully.
- The package build reported setuptools deprecation warnings for the current
  license metadata.
- Docker was installed, but the daemon was not running.
- Podman was not installed.
- Container image builds and the complete behavioral corpus could therefore
  not be rerun locally during this audit.

## Suggested completion criteria

The project can reasonably be called operationally robust when:

- an interrupted run preserves every completed case;
- expected user errors never print a traceback by default;
- subprocess and HTTP memory usage has explicit bounds;
- result persistence is safe under concurrent runs;
- parallel execution reuses bounded worker containers;
- trial and duration metrics describe actual work consistently;
- Docker and Podman paths are covered by real CI;
- published images support AMD64 and ARM64;
- evaluations can pin every sandbox image before execution;
- a wheel installation supports the dashboard, Dockerfile lookup, and
  repository-scale cases without requiring the original source checkout.
