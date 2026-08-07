"""
optarena/cli/
────────────
Command-line interface. Commands are grouped by noun:

    optarena  (or: python -m optarena) run --scenario s1.json [--scenario s2.json]
    optarena  (or: python -m optarena) run --driver aider --model llama3.2 \
           --base-url http://localhost:11434 --name aider-run [--cases a,b]
    optarena  (or: python -m optarena) compare <run_ref_a> <run_ref_b> [--force]
    optarena  (or: python -m optarena) regression <before> <after>
    optarena  (or: python -m optarena) cases list|show|init|validate|verify
    optarena  (or: python -m optarena) runs list|show
    optarena  (or: python -m optarena) drivers list
    optarena  (or: python -m optarena) sandbox build|pull|status [--lang X|--all]
    optarena  (or: python -m optarena) serve [--port 8300]
    optarena  (or: python -m optarena) doctor [--base-url URL]

Passing two --scenario files to `run` executes both and prints + saves the
comparison automatically.

Legacy spellings (`list [runs|cases|drivers]`, `init`, `verify-corpus`,
`docker build|pull`) still work as aliases of the grouped commands above, so
existing scripts and CI keep running unchanged.

P3-01: this used to be one 1400+ line module. The `cmd_*` handlers are now
split by noun into focused submodules (`_run.py`, `_cases_cmds.py`,
`_runs_cmds.py`, `_doctor.py`, `_sandbox_cmds.py`, `_serve.py`,
`_scan_report.py`); this file keeps only argparse wiring (`main`) plus
re-exports of every name another module or the test suite imports from
`optarena.cli` directly, so `from .cli import X` / `from optarena.cli import
X` needed no changes anywhere else in the codebase.
"""

from __future__ import annotations

import argparse
import os
import sys

from .. import __version__, store
from ..cases import DOCKER_IMAGES
from ..drivers import DRIVER_NAMES
from ..events import LOG_LEVELS
from ._cases_cmds import (
    _SAMPLE_CASE,
    cmd_case_groups,
    cmd_case_show,
    cmd_cases_install,
    cmd_cases_pack,
    cmd_cases_packs,
    cmd_cases_trust_publisher,
    cmd_init,
    cmd_list,
    cmd_validate,
    cmd_verify_corpus,
)
from ._constants import REPO_ROOT
from ._doctor import cmd_doctor
from ._run import (
    _EXPECTED_RUN_ERRORS,
    _confirm_host_native_execution,
    _format_expected_error,
    _scenario_from_args,
    _warn_baseline_incompatible,
    cmd_compare,
    cmd_regression,
    cmd_run,
)
from ._runs_cmds import cmd_run_show, cmd_runs_prune, cmd_runs_rebuild_index, cmd_runs_scrub_secrets
from ._sandbox_cmds import cmd_docker, cmd_sandbox_status
from ._scan_report import cmd_report, cmd_scan
from ._serve import _ScopedDashboardHandler, cmd_serve

__all__ = [
    "REPO_ROOT", "main", "main_exit",
    "_EXPECTED_RUN_ERRORS", "_format_expected_error", "_scenario_from_args",
    "_warn_baseline_incompatible", "_confirm_host_native_execution",
    "cmd_run", "cmd_compare", "cmd_regression", "cmd_list",
    "_SAMPLE_CASE", "cmd_init", "cmd_cases_pack", "cmd_cases_install", "cmd_cases_packs",
    "cmd_cases_trust_publisher", "cmd_case_show", "cmd_case_groups", "cmd_validate", "cmd_verify_corpus",
    "cmd_run_show", "cmd_runs_rebuild_index", "cmd_runs_prune", "cmd_runs_scrub_secrets",
    "cmd_doctor", "cmd_docker", "cmd_sandbox_status",
    "_ScopedDashboardHandler", "cmd_serve", "cmd_scan", "cmd_report",
]

#: Global options that take a VALUE, so `_rewrite_legacy_argv` knows to skip
#: their argument too when scanning for the command token. Anything else
#: starting with "-" before the command is treated as a valueless global flag.
_GLOBAL_VALUE_OPTIONS = ("--results-dir",)


