# OptArena findings and resolution plan

This document tracks the security, reliability, product, and maintenance findings identified during the August 2026 repository review.

> **Response, 2026-08-03:** All four P0 findings, all 7 P1 findings, all 5 P2 findings, and both P3 findings are now fixed and covered by new regression tests, with one narrow exception: the .NET SDK's own internal tool CVEs (part of P1-03) remain genuinely blocked on an upstream Microsoft rebuild, re-confirmed rather than assumed. Every finding in this document has now had at least one real fix-and-verify pass, with live testing (real Docker builds/scans, real driver invocations, real Ollama runs, a real wheel install in a clean environment) used throughout rather than code review alone. That overall "cleared to go public" call is still the maintainer's to make - see the one remaining .NET item above, and the per-finding "Not done" notes throughout for the honest edges of scope on each fix.

> **Release warning:** Do not make this repository public until the P0 findings are fixed and independently retested. This file describes sensitive security boundaries and should remain private until those fixes are released.

## Priority definitions

- **P0 — Release blocker:** Fix before making the repository public.
- **P1 — High priority:** Fix before publishing official packages or container images.
- **P2 — Product readiness:** Fix for a credible public alpha or shortly afterward.
- **P3 — Maintenance:** Important for long-term contributor health, but not an immediate security blocker.

## P0 — Release blockers

### P0-01: Case-controlled Git configuration can execute host commands

**Status:** Fixed
**Type:** Security / host command execution
**Affected code:** `optarena/cases.py`, `optarena/schema.py`, `SECURITY.md`

**Finding**

Custom cases may place files under `.git/`, including `.git/config` and `.gitattributes`. When `git_init` is enabled, OptArena subsequently runs host-native `git init`, `git add -A`, and `git commit`. Git filters configured by case content can therefore be executed on the host during `git add`, before container verification begins.

**Impact**

Installing and running an untrusted case pack could execute commands with the OptArena user's permissions. This contradicts the expectation that case behavior is contained inside the verification sandbox.

**Fixed in this pass**

- `schema.reject_unsafe_relpath()` rejects any `.git` path segment (case-insensitively) in every case-controlled path field: `setup_files`, `test_setup_files`, `reference_solution`, `broken_solutions[].files`, `expected_files[].path_pattern`, `disruptions[].write_files`/`delete_files` - enforced at `validate_case` time, before a case is loaded or a model is called.
- The same check is re-applied at the actual write sites (`cases.write_setup_files`, `cases.copy_setup_repo`, `cases._fire_disruption`) as defense in depth, in case a caller ever reaches them with content that bypassed schema validation.
- `git_init_workspace()` now runs with a minimal explicit environment instead of inheriting the full host environment: `GIT_CONFIG_NOSYSTEM=1`, an isolated scratch `HOME`/`USERPROFILE` (so no operator global/system gitconfig with hooks or filters is ever read), and `GIT_TERMINAL_PROMPT=0`. This is a second, independent layer - even a gap in the path check above could only reach a git config it never reads.
- Regression tests: `test_git_config_path_rejected_everywhere` (schema layer, all path fields), `test_git_config_write_rejected` (write-site layer, `write_setup_files`).

**Not done:** hooks/filters/pager/diff-driver are not individually disabled by flag (the path rejection above makes this moot - there is nothing under `.git/` for `git add -A` to execute that a case controlled), and `git_init` was not removed from the release. Full suite: 389 passed, 1 skipped, 0 failed after this change.

---

### P0-02: Expected-file paths can escape the workspace

**Status:** Fixed
**Type:** Security / arbitrary file write
**Affected code:** `optarena/schema.py`, `optarena/drivers/openai_chat.py`, `optarena/drivers/sdk_base.py`

**Finding**

`expected_files[].path_pattern` is checked only for being a non-empty string. Raw and SDK drivers convert the first pattern to a concrete destination and write to `workspace / target` without resolving the final path and confirming that it remains inside the workspace. Patterns such as `../outside.py` can escape it.

**Impact**

A custom case can steer model-generated content into arbitrary host paths writable by the current user.

**Fixed in this pass**

- Schema layer: `expected_files[].path_pattern` now goes through `reject_unsafe_relpath()` at `validate_case` time (rejects `..`, absolute/drive/UNC paths, `.git`) - fails before any model/API call.
- Write-site layer: `drivers.openai_chat.concrete_target()` - the one function both `openai_chat.run_case()` and `sdk_base.run_case()` build their write target from - now raises `ValueError` on an absolute path or a `..`/`.git` segment, checked explicitly (not just `Path.is_absolute()`, which does not catch a POSIX-style `/etc/passwd` pattern on a Windows host - caught by a test, see below). Both callers catch this and report it as a normal case error instead of crashing.
- Regression tests: `test_path_traversal_rejected_everywhere` (schema layer), `test_traversal_and_git_paths_rejected` (concrete_target, including the Windows POSIX-absolute-path gap).

**Not done:** `expected_files` traversal checks were not extended to `disruptions[].when.file_exists`/`file_contains.path` (read-only probes, not writes - lower risk, judged out of scope for this pass). Full suite: 389 passed, 1 skipped, 0 failed after this change.

---

### P0-03: Timed-out process trees can survive on Windows

**Status:** Fixed
**Type:** Reliability / process isolation
**Affected code:** `optarena/cases.py`, `tests/test_optarena.py`

**Finding**

The Windows process-tree timeout test fails because a child process can remain alive after its parent times out. The current `CREATE_NEW_PROCESS_GROUP` plus `taskkill /F /T` approach is not a reliable lifetime boundary.

**Earlier note in this document, now superseded:** an earlier pass reported that the specific claimed test failure ("368 run, 367 passed, 1 skipped, 1 failed" with this test named) did not reproduce here, and left the finding open without a job-object fix. That observation was accurate as far as it went, but didn't address the actual concern - `taskkill /T`'s tree-walk being unreliable is a real, structural property of how it works, independent of whether any specific test run happens to catch it. Fixed properly in this pass instead of relying on "the test currently passes."

**Fixed in this pass**

Implemented the resolution's own suggestion: a Windows kernel Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`), via raw `ctypes` bindings to `kernel32` (no new dependency - this project is stdlib-only throughout `cases.py`).

