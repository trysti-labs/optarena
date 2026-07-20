"""
optarena/cli.py
────────────
Command-line interface.

    optarena  (or: python -m optarena) run --scenario s1.json [--scenario s2.json]
    optarena  (or: python -m optarena) run --driver aider --model llama3.2 \
           --base-url http://localhost:11434 --name aider-run [--cases a,b]
    optarena  (or: python -m optarena) compare <run_ref_a> <run_ref_b>
    optarena  (or: python -m optarena) regression <run_ref_a> <run_ref_b>
    optarena  (or: python -m optarena) list [runs|cases|drivers]
    optarena  (or: python -m optarena) serve [--port 8300]
    optarena  (or: python -m optarena) doctor [--base-url URL]
    optarena  (or: python -m optarena) init [dir]
    optarena  (or: python -m optarena) docker build|pull [--lang X|--all]
    optarena  (or: python -m optarena) verify-corpus [--cases a,b]

Passing two --scenario files to `run` executes both and prints + saves the
comparison automatically.
"""

from __future__ import annotations

import argparse
import os
import http.server
import sys
from pathlib import Path

from .cases import (
    DOCKER_IMAGES, dockerfile_for, docker_image_available, filter_cases, load_cases,
)
from .compare import compare_runs, format_regression, format_table, regression_summary, save_comparison
from .drivers import DRIVER_NAMES
from .runner import run_scenario
from .scenario import Backend, Scenario
from . import store
from .store import list_runs, load_run, save_run

REPO_ROOT = Path(__file__).resolve().parents[1]


def _scenario_from_args(args, driver: str | None = None, model: str | None = None) -> Scenario:
    driver = driver or args.driver
    model = model or args.model
    backend = Backend(kind=args.kind, base_url=args.base_url,
                      model=model, api_key=args.api_key)
    name = args.name or f"{driver}-{model}"
    if args.name and (driver != args.driver or model != args.model):
        name = f"{args.name}-{driver}-{model}"   # matrix cells stay distinct
    cases = args.cases.split(",") if args.cases else None
    language = getattr(args, "language", None)
    framework = getattr(args, "framework", None)
    if language or framework:
        loaded = load_cases(cases, cases_dir=args.cases_dir)
        cases = [c["name"] for c in filter_cases(loaded, language=language, framework=framework)]
    return Scenario(
        name=name, driver=driver, backend=backend,
        cases=cases,
        timeout=args.timeout,
        cases_dir=args.cases_dir,
    )


def cmd_run(args) -> int:
    try:
        scenarios: list[Scenario] = [Scenario.from_file(p) for p in args.scenario or []]
        if args.cases_dir:
            for sc in scenarios:
                sc.cases_dir = sc.cases_dir or args.cases_dir
        # --language/--framework must narrow file scenarios too, not just
        # inline ones (previously they were silently ignored alongside
        # --scenario).
        if getattr(args, "language", None) or getattr(args, "framework", None):
            for sc in scenarios:
                loaded = load_cases(sc.cases, cases_dir=sc.cases_dir)
                sc.cases = [c["name"] for c in filter_cases(
                    loaded, language=args.language, framework=args.framework)]
        matrix_drivers = (args.matrix_drivers or "").split(",") if args.matrix_drivers else []
        matrix_models = (args.matrix_models or "").split(",") if args.matrix_models else []
        if matrix_drivers or matrix_models:
            for d in [s.strip() for s in matrix_drivers if s.strip()] or [args.driver]:
                for m in [s.strip() for s in matrix_models if s.strip()] or [args.model]:
                    if d:
                        scenarios.append(_scenario_from_args(args, driver=d, model=m))
        elif args.driver:
            scenarios.append(_scenario_from_args(args))
    except (ValueError, OSError) as e:
        # ValueError covers SchemaError (a malformed scenario/case file) and
        # json.JSONDecodeError; OSError covers a missing --scenario file -
        # all should read as a clean CLI error, not a raw traceback.
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not scenarios:
        print("Nothing to run: pass --scenario file.json or --driver ...", file=sys.stderr)
        return 2

    records = []
    for sc in scenarios:
        try:
            rec = run_scenario(sc, trials=args.trials, parallel=args.parallel,
                                allow_empty=args.allow_empty,
                                keep_workspace=args.keep_workspace)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        path = save_run(rec)
        s = rec.summary
        print(f"  -> {s['passed']}/{s['cases']} passed "
              f"(mean {s['mean_duration_s']}s) - saved {path.name}")
        records.append(rec)

    if len(records) == 2:
        cmp = compare_runs(records[0].to_dict(), records[1].to_dict())
        print(format_table(cmp))
        print(f"  comparison saved: {save_comparison(cmp)}")
    elif len(records) > 2:
        print()
        print(f"  {'scenario':36} {'pass rate':>10} {'mean time':>10} {'tokens':>10}")
        print(f"  {'-'*36} {'-'*10} {'-'*10} {'-'*10}")
        for r in records:
            s = r.summary
            tok = s.get("total_tokens")
            print(f"  {r.scenario['name']:36} {s['pass_rate']:>9.0%} "
                  f"{s['mean_duration_s']:>9.1f}s {str(tok) if tok else '-':>10}")
        print("  (pairwise compare of any two: optarena compare <a> <b>)")
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
    print(f"  comparison saved: {save_comparison(cmp)}")
    return 0


