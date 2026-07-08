# OptArena Corpus Expansion Plan & Platform Gap Analysis

_Date: 2026-07-08. Companion documents:
`OptArena_Benchmark_Corpus_Specification.md` (the target),
`EXPANSION_ANALYSIS.md` (2026-07-02 landscape scan, priority shortlist rows
1-19 all shipped or explicitly deferred), `ARCH.md` §10.1 (corpus status).
This document plans what comes AFTER the 120-case Phase 1 corpus and the
2026-07-08 oracle-hardening pass (mutation testing, port isolation, csproj/
perf fixes - see commit 32dbd3d)._

## Where the corpus stands

120 cases, exactly the spec's Phase 1 per-track allocation, all difficulty
Level 1-2 (single/few-file). Every case hand-verified; the 13 testing-category
cases are now **mutation-checked** (model tests must pass against the correct
implementation AND fail against deliberately broken variants). Category
distribution is already close to the spec's target mix (feature 25%, bug_fix
19%, refactoring 13%, testing 11%, security 13%, performance 8%, devops 7%,
untagged legacy 6%).

Two corpus-quality lessons from verification, which shape everything below:

1. **Every oracle needs a "must-fail" direction.** Six refactoring cases
   passed a do-nothing model (fixed with consolidation assertions); the C#
   perf case passed the unoptimized O(n²) code (fixed with n=200k); three
   dotnet cases failed every correct solution (csproj glob bug). None of
   these were visible by reading the case - only by running both a correct
   and a broken solution through the real oracle.
2. **Hand-verification does not survive edits.** Cases verified once drift
   as prompts/tests/images change. Verification must be automated and re-run.

## Priority 0 - corpus self-verification (before writing any new case)

Add two optional case fields and one CI command:

```json
"reference_solution": {relpath: content},   // must PASS the full oracle
"broken_solutions":  [{relpath: content}]   // each must FAIL it
```

`optarena verify-corpus [--cases ...]` writes setup + reference into a
workspace, runs the real oracle (Docker sandbox included), asserts pass;
then each broken variant (plus the always-available "unmodified setup_files"
variant for fix/refactor/optimize cases), asserts fail. Exit non-zero on any
violation. Run it in CI on every case edit.

This is exactly the manual protocol that caught all five corpus bugs to
date, made repeatable. It also doubles as living documentation of what each
case considers a solution. Estimated effort: S (the oracle plumbing already
exists - `evaluate_case` + `DockerSandbox`); backfilling reference solutions
for 120 cases is the long tail - do it track by track, new cases first.

## Phase 1.5 - deepen the existing tracks (target: 120 → ~200)

Stay inside the existing 7 sandbox images; no new toolchains. Ordered by
value:

1. **Django (8-10 cases).** The python image already ships django; the spec
   and README named it; zero cases exist. Models/admin/ORM query tasks, a
   management-command task, a middleware bug fix. Verification via
   `django.test.Client` + sqlite, fully offline.
2. **Level 2 multi-file cases (15-20).** The schema already supports
   multi-file `setup_files`; what's missing is cases that exercise
   cross-file reasoning: a 5-15 file FastAPI/Express/Spring service where
   the fix spans router + service + model, a "wire the new endpoint through
   all three layers" feature, a cross-module refactor. This is the cheapest
   step toward the spec's difficulty ladder and the strongest
   discriminator between raw models and agents (agents can explore; the
   baseline driver physically cannot - it writes one file).
3. **Category rebalance (10-15).** Bring every track to the spec's
   distribution: notably performance (9 → ~12) and devops (8 → ~12, e.g.
   Dockerfile-lint cases, more GH Actions variants), and tag the 7 legacy
   untagged cases.
4. **Next.js and TypeScript-proper (6-8).** The two dropped JS frameworks.
   Next.js needs `next build` in the node image (add at image build time;
   ~200MB). TypeScript cases (tsc strict-mode fixes, type-level tasks)
   need only the already-installed `typescript` - cheap and highly
   representative of 2026 work. Angular stays dropped (heaviest scaffold,
   least marginal signal).
5. **Mutation checks beyond the testing category.** The bug_fix/security
   cases already fail on unmodified code, but several would pass a fix that
   also breaks an adjacent behavior the hidden test doesn't cover. Sweep
   each and add one "collateral damage" assertion where missing (the
   fix_wrong_join_type / fix_missing_not_null class).

## Phase 2 - repository-scale cases (Level 3; target: +30-50)

The spec's Level 3 band (20-100 files: auth, async, DB migration, API
integration) is where SWE-bench-class benchmarks live and where the corpus
is empty. Two schema additions unlock it without breaking the "a case is
one JSON file" property:

