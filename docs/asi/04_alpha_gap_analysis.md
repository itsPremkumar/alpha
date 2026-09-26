# ASI / RSI Gap Analysis: alpha against the frontier

Written 2026-09-26. Assessed against [`01_state_of_evidence.md`](01_state_of_evidence.md),
[`02_learned_orchestration.md`](02_learned_orchestration.md) and
[`03_agentic_architecture.md`](03_agentic_architecture.md).

**Method note.** Every alpha claim below is a file I read or a test I ran, not a module name I assume is
reachable. Where I could not establish reachability, the verdict is **UNVERIFIED** and I say so. This
matters: the single most common defect in this codebase is a module that exists and nothing calls it.

---

## Part 1 — The finding that should reframe the roadmap

**alpha's recurring defect is not a missing feature. It is asserting a state it never measured.**

Confirmed instances, each found by a different agent, each verified:

| Where | Asserted | Actually measured |
|---|---|---|
| `scripts/doctor.py` | `Status: Ready` | product could not start a run |
| `subagents/executor.py` | `subagent_status: completed`, `Task Succeeded (capped: turn budget)` | assistant message still carried `tool_calls` when the ceiling fired |
| `groups/runner.py` | `status="succeeded"` + a `phase:"synthesis"` receipt | `synthesis=""` — nothing was produced |
| `research/engine.py` | `citation_status="verified"` | page was never fetched, or was injection-quarantined |
| `research/engine.py` | `reliable: true, category: "academic"` | `arxiv.org.evil.test` via bare `endswith` |
| `subagents/executor.py:764-765` | capability present | missing allowlist names dropped **silently** |
| `subagent_limit_middleware.py:145` | documented cap on delegation | `prior_delegation_count` is permanently `0` |
| Buzz relay loop | healthy | no done-callback; dead while advertising health |

This matters more than any feature list because it is the *same* failure the ASI literature is converging
on from the other end. METR's own result on developer productivity estimates (arXiv 2507.09089) is that
self-reported uplift is systematically **overestimated**. Anthropic footnotes its own 8× figure as *"almost
certainly an overstatement."* Sakana flags which scores are provider-reported.

**The pattern is industry-wide, and alpha has it in eight places.** A system whose components routinely
report unmeasured success cannot be trusted with an autonomous loop, because the loop's only feedback
signal is the component's own report. That is the ASI problem in miniature, and it is fixable here at a
fraction of the cost of the frontier work.

**Verdict: this is alpha's highest-value work, and it is Wave 0.**

---

## Part 2 — Measured against the seven enterprise properties

Using OpenClaw's rubric ([`03_agentic_architecture.md`](03_agentic_architecture.md) §1).

| # | Property | alpha | Evidence |
|---|---|---|---|
| 1 | Separated trust boundary | **FAIL** | Agent loop, gateway, channels, credentials and shell share one process envelope. `bots/authority_ceiling.py` is a *policy* control inside it. |
| 2 | Policy is code | **PARTIAL** | `authz/tool_filter.py`, `guardrails/middleware.py`, `safety/authority/*`, approval gates exist and mostly fail closed. But `system_one_policy` only *selects* an operation; it never denies. |
| 3 | Authenticated access, bounded roles | **PARTIAL** | `system_role: Literal["admin","user"]`, `require_admin_user`, per-channel allowlists. **No published security-vs-convenience boundary statement.** |
| 4 | Secrets have owners | **PARTIAL** | `.env` separation, `mcp/untrusted.py`, peer-network trust audit. **No opaque-handle credential vault** — the model can still read secrets. No documented startup refusal on invalid config. |
| 5 | Versioned state, guarded upgrades | **PARTIAL** | `persistence/storekit/locking.py`, Alembic migrations, `evolution/update_policy.py` with a kill switch. State schema versioning not verified. |
| 6 | Recorded provenance | **PARTIAL** | `bots/governance_ledger.py` records **events**. No **decision receipts** (who decided, under which policy, what alternatives rejected). No documented deletion limits. |
| 7 | Independent stewardship | N/A | Single-maintainer project. Not applicable, and not worth pretending to. |

**Property 1 is the root.** Properties 2–6 are all weakened by it: a policy control inside a single
envelope is a speed bump, not a wall. No amount of property-2 work changes that.

**Property 3's missing half is cheap and high value.** OpenClaw's most useful security contribution is
simply *stating which boundaries are security and which are convenience*. alpha has no such statement, so a
reader cannot tell which controls to rely on.

---

## Part 3 — Dead and unwired components

Hunting these is the cheapest high-value work available: a large module nothing calls is usually a
three-line fix behind several hundred lines of working code.

