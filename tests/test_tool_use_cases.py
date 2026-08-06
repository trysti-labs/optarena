"""
Tool-use case domain tests (stdlib only): the mock service, the tool-use
oracle, case-schema validation for the new fields, and the tool-calling
drivers (openai-tools/ollama-tools) end to end against a real stub HTTP
backend - the same pattern BaselineDriverEndToEndTests in test_optarena.py
uses for openai-chat/ollama-chat, just serving tool_calls instead of a plain
code block.
"""

from __future__ import annotations

import http.server
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from optarena.cases import MOCK_SERVICES, evaluate_tool_case, get_mock_service, get_tool_schemas
from optarena.cases import load_cases
from optarena._cases._mock_service import TaskTrackerService
from optarena.schema import SchemaError, validate_case
from optarena.scenario import Backend, Scenario


class TaskTrackerServiceTests(unittest.TestCase):
    """Direct unit tests on the mock service - no driver/network involved."""

    def test_create_task_returns_and_logs_it(self):
        svc = TaskTrackerService()
        result = svc.dispatch("create_task", {"title": "Buy milk", "assignee": "alice"})
        self.assertEqual(result["title"], "Buy milk")
        self.assertEqual(result["status"], "open")
        self.assertEqual(len(svc.call_log), 1)
        self.assertEqual(svc.call_log[0]["tool"], "create_task")

    def test_complete_task_by_id_from_create_result(self):
        svc = TaskTrackerService()
        created = svc.dispatch("create_task", {"title": "Ship it"})
        completed = svc.dispatch("complete_task", {"task_id": created["id"]})
        self.assertEqual(completed["status"], "done")
        self.assertEqual(svc.summary()["completed_count"], 1)

    def test_complete_unknown_task_id_is_an_error_result_not_a_crash(self):
        svc = TaskTrackerService()
        result = svc.dispatch("complete_task", {"task_id": 999})
        self.assertIn("error", result)
        # Still logged - a failed call is data the oracle can inspect.
        self.assertEqual(len(svc.call_log), 1)

    def test_list_tasks_filters_by_status(self):
        svc = TaskTrackerService()
        a = svc.dispatch("create_task", {"title": "A"})
        svc.dispatch("create_task", {"title": "B"})
        svc.dispatch("complete_task", {"task_id": a["id"]})
        open_only = svc.dispatch("list_tasks", {"status": "open"})
        self.assertEqual([t["title"] for t in open_only], ["B"])

    def test_delete_task_removes_it(self):
        svc = TaskTrackerService()
        created = svc.dispatch("create_task", {"title": "Temp"})
        svc.dispatch("delete_task", {"task_id": created["id"]})
        self.assertEqual(svc.summary()["task_count"], 0)

    def test_unknown_tool_name_is_an_error_result_not_a_crash(self):
        svc = TaskTrackerService()
        result = svc.dispatch("frobnicate_task", {})
        self.assertIn("error", result)
        self.assertEqual(svc.call_log[0]["tool"], "frobnicate_task")

    def test_wrong_arguments_is_an_error_result_not_a_crash(self):
        svc = TaskTrackerService()
        result = svc.dispatch("create_task", {"nonexistent_kwarg": 1})
        self.assertIn("error", result)


class MockServiceRegistryTests(unittest.TestCase):
    def test_get_mock_service_unknown_name_raises_clear_error(self):
        with self.assertRaises(KeyError):
            get_mock_service("does_not_exist")

    def test_get_tool_schemas_defaults_to_every_tool(self):
        schemas = get_tool_schemas("task_tracker")
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(names, {"create_task", "complete_task", "list_tasks", "delete_task"})

    def test_get_tool_schemas_respects_explicit_subset_and_order(self):
        schemas = get_tool_schemas("task_tracker", ["delete_task", "create_task"])
        self.assertEqual([s["function"]["name"] for s in schemas], ["delete_task", "create_task"])

    def test_get_tool_schemas_unknown_tool_name_raises_clear_error(self):
        with self.assertRaises(KeyError):
            get_tool_schemas("task_tracker", ["not_a_real_tool"])

    def test_registry_is_not_empty(self):
        self.assertIn("task_tracker", MOCK_SERVICES)


