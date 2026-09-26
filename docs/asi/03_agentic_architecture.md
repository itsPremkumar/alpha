# Agentic Architecture Patterns That Work

Collected 2026-09-26 from frontier lab architecture writing, evaluation research, and the two most
substantial open agent systems (OpenClaw 2026.9.6, Hermes Agent v0.21.5). Each pattern states the problem,
the mechanism, and the failure mode when it is skipped.

## 1. The trust boundary is architectural, not procedural

**The single most important pattern.** OpenClaw's enterprise evaluation states it plainly:

> "A single trust envelope can put the agent loop, channel connections, credentials, and shell under one OS
> user. Wrapping that entire application in a VM isolates it from the host, but does not separate those
> components from each other."

And, comparing against Hermes:

> "The only security boundary against an adversarial LLM is the operating system." — Hermes `SECURITY.md`

OpenClaw's counter-architecture: **a trusted Gateway, untrusted and movable execution, policy enforced in
code, versioned state.** Execution moves to a sandbox, a node, or a disposable cloud machine **without
standing Gateway credentials**, and scoped worker credentials have a **separate lifecycle**.

The seven properties an enterprise harness must *prove* (not merely list):

1. **Separated trust boundary** — execution without standing credentials
2. **Policy is code** — denial structural, not a prompt request; approvals fail closed
3. **Authenticated access, bounded roles** — inbound default-deny; and *state which boundaries are security
   and which are convenience*
4. **Secrets have owners** — isolatable credential failures degrade their own owner; ingress-auth and
   invalid-config failures **stop startup**
5. **Versioned state, guarded upgrades** — schema-versioned, owned migrations, immutable signed releases
6. **Recorded provenance** — recorded facts, explicit retention, **documented deletion limits**
7. **Independent stewardship** — signed releases, public security record

**Failure mode when skipped:** a policy control described as a boundary. Alpha's
`bots/authority_ceiling.py` is a *policy* control inside a single envelope; the thing it constrains can
reach around it. That misrepresentation is the failure this pattern exists to prevent.

## 2. Credential isolation — the agent never sees the secret

OpenClaw's property 4, plus Hermes v0.21.2's password-blind vault: the agent signs in, pays, and fills
addresses from 1Password / Bitwarden / a local vault **without ever seeing a secret**; 2FA comes from a
stored authenticator key or the user's UI.

Retrieval returns an **opaque handle**. The real credential is injected at the point of use, outside the
model's address space.

**Why this beats sandboxing as the primary defence:** sandboxing constrains what compromised code can
*reach*. Credential isolation means the secret is not reachable in the first place, regardless of what the
code does. They compose, but the second is strictly stronger for the exfiltration case.

**Failure modes:** secrets in logs; secrets in exception messages; secrets in truncated tool results. The
last two are where they actually leak — audit error paths first.

OpenClaw's related hardening, worth copying: the `HERMES_*` **prefix** passthrough was removed because it
leaked non-secret configuration into arbitrary sandboxed code. Use an **exact-name allowlist** plus explicit
per-skill opt-in.

## 3. Degrade the subsystem, never fail closed on a narrow fault

Learned from OpenClaw's 44-issue `state.db` reliability campaign. Three generalisations:

**A. Scope damage classification to the damaged component.** An FTS-index error was classified as whole-file
corruption and fail-closed the entire conversation. Correct behaviour: search degrades, the index rebuilds
later, the transcript store is untouched. *Find every place alpha escalates a narrow failure to a total one.*

**B. One bad record must not kill a listing.** A single malformed row crashed sessions list, export, and
insights. Correct: coerce per record on **every reader**, render the bad value as an explicit placeholder,
and **warn naming the record**. Then test with a deliberately corrupt row.

**C. Name the actual damage class.** "FTS write corruption" when the index is merely stale sends the
operator to the wrong repair.

Two more from the same campaign:

