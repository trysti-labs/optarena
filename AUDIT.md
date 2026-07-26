# OptArena Repository Findings and Improvement Plan

_Audit date: 2026-07-26_

## Scope

This document records the repository architecture review and the findings for:

- reliability and failure recovery;
- performance and scalability;
- Docker and Podman behavior;
- build reproducibility;
- packaging and distribution;
- testing and CI;
- maintainability and operational visibility.

The cybersecurity and API-key assessment is intentionally deferred at the
request of the project owner. Security-specific findings are not included in
this document.

## Executive summary

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

---

## Reviewer summary (added after independent verification against the code)

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
(verified against code)"** block. Nothing in this repository has been
changed as part of this review - this is analysis only, at the requester's
explicit instruction.

## Architecture overview

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

- [`optarena/cli.py`](optarena/cli.py): CLI parsing and command dispatch.
- [`optarena/scenario.py`](optarena/scenario.py): scenario and backend models.
- [`optarena/schema.py`](optarena/schema.py): dependency-free validation.
- [`optarena/drivers/`](optarena/drivers/): tool and model integrations.
- [`optarena/cases.py`](optarena/cases.py): workspace preparation, oracle, and
  container execution.
- [`optarena/runner.py`](optarena/runner.py): scenario and trial lifecycle.
- [`optarena/metrics.py`](optarena/metrics.py): aggregate metrics.
- [`optarena/store.py`](optarena/store.py): filesystem result persistence.
- [`optarena/compare.py`](optarena/compare.py): A/B validity and comparison.
- [`optarena/packs.py`](optarena/packs.py): versioned case-pack handling.
- [`dashboard/index.html`](dashboard/index.html): static local dashboard.
- [`docker/`](docker/): offline sandbox images for supported language tracks.

## What is already working well

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

## Prioritized findings

### F-01: Driver preparation is outside the cleanup lifecycle

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
crashes - I did not audit each one.

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

### F-02: Completed cases are lost when a run is interrupted

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

### F-03: Subprocess and HTTP response capture is unbounded

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
process doesn't pay O(n²) for repeated trimming) instead of
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

