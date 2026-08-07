"""
database mock-service tests: the 9-tool DatabaseService itself (a mock
PostgreSQL instance, mirroring the real crystaldba/postgres-mcp's actual
tool registrations - extracted directly from its source, not its README),
its access_mode-gated execute_sql precondition (restricted mode refuses
write statements, unrestricted allows everything - not agent-discoverable
in advance so it is covered only by direct unit tests here, not by a
tool_db_* case), its seed() mechanism, and all 8 shipped tool_db_* cases -
each dry-run against a hand-built "ideal" trajectory (must PASS) and at
least one realistic wrong trajectory (must FAIL), same discriminating-
oracle philosophy as the other services.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from optarena._cases._mock_service import DatabaseService, get_mock_service, get_tool_schemas
from optarena._cases._tool_evaluate import evaluate_tool_case
from optarena.cases import load_cases
from optarena.schema import SchemaError, validate_case
from optarena.scenario import Backend, Scenario
from tests.test_tool_use_cases import _ToolStubBackend


class DatabaseServiceBasicsTests(unittest.TestCase):
    def test_all_9_tools_registered(self):
        schemas = get_tool_schemas("database")
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(len(names), 9)
        self.assertEqual(names, set(DatabaseService.TOOLS))

    def test_list_objects_refuses_unknown_schema(self):
        svc = DatabaseService()
        result = svc.dispatch("list_objects", {"schema_name": "nope"})
        self.assertIn("error", result)

    def test_get_object_details_refuses_unknown_table(self):
        svc = DatabaseService()
        svc.seed({"tables": [{"schema": "public", "name": "orders"}]})
        result = svc.dispatch("get_object_details", {"schema_name": "public", "object_name": "users"})
        self.assertIn("error", result)

    def test_execute_sql_allows_writes_by_default(self):
        svc = DatabaseService()
        result = svc.dispatch("execute_sql", {"sql": "DELETE FROM orders"})
        self.assertNotIn("error", result)

    def test_execute_sql_refuses_writes_in_restricted_mode(self):
        svc = DatabaseService()
        svc.seed({"access_mode": "restricted"})
        result = svc.dispatch("execute_sql", {"sql": "DELETE FROM orders"})
        self.assertIn("error", result)

    def test_execute_sql_allows_reads_in_restricted_mode(self):
        svc = DatabaseService()
        svc.seed({"access_mode": "restricted"})
        result = svc.dispatch("execute_sql", {"sql": "SELECT * FROM orders"})
        self.assertNotIn("error", result)

    def test_explain_query_refuses_analyze_with_hypothetical_indexes(self):
        svc = DatabaseService()
        result = svc.dispatch("explain_query", {"sql": "SELECT 1", "analyze": True,
                                                  "hypothetical_indexes": [{"table": "orders", "columns": ["id"]}]})
        self.assertIn("error", result)

    def test_analyze_query_indexes_refuses_empty_list(self):
        svc = DatabaseService()
        result = svc.dispatch("analyze_query_indexes", {"queries": []})
        self.assertIn("error", result)

    def test_analyze_query_indexes_refuses_more_than_ten(self):
        svc = DatabaseService()
        result = svc.dispatch("analyze_query_indexes", {"queries": ["SELECT 1"] * 11})
        self.assertIn("error", result)

    def test_analyze_db_health_refuses_unknown_health_type(self):
        svc = DatabaseService()
        result = svc.dispatch("analyze_db_health", {"health_type": "bogus"})
        self.assertIn("error", result)

    def test_get_top_queries_refuses_unknown_sort_by(self):
        svc = DatabaseService()
        result = svc.dispatch("get_top_queries", {"sort_by": "bogus"})
        self.assertIn("error", result)


class DatabaseSeedTests(unittest.TestCase):
    def test_seed_is_never_logged_to_call_log(self):
        svc = DatabaseService()
        svc.seed({"tables": [{"schema": "public", "name": "orders"}]})
        self.assertEqual(svc.call_log, [])

    def test_seeding_a_table_auto_registers_its_schema(self):
        svc = DatabaseService()
        svc.seed({"tables": [{"schema": "app", "name": "orders"}]})
        self.assertIn("app", {s["name"] for s in svc.dispatch("list_schemas", {})["schemas"]})

    def test_seeded_top_queries_reachable(self):
        svc = DatabaseService()
        svc.seed({"top_queries": [{"query": "SELECT 1", "total_time": 10.0, "mean_time": 1.0, "calls": 10}]})
        result = svc.dispatch("get_top_queries", {})
        self.assertEqual(result["queries"][0]["query"], "SELECT 1")

    def test_seeded_health_findings_reachable(self):
        svc = DatabaseService()
        svc.seed({"health_findings": {"vacuum": ["table orders needs a vacuum"]}})
        result = svc.dispatch("analyze_db_health", {"health_type": "vacuum"})
        self.assertEqual(result["findings"]["vacuum"], ["table orders needs a vacuum"])


class DatabaseCaseSchemaTests(unittest.TestCase):
    def test_minimal_valid_database_case_passes(self):
        case = {"name": "c", "tool_service": "database",
                "expected_calls": [{"tool": "list_schemas"}]}
        validate_case(case)


class ShippedDatabaseCaseDryRunTests(unittest.TestCase):
    """Every tool_db_* case, run against a hand-built ideal trajectory
    (must PASS) and at least one realistic wrong trajectory (must FAIL)."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["name"]: c for c in load_cases() if c.get("tool_service") == "database"}

    def _run(self, case_name: str, calls: list[tuple[str, dict]]) -> list[str]:
        case = self.cases[case_name]
        svc = get_mock_service(case["tool_service"])()
        svc.seed(case.get("tool_service_seed") or {})
        for tool, args in calls:
            svc.dispatch(tool, args)
        failures, _info = evaluate_tool_case(case, svc)
        return failures

    def test_all_8_database_cases_shipped(self):
        self.assertEqual(len(self.cases), 8)

    def test_explore_schema_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_db_explore_schema", [('list_schemas', {}), ('list_objects', {'schema_name': 'public'})]), [])
        self.assertTrue(self._run("tool_db_explore_schema", [('list_schemas', {})]))

    def test_inspect_table_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_db_inspect_table", [('get_object_details', {'schema_name': 'public', 'object_name': 'orders'})]), [])
        self.assertTrue(self._run("tool_db_inspect_table", []))

    def test_explain_before_running_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_db_explain_before_running", [('explain_query', {'sql': 'SELECT * FROM orders WHERE user_id = 42'}), ('execute_sql', {'sql': 'SELECT * FROM orders WHERE user_id = 42'})]), [])
        self.assertTrue(self._run("tool_db_explain_before_running", [('execute_sql', {'sql': 'SELECT * FROM orders WHERE user_id = 42'})]))

    def test_explain_with_hypothetical_index_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_db_explain_with_hypothetical_index", [('explain_query', {'sql': 'SELECT * FROM orders WHERE user_id = 42', 'hypothetical_indexes': [{'table': 'orders', 'columns': ['user_id']}]})]), [])
        self.assertTrue(self._run("tool_db_explain_with_hypothetical_index", []))

    def test_recommend_indexes_for_workload_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_db_recommend_indexes_for_workload", [('analyze_workload_indexes', {})]), [])
        self.assertTrue(self._run("tool_db_recommend_indexes_for_workload", []))

    def test_recommend_indexes_for_queries_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_db_recommend_indexes_for_queries", [('analyze_query_indexes', {'queries': ["SELECT * FROM orders WHERE created_at > now() - interval '7 days'"]})]), [])
        self.assertTrue(self._run("tool_db_recommend_indexes_for_queries", []))

    def test_check_health_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_db_check_health", [('analyze_db_health', {})]), [])
        self.assertTrue(self._run("tool_db_check_health", []))

    def test_find_slow_queries_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_db_find_slow_queries", [('get_top_queries', {'sort_by': 'total_time'})]), [])
        self.assertTrue(self._run("tool_db_find_slow_queries", []))


class DatabaseCaseDriverEndToEndTests(unittest.TestCase):
    """Proves tool_service_seed reaches the mock service through the real
    driver's run_case(), same proof pattern the other services have."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_database_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_seeded_table_reachable_via_the_real_driver(self):
        from optarena.drivers.tool_chat import ToolChatDriver

        case = {
            "name": "c", "tool_service": "database",
            "tools": ["list_objects"],
            "tool_service_seed": {"tables": [{"schema": "public", "name": "orders"}]},
            "prompts": ["list tables in the public schema"],
            "expected_calls": [{"tool": "list_objects"}],
        }
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "list_objects", "arguments": {"schema_name": "public"}}]},
            {"content": "There's one table: orders."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual(result.extra["tool_calls"][0]["result"]["objects"][0]["name"], "orders")


if __name__ == "__main__":
    unittest.main()
