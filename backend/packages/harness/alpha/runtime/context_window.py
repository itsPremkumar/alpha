"""Context-window accounting: declared, usable, and occupied.

Three numbers that are routinely collapsed into one cause every bug this module
exists to prevent:

* the **declared** window an operator wrote in ``config.yaml``
  (``models[].context_window``),
* the **usable input** window that is left once the response is paid for, and
* the **occupancy** the thread's messages actually measure.

The industry's own capability contract already separates them: OpenRouter's
descriptor keeps ``context_length`` (the model), ``endpoint_context_length``
(what *this* endpoint serves) and ``endpoint_max_completion_tokens`` apart,
precisely because collapsing them "is the mistake that produces the documented
drift". Alpha carries the same split one layer in.

## What ``models[].context_window`` means here

It is an **input** window. That is a correction, not a restatement: the field
was documented as "prompt + completion" while every consumer treated it as
input-only — :func:`alpha.models.factory` puts it in the langchain profile as
``max_input_tokens``, the fraction summarization triggers resolve their
thresholds against it, and the Gateway's usage percentage divides the *input*
message count by it. An operator who read the old description and declared the
total would have understated occupancy by exactly their output reservation, and
the summarization fraction would have fired late by the same amount.

So the total is derived, never declared:
``usable_input_window = declared - output_reserve - next_turn_reserve``.

## Bands, and why the ceiling is not the problem

Occupancy is not binary. A model asked to reason over a full window degrades
*well before* the window is full — "lost in the middle", context rot, call it
what the literature calls it — so the bands are ordered on a fraction of the
usable window and the highest one is deliberately **below** 1.0:

| Band | Means | What a caller does |
|---|---|---|
| ``unknown`` | no window is declared | nothing; the percentage is hidden, never ``0`` |
| ``nominal`` | comfortably under the elevated threshold | nothing |
| ``elevated`` | compaction should run before the next turn | pre-empt |
| ``critical`` | a turn will not fit headroom | act now |
| ``over`` | occupancy already exceeds the usable window | recover, or park |

``unknown`` is a real answer and never degrades into ``nominal``: a deployment
that declared nothing must not be told it is comfortable, which is the same
negative rule ``alpha.runtime.network`` applies to connectivity.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: Ordered occupancy bands, weakest first. ``unknown`` is a *measurement* state,
#: not a severity, and sorts first so a caller that compares bands numerically
#: does not rank an undeclared window above a declared one.
ContextPressureBand = Literal["unknown", "nominal", "elevated", "critical", "over"]

#: Bands in ascending order, excluding ``unknown``. Used to compare two bands
#: without re-deriving the threshold arithmetic.
_PRESSURE_ORDER: tuple[str, ...] = ("nominal", "elevated", "critical", "over")

#: Bands a caller should treat as "act before the next turn".
PRE_EMPTIVE_BANDS: frozenset[str] = frozenset({"elevated", "critical", "over"})

#: Stable machine-readable reason codes for a pressure reading. A caller renders
#: these rather than inventing a sentence, so two surfaces cannot disagree
#: about why a window reads the way it does.
REASON_WINDOW_NOT_DECLARED = "context_window_not_declared"
REASON_WINDOW_UNDECLARED_ONLY = "context_window_declared_but_unusable"
REASON_NOMINAL = "occupancy_nominal"
REASON_ELEVATED = "occupancy_elevated"
REASON_CRITICAL = "occupancy_critical"
REASON_OVER = "occupancy_exceeds_usable_window"
REASON_OCCUPANCY_NEGATIVE = "occupancy_measurement_negative"
REASON_THRESHOLD_UNSET = "threshold_not_configured"


def _coerce_tokens(value: object | None) -> int | None:
    """Return a positive token count, or ``None`` when nothing was declared.

    Coercion never raises out of a request path: a malformed reading becomes
    ``None`` (undeclared) rather than a crash that would take the thread's
    usage panel down with it.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def band_rank(band: str) -> int:
    """Rank a band so two readings can be compared.

    ``unknown`` ranks **below** ``nominal`` on purpose: an unmeasured window is
    not a comfortable one, and a caller choosing "the tighter of these two
    readings" must land on ``unknown`` rather than on a band derived from a
    fraction nobody configured. An unrecognised band also ranks below nominal
    for the same reason — a word from a newer Gateway must not read as healthy.
    """
    if band not in _PRESSURE_ORDER:
        return -1
    return _PRESSURE_ORDER.index(band)


