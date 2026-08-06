"""
git_repo mock-service tests: the 18-tool GitRepoService itself, its seed()
mechanism, the blame algorithm specifically, the oracle's list-argument
matcher enhancement, and all 12 shipped tool_git_* cases - each dry-run
against a hand-built "ideal" trajectory (must PASS) and at least one
realistic wrong trajectory (must FAIL), the same discriminating-oracle
philosophy the coding corpus's reference_solution/broken_solutions pairs
enforce, done here as fast synthetic checks rather than live-model runs.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from optarena._cases._mock_service import GitRepoService, get_mock_service, get_tool_schemas
from optarena._cases._tool_evaluate import evaluate_tool_case
from optarena.cases import load_cases
from optarena.schema import SchemaError, validate_case
from optarena.scenario import Backend, Scenario
from tests.test_tool_use_cases import _ToolStubBackend


class GitRepoServiceBasicsTests(unittest.TestCase):
    def test_all_18_tools_registered(self):
        schemas = get_tool_schemas("git_repo")
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(len(names), 18)
        self.assertEqual(names, set(GitRepoService.TOOLS))

    def test_status_reports_staged_unstaged_untracked(self):
        svc = GitRepoService()
        svc.seed({"committed": {"a.py": "1\n"}, "working_dir": {"a.py": "2\n", "b.py": "new\n"}})
        status = svc.dispatch("git_status", {})
        self.assertEqual(status, {"staged": [], "unstaged_modified": ["a.py"], "untracked": ["b.py"]})

    def test_add_accepts_bare_string_not_just_a_list(self):
        """Real models sometimes pass a single path as a string instead of
        a one-element list - must not silently iterate it character by
        character."""
        svc = GitRepoService()
        svc.seed({"committed": {"a.py": "1\n"}, "working_dir": {"a.py": "2\n"}})
        result = svc.dispatch("git_add", {"paths": "a.py"})
        self.assertEqual(result["staged"], ["a.py"])

    def test_add_unknown_path_reports_error_not_crash(self):
        svc = GitRepoService()
        result = svc.dispatch("git_add", {"paths": ["nope.py"]})
        self.assertIn("error", result)

    def test_commit_with_nothing_staged_is_an_error_result(self):
        svc = GitRepoService()
        svc.seed({"committed": {"a.py": "1\n"}})
        result = svc.dispatch("git_commit", {"message": "x"})
        self.assertIn("error", result)
        self.assertEqual(svc.summary()["commits_on_current_branch"], 1)

    def test_reset_clears_staged_without_touching_working_tree(self):
        svc = GitRepoService()
        svc.seed({"committed": {"a.py": "1\n"}, "working_dir": {"a.py": "2\n"}})
        svc.dispatch("git_add", {"paths": ["a.py"]})
        svc.dispatch("git_reset", {})
        status = svc.dispatch("git_status", {})
        self.assertEqual(status["staged"], [])
        self.assertEqual(status["unstaged_modified"], ["a.py"])   # still modified, just unstaged

    def test_commit_snapshot_carries_forward_unrelated_files_unchanged(self):
        """A commit is a full-tree snapshot (real git semantics) - staging
        only file A must not drop file B from the resulting commit."""
        svc = GitRepoService()
        svc.seed({"committed": {"a.py": "1\n", "b.py": "1\n"}, "working_dir": {"a.py": "2\n", "b.py": "1\n"}})
        svc.dispatch("git_add", {"paths": ["a.py"]})
        result = svc.dispatch("git_commit", {"message": "update a"})
        self.assertEqual(sorted(result["files_committed"]), ["a.py", "b.py"])

    def test_diff_unstaged_only_shows_tracked_modified_files(self):
        svc = GitRepoService()
        svc.seed({"committed": {"a.py": "1\n"}, "working_dir": {"a.py": "2\n", "new.py": "x\n"}})
        diff = svc.dispatch("git_diff_unstaged", {})
        self.assertEqual(set(diff["files"]), {"a.py"})   # untracked new.py doesn't show in a diff

    def test_log_and_show_head_alias(self):
        svc = GitRepoService()
        svc.seed({"initial_commits": [{"message": "one", "files": {"a": "1\n"}},
                                      {"message": "two", "files": {"a": "2\n"}}]})
        log = svc.dispatch("git_log", {})
        self.assertEqual([c["message"] for c in log["commits"]], ["two", "one"])
        show = svc.dispatch("git_show", {"ref": "HEAD"})
        self.assertEqual(show["message"], "two")

    def test_create_branch_does_not_switch_to_it(self):
        svc = GitRepoService()
        svc.seed({"committed": {"a": "1\n"}})
        svc.dispatch("git_create_branch", {"name": "feature"})
        self.assertEqual(svc.summary()["current_branch"], "main")

    def test_create_branch_duplicate_name_is_an_error(self):
        svc = GitRepoService()
        svc.seed({"committed": {"a": "1\n"}})
        svc.dispatch("git_create_branch", {"name": "feature"})
        result = svc.dispatch("git_create_branch", {"name": "feature"})
        self.assertIn("error", result)

    def test_checkout_unknown_branch_is_an_error(self):
        svc = GitRepoService()
        result = svc.dispatch("git_checkout", {"ref": "does-not-exist"})
        self.assertIn("error", result)

    def test_tag_and_list_tags(self):
        svc = GitRepoService()
        svc.seed({"committed": {"a": "1\n"}})
        svc.dispatch("git_tag", {"name": "v1.0.0"})
        tags = svc.dispatch("git_tags", {})
        self.assertEqual(tags["tags"], [{"name": "v1.0.0", "commit_id": "c1"}])

    def test_tag_with_no_commits_is_an_error(self):
        svc = GitRepoService()
        result = svc.dispatch("git_tag", {"name": "v1.0.0"})
        self.assertIn("error", result)

    def test_remotes_preseeded_with_origin(self):
        svc = GitRepoService()
        remotes = svc.dispatch("git_remotes", {})
        self.assertEqual([r["name"] for r in remotes["remotes"]], ["origin"])

    def test_unknown_git_tool_name_is_error_result(self):
        svc = GitRepoService()
        result = svc.dispatch("git_frobnicate", {})
        self.assertIn("error", result)


class GitRepoSeedTests(unittest.TestCase):
    def test_committed_seeds_one_commit(self):
        svc = GitRepoService()
        svc.seed({"committed": {"a.py": "1\n"}, "committed_message": "start"})
        self.assertEqual(svc.summary()["commits_on_current_branch"], 1)
        self.assertEqual(svc.summary()["last_commit_message"], "start")

    def test_initial_commits_builds_multi_commit_history(self):
        svc = GitRepoService()
        svc.seed({"initial_commits": [
            {"message": "a", "files": {"x": "1\n"}},
            {"message": "b", "files": {"x": "2\n"}},
            {"message": "c", "files": {"x": "3\n", "y": "1\n"}},
        ]})
        self.assertEqual(svc.summary()["commits_on_current_branch"], 3)
        self.assertEqual(svc.summary()["last_commit_files"], ["x", "y"])

    def test_working_dir_overlay_creates_untracked_and_modified(self):
        svc = GitRepoService()
        svc.seed({"committed": {"a": "1\n"}, "working_dir": {"a": "2\n", "b": "new\n"}})
        status = svc.dispatch("git_status", {})
        self.assertEqual(status["unstaged_modified"], ["a"])
        self.assertEqual(status["untracked"], ["b"])

    def test_seed_is_never_logged_to_call_log(self):
        svc = GitRepoService()
        svc.seed({"committed": {"a": "1\n"}, "initial_commits": [{"message": "x", "files": {"a": "2\n"}}],
                  "remote_ahead_commits": [{"message": "y", "files": {"a": "3\n"}}]})
        self.assertEqual(svc.call_log, [])

    def test_remote_ahead_commits_not_reachable_locally_until_pulled(self):
        svc = GitRepoService()
        svc.seed({"committed": {"a": "1\n"}, "remote_ahead_commits": [{"message": "teammate", "files": {"a": "2\n"}}]})
        self.assertEqual(svc.summary()["commits_on_current_branch"], 1)   # not yet pulled
        svc.dispatch("git_pull", {})
        self.assertEqual(svc.summary()["commits_on_current_branch"], 2)

    def test_pull_fast_forwards_clean_working_tree_but_preserves_local_edits(self):
        svc = GitRepoService()
        svc.seed({
            "committed": {"a": "1\n", "b": "1\n"},
            "working_dir": {"a": "1\n", "b": "locally-edited\n"},   # b is dirty, a is clean
            "remote_ahead_commits": [{"message": "teammate", "files": {"a": "2\n", "b": "2\n"}}],
        })
        svc.dispatch("git_pull", {})
        status = svc.dispatch("git_status", {})
        self.assertEqual(status["unstaged_modified"], ["b"])   # a became clean (fast-forwarded), b still dirty


class GitRepoBlameTests(unittest.TestCase):
    def test_unchanged_line_attributed_to_originating_commit_not_newest(self):
        svc = GitRepoService()
        svc.seed({"initial_commits": [
            {"message": "add both lines", "files": {"f.py": "line1\nline2\n"}},
            {"message": "unrelated change elsewhere", "files": {"f.py": "line1\nline2\n", "g.py": "x\n"}},
        ]})
        blame = svc.dispatch("git_blame", {"path": "f.py"})
        commit_ids = [ln["commit_id"] for ln in blame["lines"]]
        self.assertEqual(commit_ids, ["c1", "c1"])   # both lines stay attributed to c1, not c2

    def test_changed_line_attributed_to_the_commit_that_changed_it(self):
        svc = GitRepoService()
        svc.seed({"initial_commits": [
            {"message": "v1", "files": {"f.py": "line1\nold\n"}},
            {"message": "v2", "files": {"f.py": "line1\nnew\n"}},
        ]})
        blame = svc.dispatch("git_blame", {"path": "f.py"})
        self.assertEqual(blame["lines"][0]["commit_id"], "c1")
        self.assertEqual(blame["lines"][1]["commit_id"], "c2")

    def test_trailing_newline_does_not_produce_a_phantom_blamed_line(self):
        svc = GitRepoService()
        svc.seed({"committed": {"f.py": "only line\n"}})
        blame = svc.dispatch("git_blame", {"path": "f.py"})
        self.assertEqual(len(blame["lines"]), 1)

    def test_uncommitted_line_attributed_to_working_directory(self):
        svc = GitRepoService()
        svc.seed({"committed": {"f.py": "line1\n"}, "working_dir": {"f.py": "line1\nline2\n"}})
        blame = svc.dispatch("git_blame", {"path": "f.py"})
        self.assertEqual(blame["lines"][1]["commit_id"], "working directory (uncommitted)")


class ListArgumentMatcherTests(unittest.TestCase):
    """The oracle enhancement for list-valued arguments - see _tool_evaluate.py."""

    def test_expected_calls_list_containment_not_exact_match(self):
        svc = GitRepoService()
        svc.seed({"committed": {"a": "1\n", "b": "1\n"}, "working_dir": {"a": "2\n", "b": "2\n"}})
        svc.dispatch("git_add", {"paths": ["a", "b"]})
        case = {"name": "c", "tool_service": "git_repo",
                "expected_calls": [{"tool": "git_add", "arguments_contains": {"paths": ["a"]}}]}
        failures, _info = evaluate_tool_case(case, svc)
        self.assertEqual(failures, [])   # "a" is IN the call's paths, even though it's not the whole list

    def test_forbidden_calls_list_containment_catches_partial_membership(self):
        svc = GitRepoService()
        svc.seed({"committed": {"a": "1\n", "b": "1\n"}, "working_dir": {"a": "2\n", "b": "2\n"}})
        svc.dispatch("git_add", {"paths": ["a", "b"]})
        case = {"name": "c", "tool_service": "git_repo",
                "forbidden_calls": [{"tool": "git_add", "arguments_contains": {"paths": ["b"]}}]}
        failures, _info = evaluate_tool_case(case, svc)
        self.assertEqual(len(failures), 1)   # "b" being anywhere in the list is caught, not just an exact-list match

    def test_scalar_arguments_still_require_exact_equality(self):
        svc = GitRepoService()
        svc.seed({"committed": {"a": "1\n"}})
        svc.dispatch("git_tag", {"name": "v2.0.0"})
        case = {"name": "c", "tool_service": "git_repo",
                "expected_calls": [{"tool": "git_tag", "arguments_contains": {"name": "v1.0.0"}}]}
        failures, _info = evaluate_tool_case(case, svc)
        self.assertEqual(len(failures), 1)   # v2.0.0 != v1.0.0, no partial/fuzzy match for scalars


class GitCaseSchemaTests(unittest.TestCase):
    def test_tool_service_seed_must_be_an_object(self):
        case = {"name": "c", "tool_service": "git_repo", "tool_service_seed": ["nope"]}
        with self.assertRaises(SchemaError):
            validate_case(case)

    def test_tool_service_seed_object_is_accepted(self):
        case = {"name": "c", "tool_service": "git_repo",
                "tool_service_seed": {"committed": {"a": "1\n"}}}
        validate_case(case)   # must not raise


class ShippedGitCaseDryRunTests(unittest.TestCase):
    """Every tool_git_* case, run against a hand-built ideal trajectory
    (must PASS) and at least one realistic wrong trajectory (must FAIL) -
    the corpus's reference/broken-solution discrimination guarantee, done
    as fast synthetic checks instead of a live model."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["name"]: c for c in load_cases() if c.get("tool_service") == "git_repo"}

    def _run(self, case_name: str, calls: list[tuple[str, dict]]) -> list[str]:
        case = self.cases[case_name]
        svc = get_mock_service(case["tool_service"])()
        svc.seed(case.get("tool_service_seed") or {})
        for tool, args in calls:
            svc.dispatch(tool, args)
        failures, _info = evaluate_tool_case(case, svc)
        return failures

    def test_all_12_git_cases_shipped(self):
        self.assertEqual(len(self.cases), 12)

    def test_status_before_acting_ideal_passes_and_premature_commit_fails(self):
        self.assertEqual(self._run("tool_git_status_before_acting", [
            ("git_status", {}), ("git_diff_unstaged", {}),
        ]), [])
        self.assertTrue(self._run("tool_git_status_before_acting", [("git_commit", {"message": "oops"})]))

    def test_stage_review_and_commit_ideal_passes_and_skip_review_fails(self):
        self.assertEqual(self._run("tool_git_stage_review_and_commit", [
            ("git_add", {"paths": ["README.md"]}), ("git_diff_staged", {}),
            ("git_commit", {"message": "Add description"}),
        ]), [])
        self.assertTrue(self._run("tool_git_stage_review_and_commit", [
            ("git_add", {"paths": ["README.md"]}), ("git_commit", {"message": "Add description"}),
        ]))

    def test_commit_only_one_file_ideal_passes_and_committing_both_fails(self):
        self.assertEqual(self._run("tool_git_commit_only_one_file", [
            ("git_add", {"paths": ["a.py"]}), ("git_commit", {"message": "Update a.py"}),
        ]), [])
        self.assertTrue(self._run("tool_git_commit_only_one_file", [
            ("git_add", {"paths": ["a.py", "b.py"]}), ("git_commit", {"message": "both"}),
        ]))

    def test_branch_before_editing_ideal_passes_and_direct_to_main_fails(self):
        self.assertEqual(self._run("tool_git_branch_before_editing", [
            ("git_branch", {}), ("git_create_branch", {"name": "feature-x"}),
            ("git_checkout", {"ref": "feature-x"}), ("git_add", {"paths": ["app.py"]}),
            ("git_commit", {"message": "New feature"}),
        ]), [])
        self.assertTrue(self._run("tool_git_branch_before_editing", [
            ("git_add", {"paths": ["app.py"]}), ("git_commit", {"message": "feature"}),
        ]))

    def test_no_commit_without_staging_ideal_passes_and_blind_commit_fails(self):
        self.assertEqual(self._run("tool_git_no_commit_without_staging", [("git_status", {})]), [])
        self.assertTrue(self._run("tool_git_no_commit_without_staging", [("git_commit", {"message": "x"})]))

    def test_log_and_show_ideal_passes_and_skipping_show_fails(self):
        self.assertEqual(self._run("tool_git_log_and_show_commit", [
            ("git_log", {}), ("git_show", {"ref": "HEAD"}),
        ]), [])
        self.assertTrue(self._run("tool_git_log_and_show_commit", [("git_log", {})]))

    def test_diff_between_branches_ideal_passes_and_no_branch_fails(self):
        self.assertEqual(self._run("tool_git_diff_between_branches", [
            ("git_create_branch", {"name": "staging"}), ("git_checkout", {"ref": "staging"}),
            ("git_add", {"paths": ["config.py"]}), ("git_commit", {"message": "PORT 9000"}),
            ("git_diff", {"ref_a": "main", "ref_b": "staging"}),
        ]), [])
        self.assertTrue(self._run("tool_git_diff_between_branches", [
            ("git_add", {"paths": ["config.py"]}), ("git_commit", {"message": "PORT 9000"}),
        ]))

    def test_blame_ideal_passes_and_never_calling_it_fails(self):
        self.assertEqual(self._run("tool_git_blame_find_line_origin", [
            ("git_log", {}), ("git_blame", {"path": "greet.py"}),
        ]), [])
        self.assertTrue(self._run("tool_git_blame_find_line_origin", [("git_log", {})]))

    def test_tag_release_ideal_passes_and_forgetting_to_list_fails(self):
        self.assertEqual(self._run("tool_git_tag_release", [
            ("git_tag", {"name": "v1.0.0"}), ("git_tags", {}),
        ]), [])
        self.assertTrue(self._run("tool_git_tag_release", [("git_tag", {"name": "v1.0.0"})]))

    def test_push_after_commit_ideal_passes_and_forgetting_to_push_fails(self):
        self.assertEqual(self._run("tool_git_push_after_commit", [
            ("git_remotes", {}), ("git_add", {"paths": ["app.py"]}),
            ("git_commit", {"message": "Bump version"}), ("git_push", {}),
        ]), [])
        self.assertTrue(self._run("tool_git_push_after_commit", [
            ("git_remotes", {}), ("git_add", {"paths": ["app.py"]}), ("git_commit", {"message": "Bump version"}),
        ]))

    def test_pull_before_push_ideal_passes_and_pushing_blind_fails(self):
        self.assertEqual(self._run("tool_git_pull_before_push", [
            ("git_pull", {}), ("git_add", {"paths": ["new_feature.py"]}),
            ("git_commit", {"message": "Add feature"}), ("git_push", {}),
        ]), [])
        self.assertTrue(self._run("tool_git_pull_before_push", [
            ("git_add", {"paths": ["new_feature.py"]}), ("git_commit", {"message": "Add feature"}), ("git_push", {}),
        ]))

    def test_reset_undo_ideal_passes_and_committing_anyway_fails(self):
        self.assertEqual(self._run("tool_git_reset_undo_wrong_stage", [
            ("git_add", {"paths": ["a.py", "b.py"]}), ("git_reset", {}),
        ]), [])
        self.assertTrue(self._run("tool_git_reset_undo_wrong_stage", [
            ("git_add", {"paths": ["a.py", "b.py"]}), ("git_commit", {"message": "oops"}),
        ]))


