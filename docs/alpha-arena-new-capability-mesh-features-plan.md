# Alpha Capability Mesh — New-Features-Only Expansion Plan

**Target:** `itsPremkumar/alpha`  
**Companion specification:** `alpha-arena-advanced-implementation-plan.md`  
**Reference inspiration:** [Arena Skill](https://github.com/Jakeschincariol/arena-skill), [RouteLLM](https://github.com/lm-sys/RouteLLM), [Mixture-of-Agents](https://arxiv.org/abs/2406.04692), [LLMRouterBench](https://github.com/ynulihao/LLMRouterBench), [FrugalGPT](https://arxiv.org/abs/2305.05176)  
**Document scope:** Additive features that extend the previous Alpha Arena+ plan. Do **not** rebuild or restate its basic tournament, attack/defend, judge, contribution synthesis, evidence ledger, standard task contracts, persistence, UI, or baseline budget controls. Audit Alpha first and reuse existing equivalents.

---

## 1. Purpose: From “Which Answer Wins?” to “Who Is Best at This Exact Step?”

The next evolution should not treat an AI agent as universally good or bad. An agent might be excellent at debugging Python but mediocre at writing product copy; another might be strong at researching recent documentation but poor at integrating a large code change; a third might be especially good at discovering edge cases, while another is best at explaining the final result clearly.

Alpha should therefore make a **situation-specific capability decision for each meaningful part of a task**. The system should discover the work, identify the capabilities needed, choose the most suitable specialist for each step, ask complementary agents to cover blind spots, select the strongest verified contribution from each specialist, and assemble the contributions without losing dependencies or contradictions.

The key new idea is the **Alpha Capability Mesh (ACM)**: a live, evidence-backed map connecting tasks, microtasks, agents, models, tools, skills, capabilities, past outcomes, known failure modes, and collaboration patterns.

An agent is not assigned merely because its label says “coder” or “researcher.” Its suitability is estimated for the exact operation, in the current context, with the currently available tools and budget. The estimate improves only when Alpha records meaningful evaluation evidence.

### The desired outcome

For every multi-step request, Alpha should be able to answer these internal questions:

1. Which specific operations make up this request?
2. What capability is required for each operation?
3. Which available agent/model/tool is empirically strongest for that capability and situation?
4. Which second agent provides a genuinely different or complementary perspective?
5. What is the best contribution for each step—not necessarily the best whole answer?
6. Which contributions can safely be combined, and which depend on incompatible assumptions?
7. What is the cheapest next action that is likely to improve the final result?
8. What did Alpha learn about the agent, task type, and collaboration from the actual outcome?

This is **not** a promise that Alpha will always know the objectively best model. It is a mechanism to make assignment decisions measurable, testable, revisable, and honest about uncertainty.

---

## 2. Feature A — The Capability Passport for Every Agent, Model, Tool, and Skill

### 2.1 Why this is new

A static agent directory usually describes what an agent is *supposed* to do. Alpha needs an empirical passport that describes what it can demonstrably do, what it struggles with, and under what conditions its performance changes.

### 2.2 Passport data model

Create a versioned `CapabilityPassport` record for each runnable configuration. Distinguish the agent persona, the underlying model/provider, the configured toolset, the skill bundle, and the runtime environment; these are separate sources of capability.

Suggested fields:

- `entity_id`, `entity_type`, `display_name`, `version`, `enabled`.
- `model_provider`, `model_id`, `model_family`, `local_or_remote`, `quantization`, `context_limit`, `modalities`, `structured_output_support`, `tool_call_support`.
- `capability_vector`: task-relative dimensions such as implementation, debugging, code review, test design, technical research, source analysis, architecture, reasoning, math, summarization, planning, instruction following, data extraction, translation, UX, creativity, security review, and communication.
- `domain_tags`: language/framework/product/domain contexts (for example, Python, TypeScript, React, databases, DevOps, API design).
- `tool_affinity`: verified fit for browser, terminal, repository, test runner, SQL read-only tools, filesystem, image input, and other registered tools.
- `quality_estimates`: measured outcome quality per task category and complexity band.
- `calibration_estimates`: how often expressed confidence aligns with measured correctness.
- `reliability`: timeout rate, malformed output rate, tool failure rate, retry rate, crash rate, and completion rate.
- `efficiency`: latency distribution, token consumption, estimated/actual monetary cost, local CPU/RAM/GPU pressure if relevant.
- `known_failure_modes`: observed weaknesses with evidence and sample size.
- `data_recency`: number of tested observations, last tested date, model/version change date, and estimate uncertainty.
- `provenance`: benchmark IDs, test IDs, evaluation protocol versions, and whether measurements were automatic, human-reviewed, or weak LLM-judged.

Do not encode one universal scalar like `intelligence: 0.98`. Store dimensioned estimates and confidence intervals or sample-size-aware confidence levels. Keep “unknown” distinct from “bad.” A new model with no evaluation history must not be treated as incompetent; it should be marked **unmeasured** and tested cautiously.

### 2.3 Capability evidence levels

Tag every capability estimate with one of these evidence levels:

- **Unmeasured:** provider description or user configuration only.
- **Observed:** the agent completed tasks, but evaluations were weak or indirect.
- **Benchmarked:** evaluated on a controlled set of representative tasks.
- **Outcome-verified:** the contribution passed deterministic tests, source validation, real task acceptance, or human review.
- **Recently revalidated:** verified after a model, prompt, skill, tool, or runtime version change.

The router should prefer stronger evidence when estimates are otherwise similar, but must not let a model vendor's marketing text outrank actual benchmark results.

### 2.4 Separate capabilities that are often conflated

Track at least these distinctions:

- Can produce good code vs. can safely edit a repository.
- Can explain an answer vs. can discover current factual information.
- Can identify risks vs. can implement a correct fix.
- Can use tools in a prompt vs. can reliably execute and inspect tool results.
- Can solve a task when isolated vs. can integrate changes from other agents.
- Can generate a valid answer vs. can admit uncertainty appropriately.
- Can perform well with a large context vs. can work within Alpha's current small context budget.
- Can reason well in English vs. can produce clear Tamil/English or other requested-language output.

### 2.5 Acceptance tests

- Every selected agent has a passport, even if most fields say `unknown`.
- Passport estimates can be traced to observations and version IDs.
- A model/version update invalidates or reduces confidence in stale measurements according to policy.
- Missing data never silently turns into a high score.
- UI and logs never present inferred capability as a guaranteed fact.

---

## 3. Feature B — Microtask Capability Router (Route by Exact Step, Not by Whole Prompt)

### 3.1 New behavior

Decompose a request into named operations with explicit deliverables. Route each operation independently. Do not automatically send a 12-step task to one “best overall” model or to every model.

Example task: “Find why my FastAPI endpoint fails, implement the fix, add tests, and explain deployment.”

| Microtask | Capability needed | Candidate assignment |
|---|---|---|
| Reproduce the failure | debugging, environment/tool use | debugging specialist + isolated runner |
| Identify likely cause | code reading, causal reasoning | repository analyst + independent challenger |
| Verify relevant library/API behavior | current research, source reading | documentation researcher |
| Propose patch | code generation, project conventions | implementation specialist |
| Add regression tests | test design, edge-case generation | test specialist |
| Execute tests and inspect failures | terminal/test runner | execution-capable agent |
| Review security and breaking changes | security/code review | independent reviewer |
| Explain final result | concise technical communication | explanation specialist |

The “best” agent is selected separately for each row; the overall final-answer writer is not allowed to override a verified patch or test result merely because its prose is better.

### 3.2 Decomposition contract

Each microtask should contain:

- `microtask_id` and parent task ID.
- A single desired output and objective acceptance criteria.
- Required and optional capabilities.
- Required context paths or data slices.
- Permitted tools and permission boundary.
- Input dependencies and outputs other tasks need.
- Risk class and reversibility.
- Completion evidence required.
- Estimated effort and maximum budget.
- Whether independent replication is warranted.

The decomposition stage should create only as many microtasks as add value. Tiny requests remain single-step. Use deterministic task templates and lightweight classification before asking an LLM to recursively split everything.

### 3.3 Context-aware fit score

For microtask `t` and candidate executor `a`, estimate:

`Fit(a,t) = Expertise × EvidenceStrength × ContextFit × ToolFit × Reliability × Calibration × Freshness × DiversityValue − RiskPenalty − CostPenalty − LatencyPenalty`

Each component should be normalized into documented ranges; hard constraints are eligibility gates rather than small penalties. For example, an agent with no repository write permission is ineligible for an authorized write task, even if it has a high code-generation score. An agent without current web access is ineligible for a task requiring current sources unless a trusted retrieval artifact is supplied.

Do not hard-code arbitrary weights as if they were objectively correct. Start with transparent configurable weights, evaluate on held-out task sets, then learn/tune routing decisions from measured outcomes. Log the component scores so developers can understand why an assignment happened.

### 3.4 Distinguish best executor, challenger, and verifier

When a second opinion is valuable, assign three different responsibilities:

- **Primary specialist:** expected to make the main contribution.
- **Complementary challenger:** targets weaknesses that the primary may overlook, selected for different capabilities or evidence.
- **Verifier:** checks a defined output property, using tests, sources, calculations, or a task-specific rubric.

These roles need not be three LLM calls for every step. Deterministic verification may be better than an extra model. Avoid selecting the same model family, same prompt, and same context as “independent” reviewers unless this is intentional.

### 3.5 Acceptance tests

- A complex task produces a step-to-specialist map with a rationale.
- Different steps can choose different agents and providers.
- A single-step question can avoid unnecessary decomposition.
- Hard constraints override ranking.
- Router output is deterministic given identical input, passport snapshot, settings, and random seed, unless adaptive exploration is expressly enabled.
- The UI can explain the routing decision without exposing private chain-of-thought.

---

## 4. Feature C — Capability Graph and Complementarity-Aware Team Formation

### 4.1 Move beyond a leaderboard

A leaderboard answers “who scored highest?” A capability graph answers “which group produces the best coverage with the least redundant effort?”

Represent the system as a graph with these node types:

- agent/model configurations;
- capabilities and sub-capabilities;
- task categories and microtask patterns;
- tools and external data sources;
- skills and prompt recipes;
- recurring failure modes;
- verified outcomes and benchmark cases;
- collaboration pairs/teams.

Edges represent empirical relationships: `demonstrated_capability`, `requires_tool`, `improves_with_skill`, `often_misses`, `complements`, `duplicates`, `conflicts_with`, and `verified_on`.

### 4.2 Complementarity is a measured property

Two agents may have similar individual scores but low combined value because they make the same mistakes. A moderate-performing pair may be much more useful if their failure modes differ.

For each candidate pair and task category, track:

- accuracy/quality of agent A alone;
- quality of agent B alone;
- quality when A and B collaborate;
- disagreement usefulness (did disagreement identify real issues?);
- redundant-call rate;
- whether one agent's output makes the other more accurate or merely longer;
- conflict-resolution cost;
- cost and latency added by the pairing.

Use the outcomes to maintain **complementarity estimates**. Only state that a pair is complementary after collecting sufficient evidence; otherwise tag the estimate as speculative.

### 4.3 Team portfolio selection

Build a small team that maximizes expected task coverage and useful diversity subject to a budget. The objective should reward unique capability coverage, expected quality, independence of verification, and past synergy, while penalizing correlated failure, duplicated effort, cost, and integration overhead.

A practical first version can use a greedy selector:

1. Add the highest-fit primary specialist.
2. Compute the capability gaps the primary does not cover well.
3. Add the candidate with the best marginal gap coverage, adjusted for correlated-error risk and cost.
4. Add a verifier only for properties that lack cheap deterministic checks or whose risk requires independent review.
5. Stop when marginal expected value falls below a threshold.

Later, benchmark constrained set-cover, submodular selection, or learned team policies against the greedy baseline. The advanced algorithm is not automatically better; retain the best one supported by tests.

### 4.4 Diversity that matters

Track diversity by several independent axes: model family, provider, training lineage if known, reasoning workflow, source collection, tool path, data view, and verification method. Strategy-card variety alone should not count as proof of independent judgement.

### 4.5 Acceptance tests

- Team selection can prefer a complementary verifier over a higher-ranked duplicate.
- It records why a candidate was added and the marginal value expected.
- It can work with one configured model by varying methods/roles, but transparently labels the reduced independence.
- Team size has a hard maximum, and adding another agent requires a predicted benefit.

---

## 5. Feature D — Situation Fingerprinting and Routing by Conditions

Two questions in the same broad category may need different capabilities. A debugging task about a syntax error is not the same as a flaky distributed system incident; a request for “latest pricing” is not the same as a timeless conceptual explanation.

Create a **Situation Fingerprint** before routing. This is a compact, structured description, not a long natural-language summary.

Possible dimensions:

- task type and sub-type;
- expected answer format;
- new vs. familiar domain;
- recency requirement;
- source/tool availability;
- input size and context pressure;
- ambiguity and missing-information level;
- correctness/risk sensitivity;
- impact of a wrong answer;
- whether code, files, external actions, or private data are involved;
- number of dependent steps;
- user preference for speed, quality, low cost, local-only, or breadth;
- need for reproducibility;
- reversibility of any proposed action.

Use this fingerprint to choose the relevant capability dimensions and the appropriate selection policy. Do not assign a model a permanent label like “best researcher” based on its mean score across all research prompts.

### Context slices

When assigning a subtask, provide the **smallest sufficient context slice** plus a stable pointer to the full task contract. One specialist may need the stack trace and function, while another needs the project dependency files. Avoid flooding every specialist with every past message, every candidate answer, and all repository files.

Context reduction must preserve requirements and critical negatives. Add an automatic coverage check that confirms all required constraints, acceptance criteria, and safety boundaries were retained in each assignment.

---

## 6. Feature E — Adaptive Expert Discovery and Temporary Specialist Creation

### 6.1 The capability gap detector

Alpha should detect when no existing agent has credible evidence for a needed capability. Instead of silently choosing a poor fit, it should:

1. state the capability gap internally in structured form;
2. search existing skills, MCP tools, provider capabilities, local runtimes, and reusable prompts;
3. determine whether the missing capability is a model gap, tool gap, context gap, or workflow gap;
4. assemble a temporary specialist from a safe template and a minimal tool/skill bundle;
5. run a capability-specific smoke check before giving the specialist real work;
6. assign a bounded subtask with a clear acceptance test;
7. retire the temporary specialist after the run unless evidence supports promoting it.

Examples: a temporary migration-review specialist, CSS accessibility checker, SQL query-plan analyst, API contract test designer, or source-freshness reviewer.

### 6.2 Do not confuse prompt personas with new models

A temporary role built on the same model is useful specialization, but not an independent model. The registry must show whether a specialist differs in model, data/tool access, skill instructions, or only role prompting. The system must not claim that “three different agents agreed” when all three are the same model responding to closely related prompts.

### 6.3 Skill proposals and promotion

Allow a specialist to propose a reusable skill, tool wrapper, checklist, or prompt recipe. New artifacts remain quarantined until:

- syntax/schema validation passes;
- permission policy passes;
- a sandbox test succeeds;
- a small benchmark shows measurable benefit;
- a regression set finds no unacceptable damage;
- the user or maintainer policy approves promotion.

Promote via versioned registry entries. Never let an agent silently modify its own capability scores or permanently install unreviewed skills because it claims they would help.

---

## 7. Feature F — Best-Per-Step Composition with an Integration Compiler

The companion plan defines contribution-level synthesis. This new feature makes that capability **expert-aware and interface-aware**: choose each contribution from the agent most suitable for that particular unit, then compile those units into a consistent deliverable.

### 7.1 Contribution slots

Create a `ContributionSlot` for each subtask output or important answer component. It should include:

- parent requirement and dependent slots;
- the capability dimensions most relevant to this slot;
- candidate contributions and provenance;
- test/evidence results;
- assumptions and version/context snapshot;
- confidence and known limitations;
- semantic interface/format contract;
- conflict status and merge policy;
- selected contribution and reason.

A slot might represent an algorithm, code function, source-backed claim, database schema decision, test suite, deployment instruction, or one section of an educational explanation.

### 7.2 Contribution selection rule

Do not select a contribution from overall agent rank alone. Evaluate the artifact for the slot's target capability and constraints. A candidate can be the top contributor for one slot and a weak contributor for another in the same run.

Selection should combine: objective validation, task-specific quality, evidence strength, slot-specific historical calibration, compatibility with dependencies, freshness, and risk. A hard failure (e.g., a test that demonstrably fails or a claim contradicted by a reliable source) cannot be compensated for by style or verbosity.

### 7.3 Integration compiler

Create a deterministic-enough final assembly stage that:

1. loads the selected contributions and their interface contracts;
2. builds the dependency graph among contribution slots;
3. topologically orders slots where dependencies exist;
4. detects duplicated, conflicting, or incompatible material;
5. harmonizes naming, notation, terminology, imports, conventions, and requested tone;
6. preserves required citations/provenance and original user constraints;
7. emits a consistent final artifact;
8. runs post-assembly checks that each selected contribution still means what it meant before assembly.

Use code for stable structural operations; use an LLM only where semantic decisions are required. If a synthesis model rewrites a contribution, treat the rewritten section as a new artifact that must retain provenance and be reverified.

### 7.4 Never combine incompatible fragments blindly

For code, individually good patches can conflict on types, state, APIs, schemas, dependencies, security policy, or architectural assumptions. For plans, individually valid steps may rely on mutually exclusive deployment targets. The compiler must mark compatibility requirements before merge and reject unresolved conflicts instead of producing an attractive but impossible hybrid.

### 7.5 Slot ownership and tie handling

When two contributions are similar, choose the one with stronger verification or better slot fit. When each has a unique strength, keep both only if they add non-duplicative value. When they represent real alternatives, retain the alternatives with a short decision rule rather than averaging them into a false compromise.

---

## 8. Feature G — Capability Calibration and Confidence That Means Something

### 8.1 Separate answer confidence from agent capability

An agent saying “I am 95% confident” does not mean its answer has a 95% chance of being correct. Keep distinct fields for:

- the candidate's stated confidence;
- Alpha's empirical reliability estimate for this agent/capability/situation;
- evidence coverage;
- verifier result;
- unresolved ambiguity;
- final system confidence or confidence band.

### 8.2 Calibrate by narrow task groups

Measure calibration separately for categories with enough observations. A model may be well calibrated on code explanation but overconfident on current-events research. Do not pool data blindly across categories. Use explicit sample counts and uncertainty estimates; small sample sizes should trigger conservative routing.

### 8.3 Selective abstention

If the best available contribution fails a confidence/evidence threshold, Alpha should do one of the following:

- request one targeted source/tool check;
- select another independent specialist;
- state what remains unknown;
- return a qualified partial answer;
- ask the user for missing information if it materially changes the result.

Do not automatically call more agents just to make the answer appear certain. Additional calls are useful only when they can resolve a concrete uncertainty.

### 8.4 Calibration tests

Use held-out tasks with known answers, compare confidence bins with observed success, track overconfidence and underconfidence, and alert when calibration changes after a model/prompt/tool upgrade. The goal is better decision-making, not a misleadingly precise percentage on every answer.

---

## 9. Feature H — Disagreement Topology and Targeted Uncertainty Resolution

A disagreement between agents is not simply a vote. It can reveal where Alpha needs more evidence, a different expert, or a different assumption.

### 9.1 Build a disagreement graph

Represent each major claim, decision, or proposed patch as a node. Draw edges for:

- contradiction;
- dependency;
- shared evidence;
- same underlying assumption;
- one contribution refuting another;
- an unresolved alternative;
- a test result that decides between candidates.

Group disagreements into types: factual, temporal, mathematical, requirement interpretation, implementation, risk tolerance, terminology, or user preference.

### 9.2 Resolve by appropriate method

- Factual disagreement → source check / primary source.
- Mathematical disagreement → independent calculation or executable computation.
- Code behavior disagreement → reproduce and run a targeted test.
- Current information disagreement → current source and timestamp check.
- Strategy disagreement → evaluate trade-offs against constraints; do not pretend there is one universally correct choice.
- User-intent disagreement → ask only if the unresolved ambiguity is material.
- Normative or preference disagreement → expose options and let the user choose, rather than creating a fake objective verdict.

### 9.3 Counterfactual probes

When two candidates disagree, generate the smallest test or scenario that would distinguish them. Examples: an input that triggers a claimed bug, a fixture that distinguishes two parser behaviors, a deployment constraint that makes one design fail, or an edge case that exposes hidden assumptions.

Generate probes from the disagreement and execute them when possible. Prefer one decisive test over asking five more agents the same question.

---

## 10. Feature I — Value-of-Information Scheduler

This scheduler decides **what to do next**, not just which model is strongest. It estimates the expected value of another action relative to its time, cost, and risk.

Candidate next actions may be:

- run a test;
- read a primary source;
- ask a domain specialist;
- inspect one repository file;
- compare alternative patches;
- use a different model family;
- create a counterexample;
- request a missing user constraint;
- stop and return the verified answer.

For each action, estimate:

`ExpectedNetValue(action) = ExpectedRiskOrErrorReduction + ExpectedRequirementCoverageGain + ExpectedDecisionClarity − Cost − Latency − NewFailureRisk`

Use a calibrated policy and conservative defaults. This is an estimate, not a claim that Alpha can know the future. After each action, update the unresolved-issues list and calculate whether more effort remains worthwhile.

### Required controls

- Stop on satisfied acceptance criteria unless meaningful unresolved risk remains.
- Avoid repeatedly asking agents to rephrase an already settled point.
- Do not treat more opinions as evidence when the underlying issue is factual and a source/test is available.
- Continue deeper on high-impact uncertainty even if a shallow answer looks polished.
- Allow user policies such as “answer fast,” “deepest reliable result,” “no paid models,” “local only,” or a hard token/cost cap.
- Record why Alpha stopped so the decision can be audited.

---

## 11. Feature J — Correlated-Error Awareness and Independence Scoring

### 11.1 Problem

Multiple agents can repeat the same error because they share a model family, source, prompt, context, or mistaken assumption. Three near-identical completions are not three independent confirmations.

### 11.2 Proposed mechanisms

For each task category, estimate correlation based on historical joint failures and repeated answer patterns. Use known model-family/provider relationships as weak priors only; do not invent undisclosed training-lineage facts.

Use independent verification modes wherever feasible:

- LLM review + deterministic test;
- model answer + primary-source retrieval;
- code generation + compiler/type checker;
- explanation + arithmetic/calculation;
- research + independent source search;
- patch author + clean-worktree execution agent.

Track source independence separately from model independence. Two different models citing the same unsupported blog do not provide independent factual support.

### 11.3 Consensus policy

Never use majority vote as the only truth mechanism. A vote can help decide which candidates deserve attention, but factual acceptance requires evidence; code acceptance requires tests/inspection appropriate to the change; sensitive actions require policy checks and user permissions.

---

## 12. Feature K — Cross-Run Capability Memory and Outcome-Based Learning

This is not a general conversation-memory feature. It is a **capability-performance memory** used to improve future routing.

### 12.1 What to remember

Store compact, versioned records such as:

- task fingerprint and task type;
- chosen agents and capability rationale;
- contribution slot and agent responsible;
- evaluation/test outcome;
- whether the contribution was selected, repaired, rejected, or materially edited by another specialist;
- final outcome when measurable;
- actual latency/cost and failure mode;
- user feedback or correction, if provided;
- model, prompt, skill, tool, and benchmark versions.

Avoid preserving private task content longer than needed. Separate reusable performance metadata from user-specific memory, support retention controls, and allow local-only storage policy.

### 12.2 Credit assignment

The system should not give a model full credit merely because it appeared in a successful run. Attribute credit at the contribution-slot level:

- Did the contribution survive objective checks?
- Did it improve the final result compared with the previous best?
- Did it expose a real flaw in another contribution?
- Did a proposed test find a real defect?
- Did its work increase cost without adding measurable value?
- Was its contribution only accepted after substantial repair?

For team interactions, start with marginal-value logging: compare the run with/without a particular contribution when an affordable evaluation or replay is available. More complex attribution methods can be explored later, but must be tested for stability and cost.

### 12.3 Learn conservatively

Update capability estimates only from credible, categorized observations. User “thanks” is positive experience data but not a correctness benchmark. Repeated success on one project should not automatically establish global superiority in the domain.

### 12.4 Privacy and controls

Provide settings for local-only performance memory, no cross-run learning, erase run-derived profile updates, and retention duration. Do not use one user's private project contents as a shared training corpus for other users. Keep any anonymized aggregate telemetry opt-in and documented.

---

## 13. Feature L — Shadow Routing, Canary Rollouts, and Router Self-Evaluation

The router itself is software that may regress. Add a safe experiment mode before allowing learned routing changes to affect normal tasks.

### 13.1 Shadow mode

For a sample of requests, the current router makes the real assignment while a candidate router computes a parallel *prediction only*. The candidate router must not spend additional model budget unless a deliberate evaluation policy permits it. Compare predicted choices against outcomes collected from ordinary runs and replay datasets.

### 13.2 Offline replay suite

Build a frozen, versioned evaluation dataset covering the project's real request types: simple Q&A, research, code generation, debugging, test design, repository edits, tool use, planning, long-context tasks, and local-model constraints. Maintain a separate holdout set to prevent tuning the router to its own test answers.

Evaluate:

- quality at fixed cost;
- cost at fixed quality threshold;
- latency and timeout rates;
- hard-gate failures;
- routing regret versus the best observed available choice;
- unnecessary multi-agent escalations;
- diversity and correlation of assigned team;
- failure recovery and user-visible transparency.

### 13.3 Canary and rollback

A new routing policy can be enabled for internal/test tasks first, then a small configurable slice of safe low-risk requests. Promote only if it beats the baseline on defined metrics without violating hard guardrails. Keep instant rollback to a known policy version. Do not allow the router to rewrite its own production policy without a maintainer-approved release path.

---

## 14. Feature M — Tool-and-Model Joint Routing

Model selection alone is insufficient. Some tasks are best handled by a smaller model connected to a powerful deterministic tool; others require a model with long context, vision input, browsing, or safe repository access.

### 14.1 Joint action selection

Select a **model + agent instructions + skills + toolset + context slice + verification method** as a single execution configuration. The registry should distinguish:

- model can perform an operation in principle;
- model endpoint is currently available;
- required tool is installed and permitted;
- credentials/configuration are valid;
- runtime can meet memory/latency limits;
- verification method is executable in this environment.

### 14.2 Examples

- For exact arithmetic: inexpensive model plus deterministic calculator, rather than several chat agents.
- For repository diagnosis: code-capable model plus restricted filesystem/search and test runner.
- For changing API behavior: research specialist plus current documentation retrieval, followed by implementation and test specialists.
- For image interpretation: an actually vision-capable model receiving the image, not a text-only agent asked to guess.
- For SQL: a query-planning specialist paired with a read-only database tool; writes remain separately permissioned.
- For offline/local mode: only choose configurations that truly run in the available local runtime and fit resource limits.

### 14.3 Explicit fallback chain

If a selected model/provider/tool fails, Alpha may follow a preconfigured fallback chain, but it must log the failure and show a user-visible status when it materially changes capabilities, privacy, cost, or result quality. No silent downgrade from local-only to a remote provider. No silent switch to a model that cannot perform the task.

---

## 15. Feature N — User-Selectable Decision Policy, Not Just “More Agents”

Offer task-level goals that change the router objective. This is a policy choice, not merely a model dropdown.

Suggested policies:

- **Balanced:** best expected outcome with reasonable cost/latency.
- **Highest confidence:** additional verification for material uncertainties; still bounded by explicit caps.
- **Fast response:** minimize latency while meeting acceptance criteria.
- **Cost-minimal:** use the lowest-cost configuration that meets the quality floor.
- **Local/private:** only use approved local resources and tools.
- **Deep analysis:** maximize coverage and verification within declared limits.
- **Code-safe:** prioritize tests, isolation, patch review, and rollback safety.
- **Research-grade:** prioritize current primary sources, attribution, and recency.
- **Teaching mode:** prioritize step-by-step clarity, learner level, and checking understanding.
- **Custom budget:** explicit max agents, calls, tokens, time, or monetary spend.

The final answer can show a small rationale such as “selected the documentation specialist for API version checking, the coding specialist for the patch, and the test runner for verification.” Avoid long internal reasoning traces; expose decisions, evidence, and outcomes rather than private reasoning.

---

## 16. Feature O — Capability-Specific Evaluation Harness

The existing general-purpose Arena rubric is not enough to measure whether the router assigns the right specialist to the right subtask. This feature evaluates **routing quality itself**.

### 16.1 Evaluation dimensions

1. **Assignment fit:** Did the chosen executor possess the capabilities and tools required by the microtask?
2. **Contribution quality:** Did its specific artifact meet acceptance criteria?
3. **Marginal value:** Did this agent improve the deliverable beyond what was already available?
4. **Complementarity value:** Did the chosen combination discover or fix things the primary alone missed?
5. **Calibration:** Were confidence estimates consistent with measured outcomes?
6. **Routing regret:** How far was the result from the best observed eligible candidate for this task in the benchmark?
7. **Resource effectiveness:** Did the added quality justify cost and latency?
8. **Robustness:** Did performance persist across task variants and project contexts?
9. **Fairness of opportunity:** Does a newly added or locally hosted model ever get a measured chance, or does an incumbent permanently dominate because of historical data?
10. **Explainability:** Can the system show the observable evidence behind its routing decision?

### 16.2 Required baseline comparisons

Compare the new router against:

- fixed default model;
- best global-average model;
- random eligible specialist;
- rules-only router;
- existing Alpha router;
- an “always add one verifier” policy;
- a simple cost-quality cascade inspired by RouteLLM/FrugalGPT;
- a complementarity-aware greedy team selector.

Use paired tasks, consistent prompts and versions, deterministic test data where possible, and report confidence intervals or sample size. Do not report a percentage “improvement” from a tiny unrepresentative sample as a universal fact.

---

## 17. Feature P — Diversity Exploration Without Wasting Every Request

Purely exploitative routing can become trapped: an established agent keeps getting tasks, so it accumulates evidence, while newer specialists never get a chance. Pure exploration wastes user time and money.

Implement a controlled exploration policy:

- Normal user requests use the best known configuration subject to confidence and budget.
- A small optional share of safe benchmark or opt-in tasks tests under-measured candidates.
- Exploration is disabled for sensitive actions unless explicitly authorized.
- New candidates first run on synthetic, public, or privacy-safe examples.
- Promotion depends on measured benefit and regression checks.
- Stop exploring a candidate after sufficient evidence of poor fit, unless its model/skill/runtime changes.

The router must support a “no experimentation on my requests” setting. For personal/private projects, default to using run metadata for routing but do not send content to experimental providers outside normal permission rules.

---

## 18. Feature Q — Contribution Lineage and Capability-Aware Provenance

The existing plan includes provenance for selected contributions. The new extension records **who was suited for this contribution, what was transformed, and what is safe to reuse later**.

For every contribution slot, record:

- source agent/model/passport snapshot;
- exact task and context version IDs (not necessarily full sensitive content);
- skill/tool versions;
- raw output artifact hash and normalized representation;
- evidence/tests run and results;
- selection rationale by observable criteria;
- downstream transformations and the agent/tool that performed them;
- whether the final form differs materially from the original contribution;
- known limitations, licensing/provenance notes where relevant;
- reuse eligibility and expiration/version conditions.

When the final integrator rewrites a code snippet, claim, or instruction, do not keep credit attached to the original output as though nothing changed. The transformed output must be rechecked. For code, preserve file/line/patch identity; for research, preserve claim-to-source mapping; for plans, preserve step dependencies and assumption provenance.

---

## 19. Feature R — Dynamic Rubric Selection from the Microtask Type

The companion specification describes task-specific rubrics; the added capability here is to select rubrics at the **microtask/slot level** and to keep them linked to the required capability dimensions.

Examples:

- A code implementation slot emphasizes behavioral correctness, project conventions, type safety, and test evidence.
- A test-generation slot emphasizes requirement coverage, mutation sensitivity where available, determinism, and readability.
- A source-research slot emphasizes primary-source reliability, publication/version recency, claim support, and contradiction checks.
- An explanation slot emphasizes conceptual correctness, sequence, audience fit, and absence of misleading simplification.
- A security-review slot emphasizes threat coverage and reproducible evidence; a generic clarity score must not compensate for a missed critical vulnerability.
- A creative-writing slot emphasizes intended effect, tone, structure, constraints, and originality rather than factual consensus.

Do not let a single weighted average mask fatal failures. Define non-negotiable gates per slot type, then use weighted scoring only among candidates that pass them. Rubric versions must be pinned to each run so results remain interpretable after future rubric changes.

---

## 20. Feature S — Specialist-Generated Tests and Mutation Checks

Ask the system not only to review an answer but to create a **test that would reveal whether the answer is wrong**. This is particularly valuable when two specialist agents disagree about code, parsing, calculations, API behavior, or edge cases.

Potential additions:

- requirements-to-test traceability;
- edge-case and adversarial input generation;
- property-based tests for functions with clear invariants;
- mutation testing or mutation-inspired cases to see whether tests detect likely defects;
- independent test authoring, where the test specialist receives the requirements but not the implementation's rationale;
- minimized reproductions for failures;
- regression corpus creation from resolved incidents;
- test adequacy score that distinguishes “tests passed” from “tests actually cover the changed behavior.”

All generated tests must be treated as untrusted until reviewed and executed. Do not let an agent weaken or delete a test simply to make its own patch pass. Track the source requirement of each test, and require review for changes to safety-critical or access-control coverage.

---

## 21. Feature T — User Correction and Preference Feedback Loop

Give the user a lightweight way to correct the system after seeing the answer, without demanding a detailed rating every time.

Useful feedback signals include:

- “This source is outdated.”
- “This implementation does not run.”
- “This explanation is too complicated.”
- “The API/tool you chose cannot do this.”
- “I prefer the local model for this task.”
- “Agent X was useful for the test-writing step.”
- “You combined two incompatible suggestions.”

Treat each feedback item as a categorized observation, not universal truth. If a user says that one agent failed in one project, update a contextual signal for that project/task pattern; do not globally mark the agent bad. Offer undo/ignore controls for incorrect feedback and keep user preference memory separate from objective capability measurements.

---

## 22. Feature U — Multi-Outcome Answers When the Problem Has No Single Winner

The synthesis system must know when combining contributions is appropriate and when the task contains a genuine choice.

For trade-offs such as cost vs. performance, PostgreSQL vs. MySQL, speed vs. maintainability, or two deployment architectures, do not merge incompatible assumptions into a synthetic “best of both” answer. Build a **decision frontier**:

- list the viable options;
- show the constraint each option optimizes;
- state which assumptions decide the recommendation;
- identify the evidence that favors each option;
- recommend one option only under clearly named conditions;
- state what additional fact would change the recommendation.

For tasks with one required output, return one integrated answer. For inherently multi-objective tasks, preserve a small set of non-dominated options and provide a conditional recommendation. Do not overload users with alternatives when the evidence supports one clear answer.

---

## 23. Feature V — Capability Drift Detection

Agent performance can change when a provider updates a model, an endpoint changes behavior, tools fail, prompts are modified, a skill becomes stale, or the project environment changes.

Detect drift by monitoring:

- sudden changes in pass rate, judge disagreement, latency, timeout/error rate, or tool-call completion;
- quality variation by task category, not only overall averages;
- increases in output schema failures or unsupported claims;
- stale documentation/skill references;
- failed model smoke tests;
- changes in available context window or tool support.

When drift is detected:

1. mark the affected profile as degraded or stale;
2. limit high-risk assignments until validation succeeds;
3. run a small version-specific benchmark;
4. compare with a previous known-good version where available;
5. notify the user if the capability/cost/privacy impact is material;
6. roll back configuration when policy allows;
7. preserve audit evidence for debugging.

Do not automatically erase history; mark which evidence belongs to which version.

---

## 24. Feature W — Capability-Aware UI and Explainability

Add a compact UI view distinct from a verbose internal debug trace.

### Recommended panels

**Task map:** visible substeps and completion states.  
**Specialist assignments:** chosen agent/model/tool for each step, with a short observable reason.  
**Contribution map:** which result was selected for each slot and whether it passed tests/source checks.  
**Capability confidence:** “well measured,” “limited history,” or “unmeasured,” with sample count where meaningful.  
**Disagreements:** the important unresolved conflicts and how Alpha resolved them.  
**Resource meter:** calls, estimated cost, latency, and remaining budget.  
**Fallback notice:** clearly reveal meaningful switches in provider, locality, tools, or quality assumptions.  
**Learning controls:** allow users to rate a result, opt out of routing experiments, disable cross-run performance memory, or clear learned profile updates.

Do not render a giant model leaderboard as the primary UX; it encourages users to optimize for broad rank rather than task fit. Make the primary explanation “why this agent was selected for this particular step.”

---

## 25. Suggested New Data Contracts

These are conceptual Pydantic/dataclass-style schemas; align naming and storage with Alpha's actual code. Do not create duplicate registries if Alpha already has equivalent typed records.

```python
from typing import Literal
from pydantic import BaseModel, Field

EvidenceLevel = Literal[
    "unmeasured", "observed", "benchmarked",
    "outcome_verified", "recently_revalidated"
]

class CapabilityEstimate(BaseModel):
    capability: str
    task_category: str
    quality_mean: float | None = None
    uncertainty: float | None = None
    sample_count: int = 0
    evidence_level: EvidenceLevel = "unmeasured"
    last_evaluated_at: str | None = None
    profile_version: str

class CapabilityPassport(BaseModel):
    entity_id: str
    entity_type: Literal["agent", "model_config", "tool", "skill_bundle"]
    version: str
    model_provider: str | None = None
    model_id: str | None = None
    model_family: str | None = None
    capabilities: list[CapabilityEstimate] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    known_failure_modes: list[str] = Field(default_factory=list)
    enabled: bool = True

class SituationFingerprint(BaseModel):
    task_type: str
    subtask_type: str | None = None
    complexity_band: Literal["low", "medium", "high", "unknown"]
    recency_required: bool = False
    risk_band: Literal["low", "medium", "high", "critical"]
    context_pressure: Literal["low", "medium", "high"]
    required_capabilities: list[str]
    required_tools: list[str] = Field(default_factory=list)
    user_policy: str = "balanced"

class MicrotaskAssignment(BaseModel):
    microtask_id: str
    passport_id: str
    role: Literal["primary", "challenger", "verifier", "integrator"]
    fit_score: float
    score_explanation: list[str]
    expected_outputs: list[str]
    acceptance_checks: list[str]
    max_attempts: int = 1

class ContributionSlot(BaseModel):
    slot_id: str
    requirement_id: str
    capability_target: list[str]
    selected_artifact_id: str | None = None
    dependency_slots: list[str] = Field(default_factory=list)
    verification_status: Literal[
        "pending", "passed", "failed", "uncertain", "not_applicable"
    ] = "pending"
    compatibility_status: Literal[
        "unchecked", "compatible", "conflict", "alternative"
    ] = "unchecked"
```

Required invariants:

- No score without its profile/rubric version.
- No “measured” estimate with zero supporting observations.
- No assignment without explicit acceptance criteria.
- No unverified rewritten artifact inherits verified status automatically.
- No failed hard constraint can be overridden by an aggregate fit score.
- Do not persist raw user prompts in shared profile telemetry by default.

---

## 26. New Algorithm: Adaptive Capability-Mesh Dispatch

The following is a starting policy, not sacred logic. Implement a baseline first, evaluate it, then tune it from replay outcomes.

```text
INPUT: task, user policy, available agents/tools, capability passports

1. Fingerprint situation.
2. Decompose into the minimum useful set of microtasks.
3. For each microtask:
   a. Derive required capabilities, tools, and acceptance tests.
   b. Exclude agents that fail hard permission/runtime/tool constraints.
   c. Estimate task-specific fit and estimate uncertainty.
   d. Select a primary specialist.
   e. Estimate correlated-error risk and missing capability coverage.
   f. Add a challenger only if expected marginal value exceeds its cost.
   g. Prefer deterministic/executable verification when appropriate.
4. Run microtasks with bounded concurrency and isolated inputs.
5. Normalize outputs into contribution slots.
6. Evaluate each contribution for its target capability, not global agent rank.
7. Build dependency, disagreement, and compatibility graphs.
8. For each unresolved issue, choose the highest expected-value next action:
   test, source lookup, specialist, counterexample, user clarification, or stop.
9. Select best compatible contribution per slot.
10. Integrate using slot contracts and preserve provenance.
11. Revalidate the assembled answer/artifact.
12. Record measured outcomes against the precise agent/model/tool versions.
13. Return result and meaningful caveats; do not expose private reasoning traces.
```

### Stop conditions

Stop when all required slots pass their hard gates, unresolved high-impact conflicts are handled, and the expected benefit of further work is below the configured threshold—or when the budget is exhausted. If the budget is exhausted with meaningful gaps, say so rather than silently lowering requirements.

---

## 27. Implementation Roadmap — Only the Additive Features

Implementation order is based on dependency and measurable value, not on how impressive a feature sounds. Reuse the previous Arena+ foundation and Alpha's existing services. Do not duplicate them.

### Phase A — Capability passports and observation records

Build the versioned capability schema, evidence levels, task-category dimensions, model/tool identity and a profile viewer. Initially populate with `unknown` defaults and measured results from existing test runs. No learned routing until records are trustworthy.

**Acceptance:** profile provenance and versioning tests pass; missing evidence stays unknown; stale model configs are recognized.

### Phase B — Situation fingerprints and microtask routing

Add task fingerprinting, minimal subtask decomposition, fit scoring, hard eligibility checks, and primary/challenger/verifier assignment. Start with transparent rule-based routing using existing agent records.

**Acceptance:** multiple subtasks can pick different specialists; the routing rationale is inspectable; routine prompts avoid unnecessary fan-out.

### Phase C — Contribution slots and integration compiler

Add capability-targeted slots, slot contracts, dependency graph, compatibility detection, selected-source lineage, and post-assembly revalidation.

**Acceptance:** a specialist can win one slot and lose another; incompatible code/design fragments are rejected or preserved as alternatives; transformations trigger rechecks.

### Phase D — Complementarity and disagreement graph

Collect pair-level marginal-value outcomes, correlation signals, contradiction groups, and targeted counterfactual probes. Start with heuristic selection before attempting learned portfolio optimization.

**Acceptance:** agent diversity is not inferred from names alone; meaningful disagreement can trigger a test/source check; redundant calls are measurable.

### Phase E — Calibration and value-of-information scheduler

Separate candidate confidence from empirical quality, add sample-aware estimates, selective abstention, and next-action utility. Use benchmarked thresholds rather than invented universal percentages.

**Acceptance:** uncertain high-risk answers can escalate or qualify; low-value repeated opinions stop; budget exhaustion is explicit.

### Phase F — Outcome memory and router evaluation

Persist capability outcome metadata under privacy controls, build offline replay datasets, add shadow routing and canary/rollback policy, evaluate against baselines.

**Acceptance:** router versions are comparable; data is properly scoped and versioned; opt-out and deletion controls work; a bad policy can be rolled back.

### Phase G — Dynamic expert discovery and drift detection

Add temporary specialist templates, quarantined skills, capability smoke tests, model/version drift detection, and explicit fallback transparency.

**Acceptance:** temporary skills never promote themselves silently; unavailable endpoints result in honest fallback behavior; drift can suspend risky assignments.

### Phase H — Advanced controls and UI

Add policy profiles, contribution lineage UI, task-step specialist map, cost/quality explanation, preference feedback, and the multi-outcome decision frontier.

**Acceptance:** users can understand the assignment at a glance, control locality/budget/privacy, and see unresolved choices without reading raw logs.

---

## 28. Test Plan Specific to These New Features

### 28.1 Unit tests

- Capability passport schema, version checks, unknown values, evidence-level monotonicity rules.
- Situation fingerprint extraction for recency, risk, tool needs, context pressure, and user policy.
- Fit-score boundaries, hard exclusions, score explanation and deterministic tie breaks.
- Microtask decomposition does not drop requirements or invent unrelated work.
- Team selection prefers marginal capability coverage over duplicate agents when measured evidence supports it.
- Contribution slot dependency graph detects cycles and incompatible interfaces.
- Integration compiler preserves slot provenance and marks rewritten artifacts unverified.
- Calibration calculations remain stable for small samples and cannot represent unknown as perfect.
- Privacy retention and deletion rules apply to capability memory.

### 28.2 Scenario tests

1. **Different best agent per step:** use a deliberately mixed task where a documentation researcher is best at one step and a code specialist at another; assert that they are routed differently.
2. **Same-model illusion:** use three personas backed by one model; ensure Alpha does not count this as independent model-family agreement.
3. **Best local model constraint:** require local-only; make the local endpoint fail; assert Alpha does not switch to remote silently.
4. **Unmeasured specialist:** show a capable but new agent; assert it is neither automatically selected as best nor unfairly assigned a zero-quality score.
5. **Misleading global leaderboard:** create an agent with a high average score but a low score for the exact subtask; ensure a specialist with better relevant evidence wins.
6. **Conflict between great fragments:** two high-scoring code changes assume incompatible API contracts; integration must stop/resolve conflict rather than combine blindly.
7. **Strong challenger value:** primary answer contains a subtle defect; a complementary critic proposes a reproducer that fails; Alpha uses that test to repair the result.
8. **No additional information value:** agents repeat the same claims without new evidence; scheduler stops rather than spending more.
9. **Genuine trade-off:** two options optimize different constraints; Alpha preserves a decision frontier rather than combining them into nonsense.
10. **Model drift:** update the model or prompt version and inject benchmark regression; stale profile is flagged and router policy degrades safely.
11. **Feedback is contextual:** a user correction applies to one task pattern and does not globally blacklist the agent.
12. **Skill promotion gate:** a generated skill passes a smoke test but fails regression; it remains quarantined.

### 28.3 Evaluation metrics

Report at least:

- task success and hard-gate pass rate by category;
- slot-level contribution acceptance;
- router regret against best observed eligible candidate;
- marginal benefit per additional call;
- contribution complementarity and redundancy;
- independent verification coverage;
- confidence calibration and overconfidence rate;
- latency and cost at fixed quality thresholds;
- local-only/fallback policy violations (target: zero);
- stale-profile detection delay;
- number of integration conflicts caught before final output;
- user correction rate and repeated-error rate;
- performance by model family/provider, tool availability, context size and task complexity.

Do not make “number of agents used” a success metric. More agents is not the goal; better outcomes per unit of cost and time is.

---

## 29. Operational, Security, and Reliability Requirements

- Capability passports and routing records must be validated typed data, not free-form model output trusted directly.
- Agent-generated descriptions never grant tools or permissions.
- Tool eligibility is checked by Alpha's authorization layer before execution.
- Temporary specialists run with the minimum required tools and isolated workspaces.
- Tool output and retrieved web content are treated as untrusted data, not instructions that can rewrite policy.
- Capability memory must respect privacy scope and configured retention.
- Every profile estimate is tied to model, prompt, skill, tool and rubric versions.
- No silent provider downgrade when privacy or cost policy changes.
- No global ranking update from a single judge verdict.
- No self-promotion of skills, models, or router policies to production without the configured evaluation gate.
- Every long-running experiment has a hard cost/call/time/concurrency budget and cancellation mechanism.
- A restart restores the last consistent routing run state and avoids double-counting outcomes.
- Observability should make it possible to answer: why was this agent selected, what evidence backed that choice, what contribution did it add, and did the final result pass its gates?

---

## 30. Anti-Patterns to Explicitly Prevent

1. **One global “best AI” leaderboard.** It ignores situational strengths.
2. **Fixed role names as proof of skill.** “Research agent” is a description, not evidence.
3. **One scalar score for all capabilities.** Averages hide exactly the specialization Alpha needs.
4. **Majority voting as verification.** Correlated failures can agree confidently.
5. **Unlimited spawning.** New specialists must justify their marginal value and stay inside budgets.
6. **Blind answer collage.** Excellent fragments can contradict each other or break interfaces.
7. **Uncalibrated self-reported confidence.** Confidence claims are not measured correctness.
8. **Promoting skills after a successful demo.** Require regression-tested evidence.
9. **Permanent punishment from one failure.** Keep failures scoped to category, environment and version.
10. **Silent fallback.** A different provider, privacy boundary, or capability is a material event.
11. **Optimizing for long answers.** Length is not evidence of capability.
12. **Learning from every judge score equally.** Deterministic tests, source validation, and human acceptance should have distinct evidence weights.
13. **Endless debate.** When a test or authoritative source can settle the issue, use it.
14. **Rewriting verified artifacts without revalidation.** Every meaningful transformation resets affected checks.
15. **Changing the live router while evaluating it.** Version policies and use shadow/canary rollout.

---

## 31. Research References and What to Borrow Carefully

These works inform specific components of the proposed expansion; they do not prove that one architecture will automatically work best inside Alpha.

- **Arena Skill** — strategy-card diversity, attack/defend review, rubric judging and persistent tournament bookkeeping: <https://github.com/Jakeschincariol/arena-skill>
- **RouteLLM** — learned/rule-based routing between models and explicit quality-cost thresholds; useful as a baseline for routing policies, not as a complete specialist-team router: <https://github.com/lm-sys/RouteLLM>
- **FrugalGPT** — cascading and cost-aware selection policies; useful for the value-of-information and escalation layers: <https://arxiv.org/abs/2305.05176>
- **Mixture-of-Agents** — layered interaction where agents use earlier outputs; useful to test against the slot-based approach, while measuring whether each layer adds real value: <https://arxiv.org/abs/2406.04692>
- **LLMRouterBench** — broad, unified model-routing evaluation and the importance of evaluating quality/cost/latency with consistent datasets: <https://github.com/ynulihao/LLMRouterBench>
- **RouterBench** — multi-LLM routing benchmarks and comparison framework: <https://arxiv.org/abs/2403.12031>

Important design lesson: model complementarity is a measurable hypothesis. Do not assume that adding more models, adding more layers, or using a sophisticated router is necessarily better than a careful, small, well-calibrated team. Alpha should make the improvements measurable on its own representative task set.

---

## 32. Copy-Paste Implementation Prompt — Add Only These New Features

```text
You are the senior architect and implementation team for my Alpha AI agent repository:
https://github.com/itsPremkumar/alpha

Reference inspiration:
https://github.com/Jakeschincariol/arena-skill
https://github.com/lm-sys/RouteLLM
https://arxiv.org/abs/2406.04692
https://github.com/ynulihao/LLMRouterBench
https://arxiv.org/abs/2305.05176

Read these planning documents if present:
- alpha-arena-advanced-implementation-plan.md
- alpha-arena-new-capability-mesh-features-plan.md

SCOPE: Implement only the additive features specified in the new-features plan. Do not rebuild features that the existing Alpha code or the original Arena+ plan already provides. First inspect the actual repository and make an implementation matrix mapping each proposed feature to existing code, partially implemented code, missing code, tests, and integration points.

Primary objective:
Implement an Alpha Capability Mesh that routes each microtask to the most suitable measured agent/model/tool configuration, chooses complementary specialists rather than a generic crowd, selects the best verified contribution per step, and integrates compatible contributions into one coherent answer.

Mandatory feature groups:
1. Versioned capability passports for agents/models/tools/skills, with evidence levels, capability dimensions, profile provenance, and stale-version handling.
2. Situation fingerprinting and microtask-level capability routing, with hard eligibility checks and a transparent assignment rationale.
3. Complementarity-aware team formation and correlated-error awareness.
4. Dynamic specialist-gap detection and quarantined temporary skill/specialist proposals.
5. Capability-aware contribution slots plus an integration compiler with dependency and compatibility checks.
6. Empirical calibration, selective abstention, and value-of-information next-action scheduling.
7. Disagreement graphs and targeted counterfactual test/source probes.
8. Privacy-controlled capability outcome memory and slot-level credit attribution.
9. Shadow routing, replay benchmarks, canary policy promotion and rollback.
10. Joint model/tool/skill/context routing and explicit fallback disclosure.
11. User-selectable objective profiles, capability-specific evaluation and exploration controls.
12. Contribution lineage, dynamic microtask rubrics, specialist-generated tests, feedback learning, decision-frontier output, drift detection and UI explanation.

Non-negotiable instructions:
- Preserve every existing Alpha feature, API, command, tool, agent mode, plugin/MCP, memory mechanism and UI path unless a documented, tested migration is necessary and approved.
- Do not overwrite or replace an existing implementation before mapping dependencies.
- Reuse current LangGraph/Deep Agents, provider abstraction, persistence/checkpointer, permission, event, logging, and UI systems wherever suitable.
- Adapt file paths and names to the real repository. Do not create the proposed example directory blindly.
- Keep model/provider identity separate from agent persona and strategy prompt. Do not claim that multiple personas using one model are independent models.
- Unknown capability data must remain unknown, not score as perfect or zero by default.
- A single overall leaderboard must not determine task assignments. Score each agent for the specific capability and situation.
- Do not use majority vote as truth verification.
- Do not combine incompatible code, assumptions, or strategies blindly; use dependency/interface contracts and preserve genuine alternatives.
- Rewritten contributions must be revalidated.
- Local-only/private settings must never silently fall back to remote services. Log and disclose material provider, cost, tool, or capability changes.
- Do not let agent-generated tools, skills or router policies promote themselves to production.
- Do not persist sensitive prompts in shared telemetry by default. Implement configurable retention, opt-out, and deletion.
- Do not make API calls or spawn broad agent swarms during tests unless the test is explicitly marked live and budget-approved.
- Do not state tests passed unless they actually ran. Show exact commands, output summaries and unresolved failures.

Implementation sequence:
A. Audit repository; map current equivalents and gaps.
B. Design typed schemas and migrations with backward compatibility.
C. Implement capability passports + unit tests.
D. Implement situation fingerprint + routing baseline + assignment explanations.
E. Implement contribution slots and integration compiler.
F. Add complementarity, disagreement, calibrated uncertainty and value-of-information step by step.
G. Add privacy-controlled outcome memory, router replay/shadow/canary evaluation.
H. Add specialist discovery, drift controls, UI and user policy switches.
I. Run unit, integration, security, restart/recovery, routing benchmark and regression tests.
J. Report implemented, partially implemented, blocked, and not attempted items truthfully.

Before coding, produce a short implementation matrix and identify high-risk integration points. Then implement the smallest vertical slice end-to-end rather than generating many disconnected modules. After each phase run relevant tests. Keep changes in an isolated branch/worktree, preserve unrelated user changes, and provide a reversible migration/rollback. Finish with an acceptance matrix linking every new feature to code, tests, evidence, and limitations.
```

---

## Final Recommendation

Prioritize the **Capability Passport → Microtask Router → Contribution Slots/Integration Compiler** path first. Those three additions create the foundation for the central capability the user described: one agent can provide the strongest implementation, another the strongest tests, another the best current-source check, and a fourth the clearest explanation—then Alpha combines those role-specific strengths into one result that passes the relevant checks.

Next add complementarity-aware team selection and disagreement-triggered tests. Then implement calibration, value-of-information scheduling, outcome learning and router rollout safety. Save automatic specialist creation and self-improving routing for after the measurement/evaluation foundation is stable.

The success metric is not “Alpha used more agents.” It is: **Alpha selected the right capability for each step, combined compatible verified contributions, caught more real errors, avoided unnecessary calls, and can explain why the final result is trustworthy.**
