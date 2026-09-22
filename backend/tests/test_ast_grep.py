"""Tests for AST-Grep Structural Code Search and Rewriting Tool."""

from alpha.tools.builtins.ast_grep_tool import (
    ast_grep_rewrite,
    ast_grep_search,
)

SAMPLE_CODE = """
def calculate_tax(user, amount):
    return amount * 0.15

def calculate_discount(user, amount):
    return amount * 0.05
"""


def test_ast_grep_search_with_metavariables():
    pattern = "def $FUNC(user, amount):"
    matches = ast_grep_search.invoke({"source_code": SAMPLE_CODE, "pattern": pattern})
    
    assert len(matches) == 2
    assert matches[0]["metavars"]["FUNC"] == "calculate_tax"
    assert matches[1]["metavars"]["FUNC"] == "calculate_discount"


def test_ast_grep_rewrite():
    pattern = "def $FUNC(user, amount):"
    template = "@audit_log\ndef $FUNC(user, amount):"

    rewritten = ast_grep_rewrite.invoke({
        "source_code": SAMPLE_CODE,
        "pattern": pattern,
        "rewrite_template": template,
    })
    assert "@audit_log\ndef calculate_tax(user, amount):" in rewritten
    assert "@audit_log\ndef calculate_discount(user, amount):" in rewritten
