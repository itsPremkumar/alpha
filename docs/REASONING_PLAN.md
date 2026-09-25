# Alpha Reasoning Plane — Integration Inventory and Design Contract

**Status:** inventory-first design contract for the additive reasoning plane
**Base revision:** `c9b11f8777f2871e5a65d1cc468b46674c1308b3`
**Target package:** `backend/packages/harness/alpha/reasoning/`
**Write scope of this work:** the new reasoning modules, `backend/tests/test_reasoning_*.py`, and this document

## 0. Executive integration decision

Alpha does **not** need a second agent runtime, planner, evidence engine, verifier, memory tree, or lifecycle owner. The authoritative plan is a reference shape, not a build order. This work therefore adds only typed, default-off contracts and pure decision functions for capabilities that do not already have a production owner:

- a structured reasoning control state and bounded record set;
- the Atom-of-Thoughts dependency-DAG/contraction substrate;
- an explicit-signal policy engine with disclosed under-determination;
- a multi-dimensional local budget ledger;
- test-time-compute allocation with a step-verifier honesty gate;
- multi-dimensional uncertainty and a decision policy that never grants completion;
- a structured user-facing summary plus the private-reasoning persistence guard;
- an emit-only reasoning event taxonomy;
- pure loop/thrash signals and escalation recommendations that delegate enforcement to the existing loop detector/watchdog;
- a default-off package configuration that does not import the shared config layer.

Everything that executes, authorizes, approves, cancels, verifies, checkpoints, persists run lifecycle state, or publishes to the runtime event bus is **DELEGATE** to the existing owner. This package proposes structured inputs to those owners. It must never claim that a proposal was approved, executed, verified, or completed.

## 1. Sources read

### 1.1 Repository authorities

Read in full before this inventory was written:

- `references/ALPHA_CHAIN_OF_THOUGHT_REASONING_IMPLEMENTATION_PLAN.md` (the current main-checkout copy is 2,958 lines; all sections were read), especially §§0–5, 7–8, 10–26, 33, 35–40, 43–45, 49–52, 55–62.
- `AGENTS.md` and `backend/AGENTS.md`.
- `alpha/reasoning/governor.py`, `alpha/reasoning/introspective_tree_search.py`, `alpha/reasoning/tom/`.
- `alpha/agents/middlewares/metacognitive_middleware.py`, `alpha/metacognition/`, `alpha/deliberation/`, `alpha/agency/`, `alpha/supervision/`, `alpha/memory/active_memory.py`, `alpha/autoconfig/`.
- `alpha/orchestrator/`, `alpha/workflow/`, `alpha/runtime/goal.py`, `alpha/goals/`, and goal routes.
- `alpha/evidence/`, `alpha/runtime/runs/verification.py`, `alpha/subagents/acceptance_checks.py`, `alpha/selfrepair/`, `alpha/rsi/`, `alpha/evaluation/`, `alpha/benchmarks/release_gate.py`, and reproduction/verification surfaces.
- `alpha/models/`, `alpha/subagents/`, `alpha/events/bus.py`, `alpha/runtime/events/`, `alpha/runtime/journal.py`, `alpha/runtime/runs/worker.py`, durable context, summarization, memory, authorization, sandbox, and approval middlewares.

### 1.2 Research inputs

These papers are **design inputs only**. No source code was copied. Final modules cite them in docstrings.

