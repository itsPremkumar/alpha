"""Hashline Content-Hashed Editing Engine.

Solves "The Harness Problem" (Can Bölük / oh-my-pi / oh-my-openagent):
Models struggle with whitespace reproduction and line numbers.
By tagging each line with a deterministic 2-character content hash (LINE#HASH| content),
edits specify start and end line reference tokens (e.g. 11#VK to 15#MB).
If the line content or position has drifted, the edit fails closed before any corruption.
"""

from __future__ import annotations

import re
import zlib
from typing import NamedTuple

# Base-36 dictionary for short line hashes
HASHLINE_DICT = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
HASHLINE_REF_PATTERN = re.compile(r"^(\d+)#([A-Z0-9]{2})$")


class LineRef(NamedTuple):
    line_num: int
    hash_str: str


class HashlineMismatchError(ValueError):
    """Raised when a specified line reference does not match the actual line hash."""
    pass


def compute_line_hash(line: str) -> str:
    """Compute a deterministic 2-character base36 hash for line content.
    
    Trailing newlines and carriage returns are stripped to be platform-agnostic.
    """
    clean_line = line.rstrip("\r\n")
    # xxHash / crc32 truncated to 2 base36 digits
    val = zlib.crc32(clean_line.encode("utf-8")) & 0xFFFF
    d1 = HASHLINE_DICT[(val // 36) % 36]
    d2 = HASHLINE_DICT[val % 36]
    return f"{d1}{d2}"


def format_hash_line(line_num: int, line_content: str) -> str:
    """Format a single line as 'LINE#HASH| content'."""
    h = compute_line_hash(line_content)
    clean = line_content.rstrip("\r\n")
    return f"{line_num}#{h}| {clean}"


def format_hash_lines(content: str) -> str:
    """Format an entire document tagging each line with its hash reference."""
    if not content:
        return ""
    lines = content.splitlines()
    formatted = [format_hash_line(i + 1, l) for i, l in enumerate(lines)]
    return "\n".join(formatted)


def parse_line_ref(ref: str) -> LineRef:
    """Parse '12#VK' into LineRef(line_num=12, hash_str='VK')."""
    m = HASHLINE_REF_PATTERN.match(ref.strip())
    if not m:
        raise ValueError(f"Invalid hashline reference: '{ref}'. Expected format '<line>#<2-char-hash>' (e.g., '14#A7')")
    return LineRef(line_num=int(m.group(1)), hash_str=m.group(2).upper())


def validate_line_ref(lines: list[str], ref: LineRef) -> None:
    """Check that line_num is in range and its hash matches."""
    if ref.line_num < 1 or ref.line_num > len(lines):
        raise HashlineMismatchError(
            f"Line {ref.line_num} is out of bounds (file has {len(lines)} lines)"
        )
    actual_line = lines[ref.line_num - 1]
    actual_hash = compute_line_hash(actual_line)
    if actual_hash != ref.hash_str:
        raise HashlineMismatchError(
            f"Hashline mismatch at line {ref.line_num}! Expected hash '{ref.hash_str}', "
            f"but found actual hash '{actual_hash}' for content: '{actual_line.strip()}'"
        )


def apply_hashline_edit(
    original_text: str,
    start_ref_str: str,
    end_ref_str: str,
    replacement: str,
) -> str:
    """Apply a replacement spanning from start_ref to end_ref (inclusive).
    
    1. Parses line references (e.g. '10#VK', '12#MB').
    2. Validates hashes against existing content.
    3. Replaces lines start_line..end_line with replacement.
    """
    start_ref = parse_line_ref(start_ref_str)
    end_ref = parse_line_ref(end_ref_str)

    if start_ref.line_num > end_ref.line_num:
        raise ValueError(
            f"start_ref ({start_ref.line_num}) cannot be after end_ref ({end_ref.line_num})"
        )

    lines = original_text.splitlines()
    validate_line_ref(lines, start_ref)
    validate_line_ref(lines, end_ref)

    # Split replacement into lines
    rep_lines = replacement.splitlines() if replacement else []

    # Reconstruct lines
    new_lines = (
        lines[: start_ref.line_num - 1]
        + rep_lines
        + lines[end_ref.line_num :]
    )
    
    trailing_newline = original_text.endswith("\n")
    result = "\n".join(new_lines)
    if trailing_newline and not result.endswith("\n"):
        result += "\n"
    return result
