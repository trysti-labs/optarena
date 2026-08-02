# OptArena findings and resolution plan

This document tracks the security, reliability, product, and maintenance findings identified during the August 2026 repository review.

> **Release warning:** Do not make this repository public until the P0 findings are fixed and independently retested. This file describes sensitive security boundaries and should remain private until those fixes are released.

## Priority definitions

- **P0 — Release blocker:** Fix before making the repository public.
- **P1 — High priority:** Fix before publishing official packages or container images.
- **P2 — Product readiness:** Fix for a credible public alpha or shortly afterward.
- **P3 — Maintenance:** Important for long-term contributor health, but not an immediate security blocker.

## P0 — Release blockers

### P0-01: Case-controlled Git configuration can execute host commands

**Status:** Open  
**Type:** Security / host command execution  
**Affected code:** `optarena/cases.py`, `optarena/schema.py`, `SECURITY.md`

**Finding**

Custom cases may place files under `.git/`, including `.git/config` and `.gitattributes`. When `git_init` is enabled, OptArena subsequently runs host-native `git init`, `git add -A`, and `git commit`. Git filters configured by case content can therefore be executed on the host during `git add`, before container verification begins.

**Impact**

Installing and running an untrusted case pack could execute commands with the OptArena user's permissions. This contradicts the expectation that case behavior is contained inside the verification sandbox.

**Resolution**

- Reject `.git` and all descendants as case-controlled paths, case-insensitively.
- Apply the restriction to `setup_files`, disruptions, pack files, repository fixtures, and every future file-writing feature.
- Create the Git repository only after all case paths have been validated.
- Run Git with a minimal allowlisted environment and isolated system/global configuration.
- Disable hooks, filters, external diff commands, and other configurable command execution.
- Consider removing `git_init` from the first public release if it is not essential.
- Add a regression test using a harmless marker command and assert that the case is rejected before Git runs.

**Acceptance criteria**

- A case containing `.git/config`, `.GIT/config`, or an equivalent normalized path fails validation.
- No case-controlled Git hook, filter, pager, editor, diff driver, or configuration command can run on the host.
- Existing bundled `git_init` cases still prepare a clean initial commit.

---

### P0-02: Expected-file paths can escape the workspace

**Status:** Open  
**Type:** Security / arbitrary file write  
**Affected code:** `optarena/schema.py`, `optarena/drivers/openai_chat.py`, `optarena/drivers/sdk_base.py`

**Finding**

`expected_files[].path_pattern` is checked only for being a non-empty string. Raw and SDK drivers convert the first pattern to a concrete destination and write to `workspace / target` without resolving the final path and confirming that it remains inside the workspace. Patterns such as `../outside.py` can escape it.

**Impact**

A custom case can steer model-generated content into arbitrary host paths writable by the current user.

**Resolution**

- Introduce one shared path-validation and safe-join function.
- Reject absolute paths, drive-qualified paths, UNC paths, `..`, NUL bytes, and reserved internal directories.
- Resolve the final destination and require it to be a descendant of the resolved workspace root.
- Apply the same helper to expected files, disruptions, setup files, snapshots, pack installation, and trigger paths.
- Validate before contacting a model so an invalid case cannot consume API credits.
- Add Windows and POSIX regression tests.

**Acceptance criteria**

- Traversal, absolute, drive-qualified, UNC, and mixed-separator escape attempts fail validation.
- Every host-side write has a containment assertion immediately before it occurs.
- Valid nested paths and supported glob patterns continue to work.

---

### P0-03: Timed-out process trees can survive on Windows

**Status:** Open  
**Type:** Reliability / process isolation  
**Affected code:** `optarena/cases.py`, `tests/test_optarena.py`

**Finding**

The Windows process-tree timeout test fails because a child process can remain alive after its parent times out. The current `CREATE_NEW_PROCESS_GROUP` plus `taskkill /F /T` approach is not a reliable lifetime boundary.

**Impact**

Agent processes, language servers, or commands spawned by them may continue consuming CPU, memory, network access, or credentials after the run reports a timeout.

**Resolution**

- Use a Windows Job Object configured to terminate all assigned processes when its handle closes.
- Assign the process to the job before allowing normal execution to proceed.
- Retain a bounded fallback cleanup path and report cleanup failure explicitly.
- Ensure timeout handling cannot block for another full case timeout.
- Test parent, child, grandchild, and detached-child scenarios.

**Acceptance criteria**

- The existing child-process-tree test passes repeatedly on Windows CI.
- No descendant remains after the cleanup grace period.
- Cleanup failures appear as explicit run errors rather than being silently ignored.

