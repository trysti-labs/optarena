"""
build_tools mock-service tests: the 13-tool BuildToolsService itself (a
mock Nx workspace plus Nx Cloud, mirroring the real official nrwl/
nx-console's bundled nx-mcp server's actual tool registrations - extracted
directly from its source). No build-tool ecosystem surveyed this session
(Gradle, Maven, Bazel, Cargo, CMake, Python packaging, ...) had an
official or dominant real implementation except Nx - see
DEV_NOTES/MCP_IMPLEMENTATION_GAPS.md for the full survey. Covers the real
type-dependent precondition on nx_visualize_graph (project requires
projectName; project-task requires both projectName and taskName;
full-project-graph requires neither), the real three-way fix
identification on update_self_healing_fix (aiFixId, shortLink, or
branch), its seed() mechanism, and all 13 shipped tool_bt_* cases - each
dry-run against a hand-built "ideal" trajectory (must PASS) and at least
one realistic wrong trajectory (must FAIL), same discriminating-oracle
philosophy as the other services.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from optarena._cases._mock_service import BuildToolsService, get_mock_service, get_tool_schemas
from optarena._cases._tool_evaluate import evaluate_tool_case
from optarena.cases import load_cases
from optarena.schema import SchemaError, validate_case
from optarena.scenario import Backend, Scenario
from tests.test_tool_use_cases import _ToolStubBackend


class BuildToolsServiceBasicsTests(unittest.TestCase):
    def test_all_13_tools_registered(self):
        schemas = get_tool_schemas("build_tools")
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(len(names), 13)
        self.assertEqual(names, set(BuildToolsService.TOOLS))

    def test_project_details_refuses_unknown_project(self):
        svc = BuildToolsService()
        result = svc.dispatch("nx_project_details", {"projectName": "nope"})
        self.assertIn("error", result)

    def test_generator_schema_refuses_unknown_generator(self):
        svc = BuildToolsService()
        result = svc.dispatch("nx_generator_schema", {"generatorName": "nope"})
        self.assertIn("error", result)

    def test_visualize_graph_project_requires_project_name(self):
        svc = BuildToolsService()
        result = svc.dispatch("nx_visualize_graph", {"visualizationType": "project"})
        self.assertIn("error", result)

    def test_visualize_graph_project_task_requires_task_name(self):
        svc = BuildToolsService()
        result = svc.dispatch("nx_visualize_graph", {"visualizationType": "project-task", "projectName": "web-app"})
        self.assertIn("error", result)

    def test_visualize_graph_project_task_requires_project_name(self):
        svc = BuildToolsService()
        result = svc.dispatch("nx_visualize_graph", {"visualizationType": "project-task", "taskName": "build"})
        self.assertIn("error", result)

    def test_visualize_graph_full_project_graph_needs_nothing(self):
        svc = BuildToolsService()
        result = svc.dispatch("nx_visualize_graph", {"visualizationType": "full-project-graph"})
        self.assertNotIn("error", result)

    def test_visualize_graph_refuses_unknown_type(self):
        svc = BuildToolsService()
        result = svc.dispatch("nx_visualize_graph", {"visualizationType": "bogus"})
        self.assertIn("error", result)

    def test_current_running_task_output_refuses_unknown_task(self):
        svc = BuildToolsService()
        result = svc.dispatch("nx_current_running_task_output", {"taskId": "nope"})
        self.assertIn("error", result)

    def test_ci_information_refuses_unknown_branch(self):
        svc = BuildToolsService()
        result = svc.dispatch("ci_information", {"branch": "nope"})
        self.assertIn("error", result)

    def test_ci_task_output_refuses_unknown_task(self):
        svc = BuildToolsService()
        result = svc.dispatch("ci_task_output", {"taskId": "nope"})
        self.assertIn("error", result)

    def test_update_self_healing_fix_refuses_unknown_action(self):
        svc = BuildToolsService()
        result = svc.dispatch("update_self_healing_fix", {"action": "BOGUS"})
        self.assertIn("error", result)

    def test_update_self_healing_fix_refuses_when_unresolvable(self):
        svc = BuildToolsService()
        result = svc.dispatch("update_self_healing_fix", {"action": "APPLY"})
        self.assertIn("error", result)

    def test_update_self_healing_fix_resolves_via_short_link(self):
        svc = BuildToolsService()
        svc.seed({"cipes": {"main": {"fixes": [{"ai_fix_id": "fix-1", "short_link": "abc-def"}]}}})
        result = svc.dispatch("update_self_healing_fix", {"shortLink": "abc-def", "action": "APPLY"})
        self.assertEqual(result["aiFixId"], "fix-1")

    def test_update_self_healing_fix_resolves_via_current_branch(self):
        svc = BuildToolsService()
        svc.seed({"current_branch": "feature-x",
                  "cipes": {"feature-x": {"fixes": [{"ai_fix_id": "fix-1", "short_link": "abc-def"}]}}})
        result = svc.dispatch("update_self_healing_fix", {"action": "REJECT"})
        self.assertEqual(result["aiFixId"], "fix-1")


class BuildToolsSeedTests(unittest.TestCase):
    def test_seed_is_never_logged_to_call_log(self):
        svc = BuildToolsService()
        svc.seed({"projects": {"web-app": {}}})
        self.assertEqual(svc.call_log, [])

    def test_seeded_project_dependencies_split_internal_external(self):
        svc = BuildToolsService()
        svc.seed({"projects": {"web-app": {}, "ui-lib": {}}, "dependencies": {"web-app": ["ui-lib", "lodash"]}})
        result = svc.dispatch("nx_project_details", {"projectName": "web-app"})
        self.assertEqual(result["projectDependencies"], ["ui-lib"])
        self.assertEqual(result["externalDependencies"], ["lodash"])

    def test_seeded_workspace_filter_matches_tags(self):
        svc = BuildToolsService()
        svc.seed({"projects": {"web-app": {"tags": ["type:app"]}, "ui-lib": {"tags": ["type:lib"]}}})
        result = svc.dispatch("nx_workspace", {"filter": "type:lib"})
        self.assertEqual([p["name"] for p in result["projects"]], ["ui-lib"])


class BuildToolsCaseSchemaTests(unittest.TestCase):
    def test_minimal_valid_build_tools_case_passes(self):
        case = {"name": "c", "tool_service": "build_tools",
                "expected_calls": [{"tool": "nx_available_plugins"}]}
        validate_case(case)


class ShippedBuildToolsCaseDryRunTests(unittest.TestCase):
    """Every tool_bt_* case, run against a hand-built ideal trajectory
    (must PASS) and at least one realistic wrong trajectory (must FAIL)."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["name"]: c for c in load_cases() if c.get("tool_service") == "build_tools"}

    def _run(self, case_name: str, calls: list[tuple[str, dict]]) -> list[str]:
        case = self.cases[case_name]
        svc = get_mock_service(case["tool_service"])()
        svc.seed(case.get("tool_service_seed") or {})
        for tool, args in calls:
            svc.dispatch(tool, args)
        failures, _info = evaluate_tool_case(case, svc)
        return failures

    def test_all_13_build_tools_cases_shipped(self):
        self.assertEqual(len(self.cases), 13)

    def test_orientation_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_bt_orientation", [('nx_workspace_path', {}), ('nx_workspace', {})]), [])
        self.assertTrue(self._run("tool_bt_orientation", [('nx_workspace_path', {})]))

    def test_inspect_project_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_bt_inspect_project", [('nx_project_details', {'projectName': 'web-app'})]), [])
        self.assertTrue(self._run("tool_bt_inspect_project", []))

    def test_find_generator_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_bt_find_generator",
            [('nx_generators', {}), ('nx_generator_schema', {'generatorName': '@nx/react:component'})]), [])
        self.assertTrue(self._run("tool_bt_find_generator", [('nx_generators', {})]))

    def test_visualize_project_graph_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_bt_visualize_project_graph",
            [('nx_visualize_graph', {'visualizationType': 'project', 'projectName': 'web-app'})]), [])
        self.assertTrue(self._run("tool_bt_visualize_project_graph",
            [('nx_visualize_graph', {'visualizationType': 'full-project-graph'})]))

    def test_visualize_task_graph_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_bt_visualize_task_graph",
            [('nx_visualize_graph', {'visualizationType': 'project-task', 'projectName': 'web-app', 'taskName': 'build'})]), [])
        self.assertTrue(self._run("tool_bt_visualize_task_graph",
            [('nx_visualize_graph', {'visualizationType': 'project', 'projectName': 'web-app'})]))

    def test_visualize_full_graph_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_bt_visualize_full_graph",
            [('nx_visualize_graph', {'visualizationType': 'full-project-graph'})]), [])
        self.assertTrue(self._run("tool_bt_visualize_full_graph", []))

    def test_check_docs_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_bt_check_docs", [('nx_docs', {'userQuery': 'how do targets work'})]), [])
        self.assertTrue(self._run("tool_bt_check_docs", []))

    def test_check_plugins_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_bt_check_plugins", [('nx_available_plugins', {})]), [])
        self.assertTrue(self._run("tool_bt_check_plugins", []))

    def test_check_running_task_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_bt_check_running_task",
            [('nx_current_running_tasks_details', {}), ('nx_current_running_task_output', {'taskId': 'web-app:build'})]), [])
        self.assertTrue(self._run("tool_bt_check_running_task", [('nx_current_running_tasks_details', {})]))

    def test_check_ci_status_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_bt_check_ci_status", [('ci_information', {})]), [])
        self.assertTrue(self._run("tool_bt_check_ci_status", []))

    def test_debug_ci_failure_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_bt_debug_ci_failure",
            [('ci_information', {}), ('ci_task_output', {'taskId': 'web-app:test'})]), [])
        self.assertTrue(self._run("tool_bt_debug_ci_failure", [('ci_information', {})]))

    def test_apply_self_healing_fix_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_bt_apply_self_healing_fix", [('update_self_healing_fix', {'action': 'APPLY'})]), [])
        self.assertTrue(self._run("tool_bt_apply_self_healing_fix", [('update_self_healing_fix', {'action': 'REJECT'})]))

    def test_reject_self_healing_fix_via_link_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_bt_reject_self_healing_fix_via_link",
            [('update_self_healing_fix', {'shortLink': 'xyz-789', 'action': 'REJECT'})]), [])
        self.assertTrue(self._run("tool_bt_reject_self_healing_fix_via_link",
            [('update_self_healing_fix', {'shortLink': 'xyz-789', 'action': 'APPLY'})]))


class BuildToolsCaseDriverEndToEndTests(unittest.TestCase):
    """Proves tool_service_seed reaches the mock service through the real
    driver's run_case(), same proof pattern the other services have."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_build_tools_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_seeded_project_reachable_via_the_real_driver(self):
        from optarena.drivers.tool_chat import ToolChatDriver

        case = {
            "name": "c", "tool_service": "build_tools",
            "tools": ["nx_project_details"],
            "tool_service_seed": {"projects": {"web-app": {"tags": ["type:app"]}}},
            "prompts": ["show me the web-app project details"],
            "expected_calls": [{"tool": "nx_project_details"}],
        }
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "nx_project_details", "arguments": {"projectName": "web-app"}}]},
            {"content": "web-app is tagged type:app."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual(result.extra["tool_calls"][0]["result"]["tags"], ["type:app"])


if __name__ == "__main__":
    unittest.main()
