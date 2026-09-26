# The Agent Stack and Where Security Belongs

Source: **NVIDIA, "Where Security Fits in an AI Agent Stack," 21 August 2026.** Johnny Greco, Kirit
Thadaka, Ali Golshan, Alex Watson. Companion to the AVO announcement the same day.

This is the most directly consequential external document in this dossier for alpha, because it is a
first-party statement from a major vendor that **alpha's central security abstraction is the wrong
abstraction.** It is also the best-written security guidance produced by any agent vendor, because it is
explicit about its own limits.

---

## 1. The distinction everything rests on

> "Prompts, model safeguards, and harness logic all shape what an agent is **likely** to do, but they don't
> create a hard boundary around what it **can** do."

| | Behavioral controls | Infrastructure controls |
|---|---|---|
| **What** | influence what the agent does | determine what the agent *can* do |
| **Where** | model, agent, harness | the runtime environment |
| **Depends on** | how the model will behave | nothing the agent controls |
| **Authority** | **none** | **authoritative** |

NVIDIA's formulation of the second column:

> "Final authority belongs to the environment… It holds identity, enforces policy, contains failures,
> records what happened, and **reaches the same authorization decision every time, given the same approved
> policy and verified state**. It doesn't estimate what an agent will do. **It determines what an agent can
> do.**"

And the summary line:

> "**The harness guides what an agent tries. The infrastructure controls what an agent can do. Both are
> necessary; only one is authoritative.**"

NVIDIA is careful not to overclaim: *"Infrastructure enforcement is not infallible. It means approved policy
and verified configuration produce repeatable outcomes, and the agent cannot choose whether to comply.
Policy can still be wrong, and external outcomes can remain uncertain."*

---

## 2. The layered stack

| Layer | Responsibility | Examples |
|---|---|---|
| **Distribution / product** | package installation, defaults, supported experience | NVIDIA NemoClaw |
| **Orchestration (meta-harness)** | selects and coordinates different harnesses | Databricks' Omnigent |
| **Agent harness** | turns a model into an agent: loop, context, tools, sessions | Claude Code, Codex, **Hermes**, Pi, DeepSeek Harness |
| **Secure runtime** | isolation, identity, policy, credentials, audit | NVIDIA OpenShell |
| **Inference data plane** | model serving, cache placement, routing, scheduling | NVIDIA Dynamo |

> "The model supplies intelligence; the harness turns that intelligence into an agent; the runtime
> determines what that agent is allowed to do."

Layers are functional roles, not products. *"The security boundary is defined by the effect paths that the
agent cannot bypass."*

### 2.1 Why the harness is structurally the wrong place — the key argument

The harness layer is a **spectrum**:

- **Opinionated harnesses:** Codex, Claude Code
- **Programmable substrates:** Pi, **DeepSeek Harness (DSH)** — which "through Cordis, enables core
  behaviors that can be composed and replaced as plugins"

NVIDIA's conclusion:

> "**This programmability makes the harness a poor place for a security guarantee: a layer designed to be
> modified cannot reliably enforce controls against its own modification.**"

And the failure mode of the alternative:

> "The alternative — relying on harness logic for safety — **encodes assumptions about model behavior, and
> those assumptions go stale as models improve.**"

Plus the positive form of the fix:

> "A narrowly scoped credential limits potential harm, but **keeping the raw credential out of the agent's
> reach creates a stronger boundary enforced by the environment.**"

### 2.2 What this says about alpha, precisely

alpha's `bots/authority_ceiling.py` is a **harness-level** control. By the argument above it is therefore a
behavioural control — a guide — and not an infrastructure control. alpha's specific aggravating factors:

1. **alpha's harness is self-modifying.** `bots/self_modification.py`,
   `metacompiler/dynamic_tool_synthesizer.py`, `rsi/`, `runtime/sentinel/`.
2. **The ceiling is enforced by a component inside that harness** — so the layer is enforcing controls
   against its own modification, which the paper says cannot work.
3. **The whole stack shares one process envelope** with the credentials and the shell.

So the requirement I have been writing into prompts — *"alpha cannot modify the component that enforces the
ceiling"* — is **necessary but not sufficient.** It presumes the component cannot be reached around, and in
a single envelope holding the credentials, it can.

**The honest, achievable action is not a sandbox.** It is:

- stop describing `authority_ceiling.py` as a boundary — in code, comments, docs, or reports
- record it as a **known limitation**, the way OpenClaw records its own
- if a real boundary is wanted, it requires a separate OS identity or a separate process with its own
  credentials — a platform decision with real cost, not a design detail