| Component | Status | Cost to wire |
|---|---|---|
| `swarm/cnp_auction.py` — `ContractNetAuctionEngine` | referenced **only by its own test** | low — it works, nothing calls it |
| `bots/capability_dispatch.py` (761 ln) | reachable, but selection is by department/name | medium — needs the learned router (below) |
| `capability_tags` | consulted **only for leader election** | low |
| `match_bot_for_task` | reachable only from a **manual endpoint** | low |
| `groups/quorum.py` — `QuorumEngine` | **dead** | low |
| `channels/debounce/debouncer.py` | **unreachable** | low |
| `agents/middlewares/group_chat.py` | **does not exist** | n/a — a missing file, not dead code |
| `agent_eye_search` / `agent_eye_sources` | referenced by `subagents/categories.py:159,166,167`; **zero occurrences** in `tools.py` | low — but see below |
| `subagents/executor.py:764-765` | drops missing allowlist names **silently** | trivial, and it is the *reason* the row above is invisible |
| `start_subagent` / `complete_subagent` | **zero production callers**; `POST /api/subagents/control/spawn` creates records no worker dispatches, and reports `status="success"` | medium |
| `DelegationEntry` | nothing writes ledger entries → `prior_delegation_count` always `0` → **the documented delegation cap does not exist** | low — the producer belongs in `tools/builtins/task_tool.py` |
| `handle_parent_failure` / `list_orphans` | **uncalled** — no parent-failure detection | medium |
| `subagents/yield_handoff.py`, `subagents/messaging.py` | **zero importers** | unknown |
| 3 `GroupRun` fields | published but **never populated** | low |
| Buzz relay loop | no done-callback; **advertises healthy while dead** | low |
| `ExtensionManifest` | exists in **0 Python files** | n/a — a phantom type |

**The `agent_eye_*` case is the instructive one.** A category in `subagents/categories.py` promises two
capabilities. The tool registry has never heard of them. The executor discards unrecognised allowlist
names without a word. So a user selecting that category gets a silently degraded agent and no signal. Three
defects compounding: an unwired capability, a silent drop, and no test that enumerates promised
capabilities.

**Recommended structural fix:** a test that walks every category/tool manifest and asserts each named
capability resolves to a registered tool. That single test would have caught this, and would catch the next
one.

---

## Part 4 — Blockers: alpha cannot complete a run

### 4.1 A 13-hop circular import (P0, blocks test collection)

Fully diagnosed 2026-09-26:

```
alpha/models/__init__.py:1            from .cost_governor import ...
  → cost_governor.py:37               from alpha.projects.approval_queue import ...
    → projects/__init__.py:3          from alpha.projects import (auction_engine, ...)
      → projects/auction_engine.py:17 from alpha.bots.registry import ...
        → bots/__init__.py:38         from alpha.bots.capability_dispatch import (...)
          → capability_dispatch.py:75 from alpha.swarm.cnp_auction import ...
            → swarm/__init__.py:9     from alpha.swarm.aggregator import ...
              → aggregator.py:10      from alpha.runtime.runs.verification import ...
                → runtime/__init__.py:10
                  → runs/__init__.py:6  from .worker import ...
                    → runs/worker.py:57  from alpha.runtime.goal import (...)
                      → runtime/goal.py:25  from alpha.models import create_chat_model
ImportError: cannot import name 'create_chat_model' from partially initialized module 'alpha.models'
```

**Observed impact, measured:** `import alpha.tools.tools` succeeds (130 tools) because that path enters the
package in a working order. `from alpha.community.aio_sandbox import network_proxy` **fails**. So the package
is order-dependent, and any test importing a module that reaches `alpha.models` first cannot even be
collected. This is not theoretical — it is currently blocking `test_aio_sandbox_network_proxy.py`.

**Shape of the fix:** module-level `from alpha.models import create_chat_model` in
`alpha/runtime/goal.py:25` (and any sibling site) must become a deferred, function-local import. `alpha.models`
should not eagerly import the world. A test that imports **every** module in isolation, in a fresh
interpreter, would have caught this permanently — that is the highest-value single test in this document.

### 4.2 Other blockers

- **`max_turns` was not a turn ceiling** — fixed in `executor.py` (LangGraph `recursion_limit` counts
  super-steps; the middleware burns ~7.14 per model turn, so 150 turns bought 21). *Landed? No — batch
  pending.*
- **Capped child reported as success** — fixed in `executor.py`; now `FAILED` with a reason. *Pending.*
- **Delegation cap structurally dead** — see Part 3.
- **No depth counter on the execution path** — nesting blocked only by the `task`-in-denylist convention.
- **Batch items have no checkpoint** — needs a new Alembic revision; half-implementing breaks Gateway startup.
- **`test_client.py` encoding corruption** — 183 tests pass, so the damage is comment-level, but the file
  is double-encoded and needs a clean restoration.

### 4.3 An unresolved verification discrepancy

The group-chat batch reports 99 passed. Measured: `test_group_conversation_execution.py` is **14 passed in
isolation** and **7 failures when run alongside its two sibling new test files**. That is test pollution. A
suite that passes alone and fails beside its siblings converts a real defect into a green check, and it is
currently unexplained. **Not landed, and should not be until understood.**

---

## Part 5 — Where alpha is genuinely ahead

Stated explicitly, because a gap analysis that only lists deficits produces bad roadmaps.

