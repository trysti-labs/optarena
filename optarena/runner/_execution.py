"""Case execution: one case (possibly N trials), the --parallel worker
pool, and `run_scenario` itself - the orchestration entry point that ties
manifest-building, execution, and result assembly together."""

from __future__ import annotations

import os
import queue
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .. import cases as cases_mod
from .. import store
from ..cases import DOCKER_IMAGE_DEFAULT, DockerSandbox, load_cases
from ..drivers import DRIVERS, get_driver
from ..drivers.base import CaseResult
from ..events import RunEvents
from ..metrics import aggregate
from ..scenario import Scenario
from ._manifest import build_manifest
from ._results import RunRecord, _merge_trials, _print_result, safe_run_name


def _run_case(driver, case: dict, scenario: Scenario, root: Path, trials: int,
              security_scan: bool = False) -> CaseResult:
    """Run one case (possibly multiple trials), each in a fresh workspace."""
    results: list[CaseResult] = []
    # P2-07: capability-aware scoring. A driver with no file-editing tools
    # (every baseline/SDK driver, `file_tools: False` in the DRIVERS
    # registry) structurally cannot satisfy some cases - needs a starter
    # repo, needs more than one file - regardless of what the model
    # produces; `cases.baseline_incompatible` (A-40) already computes
    # exactly this. Recorded once per case into `extra`, not silently
    # dropped from anything, so `metrics.aggregate` can report an
    # eligible-only pass rate ALONGSIDE the overall one, with the exclusion
    # reason attached to every case it affected - never removed, never
    # unexplained.
    capability_excluded = None
    if not DRIVERS.get(scenario.driver, {}).get("file_tools", True):
        capability_excluded = cases_mod.baseline_incompatible(case)
    for trial in range(trials):
        ws = root / case["name"] if trials == 1 else root / case["name"] / f"t{trial + 1}"
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True, exist_ok=True)
        # A-36: a non-root sandbox uid can't write into a workspace the HOST
        # user created 0700. No-op unless OPTARENA_SANDBOX_USER is set.
        cases_mod.relax_workspace_permissions(ws)
        # P1-09: the workspace-quota watchdog previously wrapped ONLY
        # check_command - the driver/agent's own write phase (which for a
        # `cli`-kind driver runs on the real host filesystem, per
        # SECURITY.md's trust model) was completely unguarded, so a runaway
        # or malicious agent could fill the host disk before check_command
        # ever ran. There is no generic handle here to forcibly kill an
        # in-flight `driver.run_case()` call the way check_command's own
        # paths can kill their subprocess/container (a CLI driver's
        # subprocess, an SDK driver's worker process, and a baseline
        # driver's HTTP request all have different internal shutdown
        # mechanisms this call site can't reach into) - `on_exceeded` is a
        # no-op here, a real, disclosed asymmetry with check_command's
        # early termination. What this DOES guarantee: the result is never
        # silently trusted if the workspace blew its quota at any point
        # during driver execution, via the poll during the call AND
        # `check_final()`'s synchronous check immediately after it returns
        # (closing the same fast-writer timing gap check_command's own
        # paths were just fixed for).
        watchdog = cases_mod._WorkspaceQuotaWatchdog(ws, on_exceeded=lambda _reason: None).start()
        try:
            r = driver.run_case(case, scenario, ws)
        finally:
            watchdog.stop()
            watchdog.check_final()
        if watchdog.triggered_reason:
            r.passed = False
            r.failures.append(
                f"workspace quota exceeded during agent execution: {watchdog.triggered_reason}")
            r.extra["workspace_quota_exceeded"] = True
        if capability_excluded:
            r.extra["capability_excluded"] = capability_excluded
        if security_scan:
            # Static-scan the files the agent actually changed, before the
            # workspace is cleaned up - "did it introduce a secret/injection?"
            from ..security import scan_workspace
            r.extra["security"] = scan_workspace(r.files, ws)
        results.append(r)
    return _merge_trials(case["name"], results)


