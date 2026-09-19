# Borrowed Ideas & Alpha Gap Map

Feature-by-feature comparison of **WorkBuddy / CodeBuddy** product behaviour against
Alpha's current surface, ending in a concrete proposal for every row that has a
real gap.

**Legend**

| Tag | Meaning |
| :--- | :--- |
| `ALREADY HAVE` | Alpha covers this at or above the reference level — no action |
| `PARTIAL` | Alpha has the engine but lacks the product surface, policy, or default |
| `NEW` | Alpha has no equivalent |
| `[TIER-1]` | High leverage, low risk, mostly additive |
| `[TIER-2]` | High leverage, requires new engine or cross-cutting change |
| `[TIER-3]` | Strategic / speculative, justified but not urgent |

---

## Phase A — Product Trust & Safety Surface

### A1. Filesystem destructiveness policy
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Destructive operations on personal directories are governed by a fixed, written policy: no-go zones, scan-is-read-only, ambiguous-means-ask, mandatory warning + explicit file enumeration + confirmation, backup before mutation, trash not delete, ≤10 files per batch, no non-ASCII script files on Windows |
| **Alpha today** | `safety` (Estop), `guardrails` (input/output filters), `policy` (tool allow/deny), `security.verify_command_approval` for shell risk. **`PARTIAL`** — the general risk machinery exists but there is no *personal-data* doctrine and no trash/backup discipline |
| **Proposal** | `safety/personal_data_guard.py` → **`guard_personal_data_operation`** tool `[TIER-1]`. A declarative `PersonalDataPolicy` (protected roots, trash adapter per OS, backup-before-mutate, batch ceiling, path-encoding rule) consulted by `write_file`, `str_replace`, `hashline_edit`, and the shell executor. Emits a structured *operation dossier* (target list, sizes, risk class) that the approval gate renders. |

### A2. Read-only-first scan mode
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Even when the user asks to "clean up", pass one is always scan-only and produces a report, with an explicit statement that nothing will be acted on without confirmation |
| **Alpha today** | `sandbox`, `workspace_changes` track diffs, but there is no concept of a *non-mutating first pass* as a required phase. **`NEW`** |
| **Proposal** | Add a `scan_only` execution mode to the `planning` engine `[TIER-1]`: when a plan is classified as destructive-capable, the planner is *forced* to emit a Plan-Step-0 that is read-only and terminates in a user-visible report artifact. The goal engine refuses to advance past Step 0 without an explicit approval event. |

### A3. Trash-native deletion
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Never hard-delete: macOS `osascript`/`trash`, Windows Recycle Bin API, Linux `gio trash`/`trash-put`. If no trash is available, warn and require a second confirmation |
| **Alpha today** | **`NEW`** — deletions go through the shell/file tools with no recovery layer |
| **Proposal** | `safety/reversible_delete.py` with an OS-adaptive `TrashProvider` interface plus a fallback *quarantine vault* (move to an encrypted, timestamped holding area with a TTL and a restore tool). Pairs with the existing `checkpoints` router for a unified "undo" story. `[TIER-1]` |

### A4. Reversibility contract
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | The system is designed so that a bad action is cheap to undo, and the user is told where the backup/undo lives before it is needed |
| **Alpha today** | Git shadow checkpoints + rollback API, `boulder_checkpoint_manage`, `state` snapshots, `manage_durable_orchestration`. **`PARTIAL`** — strong machinery, no unified user-facing "undo last action" concept, and no coverage for non-git, non-code artifacts |
| **Proposal** | `recovery/unified_undo_stack.py` `[TIER-2]`: a single append-only undo ledger that every mutating tool writes to (filesystem, git ref, DB row, external API call where feasible), exposed as `POST /api/undo/last`, `GET /api/undo/stack`, and a UI surface. Makes the existing checkpoint engines *legible* rather than merely *available*. |

