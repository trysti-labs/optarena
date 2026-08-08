"""
`optarena model` / `optarena agent` - opinionated front doors onto `run`.

Both build a list of `Scenario` and hand it to the exact same
`_execute_scenarios` tail `run` itself uses (confirmed by patching that one
function and inspecting what each command handed it) - the point of the
whole design being that `model`/`agent` are "how the scenario list gets
built", not a second execution path. See _run.py's docstrings for the
rest of the reasoning.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from optarena.cli import main
from optarena.cli._run import _parse_agent_token


class ParseAgentTokenTests(unittest.TestCase):
    def test_valid_token(self):
        self.assertEqual(_parse_agent_token("aider@gemma4:12b"), ("aider", "gemma4:12b"))

    def test_model_name_with_its_own_at_free_colon_tag(self):
        # Ollama-style tags (":12b") are common and must not be mistaken
        # for part of the separator.
        self.assertEqual(_parse_agent_token("goose@qwen3-coder:30b"), ("goose", "qwen3-coder:30b"))

    def test_missing_separator_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_agent_token("gemma4:12b")
        self.assertIn("DRIVER@MODEL", str(ctx.exception))

    def test_empty_driver_rejected(self):
        with self.assertRaises(ValueError):
            _parse_agent_token("@gemma4:12b")

    def test_empty_model_rejected(self):
        with self.assertRaises(ValueError):
            _parse_agent_token("aider@")


class ModelCommandTests(unittest.TestCase):
    """`optarena model` never asks for a driver name - it picks the one
    `kind: "baseline"` driver --coding/--tool-call + --kind implies, and
    builds one scenario per positional model (matches --matrix-models with
    the driver held fixed, not crossed - though here there's only one
    driver to hold fixed)."""

    def _scenarios_for(self, argv):
        with patch("optarena.cli._run._execute_scenarios", return_value=0) as mock_exec:
            rc = main(argv)
        self.assertEqual(rc, 0)
        scenarios, _args = mock_exec.call_args.args[:2]
        return scenarios

    def test_coding_ollama_default(self):
        scs = self._scenarios_for(["model", "--coding", "gemma4:12b", "qwen3-coder:30b"])
        self.assertEqual([s.driver for s in scs], ["ollama-chat", "ollama-chat"])
        self.assertEqual([s.backend.model for s in scs], ["gemma4:12b", "qwen3-coder:30b"])

    def test_coding_openai_kind(self):
        scs = self._scenarios_for(["model", "--coding", "--kind", "openai", "gpt-4o-mini", "gpt-4o"])
        self.assertEqual([s.driver for s in scs], ["openai-chat", "openai-chat"])

    def test_tool_call_ollama(self):
        scs = self._scenarios_for(["model", "--tool-call", "gemma4:12b", "qwen3-coder:30b"])
        self.assertEqual([s.driver for s in scs], ["ollama-tools", "ollama-tools"])

    def test_tool_call_openai_kind(self):
        scs = self._scenarios_for(["model", "--tool-call", "--kind", "openai", "gpt-4o-mini", "gpt-4o"])
        self.assertEqual([s.driver for s in scs], ["openai-tools", "openai-tools"])

    def test_single_model_still_builds_one_scenario(self):
        scs = self._scenarios_for(["model", "--coding", "gemma4:12b"])
        self.assertEqual(len(scs), 1)

    def test_n_way_builds_n_scenarios(self):
        scs = self._scenarios_for(["model", "--coding", "a", "b", "c", "d"])
        self.assertEqual(len(scs), 4)

    def test_coding_and_tool_call_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            main(["model", "--coding", "--tool-call", "a", "b"])

    def test_one_of_coding_or_tool_call_is_required(self):
        with self.assertRaises(SystemExit):
            main(["model", "a", "b"])

    def test_scenario_names_include_the_model(self):
        scs = self._scenarios_for(["model", "--coding", "gemma4:12b", "qwen3-coder:30b"])
        self.assertEqual(scs[0].name, "ollama-chat-gemma4:12b")
        self.assertEqual(scs[1].name, "ollama-chat-qwen3-coder:30b")


class AgentCommandTests(unittest.TestCase):
    """`optarena agent` zips explicit driver@model pairs - NOT the
    --matrix-drivers x --matrix-models cross product - so
    `aider@a goose@b` never also implicitly runs `aider@b`/`goose@a`."""

    def _scenarios_for(self, argv):
        with patch("optarena.cli._run._execute_scenarios", return_value=0) as mock_exec:
            rc = main(argv)
        self.assertEqual(rc, 0)
        scenarios, _args = mock_exec.call_args.args[:2]
        return scenarios

    def test_pairs_are_zipped_not_crossed(self):
        scs = self._scenarios_for(
            ["agent", "--coding", "aider@gemma4:12b", "goose@qwen3-coder:30b"])
        self.assertEqual(len(scs), 2)
        self.assertEqual((scs[0].driver, scs[0].backend.model), ("aider", "gemma4:12b"))
        self.assertEqual((scs[1].driver, scs[1].backend.model), ("goose", "qwen3-coder:30b"))

    def test_same_agent_different_model_is_just_a_repeated_token(self):
        scs = self._scenarios_for(
            ["agent", "--coding", "aider@gemma4:12b", "aider@qwen3-coder:30b"])
        self.assertEqual([s.driver for s in scs], ["aider", "aider"])
        self.assertEqual([s.backend.model for s in scs], ["gemma4:12b", "qwen3-coder:30b"])

    def test_sdk_driver_accepted(self):
        scs = self._scenarios_for(["agent", "--coding", "crewai@gemma4:12b"])
        self.assertEqual(scs[0].driver, "crewai")

    def test_unknown_driver_rejected_with_clean_error(self):
        with patch("optarena.cli._run._execute_scenarios") as mock_exec:
            rc = main(["agent", "--coding", "not-a-real-driver@gemma4:12b"])
        self.assertEqual(rc, 2)
        mock_exec.assert_not_called()

    def test_baseline_driver_rejected_pointing_at_model_command(self):
        # A raw baseline driver (ollama-chat/ollama-tools/...) is `model`'s
        # job, not `agent`'s - this is the one enforced boundary between
        # the two commands.
        with patch("optarena.cli._run._execute_scenarios") as mock_exec:
            rc = main(["agent", "--coding", "ollama-chat@gemma4:12b"])
        self.assertEqual(rc, 2)
        mock_exec.assert_not_called()

    def test_malformed_token_rejected(self):
        with patch("optarena.cli._run._execute_scenarios") as mock_exec:
            rc = main(["agent", "--coding", "gemma4:12b"])
        self.assertEqual(rc, 2)
        mock_exec.assert_not_called()

    def test_tool_call_refused_cleanly_no_agent_driver_exists_yet(self):
        # No cli/sdk driver speaks the tool_calls loop today - refusing
        # cleanly (not silently running nothing) is the documented gap.
        with patch("optarena.cli._run._execute_scenarios") as mock_exec:
            rc = main(["agent", "--tool-call", "aider@gemma4:12b", "goose@qwen3-coder:30b"])
        self.assertEqual(rc, 2)
        mock_exec.assert_not_called()

    def test_tool_call_refusal_happens_before_token_validation(self):
        # Malformed tokens under --tool-call should still just report the
        # "no agent driver" refusal, not a confusing parse error about
        # tokens that were never going to be used anyway.
        with patch("optarena.cli._run._execute_scenarios") as mock_exec:
            rc = main(["agent", "--tool-call", "totally not a valid token"])
        self.assertEqual(rc, 2)
        mock_exec.assert_not_called()


class ExecuteScenariosSharedTailTests(unittest.TestCase):
    """`run`/`model`/`agent` must reach the identical tail function - this
    is the actual architectural claim ("thin front door, not a second
    execution path"), checked directly rather than just by behavior."""

    def test_run_model_and_agent_all_call_the_same_function(self):
        import inspect

        from optarena.cli import _run
        src_run = inspect.getsource(_run.cmd_run)
        src_model = inspect.getsource(_run.cmd_model)
        src_agent = inspect.getsource(_run.cmd_agent)
        for src in (src_run, src_model, src_agent):
            self.assertIn("_execute_scenarios(", src)


if __name__ == "__main__":
    unittest.main()
