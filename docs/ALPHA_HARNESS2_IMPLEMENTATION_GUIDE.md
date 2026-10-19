# Alpha AI Agent — Harness²-Inspired Recursive Harness Improvement

**Document type:** Research and implementation plan  
**Prepared:** 18 October 2026  
**Target:** Alpha, a local-first AI-agent runtime with specialist agents, subagents/swarm execution, tools, skills, memory, task/goal orchestration, provider abstraction, and recursive self-improvement (RSI).  
**Implementation posture:** Rust-first orchestration and policy enforcement; retain the existing model/provider and executor abstractions. Use another language only where an existing integration makes it worthwhile—not as a requirement to port Harness² wholesale.

> **Core recommendation:** Build a bounded, evidence-driven *Harness Improvement subsystem* inside Alpha. Start with one baseline run, two candidate edits (general and task/domain-specific), isolated probes, a conservative composer, deterministic verification, and a versioned experience library. Do not let an LLM edit the active production harness directly. Treat every change as a candidate until it passes policy, validation, and promotion gates.

---

## 1. Executive summary

Google Research's **Harness²: Recursive Agent Harnessing for an Open World** proposes a way for an agent to improve the runtime scaffolding around a fixed model. Instead of fine-tuning model weights, it edits the agent's harness—prompts, guardrails, memory, tool guidance, skills, scripts, subagents, and middleware—and examines what changes in actual execution.

The design links two loops:

1. **Task-level recursion:** Improve the harness for the current task through **Propose → Probe → Compose**. Run a baseline; propose candidate edits; execute candidates on the same task under matched conditions; compare behavior and outputs; compose a successor using changes supported by evidence.
2. **Harnessing-level recursion:** After finishing the task, review the improvement record and retain only reusable, evidence-supported lessons for future tasks. Store the adapted harness and an `EXPERIENCE.md`-style explanation of where it applies, what it changed, and what the executions showed.

This is relevant to Alpha because Alpha already has the ingredients that can be improved: tool use, persistent memory, skills, agent definitions, subagents, goal/task execution, model/provider selection, and RSI. Harness² contributes a **disciplined experimental process** around changing those ingredients rather than merely adding more memory or giving the agent a stronger “self-improve” prompt.

### Recommended implementation decision

| Decision | Recommendation for Alpha |
|---|---|
| Adopt the whole repository as Alpha's runtime? | **No.** Treat Harness² primarily as a research pattern and reference implementation. Its release is Python 3.12-oriented and benchmark/executor-focused; Alpha should preserve its Rust-first core and existing runtime. |
| Implement the core idea? | **Yes.** Build a versioned, pluggable harness registry and a recursively improving workflow. |
| First recursion mode | **Sequential, `k = 1`** for the initial implementation. It is easier to debug, less expensive, and easier to audit. |
| Candidate scope | One **invariant/general** candidate and one **task/domain-specific** candidate; probe both in isolated workspaces. |
| Promotion | Draft → validation → shadow/canary → promoted. Never overwrite the active harness during a probe. |
| Memory | Keep normal agent memory separate from the **harness-improvement experience library**. Store evidence and scope with every lesson. |
| Scoring | Deterministic validators first; structured trace comparisons second; model-based judging only as a fallible signal. |
| Parallel swarm | Reuse Alpha's existing scheduler. Add parallel candidate exploration after the sequential pathway is stable and budgeted. |
| Security | Enforce boundaries outside model prompts: immutable guardrails, permission checks, capability-limited tools, isolated scripts/plugins, secret redaction, and audit logs. |

---

## 2. Research reviewed and what the project actually does

### Primary sources

- **Repository:** <https://github.com/google-research/harness2>
- **Paper (HTML):** <https://arxiv.org/html/2610.14224>
- **Paper abstract/PDF entry:** <https://arxiv.org/abs/2610.14224>
- **Experiment guide:** <https://github.com/google-research/harness2/blob/main/docs/EXPERIMENTS.md>
- **Package/project metadata:** <https://github.com/google-research/harness2/blob/main/pyproject.toml>
- **Third-party dependency and license notes:** <https://github.com/google-research/harness2/blob/main/dependencies/README.md>

The paper is dated **9 October 2026** (arXiv:2610.14224, v1). The README describes Harness² as a plug-and-play framework for recursive harnessing in an open world. The research evaluation covers WorkBuddy-Bench, JobBench, and Harvey's Legal Agent Benchmark (LAB), with OpenCode/Codex executor configurations and several models.

The authors report a single Harness² step improved the evaluated GLM-5 and Gemini 3.5 Flash configurations over their base harnesses, with maximum reported gains of up to **11.5 points on WorkBuddy-Bench, 4.5 points on JobBench, and 11.8 percentage points in LAB partial-pass rate**. These are benchmark-specific paper results—not promises that Alpha will gain the same amount. The paper also reports the gains depend on executor, domain, and configuration.

### What Harness² is—and is not

**It is:**
- A method for evolving the *execution harness*, meaning the scaffolding around a model.
- A task-local experiment loop that looks for behavioral differences caused by harness edits.
- A second loop that learns how to conduct future harness improvements.
- A file/component-based method that makes edits inspectable as diffs.
- A framework with parallel and sequential recursion, result artifacts, resumption, and benchmark evaluation.

**It is not:**
- Model-weight training or fine-tuning.
- A guarantee that the model's answer is correct.
- A reason to accept every self-generated edit.
- A general-purpose production-ready Rust agent framework.
- Proof that one task-specific edit will transfer to unrelated tasks.
- A substitute for test environments, permission boundaries, or regression evaluation.

### Source-specific implementation observations

1. **Eight editable component types:** system prompt, guardrails, memory, tool guidance, skills, subagents, scripts, and middleware plugins.
2. **File-level editing:** Components are represented in an editable file/component tree, making an edit reviewable and comparable to a previous version.
3. **Matched probes:** Candidate harnesses execute on the same task, keeping the model and environment configuration as similar as practical.
4. **Selective composition:** The composer can merge changes, revise conflicts, adopt one candidate, or retain the baseline. More edits are not inherently better.
5. **Two recursion modes:** Parallel recursion explores a wider candidate pool; sequential recursion carries a revised harness and accumulated evidence into later steps.
6. **Experience library:** Adopted harnesses and evidence-supported lessons are indexed for reuse. The library is updated after the task, not during that task's candidate exploration.
7. **Bounded runs and reproducibility:** Budget, task selection, executor, seed, and configuration matter. The experiment guide warns about API cost and says the same settings should be retained when comparing runs.
8. **Security caveat:** The published setup notes unrestricted executor network access and warns that public benchmark rubrics may be reachable. Alpha should make sandboxing and network policy stricter by default.
9. **License boundary:** The repository states Harness² itself is Apache-2.0, while third-party tools, benchmark datasets, and their licenses may differ. Do not assume every benchmark/data asset is Apache-2.0.
10. **Research limits:** The paper explicitly says that execution contrasts and model judgments are noisy, cost extra inference, and do not prove correctness or transferability. It identifies library consolidation and adaptive recursion as future work.

---

## 3. The central concept: two coupled feedback loops

### 3.1 Loop A — improve the current task's execution harness

