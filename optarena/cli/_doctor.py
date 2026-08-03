"""`optarena doctor`: preflight checks for every driver, the container
engine, and the source-checkout-only assets (ARCH.md section 8 as
executable checks)."""

from __future__ import annotations

import json

from .. import __version__
from ..cases import DOCKER_IMAGES, REPOS_DIR, container_engine, docker_image_available, load_cases


def cmd_doctor(args) -> int:
    """
    Preflight checks: which drivers can actually run on this machine, is the
    backend reachable, and are any known environment gotchas present
    (ARCH.md section 8 as executable checks).
    """
    import shutil as _shutil
    import subprocess as _sp
    import urllib.request as _rq

    from ..drivers.cli_agents import CLI_AGENTS

    ok = True
    # A-28: --json makes doctor scriptable. Without it an automation wrapper
    # was back to scraping human-formatted text - the exact problem F-18
    # solved for `run`.
    as_json = getattr(args, "json", False)
    report: dict = {"checks": [], "sections": {}}
    section = {"name": None}

    def _record(label: str, good: bool, detail: str, required: bool) -> None:
        report["checks"].append({"section": section["name"], "label": label,
                                 "ok": bool(good), "detail": detail, "required": required})

    def _print(*args_, **kwargs) -> None:
        if not as_json:
            print(*args_, **kwargs)

    def _section(name: str) -> None:
        section["name"] = name
        _print(f"{name}:")

    def _check(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        mark = "ok " if good else "MISS"
        # Detail is the fix hint - only useful when the check failed.
        _print(f"  [{mark}] {label}" + (f" - {detail}" if detail and not good else ""))
        _record(label, good, detail, required=True)
        if not good:
            ok = False

    def _check_info(label: str, good: bool, detail: str = "") -> None:
        # Like _check but advisory only - doesn't flip the overall exit code.
        # Used for the container engine: strongly recommended (without it,
        # check_command REFUSES to run unless host execution is explicitly
        # opted into via OPTARENA_DISABLE_SANDBOX=1 or
        # OPTARENA_ALLOW_UNSAFE_HOST_EXEC=1), but a doctor run on a machine
        # with neither Docker nor Podman shouldn't read as broken.
        mark = "ok " if good else "MISS"
        _print(f"  [{mark}] {label}" + (f" - {detail}" if detail and not good else ""))
        _record(label, good, detail, required=False)

    _section("backend")
    url = args.base_url.rstrip("/") + ("/api/tags" if args.kind == "ollama" else "/v1/models")
    try:
        with _rq.urlopen(url, timeout=4) as resp:
            _check(f"backend {args.base_url}", resp.status == 200)
    except Exception as exc:
        _check(f"backend {args.base_url}", False, f"{type(exc).__name__}: {exc}")

    # P2-06: "record tested driver/SDK versions" - `DRIVERS[key]["tested_with"]`
    # is the version this driver was last confirmed working against via a
    # real invocation; flagging when the INSTALLED version has since moved
    # makes "this hasn't been re-verified since it changed" visible instead
    # of silently assumed still fine.
    from ..drivers import DRIVERS as _DRIVERS_REGISTRY
    from ..drivers import get_driver_version as _get_driver_version

    def _version_note(key: str) -> str:
        v = _get_driver_version(key)
        if not v:
            return ""
        tested = _DRIVERS_REGISTRY.get(key, {}).get("tested_with")
        if tested and v != tested:
            return f"version: {v}  (last tested with {tested!r} - not re-verified against this version)"
        return f"version: {v}"

    def _print_version_note(key: str) -> None:
        # P2-03: `_print` alone is a no-op under `--json` (by design, so a
        # scripted caller gets clean JSON on stdout) - which meant a version
        # note NEVER reached `--json` output at all, even though it's the
        # exact signal a CI job would want to gate on. Recorded into
        # `report["checks"]` here too (as its own advisory row) so both
        # output modes carry the same information, not just the human one.
        note = _version_note(key)
        if note:
            _print(f"         {note}")
            _record(f"{key} version", "not re-verified" not in note, note, required=False)

    _section("cli drivers")
    aider_found = _shutil.which("aider") is not None
    _check("aider", aider_found, "pip install aider-chat")
    if aider_found:
        _print_version_note("aider")
    for key, spec in CLI_AGENTS.items():
        found = next((b for b in spec["binaries"] if _shutil.which(b)), None)
        _check(key, found is not None,
               found or f"install {spec['label']} ({'/'.join(spec['binaries'])})")
        if found:
            _print_version_note(key)

    engine = container_engine()
    _section(f"{engine} (sandboxed check_command execution)")
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
            hint = "run `optarena sandbox build`" if lang == "base" else f"run `optarena sandbox build --lang {lang}`"
            _check_info(f"{image} image built", built, hint)

    _section("sdk drivers (optional - each needs its own pip extra)")
    import importlib.util as _ilu
    for driver_key, import_name, extra in (
        ("crewai", "crewai", "crewai"),
        ("openai-agents", "agents", "openai-agents"),
        ("smolagents", "smolagents", "smolagents"),
        ("langgraph", "langgraph", "langgraph"),
        ("autogen", "autogen_agentchat", "autogen"),
        ("semantic-kernel", "semantic_kernel", "semantic-kernel"),
    ):
        installed = _ilu.find_spec(import_name) is not None
        _check_info(driver_key, installed, f"pip install optarena[{extra}]")
        if installed:
            _print_version_note(driver_key)

    # A-29: the three directories that are NOT packaged into a wheel
    # (README's source-checkout note). Installed from a wheel they're simply
    # absent, and the features that need them fail at use time with no earlier
    # signal - `sandbox build` has no Dockerfiles, `serve` has no dashboard,
    # and every setup_repo case errors. Report it here instead.
    #
    # Deferred import, not the module-level REPO_ROOT: looked up through the
    # `cli` package facade at call time, same reasoning as cmd_serve in
    # _serve.py - REPO_ROOT lived directly in cli.py before the P3-01 split,
    # and a test/caller patching `cli.REPO_ROOT` should still be honored here.
    from . import REPO_ROOT
    _section("source-checkout assets (not bundled into a wheel)")
    repo_cases = sum(1 for c in load_cases() if c.get("setup_repo"))
    _check_info("dashboard/", (REPO_ROOT / "dashboard" / "index.html").is_file(),
                "needed by `optarena serve` - keep the git checkout")
    _check_info("docker/", (REPO_ROOT / "docker" / "Dockerfile").is_file(),
                "needed by `optarena sandbox build` (pulling published images still works)")
    _check_info(f"repos/ ({repo_cases} case(s) need it)", REPOS_DIR.is_dir(),
                "needed by setup_repo cases - they error without it")

    if as_json:
        report["ok"] = ok
        report["optarena_version"] = __version__
        print(json.dumps(report, indent=2))
    else:
        print()
        print("  doctor result:", "all good" if ok else "some checks failed (see MISS lines)")
    return 0 if ok else 1
