# Sentinel — Autonomous Monitor / Diagnose / Fix / Verify / Commit Agent

**Goal:** one always-on agent that watches the whole system, detects crashes and  
errors, diagnoses root cause, applies a fix, verifies it, and commits — looping  
continuously withoutHuman intervention.

**Date:** 2026-09-20 · **Branch:** `main` @ `7c7be3c`

---


## 0. What already exists (verified — do not rebuild)

This is the most important section. Most of the machinery is already here; the  
gap is that **nothing connects it**, and **nothing commits**.

| Capability            | Where                                                                                                                             | State                                                                                              |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| Anomaly detection     | `supervision/watchdog.py`                                                                                                         | ✅ detects `PROGRESS_FROZEN`, `TOOL_THRASHING`, `CIRCULAR_LOOP`, `LEASE_EXPIRED`                    |
| Recovery ladder       | `supervision/recovery.py`                                                                                                         | ⚠️ ladder exists, but **restarts nothing** (I made this honest — `restart_worker` hook is unwired) |
| Environment healing   | `runtime/environment_auto_healer.py`                                                                                             | ✅ `diagnose_and_heal_environment` tool, imported at `tools/builtins/__init__.py:82` and listed in `BUILTIN_TOOLS` at `:311` |
| Test-fix retry loop   | `projects/self_healing_runner.py`                                                                                                 | ✅ retries with backoff (added `b4f2b2d`)                                                           |
| Deep debugging agents | `subagents/builtins/deep_debugger_agent.py`, `deep_code_reviewer_agent.py`, architect / performance / security / test-synthesizer | ✅ fleet + hierarchical delegator                                                                   |
| Frontier engines      | introspective-tree-search, program-slicing, differential-invariant-fuzzer, structural-ast-reconciler                              | ✅                                                                                                  |
| Bot profiles          | `bots/templates.py` — **23** templates (`BOT_TEMPLATES`; the module docstring calls it "inventory #23")                   | ✅ includes `sre` and `devops`                                                                      |
| Retirement / routines | `bots/profile.py`, `bots/registry.py`                                                                                             | ✅ added `cd549e2`, `7c7be3c`                                                                       |
| Frontend monitor      | `frontend/src/components/WorkspaceVitals.tsx`                                                                                     | ⚠️ covers Gateway + Database only                                                                  |
| Tool registry         | 130 tools                                                                                                                         | ✅                                                                                                  |

### The three real gaps

1. **No auto-commit exists.** A grep for `git commit` / `auto_commit` across the  
   harness returns nothing relevant. Nothing in the system can commit.
2. **Nothing closes the loop.** Detection (`watchdog`) → diagnosis  
   (`environment_auto_healer`) → fix (`self_healing_runner`) → **stop**. There is  
   no orchestrator that runs them in sequence and persists the result.
3. **No error ingestion.** The watchdog watches *agent* health. Nothing watches  
   *system* health — gateway logs, frontend build output, test failures, process  
   exits.

> **Terminology note:** you mentioned "omarch". I could not find that in the  
> repo. The closest matches are **OmO (oh-my-openagent)**, which the resident  
> memory sidecar is inspired by (`memory/__init__.py`), and  
> **`references/05-autonomous-operations-and-execution/`**, which is directly on  
> topic. I have used both. Tell me if you meant something else.

---

## 1. Architecture — the sentinel loop

```
        ┌──────────────────────────────────────────────┐
        │                                              │
   OBSERVE ──► DIAGNOSE ──► FIX ──► VERIFY ──► COMMIT ──┘
        │          │          │         │          │
        │          │          │         │          └─ git commit (only if green)
        │          │          │         └─ tests + build + smoke
        │          │          └─ patch (bounded, reversible)
        │          └─ root-cause, named rule
        └─ signals from 4 sources (below)
```

Every stage is **bounded and reversible**. A stage that cannot complete does not  
silently proceed — it escalates.

### The five stages