def _rewrite_legacy_argv(argv: list[str] | None) -> list[str] | None:
    """
    Translate the pre-grouping command spellings to the grouped ones so both
    keep working with a single code path and a clean `--help`:

        list [runs|cases|drivers]  ->  <that-noun> list
        init [dir]                 ->  cases init [dir]
        verify-corpus ...          ->  cases verify ...
        docker build|pull ...      ->  sandbox build|pull ...

    Only the command token is rewritten; all flags pass through untouched.

    A-22: ANY leading global flag is skipped over, not just `--results-dir`.
    The scan used to break out of the loop on the first unrecognized token,
    so `--debug`/`--version` (added after this function) stopped the rewrite
    dead and `optarena --debug list runs` failed with "invalid choice: 'list'"
    while `optarena list runs` worked.
    """
    import sys as _sys
    if argv is None:
        argv = _sys.argv[1:]
    argv = list(argv)

    # Find the command token: the first arg that isn't a global option (or the
    # value of one).
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in _GLOBAL_VALUE_OPTIONS:
            i += 2
            continue
        if any(tok.startswith(opt + "=") for opt in _GLOBAL_VALUE_OPTIONS):
            i += 1
            continue
        if tok in ("-h", "--help", "--version"):
            return argv
        if tok.startswith("-"):
            i += 1          # a valueless global flag, e.g. --debug
            continue
        break
    if i >= len(argv):
        return argv

    cmd = argv[i]
    rest = argv[i + 1:]
    head = argv[:i]

    if cmd == "list":
        noun = "runs"
        if rest and rest[0] in ("runs", "cases", "drivers"):
            noun, rest = rest[0], rest[1:]
        return head + [noun, "list", *rest]
    if cmd == "init":
        return head + ["cases", "init", *rest]
    if cmd == "verify-corpus":
        return head + ["cases", "verify", *rest]
    if cmd == "docker":
        return head + ["sandbox", *rest]
    return argv


