"""Bounded due-time parsing for explicit reminder phrases.

Prospective capture used to record every ``remind me to …`` request as an
undated obligation; the only due logic downstream was expiry after an elapsed
grace window. This module adds the deterministic half of date parsing: the
explicit relative and clock phrases the user actually wrote are turned into a
``due_at`` epoch, and the matched phrase plus the interpretation rule are
recorded so the origin of a due time stays auditable.

Everything here is deliberately bounded:

* stdlib only — no external date library, no model call, no network access;
* local-first — wall-clock semantics use the server's local timezone (the same
  timezone ``DynamicContextMiddleware`` quotes back to the model);
* self-disambiguating clocks only — an hour of 1–12 must carry an explicit
  ``am``/``pm`` and every other accepted clock phrase is a 24h reading;
  ``at 5`` or ``at 9:30`` alone are refused because the meridiem would be
  guesswork;
* conservative — anything ambiguous (two conflicting day cues, two different
  clocks, a phrase outside the supported grammar, a false match inside an
  e-mail address) returns ``None`` and the reminder stays undated rather than
  getting a wrong date;
* observable — ``None`` means "stay undated"; on success :class:`DueAtParse`
  carries the matched phrase and the stable rule name that produced the epoch;
* injectable clock — ``now`` is always accepted so tests and callers stay
  deterministic.
"""

from __future__ import annotations

import calendar
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

__all__ = ["DueAtParse", "parse_due_at"]

#: Documented default wall-clock times for unqualified part-of-day phrases.
#: These are conventions, and the applied one is recorded in
#: :attr:`DueAtParse.rule` (and persisted next to the reminder) so a due time
#: is never arbitrary-invisible. An explicit clock phrase in the same request
#: always wins over these defaults.
TIME_OF_DAY_DEFAULTS: dict[str, int] = {
    "morning": 9,
    "afternoon": 15,
    "evening": 19,
    "tonight": 21,
}

_NOON_HOUR = 12

_NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "a": 1,
    "an": 1,
}

_OFFSET_SECONDS: dict[str, float] = {
    "minute": 60.0,
    "minutes": 60.0,
    "hour": 3600.0,
    "hours": 3600.0,
    "day": 86400.0,
    "days": 86400.0,
    "week": 604800.0,
    "weeks": 604800.0,
}

_OFFSET_RE = re.compile(
    r"\bin\s+(?P<num>\d+|" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + r")\s+(?P<unit>minutes?|hours?|days?|weeks?)\b",
    re.IGNORECASE,
)

#: ``at 5pm``, ``at 5:30 pm``, ``at 17:30``, ``@5pm``. The hour is only trusted
#: through :func:`_abs_hour`, so an unqualified 1–12 hour stays refused.
_CLOCK_AT_RE = re.compile(r"\b(?:at|@)\s*(\d{1,2})(?::(\d{1,2}))?(?::\d{2})?\s*(am|pm)?\b", re.IGNORECASE)
#: ``5pm`` / ``5 pm`` without the ``at`` prefix (needs an explicit meridiem).
_CLOCK_MER_RE = re.compile(r"\b(\d{1,2})(?::(\d{1,2}))?\s*(am|pm)\b", re.IGNORECASE)
#: ``13 o'clock`` — accepted through the same 24h disambiguation rule.
_OCLOCK_RE = re.compile(r"\b(\d{1,2})\s*o'?clock\b", re.IGNORECASE)

_TODAY_RE = re.compile(r"\btoday\b", re.IGNORECASE)
_TOMORROW_RE = re.compile(r"\btomorrow\b", re.IGNORECASE)
_TONIGHT_RE = re.compile(r"\btonight\b", re.IGNORECASE)
_PART_OF_DAY_RE = re.compile(r"\bthis\s+(morning|afternoon|evening)\b|\bin the\s+(morning|afternoon|evening)\b", re.IGNORECASE)
_NOON_RE = re.compile(r"\bnoon\b", re.IGNORECASE)
_MIDNIGHT_RE = re.compile(r"\bmidnight\b", re.IGNORECASE)

