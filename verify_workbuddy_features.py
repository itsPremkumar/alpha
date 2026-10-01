"""Live verification of the WorkBuddy-inspired features.

Scenario: a user drives Alpha the way they would drive WorkBuddy —
  * choose a work mode (Plan to design, Craft to execute),
  * browse and install an Expert Group from the market,
  * schedule automations (daily briefing, weekly report, monthly report).

Run: python verify_workbuddy_features.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "backend" / "packages" / "harness"
sys.path.insert(0, str(HARNESS))

from alpha.modes import ModeViolation, WorkMode, assert_allowed, can_escalate, capabilities_for, parse_mode  # noqa: E402
from alpha.experts import ExpertCatalog, ExpertError, ExpertRegistry  # noqa: E402
from alpha.automations import Automation, AutomationSchedule, AutomationStore, Frequency, next_run, to_rrule  # noqa: E402

CHECKS: list[tuple[str, bool]] = []


def section(t: str) -> None:
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}")


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"\n         -> {detail}" if detail else ""))


def must_raise(label: str, exc: type[BaseException], fn, *a, **k) -> None:
    try:
        fn(*a, **k)
    except exc:
        check(label, True, f"refused ({exc.__name__})")
    except Exception as e:  # noqa: BLE001
        check(label, False, f"wrong exception {type(e).__name__}")
    else:
        check(label, False, "no exception")


work = Path(tempfile.mkdtemp(prefix="wb_live_"))

# ---------------------------------------------------------------------
section("1 — Work modes (Ask / Plan / Craft)")
mode = parse_mode("plan")
print(f"  user starts in {mode.name}: {capabilities_for(mode).description}")
check("1.1  Plan mode permits producing a plan", capabilities_for("plan").produces_artifacts is True)
check("1.2  Plan mode does NOT permit running commands", capabilities_for("plan").can_run_commands is False)
must_raise("1.3  Ask mode refuses to write files", ModeViolation, assert_allowed, "ask", "write_file")
assert_allowed("craft", "write_file")
check("1.4  Craft mode permits writing files", True, "escalating Plan -> Craft unlocks execution")
check("1.5  escalation Plan -> Craft is an increase", can_escalate(WorkMode.PLAN, WorkMode.CRAFT) is True)

# ---------------------------------------------------------------------
section("2 — Experts & Expert Groups (marketplace)")
cat = ExpertCatalog()
print(f"  market has {len(cat.list_experts())} experts and {len(cat.list_groups())} expert groups")
hits = cat.search_experts("research")
check("2.1  expert search finds the Researcher", "researcher" in {e.id for e in hits}, f"hits: {[e.name for e in hits]}")
group = cat.get_group("content-creation")
print(f"  Expert Group '{group.name}' pipeline:")
for step in group.steps:
    print(f"    {step.stage:10s} -> {cat.get_expert(step.expert_id).name}")
check("2.2  expert group resolves all its members", cat.validate_group("content-creation") == [])

reg = ExpertRegistry(work / "experts.json")
reg.install_group("content-creation", enable=True)
reg.install_expert("data-analyst", enable=True)
reloaded = ExpertRegistry(work / "experts.json")
check("2.3  installed experts/groups survive a restart",
      set(reloaded.enabled_ids()) == {"content-creation", "data-analyst"},
      f"enabled: {reloaded.enabled_ids()}")
must_raise("2.4  installing an unknown expert is refused", ExpertError, reg.install_expert, "ghost")

# ---------------------------------------------------------------------
section("3 — Automations (RRULE scheduling)")
store = AutomationStore(work / "automations.json")
now = datetime(2026, 9, 30, 12, 0)
specs = [
    ("daily-brief", "Daily Briefing", "Fetch 3 industry items and summarise", AutomationSchedule(Frequency.DAILY, at="08:00")),
    ("weekly-report", "Weekly Report", "Summarise the week's work", AutomationSchedule(Frequency.WEEKLY, at="17:00", weekday=4)),
    ("monthly-close", "Monthly Close", "Reconcile and report", AutomationSchedule(Frequency.MONTHLY, at="09:00", day_of_month=1)),
]
for aid, name, prompt, sched in specs:
    store.add(Automation(aid, name, prompt, sched))
    nxt = next_run(sched, now)
    print(f"  {name:16s} next={nxt.isoformat(sep=' ')}  rrule={to_rrule(sched)}")

check("3.1  daily automation fires tomorrow at 08:00",
      next_run(specs[0][3], now) == datetime(2026, 10, 1, 8, 0))
check("3.2  weekly automation fires on Friday",
      next_run(specs[1][3], now) == datetime(2026, 10, 2, 17, 0))
check("3.3  monthly automation fires on the 1st",
      next_run(specs[2][3], now) == datetime(2026, 10, 1, 9, 0))
check("3.4  RRULE emitted for a weekly schedule",
      to_rrule(specs[1][3]) == "FREQ=WEEKLY;BYDAY=FR;BYHOUR=17;BYMINUTE=0",
      to_rrule(specs[1][3]))

reloaded_store = AutomationStore(work / "automations.json")
check("3.5  automations survive a restart", len(reloaded_store.list()) == 3,
      f"{[a.name for a in reloaded_store.list()]}")
reloaded_store.set_enabled("daily-brief", False)
check("3.6  an automation can be paused", "daily-brief" not in {a.id for a in reloaded_store.enabled()})

# ---------------------------------------------------------------------
section("SUMMARY")
passed = sum(1 for _, ok in CHECKS if ok)
for label, ok in CHECKS:
    if not ok:
        print(f"  FAILED: {label}")
print(f"\n  {passed}/{len(CHECKS)} checks passed")
sys.exit(0 if passed == len(CHECKS) else 1)
