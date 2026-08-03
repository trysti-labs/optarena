"""The case catalogue commands: list/show/init/validate/verify/pack/install,
plus the shared `list` dispatcher (cases/runs/drivers all funnel through it
via `args.what`)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..cases import filter_cases, load_cases
from ..store import list_runs


def cmd_list(args) -> int:
    what = args.what
    if what == "cases":
        language = getattr(args, "language", None)
        framework = getattr(args, "framework", None)
        for c in filter_cases(load_cases(), language=language, framework=framework):
            print(f"  {c['name']:28} {c.get('language', '-'):10} "
                  f"{c.get('framework', '-'):12} {c.get('description', '')}")
    elif what == "drivers":
        from ..drivers import DRIVERS
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
    from ..packs import build_pack, write_pack
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
    from ..packs import install_pack, load_pack
    try:
        pack = load_pack(args.source, allow_insecure=args.allow_insecure)
        dest = install_pack(pack, force=args.force)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"  installed {pack['name']}@{pack['version']} ({pack['case_count']} case(s)) -> {dest}")
    print(f"  run it: optarena run --pack {pack['name']} --driver ollama-chat --name r1")
    return 0


def cmd_cases_packs(args) -> int:
    """List installed case packs (discovery)."""
    from ..packs import PACKS_DIR, list_installed
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
        print(f"  image: {c.get('image') or 'default'}")
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


def cmd_verify_corpus(args) -> int:
    """
    Corpus self-verification (CI gate): every case's reference_solution must
    PASS the real oracle and every broken/unmodified variant must FAIL it.
    See optarena/verify.py for the schema and rationale.
    """
    from ..verify import cases_without_failing_variant, verify_cases

    names = args.cases.split(",") if args.cases else None
    try:
        cases = load_cases(names, cases_dir=args.cases_dir)
    except (ValueError, OSError) as e:
        # A-34: OSError as well as ValueError. ValueError covers SchemaError (a
        # malformed/duplicate case file); a typo in `--cases` raises
        # FileNotFoundError from load_cases, which used to escape as a raw
        # traceback - the everyday-failure-should-not-traceback rule (F-04)
        # that every other command already follows.
        if getattr(args, "debug", False):
            raise
        print(f"error: {e}", file=sys.stderr)
        return 2
    cases = filter_cases(cases, language=getattr(args, "language", None),
                         framework=getattr(args, "framework", None))
    violations, checked, skipped = verify_cases(cases)
    print(f"\n  verify-corpus: {checked} variant(s) checked across "
          f"{len(cases) - skipped} case(s); {skipped} case(s) declare no variants")
    # A-31: a case with nothing that must FAIL proves only that its oracle can
    # pass - it cannot catch an oracle that accepts everything, which is the
    # exact failure class this command exists for. Always reported; fatal
    # under --strict.
    weak = cases_without_failing_variant(cases)
    if weak:
        label = "VIOLATION(S)" if getattr(args, "strict", False) else "warning"
        print(f"  {label}: {len(weak)} case(s) declare no failing variant "
              f"(no broken_solutions, and not a task_type that gets the implicit "
              f"'unmodified' check) - their oracle is never proven to discriminate:")
        for name in weak[:20]:
            print(f"    - {name}")
        if len(weak) > 20:
            print(f"    ... and {len(weak) - 20} more")
    if violations:
        print(f"  {len(violations)} VIOLATION(S):")
        for v in violations:
            print(f"    - {v}")
        return 1
    if weak and getattr(args, "strict", False):
        return 1
    print("  all verified")
    return 0
