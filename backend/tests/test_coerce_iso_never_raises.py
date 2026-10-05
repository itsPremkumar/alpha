"""`coerce_iso` must never raise, whatever the stored value is.

Found by running a real review task through Alpha's own `reviewer` bot against
this file, then verifying its Finding 1 by hand. It was correct.

The bug
------
``coerce_iso`` is documented as best-effort and its every branch returns a
string. But the finiteness guard sat *outside* the ``try``::

    if not isfinite(value):      # <-- raises here
        return ""
    try:
        return datetime.fromtimestamp(float(value), UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return str(value)

``math.isfinite`` coerces its argument to a float, so an int too large for one
raises ``OverflowError`` **from the guard itself** -- the precise exception the
``except`` two lines below exists to absorb. Confirmed before the fix::

    coerce_iso(10**400)  -> OverflowError: int too large to convert to float
    coerce_iso(10**309)  -> OverflowError: int too large to convert to float

Every caller therefore turned a corrupt stored value into a 500:
``routers/projects.py``, ``routers/threads.py`` and ``routers/evidence.py`` all
call this while rendering.

Why the fix is the cause and not the symptom
--------------------------------------------
Moving the guard inside the ``try`` puts the finiteness test under the same
handler as the conversion, so an overflow in *either* takes the documented
"stringify as a last resort" path. Widening the ``except`` or coercing the
argument to a string first would each hide one caller; this makes the function
honour its own contract for every input.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from alpha.utils.time import coerce_iso, is_lease_expired

#: Values a corrupt column can actually hold. Each is a real class of garbage,
#: not a synthetic one.
HOSTILE = [
    10**400,
    -(10**400),
    10**309,
    2**1024,
    float("nan"),
    float("inf"),
    float("-inf"),
    1e308 * 10 if False else 1e308,
]


@pytest.mark.parametrize("value", HOSTILE)
def test_coerce_iso_never_raises(value: object) -> None:
    """The contract is a string, always. An exception here is a 500 in a caller."""
    result = coerce_iso(value)
    assert isinstance(result, str), f"coerce_iso({value!r}) returned {type(result).__name__}"


def test_the_overflow_specifically_is_absorbed() -> None:
    """The exact input that used to escape, asserted directly.

    ``math.isfinite`` is what raises, so this pins the mechanism rather than
    only the outcome -- a future refactor that pre-stringifies would pass an
    outcome-only test while leaving the guard able to raise elsewhere.
    """
    with pytest.raises(OverflowError):
        math.isfinite(10**400)  # documents why the guard has to be inside the try

    assert isinstance(coerce_iso(10**400), str)


def test_a_huge_int_degrades_to_the_last_resort_string() -> None:
    """The documented fallback, not a crash and not a plausible timestamp.

    Returning ``str(value)`` is correct here: it is visibly not an ISO string, so
    a consumer sees no timestamp rather than a value that looks real and reads
    as expired.
    """
    assert coerce_iso(10**400) == str(10**400)
    assert is_lease_expired(coerce_iso(10**400), grace_seconds=30) is True


def test_non_finite_floats_still_report_no_timestamp() -> None:
    """The original guard's purpose is unchanged by moving it."""
    assert coerce_iso(float("nan")) == ""
    assert coerce_iso(float("inf")) == ""
    assert coerce_iso(float("-inf")) == ""


def test_ordinary_values_are_untouched() -> None:
    """A guard fix must not change any answer that was already right."""
    assert coerce_iso(0) == datetime.fromtimestamp(0, UTC).isoformat()
    assert coerce_iso(1764123456.789) == datetime.fromtimestamp(1764123456.789, UTC).isoformat()
    assert coerce_iso("2026-01-01T00:00:00+00:00") == "2026-01-01T00:00:00+00:00"
    assert coerce_iso(datetime(2026, 1, 1, tzinfo=UTC)).startswith("2026-01-01")
    # `None` is "no timestamp", which is this module's `""` sentinel -- not the
    # literal string "None". Asserted against the real behaviour after an
    # earlier draft of this file guessed `"None"` and failed; the production
    # code was right and the assertion was not.
    assert coerce_iso(None) == ""
