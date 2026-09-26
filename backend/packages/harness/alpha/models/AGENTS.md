### System One transport (`packages/harness/alpha/models/system_one.py`)

- `SystemOneClient` supports hosted `vercel-gateway`, direct `typesafe`, and local `laya` providers. Laya uses the TypeSafe/Jev-compatible `POST /v1/systemone` contract, `noul` boolean questions, and the same `choice`/`score` answer parser; it is not a chat/LLM model and must not be added to `models[]`.
- The Windows desktop decision path is opt-in via `system_one.enable_computer_action` and is shadow-first. `alpha.computer_use.system_one_policy` projects UI Automation elements to semantic indexes only; `alpha.tools.builtins.computer_system_one_tool` resolves geometry locally and dispatches through the existing sentinel guard. Do not add coordinates, selectors, UIA handles, typed text, keys, or hotkey values to System One state, criteria, decisions, or receipts.
- Laya may run keyless on loopback. The client must not fall back to `AI_GATEWAY_API_KEY`, `TYPESAFE_API_KEY`, or `JEV_API_KEY` for that provider; it sends no Authorization header unless `LAYA_API_KEY`/`system_one.api_key` is configured. Hosted providers still fail closed without their key.
- Laya preflight bounds (`laya_max_state_chars`, `laya_max_request_chars`, `laya_max_questions`, `laya_max_choice_options`) abstain to the existing heuristic/LLM path before HTTP; a short checkpoint must never silently decide on truncated evidence. `evaluate_choice_partitioned()` is the provider-neutral bounded tournament for larger choice catalogs: it retains every original option for ranking, returns `None` on any failed/low-confidence stage, and obeys `laya_max_partition_requests` and `laya_max_partition_latency_ms` rather than flooding the local runtime. Browser partitions project only the relevant indexed elements; no selector or coordinate is sent to the model. Calibration records carry `provider` so local Laya and hosted Jev samples are not pooled.
- Successful HTTP is not treated as a successful decision until the response shape and numeric fields validate. Malformed provider JSON records a transport failure, opens the circuit under the normal threshold, and returns `None`; retries share a total deadline and only one half-open probe is admitted after cooldown.
- The default client resolves the current `AppConfig.system_one` on each access so provider changes in `config.yaml` do not require a restart; explicitly constructed clients remain stable until `reload()`. Tests: `tests/test_system_one_laya.py` and the existing `tests/test_system_one*.py` suites.

### Model Factory (`packages/harness/alpha/models/factory.py`)

- `create_chat_model(name, thinking_enabled)` instantiates LLM from config via reflection
- Supports `thinking_enabled` flag with per-model `when_thinking_enabled` overrides
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

`system_one.provider` is a validated choice between hosted Jev (`vercel-gateway`, `typesafe`) and the self-hosted Apache-2.0 Laya decision model (`laya`). Laya speaks the same `/v1/systemone` wire protocol as TypeSafe Jev (`noul` for boolean questions) but is not a chat model and must not be inserted into the `models[]` catalog. Laya's runtime and weights live under the ignored project-local `.agent-workspace/laya` environment so its PyTorch/Transformers stack is not pulled into Alpha's core lockfile. The client permits keyless loopback Laya, never forwards cloud credentials to it, and abstains before HTTP when state, question count, or choice cardinality exceeds the configured safe budget. Keep the initial local rollout in `shadow_mode`; calibration records are partitioned by provider. Reproducible setup is `make system-one-laya-setup MODEL=english DEVICE=auto` followed by `make system-one-laya-serve` and `make system-one-laya-status`.

### System One / Laya

The System One client is provider-neutral across hosted Jev and the self-hosted
Convai Innovations Laya decision model. Laya uses the Jev-compatible
`/v1/systemone` contract, so it is configured under `system_one`, not `models[]`.
Its PyTorch runtime/checkpoints are installed under ignored
`.agent-workspace/laya`; Alpha's core dependency lock does not include the ML
stack. Laya is keyless only on loopback, never receives hosted credentials, and
abstains before HTTP when its state/question/choice budgets are unsafe. Keep a
new local provider in `shadow_mode` until its provider-specific calibration is
reviewed. Tests: `tests/test_system_one_laya.py` and
`tests/test_system_one_laya_setup.py`.
