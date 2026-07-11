# OptArena Status

_As of 2026-07-11, on `main` (github.com/trysti-labs/optarena), through
batch 17._

## Where things stand

The 0.1 platform cut is done and stable (see ARCH.md): verify-corpus CI gate,
driver telemetry, trials stability, p95 aggregation, GHCR-published sandbox
images, Apache-2.0 licensing. Since then the work has been entirely corpus
expansion per [CORPUS_EXPANSION_PLAN.md](CORPUS_EXPANSION_PLAN.md): **120 →
348 cases**, all new cases shipped with a `reference_solution` and
behaviorally-failing `broken_solutions`, every variant proven through
`verify-corpus` before commit. **The L3 (repo-scale) track is live**: the
`setup_repo`/`git_init` schema fields, the first shared starter repo
(`repos/fastapi-tasktracker`), and 9 L3 cases on it spanning 6 task types.

## Corpus census (348 cases)

| | |
|---|---|
| Total cases | **348** (target 500, 70%) |
| With `reference_solution` | 244 (70%; 100% of the 228 added this expansion) |
| Mutation-checked testing cases | every `testing` case added since Wave A |
| L3 (repo-scale, `setup_repo`) cases | 9 (fastapi-tasktracker) |
| Multi-prompt session cases | 2 |
| Sandbox images | 9 (base, python, node, jvm, go, rust, dotnet, php, ruby) - all in the GHCR publish matrix |

**By language** (recomputed from the case JSONs' `language` tags - the
previous census under-counted several tracks): python 78, javascript 45,
go 27, csharp 25, rust 24, java 24, sql 20, typescript 18, php 13, ruby 13,
yaml 13, kotlin 12, shell 10, c 7, hcl 5, cpp 3, dockerfile 3, makefile 1 -
plus 7 language-neutral devops cases.

**By task type:** bug_fix 95, feature 75, refactoring 43, security 39,
testing 38, performance 25, devops 20, data_engineering 13.

## Waves shipped (chronology)

