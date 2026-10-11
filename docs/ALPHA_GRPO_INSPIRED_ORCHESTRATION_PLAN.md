# Alpha: GRPO-Inspired Group-Relative Agent Orchestration

**Implementation plan for the existing Python agent harness/orchestrator — no model training, no model replacement, no LLM fine-tuning**

- **Target project:** Alpha AI Agent (`github.com/itsPremkumar/alpha`)
- **Implementation boundary:** Runtime orchestration, candidate execution, evaluation, selection, safe result synthesis, observability, and recovery.
- **Core idea:** For a task where one attempt may be unreliable, Alpha creates a bounded group of meaningfully different candidate plans or execution attempts, evaluates them using evidence, compares them, then selects or carefully combines the strongest verified outcome.
- **Working name:** **Group-Relative Orchestration (GRO)** — a GRPO-inspired orchestration pattern. This document intentionally does **not** call the runtime algorithm actual GRPO training.
- **Status:** Architecture and staged implementation specification. Proposed module names and API contracts must be mapped to Alpha's actual package tree before implementation.

---

## 1. Executive summary

Alpha can use the central *idea* behind Group Relative Policy Optimization (GRPO) without training a model: generate a group of alternatives for the same task, score the alternatives against a shared objective, and use their relative performance to make an execution decision. The harness—not the model weights—does the optimization at runtime.

For example, when Alpha receives “fix the failing API tests,” it should not immediately trust the first implementation proposed by one agent. It can create several bounded approaches from the same repository snapshot:

1. **Minimal-fix candidate:** inspect the failing tests and make the smallest plausible repair.
2. **Root-cause candidate:** trace the failing call through the relevant code path before changing anything.
3. **Test-first candidate:** reproduce the failure, add or identify a regression test, then repair it.
4. **Independent reviewer/repair candidate:** inspect the first candidates' evidence and try a different fix, if the budget allows.

Each attempt works in an isolated worktree or sandbox. Alpha evaluates candidate results using test results, patch validity, regression checks, task requirements, safety policy, and recorded evidence. Unsafe or invalid candidates are rejected regardless of their relative rank. Among candidates that pass the hard gates, Alpha chooses the strongest result under a task-specific scoring policy. If it merges useful work from more than one candidate, it must re-run validation against the merged result before publishing or committing anything.

This is a *runtime search-and-selection layer*. It does not update LLM weights, create a reward model, require a new provider, or require a training GPU. It can run above Alpha's existing model adapters, tool system, single-agent mode, bot mode, subagents, and swarm mode.

### The main principle

> **Do not merely ask more agents. Create independent candidates, measure outcomes, reject invalid results, and use a controlled selector to choose the best verified result.**