```text
Task + active harness snapshot
            |
            v
      Baseline execution ----------------------+
            |                                  |
            v                                  |
    Diagnose first failure                     |
            |                                  |
            +--> General/invariant proposal    |
            +--> Task/domain proposal          |
                         |                      |
                         v                      |
                 Validate candidate patches    |
                         |                      |
                         v                      |
                Isolated candidate probes      |
                         |                      |
                         v                      |
          Compare trajectories, artifacts, and checks
                         |
                         v
                 Compose a successor
                         |
                         v
             Execute + verify successor
                         |
                         v
         Select deliverable / retain baseline if safer
                         |
                         v
                     Return result
```

The baseline tells Alpha how it behaved, not whether the deliverable is correct. The probe asks: *did this specific harness edit change the failure mode we targeted, and did it create a regression?* The composer should connect every proposed edit to an observed event and an observed consequence.

**Important:** A candidate's success on one run is evidence, not proof. Execution can be stochastic; the model may share the same blind spots when it proposes an edit and when it judges the result. Alpha must expose this uncertainty in run records and avoid promoting changes based only on a persuasive explanation.

### 3.2 Loop B — improve the process that improves harnesses

```text
Completed task improvement record
  (baseline, candidate diffs, probe traces, compose decisions, checks)
                         |
                         v
                Reflect on evidence
                         |
                         v
   Extract: failure pattern, useful edit, scope, counter-evidence
                         |
                         v
       Validate reusable lesson and component bundle
                         |
                         v
           Versioned experience library
                         |
                         v
       Retrieve relevant precedents for future runs
```

The most important distinction: **do not copy the entire last task's harness into the next task by default.** Carry forward only components and lessons that have a defensible scope. A fix for a Python packaging task may be useful for future coding tasks; a discovered fact about one repository should not become a universal rule for every future task.

### 3.3 Why Alpha should keep these loops separate

| Concern | Task-level loop | Harnessing-level loop |
|---|---|---|
| Scope | One goal/task/run | Many future tasks |
| Input | Current task, baseline trace, active harness, retrieved experience | Complete improvement record and promotion outcomes |
| Output | Candidate successor and final task deliverable | Reusable lessons and approved reference harness components |
| Lifetime | Short-lived, budgeted | Persistent and indexed |
| Safety rule | Probe in isolation; do not mutate active config | Do not store/promote lessons without evidence and scope |
| Main failure mode | Overfitting one task or choosing a noisy winner | Polluting long-term behavior with false/generalized lessons |

---

## 4. Map Harness² onto Alpha's existing architecture

Alpha should be the execution system; a new **Harness Improvement Service** should be a contained subsystem that uses Alpha's existing model gateway, tool registry, scheduler, memory, tracing, and UI. Do not build a second agent runtime inside the RSI feature.

| Harness² concept | Alpha implementation target | Notes |
|---|---|---|
| Frozen base model | Existing universal provider/model adapter | Freeze the provider/model and generation settings during paired probes where possible. If a provider lacks deterministic sampling, log the limitation. |
| Execution harness | Immutable `HarnessBundle` snapshot | Prompt files, tool guidance, skill manifests, agent/subagent definitions, scripts, and eligible middleware configuration. |
| System prompt | Versioned agent base prompt | Keep generic operating instructions concise. |
| Guardrails | Policy/permission configuration | The non-bypassable safety core must be outside the editable candidate surface. Editable guidance can supplement—but never weaken—enforced policy. |
| Memory | Existing durable agent memory, plus task/domain memory | Candidate edits to memory should be versioned; retrieved private facts must be redacted from cross-task traces when needed. |
| Tool guidance | Per-tool or per-capability instruction documents | Prefer tool-level guidance and schema validation to a giant always-loaded prompt. |
| Skills | On-demand versioned `SKILL.md` workflows | Record trigger conditions and bundled helper dependencies. Test discovery and loading, not just file syntax. |
| Subagents | Versioned specialist agent definitions | Record role, input/output contract, model, permissions, and task boundaries. |
| Scripts | Deterministic helpers run by Alpha's sandbox runner | Candidate-generated scripts must not run with unrestricted host privileges. |
| Middleware plugins | Hook/interceptor layer around tool calls and task completion | Powerful, always-active hooks need stricter review and must not create deadlocks or block valid work. |
| Executor | Existing Alpha agent loop and tool executor | Use the existing production path so improvement results reflect real Alpha behavior. |
| Improvement editor/composer/reflector | Specialized internal roles or controlled LLM calls | They should receive structured evidence via files/records, not a giant unsorted prompt dump. |
| Harness improvement library | New indexed collection in Alpha's durable store | Separate from episodic memory, semantic memory, and raw chat history. |
| Runs/results | Existing trace store + immutable artifacts | Store configs, hashes, diffs, traces, selected result, checks, token/cost/latency, and decision rationale. |
| Judge/benchmark | Local validators + task-specific evaluators + optional blind model comparison | Never expose hidden evaluation answers to the execution agent. |

### Alpha-specific design principle

**Improve the harness, not everything at once.** Keep the model/provider adapter, task planner, memory system, tool execution, swarm scheduler, safety policy, and UI independently versioned. A task should be able to change a skill or tool-guidance document without simultaneously modifying the runtime scheduler or trusted permission engine.

---

## 5. Proposed component model for Alpha

Represent an agent harness as a versioned manifest pointing to independently versioned components. Store actual content in immutable files/content-addressed blobs and relational metadata in Alpha's existing database. Avoid embedding every component as one huge JSON prompt.

### 5.1 Suggested workspace layout

Names are illustrative; adapt to Alpha's current repository conventions rather than creating duplicate directories if equivalent systems already exist.

```text
alpha/
  crates/
    harness-core/                 # types, versioning, diffs, policy checks
    harness-runner/               # baseline/probe execution orchestration
    harness-evaluator/             # artifact validators and trace comparisons
    harness-library/               # retrieval, experience entries, promotion
    harness-api/                   # APIs/events used by UI
  harnesses/
    active/                        # immutable pointer/manifest to active version
    versions/<bundle-id>/
      manifest.json
      system_prompt.md
      guardrails_guidance.md       # additional guidance only; not trusted policy
      tool_guidance/
      skills/<skill-id>/SKILL.md
      subagents/<agent-id>/AGENT.md
      scripts/
      middleware/
  harness-library/
    index.json                     # optional rebuildable index
    experiences/<experience-id>/
      EXPERIENCE.md
      evidence.json
      manifest.json
  runs/
    <run-id>/
      run.json
      baseline/
      candidates/
      probes/
      composition/
      evaluation/
      report.md
```

Store generated or user-provided artifacts separately from executable code. Treat an active harness pointer as a transactional reference to an immutable version, not as mutable files that probe workers edit in place.

### 5.2 Harness manifest — conceptual schema