### A5. Capability honesty / explicit non-capabilities
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | The product maintains an explicit list of things it does not do, and instructs the agent to decline directly rather than search for a workaround |
| **Alpha today** | **`NEW`** — Alpha's breadth means it will often *attempt* rather than decline |
| **Proposal** | `policy/capability_boundary_registry.py` `[TIER-1]`: a machine-readable registry of known non-capabilities and degraded capabilities, consumed by the planner (pre-flight refusal), the tool selector (avoid pointless tool searches), and the UI (honest empty states). Include per-provider degradations, e.g. a model without vision being asked to read a screenshot. |

---

## Phase B — Extensibility Ergonomics

### B1. Skill install-time security audit
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Installing a skill is treated as installing software: audit `SKILL.md` *and* every bundled file (scripts, references, assets), produce a report, warn hard on critical findings, warn on medium, proceed on clean — before completing the install |
| **Alpha today** | `skills` engine with curator lifecycle and quarantine, `skill-reviewer` skill, `review_skill_package` tool, `contracts/skill_review/`, CI waivers. **`PARTIAL`** — the *review* capability is arguably stronger than the reference, but it is not gated into the *install* path |
| **Proposal** | Wire `review_skill_package` into the install transaction as a mandatory stage `[TIER-1]`. Add a `severity_tiers` contract (blocker / warn / informational) with explicit confirmation gates, and make the audit report the artifact the user approves — not a yes/no dialog. |

### B2. Skill accumulation without being asked
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | After a multi-step or tricky piece of work, saving the approach as a reusable skill is mandatory, not offered. Skipping is only allowed when the workflow is one-off, sensitive, or already covered |
| **Alpha today** | `learning`, `skills` (forge), `forge_skill_from_trace`, `propose_skill_tool`, `skill-creator`. **`PARTIAL`** — the *forge* exists but nothing in the runtime *demands* it at the right moment |
| **Proposal** | `learning/skill_accumulation_trigger.py` `[TIER-1]`: a post-run hook that scores a completed trajectory on (step count, error recovery, novelty vs. existing skill corpus) and, above threshold, *requires* either a forged skill or a written justification for skipping. Store justifications so the corpus learns what "not worth a skill" looks like. |

### B3. Skill reflection after use
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | After using a skill, evaluate whether it was outdated, wrong, ambiguous, inefficient, or missing a prerequisite — and fix it in the same turn rather than reporting it |
| **Alpha today** | **`NEW`** — no post-invocation feedback loop into the skill corpus |
| **Proposal** | Extend the `skills` engine with a `SkillUseRecord` (skill id, version, friction signals: retries, tool errors, clarifications requested) and a `skill_self_correction` step that proposes a patch, gated by the same review path as B1. `[TIER-2]` |

### B4. Skill corpus hygiene
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | When duplication, confusing naming, unclear responsibility boundaries, or overlapping/conflicting skills are detected, *raise it* — but never mass-refactor or delete without explicit consent |
| **Alpha today** | Curator lifecycle (active/stale/archived) and quarantine. **`PARTIAL`** |
| **Proposal** | `skills/skill_corpus_hygiene.py` `[TIER-2]`: periodic similarity clustering over skill descriptions, conflict detection (two skills claiming the same trigger), and a *report-only* remediation artifact with one-click action suggestions. |

### B5. Connector trust-by-default-off
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | A newly added MCP server does **not** activate automatically; the user must explicitly trust it. Credentials live where the provider documents them; the config is merged, never overwritten |
| **Alpha today** | `mcp` engine, `extensions` with dynamic loading, `mcp.py` router, enterprise MCP reference doc with zero-trust RBAC and mTLS. **`PARTIAL`** — strong transport and policy story, but activation-on-add is the natural default |
| **Proposal** | `extensions/connector_trust_gate.py` `[TIER-1]`: an explicit trust state per server (`pending_trust` → `trusted` → `suspended`), merged-config writes, and a surface showing exactly which tools a connector will expose before trust is granted. Ties into `authz` for per-connector credential scoping. |

