"""
optarena/_cases/_sandboxed_mcp_service.py
───────────────────────────────────────
Sandboxed-real tool-use execution: runs the ACTUAL open-source reference MCP
server binary inside a disposable, hardened container (reusing
`_sandbox.DockerSandbox`), instead of the hand-written in-process
approximations in `_mock_service.py`. Same safety guarantee as a mock
(isolated, no real credentials, no third-party blast radius) - the real
server's real behavior instead of our approximation of it.

`SandboxedMCPService` satisfies the exact same contract as `MockService`
(`dispatch`/`call_log`/`seed`/`summary`), so `tool_chat.py`'s driver loop and
`_tool_evaluate.py`'s oracle work against it completely unmodified - see
`build_sandboxed_service()` below for the one new branch point
(`tool_chat.run_case`) that decides which kind of service a case gets.

One dedicated container per case, not shared with the check_command sandbox
pool: a sandboxed tool-use case is a long-lived attached process (an MCP
server you hold a live stdio session with), a fundamentally different
lifecycle than check_command's one-shot exec-and-capture calls, and mixing
the two pools would let a hung MCP server outlive/collide with an unrelated
case's compile-and-test run.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from ._constants import DOCKER_IMAGES
from ._mcp_client import MCPProtocolError, MCPStdioClient
from ._mcp_schema import mcp_tools_to_openai_schemas
from ._mock_service import MOCK_SERVICES, MockService
from ._sandbox import DockerSandbox, container_engine, resolve_image

#: Registry of every sandboxable tool_service, keyed by the same name used
#: in `MOCK_SERVICES` (a case's `tool_service` field doesn't change between
#: modes - see `--tool-service-mode`). `image` is a `DOCKER_IMAGES` short key
#: (resolved via `resolve_image`, same as a case's own `image` field);
#: `command` is the real server's stdio launch command, run with cwd already
#: set to the case's own workspace directory by `exec_attached` (so "."
#: names the case's own directory as the server's allowed root).
SANDBOXED_SERVICES: dict[str, dict] = {
    "filesystem": {
        "image": DOCKER_IMAGES["mcp-filesystem"],
        "command": ["mcp-server-filesystem", "."],
    },
    "git_repo": {
        "image": DOCKER_IMAGES["mcp-git-repo"],
        "command": ["mcp-server-git", "--repository", "."],
    },
    "code_intel": {
        "image": DOCKER_IMAGES["mcp-code-intel"],
        "command": ["mcp-language-server", "--workspace", ".", "--lsp", "pyright-langserver", "--", "--stdio"],
    },
    "build_tools": {
        "image": DOCKER_IMAGES["mcp-build-tools"],
        # --no-minimal: nx-mcp defaults --minimal=true, which HIDES exactly
        # the workspace-analysis tools (nx_workspace, nx_project_details,
        # nx_generators, ...) most cases exercise - confirmed via its own
        # --help output, not assumed.
        "command": ["nx-mcp", ".", "--transport", "stdio", "--disableTelemetry", "--no-minimal"],
    },
    "database": {
        "image": DOCKER_IMAGES["mcp-database"],
        # A wrapper script, not the bare postgres-mcp binary: unlike every
        # other sandboxed service, this one needs a real backing daemon
        # (Postgres) started and ready BEFORE the MCP server attaches to
        # it - see docker/mcp-database/start-postgres-mcp.sh.
        "command": ["/opt/start-postgres-mcp.sh"],
        # Real Postgres refuses outright to run as uid 0 (no flag overrides
        # this) - the wrapper script must `su postgres` to start it, which
        # needs CAP_SETUID/CAP_SETGID, neither in the default hardened cap
        # set (confirmed empirically: "su: cannot set groups: Operation not
        # permitted" without this). Scoped to ONLY this service's own
        # dedicated container via DockerSandbox's extra_run_args - every
        # other sandboxed image's hardening is unchanged.
        "extra_run_args": ["--cap-add", "SETUID", "--cap-add", "SETGID"],
    },
    "observability": {
        "image": DOCKER_IMAGES["mcp-observability"],
        # A wrapper script, same reasoning as database's: needs a real
        # backing daemon (Grafana) started and ready before the MCP server
        # attaches - see docker/mcp-observability/start-grafana-mcp.sh.
        # No extra_run_args: unlike Postgres, Grafana runs fine as the
        # container's default root user (confirmed empirically), no
        # su/privilege-drop needed.
        "command": ["/opt/start-grafana-mcp.sh"],
    },
    "package_registry": {
        "image": DOCKER_IMAGES["mcp-package-registry"],
        "command": ["package-registry-mcp"],
        # Real network access, not "none" - see the long comment in
        # docker/mcp-package-registry/Dockerfile for why this is a
        # deliberate, narrowly-scoped exception (read-only public package
        # metadata lookups only, no publish/mutate tool exists).
        "network": "bridge",
    },
    "cloud_infra": {
        "image": DOCKER_IMAGES["mcp-cloud-infra"],
        # No LocalStack, no AWS credentials: confirmed empirically that the
        # real server's tool surface (see the docker/mcp-cloud-infra
        # Dockerfile comment) is IaC documentation/validation, not live
        # resource management - --network none (the default) is sufficient.
        "command": ["awslabs.aws-iac-mcp-server"],
    },
    "docker": {
        "image": DOCKER_IMAGES["mcp-docker"],
        "command": ["mcp-server-docker"],
        # Docker-outside-of-docker, not a nested/privileged inner daemon:
        # bind-mounts the HOST's real docker.sock so the real MCP server
        # talks to the actual host Docker daemon directly. No --privileged/
        # SYS_ADMIN needed - a unix socket mount needs no special
        # capabilities. Real, explicit trust boundary, not a hidden one:
        # this means the sandboxed server can control the HOST's real
        # Docker daemon (create/remove real containers/images/networks
        # visible outside this sandbox) - deliberately chosen over full DinD
        # after user sign-off (see the implementation plan's Tier 3 note).
        # --network none is unaffected: Docker's Python SDK talks over the
        # unix socket, not TCP/IP, so network isolation stays intact.
        "extra_run_args": ["-v", "/var/run/docker.sock:/var/run/docker.sock"],
        # S-3 (tool-call audit): with the socket mounted, the effective
        # principal is the MODEL UNDER TEST - its tool calls flow unfiltered
        # to the real host daemon (create_container with volume binds =
        # host root). Requires OPTARENA_ALLOW_HOST_DOCKER=1, enforced by
        # build_sandboxed_service - the same explicit-opt-in pattern as
        # OPTARENA_ALLOW_UNSAFE_HOST_EXEC.
        "host_docker_socket": True,
    },
    "kubernetes": {
        "image": DOCKER_IMAGES["mcp-kubernetes"],
        # A wrapper script: creates a real, throwaway `kind` cluster (nodes
        # as sibling containers on the HOST's real Docker daemon - same
        # docker-outside-of-docker trust boundary as "docker" above, same
        # host-socket mount) before the real MCP server attaches, and tears
        # the cluster down on exit - see docker/mcp-kubernetes/start-k8s-mcp.sh.
        "command": ["/opt/start-k8s-mcp.sh"],
        "extra_run_args": ["-v", "/var/run/docker.sock:/var/run/docker.sock"],
        # "bridge", not "none": kind's own kubeconfig is host-loopback-
        # relative, unreachable from a --network none sandbox (confirmed
        # empirically), so the wrapper script joins this container to
        # kind's own docker network at runtime - but Docker flatly refuses
        # `network connect` on a container started in "none" mode at all
        # ("cannot be connected to multiple networks with one of the
        # networks in private (none) mode"), confirmed empirically too.
        "network": "bridge",
        # Every other service's real server starts in low single-digit
        # seconds (or instantly) - the client's default 30s handshake
        # timeout was tuned against those. `kind create cluster` alone
        # timed 36s in a warm-cache manual run (confirmed empirically) and
        # can run longer cold, all BEFORE the MCP server has even launched
        # to start responding to `initialize` - the default timeout doesn't
        # fit this one service's real startup cost.
        "startup_timeout": 180.0,
        # Same host-socket trust boundary as "docker" above (S-3) - and the
        # kind node is itself a PRIVILEGED sibling container, so this one is
        # strictly the more powerful of the two.
        "host_docker_socket": True,
        # B-2 (tool-call audit): the kind cluster's node container lives on
        # the HOST daemon and its in-container cleanup trap dies with the
        # sandbox on any non-graceful stop. `kind_cluster` makes teardown
        # two-layered: close() first waits `shutdown_grace` seconds for the
        # trap's own `kind delete cluster` (the fast, clean path - observed
        # ~2-3s live), then reaps the node container host-side by its
        # DERIVED name - derivable only because the cluster is named after
        # the sandbox itself, via the OPTARENA_SANDBOX_NAME env
        # exec_attached passes (see start-k8s-mcp.sh).
        "kind_cluster": True,
        "shutdown_grace": 15.0,
    },
}


def _probe_setup_git_repo(root: Path) -> None:
    """`mcp-server-git --repository .` REFUSES to start against a
    non-repository ("`.` is not a valid Git repository", found live via the
    stderr tail) - so the drift pre-flight's throwaway probe directory has
    to be a real repo before the server launches. A real run never needs
    this: its workspace is a real case workspace whose `tool_service_seed`
    already establishes history."""
    for args in (["init", "-q"],
                 ["config", "user.email", "probe@optarena.local"],
                 ["config", "user.name", "optarena-probe"]):
        subprocess.run(["git", *args], cwd=root, capture_output=True, timeout=30)


def _probe_setup_build_tools(root: Path) -> None:
    """nx-mcp introspects a workspace through the real `nx` CLI, which needs
    at least an nx.json + package.json to consider the directory a
    workspace at all."""
    (root / "nx.json").write_text('{"targetDefaults": {}}', encoding="utf-8", newline="")
    (root / "package.json").write_text(
        '{"name": "optarena-probe", "private": true, "version": "0.0.0"}',
        encoding="utf-8", newline="")


#: Optional per-service preparation of the THROWAWAY probe directory the
#: `cases verify --sandboxed` drift check uses (see
#: `cli/_cases_cmds.py::_verify_sandboxed_schema_drift`). Only services
#: whose real server refuses to start against an empty directory need an
#: entry; a real run never uses these (it has a real case workspace).
PROBE_SETUP: dict[str, Callable[[Path], None]] = {
    "git_repo": _probe_setup_git_repo,
    "build_tools": _probe_setup_build_tools,
}


def get_sandboxed_service_factory(name: str) -> dict:
    """The sandboxed-real implementation spec for `name` - which real MCP
    server binary runs, in which image, with which operational settings
    (hardening exceptions, network mode, timeouts, cleanup hooks)."""
    entry = SANDBOXED_SERVICES.get(name)
    if entry is None:
        raise KeyError(
            f"No sandboxed-real implementation for tool_service {name!r}. "
            f"Available: {', '.join(sorted(SANDBOXED_SERVICES)) or '(none yet)'}"
        )
    return entry


def impl_identity(spec: dict) -> str:
    """How one implementation is IDENTIFIED in a result - its `label` when
    the registry entry has one, else the image reference plus the launch
    command, which is what actually distinguishes two servers. Recorded as
    diagnostic evidence ("which server did this case actually talk to"),
    never a comparability gate."""
    if spec.get("label"):
        return str(spec["label"])
    return f"{spec['image']} [{' '.join(spec['command'])}]"


def diff_case_tools_against_live(case: dict, live_tool_names: set[str]) -> list[str]:
    """Tool names this case references (`tools`, plus every `expected_calls`/
    `forbidden_calls` entry's `tool`) that the real server's own `tools/list`
    doesn't have - schema drift between the hand-written mock this case was
    likely authored against and the actual current server. An empty result
    doesn't prove the case is semantically valid against the real server
    (argument shapes/paths can still differ - see SandboxedMCPService.seed's
    docstring), only that every tool NAME it names still exists."""
    referenced: set[str] = set(case.get("tools") or [])
    referenced |= {c["tool"] for c in (case.get("expected_calls") or []) if "tool" in c}
    referenced |= {c["tool"] for c in (case.get("forbidden_calls") or []) if "tool" in c}
    return sorted(referenced - live_tool_names)


def resolve_tool_service_mode(case_mode: str | None, user_mode: str | None) -> str:
    """The ONE place execution-mode precedence lives - used identically by
    `tool_chat.run_case` (what actually happens) and `runner._manifest`
    (what the run record claims happened), so the two can never disagree.

    Sandboxed execution is capability-increasing (it launches containers;
    for docker/kubernetes it reaches the host Docker daemon), so it is
    granted only by the USER (CLI flag / scenario field), never by case
    content: a case may opt DOWN to "mock" (it knows it doesn't work
    sandboxed), but a case asking for "sandboxed" is honored only when the
    user already enabled sandboxed execution for the run (S-2 in the
    tool-call audit - a third-party case pack must not be able to
    self-escalate). This deliberately BREAKS symmetry with the `image`
    field's case-wins precedence: `image` selects among equally-trusted
    local images; this field changes what the run is allowed to touch.
    """
    if case_mode == "mock":
        return "mock"
    if user_mode == "sandboxed":
        return "sandboxed"
    return "mock"


def diff_case_asserted_arguments(case: dict, live_schemas: dict[str, dict]) -> list[str]:
    """The check that actually predicts whether a case can pass sandboxed:
    every argument name a case ASSERTS on (`arguments_contains` keys) must
    be an argument the real tool actually accepts, or that assertion can
    never match any real call and the case is definitively broken.

    Sharper than `diff_case_required_arguments` below, and empirically the
    one that matters: `arguments_contains` is a SUBSET match, so arguments
    the real server newly REQUIRES don't break anything (the model supplies
    them because the real schema says required, and extra keys in a call
    are ignored by the matcher) - across the whole git_repo corpus, the
    real server's newly-required `repo_path` broke zero cases while a
    single renamed argument (`paths` -> the real server's `files`) broke
    one. Blocking, not advisory: an unmatched assertion is a guaranteed
    failure, the same category as a tool that doesn't exist.
    """
    out: list[str] = []
    for field in ("expected_calls", "forbidden_calls"):
        for spec in case.get(field) or []:
            tool = spec.get("tool")
            schema = live_schemas.get(tool)
            if schema is None:
                continue    # missing tool -> diff_case_tools_against_live's job
            accepted = set((schema.get("function", {}).get("parameters", {}) or {})
                           .get("properties", {}) or {})
            for key in sorted(spec.get("arguments_contains") or {}):
                if key not in accepted:
                    out.append(f"{field}: {tool}.{key} is not an argument the real server "
                               f"accepts (it takes {sorted(accepted)})")
    return out


def diff_case_required_arguments(case: dict, service_name: str,
                                 live_schemas: dict[str, dict]) -> list[str]:
    """B-3 (tool-call audit): for each tool this case references that exists
    on BOTH sides, compare the live server's `inputSchema.required` against
    the mock's and report arguments the real server requires that the mock
    never did.

    ADVISORY only, and deliberately so - the audit predicted this would be
    the drift that bites, and measuring it against the real corpus proved
    otherwise: it fires on every git_repo tool (all of them newly require
    `repo_path`) while predicting nothing, because `arguments_contains` is
    a SUBSET match and the model is shown the real schema, so a
    newly-required argument breaks no assertion. It stays because it names
    a real mock-vs-reality fidelity gap worth an author's attention (and a
    weak model may simply fail to supply the argument), but
    `diff_case_asserted_arguments` above is the check that actually
    predicts a broken case, and that one blocks.
    """
    mock_entry = MOCK_SERVICES.get(service_name)
    if mock_entry is None:
        return []
    _, mock_schemas = mock_entry
    referenced: set[str] = set(case.get("tools") or [])
    referenced |= {c["tool"] for c in (case.get("expected_calls") or []) if "tool" in c}
    referenced |= {c["tool"] for c in (case.get("forbidden_calls") or []) if "tool" in c}

    def required_of(schema: dict) -> set[str]:
        return set((schema.get("function", {}).get("parameters", {}) or {}).get("required") or [])

    out: list[str] = []
    for name in sorted(referenced):
        if name not in live_schemas or name not in mock_schemas:
            continue    # missing entirely -> diff_case_tools_against_live's job
        extra = required_of(live_schemas[name]) - required_of(mock_schemas[name])
        if extra:
            out.append(f"{name}: real server additionally requires argument(s) {sorted(extra)}")
    return out


class SandboxedMCPService(MockService):
    """Adapter: satisfies `MockService`'s contract by proxying every call
    through a live `MCPStdioClient` session with a real server running
    inside `sandbox`. Construct via `build_sandboxed_service()` below rather
    than directly - it owns resolving the registry entry, starting the
    sandbox, and cleaning up on a failed handshake.
    """

    def __init__(self, sandbox: DockerSandbox, case_root: Path, command: list[str], service_name: str,
                 startup_timeout: float | None = None, shutdown_grace: float = 2.0,
                 extra_cleanup: Callable[[], None] | None = None,
                 env: dict[str, str] | None = None) -> None:
        super().__init__()
        self.service_name = service_name
        self._case_root = case_root
        self._sandbox = sandbox
        self._shutdown_grace = shutdown_grace
        self._extra_cleanup = extra_cleanup
        self._proc = sandbox.exec_attached(command, case_root, env=env)
        self._client = MCPStdioClient(self._proc)
        # startup_timeout overrides ONLY this first handshake call - a
        # service whose real server has an expensive startup dependency
        # (kind cluster creation, confirmed empirically to take 30s+ before
        # the MCP process even starts responding) needs longer here than
        # the client's normal per-call default, which every other
        # (near-instant-starting) service's calls should keep using.
        try:
            self._client.initialize({"name": "optarena", "version": _optarena_version()},
                                    timeout=startup_timeout)
            self.tool_schemas: dict[str, dict] = mcp_tools_to_openai_schemas(self._client.list_tools())
        except MCPProtocolError as exc:
            # B-1 (tool-call audit): a dead/hung handshake with no clue why
            # is what every real startup failure this cycle looked like -
            # the server's own stderr (drained into proc.stderr_tail by
            # exec_attached) is exactly the missing diagnostic. Append its
            # tail rather than making callers know where to dig.
            tail = list(getattr(self._proc, "stderr_tail", []) or [])[-15:]
            if tail:
                raise MCPProtocolError(
                    f"{exc}\nserver stderr (last {len(tail)} line(s)):\n" + "\n".join(tail)
                ) from None
            raise
        # dispatch() below is a full override - it never reads TOOLS to route
        # a call - but _tool_evaluate.evaluate_tool_case() computes
        # n_unknown_calls from `set(service.TOOLS)` directly, so this still
        # needs to be populated (instance-level, overriding the empty class
        # default) or every real call would be misreported as unknown.
        self.TOOLS = {name: name for name in self.tool_schemas}

    def schemas_for(self, tool_names: list[str] | None = None) -> list[dict]:
        """Same selection/ordering/error-message contract as
        `_mock_service.get_tool_schemas()` - a case's own `tools` subset/
        order when given, else every tool the live server has, in the order
        `tools/list` returned them."""
        names = tool_names if tool_names is not None else list(self.tool_schemas.keys())
        try:
            return [self.tool_schemas[n] for n in names]
        except KeyError as exc:
            raise KeyError(
                f"tool_service {self.service_name!r} (sandboxed) has no tool named {exc.args[0]!r} "
                f"(available: {', '.join(sorted(self.tool_schemas))})"
            ) from None

    def seed(self, spec: dict) -> None:
        """Filesystem-specific: writes real files/directories directly onto
        `case_root` (the host side of the container's bind mount) BEFORE the
        conversation starts - the correct mock-equivalent behavior for a
        server that operates on real disk, using the exact same spec shape
        (`files`/`directories`) `_mock_service.FilesystemService.seed()`
        accepts, so an existing case's `tool_service_seed` needs no changes
        to run in either mode. NOT a general pattern: a future sandboxed
        service with no host-visible state will need its own `seed()`
        (likely a documented no-op), not a copy of this one.

        Path convention - verified empirically against the real server
        (`@modelcontextprotocol/server-filesystem@2026.7.10`), not assumed:
        a bare relative path like "src/app.py" (the mock's convention) DOES
        work for every tool tested (read_text_file, write_file,
        list_directory), because the real process's cwd is already this
        case's own directory (`exec_attached`'s `-w`). The one real gap:
        `list_allowed_directories` returns the case's actual ABSOLUTE
        container path (e.g. "/workspace/<case_dir>"), not "/workspace" -
        so a model that calls it first and then echoes that absolute path
        back into later tool calls (plausible, real agent behavior) would
        produce `call_log` entries an `expected_calls[].arguments_contains`
        written against the mock's bare-relative convention won't match,
        even though the call itself was correct. `expected_final_state` is
        unaffected either way (`summary()` below always reports in the
        mock's own relative convention, since that's under our control on
        the host side).
        """
        for path, content in (spec.get("files") or {}).items():
            target = self._contained(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="" - write the string's bytes exactly as given. Python's
            # default text-mode write translates every "\n" to the platform
            # line separator ("\r\n" on Windows), which would silently
            # corrupt a case's seeded content before the real server (or a
            # model reading it via read_text_file) ever sees it - confirmed
            # empirically: a "\n"-only seed round-tripped as "\r\n" on disk
            # without this.
            target.write_text(content, encoding="utf-8", newline="")
        for path in spec.get("directories") or []:
            self._contained(path).mkdir(parents=True, exist_ok=True)
        # `media_files` (the mock's third seed key) has no real-bytes
        # equivalent in any existing case's seed spec - documented no-op
        # rather than fabricated binary content.

    def _contained(self, path: str) -> Path:
        """S-1 (tool-call audit): `tool_service_seed` paths are case content
        - untrusted, installable from a URL via case packs - and in THIS
        mode they become real host disk writes. `Path.__truediv__` with an
        absolute right-hand side REPLACES the base entirely, and `..`
        segments walk out of it, so join-then-verify is mandatory. Schema
        validation (`validate_case`) rejects these earlier with a better
        message; this is the load-bearing runtime check for any caller that
        didn't come through `load_cases` - the same two-layer pattern
        `write_setup_files` already uses. Raises ValueError (caught by
        `tool_chat.run_case`'s loop guard -> clean CaseResult.error)."""
        root = self._case_root.resolve()
        target = (root / path).resolve()
        if target != root and root not in target.parents:
            raise ValueError(
                f"tool_service_seed path escapes the case workspace: {path!r}")
        return target

    def dispatch(self, tool_name: str, arguments: dict) -> Any:
        """Full override, not `MockService.TOOLS`-based: there is no Python
        method per tool here, every call funnels through one live
        `call_tool()`. Still never raises and still logs to `call_log` in
        the identical `{"tool","arguments","result"}` shape - the contract
        every caller (the driver loop, the oracle) depends on."""
        arguments = dict(arguments or {})
        if tool_name not in self.tool_schemas:
            result: Any = {"error": f"unknown tool {tool_name!r}"}
        else:
            result = self._client.call_tool(tool_name, arguments)
        self.call_log.append({"tool": tool_name, "arguments": arguments, "result": result})
        return result

    def summary(self) -> dict:
        """Walks `case_root` on the host side (fast, exploits the bind
        mount) rather than round-tripping through MCP `list_directory`/
        `read_text_file` calls that would themselves show up in `call_log`
        and pollute the oracle's view of what the AGENT actually did.
        Filesystem-specific shortcut, not a general pattern - a future
        sandboxed service with no host-visible state must implement
        `summary()` by querying the real server instead. Field names match
        `FilesystemService.summary()` exactly, so `expected_final_state`
        checks written against the mock have a real chance of matching."""
        # E-4 (tool-call audit): this dict lands in the saved run JSON via
        # extra["oracle"]["final_state"] - a model writing one huge file
        # must not bloat memory/the results store. Caps chosen far above
        # anything a case legitimately asserts on (mock-seeded content is
        # hundreds of bytes); truncation is RECORDED, never silent, so an
        # expected_final_state mismatch on a truncated file is explainable.
        per_file_cap = 64 * 1024
        total_cap = 4 * 1024 * 1024
        files: dict[str, str | None] = {}
        truncated: list[str] = []
        total = 0
        n_dirs = 0
        for p in sorted(self._case_root.rglob("*")):
            rel = p.relative_to(self._case_root).as_posix()
            if p.is_dir():
                n_dirs += 1
            elif p.is_file():
                try:
                    # newline="" - report the file's real on-disk bytes, not
                    # a universal-newlines-normalized view. A model's own
                    # write_file call writes real bytes through the real
                    # server; expected_final_state should see exactly what
                    # that produced, same reasoning as seed()'s newline="".
                    # Path.read_text() only gained a newline= parameter in
                    # Python 3.13 - open() directly for 3.10+ compatibility.
                    with p.open("r", encoding="utf-8", newline="") as f:
                        content = f.read(per_file_cap + 1)
                    if len(content) > per_file_cap or total >= total_cap:
                        files[rel] = content[:per_file_cap] if total < total_cap else None
                        truncated.append(rel)
                    else:
                        files[rel] = content
                    total += len(files[rel] or "")
                except (UnicodeDecodeError, OSError):
                    files[rel] = None  # binary/unreadable - existence still recorded
        out: dict = {
            "n_calls": len(self.call_log),
            "file_count": len(files),
            "directory_count": n_dirs,
            "files": dict(sorted(files.items())),
        }
        if truncated:
            out["truncated_files"] = truncated
        return out

    def close(self) -> None:
        """Tears down the whole dedicated sandbox, not just the attached
        process - this service owns a container nothing else shares.

        Ordering matters (B-2 in the tool-call audit): closing the client's
        stdin is the GRACEFUL shutdown signal (MCP spec: server exits on
        stdin EOF), and a server with real external state (the kubernetes
        wrapper's `kind delete cluster` trap) needs its exit to actually
        complete before the container is killed out from under it -
        `sandbox.stop()`'s 2s container grace alone isn't enough, and
        `docker stop` never signals exec'd processes at all, only PID 1.
        So: EOF -> wait up to `shutdown_grace` for the server's own exit ->
        stop the container -> run the host-side `extra_cleanup` reaper for
        anything that outlives the container BY DESIGN (kind's node
        containers are HOST siblings). Never raises.
        """
        try:
            self._client.close()
        except Exception:  # noqa: BLE001 - teardown must never raise
            pass
        try:
            self._proc.wait(timeout=self._shutdown_grace)
        except Exception:  # noqa: BLE001 - subprocess.TimeoutExpired, or a fake in tests
            pass
        self._sandbox.stop()
        if self._extra_cleanup is not None:
            try:
                self._extra_cleanup()
            except Exception:  # noqa: BLE001 - best-effort reaper, teardown never raises
                pass


def start_sandboxed_container(name: str, case_root: Path) -> "tuple[DockerSandbox, dict, object]":
    """The container-lifecycle half of `build_sandboxed_service`, split out
    so agent-mode tool-use (`_agent_tool_use.py`) can get a RUNNING
    container without also starting the harness's own MCP client session -
    an external agent process is going to be the one exec-ing the real
    server command into this container (via `_mcp_proxy.py`), not this
    process. `build_sandboxed_service` below is now just this call plus its
    own `SandboxedMCPService` handshake.

    Returns `(sandbox, spec, extra_cleanup)` - `extra_cleanup` (a kind
    node's host-side reaper, or None) is the caller's to invoke in its own
    teardown, same contract as before.

    Raises `KeyError` for an unknown service name, or `RuntimeError` if the
    host-docker-socket gate blocks it or the container fails to start.
    """
    spec = get_sandboxed_service_factory(name)
    # S-3 (tool-call audit): a host_docker_socket service hands the model
    # under test unfiltered reach into the REAL host Docker daemon (and for
    # kubernetes, exec into a privileged kind node). The evaluation
    # harness's premise is that the backend may be arbitrarily bad - so
    # this specific capability is opt-in per environment, never ambient.
    # Same fail-closed pattern as OPTARENA_ALLOW_UNSAFE_HOST_EXEC.
    if spec.get("host_docker_socket") and os.environ.get("OPTARENA_ALLOW_HOST_DOCKER") != "1":
        raise RuntimeError(
            f"tool_service {name!r} (sandboxed) mounts the HOST Docker socket: the model "
            f"under test can create/remove real containers on your machine through it. "
            f"Set OPTARENA_ALLOW_HOST_DOCKER=1 to allow this explicitly."
        )
    image = resolve_image(spec["image"])
    # S-4 (tool-call audit): mount the CASE directory itself, never its
    # parent - the parent is the shared run root (every other case's
    # workspace, including hidden test files written mid-run), and the
    # containment must come from the mount, not from trusting the
    # third-party server's own path checks. exec_attached's relative_to
    # handles case_root == root fine (rel "." -> workdir "/workspace/.").
    sandbox = DockerSandbox(case_root, image=image, extra_run_args=spec.get("extra_run_args"),
                            network=spec.get("network", "none"), register=False)
    if not sandbox.start():
        raise RuntimeError(
            f"could not start a sandbox for tool_service {name!r} (image {image!r}) - "
            f"is a container engine running, and is the image built/pulled? "
            f"(see `optarena sandbox status`)"
        )
    extra_cleanup = None
    if spec.get("kind_cluster"):
        # B-2: the wrapper names its cluster "optarena-$OPTARENA_SANDBOX_NAME"
        # (env passed below), so the node container's HOST-side name is
        # derivable here for the reaper - kind's convention is
        # <cluster>-control-plane.
        node_container = f"optarena-{sandbox.name}-control-plane"

        def extra_cleanup(node=node_container):
            subprocess.run([container_engine(), "rm", "-f", node],
                           capture_output=True, timeout=30)
    return sandbox, spec, extra_cleanup


def build_sandboxed_service(name: str, case_root: Path) -> SandboxedMCPService:
    """Resolve `name` in `SANDBOXED_SERVICES`, start a dedicated per-case
    `DockerSandbox`, launch the real server inside it, and return a ready
    (handshake complete, tools discovered) `SandboxedMCPService`.

    Raises `KeyError` for an unknown service name (matches `get_mock_service`'s
    contract exactly) or `RuntimeError`/`MCPProtocolError` if the sandbox
    can't start or the handshake fails - all three are caught by
    `tool_chat.run_case()` and turned into `CaseResult.error`, never a crash.
    """
    sandbox, spec, extra_cleanup = start_sandboxed_container(name, case_root)
    try:
        return SandboxedMCPService(
            sandbox, case_root, spec["command"], name,
            startup_timeout=spec.get("startup_timeout"),
            shutdown_grace=spec.get("shutdown_grace", 2.0),
            extra_cleanup=extra_cleanup,
            env={"OPTARENA_SANDBOX_NAME": sandbox.name},
        )
    except Exception:  # noqa: BLE001 - e.g. MCPProtocolError from a failed handshake
        sandbox.stop()
        if extra_cleanup is not None:
            try:
                extra_cleanup()
            except Exception:  # noqa: BLE001 - best-effort reaper
                pass
        raise


def _optarena_version() -> str:
    from .. import __version__
    return __version__
