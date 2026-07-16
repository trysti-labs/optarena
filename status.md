# OptArena Status

_As of 2026-07-16, on `main` (github.com/trysti-labs/optarena), through
batch 32 - **corpus at 470/500 (94%)**._

## Where things stand

The 0.1 platform cut is done and stable (see ARCH.md): verify-corpus CI gate,
driver telemetry, trials stability, p95 aggregation, GHCR-published sandbox
images, Apache-2.0 licensing. Since then the work has been entirely corpus
expansion per [CORPUS_EXPANSION_PLAN.md](CORPUS_EXPANSION_PLAN.md): **120 →
460 cases**, all new cases shipped with a `reference_solution` and
behaviorally-failing `broken_solutions`, every variant proven through
`verify-corpus` before commit. **data_engineering is now at target
(15/15)**; bug_fix has been at target (95/95) since batch 15; **devops is
at 44/45** and **refactoring is at 64/70**, both effectively closed out.
**The L3 (repo-scale) track is live with two
starter repos**: `repos/fastapi-tasktracker` (python image, 9 cases) and
`repos/express-ts-shortlink` (node image, 5 cases) - 14 L3 cases across two
toolchains.

**Docker became available in the working environment as of batch 30** -
every case verified since then (and the whole kotlin track, retroactively)
has been proven through the actual `--network none` sandbox, not a bare
host toolchain. This immediately paid for itself: it caught a corpus-wide
gate breach across all 15 kotlin cases (a missing Maven `<executions>`
block meant the image's Kotlin-plugin warmup - and every case's own build -
silently never invoked the Kotlin compiler) and a stale, too-loose
performance budget. Both fixed; see
[Quality gates](#quality-gates-in-practice) below.

A full-corpus `verify-corpus` sweep (891 variants / 368 verifiable cases)
done alongside batch 26 found and fixed two real pre-existing gate breaches:
CI has very likely been red on `main` since the first Terraform devops case
was added, because `terraform init` against `aws_*`/`null_*` resources needs
the public registry and the sandbox runs with `--network none` - fixed by
baking an offline provider mirror into the base image. See
[Quality gates](#quality-gates-in-practice) below for both fixes.

## Corpus census (470 cases)

| | |
|---|---|
| Total cases | **470** (target 500, 94%) |
| With `reference_solution` | 366 (78%; 100% of the 350 added this expansion) |
| Mutation-checked testing cases | every `testing` case added since Wave A |
| L3 (repo-scale, `setup_repo`) cases | 14 (fastapi-tasktracker 9, express-ts-shortlink 5) |
| Multi-prompt session cases | 2 |
| Sandbox images | 9 (base, python, node, jvm, go, rust, dotnet, php, ruby) - all in the GHCR publish matrix |

**By language** (directly recomputed from the case JSONs' `language` tags):
python 100, javascript 57, java 34, go 33, csharp 29, rust 28, yaml 28,
typescript 28, sql 24, php 18, ruby 18, kotlin 17, shell 15, c 11, hcl 10,
dockerfile 6, cpp 5, makefile 2 - plus 7 language-neutral devops cases.

**By task type:** bug_fix 95, feature 93, **refactoring 64**, testing 63,
security 53, devops 44, performance 43, data_engineering 15.

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
| **Testing + refactoring breadth** | 1 | 348 → 354 | Batch 18: four mutation-checked **testing** cases on distinct functions - python parse_duration (1h30m45s parser; seconds-dropped / hours-as-minutes / empty-returns-0 mutants) and merge_intervals (touching-not-merged / unsorted / contained-shrinks-end), js slugify with the corpus's first **node-native** mutation harness (`node mutation_check.js` - no python dependency), and java ExcelColumn with a **java-native single-file harness** (`java MutationCheck.java`, plain javac/java -ea, no maven/JUnit). Two **refactoring** cases: python range(len)->zip with a per-line-rounding drift broken, and js constructor+prototype -> ES class with a static-lost-in-translation broken. Every vacuous/broken variant tuned to survive the shape checks so the mutation harness or hidden suite is what catches it |
| **L3 repo #2** - express-ts-shortlink | 1 | 354 → 359 | Batch 19: second starter repo, on the **node image** - a TypeScript + Express 4 link shortener (src/models-store-services-routes layering, strict `tsc -p .` as part of every oracle, plain-JS node:test suites against the compiled dist/, portless `listen(0)` + global fetch). Local ambient typings under types/ (function+namespace merge mirroring @types/express) since tsc does not consult NODE_PATH and the sandbox has no per-project node_modules. 5 L3 cases: link-expiry feature (410 Gone on the redirect path, expired hits not counted; brokens: redirect-unchecked, click-recorded-before-check), shared sendError refactor across 3 routers (broken: body-key drift), mutation-checked summarizeClicks tests (node-native harness recompiling TS per mutant; earliest-day tie-break mutants), URL scheme allowlist security fix (broken: case-sensitive prefix blocklist defeated by 'JavaScript:'), and a top-links aggregation endpoint whose star broken is **route shadowing** (/top registered after /:slug) |
| **Perf + devops push #2** | 1 | 359 → 365 | Batch 20: two calibrated **performance** cases (js Array.includes-in-loop -> Set at 30k x 30k, naive ~7s native vs 2.5s budget; python max()+remove() full-ranking extraction -> one descending sort at n=50k, naive ~6.5s vs 2s budget - both with a wrong-output fast broken AND a proven still-quadratic broken). Three **devops**: bash backup-script hardening proven by REAL bash runs against paths with spaces (brokens: unmodified, strict-mode-but-still-unquoted - set -euo pipefail alone doesn't fix word-splitting), K8s HPA where the oracle demands BOTH the autoscaling/v2 HPA and the resources.requests.cpu it computes against (brokens: request-less deployment, min > max), and compose production hygiene (pin :latest, restart policies, memory limits; 3 brokens). One **testing**: java Roman-numeral parser on the java-native harness (subtractive-rule / empty-returns-0 / equal-neighbour-subtracts mutants) |
| **Refactoring breadth** | 1 | 365 → 371 | Batch 21: six distinct refactor shapes across six languages, each with a behavior-*drift* broken - python 3x-copy-pasted retry loops -> one @retry decorator (drift: retries EVERY exception, caught by a non-transient-error-propagates-immediately test), js triple parallel switch -> lookup table (drift: fat-fingered rate; hidden test also probes 'toString'/'constructor' so plain `obj[key]` lookups fail - Object.hasOwn required), java triple if/else-if tier chains -> enum with data (drift: silver 0.05 -> 0.5; cross-platform CheckPricing driver compiles whatever .java layout the model chose), sql three correlated scalar subqueries -> grouped CTE + LEFT JOIN (drift: INNER JOIN drops zero-order customers, proven via real sqlite), python os.path -> PurePosixPath with exact splitext semantics (drift: split('.')[0] breaks 'report.tar.gz' and '.bashrc'), and bash copy-pasted env blocks -> function/loop with byte-identical appended output (drift: replica counts swapped) - the last two proven by real interpreter runs |
| **Testing + devops + perf + security** | 1 | 371 → 381 | Batch 22: four mutation-checked **testing** cases (python next_business_day, js parseCookies with a value-containing-'=' pin, java daysInMonth/isLeapYear with the century-rule mutant, python ordinal suffix). Three **devops**: Dockerfile layer-caching reorder (manifests-install-source; broken copies package.json without the lockfile), a hardcoded-password-to-Secret migration where the broken is a secretKeyRef name/key MISMATCH (CreateContainerConfigError), and a from-scratch nightly cron workflow whose point is the guardrails (workflow_dispatch escape hatch, contents:read, job timeout, concurrency group). Two calibrated **performance**: python sort-hoisted-out-of-the-query-loop (100k samples x 400 queries, naive ~4s vs 1.5s budget) and java StringBuilder vs `+=` (n=120k, naive ~7.5s vs 2.5s budget). One **security**: python subprocess shell-injection fix (argv list, no shell) whose incomplete-fix broken quotes the filename but keeps shell=True - defeated by an embedded single quote |
| **Broad push toward 400** | 1 | 381 → 391 | Batch 23: 2 **feature** (python notification dispatcher gains a webhook channel; js EventBus gains prefix-wildcard subscriptions with a namespace-boundary discriminator - `user.*` must not match bare `user` or `userprofile.updated`). 2 **testing**: python camelCase->snake_case with acronym handling, js one-level array flattener. 2 **devops**: terraform S3 hardening proven by a REAL `terraform init`+`validate`+`fmt -check` run (versioning + a 4-flag public-access-block + default encryption; broken leaves one PAB flag false), and a GitHub Actions docs-path-filter (broken applies `paths-ignore` to push but not pull_request - the common real-world half-fix). 2 calibrated **performance**: python dict-merge-in-loop (`{**acc,**d}` -> `.update()`, n=30k growing keys, naive ~2.5s vs 1.2s budget) and java dedup (`List.contains` -> `LinkedHashSet`, n=150k all-unique, naive ~3.2s vs 1.5s budget). 1 **security**: js static-asset path traversal, where the vulnerable code uses `path.resolve` (not `path.join`) so an absolute `requestedPath` resets off ROOT entirely - the incomplete-fix broken's `..`-substring check stops relative traversal but not the absolute-path bypass, which has no dots in it at all. 1 **refactoring**: python manual index-loops -> `zip(*matrix)`, drift over-generalizes row_maxes into a column operation on a non-square matrix |
| **400 milestone** | 1 | 391 → 400 | Batch 24: 2 **feature** (python feature-flag evaluator gains a percentage-rollout type keyed on (flag name, user) so independent flags don't share an enabled-user set; js template renderer gains nested `{{#if}}` blocks via a depth-tracking parser - the first naive regex-loop attempt failed verify-corpus on real nesting and was rewritten). 2 **testing**: python run-length encode/decode, java greedy word-wrap (exact-width-fit and dropped-trailing-line mutants). 2 **devops**: k8s NetworkPolicy default-deny-all + scoped allow (broken deny covers Ingress only, leaving egress wide open), Makefile build-dependency fix proven by a REAL `make -n` dry-run ordering check (scoop-installed make on this host). 2 calibrated **performance**: python list-membership-in-loop -> set (n=30k, naive ~2.7s vs 1.5s budget) and js `Array.indexOf`-per-element frequency counter -> Map (n=80k all-distinct, naive ~3.5s vs 2s budget; wrong-output broken is a plain object, which reorders integer-like string keys). 1 **data_engineering** (closing that category to target, 15/15): CSV daily-totals rollup with gap-fill - days with no rows must appear at 0.0, not be skipped |
| **Refactoring + security + feature** | 1 | 400 → 410 | Batch 25: 3 **refactoring** (python nested-if validation -> flat guard clauses, drift flips a `>` boundary to `>=`; js callback pyramid -> async/await via promisify, drift passes the lookup id instead of the canonical user.id; sql two parallel CASE ladders -> one VALUES-mapping CTE + LEFT JOIN, drift INNER JOIN drops unmapped-status rows). 2 **feature** (python fixed-window rate limiter -> true sliding window, discriminator is inclusive boundary eviction at exactly `window` seconds ago; java Money value-object gains remainder-distributing `allocate(n)`, broken loses the indivisible cent so parts don't sum back). 2 **security** (python HTML-injection f-string -> `html.escape`, incomplete-fix broken hand-replaces `<`/`>` only leaving `&` and quotes raw; js timing-unsafe token `===` -> `crypto.timingSafeEqual`, **the oracle times a first-char vs last-char mismatch** to catch a short-circuit loop that looks constant-time - stress-tested 20x locally, 8x threshold against a ~thousands-x real gap). 2 **devops** (terraform variable validation blocks bounding instance_count and allowlisting environment, proven by real `terraform validate`, broken validates only one var; GitHub Actions node 18/20/22 matrix with `fail-fast: false`, broken leaves fail-fast at the default). 1 **testing** (python IPv4 CIDR-membership, mutation-checked with mask off-by-one / `/0`-matches-nothing mutants) |
| **Perf + devops + testing + security + feature** | 1 | 410 → 418 | Batch 26, in fresh languages (go, kotlin, ruby, rust, php): 2 **performance** (go `regexp.MustCompile` recompiled per-call -> compiled once, budget 500ms at n=150000; kotlin naive exponential recursive Fibonacci -> linear, budget 2.5s at n=45). 2 **devops** (k8s PodDisruptionBudget for an existing Deployment, minAvailable >= 2 + matching selector; `.dockerignore` excluding secrets/dev-artifacts from a naive `COPY . .`). 2 **testing**, mutation-checked (ruby balanced-brackets checker; rust binary search - see quality-gates note below, both needed real fixes after verify-corpus caught genuine problems). 1 **security** (go zip-slip path traversal in archive extraction, incomplete-fix broken checks only a leading ".." prefix and misses a ".." component appearing later in the path, verified against Go's actual filepath.Join/Clean semantics first). 1 **feature** (php fixed-capacity LRU cache, broken behaves like FIFO since get() doesn't refresh recency) |
| **Perf + devops + testing + security + feature #2** | 1 | 418 → 426 | Batch 27, in csharp/java/typescript: 2 **performance** (C# `new Regex(...)` constructed per-call -> static readonly field, budget 800ms at n=300000; java naive exponential recursive Fibonacci -> **memoized** (HashMap cache) - a deliberately different fix shape from batch 26's Kotlin iterative-fib, on the java-native javac+java bootstrap with no maven). 2 **devops** (GitHub Actions blanket `contents: write` moved to only the one job that needs it, broken grants it to the wrong job; k8s container securityContext hardening - readOnlyRootFilesystem/no-priv-escalation/non-root/capabilities-drop-ALL). 2 **testing**, mutation-checked (C# xUnit Caesar cipher; TypeScript query-string parser on the plain tsc+node harness, no test framework). 1 **security** (java `ObjectInputStream.readObject()` on untrusted bytes -> Jackson JSON - Java's version of the pickle/YAML/unserialize RCE family already covered for Python/Ruby/PHP; incomplete-fix broken falls back to legacy Java deserialization "for old sessions", caught by an EvilGadget tripwire). 1 **feature** (TypeScript Trie/prefix-tree with sorted prefix lookup; broken returns unsorted traversal-order results). All 21 variants passed verify-corpus on the first attempt |
| **Devops-only push** | 1 | 426 → 434 | Batch 28, all 8 cases devops - closes the category to 44/45. Fresh K8s object types the corpus lacked entirely: Ingress (TLS + host routing), a Service correctly mapping an external port to the container's real (different) port, ResourceQuota+LimitRange (default AND defaultRequest, not just limits). Fresh workflow shapes: GitHub Actions cross-job artifact upload/download (separate runners don't share a filesystem), a Compose named volume for Postgres persistence (vs. a host bind-mount), a Terraform for_each+locals dedup of 3 copy-pasted resources (real offline `terraform init`+`validate` via the batch-26 provider mirror), a Go Dockerfile multi-stage rewrite to a `scratch` final image (still-ships-the-toolchain broken swaps in `golang:1.22-slim`), and a deploy script needing both `set -euo pipefail` AND a `trap`-based lock-file cleanup (proven by a real bash run with a forced-failing step). All 24 variants passed verify-corpus on the first attempt |
| **Refactoring-only push** | 1 | 434 → 442 | Batch 29, one case per language (8 languages) - closes the category to 64/70. ruby case/when -> Hash dispatch; php if/elseif -> PHP 8 `match`; kotlin external when-chain type-switching over a sealed class -> polymorphic per-subclass methods; c duplicated NULL/length guard clause -> one static helper; rust manual index-based while loop -> iterator chain (enumerate+map+collect); go three functions repeating an identical error format string -> one `requireEnv()` helper; typescript manual `&&`-chained null checks -> optional chaining + nullish coalescing; sql manual `OR`-chain of equality checks -> a single `IN (...)` clause, proven via real sqlite3. Each has a behavior-drift broken. verify-corpus caught the same authoring mistake twice (go and, earlier in the design pass, c): an `expected_files` regex assuming duplicated literal text survives a *correct* refactor, which it doesn't by definition - both fixed pre-commit (see [Quality gates](#quality-gates-in-practice)) |
| **Real-Docker verification begins + perf/security/feature push** | 1 | 442 → 450 | Batch 30, the first batch verified through the actual `--network none` sandbox rather than a bare host toolchain (Docker became available in-environment). Found and fixed a corpus-wide gate breach affecting **all 15 kotlin cases** and a stale performance budget - see [Quality gates](#quality-gates-in-practice). New cases, each Docker-verified: 2 **performance** (typescript `Array.find()`-in-loop -> a Map, first-wins tie-break preserved, n=60k naive ~3.7s vs 1.5s budget; go string `+=` -> `strings.Builder`, n=40k naive ~2.5s vs 1.2s budget, both calibrated *inside* the real sandbox container). 2 **security** (csharp SQL built by string interpolation -> a parameterized query text+params pair, no live DB needed, broken escapes quotes but still interpolates; kotlin `java.util.Random` session tokens -> `SecureRandom`, broken switches to `SecureRandom` but hardcodes its seed - an order-independent discriminator checks the token against a 30-deep window of the known fixed-seed sequence rather than assuming call order). 3 **feature** (ruby cart bulk-discount tiers, broken orders the tier table ascending so `Array#find` matches the lowest satisfied threshold instead of the highest; php validator chain gains per-field stop-on-first-failure, broken stops *all* fields instead of just the failed one; go in-memory todo store gains status-filter + pagination, broken paginates the unfiltered store before filtering - the discriminator needed a white-box same-package test to seed genuinely interleaved statuses, since the store has no public setter). 1 **testing** (go alphanumeric-only palindrome check, mutation-checked) |
| **460 push - underused languages** | 1 | 450 → 460 | Batch 31, all Docker-verified, deliberately targeting the corpus's thinnest language cells. 2 **performance**, both calibrated on the *debug* build profile (matching `cargo test`'s default, not `--release`) since a release-optimized O(n^2) is too fast to bust any sane budget: rust `Vec::contains` dedup -> `HashSet` (n=30k, naive ~1.5s vs 700ms budget; wrong-output broken sorts first, losing first-occurrence order), ruby `Array#delete`-per-id -> `reject` + `Set` (n=20k, naive ~0.86s vs 0.4s budget; wrong-output broken dedupes surviving items via Set arithmetic). 3 **security**: rust `sh -c` shell interpolation -> direct argv (no shell at all - the discriminator just asserts `cmd.get_program() == "ping"`, not `"sh"`); csharp ReDoS from a nested-quantifier regex -> a linear pattern, calibrated in-container (vulnerable ~2.6s on a 26-char near-miss vs 0ms fixed) with an incomplete-fix broken that bolts on a `MatchTimeout` band-aid instead of fixing the pattern; typescript CSV/formula injection (`=`/`+`/`-`/`@` prefixes execute as formulas in Excel) -> neutralize with a leading apostrophe, broken guards only the `=` prefix. 3 **feature**: kotlin `Cache` gains TTL expiry via an injectable `Clock` (broken uses an exclusive expiry boundary); terraform S3 lifecycle rule (transition + noncurrent-version expiration), proven by real `terraform validate`, broken leaves the rule `status = "Disabled"` (AWS silently ignores a disabled rule entirely); C++ `RingBuffer` gains overwrite-oldest-when-full semantics, broken forgets to advance `head_` so ordering breaks across multiple wraparounds. 2 **testing**, mutation-checked: kotlin Roman-numeral formatter, and a SQL `RANK() OVER (PARTITION BY ...)` top-spender-per-region view exercised via real sqlite3 (mutants: ranking direction flipped, ties widened, `LEFT JOIN` leaking an orderless customer into the results) |
| **470 push - c/php/ruby/shell/cpp/hcl breadth** | 1 | 460 → 470 | Batch 32, all Docker-verified, targeting the categories still below target (performance/testing/feature/security) in languages the corpus was thinnest on (c, php, shell, ruby, cpp, hcl). 2 **performance**, both calibrated in-container: C `rtrim_spaces()` recomputing `strlen()` on every loop iteration (O(n·k) for k trailing spaces) -> cache the length once (n=700000/685000 trailing, naive ~2.1s vs 1000ms budget), PHP `in_array()` per item against an allowlist (O(n·m)) -> `array_flip()` once + `isset()` (n=m=100000, naive ~2.7s vs 1200ms budget; a wrong-output broken flips the wrong side of the comparison, losing duplicate items). 3 **testing**, mutation-checked: PHP title-case formatter with small-word exceptions (first/last-word capitalization dropped, small-word check inverted), a POSIX-sh IPv4 dotted-quad validator (octet bound off-by-one both directions, octet-count check loosened), and a C fixed-size-stack balanced-bracket checker (forgot-to-pop stack corruption, closer-type check dropped, final all-closed check dropped). 3 **feature**: Ruby `Order` gains percentage AND fixed-amount coupons with a minimum-order-value gate on fixed coupons only, broken forgets to clamp the fixed discount at 0 (goes negative); C++ `Matrix` gains `transpose()`, broken iterates the loop but never swaps the (r, c) coordinates, so it's just a same-shape deep copy; Terraform `aws_iam_policy` scoped to one S3 bucket's ARN, proven by real `terraform validate`, broken scopes `Resource` to a bare `"*"` wildcard (every bucket in the account). 2 **security**: C `printf(msg)` format-string vulnerability -> pass `msg` via `"%s"`, incomplete-fix broken routes it through `snprintf(buf, n, msg)` instead - still format-string-vulnerable, just one function later; shell `read_report.sh` path traversal via unvalidated `".."` segments -> canonicalize with `realpath -m` and verify the result stays inside the base dir, incomplete-fix broken only rejects a name that *starts with* `"../"`, missing the equally-effective escape through a real subdirectory (`subdir/../../secret.txt`). All 28 variants passed `verify-corpus` on the second attempt per case (a handful of first-pass authoring bugs - a mistyped test expectation, an under-calibrated perf budget, a mutant with no observable effect - were each caught and fixed before commit, consistent with the corpus's established discipline) |

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

**Batch 26 ran a full-corpus `verify-corpus` sweep for the first time since
Wave A (891 variants / 368 verifiable cases) and found two real,
previously-invisible defects, both fixed:**

1. **CI has very likely been red on `main`** since the first Terraform
   `terraform init` usage was added: `aws_*`/`null_*` resources need the
   public registry to resolve their provider, but `check_command` runs under
   `--network none`. This was invisible case-by-case because whoever verified
   those two cases apparently had real network access at the time (a host
   run, not the sandboxed guarantee `verify-corpus` exists to prove). Fixed
   by baking an offline provider mirror (hashicorp/aws + hashicorp/null) into
   the base image at build time and pointing every terraform invocation at it
   via `TF_CLI_CONFIG_FILE` - the same "solve it once in the image" pattern
   used for every other toolchain.
2. A Wave-D-era mutation-checked case (predating the batch-14 pycache fix)
   still had the stale-bytecode bug and intermittently let a mutant survive -
   invisible in isolated verification because it's timing/load-dependent;
   the full sweep's contention was enough to trigger it. Fixed with the same
   `__pycache__` purge already proven in four sibling harnesses.

Batch 26 also surfaced a **new failure mode worth internalizing**: a
mutation candidate for a search algorithm (`lo = mid` instead of
`lo = mid + 1`) doesn't just fail cleanly - it can loop forever. A first
attempt at fixing this went into an infinite loop that ran for 9+ minutes in
a background container (silently eating a whole CPU core) before being
noticed and stopped by hand; the corrupted, never-restored source file it
left behind on a bind-mounted host directory then produced a string of
confusing, contention-flavored timeouts across *unrelated* re-verification
attempts until the actual cause (a stale mutated file, not system load) was
traced. The fix has two parts: (a) any mutation candidate must be checked
with a **Python-level `subprocess.run(..., timeout=N)`**, not a shell-level
`timeout` wrapper (which does not reliably kill hung grandchild processes),
so a hang can never corrupt a host-mounted fixture mid-test; (b) the shipped
harness itself now also carries a per-attempt timeout as a backstop, since a
real *model-submitted* broken solution could hang the same way in production
use, not just an authoring mistake.

Batch 27 applied those same lessons up front (per-mutant safety reasoned
through before writing the case, not discovered after) and every one of its
21 variants passed `verify-corpus` on the first attempt - the intended
steady state once a failure mode has been internalized once.

Batch 29 (refactoring) surfaced a *different* recurring authoring mistake,
twice in one design pass: an `expected_files` regex written to assert "the
duplicated text is still there" using the literal pre-refactor phrasing -
which a *correct* refactor fails by definition, since the whole point is
collapsing N copies to one. Both (Go's error-format-string check, C's
NULL-guard check) were caught by the reference solution itself failing
`verify-corpus`, not by a broken variant passing when it shouldn't - a
useful reminder that the gate protects against false negatives on the
*reference* just as much as false positives on *broken* variants. Fixed by
counting occurrences with a regex tolerant of the correct post-refactor
phrasing (e.g. matching `arr\s*[=!]=\s*NULL` instead of requiring the exact
losing-side spelling) and asserting `<= 1`, not requiring an exact broken
phrasing to persist.

**Batch 30: Docker became available in-environment, and the first real
`--network none` sandbox run of the kotlin track immediately failed every
single one of its 15 cases** (14 pre-existing + the one new to that batch).
Root cause: `docker/jvm/kotlin-pom.xml`'s `kotlin-maven-plugin` declaration
had no `<executions>` block, so Maven's default `jar`-packaging lifecycle
never actually bound its compile/test-compile goals - `mvn package` "built"
successfully during image warmup by silently never touching a single `.kt`
file, so the plugin (and its own dependencies) never got resolved into the
image's offline `~/.m2` cache at all. Every kotlin case's own `mvn -o test`
then failed offline for the same structural reason, previously invisible
because prior verification ran on a host with live network access (which
masks a *caching* gap - the plugin resolves fine online, it's specifically
absent from the image's *offline* mirror). Fixed by adding the standard
`<executions>` block (`compile` + `test-compile` goals) to
`docker/jvm/kotlin-pom.xml`, rebuilding the jvm image, confirming the
`~/.m2` cache actually populates (`kotlin-maven-plugin`, `kotlin-compiler`,
etc. now present, versus only `kotlin-bom` before), then bulk-patching the
identical missing block into all 14 pre-existing kotlin cases' own
`pom.xml` (case files ship a full project skeleton in `setup_files`, so the
same bug existed independently in each one). All 15 now build and test
correctly through the real sandbox. The same sweep also caught
`optimize_kotlin_fib_naive_recursion`'s performance budget as too loose -
naive exponential `fib(45)` completes in under 2s on this container's
hardware (~1.9s), comfortably under its 2500ms budget; recalibrated to
`fib(48)` (naive ~7.9s in-container) with a 3000ms budget, timed directly
inside the sandbox rather than assumed.

## Remaining to 500 (30 cases)

**L3 (repo-scale) is live on two toolchains.** The `setup_repo`/`git_init`
schema fields exist (see `prepare_workspace` in cases.py; documented in the
module docstring); `repos/fastapi-tasktracker` (python image) carries 9
verified cases across 6 task types and `repos/express-ts-shortlink` (node
image) carries 5 - setup_repo is proven beyond a single image. Next L3
steps: L3 performance cases (need in-container calibration per the perf
protocol), then further Wave D repos (Spring multi-module, Gin, Axum,
ASP.NET, Rails, Laravel). Remaining work after L3, per the plan's
[wave sequencing](CORPUS_EXPANSION_PLAN.md#the-waves-380-cases-ordered-by-machinery-dependencies):

1. **More Wave D depth** — every big track now has a stdlib-seam layer
   (Python, Node/JS, Rust, C#, Go, Java, TS all covered); Kotlin/PHP/Ruby at
   12 each still climbing to ~20. Still open: SQL/Go depth, and pushing the
   new-language tracks toward their targets.
2. **Wave E — workflow shapes** — multi-step tasks, `setup_repo`/`git_init`
   repo-scale cases, failing-CI-fix shapes ([new interaction shapes](CORPUS_EXPANSION_PLAN.md#new-interaction-shapes-not-just-new-topics)).
3. **Wave F + moat hardening** — private held-out slice, paraphrase variants,
   anti-memorization checks ([moat hardening](CORPUS_EXPANSION_PLAN.md#moat-hardening-do-alongside-wave-f)).

Rebalance note: **bug_fix (95/95), data_engineering (15/15), devops (44/45),
and refactoring (64/70) are all effectively at target - stop adding any of
them** (batches 15-25 shipped zero bug_fix; batch 24 closed out
data_engineering; batch 28 closed out devops; batch 29 closed out
refactoring). After batch 32 the remaining categories are **performance
(43/45), testing (63/65), feature (93/95), and security (53/55)** - all four
now within 2 cases of target, so essentially any mix of them closes the
corpus out. Batch 17
proved host-calibrated perf budgets work when margins are wide (naive 3-4x
over budget on fast native hardware, fast path 50-500x under it); batch 18
added node-native and java-native mutation harnesses so testing cases in
those tracks no longer need python in the loop. **Update: as of batch 30,
Docker is available in-environment**, so "verified" now means proven
through the real Docker sandbox directly (not a bare host run standing in
for it) - the gap flagged in this note previously (the terraform regression
batch 26 found, see [Quality gates](#quality-gates-in-practice)) no longer
applies going forward; batch 30's kotlin fix is the second real gate breach
this same real-sandbox verification has caught. Next levers: performance/
feature/security breadth (all Docker-verifiable now), and the next L3
starter repo (Spring multi-module, Gin, Axum, ASP.NET, Rails, or Laravel).
