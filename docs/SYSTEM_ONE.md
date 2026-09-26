# System One (Jev or local Laya) — Fast Structured Decisions

Alpha uses System One models for the small, frequent judgements that used to be regex
heuristics or an extra LLM call. Two interchangeable providers are supported:

- **Jev** — hosted through the Vercel AI Gateway or TypeSafe's direct API.
- **Laya** — Convai Innovations' Apache-2.0, open-weights model, self-hosted through
  `laya-serve` with the same Jev-compatible `/v1/systemone` protocol. The project
  setup pins `laya==0.3.20`; the English, multilingual, and typed-decisions
  checkpoints are available from the same Hugging Face hub.

Normal LLMs and the existing deterministic heuristics remain the fallback everywhere.

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

## Provider endpoints

The client selects the endpoint, model, and boolean vocabulary from
`system_one.provider`:

| Provider | Endpoint | Model field | Boolean type | Auth |
| --- | --- | --- | --- | --- |
| `vercel-gateway` | `https://ai-gateway.vercel.sh/v1/evaluate` | `typesafe-ai/jev` | `boolean` | `AI_GATEWAY_API_KEY` |
| `typesafe` | `https://api.typesafe.ai/v1/systemone` | `jev-latest` | `noul` | `TYPESAFE_API_KEY` |
| `laya` | `http://127.0.0.1:8000/v1/systemone` | `english`, `multilingual`, `typed-decisions`, or `""` | `noul` | Optional on loopback; otherwise `LAYA_API_KEY` |

Laya's server is deliberately wire-compatible with Jev, so switching provider does not
change any call site or answer parser. Provider values are validated; an unknown value
fails configuration loading rather than silently sending traffic to the gateway.

## The fallback contract

**Every call site treats System One as an optional fast path.** Each entry point
returns `None` when System One is disabled, unreachable, misconfigured, or below
`min_confidence`. `None` never means "false" — it means "use the existing path".

Failure modes that degrade instead of breaking: disabled, missing key, 401/403, 429,
529, 5xx, timeout, transport error, non-JSON body, malformed numeric/provider data,
unparseable answer, low confidence, or an exhausted request/deadline budget.
A circuit breaker stops callouts after `circuit_breaker_threshold` consecutive
failures and admits only one half-open probe after the cooldown. Retry-After values
are bounded, and the whole retry sequence shares the per-call deadline.

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
| Windows desktop action | `alpha/computer_use/system_one_policy.py` + `alpha/tools/builtins/computer_system_one_tool.py` | `CLICK`/`TYPE_TEXT`/`PRESS`/`HOTKEY` + accessibility index where needed | Existing desktop/LLM fallback |
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
# Hosted Jev (default in config.example.yaml)
system_one:
  enabled: true
  provider: "vercel-gateway"     # vercel-gateway | typesafe | laya
  api_key: "$AI_GATEWAY_API_KEY"
  model: "typesafe-ai/jev"
  timeout_ms: 2000
  max_retries: 2
  min_confidence: 0.60           # below this -> fall back
  fail_open: true
  log_decisions: false
  # Optional, accessibility-first Windows desktop route. Keep it off until
  # shadow-mode calibration is reviewed locally.
  enable_computer_action: false
```

For local Laya, use the project-local setup helper. It creates an isolated environment
under `.agent-workspace/laya` (so Alpha's normal lockfile and install do not pull
PyTorch/Transformers), downloads only the selected checkpoint(s), and keeps the Hugging
Face cache inside the project:

```bash
# English checkpoint, auto-select CUDA when available (otherwise CPU), loopback-only server
python backend/scripts/system_one_laya_setup.py setup
python backend/scripts/system_one_laya_setup.py serve

# Force a device when needed:
python backend/scripts/system_one_laya_setup.py setup --device cuda
python backend/scripts/system_one_laya_setup.py setup --device cpu

# Other useful variants
python backend/scripts/system_one_laya_setup.py setup --model multilingual
python backend/scripts/system_one_laya_setup.py setup --model router
python backend/scripts/system_one_laya_setup.py status
```

Stop the Laya server before rerunning `setup` (the setup command replaces the
isolated PyTorch wheel). The helper's `serve` command performs one warm-up
forward before opening the HTTP port, so the first Alpha request does not pay
the CUDA initialization cost. The matching local configuration is:

```yaml
system_one:
  enabled: true
  provider: "laya"
  base_url: "http://127.0.0.1:8000"
  api_key: null                 # keyless loopback
  model: "english"              # use "" for Laya Router
  timeout_ms: 10000
  laya_max_state_chars: 12000
  laya_max_request_chars: 24000
  laya_max_questions: 64
  laya_max_choice_options: 20
  laya_max_partition_requests: 16
  laya_max_partition_latency_ms: 60000
  max_retries: 1
  min_confidence: 0.75
  shadow_mode: true             # measure before allowing decisions to act
  record_decisions: true
  enable_computer_action: false    # opt in only after desktop calibration
