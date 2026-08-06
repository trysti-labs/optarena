"""
docker mock-service tests: the 25-tool DockerService itself (including its
real-git-like precondition enforcement - can't remove a running container,
can't remove an image/volume still in use), its seed() mechanism, and all
15 shipped tool_docker_* cases - each dry-run against a hand-built "ideal"
trajectory (must PASS) and at least one realistic wrong trajectory (must
FAIL), same discriminating-oracle philosophy as the other services.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from optarena._cases._mock_service import DockerService, get_mock_service, get_tool_schemas
from optarena._cases._tool_evaluate import evaluate_tool_case
from optarena.cases import load_cases
from optarena.schema import SchemaError, validate_case
from optarena.scenario import Backend, Scenario
from tests.test_tool_use_cases import _ToolStubBackend


class DockerServiceBasicsTests(unittest.TestCase):
    def test_all_25_tools_registered(self):
        schemas = get_tool_schemas("docker")
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(len(names), 25)
        self.assertEqual(names, set(DockerService.TOOLS))

    def test_create_container_requires_image_to_exist(self):
        svc = DockerService()
        result = svc.dispatch("create_container", {"name": "web", "image": "nope:latest"})
        self.assertIn("error", result)

    def test_create_container_succeeds_with_pulled_image(self):
        svc = DockerService()
        svc.seed({"images": ["nginx:latest"]})
        result = svc.dispatch("create_container", {"name": "web", "image": "nginx:latest"})
        self.assertEqual(result["status"], "running")

    def test_create_container_duplicate_name_is_error(self):
        svc = DockerService()
        svc.seed({"images": ["nginx:latest"]})
        svc.dispatch("create_container", {"name": "web", "image": "nginx:latest"})
        result = svc.dispatch("create_container", {"name": "web", "image": "nginx:latest"})
        self.assertIn("error", result)

    def test_remove_running_container_is_refused(self):
        svc = DockerService()
        svc.seed({"images": ["nginx:latest"], "containers": {"web": {"image": "nginx:latest", "status": "running"}}})
        result = svc.dispatch("remove_container", {"name": "web"})
        self.assertIn("error", result)
        self.assertEqual(svc.summary()["container_count"], 1)

    def test_remove_stopped_container_succeeds(self):
        svc = DockerService()
        svc.seed({"images": ["nginx:latest"], "containers": {"web": {"image": "nginx:latest", "status": "stopped"}}})
        result = svc.dispatch("remove_container", {"name": "web"})
        self.assertTrue(result["removed"])

    def test_remove_running_container_with_force_succeeds(self):
        svc = DockerService()
        svc.seed({"images": ["nginx:latest"], "containers": {"web": {"image": "nginx:latest", "status": "running"}}})
        result = svc.dispatch("remove_container", {"name": "web", "force": True})
        self.assertTrue(result["removed"])

    def test_pause_a_stopped_container_is_error(self):
        svc = DockerService()
        svc.seed({"images": ["nginx:latest"], "containers": {"web": {"image": "nginx:latest", "status": "stopped"}}})
        result = svc.dispatch("pause_container", {"name": "web"})
        self.assertIn("error", result)

    def test_operating_on_unknown_container_is_error_not_crash(self):
        svc = DockerService()
        for tool in ("start_container", "stop_container", "restart_container",
                     "pause_container", "remove_container", "inspect_container",
                     "get_container_logs", "get_container_stats"):
            self.assertIn("error", svc.dispatch(tool, {"name": "nope"}))

    def test_pull_and_build_record_different_provenance(self):
        svc = DockerService()
        svc.dispatch("pull_image", {"tag": "nginx:latest"})
        svc.dispatch("build_image", {"tag": "myapp:v1"})
        images = {i["tag"]: i["source"] for i in svc.dispatch("list_images", {})["images"]}
        self.assertEqual(images, {"nginx:latest": "pulled", "myapp:v1": "built"})

    def test_tag_image_unknown_source_is_error(self):
        svc = DockerService()
        result = svc.dispatch("tag_image", {"source": "nope:latest", "target": "x:latest"})
        self.assertIn("error", result)

    def test_remove_image_in_use_is_refused(self):
        svc = DockerService()
        svc.seed({"images": ["myapp:v1"], "containers": {"app": {"image": "myapp:v1", "status": "running"}}})
        result = svc.dispatch("remove_image", {"tag": "myapp:v1"})
        self.assertIn("error", result)

    def test_prune_images_keeps_only_images_in_use(self):
        svc = DockerService()
        svc.seed({"images": ["myapp:v1", "unused:v0"], "containers": {"app": {"image": "myapp:v1", "status": "running"}}})
        result = svc.dispatch("prune_images", {})
        self.assertEqual(result["removed"], ["unused:v0"])
        self.assertEqual(svc.summary()["image_count"], 1)

    def test_create_network_duplicate_name_is_error(self):
        svc = DockerService()
        svc.dispatch("create_network", {"name": "appnet"})
        result = svc.dispatch("create_network", {"name": "appnet"})
        self.assertIn("error", result)

    def test_connect_and_disconnect_network(self):
        svc = DockerService()
        svc.seed({"images": ["nginx:latest"], "containers": {"web": {"image": "nginx:latest", "status": "running"}},
                  "networks": ["appnet"]})
        svc.dispatch("connect_network", {"network": "appnet", "container": "web"})
        self.assertIn("appnet", svc.dispatch("inspect_container", {"name": "web"})["networks"])
        svc.dispatch("disconnect_network", {"network": "appnet", "container": "web"})
        self.assertNotIn("appnet", svc.dispatch("inspect_container", {"name": "web"})["networks"])

    def test_create_container_with_unknown_volume_is_error(self):
        svc = DockerService()
        svc.seed({"images": ["postgres:latest"]})
        result = svc.dispatch("create_container", {"name": "db", "image": "postgres:latest", "volumes": ["nope"]})
        self.assertIn("error", result)

    def test_volume_mounted_into_a_container_tracks_usage(self):
        svc = DockerService()
        svc.seed({"images": ["postgres:latest"], "volumes": ["dbdata"]})
        svc.dispatch("create_container", {"name": "db", "image": "postgres:latest", "volumes": ["dbdata"]})
        self.assertIn("error", svc.dispatch("remove_volume", {"name": "dbdata"}))
        self.assertEqual(svc.dispatch("prune_volumes", {})["removed"], [])

    def test_volume_freed_after_container_removed(self):
        svc = DockerService()
        svc.seed({"images": ["postgres:latest"], "volumes": ["dbdata"]})
        svc.dispatch("create_container", {"name": "db", "image": "postgres:latest", "volumes": ["dbdata"]})
        svc.dispatch("stop_container", {"name": "db"})
        svc.dispatch("remove_container", {"name": "db"})
        result = svc.dispatch("remove_volume", {"name": "dbdata"})
        self.assertTrue(result["removed"])

    def test_docker_info_reports_accurate_counts(self):
        svc = DockerService()
        svc.seed({"images": ["nginx:latest"],
                  "containers": {"web": {"image": "nginx:latest", "status": "running"},
                                "cache": {"image": "nginx:latest", "status": "stopped"}}})
        info = svc.dispatch("docker_info", {})
        self.assertEqual(info["containers"], 2)
        self.assertEqual(info["containers_running"], 1)

    def test_unknown_docker_tool_name_is_error_result(self):
        svc = DockerService()
        result = svc.dispatch("delete_everything", {})
        self.assertIn("error", result)


class DockerSeedTests(unittest.TestCase):
    def test_seed_is_never_logged_to_call_log(self):
        svc = DockerService()
        svc.seed({"images": ["nginx:latest"], "volumes": ["dbdata"],
                  "containers": {"web": {"image": "nginx:latest", "volumes": ["dbdata"]}},
                  "networks": ["appnet"]})
        self.assertEqual(svc.call_log, [])

    def test_seeded_container_logs_are_readable(self):
        svc = DockerService()
        svc.seed({"images": ["myapi:v1"],
                  "containers": {"api": {"image": "myapi:v1", "status": "stopped",
                                         "logs": ["boot", "crash"]}}})
        self.assertEqual(svc.dispatch("get_container_logs", {"name": "api"})["logs"], ["boot", "crash"])

    def test_seeded_container_volume_is_pre_attached(self):
        svc = DockerService()
        svc.seed({"images": ["postgres:latest"], "volumes": ["dbdata"],
                  "containers": {"db": {"image": "postgres:latest", "volumes": ["dbdata"]}}})
        self.assertIn("error", svc.dispatch("remove_volume", {"name": "dbdata"}))


class DockerCaseSchemaTests(unittest.TestCase):
    def test_minimal_valid_docker_case_passes(self):
        case = {"name": "c", "tool_service": "docker",
                "expected_calls": [{"tool": "list_containers"}]}
        validate_case(case)


class ShippedDockerCaseDryRunTests(unittest.TestCase):
    """Every tool_docker_* case, run against a hand-built ideal trajectory
    (must PASS) and at least one realistic wrong trajectory (must FAIL)."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["name"]: c for c in load_cases() if c.get("tool_service") == "docker"}

    def _run(self, case_name: str, calls: list[tuple[str, dict]]) -> list[str]:
        case = self.cases[case_name]
        svc = get_mock_service(case["tool_service"])()
        svc.seed(case.get("tool_service_seed") or {})
        for tool, args in calls:
            svc.dispatch(tool, args)
        failures, _info = evaluate_tool_case(case, svc)
        return failures

    def test_all_15_docker_cases_shipped(self):
        self.assertEqual(len(self.cases), 15)

    def test_pull_before_run_ideal_passes_and_no_pull_fails(self):
        self.assertEqual(self._run("tool_docker_pull_before_run", [
            ("pull_image", {"tag": "nginx:latest"}), ("create_container", {"name": "web", "image": "nginx:latest"}),
        ]), [])
        self.assertTrue(self._run("tool_docker_pull_before_run", []))

    def test_stop_before_remove_ideal_passes_and_force_removing_fails(self):
        self.assertEqual(self._run("tool_docker_stop_before_remove", [
            ("stop_container", {"name": "web"}), ("remove_container", {"name": "web"}),
        ]), [])
        self.assertTrue(self._run("tool_docker_stop_before_remove", [
            ("remove_container", {"name": "web", "force": True}),
        ]))

    def test_check_logs_before_restart_ideal_passes_and_blind_restart_fails(self):
        self.assertEqual(self._run("tool_docker_check_logs_before_restart", [
            ("get_container_logs", {"name": "api"}), ("restart_container", {"name": "api"}),
        ]), [])
        self.assertTrue(self._run("tool_docker_check_logs_before_restart", [
            ("restart_container", {"name": "api"}),
        ]))

    def test_full_teardown_order_ideal_passes_and_partial_cleanup_fails(self):
        self.assertEqual(self._run("tool_docker_full_teardown_order", [
            ("stop_container", {"name": "app"}), ("remove_container", {"name": "app"}),
            ("remove_image", {"tag": "myapp:v1"}),
        ]), [])
        self.assertTrue(self._run("tool_docker_full_teardown_order", [
            ("stop_container", {"name": "app"}), ("remove_container", {"name": "app"}),
        ]))

    def test_build_custom_image_ideal_passes_and_pulling_instead_fails(self):
        self.assertEqual(self._run("tool_docker_build_custom_image", [
            ("build_image", {"tag": "myapp:latest"}), ("create_container", {"name": "app", "image": "myapp:latest"}),
        ]), [])
        self.assertTrue(self._run("tool_docker_build_custom_image", [
            ("pull_image", {"tag": "myapp:latest"}), ("create_container", {"name": "app", "image": "myapp:latest"}),
        ]))

    def test_list_before_assuming_ideal_passes_and_nothing_fails(self):
        self.assertEqual(self._run("tool_docker_list_before_assuming", [("list_containers", {})]), [])
        self.assertTrue(self._run("tool_docker_list_before_assuming", []))

    def test_inspect_container_ideal_passes_and_nothing_fails(self):
        self.assertEqual(self._run("tool_docker_inspect_container", [("inspect_container", {"name": "web"})]), [])
        self.assertTrue(self._run("tool_docker_inspect_container", []))

    def test_stats_before_restart_ideal_passes_and_nothing_fails(self):
        self.assertEqual(self._run("tool_docker_stats_before_restart_decision", [
            ("get_container_stats", {"name": "api"}),
        ]), [])
        self.assertTrue(self._run("tool_docker_stats_before_restart_decision", []))

    def test_network_setup_ideal_passes_and_skip_check_fails(self):
        self.assertEqual(self._run("tool_docker_network_setup", [
            ("list_networks", {}), ("create_network", {"name": "appnet"}),
            ("connect_network", {"network": "appnet", "container": "app"}),
        ]), [])
        self.assertTrue(self._run("tool_docker_network_setup", [
            ("create_network", {"name": "appnet"}),
        ]))

    def test_volume_for_persistence_ideal_passes_and_no_volume_fails(self):
        self.assertEqual(self._run("tool_docker_volume_for_persistence", [
            ("create_volume", {"name": "dbdata"}),
            ("create_container", {"name": "db", "image": "postgres:latest", "volumes": ["dbdata"]}),
        ]), [])
        self.assertTrue(self._run("tool_docker_volume_for_persistence", [
            ("create_container", {"name": "db", "image": "postgres:latest"}),
        ]))

    def test_prune_unused_only_ideal_passes_and_no_prune_fails(self):
        self.assertEqual(self._run("tool_docker_prune_unused_only", [
            ("prune_images", {}), ("prune_volumes", {}),
        ]), [])
        self.assertTrue(self._run("tool_docker_prune_unused_only", []))

    def test_tag_for_release_ideal_passes_and_no_tag_fails(self):
        self.assertEqual(self._run("tool_docker_tag_for_release", [
            ("tag_image", {"source": "myapp:v1", "target": "myapp:stable"}),
        ]), [])
        self.assertTrue(self._run("tool_docker_tag_for_release", []))

    def test_pause_then_resume_ideal_passes_and_only_pause_fails(self):
        self.assertEqual(self._run("tool_docker_pause_then_resume", [
            ("pause_container", {"name": "web"}), ("start_container", {"name": "web"}),
        ]), [])
        self.assertTrue(self._run("tool_docker_pause_then_resume", [
            ("pause_container", {"name": "web"}),
        ]))

    def test_system_overview_ideal_passes_and_nothing_fails(self):
        self.assertEqual(self._run("tool_docker_system_overview", [("docker_info", {})]), [])
        self.assertTrue(self._run("tool_docker_system_overview", []))

    def test_decommission_service_ideal_passes_and_removing_wrong_volume_fails(self):
        self.assertEqual(self._run("tool_docker_decommission_service", [
            ("disconnect_network", {"network": "bridge", "container": "app"}),
            ("list_images", {}), ("list_volumes", {}),
            ("remove_volume", {"name": "old-backup"}),
        ]), [])
        self.assertTrue(self._run("tool_docker_decommission_service", [
            ("disconnect_network", {"network": "bridge", "container": "app"}),
            ("list_images", {}), ("list_volumes", {}),
        ]))


class DockerCaseDriverEndToEndTests(unittest.TestCase):
    """Proves tool_service_seed reaches the mock service through the real
    driver's run_case(), same proof pattern the other services have."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_docker_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_seeded_container_reachable_via_the_real_driver(self):
        from optarena.drivers.tool_chat import ToolChatDriver

        case = {
            "name": "c", "tool_service": "docker",
            "tools": ["list_containers"],
            "tool_service_seed": {"images": ["nginx:latest"],
                                  "containers": {"web": {"image": "nginx:latest", "status": "running"}}},
            "prompts": ["list containers"],
            "expected_calls": [{"tool": "list_containers"}],
        }
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "list_containers", "arguments": {}}]},
            {"content": "There's one container: web."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual(result.extra["tool_calls"][0]["result"]["containers"],
                         [{"name": "web", "image": "nginx:latest", "status": "running"}])


if __name__ == "__main__":
    unittest.main()