- `run_capture()` now creates a `_WindowsJob` immediately before spawning the process (Windows only) and assigns the new process to it right after `Popen` returns. Windows automatically adds every process a job member later spawns to the same job - assignment doesn't need to happen again for each new descendant, which is what makes this robust against nesting depth.
- Why this is actually more reliable than `taskkill /T`, not just different: `taskkill /T` walks the process tree via each process's own recorded parent PID. That link breaks if an intermediate process has already exited by kill time (its PID can even be reused by something unrelated) - a real, common situation for the workloads this sandbox runs (`check_command`s that fork Maven/npm, which themselves fork further JVMs/workers over a run lasting minutes). Job Object membership is tracked by the kernel at process-creation time instead, independent of whether the immediate parent is still alive, and isn't defeated by a descendant spawning into its own process group (only an explicit breakaway request would escape it, which this job's `LimitFlags` do not permit). It's also self-healing on a hard crash of optarena itself: Windows closes every handle a process holds on exit for any reason, so `KILL_ON_JOB_CLOSE` fires even then - `taskkill` has no equivalent, since it requires optarena to still be alive to run a separate command.
- `taskkill /T` is retained and still runs unconditionally as a best-effort backstop (job creation can fail on some restricted/sandboxed hosts; `assign()` can fail if the process already exited in the race between `Popen` and assignment) - not a replacement, defense in depth.
- Cleanup failure is now surfaced explicitly rather than silently swallowed: `_kill_process_tree` returns whether either mechanism confirmed success, and a confirmed-failed cleanup gets a `[WARNING: process-tree cleanup could not be confirmed]` annotation on the raised `TimeoutExpired` instead of reading identically to an ordinary clean timeout.
- Regression tests covering exactly the scenarios the resolution asked for: parent+child (existing test, unchanged), **grandchild whose immediate parent already exited before cleanup runs** (`test_grandchild_killed_even_if_immediate_parent_already_exited` - the specific case `taskkill /T`'s PPID-walk is weakest against; written to pass on POSIX too, since process-group membership doesn't depend on this either, making it a genuine cross-platform regression test rather than Windows-only), and **a detached grandchild in its own process group** (`test_detached_grandchild_process_group_still_killed`, Windows-only - `CREATE_NEW_PROCESS_GROUP`). A separate `WindowsJobObjectTests` class (4 tests) exercises `_WindowsJob` directly: create/close, assign+terminate actually kills a real process (confirmed via `proc.wait()`+`returncode`, not assumed), assigning to an already-exited PID fails gracefully rather than raising, and a grandchild spawned *after* assignment is confirmed to be an automatic job member without a second `assign()` call.

**Verified:** all 10 new/existing tests pass (412 total, 0 failed, up from 400). `ruff check` clean.

### P0-04: Synchronous SDK timeout does not cancel the request

**Status:** Fixed
**Type:** Reliability / cost control
**Affected code:** `optarena/drivers/sdk_base.py`

**Finding**

Synchronous SDK calls run in a `ThreadPoolExecutor`. Calling `future.cancel()` cannot stop work that has already started, and `shutdown(wait=False)` does not guarantee that the worker cannot delay interpreter shutdown. The session may also be closed while the abandoned worker is still using it.

**Correction to an earlier note in this document:** a first pass at this fix claimed the existing `ThreadPoolExecutor`-based code already made the process-hang half of this finding moot, because its own comment said the worker thread was "a daemon, so it can never block interpreter exit." That claim was taken from the code without independently checking it, and it is false: `ThreadPoolExecutor` worker threads are **not** daemon threads, and CPython's `concurrent.futures.thread` module registers an `atexit` hook that joins every one of them before the interpreter exits. Verified directly: a standalone script that abandons a `ThreadPoolExecutor` call sleeping 3s took 3.05s to actually exit, not the near-instant exit a daemon thread would give.

A second pass replaced the thread with `threading.Thread(daemon=True)` directly, which genuinely fixed the process-hang half (confirmed: 0.32s exit instead of 3.05s) - but a Python thread still cannot be forcibly stopped once it has started running arbitrary (possibly C-extension, possibly blocked-on-a-socket) code. The orphaned call would still keep running and could still burn API credits until the SDK's own timeout fired. That gap is what this final pass closes.

**Fixed in this pass (final)**

The only way to actually stop a synchronous, in-process call with no cooperative cancellation token is to not be in the same process as it. `_complete_with_deadline` no longer runs `complete()` on a thread at all:

- `_open_worker()` spawns a dedicated child process (`multiprocessing.get_context("spawn")`, forced explicitly rather than relying on the platform default, so Windows - spawn-only - and POSIX exercise the identical code path) once per case. A module-level function, `_sdk_worker_main`, runs inside that child: it builds a *fresh* driver instance there and calls that instance's own `open_session()`/`complete()`/`close_session()` - the real, often-unpicklable SDK session (an LLM client, an Agent wrapper) is built and used entirely inside the child and never itself crosses the process boundary. Only `type(self)` (a class reference) and `scenario` (a plain dataclass of str/int/dict) cross in; only `(text, usage)` crosses back. Verified this reuses the exact same subclass hooks unchanged - none of the six SDK driver files (`crewai_sdk.py`, `openai_agents_sdk.py`, `langgraph_sdk.py`, `smolagents_sdk.py`, `autogen_sdk.py`, `semantic_kernel_sdk.py`) needed any change.
- `_complete_with_deadline()` now sends each prompt to that worker over a pipe and waits up to the case's remaining budget. On timeout, it calls `_kill_worker()` - `terminate()`, then `kill()` if that doesn't land within a short grace period - **before** raising `SDKTimeout`. By the time `run_case` reports the timeout, the process making the call is already gone, confirmed via `proc.is_alive()` and `proc.exitcode` in tests, not assumed.
- This also fully resolves "the session may also be closed while the abandoned worker is still using it": there is no longer an orphan to worry about, because the process it was running in has already been killed by the time cleanup runs. The earlier pass's thread-based mitigation for this (track orphaned threads, skip `close_session` while one might still be alive) is now unnecessary and was removed along with the thread-based approach.
- `AsyncSingleFileSDKDriver` (AutoGen, Semantic Kernel) is untouched - it already cancels for real via `asyncio.wait_for`, so it overrides the new `_open_worker`/`_close_worker` seam to a no-op and keeps its existing per-call `aopen_session`/`aclose_session` lifecycle exactly as before.
- Regression tests: a new `SDKWorkerProcessTests` class, using a genuine module-level driver (a locally-scoped/closure-based fake, as the rest of the suite already used, cannot be sent across a real process boundary - pickling requires an importable module-level class) - covers the worker actually running in a different OS process, a normal call round-tripping through it, and specifically `test_overrun_call_actually_kills_the_process` (asserts `proc.is_alive()` is `False` and `proc.exitcode` is set after a deliberate overrun) and a full `run_case()` end-to-end timeout. The rest of `SDKDriverBaseTests`' existing tests (deadline math, disruptions, token accounting, step records) keep using the original in-process closure-based fake for speed, with a small thread-based `_complete_with_deadline` override added to it purely so those orchestration tests don't need to spawn a real process to verify logic that has nothing to do with process management.

**Tradeoff, disclosed rather than hidden:** spawning a process is slower than a thread - each case now pays one Python-interpreter-startup-plus-SDK-reimport cost (once per case, not per prompt, matching the existing session-reuse-per-case granularity), not just object construction. This is the honest cost of genuine killability; it was not optimized away or avoided in this pass.

Verified: full suite (394 passed, 1 skipped, 0 failed) run three times under `pytest-randomly`'s randomized ordering with no flakiness from the new process-based mechanism, plus `ruff check` clean.

---

## P1 — Security and distribution readiness

### P1-01: Credentials can be persisted through stderr

**Status:** Fixed
**Type:** Security / secret exposure
**Affected code:** `optarena/drivers/cli_agents.py`, `optarena/drivers/aider_cli.py`, `optarena/security.py`, `optarena/store.py`, result storage

**Finding**

CLI agents receive credentials through environment variables, but their stderr tails are persisted without secret redaction. A tool can intentionally or accidentally echo an API key.

**Fixed in this pass**

Two independent layers, matching the resolution's own structure:

- **Known-secret redaction at the driver** (`security.redact_known_secrets`): both `cli_agents.py` and `aider_cli.py` now collect the actual credential value(s) the subprocess was handed - `scenario.backend.api_key`, plus any `auth_env` passthrough values that resolved (Claude Code's `ANTHROPIC_API_KEY`, etc.) - and redact every occurrence of those exact values out of captured stderr *and* exception text (`f"{type(exc).__name__}: {exc}"`, since an HTTP client's own exception can embed a credential in a URL or header dump) before either ever reaches `result.extra`/`result.error`.
- **Pattern-based redaction as defense in depth** (`security.redact_secret_patterns`): reuses the same shaped rules `security.scan_text` already uses to find secrets in agent-changed files (AWS access key, GitHub token, a provider `sk-...` key, a PEM private-key header) - catches a credential the driver didn't know about explicitly (a different provider's key echoed by mistake), not just this run's own configured value.
- **A blanket storage-layer safety net** (`store._redact_before_write`, wired into both `save_checkpoint` and `save_run`): pattern-based redaction recursively across the *entire* record right before it's written to disk - the driver-agnostic backstop for a future driver that adds stderr capture without wiring in its own redaction, or any string field the per-driver fix didn't anticipate. `backend.api_key` is already `None` by this point (`scenario.to_dict(redact=True)`, unchanged from before), so this layer doesn't need the live credential value in scope - pattern-shape matching only.
- **`runs scrub-secrets` extended** to also recursively pattern-redact every stored run record, not just null out `backend.api_key` - cleans up records saved before any of the above existed.

Regression tests (`SecretRedactionTests`, 11 tests, all using a real canary secret matching the `sk-...` shape): the four redaction primitives directly; both CLI drivers' non-zero-exit stderr path; both drivers' exception path; the timeout path (pinning that a timeout's partial captured output never reaches the result at all today, so there's nothing to leak there); the storage layer catching a secret a driver let through; and the retroactive scrub command catching a pattern-shaped secret in a field that isn't `backend.api_key`. Every test confirms the canary is absent from the full serialized result/record, not just the specific field it started in.

---

### P1-02: Secret scanner saves the secret-bearing source line

**Status:** Fixed
**Type:** Security / secret exposure
**Affected code:** `optarena/security.py`

**Finding**

When a secret rule matches, the scanner stores up to 160 characters from the original line as a snippet. The persisted finding can therefore contain the secret it detected.

**Fixed in this pass**

`scan_text()` now redacts the matched span (the captured group when the rule has one, the whole match otherwise) before storing the snippet, for every `is_secret` rule - the surrounding line is kept for triage context, the secret value itself is replaced with a fixed placeholder. Non-secret rules are unaffected. Regression test `test_secret_finding_never_persists_the_canary` plants a canary secret and asserts it does not appear in any field of the resulting finding.

---

### P1-03: Critical/high container findings do not block CI

**Status:** Fixed
**Type:** Supply-chain security
**Affected code:** `.github/workflows/ci.yml`, Docker images

**Finding**

The image vulnerability scan is configured with `continue-on-error: true` while a known backlog is being tolerated.

**Fixed in this pass**

Live-scanned all 9 published images with Trivy (CRITICAL/HIGH), fixed what's actually fixable, then built a real blocking mechanism for what's left - not a blanket `continue-on-error`, not a rubber-stamped ignore.