### B6. Contextual connector recommendation
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Recommends a connector only when the current task needs one, never invents names/IDs/statuses, presents candidates in a card, and caps a single recommendation batch |
| **Alpha today** | `catalog_tool_search`, `community` marketplace, `skills_hub_manage`. **`PARTIAL`** — discovery exists, proactive contextual recommendation does not |
| **Proposal** | `integrations/capability_gap_recommender.py` `[TIER-2]`: after a run that visibly worked around a missing integration (e.g. manual CSV export instead of an API), emit a *grounded* recommendation referencing only real, resolvable candidates, with a hard rule against fabricating identifiers. |

---

## Phase C — Context & Cognitive Economy

### C1. Deferred / progressive tool disclosure
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Tool schemas load on demand from a deferred registry; a lookup step resolves a name to a schema before first use |
| **Alpha today** | 100+ built-in tools plus MCP, `catalog_tool_search` / `catalog_tool_describe` / `catalog_tool_call` meta-tools. **`PARTIAL`** — the meta-tools exist but the default path still front-loads the registry |
| **Proposal** | `tools/progressive_disclosure.py` `[TIER-1]`: partition tools into an always-visible core and a deferred pool; the runtime injects only core schemas, with a mandatory schema-resolution step before first use of a deferred tool. Measure prompt-token delta as the success metric. |

### C2. Delegation before exploration
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Broad, open-ended codebase search is delegated to a dedicated exploration path rather than performed inline in the main thread |
| **Alpha today** | `orchestrator`, `subagents` (general/research/quick/deep-research), `delegate_to_deep_agent`, `swarms`. **`ALREADY HAVE`** — Alpha's delegation is deeper |
| **Proposal** | No new engine. **Adopt the default:** add a runtime rule that any search classified as "open-ended, multi-file, exploratory" is routed to a read-only explorer subagent returning a distilled digest, keeping raw search noise out of the main context. `[TIER-1]` |

### C3. Context-budget thrift as a behavioural rule
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Explicitly avoids redundant verification reads when the needed context is already available; prefers targeted reads over blanket reads |
| **Alpha today** | `context` (sliding-window compaction, context-as-data), `trajectory` compaction, `trajectory_audit_tool`. **`PARTIAL`** — engineering exists, policy is absent |
| **Proposal** | `context/read_economy_policy.py` `[TIER-1]`: instrument re-read detection and inject a corrective nudge via the `supervision` (Kibitzer) channel when redundant reads exceed a threshold. Report wasted tokens per run in `ledger`. |

### C4. Multi-layer memory with **differentiated write policy**
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Memory is split by scope *and* by write authority: a server-learned read-only profile, an explicit user-rules file written only on request, and a workspace log + curated notes. Logs are append-only; old logs are distilled, not deleted |
| **Alpha today** | `memory` (vector + episodic + dreaming), `knowledge` (graph), `reflection`, the L0–L8 plane, `cognitive_memory_tiering`, `consolidate_cognitive_memory`, `recall_agent_memory`, FTS5 session recall, cross-thread lineage, collective fleet memory. **`ALREADY HAVE` on capability, `PARTIAL` on policy** — Alpha has *more* tiers but no explicit rule about which tiers the agent may write implicitly versus only on request |
| **Proposal** | `memory/write_authority_policy.py` `[TIER-1]`: annotate every memory tier with a write authority (`implicit` / `explicit_only` / `system`) and enforce it at the store boundary. This is a small change that makes an already-superior memory system *trustworthy*. |

### C5. Memory transparency & correction
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | The user can inspect what was remembered and correct it |
| **Alpha today** | `memory.py` router with semantic query and episodic replay; `MEMORY.md` surfaces. **`PARTIAL`** — read/query exists, *correction* and provenance-of-a-memory do not |
| **Proposal** | `memory/memory_provenance.py` `[TIER-2]`: every memory record carries its originating run, tool call, and confidence; the UI exposes an "why do you think this?" view and an edit/forget action that cascades to derived summaries. |

### C6. Docs-as-authoritative-retrieval
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | When asked how a platform feature works, consult the official documentation rather than improvise |
| **Alpha today** | `knowledge`, `deep_research`, `project-cartographer`, 17 local docs. **`PARTIAL`** |
| **Proposal** | `knowledge/self_documentation_index.py` `[TIER-1]`: index Alpha's own `docs/` + `AGENTS.md` + `config.example.yaml` as a privileged, versioned retrieval source, and inject a rule that self-referential "how do I configure X" questions resolve against it *first*. This also reduces config-hallucination risk, which is significant given the size of `config.example.yaml`. |

