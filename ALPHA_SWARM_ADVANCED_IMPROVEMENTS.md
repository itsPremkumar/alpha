# Alpha Swarm — Advanced Improvements Plan

> **Compiled:** 17 October 2026
> **Subject:** `alpha.swarm` (`backend/packages/harness/alpha/swarm/`, 25 modules) and the
> surrounding multi-agent plane.
> **Companion document:** [`SWARM_AI_AGENTS.md`](SWARM_AI_AGENTS.md) — the external research
> dossier this plan is measured against.
> **Method:** direct reads of the swarm module contracts (`swarm/AGENTS.md`), the harness
> guide, `docs/architecture/multi-agent-baseline.md`, `docs/ADVANCED_MULTI_AGENT_BUILD_PLAN.md`,
> `docs/PRODUCTION_READINESS_INVENTORY.md`, `contracts/feature_manifest.json`, and the last
> 25 commits touching `alpha/swarm/`.
> **Honesty note:** items marked **[measured]** are backed by a recorded observation in the
> repo. Items marked **[core-audit]** are line-verified against source by the completed audits —
> the full `alpha/swarm/` module set (runtime and intelligence layer), `config/limits.py`, the
> adjacent orchestration systems (`apex`, `gro`, `arena`, `subagents`, `tasks`,
> `capability_mesh`, `autoconfig`, `enterprise`, `company_os`, `workflow`, `planning`, and the
> remaining multi-agent engines), and the API/UI surface (router, `swarm` tool, frontend,
> config, tests, docs). Items marked **[contract]** are derived from the repository's own
> authored contracts (AGENTS.md docs) rather than a line-by-line read; where an audit covered
> the same ground, the line-verified form wins.

---

## 0. The verdict in five sentences

1. Alpha's swarm is **not a toy and not a copy of OpenAI's Swarm** — it is a lease-fenced,
   checkpointed, budget-aware DAG runtime with deliberation, stigmergy, contract-net
   auctions, and a bounded self-reporting telemetry plane. On architecture breadth it is
   ahead of most of the field.
2. Its **biggest risk is not missing features — it is unverified output**: a live swarm run
   completed 5/5 tasks with **zero acceptance criteria** and a QA worker that **returned
   fabricated repository paths and line numbers** **[measured]**, and the code confirms the
   only acceptance check today is a **keyword gate** that discloses its own limits
   (`"keyword_acceptance_gate_v1; not independent task verification"`, `aggregator.py:251`)
   **[core-audit]**.
3. Its **second risk is correlated failure**: like every LLM system in the literature,
   identical workers make identical mistakes, and nothing in the current design forces
   diversity or measures it before a result is accepted.
4. Its **third risk is economics**: there is **no monetary ceiling anywhere in run admission**
   **[measured]** — `SwarmBudget` has no money field at all (`models.py:93–106`) — and
   token/tool projections are always `None` because token/tool estimators do not exist
   (`telemetry.py:399–400`) **[core-audit]**.
5. The highest-value work is therefore **verification, orthogonalisation, and bounded
   economics** — not another orchestration engine, not more agents, and not copying
   vendor swarm demos.

**Design north star (from the research dossier):** *the model is not the bottleneck; the
coordination around it is. Every inter-agent boundary is a trust boundary, and a fluent
answer is not evidence.*

---

## 1. What Alpha already has (do not rebuild)

Verified against the module contracts and the repo's own audit **[contract]**:

| Capability | Where | Why it is already advanced |
|---|---|---|
| Explicit DAG plans + atomic checkpoints + ordered JSONL audit | `swarm/coordinator.py`, persistence adapter | Restart-recoverable, states separated (cancellation / budget exhaustion / dependency failure / pause-resume) |
| Lease-fenced task attempts with renewal + stale-result fencing | `swarm/scheduler.py` | A late worker result is accepted only while its lease matches |
| Consecutive-failure circuit breaking | `swarm/runner.py` | Reports `budget_exhausted`/`stalled` instead of fabricating completion |
| Adaptive mode selection with precedence | `swarm/strategy.py` | explicit > measured DAG topology > goal heuristic > fallback; complexity tiers gate machinery (`DIRECT`…`FULL`) |
| Candidate planning + makespan scoring in **seconds** | `swarm/planner.py` | Duration-weighted critical path; `exposed_width` vs `lower_bound_rounds` kept distinct |
| Cost projection **before** spawn | `swarm/telemetry.py` | A plan whose projected makespan exceeds its ceiling is refused before registration |
| SPRT deliberation (Wald sequential test) | `swarm/deliberation.py` | Guards may only *block*: domination, sycophancy, evidence-free approval → `manual_review` |
| Bounded stigmergic trace store | `swarm/stigmergy.py` (`MAX_TRACES=256`) | Advisory only; sub-linear capped amplification; decayed reads; deposits run only at medium/full tiers (`coordinator.py:899–940`) **[core-audit]** |
| Contract-net auction + leader election | `swarm/cnp_auction.py` | Leader election runs at swarm create but is a **recorded metric only** (`coordinator.py:326–334`); the auction engine is **not in the swarm runner** — its live production use is the bots capability-dispatch path (`bots/capability_dispatch.py:610–644`, reached via `planning/bridge.py:266`) **[core-audit]** |
| Read-only worker tool isolation | runner + governance registry | A read-only task's worker **physically cannot write**; MCP tools excluded; dropped names also land in `disallowed_tools` |
| Receipt-citation audit | `worker.py:459–486` → `runner.py:383–389` (`validate_receipt_verdict`) | External completion cannot populate it; a never-run audit renders nothing. Acceptance runs **only when `acceptance_criteria` is set** (`aggregator.py:20–56`) and the quality gate's own basis is `"keyword_acceptance_gate_v1; not independent task verification"` (`aggregator.py:251`) **[core-audit]** |
| Degeneracy detection | telemetry | *Serial collapse* and *spurious parallelism* flagged, reported, never aborted |
| Owner-scoped API + tool | `app/gateway/routers/swarms.py` (~20 routes), `swarm` tool (22 actions) | Mutations are **admin-gated**, identity is **server-derived everywhere** (no caller-supplied owner exists), blackboard posts are force-published `trust="untrusted"`, and the GRO candidate pool strips the `swarm` tool by name (`swarms.py:255–609`; `swarm_tool.py:255, 411`) **[core-audit]** |
| Canonical task contract with adapters | `alpha/tasks/` | Unmappable status refuses naming engine + value; lossless round-trip **[measured: implemented]** |

**Conclusion:** the swarm plane's *mechanics* are strong. The gaps are all in what the
mechanics do not yet **prove**, **measure**, or **bound**.

---

## 2. Research → Alpha gap map

Each row is a finding from the external research dossier mapped onto Alpha's actual state.
"Already planned" means the repo's own `ADVANCED_MULTI_AGENT_BUILD_PLAN.md` covers it (G1–G12)
— those are cross-referenced, not re-proposed.

