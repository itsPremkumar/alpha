"""Whitespace-equivalence guard for the lead-agent system prompt.

``alpha/agents/lead_agent/prompt.py`` is a *prompt*: the literal text in it is
load-bearing for model behaviour, and the file carries a large volume of it.
When the Ruff E501 backlog in that file was paid down, every over-long literal
line was rewrapped onto continuation lines. A rewrap is precisely the kind of
edit that can quietly drop a clause, duplicate a sentence, or reorder two rules,
and neither the compiler nor the linter notices, because the result is still a
perfectly valid string that the model will read as authoritative instructions.

So the prompt's *word stream* is pinned here. The current source is normalized by
collapsing every whitespace run to a single space, and that token sequence is
compared against a checked-in golden plus a SHA-256 digest pinned independently
inside this test module. Rewrapping cannot move either; a real prompt edit fails
both and therefore has to be an acknowledged, reviewed act.

The normalizer is itself exercised against the real pre-rewrap lines, so this
guard cannot pass by being vacuously true.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import textwrap
import tomllib
from pathlib import Path

import pytest

# backend/tests/test_lead_agent_prompt_whitespace.py -> backend/
BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROMPT_SOURCE = BACKEND_ROOT / "packages" / "harness" / "alpha" / "agents" / "lead_agent" / "prompt.py"
RUFF_CONFIG = BACKEND_ROOT / "ruff.toml"
GOLDEN = BACKEND_ROOT / "tests" / "fixtures" / "lead_agent_prompt_whitespace.golden.txt"
PRE_REWRAP = BACKEND_ROOT / "tests" / "fixtures" / "lead_agent_prompt_prerewrap_lines.txt"

# Pinned independently of the golden file, so regenerating the golden alone is
# not enough to rewrite history: changing the prompt means editing this constant
# too, and both edits then land in the same reviewable diff.
PROMPT_WORD_STREAM_SHA256 = "b33cf537172a8731057b364ce0fea04f15bbff9c8a02d84b5322009d235f7bce"

# The cleanup covered exactly ten E501s in this file.
EXPECTED_PRE_REWRAP_COUNT = 10
# Guards against a degenerate "fix" that empties a fixture to make a test pass.
MINIMUM_GOLDEN_WORDS = 7000


def _preorder(tree: ast.AST) -> list[ast.AST]:
    nodes: list[ast.AST] = []

    def walk(node: ast.AST) -> None:
        nodes.append(node)
        for child in ast.iter_child_nodes(node):
            walk(child)

    walk(tree)
    return nodes


def prompt_word_stream(source: str) -> str:
    """Every string literal in the module, in source order, whitespace-normalized.

    f-strings contribute only their literal parts, so this captures the prompt
    *skeleton* the module authors by hand rather than runtime-interpolated values.
    That skeleton is the text a rewrap can damage.
    """
    nodes = _preorder(ast.parse(source))
    nodes.sort(key=lambda node: (getattr(node, "lineno", 0), getattr(node, "col_offset", 0)))
    parts: list[str] = []
    for node in nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            parts.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            for piece in node.values:
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                    parts.append(piece.value)
    return " ".join("".join(parts).split())


def configured_line_length() -> int:
    return int(tomllib.loads(RUFF_CONFIG.read_text(encoding="utf-8"))["line-length"])


def pre_rewrap_lines() -> list[str]:
    return PRE_REWRAP.read_text(encoding="utf-8").splitlines()


def test_fixtures_and_config_are_present() -> None:
    """Fail loudly, never skip, if an input moves."""
    assert PROMPT_SOURCE.is_file(), f"prompt source moved or vanished: {PROMPT_SOURCE}"
    assert GOLDEN.is_file(), f"prompt golden moved or vanished: {GOLDEN}"
    assert PRE_REWRAP.is_file(), f"pre-rewrap fixture moved or vanished: {PRE_REWRAP}"
    assert RUFF_CONFIG.is_file(), f"ruff config moved or vanished: {RUFF_CONFIG}"


def test_prompt_word_stream_matches_golden() -> None:
    actual = prompt_word_stream(PROMPT_SOURCE.read_text(encoding="utf-8"))
    expected = " ".join(GOLDEN.read_text(encoding="utf-8").split())

    assert len(expected.split()) >= MINIMUM_GOLDEN_WORDS, f"the golden prompt collapsed to {len(expected.split())} words; a golden that small is not a real guard"

    if actual != expected:
        delta = "\n".join(
            difflib.unified_diff(
                textwrap.wrap(expected, width=100),
                textwrap.wrap(actual, width=100),
                fromfile="golden",
                tofile="prompt.py",
                lineterm="",
                n=1,
            )
        )
        pytest.fail(
            "The lead-agent prompt's word stream changed. If this was an intentional prompt edit, regenerate the golden and the pinned digest deliberately; if it was a rewrap, the rewrap altered the text.\n\nWord-level diff:\n" + delta
        )


def test_prompt_word_stream_matches_pinned_digest() -> None:
    actual = prompt_word_stream(PROMPT_SOURCE.read_text(encoding="utf-8"))
    digest = hashlib.sha256(actual.encode("utf-8")).hexdigest()

    assert digest == PROMPT_WORD_STREAM_SHA256, f"prompt word stream digest changed: {digest} != {PROMPT_WORD_STREAM_SHA256}"


def test_golden_is_a_lossless_encoding_of_one_word_stream() -> None:
    """The wrapped golden must reconstruct exactly one normalized stream."""
    wrapped = GOLDEN.read_text(encoding="utf-8")
    reconstructed = " ".join(wrapped.split())

    assert hashlib.sha256(reconstructed.encode("utf-8")).hexdigest() == PROMPT_WORD_STREAM_SHA256
    assert not wrapped.startswith("﻿"), "golden must be UTF-8 without BOM"
    assert "\r" not in wrapped, "golden must use LF line endings"


def test_no_prompt_source_line_exceeds_configured_line_length() -> None:
    """The rewrap that this file exists to police must not regress."""
    limit = configured_line_length()
    source = PROMPT_SOURCE.read_text(encoding="utf-8")
    too_long = [(number, len(line)) for number, line in enumerate(source.split("\n"), start=1) if len(line) > limit]

    assert not too_long, f"lines over the configured line-length {limit}: {too_long}; rewrap them without changing the prompt's words"


def test_pre_rewrap_fixture_really_is_the_disclosed_e501_backlog() -> None:
    """The ten saved lines must still be genuine over-limit lines, all of them."""
    limit = configured_line_length()
    lines = pre_rewrap_lines()

    assert len(lines) == EXPECTED_PRE_REWRAP_COUNT, f"expected 10 saved lines, got {len(lines)}"
    for number, line in enumerate(lines, start=1):
        assert len(line) > limit, f"saved line {number} is only {len(line)} chars, not over {limit}"


def test_every_pre_cleanup_line_survived_verbatim_in_the_prompt() -> None:
    """All ten cleaned lines are still present, word for word, in the prompt."""
    stream = prompt_word_stream(PROMPT_SOURCE.read_text(encoding="utf-8"))
    missing = [line for line in pre_rewrap_lines() if " ".join(line.split()) not in stream]

    assert not missing, f"{len(missing)} cleaned prompt lines are no longer present: {missing}"


def rewrap_at_word_boundaries(line: str, width: int = 100, indent: int = 2) -> str:
    """Simulate the kind of reflow the E501 cleanup performs.

    Hyphen and long-word breaking are disabled: a rewrap is allowed to move
    whitespace, never to insert a space inside a hyphenated token.
    """
    return "\n".join(
        textwrap.wrap(
            " ".join(line.split()),
            width=width,
            subsequent_indent=" " * indent,
            break_long_words=False,
            break_on_hyphens=False,
        )
    )


@pytest.mark.parametrize("index", range(EXPECTED_PRE_REWRAP_COUNT))
def test_normalizer_accepts_a_rewrap_of_a_real_pre_cleanup_line(index: int) -> None:
    """A rewrap is invisible to the normalizer; prove it on the real input."""
    original = pre_rewrap_lines()[index]
    rewrapped = rewrap_at_word_boundaries(original)

    assert "\n" in rewrapped, "the rewrap did not actually introduce a line break"
    assert " ".join(rewrapped.split()) == " ".join(original.split())


@pytest.mark.parametrize("index", range(EXPECTED_PRE_REWRAP_COUNT))
def test_normalizer_rejects_a_dropped_clause_in_a_real_pre_cleanup_line(index: int) -> None:
    """The guard must have teeth: a silently dropped clause is detected."""
    original = pre_rewrap_lines()[index]
    words = " ".join(original.split()).split()

    assert len(words) > 20, "fixture should be long enough to drop a clause from"
    mutilated = " ".join(words[:-6])

    assert " ".join(mutilated.split()) != " ".join(original.split())


@pytest.mark.parametrize("index", range(EXPECTED_PRE_REWRAP_COUNT))
def test_normalizer_rejects_a_duplicated_clause_in_a_real_pre_cleanup_line(index: int) -> None:
    """Duplication changes what the model reads, so it must also be caught."""
    original = pre_rewrap_lines()[index]
    words = " ".join(original.split()).split()

    assert len(words) > 20, "fixture should be long enough to duplicate a clause from"
    duplicated = " ".join(words + words[-6:])

    assert " ".join(duplicated.split()) != " ".join(original.split())


@pytest.mark.parametrize("index", range(EXPECTED_PRE_REWRAP_COUNT))
def test_normalizer_rejects_a_reordered_clause_in_a_real_pre_cleanup_line(index: int) -> None:
    """Reordering two rules is also a semantic change to a prompt."""
    original = pre_rewrap_lines()[index]
    words = " ".join(original.split()).split()

    assert len(words) > 20, "fixture should be long enough to reorder within"
    head, tail = words[:10], words[10:20]
    reordered = " ".join(tail + head + words[20:])

    assert " ".join(reordered.split()) != " ".join(original.split())


def test_nested_boulder_bullet_is_continued_at_its_nested_indent() -> None:
    """The one nested bullet continues at indent 4, so it stays a nested item.

    The other nine bullets sit at column 0 and continue at indent 2. Wrapping the
    nested bullet at indent 2 instead would have promoted it to a sibling, which
    is a structural change to the prompt's list nesting.
    """
    source = PROMPT_SOURCE.read_text(encoding="utf-8").split("\n")
    bullet = [line for line in source if line.lstrip().startswith("- For long-running missions")]
    continuation = [line for line in source if line.lstrip().startswith("to package verified outputs")]

    assert len(bullet) == 1, f"expected one boulder bullet line, got {len(bullet)}"
    assert len(continuation) == 1, f"expected one boulder continuation line, got {len(continuation)}"
    assert bullet[0].startswith("  - For long-running missions"), f"boulder bullet should be indented 2, got {bullet[0]!r}"
    assert continuation[0].startswith("    to package verified outputs"), f"boulder continuation should be indented 4, got {continuation[0]!r}"