```json
{
  "schema_version": 1,
  "bundle_id": "sha256:...",
  "parent_bundle_id": "sha256:...",
  "created_at": "2026-10-18T00:00:00Z",
  "created_by": "harness_composer",
  "agent_profile": "general|coding|research|browser|...",
  "components": {
    "system_prompt": {"version": "...", "sha256": "..."},
    "guardrails_guidance": {"version": "...", "sha256": "..."},
    "memory_refs": [],
    "tool_guidance": [],
    "skills": [],
    "subagents": [],
    "scripts": [],
    "middleware": []
  },
  "compatibility": {
    "alpha_runtime_version": "...",
    "tool_schema_version": "...",
    "policy_version": "...",
    "os": ["windows", "linux"]
  },
  "provenance": {
    "source_run_id": "...",
    "source_task_type": "coding",
    "promotion_state": "draft"
  }
}
```

Validate the schema strictly and reject unknown or forbidden component types. A model may propose data that conforms to a schema, but it must not dictate the enforcement rules for that schema.

### 5.3 Not every component has equal risk or cost

Harness²'s component design distinguishes components by activation and runtime impact. Alpha should do the same:

- **Always-loaded components:** base system prompt, enforced policy summary, small universal memory, tool-selection guidance. Every token costs context on every call.
- **On-demand components:** skills and specialist workflows. Load only when trigger conditions match and the skill is needed.
- **Separate execution contexts:** subagents. Use when the subtask has a large input and a compact answer, and enforce a narrow input/output contract.
- **Deterministic executable capabilities:** scripts. Use for repeatable extraction, conversion, validation, or arithmetic rather than asking the LLM to reason through every mechanical step.
- **Runtime hooks:** middleware/plugin hooks. Use only when an invariant must be enforced inside the tool loop; treat code hooks as high-risk changes.

**Recommended edit-cost order:** first fix incorrect tool use with tool guidance; use an on-demand skill for a triggered multi-step workflow; add a deterministic script for repeated mechanical work; add a subagent for a well-bounded subproblem; change always-on prompt text only when the behavior is genuinely universal; add middleware only when a repeated failure shows prompt instructions are not sufficient and a hard runtime check is necessary.

Do not put task-specific discovered facts in permanent universal memory. Store scoped facts in task/repository/domain memory with provenance and expiry or invalidation rules.

---

## 6. Rust-first internal API design

The snippets below are **design sketches**, not drop-in compiled code. They show the boundaries to implement. Use Alpha's existing crates and async runtime if they already fill these roles.

### 6.1 Core data types

```rust
// Illustrative Rust types. Keep IDs immutable and serialize records to JSON/SQLite.

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub enum CandidateScope {
    Invariant,       // reusable runtime/process behavior
    TaskOrDomain,    // this task, repo, tool, or professional domain
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub enum HarnessComponentKind {
    SystemPrompt,
    GuardrailGuidance,
    MemoryReference,
    ToolGuidance,
    Skill,
    Subagent,
    Script,
    Middleware,
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct HarnessBundle {
    pub bundle_id: String,          // content hash / immutable identifier
    pub parent_bundle_id: Option<String>,
    pub manifest_path: String,
    pub component_hashes: Vec<String>,
    pub policy_version: String,     // cannot be weakened by a candidate
    pub runtime_version: String,
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct FailureHypothesis {
    pub summary: String,
    pub trace_event_ids: Vec<String>,
    pub expected_behavioral_change: String,
    pub falsification_condition: String,
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct HarnessProposal {
    pub proposal_id: String,
    pub scope: CandidateScope,
    pub base_bundle_id: String,
    pub hypothesis: FailureHypothesis,
    pub component_diffs: Vec<ComponentDiff>,
    pub expected_regressions: Vec<String>,
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct ProbeOutcome {
    pub run_id: String,
    pub bundle_id: String,
    pub task_fingerprint: String,
    pub trace_artifact: String,
    pub deliverable_refs: Vec<String>,
    pub validator_results: Vec<ValidatorResult>,
    pub token_usage: Option<TokenUsage>,
    pub elapsed_ms: u64,
    pub tool_error_count: u32,
}
```

`ComponentDiff`, `ValidatorResult`, and `TokenUsage` should be defined around existing Alpha types instead of duplicated. Keep task content and user data out of `task_fingerprint`; use a salted hash or stable non-sensitive identifier as appropriate.

### 6.2 Traits / interfaces to keep modules swappable

```rust
#[async_trait::async_trait]
pub trait HarnessExecutor: Send + Sync {
    async fn execute(
        &self,
        task: &TaskSpec,
        harness: &HarnessBundle,
        environment: &ProbeEnvironment,
    ) -> anyhow::Result<ProbeOutcome>;
}

#[async_trait::async_trait]
pub trait HarnessEditor: Send + Sync {
    async fn propose(
        &self,
        context: &ImprovementContext,
        scope: CandidateScope,
    ) -> anyhow::Result<HarnessProposal>;
}

#[async_trait::async_trait]
pub trait HarnessComposer: Send + Sync {
    async fn compose(
        &self,
        context: &ImprovementContext,
        contrasts: &[ContrastReport],
    ) -> anyhow::Result<CompositionDecision>;
}

#[async_trait::async_trait]
pub trait HarnessEvaluator: Send + Sync {
    async fn evaluate(
        &self,
        task: &TaskSpec,
        outcome: &ProbeOutcome,
    ) -> anyhow::Result<EvaluationReport>;
}
```

These interfaces let Alpha use different LLM providers/editors without coupling the recursive improvement logic to one model API. Editor output is untrusted input: parse it, validate each diff, check allowed paths, then apply it in a temporary workspace. Never give the editor raw access to the active harness directory.

### 6.3 Improvement run state machine

Persist each transition so Alpha can restart or resume safely:

```text
QUEUED
  -> SNAPSHOTTING
  -> BASELINE_RUNNING
  -> BASELINE_RECORDED
  -> PROPOSING
  -> CANDIDATES_VALIDATING
  -> PROBING
  -> CONTRASTING
  -> COMPOSING
  -> SUCCESSOR_RUNNING
  -> VERIFYING
  -> DELIVERABLE_SELECTED
  -> REFLECTING
  -> COMPLETED
```

Any step can transition to `BLOCKED`, `FAILED`, `CANCELLED`, or `BUDGET_EXHAUSTED`. Those are durable outcomes, not missing rows. Record why the transition occurred and what can be resumed.

**Idempotency:** every stage should have a stage key derived from run ID, stage name, relevant input hashes, and config hash. A retry must not double-spend a provider request or overwrite an existing artifact unnoticed. Store partial artifacts on cancellation and resume from the latest valid checkpoint.

---

## 7. End-to-end execution algorithm

### Step 0 — qualify the task

Before recursion, decide whether harnessing is worth its cost.

Do not recurse for a trivial factual answer, a low-risk one-shot task, an explicit user request to avoid actions, or a task that has no observable success criteria. Consider it for multi-step coding, browser/tool-heavy research, document/artifact creation, recurring Alpha workflow failures, or complex goals with measurable deliverables.

Create a task contract:
- Requested outcome and allowed side effects.
- Deliverables and required output locations/formats.
- Available tools and permissions.
- Success checks and known uncertainties.
- Cost/latency/recursion budget.
- Task domain tags and relevant experience entries.

### Step 1 — snapshot the baseline

- Resolve the active harness version and calculate the manifest/component hashes.
- Freeze the model/provider, generation parameters, tool versions, runtime build, environment permissions, working tree, and relevant memory references for the run where possible.
- Prepare an isolated baseline workspace.
- Record network access policy and whether environment state could not be held constant.

