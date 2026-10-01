"""Live, end-to-end verification of every implemented feature.

This is NOT a unit test. It plays a realistic scenario — onboarding a new sales
rep and automating their morning briefing — and exercises each new package as a
user would, printing the concrete data produced at each step.

Scenario: "Priya" joins the sales team.
  1. She installs the apps she needs from the connector marketplace.
  2. She sets egress routing so finance sites use a residential proxy, and
     imports her authenticated browser profile.
  3. She demonstrates her morning-briefing workflow once; Alpha captures it as a
     reusable, parameterised routine and replays it for a specific day.

Section 4 structurally verifies the pre-existing capabilities (council/swarm/
cloning/computer-use/perpetual/tournament) that were found during the audit,
since executing those needs the full LangGraph dependency stack.

Run:  python verify_features_live.py
Exit: 0 if every check passed, 1 otherwise.
"""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
HARNESS = REPO / "backend" / "packages" / "harness"
sys.path.insert(0, str(HARNESS))

from alpha.routines import (  # noqa: E402
    RoutineRecorder,
    RoutineStore,
    RoutineStoreUnreadable,
    RoutineValidationError,
    looks_like_input,
    replay,
)
from alpha.connectors import (  # noqa: E402
    ConnectorCatalog,
    ConnectorError,
    ConnectorHealth,
    ConnectorStore,
    ConnectorStoreUnreadable,
)
from alpha.egress import (  # noqa: E402
    EgressPolicy,
    EgressRoute,
    EgressStore,
    EgressValidationError,
    import_profile,
)

CHECKS: list[tuple[str, bool]] = []


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok)))
    mark = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {label}"
    if detail:
        line += f"\n         -> {detail}"
    print(line)


