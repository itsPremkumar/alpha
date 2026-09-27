"""The metadata filter-key gate must not accept a key it forbids.

`alpha.persistence.json_compat.validate_metadata_filter_key` is the *only* gate
standing between a client-supplied metadata filter key and raw string
interpolation into compiled SQL. Its own docstring says so:

    "The charset is restricted because the key is interpolated into the
    compiled SQL path expression (``$."<key>"`` / ``->`` literal), so any
    laxer pattern would open a SQL/JSONPath injection surface."

The original used ``_KEY_CHARSET_RE.match(...)`` against ``^[A-Za-z0-9_\\-]+$``.
``re.match`` anchors only the *start*, and Python's ``$`` also matches
immediately before a trailing newline, so ``"abc\\n"`` passed the gate. MEASURED
at HEAD, with the fix reverted:

    validate_metadata_filter_key("abc\\n") -> True
    validate_metadata_filter_key("a\\n")     -> True

and the accepted key then lands **interpolated** in the compiled SQL, which is
the exact property the gate exists to prevent:

    -- sqlite, key='abc\\n'
    WHERE (json_type(t.metadata_json, '$."abc
    "') = 'text' AND json_extract(t.metadata_json, '$."abc
    "') = ?)

    -- postgresql, key='abc\\n'
    WHERE (json_typeof(t.metadata_json -> 'abc
    ') = 'string' AND (t.metadata_json ->> 'abc
    ') = %(param_1)s)

A SQL string literal may legitimately span a newline, so the SQL grammar does
not neutralise this either: the key arrives as a *different* JSON path and a
different ``->`` operand than the caller asked for.

This is the same defect the shipped ``B5`` fix corrected in
``alpha/config/paths.py`` and in ``alpha/utils/thread_id.py``: a ``$``-anchored
pattern consumed with ``.match``. Those two already use ``.fullmatch``;
``json_compat`` was the third copy of the same mistake.

Reach: ``app/gateway/routers/threads.py::ThreadSearchRequest._validate_metadata_filters``
calls this function directly on the request body's ``metadata`` dict, so the
key is client-supplied. Downstream, ``ThreadMetaRepository.search`` catches the
``ValueError`` from ``json_match``, logs and skips the key, and raises
``InvalidMetadataFilterError`` when it was the only key -- so the fail-closed
path is the one that must engage.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Column, Integer, MetaData, Table
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from alpha.persistence.json_compat import JsonMatch, json_match, validate_metadata_filter_key

# Keys the pattern documents as safe: the whole documented charset.
LEGAL_KEYS = ("a", "A", "Z", "0", "9", "_", "-", "agent_workspace_pinned", "a-b_C9", "-", "_", "x" * 200)

# Keys the pattern documents as unsafe. The ``"...\n"`` entries are the ones
# Python's ``$`` lets through; the rest are control cases proving the detector
# is not vacuous and that the charset is still enforced.
UNSAFE_KEYS = [
    "abc\n",
    "a\n",
    "agent_workspace_pinned\n",
    "-x-\n",
    "a_b-C9\n",
    "abc\r",
    "abc\t",
    "abc ",
    "ab\nc",
    "ab c",
    "a.b",
    "a$b",
    "a'b",
    'a"b',
    "a;b",
    "a\\b",
    "a/b",
    "a\0b",
    "..\n",
    ".",
    "..",
    "",
]


@pytest.mark.parametrize("key", UNSAFE_KEYS)
def test_the_gate_rejects_every_key_the_documented_charset_forbids(key: str) -> None:
    assert not validate_metadata_filter_key(key), f"{key!r} must not be a safe metadata filter key"


@pytest.mark.parametrize("key", LEGAL_KEYS)
def test_the_gate_still_accepts_the_documented_charset(key: str) -> None:
    assert validate_metadata_filter_key(key), f"{key!r} is in the documented charset and must stay accepted"


def test_the_detector_is_not_vacuous_on_a_genuinely_safe_and_unsafe_pair() -> None:
    """Positive/negative control in one place, so narrowing the gate is caught too."""
    assert validate_metadata_filter_key("agent_workspace_pinned")
    assert not validate_metadata_filter_key("agent workspace pinned")


@pytest.mark.parametrize("key", ["abc\n", "a\n", "-x-\n"])
def test_json_match_refuses_a_key_the_gate_refuses(key: str) -> None:
    """``JsonMatch`` re-checks the key, so a hole here is not a compile-time crash."""
    md = MetaData()
    tbl = Table("threads_meta", md, Column("thread_id", Integer, primary_key=True), Column("metadata_json", JSON))
    with pytest.raises(ValueError):
        json_match(tbl.c.metadata_json, key, "x")


# How each dialect wraps the interpolated key literal: ``$."KEY"`` on SQLite,
# ``'KEY'`` on PostgreSQL. Normalising exactly this token (and not the bare key)
# keeps coincidental substrings elsewhere in the statement -- ``threads_meta``,
# ``'string'`` -- out of the comparison.
SQLITE_LITERAL = ('$."', '"')
PG_LITERAL = ("'", "'")


@pytest.mark.parametrize("dialect_name,dialect,column_type,literal", [("sqlite", sqlite.dialect(), JSON, SQLITE_LITERAL), ("postgresql", postgresql.dialect(), JSONB, PG_LITERAL)])
def test_no_accepted_key_can_smuggle_anything_but_itself_into_compiled_sql(dialect_name: str, dialect, column_type, literal: tuple[str, str]) -> None:
    """The gate's stated purpose: an accepted key contributes only its own bytes.

    A raw ``"\\n" not in sql`` check would be vacuous -- SQLAlchemy emits its own
    newlines as separators, and both compilers legitimately emit ``'`` and ``"``
    as the delimiters *around* the literal. So this pins the exact property
    instead: blank out the key literal and the statement must be byte-identical
    to the one a known-safe reference key produces. A key that carried structure
    (a newline, a quote, a ``;``) would change the shape and fail here.
    """
    md = MetaData()
    tbl = Table("threads_meta", md, Column("thread_id", Integer, primary_key=True), Column("metadata_json", column_type))
    opening, closing = literal

    def raw_sql(key: str) -> str:
        return str(json_match(tbl.c.metadata_json, key, "x").compile(dialect=dialect))

    def sql_with_key_blanked(key: str) -> str:
        token = f"{opening}{key}{closing}"
        sql = raw_sql(key)
        assert token in sql, f"key {key!r} is not interpolated as {token!r}; the dialect's literal shape changed: {sql}"
        return sql.replace(token, f"{opening}\x00{closing}")

    template = sql_with_key_blanked("Qq")

    # Control: prove the comparison below is not vacuous, and that the
    # compilers do *not* neutralise a newline for us. The token is spliced in
    # directly (rather than built through ``JsonMatch``) so this control holds
    # whether or not the constructor refuses such a key -- it documents the
    # reason the gate has to refuse it.
    spliced = raw_sql("Qq").replace(f"{opening}Qq{closing}", f"{opening}Qq\n{closing}")
    assert spliced != template, "a trailing newline did not change the compiled statement; this test can no longer detect a leak"

    for key in LEGAL_KEYS:
        element = json_match(tbl.c.metadata_json, key, "x")
        assert isinstance(element, JsonMatch)
        got = sql_with_key_blanked(key)
        assert got == template, f"key {key!r} changed the structure of the compiled {dialect_name} SQL beyond its own bytes:\n  got      {got}\n  template {template}"


def test_the_gate_and_the_compiler_agree_on_the_exact_key_set() -> None:
    """One source of truth: the constructor gate and the standalone gate cannot disagree.

    The two ``_compile_*`` functions carry a redundant
    ``if not validate_metadata_filter_key(...)`` re-check precisely because they
    distrust this. This pins that the redundant re-check and the public gate
    accept exactly the same keys, so a future loosening of one is caught.
    """
    md = MetaData()
    tbl = Table("threads_meta", md, Column("thread_id", Integer, primary_key=True), Column("metadata_json", JSON))
    for key in [*LEGAL_KEYS, *UNSAFE_KEYS]:
        gate = validate_metadata_filter_key(key)
        constructed = True
        try:
            json_match(tbl.c.metadata_json, key, "x")
        except ValueError:
            constructed = False
        assert gate is constructed, f"gate says {gate} for {key!r} but JsonMatch says {constructed}"