---

### P0-04: Synchronous SDK timeout does not cancel the request

**Status:** Open  
**Type:** Reliability / cost control  
**Affected code:** `optarena/drivers/sdk_base.py`

**Finding**

Synchronous SDK calls run in a `ThreadPoolExecutor`. Calling `future.cancel()` cannot stop work that has already started, and `shutdown(wait=False)` does not guarantee that the worker cannot delay interpreter shutdown. The session may also be closed while the abandoned worker is still using it.

**Impact**

Requests can continue after OptArena reports a timeout, consume API credits, race with session cleanup, and prevent the process from exiting promptly.

**Resolution**

- Prefer provider/transport-level connect, read, write, and total timeouts.
- Run uncooperative synchronous SDK calls in a killable child process.
- Return structured timeout and cleanup information.
- Do not close a session while another worker can still access it.
- For async drivers, verify that cancellation reaches the underlying transport rather than only cancelling the wrapper coroutine.

**Acceptance criteria**

- A deliberately blocked SDK call cannot outlive the configured deadline plus a small cleanup grace period.
- The OptArena process exits promptly after a timed-out call.
- Tests verify background work has stopped, not merely that `run_case()` returned.

## P1 — Security and distribution readiness

### P1-01: Credentials can be persisted through stderr

**Status:** Open  
**Type:** Security / secret exposure  
**Affected code:** `optarena/drivers/cli_agents.py`, `optarena/drivers/aider_cli.py`, result storage

**Finding**

CLI agents receive credentials through environment variables, but their stderr tails are persisted without secret redaction. A tool can intentionally or accidentally echo an API key.

**Resolution**

- Redact known configured secrets from stdout, stderr, exceptions, and result extras before persistence.
- Add pattern-based redaction for common token formats as defense in depth.
- Extend `runs scrub-secrets` to scan all stored string fields recursively.
- Add canary-secret tests covering failures, timeouts, exceptions, and dashboard data.

**Acceptance criteria**

- A canary API key never appears in any saved result, index, comparison, log, or dashboard payload.

---

### P1-02: Secret scanner saves the secret-bearing source line

**Status:** Open  
**Type:** Security / secret exposure  
**Affected code:** `optarena/security.py`

**Finding**

When a secret rule matches, the scanner stores up to 160 characters from the original line as a snippet. The persisted finding can therefore contain the secret it detected.

**Resolution**

- Never retain the original match for secret-related rules.
- Store the rule identifier, filename, line number, and a fully redacted preview.
- Apply recursive redaction before writing security findings.

**Acceptance criteria**

- Scanner fixtures containing canary secrets produce useful findings without persisting any part of the secret.

---

### P1-03: Critical/high container findings do not block CI

**Status:** Open  
**Type:** Supply-chain security  
**Affected code:** `.github/workflows/ci.yml`, Docker images

**Finding**

The image vulnerability scan is configured with `continue-on-error: true` while a known backlog is being tolerated.

**Resolution**

- Generate a current report for every published image.
- Upgrade or replace vulnerable base images and packages.
- Document any accepted finding with justification, scope, owner, and expiry date.
- Fail release builds on new unfixed CRITICAL/HIGH findings.
- Produce an SBOM and attach it to each image release.
- Sign images and publish provenance/attestations.

**Acceptance criteria**

- The image security job is blocking for release commits.
- Every remaining exception is documented, time-bounded, and reviewed.

---

### P1-04: Docker dependency ledger is not fully enforced

**Status:** Open  
**Type:** Reproducibility / supply chain  
**Affected code:** `docker/images.lock.json`, `docker/php/Dockerfile`, `docker/check_images_lock.py`

**Finding**

The lock file records PHPUnit `11.5.56`, while the PHP image installs `12.0.0`. The lock checker validates base-image information but does not verify all `pinned_dependencies` values against Dockerfiles.

**Resolution**

- Update the PHPUnit ledger entry.
- Extend the checker to validate every declared pinned dependency.
- Prefer machine-readable lock inputs used directly during the build rather than duplicated documentation values.
- Add a CI test that deliberately changes a pinned version and expects the checker to fail.

**Acceptance criteria**

- Lock metadata and built tool versions cannot drift without CI failure.

---

### P1-05: Host-native agents need a stronger trust boundary

**Status:** Open  
**Type:** Product security / user safety  
**Affected code:** CLI, `README.md`, `SECURITY.md`

**Finding**

CLI agents execute on the host with the user's filesystem, environment, credentials, and network access. Container isolation protects verification, not agent execution.

**Resolution**

