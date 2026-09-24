"""Tests for Pre-Commit AST Syntax & Linter Guardrail."""

from __future__ import annotations

import json
import sys

import pytest
from pathlib import Path

from alpha.safety.ast_syntax_guard import (
    validate_syntax_precommit,
    _validate_python_syntax,
    _validate_json_syntax,
    _validate_yaml_syntax,
)


def test_valid_python_syntax():
    code = (
        "def add(a: int, b: int) -> int:\n"
        "    return a + b\n\n"
        "class Calculator:\n"
        "    def multiply(self, x: float, y: float) -> float:\n"
        "        return x * y\n"
    )
    valid, err = validate_syntax_precommit("calc.py", code)
    assert valid is True
    assert err is None


def test_invalid_python_indentation_rejected():
    code = (
        "def broken():\n"
        "return 42\n"
    )
    valid, err = validate_syntax_precommit("broken.py", code)
    assert valid is False
    assert err is not None
    assert "IndentationError" in err
    assert "The file was NOT modified" in err


def test_invalid_python_syntax_rejected():
    code = (
        "def broken(\n"
        "    return 42\n"
    )
    valid, err = validate_syntax_precommit("syntax_err.py", code)
    assert valid is False
    assert err is not None
    assert "SyntaxError" in err
    assert "The file was NOT modified" in err


def test_valid_json_syntax():
    valid_json = '{"name": "alpha", "version": "1.0", "active": true, "items": [1, 2, 3]}'
    valid, err = validate_syntax_precommit("config.json", valid_json)
    assert valid is True
    assert err is None


def test_invalid_json_rejected():
    invalid_json = '{"name": "alpha", trailing_comma: true,}'
    valid, err = validate_syntax_precommit("config.json", invalid_json)
    assert valid is False
    assert err is not None
    assert "JSONDecodeError" in err


def test_bypass_syntax_check():
    broken_code = "def broken(:\n return\n"
    valid, err = validate_syntax_precommit("fixture.py", broken_code, bypass=True)
    assert valid is True
    assert err is None


def test_unsupported_extensions_pass_through():
    markdown = "# Documentation\n\nSome unparsed text here.\n"
    valid, err = validate_syntax_precommit("README.md", markdown)
    assert valid is True
    assert err is None


def test_empty_json_is_rejected_not_skipped():
    """Empty content is invalid JSON — it must be parsed and fail, not skip."""
    valid, err = validate_syntax_precommit("config.json", "")
    assert valid is False
    assert err is not None
    assert "JSONDecodeError" in err


def test_empty_yaml_is_parsed_not_skipped():
    """Empty YAML is validated by an actual parse (safe_load -> None), not waved through."""
    valid, err = validate_syntax_precommit("config.yaml", "")
    assert valid is True
    assert err is None


def test_invalid_yaml_rejected():
    valid, err = validate_syntax_precommit("config.yaml", "key: [unclosed")
    assert valid is False
    assert err is not None
    assert "YAML parse error" in err


def test_yaml_fails_closed_when_pyyaml_missing(monkeypatch):
    """Without PyYAML, YAML writes must fail closed with a disclosed reason."""
    monkeypatch.setitem(sys.modules, "yaml", None)
    valid, err = validate_syntax_precommit("config.yaml", "key: value")
    assert valid is False
    assert err is not None
    assert "YAML validation unavailable" in err
    assert "PyYAML not installed" in err
