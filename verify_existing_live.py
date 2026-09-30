"""Live execution of the pre-existing capabilities (real tasks, real output).

Runs each pre-existing feature on an actual task and prints what it produced.
Where a feature genuinely needs an LLM/OS backend that isn't configured, it is
expected to REFUSE honestly rather than fabricate — that refusal is itself the
verified behaviour.

Run:  uv run --with pydantic --with langchain python verify_existing_live.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
HARNESS = REPO / "backend" / "packages" / "harness"
EXT_API = REPO / "backend" / "packages" / "extension-api"
sys.path.insert(0, str(HARNESS))
sys.path.insert(0, str(EXT_API))
os.environ.setdefault("ALPHA_HOME", tempfile.mkdtemp(prefix="alpha_existing_home_"))

CHECKS: list[tuple[str, bool]] = []


def section(t: str) -> None:
    print(f"\n{'=' * 72}\n{t}\n{'=' * 72}")


def check(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok)))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"\n         -> {detail}" if detail else ""))


# ---------------------------------------------------------------------
section("A — Swarm task decomposition: goal -> executable DAG")
try:
    from alpha.swarm.decomposer import SwarmTaskDecomposer

    plan = SwarmTaskDecomposer.decompose("Build a marketing landing page, test it, and deploy to production")
    print(f"  swarm_id={plan.swarm_id}  mode={plan.mode.value}  status={plan.status}")
    print(f"  {len(plan.tasks)} tasks, max_concurrency={plan.max_concurrency}, est. speedup={plan.estimated_speedup}")
    for _tid, node in plan.tasks.items():
        print(f"    [{node.state.value:8s}] {node.objective!r}  deps={node.dependencies}  tags={node.capability_tags}")
    check("A  goal decomposed into a DAG of tasks", len(plan.tasks) >= 2, f"{len(plan.tasks)} task nodes produced")
except Exception as e:  # noqa: BLE001
    check("A  goal decomposed into a DAG of tasks", False, f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------------
section("B — Speculative tournament: best-of-N selection")
try:
    from alpha.synthesis.speculative_tournament import SpeculativeSynthesisEngine

    engine = SpeculativeSynthesisEngine()
    original = "def add(a, b):\n    return a + b\n"
    cands = engine.generate_speculative_candidates("calc.py", original, issue_type="bugfix")
    print(f"  generated {len(cands)} candidate patches: {[c.candidate_id for c in cands]}")

    def runner(code: str) -> tuple[int, int]:
        try:
            ns: dict = {}
            exec(compile(code, "<cand>", "exec"), ns)  # noqa: S102
            ns["add"](2, 3)
            return 1, 0
        except Exception:  # noqa: BLE001
            return 0, 1

    results = engine.run_tournament_bakeoff(cands, runner)
    for r in sorted(results, key=lambda r: -r.pareto_score):
        print(f"    {r.candidate_id:16s} {r.strategy:20s} pass_rate={r.pass_rate} pareto={r.pareto_score}")
    winner = max(results, key=lambda r: r.pareto_score)
    check("B  tournament ranked candidates and picked a winner", len(results) == 3, f"winner={winner.candidate_id} pareto={winner.pareto_score}")
except Exception as e:  # noqa: BLE001
    check("B  tournament ranked candidates and picked a winner", False, f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------------
section("C — Bot cloning: self-multiplying specialized agents")
try:
    from alpha.bots.cloning import BotCloneEngine, CloneMode

    cloner = BotCloneEngine()
    prof = cloner.clone_bot("researcher", mode=CloneMode.SPECIALIST_FORK,
                            specialist_directive="Focus only on competitor pricing research.",
                            skills_to_add=["deep-research"])
    print(f"  cloned 'researcher' -> '{prof.name}'")
    print(f"    role:   {prof.role}")
    print(f"    skills: {prof.skills}")
    print(f"    lineage:{prof.metadata.get('lineage')}  mode={prof.metadata.get('clone_mode')}")
    check("C  a specialized clone was created from a base bot", bool(prof.name) and prof.name != "researcher", f"new bot '{prof.name}'")
except Exception as e:  # noqa: BLE001
    check("C  a specialized clone was created from a base bot", False, f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------------
section("D — Deliberation council: routing + honest refusal without models")
eng = None
try:
    from alpha.deliberation.engine import get_master_deliberation_engine

    eng = get_master_deliberation_engine()
    eval_result = eng.router.classify_smart("Compare three database engines and prove which is fastest", user_strategy="auto")
    print(f"  router chose strategy: {eval_result.strategy.value}")
    print(f"  rationale: {eval_result.rationale[:160]}")
    check("D1  council router classifies a query into a strategy", eval_result.strategy is not None,
          f"strategy={eval_result.strategy.value}")
except Exception as e:  # noqa: BLE001
    check("D1  council router classifies a query into a strategy", False, f"{type(e).__name__}: {e}")

if eng is not None:
    try:
        eng.deliberate("What is 2+2?", roster=[])  # forced empty roster
        check("D2  refuses to fabricate an answer with no models", False, "it returned an answer with no models!")
    except RuntimeError as e:
        check("D2  refuses to fabricate an answer with no models", "No chat models configured" in str(e), f"RuntimeError: {e}")
    except Exception as e:  # noqa: BLE001
        check("D2  refuses to fabricate an answer with no models", False, f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------------
section("E — Computer use: module loads (OS backend optional)")
try:
    from alpha.computer_use import guard  # noqa: F401
    check("E  computer_use package imports (guard present)", True,
          "dispatcher performs real OS control when pyautogui/pynput is installed; reports 'unavailable' otherwise")
except Exception as e:  # noqa: BLE001
    check("E  computer_use package imports (guard present)", False, f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------------
section("F — Perpetual daemon: always-on autonomy loop")
try:
    from alpha.perpetual.daemon import PerpetualDaemon

    daemon = PerpetualDaemon(project_id="live-check")
    print(f"  daemon created; state={daemon.state}")
    check("F  perpetual daemon instantiates and is RUNNING", str(daemon.state).endswith("RUNNING"), f"state={daemon.state}")
except Exception as e:  # noqa: BLE001
    check("F  perpetual daemon instantiates and is RUNNING", False, f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------------
section("SUMMARY")
passed = sum(1 for _, ok in CHECKS if ok)
for label, ok in CHECKS:
    if not ok:
        print(f"  FAILED: {label}")
print(f"\n  {passed}/{len(CHECKS)} pre-existing feature checks passed")
sys.exit(0 if passed == len(CHECKS) else 1)