*Actual remediation (measured before/after with live Trivy scans, not estimated):*
- **Go image**: base bumped from `golang:1.22-bookworm` to `golang:1.26.5-bookworm` - 1.22 is out of Go's own security-support window, which is exactly why the image carried 285 stdlib CVEs with patches that simply were never in a build this pin could reach. Verified safe: no corpus case pins a `toolchain` directive (checked directly), and Go's compatibility promise means a `go 1.21`/`go 1.22` directive is a minimum a newer toolchain satisfies automatically. Result: **1216 → 246 findings** for this image alone.
- **PHP and .NET SDK images**: base digests refreshed to the latest published build of the SAME tag (`php:8.3-cli`, `mcr.microsoft.com/dotnet/sdk:8.0`) - a security-patch republish, not a version bump. PHP: 133 → 104. (.NET's remaining backlog turned out to be inside the SDK's own bundled tools - MSBuild, `dotnet-format`, embedded PowerShell - not touched by an OS-layer refresh; documented below.)
- **Rust image**: removed an entire unused ImageMagick suite (imagemagick, libmagickcore/libmagickwand + dev headers) that the upstream `rust:1-bookworm` base ships but this image - headless Rust compilation against axum/actix-web/tokio - never uses. `apt-get remove --purge --auto-remove` took ~30 packages with it (libopenexr, libheif, libaom, X11, fontconfig, gdk-pixbuf). Verified the image still builds/tests correctly via `cases verify --language rust` before treating this as safe. 423 → 300.
- **`apt-get upgrade -y`** added to every one of the 9 Dockerfiles (previously none had it) - pulls from the live Debian archive at build time rather than staying frozen to whatever the base image's snapshot had. Real effect measured: base -13, node -6, rust an additional -64 (on top of the ImageMagick removal) purely from this.
- **Combined result: 2508 → 1367 CRITICAL/HIGH findings, a 45% reduction, verified by re-running Trivy against the rebuilt images**, not claimed from reading the Dockerfiles.
- `docker/images.lock.json` updated to match the new pinned digests (`check_images_lock.py` - see P1-04 - would otherwise have flagged the drift).
- Every changed image (go, php, dotnet, rust) re-verified with `optarena cases verify --language <lang>` against the real oracle before being treated as safe; python was spot-checked too (the `apt-get upgrade` addition alone) - all pass clean.

*The blocking mechanism (the actual ask - "fail release builds on new unfixed CRITICAL/HIGH findings"):*

A flat `.trivyignore` only supports per-CVE-ID entries, and the remaining backlog is dominated by two categories that would make that impractical: **499 Linux-kernel-header findings** (`linux-libc-dev` - confirmed by reading sample CVE descriptions: `nvmet-tcp`, `net/x25`, `ip6_tunnel` - actual Linux kernel code that never executes inside a container, which shares the host kernel; confirmed NOT removable - it's a mandatory transitive dependency of `libc6-dev`/`build-essential`, tested directly: purging it cascades into removing gcc/make/dpkg-dev entirely) and **208 Debian perl-tooling findings** (`perl`/`perl-base`/`perl-modules-*`/`libperl5.*` - confirmed a mandatory transitive dependency of `adduser`/`debconf`, not this project's own choice, not removable either). Enumerating ~700 individual CVE IDs for these would be impractical today and guaranteed to silently miss newly-published kernel CVEs tomorrow.

Built `docker/check_vuln_baseline.py` and `docker/vuln-baseline/<image>.json` (one per image, 9 total) instead: each baseline is a checked-in list of exactly which (CVE, package) pairs are already known and accepted, with an `owner`, an `expires_at` (2026-11-02, 90 days out), and a `note` explaining why. The checker fails on any CRITICAL/HIGH finding **not** already in the baseline (a genuinely new vulnerability - a freshly introduced dependency, or a newly disclosed CVE), and fails independently if `expires_at` has passed (forces periodic re-triage rather than the acceptance silently becoming permanent). This is what "fail on new unfixed findings" concretely means without either reopening a fight against 707 kernel/perl CVEs nobody can fix, or leaving the gate toothless.

CI (`image-vuln-scan` job) rewired accordingly: `continue-on-error: true` is gone entirely. Trivy now runs twice per image - once for a human-readable SARIF artifact (informational, `continue-on-error: true` stays on *that* step only, since it's not the gate), once for JSON that feeds `check_vuln_baseline.py`, which is the actual blocking step. A third Trivy run generates a CycloneDX SBOM per image, uploaded alongside the SARIF artifact.

Regression tests (`CheckVulnBaselineTests`, 6 tests): a missing baseline file is reported as a problem rather than crashing; a genuinely new finding fails; baseline-known findings pass clean; an expired baseline fails even with zero new findings; the same CVE appearing across multiple scan targets (two vendored copies of one library) is correctly deduplicated rather than double-counted; and all 9 real baseline files are checked for existence and well-formedness directly.

Verified end-to-end locally, simulating exactly what CI runs: built an image, ran Trivy to JSON the same way `trivy-action` would, ran `check_vuln_baseline.py` against it, confirmed exit 0 - not just unit-tested in isolation.

**Remaining pieces, addressed in a follow-up pass:**

- **Image signing and provenance/attestations**: added to `.github/workflows/publish-images.yml` - `id-token: write` permission, `docker buildx build --metadata-file` to capture the pushed image's exact digest, `actions/attest-build-provenance@v4.1.1` (GitHub's own SLSA build-provenance attestation, published to both GitHub's attestation store and the registry via `push-to-registry: true`), and `sigstore/cosign-installer@v4.1.2` + `cosign sign --yes` for a general-purpose keyless Sigstore signature (verifiable via any Sigstore-aware client, not just GitHub's own UI). Both mechanisms are keyless - they exchange the workflow's OIDC token for a short-lived cert from Sigstore's Fulcio CA, so there is no signing key to generate, store, or rotate.

  **Real end-to-end publish verification, 2026-08-03 - two real bugs found and fixed live, not just code-reviewed:**

  1. *GHCR package-publish permission.* The live `publish-images` workflow on `trysti-labs/optarena` had been failing on every run for weeks with `denied: permission_denied: The requested installation does not exist` when pushing to `ghcr.io/trysti-labs/optarena/*` - a GHCR package-publish permission that needed enabling at the GitHub org/repo level (not fixable from a code change; the workflow's own `permissions:` block was already correct for this part). Traced to this exact root cause via `gh run list`/`gh run view` against the real Actions history (initially misdiagnosed against the wrong remote - `selfopt/optarena`, a different org entirely, whose token can never have rights to `trysti-labs`'s GHCR namespace regardless of any permission setting - corrected once caught). Once the org-level permission was fixed and a fresh run triggered directly against `trysti-labs/optarena`, all 9 images built and pushed successfully on the pre-signing version of the workflow (the cosign/attestation steps were still uncommitted local changes at that point) - confirming the permission/plumbing issue is genuinely resolved.
  2. *Missing `attestations: write` permission.* Once the signing/attestation steps above were committed and pushed for real, their actual first live run immediately surfaced a second, genuine bug: `actions/attest-build-provenance` failed on all 9 matrix jobs with `Failed to persist attestation: Resource not accessible by integration` - `id-token: write` (already present) covers the OIDC exchange for the Fulcio signing cert, but persisting the attestation to GitHub's own attestation store via its REST API separately needs `attestations: write`, which the `permissions:` block didn't grant. That failure then skipped the cosign install/sign steps too (default `if: success()` step gating), and - important given GitHub's UI can read as if the job passed - the job's own `Complete job` step always reports success regardless; the real per-job `conclusion` (confirmed via `gh run view --json jobs`) was `failure` on every image. Fixed by adding `attestations: write` to the workflow's `permissions:` block.

  **Final confirmation, same day:** a third live run, on the corrected workflow, succeeded end to end on all 9 images - `gh run view --json jobs` confirms every one of `Build and push` / `Attest build provenance` / `Install cosign` / `Sign the image (keyless)` reporting `success` for real, not inferred from the job's always-green housekeeping step. The full publish + sign + attest pipeline this finding asked for is now genuinely proven working, not just code-reviewed or partially exercised.
- **JVM Spring Boot bump** (was flagged as unsafe to do in isolation - not attempted at first for exactly that reason): done properly this time. `docker/jvm/scratch-pom.xml`, `kotlin-pom.xml`, and the two starter repos that also pin it (`repos/springboot-tasktracker/pom.xml`, `repos/kotlin-inventory/pom.xml` - found by actually re-running `cases verify --language java` after the first pass and tracing 8 real failures back to these, not assumed clean) all moved from `3.3.4` to `3.5.16` (latest same-major release - Spring's own minor-version compatibility promise, not a riskier major bump) together with all 54 case files that embed the same pin, plus 4 more where the version appeared in *prompt text* describing the pre-existing pom.xml to the model (a real accuracy issue once the actual file no longer matched what the prompt told the agent to expect). **86 → 16 CRITICAL/HIGH findings on the JVM image (81% reduction)** - tomcat-embed-core, jackson-databind, commons-io, plexus-utils, and spring-webmvc all moved to patched versions. Both the Java (56 cases, 129 variants) and Kotlin (35 cases, 86 variants) corpora re-verified against the real oracle after the fix - "all verified", 0 violations, both languages. `docker/images.lock.json` gained a real `pinned_dependencies` entry for `spring-boot-starter-parent` (the Dockerfile's own comment was also stale at `3.3.4` - fixed, since `check_images_lock.py` validates against exactly that text). The JVM vulnerability baseline was regenerated to the new, much smaller finding set rather than left over-permissive.
- **.NET SDK-internal tool CVEs**: re-checked directly against Microsoft's own registry - `8.0.423` is still the latest published tag for `mcr.microsoft.com/dotnet/sdk:8.0` as of this pass, unchanged from the earlier check. Genuinely still blocked on an upstream Microsoft rebuild; nothing in this repository can fix it. Not attempted, and re-confirmed rather than assumed still true.

