"""
ci_pipeline mock-service tests: the 13-tool CIPipelineService itself (a
mock CircleCI instance, mirroring the real official CircleCI-Public/
mcp-server-circleci's actual `CCI_TOOLS`/`CCI_HANDLERS` registrations -
extracted directly from its source, not a lower-traction Jenkins
alternative, since no Jenkins MCP server has real adoption while
CircleCI's is vendor-published), its project-identification precondition
(most project-scoped tools require projectSlug+branch, a parseable
projectURL, or workspaceRoot+gitRemoteURL(+branch) - never an opaque id,
so the discipline being tested is "supplied a complete identification
method" rather than "used the right identifier"), its seed() mechanism,
and all 13 shipped tool_ci_* cases - each dry-run against a hand-built
"ideal" trajectory (must PASS) and at least one realistic wrong
trajectory (must FAIL), same discriminating-oracle philosophy as the
other services.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from optarena._cases._mock_service import CIPipelineService, get_mock_service, get_tool_schemas
from optarena._cases._tool_evaluate import evaluate_tool_case
from optarena.cases import load_cases
from optarena.schema import SchemaError, validate_case
from optarena.scenario import Backend, Scenario
from tests.test_tool_use_cases import _ToolStubBackend


class CIPipelineServiceBasicsTests(unittest.TestCase):
    def test_all_13_tools_registered(self):
        schemas = get_tool_schemas("ci_pipeline")
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(len(names), 13)
        self.assertEqual(names, set(CIPipelineService.TOOLS))

    def test_project_scoped_tool_refuses_with_no_identification(self):
        svc = CIPipelineService()
        result = svc.dispatch("get_latest_pipeline_status", {})
        self.assertIn("error", result)

    def test_project_slug_requires_branch(self):
        svc = CIPipelineService()
        svc.seed({"projects": {"gh/acme/webapp": {}}})
        result = svc.dispatch("get_latest_pipeline_status", {"projectSlug": "gh/acme/webapp"})
        self.assertIn("error", result)

    def test_project_slug_must_be_a_followed_project(self):
        svc = CIPipelineService()
        result = svc.dispatch("get_latest_pipeline_status", {"projectSlug": "gh/acme/webapp", "branch": "main"})
        self.assertIn("error", result)

    def test_project_url_is_parsed_to_a_slug(self):
        svc = CIPipelineService()
        svc.seed({"pipeline_status": [{"project_slug": "gh/acme/webapp", "branch": "main", "status": "success"}]})
        result = svc.dispatch("get_latest_pipeline_status",
                               {"projectURL": "https://app.circleci.com/pipelines/gh/acme/webapp/123", "branch": "main"})
        self.assertEqual(result.get("status"), "success")

    def test_find_flaky_tests_does_not_require_branch(self):
        svc = CIPipelineService()
        svc.seed({"projects": {"gh/acme/webapp": {}}, "flaky_tests": {"gh/acme/webapp": ["test_a"]}})
        result = svc.dispatch("find_flaky_tests", {"projectSlug": "gh/acme/webapp"})
        self.assertEqual(result["flaky_tests"], ["test_a"])

    def test_run_pipeline_with_multiple_definitions_requires_a_choice(self):
        svc = CIPipelineService()
        svc.seed({"projects": {"gh/acme/webapp": {}},
                  "pipeline_definitions": {"gh/acme/webapp": ["deploy", "test-only"]}})
        result = svc.dispatch("run_pipeline", {"projectSlug": "gh/acme/webapp", "branch": "main"})
        self.assertIn("error", result)
        self.assertEqual(result["available_pipelines"], ["deploy", "test-only"])

    def test_run_pipeline_with_valid_choice_succeeds(self):
        svc = CIPipelineService()
        svc.seed({"projects": {"gh/acme/webapp": {}},
                  "pipeline_definitions": {"gh/acme/webapp": ["deploy", "test-only"]}})
        result = svc.dispatch("run_pipeline", {"projectSlug": "gh/acme/webapp", "branch": "main",
                                                "pipelineChoiceName": "deploy"})
        self.assertNotIn("error", result)

    def test_rerun_workflow_refuses_unknown_id(self):
        svc = CIPipelineService()
        result = svc.dispatch("rerun_workflow", {"workflowId": "nope"})
        self.assertIn("error", result)

    def test_run_rollback_pipeline_refuses_unconfigured_project(self):
        svc = CIPipelineService()
        svc.seed({"projects": {"gh/acme/webapp": {}}})
        result = svc.dispatch("run_rollback_pipeline", {
            "projectSlug": "gh/acme/webapp", "environmentName": "prod", "componentName": "fe",
            "currentVersion": "1", "targetVersion": "2", "namespace": "ns"})
        self.assertIn("error", result)

    def test_run_rollback_pipeline_resolves_via_project_id(self):
        svc = CIPipelineService()
        svc.seed({"projects": {"gh/acme/webapp": {"id": "proj-1"}}, "rollback_configured": ["gh/acme/webapp"]})
        result = svc.dispatch("run_rollback_pipeline", {
            "projectID": "proj-1", "environmentName": "prod", "componentName": "fe",
            "currentVersion": "1", "targetVersion": "2", "namespace": "ns"})
        self.assertNotIn("error", result)

    def test_list_component_versions_progressive_disclosure(self):
        svc = CIPipelineService()
        svc.seed({
            "projects": {"gh/acme/webapp": {}},
            "environments": {"gh/acme/webapp": [{"id": "env-prod", "name": "production"}]},
            "components": {"gh/acme/webapp": [{"id": "comp-fe", "name": "frontend"}]},
            "component_versions": [{"project_slug": "gh/acme/webapp", "environment_id": "env-prod",
                                     "component_id": "comp-fe", "versions": [{"version": "1.0"}]}],
        })
        no_env = svc.dispatch("list_component_versions", {"projectSlug": "gh/acme/webapp"})
        self.assertIn("environments", no_env)
        no_comp = svc.dispatch("list_component_versions", {"projectSlug": "gh/acme/webapp", "environmentID": "env-prod"})
        self.assertIn("components", no_comp)
        full = svc.dispatch("list_component_versions",
                             {"projectSlug": "gh/acme/webapp", "environmentID": "env-prod", "componentID": "comp-fe"})
        self.assertEqual(full["versions"], [{"version": "1.0"}])

    def test_find_underused_resource_classes_refuses_unknown_csv(self):
        svc = CIPipelineService()
        result = svc.dispatch("find_underused_resource_classes", {"csvFilePath": "/tmp/nope.csv"})
        self.assertIn("error", result)

    def test_download_usage_api_data_refuses_unknown_org(self):
        svc = CIPipelineService()
        result = svc.dispatch("download_usage_api_data", {"orgId": "nope", "outputDir": "/tmp"})
        self.assertIn("error", result)


class CIPipelineSeedTests(unittest.TestCase):
    def test_seed_is_never_logged_to_call_log(self):
        svc = CIPipelineService()
        svc.seed({"projects": {"gh/acme/webapp": {}}})
        self.assertEqual(svc.call_log, [])

    def test_seeded_git_remote_resolves_a_project(self):
        svc = CIPipelineService()
        svc.seed({"projects": {"gh/acme/webapp": {}},
                  "git_remotes": {"https://github.com/acme/webapp.git": "gh/acme/webapp"},
                  "pipeline_status": [{"project_slug": "gh/acme/webapp", "branch": "main", "status": "success"}]})
        result = svc.dispatch("get_latest_pipeline_status", {
            "workspaceRoot": "/home/user/webapp", "gitRemoteURL": "https://github.com/acme/webapp.git", "branch": "main"})
        self.assertEqual(result["status"], "success")


class CIPipelineCaseSchemaTests(unittest.TestCase):
    def test_minimal_valid_ci_pipeline_case_passes(self):
        case = {"name": "c", "tool_service": "ci_pipeline",
                "expected_calls": [{"tool": "list_followed_projects"}]}
        validate_case(case)


class ShippedCIPipelineCaseDryRunTests(unittest.TestCase):
    """Every tool_ci_* case, run against a hand-built ideal trajectory
    (must PASS) and at least one realistic wrong trajectory (must FAIL)."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["name"]: c for c in load_cases() if c.get("tool_service") == "ci_pipeline"}

    def _run(self, case_name: str, calls: list[tuple[str, dict]]) -> list[str]:
        case = self.cases[case_name]
        svc = get_mock_service(case["tool_service"])()
        svc.seed(case.get("tool_service_seed") or {})
        for tool, args in calls:
            svc.dispatch(tool, args)
        failures, _info = evaluate_tool_case(case, svc)
        return failures

    def test_all_13_ci_pipeline_cases_shipped(self):
        self.assertEqual(len(self.cases), 13)

    def test_list_projects_and_check_status_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_ci_list_projects_and_check_status",
            [('list_followed_projects', {}), ('get_latest_pipeline_status', {'projectSlug': 'gh/acme/webapp', 'branch': 'main'})]), [])
        self.assertTrue(self._run("tool_ci_list_projects_and_check_status", [('list_followed_projects', {})]))

    def test_debug_build_failure_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_ci_debug_build_failure",
            [('get_build_failure_logs', {'projectSlug': 'gh/acme/webapp', 'branch': 'feature-login'})]), [])
        self.assertTrue(self._run("tool_ci_debug_build_failure", []))

    def test_check_failed_tests_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_ci_check_failed_tests",
            [('get_job_test_results', {'projectSlug': 'gh/acme/webapp', 'branch': 'main', 'filterByTestsResult': 'failure'})]), [])
        self.assertTrue(self._run("tool_ci_check_failed_tests", []))

    def test_find_flaky_tests_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_ci_find_flaky_tests", [('find_flaky_tests', {'projectSlug': 'gh/acme/webapp'})]), [])
        self.assertTrue(self._run("tool_ci_find_flaky_tests", []))

    def test_list_build_artifacts_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_ci_list_build_artifacts",
            [('list_artifacts', {'projectSlug': 'gh/acme/webapp', 'branch': 'main'})]), [])
        self.assertTrue(self._run("tool_ci_list_build_artifacts", []))

    def test_validate_config_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_ci_validate_config",
            [('config_helper', {'configFile': 'version: 2.1\njobs:\n  build: {}\n'})]), [])
        self.assertTrue(self._run("tool_ci_validate_config", []))

    def test_trigger_pipeline_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_ci_trigger_pipeline",
            [('run_pipeline', {'projectSlug': 'gh/acme/webapp', 'branch': 'main'})]), [])
        self.assertTrue(self._run("tool_ci_trigger_pipeline", []))

    def test_trigger_multi_pipeline_project_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_ci_trigger_multi_pipeline_project",
            [('run_pipeline', {'projectSlug': 'gh/acme/webapp', 'branch': 'main'}),
             ('run_pipeline', {'projectSlug': 'gh/acme/webapp', 'branch': 'main', 'pipelineChoiceName': 'deploy'})]), [])
        self.assertTrue(self._run("tool_ci_trigger_multi_pipeline_project",
            [('run_pipeline', {'projectSlug': 'gh/acme/webapp', 'branch': 'main'})]))

    def test_rerun_failed_workflow_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_ci_rerun_failed_workflow",
            [('rerun_workflow', {'workflowId': 'wf-1', 'fromFailed': True})]), [])
        self.assertTrue(self._run("tool_ci_rerun_failed_workflow", []))

    def test_rollback_component_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_ci_rollback_component",
            [('run_rollback_pipeline', {'projectSlug': 'gh/acme/webapp', 'environmentName': 'production',
                                         'componentName': 'frontend', 'currentVersion': '1.2.0',
                                         'targetVersion': '1.1.0', 'namespace': 'ns-1'})]), [])
        self.assertTrue(self._run("tool_ci_rollback_component", []))

    def test_explore_component_versions_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_ci_explore_component_versions",
            [('list_component_versions', {'projectSlug': 'gh/acme/webapp'}),
             ('list_component_versions', {'projectSlug': 'gh/acme/webapp', 'environmentID': 'env-prod'}),
             ('list_component_versions', {'projectSlug': 'gh/acme/webapp', 'environmentID': 'env-prod', 'componentID': 'comp-fe'})]), [])
        self.assertTrue(self._run("tool_ci_explore_component_versions",
            [('list_component_versions', {'projectSlug': 'gh/acme/webapp'})]))

    def test_download_usage_data_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_ci_download_usage_data",
            [('download_usage_api_data', {'orgId': 'org-1', 'outputDir': '/tmp'})]), [])
        self.assertTrue(self._run("tool_ci_download_usage_data", []))

    def test_find_underused_resources_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_ci_find_underused_resources",
            [('download_usage_api_data', {'orgId': 'org-1', 'outputDir': '/tmp'}),
             ('find_underused_resource_classes', {'csvFilePath': '/tmp/usage_org-1.csv'})]), [])
        self.assertTrue(self._run("tool_ci_find_underused_resources",
            [('download_usage_api_data', {'orgId': 'org-1', 'outputDir': '/tmp'})]))


class CIPipelineCaseDriverEndToEndTests(unittest.TestCase):
    """Proves tool_service_seed reaches the mock service through the real
    driver's run_case(), same proof pattern the other services have."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_ci_pipeline_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_seeded_project_reachable_via_the_real_driver(self):
        from optarena.drivers.tool_chat import ToolChatDriver

        case = {
            "name": "c", "tool_service": "ci_pipeline",
            "tools": ["list_followed_projects"],
            "tool_service_seed": {"projects": {"gh/acme/webapp": {"name": "webapp"}}},
            "prompts": ["list my circleci projects"],
            "expected_calls": [{"tool": "list_followed_projects"}],
        }
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "list_followed_projects", "arguments": {}}]},
            {"content": "You follow one project: webapp."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual(result.extra["tool_calls"][0]["result"]["projects"][0]["name"], "webapp")


if __name__ == "__main__":
    unittest.main()
