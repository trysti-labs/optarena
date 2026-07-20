"""
OptArena unit tests (stdlib only - run with: python -m unittest discover tests).

Covers the July 2026 expansion: extended oracle assertions, check_command,
trial merging, the driver registry metadata, scenario cases_dir, and the
atomic results store.
"""

import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from optarena.cases import (
    DOCKER_IMAGE_DEFAULT, DOCKER_IMAGES, DockerSandbox, REPOS_DIR, check_expected,
    classify_failure, diff_stats, dockerfile_for, evaluate_case, filter_cases, load_cases,
    prepare_workspace, run_capture, run_check_command,
)
from optarena.compare import (
    _cheaper, compare_runs, format_regression, format_table,
    manifest_compatibility, regression_summary,
)
from optarena.drivers import DRIVERS, get_driver
from optarena.drivers.base import CaseResult, subprocess_env
from optarena.drivers.aider_cli import AiderDriver, parse_aider_metrics
from optarena.drivers.cli_agents import CLIAgentDriver, parse_claude_json_metrics
from optarena.drivers.openai_chat import concrete_target
from optarena.metrics import aggregate, case_deltas
from optarena.pricing import estimate_cost, is_local_backend, price_for
from optarena.runner import _merge_trials, run_scenario, safe_run_name
from optarena.scenario import Backend, Scenario
from optarena.schema import SchemaError, validate_case, validate_scenario, validate_unique_case_names
from optarena.verify import variants_for, verify_cases


class OracleTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_"))
        (self.ws / "out.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8")

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
        self._env = mock.patch.dict(os.environ, {"OPTARENA_NO_DOCKER": "1"})
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
        self.assertEqual(oracle["sandbox"], "host")   # OPTARENA_NO_DOCKER=1 in setUp

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