| Stage        | Responsibility                        | Failure behaviour                   |
| ------------ | ------------------------------------- | ----------------------------------- |
| **OBSERVE**  | Collect signals from all sources      | n/a — always safe                   |
| **DIAGNOSE** | Classify + root-cause, name the rule  | Unknown → escalate, never guess-fix |
| **FIX**      | Apply smallest reversible change      | Bounded attempts, checkpoint first  |
| **VERIFY**   | Tests + build + smoke must pass       | **Fail → revert, never commit**     |
| **COMMIT**   | Atomic commit with structured message | Only reached if VERIFY green        |



---


## 2. Phase 1 — Signal ingestion (OBSERVE) · M

**Why first:** without signals the loop has nothing to act on.

Four sources, unified into one `Signal` shape:

```python
@dataclass(frozen=True)
class Signal:
    source: str        # "watchdog" | "logs" | "tests" | "process"
    kind: str          # "progress_frozen" | "import_error" | "test_failure" | "process_exit" | ...
    severity: str      # "critical" | "high" | "medium" | "low"
    message: str
    context: dict      # file paths, traceback, exit code, ...
    fingerprint: str   # dedup key so the same fault is not re-fixed forever
```

1. **Watchdog adapter** — wrap `supervision/watchdog.py` anomalies into `Signal`.  
   Cheapest: existing detection, no new logic.
2. **Log tailer** — watch `logs/*.txt` (the repo already writes gateway, backend  
   and build logs there) for tracebacks and `ERROR`/`Failed`/`FAILED` lines.
3. **Test watcher** — parse `uv run pytest` output for failures.
4. **Process liveness** — gateway/frontend up-or-down (extends `WorkspaceVitals`,  
   which currently covers Gateway + Database only).

**Dedup is mandatory.** `fingerprint` (source + kind + normalised message) stops  
the sentinel from retrying the same unfixable fault forever. Add a cooldown and  
a per-fingerprint attempt cap.

**Files:** new `runtime/sentinel/signals.py`, `runtime/sentinel/sources/*.py`

---

## 3. Phase 2 — Diagnosis (DIAGNOSE) · M

Route each signal to the right specialist rather than one generic code path:

| Signal kind                       | Route to                                                          |
| --------------------------------- | ----------------------------------------------------------------- |
| Missing dependency / import error | `environment_auto_healer.diagnose_and_heal` (exists)              |
| Test failure                      | `self_healing_runner.run_with_self_healing` (exists, has backoff) |
| Logic bug / regression            | `deep-debugger` subagent (exists)                                 |
| Frozen / thrashing / loop         | `supervision/recovery.py` ladder                                  |
| Build failure                     | targeted — see Phase 6                                            |

**Rule: an unclassified signal escalates; it is never guess-fixed.** This is the  
single most important safety property. A confident wrong fix is worse than no  
fix.

**Files:** new `runtime/sentinel/diagnose.py`

---

## 4. Phase 3 — Fix + Verify (the safety core) · L

This is where the real risk lives. Order matters:

```
checkpoint()  →  apply fix  →  verify()  →  green? commit : revert(checkpoint)
```

- **`checkpoint()`** — snapshot before touching anything. WikiVault already does  
  atomic writes (`memory/wiki_vault/vault.py`); reuse that pattern, or a git  
  stash/worktree. Nothing is fixed without a way back.
- **Bounded attempts** — max N attempts per fingerprint, with backoff (the  
  `self_healing_runner` backoff added in `b4f2b2d` is the model).
- **`verify()`** is a hard gate: tests **and** build **and** smoke must pass.  
  **Failure means revert — never commit.** No exceptions, no "probably fine".
- **Regression guard** — re-run the *previously passing* checks too. A fix that  
  resolves the signal but breaks something else must fail.

**Files:** new `runtime/sentinel/fix.py`, `runtime/sentinel/verify.py`

---

## 5. Phase 4 — Commit (COMMIT) · M

Nothing in the system can commit today. Add one narrow, auditable primitive.

- **Structured message** — subject + why + what + evidence (signal fingerprint,  
  verification output). Makes `git log` a repair history you can actually read.
- **Scope limit** — only files the fix touched. Never `git add -A` (a parallel  
  session frequently leaves unrelated files dirty in this repo — verified).
