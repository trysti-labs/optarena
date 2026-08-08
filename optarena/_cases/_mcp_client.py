"""
optarena/_cases/_mcp_client.py
───────────────────────────
A minimal MCP (Model Context Protocol) client for the stdio transport.

Per the spec (basic/transports#stdio): JSON-RPC 2.0 messages, one per line,
UTF-8, MUST NOT contain embedded newlines. Deliberately transport-agnostic
below the Popen boundary - it only needs an object with text-mode `.stdin`/
`.stdout` file objects (`write`/`flush`/`readline`); a local
`subprocess.Popen` and a `docker exec -i` Popen behave identically from here,
so this module has zero knowledge of sandboxing. See
`optarena/_cases/_sandboxed_mcp_service.py` for the caller that supplies a
sandboxed Popen.

Handshake per basic/lifecycle: `initialize` request -> server's capabilities/
serverInfo response -> client sends `notifications/initialized` (no response
expected) -> `tools/list` / `tools/call` thereafter.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from typing import Any

#: The protocol version this client negotiates. Per spec, the server may
#: respond with a different (older) version it supports instead; this client
#: doesn't currently enforce a match - see MCPStdioClient.initialize.
PROTOCOL_VERSION = "2025-06-18"


class MCPProtocolError(Exception):
    """A JSON-RPC error response, a broken pipe, or a request that timed out
    waiting for its response."""


class MCPStdioClient:
    """Talks MCP over a Popen's stdin/stdout. Only one request is ever in
    flight at a time (matches how the tool-calling driver loop uses it -
    strictly request/response, never concurrent calls), so there's no
    request-pipelining logic here."""

    def __init__(self, proc: subprocess.Popen, request_timeout: float = 30.0) -> None:
        self._proc = proc
        self._request_timeout = request_timeout
        self._next_id = 1
        self._id_lock = threading.Lock()
        # stdin is written from the caller's thread (_send) AND from the
        # reader thread (error-answering a server-initiated request, below) -
        # interleaved partial writes would corrupt the newline framing.
        self._write_lock = threading.Lock()
        # What the server said back in its `initialize` result - recorded for
        # run forensics (a future genuine protocol incompatibility shouldn't
        # be a mystery hang with no clue what the server negotiated).
        self.server_info: dict = {}
        self.server_protocol_version: str | None = None
        # Bounded: a server spamming notifications must cost bounded memory
        # for the session's lifetime, not unbounded growth. 1000 comfortably
        # exceeds any observed real traffic; on overflow the OLDEST entry is
        # dropped (see _read_loop) - the newest message is the one a pending
        # _recv is waiting for.
        self._responses: "queue.Queue[dict]" = queue.Queue(maxsize=1000)
        # Daemon thread: dies with the process, never blocks interpreter exit.
        # Runs for the server's whole lifetime; a closed stdout (server
        # exited) just ends iter(readline, "") - _recv's queue.get(timeout=)
        # is what turns "nothing arrived" into a clean MCPProtocolError
        # instead of a silent hang.
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # ── wire ────────────────────────────────────────────────────────────

    def _read_loop(self) -> None:
        stdout = self._proc.stdout
        if stdout is None:
            return
        for line in iter(stdout.readline, ""):
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                # Spec: "The server MUST NOT write anything to its stdout
                # that is not a valid MCP message" - a violation is a server
                # bug, not something that should take the client down.
                continue
            if "method" in message:
                # A server-initiated message. A notification (no id) needs no
                # reply; a server->client REQUEST (id present - ping,
                # roots/list, sampling) expects one, and a server that blocks
                # on that answer would deadlock the whole session if we just
                # dropped it (B-4 in the tool-call audit). This client
                # supports none of those capabilities (it declares none in
                # `initialize`), so the spec-correct reply is a method-not-
                # found error, sent immediately from this thread.
                if message.get("id") is not None:
                    try:
                        self._send_raw({
                            "jsonrpc": "2.0", "id": message["id"],
                            "error": {"code": -32601,
                                      "message": f"client does not support {message.get('method')!r}"},
                        })
                    except MCPProtocolError:
                        pass    # dead pipe - the caller-side timeout reports it
                continue
            try:
                self._responses.put_nowait(message)
            except queue.Full:
                # Drop the OLDEST message, keep the newest - a pending _recv
                # is always waiting for the most recent response.
                try:
                    self._responses.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._responses.put_nowait(message)
                except queue.Full:
                    pass

    def _send_raw(self, message: dict) -> None:
        """Locked, framed write of one already-shaped JSON-RPC message.
        Called from the caller's thread (_send) and from the reader thread
        (error-answering server-initiated requests) - the lock keeps the two
        from interleaving partial lines. Reader-thread callers swallow the
        failure (a dead pipe there just ends the session; the caller-side
        _recv timeout reports it)."""
        stdin = self._proc.stdin
        if stdin is None or stdin.closed:
            raise MCPProtocolError("server process has no open stdin")
        try:
            with self._write_lock:
                stdin.write(json.dumps(message) + "\n")
                stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise MCPProtocolError(f"failed writing to server stdin: {exc}") from None

    def _send(self, method: str, params: dict | None, *, notification: bool = False) -> int | None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        request_id = None
        if not notification:
            with self._id_lock:
                request_id = self._next_id
                self._next_id += 1
            message["id"] = request_id
        self._send_raw(message)
        return request_id

    def _recv(self, request_id: int, timeout: float | None) -> dict:
        # A stray notification (e.g. a logging message) can legitimately
        # interleave before our response arrives - drain until the matching
        # id shows up or the deadline passes, rather than assume FIFO.
        deadline = time.monotonic() + (timeout if timeout is not None else self._request_timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPProtocolError(f"timed out waiting for response to request {request_id}")
            try:
                message = self._responses.get(timeout=remaining)
            except queue.Empty:
                raise MCPProtocolError(f"timed out waiting for response to request {request_id}") from None
            if message.get("id") == request_id:
                return message
            # else: a notification or a stray/late response for a different
            # id - drop it and keep waiting for ours.

    def _request(self, method: str, params: dict | None, timeout: float | None) -> Any:
        request_id = self._send(method, params)
        response = self._recv(request_id, timeout)
        if "error" in response:
            err = response.get("error") or {}
            raise MCPProtocolError(err.get("message") or f"unknown error calling {method}")
        return response.get("result")

    # ── protocol ────────────────────────────────────────────────────────

    def initialize(self, client_info: dict, timeout: float | None = None) -> dict:
        result = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": client_info,
        }, timeout) or {}
        # Recorded, not enforced: the server may legitimately answer with an
        # older version it supports (postgres-mcp answers 2025-03-26 to our
        # 2025-06-18, observed live) and the subset this client uses is
        # stable across them - but if a future genuine incompatibility ever
        # bites, what was negotiated must be in the run record, not a
        # mystery (B-6 in the tool-call audit). Callers surface these via
        # CaseResult.extra.
        self.server_info = result.get("serverInfo") or {}
        self.server_protocol_version = result.get("protocolVersion")
        self.notify_initialized()
        return result

    def notify_initialized(self) -> None:
        self._send("notifications/initialized", None, notification=True)

    def list_tools(self, timeout: float | None = None) -> list[dict]:
        # tools/list supports cursor-based pagination per spec; no service
        # this client currently talks to returns more than one page, but
        # follow nextCursor defensively rather than silently truncate.
        tools: list[dict] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params, timeout) or {}
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict, timeout: float | None = None) -> Any:
        """Returns the tool's result on success, or ``{"error": ...}`` on
        EITHER a JSON-RPC protocol error OR a tool-execution error
        (``isError: true``) - the exact shape ``MockService.dispatch()``
        already returns for a failed call, so callers never need to
        distinguish "the server rejected the call" from "the tool ran and
        reported failure"."""
        try:
            result = self._request("tools/call", {"name": name, "arguments": arguments}, timeout) or {}
        except MCPProtocolError as exc:
            return {"error": str(exc)}
        if result.get("isError"):
            return {"error": _flatten_content(result.get("content")) or "tool reported an error"}
        return _flatten_content(result.get("content"), structured=result.get("structuredContent"))

    def close(self) -> None:
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except OSError:
            pass


def _flatten_content(content: list[dict] | None, structured: dict | None = None) -> Any:
    """A CallToolResult's ``content`` is a list of typed blocks (text/image/
    resource/...). A mock service's ``dispatch()`` just returns plain data
    (whatever the Python method returned) - flatten to the closest
    equivalent: ``structuredContent`` verbatim when the server provided it,
    else the concatenated text of every text block, else the first non-text
    block as-is (image/audio/resource results have no plain-text form to
    collapse to)."""
    if structured is not None:
        return structured
    if not content:
        return None
    texts = [block.get("text", "") for block in content if block.get("type") == "text"]
    if texts:
        return "\n".join(texts)
    return content[0]
