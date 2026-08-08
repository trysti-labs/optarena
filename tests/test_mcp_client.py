"""
`optarena._cases._mcp_client`: the MCP stdio JSON-RPC client used by
sandboxed-real tool-use execution (see `_sandboxed_mcp_service.py`).

Driven against a tiny real Python subprocess speaking the protocol over real
OS pipes (not a Docker container, not an in-memory stub) - this is both the
unit coverage for the client and the "does attached-stdio-over-a-background-
reader-thread actually work" spike the implementation plan flagged as
unverified, without requiring Docker to run in CI.
"""

from __future__ import annotations

import subprocess
import sys
import unittest

from optarena._cases._mcp_client import MCPStdioClient

_FAKE_SERVER_SRC = r"""
import json, sys

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

TOOLS = [
    {"name": "echo", "description": "Echoes arguments back as text.",
     "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]}},
]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    req_id = msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-mcp-server", "version": "0.0.1"},
        }})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            send({"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": "echo:" + str(args.get("msg"))}],
                "isError": False,
            }})
        elif name == "boom":
            send({"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": "it broke"}],
                "isError": True,
            }})
        elif name == "structured":
            send({"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": "{}"}],
                "structuredContent": {"ok": True},
            }})
        elif name == "never_responds":
            pass
        elif name == "ask_client_first":
            # B-4 test hook: before answering the tool call, the SERVER
            # issues its own JSON-RPC REQUEST to the client and blocks on
            # the reply - a client that silently dropped it would deadlock
            # this tool call. The client's answer is echoed back so the
            # test can assert exactly what was sent.
            send({"jsonrpc": "2.0", "id": 999, "method": "roots/list", "params": {}})
            reply = json.loads(sys.stdin.readline())
            send({"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": json.dumps(reply)}],
                "isError": False,
            }})
        else:
            send({"jsonrpc": "2.0", "id": req_id,
                  "error": {"code": -32602, "message": "Unknown tool: " + str(name)}})
"""


class MCPStdioClientTests(unittest.TestCase):
    def setUp(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-c", _FAKE_SERVER_SRC],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self.client = MCPStdioClient(self.proc, request_timeout=5.0)

    def tearDown(self):
        self.client.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def test_initialize_returns_server_info(self):
        result = self.client.initialize({"name": "optarena-test", "version": "0.0.1"})
        self.assertEqual(result["serverInfo"]["name"], "fake-mcp-server")

    def test_list_tools_returns_the_real_schema(self):
        self.client.initialize({"name": "optarena-test", "version": "0.0.1"})
        tools = self.client.list_tools()
        self.assertEqual([t["name"] for t in tools], ["echo"])
        self.assertIn("inputSchema", tools[0])

    def test_call_tool_success_flattens_text_content(self):
        self.client.initialize({"name": "optarena-test", "version": "0.0.1"})
        result = self.client.call_tool("echo", {"msg": "hi"})
        self.assertEqual(result, "echo:hi")

    def test_call_tool_execution_error_becomes_error_dict(self):
        self.client.initialize({"name": "optarena-test", "version": "0.0.1"})
        result = self.client.call_tool("boom", {})
        self.assertIn("error", result)
        self.assertEqual(result["error"], "it broke")

    def test_call_tool_protocol_error_also_becomes_error_dict(self):
        # Same shape as an execution error - callers never need to tell a
        # JSON-RPC-level rejection apart from a tool-level failure.
        self.client.initialize({"name": "optarena-test", "version": "0.0.1"})
        result = self.client.call_tool("no_such_tool", {})
        self.assertIn("error", result)
        self.assertIn("Unknown tool", result["error"])

    def test_call_tool_structured_content_returned_verbatim(self):
        self.client.initialize({"name": "optarena-test", "version": "0.0.1"})
        result = self.client.call_tool("structured", {})
        self.assertEqual(result, {"ok": True})

    def test_server_initiated_request_gets_an_error_reply_not_a_deadlock(self):
        # B-4: the server sends its OWN request mid-tool-call and blocks on
        # the answer. A client that dropped it would hang this call until
        # timeout; the correct behavior is an immediate -32601 reply.
        self.client.initialize({"name": "optarena-test", "version": "0.0.1"})
        result = self.client.call_tool("ask_client_first", {})
        import json as _json
        reply = _json.loads(result)
        self.assertEqual(reply["id"], 999)
        self.assertEqual(reply["error"]["code"], -32601)

    def test_initialize_records_server_info_and_protocol_version(self):
        # B-6: what the server negotiated must be recordable, not a mystery.
        self.client.initialize({"name": "optarena-test", "version": "0.0.1"})
        self.assertEqual(self.client.server_info["name"], "fake-mcp-server")
        self.assertEqual(self.client.server_protocol_version, "2025-06-18")

    def test_call_tool_timeout_becomes_error_dict_not_a_hang_or_raise(self):
        # call_tool never raises (same contract as MockService.dispatch()) -
        # a request that never gets a response collapses to {"error": ...}
        # via a bounded per-call timeout, not an indefinite block.
        self.client.initialize({"name": "optarena-test", "version": "0.0.1"})
        result = self.client.call_tool("never_responds", {}, timeout=0.3)
        self.assertIn("error", result)
        self.assertIn("timed out", result["error"])


if __name__ == "__main__":
    unittest.main()
