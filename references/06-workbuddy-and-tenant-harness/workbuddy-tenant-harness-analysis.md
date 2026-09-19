# WorkBuddy & Tencent CodeBuddy — Harness Analysis

> Reference analysis for Alpha. Describes the *observed product behaviour and
> architecture* of WorkBuddy AI / Tencent CodeBuddy and the tenant-grade agent
> harness pattern it belongs to. Feature claims are recorded as **observed
> behaviour of the product surface**, not as verified internal specifications.

---

## 1. Classification

WorkBuddy / Tencent CodeBuddy is best understood as a **tenant-grade, product-first
agent harness**:

```
┌───────────────────────────────────────────────────────────────┐
│  PRESENTATION:  desktop app · IDE plugin · web · IM channels   │
├───────────────────────────────────────────────────────────────┤
│  PRODUCT LAYER: skills · connectors · experts · automations    │
│                 memory editor · approvals · artifacts          │
├───────────────────────────────────────────────────────────────┤
│  HARNESS LAYER: agent loop · context assembly · tool dispatch  │
│                 subagent/file-search delegation · hooks        │
├───────────────────────────────────────────────────────────────┤
│  RUNTIME LAYER: bundled runtimes · sandboxed shell · filesystem│
│                 MCP transports · browser automation            │
├───────────────────────────────────────────────────────────────┤
│  PLATFORM LAYER: identity · entitlements · cloud sync · docs   │
└───────────────────────────────────────────────────────────────┘
```

The distinguishing property versus a CLI coding agent: **every layer above the
runtime is a product surface a non-engineer can operate.**

---

## 2. Product & Platform Characteristics

### 2.1 Bundled, dependency-free distribution
- Ships desktop installers that bundle their own runtimes; the user does not
  install Python, Node, or package managers.
- Implies a **deterministic start sequence** with explicit readiness gates, and a
  strong bias toward self-repair over "please install X".

### 2.2 Account-bound identity and entitlements
- Features are gated by account tier; the harness must know *who* is running and
  *what they are allowed to do* at runtime, not at build time.
- Implies server-side feature flags mirrored client-side, plus graceful
  degradation when a capability is unavailable.

### 2.3 First-class local filesystem safety
- Operations on personal directories (Desktop, Downloads, Documents) are treated
  as high risk and subject to stricter rules than project directories.
- The controlling ideas: **no-go zones**, **scan-is-read-only**, **ambiguous means
  ask first**, **warning + explicit enumeration + confirmation before destructive
  action**, **backup first**, **trash instead of delete**, **small batches with
  verification**, and **no non-ASCII script files on Windows**.
- This is a *product trust* mechanism, not merely a safety guardrail.

### 2.4 Skills as the primary extensibility unit
- A skill is a folder with a `SKILL.md` manifest plus bundled scripts and
  references; it is loaded on demand, not all at once.
- Installation from a marketplace, a folder, or a URL is a **security event**:
  the recommended flow audits the manifest *and* every bundled file before
  completing the install, with severity tiers (blocker / warn / informational)
  and an explicit user confirmation gate at the higher tiers.
- Skills are described as "actionable and reusable" — the product nudges users to
  *accumulate* them from successful work rather than treating them as static config.

### 2.5 Connectors (MCP) as the integration unit
- External systems are attached as connectors, configured through a management
  surface rather than a config file, and require explicit user trust before
  activation.
- Config is written to a user-scoped location and a newly added server does **not**
  auto-activate until the user trusts it — an intentionally conservative default.
- Recommendation of connectors is contextual: the assistant proposes a connector
  only when the current task actually needs it, and never invents one.

### 2.6 Experts / personas as a routing layer
- A curated set of domain experts is presented in a browsable "center".
- Only **one** expert or expert team may be active per session — a deliberate
  constraint that keeps context and responsibility unambiguous.
- Enabling an expert is a session-level decision, separate from installing it.

### 2.7 Memory presented as user-visible and editable
- Memory is split across layers with different scopes and different write policies:
  a read-only learned profile, an explicit user-level rules file, and a
  workspace-level log plus curated notes.
- The important design choice: **some layers are written implicitly by the product,
  others only when the user explicitly asks**. Users can inspect and correct them.