### F-04: Expected CLI errors escape as raw tracebacks

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
("an unknown case name and an invalid `--matrix-drivers` entry") I did not
independently re-derive line-by-line, but the general claim ("other expected
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

### F-05: No whole-case deadline exists

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
corpus stats (which I independently re-derived and match exactly: 501
one-prompt cases, 7 two-prompt, 2 three-prompt) mean this mostly matters for
9 of 510 cases today, but it's a real correctness gap in the timeout
contract, not a hypothetical one, and would matter more as multi-prompt
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

### F-06: Parallel verification loses the shared-container optimization

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
comment block (tagged H-02/C-05) explaining *why* parallel and custom-pack
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

### F-07: Result indexing is O(number of historical runs) per save

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

### F-08: Comparison files can overwrite each other

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

### F-09: Trial identity and duration metrics are inconsistent

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
caching driver" is accurate today - the code comment at
`optarena/drivers/base.py` and `runner.py`'s own docstring both say no
current driver sets `caches_results`, so this path is dead code today, not
a live bug affecting current runs; it would activate the moment any driver
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
code today (no shipped driver sets it), so this manifest half is verified
by direct testing of the mechanism, not by a live caching-driver run in
production. The duration-sum fix is live and affects every `--trials N > 1`
run today, matching the review's assessment of which half matters now.

### F-10: Infrastructure failures can look like model failures

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

### F-11: Case-pack installation is not fully transactional

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

### F-12: A wheel does not contain the complete application

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

### F-13: Image builds are not fully reproducible

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
via direct search - confirmed for npm and Rust; I did not separately verify
"some downloaded tools are versioned but not represented in a shared image
lock manifest," but given the other four points are all independently
confirmed and no `images.lock.json`-style manifest exists anywhere in the
repo, this is consistent.

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

### F-14: Published sandbox images are effectively AMD64-only

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

### F-15: Mutable image tags remain the normal execution path

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
today to pin, say, "python cases use `:sha-abc123`, go cases use
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

### F-16: Container-engine health is cached too aggressively

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
resource-saver wake-up timeout (I updated that comment this session to also
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

### F-17: CI does not exercise the complete distribution

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
job, no ARM64 job, and no Dockerfile-lint step exist anywhere in
`ci.yml`/`publish-images.yml` - confirmed by reading both files in full. One
addition specific to today's changes: since this session added real Podman
support to the tool, "no CI job actually exercises Podman" is now a slightly
sharper gap than when the audit was likely written against the
still-Docker-only surface - the CI matrix has zero coverage of the
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

### F-18: Operational visibility is mostly console output

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
Every status line in `runner.py`/`cli.py` is a plain `print()` to stdout/stderr
with no level, no structured fields, no way to consume progress
programmatically short of scraping text output. Accurate as stated; this is
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

## Performance observations

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

## Docker and Podman improvement plan

### Runtime

- Keep the existing shared-container optimization for serial built-in runs.
- Introduce isolated worker-container pools for parallel runs.
- Add engine-health TTLs and bounded retries.
- Add cleanup timeouts and post-cleanup verification.
- Record container startup, execution, and cleanup durations.
- Add a configurable Podman SELinux mount mode for Linux environments that
  require relabeling.

### Images

- Build multi-platform images with Buildx.
- Use `TARGETARCH` for downloaded binaries.
- Add dependency lockfiles and an image lock manifest.
- Add GitHub Actions layer caching.
- Publish immutable version and commit tags alongside `latest`.
- Allow scenarios to select an immutable tag for every language image.

### Distribution

OptArena is a local CLI rather than a multi-service server, so Docker Compose
is not required for its core operation. If a containerized OptArena CLI is
published later, document how it accesses:

- the user-provided scenario and case directories;
- the local results directory;
- the model backend;
- the host Docker or Podman engine used for nested verification.

## Recommended implementation sequence

### Phase 1: Failure safety

1. Move preparation into the cleanup lifecycle.
2. Add per-case run checkpoints.
3. Normalize expected CLI exceptions.
4. Add cleanup timeouts.
5. Add explicit infrastructure-error statuses.

### Phase 2: Resource control and performance

1. Bound process and HTTP output.
2. Add whole-case deadlines.
3. Implement container worker pools.
4. Separate agent, oracle, startup, and persistence timing.
5. Make result indexing incremental and concurrency-safe.

### Phase 3: Reproducible containers

1. Add image and dependency lockfiles.
2. Add multi-architecture builds.
3. Add Buildx cache support.
4. Add per-language immutable image selection.
5. Add rootless-Podman CI.

### Phase 4: Distribution and maintainability

1. Package all required assets.
2. Add wheel-install smoke tests.
3. Add dashboard tests.
4. Add structured progress events.
5. Update architecture documentation and packaging metadata.

## Verification completed during the audit

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
not a knock against the findings themselves (I independently confirmed all
of them by reading the same source). Separately: this audit predates (or was
run concurrently with) this session's addition of real Podman support to the
tool - Docker and Podman are both installed and working on this machine as
of this review (verified via a live `podman machine init/start` and a real
`optarena cases verify` run under `OPTARENA_CONTAINER_ENGINE=podman`), so
the container-engine-specific findings (F-14, F-16, F-17's Podman-CI gap)
are current and actionable, not blocked by tooling availability the way
they were when this audit ran.

## Suggested completion criteria

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

## Addendum: user-facing naming cleanup (not one of the 18 findings)

While implementing F-15's Podman-aware image handling, a separate
naming-quality issue surfaced: several user-facing names were
Docker-specific artifacts of a Docker-only past, or used a
double-negative-prone `NO_X` pattern that reads badly regardless of the
Docker/Podman question. Fixed alongside the findings above, with no
back-compat aliases (pre-release, no versioned release has shipped yet):

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
