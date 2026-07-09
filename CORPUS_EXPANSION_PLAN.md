# OptArena Corpus Expansion Plan: 120 → 500

_Date: 2026-07-09 (supersedes the 2026-07-08 ~200-case plan). Companion
documents: `OptArena_Benchmark_Corpus_Specification.md` (the spec; 500 sits
inside its Phase 2 band of 400-600), `EXPANSION_ANALYSIS.md` (landscape scan +
shipped shortlist), `ARCH.md` §10.1-10.2 (corpus + 0.1-cut status)._

**The strategic premise:** the moat is the test corpus, and the moat is
measured in *replication cost*. 120 public cases is a weekend of determined
copying; what is genuinely hard to replicate is (a) volume × breadth, (b) the
verification discipline behind every case - each one ships a
`reference_solution` that must PASS the real Docker-sandboxed oracle and
`broken_solutions` that must FAIL it, enforced by `verify-corpus` in CI - and
(c) the offline sandbox-image engineering that makes hidden tests run with
`--network none` across a dozen toolchains. A 500-case corpus with that
discipline is roughly a person-quarter of skilled, domain-spread authoring
plus infra work. That's the moat.

## Where the corpus stands (measured 2026-07-09)

- **120 cases** across 11 tracks: Python 20, JS 20, Java 15, Go 10, Rust 10,
  C# 10, C/C++ 10, SQL 10, Shell 5, Compose 5, Terraform 5.
- Categories: feature 30, bug_fix 23, security 15, refactoring 15, testing
  13, performance 9, devops 8, untagged 7. **Zero** cases in three spec
  categories: data engineering, dependency upgrades, documentation.
- Difficulty: L1 60, L2 53 (7 untagged). **Zero** Level 3+.
- Only **4** cases have ≥4 setup files; only **1** is multi-prompt. Almost
  everything is single-file, single-shot - the regime where a raw-model
  baseline is *most* competitive with agents, i.e. the least discriminating
  regime.
- Infrastructure shipped (2026-07-08): `verify-corpus` + reference/broken
  solution schema (16 cases backfilled, 91 variants green in CI), mutation
  checking on all 13 testing-category cases, GHCR-published images,
  aider/claude-code cost telemetry, trials-stability metrics.

The whitespace is therefore not "more of the same L1 cases" - it is
**multi-file, multi-step, more workflows, more stacks, and a difficulty
ladder**, verified to the same standard.

## Target allocation: what 500 looks like

### By track (language / stack)

| Track | Now | Target | Δ | Framework spread at target |
|---|---:|---:|---:|---|
| Python | 20 | 90 | +70 | FastAPI 15, Django 12, Flask 10, pandas/data-eng 10, stdlib/algorithms 10, asyncio 8, SQLAlchemy 8, Typer/CLI 6, pytest workflows 6, Pydantic 5 |
| JavaScript/TypeScript | 20 | 90 | +70 | Express 15, React 12, **TypeScript-strict 12**, Node core (streams/fs/events) 12, testing workflows 9, **Next.js 8**, NestJS 8, Vue 8, **Svelte 6** |
| Java | 15 | 45 | +30 | Spring Boot web 15, plain Java 10, **Spring Data/JPA (H2) 8**, JUnit workflows 6, Maven multi-module 6 |
| Go | 10 | 40 | +30 | net/http stdlib 8, Gin 8, **goroutines/races 8**, testing 7, stdlib CLI 4, Fiber 5 |
| Rust | 10 | 40 | +30 | Axum 8, **ownership/borrow bugs 8**, tokio concurrency 6, traits/generics 6, Actix 5, clap CLI 4, testing 3 |
| C# | 10 | 30 | +20 | Minimal API 8, WebApplicationFactory 6, **async/await bugs 6**, LINQ perf 5, xUnit workflows 5 |
| C/C++ | 10 | 25 | +15 | Memory safety 8, data structures 6, pointers/UB 5, C++ STL 3, make/build 3 |
| SQL | 10 | 25 | +15 | Schema design 6, query bugs 6, **migrations 5**, **window functions 4**, indexing/perf 4 |
| Shell | 5 | 15 | +10 | Quoting/robustness 5, pipelines (awk/sed) 4, error handling 3, portability 3 |
| Docker | 5 | 15 | +10 | Compose 7, **Dockerfile multi-stage/lint 8** (static structural checks) |
| Terraform/IaC | 5 | 15 | +10 | variables/outputs/locals 7, **provider-free modules 4**, fmt/validate/policy 4 |
| **Kotlin** (new) | 0 | 20 | +20 | Spring Boot 8, plain + JUnit 8, coroutines 4 - extends the existing jvm image |
| **PHP** (new) | 0 | 20 | +20 | Laravel 10, plain PHP 6, PHPUnit 4 - small new image, composer warms offline |
| **Ruby** (new) | 0 | 20 | +20 | Rails 10, plain Ruby 6, minitest 4 - small new image |
| **Deno/Bun** (new) | 0 | 10 | +10 | Deno std 5, Bun 5 - one small "alt-TS-runtime" image |
| **Total** | **120** | **500** | **+380** | ~40 distinct frameworks/sub-stacks (vs ~20 today) |

