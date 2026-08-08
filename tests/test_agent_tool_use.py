"""
Agent-mode tool-use: a REAL third-party agent (Claude Code, goose) as the
MCP client, with `_mcp_proxy.py` sitting in the middle so the harness can
still see every `tools/call` and grade it with the SAME oracle
`openai-tools`/`ollama-tools` use.

The proxy is tested against a fake "real server" (a plain Python script
speaking newline-delimited JSON-RPC on stdio) rather than Docker: what
matters here is the relay/log logic, and the container half is exercised
live elsewhere. The subprocess is launched with `--engine` pointed at a
stub so no container engine is required.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from optarena._cases._agent_tool_use import _ObservedToolService, _read_call_log, run_agent_tool_case
from optarena.drivers.cli_agents import CLI_AGENTS, _claude_mcp_client, _goose_mcp_client


# A stand-in for `<engine> exec -i <container> <real server cmd>`: ignores
# every argument up to the script path, then runs it. Lets the proxy's real
# argv shape (engine, exec, -i, -w, container, command...) stay untouched
# while needing no Docker.
_FAKE_ENGINE = textwrap.dedent("""
    import subprocess, sys
    args = sys.argv[1:]
    script = [a for a in args if a.endswith(".py")][0]
    sys.exit(subprocess.call([sys.executable, script]))
""")

# Answers initialize/tools/list, and echoes tools/call back with a result
# whose text names the tool - enough for the proxy to have something real
# to parse. Also emits an unsolicited notification, to prove the proxy
# relays non-response traffic without trying to log it.
_FAKE_SERVER = textwrap.dedent("""
    import json, sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method, mid = msg.get("method"), msg.get("id")
        if method == "initialize":
            out = {"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": "2025-06-18",
                   "capabilities": {}, "serverInfo": {"name": "fake", "version": "1"}}}
        elif method == "tools/list":
            out = {"jsonrpc": "2.0", "id": mid, "result": {"tools": [{"name": "read_file"}]}}
        elif method == "tools/call":
            name = msg["params"]["name"]
            if name == "explode":
                out = {"jsonrpc": "2.0", "id": mid, "result":
                       {"isError": True, "content": [{"type": "text", "text": "boom"}]}}
            elif name == "rejected":
                out = {"jsonrpc": "2.0", "id": mid,
                       "error": {"code": -32602, "message": "bad args"}}
            else:
                out = {"jsonrpc": "2.0", "id": mid, "result":
                       {"content": [{"type": "text", "text": "ran " + name}]}}
        elif mid is None:
            continue
        else:
            out = {"jsonrpc": "2.0", "id": mid, "result": {}}
        sys.stdout.write(json.dumps(out) + "\\n")
        sys.stdout.flush()
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/x"}) + "\\n")
        sys.stdout.flush()
