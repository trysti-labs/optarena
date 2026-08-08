"""
optarena/_cases/_agent_tool_use.py
───────────────────────────────────
Runs one tool-use case through a REAL third-party agent (Claude Code,
goose, ...) instead of the harness's own request loop - "agent vs
no-agent" for tool-calling, the comparison `optarena agent --tool-call`
refused until a driver actually had a path here (see CLI_AGENTS'
`mcp_client` entries in `drivers/cli_agents.py`).

Only reachable for sandboxed mode: the 14 mock services are in-process
Python objects satisfying an internal `dispatch()` contract, not real MCP
JSON-RPC endpoints - a third-party agent process literally cannot connect
to one. `mock` mode has nothing for this module to do.

Flow: start a dedicated sandbox container (same `start_sandboxed_container`
sandboxed mode already uses, but WITHOUT the harness's own MCP handshake -
the agent is going to be the client this time) -> point the agent at
`_mcp_proxy.py` as its MCP server command, via whichever config mechanism
`CLI_AGENTS[driver]["mcp_client"]` builds -> run the agent's normal
subprocess with that extra argv/env -> tear the container down -> read the
proxy's JSONL call log back and reconstruct a `call_log`-shaped object ->
run `evaluate_tool_case`, the EXACT SAME oracle `openai-tools`/
`ollama-tools` use, so a case behaves identically regardless of which side
made the calls.

Known, deliberate gap: `expected_final_state` assertions can't be graded
here beyond `{"n_calls": N}` - that check inspects a `MockService`'s own
`summary()`, which is domain-specific business logic (e.g. "does this
folder exist") this module has no generic way to reconstruct from an
observed call log against a REAL server. A case relying on
`expected_final_state` beyond what `_ObservedToolService.summary()`
provides will correctly fail to match those extra keys - reported as an
oracle mismatch, not silently skipped or false-passed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ._sandbox import run_capture
from ._sandboxed_mcp_service import impl_identity, resolve_tool_service_mode, start_sandboxed_container
from ._tool_evaluate import evaluate_tool_case


def agents_supporting_tool_use() -> "set[str]":
    """CLI-agent driver names that can run tool-use cases - i.e. that have
    a verified, headless way to be pointed at ONE specific MCP server while
    ignoring the user's own configured ones (`mcp_client` in CLI_AGENTS).
    Derived from the registry rather than hardcoded here, so adding support
    for another agent is one entry in one place."""
    from ..drivers.cli_agents import CLI_AGENTS
    return {name for name, spec in CLI_AGENTS.items() if spec.get("mcp_client")}


class _ObservedToolService:
    """Just enough of `MockService`'s contract for `evaluate_tool_case` to
    run unmodified against a call log OBSERVED by the proxy rather than
    produced by an in-process mock - see this module's docstring for the
    `summary()` limitation.

    `TOOLS` is the REAL server's advertised tool list (captured by the
    proxy from the `tools/list` response), where a mock's is a class
    attribute - it feeds the oracle's `n_unknown_calls` diagnostic. Empty
    when the agent never listed tools, which is not the same as "the
    server has none"; that only costs the diagnostic, never a verdict."""

    def __init__(self, call_log: list[dict], tools: "list[str] | None" = None) -> None:
        self.call_log = call_log
        self.TOOLS = tools or []

    def summary(self) -> dict:
        return {"n_calls": len(self.call_log)}


def _read_call_log(log_path: Path) -> "tuple[list[dict], list[str]]":
    """The proxy appends one JSON object per completed `tools/call` as it
    happens, plus at most one `{"_tools": [...]}` record - read whatever's
    there, including a partial last line from a process killed mid-write
    (a timed-out or crashed agent shouldn't lose every call it completed
    before that point). Returns `(call_log, tool_names)`."""
    if not log_path.exists():
        return [], []
    entries: list[dict] = []
    tools: list[str] = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue   # a truncated final line - see docstring
        if "_tools" in record:
            tools = record["_tools"]
        else:
            entries.append(record)
    return entries, tools


