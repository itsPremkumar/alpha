# Worktree strategies for Alpha — the variety, and how to choose

**Companion to** [GIT_INTEGRATION_PLAN.md](GIT_INTEGRATION_PLAN.md). That document
answers *what* to build. This one answers **which kind of isolation an agent gets**,
because "give it a worktree" is a family of choices with very different cost and
guarantee, and picking the wrong one is how a fleet of agents ends up fighting over
one checkout.

**Status:** the **vocabulary, selection, and merge simulation are implemented**:
`packages/harness/alpha/sandbox/worktree_strategy.py`,
`sandbox/merge_simulation.py`, and detached-worktree support on
`WorktreeManager`. The `LONG_LIVED` aging policy is still design. Verified
against `main` = `ee0ef86`.

## 0. What is already code

`WorktreeMode` / `Assurance` / `TaskSignals` / `WorktreeSelection` exist, with
`select_strategy()` implementing the §3 table and `disclosure()` implementing
the §4 text. `alpha.rsi.workspace.create()` now routes its worktree-vs-copy
choice through `select_strategy` instead of re-deriving it, so an RSI candidate
and any future task-driven caller resolve isolation through one rule.

Three properties are enforced in code rather than documented, because each was a
real bug first:

- **A selection cannot be created without a reason.** `WorktreeSelection.__post_init__`
  rejects a blank one; an unexplained mode is an arbitrary mode.
- **The git-availability check covers every git-backed outcome, not just the
  default.** It was originally applied only to the default branch, so a task
  declaring `produces_mergeable_diff` still received `WORKTREE` on a deployment
  that could not create one — a record promising an isolation the caller never
  got. `_guard_git` now applies it uniformly, and `COPY`/`SANDBOX` pass through.
- **A degraded copy names the mode it replaced.** "A copy was used" is not
  actionable; the reason now reads "worktree was requested but is unavailable,
  so a copy snapshot was used instead. Cause: <verbatim>".

## 0b. The `INTEGRATION` mode is now a real capability

`alpha.sandbox.merge_simulation.simulate_merge` attempts a merge in a
**detached throwaway worktree** and leaves the base ref untouched, reachable as
`WorktreeManager.simulate_merge(...)`. Detached is the point: the tree has no
branch, so nothing in a simulation can be pushed and a stray commit inside it
belongs to no ref.

Three outcomes are kept distinct, and the middle distinction matters most:

| outcome | meaning |
| --- | --- |
| `CLEAN` | git applied the merge with no textual conflict |
| `CONFLICTED` | git stopped with unmerged paths, named verbatim from `git diff --diff-filter=U` |
| `FAILED` | the merge could not be attempted, or failed for a non-conflict reason |

`FAILED` is not a flavour of `CONFLICTED`. An unknown ref, a missing object, or
a non-zero exit with no unmerged paths has **no resolution to apply**; reporting
it as a conflict sends an operator hunting for merge markers that were never
written. The tests build real conflicting branches rather than stubbing git,
because that distinction is one git makes.

**A clean merge is explicitly not a safety verdict.** `disclosure()` says so in
those words and a test asserts the sentence is present:

> git applied the merge with no textual conflict. This says the trees could be
> joined — NOT that the result is correct, tested, or safe to ship.

That is why the simulation is a separate module from the release gate: the gate
answers "should this ship?", this answers "can these two trees be joined, and
if not, where?".

The simulation worktree is removed on every path, including failures, and tests
assert `main` neither moves nor ends up holding conflict markers afterwards.

---

## 1. The mistake to avoid first

The obvious design is one flag: `use_worktree: bool`. It is wrong, because it
collapses four independent decisions into one:

1. **Isolation kind** — what the agent's files physically are
2. **Durability** — whether the work survives a crash
3. **Base** — what the work starts from
4. **Lifecycle** — who removes it, and when

Alpha already proved the right shape. `rsi/workspace.py:90-93` models a workspace
as `kind: Literal["worktree", "copy"]` plus `assurance: Literal["standard",
"lower"]`, and the copy fallback is recorded as `assurance="lower"` **with the real
error text**, never silently downgraded (`:9-20`). That is the pattern to copy: a
strategy is a *named, labelled* choice, and a degraded one says so.

---

## 2. The strategies

### A. `worktree` — isolated git worktree *(the default)*

```
git worktree add -b agent/<id>/<task> .worktrees/<hash> <base>
```

- **Use when:** the task changes tracked files and may be merged.
- **Guarantees:** real branch, real `git diff`, mergeable, crash-recoverable if
  committed, shares one object database (cheap).
- **Cost:** one checkout on disk. `.git/index` is per-worktree, so writes are isolated.
- **Existing:** `WorktreeManager.create_worktree` (`sandbox/worktrees.py`).

### B. `sandbox` — container/micro-VM, optionally with a worktree

Local → Docker (`aio_sandbox`) → E2B / BoxLite / Tenki.

- **Use when:** the task runs untrusted or third-party code, installs dependencies,
  or needs a network policy. **This is the only mode with a real host-filesystem
  boundary** — `sandbox/AGENTS.md:48` states plainly that the local provider *"is not
  a host filesystem security boundary."*
- **Guarantees:** depends on the provider. `LocalSandbox` isolates nothing;
  AIO/Docker/E2B do.
- **Cost:** a container per thread; slowest but only genuinely safe one.

### C. `copy` — bounded `shutil.copytree` snapshot

