# Inspiration Notes — Product Craft & Feel

The gap map covers *features*. This document covers what the feature list cannot
express: **how WorkBuddy behaves**, and what Alpha should internalize as taste.

These are the lessons that survive translation into any architecture.

---

## 1. Restraint is a feature

WorkBuddy's defaults are deliberately narrow:

- **One expert per session**, not many.
- **Connectors only when the task needs them**, not a pre-connected everything.
- **Memory tiers with different write authority**, not one omnivorous store.
- **A capped recommendation batch**, not a catalogue dump.

Every one of these is a *reduction* in apparent capability that increases actual
usability. The instinct of a capability-rich system — and Alpha is a very
capability-rich system — is to expose everything at once. That instinct is wrong.

> **Alpha translation:** the deferred-tool pool (C1) and the expert exclusivity
> constraint (F2) are the same idea applied to Alpha's own surface. Restraint
> should be a first-class architectural value, not an afterthought.

---

## 2. The internal/external asymmetry

The reference product is **bold internally and conservative externally**:

| Bold (act now) | Conservative (ask first) |
| :--- | :--- |
| Reading files, searching, organising, learning | Sending messages, publishing, deleting, spending |
| Forming plans, delegating to subagents | Anything touching the user's personal directories |
| Writing to its own workspace memory | Anything irreversible or externally visible |

This is a genuinely useful heuristic, and it is *not* the same as a risk score.
It is a stance about whose authority is being spent.

> **Alpha translation:** A1/A3/A4 implement the conservative half. The bold half
> already exists in abundance. The missing piece is the *stance statement* — a
> written doctrine in `AGENTS.md` that makes the asymmetry explicit to every
> agent, every run.

---

## 3. Ambiguity means ask, not guess

For vague requests ("clean up my computer", "free up space"), the reference
product does not infer a target and start. It asks for the directory, the file
types, and the criteria — **before scanning anything**.

That last part is the subtle one: even the *scan* waits. The reasoning is that a
scan on the wrong target produces a misleading report, which is worse than no
report because it looks like progress.

> **Alpha translation:** the planner's scan-only mode (A2) should extend to
> *pre-scan clarification*. When a destructive-capable plan has an under-specified
> target, the clarification gate fires before Step 0, not after.

---

## 4. Completion is a presentation act

The most transferable single behaviour in the reference product:

> Work is not done when the operation succeeds. Work is done when the result is
> **surfaced** and **summarized in a way that stands alone**.

Two details matter:

1. **Presentation is mandatory, not remembered.** It is a required terminal step
   in the loop, not a cultural norm.
2. **The summary stands alone.** The user should be able to read the text and
   understand the outcome without opening the artifact. The artifact is proof,
   not communication.

> **Alpha translation:** E1 is the formal gate. But the cultural part matters
> more — Alpha's `evidence` and `council` engines verify that work is *correct*.
> They do not verify that it is *communicated*. Those are different failure modes
> and Alpha only guards one.

---

## 5. Failure messaging is product surface

The reference product's failure language has three properties:

- **Phrased as next steps**, not as diagnoses. ("This command needs approval" not
  "PermissionError: policy violation at layer 3".)
- **With an escalation path.** The user always knows what to do if the answer is no.
- **Without machinery.** The user never sees the internal setup sequence.

And there is a deliberate refusal mode: when a capability genuinely does not
exist, say so **immediately**, and explicitly do not go looking for a workaround.
The cost of a confident non-answer is lower than the cost of a slow wrong one.

> **Alpha translation:** F4 and A5. Note the inversion — a system as capable as
> Alpha fails *more* often by over-promising than by under-delivering. The
> capability-boundary registry is therefore a quality feature, not a limitation.

---

## 6. Skills are a habit, not a configuration

The reference product treats skill creation as something that *happens as a side
effect of working*:

- Accumulate after multi-step or tricky work, without being asked.
- Fix the skill you just used if it was wrong, in the same turn.
- Warn about sprawl, but never clean up unilaterally.

Notice the shape: **write aggressively into your own corpus, be conservative
about deleting from it.** That is the opposite of how most systems treat their
own configuration.

> **Alpha translation:** B2 (accumulation trigger) and B3 (reflection after use)
> are the mechanics. The **write-boldly / delete-cautiously** asymmetry is the
> design principle worth writing down, because it should also govern Alpha's
> memory tiering (C4) and its RSI instruction-corpus maintenance (E2).

---

## 7. Trust boundaries are explicit and visible

Connector trust, skill install audit, and destructive-op confirmation all share
one property: **the user makes the decision, and the decision is cheap to make
because the system pre-computed what the decision means.**