class GitCaseDriverEndToEndTests(unittest.TestCase):
    """Proves tool_service_seed actually reaches the mock service THROUGH
    the real driver's run_case() (ToolChatDriver.seed() wiring in
    tool_chat.py), not just via direct service.seed() calls as everything
    above this class exercises."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_git_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_seeded_history_is_visible_to_the_agent_via_the_real_driver(self):
        from optarena.drivers.tool_chat import ToolChatDriver

        case = {
            "name": "c", "tool_service": "git_repo",
            "tools": ["git_add", "git_commit"],
            "tool_service_seed": {"committed": {"a.py": "1\n"}, "working_dir": {"a.py": "2\n"}},
            "prompts": ["stage and commit the change"],
            "expected_calls": [{"tool": "git_commit"}],
            "expected_final_state": {"commits_on_current_branch": 2},
        }
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "git_add", "arguments": {"paths": ["a.py"]}}]},
            {"tool_calls": [{"name": "git_commit", "arguments": {"message": "Update a.py"}}]},
            {"content": "Done."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        # Confirms the commit that landed built on the SEEDED c1, not an
        # empty repo - i.e. seed() genuinely ran before the conversation.
        self.assertEqual(result.extra["oracle"]["final_state"]["commits_on_current_branch"], 2)


if __name__ == "__main__":
    unittest.main()