The point of matched probes is not to pretend every environment is deterministic; it is to log and reduce confounders.

### Step 2 — execute baseline and capture evidence

Run the task once with the active harness. Capture a structured trace rather than only the final chat response:
- task/goal and plan revisions;
- selected tools, arguments (with secrets removed), results, errors and retries;
- agent/subagent assignments and handoffs;
- files read/written and artifact hashes;
- verification commands and output;
- cancellation, timeout, looping, and early-exit events;
- context/token usage, provider cost if available, latency, and resource usage;
- final deliverables and validator results.

Diagnose the **earliest meaningful divergence** from the desired behavior. For example, “tool permission denied” is not the same failure as “tool is unavailable,” “tool arguments are invalid,” or “the result was returned but not read.” Match the edit to the observed problem.

### Step 3 — form two bounded proposals

Produce at least two distinct hypotheses, using the evidence from the baseline and applicable experience entries:

1. **Invariant/general proposal:** fixes a broadly reusable agent-process problem, such as incorrect tool selection, excessive repeated calls, missing completion checks, or poor context management.
2. **Task/domain proposal:** fixes a task- or domain-specific issue, such as repository-specific build commands, a data transformation procedure, or a narrow browser workflow.

Every proposal must state:
- the trace event(s) motivating it;
- the exact component(s) to change and diff;
- expected observable improvement;
- how the probe could disconfirm the hypothesis;
- likely side effects and intended scope;
- why a cheaper component was insufficient, if proposing a script/plugin or always-on prompt change.

Do not create a new tool or script for every individual failure. Avoid broad refactors while trying to test one behavioral hypothesis.

### Step 4 — validate candidate edits before execution

Apply candidate diffs only to a new immutable candidate bundle. Reject or quarantine changes that:
- touch protected runtime code or trusted safety policy;
- add undisclosed network access, credential access, or external side effects;
- add tool permissions beyond the task's existing authorization;
- reference missing files or unbundled helpers;
- introduce invalid frontmatter/schema, path traversal, symlink escape, or malformed manifests;
- remove required triggers or break dependency references;
- introduce shell commands outside the allowlist/sandbox;
- change the provider/model/environment in a paired experiment without an explicit reason;
- exceed the configured size, recursion, or token budget.

Run formatting, parsing, static checks, manifest checks, script syntax checks, skill loading tests, and policy diff checks before probing.

### Step 5 — probe on the same task under matched conditions

Execute each candidate in its own scratch workspace/container. Reuse the task contract and the base environmental snapshot where technically possible. Do not share writable state between candidate probes unless the experiment requires it and the shared state is deliberately recorded.

Keep isolated:
- filesystem/worktree;
- generated scripts and caches;
- browser profiles/downloads;
- environment variables and temporary secrets;
- memory writes and long-term memory updates;
- message histories and agent state;
- external writes such as sending messages, deleting files, purchases, deploys, or publishing.

For side-effectful tasks, use dry-run/mock tools or require user confirmation before external execution. A harness experiment should not send multiple real emails, deploy several versions, or mutate real user files merely to compare candidates.

### Step 6 — produce a contrast report

The evaluator should answer narrow questions:
- Did the targeted failure happen again?
- Did the expected action occur earlier or more reliably?
- Did tool errors, retries, stuck loops, or needless calls decline?
- Did the deliverable become more complete or more conformant?
- Did regression validators fail?
- What changed besides the target behavior?
- Was the difference large enough to separate from run-to-run noise?
- What remains unknown?

A useful contrast can be **positive**, **neutral**, **negative**, or **confounded**. A neutral/unclear result should not be described as a win. Use deterministic checks wherever possible (tests, file existence, schema/format validation, compile/build, required sections, URL status, diff cleanliness). Use model judging only for qualities that deterministic checks cannot measure, and include the judge prompt/model and uncertainty in the trace.

### Step 7 — compose conservatively

The composer receives the original harness, the baseline trace, candidate diffs, candidate traces, artifact checks, and contrast reports. It must:
- adopt only edits supported by a concrete observed benefit;
- preserve non-conflicting successful edits where evidence supports each one;
- resolve contradictory edits explicitly;
- retain the base component when the evidence is neutral, ambiguous, or regressive;
- write a decision record tying every adopted or rejected edit to evidence;
- avoid converting task facts into global rules;
- avoid making hard safety restrictions weaker.

Do not reward the composer for producing more code or a more complicated harness. “No change” is a valid and sometimes preferred result.

### Step 8 — execute and verify the composed candidate

Run the composed successor and validate the actual final output. A successful tool call or file-write response is not proof the artifact is usable. Re-open files, parse outputs, run relevant tests, check requested paths/formats, and confirm the deliverable contract.

Keep **harness selection** separate from **deliverable selection**. If several outputs are available, optionally run a blind selector that sees the task contract, source material, and anonymized deliverables—but not which harness produced which deliverable. The selector should choose an existing candidate output rather than silently synthesizing a new output if the purpose is to evaluate harness changes. The project can offer a separate synthesis/finalization phase, but record it as an additional intervention.

For critical tasks, compare with the baseline before returning the result. A more elaborate successor is not necessarily better. If the composed candidate regresses, fall back to the baseline deliverable, and do not promote the successor harness.

### Step 9 — reflect and update the long-term library

After the task result has been returned/settled, reflect over the whole improvement record. Record reusable lessons only when the trace contrast supports them.

Each lesson should contain:
- short title and actionable lesson;
- applicable domain/task/environment conditions;
- symptoms that trigger retrieval;
- component diffs or reference bundle IDs;
- supporting run and trace IDs;
- observed positive effect and metrics;
- counter-evidence or known regressions;
- confidence/status, creation time, review time, expiry/invalidation condition;
- compatibility constraints (Alpha version, model/tool versions, OS, provider as relevant).

If evidence is insufficient, create **no new lesson**. It is better to have an empty library update than to turn speculation into durable behavior.

---

## 8. Sequential vs. parallel recursion

### 8.1 Sequential mode — recommended first

Sequential mode runs a sequence of Propose → Probe → Compose steps. Each later step sees the newly composed candidate and accumulated earlier evidence.

**Advantages for Alpha:** easier to inspect, fewer simultaneous sandboxes, clearer causal sequence, lower peak resource demand, simple cancellation/resumption.

**Risks:** an early poor change may bias later steps; it can settle on a local improvement and explore less broadly.

Use it for the initial MVP and on memory/resource-constrained local machines.

### 8.2 Parallel mode — add after the MVP

In Harness²'s documented scheme, `k` controls a wider exploration: it gathers `k` reference executions, proposes/probes `k` invariant and `k` domain-specific candidates, and produces `k` composed successors from the shared candidate pool. This is not simply “launch k generic agents”; candidates are varied harnesses tested against evidence from the same task.

**Advantages:** explores multiple hypotheses and compositions; may find alternatives sequential refinement misses.

**Risks:** more model calls and sandbox work, higher peak CPU/RAM, race risks if state is not isolated, and more difficult evidence interpretation.

Alpha should implement parallel mode using the existing scheduler with a bounded concurrency semaphore and separate scratch workspaces. Add per-provider rate-limit awareness and queueing. Never let `k` multiply without a hard cap.

