"""
optarena/_cases/_tag_expr.py
────────────────────────────
A small pytest `-m`-style boolean expression language for `--tags`, e.g.
`"tool-use and observability"`, `"cloudformation or cdk"`, `"not slow"`,
`"(a or b) and not c"`. Standard precedence: `not` > `and` > `or`, with
parentheses for grouping. Tag names may contain hyphens/underscores/dots
(anything but whitespace and parens) and are matched case-insensitively.

`compile_tag_expression` parses the expression once into a small closure
tree and returns a predicate over a case's tag set - so filtering N cases
against one `--tags` expression parses it once, not N times.
"""

from __future__ import annotations

from typing import Callable

_KEYWORDS = ("and", "or", "not")


class TagExpressionError(ValueError):
    """A malformed `--tags` boolean expression."""


def _tokenize(expr: str) -> list[str]:
    tokens: list[str] = []
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c.isspace():
            i += 1
            continue
        if c in "()":
            tokens.append(c)
            i += 1
            continue
        j = i
        while j < n and expr[j] not in "() \t\n\r":
            j += 1
        tokens.append(expr[i:j])
        i = j
    return tokens


def compile_tag_expression(expr: str) -> Callable[[set[str]], bool]:
    """Parse *expr* into a predicate `(tag_set) -> bool`. Raises
    `TagExpressionError` on anything malformed (empty, unbalanced
    parens, a trailing/missing operand, a keyword used as a tag name).
    The returned predicate expects an already-lowercased tag set."""
    tokens = _tokenize(expr)
    if not tokens:
        raise TagExpressionError("empty tag expression")

    pos = 0

    def parse_or() -> Callable[[set[str]], bool]:
        nonlocal pos
        left = parse_and()
        while pos < len(tokens) and tokens[pos].lower() == "or":
            pos += 1
            right = parse_and()
            prev = left
            left = lambda tags, l=prev, r=right: l(tags) or r(tags)  # noqa: E731
        return left

    def parse_and() -> Callable[[set[str]], bool]:
        nonlocal pos
        left = parse_not()
        while pos < len(tokens) and tokens[pos].lower() == "and":
            pos += 1
            right = parse_not()
            prev = left
            left = lambda tags, l=prev, r=right: l(tags) and r(tags)  # noqa: E731
        return left

    def parse_not() -> Callable[[set[str]], bool]:
        nonlocal pos
        if pos < len(tokens) and tokens[pos].lower() == "not":
            pos += 1
            operand = parse_not()
            return lambda tags, o=operand: not o(tags)  # noqa: E731
        return parse_atom()

    def parse_atom() -> Callable[[set[str]], bool]:
        nonlocal pos
        if pos >= len(tokens):
            raise TagExpressionError(f"unexpected end of expression in {expr!r}")
        tok = tokens[pos]
        if tok == "(":
            pos += 1
            inner = parse_or()
            if pos >= len(tokens) or tokens[pos] != ")":
                raise TagExpressionError(f"missing closing ')' in {expr!r}")
            pos += 1
            return inner
        if tok == ")":
            raise TagExpressionError(f"unexpected ')' in {expr!r}")
        if tok.lower() in _KEYWORDS:
            raise TagExpressionError(f"unexpected keyword {tok!r} used as a tag name in {expr!r}")
        pos += 1
        name = tok.lower()
        return lambda tags, name=name: name in tags  # noqa: E731

    predicate = parse_or()
    if pos != len(tokens):
        raise TagExpressionError(f"unexpected trailing token {tokens[pos]!r} in {expr!r}")
    return predicate


def match_tag_expression(expr: str, tags: list[str]) -> bool:
    """Convenience one-shot form: compile *expr* and evaluate it against
    *tags* immediately. Prefer `compile_tag_expression` when filtering many
    cases against the same expression - this recompiles every call."""
    return compile_tag_expression(expr)({t.lower() for t in tags})
