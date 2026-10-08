# Alpha Mod Kernel (AMK) & Autonomous Execution Governance Spine
## Master Architecture & Engineering Specification

**Document Version:** 1.0.0  
**Status:** Design specification; implementation is partial (see current status below)
**Target Project:** `alpha` (`https://github.com/itsPremkumar/alpha`)  
**Derived From:** `ALPHA_CLAUDE_MODS_DERIVED_MASTER_IMPLEMENTATION_PLAN.md`  

---

## 1. Executive Summary & Architectural Motivation

Claude Code introduces a seminal concept in agentic runtime design: **Mods**—programmable, event-driven middleware extensions that receive an ambient capability interface (`$`), typed event payloads (`e`), and an asynchronous continuation function (`next`). Handlers can **observe**, **rewrite**, or **answer/short-circuit** operations across tool execution, prompt composition, turn iterations, and subagent spawns.

Alpha now has a native ordered Mod Kernel, connected to the lead-agent tool/model middleware chain, run admission, autonomy ticks, bot DMs and task claims, and channel dispatch. The first-party package includes ESTOP, tool-risk, evidence, bot-mode, task-routing, and failure-sentinel modules. This is a partial control plane, not a general or durable workflow engine. The kernel journal, held actions, UI cards, key-value storage, and timers are process-local; deferred actions have no durable operator-resume route; the synthetic harness is not a sandbox; and several external side-effect paths are governed separately. See `docs/PRODUCTION_READINESS_INVENTORY.md` before making production-readiness claims.

---

## 2. Core Architecture: The 4-Tier Topology

```
                         ALPHA AGENT RUNTIME
                                   │
                     ┌─────────────┴─────────────┐
                     │    Alpha Runtime Bus      │
                     │  Typed Events + Lineage   │
                     └─────────────┬─────────────┘
                                   │
                     ┌─────────────┴─────────────┐
                     │     Alpha Mod Kernel      │
                     │ Ordered Middleware Chain  │
                     └─────────────┬─────────────┘
                                   │
      ┌────────────────────────────┼────────────────────────────┐
      │                            │                            │
Tier 1: Emergency            Tier 2: Security            Tier 8: Verification
Fleet-Wide ESTOP             Blast Radius Tool Guard     Evidence Gate
(Circuit Breaker)            (R0-R5 Policy & Hold)       (Executable Receipts)
      │                            │                            │
      └────────────────────────────┼────────────────────────────┘
                                   │
                     ┌─────────────┴─────────────┐
                     │  Capability Context ($)   │
                     │ Scoped Tools, Models, DB  │
                     └─────────────┬─────────────┘
                                   │
                     ┌─────────────┴─────────────┐
                     │   LangGraph / RunManager  │
                     │ Native Tools / Subagents  │
                     └───────────────────────────┘
```

---

## 3. The Alpha Mod Kernel (AMK) Protocol & Pipeline

### 3.1 Priority Tiers
Every mod registers with a deterministic priority integer. Execution proceeds strictly from lowest priority to highest:

```python
class ModPriority(IntEnum):
    KERNEL = 0          # Invariants, correlation tagging, audit logging
    EMERGENCY = 100     # Fleet-wide ESTOP, immediate halts
    SECURITY = 200      # Blast radius, secret leakage, permission checks
    BUDGET = 300        # Token/spend quotas, admission control
    AUTONOMY = 500      # APEX mission controller, dynamic task routing
    EXECUTION = 700     # Execution instrumentation, tracing
    VERIFICATION = 800  # Evidence collection, acceptance gate
    RECOVERY = 900      # Failure sentinels, repair supervisors
    OBSERVABILITY = 1000# Flight recorder, metrics, UI projections
    USER_EXTENSIONS = 2000 # Optional operator-installed extensions
```

### 3.2 Mod Event Outcomes
When handling an event, a mod must return an explicit `EventResult` variant:
- **`CONTINUE`**: Mod observed or logged the event and passes control to `next(event)`.

The kernel executes a handler's downstream continuation at most once. Repeated or
concurrent calls to `next()` share the same downstream task and result, preventing
an accidental duplicate tool side effect. This is an in-process dispatch invariant;
it does not provide cross-process idempotency for external effects.
- **`REWRITE`**: Mod modified the event payload (e.g. injected policy reminders or normalized paths) and passes `next(mutated_event)`.
- **`ANSWER`**: Mod short-circuits execution and returns a terminal response immediately without invoking downstream handlers or tools.
- **`DENY`**: Mod explicitly refuses the operation due to security/policy violations.
- **`DEFER`**: Mod returns a hold result. Durable parking and operator resume are not implemented.
- **`RETRY`**: Mod returns a retry request. The lead-agent adapter currently refuses it because no Mod retry scheduler is wired.
- **`ESCALATE`**: Mod returns an escalation result. The lead-agent adapter refuses the tool call; supervisor dispatch is not implemented here.