Swift / Flutter / Android stay deferred (macOS hosts or SDK-heavy images;
hostile to `--network none`). Angular stays dropped.

### By category (workflow)

| Category | Now | Target | Notes |
|---|---:|---:|---|
| feature | 30 | 95 | Increasingly multi-file ("wire it through all three layers") |
| bug_fix | 23 | 95 | Adds concurrency/race, unicode/timezone, off-by-one families |
| refactoring | 15 | 70 | Cross-module consolidation, callback→async, class→hooks migrations |
| testing | 13 | 65 | All mutation-checked, per the existing 13 |
| security | 15 | 55 | Adds SSRF-shape, deserialization, secrets-in-logs, JWT validation |
| performance | 9 | 45 | Time-budget oracles per the calibrated pattern (n large enough that O(n²) busts compiled languages too) |
| devops | 8 | 45 | Dockerfile, more CI variants, Makefile, IaC modules |
| **data engineering** (new) | 0 | 15 | pandas/stdlib ETL, CSV/JSON wrangling, SQL pipelines - highly verifiable |
| **dependency upgrade** (new) | 0 | 10 | Migrate across a breaking library change; both versions warmed in-image |
| **documentation** (new) | 0 | 5 | Doctest-verified docstrings only - the one objectively checkable doc task |
| **Total** | 113 (+7 untagged) | 500 | |

### By difficulty

