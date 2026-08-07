"""
code_intel mock-service tests: the 6-tool CodeIntelService itself (a mock
language server, mirroring the real isaacphi/mcp-language-server's actual
tools.go registrations - by far the dominant real implementation surveyed
this session at 1,572 stars, next best found was 192), its refuse-path
preconditions (definition/references refuse an unknown symbol,
diagnostics/edit_file refuse an unknown file, hover/rename_symbol refuse
a position with no known symbol, edit_file refuses an out-of-range line
edit), its seed() mechanism, and all 9 shipped tool_lsp_* cases - each
dry-run against a hand-built "ideal" trajectory (must PASS) and at least
one realistic wrong trajectory (must FAIL), same discriminating-oracle
philosophy as the other services.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from optarena._cases._mock_service import CodeIntelService, get_mock_service, get_tool_schemas
from optarena._cases._tool_evaluate import evaluate_tool_case
from optarena.cases import load_cases
from optarena.schema import SchemaError, validate_case
from optarena.scenario import Backend, Scenario
from tests.test_tool_use_cases import _ToolStubBackend


class CodeIntelServiceBasicsTests(unittest.TestCase):
    def test_all_6_tools_registered(self):
        schemas = get_tool_schemas("code_intel")
        names = {s["function"]["name"] for s in schemas}
        self.assertEqual(len(names), 6)
        self.assertEqual(names, set(CodeIntelService.TOOLS))

    def test_definition_refuses_unknown_symbol(self):
        svc = CodeIntelService()
        result = svc.dispatch("definition", {"symbolName": "nope"})
        self.assertIn("error", result)

    def test_references_refuses_unknown_symbol(self):
        svc = CodeIntelService()
        result = svc.dispatch("references", {"symbolName": "nope"})
        self.assertIn("error", result)

    def test_diagnostics_refuses_unknown_file(self):
        svc = CodeIntelService()
        result = svc.dispatch("diagnostics", {"filePath": "nope.py"})
        self.assertIn("error", result)

    def test_hover_refuses_position_with_no_symbol(self):
        svc = CodeIntelService()
        svc.seed({"files": {"a.py": "x = 1\n"}})
        result = svc.dispatch("hover", {"filePath": "a.py", "line": 1, "column": 1})
        self.assertIn("error", result)

    def test_rename_symbol_refuses_position_with_no_symbol(self):
        svc = CodeIntelService()
        svc.seed({"files": {"a.py": "x = 1\n"}})
        result = svc.dispatch("rename_symbol", {"filePath": "a.py", "line": 1, "column": 1, "newName": "y"})
        self.assertIn("error", result)

    def test_edit_file_refuses_unknown_file(self):
        svc = CodeIntelService()
        result = svc.dispatch("edit_file", {"filePath": "nope.py", "edits": [{"startLine": 1, "endLine": 1, "newText": "x"}]})
        self.assertIn("error", result)

    def test_edit_file_refuses_out_of_range_edit(self):
        svc = CodeIntelService()
        svc.seed({"files": {"a.py": "x = 1\n"}})
        result = svc.dispatch("edit_file", {"filePath": "a.py", "edits": [{"startLine": 5, "endLine": 5, "newText": "y = 2"}]})
        self.assertIn("error", result)

    def test_edit_file_applies_a_valid_edit(self):
        svc = CodeIntelService()
        svc.seed({"files": {"a.py": "x = 1\ny = 2\n"}})
        result = svc.dispatch("edit_file", {"filePath": "a.py", "edits": [{"startLine": 1, "endLine": 1, "newText": "x = 99"}]})
        self.assertEqual(result["editsApplied"], 1)


class CodeIntelSeedTests(unittest.TestCase):
    def test_seed_is_never_logged_to_call_log(self):
        svc = CodeIntelService()
        svc.seed({"files": {"a.py": "x = 1\n"}})
        self.assertEqual(svc.call_log, [])

    def test_seeded_diagnostics_reference_auto_registers_file(self):
        svc = CodeIntelService()
        svc.seed({"diagnostics": {"a.py": [{"line": 1, "severity": "error", "message": "bad"}]}})
        result = svc.dispatch("diagnostics", {"filePath": "a.py"})
        self.assertNotIn("error", result)

    def test_seeded_hover_with_symbol_also_registers_rename_target(self):
        svc = CodeIntelService()
        svc.seed({"files": {"a.py": "def f():\n    pass\n"},
                  "hover": [{"filePath": "a.py", "line": 1, "text": "def f()", "symbol": "f"}]})
        result = svc.dispatch("rename_symbol", {"filePath": "a.py", "line": 1, "column": 1, "newName": "g"})
        self.assertEqual(result["symbol"], "f")


class CodeIntelCaseSchemaTests(unittest.TestCase):
    def test_minimal_valid_code_intel_case_passes(self):
        case = {"name": "c", "tool_service": "code_intel",
                "expected_calls": [{"tool": "definition"}]}
        validate_case(case)


class ShippedCodeIntelCaseDryRunTests(unittest.TestCase):
    """Every tool_lsp_* case, run against a hand-built ideal trajectory
    (must PASS) and at least one realistic wrong trajectory (must FAIL)."""

    @classmethod
    def setUpClass(cls):
        cls.cases = {c["name"]: c for c in load_cases() if c.get("tool_service") == "code_intel"}

    def _run(self, case_name: str, calls: list[tuple[str, dict]]) -> list[str]:
        case = self.cases[case_name]
        svc = get_mock_service(case["tool_service"])()
        svc.seed(case.get("tool_service_seed") or {})
        for tool, args in calls:
            svc.dispatch(tool, args)
        failures, _info = evaluate_tool_case(case, svc)
        return failures

    def test_all_9_code_intel_cases_shipped(self):
        self.assertEqual(len(self.cases), 9)

    def test_lookup_definition_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_lsp_lookup_definition", [('definition', {'symbolName': 'login'})]), [])
        self.assertTrue(self._run("tool_lsp_lookup_definition", []))

    def test_find_references_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_lsp_find_references", [('references', {'symbolName': 'login'})]), [])
        self.assertTrue(self._run("tool_lsp_find_references", []))

    def test_check_diagnostics_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_lsp_check_diagnostics", [('diagnostics', {'filePath': 'src/auth.py'})]), [])
        self.assertTrue(self._run("tool_lsp_check_diagnostics", []))

    def test_hover_info_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_lsp_hover_info", [('hover', {'filePath': 'src/auth.py', 'line': 1, 'column': 5})]), [])
        self.assertTrue(self._run("tool_lsp_hover_info", []))

    def test_rename_symbol_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_lsp_rename_symbol",
            [('rename_symbol', {'filePath': 'src/auth.py', 'line': 1, 'column': 5, 'newName': 'authenticate_user'})]), [])
        self.assertTrue(self._run("tool_lsp_rename_symbol",
            [('rename_symbol', {'filePath': 'src/auth.py', 'line': 1, 'column': 5, 'newName': 'wrong_name'})]))

    def test_apply_edit_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_lsp_apply_edit",
            [('edit_file', {'filePath': 'src/auth.py', 'edits': [{'startLine': 2, 'endLine': 2, 'newText': '    return check_user(user)'}]})]), [])
        self.assertTrue(self._run("tool_lsp_apply_edit", []))

    def test_investigate_before_rename_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_lsp_investigate_before_rename",
            [('hover', {'filePath': 'src/auth.py', 'line': 1, 'column': 5}),
             ('rename_symbol', {'filePath': 'src/auth.py', 'line': 1, 'column': 5, 'newName': 'authenticate_user'})]), [])
        self.assertTrue(self._run("tool_lsp_investigate_before_rename",
            [('rename_symbol', {'filePath': 'src/auth.py', 'line': 1, 'column': 5, 'newName': 'authenticate_user'})]))

    def test_debug_and_fix_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_lsp_debug_and_fix",
            [('diagnostics', {'filePath': 'src/auth.py'}),
             ('edit_file', {'filePath': 'src/auth.py', 'edits': [{'startLine': 1, 'endLine': 1, 'newText': ''}]})]), [])
        self.assertTrue(self._run("tool_lsp_debug_and_fix", [('diagnostics', {'filePath': 'src/auth.py'})]))

    def test_trace_usage_before_refactor_ideal_passes_and_wrong_fails(self):
        self.assertEqual(self._run("tool_lsp_trace_usage_before_refactor",
            [('references', {'symbolName': 'login'}),
             ('rename_symbol', {'filePath': 'src/auth.py', 'line': 1, 'column': 5, 'newName': 'authenticate_user'})]), [])
        self.assertTrue(self._run("tool_lsp_trace_usage_before_refactor", [('references', {'symbolName': 'login'})]))


class CodeIntelCaseDriverEndToEndTests(unittest.TestCase):
    """Proves tool_service_seed reaches the mock service through the real
    driver's run_case(), same proof pattern the other services have."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_code_intel_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_seeded_definition_reachable_via_the_real_driver(self):
        from optarena.drivers.tool_chat import ToolChatDriver

        case = {
            "name": "c", "tool_service": "code_intel",
            "tools": ["definition"],
            "tool_service_seed": {"definitions": {"login": {"filePath": "src/auth.py", "code": "def login(): ..."}}},
            "prompts": ["show me the definition of login"],
            "expected_calls": [{"tool": "definition"}],
        }
        backend = _ToolStubBackend([
            {"tool_calls": [{"name": "definition", "arguments": {"symbolName": "login"}}]},
            {"content": "Here's the definition."},
        ])
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-tools",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="m", api_key="sk-x"))
        result = ToolChatDriver().run_case(case, scenario, self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual(result.extra["tool_calls"][0]["result"]["filePath"], "src/auth.py")


if __name__ == "__main__":
    unittest.main()
