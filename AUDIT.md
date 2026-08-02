# OptArena Audit History (Consolidated)

This file consolidates four independent audit rounds plus the execution/
build log into one document. It replaces five separate files that used to
live at `AUDIT.md`, `AUDIT_2.md`, `AUDIT_EXEC.md`,
`DEV_NOTES/AUDIT_VERIFICATION_2026-07-20.md`, and
`DEV_NOTES/AUDIT_CODE_AND_DOCS_2026-07-25.md`. Each round is preserved in
its own dated section below, with its own original ID scheme intact (no
attempt is made to renumber every finding into one global ID space - that
would be error-prone and would destroy traceability back to the fixes and
tests that reference these IDs). Where two rounds happen to reuse the same
letter prefix for unrelated things (for example Round 1's `B-*`/`C-*` IDs
are not the same namespace as Round 4's `B-*`/`C-*` IDs), the section
headers below call that out explicitly.

Nothing in this consolidation changes any finding's content, fixes anything
new, or re-evaluates any verdict - it is a faithful merge and reorganization
of the five source documents, with one freshly-written "Current state"
summary added up front and the current corpus numbers re-verified live
against the repository as it stands today.

## Table of contents

- [Current state (read this first)](#current-state-read-this-first)
- [Round 1 (2026-07-20): Full Verification Audit](#round-1-2026-07-20-full-verification-audit) - full code review against an external security docx and a remediation plan, plus two remediation passes. IDs: `C-01..`, `H-01..`, `M-01..`, `L-01..`, `V-1..`, `B-1..` (this round's own numbering, not Round 4's).
- [Round 2 (2026-07-25): Code + Docs + Website Consistency Audit](#round-2-2026-07-25-code--docs--website-consistency-audit) - unused/stale code, wording consistency, docs-vs-code accuracy, security, docs-site styling, docs completeness. Findings are prose/table based, not ID-coded.
- [Round 3 (2026-07-26): Reliability/Performance/Docker/Packaging Audit (F-01..F-18)](#round-3-2026-07-26-reliabilityperformancedockerpackaging-audit-f-01f-18) - first external-style review, security explicitly deferred/excluded. IDs: `F-01..F-18`.
- [Round 4 (2026-07-26): Security/Coverage/Utility Audit (B/S/D/C/U/P)](#round-4-2026-07-26-securitycoverageutility-audit-bsdcup) - second, independent audit; explicitly does not restate Round 3's F-01..F-18. IDs: `B-1..B-10`, `S-1..S-8`, `D-1..D-9`, `C-1..C-5` (not the same namespace as Round 1's `C-01..`), `U-1..U-5`, `P-1..P-2`.
- [Execution & Corpus Development Log](#execution--corpus-development-log) - chronological narrative log (`## 1.` through `## 22.`) of the actual work sessions that followed the four audit rounds: incidents, hardening passes, calibration runs, driver sweeps, corpus-integrity fixes, and the corpus-expansion pushes that grew the corpus from 510 to 836 cases.

---

## Current state (read this first)

This section is the honest summary, not marketing copy. It reflects the
repository as verified live on the date of this consolidation, cross-checked
against the latest text of Round 4 and the execution log (which is the most
detailed and most recently active of the five source documents).

### What's resolved

- **Every code-fixable finding from all four audit rounds has been fixed
  and verified**, with regression tests, at the time each round's own
  remediation pass completed. This includes: Round 1's Criticals/Highs/
  Mediums/Lows (workspace isolation, environment allowlisting, container
  hardening, schema validation, CLI reorganization); Round 3's F-01
  through F-18 (cleanup lifecycle, run checkpointing, bounded output
  capture, clean CLI errors, whole-case deadlines, parallel worker pools,
  incremental result indexing, atomic comparison saves, trial/duration
  metric consistency, infrastructure-error flagging, transactional pack
  installs, image lock manifests, multi-platform image builds, image
  overrides, engine-health TTLs, wheel-smoke/hadolint/rootless-Podman CI,
  structured run events); and Round 4's B/S/D/C/U/P findings (parallel
  checkpoint/deadlock bugs, `setup_repo` path traversal, API keys off
  argv, image-reference validation, non-root sandbox support, dashboard
  escaping, lock-file ownership, dead code removed, CLI/packaging gaps).
- **Round 2's single actionable code fix** (the `openai-agents` driver
  silently phoning prompts/output to `api.openai.com` via ambient tracing)
  and its sibling telemetry findings (crewai, langgraph) were flagged
  there as analysis-only at the time Round 2 was written; the driver
  layer was substantially rewritten during the execution log's section 12
  (`drivers/sdk_base.py`) in response to Round 4's B-3, which is the same
  code path - see Round 4 and the execution log for the shipped fix
  details.
- **Round 4's C-3** ("44 of 510 cases have no failing variant") is now
  **fully resolved**, independently re-verified live for this
  consolidation, not just trusted from prior text: across all 836 cases
  in the corpus today, zero cases outside the `bug_fix`/`refactoring`/
  `performance`/`security` task types (which get an implicit `unmodified`
  must-fail variant) lack a `broken_solutions` entry. See the execution
  log's section 17 for the remediation work and the real Rust
  `CARGO_TARGET_DIR` sandbox bug found while doing it.
- **Round 4's C-4** ("the corpus is thin exactly where the product
  differentiates") is **partially resolved** through five phases of
  corpus-expansion work documented in the execution log's sections 18-22:
  disruption/multi-turn coverage went from 6 cases (1.2%) to 100+ cases
  across every one of the 18 languages; difficulty-3 depth went from
  ~2-3 cases per language to 4-27; and difficulty 4/5, which had zero
  cases before this work, now has exactly 4 D4 and 2 D5 cases in every
  language (111 cases total). Difficulty 4/5 volume (111 of 836, about
  13%) is explicitly **not** claimed as a final target - it remains the
  smallest tier and further growth is gated on building more repo-scale
  fixtures, which is expensive per case.
- Two genuine bugs were found and fixed **outside** the four formal audit
  rounds, during calibration/driver-sweep work (execution log sections
  12, 14, 16): the `"**/X"` glob path-matching bug that made 87 built-in
  cases artificially unwinnable by any flat-file-writing driver; a
  preflight warning for structurally unwinnable cases; the `openai-agents`
  driver never closing its async client (leaking tracebacks to stderr,
  cosmetic but real); and a Windows-CRLF-corrupting-the-Linux-sandbox bug
  that was silently failing shell-heavy cases for any driver/model
  writing files from a Windows host.
- **The aider `setup_repo` context gap (previously listed below as open)
  is now fixed.** See execution log section 23: `aider_cli.py` only ever
  told aider about `case["setup_files"]`'s own small overlay, never
  anything `setup_repo` copied in, and `--no-git` meant aider had no repo
  map of its own to fall back on either - a D4/D5 case's model had
  effectively zero visibility into the real project. Fixed by passing the
  full post-`prepare_workspace` file list instead. Verified with a real
  before/after: the same 15 previously-failing `setup_repo` cases went
  from 0/15 to 4/15 passing under the identical model. Model-capability
  limits on the harder D4/D5 tasks remain (that part is not a bug), and
  section 23 also fixes a real, unrelated reliability bug found while
  investigating this: an unbounded Windows `taskkill` call that could
  hang a case's cleanup indefinitely past its configured timeout.
- **A full 232-case real-execution sweep** (aider + qwen3-coder:30b,
  Docker-sandboxed, no baseline drivers) was run in section 23 against
  every one of the D3/D4/D5 cases added by the corpus-expansion phases -
  the corpus's difficulty tiers are not just structurally valid, they are
  empirically solvable by a real local model/driver stack at a
  believable, non-trivial rate (72% at D3, collapsing at D4/D5 mostly
  because of the driver bug above, not the corpus).
- **A previously-undocumented test-suite bug was found and fixed**: roughly
  35 of `tests/test_optarena.py`'s ~55 `tempfile.mkdtemp()` call sites had
  no matching cleanup (some `tearDown()`s existed but only restored
  mocked module state, never the workspace itself). This, not production
  code, was the dominant source of leaked `optarena_*` temp directories -
  9,445 of them found this session (~642MB), the large majority prefixed
  `optarena_test_*`. Fixed with `self.addCleanup(shutil.rmtree, ...)` on
  every missing site; verified empirically (a full suite run went from
  leaking on nearly every one of ~55 workspaces to 2 residual `.git`-only
  directories from one test, not yet chased further). See section 23.
- **`test_check_command_pass_and_fail`'s flake, called "pre-existing and
  environmental" in every prior session's notes, is now actually fixed**,
  not just re-described. It used `sys.executable` (a host path) as a
  check_command binary without opting out of sandbox routing the way every
  sibling host-exec test in the same file already does, so on any machine
  with Docker installed it silently ran inside the Linux container instead
  of on the host. See section 23.
- **The 7 `repos/fastapi-tasktracker/` ruff findings, also called
  "pre-existing, deliberately untouched" in every prior session, are now
  resolved**: the 4 F821s are a genuine SQLAlchemy 3-way circular
  `Mapped["X"]` forward-ref pattern ruff can't statically resolve without
  SQLAlchemy-plugin awareness, fixed with a scoped `per-file-ignores` in
  `pyproject.toml` rather than touching the fixture's ORM logic; the 1
  F841 (unused test variable) was a genuine trivial fix. `ruff check .` is
  now fully clean repo-wide. See section 23.

### What's still open

- **22 pre-existing Rust/axum corpus-verification violations** (19
  "broken variant PASSED the oracle", 3 "reference solution FAILED the
  oracle") were surfaced by a full-corpus verify run in execution log
  section 12, most plausibly caused by the same `CARGO_TARGET_DIR`
  shared-build-cache bug root-caused and fixed for two specific cases in
  section 17. Not re-investigated case-by-case as of the last update.
- **Difficulty 4/5 volume** remains a small fraction of the corpus (111 of
  836, ~13%) next to difficulty 1-3's volume, by explicit design choice
  (each new fixture is expensive) rather than an oversight.
- **`test_kind` is unset on the large majority of cases** - untouched by
  any of the corpus-expansion phases; still an open item from Round 4's
  original C-4 finding.
- Round 1's genuinely-not-code-fixable items (§7 of that round) - PAT
  rotation, a live UI smoke test, agent-process containerization, org
  identity naming - are user/architecture/product decisions, not defects;
  see Round 1's own section 7 for the full list. Note that the VS Code UI
  driver referenced throughout Round 1 and Round 2 has since been removed
  from the codebase entirely (confirmed dead by Round 2's own review), so
  several of Round 1's UI-specific items are now moot rather than open.
- Round 2's documentation-completeness gaps (no FAQ/Troubleshooting page,
  no CONTRIBUTING.md, no CHANGELOG page on the docs site, no comparison
  page, `SECURITY.md` not linked from the docs site) were analysis-only
  recommendations; their resolution status was not re-verified as part of
  this consolidation.

### Current corpus size and composition

Re-verified live against `optarena/cases/*.json` for this consolidation
(not merely copied from prior text):

- **836 total cases**, spanning 18 languages.
- **Difficulty distribution: 1: 168, 2: 387, 3: 170, 4: 72, 5: 39.**
- **103 cases carry `disruptions`** (dynamic, mid-session environment
  changes) and **106 cases are multi-prompt** - both figures matching the
  execution log's own final tallies from sections 19 and 22.
- **Zero cases lack a discriminating failing variant** outside the
  task types that get one implicitly (`bug_fix`/`refactoring`/
  `performance`/`security`) - Round 4's C-3 confirmed fully closed by a
  direct scan of the live corpus.
- Every one of the 18 languages now has real, Docker-verified signal at
  every difficulty tier 1 through 5, and at least a handful of disruption
  cases - the qualitative gap ("N of 18 languages have zero data on this
  dimension") that motivated most of the corpus-expansion work is closed,
  even though raw volume at the harder tiers remains comparatively small.

---

## Round 1 (2026-07-20): Full Verification Audit

_Original file: `DEV_NOTES/AUDIT_VERIFICATION_2026-07-20.md`._

_Scope: every file in the `optarena` repo (500-case corpus, Python package, drivers,
ui-harness, dashboard, CI, Dockerfiles), plus the `website/` and `website-docs/`
repos, verified against `OptArena_Comprehensive_AI_Software_Security_Audit_2026-07-18.docx`
and `SECURITY_REMEDIATION_PLAN.md`. Three passes: a full verification review, then two
remediation passes applying every feasible fix.
Method: line-by-line read of all sources, scripted checks across the case corpus,
full unit-test runs (155/155 green), a docs-site production build, `npm audit`, ruff,
and doc-vs-code drift comparison._

**Bottom line.** Commit `9f4f32c` had already fixed the large majority of the audit -
all four release-blocker Criticals were correctly implemented with regression tests.
The first remediation pass closed the code- and docs-addressable gaps; the **second
pass (§6) closed the deeper items** the first had deferred: custom-case-pack isolation,
non-root sandbox opt-in, a cross-oracle conformance suite, real UI `--trials`,
digest-pinned base images + digest recording in manifests, an OS/Python CI matrix with
a ruff gate, a docs CI with the `serialize-javascript` high-severity advisory resolved
(now **0 high/critical**), and the CLI help cleanup. What still remains is genuinely
**not code-fixable from here** and is listed in §7: user actions (rotate the PAT),
true architecture programs (containerize the agent process itself), and content authoring
(the 104-case reference backlog).

### 1. Audit finding scorecard (final state)

Status: fixed in `9f4f32c` (marked done) / fixed in this remediation pass (marked done②) / decided/deferred by design (marked deferred) / still open (marked open).

| ID | Finding | Status | Where |
|---|---|---|---|
| C-01 | UI harness leaked hidden tests into the live agent workspace | done | `oracle.js` grades a private copy (separate dir + separate Docker mount); live workspace is never written to by anything test-related |
| C-02 | Docker absence silently fell back to host execution | done | Both oracles fail closed; `OPTARENA_NO_DOCKER=1` / `OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1` are the only opt-ins; 4 unit tests |
| C-03 | Agents inherited the full host environment | done + done② | CLI drivers: explicit allowlist (`subprocess_env`) in `9f4f32c`. **This pass:** the UI harness now gets the same allowlist (plus display/session vars) instead of a full `os.environ` copy (`vscode_ui.py`); extension seeds are now workspace-scoped - external reads/edits, browser, and MCP are no longer auto-approved (`extensions.js`); UI workspaces moved **outside the repo checkout** to the system temp dir (`paths.js`, `OPTARENA_UI_DIR` to override). Still open: the agent process itself is not containerized (open, §4) |
| C-04 | API keys persisted; `serve` exposed the repo root | done + done② | Redacted persistence + scoped localhost server in `9f4f32c`. **This pass:** `OPTARENA_API_KEY` env fallback so real keys stay off argv |
| C-05 | Open corpus + broad filesystem access ⇒ answers retrievable | done②(partial) / deferred | **This pass:** trust model stated explicitly (README "Trust model" section + new `SECURITY.md`): public cases are auditable regression fixtures, not a tamper-resistant benchmark; private packs via `--cases-dir`. UI workspace relocation + external-read lockdown closes the practical answer/secret-lookup path. Reference solutions still ship in the package by design (they power `verify-corpus`); whole-run-root mount in serial mode still open (§4) |
| H-01 | Case paths not contained to the workspace | done | Resolve + `is_relative_to` in both oracles; traversal/absolute tests. (JS check is lexical - noted in §4) |
| H-02 | Shared container across parallel cases + `kill -9 -1` | done | Shared sandbox skipped under `--parallel`; ephemeral per-call containers instead; unit-tested. `reap()` only ever fires serially |
| H-03 | Mutable images / unpinned actions | done(partial) | Actions pinned to SHAs; `:git-sha` image tags published alongside `:latest`. Still open: digest-pinned bases, digest recording in manifests (§4) |
| H-04 | Wheel omits runtime assets | deferred | Source-checkout-only, documented in `pyproject.toml` + README |
| H-05 | `--trials` kept only the last trial's telemetry | done | Summed across trials; 6 tests |
| H-06 | Empty case set exited green | done | Refused with exit 2; `--allow-empty` to override |
| H-07 | Missing cost presented as free and could win | done + done② | Python `_cheaper`/hostname parsing in `9f4f32c`. **This pass:** the dashboard - which still had the exact coerce-to-`$0`/"free" bug - now shows "-"/"no cost/token telemetry reported", only crowns "Cheaper" when *both* runs have real cost data, and suppresses all winner tiles for non-comparable manifests (`dashboard/index.html`) |
| H-08 | UI drivers ignored api_key; `/v1/v1` URLs | done | `API_KEY` forwarded; `openaiBase()` normalization |
| H-09 | Python and JS oracles diverged | done + done② | SHA-1 snapshots unified in `9f4f32c`. **This pass:** `diffStats` and `classifyFailure` ported to `oracle.js` and wired into `evaluateCase`, so UI runs now carry the same `diff`/`failure_class` fields as CLI runs; docs claims softened to "kept in sync by hand" with the private-copy caveat. Still open: a generated cross-language conformance fixture suite (§4) |
| H-10 | Corpus self-verification incomplete | open | Content work (Phase 4): 104 of 500 cases lack `reference_solution`, 42 are skipped by `verify-corpus`, 7 legacy cases untagged. Unchanged - it's an authoring backlog, not a code fix |
| H-11 | Timeouts orphaned process trees; temp dirs leaked | done | Process-group kill both languages; workspace cleanup + `--keep-workspace` |
| H-12 | Docs npm high-severity advisory | open | Needs `npm` upgrade work + docs CI in `website-docs` (§4) |
| M-01 | No schema validation | done + done② | `schema.py` in `9f4f32c`. **This pass:** standalone `optarena cases validate` command |
| M-02 | Non-zero agent exit could still grade PASS | done | `execution_ok` field, merged, printed, tested |
| M-03 | Comparison validity not enforced | done + done② | Manifest + gate + suppression + `--force` in `9f4f32c`. **This pass:** `optarena regression` now prints the same NOT-DIRECTLY-COMPARABLE warning (`compare.py`), and the dashboard gates its verdict tiles on manifest compatibility |
| M-04 | Run-id collisions; fixed results dir; ambiguous lookup | done + done② | UUID ids, collision refusal, `--results-dir`. **This pass:** a collision at save time no longer tracebacks after a paid run - the record is preserved to a temp file with a clean error (`cli.py`) |
| M-05 | Bare `python` assumed on host | done + done② | Corpus fixed (0 of 500) in `9f4f32c`. **This pass:** the test suite's own fixtures now use `sys.executable` - the audit's original 5-tests-fail-on-macOS failure mode is gone |
| M-06 | Coverage gaps / missing CI gates | done(partial) | 75→145 tests with per-finding regression tests. Still open: lint gate, OS matrix, JS tests, docs CI (§4) |
| M-07 | Docs stale; org identity split | done② | **This pass:** docs site caught up - 500-case counts, regenerated catalogue (now script-generated from case JSON: `website-docs/scripts/generate_catalogue.py`), `editUrl` fixed to the real `selfopt` repo, fail-closed behavior documented, full CLI reference rewritten. Marketing site was updated upstream (hero 500/18/10, reworked corpus section) before this pass. Org naming (selfopt origin vs trysti-labs links/GHCR) remains a product decision (§4) |
| M-08 | Privacy/metric certainty overstated | done② | Dashboard "free"/"local backend" labels replaced with explicit unknowns; Google Fonts removed (dashboard now makes zero external requests); docs "Everything is local" replaced with precise data-flow language |
| M-09 | UI drivers silently reduce `--trials` to 1 | open | Architecture work: per-case UI re-runs with fresh profiles (§4) |
| M-10 | Container least-privilege gaps | done(mostly) | cap-drop/no-new-privileges/pids-limit/read-only+tmpfs in `9f4f32c`. Deferred: non-root `--user` (documented in-code), per-case mounts (§4) |
| M-11 | Scenario paths resolved against CWD | done | Resolved against the scenario file's directory |
| L-01 | Lint debt; metric semantics | done②(partial) | **This pass:** zero durations no longer dropped from means (`metrics.py`); unused-import/`__all__` cleanup in `drivers/__init__.py`; unused `os` import removed from `crewai_sdk.py`. Still open: unified duration semantics across driver kinds, pricing-table provenance (§4) |
| L-02 | Docs delivery has no CI | open | Docs CI (build/link/audit) still absent in `website-docs` (§4) |

### 2. Additional findings from this review - and their fixes

| # | Finding | Status |
|---|---|---|
| V-1 | Real `GIT_PAT` in `.env` readable by UI agents (workspace inside the checkout + `readFilesExternally: true`) | done② mitigated: workspace moved to system temp, external reads/edits/MCP/browser no longer auto-approved. **Rotate the PAT** - it sat in reach of any UI agent run before today (§4) |
| V-2 | Dashboard: unknown cost → "free"/$0-wins-Cheaper; no manifest gating | done② fixed (see H-07/M-03 rows) |
| V-3 | UI harness process tree inherited the full host env | done② fixed (allowlist in `vscode_ui.py`) |
| V-4 | Keys on argv (`--api-key`, aider's `--openai-api-key`) | done② `OPTARENA_API_KEY` env fallback added and documented; aider's flag remains (its env alternative is aider-version-dependent) |
| V-5 | `crewai` driver mutated `os.environ` with the scenario key | done② removed; key passed only via `LLM(api_key=...)` |
| V-6 | Supply-chain residue (mutable `:latest` default, unpinned bases) | open (§4) |
| V-7 | Audit docx + internal strategy docs committed to the (public) repo | open, decision for the owner (§4) |
| B-1 | `regression` bypassed the comparability warning | done② `format_regression` now prints it |
| B-2 | `rebuild_index` crashed on foreign JSON in `runs/` | done② skipped like corrupt files |
| B-3 | Zero durations dropped from aggregate means | done② `is not None` filter |
| B-4 | `failure_class: "timeout"` effectively unreachable | done② explicit `timed_out` marker set on every timeout path (both oracles); `classify_failure`/`classifyFailure` check it and exit-code 124 first |
| B-5 | `oracle.js` reported `ran: true` for spawn-level failures | done② ENOENT now reports "could not run", `ran` stays false; outer-kill marked `timed_out` |
| B-6 | macOS Docker temp-mount edge on the ephemeral verify path | open, noted; the shared-sandbox path (the normal one) is unaffected |
| B-7 | `_merge_trials` keeps only the last trial's `files` | open, cosmetic; left as-is |
| B-8 | Tests used bare `python` | done② `sys.executable` |
| B-9 | UI vs CLI duration semantics differ | open (§4, with M-09) |
| B-10 | `save_run` collision → raw traceback after a paid run | done② caught; record preserved to a temp file |
| B-11 | F401 lint debt | done② cleaned |

Stale docs/comments fixed this pass: README host-fallback claims (2 places),
ARCH.md `serve`-rooted-at-repo + oracles-"identical" sections, `cases.py`
`diff_stats` size:mtime docstring, `_HARDENING_ARGS` "396" count, `cmd_doctor`
fallback comment, `oracle.js` header (referenced a deleted `workspace.py`),
`vscode_ui.py` header (now lists Kilo). New: `SECURITY.md` (reporting channel,
trust boundaries, credential handling, data flow); `.claude/` added to
`.gitignore`; `AUDIT_VERIFICATION` = this file.

### 3. CLI reorganization (shipped this pass)

Commands are now grouped by noun, with every pre-existing spelling kept as a
working legacy alias (zero script/CI breakage - the full 145-test suite,
which exercises the old spellings, still passes):

```
optarena run | compare [--force] | regression | doctor | serve     (unchanged)
optarena cases   list | show <name> | init [dir] | validate | verify
optarena runs    list | show <run_ref>
optarena drivers list
optarena sandbox build | pull [--lang X | --all] | status
```

New capabilities beyond the regrouping: `cases show` (print one case's
prompts/oracle/metadata), `cases validate` (standalone schema check),
`runs show` (summary + per-case table), `sandbox status` (scriptable
daemon/image report), `OPTARENA_API_KEY`. The docs-site CLI reference was
rewritten to match, including a full environment-variable table.

### 4. New CLI capabilities

New beyond the regrouping: `cases show` (print one case's prompts/oracle/
metadata), `cases validate` (standalone schema check), `runs show` (summary +
per-case table), `sandbox status` (scriptable daemon/image report),
`OPTARENA_API_KEY`, `OPTARENA_SANDBOX_USER`, `OPTARENA_UI_DIR`. Legacy
spellings (`list …`, `init`, `verify-corpus`, `docker …`) are rewritten to the
grouped commands before parsing (`_rewrite_legacy_argv`, unit-tested), so
`--help` shows only the clean canonical set while old scripts keep working.

### 5. Second remediation pass - deeper items closed

Everything the first pass deferred to "still open" that was code-fixable has
now been done and verified:

| Item | Fix | Verified by |
|---|---|---|
| **C-05 custom-pack isolation** | A `cases_dir` (untrusted) pack never uses the shared whole-run-root container; each case gets its own ephemeral container mounting only its own workspace | `test_no_shared_sandbox_for_custom_case_pack` + `test_sandbox_still_started_for_builtin_corpus_serial` |
| **M-10 non-root sandbox** | `OPTARENA_SANDBOX_USER=uid:gid` runs both oracles' containers as that user with HOME on the writable tmpfs (opt-in, documented rationale) | code review; mirrored Python/JS |
| **H-03 base-image digests** | All 9 Dockerfile `FROM`s pinned by multi-arch manifest-list digest (fetched live), tag kept for readability | `grep FROM docker/**` |
| **H-03 manifest digests** | Run manifest records `image_digests` (best-effort `docker image inspect` RepoDigest), for evidence/repro - deliberately not a comparability gate | `_image_digests` |
| **H-09 conformance suite** | `tests/test_conformance.py` drives the JS oracle over the same workspaces as the Python one (snapshot incl. Unicode/nested/ignore-dirs, same-size rewrite, glob/regex/min_lines, containment) and asserts equality | 4 tests pass with Node present |
| **H-01 JS symlink parity** | `writeSetupFiles` now realpath-checks physical containment after mkdir, matching Python's symlink-resolving `resolve()` | conformance containment test |
| **M-09 UI `--trials`** | Runner hands the trial count to the caching UI driver; the harness repeats each case N times (fresh workspace) emitting one line per trial; the driver majority-merges via the shared `_merge_trials` | `test_trials_handed_to_caching_driver_not_dropped` |
| **B-9 UI duration** | The harness now reports agent-work time (first prompt → last file activity), excluding idle-poll and oracle grading | code review (UI runtime unverified, see §7) |
| **B-7 trial files** | `_merge_trials` unions files across trials instead of keeping the last trial's | code review |
| **M-06/L-01 lint** | ruff gate added to CI; both F401s fixed; clean | `ruff check` |
| **M-06 CI matrix** | ubuntu/macos/windows × py3.10/3.11/3.12, **no `python` shim** (the audit's original failure class), Node present so conformance runs | `.github/workflows/ci.yml` |
| **H-12 docs advisory** | `serialize-javascript` overridden to 7.0.7 → **0 high/critical** (was 1 high); site still builds | `npm audit --omit=dev` |
| **L-02 docs CI** | New `website-docs/.github/workflows/ci.yml`: build (throws on broken links) + `npm audit --audit-level=high` + catalogue-drift check | build succeeds locally |
| **M-07 catalogue** | Now generated from the corpus (`scripts/generate_catalogue.py`), MDX-safe escaping, 500 cases; drift-gated in CI | docs build |
| **CLI help** | Legacy aliases removed from `--help` via argv rewrite | `optarena --help` |
| Action pins | All 4 GitHub Action SHAs cross-checked against their real tag SHAs | GitHub API |

### 6. Verification

- **155/155** unit tests pass (`OPTARENA_NO_DOCKER=1`), including the new
  conformance, UI-trials, CLI-rewrite, and custom-pack-isolation tests.
- `ruff check optarena tests`: clean. `node --check`: clean on all JS.
- Docs site: `npm run build` **succeeds** (0 high/critical advisories,
  0 broken internal links); catalogue regenerates byte-identically.
- New CLI verified end-to-end against the 500-case corpus; all legacy
  spellings rewrite correctly (incl. with a leading `--results-dir`).
- Corpus is exactly the 500 committed cases (7 local WIP duplicates of
  remotely-completed work discarded per instruction).
- Trysti Labs link corrected from `labs.trysti.com` to `trysti.com/labs`
  across all three repos (marketing site, docs site, README).

### 7. What genuinely remains (not code-fixable from here)

These are the honest pre-audit disclosures - none is a defect left unaddressed
for lack of effort; each needs a person, a runtime, or a program:

1. **Rotate the `.env` `GIT_PAT`** - *user action, do before audit.* It was
   reachable by UI agents before the workspace/permission lockdown; treat any
   secret an auto-approving agent could once read as exposed.
2. **Live UI smoke test** - *runtime not available at audit time.* The UI-harness changes
   (permissions, env allowlist, relocated workspace, `--trials` loop,
   agent-only duration) are syntax- and logic-verified but not driven through
   a real VS Code session. Do one `cline-ui` run before the audit; the risk is
   a missing allowlisted env var or an approval that's now manual.
3. **Agent-process containerization (C-03 endgame)** - *architecture program.*
   CLI agents still run as your user (env-allowlisted, but not in their own
   container separate from the verifier). This is a design effort, not a patch.
4. **Corpus reference backlog (H-10)** - *content authoring.* 104 of 500 cases
   lack a `reference_solution`; 42 are skipped by `verify-corpus`. Author them
   (42 zero-variant first), then flip the CI skip-count gate to zero.
5. **Org identity (M-07 residue)** - *product decision.* Origin is `selfopt`
   while README/GHCR/marketing link `trysti-labs`; pick one canonical.
6. **Publishing the audit + strategy docs (V-7)** - *owner call.* The audit,
   this report, and the corpus-moat strategy sit at the repo root; if the repo
   is public, decide deliberately whether they belong there.
7. **Nothing was committed as of this round.** All changes across both repos were staged in
   working trees for review at the time; commit/push was left to the repo owner.

---

## Round 2 (2026-07-25): Code + Docs + Website Consistency Audit

_Original file: `DEV_NOTES/AUDIT_CODE_AND_DOCS_2026-07-25.md`._

Scope: `optarena/` (branch `v0.1`), `website-docs/` (Docusaurus, docs.optarena.com), cross-checked
against `website2/` (optarena.com marketing site) and `README.md`/`ARCH.md` for consistency. Six
questions were asked: unused/stale code, wording/story consistency, docs-vs-code accuracy, security
(code + docs site), docs-site styling, and docs completeness vs comparable tools. Findings below are
organized by question, each with file:line references and a confidence/severity level. Nothing in
this document had been acted on at the time it was written - it was analysis only.

**Read this first - the one finding that should probably become a real fix, not just a note:**
§2 HIGH, `openai-agents` driver. It silently ships prompts and model output to `api.openai.com` on
every run, including runs explicitly configured against a fully local Ollama backend, if the host
happens to have `OPENAI_API_KEY` set for unrelated reasons. That directly contradicts the project's
own "local-first" claim and is a one-line fix.

### 1. Unused / stale code

The codebase post-refactor was unusually clean at the time: **zero unused imports** (`ruff check --select
F401,F811,F841` passes clean - this is the same rule CI's lint job runs), **zero orphaned `.py`
files**, **zero tracked build artifacts**. The real findings cluster into three groups.

#### 1a. Stale prose from the VS Code-driver era (confirmed dead, low effort to fix)

| File:Line | Text |
|---|---|
| `optarena/__init__.py:4` | Package docstring still says *"...real coding tools (Cline's actual VS Code UI, aider's CLI, raw chat baselines, SDK agents)..."* - the single highest-visibility file in the package (`help(optarena)`). |
| `optarena/drivers/base.py:8-9` | Module docstring example: *"...prepare()/teardown() (e.g. the VS Code UI driver keeps one editor session alive...)"*. |
| `optarena/drivers/base.py:118` | Comment on `caches_results` names "the VS Code UI driver" as the example - see 1b, the flag itself is now an orphaned mechanism. |
| `optarena/runner.py:12,14,338` | Three separate module/inline comments referencing "the VS Code UI drivers"/"the VS Code UI harness". |
| `optarena/cases.py:220` | `IGNORE_DIRS` still includes `.cline` (no current driver ever creates this dir; `.vscode` is arguably still legitimate since a `setup_repo` starter repo could contain one). |
| `dashboard/index.html:179` | Footer example command: `optarena run --driver cline-ui --name my-run` - not a valid driver name anymore, would error if copy-pasted. |
| `dashboard/index.html:402` | JS comment: *"UI drivers cache results and record none"* - stale terminology, doesn't affect the (still-correct) rendering logic. |

Not stale, kept intentionally: `optarena/drivers/__init__.py:26-29`'s comment explaining that
IDE/UI automation is deliberately out of scope for `v0.1` and still lives on `main` - this is
accurate and should stay.

#### 1b. One genuinely vestigial runtime mechanism

`Driver.caches_results` (`optarena/drivers/base.py:119`, default `False`) and its consumer in
`optarena/runner.py:337-347` exist purely to support a driver that runs its whole case set once
inside `prepare()` and serves cached per-case results from `run_case()` - a pattern only the
now-deleted VS Code UI driver ever used. No current driver (not `aider`, not any of the 6 CLI
drivers, not any of the 6 SDK drivers) sets `caches_results = True`. Consequently:

- `driver.trials = trials` (`runner.py:344`) sets an attribute no current `Driver` subclass ever reads.
- The explanatory print at `runner.py:345-346` is currently unreachable in real usage.
- It's still exercised by `tests/test_optarena.py:532-556` (`UITrialsPlumbingTests`) via a
  `mock.Mock(caches_results=True, ...)` - the test's own docstring literally says "a caching driver
  (VS Code UI) can't be repeated..."

This isn't dead code to delete (a future driver with expensive per-scenario startup could
legitimately use it - it's documented as exactly this in ARCH.md's driver-lifecycle section), but
right now every comment and test describing it points at a driver that no longer exists. Worth a
pass to reword the explanation around a hypothetical/generic driver instead of "VS Code UI."

Also minor: `optarena/drivers/__init__.py`'s docstring lists the `kind` taxonomy as `ui | cli | sdk
| baseline` - no registry entry uses `"ui"` anymore.

#### 1c. Documentation that describes code that no longer exists (the most actionable finding)

`DEV_NOTES/MANUAL_TESTING.md` is a **living QA checklist**, not a historical snapshot, which makes
its staleness higher-priority than 1a/1b:

- **Line 24**: prerequisites still say "For VS Code UI driver tests (section 12): Node >= 18, `cd
  ui-harness && npm install`..." - references a deleted directory.
- **Lines 890-948, entire "## 12. VS Code UI Drivers" section** (~60 lines, 4 subsections) - every
  procedure references `cline-ui`/`roo-ui`/`continue-ui`/`kilo-ui` or `ui-harness/src/extensions.js`.
  100% untestable now.
- **Lines 129-141, "### 2.2 Doctor detects a stuck VS Code updater (Windows)"** - documents an
  `optarena doctor` check that's confirmed **gone** from `cli.py`'s `cmd_doctor` (grepped for
  `CodeSetup`/`stuck.*updater` - zero matches).
- **Line 56**: "Exactly 13 rows" in the driver registry - both the row count (actual is 14) and the
  listed driver names (still lists the 4 removed UI drivers, omits all 5 new non-crewai SDK drivers)
  are wrong.
- **"## 14. SDK Driver (crewAI)"** only covers manual test steps for `crewai` - never extended to
  the 5 SDK drivers added in the same branch (`openai-agents`, `smolagents`, `langgraph`, `autogen`,
  `semantic-kernel`). A coverage gap in the opposite direction from the rest of this section.

`DEV_NOTES/SECURITY_REMEDIATION_PLAN.md` also deserves a flag: its own header says *"Not yet
executed - this is a proposal to review before any of it is implemented"*, but subsequent commits
(`9f4f32c`, `d14d8df`) and `AUDIT_VERIFICATION_2026-07-20.md` (Round 1, above) show most/all of it *was* executed,
and it names `drivers/vscode_ui.py`/`ui-harness/src/extensions.js` as fix targets - both now
deleted. Someone reading only this file would think a security plan is still pending. Recommend a
one-line "superseded by Round 1 above" banner at the top.

The other 13 files in `DEV_NOTES/` read as clearly historical/closed and are fine to leave as-is.
`WEBSITE.md` and `OptArena_Website_v2.md` cover overlapping territory (which one is "current" isn't
obvious from the filenames alone) - not urgent, just worth a note next time either is touched.

#### 1d. One test-coverage inconsistency worth a decision, not a bug

`tests/test_optarena.py:1096-1104`, `RegistryTests.test_all_registered_drivers_instantiate` skips
only `"crewai"` when iterating the driver registry (`if name == "crewai": continue  # optional
dependency`), but the other 5 optional SDK drivers are **not** skipped. Verified none of the 6 SDK
driver modules import their SDK package at module scope (all imports are lazy, inside `prepare()`),
so `get_driver()`/instantiation should succeed regardless of whether the package is installed -
meaning either the `crewai` skip is itself now-unnecessary leftover caution, or the other 5 are
missing a skip they should have for symmetry. Worth a quick look, not urgent.

#### 1e. Local-only hygiene (not a git issue)

A `legacy/` directory with compiled `.pyc` caches still physically existed at
`c:\ai\selfopt\optarena\legacy\` on disk at audit time. Confirmed via `git ls-files legacy` (empty) and `git log
--diff-filter=D -- legacy` that this was fully removed from git tracking in `1ea3395` - it's purely
a leftover local checkout artifact, safe to `rm -rf legacy/` whenever convenient.

### 2. Security audit

Scope: `subprocess_env()`, all 9 driver files (6 new SDK drivers + `cli_agents.py` + `aider_cli.py`
+ `openai_chat.py`), `cases.py`'s `DockerSandbox`/path-containment, `scenario.py`/`schema.py`'s new
`num_ctx` field, `cli.py`'s `cmd_serve`, `packs.py`, `.github/workflows/*.yml`, `docker/*/Dockerfile`,
and `website-docs/`. Cross-checked against `DEV_NOTES/SECURITY_REMEDIATION_PLAN.md` and Round 1's
audit-verification document so already-fixed items aren't re-reported. **The prior
remediation pass was genuinely thorough** - `DockerSandbox` hardening (`--cap-drop ALL`,
`--read-only`, `--pids-limit`, `--network none`, digest-pinned base images), `subprocess_env()`'s
allowlist, path-containment checks, `cmd_serve`'s scoped handler, and `Backend.redacted_dict()` all
check out with no bypass found, including from the 6 new SDK drivers. All 9 driver subprocess calls
use list-form argv - no command injection anywhere in the driver layer.

The new findings below are almost entirely in **the SDK drivers' interaction with their own
frameworks' hidden telemetry systems** - a leak class `subprocess_env()` structurally cannot
address, because these drivers run in-process rather than spawning a subprocess.

#### HIGH - `openai-agents` driver leaks prompts/output to OpenAI via the host's ambient API key, on every run, regardless of backend

**File:** `optarena/drivers/openai_agents_sdk.py:53-75`

The driver correctly scopes its `AsyncOpenAI` client to the *scenario's* backend/key (avoiding
key-shadowing, per its own comment). What it misses: the OpenAI Agents SDK has **tracing enabled by
default**, as a separate code path from the client object passed to `Runner`. Verified directly
against the installed package (`openai-agents==0.18.3`):

- `agents.run.RunConfig.tracing_disabled` defaults to `False`; the driver never passes a
  `run_config` to `Runner.run_sync(...)` (line 71).
- `agents.tracing.processors.BackendSpanExporter.api_key` reads `os.environ.get("OPENAI_API_KEY")`
  - the **host's ambient env var**, not the `backend.api_key` the driver built its client with.
- `GenerationSpanData` carries the full input/output of every model call - the real prompt
  (including any file content injected via `context`) and the real completion.
- The export target, `_OPENAI_TRACING_INGEST_ENDPOINT`, is `https://api.openai.com/v1/traces/ingest`
  - a live, real endpoint.

**Concretely:** run `optarena run --driver openai-agents --base-url http://localhost:11434 ...` to
benchmark a fully local Ollama model. If `OPENAI_API_KEY` happens to be set in the shell (common -
many developers export it for unrelated use of the real API), every prompt and completion from that
"local-only" run is silently POSTed to `api.openai.com` under the developer's real account. Nothing
in the driver, CLI output, or docs (`concepts/drivers.md` makes no privacy claim about this driver
either way) indicates this happens. Directly contradicts the "local-first" framing in
`pyproject.toml`/`optarena/__init__.py`. If the case content includes proprietary source (an L3
`setup_repo` case, or anything from a custom `--cases-dir`), that source leaves the machine.

This file was added in `1ea3395`, after the prior remediation passes - genuinely new, not a
re-report.

**Fix:** `RunConfig(tracing_disabled=True)` on every `Runner.run_sync` call (or
`agents.set_tracing_disabled(True)` once in `prepare()`), matching what the driver already does
correctly for the client/key.

**Note added during consolidation:** this driver's code path was substantially rewritten by the
execution log's section 12 work (new `drivers/sdk_base.py`, in response to Round 4's B-3) - see
Round 4 and the Execution Log below for what shipped there.

#### MEDIUM - `crewai` driver leaves crewAI's own telemetry on, phoning home on every run

**File:** `optarena/drivers/crewai_sdk.py:37-57`

crewAI initializes an OTLP exporter to `https://telemetry.crewai.com:4319` unless
`OTEL_SDK_DISABLED`/`CREWAI_DISABLE_TELEMETRY`/`CREWAI_DISABLE_TRACKING` is set - none of which this
driver sets at the time of this audit. crewAI's own docs claim this is metadata/usage-only, not prompt/response content, so
it's not the same class of leak as the finding above - but it's still a silent third-party network
call on every run, including fully local/offline backends, inconsistent with "local-first."

**Fix:** set `CREWAI_DISABLE_TELEMETRY=true` in `prepare()`.

#### LOW/INFO - `langgraph` driver inherits ambient LangSmith tracing if the host has it configured

**File:** `optarena/drivers/langgraph_sdk.py:59-64`

LangSmith tracing is opt-in at the LangChain level (off by default, unlike the `openai-agents`
case), so this isn't a driver-introduced leak - but a developer who already has LangSmith tracing on
globally for other LangChain work will silently have every OptArena prompt/response traced there
too. Worth an explicit override in `prepare()` for defense-in-depth, same reasoning as the crewai fix.

#### MEDIUM - `opencode` driver writes the backend API key to a temp file that's never deleted

**File:** `optarena/drivers/cli_agents.py:96-118` (`_opencode_env`)

```python
fd, path = tempfile.mkstemp(prefix="optarena-opencode-config-", suffix=".json")
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(config, f)
return {"OPENCODE_CONFIG": path}
```

`config[...]["apiKey"] = backend.api_key or "optarena"` - a real credential whenever the scenario
targets a real hosted endpoint (a proxy, a paid router), not just the local placeholder. Called once
per `run_case`/trial. Nothing ever unlinks the file - no `finally`, no cleanup in
`CLIAgentDriver.run_case`/`teardown()`. Every `opencode` run leaves one more
`optarena-opencode-config-*.json` with a plaintext key permanently in the OS temp dir, accumulating
across runs and surviving into any backup/forensic image of that directory.
`tempfile.mkstemp` does create the file owner-only on POSIX, so it's not readable by *other* local
users while it exists - the defect is the indefinite persistence with no cleanup, the same class of
thing already correctly handled elsewhere (`evaluate_case_isolated`'s temp dirs, the Docker sandbox
containers) via `try/finally` + `shutil.rmtree`/teardown.

Added in `e2f4e9c`, after the last remediation pass - genuinely missed, not a re-report.

**Fix:** wrap the opencode invocation in `try/finally` and unlink the config path, mirroring
`evaluate_case_isolated`'s pattern.

#### Already tracked, confirmed still open (not new)

Round 1's **V-4** ("keys on argv") already documents that
`aider_cli.py:106-114` still passes `backend.api_key` as a literal CLI argument, visible via
`ps`/`/proc/<pid>/cmdline`/Task Manager to any other local user on a shared host while the process
runs. Confirmed still present, still open, already tracked - not re-reporting as new. (Round 4,
below, independently reports the same gap as its own S-2 and records it fixed.)

#### LOW - sandbox images install most packages with no version pins

**Files:** `docker/python/Dockerfile:20-33`, `docker/node/Dockerfile:22-35`

Only `typescript@5.6.3` and the separate `pydantic==1.10.14` venv are pinned; everything else
(fastapi, express, react, jest, etc.) floats to whatever's current at image-build time. The base OS
images are digest-pinned specifically because "a floating tag can be silently replaced upstream,
changing oracle behavior between identical commits" (the Dockerfiles' own comment) - the same
reasoning applies here but wasn't carried through. Reproducibility/supply-chain-drift risk, not an
active exploit (generated code still can't reach the network at check-command time).

#### LOW - Composer/PHPUnit fetched via `curl | execute` with no integrity check

**File:** `docker/php/Dockerfile:21-29`

No SHA-384 verification of the Composer installer (which Composer's own docs recommend specifically
to guard against a compromised/MITM'd download), no checksum on the PHPUnit phar. Build-time only,
not reachable by generated code, but worth pinning to a verified hash given everything else in this
layer is already digest-pinned.

#### website-docs - informational only

- `npm audit --omit=dev`: 8 unresolved advisories (1 low, 3 moderate, 4 high - `brace-expansion`,
  `fast-uri`, `postcss`, `svgo`, transitively via webpack-dev-server/postcss/svgo), down from 21 the
  prior pass flagged. All build-time-only (not shipped into the static `build/` output); the
  previously-flagged high-severity `serialize-javascript` is confirmed fixed via the `overrides`
  block. `npm audit fix`/a Docusaurus minor bump should close most of these.
- No CSP or security-headers file anywhere (`_headers`/`netlify.toml`/`vercel.json` all absent) -
  whatever headers `docs.optarena.com` has are entirely the hosting platform's defaults. Low
  practical risk for a static site with no forms/dynamic content, but worth an explicit config as
  defense-in-depth, same as the `_headers` file already added to `website2` this session.
- `scripts/generate_catalogue.py` correctly escapes MDX-breaking characters (`{`, `}`, `<`, `>`,
  pipe) when writing case descriptions into generated Markdown - but not backticks. Rendering
  glitch at worst today (cases are first-party), would matter if the corpus ever accepts external
  contributions.
- `src/` contains only `src/css/custom.css` - no custom components, no `dangerouslySetInnerHTML`, no
  `postMessage`/query-string handling. Minimal attack surface by construction.

### 3. Wording/story consistency (docs vs. website vs. code)

**Strong alignment, no fixes needed.** All three surfaces were rewritten in the same pass this
session and share one voice:

- Tagline: *"Know when your AI agents actually get better."* - identical, verbatim, in
  `README.md:3`, `website-docs/docs/intro.md:10`, and `website2`'s `Hero.jsx`/`Footer.jsx`/
  `docusaurus.config.js` tagline field.
- Core positioning paragraph (driver categories, Docker verification, "not keyword-matching")
  matches near-verbatim across `README.md:10-17` and `docs/intro.md:12`.
- Driver table in `docs/concepts/drivers.md` matches the live `optarena drivers list` output
  exactly - 14 rows, correct kind/backend/status for every one (re-verified directly against the
  registry as part of this audit, see §4).

Nothing to fix here; noted as a genuine strength, not a gap.

### 4. Docs-vs-code accuracy

Re-verified directly against the running tool as part of this audit (not just re-reading prior
work): `optarena drivers list` output cross-checked line-by-line against
`docs/concepts/drivers.md`'s table - exact match, including the two most recently added things
(`--num-ctx` flag and `OLLAMA_CONTEXT_LENGTH` env var, both correctly documented in
`docs/reference/cli.md:39,210` and `docs/getting-started/scenarios.md:37`, confirmed already present
from earlier in this session rather than a gap).

No drift found. The `opencode`/`qwen-code` internal bug fixes from earlier this session
(`SystemDrive` allowlist gap, opencode's custom-provider-config mechanism) are implementation
details with no user-facing flag/behavior change, so they correctly don't need a docs update.

### 5. Docs website styling recommendations

The site already has real custom theming (`src/css/custom.css`, 169 lines) matching `website2`'s
violet accent (`#7C5CFC`), Inter/JetBrains Mono font stack, and dark-canvas colors (`#08080C`) rather
than Docusaurus's default flat grey - this is a deliberately maintained, consistent design system,
not an oversight. Two concrete gaps worth addressing:

1. **No search.** `docusaurus.config.js`: `algolia: undefined, // add when ready`. This is close to
   a table-stakes feature for any documentation site with more than a handful of pages (this one has
   11 + a generated case catalogue covering 510 cases). Algolia DocSearch is free for qualifying
   open-source projects; `@easyops-cn/docusaurus-search-local` is a same-day-viable alternative that
   needs no external service/application if Algolia's approval process is a blocker.
2. **No distinct landing page.** There's no `src/pages/index.js` - `routeBasePath: '/'` plus
   `intro.md`'s `slug: /` means `docs.optarena.com/` renders as a plain Introduction doc page (sidebar
   + content + right-rail TOC), identical chrome to every other page. Compare to Vitest, Playwright,
   Prisma, tRPC docs - all of these have *some* kind of landing moment (even a simple one: a short
   hero + 3-4 nav cards into Getting Started/Concepts/Reference) before dropping into prose. Worth a
   lightweight custom `src/pages/index.js` that reuses the same design tokens already in
   `custom.css`, rather than relying on the first doc page to also do a homepage's job.

Minor, lower priority: `prism.additionalLanguages` only lists `['bash', 'python', 'json']` - any
future doc page showing example output/config in Go, Rust, YAML, etc. (the corpus spans 18
languages) would render unhighlighted. Cheap to extend if/when needed.

### 6. Docs completeness vs. comparable tools

Checked for sections that are close to standard on mature CLI/eval-tool documentation sites
(Playwright, Vitest, pytest, tRPC, Prisma-style docs) and confirmed present/absent here:

**Present and solid:** Getting Started (install/quickstart/scenarios), Concepts (cases-and-oracle,
drivers, metrics-and-comparison, results-and-dashboard), CLI Reference, an auto-generated Case
Catalogue that can't drift from the real corpus (CI-gated). The "Adding a driver"/"Adding a case"
sections inside Concepts pages cover the extensibility-docs role most tools give a dedicated page.

**Missing, worth considering:**

- **No FAQ or Troubleshooting page.** ARCH.md's own "Cross-platform & environment notes (hard-won)"
  section (Windows specifics, the `subprocess_env` case-sensitivity bug, etc.) is real, hard-won
  operational knowledge that currently lives only in the internal architecture doc, not anywhere a
  user hitting the same issue would find it.
- **No CONTRIBUTING.md** anywhere in the repo, and nothing in the docs site pointing a would-be
  contributor anywhere (no "Contributing" nav item, no repo file).
- **No CHANGELOG.** With `blog: false` (a reasonable choice) there's currently no version-history
  page of any kind - not urgent pre-PyPI-publish, but worth planning for before a 0.1.0/1.0 release.
- **No comparison page.** `docs/intro.md`'s "What it is not" section briefly contrasts with
  SWE-bench, but there's no dedicated "OptArena vs. X" page of the kind most eval/benchmark tools
  ship (helps both SEO and a skeptical first-time reader decide quickly whether this is the right
  tool).
- **`SECURITY.md` isn't linked from the docs site anywhere.** Confirmed via grep - "security" only
  appears in the docs as a task-category name, never as a link to the actual trust-model writeup.
  Given the corpus's own trust model (public reference solutions, adversarial-benchmarking caveat)
  is genuinely important context for anyone deciding how much to trust a result, this is worth a
  footer link or an explicit page, not just a repo-root file nobody visiting the docs site would
  find.
- **No community/support channel** beyond GitHub Issues (no Discussions, Discord, etc.) - normal for
  a project at this stage, not flagging as a real gap, just noting it wasn't found.

Given the pre-PyPI-publish stage this project was at, the highest-leverage additions from this list
are probably the Troubleshooting page (real content already exists in ARCH.md, just needs
surfacing) and linking `SECURITY.md` - both are "write nothing new, just expose what already
exists" fixes rather than net-new content.

---

## Round 3 (2026-07-26): Reliability/Performance/Docker/Packaging Audit (F-01..F-18)

_Original file: `AUDIT.md` (the pre-consolidation top-level audit)._

_Audit date: 2026-07-26_

### Scope

This section records the repository architecture review and the findings for:

- reliability and failure recovery;
- performance and scalability;
- Docker and Podman behavior;
- build reproducibility;
- packaging and distribution;
- testing and CI;
- maintainability and operational visibility.

The cybersecurity and API-key assessment was intentionally deferred at the
request of the project owner at the time. Security-specific findings are not included in
this round (see Round 4 below for the dedicated security audit).

### Executive summary

OptArena has a strong core design for a local-first coding-agent evaluation
tool. Its best qualities are the driver abstraction, real behavioral oracle,
large self-verifying case corpus, run manifests, comparison-validity checks,
and dependency-free Python core.

The primary work needed for production robustness is not a redesign. It is
hardening the lifecycle around the existing architecture:

1. Preserve partial results and always clean up resources after failures.
2. Bound subprocess and HTTP output so one case cannot exhaust memory.
3. Return clean CLI errors for expected user and infrastructure problems.
4. Replace per-check container startup under parallelism with worker pools.
5. Make the result store safe for concurrent and long-running usage.
6. Make sandbox images reproducible and multi-architecture.
7. Package the dashboard, Docker contexts, and starter repositories so normal
   wheel installations contain the complete application.

### Reviewer summary (added after independent verification against the code)

**Every one of the 18 findings (F-01 through F-18) is confirmed real** -
verified either by direct code reading with exact line references, or (for
F-01, the highest-severity one) by a live, controlled reproduction that
confirmed both an uncaught traceback and a leaked temp directory on a
missing-CLI-binary failure, the most ordinary failure mode this tool has.
None are false alarms. The corpus/size/prompt-count numbers in "Performance
observations" were independently re-derived and match exactly (510 cases,
2.42-2.43 MiB, prompt distribution `{1: 501, 2: 7, 3: 2}`).

Two things worth adding that the audit doesn't say explicitly:

- **F-01 and F-04 share one root cause** (`cmd_run` only catches
  `ValueError` around `run_scenario()`; `run_scenario()` doesn't wrap
  `driver.prepare()`/`sandbox.start()` in the cleanup `try/finally` at all)
  - fixing the `try/finally` placement mostly fixes both at once.
- **F-06 and F-13's Docker/Podman framing predates real Podman support**
  landing in this codebase (this session). Docker and Podman are both
  installed and confirmed working here now, so the container-specific
  findings are testable and actionable today, not blocked on tooling like
  they were when the audit ran.

Per-finding detail, with exact code references and what (if anything) each
claim gets slightly wrong, is inline below each finding as a **"Review
(verified against code)"** block. Nothing in this repository was
changed as part of this review at the time it was written - it was analysis only, at the requester's
explicit instruction (the "Fixes" blocks below record what was implemented afterward).

### Architecture overview

```text
CLI flags / scenario JSON
            |
            v
Scenario and case validation
            |
            v
Driver registry and driver implementation
  |             |                 |
  |             |                 +-- optional in-process SDK drivers
  |             +-- host CLI-agent drivers
  +-- raw OpenAI/Ollama baseline drivers
            |
            v
Per-case temporary workspace
            |
            v
Filesystem checks + behavioral check_command
            |
            v
Docker/Podman language sandbox
            |
            v
RunRecord -> JSON store -> compare/report/dashboard
```

Important components:

- `optarena/cli.py`: CLI parsing and command dispatch.
- `optarena/scenario.py`: scenario and backend models.
- `optarena/schema.py`: dependency-free validation.
- `optarena/drivers/`: tool and model integrations.
- `optarena/cases.py`: workspace preparation, oracle, and
  container execution.
- `optarena/runner.py`: scenario and trial lifecycle.
- `optarena/metrics.py`: aggregate metrics.
- `optarena/store.py`: filesystem result persistence.
- `optarena/compare.py`: A/B validity and comparison.
- `optarena/packs.py`: versioned case-pack handling.
- `dashboard/index.html`: static local dashboard.
- `docker/`: offline sandbox images for supported language tracks.

### What is already working well

- The Python core has no mandatory third-party runtime dependencies.
- Driver implementations share one `Driver -> CaseResult` contract.
- Optional SDK dependencies are imported lazily.
- The 510-case corpus is structurally validated.
- Reference and broken solutions provide corpus self-verification.
- Behavioral checks compile and execute generated code rather than relying
  only on content matching.
- Run manifests identify the resolved case set, oracle version, trial count,
  backend, driver, and container images.
- Invalid A/B comparisons suppress aggregate winner claims.
- Serial runs reuse one sandbox container per required language image.
- Docker and Podman use the same container-engine abstraction.
- Base container images are digest-pinned.
- The unit suite covers Windows, macOS, Linux, and Python 3.10-3.12 in CI.

### Prioritized findings

#### F-01: Driver preparation is outside the cleanup lifecycle

**Priority:** High
**Area:** Reliability and cleanup
**Location:** `optarena/runner.py`, around `driver.prepare()` and the following
`try/finally`

`driver.prepare()` runs before the `try/finally` that stops sandboxes, calls
driver teardown, and deletes the temporary workspace. A missing CLI, missing
SDK package, or preparation exception can therefore leave temporary files
behind and produce a traceback instead of a controlled run failure.

**Recommended change**

- Start the outer `try/finally` immediately after workspace creation.
- Include driver preparation and sandbox startup inside it.
- Make sandbox stop and driver teardown individually best-effort so one cleanup
  error does not prevent the remaining cleanup.
- Add tests for preparation failure, sandbox-start failure, teardown failure,
  and `KeyboardInterrupt`.

**Review (verified against code): CONFIRMED, high severity, easily reproducible.**
`optarena/runner.py` line ~418: `driver.prepare(scenario, root)` runs
*before* the `try:` at line 419 that guards `sandbox.stop()`,
`driver.teardown()`, and `shutil.rmtree(root)`. Reproduced directly with a
driver whose `prepare()` raises `RuntimeError` (exactly what
`cli_agents.py`'s real `prepare()` does when the binary isn't on PATH -
`optarena/drivers/cli_agents.py:210-214` - a completely ordinary user
mistake, not an edge case): the exception propagated uncaught out of
`run_scenario()`, and the `tempfile.mkdtemp(prefix="optarena_")` workspace
from line 373 was left on disk - exactly 1 leaked directory per failed run,
confirmed via a controlled before/after glob. `cli.py`'s `cmd_run` only
catches `ValueError` around `run_scenario()` (line 120), so this
`RuntimeError` (and anything else `prepare()`/`sandbox.start()` can throw)
prints a raw traceback instead of a clean CLI error - this is the same root
cause as F-04, not a separate bug. Worth noting as corroborating (not
conclusive) evidence: this machine's temp directory currently has **3,929**
leftover `optarena_*` directories dating back to early July, consistent
with this leak having been live throughout the project's development,
though some of that count is plausibly `--keep-workspace` usage rather than
crashes - not each one was individually audited.

**Fixes.** `runner.py`'s `run_scenario()`: `driver.prepare(scenario, root)`
and every `sandbox.start()` call now run *inside* the `try:` that guards
cleanup, not before it. The `finally:` block was rewritten so every cleanup
step is individually best-effort - each `sandbox.stop()` call and
`driver.teardown()` is wrapped in its own `try/except Exception: pass`, so
one failing cleanup step (a sandbox that won't stop) can't also skip the
rest (driver teardown, workspace removal, the final checkpoint write). The
outer exception handler was widened from `except Exception` to `except
BaseException` so a `Ctrl+C` (`KeyboardInterrupt`) mid-run also reaches the
`finally` block and gets a proper `status="interrupted"` checkpoint (see
F-02) instead of skipping cleanup entirely. Verified live: a driver whose
`prepare()` raises `RuntimeError` (the exact original repro) now leaves zero
leaked workspace directories, confirmed via a controlled before/after glob
comparison, and `RunScenarioCleanupResilienceTests.
test_teardown_failure_does_not_propagate_or_lose_the_result` (new test)
asserts a raising `driver.teardown()` no longer prevents `run_scenario()`
from returning a completed record. Not done: the report's suggested test
matrix ("preparation failure, sandbox-start failure, teardown failure, and
KeyboardInterrupt") is only partially covered by new tests - teardown
failure and the empty-case-set path are tested; sandbox-start failure and a
simulated `KeyboardInterrupt` mid-loop are not, since exercising those
without a real container engine needs more elaborate mocking than this pass
covered.

#### F-02: Completed cases are lost when a run is interrupted

**Priority:** High
**Area:** Result durability
**Location:** `optarena/runner.py`, `optarena/cli.py`, and `optarena/store.py`

Case results are assigned to the final `RunRecord` only after the scenario loop
finishes, and the record is saved only after `run_scenario()` returns. An
unexpected failure late in a large or paid run can lose all earlier results.

**Recommended change**

- Create an in-progress run record before the first case.
- Atomically checkpoint after every completed case.
- Record a lifecycle status such as `running`, `completed`, `interrupted`, or
  `infrastructure_error`.
- On success, atomically promote the checkpoint to the final run record.
- Add a `runs recover` or `runs show` path for interrupted records.

**Review (verified against code): CONFIRMED, high severity.**
`optarena/runner.py`'s `run_scenario()` accumulates every case's
`CaseResult` into a local `results` list across the whole `for case in
cases:` loop (line ~432) and only assigns `record.cases = [r.to_dict() for
r in results]` after the loop fully completes (line 438). `cli.py`'s
`cmd_run` then only calls `save_run(rec)` after `run_scenario()` returns
(line 124). There is no intermediate persistence anywhere in this path - a
`Ctrl+C`, OOM kill, or crash on case 99 of 100 loses all 99 completed
results, including any paid-API spend they represent. This is real and, for
a long/expensive run, the single most consequential finding in the report.

**Fixes.** Implemented the "smaller interim option" shape (incremental
checkpoint + lifecycle status), not the SQLite rewrite - see F-07 for why
that's the right call for now. `runner.RunRecord` gained a `status` field
(`"running"` while the case loop is in progress, `"completed"` on normal
finish, `"interrupted"` on `KeyboardInterrupt`/any exception - see F-01's
widened `except BaseException`). `store.py` gained `save_checkpoint()`,
called after every completed case (serial loop and the parallel
`_on_result` callback alike) - it always overwrites the run's file (unlike
`save_run()`, which refuses to) and does an incremental `index.json` update
via `_upsert_index_entry()` rather than F-07's O(n) `rebuild_index()`.
`store.save_run()` now treats an existing file at the target path as *this
run's own in-progress checkpoint being finalized* (its saved `status ==
"running"`) rather than a collision, so the normal "checkpoint every case,
then `save_run()` at the end" flow doesn't trip its own collision guard.
Verified live: a scenario forced to raise mid-loop leaves a checkpoint file
on disk with `status: "interrupted"` and every case completed before the
interruption intact. New tests: `CheckpointStatusTests` (4 tests - checkpoint
-then-finalize, repeated checkpoints don't raise, a genuinely different
existing file still raises `FileExistsError`, incremental index update) and
`RunScenarioLifecycleEventsTests` (the `checkpoint_saved` event fires once
per completed case). Not done: no `runs recover` CLI subcommand - an
interrupted run's checkpoint is a completely ordinary run file (`status:
"interrupted"`), so `optarena runs show <id>` already reads it today, but
there's no command that specifically surfaces "here are your interrupted
runs" or resumes execution from one.

#### F-03: Subprocess and HTTP response capture is unbounded

**Priority:** High
**Area:** Memory robustness
**Location:** `optarena/cases.py`, `optarena/drivers/openai_chat.py`,
`optarena/drivers/cli_agents.py`, and `optarena/packs.py`

Agent output, test output, container output, backend responses, and downloaded
case packs are read fully into memory. The saved run normally retains only a
small tail, but the process has already allocated the complete output.

**Recommended change**

- Stream process output to a bounded buffer or temporary file.
- Retain a configurable head and tail, for example 64 KiB each.
- Store `output_truncated: true` and the original byte count.
- Enforce a maximum backend-response size.
- Enforce a maximum downloaded case-pack size.
- Add tests using a process and HTTP fixture that return oversized output.

**Review (verified against code): CONFIRMED, real but lower practical risk
than F-01/F-02.** Three unbounded reads, all confirmed by direct grep:
`optarena/drivers/openai_chat.py:64` (`resp.read().decode()` on the backend
response), `optarena/packs.py:95` (`resp.read().decode("utf-8")` on a
downloaded pack, which per the docstring can come from an arbitrary
user-supplied URL), and `optarena/cases.py`'s `run_capture()` (used by
every CLI-agent driver), which wraps `subprocess.Popen.communicate()` -
Python buffers the whole child-process output in memory before returning,
with no cap. The saved run does truncate to a tail (confirmed:
`_run_check_command_sandbox` slices `[-400:]` before storing), but that
truncation happens *after* the full read/buffer, so it doesn't bound peak
memory the way the finding implies streaming/bounded capture would. Real
gap; severity is "an adversarial or badly-broken backend/agent/pack can OOM
the host," which is plausible but requires either a malicious pack URL or a
badly misbehaving local process - lower likelihood than F-01/F-02's "happens
on the very first missing-binary run."

**Fixes.** All four unbounded reads bounded, each with its own cap
appropriate to what's realistic for that data: `cases.py`'s `run_capture()`
was rewritten to read `stdout`/`stderr` via two reader threads calling a new
`_drain_bounded()` (256 KiB cap, periodic compaction so a long-running
process doesn't pay O(n squared) for repeated trimming) instead of
`Popen.communicate()`'s unbounded buffering; `openai_chat.py`'s
`_post_json()` now does `resp.read(_MAX_RESPONSE_BYTES + 1)` (8 MiB) and
raises `ValueError` if exceeded; `packs.py`'s `load_pack()` URL branch does
the same at 64 MiB (packs legitimately bundle many cases, so a more generous
cap than a chat response). Fixed a real bug found while implementing this:
the first version of `run_capture()`'s timeout path joined the reader
threads *before* killing the timed-out process - since a still-alive
process's pipes never close, this delayed the actual process-tree kill by
the full 10s join timeout on each stream (~20s), caught by
`test_child_process_tree_killed_on_timeout` failing after the change,
fixed by reordering (kill first, then join). Also fixed a co-occurring
`ResourceWarning: unclosed file` from the same rewrite. New test:
`RunCaptureTests.test_output_capture_is_bounded_not_unbounded` (a child
writing 2 MiB to stdout; captured output stays under 2x
`_MAX_CAPTURE_BYTES`). Not done: `output_truncated: true` and an original
byte count are not recorded anywhere in the oracle info dict - the cap
silently truncates rather than flagging that truncation happened, which the
report's recommendation asked for explicitly.

#### F-04: Expected CLI errors escape as raw tracebacks

**Priority:** High
**Area:** CLI usability and automation
**Location:** `optarena/cli.py`

The run command catches `ValueError` around scenario execution, but other
expected exceptions are not normalized. Confirmed examples include an unknown
case name and an invalid `--matrix-drivers` entry.

**Recommended change**

- Validate matrix driver names before creating scenarios.
- Validate inline scenarios using the same rules as JSON scenarios.
- Convert `FileNotFoundError`, `KeyError`, driver availability errors, and
  expected container errors into concise messages and exit code 2.
- Reserve tracebacks for `--debug` mode or truly unexpected defects.
- Add CLI integration tests that assert exit codes and stderr.

**Review (verified against code): CONFIRMED, same root cause as F-01.**
`cli.py`'s `cmd_run` (line ~115) wraps `run_scenario()` in `try: ... except
ValueError as e:` only. Everything driver `prepare()`/`sandbox.start()` can
raise beyond `ValueError` - confirmed concretely: `cli_agents.py` raises
plain `RuntimeError` for a missing binary, `KeyError` for an unknown CLI
agent key - escapes as a raw traceback. The finding's two named examples
("an unknown case name and an invalid `--matrix-drivers` entry") were not
independently re-derived line-by-line, but the general claim ("other expected
exceptions are not normalized") is verified true via the `prepare()` path
alone, which is a more common failure mode than either named example.

**Fixes.** `cli.py` gained a module-level `_EXPECTED_RUN_ERRORS = (ValueError,
RuntimeError, KeyError, FileNotFoundError, OSError, ImportError)` tuple
covering every exception type a driver's `prepare()`/the scenario/sandbox
layer can raise for an ordinary, anticipated failure (missing CLI binary,
unknown driver name, missing SDK-driver pip extra, missing scenario file,
container-engine `OSError`), plus `_format_expected_error()` (special-cases
`KeyError`'s `repr()`-quoting `__str__` quirk so it doesn't print as
double-quoted). `cmd_run`'s scenario-construction step and its
`run_scenario()` call both now `except _EXPECTED_RUN_ERRORS as e:` instead
of `except ValueError` alone, printing a clean one-line message and
returning exit code 2; a new global `--debug` flag re-raises the full
traceback on request instead of never showing one. A genuinely unanticipated
exception type also gets a new `except Exception as e:` catch-all that
prints `"unexpected {error} (re-run with --debug for the full traceback)"` -
distinct wording from the anticipated-error path, so a real defect doesn't
read as an ordinary user mistake. Verified live: a scenario with a
deliberately-broken driver produces a clean one-line `error:` message by
default and the full traceback under `--debug`. Not done: the report's two
named repro examples (unknown case name pre-validation, invalid
`--matrix-drivers` entry validated before scenario construction) were not
independently re-derived or given dedicated tests - the fix covers the
broader exception-type gap they're both instances of, but no test pins
those two specific CLI invocations.

#### F-05: No whole-case deadline exists

**Priority:** Medium-High
**Area:** Runtime predictability
**Location:** driver prompt loops

The documented case timeout is applied separately to each prompt. A case with
three prompts can consume roughly three times the configured timeout, plus
oracle time.

**Recommended change**

- Establish one monotonic deadline per case.
- Pass the remaining budget to every prompt and verification step.
- Optionally expose separate `agent_timeout` and `oracle_timeout` settings.
- Record `agent_duration_s`, `oracle_duration_s`, and total wall duration.

**Review (verified against code): CONFIRMED as described, with one nuance
worth adding.** `cli_agents.py`'s `run_case()` (line ~256) loops `for i,
prompt in enumerate(case.get("prompts", []), 1):` and passes the *same*
unreduced `timeout = scenario.timeout or case.get("timeout", 180)` into
`run_capture(..., timeout=timeout, ...)` on every iteration - confirmed, no
per-case deadline or remaining-budget tracking exists. The audit's own
corpus stats (which were independently re-derived and match exactly: 501
one-prompt cases, 7 two-prompt, 2 three-prompt) mean this mostly matters for
9 of 510 cases as of this round, but it's a real correctness gap in the
timeout contract, not a hypothetical one, and would matter more as multi-prompt
cases grow.

**Fixes.** Implemented the core recommendation (one monotonic deadline per
case, remaining budget passed to each prompt) in all three prompt-loop
drivers: `cli_agents.py`, `aider_cli.py`, and `openai_chat.py`'s
`OpenAIChatDriver.run_case()` (shared by the Ollama variant). Each now
computes `deadline = t0 + timeout` once before the loop; at each prompt
boundary, `remaining = deadline - time.monotonic()` is checked, and if
`<= 0` the loop breaks with `result.error = "case deadline ({timeout}s)
exceeded before prompt {i}/{n_prompts}"` instead of starting a prompt that
can no longer fit its share of the budget; `remaining` (not the original
`timeout`) is what's actually passed to the prompt's own execution call
(`run_capture(..., timeout=remaining, ...)` / `self._chat(..., remaining)`).
A 3-prompt case can therefore no longer consume ~3x the configured timeout.
Not done: the optional `agent_timeout`/`oracle_timeout` split and recording
`agent_duration_s`/`oracle_duration_s` separately - the fix caps total
prompt-loop time but doesn't yet break that time down by phase, and no new
test specifically exercises the deadline-exceeded-mid-loop path (only
manually verified via code reading of the three call sites).

#### F-06: Parallel verification loses the shared-container optimization

**Priority:** Medium-High
**Area:** Container performance
**Location:** `optarena/runner.py` and `optarena/cases.py`

Serial built-in runs reuse one container per image. Parallel and custom-pack
runs use one ephemeral container per check. With many cases or trials,
container startup and writable-cache initialization may dominate evaluation
time.

**Recommended change**

- Create a bounded pool of worker containers per image.
- Assign one check to a worker container at a time.
- Recycle a worker after timeout or failed health validation.
- Keep custom-pack workspaces isolated while still allowing reuse of the
  container process.
- Measure and report container startup time separately from test time.

**Review (verified against code): CONFIRMED as a real performance gap, but
important context the finding omits: this is a deliberate, already-reasoned
tradeoff, not an oversight.** `runner.py` lines ~392-408 carry an explicit
comment block (tagged H-02/C-05, Round 1's IDs) explaining *why* parallel and custom-pack
runs skip the shared container: a shared container under `--parallel` would
let concurrent cases' `check_command`s collide in one process table/network
namespace, and the timeout-reap path (`kill -9 -1`) would kill every other
case's in-flight process along with the one that timed out; for custom
packs, the shared container bind-mounts the *whole* run root, so one
case's check_command could read/tamper with another's workspace - judged
acceptable for the self-verified built-in corpus but not for a downloaded
pack. The ephemeral-per-check fallback is the current fix for both
problems, traded for the performance cost this finding describes. A worker
pool (one container per image, one case in flight per worker, correctly
isolated) is a reasonable next step and would need to preserve both of
those isolation properties, not just restore the shared-container speed.

**Fixes.** Implemented exactly the "reasonable next step" the review
outlined, preserving both isolation properties. `runner.py`: `--parallel`
now starts `pool_size = parallel` `DockerSandbox` instances per image
(`sandbox_pool: dict[image, list[DockerSandbox]]`) instead of zero; a new
persistent worker-thread model (`_worker_loop`/`_run_parallel`, replacing
the previous `ThreadPoolExecutor` task-per-case approach) lets each of the
`parallel` worker threads bind exactly one sandbox per image for its whole
lifetime via a new `cases_mod._worker_sandboxes` thread-local map, so
concurrent workers' `exec`s land in *different* containers and a
timeout-triggered `kill -9 -1` in one worker's container can no longer
touch another worker's in-flight process. `cases.py` gained
`_active_sandbox_for(image)`, which checks the calling thread's
`_worker_sandboxes` map first and falls back to the old shared
`_active_sandboxes` dict otherwise (so serial execution is unaffected).
C-05's custom-pack isolation is untouched - the sandbox-skip condition is
now `if scenario.cases_dir:` (was `if parallel > 1 or scenario.cases_dir:`),
so a custom pack still gets zero pooled/shared sandboxes regardless of
parallel/serial, confirmed by the pre-existing
`ParallelSandboxSharingTests.test_no_sandbox_started_when_parallel` test
(unchanged, still passing - it already used a `cases_dir`-based scenario).
Verified live: a real `--parallel 2` run against real Docker confirmed
exactly 2 sandboxes started per image needed. Not done: worker recycling
after a failed health check, and separate startup-vs-test timing
measurement - both from the recommendation's remaining bullets - were not
implemented; no new automated test covers the built-in-corpus + parallel
pooling path specifically (verified live only, not via a permanent test).

#### F-07: Result indexing is O(number of historical runs) per save

**Priority:** Medium-High
**Area:** Storage scalability
**Location:** `optarena/store.py`

Saving one run rebuilds `index.json` by reading and parsing every historical
run. This becomes progressively slower as the results directory grows.

The atomic writer also uses a predictable `.json.tmp` path, creating a race
between concurrent OptArena processes.

**Recommended change**

Preferred option:

- Use SQLite with WAL mode for runs, cases, summaries, and comparisons.
- Keep the existing JSON export format for portability.

Smaller interim option:

- Incrementally prepend/update one index entry.
- Use unique temporary filenames.
- Use an inter-process lock around index updates.
- Provide `runs rebuild-index` as an explicit repair command.

**Review (verified against code): CONFIRMED, both parts.** `store.py`'s
`save_run()` calls `rebuild_index()` (line 66) unconditionally, which does
`for f in sorted(RUNS_DIR.glob("*.json"), ...)` and JSON-parses *every*
historical run file on *every* save - confirmed, no incremental path
exists. The race: `_write_atomic()` (line 43-47) always writes to
`path.with_suffix(".json.tmp")` - the same temp filename regardless of
which process is writing - so two OptArena processes finishing a run around
the same moment can both target `index.json.tmp` concurrently. Because the
final step is an atomic `.replace()`, the practical failure mode is a lost
update (whichever finishes last wins, and the other's index contribution is
overwritten - not silent corruption of the index file itself), and since
`index.json` is fully reconstructible from the run files, an explicit
`rebuild-index` command is a reasonable-enough mitigation as the audit
suggests. Not catastrophic, but real, and gets slower with every run added
to `results/`.

**Fixes (interim option, by explicit choice - not the SQLite rewrite).**
The smaller interim option was deliberately chosen over the SQLite/WAL
option: this project's own results directories are realistically hundreds
to low-thousands of runs, not the millions-of-rows scale where SQLite
clearly wins, and introducing a database dependency (even stdlib
`sqlite3`) into a project whose core is explicitly "dependency-free,
portable JSON files" (see `docker/images.lock.json`'s own philosophy and
the project's stdlib-only stance) is a bigger, harder-to-reverse
architectural commitment than this fix-up pass should make unilaterally.
`store.py`: new `_index_entry()` builds one index row; new
`_upsert_index_entry()` reads the current `index.json` (tolerant of a
missing/corrupt file), removes any existing entry for the same `run_id`,
prepends the new one, and writes atomically - O(1) per save instead of
O(n). `rebuild_index()` still exists, now explicitly documented as a manual
repair command, no longer called from `save_run()`'s normal path. Race
fixed: `_write_atomic()`'s temp filename is now
`path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")` (was a fixed
`.json.tmp` suffix shared by every writer), so two processes writing around
the same moment can no longer collide on the same temp path. A new
`_IndexLock` (exclusive-file-creation mutex with staleness-based
lock-breaking after 30s and a 10s wait-then-proceed-unlocked fallback,
implemented via `os.O_CREAT | os.O_EXCL` rather than `fcntl`/`msvcrt` so it
works identically on Windows and POSIX) serializes concurrent
`_upsert_index_entry()` calls, closing the lost-update race the review
described, not just leaving it as an accepted risk. `compare.py`'s
`save_comparison()` also switched to the unique-temp-name path via the same
`_write_atomic()`. `store.list_runs()` still falls back to
`rebuild_index()` if `index.json` is missing entirely. `runs rebuild-index`
already existed as the explicit repair command the audit asked for; not
newly added.

#### F-08: Comparison files can overwrite each other

**Priority:** Medium
**Area:** Result durability
**Location:** `optarena/compare.py`

Comparison filenames contain second-resolution time and scenario labels but no
random identifier. Identical comparisons created in the same second can target
the same file.

**Recommended change**

- Add a UUID suffix, as run IDs already do.
- Refuse to overwrite an existing comparison.
- Use the same unique atomic-write helper as run persistence.

**Review (verified against code): CONFIRMED, and this one is actually
weaker-protected than run persistence, worth flagging explicitly.**
`compare.py`'s `save_comparison()` (line ~147) builds the filename as
`f"{time.strftime('%Y%m%d-%H%M%S')}_{safe_a}_vs_{safe_b}.json"` - second
resolution, no UUID - then writes via `store._write_atomic()` with no
existence check. Contrast with `store.save_run()`, which explicitly checks
`path.exists()` and raises `FileExistsError` before writing (line 61-64) -
`save_comparison()` has neither that guard nor a UUID, so it is silently
overwritten, not just theoretically collidable. Two `optarena compare`
calls between the same two run labels within the same second (a scripted
regression-gate loop, or `run` auto-comparing two just-finished scenarios
twice) lose the earlier comparison with no error.

**Fixes.** `compare.py` gained a `uuid` import; `save_comparison()` now
loops up to 5 times, each attempt appending a fresh
`f"_{uuid.uuid4().hex[:8]}.json"` suffix and checking `path.exists()` before
writing, raising `FileExistsError` only if all 5 attempts collide
(astronomically unlikely - 5 independent UUID collisions). `cli.py`'s three
call sites (`cmd_run`'s auto-compare, `cmd_compare`, `cmd_regression`) all
wrap the `save_comparison()` call in `try/except FileExistsError`, printing
`"comparison NOT saved: {e} (both runs are saved; re-run \`optarena
compare\` to retry)"` to stderr rather than crashing - the underlying runs
being compared are already safely saved via F-02's checkpointing regardless
of whether the secondary comparison artifact succeeds. Same unique-filename
pattern as F-07's `_write_atomic()` fix, applied to the one place that
didn't have it. Not done: `save_comparison()` does not (and now doesn't need
to) refuse-then-overwrite the way `store.save_run()` does - the UUID suffix
makes every call target a distinct path, so there's no longer an
"overwrite an existing comparison" case to guard against; this is a
different mechanism than the report's suggested "refuse to overwrite" but
achieves the same end (no silent loss).

#### F-09: Trial identity and duration metrics are inconsistent

**Priority:** Medium
**Area:** Metrics correctness
**Location:** `optarena/runner.py` and `optarena/metrics.py`

For a future caching driver, the requested trial count is handed to the driver
and then the runner-local count is reset to one before the manifest is built.
The run manifest can therefore claim one trial even when the driver performed
several.

For normal trial runs, the merged case duration is the mean of the trials.
`total_duration_s` then sums those means, so it is not the total amount of work
performed.

**Recommended change**

- Preserve `requested_trials` separately from `runner_trials`.
- Require caching drivers to report the number of trials actually completed.
- Store both mean trial duration and summed trial work.
- Clarify wall-clock duration versus cumulative worker duration under
  parallelism.

**Review (verified against code): CONFIRMED, both halves.** Manifest
mismatch: `runner.py` lines ~338-348 reassign `trials = 1` for a caching
driver (after handing the real count to `driver.trials`) *before*
`build_manifest(scenario, cases, trials)` is called at line 367 - the
manifest's `"trials"` field is provably built from the already-reset value.
(Caveat also worth stating plainly: the finding's phrasing "for a future
caching driver" is accurate as of this round - the code comment at
`optarena/drivers/base.py` and `runner.py`'s own docstring both say no
current driver sets `caches_results`, so this path is dead code at the time
of writing, not a live bug affecting current runs; it would activate the moment any driver
sets that flag.) Duration-sum claim: `runner.py`'s `_merge_trials()` sets
`duration_s=statistics.mean(r.duration_s for r in results)` per case (line
152), and `metrics.py`'s `aggregate()` computes `total_duration_s =
round(sum(durations), 1)` over those already-averaged per-case values (line
153) - confirmed, `total_duration_s` under `--trials N` is the sum of
per-case *means*, not the sum of actual per-case *work*, understating total
work by roughly a factor of N for trial-repeated cases. This one is live
and affects every `--trials N > 1` run today, unlike the manifest half.

**Fixes, both halves.** Manifest identity: `runner.build_manifest()` gained
a `runner_trials: "int | None" = None` parameter, defaulting to
`requested_trials` when not given; `run_scenario()` now computes
`requested_trials = max(1, int(trials))` up front and passes it to
`build_manifest(..., requested_trials, runner_trials=trials)` *before* the
caching-driver branch resets its own local `trials` variable to 1 - the
manifest's `"trials"` field (semantic count) and new `"runner_trials"` field
(the runner's own loop count, which does still drop to 1 for a caching
driver) are now recorded separately, so a caching-driver run's manifest
correctly claims N trials even though the runner's loop executed once.
Duration-sum fix: `metrics.py` gained `_case_total_duration(c)`, which
returns `sum(c["extra"]["durations_s"])` when a case recorded per-trial
durations, falling back to the merged `c["duration_s"]` (a mean) otherwise;
`aggregate()`'s `total_duration_s` now sums `_case_total_duration(c)` across
cases instead of summing the already-averaged per-case means directly.
New tests: `ManifestTrialsTests` (3 tests - `runner_trials` defaults to
requested trials, can diverge from it, and a real caching-driver run
through `run_scenario()` records `trials=5, runner_trials=1` in its
manifest). Caveat unchanged from the review: `caches_results` is still dead
code at the time of writing (no shipped driver sets it), so this manifest half is verified
by direct testing of the mechanism, not by a live caching-driver run in
production. The duration-sum fix is live and affects every `--trials N > 1`
run today, matching the review's assessment of which half matters now.

#### F-10: Infrastructure failures can look like model failures

**Priority:** Medium
**Area:** Benchmark validity
**Location:** oracle result handling

An unavailable verification environment or failure to execute the oracle can
be represented as an ordinary case failure. This can incorrectly reduce the
measured pass rate of a driver or model.

**Recommended change**

- Introduce explicit statuses: `pass`, `oracle_fail`, `driver_error`, and
  `infrastructure_error`.
- Exclude infrastructure errors from model accuracy or report both raw and
  adjusted denominators.
- Make regression gates fail separately when infrastructure errors exist.

**Review (verified against code): CONFIRMED.** `cases.py`'s
`_run_check_command_sandbox()` (line ~652-661) catches `OSError`/`ValueError`
around the container `exec` call (e.g. the container engine crashing or
being killed mid-run) and returns a plain failure string into the *same*
`failures` list an ordinary wrong-code failure would populate. `_new_oracle_info()`
(the dict this flows through) has no `error_type`/`infrastructure_error`
field of any kind - just `check_command, ran, sandbox, engine, image,
exit_code, duration_s, output`. At the `metrics.aggregate()` level,
`passed = sum(1 for c in case_dicts if c.get("passed"))` treats this
identically to a real correctness failure. A container engine hiccup mid-run
genuinely does drag down the measured pass rate with no way to distinguish
it after the fact from the model actually being wrong - confirmed, and this
is a real benchmark-validity concern given the project's own stated
positioning as evidence for tool/model decisions.

**Fixes (complete fix, threaded end to end).** Rather than the report's
proposed 4-state enum (`pass`/`oracle_fail`/`driver_error`/
`infrastructure_error`), added a single boolean `infrastructure_error` flag
orthogonal to the existing pass/fail verdict - simpler to thread through
every consumer and sufficient to answer "was this failure the model's fault
or the environment's": `cases._new_oracle_info()` now includes
`"infrastructure_error": False` by default, set `True` in exactly the
infra-failure paths the review identified plus their siblings:
`run_check_command`'s sandbox-refused branch, and the `except (OSError,
ValueError)`/`except OSError` handlers in `_run_check_command_sandbox`,
`_run_check_command_docker`, and `_run_check_command_local`. From there it
flows through the whole pipeline: `metrics.py` gained
`_case_infrastructure_error(c)` (checks both the case's single oracle and
every trial in `oracle_all_trials`, so a flag set on any one trial isn't
lost by trial-merging) and `aggregate()` now reports `infrastructure_errors`
(count, `None` when zero - absent rather than a false zero) and
`adjusted_pass_rate` (pass rate excluding infra-error cases from the
denominator, computed only when there's at least one non-infra case to
divide by). `metrics.case_deltas()` carries `a_infrastructure_error`/
`b_infrastructure_error` per case into comparisons.
`compare.regression_summary()` gained `infrastructure_error_cases` (case
names where either run side hit one); `compare.format_regression()` prints
a `** INFRASTRUCTURE ERRORS (not a code verdict) **` warning block listing
them. `cli.py`'s `cmd_regression` now returns exit code 3 (distinct from
1's "something regressed" and 0's "clean") when
`infrastructure_error_cases` is non-empty and nothing regressed - a CI gate
can tell "the environment broke" apart from "nothing regressed" without
parsing the printed report. New tests: `InfrastructureErrorAggregateTests`
(3 tests - counted and excluded from `adjusted_pass_rate`, `None` when
absent, checked across all trials not just the final oracle).

#### F-11: Case-pack installation is not fully transactional

**Priority:** Medium
**Area:** Pack reliability
**Location:** `optarena/packs.py`

Pack versions are described as semantic versions but are sorted as ordinary
strings. This can select `1.9.0` over `1.10.0`.

A forced reinstall deletes and rewrites files in place. A crash can leave a
partial mixture of the old and new pack.

**Recommended change**

- Validate and compare semantic versions properly.
- Validate `case_count`, the `cases` object, filenames, and filename
  normalization collisions.
- Install into a temporary sibling directory.
- Validate the staged directory using `load_cases()`.
- Atomically rename the staged directory into place.

**Review (verified against code): CONFIRMED, all three sub-claims.**
(1) Version sort: `packs.py`'s `resolve_pack()` (line ~174) sorts candidates
by `str(m.get("version", ""))` - a plain string sort. `"1.9.0" >
"1.10.0"` lexicographically (character 3 is `'9'` vs `'1'`), so a bare-name
`--pack` reference would indeed resolve to `1.9.0` over the semantically
newer `1.10.0` - confirmed, not a hypothetical. (2) Non-atomic reinstall:
`install_pack()` (line ~114) with `force=True` deletes old `*.json` files in
one loop (line 130-132) then writes new ones in a second loop (line
133-135), directly on `root` - no staging directory, no atomic rename; a
crash between or during those loops leaves a genuine old/new mixture. (3)
Missing validation, confirmed and actually slightly broader than stated:
`load_pack()` validates the pack envelope (format version, required keys,
name regex, content hash) but never calls `validate_case()` on the
individual cases inside `pack["cases"]` - unlike `build_pack()`, which does
validate when *creating* a pack (line 67). A downloaded pack's cases are
therefore installed to disk unvalidated; a malformed case only surfaces
later when something tries to actually load/run it. Filename-collision risk
also confirmed: `install_pack()` writes each case to `root /
_safe(fname)` (line 134) with no collision check - two case filenames that
sanitize to the same string silently overwrite each other, losing a case
with no warning.

**Fixes, all three.** Version sort: new `packs._version_key(v)` parses a
`MAJOR[.MINOR[.PATCH]]` numeric prefix via regex and compares those
components as integers (falling back to a string tail for anything after,
e.g. `-rc.1`; a version with no numeric prefix at all still sorts
consistently rather than crashing) - hand-rolled and stdlib-only rather than
adding a `packaging` dependency, since "1.10 beats 1.9" doesn't need full
semver-spec compliance. `resolve_pack()`'s `candidates.sort()` now keys on
`_version_key(...)` instead of the plain string. Atomic install:
`install_pack()` now stages the whole install (every case file plus
`_pack.json`) into a sibling directory
(`parent / f".{root.name}.staging-{uuid.uuid4().hex[:8]}"`), wraps the
staging writes in `try/except BaseException: shutil.rmtree(staging); raise`,
and only removes the old `root` and renames the staging directory into
place (`staging.replace(root)`) after every file is written successfully -
a crash mid-install now leaves either the untouched old pack or the
complete new one, never a mixture. Validation: `load_pack()` now calls
`validate_case()` on every case in `pack["cases"]` and
`validate_unique_case_names()` across all of them (previously only
`build_pack()` did this, at creation time, not at install time), checks
`case_count` against the actual case count, and - a filename-collision
check the audit didn't explicitly ask for as a `load_pack()`-time check but
that closes the exact gap it flagged - scans every case filename's
`_safe()`-sanitized form for collisions before install, raising with both
colliding original names named explicitly. New tests:
`PackVersionSortTests` (4), `PackLoadValidationTests` (5 - valid pack loads,
malformed case rejected, duplicate names rejected, case_count mismatch
rejected, filename-sanitization collision rejected), `PackInstallAtomicityTests`
(2 - a failure partway through install leaves no half-written staging
directory behind, and a successful install is both visible and idempotent
on re-install of the identical pack).

#### F-12: A wheel does not contain the complete application

**Priority:** Medium
**Area:** Packaging
**Location:** `pyproject.toml`

The wheel intentionally includes the Python package and built-in case JSON
files but excludes the dashboard, Docker contexts, and starter repositories.
Commands that depend on those assets therefore require a source checkout.

**Recommended change**

- Access packaged assets through `importlib.resources`.
- Bundle the dashboard and small Docker contexts as package data, or publish a
  separately versioned `optarena-assets` package.
- Decide whether starter repositories belong in package data or downloadable
  versioned packs.
- Add a wheel-install smoke test in a clean virtual environment.
- Replace the deprecated license table with an SPDX license expression.

**Review (verified against code): CONFIRMED, and it's deliberate/documented
rather than an oversight - worth stating precisely.** `pyproject.toml`'s
`[tool.setuptools.packages.find]`/`[tool.setuptools.package-data]` only
include `optarena*` and `optarena/cases/*.json`; a comment directly above
states "Intentionally NOT bundled: docker/, dashboard/, repos/ - all
top-level siblings of optarena/, not part of this package." `README.md` has
a matching "Source-checkout install only" section spelling out the same
limitation for users. So this is a known, documented current constraint,
not a hidden gap - the audit's recommendation (package via
`importlib.resources`, or a separate assets package) is a reasonable next
step, but "the project doesn't know about this" would be the wrong
takeaway. The license-table claim is separately and independently
confirmed: `license = { text = "Apache-2.0" }` is exactly the deprecated
table form; a plain SPDX string (`license = "Apache-2.0"`) is what current
setuptools wants, matching the deprecation warning the audit says it
observed during its own build.

**Fixes: none - deliberate decision, not a gap.** Explicit call: "let it be,
git clone is fine for v0.1." The core finding (wheel deliberately excludes
`docker/`, `dashboard/`, `repos/`) stays exactly as documented in
`pyproject.toml`'s comment and README's "Source-checkout install only"
section - no `importlib.resources` migration, no bundling, no
`optarena-assets` package. One independent sub-item from this finding's own
recommendation list *was* addressed as part of this pass, since it's
unrelated to the wheel-completeness decision: `pyproject.toml`'s deprecated
`license = { text = "Apache-2.0" }` table is now the PEP 639 SPDX string
`license = "Apache-2.0"` (with the now-redundant `"License :: OSI Approved
:: Apache Software License"` trove classifier removed, since setuptools
rejects having both). Verified by rebuilding the wheel locally: the
resulting `METADATA` now reports `Metadata-Version: 2.4` /
`License-Expression: Apache-2.0` with no deprecation warning. The other
sub-item from this finding's list, a wheel-install smoke test, was also
added - see F-17's `wheel-smoke` CI job - again because it stands on its
own regardless of whether the wheel's *contents* change.

#### F-13: Image builds are not fully reproducible

**Priority:** Medium
**Area:** Container builds
**Location:** `docker/`

Base images are digest-pinned and many direct dependencies are pinned, but
several sources of drift remain:

- `pyyaml` is unpinned in the base image;
- apt repositories are not snapshot-pinned;
- npm direct versions do not lock the transitive dependency graph;
- the Rust warmup does not ship a `Cargo.lock`;
- some downloaded tools are versioned but not represented in a shared image
  lock manifest.

**Recommended change**

- Add language lockfiles to every image context.
- Pin the remaining direct packages.
- Generate an `images.lock.json` containing image tags, base digests, package
  versions, and expected published digests.
- Include that lock identity in run manifests.

**Review (verified against code): CONFIRMED, all five bullet points.**
`docker/Dockerfile` line 29: `RUN pip install --no-cache-dir
--break-system-packages pyyaml` - genuinely unpinned, confirmed by direct
read (contrast with the project's own Docker-hardening pattern elsewhere of
pinning exact versions - this one slipped through). `apt-get install -y
--no-install-recommends` (line 17) has no `=<version>` pins and there's no
snapshot-pinning mechanism anywhere in the file - confirmed. No
`package-lock.json`/`Cargo.lock`/`yarn.lock` found anywhere under `docker/`
via direct search - confirmed for npm and Rust; "some downloaded tools are
versioned but not represented in a shared image lock manifest" was not separately verified, but given the other four points are all independently
confirmed and no `images.lock.json`-style manifest existed anywhere in the
repo at the time, this is consistent.

**Fixes, to the extent feasible without a full container-build-based CI
pipeline for every language.** `pyyaml` pinned: `docker/Dockerfile` line 29
is now `pip install ... pyyaml==6.0.2`. `docker/rust/Cargo.lock` was
**actually generated** (not stubbed) by running `cargo generate-lockfile`
inside the real `rust:1-bookworm` image against the warmup crate's real
source layout, then copied into the repo; `docker/rust/Dockerfile` now
`COPY`s it in and builds with `cargo build --locked` (verified: a full local
`docker build` of the rust image succeeds against the committed lockfile).
`docker/node/package.json` + `docker/node/package-lock.json` were likewise
**actually generated** by running `npm install` for the same 17 exact
package versions the Dockerfile previously installed via bare `npm install
-g`, inside the real `node:20-bookworm-slim` image (561 packages resolved);
the Dockerfile now runs `npm ci` against the committed lockfile into
`/opt/node-packages` instead of an unlocked global install, with `NODE_PATH`
and `PATH` (for `npm ci`'s local `.bin`, unlike the old global install)
pointed at it. Verified live: a full rebuild of the node image plus a
`require()` smoke test of all 17 packages, and a real `tsc --strict ...`
invocation matching the exact form the built-in TypeScript cases use,
both passed against the rebuilt image. New `docker/images.lock.json`: a
per-image ledger of base-image digest, pinned direct-dependency versions,
and (where one exists) the dependency-lockfile path, with an explicit
`known_gaps` list per image documenting what's still unlocked and why
(apt-snapshot-pinning deferred as a bigger, riskier change; Maven/NuGet/gem
gaps noted honestly rather than papered over) - go's image is noted as
*not* needing a `go.sum` despite having no committed one, since
`GOPROXY=off`/`GOSUMDB=off` plus a warm module cache already make its build
deterministic by a different mechanism. New `docker/check_images_lock.py`
verifies every image's `base_digest` matches its Dockerfile's actual `FROM`
digest and that every declared `dependency_lockfile` path exists - wired
into CI as the `images-lock-check` job (see F-17), with a deliberate
mismatch tested locally to confirm it actually fails, not just passes
vacuously. Not done: apt-snapshot-pinning (all images) and NuGet/Maven/gem
lockfile equivalents - documented as deferred `known_gaps` in
`images.lock.json` rather than silently left out.

#### F-14: Published sandbox images are effectively AMD64-only

**Priority:** Medium
**Area:** Docker/Podman portability
**Location:** `.github/workflows/publish-images.yml` and `docker/Dockerfile`

The image publishing workflow uses ordinary `docker build` on an AMD64 GitHub
runner. The base Dockerfile also downloads the
`terraform_*_linux_amd64.zip` artifact explicitly.

**Recommended change**

- Use Docker Buildx with `linux/amd64,linux/arm64`.
- Select downloads using `TARGETARCH`.
- Test at least the base, Python, Node, and Go images on ARM64.
- Publish one multi-platform manifest for each image tag.

**Review (verified against code): CONFIRMED, both specifics.**
`.github/workflows/publish-images.yml` line 70 runs a plain `docker build
-t "$BASE:latest" -t "$BASE:${{ github.sha }}"` with no `buildx`/`--platform`
invocation, on `runs-on: ubuntu-latest` (amd64) - confirmed single-platform.
`docker/Dockerfile` line 32 hardcodes
`https://releases.hashicorp.com/terraform/1.9.8/terraform_1.9.8_linux_amd64.zip`
- confirmed the exact artifact the finding names. This also means: anyone on
Apple Silicon (or any ARM64 host) running Docker/Podman today either eats
QEMU emulation overhead or can't use the published images at all - worth
noting as a real Podman-adjacent portability gap now that Podman support
exists in the tool itself (Podman on Apple Silicon is arguably the more
likely audience to hit this than Docker Desktop users, who've had amd64
emulation longer).

**Fixes, both specifics.** `docker/Dockerfile`'s terraform download now
uses a build-time `ARG TARGETARCH` (BuildKit's Go-style arch string,
"amd64"/"arm64" - exactly HashiCorp's own release-artifact naming, no
translation table needed) instead of the hardcoded
`terraform_1.9.8_linux_amd64.zip`; checked every other Dockerfile in the
project for similar hardcoded-arch downloads (composer's installer script
and the PHPUnit `.phar` are both architecture-independent, so terraform was
the only offender). `.github/workflows/publish-images.yml` now runs `docker
buildx build --platform linux/amd64,linux/arm64 ... --push` (instead of
plain `docker build` + separate `docker push`) with QEMU and Buildx set up
via `docker/setup-qemu-action`/`docker/setup-buildx-action` (both pinned by
commit SHA, resolved against the live GitHub API rather than guessed, same
supply-chain-pinning discipline the workflow already used elsewhere) - a
multi-platform result can only be pushed as a manifest list, not
`docker load`ed locally, hence `--push` replacing the build-then-push
two-step. Not independently re-verified: an actual `buildx build
--platform linux/amd64,linux/arm64` run for every one of the 9 images (that
would need real ARM64 build minutes/QEMU emulation time this session didn't
spend) - the Dockerfile-level fix (TARGETARCH) was verified by a real local
build, but the multi-platform workflow change itself was verified by
reading/dry-checking the YAML, not by an actual cross-platform CI run.

#### F-15: Mutable image tags remain the normal execution path

**Priority:** Medium
**Area:** Reproducible evaluations
**Location:** `optarena/cases.py` and built-in case definitions

The default runtime tags use `:latest`. Run manifests record the resolved
digest after execution, which is useful evidence, but users cannot easily lock
every per-language image before a mixed-language run.

**Recommended change**

- Support an image map in a scenario or lock file:

  ```json
  {
    "base": "optarena-tester:<immutable-tag>",
    "python": "optarena-tester-python:<immutable-tag>",
    "node": "optarena-tester-node:<immutable-tag>"
  }
  ```

- Resolve all case image aliases through that map.
- Store both requested image reference and resolved digest.

**Review (verified against code): CONFIRMED.** Every path that resolves an
image (`runner.py`'s manifest-building, `cases.py`'s `DockerSandbox`/
`run_check_command`) does `c.get("docker_image") or
os.environ.get("OPTARENA_DOCKER_IMAGE", DOCKER_IMAGE_DEFAULT)` - a plain
string, always resolving to a mutable tag like `optarena-tester-python:latest`.
The *only* override mechanism is `OPTARENA_DOCKER_IMAGE`, a single global
env var that replaces the image for every case uniformly - there's no way
at the time of this round to pin, say, "python cases use `:sha-abc123`, go cases use
`:sha-def456`" in one run. The manifest's `image_digests` (F-15 acknowledges
this) records what was *actually* used after the fact, which is good
evidence but doesn't let you *choose* a pinned image up front the way the
finding's proposed lock-map would. Confirmed real gap.

**Fixes.** Added exactly the image-map mechanism the finding proposed, as a
new `image_overrides: dict[str, str] | None` scenario field (validated in
`schema.py` as a string-to-string map). `cases.py` gained a run-scoped
`_image_overrides` module global, `set_image_overrides(overrides)` (called
by `run_scenario()` at the start of a run and reset to `None` in its
`finally` block, so overrides never leak into the next scenario in a
`--matrix-drivers`/`--matrix-models` sweep), and `resolve_image(image)`,
which matches either the exact image reference or a `DOCKER_IMAGES` short
track name (e.g. `"python"`) - so a scenario can pin either one specific
unusual `image` value or a whole registered track without knowing every
case's exact tag, matching the finding's example. Every image-resolving
call site (`run_check_command`, `runner.build_manifest()`'s image-set
computation, `verify.py`) now routes through `resolve_image()` instead of
using the raw case/env-var value directly. New tests:
`ImageOverrideResolutionTests` (3 - no-override passthrough, exact-match
override applied, unrelated image left alone). The manifest already
recorded `image_digests` (what was actually used) before this session; this
fix adds the missing other half - choosing a pinned image up front - without
touching that existing evidence-recording behavior.

#### F-16: Container-engine health is cached too aggressively

**Priority:** Medium-Low
**Area:** Docker/Podman recovery
**Location:** `optarena/cases.py`

Container-engine availability is cached for the entire process. A transient
Docker Desktop or Podman-machine startup failure affects every later scenario
in the same matrix run. A failed image pull is also attempted only once per
process.

**Recommended change**

- Cache health checks for a short TTL.
- Reset health state between scenarios.
- Retry transient engine and registry failures with bounded exponential
  backoff.
- Check and report the result of image tagging.
- Add timeouts to container stop and forced removal.

**Review (verified against code): CONFIRMED.** `cases.py`'s `_engine_checked`/
`_engine_bin`, `_docker_checked`/`_docker_ok`, and `_pull_attempted` are all
plain module-level globals set once per process with no expiry - confirmed
directly (`if _engine_checked: return _engine_bin`, no TTL comparison
anywhere near it). A transient failure (Docker Desktop still waking up,
Podman machine mid-start) gets cached as "unavailable" for the rest of the
process, and a failed pull is genuinely attempted "only once per process"
(`_pull_attempted` is add-only, confirmed) - both exactly as described. One
thing worth adding: this caching is deliberate for a *different*, already
partially-handled reason - `ensure_image()`'s docstring explicitly notes the
probe is retried once specifically to survive Docker Desktop's
resource-saver wake-up timeout (updated that comment this session to also
mention `podman machine`). So there's *some* existing awareness of
transient-failure risk, just not the general TTL/backoff the finding asks
for.

**Fixes, all five bullets.** `cases.py`'s `_docker_checked` boolean became
`_docker_checked_at: float` (a monotonic timestamp, -1 = never probed) with
a new `_ENGINE_HEALTH_TTL_S = 20.0`; `_docker_available(*,
force_recheck=False)` now re-probes once the TTL expires instead of caching
for the process lifetime, and `run_scenario()` calls it with
`force_recheck=True` once at the start of every scenario, so a
`--matrix-drivers`/`--matrix-models` sweep re-checks engine health for each
scenario rather than staying convinced it's down for the whole sweep
because an early scenario probed it mid-startup. New
`reset_engine_health_cache()` for callers that want an explicit reset.
Retry with backoff: the previous add-only `_pull_attempted: set[str]` (one
failed pull = never retried again this process) became `_PULL_MAX_ATTEMPTS
= 3` with per-image `_pull_attempts`/`_pull_last_attempt_at` dicts and
exponential backoff (`min(2**attempts, 30)` seconds) between attempts -
bounded, not infinite, so a genuinely unpublished/unreachable image still
gives up for real. Tag-result checking: `docker_image_pull()` now checks
`tag_proc.returncode != 0` after the `docker/podman tag` step (previously
unchecked - a failed tag silently reported success). Cleanup timeouts:
`DockerSandbox.stop()`'s `stop -t 2` call gained `timeout=15` with a
try/except fallback to `rm -f` (also `timeout=15`); the timeout-path `rm -f`
in `_run_check_command_docker` got the same treatment. New tests:
`EngineHealthTTLTests` (3 - within-TTL result is cached not reprobed,
`force_recheck` bypasses the TTL, an expired TTL triggers a reprobe).

#### F-17: CI does not exercise the complete distribution

**Priority:** Medium
**Area:** CI coverage
**Location:** `.github/workflows/`

Current CI has good unit-test OS coverage and a corpus verification job, but it
does not cover:

- installation and execution from the built wheel;
- dashboard JavaScript behavior;
- Dockerfile linting;
- rootless Podman;
- ARM64 images;
- container image build cache behavior;
- current Python versions newer than 3.12 while the package declares only a
  lower bound.

**Recommended change**

- Add a wheel-install smoke-test job.
- Add a lightweight dashboard browser test.
- Add Dockerfile lint/build checks for changed image contexts.
- Add one Linux rootless-Podman job.
- Add Python 3.13 and later supported versions.
- Use Buildx cache exports/imports in the image publishing workflow.

**Review (verified against code): CONFIRMED, read the full CI workflow
directly.** `unit-tests`' matrix is exactly `python-version: ["3.10",
"3.11", "3.12"]` while `pyproject.toml` declares `requires-python = ">=3.10"`
(lower bound only, implying 3.13+ is claimed-supported but untested) -
confirmed. No wheel-install job, no dashboard/JS test step, no rootless-Podman
job, no ARM64 job, and no Dockerfile-lint step existed anywhere in
`ci.yml`/`publish-images.yml` - confirmed by reading both files in full. One
addition specific to this round's changes: since this session added real
Podman support to the tool, "no CI job actually exercises Podman" was
a slightly sharper gap than when the audit was likely written against the
still-Docker-only surface - the CI matrix had zero coverage of the
newly-added `container_engine()` auto-detection/override logic under an
actual Podman install.

**Fixes.** All six recommended bullets except the dashboard browser test.
`wheel-smoke` job: builds a real wheel (`python -m build --wheel`), installs
it into a clean venv, and smoke-tests the installed CLI
(`optarena --help` + `optarena cases validate`) - verified locally first
(built, installed into a throwaway venv, ran both commands successfully)
before writing the CI job. `dockerfile-lint` job: runs `hadolint` (pinned by
commit SHA, resolved live against GitHub's API) against all 9 Dockerfiles
in a matrix, with a new `.hadolint.yaml` deliberately ignoring exactly two
rules with a documented reason each (DL3008 apt-version-pinning, deferred
per F-13's `known_gaps`; DL3003, a false positive against
`docker/Dockerfile`'s intentional subshell `cd`) - verified by running
hadolint locally against every Dockerfile both before (2 warnings each,
would have permanently failed the gate) and after the config (clean exit 0
on all 9). `images-lock-check` job: runs `docker/check_images_lock.py` (see
F-13). Rootless Podman: new `rootless-podman` job installs `podman`+`uidmap`
via apt and runs `optarena cases verify --language shell` under
`OPTARENA_CONTAINER_ENGINE=podman` - deliberately scoped to the 16
shell-tagged cases (base image only) rather than the full corpus, so this
stays a fast per-PR job; `verify-corpus` already covers full-corpus breadth
under Docker. Python 3.13: added to `unit-tests`' matrix (was
`["3.10","3.11","3.12"]` against a `requires-python = ">=3.10"` lower bound
with no upper bound tested). Buildx cache exports/imports for
`publish-images.yml`: not implemented - the workflow gained multi-platform
Buildx (see F-14) but not layer caching. Not done: the dashboard
JavaScript/browser test - no browser-automation tooling was added this
session.

#### F-18: Operational visibility is mostly console output

**Priority:** Medium-Low
**Area:** Observability and automation
**Location:** CLI and runner

The CLI prints useful human-readable status, but there is no structured event
stream, log level, quiet mode, or machine-readable live progress.

**Recommended change**

- Add `--log-level`, `--quiet`, and `--json-events`.
- Emit lifecycle events such as `run_started`, `case_started`,
  `case_completed`, `checkpoint_saved`, and `run_completed`.
- Record agent, oracle, container-startup, and persistence time separately.
- Keep human console output as the default.

**Review (verified against code): CONFIRMED.** Grepped `cli.py` for
`log-level`/`quiet`/`json-events` (and underscore variants) - zero matches.
Every status line in `runner.py`/`cli.py` was a plain `print()` to stdout/stderr
with no level, no structured fields, no way to consume progress
programmatically short of scraping text output. Accurate as stated; this was
the lowest-severity finding in the report (a usability/integration gap, not
a correctness or durability one) and the audit's own priority label
(Medium-Low) reflects that appropriately.

**Fixes.** New `optarena/events.py` module (`RunEvents`): `.say()` for the
existing human console lines (suppressed under `--quiet`), `.detail()` for
secondary per-case lines (additionally dropped at `--log-level warn`/
`error`, keeping only the PASS/FAIL headline and failure attribution), and
`.emit()` for the five named structured lifecycle events (`run_started`,
`case_started`, `case_completed`, `checkpoint_saved`, `run_completed`; one
JSON object per line on stdout, opt-in via `--json-events`). A
default-constructed `RunEvents` (what every pre-existing `run_scenario()`
caller gets, since `events` is an additive optional parameter) behaves
exactly like the old unconditional `print()` calls - human output stays the
default, nothing changes for a caller that doesn't opt in. `cli.py`'s `run`
subcommand gained `--log-level {debug,info,warn,error,quiet}`, `--quiet`,
and `--json-events`; `cmd_run` constructs one `RunEvents` shared across
every scenario in the invocation (a single run or a full
`--matrix-drivers`/`--matrix-models` sweep) and threads it through
`run_scenario()` and its own post-run summary/comparison prints.
`run_scenario()`/`_print_result()`/`_run_parallel()`/`_worker_loop()` were
all updated to route every console line through `events.say()`/
`.detail()` and to emit the five lifecycle events at the right points -
`case_started` fires from the worker thread itself under `--parallel`
(concurrently across workers; `print()`/`RunEvents.emit()` are safe to call
from multiple threads since CPython serializes writes to one file object).
Not done: recording agent/oracle/container-startup/persistence time
*separately* in the event payloads - the events carry status, duration, and
summary data, but not that phase-by-phase timing breakdown. New tests:
`RunEventsTests` (7 - default prints unconditionally, `--quiet` suppresses
both `.say()`/`.detail()`, `--log-level warn` drops `.detail()` but keeps
`.say()`, `--log-level quiet` equals `--quiet`, an invalid log level is
rejected, `.emit()` only fires under `--json-events`, `--json-events`
without `--quiet` produces both streams) and
`RunScenarioLifecycleEventsTests` (a full `run_scenario()` call under
`quiet=True, json_events=True` asserts all five events fire in the
documented order and every line of captured stdout parses as JSON - proving
`--quiet` leaves no stray human text interleaved with the event stream).

### Performance observations

Measured on the audit machine:

- 510 built-in cases.
- Approximately 2.43 MiB of case JSON.
- Full corpus loading and structural validation averaged about 70 ms.
- 501 cases have one prompt; seven have two prompts; two have three prompts.

Case JSON parsing is not a meaningful performance bottleneck at the current
scale. The likely sources of perceived lag are:

1. model or tool execution;
2. container startup in parallel/custom-pack mode;
3. heavyweight language warmups and writable cache initialization;
4. repeated full-workspace hashing after prompts;
5. copying a whole workspace for mid-session behavioral checks;
6. rebuilding the complete results index after every run.

Optimization should focus on those areas before adding complexity to case
loading.

**Review (verified against code): the corpus-shape numbers are exactly
right; the timing number doesn't reproduce but the conclusion still holds.**
Independently re-measured: 510 case files, 2.42 MiB total (audit says 2.43
MiB - rounding/methodology, not a discrepancy), prompt-count distribution
`{1: 501, 2: 7, 3: 2}` - all three match the audit exactly. `load_cases()`
wall time on this machine measured 160ms, not the audit's ~70ms - almost
certainly just a different machine/cold-vs-warm-disk-cache/first-import-overhead
difference, not an error in the audit's method, and it doesn't change the
qualitative conclusion either way: 160ms is still nowhere near a real
bottleneck next to model/container execution time. The six listed "likely
sources of perceived lag" are consistent with everything else verified in
this review (container startup dominates under F-06's ephemeral-container
fallback; full-workspace hashing and copying are real per the driver code
read for F-05/F-01).

### Docker and Podman improvement plan

#### Runtime

- Keep the existing shared-container optimization for serial built-in runs.
- Introduce isolated worker-container pools for parallel runs.
- Add engine-health TTLs and bounded retries.
- Add cleanup timeouts and post-cleanup verification.
- Record container startup, execution, and cleanup durations.
- Add a configurable Podman SELinux mount mode for Linux environments that
  require relabeling.

#### Images

- Build multi-platform images with Buildx.
- Use `TARGETARCH` for downloaded binaries.
- Add dependency lockfiles and an image lock manifest.
- Add GitHub Actions layer caching.
- Publish immutable version and commit tags alongside `latest`.
- Allow scenarios to select an immutable tag for every language image.

#### Distribution

OptArena is a local CLI rather than a multi-service server, so Docker Compose
is not required for its core operation. If a containerized OptArena CLI is
published later, document how it accesses:

- the user-provided scenario and case directories;
- the local results directory;
- the model backend;
- the host Docker or Podman engine used for nested verification.

### Recommended implementation sequence

#### Phase 1: Failure safety

1. Move preparation into the cleanup lifecycle.
2. Add per-case run checkpoints.
3. Normalize expected CLI exceptions.
4. Add cleanup timeouts.
5. Add explicit infrastructure-error statuses.

#### Phase 2: Resource control and performance

1. Bound process and HTTP output.
2. Add whole-case deadlines.
3. Implement container worker pools.
4. Separate agent, oracle, startup, and persistence timing.
5. Make result indexing incremental and concurrency-safe.

#### Phase 3: Reproducible containers

1. Add image and dependency lockfiles.
2. Add multi-architecture builds.
3. Add Buildx cache support.
4. Add per-language immutable image selection.
5. Add rootless-Podman CI.

#### Phase 4: Distribution and maintainability

1. Package all required assets.
2. Add wheel-install smoke tests.
3. Add dashboard tests.
4. Add structured progress events.
5. Update architecture documentation and packaging metadata.

### Verification completed during the audit

- `201/201` unit tests passed with the repository's CI-equivalent
  `OPTARENA_NO_DOCKER=1` configuration.
- All 510 built-in cases passed structural validation.
- Python bytecode compilation completed successfully.
- Wheel and source-distribution builds completed successfully.
- The package build reported setuptools deprecation warnings for the current
  license metadata.
- Docker was installed, but the daemon was not running.
- Podman was not installed.
- Container image builds and the complete behavioral corpus could therefore
  not be rerun locally during this audit.

**Review note:** worth flagging that this environment constraint means every
Docker/Podman-specific finding (F-06, F-13 through F-17) was derived from
reading code and workflow files, not from actually exercising a
build/run/pull cycle - which is an honest and correctly-disclosed limitation,
not a knock against the findings themselves (independently confirmed all
of them by reading the same source). Separately: this audit predates (or was
run concurrently with) this session's addition of real Podman support to the
tool - Docker and Podman are both installed and working on this machine as
of this review (verified via a live `podman machine init/start` and a real
`optarena cases verify` run under `OPTARENA_CONTAINER_ENGINE=podman`), so
the container-engine-specific findings (F-14, F-16, F-17's Podman-CI gap)
are current and actionable, not blocked by tooling availability the way
they were when this audit ran.

### Suggested completion criteria

The project can reasonably be called operationally robust when:

- an interrupted run preserves every completed case;
- expected user errors never print a traceback by default;
- subprocess and HTTP memory usage has explicit bounds;
- result persistence is safe under concurrent runs;
- parallel execution reuses bounded worker containers;
- trial and duration metrics describe actual work consistently;
- Docker and Podman paths are covered by real CI;
- published images support AMD64 and ARM64;
- evaluations can pin every sandbox image before execution;
- a wheel installation supports the dashboard, Dockerfile lookup, and
  repository-scale cases without requiring the original source checkout.

### Addendum: user-facing naming cleanup (not one of the 18 findings)

While implementing F-15's Podman-aware image handling, a separate
naming-quality issue surfaced: several user-facing names were
Docker-specific artifacts of a Docker-only past, or used a
double-negative-prone `NO_X` pattern that reads badly regardless of the
Docker/Podman question. Fixed alongside the findings above, with no
back-compat aliases (pre-release, no versioned release had shipped yet):

- Case-schema field `docker_image` -> `image` (renamed across all 470 case
  files that used it, via a minimal text-level substitution that preserved
  each file's original formatting rather than a full JSON re-serialization).
- `OPTARENA_DOCKER_IMAGE` -> `OPTARENA_SANDBOX_IMAGE`.
- `OPTARENA_NO_DOCKER` -> `OPTARENA_DISABLE_SANDBOX` (also fixes a
  misleading name in its own right - it doesn't mean "don't use Docker,"
  it means "skip sandboxing entirely, run the oracle on the host").
- `OPTARENA_NO_PULL` -> `OPTARENA_DISABLE_PULL` (same `NO_X` pattern, found
  during a follow-up sweep specifically for other instances of it).
- Stale `optarena docker build`/`optarena docker pull` hints (in
  `cases.py`, `cli.py`, `README.md`, `ARCH.md`) updated to the canonical
  `optarena sandbox build`/`optarena sandbox pull` - the `docker` spelling
  still works as a documented legacy alias, but new hint text should point
  at the current name, not perpetuate the old one.

A full sweep of every `OPTARENA_*` env var, every CLI flag, and every
case/scenario/backend schema key found nothing else in this category -
internal (non-user-facing) Python identifiers like `docker_image_available`/
`docker_image_pull`/`DockerSandbox` were deliberately left alone (accurately
named, never typed by a user). See `CHANGELOG.md`'s `[Unreleased]` section
for the user-facing summary of this change.

---

## Round 4 (2026-07-26): Security/Coverage/Utility Audit (B/S/D/C/U/P)

_Original file: `AUDIT_2.md` ("OptArena Second Audit - Code, Security, Coverage and Utility")._

**Date:** 2026-07-26
**Branch/commit:** `v0.1` @ `b6cffd1` (working tree clean)
**Scope:** the whole `optarena/` repository excluding the nested, separately
tracked `website/`, `website2/`, `website-docs/` checkouts.
**Method:** full read of every Python source file (3,189 statements across 28
modules), the 2,895-line test suite, all 10 Dockerfiles, both CI workflows, the
593-line dashboard, the 510-case corpus (parsed and analysed programmatically),
and every root-level document. Every behavioural claim below marked
**[verified]** was reproduced by executing code against this checkout, not
inferred from reading.

This is a *second*, independent audit. Round 3 above documents an earlier external review
(F-01 .. F-18) whose fixes are largely in place; nothing here restates those
findings. Where a prior fix is incomplete or where a prior fix *introduced* a
new defect, it is called out explicitly. Note: this round uses its own `B-*`/`C-*`
ID prefixes for bugs/corpus findings, which are a different namespace from
Round 1's `B-*`/`C-01..` IDs above - not the same findings, just reused letters.

### 1. Executive summary

The codebase is in unusually good shape for its stage: stdlib-only core, a real
container-sandboxed oracle, 240 passing tests, a clean `ruff` gate, digest-pinned
base images, and genuinely careful reasoning captured in comments. The
architecture is sound and the security posture is above average for a tool in
this category.

That said, this audit found **28 issues**, including two High-severity
correctness defects introduced by the F-02/F-06 parallelism work, one High
security issue that directly contradicts a written guarantee in `SECURITY.md`,
and a class of gaps in the six SDK drivers that quietly makes their results
non-comparable with the CLI drivers they are meant to be benchmarked against.

| Severity | Count | Headline items |
|---|---|---|
| High | 4 | Parallel checkpoints lose all work (B-1), parallel worker exception deadlocks the run (B-2), SDK drivers have no timeout or disruption support (B-3), `setup_repo` path traversal (S-1) |
| Medium | 7 | API key in `aider` argv (S-2), unvalidated image name in `docker run` argv (S-3), root+DAC_OVERRIDE sandbox (S-4), false regressions in the CI gate (B-4), legacy aliases broken by global flags (B-5), 44 non-discriminating corpus cases (C-3), packaging writes into `site-packages` (P-1) |
| Low | 9 | Unredacted keys in historical run files (S-5), dashboard `innerHTML` (S-6), lock stealing (S-7), HTML entity truncation (B-6), case-insensitive-only regex oracle (B-7), and others |
| Stale/dead | 9 | `reset_engine_health_cache`, a CLI command referenced in three docstrings but never implemented, a CI job installing Node for a test file that does not exist, and more |

Nothing here is an emergency, but B-1 and B-2 silently undermine the two
features (`--parallel` and checkpointing) most likely to be used on a long
corpus run, and S-1 breaks a guarantee the project has published.

### 1a. Remediation status (updated 2026-07-26, same day)

**Everything in this document except the corpus-content findings has been
fixed.** The two deferred items are corpus *content* work (writing 44 missing
`broken_solutions`, deepening the multi-turn/difficulty distribution), held
deliberately for a separate pass; the *tooling* that surfaces them shipped
(`optarena cases verify --strict` names them and fails on them).

| ID | Status | Where |
|---|---|---|
| B-1 parallel checkpoints lose work | **fixed** | `runner.py` `_run_parallel(results=...)`; `ParallelDurabilityTests` |
| B-2 worker exception deadlocks | **fixed** | `runner.py` `_worker_loop` + collector liveness check |
| B-3 SDK drivers: no timeout/disruptions/telemetry | **fixed** | new `drivers/sdk_base.py`; all 6 drivers rewritten; `SDKDriverBaseTests` |
| B-4 false regressions | **fixed** | `compare.py` `is False` + `missing_in_{a,b}_cases` |
| B-5 legacy aliases behind a global flag | **fixed** | `cli.py` `_rewrite_legacy_argv` |
| B-6 escape-then-truncate | **fixed** | `report.py` |
| B-7 no case-sensitive assertions | **fixed** | `case_sensitive: true` on an expected-file spec |
| B-8 `_docker_warned` never reset | **fixed** | `reset_engine_health_cache()` |
| B-9 pricing cache | **fixed** | keyed on the resolved path + `clear_pricing_cache()` |
| B-10 verify bypasses `resolve_image` | **fixed** | `verify.py` |
| S-1 `setup_repo` traversal | **fixed** | source containment + schema rejection; `SetupRepoContainmentTests` |
| S-2 API key in aider argv | **fixed** | passed via `subprocess_env`; `ApiKeyNeverInArgvTests` |
| S-3 unvalidated image reference | **fixed** | `validate_image_ref` + schema; `ImageReferenceValidationTests` |
| S-4 root sandbox / unusable non-root opt-in | **fixed** | uid 1000 account + reachable caches in all 9 images, workspace permission relaxation, `sandbox-nonroot` CI job |
| S-5 unredacted keys in old records | **fixed** | `optarena runs scrub-secrets` |
| S-6 dashboard escaping | **fixed** | `esc()` covers `'`; `num()`/type-safe formatters; `DashboardEscapingTests` |
| S-7 lock stealing | **fixed** | `_IndexLock.acquired`; `IndexLockOwnershipTests` |
| S-8 pack provenance | **documented** | `SECURITY.md` now states the hash is not a signature |
| D-1..D-9 dead/stale code | **fixed** | all removed or made real (see section 4) |
| C-1 CLI untested | **fixed** | new `tests/test_cli.py`, 42 tests |
| C-2 no driver `run_case` test | **fixed** | stub HTTP backend; baseline + SDK-base suites |
| C-3 44 non-discriminating cases | **tooling fixed, content deferred** | `cases verify --strict`; corpus edits are the deferred pass |
| C-4 corpus depth | **partially resolved** | corpus content (disruptions, difficulty-3 depth, difficulty-4/5 breadth all addressed; overall D4/5 volume still open) |
| C-5 no JS/Python conformance test | **fixed** | new `tests/test_conformance.py` (mutation-checked) |
| U-1..U-5 CLI gaps | **fixed** | `--version`, `runs rebuild-index/prune/scrub-secrets`, `serve --host`, `report --out-dir`, `doctor --json` |
| P-1 results in `site-packages` | **fixed** | `~/.optarena/results` outside a checkout |
| P-2 wheel-missing assets invisible | **fixed** | `doctor` reports `dashboard/`, `docker/`, `repos/` |
| Docs (section 8) | **fixed** | README, SECURITY.md, CHANGELOG, CI comment, code comments |

Two defects were found *by the new tests* while writing them and are also
fixed: `cases verify --cases <typo>` raised a raw `FileNotFoundError`
traceback (A-34), and the suite was depositing ~13 run records per invocation
into the developer's own `results/runs/` (A-32).

Verification: **350 tests pass** (was 240), `ruff` clean, and the non-root
sandbox work was validated against real Docker 28 - base, JVM (relocated
Maven repo), .NET (relocated NuGet cache) and Rust tracks all verify
end-to-end both as root and as uid 1000.

**Note added during consolidation:** the corpus-content items deferred here
(C-3, C-4) have since been substantially advanced by the Execution &
Corpus Development Log below (sections 17-22) - C-3 is now fully resolved
and C-4 is partially resolved, as summarized in "Current state" at the top
of this file.

### 2. Security findings

#### S-1 (High) - `setup_repo` copies an arbitrary host directory into the model-visible workspace

**Where:** [cases.py:1235-1255](optarena/cases.py#L1235-L1255), schema at
[schema.py:149-150](optarena/schema.py#L149-L150)

`copy_setup_repo()` containment-checks the *destination* of every copied file
but never the *source*:

```python
src = REPOS_DIR / repo_name        # repo_name comes straight from case JSON
if not src.is_dir(): raise FileNotFoundError(...)
```

The schema only requires `setup_repo` to be a string. A case declaring
`"setup_repo": "../../.."` walks out of `repos/` and recursively copies whatever
it finds into the case workspace, which is then handed to the agent as context
and, for the baseline/SDK drivers, read back into the prompt sent to the backend
URL. That is an arbitrary-host-file-read-to-remote-endpoint primitive driven by
case content.

**[verified]** `copy_setup_repo(tmp, "../optarena/cases")` copied all 510
catalogue files into a fresh workspace, no error raised.

The in-code comment asserts the input is safe:

> `repo_name` itself is trusted (a project-controlled directory name, not case JSON)

That is factually wrong - it is read from case JSON at
[cases.py:1392-1393](optarena/cases.py#L1392-L1393), and case JSON is
installable from a URL via `optarena cases install https://...`. It also
contradicts `SECURITY.md`:

> All case-supplied file paths are containment-checked before anything is
> written; traversal (`../`), absolute paths, and symlinked escapes are rejected.

**Fix:** resolve `src` and require `REPOS_DIR` containment, and reject separators
in the schema:

```python
src = (REPOS_DIR / repo_name).resolve()
if not src.is_relative_to(REPOS_DIR.resolve()):
    raise ValueError(f"setup_repo escapes the repos directory: {repo_name!r}")
```
plus a `^[A-Za-z0-9._-]+$` check in `validate_case`. Add a regression test
alongside the existing `write_setup_files` traversal tests.

#### S-2 (Medium) - the `aider` driver puts the backend API key on the command line

**Where:** [aider_cli.py:116-125](optarena/drivers/aider_cli.py#L116-L125)

```python
cmd = [self._aider, "--openai-api-base", backend.openai_base,
       "--openai-api-key", backend.api_key, ...]
```

Process arguments are world-readable on Linux (`/proc/<pid>/cmdline`) and
visible to any user session on Windows. The project already knows this - the
CLI's own flag help says so at [cli.py:850-852](optarena/cli.py#L850-L852)
("argv is visible in `ps`/shell history, so a real key should come from the
environment"), and `SECURITY.md` repeats the advice. The `aider` driver is the
one place that then does exactly the opposite, and it is the only `stable`
non-baseline driver, so it is the most likely to be pointed at a paid endpoint.

**Fix:** pass the key through the subprocess environment instead. `aider` reads
`OPENAI_API_KEY`/`OPENAI_API_BASE`, and `subprocess_env(extra=...)` already
exists for precisely this (`cli_agents.py` uses it). One-line change:
`env = subprocess_env(_scenario_openai_env(backend))` and drop the two flags.

#### S-3 (Medium) - a case's `image` field is interpolated into the `docker run` argv unvalidated

**Where:** [cases.py:744](optarena/cases.py#L744),
[cases.py:984-995](optarena/cases.py#L984-L995),
[cases.py:591-596](optarena/cases.py#L591-L596)

`case["image"]` (any string per the schema) is placed into the container
command line ahead of `sh -c cmd`. `docker run` parses options up to the first
non-option token, so a value beginning with `-` is consumed as a *flag*, not an
image name (`--privileged`, `--pid=host`, `--volume=/:/host` are all single
tokens). Full exploitation is awkward because the token after it (`sh`) then
becomes the image reference and must exist, but this is an option-injection
surface into the one command that is supposed to be the security boundary, and
it costs nothing to close.

The same applies to `OPTARENA_SANDBOX_IMAGE` and to scenario `image_overrides`
(both operator-controlled, so lower risk) and to `docker_image_pull()`, which at
least prefixes GHCR.

**Fix:** validate every resolved image reference against a conservative pattern
(`^[a-z0-9][a-z0-9._/-]*(:[A-Za-z0-9._-]+)?(@sha256:[0-9a-f]{64})?$`) in
`resolve_image()`, and reject anything starting with `-`.

#### S-4 (Medium) - sandbox containers run as root with `CAP_DAC_OVERRIDE` and no image provides a non-root account

**Where:** [cases.py:102-142](optarena/cases.py#L102-L142),
[cases.py:162-173](optarena/cases.py#L162-L173), all 10 Dockerfiles

The hardening set is otherwise strong (`--cap-drop ALL`, `no-new-privileges`,
`--pids-limit 256`, `--read-only`, `--network none`, memory/CPU caps). But:

- every image runs as uid 0 (no `USER` directive anywhere in `docker/`),
- `DAC_OVERRIDE` was restored in `b6cffd1`, which lets that root bypass file
  permission checks on the bind-mounted run root,
- for the built-in corpus the *entire run root* is mounted, so one case's
  model-generated code can read and rewrite every other case's workspace
  (acknowledged as C-05 for packs in Round 1, but it applies to the built-in corpus by
  design),
- `OPTARENA_SANDBOX_USER` exists as the mitigation but is unusable as shipped:
  no image creates a matching account, and the docstring itself says each
  toolchain still needs validation under a non-root uid.

Impact is bounded by `--network none` and the read-only rootfs, and the threat
model ("only load packs you trust") is stated. Still, "the model's code cannot
tamper with grading" is not currently true within a run.

**Fix (staged):** add a `optarena` uid 1000 account plus writable cache dirs to
each image, run `cases verify --language <x>` per track under it, then flip
`--user` on by default. Shorter term, drop `DAC_OVERRIDE` and instead
`chmod 0777` the per-case workspace directories the runner creates (the reason
`DAC_OVERRIDE` was needed at all is host-side 0700 `mkdtemp` permissions).

#### S-5 (Low) - historical run records on disk still contain unredacted `backend.api_key`

**[verified]** 27 of 297 files under `results/runs/` carry a non-null
`scenario.backend.api_key` (values `optarena` and `selfopt` in this checkout -
placeholders, but the *format* leaked). These predate the `redact=True` fix at
[runner.py:490-493](optarena/runner.py#L490-L493).

`results/` is correctly gitignored, so nothing is published, but
`optarena serve` exposes the whole results directory over HTTP to any process on
the host, and there is no scrub path. `SECURITY.md`'s "`backend.api_key` is
**never persisted**" is true going forward but not of what is on disk.

**Fix:** a one-shot `optarena runs scrub-secrets` (or fold it into the
rebuild-index command proposed in D-2) that rewrites `api_key` to null and sets
`api_key_set` on legacy records.

#### S-6 (Low) - dashboard renders run-record fields through `innerHTML` with partial escaping

**Where:** [dashboard/index.html:290](dashboard/index.html#L290),
[385-387](dashboard/index.html#L385-L387),
[304-328](dashboard/index.html#L304-L328)

Strings are consistently passed through `esc()`, which is good. Numeric fields
are not: `st.i`, `st.completion_tokens`, and the summary counters in
`tilesHTML` are interpolated raw, and the tooltip body is injected with
`tip.innerHTML = el.dataset.tip`. A run JSON with a string where a number is
expected (a hand-edited or shared results directory, or a future driver that
records `"i": "<img onerror=...>"`) yields script execution in the dashboard
origin. Also note `esc()` does not escape `'`; that is covered today only by an
ad-hoc `.replace(/'/g,"&#39;")` on the assembled tooltip string, which is easy
to forget at the next call site.

**Fix:** `esc()` everything interpolated, add `'` to the escape map, and drop
the per-site quote replacement. Better still, build the tooltip with
`textContent`.

#### S-7 (Low) - `_IndexLock` deletes a lock file it never acquired

**Where:** [store.py:75-94](optarena/store.py#L75-L94)

On wait-deadline expiry `__enter__` returns `self` without holding the lock (a
deliberate "never hang a run" choice, fine), but `__exit__` unconditionally
unlinks the lock path. Process B therefore removes process A's still-valid
lock, after which a third process acquires it while A is mid-update. The
failure mode is a lost `index.json` entry, which is exactly what the lock exists
to prevent.

**Fix:** track `self.acquired` and only unlink when true.

#### S-8 (Informational) - pack installation has integrity but no provenance

[packs.py:119-178](optarena/packs.py#L119-L178) validates schema, filename
collisions, declared count and the content hash - but the hash is *self*
declared, so it detects corruption, not tampering. Combined with S-1/S-3, a
malicious pack URL is the highest-leverage untrusted input in the tool. Worth
an explicit note in `SECURITY.md`, and eventually detached signatures.

### 3. Correctness bugs

#### B-1 (High) - `--parallel` checkpoints always record zero cases; an interrupted parallel run loses everything

**Where:** [runner.py:562-591](optarena/runner.py#L562-L591)

```python
results: list[CaseResult] = []
...
if parallel > 1:
    def _on_result(result):
        ...
        _checkpoint(results)          # closes over run_scenario's `results`
    results = _run_parallel(...)      # ...which is only rebound AFTER the run
```

`_run_parallel` accumulates into its own local list; `run_scenario`'s `results`
stays `[]` for the entire parallel run. Every checkpoint therefore serialises
zero cases, `checkpoint_saved` reports `cases_completed: 0`, and - the part that
actually costs money - the `finally` block's
`record.cases = [r.to_dict() for r in results]` also sees `[]`, so a Ctrl-C or
crash during a parallel run discards **every completed case**. This defeats
Round 3's F-02 precisely under the configuration where a run is longest and an
interruption most likely.

**[verified]** 4 cases at `--parallel 3`: checkpoint case counts were
`[0, 0, 0, 0]`; the final record had 4 only because normal completion rebinds
`results`.

**Fix:** have `_run_parallel` append into a caller-owned list (pass `results`
in), or have `_on_result` receive the accumulated list.

#### B-2 (High) - an exception in a parallel worker deadlocks the run forever

**Where:** [runner.py:257-272](optarena/runner.py#L257-L272),
[runner.py:300-307](optarena/runner.py#L300-L307)

`_worker_loop` calls `_run_case` outside any `try/except`; the `finally` only
does `task_done()`. If `_run_case` raises (an `OSError` from `mkdir`/`rmtree`,
a `MemoryError`, anything a driver lets escape, or the `--security-scan`
scanner), the thread dies without putting a result. The collector loop is a
fixed-count blocking `results_queue.get()` with no timeout, so the main thread
waits forever. There is no run-level watchdog, so the process hangs
indefinitely - in CI, until the job timeout.

**[verified]** one worker raising `OSError` on case `c1` with `--parallel 2`:
run still alive after 15s, other cases completed, process hung.

**Fix:** wrap the worker body and push a synthetic error `CaseResult` on
exception, and give the collector `get(timeout=...)` with a liveness check on
the worker threads.

#### B-3 (High) - the six SDK drivers ignore timeouts, disruptions, and all telemetry

**Where:** `crewai_sdk.py`, `openai_agents_sdk.py`, `smolagents_sdk.py`,
`langgraph_sdk.py`, `autogen_sdk.py`, `semantic_kernel_sdk.py`

Every one of them has the same shape, and every one of them is missing what the
baseline and CLI drivers do:

| Behaviour | `openai-chat` / CLI drivers | SDK drivers |
|---|---|---|
| Honours `scenario.timeout` / `case["timeout"]` | yes, whole-case deadline (F-05) | **no - unbounded** |
| Fires `disruptions` between prompts | yes | **no** |
| Records `steps` / `n_steps` | yes | no |
| Records tokens / `cost_usd` | yes | no |
| Sets `execution_ok` | yes | no (always True) |
| `parallel_safe` | True | False |

Consequences:

1. **No timeout at all.** `case["timeout"]` is never read. A backend that
   accepts the connection and never responds hangs the run permanently; the
   corpus declares timeouts up to 900s that simply do not apply here.
2. **Disruption cases are silently mis-graded.** The 6 `*_dynamic` cases exist
   to measure adaptation to a mid-session environment change. Under an SDK
   driver the disruption never fires, so the agent faces an *easier* task and
   its result is not comparable to the same case under `aider` or
   `ollama-chat` - while `compare`/`regression` will happily rank them
   side by side, because manifest compatibility keys on the case set, not the
   driver's capabilities.
3. **`tokens_per_pass`, `steps_per_pass`, `total_cost_usd` and failure
   attribution are all `None`** for SDK runs, so `cheaper` degrades to
   "unknown" and the efficiency columns the README advertises are blank.

README's claim that CLI and SDK agents "run through the exact same case set,
oracle, and comparison output, so tool-vs-tool numbers are apples-to-apples" is
therefore only true for correctness, not for cost, path, or dynamic behaviour.

**Fix:** factor the common loop (already ~90% identical across all six files)
into a `SingleBlockSDKDriver` base holding the deadline, per-step record, and
`apply_disruptions` call, then let each subclass implement one `_complete(prompt)
-> (text, usage)` method. That removes roughly 300 duplicated lines and fixes
all six at once. Where the SDK cannot be interrupted, at minimum run it on a
worker thread with a deadline and record a timeout error.

#### B-4 (Medium) - `optarena regression` reports a case missing from run B as a regression

**Where:** [compare.py:172-173](optarena/compare.py#L172-L173) vs
[compare.py:90-92](optarena/compare.py#L90-L92)

```python
regressed = [r["case"] for r in cmp["cases"] if r["a_passed"] and not r["b_passed"]]
```

`b_passed` is `None` when the case is absent from B (`case_deltas` aligns by
name over the union), and `not None` is True. `compare_runs` gets this right
one file over, using `is False`. So the two code paths disagree, and the one
that disagrees is the **CI gate**: `cmd_regression` returns exit 1 on a
non-empty `regressed_cases`.

**[verified]** A={c1 pass, c2 pass}, B={c1 pass}: `regressed_cases == ['c2']`,
`n_discordant` 1 in the regression summary vs 0 in the comparison verdict.

Practical trigger: re-running a subset (`--cases`, `--language`) and comparing
against a full baseline. The compatibility banner does warn "different case
set", but the gate still fails with fabricated case names.

**Fix:** use `r["b_passed"] is False` (and the mirror for `improved`), and add
a `missing_cases` list so the real difference is reported honestly.

#### B-5 (Medium) - legacy command aliases break when any global flag precedes them

**Where:** [cli.py:769-802](optarena/cli.py#L769-L802)

`_rewrite_legacy_argv` skips over `--results-dir` and its value while locating
the command token, but `--debug` was added later and is not in that list, so the
scan `break`s on it and no rewrite happens.

**[verified]** `optarena --debug list runs` -> `argparse: invalid choice: 'list'`
(exit 2). `optarena list runs` works.

**Fix:** derive the skip set from the parser's own global options rather than
hardcoding `("--results-dir",)`, or simply skip any leading token starting with
`-` (plus a value for the known value-taking ones).

#### B-6 (Low) - HTML report escapes then truncates, which can split an entity

**Where:** [report.py:117-118](optarena/report.py#L117-L118)

```python
detail = "" if status == "pass" else escape(...)[:400]
```

Truncating after escaping can cut `&lt;` into `&l`, which renders as literal
text. Not exploitable (truncation cannot *create* markup) but it produces
visibly broken output on long failure strings. Swap the order:
`escape(text[:400])`.

#### B-7 (Low) - the content oracle cannot assert case-sensitivity

**Where:** [cases.py:296-311](optarena/cases.py#L296-L311)

`content` is lowercased, then `regex_patterns` are matched against it with
`re.IGNORECASE`. So a case can never require `class UserDTO` over `class
userdto`, nor `SELECT` over `select`, nor distinguish `MyClass` from `myclass` -
even with an explicitly case-sensitive regex. For 18 languages including
case-sensitive ones (Go exported identifiers, Java class names, C# properties),
that is a real expressiveness limit that silently accepts wrong-looking code at
the shape-check stage. The behavioural `check_command` still catches most of it,
but shape checks run first and gate the behavioural stage.

**Fix:** keep the lowercased copy for `content_patterns`/`not_content_patterns`
(existing semantics), and run `regex_patterns` against the *original* text
without `IGNORECASE`, or add an opt-in `"case_sensitive": true` to the spec.

#### B-8 (Low) - `_docker_warned` is a process global that is never reset

**Where:** [cases.py:355](optarena/cases.py#L355),
[cases.py:756-784](optarena/cases.py#L756-L784)

Round 3's F-16 fixed the *health* cache to re-probe per scenario, but the "no sandbox
available, refusing to run" warning still fires at most once per process. In a
`--matrix-drivers` sweep, scenarios 2..N produce refused check_commands with no
explanatory stderr line, only opaque `check_command refused` failures. Reset it
alongside the health cache in `run_scenario`.

#### B-9 (Low) - the pricing table is cached for the process lifetime

`_table()` is `@lru_cache(maxsize=1)` ([pricing.py:46](optarena/pricing.py#L46)),
so a `~/.optarena/pricing.json` edit or an `OPTARENA_PRICING` change mid-process
is ignored. Harmless for the CLI, surprising for library/embedded use and for
tests. Add a `clear_pricing_cache()` or key the cache on the resolved path.

#### B-10 (Low) - `verify.py` resolves images without `resolve_image()`

[verify.py:116-119](optarena/verify.py#L116-L119) reads `case["image"]` and
`OPTARENA_SANDBOX_IMAGE` directly instead of going through
`cases.resolve_image()`. Today `verify` has no scenario so no overrides are
active, but this is the one image-resolution site that does not share the common
path - it will drift the moment `cases verify --image-override` or similar is
added. Route it through `resolve_image()` now.

### 4. Stale and dead code

| ID | Item | Where | Note |
|---|---|---|---|
| D-1 | `reset_engine_health_cache()` | [cases.py:388-395](optarena/cases.py#L388-L395) | **Zero callers.** Its docstring claims "Called once per scenario by `runner.run_scenario` (F-16)" - `runner.py:476` actually calls `_docker_available(force_recheck=True)`. Delete it, or use it and delete the private-call. |
| D-2 | `optarena runs rebuild-index` | referenced at [store.py:67](optarena/store.py#L67), [128](optarena/store.py#L128), [208](optarena/store.py#L208) | The command **does not exist**. `optarena runs rebuild-index` -> `invalid choice`. Three docstrings promise a recovery path users cannot invoke; `rebuild_index()` is only reachable implicitly when `index.json` is missing. Either add the subcommand (three lines) or stop citing it. |
| D-3 | `Driver.timed()` | [base.py:133-137](optarena/drivers/base.py#L133-L137) | No callers anywhere. |
| D-4 | `Driver.caches_results` + the M-09 branch | [base.py:121](optarena/drivers/base.py#L121), [runner.py:454-464](optarena/runner.py#L454-L464) | No driver sets it; the comments say so twice. ~15 lines of runner logic and a manifest field (`runner_trials`) exist for a hypothetical. Tests cover it only via a synthetic fake. Keep it if a caching driver is planned, otherwise delete. |
| D-5 | Dead assignment in `resolve_pack` | [packs.py:247](optarena/packs.py#L247) | `d = root / _safe(ref.replace("@", "@"))` - a no-op `replace`, immediately overwritten two lines later. |
| D-6 | CI installs Node 20 for a nonexistent test file | [ci.yml:47-51](.github/workflows/ci.yml#L47-L51) | The comment cites `tests/test_conformance.py` as "the H-09 guarantee that the Python and JS oracles agree byte-for-byte" (Round 1's H-09). That file does not exist anywhere in the repo, and no conformance test exists. The dashboard *does* reimplement `mcnemar_exact_p`, `manifest_compatibility`, `case_trajectory` and `attribute_failure` in JS ([index.html:215-271](dashboard/index.html#L215-L271), [352-368](dashboard/index.html#L352-L368)) with no test keeping them in sync - so the guarantee is not merely untested, it is absent while being documented as present. Either write the suite or remove the Node step and the comment. |
| D-7 | `ensure_image` "retry" | [cases.py:515](optarena/cases.py#L515) | `docker_image_available(image) or docker_image_available(image)` - correct by accident (short-circuit) but reads as a copy-paste bug; extract a `_retry_once` helper or add a comment at the call. |
| D-8 | Unbounded pull bookkeeping | [cases.py:426-427](optarena/cases.py#L426-L427) | `_pull_attempts` / `_pull_last_attempt_at` grow without bound and are never reset between scenarios. Trivial in practice, but they are the same "process-global that outlives its scope" pattern Round 3's F-16 fixed elsewhere. |
| D-9 | Inverted claim in a docstring | [packs.py:257-259](optarena/packs.py#L257-L259) | "numeric semver compare, not string compare - `1.9.0` must sort above `1.10.0`". The intent (and the code, and the docstring at [packs.py:42-52](optarena/packs.py#L42-L52)) is the opposite: `1.10.0` must sort above `1.9.0`. |

Also worth noting: `results/cline_*` directories remain in the local results tree
from the removed IDE-automation drivers that `CHANGELOG.md` records as deleted.
Gitignored, so cosmetic only.

### 5. Test coverage

240 tests, all passing, `ruff` clean. Measured with `coverage 7.15.2` over
`OPTARENA_DISABLE_SANDBOX=1 python -m unittest discover tests`:

```
TOTAL                                      3189   1074    66%
```

| Module | Cover | Assessment |
|---|---|---|
| `scenario.py`, `events.py` | 100% | Complete. |
| `report.py`, `verify.py` | 99% | Complete. |
| `metrics.py`, `compare.py`, `drivers/__init__.py` | 91-96% | Strong. |
| `security.py`, `packs.py`, `schema.py`, `pricing.py`, `aider_cli.py` | 82-89% | Good; gaps are error branches. |
| `runner.py`, `store.py`, `cases.py` | 77-80% | Adequate, but see below - the parallel path is the weak spot and it is where B-1/B-2 live. |
| `cli_agents.py` | 73% | No test drives an actual CLI agent end to end. |
| **`cli.py`** | **33%** | Only `_rewrite_legacy_argv` and fragments are tested. `cmd_run`, `cmd_compare`, `cmd_regression`, `cmd_report`, `cmd_scan`, `cmd_doctor`, `cmd_serve`, `cmd_docker`, every `cases`/`runs` subcommand: **zero coverage**. |
| **`openai_chat.py`** | **28%** | `run_case` (the reference baseline driver, and the model for every other driver) is never executed by a test. |
| **All 6 SDK drivers** | **25-29%** | Only `prepare()`'s ImportError path. No `run_case` is ever executed, which is consistent with B-3 going unnoticed. |

#### C-1 (Medium) - the CLI surface is effectively untested

Every exit code the tool documents as a CI contract (`regression` returning
0/1/3, `scan` returning 1 on error-level findings, `run` returning 1 on any
failure) is asserted nowhere. B-5 (a broken alias path) would have been caught
by a single end-to-end `main(["--debug", "list", "runs"])` assertion.

**Fix:** a `CliSmokeTests` class calling `cli.main([...])` against a temp
`--results-dir` for each subcommand, asserting exit codes. Cheap - no driver or
container needed for `cases`/`runs`/`drivers`/`report`/`scan`/`compare`/
`regression`.

#### C-2 (Medium) - no test executes a driver's `run_case` against a fake HTTP backend

`openai_chat.OpenAIChatDriver.run_case` is the template every other driver
copies, and it holds the F-05 deadline logic, the code-block extraction, the
per-step record, and the disruption hook. A ~40-line `http.server`-based stub
backend would cover it and all six SDK drivers' shared behaviour, and would have
surfaced B-3.

#### C-3 (Medium) - 44 of 510 corpus cases have no failing variant

**[verified]** by parsing the corpus: 107 cases declare no `broken_solutions`;
of those, 63 are `bug_fix`/`refactoring`/`performance`/`security` and so get the
implicit `unmodified` must-fail variant, leaving **44 cases where
`cases verify` only ever proves the reference solution passes**. Nothing proves
the oracle can fail, which is exactly the class of corpus bug `verify.py`'s own
docstring says motivated it ("do-nothing refactors passing, a csproj glob failing
every correct solution").

The 44 are concentrated in `feature`/`devops` task types, including
`create_hello_world`, `create_factorial`, `create_health_endpoint_*` (all 6
languages), `add_github_actions_ci_*` (all 6), and `multi_prompt_session`.

This also makes README's blanket claim inaccurate:

> was hand-verified end-to-end - a correct reference solution passes, a broken
> one fails, through the real `--network none` container sandbox - before being
> counted as done

**Fix:** add one `broken_solutions` entry per case (a vacuous or subtly wrong
implementation), and then make it structural: have `cases verify` warn (or fail
under `--strict`) for any case with no expect-fail variant.

**[RESOLVED]** All 44 cases now have a `broken_solutions` entry - a single,
minimal, plausible bug crafted against that specific case's real oracle
(expected_files content/regex patterns, or check_command behavior), not a
blank/garbage file. Verified with the real sandboxed oracle, not assumed:
`optarena cases verify --strict` over all 44 cases reports 88/88 variants
correct (44 references pass, 44 broken variants fail) - zero violations.
Corpus-wide, `cases_without_failing_variant()` now returns 0 (was 44).
Full detail, including a real infrastructure bug found and fixed while doing
this (Rust's shared `CARGO_TARGET_DIR` silently serving a stale binary
instead of rebuilding), is in the Execution Log's section 17.

The structural gate (`cli.cmd_verify_corpus`, so a future case can't be
added without a discriminating variant) was already wired in before this
pass - `--strict` already exits 1 if `cases_without_failing_variant` is
non-empty. Confirmed by the fact that `optarena cases verify --strict` with
no `--cases` filter now exits 0 cleanly across the full 510-case corpus.
Both halves of this finding are resolved.

**Note added during consolidation:** independently re-verified live against
the current 836-case corpus for this consolidation (not merely trusted from
this text) - still zero cases outside the implicit-fail task types lack a
`broken_solutions` entry. See "Current state" at the top of this file.

#### C-4 (Low) - the corpus is thin exactly where the product differentiates

| Dimension | Reality (original) | Reality (current, this round) |
|---|---|---|
| Prompts per case | 501 of 510 single-prompt; 7 have two, 2 have three | **~510 of 836 single-prompt; ~100 multi-prompt** |
| `disruptions` | 6 cases | **100 cases (94 new, across all 18 languages)** |
| `difficulty` | 1: 157, 2: 318, 3: 35 - nothing at 4 or 5 | 1: 168, 2: 387, 3: 170, **4: 72, 5: 39** |
| `test_kind` | unset on 500 of 510 | unset on the large majority (unchanged) |

**[PARTIALLY RESOLVED, multi-phase.]** Phase 1 (session-earlier): every
language gained at least one disruption case (6 -> 18, 1.2% -> 3.4%) - see
prior revision of this note. Phase 2 (this pass, in response to "should we
increase it further to maybe 10-20%?" / "get to 100 cases"): expanded from
18 to **100 disruption cases**, allocated by existing corpus weight rather
than flat per-language volume - python +9 (1->9), javascript +8 (1->8),
csharp/go/java/rust/typescript +7 each (1->7), kotlin/php/ruby/sql/yaml +5
each (1->5-6), c +4 (1->4), cpp/hcl/shell +3 each (1->2-3), dockerfile/
makefile brought up from 1 to 5 each (previously the thinnest, single-case
tracks - a corpus can't discriminate anything statistically from n=1).

Six reusable scenario archetypes (not "config value swap" repeated 82
times): dependency-removed, reverted-fix (a `bug_fix` case where the
disruption UNDOES the agent's own prior fix, the most direct test of "the
environment shifts mid-task"), renamed-symbol (reactive), moved-module/
namespace/package, requirements-drift (a spec file changes - the only
archetype usable for declarative languages with no runtime to read a value
at: SQL/HCL/YAML/Dockerfile/Makefile), and compounding (two disruptions at
one boundary, rust only). Full breakdown, the two real design bugs found
and fixed (a TypeScript `@types/node` gap, and a broken variant that
accidentally computed the SAME result as the reference and would have
silently passed the oracle - caught by verification, not shipped), and the
Rust `CARGO_TARGET_DIR` fix applied proactively this time (from the
Execution Log's section 17's earlier finding) are all in the Execution
Log's section 19.

Coverage: **100/604 (16.6%)**, up from 18/522 (3.4%) before this pass and
6/510 (1.2%) originally. Every language now has enough disruption cases
(2-9) to compute a real per-language signal rather than one anecdotal data
point. Verified with the real sandboxed oracle in per-language batches as
each was written, then a full combined pass over all 94 newly-added case
files together: **213 variants checked, 0 violations.**

16.6% is still a minority of the corpus and not claimed as a final target -
this closed the "many languages have zero data" gap, which was the specific
problem identified, not the broader "how much of the corpus should be
dynamic" question.

**Phase 3 (difficulty-4/5 ceiling): [PARTIALLY RESOLVED].** Relabeling
existing difficulty-3 cases was explicitly rejected - it would reintroduce
the same "claiming something the data doesn't back up" problem the
disruption work was fixing. Instead, 6 genuinely harder cases were added,
defined as repo-scale (`setup_repo`, the agent edits a real 20-30 file
starter app, not writes from scratch) combined with either a real
cross-cutting constraint (difficulty 4) or that plus a multi-turn
disruption (difficulty 5) - a combination nothing in the corpus had before,
since the two existing repo-scale fixtures (`fastapi-tasktracker`,
`express-ts-shortlink`) were both single-prompt, zero-disruption, capped at
difficulty 3. A third repo-scale fixture, `repos/springboot-tasktracker/`
(Java/Spring Boot, in-memory repositories rather than JPA+H2 since only
`spring-boot-starter-web`/`-test` are warmed in the offline Maven cache
`--network none` requires), was added alongside it, verified standalone
before any case was written against it. One D4 ("labels" feature, a
filter that must compose with existing filters) and one D5 ("filter fix"
bug_fix, reverted mid-task via the reverted-fix archetype from Phase 2)
per fixture - 6 cases, 15 variants, 0 violations. Full design detail in
the Execution Log's section 20.

Coverage summary: **100/604 (16.6%)** disruption coverage, up from 18/522
(3.4%) before this pass and 6/510 (1.2%) originally.

**Still open, not touched in any pass at the time this round was written:** difficulty 4/5 (111 cases, 72+39)
remain a small fraction next to 168/387/170 at levels 1-3 - every language
now has real per-language D4/D5 signal (4 and 2 respectively), which
closes the "confined to three languages" gap Phase 3 left open, but does
not close the overall volume gap, and expanding it further is still
expensive (repo-scale infrastructure per fixture) rather than an
oversight. The `test_kind` gap is untouched - see the original priority-2
recommendation for that work.

**Note added during consolidation:** the phases referenced above as "not
yet done" at the time (difficulty-3 depth, difficulty-4/5 volume) were
subsequently completed - see the Execution Log's sections 21-22 and
"Current state" at the top of this file for the final, re-verified
numbers (836 cases, difficulty 1/2/3/4/5 = 168/387/170/72/39).

#### C-5 (Low) - no test asserts the dashboard JS matches its Python counterparts

See D-6. Four algorithms are duplicated across the language boundary with
nothing keeping them honest.

### 6. Utility and CLI structure

The command tree is well designed: noun-grouped (`cases`/`runs`/`drivers`/
`sandbox`), legacy spellings rewritten before parsing so `--help` stays clean,
consistent exit codes, and `--json-events`/`--quiet`/`--log-level` for
automation. `doctor` is genuinely useful. The following are gaps, not
structural problems.

#### U-1 (Medium) - no `--version` flag

**[verified]** `optarena --version` prints the usage error. `__version__` exists
in `optarena/__init__.py` and is never surfaced. For a tool whose entire value
proposition is reproducible comparison, "which version produced this run" should
be one flag and should also land in the run manifest (which records
`oracle_version` but not the tool version).

**Fix:** `parser.add_argument("--version", action="version", version=f"optarena {__version__}")`
and add `"optarena_version": __version__` to `build_manifest`.

#### U-2 (Medium) - missing recovery and maintenance commands

- `runs rebuild-index` - promised in three docstrings, does not exist (D-2).
- No way to delete or prune runs. `results/runs/` here holds 271 files with no
  lifecycle management; `runs list` prints all of them.
- No `runs scrub-secrets` for S-5.
- No `cases verify --strict` for C-3.

#### U-3 (Low) - `serve` has no host/port ergonomics

[cli.py:355-368](optarena/cli.py#L355-L368): binding is hardcoded to
`127.0.0.1` (correct default), but there is no `--host` for the container/remote
case and no `OSError`/`EADDRINUSE` handling - a port clash surfaces as a raw
traceback from `ThreadingHTTPServer`.

#### U-4 (Low) - `report --out` single-vs-directory detection is fragile

[cli.py:726-729](optarena/cli.py#L726-L729): `single` is inferred from
`--format != all`, a trailing `/`, and whether the path already exists as a
directory. `--format junit --out build/reports` (not yet existing) writes a
*file* named `reports`. An explicit `--out-dir` would remove the guesswork.

#### U-5 (Low) - `doctor` cannot be scripted per-check

It prints a human table and returns 0/1. There is no `--json`, so an automation
wrapper is back to scraping text - the exact problem F-18 (Round 3) solved for `run`.

### 7. Packaging and distribution

#### P-1 (Medium) - a non-editable install writes results into `site-packages`

[store.py:27-29](optarena/store.py#L27-L29) and
[cli.py:47](optarena/cli.py#L47) both resolve their root as
`Path(__file__).resolve().parents[1]`. In a source checkout that is the repo
root. In a wheel install that is `site-packages/`, so:

- `results/` (every run record) is written **inside `site-packages`**,
- `optarena serve` looks for `site-packages/dashboard/`, which does not exist,
- `REPOS_DIR` and `DOCKERFILE_DIR` point at nonexistent siblings.

README documents that `docker/`, `dashboard/` and `repos/` are not bundled and
that this is a source-checkout tool (and Round 3's F-12 covers the bundling
question), but it does not cover the *results directory silently landing in
site-packages*, which is a data-loss-on-upgrade hazard: `pip install -U optarena`
can remove the directory holding a user's run history.

The `wheel-smoke` CI job runs only `--help` and `cases validate`, neither of
which touches `RESULTS_DIR`, so this is invisible to CI.

**Fix:** default to a user data directory (`platformdirs`-style:
`~/.optarena/results`, matching where packs and pricing already live) unless a
repo-root `results/` already exists or `OPTARENA_RESULTS_DIR` is set. Extend
`wheel-smoke` with an actual `run`+`runs list` against `--results-dir`.

#### P-2 (Low) - `setup_repo` cases silently unavailable in a wheel

14 cases declare `setup_repo`. Installed from a wheel they raise
`FileNotFoundError` from `copy_setup_repo`, which the driver converts into a
per-case `error`. Documented as expected, but it means the shipped catalogue is
496 usable cases in that install mode with no `doctor` check for it. A
`doctor` line ("repos/ present: yes/no - 14 cases require it") would close the
loop.

### 8. Documentation accuracy

All of these were statements the code did not back up. Each row is now either
true because the code changed, or the claim was corrected. **All fixed.**

| Doc | Claim | Reality when audited | Resolution |
|---|---|---|---|
| `SECURITY.md` | "All case-supplied file paths are containment-checked ... traversal rejected" | `setup_repo` was not (S-1) | code fixed; the section now also covers image references, pack provenance, and the shared-container caveat |
| `SECURITY.md` | "Prefer `OPTARENA_API_KEY` over `--api-key` (visible in `ps`)" | the `aider` driver put it in argv anyway (S-2) | code fixed; text now states no driver puts the key in argv |
| `README.md` | "every case ... hand-verified ... a correct reference solution passes, a broken one fails" | 44 cases have no failing variant (C-3) | claim replaced with the real 466/510 split and a pointer to `verify --strict` |
| `README.md` | CLI and SDK agents are "apples-to-apples" | SDK drivers lacked timeouts, disruptions, tokens, cost, steps (B-3) | code fixed; the claim is now true and names what is shared |
| `store.py` x3 | "recoverable via `optarena runs rebuild-index`" | the command did not exist (D-2) | command implemented |
| `ci.yml` | Node installed for `tests/test_conformance.py`, "the H-09 guarantee" | the file did not exist (D-6) | suite written and mutation-checked; comment now describes what it actually asserts |
| `cases.py` | "`repo_name` itself is trusted ... not case JSON" | it is read from case JSON (S-1) | comment corrected, containment enforced |
| `packs.py` | "`1.9.0` must sort above `1.10.0`" | inverted (D-9) | corrected |
| `cases.py` | "Called once per scenario by `runner.run_scenario`" | never called (D-1) | it is now genuinely the entry point |

Docs are otherwise unusually accurate and the ARCH/AUDIT/CHANGELOG set is a
genuine asset.

### 9. What is solidly built (verified, no action needed)

Recording this so a future reader does not re-audit it:

- **Path containment for `setup_files` / `test_setup_files` / disruption
  writes and deletes** - resolve-then-`is_relative_to`, absolute paths and
  symlink escapes both rejected ([cases.py:1209-1227](optarena/cases.py#L1209-L1227),
  [cases.py:1311-1323](optarena/cases.py#L1311-L1323)).
- **Fail-closed sandbox policy** - no engine means `check_command` is refused,
  not silently run on the host; two distinct explicit opt-ins, both documented
  ([cases.py:694-711](optarena/cases.py#L694-L711)).
- **`subprocess_env` allowlist** - builds the child environment from an
  allowlist rather than filtering the host's, with case-insensitive matching for
  the Git-Bash/Windows casing trap. This is better than most tools in this space
  ([base.py:29-77](optarena/drivers/base.py#L29-L77)).
- **Hidden-test isolation** - temporal for normal cases, plus a private copy for
  mid-session grading (`evaluate_case_isolated`), so the agent can never read
  what it is graded against.
- **Secret redaction on the write path** - `Backend.redacted_dict` plus
  `to_dict(redact=True)`, with `api_key_set` preserving the useful bit.
- **`_ScopedDashboardHandler`** - prefix allowlist plus resolve-and-contain,
  directory listing disabled, `127.0.0.1` only, `nosniff`/`DENY` headers.
- **Statistics** - Wilson CI and exact two-sided McNemar are both correctly
  implemented, and the tool refuses an aggregate verdict when manifests say the
  runs are not comparable. That restraint is rare and worth keeping.
- **`run_capture`** - concurrent drain threads (no pipe deadlock), bounded
  retention, kill-before-join ordering, process-tree kill on both POSIX and
  Windows. The ordering comment there is correct and non-obvious.
- **Image supply chain** - every base digest-pinned, `images.lock.json` checked
  in CI, multi-arch publish, honest `known_gaps` entries.
- **Corpus hygiene** - 510 cases, zero duplicate prompt sets, zero duplicate
  names, all 510 with a `check_command` and a `reference_solution`, only one
  expected-file spec lacking any content assertion
  (`fix_unquoted_variable_bug`).

### 10. Remediation - what was done

Executed the same day as the audit, in the order below. Everything is
complete except the corpus-content work, which is deliberately a separate pass.

**Phase 1 - correctness of the parallel/interrupt path.** `_run_parallel` now
appends into the caller's list (B-1); `_worker_loop` converts any exception
into that case's error result and the collector polls with a liveness check
(B-2); `regression_summary` uses `is False` and reports only-in-one-run cases
separately (B-4). Each fix was verified against the repro that demonstrated the
bug, then pinned by tests (`ParallelDurabilityTests`, three new
`RegressionTests`).

**Phase 2 - security.** `copy_setup_repo` containment-checks its SOURCE against
`repos/` and the schema rejects anything but a bare directory name (S-1); the
aider driver passes the key through the environment (S-2); every image
reference is shape-validated at the schema and at the single runtime funnel
(S-3); `_IndexLock` only deletes a lock it acquired (S-7); the dashboard
escapes `'` and routes every interpolated value - including numbers - through
`esc()`/`num()` with type-safe formatters (S-6); `optarena runs scrub-secrets`
redacts legacy records (S-5).

**Phase 3 - driver parity.** New `drivers/sdk_base.py` holds the case loop
(whole-case deadline, per-step records, disruptions, usage accumulation, cost);
all six SDK drivers are now ~60-line adapters over it, and
`AsyncSingleFileSDKDriver` gives the two async SDKs real `asyncio.wait_for`
cancellation. A stub HTTP backend covers `openai_chat.run_case` end to end
(C-2), and a test asserts no SDK driver may override `run_case` and silently
lose the guarantees again.

**Phase 4 - dead code and small bugs.** D-1 through D-9 resolved (D-4
`caches_results` was deliberately KEPT as a documented, tested extension point
rather than deleted); B-6 through B-10 fixed, including the new
`case_sensitive` assertion flag (B-7).

**Phase 5 - CLI and packaging.** `--version` plus the tool version in every
manifest (U-1); `runs rebuild-index` / `prune` / `scrub-secrets` (U-2, D-2);
`serve --host` with bind-error handling (U-3); `report --out-dir` (U-4);
`doctor --json` and a source-checkout-assets section (U-5, P-2); results
default outside `site-packages` (P-1); legacy aliases survive global flags
(B-5).

**Phase 6 - tests.** `tests/test_cli.py` (42 tests over the documented exit
codes and every subcommand) and `tests/test_conformance.py`, the Python/JS
cross-oracle suite CI claimed to run. The conformance suite was
mutation-checked: perturbing the dashboard's `mcnemarExactP` makes it fail.
Two further defects surfaced while writing these and were fixed (A-34, A-32).

**Phase 7 - sandbox hardening (S-4).** Every image now provides an
unprivileged uid 1000 account (adopting the base image's own where one exists),
with toolchain caches relocated out of root's 0700 HOME: Maven to `/opt/m2`,
NuGet to `/opt/nuget`, plus permission fixes for cargo's pre-warmed target dir
and the Go module cache. The runner relaxes workspace permissions when
`OPTARENA_SANDBOX_USER` is set, since a bind-mounted 0700 `mkdtemp` root is
otherwise unwritable to a different uid. Validated against real Docker 28: the
base, JVM, .NET and Rust tracks all verify end-to-end **both** as root and as
uid 1000, and all nine Dockerfiles are hadolint-clean. The default stays root
because the run root's host ownership is not something OptArena can guarantee
across Docker Desktop, rootless Podman and CI; a `sandbox-nonroot` CI job now
exercises the non-root path on Linux, which is the only place it can be
honestly validated.

**Deferred (corpus content only).** C-3's 44 missing `broken_solutions` and
C-4's corpus depth (multi-turn, disruptions, difficulty 4-5, `test_kind`).
The tooling that makes C-3 visible and enforceable shipped:
`optarena cases verify --strict`.

*Prepared by reading every line of the repository at `b6cffd1`. Behavioural
claims marked [verified] were reproduced by executing code against this
checkout; coverage figures are from `coverage 7.15.2` over the full suite with
`OPTARENA_DISABLE_SANDBOX=1`. Remediation verified by the same means: 350
tests, `ruff` clean, hadolint clean, and real-Docker corpus verification on
four image tracks as both root and uid 1000.*

---

## Execution & Corpus Development Log

_Original file: `AUDIT_EXEC.md` ("Execution Log - gemma4:12b Corpus Calibration")._

**Purpose:** a running record of the overnight/ongoing 510-case corpus
calibration run - incidents, fixes, design discussions, and status. This is
an *execution* log, distinct from Round 4 above (the static code/security
audit of the `optarena` repo itself). Most of this log documents work in an
external orchestrator script (`calibrate.py`, outside the repo, in a
session scratchpad) used to drive calibration; sections that changed
`optarena/` code itself are flagged explicitly where they occur (sections
12, 14, 16, and 17 onward's corpus-content edits).

This log's own numbered sections (`## 1.` through `## 22.`) are preserved
below as subsections, unrenumbered, since later sections and other rounds
of this consolidated file cross-reference them by number.

### 1. What is running and why

Goal: measure the 510-case built-in corpus against real models to answer
"more cases or harder cases?" (see prior discussion) - before that question
is answerable, the corpus itself needs calibration data, since as of session
start only 9 of 510 cases had ever produced a verdict from any real run.

Driver: `ollama-chat` (raw-model baseline, no agent) against models already
pulled locally: `gemma4:12b`, `gemma3:1b`, `qwen3-coder:30b`.

Orchestration: `calibrate.py`, a resumable script in the session scratchpad
(not part of the `optarena` repo) that:
- Loads all 510 cases, groups them by required sandbox image (9 images ->
  17 chunks, capped at 40 cases/chunk).
- Runs each chunk as one `optarena run` subprocess invocation.
- Records completed chunks in a JSON state file so a re-launch resumes
  instead of restarting.

**Why chunked by image, why serial:**
- `runner.run_scenario` starts one long-lived container per distinct image
  the loaded cases need, alive for the whole invocation. A naive one-shot
  510-case run would hold up to 9 containers open simultaneously for ~5
  hours; chunking by image keeps exactly one alive at a time (mirrors the
  fix already in the project's own `verify.py`).
- `--parallel N` was measured, not assumed, to be unsafe against a
  single-GPU Ollama backend: the same 6 cases scored 5/6 at `--parallel 1`
  and 2/6 at `--parallel 4` in a pilot run before calibration started. N
  concurrent requests queue behind one GPU; the per-case deadline is
  wall-clock from case start, so queueing time gets charged to the case and
  manufactures deadline failures. Parallelism here doesn't just fail to
  help, it corrupts the measurement. Calibration runs strictly serial.

### 2. Incident #1: system crash mid-run

The host crashed and restarted partway through the first calibration launch.

**Recovery, verified not assumed:**
- Docker daemon: reachable, no orphaned `optarena-sandbox-*` containers.
- State file showed 3 of 17 chunks cleanly completed (104 cases: dotnet
  18/29, go 24/35, jvm-1 6/40).
- One run record was left in `status: "running"` - the chunk that was
  mid-flight when the crash hit (`base-jvm-2`, Round 3's F-02's own checkpoint
  behavior working as designed, not a bug). The CLI subprocess itself never
  returned, so the orchestrator's state file correctly did NOT mark it done.
- All other `status: "running"` records found in `results/runs/` predate
  the crash by hours and are unrelated pre-existing clutter from earlier
  manual CLI testing that same day - left alone, not this incident's
  concern.

Relaunching `calibrate.py` resumed cleanly at chunk 4/17.

### 3. Incident #2: duplicate orchestrators + silently poisoned data

**What happened.** After the crash, a background task from the *original*
pre-crash launch was reported by the harness as "stopped, no completion
record" - but had actually survived as a live OS process. A second
orchestrator was launched on top of it without verifying the first was
truly dead. Both ran concurrently for a stretch, racing on the same
**unlocked** JSON state file and hitting the same Ollama backend
concurrently.

Sometime in that window, Ollama itself went down (most likely a casualty of
the original crash - a user-mode app with no auto-restart, not a Windows
service; nothing had restarted it). This surfaced as `URLError: ... actively
refused` on every HTTP call.

**Why this went undetected initially.** `optarena run` exits 1 whenever any
case fails - the normal, expected outcome of a real calibration chunk. The
orchestrator's only pre-existing success check was "exit code in {0, 1}".
A chunk where *every* case fails because the backend is unreachable is
*also* exit 1, and was therefore indistinguishable from a real (if poor)
measurement at the exit-code level. One retried chunk (`base-jvm-2`, 13
cases) was recorded as a completed 0/13 measurement - the backend had been
down for its entire duration.

**How it was actually caught.** Not by any code check - by the wall-clock
timing not fitting: 13 cases in 0.9 minutes (4.1s/case) against every other
chunk's ~60-90s/case. A case that fails because the model produced wrong
code still costs a full LLM generation; a case that fails on a connection
refusal costs nothing. The speed was the tell.

**Concrete damage, precisely bounded:**
- Two duplicate orchestrator processes, both killed (`taskkill /F`).
- One corrupted "done" entry in the state file (`base-jvm-2`), removed.
- Two zero-information run records deleted from `results/runs/`:
  - `base-jvm-2` retry (0/13, 100% connection-refused).
  - `base-node-1` (36/40 cases attempted before the killed subprocess
    stopped writing; also 100% connection-refused).
- `results/index.json` rebuilt after the deletions.
- Ollama restarted (`ollama serve`), and verified with an actual generation
  call (17s round-trip, real 441-char response) - not just the `/api/tags`
  health endpoint, and cross-checked against `optarena doctor`'s own
  backend probe before trusting it for an unattended multi-hour run.

Nothing beyond these two run records was lost or corrupted. The three
cleanly-completed pre-incident chunks (dotnet, go, jvm-1) were never
touched by the race and remain valid.

### 4. Hardening applied to `calibrate.py` (external orchestrator only)

Three changes, each verified before being trusted:

1. **Backend health probe + wait before every chunk.** `wait_for_backend()`
   polls `/api/tags` for up to 15 minutes before starting a chunk, so a
   transient backend restart resolves itself instead of the chunk racing
   ahead into a wall of connection errors.

2. **Poisoned-chunk detection.** After a chunk completes, its saved run
   record is inspected: if *every* case's `error` field matches a
   connection-error signature (refused / reset / timed out / URLError), the
   chunk is treated as an infra failure - the run record is deleted and the
   chunk is NOT marked done, regardless of exit code. A chunk with even one
   real pass or one real oracle-rejection is never flagged (verified with a
   synthetic mixed-outcome record before trusting it).

3. **Single-instance lock.** An exclusive lock file (`calib-<model>.lock`)
   holding the current PID; a second launch checks the recorded PID's
   liveness and refuses to start if it's still running, or takes over if
   the holder is confirmed dead. This directly targets the root cause of
   incident #2 - the harness's "stopped" status is not proof a process is
   actually gone.

   **A bug was found and fixed in this same hardening pass, before it was
   trusted:** the liveness check first used `tasklist //FI "PID eq <pid>"`
   (the `//` double-slash is an MSYS/Git-Bash convention for escaping a
   leading slash when invoking a Windows tool *from bash*). Called directly
   from Python's `subprocess.run` (no shell involved), `//FI` is not a
   syntax `tasklist.exe` understands, so the filter silently failed and
   every PID looked dead - which would have made the lock a complete no-op,
   the exact opposite of its purpose. Caught by testing the lock against a
   live PID (this session's own process) before relying on it: it failed
   to block, which is what exposed the bug. Fixed to `/FI` (Windows' actual
   switch syntax) and re-verified both directions - blocks a genuinely live
   duplicate, takes over a genuinely stale lock, releases cleanly on exit.

Run resumed cleanly under the hardened orchestrator (single PID, confirmed
via live process listing) at chunk 4/17. No further incidents since.

### 5. Discussion: why do sandbox containers get 2GB when they use <60MB?

Raised as a discussion question; no change made or proposed to the harness.

**Measured:** live containers during calibration showed 1.8-59MB usage
against a 2GB limit (0.09-2.9%).

**Why the code sets 2GB uniformly.** From `optarena/cases.py`
(`_HARDENING_ARGS`, `DockerSandbox.start`, `_run_check_command_docker`):
the container is **shared across every case in a run/chunk that needs that
image**, not sized per-check. The limit has to cover the heaviest thing
that container might ever run, not the median. Two concrete pressures noted
in the code's own comments:
- A `/tmp` tmpfs (used for build scratch - Go's `GOCACHE`, compiled
  binaries, etc.) is capped at 1GB, and tmpfs usage counts against the
  container's total memory (it's RAM-backed) - so up to half the 2GB budget
  can be consumed by build artifacts alone before the running process gets
  any of it.
- A Spring Boot / Maven build (`jvm` track) or a Rust compile can want
  several hundred MB just for the toolchain's own overhead, independent of
  what the actual test does.

The 2GB figure is one hardcoded literal, applied identically to all 9
per-image sandbox tracks (`--memory 2g` appears in exactly two places in
`cases.py`), with no per-image override in the schema.

**The observation is valid as a review point** (not something acted on
tonight): the python/node/base/ruby/php tracks are running at a tiny
fraction of their allocation, which is real headroom being reserved that
isn't needed for those images specifically. A per-track budget (e.g. 512MB
for the light tracks, 2GB reserved for jvm/rust/dotnet/go) would let more
containers/workers coexist on a given host without host RAM being the
binding constraint. Logged here as a candidate item for the next code-audit
pass, not touched.

**Aside noticed while checking this, unrelated to the question asked but
worth recording:** two containers were observed running simultaneously at
one point (`optarena-tester-node:latest`, created ~20 minutes apart). The
older one is almost certainly an orphan from incident #2's `taskkill` - a
hard external kill of the `optarena run` Python subprocess doesn't let
`runner.run_scenario`'s own `finally` block (which calls `sandbox.stop()`)
execute, so the container it had started was never torn down. Harmless
(idle, 1.8MB), left alone per instruction not to change anything.

### 6. Discussion: can LLM generation and Docker verification overlap?

**Question asked:** could case N+1's LLM generation start while case N's
Docker check_command is still running - one step ahead, not full N-way
parallelism like `--parallel`?

**Current control flow, verified by re-reading the code (not from
memory):**
- Within one case: the driver blocks on each prompt's Ollama HTTP call in
  turn, writing output to disk; only after every prompt is `evaluate_case()`
  called, which is the one call that touches Docker
  (`optarena/drivers/openai_chat.py:124-201`).
- Across cases: the runner's serial loop
  (`optarena/runner.py:328, 661-664`) does not begin `_run_case` for case
  N+1 until case N's `_run_case` - LLM *and* Docker both - has fully
  returned.
- One thing already amortized: the Docker container itself is not started
  fresh per check. One container is started once per run/chunk and every
  case's check is a `docker exec` into that already-warm container -
  cheap relative to a full container lifecycle.
- No prefetch/pipelining of any kind exists between the generate and verify
  phases, or across cases, anywhere in the codebase.

**Is one-step-ahead overlap architecturally sound?** Yes, and importantly
it is a *different* form of concurrency than `--parallel`'s failure mode:
- `--parallel N` puts N cases' LLM calls in flight on the *same* GPU
  concurrently - genuine resource contention, which is exactly what
  corrupted the pilot's measurements (queueing time charged to the
  per-case deadline).
- LLM(N+1) || Docker(N) is LLM (GPU-bound) against Docker (CPU-bound) -
  different resources, no contention in principle. The GPU sits idle
  today during every case's Docker phase; a one-step-ahead design would
  put it to use instead.
- It would also be safer than `--parallel` in a way that matters: at most
  one process is ever exec'd into the shared container at any moment
  (only case N's check is in flight; case N+1 is still generating, hasn't
  reached Docker yet), so none of F-06's shared-container collision /
  cross-case `reap()` risk applies. `--parallel` had to solve that by
  giving each worker its own dedicated container; a strict one-step
  pipeline wouldn't need to.
- Case N+1's own deadline clock would start when its generation call
  actually begins - which, with no GPU contention, is immediately. No
  reproduction of the `--parallel` deadline-corruption failure mode.

**Is it worth doing? Measured, not assumed - this is the deciding
number.** Every completed chunk's run record carries both the case's total
wall time and the check_command's own duration separately
(`extra.oracle.duration_s`). Computed across all 245 cases measured so far
tonight:

```
total wall time   : 272.7 min
total Docker time :   5.4 min  (2.0% of wall time)
total LLM/other    : 267.3 min  (98.0% of wall time)
mean Docker time per case : 1.33s
mean total time per case  : 66.78s
```

Per-chunk Docker share ranges from ~0% (php, python, most tracks) up to
~11% for the compile-heavy dotnet track; every other track is under 2%.

**Conclusion: not worth building.** Even *perfect* one-step overlap -
saving 100% of Docker time with zero LLM slowdown - caps the total
achievable speedup at ~2% corpus-wide (dotnet alone, worst case, ~11%).
A 5-hour run would become a ~4h54m run. The bottleneck is not the
generate-then-verify handoff; it is that there is exactly one GPU serving
exactly one loaded model, and generation time (98% of wall time) is bound
by that regardless of what Docker is or isn't doing concurrently. Pipelining
optimizes the 2% that isn't the bottleneck. Not implemented; not planned
unless the calling context changes (e.g., a remote/paid API backend with
real request-level concurrency, where the GPU-contention argument doesn't
apply and the calculus would need redoing).

### 7. Status as of last update

Chunk `base-4` completed (1/2, 50%). All 17 chunks complete.

```
510/510 cases (100%) - 17 of 17 chunks done
```

| Chunk | Result |
|---|---|
| dotnet | 18/29 (62%) |
| go | 24/35 (69%) |
| jvm-1 | 6/40 (15%) |
| jvm-2 | 5/13 (38%) |
| node-1 | 25/40 (62%) |
| node-2 | 34/40 (85%) |
| node-3 | 5/6 (83%) |
| php | 1/19 (5%) |
| python-1 | 25/40 (62%) |
| python-2 | 34/40 (85%) |
| python-3 | 33/37 (89%) |
| ruby | 6/20 (30%) |
| rust | 13/29 (45%) |
| 1 | 20/40 (50%) |
| 2 | 29/40 (72%) |
| 3 | 30/40 (75%) |
| 4 | 1/2 (50%) |

Full per-case results (every case, every completed chunk, with the actual
failure reason - not just the aggregate above) are in section 9.

**Standing corpus-tooling finding (from earlier in the session, still
load-bearing):** the raw-model baseline driver writes one flat file to the
case's first expected path (no directory structure). 133 of 510 cases (26%)
have an expected-file pattern the baseline cannot satisfy by construction -
either >1 expected file / a required starter repo (46 cases), or a single
file whose pattern requires a nested directory the baseline's flattening
can never produce (`**/ClassName.ext`-style patterns; 91 further cases,
found by tracing jvm-1's anomalously low raw pass rate to
`expected file matching "**/MessageFormatterTest.java" not created (got: MessageFormatterTest.java)`
- the model's output IS correct, sitting right there in "got:", just not at
a path the pattern can match under `fnmatch`, which has no concept of "/"
as a separator). Concentrated in jvm/kotlin (Maven/Spring project layout) and
php (PSR-4 namespace/directory convention) - both language communities where
`**/`-style nested paths are the norm, not the exception.

**Measured across everything run so far (510 cases):** 131 (26%)
are this baseline-gap artifact, not a model-capability signal. Excluding
them, gemma4:12b's fair pass rate is **82%** (309/379),
against a raw blended rate of 61% (309/510) that conflates the
two. This correction applies to the whole run once it finishes and will be
carried into the final corpus report.

No poisoned chunks, no failures, no incidents since section 3/4's fixes
were deployed. Single orchestrator process, confirmed via live process
listing. Ollama confirmed healthy via `optarena doctor` and a real
generation call.

**Not started yet:** the `gemma3:1b` weak-anchor run (queued to run only
over cases gemma4:12b passed, per the plan from earlier tonight - it's the
information-dense subset for discrimination analysis, and running it over
everything would cost nearly as much as gemma4 for no extra signal, since
container/check_command time is model-independent).

**No code in `optarena/` has been modified tonight.** All fixes described
in sections 3-4 are in the external orchestrator script only.

### 9. Per-case results, every completed chunk

Full detail behind section 7's summary table - every case that has
run so far, its verdict, and (for a failure) the actual reason, not
just an aggregate percentage. `kind` values:

- `pass` - satisfied the full oracle
- `baseline_gap_glob` - the model's output was correct; the raw-model
  baseline writes a flat file and the case's expected-file pattern
  needs a nested path (`**/X`-style) the baseline structurally cannot
  produce. Not a model-capability signal.
- `baseline_gap_multifile` - the case needs >1 file or a starter repo;
  same non-signal reason.
- `oracle_fail[:class]` - the model produced something and the oracle
  rejected it. This is real signal. `failure_class` (syntax_error /
  compile_error / assertion_failure / runtime_error / timeout) is
  `cases.classify_failure`'s own best-effort bucketing, shown when set.
- `infra` - a connection/backend error, not a verdict about the model.

<details>
<summary>Full per-case, per-chunk results (click to expand) - C# through Shell/Docker/YAML/etc., 17 chunks</summary>

#### C# (.NET) - `base-dotnet` (18/29)
| Case | Verdict | Reason |
|---|---|---|
| `refactor_csharp_loop_to_linq` | PASS | - |
| `refactor_aspnet_duplicate_endpoints` | PASS | - |
| `optimize_csharp_string_concat_stringbuilder` | PASS | - |
| `optimize_csharp_regex_compiled_per_call` | PASS | - |
| `optimize_csharp_list_dedup` | PASS | - |
| `fix_csharp_sequential_tasks` | PASS | - |
| `fix_csharp_redos_nested_quantifier` | PASS | - |
| `fix_csharp_parallel_race` | PASS | - |
| `fix_csharp_modify_during_foreach` | PASS | - |
| `fix_csharp_for_loop_closure_capture` | PASS | - |
| `fix_csharp_dictionary_race` | PASS | - |
| `fix_aspnet_wrong_status_code` | PASS | - |
| `fix_aspnet_missing_validation` | PASS | - |
| `fix_aspnet_error_status_mapping` | PASS | - |
| `fix_aspnet_di_lifetime` | PASS | - |
| `create_todo_minimal_api` | PASS | - |
| `create_health_endpoint_dotnet` | PASS | - |
| `add_tests_csharp_shipping_tiers` | PASS | - |
| `refactor_csharp_extract_discount_rule` | fail | **baseline_gap_multifile**: expected file matching "CheckoutService.cs" not created (got: DiscountRules.cs) |
| `fix_csharp_sql_injection_interpolation` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 1): dotnet test tests/tests.csproj |
| `fix_aspnet_path_traversal` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 1): python3 test_traversal.py |
| `create_items_validation_endpoint` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 1): python3 test_items.py |
| `create_csharp_semaphore_limiter` | fail | **oracle_fail:compile_error**: check_command failed in docker (exit 1): dotnet run --project app.csproj |
| `create_csharp_csv_category_totals` | fail | **oracle_fail:compile_error**: check_command failed in docker (exit 1): dotnet test tests/tests.csproj |
| `create_aspnet_inventory_service` | fail | **baseline_gap_multifile**: expected file matching "Program.cs" not created (got: Services/InventoryService.cs) |
| `add_tests_csharp_caesar_cipher` | fail | **baseline_gap_glob**: wanted "**/CaesarCipherTests.cs", model wrote "CaesarCipherTests.cs" (correct content, unmatchable path) |
| `add_tests_aspnet_webapplicationfactory` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 mutation_check.py |
| `add_tests_aspnet_service` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 mutation_check.py |
| `add_github_actions_ci_dotnet` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 verify_ci.py |

#### Go - `base-go` (24/35)
| Case | Verdict | Reason |
|---|---|---|
| `refactor_go_table_driven_tiers` | PASS | - |
| `refactor_go_duplicated_env_checks_to_helper` | PASS | - |
| `refactor_gin_duplicate_handlers` | PASS | - |
| `optimize_go_string_concat_loop` | PASS | - |
| `optimize_go_slice_dedup` | PASS | - |
| `optimize_go_regex_compile_in_loop` | PASS | - |
| `optimize_go_linear_contains_dedup` | PASS | - |
| `fix_go_zip_slip_path_traversal` | PASS | - |
| `fix_go_nil_map_write` | PASS | - |
| `fix_go_mutex_copy` | PASS | - |
| `fix_go_errors_is_wrapped_sentinel` | PASS | - |
| `fix_go_counter_race` | PASS | - |
| `fix_go_context_cancellation` | PASS | - |
| `fix_go_command_injection_echo` | PASS | - |
| `fix_go_channel_deadlock` | PASS | - |
| `fix_gin_wrong_status_code` | PASS | - |
| `fix_fiber_missing_validation` | PASS | - |
| `create_health_endpoint_go` | PASS | - |
| `create_gin_crud_todos` | PASS | - |
| `create_fiber_items_endpoint` | PASS | - |
| `add_tests_go_luhn` | PASS | - |
| `add_tests_go_grade_brackets` | PASS | - |
| `add_go_todo_store_query_pagination` | PASS | - |
| `adapt_removed_dependency_go_dynamic` | PASS | - |
| `refactor_go_extract_age_parser` | fail | **baseline_gap_multifile**: expected file matching "users.go" not created (got: validate.go) |
| `fix_gin_unique_constraint` | fail | **baseline_gap_multifile**: expected file matching "handlers/user.go" not created (got: repository/user.go) |
| `fix_gin_service_status_mapping` | fail | **baseline_gap_glob**: wanted "**/account.go", model wrote "account.go" (correct content, unmatchable path) |
| `fix_gin_response_leak` | fail | **baseline_gap_glob**: wanted "**/user.go", model wrote "none" (correct content, unmatchable path) |
| `fix_gin_path_traversal` | fail | **oracle_fail**: expected file matching "main.go" not created (got: none) |
| `create_go_generic_lru_cache` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 1): go test ./... -count=1 :: lru.go:1:1: expected 'package', found 'i... |
| `create_gin_reviews_resource` | fail | **baseline_gap_multifile**: expected file matching "**/review.go" not created (got: review.go) |
| `add_tests_go_palindrome_alphanumeric` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 mutation_check.py |
| `add_tests_gin_handler_logic` | fail | **oracle_fail**: "output_test.go" does not match regex "func\s+Test\w+[\s\S]*func\s+Test\w+" |
| `add_github_actions_ci_go` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 1): python3 check_ci.py |
| `add_gin_query_filter` | fail | **baseline_gap_glob**: wanted "**/product.go", model wrote "product.go" (correct content, unmatchable path) |

#### Java/Kotlin (1/2) - `base-jvm-1` (6/40)
| Case | Verdict | Reason |
|---|---|---|
| `fix_kotlin_insecure_random_token` | PASS | - |
| `add_tests_java_roman_to_int` | PASS | - |
| `add_tests_java_excel_column` | PASS | - |
| `add_tests_java_days_in_month` | PASS | - |
| `add_money_allocate_remainder` | PASS | - |
| `adapt_reactive_config_java_dynamic` | PASS | - |
| `fix_wrong_http_status_spring` | fail | **baseline_gap_glob**: wanted "**/NoteController.java", model wrote "NoteController.java" (correct content, unmatchable path) |
| `fix_spring_password_leak` | fail | **baseline_gap_multifile**: expected file matching "**/UserDto.java" not created (got: UserDto.java) |
| `fix_spring_missing_validation` | fail | **baseline_gap_multifile**: expected file matching "**/RegistrationValidator.java" not created (got: RegistrationValidator.java) |
| `fix_reflected_input_in_error_message` | fail | **baseline_gap_glob**: wanted "**/EchoController.java", model wrote "EchoController.java" (correct content, unmatchable path) |
| `fix_null_pointer_service` | fail | **baseline_gap_glob**: wanted "**/OrderService.java", model wrote "OrderService.java" (correct content, unmatchable path) |
| `fix_missing_exception_handler` | fail | **baseline_gap_glob**: wanted "**/DivideController.java", model wrote "DivideController.java" (correct content, unmatchable path) |
| `fix_kotlin_xxe_xml_parser` | fail | **baseline_gap_glob**: wanted "**/XmlConfigParser.kt", model wrote "XmlConfigParser.kt" (correct content, unmatchable path) |
| `fix_kotlin_toint_crashes_on_bad_input` | fail | **baseline_gap_glob**: wanted "**/Parser.kt", model wrote "Parser.kt" (correct content, unmatchable path) |
| `fix_kotlin_shared_mutable_copy` | fail | **baseline_gap_glob**: wanted "**/Billing.kt", model wrote "Billing.kt" (correct content, unmatchable path) |
| `fix_kotlin_missing_when_branch` | fail | **baseline_gap_glob**: wanted "**/OrderFlow.kt", model wrote "OrderFlow.kt" (correct content, unmatchable path) |
| `fix_kotlin_integer_division_average` | fail | **baseline_gap_glob**: wanted "**/Stats.kt", model wrote "Stats.kt" (correct content, unmatchable path) |
| `fix_kotlin_bang_bang_npe` | fail | **baseline_gap_glob**: wanted "**/SettingsStore.kt", model wrote "SettingsStore.kt" (correct content, unmatchable path) |
| `fix_java_path_traversal_vault` | fail | **baseline_gap_glob**: wanted "**/FileVault.java", model wrote "FileVault.java" (correct content, unmatchable path) |
| `fix_java_integer_boxing_equality` | fail | **baseline_gap_glob**: wanted "**/Totals.java", model wrote "Totals.java" (correct content, unmatchable path) |
| `fix_java_insecure_deserialization` | fail | **baseline_gap_glob**: wanted "**/SessionStore.java", model wrote "SessionStore.java" (correct content, unmatchable path) |
| `fix_java_concurrent_modification_remove` | fail | **baseline_gap_glob**: wanted "**/Evens.java", model wrote "Evens.java" (correct content, unmatchable path) |
| `fix_insecure_cors_config` | fail | **baseline_gap_glob**: wanted "**/ReportsController.java", model wrote "ReportsController.java" (correct content, unmatchable path) |
| `create_user_dto_validation` | fail | **baseline_gap_glob**: wanted "**/UserDto.java", model wrote "UserDto.java" (correct content, unmatchable path) |
| `create_spring_order_endpoint` | fail | **baseline_gap_multifile**: expected file matching "**/OrderService.java" not created (got: OrderService.java) |
| `create_kotlin_spring_shipping` | fail | **baseline_gap_multifile**: expected file matching "**/ShippingService.kt" not created (got: ShippingService.kt) |
| `create_kotlin_sealed_result` | fail | **baseline_gap_glob**: wanted "**/Duration.kt", model wrote "Duration.kt" (correct content, unmatchable path) |
| `create_kotlin_csv_leaderboard` | fail | **baseline_gap_glob**: wanted "**/ScoreBoard.kt", model wrote "ScoreBoard.kt" (correct content, unmatchable path) |
| `create_java_generic_lru_cache` | fail | **baseline_gap_glob**: wanted "**/LruCache.java", model wrote "LruCache.java" (correct content, unmatchable path) |
| `create_items_crud_controller` | fail | **baseline_gap_multifile**: expected file matching "**/ItemsController.java" not created (got: ItemsController.java) |
| `create_health_endpoint_java` | fail | **baseline_gap_multifile**: expected file matching "**/HealthController.java" not created (got: HealthController.java) |
| `create_greeting_service` | fail | **baseline_gap_glob**: wanted "**/GreetingService.java", model wrote "GreetingService.java" (correct content, unmatchable path) |
| `add_tests_kotlin_roman_numeral` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 mutation_check.py |
| `add_tests_kotlin_classifier` | fail | **baseline_gap_glob**: wanted "**/PasswordStrengthTest.kt", model wrote "PasswordStrengthTest.kt" (correct content, unmatchable path) |
| `add_tests_java_word_wrap` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): java MutationCheck.java |
| `add_tests_java_grade_brackets` | fail | **baseline_gap_glob**: wanted "**/GradesTest.java", model wrote "GradesTest.java" (correct content, unmatchable path) |
| `add_tests_items_controller_webmvctest` | fail | **baseline_gap_glob**: wanted "**/CatalogControllerTest.java", model wrote "CatalogControllerTest.java" (correct content, unmatchable path) |
| `add_tests_greeting_service_junit` | fail | **baseline_gap_glob**: wanted "**/MessageFormatterTest.java", model wrote "MessageFormatterTest.java" (correct content, unmatchable path) |
| `add_kotlin_cache_ttl_expiry` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 1): mvn -o -q test -Dtest=CacheTtlTest |
| `add_github_actions_ci_java` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 1): python3 check_ci.py |

#### Java/Kotlin (2/2) - `base-jvm-2` (5/13)
| Case | Verdict | Reason |
|---|---|---|
| `refactor_kotlin_currency_formatters_to_shared_function` | PASS | - |
| `refactor_java_tier_chains_to_enum` | PASS | - |
| `optimize_java_string_concat_loop` | PASS | - |
| `optimize_java_fib_memoized_recursion` | PASS | - |
| `optimize_java_dedup_nested_contains` | PASS | - |
| `refactor_kotlin_when_chain_to_polymorphism` | fail | **baseline_gap_glob**: wanted "**/Shape.kt", model wrote "Shape.kt" (correct content, unmatchable path) |
| `refactor_kotlin_extract_discount_rule` | fail | **baseline_gap_multifile**: expected file matching "**/DiscountRules.kt" not created (got: DiscountRules.kt) |
| `refactor_java_extract_username_validator` | fail | **baseline_gap_multifile**: expected file matching "**/UsernameValidator.java" not created (got: UsernameValidator.java) |
| `refactor_extract_service_layer` | fail | **baseline_gap_multifile**: expected file matching "**/PricingController.java" not created (got: PricingController.java) |
| `refactor_duplicate_controller_logic` | fail | **baseline_gap_glob**: wanted "**/RegistrationController.java", model wrote "RegistrationController.java" (correct content, unmatchable path) |
| `optimize_kotlin_immutable_list_accumulation` | fail | **baseline_gap_glob**: wanted "**/Scaler.kt", model wrote "Scaler.kt" (correct content, unmatchable path) |
| `optimize_kotlin_fib_naive_recursion` | fail | **baseline_gap_glob**: wanted "**/Fib.kt", model wrote "Fib.kt" (correct content, unmatchable path) |
| `optimize_inefficient_stream_loop` | fail | **baseline_gap_glob**: wanted "**/DuplicateFinder.java", model wrote "DuplicateFinder.java" (correct content, unmatchable path) |

#### JS/TS (1/3) - `base-node-1` (25/40)
| Case | Verdict | Reason |
|---|---|---|
| `fix_express_error_handling` | PASS | - |
| `create_vue_reactive_counter` | PASS | - |
| `create_ts_type_guard` | PASS | - |
| `create_ts_partial_update` | PASS | - |
| `create_ts_generic_dedupe` | PASS | - |
| `create_react_counter_component` | PASS | - |
| `create_node_transform_stream` | PASS | - |
| `create_node_group_by` | PASS | - |
| `create_node_csv_parse_objects` | PASS | - |
| `create_node_arg_parser` | PASS | - |
| `create_nestjs_items_controller` | PASS | - |
| `create_health_endpoint_node` | PASS | - |
| `create_express_todo_api` | PASS | - |
| `add_ts_trie_autocomplete` | PASS | - |
| `add_tests_ts_parse_query_string` | PASS | - |
| `add_tests_ts_format_bytes` | PASS | - |
| `add_tests_shortlink_stats_service` | PASS | - |
| `add_tests_node_utility` | PASS | - |
| `add_tests_node_shipping_tiers` | PASS | - |
| `add_tests_js_slugify` | PASS | - |
| `add_tests_js_parse_cookies` | PASS | - |
| `add_tests_express_multi` | PASS | - |
| `add_tests_express_endpoint` | PASS | - |
| `add_eventbus_wildcard_subscription` | PASS | - |
| `adapt_reactive_retry_config_node_dynamic` | PASS | - |
| `fix_express_command_injection` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 test_injection.py |
| `fix_express_async_error_swallowed` | fail | **baseline_gap_multifile**: expected file matching "src/app.js" not created (got: src/routes/books.js) |
| `create_ts_typed_event_bus` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 2): sh run_checks.sh |
| `create_ts_discriminated_union` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 2): sh run_checks.sh |
| `create_node_task_queue_events` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 1): node behavior_test.js |
| `create_express_reviews_resource` | fail | **baseline_gap_multifile**: expected file matching "src/app.js" not created (got: src/routes/reviews.js) |
| `add_tests_ts_utility` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 mutation_check.py |
| `add_tests_react_component` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 mutation_check.py |
| `add_tests_node_semver_compare` | fail | **oracle_fail**: "test_semver.js" does not match regex "test\s*\(\s*['\"][\s\S]*test\s*\(\s*['\"][\s\S]*test\s*\(\s*['\"][\s\S]..." |
| `add_tests_js_flatten_one_level` | fail | **oracle_fail**: expected file matching "test_flatten.js" not created (got: none) |
| `add_template_conditional_blocks` | fail | **oracle_fail**: expected file matching "template.js" not created (got: none) |
| `add_shortlink_top_links_endpoint` | fail | **baseline_gap_multifile**: expected file matching "*/routes/links.ts" not created (got: routes/links.ts) |
| `add_shortlink_link_expiry` | fail | **baseline_gap_multifile**: expected file matching "*/routes/redirect.ts" not created (got: routes/redirect.ts) |
| `add_github_actions_ci_node` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 1): python3 test_ci.py |
| `add_express_query_filter` | fail | **baseline_gap_multifile**: expected file matching "src/routes/products.js" not created (got: src/repository/productRepository.js) |

#### JS/TS (2/3) - `base-node-2` (34/40)
| Case | Verdict | Reason |
|---|---|---|
| `refactor_node_callback_to_promise` | PASS | - |
| `refactor_nestjs_service_extraction` | PASS | - |
| `refactor_js_switch_to_lookup_table` | PASS | - |
| `refactor_js_prototype_to_class` | PASS | - |
| `refactor_js_callback_pyramid_to_async` | PASS | - |
| `optimize_ts_unshift_in_loop` | PASS | - |
| `optimize_ts_array_find_in_loop` | PASS | - |
| `optimize_node_spread_accumulation` | PASS | - |
| `optimize_node_array_dedup` | PASS | - |
| `optimize_js_indexof_frequency_map` | PASS | - |
| `optimize_js_includes_in_loop` | PASS | - |
| `migrate_js_to_ts` | PASS | - |
| `fix_vue_computed_bug` | PASS | - |
| `fix_ts_strict_null_checks` | PASS | - |
| `fix_ts_readonly_mutation` | PASS | - |
| `fix_ts_prototype_pollution_deep_merge` | PASS | - |
| `fix_ts_promise_all` | PASS | - |
| `fix_ts_lexicographic_number_sort` | PASS | - |
| `fix_ts_interface_optional` | PASS | - |
| `fix_ts_implicit_any` | PASS | - |
| `fix_react_stale_closure` | PASS | - |
| `fix_node_url_query_parsing` | PASS | - |
| `fix_node_sort_lexicographic` | PASS | - |
| `fix_node_secrets_in_logs` | PASS | - |
| `fix_node_redos_validator` | PASS | - |
| `fix_node_path_traversal_resolve` | PASS | - |
| `fix_node_open_redirect` | PASS | - |
| `fix_node_once_listener` | PASS | - |
| `fix_node_map_parseint` | PASS | - |
| `fix_node_buffer_encoding` | PASS | - |
| `fix_nestjs_status_code` | PASS | - |
| `fix_js_token_compare_timing_safe` | PASS | - |
| `fix_js_static_asset_path_traversal` | PASS | - |
| `fix_express_xss_unescaped_output` | PASS | - |
| `refactor_express_extract_controller` | fail | **baseline_gap_multifile**: expected file matching "src/routes/quotes.js" not created (got: src/services/pricing.js) |
| `refactor_express_duplicate_routes` | fail | **oracle_fail**: "server.js" does not match regex "function\s+\w+\s*\(" |
| `fix_ts_foreach_async_never_awaits` | fail | **oracle_fail**: "prices.ts" contains forbidden content "foreach" |
| `fix_ts_csv_formula_injection` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 2): tsc --strict --target es2020 --module commonjs --skipLibCheck |
| `fix_shortlink_url_scheme_allowlist` | fail | **baseline_gap_multifile**: expected file matching "*/routes/links.ts" not created (got: routes/links.ts) |
| `fix_express_unique_constraint` | fail | **baseline_gap_multifile**: expected file matching "src/repository/userRepository.js" not created (got: none) |

#### JS/TS (3/3) - `base-node-3` (5/6)
| Case | Verdict | Reason |
|---|---|---|
| `refactor_ts_promise_chain_to_async_await` | PASS | - |
| `refactor_ts_manual_null_chain_to_optional_chaining` | PASS | - |
| `refactor_ts_enum_to_union` | PASS | - |
| `refactor_node_promise_chain_to_async` | PASS | - |
| `refactor_node_fs_promises` | PASS | - |
| `refactor_shortlink_shared_error_responses` | fail | **baseline_gap_multifile**: expected file matching "*/routes/respond.ts" not created (got: routes/respond.ts) |

#### PHP - `base-php` (1/19)
| Case | Verdict | Reason |
|---|---|---|
| `add_php_validator_stop_on_first_field_failure` | PASS | - |
| `refactor_php_if_elseif_to_match` | fail | **baseline_gap_glob**: wanted "**/LogLevel.php", model wrote "LogLevel.php" (correct content, unmatchable path) |
| `refactor_php_extract_email_validator` | fail | **baseline_gap_multifile**: expected file matching "**/Validator.php" not created (got: Validator.php) |
| `refactor_php_carrier_shipping_cost_to_shared_helper` | fail | **baseline_gap_glob**: wanted "**/Shipping.php", model wrote "Shipping.php" (correct content, unmatchable path) |
| `optimize_php_flatten_array_merge` | fail | **baseline_gap_glob**: wanted "**/BatchFlattener.php", model wrote "BatchFlattener.php" (correct content, unmatchable path) |
| `optimize_php_allowlist_in_array_loop` | fail | **baseline_gap_glob**: wanted "**/AllowlistFilter.php", model wrote "AllowlistFilter.php" (correct content, unmatchable path) |
| `fix_php_unsafe_unserialize` | fail | **baseline_gap_glob**: wanted "**/SessionStore.php", model wrote "SessionStore.php" (correct content, unmatchable path) |
| `fix_php_switch_missing_break_permissions` | fail | **baseline_gap_glob**: wanted "**/Permissions.php", model wrote "Permissions.php" (correct content, unmatchable path) |
| `fix_php_magic_hash_token_compare` | fail | **baseline_gap_glob**: wanted "**/TokenVerifier.php", model wrote "TokenVerifier.php" (correct content, unmatchable path) |
| `fix_php_foreach_reference_alias` | fail | **baseline_gap_glob**: wanted "**/Normalizer.php", model wrote "Normalizer.php" (correct content, unmatchable path) |
| `fix_php_elvis_swallows_falsy_config` | fail | **baseline_gap_glob**: wanted "**/Config.php", model wrote "Config.php" (correct content, unmatchable path) |
| `fix_php_array_filter_reindex` | fail | **baseline_gap_glob**: wanted "**/UserList.php", model wrote "UserList.php" (correct content, unmatchable path) |
| `create_php_money_value_object` | fail | **baseline_gap_glob**: wanted "**/Money.php", model wrote "Money.php" (correct content, unmatchable path) |
| `create_php_json_event_summary` | fail | **baseline_gap_glob**: wanted "**/EventSummary.php", model wrote "EventSummary.php" (correct content, unmatchable path) |
| `create_php_csv_dedupe_latest` | fail | **baseline_gap_glob**: wanted "**/UserImporter.php", model wrote "UserImporter.php" (correct content, unmatchable path) |
| `add_tests_php_title_case_small_words` | fail | **baseline_gap_glob**: wanted "**/TitleCaseTest.php", model wrote "TitleCaseTest.php" (correct content, unmatchable path) |
| `add_tests_php_password_policy` | fail | **baseline_gap_glob**: wanted "**/PasswordTest.php", model wrote "PasswordTest.php" (correct content, unmatchable path) |
| `add_tests_php_cart_total` | fail | **baseline_gap_glob**: wanted "**/CartTest.php", model wrote "CartTest.php" (correct content, unmatchable path) |
| `add_php_lru_cache` | fail | **baseline_gap_glob**: wanted "**/LruCache.php", model wrote "LruCache.php" (correct content, unmatchable path) |

#### Python (1/3) - `base-python-1` (25/40)
| Case | Verdict | Reason |
|---|---|---|
| `create_django_management_command` | PASS | - |
| `create_django_book_model` | PASS | - |
| `add_tests_typer_cli` | PASS | - |
| `add_tests_tasktracker_progress_summary` | PASS | - |
| `add_tests_sql_first_login_per_user` | PASS | - |
| `add_tests_python_shipping_tiers` | PASS | - |
| `add_tests_python_run_length_encoding` | PASS | - |
| `add_tests_python_roman_numeral` | PASS | - |
| `add_tests_python_parse_duration` | PASS | - |
| `add_tests_python_ordinal_suffix` | PASS | - |
| `add_tests_python_next_business_day` | PASS | - |
| `add_tests_python_merge_intervals` | PASS | - |
| `add_tests_python_median` | PASS | - |
| `add_tests_python_camel_to_snake` | PASS | - |
| `add_tests_flask_endpoint` | PASS | - |
| `add_tests_fastapi_multi` | PASS | - |
| `add_rate_limiter_sliding_window` | PASS | - |
| `add_notification_webhook_channel` | PASS | - |
| `add_feature_flag_percentage_rollout` | PASS | - |
| `add_docs_is_palindrome` | PASS | - |
| `add_docs_format_cents` | PASS | - |
| `add_docs_flatten_one_level` | PASS | - |
| `add_docs_dedupe_preserve_order` | PASS | - |
| `add_docs_chunk_list` | PASS | - |
| `add_csv_daily_totals_with_gap_fill` | PASS | - |
| `create_flask_reviews_blueprint` | fail | **baseline_gap_multifile**: expected file matching "app/__init__.py" not created (got: app/routes/reviews.py) |
| `create_fastapi_tags_resource` | fail | **baseline_gap_multifile**: expected file matching "app/main.py" not created (got: app/routers/tags.py) |
| `create_django_health_view` | fail | **baseline_gap_multifile**: expected file matching "urls.py" not created (got: views.py) |
| `add_tests_sql_top_spender_view` | fail | **oracle_fail**: "test_top_spender.py" missing expected content "assert" |
| `add_tests_python_cidr_contains` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 mutation_check.py |
| `add_tests_pandas_cleaning` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 mutation_check.py |
| `add_tests_flask_multi` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 mutation_check.py |
| `add_tests_django_view` | fail | **oracle_fail**: "test_price.py" does not match regex "assert\s" |
| `add_tasktracker_tasks_csv_export` | fail | **baseline_gap_multifile**: expected file matching "*/routers/projects.py" not created (got: routers/projects.py) |
| `add_tasktracker_task_due_date_migration` | fail | **baseline_gap_multifile**: expected file matching "*/versions/*due_date*" not created (got: versions/outputdue_dateoutput) |
| `add_tasktracker_task_comments` | fail | **baseline_gap_multifile**: expected file matching "*/models/comment.py" not created (got: models/comment.py) |
| `add_tasktracker_project_archiving` | fail | **baseline_gap_multifile**: expected file matching "*/models/project.py" not created (got: models/project.py) |
| `add_tasktracker_dockerfile` | fail | **baseline_gap_multifile**: expected file matching ".dockerignore" not created (got: Dockerfile) |
| `add_github_actions_ci_python` | fail | **oracle_fail**: "ci.yml" missing expected content "pytest" |
| `add_fastapi_search_filter` | fail | **baseline_gap_multifile**: expected file matching "app/routers/products.py" not created (got: app/repository.py) |

#### Python (2/3) - `base-python-2` (34/40)
| Case | Verdict | Reason |
|---|---|---|
| `optimize_django_bulk_create` | PASS | - |
| `fix_ssrf_url_validation` | PASS | - |
| `fix_sql_injection_python` | PASS | - |
| `fix_python_unsafe_pickle_load` | PASS | - |
| `fix_python_subprocess_shell_injection` | PASS | - |
| `fix_python_mutable_default_arg` | PASS | - |
| `fix_python_mass_assignment` | PASS | - |
| `fix_python_late_binding_closure` | PASS | - |
| `fix_python_html_injection_escape` | PASS | - |
| `fix_pydantic_validator_bug` | PASS | - |
| `fix_path_traversal_fastapi` | PASS | - |
| `fix_pandas_merge_row_explosion` | PASS | - |
| `fix_jwt_signature_not_verified` | PASS | - |
| `fix_flask_missing_field_validation` | PASS | - |
| `fix_fastapi_wrong_status_code` | PASS | - |
| `fix_fastapi_password_leak` | PASS | - |
| `fix_django_sql_injection_raw` | PASS | - |
| `fix_django_pagination_off_by_one` | PASS | - |
| `fix_django_orm_n_plus_one` | PASS | - |
| `fix_django_missing_security_header` | PASS | - |
| `fix_django_form_validation` | PASS | - |
| `fix_django_exclude_filter_bug` | PASS | - |
| `fix_csv_manual_split_quoting` | PASS | - |
| `fix_asyncio_sequential_awaits` | PASS | - |
| `fix_asyncio_missing_await` | PASS | - |
| `fix_asyncio_lost_update` | PASS | - |
| `fix_asyncio_blocking_sleep` | PASS | - |
| `create_typer_greet_cli` | PASS | - |
| `create_todo_api_fastapi` | PASS | - |
| `create_sqlite_csv_pipeline` | PASS | - |
| `create_python_sessionize_events` | PASS | - |
| `create_pandas_sales_summary` | PASS | - |
| `create_json_to_csv_flatten` | PASS | - |
| `create_health_endpoint_python` | PASS | - |
| `fix_tasktracker_search_sql_injection` | fail | **baseline_gap_multifile**: expected file matching "*/routers/search.py" not created (got: routers/search.py) |
| `fix_iso_date_parsing` | fail | **oracle_fail**: expected file matching "dates.py" not created (got: none) |
| `fix_flask_unique_constraint` | fail | **baseline_gap_multifile**: expected file matching "app/routes/users.py" not created (got: app/repository.py) |
| `fix_flask_response_leak` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 1): python3 test_leak.py |
| `fix_fastapi_duplicate_email` | fail | **baseline_gap_multifile**: expected file matching "app/routers/users.py" not created (got: app/repository.py) |
| `create_flask_todo_api` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 1): python3 test_todo.py |

#### Python (3/3) - `base-python-3` (33/37)
| Case | Verdict | Reason |
|---|---|---|
| `upgrade_pydantic_validate_all_to_validate_default` | PASS | - |
| `upgrade_pydantic_v1_to_v2` | PASS | - |
| `upgrade_pydantic_root_validator_to_model_validator` | PASS | - |
| `upgrade_pydantic_orm_mode_to_from_attributes` | PASS | - |
| `upgrade_pydantic_min_anystr_length_to_str_min_length` | PASS | - |
| `upgrade_pydantic_field_regex_to_pattern` | PASS | - |
| `upgrade_pydantic_field_const_to_literal` | PASS | - |
| `upgrade_pydantic_config_fields_alias_to_field_alias` | PASS | - |
| `upgrade_pydantic_anystr_strip_whitespace_to_str_strip_whitespace` | PASS | - |
| `upgrade_pydantic_allow_population_by_field_name_to_populate_by_name` | PASS | - |
| `upgrade_pydantic_allow_mutation_to_frozen_configdict` | PASS | - |
| `refactor_sqlalchemy_model_relationships` | PASS | - |
| `refactor_sql_scalar_subqueries_to_cte` | PASS | - |
| `refactor_sql_parallel_case_to_mapping_cte` | PASS | - |
| `refactor_python_ospath_to_pathlib` | PASS | - |
| `refactor_python_nested_ifs_to_guard_clauses` | PASS | - |
| `refactor_python_manual_loops_to_zip_star` | PASS | - |
| `refactor_python_index_loop_to_zip` | PASS | - |
| `refactor_python_duplicate_retry_to_decorator` | PASS | - |
| `refactor_python_dict_dispatch` | PASS | - |
| `refactor_python_class_to_dataclass` | PASS | - |
| `refactor_duplicate_route_logic_fastapi` | PASS | - |
| `refactor_django_duplicate_views` | PASS | - |
| `optimize_slow_dedup_python` | PASS | - |
| `optimize_python_sort_per_query` | PASS | - |
| `optimize_python_repeated_max_extraction` | PASS | - |
| `optimize_python_prepend_accumulation` | PASS | - |
| `optimize_python_pair_sum_nested_loop` | PASS | - |
| `optimize_python_list_membership_to_set` | PASS | - |
| `optimize_python_dict_merge_loop` | PASS | - |
| `optimize_python_count_in_loop` | PASS | - |
| `optimize_pandas_iterrows` | PASS | - |
| `optimize_n_plus_one_sqlalchemy` | PASS | - |
| `refactor_tasktracker_shared_pagination` | fail | **baseline_gap_multifile**: expected file matching "*/crud/pagination.py" not created (got: crud/pagination.py) |
| `refactor_tasktracker_or404_helpers` | fail | **baseline_gap_multifile**: expected file matching "*/routers/shared.py" not created (got: routers/shared.py) |
| `refactor_flask_extract_service` | fail | **baseline_gap_multifile**: expected file matching "app/routes/quotes.py" not created (got: app/services.py) |
| `refactor_fastapi_extract_service` | fail | **baseline_gap_multifile**: expected file matching "app/routers/quotes.py" not created (got: app/services.py) |

#### Ruby - `base-ruby` (6/20)
| Case | Verdict | Reason |
|---|---|---|
| `refactor_ruby_pluralize_methods_to_shared_helper` | PASS | - |
| `refactor_ruby_case_when_to_hash_dispatch` | PASS | - |
| `optimize_ruby_array_delete_loop` | PASS | - |
| `add_ruby_order_coupon_percentage_and_fixed` | PASS | - |
| `add_ruby_cart_bulk_discount_tiers` | PASS | - |
| `adapt_removed_helper_ruby_dynamic` | PASS | - |
| `refactor_ruby_extract_validation_module` | fail | **baseline_gap_multifile**: expected file matching "**/validations.rb" not created (got: validations.rb) |
| `optimize_ruby_membership_set` | fail | **baseline_gap_glob**: wanted "**/order_filter.rb", model wrote "order_filter.rb" (correct content, unmatchable path) |
| `fix_ruby_yaml_unsafe_load` | fail | **baseline_gap_glob**: wanted "**/config_loader.rb", model wrote "config_loader.rb" (correct content, unmatchable path) |
| `fix_ruby_shallow_dup_shared_nested` | fail | **baseline_gap_glob**: wanted "**/settings.rb", model wrote "settings.rb" (correct content, unmatchable path) |
| `fix_ruby_or_swallows_false_flag` | fail | **baseline_gap_glob**: wanted "**/feature_flags.rb", model wrote "feature_flags.rb" (correct content, unmatchable path) |
| `fix_ruby_hash_shared_default_array` | fail | **baseline_gap_glob**: wanted "**/grouper.rb", model wrote "grouper.rb" (correct content, unmatchable path) |
| `fix_ruby_each_instead_of_map` | fail | **baseline_gap_glob**: wanted "**/doubler.rb", model wrote "doubler.rb" (correct content, unmatchable path) |
| `fix_ruby_command_injection_line_counter` | fail | **baseline_gap_glob**: wanted "**/line_counter.rb", model wrote "line_counter.rb" (correct content, unmatchable path) |
| `create_ruby_sales_report` | fail | **baseline_gap_glob**: wanted "**/sales_report.rb", model wrote "sales_report.rb" (correct content, unmatchable path) |
| `create_ruby_paginator` | fail | **baseline_gap_glob**: wanted "**/paginator.rb", model wrote "paginator.rb" (correct content, unmatchable path) |
| `add_tests_ruby_time_ago` | fail | **baseline_gap_glob**: wanted "**/time_ago_test.rb", model wrote "time_ago_test.rb" (correct content, unmatchable path) |
| `add_tests_ruby_shipping_cost` | fail | **baseline_gap_glob**: wanted "**/shipping_test.rb", model wrote "shipping_test.rb" (correct content, unmatchable path) |
| `add_tests_ruby_csv_normalizer` | fail | **baseline_gap_glob**: wanted "**/row_normalizer_test.rb", model wrote "row_normalizer_test.rb" (correct content, unmatchable path) |
| `add_tests_ruby_balanced_parens` | fail | **baseline_gap_glob**: wanted "**/balanced_test.rb", model wrote "balanced_test.rb" (correct content, unmatchable path) |

#### Rust - `base-rust` (13/29)
| Case | Verdict | Reason |
|---|---|---|
| `refactor_rust_manual_loop_to_iterator_chain` | PASS | - |
| `refactor_axum_duplicate_handlers` | PASS | - |
| `optimize_rust_slice_intersection` | PASS | - |
| `fix_rust_utf8_byte_slice_panic` | PASS | - |
| `fix_rust_u32_sum_overflow` | PASS | - |
| `fix_axum_wrong_status_code` | PASS | - |
| `fix_axum_state_not_shared` | PASS | - |
| `fix_actix_missing_validation` | PASS | - |
| `create_rust_category_totals` | PASS | - |
| `create_health_endpoint_rust` | PASS | - |
| `create_axum_crud_todos` | PASS | - |
| `add_tests_rust_binary_search` | PASS | - |
| `add_tests_axum_handler_logic` | PASS | - |
| `refactor_rust_extract_discount_rule` | fail | **baseline_gap_multifile**: expected file matching "src/checkout.rs" not created (got: src/discount.rs) |
| `optimize_rust_vec_dedup` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 101): cargo test --offline |
| `optimize_rust_vec_contains_dedup` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 101): cargo test --offline |
| `fix_tokio_blocking_sleep` | fail | **oracle_fail**: "src/lib.rs" contains forbidden content "std::thread::sleep" |
| `fix_rust_shell_command_injection` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 101): cargo test --offline |
| `fix_rust_double_lock_deadlock` | fail | **oracle_fail:compile_error**: check_command failed in docker (exit 101): cargo test --offline |
| `fix_rust_arc_missing_clone` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 101): cargo test --offline |
| `fix_axum_path_traversal` | fail | **oracle_fail**: "src/main.rs" does not match regex "(canonicalize\|starts_with\|contains\s*\(\s*\"\.\.\"\|\.\.)" |
| `fix_axum_error_status_mapping` | fail | **oracle_fail**: "src/handlers.rs" missing expected content "conflict" |
| `create_rust_parallel_map` | fail | **oracle_fail:compile_error**: check_command failed in docker (exit 101): cargo test --offline :: trait bound `F: Send` not satisfied |
| `create_axum_reviews_module` | fail | **baseline_gap_multifile**: expected file matching "src/lib.rs" not created (got: src/repository.rs) |
| `create_actix_items_endpoint` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 1): python3 test_items.py |
| `add_tests_rust_shipping_tiers` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 mutation_check.py |
| `add_github_actions_ci_rust` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 1): python3 test_ci_yaml.py |
| `add_axum_query_filter` | fail | **baseline_gap_multifile**: expected file matching "src/handlers.rs" not created (got: src/repository.rs) |
| `adapt_compounding_config_rust_dynamic` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 101): cargo test --offline |

#### Shell/Docker/YAML/etc. (base image, 1/4) - `base-1` (20/40)
| Case | Verdict | Reason |
|---|---|---|
| `create_factorial` | PASS | - |
| `create_cpp_stack_class` | PASS | - |
| `create_c_linked_list` | PASS | - |
| `build_calculator_longhorizon` | PASS | - |
| `add_variable_validation_block` | PASS | - |
| `add_tests_cpp_stack_class` | PASS | - |
| `add_tests_cpp_anagram_checker` | PASS | - |
| `add_tests_c_balanced_brackets` | PASS | - |
| `add_terraform_variable_bounds_validation` | PASS | - |
| `add_terraform_iam_policy_scoped_to_bucket` | PASS | - |
| `add_multiple_environment_variables` | PASS | - |
| `add_healthcheck_to_compose` | PASS | - |
| `add_dockerignore_secrets_exclusion` | PASS | - |
| `add_cpp_ring_buffer_overwrite_oldest` | PASS | - |
| `add_cpp_matrix_transpose` | PASS | - |
| `add_compose_worker_service` | PASS | - |
| `add_compose_named_volume_persistence` | PASS | - |
| `add_compose_depends_service_healthy` | PASS | - |
| `add_check_constraint_for_validation` | PASS | - |
| `adapt_config_change_dynamic` | PASS | - |
| `create_backup_script` | fail | **oracle_fail**: expected file matching "backup.sh" not created (got: none) |
| `add_tests_shell_ipv4_validator` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 mutation_check.py |
| `add_tests_schema_constraints` | fail | **oracle_fail**: "test_constraints.py" missing expected content "assert" |
| `add_tests_for_utility_script` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 mutation_check.py |
| `add_terraform_s3_lifecycle_rule` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 check_lifecycle.py |
| `add_shell_deploy_script_rollback` | fail | **oracle_fail**: expected file matching "deploy.sh" not created (got: none) |
| `add_k8s_service_selector_port_mapping` | fail | **baseline_gap_glob**: wanted "**/service.yaml", model wrote "service.yaml" (correct content, unmatchable path) |
| `add_k8s_security_context_hardening` | fail | **baseline_gap_glob**: wanted "**/deployment.yaml", model wrote "deployment.yaml" (correct content, unmatchable path) |
| `add_k8s_resourcequota_limitrange` | fail | **baseline_gap_glob**: wanted "**/quota.yaml", model wrote "quota.yaml" (correct content, unmatchable path) |
| `add_k8s_pod_disruption_budget` | fail | **baseline_gap_glob**: wanted "**/pdb.yaml", model wrote "pdb.yaml" (correct content, unmatchable path) |
| `add_k8s_networkpolicy_default_deny` | fail | **baseline_gap_glob**: wanted "*/network-policy.yaml", model wrote "network-policy.yaml" (correct content, unmatchable path) |
| `add_k8s_ingress_tls_routing` | fail | **baseline_gap_glob**: wanted "**/ingress.yaml", model wrote "ingress.yaml" (correct content, unmatchable path) |
| `add_k8s_hpa_with_resource_requests` | fail | **baseline_gap_glob**: wanted "*/hpa.yaml", model wrote "hpa.yaml" (correct content, unmatchable path) |
| `add_k8s_configmap_env_from` | fail | **baseline_gap_glob**: wanted "**/configmap.yaml", model wrote "configmap.yaml" (correct content, unmatchable path) |
| `add_github_actions_per_job_permissions` | fail | **baseline_gap_glob**: wanted "**/ci.yml", model wrote "ci.yml" (correct content, unmatchable path) |
| `add_github_actions_node_matrix` | fail | **baseline_gap_glob**: wanted "**/test.yml", model wrote "test.yml" (correct content, unmatchable path) |
| `add_github_actions_matrix` | fail | **baseline_gap_glob**: wanted "**/ci.yml", model wrote "ci.yml" (correct content, unmatchable path) |
| `add_github_actions_docs_path_filter` | fail | **baseline_gap_glob**: wanted "**/ci.yml", model wrote "ci.yml" (correct content, unmatchable path) |
| `add_github_actions_cache_concurrency` | fail | **baseline_gap_glob**: wanted "**/ci.yml", model wrote "ci.yml" (correct content, unmatchable path) |
| `add_github_actions_artifact_passing` | fail | **baseline_gap_glob**: wanted "**/ci.yml", model wrote "ci.yml" (correct content, unmatchable path) |

#### Shell/Docker/YAML/etc. (base image, 2/4) - `base-2` (29/40)
| Case | Verdict | Reason |
|---|---|---|
| `fix_dockerfile_security_hardening` | PASS | - |
| `fix_dockerfile_run_as_root` | PASS | - |
| `fix_dockerfile_pinned_base` | PASS | - |
| `fix_dockerfile_layer_order` | PASS | - |
| `fix_cpp_memory_leak` | PASS | - |
| `fix_compose_pin_images_restart_limits` | PASS | - |
| `fix_compose_missing_depends_on` | PASS | - |
| `fix_compose_hardcoded_secret` | PASS | - |
| `fix_c_off_by_one` | PASS | - |
| `fix_c_format_string_vulnerability` | PASS | - |
| `fix_c_buffer_overflow` | PASS | - |
| `create_web_db_compose` | PASS | - |
| `create_view_for_reporting` | PASS | - |
| `create_variables_and_outputs` | PASS | - |
| `create_users_table_schema` | PASS | - |
| `create_stable_sort_metamorphic` | PASS | - |
| `create_sql_running_total` | PASS | - |
| `create_sql_rank_per_group` | PASS | - |
| `create_sql_month_over_month` | PASS | - |
| `create_sql_migration_split_column` | PASS | - |
| `create_sql_migration_add_column` | PASS | - |
| `create_server_c` | PASS | - |
| `create_reverse_string_js` | PASS | - |
| `create_orders_with_foreign_key` | PASS | - |
| `create_number_stats_property` | PASS | - |
| `create_makefile_c_build` | PASS | - |
| `create_makefile_build_run_clean` | PASS | - |
| `create_hello_world` | PASS | - |
| `create_fibonacci` | PASS | - |
| `fix_k8s_hardcoded_password_to_secret` | fail | **baseline_gap_glob**: wanted "*/secret.yaml", model wrote "secret.yaml" (correct content, unmatchable path) |
| `fix_github_actions_pull_request_target_secrets` | fail | **baseline_gap_glob**: wanted "**/pr-checks.yml", model wrote "pr-checks.yml" (correct content, unmatchable path) |
| `fix_github_actions_pin_to_sha` | fail | **baseline_gap_glob**: wanted "**/release.yml", model wrote "release.yml" (correct content, unmatchable path) |
| `fix_github_actions_missing_checkout` | fail | **baseline_gap_glob**: wanted "**/ci.yml", model wrote "ci.yml" (correct content, unmatchable path) |
| `fix_github_actions_least_privilege` | fail | **baseline_gap_glob**: wanted "**/deploy.yml", model wrote "deploy.yml" (correct content, unmatchable path) |
| `fix_dockerfile_layer_caching_order` | fail | **oracle_fail**: "Dockerfile" missing expected content "package.json" |
| `create_sh_rotate_logs` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 test_rotate.py |
| `create_sh_env_check` | fail | **oracle_fail**: expected file matching "preflight.sh" not created (got: none) |
| `create_k8s_deployment_probes` | fail | **baseline_gap_glob**: wanted "**/deployment.yaml", model wrote "deployment.yaml" (correct content, unmatchable path) |
| `create_k8s_backup_cronjob` | fail | **baseline_gap_glob**: wanted "*/backup-cronjob.yaml", model wrote "backup-cronjob.yaml" (correct content, unmatchable path) |
| `create_github_actions_nightly_cron` | fail | **baseline_gap_glob**: wanted "**/nightly-cleanup.yml", model wrote "nightly-cleanup.yml" (correct content, unmatchable path) |

#### Shell/Docker/YAML/etc. (base image, 3/4) - `base-3` (30/40)
| Case | Verdict | Reason |
|---|---|---|
| `refactor_sql_subquery_to_join` | PASS | - |
| `refactor_sql_or_chain_to_in_clause` | PASS | - |
| `refactor_makefile_pattern_rule` | PASS | - |
| `refactor_duplicate_locals` | PASS | - |
| `refactor_dockerfile_multistage_build` | PASS | - |
| `refactor_dockerfile_multistage` | PASS | - |
| `refactor_dockerfile_go_multistage_scratch` | PASS | - |
| `refactor_denormalized_table` | PASS | - |
| `refactor_cpp_clamp_functions_to_template` | PASS | - |
| `refactor_compose_env_blocks_to_yaml_anchor` | PASS | - |
| `refactor_compose_duplicate_env` | PASS | - |
| `refactor_c_duplicated_validation_to_helper` | PASS | - |
| `refactor_c_duplicate_functions` | PASS | - |
| `optimize_sql_or_to_union` | PASS | - |
| `optimize_missing_index` | PASS | - |
| `optimize_c_strlen_in_loop_condition` | PASS | - |
| `multi_prompt_session` | PASS | - |
| `modify_add_type_hints` | PASS | - |
| `harden_terraform_s3_bucket` | PASS | - |
| `fix_wrong_join_type` | PASS | - |
| `fix_terraform_security_group_open_ssh` | PASS | - |
| `fix_terraform_fmt_violation` | PASS | - |
| `fix_sql_null_comparison` | PASS | - |
| `fix_sql_left_join_filter` | PASS | - |
| `fix_sql_injection_query` | PASS | - |
| `fix_sql_groupwise_max` | PASS | - |
| `fix_shell_deploy_script_trap_cleanup` | PASS | - |
| `fix_missing_not_null_constraint` | PASS | - |
| `fix_makefile_missing_build_dependency` | PASS | - |
| `fix_makefile_incremental_rebuild` | PASS | - |
| `refactor_shell_env_blocks_to_loop` | fail | **baseline_gap_glob**: wanted "*/deploy_report.sh", model wrote "deploy_report.sh" (correct content, unmatchable path) |
| `refactor_sh_dedupe_functions` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 test_backup.py |
| `refactor_duplicate_validation_functions` | fail | **oracle_fail**: expected file matching "validate.sh" not created (got: none) |
| `optimize_c_bubble_to_qsort` | fail | **oracle_fail**: expected file matching "sort_dedup.c" not created (got: none) |
| `fix_unquoted_variable_bug` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 test_count_lines.py |
| `fix_shell_report_path_traversal` | fail | **oracle_fail**: expected file matching "read_report.sh" not created (got: none) |
| `fix_shell_backup_script_hardening` | fail | **baseline_gap_glob**: wanted "*/backup.sh", model wrote "backup.sh" (correct content, unmatchable path) |
| `fix_sh_word_splitting_find` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 test_archive.py |
| `fix_sh_set_e_pipeline` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 test_check.py |
| `fix_script_command_injection` | fail | **oracle_fail:assertion_failure**: check_command failed in docker (exit 1): python3 test_injection.py |

#### Shell/Docker/YAML/etc. (base image, 4/4) - `base-4` (1/2)
| Case | Verdict | Reason |
|---|---|---|
| `refactor_terraform_foreach_locals` | PASS | - |
| `refactor_terraform_per_env_variables_to_map` | fail | **oracle_fail:runtime_error**: check_command failed in docker (exit 1): python3 check_instance_types.py |

#### Failure-kind totals across all completed chunks so far
| kind | count |
|---|---|
| baseline_gap_glob | 87 |
| baseline_gap_multifile | 44 |
| oracle_fail:assertion_failure | 22 |
| oracle_fail:runtime_error | 22 |
| oracle_fail | 22 |
| oracle_fail:compile_error | 4 |
| **total cases** | 510 |
| **pass** | 309 |

</details>

### 10. FINAL: gemma4:12b calibration complete (510/510)

All 17 chunks done, no failures, no poisoned data. 510 of 510 cases
measured (100%).

- **Raw pass rate: 309/510 (61%)**
- **Fair pass rate (excluding 131 baseline-gap cases,
  26% of the corpus): 309/379 (82%)**

#### Per-language: raw vs. fair, and why they diverge

The raw per-language numbers below (matching `analyze_corpus.py`'s own
output) are badly misleading for several tracks - not because the model
is weak in those languages, but because the corpus's own path
conventions for those languages are almost entirely unmeasurable by
the raw-model baseline driver (see section 7's standing finding). The
`fair` column excludes those cases; `n` is the fair sample size, which
for 5 languages is now too small to mean much - flagged explicitly.

| Language | Raw | Fair | Fair n | Baseline-gap share | Read |
|---|---|---|---|---|---|
| makefile | 5/5 (100%) | 5/5 (100%) | 5 | 0/5 (0%) | **sample too small to read (5 case(s))** |
| cpp | 7/7 (100%) | 7/7 (100%) | 7 | 0/7 (0%) | well-measured |
| sql | 23/25 (92%) | 23/25 (92%) | 25 | 0/25 (0%) | well-measured |
| c | 10/11 (91%) | 10/11 (91%) | 11 | 0/11 (0%) | well-measured |
| hcl | 10/12 (83%) | 10/12 (83%) | 12 | 0/12 (0%) | well-measured |
| python | 97/120 (81%) | 97/105 (92%) | 105 | 15/120 (12%) | well-measured |
| dockerfile | 8/10 (80%) | 8/9 (89%) | 9 | 1/10 (10%) | well-measured |
| javascript | 45/58 (78%) | 45/53 (85%) | 53 | 5/58 (9%) | well-measured |
| typescript | 20/29 (69%) | 20/25 (80%) | 25 | 4/29 (14%) | well-measured |
| go | 24/35 (69%) | 24/29 (83%) | 29 | 6/35 (17%) | well-measured |
| csharp | 18/29 (62%) | 18/26 (69%) | 26 | 3/29 (10%) | well-measured |
| rust | 13/29 (45%) | 13/26 (50%) | 26 | 3/29 (10%) | well-measured |
| yaml | 10/32 (31%) | 10/10 (100%) | 10 | 22/32 (69%) | mostly corpus/driver mismatch, not model capability |
| ruby | 6/20 (30%) | 6/6 (100%) | 6 | 14/20 (70%) | **sample too small to read (6 case(s))** |
| java | 9/35 (26%) | 9/11 (82%) | 11 | 24/35 (69%) | mostly corpus/driver mismatch, not model capability |
| kotlin | 2/18 (11%) | 2/4 (50%) | 4 | 14/18 (78%) | **sample too small to read (4 case(s))** |
| shell | 1/16 (6%) | 1/14 (7%) | 14 | 2/16 (12%) | **genuinely low even excluding gap cases - real weak spot, or an oracle issue worth a closer look** |
| php | 1/19 (5%) | 1/1 (100%) | 1 | 18/19 (95%) | **sample too small to read (1 case(s))** |

**The takeaway for the corpus discussion this was all in service of:**
php, yaml, ruby, kotlin (and to a lesser extent java) are not currently
measuring gemma4:12b's actual capability in those languages via this
driver - they're measuring whether the case needs more than the
baseline can give it, which is a known, structural, and (per the
earlier corpus-tooling finding) already-diagnosed property of those
cases, not a fact about the model. A CLI-agent driver (aider,
Claude Code, etc. - which write real project layouts) would not hit
this ceiling and is the honest way to measure those tracks.

### 11. `shell` root-caused: a deadline-timeout problem, not a path or oracle issue

Root-caused on request. The 7% (1/14) figure in section 10 undercounted the
real problem, because it only excluded baseline-gap cases - it lumped
deadline timeouts in with genuine content failures.

**The real finding: gemma4:12b is disproportionately slow generating shell
specifically, and it's costing real measurements.**

- **5 of 16 shell cases (31%) hit their case deadline** - the driver
  hard-cut generation before it ever finished. Corpus-wide the deadline
  rate is 12/510 (2.4%); shell is ~13x that, and by far the most
  concentrated (next highest: javascript 3/58 = 5%).
- All 5 hit their declared limit almost exactly (150.1s vs 150s declared,
  150.0/150s, 180.0/180s, 120.0/120s, 120.0/120s) - confirmed hard
  truncation mid-generation, not five cases that coincidentally ran long
  and finished anyway.
- **Even the 11 shell cases that DID complete ran 39% slower than the
  corpus mean** (82.7s vs 59.7s corpus-wide), several right at the edge of
  their own budget (94-135s) rather than comfortably under it. This isn't
  five unlucky prompts - it's systemic across the whole language.
- Checked whether this is a prompt-complexity artifact: pulled the actual
  prompt text for two timed-out cases
  (`create_backup_script`, `fix_shell_report_path_traversal`). Both are
  ordinary single-paragraph specs, no longer or more convoluted than a
  typical corpus prompt in any other language. Points toward the MODEL
  being slower/more deliberative on shell specifically - plausibly because
  POSIX shell correctness (quoting, word-splitting, path-traversal
  validation - both sampled cases were exactly this kind of fiddly
  correctness work) induces more hedging/self-correction in generation -
  rather than the prompts themselves demanding it.

**Corrected fair pass rate, excluding BOTH baseline-gap (2) and deadline
timeouts (5), not just baseline-gap:** of the 9 cases where gemma4:12b
actually finished a real attempt within budget, 1 passed - **11% (1/9)**,
not the 7% (1/14) reported in section 10. Still low, and now isolated from
the timeout confound: this is a genuine content-quality signal, not
partially an artifact of an unlucky mid-generation cutoff.

**Of the 8 genuine content failures, two show the model's OWN generated
shell code with real defects** - not the case's reference solution, the
MODEL's output:
- `add_tests_shell_ipv4_validator`: the model's own generated test script
  has a syntax error - `Syntax error: "done" unexpected (expecting "then")`.
- `add_tests_for_utility_script`: the model's own generated test script
  fails at runtime - `local: not in a function` (used the `local` keyword
  outside a function definition, a real POSIX-shell scoping bug).

**Is this an optarena bug?** No. The deadline mechanism (Round 3's F-05, one
whole-case budget) worked exactly as designed - it protects a run from a
hung generation, and did precisely that here. The declared timeouts
(120-180s) aren't unusually tight relative to the rest of the corpus.
This is a genuine, well-evidenced gemma4:12b characteristic - slow and
frequently timing out specifically on shell-scripting generation - which
is exactly the kind of signal a calibration run exists to surface, not a
defect in the harness or the corpus's shell cases.

**Not yet run:** the `gemma3:1b` weak-anchor pass (queued since early in
the session, over the 309 cases gemma4:12b passed) - needed to compute
actual discrimination (does a case separate a weak model from a strong
one), as opposed to this single-model calibration, which only tells us
what one mid-size model can already do.

### 12. Two real code fixes to `optarena/` itself

Everything above this section was investigation, an external orchestrator
script, and documentation - **zero changes to the `optarena/` package**.
This section is different: these are real fixes to `optarena/cases.py`,
`optarena/drivers/__init__.py`, and `optarena/cli.py`, made in response to
the calibration findings above. Flagged explicitly because of how
different the risk profile is - these change behavior for every future
run, not just this run's data.

#### Fix A: `**/X` path-pattern matching (`cases.py`)

**The bug.** `check_expected` (the pass/fail oracle) and `trajectory_stats`
(off-target-file detection) each had their own copy of the same
`fnmatch`-based path matching, and `fnmatch` has no concept of "/" as a
path separator - a `"**/X"` pattern can only ever match a candidate that
LITERALLY CONTAINS "/". A flat file at the workspace root - exactly what
every baseline/SDK driver writes, since none of them have real file tools -
can never satisfy it, even when its name and content are exactly right.
Every other tool's globstar convention (bash's `globstar`, rsync excludes,
Python 3.13's own `glob.translate`/`pathlib` matching, Ant filesets)
defines `**` as "zero or more directories", which explicitly includes the
zero case. Nobody had deliberately chosen the stricter reading - no
comment anywhere addressed it - and it was silently costing 87 of the 510
built-in cases a shape-check failure regardless of model quality (found by
tracing jvm-1's unexplained 15% raw pass rate down to
`expected file matching "**/MessageFormatterTest.java" not created
(got: MessageFormatterTest.java)` - the model's own output, correct,
sitting right there in "got:").

**The fix.** New `cases.path_pattern_matches(rel, pattern)`, the single
source of truth both `check_expected` and `trajectory_stats` now call
(deduplicating logic that had already drifted apart once - that
duplication, not a deliberate design choice, is exactly how this went
unnoticed). Adds one narrow, deliberately scoped extra check: for a
pattern with a leading `"**/"`, also try matching with that prefix
stripped. Scoped to an EXACT leading `"**/"` only - not `"**"` appearing
elsewhere - because that is the only shape used anywhere in the built-in
corpus (verified by scanning every `path_pattern` in `optarena/cases/*.json`
before writing the fix, not assumed). A single `"*"` prefix (25 corpus
cases, e.g. `"*/routes/foo.ts"`) is deliberately left unchanged: single-star
conventionally means "exactly one path segment", and `concrete_target` has
no way to invent an arbitrary wrapper directory name for it - that class of
case stays genuinely, structurally unsatisfiable by a flat-file writer,
which is correct, not a bug.

**Verification before trusting it:**
- Direct unit checks: a bare file now matches a `"**/X"` pattern; a real
  nested path (what a genuine CLI-agent driver produces) still matches
  unchanged; a `"*/X"` single-star pattern is confirmed NOT loosened; `**`
  appearing mid-pattern (not a prefix) is confirmed unaffected;
  case-insensitivity preserved.
- Ran the exact real failing case from the calibration run
  (`add_tests_greeting_service_junit`) with the model's actual bare-file
  output simulated: the path-not-found failure is gone; content-pattern
  checks now run and correctly fail on the fake content, proving the fix
  only affects whether the file is FOUND, nothing about content judging.
- **Regression check against real Docker, not assumed safe:** identified
  every case with a `"**/"`-prefixed pattern AND declared
  `broken_solutions` (81 cases - the highest-risk set, since a broken
  variant relying on strict path matching to correctly fail is exactly what
  a looser match could silently break) and ran the real, sandboxed
  `optarena cases verify` against all of them. **231 variants checked
  across 81 cases, zero violations** - every reference solution still
  passes, every broken/unmodified variant still correctly fails.
- **Full corpus verification** (`optarena cases verify`, all 510 cases, 1289
  variants) - completed with 22 pre-existing violations, **confirmed
  unrelated to this fix**: every one of the 22 uses a plain literal
  `path_pattern` with no `"**/"` anywhere in it (checked individually, not
  assumed), and `git diff --stat` confirms this session's changes touch zero
  Rust-track files and no mutation-testing code (`mutation_check.py` isn't
  part of `optarena/` at all - it's a per-case test file baked into case
  JSON). All 22 are Rust/axum `cargo`/mutation-check oracle issues (19
  "broken variant PASSED the oracle", 3 "reference solution FAILED the
  oracle") - a genuine, previously-undiscovered corpus-content problem,
  surfaced only because this was the first time the FULL corpus verify had
  actually been run end-to-end this session. Out of scope for this pass (corpus
  *content* fixes are explicitly deferred, same as the 44-case gap from
  Round 4) - logged here as a new finding for that future pass,
  not something addressed now.

  **Process note, for honesty:** the first attempt at this check
  (`optarena cases verify | tail -15`) reported a misleadingly clean "exit
  code 0" - that was `tail`'s exit code, not the real command's, and the
  pipe discarded everything but the last 15 lines. Caught by noticing actual
  violation lines in what little output survived the truncation, contradicting
  the "success" framing. Re-ran with output redirected to a file (`>`, not
  `| tail`) and the real exit code captured explicitly, which is what
  produced the complete, trustworthy 22-violation list above.
- 8 new unit tests (`PathPatternMatchesTests`) plus 2 reused in
  `BaselineIncompatibleTests` covering the exact scenarios above.
- 334 pre-existing tests: no regressions.

**Effect on the corpus:** baseline-incompatible case count dropped from
133 to 52 (see Fix B) - the 81-case gap this fix closes.

#### Fix B: preflight warning for structurally unwinnable cases (`cases.py`, `drivers/__init__.py`, `cli.py`)

**The gap.** Nothing in optarena warned a user, before a run started, that
some of their selected cases could never pass under the driver they chose -
regardless of model quality - because that driver has no file-editing
tools. This is exactly what cost hours of investigation (an
unexplained 15% jvm pass rate that took manual tracing to explain). A user
running `ollama-chat` before this fix got a confusingly low number with zero
explanation.

**The fix, three pieces:**
1. `DRIVERS` registry (`drivers/__init__.py`) gains an explicit
   `file_tools: bool` field per driver - `True` for every `cli`-kind driver
   (aider, Claude Code, Codex, OpenCode, Goose, Qwen Code - real file
   tools), `False` for every `baseline`/`sdk`-kind driver (openai-chat,
   ollama-chat, and all six SDK-agent drivers - all share
   `concrete_target`'s "one flat file" write pattern). A `RegistryTests`
   test asserts every driver declares it and that it currently always
   equals `kind == "cli"`, so a future driver that's missing the field (or
   breaks that pattern without updating the test) fails loudly instead of
   silently under-warning.
2. `cases.baseline_incompatible(case) -> str | None` - can a flat-file
   writer EVER satisfy this case, regardless of what the model produces?
   Deliberately built on the SAME functions that decide the real outcome
   (`concrete_target`, `path_pattern_matches`) rather than a separately
   maintained heuristic - two independent copies of one decision silently
   disagreeing is the exact failure class Fix A just cleaned up one
   function over; this avoids creating a third copy of it.
3. `cli._warn_baseline_incompatible`, called once per scenario in
   `cmd_run` before `run_scenario` - if the driver has no file tools, scans
   the resolved case set and prints a one-time note naming which cases
   (capped at 8, "+K more") cannot pass and why, then continues normally.
   Purely informational: best-effort (any failure resolving the driver or
   loading cases here is silently swallowed, since `run_scenario`
   immediately after raises the SAME error through its own already-correct
   handling), never changes the exit code, respects `--quiet`.

**Verified corpus-wide count after Fix A landed:** 52 of 510 cases remain
genuinely baseline-incompatible (down from 133 before Fix A - the 81-case
gap Fix A closed). Spot-checked: a pure `**/`-prefix case
(`add_tests_greeting_service_junit`) is now correctly `None` (winnable); a
genuinely multi-file case is still correctly flagged.

**Verification before trusting it:**
- End-to-end CLI run with a mixed case selection (one winnable, one
  multi-file): warning fires, names only the real offender, exit code
  unaffected (0), run proceeds normally.
- Confirmed silent for: `--quiet`, a `cli`-kind driver (aider), an unknown
  driver name (no crash), and a selection containing zero incompatible
  cases.
- 8 new unit tests (`BaselineIncompatibleTests`) plus 4 CLI-level
  integration tests (`BaselineIncompatiblePreflightWarningTests`) covering
  all of the above.

**Suite after both fixes:** 355 tests (was 334), all passing; `ruff` clean.

### 13. aider + gemma4:12b over the 52 baseline-incompatible cases (COMPLETE, 52/52)

**Why this ran:** validating a direct prediction from section 12/discussion -
if the 52 cases `cases.baseline_incompatible` flags are structurally
impossible for a flat-file driver, does a driver with REAL file tools
(aider) actually win them? Real data instead of a guess.

**Status: complete.** Paused mid-run at 24/52 for a while (driver sweep,
section 14, ran in between), then resumed cleanly - the pause left a stale
lock file (dead PID) which the orchestrator's own liveness check correctly
detected and took over, exactly as designed. One orphaned sandbox container
from the original pause (`optarena-tester-php`) and one from the interrupted
full-corpus verify attempt (`optarena-tester-dotnet`, unrelated to this run)
were both found and removed before/during resume. All 9/9 chunks completed
clean, no poisoning, no further incidents.

#### Final results: 25/52 (48%)

| Chunk | Result | Mean time |
|---|---|---|
| dotnet | 2/2 (100%) | 112.9s |
| go | 2/3 (67%) | 144.5s |
| jvm | 7/9 (78%) | 115.0s |
| node | 6/10 (60%) | 83.0s |
| php | 0/1 (0%) | 300.4s |
| python | 5/17 (29%) | 135.8s |
| ruby | 0/1 (0%) | 300.4s |
| rust | 2/3 (67%) | 198.3s |
| base image | 1/6 (17%) | 199.3s |
| **total** | **25/52 (48%)** | |

**The core prediction still holds, with a cleaner number once the known bug
is separated out.** `jvm` scored 15% RAW under `ollama-chat` (section 7/10)
- almost entirely the structural "flat-file writer can't produce a nested
project layout" ceiling from section 12. With real file tools, the same
track scores 78%.

#### The `setup_repo` context bug (found at 24/52, documented then) now confirmed at full scale: 11 of 52 cases affected, 0/11 passed

The bug itself (root cause, evidence, fix needed) was already fully
documented below and is unchanged - restating only the final scope now that
every `setup_repo` case in the 52-case list has actually been run:
**11 of 52 cases** share `setup_repo` (`express-ts-shortlink`: 4 cases in
the `node` chunk; `fastapi-tasktracker`: 7 cases, 6 in the `python` chunk
plus `fix_tasktracker_search_sql_injection`) - **every single one of the 11
failed**, and every one failed the same way: the expected file was simply
never created (`got: none`), consistent across both starter repos and all
three chunks they appear in. Zero exceptions in either direction (no
`setup_repo` case passed; no non-`setup_repo` case failed this specific
way) - about as clean a signature as a bug gets.

#### A second, distinct pattern in the remaining failures: aider+gemma4:12b frequently fails to produce ANY usable file on complex multi-file scaffolding tasks

Excluding the 11 known-bug cases, 16 of the remaining 41 failed - and most
of those 16 are NOT wrong-content failures, they're **zero-output**
failures on cases that DID have real `setup_files` context (ruling out the
`setup_repo` bug as the cause here):

- **8 hard 300s timeouts**, all on `create`/multi-file `refactor` tasks:
  `refactor_php_extract_email_validator`, `refactor_ruby_extract_validation_module`,
  `create_axum_reviews_module`, `fix_k8s_hardcoded_password_to_secret`,
  `refactor_shell_env_blocks_to_loop`, plus the 3 already found at 24/52
  (`create_gin_reviews_resource`, `create_spring_order_endpoint`,
  `refactor_java_extract_username_validator`). All either produced zero
  files or an incomplete edit before the clock ran out.
- **A few naturally-completed cases still wrote to the wrong path or
  nothing at all**: `refactor_fastapi_extract_service` (`got: none`),
  `create_fastapi_tags_resource` (wrote `app/main.py`/`app/repository.py`
  instead of the expected `app/routers/tags.py`), `refactor_flask_extract_service`
  (wrote to a doubled-up `app/app/services.py` - a real path-construction
  mistake in the MODEL's own output, not the driver).

**Not root-caused further at this point** (flagged the same way the original 3
timeouts were at 24/52) - plausibly gemma4:12b genuinely struggling with
aider's SEARCH/REPLACE edit-format protocol specifically on tasks that
require inventing new file paths inside a multi-file project (as opposed to
editing one clearly-identified existing file, where this driver+model combo
does fine - the `dotnet`/`rust` chunks' high pass rates are almost entirely
single- or few-file edits). Worth a closer look with a stronger model
(sections 15/16's other qwen3-coder:30b runs suggest this kind of
gap often narrows a lot with a more capable model) before drawing a firm
conclusion about aider itself.

**Clean cross-check:** excluding the 11 known-bug cases entirely,
25/41 = **61.0%** - strikingly close to gemma4:12b's own already-established
61% RAW pass rate from the original single-shot calibration (section 10).
Coincidence is plausible given the small sample, but it's at least
consistent with "a real file-tools driver mostly trades one kind of
difficulty (path/structure) for another (edit-format reliability on complex
scaffolding)," rather than being a strictly easier or harder measurement
overall.

#### The bug itself, for reference (unchanged from the 24/52 write-up)

**The bug:** `drivers/aider_cli.py`'s `run_case` builds aider's file-context
argument list from `case.get("setup_files")` only:
```
setup_names = list((case.get("setup_files") or {}).keys())
...
cmd = [..., "--message", prompt, *setup_names]
```
For a `setup_files`-based case this is correct (those keys ARE the files the
model should see). For a `setup_repo`-based case, the actual workspace
content comes from `cases.copy_setup_repo` - an entirely different mechanism
- and `setup_files` is empty. Aider is launched with **no files named in its
context at all**, despite a whole pre-existing multi-file repo sitting in
the workspace.

**Scope:** every one of the corpus's 14 `setup_repo` cases (both starter
repos), regardless of which model or `--model` value is used with the
`aider` driver - a code-level gap, not something gemma4:12b specifically
triggered. Not fixed at this point (staying inside the "check first, write it
down, fix it later" instruction for this session) - logged here
as a precise, reproducible finding: `aider_cli.py` needs to also pass
along the actual files `copy_setup_repo` populated (e.g. glob the
workspace, or read `repos/<name>/` directly) when `setup_repo` is set, not
only `setup_files`. Still open as of the last update to this document (see
"Current state" at the top of this file).

### 14. Driver sweep: 5 cases x 12 drivers - one real bug found and fixed

**Why:** the aider run (section 13) was paused mid-flight to do something
broader first - a shallow but WIDE smoke test across every testable driver,
looking specifically for CLI-level crashes/wiring bugs rather than accuracy.
`optarena doctor` only confirms a driver's package/binary is importable, never
that it actually completes a real call - exactly the gap this sweep targets.

**Setup:** 5 fixed cases (`add_csv_daily_totals_with_gap_fill` [python],
`add_eventbus_wildcard_subscription` [javascript], `add_gin_query_filter`
[go], `add_check_constraint_for_validation` [sql], `add_github_actions_ci_dotnet`
[csharp]) - all confirmed `baseline_incompatible() is None` (fair for every
driver kind, including flat-file baselines/SDK drivers), difficulty <=2,
single-prompt, no disruptions. Run through every `backend: scenario` driver
(12 of 14 registered) against `gemma4:12b`/local Ollama, one driver at a time,
serially (same GPU-contention reasoning as every other run in this session).

**Deliberately excluded: `claude-code` and `codex`** (`backend: fixed`) -
both authenticate against their own account/provider, not the scenario's
`--model`/`--base-url`. Pointing them at "gemma4:12b" is meaningless, and
running them risked silently hitting a real, possibly paid, account if one
happened to be configured on this machine. Not tested at this point; would need
separate, deliberate handling.

#### Results - 11 of 12 completely clean

| Driver | Result | Mean time | CLI-level issue? |
|---|---|---|---|
| openai-chat | 3/5 (60%) | 71.5s | none |
| ollama-chat | 3/5 (60%) | 70.9s | none |
| aider | 3/5 (60%) | 86.6s | none |
| opencode | 2/5 (40%) | 78.2s | none |
| goose | 3/5 (60%) | 53.6s | none |
| qwen-code | 3/5 (60%) | 112.7s | none |
| crewai | 2/5 (40%) | 64.8s | none |
| **openai-agents** | 3/5 (60%) | 70.4s | **found + fixed, see below** |
| smolagents | 2/5 (40%) | 151.2s | none |
| langgraph | 3/5 (60%) | 80.0s | none |
| autogen | 3/5 (60%) | 71.6s | none |
| semantic-kernel | 3/5 (60%) | 69.0s | none |

Pass-rate variance across drivers on the identical 5 cases (40-60%) is
expected and not itself a finding - different orchestration/prompting
stacks on top of the same model, same backend, same cases; that variance IS
what the whole project measures. Nothing here suggests a driver-level bug
beyond the one below - every other driver produced complete, sane results
with real token/timing telemetry and no CLI-level failure.

#### The one real bug: `openai-agents` never closed its async client

**Symptom:** `openai-agents` was the only driver whose run tripped the
sweep's crash detector - but the run itself completed correctly (3/5 passed,
real token telemetry: `4991 tokens/pass`). The "crash" was `stderr` containing
5 repeated tracebacks (one per case):
```
Exception ignored in: <function _ProactorBasePipeTransport.__del__ ...>
RuntimeError: Event loop is closed
```
`"Exception ignored in: ... __del__ ..."` is Python's own marker for a
non-fatal error during garbage collection - confirming the run itself never
actually failed, but something was leaking and printing scary-looking noise
to stderr every single case.

**Root cause:** `OpenAIAgentsDriver.open_session()` (`drivers/openai_agents_sdk.py`)
creates an `AsyncOpenAI` client and never closes it - no `close_session()`
override at all, just the base class's no-op default. By the time Python's
garbage collector destroys the client's leftover httpx transport objects
(at the next case, or at process exit), the event loop they were bound to
(created and torn down internally by `Runner.run_sync`, this driver's sync
wrapper over an async SDK) is long closed. This is the EXACT bug class
`autogen_sdk.py` and `semantic_kernel_sdk.py` were already hardened against
during the earlier SDK-driver refactor (section 12's context) - this
one driver was simply missed.

**The fix:** `open_session()` now returns the client alongside the agent/run
config (`(agent, run_config, client)`); a new `close_session()` closes it via
`asyncio.run(client.close())` (the client's `.close()` is itself a coroutine,
and this driver's `complete()` is sync, so it can't just be awaited directly
- same problem as the async drivers, solved the sync-driver way). Best-effort
- a close failure is swallowed, never crashes the run over cleanup.

**Verified, not assumed fixed:**
- Re-ran the identical driver against the identical 5 cases: `crashed: False`,
  `stderr_tail: ''` - zero tracebacks, same solid results (3/5, 61.1s mean,
  consistent with the pre-fix run).
- 2 new unit tests (`OpenAIAgentsSessionCleanupTests`) - `close_session`
  actually calls `client.close()`; a close failure is swallowed, never
  raises. Deliberately test `close_session` in isolation (it imports nothing
  from `agents`/`openai`) so they run regardless of whether the optional
  `openai-agents` extra is installed, matching this project's own
  `SDKDriverBaseTests` convention.
- Full suite: 357 tests (was 355), all passing; `ruff` clean.

**This is a real code fix to `optarena/`**, same category as section 12's
Fix A/B - not investigation or external tooling. Third genuine optarena bug
found and fixed in this session, and the first one caught by breadth (many drivers,
few cases) rather than depth (one driver, many cases) - a different kind of
testing surfacing a different kind of bug, which is the whole argument for
doing this sweep in the first place.

#### Resuming the paused aider run

Section 13's `img-php`-onward resume (28 cases remaining) was paused,
state intact, and unaffected by any of this - `calibrate_aider.py` already
carries this session's earlier fixes (poison-detector marker list,
no forced `--timeout` override).

### 15. Spot-check: qwen3-coder:30b + aider against gemma4:12b's genuine content failures

**Why:** gemma4:12b's full calibration (section 10) found 70 "genuine" content
failures - real oracle rejections (`oracle_fail:*`), excluding baseline-gap,
infra, and deadline-timeout kinds. Question: does a larger, coder-specialized
model with real file/test tool access (aider) actually fix these, or is the
gap elsewhere (prompting, oracle strictness, task difficulty)?

**Methodological caveat, stated up front:** this swaps BOTH the model
(gemma4:12b -> qwen3-coder:30b) AND the driver (ollama-chat, a flat single-shot
completion -> aider, a real edit/test/iterate loop) at once. A pass here
cannot be cleanly attributed to "smarter model" vs "aider's tool loop helped"
- both changed together. Treat this as "does the stronger stack fix it", not
"which factor mattered."

**Selection:** 6 cases hand-picked from the 70 for language/task-type spread,
deliberately EXCLUDING `rust` (confounded with the 22 pre-existing rust/cargo
oracle bugs from section 12 - a rust fail here could be oracle flakiness, not
model quality) and `shell` (already deeply analyzed separately, section 11).
Picked: `fix_iso_date_parsing` (python/bug_fix), `fix_express_command_injection`
(javascript/security), `create_go_generic_lru_cache` (go/feature),
`fix_csharp_sql_injection_interpolation` (csharp/security),
`fix_ts_csv_formula_injection` (typescript/security), `add_tests_java_word_wrap`
(java/testing).

**Preflight:** confirmed Ollama up and `qwen3-coder:30b` present via
`/api/tags`; ran one real generation to confirm it actually loads and responds
(28s total incl. one-time model load into memory, ~50 tok/s once warm - the
model is 18.6GB, exceeds this machine's 16GB VRAM, so partial CPU offload was
expected but not a practical bottleneck here). Piloted 1 case
(`fix_iso_date_parsing`) before committing to the batch: PASS in 16.8s
(model already warm).

#### Result: 5/6 (83%), vs 0/6 under gemma4:12b/ollama-chat

| Case | gemma4:12b/ollama-chat | qwen3-coder:30b/aider | Time |
|---|---|---|---|
| fix_iso_date_parsing | oracle_fail | **PASS** | 16.8s |
| fix_express_command_injection | oracle_fail:assertion_failure | **PASS** | 9.4s |
| create_go_generic_lru_cache | oracle_fail:runtime_error | **PASS** | 36.2s |
| fix_csharp_sql_injection_interpolation | oracle_fail:runtime_error | **PASS** | 9.5s |
| fix_ts_csv_formula_injection | oracle_fail:runtime_error | **PASS** | 11.8s |
| add_tests_java_word_wrap | oracle_fail:assertion_failure | FAIL (same kind) | 37.4s |

Efficiency: 1372-1815 tokens/pass, well within budget. No CLI-level issues,
no timeouts, no poisoned chunks (single serial batch, backend healthy
throughout).

**The one repeat failure is informative, not noise.** `add_tests_java_word_wrap`
asks the model to write a test; under BOTH stacks, the model's own test fails
against the correct reference implementation (`AssertionError: Test 3 failed:
size mismatch`), same failure class (`assertion_failure`) both times. Since
this survived a full swap of both model and driver, it looks less like
"gemma4:12b is too weak" and more like the task itself (a word-wrap edge case,
likely something like a boundary-length or unicode case) is a genuinely tricky
spot for a model to reason about test correctness on - worth a manual look at
the case's expected behavior before assuming it's fixable by "just use a
bigger model."

**Interpretation:** for 5 of 6 non-rust, non-shell content failures, a
stronger stack (bigger model + real tool access) fixed what gemma4:12b/
ollama-chat could not - consistent with section 10's framing of gemma4:12b's
raw-vs-fair gap as substantially a capability/driver-access story rather than
a broken-oracle story. Does not, by itself, distinguish how much of the fix
is the model vs the driver (see caveat above) - a clean answer would need a
2x2 (each model x each driver), not attempted here.

**Scope:** 6 of 70 genuine failures spot-checked, not a full re-run. No
optarena code changed by this experiment - purely a calibration data point.

### 16. Full 70-case qwen3-coder:30b+aider sweep, a real CRLF bug found and fixed, and the 23 genuinely tricky survivors

**Why:** follow-up to section 15's 6-case spot-check (5/6 passed). Ran ALL 70
of gemma4:12b's genuine content failures (`oracle_fail:*` kinds - excludes
baseline-gap/infra, per section 10) through the same qwen3-coder:30b+aider
stack, to separate "fixable by a stronger model+driver" from "genuinely
tricky" - the latter being real candidates for a corpus/oracle review, not
just model weakness. Same script design as section 13/15 (resumable,
poison-checked, single-instance-locked), 7 image-chunks, 70/70 completed
clean, no poisoning, no CLI-level failures.

#### First pass: 35/70 (50%) passed

Chunk detail: dotnet 6/8, go 3/5, jvm 3/4, node 7/13, python 7/9, rust
6/13, base-image 3/18.

#### A real bug found investigating the survivors: Windows CRLF corrupting the Linux sandbox

The 35 survivors skewed heavily toward `shell` (13 of 16 shell cases still
failing) - disproportionate even against shell's already-known weakness
(section 11). Pulled one survivor's exact failure text
(`create_backup_script`): `backup.sh: 2: \n: not found` /
`Syntax error: end of file unexpected (expecting "then")` - on a script
that is, by inspection, syntactically and semantically correct shell.

**Root-caused with `--keep-workspace` + a raw byte inspection, not
guessed:** the generated `backup.sh` was 100% CRLF-terminated (35 of 35
lines, confirmed byte-for-byte: CRLF count 35, lone CR 0, bare LF 0). A
stray CR glued to `then`/`fi` breaks dash/sh's parser - it never sees a
valid `then` token, so the whole script reads as one unterminated `if`
block until EOF.

**Two independent sources of the same corruption, both traced to code:**
1. `optarena/cases.py`'s `write_setup_files` ALREADY has the fix
   (`write_text(..., newline="")`) with a comment describing this exact
   failure mode - but `drivers/openai_chat.py:154` and
   `drivers/sdk_base.py:194` (the raw-model baseline and all 6 SDK drivers
   own single-file write, `concrete_target`'s write path) were missing it -
   the same bug, unfixed in two of three call sites.
2. The CLI-agent drivers (aider, opencode, goose, ...) do their OWN file
   I/O directly in the workspace - optarena has no `write_text` call to
   patch for those at all. This is what corrupted `create_backup_script`:
   aider itself, running on this Windows host, wrote the file CRLF-terminated.

**The fix - one universal normalization point instead of chasing every
writer:** new `cases.normalize_workspace_line_endings(root)`, called once
at the top of `run_check_command` (the single dispatcher every sandbox path
- shared-container, ephemeral-docker, and host-exec - already funnels
through), right before anything executes inside Linux. Walks every file in
the workspace, replaces CRLF/lone-CR with LF, skips anything binary-looking
(NUL byte in the first 8KB) and symlinks. Catches BOTH sources above in one
place, regardless of which driver or tool wrote the file. Also added
`newline=""` to the two missed `write_text` calls (`openai_chat.py`,
`sdk_base.py`) as defense in depth, matching the already-established
pattern.

**Verified, not assumed:**
- Direct byte-level reproduction: re-ran `create_backup_script` with
  `--keep-workspace` before the fix (confirmed 35/35 CRLF lines, FAIL) and
  after (confirmed zero CR bytes, PASS) - same model, same prompt, same
  driver, isolated to the fix.
- 12 new unit tests (`NormalizeWorkspaceLineEndingsTests`): CRLF -> LF,
  lone-CR -> LF, an already-LF file is left byte-for-byte unchanged (mtime
  unchanged - proving no needless rewrite), a NUL-containing "binary" file
  is left alone, recursion into subdirectories, symlinks skipped. Plus one
  `DockerCheckCommandTests` integration test proving `run_check_command`
  itself normalizes before dispatch, and one test each on
  `BaselineDriverEndToEndTests`/`SDKDriverBaseTests` asserting the written
  file's raw bytes never contain CRLF.
- Full suite: 366 tests (was 357), 365 passing. The 1 failure
  (`test_check_command_pass_and_fail`) is a **pre-existing, unrelated**
  flake - confirmed via `git stash` that it fails identically with NONE of
  this session's changes applied (it hardcodes a Windows venv `sys.executable`
  path as a docker check_command, which does not exist inside the Linux
  image; whether it is hit depends on whether Docker happens to be live in
  the test environment, not on anything this session touched).

#### Second pass: re-ran the original 35 survivors after the fix - 12/35 (34%) now pass

| Chunk | Before fix | After fix |
|---|---|---|
| dotnet (2) | 0/2 | 1/2 |
| go (2) | 0/2 | 0/2 |
| jvm (1) | 0/1 | 0/1 |
| node (6) | 0/6 | 0/6 |
| python (2) | 0/2 | 0/2 |
| rust (7) | 0/7 | 1/7 |
| base-image (15) | 0/15 | **10/15 (67%)** |

The base-image chunk (shell/sql/hcl/etc.) jumping from 0% to 67% confirms
the theory directly: 10 of 12 newly-passing cases are `shell` -
`add_shell_deploy_script_rollback`, `add_tests_for_utility_script`,
`add_tests_shell_ipv4_validator`, `create_backup_script`,
`create_sh_env_check`, `fix_sh_set_e_pipeline`, `fix_sh_word_splitting_find`,
`fix_shell_report_path_traversal`, `refactor_duplicate_validation_functions`,
`refactor_sh_dedupe_functions`. Plus `fix_tokio_blocking_sleep` (rust) and
`add_tests_aspnet_service` (csharp).

**Honest caveat on attribution:** unlike `create_backup_script` (directly,
byte-level reproduced), the other 11 are inferred from a fresh model
generation before vs. after the fix, not a byte-identical retry - qwen3-coder
sampling is not perfectly deterministic, so a small share of this 12 could
be ordinary run-to-run variance rather than the CRLF fix specifically. The
shell-heavy concentration (10/12) lines up too well with a CRLF-sensitive
language to be coincidence, but this is not claimed as 12/12 ironclad the
way the single reproduced case is.

#### Combined picture: 47/70 (67%) of gemma4:12b's genuine failures now pass under qwen3-coder:30b+aider

35 (first pass) + 12 (after the CRLF fix) = 47. Zero of these 70 passed
under gemma4:12b/ollama-chat by construction (that is how the set was
selected) - so this is a real, substantial recovery, not a wash.

#### The 23 genuinely tricky survivors (real candidates for a future corpus/oracle pass, not touched at this point)

| Language | Case | Failure shape |
|---|---|---|
| rust | `adapt_compounding_config_rust_dynamic` | cargo test failure (runtime) |
| rust | `create_actix_items_endpoint` | runtime, python-side test harness |
| rust | `create_rust_parallel_map` | **compile error - Send trait bound on a generic worker closure, a genuinely hard Rust concurrency task** |
| rust | `add_tests_rust_shipping_tiers` | model's own test fails mutation_check |
| rust | `fix_axum_path_traversal` | runtime, python-side test harness |
| rust | `fix_rust_arc_missing_clone` | content pattern: missing literal "arc::clone" (may be an over-strict oracle - functionally equivalent code can phrase this differently) |
| go | `add_tests_gin_handler_logic` | content regex: fewer than 2 "func Test" matches |
| go | `add_tests_go_palindrome_alphanumeric` | content regex: fewer than 5 "func Test" matches (an unusually strict count) |
| javascript | `add_template_conditional_blocks` | node --test runtime failure |
| javascript | `add_tests_node_semver_compare` | content pattern: missing literal "node:test" |
| javascript | `refactor_express_duplicate_routes` | content regex: no top-level named function found |
| typescript | `add_tests_ts_utility` | model's own test fails mutation_check |
| typescript | `create_ts_discriminated_union` | content pattern: missing literal "export" |
| typescript | `create_ts_typed_event_bus` | tsc strict-mode generic constraint error |
| python | `add_tests_flask_multi` | model's own test fails mutation_check (test file not found by pytest) |
| csharp | `add_tests_aspnet_webapplicationfactory` | model's own test fails mutation_check |
| kotlin | `add_tests_kotlin_roman_numeral` | Maven build failure inside mutation_check |
| sql | `add_tests_schema_constraints` | content pattern: missing literal "assert" |
| sql | `add_tests_sql_top_spender_view` | model's own test fails mutation_check |
| hcl | `refactor_terraform_per_env_variables_to_map` | terraform validation check failure |
| shell | `create_sh_rotate_logs` | model's own test fails (assertion) |
| shell | `fix_script_command_injection` | expected file never created |
| shell | `fix_unquoted_variable_bug` | model's own test fails (assertion) |

**A pattern worth flagging for that future pass, not acted on now:** roughly
a third of these (6 of 23) are strict literal-substring content-pattern
checks (missing expected content "assert"/"export"/"node:test"/"arc::clone")
rather than a real behavioral check_command failure - the kind of oracle
that can reject functionally-correct code that just phrases something
differently (e.g. `Arc::clone(&x)` vs `x.clone()`, or a semantically
equivalent test assertion that is not the literal word "assert"). Separately,
6 more are `mutation_check.py`-style "the model's own test failed against
the CORRECT reference implementation" - a test-writing quality signal, not
a content generation one. Neither pattern is a code bug in `optarena/`
(the oracle is doing exactly what its case JSON specifies) - both are
**corpus-content questions**, explicitly out of scope for this section per this
session's standing instruction, but this list is exactly the input a future
corpus-content pass would want.

**Scope, as always:** this section is a code fix (CRLF) plus calibration
data (the 70-case sweep). No corpus JSON was touched.

### 17. Corpus integrity: all 44 "no failing variant" cases fixed (Round 4's C-3), plus a real Rust sandbox bug found along the way

**Why:** user asked to act on the highest-priority corpus recommendation from
the earlier "how should we expand the corpus" discussion - fix oracle
integrity before adding volume. Round 4's C-3 finding: 44 of 510 cases
had a `reference_solution` but no `broken_solutions` and no implicit
`unmodified` check (their task_type isn't in `bug_fix`/`refactoring`/
`performance`/`security`) - meaning `cases verify` could only ever prove
those oracles CAN pass, never that they CAN fail. An oracle that silently
accepts anything is exactly the failure class `verify.py` exists to catch,
and it was untested for 44 cases.

**Scope:** 8 `devops` cases (6 `add_github_actions_ci_*`, plus
`add_healthcheck_to_compose`, `add_multiple_environment_variables`) and 36
`feature` cases (mostly `create_*` - health endpoints, CRUD APIs, CLI tools,
SQL schemas, Terraform files, data structures).

#### The work: one hand-crafted, oracle-specific broken variant per case

Not a generic "write garbage" broken variant - for each case, read its real
`expected_files` (content/regex patterns) and `check_command`/
`test_setup_files`, then designed ONE minimal, plausible bug that a real
model could actually make, verified to fail that case's specific oracle.
Examples of the range: an off-by-one loop bound (`create_factorial`), a
CI workflow missing one step while still mentioning it in a comment
(`add_github_actions_ci_rust` - a stricter test of the oracle's "must
actually run, not just be mentioned" check), a DELETE endpoint that reports
204 without removing anything (`create_gin_crud_todos`,
`create_todo_minimal_api`, `create_axum_crud_todos`), a validation check
that misses whitespace-only input (`create_items_validation_endpoint`), a
SQL view using INNER JOIN where LEFT JOIN was required so zero-order
customers vanish (`create_view_for_reporting`), and - fitting for the one
genuinely multi-turn case in the whole corpus - `multi_prompt_session`'s
broken variant simply ignores the SECOND prompt entirely, simulating the
exact failure mode multi-turn evaluation exists to catch.

#### A real infrastructure bug found investigating 2 unexpected violations

First full pass: 42/44 clean, 2 violations - both Rust, both saying the
broken variant "PASSED the oracle" when it should have failed
(`create_actix_items_endpoint`, `create_axum_crud_todos`).

**Root-caused, not patched around blindly.** Isolated it with an
increasingly extreme test: replaced one broken variant's `src/main.rs` with
text that is not valid Rust at all (`this_symbol_does_not_exist(!!!...)`,
guaranteed to fail `cargo build`). It STILL "passed the oracle" - the
launched server responded correctly to real HTTP requests despite the
source being uncompilable. This proves the running binary wasn't built from
the new source at all.

**Cause:** `docker/rust/Dockerfile` sets `ENV CARGO_TARGET_DIR=/opt/cargo-target`
- one shared build-artifact directory across every case run against that
image, deliberately, to avoid recompiling the whole dependency tree per
case. But `verify_cases` runs each variant (reference, then each broken
solution) from a genuinely different temp workspace directory, all sharing
identical `Cargo.toml` (same package `name`/`version`, since only
`src/main.rs` differs between reference and broken). Cargo's build-cache
fingerprinting apparently doesn't reliably distinguish "same package
identity, different absolute source directory" under `--offline` in this
configuration - it treated the broken variant as already up to date and
reused the reference variant's freshly-built binary from the shared target
dir instead of recompiling.

**Confirmed by a second experiment, not just theory:** bumping only the
`version` field in the broken variant's Cargo.toml (`0.1.0` -> `0.1.1`, same
crate name, same source bug) made cargo correctly recompile and correctly
fail. Package version is part of cargo's fingerprint identity; the absolute
source path apparently is not, at least not reliably in this shared-target
offline configuration.

**The fix applied here:** both affected `broken_solutions` entries
(`create_actix_items_endpoint`, `create_axum_crud_todos`) now also override
`Cargo.toml` with a bumped patch version, forcing a genuine rebuild. This is
a workaround at the case-content level, not a fix to the underlying cargo/
Dockerfile caching behavior - flagged explicitly as a real, separate,
unresolved infrastructure finding.

**Why this matters beyond these 2 cases:** this is a highly plausible
explanation for some or all of section 12's 22 pre-existing Rust `verify
cases` violations ("19 broken variant PASSED the oracle, 3 reference
solution FAILED the oracle") - those were logged as corpus-content bugs at
the time, but at least the "broken variant PASSED" ones are the EXACT
symptom this bug produces. Not re-investigated case-by-case at this point
(would require checking whether each of those 22 cases' reference and broken
variants share a Cargo.toml package identity, then re-testing with a
version bump) - logged here as the most likely next step for that
deferred corpus-content work, not corpus content itself. Still an open
item as of the last update to this document.

#### Verification

- `optarena cases verify --strict` over all 44 cases: 88/88 variants
  correct (44 references pass, 44 broken variants fail), 0 violations.
- Corpus-wide: `cases_without_failing_variant()` dropped from 44 to 0.
- Confirmed exactly 44 case JSON files touched (`git diff --stat`), nothing
  else.
- Full suite: 366 tests, 365 passing - the 1 failure
  (`test_check_command_pass_and_fail`) is the same pre-existing, unrelated
  Docker-availability flake from section 16, reconfirmed via `git stash`
  to fail identically with none of this session's changes applied. `ruff` clean.
- A full 510-case `cases verify --strict` re-run was started as a final
  end-to-end check but deliberately stopped partway through and NOT
  redone - a full corpus re-verify (~1300+ variants, tens of minutes) adds
  nothing the scoped 44-case run above doesn't already establish, since
  `git diff --stat` already confirms only those 44 files changed and
  every other case's own reference/broken variants are untouched. Scoping
  verification to exactly what changed is the right level of proof here.
  One orphaned sandbox container left by the interrupted run
  (`optarena-tester-dotnet`) was found and removed.

### 18. Corpus expansion: disruption/multi-turn coverage for every language (Round 4's C-4, disruption half)

**Why:** follow-up to the "how should we expand the corpus" discussion -
priority 3 (expand disruptions/multi-turn) over priority 2 (difficulty 4-5),
picked because it reuses an already-working mechanism
(`cases.apply_all_disruptions`/`apply_disruptions`, the `adapt_*_dynamic`
pattern) rather than needing new infrastructure, and directly targets the
sharpest gap C-4 identified: the product's own stated differentiator
(dynamic, multi-turn evaluation - "agents lose ~40% when the environment
shifts mid-task") was exercised by only 1.2% of the corpus, and 12 of the
corpus's 18 languages had ZERO disruption coverage at all.

**Scope: 12 new cases, one per language that previously had none** -
`csharp`, `typescript`, `php`, `kotlin`, `c`, `cpp`, `sql`, `hcl`, `shell`,
`dockerfile`, `yaml`, `makefile`. (`go`, `java`, `javascript`, `python`,
`ruby`, `rust` already had one each from earlier session work.)

#### Two distinct disruption designs, matched to what each language actually is

**Compiled/interpreted languages with a real runtime (csharp, typescript,
php, kotlin, c, cpp) - REACTIVE trigger, mirroring the existing
java/go/ruby pattern exactly:** a `Config`-style file declares a named
constant; the agent's first prompt asks it to write a `Greeter`-style
function/class that reads that constant LIVE (not copies it); a
`when: {file_contains: {path: "<the file the agent just wrote>", pattern:
"Hello"}}` trigger fires the instant the agent's own first draft appears
(not a fixed step count - REALM-Bench-style, keyed off observable agent
state) and swaps the constant's value; the second prompt asks the agent to
confirm it still reflects the CURRENT value. Two broken variants per case:
a literal hardcoded old value, and a value "cached" into a differently-named
local constant (same bug, different disguise) - both must fail once the
disruption fires.

**Declarative/build languages with no meaningful "runtime" to read a value
at (sql, hcl, shell, dockerfile, yaml, makefile) - fixed `after_prompt: 1`
trigger, a spec-file-changed-under-you pattern instead:** a plain-text
spec file (`allowed_statuses.txt`, `policy.txt`, `config.conf`,
`python_version.txt`, `app_version.txt`, `version.txt`) states a
requirement the artifact must match exactly; the disruption rewrites that
file after the first prompt; the second prompt tells the agent to re-sync.
`shell` is the one case in this group that still gets genuine runtime
behavior (the script sources the config file each execution, so the
generated script MUST read it live, not copy it) - closer in spirit to the
reactive group despite using a fixed trigger for design consistency with
its declarative neighbors.

Concrete per-language design notes worth recording:
- **shell**: closest to a "real" runtime disruption of any in this batch -
  `greet.sh` sources `config.conf` and must print the live value; a broken
  variant with a case-typo'd variable name (`$Name` vs `$NAME`) is a second,
  realistic discriminator beyond plain hardcoding.
- **dockerfile/yaml/makefile**: none of these get an actual `docker build`/
  `make` execution against untrusted network access in the sandbox
  (`--network none`), so - matching the existing convention for every other
  Dockerfile/compose case in the corpus - verification is a static
  regex/YAML-parse check against the CURRENT spec-file value, not a real
  build. `makefile` is the one exception that DOES actually execute
  (`make build` really runs, `$(shell cat version.txt)` really reads the
  file at build time) since Make has no sandboxing concern `docker build`/
  network access would raise.
- **hcl**: two broken variants, not one - besides a stale hardcoded
  default, a second variant hardcodes the OUTPUT value directly instead of
  referencing `var.instance_type`, which the case's own oracle correctly
  rejects even though the variable's default is right. Mirrors the
  "copies-value-instead-of-reading-live" pattern from the reactive group,
  adapted to Terraform's variable/output distinction.
- **sql**: uses SQLite's own `CHECK` constraint enforcement as the runtime
  proof - the test script inserts every currently-allowed status (must
  succeed) and one clearly-invalid one (must be rejected), rather than
  parsing the DDL text.

#### Verification, one case at a time before batching (same discipline as the 44-case pass)

Each of the 12 was verified individually with the real sandboxed oracle
immediately after being written - not batched until confirmed - catching
issues early:
- **TypeScript**: first attempt used `process.exit(1)` in the test file,
  which fails under `--strict` without `@types/node` (`TS2580: Cannot find
  name 'process'`) - caught immediately by the reference solution itself
  failing to verify. Fixed by switching to `throw new Error(...)`, which
  produces a nonzero exit code without needing the Node type declarations.
- **PHP**: needed the corpus's actual `image: optarena-tester-php:latest`
  field (initially omitted) - caught the same way, by the reference failing
  first-verify rather than assuming the default image would be right.

Final combined verification, all 12 together: **32/32 variants correct
(12 references pass, 20 broken variants fail), 0 violations.**

#### Corpus impact

| Metric | Before | After |
|---|---|---|
| Total cases | 510 | 522 |
| Cases with `disruptions` | 6 | 18 |
| Languages with >=1 disruption case | 6 of 18 | **18 of 18 (100%)** |
| Multi-prompt cases | 9 | 21 |
| Disruption coverage | 1.2% | 3.4% |

Not claimed as "solved" - 3.4% is still a small share of the corpus, and
C-4's other sub-findings (nothing at difficulty 4/5, `test_kind` unset on
98% of cases) are untouched at this point. What changed is qualitative, not just
quantitative: every language the corpus covers can now exercise the
dynamic/multi-turn dimension at all, closing the "12 of 18 languages have
literally zero data on this" gap rather than just adding more volume to
the 6 that already had some.

#### Verification

- `optarena cases verify --strict` over all 12 new cases: 32/32 variants
  correct, 0 violations.
- `git status` confirms exactly 12 new untracked case files, no existing
  case content touched by this pass (separate from the 44 files touched by
  section 17's earlier fix, still showing as modified from that work).
- Full suite: 366 tests, 365 passing - same pre-existing, unrelated
  Docker-availability flake as sections 16/17 (`test_check_command_pass_and_fail`).
  `ruff` clean.
- No `optarena/` code was changed in this pass - purely corpus content
  (12 new case JSON files), unlike sections 12/16/17's real code fixes.

### 19. Corpus expansion, phase 2: disruption coverage 18 -> 100 cases (18% -> ~17% of a larger corpus)

**Why:** direct follow-up to section 18 - user asked "should we increase it
further to maybe 10-20%?", the recommendation given was to target existing corpus weight
rather than flat volume (python/javascript/csharp/go/java/rust get the most,
since they already have 25+ single-turn cases and can support several
genuinely different disruption scenarios each, not five variations on one
pattern); user said "get to 100 cases." This section covers that full build.

**Scope: 82 new disruption cases** (18 before this pass -> 100 after),
allocated by existing corpus weight: python +9, javascript +8, csharp/go/
java/rust/typescript +7 each, kotlin/php/ruby/sql/yaml +5 each, c +4,
cpp/hcl/shell +3 each, dockerfile/makefile +5 each (deliberately brought up
from 1 each, since they were the thinnest tracks in section 18 and a
single-case track can't discriminate anything statistically).

#### Six reusable scenario archetypes, not "config value swap" repeated 82 times

Each language's new cases mix several of these, chosen to fit that
language's actual toolchain rather than forcing every language into an
identical shape:

- **dependency-removed** - a file/header/module the first solution imports
  is deleted; the second draft must stop depending on it (already section
  18's pattern for go/ruby, extended to every language with a real module
  system).
- **reverted-fix** - a `bug_fix`-type case where the disruption REWRITES
  the file back to its ORIGINAL buggy state after the agent's fix lands;
  the agent must notice the regression and reapply, not assume the fix is
  still there. New this pass, and arguably the most direct test of the
  "environment shifts mid-task" claim: it doesn't just change a value, it
  actively undoes the agent's own prior work.
- **renamed-symbol** (reactive trigger) - a function/method the first
  draft calls gets renamed under it; the second draft must follow the
  rename, not keep calling a symbol that no longer exists (compile/import
  error if it doesn't).
- **moved-module/namespace/package** - the file or namespace a dependency
  lives in changes (Python package moved, Go/Java/C# namespace changed,
  Ruby file renamed); tests whether the agent updates the *reference*, not
  just the value.
- **requirements-drift** (fixed trigger, spec file) - a plain-text spec
  file (`allowed_roles.txt`, `max_items.txt`, `policy.txt`, ...) states a
  requirement the artifact must match exactly; it's rewritten after the
  first prompt, and the second prompt tells the agent to re-sync. This is
  the only archetype usable for declarative languages with no real runtime
  to read a value at (SQL, HCL, YAML, Dockerfile, Makefile) - each of
  those five "requirements-drift" cases has its own realistic drift target
  (a CHECK constraint's allowed values, a Terraform variable's default, a
  Deployment's replica count, a Dockerfile's pinned base-image tag, a
  Makefile's `$(shell ...)`-read build parameter).
- **compounding** (rust only, matching section 10's existing rust case) -
  two disruptions fire at the identical prompt boundary; the LAST one's
  effect is what should stick, testing whether the agent tracks the
  CURRENT state through repeated changes, not just survives one.

#### Two real design bugs found and fixed while building this, both caught by verification before being trusted

1. **TypeScript `--strict` + `@types/node`.** `import * as fs from 'fs'`
   fails to compile (`TS2307: Cannot find module 'fs'`) in this sandbox -
   same underlying gap as the earlier `process.exit` lesson from section
   18 (no `@types/node` installed). Two cases (`adapt_role_list_typescript_dynamic`,
   `adapt_cart_limit_typescript_dynamic`) hit this on first verify. Fixed
   by declaring an ambient `require` shim (`declare function require(id:
   string): any;`) instead of a typed `import`, sidestepping the missing
   type declarations entirely rather than trying to install them.
2. **A genuinely wrong broken variant, caught by its own PASS.** The first
   draft of the TypeScript `renamed-symbol` and `moved-module` broken
   variants tried to dodge the renamed/deleted symbol by reimplementing
   the same formula inline (e.g. `r * r` instead of calling the renamed
   `square`/`sq`) - which produces the IDENTICAL numeric result to the
   reference, since only the symbol's NAME changed, not its behavior. This
   would have made the broken variant silently pass the oracle - exactly
   the class of bug `verify.py` exists to catch, caught here before it
   ever reached a real run. Fixed by reverting the broken variant to
   actually reference the old (now-missing) symbol, which is the real
   failure mode being tested (a compile/import error), not a coincidental
   numeric mismatch.

#### Rust: proactively applying section 17's CARGO_TARGET_DIR fix from the start

Section 17 found that Rust's shared `CARGO_TARGET_DIR` can silently reuse a
stale binary when a reference and broken variant share identical `Cargo.toml`
package name+version across different workspace paths. All 6 new Rust cases'
`broken_solutions` entries proactively override `Cargo.toml` with a bumped
patch version (`0.1.1`, `0.1.2`, ...) from the start, rather than discovering
the issue reactively per case. All 6 verified clean on the first attempt as
a result - no repeat of section 17's investigation needed.

#### Verification

- Every batch (grouped by language, 2-9 cases at a time) verified
  immediately after being written, before moving to the next language -
  same discipline as section 17's 44-case pass.
- Final combined verification, all 94 newly-added case files together
  (82 from this pass + the 12 from section 18, re-confirmed): **213
  variants checked across 94 cases, 0 violations.**
- Full suite: 366 tests, 365 passing - same pre-existing, unrelated
  Docker-availability flake as every prior section
  (`test_check_command_pass_and_fail`). `ruff` clean.
- No `optarena/` code was touched in this pass - purely corpus content
  (82 new case JSON files).
- One arithmetic slip worth recording plainly: the running total was
  miscounted mid-build (announced as "93," "96," "98" at various points),
  landing at 90 actual cases instead of the intended 100 once real numbers
  were checked. Caught by actually querying the corpus rather than trusting
  the running tally, and closed with one final batch (dockerfile +4,
  makefile +3, plus one each to makefile/hcl/sql to correct a second
  undercount in that batch) to reach exactly 100.

#### Final corpus state

| Metric | Section 18 (before this pass) | Now |
|---|---|---|
| Total cases | 522 | 604 |
| Disruption cases | 18 | 100 |
| Disruption coverage | 3.4% | 16.6% |
| Cases per language (disruption) | 1 everywhere | python 9, javascript 8, csharp/go/java/rust/typescript 7, kotlin/php/ruby/sql/yaml 5, c 4, cpp/hcl/makefile/dockerfile 3-5, shell 2 |

Every language now has enough disruption cases to compute a real (if still
modest) per-language pass rate rather than a single anecdotal data point -
the original C-4 gap ("12 of 18 languages have literally zero data on this
dimension") is now "every language has a small but real sample," which is
the qualitative shift that mattered most. 16.6% is still a minority of the
corpus and not claimed as final - the difficulty-4/5 ceiling and `test_kind`
gaps from C-4 remain untouched at this point, same as noted in section 18.

### 20. Corpus expansion: genuine difficulty 4/5 cases (a new repo-scale fixture, springboot-tasktracker)

**Why:** direct follow-up to sections 18/19 - user asked whether to fill the
difficulty-4/5 ceiling by relabeling existing difficulty-3 cases or by
authoring genuinely harder ones. Relabeling was rejected: bumping an
existing case's `difficulty` field without changing its actual complexity
would reintroduce the same "claiming something the data doesn't back up"
problem the disruption-coverage work was fixing - the honest fix is cases
that are qualitatively harder than anything difficulty-3 currently covers,
not re-tagged copies of them.

**What actually distinguishes difficulty 4/5 here:** before this pass, the
corpus's only "repo-scale" cases (`setup_repo`: the agent edits a real
20-30 file starter app instead of writing from scratch) were
`fastapi-tasktracker` (python, 9 cases) and `express-ts-shortlink`
(typescript, 5 cases) - and every one of those was single-prompt with zero
disruptions, capped at difficulty 3. Nothing in the whole corpus combined
repo-scale multi-file scope *with* a multi-turn disruption. That
combination is the definition used here:

- **difficulty 4** - repo-scale, single prompt, but with a real
  cross-cutting constraint spanning several files at once (a filter that
  must compose with existing filters, not just bolt on independently).
- **difficulty 5** - the same repo-scale depth, plus a multi-turn
  disruption stacked on top (a real regression fix that gets reverted mid-task
  and must be reapplied, this time covering a case the original fix didn't
  need to).

#### A third repo-scale fixture: `repos/springboot-tasktracker/`

User chose to also add a third language's fixture rather than stay within
the two existing ones (python, typescript) - picked Java + Spring Boot
(17 existing spring-boot cases already in the corpus, the strongest
existing ecosystem of the candidates considered: java/spring-boot vs.
go/gin vs. csharp/aspnet-core).

**A real constraint discovered while designing it, not assumed:**
`optarena-tester-jvm`'s offline Maven cache (`docker/jvm/scratch-pom.xml`)
is only warmed for `spring-boot-starter-web` and `spring-boot-starter-test`
- not `spring-boot-starter-data-jpa` or `com.h2database`. Since
`check_command` runs with `--network none`, a JPA+H2-backed fixture
(mirroring `fastapi-tasktracker`'s SQLAlchemy persistence) would fail to
resolve dependencies at build time. Rather than rebuild the shared Docker
image (a real infra change affecting all 41 existing java cases, for a
single new fixture), the fixture uses plain in-memory
`ConcurrentHashMap`-backed repositories (`UserRepository`,
`ProjectRepository`, `TaskRepository`, each with a `clear()` hook tests use
via `@BeforeEach` instead of `@DirtiesContext`, keeping the shared
`@SpringBootTest` context fast across test classes). Same domain as
`fastapi-tasktracker` deliberately (users own projects, projects contain
tasks, task status/priority/assignee) - not for its own sake, but so the
difficulty-4/5 "labels" and "filter fix" cases below are the *same task*
implemented against three different repo-scale fixtures, which is a
genuinely useful property for any future cross-language comparison.

Verified standalone before any case was written against it: a throwaway
smoke-test case (`reference_solution` = the fixture's own unmodified
`pom.xml`, one deliberately-broken `broken_solutions` variant) confirmed
the fixture builds and its own MockMvc suite (`UserControllerTest`,
`ProjectControllerTest`, `TaskControllerTest`) passes fully offline in the
real sandbox - 2/2 variants correct - before it was trusted as a
foundation for further cases, same discipline as every prior section.

#### Six new cases: one D4 + one D5 per fixture, same two archetypes each time

- **D4 - "labels"** (feature): tasks/links gain a validated `labels` list
  and a `?label=` filter that must compose with the fixture's existing
  filters (project_id/status/assignee_id for the tasktrackers, nothing else
  for shortlink's simpler model). The deliberate trap in each case's
  `broken_solutions`: a label filter that works in isolation but silently
  ignores every other filter when combined with one - exactly the
  cross-cutting-constraint failure mode difficulty 4 exists to catch.
- **D5 - "filter fix, reverted"** (bug_fix): a real, plausible regression -
  independent `if`/`continue` filter checks quietly refactored into an
  `if`/`elif` chain (python), a broken `if`/`else if` chain (java), or (for
  shortlink, no multi-filter query to break) `isValidSlug`'s length-and-
  pattern check refactored into an early-return that checks one or the
  other depending on slug length, never both. Prompt 1 asks for the fix;
  a fixed `after_prompt: 1` disruption reverts the file back to the buggy
  version (the "reverted-fix" archetype from section 19, now at repo
  scale); prompt 2 reports the regression and asks for the fix again, this
  time also covering a case the first fix didn't need to (all three
  filters combined at once, not just pairs). `broken_solutions` for each
  is a second, structurally different wrong fix (OR-combined filters
  instead of AND; bounds-only validation with the pattern check dropped
  entirely) - not just "did nothing," which the task_type's automatic
  `unmodified` check already covers.

Case names: `add_tasktracker_task_labels` / `add_tasktracker_task_filter_fix_dynamic`
(python), `add_shortlink_link_labels` / `add_shortlink_slug_validation_fix_dynamic`
(typescript), `add_springboot_tasktracker_task_labels` /
`add_springboot_tasktracker_filter_fix_dynamic` (java).

#### Verification

- Each pair verified immediately after being written, then all six
  together in one combined pass: **15 variants checked across 6 cases, 0
  violations.**
- Full suite: 375 tests (1 skipped) - the same pre-existing, unrelated
  Docker-availability flake as every prior section
  (`test_check_command_pass_and_fail`, host Windows python path invalid
  inside the Linux sandbox). `ruff check optarena/ repos/springboot-tasktracker/`
  clean; the only ruff findings in a full repo-wide check are 7
  pre-existing ones inside `repos/fastapi-tasktracker/` (SQLAlchemy string
  forward-refs ruff's F821 doesn't resolve, plus one unused test variable),
  confirmed untouched by this pass (`git status` shows no local
  modification to that fixture) and unrelated to it.
- `optarena cases validate`: 610/610 cases structurally valid.

#### Final corpus state

| Metric | Section 19 (before this pass) | Now |
|---|---|---|
| Total cases | 604 | 610 |
| Difficulty 1 / 2 / 3 / 4 / 5 | 159 / 322 / 41+ / 0 / 0 | 168 / 387 / 49 / **3** / **3** |
| Repo-scale (`setup_repo`) fixtures | 2 (python, typescript) | **3** (+ java) |
| Repo-scale cases | 14 | **20** |

Six cases is a deliberately small, expensive-per-case start (each one is a
real multi-file addition or fix against a 20-30 file codebase, verified
against the live Docker oracle, not a JSON template fill) - the point of
this pass was proving the difficulty-4/5 tier can be honest and
non-trivial to build, and standing up a third language's repo-scale
fixture for future difficulty-4/5 work to build on, not exhausting the
tier in one push. The difficulty-1/2/3 volume imbalance (168/387/49 vs. 3/3)
remains open, same as flagged in section 19 - not addressed in this pass.

### 21. Corpus expansion: difficulty-3 depth across all 18 languages (Round 4's C-4, difficulty-3 half)

**Why:** direct follow-up to section 20 - asked "how much should we build
to have a reasonable corpus?" Difficulty-4/5 further expansion was
explicitly *not* the next move: it's expensive per case (repo-scale
fixtures) and the more pressing gap was difficulty-3, which sat at only 49
cases spread across 18 languages - roughly 2-3 per language, too thin to
say anything about a language's difficulty-3 signal specifically. User
confirmed ("lets go with 3 first") then chose the full ~121-case push in
one session over a paced rollout.

**Allocation:** weighted by each language's existing total corpus size
(same methodology as the disruption-coverage push in section 19), landing
on 121 new cases: python +15, javascript +10, go +9, java +8, rust +8,
ruby +8, typescript +6, yaml +7, csharp +7, sql +6, php +7, kotlin +6,
shell +5, dockerfile +4, c +4, hcl +4, makefile +4, cpp +3.

**Reusable archetype pool** (adapted idiomatically per language, not
templated copy-paste): an 8-archetype "hard correctness" core used across
the general-purpose languages - LRU cache + per-entry TTL, token-bucket
rate limiter (fractional refill capped at capacity), idempotency-key
dedup with a TTL window, consistent-hash ring with virtual nodes (a real
hash function, not `hashCode()` mod), a FIFO-tiebreak-stable priority
queue, RFC4180 CSV field escaping, business-day date arithmetic (skipping
weekends, rejecting the classic `n + 2*(n/5)` approximation-formula bug),
and a 3-state circuit breaker with reset-timeout semantics - plus
language-specific archetypes for the declarative/infra languages:
Kubernetes YAML hardening (PDB selector-exactness, RollingUpdate strategy,
probe timing, PriorityClass, topologySpreadConstraints, ConfigMap
checksum-rollout, graceful shutdown), Terraform/HCL hardening (RDS
deletion_protection+encryption, `lifecycle.prevent_destroy`, ASG
ELB-health-check grace period, CloudWatch alarm evaluation_periods),
Dockerfile best practices (non-root USER, layer-cache-friendly COPY
ordering, apt cache cleanup in-layer, multi-stage test stage), Makefile
correctness (header-dependency tracking, `.PHONY`, immediate `:=` vs.
deferred `=` expansion, order-only prerequisites under `-j`), and SQL
window-function/CTE patterns (dedup-keep-latest, gaps-and-islands,
recursive management-chain CTE, self-join duplicate pairs, pivot,
bounded moving average).

**Real bugs found and fixed during the build** (11, all caught by the live
Docker oracle before being trusted, none assumed correct by inspection):

- JS/Ruby stable-priority-queue: a naive "no explicit tiebreak" broken
  variant didn't actually break FIFO ordering, because V8 and Ruby's
  sorts are both guaranteed-stable - switched to an explicit
  counter-decrements-each-push broken variant (the pattern already used
  successfully for Python/Go/Java/C#/Kotlin/PHP).
- Python ReDoS case: reference solution's email regex rejected a valid
  dotted local-part address - regex widened.
- PHP circuit breaker: a `private function boom()` test helper threw
  `TypeError` when invoked as a callable array from outside the class -
  made `public`.
- Rust business-day math: an off-by-one in the epoch-day-to-weekday
  formula (`+4` instead of `+3`) - fixed and cross-checked against
  Python `datetime` epoch-day values for every test date.
- Shell retry-with-backoff: designed around `bc`, which isn't installed
  in the sandbox - rewritten with pure bash integer arithmetic; a
  companion broken variant was also failing via the wrong mechanism
  (`set -uo pipefail` missing `-e`) rather than the intended over-retry
  bug - fixed to `-euo pipefail`.
- Shell atomic-file-write: a `cp`-based broken variant never exercised
  the real leaked-temp-file bug because it didn't match the `mv`
  content-gate - redesigned to leak via `cp` + `mv "$TMP" "$TMP.bak"`,
  now caught by the leftovers assertion instead.
- Dockerfile non-root-user: a broken variant that switches back to
  `USER root` *after* `USER appuser` was passing, because the check only
  confirmed `appuser` appeared somewhere rather than being the *last*
  USER instruction before CMD - fixed to check the last one.
- HCL RDS hardening: a literal-substring check broke when the reference
  solution's `=` alignment shifted for a longer attribute name -
  loosened to a whitespace-tolerant regex.
- HCL ASG health-check: `terraform validate` failed on a real AWS
  provider schema constraint (`aws_autoscaling_group` requires one of
  `launch_template`/`launch_configuration`/`mixed_instances_policy`) -
  debugged locally against the installed `terraform` binary before
  fixing all three file variants.
- C++ LRU+TTL: an initial design with an iterator-holding `Entry` struct
  hit a template/iterator compile error - redesigned to the standard
  `std::list<Node>` + `unordered_map<string, list<Node>::iterator>`
  pattern where the list node itself is the stored element.
- A pre-existing corpus collision (`add_k8s_pod_disruption_budget.json`,
  unrelated prior content) triggered a hard file-write assertion -
  adopted a discipline of grepping existing case filenames for topic
  keywords before finalizing each subsequent language's case list, and
  renamed the collision to `add_k8s_pdb_selector_must_match_pods`.

**Verification:** every batch (1-4 cases) verified immediately after
being written; a final combined pass across all 121 case names together:
**262 variants checked across 121 cases, 0 violations.**

Full suite: 375 tests, 1 skipped, 1 failed - the same pre-existing,
unrelated flake as every prior section (`test_check_command_pass_and_fail`,
host Windows venv python path invalid inside the Linux sandbox). `ruff
check .` repo-wide: 7 findings, all inside `repos/fastapi-tasktracker/`
(pre-existing, confirmed untouched by this pass - the SQLAlchemy
string-forward-ref F821s and one unused test variable already flagged in
section 20). `optarena cases validate`: 731/731 structurally valid.

#### Final corpus state

| Metric | Section 20 (before this pass) | Now |
|---|---|---|
| Total cases | 610 | 731 |
| Difficulty 1 / 2 / 3 / 4 / 5 | 168 / 387 / 49 / 3 / 3 | 168 / 387 / **170** / 3 / 3 |
| Difficulty-3 per language | ~2-3 (thin, 18 languages) | 4-27, python richest at 27 |

Difficulty-3 is no longer the thinnest tier by a wide margin - every
language now has at least 4 difficulty-3 cases, most have 6-12. Python
(27) is deliberately the deepest given its share of the overall corpus.
Difficulty 4/5 remain untouched at 3 each, same open gap flagged at the
end of section 20 - not in scope for this pass, which was authorized
specifically as "3 first."

### 22. Corpus expansion: difficulty-4 depth + difficulty-5 breadth across all 18 languages (Round 4's C-4, phase 5)

**Why:** direct follow-up to sections 20/21 - asked how much to build for a
"reasonable corpus" and what difficulty coverage looked like per language.
Answer, queried directly against the live corpus rather than from memory:
every language was well-covered at difficulty 1-3, but difficulty 4/5 sat
at 3 cases each, all three concentrated in the "labels"/"filter fix" pair
built in section 20 - python, java, and typescript each had exactly one
D4 case and one D5 case; the other 15 languages had zero. A proposal to
give every language "1 D4 + 1 D5" (matching disruption-coverage's
breadth-first approach) was explicitly rejected by the user's own
reasoning before it could be raised independently: unlike a disruption case (a
cheap variant of an existing case), each D4/D5 case needs its own
repo-scale fixture, so 1-per-language across 15 new languages would mean
building 15 new fixtures for a statistically meaningless n=1 apiece.
Depth was recommended instead - authorized as **"4 D4, 2 D5 for all,
start with D4 first"**, i.e. every language reaches exactly 4 D4 and 2 D5
cases, whatever that takes in new fixtures.

**Adapting "repo-scale" to 6 languages with no application runtime:**
SQL, YAML, HCL, Dockerfile, Makefile, and Shell don't have a 20-30 file
application to edit. Put to the user directly rather than assumed: adapt
the concept per language, or skip D4/D5 for those six. Chosen: adapt.
Concretely, "repo-scale" became multi-manifest Kubernetes YAML
(Deployment+Service+HPA+PDB+ResourceQuota, cross-file consistency
enforced with real `yaml.safe_load` + assertions), multi-file Terraform
HCL validated with the real `terraform validate` (offline provider
mirror, `--network none`), a multi-table/view SQLite schema, a multi-service
Docker Compose + Dockerfiles + nginx.conf stack (static regex checks, no
real `docker build`), a real-`make`-executed multi-module C build system,
and a multi-script bash toolkit sourcing a `lib/` of shared helpers.

#### 15 new repo-scale fixtures, 4 D4 cases each (60 cases), plus 3 existing fixtures deepened by 3 D4 cases each (9 cases) = 69 new D4 cases (72 total with the 3 pre-existing)

New fixtures, one per language not already repo-scale: `express-js-inventory`
(warehouses/items/movements), `gin-helpdesk` (Go), `axum-bookclub` (Rust),
`ruby-eventboard` (plain Ruby, waitlist promotion logic), `aspnet-helpdesk`
(C# minimal API), `php-library` (plain PHP), `kotlin-inventory`
(Spring Boot/Kotlin), `c-jobqueue` (priority queue + scheduler,
multi-file), `cpp-eventbus` (header-only pub/sub), `k8s-appstack`,
`sql-ecommerce`, `terraform-webapp`, `docker-multiservice`,
`makefile-buildsystem`, `shell-toolkit`. Every fixture was smoke-verified
standalone (a throwaway `zzz_smoke_<name>` case, reference = a trivial
unmodified-file touch, one deliberately-broken variant) before any real
case was written against it - 0 violations each time - then the smoke
case file was deleted.

**A reusable 4-archetype pool**, translated idiomatically per
language/domain rather than templated:

1. **pagination composes with an existing filter** - paginate-after-filter,
   not filter-after-paginate (the classic bug: applying `LIMIT`/`skip` to
   the unfiltered set first, then filtering the already-truncated page).
2. **atomic all-or-nothing bulk operation** - validate the WHOLE batch
   before mutating ANYTHING, not validate-and-mutate item-by-item in the
   same loop (the classic bug: an invalid item partway through a batch
   leaves everything before it already committed).
3. **a numeric cap/invariant enforced from TWO separate mutation paths** -
   e.g. a warehouse's `maxCapacity` checked at both item creation and
   stock-in movements; an agent's `maxOpenTickets` checked at both ticket
   creation and reassignment (the classic bug: the cap is enforced where
   the feature was originally built, then quietly bypassed by whichever
   second path gets added later).
4. **a cascading state flag enforced from THREE separate call sites** -
   deactivating/archiving/pausing an entity must be respected everywhere
   that entity could still be reached (a list-exclusion plus two
   independent action paths; the classic bug: two of the three sites
   remember the check, one doesn't).

Deployed across all 15 new fixtures for a total of 60 D4 cases, then the
same 4-archetype pool minus "labels" (already used in section 20) filled
in 3 more D4 cases each for the three pre-existing fixtures
(`fastapi-tasktracker`, `springboot-tasktracker`, `express-ts-shortlink`),
bringing every one of the 18 languages to exactly 4 D4 cases.

#### D5: 2 cases per language (36 cases), composing 2+ D4-style invariants into one genuinely harder task

D5 was deliberately not a 5th new archetype - it's the existing 4-archetype
pool *composed*, so a case actually requires reasoning about how two
invariants interact rather than applying either one in isolation. Two
compositions, reused across every application-language fixture:

- **D5-1 - atomic bulk operation validated against a CUMULATIVE cap,
  computed once for the whole batch.** E.g. `POST /items/bulk-add`: sum
  the batch's total quantity, compare `existingTotal + batchTotal` against
  `maxCapacity` ONCE - not each item individually against the pre-batch
  total. The bug this catches is subtler than either source archetype
  alone: a batch of several small items can sail straight past a cap that
  any single one of them wouldn't trip, if the check is done per-item
  against a baseline that's never incremented as earlier items in the
  *same* batch are virtually added.
- **D5-2 - atomic bulk transfer between two collections/owners, plus a
  destination-side cumulative cap.** E.g. `POST /warehouses/:from/transfer-items`:
  validate every item belongs to the source warehouse BEFORE moving any
  of them (atomicity spans TWO collections now, not one - a partial
  transfer leaves items split unpredictably between source and
  destination rather than just "some created, some not"), then check the
  destination's cap using the batch's combined size.

For the 6 declarative/config languages, D5 instead composed two of their
own D4-style cross-file constraints into one task requiring both
simultaneously - e.g. Kubernetes: a new ResourceQuota sized against the
HPA's `maxReplicas` (the true autoscaling peak) rather than the
Deployment's current `spec.replicas` (a ceiling-vs-current-value
confusion, not a stale-baseline bug, but the same "which number is
actually the invariant" difficulty as the API-language D5s); Terraform: a
new scale-in policy + low-CPU alarm that must maintain a real 20-point
hysteresis gap below the existing high-CPU alarm's threshold, not just be
numerically lower; SQL: a `discounted_total_cents` column that must apply
a loyalty-tier discount to the SAME cancelled-order-excluded base as
`total_cents`, not a separately-computed raw sum that forgets the
exclusion; Dockerfile: a port rename (8000->9090) composed with a new
HEALTHCHECK/health-route feature, both of which must land on the SAME new
port; Makefile: a new module whose embedded version string must read the
SAME `VERSION` file the Makefile already threads through `-D`, not
hardcode a stale copy, *and* `install` must depend on `test` passing (not
just `all` building) so an unverified build can never reach `stage/`;
Shell: a `-n`/dry-run flag that must suppress the config backup while
STILL running validation (the opposite scoping direction from an
already-shipped `-f`/force flag, which bypasses validation but must never
skip the backup), plus a validated `-t TARGET` flag that must actually
flow into both the backup step (target-specific file) and the
notification message, not be validated and then dropped.

36 D5 cases total (2 x 18 languages), on top of the pre-existing 3
"reverted-fix" D5 cases from section 20 (39 difficulty-5 cases now).

#### Real bugs found and fixed during the build (all caught by the live Docker oracle, or by local toolchains before trusting a design)

- **Shell `set -u` masking the intended bug:** a broken variant swallowed
  `validate_env`'s failure with `|| true` but then referenced
  `$DEPLOY_TARGET` in the same log line as the reference solution - under
  `set -u` that unset-variable reference aborted the script anyway, for
  the wrong reason, accidentally producing the "correct" (nonzero) exit
  code and masking the real defect. Fixed by keeping the target out of
  the broken variant's log message so it fails for the intended reason.
- **Go `expected_files` glob convention:** `gin-helpdesk`'s files are
  rooted directly at `internal/...` with no leading path segment, so a
  `*/internal/store/store.go`-style pattern (correct for fixtures with a
  prefix directory) silently never matched. Fixed to bare paths, matching
  the convention already used by that fixture's own D4 cases - should
  have been checked before assuming the pattern from a different fixture.
- **Python `crud/__init__.py` export gap:** a new
  `count_active_tasks_for_assignee`/`MAX_ACTIVE_TASKS_PER_ASSIGNEE` pair
  was added to `crud/task.py` but not re-exported through
  `crud/__init__.py`, so `crud.count_active_tasks_for_assignee(...)`
  raised `AttributeError` at request time - caught by the reference
  solution failing its own oracle, not assumed correct by inspection.
- **C linker error from a dropped function body:** rewriting
  `scheduler.c` for a D5 case accidentally omitted `scheduler_submit`'s
  implementation (kept only in the header), breaking both the reference
  AND broken variants with `undefined reference to scheduler_submit` -
  caught immediately since the reference solution failed too.
- **C# silently-passing broken variant from an ambiguous `dotnet test`:**
  `aspnet-helpdesk` has no `.sln` and both `app.csproj` and
  `tests/tests.csproj` at the repo root; a bare `check_command: "dotnet
  test"` silently targeted `app.csproj` (0 tests, exit 0) instead of the
  test project, making a broken variant "pass" the oracle by running no
  tests at all rather than by being correct. Root-caused by direct
  `docker run` reproduction (the optarena verify output alone didn't show
  the "0 tests ran" detail) - fixed to `dotnet test tests/tests.csproj`,
  the convention every other C# case already used.
- **Kotlin/Java syntax cross-contamination:** a Java MockMvc test
  accidentally used Kotlin's property-access syntax
  (`.andReturn().response.contentAsString`) instead of Java's
  `.getResponse().getContentAsString()`, left over from writing the
  Kotlin fixture's tests earlier in the same session - a real compile
  error, caught by the reference solution failing to build.
- **Python off-by-one in a cap-boundary test:** a hidden test tried to
  exceed a cap of 3 by assigning exactly 3 tasks in one batch - "at most
  3" correctly *allows* exactly 3, so the reference solution's correct
  200 response looked like a test failure until the batch was widened to
  4 tasks to genuinely exceed the cap.
- **Python SQLAlchemy session-commit timing masking a real atomicity
  bug:** an initial "non-atomic" broken variant batched `db.commit()`
  once after the loop; when the loop raised `HTTPException` partway
  through, FastAPI's request-scoped session dependency closed the session
  without ever committing, so SQLAlchemy's implicit rollback-on-close
  silently undid the "partial" mutation the broken variant was supposed
  to demonstrate - it accidentally passed the atomicity oracle. Fixed by
  moving `db.commit()` inside the loop (per-item, matching the proven
  pattern from section 20's `bulk_status_atomic` case), so a partial
  mutation genuinely persists.
- **Shell test assertion too broad:** a hidden test asserted
  `'production' in resp.stdout` to check that a notification message
  named the deploy target, but an unrelated `log_info` line earlier in
  the same output already contained "production," so a broken variant
  that dropped the target from the notification specifically still
  passed. Fixed to isolate and check only the `[NOTIFY]`-prefixed line.
- **TypeScript's hand-rolled `express.d.ts` shim:** `express-ts-shortlink`
  has no per-project `node_modules` in the sandbox (the package is
  resolved via `NODE_PATH` at runtime, but `tsc` doesn't consult
  `NODE_PATH`), so a local ambient `types/express.d.ts` stands in for
  `@types/express` - and it only declared the HTTP methods the fixture
  used at the time (`get`/`post`/`delete`), not `patch`. Any new case
  using `router.patch(...)` failed `tsc` with "Property 'patch' does not
  exist" until the shim's `Router` interface gained a `patch` method too
  - traced by reproducing the exact error against a real Docker
  container rather than assuming a local `npm install` reflected the
  sandbox's actual type-resolution setup (a local repro of the same
  error initially looked environment-specific and was nearly dismissed
  as such before confirming it reproduced identically in Docker).

#### Verification

Every new fixture smoke-tested standalone before any real case was
written against it. Every case verified individually immediately after
being written, then combined per-fixture (that fixture's full D4+D5 set
together) as each language finished - all 18 languages passed their own
full combined set with 0 violations. Two additional whole-corpus passes
beyond the per-fixture ones: all 72 D4 cases together (**144 variants, 0
violations**) and all 36 new D5 cases together (**72 variants, 0
violations**) - both catch cross-case collisions a per-fixture check
can't (e.g. the Rust `CARGO_TARGET_DIR` sharing bug from section 17,
re-guarded against here by giving every Rust case's `Cargo.toml` a
globally-unique `version` across all 6 `axum-bookclub` D4+D5 cases, not
just within one case).

Full suite: 365 tests, 1 skipped, 1 failed - the same pre-existing,
unrelated flake as every prior section (`test_check_command_pass_and_fail`,
host Windows venv python path invalid inside the Linux sandbox). A second,
genuine test update was needed and made: `test_corpus_wide_count_dropped_after_the_glob_fix`
asserted `baseline_incompatible` count `< 100`, a bound written before this
pass - every one of the 108 new cases has `setup_repo` set (that's the
literal definition of repo-scale) and is therefore correctly
baseline-incompatible by design, pushing the count to 175. Bound raised to
`< 250`, comment updated to explain why, well above the current count so
it still catches a real regression. `ruff check .` repo-wide: 7 findings,
all inside `repos/fastapi-tasktracker/` (pre-existing, confirmed via `git
status` that none of the flagged files were touched this pass - the same
SQLAlchemy string-forward-ref F821s and one unused test variable already
flagged in sections 20/21).

#### Final corpus state

| Metric | Section 21 (before this pass) | Now |
|---|---|---|
| Total cases | 731 | 836 |
| Difficulty 1 / 2 / 3 / 4 / 5 | 168 / 387 / 170 / 3 / 3 | 168 / 387 / 170 / **72** / **39** |
| D4 per language | 1 (python/java/typescript only), 0 elsewhere | **exactly 4, all 18 languages** |
| D5 per language | 1 (python/java/typescript only), 0 elsewhere | **exactly 2, all 18 languages** |

Difficulty 4 and 5 are no longer a 3-case curiosity confined to three
languages - every language in the corpus now has real, Docker-verified
signal at both tiers. Not claimed as a final ceiling: D4/D5 are still a
small fraction (111 of 836) next to 1-3's volume, and repo-scale
fixture-building remains the expensive part of growing them further -
same caveat section 20 raised, now satisfied at "4 and 2 for all" rather
than "3 total."

_This was the last dated entry in the execution log as of the audit
consolidation. Section 23 below is a new pass added after that
consolidation._

### 23. Real-execution validation of the D3/D4/D5 push, a test-suite temp-dir
leak, and the aider `setup_repo` context gap fixed and verified

**Why:** the corpus-expansion phases (sections 18-22) verified every new
case's `reference_solution` and `broken_solutions` against the Docker
oracle, which proves the cases are structurally sound and self-consistent -
it does not prove a real model/driver stack can actually solve them. Asked
directly: have the new D3/D4/D5 cases been executed for real? Answered by
running all 232 of them (121 D3 + 72 D4 + 39 D5) through `aider` +
`qwen3-coder:30b` over a local Ollama backend, Docker-sandboxed, no
shortcuts.

#### Full 232-case sweep: 98/232 passed (42.2%), and why that number is misleading on its own

`optarena run --driver aider --kind ollama --model qwen3-coder:30b --cases
<all 232> --timeout 300`, single invocation, one persistent Docker sandbox
per language image. Real numbers, not estimates:

| Metric | Value |
|---|---|
| Overall | 98/232 = 42.2% (95% CI 36-49%) |
| Plain (`setup_files`) cases | 87/121 = 71.9% |
| Repo-scale (`setup_repo`) cases | 11/111 = 9.9% |
| D3 | 87/121 = 71.9% |
| D4 | 7/72 = 9.7% |
| D5 | 4/39 = 10.3% |

The blended 42.2% is dominated by a near-total collapse specifically on
`setup_repo` cases, which is exactly what D4/D5 are built from by design -
not evidence the corpus's hard tier is unreasonable, and not (mainly)
evidence of model incapability. A concrete tell, not just a statistic: the
Rust `axum-bookclub` D4/D5 cases were failing with aider writing
`clubs.js`/`books.js` into a Rust project - the model wasn't "forgetting
Rust," it had no visibility into the project being Rust at all.

#### Root cause: `aider_cli.py` never told aider about `setup_repo` files, and had no fallback

Two compounding bugs in `optarena/drivers/aider_cli.py`:

1. `setup_names = list((case.get("setup_files") or {}).keys())` - only
   ever included the case's own small `setup_files` overlay dict as
   explicit context for aider. `setup_repo` (which copies a whole starter
   fixture into the workspace via `prepare_workspace()`, and is the entire
   basis of every D4/D5 case) was never included. For a repo-scale case,
   `setup_names` was effectively empty.
2. The aider invocation passes `--no-git`, which disables aider's own
   automatic repository-map discovery (aider normally uses git to
   enumerate/rank relevant files) - so there was no fallback mechanism
   either. Combined, a D4/D5 case's model had **zero structural awareness**
   of the real project it was supposed to be editing.

**Fix:** `before = snapshot(workspace)` already existed one line above
(the pre-agent-turn snapshot, used to compute the post-run diff) and
already uses the same `IGNORE_DIRS`/`.aider*`-exclusion rules vetted
elsewhere in `cases.py`. Hidden `test_setup_files` are not written until
`evaluate_case()` runs, after the agent's turn, so `before` can never leak
them. Changed `setup_names` to `sorted(before.keys())` - one line,
naturally covers both `setup_repo` and `setup_files` cases, reuses
already-correct code instead of adding new logic.

**Verified with a real before/after, not just code review.** Sampled 15 of
the 100 `setup_repo` cases that failed in the full sweep (diverse
languages: C, C++, C#, Go/JS/TS via inventory/helpdesk fixtures, Kotlin,
Ruby, Shell, shortlink, SQL, Spring Boot, Terraform), re-ran the identical
`optarena run` invocation against the fixed driver, same model, same
timeout, nothing else changed:

| | Before (buggy driver) | After (fixed driver) |
|---|---|---|
| Pass rate on this 15-case sample | 0/15 (all were failures - that's how the sample was selected) | 4/15 = 27% (95% CI 11-52%) |

Passing after the fix: `add_ruby_eventboard_bulk_register_running_count`,
`add_shell_toolkit_deploy_target_validation`,
`add_sql_ecommerce_order_discounts`, `add_terraform_webapp_bastion_access`.
Of the 11 still failing, most now fail in a materially different and much
healthier way - `"Store.cs" missing expected content
"CountOpenTicketsForAgent"`, `"ItemController.kt" missing expected content
"bulkAddItems"` - the model finding and editing the *correct* real file
and getting the specific requirement wrong, not failing to locate the
codebase at all. That remaining gap is genuine D4/D5 task difficulty for a
30B local model, which the driver fix was never going to close by itself,
and is explicitly out of scope as a "bug" - see the "ignore model
capability" instruction that shaped this pass.

#### Two real anomalies investigated during the retest, one fixed, one correctly left alone

- **`add_springboot_tasktracker_task_labels` took 2222.3s against a 300s
  configured timeout.** Root-caused, not dismissed: `run_capture()`'s
  timeout *detection* is sound (`proc.wait(timeout=...)` reliably raises
  `TimeoutExpired`), but the Windows branch of `_kill_process_tree()`
  (`optarena/cases.py`) called `subprocess.run(["taskkill", "/F", "/T",
  "/PID", str(pid)], capture_output=True)` with **no timeout of its own**.
  If `taskkill` itself hangs - plausible here, since aider's Maven/Java
  subprocess tree for a Spring Boot case is more complex than most - the
  whole case blocks for an unbounded time, defeating the timeout mechanism
  entirely. Contrast with `DockerSandbox.stop()` two functions away, which
  already does exactly the right thing (`timeout=15` on both its `stop`
  and `rm` calls, per an existing F-16 comment) - this `taskkill` call was
  the one place that discipline hadn't been applied. Fixed: added
  `timeout=10` with a best-effort `except subprocess.TimeoutExpired:
  pass`, matching the `proc.wait(timeout=5)` best-effort pattern two lines
  below it in the same function. Grepped the rest of the codebase for the
  same unbounded-`subprocess.run` shape; found none. Verified:
  `test_child_process_tree_killed_on_timeout` still passes, full suite
  311/311, `ruff check .` clean.
- **`add_cpp_eventbus_subscriber_cap` recorded a changed file literally
  named `};`.** Investigated rather than assumed: checked the
  `cpp-eventbus` fixture's actual file list to rule out the new
  `setup_names = sorted(before.keys())` fix as the source (no file
  remotely like this exists there, so it isn't a mis-flattened argument
  from the fix above) - it is a genuine file that landed on disk during
  the aider run, correctly detected by `snapshot()`/`changed_files()`
  doing exactly their job. Most likely explanation: aider's own
  SEARCH/REPLACE edit-block parser mis-handling malformed output from
  qwen3-coder:30b and writing the parsing debris as a filename - a
  third-party-tool-plus-model-output-formatting issue, not an optarena
  code path. Per instruction to ignore model-capability-adjacent issues,
  left as diagnosed-but-out-of-scope rather than "fixed."

#### A previously-undocumented test-suite bug: ~35 of ~55 `mkdtemp()` call sites in `tests/test_optarena.py` never cleaned up

Found while sweeping for bugs/stale code unrelated to the above. **9,445
leaked `optarena_*` directories** in `%TEMP%` (~642MB, extrapolated from a
500-directory sample), growing steadily across nearly every session since
2026-07-02 - not a new problem, but never previously root-caused. Broken
down by prefix, `optarena_test_*` accounted for 8,529 of the 9,445 - the
test suite itself, not production code, and not the same issue as F-01
(driver-preparation-failure cleanup, already fixed and confirmed effective
for its own failure path).

Root cause: most `setUp()` methods across the file create a workspace via
`self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_..."))` with no
matching `tearDown()`/`addCleanup()`. Several classes *do* have a
`tearDown()`, but it only restores mocked module-level state
(`_docker_checked_at`, `RESULTS_DIR`, etc.) - not the workspace itself, a
subtle enough pattern to read as "this class handles cleanup" at a glance
without actually doing so. Systematically audited every `tempfile.mkdtemp`
call site (a small Python scan confirmed full coverage, not spot-checking)
and added `self.addCleanup(shutil.rmtree, <path>, ignore_errors=True)` to
every site missing it - about 35 individual fixes across `OracleTests`,
`PrepareWorkspaceTests`, `TestSetupFilesTests`, `SetupRepoContainmentTests`,
`DockerCheckCommandTests`, `SharedSandboxTests`,
`RunScenarioEmptyCasesTests`, `ParallelSandboxSharingTests`,
`ParallelDurabilityTests`, `CachingDriverTrialsPlumbingTests`,
`RunCaptureTests`, `WorkspaceCleanupTests`, `ExecutionOkTests`,
`DynamicDriverIntegrationTests`, `ApiKeyNeverInArgvTests`, `ScenarioTests`
(5 sites), `SchemaValidationTests` (3 sites), `MultiImageSandboxTests`,
`StoreTests`, `RunIdCollisionTests`, `ConfigurableResultsDirTests` (3
sites), `RichMetricsTests`, `ManifestTests`, `PackVersionSortTests`,
`PackInstallAtomicityTests` (2 sites), `RunScenarioLifecycleEventsTests`,
`ManifestTrialsTests`, `RunScenarioCleanupResilienceTests`,
`CheckpointStatusTests`, `CaseSensitiveAssertionTests`,
`IndexLockOwnershipTests`, `BaselineDriverEndToEndTests`,
`SDKDriverBaseTests`. Left alone, correctly: the handful of tests whose
entire point is asserting that *production* code cleans up its own
workspace (`test_run_scenario_removes_its_workspace`,
`test_verify_cases_removes_its_workspace`) - adding test-side cleanup
there would mask a real regression instead of catching one.

Verified empirically, not just "the tests still pass": counted matching
temp directories before and after a full suite run. Before the fix, a
single `pytest` invocation left new leaked directories on very close to
every one of its ~55 workspaces. After, a full run left exactly 2 new
directories, both `.git`-only, both from the same `git_init` test - a
96%+ reduction, investigated but not fully chased to zero (low priority,
noted rather than pursued further). 311/311 tests pass throughout (one
pre-existing unrelated skip).

#### `test_check_command_pass_and_fail` flake, actually fixed this time

Called "the same pre-existing, unrelated flake" in the notes for sections
21 and 22 and left untouched both times. Root-caused properly this pass:
`OracleTests` never set `OPTARENA_DISABLE_SANDBOX=1`, unlike sibling
classes in the same file (`TestSetupFilesTests`, `BaselineDriverEndToEndTests`,
`SDKDriverBaseTests`) that correctly do. The test's check_command uses
`sys.executable` - a host-specific Windows venv path - which is meaningless
if `run_check_command` happens to route it into the Linux sandbox instead
of the host, which it will on any machine with Docker installed and no
explicit opt-out. Fixed by adding the same env patch every other
host-exec test in the file already uses. Genuinely fixed, not
re-suppressed: 311/311 tests pass, zero failures, first time in the
project's recorded history this specific flake has actually been
resolved rather than described.

#### The 7 `repos/fastapi-tasktracker/` ruff findings, resolved

Called "pre-existing, deliberately untouched" in sections 20-22. Resolved
this pass without touching the fixture's runtime behavior: the 4 F821s
(`project.py`/`task.py`/`user.py`) are a genuine SQLAlchemy 3-way circular
`Mapped["Task"]`/`Mapped["Project"]`/`Mapped["User"]` forward-reference
pattern, resolved at runtime by SQLAlchemy's mapper registry against the
shared `Base`, not by Python's import system - ruff's F821 can't see that
resolution path without SQLAlchemy-plugin-level awareness (the way mypy's
sqlalchemy plugin has). Confirmed genuine (not a real bug) by checking
each file's actual imports before touching anything. Fixed with a scoped
`[tool.ruff.lint.per-file-ignores]` entry in `pyproject.toml` targeting
exactly `repos/fastapi-tasktracker/app/models/*.py`, not a blanket
exclusion. The 1 F841 (`t1` unused in `test_tasks.py`) was a genuine
trivial fix - removed the dead binding, kept the side-effecting `POST`
call the test actually needs. Verified: `ruff check .` fully clean
repo-wide, and the fixture's own test suite (unaffected by a lint-only
change, checked anyway) still passes 10/10.

#### Also cleaned up: 65 case descriptions with leftover internal shorthand

Found while making marketing/docs copy human-readable (a separate,
website-focused pass, noted here only because it touched `optarena/cases/`
content): 65 case descriptions (36 D5, 14 D3, 12 D4, across the
corpus-expansion sessions) had a leftover internal `L3`/`L4`/`L5`/`D5
repo-scale (...)` prefix - shorthand from the build sessions that leaked
into shipped case content, redundant with the case's own `difficulty`
field. Stripped the prefix from all 65 (`Repo-scale (fixture): ...`
instead of `D5 repo-scale (fixture): ...`), regenerated the docs site's
generated case catalogue from the fixed source, reconfirmed zero
`[LD][1-5]` matches anywhere in the corpus and `optarena cases validate`
still green (836/836).

#### Verification summary for this whole pass

- Full 232-case real sweep: 98/232 passed, real Docker-sandboxed verdicts,
  saved run `20260802-064228_d345_sweep_aider_qwen3coder30b_54c91e32`.
- 15-case aider-fix retest: 4/15 passed (0/15 before), saved run
  `20260802-084954_aider_setup_repo_fix_retest_83bdda4a`.
- `optarena cases validate`: 836/836 structurally valid.
- Full test suite: 311/311 pass (1 pre-existing unrelated skip, 0
  failures - the first section where this line reads "0 failures").
- `ruff check .`: clean repo-wide (previously always "7 findings,
  pre-existing").
- Temp-directory leak: confirmed via direct before/after measurement, not
  assumed from the code change alone.
- 2 stale Docker containers (10h/20h old, predating this session's work)
  and 1 stale debug artifact removed; 3 more (an in-progress sweep's log,
  two running dev servers' logs) deliberately left alone since their
  owning processes were still active at the time.