""")


class ProxyRelayTests(unittest.TestCase):
    """End-to-end through the real `_mcp_proxy` subprocess: an "agent"
    (this test, writing to the proxy's stdin) talking to a "real server"
    through it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="optarena_proxy_test_"))
        self.engine = self.tmp / "engine.py"
        self.engine.write_text(_FAKE_ENGINE, encoding="utf-8")
        self.server = self.tmp / "server.py"
        self.server.write_text(_FAKE_SERVER, encoding="utf-8")
        self.log = self.tmp / "calls.jsonl"

    def _run_proxy(self, messages: list[dict]) -> tuple[list[dict], list[dict]]:
        """Drive the proxy with `messages`, return (replies, logged calls)."""
        proc = subprocess.Popen(
            [sys.executable, "-m", "optarena._cases._mcp_proxy",
             "--container", "fake-container", "--log", str(self.log),
             "--engine", f"{sys.executable} {self.engine}",
             "--", str(self.server)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        payload = "".join(json.dumps(m) + "\n" for m in messages)
        out, _err = proc.communicate(payload, timeout=30)
        replies = [json.loads(line) for line in out.splitlines() if line.strip()]
        calls, _tools = _read_call_log(self.log)
        return replies, calls

    def test_relays_responses_back_to_the_agent_verbatim(self):
        replies, _ = self._run_proxy([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        ])
        results = [r for r in replies if r.get("id") == 1]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["result"]["serverInfo"]["name"], "fake")

    def test_logs_a_completed_tool_call_with_arguments_and_result(self):
        _, calls = self._run_proxy([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "read_file", "arguments": {"path": "a.txt"}}},
        ])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool"], "read_file")
        self.assertEqual(calls[0]["arguments"], {"path": "a.txt"})
        self.assertEqual(calls[0]["result"], "ran read_file")

    def test_logs_every_call_in_order(self):
        _, calls = self._run_proxy([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "read_file", "arguments": {"path": "a"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "write_file", "arguments": {"path": "b"}}},
        ])
        self.assertEqual([c["tool"] for c in calls], ["read_file", "write_file"])

    def test_non_tool_call_traffic_is_relayed_but_not_logged(self):
        # tools/list and notifications must reach the agent (it can't work
        # without the tool list) while never appearing in the graded log.
        replies, calls = self._run_proxy([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        ])
        self.assertTrue(any(r.get("id") == 1 for r in replies))
        self.assertTrue(any(r.get("method") == "notifications/x" for r in replies))
        self.assertEqual(calls, [])

    def test_tool_execution_error_is_logged_as_an_error_result(self):
        # isError:true is a call that HAPPENED - it must appear in the log
        # (a forbidden_calls assertion has to catch it) with the same
        # {"error": ...} shape MockService.dispatch uses.
        _, calls = self._run_proxy([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "explode", "arguments": {}}},
        ])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["tool"], "explode")
        self.assertEqual(calls[0]["result"], {"error": "boom"})

    def test_jsonrpc_error_response_is_logged_too(self):
        _, calls = self._run_proxy([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "rejected", "arguments": {"x": 1}}},
        ])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["result"], {"error": "bad args"})

    def test_missing_arguments_key_logs_an_empty_dict_not_none(self):
        # evaluate_tool_case does `entry["arguments"]` membership tests -
        # None would raise instead of cleanly not-matching.
        _, calls = self._run_proxy([
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "read_file"}},
        ])
        self.assertEqual(calls[0]["arguments"], {})


class RelayOrderingTests(unittest.TestCase):
    """`_relay` must log a message BEFORE forwarding it. Forwarding first
    loses the final call to a race (the agent can exit the instant it has
    its last response, killing the proxy before the queued write happens) -
    observed intermittently against real goose, where the same case graded
    PASS or "made no calls" run to run."""

    def test_callback_runs_before_the_write(self):
        from optarena._cases._mcp_proxy import _relay

        order: list[str] = []

        class Src:
            def __init__(self):
                self.lines = ['{"id": 1}\n', ""]
            def readline(self):
                return self.lines.pop(0)

        class Dst:
            def write(self, _line):
                order.append("forward")
            def flush(self):
                pass

        _relay(Src(), Dst(), lambda _m: order.append("log"))
        self.assertEqual(order, ["log", "forward"])

    def test_malformed_json_still_forwards(self):
        # A non-JSON line is the server violating spec - relay it anyway
        # rather than silently breaking the session.
        from optarena._cases._mcp_proxy import _relay

        forwarded: list[str] = []

        class Src:
            def __init__(self):
                self.lines = ["not json\n", ""]
            def readline(self):
                return self.lines.pop(0)

        class Dst:
            def write(self, line):
                forwarded.append(line)
            def flush(self):
                pass

        _relay(Src(), Dst(), lambda _m: None)
        self.assertEqual(forwarded, ["not json\n"])

    def test_callback_exception_still_forwards(self):
        from optarena._cases._mcp_proxy import _relay

        forwarded: list[str] = []

        class Src:
            def __init__(self):
                self.lines = ['{"id": 1}\n', ""]
            def readline(self):
                return self.lines.pop(0)

        class Dst:
            def write(self, line):
                forwarded.append(line)
            def flush(self):
                pass

        def boom(_m):
            raise RuntimeError("logging blew up")

        _relay(Src(), Dst(), boom)
        self.assertEqual(forwarded, ['{"id": 1}\n'])


class ReadCallLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="optarena_log_test_"))

    def test_missing_file_is_empty_not_an_error(self):
        self.assertEqual(_read_call_log(self.tmp / "nope.jsonl"), ([], []))

    def test_truncated_final_line_keeps_the_completed_entries(self):
        # A killed/timed-out agent must not lose the calls it did complete.
        p = self.tmp / "calls.jsonl"
        p.write_text('{"tool": "a", "arguments": {}, "result": 1}\n{"tool": "b", "argum',
                     encoding="utf-8")
        entries, _tools = _read_call_log(p)
        self.assertEqual([e["tool"] for e in entries], ["a"])

    def test_blank_lines_ignored(self):
        p = self.tmp / "calls.jsonl"
        p.write_text('\n{"tool": "a", "arguments": {}, "result": 1}\n\n', encoding="utf-8")
        entries, _tools = _read_call_log(p)
        self.assertEqual(len(entries), 1)

    def test_tools_record_is_separated_from_the_call_log(self):
        # The real server's advertised tool list rides in the same file as
        # a distinct record type - it must never be graded as a call.
        p = self.tmp / "calls.jsonl"
        p.write_text('{"_tools": ["read_file", "write_file"]}\n'
                     '{"tool": "read_file", "arguments": {}, "result": 1}\n', encoding="utf-8")
        entries, tools = _read_call_log(p)
        self.assertEqual([e["tool"] for e in entries], ["read_file"])
        self.assertEqual(tools, ["read_file", "write_file"])


class ObservedServiceTests(unittest.TestCase):
    """`evaluate_tool_case` must run unmodified against an observed log."""

    def test_oracle_passes_on_a_matching_observed_call(self):
        from optarena._cases._tool_evaluate import evaluate_tool_case
        svc = _ObservedToolService([
            {"tool": "read_file", "arguments": {"path": "a.txt"}, "result": "x"}])
        case = {"name": "c", "expected_calls": [
            {"tool": "read_file", "arguments_contains": {"path": "a.txt"}}]}
        failures, _oracle = evaluate_tool_case(case, svc)
        self.assertEqual(failures, [])

    def test_oracle_fails_on_a_missing_expected_call(self):
        from optarena._cases._tool_evaluate import evaluate_tool_case
        svc = _ObservedToolService([])
        case = {"name": "c", "expected_calls": [{"tool": "read_file"}]}
        failures, _oracle = evaluate_tool_case(case, svc)
        self.assertTrue(failures)

    def test_oracle_catches_a_forbidden_call(self):
        from optarena._cases._tool_evaluate import evaluate_tool_case
        svc = _ObservedToolService([
            {"tool": "write_file", "arguments": {"path": "a"}, "result": "x"}])
        case = {"name": "c", "forbidden_calls": [{"tool": "write_file"}]}
        failures, _oracle = evaluate_tool_case(case, svc)
        self.assertTrue(failures)

    def test_summary_reports_call_count(self):
        self.assertEqual(_ObservedToolService([{"tool": "a"}]).summary(), {"n_calls": 1})


class McpClientHookTests(unittest.TestCase):
    """Each agent's hook must isolate the run from the user's OWN configured
    MCP servers - otherwise the model under test sees tools the case never
    granted, and the comparison measures the wrong thing."""

    _PROXY = ["python", "-m", "optarena._cases._mcp_proxy", "--container", "c", "--log", "l"]

    def test_claude_uses_strict_mcp_config(self):
        out = _claude_mcp_client(self._PROXY, "filesystem", Path("."))
        self.addCleanup(lambda: [Path(p).unlink(missing_ok=True) for p in out["cleanup"]])
        self.assertIn("--strict-mcp-config", out["argv"])
        config = json.loads(Path(out["argv"][out["argv"].index("--mcp-config") + 1]).read_text())
        self.assertEqual(list(config["mcpServers"]), ["filesystem"])
        self.assertEqual(config["mcpServers"]["filesystem"]["command"], self._PROXY[0])
        self.assertEqual(config["mcpServers"]["filesystem"]["args"], self._PROXY[1:])

    def test_claude_config_file_is_cleaned_up(self):
        # The config carries no secret, but it is written per case - never
        # cleaning it up would accumulate one file per tool-use case run,
        # indefinitely, in the OS temp dir.
        out = _claude_mcp_client(self._PROXY, "filesystem", Path("."))
        path = Path(out["cleanup"][0])
        self.assertTrue(path.exists())
        self._cleanup(out)
        self.assertFalse(path.exists())

    def _cleanup(self, out):
        import shutil
        for p in out["cleanup"]:
            p = Path(p)
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)

    def test_goose_uses_no_profile_and_one_extension(self):
        out = _goose_mcp_client(self._PROXY, "filesystem", Path("."))
        self.addCleanup(self._cleanup, out)
        self.assertIn("--no-profile", out["argv"])
        self.assertIn("--with-extension", out["argv"])

    def test_goose_extension_is_named_after_the_service(self):
        # goose derives the model-facing tool namespace from the command's
        # binary name - invoked as `python -m optarena...` directly, every
        # tool reached the model as `python_exe__<tool>` (observed live).
        out = _goose_mcp_client(self._PROXY, "filesystem", Path("."))
        self.addCleanup(self._cleanup, out)
        launcher = Path(out["argv"][out["argv"].index("--with-extension") + 1])
        self.assertTrue(launcher.stem.startswith("filesystem"), launcher.name)
        self.assertIn("_mcp_proxy", launcher.read_text(encoding="utf-8"))

    def test_goose_launcher_is_cleaned_up(self):
        out = _goose_mcp_client(self._PROXY, "filesystem", Path("."))
        launcher = Path(out["argv"][out["argv"].index("--with-extension") + 1])
        self.assertTrue(launcher.exists())
        self._cleanup(out)
        self.assertFalse(launcher.exists())
        self.assertFalse(launcher.parent.exists())

    def test_only_verified_agents_declare_mcp_support(self):
        # aider/codex/qwen-code have no verified headless MCP-client flags,
        # so they must NOT advertise support - run_agent_tool_case reports a
        # clean error for them rather than silently running with no server.
        with_mcp = {k for k, v in CLI_AGENTS.items() if v.get("mcp_client")}
        self.assertEqual(with_mcp, {"claude-code", "goose"})


