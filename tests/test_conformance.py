"""
Cross-oracle conformance: the dashboard's JavaScript must agree with the
Python it mirrors.

A-35: `.github/workflows/ci.yml` installed Node 20 for "the cross-oracle
conformance suite (tests/test_conformance.py) ... the H-09 guarantee that the
Python and JS oracles agree byte-for-byte". That file did not exist. Four
algorithms are genuinely duplicated across the language boundary in
`dashboard/index.html`:

    mcnemarExactP        <-> compare.mcnemar_exact_p
    manifestCompatibility<-> compare.manifest_compatibility
    trajOf               <-> metrics.case_trajectory
    attributeFailure     <-> metrics.attribute_failure

and nothing kept them in sync, so the dashboard could disagree with the
terminal about whether an A/B result is significant, whether two runs are even
comparable, or where a multi-step case went wrong - with no test to catch it.

This suite extracts the real `<script>` block from dashboard/index.html (not a
copy - a copy would drift too), runs the functions under `node` against shared
vectors, and asserts the answers match Python's. Skipped, not failed, when
node is unavailable, so a Node-less contributor machine still runs everything
else; CI installs Node so it always executes there.
"""

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from optarena.compare import manifest_compatibility, mcnemar_exact_p
from optarena.metrics import attribute_failure, case_trajectory

_DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"
_NODE = shutil.which("node")


def _dashboard_script() -> str:
    """The dashboard's own script block, with the DOM bootstrap stubbed so it
    can be evaluated headlessly."""
    html = _DASHBOARD.read_text(encoding="utf-8")
    match = re.search(r'<script>\s*"use strict";(.*?)</script>', html, re.S)
    if not match:
        raise AssertionError("could not find the dashboard's script block")
    # Minimal DOM stand-ins: enough for the module's top-level theme IIFE and
    # its `init()` bootstrap to run to completion headlessly (init fails its
    # fetch and takes its own error branch, which writes to `app`). The
    # functions under test are pure and untouched by any of this.
    stub = """
const _el = () => ({
  innerHTML: "", style: {}, dataset: {}, value: "0", offsetWidth: 0, offsetHeight: 0,
  addEventListener() {}, querySelectorAll: () => [], appendChild() {},
});
const document = {
  documentElement: {getAttribute: () => null, setAttribute() {}, removeAttribute() {}},
  getElementById: () => _el(), querySelector: () => _el(),
};
const window = {matchMedia: () => ({matches: false})};
const localStorage = {getItem: () => null, setItem() {}};
const innerWidth = 1024, innerHeight = 768;
"""
    return stub + match.group(1)