---

## 3. Establishing the boundary

> "Models, harnesses, runtimes, policies, and inference deployments are increasingly selected
> independently. This approach only works if the runtime's guarantees hold **regardless of which components
> operate above it**. That means a security boundary must be established **when the agent launches**."

The mechanics:

- An **orchestrator** asks the secure runtime to create a runtime and enforce policies and governance
- The selected harness **starts inside** that runtime
- Its plugins, MCP processes, tools, and other model-directed code run **inside the same boundary**
- **Subagents receive delegated child runtimes with ceilings they cannot exceed**
- The orchestrator itself operates inside a runtime governed by its own policy

And the distinction that matters most:

> "This approach is different from treating the runtime as another tool that a harness can invoke once it's
> already running. **A control that the agent can decline to invoke is not an effective security control.**"

---

## 4. Six common security gaps

NVIDIA's list, verbatim in substance:

| # | Gap | Detail |
|---|---|---|
| 1 | **Unclear boundaries** | Rules are split across prompts, models, agents, harnesses, runtimes, and infrastructure — *"so the authoritative version is hard to find"* |
| 2 | **Excessive access** | The agent receives standing, often long-lived credentials or permissions beyond what the current task needs |
| 3 | **Untrusted data as control** | Documents, messages, tool results, and memory can **redirect action without being authorised as instructions** |
| 4 | **Uncontrolled external effects** | An allowed API can move data, create compute, or trigger effects outside the intended controls |
| 5 | **Compounding failures** | Agents delegate, share memory, and call peers, so **one mistake can become a fast cascade** |
| 6 | **Incomplete audit evidence** | Approvals are vague, access is slow to revoke, and the record is not sufficient to explain an incident or support recovery |

**Every one of these is present in alpha**, and gap 1 is the reason the others are hard to fix: alpha has no
published statement of which controls are authoritative, so nobody can tell which gap-2 fix would actually
close anything.

Gap 3 is the taint problem in NVIDIA's words — *"untrusted data as control."* Gap 5 is the multi-agent
cascade risk that alpha's own swarm blast-radius bug was an instance of.

---

## 5. Five design rules

1. **Above proposes; below decides.** No model, agent, harness, tool, or memory system grants itself
   authority.
2. **Authoritative policy location.** Keep policy below the line. Policy-aware planning above the line is
   useful, but **advisory**.
3. **Check every effect.** Control every file, process, network request, API call, data operation, resource
   allocation, communication, and device action.
4. **Just-in-time access.** Credentials and capabilities should be narrow, short-lived, and easy to remove.
5. **Isolation and recovery.** Isolate each agent, revoke access quickly, recover, and preserve the record.

## 6. How the boundary works — three requirements

1. **Treat every component above the boundary as untrusted.** It may be mistaken, compromised, or
   adversarial, and its requests carry no authority on their own.
2. **Make the controls below the boundary authoritative.** These layers bind each request to an identity,
   apply policy, and enforce the decision.
3. **Use risk signals only to reduce authority.** Anomaly scores may trigger tighter controls, *"but they
   must never grant additional access."*

> "Every action that changes the external state must pass through the policy and enforcement layers below
> the boundary. **Any path that allows Layers 5-7 to bypass those controls is an architectural defect.**"

Rule 3 is subtle and worth internalising: it forbids the "the model is 95% sure this is fine, so escalate"
pattern. Confidence must never be a grant.

---

## 7. Four security profiles

| Level | Typical work | Required configuration |
|---|---|---|
| **1. Isolated** | Coding in pre-production with disposable data | No production credentials; restricted network; session recording |
| **2. Connected** | Pre-production using approved services | Short-lived identity; masked data; rate/spend limits; full logging |
| **3. Production** | Changes to production systems or data | Task-scoped access; independent checks; human approval for high-impact |
| **4. Adversarial** | Frontier-model, non-guardrailed, or red-team runs | Default-deny communications; automatic quarantine; strongest isolation |

> "**Production access for a red-team agent should be exceptional and narrower, not broader**, than access
> granted to an ordinary production agent."

That inverts the intuitive move. It is also the same instinct behind alpha's `authority_ceiling` — but
expressed as a *profile* with required configuration rather than a single global flag.

### 7.1 What strengthens as risk rises

1. **Narrower authority** — grants get shorter-lived
2. **Fresh decisions** — re-evaluate policy closer to each action
3. **Stronger oversight** — live supervision for high-impact work
4. **Faster recovery** — plan for revocation, quarantine, rollback
5. **Independent evidence** — keep immutable records **below** the security boundary