| Research input | Design consequence in this package |
|---|---|
| **Atom of Thoughts for Markov LLM Test-Time Scaling**, arXiv:2502.12018v4, NeurIPS 2025 | `atoms.py` decomposes a problem into an acyclic dependency DAG and contracts dependent sub-atoms into one answer-equivalent state. The brief reports +3.4% over o3-mini and +10.6% over DeepSeek-R1 on HotpotQA with GPT-4o-mini; those are paper claims, not measurements reproduced here. The package measures the token-accounting trade-off on a synthetic problem. |
| **Test-Time Scaling in Reasoning LLMs**, arXiv:2608.04001v2 | `ttcs.py` models the three research regimes separately: single-trajectory sequential, leaf-level with terminal reduction, and prefix-level search. A scalar budget is never reported without its protocol, sample reservation, and consumption trail. |
| **Step-level Verifier-guided Hybrid Test-Time Scaling**, arXiv:2507.15512v3, EMNLP 2025 | `ttcs.py` exposes a step-level process-verifier interface. Prefix-level search requires a real injected step verifier; no search engine is built here. Existing DWE/introspective-search owners remain the execution engines. |
| **Sleep-time Compute**, arXiv:2504.13171v1 | No background precompute loop is added. The paper reports roughly 5x less test-time compute for equal accuracy, up to +13%/+18% accuracy, and better results when the future query is predictable. Alpha has no query-predictability or sleep-time scheduler contract in this scope, so contracted states are only cacheable artifacts a future host may precompute after an explicit predictability decision. |
| **T1: Tool-integrated Verification for Test-time Compute Scaling in Small Language Models**, arXiv:2504.04718v2, ICLR 2026 | `ttcs.py` refuses prefix-level search without a step-level process verifier and refuses to represent a cheap LLM self-rating as one. T1 found that small verifiers fail on memorization-heavy checks; deterministic/tool/invariant-backed signals are preferred. |

## 2. Verdict definitions

- **DELEGATE** — a live production subsystem already owns the capability. Call, wrap, or adapt it. File/line evidence is mandatory.
- **EXTEND** — the correct subsystem or seam exists, but it lacks the specific typed contract, propagation, or honesty behavior. This branch owns only the additive contract; wiring is reported.
- **NEW** — no viable existing owner was found. The new capability must still be default-off, inert without explicit invocation, and justified by this inventory.

## 3. Pre-existing contents of the target package

The directory is not empty. Its existing public behavior is preserved.

| Existing symbol | Evidence | Disposition |
|---|---|---|
| `ReasoningGovernor`, governor-tier `ReasoningConfig`, prompt-regex effort selection, context/run-budget clamp | `backend/packages/harness/alpha/reasoning/governor.py:13-56`, `:59-166` | Preserve. The new `config.py:ReasoningConfig` is the reasoning-plane config; the package root keeps exporting the legacy governor `ReasoningConfig` for compatibility and exposes the new one as `ReasoningPlaneConfig`. |
| `IntrospectiveTreeSearchEngine`, `MCTSNode`, `CompositeRewardEvaluator`, `run_introspective_tree_search` | `backend/packages/harness/alpha/reasoning/introspective_tree_search.py:28-102`, `:105-181`, `:183-454`, `:457-554` | Preserve and DELEGATE actual search. `ttcs.py` allocates protocol/budget only. Its unbounded `thought_trace` is a pre-existing privacy/integrity risk described below. |
| `TheoryOfMindConsultant`, `IntentHypothesis` | `backend/packages/harness/alpha/reasoning/tom/models.py:9-38`, `backend/packages/harness/alpha/reasoning/tom/consultant.py:44-182` | Preserve. This models the user's intent/risk tolerance; it is **not** Tree-of-Thought and is not a solution-hypothesis record. |
| Existing eager package export | `backend/packages/harness/alpha/reasoning/__init__.py:1-10` at the base revision | Replace only the export mechanism with the repository's lazy helper while preserving the four existing public names. |

## 4. Plan §4 module-table mapping

The plan's §4 responsibility table contains 22 capability rows. Every row is accounted for below.

