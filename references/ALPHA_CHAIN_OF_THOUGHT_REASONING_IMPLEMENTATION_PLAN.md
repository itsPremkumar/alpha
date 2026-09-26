# Alpha — Advanced Reasoning / Chain-of-Thought Implementation Plan

**Project:** Alpha — Autonomous Multi-Agent Operating System & Frontier Cognitive Intelligence Platform  
**Repository:** https://github.com/itsPremkumar/alpha  
**Document date:** 2026-09-25  
**Purpose:** Design and implementation blueprint for adding robust reasoning capabilities to Alpha without exposing private model chain-of-thought.

---

## 0. Executive decision

Alpha should **not** be implemented as a system that asks every model to print its complete chain-of-thought.

The recommended design is a **Reasoning Control Plane** that combines:

1. Native model reasoning / extended thinking when the provider supports it.
2. Explicit Alpha planning state that is visible to the runtime and UI.
3. ReAct-style reasoning/action/observation cycles for tool work.
4. Selective Tree-of-Thought-style branching only for problems that benefit from search.
5. Independent verification and evidence collection.
6. Reflection after failures and bounded retries.
7. Reasoning summaries and decision records instead of raw private reasoning traces.
8. Durable checkpoints so a long reasoning task can resume.
9. Adaptive reasoning budgets based on task complexity, uncertainty, failure rate, and expected value.
10. Evaluation infrastructure that measures task success, evidence quality, recovery, latency, and cost.

