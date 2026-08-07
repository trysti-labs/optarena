"""
`optarena._cases._tag_expr`: the pytest `-m`-style boolean expression
language behind `--tags`. Covers atom matching, `and`/`or`/`not`,
precedence, parentheses, case-insensitivity, and every malformed-input
error path - the parser is the whole surface area here, so it gets
dedicated, exhaustive coverage rather than a few smoke tests folded into
a larger file.
"""

from __future__ import annotations

import unittest

from optarena._cases._tag_expr import TagExpressionError, compile_tag_expression, match_tag_expression


class TagExpressionMatchingTests(unittest.TestCase):
    def test_single_tag_present(self):
        self.assertTrue(match_tag_expression("tool-use", ["tool-use", "observability"]))

    def test_single_tag_absent(self):
        self.assertFalse(match_tag_expression("tool-use", ["database"]))

    def test_case_insensitive_on_both_sides(self):
        self.assertTrue(match_tag_expression("TOOL-USE", ["tool-use"]))
        self.assertTrue(match_tag_expression("tool-use", ["TOOL-USE"]))

    def test_and_requires_both(self):
        self.assertTrue(match_tag_expression("a and b", ["a", "b"]))
        self.assertFalse(match_tag_expression("a and b", ["a"]))
        self.assertFalse(match_tag_expression("a and b", []))

    def test_or_requires_either(self):
        self.assertTrue(match_tag_expression("a or b", ["a"]))
        self.assertTrue(match_tag_expression("a or b", ["b"]))
        self.assertFalse(match_tag_expression("a or b", ["c"]))

    def test_not_negates(self):
        self.assertTrue(match_tag_expression("not slow", ["fast"]))
        self.assertFalse(match_tag_expression("not slow", ["slow"]))

    def test_double_not(self):
        self.assertTrue(match_tag_expression("not not a", ["a"]))
        self.assertFalse(match_tag_expression("not not a", []))

    def test_and_binds_tighter_than_or(self):
        # "a or b and c" == "a or (b and c)", not "(a or b) and c"
        self.assertTrue(match_tag_expression("a or b and c", ["a"]))
        self.assertFalse(match_tag_expression("a or b and c", ["b"]))
        self.assertTrue(match_tag_expression("a or b and c", ["b", "c"]))

    def test_not_binds_tighter_than_and(self):
        # "not a and b" == "(not a) and b"
        self.assertFalse(match_tag_expression("not a and b", ["a", "b"]))
        self.assertTrue(match_tag_expression("not a and b", ["b"]))

    def test_parentheses_override_precedence(self):
        self.assertTrue(match_tag_expression("(a or b) and not c", ["a"]))
        self.assertFalse(match_tag_expression("(a or b) and not c", ["a", "c"]))
        self.assertFalse(match_tag_expression("(a or b) and not c", ["d"]))

    def test_nested_parentheses(self):
        self.assertTrue(match_tag_expression("((a and b) or c)", ["c"]))
        self.assertFalse(match_tag_expression("((a and b) or c)", ["a"]))

    def test_tag_names_with_hyphens_underscores_dots(self):
        self.assertTrue(match_tag_expression("tool-use.v2 and cloud_infra", ["tool-use.v2", "cloud_infra"]))

    def test_empty_tag_list_matches_nothing_but_not_expression(self):
        self.assertFalse(match_tag_expression("a", []))
        self.assertTrue(match_tag_expression("not a", []))


class TagExpressionErrorTests(unittest.TestCase):
    def test_empty_expression_refused(self):
        with self.assertRaises(TagExpressionError):
            compile_tag_expression("")

    def test_whitespace_only_expression_refused(self):
        with self.assertRaises(TagExpressionError):
            compile_tag_expression("   ")

    def test_unbalanced_open_paren_refused(self):
        with self.assertRaises(TagExpressionError):
            compile_tag_expression("(a")

    def test_unbalanced_close_paren_refused(self):
        with self.assertRaises(TagExpressionError):
            compile_tag_expression("a)")

    def test_dangling_operator_refused(self):
        with self.assertRaises(TagExpressionError):
            compile_tag_expression("a and")

    def test_leading_operator_refused(self):
        with self.assertRaises(TagExpressionError):
            compile_tag_expression("and a")

    def test_doubled_operator_refused(self):
        with self.assertRaises(TagExpressionError):
            compile_tag_expression("a and and b")

    def test_missing_operator_between_atoms_refused(self):
        with self.assertRaises(TagExpressionError):
            compile_tag_expression("a b")

    def test_error_message_includes_the_offending_expression(self):
        with self.assertRaises(TagExpressionError) as ctx:
            compile_tag_expression("a and")
        self.assertIn("a and", str(ctx.exception))


class CompiledPredicateReuseTests(unittest.TestCase):
    def test_compiled_predicate_evaluates_multiple_tag_sets(self):
        # compile_tag_expression parses once; the returned predicate should
        # be reusable across many cases without re-parsing each time.
        predicate = compile_tag_expression("tool-use and observability")
        self.assertTrue(predicate({"tool-use", "observability"}))
        self.assertFalse(predicate({"tool-use"}))
        self.assertFalse(predicate(set()))


if __name__ == "__main__":
    unittest.main()
