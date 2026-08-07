"""
terraform mock-service tests: the 55-tool TerraformService itself (a mock
Terraform Cloud/Enterprise plus public registry, mirroring the real
hashicorp/terraform-mcp-server's actual tool registrations - extracted
directly from its source, not its README), including its real workflow-
discipline preconditions (create_run refuses a locked workspace,
action_run("apply") refuses a run that isn't in a plannable-to-apply
state, delete_workspace_safely refuses a locked workspace,
force_unlock_workspace refuses one that isn't locked, delete_project
refuses while workspaces still reference it), its seed() mechanism, and
all 31 shipped tool_tf_* cases - each dry-run against a hand-built
"ideal" trajectory (must PASS) and at least one realistic wrong
trajectory (must FAIL), same discriminating-oracle philosophy as the
other services.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from optarena._cases._mock_service import TerraformService, get_mock_service, get_tool_schemas
from optarena._cases._tool_evaluate import evaluate_tool_case
from optarena.cases import load_cases
from optarena.schema import SchemaError, validate_case
from optarena.scenario import Backend, Scenario
from tests.test_tool_use_cases import _ToolStubBackend


class TerraformServiceBasicsTests(unittest.TestCase):
    def test_all_55_tools_registered(self):
        schemas = get_tool_schemas("terraform")
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(len(names), 55)
        self.assertEqual(names, set(TerraformService.TOOLS))

    def test_repo_scoped_tool_refuses_unknown_org(self):
        svc = TerraformService()
        self.assertIn("error", svc.dispatch("list_workspaces", {"org": "nope"}))

    def test_create_run_refuses_locked_workspace(self):
        svc = TerraformService()
        svc.seed({"workspaces": [{"org": "acme", "id": "ws-1", "name": "prod", "locked": True}]})
        result = svc.dispatch("create_run", {"org": "acme", "workspace_id": "ws-1"})
        self.assertIn("error", result)

    def test_create_run_locks_workspace_and_apply_unlocks_it(self):
        svc = TerraformService()
        svc.seed({"workspaces": [{"org": "acme", "id": "ws-1", "name": "prod"}]})
        run = svc.dispatch("create_run", {"org": "acme", "workspace_id": "ws-1"})
        self.assertTrue(svc.dispatch("get_workspace_details", {"org": "acme", "workspace_id": "ws-1"})["locked"])
        svc.dispatch("action_run", {"run_id": run["id"], "action": "apply"})
        self.assertFalse(svc.dispatch("get_workspace_details", {"org": "acme", "workspace_id": "ws-1"})["locked"])

    def test_action_run_apply_refuses_already_applied_run(self):
        svc = TerraformService()
        svc.seed({"workspaces": [{"org": "acme", "id": "ws-1", "name": "prod"}]})
        run = svc.dispatch("create_run", {"org": "acme", "workspace_id": "ws-1"})
        svc.dispatch("action_run", {"run_id": run["id"], "action": "apply"})
        result = svc.dispatch("action_run", {"run_id": run["id"], "action": "apply"})
        self.assertIn("error", result)

    def test_action_run_apply_records_a_new_state_version(self):
        svc = TerraformService()
        svc.seed({"workspaces": [{"org": "acme", "id": "ws-1", "name": "prod"}]})
        run = svc.dispatch("create_run", {"org": "acme", "workspace_id": "ws-1"})
        svc.dispatch("action_run", {"run_id": run["id"], "action": "apply"})
        self.assertEqual(len(svc.dispatch("list_state_versions", {"org": "acme", "workspace_id": "ws-1"})["state_versions"]), 1)

    def test_action_run_discard_unlocks_workspace(self):
        svc = TerraformService()
        svc.seed({"workspaces": [{"org": "acme", "id": "ws-1", "name": "prod"}]})
        run = svc.dispatch("create_run", {"org": "acme", "workspace_id": "ws-1"})
        svc.dispatch("action_run", {"run_id": run["id"], "action": "discard"})
        self.assertFalse(svc.dispatch("get_workspace_details", {"org": "acme", "workspace_id": "ws-1"})["locked"])

    def test_action_run_unknown_action_is_error(self):
        svc = TerraformService()
        svc.seed({"workspaces": [{"org": "acme", "id": "ws-1", "name": "prod"}]})
        run = svc.dispatch("create_run", {"org": "acme", "workspace_id": "ws-1"})
        self.assertIn("error", svc.dispatch("action_run", {"run_id": run["id"], "action": "nope"}))

    def test_delete_workspace_safely_refuses_locked(self):
        svc = TerraformService()
        svc.seed({"workspaces": [{"org": "acme", "id": "ws-1", "name": "prod", "locked": True}]})
        self.assertIn("error", svc.dispatch("delete_workspace_safely", {"org": "acme", "workspace_id": "ws-1"}))

    def test_force_unlock_workspace_refuses_when_not_locked(self):
        svc = TerraformService()
        svc.seed({"workspaces": [{"org": "acme", "id": "ws-1", "name": "prod"}]})
        self.assertIn("error", svc.dispatch("force_unlock_workspace", {"org": "acme", "workspace_id": "ws-1"}))

    def test_delete_project_refuses_while_workspaces_reference_it(self):
        svc = TerraformService()
        svc.seed({"projects": [{"org": "acme", "id": "prj-1", "name": "sandbox"}],
                  "workspaces": [{"org": "acme", "id": "ws-1", "name": "dev", "project_id": "prj-1"}]})
        self.assertIn("error", svc.dispatch("delete_project", {"org": "acme", "project_id": "prj-1"}))

    def test_delete_project_succeeds_once_empty(self):
        svc = TerraformService()
        svc.seed({"projects": [{"org": "acme", "id": "prj-1", "name": "sandbox"}]})
        result = svc.dispatch("delete_project", {"org": "acme", "project_id": "prj-1"})
        self.assertTrue(result["deleted"])

    def test_create_workspace_duplicate_name_is_error(self):
        svc = TerraformService()
        svc.seed({"orgs": ["acme"]})
        svc.dispatch("create_workspace", {"org": "acme", "name": "prod"})
        result = svc.dispatch("create_workspace", {"org": "acme", "name": "prod"})
        self.assertIn("error", result)

    def test_create_workspace_variable_refuses_duplicate_key(self):
        svc = TerraformService()
        svc.seed({"workspaces": [{"org": "acme", "id": "ws-1", "name": "prod"}]})
        svc.dispatch("create_workspace_variable", {"org": "acme", "workspace_id": "ws-1", "key": "region", "value": "us-east-1"})
        result = svc.dispatch("create_workspace_variable", {"org": "acme", "workspace_id": "ws-1", "key": "region", "value": "eu-west-1"})
        self.assertIn("error", result)

    def test_attach_variable_set_refuses_unknown_workspace(self):
        svc = TerraformService()
        svc.seed({"variable_sets": [{"org": "acme", "id": "varset-1", "name": "shared"}]})
        result = svc.dispatch("attach_variable_set_to_workspaces", {"org": "acme", "varset_id": "varset-1", "workspace_ids": ["nope"]})
        self.assertIn("error", result)

    def test_search_matches_a_natural_multi_word_query(self):
        # Live-verified: a model reasonably searched with a natural phrase
        # ("tag enforcement") that a naive whole-string substring check
        # would never match against a hyphenated key. Tokenized matching
        # (any word of the query matches) is what a real search box does.
        svc = TerraformService()
        svc.seed({"policies": {"hashicorp/require-tags": {}}}, )
        self.assertEqual(svc.dispatch("search_policies", {"query": "tag enforcement"})["policies"],
                          ["hashicorp/require-tags"])
        self.assertEqual(svc.dispatch("search_policies", {"query": "TAGS"})["policies"],
                          ["hashicorp/require-tags"])

    def test_registry_lookups_refuse_unknown_entries(self):
        svc = TerraformService()
        for tool, args in (
            ("get_provider_details", {"namespace": "hashicorp", "name": "nope"}),
            ("get_module_details", {"namespace": "a", "name": "b", "provider": "c"}),
            ("get_policy_details", {"namespace": "a", "name": "b"}),
        ):
            self.assertIn("error", svc.dispatch(tool, args), tool)

    def test_unknown_terraform_tool_name_is_error_result(self):
        svc = TerraformService()
        result = svc.dispatch("delete_everything", {})
        self.assertIn("error", result)


class TerraformSeedTests(unittest.TestCase):
    def test_seed_is_never_logged_to_call_log(self):
        svc = TerraformService()
        svc.seed({"orgs": ["acme"], "workspaces": [{"org": "acme", "id": "ws-1", "name": "prod"}]})
        self.assertEqual(svc.call_log, [])

    def test_seeding_a_workspace_auto_registers_its_org(self):
        svc = TerraformService()
        svc.seed({"workspaces": [{"org": "acme", "id": "ws-1", "name": "prod"}]})
        self.assertIn("acme", svc.dispatch("list_terraform_orgs", {})["organizations"])

    def test_seeded_workspace_variables_reachable(self):
        svc = TerraformService()
        svc.seed({"workspaces": [{"org": "acme", "id": "ws-1", "name": "prod",
                                   "variables": [{"id": "var-1", "key": "region", "value": "us-east-1"}]}]})
        result = svc.dispatch("list_workspace_variables", {"org": "acme", "workspace_id": "ws-1"})
        self.assertEqual(result["variables"], [{"id": "var-1", "key": "region", "value": "us-east-1",
                                                 "category": "terraform", "sensitive": False}])

    def test_seeded_provider_and_module_reachable(self):
        svc = TerraformService()
        svc.seed({"providers": {"hashicorp/aws": {"latest_version": "5.60.0"}},
                  "modules": {"a/b/c": {"latest_version": "1.0.0"}}})
        self.assertEqual(svc.dispatch("get_latest_provider_version", {"namespace": "hashicorp", "name": "aws"})["version"], "5.60.0")
        self.assertEqual(svc.dispatch("get_latest_module_version", {"namespace": "a", "name": "b", "provider": "c"})["version"], "1.0.0")


class TerraformCaseSchemaTests(unittest.TestCase):
    def test_minimal_valid_terraform_case_passes(self):
        case = {"name": "c", "tool_service": "terraform",
                "expected_calls": [{"tool": "list_terraform_orgs"}]}
        validate_case(case)


class ShippedTerraformCaseDryRunTests(unittest.TestCase):
    """Every tool_tf_* case, run against a hand-built ideal trajectory
    (must PASS) and at least one realistic wrong trajectory (must FAIL)."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["name"]: c for c in load_cases() if c.get("tool_service") == "terraform"}

    def _run(self, case_name: str, calls: list[tuple[str, dict]]) -> list[str]:
        case = self.cases[case_name]
        svc = get_mock_service(case["tool_service"])()
        svc.seed(case.get("tool_service_seed") or {})
        for tool, args in calls:
            svc.dispatch(tool, args)
        failures, _info = evaluate_tool_case(case, svc)
        return failures

    def test_all_31_terraform_cases_shipped(self):
        self.assertEqual(len(self.cases), 31)

    def test_check_provider_before_use_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_check_provider_before_use", [('search_providers', {'query': 'aws'}), ('get_provider_details', {'namespace': 'hashicorp', 'name': 'aws'}), ('get_latest_provider_version', {'namespace': 'hashicorp', 'name': 'aws'})]), [])
        self.assertTrue(self._run("tool_tf_check_provider_before_use", [('search_providers', {'query': 'aws'})]))

    def test_check_module_before_use_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_check_module_before_use", [('search_modules', {'query': 'vpc aws'}), ('get_module_details', {'namespace': 'terraform-aws-modules', 'name': 'vpc', 'provider': 'aws'}), ('get_latest_module_version', {'namespace': 'terraform-aws-modules', 'name': 'vpc', 'provider': 'aws'})]), [])
        self.assertTrue(self._run("tool_tf_check_module_before_use", [('search_modules', {'query': 'vpc aws'})]))

    def test_check_policy_docs_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_check_policy_docs", [('search_policies', {'query': 'tag enforcement'}), ('get_policy_details', {'namespace': 'hashicorp', 'name': 'require-tags'})]), [])
        self.assertTrue(self._run("tool_tf_check_policy_docs", [('search_policies', {'query': 'tags'})]))

    def test_check_provider_capabilities_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_check_provider_capabilities", [('get_provider_capabilities', {'namespace': 'hashicorp', 'name': 'aws'})]), [])
        self.assertTrue(self._run("tool_tf_check_provider_capabilities", []))

    def test_orientation_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_orientation", [('list_terraform_orgs', {}), ('list_terraform_projects', {'org': 'acme'})]), [])
        self.assertTrue(self._run("tool_tf_orientation", [('list_terraform_orgs', {})]))

    def test_create_project_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_create_project", [('create_project', {'org': 'acme', 'name': 'data-platform'})]), [])
        self.assertTrue(self._run("tool_tf_create_project", []))

    def test_teardown_project_and_workspace_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_teardown_project_and_workspace", [('delete_workspace_safely', {'org': 'acme', 'workspace_id': 'sandbox-dev'}), ('delete_project', {'org': 'acme', 'project_id': 'sandbox'})]), [])
        self.assertTrue(self._run("tool_tf_teardown_project_and_workspace", [('delete_project', {'org': 'acme', 'project_id': 'sandbox'})]))

    def test_manage_teams_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_manage_teams", [('list_teams', {'org': 'acme'}), ('create_team', {'org': 'acme', 'name': 'platform-eng'})]), [])
        self.assertTrue(self._run("tool_tf_manage_teams", [('list_teams', {'org': 'acme'})]))

    def test_check_token_scope_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_check_token_scope", [('get_token_permissions', {'token': 'tok-abc'})]), [])
        self.assertTrue(self._run("tool_tf_check_token_scope", []))

    def test_create_and_tag_workspace_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_create_and_tag_workspace", [('create_workspace', {'org': 'acme', 'name': 'prod-api'}), ('create_workspace_tags', {'org': 'acme', 'workspace_id': 'ws-1', 'tags': ['env:prod', 'team:api']})]), [])
        self.assertTrue(self._run("tool_tf_create_and_tag_workspace", [('create_workspace', {'org': 'acme', 'name': 'prod-api'})]))

    def test_check_before_delete_workspace_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_check_before_delete_workspace", [('list_workspaces', {'org': 'acme'}), ('get_workspace_details', {'org': 'acme', 'workspace_id': 'ws-1'}), ('delete_workspace_safely', {'org': 'acme', 'workspace_id': 'ws-1'})]), [])
        self.assertTrue(self._run("tool_tf_check_before_delete_workspace", [('list_workspaces', {'org': 'acme'})]))

    def test_unlock_before_delete_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_unlock_before_delete", [('force_unlock_workspace', {'org': 'acme', 'workspace_id': 'stuck-workspace'}), ('delete_workspace_safely', {'org': 'acme', 'workspace_id': 'stuck-workspace'})]), [])
        self.assertTrue(self._run("tool_tf_unlock_before_delete", [('delete_workspace_safely', {'org': 'acme', 'workspace_id': 'stuck-workspace'})]))

    def test_update_workspace_version_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_update_workspace_version", [('update_workspace', {'org': 'acme', 'workspace_id': 'prod', 'terraform_version': '1.9.0'})]), [])
        self.assertTrue(self._run("tool_tf_update_workspace_version", []))

    def test_set_workspace_variable_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_set_workspace_variable", [('list_workspace_variables', {'org': 'acme', 'workspace_id': 'prod'}), ('create_workspace_variable', {'org': 'acme', 'workspace_id': 'prod', 'key': 'region', 'value': 'us-east-1'})]), [])
        self.assertTrue(self._run("tool_tf_set_workspace_variable", [('list_workspace_variables', {'org': 'acme', 'workspace_id': 'prod'})]))

    def test_update_workspace_variable_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_update_workspace_variable", [('list_workspace_variables', {'org': 'acme', 'workspace_id': 'prod'}), ('update_workspace_variable', {'org': 'acme', 'workspace_id': 'prod', 'variable_id': 'var-1', 'value': 'us-west-2'})]), [])
        self.assertTrue(self._run("tool_tf_update_workspace_variable", [('list_workspace_variables', {'org': 'acme', 'workspace_id': 'prod'})]))

    def test_create_shared_variable_set_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_create_shared_variable_set", [('create_variable_set', {'org': 'acme', 'name': 'shared-networking'}), ('create_variable_in_variable_set', {'org': 'acme', 'varset_id': 'varset-1', 'key': 'vpc_cidr', 'value': '10.0.0.0/16'})]), [])
        self.assertTrue(self._run("tool_tf_create_shared_variable_set", [('create_variable_set', {'org': 'acme', 'name': 'shared-networking'})]))

    def test_attach_variable_set_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_attach_variable_set", [('list_variable_sets', {'org': 'acme'}), ('attach_variable_set_to_workspaces', {'org': 'acme', 'varset_id': 'varset-1', 'workspace_ids': ['prod']})]), [])
        self.assertTrue(self._run("tool_tf_attach_variable_set", [('list_variable_sets', {'org': 'acme'})]))

    def test_remove_variable_from_set_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_remove_variable_from_set", [('delete_variable_in_variable_set', {'org': 'acme', 'varset_id': 'shared-networking', 'key': 'old_key'})]), [])
        self.assertTrue(self._run("tool_tf_remove_variable_from_set", []))

    def test_detach_variable_set_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_detach_variable_set", [('detach_variable_set_from_workspaces', {'org': 'acme', 'varset_id': 'shared-networking', 'workspace_ids': ['prod']})]), [])
        self.assertTrue(self._run("tool_tf_detach_variable_set", []))

    def test_attach_policy_set_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_attach_policy_set", [('attach_policy_set_to_workspaces', {'org': 'acme', 'policyset_id': 'required-tags', 'workspace_ids': ['prod']}), ('list_workspace_policy_sets', {'org': 'acme', 'workspace_id': 'prod'})]), [])
        self.assertTrue(self._run("tool_tf_attach_policy_set", [('attach_policy_set_to_workspaces', {'org': 'acme', 'policyset_id': 'required-tags', 'workspace_ids': ['prod']})]))

    def test_plan_and_apply_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_plan_and_apply", [('create_run', {'org': 'acme', 'workspace_id': 'prod', 'message': 'add vpc'}), ('get_plan_logs', {'plan_id': 'plan-2'}), ('action_run', {'run_id': 'run-1', 'action': 'apply'}), ('get_apply_logs', {'apply_id': 'apply-3'})]), [])
        self.assertTrue(self._run("tool_tf_plan_and_apply", [('create_run', {'org': 'acme', 'workspace_id': 'prod'})]))

    def test_investigate_before_applying_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_investigate_before_applying", [('get_run_details', {'run_id': 'run-1'}), ('get_run_comments', {'run_id': 'run-1'})]), [])
        self.assertTrue(self._run("tool_tf_investigate_before_applying", [('action_run', {'run_id': 'run-1', 'action': 'apply'})]))

    def test_discard_bad_plan_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_discard_bad_plan", [('list_runs', {'org': 'acme', 'workspace_id': 'prod'}), ('action_run', {'run_id': 'run-1', 'action': 'discard'})]), [])
        self.assertTrue(self._run("tool_tf_discard_bad_plan", [('list_runs', {'org': 'acme', 'workspace_id': 'prod'})]))

    def test_inspect_plan_details_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_inspect_plan_details", [('get_plan_details', {'plan_id': 'plan-1'}), ('get_plan_json_output', {'plan_id': 'plan-1'})]), [])
        self.assertTrue(self._run("tool_tf_inspect_plan_details", [('get_plan_details', {'plan_id': 'plan-1'})]))

    def test_check_apply_status_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_check_apply_status", [('get_apply_details', {'apply_id': 'apply-1'})]), [])
        self.assertTrue(self._run("tool_tf_check_apply_status", []))

    def test_check_state_after_apply_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_check_state_after_apply", [('list_state_versions', {'org': 'acme', 'workspace_id': 'prod'}), ('get_state_version', {'org': 'acme', 'workspace_id': 'prod'})]), [])
        self.assertTrue(self._run("tool_tf_check_state_after_apply", [('list_state_versions', {'org': 'acme', 'workspace_id': 'prod'})]))

    def test_inspect_stack_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_inspect_stack", [('list_stacks', {'org': 'acme'}), ('get_stack_details', {'org': 'acme', 'stack_id': 'stack-1'})]), [])
        self.assertTrue(self._run("tool_tf_inspect_stack", [('list_stacks', {'org': 'acme'})]))

    def test_deploy_no_code_workspace_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_deploy_no_code_workspace", [('create_no_code_workspace', {'org': 'acme', 'name': 'quick-vpc', 'module_source': 'terraform-aws-modules/vpc/aws'})]), [])
        self.assertTrue(self._run("tool_tf_deploy_no_code_workspace", []))

    def test_test_sentinel_policy_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_test_sentinel_policy", [('get_sentinel_mock', {'policy_id': 'policy-1'})]), [])
        self.assertTrue(self._run("tool_tf_test_sentinel_policy", []))

    def test_find_internal_module_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_find_internal_module", [('search_private_modules', {'org': 'acme', 'query': 'EKS'}), ('get_private_module_details', {'org': 'acme', 'namespace': 'acme', 'name': 'eks-cluster', 'provider': 'aws'})]), [])
        self.assertTrue(self._run("tool_tf_find_internal_module", [('search_private_modules', {'org': 'acme', 'query': 'eks'})]))

    def test_find_internal_provider_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_tf_find_internal_provider", [('search_private_providers', {'org': 'acme', 'query': 'internal platform'}), ('get_private_provider_details', {'org': 'acme', 'namespace': 'acme', 'name': 'internal-platform'})]), [])
        self.assertTrue(self._run("tool_tf_find_internal_provider", [('search_private_providers', {'org': 'acme', 'query': 'internal'})]))


class TerraformCaseDriverEndToEndTests(unittest.TestCase):
    """Proves tool_service_seed reaches the mock service through the real
    driver's run_case(), same proof pattern the other services have."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_terraform_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_seeded_workspace_reachable_via_the_real_driver(self):
        from optarena.drivers.tool_chat import ToolChatDriver

        case = {
            "name": "c", "tool_service": "terraform",
            "tools": ["list_workspaces"],
            "tool_service_seed": {"workspaces": [{"org": "acme", "id": "ws-1", "name": "prod"}]},
            "prompts": ["list workspaces"],
            "expected_calls": [{"tool": "list_workspaces"}],
        }
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "list_workspaces", "arguments": {"org": "acme"}}]},
            {"content": "There's one workspace: prod."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual(result.extra["tool_calls"][0]["result"]["workspaces"][0]["name"], "prod")


if __name__ == "__main__":
    unittest.main()