---

## 4. The Ambient Capability Context (`$`)

Mods receive a capability context with scoped, audited API access to runtime primitives. Python modules are not isolated from the Gateway process and can still perform ambient side effects; capability checks are not an OS sandbox:

| Namespace | Method | Description |
|---|---|---|
| `$.tools` | `dispatch(name, args)` | Requires explicit `tools:execute` grant and an explicitly registered executor. This helper is not a substitute for the normal Gateway tool path and does not itself guarantee policy/sandbox enforcement. |
| `$.models` | `complete(prompt, model_profile)` | Optional sidecar request; behavior depends on configured model routing and is not a durable service. |
| `$.clock` | `after(seconds, event)` | Schedules an in-process task; it is lost on process restart. |
| `$.evidence`| `record(receipt)` | Records in a process-local ledger and may mirror into the evidence store; persistence depends on that store succeeding. |
| `$.estop` | `status()`, `engage(reason)` | Reads or trips the fleet stop. A failed state read is treated as engaged. |
| `$.ui` | `render_card(card_spec)` | Stores an in-process card projection; this alone does not deliver an interactive frontend approval. |

---

## 5. The Core Triad of First-Party Enforcer Mods

### Mod 1: Fleet-Wide Emergency Stop (`FleetEstopMod`)
- **Priority:** `100` (`ModPriority.EMERGENCY`)
- **Lifecycle Interception:** tool, agent, mission, turn, autonomy, run, task, bot, and group event patterns handled by this Mod.
- **Guarantees:**
  - When active, matching new operations are refused by the kernel.
  - Active worker cancellation and durable `cancelled_by_estop` state are not implemented by this Mod.
  - This package does not expose the documented `POST /api/mods/estop/release` route; release follows the existing runtime ESTOP owner.

### Mod 2: Blast Radius & Tool Risk Guardian (`BlastRadiusGuardMod`)
- **Priority:** `200` (`ModPriority.SECURITY`)
- **Lifecycle Interception:** `tool.requested`
- **Risk Classification:**
  - **R0 (Read-Only):** `view_file`, `grep_search`, `read_url`. Auto-allowed.
  - **R1 (Local Reversible):** Single-file edit with git tracking. Auto-allowed.
  - **R2 (Broad Mutation):** No complete diff-aware classifier is implemented.
  - **R3 (Network & Credentials):** Pattern-based classification only; scoped credential leases are not implemented here.
  - **R4 (Destructive / Irreversible):** Selected string patterns are deferred into an in-memory hold. No durable approval, release route, or guaranteed diff preview exists.
  - **R5 (Production Critical):** Selected command/tool-name patterns are classified; MFA or dual-key approval is not implemented here.

### Mod 3: Verification & Evidence Gate (`VerificationEvidenceGateMod`)
- **Priority:** `800` (`ModPriority.VERIFICATION`)
- **Lifecycle Interception:** `turn.complete`, `task.completion_requested`, `mission.completion_requested`
- **Claim Honesty Boundary:**
  - Detects selected success claims and consults receipts in the Mod evidence context.
  - It does not independently authenticate receipts or run tests, syntax checks, or type checks.
  - Missing evidence can add a remediation directive to the model turn; APEX run completion still requires its separate measured acceptance contract.

---

## 6. Developer & Operator Tooling

### Static Manifest Analysis
```bash
alpha mod validate ./my_mod
```
Checks selected `ctx` capability references against declarations using AST analysis. It does not sandbox imports or reliably detect network access, shell escapes, or arbitrary Python side effects.

### Synthetic Offline Testing Harness
```bash
alpha mod test ./my_mod
```
Runs a small local synthetic event harness. It is not a sandbox and does not prove deterministic behavior or production integration.

---

## 7. Production Gates Still Open

1. Move journals, approval holds, storage, and scheduled events to durable stores with leases, idempotency, retention, and restart recovery.
2. Add an authenticated, owner-scoped approval workflow that resumes or rejects the exact held action without replaying an unapproved side effect.
3. Run every consequential tool through existing authorization, sandbox, approval, and cancellation controls; add tests proving no alternate Mod helper bypasses them.
4. Add per-handler deadlines, cancellation semantics, event replay, dead-letter handling, and bounded payload validation.
5. Run the integrated event → task → tool → independent verification path against real Alpha runtime resources and supported persistence configurations.
