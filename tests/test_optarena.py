"""
OptArena unit tests (stdlib only - run with: python -m unittest discover tests).

Covers the July 2026 expansion: extended oracle assertions, check_command,
trial merging, the driver registry metadata, scenario cases_dir, and the
atomic results store.
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from optarena.cases import (
    DOCKER_IMAGE_DEFAULT, DOCKER_IMAGES, DockerSandbox, REPOS_DIR, _HARDENING_ARGS, baseline_incompatible,
    check_expected, classify_failure, diff_stats, dockerfile_for, evaluate_case, filter_cases, load_cases,
    normalize_workspace_line_endings, path_pattern_matches, prepare_workspace, run_capture, run_check_command,
    write_setup_files,
)
from optarena.compare import (
    _cheaper, compare_runs, format_regression, format_table,
    manifest_compatibility, regression_summary,
)
from optarena.drivers import DRIVERS, get_driver
from optarena.drivers.base import CaseResult, subprocess_env
from optarena.drivers.aider_cli import AiderDriver, parse_aider_metrics
from optarena.drivers.cli_agents import (
    CLIAgentDriver, parse_claude_json_metrics, parse_gemini_json_metrics,
)
from optarena.drivers.openai_chat import concrete_target
from optarena.drivers.sdk_base import SingleFileSDKDriver
from optarena.metrics import aggregate, case_deltas
from optarena.pricing import estimate_cost, is_local_backend, price_for
from optarena.runner import _merge_trials, run_scenario, safe_run_name
from optarena.scenario import Backend, Scenario
from optarena.schema import SchemaError, validate_case, validate_scenario, validate_unique_case_names
from optarena.verify import variants_for, verify_cases

_MODULE_RESULTS_DIR = None
_ORIG_RESULTS_DIRS = None


def setUpModule():
    """
    A-32: point the whole suite at a throwaway results directory.

    Several tests call `run_scenario`, which checkpoints through
    `store.save_checkpoint` - so running the suite used to deposit ~13 run
    records per invocation into the DEVELOPER'S OWN `results/runs/`, silently
    inflating `optarena runs list` and the dashboard with junk named after
    test scenarios. Tests that need their own results directory still override
    it themselves (they save and restore the module globals); this just makes
    the default safe.
    """
    global _MODULE_RESULTS_DIR, _ORIG_RESULTS_DIRS
    import optarena.store as store_mod
    _ORIG_RESULTS_DIRS = (store_mod.RESULTS_DIR, store_mod.RUNS_DIR)
    _MODULE_RESULTS_DIR = Path(tempfile.mkdtemp(prefix="optarena_test_results_root_"))
    store_mod.set_results_dir(_MODULE_RESULTS_DIR)


def tearDownModule():
    import shutil
    import optarena.store as store_mod
    if _ORIG_RESULTS_DIRS:
        store_mod.RESULTS_DIR, store_mod.RUNS_DIR = _ORIG_RESULTS_DIRS
    if _MODULE_RESULTS_DIR:
        shutil.rmtree(_MODULE_RESULTS_DIR, ignore_errors=True)


class OracleTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        (self.ws / "out.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8")
        # test_check_command_pass_and_fail below uses sys.executable (a host
        # path) as the check_command binary - meaningless if it gets routed
        # into a Linux container. Force the host path explicitly, the same
        # opt-out every other host-exec test in this file already uses,
        # instead of silently depending on whether Docker happens to be
        # available on the machine running the suite.
        self._env = mock.patch.dict(os.environ, {"OPTARENA_DISABLE_SANDBOX": "1"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_content_patterns_still_work(self):
        spec = [{"path_pattern": "out.py", "content_patterns": ["def add"]}]
        self.assertEqual(check_expected(["out.py"], spec, self.ws), [])

    def test_not_content_patterns(self):
        spec = [{"path_pattern": "out.py", "not_content_patterns": ["def add"]}]
        failures = check_expected(["out.py"], spec, self.ws)
        self.assertTrue(failures and "forbidden" in failures[0])

    def test_regex_patterns(self):
        good = [{"path_pattern": "out.py", "regex_patterns": [r"return\s+a\s*\+\s*b"]}]
        bad = [{"path_pattern": "out.py", "regex_patterns": [r"class \w+"]}]
        self.assertEqual(check_expected(["out.py"], good, self.ws), [])
        self.assertTrue(check_expected(["out.py"], bad, self.ws))

    def test_min_lines(self):
        spec = [{"path_pattern": "out.py", "min_lines": 99}]
        failures = check_expected(["out.py"], spec, self.ws)
        self.assertTrue(failures and "line(s)" in failures[0])

    def test_check_command_pass_and_fail(self):
        ok = {"check_command": f'"{sys.executable}" -c "import out; assert out.add(1,2)==3"'}
        failures, oracle = run_check_command(ok, self.ws)
        self.assertEqual(failures, [])
        self.assertTrue(oracle["ran"])
        self.assertEqual(oracle["exit_code"], 0)
        self.assertIn(oracle["sandbox"], ("host", "docker"))
        self.assertIsInstance(oracle["duration_s"], float)

        bad = {"check_command": f'"{sys.executable}" -c "raise SystemExit(2)"'}
        failures, oracle = run_check_command(bad, self.ws)
        self.assertTrue(failures and "exit 2" in failures[0])
        self.assertEqual(oracle["exit_code"], 2)

    def test_evaluate_case_skips_command_when_files_fail(self):
        case = {"expected_files": [{"path_pattern": "missing.py"}],
                "check_command": f'"{sys.executable}" -c "raise SystemExit(9)"'}
        failures, oracle = evaluate_case(case, [], self.ws)
        self.assertEqual(len(failures), 1)          # only the file failure
        self.assertIn("missing.py", failures[0])
        self.assertFalse(oracle["ran"])             # check_command never invoked


class PrepareWorkspaceTests(unittest.TestCase):
    """setup_repo/git_init: L3 repo-scale workspace preparation (cases.prepare_workspace)."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_plain_setup_files_only_unchanged(self):
        prepare_workspace(self.ws, {"setup_files": {"a.py": "x = 1\n"}})
        self.assertEqual((self.ws / "a.py").read_text(encoding="utf-8"), "x = 1\n")
        self.assertFalse((self.ws / ".git").exists())

    def test_unknown_setup_repo_raises(self):
        with self.assertRaises(FileNotFoundError):
            prepare_workspace(self.ws, {"setup_repo": "no-such-starter-repo"})

    def test_setup_repo_copied_then_setup_files_overlay(self):
        # Uses a real starter repo from repos/ (they ship with the corpus).
        repo = next((p.name for p in REPOS_DIR.iterdir() if p.is_dir()), None)
        self.assertIsNotNone(repo, f"no starter repos found under {REPOS_DIR}")
        case = {"setup_repo": repo, "setup_files": {"README.md": "overlaid\n"}}
        prepare_workspace(self.ws, case)
        copied = [p for p in self.ws.rglob("*") if p.is_file()]
        self.assertGreater(len(copied), 5, "starter repo files were not copied")
        # setup_files must win over the copied repo's file of the same name
        self.assertEqual((self.ws / "README.md").read_text(encoding="utf-8"), "overlaid\n")

    def test_git_init_commits_prepared_state(self):
        import shutil as _shutil
        if not _shutil.which("git"):
            self.skipTest("git not on PATH")
        prepare_workspace(self.ws, {"setup_files": {"a.py": "x = 1\n"}, "git_init": True})
        self.assertTrue((self.ws / ".git").is_dir())
        import subprocess as _sp
        status = _sp.run(["git", "status", "--porcelain"], cwd=self.ws,
                         capture_output=True, text=True)
        self.assertEqual(status.stdout, "", "git_init must leave a clean tree (all committed)")

    def test_setup_files_traversal_rejected(self):
        # H-01: a malicious/broken case JSON key must not be able to escape
        # the sandboxed workspace via "../" and write onto the host.
        outside_marker = self.ws.parent / "optarena_test_traversal_canary.txt"
        self.addCleanup(lambda: outside_marker.unlink(missing_ok=True))
        with self.assertRaises(ValueError):
            prepare_workspace(self.ws, {"setup_files": {
                f"../{outside_marker.name}": "pwned\n"}})
        self.assertFalse(outside_marker.exists())

    def test_setup_files_absolute_path_rejected(self):
        outside_marker = self.ws.parent / "optarena_test_abs_canary.txt"
        self.addCleanup(lambda: outside_marker.unlink(missing_ok=True))
        with self.assertRaises(ValueError):
            prepare_workspace(self.ws, {"setup_files": {
                str(outside_marker): "pwned\n"}})
        self.assertFalse(outside_marker.exists())


