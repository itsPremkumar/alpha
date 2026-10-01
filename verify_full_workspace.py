"""Full-workspace verification: every implemented module, driven together.

Builds one AgentWorkspace and exercises the complete feature set — modes,
connectors, experts + expert groups, skill marketplace, egress, routines,
automations — plus persistence, mode enforcement, and the capability scorecard.

Run: python verify_full_workspace.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

HARNESS = Path(__file__).resolve().parent / "backend" / "packages" / "harness"
sys.path.insert(0, str(HARNESS))

from alpha.automations import Automation, AutomationSchedule, Frequency, next_run, to_rrule  # noqa: E402
from alpha.egress import EgressRoute, EgressValidationError, import_profile  # noqa: E402
from alpha.modes import ModeViolation  # noqa: E402
from alpha.routines import RoutineRecorder, replay  # noqa: E402
from alpha.workspace import AgentWorkspace  # noqa: E402

CHECKS: list[tuple[str, bool]] = []


def section(t: str) -> None:
    print(f"\n{'=' * 72}\n{t}\n{'=' * 72}")


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


root = Path(tempfile.mkdtemp(prefix="alpha_full_ws_"))
ws = AgentWorkspace(root, mode="craft", user_id="priya")
print(f"Workspace: {root}")

# ---------------------------------------------------------------------
section("A — One workspace, every module wired")
check("A1  connectors catalog wired", ws.connectors.get("gmail") is not None)
check("A2  experts catalog wired", ws.experts.get_expert("researcher") is not None)
check("A3  skill marketplace wired", ws.skills_market.get("web-search") is not None)
check("A4  stores start empty", ws.status()["connectors_enabled"] == 0)

# ---------------------------------------------------------------------
section("B — Connectors, Experts, Skills (install across modules)")
for cid in ("gmail", "slack", "hubspot"):
    ws.install_connector(cid)
ws.install_expert("researcher")
ws.install_expert("data-analyst")
ws.install_expert_group("content-creation")
for sid in ("excel-processing", "web-search", "ppt-generator"):
    ws.install_skill(sid)
st = ws.status()
print(f"  status: {st}")
check("B1  connectors installed", st["connectors_enabled"] == 3)
check("B2  experts + group installed", st["experts_enabled"] == 3)
check("B3  marketplace skills installed", st["skills_enabled"] == 3)
check("B4  expert group pipeline resolves",
      [e.id for e in ws.experts.group_members("content-creation")] ==
      ["creative-director", "copywriter", "graphic-designer", "video-generator"])

# ---------------------------------------------------------------------
section("C — Egress routing + profile (security boundary)")
ws.egress.set_default_route(EgressRoute.DIRECT)
ws.egress.add_rule("*.bank.com", EgressRoute.RESIDENTIAL_PROXY, prepend=True)
ws.egress.add_profile(import_profile("priya", label="Priya Chrome",
                                     source_path="/profiles/priya", authenticated_domains=["mail.google.com"]))
check("C1  finance domain routed via residential proxy",
      ws.egress.resolve("pay.bank.com") == EgressRoute.RESIDENTIAL_PROXY)
must_raise("C2  credential smuggling refused", EgressValidationError,
           import_profile, "x", source_path='{"cookies":"SID=1"}')

# ---------------------------------------------------------------------
section("D — Routine capture + replay")
rec = RoutineRecorder("renewal_outreach")
rec.record_step("hubspot_list_deals", {"stage": "renewal_due", "owner": "priya@acme.com"})
rec.record_step("gmail_draft", {"to": "{{contact}}", "subject": "Renewal"})
routine = rec.finalize()
ws.save_routine(routine)
calls = replay(ws.routines.get("renewal_outreach"), {"owner": "priya@acme.com", "contact": "cto@bigco.com"})
check("D1  routine saved and replayed", calls[1]["args"]["to"] == "cto@bigco.com",
      f"{len(ws.routines.list())} routine(s); owner auto-parameterised")

# ---------------------------------------------------------------------
section("E — Automations (RRULE)")
now = datetime(2026, 9, 30, 12, 0)
ws.add_automation(Automation("daily-brief", "Daily Briefing", "Summarise news", AutomationSchedule(Frequency.DAILY, at="08:00")))
ws.add_automation(Automation("weekly", "Weekly Report", "Weekly summary", AutomationSchedule(Frequency.WEEKLY, at="17:00", weekday=4)))
ws.add_automation(Automation("monthly", "Monthly Close", "Reconcile", AutomationSchedule(Frequency.MONTHLY, at="09:00", day_of_month=1)))
for a in ws.automations.list():
    print(f"    {a.name:16s} next={next_run(a.schedule, now).isoformat(sep=' ')}  rrule={to_rrule(a.schedule)}")
check("E1  three automations scheduled", ws.status()["automations"] == 3)
check("E2  next-run computation correct",
      next_run(ws.automations.get("weekly").schedule, now) == datetime(2026, 10, 2, 17, 0))

# ---------------------------------------------------------------------
section("F — Mode enforcement")
ws.set_mode("ask")
check("F1  Ask mode is read-only", ws.can("write_file") is False)
must_raise("F2  Ask mode refuses a write", ModeViolation, ws.assert_action, "write_file")
ws.set_mode("craft")
ws.assert_action("write_file")
check("F3  Craft mode unlocks execution", ws.can("run_command") is True)

# ---------------------------------------------------------------------
section("G — Persistence across a restart")
ws2 = AgentWorkspace(root)
check("G1  connectors persisted", ws2.status()["connectors_enabled"] == 3)
check("G2  experts persisted", ws2.status()["experts_enabled"] == 3)
check("G3  skills persisted", ws2.status()["skills_enabled"] == 3)
check("G4  automations persisted", ws2.status()["automations"] == 3)
check("G5  routines persisted", ws2.status()["routines"] == 1)
check("G6  egress policy persisted", ws2.egress.resolve("pay.bank.com") == EgressRoute.RESIDENTIAL_PROXY)

# ---------------------------------------------------------------------
section("H — Capability scorecard")
report = ws.capability_report()
check("H1  frontier coverage is 100%", report.present_count == report.total,
      f"{report.present_count}/{report.total} across {len(report.by_category())} categories")

# ---------------------------------------------------------------------
section("I — Snapshot")
snap = ws.snapshot()
check("I1  snapshot serialises the whole workspace",
      set(snap) >= {"status", "mode_capabilities", "connectors", "experts", "skills", "automations", "routines", "egress"})

# ---------------------------------------------------------------------
section("SUMMARY")
passed = sum(1 for _, ok in CHECKS if ok)
for label, ok in CHECKS:
    if not ok:
        print(f"  FAILED: {label}")
print(f"\n  {passed}/{len(CHECKS)} checks passed")
sys.exit(0 if passed == len(CHECKS) else 1)
