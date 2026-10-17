# `alpha.tasks` — the canonical task contract

One neutral vocabulary that every orchestration engine projects onto, so a task
can be described, budgeted, handed off and verified the same way whichever
engine ran it.

This package is **additive**. It is not a scheduler and it owns no lifecycle.

## The problem it exists to solve

Alpha grew **seven** schedulers and **three** incompatible task vocabularies:

| Engine | Model | Status vocabulary |
|---|---|---|
| `alpha.workflow` | `WorkflowNode` | pending, ready, running, waiting, succeeded, failed, retrying, skipped, cancelled, suspended, compensating, aborted |
| `alpha.swarm` | `SwarmTaskNode` | `state: TaskNodeState` (7) **plus** `SwarmPlan.status`: a bare string of 11 live values at `swarm/models.py`, enumerated by `TERMINAL_SWARM_STATUSES` ∪ `NON_TERMINAL_SWARM_STATUSES` |
| `alpha.graph` | `TaskNode` | pending, ready, queued, running, waiting_approval, blocked, succeeded, failed, cancelled, skipped |

Nothing translated between them. Swarm alone carries **two** vocabularies —
the typed enum on `SwarmTaskNode` and the status string on its plan. A run
started in one engine could not be observed, budgeted,
resumed or verified in another. The source plan's own §31 ("What Not to Build
First") names this failure: *"multiple overlapping orchestration frameworks
running the same task"*.

## What this package owns

* **`contracts.py`** — `TaskContract`, `TaskContractBudget`,
  `TaskContractStatus`, `TaskContractType`, `UNBOUNDED`, `CONTRACT_VERSION`, and
  the refusal types `ContractValidationError` / `UnmappableStatus`.
* **`adapters.py`** — one projection per engine
  (`graph_task_to_contract`, `workflow_node_to_contract`,
  `swarm_task_to_contract`) plus the two status maps.

## What this package does NOT own

* **Run lifecycle.** `RunManager` (`alpha.runtime.runs.manager`) is the sole
  owner. Nothing here creates, cancels or terminalizes a run.
* **Acceptance.** `alpha.mission.acceptance` remains the only producer of a
  terminal *passed*, and `alpha.mission.acceptance.assert_acceptance_passed`
  remains the only writer of an accepted outcome. A `TaskContract` carries
  `acceptance_criteria` as **declarations**, never verdicts.
* **Error codes.** `alpha.errors.registry` is the sole owner.
* **Scheduling.** Each engine keeps its own scheduler and its own task model.
  The contract is a projection target, not a seventh scheduler.

`tests/test_task_contract.py::TestPackageBoundary` asserts the first three
against the **parsed AST** of every module in the package. A text grep cannot
do this job: these owners are *named* in the docstrings precisely to record who
owns what, so a substring scan would flag the documentation as though it were
an import.

## Five load-bearing invariants

1. **An unmappable status is a refusal, never a default.** A projection that
   guesses `pending` for a status it does not recognise turns an unfamiliar
   state into a task that looks **ready to run**. `UnmappableStatus` raises with
   the engine *and* the offending value named, because "invalid status" sends an
   operator to the wrong subsystem.
2. **The status maps are exhaustive by construction.** Each adapter covers
   **every** member of its engine's live enum, and the tests enumerate those
   enums and fail on a gap. A gap turns an ordinary state change into a runtime
   refusal in the middle of a live run.
3. **The vocabulary is the narrowest common core, not the union.** A union
   would drift toward whichever engine grew last. That is why `native_status`
   and `native_type` exist: they preserve what the engine actually said, so a
   projection is **lossless** rather than lossy-and-pretending-otherwise.
   `SwarmTaskNode`'s `completed` becomes `succeeded` in the contract and comes
   back as `completed`.
4. **Bounds are refused, not clamped.** `max_attempts=0` raises rather than
   becoming 1. `max_cost_usd=0.0` is a **real ceiling** meaning paid calls are
   blocked — never a stand-in for "unknown", which is the source plan §5.2's
   explicit warning. `None` is *unstated* and `UNBOUNDED` is *explicitly
   unbounded*; they are different values on purpose.
5. **The contract is frozen.** `with_status()` / `with_attempt()` return new
   values rather than mutating, so two engines can hold different views of one
   task without either writing over the other.

## Honesty boundary

This package does **not** claim:

* **That the engines now share a scheduler.** They do not; they share a
  vocabulary. Unifying the schedulers is later work.
* **That a `TaskContract` is verified.** `acceptance_criteria` is a
  declaration. An empty list means *undeclared*, never *satisfied*.
* **Cross-process anything.** The contract has no store and no clock.

## Feature flag

None. The contract is a pure data shape, so it is always importable; the
adapters are the only place an engine's model is read. Engine selection stays
where it already is (`ALPHA_GRAPH_EXECUTION_ENABLED` for the graph plane, the
DWE's own opt-in, `gro.enabled` for GRO).

## Tests

`backend/tests/test_task_contract.py`:

* contract shape, attribution and `extra="forbid"` boundaries;
* budget refusals and the zero-cost-is-a-ceiling rule;
* derived-vs-declared state (`is_terminal`, `satisfies_dependency`,
  `is_blocked`, `needs_approval`);
* self-dependency refusal and duplicate-dependency dedup;
* **exhaustiveness of all three status maps** against live enums;
* **unmapped-status refusal** naming engine and value;
* round-trip fidelity through each real adapter;
* the package-owns-no-lifecycle grep boundary.

## Wiring

Declared in `alpha.capabilities.catalog` as `task_contract` and
`task_contract_adapters` (kind `utility`), which is what satisfies
`tests/test_no_orphan_modules.py` and surfaces it on
`GET /api/ops/integration-health`.