_NEXT_RE = re.compile(r"\bnext\s+(week|month)\b", re.IGNORECASE)
_WEEKDAY_RE = re.compile(r"\b(?:on\s+)?(?P<next>next\s+)?(?P<name>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.IGNORECASE)

_WEEKDAY_NAMES: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

#: Rules whose day must be re-decided from the effective clock time instead of
#: their own default-hour day, so ``remind me tonight at 11pm`` said at 21:30
#: lands tonight at 23:00 rather than at a day chosen for the 21:00 default.
_PART_CARRIER_PREFIXES = ("tonight_", "morning_", "afternoon_", "evening_", "noon_", "midnight_")

#: Anchor kinds that scroll forward by whole weeks when their substituted
#: clock time already passed: ``friday at 5pm`` spoken Friday evening means
#: *next* Friday, not tomorrow afternoon. Everything else scrolls one day.
_WEEKLY_ROLL_UNIT = "week"

_TOKEN_CHARS_RE = re.compile(r"[\w@.\-/:]")


@dataclass(slots=True, frozen=True)
class DueAtParse:
    """One confidently parsed due time.

    ``phrase`` is the matched text (useful for audit metadata) and ``rule`` is
    the stable name of the interpretation applied, so an operator can see
    which convention produced a due time.
    """

    due_at: float
    phrase: str
    rule: str

    def to_metadata(self) -> dict[str, Any]:
        """Return the bounded metadata block the middleware persists."""

        return {"due_at": self.due_at, "due_phrase": self.phrase, "due_rule": self.rule}


@dataclass(slots=True)
class _Anchor:
    """One candidate due moment awaiting conflict resolution."""

    moment: datetime
    phrase: str
    rule: str
    roll_unit: str


def _token_before_ok(text: str, index: int) -> bool:
    """True when no token character sits directly before ``index``.

    Keeps ``bob@5pm.com`` an address and ``page-5`` a filename by refusing
    matches glued to a preceding word, ``@``, ``.``, ``-``, ``:`` or ``/``.
    """

    return index == 0 or _TOKEN_CHARS_RE.match(text[index - 1]) is None


def _token_after_ok(text: str, index: int) -> bool:
    """True when no token character sits directly after ``index``."""

    return index >= len(text) or _TOKEN_CHARS_RE.match(text[index]) is None


def _parse_number(raw: str | None) -> int | None:
    text = (raw or "").strip().lower()
    if text.isdigit():
        return int(text) if int(text) > 0 else None
    return _NUMBER_WORDS.get(text)


def _self_disambiguating(hour: int) -> bool:
    """A 24h hour that cannot be confused with a 12h clock-face reading."""

    return hour == 0 or hour >= 13


def _abs_hour(hour: int, minute: int, meridiem: str | None) -> tuple[int, int] | None:
    """Convert a parsed clock phrase to a 24h ``(hour, minute)`` pair.

    With ``am``/``pm`` the hour must be 1–12. Without it only a
    :func:`_self_disambiguating` hour (``17:30``, ``0:30`` or ``21:00``) is
    trusted; ``at 5`` and ``at 9:30`` return ``None``.
    """

    if not 0 <= minute <= 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        meridiem = meridiem.lower()
        if meridiem == "am":
            return (0 if hour == 12 else hour, minute)
        return (12 if hour == 12 else 12 + hour, minute)
    if not _self_disambiguating(hour) or hour > 23:
        return None
    return (hour, minute)


def _minutes(raw: str | None) -> int:
    return int(raw) if raw else 0


def _epoch(moment: datetime) -> float:
    """Encode an aware datetime as a whole-second epoch."""

    return float(int(moment.timestamp()))


def _minute_floor(moment: datetime) -> datetime:
    """Reduce ``moment`` to minute resolution for roll-forward comparisons."""

    return moment.replace(second=0, microsecond=0)


def _at_time(moment: datetime, hour: int, minute: int) -> datetime:
    """Return ``moment``'s date carrying ``hour:minute``."""

    return moment.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _future_part_moment(base: datetime, hour: int, minute: int) -> datetime:
    """The next time ``hour:minute`` occurs strictly after ``base``."""

    moment = _at_time(base, hour, minute)
    return moment if moment > base else moment + timedelta(days=1)


def _next_weekday_occurrence(base: datetime, weekday: int, *, spoken_as_next: bool) -> datetime:
    """The next occurrence of ``weekday``.

    The plain weekday includes today when spoken on that day; ``next
    <weekday>`` always leaves the current calendar week.
    """

    days = (weekday - base.weekday()) % 7
    if not spoken_as_next:
        return base + timedelta(days=days)
    candidate = base + timedelta(days=days if days else 7)
    if candidate.isocalendar()[:2] == base.isocalendar()[:2]:
        candidate += timedelta(days=7)
    return candidate


def _add_months(base: datetime, months: int) -> datetime:
    """Add calendar months, clamping the day-of-month on shorter months."""

    year = base.year + (base.month - 1 + months) // 12
    month = (base.month - 1 + months) % 12 + 1
    day = min(base.day, calendar.monthrange(year, month)[1])
    return base.replace(year=year, month=month, day=day)


def _collect_clock(text: str) -> tuple[tuple[int, int] | None, str]:
    """Find one confident clock phrase; two different clocks refuse."""

    found: tuple[int, int] | None = None
    phrase = ""
    for pattern, has_meridiem in ((_CLOCK_AT_RE, True), (_CLOCK_MER_RE, True), (_OCLOCK_RE, False)):
        for match in pattern.finditer(text):
            if not _token_before_ok(text, match.start(1)) or not _token_after_ok(text, match.end()):
                continue
            if has_meridiem:
                parsed = _abs_hour(int(match.group(1)), _minutes(match.group(2)), match.group(3))
            else:
                parsed = _abs_hour(int(match.group(1)), 0, None)
            if parsed is None:
                continue
            if found is not None and found != parsed:
                return None, "conflicting"
            if found is None:
                found, phrase = parsed, match.group(0)
    return found, phrase


def _relative_anchors(text: str, base: datetime) -> list[_Anchor]:
    anchors: list[_Anchor] = []
    for match in _OFFSET_RE.finditer(text):
        number = _parse_number(match.group("num"))
        seconds = _OFFSET_SECONDS.get(match.group("unit").lower())
        if number is None or seconds is None:
            continue
        moment = base + timedelta(seconds=number * seconds)
        anchors.append(_Anchor(moment, match.group(0), f"relative_{number}_{match.group('unit').lower()}", "none"))
    return anchors


def _weekday_anchors(text: str, base: datetime) -> list[_Anchor]:
    anchors: list[_Anchor] = []
    for match in _WEEKDAY_RE.finditer(text):
        name = match.group("name").lower()
        spoken_as_next = match.group("next") is not None
        moment = _next_weekday_occurrence(base, _WEEKDAY_NAMES[name], spoken_as_next=spoken_as_next)
        rule = f"next_{name}" if spoken_as_next else f"weekday_{name}"
        anchors.append(_Anchor(moment, match.group(0), rule, _WEEKLY_ROLL_UNIT))
    return anchors


def _next_period_anchors(text: str, base: datetime) -> list[_Anchor]:
    anchors: list[_Anchor] = []
    for match in _NEXT_RE.finditer(text):
        label = match.group(1).lower()
        moment = base + timedelta(days=7) if label == "week" else _add_months(base, 1)
        anchors.append(_Anchor(moment, match.group(0), f"next_{label}", "none"))
    return anchors


def _named_day_anchors(text: str, base: datetime) -> list[_Anchor]:
    anchors: list[_Anchor] = []
    match = _TOMORROW_RE.search(text)
    if match is not None:
        anchors.append(_Anchor(base + timedelta(days=1), match.group(0), "tomorrow_next_day", "day"))
    match = _TODAY_RE.search(text)
    if match is not None:
        anchors.append(_Anchor(base, match.group(0), "today", "day"))
    match = _TONIGHT_RE.search(text)
    if match is not None:
        anchors.append(_part_of_day_anchor(base, "tonight", TIME_OF_DAY_DEFAULTS["tonight"], match.group(0)))
    match = _PART_OF_DAY_RE.search(text)
    if match is not None:
        word = (match.group(1) or match.group(2) or "").lower()
        hour = TIME_OF_DAY_DEFAULTS.get(word, TIME_OF_DAY_DEFAULTS["evening"])
        anchors.append(_part_of_day_anchor(base, word, hour, match.group(0)))
    match = _NOON_RE.search(text)
    if match is not None:
        anchors.append(_part_of_day_anchor(base, "noon", _NOON_HOUR, match.group(0)))
    match = _MIDNIGHT_RE.search(text)
    if match is not None:
        tomorrow = _at_time(base, 0, 0) + timedelta(days=1)
        anchors.append(_Anchor(tomorrow, match.group(0), "midnight_next", "day"))
    return anchors


def _part_of_day_anchor(base: datetime, word: str, hour: int, phrase: str) -> _Anchor:
    return _Anchor(_future_part_moment(base, hour, 0), phrase, f"{word}_{hour}", "day")


def _resolve(base: datetime, clock: tuple[int, int] | None, clock_phrase: str, anchors: list[_Anchor]) -> DueAtParse | None:
    """Combine anchors into one confident interpretation, or ``None``."""

    if not anchors:
        if clock is None:
            return None
        moment = _future_part_moment(base, clock[0], clock[1])
        rule = "clock_next_day" if moment.date() != base.date() else "clock_today"
        return DueAtParse(due_at=_epoch(moment), phrase=clock_phrase, rule=rule)

    if clock is not None:
        if any(anchor.rule.startswith("relative_") for anchor in anchors):
            # "in 2 hours at 5pm": the offset already fixes the whole instant;
            # adding a second, independent clock reading has no single
            # interpretation, so the request stays undated.
            return None
        offset_hour, offset_minute = clock
        resolved: list[_Anchor] = []
        for anchor in anchors:
            if anchor.rule.startswith(_PART_CARRIER_PREFIXES):
                moment = _future_part_moment(base, offset_hour, offset_minute)
            else:
                moment = _at_time(anchor.moment, offset_hour, offset_minute)
            resolved.append(_Anchor(moment, anchor.phrase, f"{anchor.rule}+clock", anchor.roll_unit))
        anchors = resolved

    readings = {_epoch(anchor.moment) for anchor in anchors}
    if len(readings) > 1:
        return None

    anchor = anchors[0]
    due = _epoch(anchor.moment)
    if anchor.roll_unit != "none" and due < _epoch(_minute_floor(base)):
        days = 7 if anchor.roll_unit == _WEEKLY_ROLL_UNIT else 1
        suffix = "next_week" if anchor.roll_unit == _WEEKLY_ROLL_UNIT else "next_day"
        anchor = _Anchor(anchor.moment + timedelta(days=days), anchor.phrase, f"{anchor.rule}+{suffix}", "none")
        due = _epoch(anchor.moment)
    return DueAtParse(due_at=due, phrase=anchor.phrase, rule=anchor.rule)


def parse_due_at(text: str, now: float | None = None) -> DueAtParse | None:
    """Parse one due time from reminder prose, or return ``None`` when unsure.

    ``None`` means "stay undated", never "wrong date".
    """

    if not isinstance(text, str) or not text.strip():
        return None
    base = _local_now(now)

    clock, clock_phrase = _collect_clock(text)
    if clock_phrase == "conflicting":
        return None

    anchors: list[_Anchor] = [
        *_relative_anchors(text, base),
        *_weekday_anchors(text, base),
        *_next_period_anchors(text, base),
        *_named_day_anchors(text, base),
    ]
    return _resolve(base, clock, clock_phrase, anchors)


def _local_now(now: float | None) -> datetime:
    """Convert an epoch (or real time) into an aware local datetime."""

    epoch = time.time() if now is None else float(now)
    return datetime.fromtimestamp(epoch).astimezone()