### 8.3 Recommended recursion budget ladder

| Stage | Mode | Budget | Purpose |
|---|---|---:|---|
| Unit tests | Stubbed/deterministic | 1 | Verify state machine, patch validation, recovery, and records without model costs. |
| Local MVP | Sequential | `k = 1` | One general/domain contrastive cycle. |
| Small pilot | Sequential | `k = 2` | Test whether a second step produces added measurable value. |
| Candidate research | Parallel | `k = 2` | Explore a small population with explicit cost and concurrency caps. |
| Production default | Adaptive, bounded | 0–2 by task | Skip when unnecessary; recurse only when expected benefit exceeds cost/risk. |

Do not set `k` to a large number by default. The paper explicitly notes that recursive refinement adds inference cost and does not establish a universally optimal budget. Alpha should stop when there is no new evidence, budgets are reached, or expected gains fall below the minimum threshold.

---

## 9. Harness improvement library and memory integration

Alpha's existing memory system should not be replaced. Add a **separate evidence-indexed library for learning how to improve the harness**.

### 9.1 Keep memory categories distinct

| Type | Stores | Example | Should it automatically change the harness? |
|---|---|---|---|
| Conversation/episodic memory | What happened in a particular chat/run | “Build command failed yesterday” | No |
| Semantic/domain memory | Durable facts about a subject/domain | Known module/API constraints with source | No; retrieval only |
| Task/workspace memory | Facts specific to the current repository/project/session | “This repo uses `pnpm test`” | Only while the context matches; never global by default |
| Procedural memory/skills | Reusable how-to workflows | “Reproduce a UI bug and capture screenshot evidence” | Load on trigger; version and test |
| Harness-improvement experience | Evidence about which harness changes help under what conditions | “Adding an explicit post-write reopen check reduced empty artifact outputs in these tasks” | May inform proposals, not bypass validation or promotion |
| Guardrail/policy | Permissions and non-negotiable restrictions | “Never access credentials without authorization” | Enforced by trusted runtime; not editable by LLM harness proposals |

### 9.2 Entry structure

Prefer a small Markdown entry for human readability plus a structured metadata file for filtering/ranking.

`EXPERIENCE.md` template:

```markdown
# Experience: <specific, observable lesson>

## Scope
- Domain/task types:
- Trigger symptoms:
- Environment/tool/runtime assumptions:
- Explicitly not applicable to:

## Evidence
- Baseline run / trace:
- Candidate bundle(s):
- Probe run(s):
- Validator results:
- Observed change and uncertainty:

## Harness change
- Component(s):
- Diff or bundle reference:
- Why this component is the cheapest effective intervention:

## Result
- Benefit observed:
- Regressions/counter-evidence:
- Confidence/status:

## Maintenance
- Created / last reviewed:
- Review or expiry condition:
- Supersedes / superseded by:
```

### 9.3 Retrieval strategy

Start simply; don't require a vector database just to launch harness RSI.

1. Filter by task class, current agent profile, tool names, OS/runtime compatibility, and permission scope.
2. Rank using lexical or embedding similarity plus empirical success rate and recency.
3. Retrieve a small set with the actual component diff and the experience explanation.
4. Prefer prior evidence from the same environment; treat cross-domain transfer as a hypothesis.
5. Never treat the retrieved entry as an instruction that can override the current task or safety policy.

Store evidence metadata and use a rebuildable search index. If Alpha already has vector/semantic retrieval, reuse it behind an interface; do not introduce a new database without a demonstrated need.

### 9.4 Avoid library pollution

- Require explicit trace references for every claim of benefit.
- Write no lesson if the probes are confounded or the effect cannot be observed.
- Deduplicate near-identical lessons; link rather than copy them.
- Mark outdated tool/model-specific lessons as stale when their version constraints no longer apply.
- Track negative findings (“this middleware caused timeouts under tool X”) as well as wins.
- Set size/token limits on retrieved lessons; summarization must not remove scope or counter-evidence.
- Add a review/decay policy and preserve raw evidence for audit.
- Do not use a model's self-asserted confidence as the sole retention criterion.

---

## 10. Storage and trace schema

Use Alpha's current durable storage and migrations. For a single-user local-first install, a transactional embedded database can be sufficient for metadata; use a server database only if existing multi-process/distributed concurrency requires it. Keep larger trace payloads and artifacts as content-addressed files/blobs, indexed by metadata. The design must support atomic state transitions, durable resume, and immutable history.

### Suggested logical entities

| Entity | Important fields | Reason |
|---|---|---|
| `harness_bundle` | ID/hash, parent, runtime/policy version, creation time, status | Immutable version lineage. |
| `harness_component` | component ID/kind, bundle ID, path/hash, dependency refs | Inspectable, scoped edits and diffs. |
| `harness_run` | run ID, task fingerprint, mode, budget, status, active/base bundle ID, config hash | Durable state machine/resume. |
| `execution_run` | run ID, role (baseline/probe/successor), bundle ID, environment fingerprint, trace/artifact refs | Matched execution records. |
| `trace_event` | event ID, parent run, step index, agent ID, type, safe payload ref, timestamp | Debugging and contrast. |
| `proposal` | proposal ID, scope, hypothesis, evidence IDs, diff refs, validation state | Why a candidate was created. |
| `probe_result` | proposal/bundle ID, run ID, validator metrics, tool errors, tokens, duration | Candidate outcome. |
| `contrast_report` | baseline/candidate refs, targeted failure result, regression summary, uncertainty | Evidence connecting edit to outcome. |
| `composition_decision` | selected diffs, rejected diffs, rationale/evidence refs, composer version | Audit trail for compose. |
| `evaluation` | task contract version, validator versions/results, judge metadata | Versioned acceptance criteria. |
| `experience_entry` | scope/tags, evidence refs, component refs, confidence/status, expiry/supersession | Long-term improvement learning. |
| `promotion_event` | prior active bundle, candidate bundle, checks, approval mode, rollback ref | Safe activation and rollback. |
| `budget_ledger` | provider/model, estimated/actual tokens/cost, elapsed time, ceilings | Hard cost control. |

### Trace redaction and privacy

Do not copy API keys, access tokens, cookies, private file contents, or sensitive user data into the reusable experience library. For detailed traces, redact secrets before persistence and before exposing evidence to LLM editors. Keep the unredacted content only where needed for task execution and allowed under the user's data settings. Experience entries should prefer references and hashes over duplicating source files.

---

## 11. Evaluation and promotion policy

A central lesson is that the probe comparison helps decide which change merits further testing; it **does not certify correctness**. Alpha needs a promotion framework beyond “the judge preferred candidate B.”

### 11.1 Layered evaluation

1. **Hard acceptance checks:** requested deliverables exist, output parses, schema/format matches, files reopen, tests/build pass, permissions were respected, prohibited side effects did not occur.
2. **Task-specific checks:** coding tests, artifact completeness, required fields/sections, citations/source coverage, browser action completion, or explicit goal subtasks.
3. **Trace/process metrics:** correct tool selection, fewer tool errors, fewer redundant retries, fewer premature exits, successful handoffs, sensible completion checks.
4. **Quality review:** blind pairwise comparison or rubric-based model judging when hard checks cannot measure quality. Record the judge model, prompt/version, and disagreement.
5. **Regression suite:** run the candidate against a small fixed set of historical/synthetic tasks before promotion; test at least the task family the edit claims to generalize to.