- `"setup_repo": "repos/<name>"` - a directory (or tarball) checked into a
  new top-level `repos/` tree, copied into the workspace before the run.
  Inline `setup_files` stays for Level 1-2; repos are shared and versioned
  across cases (one starter repo, many tasks - the SWE-bench Pro pattern,
  and the spec's own "Starter repository" field).
- `"git_init": true` - initialize the workspace as a git repo (several CLI
  agents behave differently/better inside one; also enables diff-based
  metrics against a real baseline commit).

Author 3-5 starter repos (one per major track: FastAPI+SQLAlchemy+alembic,
Express+TS, Spring Boot multi-module, Gin, Axum) of 30-80 files each, then
6-10 tasks per repo: add auth middleware, fix an async race, write a
migration, integrate a mocked external API. Hidden tests are real
integration tests (the images already run servers offline).

Per-suite groupings become meaningful here: tag cases into **Arena Lite**
(20 cases, <5 min), **Standard** (~100), **Extended** (all), as the spec
defines - implemented as a `suite` tag + `--suite` filter (S effort).

## Phase 2+ - new language tracks (spec Phase 2)

Priority strictly by image feasibility and 2026 usage:

| Track | Image cost | Notes |
|---|---|---|
| Kotlin (Spring/plain) | ~free (jvm image + kotlin plugin warm) | Highest value/effort ratio |
| PHP / Laravel | small new image | Huge install base; composer warms offline |
| Ruby / Rails | small new image | Same pattern |
| TypeScript-first (Deno/Bun) | small | Rising fast in 2026 |
| Swift / Dart-Flutter / Android | **defer** | Need macOS hosts or SDK-heavy images; poor fit for `--network none` sandboxes today |

Each new track ships only with: image warmed offline, ≥8 cases across ≥4
categories, reference+broken solutions, `verify-corpus` green.

## Platform gap analysis (what's missing beyond cases)

Ordered by impact; informed by the 2026 landscape (agent-stack indexes now
rank model+harness *pairs* on accuracy, tokens, and $/task; contamination
and "passes-tests-but-semantically-wrong" are the active criticisms of
SWE-bench-class suites).

1. **Per-driver token/cost/turn telemetry (M, very high).** Today only the
   baseline drivers report tokens; aider and every CLI agent report
   nothing, so cost comparison - the industry's current axis - silently
   degrades to duration. Aider prints per-message token/cost lines to
   stdout; `claude -p` supports `--output-format json` with usage and
   cost; Codex/OpenCode/Goose have similar structured outputs. Parse each
   in its driver, populate the same `extra` keys the baselines use, and
   the existing aggregate/compare/pricing plumbing lights up unchanged.
   Add `tool_calls`/`turns` where the tool reports them (the
   EXPANSION_ANALYSIS row-17 deferral, now unblocked for JSON-output
   agents).
2. **Stability surfaced in compare (S, high).** `--trials` exists but the
   compare table shows only the majority verdict. Add a per-case
   `pass_rate_trials` column and a suite-level flake count - "3/3 vs 2/3"
   is a different claim than "pass vs pass", and single-trial certainty is
   the most common external criticism of small suites.
3. **Corpus self-verification CI** - Priority 0 above; it is as much a
   platform feature as a corpus one.
4. **Contamination hygiene (M, high for credibility).** All 120 cases are
   public in this repo, so any post-2026 model may have trained on them.
   Cheap mitigations: (a) a `variants` mechanism that programmatically
   perturbs literals per run (function names, expected strings, port
   numbers - the oracle already parametrizes these in the new mutation
   scripts); (b) first-class support for *private case packs*
   (`--cases-dir` already works; document the pattern, add
   `optarena init --pack`); (c) date-stamp corpus releases so results cite
   a version.
5. **Headless CI mode (M, medium).** Xvfb wrapper for UI drivers on Linux
   (shortlist row 13, still open). CLI/baseline drivers already work in
   CI; this plus `optarena regression`'s exit code completes the "CI for
   AI coding systems" story in the vision doc.
6. **Publish sandbox images (S, medium).** `optarena docker build --all`
   takes ~30 min and ~7GB from cold. Push tags to GHCR and have the tool
   `docker pull` with build as fallback; case authors and CI get minutes
   back.
7. **Dashboard matrix grid (M, medium).** Row 8's deferred half: the
   N-scenario terminal table exists; the dashboard still renders pairs
   only.
8. **Case recorder (M, medium; row 13 of the old shortlist).** Derive a
   case skeleton from a real workspace diff. With `reference_solution` in
   the schema (Priority 0), the recorder can emit reference solutions for
   free - record once, verify forever.
9. **Driver validation pass (S each, medium).** Six drivers are still
   `experimental` (claude-code, codex, opencode, goose, qwen-code,
   kilo-ui). Validate on a machine with each installed, promote or fix;
   add the 2026 newcomers behind the same descriptor pattern
   (`cursor-agent` CLI, Copilot CLI as `fixed`-backend, OpenHands CLI,
   Amp/Crush on demand).
10. **Latency percentiles + cache metrics (S, low-med).** p50/p95 per
    scenario next to the mean (data already in the run JSON), and prompt
    cache-hit rate where backends report it - both now standard columns in
    public agent indexes.
11. **LLM-judge scoring** - remains deliberately out (subjective scores
    would dilute the behavioral oracle's credibility; unchanged position).

## Sequencing

1. `verify-corpus` + reference/broken solution fields (unlocks safe
   authoring at scale).
2. Phase 1.5 case waves (Django → multi-file Level 2 → rebalance → Next/TS),
   each wave landing with reference solutions.
3. Telemetry (gap 1) + stability column (gap 2) - makes every existing
   comparison more valuable while case authoring proceeds in parallel.
4. Level 3 repos (`setup_repo`, `git_init`, suites).
5. Kotlin/PHP/Ruby tracks + image publishing + headless CI.

Everything stays inside the essence tests from `EXPANSION_ANALYSIS.md` §1:
local-first, comparison-first, one JSON file per case, no leaderboard-farm
drift.