def must_raise(label: str, exc: type[BaseException], fn, *a, **k) -> None:
    try:
        fn(*a, **k)
    except exc as e:
        check(label, True, f"correctly refused: {type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        check(label, False, f"wrong exception {type(e).__name__}: {e}")
    else:
        check(label, False, "no exception raised")


workdir = Path(tempfile.mkdtemp(prefix="alpha_live_"))
print(f"Live workspace: {workdir}")

# =====================================================================
# FEATURE 1 — Connector marketplace
# =====================================================================
section("FEATURE 1 — Connector marketplace (alpha.connectors)")

catalog = ConnectorCatalog()
print(f"  Marketplace has {len(catalog.ids())} connectors: {', '.join(catalog.ids())}")

hits = catalog.search("google")
check("1.1  search 'google' finds the Google apps", {c.id for c in hits} == {"gmail", "google_calendar"},
      f"results: {[c.name for c in hits]}")

store = ConnectorStore(workdir / "connectors.json")
for cid in ("gmail", "google_calendar", "slack"):
    state = store.install(cid, enable=True)
    print(f"         installed {state.connector_id}: enabled={state.enabled}")

check("1.2  three connectors installed & enabled", set(store.enabled_ids()) == {"gmail", "google_calendar", "slack"},
      f"enabled: {store.enabled_ids()}")

h = store.record_health("slack", ConnectorHealth.DEGRADED, error="rate limited by upstream", rate_limit_per_min=20)
check("1.3  health recorded for slack", h.health == ConnectorHealth.DEGRADED and h.rate_limit_per_min == 20,
      f"health={h.health.value}, rate_limit={h.rate_limit_per_min}/min, error={h.last_error!r}")

store.set_enabled("gmail", False)
check("1.4  gmail disabled", "gmail" not in store.enabled_ids(), f"enabled now: {store.enabled_ids()}")

reloaded = ConnectorStore(workdir / "connectors.json")
ok = (reloaded.get("gmail") is not None and reloaded.get("gmail").enabled is False
      and reloaded.get("slack").health == ConnectorHealth.DEGRADED)
check("1.5  state survives a restart (reloaded from disk)", ok,
      f"gmail.enabled={reloaded.get('gmail').enabled}, slack.health={reloaded.get('slack').health.value}")

must_raise("1.6  unknown connector is refused", ConnectorError, reloaded.install, "myspace")

bad = workdir / "connectors_bad.json"
bad.write_text("not json", encoding="utf-8")
must_raise("1.7  corrupt store fails loudly (no silent reset)", ConnectorStoreUnreadable, ConnectorStore, bad)

# =====================================================================
# FEATURE 2 — Egress routing + browser-profile import
# =====================================================================
section("FEATURE 2 — Egress routing + browser-profile import (alpha.egress)")

egress = EgressStore(workdir / "egress.json")
egress.set_default_route(EgressRoute.DIRECT)
egress.add_rule("*.bank.com", EgressRoute.RESIDENTIAL_PROXY, prepend=True)
egress.add_rule("*.chase.com", EgressRoute.RESIDENTIAL_PROXY, prepend=True)

check("2.1  finance domains route via residential proxy",
      egress.resolve("secure.bank.com") == EgressRoute.RESIDENTIAL_PROXY
      and egress.resolve("login.chase.com") == EgressRoute.RESIDENTIAL_PROXY,
      f"bank.com -> {egress.resolve('secure.bank.com').value}, chase.com -> {egress.resolve('login.chase.com').value}")

check("2.2  ordinary domains stay direct", egress.resolve("news.ycombinator.com") == EgressRoute.DIRECT,
      f"news.ycombinator.com -> {egress.resolve('news.ycombinator.com').value}")

profile = import_profile("priya-work", label="Priya Work Chrome",
                         source_path="C:/Users/Priya/Chrome/Profile 1",
                         authenticated_domains=["mail.google.com", "calendar.google.com"])
egress.add_profile(profile)
check("2.3  authenticated browser profile imported (reference only)", profile.profile_id == "priya-work",
      f"domains={profile.authenticated_domains}, path={profile.source_path}")

must_raise("2.4  importing a raw credential blob is refused", EgressValidationError,
           import_profile, "leak", source_path='{"cookies": "SID=secret"}')
must_raise("2.5  a cookie/token field is refused outright", EgressValidationError,
           type(profile).from_dict, {"profile_id": "p", "cookies": "SID=secret"})

egress2 = EgressStore(workdir / "egress.json")
check("2.6  policy + profile survive a restart",
      egress2.resolve("secure.bank.com") == EgressRoute.RESIDENTIAL_PROXY and egress2.get_profile("priya-work") is not None,
      f"profile count after reload: {len(egress2.list_profiles())}")

# =====================================================================
# FEATURE 3 — Routine capture + replay
# =====================================================================
section("FEATURE 3 — Demonstration-captured routines (alpha.routines)")

rec = RoutineRecorder("morning_briefing", "Email digest + calendar + Slack post")
rec.record_step("gmail_search", {"query": "is:unread newer_than:1d", "to": "priya@acme.com"})
rec.record_step("calendar_list", {"date": "2026-09-30", "calendar": "primary"})
rec.record_step("slack_post", {"channel": "{{channel}}", "text": "Briefing for {{date}} ready"})
rec.set_parameter("channel", "#sales")          # optional, has a default
routine = rec.finalize()

check("3.1  email address auto-detected as a parameter", "to" in routine.required_parameters,
      f"required params: {routine.required_parameters}, optional: {routine.optional_parameters}")

routines = RoutineStore(workdir / "routines.json")
routines.save(routine)
print(f"         saved routine '{routine.name}' with {len(routine.steps)} steps")

routines2 = RoutineStore(workdir / "routines.json")
loaded = routines2.get("morning_briefing")
check("3.2  routine survives a restart", loaded is not None and len(loaded.steps) == 3,
      f"steps after reload: {[s.tool for s in loaded.steps]}")

calls = replay(loaded, {"to": "rep42@acme.com", "date": "2026-10-01"})
print("         replayed workflow for rep42@acme.com on 2026-10-01:")
for c in calls:
    print(f"           - {c['tool']}({c['args']})")
check("3.3  replay substitutes parameters correctly",
      calls[0]["args"]["to"] == "rep42@acme.com"
      and calls[2]["args"]["text"] == "Briefing for 2026-10-01 ready"
      and calls[2]["args"]["channel"] == "#sales",
      "recipient + date substituted, channel fell back to its default")

must_raise("3.4  replay without a required parameter is refused", RoutineValidationError, replay, loaded, {})

check("3.5  'looks_like_input' flags user-specific values",
      looks_like_input("priya@acme.com") and looks_like_input("C:/tmp/x") and not looks_like_input("hello"))

# =====================================================================
# FEATURE 4 — Structural verification of pre-existing capabilities
# =====================================================================
section("FEATURE 4 — Pre-existing capabilities (structural check)")

existing = {
    "Reasoning council / debate": "deliberation/engine.py",
    "Task decomposition (swarm)": "swarm/decomposer.py",
    "Self-multiplying agents": "bots/cloning.py",
    "Agent computer": "computer_use/dispatcher.py",
    "Perpetual autonomy": "perpetual/daemon.py",
    "Best-of-N tournament": "synthesis/speculative_tournament.py",
    "Arena / leaderboard": "benchmarks/arena.py",
    "Tree-search reasoning": "reasoning/introspective_tree_search.py",
}
for label, rel in existing.items():
    path = HARNESS / "alpha" / rel
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
        funcs = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        public = [c for c in classes if not c.startswith("_")] + [f for f in funcs if not f.startswith("_")]
        check(f"4.x  {label}: real implementation present",
              len(public) >= 1 and len(src.splitlines()) > 30,
              f"{rel} — {len(src.splitlines())} lines, {len(classes)} classes, {len(funcs)} functions")
    except FileNotFoundError:
        check(f"4.x  {label}: file present", False, f"MISSING {rel}")

# Honest note: executing these needs the full dependency stack.
try:
    import alpha.deliberation.engine  # noqa: F401
    print("  [INFO] alpha.deliberation.engine imported successfully.")
except Exception as e:  # noqa: BLE001
    print(f"  [INFO] Cannot execute pre-existing packages here: {type(e).__name__}: {e}")
    print("         (needs the project dependency stack — `make install`; verified structurally above.)")

# =====================================================================
# SUMMARY
# =====================================================================
section("SUMMARY")
passed = sum(1 for _, ok in CHECKS if ok)
total = len(CHECKS)
for label, ok in CHECKS:
    if not ok:
        print(f"  FAILED: {label}")
print(f"\n  {passed}/{total} live checks passed")
sys.exit(0 if passed == total else 1)
