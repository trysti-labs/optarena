# OptArena Status

_As of 2026-07-10, HEAD `00c4b73` on `main` (pushed to github.com/trysti-labs/optarena)._

## Where things stand

The 0.1 platform cut is done and stable (see ARCH.md): verify-corpus CI gate,
driver telemetry, trials stability, p95 aggregation, GHCR-published sandbox
images, Apache-2.0 licensing. Since then the work has been entirely corpus
expansion per [CORPUS_EXPANSION_PLAN.md](CORPUS_EXPANSION_PLAN.md): **120 →
268 cases**, all new cases shipped with a `reference_solution` and
behaviorally-failing `broken_solutions`, every variant proven through
`verify-corpus` against the real Docker sandboxes before commit.

## Corpus census (268 cases)

| | |
|---|---|
| Total cases | **268** (target 500, 54%) |
| With `reference_solution` | 164 (61%; 100% of the 148 added this expansion) |
| Mutation-checked testing cases | every `testing` case added since Wave A |
| Sandbox images | 9 (base, python, node, jvm, go, rust, dotnet, **php**, **ruby**) — all in the GHCR publish matrix |

**By language:** python 55, javascript 34, go 25, java 24, sql 19,
rust 18, csharp 18, typescript 18, shell 10, c 7, yaml 7, php 6, kotlin 6,
ruby 6, hcl 5, cpp 3 — 16 tracked languages plus 7 language-neutral devops
cases.

**By task type:** bug_fix 81, feature 72, refactoring 32, security 28,
testing 26, performance 16, devops 10, data_engineering 3.

## Waves shipped (chronology)

Wave A and the devops rebalance (120 → 199) are documented in the plan's
[Progress section](CORPUS_EXPANSION_PLAN.md#progress-updated-2026-07-09).
Since then:

| Wave | Batches | Corpus | What |
|---|---|---|---|
| **B** — multi-file framework sweep | 7 | 199 → 232 | Cross-layer (L2) cases per web stack: FastAPI, Express, Spring, Gin, Flask, Axum, ASP.NET — all portless in-process testing (TestClient, `app.listen(0)`, MockMvc, `httptest`, `WebApplicationFactory`, tower `oneshot`) |
| **C** — new language tracks | 3 | 232 → 250 | Kotlin (jvm image + Kotlin warmup), PHP (new php:8.3 image, PHP-native mutation harness), Ruby (new ruby:3.3 image, Minitest, Psych-4-accurate `unsafe_load` security case) |
| **D** — depth in core tracks | 3 | 250 → 268 | Go stdlib (nil map, `errors.Is`, generic LRU, command injection), TypeScript (forEach-async, typed EventBus, prototype pollution), plain Java/JUnit (Integer cache `==`, ConcurrentModificationException, path traversal) — all previously framework-only tracks |

Each C/D batch follows the same shape per track: L1 idiom bug_fixes,
an L2 cross-file feature, a behavior-preserving refactor with a
"behavior-drifted" broken variant, a mutation-checked testing case, and a
security case with a realistic *incomplete-fix* broken variant (e.g.
top-level-only prototype-pollution guard, naive `".."` string check defeated
by an absolute path).

## Quality gates in practice

`verify-corpus` (the plan's [non-negotiable gate](CORPUS_EXPANSION_PLAN.md#quality-gates-non-negotiable-or-500-cases-make-the-corpus-worse))
has now caught **~25 authoring defects pre-commit** across the expansion —
broken variants that weren't actually broken, perf budgets the slow code
beat, a csproj glob compiling tests into the app, a `filter_var` "drift"
that didn't drift, and the fnmatch `**/x.go` root-level-file miss (Go batch).
None shipped. New-image batches additionally smoke-test the offline
toolchain in-container *before* any case is authored.

## Remaining to 500 (232 cases)

Per the plan's [wave sequencing](CORPUS_EXPANSION_PLAN.md#the-waves-380-cases-ordered-by-machinery-dependencies):

1. **More Wave D depth** — deepen Kotlin/PHP/Ruby to ~20 each; Rust/C# stdlib
   seams; python/node depth beyond current frameworks.
2. **Wave E — workflow shapes** — multi-step tasks, `setup_repo`/`git_init`
   repo-scale cases, failing-CI-fix shapes ([new interaction shapes](CORPUS_EXPANSION_PLAN.md#new-interaction-shapes-not-just-new-topics)).
3. **Wave F + moat hardening** — private held-out slice, paraphrase variants,
   anti-memorization checks ([moat hardening](CORPUS_EXPANSION_PLAN.md#moat-hardening-do-alongside-wave-f)).

Rebalance note: data_engineering (3) and performance (16) are furthest below
their target allocation and should be woven into upcoming depth batches.
