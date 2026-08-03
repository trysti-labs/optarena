# OptArena unresolved issues

Last reviewed: 2026-08-03

This file contains only findings that remain open or partially resolved after the August 2026 remediation pass. Historical implementation notes and already-closed findings are intentionally excluded so this remains an actionable tracker.

## Status definitions

- **Open:** The underlying problem remains unresolved.
- **Partially resolved:** Meaningful protections exist, but the stated acceptance criteria are not fully met.
- **Needs verification:** The implementation exists but could not be independently exercised in the current environment.

## Release guidance

The source repository is suitable for publication as an **experimental alpha** if the remaining limitations are disclosed. Do not describe the container images or the overall product as production-ready until the P1 release and security issues below are closed.

## P1 — Security and release blockers

### P1-01: Published images are not gated by the vulnerability scan

**Status:** Open
**Affected files:** `.github/workflows/ci.yml`, `.github/workflows/publish-images.yml`

**Problem**

The `image-vuln-scan` job is blocking within the CI workflow, but image publishing is performed by a separate push-triggered workflow. The publishing workflow does not depend on the successful completion of the vulnerability scan. Both workflows can therefore run independently, and an image can be pushed and signed before a failing scan is known.

**Resolution**

- Make image publication depend on a successful scan of the exact source revision and image digest.
- Prefer a reusable workflow in which build, scan, baseline evaluation, publication, signing, and attestation form one dependency chain.
- Do not assign `latest` or another public release tag until scanning succeeds.
- If scanning must occur after an initial registry push, push to a private/staging tag first and promote the verified digest afterward.
- Add a workflow test or policy check that fails if the publish job has no scan dependency.

**Acceptance criteria**

- A deliberately introduced new HIGH/CRITICAL finding prevents the image from receiving any public release tag.
- The digest scanned is exactly the digest signed and published.
- The release workflow cannot succeed when the vulnerability gate fails.

---

### P1-02: Vulnerability-baseline matching ignores version and fix status

**Status:** Open
**Affected files:** `docker/check_vuln_baseline.py`, `docker/vuln-baseline/*.json`

**Problem**

The checker currently identifies an accepted finding using only `(CVE ID, package name)`. It does not bind acceptance to the scan target, installed version, fixed version, vulnerability status, or an individual justification. A previously accepted CVE/package pair can remain allowed after the installed package changes or after a fix becomes available.

The checked-in baseline also accepts a large CRITICAL/HIGH backlog under broad image-level notes rather than an individual disposition for every finding.

**Resolution**

- Include target, package type, installed version, CVE ID, severity, fixed version, and status in the baseline identity.
- Fail when an accepted finding gains a non-empty fixed version unless the exception is explicitly renewed.
- Require a per-finding justification, owner, review date, and expiry.
- Reject malformed baseline entries and duplicate/conflicting dispositions.
- Add tests for changed installed versions, newly available fixes, severity increases, target changes, and expired exceptions.

**Acceptance criteria**

- An accepted CVE that later becomes fixable fails the gate.
- Changing the installed package version invalidates the old acceptance unless re-triaged.
- Every accepted finding has a documented, time-bounded disposition.

---

### P1-03: PEM private-key redaction retains the key body

**Status:** Open
**Affected file:** `optarena/security.py`

**Problem**

Pattern redaction replaces the PEM `BEGIN ... PRIVATE KEY` header but can leave the base64 key body and closing marker in persisted output. The key remains reconstructable by restoring the standard header.

Configured API-key redaction and single-line scanner-snippet redaction are working; this issue concerns complete multiline PEM blocks.

**Resolution**

- Add a multiline expression that replaces the complete PEM block from its `BEGIN` marker through the matching `END` marker.
- Cover RSA, EC, DSA, OpenSSH, encrypted, and generic private-key headers.
- Apply the same redaction before saving run records, indexes, stderr, exception text, reports, and SARIF.
- Add bounded handling for malformed blocks that have a `BEGIN` marker but no closing marker.

**Acceptance criteria**

- No key body, header, or footer from a canary private key appears in any persisted artifact.
- Tests cover multiline strings and nested result structures.

---

### P1-04: Reactive disruption paths are not fully containment-checked

**Status:** Open
**Affected files:** `optarena/schema.py`, `optarena/_cases/_workspace_setup.py`

**Problem**

`disruptions[].when.file_exists` and `file_contains.path` are validated only as non-empty strings. `file_exists` joins the value to the workspace without resolving and checking containment, allowing a custom case to test whether a path outside the workspace exists. `file_contains` has a runtime containment check, but invalid traversal paths still pass schema validation.

