### System One transport (`packages/harness/alpha/models/system_one.py`)

- `SystemOneClient` supports hosted `vercel-gateway`, direct `typesafe`, and local `laya` providers. Laya uses the TypeSafe/Jev-compatible `POST /v1/systemone` contract, `noul` boolean questions, and the same `choice`/`score` answer parser; it is not a chat/LLM model and must not be added to `models[]`.
- The Windows desktop decision path is opt-in via `system_one.enable_computer_action` and is shadow-first. `alpha.computer_use.system_one_policy` projects UI Automation elements to semantic indexes only; `alpha.tools.builtins.computer_system_one_tool` resolves geometry locally and dispatches through the existing sentinel guard. Do not add coordinates, selectors, UIA handles, typed text, keys, or hotkey values to System One state, criteria, decisions, or receipts.
- Laya may run keyless on loopback. The client must not fall back to `AI_GATEWAY_API_KEY`, `TYPESAFE_API_KEY`, or `JEV_API_KEY` for that provider; it sends no Authorization header unless `LAYA_API_KEY`/`system_one.api_key` is configured. Hosted providers still fail closed without their key.
- Laya preflight bounds (`laya_max_state_chars`, `laya_max_request_chars`, `laya_max_questions`, `laya_max_choice_options`) abstain to the existing heuristic/LLM path before HTTP; a short checkpoint must never silently decide on truncated evidence. `evaluate_choice_partitioned()` is the provider-neutral bounded tournament for larger choice catalogs: it retains every original option for ranking, returns `None` on any failed/low-confidence stage, and obeys `laya_max_partition_requests` and `laya_max_partition_latency_ms` rather than flooding the local runtime. Browser partitions project only the relevant indexed elements; no selector or coordinate is sent to the model. Calibration records carry `provider` so local Laya and hosted Jev samples are not pooled.
- Successful HTTP is not treated as a successful decision until the response shape and numeric fields validate. Malformed provider JSON records a transport failure, opens the circuit under the normal threshold, and returns `None`; retries share a total deadline and only one half-open probe is admitted after cooldown.
- The default client resolves the current `AppConfig.system_one` on each access so provider changes in `config.yaml` do not require a restart; explicitly constructed clients remain stable until `reload()`. Tests: `tests/test_system_one_laya.py` and the existing `tests/test_system_one*.py` suites.

### Bring-your-own-model egress, credentials and credit exhaustion

- **Egress policy.** A caller-supplied `base_url` (`POST /api/models/providers/configure`, `models/provider_manager.py::configure_provider`) is screened by `alpha.community.url_safety.assert_model_endpoint_url` *before* it is persisted; `NetworkPolicyGuard.validate_model_endpoint` is the same policy behind the existing guard. Refused: non-`http(s)` schemes, URL-embedded credentials, every cloud metadata endpoint (never relaxable, including `::ffff:`-mapped literals), and — outside the declared local tier — loopback and RFC 1918/CGNAT hosts. `kind: "local"` (the default for the `custom` provider) permits loopback for Ollama/LM Studio/vLLM; a LAN host needs the operator allowlist `ALPHA_MODEL_ENDPOINT_PRIVATE_HOSTS`. Refusal raises `ModelEndpointBlockedError` (a `ValueError`) → HTTP 400. Persisted endpoints are re-screened offline on read (`resolve_dns=False`) so a file written before the guard existed cannot keep routing egress internally.
- **Credentials at rest.** `runtime_home()/models/provider_credentials.json` is an encrypted envelope (`alpha.provider_credentials` v2): Windows DPAPI at user scope, else a 0600 key file, else plaintext with a warning. A legacy plaintext file is migrated transparently on first read (`migrate_plaintext_credentials_file()`); `credentials_storage_status()` reports the live guarantee and `GET /api/models/providers/credentials-storage` exposes it. `api_key_env` stores only the *variable name*, so a key can live solely in `$OPENROUTER_API_KEY` and never touch the API body or the file. Residual risk, stated plainly: none of these stop code already running as the OS user that owns the Gateway.
- **Credit exhaustion is a typed, routed condition.** `models/fallback.py::CreditExhaustedError` normalizes HTTP 402 / `insufficient_quota` / "out of credits" (rate-limit phrasing is excluded so a throttle is not misread as billing), counts as retryable, and moves the chain forward; an exhausted chain sets `budget_status="CREDIT_EXHAUSTED"`. Provider switches are recorded as `FailoverEvent`s (`get_last_failover_events()`, optional `on_failover` callback) instead of only being logged, and an empty member response fails over too.
- **Cost accounting is wired.** `FallbackChatModel` charges the provider-reported usage of the member that actually served the call to `CostGovernor.record_usage` via `cost_governor.record_token_usage`, attributed through `usage_attribution(...)` / `ALPHA_COST_PROJECT_ID`. Accounting never fails an answer: a tripped breaker is recorded as a `budget_exhausted` `FailoverEvent`. Tests: `tests/test_byo_model_egress_policy.py`, `tests/test_credit_exhaustion_failover.py`.