| Plan §4 module / capability | Verdict | Existing owner and file:line evidence | Integration decision |
|---|---|---|---|
| `models.py` — typed reasoning schemas | **EXTEND** | The target package has only a governor config (`alpha/reasoning/governor.py:13-56`), search dataclasses (`alpha/reasoning/introspective_tree_search.py:28-181`), and a separate ToM model (`alpha/reasoning/tom/models.py:28-38`). `ThreadState` owns conversation channels (`alpha/agents/thread_state.py:280-295` for `goal`), not a reasoning control state. | NEW additive `models.py` with closed enums, bounded collections, and disclosed range clamping. It does not become a lifecycle or completion owner. |
| `policy.py` — strategy selection | **EXTEND** | The governor offers three prompt-regex tiers (`alpha/reasoning/governor.py:80-151`), not explicit task signals or the plan's nine classes. Metacognition is deliberately observe-only (`alpha/agents/middlewares/metacognitive_middleware.py:10-14`, `:74-88`). Other overlapping classifiers exist (`alpha/orchestration/intent.py:60-95`; `alpha/deliberation/router.py:62-335`; `alpha/planning/meta_planner.py:25-227`). | Add one pure explicit-signal classifier. Under-determination resolves to a disclosed default. It does not replace the governor, intent engine, or model router. |
| `budget.py` — token/time/tool/branch/retry budgets | **EXTEND** | The governor clamps only thinking tokens/temperature/timeout (`alpha/reasoning/governor.py:30-78`). DWE has its own node/workflow budgets (`alpha/workflow/runtime.py:363-367`, `:547-591`); the parent has `TokenBudgetMiddleware` (`alpha/agents/middlewares/token_budget_middleware.py:77-145`); subagent caps are separate (`alpha/agents/middlewares/subagent_limit_middleware.py`). | Add a bounded local ledger with hard refusal, marginal-value admission, and an accounting trail. It can only be stricter than existing scopes; it never raises a parent/subagent/DWE budget. |
| `router.py` — model/provider/strategy routing | **DELEGATE** | `create_chat_model()` resolves and constructs chains and thinking-capable members (`alpha/models/factory.py:204-234`, `:259-333`). `FallbackChatModel` performs per-call retryable failover (`alpha/models/fallback.py:90-119`, `:202-255`). System One is a non-chat decision client with an abstain contract (`alpha/models/system_one.py:1-22`, `:365-419`). | No new router. Policy consumes provider capabilities; model construction/failover remain with the factory. |
| `planner.py` — plan create/update | **DELEGATE** | `AutonomousPlanner` produces plans, waves, criteria, and cost estimates (`alpha/planning/autonomous.py:369-540`); the DWE bridge compiles real definitions (`alpha/workflow/dynamic_bridge.py:134-267`); `DynamicDecomposer` owns typed dependencies/retries/compensation (`alpha/workflow/dynamic_decomposer.py:70-120`, `:142-345`). | No new planner. `ReasoningState.plan` is a projection/reference to the existing plan, not a competing task system. |
| `hypothesis.py` — candidate explanations/solutions | **EXTEND** | No general solution-hypothesis record was found. The ToM `IntentHypothesis` is a user mental model (`alpha/reasoning/tom/models.py:28-38`). Discriminating-hypothesis debugging already exists operationally in planning/repair paths. | Add a typed `HypothesisRecord` to structured state. It is data for existing verifiers/critics, never an automatic selection. |
| `loop.py` — plan/act/observe controller | **DELEGATE** | The lead graph is created with `create_agent()` (`alpha/agents/lead_agent/agent.py:1308-1314`); DWE executes typed child graphs (`alpha/workflow/runtime.py:454-468`); goal continuations are bounded in `alpha/runtime/goal.py:329-424` and invoked by the parent worker (`alpha/runtime/runs/worker.py:1294-1320`). | No second agent loop. ReAct events are observations of the existing loop. |
| `action.py` — structured action intent | **DELEGATE** | Tool authorization is two-layer and fail-closed by provider policy (`alpha/authz/adapter.py:27-102`); sandbox execution is separately authorized (`alpha/authz/sandbox_authz.py:124-187`, `alpha/sandbox/tools.py:1453-1486`); read-before-write, approvals, receipts, and hooks are orthogonal middlewares. | `ReasoningActionIntent` is an explicitly untrusted proposal with digests only. It cannot execute or imply approval. |
| `observation.py` — normalized tool/environment result | **EXTEND** | The parent run journal records tool results (`alpha/runtime/journal.py:394-557`); DWE nodes carry output/evidence (`alpha/workflow/models.py:115-138`), but string/dict evidence is not a typed observation contract. | Add a bounded `ObservationRecord` adapter contract. The existing tool runtime remains the producer and the security boundary. |
| `evidence.py` — evidence records/provenance | **DELEGATE** | The owner-scoped evidence store enforces state transitions and atomic persistence (`alpha/evidence/store.py:53-129`). The run acceptance overlay consumes evidence without owning lifecycle (`alpha/runtime/runs/verification.py:32-73`). | No parallel store. `EvidenceRecord` is a typed hand-off; persistence goes through the existing evidence subsystem. |
| `verifier.py` — deterministic/model/hybrid verification | **DELEGATE** | Deterministic subagent acceptance is the strongest existing verifier (`alpha/subagents/acceptance_checks.py:1610-1748`). Run acceptance is an overlay (`alpha/runtime/runs/verification.py:32-73`). RSI holdout executes real suites and emits measured evidence (`alpha/rsi/holdout.py:178-259`). Reproduction/self-repair are existing verifier seams (`alpha/selfrepair/engine.py:1-4`; `alpha/reproduction/gates.py:19-145`). | No new verifier class or completion path. `ttcs.py` only requires an injected step-level verifier contract. |
| `critic.py` — adversarial challenge | **DELEGATE** | Finish-First completion critics are wired (`alpha/agents/middlewares/finish_first_verifier_middleware.py:274-306`; `alpha/agents/lead_agent/agent.py:770-772`). Honest deliberation council/debate/verifier paths exist (`alpha/deliberation/council.py:59-91`; `alpha/deliberation/debate.py:59-267`; `alpha/deliberation/verifier.py:35-115`). | No new critic engine. New records feed existing critics; their fail-open behavior remains disclosed below. |
| `reflection.py` — bounded failure analysis | **DELEGATE** | Reflexion persistence exists (`alpha/learning/reflexion.py:38+`; `alpha/tools/builtins/reflexion_tool.py:15`). The live continual harness is wired at `alpha/agents/lead_agent/agent.py:556-559`. DWE replanning uses typed repair/evidence patches (`alpha/workflow/replanner.py:15-95`; `alpha/orchestrator/loop.py:285-315`). | `ReflectionRecord` stores a compact lesson and `should_persist_to_memory=false` by default. Existing review/reflexion owners validate and persist it. |
| `branch_search.py` — selective ToT-style search | **EXTEND** | Introspective MCTS/LATS already exists (`alpha/reasoning/introspective_tree_search.py:105-454`) and DWE has deterministic routing/branching (`alpha/workflow/router.py:21-58`; `alpha/workflow/runtime.py:607-833`). No general reasoning-search budget/verifier policy exists. | `ttcs.py` allocates the three test-time regimes and reserves budget; the existing engines execute. No search engine is built here. |
| `consensus.py` — MoA/council aggregation | **DELEGATE** | Real council/debate deliberation is in `alpha/deliberation/council.py:59-296` and `alpha/deliberation/debate.py:59-267`; deterministic routing uses the configured roster (`alpha/deliberation/router.py:62-335`). A separate registered MoA tool and orchestrator exist (`alpha/models/moa/orchestrator.py:36-93`). | No new consensus algorithm. The fabricated registered MoA tool is a pre-existing blocker and must not be used as a reasoning-plane verifier. |
| `uncertainty.py` — multi-source confidence | **EXTEND** | Calibration math exists (`alpha/metacognition/calibration.py:54-85`) but the live middleware creates a fresh monitor per result and never feeds the calibrator (`alpha/agents/middlewares/metacognitive_middleware.py:39-53`). Epistemic claims and falsification exist (`alpha/epistemics/models.py:24-52`); deliberation confidence is disclosed but not calibrated. No six-dimension decision engine exists. | Add factual/model/tool/environment/plan/verification estimates, each carrying a calibration status. Decisions return to existing verification; they never grant success. |
| `compression.py` — context/state compaction | **EXTEND** | Production summarization writes `summary_text` through one factory (`alpha/agents/middlewares/summarization_middleware.py:906-938`); durable context injects authority and untrusted data on separate channels (`alpha/agents/middlewares/durable_context_middleware.py:255-312`); a structured condenser exists (`alpha/context/condenser/summarizer.py:10-83`). | AoT contracted states reduce what a future prompt compiler must carry. No second message compactor or conversation-state owner is added. |
| `summary.py` — user-facing reasoning explanation and §24 guard | **NEW** | Conversation summarization is context compaction, not a structured user-facing reasoning summary. No existing fail-closed guard rejects raw hidden reasoning, provider thinking blocks, credential-bearing reasoning payloads, and unbounded chain logs before persistence. `alpha/utils/llm_text.py` strips think blocks for specific input-polish/suggestion paths; it is not a persistence guard. | NEW generator built only from typed records plus `assert_no_private_reasoning` and a guarded persistence helper. Tests must prove rejection of all five prohibited classes and acceptance of the ten legitimate record classes. |
| `provenance.py` — action/evidence/artifact linkage | **DELEGATE** | Evidence models carry owner/locator/provenance (`alpha/evidence/models.py:9-115`); durable run events define ordered, retrievable envelopes (`alpha/runtime/events/store/base.py:46-100`); task continuity hashes visible text and tool arguments (`alpha/agents/task_continuity/archive.py:31-40`). | No new provenance store. New records carry IDs/digests for existing event/evidence/artifact owners. |
| `metrics.py` — quality/cost/latency/recovery telemetry | **DELEGATE** | `RunJournal` and the event catalog already own execution/cost/middleware telemetry (`alpha/runtime/journal.py:394-658`; `alpha/runtime/events/catalog.py:58-92`). The token meter, console routes, and ops metrics are existing owners. | No parallel metrics plane. The budget ledger exposes only its own accounting trail; a reported patch publishes summarized events. |
| `limits.py` — hard safety/runtime limits | **EXTEND** | Recursion is Gateway-clamped (`backend/app/gateway/services.py:810-825`); subagent concurrency/total caps are enforced in the lead chain (`alpha/agents/lead_agent/agent.py:723-734`); sandbox/tool limits and emergency stop are existing owners. | The new budget ledger is a local, stricter envelope and a disclosed stop. It cannot relax or bypass any existing limit. |
| `protocols.py` — provider-neutral interfaces/extension contracts | **EXTEND** | Model construction, authorization providers, subagent execution, run events, and extension contributions already expose provider-neutral protocols (`alpha/models/factory.py:259-333`; `alpha/authz/provider.py:85-121`; `alpha/subagents/executor.py:783-809`; `alpha/runtime/events/store/base.py:46-100`). | Add only two narrow protocols required by this scope: a step-level process verifier and a prefix-search adapter. Neither protocol is an implementation. |

