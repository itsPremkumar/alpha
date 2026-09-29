# Alpha Git + Worktree Integration — corrected implementation plan

**Corrects:** `ALPHA_FULL_GIT_INTEGRATION_ARCHITECTURE.md` (external, 2400+ lines)
**Basis:** every claim below was verified by reading alpha's code on 2026-09-29 at
`main` = `866460a`. Superseded statements are quoted and refuted with `file:line`.
**Scope:** a plan, not an implementation. Nothing here has been built yet.

---

## 0. The finding that reshapes this plan

The source document describes a system to build. Most of its **mechanics already
exist in alpha**. Building them again would be the second time this repo has
shipped a capability without a consumer — the exact failure `IMPROVEMENT_PLAN.md`
names: *"a claim that is more specific than the evidence behind it."*

Verified inventory:

| Document assumes missing | Actual state in alpha | Evidence |
| --- | --- | --- |
| Worktree lifecycle (`add`/`remove`/`list`/verify) | **`WorktreeManager` exists** — create, remove, list, `worktree_context`, plus `_verify_worktree` identity proof | `sandbox/worktrees.py:27-145` |
| Merge/ordering engine | `evaluate_release_gate` — fail-closed, 4 metric gates | `benchmarks/release_gate.py:31-53` |
| Overlap/conflict classes | `analyze_semantic_git_delta` is a **shipped model-facing tool** | `tools/builtins/semantic_git_delta_tool.py`, manifest `wired` |
| PR packaging | `PRSynthesizer` + `infer_conventional_commit` | `projects/pr_synthesizer.py` |
| Task DAG | `planning/dag_orchestrator.py` | present |
| Checkpointing | `projects/checkpoint_engine.py` | present |
| Canaries | `projects/canary_watchdog.py` (consumes `WorktreeManager`) | `canary_watchdog.py:140,142` |
| Repo map / provenance | `repo_twin_tool`, `generate_repo_map`, `lineage/artifact_lineage.py` | manifest |
| Event system | `alpha/events/bus.py`, `runtime/side_effects/` | present |

**So the real gap is not "build git integration." It is: alpha has the pieces and
almost none of them are reachable, ordered, or measured.**

### What genuinely does not exist (verified)

| Gap | Evidence |
| --- | --- |
| **No model-facing git tool at all** | The only manifest tool matching `git` is `analyze_semantic_git_delta`. `config.example.yaml:1507` references `git_push` in a *commented-out* preset — it is a policy fiction with no implementation. |
| **No `main` protection** | No `CODEOWNERS`, no branch-protection workflow. `git branch --show-current` shows `main` freely editable. |
| **No git hooks** | `.githooks/` absent. |
| **No merge authority separation** | `WorktreeManager` has no notion of an owner, role, or permission. Any caller may create a worktree on any branch. |
| **WorktreeManager has no task identity** | `WorktreeInstance` is `{path, branch_name, is_active, created_at}` — no task id, agent id, base sha, or state. `sandbox/worktrees.py:19-25` |
| **`WorktreeManager` state is process-local** | `self._active_worktrees: dict` is an unsynchronized in-memory map (`worktrees.py:31`) that is **never rehydrated from `git worktree list`**. A Gateway restart forgets every worktree it owns while git still lists them — so `remove_worktree` returns `False` and orphaned worktrees leak. |
| **`worktree_context` force-removes by default** | `delete_on_exit: bool = True` with `force=True` (`worktrees.py:139,144`) — an uncommitted-work loss path the document's §32 crash-recovery rules explicitly forbid. |

That last two are **real defects today**, not roadmap items.

---

## 1. What to correct in the source document