### Model catalog data lives in `models.yaml`, never in code

`provider_manager.PROVIDER_SPECS` and `free_router.PROVIDERS` used to be ~550
lines of literals in this package. They are now read from the dedicated
`models.yaml` catalog (`alpha.config.models_catalog`), because a hand-maintained
provider list is a second source of truth that drifts silently. Add a provider
or gateway in YAML, not in Python. Both are re-resolved per call
(`refresh_provider_specs`, `refresh_free_gateways`) so a catalog edit is visible
without a restart; the module-level `PROVIDER_SPECS` / `PROVIDERS` bindings exist
only for importers that captured them at import time. With no catalog configured
they are **empty**, which is honest — offering a stale in-code list is the exact
drift this removed. Regenerate `models.example.yaml` from a pre-catalog build
with `backend/scripts/gen_models_example.py`.

### Cross-namespace capability drift (`catalog_consistency.py`)

`GET /api/models` merges four namespaces first-wins by name: `models[]`, the
bring-your-own-provider catalog, `custom_models`, and the free router. When the
same name appears in two with different capabilities, the API silently keeps the
`models[]` value while the other declaration keeps claiming something else — the
UI then offers a control the factory rejects. This shipped: `union-alpha` was
`supports_thinking: false` in `config.example.yaml` (pinned by
`tests/test_model_config.py`) and `true` in `PROVIDER_SPECS` and in a
hand-copied frontend fallback.

`check_model_catalog_consistency` compares `supports_thinking`,
`supports_vision`, `supports_reasoning_effort` and `context_window` across the
namespaces; `enforce_model_catalog_consistency(..., strict=True)` logs each
disagreement at ERROR. A `None` on either side is *undeclared*, not drift. Run it
on boot and keep `tests/test_model_catalog_consistency.py` green — a hand-typed
capability table is the industry's most common source of this bug.

### Provider model discovery (`discovery.py`)

