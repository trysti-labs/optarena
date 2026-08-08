"""
`optarena cases verify --sandboxed` (`_cases_cmds._verify_sandboxed_schema_drift`):
the CI-friendly, no-model-call pre-flight that checks matched tool-use
cases' tool names against a real sandboxed server's live `tools/list`.
`build_sandboxed_service` is faked throughout - no real Docker/MCP session -
this file is scoped to the batching/reporting logic (one sandbox per
DISTINCT tool_service, not per case; unregistered services skipped, not
failed; a case-level KeyError/RuntimeError/MCPProtocolError is caught, not
a crash).
"""

from __future__ import annotations

import unittest
from unittest import mock

from optarena.cli._cases_cmds import _verify_sandboxed_schema_drift


class _FakeService:
    def __init__(self, tool_names):
        self.tool_schemas = {n: {} for n in tool_names}
        self.closed = False

    def close(self):
        self.closed = True


class ProbeSetupTests(unittest.TestCase):
    """Some real servers refuse to start against an EMPTY directory, which
    is exactly what the drift check hands them - found live, not theorized:
    `mcp-server-git` exits with "`.` is not a valid Git repository" and the
    check reported only an opaque handshake timeout until B-1's stderr tail
    made the cause visible."""

    def test_probe_setup_registered_for_the_services_that_need_it(self):
        from optarena._cases._sandboxed_mcp_service import PROBE_SETUP, SANDBOXED_SERVICES
        self.assertEqual(set(PROBE_SETUP), {"git_repo", "build_tools"})
        for name in PROBE_SETUP:
            self.assertIn(name, SANDBOXED_SERVICES)

    def test_git_repo_probe_makes_a_real_repository(self):
        import shutil as _shutil
        import subprocess
        import tempfile
        from pathlib import Path

        from optarena._cases._sandboxed_mcp_service import PROBE_SETUP

        if not _shutil.which("git"):
            self.skipTest("git not on PATH")
        tmp = Path(tempfile.mkdtemp(prefix="optarena_test_probe_git_"))
        self.addCleanup(_shutil.rmtree, tmp, ignore_errors=True)
        PROBE_SETUP["git_repo"](tmp)
        self.assertTrue((tmp / ".git").is_dir())
        # Real proof it's usable, not just that a directory appeared.
        proc = subprocess.run(["git", "status", "--porcelain"], cwd=tmp,
                              capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 0)

    def test_build_tools_probe_writes_a_recognizable_workspace(self):
        import json
        import shutil as _shutil
        import tempfile
        from pathlib import Path

        from optarena._cases._sandboxed_mcp_service import PROBE_SETUP

        tmp = Path(tempfile.mkdtemp(prefix="optarena_test_probe_nx_"))
        self.addCleanup(_shutil.rmtree, tmp, ignore_errors=True)
        PROBE_SETUP["build_tools"](tmp)
        self.assertIn("targetDefaults", json.loads((tmp / "nx.json").read_text(encoding="utf-8")))
        self.assertTrue((tmp / "package.json").is_file())

    def test_probe_runs_before_the_sandbox_starts(self):
        calls = []
        fake_service = _FakeService(["git_status"])
        cases = [{"name": "c1", "tool_service": "git_repo", "expected_calls": [{"tool": "git_status"}]}]

        def fake_probe(root):
            calls.append("probe")

        def fake_build(name, root):
            calls.append("build")
            return fake_service

        with mock.patch.dict("optarena._cases._sandboxed_mcp_service.PROBE_SETUP",
                             {"git_repo": fake_probe}, clear=False), \
             mock.patch("optarena._cases._sandboxed_mcp_service.build_sandboxed_service",
                        side_effect=fake_build):
            _verify_sandboxed_schema_drift(cases)
        self.assertEqual(calls, ["probe", "build"])


class VerifySandboxedSchemaDriftTests(unittest.TestCase):
    def test_no_tool_use_cases_matched_returns_false(self):
        found = _verify_sandboxed_schema_drift([{"name": "c1", "prompts": ["x"]}])
        self.assertFalse(found)

    def test_service_with_no_sandboxed_implementation_is_skipped_not_failed(self):
        cases = [{"name": "c1", "tool_service": "not_a_real_service_yet", "expected_calls": []}]
        found = _verify_sandboxed_schema_drift(cases)
        self.assertFalse(found)

    def test_no_drift_when_every_referenced_tool_exists(self):
        fake_service = _FakeService(["write_file", "read_text_file"])
        cases = [{"name": "c1", "tool_service": "filesystem",
                  "expected_calls": [{"tool": "write_file"}]}]
        with mock.patch("optarena._cases._sandboxed_mcp_service.build_sandboxed_service",
                        return_value=fake_service) as build:
            found = _verify_sandboxed_schema_drift(cases)
        self.assertFalse(found)
        build.assert_called_once()
        self.assertTrue(fake_service.closed)

    def test_drift_detected_and_reported(self):
        fake_service = _FakeService(["write_file"])
        cases = [{"name": "c1", "tool_service": "filesystem",
                  "expected_calls": [{"tool": "delete_everything"}]}]
        with mock.patch("optarena._cases._sandboxed_mcp_service.build_sandboxed_service",
                        return_value=fake_service):
            found = _verify_sandboxed_schema_drift(cases)
        self.assertTrue(found)

    def test_one_sandbox_started_per_distinct_service_not_per_case(self):
        fake_service = _FakeService(["write_file"])
        cases = [
            {"name": "c1", "tool_service": "filesystem", "expected_calls": [{"tool": "write_file"}]},
            {"name": "c2", "tool_service": "filesystem", "expected_calls": [{"tool": "write_file"}]},
            {"name": "c3", "tool_service": "filesystem", "expected_calls": [{"tool": "write_file"}]},
        ]
        with mock.patch("optarena._cases._sandboxed_mcp_service.build_sandboxed_service",
                        return_value=fake_service) as build:
            _verify_sandboxed_schema_drift(cases)
        self.assertEqual(build.call_count, 1)

    def test_sandbox_start_failure_counts_as_drift_not_a_crash(self):
        cases = [{"name": "c1", "tool_service": "filesystem", "expected_calls": []}]
        with mock.patch("optarena._cases._sandboxed_mcp_service.build_sandboxed_service",
                        side_effect=RuntimeError("no container engine")):
            found = _verify_sandboxed_schema_drift(cases)
        self.assertTrue(found)

    def test_debug_true_reraises_the_sandbox_start_failure(self):
        cases = [{"name": "c1", "tool_service": "filesystem", "expected_calls": []}]
        with mock.patch("optarena._cases._sandboxed_mcp_service.build_sandboxed_service",
                        side_effect=RuntimeError("no container engine")):
            with self.assertRaises(RuntimeError):
                _verify_sandboxed_schema_drift(cases, debug=True)

    def test_mixed_registered_and_unregistered_services_both_reported(self):
        fake_service = _FakeService(["write_file"])
        cases = [
            {"name": "c1", "tool_service": "filesystem", "expected_calls": [{"tool": "write_file"}]},
            {"name": "c2", "tool_service": "not_registered_yet", "expected_calls": []},
        ]
        with mock.patch("optarena._cases._sandboxed_mcp_service.build_sandboxed_service",
                        return_value=fake_service):
            found = _verify_sandboxed_schema_drift(cases)
        self.assertFalse(found)


if __name__ == "__main__":
    unittest.main()
