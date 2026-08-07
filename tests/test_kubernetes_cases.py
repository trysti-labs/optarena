"""
kubernetes mock-service tests: the 23-tool KubernetesService itself
(including its real-kubectl/helm-like precondition enforcement - kubectl_create
refuses a duplicate while kubectl_apply upserts, install_helm_chart refuses a
duplicate while upgrade_helm_chart refuses a missing release, node drain
refuses without confirm_drain, deleting a namespace refuses while it still
has resources or Helm releases), its seed() mechanism, and all 19 shipped
tool_kube_* cases - each dry-run against a hand-built "ideal" trajectory
(must PASS) and at least one realistic wrong trajectory (must FAIL), same
discriminating-oracle philosophy as the other services.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from optarena._cases._mock_service import KubernetesService, get_mock_service, get_tool_schemas
from optarena._cases._tool_evaluate import evaluate_tool_case
from optarena.cases import load_cases
from optarena.schema import SchemaError, validate_case
from optarena.scenario import Backend, Scenario
from tests.test_tool_use_cases import _ToolStubBackend


class KubernetesServiceBasicsTests(unittest.TestCase):
    def test_all_23_tools_registered(self):
        schemas = get_tool_schemas("kubernetes")
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(len(names), 23)
        self.assertEqual(names, set(KubernetesService.TOOLS))

    def test_kubectl_generic_is_deliberately_not_mocked(self):
        self.assertNotIn("kubectl_generic", KubernetesService.TOOLS)

    def test_kubectl_create_requires_namespace_to_exist(self):
        svc = KubernetesService()
        result = svc.dispatch("kubectl_create", {"kind": "deployment", "name": "web", "namespace": "nope"})
        self.assertIn("error", result)

    def test_kubectl_create_duplicate_is_error(self):
        svc = KubernetesService()
        svc.dispatch("kubectl_create", {"kind": "deployment", "name": "web"})
        result = svc.dispatch("kubectl_create", {"kind": "deployment", "name": "web"})
        self.assertIn("error", result)

    def test_kubectl_apply_upserts_instead_of_refusing(self):
        svc = KubernetesService()
        svc.dispatch("kubectl_create", {"kind": "deployment", "name": "web", "image": "app:v1"})
        result = svc.dispatch("kubectl_apply", {"kind": "deployment", "name": "web", "image": "app:v2"})
        self.assertNotIn("error", result)
        self.assertEqual(result["image"], "app:v2")

    def test_kubectl_create_namespace_kind_registers_namespace(self):
        svc = KubernetesService()
        result = svc.dispatch("kubectl_create", {"kind": "namespace", "name": "staging"})
        self.assertEqual(result["name"], "staging")
        self.assertIn("staging", svc.summary()["namespaces"])

    def test_kubectl_delete_namespace_refuses_while_in_use(self):
        svc = KubernetesService()
        svc.dispatch("kubectl_create", {"kind": "namespace", "name": "staging"})
        svc.dispatch("kubectl_create", {"kind": "deployment", "name": "web", "namespace": "staging"})
        result = svc.dispatch("kubectl_delete", {"kind": "namespace", "name": "staging"})
        self.assertIn("error", result)

    def test_kubectl_delete_namespace_succeeds_once_empty(self):
        svc = KubernetesService()
        svc.dispatch("kubectl_create", {"kind": "namespace", "name": "staging"})
        result = svc.dispatch("kubectl_delete", {"kind": "namespace", "name": "staging"})
        self.assertTrue(result["deleted"])

    def test_kubectl_delete_default_namespace_is_refused(self):
        svc = KubernetesService()
        result = svc.dispatch("kubectl_delete", {"kind": "namespace", "name": "default"})
        self.assertIn("error", result)

    def test_kubectl_scale_refuses_non_scalable_kind(self):
        svc = KubernetesService()
        svc.dispatch("kubectl_create", {"kind": "pod", "name": "p1"})
        result = svc.dispatch("kubectl_scale", {"kind": "pod", "name": "p1", "replicas": 3})
        self.assertIn("error", result)

    def test_kubectl_patch_updates_only_known_fields(self):
        svc = KubernetesService()
        svc.dispatch("kubectl_create", {"kind": "deployment", "name": "web", "image": "app:v1"})
        svc.dispatch("kubectl_patch", {"kind": "deployment", "name": "web", "patch": {"image": "app:v2"}})
        result = svc.dispatch("kubectl_get", {"kind": "deployment", "name": "web"})
        self.assertEqual(result["image"], "app:v2")

    def test_kubectl_rollout_undo_reverts_to_previous_image(self):
        svc = KubernetesService()
        svc.dispatch("kubectl_create", {"kind": "deployment", "name": "web", "image": "app:v1"})
        svc.dispatch("kubectl_apply", {"kind": "deployment", "name": "web", "image": "app:v2"})
        result = svc.dispatch("kubectl_rollout", {"subcommand": "undo", "name": "web"})
        self.assertEqual(result["image"], "app:v1")

    def test_kubectl_rollout_undo_with_no_history_is_error(self):
        svc = KubernetesService()
        svc.dispatch("kubectl_create", {"kind": "deployment", "name": "web", "image": "app:v1"})
        result = svc.dispatch("kubectl_rollout", {"subcommand": "undo", "name": "web"})
        self.assertIn("error", result)

    def test_kubectl_rollout_restart_bumps_revision_without_changing_image(self):
        svc = KubernetesService()
        svc.dispatch("kubectl_create", {"kind": "deployment", "name": "web", "image": "app:v1"})
        result = svc.dispatch("kubectl_rollout", {"subcommand": "restart", "name": "web"})
        self.assertEqual(result["revision"], 2)
        self.assertEqual(svc.dispatch("kubectl_get", {"kind": "deployment", "name": "web"})["image"], "app:v1")

    def test_kubectl_scale_does_not_bump_rollout_history(self):
        svc = KubernetesService()
        svc.dispatch("kubectl_create", {"kind": "deployment", "name": "web", "image": "app:v1"})
        svc.dispatch("kubectl_scale", {"name": "web", "replicas": 5})
        history = svc.dispatch("kubectl_rollout", {"subcommand": "history", "name": "web"})["history"]
        self.assertEqual(len(history), 1)

    def test_operating_on_unknown_resource_is_error_not_crash(self):
        svc = KubernetesService()
        for tool in ("kubectl_describe", "kubectl_delete", "kubectl_logs",
                     "kubectl_scale", "kubectl_patch", "kubectl_rollout"):
            args = {"name": "nope"}
            if tool in ("kubectl_describe", "kubectl_delete"):
                args["kind"] = "pod"
            if tool == "kubectl_patch":
                args["kind"] = "pod"
                args["patch"] = {}
            if tool == "kubectl_rollout":
                args["subcommand"] = "status"
            self.assertIn("error", svc.dispatch(tool, args))

    def test_port_forward_requires_running_pod(self):
        svc = KubernetesService()
        svc.seed({"resources": [{"kind": "pod", "name": "p1", "status": "Pending"}]})
        result = svc.dispatch("port_forward", {"name": "p1", "local_port": 8080, "remote_port": 80})
        self.assertIn("error", result)

    def test_exec_in_pod_requires_running_pod(self):
        svc = KubernetesService()
        svc.seed({"resources": [{"kind": "pod", "name": "p1", "status": "Error"}]})
        result = svc.dispatch("exec_in_pod", {"name": "p1", "command": ["ls"]})
        self.assertIn("error", result)

    def test_stop_port_forward_unknown_id_is_error(self):
        svc = KubernetesService()
        result = svc.dispatch("stop_port_forward", {"id": "pf9"})
        self.assertIn("error", result)

    def test_install_helm_chart_duplicate_is_error(self):
        svc = KubernetesService()
        svc.dispatch("install_helm_chart", {"name": "cache", "chart": "bitnami/redis"})
        result = svc.dispatch("install_helm_chart", {"name": "cache", "chart": "bitnami/redis"})
        self.assertIn("error", result)

    def test_upgrade_helm_chart_missing_release_is_error(self):
        svc = KubernetesService()
        result = svc.dispatch("upgrade_helm_chart", {"name": "cache", "chart": "bitnami/redis"})
        self.assertIn("error", result)

    def test_install_helm_chart_creates_namespace_by_default(self):
        svc = KubernetesService()
        svc.dispatch("install_helm_chart", {"name": "cache", "chart": "bitnami/redis", "namespace": "data"})
        self.assertIn("data", svc.summary()["namespaces"])

    def test_install_helm_chart_refuses_missing_namespace_without_create_namespace(self):
        svc = KubernetesService()
        result = svc.dispatch("install_helm_chart", {
            "name": "cache", "chart": "bitnami/redis", "namespace": "data", "create_namespace": False,
        })
        self.assertIn("error", result)

    def test_helm_template_apply_upserts(self):
        svc = KubernetesService()
        svc.dispatch("helm_template_apply", {"name": "tools", "chart": "internal-tools"})
        result = svc.dispatch("helm_template_apply", {"name": "tools", "chart": "internal-tools"})
        self.assertNotIn("error", result)
        self.assertEqual(result["revision"], 2)

    def test_cleanup_pods_only_removes_terminal_states(self):
        svc = KubernetesService()
        svc.seed({"resources": [
            {"kind": "pod", "name": "ok", "status": "Running"},
            {"kind": "pod", "name": "bad", "status": "Error"},
        ]})
        result = svc.dispatch("cleanup_pods", {})
        self.assertEqual(result["removed"], [{"namespace": "default", "name": "bad"}])
        self.assertEqual(svc.summary()["pod_count"], 1)

    def test_node_management_drain_requires_confirm(self):
        svc = KubernetesService()
        result = svc.dispatch("node_management", {"operation": "drain", "node_name": "node-1"})
        self.assertIn("error", result)
        self.assertTrue(svc.summary()["node_schedulable"]["node-1"])

    def test_node_management_drain_evicts_pods_on_that_node(self):
        svc = KubernetesService()
        svc.seed({"resources": [{"kind": "pod", "name": "p1", "status": "Running", "node": "node-1"}]})
        result = svc.dispatch("node_management", {"operation": "drain", "node_name": "node-1", "confirm_drain": True})
        self.assertEqual(result["evicted_pods"], ["p1"])
        self.assertEqual(svc.dispatch("kubectl_get", {"kind": "pod", "name": "p1"})["status"], "Evicted")

    def test_node_management_unknown_node_is_error(self):
        svc = KubernetesService()
        result = svc.dispatch("node_management", {"operation": "cordon", "node_name": "nope"})
        self.assertIn("error", result)

    def test_explain_resource_unknown_kind_is_error(self):
        svc = KubernetesService()
        result = svc.dispatch("explain_resource", {"resource": "widget"})
        self.assertIn("error", result)

    def test_kubectl_context_use_unknown_context_is_error(self):
        svc = KubernetesService()
        result = svc.dispatch("kubectl_context", {"operation": "use", "name": "nope"})
        self.assertIn("error", result)

    def test_ping_reports_current_context(self):
        svc = KubernetesService()
        svc.seed({"contexts": ["staging"]})
        svc.dispatch("kubectl_context", {"operation": "use", "name": "staging"})
        self.assertEqual(svc.dispatch("ping", {}), {"connected": True, "context": "staging"})

    def test_unknown_kubernetes_tool_name_is_error_result(self):
        svc = KubernetesService()
        result = svc.dispatch("delete_everything", {})
        self.assertIn("error", result)


class KubernetesSeedTests(unittest.TestCase):
    def test_seed_is_never_logged_to_call_log(self):
        svc = KubernetesService()
        svc.seed({"namespaces": ["staging"], "nodes": {"node-2": {"schedulable": False}},
                  "resources": [{"kind": "pod", "name": "p1"}],
                  "helm_releases": [{"name": "cache", "chart": "bitnami/redis"}]})
        self.assertEqual(svc.call_log, [])

    def test_seeded_resource_image_synthesizes_single_revision_history(self):
        svc = KubernetesService()
        svc.seed({"resources": [{"kind": "deployment", "name": "web", "image": "app:v1"}]})
        history = svc.dispatch("kubectl_rollout", {"subcommand": "history", "name": "web"})["history"]
        self.assertEqual(history, [{"revision": 1, "image": "app:v1"}])

    def test_seeded_explicit_history_is_used_directly(self):
        svc = KubernetesService()
        svc.seed({"resources": [{
            "kind": "deployment", "name": "web", "image": "app:v2",
            "history": [{"revision": 1, "image": "app:v1"}, {"revision": 2, "image": "app:v2"}],
        }]})
        history = svc.dispatch("kubectl_rollout", {"subcommand": "history", "name": "web"})["history"]
        self.assertEqual(len(history), 2)
        undo = svc.dispatch("kubectl_rollout", {"subcommand": "undo", "name": "web"})
        self.assertEqual(undo["image"], "app:v1")

    def test_seeded_helm_release_reachable(self):
        svc = KubernetesService()
        svc.seed({"helm_releases": [{"name": "cache", "chart": "bitnami/redis", "revision": 3}]})
        self.assertIn("error", svc.dispatch("install_helm_chart", {"name": "cache", "chart": "bitnami/redis"}))
        self.assertEqual(svc.summary()["helm_releases"]["default/cache"]["revision"], 3)


class KubernetesCaseSchemaTests(unittest.TestCase):
    def test_minimal_valid_kubernetes_case_passes(self):
        case = {"name": "c", "tool_service": "kubernetes",
                "expected_calls": [{"tool": "ping"}]}
        validate_case(case)


class ShippedKubernetesCaseDryRunTests(unittest.TestCase):
    """Every tool_kube_* case, run against a hand-built ideal trajectory
    (must PASS) and at least one realistic wrong trajectory (must FAIL)."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["name"]: c for c in load_cases() if c.get("tool_service") == "kubernetes"}

    def _run(self, case_name: str, calls: list[tuple[str, dict]]) -> list[str]:
        case = self.cases[case_name]
        svc = get_mock_service(case["tool_service"])()
        svc.seed(case.get("tool_service_seed") or {})
        for tool, args in calls:
            svc.dispatch(tool, args)
        failures, _info = evaluate_tool_case(case, svc)
        return failures

    def test_all_19_kubernetes_cases_shipped(self):
        self.assertEqual(len(self.cases), 19)

    def test_create_namespace_then_deploy_ideal_passes_and_skipping_namespace_fails(self):
        self.assertEqual(self._run("tool_kube_create_namespace_then_deploy", [
            ("kubectl_create", {"kind": "namespace", "name": "staging"}),
            ("kubectl_create", {"kind": "deployment", "name": "web", "namespace": "staging",
                                "image": "nginx:latest", "replicas": 2}),
        ]), [])
        self.assertTrue(self._run("tool_kube_create_namespace_then_deploy", [
            ("kubectl_create", {"kind": "deployment", "name": "web", "namespace": "staging", "image": "nginx:latest"}),
        ]))

    def test_apply_not_recreate_ideal_passes_and_create_fails(self):
        self.assertEqual(self._run("tool_kube_apply_not_recreate", [
            ("kubectl_apply", {"kind": "deployment", "name": "web", "image": "app:v2"}),
        ]), [])
        self.assertTrue(self._run("tool_kube_apply_not_recreate", [
            ("kubectl_create", {"kind": "deployment", "name": "web", "image": "app:v2"}),
        ]))

    def test_patch_and_scale_ideal_passes_and_partial_fails(self):
        self.assertEqual(self._run("tool_kube_patch_and_scale", [
            ("kubectl_patch", {"kind": "deployment", "name": "web", "patch": {"image": "app:v2"}}),
            ("kubectl_scale", {"name": "web", "replicas": 5}),
        ]), [])
        self.assertTrue(self._run("tool_kube_patch_and_scale", [
            ("kubectl_patch", {"kind": "deployment", "name": "web", "patch": {"image": "app:v2"}}),
        ]))

    def test_list_before_assuming_ideal_passes_and_blind_scale_fails(self):
        self.assertEqual(self._run("tool_kube_list_before_assuming", [
            ("kubectl_get", {"kind": "deployment"}),
        ]), [])
        self.assertTrue(self._run("tool_kube_list_before_assuming", [
            ("kubectl_scale", {"name": "cache", "replicas": 3}),
        ]))

    def test_rollout_check_then_undo_ideal_passes_and_undo_only_fails(self):
        self.assertEqual(self._run("tool_kube_rollout_check_then_undo", [
            ("kubectl_rollout", {"subcommand": "history", "name": "web"}),
            ("kubectl_rollout", {"subcommand": "undo", "name": "web"}),
        ]), [])
        self.assertTrue(self._run("tool_kube_rollout_check_then_undo", [
            ("kubectl_rollout", {"subcommand": "undo", "name": "web"}),
        ]))

    def test_drain_with_confirm_ideal_passes_and_unconfirmed_fails(self):
        self.assertEqual(self._run("tool_kube_drain_with_confirm", [
            ("node_management", {"operation": "drain", "node_name": "node-1", "confirm_drain": True}),
        ]), [])
        self.assertTrue(self._run("tool_kube_drain_with_confirm", [
            ("node_management", {"operation": "drain", "node_name": "node-1"}),
        ]))

    def test_cordon_not_drain_ideal_passes_and_draining_fails(self):
        self.assertEqual(self._run("tool_kube_cordon_not_drain", [
            ("node_management", {"operation": "cordon", "node_name": "node-1"}),
        ]), [])
        self.assertTrue(self._run("tool_kube_cordon_not_drain", [
            ("node_management", {"operation": "drain", "node_name": "node-1", "confirm_drain": True}),
        ]))

    def test_cleanup_crashed_pods_ideal_passes_and_nothing_fails(self):
        self.assertEqual(self._run("tool_kube_cleanup_crashed_pods", [
            ("cleanup_pods", {"namespace": "default"}),
        ]), [])
        self.assertTrue(self._run("tool_kube_cleanup_crashed_pods", []))

    def test_investigate_before_deleting_ideal_passes_and_blind_delete_fails(self):
        self.assertEqual(self._run("tool_kube_investigate_before_deleting", [
            ("kubectl_logs", {"name": "worker-1"}), ("kubectl_describe", {"kind": "pod", "name": "worker-1"}),
        ]), [])
        self.assertTrue(self._run("tool_kube_investigate_before_deleting", [
            ("kubectl_logs", {"name": "worker-1"}), ("kubectl_delete", {"kind": "pod", "name": "worker-1"}),
        ]))

    def test_exec_healthcheck_ideal_passes_and_wrong_command_fails(self):
        self.assertEqual(self._run("tool_kube_exec_healthcheck", [
            ("exec_in_pod", {"name": "web-1", "command": ["curl", "-sf", "localhost:8080/health"]}),
        ]), [])
        self.assertTrue(self._run("tool_kube_exec_healthcheck", [
            ("exec_in_pod", {"name": "web-1", "command": ["echo", "hi"]}),
        ]))

    def test_port_forward_then_stop_ideal_passes_and_left_open_fails(self):
        self.assertEqual(self._run("tool_kube_port_forward_then_stop", [
            ("port_forward", {"name": "web-1", "local_port": 9090, "remote_port": 80}),
            ("stop_port_forward", {"id": "pf1"}),
        ]), [])
        self.assertTrue(self._run("tool_kube_port_forward_then_stop", [
            ("port_forward", {"name": "web-1", "local_port": 9090, "remote_port": 80}),
        ]))

    def test_helm_install_new_release_ideal_passes_and_nothing_fails(self):
        self.assertEqual(self._run("tool_kube_helm_install_new_release", [
            ("install_helm_chart", {"name": "db-release", "chart": "bitnami/postgresql", "namespace": "data"}),
        ]), [])
        self.assertTrue(self._run("tool_kube_helm_install_new_release", []))

    def test_helm_upgrade_not_reinstall_ideal_passes_and_install_fails(self):
        self.assertEqual(self._run("tool_kube_helm_upgrade_not_reinstall", [
            ("upgrade_helm_chart", {"name": "cache", "chart": "bitnami/redis", "values": {"replicaCount": 3}}),
        ]), [])
        self.assertTrue(self._run("tool_kube_helm_upgrade_not_reinstall", [
            ("install_helm_chart", {"name": "cache", "chart": "bitnami/redis"}),
        ]))

    def test_helm_uninstall_release_ideal_passes_and_nothing_fails(self):
        self.assertEqual(self._run("tool_kube_helm_uninstall_release", [
            ("uninstall_helm_chart", {"name": "cache"}),
        ]), [])
        self.assertTrue(self._run("tool_kube_helm_uninstall_release", []))

    def test_helm_template_deploy_and_teardown_ideal_passes_and_leftover_fails(self):
        self.assertEqual(self._run("tool_kube_helm_template_deploy_and_teardown", [
            ("helm_template_apply", {"name": "debug-tools", "chart": "internal-tools", "namespace": "ops"}),
            ("helm_template_uninstall", {"name": "debug-tools", "namespace": "ops"}),
        ]), [])
        self.assertTrue(self._run("tool_kube_helm_template_deploy_and_teardown", [
            ("helm_template_apply", {"name": "debug-tools", "chart": "internal-tools", "namespace": "ops"}),
        ]))

    def test_explain_before_create_ideal_passes_and_blind_create_fails(self):
        self.assertEqual(self._run("tool_kube_explain_before_create", [
            ("explain_resource", {"resource": "cronjob"}),
            ("kubectl_create", {"kind": "cronjob", "name": "nightly-backup", "image": "backup:latest"}),
        ]), [])
        self.assertTrue(self._run("tool_kube_explain_before_create", [
            ("kubectl_create", {"kind": "cronjob", "name": "nightly-backup", "image": "backup:latest"}),
        ]))

    def test_discover_resources_and_ping_ideal_passes_and_half_fails(self):
        self.assertEqual(self._run("tool_kube_discover_resources_and_ping", [
            ("ping", {}), ("list_api_resources", {}),
        ]), [])
        self.assertTrue(self._run("tool_kube_discover_resources_and_ping", [("ping", {})]))

    def test_context_switch_then_list_ideal_passes_and_skipping_switch_fails(self):
        self.assertEqual(self._run("tool_kube_context_switch_then_list", [
            ("kubectl_context", {"operation": "use", "name": "staging"}),
            ("kubectl_get", {"kind": "pod"}),
        ]), [])
        self.assertTrue(self._run("tool_kube_context_switch_then_list", [
            ("kubectl_get", {"kind": "pod"}),
        ]))

    def test_delete_completed_job_ideal_passes_and_not_deleting_fails(self):
        self.assertEqual(self._run("tool_kube_delete_completed_job", [
            ("kubectl_delete", {"kind": "job", "name": "batch-cleanup"}),
        ]), [])
        self.assertTrue(self._run("tool_kube_delete_completed_job", [
            ("kubectl_get", {"kind": "job"}),
        ]))


class KubernetesCaseDriverEndToEndTests(unittest.TestCase):
    """Proves tool_service_seed reaches the mock service through the real
    driver's run_case(), same proof pattern the other services have."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_kubernetes_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_seeded_pod_reachable_via_the_real_driver(self):
        from optarena.drivers.tool_chat import ToolChatDriver

        case = {
            "name": "c", "tool_service": "kubernetes",
            "tools": ["kubectl_get"],
            "tool_service_seed": {"resources": [{"kind": "pod", "name": "web-1", "status": "Running"}]},
            "prompts": ["list pods"],
            "expected_calls": [{"tool": "kubectl_get"}],
        }
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "kubectl_get", "arguments": {"kind": "pod"}}]},
            {"content": "There's one pod: web-1."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual(result.extra["tool_calls"][0]["result"]["items"][0]["name"], "web-1")


if __name__ == "__main__":
    unittest.main()
