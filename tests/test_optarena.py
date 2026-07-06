"""
OptArena unit tests (stdlib only - run with: python -m unittest discover tests).

Covers the July 2026 expansion: extended oracle assertions, check_command,
trial merging, the driver registry metadata, scenario cases_dir, and the
atomic results store.
"""

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from optarena.cases import (
    DOCKER_IMAGE_DEFAULT, DOCKER_IMAGES, DockerSandbox, check_expected, classify_failure,
    diff_stats, dockerfile_for, evaluate_case, filter_cases, load_cases, run_check_command,
)
from optarena.compare import compare_runs, format_regression, regression_summary
from optarena.drivers import DRIVERS, get_driver
from optarena.drivers.base import CaseResult
from optarena.metrics import aggregate
from optarena.pricing import estimate_cost, is_local_backend, price_for
from optarena.runner import _merge_trials
from optarena.scenario import Backend, Scenario


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
        ok = {"check_command": 'python -c "import out; assert out.add(1,2)==3"'}
        failures, oracle = run_check_command(ok, self.ws)
        self.assertEqual(failures, [])
        self.assertTrue(oracle["ran"])
        self.assertEqual(oracle["exit_code"], 0)
        self.assertIn(oracle["sandbox"], ("host", "docker"))
        self.assertIsInstance(oracle["duration_s"], float)

        bad = {"check_command": 'python -c "raise SystemExit(2)"'}
        failures, oracle = run_check_command(bad, self.ws)
        self.assertTrue(failures and "exit 2" in failures[0])
        self.assertEqual(oracle["exit_code"], 2)

    def test_evaluate_case_skips_command_when_files_fail(self):
        case = {"expected_files": [{"path_pattern": "missing.py"}],
                "check_command": 'python -c "raise SystemExit(9)"'}
        failures, oracle = evaluate_case(case, [], self.ws)
        self.assertEqual(len(failures), 1)          # only the file failure
        self.assertIn("missing.py", failures[0])
        self.assertFalse(oracle["ran"])             # check_command never invoked


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
            "check_command": "python test_add.py",
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
            "check_command": "python test_add.py",
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

        def fake_run(args, **kwargs):
            calls.append(args)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(self.cases_mod.subprocess, "run", side_effect=fake_run), \
             mock.patch.dict(os.environ, {"OPTARENA_NO_DOCKER": "1"}):
            failures, oracle = self.cases_mod.run_check_command({"check_command": "echo hi"}, self.ws)

        self.assertEqual(failures, [])
        self.assertEqual(oracle["sandbox"], "host")

        # shell=True local call (a bare string, not a "docker" argv list)
        self.assertEqual(calls, ["echo hi"])


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

    def test_error_only_when_all_trials_error(self):
        merged = _merge_trials("c", [self._result(False, error="boom"),
                                     self._result(True)])
        self.assertIsNone(merged.error)
        merged = _merge_trials("c", [self._result(False, error="boom"),
                                     self._result(False, error="boom")])
        self.assertEqual(merged.error, "boom")


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


class ScenarioTests(unittest.TestCase):
    def test_cases_dir_roundtrip(self):
        sc = Scenario(name="x", driver="aider", cases_dir="my/cases")
        again = Scenario.from_dict(sc.to_dict())
        self.assertEqual(again.cases_dir, "my/cases")

    def test_openai_base_never_doubles_v1(self):
        self.assertEqual(Backend(base_url="http://h:1/v1").openai_base, "http://h:1/v1")
        self.assertEqual(Backend(base_url="http://h:1").openai_base, "http://h:1/v1")

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

    def test_classify_failure_none_when_passed(self):
        info = {"ran": True, "exit_code": 0, "output": "PASS", "check_command": "python3 t.py"}
        self.assertIsNone(classify_failure(info))

    def test_evaluate_case_attaches_diff_and_failure_class(self):
        (self.ws / "add.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        case = {
            "expected_files": [{"path_pattern": "add.py"}],
            "test_setup_files": {"test_add.py": "import add\nassert add.add(2, 3) == 5\n"},
            "check_command": "python test_add.py",
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