class EvaluateToolCaseTests(unittest.TestCase):
    """The oracle, given a fabricated call log/final state directly - no
    driver or network needed. Mirrors the coding oracle's own
    reference/broken-solution discrimination, as fast synthetic checks."""

    def _case(self, **kw):
        case = {"name": "c", "tool_service": "task_tracker"}
        case.update(kw)
        return case

    def test_expected_call_present_passes(self):
        svc = TaskTrackerService()
        svc.dispatch("create_task", {"title": "Buy milk", "assignee": "alice"})
        case = self._case(expected_calls=[
            {"tool": "create_task", "arguments_contains": {"title": "Buy milk"}}
        ])
        failures, info = evaluate_tool_case(case, svc)
        self.assertEqual(failures, [])
        self.assertEqual(info["n_calls"], 1)

    def test_expected_call_missing_fails(self):
        svc = TaskTrackerService()   # never called anything
        case = self._case(expected_calls=[{"tool": "create_task"}])
        failures, _info = evaluate_tool_case(case, svc)
        self.assertEqual(len(failures), 1)
        self.assertIn("create_task", failures[0])

    def test_arguments_contains_is_a_subset_match_not_exact(self):
        svc = TaskTrackerService()
        svc.dispatch("create_task", {"title": "Buy milk", "assignee": "alice"})
        # Only asserts on title - the extra assignee argument must not fail this.
        case = self._case(expected_calls=[
            {"tool": "create_task", "arguments_contains": {"title": "Buy milk"}}
        ])
        failures, _info = evaluate_tool_case(case, svc)
        self.assertEqual(failures, [])

    def test_forbidden_call_made_fails(self):
        svc = TaskTrackerService()
        svc.dispatch("delete_task", {"task_id": 1})
        case = self._case(forbidden_calls=[{"tool": "delete_task"}])
        failures, _info = evaluate_tool_case(case, svc)
        self.assertEqual(len(failures), 1)
        self.assertIn("forbidden", failures[0])

    def test_forbidden_call_not_made_passes(self):
        svc = TaskTrackerService()
        svc.dispatch("create_task", {"title": "x"})
        case = self._case(forbidden_calls=[{"tool": "delete_task"}])
        failures, _info = evaluate_tool_case(case, svc)
        self.assertEqual(failures, [])

    def test_expected_final_state_mismatch_fails(self):
        svc = TaskTrackerService()
        svc.dispatch("create_task", {"title": "x"})
        case = self._case(expected_final_state={"task_count": 2})
        failures, _info = evaluate_tool_case(case, svc)
        self.assertEqual(len(failures), 1)
        self.assertIn("task_count", failures[0])

    def test_expected_final_state_match_passes(self):
        svc = TaskTrackerService()
        svc.dispatch("create_task", {"title": "x"})
        case = self._case(expected_final_state={"task_count": 1, "open_count": 1})
        failures, _info = evaluate_tool_case(case, svc)
        self.assertEqual(failures, [])

    def test_multiple_failures_all_reported_not_just_the_first(self):
        svc = TaskTrackerService()
        case = self._case(
            expected_calls=[{"tool": "create_task"}],
            expected_final_state={"task_count": 5},
        )
        failures, _info = evaluate_tool_case(case, svc)
        self.assertEqual(len(failures), 2)

    def test_unknown_tool_calls_surfaced_in_oracle_info(self):
        svc = TaskTrackerService()
        svc.dispatch("frobnicate", {})
        _failures, info = evaluate_tool_case(self._case(), svc)
        self.assertEqual(info["n_unknown_calls"], 1)


