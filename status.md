# OptArena Status

_As of 2026-07-10, HEAD `c120933` on `main` (pushed to github.com/trysti-labs/optarena)._

## Where things stand

The 0.1 platform cut is done and stable (see ARCH.md): verify-corpus CI gate,
driver telemetry, trials stability, p95 aggregation, GHCR-published sandbox
images, Apache-2.0 licensing. Since then the work has been entirely corpus
expansion per [CORPUS_EXPANSION_PLAN.md](CORPUS_EXPANSION_PLAN.md): **120 →
328 cases**, all new cases shipped with a `reference_solution` and
behaviorally-failing `broken_solutions`, every variant proven through
`verify-corpus` against the real Docker sandboxes before commit.

## Corpus census (328 cases)

| | |
|---|---|
| Total cases | **328** (target 500, 66%) |
| With `reference_solution` | 224 (68%; 100% of the 208 added this expansion) |
| Mutation-checked testing cases | every `testing` case added since Wave A |
| Sandbox images | 9 (base, python, node, jvm, go, rust, dotnet, php, ruby) — all in the GHCR publish matrix |

**By language:** python 61, javascript 40, go 25, rust 24, java 24,
csharp 24, sql 19, typescript 18, ruby 12, php 12, kotlin 12, **yaml 11**,
shell 10, c 7, hcl 5, cpp 3, **dockerfile 1**, **makefile 1** — plus 7
language-neutral devops cases.

**By task type:** bug_fix 95, feature 72, refactoring 41, security 38,
testing 31, performance 23, devops 16, data_engineering 12.

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
has now caught **~26 authoring defects pre-commit** across the expansion —
broken variants that weren't actually broken, perf budgets the slow code
beat, a csproj glob compiling tests into the app, a `filter_var` "drift"
that didn't drift, the fnmatch `**/x.go` root-level-file miss (Go batch), and
a Python sessionization "no-sort" broken that passed because the test's
unsorted input was accidentally already per-user ascending (fixed by
interleaving one user's events out of order).
None shipped. The batch-4-8 perf cases were each pre-calibrated in-container
(measuring the naive-vs-fast gap) so the chosen budget busts the slow path
with margin even on faster native hardware — for the compiled Rust/C# cases
`n` was pushed up (Rust intersection n=30k, C# string-concat n=120k) since
native compiled code clears a small O(n²) far faster than an interpreter.
The Kotlin XXE case was likewise probed against the real JDK parser first,
which rejects the Apache feature-flag knobs outright — hence the cosmetic-
hardening incomplete-fix. New-image batches additionally smoke-test the
offline toolchain in-container *before* any case is authored.

## Remaining to 500 (172 cases)

Per the plan's [wave sequencing](CORPUS_EXPANSION_PLAN.md#the-waves-380-cases-ordered-by-machinery-dependencies):

1. **More Wave D depth** — every big track now has a stdlib-seam layer
   (Python, Node/JS, Rust, C#, Go, Java, TS all covered); Kotlin/PHP/Ruby at
   12 each still climbing to ~20. Still open: SQL/Go depth, and pushing the
   new-language tracks toward their targets.
2. **Wave E — workflow shapes** — multi-step tasks, `setup_repo`/`git_init`
   repo-scale cases, failing-CI-fix shapes ([new interaction shapes](CORPUS_EXPANSION_PLAN.md#new-interaction-shapes-not-just-new-topics)).
3. **Wave F + moat hardening** — private held-out slice, paraphrase variants,
   anti-memorization checks ([moat hardening](CORPUS_EXPANSION_PLAN.md#moat-hardening-do-alongside-wave-f)).

Rebalance note: **bug_fix is at target (95/95) — stop adding it.** After the
devops (batch 11 → 16/45), refactoring (batch 12 → 41/70) and security
(batch 13 → 38/55) pushes, the categories still furthest behind are
**testing (31/65), performance (23/45), devops (16/45), feature (72/95), and
refactoring (41/70)**; data_engineering (12/15) and security (38/55) are
closing in. Next levers: more mutation-checked testing, further devops/CI
breadth, calibrated performance cases, and the cross-file feature shift of
Wave B/E.