## 5. Plan §40 phase mapping

| Plan phase | Verdict | Existing owner and file:line evidence | Scope in this branch |
|---|---|---|---|
| **Phase 0 — contract and inventory** | **NEW** | No equivalent single reasoning contract exists. The current package is limited to the governor/search/ToM modules listed in §3. | This document, typed records, closed enums, and the default-off config contract. Deliberately **no new `AGENTS.md`** (rationale in §9). |
| **Phase 1 — core state, budget, policy, summary, protocols** | **EXTEND** | Existing governor is token-only (`alpha/reasoning/governor.py:30-166`); existing context summarizer is conversation compaction (`alpha/agents/middlewares/summarization_middleware.py:906-938`); existing provider protocols are listed above. | Add the additive contracts. No graph/state-channel wiring in this branch. |
| **Phase 2 — plan/act/observe loop** | **DELEGATE** | The lead loop is the LangChain agent (`alpha/agents/lead_agent/agent.py:1308-1314`), tool authorization is `alpha/authz/adapter.py:27-102`, and observations are journaled in `alpha/runtime/journal.py:394-557`. | Emit-only typed action/observation records and event taxonomy; report the wiring patch. |
| **Phase 3 — evidence and verification** | **DELEGATE** | `alpha/evidence/store.py:53-129`; `alpha/runtime/runs/verification.py:32-73`; `alpha/subagents/acceptance_checks.py:1610-1748`; `alpha/rsi/holdout.py:178-259`. | Supply typed inputs only. No verifier, evidence store, or completion gate. |
| **Phase 4 — reflection, replanning, uncertainty** | **EXTEND** | Reflexion/replan owners: `alpha/learning/reflexion.py:38+`, `alpha/workflow/replanner.py:15-95`, `alpha/orchestrator/loop.py:285-315`. Calibration exists but is not fed live: `alpha/metacognition/calibration.py:54-85` versus `alpha/agents/middlewares/metacognitive_middleware.py:39-53`. | Add bounded reflection records, multi-dimensional uncertainty, and pure loop signals. Existing owners execute/validate. |
| **Phase 5 — native provider reasoning** | **DELEGATE** | `alpha/models/factory.py:259-333`; `alpha/models/fallback.py:202-255`; `alpha/models/system_one.py:365-419`; model authorization at `alpha/agents/lead_agent/agent.py:1049-1073`. | No provider adapter implementation. Policy consumes an explicit capability record and never pretends a local model has native reasoning. A provider-neutral adapter patch is reported. |
| **Phase 6 — context engineering** | **EXTEND** | Summarization and durable context are production owners (`alpha/agents/middlewares/summarization_middleware.py:906-938`; `alpha/agents/middlewares/durable_context_middleware.py:198-312`); task continuity deliberately excludes reasoning/artifacts (`alpha/agents/task_continuity/archive.py:1-5`). | Add AoT contracted-state accounting and report prompt-assembly wiring. No second compactor. |
| **Phase 7 — selective branching** | **EXTEND** | Existing search is `alpha/reasoning/introspective_tree_search.py:183-454`; DWE loop/router policy is `alpha/workflow/runtime.py:607-833` and `alpha/workflow/router.py:21-58`. | Add budgeted TTS regime allocation with the step-verifier honesty gate. No search engine. |
| **Phase 8 — multi-agent reasoning** | **DELEGATE** | Honest council/debate/ensemble: `alpha/deliberation/engine.py:52-236`; peer review: `alpha/deliberation/council.py:59-296`; subagent execution: `alpha/subagents/executor.py:783-809`, `:1307-1338`. | Policy may request diversity; it does not implement or approve subagents. |
| **Phase 9 — reasoning memory** | **EXTEND** | The memory owner contracts live in `alpha/agents/memory/manager.py:345-378`; pre-compaction flush is `alpha/agents/memory/summarization_hook.py:11-28`; review learning is `alpha/learning/review_queue.py:1-22`. The inventory found no durable validated-lesson promotion path, so the seam must be extended rather than replaced. | Produce only validated lesson hand-off records; `should_persist_to_memory` defaults false and the existing memory/review owners decide. Report the patch. |
| **Phase 10 — RSI / self-optimization** | **DELEGATE** | RSI promotion is a real fail-closed seven-gate composition (`alpha/rsi/promotion.py:417-495`), holdout evidence is measured (`alpha/rsi/holdout.py:178-259`), and release gating fails closed on missing metrics (`alpha/benchmarks/release_gate.py:39-53`). | No RSI controller, simulated metric, or self-mutation path. Policy may mark a task as requiring benchmark/rollback; existing gates decide. |

