"""
optarena/cli.py
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
"""

from __future__ import annotations

import argparse
import json
import os
import http.server
import sys
import tempfile
from pathlib import Path

from .cases import (
    DOCKER_IMAGES, container_engine, dockerfile_for, docker_image_available, filter_cases, load_cases,
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
                      model=model, api_key=args.api_key,
                      num_ctx=getattr(args, "num_ctx", None))
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
    # --pack is shorthand for --cases-dir pointing at an installed pack.
    if getattr(args, "pack", None):
        from .packs import resolve_pack
        try:
            args.cases_dir = str(resolve_pack(args.pack))
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
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
                                keep_workspace=args.keep_workspace,
                                security_scan=getattr(args, "security_scan", False))
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
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
        print(f"  -> {s['passed']}/{s['cases']} passed ({s['pass_rate']:.0%}{ci_str}{clean}, "
              f"mean {s['mean_duration_s']}s) - saved {path.name}")
        eff = []
        if s.get("tokens_per_pass"):
            eff.append(f"{s['tokens_per_pass']} tokens/pass")
        if s.get("steps_per_pass"):
            eff.append(f"{s['steps_per_pass']} steps/pass")
        if s.get("security_findings"):
            eff.append(f"{s['security_findings']} security finding(s)")
        if eff:
            print(f"     efficiency: {', '.join(eff)}")
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
        engine = container_engine()
        print(f"building {image} from {dockerfile} (via {engine}) ...")
        proc = _sp.run(
            [engine, "build", "-t", image, "-f", str(dockerfile), str(dockerfile.parent)],
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
        # Used for docker: strongly recommended (without it, check_command
        # REFUSES to run unless host execution is explicitly opted into via
        # OPTARENA_NO_DOCKER=1 or OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1), but a
        # doctor run on a docker-less machine shouldn't read as broken.
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

    engine = container_engine()
    print(f"{engine} (sandboxed check_command execution):")
    engine_bin = _shutil.which(engine)
    docker_running = False
    if engine_bin:
        try:
            docker_running = _sp.run([engine, "info"], capture_output=True, timeout=10).returncode == 0
        except Exception:
            docker_running = False
    _check_info(f"{engine} daemon reachable", docker_running,
                "install/start Docker or Podman - recommended so check_command needs no host toolchains")
    if docker_running:
        for lang, image in DOCKER_IMAGES.items():
            built = docker_image_available(image)
            hint = "run `optarena docker build`" if lang == "base" else f"run `optarena docker build --lang {lang}`"
            _check_info(f"{image} image built", built, hint)

    print("sdk drivers (optional - each needs its own pip extra):")
    import importlib.util as _ilu
    for driver_key, import_name, extra in (
        ("crewai", "crewai", "crewai"),
        ("openai-agents", "agents", "openai-agents"),
        ("smolagents", "smolagents", "smolagents"),
        ("langgraph", "langgraph", "langgraph"),
        ("autogen", "autogen_agentchat", "autogen"),
        ("semantic-kernel", "semantic_kernel", "semantic-kernel"),
    ):
        _check_info(driver_key, _ilu.find_spec(import_name) is not None,
                    f"pip install optarena[{extra}]")

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


def cmd_cases_pack(args) -> int:
    """Bundle a cases directory into a single shareable, versioned pack file."""
    from .packs import build_pack, write_pack
    try:
        pack = build_pack(args.dir, args.name, args.version)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out = write_pack(pack, args.out)
    print(f"  packed {pack['case_count']} case(s) -> {out}")
    print(f"  name={pack['name']} version={pack['version']} hash={pack['hash']}")
    return 0


def cmd_cases_install(args) -> int:
    """Install a pack (local file or URL) into the local registry (~/.optarena/packs)."""
    from .packs import load_pack, install_pack
    try:
        pack = load_pack(args.source)
        dest = install_pack(pack, force=args.force)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"  installed {pack['name']}@{pack['version']} ({pack['case_count']} case(s)) -> {dest}")
    print(f"  run it: optarena run --pack {pack['name']} --driver ollama-chat --name r1")
    return 0


def cmd_cases_packs(args) -> int:
    """List installed case packs (discovery)."""
    from .packs import list_installed, PACKS_DIR
    packs = list_installed()
    if not packs:
        print(f"  no packs installed (registry: {PACKS_DIR})")
        print("  install one: optarena cases install <file-or-url.optpack.json>")
        return 0
    print(f"  {'name@version':32} {'cases':>6}  hash")
    print(f"  {'-'*32} {'-'*6}  {'-'*20}")
    for m in packs:
        print(f"  {m.get('name','?')+'@'+str(m.get('version','?')):32} "
              f"{m.get('case_count','?'):>6}  {str(m.get('hash',''))[:23]}")
    return 0


