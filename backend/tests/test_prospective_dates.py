"""Bounded due-time parsing regressions for prospective reminders."""

from __future__ import annotations

from datetime import datetime, timedelta

from alpha.memory.prospective.dates import DueAtParse, parse_due_at

# 2026-10-14 is a Wednesday; noon local keeps the assertions inside one week.
NOW = datetime(2026, 10, 14, 12, 0).astimezone().timestamp()
WEDNESDAY_EVENING = datetime(2026, 10, 14, 18, 0).astimezone().timestamp()
FRIDAY_EVENING = datetime(2026, 10, 16, 18, 0).astimezone().timestamp()
LATE_NIGHT = datetime(2026, 10, 14, 21, 30).astimezone().timestamp()
MID_MORNING = datetime(2026, 10, 14, 11, 0, 30).astimezone().timestamp()
JANUARY_END = datetime(2026, 1, 31, 12, 0).astimezone().timestamp()


def _base(now: float = NOW) -> datetime:
    return datetime.fromtimestamp(now).astimezone()


def _expect(due_at: float | None = None, hour: int | None = None, days: int = 0, minutes: int = 0) -> datetime:
    """Build the expected local moment relative to ``NOW``."""

    if due_at is not None:
        return datetime.fromtimestamp(due_at).astimezone()
    base = _base() + timedelta(days=days)
    if hour is not None:
        return base.replace(hour=hour, minute=minutes, second=0, microsecond=0)
    return base.replace(minute=base.minute + minutes, second=0, microsecond=0)


def test_relative_offsets_use_the_injected_clock() -> None:
    parsed = parse_due_at("Remind me in 30 minutes", NOW)
    assert parsed is not None
    assert parsed.due_at == (_base() + timedelta(minutes=30)).timestamp()
    assert parsed.rule == "relative_30_minutes"
    assert parsed.phrase == "in 30 minutes"

    two_hours = parse_due_at("remind me in 2 hours", NOW)
    assert two_hours is not None
    assert two_hours.due_at == (_base() + timedelta(hours=2)).timestamp()

    word_number = parse_due_at("remind me in three days", NOW)
    assert word_number is not None
    assert word_number.due_at == (_base() + timedelta(days=3)).timestamp()

    assert parse_due_at("remind me in one week", NOW) is not None
    assert parse_due_at("remind me in 0 minutes", NOW) is None