- **A write lock must not be taken when nothing is written.** Opening the store stalled 4-20s. A read-only
  path opening a write handle is a defect.
- **`doctor --fix` must refuse an action it cannot prove is safe**, and must not half-apply.
- **Repair must not lose capability.** OpenClaw PR #136045: a doctor repair was dropping the bundled plugin
  inventory so default plugins vanished after restart. A repair that drops a capability is a failed repair.
  Recovery must also work from a *partial* state left by a prior bad version.

## 4. Provenance and verification evidence

OpenClaw publishes, per release: `sha256` per asset, a release manifest, post-publish evidence, dependency
evidence, and separate CI lanes. And then the part no other project does — a **lane report naming each
check, its real status, and who waived what**:

> "Operator lane waiver approved by Peter (2026-09-23, 'release this now'): non-proof CI, plugin and
> release-check lane failures are advisory…"
> waived lanes: `checks-node-agentic-control-plane-agent-chat (failure)`;
> `checks-node-bundle-infra-small-runtime-2 (failure)`; `ci-gate (failure)`

**Never collapse a failed lane into an aggregate pass.** A system that can be quietly bypassed is worse
than none, because it converts unknown state into false confidence.

Related CI patterns worth adopting:

- **Shrink-only ratchets** — budgets that can only tighten, never loosen
- **CI import guards** — plugin/module boundaries enforced by CI, *"not convention"*
- **Scope gates and changed-scope lane routing** — only run what changed
- **Durable run ledger** — every update/upgrade writes reports and artifacts

Provenance also needs **documented deletion limits**. OpenClaw is careful here: forgettable memories are
tracked and purgeable, but original transcripts, untracked writes, and external copies are separate and
out of scope. A deletion claim without its limits is a false claim.

## 5. Learned orchestration, not hand-designed topology

See [`02_learned_orchestration.md`](02_learned_orchestration.md) in full. The transferable mechanisms:

- **Decision-only parametrisation** — route on logits, skip autoregressive decoding
- **Soft performance targets** — softmax over measured per-worker reward, not a hard argmax label
- **Evolutionary optimisation of the router** on end-to-end trajectories when step labels are unavailable
- **Intra-workflow agent isolation** to prevent orchestration collapse
- **Shared memory across workflow boundaries** to prevent redundant rediscovery

**Failure mode:** hand-designed collaboration patterns with fixed interaction structure. Handled well by
Fugu's own account: most prior multi-agent systems *"orchestrate multiple agents across successive
discussion or reasoning rounds, using fixed interaction structures."*

## 6. Taint propagation on the turn

Track that content from an untrusted source is untrusted — and propagate through derived work.

**OpenClaw's admitted gap:** *"Turn taint covers network-sourced tool output; text arriving through
non-network tools does not taint the turn."* That is a real hole.

The complete design: taint is a property of the **turn**, not the tool call. Sources include web fetch, file
read, memory recall, skill output, another agent's message, a database row, an MCP response. A summary of
tainted content is tainted; a tool result computed from tainted input is tainted; a subagent spawned on a
tainted turn inherits taint unless explicitly isolated.

**The point of taint is authority bounding:** a tainted turn cannot satisfy an approval gate, cannot pass a
policy gate without human review, and cannot be recorded as verified evidence. Without that, taint is
bookkeeping. And taint must be clearable only by a control that is not the model.

## 7. Verification must be structural, and honesty is enforced by gates

**Hermes `/goal` quality gates, run BEFORE the judge:**

> "Gates run before the judge. If any gate fails, the judge is not called — a red gate is deterministic
> evidence the goal isn't done. The gate's exit code and output tail (last ~3 KB) become the continuation
> prompt."

Plus: re-run every boundary (never replay a stale result), 3 retries, 5-minute timeout, auto-pause on
exhaustion, persisted with the goal.

This is the best single idea in either codebase. A model asserting completion is an opinion; a red shell
gate is evidence.