class ProcessTreeTimeoutTests(unittest.TestCase):
    """The agent spawns the MCP proxy as its own child, so a timeout has to
    reap the whole TREE. Plain `subprocess.run` kills only the agent and
    then blocks in communicate() on a pipe the live grandchild still holds -
    observed as a trial running 10011s against a 180s timeout while its
    siblings took ~11s."""

    def test_uses_run_capture_not_subprocess_run(self):
        import inspect

        from optarena._cases import _agent_tool_use
        src = inspect.getsource(_agent_tool_use.run_agent_tool_case)
        self.assertIn("run_capture(", src)
        self.assertNotIn("subprocess.run(", src)

    def test_timeout_is_reported_as_an_infrastructure_error(self):
        from optarena._cases import _agent_tool_use

        def boom(*_a, **_kw):
            raise subprocess.TimeoutExpired(cmd="goose", timeout=180)

        with mock.patch.object(_agent_tool_use, "run_capture", boom), \
             mock.patch.object(_agent_tool_use, "start_sandboxed_container") as start:
            sandbox = mock.MagicMock()
            start.return_value = (sandbox, {"command": ["srv"], "image": "i:1"}, None)
            out = _agent_tool_use.run_agent_tool_case(
                CLI_AGENTS["goose"], "goose", [], {},
                {"name": "c", "tool_service": "filesystem"}, "sandboxed", Path("."), 180)
        self.assertIn("timed out", out["error"])
        # The container must still be torn down on the timeout path.
        sandbox.stop.assert_called_once()


class RunAgentToolCaseGuardTests(unittest.TestCase):
    """The refusals that must happen BEFORE any container is started."""

    def test_driver_without_mcp_support_refused(self):
        out = run_agent_tool_case(
            {"label": "aider"}, "aider", [], {}, {"name": "c", "tool_service": "filesystem"},
            "sandboxed", Path("."), 10)
        self.assertIn("no MCP client support", out["error"])

    def test_non_tool_use_case_refused(self):
        out = run_agent_tool_case(
            CLI_AGENTS["goose"], "goose", [], {}, {"name": "c"}, "sandboxed", Path("."), 10)
        self.assertIn("not a tool-use case", out["error"])

    def test_mock_mode_refused_without_starting_a_container(self):
        # There is no in-process mock a real external agent could connect
        # to - this must fail clearly, not silently produce an empty log
        # that the oracle would then grade as "made no calls".
        with mock.patch("optarena._cases._agent_tool_use.start_sandboxed_container") as start:
            out = run_agent_tool_case(
                CLI_AGENTS["goose"], "goose", [], {},
                {"name": "c", "tool_service": "filesystem"}, "mock", Path("."), 10)
        start.assert_not_called()
        self.assertIn("sandboxed mode", out["error"])

    def test_case_can_still_downgrade_itself_to_mock_and_is_refused(self):
        with mock.patch("optarena._cases._agent_tool_use.start_sandboxed_container") as start:
            out = run_agent_tool_case(
                CLI_AGENTS["goose"], "goose", [], {},
                {"name": "c", "tool_service": "filesystem", "tool_service_mode": "mock"},
                "sandboxed", Path("."), 10)
        start.assert_not_called()
        self.assertIn("sandboxed mode", out["error"])


if __name__ == "__main__":
    unittest.main()