### 11.2 Suggested decision rules

A candidate can be **adopted for the current task output** without being **promoted globally**. Track these decisions separately.

- **Reject immediately:** guardrail violation, invalid patch, unauthorized side effect, severe regression, or unbounded resource use.
- **No evidence / neutral:** retain baseline; do not write a positive experience lesson.
- **Current-task improvement only:** use the better deliverable, keep harness as a draft, and record the scope narrowly.
- **Candidate for shadow/canary:** all hard checks pass and the improvement is repeatable enough for the applicable task family.
- **Promote:** candidate passes regression and safety checks, shows predefined benefit above a noise/cost threshold, and activation is atomic with rollback. Require human approval for high-impact components or external-side-effect changes.

Do not automatically promote changes to middleware, tool permissions, trusted policy, provider secrets handling, process execution, network access, or filesystem boundaries. Ideally, candidates cannot edit those systems at all.

### 11.3 Promotion states

```text
DRAFT -> STATICALLY_VALIDATED -> PROBED -> REGRESSION_TESTED
      -> SHADOW -> CANARY -> ACTIVE
          \\-> REJECTED / QUARANTINED

ACTIVE -> ROLLED_BACK (if health/regression trigger fires)
```

Maintain a known-good last-active bundle and one-click rollback. Use an atomic manifest/pointer swap only after checks pass. The watchdog/health supervisor must continue to run even if the UI closes; recursive harness work must not own Alpha's service supervision lifecycle.

---

## 12. Security and reliability requirements

These are requirements, not optional prompt suggestions.

### 12.1 Trust boundaries

- The user's task, web pages, tool outputs, repositories, skill files, trace logs, and model-generated proposals are **untrusted content**.
- The harness editor can produce candidate diffs, not execute them with host privileges.
- A composer cannot override trusted runtime permissions.
- A retrieved experience entry cannot override the current task, user consent, or security policy.
- Evaluation code and hidden grader data must not be visible to the task executor.

### 12.2 Execution isolation

- Run candidate scripts/plugins in disposable sandboxed workspaces or containers with minimal permissions.
- Use per-probe working directories, environment variables, browser profiles, and caches.
- Block sensitive host paths and secret stores unless explicitly required and authorized.
- Network access should be deny-by-default for generated scripts and configurable per tool/task; log exceptions.
- Never execute a patch just because the LLM claims it is safe.
- Prevent symlink/path traversal, shell escaping, command injection, and dependency confusion.
- Require human consent for irreversible real-world actions unless the user has already authorized that exact action path.

### 12.3 Budgets and circuit breakers

Configure hard limits for:
- recursion depth/width;
- model calls and tokens per run;
- total cost per run and per day/provider;
- wall-clock duration;
- tool calls/retries;
- number and size of diff files;
- sandbox CPU/memory/disk/network usage;
- parallel candidate count;
- repeated no-progress steps.

Stop when the same hypothesis has been tested without new evidence, the task contract is satisfied, the improvement is below the minimum value threshold, budgets are depleted, or a safety/health check fails. Never treat repeated blind retries as self-healing.

### 12.4 Crash handling and recovery

- Persist each transition and artifact before advancing.
- Stage work in candidate-specific temporary folders and clean up safely after failure.
- Resume only when run configuration and input hashes match.
- Keep separate run output roots when model, budget, seed, executor, or task selection changes.
- Use a lease/lock so the same harness run cannot be resumed concurrently by multiple workers.
- Retain failures as first-class results with error classification and recovery steps.

---

## 13. API and UI integration for Alpha

The harnessing workflow should be visible to users, especially because Alpha supports goals, multiple agents, subagents, and a live status UI. Add this as a separate **Harness Lab / Improve Runtime** view rather than cluttering the normal chat.

### 13.1 Candidate API surface

Adapt routes to Alpha's existing API conventions; these are illustrative:

```text
POST /api/harness/runs
GET  /api/harness/runs/{run_id}
POST /api/harness/runs/{run_id}/cancel
GET  /api/harness/runs/{run_id}/events
GET  /api/harness/bundles
GET  /api/harness/bundles/{bundle_id}/diff
GET  /api/harness/experiences
GET  /api/harness/experiences/{experience_id}
POST /api/harness/candidates/{bundle_id}/approve
POST /api/harness/candidates/{bundle_id}/reject
POST /api/harness/active/{bundle_id}/rollback
GET  /api/harness/budgets
```

Useful event types for WebSocket/SSE: `run.created`, `stage.changed`, `baseline.completed`, `proposal.created`, `candidate.validation_failed`, `probe.started`, `probe.completed`, `contrast.ready`, `composition.ready`, `evaluation.completed`, `experience.created`, `promotion.changed`, `budget.warning`, `run.failed`, `run.completed`.

### 13.2 UI panels

1. **Live run timeline:** Baseline → proposal → probe → contrast → compose → verify → reflect. Show current stage, agent/subagent, duration, token/cost usage, and cancellation.
2. **Baseline vs. candidate:** Side-by-side diffs, tool timeline, error counts, artifact previews, test results, and uncertainty/confounders.
3. **Change rationale:** Every diff linked to the observed trace event and its expected effect. Show rejected changes as well as adopted ones.
4. **Harness versions:** Immutable version tree, active/draft status, compatibility, and rollback.
5. **Experience library:** Scope filters, evidence links, superseded/stale warnings, and component reuse.
6. **Controls:** Recursion mode, budget cap, domain selection, sandbox policy, approval requirements, and “dry run/no side effects.”
7. **Goal integration:** When a Goal spawns tasks/subtasks, show a harness improvement as a separate child activity, not as an invisible rewrite of the whole goal plan.

UI status must come from persisted backend run state, not from optimistic assumptions or an LLM-generated status message.

---

## 14. Metrics to collect

Track metrics per task, domain, model/provider, active bundle, candidate bundle, and runtime version.

### Outcome metrics
- Task contract pass rate.
- Artifact completeness/format validity.
- Regression tests passed/failed.
- Human acceptance/override rate if available.
- Success rate on a held-out task-family suite.

### Runtime/process metrics
- Tool calls per successful task.
- Tool-call failure rate by category.
- Retry count and repeated identical call count.
- Stuck/no-progress loops.
- Premature completion/empty artifact rate.
- Subagent handoff success/failure.
- Required verification frequency and actual verification outcomes.

### Efficiency metrics
- Input/output tokens and provider cost.
- End-to-end latency and stage latency.
- CPU/memory/disk peak in local probes.
- Additional cost per absolute task-success improvement.
- Improvement yield per probe and stop reason.

### Safety/reliability metrics
- Rejected unsafe patch count.
- Unauthorized action attempts blocked.
- Secret-redaction events.
- Probe sandbox violations.
- Rollback rate and time to rollback.
- Resume/recovery success after simulated crash.

Avoid optimizing a single metric in isolation. “Fewer tool calls” is not a win if completion quality drops; “higher judge score” is not a win if the candidate violates permissions or exceeds cost by 10×.