Providers turn catalogs over continuously (OpenRouter rotates its `:free` set
daily), so the live list is **fetched, not hardcoded**. `GET
/api/models/discovery` returns each provider's catalog; `POST
/api/models/discovery/refresh` forces a refetch (admin-only — it makes an
outbound request). `backend/scripts/check_discovery.py` prints what each provider
reports.

- **Normalized to OpenRouter's descriptor shape**, the best capability contract
  any provider offers: `context_length` (the model), `endpoint_context_length`
  and `endpoint_max_completion_tokens` (what *this* endpoint serves) stay three
  separate numbers. Collapsing them is the documented cause of wrong
  summarization thresholds. `reasoning` is
  `{mandatory, supported_efforts[], default_effort}`, not one boolean.
- **Free is computed, not trusted**: a model is free only when both the input
  and output price are zero, because providers label free-input/paid-output
  models `:free` too.
- **"Undeclared" stays distinguishable from "declared false"**: a minimal
  OpenAI-compatible `{"data":[{"id":...}]}` entry leaves every richer field
  `None` rather than guessing.
- **Operational bounds**: 12s per-request timeout, 6h success TTL, 15m negative
  TTL (a down provider is not re-probed on every request), results cached under
  `runtime_home()/models/discovery/` so a restart does not re-fetch, 2000-model
  cap, `follow_redirects=False`, and a cache filename that cannot escape its
  directory.
- **Egress-screened** by `assert_model_endpoint_url` before every request — the
  same policy that guards BYO model configuration — so discovery cannot become
  an SSRF probe against loopback, RFC1918, or cloud metadata.
- **A provider with no dedicated adapter still works**: any catalog provider with
  a `base_url` falls back to the OpenAI-compatible `GET {base}/models` probe, so
  adding a gateway needs no code change. Tests: `tests/test_model_discovery.py`.

### Model routers fail closed on unresolvable routes

`task_router` / `category_router` / `workforce_router` used to carry hardcoded
vendor model ids (`gpt-4o`, `claude-opus-5`, `kimi-k3`, `ollama/qwen3:32b`, …)
that resolve against no operator's `models[]`. `task_router`'s
`[m for m in chain if m in have] or chain` then handed the unfiltered chain back,
so `POST /api/bots/route-task` returned a `primary` the factory rejects and the
lead agent silently degraded to the default model — a "quick" task quietly ran on
the flagship and billed accordingly.

Now: `model_routing.categories` / `model_routing.tiers` in `models.yaml` (or
`config.yaml`) are the only executable routing source, every declared name is
validated against `models[]` at load, and a chain that filters to nothing returns
an **empty `chain`/`primary` plus a `reason`** instead of inventing a route.
Escalation never invents one either. The built-in tables remain only as clearly
labelled advisory *suggestions* for operators who declared nothing. This is the
mechanism the rest of the field converged on: Continue `roles:`, Aider
`weak_model_name`, OpenHands `usage_id`, Letta's required `model`+`embedding`.

### Reasoning effort: one ladder, per-provider wire shapes

Effort is the control the whole field converged on — Claude Code's `/effort`,
Codex's `model_reasoning_effort`, OpenCode's `/variants`, Inspect's
`--reasoning-effort` — and no two spell the rungs or place the value the same
way. Alpha keeps **one canonical ladder** and a separate translation layer.

- **`config/reasoning_effort.py` owns the ladder**, weakest → strongest:
  `none, minimal, low, medium, high, xhigh, max`. It is dependency-free and
  lives under `config/` precisely so `config/model_config.py` can validate a
  declared ladder without importing `alpha.models` (which would cycle through
  the factory). It also owns the aliases (`off`, `x-high`, `ultra`,
  `ultrathink`, `adaptive`) and `clamp_effort`.
- **`models/effort_translation.py` owns the wire shapes** and is the only place
  that knows about LangChain client classes. Styles: `openai`,
  `openrouter`, `anthropic`, `anthropic_budget`, `google`, `bedrock`, `vllm`,
  `inert` (no knob), plus `auto` detection. `_STYLE_LADDER` is the **fallback**
  used when an entry declares nothing; a declared `reasoning_efforts` is
  authoritative and is never second-guessed, because declaring it *is* the
  assertion that this endpoint serves those rungs.

Four invariants, all regression-covered by `tests/test_reasoning_effort.py`:

1. **A rung is never sent that the resolved ladder cannot express, and effort is
   never silently raised.** `clamp_effort` picks the strongest rung at or below
   the request and, only when the request is under the floor, the floor itself.
   Both directions are logged. A *declared* ladder is the ceiling, so
   second-guessing it with `_STYLE_LADDER` is a bug, not a safety net.
2. **`none` is a thinking decision, not a rung.** It is resolved in
   `_build_single_model` *before* the thinking transforms and flips
   `thinking_enabled = False`, which routes it to the per-provider disable path
   that already exists (`when_thinking_disabled`, `extra_body.thinking.type=disabled`
   + `minimal`, vLLM `enable_thinking: false`, Anthropic `thinking: disabled`).
   Never write a disable alongside a high effort: Claude rejects
   `thinking: disabled` at `xhigh`/`max` with a 400. Google is the sole
   exception, because a zero `thinking_budget` is the only "off" it has.
3. **An unrecognized value is never silently honored.** `models[]` and a custom
   agent's `config.yaml` reject a misspelled rung at load; a run boundary value
   is a 422 (`app/gateway/run_models.py::_canonicalize_effort_section`, which
   canonicalizes `body.context` *and* the two free-form `config` carriers
   because the web client and the LangGraph SDK send it in different places).
4. **A capability the client lacks must not receive a request.** A model that
   declares neither `supports_reasoning_effort` nor a ladder gets the kwarg
   stripped and a warning naming the model, and an IM channel pinned to one has
   the value reconciled away in `ChannelManager._reconcile_reasoning_effort`
   rather than failing the delivery. Cross-namespace ladder drift is part of
   `catalog_consistency.COMPARED_FIELDS`, because a drifted ladder is the
   effort picker's version of the `union-alpha` `supports_thinking` bug: the UI
   offers exactly the rungs the drifted namespace advertised.

The composer's picker is fed by `GET /api/models`, which returns each entry's
`reasoning_efforts` / `default_reasoning_effort` plus the canonical
`reasoning_effort_levels` / `reasoning_effort_labels`. A model that declares
no ladder must render as "no effort control" rather than a menu whose
selections would be clamped.

**Don't edit `AgentConfig.reasoning_effort` back to a `Literal`.** It used to be
`Literal["low", "medium", "high"]`, which rejected `minimal` because Codex did
not serve it. That coupled a per-agent declaration to one provider: an agent
serving both a Codex model and a Gemini model could not name a rung only one of
them accepts. Agents now declare provider-neutral intent and the factory
clamps per model.

### One retry owner per call (`factory.py`)

Alpha stacks three retry/failover layers: `LLMErrorHandlingMiddleware`
(`retry_max_attempts`, default 3), the provider client's own `max_retries`, and
`FallbackChatModel` (up to 5 members). They multiply: the shipped defaults made
one persistently failing message cost up to `3 x 3 x 5 = 45` upstream calls, and
`max_retries: 2` read like "two retries" rather than "two retries inside every
attempt of every fallback member". LiteLLM hit this and pins the client to
`max_retries: 0` whenever its router owns retries.

`create_chat_model(..., retries_orchestrated=True)` does the same, **scoped by the
caller** rather than globally: the lead agent and the subagent executor pass it
(their graphs are wrapped by the middleware, and a chain by `FallbackChatModel`),
while standalone one-shot callers keep the SDK's retries because nothing above
them would retry otherwise. See `_pin_provider_retries_when_orchestrated`.

### Model Factory (`packages/harness/alpha/models/factory.py`)

- `create_chat_model(name, thinking_enabled)` instantiates LLM from config via reflection
- Supports `thinking_enabled` flag with per-model `when_thinking_enabled` overrides
- Accepts `reasoning_effort=<rung>` as a caller kwarg; it is popped before the
  constructor splat and resolved per provider (see the reasoning-effort section).
  An entry that pins `reasoning_effort:` in `models[]` is treated as a request
  too, so a pinned rung is clamped and logged rather than forwarded blindly.
  Codex keeps its own resolution (`none` when thinking is off, `medium` when
  nothing is pinned) so an existing deployment does not change which rung it
  reasons at, and an explicit request still wins even when the style detector
  cannot classify an extension's own `CodexChatModel` subclass.
- Provider failover: `models[].fallbacks` names an ordered chain tried on retryable errors only (429, 5xx, timeout/connection); deterministic failures raise at once. Single-member chains return the plain client (zero behavior change). Chain members that lack `supports_thinking` are skipped with a warning when thinking is on; `FallbackChatModel` (`models/fallback.py`) stays a `BaseChatModel` so `bind_tools`/streaming/middlewares work, binds tools per member, and reports the serving member via `get_last_effective_model()`. Exhaustion raises `ModelFallbackExhaustedError` carrying names + error classes only (never messages/secrets). Chains cap at 5, resolve transitively, reject cycles at build.
- Provider profiles: top-level `providers:` (`config/model_config.py::ProviderConfig`) supplies defaults (`use`, endpoint, keys, timeouts) inherited by `models[].provider` entries; model-level keys win, `name` never inherits. A model with neither `use` nor a supplying provider fails closed at build.
- Supports vLLM-style thinking toggles via `when_thinking_enabled.extra_body.chat_template_kwargs.enable_thinking` for Qwen reasoning models, while normalizing legacy `thinking` configs for backward compatibility
- Supports `supports_vision` flag for image understanding models
- Config values starting with `$` resolved as environment variables
- Missing provider modules surface actionable install hints from reflection resolvers (for example `uv add langchain-google-genai`)

### vLLM Provider (`packages/harness/alpha/models/vllm_provider.py`)

- `VllmChatModel` subclasses `langchain_openai:ChatOpenAI` for vLLM 0.19.0 OpenAI-compatible endpoints
- Preserves vLLM's non-standard assistant `reasoning` field on full responses, streaming deltas, and follow-up tool-call turns
- Designed for configs that enable thinking through `extra_body.chat_template_kwargs.enable_thinking` on vLLM 0.19.0 Qwen reasoning models, while accepting the older `thinking` alias
- `cumulative_stream_usage` is an opt-in model setting (default `false`) for endpoints that repeat cumulative token totals on each streaming chunk. The provider converts snapshots to deltas only when a stable completion id is present, isolates interleaved streams by id, and leaves the original usage untouched otherwise. Per-model tracking is lock-protected and cleared on the trailing empty-`choices` frame whether or not that frame carries usage. A soft cap of 1024 ids evicts only entries idle for at least one hour; active streams may temporarily exceed the cap so eviction cannot corrupt their deltas. Regression coverage lives in `tests/test_vllm_provider.py`.

## System One / Laya provider boundary

`system_one.provider` is a validated choice between hosted Jev (`vercel-gateway`, `typesafe`) and the self-hosted Apache-2.0 Laya decision model (`laya`). Laya speaks the same `/v1/systemone` wire protocol as TypeSafe Jev (`noul` for boolean questions) but is not a chat model and must not be inserted into the `models[]` catalog. Laya's runtime and weights live under the ignored project-local `.alpha/laya` environment so its PyTorch/Transformers stack is not pulled into Alpha's core lockfile. The client permits keyless loopback Laya, never forwards cloud credentials to it, and abstains before HTTP when state, question count, or choice cardinality exceeds the configured safe budget. Keep the initial local rollout in `shadow_mode`; calibration records are partitioned by provider. Reproducible setup is `make system-one-laya-setup MODEL=english DEVICE=auto` followed by `make system-one-laya-serve` and `make system-one-laya-status`.

### System One / Laya

The System One client is provider-neutral across hosted Jev and the self-hosted
Convai Innovations Laya decision model. Laya uses the Jev-compatible
`/v1/systemone` contract, so it is configured under `system_one`, not `models[]`.
Its PyTorch runtime/checkpoints are installed under ignored
`.alpha/laya`; Alpha's core dependency lock does not include the ML
stack. Laya is keyless only on loopback, never receives hosted credentials, and
abstains before HTTP when its state/question/choice budgets are unsafe. Keep a
new local provider in `shadow_mode` until its provider-specific calibration is
reviewed. Tests: `tests/test_system_one_laya.py` and
`tests/test_system_one_laya_setup.py`.
