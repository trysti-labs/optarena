# OptArena Status

_As of 2026-07-10, HEAD `c3eff7f` on `main` (pushed to github.com/trysti-labs/optarena)._

## Where things stand

The 0.1 platform cut is done and stable (see ARCH.md): verify-corpus CI gate,
driver telemetry, trials stability, p95 aggregation, GHCR-published sandbox
images, Apache-2.0 licensing. Since then the work has been entirely corpus
expansion per [CORPUS_EXPANSION_PLAN.md](CORPUS_EXPANSION_PLAN.md): **120 →
304 cases**, all new cases shipped with a `reference_solution` and
behaviorally-failing `broken_solutions`, every variant proven through
`verify-corpus` against the real Docker sandboxes before commit.

## Corpus census (304 cases)

| | |
|---|---|
| Total cases | **304** (target 500, 61%) |
| With `reference_solution` | 200 (66%; 100% of the 184 added this expansion) |
| Mutation-checked testing cases | every `testing` case added since Wave A |
| Sandbox images | 9 (base, python, node, jvm, go, rust, dotnet, php, ruby) — all in the GHCR publish matrix |

**By language:** **python 61**, javascript 34, go 25, rust 24, java 24,
csharp 24, sql 19, typescript 18, ruby 12, php 12, kotlin 12,
shell 10, c 7, yaml 7, hcl 5, cpp 3 — 16 tracked languages plus 7
language-neutral devops cases.

**By task type:** bug_fix 93, feature 72, refactoring 35, security 32,
testing 30, performance 22, devops 10, data_engineering 10.

## Waves shipped (chronology)

Wave A and the devops rebalance (120 → 199) are documented in the plan's
[Progress section](CORPUS_EXPANSION_PLAN.md#progress-updated-2026-07-09).
Since then:

| Wave | Batches | Corpus | What |
|---|---|---|---|
| **B** — multi-file framework sweep | 7 | 199 → 232 | Cross-layer (L2) cases per web stack: FastAPI, Express, Spring, Gin, Flask, Axum, ASP.NET — all portless in-process testing (TestClient, `app.listen(0)`, MockMvc, `httptest`, `WebApplicationFactory`, tower `oneshot`) |
| **C** — new language tracks | 3 | 232 → 250 | Kotlin (jvm image + Kotlin warmup), PHP (new php:8.3 image, PHP-native mutation harness), Ruby (new ruby:3.3 image, Minitest, Psych-4-accurate `unsafe_load` security case) |
| **D** — depth in core tracks | 9 | 250 → 304 | Batches 1-3: Go stdlib (nil map, `errors.Is`, generic LRU, command injection), TypeScript (forEach-async, typed EventBus, prototype pollution), plain Java/JUnit (Integer cache `==`, ConcurrentModificationException, path traversal). Batches 4-6 deepen the Wave C tracks to 12 each: Ruby, PHP, Kotlin. Batches 7-8 cover the plain-stdlib seams of the previously framework-only Rust (Vec::contains→HashSet perf, u32 overflow, UTF-8 byte-slice panic) and C# (string += →StringBuilder perf, foreach-mutation, `for`-loop closure capture) — each 18 → 24. Batch 9 does the same for **Python** (`list.count()`-in-loop perf, mutable-default-arg, late-binding closure, `pickle.loads` RCE, event sessionization), whose 55 cases were framework-heavy → 61 |

Each C/D batch follows the same shape per track: L1 idiom bug_fixes,
an L2 cross-file feature, a behavior-preserving refactor with a
"behavior-drifted" broken variant, a mutation-checked testing case, and a
security case with a realistic *incomplete-fix* broken variant (e.g.
top-level-only prototype-pollution guard, naive `".."` string check defeated
by an absolute path, an XXE "fix" that toggles unrelated hardening flags but
still resolves external entities).

The batch-4-8 language-depth cases were weighted toward the categories
furthest below their target allocation: each track's first calibrated
`performance` case (an O(n²)→O(n) with the budget proven by a slow variant
that returns *correct* output but busts the time budget — for the compiled
Rust/C# tracks `n` is sized so the naive path busts even on fast native
hardware) and one or two `data_engineering` cases (CSV/JSON aggregation wired
through an existing consumer, i.e. L2 cross-file).

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

## Remaining to 500 (196 cases)

Per the plan's [wave sequencing](CORPUS_EXPANSION_PLAN.md#the-waves-380-cases-ordered-by-machinery-dependencies):

1. **More Wave D depth** — Kotlin/PHP/Ruby at 12 each, Rust/C# at 24, Python
   stdlib seams now covered (→ their targets of ~20/40/30/90); still open:
   Node/JS stdlib depth beyond current frameworks, SQL/Go depth.
2. **Wave E — workflow shapes** — multi-step tasks, `setup_repo`/`git_init`
   repo-scale cases, failing-CI-fix shapes ([new interaction shapes](CORPUS_EXPANSION_PLAN.md#new-interaction-shapes-not-just-new-topics)).
3. **Wave F + moat hardening** — private held-out slice, paraphrase variants,
   anti-memorization checks ([moat hardening](CORPUS_EXPANSION_PLAN.md#moat-hardening-do-alongside-wave-f)).

Rebalance note: data_engineering (now 10, target 15) and performance (now 22,
target 45) remain below their target allocation and should keep being woven
into upcoming depth batches, as batches 4-9 did (each added one perf + one or
two data-eng cases).
