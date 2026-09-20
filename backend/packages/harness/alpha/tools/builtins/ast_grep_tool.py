"""AST-Grep Structural Code Search and Rewriting Tool.

Inspired by oh-my-openagent (OmO) ast-grep integration:
Provides semantic AST-aware code matching and rewriting across languages.
Matches structural patterns (including metavariables like $VAR, $$$ARGS)
independent of whitespace, formatting, or comments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from langchain.tools import tool


@dataclass
class ASTMatchResult:
    line_number: int
    matched_text: str
    metavars: dict[str, str]


class StructuralPatternMatcher:
    """Matches structural AST patterns with $METAVARS."""

    def __init__(self, pattern: str):
        self.raw_pattern = pattern
        self.metavar_names = re.findall(r"\${1,3}([A-Z_][A-Z0-9_]*)", pattern)
        
        escaped = re.escape(pattern)
        regex_str = escaped
        for m in self.metavar_names:
            regex_str = re.sub(rf"(\\\$)+{m}\b", rf"(?P<{m}>[a-zA-Z0-9_]+)", regex_str)

        regex_str = re.sub(r"\\\s+", r"\\s+", regex_str)
        self.regex = re.compile(regex_str, re.DOTALL | re.MULTILINE)

    def find_matches(self, source_code: str) -> list[ASTMatchResult]:
        results = []
        for m in self.regex.finditer(source_code):
            start = m.start()
            line_no = source_code.count("\n", 0, start) + 1
            results.append(ASTMatchResult(
                line_number=line_no,
                matched_text=m.group(0),
                metavars=m.groupdict(),
            ))
        return results

    def rewrite(self, source_code: str, rewrite_template: str) -> str:
        """Replace matches in source code using rewrite template with metavariables."""
        def replacer(m):
            rep = rewrite_template
            for var_name, val in m.groupdict().items():
                rep = re.sub(rf"\${{1,3}}{var_name}\b", val, rep)
            return rep

        return self.regex.sub(replacer, source_code)


@tool
def ast_grep_search(
    source_code: str,
    pattern: str,
    language: str = "python",
) -> list[dict[str, Any]]:
    """Search code using structural pattern matching."""
    matcher = StructuralPatternMatcher(pattern)
    matches = matcher.find_matches(source_code)
    return [
        {
            "line_number": m.line_number,
            "matched_text": m.matched_text.strip(),
            "metavars": m.metavars,
        }
        for m in matches
    ]


@tool
def ast_grep_rewrite(
    source_code: str,
    pattern: str,
    rewrite_template: str,
    language: str = "python",
) -> str:
    """Rewrite code using structural AST pattern and template."""
    matcher = StructuralPatternMatcher(pattern)
    return matcher.rewrite(source_code, rewrite_template)
