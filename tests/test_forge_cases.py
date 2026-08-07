"""
forge mock-service tests: the 77-tool ForgeService itself (GitHub-MCP-
inspired issue/PR/review/label/notification workflow, including its real
GitHub-like precondition enforcement - issue_write/label_write/
projects_write create-vs-update by id presence, install-style refusal for
duplicates, merge_pull_request refusing drafts/closed/unresolved-
REQUEST_CHANGES PRs, add_comment_to_pending_review requiring an open
pending review), its seed() mechanism (including auto-registering a repo
referenced by any repo-scoped seed section), and all 46 shipped
tool_forge_* cases - each dry-run against a hand-built "ideal" trajectory
(must PASS) and at least one realistic wrong trajectory (must FAIL), same
discriminating-oracle philosophy as the other services.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from optarena._cases._mock_service import ForgeService, get_mock_service, get_tool_schemas
from optarena._cases._tool_evaluate import evaluate_tool_case
from optarena.cases import load_cases
from optarena.schema import SchemaError, validate_case
from optarena.scenario import Backend, Scenario
from tests.test_tool_use_cases import _ToolStubBackend


class ForgeServiceBasicsTests(unittest.TestCase):
    def test_all_77_tools_registered(self):
        schemas = get_tool_schemas("forge")
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(len(names), 77)
        self.assertEqual(names, set(ForgeService.TOOLS))

    def test_repo_scoped_tool_refuses_unknown_repo(self):
        svc = ForgeService()
        result = svc.dispatch("list_issues", {"owner": "acme", "repo": "nope"})
        self.assertIn("error", result)

    def test_issue_write_creates_when_number_omitted(self):
        svc = ForgeService()
        svc.seed({"repos": {"acme/webapp": {}}})
        result = svc.dispatch("issue_write", {"owner": "acme", "repo": "webapp", "title": "New bug"})
        self.assertEqual(result["number"], 1)
        self.assertEqual(result["state"], "open")

    def test_issue_write_updates_when_number_given(self):
        svc = ForgeService()
        svc.seed({"issues": [{"owner": "acme", "repo": "webapp", "number": 1, "title": "Bug"}]})
        result = svc.dispatch("issue_write", {"owner": "acme", "repo": "webapp", "issue_number": 1, "state": "closed"})
        self.assertEqual(result["state"], "closed")

    def test_issue_write_unknown_number_is_error(self):
        svc = ForgeService()
        svc.seed({"repos": {"acme/webapp": {}}})
        result = svc.dispatch("issue_write", {"owner": "acme", "repo": "webapp", "issue_number": 99, "title": "x"})
        self.assertIn("error", result)

    def test_label_write_creates_and_updates(self):
        svc = ForgeService()
        svc.seed({"repos": {"acme/webapp": {}}})
        created = svc.dispatch("label_write", {"owner": "acme", "repo": "webapp", "name": "bug", "color": "d73a4a"})
        self.assertEqual(created["color"], "d73a4a")
        updated = svc.dispatch("label_write", {"owner": "acme", "repo": "webapp", "name": "bug", "color": "ff0000"})
        self.assertEqual(updated["color"], "ff0000")

    def test_label_write_delete_unknown_label_is_error(self):
        svc = ForgeService()
        svc.seed({"repos": {"acme/webapp": {}}})
        result = svc.dispatch("label_write", {"owner": "acme", "repo": "webapp", "name": "nope", "delete": True})
        self.assertIn("error", result)

    def test_sub_issue_write_requires_both_issues_to_exist(self):
        svc = ForgeService()
        svc.seed({"issues": [{"owner": "acme", "repo": "webapp", "number": 1, "title": "Parent"}]})
        result = svc.dispatch("sub_issue_write", {"owner": "acme", "repo": "webapp", "issue_number": 1, "sub_issue_number": 99})
        self.assertIn("error", result)

    def test_create_pull_request_requires_both_branches(self):
        svc = ForgeService()
        svc.seed({"repos": {"acme/webapp": {}}})
        result = svc.dispatch("create_pull_request", {"owner": "acme", "repo": "webapp", "title": "x",
                                                        "head": "nope", "base": "main"})
        self.assertIn("error", result)

    def test_merge_pull_request_refuses_draft(self):
        svc = ForgeService()
        svc.seed({"pull_requests": [{"owner": "acme", "repo": "webapp", "number": 1, "title": "x", "draft": True}]})
        result = svc.dispatch("merge_pull_request", {"owner": "acme", "repo": "webapp", "pull_number": 1})
        self.assertIn("error", result)

    def test_merge_pull_request_refuses_already_merged(self):
        svc = ForgeService()
        svc.seed({"pull_requests": [{"owner": "acme", "repo": "webapp", "number": 1, "title": "x", "merged": True, "state": "closed"}]})
        result = svc.dispatch("merge_pull_request", {"owner": "acme", "repo": "webapp", "pull_number": 1})
        self.assertIn("error", result)

    def test_merge_pull_request_refuses_unresolved_change_request(self):
        svc = ForgeService()
        svc.seed({"pull_requests": [{"owner": "acme", "repo": "webapp", "number": 1, "title": "x",
                                      "reviews": [{"event": "REQUEST_CHANGES"}]}]})
        result = svc.dispatch("merge_pull_request", {"owner": "acme", "repo": "webapp", "pull_number": 1})
        self.assertIn("error", result)

    def test_merge_pull_request_succeeds_after_approve_supersedes_change_request(self):
        svc = ForgeService()
        svc.seed({"pull_requests": [{"owner": "acme", "repo": "webapp", "number": 1, "title": "x",
                                      "reviews": [{"event": "REQUEST_CHANGES"}, {"event": "APPROVE"}]}]})
        result = svc.dispatch("merge_pull_request", {"owner": "acme", "repo": "webapp", "pull_number": 1})
        self.assertTrue(result["merged"])

    def test_pull_request_review_write_with_no_event_starts_pending(self):
        svc = ForgeService()
        svc.seed({"pull_requests": [{"owner": "acme", "repo": "webapp", "number": 1, "title": "x"}]})
        result = svc.dispatch("pull_request_review_write", {"owner": "acme", "repo": "webapp", "pull_number": 1})
        self.assertEqual(result["review_state"], "pending")

    def test_add_comment_to_pending_review_requires_open_review(self):
        svc = ForgeService()
        svc.seed({"pull_requests": [{"owner": "acme", "repo": "webapp", "number": 1, "title": "x"}]})
        result = svc.dispatch("add_comment_to_pending_review", {"owner": "acme", "repo": "webapp", "pull_number": 1,
                                                                  "path": "a.py", "line": 1, "body": "x"})
        self.assertIn("error", result)

    def test_add_comment_to_pending_review_succeeds_after_starting_one(self):
        svc = ForgeService()
        svc.seed({"pull_requests": [{"owner": "acme", "repo": "webapp", "number": 1, "title": "x"}]})
        svc.dispatch("pull_request_review_write", {"owner": "acme", "repo": "webapp", "pull_number": 1})
        result = svc.dispatch("add_comment_to_pending_review", {"owner": "acme", "repo": "webapp", "pull_number": 1,
                                                                  "path": "a.py", "line": 1, "body": "x"})
        self.assertNotIn("error", result)

    def test_create_repository_duplicate_is_error(self):
        svc = ForgeService()
        svc.dispatch("create_repository", {"name": "webapp"})
        result = svc.dispatch("create_repository", {"name": "webapp"})
        self.assertIn("error", result)

    def test_fork_repository_unknown_source_is_error(self):
        svc = ForgeService()
        result = svc.dispatch("fork_repository", {"owner": "acme", "repo": "nope"})
        self.assertIn("error", result)

    def test_create_branch_duplicate_is_error(self):
        svc = ForgeService()
        svc.seed({"repos": {"acme/webapp": {}}})
        svc.dispatch("create_branch", {"owner": "acme", "repo": "webapp", "branch": "feature"})
        result = svc.dispatch("create_branch", {"owner": "acme", "repo": "webapp", "branch": "feature"})
        self.assertIn("error", result)

    def test_delete_file_unknown_path_is_error(self):
        svc = ForgeService()
        svc.seed({"repos": {"acme/webapp": {}}})
        result = svc.dispatch("delete_file", {"owner": "acme", "repo": "webapp", "path": "nope.md", "message": "m"})
        self.assertIn("error", result)

    def test_push_files_unknown_branch_is_error(self):
        svc = ForgeService()
        svc.seed({"repos": {"acme/webapp": {}}})
        result = svc.dispatch("push_files", {"owner": "acme", "repo": "webapp", "branch": "nope",
                                              "files": {"a.py": "1"}, "message": "m"})
        self.assertIn("error", result)

    def test_install_style_projects_write_and_helm_release_semantics(self):
        # projects_write: omit project_id to create, include it to update, unknown id is an error.
        svc = ForgeService()
        created = svc.dispatch("projects_write", {"owner": "acme", "title": "Roadmap"})
        self.assertEqual(created["title"], "Roadmap")
        updated = svc.dispatch("projects_write", {"owner": "acme", "project_id": created["id"], "title": "Roadmap v2"})
        self.assertEqual(updated["title"], "Roadmap v2")
        self.assertIn("error", svc.dispatch("projects_write", {"owner": "acme", "project_id": "nope", "title": "x"}))

    def test_dismiss_and_manage_notification_unknown_id_is_error(self):
        svc = ForgeService()
        self.assertIn("error", svc.dispatch("dismiss_notification", {"notification_id": "nope"}))
        self.assertIn("error", svc.dispatch("manage_notification_subscription", {"notification_id": "nope", "action": "ignore"}))

    def test_unknown_forge_tool_name_is_error_result(self):
        svc = ForgeService()
        result = svc.dispatch("delete_everything", {})
        self.assertIn("error", result)


class ForgeSeedTests(unittest.TestCase):
    def test_seed_is_never_logged_to_call_log(self):
        svc = ForgeService()
        svc.seed({"repos": {"acme/webapp": {}}, "issues": [{"owner": "acme", "repo": "webapp", "number": 1, "title": "x"}]})
        self.assertEqual(svc.call_log, [])

    def test_seeding_an_issue_auto_registers_its_repo(self):
        svc = ForgeService()
        svc.seed({"issues": [{"owner": "acme", "repo": "webapp", "number": 1, "title": "x"}]})
        result = svc.dispatch("issue_write", {"owner": "acme", "repo": "webapp", "issue_number": 1, "state": "closed"})
        self.assertNotIn("error", result)

    def test_seeding_a_pull_request_auto_registers_its_repo_and_branches(self):
        svc = ForgeService()
        svc.seed({"pull_requests": [{"owner": "acme", "repo": "webapp", "number": 1, "title": "x",
                                      "head": "feature-x", "base": "main"}]})
        result = svc.dispatch("merge_pull_request", {"owner": "acme", "repo": "webapp", "pull_number": 1})
        self.assertTrue(result["merged"])

    def test_seeded_files_reachable(self):
        svc = ForgeService()
        svc.seed({"files": {"acme/webapp": {"README.md": "# hi"}}})
        result = svc.dispatch("get_file_contents", {"owner": "acme", "repo": "webapp", "path": "README.md"})
        self.assertEqual(result["content"], "# hi")

    def test_seeded_tags_and_releases_reachable(self):
        svc = ForgeService()
        svc.seed({"tags": {"acme/webapp": {"v1.0.0": "sha1"}},
                  "releases": {"acme/webapp": {"v1.0.0": {"name": "First", "body": "", "draft": False, "prerelease": False}}}})
        self.assertEqual(svc.dispatch("get_tag", {"owner": "acme", "repo": "webapp", "tag": "v1.0.0"})["sha"], "sha1")
        self.assertEqual(svc.dispatch("get_latest_release", {"owner": "acme", "repo": "webapp"})["name"], "First")

    def test_seeded_commits_reachable(self):
        svc = ForgeService()
        svc.seed({"commits": {"acme/webapp": {"sha1": {"message": "Initial commit", "files": ["a.py"]}}}})
        result = svc.dispatch("get_commit", {"owner": "acme", "repo": "webapp", "sha": "sha1"})
        self.assertEqual(result["message"], "Initial commit")


class ForgeCaseSchemaTests(unittest.TestCase):
    def test_minimal_valid_forge_case_passes(self):
        case = {"name": "c", "tool_service": "forge",
                "expected_calls": [{"tool": "get_me"}]}
        validate_case(case)


class ShippedForgeCaseDryRunTests(unittest.TestCase):
    """Every tool_forge_* case, run against a hand-built ideal trajectory
    (must PASS) and at least one realistic wrong trajectory (must FAIL)."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["name"]: c for c in load_cases() if c.get("tool_service") == "forge"}

    def _run(self, case_name: str, calls: list[tuple[str, dict]]) -> list[str]:
        case = self.cases[case_name]
        svc = get_mock_service(case["tool_service"])()
        svc.seed(case.get("tool_service_seed") or {})
        for tool, args in calls:
            svc.dispatch(tool, args)
        failures, _info = evaluate_tool_case(case, svc)
        return failures

    def test_all_47_forge_cases_shipped(self):
        self.assertEqual(len(self.cases), 47)

    def test_investigate_specific_alert_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_investigate_specific_alert", [
            ('get_code_scanning_alert', {'owner': 'acme', 'repo': 'webapp', 'alert_number': 1}),
            ('get_dependabot_alert', {'owner': 'acme', 'repo': 'webapp', 'alert_number': 1}),
            ('get_secret_scanning_alert', {'owner': 'acme', 'repo': 'webapp', 'alert_number': 1}),
        ]), [])
        self.assertTrue(self._run("tool_forge_investigate_specific_alert", [
            ('get_code_scanning_alert', {'owner': 'acme', 'repo': 'webapp', 'alert_number': 1}),
        ]))

    def test_orientation_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_orientation", [('get_me', {}), ('get_teams', {'org': 'acme'}), ('get_team_members', {'org': 'acme', 'team_slug': 'backend'}), ('search_orgs', {'query': 'acme'})]), [])
        self.assertTrue(self._run("tool_forge_orientation", [('get_me', {})]))

    def test_security_posture_review_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_security_posture_review", [('list_code_scanning_alerts', {'owner': 'acme', 'repo': 'webapp'}), ('list_dependabot_alerts', {'owner': 'acme', 'repo': 'webapp'}), ('list_secret_scanning_alerts', {'owner': 'acme', 'repo': 'webapp'}), ('get_code_quality_finding', {'owner': 'acme', 'repo': 'webapp', 'finding_id': 'cq1'})]), [])
        self.assertTrue(self._run("tool_forge_security_posture_review", [('list_code_scanning_alerts', {'owner': 'acme', 'repo': 'webapp'})]))

    def test_explore_then_edit_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_explore_then_edit", [('get_repository_tree', {'owner': 'acme', 'repo': 'webapp'}), ('get_file_contents', {'owner': 'acme', 'repo': 'webapp', 'path': 'README.md'}), ('create_or_update_file', {'owner': 'acme', 'repo': 'webapp', 'path': 'README.md', 'content': '# webapp\n\n## License\nMIT\n', 'message': 'add license'})]), [])
        self.assertTrue(self._run("tool_forge_explore_then_edit", [('create_or_update_file', {'owner': 'acme', 'repo': 'webapp', 'path': 'README.md', 'content': 'blind overwrite', 'message': 'm'})]))

    def test_trigger_and_check_run_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_trigger_and_check_run", [('actions_run_trigger', {'owner': 'acme', 'repo': 'webapp', 'workflow_id': 'ci.yml', 'ref': 'main'}), ('actions_get', {'owner': 'acme', 'repo': 'webapp', 'run_id': 1})]), [])
        self.assertTrue(self._run("tool_forge_trigger_and_check_run", [('actions_run_trigger', {'owner': 'acme', 'repo': 'webapp', 'workflow_id': 'ci.yml'})]))

    def test_debug_failed_run_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_debug_failed_run", [('actions_list', {'owner': 'acme', 'repo': 'webapp'}), ('get_job_logs', {'owner': 'acme', 'repo': 'webapp', 'job_id': 'job-1'})]), [])
        self.assertTrue(self._run("tool_forge_debug_failed_run", [('actions_list', {'owner': 'acme', 'repo': 'webapp'})]))

    def test_delegate_issue_with_intent_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_delegate_issue_with_intent", [('assign_copilot_to_issue_with_intent', {'owner': 'acme', 'repo': 'webapp', 'issue_number': 1, 'intent': 'investigate CI flakiness'})]), [])
        self.assertTrue(self._run("tool_forge_delegate_issue_with_intent", [('assign_copilot_to_issue', {'owner': 'acme', 'repo': 'webapp', 'issue_number': 1})]))

    def test_quick_copilot_assign_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_quick_copilot_assign", [('assign_copilot_to_issue', {'owner': 'acme', 'repo': 'webapp', 'issue_number': 1})]), [])
        self.assertTrue(self._run("tool_forge_quick_copilot_assign", []))

    def test_request_copilot_pr_review_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_request_copilot_pr_review", [('request_copilot_review', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 2})]), [])
        self.assertTrue(self._run("tool_forge_request_copilot_pr_review", []))

    def test_answer_discussion_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_answer_discussion", [('list_discussions', {'owner': 'acme', 'repo': 'webapp'}), ('get_discussion', {'owner': 'acme', 'repo': 'webapp', 'discussion_number': 1}), ('get_discussion_comments', {'owner': 'acme', 'repo': 'webapp', 'discussion_number': 1}), ('discussion_comment_write', {'owner': 'acme', 'repo': 'webapp', 'discussion_number': 1, 'body': 'Try memoizing the query.'})]), [])
        self.assertTrue(self._run("tool_forge_answer_discussion", [('list_discussions', {'owner': 'acme', 'repo': 'webapp'})]))

    def test_discussion_categories_overview_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_discussion_categories_overview", [('list_discussion_categories', {'owner': 'acme', 'repo': 'webapp'})]), [])
        self.assertTrue(self._run("tool_forge_discussion_categories_overview", []))

    def test_share_snippet_as_gist_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_share_snippet_as_gist", [('create_gist', {'description': 'quick debug helper', 'public': True, 'files': {'debug-snippet.py': 'print("debug")'}})]), [])
        self.assertTrue(self._run("tool_forge_share_snippet_as_gist", [('create_gist', {'description': 'wrong', 'public': False, 'files': {'a.py': 'x'}})]))

    def test_update_existing_gist_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_update_existing_gist", [('list_gists', {}), ('get_gist', {'gist_id': 'gist1'}), ('update_gist', {'gist_id': 'gist1', 'files': {'debug.py': 'print(2)'}})]), [])
        self.assertTrue(self._run("tool_forge_update_existing_gist", [('list_gists', {})]))

    def test_report_and_label_bug_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_report_and_label_bug", [('label_write', {'owner': 'acme', 'repo': 'webapp', 'name': 'bug', 'color': 'd73a4a'}), ('issue_write', {'owner': 'acme', 'repo': 'webapp', 'title': 'Login button unresponsive on mobile', 'labels': ['bug']})]), [])
        self.assertTrue(self._run("tool_forge_report_and_label_bug", [('issue_write', {'owner': 'acme', 'repo': 'webapp', 'title': 'Login button unresponsive on mobile'})]))

    def test_close_resolved_issue_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_close_resolved_issue", [('issue_read', {'owner': 'acme', 'repo': 'webapp', 'issue_number': 3}), ('issue_write', {'owner': 'acme', 'repo': 'webapp', 'issue_number': 3, 'state': 'closed'})]), [])
        self.assertTrue(self._run("tool_forge_close_resolved_issue", [('issue_read', {'owner': 'acme', 'repo': 'webapp', 'issue_number': 3})]))

    def test_comment_before_close_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_comment_before_close", [('add_issue_comment', {'owner': 'acme', 'repo': 'webapp', 'issue_number': 4, 'body': 'Not planned for this release.'}), ('issue_write', {'owner': 'acme', 'repo': 'webapp', 'issue_number': 4, 'state': 'closed'})]), [])
        self.assertTrue(self._run("tool_forge_comment_before_close", [('issue_write', {'owner': 'acme', 'repo': 'webapp', 'issue_number': 4, 'state': 'closed'})]))

    def test_no_duplicate_issue_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_no_duplicate_issue", [('search_issues', {'query': 'crash uploading large files'})]), [])
        self.assertTrue(self._run("tool_forge_no_duplicate_issue", [('issue_write', {'owner': 'acme', 'repo': 'webapp', 'title': 'App crashes on large upload'})]))

    def test_sub_issue_breakdown_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_sub_issue_breakdown", [('sub_issue_write', {'owner': 'acme', 'repo': 'webapp', 'issue_number': 6, 'sub_issue_number': 7})]), [])
        self.assertTrue(self._run("tool_forge_sub_issue_breakdown", []))

    def test_check_label_before_use_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_check_label_before_use", [('list_label', {'owner': 'acme', 'repo': 'webapp'}), ('get_label', {'owner': 'acme', 'repo': 'webapp', 'name': 'enhancement'}), ('issue_write', {'owner': 'acme', 'repo': 'webapp', 'title': 'Add dark mode', 'labels': ['enhancement']})]), [])
        self.assertTrue(self._run("tool_forge_check_label_before_use", [('issue_write', {'owner': 'acme', 'repo': 'webapp', 'title': 'Add dark mode', 'labels': ['enhancement']})]))

    def test_list_issue_custom_fields_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_list_issue_custom_fields", [('list_issue_fields', {'owner': 'acme', 'repo': 'webapp'}), ('list_issue_types', {'owner': 'acme', 'repo': 'webapp'})]), [])
        self.assertTrue(self._run("tool_forge_list_issue_custom_fields", [('list_issue_fields', {'owner': 'acme', 'repo': 'webapp'})]))

    def test_list_open_bugs_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_list_open_bugs", [('list_issues', {'owner': 'acme', 'repo': 'webapp', 'state': 'open', 'labels': ['bug']})]), [])
        self.assertTrue(self._run("tool_forge_list_open_bugs", [('list_issues', {'owner': 'acme', 'repo': 'webapp'})]))

    def test_triage_notifications_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_triage_notifications", [('list_notifications', {'owner': 'acme', 'repo': 'webapp'}), ('get_notification_details', {'notification_id': 'n1'}), ('dismiss_notification', {'notification_id': 'n1'})]), [])
        self.assertTrue(self._run("tool_forge_triage_notifications", [('list_notifications', {'owner': 'acme', 'repo': 'webapp'})]))

    def test_mute_noisy_repo_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_mute_noisy_repo", [('manage_repository_notification_subscription', {'owner': 'acme', 'repo': 'webapp', 'action': 'ignore'})]), [])
        self.assertTrue(self._run("tool_forge_mute_noisy_repo", []))

    def test_clear_inbox_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_clear_inbox", [('mark_all_notifications_read', {})]), [])
        self.assertTrue(self._run("tool_forge_clear_inbox", [('dismiss_notification', {'notification_id': 'n1'})]))

    def test_unsubscribe_from_thread_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_unsubscribe_from_thread", [('list_notifications', {'owner': 'acme', 'repo': 'webapp'}), ('manage_notification_subscription', {'notification_id': 'n1', 'action': 'delete'})]), [])
        self.assertTrue(self._run("tool_forge_unsubscribe_from_thread", [('list_notifications', {'owner': 'acme', 'repo': 'webapp'})]))

    def test_create_roadmap_project_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_create_roadmap_project", [('projects_write', {'owner': 'acme', 'title': 'Q3 Roadmap'})]), [])
        self.assertTrue(self._run("tool_forge_create_roadmap_project", []))

    def test_update_existing_project_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_update_existing_project", [('projects_list', {'owner': 'acme'}), ('projects_get', {'project_id': 'proj1'}), ('projects_write', {'project_id': 'proj1', 'title': 'Q2 Roadmap (archived)'})]), [])
        self.assertTrue(self._run("tool_forge_update_existing_project", [('projects_list', {'owner': 'acme'})]))

    def test_open_pr_for_fix_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_open_pr_for_fix", [('create_pull_request', {'owner': 'acme', 'repo': 'webapp', 'title': 'Fix login crash', 'head': 'feature-fix', 'base': 'main'})]), [])
        self.assertTrue(self._run("tool_forge_open_pr_for_fix", []))

    def test_dont_merge_with_changes_requested_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_dont_merge_with_changes_requested", [('pull_request_read', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 2})]), [])
        self.assertTrue(self._run("tool_forge_dont_merge_with_changes_requested", [('merge_pull_request', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 2})]))

    def test_approve_and_merge_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_approve_and_merge", [('pull_request_review_write', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 3, 'event': 'APPROVE'}), ('merge_pull_request', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 3})]), [])
        self.assertTrue(self._run("tool_forge_approve_and_merge", [('pull_request_review_write', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 3, 'event': 'APPROVE'})]))

    def test_line_by_line_review_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_line_by_line_review", [('pull_request_review_write', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 4}), ('add_comment_to_pending_review', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 4, 'path': 'auth.py', 'line': 42, 'body': 'Add a docstring here.'}), ('pull_request_review_write', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 4, 'event': 'COMMENT'})]), [])
        self.assertTrue(self._run("tool_forge_line_by_line_review", [('pull_request_review_write', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 4, 'event': 'COMMENT'})]))

    def test_reply_to_review_comment_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_reply_to_review_comment", [('add_reply_to_pull_request_comment', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 5, 'comment_id': 17, 'body': 'Good catch, fixed in the latest commit.'})]), [])
        self.assertTrue(self._run("tool_forge_reply_to_review_comment", []))

    def test_sync_pr_with_base_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_sync_pr_with_base", [('update_pull_request_branch', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 6})]), [])
        self.assertTrue(self._run("tool_forge_sync_pr_with_base", []))

    def test_retarget_pr_base_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_retarget_pr_base", [('update_pull_request', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 7, 'base': 'release-2.0'})]), [])
        self.assertTrue(self._run("tool_forge_retarget_pr_base", [('update_pull_request', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 7, 'title': 'Hotfix (updated)'})]))

    def test_find_stale_prs_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_find_stale_prs", [('list_pull_requests', {'owner': 'acme', 'repo': 'webapp', 'state': 'open'}), ('search_pull_requests', {'query': 'WIP'}), ('pull_request_read', {'owner': 'acme', 'repo': 'webapp', 'pull_number': 8})]), [])
        self.assertTrue(self._run("tool_forge_find_stale_prs", [('list_pull_requests', {'owner': 'acme', 'repo': 'webapp', 'state': 'open'})]))

    def test_bootstrap_new_repo_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_bootstrap_new_repo", [('create_repository', {'name': 'internal-tools', 'private': True})]), [])
        self.assertTrue(self._run("tool_forge_bootstrap_new_repo", [('create_repository', {'name': 'internal-tools', 'private': False})]))

    def test_fork_before_contributing_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_fork_before_contributing", [('fork_repository', {'owner': 'acme', 'repo': 'webapp'})]), [])
        self.assertTrue(self._run("tool_forge_fork_before_contributing", []))

    def test_find_repo_by_name_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_find_repo_by_name", [('search_repositories', {'query': 'webapp'})]), [])
        self.assertTrue(self._run("tool_forge_find_repo_by_name", []))

    def test_remove_deprecated_file_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_remove_deprecated_file", [('delete_file', {'owner': 'acme', 'repo': 'webapp', 'path': 'legacy.py', 'message': 'Remove deprecated module'})]), [])
        self.assertTrue(self._run("tool_forge_remove_deprecated_file", []))

    def test_batch_update_files_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_batch_update_files", [('push_files', {'owner': 'acme', 'repo': 'webapp', 'branch': 'main', 'files': {'config/dev.yaml': 'debug: true', 'config/prod.yaml': 'debug: false'}, 'message': 'Add environment configs'})]), [])
        self.assertTrue(self._run("tool_forge_batch_update_files", [('create_or_update_file', {'owner': 'acme', 'repo': 'webapp', 'path': 'config/dev.yaml', 'content': 'debug: true', 'message': 'm'})]))

    def test_branch_before_pr_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_branch_before_pr", [('create_branch', {'owner': 'acme', 'repo': 'webapp', 'branch': 'feature-search'}), ('list_branches', {'owner': 'acme', 'repo': 'webapp'})]), [])
        self.assertTrue(self._run("tool_forge_branch_before_pr", [('create_branch', {'owner': 'acme', 'repo': 'webapp', 'branch': 'feature-search'})]))

    def test_inspect_commit_history_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_inspect_commit_history", [('list_commits', {'owner': 'acme', 'repo': 'webapp'}), ('get_commit', {'owner': 'acme', 'repo': 'webapp', 'sha': 'sha1'})]), [])
        self.assertTrue(self._run("tool_forge_inspect_commit_history", [('list_commits', {'owner': 'acme', 'repo': 'webapp'})]))

    def test_find_commit_by_message_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_find_commit_by_message", [('search_commits', {'owner': 'acme', 'repo': 'webapp', 'query': 'login'})]), [])
        self.assertTrue(self._run("tool_forge_find_commit_by_message", []))

    def test_find_code_across_repos_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_find_code_across_repos", [('search_code', {'query': 'handle_login'})]), [])
        self.assertTrue(self._run("tool_forge_find_code_across_repos", []))

    def test_check_release_tag_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_check_release_tag", [('get_tag', {'owner': 'acme', 'repo': 'webapp', 'tag': 'v1.0.0'}), ('list_tags', {'owner': 'acme', 'repo': 'webapp'})]), [])
        self.assertTrue(self._run("tool_forge_check_release_tag", [('get_tag', {'owner': 'acme', 'repo': 'webapp', 'tag': 'v1.0.0'})]))

    def test_release_notes_lookup_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_release_notes_lookup", [('get_latest_release', {'owner': 'acme', 'repo': 'webapp'}), ('get_release_by_tag', {'owner': 'acme', 'repo': 'webapp', 'tag': 'v1.0.0'}), ('list_releases', {'owner': 'acme', 'repo': 'webapp'})]), [])
        self.assertTrue(self._run("tool_forge_release_notes_lookup", [('get_latest_release', {'owner': 'acme', 'repo': 'webapp'})]))

    def test_list_collaborators_before_assigning_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_forge_list_collaborators_before_assigning", [('list_repository_collaborators', {'owner': 'acme', 'repo': 'webapp'})]), [])
        self.assertTrue(self._run("tool_forge_list_collaborators_before_assigning", []))


class ForgeCaseDriverEndToEndTests(unittest.TestCase):
    """Proves tool_service_seed reaches the mock service through the real
    driver's run_case(), same proof pattern the other services have."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_forge_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_seeded_issue_reachable_via_the_real_driver(self):
        from optarena.drivers.tool_chat import ToolChatDriver

        case = {
            "name": "c", "tool_service": "forge",
            "tools": ["list_issues"],
            "tool_service_seed": {"issues": [{"owner": "acme", "repo": "webapp", "number": 1, "title": "Crash on login"}]},
            "prompts": ["list issues"],
            "expected_calls": [{"tool": "list_issues"}],
        }
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "list_issues", "arguments": {"owner": "acme", "repo": "webapp"}}]},
            {"content": "There's one issue: Crash on login."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual(result.extra["tool_calls"][0]["result"]["issues"][0]["title"], "Crash on login")


if __name__ == "__main__":
    unittest.main()
