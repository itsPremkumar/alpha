"""Tests for automations (alpha.automations)."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

_HARNESS_ROOT = Path(__file__).resolve().parents[1] / "packages" / "harness"
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

import pytest  # noqa: E402

from alpha.automations import (  # noqa: E402
    Automation,
    AutomationSchedule,
    AutomationStore,
    AutomationStoreUnreadable,
    AutomationValidationError,
    Frequency,
    next_run,
    to_rrule,
)


def test_next_run_daily():
    s = AutomationSchedule(Frequency.DAILY, at="09:00")
    assert next_run(s, datetime(2026, 9, 30, 8, 0)) == datetime(2026, 9, 30, 9, 0)
    assert next_run(s, datetime(2026, 9, 30, 10, 0)) == datetime(2026, 10, 1, 9, 0)


def test_next_run_weekly():
    # 2026-09-30 is a Wednesday; next Monday is 2026-10-05.
    s = AutomationSchedule(Frequency.WEEKLY, at="09:00", weekday=0)
    assert next_run(s, datetime(2026, 9, 30, 12, 0)) == datetime(2026, 10, 5, 9, 0)


def test_next_run_monthly_and_skips_short_months():
    s = AutomationSchedule(Frequency.MONTHLY, at="09:00", day_of_month=15)
    assert next_run(s, datetime(2026, 9, 30)) == datetime(2026, 10, 15, 9, 0)
    # day 31 does not exist in November; next is December 31.
    s31 = AutomationSchedule(Frequency.MONTHLY, at="09:00", day_of_month=31)
    assert next_run(s31, datetime(2026, 10, 31, 10, 0)) == datetime(2026, 12, 31, 9, 0)


def test_next_run_yearly():
    s = AutomationSchedule(Frequency.YEARLY, at="09:00", month=12, day_of_month=25)
    assert next_run(s, datetime(2026, 9, 30)) == datetime(2026, 12, 25, 9, 0)


def test_next_run_once():
    s = AutomationSchedule(Frequency.ONCE, at="09:00", date="2026-12-31T08:00")
    assert next_run(s, datetime(2026, 9, 30)) == datetime(2026, 12, 31, 8, 0)
    assert next_run(s, datetime(2027, 1, 1)) is None


def test_to_rrule():
    assert to_rrule(AutomationSchedule(Frequency.DAILY, at="09:00")) == "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
    assert to_rrule(AutomationSchedule(Frequency.WEEKLY, at="09:30", weekday=0)) == "FREQ=WEEKLY;BYDAY=MO;BYHOUR=9;BYMINUTE=30"
    assert to_rrule(AutomationSchedule(Frequency.MONTHLY, at="09:00", day_of_month=15)) == "FREQ=MONTHLY;BYMONTHDAY=15;BYHOUR=9;BYMINUTE=0"
    assert to_rrule(AutomationSchedule(Frequency.YEARLY, at="09:00", month=12, day_of_month=25)) == "FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=25;BYHOUR=9;BYMINUTE=0"
    assert to_rrule(AutomationSchedule(Frequency.ONCE, at="09:00", date="2026-12-31T08:00")) is None


def test_schedule_validation():
    with pytest.raises(AutomationValidationError):
        AutomationSchedule(Frequency.WEEKLY, at="09:00")  # missing weekday
    with pytest.raises(AutomationValidationError):
        AutomationSchedule(Frequency.MONTHLY, at="09:00")  # missing day
    with pytest.raises(AutomationValidationError):
        AutomationSchedule(Frequency.DAILY, at="25:00")  # bad time
    with pytest.raises(AutomationValidationError):
        AutomationSchedule(Frequency.ONCE, at="09:00")  # missing date


def test_store_add_persist_enable_delete(tmp_path):
    path = tmp_path / "automations.json"
    store = AutomationStore(path)
    a = Automation("daily-brief", "Daily Briefing", "Fetch news and summarise",
                   AutomationSchedule(Frequency.DAILY, at="08:00"))
    store.add(a)
    with pytest.raises(AutomationValidationError):
        store.add(a)  # duplicate id

    store.set_enabled("daily-brief", False)
    reloaded = AutomationStore(path)
    assert reloaded.get("daily-brief") is not None
    assert reloaded.get("daily-brief").enabled is False
    assert reloaded.delete("daily-brief") is True
    assert reloaded.get("daily-brief") is None


def test_store_is_loud_on_corruption(tmp_path):
    path = tmp_path / "automations.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(AutomationStoreUnreadable):
        AutomationStore(path)