class ToolCaseSchemaTests(unittest.TestCase):
    def _case(self, **kw):
        case = {"name": "c", "tool_service": "task_tracker",
                "expected_calls": [{"tool": "create_task"}]}
        case.update(kw)
        return case

    def test_minimal_valid_tool_case_passes(self):
        validate_case(self._case())   # must not raise

    def test_tool_service_must_be_string(self):
        with self.assertRaises(SchemaError):
            validate_case(self._case(tool_service=123))

    def test_tools_must_be_list_of_strings(self):
        with self.assertRaises(SchemaError):
            validate_case(self._case(tools="create_task"))

    def test_max_tool_turns_out_of_range_rejected(self):
        with self.assertRaises(SchemaError):
            validate_case(self._case(max_tool_turns=0))
        with self.assertRaises(SchemaError):
            validate_case(self._case(max_tool_turns=21))

    def test_expected_calls_entry_requires_tool_key(self):
        with self.assertRaises(SchemaError):
            validate_case(self._case(expected_calls=[{"arguments_contains": {}}]))

    def test_expected_calls_entry_rejects_unknown_key(self):
        with self.assertRaises(SchemaError):
            validate_case(self._case(expected_calls=[{"tool": "x", "bogus": 1}]))

    def test_arguments_contains_must_be_object(self):
        with self.assertRaises(SchemaError):
            validate_case(self._case(expected_calls=[{"tool": "x", "arguments_contains": "nope"}]))

    def test_expected_final_state_must_be_object(self):
        with self.assertRaises(SchemaError):
            validate_case(self._case(expected_final_state=["nope"]))

    def test_shipped_tool_cases_all_load_and_validate(self):
        cases = load_cases()
        tool_cases = [c for c in cases if c.get("tool_service")]
        self.assertGreaterEqual(len(tool_cases), 5)
        for case in tool_cases:
            self.assertTrue(case.get("expected_calls") or case.get("expected_final_state"),
                            f"{case['name']} has no oracle assertions at all")


class _ToolStubBackend:
    """A real HTTP server on localhost serving scripted tool-calling turns,
    in order - each entry in ``script`` is either
    ``{"tool_calls": [{"name": ..., "arguments": {...}}, ...]}`` or
    ``{"content": "..."}`` (the model's final, non-tool-calling reply).
    Serves both the OpenAI-compatible and Ollama-native tool_calls shapes
    from the SAME script, so one script drives both driver variants.
    """

    def __init__(self, script: list[dict]):
        self.script = script
        self.requests: list[dict] = []
        self._call_count = 0
        stub = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):                          # noqa: N802 - stdlib signature
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                stub.requests.append({"path": self.path, "body": body})
                step = stub.script[min(stub._call_count, len(stub.script) - 1)]
                stub._call_count += 1
                is_ollama = self.path.endswith("/api/chat")
                if "tool_calls" in step:
                    if is_ollama:
                        message = {
                            "role": "assistant", "content": "",
                            "tool_calls": [
                                {"function": {"name": tc["name"], "arguments": tc["arguments"]}}
                                for tc in step["tool_calls"]
                            ],
                        }
                    else:
                        message = {
                            "role": "assistant", "content": None,
                            "tool_calls": [
                                {"id": f"call_{i}", "type": "function",
                                 "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])}}
                                for i, tc in enumerate(step["tool_calls"])
                            ],
                        }
                else:
                    message = {"role": "assistant", "content": step.get("content", "Done.")}
                if is_ollama:
                    payload = {"message": message, "prompt_eval_count": 5, "eval_count": 3}
                else:
                    payload = {"choices": [{"message": message}],
                              "usage": {"prompt_tokens": 5, "completion_tokens": 3}}
                raw = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):               # noqa: A003
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()


