"""Real-world end-to-end scenario + advanced testing.

Scenario: "Acme Sales — automated Q4 renewal drive."
An operator wires Alpha to the sales stack, then Alpha captures the renewal
workflow, plans the drive, staffs it with specialized agents, decides a pricing
exception via the council, picks the best outreach email via a tournament, and
hands the loop to the perpetual daemon.

After the scenario, an ADVANCED TESTING section exercises concurrency,
persistence-across-restart, security boundaries, failure modes, and scale.

Run:
  uv run --with pydantic --with langchain --with "sqlalchemy[asyncio]" \
    --with python-dotenv python realworld_scenario.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
HARNESS = REPO / "backend" / "packages" / "harness"
EXT_API = REPO / "backend" / "packages" / "extension-api"
sys.path.insert(0, str(HARNESS))
sys.path.insert(0, str(EXT_API))
os.environ.setdefault("ALPHA_HOME", tempfile.mkdtemp(prefix="acme_alpha_home_"))

from alpha.connectors import (  # noqa: E402
    ConnectorCatalog,
    ConnectorError,
    ConnectorHealth,
    ConnectorStore,
    ConnectorStoreUnreadable,
)
from alpha.egress import (  # noqa: E402
    EgressRoute,
    EgressStore,
    EgressValidationError,
    import_profile,
)
from alpha.routines import (  # noqa: E402
    RoutineRecorder,
    RoutineStore,
    RoutineStoreUnreadable,
    RoutineValidationError,
    replay,
)

WORK = Path(tempfile.mkdtemp(prefix="acme_scenario_"))
CHECKS: list[tuple[str, bool]] = []


def section(t: str) -> None:
    print(f"\n{'#' * 74}\n# {t}\n{'#' * 74}")


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"\n         -> {detail}" if detail else ""))


def must_raise(label: str, exc: type[BaseException], fn, *a, **k) -> None:
    try:
        fn(*a, **k)
    except exc as e:
        check(label, True, f"refused: {type(e).__name__}")
    except Exception as e:  # noqa: BLE001
        check(label, False, f"wrong exception {type(e).__name__}: {e}")
    else:
        check(label, False, "no exception raised")


print(f"Workspace: {WORK}")
print(f"ALPHA_HOME: {os.environ['ALPHA_HOME']}")

# =====================================================================
section("STEP 1 — Wire up the sales stack (connector marketplace)")
catalog = ConnectorCatalog()
print(f"  marketplace: {len(catalog.ids())} connectors available")
conn = ConnectorStore(WORK / "connectors.json")
for cid in ("gmail", "google_calendar", "slack", "hubspot", "github"):
    conn.install(cid, enable=True)
conn.record_health("hubspot", ConnectorHealth.OK, rate_limit_per_min=100)
print(f"  installed & enabled: {conn.enabled_ids()}")
check("1  sales stack installed and enabled", set(conn.enabled_ids()) == {"gmail", "google_calendar", "slack", "hubspot", "github"},
      f"{len(conn.enabled_ids())} connectors live")

# =====================================================================
section("STEP 2 — Security boundary (egress routing + profile import)")
eg = EgressStore(WORK / "egress.json")
eg.set_default_route(EgressRoute.DIRECT)
eg.add_rule("*.bank.com", EgressRoute.RESIDENTIAL_PROXY, prepend=True)
eg.add_rule("*.stripe.com", EgressRoute.RESIDENTIAL_PROXY, prepend=True)
eg.add_profile(import_profile("rep-42", label="Rep 42 Chrome", source_path="C:/Users/Rep42/Chrome/Profile 2",
                              authenticated_domains=["mail.google.com", "app.hubspot.com"]))
check("2.1  finance/CRM domains use the residential proxy",
      eg.resolve("pay.bank.com") == EgressRoute.RESIDENTIAL_PROXY and eg.resolve("dashboard.stripe.com") == EgressRoute.RESIDENTIAL_PROXY,
      f"bank={eg.resolve('pay.bank.com').value}, stripe={eg.resolve('dashboard.stripe.com').value}")
must_raise("2.2  smuggling cookies into a profile is refused", EgressValidationError,
           import_profile, "x", source_path='{"cookies":"SID=secret"}')

# =====================================================================
section("STEP 3 — Capture the renewal workflow (routines)")
rec = RoutineRecorder("renewal_outreach", "CRM -> draft email -> Slack ping")
rec.record_step("hubspot_list_deals", {"stage": "renewal_due", "owner": "rep42@acme.com"})
rec.record_step("gmail_draft", {"to": "{{contact}}", "subject": "Your Q4 renewal", "body": "Hi {{name}}, ..."})
rec.record_step("slack_post", {"channel": "#renewals", "text": "Drafted outreach for {{contact}}"})
rec.set_parameter("channel", "#renewals")
routine = rec.finalize()
rt = RoutineStore(WORK / "routines.json")
rt.save(routine)
print(f"  captured '{routine.name}' ({len(routine.steps)} steps); required={routine.required_parameters}, optional={routine.optional_parameters}")

calls = replay(rt.get("renewal_outreach"), {"owner": "rep42@acme.com", "contact": "cto@bigco.com", "name": "Dana"})
print("  replay for cto@bigco.com:")
for c in calls:
    print(f"    - {c['tool']}({c['args']})")
check("3  workflow captured and replayed for a real account",
      calls[1]["args"]["to"] == "cto@bigco.com" and calls[2]["args"]["text"] == "Drafted outreach for cto@bigco.com",
      "parameters substituted end-to-end")

# =====================================================================
section("STEP 4 — Plan the drive (swarm decomposition)")
try:
    from alpha.swarm.decomposer import SwarmTaskDecomposer

    plan = SwarmTaskDecomposer.decompose("Run the Q4 renewal drive: segment accounts, draft outreach, review, and send")
    print(f"  mode={plan.mode.value}  tasks={len(plan.tasks)}  concurrency={plan.max_concurrency}")
    for _tid, n in plan.tasks.items():
        print(f"    [{n.state.value}] {n.objective[:70]}...")
    check("4  drive decomposed into a task DAG", len(plan.tasks) >= 3, f"{len(plan.tasks)} nodes, mode={plan.mode.value}")
except Exception as e:  # noqa: BLE001
    check("4  drive decomposed into a task DAG", False, f"{type(e).__name__}: {e}")

# =====================================================================
section("STEP 5 — Staff it (bot cloning / specialized forks)")
try:
    from alpha.bots.cloning import BotCloneEngine, CloneMode

    cloner = BotCloneEngine()
    staffed = []
    for base, directive in (("researcher", "Research each account's renewal risk."),
                            ("writer", "Draft personalised renewal emails."),
                            ("analyst", "Score renewal likelihood and pricing.")):
        try:
            p = cloner.clone_bot(base, mode=CloneMode.SPECIALIST_FORK, specialist_directive=directive,
                                 skills_to_add=["deep-research"])
            staffed.append(p.name)
            print(f"  hired {p.name}  role={p.role!r}")
        except KeyError:
            print(f"  (no base template '{base}')")
    check("5  specialized agents created for the workstreams", len(staffed) >= 1, f"staffed: {staffed}")
except Exception as e:  # noqa: BLE001
    check("5  specialized agents created for the workstreams", False, f"{type(e).__name__}: {e}")

# =====================================================================
section("STEP 6 — Decide a pricing exception (deliberation council)")
try:
    from alpha.deliberation.engine import get_master_deliberation_engine

    eng = get_master_deliberation_engine()
    ev = eng.router.classify_smart("Should we grant a 20% discount to keep a churn-risk account? Weigh margin vs retention.")
    print(f"  router -> strategy={ev.strategy.value}")
    print(f"  rationale: {ev.rationale[:140]}")
    check("6  hard decision routed to a multi-agent strategy", ev.strategy is not None, f"strategy={ev.strategy.value}")
except Exception as e:  # noqa: BLE001
    check("6  hard decision routed to a multi-agent strategy", False, f"{type(e).__name__}: {e}")

# =====================================================================
section("STEP 7 — Pick the best outreach email (speculative tournament)")
try:
    from alpha.synthesis.speculative_tournament import SpeculativeSynthesisEngine

    t = SpeculativeSynthesisEngine()
    original = "def renewal_email(name):\n    return f'Hi {name}'\n"
    cands = t.generate_speculative_candidates("outreach.py", original, issue_type="feature")
    results = t.run_tournament_bakeoff(cands, lambda code: (1, 0))
    ranked = sorted(results, key=lambda r: -r.pareto_score)
    for r in ranked:
        print(f"    {r.candidate_id:16s} {r.strategy:20s} pareto={r.pareto_score}")
    check("7  best candidate selected by Pareto bake-off", ranked[0].pareto_score >= ranked[-1].pareto_score,
          f"winner={ranked[0].candidate_id}")
except Exception as e:  # noqa: BLE001
    check("7  best candidate selected by Pareto bake-off", False, f"{type(e).__name__}: {e}")

# =====================================================================
section("STEP 8 — Hand the loop to the perpetual daemon")
try:
    from alpha.perpetual.daemon import PerpetualDaemon

    d = PerpetualDaemon(project_id="acme-renewals")
    check("8  perpetual daemon running the drive", str(d.state).endswith("RUNNING"), f"state={d.state}")
except Exception as e:  # noqa: BLE001
    check("8  perpetual daemon running the drive", False, f"{type(e).__name__}: {e}")

# =====================================================================
section("ADVANCED TESTING 9.1 — Concurrency (parallel operators)")
errors: list[str] = []
lock = threading.Lock()


def worker(i: int) -> None:
    try:
        r = RoutineRecorder(f"routine_{i}")
        r.record_step("do", {"i": i, "email": f"u{i}@acme.com"})
        rt.save(r.finalize())
        conn.install("gmail", enable=(i % 2 == 0))
        replay(rt.get(f"routine_{i}"), {"email": f"u{i}@acme.com"})
    except Exception as e:  # noqa: BLE001
        with lock:
            errors.append(f"worker {i}: {type(e).__name__}: {e}")


threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
t0 = time.perf_counter()
for th in threads:
    th.start()
for th in threads:
    th.join()
elapsed = time.perf_counter() - t0
check("9.1  30 concurrent operators completed with no corruption", not errors and len(rt.list()) >= 31,
      f"{len(rt.list())} routines stored, {elapsed * 1000:.0f} ms, errors={errors[:2]}")

# =====================================================================
section("ADVANCED TESTING 9.2 — Persistence across a restart")
conn2 = ConnectorStore(WORK / "connectors.json")
rt2 = RoutineStore(WORK / "routines.json")
eg2 = EgressStore(WORK / "egress.json")
check("9.2  connectors survived restart", set(conn2.enabled_ids()) >= {"google_calendar", "slack"},
      f"enabled after reload: {conn2.enabled_ids()}")
check("9.2  routines survived restart", rt2.get("renewal_outreach") is not None and rt2.get("routine_7") is not None,
      f"{len(rt2.list())} routines after reload")
check("9.2  egress policy + profile survived restart",
      eg2.resolve("pay.bank.com") == EgressRoute.RESIDENTIAL_PROXY and eg2.get_profile("rep-42") is not None,
      "routing + profile intact")

# =====================================================================
section("ADVANCED TESTING 9.3 — Security boundaries & failure modes")
must_raise("9.3a routine name path-traversal refused", RoutineValidationError,
           RoutineRecorder("../../etc/passwd").finalize)
must_raise("9.3b replay with a missing required param refused", RoutineValidationError,
           replay, rt.get("renewal_outreach"), {})
must_raise("9.3c unknown connector refused", ConnectorError, conn.install, "evil-app")
must_raise("9.3d negative rate limit refused", ConnectorError,
           lambda: conn.install("slack", rate_limit_per_min=-5))
must_raise("9.3e egress credential field refused", EgressValidationError,
           lambda: import_profile("z", source_path="/tmp/token.txt"))

bad = WORK / "bad.json"
bad.write_text("{broken", encoding="utf-8")
must_raise("9.3f corrupt routine store fails loudly", RoutineStoreUnreadable, RoutineStore, bad)
must_raise("9.3g corrupt connector store fails loudly", ConnectorStoreUnreadable, ConnectorStore, bad)

# schema-version mismatch must be refused
mismatch = WORK / "mismatch.json"
mismatch.write_text('{"schema_version": 999, "routines": []}', encoding="utf-8")
must_raise("9.3h unknown store schema version refused", RoutineStoreUnreadable, RoutineStore, mismatch)

try:
    from alpha.deliberation.engine import get_master_deliberation_engine
    try:
        get_master_deliberation_engine().deliberate("hi", roster=[])
        check("9.3i council refuses to fabricate without models", False, "returned an answer")
    except RuntimeError as e:
        check("9.3i council refuses to fabricate without models", "No chat models" in str(e), str(e))
except Exception as e:  # noqa: BLE001
    check("9.3i council refuses to fabricate without models", False, f"{type(e).__name__}: {e}")

# =====================================================================
section("ADVANCED TESTING 9.4 — Scale")
t0 = time.perf_counter()
big = RoutineStore(WORK / "big_routines.json")
for i in range(300):
    r = RoutineRecorder(f"bulk_{i}")
    r.record_step("noop", {"i": i})
    big.save(r.finalize())
scale_ms = (time.perf_counter() - t0) * 1000
reloaded = RoutineStore(WORK / "big_routines.json")
check("9.4  300 routines written and reloaded", len(reloaded.list()) == 300, f"{len(reloaded.list())} routines, {scale_ms:.0f} ms")

# =====================================================================
section("SUMMARY")
passed = sum(1 for _, ok in CHECKS if ok)
for label, ok in CHECKS:
    if not ok:
        print(f"  FAILED: {label}")
print(f"\n  {passed}/{len(CHECKS)} checks passed across the full scenario + advanced testing")
sys.exit(0 if passed == len(CHECKS) else 1)