def cmd_regression(args) -> int:
    """
    'Did the upgrade help or hurt?' - accuracy/time/token deltas plus named
    regressed/improved cases. Exit code is 1 if any case regressed, so this
    is usable as a CI gate on a model/tool/prompt upgrade.
    """
    try:
        run_a, run_b = load_run(args.run_a), load_run(args.run_b)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    cmp = compare_runs(run_a, run_b)
    summary = regression_summary(cmp)
    print(format_regression(summary))
    print(f"  comparison saved: {save_comparison(cmp)}")
    return 1 if summary["regressed_cases"] else 0


def cmd_list(args) -> int:
    what = args.what
    if what == "cases":
        language = getattr(args, "language", None)
        framework = getattr(args, "framework", None)
        for c in filter_cases(load_cases(), language=language, framework=framework):
            print(f"  {c['name']:28} {c.get('language', '-'):10} "
                  f"{c.get('framework', '-'):12} {c.get('description', '')}")
    elif what == "drivers":
        from .drivers import DRIVERS
        print(f"  {'driver':14} {'kind':10} {'backend':10} {'status':13} summary")
        print(f"  {'-'*14} {'-'*10} {'-'*10} {'-'*13} {'-'*40}")
        for d, meta in DRIVERS.items():
            print(f"  {d:14} {meta['kind']:10} {meta['backend']:10} "
                  f"{meta['status']:13} {meta['summary']}")
    else:
        for r in list_runs():
            s = r.get("summary", {})
            b = r.get("backend", {})
            print(f"  {r['run_id']:44} {r.get('driver', ''):12} "
                  f"{b.get('model', ''):14} {s.get('passed', '?')}/{s.get('cases', '?')} "
                  f"({s.get('mean_duration_s', '?')}s avg)")
    return 0


class _ScopedDashboardHandler(http.server.SimpleHTTPRequestHandler):
    """Serves ONLY dashboard/ (static assets) and results/ (JSON run data) -
    never the repository root. C-04: the previous handler used
    `directory=REPO_ROOT`, which exposed source, scenarios, .git, and every
    other file in the repo to any local process/user that could reach the
    port, not just the two directories the dashboard actually needs
    (dashboard/index.html fetches "../results/index.json" and
    "../results/<run>.json" - i.e. `/results/*` - relative to `/dashboard/`).
    """

    #: url prefix -> directory on disk it's allowed to serve from. Set by
    #: cmd_serve right before the server starts, so a --results-dir override
    #: (applied earlier in main()) is reflected - REPO_ROOT/"results" here
    #: would be stale the moment store.set_results_dir() is called.
    _ROOTS = {"dashboard": REPO_ROOT / "dashboard", "results": REPO_ROOT / "results"}

    def translate_path(self, path: str) -> str:
        # Strip query/fragment the same way the base implementation does.
        path = path.split("?", 1)[0].split("#", 1)[0]
        parts = [p for p in path.split("/") if p not in ("", ".")]
        if not parts or parts[0] not in self._ROOTS:
            return ""  # signal "not servable" - do_GET below turns this into a 404
        base = self._ROOTS[parts[0]].resolve()
        candidate = (base / "/".join(parts[1:])).resolve()
        if candidate != base and base not in candidate.parents:
            return ""  # containment check failed - traversal attempt
        return str(candidate)

    def do_GET(self) -> None:
        if self.path in ("/", ""):
            self.send_response(302)
            self.send_header("Location", "/dashboard/")
            self.end_headers()
            return
        if not self.translate_path(self.path):
            self.send_error(404, "Not Found")
            return
        super().do_GET()

    def list_directory(self, path):  # noqa: ANN001 - matches base signature
        self.send_error(403, "Directory listing disabled")
        return None

    def end_headers(self) -> None:
        # Belt-and-suspenders against embedding/sniffing from other origins;
        # this is a local single-user server, but it's trivial to add.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()