class TestSetupFilesTests(unittest.TestCase):
    """test_setup_files: real test code written after generation, run via check_command."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        self._env = mock.patch.dict(os.environ, {"OPTARENA_DISABLE_SANDBOX": "1"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_writes_test_files_and_runs_them(self):
        (self.ws / "add.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        case = {
            "expected_files": [{"path_pattern": "add.py", "content_patterns": ["def add"]}],
            "test_setup_files": {
                "test_add.py": "import add\nassert add.add(2, 3) == 5\n",
            },
            "check_command": f'"{sys.executable}" test_add.py',
        }
        failures, oracle = evaluate_case(case, ["add.py"], self.ws)
        self.assertEqual(failures, [])
        self.assertTrue((self.ws / "test_add.py").exists())
        self.assertEqual(oracle["test_setup_files"], ["test_add.py"])
        self.assertTrue(oracle["ran"])
        self.assertEqual(oracle["sandbox"], "host")   # OPTARENA_DISABLE_SANDBOX=1 in setUp

    def test_hidden_test_file_not_required_in_created_list(self):
        # test_setup_files must not need to appear in `created` (the diff of
        # files the *model* produced) - it's written by the harness afterward.
        (self.ws / "add.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        case = {
            "expected_files": [{"path_pattern": "add.py"}],
            "test_setup_files": {"test_add.py": "import add\nassert add.add(2, 3) == 5\n"},
            "check_command": f'"{sys.executable}" test_add.py',
        }
        failures, oracle = evaluate_case(case, ["add.py"], self.ws)  # test_add.py deliberately absent from `created`
        self.assertTrue(failures and "exit" in failures[0])  # real bug (subtraction) caught
        self.assertNotEqual(oracle["exit_code"], 0)


class SetupRepoContainmentTests(unittest.TestCase):
    """
    A-04: `setup_repo` names a directory under repos/ and nothing else. It is
    untrusted input (case JSON, installable from a URL), and copy_setup_repo
    turns it into a filesystem path - a traversal there copies an arbitrary
    host directory INTO the agent-visible workspace, where the baselines feed
    it back to the configured backend.
    """

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_setuprepo_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_traversal_source_is_rejected(self):
        from optarena.cases import copy_setup_repo
        for evil in ("..", "../..", "../optarena/cases", "../../etc"):
            with self.subTest(setup_repo=evil):
                with self.assertRaises(ValueError):
                    copy_setup_repo(self.ws, evil)
                self.assertEqual(list(self.ws.rglob("*")), [],
                                 f"{evil!r} copied files into the workspace")

    def test_absolute_source_is_rejected(self):
        from optarena.cases import copy_setup_repo
        absolute = str(Path(__file__).resolve().parent)
        with self.assertRaises(ValueError):
            copy_setup_repo(self.ws, absolute)

    def test_schema_rejects_separators_in_setup_repo(self):
        for evil in ("../x", "a/b", "a\\b", "/abs", "..", "."):
            with self.subTest(setup_repo=evil):
                with self.assertRaises(SchemaError):
                    validate_case({"name": "c", "setup_repo": evil})

    def test_legitimate_starter_repo_still_copies(self):
        from optarena.cases import copy_setup_repo
        available = [p.name for p in REPOS_DIR.iterdir() if p.is_dir()] if REPOS_DIR.is_dir() else []
        if not available:
            self.skipTest("no starter repos checked out")
        copy_setup_repo(self.ws, available[0])
        self.assertTrue(any(p.is_file() for p in self.ws.rglob("*")))
        validate_case({"name": "c", "setup_repo": available[0]})   # and it validates

    def test_missing_repos_dir_gives_wheel_install_remediation(self):
        """P2-01: a wheel install has no repos/ at all - the error should say
        so and point at the fix, not just "not found" (indistinguishable from
        a typo'd case name in a real source checkout)."""
        from optarena.cases import copy_setup_repo
        missing = self.ws / "no-such-repos-dir"
        with mock.patch("optarena._cases._workspace_setup.REPOS_DIR", missing):
            with self.assertRaises(FileNotFoundError) as ctx:
                copy_setup_repo(self.ws, "some-repo")
        self.assertIn("source-checkout-only", str(ctx.exception))
        self.assertIn("wheel", str(ctx.exception))

    def test_existing_repos_dir_wrong_name_gives_plain_not_found(self):
        """The wheel-install remediation must not fire when repos/ genuinely
        exists but the named repo simply isn't in it - that's a real typo,
        not a packaging-boundary problem."""
        from optarena.cases import copy_setup_repo
        present = self.ws / "real-repos-dir"
        present.mkdir()
        with mock.patch("optarena._cases._workspace_setup.REPOS_DIR", present):
            with self.assertRaises(FileNotFoundError) as ctx:
                copy_setup_repo(self.ws, "nonexistent-repo-name")
        self.assertNotIn("source-checkout-only", str(ctx.exception))


class SandboxBuildWheelRemediationTests(unittest.TestCase):
    """P2-01: `optarena sandbox build` from a wheel install (no docker/
    checked out) should say so plainly rather than a bare "no Dockerfile"."""

    def test_missing_docker_dir_gives_wheel_install_remediation(self):
        import io
        from contextlib import redirect_stderr
        from optarena.cli._sandbox_cmds import cmd_docker
        missing = Path(tempfile.mkdtemp(prefix="optarena_test_nodocker_")) / "docker"
        self.addCleanup(shutil.rmtree, missing.parent, ignore_errors=True)
        args = argparse.Namespace(action="build", all=False, lang=None)
        with mock.patch("optarena.cli._sandbox_cmds.DOCKERFILE_DIR", missing):
            with mock.patch("optarena.cli._sandbox_cmds.dockerfile_for", return_value=missing / "Dockerfile"):
                buf = io.StringIO()
                with redirect_stderr(buf):
                    rc = cmd_docker(args)
        self.assertNotEqual(rc, 0)
        self.assertIn("source-checkout-only", buf.getvalue())
        self.assertIn("wheel", buf.getvalue())


class ImageReferenceValidationTests(unittest.TestCase):
    """
    A-05: an image reference reaches a `docker run` command line ahead of the
    image argument, and the engine parses options up to the first non-option
    token - so a value starting with "-" is read as a FLAG. Every source of an
    image reference (case field, scenario override, OPTARENA_SANDBOX_IMAGE)
    must be shape-checked.
    """

    def test_option_lookalikes_are_rejected(self):
        from optarena.cases import validate_image_ref
        for evil in ("--privileged", "-v=/:/hostfs", "--entrypoint=sh",
                     "--volume=/:/host", "-it", "img with space", ""):
            with self.subTest(image=evil):
                with self.assertRaises(ValueError):
                    validate_image_ref(evil)

    def test_real_references_are_accepted(self):
        from optarena.cases import validate_image_ref
        for good in ("optarena-tester:latest", "optarena-tester-python:latest",
                     "ghcr.io/trysti-labs/optarena/optarena-tester:v1.2.3",
                     "debian@sha256:" + "a" * 64, "img"):
            with self.subTest(image=good):
                self.assertEqual(validate_image_ref(good), good)

    def test_schema_rejects_case_image_option_lookalike(self):
        with self.assertRaises(SchemaError):
            validate_case({"name": "c", "image": "--privileged"})
        validate_case({"name": "c", "image": "optarena-tester:latest"})

    def test_schema_rejects_override_option_lookalike(self):
        with self.assertRaises(SchemaError):
            validate_scenario({"name": "s", "driver": "aider",
                               "image_overrides": {"python": "--privileged"}})

    def test_resolve_image_validates_env_override(self):
        import optarena._cases._sandbox as cases_mod
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with mock.patch.dict(os.environ, {"OPTARENA_SANDBOX_IMAGE": "--privileged"}):
            with self.assertRaises(ValueError):
                cases_mod.DockerSandbox(d)

    def test_every_builtin_case_image_is_valid(self):
        from optarena.cases import validate_image_ref
        for case in load_cases():
            if case.get("image"):
                validate_image_ref(case["image"])


class DockerCheckCommandTests(unittest.TestCase):
    """Docker sandboxing for check_command: command construction + fallback."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        import optarena._cases._sandbox as cases_mod
        self.cases_mod = cases_mod
        # Reset the module-level docker-availability cache before each test.
        self._orig = (cases_mod._docker_checked_at, cases_mod._docker_ok,
                      cases_mod._docker_warned, dict(cases_mod._active_sandboxes))
        cases_mod._docker_checked_at = -1.0
        cases_mod._docker_ok = False
        cases_mod._docker_warned = False
        cases_mod._active_sandboxes.clear()

    def tearDown(self):
        (self.cases_mod._docker_checked_at, self.cases_mod._docker_ok,
         self.cases_mod._docker_warned, sandboxes) = self._orig
        self.cases_mod._active_sandboxes.clear()
        self.cases_mod._active_sandboxes.update(sandboxes)

    def test_workspace_crlf_is_normalized_before_the_command_runs(self):
        # A-42: run_check_command must normalize the workspace before
        # dispatching, regardless of which sandbox path is used - this is
        # the one place every driver's check funnels through.
        (self.ws / "backup.sh").write_bytes(b'#!/bin/sh\r\necho hi\r\n')

        def fake_run(args, **kwargs):
            if args[:2] == ["docker", "info"]:
                return mock.Mock(returncode=0)
            if args[:3] == ["docker", "image", "inspect"]:
                return mock.Mock(returncode=0)
            if args[:2] == ["docker", "run"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected subprocess call: {args}")

        with mock.patch.object(self.cases_mod.subprocess, "run", side_effect=fake_run), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPTARENA_DISABLE_SANDBOX", None)
            self.cases_mod.run_check_command({"check_command": "sh backup.sh"}, self.ws)

        self.assertNotIn(b"\r", (self.ws / "backup.sh").read_bytes())

    def test_docker_run_invoked_with_isolation_flags(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["docker", "info"]:
                return mock.Mock(returncode=0)
            if args[:3] == ["docker", "image", "inspect"]:
                return mock.Mock(returncode=0)
            if args[:2] == ["docker", "run"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected subprocess call: {args}")

        with mock.patch.object(self.cases_mod.subprocess, "run", side_effect=fake_run), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPTARENA_DISABLE_SANDBOX", None)
            failures, oracle = self.cases_mod.run_check_command({"check_command": "echo hi"}, self.ws)

        self.assertEqual(failures, [])
        self.assertEqual(oracle["sandbox"], "docker")
        self.assertEqual(oracle["image"], self.cases_mod.DOCKER_IMAGE_DEFAULT)
        self.assertEqual(oracle["exit_code"], 0)
        self.assertTrue(oracle["ran"])
        run_call = next(c for c in calls if c[:2] == ["docker", "run"])
        self.assertIn("--network", run_call)
        self.assertEqual(run_call[run_call.index("--network") + 1], "none")
        self.assertIn("-v", run_call)
        mount = run_call[run_call.index("-v") + 1]
        self.assertTrue(mount.endswith(":/workspace"))
        self.assertIn("echo hi", run_call)

    def test_falls_back_to_host_when_docker_unavailable(self):
        def fake_run(args, **kwargs):
            if args[:2] == ["docker", "info"]:
                raise FileNotFoundError("no docker")
            raise AssertionError(f"unexpected subprocess call: {args}")

        with mock.patch.object(self.cases_mod.subprocess, "run", side_effect=fake_run), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPTARENA_DISABLE_SANDBOX", None)
            self.assertFalse(self.cases_mod._docker_available())

    def test_no_docker_env_forces_local_execution(self):
        calls = []

        def fake_capture(cmd, **kwargs):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        # Host exec now goes through run_capture (process-group aware), not
        # subprocess.run directly - see H-11 / _run_check_command_local.
        with mock.patch.object(self.cases_mod, "run_capture", side_effect=fake_capture), \
             mock.patch.dict(os.environ, {"OPTARENA_DISABLE_SANDBOX": "1"}):
            failures, oracle = self.cases_mod.run_check_command({"check_command": "echo hi"}, self.ws)

        self.assertEqual(failures, [])
        self.assertEqual(oracle["sandbox"], "host")

        # shell=True local call (a bare string, not a "docker" argv list)
        self.assertEqual(calls, ["echo hi"])

    def test_refuses_host_execution_when_docker_unavailable_and_not_allowed(self):
        # C-02: Docker merely being unavailable (no explicit opt-out) must not
        # silently fall back to running an untrusted check_command on the host.
        def fake_run(args, **kwargs):
            if args[:2] == ["docker", "info"]:
                raise FileNotFoundError("no docker")
            raise AssertionError(f"unexpected subprocess call: {args}")

        with mock.patch.object(self.cases_mod.subprocess, "run", side_effect=fake_run), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPTARENA_DISABLE_SANDBOX", None)
            os.environ.pop("OPTARENA_ALLOW_UNSAFE_HOST_EXEC", None)
            failures, oracle = self.cases_mod.run_check_command({"check_command": "echo hi"}, self.ws)

        self.assertTrue(failures)
        self.assertIn("refused", failures[0])
        self.assertEqual(oracle["sandbox"], "refused")
        self.assertFalse(oracle.get("ran"))

    def test_allow_unsafe_host_exec_permits_host_fallback(self):
        def fake_run(args, **kwargs):
            if args[:2] == ["docker", "info"]:
                raise FileNotFoundError("no docker")
            raise AssertionError(f"unexpected subprocess call: {args}")

        def fake_capture(cmd, **kwargs):
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.cases_mod.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(self.cases_mod, "run_capture", side_effect=fake_capture), \
             mock.patch.dict(os.environ, {"OPTARENA_ALLOW_UNSAFE_HOST_EXEC": "1"}, clear=False):
            os.environ.pop("OPTARENA_DISABLE_SANDBOX", None)
            failures, oracle = self.cases_mod.run_check_command({"check_command": "echo hi"}, self.ws)

        self.assertEqual(failures, [])
        self.assertEqual(oracle["sandbox"], "host")
        self.assertTrue(oracle["ran"])

    def test_no_docker_env_still_sufficient_on_its_own(self):
        # OPTARENA_DISABLE_SANDBOX=1 alone (the pre-existing, already-explicit
        # opt-out) must keep working unchanged - it must not also require
        # OPTARENA_ALLOW_UNSAFE_HOST_EXEC.
        def fake_capture(cmd, **kwargs):
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.cases_mod, "run_capture", side_effect=fake_capture), \
             mock.patch.dict(os.environ, {"OPTARENA_DISABLE_SANDBOX": "1"}, clear=False):
            os.environ.pop("OPTARENA_ALLOW_UNSAFE_HOST_EXEC", None)
            failures, oracle = self.cases_mod.run_check_command({"check_command": "echo hi"}, self.ws)

        self.assertEqual(failures, [])
        self.assertEqual(oracle["sandbox"], "host")


class SharedSandboxTests(unittest.TestCase):
    """
    One container for the whole run, not one per check_command call - this is
    what DockerSandbox exists to guarantee. A prior version started a fresh
    `docker run --rm` on every call (7 cases x 3 trials = 21 containers for
    one `optarena run`); these tests pin down that a single `docker run -d`
    starts the sandbox and every subsequent call reuses it via `docker exec`.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="optarena_test_root_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "case_a").mkdir()
        (self.root / "case_b" / "t1").mkdir(parents=True)
        import optarena._cases._sandbox as cases_mod
        self.cases_mod = cases_mod
        self._orig = (cases_mod._docker_checked_at, cases_mod._docker_ok,
                      cases_mod._docker_warned, dict(cases_mod._active_sandboxes))
        cases_mod._docker_checked_at = -1.0
        cases_mod._docker_ok = False
        cases_mod._docker_warned = False
        cases_mod._active_sandboxes.clear()

    def tearDown(self):
        (self.cases_mod._docker_checked_at, self.cases_mod._docker_ok,
         self.cases_mod._docker_warned, sandboxes) = self._orig
        self.cases_mod._active_sandboxes.clear()
        self.cases_mod._active_sandboxes.update(sandboxes)

    def test_one_container_serves_every_call(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["docker", "info"]:
                return mock.Mock(returncode=0)
            if args[:3] == ["docker", "image", "inspect"]:
                return mock.Mock(returncode=0)
            if args[:3] == ["docker", "run", "-d"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args[:2] == ["docker", "exec"]:
                return mock.Mock(returncode=0, stdout="PASS", stderr="")
            if args[:2] == ["docker", "stop"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected subprocess call: {args}")

        with mock.patch.object(self.cases_mod.subprocess, "run", side_effect=fake_run), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPTARENA_DISABLE_SANDBOX", None)
            with self.cases_mod.DockerSandbox(self.root) as sandbox:
                self.assertTrue(sandbox.active)
                f1, o1 = self.cases_mod.run_check_command({"check_command": "echo a"}, self.root / "case_a")
                f2, o2 = self.cases_mod.run_check_command({"check_command": "echo b"}, self.root / "case_b" / "t1")

        run_d_calls = [c for c in calls if c[:3] == ["docker", "run", "-d"]]
        exec_calls = [c for c in calls if c[:2] == ["docker", "exec"]]
        stop_calls = [c for c in calls if c[:2] == ["docker", "stop"]]
        self.assertEqual(len(run_d_calls), 1, "expected exactly ONE container start for the whole run")
        self.assertEqual(len(exec_calls), 2, "expected one `docker exec` per check_command call")
        self.assertEqual(len(stop_calls), 1)

        self.assertEqual(f1, [])
        self.assertEqual(f2, [])
        self.assertEqual(o1["container"], o2["container"])       # same container both times
        self.assertEqual(o1["container"], run_d_calls[0][run_d_calls[0].index("--name") + 1])
        self.assertEqual(o1["sandbox"], "docker")

        # Each exec targets the right subdirectory under the one shared mount.
        workdir_a = exec_calls[0][exec_calls[0].index("-w") + 1]
        workdir_b = exec_calls[1][exec_calls[1].index("-w") + 1]
        self.assertEqual(workdir_a, "/workspace/case_a")
        self.assertEqual(workdir_b, "/workspace/case_b/t1")

    def test_no_sandbox_falls_back_to_ephemeral_per_call(self):
        # Without an active DockerSandbox, behavior is unchanged: run_check_command
        # still works (via the pre-existing ephemeral `docker run --rm` path).
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:2] == ["docker", "info"]:
                return mock.Mock(returncode=0)
            if args[:3] == ["docker", "image", "inspect"]:
                return mock.Mock(returncode=0)
            if args[:2] == ["docker", "run"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected subprocess call: {args}")

        with mock.patch.object(self.cases_mod.subprocess, "run", side_effect=fake_run), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPTARENA_DISABLE_SANDBOX", None)
            failures, oracle = self.cases_mod.run_check_command({"check_command": "echo hi"}, self.root / "case_a")

        self.assertEqual(failures, [])
        self.assertIn("container", oracle)  # ephemeral path also records its (unique) container name


class RunScenarioEmptyCasesTests(unittest.TestCase):
    """
    H-06: a resolved case set of zero (typo'd --cases name already raises
    elsewhere via load_cases; the real gap is a --language/--framework filter
    that matches nothing) must not silently produce a "0/0 passed" run whose
    `failed` count is also 0 - indistinguishable from a real all-green run to
    anything checking just the exit code.
    """

    def setUp(self):
        self.empty_dir = Path(tempfile.mkdtemp(prefix="optarena_test_nocases_"))
        self.addCleanup(shutil.rmtree, self.empty_dir, ignore_errors=True)

    def test_empty_case_set_refused_by_default(self):
        sc = Scenario(name="x", driver="aider", cases=[], cases_dir=str(self.empty_dir))
        with mock.patch("optarena.runner._execution.get_driver") as get_driver_mock:
            with self.assertRaises(ValueError) as ctx:
                run_scenario(sc)
            get_driver_mock.assert_not_called()
        self.assertIn("0 cases", str(ctx.exception))

    def test_empty_case_set_allowed_with_flag(self):
        sc = Scenario(name="x", driver="aider", cases=[], cases_dir=str(self.empty_dir))
        fake_driver = mock.Mock(parallel_safe=False, caches_results=False)
        with mock.patch("optarena.runner._execution.get_driver", return_value=fake_driver):
            rec = run_scenario(sc, allow_empty=True)
        fake_driver.prepare.assert_called_once()
        fake_driver.teardown.assert_called_once()
        self.assertEqual(rec.summary["cases"], 0)
        self.assertEqual(rec.summary["failed"], 0)


class ParallelSandboxSharingTests(unittest.TestCase):
    """
    H-02/C-05: the shared DockerSandbox container (whole-run-root mount) is
    only used when it's safe on BOTH axes - serial execution (concurrent
    `docker exec`s would share a process table and the timeout-reap
    `kill -9 -1` would kill sibling cases) AND the trusted built-in corpus
    (a custom `cases_dir` pack could ship a check_command that reads/tampers
    with other cases' workspaces via the shared mount). Everything else gets
    run_check_command's per-call ephemeral container, which mounts only that
    case's own workspace.
    """

    def setUp(self):
        self.cases_dir = Path(tempfile.mkdtemp(prefix="optarena_test_parallel_"))
        self.addCleanup(shutil.rmtree, self.cases_dir, ignore_errors=True)
        (self.cases_dir / "c1.json").write_text(json.dumps({
            "name": "c1", "prompts": ["do it"], "check_command": "echo hi",
        }), encoding="utf-8")

    @staticmethod
    def _fake_driver(name="c1"):
        driver = mock.Mock(parallel_safe=True, caches_results=False)
        driver.run_case.return_value = CaseResult(name=name, passed=True, duration_s=0.1)
        return driver

    def test_no_sandbox_started_when_parallel(self):
        sc = Scenario(name="x", driver="aider", cases_dir=str(self.cases_dir))
        with mock.patch("optarena.runner._execution.get_driver", return_value=self._fake_driver()), \
             mock.patch("optarena.runner._execution.DockerSandbox") as sandbox_cls:
            run_scenario(sc, parallel=4)
        sandbox_cls.assert_not_called()

    def test_no_shared_sandbox_for_custom_case_pack(self):
        # C-05: a custom cases_dir is untrusted input - it must never share
        # one whole-run-root mount across its cases, even serially.
        sc = Scenario(name="x", driver="aider", cases_dir=str(self.cases_dir))
        with mock.patch("optarena.runner._execution.get_driver", return_value=self._fake_driver()), \
             mock.patch("optarena.runner._execution.DockerSandbox") as sandbox_cls:
            run_scenario(sc, parallel=1)
        sandbox_cls.assert_not_called()

    def test_sandbox_still_started_for_builtin_corpus_serial(self):
        # The built-in catalogue (repo-controlled, self-verified) keeps the
        # fast shared-container path for serial runs.
        sc = Scenario(name="x", driver="aider", cases=["create_factorial"])
        with mock.patch("optarena.runner._execution.get_driver",
                        return_value=self._fake_driver("create_factorial")), \
             mock.patch("optarena.runner._execution.DockerSandbox") as sandbox_cls:
            run_scenario(sc, parallel=1)
        sandbox_cls.assert_called_once()


class ParallelDurabilityTests(unittest.TestCase):
    """
    A-01/A-02: the --parallel path must be as durable and as crash-safe as the
    serial one. Two defects lived here: checkpoints that recorded zero cases
    (so an interrupted parallel run lost everything F-02 exists to preserve),
    and a worker exception that hung the whole run forever.
    """

    def setUp(self):
        self.cases_dir = Path(tempfile.mkdtemp(prefix="optarena_test_pardur_"))
        self.addCleanup(shutil.rmtree, self.cases_dir, ignore_errors=True)
        for i in range(4):
            (self.cases_dir / f"c{i}.json").write_text(json.dumps({
                "name": f"c{i}", "prompts": ["do it"], "expected_files": [],
            }), encoding="utf-8")
        self.results_dir = Path(tempfile.mkdtemp(prefix="optarena_test_pardur_out_"))
        self.addCleanup(shutil.rmtree, self.results_dir, ignore_errors=True)

    def _scenario(self):
        return Scenario(name="x", driver="aider", cases_dir=str(self.cases_dir))

    def test_checkpoints_record_completed_cases(self):
        # A-01: every checkpoint must see the cases finished SO FAR - the
        # regression was a constant zero, because on_result closed over a list
        # the parallel runner never appended to.
        driver = mock.Mock(parallel_safe=True, caches_results=False)
        driver.run_case.side_effect = lambda case, sc, ws: CaseResult(
            name=case["name"], passed=True, duration_s=0.1)
        seen = []
        with mock.patch("optarena.runner._execution.get_driver", return_value=driver), \
             mock.patch("optarena.runner._execution.store.save_checkpoint",
                        side_effect=lambda rec: seen.append(len(rec.cases))):
            record = run_scenario(self._scenario(), parallel=3)
        self.assertEqual(seen, [1, 2, 3, 4])
        self.assertEqual(len(record.cases), 4)

    def test_worker_exception_does_not_hang_the_run(self):
        # A-02: an unexpected exception inside a worker used to kill that
        # thread without ever queuing a result, leaving the collector blocked
        # on a fixed-count get() forever. It must become this case's error.
        def _boom(case, sc, ws):
            if case["name"] == "c1":
                raise OSError("disk full")
            return CaseResult(name=case["name"], passed=True, duration_s=0.1)

        driver = mock.Mock(parallel_safe=True, caches_results=False)
        driver.run_case.side_effect = _boom
        done = []
        thread = __import__("threading").Thread(
            target=lambda: done.append(run_scenario(self._scenario(), parallel=2)),
            daemon=True)
        with mock.patch("optarena.runner._execution.get_driver", return_value=driver), \
             mock.patch("optarena.runner._execution.store.save_checkpoint"):
            thread.start()
            thread.join(timeout=60)
        self.assertFalse(thread.is_alive(), "parallel run hung on a worker exception")
        record = done[0]
        self.assertEqual(len(record.cases), 4)
        failed = [c for c in record.cases if c["error"]]
        self.assertEqual(len(failed), 1)
        self.assertIn("disk full", failed[0]["error"])
        self.assertFalse(failed[0]["execution_ok"])

    def test_interrupted_parallel_run_keeps_completed_cases(self):
        # The whole point of A-01: what survives a KeyboardInterrupt.
        import threading as _threading
        gate = _threading.Event()

        def _hang_after_two(case, sc, ws):
            if case["name"] in ("c2", "c3"):
                gate.wait(timeout=30)
                raise KeyboardInterrupt
            return CaseResult(name=case["name"], passed=True, duration_s=0.1)

        driver = mock.Mock(parallel_safe=True, caches_results=False)
        driver.run_case.side_effect = _hang_after_two
        saved = []
        with mock.patch("optarena.runner._execution.get_driver", return_value=driver), \
             mock.patch("optarena.runner._execution.store.save_checkpoint",
                        side_effect=lambda rec: saved.append([c["name"] for c in rec.cases])):
            gate.set()
            run_scenario(self._scenario(), parallel=2)
        # c2/c3 report as errored cases; c0/c1 must still be in the record.
        self.assertTrue(saved, "no checkpoint was written at all")
        self.assertIn("c0", saved[-1])


class LegacyArgvRewriteTests(unittest.TestCase):
    """The pre-grouping command spellings must rewrite to the grouped ones so
    existing scripts/CI keep working while `--help` stays clean."""

    def _rw(self, argv):
        from optarena.cli import _rewrite_legacy_argv
        return _rewrite_legacy_argv(argv)

    def test_list_forms(self):
        self.assertEqual(self._rw(["list"]), ["runs", "list"])
        self.assertEqual(self._rw(["list", "runs"]), ["runs", "list"])
        self.assertEqual(self._rw(["list", "cases", "--language", "go"]),
                         ["cases", "list", "--language", "go"])
        self.assertEqual(self._rw(["list", "drivers"]), ["drivers", "list"])

    def test_init_verify_docker(self):
        self.assertEqual(self._rw(["init", "mydir"]), ["cases", "init", "mydir"])
        self.assertEqual(self._rw(["verify-corpus", "--cases", "a"]),
                         ["cases", "verify", "--cases", "a"])
        self.assertEqual(self._rw(["docker", "build", "--lang", "go"]),
                         ["sandbox", "build", "--lang", "go"])

    def test_global_option_skipped_when_finding_command(self):
        self.assertEqual(self._rw(["--results-dir", "/tmp/x", "list", "runs"]),
                         ["--results-dir", "/tmp/x", "runs", "list"])
        self.assertEqual(self._rw(["--results-dir=/tmp/x", "init"]),
                         ["--results-dir=/tmp/x", "cases", "init"])

    def test_grouped_and_core_commands_pass_through(self):
        for argv in (["cases", "list"], ["run", "--driver", "aider"],
                     ["compare", "a", "b"], ["sandbox", "status"], ["--help"]):
            self.assertEqual(self._rw(list(argv)), list(argv))


class CachingDriverTrialsPlumbingTests(unittest.TestCase):
    """
    M-09: a caching driver (one with caches_results=True; see
    Driver.caches_results) can't be repeated by the runner's own per-case
    loop, so instead of silently dropping --trials to 1, the runner hands
    the trial count to the driver (which repeats each case itself). Verify
    the count is plumbed through and the runner doesn't also double-run.
    """

    def setUp(self):
        self.cases_dir = Path(tempfile.mkdtemp(prefix="optarena_test_uitrials_"))
        self.addCleanup(shutil.rmtree, self.cases_dir, ignore_errors=True)
        (self.cases_dir / "c1.json").write_text(json.dumps({
            "name": "c1", "prompts": ["do it"],
        }), encoding="utf-8")

    def test_trials_handed_to_caching_driver_not_dropped(self):
        driver = mock.Mock(parallel_safe=False, caches_results=True, trials=1)
        driver.run_case.return_value = CaseResult(name="c1", passed=True, duration_s=0.1)
        sc = Scenario(name="x", driver="aider", cases_dir=str(self.cases_dir))
        with mock.patch("optarena.runner._execution.get_driver", return_value=driver):
            run_scenario(sc, trials=3)
        # the trial count reached the driver...
        self.assertEqual(driver.trials, 3)
        # ...and the runner did NOT also loop 3x (the harness owns repetition).
        self.assertEqual(driver.run_case.call_count, 1)


class RunCaptureTests(unittest.TestCase):
    """H-11: run_capture must kill the whole process TREE on timeout, so a
    timed-out check/agent can't leave orphaned children holding ports/CPU."""

    def test_normal_command_returns_completed_process(self):
        import sys
        proc = run_capture([sys.executable, "-c", "print('ok')"], timeout=30,
                           text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("ok", proc.stdout)

    def test_timeout_raises_timeout_expired(self):
        import subprocess as sp
        import sys
        with self.assertRaises(sp.TimeoutExpired):
            run_capture([sys.executable, "-c", "import time; time.sleep(30)"],
                        timeout=1)

    def test_child_process_tree_killed_on_timeout(self):
        import subprocess as sp
        import sys
        import time
        tmp = Path(tempfile.mkdtemp(prefix="optarena_treekill_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        sentinel = tmp / "child_ran.txt"
        child = tmp / "child.py"
        child.write_text(
            "import time\n"
            "time.sleep(3)\n"
            f"open(r'{sentinel}', 'w').close()\n",
            encoding="utf-8")
        parent = tmp / "parent.py"
        parent.write_text(
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, r'{child}'])\n"
            "time.sleep(30)\n",
            encoding="utf-8")
        with self.assertRaises(sp.TimeoutExpired):
            run_capture([sys.executable, str(parent)], timeout=1)
        # Wait past the child's own 3s sleep: if the tree was killed the
        # sentinel never appears; if only the parent was killed it does.
        time.sleep(5)
        self.assertFalse(sentinel.exists(),
                         "child outlived the timed-out parent - process tree not killed")

    def test_grandchild_killed_even_if_immediate_parent_already_exited(self):
        """P0-03: the scenario `taskkill /T`'s parent-PID tree-walk is
        weakest against - by the time cleanup runs, the immediate child has
        already exited (its PID could even be reused by something
        unrelated), so a walk keyed on live parent-child PID links can miss
        the grandchild entirely. A Windows Job Object's membership doesn't
        depend on the immediate parent still being alive; on POSIX, process
        GROUP membership (set once, at session creation) doesn't either -
        this is a real cross-platform regression test, not Windows-only."""
        import subprocess as sp
        import sys
        import time
        tmp = Path(tempfile.mkdtemp(prefix="optarena_treekill_gc_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        sentinel = tmp / "grandchild_ran.txt"
        grandchild = tmp / "grandchild.py"
        grandchild.write_text(
            "import time\n"
            "time.sleep(3)\n"
            f"open(r'{sentinel}', 'w').close()\n",
            encoding="utf-8")
        child = tmp / "child.py"
        child.write_text(
            "import subprocess, sys\n"
            f"subprocess.Popen([sys.executable, r'{grandchild}'])\n"
            # exits immediately - the grandchild's recorded parent is gone
            # well before the top-level timeout fires
            "\n",
            encoding="utf-8")
        parent = tmp / "parent.py"
        parent.write_text(
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, r'{child}'])\n"
            "time.sleep(30)\n",
            encoding="utf-8")
        with self.assertRaises(sp.TimeoutExpired):
            run_capture([sys.executable, str(parent)], timeout=1)
        time.sleep(5)
        self.assertFalse(sentinel.exists(),
                         "grandchild outlived the timed-out ancestor after its own "
                         "immediate parent had already exited")

    @unittest.skipUnless(os.name != "posix", "Windows Job Object behavior")
    def test_detached_grandchild_process_group_still_killed(self):
        """P0-03: a grandchild spawned into its OWN new console/process
        group (CREATE_NEW_PROCESS_GROUP - common for tools that want to
        survive a Ctrl+C sent to their parent's group) must still be caught.
        Job Object membership is unaffected by process-group detachment -
        only an explicit breakaway request (which this job's LimitFlags do
        not permit) would let a descendant escape it."""
        import subprocess as sp
        import sys
        import time
        tmp = Path(tempfile.mkdtemp(prefix="optarena_treekill_detach_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        sentinel = tmp / "detached_ran.txt"
        detached = tmp / "detached.py"
        detached.write_text(
            "import time\n"
            "time.sleep(3)\n"
            f"open(r'{sentinel}', 'w').close()\n",
            encoding="utf-8")
        child = tmp / "child.py"
        child.write_text(
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, r'{detached}'], "
            "creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)\n"
            "time.sleep(30)\n",
            encoding="utf-8")
        with self.assertRaises(sp.TimeoutExpired):
            run_capture([sys.executable, str(child)], timeout=1)
        time.sleep(5)
        self.assertFalse(sentinel.exists(),
                         "detached grandchild (own process group) outlived the "
                         "timed-out parent")

    def test_output_capture_is_bounded_not_unbounded(self):
        # F-03: a misbehaving process dumping way more than any oracle tail
        # actually uses must not make run_capture buffer all of it - confirms
        # captured stdout stays near _MAX_CAPTURE_BYTES, not the ~2MB the
        # child actually writes.
        import sys
        from optarena._cases._sandbox import _MAX_CAPTURE_BYTES
        proc = run_capture(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * (2 * 1024 * 1024))"],
            timeout=30, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertLess(len(proc.stdout), 2 * _MAX_CAPTURE_BYTES)


class WorkspaceQuotaWatchdogTests(unittest.TestCase):
    """P1-07: a Docker bind mount (how /workspace always reaches the
    sandbox) cannot be size-quota'd via Docker's own --storage-opt at all -
    confirmed by reading the actual DockerSandbox.start()/
    _run_check_command_docker container-run args, which have no such flag
    for the bind-mounted path. This is the host-side polling fallback:
    real filesystem growth, real threads - not mocked I/O - the same
    live-verification discipline the rest of this project's security tests
    already use."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_quotawatch_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_workspace_usage_counts_real_files(self):
        """P1-09: byte totals come from regular files only; the entry count
        includes the one directory too (`sub`) - 2 root files + 1
        directory + 1 nested file = 4, not 3."""
        from optarena._cases._sandbox import _workspace_usage
        (self.ws / "a.txt").write_bytes(b"x" * 100)
        (self.ws / "b.txt").write_bytes(b"y" * 250)
        sub = self.ws / "sub"
        sub.mkdir()
        (sub / "c.txt").write_bytes(b"z" * 50)
        total, count = _workspace_usage(self.ws)
        self.assertEqual(total, 400)
        self.assertEqual(count, 4)

    def test_workspace_usage_counts_empty_directories(self):
        """P1-09: reproduces the review's own live finding - creating N
        empty directories used to report (0 bytes, 0 files), a real bypass
        of the file/inode-count quota. Directories now count toward the
        entry total even with zero file content."""
        from optarena._cases._sandbox import _workspace_usage
        for i in range(20):
            (self.ws / f"dir{i}").mkdir()
        total, count = _workspace_usage(self.ws)
        self.assertEqual(total, 0)          # no file bytes - correct, directories carry none
        self.assertEqual(count, 20)         # but they must not be invisible to the entry count

    def test_workspace_usage_skips_symlinks(self):
        target = Path(tempfile.mkdtemp(prefix="optarena_test_quotawatch_target_"))
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        (target / "big.bin").write_bytes(b"x" * 10_000)
        try:
            (self.ws / "link").symlink_to(target / "big.bin")
        except OSError:
            self.skipTest("host does not permit symlink creation "
                          "(needs elevation/Developer Mode on Windows)")
        from optarena._cases._sandbox import _workspace_usage
        total, count = _workspace_usage(self.ws)
        self.assertEqual((total, count), (0, 0), "a symlink's target must not count against the quota")

    def test_watchdog_triggers_on_byte_quota(self):
        from optarena._cases._sandbox import _WorkspaceQuotaWatchdog
        triggered = []
        watchdog = _WorkspaceQuotaWatchdog(
            self.ws, on_exceeded=triggered.append, max_bytes=500, max_files=10_000, interval=0.05)
        watchdog.start()
        try:
            (self.ws / "big.bin").write_bytes(b"x" * 1000)
            for _ in range(100):
                if triggered:
                    break
                time.sleep(0.05)
        finally:
            watchdog.stop()
        self.assertEqual(len(triggered), 1)
        self.assertIn("byte", triggered[0])
        self.assertIsNotNone(watchdog.triggered_reason)

    def test_check_final_catches_a_fast_writer_the_poll_would_miss(self):
        """P1-09: the review's own live reproduction - a poll interval long
        enough that the background thread never gets a chance to fire
        before the caller stops the watchdog (matching a command that
        writes past the quota and exits within one poll tick). Without
        `check_final()`, this reports no violation at all."""
        from optarena._cases._sandbox import _WorkspaceQuotaWatchdog
        watchdog = _WorkspaceQuotaWatchdog(
            self.ws, on_exceeded=lambda _r: None, max_bytes=500, max_files=10_000, interval=10.0)
        watchdog.start()
        (self.ws / "big.bin").write_bytes(b"x" * 1000)   # over quota, immediately
        watchdog.stop()   # stopped well inside the 10s interval - the poll never ran
        self.assertIsNone(watchdog.triggered_reason, "the poll should not have fired yet")
        reason = watchdog.check_final()
        self.assertIsNotNone(reason)
        self.assertIn("byte", reason)
        self.assertEqual(watchdog.triggered_reason, reason)

    def test_check_final_is_a_noop_when_poll_already_triggered(self):
        """check_final() must not overwrite an already-recorded reason or
        call on_exceeded a second time - the wrapped process is already
        gone by the time it runs."""
        from optarena._cases._sandbox import _WorkspaceQuotaWatchdog
        calls = []
        watchdog = _WorkspaceQuotaWatchdog(
            self.ws, on_exceeded=calls.append, max_bytes=500, max_files=10_000, interval=0.05)
        watchdog.start()
        try:
            (self.ws / "big.bin").write_bytes(b"x" * 1000)
            for _ in range(100):
                if watchdog.triggered_reason:
                    break
                time.sleep(0.05)
        finally:
            watchdog.stop()
        first_reason = watchdog.triggered_reason
        self.assertIsNotNone(first_reason)
        self.assertEqual(watchdog.check_final(), first_reason)
        self.assertEqual(len(calls), 1)   # on_exceeded still only ever called once

    def test_check_final_returns_none_when_under_quota(self):
        from optarena._cases._sandbox import _WorkspaceQuotaWatchdog
        watchdog = _WorkspaceQuotaWatchdog(
            self.ws, on_exceeded=lambda _r: None, max_bytes=10_000, max_files=10_000, interval=10.0)
        watchdog.start()
        (self.ws / "small.bin").write_bytes(b"x" * 10)
        watchdog.stop()
        self.assertIsNone(watchdog.check_final())
        self.assertIsNone(watchdog.triggered_reason)

    def test_watchdog_triggers_on_file_count_quota(self):
        from optarena._cases._sandbox import _WorkspaceQuotaWatchdog
        triggered = []
        watchdog = _WorkspaceQuotaWatchdog(
            self.ws, on_exceeded=triggered.append, max_bytes=10**9, max_files=5, interval=0.05)
        watchdog.start()
        try:
            for i in range(10):
                (self.ws / f"f{i}.txt").write_text("x")
            for _ in range(100):
                if triggered:
                    break
                time.sleep(0.05)
        finally:
            watchdog.stop()
        self.assertEqual(len(triggered), 1)
        self.assertIn("file", triggered[0])

    def test_watchdog_never_triggers_when_under_both_quotas(self):
        from optarena._cases._sandbox import _WorkspaceQuotaWatchdog
        triggered = []
        watchdog = _WorkspaceQuotaWatchdog(
            self.ws, on_exceeded=triggered.append, max_bytes=10**9, max_files=10_000, interval=0.05)
        watchdog.start()
        try:
            (self.ws / "small.txt").write_text("just a little content")
            time.sleep(0.3)   # several poll intervals, ample time to have fired if it were going to
        finally:
            watchdog.stop()
        self.assertEqual(triggered, [])
        self.assertIsNone(watchdog.triggered_reason)

    def test_watchdog_fires_at_most_once(self):
        from optarena._cases._sandbox import _WorkspaceQuotaWatchdog
        triggered = []
        watchdog = _WorkspaceQuotaWatchdog(
            self.ws, on_exceeded=triggered.append, max_bytes=10, max_files=10_000, interval=0.02)
        (self.ws / "big.bin").write_bytes(b"x" * 1000)
        watchdog.start()
        time.sleep(0.3)  # many poll intervals past the first trigger
        watchdog.stop()
        self.assertEqual(len(triggered), 1, "must stop polling after the first trigger, not fire repeatedly")


class WorkspaceQuotaIntegrationTests(unittest.TestCase):
    """P1-07: the actual wiring through run_check_command's host-exec path -
    a real Python subprocess writing real bytes to a real workspace, killed
    by a real watchdog thread, not a mocked simulation of any of that."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_quotaintegration_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_runaway_check_command_is_aborted_for_quota_not_left_to_time_out(self):
        import sys
        from optarena._cases import _sandbox as sandbox_mod
        # A real script file, not an inline `-c` one-liner: `shell=True`
        # dispatches through cmd.exe on native Windows, which doesn't
        # handle an embedded-newline multi-statement -c string the way a
        # POSIX shell would - writing a real .py file sidesteps shell
        # quoting entirely and works identically on every platform.
        script = self.ws / "runaway.py"
        script.write_text(
            "import time\n"
            "f = open('runaway.bin', 'wb')\n"
            "for _ in range(300):\n"
            "    f.write(b'x' * 1024); f.flush(); time.sleep(0.02)\n",
            encoding="utf-8")
        # A command that would otherwise run for a full ~6s, writing 1KB
        # chunks as fast as it can - the watchdog must abort it long before
        # either that artificial runtime or the case's own (generous) timeout.
        cmd = f'{sys.executable} runaway.py'
        with mock.patch.object(sandbox_mod, "_WORKSPACE_MAX_BYTES", 5000), \
             mock.patch.object(sandbox_mod, "_WORKSPACE_QUOTA_POLL_INTERVAL_S", 0.05):
            failures, info = sandbox_mod._run_check_command_local(cmd, self.ws, 30, sandbox_mod._new_oracle_info(cmd))
        self.assertTrue(info.get("workspace_quota_exceeded"))
        self.assertEqual(len(failures), 1)
        self.assertIn("workspace exceeded", failures[0])
        # Aborted well before the command's own artificial ~6s runtime
        # (300 * 0.02s) - proves it was actually killed, not just that the
        # command happened to finish and looked like a quota match by luck.
        self.assertLess(info["duration_s"], 4)

    def test_well_behaved_check_command_is_unaffected(self):
        """No false positives: a normal, small check_command must run to
        completion exactly as before, with no quota fields set."""
        import sys
        from optarena._cases import _sandbox as sandbox_mod
        cmd = f'{sys.executable} -c "print(\'ok\')"'
        failures, info = sandbox_mod._run_check_command_local(cmd, self.ws, 30, sandbox_mod._new_oracle_info(cmd))
        self.assertEqual(failures, [])
        self.assertNotIn("workspace_quota_exceeded", info)
        self.assertEqual(info["exit_code"], 0)


@unittest.skipUnless(os.name != "posix", "Windows Job Object behavior")
class WindowsJobObjectTests(unittest.TestCase):
    """P0-03: _WindowsJob directly, not just through run_capture's
    integration - proves the primitive itself does what its docstring
    claims (real termination, not just 'the test process happened to be
    reaped some other way')."""

    def test_create_and_close(self):
        from optarena._cases._sandbox import _WindowsJob
        job = _WindowsJob()
        self.assertIsNotNone(job.handle)
        job.close()
        self.assertIsNone(job.handle)

    def test_assign_and_terminate_actually_kills_the_process(self):
        import subprocess as sp
        import sys
        from optarena._cases._sandbox import _WindowsJob
        proc = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        job = _WindowsJob()
        try:
            self.assertTrue(job.assign(proc.pid))
            self.assertTrue(job.terminate())
            proc.wait(timeout=5)
            self.assertIsNotNone(proc.returncode)
        finally:
            job.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

    def test_assign_to_already_exited_pid_fails_gracefully(self):
        import subprocess as sp
        import sys
        from optarena._cases._sandbox import _WindowsJob
        proc = sp.Popen([sys.executable, "-c", "pass"])
        proc.wait(timeout=5)
        job = _WindowsJob()
        try:
            self.assertFalse(job.assign(proc.pid))
        finally:
            job.close()

    def test_grandchild_spawned_after_assignment_is_also_a_member(self):
        """The property the whole fix depends on: Windows automatically
        adds a job member's OWN children to the same job - assignment
        doesn't need to happen again for each new descendant."""
        import subprocess as sp
        import sys
        import time
        from optarena._cases._sandbox import _WindowsJob
        tmp = Path(tempfile.mkdtemp(prefix="optarena_test_jobobject_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        sentinel = tmp / "grandchild_ran.txt"
        grandchild = tmp / "grandchild.py"
        grandchild.write_text(
            f"import time\ntime.sleep(3)\nopen(r'{sentinel}', 'w').close()\n",
            encoding="utf-8")
        child = tmp / "child.py"
        child.write_text(
            f"import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, r'{grandchild}'])\n"
            f"time.sleep(30)\n",
            encoding="utf-8")
        proc = sp.Popen([sys.executable, str(child)])
        job = _WindowsJob()
        try:
            self.assertTrue(job.assign(proc.pid))
            time.sleep(0.5)  # let the child actually spawn the grandchild
            self.assertTrue(job.terminate())
            proc.wait(timeout=5)
        finally:
            job.close()
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
        time.sleep(4)
        self.assertFalse(sentinel.exists(),
                         "grandchild survived - it was not a member of the "
                         "job its parent was assigned to")


class WorkspaceCleanupTests(unittest.TestCase):
    """H-11: the mkdtemp workspace a run/verify creates must be removed once
    it's done (never read again), unless the user asks to keep it."""

    def setUp(self):
        self.cases_dir = Path(tempfile.mkdtemp(prefix="optarena_test_wscleanup_"))
        self.addCleanup(shutil.rmtree, self.cases_dir, ignore_errors=True)
        (self.cases_dir / "c1.json").write_text(json.dumps({
            "name": "c1", "prompts": ["do it"],
        }), encoding="utf-8")

    @staticmethod
    def _fake_driver():
        driver = mock.Mock(parallel_safe=False, caches_results=False)
        driver.run_case.return_value = CaseResult(name="c1", passed=True, duration_s=0.1)
        return driver

    def test_run_scenario_removes_its_workspace(self):
        made = Path(tempfile.mkdtemp(prefix="optarena_test_madews_"))
        sc = Scenario(name="x", driver="aider", cases_dir=str(self.cases_dir))
        with mock.patch("optarena.runner._execution.get_driver", return_value=self._fake_driver()), \
             mock.patch("optarena.runner._execution.tempfile.mkdtemp", return_value=str(made)):
            run_scenario(sc)
        self.assertFalse(made.exists(), "run workspace was not cleaned up")

    def test_run_scenario_keeps_workspace_when_requested(self):
        made = Path(tempfile.mkdtemp(prefix="optarena_test_keptws_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(made, ignore_errors=True))
        sc = Scenario(name="x", driver="aider", cases_dir=str(self.cases_dir))
        with mock.patch("optarena.runner._execution.get_driver", return_value=self._fake_driver()), \
             mock.patch("optarena.runner._execution.tempfile.mkdtemp", return_value=str(made)):
            run_scenario(sc, keep_workspace=True)
        self.assertTrue(made.exists(), "workspace should have been kept for debugging")

    def test_run_scenario_does_not_delete_caller_supplied_workspace(self):
        supplied = Path(tempfile.mkdtemp(prefix="optarena_test_suppliedws_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(supplied, ignore_errors=True))
        sc = Scenario(name="x", driver="aider", cases_dir=str(self.cases_dir))
        with mock.patch("optarena.runner._execution.get_driver", return_value=self._fake_driver()):
            run_scenario(sc, workspace_root=supplied)
        self.assertTrue(supplied.exists(),
                        "a caller-supplied workspace must not be deleted by run_scenario")

    def test_verify_cases_removes_its_workspace(self):
        made = Path(tempfile.mkdtemp(prefix="optarena_test_verifyws_"))
        # A case with no variants is skipped, so no oracle/Docker work runs -
        # this isolates the mkdtemp cleanup path.
        with mock.patch("optarena.verify.tempfile.mkdtemp", return_value=str(made)):
            verify_cases([{"name": "novariants", "prompts": ["x"]}])
        self.assertFalse(made.exists(), "verify workspace was not cleaned up")


class TrialMergeTests(unittest.TestCase):
    @staticmethod
    def _result(passed, duration=1.0, error=None):
        return CaseResult(name="c", passed=passed, duration_s=duration,
                          failures=[] if passed else ["nope"], error=error)

    def test_single_trial_unchanged(self):
        r = self._result(True)
        self.assertIs(_merge_trials("c", [r]), r)

    def test_majority_pass(self):
        merged = _merge_trials("c", [self._result(True), self._result(True),
                                     self._result(False)])
        self.assertTrue(merged.passed)
        self.assertEqual(merged.extra["passes"], 2)
        self.assertEqual(merged.extra["trials"], 3)
        self.assertEqual(merged.failures, [])

    def test_majority_fail_keeps_failures(self):
        merged = _merge_trials("c", [self._result(False), self._result(False),
                                     self._result(True)])
        self.assertFalse(merged.passed)
        self.assertEqual(merged.failures, ["nope"])

    def test_cost_and_tokens_summed_not_last_trial_only(self):
        # H-05: 3 trials costing $0.01 each must report $0.03 total, not $0.01
        # (the bug: only the last trial's `extra` survived merging).
        r1, r2, r3 = self._result(True), self._result(True), self._result(True)
        r1.extra = {"cost_usd": 0.01, "prompt_tokens": 100, "completion_tokens": 50}
        r2.extra = {"cost_usd": 0.01, "prompt_tokens": 100, "completion_tokens": 50}
        r3.extra = {"cost_usd": 0.01, "prompt_tokens": 100, "completion_tokens": 50}
        merged = _merge_trials("c", [r1, r2, r3])
        self.assertAlmostEqual(merged.extra["cost_usd"], 0.03)
        self.assertEqual(merged.extra["prompt_tokens"], 300)
        self.assertEqual(merged.extra["completion_tokens"], 150)

    def test_stderr_concatenated_across_trials(self):
        r1, r2 = self._result(False), self._result(True)
        r1.extra = {"stderr": "first failure"}
        r2.extra = {"stderr": "second output"}
        merged = _merge_trials("c", [r1, r2])
        self.assertEqual(merged.extra["stderr"], "first failuresecond output")

    def test_oracle_not_summed_only_surfaced_via_all_trials(self):
        r1, r2 = self._result(True), self._result(True)
        r1.extra = {"oracle": {"exit_code": 0}}
        r2.extra = {"oracle": {"exit_code": 1}}
        merged = _merge_trials("c", [r1, r2])
        self.assertNotIn("oracle", merged.extra)
        self.assertEqual(merged.extra["oracle_all_trials"],
                         [{"exit_code": 0}, {"exit_code": 1}])

    def test_error_only_when_all_trials_error(self):
        merged = _merge_trials("c", [self._result(False, error="boom"),
                                     self._result(True)])
        self.assertIsNone(merged.error)
        merged = _merge_trials("c", [self._result(False, error="boom"),
                                     self._result(False, error="boom")])
        self.assertEqual(merged.error, "boom")

    def test_execution_ok_true_only_when_all_trials_executed_cleanly(self):
        # Phase 2.7: merged execution_ok answers "did every trial run
        # cleanly" - one trial's tool reporting failure must show up even
        # if the majority vote still passed on artifact content.
        clean1, clean2 = self._result(True), self._result(True)
        merged = _merge_trials("c", [clean1, clean2])
        self.assertTrue(merged.execution_ok)

        clean, dirty = self._result(True), self._result(True)
        dirty.execution_ok = False
        merged = _merge_trials("c", [clean, dirty])
        self.assertFalse(merged.execution_ok)


class ExecutionOkTests(unittest.TestCase):
    """
    Phase 2.7: a CLI tool that exits non-zero must not silently produce an
    unqualified PASS just because the workspace happens to satisfy the
    oracle (an earlier prompt succeeded, or the model got lucky). Previously
    only `extra["stderr"]` recorded the non-zero exit; `passed` depended
    solely on the oracle.
    """

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_execok_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    @staticmethod
    def _case():
        return {"name": "c", "prompts": ["do it"],
                "expected_files": [{"path_pattern": "add.py"}]}

    def _write_artifact(self, *_a, **_kw):
        # Simulate the tool writing a passing file DURING its invocation
        # (e.g. an earlier prompt succeeded) before reporting failure via
        # its own exit code - exactly the "lucky partial artifact" scenario.
        (self.ws / "add.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        return mock.Mock(returncode=1, stdout="", stderr="boom: rate limited")

    def test_cli_agent_nonzero_exit_blocks_pass_despite_passing_artifact(self):
        driver = CLIAgentDriver("codex")
        driver._binary = "fake-codex"
        sc = Scenario(name="x", driver="codex", backend=Backend())
        with mock.patch("optarena.drivers.cli_agents.run_capture", side_effect=self._write_artifact):
            result = driver.run_case(self._case(), sc, self.ws)
        # The file WAS written (satisfies expected_files) - only the tool's
        # own exit code is the reason this must not be a PASS.
        self.assertEqual(result.failures, [])
        self.assertFalse(result.execution_ok)
        self.assertFalse(result.passed)
        self.assertIn("boom", result.extra.get("stderr", ""))

    def test_cli_agent_zero_exit_with_passing_artifact_passes(self):
        driver = CLIAgentDriver("codex")
        driver._binary = "fake-codex"
        sc = Scenario(name="x", driver="codex", backend=Backend())

        def write_and_succeed(*_a, **_kw):
            (self.ws / "add.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("optarena.drivers.cli_agents.run_capture", side_effect=write_and_succeed):
            result = driver.run_case(self._case(), sc, self.ws)
        self.assertTrue(result.execution_ok)
        self.assertTrue(result.passed)

    def test_aider_nonzero_exit_blocks_pass_despite_passing_artifact(self):
        driver = AiderDriver()
        driver._aider = "fake-aider"
        sc = Scenario(name="x", driver="aider", backend=Backend())
        with mock.patch("optarena.drivers.aider_cli.run_capture", side_effect=self._write_artifact):
            result = driver.run_case(self._case(), sc, self.ws)
        self.assertEqual(result.failures, [])
        self.assertFalse(result.execution_ok)
        self.assertFalse(result.passed)


class SecretRedactionTests(unittest.TestCase):
    """
    P1-01: a CLI agent's captured stderr (and exception text) is untrusted
    output the tool itself controls - it can echo an environment variable
    it was handed, intentionally (a verbose auth error) or not (a crash
    dump). Canary-secret tests at three layers: the redaction primitives
    themselves, the two CLI drivers that capture raw stderr, and the
    storage layer that persists whatever a driver produced (the blanket
    safety net for a driver that forgets its own redaction).
    """

    CANARY = "sk-CANARY0123456789ABCDEFGHIJKLMNOPQRSTUVWX"  # matches provider-api-key shape

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_secretredact_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    @staticmethod
    def _case():
        return {"name": "c", "prompts": ["do it"], "expected_files": []}

    # ── primitives ───────────────────────────────────────────────────────
    def test_redact_known_secrets_exact_value(self):
        from optarena.security import redact_known_secrets
        text = f"auth failed, tried key={self.CANARY}"
        self.assertNotIn(self.CANARY, redact_known_secrets(text, [self.CANARY]))

    def test_redact_known_secrets_ignores_short_values(self):
        """A short 'secret' (a placeholder default, a single word) is too
        likely to collide with ordinary output to redact blindly."""
        from optarena.security import redact_known_secrets
        self.assertEqual(redact_known_secrets("the cat sat", ["cat"]), "the cat sat")

    def test_redact_secret_patterns_catches_unknown_shaped_secret(self):
        """Defense in depth: a token this call site was never TOLD about
        (not in known_secrets) but that matches a known shape still gets
        redacted."""
        from optarena.security import redact_secret_patterns
        text = f"leaked: {self.CANARY}"
        self.assertNotIn(self.CANARY, redact_secret_patterns(text))

    def test_redact_secrets_recursive_walks_nested_structure(self):
        from optarena.security import redact_secrets_recursive
        data = {"a": [{"b": f"key is {self.CANARY}"}, "clean"], "c": 42, "d": None}
        out = redact_secrets_recursive(data)
        self.assertNotIn(self.CANARY, json.dumps(out))
        self.assertEqual(out["c"], 42)
        self.assertIsNone(out["d"])
        self.assertEqual(out["a"][1], "clean")

    # ── P1-03: full multiline PEM block redaction, not just the header ────
    PEM_BODY = (
        "MIIEowIBAAKCAQEACANARYKEYBODYLINE1abcdefghijklmnopqrstuvwxyz0123\n"
        "CANARYKEYBODYLINE2abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ\n"
        "CANARYKEYBODYLINE3abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ"
    )

    def _pem(self, kind="RSA "):
        return f"-----BEGIN {kind}PRIVATE KEY-----\n{self.PEM_BODY}\n-----END {kind}PRIVATE KEY-----"

    def test_redact_secret_patterns_removes_the_full_pem_body_not_just_header(self):
        """The original bug: only the '-----BEGIN...-----' header line was
        replaced, leaving the base64 key body and footer fully intact and
        reconstructable by anyone who re-prepended a standard header."""
        from optarena.security import redact_secret_patterns
        pem = self._pem()
        out = redact_secret_patterns(f"dumping config:\n{pem}\nend of dump")
        self.assertNotIn("BEGIN RSA PRIVATE KEY", out)
        self.assertNotIn("CANARYKEYBODYLINE1", out)
        self.assertNotIn("CANARYKEYBODYLINE2", out)
        self.assertNotIn("CANARYKEYBODYLINE3", out)
        self.assertNotIn("END RSA PRIVATE KEY", out)
        self.assertIn("dumping config:", out)
        self.assertIn("end of dump", out)

    def test_redact_pem_block_covers_every_documented_key_type(self):
        from optarena.security import redact_secret_patterns
        for kind in ("RSA ", "EC ", "OPENSSH ", "DSA ", "ENCRYPTED ", ""):
            with self.subTest(kind=kind or "generic/PKCS8"):
                out = redact_secret_patterns(self._pem(kind))
                self.assertNotIn("CANARYKEYBODYLINE1", out)
                self.assertNotIn(f"BEGIN {kind}PRIVATE KEY", out)

    def test_redact_pem_block_handles_truncated_key_with_no_end_marker(self):
        """A malformed/truncated block (output cut off mid-capture) must
        still be redacted from BEGIN onward, not left with a live,
        unredacted key body just because the footer never arrived."""
        from optarena.security import redact_secret_patterns
        truncated = f"-----BEGIN RSA PRIVATE KEY-----\n{self.PEM_BODY}"
        out = redact_secret_patterns(truncated)
        self.assertNotIn("CANARYKEYBODYLINE1", out)
        self.assertNotIn("BEGIN RSA PRIVATE KEY", out)

    def test_redact_pem_block_through_the_full_recursive_pipeline(self):
        """End to end through the same call path a saved run record goes
        through - not just the primitive in isolation."""
        from optarena.security import redact_secrets_recursive
        data = {"extra": {"stderr": f"leaked config:\n{self._pem()}"}}
        out = redact_secrets_recursive(data)
        dumped = json.dumps(out)
        self.assertNotIn("CANARYKEYBODYLINE1", dumped)
        self.assertNotIn("CANARYKEYBODYLINE2", dumped)

    def test_scan_text_still_flags_a_private_key_finding_on_the_header_line(self):
        """P1-03's fix must not regress `optarena scan`'s finding-detection
        - scan_text processes a file LINE BY LINE, so the header-only rule
        (not the multiline redaction pattern) is what has to keep matching
        there; confirms the two mechanisms didn't collide."""
        from optarena.security import scan_text
        findings = scan_text(self._pem(), "leaked_key.pem")
        self.assertTrue(any(f["rule"] == "private-key" for f in findings))
        # P1-03: the persisted FINDING itself must not carry the key body
        # either - _redacted_snippet only ever kept the matched line (the
        # header), never the multiline body, so this was already safe; a
        # regression test in case that ever changes.
        self.assertFalse(any("CANARYKEYBODYLINE1" in f["snippet"] for f in findings))

    # ── CLI agent driver: non-zero exit (stderr) ────────────────────────
    def test_cli_agent_stderr_canary_redacted(self):
        driver = CLIAgentDriver("codex")
        driver._binary = "fake-codex"
        sc = Scenario(name="x", driver="codex",
                      backend=Backend(api_key=self.CANARY))
        fake = mock.Mock(returncode=1, stdout="", stderr=f"auth error: {self.CANARY}")
        with mock.patch("optarena.drivers.cli_agents.run_capture", return_value=fake):
            result = driver.run_case(self._case(), sc, self.ws)
        self.assertNotIn(self.CANARY, result.extra.get("stderr", ""))
        self.assertNotIn(self.CANARY, json.dumps(result.to_dict()))

    def test_aider_stderr_canary_redacted(self):
        driver = AiderDriver()
        driver._aider = "fake-aider"
        sc = Scenario(name="x", driver="aider",
                      backend=Backend(api_key=self.CANARY))
        fake = mock.Mock(returncode=1, stdout="", stderr=f"auth error: {self.CANARY}")
        with mock.patch("optarena.drivers.aider_cli.run_capture", return_value=fake):
            result = driver.run_case(self._case(), sc, self.ws)
        self.assertNotIn(self.CANARY, result.extra.get("stderr", ""))
        self.assertNotIn(self.CANARY, json.dumps(result.to_dict()))

    # ── exception path ───────────────────────────────────────────────────
    def test_cli_agent_exception_message_canary_redacted(self):
        driver = CLIAgentDriver("codex")
        driver._binary = "fake-codex"
        sc = Scenario(name="x", driver="codex",
                      backend=Backend(api_key=self.CANARY))

        def _raise(*_a, **_kw):
            raise RuntimeError(f"connection failed for key {self.CANARY}")

        with mock.patch("optarena.drivers.cli_agents.run_capture", side_effect=_raise):
            result = driver.run_case(self._case(), sc, self.ws)
        self.assertIsNotNone(result.error)
        self.assertNotIn(self.CANARY, result.error)

    def test_aider_exception_message_canary_redacted(self):
        driver = AiderDriver()
        driver._aider = "fake-aider"
        sc = Scenario(name="x", driver="aider",
                      backend=Backend(api_key=self.CANARY))

        def _raise(*_a, **_kw):
            raise RuntimeError(f"connection failed for key {self.CANARY}")

        with mock.patch("optarena.drivers.aider_cli.run_capture", side_effect=_raise):
            result = driver.run_case(self._case(), sc, self.ws)
        self.assertIsNotNone(result.error)
        self.assertNotIn(self.CANARY, result.error)

    # ── timeout path ─────────────────────────────────────────────────────
    def test_cli_agent_timeout_never_surfaces_canary(self):
        """A timeout's partial captured output never makes it into the
        result at all today - this pins that behavior explicitly rather
        than leaving it as an untested assumption."""
        import subprocess as sp
        driver = CLIAgentDriver("codex")
        driver._binary = "fake-codex"
        sc = Scenario(name="x", driver="codex",
                      backend=Backend(api_key=self.CANARY))

        def _timeout(*_a, **_kw):
            raise sp.TimeoutExpired("fake-codex", 1, output="", stderr=self.CANARY)

        with mock.patch("optarena.drivers.cli_agents.run_capture", side_effect=_timeout):
            result = driver.run_case(self._case(), sc, self.ws)
        self.assertNotIn(self.CANARY, json.dumps(result.to_dict()))

    # ── storage layer: the blanket safety net ───────────────────────────
    def test_store_save_run_redacts_pattern_secret_in_case_extra(self):
        """Simulates a driver that (for whatever reason - a bug, a future
        driver that forgot) let a pattern-shaped secret through to a
        CaseResult - the storage layer must still catch it before it
        reaches disk, since that's what the dashboard reads from."""
        from optarena import store
        from optarena.runner import RunRecord
        d = Path(tempfile.mkdtemp(prefix="optarena_test_storeredact_"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        with mock.patch.object(store, "RESULTS_DIR", d), \
             mock.patch.object(store, "RUNS_DIR", d / "runs"):
            rec = RunRecord(
                run_id="test-run-1",
                scenario={"name": "x", "driver": "codex",
                          "backend": {"api_key": None}},
                started_at="2026-01-01T00:00:00",
                cases=[{"name": "c", "passed": False,
                       "extra": {"stderr": f"boom: {self.CANARY}"}}],
                status="completed",
            )
            path = store.save_run(rec)
            on_disk = path.read_text(encoding="utf-8")
        self.assertNotIn(self.CANARY, on_disk)
        self.assertIn("redacted", on_disk)  # json.dumps escapes the marker's non-ASCII quotes by default

    # ── retroactive scrub of already-saved runs ─────────────────────────
    def test_runs_scrub_secrets_catches_pattern_secret_not_just_api_key(self):
        from optarena import cli, store
        d = Path(tempfile.mkdtemp(prefix="optarena_test_scrubredact_"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        runs_dir = d / "runs"
        runs_dir.mkdir(parents=True)
        rec = {
            "run_id": "old-run", "scenario": {"backend": {"api_key": None}},
            "cases": [{"name": "c", "extra": {"stderr": f"boom: {self.CANARY}"}}],
        }
        (runs_dir / "old-run.json").write_text(json.dumps(rec), encoding="utf-8")
        with mock.patch.object(store, "RUNS_DIR", runs_dir), \
             mock.patch.object(store, "rebuild_index"):
            args = argparse.Namespace(yes=True)
            rc = cli.cmd_runs_scrub_secrets(args)
        self.assertEqual(rc, 0)
        on_disk = (runs_dir / "old-run.json").read_text(encoding="utf-8")
        self.assertNotIn(self.CANARY, on_disk)


class DynamicDriverIntegrationTests(unittest.TestCase):
    """End-to-end (mocked-subprocess) check that a driver's per-prompt loop
    actually wires up the dynamic-eval engine: fires disruptions between
    prompts, records them on the per-step trajectory, and only pays for the
    precise (check_command-backed) per-step oracle on cases that declare
    ``disruptions`` - not on the rest of the corpus."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_dyn_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    @staticmethod
    def _disruption_case():
        return {
            "name": "dyn", "prompts": ["p1", "p2"],
            "setup_files": {"config.txt": "10\n"},
            "disruptions": [{"after_prompt": 1, "description": "cfg changed",
                              "write_files": {"config.txt": "30\n"}}],
            "expected_files": [{"path_pattern": "out.txt", "content_patterns": ["ok"]}],
        }

    def test_cli_agent_fires_disruption_and_records_precise_steps(self):
        driver = CLIAgentDriver("codex")
        driver._binary = "fake-codex"
        sc = Scenario(name="x", driver="codex", backend=Backend())

        def fake_run(*_a, **_kw):
            (self.ws / "out.txt").write_text("ok\n", encoding="utf-8")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("optarena.drivers.cli_agents.run_capture", side_effect=fake_run):
            result = driver.run_case(self._disruption_case(), sc, self.ws)

        steps = result.extra["steps"]
        self.assertEqual(len(steps), 2)
        # The disruption's fixed trigger (after_prompt=1) fires on step 1 only.
        self.assertEqual(steps[0]["disrupted"], ["cfg changed"])
        self.assertNotIn("disrupted", steps[1])
        self.assertEqual((self.ws / "config.txt").read_text(), "30\n")
        # Precise per-step attribution (oracle_ok) is present because this
        # case declares disruptions - both prompts satisfy expected_files here.
        self.assertTrue(steps[0]["oracle_ok"])
        self.assertTrue(steps[1]["oracle_ok"])
        self.assertTrue(result.passed)

    def test_cli_agent_omits_oracle_ok_for_non_disruption_cases(self):
        # Running the real oracle after every prompt is only worth the extra
        # Docker execs for the small, deliberate subset of cases with
        # `disruptions` - everything else keeps the cheap expected_ok-only path.
        driver = CLIAgentDriver("codex")
        driver._binary = "fake-codex"
        sc = Scenario(name="x", driver="codex", backend=Backend())
        case = {"name": "c", "prompts": ["p1", "p2"],
                "expected_files": [{"path_pattern": "out.txt"}]}

        def fake_run(*_a, **_kw):
            (self.ws / "out.txt").write_text("x\n", encoding="utf-8")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("optarena.drivers.cli_agents.run_capture", side_effect=fake_run):
            result = driver.run_case(case, sc, self.ws)
        for st in result.extra["steps"]:
            self.assertNotIn("oracle_ok", st)
            self.assertIn("expected_ok", st)

    def test_precise_step_check_does_not_leak_hidden_tests_into_live_workspace(self):
        # Regression: the precise per-step oracle check (Tier 3) runs BETWEEN
        # prompts, in the SAME workspace the next prompt's agent invocation
        # gets as its cwd. If that check wrote test_setup_files straight into
        # the live workspace (evaluate_case's normal behavior), a real CLI
        # agent could list/read them on its next turn and discover exactly
        # what its hidden test expects - defeating "never seen by the model".
        # evaluate_case_isolated must grade a private copy instead, leaving
        # the live workspace exactly as the driver/agent left it.
        driver = CLIAgentDriver("codex")
        driver._binary = "fake-codex"
        sc = Scenario(name="x", driver="codex", backend=Backend())
        case = {
            "name": "dyn", "prompts": ["p1", "p2"],
            "disruptions": [{"after_prompt": 1, "write_files": {"x.txt": "y\n"}}],
            "expected_files": [{"path_pattern": "out.txt"}],
            "test_setup_files": {"hidden_test.txt": "secret expected value\n"},
        }

        def fake_run(*_a, **_kw):
            (self.ws / "out.txt").write_text("x\n", encoding="utf-8")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("optarena.drivers.cli_agents.run_capture", side_effect=fake_run):
            driver.run_case(case, sc, self.ws)
        # After the WHOLE case finishes, the hidden test file is written once
        # by the final evaluate_case call (expected, no more prompts follow)
        # - but it must never have been visible mid-session. We can't observe
        # "mid-session" directly here, so assert the stronger invariant: no
        # stray `.optarena-verify-*` directory (the isolation copy) was left
        # behind, and only ONE copy of hidden_test.txt exists (from the final
        # call), not artifacts from a leaked intermediate write into a place
        # the next prompt's subprocess would also see.
        leaked_verify_dirs = list(self.ws.parent.glob(".optarena-verify-*"))
        self.assertEqual(leaked_verify_dirs, [], "isolation copy must be cleaned up")

    def test_evaluate_case_isolated_does_not_write_into_live_root(self):
        from optarena.cases import evaluate_case_isolated
        case = {
            "expected_files": [{"path_pattern": "out.txt", "content_patterns": ["ok"]}],
            "test_setup_files": {"hidden.txt": "shh\n"},
        }
        with tempfile.TemporaryDirectory() as d:
            live_root = Path(d) / "live"
            live_root.mkdir()
            (live_root / "out.txt").write_text("ok\n", encoding="utf-8")
            failures, _info = evaluate_case_isolated(case, ["out.txt"], live_root)
            self.assertEqual(failures, [])
            # The hidden test file must NOT appear in the live workspace -
            # only in the (already-cleaned-up) private copy.
            self.assertFalse((live_root / "hidden.txt").exists())
            # And the private copy must have been cleaned up, not left behind.
            leaked = list(live_root.parent.glob(".optarena-verify-*"))
            self.assertEqual(leaked, [])


class StabilityMetricsTests(unittest.TestCase):
    """--trials stability surfaced in aggregate/compare, not hidden behind the majority verdict."""

    @staticmethod
    def _case(name, passed, trials=None, passes=None, duration=1.0):
        extra = {}
        if trials:
            extra = {"trials": trials, "passes": passes}
        return {"name": name, "passed": passed, "duration_s": duration,
                "files": [], "failures": [], "extra": extra}

    def test_aggregate_counts_flaky_cases(self):
        cases = [
            self._case("a", True, trials=3, passes=3),   # unanimous pass
            self._case("b", True, trials=3, passes=2),   # flaky
            self._case("c", False, trials=3, passes=1),  # flaky
            self._case("d", False),                      # single trial
        ]
        self.assertEqual(aggregate(cases)["flaky_cases"], 2)

    def test_aggregate_percentiles(self):
        cases = [self._case(str(i), True, duration=float(i)) for i in range(1, 11)]
        s = aggregate(cases)
        self.assertEqual(s["p95_duration_s"], 10.0)
        one = aggregate([self._case("x", True, duration=7.0)])
        self.assertEqual(one["p95_duration_s"], 7.0)

    def test_case_deltas_carry_trials_marker(self):
        run_a = {"cases": [self._case("x", True, trials=3, passes=2)]}
        run_b = {"cases": [self._case("x", True)]}
        row = case_deltas(run_a, run_b)[0]
        self.assertEqual(row["a_trials"], "2/3")
        self.assertIsNone(row["b_trials"])

    def test_regression_summary_lists_flaky_cases(self):
        run = lambda name, passes: {  # noqa: E731
            "run_id": name, "scenario": {"name": name},
            "cases": [self._case("x", passes >= 2, trials=3, passes=passes)],
            "summary": {"cases": 1, "passed": 1, "failed": 0, "pass_rate": 1.0,
                        "mean_duration_s": 1.0},
        }
        summary = regression_summary(compare_runs(run("a", 3), run("b", 2)))
        self.assertEqual(summary["flaky_cases"], ["x"])


class TelemetryParsingTests(unittest.TestCase):
    """Real token/cost/turn metrics for aider and claude-code, not duration-only."""

    def test_aider_metrics_k_suffix_and_session_cost(self):
        out = ("Aider v0.86\n"
               "Tokens: 4.5k sent, 431 received. Cost: $0.0042 message, $0.0084 session.\n"
               "Applied edit to utils.py\n"
               "Tokens: 6.2k sent, 1.1k received. Cost: $0.006 message, $0.0144 session.\n")
        m = parse_aider_metrics(out)
        self.assertEqual(m["prompt_tokens"], 10700)
        self.assertEqual(m["completion_tokens"], 1531)
        self.assertAlmostEqual(m["cost_usd"], 0.0144)

    def test_aider_metrics_comma_format_no_cost(self):
        m = parse_aider_metrics("Tokens: 8,975 sent, 431 received.\n")
        self.assertEqual(m["prompt_tokens"], 8975)
        self.assertEqual(m["completion_tokens"], 431)
        self.assertNotIn("cost_usd", m)   # absent stays absent, never fabricated

    def test_aider_metrics_empty_on_quiet_output(self):
        self.assertEqual(parse_aider_metrics("no usage lines here"), {})

    def test_claude_json_metrics(self):
        payload = json.dumps({
            "type": "result", "subtype": "success", "num_turns": 4,
            "total_cost_usd": 0.0731,
            "usage": {"input_tokens": 12, "output_tokens": 345,
                      "cache_creation_input_tokens": 1000,
                      "cache_read_input_tokens": 5000},
            "result": "done",
        })
        m = parse_claude_json_metrics("some banner\n" + payload + "\n")
        self.assertEqual(m["prompt_tokens"], 6012)
        self.assertEqual(m["completion_tokens"], 345)
        self.assertEqual(m["cache_read_tokens"], 5000)
        self.assertEqual(m["turns"], 4)
        self.assertAlmostEqual(m["cost_usd"], 0.0731)

    def test_claude_json_metrics_empty_on_non_json(self):
        self.assertEqual(parse_claude_json_metrics("plain text output"), {})

    def test_gemini_json_metrics_aggregate_model_usage(self):
        payload = json.dumps({
            "response": "done",
            "stats": {"models": {
                "gemini-2.5-pro": {
                    "tokens": {"prompt": 120, "candidates": 45, "cached": 10,
                               "thoughts": 20, "tool": 5},
                },
                "gemini-2.5-flash": {
                    "tokens": {"prompt": 30, "candidates": 8, "cached": 2},
                },
            }},
        })
        metrics = parse_gemini_json_metrics("banner\n" + payload + "\n")
        self.assertEqual(metrics["prompt_tokens"], 150)
        self.assertEqual(metrics["completion_tokens"], 53)
        self.assertEqual(metrics["cache_read_tokens"], 12)
        self.assertNotIn("cost_usd", metrics)

    def test_gemini_json_metrics_empty_on_non_json(self):
        self.assertEqual(parse_gemini_json_metrics("plain text output"), {})


class GeminiDriverTests(unittest.TestCase):
    def test_gemini_registry_and_invocation(self):
        from optarena.drivers import DRIVERS
        from optarena.drivers.cli_agents import CLI_AGENTS

        self.assertIn("gemini-cli", CLI_AGENTS)
        self.assertEqual(CLI_AGENTS["gemini-cli"]["binaries"], ["gemini"])
        self.assertEqual(DRIVERS["gemini-cli"]["backend"], "fixed")
        backend = Backend(model="gemini-2.5-pro")
        argv = CLI_AGENTS["gemini-cli"]["argv"]("fix this file", backend)
        self.assertEqual(argv, ["-p", "fix this file", "--output-format", "json",
                                "--approval-mode", "yolo", "--model", "gemini-2.5-pro"])

    def test_gemini_auth_passthrough_does_not_leak_host_environment(self):
        captured = {}

        def fake_run(command, **kwargs):
            captured["command"] = command
            captured["env"] = kwargs["env"]
            return mock.Mock(returncode=0, stdout="{}\n", stderr="")

        driver = CLIAgentDriver("gemini-cli")
        driver._binary = "gemini"
        scenario = Scenario(name="s", driver="gemini-cli", backend=Backend())
        case = {"name": "c", "prompts": ["do it"], "expected_files": []}
        auth = {"GEMINI_API_KEY": "gemini-secret", "AWS_SECRET_ACCESS_KEY": "host-secret"}
        workspace = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, workspace, ignore_errors=True)
        with mock.patch.dict(os.environ, auth), \
             mock.patch("optarena.drivers.cli_agents.run_capture", side_effect=fake_run):
            driver.run_case(case, scenario, workspace)
        self.assertEqual(captured["env"]["GEMINI_API_KEY"], "gemini-secret")
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", captured["env"])
        self.assertNotIn("gemini-secret", captured["command"])


class VerifyCorpusTests(unittest.TestCase):
    """verify-corpus: reference must pass the real oracle, broken must fail."""

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"OPTARENA_DISABLE_SANDBOX": "1"})
        self._env.start()
        self.addCleanup(self._env.stop)

    CASE = {
        "name": "verify_demo",
        "task_type": "bug_fix",
        "prompts": ["fix add"],
        "setup_files": {"add.py": "def add(a, b):\n    return a - b\n"},
        "expected_files": [{"path_pattern": "add.py", "content_patterns": ["def add"]}],
        "test_setup_files": {"test_add.py": "import add\nassert add.add(2, 3) == 5\nprint('PASS')\n"},
        "check_command": f'"{sys.executable}" test_add.py',
        "reference_solution": {"add.py": "def add(a, b):\n    return a + b\n"},
        "broken_solutions": [
            {"name": "still-subtracts", "files": {"add.py": "def add(a, b):\n    return a - b - 0\n"}},
        ],
    }

    def test_variants_include_reference_broken_and_unmodified(self):
        names = [n for n, _f, _p in variants_for(self.CASE)]
        self.assertEqual(names, ["reference", "still-subtracts", "unmodified"])

    def test_setup_repo_only_case_still_gets_unmodified_variant(self):
        # An L3 case whose starting state is entirely the shared starter repo
        # (no setup_files overlay) must still run the unmodified-must-fail check.
        case = {"name": "l3", "task_type": "security", "setup_repo": "fastapi-tasktracker"}
        names = [n for n, _f, _p in variants_for(case)]
        self.assertEqual(names, ["unmodified"])

    def test_good_case_verifies_clean(self):
        violations, checked, skipped = verify_cases([dict(self.CASE)])
        self.assertEqual(violations, [])
        self.assertEqual(checked, 3)
        self.assertEqual(skipped, 0)

    def test_broken_oracle_is_reported(self):
        # An oracle that can't fail (no real assertion) must be flagged by the
        # broken variant "passing" it. (The unmodified variant still fails
        # honestly - it changed no files, so the expected-file check trips.)
        bad = dict(self.CASE)
        bad["test_setup_files"] = {"test_add.py": "print('PASS')\n"}
        violations, _checked, _skipped = verify_cases([bad])
        self.assertEqual(len(violations), 1)
        self.assertIn("still-subtracts", violations[0])
        self.assertIn("must fail", violations[0])

    def test_failing_reference_is_reported(self):
        bad = dict(self.CASE)
        bad["reference_solution"] = {"add.py": "def add(a, b):\n    return a * b\n"}
        violations, _checked, _skipped = verify_cases([bad])
        self.assertTrue(any("reference solution FAILED" in v for v in violations))

    def test_case_without_variants_is_skipped(self):
        plain = {"name": "plain", "task_type": "feature", "prompts": ["x"],
                 "expected_files": []}
        violations, checked, skipped = verify_cases([plain])
        self.assertEqual((violations, checked, skipped), ([], 0, 1))


class VerifyCorpusNonRootPermissionsTests(unittest.TestCase):
    """
    Two real bugs lived here, found in two passes against the same live
    non-root CI job (sandbox-nonroot: `cases verify --language shell` under
    OPTARENA_SANDBOX_USER). Neither reproduced on Windows (Docker Desktop's
    bind-mount layer ignores Unix permission bits), so neither could be
    caught by local testing - only by reasoning through the actual directory
    nesting and, for the second, by reading a real CI failure log.

    Bug 1 (READ, fully fixed): verify_cases() nests each variant two levels
    under its mkdtemp root (root/case_name/variant_name), but
    relax_workspace_permissions() was only ever called on the leaf variant
    directory. POSIX requires execute (traversal) permission on EVERY
    directory in a path, so the still-0700 root and case_name directories
    blocked access regardless of how open the leaf was - 100% of shell cases
    failed identically with "Permission denied" reading hidden test files.
    Fixed by relaxing the whole chain (root, case_dir, leaf).

    Bug 2 (WRITE, found in the very next real CI run after Bug 1's fix
    shipped): relaxing the chain happens BEFORE prepare_workspace() and
    write_setup_files() populate the workspace, and relax_workspace_
    permissions() was a single non-recursive chmod - so it never touched
    content created afterward (a setup_repo fixture's copied subdirectories,
    an existing setup_files script later overwritten by a mutation-check
    script). Reading was fixed (644 files are world-readable through an
    already-traversable chain); WRITING into that later content was not.
    Fixed by making relax_workspace_permissions recursive AND adding a
    second call after the workspace is fully populated, right before
    evaluate_case() grades it.
    """

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"OPTARENA_DISABLE_SANDBOX": "1"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def test_relax_called_on_root_case_dir_and_leaf_not_just_leaf(self):
        case = dict(VerifyCorpusTests.CASE)
        relaxed: list[Path] = []
        with mock.patch("optarena.verify.relax_workspace_permissions",
                        side_effect=lambda p: relaxed.append(p)):
            violations, checked, _skipped = verify_cases([case])
        self.assertEqual(violations, [])
        self.assertEqual(checked, 3)
        # One call for the shared mkdtemp root, plus 3 per variant (parent
        # and leaf before content exists, leaf again after) for each of the
        # 3 variants (reference, still-subtracts, unmodified).
        self.assertEqual(len(relaxed), 1 + 3 * 3)
        for path in relaxed:
            self.assertIsInstance(path, Path)
        # The case-level intermediate directory must be among the relaxed
        # paths, not just the mkdtemp root and the per-variant leaves - this
        # is exactly the directory Bug 1 left at 0700.
        case_dirs = {p for p in relaxed if p.name == case["name"]}
        self.assertTrue(case_dirs, "root/case_name was never relaxed - "
                        "would still block traversal for a non-root container user")
        # Bug 2: the leaf must be relaxed at least twice - once before
        # content exists (traversal) and once after (so newly-written
        # content is actually writable by the non-root container, not just
        # the directory that contains it).
        leaf_dirs = [p for p in relaxed if p.name in
                     ("reference", "still-subtracts", "unmodified")]
        from collections import Counter
        leaf_counts = Counter(leaf_dirs)
        self.assertTrue(all(c >= 2 for c in leaf_counts.values()),
                        f"expected every leaf relaxed at least twice (before "
                        f"and after content), got {leaf_counts}")

    def test_relax_workspace_permissions_is_recursive(self):
        # Bug 2 directly: a non-recursive chmod on the top directory alone
        # must not be mistaken for "the whole tree is relaxed" ever again.
        import optarena.cases as cases_mod
        tmp = Path(tempfile.mkdtemp(prefix="optarena_test_relaxrecur_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        nested = tmp / "sub" / "deeper"
        nested.mkdir(parents=True)
        leaf_file = nested / "file.txt"
        leaf_file.write_text("x", encoding="utf-8")
        with mock.patch.dict(os.environ, {"OPTARENA_SANDBOX_USER": "1000:1000"}):
            if os.name == "posix":
                for p in (tmp, tmp / "sub", nested, leaf_file):
                    p.chmod(0o700)
                cases_mod.relax_workspace_permissions(tmp)
                for p in (tmp, tmp / "sub", nested, leaf_file):
                    mode = p.stat().st_mode & 0o777
                    self.assertEqual(mode, 0o777, f"{p} was not relaxed recursively")
            else:
                # Windows: must still no-op cleanly, not raise.
                cases_mod.relax_workspace_permissions(tmp)


class VerifyCorpusSandboxConcurrencyTests(unittest.TestCase):
    """Regression test for a real bug found running `cases verify` against
    real Docker: verify_cases() used to start a sandbox for EVERY distinct
    image the corpus needs (9, one per language) simultaneously - each
    `--memory 2g --cpus 2` - and keep them all alive for the whole run, even
    though only one is ever used at a time. That reliably destabilized CI's
    2-vCPU/7GB runners (confirmed live: the exact cases that failed with
    "Permission denied"/"No such container" under the full 9-image run
    passed cleanly re-run in isolation). Cases are now grouped by image and
    processed with one sandbox alive at a time - this pins that invariant
    with a fast, fully-mocked test (no real Docker needed) so a future
    "optimization" can't silently reintroduce the concurrency."""

    def test_never_more_than_one_sandbox_active_at_once(self):
        events: list[tuple[str, str]] = []

        class FakeSandbox:
            def __init__(self, root, image=None):
                self.image = image

            def start(self):
                events.append(("start", self.image))

            def stop(self):
                events.append(("stop", self.image))

        # "img-a" appears twice (two cases sharing an image) - must still
        # get ONE sandbox, not one per case.
        cases = [
            {"name": f"c{i}", "task_type": "feature", "check_command": "echo hi",
             "image": img, "reference_solution": {"f.txt": "x"}}
            for i, img in enumerate(["img-b", "img-a", "img-c", "img-a"])
        ]

        with mock.patch("optarena.verify.DockerSandbox", FakeSandbox), \
             mock.patch("optarena.verify._run_variant", return_value=[]):
            from optarena.verify import verify_cases as _verify_cases
            _verify_cases(cases)

        active = 0
        max_active = 0
        for kind, _image in events:
            active += 1 if kind == "start" else -1
            max_active = max(max_active, active)
        self.assertLessEqual(max_active, 1, f"events: {events}")

        starts = [image for kind, image in events if kind == "start"]
        self.assertEqual(starts, sorted({"img-a", "img-b", "img-c"}),
                         "each image must start exactly once, not once per case")


class SandboxHardeningCapabilitiesTests(unittest.TestCase):
    """Regression test for a real bug found running `cases verify` against
    real Linux Docker (never reproduced under Windows/macOS Docker Desktop,
    whose bind-mount translation layer ignores real Unix permission bits -
    exactly why it went unnoticed through months of local testing): the
    sandbox's workspace is a HOST directory bind-mounted as /workspace, and
    `--cap-drop ALL` on its own strips CAP_DAC_OVERRIDE from the container's
    root process - so once host-side and container-side permission bits
    don't line up (routine, since new case/variant directories keep getting
    created under the runner's own uid throughout the run), root can no
    longer read/write files on its own bind mount. That produced exactly
    "Permission denied" / "could not find Cargo.toml" (a blocked directory
    traversal looks identical to "not there" to a tool doing its own upward
    search) - confirmed live against two real corpus cases under WSL2 and
    reproduced again under real GitHub Actions CI. `--cap-add DAC_OVERRIDE`
    restores root's normal, pre-hardening bind-mount behavior while every
    other capability - including anything that could matter for container
    escape or host interaction - stays dropped. This pins that pairing so a
    future "let's tighten this further" can't silently reintroduce the bug."""

    def test_dac_override_restored_alongside_cap_drop_all(self):
        self.assertIn("--cap-drop", _HARDENING_ARGS)
        self.assertEqual(_HARDENING_ARGS[_HARDENING_ARGS.index("--cap-drop") + 1], "ALL")
        self.assertIn("--cap-add", _HARDENING_ARGS)
        self.assertEqual(_HARDENING_ARGS[_HARDENING_ARGS.index("--cap-add") + 1], "DAC_OVERRIDE")


class SafeRunNameTests(unittest.TestCase):
    """Model-derived scenario names must survive becoming filenames."""

    def test_openrouter_slash_and_ollama_colon_squashed(self):
        self.assertEqual(safe_run_name("ollama-chat-qwen/qwen-2.5"), "ollama-chat-qwen-qwen-2.5")
        self.assertEqual(safe_run_name("ollama-chat-qwen3-coder:30b"), "ollama-chat-qwen3-coder-30b")

    def test_plain_names_unchanged(self):
        self.assertEqual(safe_run_name("aider-baseline"), "aider-baseline")
        self.assertEqual(safe_run_name("baseline_v1.2+rc"), "baseline_v1.2+rc")


class ConcreteTargetTests(unittest.TestCase):
    """Baseline drivers write to the first expected path themselves - glob
    patterns must become concrete, writable, cross-platform paths (a literal
    `**` directory is ugly on POSIX and an outright crash on Windows)."""

    def test_literal_paths_pass_through(self):
        self.assertEqual(concrete_target("factorial.py"), Path("factorial.py"))
        self.assertEqual(concrete_target("src/main.rs"), Path("src/main.rs"))

    def test_glob_dirs_dropped_and_glob_names_filled(self):
        self.assertEqual(concrete_target("**/HealthController.java"), Path("HealthController.java"))
        self.assertEqual(concrete_target("*_test.go"), Path("output_test.go"))
        self.assertEqual(concrete_target("*.tf"), Path("output.tf"))

    def test_filled_name_still_matches_the_original_glob(self):
        import fnmatch
        for pattern in ("*_test.go", "*.tf"):
            self.assertTrue(fnmatch.fnmatch(concrete_target(pattern).name, pattern))

    def test_none_defaults(self):
        self.assertEqual(concrete_target(None), Path("output.txt"))

    def test_traversal_and_git_paths_rejected(self):
        """P0-02: write-site defense in depth. schema.validate_case is the
        primary gate, but concrete_target is what both openai_chat.run_case
        and sdk_base.run_case actually build a write path from - a '..' or
        '.git' segment has no glob metacharacter and would otherwise survive
        the basename-stripping below untouched."""
        for pattern in ("../outside.py", "a/../../outside.py", "/etc/passwd",
                        ".git/config", "a/.git/hooks/pre-commit"):
            with self.assertRaises(ValueError):
                concrete_target(pattern)


class WriteSetupFilesGitPathTests(unittest.TestCase):
    """P0-01 runtime layer: write_setup_files is the actual write site
    behind setup_files, test_setup_files, and disruption write_files - this
    is the last point that can still refuse a .git/.. path if a caller ever
    reaches it with content that bypassed schema.validate_case."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="optarena_test_gitpath_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_git_config_write_rejected(self):
        with self.assertRaises(ValueError):
            write_setup_files(self.root, {".git/config": "[core]\n"})
        self.assertFalse((self.root / ".git").exists())

    def test_traversal_write_rejected(self):
        with self.assertRaises(ValueError):
            write_setup_files(self.root, {"../outside.py": "x"})

    def test_ordinary_paths_still_work(self):
        write_setup_files(self.root, {"a/b.py": "x", "gitignore_but_not_dot_git": "y"})
        self.assertEqual((self.root / "a" / "b.py").read_text(), "x")


def _try_symlink(target: Path, link: Path) -> bool:
    """True on success. Windows requires an elevated privilege or Developer
    Mode to create a symlink at all - skip the test rather than fail it on a
    host that doesn't have that (this project's own CI matrix includes
    windows-latest, where this may or may not be enabled; POSIX runners are
    unaffected)."""
    try:
        os.symlink(target, link)
        return True
    except OSError:
        return False


class WorkspaceInspectionLimitsTests(unittest.TestCase):
    """P1-06: workspace snapshotting/diffing/scanning must not follow a
    symlink outside the workspace, and must not walk/hash/read an unbounded
    amount of data - a case's check_command and an agent's own actions have
    real disk access inside the workspace."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="optarena_test_wslimits_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_symlinked_file_is_skipped_not_followed(self):
        from optarena.cases import snapshot
        outside = Path(tempfile.mkdtemp(prefix="optarena_test_wslimits_outside_"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        secret = outside / "secret.txt"
        secret.write_text("host secret content", encoding="utf-8")
        link = self.root / "link.txt"
        if not _try_symlink(secret, link):
            self.skipTest("cannot create symlinks in this environment (needs elevated "
                          "privilege / Developer Mode on Windows)")
        (self.root / "real.txt").write_text("real", encoding="utf-8")
        snap = snapshot(self.root)
        self.assertIn("real.txt", snap)
        self.assertNotIn("link.txt", snap)

    def test_symlinked_directory_is_not_traversed_into(self):
        """A symlinked directory earlier in the walked path isn't caught by
        a leaf-level is_symlink() check on the FILE - the resolved path
        must still be confirmed inside root."""
        from optarena.cases import snapshot
        outside = Path(tempfile.mkdtemp(prefix="optarena_test_wslimits_outsidedir_"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (outside / "leaked.txt").write_text("leaked", encoding="utf-8")
        link_dir = self.root / "linked_dir"
        if not _try_symlink(outside, link_dir):
            self.skipTest("cannot create symlinks in this environment (needs elevated "
                          "privilege / Developer Mode on Windows)")
        snap = snapshot(self.root)
        self.assertFalse(any("leaked" in k for k in snap),
                         f"content from outside the workspace leaked into the snapshot: {snap}")

    def test_entry_count_cap_stops_the_walk(self):
        from optarena.cases import snapshot
        import optarena._cases._snapshot as cases_mod
        with mock.patch.object(cases_mod, "_SNAPSHOT_MAX_ENTRIES", 5):
            for i in range(20):
                (self.root / f"f{i}.txt").write_text("x", encoding="utf-8")
            snap = snapshot(self.root)
        self.assertLessEqual(len(snap), 5)

    def test_oversized_file_gets_size_only_signature_not_full_read(self):
        from optarena.cases import snapshot
        import optarena._cases._snapshot as cases_mod
        with mock.patch.object(cases_mod, "_SNAPSHOT_MAX_FILE_BYTES", 10):
            (self.root / "big.txt").write_text("x" * 100, encoding="utf-8")
            snap = snapshot(self.root)
        self.assertTrue(snap["big.txt"].startswith("size-only:"))

    def test_size_only_signature_still_detects_a_change(self):
        """changed_files must still notice a re-write of an oversized file
        (different size), even though the signature isn't a full hash."""
        from optarena.cases import changed_files, snapshot
        import optarena._cases._snapshot as cases_mod
        with mock.patch.object(cases_mod, "_SNAPSHOT_MAX_FILE_BYTES", 10):
            (self.root / "big.txt").write_text("x" * 100, encoding="utf-8")
            before = snapshot(self.root)
            (self.root / "big.txt").write_text("x" * 200, encoding="utf-8")
            changed = changed_files(before, self.root)
        self.assertIn("big.txt", changed)

    def test_check_expected_rejects_oversized_file_instead_of_reading_it(self):
        from optarena.cases import check_expected
        import optarena._cases._snapshot as cases_mod
        with mock.patch.object(cases_mod, "_SNAPSHOT_MAX_FILE_BYTES", 10):
            (self.root / "big.py").write_text("x" * 100, encoding="utf-8")
            failures = check_expected(
                ["big.py"], [{"path_pattern": "big.py", "content_patterns": ["x"]}], self.root)
        self.assertTrue(failures)
        self.assertIn("too large", failures[0])

    def test_scan_workspace_symlink_skipped(self):
        from optarena.security import scan_workspace
        outside = Path(tempfile.mkdtemp(prefix="optarena_test_wslimits_scan_"))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        secret = outside / "secret.py"
        secret.write_text('password = "hunter2sekrit"', encoding="utf-8")
        link = self.root / "link.py"
        if not _try_symlink(secret, link):
            self.skipTest("cannot create symlinks in this environment (needs elevated "
                          "privilege / Developer Mode on Windows)")
        res = scan_workspace(["link.py"], self.root)
        self.assertEqual(res["total"], 0)

    def test_scan_workspace_file_count_cap(self):
        from optarena import security
        files = [f"f{i}.py" for i in range(10)]
        for f in files:
            (self.root / f).write_text("x = 1\n", encoding="utf-8")
        with mock.patch.object(security, "_SCAN_MAX_FILES", 3), \
             mock.patch.object(security, "scan_text", wraps=security.scan_text) as m:
            security.scan_workspace(files, self.root)
        self.assertLessEqual(m.call_count, 3)


class CargoCleanPrefixTests(unittest.TestCase):
    """The actual fix for 22 false verify-corpus results on the Rust
    corpus: cargo's build-cache identity for a local path package is
    (name, version, deps, profile) - it does NOT include the package's own
    absolute directory (confirmed live with
    CARGO_LOG=cargo::core::compiler::fingerprint=trace). Two variants of the
    same case, or two different cases that happen to share a generic
    package name ("webapp"/"conc_case"/"dedup_perf" all do), can then have a
    LATER cargo test silently reuse an EARLIER build's already-compiled
    test binary - an oracle-violating broken_solutions variant read as
    passing. _cargo_clean_prefix is what run_check_command prepends to
    every check_command to force a real rebuild of just that one package
    (not its dependencies) before each run."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="optarena_test_cargoclean_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_no_cargo_toml_is_a_no_op(self):
        from optarena._cases._sandbox import _cargo_clean_prefix
        self.assertEqual(_cargo_clean_prefix(self.root), "")

    def test_extracts_package_name_from_cargo_toml(self):
        from optarena._cases._sandbox import _cargo_clean_prefix
        (self.root / "Cargo.toml").write_text(
            '[package]\nname = "sum_total"\nversion = "0.1.0"\nedition = "2021"\n',
            encoding="utf-8")
        prefix = _cargo_clean_prefix(self.root)
        self.assertIn("cargo clean", prefix)
        self.assertIn("-p sum_total", prefix)
        self.assertTrue(prefix.endswith("; "))

    def test_malformed_cargo_toml_is_a_safe_no_op(self):
        from optarena._cases._sandbox import _cargo_clean_prefix
        (self.root / "Cargo.toml").write_text("not actually toml\n", encoding="utf-8")
        self.assertEqual(_cargo_clean_prefix(self.root), "")

    def test_package_name_is_shell_quoted(self):
        """Defense in depth: the name ultimately comes from case-JSON
        content. Cargo itself only allows a small charset, but this must
        not trust that blindly."""
        from optarena._cases._sandbox import _cargo_clean_prefix
        (self.root / "Cargo.toml").write_text(
            '[package]\nname = "evil; rm -rf /"\nversion = "0.1.0"\n', encoding="utf-8")
        prefix = _cargo_clean_prefix(self.root)
        self.assertNotIn("rm -rf /", prefix.split("cargo clean", 1)[1].split(">", 1)[0]
                          .replace("'evil; rm -rf /'", ""))
        # the dangerous fragment must be inside a quoted argument, not a
        # bare shell-interpretable segment
        self.assertIn("'evil; rm -rf /'", prefix)

    def test_run_check_command_prepends_it_for_rust_workspace(self):
        """End to end through the real dispatch point, not just the helper
        in isolation - proves it actually reaches the executed command."""
        (self.root / "Cargo.toml").write_text(
            '[package]\nname = "demo_pkg"\nversion = "0.1.0"\n', encoding="utf-8")
        case = {"name": "c", "check_command": "cargo test --offline"}
        with mock.patch.dict(os.environ, {"OPTARENA_DISABLE_SANDBOX": "1"}), \
             mock.patch("optarena._cases._sandbox._run_check_command_local") as m:
            m.return_value = ([], {})
            run_check_command(case, self.root)
        executed_cmd = m.call_args[0][0]
        self.assertIn("cargo clean", executed_cmd)
        self.assertIn("-p demo_pkg", executed_cmd)
        self.assertTrue(executed_cmd.endswith("cargo test --offline"))

    def test_run_check_command_unaffected_for_non_rust_workspace(self):
        case = {"name": "c", "check_command": "python3 -m pytest"}
        with mock.patch.dict(os.environ, {"OPTARENA_DISABLE_SANDBOX": "1"}), \
             mock.patch("optarena._cases._sandbox._run_check_command_local") as m:
            m.return_value = ([], {})
            run_check_command(case, self.root)
        self.assertEqual(m.call_args[0][0], "python3 -m pytest")


def _load_standalone_script(dirname: str, name: str):
    """<dirname>/<name>.py are standalone CI scripts, not part of the
    optarena package (docker/ has no __init__.py, and putting it on
    sys.path would shadow the real third-party `docker` SDK package) -
    load by file path."""
    import importlib.util
    path = Path(__file__).resolve().parent.parent / dirname / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_docker_script(name: str):
    return _load_standalone_script("docker", name)


def _load_check_images_lock():
    return _load_docker_script("check_images_lock")


class CheckImagesLockPinnedDependenciesTests(unittest.TestCase):
    """P1-04: the checker used to validate base_digest and lockfile
    existence but never the pinned_dependencies values themselves - which is
    exactly how images.lock.json's phpunit entry (11.5.56) silently drifted
    from docker/php/Dockerfile's actual installed version (12.0.0) with no
    CI failure. These exercise _check_pinned_dependencies directly against
    small fixtures rather than the real repo files, so they stay meaningful
    regardless of what versions are currently pinned."""

    def setUp(self):
        self.mod = _load_check_images_lock()

    def test_matching_version_passes(self):
        entry = {"dockerfile": "x/Dockerfile", "pinned_dependencies": {"phpunit": "12.0.0"}}
        dockerfile_text = "RUN curl -fsSL https://phar.phpunit.de/phpunit-12.0.0.phar\n"
        self.assertEqual(self.mod._check_pinned_dependencies("x", entry, dockerfile_text), [])

    def test_drifted_version_fails(self):
        """The exact real-world case this check exists for: the lock says
        one version, the Dockerfile actually installs a different one."""
        entry = {"dockerfile": "x/Dockerfile", "pinned_dependencies": {"phpunit": "11.5.56"}}
        dockerfile_text = "RUN curl -fsSL https://phar.phpunit.de/phpunit-12.0.0.phar\n"
        errors = self.mod._check_pinned_dependencies("x", entry, dockerfile_text)
        self.assertEqual(len(errors), 1)
        self.assertIn("11.5.56", errors[0])
        self.assertIn("12.0.0", errors[0])

    def test_dependency_never_mentioned_fails(self):
        entry = {"dockerfile": "x/Dockerfile", "pinned_dependencies": {"phpunit": "12.0.0"}}
        self.assertTrue(self.mod._check_pinned_dependencies("x", entry, "FROM debian:trixie\n"))

    def test_real_lock_file_currently_passes(self):
        self.assertEqual(self.mod.check(), [])


class CheckVulnBaselineTests(unittest.TestCase):
    """P1-03: docker/check_vuln_baseline.py is what makes the image-vuln-scan
    CI job actually blocking - a genuinely NEW CRITICAL/HIGH finding must
    fail it, while the documented, already-triaged backlog in
    docker/vuln-baseline/*.json must not (that's the whole point of a
    baseline instead of a blanket continue-on-error).

    P1-02: the matching identity is now (target, package type, package,
    installed version, CVE ID) - not just (CVE ID, package) - plus explicit
    checks for a fix becoming available or severity being reclassified
    upward for an otherwise-still-matching finding. Fixture shapes below
    mirror the REAL Trivy JSON schema confirmed live against this session's
    own images (PkgIdentifier.PURL, InstalledVersion, FixedVersion, a
    Target string of the "<image-ref> (<os> <version>)" shape for
    OS-package results)."""

    def setUp(self):
        self.mod = _load_docker_script("check_vuln_baseline")

    def _scan(self, findings, target="ghcr.io/x/img:sha123 (debian 12)"):
        """findings: list of dicts - cve, pkg, installed="1.0", fixed="",
        severity="CRITICAL", pkg_type="deb", status="affected". Builds one
        Trivy Results[] entry with the real field names/shape
        (PkgIdentifier.PURL etc). `status` defaults to "affected" to match
        `_accepted`'s own default - existing tests that never mention
        status stay internally consistent (no spurious P2-05 status-change
        problem) without either helper's caller needing to say so."""
        vulns = []
        for f in findings:
            installed = f.get("installed", "1.0")
            pkg_type = f.get("pkg_type", "deb")
            vulns.append({
                "VulnerabilityID": f["cve"],
                "PkgName": f["pkg"],
                "InstalledVersion": installed,
                "FixedVersion": f.get("fixed", ""),
                "Severity": f.get("severity", "CRITICAL"),
                "Status": f.get("status", "affected"),
                "PkgIdentifier": {"PURL": f"pkg:{pkg_type}/debian/{f['pkg']}@{installed}"},
            })
        return {"Results": [{"Target": target, "Vulnerabilities": vulns}]}

    def _accepted(self, cve, pkg, installed="1.0", fixed="", severity="CRITICAL",
                  pkg_type="deb", target="debian 12", status="affected"):
        return {"id": cve, "package": pkg, "package_type": pkg_type,
                "installed_version": installed, "fixed_version": fixed,
                "severity": severity, "target": target, "status": status}

    def _write_baseline(self, baseline_dir, accepted, expires_at="2099-01-01", **extra):
        (baseline_dir / "img.json").write_text(json.dumps({
            "image": "img", "expires_at": expires_at,
            "accepted_vulnerabilities": accepted, **extra,
        }), encoding="utf-8")

    def test_missing_baseline_file_is_a_problem_not_a_crash(self):
        problems = self.mod.check("no-such-image", self._scan([]))
        self.assertEqual(len(problems), 1)
        self.assertIn("no baseline file", problems[0])

    def test_new_finding_not_in_baseline_fails(self):
        """The actual point of this mechanism: a CVE that isn't already
        documented as accepted must fail, not silently pass."""
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [self._accepted("CVE-2020-1", "old-pkg")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan([
                    {"cve": "CVE-2020-1", "pkg": "old-pkg"},
                    {"cve": "CVE-2026-9999", "pkg": "new-pkg"},
                ]))
        self.assertEqual(len(problems), 1)
        self.assertIn("CVE-2026-9999", problems[0])
        self.assertIn("new-pkg", problems[0])

    def test_baseline_findings_alone_pass_clean(self):
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [self._accepted("CVE-2020-1", "old-pkg")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan([{"cve": "CVE-2020-1", "pkg": "old-pkg"}]))
        self.assertEqual(problems, [])

    def test_expired_baseline_fails_even_with_no_new_findings(self):
        """Forces periodic re-triage instead of the acceptance silently
        becoming permanent."""
        import datetime
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [self._accepted("CVE-2020-1", "old-pkg")],
                                  expires_at="2020-01-01", owner="someone")
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check(
                    "img", self._scan([{"cve": "CVE-2020-1", "pkg": "old-pkg"}]),
                    today=datetime.date(2026, 1, 1))
        self.assertEqual(len(problems), 1)
        self.assertIn("expired", problems[0])

    def test_same_cve_across_multiple_targets_deduplicated(self):
        """The same CVE+package+version can legitimately appear in multiple
        vendored copies (two .deps.json files bundling the same library) -
        that's a detail of where it was found, not a second vulnerability
        to demand a second baseline entry for."""
        scan = {"Results": [
            {"Target": "img:t (debian 12)", "Vulnerabilities": [
                {"VulnerabilityID": "CVE-2020-1", "PkgName": "p", "InstalledVersion": "1.0",
                 "FixedVersion": "", "Severity": "HIGH", "PkgIdentifier": {"PURL": "pkg:deb/debian/p@1.0"}}]},
            {"Target": "img:t (debian 12)", "Vulnerabilities": [
                {"VulnerabilityID": "CVE-2020-1", "PkgName": "p", "InstalledVersion": "1.0",
                 "FixedVersion": "", "Severity": "HIGH", "PkgIdentifier": {"PURL": "pkg:deb/debian/p@1.0"}}]},
        ]}
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [self._accepted("CVE-2020-1", "p", severity="HIGH")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", scan)
        self.assertEqual(problems, [])

    def test_changed_installed_version_invalidates_acceptance(self):
        """P1-02: accepting a CVE against one installed version must not
        silently keep covering it after the package moves to a different
        version - that's a materially different (and unreviewed) state."""
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [self._accepted("CVE-2020-1", "pkg", installed="1.0")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan(
                    [{"cve": "CVE-2020-1", "pkg": "pkg", "installed": "1.1"}]))
        self.assertEqual(len(problems), 1)
        self.assertIn("CVE-2020-1", problems[0])
        self.assertIn("1.1", problems[0])

    def test_newly_available_fix_invalidates_acceptance(self):
        """P1-02: a finding accepted as 'no fix available' must re-trigger
        once Trivy's DB reports one - the environment didn't change, but
        the finding is no longer the same acceptable risk it was."""
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [self._accepted("CVE-2020-1", "pkg", fixed="")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan(
                    [{"cve": "CVE-2020-1", "pkg": "pkg", "fixed": "1.0.1"}]))
        self.assertEqual(len(problems), 1)
        self.assertIn("gained fixed_version", problems[0])
        self.assertIn("1.0.1", problems[0])

    def test_severity_increase_invalidates_acceptance(self):
        """P1-02: Trivy's own severity classification for a CVE can be
        revised upward independent of anything in this repo - re-triage
        when that happens rather than trusting a stale severity forever."""
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [self._accepted("CVE-2020-1", "pkg", severity="HIGH")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan(
                    [{"cve": "CVE-2020-1", "pkg": "pkg", "severity": "CRITICAL"}]))
        self.assertEqual(len(problems), 1)
        self.assertIn("severity increased", problems[0])
        self.assertIn("HIGH -> CRITICAL", problems[0])

    def test_severity_decrease_or_unchanged_does_not_fail(self):
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [self._accepted("CVE-2020-1", "pkg", severity="CRITICAL")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan(
                    [{"cve": "CVE-2020-1", "pkg": "pkg", "severity": "HIGH"}]))
        self.assertEqual(problems, [])

    def test_status_change_to_affected_invalidates_acceptance(self):
        """P2-05: accepted while "under_investigation" (Trivy hadn't
        concluded yet) - now confirmed "affected". A real, unresolved
        vulnerability the accepting human never actually reviewed as such."""
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [
                self._accepted("CVE-2020-1", "pkg", status="under_investigation")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan(
                    [{"cve": "CVE-2020-1", "pkg": "pkg", "status": "affected"}]))
        self.assertEqual(len(problems), 1)
        self.assertIn("status changed", problems[0])
        self.assertIn("under_investigation", problems[0])
        self.assertIn("affected", problems[0])

    def test_status_change_to_fixed_invalidates_acceptance(self):
        """P2-05: "fixed" is caught by this check too, as defense-in-depth
        alongside the existing fixed_version check - Trivy can report
        Status="fixed" for some ecosystems without FixedVersion being
        populated, so this isn't purely redundant with that check."""
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [
                self._accepted("CVE-2020-1", "pkg", status="will_not_fix")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan(
                    [{"cve": "CVE-2020-1", "pkg": "pkg", "status": "fixed"}]))
        self.assertEqual(len(problems), 1)
        self.assertIn("status changed", problems[0])

    def test_status_change_away_from_affected_does_not_fail(self):
        """P2-05: the directionally-safe case - Trivy (or a vendor) later
        decided this finding doesn't actually apply, or won't be fixed.
        Less concerning than before, not more - must not re-block CI."""
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [
                self._accepted("CVE-2020-1", "pkg", status="affected")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan(
                    [{"cve": "CVE-2020-1", "pkg": "pkg", "status": "not_affected"}]))
        self.assertEqual(problems, [])

    def test_status_unchanged_does_not_fail(self):
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [
                self._accepted("CVE-2020-1", "pkg", status="affected")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan(
                    [{"cve": "CVE-2020-1", "pkg": "pkg", "status": "affected"}]))
        self.assertEqual(problems, [])

    def test_status_change_between_two_non_affected_states_does_not_fail(self):
        """Neither side of the transition is "affected"/"fixed" - e.g.
        under_investigation -> will_not_fix. Not the concerning direction
        this check exists to catch."""
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [
                self._accepted("CVE-2020-1", "pkg", status="under_investigation")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan(
                    [{"cve": "CVE-2020-1", "pkg": "pkg", "status": "will_not_fix"}]))
        self.assertEqual(problems, [])

    def test_target_change_invalidates_acceptance(self):
        """P1-02: an OS version bump (debian 12 -> 13) changes the actual
        scanned surface even if the CVE+package+version string still reads
        the same - treat it as unreviewed rather than assuming it's fine."""
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [self._accepted("CVE-2020-1", "pkg", target="debian 12")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan(
                    [{"cve": "CVE-2020-1", "pkg": "pkg"}], target="ghcr.io/x/img:sha (debian 13)"))
        self.assertEqual(len(problems), 1)
        self.assertIn("NEW finding", problems[0])

    def test_image_ref_prefix_in_target_does_not_break_matching(self):
        """The same underlying OS target must match across two scans that
        used different tags/registries to reach it (ci.yml's local-tag PR
        scan vs publish-images.yml's git-sha-tagged real scan) - only the
        "(os version)" suffix is the actually-comparable part."""
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [self._accepted("CVE-2020-1", "pkg", target="debian 12")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan(
                    [{"cve": "CVE-2020-1", "pkg": "pkg"}],
                    target="localhost:5000/totally/different/ref:abc123 (debian 12)"))
        self.assertEqual(problems, [])

    def test_pkg_type_distinguishes_same_named_package_across_ecosystems(self):
        """A "requests" Debian package and an unrelated "requests" gem (or
        any other same-named-different-ecosystem pair) must not be treated
        as the same accepted finding just because the bare name matches."""
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            self._write_baseline(baseline_dir, [
                self._accepted("CVE-2020-1", "requests", pkg_type="deb")])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan(
                    [{"cve": "CVE-2020-1", "pkg": "requests", "pkg_type": "gem"}]))
        self.assertEqual(len(problems), 1)
        self.assertIn("NEW finding", problems[0])

    def test_malformed_entry_missing_required_field_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            bad = self._accepted("CVE-2020-1", "pkg")
            del bad["package_type"]
            self._write_baseline(baseline_dir, [bad])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan([]))
        self.assertEqual(len(problems), 1)
        self.assertIn("malformed baseline entry", problems[0])
        self.assertIn("package_type", problems[0])

    def test_duplicate_baseline_entries_reported(self):
        with tempfile.TemporaryDirectory() as d:
            baseline_dir = Path(d)
            entry = self._accepted("CVE-2020-1", "pkg")
            self._write_baseline(baseline_dir, [entry, dict(entry)])
            with mock.patch.object(self.mod, "BASELINE_DIR", baseline_dir):
                problems = self.mod.check("img", self._scan([]))
        self.assertEqual(len(problems), 1)
        self.assertIn("duplicate", problems[0])

    def test_all_nine_real_baselines_exist_and_are_well_formed(self):
        baseline_dir = Path(__file__).resolve().parent.parent / "docker" / "vuln-baseline"
        images = [
            "optarena-tester", "optarena-tester-python", "optarena-tester-node",
            "optarena-tester-go", "optarena-tester-jvm", "optarena-tester-rust",
            "optarena-tester-dotnet", "optarena-tester-php", "optarena-tester-ruby",
        ]
        required = {"id", "package", "package_type", "installed_version", "target", "severity"}
        for image in images:
            with self.subTest(image=image):
                path = baseline_dir / f"{image}.json"
                self.assertTrue(path.is_file(), f"missing baseline: {path}")
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["image"], image)
                self.assertIn("expires_at", data)
                self.assertIn("owner", data)
                self.assertTrue(data["accepted_vulnerabilities"])
                for entry in data["accepted_vulnerabilities"]:
                    missing = required - entry.keys()
                    self.assertFalse(missing, f"{image}: entry {entry} missing {missing}")
                # P1-02: the real files themselves must be internally consistent
                # too, not just individually well-formed - reuses the exact
                # malformed/duplicate detection the check itself applies.
                _, malformed = self.mod._load_accepted(data, image)
                self.assertEqual(malformed, [], f"{image}: {malformed}")


class CheckNoInfraErrorsTests(unittest.TestCase):
    """P2-06: scripts/check_no_infra_errors.py is what lets
    integration-smoke.yml distinguish "the model got the task wrong" (an
    ordinary, expected outcome for a small local smoke-test model) from
    "the integration itself broke" (a container engine failure, a driver
    crash - the actual thing that workflow exists to catch) - verified
    directly against a real run record shape, not just plausible-looking
    fixtures."""

    def setUp(self):
        self.mod = _load_standalone_script("scripts", "check_no_infra_errors")
        self.d = Path(tempfile.mkdtemp(prefix="optarena_test_infracheck_"))
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def _write(self, summary: dict) -> str:
        path = self.d / "run.json"
        path.write_text(json.dumps({"summary": summary}), encoding="utf-8")
        return str(path)

    def test_model_failure_with_no_infra_errors_passes(self):
        """The exact scenario confirmed live: aider+gemma3:1b failed the
        create_fibonacci case (optarena run exits 1) with
        infrastructure_errors=None - this must exit 0."""
        path = self._write({"failed": 1, "infrastructure_errors": None})
        with mock.patch.object(sys, "argv", ["check_no_infra_errors.py", path]):
            self.assertEqual(self.mod.main(), 0)

    def test_infrastructure_error_fails(self):
        path = self._write({"failed": 1, "infrastructure_errors": 2})
        with mock.patch.object(sys, "argv", ["check_no_infra_errors.py", path]):
            self.assertEqual(self.mod.main(), 1)

    def test_all_passed_no_infra_errors_passes(self):
        path = self._write({"failed": 0, "infrastructure_errors": None})
        with mock.patch.object(sys, "argv", ["check_no_infra_errors.py", path]):
            self.assertEqual(self.mod.main(), 0)

    def test_missing_argv_is_a_clean_usage_error_not_a_crash(self):
        with mock.patch.object(sys, "argv", ["check_no_infra_errors.py"]):
            self.assertEqual(self.mod.main(), 2)


class NormalizeWorkspaceLineEndingsTests(unittest.TestCase):
    """
    A-42: found live via a qwen3-coder+aider run - a syntactically and
    semantically correct generated shell script (`backup.sh`) failed with
    `Syntax error: end of file unexpected (expecting "then")`, purely
    because it was CRLF-terminated on this Windows host and then
    bind-mounted, unmodified, into the Linux sandbox. `run_check_command`
    now normalizes every file in the workspace right before the command
    that actually runs inside Linux executes.
    """

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_crlf_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_crlf_file_is_normalized_to_lf(self):
        target = self.ws / "backup.sh"
        target.write_bytes(b'#!/bin/sh\r\nif [ -z "$1" ]; then\r\n  exit 1\r\nfi\r\n')
        normalize_workspace_line_endings(self.ws)
        data = target.read_bytes()
        self.assertNotIn(b"\r", data)
        self.assertEqual(data, b'#!/bin/sh\nif [ -z "$1" ]; then\n  exit 1\nfi\n')

    def test_lone_cr_is_also_normalized(self):
        target = self.ws / "old_mac.sh"
        target.write_bytes(b"echo one\recho two\r")
        normalize_workspace_line_endings(self.ws)
        self.assertEqual(target.read_bytes(), b"echo one\necho two\n")

    def test_lf_only_file_is_left_untouched(self):
        target = self.ws / "already_fine.sh"
        original = b"#!/bin/sh\necho hi\n"
        target.write_bytes(original)
        mtime_before = target.stat().st_mtime_ns
        normalize_workspace_line_endings(self.ws)
        self.assertEqual(target.read_bytes(), original)
        self.assertEqual(target.stat().st_mtime_ns, mtime_before)

    def test_binary_looking_file_is_left_alone(self):
        target = self.ws / "fixture.bin"
        original = b"\x00\x01binary\r\ndata"
        target.write_bytes(original)
        normalize_workspace_line_endings(self.ws)
        self.assertEqual(target.read_bytes(), original)

    def test_recurses_into_subdirectories(self):
        nested = self.ws / "src" / "nested.sh"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"echo a\r\necho b\r\n")
        normalize_workspace_line_endings(self.ws)
        self.assertNotIn(b"\r", nested.read_bytes())

    def test_symlink_is_skipped(self):
        real = self.ws / "real.sh"
        real.write_bytes(b"echo a\r\n")
        link = self.ws / "link.sh"
        try:
            link.symlink_to(real)
        except OSError:
            self.skipTest("symlinks not permitted on this host")
        normalize_workspace_line_endings(self.ws)
        # the real file (visited directly) is still normalized; the symlink
        # itself is never opened/rewritten as a distinct target.
        self.assertNotIn(b"\r", real.read_bytes())


class PathPatternMatchesTests(unittest.TestCase):
    """
    A-39: `**/X` must mean "X, at any depth INCLUDING the root" (standard
    globstar semantics - bash's globstar, rsync excludes, Python 3.13's own
    glob.translate()/pathlib matching, Ant filesets all agree on this), not
    "X, strictly nested" - which is what plain `fnmatch` accidentally
    enforced, since it has no concept of "/" as a path separator and a
    "**/" pattern can only ever match a candidate that literally contains a
    "/". Found via a real jvm-track calibration failure: the model's output
    was correct, sitting right in the oracle's own "got:" message, and still
    failed because it was a flat file with no "/" in its path at all.
    """

    def test_bare_file_satisfies_a_doublestar_prefix(self):
        # the exact case that surfaced this: model wrote a flat file, case
        # wanted "**/MessageFormatterTest.java".
        self.assertTrue(path_pattern_matches(
            "MessageFormatterTest.java", "**/MessageFormatterTest.java"))

    def test_genuinely_nested_path_still_satisfies_a_doublestar_prefix(self):
        # a real agent driver writing a proper project layout must be
        # completely unaffected by this fix.
        self.assertTrue(path_pattern_matches(
            "src/main/java/com/example/MessageFormatterTest.java",
            "**/MessageFormatterTest.java"))

    def test_wrong_basename_still_fails(self):
        self.assertFalse(path_pattern_matches(
            "WrongName.java", "**/MessageFormatterTest.java"))

    def test_case_insensitive_both_sides(self):
        self.assertTrue(path_pattern_matches("classname.JAVA", "**/ClassName.java"))

    def test_single_star_prefix_is_NOT_loosened(self):
        # A single "*" conventionally means "exactly one path segment", not
        # "zero or more" - concrete_target() has no way to invent an
        # arbitrary wrapper directory name, so this class of pattern stays
        # genuinely unsatisfiable by a flat-file writer. Must NOT be
        # widened by this fix - only an exact "**/" prefix is.
        self.assertFalse(path_pattern_matches("routes/respond.ts", "*/routes/respond.ts"))
        self.assertTrue(path_pattern_matches("src/routes/respond.ts", "*/routes/respond.ts"))

    def test_doublestar_not_at_the_start_is_unaffected(self):
        # scoped deliberately narrow - only a LEADING "**/" is special-cased
        # (the only shape the built-in corpus actually uses). "**" appearing
        # elsewhere in a pattern falls through to plain fnmatch, unchanged.
        self.assertFalse(path_pattern_matches("X.txt", "a/**/X.txt"))

    def test_no_regression_on_a_plain_literal_pattern(self):
        self.assertTrue(path_pattern_matches("hello.py", "hello.py"))
        self.assertFalse(path_pattern_matches("goodbye.py", "hello.py"))

    def test_no_regression_on_a_bare_glob_with_no_directory(self):
        self.assertTrue(path_pattern_matches("output_test.go", "*_test.go"))
        self.assertTrue(path_pattern_matches("src/output_test.go", "*_test.go"))


class BaselineIncompatibleTests(unittest.TestCase):
    """
    A-40: can a flat-file-writing driver (openai-chat/ollama-chat, and every
    SDK-agent driver - none have file tools) ever satisfy this case? Powers
    cli.cmd_run's preflight warning. Deliberately built on the SAME
    functions (concrete_target, path_pattern_matches) that decide the real
    outcome at run time, not a separately maintained heuristic.
    """

    def test_none_when_winnable(self):
        case = {"expected_files": [{"path_pattern": "hello.py"}]}
        self.assertIsNone(baseline_incompatible(case))

    def test_none_for_a_doublestar_prefix_after_the_A39_fix(self):
        # this exact shape used to be hostile before path_pattern_matches
        # was fixed - confirms the two fixes compose correctly.
        case = {"expected_files": [{"path_pattern": "**/MessageFormatterTest.java"}]}
        self.assertIsNone(baseline_incompatible(case))

    def test_multi_file_is_hostile(self):
        case = {"expected_files": [{"path_pattern": "a.py"}, {"path_pattern": "b.py"}]}
        reason = baseline_incompatible(case)
        self.assertIsNotNone(reason)
        self.assertIn("2 separate files", reason)

    def test_setup_repo_is_hostile(self):
        case = {"expected_files": [{"path_pattern": "a.py"}], "setup_repo": "fastapi-tasktracker"}
        reason = baseline_incompatible(case)
        self.assertIsNotNone(reason)
        self.assertIn("starter repo", reason)

    def test_single_star_prefix_is_hostile(self):
        case = {"expected_files": [{"path_pattern": "*/routes/foo.ts"}]}
        reason = baseline_incompatible(case)
        self.assertIsNotNone(reason)
        self.assertIn("does not itself satisfy", reason)

    def test_no_expected_files_is_winnable(self):
        self.assertIsNone(baseline_incompatible({"expected_files": []}))
        self.assertIsNone(baseline_incompatible({}))

    def test_every_builtin_case_is_classified_without_raising(self):
        for case in load_cases():
            baseline_incompatible(case)   # must never raise

    def test_corpus_wide_count_dropped_after_the_glob_fix(self):
        # A-39 rescued 81 **/-prefix cases from being false-flagged. The
        # D4/D5 repo-scale push then added 108 new setup_repo cases (every
        # one baseline_incompatible by construction - that's the whole
        # point of "repo-scale"), so the hostile set is now ~175 rather
        # than the earlier post-fix ~91. Bound kept well above the current
        # count so it still catches a real regression, not this expansion.
        hostile = [c for c in load_cases() if baseline_incompatible(c)]
        self.assertGreater(len(hostile), 0)
        self.assertLess(len(hostile), 250)


class RegistryTests(unittest.TestCase):
    def test_all_registered_drivers_instantiate(self):
        # No skip needed for any optional SDK driver (crewai, openai-agents,
        # smolagents, langgraph, autogen, semantic-kernel): each only
        # imports its package lazily inside prepare()/run_case(), never at
        # module scope, so get_driver() succeeds regardless of whether the
        # package is actually installed - only calling prepare() would fail.
        for name, meta in DRIVERS.items():
            driver = get_driver(name)
            self.assertTrue(hasattr(driver, "run_case"), name)
            self.assertIn(meta["kind"], ("cli", "sdk", "baseline"))
            self.assertIn(meta["backend"], ("scenario", "fixed"))

    def test_fixed_backend_drivers_are_marked(self):
        self.assertEqual(DRIVERS["claude-code"]["backend"], "fixed")
        self.assertEqual(DRIVERS["codex"]["backend"], "fixed")
        self.assertEqual(DRIVERS["aider"]["backend"], "scenario")

    def test_every_driver_declares_file_tools(self):
        # A-40: cli.cmd_run's preflight warning reads this field; a driver
        # missing it entirely would silently read as "has file tools" (the
        # permissive default in DRIVERS.get(...).get("file_tools", True)),
        # under-warning rather than crashing - catch that at registration
        # time instead, where it's obvious and cheap to fix.
        for name, meta in DRIVERS.items():
            self.assertIn("file_tools", meta, name)
            self.assertIsInstance(meta["file_tools"], bool, name)
            # Holds for every driver registered today: real file-editing
            # tools <=> a CLI agent. Not a hard law forever (a future SDK
            # driver could gain function-calling file tools), so this is a
            # deliberate assertion of the current registry, not a
            # constraint enforced elsewhere - if it ever needs to differ,
            # update this test alongside the registry entry.
            self.assertEqual(meta["file_tools"], meta["kind"] == "cli", name)


class SubprocessEnvTests(unittest.TestCase):
    """C-03: CLI-agent subprocesses get an explicit allowlist, not a filtered
    copy of the whole host environment - a stray host secret must not reach
    a subprocess whose entire job is running model-generated commands."""

    def test_unrelated_host_secret_not_forwarded(self):
        with mock.patch.dict(os.environ, {"AWS_SECRET_ACCESS_KEY": "super-secret",
                                          "PATH": os.environ.get("PATH", "")}):
            env = subprocess_env()
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)

    def test_allowlisted_vars_forwarded_when_present(self):
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin", "HOME": "/home/x"}):
            env = subprocess_env()
        self.assertEqual(env.get("PATH"), "/usr/bin")
        self.assertEqual(env.get("HOME"), "/home/x")

    def test_extra_env_always_included(self):
        env = subprocess_env(extra={"OPENAI_API_KEY": "sk-test"})
        self.assertEqual(env["OPENAI_API_KEY"], "sk-test")

    def test_passthrough_only_forwarded_if_actually_set(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            env = subprocess_env(passthrough=("ANTHROPIC_API_KEY",))
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}):
            env = subprocess_env(passthrough=("ANTHROPIC_API_KEY",))
        self.assertEqual(env["ANTHROPIC_API_KEY"], "sk-ant-test")

    def test_extra_wins_over_passthrough(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "host-value"}):
            env = subprocess_env(extra={"OPENAI_API_KEY": "scenario-value"},
                                 passthrough=("OPENAI_API_KEY",))
        self.assertEqual(env["OPENAI_API_KEY"], "scenario-value")


class ApiKeyNeverInArgvTests(unittest.TestCase):
    """
    A-06: process arguments are readable by other local users
    (/proc/<pid>/cmdline, any Windows session), so no driver may put the
    backend's API key in argv. The aider driver used to pass
    `--openai-api-key <secret>`, contradicting cli.py's own --api-key help
    and SECURITY.md.
    """

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_argvkey_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def _capture_aider_invocation(self):
        captured = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["env"] = kwargs.get("env") or {}
            import subprocess as _sp
            return _sp.CompletedProcess(cmd, 0, "", "")

        driver = AiderDriver()
        driver._aider = "aider"
        scenario = Scenario(name="s", driver="aider",
                            backend=Backend(base_url="http://localhost:11434",
                                            model="llama3.2", api_key="sk-super-secret"))
        case = {"name": "c", "prompts": ["do it"], "expected_files": []}
        with mock.patch("optarena.drivers.aider_cli.run_capture", side_effect=_fake_run):
            driver.run_case(case, scenario, self.ws)
        return captured

    def test_aider_key_absent_from_argv(self):
        captured = self._capture_aider_invocation()
        joined = " ".join(captured["cmd"])
        self.assertNotIn("sk-super-secret", joined)
        self.assertNotIn("--openai-api-key", captured["cmd"])

    def test_aider_key_present_in_subprocess_env(self):
        captured = self._capture_aider_invocation()
        self.assertEqual(captured["env"].get("OPENAI_API_KEY"), "sk-super-secret")
        self.assertEqual(captured["env"].get("OPENAI_API_BASE"),
                         "http://localhost:11434/v1")

    def test_no_cli_agent_spec_puts_the_key_in_argv(self):
        # The generic CLI-agent descriptors build argv from a lambda - none of
        # them may embed the key either.
        from optarena.drivers.cli_agents import CLI_AGENTS
        backend = Backend(api_key="sk-super-secret", model="m")
        for key, spec in CLI_AGENTS.items():
            with self.subTest(agent=key):
                argv = spec["argv"]("prompt text", backend)
                self.assertNotIn("sk-super-secret", " ".join(str(a) for a in argv))


class ScenarioTests(unittest.TestCase):
    def test_cases_dir_roundtrip(self):
        sc = Scenario(name="x", driver="aider", cases_dir="my/cases")
        again = Scenario.from_dict(sc.to_dict())
        self.assertEqual(again.cases_dir, "my/cases")

    def test_relative_cases_dir_resolved_against_scenario_file_not_cwd(self):
        # Phase 3.3: a relative cases_dir names a directory NEXT TO the
        # scenario file - the same scenario must resolve to the same case
        # set no matter what directory `optarena run` happens to be invoked
        # from.
        project = Path(tempfile.mkdtemp(prefix="optarena_test_relpath_"))
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        (project / "scenarios").mkdir()
        (project / "scenarios" / "my-cases").mkdir()
        (project / "scenarios" / "my-cases" / "c1.json").write_text(
            json.dumps({"name": "c1", "prompts": ["x"]}), encoding="utf-8")
        scenario_file = project / "scenarios" / "sc.json"
        scenario_file.write_text(json.dumps({
            "name": "x", "driver": "aider", "cases_dir": "my-cases",
        }), encoding="utf-8")

        elsewhere = Path(tempfile.mkdtemp(prefix="optarena_test_elsewhere_"))
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        cwd = os.getcwd()
        try:
            os.chdir(elsewhere)
            sc = Scenario.from_file(scenario_file)
        finally:
            os.chdir(cwd)

        self.assertTrue(Path(sc.cases_dir).is_absolute())
        loaded = load_cases(cases_dir=sc.cases_dir)
        self.assertEqual([c["name"] for c in loaded], ["c1"])

    def test_absolute_cases_dir_unchanged(self):
        abs_dir = Path(tempfile.mkdtemp(prefix="optarena_test_absdir_"))
        self.addCleanup(shutil.rmtree, abs_dir, ignore_errors=True)
        scenario_file = abs_dir / "sc.json"
        scenario_file.write_text(json.dumps({
            "name": "x", "driver": "aider", "cases_dir": str(abs_dir),
        }), encoding="utf-8")
        sc = Scenario.from_file(scenario_file)
        self.assertEqual(Path(sc.cases_dir).resolve(), abs_dir.resolve())

    def test_openai_base_never_doubles_v1(self):
        self.assertEqual(Backend(base_url="http://h:1/v1").openai_base, "http://h:1/v1")
        self.assertEqual(Backend(base_url="http://h:1").openai_base, "http://h:1/v1")

    def test_to_dict_redact_strips_api_key(self):
        # C-04: api_key must never land in anything persisted to disk
        # (saved runs, comparisons). redact=False (the default) preserves
        # round-tripping a scenario *file*, where the real key is exactly
        # what's being configured, not evaluation output.
        sc = Scenario(name="x", driver="aider",
                      backend=Backend(api_key="sk-canary-secret"))
        self.assertEqual(sc.to_dict()["backend"]["api_key"], "sk-canary-secret")
        redacted = sc.to_dict(redact=True)
        self.assertIsNone(redacted["backend"]["api_key"])
        self.assertTrue(redacted["backend"]["api_key_set"])
        self.assertNotIn("sk-canary-secret", json.dumps(redacted))

    def test_to_dict_redact_default_key_not_flagged_set(self):
        sc = Scenario(name="x", driver="aider")  # default Backend(api_key="optarena")
        self.assertFalse(sc.to_dict(redact=True)["backend"]["api_key_set"])

    def test_load_cases_custom_dir(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "a_case.json").write_text(json.dumps(
            {"name": "custom", "prompts": ["p"], "expected_files": []}), encoding="utf-8")
        cases = load_cases(cases_dir=str(d))
        self.assertEqual([c["name"] for c in cases], ["custom"])

    def test_load_cases_empty_names_list_means_none_not_all(self):
        # Found via manual testing: a --language filter that matches zero
        # cases resolves to `names=[]`. An empty list is falsy in Python, so
        # `if names:` would silently treat it the same as `names=None` (no
        # filter -> load everything) instead of "load nothing".
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "a_case.json").write_text(json.dumps(
            {"name": "custom", "prompts": ["p"], "expected_files": []}), encoding="utf-8")
        cases = load_cases(names=[], cases_dir=str(d))
        self.assertEqual(cases, [])

    def test_language_filter_resolves_to_matching_case_names_only(self):
        from optarena.cli import _scenario_from_args
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "py_case.json").write_text(json.dumps(
            {"name": "py_case", "language": "python", "prompts": ["p"], "expected_files": []}), encoding="utf-8")
        (d / "js_case.json").write_text(json.dumps(
            {"name": "js_case", "language": "javascript", "prompts": ["p"], "expected_files": []}), encoding="utf-8")
        args = argparse.Namespace(
            driver="ollama-chat", model="llama3.2", kind="ollama",
            base_url="http://localhost:11434", api_key="optarena", name=None,
            cases=None, cases_dir=str(d), timeout=None, language="python",
        )
        sc = _scenario_from_args(args)
        self.assertEqual(sc.cases, ["py_case"])

    def test_framework_filter_resolves_to_matching_case_names_only(self):
        from optarena.cli import _scenario_from_args
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "gin_case.json").write_text(json.dumps(
            {"name": "gin_case", "language": "go", "framework": "gin",
             "prompts": ["p"], "expected_files": []}), encoding="utf-8")
        (d / "axum_case.json").write_text(json.dumps(
            {"name": "axum_case", "language": "rust", "framework": "axum",
             "prompts": ["p"], "expected_files": []}), encoding="utf-8")
        args = argparse.Namespace(
            driver="ollama-chat", model="llama3.2", kind="ollama",
            base_url="http://localhost:11434", api_key="optarena", name=None,
            cases=None, cases_dir=str(d), timeout=None, language=None, framework="gin",
        )
        sc = _scenario_from_args(args)
        self.assertEqual(sc.cases, ["gin_case"])

    def test_language_and_framework_filters_and_together(self):
        # A language filter alone can't disambiguate two frameworks in the
        # same language - both filters must narrow jointly (AND), not
        # override each other.
        cases = [
            {"name": "a", "language": "python", "framework": "fastapi"},
            {"name": "b", "language": "python", "framework": "flask"},
            {"name": "c", "language": "go", "framework": "gin"},
        ]
        self.assertEqual(
            [c["name"] for c in filter_cases(cases, language="python", framework="fastapi")],
            ["a"],
        )
        self.assertEqual(
            [c["name"] for c in filter_cases(cases, language="python")],
            ["a", "b"],
        )


class HostNativeExecutionConfirmationTests(unittest.TestCase):
    """P1-05: a `cli`-kind driver (aider, Claude Code, ...) runs headlessly
    on the HOST with the user's real filesystem/environment/credentials -
    only the verifier sandbox is containerized. This gate must not silently
    let that happen unattended."""

    def _scenario(self, driver):
        return Scenario(name="x", driver=driver,
                         backend=Backend(kind="ollama", base_url="http://x", model="m"))

    def test_baseline_driver_needs_no_confirmation(self):
        from optarena.cli import _confirm_host_native_execution
        args = argparse.Namespace(yes_i_understand_host_execution=False)
        self.assertTrue(_confirm_host_native_execution([self._scenario("ollama-chat")], args))

    def test_flag_bypasses_prompt_entirely(self):
        from optarena.cli import _confirm_host_native_execution
        args = argparse.Namespace(yes_i_understand_host_execution=True)
        with mock.patch("sys.stdin.isatty", return_value=False):
            self.assertTrue(_confirm_host_native_execution([self._scenario("aider")], args))

    def test_non_interactive_without_flag_refused(self):
        from optarena.cli import _confirm_host_native_execution
        args = argparse.Namespace(yes_i_understand_host_execution=False)
        with mock.patch("sys.stdin.isatty", return_value=False):
            self.assertFalse(_confirm_host_native_execution([self._scenario("aider")], args))

    def test_interactive_yes_confirms(self):
        from optarena.cli import _confirm_host_native_execution
        args = argparse.Namespace(yes_i_understand_host_execution=False)
        with mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", return_value="y"):
            self.assertTrue(_confirm_host_native_execution([self._scenario("claude-code")], args))

    def test_interactive_no_refuses(self):
        from optarena.cli import _confirm_host_native_execution
        args = argparse.Namespace(yes_i_understand_host_execution=False)
        with mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", return_value="n"):
            self.assertFalse(_confirm_host_native_execution([self._scenario("codex")], args))


class SchemaValidationTests(unittest.TestCase):
    """Phase 2.5: malformed/fuzzed scenario or case files must fail fast,
    with a clear file+key error, before any driver/backend call."""

    def test_valid_case_passes(self):
        validate_case({"name": "x", "prompts": ["do it"],
                       "expected_files": [{"path_pattern": "a.py", "content_patterns": ["def"]}]})

    def test_unknown_case_key_rejected(self):
        with self.assertRaises(SchemaError) as ctx:
            validate_case({"name": "x", "chekc_command": "echo hi"})
        self.assertIn("chekc_command", str(ctx.exception))

    def test_missing_name_rejected(self):
        with self.assertRaises(SchemaError):
            validate_case({"prompts": ["do it"]})

    def test_check_command_timeout_bounds_enforced(self):
        with self.assertRaises(SchemaError):
            validate_case({"name": "x", "check_command_timeout": -1})
        with self.assertRaises(SchemaError):
            validate_case({"name": "x", "check_command_timeout": 999999})
        validate_case({"name": "x", "check_command_timeout": 120})

    def test_difficulty_range_enforced(self):
        with self.assertRaises(SchemaError):
            validate_case({"name": "x", "difficulty": 0})
        with self.assertRaises(SchemaError):
            validate_case({"name": "x", "difficulty": 6})
        validate_case({"name": "x", "difficulty": 3})

    def test_setup_files_must_be_string_map(self):
        with self.assertRaises(SchemaError):
            validate_case({"name": "x", "setup_files": ["not", "a", "map"]})
        with self.assertRaises(SchemaError):
            validate_case({"name": "x", "setup_files": {"a.py": 123}})

    def test_expected_files_unknown_key_rejected(self):
        with self.assertRaises(SchemaError):
            validate_case({"name": "x", "expected_files": [{"path_pattren": "a.py"}]})

    def test_git_config_path_rejected_everywhere(self):
        """P0-01: no case-controlled path field may reach .git - that's what
        cases.git_init_workspace's host-native `git add -A` would pick up as
        real repo config/hooks/filters and execute on the host."""
        for variant in (".git/config", ".GIT/config", "a/.git/hooks/pre-commit",
                        "a\\.git\\config"):
            with self.assertRaises(SchemaError):
                validate_case({"name": "x", "setup_files": {variant: "evil"}})
            with self.assertRaises(SchemaError):
                validate_case({"name": "x", "test_setup_files": {variant: "evil"}})
            with self.assertRaises(SchemaError):
                validate_case({"name": "x", "reference_solution": {variant: "evil"}})
            with self.assertRaises(SchemaError):
                validate_case({"name": "x", "broken_solutions": [
                    {"name": "v1", "files": {variant: "evil"}}]})
            with self.assertRaises(SchemaError):
                validate_case({"name": "x", "disruptions": [
                    {"after_prompt": 1, "write_files": {variant: "evil"}}]})
            with self.assertRaises(SchemaError):
                validate_case({"name": "x", "disruptions": [
                    {"after_prompt": 1, "delete_files": [variant]}]})

    def test_path_traversal_rejected_everywhere(self):
        """P0-02: a '..' segment must be rejected in every path-bearing case
        field, not just setup_files - it has no glob metacharacter, so
        drivers.openai_chat.concrete_target's basename-only stripping would
        otherwise pass it straight through to a workspace-escaping write."""
        for variant in ("../outside.py", "a/../../outside.py", "/etc/passwd", "C:/x.py"):
            with self.assertRaises(SchemaError):
                validate_case({"name": "x", "setup_files": {variant: "x"}})
            with self.assertRaises(SchemaError):
                validate_case({"name": "x", "expected_files": [{"path_pattern": variant}]})

    def test_windows_alias_paths_rejected_everywhere(self):
        """P1-05: none of these produce a working command-execution bypass
        on their own - the traversal/.git/absolute checks already close
        those - but each is a real Windows normalization trap: a trailing
        space/dot gets silently stripped by the OS on create (so the
        validated name and the real on-disk name differ), a reserved
        device name is illegal to create at all, and ':' outside a drive
        prefix addresses an NTFS alternate data stream rather than the
        file itself."""
        variants = (
            "secret.txt ",              # trailing space
            "secret.txt.",              # trailing dot
            "sub /file.py",             # trailing space on a DIRECTORY segment
            "CON", "con.txt", "Con.PY",  # reserved device name, case-insensitive
            "COM1.py", "lpt9",
            "file.txt:hidden",          # ADS syntax
        )
        for variant in variants:
            with self.subTest(variant=variant), self.assertRaises(SchemaError):
                validate_case({"name": "x", "setup_files": {variant: "x"}})
            with self.subTest(variant=variant, field="path_pattern"), self.assertRaises(SchemaError):
                validate_case({"name": "x", "expected_files": [{"path_pattern": variant}]})
            with self.subTest(variant=variant, field="file_exists"), self.assertRaises(SchemaError):
                validate_case({"name": "x", "disruptions": [{"when": {"file_exists": variant}}]})

    def test_windows_alias_check_does_not_reject_ordinary_names(self):
        """Confirms the new checks are narrowly targeted - real filenames
        that merely CONTAIN a reserved word or a dot are unaffected."""
        for ok in ("console.py", "constants.py", "nullable.py", "auxiliary.py",
                   "file.name.with.dots.py", "sub/nested/file.py"):
            with self.subTest(ok=ok):
                validate_case({"name": "x", "setup_files": {ok: "x"}})

    def test_broken_solutions_shape_enforced(self):
        with self.assertRaises(SchemaError):
            validate_case({"name": "x", "broken_solutions": [{"files": {}}]})  # missing name
        validate_case({"name": "x", "broken_solutions": [{"name": "v1", "files": {"a.py": "x"}}]})

    def test_valid_scenario_passes(self):
        validate_scenario({"name": "x", "driver": "aider",
                           "backend": {"kind": "ollama", "model": "llama3.2"}})

    def test_unknown_scenario_key_rejected(self):
        with self.assertRaises(SchemaError) as ctx:
            validate_scenario({"name": "x", "driver": "aider", "modle": "typo"})
        self.assertIn("modle", str(ctx.exception))

    def test_invalid_backend_kind_rejected(self):
        with self.assertRaises(SchemaError):
            validate_scenario({"name": "x", "driver": "aider", "backend": {"kind": "anthropic"}})

    def test_cases_empty_list_vs_none_both_valid(self):
        validate_scenario({"name": "x", "driver": "aider", "cases": None})
        validate_scenario({"name": "x", "driver": "aider", "cases": []})
        with self.assertRaises(SchemaError):
            validate_scenario({"name": "x", "driver": "aider", "cases": "not-a-list"})

    def test_generation_params_accepted_and_range_checked(self):
        """P2-02: temperature/top_p/seed are validated the same way num_ctx
        already was - a real range check, not just a type check, since an
        out-of-range value reaches a real backend request otherwise."""
        validate_scenario({"name": "x", "driver": "aider",
                           "backend": {"temperature": 0.7, "top_p": 0.9, "seed": 42}})
        validate_scenario({"name": "x", "driver": "aider",
                           "backend": {"temperature": 0, "top_p": 1, "seed": 0}})
        for bad in ({"temperature": -0.1}, {"temperature": 2.1}, {"temperature": "hot"}, {"temperature": True}):
            with self.subTest(bad=bad):
                with self.assertRaises(SchemaError):
                    validate_scenario({"name": "x", "driver": "aider", "backend": bad})
        for bad in ({"top_p": 0}, {"top_p": 1.1}, {"top_p": "high"}, {"top_p": False}):
            with self.subTest(bad=bad):
                with self.assertRaises(SchemaError):
                    validate_scenario({"name": "x", "driver": "aider", "backend": bad})
        for bad in ({"seed": 1.5}, {"seed": "seven"}, {"seed": True}):
            with self.subTest(bad=bad):
                with self.assertRaises(SchemaError):
                    validate_scenario({"name": "x", "driver": "aider", "backend": bad})

    def test_duplicate_case_names_rejected(self):
        with self.assertRaises(SchemaError):
            validate_unique_case_names([{"name": "a"}, {"name": "b"}, {"name": "a"}])
        validate_unique_case_names([{"name": "a"}, {"name": "b"}])

    def test_load_cases_rejects_malformed_case_file(self):
        d = Path(tempfile.mkdtemp(prefix="optarena_test_badcase_"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "bad.json").write_text(json.dumps({"name": "x", "chekc_command": "echo hi"}),
                                    encoding="utf-8")
        with self.assertRaises(SchemaError):
            load_cases(cases_dir=str(d))

    def test_load_cases_rejects_duplicate_names_across_files(self):
        d = Path(tempfile.mkdtemp(prefix="optarena_test_dupcase_"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / "a1.json").write_text(json.dumps({"name": "dup", "prompts": ["x"]}), encoding="utf-8")
        (d / "a2.json").write_text(json.dumps({"name": "dup", "prompts": ["y"]}), encoding="utf-8")
        with self.assertRaises(SchemaError):
            load_cases(cases_dir=str(d))

    def test_scenario_from_file_rejects_malformed_scenario(self):
        d = Path(tempfile.mkdtemp(prefix="optarena_test_badsc_"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        f = d / "bad.json"
        f.write_text(json.dumps({"name": "x", "driver": "aider", "timeout": -5}), encoding="utf-8")
        with self.assertRaises(SchemaError) as ctx:
            Scenario.from_file(f)
        self.assertIn(str(f), str(ctx.exception))


class DockerImageRegistryTests(unittest.TestCase):
    def test_base_maps_to_default_image_and_top_level_dockerfile(self):
        self.assertEqual(DOCKER_IMAGES["base"], DOCKER_IMAGE_DEFAULT)
        self.assertTrue(str(dockerfile_for("base")).endswith("Dockerfile"))
        self.assertNotIn("base", str(dockerfile_for("base")))

    def test_per_language_tracks_get_their_own_subdirectory(self):
        for lang in ("python", "node", "jvm", "go", "rust", "dotnet"):
            self.assertIn(lang, DOCKER_IMAGES)
            path = dockerfile_for(lang)
            self.assertEqual(path.parent.name, lang)
            self.assertEqual(path.name, "Dockerfile")


class MultiImageSandboxTests(unittest.TestCase):
    """
    A run whose cases span more than one language needs one live container
    PER distinct image, not just one overall - `_active_sandboxes` is keyed
    by image tag precisely so `run_check_command` can route each case to the
    container that actually has its toolchain.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="optarena_test_multiimg_"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / "case_py").mkdir()
        (self.root / "case_go").mkdir()
        import optarena._cases._sandbox as cases_mod
        self.cases_mod = cases_mod
        self._orig = (cases_mod._docker_checked_at, cases_mod._docker_ok,
                      cases_mod._docker_warned, dict(cases_mod._active_sandboxes))
        cases_mod._docker_checked_at = -1.0
        cases_mod._docker_ok = False
        cases_mod._docker_warned = False
        cases_mod._active_sandboxes.clear()

    def tearDown(self):
        (self.cases_mod._docker_checked_at, self.cases_mod._docker_ok,
         self.cases_mod._docker_warned, sandboxes) = self._orig
        self.cases_mod._active_sandboxes.clear()
        self.cases_mod._active_sandboxes.update(sandboxes)

    def test_two_images_register_independently_and_route_correctly(self):
        started = []

        def fake_run(args, **kwargs):
            if args[:2] == ["docker", "info"]:
                return mock.Mock(returncode=0)
            if args[:3] == ["docker", "image", "inspect"]:
                return mock.Mock(returncode=0)
            if args[:3] == ["docker", "run", "-d"]:
                started.append(args[args.index("--name") + 1])
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args[:2] == ["docker", "exec"]:
                # route assertion: the container name execed into must belong
                # to the image this case actually asked for
                image_hint = "py" if "case_py" in " ".join(args) else "go"
                return mock.Mock(returncode=0, stdout=f"ran-on-{image_hint}", stderr="")
            if args[:2] == ["docker", "stop"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected subprocess call: {args}")

        with mock.patch.object(self.cases_mod.subprocess, "run", side_effect=fake_run), \
             mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPTARENA_DISABLE_SANDBOX", None)
            py_sandbox = DockerSandbox(self.root, image="optarena-tester-python:latest")
            go_sandbox = DockerSandbox(self.root, image="optarena-tester-go:latest")
            py_sandbox.start()
            go_sandbox.start()
            try:
                self.assertEqual(len(started), 2, "expected one container per distinct image")
                self.assertIs(self.cases_mod._active_sandboxes["optarena-tester-python:latest"], py_sandbox)
                self.assertIs(self.cases_mod._active_sandboxes["optarena-tester-go:latest"], go_sandbox)

                py_case = {"check_command": "echo hi", "image": "optarena-tester-python:latest"}
                go_case = {"check_command": "echo hi", "image": "optarena-tester-go:latest"}
                _, py_info = run_check_command(py_case, self.root / "case_py")
                _, go_info = run_check_command(go_case, self.root / "case_go")
                self.assertEqual(py_info["image"], "optarena-tester-python:latest")
                self.assertEqual(go_info["image"], "optarena-tester-go:latest")
            finally:
                py_sandbox.stop()
                go_sandbox.stop()

        self.assertNotIn("optarena-tester-python:latest", self.cases_mod._active_sandboxes)
        self.assertNotIn("optarena-tester-go:latest", self.cases_mod._active_sandboxes)


class StoreTests(unittest.TestCase):
    def test_atomic_write_and_index(self):
        from optarena.store import _write_atomic
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        target = d / "x.json"
        _write_atomic(target, json.dumps({"ok": True}))
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"ok": True})
        self.assertFalse(list(d.glob("*.tmp")))


class RunIdCollisionTests(unittest.TestCase):
    """Phase 3.4: run_id collisions must fail loudly, not silently overwrite
    a saved run; an ambiguous substring lookup must not silently pick one."""

    def setUp(self):
        import optarena.store as store_mod
        self.store = store_mod
        results_dir = Path(tempfile.mkdtemp(prefix="optarena_test_results_"))
        self.addCleanup(shutil.rmtree, results_dir, ignore_errors=True)
        self._orig = (store_mod.RESULTS_DIR, store_mod.RUNS_DIR)
        store_mod.RESULTS_DIR = results_dir
        store_mod.RUNS_DIR = results_dir / "runs"
        self.addCleanup(self._restore)

    def _restore(self):
        self.store.RESULTS_DIR, self.store.RUNS_DIR = self._orig

    @staticmethod
    def _record(run_id):
        return mock.Mock(run_id=run_id, to_dict=lambda: {"run_id": run_id, "scenario": {"name": "x"}})

    def test_save_run_refuses_to_overwrite_existing_run_id(self):
        self.store.save_run(self._record("run-1"))
        with self.assertRaises(FileExistsError):
            self.store.save_run(self._record("run-1"))

    def test_load_run_exact_match_wins_even_with_ambiguous_substrings(self):
        self.store.save_run(self._record("20260101-000000_x_aaaaaaaa"))
        self.store.save_run(self._record("20260101-000000_x_bbbbbbbb"))
        rec = self.store.load_run("20260101-000000_x_aaaaaaaa")
        self.assertEqual(rec["run_id"], "20260101-000000_x_aaaaaaaa")

    def test_load_run_ambiguous_substring_raises_not_silently_picks_newest(self):
        self.store.save_run(self._record("20260101-000000_x_aaaaaaaa"))
        self.store.save_run(self._record("20260101-000001_x_bbbbbbbb"))
        with self.assertRaises(ValueError) as ctx:
            self.store.load_run("x")
        self.assertIn("matches 2 runs", str(ctx.exception))

    def test_load_run_unambiguous_substring_still_works(self):
        self.store.save_run(self._record("20260101-000000_uniquename_aaaaaaaa"))
        rec = self.store.load_run("uniquename")
        self.assertEqual(rec["run_id"], "20260101-000000_uniquename_aaaaaaaa")


class ConfigurableResultsDirTests(unittest.TestCase):
    """Phase 3.4: results location must be overridable (--results-dir /
    OPTARENA_RESULTS_DIR), and every consumer (store, compare) must see the
    SAME overridden location - not a stale value captured at import time."""

    def setUp(self):
        import optarena.store as store_mod
        self.store = store_mod
        self._orig = (store_mod.RESULTS_DIR, store_mod.RUNS_DIR)
        self.addCleanup(self._restore)

    def _restore(self):
        self.store.RESULTS_DIR, self.store.RUNS_DIR = self._orig

    def test_set_results_dir_updates_runs_dir_too(self):
        new_dir = Path(tempfile.mkdtemp(prefix="optarena_test_newresults_"))
        self.addCleanup(shutil.rmtree, new_dir, ignore_errors=True)
        self.store.set_results_dir(new_dir)
        self.assertEqual(self.store.RESULTS_DIR, new_dir)
        self.assertEqual(self.store.RUNS_DIR, new_dir / "runs")

    def test_compare_module_sees_overridden_results_dir(self):
        # compare.py must reference store.RESULTS_DIR live (via `from . import
        # store`), not a `from .store import RESULTS_DIR` name binding that
        # would freeze at whatever RESULTS_DIR was when compare.py was first
        # imported.
        from optarena import compare as compare_mod
        new_dir = Path(tempfile.mkdtemp(prefix="optarena_test_cmpresults_"))
        self.addCleanup(shutil.rmtree, new_dir, ignore_errors=True)
        self.store.set_results_dir(new_dir)
        cmp = {"a": {"label": "a"}, "b": {"label": "b"}}
        path = compare_mod.save_comparison(cmp)
        self.assertTrue(str(path).startswith(str(new_dir)))

    def test_cli_results_dir_flag_overrides_default(self):
        from optarena.cli import main
        new_dir = Path(tempfile.mkdtemp(prefix="optarena_test_cliresults_"))
        self.addCleanup(shutil.rmtree, new_dir, ignore_errors=True)
        rc = main(["--results-dir", str(new_dir), "list", "runs"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.store.RESULTS_DIR, new_dir)


class RichMetricsTests(unittest.TestCase):
    """diff_stats (files/lines changed) and classify_failure (syntax/compile/assertion/timeout bucketing)."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def test_diff_stats_new_file_counts_full_length(self):
        (self.ws / "new.py").write_text("a\nb\nc\n", encoding="utf-8")
        stats = diff_stats({"setup_files": {}}, ["new.py"], self.ws)
        self.assertEqual(stats["files_changed"], 1)
        self.assertEqual(stats["lines_changed_approx"], 3)

    def test_diff_stats_modified_file_counts_delta(self):
        original = "a\nb\n"
        (self.ws / "utils.py").write_text("a\nb\nc\nd\n", encoding="utf-8")  # +2 lines
        stats = diff_stats({"setup_files": {"utils.py": original}}, ["utils.py"], self.ws)
        self.assertEqual(stats["lines_changed_approx"], 2)

    def test_diff_stats_same_length_edit_still_counts_as_touched(self):
        original = "a\nb\n"
        (self.ws / "utils.py").write_text("x\ny\n", encoding="utf-8")  # same line count, content changed
        stats = diff_stats({"setup_files": {"utils.py": original}}, ["utils.py"], self.ws)
        self.assertEqual(stats["lines_changed_approx"], 1)  # not 0 - a real edit happened

    def test_classify_failure_syntax_error(self):
        info = {"ran": True, "exit_code": 1, "output": "SyntaxError: invalid syntax", "check_command": "python3 x.py"}
        self.assertEqual(classify_failure(info), "syntax_error")

    def test_classify_failure_compile_error(self):
        info = {"ran": True, "exit_code": 1,
                "output": "factorial.c:3:5: error: expected ';' before 'return'",
                "check_command": "gcc factorial.c -o a.out"}
        self.assertEqual(classify_failure(info), "compile_error")

    def test_classify_failure_assertion(self):
        info = {"ran": True, "exit_code": 1, "output": "AssertionError: expected 5 got 4", "check_command": "python3 t.py"}
        self.assertEqual(classify_failure(info), "assertion_failure")

    def test_classify_failure_non_gcc_compilers(self):
        for output, cmd in (
            ("error[E0308]: mismatched types", "cargo test --offline"),
            ("Program.cs(3,7): error CS1002: ; expected", "dotnet test tests/tests.csproj"),
            ("[ERROR] COMPILATION ERROR : cannot find symbol", "mvn -o -q test"),
            ("./main.go:7:2: undefined: ClasifyOrderPriority", "go test ./..."),
        ):
            info = {"ran": True, "exit_code": 1, "output": output, "check_command": cmd}
            self.assertEqual(classify_failure(info), "compile_error", output)

    def test_classify_failure_none_when_passed(self):
        info = {"ran": True, "exit_code": 0, "output": "PASS", "check_command": "python3 t.py"}
        self.assertIsNone(classify_failure(info))

    def test_evaluate_case_attaches_diff_and_failure_class(self):
        (self.ws / "add.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        case = {
            "expected_files": [{"path_pattern": "add.py"}],
            "test_setup_files": {"test_add.py": "import add\nassert add.add(2, 3) == 5\n"},
            "check_command": f'"{sys.executable}" test_add.py',
        }
        with mock.patch.dict(os.environ, {"OPTARENA_DISABLE_SANDBOX": "1"}):
            failures, oracle = evaluate_case(case, ["add.py"], self.ws)
        self.assertTrue(failures)
        self.assertIn("diff", oracle)
        self.assertEqual(oracle["diff"]["files_changed"], 1)
        self.assertEqual(oracle["failure_class"], "assertion_failure")


class PricingTests(unittest.TestCase):
    """Token -> USD cost: paid models priced, local/unknown are free."""

    def test_priced_model(self):
        # 1M prompt + 1M completion at claude-opus (15, 75) = 90.0
        self.assertAlmostEqual(estimate_cost("claude-opus-4.8", 1_000_000, 1_000_000), 90.0)

    def test_specific_key_wins_over_generic(self):
        # gpt-4o-mini must not match the pricier gpt-4o entry
        self.assertEqual(price_for("gpt-4o-mini"), (0.15, 0.6))

    def test_local_backend_is_free(self):
        self.assertTrue(is_local_backend("http://localhost:11434"))
        self.assertEqual(
            estimate_cost("claude-opus", 1_000_000, 1_000_000, base_url="http://localhost:11434"),
            0.0,
        )

    def test_unknown_remote_model_cost_is_unknown_not_free(self):
        """P2-05: an unpriced model on a non-local backend must report None
        (unknown), never 0.0 - 0.0 is reserved for a confirmed-free/local
        backend, and conflating the two used to make an unpriced remote run
        silently report as costing nothing."""
        self.assertIsNone(estimate_cost("gemma3:1b", 5000, 5000))
        self.assertIsNone(price_for("gemma3:1b"))

    def test_local_hostname_substring_not_falsely_matched(self):
        # H-07: a lookalike domain must not be treated as the real localhost
        # just because it contains "localhost" as a substring.
        self.assertFalse(is_local_backend("http://localhost.attacker.example"))
        self.assertFalse(is_local_backend("http://evil.example/?x=localhost"))
        self.assertFalse(is_local_backend("http://notlocalhost:11434"))

    def test_local_hostname_exact_matches_still_recognized(self):
        self.assertTrue(is_local_backend("http://localhost:11434"))
        self.assertTrue(is_local_backend("http://127.0.0.1:11434/v1"))
        self.assertTrue(is_local_backend("localhost:11434"))  # no scheme


class CostAggregationTests(unittest.TestCase):
    def test_aggregate_sums_cost_and_files(self):
        cases = [
            {"name": "a", "passed": True, "duration_s": 1.0, "files": ["x.py"],
             "extra": {"cost_usd": 0.10, "total_tokens": 100}},
            {"name": "b", "passed": False, "duration_s": 2.0, "files": ["y.py", "z.py"],
             "extra": {"cost_usd": 0.05, "total_tokens": 200}},
        ]
        s = aggregate(cases)
        self.assertEqual(s["total_cost_usd"], 0.15)
        self.assertEqual(s["mean_files_changed"], 1.5)
        self.assertEqual(s["total_tokens"], 300)

    def test_free_run_reports_none_cost(self):
        cases = [{"name": "a", "passed": True, "duration_s": 1.0, "files": ["x"], "extra": {}}]
        self.assertIsNone(aggregate(cases)["total_cost_usd"])

    def test_unpriced_case_flagged_not_silently_zeroed(self):
        """P2-05: a case that made real (priced-model-shaped) token usage but
        got cost_usd=None (unknown pricing) must surface as 'unpriced', not
        just vanish into the sum as if it cost nothing."""
        cases = [
            {"name": "a", "passed": True, "duration_s": 1.0, "files": ["x.py"],
             "extra": {"cost_usd": 0.10, "prompt_tokens": 100, "completion_tokens": 10}},
            {"name": "b", "passed": True, "duration_s": 1.0, "files": ["y.py"],
             "extra": {"cost_usd": None, "prompt_tokens": 200, "completion_tokens": 20}},
        ]
        s = aggregate(cases)
        self.assertEqual(s["total_cost_usd"], 0.10)
        self.assertEqual(s["unpriced_cases"], 1)

    def test_no_unpriced_flag_when_all_cases_priced(self):
        cases = [{"name": "a", "passed": True, "duration_s": 1.0, "files": ["x"],
                  "extra": {"cost_usd": 0.10, "prompt_tokens": 100}}]
        self.assertIsNone(aggregate(cases)["unpriced_cases"])


class CheaperVerdictTests(unittest.TestCase):
    """H-07: a run with NO cost data must never be declared 'cheaper' than a
    run with a real, known cost - None (unknown) is not the same as $0.00."""

    def test_no_verdict_when_one_side_has_no_cost_data(self):
        sa = {"total_cost_usd": None}
        sb = {"total_cost_usd": 0.50}
        self.assertIsNone(_cheaper("a", "b", sa, sb))
        self.assertIsNone(_cheaper("a", "b", sb, sa))

    def test_no_verdict_when_neither_side_has_cost_data(self):
        self.assertIsNone(_cheaper("a", "b", {"total_cost_usd": None}, {"total_cost_usd": None}))
        self.assertIsNone(_cheaper("a", "b", {}, {}))

    def test_verdict_when_both_sides_have_known_cost(self):
        self.assertEqual(_cheaper("a", "b", {"total_cost_usd": 0.10}, {"total_cost_usd": 0.20}), "a")
        self.assertEqual(_cheaper("a", "b", {"total_cost_usd": 0.20}, {"total_cost_usd": 0.10}), "b")

    def test_verdict_when_both_sides_are_genuinely_free(self):
        self.assertEqual(_cheaper("a", "b", {"total_cost_usd": 0.0}, {"total_cost_usd": 0.0}), "a")


class EligibleSummaryLinesTests(unittest.TestCase):
    """P2-04: `optarena run`'s console output previously showed only the raw
    pass rate - eligible_pass_rate/adjusted_pass_rate existed in the saved
    JSON but nowhere in what a user actually watches scroll by."""

    def test_no_extra_lines_when_nothing_excluded(self):
        from optarena.cli._run import _eligible_summary_lines
        s = {"cases": 5, "capability_excluded_cases": None, "infrastructure_errors": None}
        self.assertEqual(_eligible_summary_lines(s), [])

    def test_eligible_line_when_capability_excluded(self):
        from optarena.cli._run import _eligible_summary_lines
        s = {"cases": 3, "capability_excluded_cases": 1, "eligible_pass_rate": 0.5,
             "capability_exclusion_reasons": ["no file tools"],
             "infrastructure_errors": None}
        lines = _eligible_summary_lines(s)
        self.assertEqual(len(lines), 1)
        self.assertIn("eligible: 50%", lines[0])
        self.assertIn("2/3", lines[0])
        self.assertIn("no file tools", lines[0])

    def test_adjusted_line_when_infrastructure_errors(self):
        from optarena.cli._run import _eligible_summary_lines
        s = {"cases": 4, "capability_excluded_cases": None,
             "infrastructure_errors": 1, "adjusted_pass_rate": 0.667}
        lines = _eligible_summary_lines(s)
        self.assertEqual(len(lines), 1)
        self.assertIn("adjusted: 67%", lines[0])
        self.assertIn("3/4", lines[0])

    def test_both_lines_when_both_apply(self):
        from optarena.cli._run import _eligible_summary_lines
        s = {"cases": 4, "capability_excluded_cases": 1, "eligible_pass_rate": 1.0,
             "capability_exclusion_reasons": ["reason"],
             "infrastructure_errors": 1, "adjusted_pass_rate": 0.5}
        self.assertEqual(len(_eligible_summary_lines(s)), 2)


class DoctorVersionDriftJSONTests(unittest.TestCase):
    """
    P2-03: `doctor`'s version-drift note ("last tested with X, not
    re-verified against Y") existed only as a human-readable print - `_print`
    is a documented no-op under `--json`, so a scripted/CI caller of
    `optarena doctor --json` got NO version-drift signal at all, silently,
    even though the exact same information was computed. Found while wiring
    a CI job to gate on this signal: the JSON `checks` list had nothing to
    grep. Fixed by recording the same note as its own advisory row in
    `report["checks"]`, in both output modes.
    """

    def test_version_drift_note_appears_in_json_checks(self):
        from optarena.cli._doctor import cmd_doctor
        args = argparse.Namespace(base_url="http://unreachable.invalid:1", kind="ollama", json=True)
        with mock.patch("optarena.drivers.get_driver_version", return_value="9.9.9"):
            with mock.patch("shutil.which", return_value="/usr/bin/aider"):
                buf = _capture_stdout(lambda: cmd_doctor(args))
        report = json.loads(buf)
        version_checks = [c for c in report["checks"] if c["label"] == "aider version"]
        self.assertEqual(len(version_checks), 1)
        self.assertIn("not re-verified", version_checks[0]["detail"])
        self.assertFalse(version_checks[0]["ok"])
        self.assertFalse(version_checks[0]["required"])   # advisory - must not affect the exit code

    def test_no_drift_note_when_version_matches_tested_with(self):
        from optarena.cli._doctor import cmd_doctor
        args = argparse.Namespace(base_url="http://unreachable.invalid:1", kind="ollama", json=True)
        with mock.patch("optarena.drivers.get_driver_version", return_value="aider.EXE 0.86.2"):
            with mock.patch("shutil.which", return_value="/usr/bin/aider"):
                buf = _capture_stdout(lambda: cmd_doctor(args))
        report = json.loads(buf)
        version_checks = [c for c in report["checks"] if c["label"] == "aider version"]
        self.assertEqual(len(version_checks), 1)
        self.assertNotIn("not re-verified", version_checks[0]["detail"])
        self.assertTrue(version_checks[0]["ok"])

    def test_json_mode_still_prints_nothing_but_json(self):
        """--json must stay script-safe (A-28) - the new recording must not
        leak an extra human-readable print line onto stdout."""
        from optarena.cli._doctor import cmd_doctor
        args = argparse.Namespace(base_url="http://unreachable.invalid:1", kind="ollama", json=True)
        with mock.patch("optarena.drivers.get_driver_version", return_value="9.9.9"):
            with mock.patch("shutil.which", return_value="/usr/bin/aider"):
                buf = _capture_stdout(lambda: cmd_doctor(args))
        json.loads(buf)   # raises if anything but one JSON document was printed


def _capture_stdout(fn) -> str:
    import io
    from contextlib import redirect_stdout
    out = io.StringIO()
    with redirect_stdout(out):
        fn()
    return out.getvalue()


class ManifestTests(unittest.TestCase):
    """M-01: every run records an immutable case-set/oracle/trials identity."""

    def _run_with_cases(self, name, cases_dir, extra_case=None):
        made = Path(tempfile.mkdtemp(prefix="optarena_manifest_ws_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(made, ignore_errors=True))
        sc = Scenario(name=name, driver="aider", cases_dir=str(cases_dir))
        driver = mock.Mock(parallel_safe=False, caches_results=False)
        driver.run_case.return_value = CaseResult(name="c1", passed=True, duration_s=0.1)
        with mock.patch("optarena.runner._execution.get_driver", return_value=driver), \
             mock.patch("optarena.runner._execution.tempfile.mkdtemp", return_value=str(made)):
            return run_scenario(sc)

    def setUp(self):
        self.cases_dir = Path(tempfile.mkdtemp(prefix="optarena_manifest_cases_"))
        self.addCleanup(shutil.rmtree, self.cases_dir, ignore_errors=True)
        (self.cases_dir / "c1.json").write_text(json.dumps({
            "name": "c1", "prompts": ["do it"],
        }), encoding="utf-8")

    def test_manifest_recorded_on_run(self):
        rec = self._run_with_cases("x", self.cases_dir)
        man = rec.manifest
        self.assertEqual(man["oracle_version"], __import__("optarena.runner", fromlist=["ORACLE_VERSION"]).ORACLE_VERSION)
        self.assertEqual(man["case_names"], ["c1"])
        self.assertEqual(man["case_count"], 1)
        self.assertEqual(man["trials"], 1)
        self.assertTrue(man["case_set_hash"])

    def test_case_edit_changes_case_set_hash(self):
        rec1 = self._run_with_cases("x", self.cases_dir)
        # Editing the case's content must change the hash - a comparison
        # across the edit is no longer like-for-like.
        (self.cases_dir / "c1.json").write_text(json.dumps({
            "name": "c1", "prompts": ["do it DIFFERENTLY"],
        }), encoding="utf-8")
        rec2 = self._run_with_cases("x", self.cases_dir)
        self.assertNotEqual(rec1.manifest["case_set_hash"], rec2.manifest["case_set_hash"])


class ComparisonValidityTests(unittest.TestCase):
    """M-02: a comparison of two runs that measured different things must not
    silently crown an overall winner."""

    @staticmethod
    def _man(case_set_hash="abc", oracle_version=1, trials=1):
        return {"manifest_version": 1, "oracle_version": oracle_version,
                "case_set_hash": case_set_hash, "case_count": 3, "trials": trials}

    @staticmethod
    def _run(label, man, pass_rate=1.0, mean=1.0):
        return {
            "run_id": label, "scenario": {"name": label},
            "cases": [], "manifest": man,
            "summary": {"pass_rate": pass_rate, "mean_duration_s": mean,
                        "cases": 3, "passed": 3, "failed": 0},
        }

    def test_same_manifest_is_comparable(self):
        c = manifest_compatibility(self._man(), self._man())
        self.assertTrue(c["comparable"])
        self.assertTrue(c["verified"])
        self.assertEqual(c["reasons"], [])

    def test_different_case_set_not_comparable(self):
        c = manifest_compatibility(self._man("abc"), self._man("xyz"))
        self.assertFalse(c["comparable"])
        self.assertTrue(any("case set" in r for r in c["reasons"]))

    def test_different_oracle_and_trials_flagged(self):
        c = manifest_compatibility(self._man(oracle_version=1, trials=1),
                                   self._man(oracle_version=2, trials=5))
        self.assertFalse(c["comparable"])
        self.assertTrue(any("oracle version" in r for r in c["reasons"]))
        self.assertTrue(any("trial count" in r for r in c["reasons"]))

    def test_missing_manifest_is_unverified_not_blocking(self):
        c = manifest_compatibility(None, self._man())
        self.assertTrue(c["comparable"])
        self.assertFalse(c["verified"])

    def test_verdict_suppressed_when_incompatible(self):
        cmp = compare_runs(self._run("a", self._man("abc"), pass_rate=1.0),
                           self._run("b", self._man("xyz"), pass_rate=0.5))
        self.assertTrue(cmp["verdict"]["suppressed"])
        self.assertIsNone(cmp["verdict"]["more_accurate"])
        self.assertIsNone(cmp["verdict"]["faster"])
        self.assertIn("not comparable", format_table(cmp).lower())

    def test_force_overrides_suppression(self):
        cmp = compare_runs(self._run("a", self._man("abc"), pass_rate=1.0),
                           self._run("b", self._man("xyz"), pass_rate=0.5),
                           force=True)
        self.assertFalse(cmp["verdict"]["suppressed"])
        self.assertEqual(cmp["verdict"]["more_accurate"], "a")

    def test_compatible_runs_produce_a_winner(self):
        cmp = compare_runs(self._run("a", self._man("abc"), pass_rate=1.0),
                           self._run("b", self._man("abc"), pass_rate=0.5))
        self.assertFalse(cmp["verdict"]["suppressed"])
        self.assertEqual(cmp["verdict"]["more_accurate"], "a")


class RegressionTests(unittest.TestCase):
    """optarena regression: named regressed/improved cases, exit-code-worthy verdict."""

    @staticmethod
    def _run(name, cases):
        # cases: list of (name, passed, duration_s)
        return {
            "run_id": name, "scenario": {"name": name},
            "cases": [{"name": n, "passed": p, "duration_s": d, "failures": [] if p else ["nope"]}
                      for n, p, d in cases],
            "summary": {
                "cases": len(cases),
                "passed": sum(1 for _, p, _ in cases if p),
                "failed": sum(1 for _, p, _ in cases if not p),
                "pass_rate": round(sum(1 for _, p, _ in cases if p) / len(cases), 3),
                "mean_duration_s": round(sum(d for _, _, d in cases) / len(cases), 2),
                "total_tokens": 1000,
            },
        }

    def test_identifies_regressed_and_improved_cases_by_name(self):
        run_a = self._run("a", [("login", True, 1.0), ("oauth", True, 1.0), ("signup", False, 1.0)])
        run_b = self._run("b", [("login", False, 1.0), ("oauth", True, 1.0), ("signup", True, 1.0)])
        run_b["summary"]["total_tokens"] = 800
        cmp = compare_runs(run_a, run_b)
        summary = regression_summary(cmp)
        self.assertEqual(summary["regressed_cases"], ["login"])
        self.assertEqual(summary["improved_cases"], ["signup"])
        self.assertEqual(summary["token_delta"], -200)
        self.assertAlmostEqual(summary["token_delta_pct"], -20.0)

    def test_case_missing_from_b_is_not_a_regression(self):
        # A-03: a case run B simply didn't include (a --cases/--language
        # subset compared against a full baseline) used to be reported as a
        # regression and failed the CI gate, because `not None` is True.
        run_a = self._run("a", [("login", True, 1.0), ("oauth", True, 1.0)])
        run_b = self._run("b", [("login", True, 1.0)])
        summary = regression_summary(compare_runs(run_a, run_b))
        self.assertEqual(summary["regressed_cases"], [])
        self.assertEqual(summary["improved_cases"], [])
        self.assertEqual(summary["missing_in_b_cases"], ["oauth"])
        self.assertEqual(summary["n_discordant"], 0)
        self.assertIn("only in a", format_regression(summary))

    def test_case_missing_from_a_is_not_an_improvement(self):
        run_a = self._run("a", [("login", True, 1.0)])
        run_b = self._run("b", [("login", True, 1.0), ("oauth", True, 1.0)])
        summary = regression_summary(compare_runs(run_a, run_b))
        self.assertEqual(summary["improved_cases"], [])
        self.assertEqual(summary["missing_in_a_cases"], ["oauth"])

    def test_regression_and_comparison_agree_on_discordant_count(self):
        # The two code paths computed this differently; they must not.
        run_a = self._run("a", [("login", True, 1.0), ("oauth", True, 1.0), ("gone", True, 1.0)])
        run_b = self._run("b", [("login", False, 1.0), ("oauth", True, 1.0)])
        cmp = compare_runs(run_a, run_b)
        summary = regression_summary(cmp)
        self.assertEqual(summary["n_discordant"], cmp["verdict"]["n_discordant"])
        self.assertEqual(summary["accuracy_p_value"], cmp["verdict"]["accuracy_p_value"])

    def test_cost_delta_reported_and_formatted(self):
        run_a = self._run("a", [("login", True, 1.0)])
        run_b = self._run("b", [("login", True, 1.0)])
        run_a["summary"]["total_cost_usd"] = 0.50
        run_b["summary"]["total_cost_usd"] = 0.40
        summary = regression_summary(compare_runs(run_a, run_b))
        self.assertAlmostEqual(summary["cost_delta"], -0.10)
        self.assertAlmostEqual(summary["cost_delta_pct"], -20.0)
        text = format_regression(summary)
        self.assertIn("cost", text)
        self.assertIn("$0.50", text)
        self.assertIn("$0.40", text)

    def test_cost_omitted_when_neither_run_priced(self):
        run_a = self._run("a", [("login", True, 1.0)])
        run_b = self._run("b", [("login", True, 1.0)])
        summary = regression_summary(compare_runs(run_a, run_b))
        self.assertIsNone(summary["cost_a"])
        self.assertNotIn("cost", format_regression(summary))

    def test_format_regression_lists_cases_by_name(self):
        run_a = self._run("a", [("login", True, 1.0)])
        run_b = self._run("b", [("login", False, 2.0)])
        text = format_regression(regression_summary(compare_runs(run_a, run_b)))
        self.assertIn("login", text)
        self.assertIn("regressed cases", text)

    def test_no_regressions_reports_none(self):
        run_a = self._run("a", [("x", False, 1.0)])
        run_b = self._run("b", [("x", True, 1.0)])
        summary = regression_summary(compare_runs(run_a, run_b))
        self.assertEqual(summary["regressed_cases"], [])
        text = format_regression(summary)
        self.assertIn("(none)", text)


class TrajectoryStatsTests(unittest.TestCase):
    """Off-target-edit detection: files the task never asked for."""

    def _case(self):
        return {
            "setup_files": {"app.py": "x = 1\n"},
            "expected_files": [{"path_pattern": "app.py", "content_patterns": ["x"]}],
        }

    def test_only_expected_file_touched_is_clean(self):
        from optarena.cases import trajectory_stats
        traj = trajectory_stats(self._case(), ["app.py"], Path("/tmp"))
        self.assertEqual(traj["off_target_count"], 0)
        self.assertEqual(traj["off_target_files"], [])

    def test_extra_file_is_off_target(self):
        from optarena.cases import trajectory_stats
        traj = trajectory_stats(self._case(), ["app.py", "scratch.txt", "notes.md"], Path("/tmp"))
        self.assertEqual(traj["off_target_count"], 2)
        self.assertIn("scratch.txt", traj["off_target_files"])

    def test_setup_file_is_not_off_target(self):
        # A fix case whose expected file IS a setup file that got modified:
        # still counts as expected (matches the pattern), never off-target.
        from optarena.cases import trajectory_stats
        traj = trajectory_stats(self._case(), ["app.py"], Path("/tmp"))
        self.assertEqual(traj["off_target_count"], 0)

    def test_glob_path_pattern_matches(self):
        from optarena.cases import trajectory_stats
        case = {"expected_files": [{"path_pattern": "**/Foo.java"}]}
        traj = trajectory_stats(case, ["src/main/java/Foo.java", "junk.txt"], Path("/tmp"))
        self.assertEqual(traj["off_target_count"], 1)
        self.assertEqual(traj["off_target_files"], ["junk.txt"])


class WilsonCiTests(unittest.TestCase):
    def test_bounds_and_zero(self):
        from optarena.metrics import wilson_ci
        self.assertEqual(wilson_ci(0, 0), (0.0, 0.0))
        lo, hi = wilson_ci(5, 10)
        self.assertTrue(0.0 <= lo < 0.5 < hi <= 1.0)

    def test_small_sample_wide_large_sample_narrow(self):
        from optarena.metrics import wilson_ci
        lo_s, hi_s = wilson_ci(1, 2)      # 50% on n=2
        lo_l, hi_l = wilson_ci(300, 600)  # 50% on n=600
        self.assertGreater(hi_s - lo_s, hi_l - lo_l)  # small sample is wider

    def test_all_pass_upper_is_one_lower_below_one(self):
        from optarena.metrics import wilson_ci
        lo, hi = wilson_ci(10, 10)
        self.assertEqual(hi, 1.0)
        self.assertLess(lo, 1.0)


class McNemarTests(unittest.TestCase):
    def test_no_discordant_is_one(self):
        from optarena.compare import mcnemar_exact_p
        self.assertEqual(mcnemar_exact_p(0, 0), 1.0)

    def test_symmetric_not_significant(self):
        from optarena.compare import mcnemar_exact_p
        self.assertGreater(mcnemar_exact_p(3, 3), 0.05)

    def test_strong_asymmetry_significant(self):
        from optarena.compare import mcnemar_exact_p
        # 10 improved, 0 regressed -> p = 2 * 0.5^10 ~= 0.002
        self.assertLess(mcnemar_exact_p(0, 10), 0.05)

    def test_p_never_exceeds_one(self):
        from optarena.compare import mcnemar_exact_p
        self.assertLessEqual(mcnemar_exact_p(1, 1), 1.0)


class AggregateTrajectoryTests(unittest.TestCase):
    def _case(self, name, passed, off_target=0, execution_ok=True, n_steps=None):
        oracle = {"trajectory": {"off_target_count": off_target, "off_target_files": []}}
        extra = {"oracle": oracle}
        if n_steps is not None:
            extra["n_steps"] = n_steps
        return {"name": name, "passed": passed, "duration_s": 1.0,
                "execution_ok": execution_ok, "files": [], "extra": extra}

    def test_clean_pass_counting(self):
        cases = [
            self._case("a", True, off_target=0),                 # clean pass
            self._case("b", True, off_target=2),                 # pass but off-target
            self._case("c", True, off_target=0, execution_ok=False),  # pass but dirty exit
            self._case("d", False, off_target=0),                # fail
        ]
        s = aggregate(cases)
        self.assertEqual(s["clean_passes"], 1)
        self.assertEqual(s["off_target_edits"], 2)
        self.assertEqual(s["passed"], 3)

    def test_pass_rate_ci_present(self):
        s = aggregate([self._case("a", True), self._case("b", False)])
        self.assertIn("pass_rate_ci", s)
        self.assertEqual(len(s["pass_rate_ci"]), 2)

    def test_mean_steps(self):
        cases = [self._case("a", True, n_steps=2), self._case("b", True, n_steps=4)]
        self.assertEqual(aggregate(cases)["mean_steps"], 3.0)

    def test_trajectory_via_trials(self):
        # trajectory carried in oracle_all_trials (a --trials merge)
        from optarena.metrics import case_trajectory, is_clean_pass
        c = {"passed": True, "execution_ok": True, "extra": {
            "oracle_all_trials": [
                {"trajectory": {"off_target_count": 0}},
                {"trajectory": {"off_target_count": 3}},
            ]}}
        self.assertEqual(case_trajectory(c)["off_target_count"], 3)
        self.assertFalse(is_clean_pass(c))


class CompareSignificanceTests(unittest.TestCase):
    def _run(self, name, results):
        cases = [{"name": n, "passed": p, "duration_s": 1.0,
                  "execution_ok": True, "files": [], "extra": {}} for n, p in results]
        return {"run_id": name, "scenario": {"name": name}, "summary": aggregate(cases),
                "cases": cases, "manifest": {"oracle_version": 1, "case_set_hash": "h",
                                             "trials": 1}}

    def test_significant_flip(self):
        a = self._run("a", [(f"c{i}", False) for i in range(10)])
        b = self._run("b", [(f"c{i}", True) for i in range(10)])
        cmp = compare_runs(a, b)
        self.assertTrue(cmp["verdict"]["accuracy_significant"])
        self.assertEqual(cmp["verdict"]["n_discordant"], 10)
        self.assertIn("SIGNIFICANT", format_table(cmp))

    def test_small_delta_not_significant(self):
        # one case flips out of ten -> not significant
        a = self._run("a", [(f"c{i}", i < 5) for i in range(10)])
        b = self._run("b", [(f"c{i}", i < 6) for i in range(10)])
        cmp = compare_runs(a, b)
        self.assertFalse(cmp["verdict"]["accuracy_significant"])


class CompareEligibleSetTests(unittest.TestCase):
    """P2-04: "cross-driver comparisons state the exact common eligible case
    set" - a case belongs there only if BOTH runs actually attempted it AND
    neither driver was structurally incapable of it (capability_excluded).
    Distinct from each run's own eligible_pass_rate: two drivers can each
    exclude a DIFFERENT case and so share no common eligible set at all even
    though both individually look fine."""

    def _run(self, name, cases):
        # cases: list of (name, passed, capability_excluded_reason_or_None)
        case_dicts = [
            {"name": n, "passed": p, "duration_s": 1.0, "execution_ok": True,
             "files": [], "extra": ({"capability_excluded": reason} if reason else {})}
            for n, p, reason in cases
        ]
        return {"run_id": name, "scenario": {"name": name}, "summary": aggregate(case_dicts),
                "cases": case_dicts, "manifest": None}

    def test_excluded_case_removed_from_common_eligible_set(self):
        a = self._run("a", [("x", True, None), ("y", False, "no file tools")])
        b = self._run("b", [("x", True, None), ("y", True, None)])
        cmp = compare_runs(a, b)
        self.assertEqual(cmp["verdict"]["eligible_cases"], ["x"])
        self.assertEqual(cmp["verdict"]["eligible_case_count"], 1)

    def test_all_eligible_when_nothing_excluded(self):
        a = self._run("a", [("x", True, None), ("y", False, None)])
        b = self._run("b", [("x", True, None), ("y", True, None)])
        cmp = compare_runs(a, b)
        self.assertEqual(cmp["verdict"]["eligible_cases"], ["x", "y"])
        self.assertEqual(cmp["verdict"]["eligible_case_count"], 2)

    def test_each_side_excluding_a_different_case_leaves_no_overlap(self):
        a = self._run("a", [("x", True, "reason A"), ("y", True, None)])
        b = self._run("b", [("x", True, None), ("y", True, "reason B")])
        cmp = compare_runs(a, b)
        self.assertEqual(cmp["verdict"]["eligible_cases"], [])
        self.assertEqual(cmp["verdict"]["eligible_case_count"], 0)

    def test_format_table_shows_eligible_line_only_when_it_differs(self):
        clean_a = self._run("a", [("x", True, None)])
        clean_b = self._run("b", [("x", True, None)])
        self.assertNotIn("eligible set", format_table(compare_runs(clean_a, clean_b)))

        excl_a = self._run("a", [("x", True, None), ("y", False, "no file tools")])
        excl_b = self._run("b", [("x", True, None), ("y", True, None)])
        table = format_table(compare_runs(excl_a, excl_b))
        self.assertIn("eligible set:  1/2", table)


class ReportArtifactTests(unittest.TestCase):
    def _run(self):
        return {
            "run_id": "r1", "started_at": "2026-07-22T10:00:00",
            "scenario": {"name": "s", "driver": "aider", "backend": {"model": "m"}},
            "summary": {"cases": 3, "passed": 1, "pass_rate": 0.333,
                        "pass_rate_ci": [0.06, 0.79], "clean_passes": 1, "mean_duration_s": 2.0},
            "cases": [
                {"name": "ok", "passed": True, "duration_s": 1.0, "failures": [], "error": None,
                 "extra": {"oracle": {"output": "PASS", "trajectory": {"off_target_count": 0}}}},
                {"name": "bad", "passed": False, "duration_s": 2.0, "failures": ["assert x"], "error": None,
                 "extra": {"oracle": {"trajectory": {"off_target_count": 1}},
                           "security": {"findings": [{"rule": "py-os-system", "level": "warning",
                                        "title": "os.system()", "message": "os.system()",
                                        "file": "a.py", "line": 3}]}}},
                {"name": "err", "passed": False, "duration_s": 3.0, "failures": [], "error": "timeout"},
            ],
        }

    def test_junit_wellformed_and_counts(self):
        import xml.etree.ElementTree as ET
        from optarena.report import to_junit_xml
        root = ET.fromstring(to_junit_xml(self._run()))
        self.assertEqual(root.get("tests"), "3")
        self.assertEqual(root.get("failures"), "1")
        self.assertEqual(root.get("errors"), "1")

    def test_html_self_contained(self):
        from optarena.report import to_html
        html = to_html(self._run())
        self.assertIn("<!doctype html>", html)
        self.assertIn("95% CI", html)
        self.assertNotIn("http://", html.replace("https://github.com", ""))  # no network assets

    def test_html_omits_eligible_tile_when_nothing_excluded(self):
        from optarena.report import to_html
        self.assertNotIn("Eligible pass rate", to_html(self._run()))
        self.assertNotIn("Adjusted pass rate", to_html(self._run()))

    def test_html_shows_eligible_and_adjusted_tiles(self):
        """P2-04: the raw "Pass rate" tile is always over every case - when
        the summary carries capability_excluded_cases/infrastructure_errors,
        the report must say so explicitly instead of only in the raw JSON."""
        from optarena.report import to_html
        run = self._run()
        run["summary"].update({
            "capability_excluded_cases": 1, "eligible_pass_rate": 0.5,
            "infrastructure_errors": 1, "adjusted_pass_rate": 0.5,
        })
        html = to_html(run)
        self.assertIn("Eligible pass rate", html)
        self.assertIn("50%", html)
        self.assertIn("2/3", html)   # eligible denominator: 3 cases - 1 excluded
        self.assertIn("Adjusted pass rate", html)

    def test_sarif_valid(self):
        from optarena.report import to_sarif
        s = json.loads(to_sarif(self._run()))
        self.assertEqual(s["version"], "2.1.0")
        self.assertEqual(len(s["runs"][0]["results"]), 1)
        self.assertEqual(s["runs"][0]["results"][0]["ruleId"], "py-os-system")


class SecurityScanTests(unittest.TestCase):
    def test_detects_secret_and_injection(self):
        from optarena.security import scan_text
        py = 'API_KEY = "sk-abcdef0123456789abcdef"\nimport os\nos.system("x " + a)\n'
        rules = {f["rule"] for f in scan_text(py, "app.py")}
        self.assertIn("provider-api-key", rules)
        self.assertIn("py-os-system", rules)

    def test_ignores_placeholder_secret(self):
        from optarena.security import scan_text
        finds = scan_text('password = "changeme"\n', "app.py")
        self.assertEqual([f for f in finds if f["rule"] == "hardcoded-secret"], [])

    def test_sql_fstring_and_shell_true(self):
        from optarena.security import scan_text
        rules = {f["rule"] for f in scan_text(
            'cur.execute(f"SELECT * FROM t WHERE n=\'{n}\'")\nsubprocess.run("x", shell=True)\n', "d.py")}
        self.assertIn("py-sql-fstring", rules)
        self.assertIn("py-shell-true", rules)

    def test_js_rules_by_extension(self):
        from optarena.security import scan_text
        js = "exec(`echo ${x}`);\n"
        self.assertTrue(any(f["rule"] == "js-child-exec" for f in scan_text(js, "s.js")))
        # python rules must not fire on a .js file
        self.assertFalse(any(f["rule"].startswith("py-") for f in scan_text(js, "s.js")))

    def test_clean_file_no_findings(self):
        from optarena.security import scan_text
        self.assertEqual(scan_text("def add(a, b):\n    return a + b\n", "c.py"), [])

    def test_secret_finding_never_persists_the_canary(self):
        """P1-02: a secret-rule finding used to store up to 160 raw chars of
        the matched line - which contains the very secret it just detected.
        A canary value must never appear anywhere in the finding dict."""
        from optarena.security import scan_text
        canary = "sk-THIS_IS_A_CANARY_SECRET_VALUE_0123456789"
        finds = scan_text(f'API_KEY = "{canary}"\n', "app.py")
        secret_finds = [f for f in finds if f["rule"] == "provider-api-key"]
        self.assertTrue(secret_finds)
        for f in secret_finds:
            for value in f.values():
                self.assertNotIn(canary, str(value))
        self.assertIn("redacted", secret_finds[0]["snippet"])
        # a non-secret rule's snippet is unaffected - still the real line
        self.assertIn("os.system", scan_text('os.system("x " + a)\n', "app.py")[0]["snippet"])

    def test_scan_workspace_rollup(self):
        from optarena.security import scan_workspace
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.py").write_text('import os\nos.system("x " + a)\n')
            (root / "clean.py").write_text("x = 1\n")
            res = scan_workspace(["a.py", "clean.py"], root)
            self.assertEqual(res["counts"]["warning"], 1)
            self.assertEqual(res["total"], 1)


class PackTests(unittest.TestCase):
    def _cases_dir(self, d):
        p = Path(d) / "cases"
        p.mkdir()
        (p / "c1.json").write_text(json.dumps({
            "name": "c1", "prompts": ["do x"],
            "expected_files": [{"path_pattern": "x.py", "content_patterns": ["x"]}]}))
        return p

    def test_hash_stable_and_pack_roundtrip(self):
        from optarena import packs
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            pack = packs.build_pack(src, "mypack", "1.0.0")
            self.assertEqual(pack["case_count"], 1)
            self.assertEqual(pack["hash"], packs.content_hash(pack["cases"]))
            out = packs.write_pack(pack, Path(d) / "p.optpack.json")
            loaded = packs.load_pack(str(out))
            self.assertEqual(loaded["hash"], pack["hash"])

    def test_tamper_refused(self):
        from optarena import packs
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            pack = packs.build_pack(src, "mypack", "1.0.0")
            out = Path(d) / "p.json"
            bad = dict(pack)
            bad["cases"] = {"c1.json": {**pack["cases"]["c1.json"], "prompts": ["HACKED"]}}
            out.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                packs.load_pack(str(out))

    def test_plain_http_rejected_by_default(self):
        """P1-07: a pack fetched over plain http has no transport integrity
        against an on-path attacker - refused before any network call is
        made, not just discouraged."""
        from optarena import packs
        with self.assertRaises(ValueError) as ctx:
            packs.load_pack("http://example.com/x.optpack.json")
        self.assertIn("http", str(ctx.exception).lower())

    def test_plain_http_allowed_with_explicit_opt_in(self):
        """allow_insecure=True must get PAST the transport check - no real
        network call, just proving urlopen is actually reached instead of
        the ValueError firing first."""
        from optarena import packs
        with mock.patch("optarena.packs.urllib.request.urlopen") as m:
            m.side_effect = OSError("simulated: nothing listening")
            with self.assertRaises(OSError):
                packs.load_pack("http://127.0.0.1:1/x.optpack.json", allow_insecure=True)
            m.assert_called_once()

    def test_https_never_needs_the_opt_in(self):
        from optarena import packs
        with mock.patch("optarena.packs.urllib.request.urlopen") as m:
            m.side_effect = OSError("simulated: nothing listening")
            with self.assertRaises(OSError):
                packs.load_pack("https://example.invalid/x.optpack.json")
            m.assert_called_once()

    def test_install_idempotent_and_resolve(self):
        from optarena import packs
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            reg = Path(d) / "reg"
            pack = packs.build_pack(src, "mypack", "2.0.0")
            dest1 = packs.install_pack(pack, packs_dir=reg)
            dest2 = packs.install_pack(pack, packs_dir=reg)      # idempotent
            self.assertEqual(dest1, dest2)
            self.assertEqual(packs.resolve_pack("mypack", reg), dest1)
            self.assertEqual(packs.resolve_pack("mypack@2.0.0", reg), dest1)
            self.assertEqual(len(packs.list_installed(reg)), 1)

    def test_install_conflict_refused(self):
        from optarena import packs
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            reg = Path(d) / "reg"
            packs.install_pack(packs.build_pack(src, "p", "1.0.0"), packs_dir=reg)
            # different content, same name@version -> refused without force
            (src / "c2.json").write_text(json.dumps({
                "name": "c2", "prompts": ["y"],
                "expected_files": [{"path_pattern": "y.py", "content_patterns": ["y"]}]}))
            with self.assertRaises(FileExistsError):
                packs.install_pack(packs.build_pack(src, "p", "1.0.0"), packs_dir=reg)


class DisruptionTests(unittest.TestCase):
    def _case(self):
        return {"name": "d", "prompts": ["a", "b"],
                "disruptions": [
                    {"after_prompt": 1, "description": "cfg changed",
                     "write_files": {"config.py": "X = 2\n"}, "delete_files": ["old.txt"]}]}

    def test_fires_only_matching_after_prompt(self):
        from optarena.cases import apply_disruptions
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "config.py").write_text("X = 1\n")
            (root / "old.txt").write_text("stale")
            # after prompt 2: nothing scheduled -> no change
            self.assertEqual(apply_disruptions(self._case(), root, 2), [])
            self.assertEqual((root / "config.py").read_text(), "X = 1\n")
            self.assertTrue((root / "old.txt").exists())
            # after prompt 1: fires
            fired = apply_disruptions(self._case(), root, 1)
            self.assertEqual(fired, ["cfg changed"])
            self.assertEqual((root / "config.py").read_text(), "X = 2\n")
            self.assertFalse((root / "old.txt").exists())

    def test_apply_all_disruptions(self):
        from optarena.cases import apply_all_disruptions
        case = {"disruptions": [
            {"after_prompt": 2, "write_files": {"f.txt": "second\n"}},
            {"after_prompt": 1, "write_files": {"f.txt": "first\n"}}]}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            apply_all_disruptions(case, root)
            self.assertEqual((root / "f.txt").read_text(), "second\n")  # order: 1 then 2

    def test_delete_containment_refused(self):
        from optarena.cases import apply_disruptions
        case = {"disruptions": [{"after_prompt": 1, "delete_files": ["../escape.txt"]}]}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                apply_disruptions(case, Path(d), 1)

    def test_schema_validates_disruptions(self):
        base = {"name": "x", "prompts": ["a"]}
        validate_case({**base, "disruptions": [{"after_prompt": 1, "write_files": {"a": "b"}}]})
        with self.assertRaises(SchemaError):
            validate_case({**base, "disruptions": [{"after_prompt": 0}]})       # 1-based
        with self.assertRaises(SchemaError):
            validate_case({**base, "disruptions": [{"after_prompt": 1, "bogus": 1}]})  # unknown key
        with self.assertRaises(SchemaError):
            validate_case({**base, "disruptions": "nope"})                       # not a list

    # ── Reactive (`when`) triggers ──────────────────────────────────────────

    def test_schema_validates_when_triggers(self):
        base = {"name": "x", "prompts": ["a"]}
        validate_case({**base, "disruptions": [{"when": {"file_exists": "a.py"}}]})
        validate_case({**base, "disruptions": [
            {"when": {"file_contains": {"path": "a.py", "pattern": "TODO"}}}]})
        with self.assertRaises(SchemaError):  # both after_prompt and when
            validate_case({**base, "disruptions": [
                {"after_prompt": 1, "when": {"file_exists": "a.py"}}]})
        with self.assertRaises(SchemaError):  # neither
            validate_case({**base, "disruptions": [{"write_files": {"a": "b"}}]})
        with self.assertRaises(SchemaError):  # both file_exists and file_contains
            validate_case({**base, "disruptions": [{"when": {
                "file_exists": "a.py", "file_contains": {"path": "a.py", "pattern": "x"}}}]})
        with self.assertRaises(SchemaError):  # file_contains missing 'pattern'
            validate_case({**base, "disruptions": [
                {"when": {"file_contains": {"path": "a.py"}}}]})
        with self.assertRaises(SchemaError):  # unknown key under 'when'
            validate_case({**base, "disruptions": [{"when": {"bogus": 1}}]})

    def test_schema_rejects_unsafe_file_exists_path(self):
        """P1-04: `file_exists` was previously checked only as a non-empty
        string - a traversal/absolute value passed schema validation and
        was only an unprotected runtime existence check on the real host
        filesystem (see test_reactive_file_exists_cannot_escape_workspace
        below)."""
        base = {"name": "x", "prompts": ["a"]}
        for bad in ("../outside.txt", "/etc/passwd", "C:/Windows/win.ini", "sub/../../out.txt"):
            with self.subTest(bad=bad), self.assertRaises(SchemaError):
                validate_case({**base, "disruptions": [{"when": {"file_exists": bad}}]})

    def test_schema_rejects_unsafe_file_contains_path(self):
        base = {"name": "x", "prompts": ["a"]}
        for bad in ("../outside.txt", "/etc/passwd", "C:/Windows/win.ini"):
            with self.subTest(bad=bad), self.assertRaises(SchemaError):
                validate_case({**base, "disruptions": [
                    {"when": {"file_contains": {"path": bad, "pattern": "x"}}}]})

    def test_reactive_file_exists_fires_once_workspace_satisfies_it(self):
        from optarena.cases import apply_disruptions
        case = {"disruptions": [
            {"when": {"file_exists": "app.py"}, "description": "reactive",
             "write_files": {"flag.txt": "x\n"}}]}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            seen: set[int] = set()
            # Not created yet - condition false, nothing fires.
            self.assertEqual(apply_disruptions(case, root, 1, seen), [])
            self.assertFalse((root / "flag.txt").exists())
            # Now it exists - fires on the next boundary.
            (root / "app.py").write_text("x\n")
            self.assertEqual(apply_disruptions(case, root, 2, seen), ["reactive"])
            self.assertTrue((root / "flag.txt").exists())
            # Condition still true, but already fired (tracked via `seen`) -
            # must NOT fire again.
            (root / "flag.txt").unlink()
            self.assertEqual(apply_disruptions(case, root, 3, seen), [])
            self.assertFalse((root / "flag.txt").exists())

    def test_reactive_file_exists_cannot_escape_workspace(self):
        """P1-04's actual runtime gap, exercised directly (bypassing
        validate_case, the same way test_delete_containment_refused does
        for the write side) so this proves the RUNTIME containment check
        itself works, independent of the schema layer that now also
        rejects this case earlier. Before the fix, `(root / abs_path)`
        silently discarded `root` entirely for an absolute right-hand side
        (pathlib's own documented behaviour) and checked the REAL host
        path - a case-controlled arbitrary-host-file-existence oracle."""
        from optarena.cases import apply_disruptions
        with tempfile.TemporaryDirectory() as outside_dir:
            outside_file = Path(outside_dir) / "definitely-exists-on-the-host.txt"
            outside_file.write_text("secret host content\n")
            case = {"disruptions": [
                {"when": {"file_exists": str(outside_file)},
                 "write_files": {"flag.txt": "leaked\n"}}]}
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                # The host file genuinely exists - if containment were
                # broken, this would fire. It must not.
                self.assertTrue(outside_file.exists())
                fired = apply_disruptions(case, root, 1)
                self.assertEqual(fired, [])
                self.assertFalse((root / "flag.txt").exists())

    def test_reactive_file_contains_checks_content(self):
        from optarena.cases import apply_disruptions
        case = {"disruptions": [
            {"when": {"file_contains": {"path": "out.txt", "pattern": "READY"}},
             "write_files": {"flag.txt": "x\n"}}]}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "out.txt").write_text("still working\n")
            self.assertEqual(apply_disruptions(case, root, 1), [])
            (root / "out.txt").write_text("READY\n")
            self.assertEqual(len(apply_disruptions(case, root, 2)), 1)

    def test_apply_all_disruptions_forces_reactive_triggers_too(self):
        # verify-corpus doesn't simulate agent turns, so `when` triggers are
        # force-fired unconditionally - the conservative "fully perturbed
        # world" a reference solution must still be correct in.
        from optarena.cases import apply_all_disruptions
        case = {"disruptions": [{"when": {"file_exists": "never-created.txt"},
                                  "write_files": {"f.txt": "fired\n"}}]}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            apply_all_disruptions(case, root)
            self.assertEqual((root / "f.txt").read_text(), "fired\n")


class FailureAttributionTests(unittest.TestCase):
    def test_regression_after_disruption(self):
        from optarena.metrics import attribute_failure
        c = {"passed": False, "extra": {"steps": [
            {"i": 1, "ok": True, "expected_ok": True, "disrupted": ["cfg changed"]},
            {"i": 2, "ok": True, "expected_ok": False}]}}
        s = attribute_failure(c)
        self.assertIn("satisfied after prompt 1", s)
        self.assertIn("regressed by prompt 2", s)
        self.assertIn("cfg changed", s)

    def test_never_satisfied(self):
        from optarena.metrics import attribute_failure
        c = {"passed": False, "extra": {"steps": [
            {"i": 1, "expected_ok": False}, {"i": 2, "expected_ok": False}]}}
        self.assertIn("never", attribute_failure(c))

    def test_tool_failure_attributed(self):
        from optarena.metrics import attribute_failure
        c = {"passed": False, "extra": {"steps": [
            {"i": 1, "ok": True, "expected_ok": False},
            {"i": 2, "ok": False, "expected_ok": False}]}}
        self.assertIn("prompt 2", attribute_failure(c))

    def test_none_when_passed_or_single_step(self):
        from optarena.metrics import attribute_failure
        self.assertIsNone(attribute_failure({"passed": True, "extra": {"steps": [{"i": 1}, {"i": 2}]}}))
        self.assertIsNone(attribute_failure({"passed": False, "extra": {"steps": [{"i": 1}]}}))

    def test_precise_oracle_ok_preferred_over_expected_ok(self):
        # A step can look fine by the cheap expected-file check (file exists,
        # right name) yet fail the REAL oracle (check_command) - oracle_ok, when
        # present, must be the signal attribution trusts, not expected_ok.
        from optarena.metrics import attribute_failure
        c = {"passed": False, "extra": {"steps": [
            {"i": 1, "ok": True, "expected_ok": True, "oracle_ok": True, "disrupted": ["cfg changed"]},
            {"i": 2, "ok": True, "expected_ok": True, "oracle_ok": False}]}}
        s = attribute_failure(c)
        self.assertIn("real oracle (behavior) passed", s)
        self.assertIn("after prompt 1", s)
        self.assertIn("regressed by prompt 2", s)
        self.assertIn("cfg changed", s)

    def test_precise_never_passed(self):
        from optarena.metrics import attribute_failure
        c = {"passed": False, "extra": {"steps": [
            {"i": 1, "ok": True, "expected_ok": True, "oracle_ok": False},
            {"i": 2, "ok": True, "expected_ok": True, "oracle_ok": False}]}}
        s = attribute_failure(c)
        self.assertIn("real oracle never passed", s)


class EfficiencyMetricsTests(unittest.TestCase):
    def test_tokens_and_steps_per_pass(self):
        s = aggregate([
            {"passed": True, "duration_s": 1, "extra": {"total_tokens": 100, "n_steps": 2}},
            {"passed": True, "duration_s": 1, "extra": {"total_tokens": 300, "n_steps": 4}},
            {"passed": False, "duration_s": 1, "extra": {"total_tokens": 200, "n_steps": 3}}])
        self.assertEqual(s["tokens_per_pass"], 300)     # 600 total tokens / 2 passes
        self.assertEqual(s["steps_per_pass"], 3.0)      # mean(2,4) over passing cases

    def test_none_without_telemetry(self):
        s = aggregate([{"passed": True, "duration_s": 1, "extra": {}}])
        self.assertIsNone(s["tokens_per_pass"])
        self.assertIsNone(s["steps_per_pass"])


@unittest.skipUnless(shutil.which("ssh-keygen"), "requires OpenSSH's ssh-keygen (>=8.0, for -Y sign/verify)")
class PackSigningTests(unittest.TestCase):
    """P1-06: publisher authenticity for case packs, on top of the existing
    integrity (content hash) and transport (https-required) checks. Real
    ssh-keygen keys throughout, not mocked subprocess calls - the whole
    point is proving actual cryptographic verification works, the same
    live-tool discipline the rest of this project's security fixes use."""

    def _cases_dir(self, d):
        p = Path(d) / "cases"
        p.mkdir()
        (p / "c1.json").write_text(json.dumps({
            "name": "c1", "prompts": ["do x"],
            "expected_files": [{"path_pattern": "x.py", "content_patterns": ["x"]}]}))
        return p

    def _keypair(self, d, name="key"):
        import subprocess
        key_path = Path(d) / name
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", "test"],
                       capture_output=True, timeout=30, check=True)
        return key_path, key_path.with_suffix(key_path.suffix + ".pub")

    def test_sign_and_verify_trusted_publisher_round_trip(self):
        from optarena import packs
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            priv, pub = self._keypair(d)
            pack = packs.build_pack(src, "mypack", "1.0.0")
            pack = packs.sign_pack(pack, priv, signer_id="acme-corp")
            self.assertIn("signature", pack)
            self.assertEqual(pack["signature"]["signer"], "acme-corp")

            signers_file = Path(d) / "trusted_publishers"
            packs.add_trusted_publisher("acme-corp", pub, trusted_publishers_file=signers_file)
            result = packs.verify_pack_signature(pack, trusted_publishers_file=signers_file)
            self.assertTrue(result["signed"])
            self.assertTrue(result["trusted"])
            self.assertFalse(result["tampered"])
            self.assertIn("acme-corp", result["detail"])

    def test_verify_unknown_signer_is_untrusted_not_tampered(self):
        """A signer simply not (yet) in the keyring is a soft 'don't know
        them' state - distinct from an actively broken signature."""
        from optarena import packs
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            priv, _pub = self._keypair(d)
            pack = packs.build_pack(src, "mypack", "1.0.0")
            pack = packs.sign_pack(pack, priv, signer_id="acme-corp")
            empty_keyring = Path(d) / "empty_signers"
            empty_keyring.write_text("someone-else ssh-ed25519 AAAAunrelated\n", encoding="utf-8")
            result = packs.verify_pack_signature(pack, trusted_publishers_file=empty_keyring)
            self.assertTrue(result["signed"])
            self.assertFalse(result["trusted"])
            self.assertFalse(result["tampered"])

    def test_verify_tampered_content_is_detected(self):
        """A known signer whose signature does NOT verify - the content
        changed after signing (or an impersonation attempt) - is a
        materially different, stronger signal than 'unknown signer'."""
        from optarena import packs
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            priv, pub = self._keypair(d)
            pack = packs.build_pack(src, "mypack", "1.0.0")
            pack = packs.sign_pack(pack, priv, signer_id="acme-corp")
            signers_file = Path(d) / "trusted_publishers"
            packs.add_trusted_publisher("acme-corp", pub, trusted_publishers_file=signers_file)
            # Tamper AFTER signing: change content, recompute a
            # self-consistent hash (a sophisticated tamperer would do this
            # too), but the OLD signature (over the OLD hash) stays.
            pack["cases"]["c1.json"]["prompts"] = ["EVIL INSTEAD"]
            pack["hash"] = packs.content_hash(pack["cases"])
            result = packs.verify_pack_signature(pack, trusted_publishers_file=signers_file)
            self.assertTrue(result["signed"])
            self.assertFalse(result["trusted"])
            self.assertTrue(result["tampered"])

    def test_load_pack_local_file_never_gated_on_trust(self):
        """A pack already on the local filesystem needed no network trust
        decision to get there - unsigned/untrusted is fine for a local
        install, same as before this fix (no regression)."""
        from optarena import packs
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            pack = packs.build_pack(src, "mypack", "1.0.0")  # unsigned
            out = packs.write_pack(pack, Path(d) / "p.optpack.json")
            loaded = packs.load_pack(str(out))
            self.assertFalse(loaded["verification"]["signed"])

    def test_load_pack_remote_untrusted_refused_without_allow_unsigned(self):
        from optarena import packs
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            priv, _pub = self._keypair(d)
            pack = packs.build_pack(src, "mypack", "1.0.0")
            pack = packs.sign_pack(pack, priv, signer_id="acme-corp")
            raw = json.dumps(pack).encode("utf-8")
            with mock.patch("optarena.packs.urllib.request.urlopen") as m:
                m.return_value.__enter__.return_value.read.return_value = raw
                empty_keyring = Path(d) / "empty_signers"
                with mock.patch.object(packs, "TRUSTED_PUBLISHERS_FILE", empty_keyring):
                    with self.assertRaises(ValueError) as ctx:
                        packs.load_pack("https://example.invalid/p.optpack.json")
                    self.assertIn("unsigned/untrusted", str(ctx.exception))

    def test_load_pack_remote_untrusted_allowed_with_allow_unsigned(self):
        from optarena import packs
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            priv, _pub = self._keypair(d)
            pack = packs.build_pack(src, "mypack", "1.0.0")
            pack = packs.sign_pack(pack, priv, signer_id="acme-corp")
            raw = json.dumps(pack).encode("utf-8")
            with mock.patch("optarena.packs.urllib.request.urlopen") as m:
                m.return_value.__enter__.return_value.read.return_value = raw
                empty_keyring = Path(d) / "empty_signers"
                with mock.patch.object(packs, "TRUSTED_PUBLISHERS_FILE", empty_keyring):
                    loaded = packs.load_pack("https://example.invalid/p.optpack.json",
                                             allow_unsigned=True)
            self.assertFalse(loaded["verification"]["trusted"])

    def test_load_pack_tampered_refused_unconditionally_even_local_and_allow_unsigned(self):
        """The hard-tamper case bypasses NEITHER the local-file exemption
        NOR --allow-unsigned - it's never a legitimate 'haven't decided to
        trust this yet' state the way an unknown signer is."""
        from optarena import packs
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            priv, pub = self._keypair(d)
            pack = packs.build_pack(src, "mypack", "1.0.0")
            pack = packs.sign_pack(pack, priv, signer_id="acme-corp")
            signers_file = Path(d) / "trusted_publishers"
            packs.add_trusted_publisher("acme-corp", pub, trusted_publishers_file=signers_file)
            pack["cases"]["c1.json"]["prompts"] = ["EVIL INSTEAD"]
            pack["hash"] = packs.content_hash(pack["cases"])
            out = packs.write_pack(pack, Path(d) / "tampered.optpack.json")
            with mock.patch.object(packs, "TRUSTED_PUBLISHERS_FILE", signers_file):
                with self.assertRaises(ValueError) as ctx:
                    packs.load_pack(str(out), allow_unsigned=True)  # local AND allow_unsigned
                self.assertIn("BROKEN signature", str(ctx.exception))

    def test_add_trusted_publisher_replaces_existing_entry_for_same_identity(self):
        from optarena import packs
        with tempfile.TemporaryDirectory() as d:
            _priv1, pub1 = self._keypair(d, "key1")
            _priv2, pub2 = self._keypair(d, "key2")
            signers_file = Path(d) / "trusted_publishers"
            packs.add_trusted_publisher("acme-corp", pub1, trusted_publishers_file=signers_file)
            packs.add_trusted_publisher("acme-corp", pub2, trusted_publishers_file=signers_file)
            lines = signers_file.read_text(encoding="utf-8").splitlines()
            acme_lines = [ln for ln in lines if ln.startswith("acme-corp ")]
            self.assertEqual(len(acme_lines), 1, "must replace, not duplicate, the same identity")
            self.assertIn(pub2.read_text(encoding="utf-8").split()[1], acme_lines[0])

    def test_signature_and_verification_survive_install_and_feed_the_manifest(self):
        """End to end through install_pack (which writes _pack.json) and
        runner._manifest._pack_info (which reads it back) - not just the
        packs.py primitives in isolation."""
        from optarena import packs
        from optarena.runner._manifest import _pack_info
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            priv, pub = self._keypair(d)
            pack = packs.build_pack(src, "mypack", "3.0.0")
            pack = packs.sign_pack(pack, priv, signer_id="acme-corp")
            signers_file = Path(d) / "trusted_publishers"
            packs.add_trusted_publisher("acme-corp", pub, trusted_publishers_file=signers_file)
            with mock.patch.object(packs, "TRUSTED_PUBLISHERS_FILE", signers_file):
                loaded = packs.load_pack(str(packs.write_pack(pack, Path(d) / "p.optpack.json")))
                reg = Path(d) / "reg"
                dest = packs.install_pack(loaded, packs_dir=reg)
            info = _pack_info(str(dest))
            self.assertEqual(info["name"], "mypack")
            self.assertEqual(info["version"], "3.0.0")
            self.assertTrue(info["verification"]["trusted"])

    def test_signature_made_under_a_different_namespace_is_refused(self):
        """P1-10: `verify_pack_signature` must always verify against the
        fixed `_SIGN_NAMESPACE`, never a namespace the pack itself
        declares - otherwise a signature a trusted publisher made for some
        OTHER purpose under a namespace of the attacker's choosing verifies
        successfully here even though it was never meant to authorize an
        optarena pack. Reproduces the review's own live exploit: a REAL
        ssh-keygen signature, from a REAL trusted key, made under a
        namespace that is NOT "optarena-pack"."""
        import subprocess
        from optarena import packs
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            priv, pub = self._keypair(d)
            pack = packs.build_pack(src, "mypack", "1.0.0")
            blob = packs._signable_blob(pack)
            data_path = Path(d) / "pack.blob"
            data_path.write_bytes(blob)
            # Sign the IDENTICAL blob, but under a namespace the attacker
            # picked ("some-other-tool"), not "optarena-pack" - a real,
            # valid ssh-keygen signature, just for a different purpose.
            subprocess.run(
                ["ssh-keygen", "-Y", "sign", "-f", str(priv), "-n", "some-other-tool", str(data_path)],
                capture_output=True, timeout=30, check=True)
            forged_sig = data_path.with_name(data_path.name + ".sig").read_text(encoding="utf-8")
            pack["signature"] = {"signer": "acme-corp", "namespace": "some-other-tool", "sig": forged_sig}
            signers_file = Path(d) / "trusted_publishers"
            packs.add_trusted_publisher("acme-corp", pub, trusted_publishers_file=signers_file)
            result = packs.verify_pack_signature(pack, trusted_publishers_file=signers_file)
            self.assertFalse(result["trusted"], "a signature for a different namespace must never verify")

    def test_signature_under_the_real_namespace_from_the_same_key_still_works(self):
        """Control for the test above - the SAME key, the SAME blob, signed
        under the correct namespace, must still verify as trusted. Proves
        the fix rejects the wrong namespace specifically, not signing in
        general."""
        import subprocess
        from optarena import packs
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            priv, pub = self._keypair(d)
            pack = packs.build_pack(src, "mypack", "1.0.0")
            blob = packs._signable_blob(pack)
            data_path = Path(d) / "pack.blob"
            data_path.write_bytes(blob)
            subprocess.run(
                ["ssh-keygen", "-Y", "sign", "-f", str(priv), "-n", packs._SIGN_NAMESPACE, str(data_path)],
                capture_output=True, timeout=30, check=True)
            real_sig = data_path.with_name(data_path.name + ".sig").read_text(encoding="utf-8")
            pack["signature"] = {"signer": "acme-corp", "sig": real_sig}
            signers_file = Path(d) / "trusted_publishers"
            packs.add_trusted_publisher("acme-corp", pub, trusted_publishers_file=signers_file)
            result = packs.verify_pack_signature(pack, trusted_publishers_file=signers_file)
            self.assertTrue(result["trusted"])

    def test_malformed_signature_block_is_refused_not_crashed(self):
        """P1-10: `pack["signature"]` is untrusted input - a wrong shape
        must be refused (tampered=True, the same unconditional-refusal
        state a broken crypto signature gets), not crash `.get()` on a
        non-dict, and not silently treated as merely 'unsigned'."""
        from optarena import packs
        # `{}` deliberately excluded - an empty dict is falsy in Python, so
        # `if not sig` (the existing, correct "no signature at all" check)
        # catches it before this malformed-shape check ever runs, and
        # rightly so: an empty object carries no more information than a
        # missing field would.
        for bad_sig in ("just-a-string", ["a", "list"], 42, {"signer": "x"},
                        {"signer": "x", "sig": 123}, {"signer": 123, "sig": "y"}):
            with self.subTest(bad_sig=bad_sig):
                pack = {"name": "p", "version": "1.0.0", "hash": "sha256:x", "case_count": 1,
                        "signature": bad_sig}
                result = packs.verify_pack_signature(pack)
                self.assertTrue(result["signed"])
                self.assertFalse(result["trusted"])
                self.assertTrue(result["tampered"], f"malformed shape {bad_sig!r} must be tampered=True")

    def test_installed_pack_modified_after_install_is_no_longer_reported_trusted(self):
        """P1-10: the exact stale-trust bug - `_pack_info`/
        `verify_installed_pack` must re-derive trust from what's actually
        on disk NOW, not replay `_pack.json`'s install-time snapshot
        forever. Edits a real installed case file directly on disk, exactly
        as the review's own reproduction did, and confirms the NEXT run's
        manifest reflects that instead of stale 'trusted'."""
        from optarena import packs
        from optarena.runner._manifest import _pack_info
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            priv, pub = self._keypair(d)
            pack = packs.build_pack(src, "mypack", "1.0.0")
            pack = packs.sign_pack(pack, priv, signer_id="acme-corp")
            signers_file = Path(d) / "trusted_publishers"
            packs.add_trusted_publisher("acme-corp", pub, trusted_publishers_file=signers_file)
            with mock.patch.object(packs, "TRUSTED_PUBLISHERS_FILE", signers_file):
                loaded = packs.load_pack(str(packs.write_pack(pack, Path(d) / "p.optpack.json")))
                reg = Path(d) / "reg"
                dest = packs.install_pack(loaded, packs_dir=reg)

            # Confirmed trusted immediately after install.
            self.assertTrue(_pack_info(str(dest))["verification"]["trusted"])

            # Now tamper with the installed case file directly on disk -
            # no re-signing, no re-installing, exactly what a local
            # filesystem edit (or an attacker with local access) looks like.
            # Directory-listing order isn't creation order (install_pack
            # writes case files, then _pack.json, last) - `glob()` happened
            # to return a case file first on Windows but returned
            # _pack.json first on Linux CI, so this must exclude it by name
            # rather than rely on iteration order.
            case_file = next(f for f in dest.glob("*.json") if f.name != "_pack.json")
            data = json.loads(case_file.read_text(encoding="utf-8"))
            data["prompts"] = ["a completely different, malicious prompt"]
            case_file.write_text(json.dumps(data), encoding="utf-8")

            info = _pack_info(str(dest))
            self.assertFalse(info["verification"]["trusted"],
                             "a modified installed pack must not still report as trusted")
            self.assertTrue(info["verification"]["tampered"])
            self.assertIn("modified after installation", info["verification"]["detail"])

    def test_installed_pack_unmodified_reruns_stay_trusted_without_reverifying_crypto(self):
        """Control for the test above - untouched content must keep
        reporting the original (already-correct) verification, not
        silently flip to untrusted just because the check now runs on
        every call."""
        from optarena import packs
        from optarena.runner._manifest import _pack_info
        with tempfile.TemporaryDirectory() as d:
            src = self._cases_dir(d)
            priv, pub = self._keypair(d)
            pack = packs.build_pack(src, "mypack", "1.0.0")
            pack = packs.sign_pack(pack, priv, signer_id="acme-corp")
            signers_file = Path(d) / "trusted_publishers"
            packs.add_trusted_publisher("acme-corp", pub, trusted_publishers_file=signers_file)
            with mock.patch.object(packs, "TRUSTED_PUBLISHERS_FILE", signers_file):
                loaded = packs.load_pack(str(packs.write_pack(pack, Path(d) / "p.optpack.json")))
                reg = Path(d) / "reg"
                dest = packs.install_pack(loaded, packs_dir=reg)
            self.assertTrue(_pack_info(str(dest))["verification"]["trusted"])
            self.assertTrue(_pack_info(str(dest))["verification"]["trusted"])   # a second, later run


class PackVersionSortTests(unittest.TestCase):
    """F-11: version comparisons must be numeric, not lexical - "1.9.0" is a
    LOWER version than "1.10.0" even though '9' > '1' as characters."""

    def test_numeric_minor_beats_lexical_order(self):
        from optarena.packs import _version_key
        self.assertGreater(_version_key("1.10.0"), _version_key("1.9.0"))
        self.assertGreater(_version_key("2.0.0"), _version_key("1.99.99"))

    def test_missing_components_default_to_zero(self):
        from optarena.packs import _version_key
        self.assertEqual(_version_key("1.2"), (1, 2, 0, ""))
        self.assertEqual(_version_key("3"), (3, 0, 0, ""))

    def test_non_numeric_version_does_not_raise(self):
        from optarena.packs import _version_key
        self.assertEqual(_version_key("not-a-version"), (-1, -1, -1, "not-a-version"))

    def test_resolve_pack_picks_numerically_highest_version(self):
        from optarena.packs import resolve_pack
        packs_dir = Path(tempfile.mkdtemp(prefix="optarena_test_packs_"))
        self.addCleanup(shutil.rmtree, packs_dir, ignore_errors=True)
        for version in ("1.9.0", "1.10.0", "1.2.0"):
            d = packs_dir / f"demo@{version}"
            d.mkdir()
            (d / "_pack.json").write_text(
                json.dumps({"name": "demo", "version": version, "created_at": "2026-01-01T00:00:00"}),
                encoding="utf-8")
        resolved = resolve_pack("demo", packs_dir=packs_dir)
        self.assertEqual(resolved.name, "demo@1.10.0")


class PackLoadValidationTests(unittest.TestCase):
    """F-11: a pack's cases are now validated at INSTALL time (load_pack),
    not just at build time - a hand-edited/corrupted pack must be refused
    before it's written into the registry, not silently accepted and only
    discovered broken later at `run --pack` time."""

    @staticmethod
    def _case(name="c1", **overrides):
        base = {"name": name, "prompts": ["do it"]}
        base.update(overrides)
        return base

    def _write_pack(self, cases: dict, **top_level) -> str:
        from optarena.packs import content_hash
        pack = {
            "optarena_pack": 1, "name": "demo", "version": "1.0.0",
            "case_count": len(cases), "hash": content_hash(cases), "cases": cases,
        }
        pack.update(top_level)
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".optpack.json", delete=False, encoding="utf-8")
        json.dump(pack, f)
        f.close()
        return f.name

    def test_valid_pack_loads(self):
        from optarena.packs import load_pack
        path = self._write_pack({"c1.json": self._case()})
        pack = load_pack(path)
        self.assertEqual(pack["name"], "demo")

    def test_malformed_case_is_rejected(self):
        from optarena.packs import load_pack
        path = self._write_pack({"c1.json": {"prompts": ["missing name field"]}})
        with self.assertRaises(SchemaError):
            load_pack(path)

    def test_duplicate_case_names_rejected(self):
        from optarena.packs import load_pack
        path = self._write_pack({
            "a.json": self._case(name="dup"),
            "b.json": self._case(name="dup"),
        })
        with self.assertRaises(ValueError):
            load_pack(path)

    def test_case_count_mismatch_rejected(self):
        from optarena.packs import load_pack
        path = self._write_pack({"c1.json": self._case()}, case_count=99)
        with self.assertRaises(ValueError) as ctx:
            load_pack(path)
        self.assertIn("case_count", str(ctx.exception))

    def test_filename_sanitization_collision_rejected(self):
        from optarena.packs import load_pack
        # `_safe()` maps both of these filenames to the same sanitized name
        # ("a-b.json"), which would silently overwrite one case with the
        # other at install time without this check.
        path = self._write_pack({
            "a/b.json": self._case(name="one"),
            "a b.json": self._case(name="two"),
        })
        with self.assertRaises(ValueError) as ctx:
            load_pack(path)
        self.assertIn("collision", str(ctx.exception))


class PackInstallAtomicityTests(unittest.TestCase):
    """F-11: install_pack must never leave a half-written pack directory in
    the registry - it stages into a sibling temp dir and renames into place,
    cleaning the staging dir up if anything fails partway through."""

    def test_install_failure_leaves_no_staging_directory_behind(self):
        from optarena import packs
        packs_dir = Path(tempfile.mkdtemp(prefix="optarena_test_packinstall_"))
        self.addCleanup(shutil.rmtree, packs_dir, ignore_errors=True)
        pack = {
            "optarena_pack": 1, "name": "demo", "version": "1.0.0",
            "case_count": 1, "hash": "sha256:x",
            "cases": {"c1.json": {"name": "c1", "prompts": ["x"]}},
        }
        with mock.patch("optarena.packs.json.dumps", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                packs.install_pack(pack, packs_dir=packs_dir)
        # no half-written target dir, and no leftover ".demo@1.0.0.staging-*" dir
        self.assertEqual(list(packs_dir.iterdir()), [])

    def test_successful_install_is_visible_and_idempotent(self):
        from optarena import packs
        packs_dir = Path(tempfile.mkdtemp(prefix="optarena_test_packinstall2_"))
        self.addCleanup(shutil.rmtree, packs_dir, ignore_errors=True)
        cases = {"c1.json": {"name": "c1", "prompts": ["x"]}}
        pack = {
            "optarena_pack": 1, "name": "demo", "version": "1.0.0",
            "case_count": 1, "hash": packs.content_hash(cases), "cases": cases,
        }
        root = packs.install_pack(pack, packs_dir=packs_dir)
        self.assertTrue((root / "_pack.json").is_file())
        self.assertTrue((root / "c1.json").is_file())
        # re-installing the identical pack is a no-op, not an error
        root2 = packs.install_pack(pack, packs_dir=packs_dir)
        self.assertEqual(root, root2)


class RunEventsTests(unittest.TestCase):
    """F-18: RunEvents gates human console output (.say/.detail, --quiet and
    --log-level) and structured lifecycle events (.emit, --json-events)
    independently, and a default-constructed RunEvents (what every
    pre-existing run_scenario caller effectively gets) behaves exactly like
    an unconditional print()."""

    def _capture(self, events, fn):
        import io
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            fn(events)
        finally:
            sys.stdout = old
        return buf.getvalue()

    def test_default_prints_unconditionally(self):
        from optarena.events import RunEvents
        out = self._capture(RunEvents(), lambda e: e.say("hello"))
        self.assertIn("hello", out)

    def test_quiet_suppresses_say_and_detail(self):
        from optarena.events import RunEvents
        events = RunEvents(quiet=True)
        out = self._capture(events, lambda e: (e.say("a"), e.detail("b")))
        self.assertEqual(out, "")

    def test_log_level_warn_drops_detail_but_keeps_say(self):
        from optarena.events import RunEvents
        events = RunEvents(log_level="warn")
        out = self._capture(events, lambda e: (e.say("headline"), e.detail("secondary")))
        self.assertIn("headline", out)
        self.assertNotIn("secondary", out)

    def test_log_level_quiet_is_equivalent_to_quiet_flag(self):
        from optarena.events import RunEvents
        events = RunEvents(log_level="quiet")
        self.assertTrue(events.quiet)

    def test_invalid_log_level_rejected(self):
        from optarena.events import RunEvents
        with self.assertRaises(ValueError):
            RunEvents(log_level="verbose")

    def test_emit_only_under_json_events(self):
        from optarena.events import RunEvents
        out = self._capture(RunEvents(), lambda e: e.emit("run_started", run_id="x"))
        self.assertEqual(out, "")
        out = self._capture(RunEvents(json_events=True), lambda e: e.emit("run_started", run_id="x"))
        payload = json.loads(out.strip())
        self.assertEqual(payload["event"], "run_started")
        self.assertEqual(payload["run_id"], "x")
        self.assertIn("ts", payload)

    def test_json_events_independent_of_quiet(self):
        # --json-events without --quiet: both streams are present.
        from optarena.events import RunEvents
        events = RunEvents(json_events=True)
        out = self._capture(events, lambda e: (e.say("human line"), e.emit("run_started")))
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "human line")
        json.loads(lines[1])  # the second line is valid JSON


class RunScenarioLifecycleEventsTests(unittest.TestCase):
    """F-18 integration: run_scenario emits all five named lifecycle events,
    in order, and --quiet suppresses every human line while leaving the
    JSON event stream intact."""

    def setUp(self):
        self.cases_dir = Path(tempfile.mkdtemp(prefix="optarena_test_events_"))
        self.addCleanup(shutil.rmtree, self.cases_dir, ignore_errors=True)
        (self.cases_dir / "c1.json").write_text(json.dumps({
            "name": "c1", "prompts": ["do it"],
        }), encoding="utf-8")

    def test_lifecycle_events_fire_in_order_and_human_output_is_suppressed(self):
        import io
        from optarena.events import RunEvents
        driver = mock.Mock(parallel_safe=False, caches_results=False)
        driver.run_case.return_value = CaseResult(name="c1", passed=True, duration_s=0.01)
        sc = Scenario(name="x", driver="aider", cases_dir=str(self.cases_dir))
        events = RunEvents(quiet=True, json_events=True)
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            with mock.patch("optarena.runner._execution.get_driver", return_value=driver):
                rec = run_scenario(sc, events=events)
        finally:
            sys.stdout = old
        lines = [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]
        # every line parsed as JSON (proves --quiet left no stray human text
        # interleaved with the event stream), in the documented order.
        self.assertEqual(
            [e["event"] for e in lines],
            ["run_started", "case_started", "case_completed", "checkpoint_saved", "run_completed"])
        self.assertEqual(lines[1]["case"], "c1")
        self.assertEqual(lines[2]["case"], "c1")
        self.assertTrue(lines[2]["passed"])
        self.assertEqual(lines[4]["status"], "completed")
        self.assertEqual(rec.status, "completed")


class ManifestTrialsTests(unittest.TestCase):
    """F-09: `trials` in the manifest is what was REQUESTED (for cross-run
    comparability); `runner_trials` separately records the runner's own
    local loop count, which drops to 1 for a caching driver even though the
    run still semantically has N trials."""

    def test_runner_trials_defaults_to_requested(self):
        from optarena.runner import build_manifest
        m = build_manifest(
            Scenario(name="x", driver="aider", backend=Backend(kind="ollama", base_url="http://x", model="m")),
            [], requested_trials=3)
        self.assertEqual(m["trials"], 3)
        self.assertEqual(m["runner_trials"], 3)

    def test_runner_trials_can_diverge_from_requested(self):
        # a caching driver: requested 3 trials, but the runner's own loop
        # only executes once (the driver repeats internally).
        from optarena.runner import build_manifest
        m = build_manifest(
            Scenario(name="x", driver="aider", backend=Backend(kind="ollama", base_url="http://x", model="m")),
            [], requested_trials=3, runner_trials=1)
        self.assertEqual(m["trials"], 3)
        self.assertEqual(m["runner_trials"], 1)

    def test_manifest_records_source_and_platform(self):
        """P2-04: a manifest used to record oracle_version/case_set_hash/
        trials but nothing about the exact tool build (git commit, dirty
        state) or the platform it ran on - two results could be
        semantically identical by those fields yet not be reproducible from
        one another. Values may legitimately be None off a non-git install,
        but the keys must always be present."""
        from optarena.runner import build_manifest
        m = build_manifest(
            Scenario(name="x", driver="aider", backend=Backend(kind="ollama", base_url="http://x", model="m")),
            [], requested_trials=1)
        for key in ("git_commit", "git_dirty", "python_version", "platform"):
            self.assertIn(key, m)
        self.assertIsInstance(m["python_version"], str)
        self.assertTrue(m["python_version"])

    def test_manifest_records_driver_version_key(self):
        from optarena.runner import build_manifest
        m = build_manifest(
            Scenario(name="x", driver="aider", backend=Backend(kind="ollama", base_url="http://x", model="m")),
            [], requested_trials=1)
        self.assertIn("driver_version", m)

    def test_manifest_records_generation_params_and_provider_version(self):
        """P2-02: temperature/top_p/seed and a best-effort provider version
        are part of the manifest's identity now, recorded regardless of
        whether the run's driver actually honors them."""
        from optarena.runner import build_manifest
        backend = Backend(kind="ollama", base_url="http://x", model="m",
                           temperature=0.4, top_p=0.9, seed=7)
        m = build_manifest(
            Scenario(name="x", driver="aider", backend=backend), [], requested_trials=1)
        self.assertEqual(m["backend_temperature"], 0.4)
        self.assertEqual(m["backend_top_p"], 0.9)
        self.assertEqual(m["backend_seed"], 7)
        self.assertIn("backend_provider_version", m)   # None off an unreachable host - key still present

    def test_provider_version_detected_live_from_ollama(self):
        """Real /api/version request against a live local Ollama - not
        mocked - confirming the field carries a real value when the backend
        actually is Ollama and reachable."""
        import urllib.error
        import urllib.request
        from optarena.runner._manifest import _provider_version
        backend = Backend(kind="ollama", base_url="http://localhost:11434")
        try:
            urllib.request.urlopen("http://localhost:11434/api/version", timeout=2)
        except (OSError, urllib.error.URLError):
            self.skipTest("no local Ollama reachable")
        version = _provider_version(backend)
        self.assertIsInstance(version, str)
        self.assertTrue(version)

    def test_provider_version_none_for_non_ollama_and_unreachable(self):
        from optarena.runner._manifest import _provider_version
        self.assertIsNone(_provider_version(Backend(kind="openai", base_url="http://localhost:11434")))
        self.assertIsNone(_provider_version(Backend(kind="ollama", base_url="http://127.0.0.1:1")))

    def test_source_revision_falls_back_to_embedded_build_commit(self):
        """P2-02: a wheel install has no .git directory - _source_revision
        must fall back to a build-time-embedded commit file rather than
        reporting None, and git_dirty must stay None (no working tree to be
        dirty), not falsely claim "known clean" with False."""
        from optarena.runner._manifest import _source_revision
        import optarena.runner._manifest as manifest_mod
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="")
            fake_file = Path(manifest_mod.__file__).resolve().parent.parent / "_build_commit.txt"
            real_read_text = Path.read_text

            def fake_read_text(self, *a, **kw):
                if self == fake_file:
                    return "deadbeef1234\n"
                return real_read_text(self, *a, **kw)

            with mock.patch.object(Path, "read_text", fake_read_text):
                info = _source_revision()
        self.assertEqual(info["git_commit"], "deadbeef1234")
        self.assertIsNone(info["git_dirty"])

    def test_source_revision_none_when_no_git_and_no_embedded_commit(self):
        from optarena.runner._manifest import _source_revision
        with mock.patch("subprocess.run") as run:
            run.return_value = mock.Mock(returncode=1, stdout="")
            with mock.patch.object(Path, "read_text", side_effect=OSError("no such file")):
                info = _source_revision()
        self.assertIsNone(info["git_commit"])
        self.assertIsNone(info["git_dirty"])

    def test_caching_driver_manifest_reflects_requested_trials_not_one(self):
        cases_dir = Path(tempfile.mkdtemp(prefix="optarena_test_manifesttrials_"))
        self.addCleanup(shutil.rmtree, cases_dir, ignore_errors=True)
        (cases_dir / "c1.json").write_text(json.dumps({
            "name": "c1", "prompts": ["do it"],
        }), encoding="utf-8")
        driver = mock.Mock(parallel_safe=False, caches_results=True, trials=1)
        driver.run_case.return_value = CaseResult(name="c1", passed=True, duration_s=0.1)
        sc = Scenario(name="x", driver="aider", cases_dir=str(cases_dir))
        with mock.patch("optarena.runner._execution.get_driver", return_value=driver):
            rec = run_scenario(sc, trials=5)
        self.assertEqual(rec.manifest["trials"], 5)
        self.assertEqual(rec.manifest["runner_trials"], 1)


class GetDriverVersionTests(unittest.TestCase):
    """P2-04: get_driver_version is manifest metadata, not a live-run
    dependency - every failure mode returns None rather than raising, and
    nothing here should ever talk to a model/provider."""

    def test_unknown_driver_returns_none(self):
        from optarena.drivers import get_driver_version
        self.assertIsNone(get_driver_version("not-a-real-driver"))

    def test_baseline_driver_returns_none(self):
        from optarena.drivers import get_driver_version
        self.assertIsNone(get_driver_version("openai-chat"))
        self.assertIsNone(get_driver_version("ollama-chat"))

    def test_cli_driver_binary_not_on_path_returns_none(self):
        from optarena.drivers import get_driver_version
        with mock.patch("shutil.which", return_value=None), \
             mock.patch("optarena.drivers.aider_cli.find_aider", return_value=None):
            self.assertIsNone(get_driver_version("aider"))
            self.assertIsNone(get_driver_version("codex"))

    def test_cli_driver_version_parsed_from_first_output_line(self):
        from optarena.drivers import get_driver_version
        fake = mock.Mock(returncode=0, stdout="aider 0.60.1\nsome other line\n", stderr="")
        with mock.patch("optarena.drivers.aider_cli.find_aider", return_value="/usr/bin/aider"), \
             mock.patch("subprocess.run", return_value=fake):
            self.assertEqual(get_driver_version("aider"), "aider 0.60.1")

    def test_cli_driver_version_probe_never_raises(self):
        from optarena.drivers import get_driver_version

        def _boom(*_a, **_kw):
            raise OSError("simulated: binary vanished mid-probe")

        with mock.patch("optarena.drivers.aider_cli.find_aider", return_value="/usr/bin/aider"), \
             mock.patch("subprocess.run", side_effect=_boom):
            self.assertIsNone(get_driver_version("aider"))

    def test_sdk_driver_package_not_installed_returns_none(self):
        from importlib.metadata import PackageNotFoundError
        from optarena.drivers import get_driver_version
        with mock.patch("importlib.metadata.version", side_effect=PackageNotFoundError):
            self.assertIsNone(get_driver_version("crewai"))

    def test_sdk_driver_version_from_installed_package_metadata(self):
        from optarena.drivers import get_driver_version
        with mock.patch("importlib.metadata.version", return_value="1.2.3"):
            self.assertEqual(get_driver_version("crewai"), "1.2.3")

    def test_sdk_driver_uses_pip_distribution_name_not_import_name(self):
        """Found live while implementing this: `openai-agents`' import name
        (`import agents`) differs from its PyPI distribution name
        (`openai-agents`), and `importlib.metadata.version()` needs the
        distribution name - looking up "agents" silently returned None even
        though the package IS installed. Confirms the fix queries the
        correct name, not the import name."""
        from importlib.metadata import PackageNotFoundError
        from optarena.drivers import get_driver_version

        def _fake_version(name):
            if name == "openai-agents":
                return "0.18.3"
            raise PackageNotFoundError(name)

        with mock.patch("importlib.metadata.version", side_effect=_fake_version):
            self.assertEqual(get_driver_version("openai-agents"), "0.18.3")

    def test_every_sdk_driver_has_a_pip_name_entry(self):
        from optarena.drivers import DRIVERS, _SDK_PIP_NAMES
        for key, info in DRIVERS.items():
            if info["kind"] == "sdk":
                self.assertIn(key, _SDK_PIP_NAMES, f"{key} has no _SDK_PIP_NAMES entry")


class EntryPointDriverDiscoveryTests(unittest.TestCase):
    """P3-02: a third-party package can register a driver via the
    `optarena.drivers` entry-point group without any code change here."""

    @staticmethod
    def _fake_entry_point(name, driver_cls):
        ep = mock.Mock()
        ep.name = name
        ep.value = "fake_pkg.driver:FakeDriver"
        ep.load.return_value = driver_cls
        return ep

    def test_discovered_entry_point_is_merged_into_drivers_registry(self):
        from optarena.drivers import _load_entry_point_drivers
        ep = self._fake_entry_point("my-tool", mock.Mock)
        with mock.patch("importlib.metadata.entry_points", return_value=[ep]):
            discovered = _load_entry_point_drivers()
        self.assertIn("my-tool", discovered)
        self.assertIs(discovered["my-tool"]["_entry_point"], ep)
        self.assertEqual(discovered["my-tool"]["kind"], "external")

    def test_get_driver_instantiates_a_discovered_entry_point(self):
        made = mock.Mock(name="instance")
        driver_cls = mock.Mock(return_value=made)
        ep = self._fake_entry_point("my-tool", driver_cls)
        with mock.patch.dict(DRIVERS, {"my-tool": {
            "kind": "external", "backend": "scenario", "status": "external",
            "file_tools": True, "summary": "x", "owner": None, "tested_with": None,
            "_entry_point": ep,
        }}):
            result = get_driver("my-tool")
        driver_cls.assert_called_once_with()
        self.assertIs(result, made)

    def test_get_driver_wraps_a_broken_entry_point_in_runtime_error(self):
        ep = mock.Mock()
        ep.name = "broken-tool"
        ep.value = "broken_pkg:Broken"
        ep.load.side_effect = ImportError("no module named broken_pkg")
        with mock.patch.dict(DRIVERS, {"broken-tool": {
            "kind": "external", "backend": "scenario", "status": "external",
            "file_tools": True, "summary": "x", "owner": None, "tested_with": None,
            "_entry_point": ep,
        }}):
            with self.assertRaises(RuntimeError):
                get_driver("broken-tool")

    def test_no_entry_points_installed_is_not_an_error(self):
        from optarena.drivers import _load_entry_point_drivers
        with mock.patch("importlib.metadata.entry_points", return_value=[]):
            self.assertEqual(_load_entry_point_drivers(), {})

    def test_entry_points_lookup_failure_is_swallowed(self):
        from optarena.drivers import _load_entry_point_drivers
        with mock.patch("importlib.metadata.entry_points", side_effect=RuntimeError("boom")):
            self.assertEqual(_load_entry_point_drivers(), {})


class RunScenarioCleanupResilienceTests(unittest.TestCase):
    """F-01: every cleanup step in run_scenario's finally block is
    individually best-effort - driver.teardown() raising must not prevent
    the run from returning a completed record (previously an uncaught
    exception here would propagate out of run_scenario after the case loop
    already succeeded, turning a cosmetic cleanup failure into a fake run
    failure)."""

    def setUp(self):
        self.cases_dir = Path(tempfile.mkdtemp(prefix="optarena_test_cleanup_"))
        self.addCleanup(shutil.rmtree, self.cases_dir, ignore_errors=True)
        (self.cases_dir / "c1.json").write_text(json.dumps({
            "name": "c1", "prompts": ["do it"],
        }), encoding="utf-8")

    def test_teardown_failure_does_not_propagate_or_lose_the_result(self):
        driver = mock.Mock(parallel_safe=False, caches_results=False)
        driver.run_case.return_value = CaseResult(name="c1", passed=True, duration_s=0.1)
        driver.teardown.side_effect = RuntimeError("teardown boom")
        sc = Scenario(name="x", driver="aider", cases_dir=str(self.cases_dir))
        with mock.patch("optarena.runner._execution.get_driver", return_value=driver):
            rec = run_scenario(sc)  # must not raise
        self.assertEqual(rec.status, "completed")
        self.assertEqual(rec.summary["passed"], 1)


class CheckpointStatusTests(unittest.TestCase):
    """F-02: an in-progress run is checkpointed after every case (status
    "running"), and finalizing that SAME run via save_run must not be
    treated as a run_id collision - only a genuinely different existing
    file (any other status) still is."""

    def setUp(self):
        import optarena.store as store_mod
        self.store = store_mod
        results_dir = Path(tempfile.mkdtemp(prefix="optarena_test_checkpoint_"))
        self.addCleanup(shutil.rmtree, results_dir, ignore_errors=True)
        self._orig = (store_mod.RESULTS_DIR, store_mod.RUNS_DIR)
        store_mod.RESULTS_DIR = results_dir
        store_mod.RUNS_DIR = results_dir / "runs"
        self.addCleanup(self._restore)

    def _restore(self):
        self.store.RESULTS_DIR, self.store.RUNS_DIR = self._orig

    @staticmethod
    def _record(run_id, status, cases=None):
        data = {"run_id": run_id, "scenario": {"name": "x"}, "status": status, "cases": cases or []}
        return mock.Mock(run_id=run_id, to_dict=lambda: data)

    def test_checkpoint_then_finalize_same_run_succeeds(self):
        self.store.save_checkpoint(self._record("run-1", "running", cases=[{"name": "c1"}]))
        path = self.store.save_run(self._record("run-1", "completed", cases=[{"name": "c1"}]))
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "completed")

    def test_repeated_checkpoints_do_not_raise(self):
        for i in range(3):
            self.store.save_checkpoint(
                self._record("run-1", "running", cases=[{"name": f"c{i}"}]))
        # last checkpoint wins on disk
        path = self.store.RUNS_DIR / "run-1.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["cases"]), 1)

    def test_finalizing_a_non_running_existing_file_still_raises(self):
        self.store.save_run(self._record("run-1", "completed"))
        with self.assertRaises(FileExistsError):
            self.store.save_run(self._record("run-1", "completed"))

    def test_checkpoint_updates_index_incrementally(self):
        self.store.save_checkpoint(self._record("run-1", "running"))
        index = json.loads((self.store.RESULTS_DIR / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(len(index), 1)
        self.assertEqual(index[0]["run_id"], "run-1")
        self.assertEqual(index[0]["status"], "running")


class InfrastructureErrorAggregateTests(unittest.TestCase):
    """F-10: an infrastructure_error (sandbox/engine failure, not a genuine
    model/tool failure) must be visible separately from the ordinary pass
    rate - `adjusted_pass_rate` excludes those cases so an environment
    outage can't masquerade as a correctness regression."""

    def test_infrastructure_errors_counted_and_excluded_from_adjusted_rate(self):
        cases = [
            {"passed": True, "duration_s": 1, "extra": {"oracle": {"infrastructure_error": False}}},
            {"passed": False, "duration_s": 1, "extra": {"oracle": {"infrastructure_error": False}}},
            {"passed": False, "duration_s": 1, "extra": {"oracle": {"infrastructure_error": True}}},
        ]
        s = aggregate(cases)
        self.assertEqual(s["infrastructure_errors"], 1)
        # adjusted_pass_rate excludes the 1 infra-error case: 1 pass / 2 non-infra cases
        self.assertAlmostEqual(s["adjusted_pass_rate"], 0.5)

    def test_none_when_no_infrastructure_errors(self):
        cases = [{"passed": True, "duration_s": 1, "extra": {"oracle": {"infrastructure_error": False}}}]
        s = aggregate(cases)
        self.assertIsNone(s["infrastructure_errors"])
        self.assertIsNone(s["adjusted_pass_rate"])

    def test_checks_all_trials_not_just_final_oracle(self):
        cases = [{"passed": True, "duration_s": 1, "extra": {
            "oracle": {"infrastructure_error": False},
            "oracle_all_trials": [{"infrastructure_error": False}, {"infrastructure_error": True}],
        }}]
        s = aggregate(cases)
        self.assertEqual(s["infrastructure_errors"], 1)


class CapabilityAwareAggregateTests(unittest.TestCase):
    """P2-07: a driver with no file-editing tools (every baseline/SDK
    driver) structurally cannot satisfy some cases regardless of model
    output - `pass_rate` must stay exactly what it always meant (every
    case, unadjusted, still comparable to older runs), while
    `eligible_pass_rate` gives the "of the cases this driver could possibly
    have won" second lens, same shape as F-10's adjusted_pass_rate for
    infrastructure errors."""

    def test_excluded_cases_lower_overall_but_not_eligible_rate(self):
        cases = [
            {"passed": True, "duration_s": 1, "extra": {}},
            {"passed": True, "duration_s": 1, "extra": {}},
            {"passed": False, "duration_s": 1,
             "extra": {"capability_excluded": "needs 2 separate files - a flat-file writer produces exactly one"}},
        ]
        s = aggregate(cases)
        self.assertEqual(s["pass_rate"], round(2 / 3, 3))   # unchanged - every case still counts
        self.assertEqual(s["capability_excluded_cases"], 1)
        self.assertEqual(s["eligible_pass_rate"], 1.0)      # 2/2 of what this driver could win
        self.assertEqual(
            s["capability_exclusion_reasons"],
            ["needs 2 separate files - a flat-file writer produces exactly one"])

    def test_none_when_nothing_excluded(self):
        cases = [{"passed": True, "duration_s": 1, "extra": {}}]
        s = aggregate(cases)
        self.assertIsNone(s["capability_excluded_cases"])
        self.assertIsNone(s["eligible_pass_rate"])
        self.assertIsNone(s["capability_exclusion_reasons"])

    def test_multiple_distinct_reasons_deduplicated_and_sorted(self):
        cases = [
            {"passed": False, "duration_s": 1, "extra": {"capability_excluded": "reason B"}},
            {"passed": False, "duration_s": 1, "extra": {"capability_excluded": "reason A"}},
            {"passed": False, "duration_s": 1, "extra": {"capability_excluded": "reason A"}},
        ]
        s = aggregate(cases)
        self.assertEqual(s["capability_excluded_cases"], 3)
        self.assertEqual(s["capability_exclusion_reasons"], ["reason A", "reason B"])
        self.assertIsNone(s["eligible_pass_rate"])  # eligible_total is 0 here


class RunnerCapabilityExclusionWiringTests(unittest.TestCase):
    """P2-07: the actual wiring - runner._run_case must record
    capability_excluded ONLY for a file_tools=False driver on a case
    baseline_incompatible() actually flags, and never for a file_tools=True
    (CLI-agent) driver even on the same case."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_capexclusion_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    @staticmethod
    def _multi_file_case():
        # baseline_incompatible() flags this: 2 expected_files, a flat-file
        # writer can only ever produce one.
        return {
            "name": "c", "prompts": ["do it"],
            "expected_files": [{"path_pattern": "a.py"}, {"path_pattern": "b.py"}],
        }

    def test_file_tools_false_driver_gets_capability_excluded(self):
        from optarena.runner import _run_case
        driver = mock.Mock(parallel_safe=True)
        driver.run_case.return_value = CaseResult(name="c", passed=False)
        sc = Scenario(name="s", driver="openai-chat",
                      backend=Backend(base_url="http://x", model="m"))
        result = _run_case(driver, self._multi_file_case(), sc, self.ws, trials=1)
        self.assertIn("capability_excluded", result.extra)
        self.assertIn("2 separate files", result.extra["capability_excluded"])

    def test_file_tools_true_driver_never_gets_capability_excluded(self):
        from optarena.runner import _run_case
        driver = mock.Mock(parallel_safe=True)
        driver.run_case.return_value = CaseResult(name="c", passed=False)
        sc = Scenario(name="s", driver="aider",
                      backend=Backend(base_url="http://x", model="m"))
        result = _run_case(driver, self._multi_file_case(), sc, self.ws, trials=1)
        self.assertNotIn("capability_excluded", result.extra)

    def test_file_tools_false_driver_on_a_compatible_case_unaffected(self):
        from optarena.runner import _run_case
        driver = mock.Mock(parallel_safe=True)
        driver.run_case.return_value = CaseResult(name="c", passed=True)
        sc = Scenario(name="s", driver="openai-chat",
                      backend=Backend(base_url="http://x", model="m"))
        case = {"name": "c", "prompts": ["do it"],
                "expected_files": [{"path_pattern": "a.py"}]}
        result = _run_case(driver, case, sc, self.ws, trials=1)
        self.assertNotIn("capability_excluded", result.extra)


class RunnerCaseMetadataWiringTests(unittest.TestCase):
    """`_run_case` must copy language/domain/task_type from the case
    definition onto the returned CaseResult - this is case metadata, not
    driver output, so it has to survive regardless of what the driver itself
    returns, and must not depend on which fields the driver's own
    CaseResult happened to set."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_casemeta_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)

    def _scenario(self):
        return Scenario(name="s", driver="aider", backend=Backend(base_url="http://x", model="m"))

    def test_metadata_copied_from_case_onto_result(self):
        from optarena.runner import _run_case
        driver = mock.Mock(parallel_safe=True)
        driver.run_case.return_value = CaseResult(name="c", passed=True)
        case = {"name": "c", "prompts": ["do it"], "language": "python",
                "domain": "backend", "task_type": "feature"}
        result = _run_case(driver, case, self._scenario(), self.ws, trials=1)
        self.assertEqual(result.language, "python")
        self.assertEqual(result.domain, "backend")
        self.assertEqual(result.task_type, "feature")

    def test_missing_metadata_fields_stay_none_not_dropped_silently(self):
        from optarena.runner import _run_case
        driver = mock.Mock(parallel_safe=True)
        driver.run_case.return_value = CaseResult(name="c", passed=True)
        case = {"name": "c", "prompts": ["do it"]}   # no language/domain/task_type
        result = _run_case(driver, case, self._scenario(), self.ws, trials=1)
        self.assertIsNone(result.language)
        self.assertIsNone(result.domain)
        self.assertIsNone(result.task_type)

    def test_metadata_survives_trial_merging(self):
        # Same shape as CachingDriverTrialsPlumbingTests below: trials > 1
        # routes through _merge_trials, which builds a FRESH CaseResult - the
        # metadata has to be applied to that merged object, not just the
        # driver's original one, or a >1-trial run would silently lose it.
        from optarena.runner import _run_case
        driver = mock.Mock(parallel_safe=True)
        driver.run_case.return_value = CaseResult(name="c", passed=True)
        case = {"name": "c", "prompts": ["do it"], "language": "go", "domain": "cli"}
        result = _run_case(driver, case, self._scenario(), self.ws, trials=3)
        self.assertEqual(result.language, "go")
        self.assertEqual(result.domain, "cli")

    def test_metadata_reaches_to_dict(self):
        from optarena.runner import _run_case
        driver = mock.Mock(parallel_safe=True)
        driver.run_case.return_value = CaseResult(name="c", passed=True)
        case = {"name": "c", "prompts": ["do it"], "language": "rust",
                "domain": "backend", "task_type": "security"}
        result = _run_case(driver, case, self._scenario(), self.ws, trials=1)
        d = result.to_dict()
        self.assertEqual(d["language"], "rust")
        self.assertEqual(d["domain"], "backend")
        self.assertEqual(d["task_type"], "security")


class RunnerWorkspaceQuotaWiringTests(unittest.TestCase):
    """P1-09: `_run_case` must not silently trust a result the driver
    produced if the workspace blew its quota WHILE the driver was running -
    the watchdog previously only ever wrapped check_command, so a runaway
    or malicious agent's own write phase (which for a `cli`-kind driver
    happens on the real host filesystem) was invisible to it entirely."""

    def setUp(self):
        from optarena._cases import _sandbox as sandbox_mod
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_driverquota_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        # _WorkspaceQuotaWatchdog reads these module globals fresh at
        # instantiation time (not import time - see its own docstring), so
        # patching the module attributes directly, not the env var the
        # module only reads once at import, is what actually takes effect.
        self._patch_bytes = mock.patch.object(sandbox_mod, "_WORKSPACE_MAX_BYTES", 500)
        self._patch_files = mock.patch.object(sandbox_mod, "_WORKSPACE_MAX_FILES", 10_000)
        self._patch_bytes.start()
        self._patch_files.start()
        self.addCleanup(self._patch_bytes.stop)
        self.addCleanup(self._patch_files.stop)

    @staticmethod
    def _case():
        return {"name": "c", "prompts": ["do it"], "expected_files": [{"path_pattern": "a.py"}]}

    def _scenario(self):
        return Scenario(name="s", driver="aider", backend=Backend(base_url="http://x", model="m"))

    def test_driver_exceeding_quota_fails_the_case_even_if_driver_reported_pass(self):
        from optarena.runner import _run_case

        def _fake_run_case(case, scenario, ws):
            (ws / "runaway.bin").write_bytes(b"x" * 1000)   # over the 500-byte quota
            return CaseResult(name="c", passed=True)   # the driver itself thinks it succeeded

        driver = mock.Mock(parallel_safe=True)
        driver.run_case.side_effect = _fake_run_case
        result = _run_case(driver, self._case(), self._scenario(), self.ws, trials=1)
        self.assertFalse(result.passed)
        self.assertTrue(result.extra.get("workspace_quota_exceeded"))
        self.assertTrue(any("workspace quota exceeded during agent execution" in f
                            for f in result.failures))

    def test_driver_under_quota_is_unaffected(self):
        from optarena.runner import _run_case

        def _fake_run_case(case, scenario, ws):
            (ws / "fine.bin").write_bytes(b"x" * 10)
            return CaseResult(name="c", passed=True)

        driver = mock.Mock(parallel_safe=True)
        driver.run_case.side_effect = _fake_run_case
        result = _run_case(driver, self._case(), self._scenario(), self.ws, trials=1)
        self.assertTrue(result.passed)
        self.assertNotIn("workspace_quota_exceeded", result.extra)


class ImageOverrideResolutionTests(unittest.TestCase):
    """F-15: a scenario-level image_overrides map lets a run pin a specific
    immutable tag per case image without a single global env var replacing
    every image uniformly."""

    def setUp(self):
        import optarena._cases._sandbox as cases_mod
        self.cases_mod = cases_mod
        self.addCleanup(cases_mod.set_image_overrides, None)

    def test_no_overrides_returns_image_unchanged(self):
        self.cases_mod.set_image_overrides(None)
        self.assertEqual(self.cases_mod.resolve_image("optarena-tester-python:latest"),
                          "optarena-tester-python:latest")

    def test_exact_image_override_applied(self):
        self.cases_mod.set_image_overrides({"optarena-tester-python:latest": "optarena-tester-python:sha-abc123"})
        self.assertEqual(self.cases_mod.resolve_image("optarena-tester-python:latest"),
                          "optarena-tester-python:sha-abc123")

    def test_unrelated_image_not_affected_by_override(self):
        self.cases_mod.set_image_overrides({"optarena-tester-python:latest": "optarena-tester-python:sha-abc123"})
        self.assertEqual(self.cases_mod.resolve_image("optarena-tester-go:latest"),
                          "optarena-tester-go:latest")


class EngineHealthTTLTests(unittest.TestCase):
    """F-16: engine-health is cached for a short TTL, not for the whole
    process - a transient Docker/Podman startup failure must self-heal
    within seconds, not stay "unavailable" until the process exits."""

    def setUp(self):
        import optarena._cases._sandbox as cases_mod
        self.cases_mod = cases_mod
        self._orig = (cases_mod._docker_checked_at, cases_mod._docker_ok)
        cases_mod._docker_checked_at = -1.0
        cases_mod._docker_ok = False
        self.addCleanup(self._restore)

    def _restore(self):
        self.cases_mod._docker_checked_at, self.cases_mod._docker_ok = self._orig

    def test_within_ttl_result_is_cached_not_reprobed(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return mock.Mock(returncode=0)

        with mock.patch.object(self.cases_mod.subprocess, "run", side_effect=fake_run):
            self.assertTrue(self.cases_mod._docker_available())
            self.assertTrue(self.cases_mod._docker_available())
        self.assertEqual(len(calls), 1)  # second call served from the TTL cache

    def test_force_recheck_bypasses_ttl(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return mock.Mock(returncode=0)

        with mock.patch.object(self.cases_mod.subprocess, "run", side_effect=fake_run):
            self.assertTrue(self.cases_mod._docker_available())
            self.assertTrue(self.cases_mod._docker_available(force_recheck=True))
        self.assertEqual(len(calls), 2)

    def test_expired_ttl_reprobes(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return mock.Mock(returncode=0)

        with mock.patch.object(self.cases_mod.subprocess, "run", side_effect=fake_run):
            self.assertTrue(self.cases_mod._docker_available())
        # simulate the TTL having elapsed
        self.cases_mod._docker_checked_at -= (self.cases_mod._ENGINE_HEALTH_TTL_S + 1)
        with mock.patch.object(self.cases_mod.subprocess, "run", side_effect=fake_run):
            self.assertTrue(self.cases_mod._docker_available())
        self.assertEqual(len(calls), 2)


class CaseSensitiveAssertionTests(unittest.TestCase):
    """
    A-17: content was lowercased and then matched with re.IGNORECASE, so a
    case could not assert casing at all. `case_sensitive: true` opts a spec in;
    the default stays exactly as before.
    """

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_casesens_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        (self.ws / "m.go").write_text("package main\nfunc Handler() {}\n", encoding="utf-8")

    def test_default_is_case_insensitive_as_before(self):
        failures = check_expected(["m.go"], [{
            "path_pattern": "m.go", "content_patterns": ["FUNC HANDLER"],
            "regex_patterns": [r"func handler"],
        }], self.ws)
        self.assertEqual(failures, [])

    def test_case_sensitive_content_pattern_catches_wrong_casing(self):
        failures = check_expected(["m.go"], [{
            "path_pattern": "m.go", "case_sensitive": True,
            "content_patterns": ["func handler"],     # real code says Handler
        }], self.ws)
        self.assertEqual(len(failures), 1)
        self.assertIn("missing expected content", failures[0])

    def test_case_sensitive_content_pattern_accepts_right_casing(self):
        failures = check_expected(["m.go"], [{
            "path_pattern": "m.go", "case_sensitive": True,
            "content_patterns": ["func Handler"],
            "regex_patterns": [r"func [A-Z]\w+\("],   # an EXPORTED identifier
            "not_content_patterns": ["func handler"],
        }], self.ws)
        self.assertEqual(failures, [])

    def test_schema_accepts_and_type_checks_the_flag(self):
        validate_case({"name": "c", "expected_files": [
            {"path_pattern": "x", "case_sensitive": True}]})
        with self.assertRaises(SchemaError):
            validate_case({"name": "c", "expected_files": [
                {"path_pattern": "x", "case_sensitive": "yes"}]})


class EngineStateResetTests(unittest.TestCase):
    """A-11/A-12/A-13: per-scenario resets of module-level engine state, so a
    transient failure in one scenario of a matrix run doesn't silently degrade
    every later one."""

    def setUp(self):
        import optarena._cases._sandbox as cases_mod
        self.cases_mod = cases_mod
        self._orig = (cases_mod._docker_checked_at, cases_mod._docker_warned,
                      dict(cases_mod._pull_attempts))
        self.addCleanup(self._restore)

    def _restore(self):
        (self.cases_mod._docker_checked_at, self.cases_mod._docker_warned, attempts) = self._orig
        self.cases_mod._pull_attempts.clear()
        self.cases_mod._pull_attempts.update(attempts)

    def test_reset_clears_health_cache_and_warning_flag(self):
        self.cases_mod._docker_checked_at = 12345.0
        self.cases_mod._docker_warned = True
        self.cases_mod.reset_engine_health_cache()
        self.assertEqual(self.cases_mod._docker_checked_at, -1.0)
        self.assertFalse(self.cases_mod._docker_warned)

    def test_reset_clears_pull_backoff(self):
        self.cases_mod._pull_attempts["img"] = 3
        self.cases_mod._pull_last_attempt_at["img"] = 1.0
        self.cases_mod.reset_pull_backoff()
        self.assertEqual(self.cases_mod._pull_attempts, {})
        self.assertEqual(self.cases_mod._pull_last_attempt_at, {})

    def test_run_scenario_resets_engine_state_per_scenario(self):
        self.cases_mod._docker_warned = True
        driver = mock.Mock(parallel_safe=False, caches_results=False)
        driver.run_case.return_value = CaseResult(name="create_factorial", passed=True)
        with mock.patch("optarena.runner._execution.get_driver", return_value=driver), \
             mock.patch("optarena.runner._execution.DockerSandbox"), \
             mock.patch("optarena.runner._execution.store.save_checkpoint"):
            run_scenario(Scenario(name="x", driver="aider", cases=["create_factorial"]))
        self.assertFalse(self.cases_mod._docker_warned)


class IndexLockOwnershipTests(unittest.TestCase):
    """A-07: a process that gave up waiting must not delete the lock it never
    acquired - doing so hands the lock to a third process while the real
    holder is still mid-update."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="optarena_test_lock_"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.lock = self.dir / "index.lock"

    def test_acquired_lock_is_released(self):
        from optarena.store import _IndexLock
        with _IndexLock(self.lock) as lk:
            self.assertTrue(lk.acquired)
            self.assertTrue(self.lock.exists())
        self.assertFalse(self.lock.exists())

    def test_unacquired_lock_is_not_deleted(self):
        from optarena.store import _IndexLock
        self.lock.write_text("held by someone else", encoding="utf-8")
        with mock.patch.object(_IndexLock, "WAIT_DEADLINE_S", 0.01), \
             mock.patch.object(_IndexLock, "STALE_AFTER_S", 10_000):
            with _IndexLock(self.lock) as lk:
                self.assertFalse(lk.acquired)
        self.assertTrue(self.lock.exists(), "deleted a lock it never held")


class ReportEscapingTests(unittest.TestCase):
    """A-16: escape AFTER truncation, so a long detail can't be cut mid-entity."""

    def test_long_markup_detail_is_not_cut_mid_entity(self):
        from optarena.report import to_html
        run = {"run_id": "r", "scenario": {"name": "s", "driver": "d", "backend": {}},
               "summary": {"pass_rate": 0.0, "passed": 0, "cases": 1},
               "cases": [{"name": "c", "passed": False, "duration_s": 1.0,
                          "failures": ["x" * 396 + "<b>boom</b>"], "extra": {}}]}
        html = to_html(run)
        self.assertNotIn("<b>boom", html)          # escaped, not live markup
        for broken in ("&l;", "&lt", "&a;"):
            self.assertNotIn(broken + "<", html)   # and never a half-written entity
        self.assertNotRegex(html, r"&[a-z]{1,3}(?![a-z;])")


class _StubBackend:
    """
    A-10: a real HTTP backend on localhost, so the baseline drivers' run_case
    can be executed end to end by the test suite instead of only their helper
    functions. Serves both the OpenAI-compatible and the Ollama-native chat
    endpoints. `delay_s` makes it slow enough to exercise the whole-case
    deadline (F-05).
    """

    def __init__(self, reply="```python\nprint('hi')\n```", delay_s=0.0):
        import http.server
        import threading

        self.reply, self.delay_s, self.requests = reply, delay_s, []
        stub = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):                      # noqa: N802 - stdlib signature
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                stub.requests.append({"path": self.path, "body": body,
                                      "auth": self.headers.get("Authorization")})
                if stub.delay_s:
                    import time as _t
                    _t.sleep(stub.delay_s)
                text = stub.reply(body) if callable(stub.reply) else stub.reply
                if self.path.endswith("/api/chat"):
                    payload = {"message": {"content": text},
                               "prompt_eval_count": 11, "eval_count": 7}
                else:
                    payload = {"choices": [{"message": {"content": text}}],
                               "usage": {"prompt_tokens": 11, "completion_tokens": 7,
                                         "total_tokens": 18}}
                raw = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):           # noqa: A003 - silence the test log
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()


class BaselineDriverEndToEndTests(unittest.TestCase):
    """
    A-10: `openai-chat`/`ollama-chat` run_case against a real (stub) HTTP
    backend. These are the reference implementation every other driver copies,
    and nothing executed them before - only their helper functions.
    """

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_baseline_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        self.backend = _StubBackend()
        self.addCleanup(self.backend.close)
        self._env = mock.patch.dict(os.environ, {"OPTARENA_DISABLE_SANDBOX": "1"})
        self._env.start()
        self.addCleanup(self._env.stop)

    def _case(self, prompts=("write hi.py",), **extra):
        case = {"name": "c", "prompts": list(prompts),
                "expected_files": [{"path_pattern": "hi.py", "content_patterns": ["print"]}]}
        case.update(extra)
        return case

    def _scenario(self, kind="openai", **kw):
        return Scenario(name="s", driver="openai-chat",
                        backend=Backend(kind=kind, base_url=self.backend.base_url,
                                        model="test-model", api_key="sk-scenario"), **kw)

    def test_openai_chat_writes_block_and_passes(self):
        from optarena.drivers.openai_chat import OpenAIChatDriver
        result = OpenAIChatDriver().run_case(self._case(), self._scenario(), self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual((self.ws / "hi.py").read_text(encoding="utf-8"), "print('hi')\n")
        self.assertEqual(result.extra["prompt_tokens"], 11)
        self.assertEqual(result.extra["completion_tokens"], 7)
        self.assertEqual(result.extra["n_steps"], 1)
        self.assertTrue(result.extra["steps"][0]["ok"])

    def test_openai_chat_sends_the_scenario_key_as_bearer(self):
        from optarena.drivers.openai_chat import OpenAIChatDriver
        OpenAIChatDriver().run_case(self._case(), self._scenario(), self.ws)
        self.assertEqual(self.backend.requests[0]["auth"], "Bearer sk-scenario")
        self.assertTrue(self.backend.requests[0]["path"].endswith("/v1/chat/completions"))

    def test_ollama_chat_uses_native_endpoint_and_num_ctx(self):
        from optarena.drivers.openai_chat import OllamaChatDriver
        scenario = Scenario(name="s", driver="ollama-chat",
                            backend=Backend(kind="ollama", base_url=self.backend.base_url,
                                            model="m", num_ctx=4096))
        OllamaChatDriver().run_case(self._case(), scenario, self.ws)
        request = self.backend.requests[0]
        self.assertTrue(request["path"].endswith("/api/chat"))
        self.assertEqual(request["body"]["options"], {"num_ctx": 4096})

    def test_ollama_chat_sends_generation_params_in_options(self):
        """P2-02: temperature/top_p/seed must reach the real request body,
        not just the manifest - this is what "honored" means for a driver."""
        from optarena.drivers.openai_chat import OllamaChatDriver
        scenario = Scenario(name="s", driver="ollama-chat",
                            backend=Backend(kind="ollama", base_url=self.backend.base_url,
                                            model="m", temperature=0.3, top_p=0.8, seed=99))
        OllamaChatDriver().run_case(self._case(), scenario, self.ws)
        options = self.backend.requests[0]["body"]["options"]
        self.assertEqual(options, {"temperature": 0.3, "top_p": 0.8, "seed": 99})

    def test_ollama_chat_omits_options_key_when_nothing_configured(self):
        from optarena.drivers.openai_chat import OllamaChatDriver
        scenario = Scenario(name="s", driver="ollama-chat",
                            backend=Backend(kind="ollama", base_url=self.backend.base_url, model="m"))
        OllamaChatDriver().run_case(self._case(), scenario, self.ws)
        self.assertNotIn("options", self.backend.requests[0]["body"])

    def test_openai_chat_sends_generation_params_in_body(self):
        from optarena.drivers.openai_chat import OpenAIChatDriver
        scenario = Scenario(name="s", driver="openai-chat",
                            backend=Backend(kind="openai", base_url=self.backend.base_url,
                                            model="test-model", api_key="sk-scenario",
                                            temperature=0.5, top_p=0.95, seed=123))
        OpenAIChatDriver().run_case(self._case(), scenario, self.ws)
        body = self.backend.requests[0]["body"]
        self.assertEqual(body["temperature"], 0.5)
        self.assertEqual(body["top_p"], 0.95)
        self.assertEqual(body["seed"], 123)

    def test_whole_case_deadline_is_not_per_prompt(self):
        # F-05: 3 prompts with a 1s case budget against a 0.6s backend must
        # stop early, not spend 3 x 1s.
        from optarena.drivers.openai_chat import OpenAIChatDriver
        self.backend.delay_s = 0.6
        case = self._case(prompts=["a", "b", "c"])
        result = OpenAIChatDriver().run_case(case, self._scenario(timeout=1), self.ws)
        self.assertIsNotNone(result.error)
        self.assertIn("deadline", result.error)
        self.assertLess(result.duration_s, 3.0)
        self.assertLess(len(self.backend.requests), 3)

    def test_cost_is_zero_for_a_localhost_backend(self):
        from optarena.drivers.openai_chat import OpenAIChatDriver
        result = OpenAIChatDriver().run_case(self._case(), self._scenario(), self.ws)
        self.assertEqual(result.extra["cost_usd"], 0.0)

    def test_written_file_uses_lf_not_crlf(self):
        # A-42: write_text(..., newline="") must be set - otherwise Windows'
        # universal-newline translation turns every "\n" the model wrote
        # into "\r\n" on disk, corrupting POSIX shell/etc. once bind-mounted
        # into the Linux sandbox. Checked at the raw-bytes level so this
        # actually catches a regression regardless of which platform the
        # suite runs on.
        from optarena.drivers.openai_chat import OpenAIChatDriver
        backend = _StubBackend(reply="```\nline one\nline two\nline three\n```")
        self.addCleanup(backend.close)
        scenario = Scenario(name="s", driver="openai-chat",
                            backend=Backend(kind="openai", base_url=backend.base_url,
                                            model="test-model"))
        case = {"name": "c", "prompts": ["write hi.py"],
                "expected_files": [{"path_pattern": "hi.py"}]}
        OpenAIChatDriver().run_case(case, scenario, self.ws)
        raw = (self.ws / "hi.py").read_bytes()
        self.assertNotIn(b"\r\n", raw)


class _RealWorkerFakeDriver(SingleFileSDKDriver):
    """
    P0-04: a fake SDK driver for exercising the REAL subprocess worker
    mechanism end to end (SDKWorkerProcessTests below) - unlike
    SDKDriverBaseTests._driver's `_Fake`, this is a genuine MODULE-LEVEL
    class with no closure state, because a real child process needs a
    target it can actually pickle-by-reference and import; a class defined
    inside a function cannot cross a process boundary at all. Behavior is
    driven entirely by `scenario.backend.model` (the one thing that DOES
    cross the boundary, being a plain dataclass field) rather than by
    constructor arguments, for the same reason.
    """
    name = "fake-real-sdk"

    def open_session(self, scenario):
        return {"opened": True}

    def close_session(self, session) -> None:
        pass

    def complete(self, session, prompt: str, scenario, timeout):
        import time as _time
        model = scenario.backend.model
        if model.startswith("slow:"):
            _time.sleep(float(model.split(":", 1)[1]))
        return "```\nok\n```", {}


class SDKWorkerProcessTests(unittest.TestCase):
    """P0-04: proves the actual mechanism, not just run_case()'s bookkeeping
    around it - a call that overruns its deadline gets its OS process
    killed, not merely abandoned. This is what SDKDriverBaseTests' `_Fake`
    (a local class, bypassing the real worker for exactly this reason)
    cannot verify on its own."""

    def _scenario(self, model="fast"):
        return Scenario(name="s", driver="fake-real-sdk",
                        backend=Backend(base_url="http://x", model=model))

    def test_worker_runs_in_a_different_os_process(self):
        driver = _RealWorkerFakeDriver()
        handle = driver._open_worker(self._scenario())
        try:
            proc, _conn = handle
            self.assertNotEqual(proc.pid, os.getpid())
            self.assertTrue(proc.is_alive())
        finally:
            driver._close_worker(handle)

    def test_normal_completion_round_trips_through_the_worker(self):
        driver = _RealWorkerFakeDriver()
        scenario = self._scenario("fast")
        handle = driver._open_worker(scenario)
        try:
            text, usage = driver._complete_with_deadline(handle, "p", scenario, 20)
            self.assertIn("ok", text)
            self.assertEqual(usage, {})
        finally:
            driver._close_worker(handle)

    def test_overrun_call_actually_kills_the_process(self):
        """The point of P0-04's fix: not "eventually reported as timed out"
        (the old thread-based version already did that) but "the process
        making the call is confirmed dead" - a killed API request cannot
        keep consuming credits in the background."""
        from optarena.drivers.sdk_base import SDKTimeout
        driver = _RealWorkerFakeDriver()
        scenario = self._scenario("slow:5")
        handle = driver._open_worker(scenario)
        proc, _conn = handle
        with self.assertRaises(SDKTimeout):
            driver._complete_with_deadline(handle, "p", scenario, 0.3)
        self.assertFalse(proc.is_alive())
        self.assertIsNotNone(proc.exitcode)   # actually reaped, not just "not alive yet"
        driver._close_worker(handle)   # must not raise against an already-dead process

    def test_close_worker_on_a_live_process_is_clean(self):
        driver = _RealWorkerFakeDriver()
        handle = driver._open_worker(self._scenario("fast"))
        proc, _conn = handle
        driver._close_worker(handle)
        self.assertFalse(proc.is_alive())

    def test_full_run_case_survives_a_real_timeout(self):
        """End to end through run_case() itself, not just the worker
        primitives directly - the actual case loop must report the timeout
        cleanly and the worker process must not survive it.

        `timeout` must stay well above realistic `_open_worker()` spawn
        overhead: `run_case()` computes its deadline BEFORE spawning the
        real subprocess worker, so on a slow/loaded CI runner a too-tight
        budget (this was 0.3s) can be entirely consumed by process spawn
        alone - the loop then reports "case deadline exceeded before prompt
        1/1" (the preflight check) instead of ever reaching
        `_complete_with_deadline`'s "...budget" message this test is meant
        to exercise. Confirmed live as the actual failure on a real CI run,
        not a hypothetical - 2.0s leaves comfortable headroom over normal
        multiprocessing spawn time while staying far under the "slow:5"
        driver's 5s sleep, so the completion-level timeout path reliably
        wins the race regardless of machine speed.
        """
        driver = _RealWorkerFakeDriver()
        case = {"name": "c", "prompts": ["p"], "timeout": 2.0,
                "expected_files": [{"path_pattern": "hi.py"}]}
        ws = Path(tempfile.mkdtemp(prefix="optarena_test_sdkworker_"))
        self.addCleanup(shutil.rmtree, ws, ignore_errors=True)
        result = driver.run_case(case, self._scenario("slow:5"), ws)
        self.assertIsNotNone(result.error)
        self.assertIn("budget", result.error)
        self.assertLess(result.duration_s, 5.0)


class SDKDriverBaseTests(unittest.TestCase):
    """
    A-09: the six SDK drivers now share one case loop, so these test that loop
    directly through a fake subclass - no framework installed, and no network.
    Each assertion here corresponds to something every SDK driver silently did
    NOT do before: honour a timeout, fire disruptions, record steps/tokens/cost.
    """

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_sdkbase_"))
        self.addCleanup(shutil.rmtree, self.ws, ignore_errors=True)
        self._env = mock.patch.dict(os.environ, {"OPTARENA_DISABLE_SANDBOX": "1"})
        self._env.start()
        self.addCleanup(self._env.stop)

    @staticmethod
    def _driver(reply="```\nprint('hi')\n```", usage=None, delay_s=0.0, closed=None):
        from optarena.drivers.sdk_base import SDKTimeout, SingleFileSDKDriver

        class _Fake(SingleFileSDKDriver):
            name = "fake-sdk"
            prompts_seen = []

            def open_session(self, scenario):
                return {"opened": True}

            def close_session(self, session):
                if closed is not None:
                    closed.append(session)

            def complete(self, session, prompt, scenario, timeout):
                self.prompts_seen.append(prompt)
                if delay_s:
                    import time as _t
                    _t.sleep(delay_s)
                return reply, dict(usage or {})

            # Test-only: this class is defined here as a closure over
            # reply/delay_s/usage/closed, so it (like any locally-defined
            # class) cannot be sent across a real process boundary - pickle
            # needs a module-level, importable class. These overrides
            # exercise run_case()'s own orchestration (deadline math, error
            # reporting, session lifecycle) directly in-process instead, the
            # same way the base class did before P0-04's process-based
            # rewrite. The REAL subprocess mechanism - genuine killability,
            # not just deadline bookkeeping - is covered separately by
            # SDKWorkerProcessTests below, via a module-level driver.
            def _open_worker(self, scenario):
                return self.open_session(scenario)

            def _close_worker(self, session):
                self.close_session(session)

            def _complete_with_deadline(self, session, prompt, scenario, remaining):
                import threading as _threading
                box: dict = {}

                def _run():
                    try:
                        box["value"] = self.complete(session, prompt, scenario, remaining)
                    except BaseException as exc:  # noqa: BLE001
                        box["error"] = exc

                t = _threading.Thread(target=_run, daemon=True)
                t.start()
                t.join(remaining)
                if t.is_alive():
                    raise SDKTimeout(
                        f"{self.name} did not respond within the case's remaining "
                        f"{remaining:.0f}s budget")
                if "error" in box:
                    raise box["error"]
                return box["value"]

        return _Fake()

    def _case(self, **extra):
        case = {"name": "c", "prompts": ["write hi.py"],
                "expected_files": [{"path_pattern": "hi.py", "content_patterns": ["print"]}]}
        case.update(extra)
        return case

    def _scenario(self, **kw):
        return Scenario(name="s", driver="fake-sdk",
                        backend=Backend(base_url="http://localhost:11434", model="m"), **kw)

    def test_writes_block_and_passes(self):
        result = self._driver().run_case(self._case(), self._scenario(), self.ws)
        self.assertTrue(result.passed, result.failures or result.error)
        self.assertEqual((self.ws / "hi.py").read_text(encoding="utf-8"), "print('hi')\n")

    def test_written_file_uses_lf_not_crlf(self):
        # A-42: same fix as openai_chat.py - write_text(..., newline="")
        # must be set on the SDK drivers' shared write path too.
        reply = "```\nline one\nline two\nline three\n```"
        self._driver(reply=reply).run_case(self._case(), self._scenario(), self.ws)
        raw = (self.ws / "hi.py").read_bytes()
        self.assertNotIn(b"\r\n", raw)

    def test_case_timeout_is_enforced(self):
        # The headline gap: a hung SDK call used to hang the run forever.
        driver = self._driver(delay_s=5)
        result = driver.run_case(self._case(timeout=1), self._scenario(), self.ws)
        self.assertIsNotNone(result.error)
        self.assertIn("budget", result.error)
        self.assertFalse(result.execution_ok)
        self.assertFalse(result.passed)
        self.assertLess(result.duration_s, 4.0)

    def test_scenario_timeout_overrides_case_timeout(self):
        driver = self._driver(delay_s=5)
        result = driver.run_case(self._case(timeout=600), self._scenario(timeout=1), self.ws)
        self.assertIn("budget", result.error or "")

    def test_disruptions_fire_between_prompts(self):
        # A dynamic case under an SDK driver used to grade an EASIER task,
        # because the disruption never fired at all.
        case = self._case(
            prompts=["first", "second"],
            disruptions=[{"after_prompt": 1,
                          "description": "config changed",
                          "write_files": {"config.ini": "changed\n"}}],
        )
        driver = self._driver()
        result = driver.run_case(case, self._scenario(), self.ws)
        self.assertEqual((self.ws / "config.ini").read_text(encoding="utf-8"), "changed\n")
        fired = [d for st in result.extra["steps"] for d in st.get("disrupted", [])]
        self.assertEqual(fired, ["config changed"])

    def test_per_step_records_and_attribution_inputs(self):
        result = self._driver().run_case(
            self._case(prompts=["a", "b"]), self._scenario(), self.ws)
        steps = result.extra["steps"]
        self.assertEqual(result.extra["n_steps"], 2)
        self.assertEqual([s["i"] for s in steps], [1, 2])
        for step in steps:
            self.assertIn("expected_ok", step)
            self.assertIn("duration_s", step)

    def test_token_usage_accumulates_and_prices(self):
        driver = self._driver(usage={"prompt_tokens": 100, "completion_tokens": 50})
        scenario = Scenario(name="s", driver="fake-sdk",
                            backend=Backend(base_url="https://api.openai.com",
                                            model="gpt-4o", api_key="k"))
        result = driver.run_case(self._case(prompts=["a", "b"]), scenario, self.ws)
        self.assertEqual(result.extra["prompt_tokens"], 200)
        self.assertEqual(result.extra["completion_tokens"], 100)
        self.assertGreater(result.extra["cost_usd"], 0)

    def test_absent_usage_stays_absent(self):
        result = self._driver().run_case(self._case(), self._scenario(), self.ws)
        self.assertNotIn("prompt_tokens", result.extra)
        self.assertEqual(result.extra["cost_usd"], 0.0)

    def test_session_closed_on_normal_completion(self):
        closed = []
        driver = self._driver(closed=closed)
        driver.run_case(self._case(), self._scenario(), self.ws)
        self.assertEqual(len(closed), 1)

    def test_no_code_block_still_writes_and_marks_step_not_ok(self):
        result = self._driver(reply="sorry, no block here").run_case(
            self._case(), self._scenario(), self.ws)
        self.assertFalse(result.extra["steps"][0]["ok"])
        self.assertIn("sorry", (self.ws / "hi.py").read_text(encoding="utf-8"))

    def test_every_registered_sdk_driver_uses_the_shared_loop(self):
        # The parity guarantee itself: if a new SDK driver reimplements
        # run_case, it silently loses timeouts/disruptions/telemetry again.
        from optarena.drivers import _SDK_DRIVERS
        from optarena.drivers.sdk_base import SingleFileSDKDriver
        import importlib
        for key, (module_name, class_name) in _SDK_DRIVERS.items():
            with self.subTest(driver=key):
                module = importlib.import_module(f"optarena.drivers.{module_name}")
                cls = getattr(module, class_name)
                self.assertTrue(issubclass(cls, SingleFileSDKDriver))
                self.assertIs(cls.run_case, SingleFileSDKDriver.run_case,
                              f"{class_name} overrides run_case and loses the shared guarantees")


class OpenAIAgentsSessionCleanupTests(unittest.TestCase):
    """
    A-41: found live in a driver smoke-test sweep - OpenAIAgentsDriver created
    an AsyncOpenAI client in open_session() and never closed it. Harmless to
    the run's own pass/fail data, but printed a
    "RuntimeError: Event loop is closed" traceback to stderr once per case
    (5 times in the sweep's 5-case run) - noisy enough to look like a real
    crash under --debug, and a genuine resource leak (the client's httpx
    connections were never released). Same class of bug already guarded
    against in autogen_sdk.py/semantic_kernel_sdk.py.

    close_session() itself imports nothing from `agents`/`openai` - only
    open_session()/complete() do - so this is tested in isolation with a
    mock client, runs regardless of whether the optional `openai-agents`
    extra is installed, matching this project's SDKDriverBaseTests
    convention (fake driver, no real framework needed).
    """

    def test_close_session_closes_the_client(self):
        from optarena.drivers.openai_agents_sdk import OpenAIAgentsDriver

        closed = []

        class _FakeClient:
            async def close(self):
                closed.append(True)

        driver = OpenAIAgentsDriver()
        driver.close_session(("agent-placeholder", "run-config-placeholder", _FakeClient()))
        self.assertEqual(closed, [True])

    def test_close_session_never_raises_even_if_close_fails(self):
        from optarena.drivers.openai_agents_sdk import OpenAIAgentsDriver

        class _BrokenClient:
            async def close(self):
                raise RuntimeError("simulated close failure")

        driver = OpenAIAgentsDriver()
        driver.close_session(("agent-placeholder", "run-config-placeholder", _BrokenClient()))   # must not raise


class SDKDriverGenerationParamsTests(unittest.TestCase):
    """
    P2-02: each SDK driver's open_session() must actually forward
    temperature/top_p/seed into whatever object the underlying framework
    uses to build its completion request - construction alone (no network
    call happens here for any of these six) proves the wiring is real, not
    just plausible-looking code. Skipped per-driver, gracefully, when that
    optional SDK extra isn't installed - matching this project's existing
    "environment-dependent capability" pattern (PackSigningTests et al.).
    """

    def _scenario(self, driver: str, **backend_kw) -> Scenario:
        backend = Backend(kind="openai", base_url="http://localhost:1",
                          model="m", api_key="k",
                          temperature=0.4, top_p=0.8, seed=5, **backend_kw)
        return Scenario(name="s", driver=driver, backend=backend)

    def test_crewai_llm_carries_generation_params(self):
        # Each driver module imports its SDK lazily INSIDE open_session()/
        # aopen_session(), not at module level (so `optarena drivers list`
        # works without every extra installed) - so the ImportError this
        # test needs to catch only happens once that method actually runs,
        # not at the `from optarena.drivers.X import Y` line above it. The
        # whole attempt has to be inside one try/except, or an environment
        # missing the extra fails with an uncaught ModuleNotFoundError
        # instead of a clean skip (confirmed live: this was happening on
        # the "no docker" CI job, which doesn't install the optional extras).
        try:
            from optarena.drivers.crewai_sdk import CrewAIDriver
            llm = CrewAIDriver().open_session(self._scenario("crewai")).llm
        except ImportError:
            self.skipTest("crewai not installed")
        self.assertEqual(llm.temperature, 0.4)
        self.assertEqual(llm.top_p, 0.8)
        self.assertEqual(llm.seed, 5)

    def test_langgraph_chatopenai_carries_generation_params(self):
        try:
            import langchain_openai  # noqa: F401
            from optarena.drivers.langgraph_sdk import LangGraphDriver
            # create_react_agent compiles the model into an internal graph
            # with no stable public accessor for it - intercept the exact
            # `model=` kwarg it's called with instead of reverse-engineering
            # graph internals that could change between langgraph versions.
            with mock.patch("langgraph.prebuilt.create_react_agent") as fake_create:
                fake_create.return_value = "compiled-graph-placeholder"
                LangGraphDriver().open_session(self._scenario("langgraph"))
            llm = fake_create.call_args.kwargs["model"]
        except ImportError:
            self.skipTest("langgraph/langchain-openai not installed")
        self.assertEqual(llm.temperature, 0.4)
        self.assertEqual(llm.top_p, 0.8)
        self.assertEqual(llm.seed, 5)

    def test_openai_agents_model_settings_carries_generation_params(self):
        try:
            from optarena.drivers.openai_agents_sdk import OpenAIAgentsDriver
            agent, _run_config, client = OpenAIAgentsDriver().open_session(self._scenario("openai-agents"))
        except ImportError:
            self.skipTest("openai-agents not installed")
        self.assertEqual(agent.model_settings.temperature, 0.4)
        self.assertEqual(agent.model_settings.top_p, 0.8)
        self.assertEqual(agent.model_settings.extra_body, {"seed": 5})
        if getattr(client, "close", None) is not None:
            import asyncio
            asyncio.run(client.close())

    def test_autogen_client_create_args_carries_generation_params(self):
        import asyncio
        try:
            from optarena.drivers.autogen_sdk import AutoGenDriver
            _agent, client = asyncio.run(AutoGenDriver().aopen_session(self._scenario("autogen")))
        except ImportError:
            self.skipTest("autogen not installed")
        try:
            create_args = client._create_args
            self.assertEqual(create_args["temperature"], 0.4)
            self.assertEqual(create_args["top_p"], 0.8)
            self.assertEqual(create_args["seed"], 5)
        finally:
            asyncio.run(AutoGenDriver().aclose_session((_agent, client)))

    def test_semantic_kernel_execution_settings_carries_generation_params(self):
        import asyncio
        try:
            from optarena.drivers.semantic_kernel_sdk import SemanticKernelDriver
            session = asyncio.run(SemanticKernelDriver().aopen_session(self._scenario("semantic-kernel")))
        except ImportError:
            self.skipTest("semantic-kernel not installed")
        try:
            settings = session["agent"].arguments.execution_settings["default"]
            self.assertEqual(settings.temperature, 0.4)
            self.assertEqual(settings.top_p, 0.8)
            self.assertEqual(settings.seed, 5)
        finally:
            asyncio.run(session["client"].close())

    def test_smolagents_model_kwargs_carries_generation_params(self):
        try:
            from optarena.drivers.smolagents_sdk import SmolAgentsDriver
            model = SmolAgentsDriver().open_session(self._scenario("smolagents"))
        except ImportError:
            self.skipTest("smolagents not installed")
        self.assertEqual(model.kwargs["temperature"], 0.4)
        self.assertEqual(model.kwargs["top_p"], 0.8)
        self.assertEqual(model.kwargs["seed"], 5)


if __name__ == "__main__":
    unittest.main()
