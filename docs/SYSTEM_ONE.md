# System One (Jev) — Fast Structured Decisions

Alpha uses **Jev**, a System One model by [TypeSafe AI](https://typesafe.ai/), as the
default engine for the small, frequent judgements that used to be regex heuristics or
an extra LLM call. Normal LLMs remain the fallback everywhere.

## What a System One model is

It is **not** a text-generating LLM. You send a `state` plus typed `questions`; it
returns typed, calibrated decisions with probabilities. It cannot hallucinate or emit
a type error, because the answer space is defined in advance.

| Question | Asks | Answer |
| --- | --- | --- |
| `boolean` | Is this true? | probability `0..1` |
| `choice` | Which of these N options? (≤255) | `choice`, `probabilities`, `confidence` |
| `score` | Which level on this rubric? (2–10) | `score`, `probabilities`, `confidence` |

Key properties, from [TypeSafe's announcement](https://typesafe.ai/blog/introducing-system-one-models-and-jev):

- **70–500 ms** end-to-end (vs 3–329 s for frontier LLMs), because it samples in parallel instead of token-by-token.
- **Trained with RLCD** (Reinforcement Learning for Calibrated Decisions) so probabilities are epistemically honest — higher confidence really means higher accuracy.
- **Output tokens are free**; input is $0.042/MTok. On the Vercel AI Gateway it is currently **free** (promotional pricing ends 2026-09-25).
- **All questions in one request are evaluated in parallel** — asking 10 costs about the same as asking 1.
- Text-only input (no images/audio/video). 32K context for `state`.

The name comes from Kahneman's *Thinking, Fast and Slow*: System 1 is fast and
intuitive, System 2 is slow and deliberate.

## Endpoint

Alpha talks to the **Vercel AI Gateway**:

```
POST https://ai-gateway.vercel.sh/v1/evaluate
Authorization: Bearer <AI_GATEWAY_API_KEY>
model: "typesafe-ai/jev"
```

The gateway and the TypeSafe direct API use slightly different vocabularies, and the
client handles both:

| | Vercel AI Gateway | TypeSafe direct |
| --- | --- | --- |
| Endpoint | `/v1/evaluate` | `/v1/systemone` |
| Model id | `typesafe-ai/jev` | `jev-latest` |
| Boolean question type | `boolean` | `noul` |

Set `system_one.provider: "typesafe"` to switch; the client picks the right endpoint
and boolean key automatically.

## The fallback contract

**Every call site treats System One as an optional fast path.** Each entry point
returns `None` when System One is disabled, unreachable, misconfigured, or below
`min_confidence`. `None` never means "false" — it means "use the existing path".

Failure modes that degrade instead of breaking: disabled, missing key, 401/403, 429,
529, 5xx, timeout, transport error, non-JSON body, unparseable answer, low confidence.
A circuit breaker stops callouts after `circuit_breaker_threshold` consecutive
failures and probes again after the cooldown.

## Where it is used

| Site | File | Decision | Fallback |
| --- | --- | --- | --- |
| Skill security scan | `alpha/skills/security_scanner.py` | allow / warn / block | LLM rubric, then fail-closed |
| Tool-call guardrail | `alpha/guardrails/jev.py` | allow / review / deny | Abstain → deterministic policy |
| Deliberation routing | `alpha/deliberation/router.py` | SINGLE / ENSEMBLE / COUNCIL / DEBATE + difficulty + risk | Regex heuristics |
| Goal autoconfig | `alpha/autoconfig/engine.py` | domain, complexity, risk score | Keyword scoring |
| Memory escalation | `alpha/memory/active_memory.py` | is Tier-2 retrieval needed? | Confidence threshold |
| Goal completion | `alpha/runtime/goal.py` | satisfied? + blocker | LLM evaluator |
| Prompt-injection scan | `alpha/security/injection.py` | 4 decomposed signals, weighted | No check (content kept) |
| Browser action | `alpha/browser/jev_policy.py` | operation + element index | Existing step selection |
| Trace verification | `alpha/tools/trace_verify.py` | does the trace support the answer? | Existing receipts |
| Model escalation | `alpha/models/escalation.py` | does this need the flagship model? | Keyword/category routing |
| Skill & tool selection | `alpha/tools/selection.py` | ranked candidates | Regex scoring |
| Memory reranking | `alpha/memory/rerank.py` | relevance order | Retriever order (BM25) |
| Compaction retention | `alpha/context/retention.py` | drop / compress / keep / verbatim | Position + size thresholds |
| Subagent acceptance | `alpha/subagents/jev_acceptance.py` | per-criterion met / not met | UNVERIFIED |
| Citation support | `alpha/agents/middlewares/citation_support.py` | supported / unsupported / contradicted | Syntactic check only |
| Epistemic belief | `alpha/epistemics/jev_belief.py` | likelihood ratio from evidence | Fixed `likelihood_ratio=3.0` |

Every row's fallback is what ran before System One existed, and every row returns
`None` to mean "use it" — never `False`. See
[`SYSTEM_ONE_ALPHA_ROADMAP.md`](SYSTEM_ONE_ALPHA_ROADMAP.md) for who calls what.

The guardrail provider is **narrowing-only**: it can add denials but can never turn a
denial into an allow. On `allow` it abstains so the surrounding policy still applies.
Its sync `evaluate()` also abstains rather than blocking the event loop.

## Configuration

```yaml
system_one:
  enabled: true
  provider: "vercel-gateway"     # or "typesafe"
  api_key: "$AI_GATEWAY_API_KEY"
  model: "typesafe-ai/jev"
  timeout_ms: 2000
  max_retries: 2
  min_confidence: 0.60           # below this -> fall back
  fail_open: true
  log_decisions: false
  enable_skill_scan: true
  enable_guardrails: true
  enable_deliberation_router: true
  enable_goal_analysis: true
  enable_memory_escalation: true
  enable_goal_completion: true
```

Set `AI_GATEWAY_API_KEY` in `.env`. Use `log_decisions: true` to log every decision at
DEBUG (the state is truncated and the key is never logged).

### Measuring before trusting

Two more keys turn the integration into an experiment rather than a bet:

```yaml
system_one:
  shadow_mode: true          # call Jev, record the answer, then return None
  record_decisions: true     # append every decision to JSONL
  calibration_log_path: "system_one_decisions.jsonl"
```

In **shadow mode** every call site keeps running its existing path while the
decisions are recorded, so you can check whether a stated 0.9 really means 90%
right on Alpha's own data before letting any site act on one. Read the log with
`python backend/scripts/system_one_calibration.py`. See
[`SYSTEM_ONE_CALIBRATION.md`](SYSTEM_ONE_CALIBRATION.md).

## Troubleshooting

**`HTTP 403 customer_verification_required`** — the Vercel account needs a card on
file to unlock free credits, even for free models. Add one at
<https://vercel.com/d?to=%2F%5Bteam%5D%2F~%2Fai%3Fmodal%3Dadd-credit-card>. Until
then System One returns `None` and Alpha runs entirely on its fallback paths — which
is the designed behaviour, not a bug.

**Everything still works when disabled** — that is the point. Setting
`system_one.enabled: false` restores the exact previous behaviour.

## Demo

```
cd backend && python scripts/system_one_demo.py             # live, auto-fallback
cd backend && python scripts/system_one_demo.py --simulate  # force simulator
cd backend && python scripts/system_one_demo.py --verbose   # show distributions
```

Runs a variety matrix: boolean across contrasting states, choice routing, score on an
ordered rubric, an 8-question fan-out in one request, structured state with dotted path
references, a 255-option choice, and multilingual input.

## Tests

```
cd backend && python -m pytest tests/test_system_one.py tests/test_system_one_variety.py -q
```

68 tests in two files:

- **`test_system_one.py`** (34) — the *fallback* contract: config, payload shapes, both
  provider vocabularies, confidence thresholds, every failure mode (disabled, missing
  key, 401/403, 429, 5xx, timeout, transport error, non-JSON), retries, circuit
  breaker, and each call site degrading correctly.
- **`test_system_one_variety.py`** (34) — the *success* path: when Jev is reachable,
  every call site actually consumes its answer. Covers all three question types across
  contrasting inputs, mixed types in one request, 10-question fan-out, 255-option
  choice, 10-level score, structured/array/unicode/large/empty states, per-question
  confidence filtering, and each integration site being driven by a Jev verdict.

Because the gateway key is gated behind a credit-card check, the variety suite uses
`FakeJev` — a stand-in that implements the documented `/v1/evaluate` contract
faithfully, including the same schema validation and error messages the gateway
returns. Swap the transport and the identical code paths run against the real API.

## References

- [System One concept](https://docs.typesafe.ai/concepts/system-one)
- [API reference](https://docs.typesafe.ai/api)
- [Primitives](https://docs.typesafe.ai/primitives)
- [Models & pricing](https://docs.typesafe.ai/models)
- [Jev on Vercel AI Gateway](https://vercel.com/ai-gateway/models/jev)