```

If the server binds beyond loopback, set the same random `LAYA_API_KEY` in the server
environment and set `system_one.api_key: "$LAYA_API_KEY"`. The client never forwards a
cloud gateway key to Laya, and a keyless Laya URL is accepted only when it resolves to
loopback. Laya's context is shorter than Jev's, so the client abstains before sending
states above `laya_max_state_chars`, serialized request bodies above the conservative
payload budget, question batches above `laya_max_questions`, or choice questions above
`laya_max_choice_options`.

The setup helper is a local operator action, not an implicit request-time download. Keep
`shadow_mode: true` for the first calibration run; the upstream README documents that
Laya's confidence and primitive behavior are task/checkpoint dependent.

### High-cardinality choices

Laya's practical option budget is smaller than the hosted Jev ceiling. Alpha does not
silently truncate a skill/tool catalog or a browser target head. The shared
`evaluate_choice_partitioned()` helper evaluates deterministic partitions, keeps a small
shortlist from each, and performs bounded final tournaments. The result retains every
original option for ranking callers, while the browser continues to resolve only the
index it receives. A partition that is unavailable or below its confidence floor returns
`None`, preserving the caller's existing fallback; no partial winner is executed.
`laya_max_partition_requests` bounds the number of local requests per decision. Large
catalogs that exceed that budget abstain rather than flooding the GPU. Hosted Jev keeps
its one-request path for catalogs up to the documented 255-option limit.

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

**Hosted Jev `HTTP 403 customer_verification_required`** — the Vercel account needs a
card on file to unlock free credits, even for free models. Add one at
<https://vercel.com/d?to=%2F%5Bteam%5D%2F~%2Fai%3Fmodal%3Dadd-credit-card>. Until
then System One returns `None` and Alpha runs entirely on its fallback paths — which
is the designed behaviour, not a bug.

**Laya is unavailable** — run `python backend/scripts/system_one_laya_setup.py status`.
If the package is installed but health is `unreachable`, start the separate server with
`serve` and keep the `base_url` in `config.yaml` aligned with its host/port. A server
started with `LAYA_API_KEY` requires the same key in `system_one.api_key`; a keyless
server should be bound to `127.0.0.1` only.

**Laya answers look too confident** — this is why the local setup starts in shadow mode.
Review `python backend/scripts/system_one_calibration.py`, compare the
provider-specific bucket, and only then lower the confidence floor or disable shadow
mode. Laya's upstream documentation reports known `noul` label sensitivity and warns
that the base checkpoints are not calibrated for every domain.

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

71 hosted-provider tests in the two legacy files, plus dedicated local Laya, partition, and
setup regressions:

- **`test_system_one.py`** (37) — the *fallback* contract: config, payload shapes, both
  provider vocabularies, confidence thresholds, every failure mode (disabled, missing
  key, 401/403, 429, 5xx, timeout, transport error, non-JSON), retries, circuit
  breaker, and each call site degrading correctly.
- **`test_system_one_variety.py`** (34) — the *success* path: when Jev is reachable,
  every call site actually consumes its answer. Covers all three question types across
  contrasting inputs, mixed types in one request, 10-question fan-out, 255-option
  choice, 10-level score, structured/array/unicode/large/empty states, per-question
  confidence filtering, and each integration site being driven by a Jev verdict.
- **`test_system_one_laya.py`** — local endpoint/vocabulary/auth, keyless operation,
  preflight budget abstention, and provider-separated calibration.
- **`test_system_one_partition.py`** — deterministic partition/tournament budgets,
  state projection, and the 255-target browser regression.
- **`test_system_one_laya_setup.py`** — offline checks for the isolated setup helper;
  it never downloads weights or starts a server.

Because the gateway key is gated behind a credit-card check, the variety suite uses
`FakeJev` — a stand-in that implements the documented `/v1/evaluate` contract
faithfully, including the same schema validation and error messages the gateway
returns. Swap the transport and the identical code paths run against the real API.

## References

- [System One concept](https://docs.typesafe.ai/concepts/system-one)
- [TypeSafe API reference](https://docs.typesafe.ai/api)
- [Laya GitHub repository](https://github.com/NandhaKishorM/laya)
- [Laya Hugging Face checkpoints](https://huggingface.co/convaiinnovations/laya)
- [Laya PyPI package](https://pypi.org/project/laya/)
- [Jev on Vercel AI Gateway](https://vercel.com/ai-gateway/models/jev)