class DockerCheckCommandTests(unittest.TestCase):
    """Docker sandboxing for check_command: command construction + fallback."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_"))
        import optarena.cases as cases_mod
        self.cases_mod = cases_mod
        # Reset the module-level docker-availability cache before each test.
        self._orig = (cases_mod._docker_checked, cases_mod._docker_ok,
                      cases_mod._docker_warned, dict(cases_mod._active_sandboxes))
        cases_mod._docker_checked = False
        cases_mod._docker_ok = False
        cases_mod._docker_warned = False
        cases_mod._active_sandboxes.clear()

    def tearDown(self):
        (self.cases_mod._docker_checked, self.cases_mod._docker_ok,
         self.cases_mod._docker_warned, sandboxes) = self._orig
        self.cases_mod._active_sandboxes.clear()
        self.cases_mod._active_sandboxes.update(sandboxes)

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
            os.environ.pop("OPTARENA_NO_DOCKER", None)
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
            os.environ.pop("OPTARENA_NO_DOCKER", None)
            self.assertFalse(self.cases_mod._docker_available())

    def test_no_docker_env_forces_local_execution(self):
        calls = []

        def fake_capture(cmd, **kwargs):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        # Host exec now goes through run_capture (process-group aware), not
        # subprocess.run directly - see H-11 / _run_check_command_local.
        with mock.patch.object(self.cases_mod, "run_capture", side_effect=fake_capture), \
             mock.patch.dict(os.environ, {"OPTARENA_NO_DOCKER": "1"}):
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
            os.environ.pop("OPTARENA_NO_DOCKER", None)
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
            os.environ.pop("OPTARENA_NO_DOCKER", None)
            failures, oracle = self.cases_mod.run_check_command({"check_command": "echo hi"}, self.ws)

        self.assertEqual(failures, [])
        self.assertEqual(oracle["sandbox"], "host")
        self.assertTrue(oracle["ran"])

    def test_no_docker_env_still_sufficient_on_its_own(self):
        # OPTARENA_NO_DOCKER=1 alone (the pre-existing, already-explicit
        # opt-out) must keep working unchanged - it must not also require
        # OPTARENA_ALLOW_UNSAFE_HOST_EXEC.
        def fake_capture(cmd, **kwargs):
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.cases_mod, "run_capture", side_effect=fake_capture), \
             mock.patch.dict(os.environ, {"OPTARENA_NO_DOCKER": "1"}, clear=False):
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
        (self.root / "case_a").mkdir()
        (self.root / "case_b" / "t1").mkdir(parents=True)
        import optarena.cases as cases_mod
        self.cases_mod = cases_mod
        self._orig = (cases_mod._docker_checked, cases_mod._docker_ok,
                      cases_mod._docker_warned, dict(cases_mod._active_sandboxes))
        cases_mod._docker_checked = False
        cases_mod._docker_ok = False
        cases_mod._docker_warned = False
        cases_mod._active_sandboxes.clear()

    def tearDown(self):
        (self.cases_mod._docker_checked, self.cases_mod._docker_ok,
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
            os.environ.pop("OPTARENA_NO_DOCKER", None)
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
            os.environ.pop("OPTARENA_NO_DOCKER", None)
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

    def test_empty_case_set_refused_by_default(self):
        sc = Scenario(name="x", driver="aider", cases=[], cases_dir=str(self.empty_dir))
        with mock.patch("optarena.runner.get_driver") as get_driver_mock:
            with self.assertRaises(ValueError) as ctx:
                run_scenario(sc)
            get_driver_mock.assert_not_called()
        self.assertIn("0 cases", str(ctx.exception))

    def test_empty_case_set_allowed_with_flag(self):
        sc = Scenario(name="x", driver="aider", cases=[], cases_dir=str(self.empty_dir))
        fake_driver = mock.Mock(parallel_safe=False, caches_results=False)
        with mock.patch("optarena.runner.get_driver", return_value=fake_driver):
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
        with mock.patch("optarena.runner.get_driver", return_value=self._fake_driver()), \
             mock.patch("optarena.runner.DockerSandbox") as sandbox_cls:
            run_scenario(sc, parallel=4)
        sandbox_cls.assert_not_called()

    def test_no_shared_sandbox_for_custom_case_pack(self):
        # C-05: a custom cases_dir is untrusted input - it must never share
        # one whole-run-root mount across its cases, even serially.
        sc = Scenario(name="x", driver="aider", cases_dir=str(self.cases_dir))
        with mock.patch("optarena.runner.get_driver", return_value=self._fake_driver()), \
             mock.patch("optarena.runner.DockerSandbox") as sandbox_cls:
            run_scenario(sc, parallel=1)
        sandbox_cls.assert_not_called()

    def test_sandbox_still_started_for_builtin_corpus_serial(self):
        # The built-in catalogue (repo-controlled, self-verified) keeps the
        # fast shared-container path for serial runs.
        sc = Scenario(name="x", driver="aider", cases=["create_factorial"])
        with mock.patch("optarena.runner.get_driver",
                        return_value=self._fake_driver("create_factorial")), \
             mock.patch("optarena.runner.DockerSandbox") as sandbox_cls:
            run_scenario(sc, parallel=1)
        sandbox_cls.assert_called_once()


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


class UITrialsPlumbingTests(unittest.TestCase):
    """
    M-09: a caching driver (VS Code UI) can't be repeated by the runner's own
    per-case loop, so instead of silently dropping --trials to 1, the runner
    hands the trial count to the driver (which repeats each case inside the
    harness). Verify the count is plumbed through and the runner doesn't also
    double-run.
    """

    def setUp(self):
        self.cases_dir = Path(tempfile.mkdtemp(prefix="optarena_test_uitrials_"))
        (self.cases_dir / "c1.json").write_text(json.dumps({
            "name": "c1", "prompts": ["do it"],
        }), encoding="utf-8")

    def test_trials_handed_to_caching_driver_not_dropped(self):
        driver = mock.Mock(parallel_safe=False, caches_results=True, trials=1)
        driver.run_case.return_value = CaseResult(name="c1", passed=True, duration_s=0.1)
        sc = Scenario(name="x", driver="cline-ui", cases_dir=str(self.cases_dir))
        with mock.patch("optarena.runner.get_driver", return_value=driver):
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


class WorkspaceCleanupTests(unittest.TestCase):
    """H-11: the mkdtemp workspace a run/verify creates must be removed once
    it's done (never read again), unless the user asks to keep it."""

    def setUp(self):
        self.cases_dir = Path(tempfile.mkdtemp(prefix="optarena_test_wscleanup_"))
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
        with mock.patch("optarena.runner.get_driver", return_value=self._fake_driver()), \
             mock.patch("optarena.runner.tempfile.mkdtemp", return_value=str(made)):
            run_scenario(sc)
        self.assertFalse(made.exists(), "run workspace was not cleaned up")

    def test_run_scenario_keeps_workspace_when_requested(self):
        made = Path(tempfile.mkdtemp(prefix="optarena_test_keptws_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(made, ignore_errors=True))
        sc = Scenario(name="x", driver="aider", cases_dir=str(self.cases_dir))
        with mock.patch("optarena.runner.get_driver", return_value=self._fake_driver()), \
             mock.patch("optarena.runner.tempfile.mkdtemp", return_value=str(made)):
            run_scenario(sc, keep_workspace=True)
        self.assertTrue(made.exists(), "workspace should have been kept for debugging")

    def test_run_scenario_does_not_delete_caller_supplied_workspace(self):
        supplied = Path(tempfile.mkdtemp(prefix="optarena_test_suppliedws_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(supplied, ignore_errors=True))
        sc = Scenario(name="x", driver="aider", cases_dir=str(self.cases_dir))
        with mock.patch("optarena.runner.get_driver", return_value=self._fake_driver()):
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


class VerifyCorpusTests(unittest.TestCase):
    """verify-corpus: reference must pass the real oracle, broken must fail."""

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"OPTARENA_NO_DOCKER": "1"})
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


class SafeRunNameTests(unittest.TestCase):
    """Model-derived scenario names must survive becoming filenames."""

    def test_openrouter_slash_and_ollama_colon_squashed(self):
        self.assertEqual(safe_run_name("ollama-chat-qwen/qwen-2.5"), "ollama-chat-qwen-qwen-2.5")
        self.assertEqual(safe_run_name("ollama-chat-qwen3-coder:30b"), "ollama-chat-qwen3-coder-30b")

    def test_plain_names_unchanged(self):
        self.assertEqual(safe_run_name("cline-selfopt"), "cline-selfopt")
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


class RegistryTests(unittest.TestCase):
    def test_all_registered_drivers_instantiate(self):
        for name, meta in DRIVERS.items():
            if name == "crewai":
                continue   # optional dependency
            driver = get_driver(name)
            self.assertTrue(hasattr(driver, "run_case"), name)
            self.assertIn(meta["kind"], ("ui", "cli", "sdk", "baseline"))
            self.assertIn(meta["backend"], ("scenario", "fixed"))

    def test_fixed_backend_drivers_are_marked(self):
        self.assertEqual(DRIVERS["claude-code"]["backend"], "fixed")
        self.assertEqual(DRIVERS["codex"]["backend"], "fixed")
        self.assertEqual(DRIVERS["aider"]["backend"], "scenario")


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
        (project / "scenarios").mkdir()
        (project / "scenarios" / "my-cases").mkdir()
        (project / "scenarios" / "my-cases" / "c1.json").write_text(
            json.dumps({"name": "c1", "prompts": ["x"]}), encoding="utf-8")
        scenario_file = project / "scenarios" / "sc.json"
        scenario_file.write_text(json.dumps({
            "name": "x", "driver": "aider", "cases_dir": "my-cases",
        }), encoding="utf-8")

        elsewhere = Path(tempfile.mkdtemp(prefix="optarena_test_elsewhere_"))
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
        (d / "a_case.json").write_text(json.dumps(
            {"name": "custom", "prompts": ["p"], "expected_files": []}), encoding="utf-8")
        cases = load_cases(names=[], cases_dir=str(d))
        self.assertEqual(cases, [])

    def test_language_filter_resolves_to_matching_case_names_only(self):
        from optarena.cli import _scenario_from_args
        d = Path(tempfile.mkdtemp())
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

    def test_duplicate_case_names_rejected(self):
        with self.assertRaises(SchemaError):
            validate_unique_case_names([{"name": "a"}, {"name": "b"}, {"name": "a"}])
        validate_unique_case_names([{"name": "a"}, {"name": "b"}])

    def test_load_cases_rejects_malformed_case_file(self):
        d = Path(tempfile.mkdtemp(prefix="optarena_test_badcase_"))
        (d / "bad.json").write_text(json.dumps({"name": "x", "chekc_command": "echo hi"}),
                                    encoding="utf-8")
        with self.assertRaises(SchemaError):
            load_cases(cases_dir=str(d))

    def test_load_cases_rejects_duplicate_names_across_files(self):
        d = Path(tempfile.mkdtemp(prefix="optarena_test_dupcase_"))
        (d / "a1.json").write_text(json.dumps({"name": "dup", "prompts": ["x"]}), encoding="utf-8")
        (d / "a2.json").write_text(json.dumps({"name": "dup", "prompts": ["y"]}), encoding="utf-8")
        with self.assertRaises(SchemaError):
            load_cases(cases_dir=str(d))

    def test_scenario_from_file_rejects_malformed_scenario(self):
        d = Path(tempfile.mkdtemp(prefix="optarena_test_badsc_"))
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
        (self.root / "case_py").mkdir()
        (self.root / "case_go").mkdir()
        import optarena.cases as cases_mod
        self.cases_mod = cases_mod
        self._orig = (cases_mod._docker_checked, cases_mod._docker_ok,
                      cases_mod._docker_warned, dict(cases_mod._active_sandboxes))
        cases_mod._docker_checked = False
        cases_mod._docker_ok = False
        cases_mod._docker_warned = False
        cases_mod._active_sandboxes.clear()

    def tearDown(self):
        (self.cases_mod._docker_checked, self.cases_mod._docker_ok,
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
            os.environ.pop("OPTARENA_NO_DOCKER", None)
            py_sandbox = DockerSandbox(self.root, image="optarena-tester-python:latest")
            go_sandbox = DockerSandbox(self.root, image="optarena-tester-go:latest")
            py_sandbox.start()
            go_sandbox.start()
            try:
                self.assertEqual(len(started), 2, "expected one container per distinct image")
                self.assertIs(self.cases_mod._active_sandboxes["optarena-tester-python:latest"], py_sandbox)
                self.assertIs(self.cases_mod._active_sandboxes["optarena-tester-go:latest"], go_sandbox)

                py_case = {"check_command": "echo hi", "docker_image": "optarena-tester-python:latest"}
                go_case = {"check_command": "echo hi", "docker_image": "optarena-tester-go:latest"}
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
        self.store.set_results_dir(new_dir)
        cmp = {"a": {"label": "a"}, "b": {"label": "b"}}
        path = compare_mod.save_comparison(cmp)
        self.assertTrue(str(path).startswith(str(new_dir)))

    def test_cli_results_dir_flag_overrides_default(self):
        from optarena.cli import main
        new_dir = Path(tempfile.mkdtemp(prefix="optarena_test_cliresults_"))
        rc = main(["--results-dir", str(new_dir), "list", "runs"])
        self.assertEqual(rc, 0)
        self.assertEqual(self.store.RESULTS_DIR, new_dir)


class RichMetricsTests(unittest.TestCase):
    """diff_stats (files/lines changed) and classify_failure (syntax/compile/assertion/timeout bucketing)."""

    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_test_"))

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
        with mock.patch.dict(os.environ, {"OPTARENA_NO_DOCKER": "1"}):
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

    def test_unknown_model_is_free(self):
        self.assertEqual(estimate_cost("gemma3:1b", 5000, 5000), 0.0)

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


class ManifestTests(unittest.TestCase):
    """M-01: every run records an immutable case-set/oracle/trials identity."""

    def _run_with_cases(self, name, cases_dir, extra_case=None):
        made = Path(tempfile.mkdtemp(prefix="optarena_manifest_ws_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(made, ignore_errors=True))
        sc = Scenario(name=name, driver="aider", cases_dir=str(cases_dir))
        driver = mock.Mock(parallel_safe=False, caches_results=False)
        driver.run_case.return_value = CaseResult(name="c1", passed=True, duration_s=0.1)
        with mock.patch("optarena.runner.get_driver", return_value=driver), \
             mock.patch("optarena.runner.tempfile.mkdtemp", return_value=str(made)):
            return run_scenario(sc)

    def setUp(self):
        self.cases_dir = Path(tempfile.mkdtemp(prefix="optarena_manifest_cases_"))
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


if __name__ == "__main__":
    unittest.main()