---

## Phase D — Steering, Collaboration & Interaction

### D1. Mid-flight steering without restart
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | The user can interrupt and redirect during execution; the agent is expected to reconcile the new direction rather than abandon the run |
| **Alpha today** | LangGraph interrupts, `runs` cancellation, `suggestions`, `supervision` nudges, and the HITL co-work reference doc. **`PARTIAL`** — cancellation is well-supported, *steering* (redirect-and-continue with state preserved) is not first-class |
| **Proposal** | `runtime/steering_channel.py` `[TIER-2]`: an append-only steering inbox on the run state. The agent loop checks it at step boundaries, records an explicit *reconciliation* step (what changed, what is preserved, what is discarded), and continues — instead of terminating and replanning from zero. |

### D2. Hook feedback is user feedback
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | When a hook blocks an action, the agent first tries to comply by adjusting its approach; if impossible, it asks the user to check the hook configuration — it never argues with the hook |
| **Alpha today** | `events`, `guardrails`, `policy`, extension-contributed middleware. **`PARTIAL`** |
| **Proposal** | `governance/hook_reconciliation_policy.py` `[TIER-1]`: a structured taxonomy of block reasons (policy / capability / configuration / transient) with a mandated response per class — adapt, escalate, or explain and stop. Removes a real class of dead-end loops. |

### D3. Multi-surface presence
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | The same assistant is reachable from desktop, IDE, web, and IM channels with consistent behaviour |
| **Alpha today** | `channels` (Slack, Discord, Telegram, Lark, WeChat, WeCom, DingTalk, Buzz), OpenAI-compatible gateway, `deliveries` webhooks, `threads`. **`PARTIAL`** on *parity* — the surfaces exist but behavioural consistency across them is unspecified |
| **Proposal** | `channels/surface_parity_contract.py` `[TIER-2]`: a declarative capability matrix per surface (streaming? artifacts? steering? approvals? memory edits?) plus a conformance test suite. Also encode the obvious rule: never send half-baked replies to a messaging surface. |

### D4. Real-time collaboration
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Shared artifacts, live preview panels, co-editing surfaces for documents and code |
| **Alpha today** | `canvas`, `kanban`, `groups`, `deliberation`, presence tracking in `projects`, live visual artifact verification. **`ALREADY HAVE`** the agent-side collaboration; **`PARTIAL`** on multi-*human* collaboration |
| **Proposal** | `projects/multi_human_presence.py` `[TIER-3]`: extend the existing presence and resource-lock machinery from agent-only to mixed human+agent sessions (CRDT-based shared document state, per-participant cursors, conflict policy). Justified by the workforce layer already assuming multi-actor projects. |

---

## Phase E — Engineering Craft Loops

### E1. Deliverable-first completion
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Every completed task ends by surfacing the viewable artifact, accompanied by a concise summary that stands on its own without the artifact |
| **Alpha today** | `evidence` (finish-first audit), `goal_integrity`, `council`, `artifacts` router with versioning and lineage. **`PARTIAL`** — verification is strong, *presentation* is not a required terminal action |
| **Proposal** | `goals/deliverable_presentation_gate.py` `[TIER-1]`: make "artifact surfaced + standalone summary produced" a formal completion condition in the goal engine. If no artifact exists, require an explicit justification recorded in the run ledger. |

### E2. Self-correction instead of self-reporting
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | When the agent notices a defect in its own instructions or assets, it fixes it in the same turn |
| **Alpha today** | `selfrepair`, `recovery`, `environment_auto_healer`, `rsi`. **`PARTIAL`** — machinery targets the *environment*, not the agent's own instruction/config corpus |
| **Proposal** | `rsi/instruction_corpus_maintenance.py` `[TIER-2]`: extend self-repair to cover `AGENTS.md`, skill manifests, and router documentation — with the same canary/regression safety invariants already specified for RSI. |

