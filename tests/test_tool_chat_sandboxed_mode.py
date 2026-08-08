"""
`tool_chat.ToolChatDriver.run_case()`'s "sandboxed" execution-mode branch -
the mode selection (S-2: sandboxed is USER-granted via CLI/scenario, a case
may opt down to mock but never up to sandboxed), the schema-drift pre-flight
check, and that a SandboxedMCPService-shaped resource is always closed, even
on a mid-loop failure. `build_sandboxed_service` and the real container/MCP
plumbing it uses are faked here entirely (see
`test_sandboxed_mcp_service.py` for that layer's own coverage, and
`test_tool_use_cases.py::ToolChatDriverEndToEndTests` for the pre-existing
"mock" mode coverage this file is careful not to duplicate).
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from optarena.drivers.tool_chat import ToolChatDriver
from optarena.scenario import Backend, Scenario


class _FakeSandboxedService:
    """Duck-types SandboxedMCPService's public surface - no real Docker/MCP
    session, just enough to drive run_case()'s branch and teardown logic."""

    def __init__(self, tool_schemas: dict[str, dict]):
        self.tool_schemas = tool_schemas
        self.TOOLS = {name: name for name in tool_schemas}   # evaluate_tool_case reads this
        self.call_log: list[dict] = []
        self.closed = False
        self.seeded_with = None
        # run_case records these forensics (B-6) straight off the client.
        self._client = mock.Mock(server_info={"name": "fake-server", "version": "1.0"},
                                 server_protocol_version="2025-06-18")

    def schemas_for(self, tool_names=None):
        names = tool_names if tool_names is not None else list(self.tool_schemas)
        return [self.tool_schemas[n] for n in names]

    def seed(self, spec):
        self.seeded_with = spec

    def dispatch(self, tool_name, arguments):
        result = {"ok": True}
        self.call_log.append({"tool": tool_name, "arguments": arguments, "result": result})
        return result

    def summary(self):
        return {"n_calls": len(self.call_log)}

    def close(self):
        self.closed = True


_ECHO_SCHEMA = {
    "write_note": {"type": "function", "function": {
        "name": "write_note", "description": "d",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    }},
}


class SandboxedModeRoutingTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_tool_chat_sandboxed_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        self.backend = Backend(kind="openai", base_url="http://127.0.0.1:1", model="m", api_key="sk-x")

    def _case(self, **kw):
        case = {
            "name": "c", "tool_service": "filesystem",
            "prompts": ["do the thing"],
            "expected_calls": [{"tool": "write_note", "arguments_contains": {"text": "hi"}}],
        }
        case.update(kw)
        return case

    def test_scenario_level_sandboxed_routes_through_build_sandboxed_service(self):
        fake_service = _FakeSandboxedService(_ECHO_SCHEMA)
        scenario = Scenario(name="s", driver="openai-tools", backend=self.backend, tool_service_mode="sandboxed")
        with mock.patch("optarena.drivers.tool_chat.build_sandboxed_service", return_value=fake_service) as build, \
             mock.patch.object(ToolChatDriver, "_chat", return_value=(
                 {"role": "assistant", "content": "Done."}, [], {})):
            result = ToolChatDriver().run_case(self._case(expected_calls=[]), scenario, self.ws)
        build.assert_called_once_with("filesystem", self.ws)
        self.assertIsNone(result.error)
        self.assertEqual(result.extra["tool_service_mode"], "sandboxed")
        self.assertEqual(result.extra["mcp_server_info"]["name"], "fake-server")
        self.assertEqual(result.extra["mcp_protocol_version"], "2025-06-18")
        self.assertTrue(fake_service.closed, "sandboxed service must be closed after the run")

    def test_case_cannot_opt_up_to_sandboxed_when_user_did_not_enable_it(self):
        # S-2: a third-party case pack must not self-escalate. Case says
        # sandboxed, user (scenario/CLI) says nothing -> runs MOCK, with the
        # downgrade recorded rather than silent.
        scenario = Scenario(name="s", driver="openai-tools", backend=self.backend)
        with mock.patch("optarena.drivers.tool_chat.build_sandboxed_service") as build, \
             mock.patch.object(ToolChatDriver, "_chat", return_value=(
                 {"role": "assistant", "content": "Done."}, [], {})):
            result = ToolChatDriver().run_case(
                self._case(tool_service_mode="sandboxed", expected_calls=[]), scenario, self.ws)
        build.assert_not_called()
        self.assertIsNone(result.error)
        self.assertEqual(result.extra["tool_service_mode"], "mock")
        self.assertTrue(result.extra["sandboxed_downgraded"])

    def test_case_cannot_opt_up_even_when_user_explicitly_chose_mock(self):
        scenario = Scenario(name="s", driver="openai-tools", backend=self.backend, tool_service_mode="mock")
        with mock.patch("optarena.drivers.tool_chat.build_sandboxed_service") as build, \
             mock.patch.object(ToolChatDriver, "_chat", return_value=(
                 {"role": "assistant", "content": "Done."}, [], {})):
            result = ToolChatDriver().run_case(
                self._case(tool_service_mode="sandboxed", expected_calls=[]), scenario, self.ws)
        build.assert_not_called()
        self.assertEqual(result.extra["tool_service_mode"], "mock")

    def test_case_sandboxed_honored_when_user_also_enabled_sandboxed(self):
        fake_service = _FakeSandboxedService(_ECHO_SCHEMA)
        scenario = Scenario(name="s", driver="openai-tools", backend=self.backend, tool_service_mode="sandboxed")
        with mock.patch("optarena.drivers.tool_chat.build_sandboxed_service", return_value=fake_service) as build, \
             mock.patch.object(ToolChatDriver, "_chat", return_value=(
                 {"role": "assistant", "content": "Done."}, [], {})):
            result = ToolChatDriver().run_case(
                self._case(tool_service_mode="sandboxed", expected_calls=[]), scenario, self.ws)
        build.assert_called_once()
        self.assertNotIn("sandboxed_downgraded", result.extra)

    def test_case_can_opt_down_to_mock_under_a_sandboxed_run(self):
        scenario = Scenario(name="s", driver="openai-tools", backend=self.backend, tool_service_mode="sandboxed")
        with mock.patch("optarena.drivers.tool_chat.build_sandboxed_service") as build, \
             mock.patch.object(ToolChatDriver, "_chat", return_value=(
                 {"role": "assistant", "content": "Done."}, [], {})):
            result = ToolChatDriver().run_case(
                self._case(tool_service_mode="mock", expected_calls=[]), scenario, self.ws)
        build.assert_not_called()
        self.assertIsNone(result.error)   # ran fine against the real mock filesystem service
        self.assertNotIn("sandboxed_downgraded", result.extra)  # opting down is a choice, not a downgrade

    def test_default_mode_is_mock_when_neither_case_nor_scenario_sets_one(self):
        scenario = Scenario(name="s", driver="openai-tools", backend=self.backend)
        with mock.patch("optarena.drivers.tool_chat.build_sandboxed_service") as build, \
             mock.patch.object(ToolChatDriver, "_chat", return_value=(
                 {"role": "assistant", "content": "Done."}, [], {})):
            ToolChatDriver().run_case(self._case(expected_calls=[]), scenario, self.ws)
        build.assert_not_called()

    def test_schema_drift_reports_clean_error_closes_service_and_never_starts_the_loop(self):
        fake_service = _FakeSandboxedService(_ECHO_SCHEMA)  # has "write_note", not "delete_everything"
        scenario = Scenario(name="s", driver="openai-tools", backend=self.backend, tool_service_mode="sandboxed")
        case = self._case(expected_calls=[{"tool": "delete_everything"}])
        with mock.patch("optarena.drivers.tool_chat.build_sandboxed_service", return_value=fake_service), \
             mock.patch.object(ToolChatDriver, "_chat") as chat:
            result = ToolChatDriver().run_case(case, scenario, self.ws)
        chat.assert_not_called()   # drift caught before any request was made
        self.assertFalse(result.passed)
        self.assertIn("delete_everything", result.error)
        self.assertTrue(fake_service.closed)

    def test_build_sandboxed_service_failure_reports_clean_error_not_a_crash(self):
        scenario = Scenario(name="s", driver="openai-tools", backend=self.backend, tool_service_mode="sandboxed")
        with mock.patch("optarena.drivers.tool_chat.build_sandboxed_service",
                        side_effect=RuntimeError("no container engine")):
            result = ToolChatDriver().run_case(self._case(), scenario, self.ws)
        self.assertFalse(result.passed)
        self.assertIn("no container engine", result.error)

    def test_service_closed_even_when_the_loop_raises(self):
        fake_service = _FakeSandboxedService(_ECHO_SCHEMA)
        scenario = Scenario(name="s", driver="openai-tools", backend=self.backend, tool_service_mode="sandboxed")
        with mock.patch("optarena.drivers.tool_chat.build_sandboxed_service", return_value=fake_service), \
             mock.patch.object(ToolChatDriver, "_chat", side_effect=RuntimeError("backend exploded")):
            result = ToolChatDriver().run_case(self._case(expected_calls=[]), scenario, self.ws)
        self.assertIsNotNone(result.error)
        self.assertTrue(fake_service.closed, "close() must run even when the tool-call loop raises")

    def test_seed_failure_becomes_result_error_and_still_closes_the_service(self):
        # S-1/B-5: a seed containment violation (ValueError) must become a
        # clean CaseResult.error - never an escaped exception - and must
        # still tear the sandbox down.
        fake_service = _FakeSandboxedService(_ECHO_SCHEMA)
        fake_service.seed = mock.Mock(side_effect=ValueError(
            "tool_service_seed path escapes the case workspace: '../evil'"))
        scenario = Scenario(name="s", driver="openai-tools", backend=self.backend, tool_service_mode="sandboxed")
        with mock.patch("optarena.drivers.tool_chat.build_sandboxed_service", return_value=fake_service), \
             mock.patch.object(ToolChatDriver, "_chat") as chat:
            result = ToolChatDriver().run_case(self._case(expected_calls=[]), scenario, self.ws)
        chat.assert_not_called()
        self.assertIn("escapes the case workspace", result.error)
        self.assertTrue(fake_service.closed)


if __name__ == "__main__":
    unittest.main()