def is_pre_emptive(band: str) -> bool:
    """True when the band says a caller should act before the next turn."""
    return band in PRE_EMPTIVE_BANDS


@dataclass(frozen=True)
class ContextWindowSpec:
    """The window a model request may occupy, derived rather than declared.

    ``declared_input_window`` is ``None`` when the operator declared nothing.
    That is a distinct state from a declared window that is too small to use,
    which is what ``reason`` distinguishes.
    """

    declared_input_window: int | None
    output_reserve: int = 0
    next_turn_reserve: int = 0
    minimum_usable_input_window: int = 1

    @property
    def usable_input_window(self) -> int | None:
        """Tokens a request may actually fill.

        ``None`` when nothing is declared, so a caller cannot accidentally
        compute a percentage against a zero denominator. A reservation that
        would consume the whole declared window is **clamped to the floor** and
        disclosed, never silently reduced to zero (a zero usable window reads as
        "always over" and would fire compaction on every turn).
        """
        declared = _coerce_tokens(self.declared_input_window)
        if declared is None:
            return None
        reserved = max(0, self.output_reserve) + max(0, self.next_turn_reserve)
        usable = declared - reserved
        floor = max(1, int(self.minimum_usable_input_window))
        return max(usable, floor)

    @property
    def reserved_tokens(self) -> int:
        """Tokens held back across the response and the next turn."""
        return max(0, self.output_reserve) + max(0, self.next_turn_reserve)

    @property
    def clamped(self) -> bool:
        """True when the reservation was clamped to the usable floor."""
        declared = _coerce_tokens(self.declared_input_window)
        if declared is None:
            return False
        return declared - self.reserved_tokens < max(1, int(self.minimum_usable_input_window))

    @property
    def reason(self) -> str:
        """Why the window reads the way it does, for disclosure beside the number."""
        if _coerce_tokens(self.declared_input_window) is None:
            return REASON_WINDOW_NOT_DECLARED
        if self.clamped:
            return REASON_WINDOW_UNDECLARED_ONLY
        return "context_window_declared"


def resolve_context_window(
    *,
    declared_input_window: object | None = None,
    output_reserve: object | None = 0,
    next_turn_reserve: object | None = 0,
    minimum_usable_input_window: object | None = 1,
) -> ContextWindowSpec:
    """Build a :class:`ContextWindowSpec` from loosely-typed inputs.

    Every argument is coerced rather than trusted, because these values arrive
    from ``config.yaml``, a live model config, and an HTTP payload — three
    writers, one of which a client controls.
    """
    return ContextWindowSpec(
        declared_input_window=_coerce_tokens(declared_input_window),
        output_reserve=_coerce_tokens(output_reserve) or 0,
        next_turn_reserve=_coerce_tokens(next_turn_reserve) or 0,
        minimum_usable_input_window=_coerce_tokens(minimum_usable_input_window) or 1,
    )


@dataclass(frozen=True)
class ContextPressure:
    """One reading of how full a window is, and what to do about it.

    ``headroom_tokens`` is ``None`` exactly when ``band`` is ``unknown``. There
    is no reading in which an undeclared window reports a headroom, because
    "how much room is left" is unanswerable without the size of the room.
    """

    band: ContextPressureBand
    occupancy_tokens: int
    declared_input_window: int | None
    usable_input_window: int | None
    headroom_tokens: int | None
    reserved_tokens: int
    occupancy_fraction: float | None
    reason: str
    spec: ContextWindowSpec

    @property
    def is_pre_emptive(self) -> bool:
        """True when a caller should compact before the next turn."""
        return is_pre_emptive(self.band)


