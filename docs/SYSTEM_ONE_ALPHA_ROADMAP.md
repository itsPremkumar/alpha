# System One implementation roadmap for Alpha

Ordered **easiest → hardest**, weighted by usefulness. Difficulty is implementation
effort (S = under an hour, M = half a day, L = a day or more). Value is what Alpha
actually gains.

Every item follows the same contract: **System One first, existing path as fallback.**
`None` means "fall back", never `False`.

**Status: all 13 items implemented and wired into live call paths.** Tests across
five suites prove both halves of the contract for every site: the fallback path
(System One down → `None` → existing behaviour, byte-identical) and the success
path (System One reachable → verdict consumed).

A module nobody calls is not an implementation, so `tests/test_system_one_wiring.py`
exists specifically to cover the seams: the status contract, the delegation
ledger render, the epistemic engine's ratio substitution, the catalog merges, and
the research fetch screen — each with a "no signal changes nothing" test.

---

## Quick map

| # | Item | Where |
|---|---|---|
| 1 | Per-action confidence thresholds | `config/system_one_config.py` (`RiskTier`) |
| 2 | `Answer.validate` | `models/system_one.py` |
| 3 | Decomposed tool-call guardrail | `guardrails/jev.py` |
| 4 | Prompt-injection detection | `security/injection.py` |
| 5 | Citation → claim support | `agents/middlewares/citation_support.py` |
| 6 | Tool-trace verification | `tools/trace_verify.py` |
| 7 | Model routing / escalation | `models/escalation.py` → `models/task_router.py` |
| 8 | Skill & tool selection | `tools/selection.py` → `skills/catalog.py`, `tools/search/catalog.py` |
| 9 | Memory / RAG reranking | `memory/rerank.py` → `memory/session_search.py` |
| 10 | Compaction retention | `context/retention.py` → `context/micro_compaction.py` |
| 11 | Subagent acceptance | `subagents/jev_acceptance.py` → `subagents/acceptance_checks.py` |
| 12 | Browser element-index agent | `browser/element_table.py`, `jev_policy.py`, `dom_snapshot.py`, `executor.py`, `jev_agent.py` |
| 13 | Epistemic / evidence scoring | `epistemics/jev_belief.py` |

## Wiring — who actually calls each module

| Module | Production caller |
|---|---|
| `security/injection.py` | `research/engine.py` (quarantines fetched page content) · `browser/jev_agent.py` (stops before acting) |
| `tools/trace_verify.py` | `tools/builtins/task_tool.py` → status contract → `delegation_ledger.py` |
| `models/escalation.py` | `models/task_router.aroute_task` → `app/gateway/routers/bots.py` `/route-task` |
| `tools/selection.py` | `skills/catalog.py` (`describe_skill`) · `tools/builtins/tool_search.py` |
| `memory/rerank.py` | `memory/session_search.py` → `tools/builtins/code_agentic_core.py` `search_session_memory` |
| `context/retention.py` | `context/micro_compaction.py` (`apply_micro_compaction_smart`) |
| `subagents/jev_acceptance.py` | `subagents/acceptance_checks.afill_acceptance_gaps` → `task_tool.py` |
| `epistemics/jev_belief.py` | `epistemics/engine.py` `update_with_evidence` (derives the likelihood ratio) |
| `agents/middlewares/citation_support.py` | `research/engine.py` `_verify_findings` (each extracted finding checked against the page it came from) |
| `browser/*` | `tools/builtins/browser_supervisor_tool.py` (`table` / `next` / `run` / `parse`) |

Plus the six sites built before this list existed:
`skills/security_scanner.py`, `guardrails/jev.py`, `deliberation/router.py`,
`autoconfig/engine.py`, `memory/active_memory.py`, `runtime/goal.py`.

---

## 1. Per-action confidence thresholds — **done**