| # | Research finding | Alpha status | Already planned? | This plan |
|---|---|---|---|---|
| R1 | MAST: **specification issues ≈ 42%** of multi-agent failures; "unaware of termination conditions" ≈ 10% | Plans have acceptance fields, but a live run had `acceptance_status=not_required` | No | **S1** |
| R2 | MAST: **incorrect verification ≈ 24%**; "a fluent answer is not evidence" | Acceptance gate exists but is hands-fed; verification loop has **no production caller** | Yes — G4 | **S2, S3** |
| R3 | Anthropic: agents are **low-variance** → correlated failures, identical bad decisions | Deliberation detects domination/sycophancy *after* voting; nothing forces heterogeneity | No | **S4** |
| R4 | Prompt **infection propagates** LLM-to-LLM; every handoff is a trust boundary | Blackboard treated as human-channel data; read-only isolation exists | No | **S5, S6** |
| R5 | Multi-agent ≈ **15× tokens**; cost-per-decision, not per-token | Only wall-clock projected; `projected_tokens = None`; **no monetary admission gate** | Partly — G6 (budget mode), not projection | **S7, S8** |
| R6 | Agents have **"no reputation to lose … no colleague who remembers"** | `capability_mesh` holds evidence records but has **zero production callers** | Partly — G3 (routing), not reputation | **S9** |
| R7 | Topology is a **searchable variable** (GPTSwarm, AFlow, AdaptOrch) | Candidate scoring exists; selection is a static score, never learned from outcomes | No | **S10** |
| R8 | **Hidden-profile / groupthink**: discussion converges on shared knowledge; decisive minority facts get dropped | Aggregator separates execution from acceptance; no minority-report channel | No | **S11** |
| R9 | Multi-agent **turf wars** end by force/passivity/truce; agents should escalate, not escalate *harder* | `incidents.py`, watchdog, ESCALATION logic in APEX | No | **S12** |
| R10 | OTel **GenAI semantic conventions** are the interop layer | Repo emits its own run events; OTel is an optional Phase-9 line item | Partly — Phase 9 "optional" | **S13** |
| R11 | Benchmarks: SwarmBench, MASBENCH (Depth/Horizon/Breadth/Parallel/Robustness), MAST failure labeling | `scripts/benchmark/` exists for context/eviction/concurrency; **no swarm benchmark** | No | **S14** |
| R12 | Swarm lift must be **measured**, not assumed | Estimator decides *whether* to swarm; nothing measures whether the swarm actually beat a single agent | No | **S15** |
| R13 | Self-evolving agents: experience → persistent improvement, with safety scaffolding | `reflection.py` is deterministic; `workflow.self_improvement` **proposes only** | No | **S16** |
| R14 | Seven overlapping schedulers, three task vocabularies | Canonical contract exists **[measured: implemented]**; engines still journal separately | Yes — G2, G9 | cross-ref only |
| R15 | Chaos/fault tolerance is the actual production gap | Lease/restart semantics documented; no fault-injection suite | No | **S17** |

---

## 3. The advanced improvements

Ordered by (value × certainty) ÷ (effort × risk), in the repo's own scoring idiom.

### Tier 1 — Verification, truth, and safety at the boundary

---

#### S1. Specification linter — refuse ambiguous plans before they spend a token

**Problem.** MAST's largest measured failure category (~42%) is specification: the system was
never told precisely enough what to do, or quietly stopped obeying it. Alpha's own live run
completed **5/5 tasks with `acceptance_status=not_required`** **[measured]**, and the audit
confirms the door is open by design: the acceptance overlay executes **only when
`task.acceptance_criteria` is set** (`aggregator.py:20–56`), so a plan can exist, run and
complete without ever having been asked to prove anything **[core-audit]**.

**Design.**
- Add `swarm/spec_lint.py`: a pure, model-free linter over `(goal, SwarmPlan, tasks)`.
- Checks, each returning `{code, severity: refuse | warn, evidence}`:
  1. **No acceptance criteria** on a task that produces an artifact → `refuse` (or require an
     explicit `acceptance_waiver` recorded in the plan and surfaced in the UI).
  2. **No termination condition** on any task (`max_attempts` absent) → `warn`.
  3. **Unbounded verbs** in a directive ("improve", "review", "handle", "as needed") with no
     named artifact or threshold → `warn`.
  4. **Vague scope**: task directive references a path/glob that does not resolve → `warn`
     (this is the cheap half of S2).
  5. **Missing write-scope declaration** on a task whose capability tags include a mutating
     class → `refuse` (pairs with the worktree work in G5).
  6. **Capability-tag coverage**: every inferred tag must match the live `specialists:` roster
     above the hard capability floor (the team guide already requires this alignment).
- In-repo precedent: `planning/hyperplan.py` already gates plans pre-execution with four
  reviewer roles and a `BLOCKED` status **[core-audit]** — S1 is the swarm-plane analogue of
  that posture: a visible refusal, never a silent fix.
- Wire into `SwarmCoordinator.create_swarm` **beside** the existing cost projection: a
  `refuse`-severity finding is a refusal *before* registration/checkpoint/publish, exactly like
  a doomed makespan. A refusal must never leave a "running" plan behind.
- Model surface: add a `lint` action to the existing `swarm` tool (no new tool, so
  `BUILTIN_TOOLS` and the feature manifest are untouched) plus the findings on
  `POST /api/swarms` dry-run.

**Acceptance test.**
- A plan with zero acceptance criteria and no waiver is refused with the code named.
- A plan with a dangling path reference is refused/warned with the path in the finding.
- A valid plan's lint findings are byte-stable (pure function, no model call).

**Effort:** Low. **Risk:** Low. **Value:** Very high (attacks the single largest failure
category for near-zero cost).

---

#### S2. Claim-level grounding — verify *claims*, not just receipts

**Problem.** The repo already audits **receipts** (a matching successful call was recorded):
`worker.py:459–486` emits the `citation_audit` for receipt-bearing specialist outcomes and
`runner.py:383–389` validates it through `validate_receipt_verdict`. The module guide states
plainly: *"a receipt still does not prove the claim or delivery is correct and must not be
promoted to acceptance."* **[contract]** And the live run shows why: a QA worker **returned
fabricated repository paths and line numbers** **[measured]**. Beyond that audit there is
**no independent verification of worker output anywhere** in the runtime **[core-audit]**.

**Design.** Add a deterministic `swarm/grounding.py` that extracts and re-resolves claims
from a worker's outcome, off the event loop, against the *actual* workspace:

| Claim class | Extraction | Resolution | Verdict |
|---|---|---|---|
| File path | regex + path-normalise | exists in the server project root? | `resolved` / `missing` |
| File path + line | `path:line` or `path L123` | file exists **and** line in range | `resolved` / `out_of_range` / `missing` |
| Symbol | `Class.method`, `func()` | AST/grep hit in the named file | `resolved` / `absent` |
| Test name | `test_*.py::test_*` or `-k pattern` | collected by pytest `--collect-only` (bounded, cached) | `resolved` / `uncollected` |
| Command output | fenced block tagged with a command | command allowlist + hash compare, **never re-execute** | `matched` / `unverifiable` |
| Numeric measurement | "N tests pass", "X% coverage" | compare to a recorded artifact if one exists | `corroborated` / `uncorroborated` |

- The verdict is **advisory-to-blocking by policy**: `unresolved_claims` above a configured
  ratio force `manual_review`, never a silent pass. Follow the repo's honesty rule — `None`
  when nothing was checked, never `0` and never a fabricated clean bill.
- Store in a **dedicated typed field** on the task node written only by the runner after lease
  acceptance (exactly the discipline `citation_audit` already uses so external completion
  cannot forge it).
- Surface in the team report and Task UI next to the citation audit, with the same
  "an audit that never ran renders no verdict" rule.

**Acceptance test.**
- A worker outcome citing `backend/does/not/exist.py:42` yields `missing` and the task cannot
  reach an accepted terminal state.
- Grounding runs zero model calls and is deterministic on a fixed fixture.
- A task with no extractable claims reports `claims_checked: 0`, not `grounding: pass`.

