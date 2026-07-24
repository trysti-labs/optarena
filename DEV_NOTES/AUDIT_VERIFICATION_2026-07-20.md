# OptArena Full Code Review, Audit Verification & Remediation — 2026-07-20

_Scope: every file in the `optarena` repo (500-case corpus, Python package, drivers,
ui-harness, dashboard, CI, Dockerfiles), plus the `website/` and `website-docs/`
repos, verified against `OptArena_Comprehensive_AI_Software_Security_Audit_2026-07-18.docx`
and `SECURITY_REMEDIATION_PLAN.md`. Three passes: a full verification review, then two
remediation passes applying every feasible fix.
Method: line-by-line read of all sources, scripted checks across the case corpus,
full unit-test runs (155/155 green), a docs-site production build, `npm audit`, ruff,
and doc-vs-code drift comparison._

**Bottom line.** Commit `9f4f32c` had already fixed the large majority of the audit —
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

---

## 1. Audit finding scorecard (final state)

Status: ✅ fixed in `9f4f32c` · ✅② fixed in this remediation pass · ⏸ decided/deferred by design · ⬜ still open.

| ID | Finding | Status | Where |
|---|---|---|---|
| C-01 | UI harness leaked hidden tests into the live agent workspace | ✅ | `oracle.js` grades a private copy (separate dir + separate Docker mount); live workspace is never written to by anything test-related |
| C-02 | Docker absence silently fell back to host execution | ✅ | Both oracles fail closed; `OPTARENA_NO_DOCKER=1` / `OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1` are the only opt-ins; 4 unit tests |
| C-03 | Agents inherited the full host environment | ✅ + ✅② | CLI drivers: explicit allowlist (`subprocess_env`) in `9f4f32c`. **This pass:** the UI harness now gets the same allowlist (plus display/session vars) instead of a full `os.environ` copy (`vscode_ui.py`); extension seeds are now workspace-scoped — external reads/edits, browser, and MCP are no longer auto-approved (`extensions.js`); UI workspaces moved **outside the repo checkout** to the system temp dir (`paths.js`, `OPTARENA_UI_DIR` to override). Still open: the agent process itself is not containerized (⬜, §4) |
| C-04 | API keys persisted; `serve` exposed the repo root | ✅ + ✅② | Redacted persistence + scoped localhost server in `9f4f32c`. **This pass:** `OPTARENA_API_KEY` env fallback so real keys stay off argv |
| C-05 | Open corpus + broad filesystem access ⇒ answers retrievable | ✅②(partial) / ⏸ | **This pass:** trust model stated explicitly (README "Trust model" section + new `SECURITY.md`): public cases are auditable regression fixtures, not a tamper-resistant benchmark; private packs via `--cases-dir`. UI workspace relocation + external-read lockdown closes the practical answer/secret-lookup path. Reference solutions still ship in the package by design (they power `verify-corpus`); whole-run-root mount in serial mode still open (§4) |
| H-01 | Case paths not contained to the workspace | ✅ | Resolve + `is_relative_to` in both oracles; traversal/absolute tests. (JS check is lexical — noted in §4) |
| H-02 | Shared container across parallel cases + `kill -9 -1` | ✅ | Shared sandbox skipped under `--parallel`; ephemeral per-call containers instead; unit-tested. `reap()` only ever fires serially |
| H-03 | Mutable images / unpinned actions | ✅(partial) | Actions pinned to SHAs; `:git-sha` image tags published alongside `:latest`. Still open: digest-pinned bases, digest recording in manifests (§4) |
| H-04 | Wheel omits runtime assets | ⏸ | Source-checkout-only, documented in `pyproject.toml` + README |
| H-05 | `--trials` kept only the last trial's telemetry | ✅ | Summed across trials; 6 tests |
| H-06 | Empty case set exited green | ✅ | Refused with exit 2; `--allow-empty` to override |
| H-07 | Missing cost presented as free and could win | ✅ + ✅② | Python `_cheaper`/hostname parsing in `9f4f32c`. **This pass:** the dashboard — which still had the exact coerce-to-`$0`/"free" bug — now shows "—"/"no cost/token telemetry reported", only crowns "Cheaper" when *both* runs have real cost data, and suppresses all winner tiles for non-comparable manifests (`dashboard/index.html`) |
| H-08 | UI drivers ignored api_key; `/v1/v1` URLs | ✅ | `API_KEY` forwarded; `openaiBase()` normalization |
| H-09 | Python and JS oracles diverged | ✅ + ✅② | SHA-1 snapshots unified in `9f4f32c`. **This pass:** `diffStats` and `classifyFailure` ported to `oracle.js` and wired into `evaluateCase`, so UI runs now carry the same `diff`/`failure_class` fields as CLI runs; docs claims softened to "kept in sync by hand" with the private-copy caveat. Still open: a generated cross-language conformance fixture suite (§4) |
| H-10 | Corpus self-verification incomplete | ⬜ | Content work (Phase 4): 104 of 500 cases lack `reference_solution`, 42 are skipped by `verify-corpus`, 7 legacy cases untagged. Unchanged — it's an authoring backlog, not a code fix |
| H-11 | Timeouts orphaned process trees; temp dirs leaked | ✅ | Process-group kill both languages; workspace cleanup + `--keep-workspace` |
| H-12 | Docs npm high-severity advisory | ⬜ | Needs `npm` upgrade work + docs CI in `website-docs` (§4) |
| M-01 | No schema validation | ✅ + ✅② | `schema.py` in `9f4f32c`. **This pass:** standalone `optarena cases validate` command |
| M-02 | Non-zero agent exit could still grade PASS | ✅ | `execution_ok` field, merged, printed, tested |
| M-03 | Comparison validity not enforced | ✅ + ✅② | Manifest + gate + suppression + `--force` in `9f4f32c`. **This pass:** `optarena regression` now prints the same NOT-DIRECTLY-COMPARABLE warning (`compare.py`), and the dashboard gates its verdict tiles on manifest compatibility |
| M-04 | Run-id collisions; fixed results dir; ambiguous lookup | ✅ + ✅② | UUID ids, collision refusal, `--results-dir`. **This pass:** a collision at save time no longer tracebacks after a paid run — the record is preserved to a temp file with a clean error (`cli.py`) |
| M-05 | Bare `python` assumed on host | ✅ + ✅② | Corpus fixed (0 of 500) in `9f4f32c`. **This pass:** the test suite's own fixtures now use `sys.executable` — the audit's original 5-tests-fail-on-macOS failure mode is gone |
| M-06 | Coverage gaps / missing CI gates | ✅(partial) | 75→145 tests with per-finding regression tests. Still open: lint gate, OS matrix, JS tests, docs CI (§4) |
| M-07 | Docs stale; org identity split | ✅② | **This pass:** docs site caught up — 500-case counts, regenerated catalogue (now script-generated from case JSON: `website-docs/scripts/generate_catalogue.py`), `editUrl` fixed to the real `selfopt` repo, fail-closed behavior documented, full CLI reference rewritten. Marketing site was updated upstream (hero 500/18/10, reworked corpus section) before this pass. Org naming (selfopt origin vs trysti-labs links/GHCR) remains a product decision (§4) |
| M-08 | Privacy/metric certainty overstated | ✅② | Dashboard "free"/"local backend" labels replaced with explicit unknowns; Google Fonts removed (dashboard now makes zero external requests); docs "Everything is local" replaced with precise data-flow language |
| M-09 | UI drivers silently reduce `--trials` to 1 | ⬜ | Architecture work: per-case UI re-runs with fresh profiles (§4) |
| M-10 | Container least-privilege gaps | ✅(mostly) | cap-drop/no-new-privileges/pids-limit/read-only+tmpfs in `9f4f32c`. Deferred: non-root `--user` (documented in-code), per-case mounts (§4) |
| M-11 | Scenario paths resolved against CWD | ✅ | Resolved against the scenario file's directory |
| L-01 | Lint debt; metric semantics | ✅②(partial) | **This pass:** zero durations no longer dropped from means (`metrics.py`); unused-import/`__all__` cleanup in `drivers/__init__.py`; unused `os` import removed from `crewai_sdk.py`. Still open: unified duration semantics across driver kinds, pricing-table provenance (§4) |
| L-02 | Docs delivery has no CI | ⬜ | Docs CI (build/link/audit) still absent in `website-docs` (§4) |

