"""Tests for Hashline Content-Hashed Editing Engine."""

import pytest

from alpha.editing.hashline import (
    HashlineMismatchError,
    apply_hashline_edit,
    compute_line_hash,
    format_hash_line,
    format_hash_lines,
    parse_line_ref,
)

SAMPLE_CODE = """def hello():
    message = "Hello, world!"
    print(message)
    return True
"""


def test_compute_line_hash_and_format():
    line = "def hello():"
    h = compute_line_hash(line)
    assert len(h) == 2
    assert h.isalnum()
    
    formatted = format_hash_line(1, line)
    assert formatted == f"1#{h}| def hello():"


def test_format_hash_lines_tagged():
    tagged = format_hash_lines(SAMPLE_CODE)
    lines = tagged.splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("1#")
    assert lines[1].startswith("2#")
    assert "Hello, world!" in lines[1]


def test_parse_line_ref():
    ref = parse_line_ref("42#AB")
    assert ref.line_num == 42
    assert ref.hash_str == "AB"

    with pytest.raises(ValueError):
        parse_line_ref("invalid_token")


def test_apply_hashline_edit_success():
    # Tag lines first
    lines = SAMPLE_CODE.splitlines()
    h2 = compute_line_hash(lines[1])
    h3 = compute_line_hash(lines[2])

    ref_start = f"2#{h2}"
    ref_end = f"3#{h3}"

    replacement = '    message = "Updated!"\n    logger.info(message)'
    updated = apply_hashline_edit(SAMPLE_CODE, ref_start, ref_end, replacement)

    assert 'message = "Updated!"' in updated
    assert 'logger.info(message)' in updated
    assert "def hello():" in updated
    assert "return True" in updated


def test_apply_hashline_edit_mismatch_fails_closed():
    # Attempting to edit with wrong hash fails before applying
    with pytest.raises(HashlineMismatchError):
        apply_hashline_edit(SAMPLE_CODE, "2#ZZ", "3#ZZ", "dummy content")