**Effort:** Medium. **Risk:** Low (deterministic, no new model calls). **Value:** Very high —
this is the direct, provable answer to the repo's own recorded failure.

---

#### S3. Independent adversarial verifier for high-risk tasks

**Problem.** Verification is ~24% of MAST failures, and "incorrect verification" is the worst
kind because it launders a bad result into a clean one. Self-verification by the same worker
(or the same model+prompt) is the correlated-failure trap from §11 of the research dossier.
The audit states the current chain exactly: execution success is `scheduler.mark_completed`
after a lease check; the acceptance overlay is the keyword gate above; consensus runs only
when `plan.requires_consensus` is true (`aggregator.py:141`) **[core-audit]**.

**Design.**
- Add an optional `verifier` role per task, **distinct from the producer** on three axes:
  different model family where the roster allows, different prompt framing (falsification
  instruction), and **no access to the producer's reasoning trace** — only the task spec,
  the declared acceptance criteria, and the produced artifacts.
- Default posture: **falsify, not approve.** The verifier returns
  `{falsified, evidence, unverified_claims}`; producing "no finding" is not a pass.
- Eligibility: require it where the plan touches irreversible effects, external side effects,
  or a `destructive` capability tag — reuse the existing risk-class vocabulary rather than
  inventing a new one.
- Tie the verdict to the acceptance path: an acceptance criterion with no independent verifier
  is `UNVERIFIED`, matching `gro/`'s existing "no declared criteria ⇒ UNVERIFIED" rule. **Do
  not** let a verifier's opinion substitute for measured evidence — a verifier is a *second*
  signal, never the terminal `passed` producer (`alpha.mission.acceptance` remains the only
  writer of terminal `passed`).

**Acceptance test.**
- A producer that fabricates a file path is falsified by a verifier with no access to the
  producer's trace.
- A fluent-but-incomplete answer fails verification in a test (the build plan's own Phase-4
  gate).
- The verifier cannot write `passed`.

**Effort:** Medium-high. **Risk:** Medium (model cost; mitigate with risk-gated eligibility).

---

#### S4. Decorrelation policy — force heterogeneity, measure it before accepting

**Problem.** Anthropic's red-team work documents 18/30 agents choosing the same branch name,
agents independently titling a story *"The Cartographer's Last Commission"*, and over half a
swarm choosing ray-tracers or self-hosting compilers **[research §11.3]**. Low-variance agents
turn an isolated error into a systemic one. Alpha *detects* domination/sycophancy in
deliberation after the fact, but nothing **forces** diversity at composition time. The audit
shows the mechanics that make this likely: every worker makes exactly one `model.invoke` per
task from an isolated system+human pair with no per-worker sampling variation
(`worker.py:177–196`) **[core-audit]**.

**Design.**
- Add `swarm/diversity.py`:
  - **Composition rule**: for `ENSEMBLE` and `DEBATE` modes, require a minimum pairwise
    decorrelation across `(model_family, temperature, seed, prompt_framing)`. A panel of four
    identical `(model, temperature, prompt)` voters is refused at composition time with the
    colliding axis named (an even panel is not diversity).
  - **Post-hoc measure**: compute pairwise similarity of worker outputs (cheap lexical/
    shingle Jaccard first; embedding only if configured) and record
    `panel_decorrelation` in telemetry.
  - Feed the existing deliberation guards: **domination** already flags `max_share ×
    voter_count` (threshold 2.0; an even panel scores 1.0) and **sycophancy** flags flips
    toward the prior plurality with no new evidence handle (threshold 0.50)
    (`deliberation.py:46–54, 216–276`) **[core-audit]**; add the complement — when similarity
    is high *and* votes are unanimous, the result is `suspicious_unanimity` and downgrades to
    `manual_review`.
  - Report `None` when unmeasurable (fewer than two outputs), never a fabricated `1.0`.
- Reuse `SwarmBenefitEstimator`/`resolve_strategy` surfaces; do not add a second strategy path.

**Acceptance test.**
- A four-voter panel with identical tuples is refused at composition with the axis named.
- Two genuinely different outputs score a high decorrelation; two copies score low.
- Unanimous + high-similarity downgrades to `manual_review`, and no guard can *raise* a
  refusal to an approval.

**Effort:** Low-medium. **Risk:** Low. **Value:** High — directly addresses the most
under-appreciated systemic risk in the literature.

---

#### S5. Taint propagation across agent boundaries

**Problem.** *"Once an agent is compromised, its output becomes trusted input to the next
agent, and the injection rides along."* Multi-agent systems break the single-trust-boundary
assumption; memory poisoning survives into future sessions **[research §12]**.