| Capability | alpha | Nearest comparator |
|---|---|---|
| Swarm DAG execution | `swarm/` with coordinator, worker, consensus, aggregator | Hermes delegates to child agents; no DAG |
| Group deliberation | `groups/` (799-line runner) + `deliberation/` (debate, adversary, council, MoA, verifier) | Hermes has council/roles; no comparable debate stack |
| **Authority ceiling** | `bots/authority_ceiling.py` (553 ln) + `autonomy_guard.py` + `kill_switch.py` | **Neither Hermes nor OpenClaw has an equivalent for self-created agents** |
| Self-repair | `runtime/sentinel/` OBSERVE→DIAGNOSE→FIX→VERIFY→COMMIT | Hermes has `/refine`; not comparable |
| Memory depth | ~20 subsystems: entities, fabric, social, affective, cognitive, consolidation, narrative, prospective, scenarios, utility, fusion, health, policy, codebase | Hermes: `MEMORY.md` + provider plugins. OpenClaw: tiers + dreaming |
| Dreaming | `memory/dreaming/phases.py` — light/deep/REM | OpenClaw has the same phases. **Parity.** |
| Specialist subagents | 10 in `subagents/builtins/` | Fugu orchestrates a *model* pool, not persistent specialists |
| Governance | `governance_ledger.py`, `quality_gate.py`, `governance/council/` | Comparable; receipts are the gap |
| Multi-agent memory | `bots/teammate_mesh.py` | No equivalent found |

**The strategic read:** alpha has the expensive half of an orchestration platform and is missing the
learned allocation policy on top. Fugu's result — an orchestrator beating every model in its own pool —
requires a worker pool, per-worker measurement, a router, and shared memory. alpha has three of four. The
fourth is the gap.

---

## Part 6 — Prioritised recommendations

### Wave 0 — Unblock and establish truth (do this first; not a prompt)

1. **Break the circular import.** Defer `from alpha.models import ...` at module scope. Add a test that
   imports every module in a fresh interpreter. *Unblocks test collection across the repo.*
2. **Land the pending verified batches** (subagents: turn-budget + capped-cutoff-as-failure).
3. **Explain the group-chat test pollution.**
4. **Close the delegation cap** — write the `DelegationEntry` producer in `tools/builtins/task_tool.py`.
5. **Add the promised-capability test** from Part 3.
6. **Make `doctor.py` unable to claim unmeasured readiness.**

### Wave 1 — Make the system refuse to lie (highest value)

Port the anti-false-success machinery, in this order:

1. **Deterministic gates before any completion claim.** A red gate means the assertion is not evaluated.
   This is the single highest-value change in the entire dossier — it is the generalisation of every
   individual bug in Part 1.
2. **Scoped policy overlays** over the single global ceiling; a scope may only ever be *stricter*.
3. **Turn taint** with authority bounding: a tainted turn cannot satisfy an approval gate nor be recorded
   as verified evidence.
4. **Decision receipts** — actor identity (human / agent / child / automated, never collapsed), policy
   rule, effective scope, alternatives rejected with reasons, tamper-evident.
5. **The security-vs-convenience boundary statement.**

### Wave 2 — Learned orchestration (where the headroom is)

1. **Intra-workflow agent isolation.** alpha's group and swarm fan-out almost certainly suffers
   orchestration collapse — the first agent's trajectory constrains every sibling. This is a present-day
   quality bug, not a future feature.
2. **Wire the dead dispatch components.** `ContractNetAuctionEngine`, `capability_tags`,
   `match_bot_for_task` — they work; nothing calls them.
3. **Soft performance targets from measured outcomes.** alpha has `evaluation/`, `benchmarks/` and
   `autonomous_benchmark_harness.py`. Feed measured per-agent performance into dispatch as a soft target
   rather than a hard argmax.
4. **Temporal routing.** Fugu learned *when* in a task to switch specialists, not just which. alpha's swarm
   has no notion of this.

### Wave 3 — The trust boundary (requires a decision, not just code)

Property 1 cannot be closed in-process. It needs a decision about separate OS identities or a sandbox, and
it should be recorded as a **known, stated limitation** until then — which is what OpenClaw does. Do not
describe `authority_ceiling.py` as a boundary in any doc, comment, or report. It is a policy control.

---

## Part 7 — What I did not verify

- Whether `persistence/storekit` state is schema-versioned with owned migrations (property 5, unverified).
- Whether any alpha test currently enumerates promised capabilities (I believe not; I did not prove it).
- The full contents of the ~290 dirty files. Several are from agents killed mid-write; their reports were
  never delivered and their work is **unverified**, not verified-and-fine.
- Whether `rsi/`, `evolution/`, and `selfrepair/` reach any real runtime path. `runtime/sentinel/` was
  audited previously; the other two were not.
- The `channels/mentions.py:26` `SyntaxWarning: invalid escape sequence '\-'` indicates a non-raw regex
  string. Cosmetic today, a real bug the moment that pattern is used for matching. Not investigated.