def run_agent_tool_case(driver_spec: dict, binary: str, base_argv: "list[str]", env: dict,
                        case: dict, tool_service_mode_pref: "str | None",
                        workspace: Path, timeout: float) -> dict:
    """Runs `case` (a tool-use case) through the CLI agent at `binary`,
    called with `base_argv` (the agent's own prompt/flags, already built by
    its normal driver code path) extended with whatever
    `driver_spec["mcp_client"]` needs to point it at the real sandboxed
    server. Returns a plain dict the caller (`CLIAgentDriver._run_tool_case`)
    assembles into a `CaseResult` - kept driver-agnostic here rather than
    importing `CaseResult` into this module, since this is orchestration,
    not a driver.

    Keys: `error` (str|None, an infrastructure failure - no oracle ran),
    `failures` (list[str]), `oracle` (dict), `call_log` (list[dict]),
    `duration_s` (float), `mcp_server_hint` (str|None, diagnostic only),
    `sandboxed_downgraded` (bool).
    """
    mcp_client = driver_spec.get("mcp_client")
    if mcp_client is None:
        return {"error": f"{driver_spec['label']} has no MCP client support - "
                         f"can't run tool-use cases through it"}

    service_name = case.get("tool_service")
    if not service_name:
        return {"error": "case has no 'tool_service' - not a tool-use case"}

    mode = resolve_tool_service_mode(case.get("tool_service_mode"), tool_service_mode_pref)
    if mode != "sandboxed":
        # Mirrors tool_chat.py's sandboxed_downgraded case, but a downgrade
        # here has nowhere to go: there is no mock-mode path for a real
        # external agent to connect to at all (see module docstring).
        return {"error": f"{driver_spec['label']} can only run tool-use cases in sandboxed mode "
                         f"(it needs a REAL MCP server to connect to as a client; there's no "
                         f"in-process mock it could reach) - this case resolved to {mode!r}",
                "sandboxed_downgraded": mode != case.get("tool_service_mode", mode)}

    t0 = time.monotonic()
    try:
        sandbox, spec, extra_cleanup = start_sandboxed_container(service_name, workspace)
    except (KeyError, RuntimeError) as exc:
        return {"error": str(exc)}

    log_fd, log_path_str = tempfile.mkstemp(prefix="optarena-agent-mcp-log-", suffix=".jsonl")
    os.close(log_fd)
    log_path = Path(log_path_str)
    proxy_argv = [sys.executable, "-m", "optarena._cases._mcp_proxy",
                 "--container", sandbox.name, "--log", str(log_path),
                 "--", *spec["command"]]
    hook_result = mcp_client(proxy_argv, service_name, workspace)
    argv = [*base_argv, *hook_result.get("argv", [])]
    full_env = {**env, **hook_result.get("env", {})}
    cleanup_paths = [Path(p) for p in hook_result.get("cleanup", [])]

    # Distinct from `error` below, same as CLIAgentDriver's coding-case path:
    # a non-zero exit means the AGENT reported failure, which the oracle
    # (grading whatever calls it actually made before giving up) may or may
    # not agree with - not an infrastructure error that makes the run
    # itself untrustworthy. `error` stays reserved for exceptions/timeouts.
    error = None
    execution_ok = True
    stderr_tail = ""
    try:
        # run_capture, NOT subprocess.run (H-11, same reasoning as the
        # coding path in cli_agents.py): the agent spawns the MCP proxy as
        # its own child, so on timeout `subprocess.run` would kill only the
        # agent and then block forever in communicate() draining a stdout/
        # stderr pipe the still-live grandchild holds open. Observed, not
        # theoretical: one trial ran 10011s against a 180s timeout while
        # its siblings took ~11s. run_capture reaps the whole process tree.
        proc = run_capture(
            [binary, *argv], cwd=workspace, env=full_env, timeout=int(timeout),
            text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode != 0:
            execution_ok = False
            stderr_tail = (proc.stderr or "")[-800:]
    except subprocess.TimeoutExpired:
        error = f"{driver_spec['label']} timed out after {timeout}s"
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        sandbox.stop()
        if extra_cleanup is not None:
            try:
                extra_cleanup()
            except Exception:  # noqa: BLE001 - best-effort reaper
                pass
        for p in cleanup_paths:
            # Entries can be files (a config) or the temp dir holding a
            # generated launcher - both are listed, dirs last by
            # construction, so a plain unlink isn't enough.
            try:
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    p.unlink(missing_ok=True)
            except OSError:
                pass

    call_log, tools = _read_call_log(log_path)
    log_path.unlink(missing_ok=True)
    service = _ObservedToolService(call_log, tools)
    failures, oracle = evaluate_tool_case(case, service)
    return {
        "error": error,
        "execution_ok": execution_ok,
        "stderr": stderr_tail,
        "failures": failures,
        "oracle": oracle,
        "call_log": call_log,
        "duration_s": time.monotonic() - t0,
        # The SAME spec start_sandboxed_container already used, not a fresh
        # registry lookup - re-resolving is one avoidable way for this hint
        # to ever disagree with the server that actually ran.
        "mcp_server_hint": impl_identity(spec),
    }