`min_confidence` is one global number (0.60), but TypeSafe's guidance is explicit:
**thresholds scale with risk**. Their voice-banking example uses 0.6 to show a
balance and >0.85 to approve a transfer.

```python
class RiskTier(StrEnum):
    READ = "read"            # 0.60 — worst case is a retry
    WRITE = "write"          # 0.75 — recoverable but visible
    DESTRUCTIVE = "destructive"  # 0.90 — irreversible
```

Unknown tiers degrade to the global floor rather than raising, so a typo in a new
call site can never disable safety.

## 2. `Answer.validate(allowed_ids)` — **done**

Ported from `browser-use/jev-ultrafast`. Jev cannot type-error, but *we* can
misread it: a truncated response, criteria that drifted from the request, or a
reported choice that is not the argmax. Cheap insurance on every choice answer.

## 3. Decompose the tool-call guardrail — **done**

`guardrails/jev.py` now asks five atomic questions and combines them in code:
`intent` (choice), `matches_request` / `within_scope` / `is_injection` (booleans),
`severity` (score). **Narrowing-only** — it may add denials, never grants.

## 4. Prompt-injection detection on fetched content — **done**

Four decomposed booleans weighted by severity: `addresses_agent` (0.20),
`attempts_override` (0.35), `requests_exfiltration` (0.30),
`asks_for_credentials` (0.15). Wired into the browser agent loop, which stops
*before acting* when risk ≥ 0.6.

## 5. Citation → claim support — **done**

`syntax` (does `[rN]` exist?) stays in `receipt_verification.py`, pure and
unchanged. The semantic layer — does the receipt actually evidence the sentence?
— lives in `citation_support.py`, with a `judge_batch` that scores every pair in
one request.

Wired into `research/engine.py`, where the natural pair exists: **an extracted
finding and the page it was extracted from.** Extraction there is line-shaped —
any sentence over 30 characters becomes a "key finding" — so a line sitting next
to the real claim is indistinguishable from the claim itself.

* `contradicted` → dropped. Carrying a claim the source refutes into a report is
  worse than carrying nothing.
* `unsupported` → kept but flagged, and the source loses confidence. A bad
  extraction line is not a false claim.
* no verdict → untouched.

## 6. Tool-trace verification — **done**

Six questions over the whole trace (`wrong_tool`, `args_mismatch`,
`unused_output`, `cross_step_disagree`, `conclusion_unsupported`, `severity`),
then a **second stage only if something fires** that names the responsible steps.
A clean trace costs exactly one request (there is a test for that).

Deterministic problems — error status, success with empty output — are reported
with or without System One.

## 7. Model routing / escalation — **done**

Three questions: `needs_reasoning`, `needs_precision`, `downgrade_risk` (rubric).
Ronacher's asymmetry drives the combination rule: **a wrong escalation costs
money, a wrong downgrade costs quality**, so missing signal, ambiguous booleans,
and mid-rubric risk all escalate. `ESCALATION_LADDER` only ever moves *up* —
there is a test asserting no category escalates down to `quick`.

## 8. Skill & tool selection — **done**

Two requests, because here two really are correct: a coarse choice over every
candidate using summaries (one call ranks up to 255), then a re-judge of the top
few with full text. Both wired call sites **merge** rather than replace — a
literal keyword/name hit always leads, System One only adds what the keyword
scorer missed. That is what makes this an upgrade that cannot regress.

## 9. Memory / RAG reranking — **done**

One score question per (query, candidate) pair, all on one request — viable only
because questions are evaluated in parallel and output tokens are free. Capped at
`max_rerank_candidates` (24). Unscored candidates keep their input position;
nothing is ever dropped. Wired into `SessionSearchEngine.asearch_discovery_smart`
on top of BM25.

## 10. Context compaction / retention — **done**

Four-level rubric (drop / compress / keep / keep verbatim), capped at 32 chunks.
Chunks without a score keep their existing treatment. `apply_micro_compaction_smart`
is byte-identical to the plain version when there is no signal — asserted in a test.