@unittest.skipIf(_NODE is None, "node not installed - cross-oracle conformance skipped")
class CrossOracleConformanceTests(unittest.TestCase):
    """Every assertion here is 'the dashboard and the CLI say the same thing'."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="optarena_conformance_"))
        cls.script = _dashboard_script()

    def _eval_js(self, expression_src: str):
        """Run `expression_src` (JS that assigns to `RESULT`) against the
        dashboard's own functions and return the parsed JSON result."""
        path = self.tmp / "probe.js"
        path.write_text(
            f"{self.script}\nlet RESULT;\n{expression_src}\n"
            "process.stdout.write(JSON.stringify(RESULT));\n",
            encoding="utf-8")
        proc = subprocess.run([_NODE, str(path)], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            self.fail(f"node failed: {proc.stderr.strip()}")
        return json.loads(proc.stdout)

    # ── mcnemar_exact_p ───────────────────────────────────────────────────
    def test_mcnemar_matches_python(self):
        vectors = [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (0, 5), (3, 2),
                   (5, 5), (10, 2), (12, 7), (20, 0), (7, 13)]
        js = self._eval_js(
            "RESULT = " + json.dumps([list(v) for v in vectors])
            + ".map(([r, i]) => mcnemarExactP(r, i));")
        for (regressed, improved), js_value in zip(vectors, js):
            with self.subTest(regressed=regressed, improved=improved):
                self.assertAlmostEqual(
                    mcnemar_exact_p(regressed, improved), js_value, places=9,
                    msg="dashboard significance disagrees with `optarena compare`")

    def test_mcnemar_significance_threshold_agrees(self):
        # The user-visible claim on both sides is "significant" vs "noise".
        for regressed, improved in [(0, 1), (0, 5), (1, 5), (2, 8), (5, 5), (0, 6)]:
            with self.subTest(regressed=regressed, improved=improved):
                js = self._eval_js(f"RESULT = mcnemarExactP({regressed}, {improved}) < 0.05;")
                self.assertEqual(mcnemar_exact_p(regressed, improved) < 0.05, js)

    # ── manifest_compatibility ────────────────────────────────────────────
    def test_manifest_compatibility_matches_python(self):
        base = {"oracle_version": 1, "case_set_hash": "abc", "trials": 1,
                "case_count": 7}
        variants = [
            (base, dict(base)),
            (base, dict(base, oracle_version=2)),
            (base, dict(base, case_set_hash="zzz", case_count=9)),
            (base, dict(base, trials=3)),
            (base, dict(base, oracle_version=2, trials=5)),
            (None, dict(base)),
            (base, None),
            (None, None),
        ]
        for man_a, man_b in variants:
            with self.subTest(a=man_a, b=man_b):
                js = self._eval_js(
                    f"RESULT = manifestCompatibility({json.dumps(man_a)}, {json.dumps(man_b)});")
                py = manifest_compatibility(man_a, man_b)
                self.assertEqual(py["comparable"], js["comparable"])
                self.assertEqual(py["verified"], js["verified"])
                self.assertEqual(len(py["reasons"]), len(js["reasons"]))
                for py_reason, js_reason in zip(py["reasons"], js["reasons"]):
                    self.assertEqual(py_reason, js_reason)

    # ── case_trajectory / trajOf ──────────────────────────────────────────
    def test_trajectory_extraction_matches_python(self):
        cases = [
            {"extra": {}},
            {"extra": {"oracle": {"trajectory": {"off_target_count": 3}}}},
            {"extra": {"oracle_all_trials": [
                {"trajectory": {"off_target_count": 1}},
                {"trajectory": {"off_target_count": 2}}]}},
            {"extra": {"oracle_all_trials": [None, {"trajectory": {"off_target_count": 9}}]}},
            {"extra": {"oracle": {}, "oracle_all_trials": []}},
        ]
        for case in cases:
            with self.subTest(case=case):
                js = self._eval_js(f"RESULT = trajOf({json.dumps(case)});")
                self.assertEqual(case_trajectory(case), js)

    # ── attribute_failure / attributeFailure ──────────────────────────────
    def test_failure_attribution_matches_python(self):
        cases = [
            {"passed": True, "extra": {"steps": [{"i": 1, "ok": True}]}},
            {"passed": False, "extra": {"steps": [{"i": 1, "ok": True}]}},
            {"passed": False, "extra": {"steps": [
                {"i": 1, "expected_ok": True}, {"i": 2, "expected_ok": False}]}},
            {"passed": False, "extra": {"steps": [
                {"i": 1, "oracle_ok": True}, {"i": 2, "oracle_ok": False}]}},
            {"passed": False, "extra": {"steps": [
                {"i": 1, "oracle_ok": True, "disrupted": ["config changed"]},
                {"i": 2, "oracle_ok": False}]}},
            {"passed": False, "extra": {"steps": [
                {"i": 1, "ok": False, "expected_ok": False},
                {"i": 2, "ok": True, "expected_ok": False}]}},
            {"passed": False, "extra": {"steps": [
                {"i": 1, "expected_ok": False}, {"i": 2, "expected_ok": False}]}},
            {"passed": False, "extra": {"steps": [
                {"i": 1, "oracle_ok": False}, {"i": 2, "oracle_ok": False}]}},
        ]
        for case in cases:
            with self.subTest(case=case):
                js = self._eval_js(f"RESULT = attributeFailure({json.dumps(case)});")
                self.assertEqual(attribute_failure(case), js,
                                 "dashboard attribution disagrees with the CLI's")


@unittest.skipIf(_NODE is None, "node not installed")
class DashboardEscapingTests(unittest.TestCase):
    """A-08: everything the dashboard interpolates into HTML is escaped, and a
    non-numeric field cannot break rendering."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="optarena_escaping_"))
        cls.script = _dashboard_script()

    def _eval_js(self, expression_src: str):
        path = self.tmp / "probe.js"
        path.write_text(
            f"{self.script}\nlet RESULT;\n{expression_src}\n"
            "process.stdout.write(JSON.stringify(RESULT));\n", encoding="utf-8")
        proc = subprocess.run([_NODE, str(path)], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            self.fail(f"node failed: {proc.stderr.strip()}")
        return json.loads(proc.stdout)

    def test_esc_escapes_every_dangerous_character(self):
        got = self._eval_js("""RESULT = esc(`<img src=x onerror="alert('x')">&`);""")
        for char in ("<", ">", '"', "'"):
            self.assertNotIn(char, got)
        self.assertIn("&lt;", got)
        self.assertIn("&#39;", got)

    def test_num_escapes_non_numeric_values(self):
        self.assertEqual(self._eval_js("RESULT = num(42);"), "42")
        self.assertEqual(self._eval_js("RESULT = num(null, '?');"), "?")
        got = self._eval_js("""RESULT = num("<img src=x onerror=alert(1)>");""")
        self.assertNotIn("<", got)

    def test_formatters_survive_non_numeric_input(self):
        # A run record is JSON on disk; a string where a number belongs must
        # not throw (v.toFixed is not a function) and blank the whole view.
        for expr in ("fmtS('<b>')", "fmtPct('<b>')", "fmtNum('<b>')", "fmtUsd('<b>')"):
            with self.subTest(expr=expr):
                got = self._eval_js(f"RESULT = {expr};")
                self.assertNotIn("<b>", got)

    def test_tooltip_content_is_escaped_at_the_source(self):
        # The tooltip is injected with innerHTML, so a case name carrying
        # markup must already be inert by the time it reaches data-tip.
        case = {"name": "<img src=x onerror=alert(1)>", "passed": False,
                "duration_s": 1.0, "failures": ["boom"], "files": [], "extra": {}}
        runs = {"scenario": {"name": "r"}, "summary": {"pass_rate": 1.0, "cases": 1,
                                                        "passed": 1, "mean_duration_s": 1.0},
                "cases": [case], "manifest": None}
        html = self._eval_js(f"RESULT = compareHTML({json.dumps(runs)}, {json.dumps(runs)});")
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img", html)


if __name__ == "__main__":
    unittest.main()