- **Use when:** git is unavailable, the tree is read-only, or a comparison needs two
  simultaneous states with no branch.
- **Guarantees:** **lower, and must be labelled so.** `rsi/workspace.py:213` records
  this as `assurance="lower"` and it flows into every evidence line.
- **Cost:** a full tree copy. No merge path.
- **Existing:** `rsi/workspace.py:213`.

### D. `ephemeral` — worktree removed on exit, contents disposable

Same as A, but lifecycle is "discard unless committed."

- **Use when:** a speculative probe, a formatting sweep, a throwaway experiment
  whose value is the *answer*, not the diff.
- **Guarantees:** none after exit. **Must be opt-in and labelled** — see §5.
- **Existing:** `worktree_context(..., discard_uncommitted=True)`.

### E. `integration` — throwaway worktree for merge verification

The plan's Phase 4, and the highest-value one.

```
git worktree add --detach .worktrees/integration/sim-<task> main
  -> git merge --no-commit --no-ff <candidate>
  -> run the suite
  -> git merge --abort ; git worktree remove
```

- **Use when:** deciding whether a branch may be merged. Never for implementation.
- **Guarantees:** `main` is untouched while a candidate is being judged.
- **Status:** **does not exist yet** — verified, no `--detach`/sim path in the tree.
- **Cost:** one transient checkout per candidate.

### F. `long_lived` — persistent, pinned, explicitly reclaimed

- **Use when:** an agent works the same task across sessions, or a human needs to
  inspect and edit inside the agent's tree.
- **Guarantees:** survives restarts *now that the manager rehydrates* (Phase 0a).
- **Risk:** these accumulate. They need an aging policy and a **grace period** —
  the plan's §42 cleanup sequence, not an immediate delete.

---

## 3. Choosing: a decision table

The agent-facing surface should **not** expose a free-form choice. The *task
classifier* picks, and the model is told which mode it got and why.

| Task signal | Mode | Why |
|---|---|---|
| Edits tracked files, results must merge | **A `worktree`** | Branch + diff is the merge contract |
| Runs untrusted/3rd-party code or installs deps | **B `sandbox`** (+A if merging) | Only real host boundary |
| Read-only analysis, no git | **C `copy`** | No branch needed; label `assurance=lower` |
| Probe/format/sweep, diff is disposable | **D `ephemeral`** | Cheapest correct answer |
| Judging whether a branch may merge | **E `integration`** | `main` stays untouched |
| Task spans sessions, or a human must enter the tree | **F `long_lived`** | Persistence is the point |
| git unavailable or base ref unresolvable | **C `copy`** + `assurance=lower` + verbatim error | Never silently degrade to a shared dir |

**The rule that matters:** a degraded mode must be *disclosed*, following
`rsi/workspace.py:9-20`. Falling back from `worktree` to a plain shared directory
is never acceptable — that is precisely the multi-agent failure the whole design
exists to prevent.

---

## 4. What the model should see

Per the repo rule that absent is never coerced into a value, the mode must be
**stated**, not implied by a path:

```
Isolation: git worktree (branch agent/042/TASK-1028, base 8f14e3c)
Your changes are isolated. Nothing you do affects another agent's files.
```

And on degradation, the reason travels with it:

```
Isolation: copy snapshot — assurance LOWER
Reason: git exited 128: unable to access '.git': permission denied
Consequence: you cannot commit, diff, or merge from this directory.
```

That last block is the whole point. A silently-lower-assurance workspace is how an
agent spends twenty turns trying to `git commit` in a tree that was never a repo.

---

## 5. Two things to get right before building

**1. `D ephemeral` must be opt-in and labelled.** After Phase 0 the cleanup path
refuses to destroy uncommitted work, which is exactly what makes `ephemeral` an
*explicit* choice rather than the default. A mode whose defining property is "loses
work" must be named in the task record, never inferred from a missing branch.

**2. Do not build a new registry.** Alpha already has `WorktreeManager`,
`rsi/workspace.py`'s kind/assurance model, `sandbox_authz`, and the
`authz` permission system. A new `worktree_strategy` subsystem would be a fourth
place where worktree policy lives — and `sandbox/AGENTS.md` shows exactly how that
goes wrong when the docs and the schema drift. **Extend `rsi/workspace.py`'s
vocabulary and `WorktreeManager`; do not parallel them.**

---

## 6. Effort and sequencing

| Step | What | Effort |
|---|---|---|
| 1 | Mode vocabulary + `reason` enforcement | **done** |
| 2 | Classification table as code, with a test per row | **done** |
| 3 | Disclosure block (§4) + honesty tests | **done** |
| 4 | `E integration` simulation worktree, three honest outcomes | **done** |
| 5 | Carry `mode`/`assurance`/`reason` onto the persisted `TaskRecord` | S |
| 6 | Feed simulation results into `evaluate_release_gate` (plan Phase 4) | M — **blocked on a decision** |
| 7 | `F` aging policy with grace period | S |
| — | `B sandbox` | **already exists** — reuse, don't rebuild |
| — | `A`, `C`, `D` | **already exist** — label and select, don't rebuild |

Five of the six are already implemented. The work is **choosing, labelling, and
disclosing** — not constructing. That is the recurring finding of this whole
investigation, and it is worth stating plainly to whoever picks this up: the
inventory of missing git *code* is much shorter than the inventory of missing git
*decisions*.
