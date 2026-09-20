"""Hashline Content-Hashed Editing Engine.
Inspired by oh-my-openagent (OmO) and oh-my-pi to solve The Harness Problem.
"""
from alpha.editing.hashline import (
    HashlineMismatchError,
    apply_hashline_edit,
    compute_line_hash,
    format_hash_line,
    format_hash_lines,
    parse_line_ref,
    validate_line_ref,
)

__all__ = [
    "HashlineMismatchError",
    "compute_line_hash",
    "format_hash_line",
    "format_hash_lines",
    "parse_line_ref",
    "validate_line_ref",
    "apply_hashline_edit",
]