## 6. New-type necessity and default-off status

| New type/function family | Why the inventory requires NEW/EXTEND | Why it cannot activate by itself |
|---|---|---|
| `ReasoningState` + record set | No single structured cognitive control state exists; the current package's search/governor types are unrelated. | Constructed only by explicit host code; no middleware, route, capability entry, or startup registration imports it automatically. |
| `AtomicState` / dependency DAG | No subsystem implements Markovian decomposition/contraction or answer-equivalence preservation. | Pure functions; caps and refusal are explicit. No background precompute loop. |
| `TaskSignals` / `ReasoningPolicy` | Existing classifiers are prompt-regex, multi-vocabulary, or action-discarding; none emits the documented nine-class policy with disclosed under-determination. | Pure and deterministic. No model, tool, or route is called. |
| `BudgetLedger` | Existing budgets are scoped to different owners; no single typed local ledger records all plan dimensions with marginal-value refusal. | Non-singleton instance; cannot grant or execute work. |
| TTS allocation + verifier capability | No interface separates the three research regimes or enforces a step-level verifier. | Interfaces/allocation only; no search implementation. |
| `UncertaintyRecord` / decision | Existing confidence values are not a six-dimension, per-value calibration contract and do not produce the documented decisions. | Returns a recommendation; never transitions state to success. |
| `ReasoningSummary` / guard / persistence helper | No existing user-facing structured reasoning summary and no fail-closed §24 persistence boundary. | Guard runs first; only bounded structured summaries/records/state can be written. |
| `ReasoningEvent` | No closed reasoning event taxonomy exists, and the durable run-event catalog is a separate contract. | Emit-only records plus serialization; no bus or store import. |
| Loop signals/intervention policy | Existing loop detection watches tool-call patterns; it does not expose the plan's same-hypothesis/unchanged-plan/no-evidence/no-verification signals or ordered intervention ladder. | Pure signal/recommendation functions; no watchdog instance, timer, or stop authority. |
| `ReasoningConfig` | The package needs a default-off control surface that cannot import the shared config layer. | `enabled=False`; optional JSON under runtime home; no shared-config import, singleton, or hot-reload registration. |