def test_qualified_clock_phrases_are_trusted() -> None:
    at_5pm = parse_due_at("remind me at 5pm", NOW)
    assert at_5pm == DueAtParse(_expect(hour=17).timestamp(), "at 5pm", "clock_today")

    nine_am = parse_due_at("remind me at 9am", WEDNESDAY_EVENING)
    assert nine_am is not None
    assert nine_am.due_at == (_base(WEDNESDAY_EVENING) + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
    assert nine_am.rule == "clock_next_day"

    clock_24 = parse_due_at("remind me at 21:30", NOW)
    assert clock_24 is not None
    assert clock_24.due_at == _expect(hour=21, minutes=30).timestamp()
    assert clock_24.rule == "clock_today"


def test_bare_unqualified_hours_are_refused() -> None:
    assert parse_due_at("remind me at 5", NOW) is None
    assert parse_due_at("remind me at 9:30", NOW) is None
    assert parse_due_at("remind me 5 o'clock", NOW) is None
    assert parse_due_at("remind me at 5 pm", NOW) is not None


def test_clock_phrases_roll_past_times_to_tomorrow() -> None:
    parsed = parse_due_at("remind me at 17:30", WEDNESDAY_EVENING)
    assert parsed is not None
    assert parsed.due_at == (_base(WEDNESDAY_EVENING) + timedelta(days=1)).replace(hour=17, minute=30, second=0, microsecond=0).timestamp()
    assert parsed.rule == "clock_next_day"


def test_oclock_accepts_only_unambiguous_24h_hours() -> None:
    thirteen = parse_due_at("remind me at 13 o'clock", NOW)
    assert thirteen is not None
    assert thirteen.due_at == _expect(hour=13).timestamp()
    assert thirteen.rule == "clock_today"
    assert parse_due_at("remind me at 5 o'clock", NOW) is None


def test_zero_hour_is_a_24h_reading() -> None:
    parsed = parse_due_at("remind me at 0:30", NOW)
    assert parsed is not None
    assert parsed.due_at == (_base() + timedelta(days=1)).replace(hour=0, minute=30, second=0, microsecond=0).timestamp()
    assert parsed.rule == "clock_next_day"


def test_today_stays_due_now_without_a_clock() -> None:
    parsed = parse_due_at("Remind me today to call mom", NOW)
    assert parsed is not None
    assert parsed.due_at == NOW
    assert parsed.rule == "today"
    assert parsed.phrase == "today"


def test_today_plus_clock_is_that_clock_today() -> None:
    future = parse_due_at("remind me today at 5pm", NOW)
    assert future is not None
    assert future.due_at == _expect(hour=17).timestamp()
    assert future.rule == "today+clock"

    past = parse_due_at("remind me today at 5pm", WEDNESDAY_EVENING)
    assert past is not None
    assert past.due_at == (_base(WEDNESDAY_EVENING) + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0).timestamp()
    assert past.rule == "today+clock+next_day"


def test_named_day_anchors_carry_spawn_time() -> None:
    tomorrow = parse_due_at("remind me tomorrow to check the release", NOW)
    assert tomorrow is not None
    assert tomorrow.due_at == (_base() + timedelta(days=1)).timestamp()
    assert tomorrow.rule == "tomorrow_next_day"

    tonight = parse_due_at("remind me tonight to check the build", NOW)
    assert tonight is not None
    assert tonight.due_at == _expect(hour=21).timestamp()
    assert tonight.rule == "tonight_21"

    late_night = parse_due_at("remind me tonight to check the build", LATE_NIGHT)
    assert late_night is not None
    assert late_night.due_at == (_base(LATE_NIGHT) + timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0).timestamp()

    # A refused clock leaves no trace: the surviving day anchor still yields a
    # spawn-timed due date, never the unverified 05:00 reading.
    fallback = parse_due_at("remind me tomorrow at 5", NOW)
    assert fallback is not None
    assert fallback.due_at == (_base() + timedelta(days=1)).timestamp()
    assert fallback.rule == "tomorrow_next_day"
    assert fallback.phrase == "tomorrow"


def test_part_of_day_defaults_are_recorded() -> None:
    evening = parse_due_at("remind me this evening to publish", NOW)
    assert evening is not None
    assert evening.due_at == _expect(hour=19).timestamp()
    assert evening.rule == "evening_19"

    # Spoken at noon, the next occurrence of "morning" is tomorrow 09:00.
    morning = parse_due_at("remind me in the morning to draft notes", NOW)
    assert morning is not None
    assert morning.due_at == (_base() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
    assert morning.rule == "morning_9"

    noon = parse_due_at("remind me at noon to sync", MID_MORNING)
    assert noon is not None
    assert noon.due_at == _expect(hour=12).timestamp()
    assert noon.rule == "noon_12"

    midnight = parse_due_at("remind me at midnight to rotate keys", NOW)
    assert midnight is not None
    assert midnight.due_at == (_base() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    assert midnight.rule == "midnight_next"


def test_explicit_clock_overrides_the_part_of_day_default() -> None:
    parsed = parse_due_at("remind me tonight at 10pm", NOW)
    assert parsed is not None
    assert parsed.due_at == _expect(hour=22).timestamp()
    assert parsed.rule == "tonight_21+clock"

    late = parse_due_at("remind me tonight at 11pm", LATE_NIGHT)
    assert late is not None
    assert late.due_at == _base(LATE_NIGHT).replace(hour=23, minute=0, second=0, microsecond=0).timestamp()
    assert late.rule == "tonight_21+clock"


def test_weekdays_include_today_and_next_weekday_skips_the_week() -> None:
    friday = parse_due_at("remind me friday to review", NOW)
    assert friday is not None
    assert friday.due_at == (_base() + timedelta(days=2)).timestamp()
    assert friday.rule == "weekday_friday"

    monday = parse_due_at("remind me on monday to renew ssl", NOW)
    assert monday is not None
    assert monday.due_at == (_base() + timedelta(days=5)).timestamp()
    assert monday.rule == "weekday_monday"

    next_friday = parse_due_at("remind me next friday to review", NOW)
    assert next_friday is not None
    assert next_friday.due_at == (_base() + timedelta(days=9)).timestamp()
    assert next_friday.rule == "next_friday"

    next_monday = parse_due_at("remind me next monday to renew ssl", NOW)
    assert next_monday is not None
    assert next_monday.due_at == (_base() + timedelta(days=5)).timestamp()
    assert next_monday.rule == "next_monday"


def test_weekday_with_clock_and_its_weekly_roll() -> None:
    parsed = parse_due_at("remind me friday at 5pm to review", NOW)
    assert parsed is not None
    assert parsed.due_at == (_base() + timedelta(days=2)).replace(hour=17, minute=0, second=0, microsecond=0).timestamp()
    assert parsed.rule == "weekday_friday+clock"

    after = parse_due_at("remind me friday at 5pm to review", FRIDAY_EVENING)
    assert after is not None
    assert after.due_at == (_base(FRIDAY_EVENING) + timedelta(days=7)).replace(hour=17, minute=0, second=0, microsecond=0).timestamp()
    assert after.rule == "weekday_friday+clock+next_week"


def test_next_week_and_next_month() -> None:
    next_week = parse_due_at("remind me next week to reopen the budget", NOW)
    assert next_week is not None
    assert next_week.due_at == (_base() + timedelta(days=7)).timestamp()
    assert next_week.rule == "next_week"

    next_month = parse_due_at("remind me next month to audit subscriptions", NOW)
    assert next_month is not None
    expected = _base()
    assert next_month.due_at == expected.replace(month=11, day=14, hour=12, minute=0, second=0, microsecond=0).timestamp()


def test_short_month_clamps_next_month() -> None:
    parsed = parse_due_at("remind me next month to audit subscriptions", JANUARY_END)
    assert parsed is not None
    assert parsed.due_at == datetime(2026, 2, 28, 12, 0).astimezone().timestamp()


def test_conflicting_cues_refuse_instead_of_guessing() -> None:
    assert parse_due_at("remind me tomorrow and monday at 5pm", NOW) is None
    assert parse_due_at("remind me at 5pm and at 7pm", NOW) is None
    assert parse_due_at("remind me in 2 hours and in 3 hours", NOW) is None
    assert parse_due_at("remind me in 30 minutes at 5pm", NOW) is None
    assert parse_due_at("remind me today at 10pm and tomorrow", NOW) is None


def test_false_matches_stay_out_of_addresses_and_words() -> None:
    assert parse_due_at("email bob@5pm.com about the release", NOW) is None
    assert parse_due_at("meet @5pm", NOW) is None
    assert parse_due_at("save this as page-5", NOW) is None
    assert parse_due_at("scheduled within 5 minutes checks", NOW) is None
    assert parse_due_at("the website at 5pm.example.com is up", NOW) is None


def test_out_of_range_and_malformed_clocks_refuse() -> None:
    assert parse_due_at("remind me at 25:00", NOW) is None
    assert parse_due_at("remind me at 25pm", NOW) is None
    assert parse_due_at("remind me at 5am-pm", NOW) is None


def test_no_cue_stays_undated() -> None:
    assert parse_due_at("remind me to call mom", NOW) is None
    assert parse_due_at("", NOW) is None
    assert parse_due_at(123, NOW) is None  # type: ignore[arg-type]
    assert parse_due_at("   ", NOW) is None


def test_uppercase_and_spacing_are_normalized() -> None:
    loud = parse_due_at("REMIND ME TOMORROW AT 5PM", NOW)
    assert loud is not None
    assert loud.due_at == (_base() + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0).timestamp()

    spaced = parse_due_at("remind me at 5 PM", NOW)
    assert spaced is not None
    assert spaced.due_at == _expect(hour=17).timestamp()


def test_same_now_is_deterministic() -> None:
    first = parse_due_at("remind me tomorrow at 5pm", NOW)
    second = parse_due_at("remind me tomorrow at 5pm", NOW)
    assert first == second


def test_to_metadata_carries_the_audit_triple() -> None:
    parsed = parse_due_at("remind me tomorrow at 5pm", NOW)
    assert parsed is not None
    assert parsed.to_metadata() == {
        "due_at": parsed.due_at,
        "due_phrase": parsed.phrase,
        "due_rule": parsed.rule,
    }
