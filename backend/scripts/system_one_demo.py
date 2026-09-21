"""Interactive demo: exercise System One (Jev) across a variety of scenarios.

Runs the real Vercel AI Gateway when the account can serve requests, and
otherwise falls back to the faithful local simulator so the whole variety matrix
is still demonstrable.

Usage:
    cd backend
    python scripts/system_one_demo.py              # live, auto-fallback to sim
    python scripts/system_one_demo.py --simulate   # force the simulator
    python scripts/system_one_demo.py --verbose    # show raw distributions
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND / "packages" / "harness"))
sys.path.insert(0, str(BACKEND / "tests"))

import httpx  # noqa: E402

from alpha.config.system_one_config import SystemOneConfig  # noqa: E402
from alpha.evaluation.system_one_calibration import load_records  # noqa: E402
from alpha.models.system_one import (  # noqa: E402
    BooleanQuestion,
    ChoiceQuestion,
    ScoreQuestion,
    SystemOneClient,
)

BAR = "=" * 74


def _fmt_probs(probs: dict[str, float], top: int = 4) -> str:
    items = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)[:top]
    return ", ".join(f"{k}={v:.3f}" for k, v in items)


async def run(client: SystemOneClient, label: str, verbose: bool) -> None:
    print(f"\n{BAR}\n{label}\n{BAR}")

    # ---- 1. Boolean across very different states -------------------------
    print("\n[1] BOOLEAN — same question, different states")
    print(f"    {'state':<58} P(yes)")
    for state in [
        "URGENT: payouts have been failing for 3 days, customers are furious",
        "Hi, just wondering how refunds work. No rush at all, thanks!",
        "The deploy is down and prod is broken, need this fixed ASAP",
        "Thanks, everything works perfectly now.",
    ]:
        r = await client.evaluate(state, {"q": BooleanQuestion("Does this convey urgency?")})
        p = r.answers["q"].boolean if r else None
        shown = state[:56] + ".." if len(state) > 58 else state
        print(f"    {shown:<58} {p if p is not None else 'n/a'}")

    # ---- 2. Choice / routing --------------------------------------------
    print("\n[2] CHOICE — ticket routing")
    criteria = {
        "billing": "Payments, invoicing, refunds, duplicate charges",
        "technical": "Bugs, outages, API errors, integrations",
        "sales": "Pricing, upgrades, new accounts, plans",
    }
    for state in [
        "I was charged twice for order A-104, please refund the duplicate.",
        "Our API integration returns 500 errors on every request.",
        "What is the price for 50 seats on the enterprise plan?",
    ]:
        r = await client.evaluate(state, {"dept": ChoiceQuestion("Which team should handle this?", criteria)})
        if not r:
            print(f"    {state[:56]:<58} n/a")
            continue
        a = r.answers["dept"]
        print(f"    {state[:56]:<58} -> {a.choice} (conf {a.confidence:.2f})")
        if verbose:
            print(f"        {_fmt_probs(a.probabilities)}")

    # ---- 3. Score / ordered rubric --------------------------------------
    print("\n[3] SCORE — customer frustration on an ordered rubric")
    levels = ["Calm and neutral", "Frustrated but civil", "Very angry, strong language"]
    for state in [
        "Thanks, that worked perfectly. Have a nice day!",
        "This is the third time I've had to ask. Please fix it.",
        "I am FURIOUS. This is completely unacceptable garbage!",
    ]:
        r = await client.evaluate(state, {"frust": ScoreQuestion("How frustrated is the customer?", levels)})
        if not r:
            print(f"    {state[:56]:<58} n/a")
            continue
        a = r.answers["frust"]
        print(f"    {state[:56]:<58} -> {a.score:.2f} (conf {a.confidence:.2f})")
        if verbose:
            print(f"        {_fmt_probs(a.probabilities)}")

    # ---- 4. Fan-out: many questions, one request -------------------------
    print("\n[4] FAN-OUT — 8 questions in a SINGLE request")
    state = "Our API integration started returning 500 errors 20 minutes ago; we cannot process any customer orders."
    questions = {
        "department": ChoiceQuestion("Which team should handle this?", criteria),
        "is_urgent": BooleanQuestion("Does this convey urgency or time-sensitivity?"),
        "is_outage": BooleanQuestion("Is this describing an active outage?"),
        "mentions_money": BooleanQuestion("Does this mention money, billing or payments?"),
        "frustration": ScoreQuestion("How frustrated does the customer appear?", levels),
        "severity": ScoreQuestion("How severe is the impact?", ["Cosmetic", "Degraded", "Blocking", "Total outage"]),
        "needs_human": BooleanQuestion("Does this need a human on call right now?"),
        "language": ChoiceQuestion("What language is this written in?", {"english": "English", "spanish": "Spanish", "french": "French"}),
    }
    import time

    t0 = time.perf_counter()
    r = await client.evaluate(state, questions)
    elapsed = (time.perf_counter() - t0) * 1000
    if r:
        print(f"    8 answers in one call — {elapsed:.0f}ms, {r.input_tokens} input tokens")
        for qid, a in r.answers.items():
            conf = f"conf {a.confidence:.2f}" if a.confidence is not None else "no conf field"
            val = a.value if a.type != "boolean" else f"P={a.value:.3f}"
            print(f"      {qid:<16} {a.type:<9} {str(val):<14} {conf}")

    # ---- 5. Structured state + path references ---------------------------
    print("\n[5] STRUCTURED STATE — object with dotted path references")
    structured = {
        "ticket": {"messages": [{"from": "customer", "text": "I was charged twice for order A-104. Please refund."}]},
        "order": {"id": "A-104", "charges": [{"amount_usd": 49, "status": "captured"}, {"amount_usd": 49, "status": "captured"}]},
        "refund_policy": "Duplicate charges are eligible for a refund.",
    }
    r = await client.evaluate(
        structured,
        {
            "refund_requested": BooleanQuestion("Does `ticket.messages[0].text` request a refund?"),
            "duplicate_charge": BooleanQuestion("Does `order.charges` contain the same amount twice?"),
            "policy_supports": BooleanQuestion("Does `refund_policy` support the requested refund?"),
        },
    )
    if r:
        for qid, a in r.answers.items():
            print(f"      {qid:<18} P={a.boolean:.3f}")

    # ---- 6. High-cardinality choice --------------------------------------
    print("\n[6] HIGH CARDINALITY — choice over 255 options")
    big = {f"opt_{i}": f"option number {i}" for i in range(255)}
    r = await client.evaluate("state", {"q": ChoiceQuestion("Which option?", big)})
    if r:
        a = r.answers["q"]
        total = sum(a.probabilities.values())
        print(f"      255 options returned, probabilities sum to {total:.6f} -> {a.choice}")

    # ---- 7. Unicode / multilingual ---------------------------------------
    print("\n[7] MULTILINGUAL state")
    for state in ["客户非常愤怒，付款失败三天了！😡", "Merci beaucoup, tout fonctionne. 🙏", "Запрос на возврат — срочно!"]:
        r = await client.evaluate(state, {"q": BooleanQuestion("Is the customer unhappy?")})
        p = r.answers["q"].boolean if r else None
        print(f"      {state[:34]:<36} P={p}")

    # ---- 8. The Alpha call sites ----------------------------------------
    print("\n[8] ALPHA CALL SITES — the modules built on top of the client")
    from alpha.browser.dom_snapshot import page_state_from_html
    from alpha.context.retention import retention_scores
    from alpha.epistemics.jev_belief import update_belief
    from alpha.memory.rerank import rerank
    from alpha.models.escalation import needs_flagship_model
    from alpha.subagents.jev_acceptance import evaluate_criteria
    from alpha.tools.selection import Candidate, rank_candidates
    from alpha.tools.trace_verify import TraceStep, verify_trace

    # Trace verification: does the tool trace support the answer?
    v = await verify_trace(
        "count the rows in users.csv",
        "there are 1,204 rows",
        [
            TraceStep(tool="read_file", step_id="r1", result="id,name\n1,a\n2,b"),
            TraceStep(tool="bash", step_id="r2", result="1204"),
        ],
    )
    print(f"      trace_verify       -> {v.trace_supported if v else 'no signal'} "
          f"(problems={v.problems if v else '-'})")

    # Escalation: does this need the big model?
    for prompt in ["capital of France", "prove the migration preserves referential integrity"]:
        print(f"      escalation         -> {await needs_flagship_model(prompt)!s:<5} {prompt[:44]}")

    # Selection: rank candidates for a request.
    ranking = await rank_candidates(
        "make a chart of quarterly revenue",
        [Candidate(id="data-analysis", title="data-analysis", summary="analyse tabular data"),
         Candidate(id="podcast", title="podcast", summary="produce audio episodes"),
         Candidate(id="deep-research", title="deep-research", summary="investigate a topic")],
    )
    print(f"      selection          -> {ranking.ids if ranking else 'no signal'}")

    # Reranking: reorder retrieved candidates.
    rr = await rerank("what is the refund window", ["shipping takes 3 days", "refunds within 30 days"])
    print(f"      rerank             -> {rr.order if rr else 'no signal'}")

    # Retention: what survives compaction.
    rs = await retention_scores("book flight BA249", ["ok thanks!", "confirmation AA-1234"])
    print(f"      retention          -> {rs.levels if rs else 'no signal'}")

    # Acceptance: does the result meet each criterion?
    acc = await evaluate_criteria("write the report", "wrote /out/report.md", ["report file exists", "tests pass"])
    print(f"      acceptance         -> {acc or 'no signal'}")

    # Belief: posterior from evidence.
    ub = await update_belief("the service is healthy", ["200 OK", "p95 latency 120ms"], prior=0.5)
    print(f"      belief             -> posterior {ub.posterior if ub else 'no signal'}")

    # Browser: index a page straight from HTML, no browser process needed.
    page = page_state_from_html(
        '<html><head><title>Checkout</title></head><body>'
        '<input placeholder="Email"><select id="cc"><option value="us">US</option></select>'
        '<button id="pay">Pay now</button></body></html>'
    )
    print(f"      browser (html)     -> {len(page['elements'])} elements indexed, "
          f"title={page['title']!r}")
    # ---- 9. Calibration ---------------------------------------------------
    print("\n[9] CALIBRATION — what a stated 0.9 has to be worth")
    from alpha.evaluation.system_one_calibration import (
        DecisionRecord,
        calibration_report,
        configure_recorder,
        reset_recorder,
    )

    # Two synthetic sites: one calibrated, one dangerously overconfident.
    demo = []
    for i in range(10):
        demo.append(DecisionRecord(ts=i, site="guardrail", tier="read", question_id="q", type="boolean",
                                   value=0.9, confidence=None, threshold=0.6, latency_ms=140, shadow=True,
                                   outcome=(i < 9)))
    for i in range(10):
        demo.append(DecisionRecord(ts=i, site="browser", tier="write", question_id="q", type="boolean",
                                   value=0.9, confidence=None, threshold=0.75, latency_ms=210, shadow=True,
                                   outcome=(i < 5)))
    print("      (synthetic: both sites state 0.9; guardrail is right 9/10, browser only 5/10)")
    print(f"        {'site':<12}{'n':>4}{'observed':>10}{'gap':>8}   verdict")
    for site in ("guardrail", "browser"):
        sub = calibration_report([r for r in demo if r.site == site])
        top = [b for b in sub.buckets if b.count][-1]
        verdict = "overconfident - do not trust yet" if (top.gap or 0) > 0.1 else "calibrated - safe to act"
        print(f"        {site:<12}{sub.total:>4}{top.observed:>10.2f}{top.gap:>+8.2f}   {verdict}")

    pooled = calibration_report(demo)
    print(f"        {'POOLED':<12}{pooled.total:>4}  <- averaging both hides the browser problem entirely")
    print("        -> gap = stated - observed; positive means wrong more often than claimed.")
    print("           This is why calibration is reported per site, not as one number.")

    # Shadow mode: measure without changing behaviour.
    shadow_cfg = SystemOneConfig(api_key="demo-key", shadow_mode=True)
    shadow_cli = SystemOneClient(shadow_cfg)
    shadow_cli._get_client = lambda: httpx.AsyncClient(  # noqa: SLF001
        transport=httpx.MockTransport(lambda req: httpx.Response(
            200, json={"model": "jev", "answers": {"q": {"type": "boolean", "boolean": 0.93}}}, request=req)),
        timeout=5.0,
    )
    shadow_log = Path(tempfile.mkdtemp()) / "shadow.jsonl"
    configure_recorder(shadow_log)
    try:
        out = await shadow_cli.evaluate({"x": 1}, {"q": BooleanQuestion("true?")}, site="guardrail")
        n = len(load_records(shadow_log)) if shadow_log.exists() else 0
        print(f"      shadow evaluate    -> returned {out!r} (None = caller keeps its existing path)")
        print(f"      shadow logged      -> {n} decision(s) recorded despite returning None")
    finally:
        reset_recorder()

    print("\n      'no signal' above is the fallback contract working: the simulator returns")
    print("      uninformative 0.5 booleans, which fall below every confidence floor, so")
    print("      each site correctly abstains instead of inventing a verdict.")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", action="store_true", help="force the local simulator")
    ap.add_argument("--verbose", action="store_true", help="show probability distributions")
    args = ap.parse_args()

    live = SystemOneClient(SystemOneConfig())
    if not args.simulate:
        print("Checking live System One endpoint...")
        probe = await live.evaluate("ping", {"q": BooleanQuestion("Is this a test?")})
        if probe is not None:
            print(f"LIVE: reachable (model={probe.model})")
            await run(live, "SYSTEM ONE VARIETY MATRIX — LIVE", args.verbose)
            return 0
        print("LIVE: unavailable (see warning above) — falling back to the simulator.\n")

    from test_system_one_variety import jev_client

    client, _ = jev_client()
    await run(client, "SYSTEM ONE VARIETY MATRIX — SIMULATED", args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
