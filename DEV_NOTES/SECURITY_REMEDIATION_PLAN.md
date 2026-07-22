# OptArena Security & Integrity Remediation Plan

_Source: `OptArena_Comprehensive_AI_Software_Security_Audit_2026-07-18.docx`,
reviewing `selfopt/optarena@dff5306` and `selfopt/optarena-docs@5cb2127`._
_This plan written 2026-07-18, after independently re-reading the flagged
source lines against the current repo. Not yet executed - this is a
proposal to review before any of it is implemented._

## How this plan was produced

Every Critical and most High findings were re-verified directly against the
code in this session (not taken on faith from the audit's prose). Where a
number could be independently recomputed, it matched exactly:

| Claim | Recomputed | Match |
|---|---|---|
| Cases with `reference_solution` | 396 (of 500) | exact |
| Cases without `reference_solution` | 104 | exact |
| Cases with no verify variants at all (`verify-corpus` skips) | 42 | exact |
| Built-in `check_command`s invoking bare `python` | 57 | exact |
| Difficulty distribution (1 / 2 / 3, no 4-5) | 157 / 311 / 32 | exact |

That level of precision, combined with direct code inspection below,
is why this plan treats the audit as accurate rather than re-litigating it.

**Directly verified this session (read the exact code, not just the audit's
description of it):**

- **C-01** - `ui-harness/test/agent.e2e.js:379-381` calls `evaluateCase()`
  inside the live polling loop on every tick while the agent is still
  active; `ui-harness/src/oracle.js:357-359` `evaluateCase()` calls
  `writeSetupFiles()`, which writes `test_setup_files` straight into the
  same workspace the agent is actively editing. Confirmed.
- **C-02** - `optarena/cases.py` `run_check_command()` (~line 474) falls
  through to `_run_check_command_local()`, which runs
  `subprocess.run(cmd, shell=True, cwd=root, ...)` directly on the host the
  moment Docker is unavailable - a warning to stderr, not a refusal.
  Confirmed; `ui-harness/src/oracle.js:300-322` mirrors it.
- **C-03** - `optarena/drivers/cli_agents.py:177-181` copies `os.environ`
  wholesale except three narrow exclusions (`ELECTRON_RUN_AS_NODE`,
  `VSCODE_*`, one per-driver `scrub_env_prefixes` - empty for every driver
  except Claude's `("CLAUDE",)`). `--dangerously-skip-permissions` (Claude)
  and `--full-auto` (Codex) are real, present flags. Confirmed.
- **C-04** - `optarena/scenario.py:38,79` - `Backend.api_key` is a plain
  field, `Scenario.to_dict()` serializes `vars(self.backend)` verbatim, and
  `store.py:30-36` `save_run()` writes that straight to
  `results/runs/<id>.json`. `optarena/cli.py` `cmd_serve()` uses
  `SimpleHTTPRequestHandler(directory=str(REPO_ROOT))` - the entire repo
  root, not a scoped results/dashboard directory. Confirmed.
- **C-05** - `pyproject.toml:25-30` packages `optarena*` and `cases/*.json`
  only - the 396 `reference_solution` payloads ship inside the installed
  package. `DockerSandbox.start()` in `cases.py` mounts
  `-v {root}:/workspace` (the whole run root, read-write, no `--user`,
  `--cap-drop`, `--pids-limit`, or `--read-only`). Confirmed (this also
  independently confirms M-10's container-hardening gap).
- **H-01** - `write_setup_files()` in `cases.py` does `dest = root / rel`
  with no `.resolve()` / containment check before `write_text()`. A
  `test_setup_files`/`setup_files` key like `"../../.bashrc"` would write
  outside the workspace. Confirmed.
- **H-02** - `runner.py:206-216` starts exactly one `DockerSandbox` per
  distinct image for the *entire run*, then fans cases out across it via
  `ThreadPoolExecutor` when `--parallel > 1`. `cases.py`
  `DockerSandbox.reap()` and its JS mirror both run `kill -9 -1` inside
  that shared container on a timeout - which kills every other
  concurrently-running case's process in the same container, not just the
  timed-out one. Confirmed.
- **H-04** - `pyproject.toml` packaging config omits `dashboard/`,
  `docker/`, `repos/`, `ui-harness/`, and docs entirely. Confirmed.
- **H-05** - `runner.py:82` `_merge_trials()`: `merged.extra =
  dict(results[-1].extra)` - only the *last* trial's `extra` dict (which
  carries cost/tokens/stderr) survives; the rest are discarded before the
  aggregate counts are merged in. Confirmed.
- **H-09** (oracle drift) - independently confirmed from this session's own
  history: `ui-harness/src/oracle.js:47` still signs files by
  `size:mtimeMs`; the Python side moved to a SHA-1 content hash specifically
  *because* `size:mtime` was proven flaky (documented in this repo's own
  `status.md`, L3 batch). The two oracles are provably not identical.
- **H-10** - see the recomputed-numbers table above; also confirmed 7 legacy
  cases have no `language` tag (`copy_setup_repo`/pre-metadata cases).
- **M-05** - 57 is the exact, recomputed count of bare `python` (vs.
  `python3`/`sys.executable`) in built-in `check_command`s.

**Not independently re-verified line-by-line this session, but consistent
with everything else observed and the audit's demonstrated precision on
every item that *was* checked:** H-03, H-06 through H-08, H-11, H-12, all
Medium and Low findings, and the market/positioning section.

## What is NOT a code defect - flagging for a product decision, not a fix

Section 9 of the audit (repositioning away from "the arena where agents
compete" toward "a local acceptance-test lab") and the "corpus is not a
moat" framing are strategic opinions, not bugs. They don't belong in an
engineering remediation plan and aren't included below as tasks. Worth a
separate conversation once the safety work lands, not before.

## Sequencing logic

Findings are grouped by what they actually require, not just severity,
because two Criticals can have wildly different effort:

- **Contained, mechanical** (C-04, H-01, H-05, H-06, H-07, M-05): a fix in
  one function/file, testable in isolation, no architectural change.
- **Requires a real design decision** (C-01, C-02, C-03, H-02, H-04): the
  *direction* of the fix has tradeoffs (performance, agent capability,
  distribution model) that should be confirmed before writing code.
- **Content work, not code work** (H-10): authoring `reference_solution` +
  `broken_solutions` for the 104 gap cases is exactly the batch-authoring
  process already used to build the corpus this session - it's a large
  content task with a known, proven methodology, not a design problem.
- **Belongs to `optarena-docs`, not `optarena`** (H-12, M-07, L-02): a
  separate repo, separate CI, separate release cadence.

---

## Phase 0 (this week): stop the bleeding - no architecture changes

Small, contained, individually testable. Nothing here changes how a driver
or the sandbox is *supposed* to work - it closes gaps in the current design.

| # | Task | File(s) | Done when |
|---|---|---|---|
| 0.1 | Stop persisting `api_key`. Add `Backend.to_dict()`/`redacted_dict()` that omits it; use it everywhere a `Scenario`/`Backend` is serialized (`to_dict`, logs, exceptions). | `scenario.py`, `store.py`, `runner.py` | A canary key placed in a scenario never appears in any file under `results/`. |
| 0.2 | Narrow `cmd_serve`'s document root to a dedicated `results/` + `dashboard/` view; no directory listing; reject path traversal in the handler. | `cli.py` | `curl localhost:PORT/../optarena/cli.py` (or any path outside the intended root) 404s. |
| 0.3 | Fail closed when Docker is required and unavailable: default to a non-zero, clearly-labeled configuration error instead of falling through to host `shell=True`. Host execution becomes opt-in via an explicit flag (name TBD, e.g. `--allow-unsafe-host-exec`), off by default. | `cases.py` `run_check_command`/`_run_check_command_local`, `ui-harness/src/oracle.js` `runCheckCommand` | A run with Docker stopped and no opt-in flag exits non-zero with a clear message; the same run with the opt-in flag behaves exactly as today. |
| 0.4 | Reject empty resolved case sets by default (`--allow-empty` to override). | `runner.py` `run_scenario`, `cli.py` `cmd_run` | `optarena run --language pythno` (typo) exits non-zero, prints available languages, writes no run artifact. |
| 0.5 | Fix `_merge_trials` to sum billable cost/tokens/turns across trials instead of keeping only the last trial's `extra`; keep per-trial arrays already added for durations. | `runner.py` | 3 trials at a fixed per-trial cost report 3x the total, not 1x; existing majority-vote/duration tests still pass. |
| 0.6 | Path-containment check for every case-supplied relative path (`setup_files`, `test_setup_files`, reference/broken file maps, `setup_repo` name) - resolve, reject `..`/absolute/drive paths, assert `is_relative_to(root)`. One shared implementation, ported to both Python and JS. | `cases.py` `write_setup_files`/`copy_setup_repo`, `ui-harness/src/oracle.js` `writeSetupFiles` | Traversal/absolute/UNC/symlink fixtures are all rejected with a clear error before any file is written, on both oracles. |
| 0.7 | Tri-state cost/token provenance (`known`, `known-zero`, `unknown`) instead of coercing missing telemetry to `0`/"free"; parse backend hostnames with `urllib.parse` instead of substring match for "is this actually local". | `pricing.py`, `compare.py` `_cheaper`, `dashboard/index.html` | A model with no telemetry is never labeled cheaper/free; `localhost.attacker.example` is not classified as local. |
| 0.8 | Replace the remaining bare `python` in built-in `check_command`s with `python3` (mechanical, same technique used throughout this session's batches - `Edit`/bulk `Grep` + verify). | 57 case JSON files under `optarena/cases/` | `grep -c '\bpython \b'` (excluding `python3`) across `check_command` values is 0; `verify-corpus` still green for every touched case. |

## Phase 1 (2-4 weeks): isolation - agent, verifier, and cases stop sharing a blast radius

These require picking a direction first (see "Open decisions" below) but
each is independently shippable once decided.

| # | Task | File(s) | Done when |
|---|---|---|---|
| 1.1 | **C-01 fix**: never write or run hidden tests inside the live agent workspace. On agent-completion signal (UI/trajectory state only - no oracle call in the loop), freeze the workspace, copy it to a separate verifier directory the agent process cannot reach, inject `test_setup_files` there, grade once. | `ui-harness/test/agent.e2e.js`, `ui-harness/src/oracle.js` | An adversarial fake-UI-agent test that watches for `test_*` files never observes one; Python and UI paths produce the same oracle input for the same case. |
| 1.2 | **C-03 fix**: replace "copy `os.environ`" with an explicit allowlist (`PATH`, locale, an isolated `HOME`, exactly the one provider credential the scenario needs). Keep `--dangerously-skip-permissions`/`--full-auto` (agents need them to run headless) but contain their blast radius by running the *agent* inside its own sandbox too, separate from the verifier's. | `drivers/cli_agents.py`, `drivers/base.py` | Security-canary env vars (fake `AWS_SECRET_ACCESS_KEY`, a decoy `~/.ssh` file, a blocked network endpoint) are unreachable from every driver by default. |
| 1.3 | **H-02 fix**: move from one shared container per image to either (a) one ephemeral container per case/trial, or (b) a small worker pool with exclusive per-worker container leases; mount only that case's own subdirectory, not the whole run root. Never issue a container-wide `kill` from a worker that doesn't own the container outright. | `runner.py`, `cases.py` `DockerSandbox`, JS mirror | Two cases run in parallel, one deliberately times out; the other's check_command is provably unaffected (integration test with a fixed-port server + forced timeout + sibling-file canary). |
| 1.4 | Container hardening: non-root UID/GID, `--cap-drop=ALL`, `--security-opt=no-new-privileges`, `--pids-limit`, read-only rootfs with a small tmpfs, per-case mount instead of whole-run mount. | `cases.py` `DockerSandbox`/`_run_check_command_docker`, JS mirror | A fork-bomb/disk-fill fixture fails safely without affecting the host or other cases. |
| 1.5 | Process-tree cleanup on timeout (process group / job object, not just the direct child) for host-mode CLI drivers; clean up `mkdtemp()` run/verify directories in a top-level `finally` (with a `--keep-workspace` debug escape hatch). | `drivers/cli_agents.py`, `drivers/aider_cli.py`, `runner.py`, `verify.py` | A fixture that spawns a background listener and times out leaves no surviving process or port; repeated runs don't accumulate `/tmp` directories. |

## Phase 2 (1-2 months): evidence integrity

| # | Task | File(s) | Done when |
|---|---|---|---|
| 2.1 | Immutable run manifest: hash of case-set version, oracle version, image digests, trial count, driver/provider identity, pricing table version. | `runner.py`, `store.py`, `RunRecord` | Two runs against genuinely different manifests refuse to produce a "faster/cheaper" verdict unless `--force`; comparable runs are unaffected. |
| 2.2 | Comparison validity gate using the manifest above. | `compare.py` | Mismatched case sets / trial counts / provider identity produce "non-equivalent", not a silent winner. |
| 2.3 | Content-addressed (digest-pinned) sandbox images per release; GitHub Actions pinned to full commit SHAs, not tags. | `.github/workflows/*.yml`, `docker/**/Dockerfile`, `cases.py` `ensure_image`/`GHCR_PREFIX` | CI fails on an unpinned action or a `:latest`-only image reference. |
| 2.4 | Unify the Python and JS oracles (H-09): either have the UI harness call the Python verifier post-completion, or publish one versioned conformance spec with a generated cross-language golden fixture suite (globs, Unicode, same-size rewrites, symlinks, timeouts). | `ui-harness/src/oracle.js`, `optarena/cases.py`, new conformance fixtures | The same case produces byte-identical pass/fail and file-diff results through both oracles. |
| 2.5 | JSON Schema validation for scenario/case files before any driver/backend call - required keys, enum/range bounds, size/timeout limits, unique names, unknown-key rejection. | new `optarena/schema.py` or similar, `scenario.py`, `cases.py` | Fuzzed/malformed case packs fail fast with a file+JSON-path error, before any external call. |
| 2.6 | UI driver fixes (H-08): pass `Backend.api_key` into the harness; normalize `/v1` once instead of hardcoding + re-appending. | `drivers/vscode_ui.py`, `ui-harness/src/extensions.js` | A fake authenticated `/v1` endpoint works through the UI driver with and without a trailing `/v1` already present. |
| 2.7 | Record explicit `execution_ok`/`error` separately from `artifact_pass` so a crashed agent with a lucky partial artifact isn't silently PASS. | `drivers/cli_agents.py`, `aider_cli.py`, `base.py` `CaseResult` | An agent that exits non-zero before/after writing a passing file produces a deterministic, documented status - not an unqualified PASS. |

## Phase 3 (2-3 months): release engineering

| # | Task | Done when |
|---|---|---|
| 3.1 | Pick one distribution model and make it true: either bundle `dashboard/`, `docker/`, `repos/`, `ui-harness/` under `optarena/resources` via `importlib.resources` + a platform user-data dir, **or** explicitly declare wheel installs unsupported and document source-checkout as the only supported path. | A clean venv from the built wheel can run `list cases`, `init`, a `setup_repo` case, `serve`, and `docker build` - or the docs/README stop implying the wheel is a complete install. |
| 3.2 | Installed-wheel smoke suite + OS/Python matrix CI (catches the `python`-vs-`python3` class of bug and macOS/Windows/Linux host-fallback portability). | CI matrix green across supported OS/Python combinations with no Docker and no `python` alias present. |
| 3.3 | Resolve scenario-relative paths (`cases_dir`, etc.) against the scenario file's own directory, not the process CWD. | Running the same scenario from three different working directories produces identical manifests/results. |
| 3.4 | Run storage collision fix: UUID/ULID + UTC timestamp run IDs, fail on collision, configurable results directory (XDG/platform data dir or `--results-dir`), exact-ID lookup required when a substring match is ambiguous. | Concurrency test: hundreds of same-scenario-name runs, zero overwrites. |

## Phase 4 (ongoing content work, not code): corpus self-verification completion

This is the H-10 gap and is explicitly **not** a code fix - it's the same
authoring methodology used for every batch this session (write
`reference_solution` + at least one `broken_solutions` entry, verify
through the real Docker sandbox, commit). Track it the same way:

- 104 cases currently lack `reference_solution`; 42 of those lack *any*
  verification variant and are silently skipped by `verify-corpus`.
- Target: zero skipped cases, every case with a passing reference and at
  least one failing variant, `verify-corpus`'s skip count enforced at zero
  in CI once the backlog is cleared (do not flip that CI gate on before the
  backlog is done, or it just goes red).
- Suggested batching: same size/rhythm as the corpus-expansion batches
  (8-13 cases at a time), prioritizing the oldest/legacy cases first (the 7
  with no `language` tag, then the rest of the original 120).

## Documentation repo (`optarena-docs`) - separate track, own commits

- Correct the two false integrity claims immediately, independent of when
  the underlying code fix lands: "tests are never seen" (false for UI
  drivers today - H-01/C-01) and "Python and JS oracles are identical"
  (false - H-09). State the current, honest behavior instead of removing
  the sentence.
- Regenerate the case catalogue and stats from the actual case JSON at
  build time instead of hand-maintained prose (recurring drift risk this
  session's own batches already fought to keep current in `status.md`).
- Fix the canonical org / `editUrl` mismatch (`selfopt` vs `trysti-labs`).
- Add a threat-model / safe-use page: trust boundaries, credential
  handling, what "no Docker" actually means today vs. after Phase 0.3.
- Precise data-flow language: replace "everything is local" with what's
  actually true (local orchestration/storage; prompts/code go to whatever
  backend URL the scenario configures).
- `npm audit --omit=dev` currently reports 21 advisories (20 moderate, 1
  high - `serialize-javascript` via the Docusaurus build chain). Upgrade/
  override to a patched chain; add a docs CI: `npm ci` + build + link
  check + audit gate, all currently absent.

---

## Open decisions before starting Phase 1

These have real tradeoffs and should be confirmed, not assumed:

1. **C-01 architecture**: does "freeze and copy to a separate verifier
   directory" mean a second container, a second VM, or just a
   non-agent-writable directory on the same host? Affects implementation
   size significantly.
2. **C-03 posture**: keeping `--dangerously-skip-permissions`/`--full-auto`
   (agents likely can't run headless without them) but sandboxing the agent
   itself is the audit's suggested middle ground - confirm that's
   acceptable versus a stricter "no headless flags at all" posture.
3. **H-02 performance tradeoff**: per-case/per-trial containers instead of
   one shared container per image will increase container-startup overhead
   per run. Worth measuring before committing to (a) vs (b) in task 1.3.
4. **H-04 distribution model**: bundle assets into the wheel (more
   packaging work, better UX) vs. declare source-checkout-only (less work,
   more honest about current reality) - this is a product call, not
   something to default silently.
5. **Phase 0 vs Phase 1 sequencing**: Phase 0 ships real safety
   improvements without the bigger architectural questions above being
   settled yet. Confirm it's fine to ship Phase 0 as its own release before
   Phase 1 design is finalized (recommended - no reason to gate the
   cheap, high-value fixes on the harder ones).

## Definition of done (adapted from the audit's release gates)

- [ ] No secret canary appears in any artifact, log, exception, or HTTP response.
- [ ] Docker/sandbox unavailable -> non-zero refusal by default; host execution is an explicit opt-in.
- [ ] UI agent never observes `test_setup_files`; Python and UI oracles are conformance-tested as identical.
- [ ] Every case has a passing reference and a failing variant; `verify-corpus` skipped-case count is zero (Phase 4 complete) before that becomes a CI-enforced gate.
- [ ] Every run records an immutable case/oracle/image/driver/provider/pricing identity manifest.
- [ ] Comparisons refuse a winner for incompatible manifests or unknown-provenance cost telemetry.
- [ ] Empty case resolution, malformed schemas, and ambiguous run references fail before any paid agent work.
- [ ] Installed-wheel smoke tests, an OS/Python matrix, and JS/dashboard/docs builds are required CI checks.
- [ ] All GitHub Actions and verifier images are pinned by digest/SHA; no unresolved high/critical advisories in either repo.