def classify_context_pressure(
    occupancy_tokens: object | None,
    spec: ContextWindowSpec,
    *,
    elevated_fraction: float | None = None,
    critical_fraction: float | None = None,
    over_fraction: float | None = None,
) -> ContextPressure:
    """Classify occupancy against a usable window.

    An **unset threshold never blocks a band that a higher threshold would
    reach**. ``critical_fraction=None`` with ``over_fraction=0.95`` set still
    reports ``over`` at 96%, because a missing middle rung must not disable the
    one above it — that is the same fail-open-toward-evidence rule
    ``alpha.runtime.network`` applies to an unset hysteresis gate. A threshold
    that is set but not in ``(0, 1]`` is ignored rather than clamped, so a
    mis-typed ``1.5`` cannot read as "over at 150% of the window".
    """
    usable = spec.usable_input_window
    occupancy = 0 if occupancy_tokens is None else int(occupancy_tokens)
    if occupancy < 0:
        # A negative occupancy is a measurement fault, not an empty context.
        # Report it clamped with the reason attached so the panel can say so.
        occupancy = 0

    declared = _coerce_tokens(spec.declared_input_window)
    if usable is None or declared is None:
        return ContextPressure(
            band="unknown",
            occupancy_tokens=occupancy,
            declared_input_window=declared,
            usable_input_window=usable,
            headroom_tokens=None,
            reserved_tokens=spec.reserved_tokens,
            occupancy_fraction=None,
            reason=REASON_WINDOW_NOT_DECLARED,
            spec=spec,
        )

    fraction = occupancy / usable if usable > 0 else None
    band: str = "nominal"
    reason = REASON_NOMINAL
    if fraction is not None:
        if _at_or_above(fraction, over_fraction):
            band, reason = "over", REASON_OVER
        elif _at_or_above(fraction, critical_fraction):
            band, reason = "critical", REASON_CRITICAL
        elif _at_or_above(fraction, elevated_fraction):
            band, reason = "elevated", REASON_ELEVATED

    raw_negative = isinstance(occupancy_tokens, (int, float)) and not isinstance(occupancy_tokens, bool) and occupancy_tokens < 0
    if raw_negative and band == "nominal":
        # A negative reading is disclosed rather than silently clamped to 0%,
        # which would read as an empty thread.
        reason = REASON_OCCUPANCY_NEGATIVE

    return ContextPressure(
        band=band,  # type: ignore[arg-type]
        occupancy_tokens=occupancy,
        declared_input_window=declared,
        usable_input_window=usable,
        headroom_tokens=max(0, usable - occupancy),
        reserved_tokens=spec.reserved_tokens,
        occupancy_fraction=round(fraction, 4) if fraction is not None else None,
        reason=reason,
        spec=spec,
    )


def _at_or_above(fraction: float, threshold: float | None) -> bool:
    """True when ``fraction`` reaches a threshold that is actually configured.

    A ``None`` threshold is not "never", it is "not configured": the band it
    guards is skipped and a *higher* threshold may still fire. A threshold
    outside ``(0, 1]`` is ignored for the same reason a mis-typed one would
    otherwise mis-fire.
    """
    if threshold is None:
        return False
    if not 0 < threshold <= 1:
        return False
    return fraction >= threshold


def pressure_payload(pressure: ContextPressure) -> dict[str, object]:
    """Project a reading onto the stable API shape.

    Every absence is an explicit ``None``. A caller rendering this payload must
    never substitute a default, because ``declared_input_window: None`` and
    ``declared_input_window: 0`` are different claims: the first says nobody
    declared a window, the second says the window is zero tokens.
    """
    return {
        "band": pressure.band,
        "occupancy_tokens": pressure.occupancy_tokens,
        "declared_input_window": pressure.declared_input_window,
        "usable_input_window": pressure.usable_input_window,
        "headroom_tokens": pressure.headroom_tokens,
        "reserved_tokens": pressure.reserved_tokens,
        "occupancy_fraction": pressure.occupancy_fraction,
        "reason": pressure.reason,
    }


__all__ = [
    "PRE_EMPTIVE_BANDS",
    "REASON_CRITICAL",
    "REASON_ELEVATED",
    "REASON_NOMINAL",
    "REASON_OCCUPANCY_NEGATIVE",
    "REASON_OVER",
    "REASON_THRESHOLD_UNSET",
    "REASON_WINDOW_NOT_DECLARED",
    "REASON_WINDOW_UNDECLARED_ONLY",
    "ContextPressure",
    "ContextPressureBand",
    "ContextWindowSpec",
    "band_rank",
    "classify_context_pressure",
    "is_pre_emptive",
    "pressure_payload",
    "resolve_context_window",
]
