"""
package_registry mock-service tests: the 38-tool PackageRegistryService
itself (an npm-style registry plus a single local project, mirroring the
real npm-mcp reference implementation's actual server.tool() registrations
- extracted directly from its source, not its README's lower "32 tools"
estimate), including its real npm-like precondition enforcement (install
refuses an unpublished package, ci refuses without a lockfile, publish
refuses overwriting an already-published version, uninstall/run-script/
explain/unpublish/deprecate/owner/dist-tag/view refuse a package/version/
script that doesn't exist), its seed() mechanism, and all 35 shipped
tool_pkgreg_* cases - each dry-run against a hand-built "ideal" trajectory
(must PASS) and at least one realistic wrong trajectory (must FAIL), same
discriminating-oracle philosophy as the other services.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from optarena._cases._mock_service import PackageRegistryService, get_mock_service, get_tool_schemas
from optarena._cases._tool_evaluate import evaluate_tool_case
from optarena.cases import load_cases
from optarena.schema import SchemaError, validate_case
from optarena.scenario import Backend, Scenario
from tests.test_tool_use_cases import _ToolStubBackend


class PackageRegistryServiceBasicsTests(unittest.TestCase):
    def test_all_38_tools_registered(self):
        schemas = get_tool_schemas("package_registry")
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(len(names), 38)
        self.assertEqual(names, set(PackageRegistryService.TOOLS))

    def test_hyphenated_real_tool_names_map_to_valid_methods(self):
        svc = PackageRegistryService()
        self.assertEqual(PackageRegistryService.TOOLS["dist-tag"], "dist_tag")
        self.assertEqual(PackageRegistryService.TOOLS["run-script"], "run_script")
        self.assertTrue(hasattr(svc, "dist_tag"))
        self.assertTrue(hasattr(svc, "run_script"))

    def test_tools_requiring_a_project_refuse_without_one(self):
        svc = PackageRegistryService()
        for tool, args in (
            ("install", {"packages": ["x"]}), ("uninstall", {"packages": ["x"]}),
            ("update", {}), ("ci", {}), ("pkg", {"operation": "get"}),
            ("prune", {}), ("audit", {}), ("link", {"package": "x"}),
            ("run-script", {"name": "test"}), ("version", {}), ("pack", {}),
        ):
            self.assertIn("error", svc.dispatch(tool, args), tool)

    def test_init_creates_project_and_refuses_duplicate(self):
        svc = PackageRegistryService()
        created = svc.dispatch("init", {"name": "widget", "version": "1.0.0"})
        self.assertEqual(created["name"], "widget")
        self.assertIn("error", svc.dispatch("init", {}))

    def test_install_requires_package_to_exist_in_registry(self):
        svc = PackageRegistryService()
        svc.seed({"project": {}})
        result = svc.dispatch("install", {"packages": ["nope"]})
        self.assertIn("error", result)
        self.assertEqual(result["installed"], [])

    def test_install_at_pinned_version_and_default_latest(self):
        svc = PackageRegistryService()
        svc.seed({"registry": {"lodash": {"versions": ["4.17.20", "4.17.21"]}}, "project": {}})
        pinned = svc.dispatch("install", {"packages": ["lodash@4.17.20"]})
        self.assertEqual(pinned["installed"][0]["version"], "4.17.20")
        svc2 = PackageRegistryService()
        svc2.seed({"registry": {"lodash": {"versions": ["4.17.20", "4.17.21"]}}, "project": {}})
        latest = svc2.dispatch("install", {"packages": ["lodash"]})
        self.assertEqual(latest["installed"][0]["version"], "4.17.21")

    def test_uninstall_refuses_not_installed(self):
        svc = PackageRegistryService()
        svc.seed({"project": {}})
        self.assertIn("error", svc.dispatch("uninstall", {"packages": ["nope"]}))

    def test_ci_refuses_without_lockfile(self):
        svc = PackageRegistryService()
        svc.seed({"project": {}})
        self.assertIn("error", svc.dispatch("ci", {}))

    def test_ci_installs_from_declared_dependencies_when_lockfile_present(self):
        svc = PackageRegistryService()
        svc.seed({"registry": {"lodash": {"versions": ["4.17.21"]}},
                  "project": {"dependencies": {"lodash": "4.17.21"}}, "lockfile_present": True})
        result = svc.dispatch("ci", {})
        self.assertEqual(result["installed"], [{"name": "lodash", "version": "4.17.21"}])

    def test_run_script_refuses_undefined_script(self):
        svc = PackageRegistryService()
        svc.seed({"project": {"scripts": {"test": "jest"}}})
        self.assertIn("error", svc.dispatch("run-script", {"name": "build"}))
        self.assertNotIn("error", svc.dispatch("run-script", {"name": "test"}))

    def test_publish_refuses_overwriting_existing_version(self):
        svc = PackageRegistryService()
        svc.dispatch("publish", {"name": "mypkg", "version": "1.0.0"})
        result = svc.dispatch("publish", {"name": "mypkg", "version": "1.0.0"})
        self.assertIn("error", result)

    def test_unpublish_and_deprecate_refuse_unknown_version(self):
        svc = PackageRegistryService()
        svc.dispatch("publish", {"name": "mypkg", "version": "1.0.0"})
        self.assertIn("error", svc.dispatch("unpublish", {"package": "mypkg", "version": "9.9.9"}))
        self.assertIn("error", svc.dispatch("deprecate", {"package": "mypkg", "version": "9.9.9"}))

    def test_owner_dist_tag_access_view_refuse_unknown_package(self):
        svc = PackageRegistryService()
        for tool, args in (
            ("owner", {"operation": "ls", "package": "nope"}),
            ("dist-tag", {"operation": "ls", "package": "nope"}),
            ("access", {"operation": "get", "package": "nope"}),
            ("view", {"package": "nope"}),
        ):
            self.assertIn("error", svc.dispatch(tool, args), tool)

    def test_audit_reports_and_fixes_vulnerable_installed_package(self):
        svc = PackageRegistryService()
        svc.seed({"registry": {"lodash": {"versions": ["4.17.20", "4.17.21"]}},
                  "vulnerabilities": {"lodash": {"4.17.20": {"severity": "high"}}},
                  "project": {}})
        svc.dispatch("install", {"packages": ["lodash@4.17.20"]})
        report = svc.dispatch("audit", {})
        self.assertEqual(len(report["vulnerabilities"]), 1)
        fixed = svc.dispatch("audit", {"fix": True})
        self.assertEqual(fixed["fixed"], [{"name": "lodash", "fixed_to": "4.17.21"}])
        self.assertEqual(svc.dispatch("audit", {})["vulnerabilities"], [])

    def test_prune_removes_only_undeclared_installed_packages(self):
        svc = PackageRegistryService()
        svc.seed({"project": {"dependencies": {"keep": "1.0.0"}},
                  "installed": {"keep": {"version": "1.0.0"}, "orphan": {"version": "1.0.0"}}})
        result = svc.dispatch("prune", {})
        self.assertEqual(result["removed"], ["orphan"])
        self.assertEqual(svc.summary()["installed_count"], 1)

    def test_version_bump_variants(self):
        svc = PackageRegistryService()
        svc.seed({"project": {"version": "1.2.3"}})
        self.assertEqual(svc.dispatch("version", {"bump": "patch"})["version"], "1.2.4")
        self.assertEqual(svc.dispatch("version", {"bump": "minor"})["version"], "1.3.0")
        self.assertEqual(svc.dispatch("version", {"bump": "major"})["version"], "2.0.0")

    def test_pkg_set_normalizes_stringified_boolean_value(self):
        svc = PackageRegistryService()
        svc.seed({"project": {}})
        result = svc.dispatch("pkg", {"operation": "set", "field": "private", "value": "true"})
        self.assertIs(result["private"], True)
        self.assertIs(svc.call_log[-1]["arguments"]["value"], True)

    def test_pkg_set_leaves_non_boolean_string_value_untouched(self):
        svc = PackageRegistryService()
        svc.seed({"project": {}})
        svc.dispatch("pkg", {"operation": "set", "field": "license", "value": "MIT"})
        self.assertEqual(svc.call_log[-1]["arguments"]["value"], "MIT")

    def test_unknown_package_registry_tool_name_is_error_result(self):
        svc = PackageRegistryService()
        result = svc.dispatch("delete_everything", {})
        self.assertIn("error", result)


class PackageRegistrySeedTests(unittest.TestCase):
    def test_seed_is_never_logged_to_call_log(self):
        svc = PackageRegistryService()
        svc.seed({"registry": {"lodash": {"versions": ["4.17.21"]}}, "project": {},
                  "installed": {"lodash": {"version": "4.17.21"}}, "lockfile_present": True})
        self.assertEqual(svc.call_log, [])

    def test_seeded_registry_list_versions_form(self):
        svc = PackageRegistryService()
        svc.seed({"registry": {"lodash": {"versions": ["4.17.20", "4.17.21"]}}})
        self.assertEqual(svc.dispatch("view", {"package": "lodash"})["version"], "4.17.21")

    def test_seeded_registry_dict_versions_form_with_urls(self):
        svc = PackageRegistryService()
        svc.seed({"registry": {"lodash": {"versions": {"1.0.0": {"files": ["a.js"]}},
                                           "bugs_url": "https://x.invalid/bugs"}}})
        self.assertEqual(svc.dispatch("bugs", {"package": "lodash"})["url"], "https://x.invalid/bugs")

    def test_seeded_dist_tags_default_to_latest_version(self):
        svc = PackageRegistryService()
        svc.seed({"registry": {"lodash": {"versions": ["4.17.20", "4.17.21"]}}})
        self.assertEqual(svc.dispatch("dist-tag", {"operation": "ls", "package": "lodash"})["dist_tags"]["latest"], "4.17.21")


class PackageRegistryCaseSchemaTests(unittest.TestCase):
    def test_minimal_valid_package_registry_case_passes(self):
        case = {"name": "c", "tool_service": "package_registry",
                "expected_calls": [{"tool": "ping"}]}
        validate_case(case)


class ShippedPackageRegistryCaseDryRunTests(unittest.TestCase):
    """Every tool_pkgreg_* case, run against a hand-built ideal trajectory
    (must PASS) and at least one realistic wrong trajectory (must FAIL)."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["name"]: c for c in load_cases() if c.get("tool_service") == "package_registry"}

    def _run(self, case_name: str, calls: list[tuple[str, dict]]) -> list[str]:
        case = self.cases[case_name]
        svc = get_mock_service(case["tool_service"])()
        svc.seed(case.get("tool_service_seed") or {})
        for tool, args in calls:
            svc.dispatch(tool, args)
        failures, _info = evaluate_tool_case(case, svc)
        return failures

    def test_all_35_package_registry_cases_shipped(self):
        self.assertEqual(len(self.cases), 35)

    def test_bootstrap_new_project_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_bootstrap_new_project", [('init', {'name': 'widget-lib', 'version': '0.1.0'})]), [])
        self.assertTrue(self._run("tool_pkgreg_bootstrap_new_project", [('init', {})]))

    def test_edit_package_json_field_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_edit_package_json_field", [('pkg', {'operation': 'get', 'field': 'name'}), ('pkg', {'operation': 'set', 'field': 'private', 'value': True})]), [])
        self.assertTrue(self._run("tool_pkgreg_edit_package_json_field", [('pkg', {'operation': 'get', 'field': 'name'})]))

    def test_edit_package_json_field_accepts_stringified_boolean(self):
        # live-verified: a model reasonably sends "true" (string) for an
        # untyped value field, matching real npm CLI-style pkg set - the
        # dispatch()-level normalization must make this pass too.
        self.assertEqual(self._run("tool_pkgreg_edit_package_json_field", [('pkg', {'operation': 'get', 'field': 'name'}), ('pkg', {'operation': 'set', 'field': 'private', 'value': 'true'})]), [])

    def test_clean_install_from_lockfile_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_clean_install_from_lockfile", [('ci', {})]), [])
        self.assertTrue(self._run("tool_pkgreg_clean_install_from_lockfile", []))

    def test_link_local_package_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_link_local_package", [('link', {'package': 'my-shared-lib'})]), [])
        self.assertTrue(self._run("tool_pkgreg_link_local_package", []))

    def test_install_known_package_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_install_known_package", [('install', {'packages': ['axios']})]), [])
        self.assertTrue(self._run("tool_pkgreg_install_known_package", []))

    def test_no_install_unpublished_package_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_no_install_unpublished_package", [('search', {'query': 'super-fast-http'})]), [])
        self.assertTrue(self._run("tool_pkgreg_no_install_unpublished_package", [('install', {'packages': ['super-fast-http']})]))

    def test_uninstall_unused_dependency_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_uninstall_unused_dependency", [('ls', {}), ('uninstall', {'packages': ['left-pad']})]), [])
        self.assertTrue(self._run("tool_pkgreg_uninstall_unused_dependency", [('ls', {})]))

    def test_update_outdated_packages_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_update_outdated_packages", [('outdated', {}), ('update', {})]), [])
        self.assertTrue(self._run("tool_pkgreg_update_outdated_packages", [('outdated', {})]))

    def test_prune_orphaned_packages_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_prune_orphaned_packages", [('prune', {})]), [])
        self.assertTrue(self._run("tool_pkgreg_prune_orphaned_packages", []))

    def test_dedupe_tree_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_dedupe_tree", [('dedupe', {})]), [])
        self.assertTrue(self._run("tool_pkgreg_dedupe_tree", []))

    def test_check_funding_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_check_funding", [('fund', {})]), [])
        self.assertTrue(self._run("tool_pkgreg_check_funding", []))

    def test_explain_dependency_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_explain_dependency", [('explain', {'package': 'lodash'})]), [])
        self.assertTrue(self._run("tool_pkgreg_explain_dependency", []))

    def test_generate_sbom_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_generate_sbom", [('sbom', {'format': 'cyclonedx'})]), [])
        self.assertTrue(self._run("tool_pkgreg_generate_sbom", []))

    def test_query_dependency_tree_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_query_dependency_tree", [('query', {'selector': 'lo'})]), [])
        self.assertTrue(self._run("tool_pkgreg_query_dependency_tree", []))

    def test_run_test_script_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_run_test_script", [('run-script', {'name': 'test'})]), [])
        self.assertTrue(self._run("tool_pkgreg_run_test_script", []))

    def test_install_then_audit_and_fix_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_install_then_audit_and_fix", [('install', {'packages': ['lodash@4.17.20']}), ('audit', {'fix': True})]), [])
        self.assertTrue(self._run("tool_pkgreg_install_then_audit_and_fix", [('install', {'packages': ['lodash@4.17.20']})]))

    def test_doctor_diagnose_environment_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_doctor_diagnose_environment", [('doctor', {})]), [])
        self.assertTrue(self._run("tool_pkgreg_doctor_diagnose_environment", []))

    def test_ping_registry_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_ping_registry", [('ping', {})]), [])
        self.assertTrue(self._run("tool_pkgreg_ping_registry", []))

    def test_whoami_check_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_whoami_check", [('whoami', {})]), [])
        self.assertTrue(self._run("tool_pkgreg_whoami_check", []))

    def test_manage_tokens_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_manage_tokens", [('token', {'operation': 'create'}), ('token', {'operation': 'list'})]), [])
        self.assertTrue(self._run("tool_pkgreg_manage_tokens", [('token', {'operation': 'create'})]))

    def test_set_package_access_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_set_package_access", [('access', {'operation': 'set', 'package': 'mypkg', 'level': 'restricted'})]), [])
        self.assertTrue(self._run("tool_pkgreg_set_package_access", [('access', {'operation': 'get', 'package': 'mypkg'})]))

    def test_add_package_owner_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_add_package_owner", [('owner', {'operation': 'add', 'package': 'mypkg', 'user': 'bob'})]), [])
        self.assertTrue(self._run("tool_pkgreg_add_package_owner", []))

    def test_promote_beta_tag_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_promote_beta_tag", [('dist-tag', {'operation': 'add', 'package': 'mypkg', 'tag': 'beta', 'version': '2.0.0-beta.1'})]), [])
        self.assertTrue(self._run("tool_pkgreg_promote_beta_tag", []))

    def test_update_profile_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_update_profile", [('profile', {'operation': 'set', 'field': 'email', 'value': 'newmail@example.com'})]), [])
        self.assertTrue(self._run("tool_pkgreg_update_profile", []))

    def test_configure_registry_url_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_configure_registry_url", [('config', {'operation': 'set', 'key': 'registry', 'value': 'https://npm.internal.example.com/'})]), [])
        self.assertTrue(self._run("tool_pkgreg_configure_registry_url", []))

    def test_clean_cache_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_clean_cache", [('cache', {'operation': 'clean'})]), [])
        self.assertTrue(self._run("tool_pkgreg_clean_cache", [('cache', {'operation': 'verify'})]))

    def test_bump_and_publish_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_bump_and_publish", [('pkg', {'operation': 'get', 'field': 'name'}), ('version', {'bump': 'patch'}), ('publish', {'name': 'widget-lib', 'version': '1.0.1'})]), [])
        self.assertTrue(self._run("tool_pkgreg_bump_and_publish", [('version', {'bump': 'patch'})]))

    def test_check_before_republish_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_check_before_republish", [('view', {'package': 'mypkg'})]), [])
        self.assertTrue(self._run("tool_pkgreg_check_before_republish", [('publish', {'name': 'mypkg', 'version': '1.0.0'})]))

    def test_deprecate_old_version_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_deprecate_old_version", [('deprecate', {'package': 'mypkg', 'version': '1.0.0', 'message': 'use 2.0.0 instead'})]), [])
        self.assertTrue(self._run("tool_pkgreg_deprecate_old_version", []))

    def test_unpublish_bad_release_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_unpublish_bad_release", [('unpublish', {'package': 'mypkg', 'version': '1.0.1'})]), [])
        self.assertTrue(self._run("tool_pkgreg_unpublish_bad_release", []))

    def test_pack_before_publish_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_pack_before_publish", [('pack', {}), ('publish', {'name': 'widget-lib', 'version': '1.0.0'})]), [])
        self.assertTrue(self._run("tool_pkgreg_pack_before_publish", [('publish', {'name': 'widget-lib', 'version': '1.0.0'})]))

    def test_view_package_metadata_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_view_package_metadata", [('view', {'package': 'axios'})]), [])
        self.assertTrue(self._run("tool_pkgreg_view_package_metadata", []))

    def test_search_for_package_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_search_for_package", [('search', {'query': 'fetch'})]), [])
        self.assertTrue(self._run("tool_pkgreg_search_for_package", []))

    def test_check_package_links_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_check_package_links", [('bugs', {'package': 'lodash'}), ('repo', {'package': 'lodash'}), ('docs', {'package': 'lodash'})]), [])
        self.assertTrue(self._run("tool_pkgreg_check_package_links", [('bugs', {'package': 'lodash'})]))

    def test_diff_two_versions_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_pkgreg_diff_two_versions", [('diff', {'package': 'mypkg', 'from_version': '1.0.0', 'to_version': '2.0.0'})]), [])
        self.assertTrue(self._run("tool_pkgreg_diff_two_versions", []))


class PackageRegistryCaseDriverEndToEndTests(unittest.TestCase):
    """Proves tool_service_seed reaches the mock service through the real
    driver's run_case(), same proof pattern the other services have."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_package_registry_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_seeded_registry_reachable_via_the_real_driver(self):
        from optarena.drivers.tool_chat import ToolChatDriver

        case = {
            "name": "c", "tool_service": "package_registry",
            "tools": ["search"],
            "tool_service_seed": {"registry": {"axios": {"versions": ["1.6.0"]}}},
            "prompts": ["search for axios"],
            "expected_calls": [{"tool": "search"}],
        }
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "search", "arguments": {"query": "axios"}}]},
            {"content": "Found axios in the registry."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual(result.extra["tool_calls"][0]["result"]["packages"], ["axios"])


if __name__ == "__main__":
    unittest.main()