**Judge design, equally important:**

- The judge returns strict one-line JSON: `{"verdict": "done"|"blocked"|"continue"|"wait", "reason": "..."}`
- **Deliberately conservative** — `done` only with concrete evidence (command result, file excerpt, test
  output), never "looks done"
- `blocked` for genuinely unachievable goals — an impossible task pauses, it is never waved through
- **Fail open on judge error** → `continue`. A broken judge must not wedge progress *and* must not fake
  completion. The turn budget is the real backstop
- A `wait` verdict **parks** the loop on a background process instead of burning turns

**Self-improvement loop, made reachable:** `/learn` distils a skill from a directory, a URL, the workflow
just performed, or pasted notes. Large sources become **knowledge-base skills** — a lean `SKILL.md` with an
index plus one distilled file per chapter under `references/`, so query cost is proportional to the answer
rather than the source. Re-running on the same topic **folds into the existing skill** rather than
duplicating.

**Staged write approval** — the pattern alpha lacks. When enabled, every skill and memory write is
*staged*, not committed, with a list/diff/approve/reject surface, surviving restarts. The critical subtlety:
staging applies **regardless of whether the write came from a foreground turn or the background review**,
because a `SKILL.md` is too large to review inline. A gate that only catches background writes is not a
gate. And the reviewer is never the author.

## 8. Ambient presence, bounded rooms, and loop protection

- **Ambient room events** — a group room provides quiet context *unless the agent sends with the message
  tool*. The room listens; it does not speak. Anti-spam by architecture.
- **Presence that never leaks** — *"drafts stay ephemeral and never reach the model or the transcript."*
  Typing indicators must not enter model context.
- **Bot loop protection** with defaults plus per-channel overrides. Hermes shipped
  `bots_require_mention` specifically because bot-to-bot mention loops were happening. A dispatch-level loop
  guard is insufficient if the loop is in the **mention graph**.
- **Broadcast groups** — bounded agent group threads, explicitly bounded.
- **Progress drafts** — one visible WIP message that updates, rather than a flood.

## 9. Shutdown that settles, and resource lifecycle

- **Settle shutdown work before reporting completion.** In-flight work is drained or explicitly abandoned
  **with a record**, and exit status reflects what happened.
- **Close failed streams.** A dropped HTTP/response stream must be closed; a leak is a stuck run reporting
  nothing.
- **Bound expensive reads and history queries** — an unbounded read is a DoS on yourself.
- **Host-wide singleton with a rendezvous record** — a second process *attaches*, it does not spawn a
  duplicate. OpenClaw fixed this after profile switches and roster ticks spawned duplicate primaries.
- **A read-only UI inspection must not spawn a worker.** Hovering a bots roster row spawned a backend per
  row in Hermes.
- **A blocked slow callback must not starve a lease refresh.** One blocked periodic callback stalled lease
  refresh.
- **Per-entity stop/start/restart**, plus a standalone mode.

## 10. Honest self-documentation as a product feature

OpenClaw's "What we do not claim" is the best security writing in any agent project:

- Sandboxing and exec approvals are **off by default**
- One gateway is one trust domain; multi-tenancy means one **cell** per tenant, fleet still experimental
- **Native plugins run in-process and are not sandboxed**
- Egress allowlisting covers cooperating traffic only — *"the sentinel design assumes bypass instead of
  trying to prevent it"*
- Promoted memories have no time-based retention bound
- ClawHub: *"a pending or stale scan can allow installation with a warning; installation is not proof that
  every scan completed"*

Anthropic footnotes its own flattering numbers (*"8× lines of code is almost certainly an
overstatement"*). Sakana notes which scores are provider-reported.

**This is the standard.** A project that enumerates its own holes is more trustworthy than one that does
not, and the enumeration is the artefact that tells an operator where not to point the thing.

## Sources

Full URLs and retrieval dates in [`SOURCES.md`](SOURCES.md).