**Dormancy rule:** every config key is read by a public function in this package; every record type is produced or consumed in-package or by a reported wiring patch; every event type is serializable; no stub class, TODO-only branch, or fake success path is permitted.

## 7. Security, trust, and completion invariants

1. **Private chain-of-thought is never a record.** No schema field exists for raw reasoning tokens, provider thinking blocks, an internal monologue, or an unbounded chain log. `summary.py` is the enforcement point and must fail closed before persistence.
2. **Model-generated reasoning is untrusted data.** Action intents carry an explicit untrusted marker, arguments are digests rather than payloads, and no reasoning record can execute, authorize, or approve.
3. **Existing security remains authoritative.** Authorization (`alpha/authz/adapter.py:27-102`), sandbox execution (`alpha/authz/sandbox_authz.py:124-187`), tool allowlists, approvals, read-before-write, cancellation, ownership, recursion limits, and run budgets are never imported or bypassed by this package.
4. **No premature success.** The package has no completion transition. `UncertaintyAction.STOP` means return to the existing verification/completion owner. Budget refusal, contraction refusal, verifier refusal, and terminal loop recommendations are disclosed statuses.
5. **No raw provider artifacts by default.** Provider reasoning summaries are a capability flag in policy only. This package does not accept or persist provider thinking blocks or provider-authored chains.
6. **Bounded structures.** All record collections and free-text fields are bounded. Guarded persistence is atomic and rejects oversized payloads.

