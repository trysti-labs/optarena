"""
filesystem mock-service tests: the 13-tool FilesystemService itself, its
seed() mechanism, the oracle's nested-dict final-state matcher, and all 13
shipped tool_fs_* cases - each dry-run against a hand-built "ideal"
trajectory (must PASS) and at least one realistic wrong trajectory (must
FAIL), same discriminating-oracle philosophy as test_git_repo_cases.py.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from optarena._cases._mock_service import FilesystemService, get_mock_service, get_tool_schemas
from optarena._cases._tool_evaluate import evaluate_tool_case
from optarena.cases import load_cases
from optarena.schema import SchemaError, validate_case
from optarena.scenario import Backend, Scenario
from tests.test_tool_use_cases import _ToolStubBackend


class FilesystemServiceBasicsTests(unittest.TestCase):
    def test_all_13_tools_registered(self):
        schemas = get_tool_schemas("filesystem")
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(len(names), 13)
        self.assertEqual(names, set(FilesystemService.TOOLS))

    def test_read_text_file_returns_content(self):
        svc = FilesystemService()
        svc.seed({"files": {"a.py": "print(1)\n"}})
        result = svc.dispatch("read_text_file", {"path": "a.py"})
        self.assertEqual(result["content"], "print(1)\n")

    def test_read_text_file_missing_is_error_not_crash(self):
        svc = FilesystemService()
        result = svc.dispatch("read_text_file", {"path": "nope.py"})
        self.assertIn("error", result)

    def test_read_text_file_head_and_tail(self):
        svc = FilesystemService()
        svc.seed({"files": {"a.txt": "1\n2\n3\n4\n5"}})
        self.assertEqual(svc.dispatch("read_text_file", {"path": "a.txt", "head": 2})["content"], "1\n2")
        self.assertEqual(svc.dispatch("read_text_file", {"path": "a.txt", "tail": 2})["content"], "4\n5")

    def test_read_multiple_files_accepts_bare_string(self):
        svc = FilesystemService()
        svc.seed({"files": {"a.py": "1\n"}})
        result = svc.dispatch("read_multiple_files", {"paths": "a.py"})
        self.assertEqual(result["files"]["a.py"], "1\n")

    def test_read_multiple_files_partial_errors(self):
        svc = FilesystemService()
        svc.seed({"files": {"a.py": "1\n"}})
        result = svc.dispatch("read_multiple_files", {"paths": ["a.py", "missing.py"]})
        self.assertEqual(result["files"]["a.py"], "1\n")
        self.assertIn("error", result["files"]["missing.py"])

    def test_write_file_creates_and_overwrites(self):
        svc = FilesystemService()
        svc.dispatch("write_file", {"path": "a.py", "content": "1\n"})
        self.assertEqual(svc.dispatch("read_text_file", {"path": "a.py"})["content"], "1\n")
        svc.dispatch("write_file", {"path": "a.py", "content": "2\n"})
        self.assertEqual(svc.dispatch("read_text_file", {"path": "a.py"})["content"], "2\n")

    def test_write_file_to_nested_path_creates_parent_directories(self):
        svc = FilesystemService()
        svc.dispatch("write_file", {"path": "src/deep/a.py", "content": "1\n"})
        self.assertEqual(svc.dispatch("list_directory", {"path": "src"})["entries"],
                         [{"name": "deep", "type": "directory"}])
        self.assertEqual(svc.dispatch("get_file_info", {"path": "src/deep"})["type"], "directory")

    def test_edit_file_applies_targeted_replacement(self):
        svc = FilesystemService()
        svc.seed({"files": {"a.py": "PORT = 8000\n"}})
        result = svc.dispatch("edit_file", {"path": "a.py", "edits": [{"old_text": "8000", "new_text": "9000"}]})
        self.assertEqual(result["edits_applied"], 1)
        self.assertEqual(svc.dispatch("read_text_file", {"path": "a.py"})["content"], "PORT = 9000\n")

    def test_edit_file_old_text_not_found_is_error_and_does_not_partially_apply(self):
        svc = FilesystemService()
        svc.seed({"files": {"a.py": "PORT = 8000\n"}})
        result = svc.dispatch("edit_file", {"path": "a.py", "edits": [{"old_text": "NOPE", "new_text": "x"}]})
        self.assertIn("error", result)
        self.assertEqual(svc.dispatch("read_text_file", {"path": "a.py"})["content"], "PORT = 8000\n")

    def test_edit_file_dry_run_does_not_persist(self):
        svc = FilesystemService()
        svc.seed({"files": {"a.py": "PORT = 8000\n"}})
        result = svc.dispatch("edit_file", {"path": "a.py", "edits": [{"old_text": "8000", "new_text": "9000"}], "dry_run": True})
        self.assertTrue(result["dry_run"])
        self.assertEqual(svc.dispatch("read_text_file", {"path": "a.py"})["content"], "PORT = 8000\n")

    def test_edit_file_missing_file_is_error(self):
        svc = FilesystemService()
        result = svc.dispatch("edit_file", {"path": "nope.py", "edits": [{"old_text": "a", "new_text": "b"}]})
        self.assertIn("error", result)

    def test_list_directory_separates_files_and_directories(self):
        svc = FilesystemService()
        svc.seed({"files": {"a.py": "1\n", "src/b.py": "2\n"}})
        result = svc.dispatch("list_directory", {})
        entries = {e["name"]: e["type"] for e in result["entries"]}
        self.assertEqual(entries, {"a.py": "file", "src": "directory"})

    def test_list_directory_missing_path_is_error(self):
        svc = FilesystemService()
        result = svc.dispatch("list_directory", {"path": "nope"})
        self.assertIn("error", result)

    def test_list_directory_with_sizes_sorts_by_size_descending(self):
        svc = FilesystemService()
        svc.seed({"files": {"small.txt": "x", "big.txt": "x" * 100}})
        result = svc.dispatch("list_directory_with_sizes", {"sort_by": "size"})
        self.assertEqual([e["name"] for e in result["entries"]], ["big.txt", "small.txt"])

    def test_move_file_relocates_content(self):
        svc = FilesystemService()
        svc.seed({"files": {"old.py": "1\n"}})
        svc.dispatch("move_file", {"source": "old.py", "destination": "new.py"})
        self.assertNotIn("error", svc.dispatch("read_text_file", {"path": "new.py"}))
        self.assertIn("error", svc.dispatch("read_text_file", {"path": "old.py"}))

    def test_move_file_missing_source_is_error(self):
        svc = FilesystemService()
        result = svc.dispatch("move_file", {"source": "nope.py", "destination": "new.py"})
        self.assertIn("error", result)

    def test_move_file_existing_destination_is_error(self):
        svc = FilesystemService()
        svc.seed({"files": {"a.py": "1\n", "b.py": "2\n"}})
        result = svc.dispatch("move_file", {"source": "a.py", "destination": "b.py"})
        self.assertIn("error", result)

    def test_search_files_matches_by_name_not_content(self):
        svc = FilesystemService()
        svc.seed({"files": {"src/utils.py": "# nothing special\n", "src/main.py": "1\n"}})
        result = svc.dispatch("search_files", {"pattern": "utils"})
        self.assertEqual(result["matches"], ["src/utils.py"])

    def test_directory_tree_nests_correctly(self):
        svc = FilesystemService()
        svc.seed({"files": {"src/a.py": "1\n"}})
        tree = svc.dispatch("directory_tree", {})
        src_node = next(c for c in tree["children"] if c["name"] == "src")
        self.assertEqual(src_node["type"], "directory")
        self.assertEqual([c["name"] for c in src_node["children"]], ["a.py"])

    def test_get_file_info_file_vs_directory_vs_missing(self):
        svc = FilesystemService()
        svc.seed({"files": {"src/a.py": "12345\n"}})
        self.assertEqual(svc.dispatch("get_file_info", {"path": "src/a.py"})["type"], "file")
        self.assertEqual(svc.dispatch("get_file_info", {"path": "src/a.py"})["size"], 6)
        self.assertEqual(svc.dispatch("get_file_info", {"path": "src"})["type"], "directory")
        self.assertIn("error", svc.dispatch("get_file_info", {"path": "nope"}))

    def test_list_allowed_directories_returns_a_fixed_list(self):
        svc = FilesystemService()
        result = svc.dispatch("list_allowed_directories", {})
        self.assertEqual(result["directories"], ["/workspace"])

    def test_read_media_file_returns_mime_type(self):
        svc = FilesystemService()
        svc.seed({"media_files": {"logo.png": "image/png"}})
        result = svc.dispatch("read_media_file", {"path": "logo.png"})
        self.assertEqual(result["mime_type"], "image/png")

    def test_unknown_filesystem_tool_name_is_error_result(self):
        svc = FilesystemService()
        result = svc.dispatch("delete_everything", {})
        self.assertIn("error", result)


class FilesystemSeedTests(unittest.TestCase):
    def test_files_seed_creates_readable_files(self):
        svc = FilesystemService()
        svc.seed({"files": {"a.py": "1\n"}})
        self.assertEqual(svc.summary()["file_count"], 1)

    def test_directories_seed_creates_empty_directory(self):
        svc = FilesystemService()
        svc.seed({"directories": ["empty"]})
        result = svc.dispatch("list_directory", {})
        self.assertEqual(result["entries"], [{"name": "empty", "type": "directory"}])

    def test_seed_is_never_logged_to_call_log(self):
        svc = FilesystemService()
        svc.seed({"files": {"a.py": "1\n"}, "media_files": {"b.png": "image/png"}, "directories": ["d"]})
        self.assertEqual(svc.call_log, [])


class NestedStateMatcherTests(unittest.TestCase):
    """The expected_final_state generalization to nested-dict subset
    matching - needed for filesystem's "files" content checks, verified
    directly against the shared _value_matches used by both call-argument
    and final-state matching."""

    def test_final_state_dict_subset_ignores_unrelated_keys(self):
        svc = FilesystemService()
        svc.seed({"files": {"a.py": "1\n", "b.py": "2\n"}})
        case = {"name": "c", "tool_service": "filesystem",
                "expected_final_state": {"files": {"a.py": "1\n"}}}
        failures, _info = evaluate_tool_case(case, svc)
        self.assertEqual(failures, [])   # b.py being present too must not fail this

    def test_final_state_dict_subset_catches_wrong_content(self):
        svc = FilesystemService()
        svc.seed({"files": {"a.py": "1\n"}})
        case = {"name": "c", "tool_service": "filesystem",
                "expected_final_state": {"files": {"a.py": "WRONG\n"}}}
        failures, _info = evaluate_tool_case(case, svc)
        self.assertEqual(len(failures), 1)

    def test_final_state_scalar_still_exact(self):
        svc = FilesystemService()
        svc.seed({"files": {"a.py": "1\n"}})
        case = {"name": "c", "tool_service": "filesystem", "expected_final_state": {"file_count": 2}}
        failures, _info = evaluate_tool_case(case, svc)
        self.assertEqual(len(failures), 1)


class FilesystemCaseSchemaTests(unittest.TestCase):
    def test_minimal_valid_filesystem_case_passes(self):
        case = {"name": "c", "tool_service": "filesystem",
                "expected_calls": [{"tool": "read_text_file"}]}
        validate_case(case)


class ShippedFilesystemCaseDryRunTests(unittest.TestCase):
    """Every tool_fs_* case, run against a hand-built ideal trajectory
    (must PASS) and at least one realistic wrong trajectory (must FAIL)."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["name"]: c for c in load_cases() if c.get("tool_service") == "filesystem"}

    def _run(self, case_name: str, calls: list[tuple[str, dict]]) -> list[str]:
        case = self.cases[case_name]
        svc = get_mock_service(case["tool_service"])()
        svc.seed(case.get("tool_service_seed") or {})
        for tool, args in calls:
            svc.dispatch(tool, args)
        failures, _info = evaluate_tool_case(case, svc)
        return failures

    def test_all_13_filesystem_cases_shipped(self):
        self.assertEqual(len(self.cases), 13)

    def test_read_before_answering_ideal_passes_and_no_read_fails(self):
        self.assertEqual(self._run("tool_fs_read_before_answering", [
            ("read_text_file", {"path": "config.py"}),
        ]), [])
        self.assertTrue(self._run("tool_fs_read_before_answering", []))

    def test_write_new_file_ideal_passes_and_no_write_fails(self):
        self.assertEqual(self._run("tool_fs_write_new_file", [
            ("write_file", {"path": ".ready", "content": "ready"}),
        ]), [])
        self.assertTrue(self._run("tool_fs_write_new_file", []))

    def test_edit_not_overwrite_ideal_passes_and_blind_rewrite_fails(self):
        self.assertEqual(self._run("tool_fs_edit_not_overwrite", [
            ("read_text_file", {"path": "config.py"}),
            ("edit_file", {"path": "config.py", "edits": [{"old_text": "PORT = 8000", "new_text": "PORT = 9000"}]}),
        ]), [])
        self.assertTrue(self._run("tool_fs_edit_not_overwrite", [
            ("write_file", {"path": "config.py", "content": "PORT = 9000\n"}),
        ]))

    def test_list_before_reading_ideal_passes_and_guessing_fails(self):
        self.assertEqual(self._run("tool_fs_list_before_reading", [
            ("list_directory", {"path": "src"}), ("read_text_file", {"path": "src/main.py"}),
        ]), [])
        self.assertTrue(self._run("tool_fs_list_before_reading", [
            ("read_text_file", {"path": "src/main.py"}),
        ]))

    def test_search_then_read_ideal_passes_and_no_search_fails(self):
        self.assertEqual(self._run("tool_fs_search_then_read", [
            ("search_files", {"pattern": "utils"}), ("read_text_file", {"path": "src/utils.py"}),
        ]), [])
        self.assertTrue(self._run("tool_fs_search_then_read", [
            ("read_text_file", {"path": "src/utils.py"}),
        ]))

    def test_read_multiple_together_ideal_passes_and_two_singles_fails(self):
        self.assertEqual(self._run("tool_fs_read_multiple_together", [
            ("read_multiple_files", {"paths": ["config.py", "settings.py"]}),
        ]), [])
        self.assertTrue(self._run("tool_fs_read_multiple_together", [
            ("read_text_file", {"path": "config.py"}), ("read_text_file", {"path": "settings.py"}),
        ]))

    def test_create_directory_then_write_ideal_passes_and_no_dir_fails(self):
        self.assertEqual(self._run("tool_fs_create_directory_then_write", [
            ("create_directory", {"path": "output"}),
            ("write_file", {"path": "output/result.txt", "content": "done"}),
        ]), [])
        self.assertTrue(self._run("tool_fs_create_directory_then_write", [
            ("write_file", {"path": "output/result.txt", "content": "done"}),
        ]))

    def test_move_file_ideal_passes_and_write_new_from_scratch_fails(self):
        self.assertEqual(self._run("tool_fs_move_file", [
            ("move_file", {"source": "old_name.py", "destination": "new_name.py"}),
        ]), [])
        self.assertTrue(self._run("tool_fs_move_file", [
            ("write_file", {"path": "new_name.py", "content": "print('hello')"}),
        ]))

    def test_check_before_overwrite_ideal_passes_and_blind_clobber_fails(self):
        self.assertEqual(self._run("tool_fs_check_before_overwrite", [
            ("get_file_info", {"path": "output.txt"}),
        ]), [])
        self.assertTrue(self._run("tool_fs_check_before_overwrite", [
            ("write_file", {"path": "output.txt", "content": "overwritten"}),
        ]))

    def test_directory_tree_overview_ideal_passes_and_nothing_fails(self):
        self.assertEqual(self._run("tool_fs_directory_tree_overview", [("directory_tree", {})]), [])
        self.assertTrue(self._run("tool_fs_directory_tree_overview", []))

    def test_list_allowed_directories_ideal_passes_and_nothing_fails(self):
        self.assertEqual(self._run("tool_fs_list_allowed_directories", [("list_allowed_directories", {})]), [])
        self.assertTrue(self._run("tool_fs_list_allowed_directories", []))

    def test_read_media_file_ideal_passes_and_wrong_tool_fails(self):
        self.assertEqual(self._run("tool_fs_read_media_file", [
            ("read_media_file", {"path": "logo.png"}),
        ]), [])
        self.assertTrue(self._run("tool_fs_read_media_file", [
            ("read_text_file", {"path": "logo.png"}),
        ]))

    def test_list_with_sizes_ideal_passes_and_plain_list_fails(self):
        self.assertEqual(self._run("tool_fs_list_with_sizes", [
            ("list_directory_with_sizes", {"path": "data"}),
        ]), [])
        self.assertTrue(self._run("tool_fs_list_with_sizes", [
            ("list_directory", {"path": "data"}),
        ]))


class FilesystemCaseDriverEndToEndTests(unittest.TestCase):
    """Proves tool_service_seed reaches the mock service through the real
    driver's run_case(), same proof test_git_repo_cases.py has for git_repo."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_fs_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_seeded_file_is_readable_via_the_real_driver(self):
        from optarena.drivers.tool_chat import ToolChatDriver

        case = {
            "name": "c", "tool_service": "filesystem",
            "tools": ["read_text_file"],
            "tool_service_seed": {"files": {"config.py": "PORT = 8000\n"}},
            "prompts": ["read config.py"],
            "expected_calls": [{"tool": "read_text_file", "arguments_contains": {"path": "config.py"}}],
        }
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "read_text_file", "arguments": {"path": "config.py"}}]},
            {"content": "PORT is 8000."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual(result.extra["tool_calls"][0]["result"]["content"], "PORT = 8000\n")


if __name__ == "__main__":
    unittest.main()
