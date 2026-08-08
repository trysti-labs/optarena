"""The case catalogue commands: list/show/init/validate/verify/pack/install,
plus the shared `list` dispatcher (cases/runs/drivers all funnel through it
via `args.what`)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..cases import TagExpressionError, filter_cases, load_cases
from ..store import list_runs


def cmd_list(args) -> int:
    what = args.what
    if what == "cases":
        try:
            cases = filter_cases(
                load_cases(),
                language=getattr(args, "language", None),
                framework=getattr(args, "framework", None),
                tool_service=getattr(args, "tool_service", None),
                tags=getattr(args, "tags", None),
                like=getattr(args, "like", None),
            )
        except TagExpressionError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        for c in cases:
            print(f"  {c['name']:28} {c.get('language', '-'):10} "
                  f"{c.get('framework', '-'):12} {c.get('tool_service', '-'):16} "
                  f"{c.get('description', '')}")
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


def cmd_case_groups(args) -> int:
    """Discovery: how many cases exist per `tool_service`/`domain`/
    `language`, so `--tool-service`/`--tags`/`--language` can be aimed at
    something real instead of guessing exact case names first."""
    from collections import Counter

    cases = load_cases(cases_dir=getattr(args, "cases_dir", None))
    tool_services = Counter(c["tool_service"] for c in cases if c.get("tool_service"))
    languages = Counter(c["language"] for c in cases if c.get("language"))
    domains = Counter(c["domain"] for c in cases if c.get("domain"))
    all_tags = Counter(t for c in cases for t in (c.get("tags") or []))

    def _table(title: str, flag: str, counts: "Counter[str]") -> None:
        if not counts:
            return
        print(f"\n  {title} (use with {flag}):")
        for name, count in sorted(counts.items()):
            print(f"    {name:24} {count:>4}")

    _table("tool_service - tool-use domain", "--tool-service", tool_services)
    _table("language - filesystem domain", "--language", languages)
    _table("domain", "(informational only, no --domain flag today)", domains)
    _table("tags", "--tags", all_tags)
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
    from ..packs import build_pack, sign_pack, write_pack
    try:
        pack = build_pack(args.dir, args.name, args.version)
        if getattr(args, "sign_key", None):
            pack = sign_pack(pack, args.sign_key, signer_id=getattr(args, "signer_id", None))
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out = write_pack(pack, args.out)
    print(f"  packed {pack['case_count']} case(s) -> {out}")
    print(f"  name={pack['name']} version={pack['version']} hash={pack['hash']}")
    if "signature" in pack:
        print(f"  signed as {pack['signature']['signer']!r}")
    return 0


def cmd_cases_install(args) -> int:
    """Install a pack (local file or URL) into the local registry (~/.optarena/packs)."""
    from ..packs import install_pack, load_pack
    try:
        pack = load_pack(args.source, allow_insecure=args.allow_insecure,
                         allow_unsigned=getattr(args, "allow_unsigned", False))
        dest = install_pack(pack, force=args.force)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    # P1-06: "display signature state and signer before installation" -
    # this prints it right after (install already succeeded, since the
    # trust gate in load_pack runs BEFORE any bytes are written for a
    # remote pack; a local pack is never gated, but its state is still
    # worth showing).
    v = pack.get("verification") or {}
    if v.get("trusted"):
        print(f"  signature: trusted ({v['detail']})")
    elif v.get("signed"):
        print(f"  signature: PRESENT BUT NOT TRUSTED - {v['detail']}")
    else:
        print("  signature: none (unsigned pack)")
    print(f"  installed {pack['name']}@{pack['version']} ({pack['case_count']} case(s)) -> {dest}")
    print(f"  run it: optarena run --pack {pack['name']} --driver ollama-chat --name r1")
    return 0


def cmd_cases_trust_publisher(args) -> int:
    """Add a publisher's public key to the local trusted-publisher keyring
    (~/.optarena/trusted_publishers) - the explicit, locally-owned trust
    decision that makes a signed pack's signer actually count as verified."""
    from ..packs import TRUSTED_PUBLISHERS_FILE, add_trusted_publisher
    try:
        add_trusted_publisher(args.identity, args.public_key)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"  trusted {args.identity!r} added to {TRUSTED_PUBLISHERS_FILE}")
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
    tag = f"tool_service={c['tool_service']}" if c.get("tool_service") else f"{c.get('language', '-')}/{c.get('framework', '-')}"
    print(f"\n  {c['name']}  ({tag}, "
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
        # TagExpressionError (a ValueError) from a malformed --tags belongs in
        # the same catch as load_cases' own errors below, so it stays inside
        # the try.
        cases = filter_cases(cases, language=getattr(args, "language", None),
                             framework=getattr(args, "framework", None),
                             tool_service=getattr(args, "tool_service", None),
                             tags=getattr(args, "tags", None),
                             like=getattr(args, "like", None))
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
    sandboxed_drift = False
    if getattr(args, "sandboxed", False):
        sandboxed_drift = _verify_sandboxed_schema_drift(
            cases, debug=getattr(args, "debug", False))
    if sandboxed_drift:
        return 1
    print("  all verified")
    return 0


def _verify_sandboxed_schema_drift(cases: list[dict], *, debug: bool = False) -> bool:
    """Pre-flight, no model call: for every DISTINCT tool_service among the
    matched cases that has a sandboxed-real implementation
    (`SANDBOXED_SERVICES`), start one real sandbox, fetch its live
    `tools/list`, and check every matched case's `tools`/`expected_calls`/
    `forbidden_calls` tool names against it - the same check
    `tool_chat.run_case()` does per-run, surfaced here as a CI-friendly
    batch pass. One sandbox per service, not per case - a service with 30
    matched cases still only pays for one container start. Returns True if
    any drift (or a sandbox that couldn't even start) was found.
    """
    by_service: dict[str, list[dict]] = {}
    for c in cases:
        if c.get("tool_service"):
            by_service.setdefault(c["tool_service"], []).append(c)
    if not by_service:
        print("\n  sandboxed schema-drift check: no tool-use cases matched - skipped")
        return False

    print(f"\n  sandboxed schema-drift check: {sum(len(v) for v in by_service.values())} "
          f"tool-use case(s) across {len(by_service)} service(s)")
    return _drift_by_service(by_service, debug=debug)


def _drift_by_service(by_service: dict[str, list[dict]], *, debug: bool) -> bool:
    """The per-service body of the drift check."""
    from .._cases._mcp_client import MCPProtocolError
    from .._cases._sandboxed_mcp_service import (
        PROBE_SETUP, SANDBOXED_SERVICES, build_sandboxed_service,
        diff_case_asserted_arguments, diff_case_required_arguments,
        diff_case_tools_against_live,
    )
    import shutil
    import tempfile

    found_drift = False
    for service_name, service_cases in sorted(by_service.items()):
        if service_name not in SANDBOXED_SERVICES:
            print(f"    {service_name}: no sandboxed-real implementation yet - skipped "
                  f"({len(service_cases)} case(s))")
            continue
        tmp = Path(tempfile.mkdtemp(prefix="optarena_verify_sandboxed_"))
        try:
            try:
                # Some real servers refuse to start against an empty
                # directory (mcp-server-git needs a real repo, nx-mcp needs
                # a workspace) - prepare the throwaway probe dir first.
                probe = PROBE_SETUP.get(service_name)
                if probe is not None:
                    probe(tmp)
                service = build_sandboxed_service(service_name, tmp)
            except (KeyError, RuntimeError, MCPProtocolError) as exc:
                if debug:
                    raise
                print(f"    {service_name}: could not start sandbox - {exc}")
                found_drift = True
                continue
            try:
                live_schemas = dict(service.tool_schemas)
            finally:
                service.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        live_tools = set(live_schemas)
        arg_drift_seen: set[str] = set()
        blocked = 0
        for c in service_cases:
            problems: list[str] = []
            missing = diff_case_tools_against_live(c, live_tools)
            if missing:
                problems.append(f"references unknown tool(s) {missing}")
            # An assertion naming an argument the real server doesn't accept
            # can NEVER match - a guaranteed failure, same blocking category
            # as a missing tool (unlike required-argument drift below).
            problems += diff_case_asserted_arguments(c, live_schemas)
            if problems:
                found_drift = True
                blocked += 1
                print(f"    BLOCKED {c['name']} ({service_name}):")
                for p in problems:
                    print(f"              {p}")
            # Advisory: names a real mock-vs-reality gap but predicts no
            # failure (see diff_case_required_arguments' own docstring).
            # Deduped per service - the same tool-level fact repeated across
            # 30 cases is noise, not signal.
            for line in diff_case_required_arguments(c, service_name, live_schemas):
                if line not in arg_drift_seen:
                    arg_drift_seen.add(line)
                    print(f"    warning ({service_name}): {line}")
        ready = len(service_cases) - blocked
        summary = f"    {service_name}: {ready}/{len(service_cases)} case(s) sandboxed-ready"
        if blocked:
            summary += f", {blocked} blocked"
        if arg_drift_seen:
            summary += f" ({len(arg_drift_seen)} advisory argument-drift warning(s))"
        print(summary)
    return found_drift
