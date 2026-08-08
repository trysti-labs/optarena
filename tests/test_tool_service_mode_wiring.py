"""
`--tool-service-mode` wiring: the CLI flag -> `Scenario.tool_service_mode`
path (`optarena/cli/_run.py::_scenario_from_args`), and that the field
round-trips through `Scenario.to_dict()`. Schema-level acceptance/rejection
of the field's values is covered in `test_optarena.py`'s
`SchemaValidationTests`; this file is scoped to the CLI/dataclass wiring.
"""

from __future__ import annotations

import argparse
import unittest

from optarena.cli._run import _scenario_from_args
from optarena.scenario import Scenario


def _base_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        driver="ollama-tools", model="llama3.2", kind="ollama",
        base_url="http://localhost:11434", api_key="optarena", num_ctx=None,
        name=None, cases=None, cases_dir=None, timeout=None,
        language=None, framework=None, tool_service=None, tags=None, like=None,
        tool_service_mode=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class ScenarioFromArgsToolServiceModeTests(unittest.TestCase):
    def test_defaults_to_none_when_flag_omitted(self):
        sc = _scenario_from_args(_base_args())
        self.assertIsNone(sc.tool_service_mode)

    def test_sandboxed_flag_reaches_the_scenario(self):
        sc = _scenario_from_args(_base_args(tool_service_mode="sandboxed"))
        self.assertEqual(sc.tool_service_mode, "sandboxed")

    def test_mock_flag_reaches_the_scenario(self):
        sc = _scenario_from_args(_base_args(tool_service_mode="mock"))
        self.assertEqual(sc.tool_service_mode, "mock")


class ScenarioToolServiceModeRoundTripTests(unittest.TestCase):
    def test_to_dict_includes_the_field(self):
        sc = Scenario(name="x", driver="ollama-tools", tool_service_mode="sandboxed")
        self.assertEqual(sc.to_dict()["tool_service_mode"], "sandboxed")

    def test_from_dict_reads_the_field(self):
        sc = Scenario.from_dict({"name": "x", "driver": "ollama-tools", "tool_service_mode": "sandboxed"})
        self.assertEqual(sc.tool_service_mode, "sandboxed")

    def test_from_dict_defaults_to_none_when_absent(self):
        sc = Scenario.from_dict({"name": "x", "driver": "ollama-tools"})
        self.assertIsNone(sc.tool_service_mode)


if __name__ == "__main__":
    unittest.main()