- Show a prominent warning and confirmation before running a host-native agent with an external pack.
- Add `--yes-i-understand-host-execution` or an equivalent explicit non-interactive acknowledgement.
- Clearly label drivers as host-native or isolated in `doctor`, help output, run output, and reports.
- Investigate an opt-in container/VM execution backend for agents.
- Make verifier containers non-root by default once the corpus passes under that mode.

**Acceptance criteria**

- A user cannot reasonably mistake verifier isolation for agent isolation.
- Automation must explicitly acknowledge unsafe host-native execution.

---

### P1-06: Symlinks and unbounded workspaces can leak or exhaust resources

**Status:** Open  
**Type:** Security / denial of service / privacy  
**Affected code:** workspace snapshotting, diff collection, security scanning

**Finding**

Workspace inspection can follow symlinked files, and there are no strong file-count, per-file-size, or total-workspace-size limits.

**Resolution**

- Skip symlinks consistently unless a feature explicitly supports them safely.
- Resolve every inspected path and verify workspace containment.
- Limit file count, individual file size, total bytes, and traversal depth.
- Stream hashes and scans rather than loading large files fully into memory.
- Report truncated/skipped content in structured result metadata.

**Acceptance criteria**

- Symlinks cannot expose files outside the workspace through results or scans.
- Oversized workspaces fail cleanly without exhausting disk or memory.

---

### P1-07: External case packs lack a strong trust and provenance model

**Status:** Open  
**Type:** Supply-chain security  
**Affected code:** `optarena/packs.py`, pack documentation

**Finding**

Pack installation permits insecure transport and relies on self-declared hashes without publisher authentication. Packs influence host-side workspace preparation and agent prompts.

**Resolution**

- Require HTTPS by default and require an explicit unsafe flag for local testing exceptions.
- Verify downloaded content against a hash supplied through a trusted channel.
- Add signed manifests and trusted publisher identities.
- Display pack origin, signer, hash, requested features, and trust status before installation/run.
- Reject filenames that cannot later be loaded as cases.

**Acceptance criteria**

- A modified or unsigned trusted pack is rejected.
- The user can clearly distinguish bundled, verified, local, and untrusted packs.

## P2 — Product and open-source readiness

### P2-01: Documentation and corpus counts are stale

**Status:** Open  
**Type:** Documentation

**Finding**

The validated corpus contains 836 cases, while `README.md`, `ARCH.md`, source comments, and CI comments still reference 510. `CHANGELOG.md` also links to a missing `AUDIT_2.md`.

**Resolution**

- Generate case totals and language counts through one script.
- Use generated output in documentation or make CI verify documented counts.
- Remove or replace references to missing internal documents.

**Acceptance criteria**

- `optarena cases validate`, documentation, and release notes report consistent totals.

---

### P2-02: Internal audit history is too large and stale for public documentation

**Status:** Open  
**Type:** Documentation / repository hygiene  
**Affected code:** `AUDIT.md`

**Finding**

`AUDIT.md` is a large chronological development diary containing superseded conclusions, stale paths, old counts, and internal references.

**Resolution**

- Replace it with a concise current security assessment and release checklist.
- Move historical notes to a private archive or a clearly labeled historical document.
- Perform a final secret, identity, internal URL, and filesystem-path review before publication.

**Acceptance criteria**

- Public documentation describes the current implementation and contains no obsolete private-development references.

---

### P2-03: The wheel does not provide a complete usable installation

**Status:** Open  
**Type:** Packaging / adoption  
**Affected code:** `pyproject.toml`, CLI asset discovery, `README.md`

**Finding**

The case corpus, Docker definitions, starter repositories, and dashboard are not bundled into the wheel. Important commands therefore require a source checkout.

**Resolution**

- Choose and document one packaging model:
  - bundle assets as package data, or
  - publish versioned asset bundles downloaded and verified by the CLI.
- Use `importlib.resources` for packaged assets.
- Test the full documented workflow from an installed wheel in a clean directory.
- Add complete project metadata, maintainers, project URLs, and support links.

**Acceptance criteria**

- A clean installation can validate cases, run a small scenario, verify it, and open the dashboard without access to the source tree.

---

### P2-04: Run manifests are not sufficient for exact reproduction

**Status:** Open  
**Type:** Reproducibility  
**Affected code:** `optarena/runner.py`, scenario schema

**Finding**

Manifests record useful case and image hashes but omit the exact source revision/build, driver version, external CLI version, SDK/provider version, platform details, and common generation parameters. `ORACLE_VERSION` also remains unchanged across behavior changes.

