"""
`DockerSandbox.exec_attached()`/`stop()`: the long-lived attached-process
extension to the sandbox container lifecycle (`optarena/_cases/_sandbox.py`),
added for sandboxed-real MCP server execution. Existing `exec()`/container-
start/-stop coverage lives in `test_optarena.py`'s `DockerSandbox*Tests`
classes (mocking `subprocess.run`); this file is scoped to the new
`Popen`-based attached-process path, mocked separately since it's a
different subprocess API.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

import optarena._cases._sandbox as cases_mod


class ExecAttachedTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.root = Path(tempfile.mkdtemp(prefix="optarena_test_sandbox_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        (self.root / "case_a").mkdir()
        self.sandbox = cases_mod.DockerSandbox(self.root, image="optarena-tester:latest")
        self.sandbox.name = "optarena-sandbox-test123"

    def test_builds_docker_exec_dash_i_with_correct_workdir_and_command(self):
        fake_proc = mock.Mock(spec=subprocess.Popen)
        fake_proc.stderr = None   # spec=Popen has no instance attrs; None -> drain guard skips
        with mock.patch.object(cases_mod.subprocess, "Popen", return_value=fake_proc) as popen:
            result = self.sandbox.exec_attached(["mcp-server-filesystem", "."], self.root / "case_a")

        self.assertIs(result, fake_proc)
        args, kwargs = popen.call_args
        argv = args[0]
        self.assertEqual(argv, [
            cases_mod.container_engine(), "exec", "-i", "-w", "/workspace/case_a",
            "optarena-sandbox-test123", "mcp-server-filesystem", ".",
        ])
        self.assertEqual(kwargs["stdin"], subprocess.PIPE)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)
        self.assertTrue(kwargs["text"])
        # No `timeout` wrapper unlike exec() - the caller (MCPStdioClient)
        # owns per-request timeouts, not process-level lifecycle.
        self.assertNotIn("timeout", argv)

    def test_tracks_attached_process_for_teardown(self):
        fake_proc = mock.Mock(spec=subprocess.Popen)
        fake_proc.stderr = None
        with mock.patch.object(cases_mod.subprocess, "Popen", return_value=fake_proc):
            self.sandbox.exec_attached(["mcp-server-filesystem", "."], self.root / "case_a")
        self.assertIn(fake_proc, self.sandbox._attached)

    def test_env_becomes_dash_e_flags_before_the_workdir(self):
        fake_proc = mock.Mock(spec=subprocess.Popen)
        fake_proc.stderr = None
        with mock.patch.object(cases_mod.subprocess, "Popen", return_value=fake_proc) as popen:
            self.sandbox.exec_attached(["srv"], self.root / "case_a",
                                       env={"OPTARENA_SANDBOX_NAME": "optarena-sandbox-test123"})
        argv = popen.call_args[0][0]
        i = argv.index("-e")
        self.assertEqual(argv[i + 1], "OPTARENA_SANDBOX_NAME=optarena-sandbox-test123")
        self.assertLess(i, argv.index("-w"))

    def test_stderr_drain_captures_tail_from_a_real_process_without_deadlock(self):
        # B-1: a server writing far more than the OS pipe buffer (~64KB) to
        # stderr must neither block nor be lost - drive the module-level
        # drain helper against a REAL subprocess pipe, no Docker needed.
        proc = subprocess.Popen(
            [__import__("sys").executable, "-c",
             "import sys\n"
             "for i in range(5000):\n"
             "    print('line %d: %s' % (i, 'x' * 80), file=sys.stderr)\n"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        tail = cases_mod._start_stderr_drain(proc.stderr)
        proc.wait(timeout=30)   # would deadlock here if nothing drained ~400KB of stderr
        import time
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (not tail or "4999" not in tail[-1]):
            time.sleep(0.05)
        self.assertEqual(len(tail), 200)        # bounded, kept the newest
        self.assertIn("line 4999", tail[-1])

    def test_stderr_drain_guard_skips_none_stream(self):
        tail = cases_mod._start_stderr_drain(None)
        self.assertEqual(len(tail), 0)

    def test_stop_terminates_a_still_running_attached_process(self):
        fake_proc = mock.Mock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None  # still running
        self.sandbox._attached.append(fake_proc)
        self.sandbox.active = False  # isolate: don't also exercise the container-stop path here

        self.sandbox.stop()

        fake_proc.terminate.assert_called_once()
        fake_proc.wait.assert_called_once()
        fake_proc.kill.assert_not_called()
        self.assertEqual(self.sandbox._attached, [])

    def test_stop_kills_attached_process_if_terminate_does_not_exit_in_time(self):
        fake_proc = mock.Mock(spec=subprocess.Popen)
        fake_proc.poll.return_value = None
        fake_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="docker exec", timeout=5)
        self.sandbox._attached.append(fake_proc)
        self.sandbox.active = False

        self.sandbox.stop()

        fake_proc.terminate.assert_called_once()
        fake_proc.kill.assert_called_once()

    def test_stop_skips_already_exited_attached_process(self):
        fake_proc = mock.Mock(spec=subprocess.Popen)
        fake_proc.poll.return_value = 0  # already exited
        self.sandbox._attached.append(fake_proc)
        self.sandbox.active = False

        self.sandbox.stop()

        fake_proc.terminate.assert_not_called()
        self.assertEqual(self.sandbox._attached, [])

    def test_stop_still_stops_the_container_after_tearing_down_attached_processes(self):
        fake_proc = mock.Mock(spec=subprocess.Popen)
        fake_proc.poll.return_value = 0
        self.sandbox._attached.append(fake_proc)
        self.sandbox.active = True
        cases_mod._active_sandboxes[self.sandbox.image] = self.sandbox
        self.addCleanup(cases_mod._active_sandboxes.pop, self.sandbox.image, None)

        with mock.patch.object(cases_mod.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0)
            self.sandbox.stop()

        run.assert_called_once()
        self.assertEqual(run.call_args[0][0][:2], [cases_mod.container_engine(), "stop"])
        self.assertFalse(self.sandbox.active)


class ExtraRunArgsTests(unittest.TestCase):
    """`extra_run_args` (added for the database sandboxed service, which
    needs CAP_SETUID/CAP_SETGID for its startup script to `su postgres` -
    real Postgres refuses to run as root outright) - a per-INSTANCE addition
    to `docker run`'s argv, not a change to the shared _HARDENING_ARGS every
    other image gets."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self.root = Path(tempfile.mkdtemp(prefix="optarena_test_sandbox_extra_"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.root, ignore_errors=True))
        self._orig = (cases_mod._docker_checked_at, cases_mod._docker_ok, cases_mod._docker_warned)
        cases_mod._docker_checked_at = -1.0
        cases_mod._docker_ok = False
        cases_mod._docker_warned = False

    def tearDown(self):
        cases_mod._docker_checked_at, cases_mod._docker_ok, cases_mod._docker_warned = self._orig

    def _fake_run(self, calls):
        def fake(args, **kwargs):
            calls.append(args)
            if args[:2] == ["docker", "info"]:
                return mock.Mock(returncode=0)
            if args[:3] == ["docker", "image", "inspect"]:
                return mock.Mock(returncode=0)
            if args[:3] == ["docker", "run", "-d"]:
                return mock.Mock(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected subprocess call: {args}")
        return fake

    def test_extra_run_args_appear_in_docker_run_argv(self):
        calls = []
        sandbox = cases_mod.DockerSandbox(self.root, image="optarena-tester:latest",
                                          extra_run_args=["--cap-add", "SETUID", "--cap-add", "SETGID"])
        with mock.patch.object(cases_mod.subprocess, "run", side_effect=self._fake_run(calls)):
            sandbox.start()
        run_argv = next(c for c in calls if c[:3] == ["docker", "run", "-d"])
        self.assertIn("SETUID", run_argv)
        self.assertIn("SETGID", run_argv)

    def test_no_extra_run_args_by_default(self):
        calls = []
        sandbox = cases_mod.DockerSandbox(self.root, image="optarena-tester:latest")
        with mock.patch.object(cases_mod.subprocess, "run", side_effect=self._fake_run(calls)):
            sandbox.start()
        run_argv = next(c for c in calls if c[:3] == ["docker", "run", "-d"])
        self.assertNotIn("SETUID", run_argv)

    def test_network_defaults_to_none(self):
        calls = []
        sandbox = cases_mod.DockerSandbox(self.root, image="optarena-tester:latest")
        with mock.patch.object(cases_mod.subprocess, "run", side_effect=self._fake_run(calls)):
            sandbox.start()
        run_argv = next(c for c in calls if c[:3] == ["docker", "run", "-d"])
        self.assertEqual(run_argv[run_argv.index("--network") + 1], "none")

    def test_network_override_reaches_docker_run_argv(self):
        # package_registry's real server has no offline-registry option -
        # its registry entry overrides network to "bridge". A single
        # --network flag, not appended alongside the default: confirmed
        # empirically that Docker rejects two --network flags outright
        # ("conflicting options"), so this MUST replace, not layer.
        calls = []
        sandbox = cases_mod.DockerSandbox(self.root, image="optarena-tester:latest", network="bridge")
        with mock.patch.object(cases_mod.subprocess, "run", side_effect=self._fake_run(calls)):
            sandbox.start()
        run_argv = next(c for c in calls if c[:3] == ["docker", "run", "-d"])
        self.assertEqual(run_argv.count("--network"), 1)
        self.assertEqual(run_argv[run_argv.index("--network") + 1], "bridge")


if __name__ == "__main__":
    unittest.main()