def _worker_loop(worker_idx: int, case_queue: "queue.Queue", results_queue: "queue.Queue",
                  driver, scenario: Scenario, root: Path, trials: int, security_scan: bool,
                  sandbox_pool: "dict[str, list[DockerSandbox]]", events: RunEvents) -> None:
    """
    One F-06 worker thread: binds ITS OWN dedicated {image: DockerSandbox}
    map into cases_mod's thread-local routing (`_worker_sandboxes`) before
    touching any case, so every check_command this thread runs execs into a
    container no other worker ever touches - concurrent execs into one
    shared container would otherwise collide in its process table/network
    namespace, and a timeout-triggered `reap()` (`kill -9 -1`) would kill
    every other worker's in-flight process, not just the one that timed out.

    Pulls cases off the shared queue until empty, pushing each completed
    result onto results_queue as it finishes - not batched at the end - so
    the main thread can print/checkpoint incrementally (F-02) instead of
    only learning about completions once every worker is done.

    A-02: EVERY path out of the per-case body must put exactly one result on
    the queue. The collector below waits for exactly len(cases) results, so a
    worker that died on an unexpected exception (an OSError creating the
    workspace, a MemoryError, anything a driver lets escape) used to leave
    the main thread blocked on `results_queue.get()` forever - the whole run
    hung with no timeout and no diagnostic. Report the failure as this case's
    result instead, exactly as the serial path's per-case error handling
    would, and keep draining the queue.
    """
    cases_mod._worker_sandboxes.map = {
        image: sandboxes[worker_idx] for image, sandboxes in sandbox_pool.items()
    }
    while True:
        try:
            case = case_queue.get_nowait()
        except queue.Empty:
            return
        # F-18: fired from the worker thread itself (not the main thread's
        # completion loop below) - under --parallel, "started" genuinely
        # happens here, concurrently across workers; print()/RunEvents.emit
        # are safe to call from multiple threads (CPython serializes writes
        # to one file object), so no extra locking is needed for this.
        events.emit("case_started", case=case["name"], worker=worker_idx)
        try:
            result = _run_case(driver, case, scenario, root, trials, security_scan)
        except BaseException as exc:   # noqa: BLE001 - see A-02 above
            result = CaseResult(
                name=case["name"],
                error=f"worker {worker_idx} failed: {type(exc).__name__}: {exc}",
                execution_ok=False,
            )
        finally:
            case_queue.task_done()
        results_queue.put(result)


#: A-02: how long the collector below will wait on an otherwise-silent
#: results queue before checking whether any worker is still alive. Only a
#: liveness probe interval, NOT a per-case time limit - a case that legitimately
#: takes an hour keeps the loop waiting, because its worker thread is alive.
_COLLECT_POLL_S = 1.0


def _run_parallel(driver, cases: list[dict], scenario: Scenario, root: Path, trials: int,
                   security_scan: bool, parallel: int,
                   sandbox_pool: "dict[str, list[DockerSandbox]]",
                   on_result, events: RunEvents,
                   results: "list[CaseResult] | None" = None) -> list[CaseResult]:
    """F-06: `parallel` persistent worker threads (not a fresh thread pool
    task per case) so each can hold one dedicated sandbox set for its whole
    lifetime. `on_result(result)` is called as each case finishes (in
    completion order, not input order) - the caller uses it to print and
    checkpoint (F-02) incrementally.

    A-01: `results` is the CALLER'S list, appended to in place as each case
    completes. It used to be a local built here and returned only at the end,
    while `on_result` closed over the caller's still-empty list - so every
    parallel checkpoint serialized zero cases, and an interrupted parallel run
    (the exact scenario F-02 exists for) discarded every completed case,
    because run_scenario's `finally` also read that empty list. Sharing one
    list makes "what the checkpoint sees" and "what completed" the same object.
    """
    if results is None:
        results = []
    case_queue: "queue.Queue" = queue.Queue()
    for case in cases:
        case_queue.put(case)
    results_queue: "queue.Queue" = queue.Queue()
    threads = [
        threading.Thread(
            target=_worker_loop,
            args=(w, case_queue, results_queue, driver, scenario, root, trials, security_scan,
                  sandbox_pool, events),
            daemon=True,
        )
        for w in range(parallel)
    ]
    for t in threads:
        t.start()

    for _ in range(len(cases)):
        # A-02: _worker_loop now guarantees one result per case even on an
        # unexpected exception, so this loop terminates on its own. The
        # timeout + liveness check is the second line of defense: if every
        # worker is somehow gone with results still outstanding, fail the run
        # with a diagnostic instead of blocking forever.
        while True:
            try:
                result = results_queue.get(timeout=_COLLECT_POLL_S)
                break
            except queue.Empty:
                if not any(t.is_alive() for t in threads):
                    raise RuntimeError(
                        f"every parallel worker exited with {len(cases) - len(results)} "
                        f"case(s) unaccounted for - aborting rather than waiting forever"
                    ) from None
        results.append(result)
        on_result(result)
    for t in threads:
        t.join()
    return results