## 11. Subagent result acceptance — **done**

One boolean per undecidable criterion. **Narrowing-only by default**: a confident
"not met" fills the leaf, a confident "met" leaves it `UNVERIFIED`. `widen=True`
opts in once the false-negative rate has been measured. Decidable leaves are
never touched.

## 12. Browser element-index agent — **done**

The `jev-ultrafast` pattern, ported:

* `element_table.py` — indexed table, per-operation target heads (this is what
  solves the 255-option limit), composite `element:option` indices for dropdowns.
* `jev_policy.py` — operation + speculative per-operation target heads in **one**
  request. Single-candidate heads are decided without asking (a one-option choice
  is degenerate). Returns an index, never a selector, coordinate, or script.
* `dom_snapshot.py` — three adapters: Alpha's DOM summary, **raw HTML via the
  stdlib parser**, and an optional Playwright page. The HTML path means the whole
  fast path works on content Alpha already fetched, with no browser process.
* `executor.py` — `SupervisorExecutor`, `PlaywrightExecutor`, `ScriptedExecutor`.
* `jev_agent.py` — observe → screen for injection → decide → execute, with
  step budget, repeat detection, and A/B oscillation detection. Abstention is
  never an action: `status="no_signal"`, `fallback=True`, zero steps executed.

Tool surface (`browser_navigate_and_inspect`): `table`, `next`, `run`, `parse`.

## 13. Epistemic / evidence scoring — **done**

`epistemics/jev_belief.py` computes a posterior instead of asserting one, in
log-odds space where evidence is additive:

```
logit(posterior) = logit(prior) + Σ logit(P(evidenceᵢ supports claim)) − Σ logit(P(evidenceᵢ contradicts))
```

`logit(0.5) == 0`, so neutral evidence contributes nothing and independent
evidence accumulates. Probabilities are clamped off 0 and 1. **Advisory only —
a belief is not a verdict.**

---

## Rules that apply to every item

1. `None` means "fall back" — never `False`.
2. Never block a live event loop; sync sites use the `asyncio.get_running_loop()` guard.
3. Threshold per action, not globally.
4. System One may **narrow** (add denials/blocks) but never **widen** (grant permission).
5. Decomposition over broad questions — one property per question, combined in code.
6. Every new site ships with fallback tests *and* success tests
   (`tests/test_system_one_variety.py` is the pattern).
7. Wired selectors **merge**: the existing scorer's hits lead, System One only adds.

## What is still open

None of the code, one operational blocker: the supplied Vercel AI Gateway key
returns **403 `customer_verification_required`**. The key is valid, but the
account has no card on file, and free models still require one. Add a card and
every site above lights up. Until then each one is verified against a simulator
and degrades correctly — which is exactly what the fallback contract is for.

Once live, run each site in **shadow mode** first and confirm a stated 0.9 really
means ~90% right on Alpha's own data before letting it act.

## Item 14 — shadow mode and calibration

Not in the original list, but it is what makes the other 13 safe to switch on.
Every call site now carries a `site` label (`guardrail`, `browser`, `skill_select`,
…), and `evaluate()` records each decision — site, tier, stated probability,
latency — before returning.

* `shadow_mode: true` → record the decision, then return `None`. Because `None`
  is the existing "fall back" signal, **every site runs its current path while
  being measured**. Nothing can regress.
* `alpha/evaluation/system_one_calibration.py` → recording, plus reliability
  buckets, Brier score and expected calibration error.
* `backend/scripts/system_one_calibration.py` → read it.

See [`SYSTEM_ONE_CALIBRATION.md`](SYSTEM_ONE_CALIBRATION.md) for the rollout
order and how to read the numbers. The headline: a **positive gap**
(stated − observed) means the model is wrong more often than it claims, and that
is reported **per site**, because calibration does not transfer between them.
