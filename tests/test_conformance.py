"""
Cross-oracle conformance (H-09): the Python oracle (optarena.cases) and the
JS oracle (ui-harness/src/oracle.js) must agree byte-for-byte on the same
inputs, so a case grades identically through a CLI driver and the VS Code UI
harness. This suite builds real workspaces, runs the Python primitives, then
runs the JS primitives over the SAME directories via a small Node runner, and
asserts equality.

Skipped automatically when Node isn't on PATH (the JS oracle can't run) - the
main suite stays green on a Python-only host; CI runs it where Node exists.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from optarena.cases import snapshot, changed_files, check_expected, write_setup_files

HARNESS = Path(__file__).resolve().parents[1] / "ui-harness"
RUNNER = HARNESS / "test" / "conformance-runner.mjs"
NODE = shutil.which("node")


def _run_js(jobs: list[dict]) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"jobs": jobs}, f)
        job_path = f.name
    try:
        proc = subprocess.run(
            [NODE, str(RUNNER), job_path],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            raise AssertionError(f"JS runner failed: {proc.stderr}")
        return json.loads(proc.stdout)["results"]
    finally:
        os.unlink(job_path)


@unittest.skipUnless(NODE and RUNNER.exists(), "node / conformance runner unavailable")
class OracleConformanceTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="optarena_conf_"))
        self.addCleanup(lambda: shutil.rmtree(self.ws, ignore_errors=True))

    def _py_snapshot(self, root: Path) -> dict:
        return dict(sorted(snapshot(root).items()))

    def test_snapshot_matches_including_unicode_and_nested(self):
        (self.ws / "a.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (self.ws / "pkg").mkdir()
        (self.ws / "pkg" / "café.txt").write_text("naïve résumé — ünïcödé\n", encoding="utf-8")
        (self.ws / ".github" / "workflows").mkdir(parents=True)
        (self.ws / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
        # IGNORE_DIRS + .aider* must be excluded identically by both.
        (self.ws / ".git").mkdir()
        (self.ws / ".git" / "HEAD").write_text("ref: x\n", encoding="utf-8")
        (self.ws / ".aider.chat.history.md").write_text("noise\n", encoding="utf-8")

        py = self._py_snapshot(self.ws)
        js = _run_js([{"id": "s", "op": "snapshot", "dir": str(self.ws)}])["s"]
        self.assertEqual(py, js)

    def test_changed_files_same_size_rewrite_detected_by_both(self):
        target = self.ws / "sub.py"
        target.write_text("def f():\n    return a-b\n", encoding="utf-8")
        before_py = snapshot(self.ws)
        # Same byte length, different content - the classic size:mtime miss.
        target.write_text("def f():\n    return a+b\n", encoding="utf-8")

        py_changed = sorted(changed_files(before_py, self.ws))
        js_changed = _run_js([{
            "id": "c", "op": "changed_files", "dir": str(self.ws),
            "before": dict(before_py),
        }])["c"]
        self.assertEqual(py_changed, ["sub.py"])
        self.assertEqual(py_changed, js_changed)

    def test_check_expected_matches_on_globs_regex_min_lines(self):
        (self.ws / "HealthController.java").write_text(
            "class HealthController {\n  ok() { return 200; }\n}\n", encoding="utf-8")
        specs = [
            {"path_pattern": "*Controller.java",
             "content_patterns": ["class healthcontroller"],
             "not_content_patterns": ["todo"],
             "regex_patterns": [r"return\s+200"],
             "min_lines": 2},
            {"path_pattern": "missing.txt"},
        ]
        created = ["HealthController.java"]
        py = check_expected(created, specs, self.ws)
        js = _run_js([{
            "id": "e", "op": "check_expected", "dir": str(self.ws),
            "created": created, "spec": specs,
        }])["e"]
        self.assertEqual(py, js)
        self.assertTrue(any("missing.txt" in f for f in py))

    def test_write_setup_containment_agrees(self):
        # Both oracles must reject traversal + absolute paths, accept nested.
        cases = [
            ({"nested/ok.py": "x=1\n"}, False),
            ({"../escape.txt": "pwned\n"}, True),
            ({"/etc/escape.txt": "pwned\n"}, True),
        ]
        for i, (files, should_error) in enumerate(cases):
            d = self.ws / f"c{i}"
            d.mkdir()
            py_error = None
            try:
                write_setup_files(d, files)
            except ValueError as e:
                py_error = str(e)
            js = _run_js([{"id": "w", "op": "write_setup", "dir": str(d), "files": files}])["w"]
            self.assertEqual(bool(py_error), should_error, f"python case {i}")
            self.assertEqual(bool(js["error"]), should_error, f"js case {i}")


if __name__ == "__main__":
    unittest.main()