The skill audit is the clearest example. It does not present a yes/no dialogue.
It presents a *report* — enumerated files, findings, severities — and the report
becomes the thing the user approves. The user is not asked to estimate risk.
They are asked to read a prepared briefing.

> **Alpha translation:** every gate Alpha adds (A1, B1, B5) should produce a
> briefing artifact, not a dialog. This also makes the approvals auditable, which
> connects to F3.

---

## 8. Memory is a dialogue, not a black box

Memory in the reference product is split by both **scope** and **write
authority**. The user-visible consequence is that a user can tell the difference
between "the system learned this" and "I told it this" — and can edit the second
without fighting the first.

The deeper point: **an un-inspectable memory system is a liability, not an asset.**
It makes the agent's behaviour non-reproducible from the user's point of view, so
the user cannot form a correct mental model, so they cannot trust it.

> **Alpha translation:** C4 and C5. Alpha's L0–L8 plane is more sophisticated
> than anything in the reference product, but sophistication without provenance is
> indistinguishable from noise. Write authority + provenance + forget is what
> converts a memory system into a trustworthy one.

---

## 9. Hook feedback is treated as user feedback

When a hook blocks an action, the reference product's instruction is unambiguous:
**first try to comply by adjusting your approach.** Only if you cannot should you
surface it to the user — and even then, as a request to check configuration, not
as a complaint about being blocked.

This eliminates an entire failure mode: the agent that treats its own guardrails
as obstacles to argue with. Two agents with identical capabilities diverge
sharply in user trust based purely on this behaviour.

> **Alpha translation:** D2. Alpha has many gates (`guardrails`, `policy`,
> `authz`, `verify_command_approval`) and therefore many opportunities to
> generate this failure. A block-reason taxonomy with a mandated response per
> class is cheap and closes it.

---

## 10. Docs are a first-class tool

The reference product resolves questions about itself by **reading its own
documentation**. This has three effects:

1. Answers are accurate instead of plausible.
2. The docs get pressure-tested constantly, so they stay current.
3. Configuration questions — the highest-risk class of hallucination in a system
   with a very large config schema — are grounded in a versioned artifact.

> **Alpha translation:** C6 is unusually high-value for Alpha specifically.
> `config.example.yaml` is 151 KB. Any agent answering "how do I enable X?"
> from memory is guessing. Indexing Alpha's own docs as *privileged, versioned
> retrieval* is a small change with an outsized correctness payoff.

---

## 11. Scope discipline in memory and identity

Two scope rules from the reference product worth copying verbatim:

- **Project-tied facts go in project memory; cross-project facts go in user
  memory.** Never conflate the two.
- **Session-scoped choices (which expert is active) are separate from installed
  state (which experts exist).**

The failure they prevent is subtle: an agent that writes a project-specific
preference into global memory pollutes every future project, and the user has no
way to see it happen.

> **Alpha translation:** C4's write-authority policy should include a scope
> dimension: every memory record is tagged with its scope, and the store rejects
> writes that violate scope rules rather than silently accepting them.

---

## 12. Not narrating the machinery

The reference product never tells the user "now let me load the diagram module"
or "let me search the marketplace for a skill". It uses natural preambles that
describe *intent*, and the machinery stays invisible.

This is not cosmetic. Narrating machinery:
- makes the product feel fragile and provisional,
- implies the user should evaluate the *process* rather than the *result*,
- and leaks implementation details that will change.

> **Alpha translation:** this belongs in `AGENTS.md` as an explicit behavioural
> rule for every surface — including the Gateway's streamed step events, where
> Alpha currently exposes engine names freely. Engine names are for operators in
> `ops/` and `console/`; they should not appear in the user's chat stream.

---

## The Distilled Principle

If these notes compress to one idea:

> **Alpha optimizes for what the agent can do. WorkBuddy optimizes for what the
> human can verify. Both are required, and Alpha is currently imbalanced.**

Every proposal in the gap map is, in the end, an instance of one of five verbs:

| Verb | Meaning | Where it appears |
| :--- | :--- | :--- |
| **Reveal** | Make existing capability visible | F3, C5, E1 |
| **Gate** | Require a decision at a real boundary | A1, A3, B1, B5 |
| **Scope** | Constrain where a thing applies | C4, F2, A2 |
| **Adapt** | Respond to a block by changing approach | D2, F4 |
| **Present** | Communicate the result so it stands alone | E1, F3, F4 |

None of these add a new engine. All of them raise the ceiling on what Alpha's
existing 89 engines are actually worth to a human.