def cmd_serve(args) -> int:
    # Reflect any --results-dir/OPTARENA_RESULTS_DIR override (applied in
    # main() before this runs) rather than the REPO_ROOT default baked in
    # at class-definition time.
    _ScopedDashboardHandler._ROOTS = {"dashboard": REPO_ROOT / "dashboard", "results": store.RESULTS_DIR}
    url = f"http://localhost:{args.port}/dashboard/"
    print(f"OptArena dashboard: {url}  (Ctrl+C to stop)")
    print(f"  serving only dashboard/ and {store.RESULTS_DIR} - not the repository root")
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), _ScopedDashboardHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    return 0


def cmd_docker(args) -> int:
    """Build (or pull) one or all of the `optarena-tester*` sandbox images."""
    import subprocess as _sp

    if args.action == "pull":
        from .cases import docker_image_pull, docker_image_available
        langs = list(DOCKER_IMAGES) if args.all else [args.lang or "base"]
        overall = 0
        for lang in langs:
            image = DOCKER_IMAGES[lang]
            if docker_image_available(image):
                print(f"  {image} already present")
            elif not docker_image_pull(image):
                overall = 1
        return overall

    if args.action != "build":
        print(f"unknown docker action: {args.action}", file=sys.stderr)
        return 2

    if args.all:
        langs = list(DOCKER_IMAGES)
    elif args.lang:
        if args.lang not in DOCKER_IMAGES:
            print(f"unknown --lang '{args.lang}' - choices: {', '.join(DOCKER_IMAGES)}", file=sys.stderr)
            return 2
        langs = [args.lang]
    else:
        langs = ["base"]

    overall = 0
    for lang in langs:
        image = DOCKER_IMAGES[lang]
        dockerfile = dockerfile_for(lang)
        if not dockerfile.exists():
            print(f"no Dockerfile at {dockerfile} - skipping {lang}", file=sys.stderr)
            overall = 1
            continue
        print(f"building {image} from {dockerfile} ...")
        proc = _sp.run(
            ["docker", "build", "-t", image, "-f", str(dockerfile), str(dockerfile.parent)],
        )
        if proc.returncode == 0:
            print(f"  built {image}")
        else:
            overall = proc.returncode
    return overall