This is not the previously fixed arbitrary-write vulnerability, but it remains a host-path existence oracle and an inconsistent path-security boundary.

**Resolution**

- Run both trigger paths through `reject_unsafe_relpath()` during schema validation.
- Resolve each runtime destination and require it to remain under the resolved workspace root.
- Return `False` or a structured validation error for invalid trigger paths without touching the external path.
- Add POSIX, Windows, mixed-separator, absolute, drive-qualified, UNC, `.git`, and `..` tests.

**Acceptance criteria**

- No case-controlled trigger can query a path outside the workspace.
- Invalid trigger paths fail before a driver or model is invoked.

---

### P1-05: Windows path aliases are not fully normalized

**Status:** Partially resolved
**Affected files:** `optarena/schema.py`, all host-side safe-path call sites

**Problem**

The shared validator rejects exact `.git` segments case-insensitively, traversal, absolute paths, drive prefixes, and NUL bytes. It does not reject every Windows-normalized equivalent or malformed filename. For example, a segment with trailing spaces or dots can pass validation and later fail inconsistently at the filesystem layer.

No command-execution bypass was reproduced through this gap; the observed malformed path failed before Git ran. It remains a defense-in-depth and reliability problem.

**Resolution**

- Reject path segments with trailing spaces or dots on all platforms for cross-platform consistency.
- Reject alternate-data-stream syntax (`:` outside a valid drive prefix), reserved DOS device names, and NT namespace prefixes.
- Compare reserved names after Windows-compatible normalization and case folding.
- Keep the final resolved containment check immediately before every filesystem operation.

**Acceptance criteria**

- Windows aliases of `.git`, reserved devices, ADS paths, and malformed normalized paths fail schema validation.
- The same case pack has consistent validation results across Windows, macOS, and Linux.

---

### P1-06: External packs have integrity checks but no publisher authenticity

**Status:** Partially resolved
**Affected files:** `optarena/packs.py`, pack CLI and documentation

**Problem**

Plain HTTP is now refused by default and pack content hashes detect corruption. The hash is still declared by the pack itself, so it does not prove who created or approved the pack. HTTPS authenticates the transport endpoint, not the pack author.

**Resolution**

- Define a signed pack-manifest format that binds the pack name, version, content hash, requested features, and publisher identity.
- Maintain an explicit trusted-publisher keyring or use a transparent identity-backed signing system.
- Display signature state and signer before installation and execution.
- Require an explicit unsafe flag for unsigned remote packs.
- Record pack origin, signature identity, and verified hash in run manifests.

**Acceptance criteria**

- Modifying a signed pack causes verification failure.
- A signature from an unknown publisher is clearly distinguished from a trusted signature.
- Automated runs can require trusted signatures without interactive prompts.

---

### P1-07: Workspace inspection is bounded, but workspace disk usage is not

**Status:** Partially resolved
**Affected files:** `optarena/_cases/_snapshot.py`, sandbox and workspace lifecycle code

**Problem**

Snapshots now skip symlinks, bound entries, limit per-file reads, and cap total bytes hashed per call. These controls protect the inspection process but do not prevent an agent or check command from filling the host disk before inspection begins. There is no enforced total workspace quota.

Some symlink regressions could not run in the latest Windows review because the host did not permit symlink creation; they rely on POSIX CI coverage.

**Resolution**

- Enforce a per-case workspace byte and inode/file-count quota during execution, not only during snapshotting.
- Prefer a quota-capable temporary volume or sandbox filesystem for untrusted verification.
- Abort the case with a structured resource-limit error when the quota is crossed.
- Confirm file and directory symlink protections on Linux CI.
- Ensure cleanup remains bounded even for a workspace containing thousands of files.

**Acceptance criteria**

- A case attempting to exceed its disk or file-count allowance is terminated without exhausting the host.
- Symlinks cannot expose external file contents through snapshots, scans, reports, or cleanup.

## P2 — Product-readiness gaps

### P2-01: The installed wheel does not contain every product asset

**Status:** Open product decision
**Affected files:** `pyproject.toml`, `README.md`, CLI asset discovery

**Problem**

The wheel contains the Python package and case JSON files but intentionally excludes Docker build definitions, starter repositories, and the dashboard. A clean wheel installation can run cases when the required images already exist, but it cannot provide every documented source-checkout feature or run starter-repository cases without external assets.

This can be an acceptable alpha packaging model, but it does not fully resolve the original "complete usable installation" finding.

**Resolution options**

- Bundle all required assets in the wheel and access them through `importlib.resources`; or
- Publish signed, versioned asset bundles that the CLI downloads and verifies; or
- Explicitly remain source-checkout-only and do not market the wheel as a complete installation.