---

## 15. Test plan before enabling self-improvement

### Unit tests
- Manifest parsing, canonicalization, hashes, parent lineage, and schema migrations.
- Patch allowlist and forbidden-path detection.
- Component dependency/trigger validation.
- Diff application and rollback.
- Run-state transition validation.
- Budget accounting and circuit breakers.
- Trace redaction and secret scrubber.
- Experience retrieval scope filters and supersession rules.

### Integration tests
- Stub model generates valid, malformed, conflicting, and malicious proposals.
- Baseline/candidate probes use isolated workspaces.
- Candidate writes cannot affect the active bundle or another candidate.
- Failed probe does not stop other candidates or corrupt the run.
- Restart Alpha mid-run and resume from the last checkpoint.
- Cancel a run and verify workers/workspaces are cleaned up safely.
- Promote and roll back a candidate while the main runtime stays healthy.
- Verify the UI timeline reflects persisted state and truthful failure reasons.

### Adversarial/security tests
- Prompt injection in task documents and tool output attempts to alter policy.
- Proposal attempts to read `.env`, browser cookies, SSH keys, or unrelated user paths.
- Malicious shell code, symlink escape, and path traversal.
- Middleware that never yields, blocks valid tool completion, or loops forever.
- Script that attempts unrestricted network access or persistent background processes.
- Task that requests irreversible action during a probe.
- Experience entry that tries to supersede a newer or stricter policy.

### Deterministic synthetic benchmark
Begin with a small fixed suite of tasks where the failure mode and success conditions are known, such as:
- use the correct existing project test command rather than guessing;
- read a tool error and correct arguments instead of repeating the same call;
- write a requested file and reopen/parse it before claiming success;
- stop after the task contract is met instead of endlessly researching;
- pass a minimal task to an appropriate subagent and return a compact result;
- avoid loading an unrelated skill into every model call;
- produce a complete handoff when a subagent fails.

Record a baseline score before any harness edits. Hold a portion of tasks out of the proposal/experience workflow to test generalization, and avoid leaking expected results to the execution agent.

---

## 16. Phased implementation roadmap

### Phase 0 — instrumentation first
**Goal:** Ensure Alpha can explain what happened before allowing it to change how it works.

Tasks:
- Normalize tool/agent/subagent trace events.
- Persist tool errors, retries, handoffs, artifact writes, verification results, and usage/cost.
- Add task contracts and explicit completion validators.
- Add trace redaction and immutable artifact references.

Exit criteria: Alpha can reconstruct the baseline execution timeline and identify the first failure event in deterministic integration tests.

### Phase 1 — immutable harness bundles
**Goal:** Version the currently active components without changing normal behavior.

Tasks:
- Define a strict manifest and component version/hash model.
- Snapshot active prompts, tool guidance, skill files, agent definitions, eligible scripts and middleware configs.
- Add diff view, parent lineage, active/draft status, and rollback.
- Separate trusted policy from editable guidance.

Exit criteria: Existing tasks behave the same under the snapshot version; no candidate can modify the active version in place.

### Phase 2 — baseline + one contrastive cycle
**Goal:** Ship the smallest useful Propose–Probe–Compose loop.

Tasks:
- Implement sequential `k=1`.
- Run one baseline.
- Generate general and domain/task-specific proposals.
- Validate diffs and run probes in isolation.
- Generate contrast reports and conservative composition decisions.
- Execute and verify the composed successor.
- Return the safest successful deliverable and preserve the baseline if successor fails.

Exit criteria: The full path passes synthetic tests, including “no change,” malformed proposal, probe failure, regression, cancellation, and crash/resume.

### Phase 3 — evidence library
**Goal:** Make lessons reusable without contaminating memory.

Tasks:
- Add structured `EXPERIENCE.md` + metadata entries.
- Link every claimed win to baseline/candidate traces and validator outputs.
- Add domain/tool/model/runtime filters, stale entry status, and negative lessons.
- Retrieve a bounded number of lessons into future proposal/composition context.

Exit criteria: A lesson with no evidence is rejected; scoped repository facts do not retrieve for unrelated repositories; stale tool-version entries are marked.

### Phase 4 — verification, regression, and promotion gates
**Goal:** Prevent a one-task improvement from destabilizing the global agent.

Tasks:
- Add task-family regression suite and quality thresholds.
- Add shadow/canary status and manual approval controls.
- Add atomic activation and rollback.
- Add health checks and circuit breakers.
- Make changes to privileged components ineligible for automatic promotion.

Exit criteria: A candidate that fails any hard check cannot become active; rollback restores a known-good bundle and the runtime remains available.

### Phase 5 — Alpha UI / Goal integration
**Goal:** Make improvement runs observable and controllable.

Tasks:
- Add live stage timeline, candidate diffs, trace comparison, budget meter, and run history.
- Add cancel/resume and approval/rejection actions.
- Integrate improvement jobs under an Alpha goal as child activities.
- Show which harness version each agent/subagent used.

Exit criteria: Users can tell what is happening, why a change is proposed, what evidence supports it, what it costs, and how to undo it.

### Phase 6 — bounded parallel exploration
**Goal:** Use swarm resources for broader candidate search after sequential logic is dependable.

Tasks:
- Add a capped candidate pool and scheduler integration.
- Use isolated per-candidate environments and independent budgets.
- Add provider rate-limit awareness and deterministic run configuration.
- Compare parallel cost/latency/outcome against sequential runs.

Exit criteria: Parallel runs cannot exceed hard resource limits, interfere with one another, or produce non-auditable mixed results.

### Phase 7 — adaptive recursion and library maintenance
**Goal:** Decide intelligently when more experiments are worth their cost.

Tasks:
- Estimate task complexity and prior evidence relevance.
- Stop when the expected marginal improvement is low or a probe adds no information.
- Consolidate duplicate/conflicting experience entries.
- Add periodic revalidation and compatibility expiry.
- Test cross-domain and cross-model transfer separately from same-domain wins.

Exit criteria: Adaptive policy beats a fixed budget on a repeatable held-out suite without violating the cost ceiling or quality thresholds.

---

## 17. Suggested initial configuration

These are conservative defaults for the first Alpha implementation. Make them user-configurable and enforce them in code.

```toml
[harness_improvement]
enabled = false                 # feature-flag until Phase 2 passes tests
mode = "sequential"
max_steps = 1
max_candidates_per_scope = 1
max_concurrent_probes = 1
max_wall_time_seconds = 300
max_tool_calls_per_probe = 60
max_model_turns_per_probe = 80
max_diff_files = 8
max_changed_bytes = 32768
max_experience_entries_in_context = 5
min_evidence_for_positive_lesson = 1

[harness_improvement.sandbox]
isolate_filesystem = true
isolate_memory_writes = true
allow_external_side_effects = false
network_mode = "deny_by_default"
allow_candidate_middleware_changes = false
allow_candidate_guardrail_policy_changes = false

[harness_improvement.promotion]
automatic_global_promotion = false
require_regression_suite = true
require_human_approval_for_scripts = true
require_human_approval_for_middleware = true
require_human_approval_for_permissions = true
keep_last_known_good = true
```