### E3. Windows-native robustness
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Explicit handling of Windows-specific failure modes: no non-ASCII script files, Git Bash resolution rather than the WSL shim, readiness gates that do not treat HTTP errors as ready, abort-and-clean-up on failed frontend build |
| **Alpha today** | `start.ps1` / `stop.ps1`, `AGENTS.md` documents the launcher contract, `test_serve_frontend_skip_build.py`, `test_gateway_startup.py`, `test_compose_default_bind_host.py`. **`ALREADY HAVE`** — and well tested |
| **Proposal** | Extend the pattern to the new personal-data and trash paths (encoding-safe path handling) — folded into A1/A3. |

---

## Phase F — Platform, Identity & Governance

### F1. Entitlements & feature gating
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Capabilities are gated by account tier; runtime feature flags mirror server entitlements; unavailable features degrade gracefully |
| **Alpha today** | `features` router with runtime flag toggling and capability probing, `auth` (BetterAuth + API keys), `enterprise` (tenant quotas). **`PARTIAL`** — flags are runtime-togglable but not mapped to entitlements or to graceful degradation paths |
| **Proposal** | `enterprise/entitlement_resolver.py` `[TIER-2]`: a single resolution point (plan → entitlement → feature flag → runtime capability probe) returning a capability *decision* with a reason, consumed by the planner so the agent never promises what the tenant cannot do. |

### F2. Expert/persona routing with an exclusivity constraint
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | A browsable expert center; exactly one expert or expert team active per session; enabling is separate from installing |
| **Alpha today** | `agents` (persona isolation, model overrides, capability whitelisting), `company` (virtual org), `bots` + SOUL protocol, `subagents` category presets. **`PARTIAL`** — Alpha has more personas but no *curated, session-scoped, exclusive* selection surface |
| **Proposal** | `company/expert_session_routing.py` `[TIER-2]`: a curated expert catalog with discover→enable→exclusive-activation lifecycle, mapped onto existing persona machinery, plus a visible "who is speaking" indicator. The exclusivity constraint is the interesting part: it prevents persona soup. |

### F3. Audit surface a human can actually read
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Approvals, hook blocks, permission prompts, and connector trust changes all surface as comprehensible events |
| **Alpha today** | `trajectory` (cryptographic flight recorder), `ledger`, `lineage`, `enterprise` audit trails, `console` log tailing, `tracing` (LangSmith/Langfuse/Monocle). **`PARTIAL`** — Alpha's audit is *for machines and auditors*; there is no plain-language, user-facing activity view |
| **Proposal** | `ops/human_activity_digest.py` `[TIER-1]`: derive, from existing trajectory and ledger data, a plain-language digest ("read 14 files, edited 3, ran 22 tests, spent $0.31, asked for approval twice"). No new instrumentation — pure presentation of data Alpha already records. High leverage per unit of work. |

### F4. Honest, actionable failure messaging
| | |
| :--- | :--- |
| **WorkBuddy behaviour** | Sandbox denials and permission prompts are phrased as next steps with an explicit escalation path; internal machinery is never narrated |
| **Alpha today** | `diagnostics`, `ops`, `selfrepair`, `TROUBLESHOOTING.md`. **`PARTIAL`** |
| **Proposal** | `ops/actionable_error_surface.py` `[TIER-1]`: a failure taxonomy (sandbox / permission / dependency / provider / network / user-input) each with a templated recovery phrase and an escalation route. Combined with a rule: never expose internal setup steps to the user. |

---

## Summary Matrix