This design is informed by the group-relative comparison idea in the [DeepSeekMath GRPO paper](https://arxiv.org/abs/2402.03300), research on [test-time compute scaling for language agents](https://arxiv.org/abs/2506.12928), research on [sampling and verification](https://proceedings.mlr.press/v267/zhao25a.html), and practical agent-harness guidance on [evaluation](https://developers.openai.com/api/docs/guides/evaluation-best-practices), [orchestration](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/), and [durable graph execution](https://langchain-ai.github.io/langgraph/tutorials/introduction/). These sources support related design principles; they do not claim that a runtime selector is itself GRPO training.

---

## 2. Scope: what this is and what it is not

### In scope

- Runtime group generation for plans, tool strategies, task decompositions, specialist-agent attempts, and repair attempts.
- Task-specific scoring based on observable evidence.
- Relative comparison among candidates addressing the same task from a comparable starting point.
- Candidate isolation, bounded parallelism, budgets, timeouts, retries, cancellation, and early stopping.
- Independent verification and safe selection.
- Optional evidence-grounded result synthesis after candidate evaluation.
- Candidate traces, event logs, diagnostics, and benchmark tests.
- Integration with current Alpha orchestration and agent registry without replacing model adapters.

### Explicitly out of scope

- Implementing the GRPO policy-gradient objective.
- Updating model weights, backpropagation, SFT, LoRA/QLoRA, or any fine-tuning pipeline.
- Training a reward model or critic model.
- Replacing the current LLM or provider layer.
- Claiming that the system “learns a new model” because it stores traces or improves its runtime decisions.
- Blindly voting among agents, averaging opinions, or selecting the longest/more confident response.
- Automatically applying every candidate's changes to a shared workspace.

### Correct terminology

| Term | Meaning in this plan |
|---|---|
| **Actual GRPO** | A reinforcement-learning training method that optimizes a policy using group-relative advantages. It involves model optimization and is out of scope. |
| **GRO / GRPO-inspired orchestration** | Alpha's runtime process for generating, evaluating, comparing, and selecting candidate plans/results. No model-weight updates. |
| **Candidate** | A bounded plan or execution attempt that can be independently evaluated. |
| **Candidate group** | Alternatives for the same normalized task under a compatible context, evaluator, and budget policy. |
| **Verifier** | Deterministic tests or evidence checks that estimate whether a candidate actually satisfied requirements. |
| **Absolute quality** | A candidate's task-specific quality and pass/fail conditions. |
| **Relative standing** | How a candidate compares with its group. Useful as a selector signal, but never proof of correctness by itself. |
| **Experience record** | A trace of what happened. It can support debugging and future benchmark creation; it is not model training. |

### Why the distinction matters

In training-time GRPO, group-relative advantages contribute to a policy-gradient update. In Alpha's runtime version, relative values are only orchestration metadata used for routing, ranking, early stopping, or deciding whether another attempt is worth its cost. Do **not** implement the GRPO loss, call runtime score normalization “training,” or claim Alpha's LLM becomes intrinsically more capable. Alpha's system behavior can improve because its harness searches and verifies more intelligently while the underlying LLM stays unchanged.

---

## 3. Design objective

Build a dependable **candidate tournament** into Alpha's existing harness while preserving its current features and execution paths.

The system should answer six questions for each eligible task:

1. **Should this task use one attempt or multiple candidates?**
2. **Which distinct strategies or specialists should produce candidates?**
3. **How can the candidates execute independently and safely?**
4. **What evidence shows that each candidate has or has not succeeded?**
5. **Which candidate is safe and sufficiently correct to select?**
6. **When should Alpha stop, try another candidate, ask the user, or report failure?**

### Non-negotiable invariants

1. Existing Alpha behavior remains available as the baseline and fallback.
2. Every candidate gets the same normalized user goal and a documented starting snapshot; differences are explicit strategy choices, not hidden context differences.
3. Every candidate has a unique ID, trace, budget, working directory/worktree, status, and outcome record.
4. Relative rank never overrides a hard safety failure, failed mandatory test, invalid schema, or missing required evidence.
5. The system may report “no verified solution.” It must not force a winner when all candidates fail.
6. A model's claim that it completed a task is not the same as verified completion.
7. Only the selected, validated result can reach a commit/apply/publish step.
8. Side-effectful tools must be controlled by policy and ideally staged until selection.
9. Parallel work is bounded by resource limits, API limits, and task risk.
10. If the GRO layer errors, Alpha falls back to the existing single-attempt path or returns an explicit failure; it must not silently claim success.

---

## 4. Where the idea applies inside Alpha

This pattern belongs at the **orchestration/control plane**. It should be callable by the current single-agent, bot, subagent, and swarm modes rather than becoming a separate agent product.

### 4.1 Candidate planning

For difficult or ambiguous tasks, ask the existing model/agent to produce several structured plans with different approaches. The planner does not execute them yet. A deterministic validator checks structure, dependencies, permitted tools, risk, and estimated cost. The orchestrator can execute one plan for a low-risk task or select a small group for harder tasks.

Useful for:
- Multi-step research.
- Large coding tasks.
- Architecture choices.
- Debugging with several plausible root causes.
- Ambiguous tasks where different decomposition strategies may work.

Do not use three prompts that are practically identical and call them “diversity.” Define explicit strategy templates and measure whether candidates meaningfully differ.

### 4.2 Parallel independent execution

Create multiple execution lanes for the same goal. Each lane can use the same model or a different existing model/agent profile, but model changes are optional. The execution harness controls workspace isolation, tool permissions, execution limits, and trace collection.

Useful for:
- Code changes in independent worktrees.
- Research from different search/query strategies.
- Data transformation checked against a schema.
- Finding root causes with different diagnostic approaches.
- API troubleshooting where several safe hypotheses can be tested.

### 4.3 Specialist-agent competition

Use agents already registered in Alpha—for example, researcher, architect, implementer, tester, debugger, security reviewer, or documentation specialist—as candidate producers. Assign each an explicit role and the same task contract. Compare their *deliverables and evidence*, not their persuasiveness.

One candidate might be a full team workflow; another might be a lean workflow. A candidate group does not have to contain individual agent messages only. It can contain complete execution trajectories.

### 4.4 Tool-strategy selection

For some tasks, the central uncertainty is not the answer but the tool or sequence of tools. Generate viable execution strategies—such as search-then-read versus repository-wide search, unit-test-first versus trace-first, or API schema inspection versus controlled request reproduction. Let the harness compare the outcomes.

Tool choice can use historical reliability, but the first implementation should use simple measured statistics and policy rules. Do not create an opaque self-modifying routing system before the underlying evaluations are trustworthy.

### 4.5 Self-correction and repair rounds

If all candidates fail, collect the actual error/evaluation evidence and create a *new candidate group* with a changed strategy. Do not repeat the same request indefinitely. The failure record should identify which gate failed, what was tried, and what must change in the next round.

Examples:
- Test failure → pass the failing test output to a repair candidate.
- Tool timeout → choose a bounded retry or alternate safe tool.
- Conflicting research claims → launch a targeted evidence-check candidate.
- Partial task completion → spawn a candidate only for the missing acceptance criteria.

Set a maximum number of rounds and a total cost/time budget. If the budget is exhausted, stop and report what remains unverified.

### 4.6 Evidence-grounded answer synthesis

For research and explanation tasks, candidates may return different useful pieces. The harness can build a merged answer **only from claim/evidence records that survive validation**. Preserve citations, source URLs, timestamps, contradictions, and uncertainty. Require a final verifier to check that every material claim is supported and that incompatible claims are not collapsed into an artificial consensus.

A synthesis agent is a new candidate, not an omniscient arbiter. The final answer must be checked against the original task requirements and evidence bundle.

### 4.7 Candidate-based recovery

When an agent or tool fails, distinguish a transient infrastructure error from a bad strategy. Retry the same candidate only for retryable failures. Spawn a different candidate for a strategy failure. Route authorization, safety, missing credentials, and user-consent errors to a safe blocked state rather than repeatedly retrying.

### 4.8 When *not* to use candidate groups

Use the normal path for:
- Greetings and straightforward informational answers.
- Deterministic tasks with one validated procedure.
- Tasks where parallel candidates add no new evidence.
- High-risk destructive actions before user approval.
- Tasks with scarce API quotas or expensive model calls unless the user explicitly approves the added budget.
- Workflows involving non-idempotent external side effects that cannot be isolated or staged.

A good orchestrator spends extra compute only when expected improvement justifies it.

---

## 5. Proposed architecture

Keep the existing Alpha execution engine and add a small control-plane layer around it. Exact directory names should be adapted after inspecting the current repository; the names below describe responsibilities, not an assertion about existing files.

```text
User / UI / API / Telegram
          |
          v
  Existing Alpha request entry
          |
          v
  Task normalizer + risk classifier
          |
          v
  GRO policy / compute-budget planner
          |  (single attempt OR candidate group)
          v
  Candidate factory / strategy registry
          |
          v
  Candidate coordinator / scheduler
     |        |         |        |
  Lane A    Lane B    Lane C   Lane D
  isolated  isolated  isolated isolated
  context   context   context  context
  + tools   + tools   + tools  + tools
     |        |         |        |
     +--------+---------+--------+
                   |
                   v
        Outcome + evidence collector
                   |
                   v
      Hard gates + task-specific verifiers
                   |
                   v
         Absolute score + relative ranking
                   |
                   v
      Selector / optional synthesis candidate
                   |
                   v
       Final integrated verification gate
                   |
                   v
      Commit/apply/publish OR verified answer
                   |
                   v
       User result + trace + audit record
```

### Component responsibilities

| Component | Responsibility | Must not do |
|---|---|---|
| `TaskNormalizer` | Creates a stable task contract and acceptance criteria. | Silently change the user's request. |
| `RiskClassifier` | Assigns task type, risk tier, side-effect profile, and verification level. | Use risk classification as authorization to perform dangerous actions. |
| `GROPolicy` | Decides whether to use one attempt, several candidates, or a repair round. | Spawn unlimited agents. |
| `StrategyRegistry` | Stores named, versioned strategy templates. | Duplicate the same strategy under misleading names. |
| `CandidateFactory` | Creates candidate run specifications from one task/context snapshot. | Give candidates inconsistent requirements unintentionally. |
| `CandidateCoordinator` | Schedules lanes, manages deadlines, quotas, cancellation, and status. | Allow a candidate to mutate another candidate's workspace. |
| `ExecutionAdapter` | Reuses Alpha's existing agent/model/tool execution interfaces. | Introduce a second LLM abstraction just for GRO. |
| `EvidenceCollector` | Captures tool outputs, test artifacts, citations, diffs, errors, and completion markers. | Treat untrusted model text as trusted evidence. |
| `VerifierRegistry` | Chooses deterministic and optional semantic verifiers for task types. | Let an LLM judge override mandatory tests or safety controls. |
| `CandidateScorer` | Computes task-specific absolute score and diagnostic relative metrics. | Use one universal score for every task type. |
| `CandidateSelector` | Chooses from hard-gate-passing candidates under policy. | Select the “least bad” failure as a success. |
| `SynthesisRunner` | Optionally synthesizes validated complementary findings. | Merge code patches without integrated tests. |
| `PromotionGate` | Controls commit, apply, or external side effects. | Publish a candidate before final validation. |
| `TraceStore` | Persists candidate group events and outcomes. | Store secrets or unrestricted raw sensitive data. |

### Integration rule

Reuse Alpha's current model clients, tool registry, agent profiles, context/memory adapters, task graph, execution workers, and logging wherever possible. Do not fork these into a parallel implementation. GRO should provide a new *policy and coordination option* above the present runtime, with an adapter interface to keep it removable and testable.

---

## 6. Candidate group semantics

### 6.1 What belongs in one group?

Candidates are comparable only when they share the same:

- Normalized user goal and acceptance criteria.
- Initial repository/document/data snapshot or a documented equivalent.
- Security policy and permission envelope.
- Relevant context and evidence availability.
- Task-specific verifier set and scoring version.
- Budget class, or a documented cost-normalization rule.

They can differ in:
- Strategy, decomposition, tool sequence, and reasoning approach.
- Existing agent role or profile.
- Model/provider if already supported by Alpha and allowed by user policy.
- Search query formulation or code-debugging method.
- Number of intermediate steps, within resource limits.

If context differs significantly, do not naïvely rank the candidates as though it were a controlled group. Record the difference or create separate cohorts.

### 6.2 Candidate independence

Independence is an engineering property, not merely a prompt instruction. Use isolated workspaces for code candidates and avoid shared mutable task state. Candidate lanes may share read-only cached documentation or immutable task inputs, but writes should go into isolated artifacts. For research, retain per-candidate source sets so one candidate's unsupported claim does not become another's “evidence” through shared conversation.

Use common starting snapshots; collect common baseline test results before the candidate race. A candidate can read earlier observations only when the strategy explicitly calls for review/repair, in which case mark it as a *dependent revision* rather than an independent candidate in the original cohort.

### 6.3 Candidate diversity

Implement a `StrategySpec` with fields such as:

```yaml
id: coding.root_cause_first
version: 1
purpose: Diagnose the cause before editing
applicable_task_types: [coding, debugging]
required_capabilities: [repository_read, test_runner]
forbidden_capabilities: [production_write, external_publish]
steps:
  - inspect_task_and_tests
  - trace_failing_path
  - state_root_cause_hypothesis
  - implement_minimal_patch
  - run_targeted_tests
  - run_regression_tests
success_signals:
  - mandatory_tests_pass
  - diff_is_valid
```

Candidate factory policy should choose genuinely different applicable strategies. For an easy task, one candidate is sufficient. For a hard task, start with a small diverse group. Avoid using randomness as the only source of diversity.

### 6.4 How many candidates?

There is no universally correct number. Make group size a configuration/budget decision, not a hard-coded claim.

| Task tier | Suggested initial policy | Typical rationale |
|---|---|---|
| Tier 0 — trivial/fully deterministic | 1 attempt | Extra candidates have low expected value. |
| Tier 1 — ordinary, low risk | 1–2 candidates only when uncertainty is material | Cheap recovery or comparison. |
| Tier 2 — multi-step or ambiguous | 2–3 candidates | Explore distinct strategies without uncontrolled fan-out. |
| Tier 3 — complex coding/research/debugging | 3–4 candidates initially; expand only if justified | Diversified attempts plus stronger validation. |
| Tier 4 — destructive/high impact | Do not automatically expand actions; use read-only planning and human authorization | More agents do not make a hazardous action safe. |

These are starting values for experiments, not universal recommendations. On a memory-constrained local machine, run candidates sequentially if they share one local model/runtime; concurrency can otherwise exhaust RAM/VRAM. Concurrent execution is useful only when compute, tool quotas, and isolation allow it.

---

## 7. The selection algorithm: relative comparison with absolute gates

### 7.1 The critical design choice

Do **not** choose solely by z-score, group mean, majority vote, confidence, verbosity, or an LLM judge. The candidate group might be uniformly poor. A response can be “best in group” and still be unacceptable.

Use two layers:

1. **Absolute quality and safety gates** decide whether a candidate is eligible.
2. **Relative comparison** helps rank eligible candidates, identify outliers, decide whether another attempt is useful, and estimate the value of additional compute.

### 7.2 Task-specific outcome model

Represent every candidate outcome as structured data:

```json
{
  "candidate_id": "cand_abc123",
  "task_id": "task_xyz789",
  "status": "completed",
  "hard_gates": {
    "safety_pass": true,
    "required_tests_pass": true,
    "schema_valid": true,
    "required_evidence_present": true,
    "policy_compliant": true
  },
  "metrics": {
    "criteria_passed": 5,
    "criteria_total": 5,
    "tests_passed": 42,
    "tests_failed": 0,
    "regressions": 0,
    "tool_failures": 0,
    "unsupported_claims": 0,
    "latency_ms": 48200,
    "estimated_cost_units": 3.2
  },
  "artifacts": ["patch.diff", "pytest-report.xml"],
  "verifier_version": "coding-verifier-v1"
}
```

This is illustrative; the actual schema should be versioned and adapted to Alpha's persistence layer.

### 7.3 Absolute task score

Use a task-type-specific score only after running required hard gates. Example for coding:

- Mandatory test suite passes: hard gate.
- Patch applies cleanly: hard gate.
- No prohibited file changes: hard gate.
- Explicit acceptance criteria coverage: 0–100.
- Regression test coverage and regression results: task-specific metric.
- Code quality/lint/type checks: metric or gate depending on project policy.
- Minimality/maintainability: secondary metric, not a substitute for correctness.
- Runtime/cost: efficiency metric, not correctness.

Example for research:
- Required answer coverage.
- Citation URL validity and source relevance.
- Claim-to-source support checks.
- Contradiction detection.
- Recency requirements if the task is time-sensitive.
- Clear uncertainty and missing-information disclosure.

The exact weights belong in a versioned evaluator configuration per task category. Do not use one hard-coded global score for coding, research, planning, email, and external action tasks.

### 7.4 Relative comparison

After the hard gates and absolute evaluation, calculate diagnostic group-relative values. A simple form is:

\[
R_i = Q_i - \operatorname{median}(Q_1, \ldots, Q_n)
\]

where `Q_i` is the candidate's absolute quality score. A standardized measure can be used when score distributions and group size justify it, but it needs a variance floor and careful handling of tiny groups. In the first version, **rank and score margin are easier to debug than a formal z-score**.

Use relative values for:
- Choosing among candidates that pass required gates.
- Detecting whether one strategy consistently underperforms in a benchmark cohort.
- Deciding whether another candidate is likely to add value.
- Learning which candidate strategies deserve more or less runtime budget from measured outcomes.
- Choosing which failed candidate has the most informative diagnostic artifact for repair.

Do not treat relative values as gradients, model advantages, or a policy update. They only influence orchestration decisions.

### 7.5 Multi-objective selection

A single scalar often hides trade-offs. Candidate A might be correct but expensive; candidate B might be faster but miss a criterion. Use this priority order:

1. Hard-gate eligibility.
2. Required acceptance criteria and correctness.
3. Safety and policy compliance.
4. Evidence quality and reproducibility.
5. Regression risk and maintainability.
6. Latency, tokens, API cost, local memory, and total work.

For some task types, maintain a Pareto set: candidates not dominated simultaneously on correctness, safety, and cost. Make a final deterministic choice with configured policy. Cost must not compensate for unsafe behavior or failed mandatory requirements.

### 7.6 Tie handling

If eligible candidates have effectively equivalent scores:
- Prefer the one with stronger external verification/evidence.
- Then prefer the smaller/simpler diff or shorter dependency chain where appropriate.
- Then prefer lower cost/latency.
- Preserve both artifacts for traceability.

If no candidate passes mandatory gates, do not return the highest-scoring one as a verified success. Start a bounded repair round or return an explicit partial/failure status.

---

## 8. Task lifecycle and state machine

Use a durable state machine rather than a single opaque async function. Proposed states:

```text
RECEIVED
  -> NORMALIZED
  -> POLICY_DECIDED
  -> GROUP_CREATED (or SINGLE_ATTEMPT)
  -> READY
  -> RUNNING
  -> VERIFYING
  -> RANKED
  -> SELECTED (or NO_ACCEPTABLE_CANDIDATE)
  -> FINAL_VERIFYING
  -> PROMOTED / DELIVERED

Any non-terminal state can transition to:
  PAUSED / CANCELLED / BLOCKED / TIMED_OUT / FAILED

NO_ACCEPTABLE_CANDIDATE
  -> REPAIR_GROUP (if budget remains)
  -> NEEDS_HUMAN / PARTIAL_RESULT / FAILED
```

Terminal states must be explicit. Never interpret a lost worker, missing event, process restart, or HTTP timeout as successful completion.

### State transition requirements

- Every transition records timestamp, actor/component, reason, and relevant trace IDs.
- Retries must be idempotent and must not duplicate external side effects.
- Cancellation propagates to every candidate process and child agent.
- Durable checkpoints persist completed work and verified artifacts, not only chat text.
- A restarted worker resumes only from a known checkpoint and revalidates stale or environment-dependent evidence.
- A candidate that loses its worker lease cannot continue promoting changes.

LangGraph's docs provide a useful reference for durable orchestration and composable retries/timeouts/error handlers, whether Alpha adopts LangGraph primitives or implements its own compatible state machine: [LangGraph overview](https://langchain-ai.github.io/langgraph/tutorials/introduction/) and [fault tolerance](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/fault-tolerance.mdx).

---

## 9. End-to-end runtime algorithm

### Phase A — Normalize and classify

1. Preserve the raw user request and produce a normalized task contract.
2. Extract desired final artifact/result, acceptance criteria, constraints, deadline, and user approval requirements.
3. Classify task type: coding, research, data work, planning, tool operation, or mixed.
4. Classify action risk and external side effects.
5. Identify available tools and required verifiers.
6. Estimate expected value of multiple candidates compared with cost.
7. Choose `single`, `candidate_group`, or `human_approval_first` mode.

### Phase B — Plan candidate budget

1. Determine group size from task tier, uncertainty, system load, provider limits, and deadline.
2. Select distinct strategy specs.
3. Set a total task budget and per-candidate budget for time, model calls/tokens, tool calls, subprocesses, retries, and disk space.
4. Reserve system resources and enforce global concurrency limits.
5. If budget is insufficient, use a single candidate or ask for consent rather than silently exceeding it.

### Phase C — Freeze the task context

1. Capture a task snapshot: repository commit/worktree base, input files, context version, tool registry version, policy version, and evaluator version.
2. Give each candidate the same normalized objective and acceptance criteria.
3. Keep sensitive memory/credentials filtered by capability permissions.
4. Create isolated workspaces or immutable result folders.
5. Record which inputs were shared and which were intentionally different.

### Phase D — Execute candidates

1. Run each candidate under a controlled worker.
2. Capture LLM requests/responses at an appropriate privacy level, tool calls/results, stdout/stderr, subprocess exit code, diffs, browser steps, and errors.
3. Apply per-step timeouts and a global deadline.
4. On transient failure, retry only under the configured retry policy.
5. Do not allow one candidate to commit to the user's repository or trigger side effects simply because it finished first.
6. Support cooperative cancellation when the stopping policy determines enough evidence exists.

### Phase E — Verify independently

1. Parse the candidate's declared output and artifacts.
2. Execute deterministic validators in a restricted environment.
3. Run the relevant task-specific tests.
4. Check the required acceptance criteria.
5. Verify evidence and source links for research tasks.
6. Check safety/policy violations and unauthorized side effects.
7. Record verifier results, versions, and artifact paths.
8. Mark each required gate `pass`, `fail`, or `unknown`; do not collapse `unknown` into `pass`.

### Phase F — Compare and decide

1. Remove candidates that fail hard gates from promotion eligibility.
2. Score the remaining candidates using the appropriate evaluator.
3. Calculate relative rank, score margins, and uncertainty diagnostics within the group.
4. If one candidate passes and no other candidate can plausibly add value within the remaining budget, select it.
5. If several are competitive, use a configured verifier or start one targeted comparison/review pass.
6. If different candidates contain complementary evidence, prepare a synthesis candidate only when that adds value.
7. If all fail, decide between a bounded repair group, escalation, partial delivery, or explicit failure.

### Phase G — Final validation and promotion

1. Copy/commit only the selected candidate artifact into a staging branch/worktree.
2. Re-run relevant tests on the *final staged/merged artifact*, not only on the original candidate workspace.
3. Check changed-file allowlists and prohibited side effects.
4. Require human approval for configured risky actions.
5. Promote, deliver, or publish only after the final gate passes.
6. Save the trace and summarize the evidence, the candidate chosen, important trade-offs, and any uncertainty.

### Phase H — Runtime feedback (not model learning)

1. Update metrics about strategy success, cost, latency, failure categories, and verifier outcomes.
2. Use a bounded, transparent policy to adjust future group size or strategy priorities from observed outcomes.
3. Keep changes versioned and reversible.
4. Do not infer that a single lucky success proves a strategy is generally better.
5. Do not alter safety policy automatically based on reward/score.

---

## 10. Orchestration pseudocode

The following is a design sketch, not a drop-in implementation. It assumes Alpha already has adapters for agent execution, tools, and task persistence.

```python
async def run_with_group_relative_orchestration(task, runtime):
    contract = await runtime.normalizer.normalize(task)
    policy = await runtime.policy.decide(contract, runtime.system_state())

    if policy.mode == "single":
        return await runtime.baseline_runner.run(contract)

    if policy.requires_human_approval:
        return await runtime.approvals.request_before_execution(contract, policy)

    snapshot = await runtime.snapshots.freeze(contract)
    strategies = runtime.strategies.select_diverse(
        task_type=contract.task_type,
        allowed_tools=policy.allowed_tools,
        count=policy.group_size,
    )
    group = await runtime.groups.create(
        contract=contract,
        snapshot=snapshot,
        strategies=strategies,
        budget=policy.budget,
        evaluator_version=policy.evaluator_version,
    )

    outcomes = await runtime.coordinator.run_bounded(group)
    evaluations = await runtime.verifiers.evaluate_group(contract, outcomes)
    eligible = [e for e in evaluations if e.hard_gates_pass]

    if not eligible:
        if policy.repair_rounds_remaining > 0:
            failure_bundle = runtime.failure_analyzer.summarize(evaluations)
            repaired_task = contract.with_failure_evidence(failure_bundle)
            return await runtime.repair_policy.start_bounded_round(
                repaired_task, policy, runtime
            )
        return runtime.results.unverified_or_failed(contract, evaluations)

    ranked = runtime.selector.rank(
        candidates=eligible,
        absolute_metrics=True,
        relative_comparison=True,
        cost_policy=policy.cost_policy,
    )
    selected = ranked[0]

    if runtime.synthesis.should_run(ranked, policy):
        synthesis = await runtime.synthesis.create_candidate(
            contract=contract,
            selected_candidates=runtime.synthesis.complementary(ranked),
            snapshot=snapshot,
            budget=policy.synthesis_budget,
        )
        synthesis_eval = await runtime.verifiers.evaluate_one(contract, synthesis)
        if synthesis_eval.hard_gates_pass and runtime.selector.is_better(
            synthesis_eval, selected, policy
        ):
            selected = synthesis_eval

    staged = await runtime.promotion.stage(selected.artifacts, snapshot)
    final_check = await runtime.verifiers.verify_staged(contract, staged)
    if not final_check.hard_gates_pass:
        return runtime.results.failed_final_gate(contract, selected, final_check)

    result = await runtime.promotion.deliver_or_commit(staged, policy)
    await runtime.traces.finish(group.id, evaluations, selected, final_check)
    return result
```

### Required engineering corrections before production

- Put all state-changing transitions behind transactions/leases or equivalent concurrency controls.
- Use typed models rather than unvalidated dictionaries at internal boundaries.
- Ensure worker cancellation and subprocess termination are implemented and tested.
- Add structured exception handling and classify retryable vs non-retryable errors.
- Make staging and promotion idempotent.
- Guarantee trace completion even when a worker process crashes.
- Use explicit user approval, not model output, as the authority for restricted operations.

---

## 11. Strategy registry and initial strategy catalogue

Start with a small, reviewable catalogue. Do not create dozens of strategies before measuring them.

### Coding and debugging

| Strategy ID | Approach | Strong verification signals |
|---|---|---|
| `coding.minimal_patch` | Inspect failure and change as little as possible. | Targeted tests, regression tests, valid diff. |
| `coding.root_cause_first` | Trace call path and state a testable root-cause hypothesis first. | Reproduced failure, hypothesis matches evidence, tests pass. |
| `coding.test_first` | Identify/add regression coverage before the repair. | New test fails before patch and passes after, when practical. |
| `coding.static_analysis_first` | Use lint/type/static tools before deep edits. | Static checks and test suite. |
| `coding.alternate_design` | Explore a distinct implementation only when justified. | Same acceptance tests, complexity policy, migration safety. |
| `coding.repair_from_failure` | Start from structured verifier output and repair only failed criteria. | Previously failing gates now pass; no new regression. |
| `coding.independent_review` | Review candidate patches without making edits to their workspaces. | Finds real issues or confirms via reproducible checks. |

### Research and web/tool tasks

| Strategy ID | Approach | Strong verification signals |
|---|---|---|
| `research.broad_then_narrow` | Map the topic, then inspect authoritative sources. | Relevant primary sources, coverage, source dates. |
| `research.primary_sources_first` | Prefer papers, official docs, repositories, specifications. | Claims trace to direct sources. |
| `research.contradiction_hunt` | Search for counterevidence and disagreement. | Conflicts are recorded rather than hidden. |
| `research.independent_query` | Use different search terms/source paths. | Incremental source coverage, not duplicated links. |
| `research.fact_check` | Verify material claims from candidate drafts. | Claim-to-source support table, unsupported claims removed. |

### Planning and tool operation

| Strategy ID | Approach | Strong verification signals |
|---|---|---|
| `plan.dependency_first` | Identify prerequisites and blocked dependencies first. | Dependency graph is acyclic where expected; feasible schedule. |
| `plan.milestone_first` | Partition into milestones and acceptance criteria. | Each milestone has a testable exit condition. |
| `tools.read_only_diagnosis` | Inspect state before executing changes. | Evidence gathered without external mutation. |
| `tools.preview_then_apply` | Dry run or stage effects; apply only after verification/approval. | Preview matches intended changes. |
| `tools.alternate_safe_path` | Select another allowed tool/path after a retryable failure. | Explicit failure reason and validated outcome. |

Strategies are data/configuration plus tested adapters. Avoid arbitrary dynamically generated shell commands as the only definition of a strategy. Keep system permissions separate from prompts.

---

## 12. Verification and evaluator design

The evaluator is the most important part of the system. More candidates are not useful if Alpha cannot distinguish correct from incorrect outcomes.

### 12.1 A verifier ladder

Use the strongest affordable verification available for the task:

1. **Structural checks:** JSON schema, required fields, artifact existence, parseable patch, exit status.
2. **Deterministic checks:** unit tests, integration tests, type checks, linters, database constraints, URL status checks where appropriate.
3. **Requirement checks:** all explicit acceptance criteria have evidence.
4. **Independent evidence review:** another agent or verifier checks the artifacts without relying solely on the candidate's explanation.
5. **Semantic quality evaluation:** only where deterministic checks cannot fully cover the task; judge outputs against a rubric and supplied evidence.
6. **Human approval:** required for high-impact, destructive, irreversible, financial, security-sensitive, or externally visible actions according to policy.

The upper layers supplement the lower layers; they do not replace them. An LLM judge should never be the sole validator of a code patch when tests can be run.

### 12.2 Evaluator versioning

Every evaluation record should store:
- Evaluator ID and version.
- Rubric/policy version.
- Test command and environment metadata.
- Input and artifact hashes where feasible.
- Pass/fail/unknown plus machine-readable reasons.
- Timestamp and environment freshness constraints.

If an evaluator changes, record a new version and re-run a fixed benchmark set. Do not compare raw scores from incompatible evaluator versions as if they were directly equivalent.

### 12.3 LLM judge limitations

An LLM judge can be biased by verbosity, confidence, familiar style, shared model errors, or one candidate's framing. Reduce this risk by:
- Hiding candidate identity/strategy when practical.
- Giving the judge the original task, rubric, and evidence—not unsupported self-evaluations.
- Asking for criterion-level judgments and quotes/locations from evidence.
- Including adversarial test cases and human spot checks.
- Measuring judge-human agreement on a sample.
- Treating judge output as one metric with uncertainty, not as ground truth.

### 12.4 Research verifier

For research tasks, collect claims as records, e.g.:

```json
{
  "claim_id": "claim_01",
  "claim": "The library supports durable execution",
  "source_url": "https://example.org/docs",
  "source_title": "Official documentation",
  "source_published_at": null,
  "retrieved_at": "2026-10-10T12:00:00Z",
  "support_excerpt": "...",
  "support_status": "supported",
  "confidence": 0.88
}
```

Do not fabricate source dates or excerpts. `confidence` is not a substitute for checking source content. Mark weak or conflicting support as such and preserve it for final disclosure.

### 12.5 Coding verifier

For a coding candidate:
- Validate patch syntax and that it applies to its own base revision.
- Enforce allowed/forbidden file paths.
- Run targeted failing tests, then the relevant regression suite.
- Run configured lint/type/security checks.
- Record all commands, exit codes, duration, and truncated/full output references.
- Detect “tests pass because tests were deleted/disabled” and suspicious changes to test configuration.
- Inspect whether the patch includes unrelated changes or hard-coded shortcuts.
- Re-run mandatory checks on the final staged artifact.

A test pass is evidence, not proof of total correctness. Preserve the baseline and identify tests that were not run.

---

## 13. Safe execution, isolation, and side effects

Multi-candidate execution amplifies both throughput and risk. Treat each lane as an untrusted worker that can make mistakes.

### 13.1 Workspace isolation

For coding tasks, use one of:
- Git worktrees created from the same base commit.
- Separate disposable clones.
- Filesystem/container sandboxes with explicit read/write scopes.

Never let two candidates edit the same working directory concurrently. Store outputs and logs under a per-task/per-candidate folder. On Windows, ensure process groups and child subprocesses can be terminated; on Linux/Docker, use container or namespace limits where available. The abstraction should work across operating systems without requiring Docker for every lightweight task.

### 13.2 Tool permission profiles

A candidate receives only the capability set required for the task. Examples:
- `read_only`: repository/search/browser reads only.
- `local_write_sandbox`: can edit isolated files, run allowlisted tests.
- `network_research`: permitted web/search access, no shell write unless separately allowed.
- `staged_external_action`: may prepare an action but cannot commit it.
- `approved_apply`: allowed only after explicit policy/approval checks.

Do not trust the LLM to enforce these permissions through prompt instructions. Enforce at the tool gateway.

### 13.3 External effects

Email sending, publishing, deployment, deleting files, production database changes, purchases, and other non-idempotent actions must not be launched by every candidate. Candidate lanes prepare proposed actions in a staging form. The selected action passes policy and, when required, human approval. Only one promotion path may execute it, guarded by an idempotency key.

### 13.4 Resource budgets

Set per-candidate and global limits:
- Wall-clock time and deadline.
- LLM/model calls and token budget.
- Tool call count.
- Subprocess count/runtime.
- Workspace size and artifact size.
- Concurrent workers and local model sessions.
- API quota and expected monetary cost.
- Retry count and repair-round count.

The coordinator must have a queue and backpressure. When the machine is under pressure, reduce concurrency or use sequential lanes before allowing runaway memory/CPU use. Budget exhaustion is a visible state, not a hidden failure.

### 13.5 Prompt injection and untrusted outputs

Retrieved web pages, repository files, terminal output, issue descriptions, and candidate artifacts are untrusted inputs. A candidate may propose tool calls; the orchestrator enforces policy. One candidate's output must not be able to grant permissions to another candidate. Citation/source text does not become a trusted system instruction. Sanitize logs and omit credentials and sensitive memory from stored traces.

---

## 14. Adaptive compute, early stopping, and repair

The goal is not to maximize the number of agents. It is to spend enough effort to obtain a verified answer while avoiding needless cost.

### 14.1 Start small, expand only on evidence

Initial policy:
- Trivial task: single attempt.
- Routine task with some uncertainty: one baseline attempt; create another only on a failure/low-quality result or when task policy explicitly requests competition.
- Complex task: small diverse group.
- All candidates fail: derive a failure bundle and start a bounded repair group if there is budget.

### 14.2 Early stopping rules

Stop creating or continuing candidates when one or more of the following is true:
- One candidate passes all mandatory gates and exceeds a configured absolute-quality threshold, and remaining candidates have low expected value.
- Remaining time/cost budget is below the minimum viable retry budget.
- A safety, authorization, or data-integrity barrier is encountered.
- All candidate lanes repeatedly hit the same infrastructure problem.
- New attempts are duplicating the same evidence without improving the result.
- The task requires user clarification or external approval.

Do not early-stop merely because one candidate claims high confidence. Require actual evidence.

### 14.3 Failure-directed repair

Create a structured failure bundle:
- Failed gate and criterion.
- Actual error/test output.
- Candidate's attempted changes and diff.
- Relevant environment details.
- Tools that failed and whether the failure is retryable.
- Changes tried already, to avoid loops.
- Explicit next-round goal.

The repair group must use a different strategy or act on new evidence. Track a fingerprint of repeated failures. If the same failure signature appears beyond the configured cap, stop and report it instead of looping.

### 14.4 Candidate disagreement

Disagreement itself is not a quality metric. Use it to trigger verification:
- For factual disagreement, seek primary-source evidence.
- For code design disagreement, run tests or make a small benchmark when possible.
- For plan disagreement, compare dependencies, risk, completeness, and resource estimates.
- If evidence cannot resolve it, present the trade-off and uncertainty to the user.

### 14.5 “Best answer” versus “best work product”

For text tasks, the output may be a response. For code tasks, the output is a validated patch and its test evidence. For browser or API tasks, it may be a structured result with verified state. The candidate selector must use the task's artifact contract; it must not optimize only for the last natural-language message.

---

## 15. Multi-agent graph integration

Alpha already supports or is intended to support multiple agents, subagents, bot mode, and swarm mode. GRO should work *inside* those execution patterns.

### 15.1 Graph pattern

```text
START
  -> classify_task
  -> decide_compute_policy
  -> [single_runner | create_candidate_group]
  -> candidate_execution_lanes (bounded fan-out)
  -> collect_outcomes
  -> verify_each_candidate
  -> gate_and_rank
  -> [repair_group | synthesis | select]
  -> final_integrated_verification
  -> [human_approval | promotion | return_unverified]
  -> END
```

Keep the control graph mostly deterministic. Use the LLM for open-ended planning, candidate content, and evidence synthesis; use code for limits, transitions, permissions, eligibility gates, and state consistency. This follows the general value of combining deterministic workflow nodes with agent-driven work described in the [LangGraph overview](https://langchain-ai.github.io/langgraph/tutorials/introduction/).

### 15.2 Graph fan-out controls

- Set a maximum number of active candidate lanes per task and globally.
- Set a maximum child-agent depth and total descendants.
- Use task IDs and candidate IDs in every event.
- Propagate cancellation and deadlines through subagents.
- Require a contract for agent handoffs: objective, input artifacts, permitted tools, expected output schema, deadline, and exit conditions.
- Detect repeated agent handoffs and circular delegation.
- Ensure one candidate's final response cannot terminate sibling candidates without coordinator authorization.

### 15.3 Shared specialists versus candidate specialists

There are two valid patterns:

**Independent teams:** each candidate has its own planner/executor/tester chain. Better for independent comparison; higher cost.

**Shared review graph:** several candidate outputs flow to one common verifier/reviewer. Lower cost and more consistent grading, but can introduce correlated judgment bias and bottlenecks.

Support both as explicit configuration. For high-stakes code changes, prefer independent execution plus deterministic tests and an independent review step. The reviewer should inspect candidate artifacts, not merely read their self-reported summaries.

### 15.4 Graph node contracts

Every node should declare:
- Typed inputs and outputs.
- Timeout and retry policy.
- Idempotency behavior.
- Permitted side effects.
- Error classification and recovery handler.
- Trace/event fields.
- Completion condition.

Use shared fault-tolerance utilities rather than implementing a different retry loop in every agent.

---

## 16. Persistence and data model

Start with Alpha's current storage. If no appropriate store exists, SQLite is a reasonable single-machine starting point; move to a server database only when multi-worker concurrency or operational requirements justify it. The system should not require a new vector database just to compare candidates.

### 16.1 Minimum entities

#### `orchestration_runs`
- `run_id` primary key
- `task_id`, `conversation_id`, `parent_run_id`
- `task_type`, `risk_tier`, `mode`
- `normalized_goal`, `acceptance_criteria_json`
- `policy_version`, `strategy_registry_version`, `evaluator_version`
- `base_snapshot_id`, `status`, `created_at`, `updated_at`
- `deadline_at`, `budget_json`

#### `candidate_runs`
- `candidate_id` primary key
- `run_id`, `strategy_id`, `strategy_version`
- `agent_profile_id`, `provider/model identifier` (only if already exposed safely)
- `workspace_id`, `status`, `attempt_number`
- `started_at`, `finished_at`, `duration_ms`
- `token/cost estimate`, `tool_call_count`, `retry_count`
- `artifact_manifest_json`, `error_summary`, `trace_id`

#### `candidate_evaluations`
- `evaluation_id`, `candidate_id`
- `evaluator_id`, `evaluator_version`
- `hard_gates_json`, `metrics_json`, `absolute_score`
- `relative_rank`, `relative_margin` (diagnostic only)
- `eligible_for_promotion`, `reason_codes_json`
- `evaluated_at`, `evidence_manifest_json`

#### `orchestration_events`
- `event_id`, `run_id`, optional `candidate_id`
- `sequence_number`, `timestamp`, `event_type`
- `component`, `severity`, `message_template`, `safe_fields_json`
- `correlation_id`, optional `artifact_ref`

#### `promotion_records`
- `promotion_id`, `run_id`, `candidate_id`
- `staged_artifact_hash`, `final_verification_id`
- `approval_required`, `approval_status`
- `promotion_status`, `idempotency_key`, timestamps

### 16.2 Storage requirements

- Use migrations; do not mutate schema ad hoc in production.
- Use foreign keys and indexes for `run_id`, `candidate_id`, status, and timestamps.
- Store large logs and artifacts outside ordinary JSON columns; keep references and hashes in the database.
- Redact secrets before persistence.
- Configure retention limits for raw logs, browser screenshots, large diffs, and tool output.
- Preserve enough evidence to reproduce selection outcomes.
- Store a stable scoring breakdown rather than only a final score.
- Separate event records from mutable “current status” records.

---

## 17. Suggested Python module boundaries

Map these responsibilities onto Alpha's actual package tree rather than blindly adding every proposed module.

```text
alpha/
  orchestration/
    policy.py             # choose single vs group, budget, risk tier
    task_contract.py      # normalized objective and acceptance criteria
    candidate_factory.py  # create candidate specifications
    coordinator.py        # bounded scheduling, deadline, cancellation
    state_machine.py      # legal transitions and recovery
    selector.py           # hard-gate eligibility and candidate ranking
    synthesis.py          # optional evidence-grounded merge candidate
    promotion.py          # staged artifact verification and final apply
  strategies/
    registry.py           # strategy specs and versioning
    coding.py
    research.py
    planning.py
  evaluation/
    registry.py
    common.py
    coding.py
    research.py
    safety.py
    scorecards.py
  execution/
    adapter.py            # wraps existing Alpha executor/agent loop
    workspace.py          # isolated worktrees/sandboxes
    budget.py             # resource/cost accounting
    process_control.py    # subprocess timeout/cancellation
  observability/
    events.py
    trace.py
    metrics.py
  persistence/
    models.py
    repository.py
    migrations/
```

Prefer fewer files until real responsibilities demand separation. The important boundary is between **execution**, **verification**, **selection**, and **promotion**. Do not bury all four inside the supervisor agent prompt.

### Recommended interfaces

```python
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    run_id: str
    strategy_id: str
    strategy_version: str
    task_contract: dict[str, Any]
    snapshot_id: str
    allowed_capabilities: frozenset[str]
    budget: dict[str, Any]

@dataclass
class CandidateOutcome:
    candidate_id: str
    status: str
    artifacts: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    metrics: dict[str, Any]
    error: dict[str, Any] | None = None

@dataclass
class CandidateEvaluation:
    candidate_id: str
    hard_gates_pass: bool
    gate_results: dict[str, str]
    absolute_score: float | None
    metric_breakdown: dict[str, float]
    reason_codes: list[str]

class ExecutionAdapter(Protocol):
    async def run(self, spec: CandidateSpec) -> CandidateOutcome: ...

class CandidateVerifier(Protocol):
    async def evaluate(
        self, task_contract: dict[str, Any], outcome: CandidateOutcome
    ) -> CandidateEvaluation: ...
```

Production code should use Alpha's existing Pydantic/dataclass conventions, validated enums, typed domain models, and error handling. This example only expresses the seam between the coordinator and existing execution runtime.

---

## 18. API and UI integration

The existing Alpha UI/API should remain the main user entry point. Expose the new mode as an execution policy option rather than a separate chat product.

### 18.1 Suggested configuration

```yaml
orchestration:
  gro:
    enabled: true
    default_mode: adaptive   # single | group | adaptive
    max_candidates_per_task: 3
    max_active_candidates_global: 2
    max_repair_rounds: 1
    early_stop_on_verified_success: true
    candidate_timeout_seconds: 240
    run_timeout_seconds: 900
    require_final_verification: true
    preserve_baseline_runner: true
    relative_rank_is_tiebreaker_only: true
    require_approval_for_external_side_effects: true
    workspace_isolation: worktree_or_sandbox
    budgets:
      max_llm_calls_per_candidate: 12
      max_tool_calls_per_candidate: 40
      max_retries_per_step: 2
      max_artifact_mb_per_candidate: 100
    tiers:
      trivial: { group_size: 1 }
      ordinary: { group_size: 1 }
      uncertain: { group_size: 2 }
      complex: { group_size: 3 }
```

Values are starter defaults for local experiments, not universal optimal settings. Validate configuration at startup. Reject unknown keys or unsafe combinations. The final key names should follow Alpha's existing configuration style.

### 18.2 Suggested endpoints

Adapt to Alpha's existing FastAPI routers and naming conventions. Avoid duplicate endpoints if equivalent functionality already exists.

- `POST /api/orchestration/runs` — create a run and return its ID/status.
- `GET /api/orchestration/runs/{run_id}` — run metadata and summary.
- `GET /api/orchestration/runs/{run_id}/candidates` — candidate statuses and safe summaries.
- `GET /api/orchestration/candidates/{candidate_id}` — candidate outcome and evaluation breakdown.
- `POST /api/orchestration/runs/{run_id}/cancel` — cancel the run and propagate cancellation.
- `POST /api/orchestration/runs/{run_id}/approve` — record explicit approval if the workflow requires it.
- `GET /api/orchestration/metrics` — bounded operational metrics.

Avoid exposing raw secrets, unrestricted terminal output, internal file paths, or complete prompts to every UI user. Apply existing authentication/authorization boundaries when available; do not add a bypass because the current task is “local.”

### 18.3 UI surface

Expose a compact **Execution Strategy** setting:
- **Normal:** use the baseline one-attempt path.
- **Adaptive:** start with the baseline and use extra candidates only when policy/uncertainty justifies it.
- **Compare approaches:** explicitly run a bounded candidate group.

In advanced settings, display candidate limit, time budget, cost/tool-call budget, allowed agent roles, and whether external effects require approval. Add a setting only when it changes actual backend behavior; avoid decorative controls that imply protections the server does not enforce.

For each run, show:
- Overall run state and budget consumed.
- Each candidate's strategy, status, duration, and verifier result.
- Why a candidate was rejected or selected.
- Tests/evidence used for selection.
- Whether the final artifact passed final verification.
- A visible **unverified/partial** status when appropriate.
- Controls for canceling the whole group.

Avoid a UI that encourages choosing candidates based on flashy language. Make evidence and hard-gate results visually more prominent than raw scores.

---

## 19. Observability and debugging

Alpha has to make the candidate-selection process explainable. “Agent A got 87 points” is not enough.

### Minimum events

- `orchestration.run.created`
- `orchestration.policy.decided`
- `orchestration.group.created`
- `candidate.queued`
- `candidate.started`
- `candidate.tool_call.started`
- `candidate.tool_call.finished`
- `candidate.tool_call.failed`
- `candidate.artifact.created`
- `candidate.completed`
- `candidate.timed_out`
- `candidate.cancelled`
- `candidate.evaluation.started`
- `candidate.gate.failed`
- `candidate.evaluation.completed`
- `candidate.ranked`
- `candidate.selected`
- `candidate.rejected`
- `orchestration.repair_group.created`
- `orchestration.final_verification.failed`
- `orchestration.promoted`
- `orchestration.run.finished`

Every event should include `run_id`, optional `candidate_id`, trace/correlation ID, timestamp, component, severity, and a safe structured payload. Avoid logging secrets or entire sensitive prompts by default.

### Important operational metrics

- Task success rate after final verification.
- Fraction of runs that required a candidate group.
- Candidate group success rate vs baseline single attempt.
- Rate of runs where all candidates failed.
- Mandatory-gate failure counts by category.
- Regression rate after patch promotion.
- Selection margin and near-tie rate.
- Mean/median candidate count per task.
- Extra latency versus baseline.
- LLM calls, token/cost estimate, tool calls, wall time, CPU/RAM/VRAM pressure where available.
- Duplicate strategy rate and redundant candidate rate.
- Repair-round success rate.
- Worker crashes, cancellation failures, stuck runs, and stale leases.
- Human override / rollback rate.

### Required trace view

The trace should reconstruct:
1. Original request and normalized contract.
2. Why group mode was or was not selected.
3. Candidate strategies and shared snapshot.
4. Each lane's timeline, tool calls, errors, and artifacts.
5. Verifier gates and evidence.
6. Absolute score plus relative standing.
7. Rejection/selection rationale.
8. Final staged artifact and final verification.
9. Any user approval or side-effect promotion.

Trace grading and repeatable evaluation are useful models for this separation; see [OpenAI agent evaluation guidance](https://developers.openai.com/api/docs/guides/agent-evals) and [evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices). Alpha can implement these ideas with its own telemetry stack; no hosted evaluation service is required.

---

## 20. Evaluation plan: prove it works before turning it on by default

Do not declare success because a demo task looks good. Create a repeatable benchmark that compares the existing baseline against GRO.

### 20.1 Baseline dataset

Use representative, non-sensitive tasks from Alpha's actual workloads. Start with 30–50 hand-reviewed tasks if the project has no established benchmark, then expand. This is a starting target for evaluation coverage, not a statistical guarantee.

Include:
- Easy tasks where one attempt should be sufficient.
- Ambiguous tasks with multiple plausible strategies.
- Coding tasks with known expected behavior and automated tests.
- Research tasks with authoritative sources and known claims.
- Tool failures/timeouts.
- Long tasks that may need partial completion or resume.
- Adversarial/injected instructions inside untrusted data.
- Tasks where every candidate should fail or request clarification.
- External side-effect simulations requiring approval.

Keep some cases held out from strategy/rubric tuning so the selector is not optimized against the same tasks it is evaluated on.

### 20.2 Test modes

For each task, compare:
- **Baseline:** current Alpha orchestration with one attempt.
- **GRO single:** new infrastructure but group size one, to detect orchestration overhead or regressions.
- **GRO group:** controlled candidate group.
- **GRO group without relative rank:** ablation to determine whether relative comparison adds value beyond verifier gates.
- **GRO with synthesis disabled:** determine whether synthesis helps.

Where execution is nondeterministic, run enough repeated trials to observe variability. Record provider/model version and environment changes. Do not draw strong conclusions from one lucky run.

### 20.3 Primary metrics

1. **Verified task success rate** — most important.
2. **False success rate** — tasks reported as completed when final verification failed or was missing; target zero for required gates.
3. **Regression/safety incident rate** — target zero for mandatory safety rules.
4. **Cost per verified success** — not simply cost per attempt.
5. **Time to verified result** — p50 and p95.
6. **All-candidates-failed rate** — useful diagnostic, not necessarily a failure of the selector if tasks are genuinely impossible.
7. **Retry/repair success rate**.
8. **Selection validity** — selected candidate remains valid after final staging/reverification.
9. **Resource overhead** — CPU, memory, storage, tool quotas, model calls.
10. **Auditability** — fraction of runs whose decision can be explained from the trace.

### 20.4 Promotion thresholds

Set explicit targets before running the benchmark. Reasonable initial engineering gates include:
- No increase in false-success behavior.
- No violation of mandatory tool/security restrictions.
- All selected/promoted coding patches pass configured final mandatory checks.
- Every run terminates in a valid final or blocked/error state; no silent infinite hangs.
- Group mode improves verified success on selected difficult-task categories enough to justify its measured extra cost/time.
- Single-attempt tasks do not incur significant unnecessary overhead.
- Cancellation actually stops workers and child processes.
- The baseline path remains usable and can be restored with configuration.

Do not force a universal percentage improvement target before collecting a baseline; choose the target based on Alpha's real workload and acceptable cost.

### 20.5 Fault-injection tests

Simulate:
- Candidate process exits unexpectedly.
- Model provider timeout/rate limit.
- Tool returns malformed JSON.
- Candidate lies about test results.
- Verifier crashes.
- SQLite lock or persistence write failure.
- Cancellation during subprocess execution.
- Candidate output is too large.
- Two candidates try to promote simultaneously.
- Worker restarts after saving artifacts but before saving status.
- Deadline expires during final validation.

A correct result is not merely “the exception is caught.” The run should reach a clear state, release resources, preserve useful evidence, and avoid promotion of invalid artifacts.

---

## 21. Implementation roadmap

Build incrementally and keep every phase independently testable. Do not start by implementing the full tournament, synthesis, adaptive learning, UI dashboard, and swarm controls in one large change.

### Phase 0 — Repository and execution-path audit

**Goal:** Identify the actual single source of truth for task execution.

Tasks:
- Map the request entry points (UI/API/CLI/Telegram if enabled).
- Locate the current supervisor/graph/agent loop, tool registry, worker scheduler, model adapters, persistence, logs, and task completion signals.
- Find existing retry, timeout, cancellation, checkpoint, and workspace mechanisms.
- Identify the exact point where a user request becomes a task and where a task is marked complete.
- Trace one successful task and one failed task end to end.
- Avoid duplicating an existing feature under a new module name.

**Exit condition:** An architecture map and an integration plan identify the fewest safe insertion points for the new policy and coordinator.

### Phase 1 — Outcome and trace contracts

**Goal:** Make current behavior measurable before changing selection.

Tasks:
- Add typed `TaskContract`, `CandidateOutcome`, `GateResult`, `CandidateEvaluation`, and `OrchestrationTrace` models using Alpha's existing conventions.
- Capture exit codes, artifacts, failed criteria, tool results, and exception classification.
- Add stable run/candidate IDs and correlation IDs.
- Implement a baseline evaluator for at least one task class, preferably coding/debugging or another class with objective checks.
- Add redaction, retention, and trace tests.

**Exit condition:** A single baseline run can be replayed/debugged from structured data, and its completion status is evidence-based.

### Phase 2 — One-candidate coordinator (compatibility mode)

**Goal:** Introduce orchestration plumbing without adding parallelism.

Tasks:
- Route an existing single-agent task through `CandidateCoordinator` with group size one.
- Add deadlines, status transitions, cancellation, budget accounting, and failure reporting.
- Ensure old callers receive compatible final results.
- Add fault-injection tests for crashes/timeouts.

**Exit condition:** Group size one behaves as well as or better than the baseline; rollback to baseline is one configuration change.

### Phase 3 — Safe isolated candidate lanes

**Goal:** Execute multiple candidates without sharing mutable state.

Tasks:
- Implement candidate workspace creation from one snapshot.
- Add worktree/container/temp-directory cleanup and artifact manifests.
- Add global/per-task concurrency limits and local-resource backpressure.
- Add tool permission profiles and staged side effects.
- Verify cancellation can stop all child work.

**Exit condition:** Two or three candidates can run on the same task without workspace cross-contamination, accidental duplicate side effects, or resource leaks.

### Phase 4 — Hard gates and deterministic candidate selection

**Goal:** Choose only verified candidates.

Tasks:
- Implement the first task-specific verifiers.
- Separate hard gates from score metrics.
- Add absolute scoring breakdown and deterministic tie rules.
- Add relative ranking only as a diagnostic/tie-breaker within comparable groups.
- Return an explicit no-acceptable-candidate result when all fail.

**Exit condition:** Deliberately invalid candidates are rejected even if they have high self-reported confidence or the highest score in the group.

### Phase 5 — Diverse strategy templates and adaptive group size

**Goal:** Make groups meaningfully useful rather than duplicating the same attempt.

Tasks:
- Add a small strategy catalogue for coding/debugging and/or one other measured task type.
- Implement task-to-strategy eligibility and diversity checks.
- Add policy rules to select group size from uncertainty, task tier, budget, and system load.
- Add early stopping and budget-aware expansion.

**Exit condition:** Benchmark results demonstrate when group execution adds value, and easy tasks continue using the low-cost path.

### Phase 6 — Repair rounds and verified synthesis

**Goal:** Recover intelligently from failures and safely combine complementary work.

Tasks:
- Generate a failure bundle from verifier/tool evidence.
- Spawn a bounded repair group with new strategy instructions.
- Add optional synthesis for research findings or complementary patch ideas.
- Re-run integrated checks on the synthesized/staged artifact.
- Track repeated failure signatures and stop loops.

**Exit condition:** Repair attempts respond to concrete failure evidence; synthesis cannot bypass mandatory verification.

### Phase 7 — UI, APIs, and operational telemetry

**Goal:** Make the mode understandable and controllable to users.

Tasks:
- Add run/candidate status endpoints if equivalent endpoints do not exist.
- Display status, budgets, evidence, gate failures, and final verification.
- Add cancellation and approval actions connected to backend policy.
- Expose aggregate metrics and trace inspection.

**Exit condition:** Users can tell what Alpha did, why it chose a result, whether it was verified, and how to stop it.

### Phase 8 — Benchmark, canary, rollout

**Goal:** Enable safely with objective proof.

Tasks:
- Compare baseline vs GRO on representative tasks.
- Run fault-injection and security tests.
- Enable for developer/internal tasks or an explicit “compare approaches” mode first.
- Review cost, latency, memory use, false-success incidents, and operator feedback.
- Expand task categories only when verifier coverage is strong.
- Keep a kill switch and baseline fallback.

**Exit condition:** The measured verified-success improvement justifies overhead, all safety gates remain enforced, and rollback is proven.

---

## 22. Testing checklist

### Unit tests
- Task normalization preserves the user's goal and constraints.
- Policy selects single/group/human-approval path correctly.
- Candidate count never exceeds configured limits.
- Strategy selection returns eligible and meaningfully distinct strategies.
- Budget accounting cannot go negative or be bypassed by retries.
- Hard-gate failures make a candidate ineligible.
- Relative rank cannot make an ineligible candidate eligible.
- Tie-breaking is deterministic for identical inputs.
- Missing verifier results become `unknown`, not `pass`.
- PII/secrets are redacted from stored events.

### Integration tests
- Existing model adapter executes through the coordinator.
- Existing tool registry and agent profiles are reused.
- Multiple candidates receive a consistent task snapshot.
- Candidate workspaces remain isolated.
- Verifier sees the correct artifact and environment.
- Final verification runs on the actual staged artifact.
- Cancellation propagates to child agents and subprocesses.
- Run status survives process restart.
- Baseline mode remains compatible.

### End-to-end tests
- Known coding task: one candidate passes, weaker candidates are rejected.
- Every candidate fails: Alpha reports unverified/failure rather than false success.
- Candidate A is verbose but wrong; candidate B is short but verified: B wins.
- Candidate has high score but fails a mandatory safety gate: it cannot be promoted.
- Research candidates disagree: targeted evidence check resolves or discloses disagreement.
- External action candidates prepare proposals: only one approved promotion can execute.
- Budget expires: task ends with a visible budget-exhausted status.
- Cancellation: no child process continues modifying files afterward.

### Release checklist
- [ ] Baseline behavior can be restored quickly.
- [ ] No training code or model-weight changes are required.
- [ ] No new provider or paid service is required for core orchestration.
- [ ] Every candidate has isolated artifacts and a trace.
- [ ] Hard-gate failure always blocks promotion.
- [ ] Final staged output is re-verified.
- [ ] All failure/timeout/cancel states are explicit.
- [ ] Limits are enforced by backend code rather than only prompts/UI.
- [ ] Secrets are not stored in ordinary trace records.
- [ ] Performance and success are measured against a baseline.

---

## 23. Common mistakes to prevent

1. **Calling the runtime selector GRPO itself.** Use “GRPO-inspired orchestration” or “group-relative candidate selection.” Actual GRPO training is out of scope.
2. **Choosing the group winner even if every candidate is wrong.** Require absolute gates and allow no winner.
3. **Using an LLM judge as the only source of truth.** Prefer tests, schemas, source checks, and external evidence.
4. **Generating nearly identical candidates.** Use an explicit strategy registry and track duplication.
5. **Letting candidates share one workspace.** Isolate mutable files, side effects, and candidate logs.
6. **Running every agent in parallel.** Use bounded concurrency and local resource awareness.
7. **Multiplying external side effects.** Stage proposals and perform one approved promotion.
8. **Combining patches without re-running tests.** The merged artifact is a new result and requires full final verification.
9. **Using relative score to learn safety policy.** Safety and authorization must remain deterministic policy constraints.
10. **Treating stored experience as automatic learning.** Runtime analytics can inform a versioned policy; it does not update model weights.
11. **Retrying every error.** Separate transient infrastructure failures, strategy failures, hard blocks, and missing user inputs.
12. **No stop condition.** Every run needs deadlines, round caps, cancellation, and budget-exhaustion behavior.
13. **Optimizing for score instead of outcomes.** Evaluate verified task success, false success, regressions, cost, latency, and resource overhead.
14. **Making the first release too large.** Start with one task type and hard verifiers, then extend.
15. **Silently dropping existing Alpha features.** Implement GRO as an optional strategy/coordination layer and preserve current execution paths.

---

## 24. References and what to take from each

1. **DeepSeekMath — GRPO origin and group-relative method**  
   https://arxiv.org/abs/2402.03300  
   Use for conceptual grounding: group sampling, relative outcomes, and the distinction between runtime responses and policy optimization. The actual paper describes a training algorithm; Alpha's proposed runtime selector is only inspired by its group-comparison idea.

2. **Scaling Test-time Compute for LLM Agents**  
   https://arxiv.org/abs/2506.12928  
   Supports investigating parallel sampling, sequential revision, verification/merging, and diverse rollouts as runtime methods for agents. Use as motivation for controlled candidate groups and measured compute budgets, not as evidence that any arbitrary number of agents will improve every task.

3. **Sample, Scrutinize and Scale: Effective Inference-Time Search by Scaling Verification**  
   https://proceedings.mlr.press/v267/zhao25a.html  
   Supports sampling and verification as an inference-time search pattern. The important implementation lesson is that additional candidates are useful only when verification can discriminate quality.

4. **OpenAI — Evaluation Best Practices**  
   https://developers.openai.com/api/docs/guides/evaluation-best-practices  
   Use for task-specific evaluation, evaluation-driven development, logging, adversarial cases, and human calibration. Alpha can implement these principles independently.

5. **OpenAI — Evaluate Agent Workflows**  
   https://developers.openai.com/api/docs/guides/agent-evals  
   Use as a reference for trace grading and repeatable evaluation of tool choices, handoffs, guardrails, and multi-agent workflows.

6. **OpenAI — A Practical Guide to Building Agents**  
   https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/  
   Use for incremental orchestration design, tool boundaries, and selecting single- versus multi-agent patterns based on task needs rather than complexity for its own sake.

7. **LangGraph overview**  
   https://langchain-ai.github.io/langgraph/tutorials/introduction/  
   Use as an architecture reference for long-running stateful workflows, durable execution, persistence, human-in-the-loop, and mixing deterministic nodes with agent-driven decisions. Alpha does not need to adopt LangGraph if its existing graph/runtime already supplies these capabilities.

8. **LangGraph fault tolerance**  
   https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/fault-tolerance.mdx  
   Use for node-level timeout, retry, and error-handler design. Reuse Alpha's existing fault-tolerance mechanism if present rather than creating duplicate per-agent retry systems.

9. **SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering**  
   https://arxiv.org/abs/2405.15793  
   Use for the lesson that a purpose-built, validated agent/computer interface can matter substantially: tools should return focused, actionable outputs and validate edits/commands. This improves candidate quality before ranking even begins.

10. **Agent Lightning v1.0: Towards Harnessed Agentic RL**  
    https://arxiv.org/abs/2608.17528  
    Relevant as a terminology boundary: its harnessed agentic RL work connects runtime harness interactions to post-training. Alpha's scope here deliberately stops before that training path. Do not import training infrastructure simply to get group-relative runtime orchestration.

---

## 25. Final recommendation for Alpha

Implement **Group-Relative Orchestration (GRO)** as an optional runtime policy in Alpha's current harness:

1. Preserve the existing one-attempt path.
2. Add structured outcomes and trace/evidence capture.
3. Put candidates behind a bounded coordinator with isolated execution lanes.
4. Build deterministic hard gates for one measurable task category first.
5. Rank only eligible candidates using absolute outcome quality, with group-relative metrics as a diagnostic/tie-breaker.
6. Add evidence-directed repair and optional synthesis only after selection is reliable.
7. Validate the staged final artifact, then promote it under existing safety and approval rules.
8. Compare against baseline before enabling adaptively by default.

**Do not start by making Alpha spawn more agents. Start by making Alpha able to prove what each attempt did, whether it succeeded, and why the chosen result is better.** That is the core harness-level value of this concept. It can improve Alpha's task completion behavior while keeping your current LLMs, Python codebase, and deployment model unchanged.