| # | Document statement | Why it is wrong or unsafe for alpha | Correction |
| --- | --- | --- | --- |
| 1 | §6 "Create a dedicated **MBMA agent identity**" with merge authority | Alpha's authority model is **server-side, not agent-side** (`authz`, `sandbox_authz`, opaque approval ids in `ComputerWorker`). An LLM identity cannot be a trust boundary — it is prompt-injectable. | MBMA is a **capability token minted server-side**, re-using the existing opaque-approval pattern (`test_computer_use_sandbox.py:74-98`). Never a `SOUL`/role string. |
| 2 | §2.2 `clone -> fetch -> branch -> worktree` cold-start | Alpha already uses worktrees extensively; a full clone step is wrong. | Keep `fetch --prune` + `rev-parse` verification. Already how `WorktreeManager` works. |
| 3 | §15 "typed command registry … `GIT_READ`, `GIT_MAIN_MERGE`" | Correct instinct, wrong layer. Alpha has `alpha/authz/` and `guardrails/command_policy.py` already. | Extend the existing permission system. Do **not** build a parallel capability enum. |
| 4 | §13 branch protection as the primary control | GitHub protection cannot be assumed; alpha is self-hosted and the repo currently has none. | Pre-push hook + server-side gate must be the *primary* control; GitHub is defence in depth. |
| 5 | §27 `ASSERT current_actor == MAIN_BRANCH_MAINTAINER` | An assertion against a string is not a control (see #1). | Assert against a **server-minted lease**. |
| 6 | §55 Git state machine (26 states) | Far too coarse to be honest. Alpha's rule is "absent is never coerced." A 26-state enum with a single `STALE` cannot express *why*. | Model states as **evidence-carrying records** (`status`, `reason`, `measured_at`), reusing the pattern in `benchmarks/suites.py` and `swarm`'s `telemetry`. |
| 7 | §9 "inputs" list incl. `author confidence`, `agent self-report` | These are model opinions. Alpha's own release gate deliberately uses *numeric measured* metrics. | Exclude self-report from the gate. Let it inform, never authorize. |
| 8 | §7 candidate JSON with `"tests": {"unit": "passed"}` | A string `"passed"` is exactly the false-precision alpha forbids. | Use the `evidence_kind` discipline already in `rsi/holdout.py:182-184` (`measured` / `unverified`) and `rsi/shadow.py:57-62`. |
| 9 | §35 stash policy | **Directly contradicts this session's operating rules** — `git stash` is forbidden in the shared tree because it obscures other agents' work. | Drop the stash section. Alpha commits checkpoints instead (`checkpoint_engine.py`). |
| 10 | §19 git bundle recovery | Not needed at v1. Alpha's own honesty rules forbid claiming multi-process guarantees it lacks; a bundle channel is the same over-reach. | Defer. `reflog`/`fsck` recovery (§18) is the honest v1. |
| 11 | §40 "`git grep` + code graph" | Alpha **already has** `lsp_client_engine.py` (65KB), `repo_twin`, `program_slicing`. | Wire the existing indexer to git history. Do not build a second. |
| 12 | Whole doc assumes agents are the unit of work | Alpha's real unit is the **run** (`RunManager` is sole lifecycle owner, `backend/AGENTS.md`). | Bind worktrees to run/thread identity, which already exists. |

---

## 2. Architecture that fits alpha

Reuse the existing seams. Each box below is an **extension of something that exists**.

```
  RunManager (sole lifecycle owner — unchanged)
        │  run_id / thread_id  ← the identity that already exists
        ▼
  ┌────────────────────────────────────────────┐
  │ TaskWorktreeRegistry   (NEW — thin)        │
  │  rehydrates from `git worktree list`       │
  │  persists TaskRecord; owns cleanup policy  │
  └───────────────┬────────────────────────────┘
                  │ uses
                  ▼
  ┌────────────────────────────────────────────┐
  │ WorktreeManager (EXISTS — harden)          │
  │  add/remove/list/_verify_worktree          │
  │  +: task identity, no force-by-default     │
  └───────────────┬────────────────────────────┘
                  │ authorized by
                  ▼
  ┌────────────────────────────────────────────┐
  │ authz (EXISTS) — new `git:*` permissions   │
  │   GIT_READ / GIT_WRITE / GIT_MAIN_MERGE     │
  └────────────────────────────────────────────┘
                  │ gated by
                  ▼
  ┌────────────────────────────────────────────┐
  │ IntegrationVerifier (NEW — thin)           │
  │  runs in a THROWAWAY worktree, never main  │
  │  feeds evaluate_release_gate (EXISTS)       │
  └────────────────────────────────────────────┘
```

**The one genuinely new idea:** *every candidate branch is merged into a disposable
simulation worktree, and the release gate is evaluated there — before `main` is
touched.* The document has this in §8 Phase F / §27. It is the right core, and
alpha already has the two halves (worktrees + gate) that make it cheap.

---

## 3. Implementation phases

Effort: **S** ≤ 2 days · **M** ~1 week · **L** 2+ weeks. Every phase ships with
tests; TDD is mandatory in `backend/tests/`.

### Phase 0 — Fix two live defects (S)

Do this first. Both are bugs in shipped code, not new features.

**0a. `WorktreeManager` forgets its worktrees on restart.**
`self._active_worktrees` (`worktrees.py:31`) is never rehydrated. After a Gateway
restart `remove_worktree()` returns `False` for a worktree git still tracks, so
cleanup silently leaks. *Fix:* make `list_worktrees()` the source of truth and
treat `_active_worktrees` as a cache; reconcile at construction.
*Test:* construct a manager over a repo with a pre-existing worktree, assert it
is listed and removable.

**0b. `worktree_context` force-removes uncommitted work by default.**
`delete_on_exit=True` + `force=True` (`worktrees.py:139,144`). A crash mid-task
destroys uncommitted work, which §32 of the source doc explicitly forbids.
*Fix:* default `delete_on_exit=False`; when removal is requested and the tree is
dirty, refuse and report rather than discarding.
*Test:* dirty worktree + `delete_on_exit=True` → raises/refuses, diff still present.

### Phase 1 — Task identity for worktrees (M)

Add `TaskRecord` (`packages/harness/alpha/git/records.py`):

```
task_id, run_id, thread_id, owner_id, branch, worktree_path,
base_sha, head_sha, state, state_reason, created_at, updated_at
```

- `state` ∈ `NEW | ACTIVE | PAUSED | STALE | READY_FOR_REVIEW | BLOCKED |
  SUPERSEDED | MERGED | ABANDONED | CLEANED`
- **`state_reason` is mandatory and non-empty.** A state with no reason is
  rejected at construction — this is the §6 correction, enforced in code.
- Persist under `runtime_home()/git/tasks.json` (single process; say so plainly —
  do **not** claim cross-process safety; `PRODUCTION_READINESS_INVENTORY.md:31`
  already establishes that honesty rule).

Wire `WorktreeManager` to accept/return a `TaskRecord`. Keep `WorktreeManager`
usable standalone (canary_watchdog depends on it).

### Phase 2 — Authorization for git verbs (M)

Add `git:*` permissions to the **existing** `alpha/authz/` system (§15 correction):
`GIT_READ` (default yes), `GIT_WRITE` (yes, task branch only), `GIT_MAIN_MERGE`
(no, except a server-minted lease), `GIT_FORCE_PUSH` (no), `GIT_BRANCH_DELETE` (no).

Critical: reuse the `ComputerWorker` opaque-approval design
(`test_computer_use_sandbox.py:74-98`) — non-mintable, single-use,
command-bound, and **not exposed in the model-facing tool schema**. Per the safety
review, `smart_approval_tool.py:12-24` is advisory and must not be treated as the
gate.

### Phase 3 — Model-facing git tool (M) — *the actual missing capability*

One tool, `git_workspace`, with **actions** rather than a second tool family (keeps
tool counts stable, so `contracts/feature_manifest.json` churn is avoided):

```
status | diff | log | branch_create | commit | worktree_create
      | worktree_list | worktree_remove | submit_for_review
```

- `runtime: Runtime` as a **bare required first parameter** (hard repo rule).
- Never build a shell string: pass argv (matches `_run_git`, `worktrees.py:43-56`).
- Every action returns server-measured facts with their source; a failed git call
  surfaces git's stderr, never an empty result.
- Branch names validated by `check-ref-format` (already used at `worktrees.py:79`).

Run `scripts/check_tool_schemas.py` and regenerate the manifest.

### Phase 4 — Integration verification (M) — *highest value*

`IntegrationVerifier`:
1. create a **throwaway** worktree at current `main` (`git worktree add --detach`)
2. `git merge --no-commit --no-ff <branch>`; on conflict → `rerere` candidate →
   else `CONFLICT_REQUIRES_REVIEW` (§49: never resolve semantically by string)
3. run the configured suite
4. `git merge --abort`, remove the worktree
5. feed measured metrics into `evaluate_release_gate` (`release_gate.py:39`)

**Wiring the gate is the point.** Today `evaluate_release_gate` is defined at
`benchmarks/release_gate.py:39` and re-exported by `benchmarks/__init__.py:4,6`,
but it has **no production caller** — verified 2026-09-29: the only other
references are its own unit test. Shipping this converts a documented intent into
an enforced one, and it is the same "pin the consumer, not the declaration" rule
the repo already wrote down.

`main` is only touched after the simulation passes, and only by the lease holder.

### Phase 5 — Pre-push hook + `CODEOWNERS` (S)

- `core.hooksPath` → `.githooks/` with `pre-push` rejecting non-lease pushes to `main`.
- `commit-msg` enforcing Conventional Commits (the repo's own `commit-msg` hook
  already rejected my commit until I fixed the format — proof the rule is real).
- `.github/CODEOWNERS` for `backend/packages/harness/alpha/**`,
  `frontend/src/**`, `.github/workflows/**`, `contracts/**`.

**Honest caveat to state in the docs:** the safety review found `docs/SECURITY.md:501-505`
advertises scanners CI does not run. Do not add more aspirational controls; the
hook must be testable.

### Phase 6 — `MAIN_UPDATED` propagation (M)

On `main` change, publish `MAIN_UPDATED {old_sha, new_sha, merged_tasks,
changed_paths}` on the **existing** `alpha/events/bus.py`, and mark overlapping
branches `STALE` with a reason. Reuse the branch-overlap signal that
`analyze_semantic_git_delta` already computes — do not write a second differ.

### Phase 7 — Optional, defer (do not build at v1)

`git bisect` automation (§17) — alpha has `selfrepair/sbfl.py` already, which
localizes faults; a bisect driver is additive, not foundational.
`git notes` provenance (§38) — `lineage/artifact_lineage.py` covers this in-app.
Bundle recovery (§19) — see correction #10.

---

## 4. Explicitly not doing

| Item | Why |
| --- | --- |
| A new capability enum separate from `alpha/authz` | Duplicate authority; the safety review shows parallel control planes already drift. |
| A 26-state git state machine | Too coarse to be honest; `state_reason` + evidence records are strictly better. |
| A new code indexer | `lsp_client_engine.py` (65KB) + `repo_twin` + `program_slicing` already exceed what the doc proposes. |
| LLM-graded merge scoring | The doc itself says don't expose a numeric score; alpha's gate is deliberately numeric-and-measured. |
| `git stash` workflow | Forbidden in this repo's operating rules. |
| Rewriting `main` history | §37 (revert-first) is correct and already alpha's `update_engine` posture. |
| Bundles / offline transport | v2 at the earliest. |

---

## 5. Definition of done

- [ ] Phase 0 defects fixed, each with a test that fails before it
- [ ] `WorktreeManager` survives a simulated restart
- [ ] `TaskRecord.state_reason` non-empty is enforced by a test
- [ ] `GIT_MAIN_MERGE` provably refused without a server-minted lease
- [ ] Model-facing git tool passes `check_tool_schemas.py`; manifest regenerated
- [ ] `evaluate_release_gate` has a **production caller** (grep-provable)
- [ ] Pre-push hook refuses a non-lease push to `main` (tested)
- [ ] `pnpm test`-equivalent backend suite + `ruff format --check` green on touched files
- [ ] `docs/INDEX.md` regenerated (`scripts/generate_docs_index.py` fails closed)
- [ ] `README.md` / `llms.txt` capability counts updated **iff** the manifest changed
- [ ] No claim of cross-process safety anywhere without a shared lease repository

## 6. Open questions for the maintainer

1. **Is `main` meant to be agent-editable at all?** Today it is. If the answer is
   no, Phase 0 is a security fix, not a refactor.
2. **Which gate metrics are real today?** `prompt_injection_resistance_rate ≥ 1.00`
   is required by the existing gate, but the safety review found no suite produces
   it. Enforcing the gate as-is would block every release. Either build the
   measurement or lower the default — but do not ship a gate that always fails.
3. **Should `git_workspace` be model-facing at all?** It is the single most
   powerful capability in this plan and also the most dangerous. The doc assumes
   agents do this; alpha's threat model argues for server-orchestrated git with the
   model proposing *content* and the server deciding *git operations*.