## 8. Disclosed pre-existing integrity risks (not introduced or fixed here)

These are outside this branch's write scope. They are recorded because wiring a reasoning plane to them without repair would violate the plan's honesty contract.

| Risk | Evidence | Consequence for this design |
|---|---|---|
| Registered `moa_multi_model_reasoning` tool never calls a model; it fabricates a per-model "analysis" and uses a hardcoded roster. | `alpha/tools/builtins/moa_reasoning_tool.py:10-34`, especially `:27-33`; registered in `alpha/tools/builtins/__init__.py:112`, `:217`. | Never use as a verifier/consensus source. A separate fix must replace the mock worker with configured-roster model calls or disable the tool. |
| Evidence-matrix tool accepts model-supplied `verified`/`exit_code` and stamps physical-execution certification into a process-global singleton. | `alpha/tools/builtins/evidence_matrix_tool.py:16`, `:30`, `:65-73`; `alpha/verification/evidence/matrix.py:57-58`. | Never wire reasoning evidence to this tool. Prefer `alpha/evidence` and deterministic acceptance checks. |
| Enterprise pipeline marks dependency-ready tasks `completed` with `dod_verified=True` without execution. | `alpha/enterprise/pipeline.py:240-280`, especially `:255-259`. | Do not use the enterprise pipeline as a reasoning verification owner. |
| Surgical APR reports `verification_passed=True` after patch validation without running tests. | `alpha/selfrepair/surgical_apr.py:183-229`, especially `:221-228`. | Do not treat that boolean as process verification. |
| Acceptance verdicts are advisory and do not gate run lifecycle. | `alpha/subagents/acceptance_checks.py` leaves unknown criteria `checked=False`; `alpha/tools/builtins/task_tool.py:1090-1147`; `alpha/runtime/runs/worker.py:1368-1374`. | The new package must not introduce a second, weaker completion path; report adapters into the existing overlay only. |
| Completion critic is intentionally fail-open on missing pipeline, missing message id, retry exhaustion, or critic exception. | `alpha/agents/middlewares/finish_first_verifier_middleware.py:263-306`. | A critic pass/retry cannot be represented as verified completion. |
| Live metacognitive middleware instantiates a fresh monitor per tool result and never feeds the calibration store; `PREMATURE_CONVERGENCE` is unreachable on the live path. | `alpha/agents/middlewares/metacognitive_middleware.py:39-53`; `alpha/metacognition/monitor.py:60-66`, `:103-124`. | Do not claim live calibration or strategy switching from that middleware. |
| Introspective search persists an unbounded `thought_trace` in node projections. | `alpha/reasoning/introspective_tree_search.py:116-125`, `:159-175`, `:292`. | Search must not be treated as §24-safe until the existing trace is replaced by bounded action summaries and guarded persistence. |
| Two in-process watchdog instances, three loop detectors, four council/MoA implementations, and multiple context engines exist. | `alpha/supervision/watchdog.py`; `alpha/agents/middlewares/loop_detection_middleware.py`; `alpha/tools/builtins/supervision_tool.py`; `alpha/supervision/watchdog.py`; `alpha/deliberation/*`; `alpha/context/*` | This package adds no runtime watchdog, MoA engine, council, or compactor. |

