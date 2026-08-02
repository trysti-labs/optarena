"""
OptArena CLI smoke tests (stdlib only - run with: python -m unittest discover tests).

A-33: `optarena/cli.py` was the least-covered module in the project (33%), and
what it encodes is a CONTRACT: the exit codes documented as CI gates
(`regression` 0/1/3, `scan` 1 on an error-level finding, `run` 1 on any failed
case, 2 on a usage error) plus the legacy command aliases that existing
scripts still use. None of that was asserted anywhere, which is how a broken
alias path (`optarena --debug list runs` failing while `optarena list runs`
worked) shipped unnoticed.

Every test here drives `cli.main(argv)` in-process against a temporary
results directory. No container engine, no backend, no network.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from optarena import cli, store


def _run_cli(argv):
    """Invoke the CLI, returning (exit_code, stdout, stderr). SystemExit (from
    argparse's own error/`--version` paths) is captured rather than escaping."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv)
    except SystemExit as exc:                      # argparse usage errors / --version
        code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue(), err.getvalue()


class CliBaseTest(unittest.TestCase):
    def setUp(self):
        self.results = Path(tempfile.mkdtemp(prefix="optarena_test_cli_"))
        self._orig = (store.RESULTS_DIR, store.RUNS_DIR)
        store.set_results_dir(self.results)
        self.addCleanup(lambda: setattr_all(store, self._orig))

    def _save_run(self, name, cases, run_id=None, manifest=None):
        """Write a synthetic run record straight to the store."""
        run_id = run_id or f"20260101-0000{len(list(self.results.glob('runs/*.json'))):02d}_{name}_aaaaaaaa"
        record = {
            "run_id": run_id,
            "scenario": {"name": name, "driver": "ollama-chat",
                         "backend": {"model": "m", "base_url": "http://localhost:11434"}},
            "started_at": "2026-01-01T00:00:00",
            "manifest": manifest or {"oracle_version": 1, "case_set_hash": "h", "trials": 1,
                                     "case_count": len(cases)},
            "status": "completed",
            "cases": [{"name": n, "passed": p, "duration_s": d, "failures": [] if p else ["nope"],
                       "error": None, "execution_ok": True, "extra": {}}
                      for n, p, d in cases],
        }
        from optarena.metrics import aggregate
        record["summary"] = aggregate(record["cases"])
        (self.results / "runs").mkdir(parents=True, exist_ok=True)
        (self.results / "runs" / f"{run_id}.json").write_text(json.dumps(record), encoding="utf-8")
        store.rebuild_index()
        return run_id


def setattr_all(module, pair):
    module.RESULTS_DIR, module.RUNS_DIR = pair


class VersionAndGlobalFlagTests(CliBaseTest):
    """A-25/A-22: --version exists, and global flags don't break the legacy
    command aliases."""

    def test_version_flag(self):
        from optarena import __version__
        code, out, _ = _run_cli(["--version"])
        self.assertEqual(code, 0)
        self.assertIn(__version__, out)

    def test_legacy_aliases_still_work(self):
        for argv in (["list", "drivers"], ["list", "cases"], ["list", "runs"]):
            with self.subTest(argv=argv):
                code, _, err = _run_cli(argv)
                self.assertEqual(code, 0, err)

    def test_legacy_alias_survives_a_leading_global_flag(self):
        # The regression: --debug (a valueless global flag) stopped the argv
        # rewrite, so `list` reached argparse and failed as an invalid choice.
        for argv in (["--debug", "list", "runs"],
                     ["--results-dir", str(self.results), "list", "runs"],
                     ["--debug", "--results-dir", str(self.results), "list", "drivers"]):
            with self.subTest(argv=argv):
                code, _, err = _run_cli(argv)
                self.assertNotIn("invalid choice", err)
                self.assertEqual(code, 0, err)

    def test_unknown_case_name_is_a_clean_error_not_a_traceback(self):
        # A-34: `--cases` typo raised FileNotFoundError straight out of
        # cmd_verify_corpus.
        code, _, err = _run_cli(["cases", "verify", "--cases", "nonexistent_case_xyz"])
        self.assertEqual(code, 2)
        self.assertIn("Unknown case", err)
        self.assertNotIn("Traceback", err)


class ExitCodeContractTests(CliBaseTest):
    """The documented CI-gate exit codes."""

    def test_regression_clean_returns_0(self):
        a = self._save_run("before", [("c1", True, 1.0)])
        b = self._save_run("after", [("c1", True, 1.0)])
        code, out, err = _run_cli(["regression", a, b])
        self.assertEqual(code, 0, err)
        self.assertIn("regressed cases", out)

    def test_regression_with_a_regression_returns_1(self):
        a = self._save_run("before", [("c1", True, 1.0)])
        b = self._save_run("after", [("c1", False, 1.0)])
        code, out, _ = _run_cli(["regression", a, b])
        self.assertEqual(code, 1)
        self.assertIn("c1", out)

    def test_regression_subset_run_is_not_a_regression(self):
        # A-03 through the CLI: comparing a subset against a full baseline
        # must not fail the gate.
        a = self._save_run("before", [("c1", True, 1.0), ("c2", True, 1.0)])
        b = self._save_run("after", [("c1", True, 1.0)])
        code, out, _ = _run_cli(["regression", a, b])
        self.assertEqual(code, 0)
        self.assertIn("only in", out)

    def test_regression_infrastructure_error_returns_3(self):
        a = self._save_run("before", [("c1", True, 1.0)])
        b_id = self._save_run("after", [("c1", False, 1.0)])
        path = self.results / "runs" / f"{b_id}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["cases"][0]["passed"] = True     # not a regression...
        record["cases"][0]["extra"] = {"oracle": {"infrastructure_error": True}}
        path.write_text(json.dumps(record), encoding="utf-8")
        code, out, _ = _run_cli(["regression", a, b_id])
        self.assertEqual(code, 3)
        self.assertIn("INFRASTRUCTURE", out)

    def test_unknown_run_ref_returns_2(self):
        code, _, err = _run_cli(["compare", "nope-a", "nope-b"])
        self.assertEqual(code, 2)
        self.assertIn("error:", err)

    def test_run_without_scenario_or_driver_returns_2(self):
        code, _, err = _run_cli(["run"])
        self.assertEqual(code, 2)
        self.assertIn("Nothing to run", err)

    def test_run_with_a_bad_scenario_file_returns_2_not_a_traceback(self):
        bad = self.results / "bad.json"
        bad.write_text('{"name": "x"}', encoding="utf-8")     # no driver
        code, _, err = _run_cli(["run", "--scenario", str(bad)])
        self.assertEqual(code, 2)
        self.assertIn("error:", err)
        self.assertNotIn("Traceback", err)

    def test_scan_returns_1_on_an_error_level_finding(self):
        target = Path(tempfile.mkdtemp(prefix="optarena_test_scan_"))
        (target / "leak.py").write_text(
            'API_KEY = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8")
        code, out, _ = _run_cli(["scan", str(target)])
        self.assertEqual(code, 1)
        self.assertIn("AWS access key", out)

    def test_scan_returns_0_on_a_clean_directory(self):
        target = Path(tempfile.mkdtemp(prefix="optarena_test_scan_clean_"))
        (target / "ok.py").write_text("print('hello')\n", encoding="utf-8")
        code, out, _ = _run_cli(["scan", str(target)])
        self.assertEqual(code, 0)
        self.assertIn("no findings", out)

    def test_scan_of_a_missing_directory_returns_2(self):
        code, _, err = _run_cli(["scan", str(self.results / "nope")])
        self.assertEqual(code, 2)


class CasesCommandTests(CliBaseTest):
    def test_validate_builtin_catalogue(self):
        code, out, err = _run_cli(["cases", "validate"])
        self.assertEqual(code, 0, err)
        self.assertIn("structurally valid", out)

    def test_validate_reports_an_invalid_directory_as_1(self):
        bad_dir = Path(tempfile.mkdtemp(prefix="optarena_test_badcases_"))
        (bad_dir / "b.json").write_text('{"name": ""}', encoding="utf-8")
        code, _, err = _run_cli(["cases", "validate", "--cases-dir", str(bad_dir)])
        self.assertEqual(code, 1)
        self.assertIn("invalid:", err)

    def test_show_prints_the_oracle(self):
        code, out, err = _run_cli(["cases", "show", "create_factorial"])
        self.assertEqual(code, 0, err)
        self.assertIn("check_command", out)
        self.assertIn("prompt 1", out)

    def test_show_unknown_case_returns_2(self):
        code, _, err = _run_cli(["cases", "show", "no_such_case_xyz"])
        self.assertEqual(code, 2)

    def test_init_scaffolds_a_valid_case(self):
        target = Path(tempfile.mkdtemp(prefix="optarena_test_init_")) / "cases"
        code, out, err = _run_cli(["cases", "init", str(target)])
        self.assertEqual(code, 0, err)
        self.assertTrue((target / "sample_hello.json").is_file())
        # The scaffold must itself pass validation.
        code, _, _ = _run_cli(["cases", "validate", "--cases-dir", str(target)])
        self.assertEqual(code, 0)

    def test_pack_install_and_run_roundtrip(self):
        source = Path(tempfile.mkdtemp(prefix="optarena_test_packsrc_")) / "cases"
        _run_cli(["cases", "init", str(source)])
        out_file = source.parent / "demo.optpack.json"
        code, _, err = _run_cli(["cases", "pack", str(source), "--name", "demo",
                                 "--version", "1.0.0", "--out", str(out_file)])
        self.assertEqual(code, 0, err)
        registry = Path(tempfile.mkdtemp(prefix="optarena_test_packreg_"))
        with mock.patch("optarena.packs.PACKS_DIR", registry):
            code, out, err = _run_cli(["cases", "install", str(out_file)])
            self.assertEqual(code, 0, err)
            code, out, _ = _run_cli(["cases", "packs"])
            self.assertEqual(code, 0)
            self.assertIn("demo@1.0.0", out)


class RunsCommandTests(CliBaseTest):
    """A-23/A-24: the maintenance commands store.py has always pointed at."""

    def test_rebuild_index_recovers_from_a_corrupt_index(self):
        self._save_run("r", [("c1", True, 1.0)])
        (self.results / "index.json").write_text("{ not json", encoding="utf-8")
        code, out, err = _run_cli(["runs", "rebuild-index"])
        self.assertEqual(code, 0, err)
        self.assertIn("rebuilt", out)
        self.assertEqual(len(json.loads((self.results / "index.json").read_text())), 1)

    def test_prune_is_a_dry_run_without_yes(self):
        for i in range(4):
            self._save_run(f"r{i}", [("c1", True, 1.0)])
        code, out, _ = _run_cli(["runs", "prune", "--keep", "1"])
        self.assertEqual(code, 0)
        self.assertIn("would delete", out)
        self.assertEqual(len(list((self.results / "runs").glob("*.json"))), 4)

    def test_prune_keeps_the_newest_n(self):
        for i in range(4):
            self._save_run(f"r{i}", [("c1", True, 1.0)])
        code, out, _ = _run_cli(["runs", "prune", "--keep", "2", "--yes"])
        self.assertEqual(code, 0)
        remaining = sorted(p.name for p in (self.results / "runs").glob("*.json"))
        self.assertEqual(len(remaining), 2)
        self.assertEqual(len(json.loads((self.results / "index.json").read_text())), 2)

    def test_scrub_secrets_redacts_a_legacy_key(self):
        run_id = self._save_run("legacy", [("c1", True, 1.0)])
        path = self.results / "runs" / f"{run_id}.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        record["scenario"]["backend"]["api_key"] = "sk-legacy-secret"
        path.write_text(json.dumps(record), encoding="utf-8")

        # Reports without --yes, and exits non-zero so it can gate CI.
        code, out, _ = _run_cli(["runs", "scrub-secrets"])
        self.assertEqual(code, 1)
        self.assertIn("api_key", out)
        self.assertIn("sk-legacy-secret", path.read_text(encoding="utf-8"))

        code, out, _ = _run_cli(["runs", "scrub-secrets", "--yes"])
        self.assertEqual(code, 0)
        scrubbed = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsNone(scrubbed["scenario"]["backend"]["api_key"])
        self.assertTrue(scrubbed["scenario"]["backend"]["api_key_set"])
        self.assertNotIn("sk-legacy-secret", path.read_text(encoding="utf-8"))

    def test_show_prints_a_per_case_table(self):
        run_id = self._save_run("r", [("c1", True, 1.0), ("c2", False, 2.0)])
        code, out, err = _run_cli(["runs", "show", run_id])
        self.assertEqual(code, 0, err)
        self.assertIn("PASS", out)
        self.assertIn("FAIL", out)


class ReportCommandTests(CliBaseTest):
    """A-27: --out means a file, --out-dir means a directory - no inference."""

    def setUp(self):
        super().setUp()
        self.run_id = self._save_run("r", [("c1", True, 1.0), ("c2", False, 2.0)])

    def test_all_formats_into_a_directory(self):
        out_dir = self.results / "reports-out"
        code, _, err = _run_cli(["report", self.run_id, "--out-dir", str(out_dir)])
        self.assertEqual(code, 0, err)
        for name in ("junit.xml", "report.html", "results.sarif"):
            self.assertTrue((out_dir / name).is_file(), name)

    def test_single_format_to_an_explicit_file(self):
        target = self.results / "nested" / "junit.xml"
        code, _, err = _run_cli(["report", self.run_id, "--format", "junit", "--out", str(target)])
        self.assertEqual(code, 0, err)
        self.assertTrue(target.is_file())
        self.assertIn("<testsuite", target.read_text(encoding="utf-8"))

    def test_out_with_multiple_formats_is_a_usage_error(self):
        # Previously this silently wrote one artifact over another.
        code, _, err = _run_cli(["report", self.run_id, "--format", "all",
                                 "--out", str(self.results / "x")])
        self.assertEqual(code, 2)
        self.assertIn("--out-dir", err)

    def test_out_and_out_dir_together_is_a_usage_error(self):
        code, _, err = _run_cli(["report", self.run_id, "--format", "junit",
                                 "--out", str(self.results / "a"),
                                 "--out-dir", str(self.results / "b")])
        self.assertEqual(code, 2)

    def test_junit_output_is_well_formed_xml(self):
        out_dir = self.results / "reports-xml"
        _run_cli(["report", self.run_id, "--format", "junit", "--out-dir", str(out_dir)])
        import xml.etree.ElementTree as ET
        tree = ET.parse(out_dir / "junit.xml")
        self.assertEqual(tree.getroot().tag, "testsuites")

    def test_sarif_output_is_valid_json(self):
        out_dir = self.results / "reports-sarif"
        _run_cli(["report", self.run_id, "--format", "sarif", "--out-dir", str(out_dir)])
        sarif = json.loads((out_dir / "results.sarif").read_text(encoding="utf-8"))
        self.assertEqual(sarif["version"], "2.1.0")


class DoctorCommandTests(CliBaseTest):
    """A-28: doctor is scriptable."""

    def test_json_output_is_machine_readable(self):
        code, out, _ = _run_cli(["doctor", "--json", "--base-url", "http://127.0.0.1:9"])
        report = json.loads(out)
        self.assertIn("checks", report)
        self.assertIn("ok", report)
        self.assertTrue(all({"label", "ok", "required"} <= set(c) for c in report["checks"]))
        # An unreachable backend is a required check, so the run is not "ok".
        self.assertFalse(report["ok"])
        self.assertEqual(code, 1)

    def test_json_mode_emits_only_json(self):
        _, out, _ = _run_cli(["doctor", "--json", "--base-url", "http://127.0.0.1:9"])
        json.loads(out)     # would raise if the human table leaked into stdout

    def test_human_output_mentions_source_checkout_assets(self):
        _, out, _ = _run_cli(["doctor", "--base-url", "http://127.0.0.1:9"])
        self.assertIn("dashboard/", out)
        self.assertIn("repos/", out)


class ServeCommandTests(CliBaseTest):
    """A-26: bind failures and a missing dashboard are clean errors."""

    def test_port_in_use_is_a_clean_error(self):
        import socket
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        try:
            code, _, err = _run_cli(["serve", "--port", str(port)])
        finally:
            sock.close()
        self.assertEqual(code, 2)
        self.assertIn("cannot bind", err)
        self.assertNotIn("Traceback", err)

    def test_missing_dashboard_is_a_clean_error(self):
        with mock.patch.object(cli, "REPO_ROOT", Path(tempfile.mkdtemp())):
            code, _, err = _run_cli(["serve", "--port", "0"])
        self.assertEqual(code, 2)
        self.assertIn("no dashboard", err)


class RunCommandTests(CliBaseTest):
    """`run` end to end with a stub driver - exit code, saved artifacts, events."""

    def setUp(self):
        super().setUp()
        self.cases_dir = Path(tempfile.mkdtemp(prefix="optarena_test_clicases_"))
        (self.cases_dir / "c1.json").write_text(json.dumps({
            "name": "c1", "prompts": ["do it"],
            "expected_files": [{"path_pattern": "out.txt"}],
        }), encoding="utf-8")

    def _driver(self, passed=True):
        from optarena.drivers.base import CaseResult, Driver

        class _Stub(Driver):
            parallel_safe = True

            def run_case(self, case, scenario, workspace):
                (workspace / "out.txt").write_text("hi\n", encoding="utf-8")
                return CaseResult(name=case["name"], passed=passed, duration_s=0.1,
                                  files=["out.txt"], failures=[] if passed else ["nope"])

        return _Stub()

    def test_successful_run_returns_0_and_saves_a_record(self):
        with mock.patch("optarena.runner.get_driver", return_value=self._driver()):
            code, out, err = _run_cli(["run", "--driver", "ollama-chat", "--name", "t",
                                       "--cases-dir", str(self.cases_dir)])
        self.assertEqual(code, 0, err)
        self.assertIn("1/1 passed", out)
        self.assertEqual(len(list((self.results / "runs").glob("*.json"))), 1)

    def test_failed_case_returns_1(self):
        with mock.patch("optarena.runner.get_driver", return_value=self._driver(passed=False)):
            code, _, _ = _run_cli(["run", "--driver", "ollama-chat", "--name", "t",
                                   "--cases-dir", str(self.cases_dir)])
        self.assertEqual(code, 1)

    def test_empty_case_selection_is_refused(self):
        # H-06 through the CLI: a filter matching nothing must not read as
        # "all green".
        with mock.patch("optarena.runner.get_driver", return_value=self._driver()):
            code, _, err = _run_cli(["run", "--driver", "ollama-chat", "--name", "t",
                                     "--cases-dir", str(self.cases_dir),
                                     "--language", "klingon"])
        self.assertEqual(code, 2)
        self.assertIn("0 cases", err)

    def test_json_events_stream_is_parseable(self):
        with mock.patch("optarena.runner.get_driver", return_value=self._driver()):
            code, out, _ = _run_cli(["run", "--driver", "ollama-chat", "--name", "t",
                                     "--cases-dir", str(self.cases_dir),
                                     "--quiet", "--json-events"])
        self.assertEqual(code, 0)
        events = [json.loads(line) for line in out.splitlines() if line.strip()]
        names = [e["event"] for e in events]
        self.assertIn("run_started", names)
        self.assertIn("case_completed", names)
        self.assertIn("run_completed", names)

    def test_manifest_records_the_tool_version(self):
        from optarena import __version__
        with mock.patch("optarena.runner.get_driver", return_value=self._driver()):
            _run_cli(["run", "--driver", "ollama-chat", "--name", "t",
                      "--cases-dir", str(self.cases_dir)])
        record = json.loads(next((self.results / "runs").glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual(record["manifest"]["optarena_version"], __version__)


class BaselineIncompatiblePreflightWarningTests(CliBaseTest):
    """A-40: `run` warns, before spending any compute, when the chosen
    driver has no file tools and some selected cases can never pass under
    it regardless of the model's answer."""

    def setUp(self):
        super().setUp()
        self.cases_dir = Path(tempfile.mkdtemp(prefix="optarena_test_baselinewarn_"))
        # one case a flat-file writer CAN win, one it structurally cannot
        # (needs two files) - mixed selection, so the test also proves the
        # warning names only the real offender, not the whole set.
        (self.cases_dir / "winnable.json").write_text(json.dumps({
            "name": "winnable", "prompts": ["do it"],
            "expected_files": [{"path_pattern": "out.txt"}],
        }), encoding="utf-8")
        (self.cases_dir / "multifile.json").write_text(json.dumps({
            "name": "multifile", "prompts": ["do it"],
            "expected_files": [{"path_pattern": "a.txt"}, {"path_pattern": "b.txt"}],
        }), encoding="utf-8")

    def _driver(self):
        from optarena.drivers.base import CaseResult, Driver

        class _Stub(Driver):
            parallel_safe = True

            def run_case(self, case, scenario, workspace):
                (workspace / "out.txt").write_text("hi\n", encoding="utf-8")
                return CaseResult(name=case["name"], passed=True, duration_s=0.1, files=["out.txt"])

        return _Stub()

    def test_warns_for_a_file_tools_free_driver(self):
        with mock.patch("optarena.runner.get_driver", return_value=self._driver()):
            code, out, err = _run_cli(["run", "--driver", "ollama-chat", "--name", "t",
                                       "--cases-dir", str(self.cases_dir)])
        self.assertEqual(code, 0, err)
        self.assertIn("NOTE:", out)
        self.assertIn("multifile", out)
        self.assertIn("cannot pass regardless of the model's answer", out)
        # only the genuinely incompatible case is named, not the winnable one
        self.assertNotIn("- winnable", out)

    def test_no_warning_for_a_cli_kind_driver(self):
        # aider has real file tools - nothing to warn about, regardless of
        # which cases are selected. (Doesn't actually invoke aider - the
        # scenario resolution / warning check happens before driver dispatch,
        # and this only asserts on stdout, not exit code, since aider isn't
        # installed in this environment.)
        _code, out, _err = _run_cli(["run", "--driver", "aider", "--name", "t",
                                     "--cases-dir", str(self.cases_dir)])
        self.assertNotIn("NOTE:", out)

    def test_quiet_suppresses_the_warning(self):
        with mock.patch("optarena.runner.get_driver", return_value=self._driver()):
            code, out, err = _run_cli(["run", "--driver", "ollama-chat", "--name", "t",
                                       "--cases-dir", str(self.cases_dir), "--quiet"])
        self.assertEqual(code, 0, err)
        self.assertNotIn("NOTE:", out)

    def test_no_warning_when_no_case_is_incompatible(self):
        with mock.patch("optarena.runner.get_driver", return_value=self._driver()):
            code, out, err = _run_cli(["run", "--driver", "ollama-chat", "--name", "t",
                                       "--cases-dir", str(self.cases_dir), "--cases", "winnable"])
        self.assertEqual(code, 0, err)
        self.assertNotIn("NOTE:", out)


if __name__ == "__main__":
    unittest.main()
