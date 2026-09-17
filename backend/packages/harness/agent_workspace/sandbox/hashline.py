"""Hash-anchored line addressing for concurrent-safe file edits (OMO hashline).

A ``read_file(hashline=True)`` response tags every line ``42#a3f9| <content>``
and carries a file revision token. A later ``str_replace``/``write_file`` may
present that token as ``anchor_hash``, proving it observed that exact version.
The read-before-write gate accepts a valid anchor even when the implicit read
mark is gone (e.g. compacted away), so anchors survive summarization while
marks deliberately do not.

Contracts (single source of truth — the read tool formats, the gate verifies):

* Revision token: first ``REVISION_TOKEN_CHARS`` hex chars of the SHA-256 of
  the FULL file bytes. Always computed over the whole file, even for ranged
  reads (a slice-scoped token would let same-slice edits mask concurrent
  changes elsewhere in the file).
* Line tag: ``{absolute_lineno}#{sha256(f"{lineno}:{line}")[:4]}``. Tags are
  stable references for the model; only the revision token authorizes writes.
* Numbering is absolute (honors ``start_line``); empty lines are tagged too.
* Output caps at ``MAX_HASHLINE_LINES`` tagged lines with an explicit notice;
  the revision token still covers the whole file.

Threat model note: tokens are staleness proofs against races, not secrets and
not an authorization boundary. A correct 16-hex-char token is unguessable in
practice, which is what makes a presented anchor evidence of observation.
"""

import hashlib

#: Hex chars of the file digest exposed as the revision token / anchor.
REVISION_TOKEN_CHARS = 16

#: Hex chars of the per-line tag hash.
LINE_TAG_CHARS = 4

#: Tagged lines per response before an explicit truncation notice.
MAX_HASHLINE_LINES = 2000


def revision_token(content: str) -> str:
    """File revision token: SHA-256 hexdigest prefix over full UTF-8 content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:REVISION_TOKEN_CHARS]


def line_tag(lineno: int, line: str) -> str:
    """Stable per-line tag ``{lineno}#{hash}` for one line of content."""
    digest = hashlib.sha256(f"{lineno}:{line}".encode()).hexdigest()
    return f"{lineno}#{digest[:LINE_TAG_CHARS]}"


def format_hashline(content: str, *, start_line: int = 1) -> str:
    """Format file content with absolute line tags plus a revision header.

    Args:
        content: The file slice to display.
        start_line: Absolute 1-indexed number of its first line.

    Returns:
        A one-line revision header followed by ``TAG| <line>`` rows. Callers
        pass the FULL file content for the token even when displaying a
        ranged slice — see module docstring.
    """
    token = revision_token(content)
    lines = content.splitlines()
    total = len(lines)
    shown = lines[:MAX_HASHLINE_LINES]
    rows = [f"{line_tag(start_line + index, line)}| {line}" for index, line in enumerate(shown)]
    header = f'[hashline revision {token} | {total} lines — pass anchor_hash="{token}" to str_replace/write_file to prove this version]'
    if total > MAX_HASHLINE_LINES:
        rows.append(f"(+{total - MAX_HASHLINE_LINES} more lines untagged)")
    return "\n".join([header, *rows])


def format_hashline_ranged(full_content: str, displayed: str, *, start_line: int) -> str:
    """Format a ranged slice with absolute tags but a whole-file token.

    Args:
        full_content: Entire file content (token source).
        displayed: The slice to tag (already ranged).
        start_line: Absolute 1-indexed number of the slice's first line.
    """
    token = revision_token(full_content)
    total = len(full_content.splitlines())
    lines = displayed.splitlines()[:MAX_HASHLINE_LINES]
    rows = [f"{line_tag(start_line + index, line)}| {line}" for index, line in enumerate(lines)]
    header = f'[hashline revision {token} | {total} lines, showing {len(lines)} from line {start_line} — pass anchor_hash="{token}" to str_replace/write_file to prove this version]'
    if len(displayed.splitlines()) > MAX_HASHLINE_LINES:
        rows.append("(+more lines untagged)")
    return "\n".join([header, *rows])