## 9. Why this branch does not add `reasoning/AGENTS.md`

The plan asks for a new module guide, but the repository guidance chain is already at measured hard limits in multiple subtrees. The checker defines a 96 KiB hard chain budget and fails chains above it (`scripts/check_agent_guidance.py:13-20`, `:144-163`). Adding another `AGENTS.md` under `packages/harness/alpha/reasoning/` would extend the root + backend + harness chain for every file in this package and could push a different subtree over its hard limit.

This branch therefore uses this document as the module contract and proposes that the central agent add a concise pointer to the existing `backend/packages/harness/alpha/AGENTS.md` **only after measuring the resulting chain size**. The guidance ledgers already record this risk; no guidance markdown is edited here.

## 10. Configuration contract (to be implemented)

`config.py` will provide a default-off `ReasoningConfig` with in-package defaults and an optional operator JSON override at `$AGENT_WORKSPACE_HOME/reasoning/config.json`. It will not import `alpha.config` and will not register a shared config field.

Expected groups:

- `enabled=false`;
- default reasoning mode and explicit policy thresholds;
- multi-dimensional budget defaults;
- TTS regime plus sample/token limits and verifier policy;
- uncertainty enablement/thresholds and calibration requirement;
- bounded summary/guard settings;
- loop-guard caps and cooldown;
- `max_atoms`, `max_depth`, `max_width`;
- optional `storage_path` for guarded structured persistence.

Each key must have a real reader exercised by tests. A malformed operator file fails loudly; an absent file yields defaults.

## 11. Measured Atom-of-Thoughts accounting (to be completed after implementation)

The implementation test will measure the same synthetic multi-step problem twice:

1. per-step input carrying the full structured history; and
2. per-step input carrying only the contracted answer-equivalent atomic state.

The report must disclose the token estimator, per-step and total numbers, break-even step, and the honest limitation that this is a synthetic structural measurement rather than a reproduction of the paper's accuracy results. Short problems may not benefit; if measured, that must be stated.

## 12. Reported wiring (specified later, not applied here)

This branch will specify exact patch text and insertion points for:

1. `ReasoningState` initialization in LangGraph thread/run state;
2. a default-off lead-agent middleware position after memory/skills and before the agent loop, without bypassing security or verification;
3. compact working-set prompt assembly (objective, active step, verified facts, open questions, latest observations, evidence refs, constraints, next-action contract);
4. provider-neutral model-capability mapping with an honest local-model fallback;
5. durable run-event publication coordinated with the existing event catalog/contract;
6. validated-lesson hand-off into the existing memory/review owners;
7. checkpoint contents sufficient to resume without re-deriving the trajectory.

Any required file outside this branch's write scope will be reported, never edited.

## 13. Acceptance gates for the implementation

- Lazy package exports via `alpha.memory._lazy_exports.install_lazy_exports`, preserving the four existing root names and exposing the new config as `ReasoningPlaneConfig`.
- All ten requested modules exist, are default-off/inert without explicit invocation, and have regression tests.
- AoT decomposition is acyclic, deterministic, cap-respecting, constraint-preserving, and contractibly refusable; expansion restores the DAG; token accounting reports measured values.
- Policy boundaries are deterministic; under-determination is disclosed.
- Every budget dimension is enforced; exhaustion is disclosed; marginal-value refusal is tested.
- Prefix-level TTS refuses a missing or fake step verifier; leaf-level sample budgets and shared-budget admission are tested.
- The §24 guard rejects all five prohibited payload classes, accepts the ten legitimate record classes, and runs before any persistence helper.
- Loop signals each fire on a crafted stream; escalation is ordered and hard-capped.
- No import of the shared config module, no Gateway import, no network, no global singleton leakage, and no dependency addition.
- Ruff, duplicate-expression audit, env-wiring audit, guidance-chain comparison, and focused offline tests are reported with exact output.