def cmd_case_show(args) -> int:
    """Print one case in full: prompts, oracle, metadata - so nobody has to
    hunt down and read the raw JSON to see what a PASS actually requires."""
    try:
        cases = load_cases([args.name], cases_dir=args.cases_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    c = cases[0]
    print(f"\n  {c['name']}  ({c.get('language', '-')}/{c.get('framework', '-')}, "
          f"task_type={c.get('task_type', '-')}, difficulty={c.get('difficulty', '-')})")
    print(f"  {c.get('description', '')}\n")
    for i, prompt in enumerate(c.get("prompts", []), 1):
        print(f"  prompt {i}: {prompt}")
    if c.get("setup_repo"):
        print(f"\n  setup_repo: {c['setup_repo']}" + (" (+ git_init)" if c.get("git_init") else ""))
    if c.get("setup_files"):
        print(f"  setup_files: {', '.join(c['setup_files'])}")
    print(f"\n  expected_files: {json.dumps(c.get('expected_files', []), indent=2)}")
    if c.get("test_setup_files"):
        print(f"  hidden test files: {', '.join(c['test_setup_files'])}")
    if c.get("check_command"):
        print(f"  check_command: {c['check_command']}"
              + (f"  (timeout {c['check_command_timeout']}s)" if c.get("check_command_timeout") else ""))
        print(f"  docker_image: {c.get('docker_image') or 'default'}")
    n_broken = len(c.get("broken_solutions") or [])
    print(f"  self-verification: reference_solution {'yes' if c.get('reference_solution') else 'NO'}, "
          f"{n_broken} broken variant(s)")
    return 0


def cmd_validate(args) -> int:
    """Schema-validate case files (a directory or the built-in catalogue)
    without running anything - the fail-fast check `run`/`verify` do
    implicitly, exposed standalone for case authors and CI."""
    try:
        cases = load_cases(cases_dir=args.cases_dir)
    except ValueError as e:            # SchemaError carries file + key context
        print(f"invalid: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    where = args.cases_dir or "built-in catalogue"
    print(f"  ok: {len(cases)} case(s) in {where} are structurally valid")
    return 0


def cmd_run_show(args) -> int:
    """Summary + per-case table for one saved run."""
    try:
        run = load_run(args.run_ref)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    s = run.get("summary", {})
    sc = run.get("scenario", {})
    b = sc.get("backend", {})
    print(f"\n  {run['run_id']}")
    print(f"  driver={sc.get('driver')}  backend={b.get('model')}@{b.get('base_url')}"
          f"  started={run.get('started_at')}")
    man = run.get("manifest") or {}
    if man:
        print(f"  manifest: {man.get('case_count')} case(s) hash {man.get('case_set_hash')}, "
              f"oracle v{man.get('oracle_version')}, trials={man.get('trials')}")
    print(f"  {s.get('passed', '?')}/{s.get('cases', '?')} passed "
          f"(mean {s.get('mean_duration_s', '?')}s"
          + (f", ${s['total_cost_usd']:.2f}" if s.get("total_cost_usd") is not None else "")
          + ")\n")
    for c in run.get("cases", []):
        status = "PASS" if c.get("passed") else ("ERROR" if c.get("error") else "FAIL")
        detail = "" if c.get("passed") else \
            f"  - {c.get('error') or '; '.join((c.get('failures') or [])[:1])}"
        print(f"  {status:5} {c['name']:40} {c.get('duration_s', 0):6.1f}s{detail}")
    return 0


def cmd_scan(args) -> int:
    """Standalone static security scan of a directory (the same rules the
    --security-scan run flag applies to an agent's changed files)."""
    from .security import scan_workspace

    root = Path(args.dir)
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    files = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
    res = scan_workspace(files, root)
    if not res["total"]:
        print(f"  no findings in {root} ({len(files)} file(s) scanned)")
        return 0
    c = res["counts"]
    print(f"  {res['total']} finding(s) in {root}: "
          f"{c.get('error', 0)} error, {c.get('warning', 0)} warning, {c.get('note', 0)} note")
    for f in res["findings"]:
        print(f"    [{f['level']:7}] {f['title']}")
        print(f"              {f['file']}:{f['line']}  {f['snippet']}")
    # Exit non-zero when there's an error-level finding, so it's usable as a gate.
    return 1 if c.get("error", 0) else 0


def cmd_report(args) -> int:
    """Emit CI-native report artifacts (JUnit XML / self-contained HTML / SARIF)
    from a saved run, so an OptArena run drops into GitHub Actions/GitLab CI the
    same way a normal test suite does."""
    from . import report as _report

    try:
        run = load_run(args.run_ref)
    except (FileNotFoundError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    formats = ["junit", "html", "sarif"] if args.format == "all" else [args.format]
    renderers = {"junit": (_report.to_junit_xml, "junit.xml"),
                 "html": (_report.to_html, "report.html"),
                 "sarif": (_report.to_sarif, "results.sarif")}

    # --out: a file path when one format is requested, else a directory.
    out = Path(args.out) if args.out else (store.RESULTS_DIR / "reports" / run["run_id"])
    single = len(formats) == 1 and args.out and not str(args.out).endswith("/") and not Path(args.out).is_dir()
    if not single:
        out.mkdir(parents=True, exist_ok=True)

    written = []
    for fmt in formats:
        fn, default_name = renderers[fmt]
        content = fn(run)
        dest = out if single else (out / default_name)
        dest.write_text(content, encoding="utf-8")
        written.append(dest)
    for p in written:
        print(f"  wrote {p}")
    return 0


def cmd_sandbox_status(args) -> int:
    """Scriptable view of what `doctor`'s container-engine section reports:
    daemon reachability and which sandbox images are built locally."""
    import subprocess as _sp

    engine = container_engine()
    try:
        running = _sp.run([engine, "info"], capture_output=True, timeout=10).returncode == 0
    except (OSError, _sp.TimeoutExpired):
        running = False
    print(f"  {engine} daemon: {'reachable' if running else 'NOT reachable'}")
    if not running:
        print("  (install/start Docker or Podman - without it check_command refuses to run; "
              "see OPTARENA_ALLOW_UNSAFE_HOST_EXEC in the docs)")
        return 1
    missing = 0
    for lang, image in DOCKER_IMAGES.items():
        built = docker_image_available(image)
        if not built:
            missing += 1
        hint = "" if built else \
            f"  <- optarena sandbox build --lang {lang}" if lang != "base" else "  <- optarena sandbox build"
        print(f"  [{'ok ' if built else 'MISS'}] {image}{hint}")
    return 0 if missing == 0 else 1


def _rewrite_legacy_argv(argv: list[str] | None) -> list[str] | None:
    """
    Translate the pre-grouping command spellings to the grouped ones so both
    keep working with a single code path and a clean `--help`:

        list [runs|cases|drivers]  ->  <that-noun> list
        init [dir]                 ->  cases init [dir]
        verify-corpus ...          ->  cases verify ...
        docker build|pull ...      ->  sandbox build|pull ...

    Only the command token is rewritten; all flags pass through untouched.
    The single global option that takes a value (`--results-dir X`) is skipped
    over when locating the command token.
    """
    import sys as _sys
    if argv is None:
        argv = _sys.argv[1:]
    argv = list(argv)

    # Find the command token: first arg that isn't the global option or its value.
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("--results-dir",):
            i += 2
            continue
        if tok.startswith("--results-dir="):
            i += 1
            continue
        if tok in ("-h", "--help"):
            return argv
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
    pc_list.set_defaults(fn=cmd_list, what="cases")
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
    pc_ver.set_defaults(fn=cmd_verify_corpus)
    pc_pack = cases_sub.add_parser("pack", help="bundle a cases dir into a shareable, versioned pack file")
    pc_pack.add_argument("dir", help="directory of case JSONs to pack")
    pc_pack.add_argument("--name", required=True, help="pack name (letters/digits/. _ -)")
    pc_pack.add_argument("--version", default="0.1.0", help="pack version (default 0.1.0)")
    pc_pack.add_argument("--out", help="output file (default <name>-<version>.optpack.json)")
    pc_pack.set_defaults(fn=cmd_cases_pack)
    pc_inst = cases_sub.add_parser("install", help="install a pack (local file or URL) into the registry")
    pc_inst.add_argument("source", help="path or http(s) URL to a .optpack.json")
    pc_inst.add_argument("--force", action="store_true", help="overwrite an installed pack of the same name@version")
    pc_inst.set_defaults(fn=cmd_cases_install)
    cases_sub.add_parser("packs", help="list installed case packs").set_defaults(fn=cmd_cases_packs)

    p_runs = sub.add_parser("runs", help="saved runs: list/show")
    runs_sub = p_runs.add_subparsers(dest="runs_command", required=True)
    runs_sub.add_parser("list", help="saved runs, newest first").set_defaults(fn=cmd_list, what="runs")
    pr_show = runs_sub.add_parser("show", help="summary + per-case table for one run")
    pr_show.add_argument("run_ref", help="run id, filename, path, or unique substring")
    pr_show.set_defaults(fn=cmd_run_show)

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
    p_report.add_argument("--out", help="output file (single format) or directory "
                                        "(default: <results>/reports/<run_id>/)")
    p_report.set_defaults(fn=cmd_report)

    p_serve = sub.add_parser("serve", help="serve the results dashboard")
    p_serve.add_argument("--port", type=int, default=8300)
    p_serve.set_defaults(fn=cmd_serve)

    p_doc = sub.add_parser("doctor", help="preflight checks for every installed driver")
    p_doc.add_argument("--base-url", default=os.environ.get("OPTARENA_BASE_URL", "http://localhost:11434"))
    p_doc.add_argument("--kind", default="ollama", choices=["ollama", "openai"])
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