def cmd_doctor(args) -> int:
    """
    Preflight checks: which drivers can actually run on this machine, is the
    backend reachable, and are any known environment gotchas present
    (ARCH.md section 8 as executable checks).
    """
    import shutil as _shutil
    import subprocess as _sp
    import urllib.request as _rq
    from .drivers import DRIVERS
    from .drivers.cli_agents import CLI_AGENTS

    ok = True

    def _check(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        mark = "ok " if good else "MISS"
        # Detail is the fix hint - only useful when the check failed.
        print(f"  [{mark}] {label}" + (f" - {detail}" if detail and not good else ""))
        if not good:
            ok = False

    def _check_info(label: str, good: bool, detail: str = "") -> None:
        # Like _check but advisory only - doesn't flip the overall exit code.
        # Used for docker: recommended sandboxing, not a hard requirement
        # (check_command falls back to running on the host without it).
        mark = "ok " if good else "MISS"
        print(f"  [{mark}] {label}" + (f" - {detail}" if detail and not good else ""))

    print("backend:")
    url = args.base_url.rstrip("/") + ("/api/tags" if args.kind == "ollama" else "/v1/models")
    try:
        with _rq.urlopen(url, timeout=4) as resp:
            _check(f"backend {args.base_url}", resp.status == 200)
    except Exception as exc:
        _check(f"backend {args.base_url}", False, f"{type(exc).__name__}: {exc}")

    print("cli drivers:")
    _check("aider", _shutil.which("aider") is not None, "pip install aider-chat")
    for key, spec in CLI_AGENTS.items():
        found = next((b for b in spec["binaries"] if _shutil.which(b)), None)
        _check(key, found is not None,
               found or f"install {spec['label']} ({'/'.join(spec['binaries'])})")

    print("docker (sandboxed check_command execution):")
    docker_bin = _shutil.which("docker")
    docker_running = False
    if docker_bin:
        try:
            docker_running = _sp.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
        except Exception:
            docker_running = False
    _check_info("docker daemon reachable", docker_running,
                "install/start Docker Desktop - recommended so check_command needs no host toolchains")
    if docker_running:
        for lang, image in DOCKER_IMAGES.items():
            built = docker_image_available(image)
            hint = "run `optarena docker build`" if lang == "base" else f"run `optarena docker build --lang {lang}`"
            _check_info(f"{image} image built", built, hint)

    print("ui drivers:")
    harness = REPO_ROOT / "ui-harness"
    _check("node", _shutil.which("node") is not None, "Node 18+ needed for UI drivers")
    _check("ui-harness node_modules", (harness / "node_modules").exists(),
           f"cd {harness} && npm install")
    ext_root = Path.home() / ".vscode" / "extensions"
    for name, prefix in (("cline", "saoudrizwan.claude-dev"),
                         ("roo", "rooveterinaryinc.roo-cline"),
                         ("continue", "continue.continue"),
                         ("kilo", "kilocode.kilo-code")):
        installed = ext_root.exists() and any(
            d.name.lower().startswith(prefix) for d in ext_root.iterdir() if d.is_dir())
        _check(f"{name} extension", installed, f"install {prefix} in VS Code")

    if os.name == "nt":
        print("environment:")
        try:
            out = _sp.run(["tasklist", "/FI", "IMAGENAME eq CodeSetup*"],
                          capture_output=True, text=True, timeout=10).stdout or ""
            stuck = "CodeSetup" in out
            _check("no stuck VS Code updater", not stuck,
                   "kill CodeSetup*.exe - it blocks every VS Code launch (ARCH 8.2)")
        except Exception:
            pass
        _check("ELECTRON_RUN_AS_NODE not leaked",
               "ELECTRON_RUN_AS_NODE" not in os.environ,
               "unset it or run from a plain terminal (drivers scrub it anyway)")

    print()
    print("  doctor result:", "all good" if ok else "some checks failed (see MISS lines)")
    return 0 if ok else 1


def cmd_verify_corpus(args) -> int:
    """
    Corpus self-verification (CI gate): every case's reference_solution must
    PASS the real oracle and every broken/unmodified variant must FAIL it.
    See optarena/verify.py for the schema and rationale.
    """
    from .verify import verify_cases

    names = args.cases.split(",") if args.cases else None
    try:
        cases = load_cases(names, cases_dir=args.cases_dir)
    except ValueError as e:   # SchemaError: a malformed/duplicate case file
        print(f"error: {e}", file=sys.stderr)
        return 2
    cases = filter_cases(cases, language=getattr(args, "language", None),
                         framework=getattr(args, "framework", None))
    violations, checked, skipped = verify_cases(cases)
    print(f"\n  verify-corpus: {checked} variant(s) checked across "
          f"{len(cases) - skipped} case(s); {skipped} case(s) declare no variants")
    if violations:
        print(f"  {len(violations)} VIOLATION(S):")
        for v in violations:
            print(f"    - {v}")
        return 1
    print("  all verified")
    return 0


_SAMPLE_CASE = """{
  "name": "sample_hello",
  "description": "Creates hello.py that prints Hello World (edit me)",
  "prompts": ["Create a file hello.py that prints exactly: Hello World"],
  "setup_files": {},
  "expected_files": [
    {"path_pattern": "hello.py",
     "content_patterns": ["print", "hello world"],
     "not_content_patterns": ["TODO"]}
  ],
  "test_setup_files": {
    "test_hello.py": "import subprocess, sys\\nout = subprocess.check_output([sys.executable, 'hello.py'], text=True)\\nassert out.strip() == 'Hello World', f'unexpected output: {out!r}'\\nprint('PASS')\\n"
  },
  "check_command": "python3 test_hello.py",
  "timeout": 120
}
"""


def cmd_init(args) -> int:
    """Scaffold a project-local cases/ directory with a sample case."""
    target = Path(args.dir)
    target.mkdir(parents=True, exist_ok=True)
    sample = target / "sample_hello.json"
    if sample.exists():
        print(f"  {sample} already exists - not overwriting")
    else:
        sample.write_text(_SAMPLE_CASE, encoding="utf-8")
        print(f"  wrote {sample}")
    print(f"  run with: optarena run --driver ollama-chat --cases-dir {target} --name local-cases")
    return 0


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
    parser.add_argument("--results-dir",
                        help="where runs/comparisons are stored (default: <repo>/results, "
                             "or OPTARENA_RESULTS_DIR); applies to run/compare/regression/list/serve")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run one or more scenarios")
    p_run.add_argument("--scenario", action="append", help="scenario JSON file")
    p_run.add_argument("--driver", choices=DRIVER_NAMES, help="inline scenario: driver")
    p_run.add_argument("--name", help="inline scenario name")
    p_run.add_argument("--kind", default="ollama", choices=["ollama", "openai"])
    p_run.add_argument("--base-url", default=os.environ.get("OPTARENA_BASE_URL", "http://localhost:11434"))
    p_run.add_argument("--model", default=os.environ.get("OPTARENA_MODEL", "llama3.2"))
    p_run.add_argument("--api-key", default="optarena")
    p_run.add_argument("--cases", help="comma-separated case names (default all)")
    p_run.add_argument("--cases-dir", help="load cases from this directory instead of the built-in catalogue")
    p_run.add_argument("--language", help="only run cases tagged with this language (see `optarena list cases`)")
    p_run.add_argument("--framework", help="only run cases tagged with this framework (see `optarena list cases`)")
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

    p_list = sub.add_parser("list", help="list runs / cases / drivers")
    p_list.add_argument("what", nargs="?", default="runs",
                        choices=["runs", "cases", "drivers"])
    p_list.add_argument("--language", help="(with `cases`) only show cases tagged with this language")
    p_list.add_argument("--framework", help="(with `cases`) only show cases tagged with this framework")
    p_list.set_defaults(fn=cmd_list)

    p_serve = sub.add_parser("serve", help="serve the results dashboard")
    p_serve.add_argument("--port", type=int, default=8300)
    p_serve.set_defaults(fn=cmd_serve)

    p_doc = sub.add_parser("doctor", help="preflight checks for every installed driver")
    p_doc.add_argument("--base-url", default=os.environ.get("OPTARENA_BASE_URL", "http://localhost:11434"))
    p_doc.add_argument("--kind", default="ollama", choices=["ollama", "openai"])
    p_doc.set_defaults(fn=cmd_doctor)

    p_init = sub.add_parser("init", help="scaffold a project-local cases/ directory")
    p_init.add_argument("dir", nargs="?", default="cases", help="target directory (default ./cases)")
    p_init.set_defaults(fn=cmd_init)

    p_ver = sub.add_parser("verify-corpus",
                           help="CI gate: reference solutions must pass the oracle, broken variants must fail")
    p_ver.add_argument("--cases", help="comma-separated case names (default all)")
    p_ver.add_argument("--cases-dir", help="load cases from this directory")
    p_ver.add_argument("--language", help="only verify cases tagged with this language")
    p_ver.add_argument("--framework", help="only verify cases tagged with this framework")
    p_ver.set_defaults(fn=cmd_verify_corpus)

    p_docker = sub.add_parser("docker", help="manage the optarena-tester sandbox image(s)")
    p_docker.add_argument("action", choices=["build", "pull"],
                          help="build locally, or pull the published ghcr.io images")
    p_docker.add_argument("--lang", choices=list(DOCKER_IMAGES),
                          help="only this track's image (default: base)")
    p_docker.add_argument("--all", action="store_true", help="every registered image")
    p_docker.set_defaults(fn=cmd_docker)

    args = parser.parse_args(argv)
    if getattr(args, "results_dir", None):
        store.set_results_dir(args.results_dir)
    return args.fn(args)


def main_exit() -> None:
    """Console-script entry point (sys.exit wrapper around main)."""
    sys.exit(main())
