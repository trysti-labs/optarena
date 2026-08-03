# OptArena unresolved issues

Last reviewed: 2026-08-03

This file contains only findings that remain open or partially resolved. Full
fix narratives and evidence for closed findings live in dev notes, not here -
this stays a short, actionable tracker, not a changelog.

## Status definitions

- **Open:** The underlying problem remains unresolved.
- **Partially resolved:** Meaningful protections exist, but the stated acceptance criteria are not fully met.
- **Needs verification:** The implementation exists but could not be independently exercised in the current environment.

## Release guidance

The source repository is suitable for publication as an **experimental
alpha**. Every P0/P1 release and security blocker from the August 2026
remediation pass is closed and verified live. The one remaining gap below is
a disclosed scope boundary, not a defect - safe to publish with it stated
plainly (as `DRIVERS[...]["status"]` and `doctor` already do).

## Open findings

### P2-03: Paid/fixed-backend CLI driver coverage has no live CI

**Status:** Partially resolved
**Affected files:** `.github/workflows/integration-smoke.yml`, `optarena/drivers/__init__.py`

**Problem**

`claude-code` and `codex` are `backend: fixed` drivers - they use their own
Anthropic/OpenAI account, not the scenario's configured backend. Every other
driver (4 CLI + 6 SDK) now has scheduled live integration coverage against a
free local Ollama backend (`smoke`/`sdk-smoke` jobs). These two remain
mock-only in the unit suite, with no live end-to-end CI run.

**Resolution**

- Get a maintainer decision on funding a small, rate-limited API budget for
  scheduled (weekly, not per-commit) live smoke runs of these two drivers.
- If funded: add a `paid-smoke` job gated on repository secrets, using the
  smallest/cheapest available model from each provider, mirroring the
  existing `smoke` job's exit-2-vs-infrastructure-error acceptance logic.
- If not funded (or until decided): keep both drivers labeled
  `status: experimental` (already true today) so this is visible in
  `optarena drivers list`/`doctor`/documentation rather than silently assumed
  equivalent to the CI-covered drivers.

**Acceptance criteria**

- Either both drivers get real scheduled CI coverage, or the support-tier
  labeling makes the gap impossible to miss for anyone relying on them.

**Current state, 2026-08-03:** the labeling half is already satisfied
(`DRIVERS["claude-code"]["status"]` / `DRIVERS["codex"]["status"]` are both
`"experimental"`, surfaced by `optarena drivers list` and `doctor`). The live
coverage half needs an account/budget decision only a maintainer can make -
not something resolvable by writing more code. Revisit once that decision is
made.

## Closed findings

Every other finding from the August 2026 remediation pass - the original
P0 release blockers, all 7 P1 security/release findings, P2-01/02/04, and
P3-01 - is closed and was independently re-verified against live
infrastructure (not just re-read) as part of this pass: a real
publish-scan-promote registry cycle, a real 9-image Trivy baseline migration,
real PEM/path/pack-signing exploit attempts correctly blocked, a real
workspace-quota kill across all three execution paths, real SDK-driver
wiring confirmed against actual installed packages, a real wheel built and
installed outside the checkout, and a real crewAI run through the live
Ollama + Docker pipeline. Full evidence for each lives in dev notes, not
duplicated here.

## Latest verification baseline

- Full local test suite: **519 passed, 5 skipped, 0 failed, 148 subtests passed**.
- `ruff check`: clean.
- Built-in case schema validation: **836 valid cases**.
- Docker dependency-lock validation: passed for all 9 images.