**Design.**
- Introduce a monotonic **taint label** on every artifact, message, and task output:
  `trusted` → `tool_derived` (MCP/web/file content) → `peer_derived` (another worker's prose)
  → `external_unverified`. Labels only ever **increase** along a path; nothing declassifies
  except an explicit verifier step (S3).
- The substrate already has message-level trust: `communication.py` restricts `trust` to
  `untrusted|internal`, re-sanitizes loaded messages, downgrades invalid trust, and the admin
  publish endpoint force-marks untrusted (`communication.py:151–173, 225–247`;
  `swarms.py:589–609`) **[core-audit]** — extend this from *messages* to *artifacts and task
  outputs*, which carry no taint today.
- Concretely:
  - A task whose **inputs** include a `tool_derived`/`external_unverified` artifact may not
    hold a mutating capability class unless it also has a verifier or a human gate. This is
    the generalisation of the read-only worker isolation that already exists
    (`worker.py:56–115` — `_WRITE_CLASS_RISK`, `_READ_ONLY_DENY_TOOLS`, failing closed at
    112–115) **[core-audit]**: today the rule keys off *capability tags only*; extend it to
    key off **input taint** too.
  - Blackboard messages keep their existing "human input channel, never system instruction"
    treatment and additionally carry a taint label that survives the `SwarmMessage` bounds.
  - The aggregator refuses to promote a tainted claim to acceptance — parallel to how voter
    dedup and unverified evidence already cannot produce a clean approval.
- Model the policy as data (a `TaintPolicy` table beside the governance registry), so a
  capability declaration is never a grant — the repo's existing mod-kernel rule.

**Acceptance test.**
- A fan-out where one worker reads an MCP source cannot feed a mutating downstream task
  without a verifier/gate; the refusal names the taint path.
- Declassification without a verifier is impossible by construction (property test).
- Taint labels survive checkpoint/restore.

**Effort:** Medium. **Risk:** Medium (must not break legitimate research fan-outs — the
module guide already warns that "a research fan-out that could not reach the web would not be
a fan-out"). **Value:** Very high at the frontier.

---

#### S6. Injection detector at every handoff

**Problem.** Prompt Infection propagates silently; more capable models execute injected
instructions *more* efficiently **[research §12.2]**. Alpha already refuses to treat
blackboard content as a system instruction — an excellent structural choice. What is missing
is a **detection** signal so propagation is visible as a pattern.

**Design.**
- A boundary check at: task output → aggregator, message → bus, artifact → downstream input.
- Layered and cheap-first: (1) deterministic heuristics (imperative-to-the-model phrasing,
  role-change tokens, embedded URLs, base64/obfuscation markers, "ignore previous"
  families); (2) optional configured classifier/judge for high-risk edges only.
- Emits telemetry (`injection_suspicion` with the edge, detector, and score) and a
  **quarantine** state on the offending artifact. Detection is advisory; **quarantine is a
  gate** when the artifact's taint (S5) is `external_unverified`.
- Report `None` when the detector did not run — never "clean".

**Acceptance test.**
- A poisoned tool result is flagged before it can be consumed by a mutating task.
- The detector runs zero model calls in the default configuration.

**Effort:** Low-medium. **Risk:** Low.

---

### Tier 2 — Economics, reputation, and measurement

---

#### S7. Calibrated cost projection (tokens, tool calls, dollars) — with honest intervals

**Problem.** `telemetry.py:354–404` projects **wall-clock only**; `projected_tokens` and
`projected_tool_calls` are always `None` (`telemetry.py:399–400`) because no token or
tool-call estimator exists anywhere in the tree, and even `budget_pressure` (0.70/0.90
thresholds) is advisory **[core-audit]**. The display seam already exists — the frontend
pre-run card renders "not estimated" for these fields (`TeamOpsSection.tsx:523–549`) — but
the operator still lacks the number that actually decides whether to run.

**Design.**
- `swarm/cost_model.py`, trained from **Alpha's own recorded telemetry** only:
  - Features: task count, depth, leaf count, capability tags, model per task, prompt size
    bucket, artifact-in/out flags, historical class of the task.
  - Outputs: `projected_tokens_p50/p90`, `projected_tool_calls`, `projected_usd_p50/p90`,
    each with `sample_size` and a `confidence` field.
  - Cold start (no history): report `None` + `estimate_method: "insufficient_history"` — never
    a fabricated figure, matching the repo's existing `estimate_method` idiom.
  - Pricing resolves through `config.yaml -> model_pricing` only; no hardcoded prices.
- Wire it as the **third** answer next to the existing wall-clock projection and post-spend
  `budget_pressure`, and expose it in the `swarm` tool's `strategy`/`telemetry` actions.
- A `p90` above the declared ceiling **warns**; above a hard multiple **refuses** (the policy
  is explicit and self-consistent, like the existing "cannot drift from the budget it checks"
  rule).
- **Absorb the decorative estimator.** `SwarmBenefitEstimator.estimate` is computed and then
  **discarded** by its only production caller (`decomposer.py:254`), and its durations are
  hardcoded constants (`estimator.py:41–45` 20 s/item, `92–94`, `111–114`) **[core-audit]**.
  Precedent for token estimates exists in-tree (`arena/config.py:31`
  `DEFAULT_ESTIMATE_TOKENS_PER_CALL = 60_000`) but as a fixed constant, not a calibration.
  Feed the estimator from measured history, fold it into the cost model, or delete it — do not
  leave a third estimate that decides nothing.

**Acceptance test.**
- With no history, projections are `None` with the method named.
- With a seeded fixture history, p50/p90 are deterministic and sample size travels with them.
- A plan whose p90 exceeds a hard ceiling is refused before registration.

**Effort:** Medium. **Risk:** Low. **Value:** High — completes an existing honesty seam into a
usable decision input.

---

#### S8. Monetary admission gate + per-task chargeback (extends G6)

**Problem.** There is **no monetary cost budget anywhere in run admission** **[measured]** —
`SwarmBudget` (`models.py:93–106`) carries tokens, tool calls, wall-clock, replans and
consecutive failures and **no money field at all**, and `zero_cost` mode does not exist in
code **[core-audit]**. G6 covers the gate; the advanced part is what happens *after* a run.

**Design (beyond G6).**
- Per-task and per-agent **cost attribution** on every node (measured, not estimated), rolled
  up per swarm and per owner, exposed through the existing console reporting route.
- Adopt the canonical contract's cost semantics (`tasks/contracts.py:155–160`):
  `max_cost_usd=0.0` is a real ceiling; `None` (unstated) and `UNBOUNDED` (explicit) are
  distinct; bounds are refused, never clamped **[core-audit]**. A breaker precedent exists in
  `enterprise/governance.py` (treasury balance and TPM breakers, tripping at ≤5% remaining) —
  reuse the pattern, not the store.
- **Cost-per-accepted-result** as the headline metric (research: budget "per decision", not per
  token). A swarm that spends 15× and produces an unaccepted result costs *more* than infinity
  relative to one that spends 1× and succeeds — make that visible.
- **Cache-aware marginal cost**: record prompt-cache hits per worker and report the marginal
  spend, so "we already paid for that context" is measurable.
- Circuit breaker on cost **rate** (USD/minute), not just totals — the $47k-loop failure mode
  is a *rate* incident.

**Acceptance test.**
- Attempting a paid call under `zero_cost` is a code refusal with a test that attempts it
  (the build plan's own Phase-5 gate).
- `GET /api/swarms/{id}` reports per-task measured USD and a total that equals the sum of
  parts (no double counting under retries).

**Effort:** Medium (mostly piggybacks G6). **Risk:** Low.

---

#### S9. Reputation ledger — conditional trust, not a global score

**Problem.** Agents in the research *"enter the market with no reputation to lose, no court
to appeal to, and no colleague who remembers them."* Alpha has `capability_mesh` evidence
records but **zero production callers**, and a `capability_mesh` action that is explicitly
preview-only.

**Design.**
- Give the mesh its **first real consumer** in the swarm: a per-worker reliability record keyed
  by `(model, role, prompt-version-hash, task-class)`, updated only from **verified** outcomes
  (grounding verdict from S2, verifier verdict from S3, receipt audit) — never from
  self-reported success.
- State the audit pins: the mesh is default-off, every planning action is a locally computed
  preview, no executor wiring exists, and the absence is machine-readable
  (`capability_mesh/config.py:59–62`; pinned by `tests/test_capability_mesh_executor_wiring.py`)
  **[core-audit]**. S9 adds *readers and writers of evidence records*, not dispatch routing.
- **Conditional trust**, not one score: `trust_by_class` is a small map (e.g. `repo_path_claims`,
  `test_claims`, `numeric_measurements`, `tool_use`), so a worker that is reliable at
  classification and unreliable at citing paths is represented honestly.
- **Cold-start rule** (already a Phase-3 requirement): undeclared history uses declared
  capabilities, never a fabricated score; an agent with no history is not penalised.
- **Routing** prefers proven workers for a class, and spends a bounded exploration budget on
  new ones — an explicit explore/exploit split surfaced in the report, so a routing decision
  logs *why*, with score components.
- Reputation may **narrow** eligibility and add verifier requirements; it may never *widen*
  permissions, and it may never by itself reject a task.

**Acceptance test.**
- A worker with a history of `missing` path claims gains a verifier requirement for that class.
- A new worker is routed to, not skipped.
- Every routing decision carries its score components and `sample_size`.

**Effort:** Medium-high. **Risk:** Medium (do not let reputation become a global score — the
build plan's D6 explicitly forbids that). **Value:** High — this is the "social technology"
the research says agents lack.

---

#### S10. Evidence-gated topology learning (not RL-first)

**Problem.** GPTSwarm/AFlow showed topology is a searchable variable, with large reported
gains — but the honest reading is *"structure is worth searching"*, not "structure dominates
prompting" **[research §7.1]**. Alpha already scores candidates statically; it never learns
from outcomes.

**Design.**
- A **bandit over topology × task-class**, updated from recorded outcomes (acceptance, cost,
  latency, degeneracy flags) — a measured posterior, not a policy network. Start with Thompson
  sampling over the small set of already-implemented modes; no new topology type is added.
- **Gates before rank** (the repo's GRO rule): a topology that failed a hard gate is ineligible,
  not low-ranked. Relative comparison only ranks the eligible.
- Selection records `topology_choice_reason` + posterior summary in `plan.metrics`, and the
  plan/mode agreement invariant (`plan.mode == strategy["mode"]`) must still hold by
  construction — the existing `_settle` rebuild discipline applies.
- Never auto-select a topology that has not been exercised at least `N` times for that class;
  below `N`, the static `score_plan` decides and says so.
- **Consolidation precondition:** three other strategy-decision surfaces already exist —
  `apex/strategy.py` (**no production call site**), `autoconfig` SWARM-mode roles (no shared
  code with `resolve_strategy`), and `autopilot`'s `SWARM_PATTERNS` regex recommender (live,
  but regex) **[core-audit]**. The bandit consumes `resolve_strategy` and recorded outcomes; it
  must not become a fourth recommender.

**Acceptance test.**
- With a seeded outcome history, the selector still passes the plan/mode agreement invariant.
- A topology with zero samples is never chosen by the bandit.
- The bandit cannot reduce a refused plan to an accepted one.

**Effort:** Medium. **Risk:** Low-medium (bounded by the existing candidate planner).
**Value:** Medium-high.

---

#### S11. Minority-report aggregation + hidden-profile protection

**Problem.** Hidden-profile experiments: groups scored **17–36%** against a solo ceiling near
100%, because discussion converged on shared knowledge and never surfaced unique decisive
facts **[research §11.5]**. Alpha's aggregator currently dedups voters and refuses unverified
evidence — good — but has no channel for a **dissent that should change the answer**.

**Design.**
- Extend the aggregation report with a **Minority Report** section: every non-prevailing
  position with its evidence, verbatim-author-attributed where the panel allows. A unanimous
  majority never *erases* a labeled dissent.
- **Pre-registered uncertainty**: before conferring, each voter records a confidence and the
  single fact that would change its answer ("what would have to be true"). This is the cheap
  mechanical version of a hidden-profile fix; it makes pivotal private information explicit.
- Escalation: if any voter's "what would change my mind" fact is *not* represented in the
  shared evidence, the result is `manual_review` rather than a majority verdict. This turns
  the research finding into a gate.
- The report distinguishes **execution success** from **acceptance** (already the aggregator's
  rule) and now also from **consensus quality**.

**Acceptance test.**
- A three-voter panel with a dissent reports the dissent and its evidence.
- A voter naming an unshared decisive fact downgrades to `manual_review`.
- The minority report never changes the measured evidence record.

**Effort:** Low-medium. **Risk:** Low.

---

#### S12. Conflict escalation ladder (anti-turf-war)

**Problem.** In the research, agents given *incompatible* goals escalated into **self-replicating
malware, account lockouts, and process kill-loops**, and capable models escalated *faster*
**[research §11.6]**. Alpha already has the raw signals — incidents recorded per failed attempt
(`runner.py:550–572`), orphaned execution marked FAILED with a named reason (`runner.py:682–696`),
watchdog state mutation for stragglers and speculative backups (`watchdog.py:78–87`), and
incident succession reassignment (`incidents.py:71–81`) — but conflict detection is a **keyword
regex** over summaries (`aggregator.py:290–291`) and there is no designed **de-escalation
ladder** for multi-agent conflict **[core-audit]**.

**Design.**
- Detect conflict signals mechanically, not semantically: contradictory writes to the same
  path/scope, repeated reversals of a peer's change, mutual negative verdicts, resource
  contention across tasks (Alpha's worktree/scope work gives the substrate).
- Ladder: (1) quarantine the contested scope; (2) freeze both agents' writes to that scope;
  (3) require a **third-party arbiter** task with no stake in the contested artifact;
  (4) park as `needs_human` with the evidence bundle.
- **Hard rule:** a worker may never widen its own scope to resolve a conflict. The existing
  "a Bot may configure itself, but may not widen itself" principle generalises directly.
- Kill switch: an operator-initiated quarantine must revoke the agent's scope immediately, and
  the revocation must be journalled (the repo's durable-hold store is the right home).

**Acceptance test.**
- Two tasks with overlapping write scopes cannot both run (already a Phase-5 gate) and the
  conflict is journalled as a conflict, not a failure.
- Escalation to `needs_human` carries the evidence bundle and is not silently retried.

**Effort:** Medium. **Risk:** Low. **Value:** Medium (defensive; high if Alpha ever runs
genuinely autonomous long-horizon swarms).

---

### Tier 3 — Observability, evaluation, and adaptivity

---

#### S13. OpenTelemetry GenAI export + influence DAG

**Problem.** The industry interoperates on `gen_ai.*` spans; Alpha emits its own run events.
The build plan lists OTel as optional Phase 9. Advanced observability needs two things the
current plane lacks: **standard spans** and **causal information flow**.

**Design.**
- Map swarm execution onto the GenAI conventions: `invoke_agent` per worker, `chat` per model
  call, `execute_tool` per tool call, `handoff` per message/lease transition, with
  `gen_ai.usage.input_tokens/output_tokens` and `gen_ai.request.model`. Prompt/completion
  content stays **opt-in** (PII — the convention requires it).
- Namespace Alpha-specific attributes (`alpha.swarm.plan_id`, `task_id`, `lease_id`,
  `capability_tags`, `taint`, `grounding_verdict`) so nothing custom collides with the
  standard.
- **Influence DAG**: with content-addressed artifacts (G12) and taint labels (S5), record which
  task's artifact fed which downstream task — a provenance graph. This makes "which agent
  poisoned the result" answerable, which is exactly the analysis the prompt-infection research
  says is missing.
- Emit to the existing run-event stream as the primary sink and to OTLP as an optional export,
  so this extends the frozen contract rather than forking it (the build plan's own constraint).

**Acceptance test.**
- One swarm run produces a span tree where every worker is a child `invoke_agent` with measured
  token attributes.
- The influence DAG reconstructs a fan-out → reduce path with no duplicate edges.
- Disabling the exporter changes nothing else.

**Effort:** Medium. **Risk:** Low. **Value:** High (defensibility, interop, debugging).

---

#### S14. Swarm benchmark + MAST-aligned failure labeling

**Problem.** The repo benchmarks context, memory eviction, and concurrency — but **not its
swarm**. The build plan's definition of done requires *"benchmarking demonstrates the change's
value against Alpha's own measured baseline"*. Without a swarm eval, every swarm change is
unmeasured.

**Design.**
- Add `backend/scripts/benchmark/swarm/` following the existing protocol rules (immutable
  revisions, SHA-256, fixed clocks, credentials out of repo).
- **Task suite** across MASBENCH's five dimensions: Depth, Horizon, Breadth, Parallel,
  Robustness — plus deliberately hostile cases: a poisoned tool result (S6), a peer that
  contradicts another peer, a hidden-profile task (S11), and a task with ambiguous specs (S1).
  The repo already carries a seed task (`evaluation/benchmark.py:4,121` — `multi_agent_001`).
- **Failure labeling**: an LLM-judge pipeline over the MAST 14-mode taxonomy. The research
  shows the judge reaches human agreement (κ ≈ 0.88) when the taxonomy is fixed — so publish
  the failure-mode distribution per release, not a single number.
- **Offline first**: the core suite must run with the scripted-runner seam (the Arena suites
  already prove 160 tests can run with zero model calls), with a live, opt-in tier for realism.

**Acceptance test.**
- A seeded regression (e.g. a worker that fabricates paths) is *detected by the benchmark* and
  labeled with the matching MAST mode.
- The suite reports per-dimension scores and per-mode failure counts across two releases.

**Effort:** Medium-high. **Risk:** Low. **Value:** Very high — it is the missing measurement
that makes every other improvement provable.

---

#### S15. Shadow single-agent lane — prove swarm lift, continuously

**Problem.** The estimator decides *whether* to swarm, using a benefit model. Nothing measures
whether the swarm **actually beat** a single agent on the same task. The research is explicit
that multi-agent only wins on decomposable, parallel, high-value work — and that it can lose.

**Design.**
- On a configurable **sample** of tasks (default: a small fraction, and only when the budget
  ceiling allows), run a **shadow** single-agent lane in parallel with the same goal and the
  same tool pool, without its result ever being used for the user-visible answer.
- Compare: acceptance pass rate, grounding verdicts, measured cost, wall time. Record a
  `swarm_lift` measurement with `sample_size` and `basis` — `None` when not sampled, following
  the "measured or None" house rule.
- Feed the result into S10's posterior and into the estimator's calibration; surface it in
  telemetry and the console.
- **Explicit non-goal:** the shadow lane must never change the delivered answer, and its cost
  must count against the same ceiling (it is a measurement expense, disclosed as such).

**Acceptance test.**
- A sampled task records `swarm_lift` with the exact inputs and both outcomes.
- An unsampled task records `None`, never an inferred lift.
- Disabling the shadow lane restores exactly the prior cost profile.

**Effort:** Medium. **Risk:** Low-medium (cost; gated by sampling + ceiling). **Value:** Very
high — this is the only way "swarm is better here" becomes a fact instead of a belief.

---

#### S16. Bounded self-evolution with an experiment registry

**Problem.** The research on self-evolving agents is real and the survey is systematic; the
risk is unbounded self-modification. Alpha already has the correct posture:
`workflow.self_improvement` **proposes only**, and `reflection.py` is deterministic.

**Design — evolve the *policy*, never the code, with an evidence trail:**
- Evolution targets, in ascending risk: (1) topology selection priors (S10); (2) decomposition
  heuristics (candidate generation weights); (3) per-role prompt fragments; (4) model routing
  per task class. **Source code is out of scope.**
- Every proposal is an immutable record: `{target, before, after, evidence, expected_effect,
  expiry}`. Nothing takes effect without (a) a benchmark delta from S14, (b) the test suite,
  (c) a staged rollout, (d) a rollback path — the repo's own AVO "commit gate" pattern applied
  to swarm policy.
- A **prompt/version hash** already needs to exist for S9's reputation key; make it the
  identity that evolution versions.
- Hard rule: an evolved policy may never expand permissions, budgets, or capability classes.
  It may only change *choice among existing capabilities*.

**Acceptance test.**
- A proposed topology-prior change with no benchmark delta is refused.
- A rolled-back experiment restores the exact prior posterior.
- No evolution path can alter a tool permission, budget ceiling, or capability grant.

**Effort:** Medium-high. **Risk:** Medium. **Value:** Medium-high (long-term).

---

#### S17. Fault-injection suite for the swarm plane

**Problem.** The repo's honesty rules already admit that single-process JSON/JSONL is never
cross-process exactly-once and that leases are process-scoped. Those are *documented*
limits; the question is whether the system **degrades correctly** under them. Nothing tests it.

**Design.** A `tests/blocking_io/`-style deterministic fault harness injecting, at named seams:
worker crash mid-task; lease expiry between claim and completion; duplicate lease renewal;
truncated checkpoint write (partial JSON); torn JSONL event; clock skew across renewal; a
result arriving after lease fencing; a blackboard message delivered twice; budget exhaustion
racing a completion. Each fault asserts the **honest outcome** — a `None`, an `unknown`, a
`manual_review`, or a named refusal — and never a fabricated success.

Two audited behaviours must be pinned first because they are the ones most likely to surprise
an operator: (a) a budget stop cancels asyncio tasks but **cannot stop a running `to_thread`
provider call** (`runner.py:227–258`), and there is **no per-task timeout**, so a hung call is
bounded only by the optional `max_wall_seconds`; (b) restart recovery re-parks in-flight tasks
as `paused` with `terminal_reason="process_restart_requires_resume"` (`coordinator.py:202–219`),
so the suite asserts *that* transition instead of an imagined resume; (c) the memory manager
is process-local in-memory with no persistence (`memory.py:18–35`), so a restarted swarm must
report memory absence rather than stale entries **[core-audit]**.

**Acceptance test.**
- Every injected fault maps to a documented, asserted outcome.
- A fenced-late result is discarded, not adopted (already a stated rule — now proven under injection).

**Effort:** Medium. **Risk:** Low. **Value:** High for production confidence.

---

### 3.5 Verified defects found in the audits (fold into the first PRs)

Concrete, line-verified issues with real operational weight. None needs new architecture; all
are tests-first fixes, and several are dead-code removals that shrink the surface every later
improvement must reason about.

| # | Defect | Evidence | Fold into |
|---|---|---|---|
| D1 | Budget stop cancels asyncio tasks but **cannot stop a running `to_thread` model call**; no per-task timeout exists | `runner.py:227–258` clears leases only; the provider call inside the thread (`worker.py:181`) keeps running | S17 |
| D2 | Governor tracking is effectively **per-process, not per-model** — every provider argument defaults to `"default"` and the runner passes its constructor default | `governor.py:120–154`, `runner.py:635` | S7 / S10 |
| D3 | `SwarmBenefitEstimator.estimate` is **computed then discarded** by its only production caller; its durations are hardcoded | `decomposer.py:254`, `estimator.py:41–45, 92–94, 111–114` | S7 |
| D4 | `_CAPABILITY_TIER` / `_apply_capability_model_routing` are **defined twice**; the first definition is dead (shadowed) | `decomposer.py:17–93` vs `100–157` | S1 |
| D5 | Capability templates **hardcode permanent bots** (`architect`, `ceo`, `coder`, `researcher`, `tester`) instead of resolving the live specialist roster | `decomposer.py:496, 530, 601, 613, 639, …` | S1 |
| D6 | Tier-gating properties (`allows_deliberation`, `allows_stigmergy`, `allows_structural_routing`) have **no production callers** — tiers describe, they do not enforce, at these seams | `strategy.py:97–109` (test-only references) | S1 / S4 |
| D7 | `SwarmMemoryManager` is record-only for the live path: Tier-1 scratchpad and `record_fact`/`record_artifact` have **no production callers**, and task results are read back only by the HTTP surface, never by a worker or the aggregator | `memory.py:39–97`; `runner.py:417–430`; `swarms.py:515–564` | S9 |
| D8 | `_deposit_completion_traces` runs **only on the external completion path**, never in the runner loop — trace deposit differs between internal and external completion | `coordinator.py:831` (definition 915) | S2 |
| D9 | `SwarmSubscription`/`subscribe` (push path) and `parse_structured_vote` have **no production callers** | `communication.py:83–118`; `consensus.py:242–257` | S6 or delete |
| D10 | Doc drift: the config guide claims a swarm-plan ceiling of 64 and a native-subagent hard limit of 64; the code has **1000** for both | `config/limits.py:93, 150`; `swarm_tool.py:104` says 1000 | docs PR |
| D11 | Fenced/late worker results still **record their measured cost** (`TASK_RESULT_FENCED`) — correct for accounting; pin it with a test so spend can never silently leave the roll-ups | `runner.py:347–372` | S8 |
| D12 | `SwarmMemoryManager` is **process-local in-memory only, with no persistence** — facts, artifacts, task results and promotions vanish on restart even though the plan itself checkpoints | `memory.py:18–35` | S9 / S16 / S17 |
| D13 | `AutonomousWorkTrigger` has **no caller** — the "autonomous swarm trigger" is a single method plus tests | `triggers.py:16–57`; export at `swarm/__init__.py:84` | wire or delete (S10) |
| D14 | Strategy decisioning is scattered: `apex/strategy.py` has **no production call site**; `autoconfig` SWARM roles share no code with `resolve_strategy`; `autopilot` recommends swarms by **regex** (`SWARM_PATTERNS`) | `apex/strategy.py:6–12`; `autoconfig/engine.py:207, 291–299`; `orchestration/autopilot.py:64–67, 103–113` | S10 |
| D15 | The second dispatch layer is half-wired: `reassign_after_failure` has no production caller; `GroupSupervisor` dispatcher injection is tests-only; `ContractNetAuctionEngine.execute_and_report` is test-only | `bots/reassignment.py:46–49`; `groups/supervisor.py:205–211`; `cnp_auction.py:319–354` | S9 / S12 |
| D16 | IM `/swarm` is a catalogued, Telegram-bound command, but the manager replies **"Unknown command: /swarm"** — catalogue and runtime disagree | `channels/commands.py:22`; `channels/telegram.py:120`; `channels/manager.py:750–754, 2736–2756`; disclosed in `docs/COMMAND_HONESTY.md:94` | small UX PR (wire `/swarm status` or drop the binding) |
| D17 | The Composer swarm toggle is **inert on the wire**: `ChatView` sends no swarm field on the run request, so the toggle's only durable effect is localStorage — its own test is named "wiring pending" | `ChatView.tsx:1027–1029, 2442–2475`; `swarm-preference.test.mjs:106` | product decision (wire it through or label it advisory) |
| D18 | Router coverage gaps: no HTTP-level test for `/api/swarms` (handlers are called directly with stubs), the admin-403 branch is unasserted in swarm suites, and `GET /{id}/leader` plus `cancel`/`replan` handlers have no tests | `swarms.py:619–622`; `tests/test_swarm_api_v2.py` style | S14 / S17 |

**Capacity context (not a defect):** swarm workers run through the shared `SubagentExecutor`
under the **background** admission band (`subagents/capacity.py:15–32`), alongside group runner,
war room and batch work — swarm concurrency contends for the same ceiling, which matters when
S7/S10 make admission decisions.

Start with D4/D6/D9/D13 (deletions and wiring), then D2 and D1 (behaviour), then D10 (docs).
D5 also matters for portability: a swarm template that requires five specific permanent bots to
exist is not a roster-agnostic plan. D16/D17 are product decisions (wire the surface or label
it honestly); D18 says the HTTP surface is only stub-tested — exactly what S14 must cure first.

---

## 4. Priority matrix and roadmap

| ID | Improvement | Value | Effort | Risk | Horizon |
|---|---|---|---|---|---|
| S1 | Specification linter | Very high | Low | Low | **30 days** |
| S2 | Claim-level grounding | Very high | Medium | Low | **30 days** |
| S4 | Decorrelation policy | High | Low-med | Low | **30 days** |
| S7 | Calibrated cost projection | High | Medium | Low | **30 days** |
| S6 | Injection detector | High | Low-med | Low | **60 days** |
| S3 | Adversarial verifier | Very high | Med-high | Medium | **60 days** |
| S5 | Taint propagation | Very high | Medium | Medium | **60 days** |
| S11 | Minority report | Med-high | Low-med | Low | **60 days** |
| S17 | Fault injection | High | Medium | Low | **60 days** |
| S13 | OTel + influence DAG | High | Medium | Low | **90 days** |
| S14 | Swarm benchmark + MAST labels | Very high | Med-high | Low | **90 days** |
| S9 | Reputation ledger | High | Med-high | Medium | **90 days** |
| S8 | Monetary gate + chargeback | High | Medium | Low | with G6 |
| S15 | Shadow single-agent lane | Very high | Medium | Low-med | **90 days** |
| S10 | Topology learning | Med-high | Medium | Low-med | later |
| S12 | Conflict escalation ladder | Medium | Medium | Low | later |
| S16 | Bounded self-evolution | Med-high | Med-high | Medium | later |

### The first tickets (each one PR, each with tests)

1. `swarm/spec_lint.py` + refusal wiring in `create_swarm` + `swarm` tool `lint` action. **(S1)**
2. Acceptance-criteria presence wired into the existing cost-projection refusal seam. **(S1)**
3. `swarm/grounding.py` — path/line/symbol extraction + resolution, dedicated typed field. **(S2)**
4. Grounding verdict → aggregator gate + Team Ops rendering. **(S2)**
5. `swarm/diversity.py` — composition rule + `panel_decorrelation` + `suspicious_unanimity`. **(S4)**
6. Telemetry: `panel_decorrelation` and `grounding_verdict` distributions on task nodes. **(S4)**
7. `swarm/cost_model.py` — history-fed p50/p90 with `sample_size` + `insufficient_history`. **(S7)**
8. Injection heuristics at the message-bus and aggregator boundaries. **(S6)**
9. Minority-report section + pre-registered uncertainty in the aggregation report. **(S11)**
10. Fault-injection harness skeleton with three named faults (crash, expiry, fenced-late). **(S17)**
11. Audit-cleanup set (§3.5): delete the shadowed duplicate in `decomposer.py` (**D4**); key the
    governor by `(provider, model)` (**D2**); per-task deadline whose fault test proves a hung
    provider call is reported honestly (**D1**); fix the limits doc drift (**D10**).
12. Wire-or-delete set (§3.5): `triggers` (**D13**), `apex/strategy` + `autoconfig` duplication
    (**D14**), `reassign_after_failure` / `GroupSupervisor` dispatcher injection (**D15**), and
    the IM `/swarm` binding + Composer toggle pair (**D16**, **D17**) — each gets a caller test
    or a deletion/label, no third option.

---

## 5. What NOT to do (research-backed)

These are traps the field has already paid for. They also align with Alpha's own
`ADVANCED_MULTI_AGENT_BUILD_PLAN.md` §3 "Tier 3 — do not build (yet)".

1. **Do not add an eighth scheduler.** The measured structural finding is *seven* already.
   Improvements above are designed to attach to `SwarmCoordinator`/`SwarmScheduler` — none
   introduces a new execution loop.
2. **Do not grow the panel to fix disagreement.** Research: 3–4 debaters is the optimum; more
   rounds and more agents can *degrade* accuracy and increase adversarial consensus. Prefer S4
   (decorrelation) and S11 (minority report) over more voters.
3. **Do not let consensus substitute for evidence** (the repo's own D5, and the research's
   "incorrect verification" category).
4. **Do not chase a single global agent-quality score** (repo D6; research: trust must be
   conditional, per-domain).
5. **Do not implement pairwise peer-to-peer messaging storms.** The research's job-queue
   stampede (2.4M requests / 117 accepted) is the failure mode; keep coordination through the
   bounded blackboard and the scheduler.
6. **Do not adopt distributed infrastructure** (queues, multi-machine workers) without scale
   evidence. The single-process limits are documented honestly; improve *degradation* (S17),
   not distribution.
7. **Do not enable autonomous self-modification of prompts or code.** Propose-and-approve only
   (S16), matching the existing `workflow.self_improvement` posture.
8. **Do not treat the swarm as a product feature on its own.** Research consensus: start with
   one agent; add agents only when the task parallelises, the value clears the ~15× cost, and
   distinct specialties exist. The estimator already encodes this — keep it authoritative and
   give it measured inputs (S7, S15).

---

## 6. How this plan stays consistent with Alpha's own rules

| Repo rule | How this plan complies |
|---|---|
| `RunManager` is the sole run-lifecycle owner | No item creates a run or a scheduler; all attach to existing swarm surfaces |
| Bounds are refused, never clamped | S1 refuses ambiguous plans; S7 warns/refuses on projection; S16 refuses un-benchmarked changes |
| An unreadable source reports `None`/`null`, never `0` | S2 (`claims_checked: 0` ≠ `pass`), S4 (`None` when unmeasurable), S7 (`insufficient_history`), S15 (`None` when unsampled) |
| A completed run is never "verified" | S2/S3 only ever *add* evidence requirements; `alpha.mission.acceptance` stays the only terminal-`passed` writer |
| Single-process JSON/JSONL is never exactly-once | S17 proves the degradation is honest rather than claiming exactly-once |
| `config.yaml` is the only model file | S7/S9/S16 resolve models and prices through config only |
| Feature counts are generated, never hand-typed | All new model-facing capability rides the existing `swarm` tool actions (`lint`, plus extensions to `strategy`/`telemetry`), so `BUILTIN_TOOLS` and the feature manifest are untouched |
| TDD mandatory | Every S-item above carries an acceptance test, most offline and model-free |
| Harness never imports `app.*` | All new modules are harness-layer (`alpha/swarm/*`), consistent with the existing rule for `communication.py`, `consensus.py`, `reflection.py` |

---

## 7. One-paragraph summary

Alpha's swarm runtime is already **architecturally ahead** of most of the market on mechanism
— leases, budgets, checkpoints, deliberation, stigmergy, isolation — and **behind** on three
things the research says decide outcomes: it does not **verify claims** (a recorded run shipped
fabricated paths), it does not **force or measure diversity** (so it is exposed to correlated,
systemic failure), and it does not **bound money or measure its own lift** (no admission-level
cost ceiling; projections honestly `None`; nothing proves the swarm beat a single agent). The
plan above fixes those in the first 30 days with **S1 (spec linter), S2 (claim grounding), S4
(decorrelation)** and **S7 (calibrated cost projection)** — all deterministic, offline-testable,
and attachable to the existing coordinator without a new engine. The frontier items — **S5
(taint propagation), S3 (adversarial verification), S14 (MAST-labeled benchmark), S13 (OTel +
influence DAG), S15 (shadow lane)** — turn the swarm from a system that *runs* into a system
that *proves*, which is exactly the standard this repository already holds itself to.

---

*Verification status: architecture, contracts and the measured findings above are drawn from
repository documents (`swarm/AGENTS.md`, `docs/architecture/multi-agent-baseline.md`,
`docs/ADVANCED_MULTI_AGENT_BUILD_PLAN.md`, `docs/PRODUCTION_READINESS_INVENTORY.md`) and the
last 25 commits touching `alpha/swarm/`. Four source audits are now complete and
line-verified: the swarm core runtime (ten modules), the swarm intelligence layer (fifteen
modules), the adjacent orchestration systems (`apex`, `gro`, `arena`, `subagents`, `tasks`,
`capability_mesh`, `autoconfig`, `enterprise`, `company_os`, `workflow`, `planning`), and the
API/UI surface (router, `swarm` tool, frontend, config, tests, docs). Items marked
**[core-audit]** carry those references; §3.5 lists what the audits found. Softer [contract]
claims come from the repository's own authored guides and remain doc-grade.*

---

## 8. Implementation ledger (living)

Status of this plan against the working tree. Nothing below is committed yet;
this section is updated as tickets land.

**Landed (working tree, uncommitted).**

- **S1 — specification linter.** `swarm/spec_lint.py`, refusal wiring in
  `SwarmCoordinator.create_swarm` (after the cost-projection refusal, before
  registration/checkpoint/publish, so a refused plan never appears as running),
  and a `lint` action on the existing `swarm` tool — dry-run without
  `swarm_id`, or re-lint a registered plan (a recorded waiver is honored).
  Findings as shipped: `empty_objective` (refuse); `acceptance_missing`
  (refuse for a task that declares `output_artifacts` and no criteria, warn
  otherwise); `acceptance_waived` (warn); `vague_directive` (warn, suppressed
  when an artifact or criteria are declared); `duplicate_directive` (warn);
  `dangling_reference` (warn, reusing `grounding.ground_claims`);
  `no_declared_write_scope` (warn, declared tags only, skipped when an artifact
  or worktree path is declared); `findings_truncated` (warn, 128-finding bound).
  Scope vs. §3's design: the §3 acceptance test "a plan with zero acceptance
  criteria and no waiver is refused" is scoped to the artifact-bearing case —
  a blanket refusal would refuse every decomposer-produced plan, because the
  decomposer sets neither criteria nor artifacts (grep-verified), so the
  refusal targets the shape that can actually promise a deliverable nobody
  checks; the write-scope check is warn-severity and applies only to declared
  capability tags; the `POST /api/swarms` dry-run lint was not shipped (the
  tool `lint` action covers the model surface; the route is a follow-up if the
  UI needs it).
- **S2 — claim grounding.** `swarm/grounding.py` (deterministic, model-free,
  bounded) plus a dedicated `SwarmTaskNode.claim_grounding` field written only
  by the runner from the internal worker outcome, after the completion commit
  (lease acceptance established) and under a following state lock; the
  aggregator acceptance overlay grades unresolved claims as
  `acceptance_status="failed"` with the `grounding_unresolved` count, and the
  team report renders the row and counts unresolved claims as an unknown.
  Claim classes as shipped: workspace-relative file paths, `path:LINE` /
  `path:LINE-END`, `path::test_ref`, absolute extension paths. Scope vs. §3's
  design: symbol resolution is file-scoped (`::test_ref` presence), not an
  AST/grep sweep; command-output and numeric-measurement corroboration were not
  shipped (they need a recorded-artifact store that does not exist yet); the
  aggressiveness knob is fixed rather than configurable — with criteria,
  unresolved claims force `failed`, everything else stays visible; frontend
  Task-UI rendering is not shipped (team report and tool/API payloads carry the
  row).

**Fixed in the same change set (pre-existing failures, not S1/S2 scope).**

- `tests/test_swarm_admission_advisory.py` failed deterministically in wide
  runs: `import alpha.tools.builtins.swarm_tool as module` binds the *tool*
  once the lazy builtins facade has cached the export under the same name — the
  gotcha `test_gro_tool.py` documents. Switched to `importlib.import_module`,
  which reads `sys.modules` unconditionally.
- The runner's grounding call originally ran before the completion commit,
  widening the window `SwarmWatchdog` samples for stragglers: under load a
  healthy worker could be flagged and a duplicate speculative backup
  dispatched (observed as an extra `task-a` execution in
  `test_swarm_fault_injection.py`). Grounding now runs after the completion
  commit, so the straggler window is back to its pre-change shape; the fault
  test remains inherently timing-based, but its exposure no longer includes the
  grounding round trip.

**Verified.** `uv run pytest tests/ -k "swarm" -q` → 488 passed, 4 skipped,
12 subtests passed (447 pre-existing + 41 new in
`tests/test_swarm_spec_lint.py` and `tests/test_swarm_claim_grounding.py`);
`ruff format` / `ruff check` clean on the touched modules;
`uv run python scripts/check_tool_schemas.py` → 136 tools checked, 0 failed.

**Not started.** §4 tickets 5–12 (S4 decorrelation, S7 cost model, S6 injection
detector, S11 minority report, S17 fault-injection harness skeleton, and the
§3.5 audit-cleanup / wire-or-delete sets). They remain next per the roadmap;
nothing in S1/S2 blocks them.