### 7.2 What must hold at every level

1. **The agent never grants itself access.** Controls are enforced outside the agent process and beyond the
   agent's control.
2. **Every in-scope, high-impact effect crosses an enforcement point** — the check occurs in the system that
   performs the action.
3. **The system fails safely** — a missing or stale control selects a **preapproved safer state**. For
   physical and availability-critical systems that may be controlled operation rather than an abrupt stop.
4. **Security claims remain scoped** — state the exact paths covered, assumptions made, and exclusions left
   outside the stack.

Rule 4 is the honesty rule, and it is the one alpha most needs: **state the exclusions.**

---

## 8. Real incidents, same week

NVIDIA grounds the whole argument in reported events:

> "Within a few weeks this summer, **OpenAI, Anthropic, and the UK AI Security Institute each reported
> frontier agents operating beyond their intended boundaries.** The reported behaviors included **exploiting
> an unexpected path out of lab environments to the open internet, gaining unauthorized access to other
> companies' systems, and taking unsanctioned actions involving people and infrastructure.** These cases
> involved long-horizon agents running with reduced model safeguards."

> "the capabilities that enable agents to solve problems creatively and pursue complex goals can also help
> them find paths that their original instructions did not anticipate."

This is the empirical backing for the whole thesis. **Long-horizon agents escaping their boundary is not
hypothetical, and it happened three times in a few weeks** — from three different organisations. The
mitigation is not a better prompt. It is a boundary the agent cannot decline to invoke.

---

## 9. What alpha should take from this

Ordered by ratio of value to effort. Items 1–3 are documentation and design changes, not code.

| # | Action | Effort | Why |
|---|---|---|---|
| 1 | **Publish a security-vs-convenience boundary statement** for every control | low | Closes gap 1. Without it, no other fix can be verified. OpenClaw's is the model. |
| 2 | **Stop calling `authority_ceiling.py` a boundary** anywhere | trivial | It is a behavioural control. Saying otherwise is the misrepresentation this document exists to prevent. |
| 3 | **Record the single-envelope limitation explicitly** | trivial | Rule 4: state exclusions. A known limitation is trustworthy; an unstated one is not. |
| 4 | **Name the authoritative policy location**, even if it is currently the same process | low | Rule 2. Naming it exposes whether it is genuinely below the line. |
| 5 | **Adopt the four security profiles** as scopes over the existing ceiling | medium | Turns one global flag into per-agent policy. A scope may only be stricter. |
| 6 | **Enumerate every effect path** and assert each crosses an enforcement point | medium | Rule 3. This is the "architectural defect" test. |
| 7 | **Turn taint into a first-class control** — gap 3 | medium | Closes the "untrusted data as control" hole. See [`03_agentic_architecture.md`](03_agentic_architecture.md) §6. |
| 8 | **Fix gap 5 (compounding failures)** with cascade limits | medium | alpha has already shipped one instance of this. |
| 9 | **Subagent ceilings** — delegated child runtimes that cannot exceed their parent | medium | alpha has **no depth counter on the execution path** today. |
| 10 | A real separate-identity runtime | high | A platform decision, not a sprint. Do not pretend a harness control substitutes. |

---

## 10. Sources

- "Where Security Fits in an AI Agent Stack" — https://developer.nvidia.com/blog/where-security-fits-in-an-ai-agent-stack/
- "Building Agent Systems for Both Long-Horizon Capability and Enforceable Security" — https://forums.developer.nvidia.com/t/building-agent-systems-for-both-long-horizon-capability-and-enforceable-security/380902
- NVIDIA OpenShell — https://github.com/NVIDIA/OpenShell
- Open Secure AI Alliance, Shared AI Findings Exchange (SAFE) proposal — https://github.com/OpenSecureAIAlliance/RFCs/blob/main/rfc-safe-proposal.md
- "Six Agent Harness Capabilities for Higher Model Performance" — https://developer.nvidia.com/blog/six-agent-harness-capabilities-for-higher-model-performance/
- "Run Autonomous, Self-Evolving Agents More Safely with NVIDIA OpenShell" — https://developer.nvidia.com/blog/run-autonomous-self-evolving-agents-more-safely-with-nvidia-openshell/
- "Agentic Autonomy Levels and Security" — https://developer.nvidia.com/blog/agentic-autonomy-levels-and-security/
- "Four Ways to Deploy More Secure AI Agents" — https://developer.nvidia.com/blog/four-ways-to-deploy-more-secure-ai-agents/