Tune limits to Alpha's current execution limits. Do not use the above values as universal performance claims.

### Cost accounting

Before a run, estimate the number of model calls/turns and expected tokens for baseline, proposals, candidate probes, composition, successor execution, reflection, and evaluation. Reserve a budget for the final deliverable; do not let research/proposal stages consume the whole budget before Alpha produces the requested artifact. Stop before the cap and report the incomplete state honestly.

Track actual cost per stage. If a provider gives no trustworthy price/usage metadata, record the missing field instead of fabricating a cost estimate. A local model may have zero API charge but still incurs latency, RAM, power, and opportunity costs.

---

## 18. Example walkthrough: Alpha keeps failing to verify generated files

This example is illustrative, not a claim about Alpha's measured performance.

**Task:** “Create a JSON configuration and confirm it is valid.”

**Baseline trace:** Alpha writes `config.json`, receives a successful tool response, and announces completion without reopening/parsing the file. The completion validator finds no verification event. This is an observable premature-completion defect.

**General proposal:** Add a concise output-verification instruction for artifact-producing tasks, but keep it scoped to tasks with a deliverable.

**Domain/task proposal:** Add an on-demand “artifact verification” skill that reopens each requested output and runs the available parser/schema validator; include a helper if deterministic checks are repeated.

**Probes:** Both candidates work from the same task and test workspace. Record if the file is reopened, whether parse/schema validation passes, and whether an incomplete output is incorrectly reported as finished.

**Contrast:** If the skill candidate produces a valid file and verifies it with fewer repeated tool calls than the baseline, that supports the targeted hypothesis for artifact tasks. It does not yet prove the skill helps unrelated browser or conversational tasks.

**Compose:** Prefer the cheaper effective intervention. If a narrow skill is enough, do not add a complex middleware hook. Keep the trusted global policy unchanged.

**Final verification:** Reopen and parse the composed output. If it fails, retain the baseline harness and report the actual failure rather than claiming success.

**Experience entry:** “For artifact-producing tasks, trigger the verification skill after the write; the evidence is [run IDs], scope is artifact-producing tasks, and the lesson must be revalidated when the tool API/schema changes.”

**Promotion:** Run the skill against a few held-out artifact tasks. Only consider broader activation after the results survive regression checks.

---

## 19. Implementation anti-patterns to avoid

- **One giant self-improvement prompt:** mixes proposing, executing, judging, and permanently learning; weak evidence and hard debugging.
- **Direct active-file edits:** a failed experiment corrupts the main agent and makes rollback ambiguous.
- **Judge-only promotion:** LLM preference without deterministic checks, trace evidence, or repeatability.
- **Copying the previous task's harness:** overfits and carries task facts into unrelated work.
- **Editing protected policy:** natural-language guardrails cannot replace enforced permissions.
- **Always-on prompt bloat:** every extra instruction consumes context on every turn; use on-demand skills when triggered.
- **Script/plugin-first fixes:** adding executable code for a one-off mistake increases attack surface and failure modes.
- **Unlimited parallelism:** candidate count multiplies latency, cost, RAM/disk usage, and failure blast radius.
- **Unmatched probes:** candidates use different models, tools, permissions, network access, or source data without recording the difference.
- **No-op treated as failure:** preserving the baseline when evidence is weak is correct behavior.
- **Unbounded memory library:** near-duplicate lessons, stale procedures, and hallucinated causal claims become future prompt pollution.
- **No durable progress state:** app restarts lose the run and the system cannot distinguish incomplete from successful work.
- **Silent fallback:** model/provider/agent falls back but the trace does not expose the changed execution conditions.
- **Success defined as a final answer only:** ignores missing files, malformed artifacts, unperformed actions, failed tests, and violated permissions.

---

## 20. Definition of done for Alpha's Harness²-inspired MVP

The first release is ready when all of these are true:

- [ ] Active harnesses are immutable, versioned bundles with hashes and parent lineage.
- [ ] Runtime guardrails and permissions are outside the LLM-editable surface.
- [ ] Every improvement run stores the task contract, base bundle, configuration, costs, and state transitions.
- [ ] A baseline execution is captured with structured trace events and artifact refs.
- [ ] General and task/domain proposals include explicit hypotheses and evidence refs.
- [ ] Candidate patches are schema-checked and path/policy-checked before execution.
- [ ] Probes run in isolated workspaces and do not share mutable memory by accident.
- [ ] Contrasts use deterministic validators first and surface confounders/uncertainty.
- [ ] Composition can keep the baseline and links every selected/rejected diff to evidence.
- [ ] The final composed candidate is executed and its actual deliverables verified.
- [ ] Experience memory is separate from normal conversation/semantic memory and rejects unsupported lessons.
- [ ] A crash/restart test resumes safely without duplicate promotion or corrupted state.
- [ ] A candidate cannot promote globally without passing configured gates.
- [ ] Rollback restores the prior known-good harness.
- [ ] Budget limits, cancellation, privacy redaction, and security tests pass.
- [ ] The UI explains candidate changes and allows cancellation/rejection/rollback.
- [ ] Baseline-versus-improved results are measured on a fixed synthetic suite and a held-out task set.

---

## 21. Final recommendation

For Alpha, the best way to use Harness² is to implement its **method**, not blindly copy the full experimental stack:

1. Version every eligible harness component and keep the active bundle immutable.
2. Use one baseline execution to ground a concrete failure hypothesis.
3. Probe a general fix and a task/domain fix under matched, isolated conditions.
4. Compose only evidence-supported changes; preserve the baseline when uncertain.
5. Verify the actual artifact independently of the agent's success message.
6. Save only scoped, evidence-backed reusable lessons in a separate improvement library.
7. Promote changes slowly with regression checks, canaries, rollback, and hard security policy.
8. Add parallel swarm exploration only after the serial implementation is reliable and its value-per-cost is measured.

This gives Alpha a practical path toward recursive harness improvement without conflating it with uncontrolled self-modifying code. It also fits a Rust-first implementation: Rust can own the state machine, bundle validation, diff authorization, sandbox orchestration, trace contracts, budget accounting, and promotion policy; the existing provider adapter can supply language-model calls; and existing Python/Node executors can remain behind a controlled adapter only when needed.

---

## References

1. Xu et al., **Harness²: Recursive Agent Harnessing for an Open World**, arXiv:2610.14224, version 1, 9 October 2026. <https://arxiv.org/abs/2610.14224> (paper HTML: <https://arxiv.org/html/2610.14224>)
2. Google Research, **harness2 repository README**. <https://github.com/google-research/harness2>
3. Google Research, **Experiment guide**. <https://github.com/google-research/harness2/blob/main/docs/EXPERIMENTS.md>
4. Google Research, **package metadata (`pyproject.toml`)**. <https://github.com/google-research/harness2/blob/main/pyproject.toml>
5. Google Research, **third-party dependency/license notes**. <https://github.com/google-research/harness2/blob/main/dependencies/README.md>

**Caveat:** This is a research-informed design guide for adapting concepts to Alpha, not an official Google integration guide. Benchmark results quoted above belong to the cited paper and do not predict Alpha's performance. Check upstream repository and paper revisions before implementing against exact APIs or file names.