| Level | Now | Target | Shape |
|---|---:|---:|---|
| L1 (1-3 files) | ~60 | ~200 | Breadth coverage; the fast Lite-suite pool |
| L2 (4-15 files) | ~53 | ~250 | **The center of gravity moves here.** Cross-file reasoning is the strongest agent-vs-baseline discriminator (the one-file baseline driver physically can't do it) |
| L3 (20-100 files, shared starter repos) | 0 | ~50 | 8 starter repos × ~6 tasks; SWE-bench-Pro pattern |

### New interaction shapes (not just new topics)

380 new cases that are all "same shape, different noun" would be a weak
moat. These workflow shapes are new to the corpus and each needs a small
amount of one-time schema/harness support (noted):

1. **Failing-test-driven fixes** - a *visible* failing test ships in
   `setup_files`; the model must make it pass without breaking the hidden
   suite. No schema change.
2. **Multi-prompt sessions** (1 exists today → ~30) - build, then extend,
   then refactor across 2-4 prompts; exercises statefulness that
   single-shot cases can't. No schema change.
3. **Dependency upgrades** - image ships both library versions (side-by-side
   venvs / node_modules dirs); hidden test imports through the new one.
   Image work only.
4. **Contract-first** - an OpenAPI/JSON-schema file in the workspace; hidden
   tests validate responses against the contract. No schema change.
5. **Concurrency correctness** - stress-loop hidden tests (Go `-race`,
   tokio, asyncio, C# TPL). Needs care with sandbox CPU limits (2 cores);
   verified per-case via `verify-corpus` broken variants.
6. **Doctest documentation** - `python -m doctest` as the oracle. No schema
   change.
7. **Repo-scale tasks (L3)** - needs the two schema fields already planned:
   `setup_repo` (shared starter-repo dir under `repos/`) and `git_init`.
8. **Migration families** - callback→promise/async, React class→hooks,
   Python 2-isms, `var`→`const`. No schema change.

## Progress (updated 2026-07-09)

**Wave A shipped in full (+70) plus a devops rebalance batch (+9): corpus
120 → 199**, every new case carrying a `reference_solution` and a
behaviorally-failing `broken_solution`, all green through `verify-corpus`
against the real sandboxes before commit. The legacy 7 untagged cases were
also tagged, closing the `untagged` bucket. Batches landed:

| Batch | +n | Track | Verify notes |
|---|--:|---|---|
| A1 | 12 | Django (in-process test.Client, portless) | 32 variants |
| A2 | 12 | TypeScript-strict (@ts-expect-error + behavior) | 33 |
| A3 | 8 | Node core (streams/events/fs/url) | 25 |
| A4 | 8 | Data engineering (pandas/csv/sqlite; +image pandas/alembic) | 20 |
| A5 | 16 | Concurrency: Go race/deadlock/vet, Rust tokio/scope, asyncio, C# TPL | 46 |
| A6 | 14 | SQL windows+migrations (9) + shell (5) | 36 |
| 7 | 9 | DevOps: Makefile (real `make`), Dockerfile+CI (structural) | 25 |

Reference-solution coverage rose 16 → 95 (48% of the corpus, 100% of new
cases). `verify-corpus` earned its keep in-flight: it caught ~8 authoring
defects across the batches — a `@types/node` import the image lacks, a
missing `T: Send + Sync` bound, a migration fixture that accidentally
reproduced original ids, a `.PHONY` test where `make clean` masked the
distinction, and several broken variants that shape checks were shadowing —
every one fixed and re-verified, none shipped.

Difficulty now tilts L2 (107 vs 85 L1); devops rebalanced 8 → 10; data
engineering, TypeScript, Django, and cross-language concurrency all went
from zero to real coverage.

## The waves (380 cases, ordered by machinery dependencies)

| Wave | Cases | Contents | Prereqs |
|---|---:|---|---|
| **A** ✅ | +70 done | Quick wins inside existing images, L1-2: Django (12), TypeScript-strict (12), Node core (8), concurrency families across Go/Rust/Python/C# (16), SQL windows+migrations (9), shell (5), data-eng (8) | pandas+alembic added to python image |
| **B** | +80 | The L2 shift: 4-15-file cases across all 7 existing tracks - cross-layer features, cross-module refactors, failing-test-driven fixes, multi-prompt sessions | None (schema already supports) |
| **C** | +60 | New tracks #1: Kotlin (20), PHP/Laravel (20), Ruby/Rails (20) | 2 new images + jvm image extension; each track lands with ≥4 categories covered |
| **D** | +50 | Level 3: 8 starter repos (FastAPI+SQLAlchemy+alembic, Express+TS, Spring multi-module, Gin, Axum, ASP.NET, Rails, Laravel) × ~6 tasks; suite tags (Lite/Standard/Extended) + `--suite` filter | `setup_repo` + `git_init` schema fields |
| **E** | +70 | New workflow shapes at breadth: dependency upgrades (10), contract-first (12), doctest docs (5), more testing-with-mutation (15), security families (SSRF/deserialization/JWT, 14), devops (Dockerfile/Makefile, 14) | Multi-version libs in images |
| **F** | +50 | Frontier + rebalance: Next.js (8), Svelte (6), Deno/Bun track (10), category/difficulty rebalancing to hit the target tables (26) | next/svelte in node image; 1 small alt-runtime image |

Waves A/B/C can be authored in parallel by track once their image work
lands; D blocks on the two schema fields; E blocks on image versioning
work. At the measured authoring rate (~45-75 min per L1/L2 case *including*
reference + broken solutions and a `verify-corpus` run; L3 repos ~2-3 days
each including tasks), the whole program is roughly **12-16 engineer-weeks**
- parallelizable across tracks since cases are independent JSON files.

## Quality gates (non-negotiable, or 500 cases make the corpus worse)

Volume without discipline would *weaken* the product - the 2026-07-08 audit
found 5 classes of oracle bug in just 120 hand-verified cases. Every new
case must land with:

1. `reference_solution` that passes and ≥1 `broken_solution` that fails,
   `verify-corpus` green in CI (already enforced on every push).
2. The implicit **unmodified-workspace-must-fail** check for
   fix/refactor/perf/security categories (already automatic).
3. **Mutation checks** for every testing-category case (the established
   pattern: model tests must kill deliberately broken implementations).
4. **Calibrated perf budgets**: input sizes chosen so the naive
   implementation *measurably busts* the budget in the target language
   (the C# lesson: 30k elements was nothing to compiled .NET; 200k
   discriminates). Verified by a broken variant, not by eyeball.
5. **Unique ports** per case within a shared image (the Spring lesson), and
   no reliance on network at check time.
6. **Family diversity cap**: a task template may be stamped across at most
   ~4 languages before it must mutate meaningfully (different bug, different
   API shape) - keeps breadth from becoming machine-derivable repetition,
   which matters for both contamination and discriminating power.

## Moat hardening (do alongside Wave F)

1. **Private held-back slice.** Publish 450; keep ~50 cases (a stratified
   sample across tracks/categories/difficulty) in a private
   `optarena-cases-hidden` repo, rotated quarterly. Public corpus = the
   product anyone can run; hidden slice = the integrity check nobody can
   train on. This is the single strongest replication barrier and directly
   addresses the "passes-public-tests-but-memorized" criticism aimed at
   SWE-bench-class suites.
2. **Variants engine.** Programmatic per-run perturbation of literals
   (function names, expected strings, ports, seed data) - the mutation
   scripts already parametrize these internally, so the mechanism has a
   template to follow. Moves from "defer" to scheduled once the corpus is
   public at 500 scale.
3. **Versioned corpus releases.** Tag corpus snapshots (`corpus-2026.07`)
   so published results cite an immutable version; results without a corpus
   version are non-comparable by definition.

## Platform gap status (refreshed 2026-07-09)

| Gap | Status |
|---|---|
| Per-driver token/cost/turn telemetry | **Done** for aider + claude-code (0.1); opencode/goose/qwen-code/codex remain (same `parse_metrics` hook) |
| Trials stability in compare/regression | **Done** (0.1: `PASS 2/3` markers, `flaky_cases`) |
| Corpus self-verification CI | **Done** (0.1: `verify-corpus`, 91 variants green) |
| Latency percentiles | **Done** (0.1: p95 in aggregate + formatters) |
| Publish sandbox images | **Done** (0.1: GHCR + auto-pull) |
| Contamination hygiene | Open → scheduled above (private slice, variants, versioned releases) |
| Headless CI (Xvfb) for UI drivers | Open |
| Dashboard matrix grid | Open |
| Case recorder | Open - **rises in value at 500-case scale** (authoring throughput); build during Wave B |
| Driver validation pass (6 experimental) | Open |
| LLM-judge scoring | Deliberately out (unchanged) |

## Sequencing summary

1. Image work for Wave A (pandas/alembic) - hours, unblocks first 70 cases.
2. Waves A + B in parallel per track (the L2 shift is the highest-value
   authoring; build the **case recorder** during B to accelerate C-F).
3. Wave C images (kotlin/php/ruby) while B authoring continues.
4. `setup_repo`/`git_init` schema + Wave D repos.
5. Waves E + F + moat hardening (private slice carved from the final
   corpus, not authored separately - pull 50 representative cases private
   at the 500 mark).

Everything stays inside the essence tests from `EXPANSION_ANALYSIS.md` §1:
local-first, comparison-first, one JSON file per case (repos referenced,
not inlined), no leaderboard-farm drift.
