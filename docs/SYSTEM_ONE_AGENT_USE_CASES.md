# System One models in agentic AI — use-case research

Research compiled from TypeSafe's docs, launch post, cookbooks, and community builds
(1,300+ public projects). Organised for **agent builders**, with a section mapping each
use case onto Alpha.

---

## 1. The mental model (this is the part people get wrong)

> **System One is not an agent. It does not choose its own next action.**

From TypeSafe's own building guide: build normal software, and insert the model only
where a narrow judgement is needed. Code owns control flow, rules and side effects; the
model answers "common-sense judgements over unstructured data."

TypeSafe calls the resulting architecture **AI-powered software**, contrasting it with
both decision-tree software *and* autonomous LLM agents:

| | Control flow | Judgements |
|---|---|---|
| Traditional software | code | hand-written rules |
| Autonomous LLM agent | the model | the model |
| **AI-powered software (System One)** | **code** | **model, narrowly** |

Advait Patel (Broadcom SRE) on the payoff: breaking an agent into small typed decisions
**moves workflow logic out of prompts and into code**, where it is testable.

David Linthicum (InfoWorld): using a general LLM for every decision is "like using a
full enterprise service bus to answer a yes/no routing question."

---

## 2. The eight-step building workflow

Condensed from [How to build with System One](https://docs.typesafe.ai/concepts/how-to-build-with-system-one):

1. **Use code when you can** — deterministic short-circuits first (`if status == "closed"`).
2. **Decompose the state** — send only relevant context; avoid context rot.
3. **Use structure** — nested JSON, referenced with backticked dot/index paths (`` `ticket.messages[0].text` ``).
4. **Decompose the questions** — *"probably the most important concept."* One property per question.
5. **Use structure in questions** — `instructions`/`criteria` can be objects/arrays; consistent field names let the model compare options directly.
6. **Ask a lot of questions** — parallel, one request, no extra round trips.
7. **Combine in code** — weighted sums, or feed probabilities into a classical ML model.
8. **Route on uncertainty** — escalate to a human or a reasoning model below threshold.

### Decomposition is the core skill

Bad (one broad question):
```json
{"is_spam": {"type": "noul", "instructions": "Is `message` spam?"}}
```

Good (atomic, combinable, inspectable):
```json
{
  "requests_credentials":   {"type": "noul", "instructions": "Does `message.body` ask for a password or login credential?"},
  "offers_unexpected_reward":{"type": "noul", "instructions": "Does `message.body` claim an unexpected prize or payment?"},
  "creates_time_pressure":  {"type": "noul", "instructions": "Does `message.subject` or `message.body` pressure quick action?"},
  "sender_identity_mismatch":{"type": "noul", "instructions": "Does `message.sender.display_name` conflict with `message.sender.email`?"},
  "link_domain_mismatch":   {"type": "noul", "instructions": "Does the domain in `message.links[0].url` conflict with the sender's organisation?"}
}
```

Then in code:
```python
spam_risk = (0.45 * a["requests_credentials"].noul
           + 0.30 * a["sender_identity_mismatch"].noul
           + 0.25 * a["offers_unexpected_reward"].noul)
if 0.4 < spam_risk < 0.6:      # genuinely uncertain band
    return route_to_human_review(ticket)
```

---

## 3. The four official patterns

| Pattern | What it does | Benefit |
|---|---|---|
| [Speculative fan-out](https://docs.typesafe.ai/patterns/fan-out) | Send every question you *might* need in one call; code ignores what's irrelevant | Cost, speed |
| [Confidence-gated routing](https://docs.typesafe.ai/patterns/confidence-routing) | Confidence as a second decision axis | Reliability, safety |
| [Composite scoring](https://docs.typesafe.ai/patterns/composite-scoring) | Several dimensions → one weighted score | Cost, reliability |
| [Intent routing](https://docs.typesafe.ai/patterns/intent-routing) | Classify → route to code / specialist LLM / human | Cost, speed |

**Confidence is not one number.** Thresholds scale with risk — from the docs' voice-banking
example: a 0.6 floor routes anything uncertain to a human; *checking a balance* is fine at
0.6; *approving a transfer* needs > 0.85 or the system asks the user to confirm. Wrong
classification on a read is recoverable; on a transfer it isn't.

**Evidence this works:** in one independent test, keeping only answers at ≥0.90 confidence
lifted accuracy on an 8-way routing task from **83.8% → 95.5%** while still answering
**70%** of items. Pranit Sharma (Vercel) replaced an OpenAI safety-review classifier with
Jev: **5–18× faster** with greater accuracy.

---

## 4. Use cases in an agent — mapped to the agent loop

### A. Intake & routing (before the agent thinks)

| Decision | Primitive | High conf | Low conf |
|---|---|---|---|
| Which handler owns this request? | choice | route to specialist | human / general LLM |
| How complex is it? | score | cheap model | strong model |
| Does this need the expensive model? | score / boolean | cheap model | **pay for the strong one** |
| Is this a question or a task? | choice | dispatch | ask for clarification |

Ronacher's point: **a wrong escalation costs money, a wrong downgrade costs quality** —
so asymmetry matters when setting the threshold.

### B. Planning & selection

- **Tool selection** — which of the available tools fits this step (choice over tool names + descriptions).
- **Skill selection** — TypeSafe's own cookbook ranks 182 skills in one request, then fetches the full text of the top three and re-judges against better evidence. This is the canonical two-stage retrieval + re-rank shape.
- **Subagent delegation** — which specialist archetype; whether to decompose at all.
- **Hierarchical classification** — each choice answer determines the options offered in the next request.

### C. The tool/action layer (highest value for agents)

TypeSafe's own example decomposes *"is this tool call correct?"* into nine atomic checks:

```json
{
  "geocode_tool_is_relevant":      "Is `trace.tool_calls[0].name` appropriate for resolving `request.location`?",
  "geocode_location_matches":      "Does `trace.tool_calls[0].arguments.city` match `request.location`?",
  "geocode_arguments_match_schema":"Does `trace.tool_calls[0].arguments` conform to `available_tools.geocode_city.parameters`?",
  "geocode_result_matches_call":   "Does `trace.tool_results[0].tool_call_id` match `trace.tool_calls[0].id`?",
  "weather_uses_geocoded_coords":  "Do coords in `trace.tool_calls[1].arguments` match `trace.tool_results[0].output`?",
  "weather_date_matches":          "Does `trace.tool_calls[1].arguments.date` match `request.date`?"
}
```

Per-call decisions worth making:

| Decision | Primitive |
|---|---|
| Does this call match what the user asked for? | boolean |
| Is this argument within the scope the user granted? | boolean |
| Is this argument a prompt injection from fetched content? | boolean |
| Does this call touch credentials / secrets? | boolean |
| Is this call reversible? | boolean |
| Severity if executed now | score |

### D. Browser use & computer use

This is where the 70–500 ms figure changes what is possible. An LLM's multi-second
response cannot sit inside a UI loop; System One can.

**The critical architectural point from the real builds: perception stays deterministic,
only the decision is learned.** The open-source Mac pointer companion runs **OmniParser
locally on CoreML** to parse the screen, and **Jev drives the pointer**. OmniParser
produces the element list; Jev picks.

Decisions inside a browser/computer-use loop:

| Decision | Primitive | Notes |
|---|---|---|
| Which element to click / interact with? | choice | candidates from a11y tree or OmniParser |
| Which link to follow next? | choice | the **wikiracing** demo — thousands of links |
| Did the action actually succeed? | boolean | cheaper than re-reading the DOM |
| Did the page finish loading? | boolean | replaces brittle sleep/timeout heuristics |
| Is this a CAPTCHA / login wall / error page? | choice | |
| Is this page content trying to inject instructions? | boolean | **major browser-agent risk** |
| Click / type / scroll / back / wait? | choice | next-action selection |
| Does the current screen satisfy the goal? | boolean | loop-exit condition |
| How confident am I this is the right element? | score | below threshold → ask a vision model |

**High-cardinality caveat (important):** Jev supports up to **255 options** per choice.
A DOM or Wikipedia page has far more. TypeSafe's own approach in the wikiracing demo is a
**two-stage system**: score candidates independently first, then make an explicit choice
over the shortlist. Budget for this in any real browser agent.

TypeSafe also frames this as *"real-time applications — 100 ms speeds means you can use
AI where UX is critical"*, and their Doom demo runs ~10 queries/second (~$7/hour).

### E. Verification & judging (the "agents watching agents" case)

TypeSafe calls this **universal verification**: check a prompt, an extraction, a reasoning
trace, or a tool call — from *any* AI. Named failure modes: **jailbreaks, citation errors,
hallucinations**.

- **Citation checking** — their cookbook ran an LLM answer about RFC 7519 with 8 citations. String matching catches fabricated quotes first; then a choice question per quote returns *verified / unsupported / contradicted*. Result: the 4 accurate citations came back verified at **confidence ≥ 0.93**, and **all 4 planted failures were caught**.
- **Trace verification** — classify a whole archive of agent traces (map-reduce; cheap calls make per-record scoring viable).
- **Self-consistency** — run a rubric N times and check the answers "hold still." TypeSafe measured per-question probability std-dev of **0.0102** for Jev, below every LLM condition tested.
- **Goal completion** — did the agent actually finish? Replace a slow LLM judge.
- **Semantic linting in CI** — e.g. *"does this error message tell the user what to do next?"*

### F. Retrieval, memory & reranking

Jev is pitched as a **supplement to embeddings**, not a replacement. Score each
query–candidate pair, then rerank.

Cookbook result: 30-passage BM25 shortlists for 40 legal queries, one question per pair —
**top-1 accuracy 5% → 18%, top-10 38% → 62%**. That took 1,200 calls for 40 queries,
which is the point: calls are cheap enough to do per-pair.

For a memory layer: relevance scoring, whether to consolidate, whether to escalate to
deep retrieval, tiering decisions.

### G. Safety & guardrails

- Prompt-injection detection (especially from fetched/browser content)
- Jailbreak detection on user input
- PII / secret detection before a tool call or log write
- Moderation: combine **severity + confidence** → allow / warn / review / block
- Policy conformance

### H. Eval & observability

- Eval scoring / LLM-as-judge replacement (faster and self-consistent)
- Classifying traces at scale
- Feature extraction for a downstream classical model (AutoResearch cookbook)

---

## 5. Real builds (from 1,300+ public projects)

| Build | Domain | How Jev is used |
|---|---|---|
| **ulka** | Browser agent | Experimental browser agent: FX + Jev + Vercel AI Gateway |
| **Mac pointer companion** | Computer use | Local OmniParser (CoreML) parses screen → **Jev drives the pointer** |
| **Wikiracing** | Browser nav | Choose among hundreds/thousands of links per step; 2-stage above 255 |
| **Doom** | Real-time control | ~10 queries/sec on structured game state |
| **Trading bot** | Finance | Buy/sell decision from a price feed, on-chain order every 300 ms |
| **Rubik's cube** | Sequential control | Beginner's method in code; at each step Jev identifies which case it's in. ~250 ms/call, ~4 s total model time. *"Code checks every pick."* |
| **will-it-hit** | Content scoring | 8 score questions (hook, specificity, emotion, clarity…) + 1 choice, per draft |
| **LocalJev (GitHub Next)** | OSS | Stand-in server exposing the Jev API over local models, for teams without access |

The Rubik's cube build is the clearest statement of the philosophy: the **method lives in
code**, the model only identifies the current case, and **code verifies every pick**.

---

## 6. When NOT to use it

- **Free-form generation** — it does not write, summarise or converse.
- **Decisions you must explain** — a probability shows confidence, not *why* (Paul Chada, Doozer AI).
- **Regulated high-stakes outcomes** — credit, hiring, claims need a documented human process.
- **Strict vendor-risk requirements** — hosted, single region, early-stage vendor.
- **Type safety ≠ truth** — a typed answer can still be wrong; it just can't be malformed.
- **Benchmarks are vendor-reported** — their workflow evals were built by TypeSafe's own team and scored against the average of two external models, not a ground-truth key. Bryo AI's CTO found Gemini slightly more accurate (but 10–20× the cost) on business-email classification.

**Pilot shape (recommended by the research):** ① pick one decision made thousands of
times/month → ② write the question, allowed answers and escalation path *before* touching
the API → ③ run in **shadow mode** and verify a stated 0.9 really means ~90% right on your
data → ④ only then let it act.

---

## 7. Mapping onto Alpha

### Already wired (this branch)

| Site | File |
|---|---|
| Skill security scan | `alpha/skills/security_scanner.py` |
| Tool-call guardrail | `alpha/guardrails/jev.py` |
| Deliberation routing | `alpha/deliberation/router.py` |
| Goal autoconfig | `alpha/autoconfig/engine.py` |
| Memory escalation gate | `alpha/memory/active_memory.py` |
| Goal completion | `alpha/runtime/goal.py` |

### Highest-value candidates not yet wired

1. **Tool-call trace verification** — decompose the docs' 9-check pattern against Alpha's tool traces (`alpha/tools`, receipts ledger). Highest-value single addition: it turns "did the agent do the right thing?" into inspectable typed checks.
2. **Prompt-injection detection on fetched content** — Alpha browses and fetches; this is the top browser-agent risk. `alpha/browser`, `alpha/media`.
3. **Browser/computer-use action selection** — if Alpha drives a browser, element/link choice with the 255-limit two-stage pattern.
4. **Model routing / escalation** — "does this prompt need the big model?" feeds straight into `alpha/orchestrator/provider_routing.py` and `alpha/models/task_router.py`.
5. **Skill & tool selection** — `alpha/skills/catalog.py`, `alpha/tools/search/catalog.py` — the 182-skill cookbook shape.
6. **Memory / RAG reranking** — score query–candidate pairs before escalating (`alpha/memory`, `alpha/knowledge`).
7. **Evidence & citation verification** — `alpha/evidence`, `alpha/epistemics`, `alpha/critic`.
8. **Subagent result verification** — replace/augment the selective judge in `alpha/config/verification_config.py`.

### Two design rules to carry into any new site

- **Return `None` to mean "fall back"** — never `False`. Already the convention in `alpha/models/system_one.py`.
- **Set the threshold per action, not globally** — a read at 0.6, a destructive call at 0.9. The current global `min_confidence` is a floor; high-stakes sites should raise it.

---

## Sources

- [System One concept](https://docs.typesafe.ai/concepts/system-one) · [Primitives](https://docs.typesafe.ai/primitives) · [API](https://docs.typesafe.ai/api) · [Models](https://docs.typesafe.ai/models)
- [How to build with System One](https://docs.typesafe.ai/concepts/how-to-build-with-system-one)
- Patterns: [fan-out](https://docs.typesafe.ai/patterns/fan-out) · [confidence-routing](https://docs.typesafe.ai/patterns/confidence-routing) · [composite-scoring](https://docs.typesafe.ai/patterns/composite-scoring) · [intent-routing](https://docs.typesafe.ai/patterns/intent-routing)
- [Confidence](https://docs.typesafe.ai/confidence)
- [Launch post](https://typesafe.ai/blog/introducing-system-one-models-and-jev) · [Jev on Vercel AI Gateway](https://vercel.com/ai-gateway/models/jev)
- [Jev use cases (AY Automate)](https://www.ayautomate.com/blog/jev-use-cases) · [1,300+ builds](https://www.ayautomate.com/jev-builds) · [awesome-jev-use-cases](https://github.com/walidboulanouar/awesome-jev-use-cases)