Modern reasoning models already perform internal reasoning. OpenAI explicitly recommends avoiding prompts such as “think step by step” for reasoning models and states that reasoning tokens are not exposed through the API; OpenAI provides optional reasoning summaries instead. Anthropic similarly exposes extended-thinking controls and summarized thinking blocks, including reasoning interleaved with tool calls. Therefore Alpha should treat private model reasoning as a **provider capability**, while Alpha owns the observable **reasoning process** around it.  
Sources: [OpenAI Reasoning Best Practices](https://developers.openai.com/api/docs/guides/reasoning-best-practices), [OpenAI Reasoning Models](https://developers.openai.com/api/docs/guides/reasoning), [Anthropic Extended Thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking).

---

# 1. What “chain of thought” means for Alpha

There are three different things that are often incorrectly treated as the same feature.

| Layer | Meaning | Alpha recommendation |
|---|---|---|
| Private model reasoning | Hidden computation performed by a reasoning-capable model | Use when supported; do not depend on raw trace access |
| Agent working reasoning | Plans, hypotheses, decisions, tool observations, constraints, evidence, verifier results | Make this first-class Alpha state |
| User-facing explanation | Concise rationale, evidence, assumptions, result, and next steps | Show this; never require a raw internal monologue |

The distinction is critical because the agent needs a reliable control loop even when the selected model changes.

For example, Alpha may run with:

- a frontier reasoning API model;
- a normal tool-calling model;
- a local Ollama reasoning model;
- a smaller model for cheap subtasks;
- several heterogeneous models in a council.

The Alpha runtime should behave consistently across all of them.

### Recommended abstraction

```text
User Goal
   ↓
Mission Normalizer
   ↓
Complexity / Risk / Uncertainty Assessment
   ↓
Reasoning Policy
   ↓
┌──────────────────────────────────────────────────────────────┐
│                  REASONING CONTROL PLANE                    │
│                                                              │
│ Planner → Hypothesis → Action → Observation → Verification  │
│    ↑                                   │                     │
│    └──────── Reflection / Re-plan ─────┘                     │
│                                                              │
│ Optional: Branch Search / ToT / MoA / Council / Subagents   │
└──────────────────────────────────────────────────────────────┘
   ↓
Evidence-backed Completion Gate
   ↓
Artifact + Answer + Reasoning Summary
```

---

# 2. What the current Alpha repository already provides

The live Alpha repository is already a strong base for this feature.

The root `AGENTS.md` describes Alpha as a LangGraph-based super-agent system with per-thread isolated environments, persistent memory, subagent delegation, extensible tools, sandboxing, and a model factory with thinking/vision support. The backend guide shows the main agent package, thread state, middleware system, subagent executor, models package, MCP, skills, sandbox, and reflection-related infrastructure. The repository also has explicit run acceptance criteria, verification overlays, benchmark/release gates, durable scheduling, and mandatory TDD rules.  
Sources: [Alpha AGENTS.md](https://github.com/itsPremkumar/alpha/blob/main/AGENTS.md), [Alpha backend AGENTS.md](https://github.com/itsPremkumar/alpha/blob/main/backend/AGENTS.md).

That means the main task should be **integration and strengthening**, not building an entirely separate agent framework.

The existing Alpha README describes a cognitive planner, orchestration, deep research, continuous execution, MoA, epistemic evaluation, consequence simulation, cognitive memory, quality council, and a dedicated reasoning subsystem. This plan turns those high-level concepts into an implementation contract with clear state, interfaces, gates, and tests.  
Source: [Alpha README](https://github.com/itsPremkumar/alpha/blob/main/README.md).

---

# 3. Research-derived design principles

## 3.1 Native reasoning should be first-class

OpenAI's current reasoning guidance says reasoning models work best with simple, direct instructions and that explicit “think step by step” prompting is unnecessary for those models. OpenAI also documents reasoning effort and optional reasoning summaries.  
Source: [OpenAI Reasoning Best Practices](https://developers.openai.com/api/docs/guides/reasoning-best-practices).

**Alpha design:**

```python
ReasoningMode.NATIVE
ReasoningMode.EXPLICIT
ReasoningMode.HYBRID
ReasoningMode.ROUTED
```

`NATIVE` = provider reasoning is primary.  
`EXPLICIT` = Alpha creates a structured plan/verification loop for non-reasoning models.  
`HYBRID` = native model reasoning + Alpha orchestration.  
`ROUTED` = policy automatically selects the least expensive strategy that satisfies the task risk/complexity contract.

---

## 3.2 Reasoning must be interleaved with tool use

ReAct demonstrated the value of interleaving reasoning with actions and observations rather than producing a static answer first. Anthropic's current extended-thinking documentation also supports interleaved thinking around tool calls.  
Sources: [ReAct](https://arxiv.org/abs/2210.03629), [Anthropic Extended Thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking).

Alpha therefore should not use:

```text
Plan everything → execute everything → answer
```

for difficult tasks.

Prefer:

```text
Plan a small step
→ perform action/tool call
→ inspect observation
→ update hypothesis
→ verify
→ continue or re-plan
```

This is especially important for:

- browser research;
- repository debugging;
- shell execution;
- database investigation;
- API integration;
- long-running automation;
- self-repair;
- multi-agent workflows.

---

## 3.3 Branching should be selective, not permanent

Tree of Thoughts generalizes chain-style reasoning by allowing multiple candidate reasoning paths, evaluation, backtracking, and deliberate search.  
Source: [Tree of Thoughts](https://arxiv.org/abs/2305.10601).

Alpha should use ToT-like search only when the task has one or more of these properties:

- several plausible strategies;
- high cost of an early wrong decision;
- reversible exploration;
- a good evaluation function;
- a search space small enough to control;
- a verifier capable of comparing candidate solutions.

Do **not** branch for normal chat or trivial coding requests.

---

## 3.4 Verification must be separate from generation

Process supervision research demonstrates the value of checking intermediate steps, while more recent research continues to explore process-level verification and external critique. `Let's Verify Step by Step` showed the usefulness of step-level supervision for mathematical reasoning.  
Sources: [Let's Verify Step by Step](https://arxiv.org/abs/2305.20050), [Verifiable Process Reward Models](https://arxiv.org/html/2601.17223v1).

Alpha should therefore distinguish:

```text
Generator / Actor
        ≠
Verifier / Critic
```

For high-impact tasks, the verifier should ideally be:

- a deterministic program;
- a test suite;
- a compiler/type checker;
- an API response;
- a database invariant;
- a second model;
- or a combination.

Never accept “the model says it is correct” as the only verification signal for important work.

---

## 3.5 Reflection should create reusable learning signals

Reflexion uses verbal feedback and episodic memory rather than changing model weights to help later attempts. Self-Refine uses iterative generation, feedback, and revision without additional model training.  
Sources: [Reflexion](https://arxiv.org/abs/2303.11366), [Self-Refine](https://arxiv.org/abs/2303.17651).

This maps directly to Alpha's existing persistent-memory and self-improvement direction.

The important distinction is:

```text
Raw trajectory
     ↓
Failure analysis
     ↓
Compact lesson / rule / observation
     ↓
Validated memory
```

Do not blindly store every reasoning trace in long-term memory.

---

## 3.6 Reasoning capability can emerge from training, but Alpha should remain training-agnostic

DeepSeek-R1 reports that reinforcement learning can incentivize reasoning patterns such as self-reflection, verification, and dynamic strategy adaptation.  
Source: [DeepSeek-R1](https://arxiv.org/abs/2501.12948).

Alpha does not need to train a reasoning model to benefit from those patterns.

The runtime should support them through inference-time orchestration:

```text
Reason → Act → Observe → Verify → Reflect → Re-plan
```

Later Alpha versions may add an evaluation dataset and training pipeline, but that should be a separate project from the runtime reasoning control plane.

---

# 4. Alpha reasoning architecture

Add a dedicated subsystem under the existing harness package:

```text
backend/packages/harness/alpha/reasoning/
├── __init__.py
├── AGENTS.md
├── models.py
├── policy.py
├── budget.py
├── router.py
├── planner.py
├── hypothesis.py
├── loop.py
├── action.py
├── observation.py
├── evidence.py
├── verifier.py
├── critic.py
├── reflection.py
├── branch_search.py
├── consensus.py
├── uncertainty.py
├── compression.py
├── summary.py
├── provenance.py
├── metrics.py
├── limits.py
└── protocols.py
```

Recommended responsibility boundaries:

| Module | Responsibility |
|---|---|
| `models.py` | Typed Pydantic/TypedDict schemas for reasoning state |
| `policy.py` | Select reasoning strategy based on task properties |
| `budget.py` | Token, time, tool-call, branch, and retry budgets |
| `router.py` | Select model/provider/reasoning strategy |
| `planner.py` | Create and update task plans |
| `hypothesis.py` | Represent candidate explanations/solutions |
| `loop.py` | Main reasoning/action/observation cycle |
| `action.py` | Structured tool/action intent |
| `observation.py` | Normalize tool/environment results |
| `evidence.py` | Evidence records and provenance |
| `verifier.py` | Deterministic/model/hybrid verification |
| `critic.py` | Adversarial challenge of candidate results |
| `reflection.py` | Failure analysis and bounded self-correction |
| `branch_search.py` | Selective ToT-style branching/backtracking |
| `consensus.py` | MoA/council aggregation |
| `uncertainty.py` | Confidence and uncertainty estimation |
| `compression.py` | Context compaction and reasoning-state compression |
| `summary.py` | User-safe reasoning summaries |
| `provenance.py` | Link actions, evidence, artifacts and state transitions |
| `metrics.py` | Quality/cost/latency/recovery metrics |
| `limits.py` | Hard safety/runtime limits |
| `protocols.py` | Provider-neutral interfaces and extension contracts |

---

# 5. Reasoning state model

Do not put the entire reasoning history into `messages`.

Use a separate structured state object.

Suggested schema:

```python
class ReasoningState(TypedDict, total=False):
    run_id: str
    mission_id: str
    parent_reasoning_id: str | None

    objective: str
    success_criteria: list[str]
    constraints: list[str]
    non_goals: list[str]

    strategy: str
    mode: str
    phase: str

    plan_id: str | None
    active_step_id: str | None
    completed_step_ids: list[str]

    hypotheses: list[HypothesisRecord]
    selected_hypothesis_id: str | None

    observations: list[ObservationRecord]
    evidence: list[EvidenceRecord]
    verification: list[VerificationRecord]

    unresolved_questions: list[str]
    assumptions: list[str]
    contradictions: list[ContradictionRecord]

    reflection_events: list[ReflectionRecord]
    retry_count: int
    replan_count: int
    branch_count: int

    confidence: float | None
    uncertainty: UncertaintyRecord | None

    token_budget: int | None
    time_budget_seconds: float | None
    tool_budget: int | None

    reasoning_summary: list[ReasoningSummaryItem]
    decision_log: list[DecisionRecord]

    status: str
    stop_reason: str | None
```

### Key rule

`messages` are conversation/model inputs.

`ReasoningState` is the **agent's structured cognitive control state**.

`artifacts/evidence` are persistent outputs.

This keeps context manageable and makes the agent resumable.

---

# 6. Reasoning record types

## 6.1 HypothesisRecord

```python
class HypothesisRecord(BaseModel):
    id: str
    statement: str
    type: Literal[
        "solution",
        "diagnosis",
        "plan",
        "explanation",
        "research_claim",
    ]
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    assumptions: list[str]
    status: Literal[
        "candidate",
        "active",
        "supported",
        "refuted",
        "superseded",
    ]
    created_at: str
```

## 6.2 ObservationRecord

```python
class ObservationRecord(BaseModel):
    id: str
    source: Literal["tool", "user", "model", "system", "test"]
    tool_name: str | None
    input_digest: str | None
    output_digest: str | None
    summary: str
    facts: list[str]
    errors: list[str]
    timestamp: str
```

## 6.3 EvidenceRecord

```python
class EvidenceRecord(BaseModel):
    id: str
    claim: str
    source: str
    source_type: Literal[
        "primary",
        "secondary",
        "runtime",
        "test",
        "artifact",
        "user",
    ]
    locator: str | None
    verified: bool
    verification_method: str | None
    confidence: float | None
    created_at: str
```

## 6.4 VerificationRecord

```python
class VerificationRecord(BaseModel):
    id: str
    target: str
    method: str
    status: Literal["pass", "fail", "partial", "blocked"]
    evidence_ids: list[str]
    failure_reason: str | None
    independent: bool
    timestamp: str
```

## 6.5 DecisionRecord

A decision record should contain:

```text
decision
why it was selected
alternatives considered
supporting evidence
risk / reversibility
expected next action
```

This is the right user-facing substitute for exposing an entire private monologue.

---

# 7. Reasoning policy engine

Create a policy layer that classifies every mission before choosing a reasoning pattern.

### Inputs

```text
task complexity
risk level
ambiguity
reversibility
number of tools expected
need for external evidence
need for code execution
need for branching
need for multi-agent diversity
model capabilities
available token budget
available time budget
historical success rate
```

### Output

```python
ReasoningPolicy(
    mode="hybrid",
    reasoning_effort="high",
    max_iterations=12,
    max_branches=0,
    verifier="hybrid",
    reflection=True,
    use_subagents=True,
    evidence_required=True,
    native_thinking=True,
)
```

### Suggested policy classes

```text
TRIVIAL
FAST
STANDARD
DEEP
RESEARCH
CODE
HIGH_RISK
SYSTEM_CHANGE
SELF_IMPROVEMENT
```

Examples:

| Task | Strategy |
|---|---|
| “Explain Python list comprehensions” | FAST |
| “Fix this failing unit test” | CODE + verification |
| “Research open-source memory systems” | RESEARCH + evidence |
| “Refactor an entire subsystem” | DEEP + plan + execution + independent verification |
| “Change production configuration” | HIGH_RISK + approval + independent verification |
| “Improve Alpha itself” | SELF_IMPROVEMENT + benchmark gate + rollback |

---

# 8. Core Alpha reasoning loop

Implement the main control loop approximately as follows:

```text
START
  │
  ├─ Parse mission
  │
  ├─ Establish acceptance criteria
  │
  ├─ Classify complexity/risk
  │
  ├─ Select model + reasoning policy
  │
  ├─ Build initial plan
  │
  ▼
REASONING CYCLE
  │
  ├─ Select next step
  │
  ├─ Generate action intent
  │
  ├─ Policy / permission gate
  │
  ├─ Execute tool or delegate
  │
  ├─ Normalize observation
  │
  ├─ Update hypotheses
  │
  ├─ Update evidence
  │
  ├─ Verify the step when appropriate
  │
  ├─ Detect contradiction / failure / loop
  │
  ├─ Reflect when needed
  │
  └─ Decide:
       ├─ Continue
       ├─ Re-plan
       ├─ Branch
       ├─ Delegate
       ├─ Ask user
       └─ Finish
  │
  ▼
COMPLETION GATE
  │
  ├─ Acceptance criteria satisfied?
  ├─ Required evidence present?
  ├─ Independent verification passed?
  ├─ No unresolved critical contradictions?
  ├─ No budget violation?
  └─ Artifact integrity verified?
  │
  ▼
OUTPUT
```

This structure aligns well with Alpha's existing LangGraph run state and verification architecture.

---

# 9. Native reasoning adapter

The model layer should expose a provider-neutral interface:

```python
class ReasoningAdapter(Protocol):
    async def generate(
        self,
        request: ReasoningRequest,
    ) -> ReasoningResponse: ...

    def capabilities(self) -> ReasoningCapabilities: ...
```

Suggested capabilities:

```python
class ReasoningCapabilities(BaseModel):
    native_reasoning: bool
    reasoning_effort: bool
    reasoning_summary: bool
    tool_interleaving: bool
    structured_output: bool
    parallel_tool_calls: bool
    context_compaction: bool
    continuation: bool
```

### OpenAI adapter

For supported reasoning models:

- use native reasoning;
- use `reasoning_effort` where supported;
- optionally request reasoning summaries;
- do not instruct the model to print its private chain of thought;
- reserve enough output/context capacity for reasoning tokens.

OpenAI documents that reasoning tokens are not visible through the API, consume context space, and can account for a substantial portion of output-token usage.  
Source: [OpenAI Reasoning Models](https://developers.openai.com/api/docs/guides/reasoning).

### Anthropic adapter

For supported Claude models:

- use adaptive/manual extended thinking according to model generation;
- use appropriate reasoning budget/effort configuration;
- preserve required thinking blocks for tool-loop continuation where the API requires it;
- exploit interleaved thinking for complex tool workflows.

Anthropic currently documents manual budgets, adaptive thinking for newer models, and interleaved thinking with tool calls.  
Source: [Anthropic Extended Thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking).

### Local/Ollama adapter

When a local model does not expose a native reasoning contract, Alpha should fall back to:

```text
Explicit planner
+ structured scratch artifacts
+ ReAct tool loop
+ verifier
+ reflection
```

Do not pretend that every local model has the same reasoning capabilities.

---

# 10. Explicit scratchpad for non-reasoning models

For ordinary models, create an Alpha-managed working memory instead of prompting the model to emit a giant monologue.

Example files:

```text
.runtime/reasoning/<run_id>/
├── objective.md
├── plan.json
├── hypotheses.json
├── observations.jsonl
├── evidence.jsonl
├── decisions.jsonl
├── verification.jsonl
├── reflection.jsonl
├── unresolved.md
├── context-summary.md
└── completion.json
```

The LLM should receive a **small current working set** rather than all historical records.

Example context:

```text
OBJECTIVE
<current objective>

CURRENT PLAN STEP
<one active step>

KNOWN FACTS
<verified facts only>

OPEN QUESTIONS
<questions still unresolved>

LATEST OBSERVATIONS
<last N relevant observations>

AVAILABLE EVIDENCE
<high-value evidence references>

CONSTRAINTS
<important constraints>

NEXT-ACTION CONTRACT
Return exactly one next action or a final result.
```

This is much more scalable than passing the entire trajectory on every turn.

---

# 11. ReAct implementation

Use a structured variant of ReAct:

```text
REASON STATE
    ↓
ACTION INTENT
    ↓
TOOL
    ↓
OBSERVATION
    ↓
STATE UPDATE
```

Do not require literal text labels such as `Thought:` in the user-visible response.

Represent them as structured runtime events:

```json
{
  "type": "reasoning.action_decision",
  "run_id": "...",
  "step_id": "...",
  "action": {
    "tool": "search",
    "purpose": "verify the current implementation detail"
  },
  "requires_approval": false
}
```

And:

```json
{
  "type": "reasoning.observation",
  "step_id": "...",
  "summary": "Search result confirms X",
  "evidence_ids": ["ev_123"]
}
```

This gives the UI useful progress without exposing hidden token-by-token reasoning.

---

# 12. Planning hierarchy

Alpha already has planning/mission/goal infrastructure. Reasoning should attach to that hierarchy instead of creating another task system.

Recommended levels:

```text
Mission
└── Objective
    ├── Phase
    │   ├── Task
    │   │   ├── Subtask
    │   │   └── Verification
    │   └── Milestone
    └── Completion Gate
```

Every reasoning step should map to exactly one:

- mission;
- plan step;
- subtask;
- verification target.

This prevents “free-floating thoughts” with no operational consequence.

OpenAI's current Codex planning guidance also emphasizes living execution plans for long-running implementation work; Alpha can use the same idea while keeping its own LangGraph state authoritative.  
Source: [OpenAI Cookbook — PLANS.md / ExecPlans](https://github.com/openai/openai-cookbook/blob/main/articles/codex_exec_plans.md).

---

# 13. Hypothesis-driven reasoning

For tasks where the answer is uncertain, explicitly create hypotheses.

Example debugging task:

```text
H1: Database connection pool is exhausted
H2: Request timeout is caused by an external API
H3: Async code is blocking the event loop
H4: Recent middleware change introduced a deadlock
```

Then gather evidence that distinguishes them.

Avoid:

```text
The model guesses H4 → edits code → declares success.
```

Prefer:

```text
Generate candidate hypotheses
→ rank by current evidence
→ execute discriminating tests
→ update confidence
→ eliminate/refine hypotheses
→ select the solution
```

This pattern is especially useful for Alpha's coding and self-repair capabilities.

---

# 14. Evidence engine

Every important reasoning claim should be optionally connected to evidence.

Evidence classes:

```text
USER_FACT
SOURCE_DOCUMENT
WEB_SOURCE
REPOSITORY_FILE
TOOL_RESULT
COMPILER_RESULT
UNIT_TEST
INTEGRATION_TEST
RUNTIME_METRIC
DATABASE_OBSERVATION
ARTIFACT_HASH
OTHER_AGENT_REPORT
```

Create an evidence graph:

```text
Claim
 │
 ├── supports → Evidence A
 ├── supports → Evidence B
 └── contradicted by → Evidence C
```

For research tasks, prefer primary/vendor/project documentation when available. For coding tasks, prefer executable evidence.

Alpha's existing evidence matrix and verification contracts make this a natural integration point rather than a parallel subsystem.  
Source: [Alpha README](https://github.com/itsPremkumar/alpha/blob/main/README.md), [Alpha backend AGENTS.md](https://github.com/itsPremkumar/alpha/blob/main/backend/AGENTS.md).

---

# 15. Verification architecture

Implement multiple verifier classes.

```python
class Verifier(Protocol):
    async def verify(
        self,
        target: VerificationTarget,
        context: VerificationContext,
    ) -> VerificationRecord: ...
```

Concrete implementations:

```text
DeterministicVerifier
TestVerifier
CompilerVerifier
SchemaVerifier
ArtifactVerifier
EvidenceVerifier
ModelCriticVerifier
CrossModelVerifier
HumanApprovalVerifier
CompositeVerifier
```

### Verification policy

```text
LOW RISK
→ lightweight consistency check

MEDIUM
→ evidence + model critic

CODE
→ tests + type/lint/compiler

RESEARCH
→ source verification + contradiction search

HIGH IMPACT
→ deterministic evidence + independent reviewer

SELF-IMPROVEMENT
→ benchmark + regression + rollback checkpoint
```

The final completion gate must be fail-closed whenever required evidence is missing.

---

# 16. Reflection engine

After a failed attempt:

```text
Failure
  ↓
Classify root cause
  ↓
Was the plan wrong?
Was the tool wrong?
Was the assumption wrong?
Was the model insufficient?
Was the environment broken?
Was the verifier wrong?
  ↓
Create reflection record
  ↓
Change only the necessary part
  ↓
Retry
```

Reflection schema:

```json
{
  "failure_class": "wrong_assumption",
  "what_failed": "API endpoint assumption was incorrect",
  "evidence": ["ev_234"],
  "lesson": "Verify endpoint availability before coding against it",
  "next_change": "Search official API reference",
  "should_persist_to_memory": true,
  "confidence": 0.87
}
```

### Reflection must be bounded

Never allow:

```text
failure → reflect → retry → fail → reflect → retry → infinite loop
```

Use:

```text
max_attempts
max_reflections
max_replans
cooldown/backoff
loop detector
marginal-progress detector
```

---

# 17. Tree-of-Thought-like branch search

Implement branching as a separate strategy, not as the default agent loop.

### Branch schema

```python
class ReasoningBranch(BaseModel):
    id: str
    parent_id: str | None
    hypothesis_id: str
    proposed_action: str
    estimated_value: float | None
    evidence_ids: list[str]
    status: Literal[
        "candidate",
        "running",
        "completed",
        "pruned",
        "failed",
    ]
```

### Search strategies

Support:

```text
Best-first search
Beam search
Depth-limited search
Branch-and-bound
Early pruning
Backtracking
```

Start with **beam search with a small beam width** because it is operationally simpler.

Example:

```text
beam_width = 2
max_depth = 3
max_total_branches = 6
```

The verifier selects branches based on actual evidence rather than purely model-generated preference.

---

# 18. Mixture-of-Agents / Council reasoning

Alpha already describes MoA / multi-perspective reasoning. Implement it as a bounded meta-reasoner.

```text
             ┌── Researcher A ──┐
User task →  ├── Researcher B ──┼→ Synthesizer → Verifier
             ├── Coder C ───────┤
             └── Critic D ──────┘
```

Use heterogeneous roles instead of identical agents.

Recommended roles:

```text
Builder
Researcher
Adversarial Critic
Verifier
Alternative Solver
Synthesizer
```

Each worker should receive the minimum context necessary.

DeerFlow and Deep Agents both emphasize isolated subagents, scoped context, long-horizon execution, and delegation as ways to prevent the primary context from becoming overloaded.  
Sources: [DeerFlow Core Concepts](https://github.com/bytedance/deer-flow/blob/main/frontend/src/content/en/introduction/core-concepts.mdx), [Deep Agents](https://github.com/langchain-ai/deepagents/blob/main/README.md).

---

# 19. Context engineering

Reasoning quality degrades when the context is overloaded.

Use progressive disclosure:

```text
Always loaded:
  mission
  constraints
  active plan
  critical memory
  active evidence

Loaded on demand:
  old observations
  large tool outputs
  historical branches
  repository files
  previous attempts

Archived:
  raw tool output
  superseded branches
  old scratch state
```

Deep Agents explicitly uses filesystem-backed context management, summarization, subagent isolation, memory, and context offloading for long-running work. DeerFlow similarly treats context engineering, subagents, memory, and filesystem artifacts as core runtime concerns.  
Sources: [Deep Agents Overview](https://github.com/langchain-ai/docs/blob/main/src/oss/deepagents/overview.mdx), [Deep Agents Architecture](https://github.com/langchain-ai/deepagents/blob/main/libs/ARCHITECTURE.md), [DeerFlow Core Concepts](https://github.com/bytedance/deer-flow/blob/main/frontend/src/content/en/introduction/core-concepts.mdx).

### Alpha rule

Never summarize away:

- acceptance criteria;
- unresolved critical failures;
- evidence references;
- current branch/plan state;
- pending approvals;
- irreversible-action warnings.

Summarization can compress narrative details but must preserve control state.

---

# 20. Adaptive reasoning budget

Create a budget manager.

Budget dimensions:

```text
tokens
wall-clock time
LLM calls
tool calls
subagents
branches
replans
reflections
web searches
file reads
shell commands
```

The budget should be dynamic.

Example:

```python
budget = ReasoningBudget(
    max_model_calls=20,
    max_tool_calls=50,
    max_iterations=30,
    max_branches=4,
    max_replans=5,
    max_reflections=5,
    time_limit_seconds=1800,
)
```

Then add a marginal-value rule:

```text
Continue only when expected information/value gain
is greater than expected cost/risk.
```

For a simple task, stop early.

For a hard research/coding task, increase compute.

Do not use maximum reasoning for every request.

OpenAI documents adjustable reasoning effort and notes that reasoning tokens consume context/output capacity; Anthropic similarly documents thinking budgets/effort and warns about latency and diminishing returns as budgets rise.  
Sources: [OpenAI Reasoning](https://developers.openai.com/api/docs/guides/reasoning), [Anthropic Extended Thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking).

---

# 21. Uncertainty engine

Confidence should not be a single arbitrary number produced by the LLM.

Track distinct uncertainty sources:

```text
FACTUAL_UNCERTAINTY
MODEL_UNCERTAINTY
TOOL_UNCERTAINTY
ENVIRONMENT_UNCERTAINTY
PLAN_UNCERTAINTY
VERIFICATION_UNCERTAINTY
```

Example:

```json
{
  "overall": 0.78,
  "factual": 0.91,
  "model": 0.73,
  "tool": 0.94,
  "environment": 0.61,
  "verification": 0.84,
  "method": "composite"
}
```

Use confidence to decide:

- whether to continue research;
- whether to ask the user;
- whether to use another model;
- whether to branch;
- whether to require independent verification;
- whether to stop.

Do not present confidence values as objectively calibrated probabilities unless they were actually calibrated and evaluated.

---

# 22. Reasoning router

Alpha should route between models and strategies.

```text
Task
 ↓
Capability check
 ↓
Model registry
 ↓
Reasoning policy
 ↓
Provider selection
 ↓
Execution
```

Example:

```yaml
reasoning:
  default_mode: hybrid
  routing:
    trivial: cheap
    standard: balanced
    deep: reasoning
    research: reasoning
    coding: code_reasoning
    high_risk: strongest_available
  escalation:
    enabled: true
    after_failed_verification: true
    after_contradiction: true
    after_repeated_tool_failure: true
```

Routing must be provider-neutral.

Do not hardcode Alpha's logic around one vendor.

---

# 23. Provider fallback and degradation

When a model/provider fails:

```text
Provider timeout
   ↓
Retry same provider if safe
   ↓
Switch model
   ↓
Preserve reasoning state
   ↓
Continue from last verified checkpoint
```

Never silently restart from zero.

The checkpoint should contain:

- objective;
- plan;
- active step;
- verified observations;
- evidence;
- remaining budget;
- retry history;
- provider/model metadata;
- pending tool call status.

---

# 24. Never mix private chain-of-thought with durable memory

This is a major Alpha architectural rule.

### Do NOT store by default

```text
raw hidden reasoning tokens
full unfiltered internal monologues
provider-encrypted thinking blocks as application memory
sensitive credential-bearing thoughts
unbounded intermediate chain logs
```

### Store instead

```text
goal
plan
decision
observation summary
evidence
verification
failure reason
reflection lesson
artifact provenance
user-visible reasoning summary
```

The result is more compact, portable, auditable, and provider-neutral.

---

# 25. User-facing reasoning UI

Alpha's UI should show **progressive reasoning transparency**, not raw private thought.

Suggested interface:

```text
┌──────────────────────────────────────────┐
│ Alpha is working                         │
│                                          │
│ ✓ Goal understood                        │
│ ✓ Plan created                           │
│ ✓ Repository inspected                   │
│ ⟳ Testing hypothesis 2/4                │
│ ✓ Evidence verified                      │
│ ⟳ Running final validation               │
│                                          │
│ Evidence: 8 sources · 14 checks         │
│ Attempts: 2                              │
│ Current step: Validate API behavior      │
└──────────────────────────────────────────┘
```

Allow the user to open:

```text
Plan
Evidence
Decisions
Verification
Artifacts
Failures / recovery
Reasoning summary
```

A “Reasoning summary” should be concise and derived from structured decision records.

---

# 26. Reasoning events

Create a stable event taxonomy.

```text
reasoning.started
reasoning.policy.selected
reasoning.plan.created
reasoning.step.started
reasoning.hypothesis.created
reasoning.action.proposed
reasoning.action.approved
reasoning.action.executed
reasoning.observation.received
reasoning.evidence.added
reasoning.contradiction.detected
reasoning.verification.started
reasoning.verification.completed
reasoning.reflection.started
reasoning.reflection.completed
reasoning.replan.started
reasoning.branch.created
reasoning.branch.pruned
reasoning.consensus.started
reasoning.consensus.completed
reasoning.context.compacted
reasoning.budget.warning
reasoning.budget.exhausted
reasoning.checkpoint.created
reasoning.completed
reasoning.blocked
reasoning.failed
```

These events should work with the existing Alpha event bus and SSE/run-event machinery.

---

# 27. Integration with Alpha middleware

The exact integration order should be determined from the current lead-agent construction, but the conceptual sequence should be:

```text
Thread Context
 ↓
Security / Trust Boundary
 ↓
Memory
 ↓
Skills / Capability Discovery
 ↓
Reasoning Policy
 ↓
Planning
 ↓
Agent Loop
 ↓
Tool / Sandbox / MCP Execution
 ↓
Observation Normalization
 ↓
Verification
 ↓
Reflection / Replan
 ↓
Completion Gate
```

The important rule is that the reasoning layer should not bypass:

- authorization;
- sandbox rules;
- tool schemas;
- ownership checks;
- cancellation;
- run budgets;
- existing verification.

The Alpha backend guidance explicitly treats authorization, sandbox policy, lifecycle, budget, ownership, and verification as existing runtime contracts.  
Source: [Alpha backend AGENTS.md](https://github.com/itsPremkumar/alpha/blob/main/backend/AGENTS.md).

---

# 28. Integration with memory

Reasoning should produce three different classes of memory.

## Working memory

Short-lived state required for the current task.

```text
active plan
active hypotheses
latest observations
open questions
```

## Episodic reasoning memory

Specific past experiences:

```text
failure → cause → fix → result
```

## Semantic memory

Generalized lessons:

```text
“When using subsystem X, always check condition Y before operation Z.”
```

The memory pipeline should be:

```text
trajectory
 ↓
extract candidate lessons
 ↓
deduplicate
 ↓
verify / score
 ↓
store semantic lesson
```

Deep Agents uses persistent memory together with file-backed context and scoped subagents; Alpha can implement the same principle while keeping its own memory owner contract.  
Sources: [Deep Agents](https://github.com/langchain-ai/deepagents/blob/main/README.md), [Alpha AGENTS.md](https://github.com/itsPremkumar/alpha/blob/main/AGENTS.md).

---

# 29. Integration with subagents

Subagents should be used to reduce context overload and increase diversity.

Recommended pattern:

```text
Lead Reasoner
     │
     ├── Research Agent
     ├── Code Agent
     ├── Critic Agent
     └── Verification Agent
             │
             ▼
        Lead Synthesis
```

Each subagent returns:

```text
result
confidence/uncertainty
claims
supporting evidence
open questions
recommended action
```

It should not dump its entire trajectory back into the parent context.

DeerFlow and Deep Agents both use isolated subagent contexts as a core long-horizon design pattern.  
Sources: [DeerFlow](https://github.com/bytedance/deer-flow/blob/main/frontend/src/content/en/introduction/core-concepts.mdx), [Deep Agents](https://github.com/langchain-ai/deepagents/blob/main/README.md).

---

# 30. Research-mode reasoning

For deep research, Alpha should use:

```text
Question decomposition
→ query generation
→ parallel retrieval
→ source extraction
→ claim normalization
→ contradiction search
→ source-quality filtering
→ evidence graph
→ synthesis
→ citation validation
→ verifier
→ final answer
```

For important claims, store:

```text
claim
source
locator
source date
source type
verification status
contradictions
```

The research process should re-open sources when a claim is load-bearing.

Alpha's existing deep-research subsystem and strict citation contract already point in this direction.  
Source: [Alpha README](https://github.com/itsPremkumar/alpha/blob/main/README.md).

---

# 31. Coding-mode reasoning

For software engineering tasks, the ideal reasoning cycle is:

```text
Understand repository
 ↓
Map relevant files
 ↓
Define acceptance tests
 ↓
Hypothesize root cause / design
 ↓
Inspect exact code
 ↓
Patch
 ↓
Run focused test
 ↓
Analyze result
 ↓
Repair
 ↓
Run broader tests
 ↓
Static checks
 ↓
Review diff
 ↓
Final verification
```

Use deterministic tools aggressively.

For a code task, the compiler/test runner is often a better verifier than another LLM.

Alpha's backend already requires tests with feature changes and has explicit offline/live/blocking-I/O test boundaries. Integrate reasoning tests into this existing TDD contract.  
Source: [Alpha backend AGENTS.md](https://github.com/itsPremkumar/alpha/blob/main/backend/AGENTS.md).

---

# 32. Self-improvement / RSI reasoning

Alpha's RSI capability should never mutate itself directly from a model thought.

Use a gated evolutionary loop:

```text
Observe performance
 ↓
Identify bottleneck
 ↓
Generate candidate improvement
 ↓
Create isolated branch/checkpoint
 ↓
Implement
 ↓
Run benchmark suite
 ↓
Run regression suite
 ↓
Security / integration checks
 ↓
Independent review
 ↓
Promotion gate
```

Promotion should require measurable evidence.

Example:

```text
candidate improvement
    ↓
benchmark
    ├─ task success +4%
    ├─ latency -7%
    ├─ cost +2%
    ├─ regressions 0
    └─ security tests pass
    ↓
PROMOTION DECISION
```

Never use “it feels smarter” as an acceptance criterion.

---

# 33. Loop detection

Reasoning loops are common in autonomous agents.

Track:

```text
same action repeated
same tool arguments repeated
same error repeated
same hypothesis repeated
plan state unchanged
no new evidence
no improvement in verification
```

Trigger escalating interventions:

```text
warning
 ↓
change context
 ↓
ask for discriminating evidence
 ↓
change strategy
 ↓
change model
 ↓
delegate critic
 ↓
terminate / block
```

The current Alpha README already describes loop/thrashing supervision and continuous goal integrity monitoring; make these signals explicit inputs into the reasoning controller.  
Source: [Alpha README](https://github.com/itsPremkumar/alpha/blob/main/README.md).

---

# 34. Contradiction handling

Reasoning should treat contradiction as a first-class event.

Example:

```text
Claim A: API accepts parameter X
Claim B: API rejects parameter X
```

Do not average them together.

Trigger:

```text
contradiction detected
→ classify source quality
→ check dates/versions
→ reproduce experimentally if possible
→ resolve or retain uncertainty
```

For research, prioritize current primary sources.

For code, reproduce against the actual installed version/environment when possible.

---

# 35. Decision ledger

Create a compact durable ledger.

Example:

```json
{
  "decision_id": "dec_018",
  "question": "Which implementation path should be used?",
  "selected": "native-reasoning + explicit verifier",
  "alternatives": [
    "prompt-only CoT",
    "ToT for every request",
    "multi-agent council for every request"
  ],
  "evidence_ids": ["ev_10", "ev_17"],
  "reversibility": "high",
  "decision_confidence": 0.86,
  "timestamp": "2026-09-25T..."
}
```

This provides an auditable explanation of important decisions without exposing private model internals.

---

# 36. Reasoning summary generator

Build `summary.py` that converts structured state into a compact user-facing explanation.

Template:

```text
What Alpha did
1. <step>
2. <step>
3. <step>

Key findings
- <finding>
- <finding>

Evidence
- <source/test/artifact>

Verification
- <check>: PASS
- <check>: PASS

Uncertainty / limitations
- <limitation>

Result
<final result>
```

For models that provide provider-generated reasoning summaries, Alpha can store those summaries as a provider artifact, but should not make its correctness or availability a hard dependency.

---

# 37. Logging / trajectory flight recorder

Alpha has a trajectory/audit direction. The refined implementation should log **events and decision records**, not necessarily raw private thought.

Recommended record:

```text
run_id
step_id
timestamp
agent_id
model_id
provider
strategy
action type
tool name
tool result digest
evidence IDs
verification status
budget remaining
state checksum
```

For debugging, an operator can reconstruct the agent's path from these records without needing the provider's private token-level reasoning.

Keep sensitive provider payloads and credentials out of public logs.

---

# 38. Reasoning telemetry

Track at minimum:

### Quality

```text
task_success_rate
verification_pass_rate
first_attempt_success
final_success
regression_rate
citation_accuracy
evidence_coverage
false_completion_rate
```

### Efficiency

```text
model_calls
tool_calls
subagent_calls
reasoning_tokens if provider exposes usage metadata
wall_time
latency per phase
cost
```

### Cognitive behavior

```text
replans
reflections
branch count
branch-prune rate
contradictions
loop detections
escalations
model switches
```

### Reliability

```text
resume success
checkpoint recovery
provider failover success
cancel success
stuck-run rate
```

---

# 39. Evaluation framework

Reasoning features should be measured rather than judged by demos.

Create:

```text
backend/scripts/benchmark/reasoning/
├── datasets/
├── cases/
├── runner.py
├── graders.py
├── metrics.py
├── policies.yaml
└── README.md
```

### Benchmark categories

```text
simple QA
multi-hop research
tool use
code debugging
code implementation
long-horizon planning
contradiction resolution
memory recall
failure recovery
self-repair
multi-agent synthesis
```

### Compare

```text
baseline agent
vs
native reasoning only
vs
Alpha explicit reasoning
vs
Alpha hybrid reasoning
vs
Alpha hybrid + verification
```

This determines whether each layer actually improves the system.

---

# 40. Recommended phased implementation

## Phase 0 — Contract and inventory

Tasks:

- inspect current lead-agent graph;
- inspect model factory;
- inspect thread state;
- inspect middleware order;
- inspect run/checkpoint lifecycle;
- identify existing planner/evidence/reflection modules;
- identify current tests;
- write `reasoning/AGENTS.md`;
- create schemas and provider capability contract.

Deliverable:

```text
Reasoning architecture document
+ state contract
+ provider capability contract
```

---

## Phase 1 — Core reasoning state

Implement:

```text
models.py
budget.py
policy.py
summary.py
protocols.py
```

Add to the graph:

```text
reasoning_state initialization
```

No complex branching yet.

Goal: every run has explicit reasoning metadata.

---

## Phase 2 — Plan / act / observe loop

Implement:

```text
planner.py
loop.py
action.py
observation.py
```

Connect to existing:

- LangGraph agent loop;
- tools;
- sandbox;
- subagents;
- run manager.

Goal:

```text
reasoning/action/observation cycle
```

---

## Phase 3 — Verification

Implement:

```text
verifier.py
critic.py
evidence.py
```

Connect completion to evidence/verification gates.

Goal:

```text
No premature success.
```

---

## Phase 4 — Reflection and re-planning

Implement:

```text
reflection.py
uncertainty.py
```

Add:

- loop detection;
- failure taxonomy;
- bounded retries;
- strategy change after repeated failure.

Goal:

```text
failure → learning → targeted retry
```

---

## Phase 5 — Native provider reasoning

Enhance model adapters:

```text
OpenAI
Anthropic
local/Ollama
OpenAI-compatible providers
```

Add capability detection and policy routing.

Goal:

```text
native reasoning when available
explicit reasoning when not
```

---

## Phase 6 — Context engineering

Implement:

```text
compression.py
```

Add:

- summaries;
- file-backed scratch state;
- context slices;
- relevant-evidence retrieval;
- old trajectory offloading.

Goal: long-horizon stability.

---

## Phase 7 — Selective branching

Implement:

```text
branch_search.py
```

Start with:

```text
beam width 2
max depth 3
max branches 6
```

Benchmark before increasing limits.

---

## Phase 8 — Multi-agent reasoning

Implement:

```text
consensus.py
```

Use existing subagent infrastructure.

Roles:

```text
builder
researcher
critic
verifier
synthesizer
```

Goal: use diversity only where it improves measurable quality.

---

## Phase 9 — Reasoning memory

Connect reasoning/reflection outputs to Alpha's memory subsystem.

Only persist validated, useful lessons.

Goal:

```text
experience → lesson → future behavior
```

---

## Phase 10 — RSI / self-optimization

Connect reasoning metrics to Alpha's evolution system.

Implement:

```text
benchmark
→ candidate strategy
→ isolated experiment
→ verification
→ promotion / rollback
```

Goal: improve the reasoning controller based on evidence.

---

# 41. Suggested tests

Every component needs tests.

## Unit tests

```text
test_reasoning_state.py
test_reasoning_policy.py
test_reasoning_budget.py
test_hypothesis.py
test_evidence.py
test_verifier.py
test_reflection.py
test_loop_detection.py
test_branch_search.py
test_uncertainty.py
test_summary.py
```

## Integration tests

```text
test_reasoning_agent_loop.py
test_reasoning_tool_interleave.py
test_reasoning_checkpoint_resume.py
test_reasoning_subagents.py
test_reasoning_context_compaction.py
test_reasoning_verification_gate.py
```

## Provider contract tests

```text
test_openai_reasoning_adapter.py
test_anthropic_thinking_adapter.py
test_local_reasoning_fallback.py
```

Use mocked responses for offline tests and explicit live-test gates for real providers.

Alpha's current backend guidelines require TDD and maintain an offline/live test distinction, so reasoning should follow the same model.  
Source: [Alpha backend AGENTS.md](https://github.com/itsPremkumar/alpha/blob/main/backend/AGENTS.md).

---

# 42. Failure-injection tests

Reasoning systems should be tested against failure, not just success.

Inject:

```text
model timeout
tool timeout
invalid tool result
malformed structured output
contradictory evidence
empty search result
repeated identical error
subagent failure
provider outage
checkpoint corruption
context overflow
budget exhaustion
user cancellation
network interruption
```

Expected outcomes must be explicit.

For example:

```text
provider timeout
→ checkpoint state preserved
→ retry
→ fallback model
→ continue
```

not:

```text
provider timeout
→ start over silently
```

---

# 43. Security and trust boundaries

Reasoning must never bypass Alpha's existing security plane.

The following must remain authoritative:

```text
authorization
sandbox
credential scope
tool allowlist
approval gates
cancellation
resource limits
run ownership
```

A model-generated reasoning instruction must be treated as **untrusted content** until policy validation passes.

Example:

```text
Model says:
“Run this shell command with elevated permissions.”

Alpha policy:
→ inspect command
→ evaluate risk
→ require approval if necessary
→ execute in permitted sandbox
```

Do not let “the reasoning engine” become a hidden privilege escalation path.

---

# 44. Prompt design for Alpha reasoning

## For reasoning-capable models

Use concise, outcome-oriented prompts.

Example:

```text
You are the Alpha lead agent.

Objective:
<objective>

Success criteria:
<criteria>

Constraints:
<constraints>

Current verified facts:
<facts>

Current plan:
<plan>

Open questions:
<questions>

Available tools:
<tools>

Perform the next action that most efficiently advances the objective.
Use available tools when evidence is required.
Do not claim completion without satisfying the acceptance criteria.
```

This follows the current OpenAI recommendation to keep reasoning-model prompts direct and specific instead of forcing a visible step-by-step monologue.  
Source: [OpenAI Reasoning Best Practices](https://developers.openai.com/api/docs/guides/reasoning-best-practices).

## For non-reasoning models

Use a structured action contract:

```text
Return JSON:
{
  "action": "...",
  "purpose": "...",
  "expected_observation": "...",
  "success_condition": "..."
}
```

Alpha can then execute the action and feed back the observation.

---

# 45. Reasoning prompt/compiler layer

Alpha already has metacompiler/prompt-oriented architecture concepts in its repository description.

Implement a prompt assembly pipeline:

```text
mission
+ policy
+ relevant memory
+ skills
+ current plan
+ evidence slice
+ tool contract
+ output schema
+ safety rules
→ model request
```

Do not put all historical context into every request.

Prefer a **context compiler** that selects only the information relevant to the current reasoning step.

---

# 46. Reasoning profiles

Expose configurable profiles in `config.yaml`:

```yaml
reasoning:
  enabled: true
  default_profile: adaptive

  profiles:
    fast:
      mode: native_or_explicit
      max_iterations: 4
      max_tool_calls: 8
      max_branches: 0
      verification: lightweight

    balanced:
      mode: hybrid
      max_iterations: 12
      max_tool_calls: 30
      max_branches: 2
      verification: standard

    deep:
      mode: hybrid
      max_iterations: 30
      max_tool_calls: 80
      max_branches: 4
      verification: strong

    research:
      mode: research
      max_iterations: 40
      max_tool_calls: 120
      max_branches: 2
      evidence_required: true
      contradiction_search: true

    coding:
      mode: code
      max_iterations: 40
      max_tool_calls: 100
      verification: tests
      regression_gate: true

    self_improve:
      mode: self_improvement
      max_iterations: 60
      max_tool_calls: 150
      verification: benchmark
      rollback_required: true
```

---

# 47. Slash commands

Useful Alpha commands:

```text
/reason
/plan
/think
/verify
/review
/reflect
/research
/branch
/council
/evidence
/trace
```

However, normal users should not need to invoke them for common work.

The one-prompt autonomous planner should automatically select the correct strategy.

---

# 48. API endpoints

Add a small reasoning API surface.

Suggested:

```text
GET  /api/runs/{run_id}/reasoning
GET  /api/runs/{run_id}/reasoning/summary
GET  /api/runs/{run_id}/reasoning/plan
GET  /api/runs/{run_id}/reasoning/evidence
GET  /api/runs/{run_id}/reasoning/verification
GET  /api/runs/{run_id}/reasoning/decisions
POST /api/runs/{run_id}/reasoning/replan
POST /api/runs/{run_id}/reasoning/stop
```

Do not expose raw hidden provider reasoning by default.

---

# 49. Database / persistence design

For SQLite/PostgreSQL:

```text
reasoning_runs
reasoning_steps
reasoning_hypotheses
reasoning_observations
reasoning_evidence
reasoning_verifications
reasoning_reflections
reasoning_decisions
reasoning_branches
reasoning_summaries
```

Every row should reference:

```text
run_id
thread_id
agent_id
created_at
updated_at
```

Large payloads should be stored as artifacts/files with digests rather than duplicating them into every checkpoint row.

---

# 50. Checkpointing

Reasoning must survive:

- process restart;
- laptop restart;
- provider timeout;
- network outage;
- scheduled task suspension;
- agent cancellation and resume.

Checkpoint after every meaningful state transition:

```text
plan created
important tool result
verification result
replan
reflection
branch completion
milestone
```

The checkpoint should store **control state**, not unnecessary raw context.

---

# 51. Recommended first implementation

Do not implement everything at once.

The first usable Alpha reasoning slice should be:

```text
1. ReasoningState
2. ReasoningPolicy
3. ReasoningBudget
4. Plan/Act/Observe loop
5. Evidence records
6. Independent verification
7. Reflection + bounded retry
8. Reasoning summary
9. Checkpoint integration
10. Tests
```

Only after this is stable add:

```text
ToT
MoA
advanced uncertainty
reasoning memory
RSI optimization
```

This prevents the core runtime from becoming an oversized experimental graph.

---

# 52. Recommended Alpha reasoning graph

Conceptual LangGraph topology:

```text
                    ┌──────────────────┐
                    │ Mission Intake   │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ Policy Selector  │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ Planner          │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ Next Step        │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ Action / Tool    │
                    └────────┬─────────┘
                             ↓
                    ┌──────────────────┐
                    │ Observation      │
                    └────────┬─────────┘
                             ↓
                 ┌─────────────────────────┐
                 │ Evidence + Hypotheses   │
                 └───────────┬─────────────┘
                             ↓
                    ┌──────────────────┐
                    │ Verification    │
                    └────────┬─────────┘
                             ↓
                 ┌─────────────────────────┐
                 │ Completion / Reflection │
                 └─────┬─────────┬─────────┘
                       │         │
                  complete     retry/replan
                       │         │
                       ↓         └──────────→ Planner
                 ┌──────────────┐
                 │ Final Gate   │
                 └──────┬───────┘
                        ↓
                Answer + Artifacts
```

Optional branch/council nodes should be invoked only by policy.

---

# 53. Example: Alpha debugging a bug

User:

```text
Fix the login bug in the repository.
```

Alpha should internally do:

```text
1. Parse objective.
2. Detect coding task.
3. Inspect project guidance.
4. Identify likely authentication modules.
5. Establish acceptance criteria.
6. Form hypotheses.
7. Inspect relevant code.
8. Run a focused reproducer/test.
9. Update hypotheses using actual failure evidence.
10. Patch the smallest correct boundary.
11. Run focused test.
12. If failed, reflect and revise.
13. Run related regression tests.
14. Run lint/type checks where relevant.
15. Inspect git diff.
16. Verify acceptance criteria.
17. Produce summary.
```

User-facing summary:

```text
I traced the failure to the session validation path, reproduced it with the focused test, patched the validation condition, and reran the affected regression tests. The requested behavior now passes the verification checks.
```

That is reasoning transparency without exposing a raw hidden monologue.

---

# 54. Example: Alpha research task

User:

```text
Research open-source agent memory systems and choose an architecture for Alpha.
```

Reasoning flow:

```text
Decompose research questions
→ gather primary sources
→ identify candidate systems
→ extract architecture claims
→ compare memory types
→ run contradiction searches
→ map requirements to candidates
→ build evidence matrix
→ draft architecture
→ critic review
→ verify load-bearing claims
→ final recommendation-free technical comparison
```

For political or other sensitive decision domains, the same system can provide sourced comparisons and evidence while leaving the actual decision to the user.

---

# 55. What Alpha should NOT implement

Avoid these designs:

### A. “Always print the entire chain-of-thought”

Bad because it creates huge outputs, leaks internal process, and is unnecessary for modern reasoning APIs.

### B. “Always use Tree of Thoughts”

Bad because most tasks do not need branching.

### C. “Always use a multi-agent council”

Bad because it increases cost and latency without guaranteeing better results.

### D. “Store every thought in long-term memory”

Bad because memory becomes noisy, expensive, hard to retrieve, and difficult to govern.

### E. “Self-verification by the same model is enough”

Bad for important tasks. Prefer independent executable or cross-model evidence.

### F. “More reasoning tokens always means better”

False in practice; provider documentation discusses diminishing returns, latency, and resource limits.

### G. “Reasoning can bypass authorization”

Never.

### H. “Retry forever until the model succeeds”

Never. Use bounded budgets and explicit stop conditions.

---

# 56. Implementation priority matrix

| Capability | Priority | Reason |
|---|---:|---|
| Structured reasoning state | P0 | Foundation |
| Policy/router | P0 | Prevents waste |
| Plan/act/observe loop | P0 | Core agent reasoning |
| Verification | P0 | Prevents false completion |
| Reflection | P0 | Recovery |
| Budgeting | P0 | Reliability/cost |
| Checkpointing | P0 | Long-running tasks |
| Reasoning summaries | P0 | Transparency |
| Evidence graph | P1 | Research + auditability |
| Context compaction | P1 | Long horizon |
| Native provider adapters | P1 | Strong model reasoning |
| Loop detection | P1 | Reliability |
| Subagent reasoning | P1 | Parallelism/diversity |
| ToT branching | P2 | Hard search problems |
| MoA/council | P2 | Complex consensus |
| Advanced uncertainty | P2 | Adaptive routing |
| Reasoning memory | P2 | Learning |
| RSI optimization | P3 | Long-term evolution |

---

# 57. Definition of done

The Alpha reasoning subsystem should not be considered complete because it “looks intelligent”. It is complete only when these conditions are demonstrably true:

```text
[ ] Reasoning policy selects an appropriate strategy.
[ ] Native reasoning models are used without forced visible CoT prompts.
[ ] Non-reasoning models have a structured reasoning fallback.
[ ] Tool use is integrated with reasoning/action/observation cycles.
[ ] State is checkpointed and resumable.
[ ] Important outputs are independently verified.
[ ] Failures trigger bounded reflection/replanning.
[ ] Loop detection can stop pathological behavior.
[ ] Context is compacted without losing control-critical state.
[ ] User-facing reasoning is represented as summaries/decisions/evidence.
[ ] Raw private reasoning is not required for system correctness.
[ ] Model providers can be replaced.
[ ] Tests cover normal and failure paths.
[ ] Benchmarks compare baseline vs reasoning-enabled modes.
[ ] Cost and latency are measured.
[ ] Self-improvement changes require evidence and rollback.
```

---

# 58. Suggested repository change list

```text
backend/packages/harness/alpha/
└── reasoning/
    ├── AGENTS.md
    ├── __init__.py
    ├── models.py
    ├── policy.py
    ├── budget.py
    ├── router.py
    ├── planner.py
    ├── hypothesis.py
    ├── loop.py
    ├── action.py
    ├── observation.py
    ├── evidence.py
    ├── verifier.py
    ├── critic.py
    ├── reflection.py
    ├── branch_search.py
    ├── consensus.py
    ├── uncertainty.py
    ├── compression.py
    ├── summary.py
    ├── provenance.py
    ├── metrics.py
    ├── limits.py
    └── protocols.py

backend/tests/
├── test_reasoning_state.py
├── test_reasoning_policy.py
├── test_reasoning_budget.py
├── test_reasoning_loop.py
├── test_reasoning_verification.py
├── test_reasoning_reflection.py
├── test_reasoning_checkpoint.py
├── test_reasoning_context.py
├── test_reasoning_router.py
└── test_reasoning_provider_contracts.py

docs/
├── REASONING_ARCHITECTURE.md
├── REASONING_PROVIDER_MATRIX.md
└── REASONING_BENCHMARKS.md
```

Adjust paths to the exact current tree after inspecting the current lead-agent and middleware implementation; do not create duplicate orchestration systems if an existing Alpha component already owns that responsibility.

---

# 59. Recommended implementation order for a coding agent

Use a living plan/ExecPlan approach for the actual code change:

```text
1. Read root AGENTS.md.
2. Read backend AGENTS.md.
3. Read the lead-agent construction.
4. Read thread state.
5. Read model factory.
6. Read current planning/evidence/reflection code.
7. Map integration points.
8. Write tests first.
9. Implement P0 reasoning core.
10. Run offline tests.
11. Run blocking-I/O tests.
12. Run focused integration tests.
13. Run live provider tests only when enabled.
14. Inspect event stream/checkpoint behavior.
15. Update README/AGENTS/docs.
16. Benchmark baseline vs reasoning-enabled path.
17. Add P1 features only after P0 is stable.
```

Alpha's own repository guidance requires synchronized documentation and TDD; its architecture explicitly separates the publishable harness from the application layer. Keep the new reasoning subsystem inside the harness and keep it independent of `app.*`.  
Source: [Alpha backend AGENTS.md](https://github.com/itsPremkumar/alpha/blob/main/backend/AGENTS.md).

---

# 60. Final architecture recommendation

The target Alpha cognitive stack should become:

```text
                         ┌──────────────────────────┐
                         │         USER             │
                         └────────────┬─────────────┘
                                      ↓
                         ┌──────────────────────────┐
                         │ Mission / Goal Engine    │
                         └────────────┬─────────────┘
                                      ↓
                         ┌──────────────────────────┐
                         │ Reasoning Policy Router  │
                         └────────────┬─────────────┘
                                      ↓
               ┌─────────────────────────────────────────────┐
               │             REASONING PLANE                 │
               │                                             │
               │ Native Thinking / Explicit Planning        │
               │ Hypotheses / Evidence / Uncertainty        │
               │ ReAct / Tool Observation                    │
               │ Verification / Critic                       │
               │ Reflection / Replan                         │
               │ Optional ToT / MoA / Subagents              │
               └───────────────┬─────────────────────────────┘
                               ↓
               ┌─────────────────────────────────────────────┐
               │              EXECUTION PLANE                 │
               │ Tools / MCP / Browser / Shell / Sandbox    │
               │ Files / Git / APIs / Computer Use          │
               └───────────────┬─────────────────────────────┘
                               ↓
               ┌─────────────────────────────────────────────┐
               │              VERIFICATION PLANE              │
               │ Tests / Runtime / Evidence / Critics       │
               └───────────────┬─────────────────────────────┘
                               ↓
               ┌─────────────────────────────────────────────┐
               │              MEMORY PLANE                    │
               │ Working / Episodic / Semantic / Graph      │
               └───────────────┬─────────────────────────────┘
                               ↓
               ┌─────────────────────────────────────────────┐
               │               EVOLUTION PLANE                │
               │ Evaluation / Reflection / RSI / Benchmarks │
               └─────────────────────────────────────────────┘
```

The core principle is:

> **Alpha should not try to imitate a human's private chain-of-thought. Alpha should build a reliable reasoning runtime around the model: plan, act, observe, verify, reflect, remember, and stop based on evidence.**

That architecture is compatible with native reasoning models, ordinary tool-calling models, local models, multi-model councils, long-running autonomous execution, and Alpha's existing LangGraph/harness design.

---

# 61. Primary references

1. **Alpha repository** — https://github.com/itsPremkumar/alpha
2. **Alpha root AGENTS.md** — https://github.com/itsPremkumar/alpha/blob/main/AGENTS.md
3. **Alpha backend AGENTS.md** — https://github.com/itsPremkumar/alpha/blob/main/backend/AGENTS.md
4. **OpenAI — Reasoning best practices** — https://developers.openai.com/api/docs/guides/reasoning-best-practices
5. **OpenAI — Reasoning models** — https://developers.openai.com/api/docs/guides/reasoning
6. **Anthropic — Extended thinking** — https://platform.claude.com/docs/en/build-with-claude/extended-thinking
7. **DeepSeek-R1** — https://arxiv.org/abs/2501.12948
8. **ReAct** — https://arxiv.org/abs/2210.03629
9. **Tree of Thoughts** — https://arxiv.org/abs/2305.10601
10. **Reflexion** — https://arxiv.org/abs/2303.11366
11. **Self-Refine** — https://arxiv.org/abs/2303.17651
12. **Let's Verify Step by Step** — https://arxiv.org/abs/2305.20050
13. **Deep Agents README** — https://github.com/langchain-ai/deepagents/blob/main/README.md
14. **Deep Agents architecture** — https://github.com/langchain-ai/deepagents/blob/main/libs/ARCHITECTURE.md
15. **Deep Agents overview** — https://github.com/langchain-ai/docs/blob/main/src/oss/deepagents/overview.mdx
16. **DeerFlow core concepts** — https://github.com/bytedance/deer-flow/blob/main/frontend/src/content/en/introduction/core-concepts.mdx
17. **DeerFlow introduction** — https://github.com/bytedance/deer-flow/blob/main/frontend/src/content/en/introduction/index.mdx
18. **OpenAI Cookbook — PLANS.md / ExecPlans** — https://github.com/openai/openai-cookbook/blob/main/articles/codex_exec_plans.md
19. **AGENTS.md open format** — https://github.com/agentsmd/agents.md

---

# 62. Immediate next coding milestone

Implement this exact slice first:

```text
ReasoningState
      +
ReasoningPolicy
      +
ReasoningBudget
      +
Plan/Act/Observe loop
      +
Evidence
      +
Independent Verification
      +
Reflection
      +
Checkpoint
      +
Reasoning Summary
      +
P0 tests
```

Then benchmark it against Alpha's current agent loop before adding ToT, MoA, advanced councils, or RSI reasoning optimization.

