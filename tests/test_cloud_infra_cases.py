"""
cloud_infra mock-service tests: the 9-tool CloudInfraService itself (a mock
AWS Infrastructure-as-Code assistant, mirroring the real official
awslabs/aws-iac-mcp-server's actual server.py registrations - 8 static
tools plus one dynamically-proxied read_iac_documentation_page, extracted
directly from source). AWS's real MCP landscape is unlike every other
category in this domain - not one server but ~59 separate ones under one
awslabs/mcp monorepo with no single dominant implementation; this is the
current, actively-maintained, non-deprecated pick over a richer but
deprecated resource-CRUD alternative (ccapi-mcp-server) - see
DEV_NOTES/MCP_IMPLEMENTATION_GAPS.md for the full comparison. Covers its
real preconditions (template validation refuses malformed JSON or a
missing Resources section, compliance checking flags public-access and
wildcard-IAM patterns, deployment troubleshooting refuses an unseeded
stack, page reading refuses an unseeded URL), its seed() mechanism, and
all 9 shipped tool_cloud_* cases - each dry-run against a hand-built
"ideal" trajectory (must PASS) and at least one realistic wrong
trajectory (must FAIL), same discriminating-oracle philosophy as the
other services.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from optarena._cases._mock_service import CloudInfraService, get_mock_service, get_tool_schemas
from optarena._cases._tool_evaluate import evaluate_tool_case
from optarena.cases import load_cases
from optarena.schema import SchemaError, validate_case
from optarena.scenario import Backend, Scenario
from tests.test_tool_use_cases import _ToolStubBackend


class CloudInfraServiceBasicsTests(unittest.TestCase):
    def test_all_9_tools_registered(self):
        schemas = get_tool_schemas("cloud_infra")
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(len(names), 9)
        self.assertEqual(names, set(CloudInfraService.TOOLS))

    def test_validate_template_refuses_malformed_json(self):
        svc = CloudInfraService()
        result = svc.dispatch("validate_cloudformation_template", {"template_content": "not json"})
        self.assertFalse(result["valid"])

    def test_validate_template_refuses_missing_resources(self):
        svc = CloudInfraService()
        result = svc.dispatch("validate_cloudformation_template", {"template_content": "{}"})
        self.assertFalse(result["valid"])

    def test_validate_template_accepts_well_formed_template(self):
        svc = CloudInfraService()
        template = json.dumps({"Resources": {"B": {"Type": "AWS::S3::Bucket"}}})
        result = svc.dispatch("validate_cloudformation_template", {"template_content": template})
        self.assertTrue(result["valid"])

    def test_compliance_check_flags_public_access(self):
        svc = CloudInfraService()
        template = json.dumps({"Resources": {"B": {"Type": "AWS::S3::Bucket",
                                                     "Properties": {"PubliclyAccessible": True}}}})
        result = svc.dispatch("check_cloudformation_template_compliance", {"template_content": template})
        self.assertFalse(result["is_compliant"])

    def test_compliance_check_flags_wildcard_iam_policy(self):
        svc = CloudInfraService()
        template = json.dumps({"Resources": {"P": {"Type": "AWS::IAM::Policy", "Properties": {
            "PolicyDocument": {"Statement": [{"Effect": "Allow", "Action": "*", "Resource": "*"}]}}}}})
        result = svc.dispatch("check_cloudformation_template_compliance", {"template_content": template})
        self.assertFalse(result["is_compliant"])

    def test_compliance_check_passes_clean_template(self):
        svc = CloudInfraService()
        template = json.dumps({"Resources": {"B": {"Type": "AWS::S3::Bucket"}}})
        result = svc.dispatch("check_cloudformation_template_compliance", {"template_content": template})
        self.assertTrue(result["is_compliant"])

    def test_troubleshoot_refuses_unseeded_stack(self):
        svc = CloudInfraService()
        result = svc.dispatch("troubleshoot_cloudformation_deployment", {"stack_name": "nope", "region": "us-east-1"})
        self.assertIn("error", result)

    def test_troubleshoot_returns_seeded_stack_failures(self):
        svc = CloudInfraService()
        svc.seed({"stacks": [{"name": "s1", "region": "us-east-1", "failed_resources": [{"logicalId": "B"}]}]})
        result = svc.dispatch("troubleshoot_cloudformation_deployment", {"stack_name": "s1", "region": "us-east-1"})
        self.assertEqual(result["failedResources"], [{"logicalId": "B"}])

    def test_search_cdk_samples_refuses_unknown_language(self):
        svc = CloudInfraService()
        result = svc.dispatch("search_cdk_samples_and_constructs", {"query": "x", "language": "rust"})
        self.assertIn("error", result)

    def test_read_iac_documentation_page_refuses_unseeded_url(self):
        svc = CloudInfraService()
        result = svc.dispatch("read_iac_documentation_page", {"url": "https://nope"})
        self.assertIn("error", result)

    def test_read_iac_documentation_page_returns_seeded_content(self):
        svc = CloudInfraService()
        svc.seed({"doc_pages": {"https://x": "full page content"}})
        result = svc.dispatch("read_iac_documentation_page", {"url": "https://x"})
        self.assertEqual(result["content"], "full page content")


class CloudInfraSeedTests(unittest.TestCase):
    def test_seed_is_never_logged_to_call_log(self):
        svc = CloudInfraService()
        svc.seed({"doc_pages": {"https://x": "text"}})
        self.assertEqual(svc.call_log, [])

    def test_search_matches_a_natural_multi_word_query(self):
        svc = CloudInfraService()
        svc.seed({"cdk_docs": {"s3 bucket encryption": "encryption info"}})
        result = svc.dispatch("search_cdk_documentation", {"query": "how do I enable encryption on my bucket"})
        self.assertEqual(len(result["results"]), 1)


class CloudInfraCaseSchemaTests(unittest.TestCase):
    def test_minimal_valid_cloud_infra_case_passes(self):
        case = {"name": "c", "tool_service": "cloud_infra",
                "expected_calls": [{"tool": "cdk_best_practices"}]}
        validate_case(case)


class ShippedCloudInfraCaseDryRunTests(unittest.TestCase):
    """Every tool_cloud_* case, run against a hand-built ideal trajectory
    (must PASS) and at least one realistic wrong trajectory (must FAIL)."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["name"]: c for c in load_cases() if c.get("tool_service") == "cloud_infra"}
        cls.good_template = json.dumps({"Resources": {"MyBucket": {"Type": "AWS::S3::Bucket"}}})
        cls.public_template = json.dumps({"Resources": {"MyBucket": {"Type": "AWS::S3::Bucket",
                                                                       "Properties": {"PubliclyAccessible": True}}}})

    def _run(self, case_name: str, calls: list[tuple[str, dict]]) -> list[str]:
        case = self.cases[case_name]
        svc = get_mock_service(case["tool_service"])()
        svc.seed(case.get("tool_service_seed") or {})
        for tool, args in calls:
            svc.dispatch(tool, args)
        failures, _info = evaluate_tool_case(case, svc)
        return failures

    def test_all_9_cloud_infra_cases_shipped(self):
        self.assertEqual(len(self.cases), 9)

    def test_validate_template_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_cloud_validate_template",
            [('validate_cloudformation_template', {'template_content': self.good_template})]), [])
        self.assertTrue(self._run("tool_cloud_validate_template", []))

    def test_check_compliance_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_cloud_check_compliance",
            [('check_cloudformation_template_compliance', {'template_content': self.public_template})]), [])
        self.assertTrue(self._run("tool_cloud_check_compliance", []))

    def test_troubleshoot_deployment_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_cloud_troubleshoot_deployment",
            [('troubleshoot_cloudformation_deployment', {'stack_name': 'my-app-stack', 'region': 'us-east-1'})]), [])
        self.assertTrue(self._run("tool_cloud_troubleshoot_deployment", []))

    def test_pre_deploy_instructions_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_cloud_pre_deploy_instructions",
            [('get_cloudformation_pre_deploy_validation_instructions', {})]), [])
        self.assertTrue(self._run("tool_cloud_pre_deploy_instructions", []))

    def test_search_cdk_docs_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_cloud_search_cdk_docs", [('search_cdk_documentation', {'query': 'S3 bucket encryption'})]), [])
        self.assertTrue(self._run("tool_cloud_search_cdk_docs", []))

    def test_search_cfn_docs_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_cloud_search_cfn_docs", [('search_cloudformation_documentation', {'query': 'AWS::Lambda::Function'})]), [])
        self.assertTrue(self._run("tool_cloud_search_cfn_docs", []))

    def test_find_cdk_sample_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_cloud_find_cdk_sample",
            [('search_cdk_samples_and_constructs', {'query': 'serverless api', 'language': 'python'})]), [])
        self.assertTrue(self._run("tool_cloud_find_cdk_sample", [('search_cdk_samples_and_constructs', {'query': 'serverless api'})]))

    def test_get_best_practices_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_cloud_get_best_practices", [('cdk_best_practices', {})]), [])
        self.assertTrue(self._run("tool_cloud_get_best_practices", []))

    def test_search_then_read_full_page_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_cloud_search_then_read_full_page",
            [('search_cdk_documentation', {'query': 'S3 bucket encryption'}),
             ('read_iac_documentation_page', {'url': 'https://docs.aws.amazon.com/cdk/api/v2/docs/aws-s3-readme.html'})]), [])
        self.assertTrue(self._run("tool_cloud_search_then_read_full_page", [('search_cdk_documentation', {'query': 'S3 bucket encryption'})]))


class CloudInfraCaseDriverEndToEndTests(unittest.TestCase):
    """Proves tool_service_seed reaches the mock service through the real
    driver's run_case(), same proof pattern the other services have."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_cloud_infra_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_seeded_stack_reachable_via_the_real_driver(self):
        from optarena.drivers.tool_chat import ToolChatDriver

        case = {
            "name": "c", "tool_service": "cloud_infra",
            "tools": ["troubleshoot_cloudformation_deployment"],
            "tool_service_seed": {"stacks": [{"name": "s1", "region": "us-east-1", "failed_resources": [{"logicalId": "B"}]}]},
            "prompts": ["why did stack s1 in us-east-1 fail"],
            "expected_calls": [{"tool": "troubleshoot_cloudformation_deployment"}],
        }
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "troubleshoot_cloudformation_deployment",
                              "arguments": {"stack_name": "s1", "region": "us-east-1"}}]},
            {"content": "Here's what failed."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual(result.extra["tool_calls"][0]["result"]["failedResources"], [{"logicalId": "B"}])


if __name__ == "__main__":
    unittest.main()