def run_scenario(
    scenario: Scenario,
    workspace_root: Path | None = None,
    trials: int = 1,
    parallel: int = 1,
    allow_empty: bool = False,
    keep_workspace: bool = False,
    security_scan: bool = False,
    events: "RunEvents | None" = None,
) -> RunRecord:
    # F-18: a caller that doesn't pass `events` (every pre-existing caller,
    # including every test) gets a RunEvents with both flags off, which
    # `.say()`/`.emit()` make behave exactly like the old unconditional
    # print()s - this parameter is purely additive.
    events = events or RunEvents()
    cases = load_cases(scenario.cases, cases_dir=scenario.cases_dir)
    if not cases and not allow_empty:
        # H-06: an empty resolved case set (typo'd --cases name, a
        # --language/--framework filter that matches nothing, ...) would
        # otherwise silently produce a "0/0 passed" run whose `failed` count
        # is also 0 - exit code 0, indistinguishable from a real all-green
        # run to anything just checking the exit code (e.g. CI). Refuse
        # instead unless the caller explicitly expects zero cases.
        raise ValueError(
            f"scenario '{scenario.name}' resolved to 0 cases (requested: "
            f"{scenario.cases!r}, cases_dir: {scenario.cases_dir!r}) - refusing to "
            f"run and report a false all-passed result. Pass allow_empty=True "
            f"(CLI: --allow-empty) if this is intentional."
        )
    driver = get_driver(scenario.driver)

    # F-09: keep the REQUESTED trial count for the manifest (what "trials"
    # semantically means for comparability) separate from the runner's own
    # local loop count, which drops to 1 for a caching driver below.
    requested_trials = max(1, int(trials))
    trials = requested_trials
    if trials > 1 and driver.caches_results:
        # M-09: a caching driver runs the whole case set once in prepare(),
        # so the runner's own per-case trial loop can't repeat it. Instead of
        # dropping trials to 1 (which would hide stability for what's likely
        # the most stochastic, flakiest kind of driver), hand the trial count
        # TO the driver so it repeats each case N times itself, with a fresh
        # workspace, and reports the merged majority verdict + per-trial detail.
        driver.trials = trials
        events.say(f"  [runner] {scenario.driver} runs the case set in one session; "
                   f"repeating each case {trials}x inside the harness")
        trials = 1
    parallel = max(1, int(parallel))
    if parallel > 1 and not driver.parallel_safe:
        events.say(f"  [runner] NOTE: {scenario.driver} is not parallel-safe; running serially")
        parallel = 1

    # F-16: a fresh engine-health probe for THIS scenario, not whatever was
    # cached (even within its TTL) from a previous scenario in the same
    # process (--matrix-drivers/--matrix-models, or two --scenario files) -
    # a matrix run shouldn't stay convinced the engine is down for its whole
    # duration just because an early scenario probed it while it was still
    # starting up. A-11/A-12: via the module's public reset (which also
    # re-arms the one-shot "no sandbox" warning) rather than reaching into
    # `_docker_available(force_recheck=True)`.
    cases_mod.reset_engine_health_cache()
    # A-13: image-pull backoff state is per-run too - a registry blip during
    # scenario 1 must not permanently mark an image unpullable for scenario 5.
    cases_mod.reset_pull_backoff()
    # F-15: bind this scenario's image_overrides so every driver's
    # evaluate_case()->run_check_command() call picks it up without
    # threading a new parameter through every driver module.
    cases_mod.set_image_overrides(scenario.image_overrides)

    # M-XX: two runs of the same scenario started within the same second
    # (a scripted/parallel launch, or a fast test suite) previously got the
    # IDENTICAL run_id and silently overwrote each other's saved JSON. The
    # random suffix makes collisions practically impossible; save_run also
    # refuses to overwrite an existing file as a second line of defense.
    run_id = f"{time.strftime('%Y%m%d-%H%M%S')}_{safe_run_name(scenario.name)}_{uuid.uuid4().hex[:8]}"
    record = RunRecord(
        # redact=True: this is what gets written to results/runs/*.json - the
        # backend's api_key must never land in a persisted artifact. See
        # Scenario.to_dict / Backend.redacted_dict.
        run_id=run_id,
        scenario=scenario.to_dict(redact=True),
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        manifest=build_manifest(scenario, cases, requested_trials, runner_trials=trials),
        status="running",  # F-02
    )

    # H-11: only a workspace WE created here is ours to delete in the finally
    # below - a caller-supplied workspace_root is the caller's to manage.
    owns_workspace = workspace_root is None
    root = workspace_root or Path(tempfile.mkdtemp(prefix="optarena_"))
    root.mkdir(parents=True, exist_ok=True)
    # A-36: the run root is bind-mounted as /workspace; a non-root sandbox uid
    # needs to traverse it before it can reach any case directory.
    cases_mod.relax_workspace_permissions(root)

    events.say(f"\n=== RUN {run_id}  driver={scenario.driver}  "
               f"backend={scenario.backend.label()}  cases={len(cases)}"
               + (f"  trials={trials}" if trials > 1 else "")
               + (f"  parallel={parallel}" if parallel > 1 else "") + " ===")
    # F-18: fired once the run_id/manifest exist but before any case starts -
    # everything a consumer needs to correlate this run's later events
    # (case_started/case_completed/checkpoint_saved/run_completed all carry
    # no run_id of their own, since they're only ever emitted within this
    # one call's lifetime on one stdout stream).
    events.emit("run_started", run_id=run_id, driver=scenario.driver,
                backend=scenario.backend.label(), case_count=len(cases),
                trials=trials, parallel=parallel)

    # One dedicated container PER WORKER per distinct image needed by this
    # run's cases - not one per check_command call, and (serially) not just
    # one overall (a run mixing e.g. a Python case and a Go case needs both
    # toolchains at once). Only started for images some case actually needs
    # via check_command; a no-op (and no print) otherwise or when the
    # container engine/image isn't available (run_check_command then falls
    # back to the host for that case). Resolve each case's image exactly the
    # way run_check_command will (including OPTARENA_SANDBOX_IMAGE and F-15's
    # image_overrides) - otherwise a run with an override set would start a
    # sandbox for the wrong image and every check would silently fall back
    # to one ephemeral container per call.
    #
    # F-06: `--parallel` used to skip the shared sandbox ENTIRELY (H-02: a
    # single container shared across concurrent workers would let their
    # `exec`s collide in its process table/network namespace, and a
    # timeout-triggered `reap()` - `kill -9 -1` - would kill every other
    # worker's in-flight process too). Fixed properly instead of just
    # documented: each worker now gets its OWN dedicated container per
    # image - `pool_size` sandboxes instead of 1 - so concurrent workers
    # never share one, restoring the "no container startup per check" win
    # under --parallel without reintroducing the collision.
    #
    # C-05: CUSTOM case packs (`cases_dir` set) still get NO shared/pooled
    # sandbox at all, serial or parallel - a shared container (pooled or
    # not) bind-mounts the WHOLE run root, so one case's check_command could
    # read or tamper with another case's workspace. Acceptable for the
    # built-in corpus (repo-controlled, self-verified); not for a downloaded
    # pack. The ephemeral per-call fallback mounts only that one case's own
    # workspace directory, so a malicious case is confined to itself - a
    # security property, not a performance one, so pooling doesn't apply.
    if scenario.cases_dir:
        images_needed = set()
    else:
        images_needed = {
            cases_mod.resolve_image(c.get("image") or os.environ.get("OPTARENA_SANDBOX_IMAGE", DOCKER_IMAGE_DEFAULT))
            for c in cases if c.get("check_command")
        }
    pool_size = parallel if parallel > 1 else 1
    sandbox_pool: "dict[str, list[DockerSandbox]]" = {
        image: [DockerSandbox(root, image=image) for _ in range(pool_size)]
        for image in images_needed
    }
    all_sandboxes = [sb for lst in sandbox_pool.values() for sb in lst]

    def _checkpoint(results: list[CaseResult]) -> None:
        # F-02: best-effort - a failed checkpoint write must not abort the
        # run itself, only cost it the durability this is here to add.
        record.cases = [r.to_dict() for r in results]
        try:
            store.save_checkpoint(record)
        except OSError:
            return
        events.emit("checkpoint_saved", run_id=run_id, cases_completed=len(results))

    results: list[CaseResult] = []
    status = "running"
    try:
        # F-01: driver.prepare() and sandbox startup are now INSIDE the
        # try/finally that guards cleanup, not before it. A missing CLI
        # binary or SDK package - an everyday failure, not an edge case -
        # previously raised straight out of run_scenario() with the
        # temp workspace from mkdtemp() above never cleaned up, because the
        # finally block that does so hadn't been entered yet.
        driver.prepare(scenario, root)
        for sandbox in all_sandboxes:
            sandbox.start()

        if parallel > 1:
            def _on_result(result: CaseResult) -> None:
                events.say(f"  [case] {result.name} ...", end="")
                _print_result(result, events)
                _checkpoint(results)
            # A-01: `results` is passed IN and appended to in place - not
            # rebound from the return value. The rebinding version left this
            # name pointing at an empty list for the whole run, so both
            # `_checkpoint` above and the `finally` below saw zero cases.
            _run_parallel(driver, cases, scenario, root, trials, security_scan,
                          parallel, sandbox_pool, _on_result, events, results=results)
        else:
            for case in cases:
                events.say(f"  [case] {case['name']} ...", end="", flush=True)
                events.emit("case_started", case=case["name"], worker=0)
                result = _run_case(driver, case, scenario, root, trials, security_scan)
                _print_result(result, events)
                results.append(result)
                _checkpoint(results)  # F-02: one checkpoint per completed case

        status = "completed"
    except BaseException:
        # BaseException, not Exception: a Ctrl-C (KeyboardInterrupt) mid-run
        # is exactly the case F-02 exists for - whatever's in `results` so
        # far must still get one final checkpoint reflecting "interrupted"
        # rather than being silently lost or left claiming "running" forever.
        status = "interrupted"
        raise
    finally:
        # F-01: every cleanup step is now individually best-effort - one
        # failing must not prevent the rest (a sandbox that won't stop
        # shouldn't also skip driver.teardown() or workspace cleanup).
        for sandbox in all_sandboxes:
            try:
                sandbox.stop()
            except Exception:
                pass
        try:
            driver.teardown()
        except Exception:
            pass
        cases_mod.set_image_overrides(None)  # F-15: don't leak into the next scenario in a matrix run
        record.cases = [r.to_dict() for r in results]
        record.status = status
        if status != "completed":
            # Final on-disk state reflects what actually happened, instead
            # of being stuck at "running" forever if nothing downstream ever
            # calls store.save_run for this run_id.
            try:
                store.save_checkpoint(record)
            except OSError:
                pass
        # H-11: a run's mkdtemp workspace (every case's/trial's files, plus
        # any repo copied in) is never read again once results are saved -
        # leaving it behind leaks disk across runs. Remove the one WE created
        # unless the user asked to keep it for debugging (--keep-workspace).
        if owns_workspace and not keep_workspace:
            shutil.rmtree(root, ignore_errors=True)
        elif keep_workspace:
            events.say(f"  [runner] workspace kept at {root}")

    record.summary = aggregate(record.cases)
    events.emit("run_completed", run_id=run_id, status=status, summary=record.summary)
    return record
