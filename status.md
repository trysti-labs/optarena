# OptArena Status

_As of 2026-07-11, on `main` (github.com/trysti-labs/optarena); L3 batch 15
in the working tree._

## Where things stand

The 0.1 platform cut is done and stable (see ARCH.md): verify-corpus CI gate,
driver telemetry, trials stability, p95 aggregation, GHCR-published sandbox
images, Apache-2.0 licensing. Since then the work has been entirely corpus
expansion per [CORPUS_EXPANSION_PLAN.md](CORPUS_EXPANSION_PLAN.md): **120 →
339 cases**, all new cases shipped with a `reference_solution` and
behaviorally-failing `broken_solutions`, every variant proven through
`verify-corpus` before commit. **The L3 (repo-scale) machinery now exists**:
the `setup_repo`/`git_init` schema fields, the first shared starter repo
(`repos/fastapi-tasktracker`), and the first 5 L3 cases on it.

## Corpus census (339 cases)

| | |
|---|---|
| Total cases | **339** (target 500, 68%) |
| With `reference_solution` | 235 (69%; 100% of the 219 added this expansion) |
| Mutation-checked testing cases | every `testing` case added since Wave A |
| L3 (repo-scale, `setup_repo`) cases | 5 (fastapi-tasktracker) |
| Sandbox images | 9 (base, python, node, jvm, go, rust, dotnet, php, ruby) - all in the GHCR publish matrix |

**By language** (recomputed from the case JSONs' `language` tags - the
previous census under-counted several tracks): python 73, javascript 45,
go 27, csharp 25, rust 24, java 24, sql 20, typescript 18, php 13, ruby 13,
kotlin 12, yaml 11, shell 10, c 7, hcl 5, cpp 3, dockerfile 1, makefile 1 -
plus 7 language-neutral devops cases.

**By task type:** bug_fix 95, feature 74, refactoring 42, security 39,
testing 38, performance 23, devops 16, data_engineering 12.

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

## Remaining to 500 (161 cases)

**L3 (repo-scale) is now unblocked and started.** The `setup_repo`/`git_init`
schema fields exist (see `prepare_workspace` in cases.py; documented in the
module docstring), the first starter repo `repos/fastapi-tasktracker` is live
with 5 verified cases, and the pattern is proven end to end. Next L3 steps:
grow fastapi-tasktracker toward ~6-8 tasks (a performance case - e.g. an N+1
query - needs in-container calibration first, per the perf-case protocol),
then the next starter repos from the plan's Wave D list (Express+TS, Spring
multi-module, Gin, Axum, ASP.NET, Rails, Laravel). Remaining
work after L3, per the plan's [wave sequencing](CORPUS_EXPANSION_PLAN.md#the-waves-380-cases-ordered-by-machinery-dependencies):

1. **More Wave D depth** — every big track now has a stdlib-seam layer
   (Python, Node/JS, Rust, C#, Go, Java, TS all covered); Kotlin/PHP/Ruby at
   12 each still climbing to ~20. Still open: SQL/Go depth, and pushing the
   new-language tracks toward their targets.
2. **Wave E — workflow shapes** — multi-step tasks, `setup_repo`/`git_init`
   repo-scale cases, failing-CI-fix shapes ([new interaction shapes](CORPUS_EXPANSION_PLAN.md#new-interaction-shapes-not-just-new-topics)).
3. **Wave F + moat hardening** — private held-out slice, paraphrase variants,
   anti-memorization checks ([moat hardening](CORPUS_EXPANSION_PLAN.md#moat-hardening-do-alongside-wave-f)).

Rebalance note: **bug_fix is at target (95/95) - stop adding it** (the L3
batch deliberately shipped zero). After batch 15 the categories still
furthest behind are **performance (23/45), devops (16/45), feature (74/95),
testing (38/65), and refactoring (42/70)**; data_engineering (12/15) and
security (39/55) are closing in. Next levers: calibrated performance cases
(including L3 ones once probed in-container), further devops/CI breadth,
and more L3 tasks per repo - the L3 machinery is no longer a blocker.