- Daily logs are append-only; older logs get distilled into curated notes rather
  than deleted.

### 2.8 Approvals, hooks, and steering as product features
- Hooks allow the platform to inject or block behaviour at well-defined lifecycle
  points, and the assistant is expected to *treat hook feedback as user feedback*
  and adapt rather than argue.
- Slash commands and skill invocations are surfaced as first-class affordances.
- The user can interrupt and redirect mid-flight; the agent is expected to
  reconcile rather than restart.

### 2.9 Documentation-as-tool
- When asked how a platform feature works, the assistant is expected to consult
  the official docs rather than improvise. The product treats its own docs as an
  authoritative retrieval source.

### 2.10 Honest capability boundaries
- When a capability genuinely does not exist (e.g. video/3D generation), the
  product instructs the agent to decline directly instead of searching for a
  workaround. **Explicit non-capability is part of the capability surface.**

---

## 3. Harness-Layer Behaviours Worth Studying

| Behaviour | Description | Why it is interesting |
| :--- | :--- | :--- |
| **Delegation before exploration** | Broad open-ended search is delegated to a dedicated exploration path rather than done inline | Keeps the main thread's context clean; trades tokens for context hygiene |
| **Progressive disclosure of tools** | Tool schemas are loaded on demand from a deferred registry | Tool count stops being a context tax |
| **Precedent search before acting** | The agent searches prior conversations for a specific past decision before re-deriving it | Temporal memory as a first-class retrieval mode, distinct from semantic recall |
| **Deliverable-first framing** | Every completed task ends by surfacing the viewable artifact, plus a concise textual summary that stands alone | The user reads the summary; the artifact is the proof |
| **Result-presentation as a mandatory step** | Not "remember to show the file" but a required terminal action in the loop | Makes completion non-optional and observable |
| **Skill reflection after use** | After using a skill, the agent must evaluate whether the skill was outdated or incomplete and fix it in the same turn | Skills improve as a side effect of normal work |
| **Skill accumulation from work** | Multi-step or tricky work becomes a skill *without being asked* | The product gets better at the user's job over time |
| **Correction instead of reporting** | When the agent spots a defect in its own instructions, it fixes it rather than flagging it | Removes a class of "please fix your file" chores |
| **Organization warnings** | When skill sprawl or duplication is detected, raise it — but do not mass-refactor without consent | Surfaces entropy without acting unilaterally |
| **Context-budget awareness** | Avoid redundant verification reads when the needed context is already injected | Token thrift as an explicit behavioural rule |
| **No machinery exposure** | Internal setup steps are never narrated to the user | Keeps the surface feeling like a product, not a debug log |
| **Theme/responsiveness of generated UI** | Generated visuals must match the host theme and remain readable | Generated content is held to the host's design language |

---

## 4. Interaction Model (What Actually Feels Different)

1. **The default is competent, not maximal.** One expert, one active plan,
   connectors only when needed. Restraint is designed in.
2. **The user is the authority on destructive action.** The system is aggressive
   about *internal* actions (reading, organising, learning) and conservative about
   *external* ones (sending, deleting, publishing).
3. **Completion is defined by presentation.** Work is not done until the
   deliverable is surfaced and summarized.
4. **Memory is a dialogue.** The user can see what was remembered and why.
5. **Failure is explained in the user's terms.** Sandbox denials and permission
   prompts are phrased as next steps, with an explicit escalation path.
6. **Extensions are audited, not trusted.** Installing capability is treated with
   the same seriousness as installing software — because that is what it is.

---

## 5. What This Category Reveals About Alpha

Alpha's reference library has extensively studied *how to make agents more
capable*. WorkBuddy's category reveals the complementary question:
**how to make a capable agent trustworthy and legible to someone who did not
build it.**

The gap is not in engines. It is in:

- **Legibility** — can a user see what the agent is doing, why, and with what data?
- **Steerability** — can the user redirect mid-flight without restarting?
- **Reversibility** — can the user undo a bad action cheaply and confidently?
- **Extensibility ergonomics** — is adding a skill/connector safe and obvious?
- **Memory transparency** — is memory inspectable, correctable, and scoped?

These map directly onto proposals in
[Borrowed Ideas & Alpha Gap Map](./borrowed-ideas-and-gap-map.md).