## 2. Additional findings from this review — and their fixes

| # | Finding | Status |
|---|---|---|
| V-1 | Real `GIT_PAT` in `.env` readable by UI agents (workspace inside the checkout + `readFilesExternally: true`) | ✅② mitigated: workspace moved to system temp, external reads/edits/MCP/browser no longer auto-approved. **⚠ Rotate the PAT** — it sat in reach of any UI agent run before today (§4) |
| V-2 | Dashboard: unknown cost → "free"/$0-wins-Cheaper; no manifest gating | ✅② fixed (see H-07/M-03 rows) |
| V-3 | UI harness process tree inherited the full host env | ✅② fixed (allowlist in `vscode_ui.py`) |
| V-4 | Keys on argv (`--api-key`, aider's `--openai-api-key`) | ✅② `OPTARENA_API_KEY` env fallback added and documented; aider's flag remains (its env alternative is aider-version-dependent) |
| V-5 | `crewai` driver mutated `os.environ` with the scenario key | ✅② removed; key passed only via `LLM(api_key=...)` |
| V-6 | Supply-chain residue (mutable `:latest` default, unpinned bases) | ⬜ (§4) |
| V-7 | Audit docx + internal strategy docs committed to the (public) repo | ⬜ decision for the owner (§4) |
| B-1 | `regression` bypassed the comparability warning | ✅② `format_regression` now prints it |
| B-2 | `rebuild_index` crashed on foreign JSON in `runs/` | ✅② skipped like corrupt files |
| B-3 | Zero durations dropped from aggregate means | ✅② `is not None` filter |
| B-4 | `failure_class: "timeout"` effectively unreachable | ✅② explicit `timed_out` marker set on every timeout path (both oracles); `classify_failure`/`classifyFailure` check it and exit-code 124 first |
| B-5 | `oracle.js` reported `ran: true` for spawn-level failures | ✅② ENOENT now reports "could not run", `ran` stays false; outer-kill marked `timed_out` |
| B-6 | macOS Docker temp-mount edge on the ephemeral verify path | ⬜ noted; the shared-sandbox path (the normal one) is unaffected |
| B-7 | `_merge_trials` keeps only the last trial's `files` | ⬜ cosmetic; left as-is |
| B-8 | Tests used bare `python` | ✅② `sys.executable` |
| B-9 | UI vs CLI duration semantics differ | ⬜ (§4, with M-09) |
| B-10 | `save_run` collision → raw traceback after a paid run | ✅② caught; record preserved to a temp file |
| B-11 | F401 lint debt | ✅② cleaned |

Stale docs/comments fixed this pass: README host-fallback claims (2 places),
ARCH.md `serve`-rooted-at-repo + oracles-"identical" sections, `cases.py`
`diff_stats` size:mtime docstring, `_HARDENING_ARGS` "396" count, `cmd_doctor`
fallback comment, `oracle.js` header (referenced a deleted `workspace.py`),
`vscode_ui.py` header (now lists Kilo). New: `SECURITY.md` (reporting channel,
trust boundaries, credential handling, data flow); `.claude/` added to
`.gitignore`; `AUDIT_VERIFICATION` = this file.

## 3. CLI reorganization (shipped this pass)

Commands are now grouped by noun, with every pre-existing spelling kept as a
working legacy alias (zero script/CI breakage — the full 145-test suite,
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

## 4. New CLI capabilities

New beyond the regrouping: `cases show` (print one case's prompts/oracle/
metadata), `cases validate` (standalone schema check), `runs show` (summary +
per-case table), `sandbox status` (scriptable daemon/image report),
`OPTARENA_API_KEY`, `OPTARENA_SANDBOX_USER`, `OPTARENA_UI_DIR`. Legacy
spellings (`list …`, `init`, `verify-corpus`, `docker …`) are rewritten to the
grouped commands before parsing (`_rewrite_legacy_argv`, unit-tested), so
`--help` shows only the clean canonical set while old scripts keep working.

## 5. Second remediation pass — deeper items closed

Everything the first pass deferred to "still open" that was code-fixable has
now been done and verified:

| Item | Fix | Verified by |
|---|---|---|
| **C-05 custom-pack isolation** | A `cases_dir` (untrusted) pack never uses the shared whole-run-root container; each case gets its own ephemeral container mounting only its own workspace | `test_no_shared_sandbox_for_custom_case_pack` + `test_sandbox_still_started_for_builtin_corpus_serial` |
| **M-10 non-root sandbox** | `OPTARENA_SANDBOX_USER=uid:gid` runs both oracles' containers as that user with HOME on the writable tmpfs (opt-in, documented rationale) | code review; mirrored Python/JS |
| **H-03 base-image digests** | All 9 Dockerfile `FROM`s pinned by multi-arch manifest-list digest (fetched live), tag kept for readability | `grep FROM docker/**` |
| **H-03 manifest digests** | Run manifest records `image_digests` (best-effort `docker image inspect` RepoDigest), for evidence/repro — deliberately not a comparability gate | `_image_digests` |
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

## 6. Verification

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

## 7. What genuinely remains (not code-fixable from here)

These are the honest pre-audit disclosures — none is a defect left unaddressed
for lack of effort; each needs a person, a runtime, or a program:

1. **Rotate the `.env` `GIT_PAT`** — *user action, do before audit.* It was
   reachable by UI agents before the workspace/permission lockdown; treat any
   secret an auto-approving agent could once read as exposed.
2. **Live UI smoke test** — *runtime I don't have.* The UI-harness changes
   (permissions, env allowlist, relocated workspace, `--trials` loop,
   agent-only duration) are syntax- and logic-verified but not driven through
   a real VS Code session. Do one `cline-ui` run before the audit; the risk is
   a missing allowlisted env var or an approval that's now manual.
3. **Agent-process containerization (C-03 endgame)** — *architecture program.*
   CLI agents still run as your user (env-allowlisted, but not in their own
   container separate from the verifier). This is a design effort, not a patch.
4. **Corpus reference backlog (H-10)** — *content authoring.* 104 of 500 cases
   lack a `reference_solution`; 42 are skipped by `verify-corpus`. Author them
   (42 zero-variant first), then flip the CI skip-count gate to zero.
5. **Org identity (M-07 residue)** — *product decision.* Origin is `selfopt`
   while README/GHCR/marketing link `trysti-labs`; pick one canonical.
6. **Publishing the audit + strategy docs (V-7)** — *owner call.* The audit,
   this report, and the corpus-moat strategy sit at the repo root; if the repo
   is public, decide deliberately whether they belong there.
7. **Nothing is committed yet.** All changes across both repos are staged in
   working trees for review; commit/push is yours to make.