class ToolChatDriverEndToEndTests(unittest.TestCase):
    """openai-tools/ollama-tools, run_case() end to end against a real
    (stub) HTTP backend - same shape as BaselineDriverEndToEndTests in
    test_optarena.py for the coding-case baseline drivers."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_tools_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def _case(self, **kw):
        case = {
            "name": "c", "tool_service": "task_tracker",
            "prompts": ["do the thing"],
            "expected_calls": [{"tool": "create_task", "arguments_contains": {"title": "Buy milk"}}],
        }
        case.update(kw)
        return case

    def test_openai_tools_full_loop_passes(self):
        from optarena.drivers.tool_chat import ToolChatDriver
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "create_task", "arguments": {"title": "Buy milk"}}]},
            {"content": "Done."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(self._case(), scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual(result.extra["oracle"]["n_calls"], 1)
        self.assertEqual(result.extra["prompt_tokens"], 10)      # 5 + 5, two turns
        self.assertEqual(result.extra["completion_tokens"], 6)   # 3 + 3
        self.assertTrue(backend.requests[0]["path"].endswith("/v1/chat/completions"))

    def test_ollama_tools_full_loop_passes(self):
        from optarena.drivers.tool_chat import OllamaToolChatDriver
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "create_task", "arguments": {"title": "Buy milk"}}]},
            {"content": "Done."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="ollama-tools",
                            backend=Backend(kind="ollama", base_url=backend.base_url, model="m"))
        result = OllamaToolChatDriver().run_case(self._case(), scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertTrue(backend.requests[0]["path"].endswith("/api/chat"))

    def test_multi_turn_dispatches_each_call_and_carries_id_from_first_call(self):
        from optarena.drivers.tool_chat import ToolChatDriver
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "create_task", "arguments": {"title": "Ship it"}}]},
            {"tool_calls": [{"name": "complete_task", "arguments": {"task_id": 1}}]},
            {"content": "Done."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        case = self._case(expected_calls=[{"tool": "create_task"}, {"tool": "complete_task"}],
                          expected_final_state={"completed_count": 1})
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual(len(result.extra["tool_calls"]), 2)

    def test_hallucinated_tool_call_is_a_logged_error_not_a_crash(self):
        from optarena.drivers.tool_chat import ToolChatDriver
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "not_a_real_tool", "arguments": {}}]},
            {"content": "Done."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(self._case(expected_calls=[]), scenario, self.ws)
        self.assertIsNone(result.error)
        self.assertEqual(result.extra["oracle"]["n_unknown_calls"], 1)

    def test_model_that_never_stops_calling_tools_hits_the_turn_limit(self):
        from optarena.drivers.tool_chat import ToolChatDriver
        # Every turn returns another tool call - never a plain-content reply.
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "list_tasks", "arguments": {}}]},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        case = self._case(expected_calls=[], max_tool_turns=2)
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertIsNone(result.error)
        self.assertTrue(result.extra["hit_turn_limit"])
        self.assertEqual(len(result.extra["tool_calls"]), 2)   # exactly max_tool_turns dispatches

    def test_unknown_tool_service_reports_a_clean_error_not_a_crash(self):
        from optarena.drivers.tool_chat import ToolChatDriver
        backend = _ToolStubBackend([{"content": "n/a"}])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(self._case(tool_service="does_not_exist"), scenario, self.ws)
        self.assertFalse(result.passed)
        self.assertIsNotNone(result.error)
        self.assertIn("does_not_exist", result.error)

    def test_missing_tool_service_field_reports_a_clean_error(self):
        from optarena.drivers.tool_chat import ToolChatDriver
        backend = _ToolStubBackend([{"content": "n/a"}])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        case = {"name": "c", "prompts": ["hi"]}   # no tool_service at all
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertFalse(result.passed)
        self.assertIn("tool_service", result.error)

    def test_wrong_call_still_fails_the_oracle(self):
        """The discriminating half of the corpus philosophy: a case must be
        able to FAIL, not just pass - here, the model creates the wrong task."""
        from optarena.drivers.tool_chat import ToolChatDriver
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "create_task", "arguments": {"title": "Wrong title"}}]},
            {"content": "Done."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(self._case(), scenario, self.ws)
        self.assertFalse(result.passed)
        self.assertEqual(len(result.failures), 1)


if __name__ == "__main__":
    unittest.main()