- **Never destructive** — no `reset --hard`, no force push, no history rewrite.
- **Push is a separate, explicitly-enabled step.** Auto-commit is one thing;  
  auto-push is another and should stay opt-in.

**Files:** new `runtime/sentinel/commit.py`

---


## 6. Phase 5 — The Sentinel bot profile · S

Register a first-class bot rather than a background daemon, so it inherits  
soul, skills, toolsets, memory namespace, routines and capability epoch.

```python
# bots/templates.py — alongside the existing 19
"sentinel": {
    "display": "Sentinel",
    "role": "Autonomous reliability engineer — monitor, diagnose, fix, verify, commit",
    "avatar": "🛡️",
    "department": "engineering",
    "reports_to": "cto",
    "responsibilities": ["Continuous monitoring", "Root-cause diagnosis",
                         "Bounded automated repair", "Verification gating"],
    "capabilities": ["log_analysis", "root_cause_analysis", "patch_synthesis",
                     "test_verification", "safe_commit"],
}
```

Give it:

- `agent_preset` — a restricted preset (read + search + targeted edit + test,  
  **no** broad shell) using the preset system from `config/agent_preset_config.py`
- `memory_scope: "sentinel"` — its own namespace (`ae42d51`)
- `routines` — the observe loop as a scheduled routine (`7c7be3c`)
- `heartbeat` + `succession_fallback` — so a dead sentinel is detectable

**Critical:** wire `effective_capabilities()` (`5015eea`) into its assembly so you  
can *see* what it may touch before enabling it.

---

## 7. Phase 6 — Known concrete failures to fix first · S

Real, observed, reproducible:

1. **Frontend production build** — already fixed in `254f3ab`; the sentinel  
   should have caught this. Good first test case.
2. **Recovery never restarts** — `supervision/recovery.py` claims restarts it  
   does not perform. Wire the `restart_worker` hook I added in `b4f2b2d`, or let  
   the sentinel escalate rather than pretend.
3. **WorkspaceVitals coverage** — currently Gateway + Database only. Extend to  
   frontend build, test status, and sentinel state.

---

## 8. Safety rails (non-negotiable)

| Rail                                       | Why                                      |
| ------------------------------------------ | ---------------------------------------- |
| **Verify-before-commit**                   | A red test must never reach git          |
| **Checkpoint before fix**                  | Every change must be reversible          |
| **Scope-limited commit**                   | Parallel sessions leave dirty files here |
| **Attempt cap + cooldown per fingerprint** | Stops infinite retry loops               |
| **Unclassified → escalate**                | Never guess-fix                          |
| **No destructive git**                     | No `reset --hard`, no force push         |
| **Push opt-in**                            | Auto-commit ≠ auto-push                  |
| **Full audit trail**                       | Every stage records what it saw and did  |

---

## 9. Build order

```
Phase 1  Signal ingestion        (M)  ← nothing works without this
Phase 5  Sentinel bot profile    (S)  ← cheap, gives it an identity
Phase 6  Concrete known failures (S)  ← proves the loop end-to-end
Phase 3  Fix + Verify            (L)  ← the safety core, biggest risk
Phase 4  Commit                  (M)
Phase 2  Diagnosis routing       (M)
```

Phase 6 before Phase 3 deliberately: fix two real, known bugs through the loop  
so the mechanism is proven before it generalises.

## 10. Definition of done

The sentinel, running unattended:

- detects a deliberately injected failure (missing import / failing test /  
  killed process)
- diagnoses it with a **named** root cause
- applies a bounded fix
- **verifies** — tests + build green
- commits with a structured, evidence-bearing message
- reverts and escalates when verification fails
- never commits red, never loops forever on one fault

---

## Open questions

1. **Auto-push, or commit only?** (Recommend: commit only, push opt-in.)
2. **Which branch?** Dedicated `sentinel-fixes` branch + PR, or direct to `main`?  
   (Recommend: branch + PR — reviewable, and reverts cheaply.)
3. **Scope of fixes** — dependency/import/test failures only, or full logic bugs  
   via `deep-debugger`? (Recommend: start narrow, widen after it proves itself.)
4. **Does it fix the *product* or the *deployment*?** These are different  
   failure classes and need different verification.