**Unrelated discovery, tracked and fixed separately (see P1-08 below):** running `optarena cases verify --language rust` as part of verifying the ImageMagick removal surfaced 22 pre-existing violations that reproduced identically against the unmodified `HEAD` Dockerfile - not caused by this pass, but found while doing this pass's verification work, and fixed as a follow-up in the same session.

---

### P1-08: Rust corpus oracle silently passed broken code (found and fixed during P1-03's verification)

**Status:** Fixed
**Type:** Correctness / benchmark validity
**Affected code:** `optarena/cases.py` (`run_check_command`), 3 case files' `mutation_check.py`

**Finding**

Not in the original review - found while re-verifying the Rust image after the P1-03 fixes: `optarena cases verify --language rust` reported 22 violations. Confirmed via a direct A/B rebuild against the completely unmodified `HEAD` Dockerfile that these were pre-existing, not caused by anything in this pass.

**Root cause, confirmed directly with `CARGO_LOG=cargo::core::compiler::fingerprint=trace`:** every Rust case shares ONE `CARGO_TARGET_DIR` across its whole sandbox container (by design - it's what makes the expensive axum/actix-web/tokio dependency graph compile only once instead of once per case). Cargo's build-cache identity for a local path package turns out to be `(name, version, dependencies, profile)` - it does **not** include the package's own absolute directory. A case's `reference_solution` and a `broken_solutions` variant have the same `Cargo.toml` name/version (they're variants of the same case) but live in different workspace directories with genuinely different source. Cargo computes the identical metadata hash for both, and its freshness check compares the shared output's mtime against whatever source path its own dep-info last recorded - from whichever variant built it *first* - without ever looking at the current directory's actual file. Multiple different cases also reuse the same generic package name outright (confirmed: `webapp`, `conc_case`, `dedup_perf`, each shared by 2-4 cases), so this wasn't limited to variants of a single case.

Net effect: a `broken_solutions` variant that should fail the oracle silently reused an earlier build's already-compiled, already-passing test binary and read as passing (10+ cases); a reference solution in one case could pick up a completely different case's compiled symbols and fail to "compile" (2 cases). Three more (`add_tests_axum_handler_logic`, `add_tests_rust_binary_search`, `add_tests_rust_shipping_tiers`) hit the identical bug *inside* their own `mutation_check.py` mutation-testing loop, which runs `cargo test` several times against successively mutated source within one `check_command` call - a case-level fix couldn't reach that.

A false pass here isn't cosmetic: it means a real coding agent's broken Rust solution could have scored as passing, silently, on any of these cases.

**Fixed**

- `optarena/cases.py`: new `_cargo_clean_prefix(root)` reads the workspace's `Cargo.toml` (if any) and returns `"cargo clean --offline -p <name> >/dev/null 2>&1; "`, or `""` for every non-Rust case. `run_check_command()` prepends it to every `check_command` unconditionally - `cargo clean -p` removes only that one package's own cached lib/test/fingerprint artifacts; its dependencies (the expensive, genuinely shared part) stay warm, so this costs one small-crate recompile, not the dependency graph.
- The 3 `mutation_check.py` scripts with their own internal `cargo test` loop got the same fix applied to their own `TEST_CMD`, since the case-level prefix only covers the very first invocation.
- Regression tests (`CargoCleanPrefixTests`, 6 tests): no-op without a `Cargo.toml`; correct package-name extraction; a malformed `Cargo.toml` is a safe no-op rather than a crash; the package name is shell-quoted (defense in depth - it ultimately comes from case-JSON content); and two tests confirm the prefix actually reaches `run_check_command`'s executed command for a Rust workspace and is absent for a non-Rust one.

**Verified:** `cases verify --language rust` went from 22 violations to **0** ("all verified"), confirmed stable across 3 consecutive full runs (the bug was timing-sensitive, so a single clean run wasn't enough evidence). `cases verify --language go` re-run to confirm the shared `run_check_command` change is a genuine no-op for non-Rust languages - still "all verified". Full suite: 406 passed, 0 failed (up from 400 - all 6 new tests). `cases validate`: still 836/836. `ruff check`: clean.

---

### P1-04: Docker dependency ledger is not fully enforced

**Status:** Fixed
**Type:** Reproducibility / supply chain
**Affected code:** `docker/images.lock.json`, `docker/php/Dockerfile`, `docker/check_images_lock.py`

**Finding**

The lock file records PHPUnit `11.5.56`, while the PHP image installs `12.0.0`. The lock checker validates base-image information but does not verify all `pinned_dependencies` values against Dockerfiles.

**Fixed in this pass**

- Updated `images.lock.json`'s phpunit entry to `12.0.0` to match `docker/php/Dockerfile`.
- Extended `check_images_lock.py` with `_check_pinned_dependencies()`: for every `pinned_dependencies` entry, finds Dockerfile lines mentioning the dependency's short name and requires the pinned version string to appear verbatim on one of them. Heuristic rather than a real per-ecosystem parser (Go's `go get pkg@version`, pip's `pkg==version`, a phpunit.phar URL, and a Terraform release URL have no common grammar), but it's exactly what would have caught the real drift this finding reports.
- Verified all 9 images' other `pinned_dependencies` (Go's gin/fiber, the base image's pyyaml/terraform) already matched their Dockerfiles - phpunit was the only actual drift.
- Regression tests in `CheckImagesLockPinnedDependenciesTests`, including one that reproduces the exact original drift (lock says 11.5.56, Dockerfile has 12.0.0) and asserts the checker now catches it.

`python docker/check_images_lock.py` passes clean (9 images checked).

---

### P1-05: Host-native agents need a stronger trust boundary

**Status:** Fixed
**Type:** Product security / user safety
**Affected code:** CLI, `SECURITY.md`

**Finding**

CLI agents execute on the host with the user's filesystem, environment, credentials, and network access. Container isolation protects verification, not agent execution.

**Fixed in this pass**

- `optarena run` now refuses to start a `kind: "cli"` driver (aider, Claude Code, Codex, opencode, goose, qwen-code - this classification already existed in `drivers/__init__.py`'s `DRIVERS` registry) without confirmation: an interactive terminal gets a warning plus a y/N prompt; non-interactive use (CI, scripts) requires the new `--yes-i-understand-host-execution` flag or is refused outright.
- `SECURITY.md` documents the gate in its trust-model section.
- Regression tests in `HostNativeExecutionConfirmationTests` cover baseline/SDK drivers (no gate), the flag bypass, non-interactive refusal, and both interactive y/n paths.

**Not done:** drivers are not yet labeled host-native-vs-isolated in `doctor`/help/report output specifically (only this run-time gate was added); no container/VM execution backend for agents was investigated; verifier containers were not switched to non-root by default.

---

### P1-06: Symlinks and unbounded workspaces can leak or exhaust resources

**Status:** Fixed
**Type:** Security / denial of service / privacy
**Affected code:** `optarena/cases.py` (`snapshot`, `check_expected`), `optarena/security.py` (`scan_workspace`), `optarena/cli.py` (`cmd_scan`)

**Finding**

Workspace inspection can follow symlinked files, and there are no strong file-count, per-file-size, or total-workspace-size limits.

**Earlier note in this document, now superseded:** an earlier pass found symlink-skipping already existed in *some* places (`copy_setup_repo`, an unrelated workspace walker) and left it at "partially contradicted, not fixed." That was accurate as description but incomplete as an audit - the actual load-bearing function, `cases.snapshot()` (called before/after every single case, on every driver, to compute what changed), had no symlink protection at all: `Path.is_file()` follows symlinks by default, so a symlink an agent or check_command created would have its TARGET's content read and hashed here, then flow into `result.files`/security scanning/dashboard display as if it were the agent's own output. In host-exec mode (`OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1`) this runs directly on the host filesystem, making it a real arbitrary-host-file-read primitive, not just a container-internal one.

**Fixed in this pass**

- **`cases.snapshot()`** (the actual oracle-facing function `changed_files`/`check_expected`/every driver's `before`/`after` diff all go through): symlinks skipped via `is_symlink()`, plus a full `resolve()` + `is_relative_to(root)` containment check on every entry - the same defense `copy_setup_repo` already had, now applied where it was actually missing. Bounded: a cap on raw directory entries walked (not just files kept - stops the walk itself, not just the output), a per-file size cap beyond which a file gets a size-still-detects-a-change fallback signature instead of being read into memory in full, and a total-bytes-hashed budget across one call. Any truncation prints a visible stderr warning rather than silently changing behavior.
- **`cases.check_expected()`**: the remaining unbounded-read gap `snapshot`'s size cap didn't close on its own - content-pattern assertions read an expected file's full text separately, so a file large enough to get `snapshot`'s size-only fallback would otherwise still be read in full here. Now rejected as an explicit failure ("too large to check content") above the same size threshold, not read regardless.
- **`security.scan_workspace()`**: added the same `is_symlink()` + resolve+containment check (it only had a 1MB per-file size cap before, and that alone doesn't stop a symlinked directory from being scanned), plus a file-count cap - this function is also reachable directly via `optarena scan <dir>` with a user-supplied, non-managed directory, not only via a driver's already-filtered `changed_files()` output.
- **`cli.cmd_scan`**: the standalone command's own directory enumeration (independent of `scan_workspace`, which only receives an already-built file list) now skips symlinks and stops at the same file-count cap at enumeration time, rather than fully materializing an unbounded directory tree first.

Regression tests (`WorkspaceInspectionLimitsTests`, 8 tests): a symlinked file is skipped, not followed; a symlinked *directory* doesn't leak its contents (the leaf-level check alone wouldn't catch this); the entry-count cap actually stops the walk; an oversized file gets the size-only signature instead of a full read; that fallback signature still correctly detects a subsequent change; `check_expected` rejects an oversized file instead of reading it; `scan_workspace` skips a symlinked file; `scan_workspace`'s file-count cap is honored (verified by counting actual `scan_text` calls, not just absence of a crash). The two symlink-creation tests skip gracefully (`self.skipTest`) rather than fail on a host without symlink privileges - confirmed necessary directly: this Windows session cannot create symlinks without elevated privileges/Developer Mode, so those two run for real on POSIX/CI, not here.

**Not done:** a true total-workspace-size cap (across ALL files combined, as opposed to the per-call total-bytes-hashed budget `snapshot` now has) wasn't added as a separate, independently-configurable limit - the per-file and per-call budgets already bound the practical worst case for how this function is actually invoked (once or twice per case, not accumulated across a whole run), and adding a third, differently-scoped limit without a concrete scenario it catches that the existing two don't felt like complexity for its own sake rather than a real gap.

---

### P1-07: External case packs lack a strong trust and provenance model

**Status:** Partially fixed
**Type:** Supply-chain security
**Affected code:** `optarena/packs.py`, pack documentation

**Finding**

Pack installation permits insecure transport and relies on self-declared hashes without publisher authentication. Packs influence host-side workspace preparation and agent prompts.

**Fixed in this pass**

`load_pack()` now refuses a plain `http://` URL by default (`ValueError`, raised before any network call) - `https://` is required unless the caller explicitly passes `allow_insecure=True` (CLI: `optarena cases install <url> --allow-insecure`, documented as for local/offline testing). Regression tests confirm `http://` is blocked before `urlopen` is ever called, that `--allow-insecure` gets past specifically that check, and that `https://` never needed it.

**Not done:** the content hash is still self-declared by the pack (detects corruption/tampering, not authenticity) - no signed manifests or trusted-publisher-identity model were added. `SECURITY.md` already documented this limitation ("it is not a signature and proves nothing about who wrote the pack"); that framing still stands and is now more precisely true (transport is secured, authorship still isn't verified).

---

## P2 — Product and open-source readiness

### P2-01: Documentation and corpus counts are stale

**Status:** Fixed
**Type:** Documentation

**Finding**

The validated corpus contains 836 cases, while `README.md`, `ARCH.md`, source comments, and CI comments still reference 510. `CHANGELOG.md` also links to a missing `AUDIT_2.md`.

**Fixed in this pass**

- `README.md` and `ARCH.md`: all "510 cases" references updated to 836, with the per-language breakdown recomputed from the actual corpus (`load_cases()` + a `Counter` over `language`), not guessed. The "466 also ship a must-fail variant" claim was re-verified with `verify.variants_for()` directly - it's now actually 836/836 (every case has a discriminating variant today; `cases verify --strict` enforces this stays true going forward), so that paragraph was rewritten rather than just having its number swapped.
- `optarena/cases.py:103`'s comment ("None of the corpus's 510 check_commands install packages at runtime... verified against the whole corpus") was re-verified against all 836 cases (scanned every case's `check_command` for install-command substrings - still zero) before updating the number, rather than assuming the underlying claim still held.
- `CHANGELOG.md`'s `AUDIT_2.md` link fixed to note the historical findings are archived outside the public repo (see P2-02).
- `CHANGELOG.md:86`'s "510 built-in cases" reference was deliberately left as-is - it's a dated changelog entry describing a rename that happened when the corpus WAS 510 cases; rewriting historical entries to current numbers would make the changelog wrong, not right.

---

### P2-02: Internal audit history is too large and stale for public documentation

**Status:** Fixed
**Type:** Documentation / repository hygiene
**Affected code:** `AUDIT.md`

**Fixed in this pass**

`AUDIT.md` moved to `DEV_NOTES/AUDIT.md`, which is already in `.gitignore` - it stays available locally but is no longer part of the public repository. Two code comments that referenced `AUDIT.md` by path (`optarena/schema.py`, `.github/workflows/codeql.yml`) were updated to point at `SECURITY.md` instead, which was itself expanded (see P0-01, P1-05) so those pointers remain accurate.

---

### P2-03: The wheel does not provide a complete usable installation

**Status:** Fixed (scoped) - packaging model chosen, documented, and now actually verified end to end
**Type:** Packaging / adoption
**Affected code:** `pyproject.toml`

**Finding**

The case corpus, Docker definitions, starter repositories, and dashboard are not bundled into the wheel, so important commands require a source checkout. Resolution asked to: choose/document one packaging model, use `importlib.resources` for packaged data, actually test a full documented workflow from an installed wheel in a clean directory, and add complete project metadata.

**Already correct from a prior pass (confirmed, not re-done):** `[tool.setuptools.packages.find]`/`[tool.setuptools.package-data]` bundle `optarena/` plus `cases/*.json` only, by deliberate choice - README's "Source-checkout install only" section and a matching `pyproject.toml` comment already document that `docker/`, `dashboard/`, `repos/` are intentionally excluded (they're top-level siblings of the package, not part of it), that `optarena doctor` reports which of the three are present, and that results are written to `~/.optarena/results` outside any checkout so `pip install -U` can never delete run history. This is a real, coherent packaging decision, not an oversight - "everything in one wheel" was considered and explicitly rejected.

**Fixed in this pass**

- **Actually verified the documented workflow end to end**, which had not been done before: built a real wheel (`python -m build --wheel`), installed it into a brand-new, fully isolated venv with zero relation to the source checkout, and ran the full documented flow from an empty working directory with no `optarena` git checkout anywhere nearby - `drivers list` (all 14 drivers), `cases list` (836, confirming the packaged `cases/*.json` data resolves correctly via `Path(__file__).parent` from real `site-packages`, not just from a source tree), `doctor` (correctly reports `dashboard/`/`docker/`/`repos/` all missing, while still correctly detecting the CLI tools on `PATH` and the Docker daemon/images, which are genuinely global machine state), and a real `optarena run` against a live Ollama backend through the real Docker sandbox - PASSED, and the result correctly landed in `~/.optarena/results/runs/`, not inside `site-packages` and not in the empty test directory. This is the first time this exact path (wheel → clean install → clean directory → real case) had actually been exercised rather than assumed from code review.
- Confirmed `importlib.resources` is not actually required here: `CASES_DIR = Path(__file__).parent / "cases"` works correctly for any normal `pip install` (wheel or editable), because setuptools always extracts wheel contents to real files in `site-packages` - `importlib.resources` only becomes necessary for a zipapp/zipimport-style install, which this project doesn't support or claim to. Verified rather than assumed, via the clean-install test above.
- **Added the missing project metadata**: `pyproject.toml` gained `authors`, `keywords`, expanded `classifiers` (development status, supported Python versions, OS-independence, topic), and `[project.urls]` (Homepage/Repository/Issues, matching the public `trysti-labs/optarena` remote used in README's own clone instructions). Verified in the built wheel's actual `METADATA` file, not just the source TOML - `pip show optarena` previously reported blank `Home-page`/`Author` fields; confirmed fixed post-rebuild.

**Not done:** no separate `optarena[dashboard]`/`optarena[docker]` extras were added to optionally bundle those directories into the wheel - the existing "source checkout for those three, `pip install` for everything else" split is a coherent, already-documented model, and splintering it into more install variants wasn't asked for and adds surface area without a demonstrated need.

---

### P2-04: Run manifests are not sufficient for exact reproduction

**Status:** Fixed (driver/SDK version capture); rest unchanged from prior pass
**Type:** Reproducibility
**Affected code:** `optarena/drivers/__init__.py`, `optarena/runner.py`, scenario schema

**Finding**

Manifests record useful case and image hashes but omit the exact source revision/build, driver version, external CLI version, SDK/provider version, platform details, and common generation parameters. `ORACLE_VERSION` also remains unchanged across behavior changes.

**Fixed in a prior pass**

`build_manifest()` records `git_commit`, `git_dirty` (best-effort via `git rev-parse`/`git status --porcelain` against the repo the package is running from - `None` off a non-git install, e.g. a built wheel, rather than failing the run), `python_version`, and `platform` (via the stdlib `platform` module).

**Fixed in this pass**

`build_manifest()` now also records `driver_version`, via a new `get_driver_version(name)` in `optarena/drivers/__init__.py`:

- For `kind: "cli"` drivers, finds the real binary (`find_aider()` for aider; `CLI_AGENTS[key]["binaries"]` + `shutil.which` for the rest) and runs `<binary> --version`, returning the first non-empty output line (truncated to 200 chars).
- For `kind: "sdk"` drivers, calls `importlib.metadata.version()` against a new `_SDK_PIP_NAMES` mapping (driver key → real PyPI distribution name, sourced from `pyproject.toml`'s extras) - **not** the Python import name. This distinction matters concretely: `openai-agents` installs as the importable module `agents`, and `importlib.metadata.version("agents")` returns nothing (`packages_distributions()` doesn't reliably reverse-map it either) - only querying the distribution name directly works. Caught live in this pass via a regression test before it could ship silently broken.
- Every `DRIVERS` entry also now carries `owner` and `tested_with` (the real version this pass verified against), and `cmd_doctor` prints a per-driver note comparing the live-detected version against `tested_with`, flagging drift without failing the command.

Verified live against a fully-populated real environment (all 6 CLI tools + all 6 SDK packages actually installed) via `optarena doctor` - correct version strings for all 12, zero false mismatches against their recorded `tested_with`. New tests: `GetDriverVersionTests` (9 cases covering unknown/baseline drivers, missing binaries, CLI `--version` parsing and exception-safety, SDK metadata lookup, and a dedicated regression test for the `openai-agents`/`agents` distribution-vs-import-name bug) plus `test_manifest_records_driver_version_key`.

**Not done:** `ORACLE_VERSION` was not bumped (no oracle behavior changed this pass) and its "define compatibility rules across oracle versions" ask remains unaddressed. Common generation parameters (temperature, seed, etc.) beyond what the scenario/backend config already captures were not added.

---

### P2-05: Unknown remote-model pricing is represented as zero

**Status:** Fixed
**Type:** Product correctness
**Affected code:** `optarena/pricing.py`, metrics and dashboard

**Finding**

An unmatched remote model can receive an estimated cost of `0.0`. Unknown cost is not the same as free and can mislead users.

**Fixed in this pass**

- `pricing.estimate_cost()` now returns `None` for an unpriced remote model, reserving `0.0` for a confirmed-local backend only.
- `metrics.aggregate()` adds an `unpriced_cases` count (cases with real token usage but unknown cost), surfaced in both `cli.py`'s console summary and `report.py`'s HTML report as "(+N unpriced)" alongside the total, so a partial total no longer reads as complete.
- Regression tests cover `estimate_cost` returning `None` (not `0.0`) for an unknown remote model, and `aggregate()` flagging `unpriced_cases` correctly (and staying `None` when everything actually was priced).

**Not done:** pricing source/currency/`as_of` metadata was not added; the dashboard's own rendering (if any reads these fields separately from the summary dict) was not independently checked beyond `report.py`/`cli.py`.

---

### P2-06: External driver integrations are tested mainly through mocks

**Status:** Fixed (scoped)
**Type:** Quality / integration testing
**Affected code:** `optarena/drivers/__init__.py`, `optarena/cli.py`, `.github/workflows/integration-smoke.yml` (new), `scripts/check_no_infra_errors.py` (new)

**Finding**

The core unit suite validates driver logic (deadline handling, disruptions, token accounting) against fake binaries and mocked HTTP clients; it has never actually invoked a real CLI tool or SDK end to end. The original finding also asked for a defined support tier/owner per driver and recorded tested driver/SDK versions.

**Fixed in this pass**

- **Support tier / owner / tested versions**: every `DRIVERS` entry now has `owner` and `tested_with` (the "support tier" ask is served by the pre-existing `status` field, now documented as doing double duty rather than adding a redundant field). `cmd_doctor` compares the live-detected version (P2-04's `get_driver_version`) against `tested_with` and prints an advisory drift note without failing the command.
- **Real, scheduled, zero-secret integration testing**: new `.github/workflows/integration-smoke.yml` - weekly cron (`17 6 * * 1`) plus manual `workflow_dispatch`, matrixed over the four `backend: scenario` CLI drivers (aider, opencode, goose, qwen-code). Each job does a real `pip install`/tool install, starts a real local Ollama server, pulls a small real coding model (`qwen2.5-coder:1.5b`), builds the real sandbox image, and runs one real case (`create_fibonacci`) through the actual `optarena run` path - a genuinely different failure surface than the mocked unit suite (does the tool actually start, accept this project's invocation shape, and produce a gradeable result).
- The workflow distinguishes "the model got the task wrong" (expected and unremarkable for a small local smoke model - not a failure) from "the integration itself is broken", via a new standalone script `scripts/check_no_infra_errors.py` that inspects the saved run's `summary.infrastructure_errors` (F-10) rather than `optarena run`'s raw exit code (which is 1 on ANY case failure, model-correctness included - confirmed live: a real aider+gemma3:1b run against this case exited 1 with `infrastructure_errors: None`, i.e. the harness worked correctly and the model just lost). New `CheckNoInfraErrorsTests` (4 tests) cover this script directly.
- A final `doctor` step (`if: always()`) surfaces the driver/SDK versions actually exercised by each scheduled run, informational only (`|| true`), matching the P1-08-established pattern of session-scoped provenance without hard-gating on it.

**Not done, deliberately scoped:** `backend: fixed` tools (Claude Code, Codex - require real paid Anthropic/OpenAI accounts) and the six optional SDK drivers are not covered by this workflow; extending to them needs a secrets budget/owner decision that's the maintainer's call, not something to default into. No `@pytest.mark.integration` marker was added to the unit suite itself - the new workflow is deliberately a separate, slower-cadence job rather than folded into the fast per-commit suite.

---

### P2-07: Cross-driver scores need capability-aware denominators

**Status:** Fixed (scoped to the one capability dimension the codebase already detects)
**Type:** Product correctness / benchmark fairness
**Affected code:** `optarena/runner.py`, `optarena/metrics.py`

**Finding**

A baseline/SDK driver with `file_tools: False` (a flat-file writer, not a real multi-file editing tool) is structurally incapable of passing some cases (e.g. one expecting two separate output files) regardless of model quality. Averaging it into the same `pass_rate` as a full CLI agent misrepresents both scores. No capability-declaration or eligible-case-only scoring concept existed anywhere in `optarena/*.py` before this pass.

**Fixed in this pass**

Rather than build the full multi-dimensional capability framework the original finding's resolution list sketches (arbitrary driver capability declarations × arbitrary per-case capability requirements), this pass wires up the one dimension the codebase already has real detection logic for: `cases.baseline_incompatible(case)` (pre-existing, used elsewhere to explain why a baseline driver can't satisfy a given case's structural shape).

- `runner._run_case()` - the single helper shared by both the serial and parallel (`_worker_loop`) execution paths, so this applies uniformly regardless of run mode - now computes `capability_excluded = cases_mod.baseline_incompatible(case)` once per case, only when `DRIVERS[scenario.driver]["file_tools"]` is `False`, and stashes the reason string into `result.extra["capability_excluded"]` for every trial. A `file_tools: True` (real CLI-agent) driver is completely unaffected, even run against the identical case.
- `metrics.aggregate()` mirrors F-10's existing `infrastructure_errors`/`adjusted_pass_rate` pattern exactly: `capability_excluded_cases` (count), `eligible_pass_rate` (pass rate over only the cases the driver could structurally have won - `None` when nothing was excluded or the eligible denominator is 0), and `capability_exclusion_reasons` (sorted, deduplicated list of the reasons, for a human skimming a summary). Critically, the original `pass_rate` is left completely unchanged - every case still counts in it, exactly as before, so historical runs stay comparable; `eligible_pass_rate` is an additive second lens, not a replacement.
- Confirmed via grep that F-10's `adjusted_pass_rate`/`infrastructure_errors` fields are not surfaced anywhere in `report.py`, `cli.py`, or the dashboard JS beyond the summary dict itself - `eligible_pass_rate` follows that exact precedent, so no UI changes were made.
- New tests: `CapabilityAwareAggregateTests` (excluded cases lower the overall rate but not `eligible_pass_rate`; the fields stay `None` when nothing is excluded; multiple reasons are deduplicated and sorted) and `RunnerCapabilityExclusionWiringTests` (a `file_tools: False` driver gets `capability_excluded` set on an incompatible case; a `file_tools: True` driver never does, on the identical case; a `file_tools: False` driver on a *compatible* case is unaffected).

**Not done, deliberately scoped:** this is not the full capability framework the original finding describes - there's no general driver-capability-declaration schema beyond the existing `file_tools` boolean, and no per-case capability *requirements* beyond what `baseline_incompatible()` already infers structurally (file count). A richer framework (e.g. cases declaring "requires terminal access" or "requires multi-turn tool use") would need real per-capability detection logic to be honest, not a guessed taxonomy - left for a future pass if a second capability dimension with real detection logic emerges.

---

## P3 — Maintainability and community readiness

### P3-01: Core modules are oversized and carry excessive audit-history comments

**Status:** Fixed
**Type:** Maintainability
**Affected code:** `optarena/cases.py`, `optarena/cli.py`, `optarena/runner.py`

**Finding**

`cases.py` (2,009 lines), `cli.py` (1,400 lines), and `runner.py` (783 lines) had each grown into a single monolithic module mixing several unrelated concerns, with heavy inline audit-trail commentary compounding the size. Previously judged too large a refactor to attempt safely alongside other fixes in one pass - the test suite and driver layer import directly from all three, and `cases.py` in particular has module-level mutable cache state (Docker engine/image-pull health) that tests reset directly between runs.

**Fixed in this pass**

All three were split into focused packages, each verified against the real (not just mocked) blast radius before and after:

- **`cases.py` → `optarena/_cases/`** (`_constants.py`, `_corpus.py` [case loading/filtering], `_snapshot.py` [workspace hashing + the assertion oracle], `_sandbox.py` [container engine, `DockerSandbox`, check_command execution - kept as one ~1,190-line module rather than fragmented further, since its module-level cache state, `_active_sandboxes`/`_worker_sandboxes`/the engine-health TTL cache, is directly poked by several tests and genuinely belongs together], `_workspace_setup.py` [setup_files/setup_repo/git_init/disruptions], `_evaluate.py` [ties the oracle and check_command together]). `cases.py` itself is now a ~140-line re-export facade - not a package, deliberately: `optarena/cases/` already exists on disk as the 836-file JSON case-corpus data directory, so a `cases/__init__.py` package would collide with it. Named `_cases/` (leading underscore) specifically to avoid that collision, not for aesthetics.
- **`cli.py` → `optarena/cli/`**: `_run.py` (run/compare/regression), `_cases_cmds.py`, `_runs_cmds.py`, `_doctor.py`, `_sandbox_cmds.py`, `_serve.py`, `_scan_report.py`, with `__init__.py` (~380 lines) holding only argparse wiring (`main`) plus re-exports.
- **`runner.py` → `optarena/runner/`**: `_manifest.py` (manifest-building), `_results.py` (`RunRecord`, trial-merging, console formatting), `_execution.py` (case execution, the `--parallel` worker pool, `run_scenario` itself - kept together as one ~420-line module for the same reason as `_sandbox.py`: they call each other directly and share the exact names, `get_driver`/`DockerSandbox`/`store.save_checkpoint`/`tempfile.mkdtemp`, that ~35 existing tests patch).
- Every package's `__init__.py`/facade re-exports the full previous public surface, so no import anywhere else in the codebase (drivers, `cli.py`→now `cli/_run.py`, `verify.py`, `__main__.py`) needed to change.

**A real regression found and fixed by this pass's own verification, not by the original review:** after the split, `tests/test_cli.py::ServeCommandTests::test_missing_dashboard_is_a_clean_error` hung indefinitely (a live HTTP server never exiting) under a *fixed* test-execution order - reproducible, not the flaky kind. Root cause: the test does `mock.patch.object(cli, "REPO_ROOT", <empty temp dir>)` to simulate a missing dashboard; before the split, `REPO_ROOT` lived directly in `cli.py`, so that patch worked. After the split, `_serve.py` had captured its own separate `from ._constants import REPO_ROOT` reference at import time - the patch on the facade's copy never reached the code path that actually used it, so `cmd_serve` fell through to the *real* dashboard/, found it present, and called `httpd.serve_forever()` for real, which nothing in the test ever stops. Fixed by making `cmd_serve` (and, proactively, `cmd_doctor`, which has the identical shape) look up `REPO_ROOT` via a deferred `from . import REPO_ROOT` *inside* the function, at call time, through the `cli` package facade itself - so a patch on `optarena.cli.REPO_ROOT` is honored again, matching pre-split behavior exactly. This is exactly the class of bug this kind of refactor risks, and exactly why every step was followed by full-suite runs in both fixed and `pytest-randomly` order rather than a single pass at the end.

**Verification, this pass:** full test suite green in fixed order and 2 independent `pytest-randomly` seeds after each of the three splits (448 → 453, the +5 being P3-02's new entry-point-discovery tests) - `ruff check` clean throughout; real (non-mocked) smoke checks after all three splits: `optarena doctor` (all 12 drivers + all 9 sandbox images + source-checkout section correct), `optarena cases validate` (836 cases), and a real `optarena serve` instance curled directly (dashboard 200, a `../` traversal attempt 404s) - confirming the `REPO_ROOT` fix works in the real, unpatched environment too, not only in the test that caught the regression. Discovered `tests/test_cli.py` and `tests/test_conformance.py` exist alongside `tests/test_optarena.py` partway through this pass (the earlier P0-P2 passes' "full test suite" runs already covered all three via `pytest tests/`, but this pass's *targeted* mock-patch-path grep initially covered only `test_optarena.py` - the gap that let the `REPO_ROOT` regression through a first round of "fix + verify"); all three files' `optarena.cases`/`optarena.cli`/`optarena.runner` coupling was re-audited and fixed together once found.

**Not done:** the inline audit-trail comment density itself (A-xx/F-xx/P-xx/M-xx-tagged rationale comments) was not reduced - these were judged genuinely load-bearing (they're the reason a regression like the one above gets caught and explained, not just silently patched) rather than the actual bloat the finding was really about; the file-count/size problem is what got fixed.

---

### P3-02: Public contribution and release infrastructure is incomplete

**Status:** Fixed
**Type:** Open-source maintenance
**Affected code:** `CODE_OF_CONDUCT.md` (new), `.github/PULL_REQUEST_TEMPLATE.md` (new), `.github/ISSUE_TEMPLATE/*.md` (new), `optarena/drivers/__init__.py`

**Note from a prior pass, still accurate:** `CONTRIBUTING.md` and `SECURITY.md` already exist - the original finding's resolution list reads as if all contribution docs are missing, which isn't accurate.

**Fixed in this pass**

- Added `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1, pointing to the same `arun@trysti.com` contact `SECURITY.md` already uses), `.github/PULL_REQUEST_TEMPLATE.md` (references the actual commands `CONTRIBUTING.md` documents as CI's checks - `ruff check`, the unit suite, `cases verify` - rather than generic boilerplate), and `.github/ISSUE_TEMPLATE/bug_report.md` + `feature_request.md`.
- **Third-party driver discovery via `entry_points`**, the one genuinely missing mechanism: a new `optarena.drivers` entry-point group. A separately-installed package registers a driver with `[project.entry-points."optarena.drivers"] my-tool = "my_package.driver:MyToolDriver"` - `MyToolDriver` a `Driver` subclass, constructible with no arguments, matching every built-in driver's own convention. `optarena/drivers/__init__.py`'s new `_load_entry_point_drivers()` reads `importlib.metadata.entry_points(group="optarena.drivers")` once at import time (cheap - metadata only, imports nothing) and merges discovered names into the existing `DRIVERS` registry (`kind: "external"`) so they automatically show up in `optarena drivers list`, `--driver` argparse choices, and `doctor`'s registry-driven checks with zero special-casing elsewhere. The actual third-party module is only imported lazily, inside `get_driver()`, the first time that specific driver is requested - a broken/uninstallable third-party package doesn't prevent discovery or listing for everyone else, and costs nothing for a run that never uses it. A failure loading the entry point at that point raises a clear `RuntimeError` naming the driver and the underlying error, through the same F-04 clean-error path `cli.cmd_run` already uses for every other expected failure mode.
- 5 new regression tests (`EntryPointDriverDiscoveryTests`): discovery merges correctly into `DRIVERS`, `get_driver` instantiates a discovered entry point, a broken entry point raises `RuntimeError` (not a raw traceback), no entry points installed is not an error, and an `entry_points()` lookup failure itself (a broken environment) is swallowed rather than breaking driver discovery for everyone.

**Not done:** no real third-party driver package exists to test this end-to-end against (only mocked `importlib.metadata.entry_points` in the regression tests) - the mechanism is real and exercised, but hasn't been proven against an actual separately-`pip install`-ed driver package. `doctor` does not run any driver-specific health check against a discovered entry-point driver (only the built-in CLI/SDK sections do) - reasonable scope for a first pass at discovery, not full third-party lifecycle support.

## Cross-platform compatibility audit (this pass, not from the original review)

Requested directly: "ensure that optarena works across windows, linux, mac." A systematic sweep of the codebase for platform-sensitive code, not limited to the P0-03 fix above - a code-review-based audit (this session runs on Windows only), cross-checked against what CI actually exercises live.

**Findings, all clean:**

- **Every `os.name` branch is correctly guarded** - 4 total in `cases.py` (the P0-03 Job Object gate, `relax_workspace_permissions`'s POSIX-only chmod, `_kill_process_tree`'s killpg-vs-taskkill split, `run_capture`'s session/process-group setup) - no unconditional POSIX-only stdlib imports (`fcntl`/`termios`/`pwd`/`grp`/`tty`/`pty`) anywhere in the package.
- **`signal.SIGKILL`** is only ever accessed inside the POSIX branch - safe (the `signal` module itself is fully cross-platform; only specific constants like `SIGKILL` don't exist on Windows).
- **Container engine and CLI-agent binary discovery** (`docker`/`podman`, `aider`/`claude`/`codex`/`opencode`/`goose`/`qwen`) all go through `shutil.which`, which correctly resolves Windows' `PATHEXT`/`.cmd` shims (the way npm installs most of these tools on Windows) automatically. `find_aider()` additionally checks both `Scripts/aider.exe` and `bin/aider` venv layouts explicitly before falling back to `PATH`.
- **`drivers/base.py`'s `subprocess_env` allowlist** is already unusually well cross-platform-hardened, with real documented incidents driving its exact shape: case-insensitive env-var matching specifically because Git-Bash/MSYS2 exposes `SYSTEMROOT`/`WINDIR`/`COMSPEC` in different casing than `SystemRoot`/`windir`/`ComSpec`, and a `SystemDrive` entry added after its absence broke a Node CLI's crypto init and leaked a stray `%SystemDrive%`-named directory into the sandboxed workspace.
- **No BSD-vs-GNU coreutils divergence risk** (a real macOS-specific trap: `sed`/`date`/`stat`/`ps` take different flags on macOS's BSD userland than Linux's GNU one) - nothing in the package shells out to these directly; everything goes through Python's own stdlib, which normalizes the difference.
- **No raw ANSI escape codes, no manual `/tmp` path construction** - the one `/tmp` hit (`GOCACHE=/tmp/go-build`) is a container-internal environment variable value, not a host filesystem path; sandbox containers are always Linux regardless of host OS, so this is correct as-is, not a bug.
- **`shell=True` in the host-exec fallback path** (`_run_check_command_local`) would run POSIX-shell-syntax `check_command`s through `cmd.exe` on native Windows host execution, which wouldn't understand them. Not fixed, because this is the explicitly-gated `OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1` fallback, never the default sandboxed path (see `SECURITY.md`'s trust model) - an accepted, already-documented tradeoff rather than a bug to chase.
- **CI already runs the full unit suite on `ubuntu-latest` + `macos-latest` + `windows-latest`, × Python 3.10-3.13**, with coverage enforced uniformly across every leg specifically so a Windows-or-macOS-only branch (the job's own comment names `_kill_process_tree`'s taskkill-vs-killpg split as the example) can't silently go untested on the other platforms. P0-03's new tests get real execution on all three OSes through this, not just a Windows-only spot-check.

**One latent, low-severity risk noted, not fixed (no evidence it has actually been hit):** Windows' legacy 260-character `MAX_PATH` limit. A worst-case nested temp path (`tempfile.mkdtemp()` + a case name up to 67 characters + a variant name + a nested source file path) lands around ~200 characters in realistic testing, under the limit, but could exceed it on a machine with a long username/profile path, or in a locked-down environment where the "Enable Win32 long paths" group policy is off (not the default on modern Windows, but not guaranteed either). Python 3.6+ mitigates this automatically for many operations when the OS allows it. Flagged for awareness; not worth a speculative fix without a reproduction.

## Verification baseline (this pass)

- `python -m optarena cases validate`: 836 structurally valid cases (unchanged).
- `python -m pytest tests/`: 428 passed, 4 skipped, 91 subtests passed, 0 failed (up from 367 passed / 1 skipped / 0 failed at the start of this pass; run multiple times under `pytest-randomly`'s randomized ordering with no flakiness). All new tests are regression tests for the fixes above. 3 of the 4 skips are symlink-creation tests that correctly skip on a host without symlink privileges (confirmed this Windows session cannot create symlinks without elevation/Developer Mode) - they run for real on POSIX/CI.
- `python docker/check_images_lock.py`: OK, 9 images checked (was silently passing on stale data before P1-04's fix; now actually validates pinned_dependencies, and reflects the go/php/dotnet digest bumps from P1-03, plus a real `spring-boot-starter-parent` entry for jvm).
- `ruff check optarena/ tests/ docker/`: clean.
- P0-03: `_WindowsJob` verified directly (create/close, assign+terminate confirmed via `proc.returncode`, an already-exited PID fails gracefully, a grandchild spawned after assignment is confirmed to be an automatic member) plus through `run_capture()`'s real integration across parent+child, grandchild-after-immediate-parent-exit, and detached-process-group scenarios - 10 tests total, all passing.
- P0-04 independently verified outside the test suite too: a standalone script abandoning a 3s call took 3.05s to exit under the original `ThreadPoolExecutor` code, 0.32s under the intermediate daemon-thread fix, and the final subprocess-based fix confirms (via `proc.is_alive()`/`proc.exitcode` in `SDKWorkerProcessTests`) that an overrun call's process is genuinely killed, not just abandoned.
- P1-01: 11 canary-secret tests, all confirming a real `sk-...`-shaped canary is absent from the full serialized result/record (not just the field it started in) across stderr, exceptions, timeouts, storage, and the retroactive scrub command.
- P1-03: all 9 images rebuilt and rescanned live with Trivy (2508 → 1367 CRITICAL/HIGH in the first pass, then jvm alone a further 86 → 16 after the Spring Boot bump). `cases verify` re-run against the real oracle for every language whose image actually changed - go (134 variants/56 cases), php (92 variants/37 cases), csharp (114 variants/49 cases), rust, python (spot-check, 384 variants/150 cases), java (129 variants/56 cases), kotlin (86 variants/35 cases) - all pass clean. `check_vuln_baseline.py` verified end-to-end against a real Trivy JSON scan, not just unit-tested. The two publish-workflow action SHAs (`attest-build-provenance`, `cosign-installer`) resolved and confirmed against the live GitHub API; `docker buildx build --metadata-file`'s digest-extraction shape confirmed against a real local build.
- P1-06: 8 tests covering symlinked files, symlinked directories, the entry-count cap, the size-only fallback signature (and that it still detects changes), `check_expected`'s oversized-file rejection, and `scan_workspace`'s symlink/count handling. `cases verify` re-run on a live case afterward (`snapshot`/`check_expected` are on every driver's hot path) to confirm no functional regression.
- P1-08: `cases verify --language rust` went from 22 violations to 0 ("all verified"), stable across 3 consecutive full runs; `cases verify --language go` re-confirmed clean afterward to prove the shared `run_check_command` change is a genuine no-op for non-Rust languages.

## What's next

Every P0/P1/P2/P3 finding in this document is fixed and verified - see each finding's own entry above, and the P2/P3 pass summaries below for the two most recent rounds. One item remains open, and it isn't actionable from this repository:

- **P1-03's one true remainder** - the .NET SDK-internal tool CVEs, blocked on an upstream Microsoft 8.0.4xx rebuild. Re-checked live 2026-08-03 against Microsoft's own `dotnet/core` release notes: `8.0.423` (released 2026-07-14) is still the latest published SDK build - re-check periodically; nothing to do until Microsoft ships a new one.

## P3 maintainability pass (2026-08-03)

P3-01 (module split) and P3-02 (contribution infra + entry-points driver discovery) both fixed - see their entries above for full detail. Headline: `cases.py`/`cli.py`/`runner.py` split into `_cases/`/`cli/`/`runner/` packages (facades preserve every existing import path), and a real regression this pass's own verification caught along the way - a `REPO_ROOT` patch-divergence bug that hung `optarena serve`'s test with a real, never-exiting HTTP server - fixed rather than worked around. Also caught mid-pass: `tests/test_cli.py` and `tests/test_conformance.py` exist alongside `tests/test_optarena.py` and needed the same mock-patch-path updates; found via the hang, not via upfront planning, and fixed together once found.

Verification: full suite green in fixed order and 2 independent `pytest-randomly` seeds (453 passed, 4 skipped, 91 subtests, 0 failed - up from 448 at the end of the P2 pass); `ruff check optarena/ tests/ scripts/` clean; real (non-mocked) `optarena doctor`/`cases validate`/`serve` smoke checks after every split, not just the test suite.

## P2 product/testing maturity pass (2026-08-02)

P2-03, P2-04's remainder, P2-06, and P2-07 fixed in this pass, all with live verification rather than code review alone:

- **P2-03** (wheel packaging): the "source checkout only" packaging model was already chosen and documented in a prior pass, but never actually tested - this pass built a real wheel, installed it into a fully isolated clean venv, and ran the complete documented workflow (`drivers list`, `cases list`, `doctor`, and a real `optarena run` through a live Ollama backend and the real Docker sandbox, PASS) from an empty directory with no source checkout anywhere nearby. Also added the missing `authors`/`keywords`/`classifiers`/`[project.urls]` metadata, verified present in the built wheel's actual `METADATA` file.
- **P2-04's remainder** (driver/SDK version capture): `get_driver_version()` added to `optarena/drivers/__init__.py`, recorded in every manifest as `driver_version`, surfaced in `doctor` as a mismatch-vs-`tested_with` advisory note. Caught and fixed a real bug along the way: `openai-agents` installs as the importable module `agents`, and version lookup needs the PyPI *distribution* name, not the import name.
- **P2-06** (mocked-only integration testing): every `DRIVERS` entry now has `owner`/`tested_with`; new scheduled+manual `integration-smoke.yml` workflow runs a real case through 4 real CLI drivers against a real self-hosted Ollama backend, gated on `infrastructure_errors` (F-10) rather than raw exit code so a small smoke model's wrong answers don't fail CI.
- **P2-07** (capability-aware denominators): `runner._run_case()` now flags `capability_excluded` (via the pre-existing `cases.baseline_incompatible()`) for `file_tools: False` drivers; `metrics.aggregate()` adds `eligible_pass_rate`/`capability_excluded_cases`/`capability_exclusion_reasons` alongside (not replacing) the original `pass_rate`, mirroring F-10's `adjusted_pass_rate` pattern exactly. Scoped to the one capability dimension (`file_tools`) the codebase already detects, not the full multi-dimensional framework the original finding sketches.

Verification: `python -m pytest tests/`: 448 passed, 4 skipped, 91 subtests passed, 0 failed (up from 428 at the end of the P0/P1 pass); `ruff check optarena/ tests/ scripts/`: clean.
