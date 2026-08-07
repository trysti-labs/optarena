"""
The CLI-layer half of the `--tool-service`/`--tags`/`--like`/`cases groups`
work: `filter_cases`'s new params and `_scenario_from_args`'s wiring already
have dedicated coverage in `test_optarena.py::ScenarioTests` (alongside the
existing `--language`/`--framework` tests they extend) and `_tag_expr.py`'s
parser has its own file (`test_tag_expr.py`). This file covers what's left:
`cmd_case_groups` (real new aggregation logic, not just flag passthrough)
and the F-04 "malformed input reads as a clean one-line error, not a raw
traceback" behavior for a bad `--tags` expression reaching `cmd_list` and
`cmd_verify_corpus` - both call `filter_cases` directly and previously had
no way to raise anything beyond what `load_cases` could already raise.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from optarena.cli._cases_cmds import cmd_case_groups, cmd_list, cmd_verify_corpus


def _write_case(d: Path, name: str, **extra) -> None:
    (d / f"{name}.json").write_text(json.dumps({"name": name, **extra}), encoding="utf-8")


class CaseGroupsCommandTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="optarena_test_groups_"))
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)

    def test_reports_tool_service_counts(self):
        _write_case(self.d, "a", tool_service="build_tools", tools=[], expected_calls=[{"tool": "x"}])
        _write_case(self.d, "b", tool_service="build_tools", tools=[], expected_calls=[{"tool": "x"}])
        _write_case(self.d, "c", tool_service="observability", tools=[], expected_calls=[{"tool": "y"}])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_case_groups(argparse.Namespace(cases_dir=str(self.d)))
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("build_tools", out)
        self.assertIn("2", out)
        self.assertIn("observability", out)

    def test_reports_language_counts(self):
        _write_case(self.d, "py1", language="python", prompts=["p"], expected_files=[])
        _write_case(self.d, "py2", language="python", prompts=["p"], expected_files=[])
        _write_case(self.d, "go1", language="go", prompts=["p"], expected_files=[])
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_case_groups(argparse.Namespace(cases_dir=str(self.d)))
        out = buf.getvalue()
        self.assertIn("python", out)
        self.assertIn("go", out)

    def test_reports_tag_counts(self):
        _write_case(self.d, "a", tags=["slow", "integration"], prompts=["p"], expected_files=[])
        _write_case(self.d, "b", tags=["slow"], prompts=["p"], expected_files=[])
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_case_groups(argparse.Namespace(cases_dir=str(self.d)))
        out = buf.getvalue()
        self.assertIn("slow", out)
        self.assertIn("integration", out)

    def test_empty_directory_produces_no_group_sections(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cmd_case_groups(argparse.Namespace(cases_dir=str(self.d)))
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue(), "")


class MalformedTagsCleanErrorTests(unittest.TestCase):
    """A bad --tags expression must read as a one-line CLI error (F-04),
    not an uncaught TagExpressionError traceback - cmd_list and
    cmd_verify_corpus both call filter_cases directly and previously had
    no path for it to raise anything at all."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="optarena_test_badtags_"))
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        _write_case(self.d, "a", prompts=["p"], expected_files=[])

    def test_cmd_list_reports_clean_error_not_traceback(self):
        args = argparse.Namespace(what="cases", language=None, framework=None,
                                   tool_service=None, tags="a and", like=None)
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = cmd_list(args)
        self.assertEqual(rc, 2)
        self.assertIn("a and", buf.getvalue())
        self.assertNotIn("Traceback", buf.getvalue())

    def test_cmd_verify_corpus_reports_clean_error_not_traceback(self):
        args = argparse.Namespace(cases=None, cases_dir=str(self.d), language=None, framework=None,
                                   tool_service=None, tags="a and", like=None, strict=False, debug=False)
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = cmd_verify_corpus(args)
        self.assertEqual(rc, 2)
        self.assertIn("a and", buf.getvalue())
        self.assertNotIn("Traceback", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