**Acceptance criteria**

- The supported installation model is stated consistently in README, package metadata, CLI help, and release notes.
- Every documented command either works from that installation or gives a precise remediation message.

---

### P2-02: Run manifests still cannot guarantee exact reproduction

**Status:** Partially resolved
**Affected files:** `optarena/runner/_manifest.py`, `optarena/scenario.py`, `optarena/schema.py`

**Problem**

Manifests now record platform, Python, Git state when available, and driver/SDK versions. Remaining gaps include:

- Exact build identity for non-Git wheel installations.
- Provider/server version.
- Temperature, seed, top-p, and other generation parameters.
- Compatibility rules across oracle versions.
- `ORACLE_VERSION` remains `1` despite earlier evaluation-semantic changes.

**Resolution**

- Embed a build commit or immutable build identifier into packages and images.
- Extend scenario configuration with supported generation parameters and record effective values.
- Record backend/provider version where it can be detected.
- Define which evaluator changes require an oracle-version increment.
- Refuse or clearly warn on comparisons across incompatible oracle versions.

**Acceptance criteria**

- A saved result identifies the evaluator build, effective generation settings, driver/provider versions, cases, and images without persisting secrets.
- Incompatible results cannot be silently compared as equivalent.

---

### P2-03: Real integration coverage remains incomplete

**Status:** Partially resolved
**Affected files:** `.github/workflows/integration-smoke.yml`, driver registry

**Problem**

Scheduled integration testing now exercises four scenario-configurable CLI drivers against a real local backend. Paid/fixed-backend CLI agents and the optional SDK drivers remain covered primarily by mocks. The workflow also installs some external tools through unversioned remote installer scripts, reducing reproducibility.

**Resolution**

- Pin every tested CLI/SDK version and verify downloaded installer checksums or signatures.
- Add controlled integration coverage for SDK drivers.
- Add an explicit support tier for drivers that cannot receive live CI coverage.
- Make version drift either intentionally tested or a visible scheduled failure, rather than an informational doctor message only.

**Acceptance criteria**

- Every stable driver has a real end-to-end test against a recorded version.
- Experimental/unverified drivers are clearly labeled in CLI and documentation.

---

### P2-04: Capability-aware scoring covers only one capability dimension

**Status:** Partially resolved
**Affected files:** `optarena/metrics.py`, runner capability-exclusion logic

**Problem**

Eligible pass-rate reporting now excludes known flat-file-driver incompatibilities. There is not yet a general capability model for multi-file editing, shell access, repository navigation, network use, persistent sessions, tool calling, or other case requirements.

**Resolution**

- Define a stable driver-capability schema.
- Declare required capabilities on cases.
- Validate capability declarations and report explicit exclusion reasons.
- Present raw and eligible denominators together in CLI, reports, comparisons, and dashboard.

**Acceptance criteria**

- Every capability exclusion is deterministic and visible.
- Cross-driver comparisons state the exact common eligible case set.

## P3 — Maintenance follow-ups

### P3-01: Public support and governance policy remains informal

**Status:** Partially resolved

Issue templates, a pull-request template, a code of conduct, driver entry points, and signed image releases now exist. A concise maintainer/governance policy and supported-version policy are still needed.

**Resolution**

- Document maintainers, decision process, security response ownership, support channels, and supported release branches.
- Define deprecation and compatibility policies for case schema, drivers, results, and oracle versions.

## Closed release blockers

The following previously reported P0 findings were retested and are no longer tracked as open issues:

- Case-controlled Git configuration/filter execution.
- Raw/SDK arbitrary writes outside the workspace.
- Windows timed-out process trees surviving cleanup.
- Synchronous SDK requests continuing after their reported timeout.

Focused verification: 23 P0 regression tests passed on Windows.

## Latest verification baseline

- Full local test suite: **457 passed, 4 skipped, 0 failed**.
- Built-in case schema validation: **836 valid cases**.
- Python compilation: passed.
- Docker dependency-lock validation: passed for all 9 images.
- Docker image builds and fresh Trivy scans were not rerun locally because the Docker daemon was unavailable.
- The skipped tests were environment-dependent symlink checks plus one platform-dependent test; confirm them on Linux CI.

## Recommended order

1. Couple vulnerability scanning to image publication.
2. Strengthen vulnerability-baseline identity and exception metadata.
3. Fix complete PEM redaction and reactive-trigger path containment.
4. Harden Windows path normalization.
5. Add pack-signature/publisher verification.
6. Enforce workspace disk/file quotas.
7. Decide and complete the wheel/asset distribution model.
8. Complete reproducibility metadata and integration coverage.
9. Expand capability-aware scoring and publish governance policy.