def main(argv: list[str] | None = None) -> int:
    # Captured tool output is echoed into our own prints (oracle tails, verify
    # details) and may contain characters a non-UTF-8 console can't encode
    # (e.g. node's U+2139 on a cp1252 Windows terminal) - never let a status
    # line crash the run over that.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(
        prog="optarena", description="OptArena - test & compare AI coding tools")
    # A-25: a tool whose whole point is reproducible comparison must be able
    # to say which version produced a result. The version also goes into every
    # run manifest (see runner.build_manifest).
    parser.add_argument("--version", action="version", version=f"optarena {__version__}")
    parser.add_argument("--results-dir",
                        help="where runs/comparisons are stored (default: <repo>/results, "
                             "or OPTARENA_RESULTS_DIR); applies to run/compare/regression/list/serve")
    parser.add_argument("--debug", action="store_true",
                        help="show the full traceback on an expected/unexpected error instead of "
                             "a one-line message (F-04); off by default so automation/CI never "
                             "sees a raw traceback for an everyday failure (missing binary, bad "
                             "scenario file, unreachable container engine, ...)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run one or more scenarios")
    p_run.add_argument("--scenario", action="append", help="scenario JSON file")
    p_run.add_argument("--driver", choices=DRIVER_NAMES, help="inline scenario: driver")
    p_run.add_argument("--name", help="inline scenario name")
    p_run.add_argument("--kind", default="ollama", choices=["ollama", "openai"])
    p_run.add_argument("--base-url", default=os.environ.get("OPTARENA_BASE_URL", "http://localhost:11434"))
    p_run.add_argument("--model", default=os.environ.get("OPTARENA_MODEL", "llama3.2"))
    # OPTARENA_API_KEY env fallback: argv is visible in `ps`/shell history,
    # so a real key should come from the environment, not the command line.
    p_run.add_argument("--api-key", default=os.environ.get("OPTARENA_API_KEY", "optarena"))
    p_run.add_argument("--num-ctx", type=int, default=None,
                        help="Ollama context length override - only honored by ollama-chat "
                             "(native /api/chat); every other driver uses the OpenAI-compat "
                             "endpoint, which doesn't take a per-request override")
    p_run.add_argument("--cases", help="comma-separated case names (default all)")
    p_run.add_argument("--cases-dir", help="load cases from this directory instead of the built-in catalogue")
    p_run.add_argument("--pack", help="run an installed case pack by name or name@version "
                                      "(see `optarena cases packs`); shorthand for --cases-dir")
    p_run.add_argument("--language", help="only run cases tagged with this language (see `optarena list cases`)")
    p_run.add_argument("--framework", help="only run cases tagged with this framework (see `optarena list cases`)")
    p_run.add_argument("--tool-service", help="only run tool-use cases for these tool_service(s), "
                                              "comma-separated (e.g. build_tools,observability - "
                                              "see `optarena cases groups`)")
    p_run.add_argument("--tags", help="only run cases matching this boolean tag expression, e.g. "
                                      "\"tool-use and observability\" or \"not slow\" "
                                      "(see `optarena cases groups`)")
    p_run.add_argument("-k", "--like", help="only run cases whose name contains this substring "
                                            "(case-insensitive)")
    p_run.add_argument("--timeout", type=int, help="per-case timeout override (s)")
    p_run.add_argument("--trials", type=int, default=1,
                       help="run each case N times; majority verdict + per-trial detail (default 1)")
    p_run.add_argument("--parallel", type=int, default=1,
                       help="worker threads for parallel-safe drivers (default 1)")
    p_run.add_argument("--matrix-drivers", help="comma-separated drivers to cross with --matrix-models")
    p_run.add_argument("--matrix-models", help="comma-separated models to cross with --matrix-drivers")
    p_run.add_argument("--allow-empty", action="store_true",
                        help="permit a run whose --cases/--language/--framework filters resolve to "
                             "zero cases (otherwise refused, to avoid a silent false all-passed result)")
    p_run.add_argument("--keep-workspace", action="store_true",
                        help="do not delete the temp workspace after the run (for debugging); "
                             "by default it is removed once results are saved")
    p_run.add_argument("--security-scan", action="store_true",
                        help="statically scan each agent's changed files for secrets / "
                             "injection / unsafe calls (results in extra.security; SARIF via "
                             "`optarena report --format sarif`)")
    p_run.add_argument("--yes-i-understand-host-execution", action="store_true",
                        help="required (non-interactively) before running a `cli`-kind driver "
                             "(aider, Claude Code, Codex, ...): these run headlessly on the HOST "
                             "with your real filesystem/environment/credentials - only the "
                             "verifier sandbox is containerized, not the agent (see SECURITY.md)")
    # F-18: no structured event stream / quiet mode previously existed - an
    # automation wrapper had to scrape the human-formatted console text.
    p_run.add_argument("--log-level", choices=LOG_LEVELS, default="info",
                        help="console verbosity (default info); warn/error drop the secondary "
                             "per-case detail lines and keep only the PASS/FAIL headline + "
                             "failure attribution; quiet is shorthand for --quiet")
    p_run.add_argument("--quiet", action="store_true",
                        help="suppress all human console output (pairs with --json-events for "
                             "a machine-only run; independent of --log-level)")
    p_run.add_argument("--json-events", action="store_true",
                        help="also emit one JSON object per line to stdout for each lifecycle "
                             "event (run_started, case_started, case_completed, "
                             "checkpoint_saved, run_completed) - human console output is "
                             "unaffected unless --quiet/--log-level also asks to suppress it")
    p_run.set_defaults(fn=cmd_run)

    p_cmp = sub.add_parser("compare", help="compare two saved runs")
    p_cmp.add_argument("run_a")
    p_cmp.add_argument("run_b")
    p_cmp.add_argument("--force", action="store_true",
                       help="produce an overall winner even when the two runs are not "
                            "directly comparable (different case set / oracle / trial count)")
    p_cmp.set_defaults(fn=cmd_compare)

    p_reg = sub.add_parser("regression", help="did the upgrade help or hurt? named regressed/improved cases")
    p_reg.add_argument("run_a", help="baseline (\"before\") run")
    p_reg.add_argument("run_b", help="candidate (\"after\") run")
    p_reg.set_defaults(fn=cmd_regression)

    # ── Noun-grouped commands ──────────────────────────────────────────────
    # `cases`/`runs`/`drivers`/`sandbox` group what used to be spread over
    # `list`, `init`, `verify-corpus`, and `docker`. The legacy spellings are
    # registered further down as aliases so existing scripts keep working.

    p_cases = sub.add_parser("cases", help="the task catalogue: list/show/init/validate/verify")
    cases_sub = p_cases.add_subparsers(dest="cases_command", required=True)
    pc_list = cases_sub.add_parser("list", help="list cases (optionally filtered)")
    pc_list.add_argument("--language", help="only cases tagged with this language")
    pc_list.add_argument("--framework", help="only cases tagged with this framework")
    pc_list.add_argument("--tool-service", help="only tool-use cases for these tool_service(s), "
                                                "comma-separated (see `optarena cases groups`)")
    pc_list.add_argument("--tags", help="only cases matching this boolean tag expression, e.g. "
                                        "\"tool-use and observability\" (see `optarena cases groups`)")
    pc_list.add_argument("-k", "--like", help="only cases whose name contains this substring")
    pc_list.set_defaults(fn=cmd_list, what="cases")
    pc_groups = cases_sub.add_parser(
        "groups", help="discover tool_service/domain/language values and case counts, "
                       "to aim --tool-service/--tags/--language at something real")
    pc_groups.add_argument("--cases-dir", help="load from this directory instead of the built-in catalogue")
    pc_groups.set_defaults(fn=cmd_case_groups)
    pc_show = cases_sub.add_parser("show", help="print one case in full (prompts, oracle, metadata)")
    pc_show.add_argument("name")
    pc_show.add_argument("--cases-dir", help="load from this directory instead of the built-in catalogue")
    pc_show.set_defaults(fn=cmd_case_show)
    pc_init = cases_sub.add_parser("init", help="scaffold a project-local cases/ directory")
    pc_init.add_argument("dir", nargs="?", default="cases", help="target directory (default ./cases)")
    pc_init.set_defaults(fn=cmd_init)
    pc_val = cases_sub.add_parser("validate", help="schema-validate case files without running anything")
    pc_val.add_argument("--cases-dir", help="directory to validate (default: the built-in catalogue)")
    pc_val.set_defaults(fn=cmd_validate)
    pc_ver = cases_sub.add_parser(
        "verify", help="CI gate: reference solutions must pass the oracle, broken variants must fail")
    pc_ver.add_argument("--cases", help="comma-separated case names (default all)")
    pc_ver.add_argument("--cases-dir", help="load cases from this directory")
    pc_ver.add_argument("--language", help="only verify cases tagged with this language")
    pc_ver.add_argument("--framework", help="only verify cases tagged with this framework")
    pc_ver.add_argument("--tool-service", help="only verify tool-use cases for these tool_service(s), "
                                               "comma-separated")
    pc_ver.add_argument("--tags", help="only verify cases matching this boolean tag expression")
    pc_ver.add_argument("-k", "--like", help="only verify cases whose name contains this substring")
    pc_ver.add_argument("--strict", action="store_true",
                        help="also fail when a case declares nothing that must FAIL "
                             "(no broken_solutions and no implicit 'unmodified' check) - "
                             "such a case only ever proves its oracle can pass")
    pc_ver.set_defaults(fn=cmd_verify_corpus)
    pc_pack = cases_sub.add_parser("pack", help="bundle a cases dir into a shareable, versioned pack file")
    pc_pack.add_argument("dir", help="directory of case JSONs to pack")
    pc_pack.add_argument("--name", required=True, help="pack name (letters/digits/. _ -)")
    pc_pack.add_argument("--version", default="0.1.0", help="pack version (default 0.1.0)")
    pc_pack.add_argument("--out", help="output file (default <name>-<version>.optpack.json)")
    pc_pack.add_argument("--sign-key", help="sign the pack with this SSH private key "
                                            "(ssh-keygen -Y sign) - publish it as a trusted "
                                            "identity, not just an integrity hash")
    pc_pack.add_argument("--signer-id", help="identity recorded in the signature "
                                             "(default: the --sign-key file's own name)")
    pc_pack.set_defaults(fn=cmd_cases_pack)
    pc_inst = cases_sub.add_parser("install", help="install a pack (local file or URL) into the registry")
    pc_inst.add_argument("source", help="path or http(s) URL to a .optpack.json")
    pc_inst.add_argument("--force", action="store_true", help="overwrite an installed pack of the same name@version")
    pc_inst.add_argument("--allow-insecure", action="store_true",
                          help="allow installing a pack over plain http:// (default: https:// required for URLs)")
    pc_inst.add_argument("--allow-unsigned", action="store_true",
                          help="allow installing a REMOTE (http/https) pack that isn't signed by a "
                               "publisher in your trusted keyring (default: refused - a local file "
                               "path is never gated on this)")
    pc_inst.set_defaults(fn=cmd_cases_install)
    cases_sub.add_parser("packs", help="list installed case packs").set_defaults(fn=cmd_cases_packs)
    pc_trust = cases_sub.add_parser(
        "trust-publisher", help="add a publisher's SSH public key to the local trusted-publisher keyring")
    pc_trust.add_argument("identity", help="identity string to trust (must match the pack's --signer-id)")
    pc_trust.add_argument("public_key", help="path to the publisher's SSH public key (e.g. id_ed25519.pub)")
    pc_trust.set_defaults(fn=cmd_cases_trust_publisher)

    p_runs = sub.add_parser("runs", help="saved runs: list/show/rebuild-index/prune/scrub-secrets")
    runs_sub = p_runs.add_subparsers(dest="runs_command", required=True)
    runs_sub.add_parser("list", help="saved runs, newest first").set_defaults(fn=cmd_list, what="runs")
    pr_show = runs_sub.add_parser("show", help="summary + per-case table for one run")
    pr_show.add_argument("run_ref", help="run id, filename, path, or unique substring")
    pr_show.set_defaults(fn=cmd_run_show)
    # A-23: the recovery path store.py has always pointed users to.
    runs_sub.add_parser(
        "rebuild-index",
        help="rewrite results/index.json from the run files (repair a corrupt/out-of-sync index)"
    ).set_defaults(fn=cmd_runs_rebuild_index)
    pr_prune = runs_sub.add_parser("prune", help="delete old runs, keeping the newest N")
    pr_prune.add_argument("--keep", type=int, default=50,
                          help="how many of the newest runs to keep (default 50)")
    pr_prune.add_argument("--before", help="only delete runs whose id sorts before this "
                                           "(e.g. 20260101 for 'older than 2026-01-01')")
    pr_prune.add_argument("--yes", action="store_true",
                          help="actually delete (without this, prints what would go)")
    pr_prune.set_defaults(fn=cmd_runs_prune)
    # A-24: clean up records written before backend redaction existed.
    pr_scrub = runs_sub.add_parser(
        "scrub-secrets",
        help="redact backend.api_key from run records written before redaction existed")
    pr_scrub.add_argument("--yes", action="store_true",
                          help="actually rewrite the files (without this, only reports them)")
    pr_scrub.set_defaults(fn=cmd_runs_scrub_secrets)

    p_drivers = sub.add_parser("drivers", help="driver registry")
    drivers_sub = p_drivers.add_subparsers(dest="drivers_command", required=True)
    drivers_sub.add_parser("list", help="every driver with kind/backend/status").set_defaults(
        fn=cmd_list, what="drivers")

    p_sandbox = sub.add_parser("sandbox", help="the optarena-tester Docker sandbox images")
    sandbox_sub = p_sandbox.add_subparsers(dest="sandbox_command", required=True)
    for action in ("build", "pull"):
        p_act = sandbox_sub.add_parser(
            action, help=f"{action} sandbox image(s) "
                         f"({'locally from docker/' if action == 'build' else 'from ghcr.io'})")
        p_act.add_argument("--lang", choices=list(DOCKER_IMAGES),
                           help="only this track's image (default: base)")
        p_act.add_argument("--all", action="store_true", help="every registered image")
        p_act.set_defaults(fn=cmd_docker, action=action)
    sandbox_sub.add_parser("status", help="daemon reachability + which images are built").set_defaults(
        fn=cmd_sandbox_status)

    p_scan = sub.add_parser(
        "scan", help="static security scan a directory (secrets / injection / unsafe calls)")
    p_scan.add_argument("dir", help="directory to scan")
    p_scan.set_defaults(fn=cmd_scan)

    p_report = sub.add_parser(
        "report", help="emit CI report artifacts (JUnit XML / HTML / SARIF) from a saved run")
    p_report.add_argument("run_ref", help="run id, filename, path, or unique substring")
    p_report.add_argument("--format", choices=["junit", "html", "sarif", "all"], default="all",
                          help="artifact format (default: all)")
    p_report.add_argument("--out", help="write a single artifact to exactly this file "
                                        "(requires a single --format)")
    p_report.add_argument("--out-dir", help="write artifacts into this directory "
                                            "(default: <results>/reports/<run_id>/)")
    p_report.set_defaults(fn=cmd_report)

    p_serve = sub.add_parser("serve", help="serve the results dashboard")
    p_serve.add_argument("--port", type=int, default=8300)
    p_serve.add_argument("--host", default="127.0.0.1",
                         help="interface to bind (default 127.0.0.1 - localhost only; "
                              "anything else exposes every saved run, unauthenticated)")
    p_serve.set_defaults(fn=cmd_serve)

    p_doc = sub.add_parser("doctor", help="preflight checks for every installed driver")
    p_doc.add_argument("--base-url", default=os.environ.get("OPTARENA_BASE_URL", "http://localhost:11434"))
    p_doc.add_argument("--kind", default="ollama", choices=["ollama", "openai"])
    p_doc.add_argument("--json", action="store_true",
                       help="emit the checks as one JSON object instead of the human table "
                            "(scriptable preflight; exit code is unchanged)")
    p_doc.set_defaults(fn=cmd_doctor)

    # Legacy spellings are rewritten to the grouped commands BEFORE parsing
    # (see _rewrite_legacy_argv), so scripts/CI using the old names keep
    # working while `--help` shows only the clean, canonical command set.
    args = parser.parse_args(_rewrite_legacy_argv(argv))
    if getattr(args, "results_dir", None):
        store.set_results_dir(args.results_dir)
    return args.fn(args)


def main_exit() -> None:
    """Console-script entry point (sys.exit wrapper around main)."""
    sys.exit(main())
