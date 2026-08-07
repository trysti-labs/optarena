"""`optarena run` (scenario execution) and the two saved-run comparison
commands, `compare`/`regression`, which share its output helpers."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from ..cases import filter_cases, load_cases
from ..compare import compare_runs, format_regression, format_table, regression_summary, save_comparison
from ..events import RunEvents
from ..runner import run_scenario
from ..scenario import Backend, Scenario
from ..store import load_run, save_run

# F-04: exception types raised by the driver/scenario/sandbox layers for
# EXPECTED failure modes, not real defects - a malformed scenario/case file
# (ValueError/SchemaError), a driver reporting "binary/package not found"
# (RuntimeError - cli_agents.py/aider_cli.py; ImportError/ModuleNotFoundError
# - a missing SDK-driver pip extra), an unknown driver name (KeyError, from
# drivers.get_driver), a missing file, or an OS-level failure starting a
# subprocess/container (OSError). Caught together so every one of these
# reads as a clean one-line CLI error by default instead of a raw traceback.
_EXPECTED_RUN_ERRORS = (ValueError, RuntimeError, KeyError, FileNotFoundError, OSError, ImportError)


def _format_expected_error(e: BaseException) -> str:
    """One-line rendering of an expected/anticipated exception for CLI
    output (F-04). KeyError's own __str__ wraps its message in repr()
    quotes (a display quirk of dict-lookup KeyErrors, not the
    raise-a-readable-message pattern this project's own code uses it for -
    see drivers.get_driver) - special-cased so it doesn't read as
    "KeyError: \"Unknown driver ...\"" with the message doubly quoted."""
    if isinstance(e, KeyError) and e.args:
        return str(e.args[0])
    return f"{type(e).__name__}: {e}"


def _scenario_from_args(args, driver: str | None = None, model: str | None = None) -> Scenario:
    driver = driver or args.driver
    model = model or args.model
    backend = Backend(kind=args.kind, base_url=args.base_url,
                      model=model, api_key=args.api_key,
                      num_ctx=getattr(args, "num_ctx", None))
    name = args.name or f"{driver}-{model}"
    if args.name and (driver != args.driver or model != args.model):
        name = f"{args.name}-{driver}-{model}"   # matrix cells stay distinct
    cases = args.cases.split(",") if args.cases else None
    language = getattr(args, "language", None)
    framework = getattr(args, "framework", None)
    tool_service = getattr(args, "tool_service", None)
    tags = getattr(args, "tags", None)
    like = getattr(args, "like", None)
    if language or framework or tool_service or tags or like:
        loaded = load_cases(cases, cases_dir=args.cases_dir)
        cases = [c["name"] for c in filter_cases(
            loaded, language=language, framework=framework,
            tool_service=tool_service, tags=tags, like=like)]
    return Scenario(
        name=name, driver=driver, backend=backend,
        cases=cases,
        timeout=args.timeout,
        cases_dir=args.cases_dir,
    )


def _warn_baseline_incompatible(scenario: Scenario, events: RunEvents) -> None:
    """
    A-40: before spending any compute, warn when the chosen driver has no
    file tools (writes one flat block of text - every `baseline`/`sdk`
    driver) and some of the selected cases can never be satisfied by that
    shape regardless of what the model produces (needs >1 file, a starter
    repo, or a path a flattened write can't reach - see
    `cases.baseline_incompatible`).

    Best-effort and purely informational: never raises, never changes
    `run`'s exit code or behavior. Any failure resolving the driver or
    loading cases here is silently skipped - `run_scenario` immediately
    after this call will raise the SAME error through its own, already
    correct handling (F-04's clean-error path), so this must not pre-empt
    it with a second, differently formatted one.
    """
    from ..drivers import DRIVERS
    if DRIVERS.get(scenario.driver, {}).get("file_tools", True):
        return   # has real file tools, or an unknown driver - nothing to warn about
    try:
        cases = load_cases(scenario.cases, cases_dir=scenario.cases_dir)
    except Exception:  # noqa: BLE001 - best-effort; run_scenario reports the real error
        return
    from ..cases import baseline_incompatible
    hostile = [c["name"] for c in cases if baseline_incompatible(c)]
    if not hostile:
        return
    events.say(f"  [{scenario.name}] NOTE: {scenario.driver} has no file-editing tools - it writes "
               f"one flat block of text, so {len(hostile)} of {len(cases)} selected case(s) cannot "
               f"pass regardless of the model's answer (needs multiple files, a starter repo, or a "
               f"path a flat write can't reach):")
    shown = hostile[:8]
    for name in shown:
        events.say(f"           - {name}")
    if len(hostile) > len(shown):
        events.say(f"           ... and {len(hostile) - len(shown)} more")
    events.say("           Use a CLI-agent driver (aider, claude-code, ...) to measure these fairly.")


def _confirm_host_native_execution(scenarios: list, args) -> bool:
    """
    P1-05: `cli`-kind drivers (aider, Claude Code, Codex, opencode, goose,
    qwen-code) run headlessly on the HOST, with the invoking user's real
    filesystem, environment, and credentials - only the verifier sandbox is
    containerized (see SECURITY.md's trust model). A user running an
    unfamiliar case pack through one of these could reasonably mistake
    "OptArena sandboxes things" for "OptArena sandboxes the agent too",
    which it explicitly does not. Returns True if the run may proceed.

    `--yes-i-understand-host-execution` is the only way past this
    non-interactively (CI, scripts); an interactive terminal gets a y/N
    prompt instead of an outright refusal. Purely a confirmation gate - it
    never changes which driver runs or how.
    """
    from ..drivers import DRIVERS
    if getattr(args, "yes_i_understand_host_execution", False):
        return True
    host_native = sorted({sc.driver for sc in scenarios
                           if DRIVERS.get(sc.driver, {}).get("kind") == "cli"})
    if not host_native:
        return True
    print(
        f"warning: {', '.join(host_native)} run headlessly on THIS machine, with your real "
        f"filesystem/environment/credentials - only the verification sandbox is containerized, "
        f"not the agent itself (see SECURITY.md). Only proceed with a case pack you trust.",
        file=sys.stderr,
    )
    if sys.stdin.isatty():
        try:
            answer = input("Proceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer in ("y", "yes"):
            return True
        print("error: not confirmed - aborting", file=sys.stderr)
        return False
    print("error: refusing to run a host-native driver non-interactively without "
          "--yes-i-understand-host-execution", file=sys.stderr)
    return False


def _eligible_summary_lines(s: dict) -> list[str]:
    """
    P2-04: `pass_rate` is always the RAW denominator (every case) -
    `eligible_pass_rate`/`adjusted_pass_rate` were computed by
    `metrics.aggregate` since P2-07/F-10 but never printed anywhere, so a
    driver's real accuracy on the cases it could actually attempt was
    invisible without opening the saved run JSON by hand. Returns zero,
    one, or two extra lines - only for the dimensions that actually
    excluded something, so an ordinary run's summary doesn't grow lines
    that always say the same trivial thing. A plain function (not inlined
    into cmd_run) so this formatting is unit-testable without a full run.
    """
    lines = []
    if s.get("capability_excluded_cases"):
        reasons = ", ".join(s.get("capability_exclusion_reasons") or [])
        lines.append(f"     eligible: {s['eligible_pass_rate']:.0%} of "
                     f"{s['cases'] - s['capability_excluded_cases']}/{s['cases']} case(s) "
                     f"this driver could attempt ({s['capability_excluded_cases']} excluded: {reasons})")
    if s.get("infrastructure_errors"):
        lines.append(f"     adjusted: {s['adjusted_pass_rate']:.0%} of "
                     f"{s['cases'] - s['infrastructure_errors']}/{s['cases']} case(s) with a real verdict "
                     f"({s['infrastructure_errors']} infrastructure error(s))")
    return lines


def cmd_run(args) -> int:
    # --pack is shorthand for --cases-dir pointing at an installed pack.
    if getattr(args, "pack", None):
        from ..packs import resolve_pack
        try:
            args.cases_dir = str(resolve_pack(args.pack))
        except FileNotFoundError as e:
            if getattr(args, "debug", False):
                raise
            print(f"error: {e}", file=sys.stderr)
            return 2
    try:
        scenarios: list[Scenario] = [Scenario.from_file(p) for p in args.scenario or []]
        if args.cases_dir:
            for sc in scenarios:
                sc.cases_dir = sc.cases_dir or args.cases_dir
        # --language/--framework/--tool-service/--tags/--like must narrow file
        # scenarios too, not just inline ones (previously they were silently
        # ignored alongside --scenario).
        if any(getattr(args, name, None) for name in
               ("language", "framework", "tool_service", "tags", "like")):
            for sc in scenarios:
                loaded = load_cases(sc.cases, cases_dir=sc.cases_dir)
                sc.cases = [c["name"] for c in filter_cases(
                    loaded, language=args.language, framework=args.framework,
                    tool_service=getattr(args, "tool_service", None),
                    tags=getattr(args, "tags", None), like=getattr(args, "like", None))]
        matrix_drivers = (args.matrix_drivers or "").split(",") if args.matrix_drivers else []
        matrix_models = (args.matrix_models or "").split(",") if args.matrix_models else []
        if matrix_drivers or matrix_models:
            for d in [s.strip() for s in matrix_drivers if s.strip()] or [args.driver]:
                for m in [s.strip() for s in matrix_models if s.strip()] or [args.model]:
                    if d:
                        scenarios.append(_scenario_from_args(args, driver=d, model=m))
        elif args.driver:
            scenarios.append(_scenario_from_args(args))
    except _EXPECTED_RUN_ERRORS as e:
        # ValueError covers SchemaError (a malformed scenario/case file) and
        # json.JSONDecodeError; OSError covers a missing --scenario file; see
        # _EXPECTED_RUN_ERRORS for the rest - all should read as a clean CLI
        # error, not a raw traceback, unless --debug asks for the traceback.
        if getattr(args, "debug", False):
            raise
        print(f"error: {_format_expected_error(e)}", file=sys.stderr)
        return 2
    if not scenarios:
        print("Nothing to run: pass --scenario file.json or --driver ...", file=sys.stderr)
        return 2
    if not _confirm_host_native_execution(scenarios, args):
        return 2

    # F-18: one RunEvents shared across every scenario in this invocation
    # (a single run, or a --matrix-drivers/--matrix-models sweep) - the
    # output mode is a property of the CLI invocation, not of any one
    # scenario within it.
    events = RunEvents(quiet=getattr(args, "quiet", False),
                        json_events=getattr(args, "json_events", False),
                        log_level=getattr(args, "log_level", "info"))
    records = []
    for sc in scenarios:
        _warn_baseline_incompatible(sc, events)
        try:
            rec = run_scenario(sc, trials=args.trials, parallel=args.parallel,
                                allow_empty=args.allow_empty,
                                keep_workspace=args.keep_workspace,
                                security_scan=getattr(args, "security_scan", False),
                                events=events)
        except _EXPECTED_RUN_ERRORS as e:
            # F-04: was `except ValueError` only - a missing CLI binary
            # (RuntimeError from driver.prepare()), an unknown driver name
            # (KeyError), or a missing SDK-driver pip extra (ImportError)
            # all used to escape as a raw traceback instead of this same
            # clean-error path.
            if getattr(args, "debug", False):
                raise
            print(f"error: {_format_expected_error(e)}", file=sys.stderr)
            return 2
        except Exception as e:
            # A genuinely unexpected defect - still fails cleanly by default
            # (automation/CI should never see a raw traceback for ANY
            # failure mode), but the message says so explicitly rather than
            # reading like an anticipated, ordinary error.
            if getattr(args, "debug", False):
                raise
            print(f"error: unexpected {_format_expected_error(e)} "
                  f"(re-run with --debug for the full traceback)", file=sys.stderr)
            return 2
        try:
            path = save_run(rec)
        except FileExistsError as e:
            # run_id collision (clock rollback / copied results dir) - a paid
            # run just completed, so keep its record recoverable rather than
            # dying with a traceback after the work is done.
            print(f"error: {e}", file=sys.stderr)
            fallback = Path(tempfile.mkstemp(prefix=f"{rec.run_id}_", suffix=".json")[1])
            fallback.write_text(json.dumps(rec.to_dict(), indent=2), encoding="utf-8")
            print(f"  run record preserved at {fallback}", file=sys.stderr)
            return 2
        s = rec.summary
        ci = s.get("pass_rate_ci")
        ci_str = f" [{ci[0]:.0%}-{ci[1]:.0%}]" if ci else ""
        clean = f", {s['clean_passes']} clean" if s.get("clean_passes") is not None else ""
        events.say(f"  -> {s['passed']}/{s['cases']} passed ({s['pass_rate']:.0%}{ci_str}{clean}, "
                   f"mean {s['mean_duration_s']}s) - saved {path.name}")
        for line in _eligible_summary_lines(s):
            events.say(line)
        eff = []
        if s.get("tokens_per_pass"):
            eff.append(f"{s['tokens_per_pass']} tokens/pass")
        if s.get("steps_per_pass"):
            eff.append(f"{s['steps_per_pass']} steps/pass")
        if s.get("security_findings"):
            eff.append(f"{s['security_findings']} security finding(s)")
        if eff:
            events.say(f"     efficiency: {', '.join(eff)}")
        records.append(rec)

    if len(records) == 2:
        cmp = compare_runs(records[0].to_dict(), records[1].to_dict())
        events.say(format_table(cmp))
        try:
            saved = save_comparison(cmp)
            events.say(f"  comparison saved: {saved}")
        except FileExistsError as e:
            # F-08: astronomically unlikely (5 UUID collisions), but the runs
            # themselves are already safely saved above regardless - only
            # the secondary comparison artifact is affected. Errors are
            # never suppressed by --quiet - only informational output is.
            print(f"  comparison NOT saved: {e} (both runs are saved; "
                  f"re-run `optarena compare` to retry)", file=sys.stderr)
    elif len(records) > 2:
        events.say()
        events.say(f"  {'scenario':36} {'pass rate':>10} {'mean time':>10} {'tokens':>10}")
        events.say(f"  {'-'*36} {'-'*10} {'-'*10} {'-'*10}")
        for r in records:
            s = r.summary
            tok = s.get("total_tokens")
            events.say(f"  {r.scenario['name']:36} {s['pass_rate']:>9.0%} "
                       f"{s['mean_duration_s']:>9.1f}s {str(tok) if tok else '-':>10}")
        events.say("  (pairwise compare of any two: optarena compare <a> <b>)")
    return 0 if all(r.summary["failed"] == 0 for r in records) else 1


def cmd_compare(args) -> int:
    try:
        run_a, run_b = load_run(args.run_a), load_run(args.run_b)
    except (FileNotFoundError, ValueError) as e:
        # ValueError: an ambiguous substring match (multiple runs) - see
        # store.load_run.
        print(f"error: {e}", file=sys.stderr)
        return 2
    cmp = compare_runs(run_a, run_b, force=getattr(args, "force", False))
    print(format_table(cmp))
    try:
        print(f"  comparison saved: {save_comparison(cmp)}")
    except FileExistsError as e:
        print(f"  comparison NOT saved: {e}", file=sys.stderr)
    return 0


def cmd_regression(args) -> int:
    """
    'Did the upgrade help or hurt?' - accuracy/time/token deltas plus named
    regressed/improved cases. Exit code is 1 if any case regressed, 3 if no
    case regressed but at least one hit an infrastructure error in either
    run (F-10 - a CI gate should be able to tell "the environment broke"
    apart from "nothing regressed" without parsing the printed report), 0
    otherwise - usable as a CI gate on a model/tool/prompt upgrade.
    """
    try:
        run_a, run_b = load_run(args.run_a), load_run(args.run_b)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    cmp = compare_runs(run_a, run_b)
    summary = regression_summary(cmp)
    print(format_regression(summary))
    try:
        print(f"  comparison saved: {save_comparison(cmp)}")
    except FileExistsError as e:
        print(f"  comparison NOT saved: {e}", file=sys.stderr)
    if summary["regressed_cases"]:
        return 1
    # F-10: distinct from "genuinely clean" - an infrastructure error in
    # either run means at least one case's verdict here may not reflect the
    # model/tool at all, which a CI gate should be able to tell apart from
    # "nothing regressed" (exit 0) without having to parse the printed report.
    if summary.get("infrastructure_error_cases"):
        return 3
    return 0
