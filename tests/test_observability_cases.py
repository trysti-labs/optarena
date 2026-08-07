"""
observability mock-service tests: the 105-tool ObservabilityService itself
(a mock Grafana instance, mirroring the real official grafana/mcp-grafana's
actual tool registrations across its 30 tool-category source files -
extracted directly from source, by far the largest service in this domain,
bigger than forge's 77). Six tools are registered twice in the real server
(read-only vs. read-write variant); this mock models the full read-write
variant of each. Covers its two-tier depth design (real, source-verified
preconditions for ~22 interactive categories; a shared datasource-type
check plus tool-specific validation for the ~12 structurally-similar
query-connector categories), its seed() mechanism, and all 37 shipped
tool_obs_* cases - each dry-run against a hand-built "ideal" trajectory
(must PASS) and at least one realistic wrong trajectory (must FAIL), same
discriminating-oracle philosophy as the other services.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from optarena._cases._mock_service import ObservabilityService, get_mock_service, get_tool_schemas
from optarena._cases._tool_evaluate import evaluate_tool_case
from optarena.cases import load_cases
from optarena.schema import SchemaError, validate_case
from optarena.scenario import Backend, Scenario
from tests.test_tool_use_cases import _ToolStubBackend


class ObservabilityServiceBasicsTests(unittest.TestCase):
    def test_all_105_tools_registered(self):
        schemas = get_tool_schemas("observability")
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(len(names), 105)
        self.assertEqual(names, set(ObservabilityService.TOOLS))

    def test_get_dashboard_by_uid_refuses_unknown(self):
        svc = ObservabilityService()
        result = svc.dispatch("get_dashboard_by_uid", {"uid": "nope"})
        self.assertIn("error", result)

    def test_update_dashboard_refuses_uid_without_operations(self):
        svc = ObservabilityService()
        result = svc.dispatch("update_dashboard", {"uid": "x"})
        self.assertIn("error", result)

    def test_update_dashboard_refuses_operations_without_uid(self):
        svc = ObservabilityService()
        result = svc.dispatch("update_dashboard", {"operations": [{"op": "replace"}]})
        self.assertIn("error", result)

    def test_update_dashboard_refuses_neither_mode(self):
        svc = ObservabilityService()
        result = svc.dispatch("update_dashboard", {})
        self.assertIn("error", result)

    def test_get_datasource_refuses_neither_uid_nor_name(self):
        svc = ObservabilityService()
        result = svc.dispatch("get_datasource", {})
        self.assertIn("error", result)

    def test_create_datasource_requires_schema_review_and_name(self):
        svc = ObservabilityService()
        result = svc.dispatch("create_datasource", {"type": "prometheus"})
        self.assertTrue(result.get("schemaReviewRequired"))
        self.assertNotIn("uid", result)

    def test_create_datasource_creates_when_confirmed(self):
        svc = ObservabilityService()
        result = svc.dispatch("create_datasource", {"type": "prometheus", "name": "x", "schemaReviewed": True})
        self.assertTrue(result["created"])

    def test_alerting_manage_rules_refuses_missing_create_fields(self):
        svc = ObservabilityService()
        result = svc.dispatch("alerting_manage_rules", {"operation": "create"})
        self.assertIn("error", result)

    def test_alerting_manage_routing_refuses_unknown_operation(self):
        svc = ObservabilityService()
        result = svc.dispatch("alerting_manage_routing", {"operation": "bogus"})
        self.assertIn("error", result)

    def test_create_annotation_requires_text_unless_graphite(self):
        svc = ObservabilityService()
        result = svc.dispatch("create_annotation", {})
        self.assertIn("error", result)

    def test_create_annotation_graphite_requires_what(self):
        svc = ObservabilityService()
        result = svc.dispatch("create_annotation", {"format": "graphite"})
        self.assertIn("error", result)

    def test_create_folder_refuses_empty_title(self):
        svc = ObservabilityService()
        result = svc.dispatch("create_folder", {"title": ""})
        self.assertIn("error", result)

    def test_create_snapshot_refuses_external_without_keys(self):
        svc = ObservabilityService()
        result = svc.dispatch("create_snapshot", {"dashboard": {"title": "x"}, "external": True})
        self.assertIn("error", result)

    def test_install_plugin_requires_confirmation_when_no_version(self):
        svc = ObservabilityService()
        svc.seed({"plugin_catalog": {"p1": {"latest_version": "1.0.0"}}})
        result = svc.dispatch("install_plugin", {"pluginId": "p1"})
        self.assertTrue(result.get("confirmationRequired"))

    def test_generate_deeplink_refuses_both_dashboarduid_and_preview(self):
        svc = ObservabilityService()
        result = svc.dispatch("generate_deeplink", {"resourceType": "dashboard", "dashboardUid": "x",
                                                      "provisioningPreview": {"repo": "r", "path": "p"}})
        self.assertIn("error", result)

    def test_generate_deeplink_refuses_panel_without_panelid(self):
        svc = ObservabilityService()
        result = svc.dispatch("generate_deeplink", {"resourceType": "panel", "dashboardUid": "x"})
        self.assertIn("error", result)

    def test_get_sift_investigation_refuses_non_uuid(self):
        svc = ObservabilityService()
        result = svc.dispatch("get_sift_investigation", {"id": "not-a-uuid"})
        self.assertIn("error", result)

    def test_get_query_examples_refuses_unsupported_type(self):
        svc = ObservabilityService()
        result = svc.dispatch("get_query_examples", {"datasourceType": "bogus"})
        self.assertIn("error", result)

    def test_run_panel_query_refuses_empty_panel_ids(self):
        svc = ObservabilityService()
        result = svc.dispatch("run_panel_query", {"dashboardUid": "x", "panelIds": []})
        self.assertIn("error", result)

    def test_grafana_api_request_refuses_endpoint_without_slash(self):
        svc = ObservabilityService()
        result = svc.dispatch("grafana_api_request", {"endpoint": "api/org"})
        self.assertIn("error", result)

    def test_query_prometheus_refuses_wrong_datasource_type(self):
        svc = ObservabilityService()
        svc.seed({"datasources": {"ds-1": {"name": "x", "type": "loki"}}})
        result = svc.dispatch("query_prometheus", {"datasourceUid": "ds-1", "expr": "up", "queryType": "instant"})
        self.assertIn("error", result)

    def test_query_prometheus_refuses_range_without_step(self):
        svc = ObservabilityService()
        svc.seed({"datasources": {"ds-1": {"name": "x", "type": "prometheus"}}})
        result = svc.dispatch("query_prometheus", {"datasourceUid": "ds-1", "expr": "up", "queryType": "range"})
        self.assertIn("error", result)

    def test_query_prometheus_histogram_refuses_out_of_range_percentile(self):
        svc = ObservabilityService()
        svc.seed({"datasources": {"ds-1": {"name": "x", "type": "prometheus"}}})
        result = svc.dispatch("query_prometheus_histogram", {"datasourceUid": "ds-1", "metric": "m", "percentile": 150})
        self.assertIn("error", result)

    def test_list_clickhouse_tables_refuses_invalid_identifier(self):
        svc = ObservabilityService()
        svc.seed({"datasources": {"ds-1": {"name": "x", "type": "grafana-clickhouse-datasource"}}})
        result = svc.dispatch("list_clickhouse_tables", {"datasourceUid": "ds-1", "database": "bad; drop table"})
        self.assertIn("error", result)

    def test_list_pyroscope_label_names_refuses_reversed_time_range(self):
        svc = ObservabilityService()
        result = svc.dispatch("list_pyroscope_label_names",
                               {"data_source_uid": "ds-1", "start_rfc_3339": "2026-01-02", "end_rfc_3339": "2026-01-01"})
        self.assertIn("error", result)

    def test_datasource_type_check_refuses_wrong_type_for_query_connectors(self):
        svc = ObservabilityService()
        svc.seed({"datasources": {"ds-1": {"name": "x", "type": "prometheus"}}})
        result = svc.dispatch("query_cloudwatch", {"datasourceUid": "ds-1", "namespace": "AWS/EC2",
                                                     "metricName": "CPU", "region": "us-east-1"})
        self.assertIn("error", result)


class ObservabilitySeedTests(unittest.TestCase):
    def test_seed_is_never_logged_to_call_log(self):
        svc = ObservabilityService()
        svc.seed({"dashboards": {"d1": {"title": "x"}}})
        self.assertEqual(svc.call_log, [])

    def test_seeded_snapshot_key_reachable_via_creation(self):
        svc = ObservabilityService()
        result = svc.dispatch("create_snapshot", {"dashboard": {"title": "x"}})
        fetched = svc.dispatch("get_snapshot", {"key": result["key"]})
        self.assertNotIn("error", fetched)


class ObservabilityCaseSchemaTests(unittest.TestCase):
    def test_minimal_valid_observability_case_passes(self):
        case = {"name": "c", "tool_service": "observability",
                "expected_calls": [{"tool": "list_datasources"}]}
        validate_case(case)


class ShippedObservabilityCaseDryRunTests(unittest.TestCase):
    """Every tool_obs_* case, run against a hand-built ideal trajectory
    (must PASS) and at least one realistic wrong trajectory (must FAIL)."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["name"]: c for c in load_cases() if c.get("tool_service") == "observability"}

    def _run(self, case_name: str, calls: list[tuple[str, dict]]) -> list[str]:
        case = self.cases[case_name]
        svc = get_mock_service(case["tool_service"])()
        svc.seed(case.get("tool_service_seed") or {})
        for tool, args in calls:
            svc.dispatch(tool, args)
        failures, _info = evaluate_tool_case(case, svc)
        return failures

    def test_all_37_observability_cases_shipped(self):
        self.assertEqual(len(self.cases), 37)

    def test_orientation_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_orientation",
            [('list_datasources', {}), ('search_dashboards', {'query': 'API Overview'})]), [])
        self.assertTrue(self._run("tool_obs_orientation", [('list_datasources', {})]))

    def test_inspect_dashboard_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_inspect_dashboard", [('get_dashboard_by_uid', {'uid': 'dash-1'})]), [])
        self.assertTrue(self._run("tool_obs_inspect_dashboard", []))

    def test_patch_dashboard_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_patch_dashboard",
            [('update_dashboard', {'uid': 'dash-1', 'operations': [{'op': 'replace', 'path': '/title', 'value': 'x'}]})]), [])
        self.assertTrue(self._run("tool_obs_patch_dashboard", []))

    def test_dashboard_summary_then_panel_queries_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_dashboard_summary_then_panel_queries",
            [('get_dashboard_summary', {'uid': 'dash-1'}), ('get_dashboard_panel_queries', {'uid': 'dash-1', 'panelId': 2})]), [])
        self.assertTrue(self._run("tool_obs_dashboard_summary_then_panel_queries", [('get_dashboard_summary', {'uid': 'dash-1'})]))

    def test_check_alert_rule_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_check_alert_rule", [('alerting_manage_rules', {'operation': 'get', 'rule_uid': 'rule-1'})]), [])
        self.assertTrue(self._run("tool_obs_check_alert_rule", []))

    def test_inspect_routing_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_inspect_routing",
            [('alerting_manage_routing', {'operation': 'get_contact_point', 'contact_point_title': 'oncall-pagerduty'})]), [])
        self.assertTrue(self._run("tool_obs_inspect_routing", []))

    def test_check_datasource_health_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_check_datasource_health", [('check_datasources_health', {})]), [])
        self.assertTrue(self._run("tool_obs_check_datasource_health", []))

    def test_find_datasource_by_name_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_find_datasource_by_name", [('get_datasource', {'name': 'prod-prometheus'})]), [])
        self.assertTrue(self._run("tool_obs_find_datasource_by_name", []))

    def test_create_datasource_needs_confirmation_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_create_datasource_needs_confirmation",
            [('create_datasource', {'type': 'prometheus', 'name': 'staging-prometheus', 'schemaReviewed': True})]), [])
        self.assertTrue(self._run("tool_obs_create_datasource_needs_confirmation", [('create_datasource', {'type': 'prometheus'})]))

    def test_annotate_dashboard_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_annotate_dashboard", [('create_annotation', {'dashboardUid': 'dash-1', 'text': 'Deployed v2.3.0'})]), [])
        self.assertTrue(self._run("tool_obs_annotate_dashboard", []))

    def test_update_annotation_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_update_annotation", [('update_annotation', {'id': 5, 'text': 'Deployed v2.3.1 (hotfix)'})]), [])
        self.assertTrue(self._run("tool_obs_update_annotation", []))

    def test_create_folder_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_create_folder", [('create_folder', {'title': 'Platform Team'})]), [])
        self.assertTrue(self._run("tool_obs_create_folder", []))

    def test_snapshot_workflow_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_snapshot_workflow",
            [('create_snapshot', {'dashboard': {'title': 'API Overview', 'panels': []}}), ('get_snapshot', {'key': 'snap-1'})]), [])
        self.assertTrue(self._run("tool_obs_snapshot_workflow", [('create_snapshot', {'dashboard': {'title': 'API Overview', 'panels': []}})]))

    def test_check_plugin_installed_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_check_plugin_installed", [('get_plugin', {'pluginId': 'grafana-piechart-panel'})]), [])
        self.assertTrue(self._run("tool_obs_check_plugin_installed", []))

    def test_install_plugin_needs_version_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_install_plugin_needs_version",
            [('install_plugin', {'pluginId': 'grafana-clock-panel'}), ('install_plugin', {'pluginId': 'grafana-clock-panel', 'version': '2.1.3'})]), [])
        self.assertTrue(self._run("tool_obs_install_plugin_needs_version", [('install_plugin', {'pluginId': 'grafana-clock-panel'})]))

    def test_validate_provisioning_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_validate_provisioning",
            [('validate_provisioning_file', {'repo': 'dashboards-repo', 'path': 'team-a/overview.json'})]), [])
        self.assertTrue(self._run("tool_obs_validate_provisioning", []))

    def test_list_incidents_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_list_incidents", [('list_incidents', {})]), [])
        self.assertTrue(self._run("tool_obs_list_incidents", []))

    def test_investigate_incident_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_investigate_incident",
            [('get_incident', {'id': 'incident-1'}), ('add_activity_to_incident', {'incidentId': 'incident-1', 'body': 'x'})]), [])
        self.assertTrue(self._run("tool_obs_investigate_incident", [('get_incident', {'id': 'incident-1'})]))

    def test_who_is_oncall_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_who_is_oncall", [('get_current_oncall_users', {'scheduleId': 'sched-1'})]), [])
        self.assertTrue(self._run("tool_obs_who_is_oncall", []))

    def test_investigate_alert_group_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_investigate_alert_group", [('get_alert_group', {'alertGroupId': 'grp-1'})]), [])
        self.assertTrue(self._run("tool_obs_investigate_alert_group", []))

    def test_sift_investigation_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_sift_investigation", [('get_sift_investigation', {'id': '00000000-0000-0000-0000-000000000001'})]), [])
        self.assertTrue(self._run("tool_obs_sift_investigation", []))

    def test_run_error_pattern_check_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_run_error_pattern_check",
            [('find_error_pattern_logs', {'name': 'checkout-error-check', 'labels': {'service': 'checkout'}})]), [])
        self.assertTrue(self._run("tool_obs_run_error_pattern_check", []))

    def test_check_slo_assertions_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_check_slo_assertions", [('get_assertions', {'startTime': 'now-1h', 'endTime': 'now'})]), [])
        self.assertTrue(self._run("tool_obs_check_slo_assertions", []))

    def test_generate_dashboard_link_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_generate_dashboard_link", [('generate_deeplink', {'resourceType': 'dashboard', 'dashboardUid': 'dash-1'})]), [])
        self.assertTrue(self._run("tool_obs_generate_dashboard_link", []))

    def test_generate_loki_pipeline_config_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_generate_loki_pipeline_config", [('suggest_loki_alloy_label_config', {'approvedLabels': ['app', 'env', 'level']})]), [])
        self.assertTrue(self._run("tool_obs_generate_loki_pipeline_config", []))

    def test_render_panel_image_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_render_panel_image", [('get_panel_image', {'dashboardUid': 'dash-1', 'panelId': 1})]), [])
        self.assertTrue(self._run("tool_obs_render_panel_image", []))

    def test_get_query_examples_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_get_query_examples", [('get_query_examples', {'datasourceType': 'prometheus'})]), [])
        self.assertTrue(self._run("tool_obs_get_query_examples", []))

    def test_run_dashboard_panel_query_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_run_dashboard_panel_query", [('run_panel_query', {'dashboardUid': 'dash-1', 'panelIds': [1]})]), [])
        self.assertTrue(self._run("tool_obs_run_dashboard_panel_query", []))

    def test_generic_api_call_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_generic_api_call", [('grafana_api_request', {'endpoint': '/api/org'})]), [])
        self.assertTrue(self._run("tool_obs_generic_api_call", []))

    def test_check_agent_catalog_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_check_agent_catalog", [('agento11y_manage_agents', {'operation': 'get', 'agent_name': 'claude-code'})]), [])
        self.assertTrue(self._run("tool_obs_check_agent_catalog", []))

    def test_ask_grafana_assistant_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_ask_grafana_assistant", [('ask_assistant', {'prompt': 'why did errors spike'})]), [])
        self.assertTrue(self._run("tool_obs_ask_grafana_assistant", []))

    def test_discover_and_query_prometheus_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_discover_and_query_prometheus",
            [('list_prometheus_metric_names', {'datasourceUid': 'ds-prom', 'regex': 'http.*'}),
             ('query_prometheus', {'datasourceUid': 'ds-prom', 'expr': 'http_requests_total', 'queryType': 'instant', 'endTime': 'now'})]), [])
        self.assertTrue(self._run("tool_obs_discover_and_query_prometheus", [('list_prometheus_metric_names', {'datasourceUid': 'ds-prom'})]))

    def test_check_loki_stats_before_query_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_check_loki_stats_before_query",
            [('query_loki_stats', {'datasourceUid': 'ds-loki', 'logql': '{app="checkout"}'}),
             ('query_loki_logs', {'datasourceUid': 'ds-loki', 'logql': '{app="checkout"}'})]), [])
        self.assertTrue(self._run("tool_obs_check_loki_stats_before_query", [('query_loki_stats', {'datasourceUid': 'ds-loki', 'logql': '{app="checkout"}'})]))

    def test_audit_loki_labels_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_audit_loki_labels", [('analyze_loki_labels', {'datasourceUid': 'ds-loki'})]), [])
        self.assertTrue(self._run("tool_obs_audit_loki_labels", []))

    def test_discover_cloudwatch_metric_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_discover_cloudwatch_metric",
            [('list_cloudwatch_namespaces', {'datasourceUid': 'ds-cw', 'region': 'us-east-1'}),
             ('list_cloudwatch_metrics', {'datasourceUid': 'ds-cw', 'namespace': 'AWS/ECS', 'region': 'us-east-1'})]), [])
        self.assertTrue(self._run("tool_obs_discover_cloudwatch_metric", [('list_cloudwatch_namespaces', {'datasourceUid': 'ds-cw', 'region': 'us-east-1'})]))

    def test_query_athena_table_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_query_athena_table",
            [('describe_athena_table', {'datasourceUid': 'ds-athena', 'table': 'orders'}),
             ('query_athena', {'datasourceUid': 'ds-athena', 'query': 'SELECT COUNT(*) FROM orders'})]), [])
        self.assertTrue(self._run("tool_obs_query_athena_table", [('describe_athena_table', {'datasourceUid': 'ds-athena', 'table': 'orders'})]))

    def test_pyroscope_profile_types_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_obs_pyroscope_profile_types", [('list_pyroscope_profile_types', {'data_source_uid': 'ds-pyro'})]), [])
        self.assertTrue(self._run("tool_obs_pyroscope_profile_types", []))


class ObservabilityCaseDriverEndToEndTests(unittest.TestCase):
    """Proves tool_service_seed reaches the mock service through the real
    driver's run_case(), same proof pattern the other services have."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_observability_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_seeded_dashboard_reachable_via_the_real_driver(self):
        from optarena.drivers.tool_chat import ToolChatDriver

        case = {
            "name": "c", "tool_service": "observability",
            "tools": ["get_dashboard_by_uid"],
            "tool_service_seed": {"dashboards": {"dash-1": {"title": "API Overview", "panels": []}}},
            "prompts": ["show me dashboard dash-1"],
            "expected_calls": [{"tool": "get_dashboard_by_uid"}],
        }
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "get_dashboard_by_uid", "arguments": {"uid": "dash-1"}}]},
            {"content": "Here is the dashboard."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual(result.extra["tool_calls"][0]["result"]["title"], "API Overview")


if __name__ == "__main__":
    unittest.main()
