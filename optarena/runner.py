"""
optarena/runner.py
------------------
Executes one scenario: for each case, prepare a clean workspace subdir, invoke
the driver, and collect CaseResults into a RunRecord (the CLI persists it via
store.save_run).

Optional (both default to the historical behaviour):
- ``trials=N``   - run each case N times; ``passed`` is the majority verdict
  and per-trial detail lands in ``extra`` (agent runs are stochastic; one
  trial overstates certainty). Ignored for drivers that cache results from
  ``prepare()`` (see ``Driver.caches_results`` - no current driver sets it).
- ``parallel=N`` - fan cases out over N worker threads for drivers marked
  ``parallel_safe`` (baselines, CLI agents); other drivers stay serial. Each
  worker gets its own dedicated sandbox container per image (F-06), not one
  container shared across workers.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import statistics
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import cases as cases_mod
from . import store
from .cases import DOCKER_IMAGE_DEFAULT, DockerSandbox, container_engine, load_cases
from .drivers import get_driver
from .drivers.base import CaseResult
from .events import RunEvents
from .metrics import aggregate
from .scenario import Scenario

# Bump ONLY when a change to the oracle (cases.evaluate_case / check_expected /
# run_check_command, or the sandbox contract) changes what a pass/fail MEANS -
# so a comparison across that boundary is flagged non-equivalent rather than
# silently treating old and new verdicts as interchangeable (M-01/M-02).
ORACLE_VERSION = 1


def _image_digests(images: list[str]) -> dict[str, str]:
    """
    Best-effort map of image tag -> repo digest (H-03): tags like `:latest`
    are mutable, so a manifest recording only the tag can't prove WHICH image
    bits actually graded a run. Digests are recorded for evidence/reproduction
    but deliberately NOT part of `manifest_compatibility` - two machines with
    byte-different local builds of the same image should warn a human, not
    hard-block a comparison. Empty entries (image not present locally,
    container engine down) record as "unknown".
    """
    import subprocess
    out: dict[str, str] = {}
    for image in images:
        if image == "host":
            continue
        try:
            proc = subprocess.run(
                [container_engine(), "image", "inspect", "--format",
                 "{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Id}}{{end}}", image],
                capture_output=True, text=True, timeout=10,
            )
            digest = proc.stdout.strip() if proc.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            digest = ""
        out[image] = digest or "unknown"
    return out


def build_manifest(scenario: Scenario, cases: list[dict], requested_trials: int,
                    runner_trials: "int | None" = None) -> dict:
    """
    Immutable identity of WHAT a run measured (M-01): the exact resolved case
    set (by name + a content hash of each case, so any edit to a case's
    prompts/oracle/setup invalidates cross-run equivalence), the oracle
    version, the trial count, and the driver/backend/images in play. Two runs
    are only strictly comparable when their (oracle_version, case_set_hash,
    trials) agree - `compare.manifest_compatibility` enforces that. Driver and
    backend intentionally do NOT gate comparability: tool-vs-tool and
    backend-vs-backend are the whole point of a run comparison.

    F-09: ``trials`` records what was REQUESTED (semantically "how many
    times was each case attempted"), not runner.py's own local loop count -
    for a caching driver, the runner hands the count TO the driver and runs
    its own per-case loop exactly once, but the run still semantically has
    N trials and must compare against another N-trial run as such.
    ``runner_trials`` (defaults to ``requested_trials`` when not given)
    separately records how many times the RUNNER's own loop executed, for
    anyone who wants to know the mechanism, not just the semantic count.
    """
    case_entries = sorted(
        (c["name"],
         hashlib.sha1(json.dumps(c, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest())
        for c in cases
    )
    case_set_hash = hashlib.sha1(
        json.dumps(case_entries, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    images = sorted({
        cases_mod.resolve_image(c.get("image") or os.environ.get("OPTARENA_SANDBOX_IMAGE", DOCKER_IMAGE_DEFAULT))
        for c in cases if c.get("check_command")
    })
    return {
        "manifest_version": 1,
        "oracle_version": ORACLE_VERSION,
        "case_count": len(cases),
        "case_names": [n for n, _ in case_entries],
        "case_set_hash": case_set_hash,
        "trials": requested_trials,
        "runner_trials": requested_trials if runner_trials is None else runner_trials,
        "driver": scenario.driver,
        "backend_model": scenario.backend.model,
        "backend_base_url": scenario.backend.base_url,
        "images": images or ["host"],
        "image_digests": _image_digests(images),
    }


def safe_run_name(name: str) -> str:
    """
    Filename-safe form of a scenario name. Names default to
    `<driver>-<model>`, and model ids routinely contain "/" (openrouter-style
    ids) or ":" (Ollama tags) - written literally, "/" makes store.save_run
    fail with FileNotFoundError AFTER the whole run has been paid for, and
    ":" breaks Windows. Squash anything path-hostile.
    """
    return re.sub(r"[^\w.\-+]+", "-", name)


@dataclass
class RunRecord:
    run_id: str
    scenario: dict
    started_at: str
    cases: list[dict] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    manifest: dict = field(default_factory=dict)
    # F-02: "running" while the case loop is in progress, "completed" once
    # every case finished normally, "interrupted" if the process was
    # killed/Ctrl-C'd mid-run, "error" for a driver/sandbox setup failure
    # before any case ran. None only for records predating this field.
    status: "str | None" = None

    def to_dict(self) -> dict:
        return vars(self)


def _merge_trials(case_name: str, results: list[CaseResult]) -> CaseResult:
    """
    Collapse N trial results into one CaseResult.

    ``passed`` is the majority verdict. A single trial is returned unchanged,
    so trials=1 is byte-identical to the historical behaviour.
    """
    if len(results) == 1:
        return results[0]

    passes = sum(1 for r in results if r.passed)
    passed = passes * 2 > len(results)
    errors = [r.error for r in results if r.error]
    last_failed = next((r for r in reversed(results) if not r.passed), None)

    merged = CaseResult(
        name=case_name,
        passed=passed,
        duration_s=statistics.mean(r.duration_s for r in results),
        # Union across trials (first-seen order): showing only the last
        # trial's files could display a failing trial's file list under a
        # majority-pass verdict.
        files=sorted({f for r in results for f in r.files}),
        failures=[] if passed else (last_failed.failures if last_failed else []),
        error=errors[-1] if len(errors) == len(results) else None,
        # True only if EVERY trial's tool invocation reported clean execution
        # - "did every trial run cleanly", not folded into the passed/failed
        # majority vote (each trial's own `passed` already accounts for its
        # own execution_ok, so the vote is unaffected either way).
        execution_ok=all(r.execution_ok for r in results),
    )
    # H-05: cost/token/turn telemetry is PER TRIAL - keeping only the last
    # trial's `extra` (the historical behaviour) silently discarded every
    # other trial's cost and tokens, understating a `--trials N` run's real
    # spend by up to a factor of N. Sum numeric fields across all trials;
    # `stderr` is free text, so concatenate rather than add; `oracle` is
    # reported per trial separately via `oracle_all_trials` below.
    merged.extra = {}
    for r in results:
        for key, val in r.extra.items():
            if key == "oracle":
                continue
            elif key == "stderr":
                merged.extra["stderr"] = merged.extra.get("stderr", "") + val
            elif key in ("steps", "n_steps"):
                # Per-step trajectory is per-trial; keep the representative
                # (last) trial's rather than summing step counts across trials.
                merged.extra[key] = val
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                merged.extra[key] = merged.extra.get(key, 0) + val
            else:
                merged.extra[key] = val
    merged.extra.update({
        "trials": len(results),
        "passes": passes,
        "pass_rate_trials": round(passes / len(results), 3),
        "durations_s": [round(r.duration_s, 2) for r in results],
        "oracle_all_trials": [r.extra.get("oracle") for r in results],
    })
    return merged


def _run_case(driver, case: dict, scenario: Scenario, root: Path, trials: int,
              security_scan: bool = False) -> CaseResult:
    """Run one case (possibly multiple trials), each in a fresh workspace."""
    results: list[CaseResult] = []
    for trial in range(trials):
        ws = root / case["name"] if trials == 1 else root / case["name"] / f"t{trial + 1}"
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
        ws.mkdir(parents=True, exist_ok=True)
        r = driver.run_case(case, scenario, ws)
        if security_scan:
            # Static-scan the files the agent actually changed, before the
            # workspace is cleaned up - "did it introduce a secret/injection?"
            from .security import scan_workspace
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
        finally:
            case_queue.task_done()
        results_queue.put(result)


def _run_parallel(driver, cases: list[dict], scenario: Scenario, root: Path, trials: int,
                   security_scan: bool, parallel: int,
                   sandbox_pool: "dict[str, list[DockerSandbox]]",
                   on_result, events: RunEvents) -> list[CaseResult]:
    """F-06: `parallel` persistent worker threads (not a fresh thread pool
    task per case) so each can hold one dedicated sandbox set for its whole
    lifetime. `on_result(result)` is called as each case finishes (in
    completion order, not input order) - the caller uses it to print and
    checkpoint (F-02) incrementally."""
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

    results: list[CaseResult] = []
    for _ in range(len(cases)):
        result = results_queue.get()
        results.append(result)
        on_result(result)
    for t in threads:
        t.join()
    return results


def _describe_oracle(oracle: dict | None) -> str | None:
    """One-line summary of what the oracle actually did for one trial."""
    if not oracle or not oracle.get("check_command"):
        return None
    if not oracle.get("ran"):
        return f'check_command not run (file checks failed first): {oracle["check_command"]}'
    sandbox = oracle.get("sandbox")
    container = oracle.get("container")
    engine = oracle.get("engine") or "docker"
    where = (f'{engine}:{oracle.get("image")} (container {container})' if sandbox == "docker" and container else
             f'{engine}:{oracle.get("image")}' if sandbox == "docker" else
             "host (no container sandbox)" if sandbox == "host" else "?")
    failure_class = f' [{oracle["failure_class"]}]' if oracle.get("failure_class") else ""
    return (f'check_command via {where}, exit {oracle.get("exit_code")}{failure_class}, '
            f'{oracle.get("duration_s")}s: {oracle["check_command"]}')


def _print_result(result: CaseResult, events: RunEvents) -> None:
    status = "PASS" if result.passed else ("ERROR" if result.error else "FAIL")
    # Phase 2.7: when the tool itself exited non-zero but the workspace
    # otherwise satisfies the oracle (failures is empty), `result.error` and
    # `result.failures` are BOTH empty - without this, the line would print
    # no reason at all for an otherwise-inexplicable FAIL.
    exec_note = "tool reported failure (non-zero exit) despite the artifact " \
                "otherwise passing - see extra.stderr" if not result.execution_ok and not result.failures else None
    detail = "" if result.passed else (
        f" - {result.error or '; '.join(result.failures[:1]) or exec_note}")
    trial_note = (f" [{result.extra.get('passes')}/{result.extra.get('trials')} trials]"
                  if result.extra.get("trials") else "")
    events.say(f" {status} ({result.duration_s:.1f}s){trial_note}{detail}")
    # F-18: case_completed fires here (not a separate call at each call
    # site) so its `status`/`duration_s` always match what the human line
    # just showed - one source of truth for one case's outcome.
    events.emit("case_completed", case=result.name, status=status,
                duration_s=result.duration_s, passed=result.passed)

    # F-18: everything below is a SECONDARY per-case line - .detail(), not
    # .say(), so --log-level warn/error drop it and keep only the PASS/FAIL
    # headline above plus (still via .say()) failure attribution below.
    test_files = result.extra.get("oracle", {}).get("test_setup_files") or []
    if test_files:
        events.detail(f"         test files (hidden from the model): {', '.join(test_files)}")

    diff = result.extra.get("oracle", {}).get("diff")
    if diff and diff.get("files_changed"):
        events.detail(f"         diff: {diff['files_changed']} file(s), "
                      f"~{diff['lines_changed_approx']} line(s) changed")

    # Trajectory: flag off-target edits (files the task never asked for) - a
    # "did it stay on task" signal even when the case passes. Read from the
    # single-run oracle or, for --trials, the last trial's.
    oracle = result.extra.get("oracle") or {}
    traj = oracle.get("trajectory") or next(
        (o.get("trajectory") for o in reversed(result.extra.get("oracle_all_trials") or [])
         if o and o.get("trajectory")), None) or {}
    if traj.get("off_target_count"):
        shown = ", ".join(traj.get("off_target_files", [])[:5])
        more = "" if traj["off_target_count"] <= 5 else f" (+{traj['off_target_count'] - 5} more)"
        note = " despite PASS" if result.passed else ""
        events.detail(f"         off-target edits{note}: {traj['off_target_count']} file(s){' - ' + shown if shown else ''}{more}")
    if result.extra.get("n_steps", 0) and result.extra["n_steps"] > 1:
        events.detail(f"         steps: {result.extra['n_steps']} (per-step timing/tokens in saved run)")

    # Tier 1 (dynamic eval): note any mid-session disruptions that fired.
    fired = [d for st in (result.extra.get("steps") or []) for d in (st.get("disrupted") or [])]
    if fired:
        events.detail(f"         disruptions fired: {'; '.join(fired)}")

    # Tier 3 (failure attribution): for a failed multi-step case, say where -
    # kept at .say() (not .detail()), unlike the rest of this function: this
    # is the one line beyond the PASS/FAIL headline that --log-level warn/error
    # is meant to keep.
    if not result.passed:
        from .metrics import attribute_failure
        why = attribute_failure(result.to_dict())
        if why:
            events.say(f"         attribution: {why}")

    # Security scan (opt-in --security-scan): flag secrets/injection/unsafe calls
    # the agent introduced, even when the case passes its correctness oracle.
    sec = result.extra.get("security")
    if sec and sec.get("total"):
        c = sec.get("counts", {})
        events.detail(f"         security: {sec['total']} finding(s) "
                      f"({c.get('error', 0)} error, {c.get('warning', 0)} warning, {c.get('note', 0)} note)")
        for f in sec["findings"][:3]:
            events.detail(f"           - [{f['level']}] {f['title']} ({f['file']}:{f['line']})")

    oracle_trials = result.extra.get("oracle_all_trials")
    if oracle_trials is not None:
        for i, oracle in enumerate(oracle_trials, 1):
            line = _describe_oracle(oracle)
            if line:
                events.detail(f"         [trial {i}] {line}")
    else:
        line = _describe_oracle(result.extra.get("oracle"))
        if line:
            events.detail(f"         {line}")

    # On failure the check_command tail is already embedded in `detail` above;
    # on success there is no failure detail line, so show the captured output
    # here as proof the sandboxed test actually ran (not just exit-code 0).
    output = result.extra.get("oracle", {}).get("output")
    if output and result.passed:
        first_line = output.splitlines()[0] if output.splitlines() else output
        events.detail(f"         output: {first_line}" + (" [...see saved run for full output]" if "\n" in output else ""))


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
    # starting up.
    cases_mod._docker_available(force_recheck=True)
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
            results = _run_parallel(driver, cases, scenario, root, trials, security_scan,
                                     parallel, sandbox_pool, _on_result, events)
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