**Resolution**

- Record Git commit or build identifier, dirty-state flag, Python/platform details, driver version, binary version, provider/SDK version, and full non-secret generation parameters.
- Increment `ORACLE_VERSION` whenever pass/fail semantics change.
- Define compatibility rules for comparing runs from different oracle versions.

**Acceptance criteria**

- A result identifies the exact evaluator build and material execution parameters without storing secrets.

---

### P2-05: Unknown remote-model pricing is represented as zero

**Status:** Open  
**Type:** Product correctness  
**Affected code:** `optarena/pricing.py`, metrics and dashboard

**Finding**

An unmatched remote model can receive an estimated cost of `0.0`. Unknown cost is not the same as free and can mislead users.

**Resolution**

- Return `None` for unknown pricing and reserve zero for explicitly free/local models.
- Record pricing source, currency, and `as_of` date.
- Render unknown values as `N/A`, not `$0.00`.
- Prevent partial-known totals from being presented as complete totals.

**Acceptance criteria**

- Unknown models never appear free unless explicitly configured as free.

---

### P2-06: External driver integrations are tested mainly through mocks

**Status:** Open  
**Type:** Quality / integration testing

**Finding**

The core unit suite is substantial, but external CLI agents and SDK frameworks are largely validated with fake binaries, fake sessions, or mocked HTTP behavior.

**Resolution**

- Add scheduled or manually triggered integration tests for supported drivers.
- Maintain a small, cheap smoke corpus for live integrations.
- Record tested driver/SDK versions and clearly label experimental integrations.
- Define a support tier and owner for every registered driver.

**Acceptance criteria**

- Every non-experimental driver has a passing real integration test against a documented version.

---

### P2-07: Cross-driver scores need capability-aware denominators

**Status:** Open  
**Type:** Product correctness / benchmark fairness

**Finding**

Raw and single-file SDK drivers cannot satisfy every multi-file or tool-dependent case, but aggregate scores can still make unlike capabilities appear directly comparable.

**Resolution**

- Declare driver capabilities such as multi-file editing, shell tools, repository navigation, and multi-turn state.
- Mark cases with required capabilities.
- Report overall, eligible-case, and capability-segment scores separately.
- Never silently remove incompatible cases; show the denominator and exclusion reason.

**Acceptance criteria**

- Every displayed percentage includes a clear numerator, denominator, and capability scope.

## P3 — Maintainability and community readiness

### P3-01: Core modules are oversized and carry excessive audit-history comments

**Status:** Open  
**Type:** Maintainability  
**Affected code:** `optarena/cases.py`, `optarena/cli.py`, `optarena/runner.py`

**Resolution**

- Split path safety, process execution, sandboxing, corpus preparation, oracle evaluation, and CLI commands into focused modules.
- Keep security invariants close to code, but move historical narratives to ADRs or issue history.
- Introduce coverage reporting and dead-code checks after refactoring.

**Acceptance criteria**

- Each module has a clear responsibility and security-sensitive helpers have focused tests.

---

### P3-02: Public contribution and release infrastructure is incomplete

**Status:** Open  
**Type:** Open-source maintenance

**Resolution**

- Add issue templates, a pull-request template, code of conduct, support policy, and governance/maintainer information.
- Establish a stable default branch and version-tagging policy.
- Automate changelog, package, SBOM, provenance, signing, and release creation.
- Add third-party driver discovery through Python entry points rather than requiring edits to the core registry.

**Acceptance criteria**

- A new contributor can report, develop, test, and submit a change using only public documentation.
- A maintainer can create a reproducible signed release through CI.

## Verification baseline

The following was observed during the review and should be used as the starting point for remediation:

- `python -m optarena cases validate`: 836 structurally valid cases.
- Python compilation: passed.
- CLI help/version smoke checks: passed.
- Unit tests with sandbox execution disabled: 368 run, 367 passed, 1 skipped, 1 failed.
- Failing test: Windows child-process-tree cleanup after timeout.
- Docker image builds/scans were not rerun locally because the Docker daemon was unavailable in the review environment.

## Suggested implementation order

1. Create one shared path-safety module and fix P0-01/P0-02 together.
2. Replace Windows process cleanup and SDK thread abandonment.
3. Add comprehensive secret redaction before any result persistence.
4. Triage and block container vulnerabilities; enforce the dependency ledger.
5. Strengthen case-pack trust and host-agent warnings.
6. Correct documentation, packaging, reproducibility, and pricing semantics.
7. Add real driver integration testing and capability-aware scoring.
8. Complete public contribution and signed-release infrastructure.