| Phase | Item | Status | Tier | Proposed Artifact |
| :--- | :--- | :--- | :--- | :--- |
| A | A1 Personal-data doctrine | PARTIAL | 1 | `safety/personal_data_guard.py` |
| A | A2 Read-only-first scan | NEW | 1 | `planning` scan_only mode |
| A | A3 Trash-native deletion | NEW | 1 | `safety/reversible_delete.py` |
| A | A4 Unified undo stack | PARTIAL | 2 | `recovery/unified_undo_stack.py` |
| A | A5 Capability honesty | NEW | 1 | `policy/capability_boundary_registry.py` |
| B | B1 Install-time skill audit | PARTIAL | 1 | gate in `skills` install path |
| B | B2 Skill accumulation trigger | PARTIAL | 1 | `learning/skill_accumulation_trigger.py` |
| B | B3 Skill reflection after use | NEW | 2 | `SkillUseRecord` + self-correction |
| B | B4 Corpus hygiene reporting | PARTIAL | 2 | `skills/skill_corpus_hygiene.py` |
| B | B5 Connector trust gate | PARTIAL | 1 | `extensions/connector_trust_gate.py` |
| B | B6 Contextual connector rec. | PARTIAL | 2 | `integrations/capability_gap_recommender.py` |
| C | C1 Progressive tool disclosure | PARTIAL | 1 | `tools/progressive_disclosure.py` |
| C | C2 Delegation before exploration | HAVE | 1 | runtime default rule |
| C | C3 Read-economy policy | PARTIAL | 1 | `context/read_economy_policy.py` |
| C | C4 Memory write authority | PARTIAL | 1 | `memory/write_authority_policy.py` |
| C | C5 Memory provenance/forget | PARTIAL | 2 | `memory/memory_provenance.py` |
| C | C6 Self-docs retrieval | PARTIAL | 1 | `knowledge/self_documentation_index.py` |
| D | D1 Mid-flight steering | PARTIAL | 2 | `runtime/steering_channel.py` |
| D | D2 Hook reconciliation | PARTIAL | 1 | `governance/hook_reconciliation_policy.py` |
| D | D3 Surface parity contract | PARTIAL | 2 | `channels/surface_parity_contract.py` |
| D | D4 Multi-human presence | PARTIAL | 3 | `projects/multi_human_presence.py` |
| E | E1 Deliverable presentation gate | PARTIAL | 1 | `goals/deliverable_presentation_gate.py` |
| E | E2 Instruction corpus maintenance | PARTIAL | 2 | `rsi/instruction_corpus_maintenance.py` |
| E | E3 Windows-native robustness | HAVE | — | extend to A1/A3 |
| F | F1 Entitlement resolution | PARTIAL | 2 | `enterprise/entitlement_resolver.py` |
| F | F2 Expert session routing | PARTIAL | 2 | `company/expert_session_routing.py` |
| F | F3 Human activity digest | PARTIAL | 1 | `ops/human_activity_digest.py` |
| F | F4 Actionable error surface | PARTIAL | 1 | `ops/actionable_error_surface.py` |

**Tally:** 4 `NEW`, 22 `PARTIAL`, 2 `ALREADY HAVE` across 28 evaluated areas.

---

## Recommended Sequencing

**Wave 1 — Trust surface (all TIER-1, mostly additive):**
A1, A2, A3, A5, B1, B5, C1, C3, C4, C6, D2, E1, F3, F4

These change *what the user sees and controls* far more than *what the agent can do*.
They are individually small, mutually independent, and each closes a specific
credibility gap.

**Wave 2 — Feedback loops (TIER-2):**
A4, B3, B6, C5, D1, E2, F1, F2, D3

These introduce new state and new lifecycle stages. They should follow Wave 1 so
the surfaces that display their effects already exist.

**Wave 3 — Strategic:**
B4, D4

Worth doing, but gated on multi-human usage patterns that Alpha does not yet have
data for.

---

## Anti-Patterns to Avoid (Lessons in the Negative)

1. **Do not add engines for their own sake.** Most rows above are *policy*,
   *gate*, or *presentation* work. Alpha's engine count is already past the point
   where more engines help.
2. **Do not auto-trust extensions to reduce friction.** The reference product
   made trust explicit precisely because it is a security boundary.
3. **Do not delete or mass-refactor on the agent's own initiative.** Detect,
   report, propose — then wait.
4. **Do not narrate machinery.** Exposing internal setup steps makes a product
   feel fragile.
5. **Do not let memory writes be implicit everywhere.** The value of a memory
   system is proportional to how much the user trusts it, not how much it stores.