Wave A and the devops rebalance (120 → 199) are documented in the plan's
[Progress section](CORPUS_EXPANSION_PLAN.md#progress-updated-2026-07-09).
Since then:

| Wave | Batches | Corpus | What |
|---|---|---|---|
| **B** — multi-file framework sweep | 7 | 199 → 232 | Cross-layer (L2) cases per web stack: FastAPI, Express, Spring, Gin, Flask, Axum, ASP.NET — all portless in-process testing (TestClient, `app.listen(0)`, MockMvc, `httptest`, `WebApplicationFactory`, tower `oneshot`) |
| **C** — new language tracks | 3 | 232 → 250 | Kotlin (jvm image + Kotlin warmup), PHP (new php:8.3 image, PHP-native mutation harness), Ruby (new ruby:3.3 image, Minitest, Psych-4-accurate `unsafe_load` security case) |
| **D** — depth in core tracks | 10 | 250 → 310 | Batches 1-3: Go stdlib (nil map, `errors.Is`, generic LRU, command injection), TypeScript (forEach-async, typed EventBus, prototype pollution), plain Java/JUnit (Integer cache `==`, ConcurrentModificationException, path traversal). Batches 4-6 deepen the Wave C tracks to 12 each: Ruby, PHP, Kotlin. Batches 7-8 cover the plain-stdlib seams of the previously framework-only Rust (Vec::contains→HashSet perf, u32 overflow, UTF-8 byte-slice panic) and C# (string += →StringBuilder perf, foreach-mutation, `for`-loop closure capture) — each 18 → 24. Batch 9: **Python** stdlib (`list.count()`-in-loop perf, mutable-default-arg, late-binding closure, `pickle.loads` RCE, sessionization), 55 → 61. Batch 10: **Node/JS** stdlib (spread-accumulation perf, `map(parseInt)` radix trap, `sort()` lexicographic, quoted-CSV parser, generic groupBy), 34 → 40 |
| **Rebalance** — devops breadth | 1 | 310 → 316 | Batch 11: Dockerfile hardening, Kubernetes Deployment (probes/limits/non-root), GitHub Actions least-privilege permissions, GitHub Actions caching+concurrency, Compose `service_healthy`, Makefile `.PHONY` — all structural / real-`make` on the base image; devops 10 → 16 |
| **Rebalance** — refactoring breadth | 1 | 316 → 322 | Batch 12: six varied refactor shapes across six langs, each with a behavior-*drift* broken: Python if/elif→dict-dispatch and class→`@dataclass`, JS `.then`-chain→async/await, Go switch→table-driven, SQL correlated-subquery→LEFT JOIN, C# loop→LINQ; refactoring 35 → 41 |
| **Rebalance** — security families | 1 | 322 → 328 | Batch 13: six new vuln families, each with a realistic *incomplete-fix* broken — JWT signature-not-verified/alg:none, SSRF host allowlist (ipaddress), Python mass-assignment, secrets-in-logs (nested redaction), open redirect (`//` and `/\`), ReDoS validator (nested-quantifier backtracking); security 32 → 38 |
| **Rebalance** — testing breadth | 1 | 328 → 334 | Batch 14: six mutation-checked cases on distinct functions (Luhn, Roman numerals, password policy, time-ago, semver compare, median) across go/python/php/ruby/node; testing 31 → 37. verify-corpus caught a stale-`__pycache__` mutation-survival bug and the php-image-has-no-python3 issue pre-commit |
| **L3 kickoff** - repo-scale machinery + first repo | 1 | 334 → 339 | Batch 15: the `setup_repo` + `git_init` schema fields (`prepare_workspace` in cases.py, wired through all four drivers and verify.py), the first shared starter repo `repos/fastapi-tasktracker` (35 files: FastAPI + SQLAlchemy 2.0 + alembic + pytest, layered models/schemas/crud/services/routers, portless TestClient, file-based SQLite), and 5 L3 cases on it: Comment sub-resource feature (cross-layer, 7 files), due_date end-to-end feature (model + schemas + crud + a real `alembic upgrade head` proven by the hidden test), shared-pagination refactor (3 crud modules -> 1 helper), mutation-checked tests for the repo's `progress_summary` service, and a raw-SQL injection fix with a strips-`;`-and-`--`-only incomplete-fix broken. git added to the python image for `git_init` |
| **L3 depth** - lagging categories on the same repo | 1 | 339 → 343 | Batch 16, all on fastapi-tasktracker: project-archiving **multi-prompt session** (2 prompts: archive endpoint + default-list filter, then `include_archived` param + 409 guard on task creation - the corpus's 2nd multi-prompt case), lookup-or-404 helper-extraction refactor across 3 routers (broken: homogenized 404 details drift), a **devops** containerization case (production Dockerfile + .dockerignore with a structural oracle: slim base, `--no-cache-dir`, non-root USER *after* the install layer, uvicorn CMD, no `--reload`), and a **data_engineering** CSV export with RFC-4180 quoting (broken: naive comma-join corrupted by hostile titles). verify-corpus caught a missing `crud/__init__` re-export in the archiving reference pre-commit |
| **Perf + devops rebalance** | 1 | 343 → 348 | Batch 17: two calibrated **performance** cases (list.insert(0)-prepend -> linear+reverse at n=250k, naive ~6s native vs 2s budget; nested pair-sum -> complement dict at n=20k, naive ~6.5s vs 1.5s budget - each with a wrong-output fast broken AND an explicit still-quadratic broken proven to bust the budget) and three **devops** cases: multi-stage Dockerfile refactor (build-essential out of the runtime stage), GitHub Actions pin-to-full-SHA supply-chain hardening (brokens: third-party actions left on tags, short SHAs), and a pg_dump CronJob with scheduling hygiene (Forbid concurrency, startingDeadlineSeconds, history limits, backoffLimit, resources, secretKeyRef creds; brokens: no-hygiene, hardcoded password) |

Each C/D batch follows the same shape per track: L1 idiom bug_fixes,
an L2 cross-file feature, a behavior-preserving refactor with a
"behavior-drifted" broken variant, a mutation-checked testing case, and a
security case with a realistic *incomplete-fix* broken variant (e.g.
top-level-only prototype-pollution guard, naive `".."` string check defeated
by an absolute path, an XXE "fix" that toggles unrelated hardening flags but
still resolves external entities).

The batch-4-10 language-depth cases were weighted toward the categories
furthest below their target allocation: each track's first calibrated
`performance` case (an O(n²)→O(n) with the budget proven by a slow variant
that returns *correct* output but busts the time budget — for the compiled
Rust/C# tracks `n` is sized so the naive path busts even on fast native
hardware) and one or two `data_engineering` cases (CSV/JSON aggregation wired
through an existing consumer, i.e. L2 cross-file). Batch 11 then pivoted to
**devops** (the category then furthest below target, 10/45) with six
structural / real-`make` orchestration cases; each fix-style case ships an
explicit `unmodified` broken so doing nothing fails.

## Quality gates in practice

`verify-corpus` (the plan's [non-negotiable gate](CORPUS_EXPANSION_PLAN.md#quality-gates-non-negotiable-or-500-cases-make-the-corpus-worse))
has now caught **~29 authoring defects pre-commit** across the expansion —
broken variants that weren't actually broken, perf budgets the slow code
beat, a csproj glob compiling tests into the app, a `filter_var` "drift"
that didn't drift, the fnmatch `**/x.go` root-level-file miss (Go batch), a
Python sessionization "no-sort" broken that passed because the test's unsorted
input was accidentally already per-user ascending, a mutation harness reusing
a stale `__pycache__` so mutants "survived" (fixed with
`PYTHONDONTWRITEBYTECODE=1`), and a PHP testing case whose python3 harness
didn't run because the php sandbox has no python3 (rewritten PHP-native).
None shipped. The batch-4-8 perf cases were each pre-calibrated in-container
(measuring the naive-vs-fast gap) so the chosen budget busts the slow path
with margin even on faster native hardware — for the compiled Rust/C# cases
`n` was pushed up (Rust intersection n=30k, C# string-concat n=120k) since
native compiled code clears a small O(n²) far faster than an interpreter.
The Kotlin XXE case was likewise probed against the real JDK parser first,
which rejects the Apache feature-flag knobs outright — hence the cosmetic-
hardening incomplete-fix. New-image batches additionally smoke-test the
offline toolchain in-container *before* any case is authored.

The L3 batch hardened the gate itself in two ways. (1) The implicit
unmodified-must-fail variant now also fires for cases whose starting state
is entirely a `setup_repo` (no `setup_files` overlay) - previously it was
silently skipped for them. (2) `snapshot()` now signatures files by content
hash instead of `size:mtime_ns`: verify-corpus writes setup files and
solution files back-to-back, so a same-size rewrite landing within one
filesystem-timestamp tick was invisible on coarse-mtime filesystems
(Windows hosts), making changed-file detection - and therefore the oracle
verdict - flaky. Both fixes are unit-tested (75 tests, 5x consecutive green).
L3 mutation/check harnesses are also written portably (`sys.executable`,
list-args subprocess, `-B`) instead of `python3` + POSIX env-prefix, so they
run identically in the Linux sandbox and on a bare Windows host.

Batch 17 closed a follow-on gap the hash change itself created: a broken
variant byte-identical to its setup file (the standard slow-but-correct perf
variant) no longer registered as "changed", so verify failed it at the
expected-file check without ever running the timer - the budget was not
actually being proven. `verify._run_variant` now feeds the variant's own
file list to the oracle instead of a snapshot diff (which is also what the
old mtime semantics effectively did), and both batch-17 perf budgets are
proven by an explicit `still-quadratic` broken failing on time, not on shape.

## Remaining to 500 (157 cases)

**L3 (repo-scale) is live.** The `setup_repo`/`git_init` schema fields exist
(see `prepare_workspace` in cases.py; documented in the module docstring),
and the first starter repo `repos/fastapi-tasktracker` carries 9 verified
cases across 6 task types (feature x3 incl. a multi-prompt session,
refactoring x2, testing, security, devops, data_engineering) - at the plan's
~6-8-tasks-per-repo target. Next L3 steps: an L3 performance case (e.g. an
N+1 query - needs in-container calibration first, per the perf-case
protocol), then the next starter repos from the plan's Wave D list
(Express+TS, Spring multi-module, Gin, Axum, ASP.NET, Rails, Laravel) - the
next repo should target a non-python image to prove setup_repo across
toolchains. Remaining work after L3, per the plan's
[wave sequencing](CORPUS_EXPANSION_PLAN.md#the-waves-380-cases-ordered-by-machinery-dependencies):

1. **More Wave D depth** — every big track now has a stdlib-seam layer
   (Python, Node/JS, Rust, C#, Go, Java, TS all covered); Kotlin/PHP/Ruby at
   12 each still climbing to ~20. Still open: SQL/Go depth, and pushing the
   new-language tracks toward their targets.
2. **Wave E — workflow shapes** — multi-step tasks, `setup_repo`/`git_init`
   repo-scale cases, failing-CI-fix shapes ([new interaction shapes](CORPUS_EXPANSION_PLAN.md#new-interaction-shapes-not-just-new-topics)).
3. **Wave F + moat hardening** — private held-out slice, paraphrase variants,
   anti-memorization checks ([moat hardening](CORPUS_EXPANSION_PLAN.md#moat-hardening-do-alongside-wave-f)).

Rebalance note: **bug_fix is at target (95/95) - stop adding it** (batches
15-17 deliberately shipped zero). After batch 17 the categories still
furthest behind are **testing (38/65), refactoring (43/70), devops (20/45),
performance (25/45), and feature (75/95)**; data_engineering (13/15) and
security (39/55) are closing in. Batch 17 proved host-calibrated perf
budgets work when margins are wide (naive 3-4x over budget on fast native
hardware, fast path 50-500x under it) with CI's in-container verify-corpus
as the final proof. Next levers: more calibrated perf shapes (other
languages need their toolchains or Docker locally), testing/refactoring
breadth, and the next starter repos.
