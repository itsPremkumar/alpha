"""Tests for ``alpha.utils.time``."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

from alpha.utils.time import coerce_iso, now_iso

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def test_now_iso_is_utc_iso8601() -> None:
    value = now_iso()
    assert _ISO_RE.match(value), value
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None
    assert parsed.tzinfo.utcoffset(parsed) == UTC.utcoffset(parsed)


def test_coerce_iso_passes_iso_through() -> None:
    iso = "2026-04-27T01:13:30.411334+00:00"
    assert coerce_iso(iso) == iso


def test_coerce_iso_converts_unix_float_string() -> None:
    legacy = "1777252410.411327"
    out = coerce_iso(legacy)
    assert _ISO_RE.match(out), out
    # Round-trip: parsed timestamp matches the original epoch.
    parsed = datetime.fromisoformat(out)
    assert abs(parsed.timestamp() - 1777252410.411327) < 1e-3


def test_coerce_iso_converts_unix_int_string() -> None:
    out = coerce_iso("1700000000")
    assert _ISO_RE.match(out), out


def test_coerce_iso_converts_numeric_types() -> None:
    out_float = coerce_iso(1777252410.411327)
    out_int = coerce_iso(1700000000)
    assert _ISO_RE.match(out_float)
    assert _ISO_RE.match(out_int)


def test_coerce_iso_handles_empty_and_none() -> None:
    assert coerce_iso(None) == ""
    assert coerce_iso("") == ""


def test_coerce_iso_does_not_misinterpret_short_numeric() -> None:
    # A 4-digit year should never be parsed as a unix timestamp; only
    # 10-digit unix-second strings match the legacy pattern.
    assert coerce_iso("2026") == "2026"


def test_coerce_iso_handles_unparseable_string() -> None:
    assert coerce_iso("not-a-timestamp") == "not-a-timestamp"


def test_coerce_iso_rejects_bool() -> None:
    # ``bool`` is a subclass of ``int`` — must not be treated as epoch 0/1.
    assert coerce_iso(True) == "True"
    assert coerce_iso(False) == "False"


def test_coerce_iso_non_finite_floats_become_empty_sentinel() -> None:
    """Non-finite floats are not timestamps, so they must not leak their repr.

    ``float("nan")`` reached ``fromtimestamp``, raised, and the fallback
    emitted the literal string ``"nan"`` -- which downstream
    ``is_lease_expired("nan")`` parses as *expired*, so a corrupt numeric
    column presented itself as a plausible-looking value instead of the
    module's documented "no timestamp" sentinel. Pin the sentinel.
    """
    for value in (float("nan"), float("inf"), float("-inf")):
        assert coerce_iso(value) == "", value
        # The leaked repr must never appear on the wire.
        assert coerce_iso(value) not in {"nan", "inf", "-inf"}


def test_coerce_iso_still_heals_finite_zero_and_epoch() -> None:
    """The non-finite guard must not touch 0.0, which is a real epoch value."""
    assert coerce_iso(0.0) == "1970-01-01T00:00:00+00:00"


def test_coerce_iso_handles_tz_aware_datetime() -> None:
    # str(datetime) would emit a space separator; coerce_iso must use ``T``.
    dt = datetime(2026, 4, 27, 1, 13, 30, 411327, tzinfo=UTC)
    out = coerce_iso(dt)
    assert out == "2026-04-27T01:13:30.411327+00:00"
    assert "T" in out and " " not in out


def test_coerce_iso_handles_tz_naive_datetime_as_utc() -> None:
    dt = datetime(2026, 4, 27, 1, 13, 30, 411327)
    out = coerce_iso(dt)
    assert out == "2026-04-27T01:13:30.411327+00:00"
    parsed = datetime.fromisoformat(out)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_coerce_iso_normalises_non_utc_datetime_to_utc() -> None:
    # +08:00 wall-clock 09:13 == UTC 01:13.
    plus_eight = timezone(timedelta(hours=8))
    dt = datetime(2026, 4, 27, 9, 13, 30, 411327, tzinfo=plus_eight)
    out = coerce_iso(dt)
    assert out == "2026-04-27T01:13:30.411327+00:00"
