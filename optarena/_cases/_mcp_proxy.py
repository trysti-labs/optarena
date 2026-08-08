"""
optarena/_cases/_mcp_proxy.py
─────────────────────────────
Agent-mode tool-use: a transparent, logging MCP stdio proxy.

The problem this solves: `openai-tools`/`ollama-tools` grade a tool-use case
by being the MCP client themselves (`MCPStdioClient`, driven by the
harness's own request loop) and recording every `tools/call` into
`call_log` as it happens. A REAL third-party agent (Claude Code, goose)
run as a subprocess instead becomes the MCP client - the harness is no
longer in that conversation at all, and has nothing to grade
`expected_calls`/`forbidden_calls` against.

This script is what the agent is told to launch as its MCP server. From
the agent's side it's an ordinary stdio MCP server - spawn it, talk
JSON-RPC over its stdin/stdout, done. What it actually does is exec the
REAL server command inside the already-running sandboxed container (a
second, independent `docker exec -i` session - containers support
concurrent exec sessions fine, and each spawns a fresh instance of the real
binary) and relay every message between agent and real server verbatim,
while separately parsing `tools/call` requests/responses to append one
line per completed call to a JSONL log file. `_agent_tool_use.py` reads
that file back after the agent process exits and builds the same
`{"tool", "arguments", "result"}` call_log shape `MockService`/
`SandboxedMCPService` already produce, so `evaluate_tool_case` runs
completely unmodified regardless of which side actually made the calls.

Framing matches `_mcp_client.py` exactly (newline-delimited JSON-RPC 2.0,
UTF-8, one message per line) since that's the real MCP stdio spec, not an
OptArena convention - the agent and the real server both expect it
natively, this proxy only needs to also parse it enough to log calls.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading

from ._mcp_client import _flatten_content
from ._sandbox import container_engine


def _relay(src, dst, on_line=None) -> None:
    """Copy lines from `src` to `dst` verbatim (the agent/real-server must
    see an unmodified stream), handing each parsed message to `on_line`
    first - a parse failure or callback error must never interrupt the
    relay itself, since a broken log is far less bad than a hung or
    corrupted MCP session.

    `on_line` runs BEFORE the forward, and the ordering is load-bearing in
    BOTH directions - forwarding first loses calls to a race, observed
    intermittently against real goose:

    - server->agent: the agent can exit (and take this proxy down with it)
      the instant it has its final response, so a log write queued after
      the forward may never happen - the case then grades as "made no
      calls" when it actually made them.
    - agent->server: the response can arrive before the request's own
      `pending[id]` entry is recorded, leaving nothing to correlate the
      response against.

    Logging first makes both impossible: nothing downstream can observe a
    message this proxy has not already accounted for.
    """
    for line in iter(src.readline, ""):
        if not line:
            break
        stripped = line.strip()
        if on_line is not None and stripped:
            try:
                on_line(json.loads(stripped))
            except json.JSONDecodeError:
                pass
            except Exception:  # noqa: BLE001 - logging must never take the relay down
                pass
        try:
            dst.write(line)
            dst.flush()
        except (BrokenPipeError, OSError):
            break


def _drain(stream) -> None:
    """Real server's stderr - must be continuously read or a server that
    logs more than the OS pipe buffer blocks on the write forever (the
    exact issue `_sandbox.exec_attached`'s stderr_tail exists to prevent;
    this proxy has no caller to surface a tail to, so it just discards)."""
    for _ in iter(stream.readline, ""):
        pass


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description="Transparent logging MCP stdio proxy - real usage is via "
                    "_agent_tool_use.py, not the command line directly.")
    parser.add_argument("--container", required=True, help="already-running sandbox container name")
    parser.add_argument("--log", required=True, help="JSONL file to append completed tools/call entries to")
    parser.add_argument("--engine", default=None,
                        help="container engine override (default: auto-detect). Shell-split, so "
                             "an engine that needs its own flags ('docker --context x') works")
    parser.add_argument("command", nargs="+", help="the real MCP server's launch command")
    args = parser.parse_args(argv)

    # posix=False on Windows: POSIX mode treats "\" as an escape, so a
    # Windows path ("C:\podman\podman.exe") would silently split into
    # "C:podmanpodman.exe" - confirmed, not theoretical.
    engine = (shlex.split(args.engine, posix=(os.name != "nt"))
              if args.engine else [container_engine()])
    full_cmd = [*engine, "exec", "-i", "-w", "/workspace", args.container, *args.command]
    try:
        real = subprocess.Popen(
            full_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
        )
    except OSError as exc:
        print(f"optarena-mcp-proxy: could not exec into {args.container!r}: {exc}", file=sys.stderr)
        return 1

    # id -> (tool name, arguments) for every in-flight tools/call request,
    # so the matching response (same id) can be logged as one completed
    # entry. Only tools/call is tracked - everything else (initialize,
    # tools/list, notifications) is relayed but not logged, since the
    # oracle only ever grades tools/call.
    pending: dict[int, tuple[str, dict]] = {}
    #: tools/list request ids - the response carries the real server's tool
    #: names, which the oracle needs (see on_server_to_agent).
    pending_lists: dict[int, bool] = {}
    pending_lock = threading.Lock()
    write_lock = threading.Lock()

    def _append(entry: dict) -> None:
        with write_lock, open(args.log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def on_agent_to_server(message: dict) -> None:
        method = message.get("method")
        if "id" not in message:
            return
        params = message.get("params") or {}
        with pending_lock:
            if method == "tools/call":
                pending[message["id"]] = (params.get("name", ""), params.get("arguments") or {})
            elif method == "tools/list":
                pending_lists[message["id"]] = True

    def on_server_to_agent(message: dict) -> None:
        msg_id = message.get("id")
        if msg_id is None:
            return
        with pending_lock:
            listing = pending_lists.pop(msg_id, False)
            call = pending.pop(msg_id, None)
        if listing:
            # The REAL server's advertised tool names, recorded so the
            # oracle's "calls to tools this service doesn't have" diagnostic
            # (`n_unknown_calls`) works the same as it does for a mock,
            # whose TOOLS is a class attribute. Written as a distinct
            # record type in the same file rather than a second file - one
            # artifact, read once, no ordering assumptions.
            tools = [t.get("name") for t in ((message.get("result") or {}).get("tools") or [])]
            _append({"_tools": [t for t in tools if t]})
            return
        if call is None:
            return
        tool, arguments = call
        if "error" in message:
            result = {"error": (message.get("error") or {}).get("message") or "unknown error"}
        else:
            body = message.get("result") or {}
            if body.get("isError"):
                result = {"error": _flatten_content(body.get("content")) or "tool reported an error"}
            else:
                result = _flatten_content(body.get("content"), structured=body.get("structuredContent"))
        _append({"tool": tool, "arguments": arguments, "result": result})

    stderr_thread = threading.Thread(target=_drain, args=(real.stderr,), daemon=True)
    stderr_thread.start()
    # agent (this process's own stdin/stdout) <-> real server (the exec'd
    # Popen's stdin/stdout), each direction relayed on its own thread so
    # neither side can starve the other.
    def pump_to_server() -> None:
        """Agent -> real server, then propagate the agent's DISCONNECT as a
        stdin EOF. Without that close, a well-behaved server that reads
        until EOF (the MCP stdio shutdown signal per spec) blocks on stdin
        forever after the agent exits - `real.wait()` below never returns
        and every case leaks a live server process into the container."""
        try:
            _relay(sys.stdin, real.stdin, on_agent_to_server)
        finally:
            try:
                if real.stdin and not real.stdin.closed:
                    real.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    to_server = threading.Thread(target=pump_to_server, daemon=True)
    to_agent = threading.Thread(
        target=_relay, args=(real.stdout, sys.stdout, on_server_to_agent), daemon=True)
    to_server.start()
    to_agent.start()
    # Bounded: a server that ignores stdin EOF (or is mid-request when the
    # agent dies) must not hold the case open forever - the agent process
    # has already exited by the time we get here, so there is nothing left
    # to serve. The container teardown in _agent_tool_use is the outer
    # backstop; this just keeps the proxy itself from being the thing that
    # hangs.
    try:
        real.wait(timeout=10)
    except subprocess.TimeoutExpired:
        real.kill()
        real.wait(timeout=5)
    to_agent.join(timeout=2)
    return real.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
