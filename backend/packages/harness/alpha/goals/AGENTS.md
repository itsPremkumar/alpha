# Goals owner-scoped contract

- `models.py` defines strict, frozen `GoalContract`, `PlanVersion` and
  `TaskAttempt` records. `store.py` owns the file-backed state machine;
  `GoalsRepository` is an alias of `GoalStore`, not a separate execution engine.
- Every operation requires a non-empty `owner_id`. Snapshots use the exact
  owner's SHA-256 filename beneath the configured storage directory; records
  and parent references are resolved only within that owner's snapshot.
  Callers must supply trusted server identity, never payload-selected ownership.
- Contracts transition from `active` to `achieved` or `abandoned`. Plans have
  consecutive per-contract versions; approving a draft supersedes the prior
  approved plan. Attempt creation/start requires an active contract and approved
  plan. Attempts move `pending` to `running` or `cancelled`, then `running` to
  `succeeded`, `failed` or `cancelled`; terminal attempts cannot restart.
  Already-running attempts may finish after plan supersession or contract closure.
- Each operation reloads its snapshot under the store instance's `RLock`.
  Writes use a same-directory temporary file, flush, file `fsync` and atomic
  replacement; persistence errors propagate. Locks are per instance only:
  separate instances/processes targeting the same files can lose updates.
  Atomic replacement is not cross-process coordination or directory-fsync durability.
- Missing files start empty. Corrupt UTF-8/JSON, duplicate keys, invalid schemas,
  owner mismatches and invalid record relationships raise `StoreCorruptionError`;
  reads and writes fail closed without replacing the corrupt file.
- Gateway integration: `backend/app/gateway/routers/goal_contracts.py` resolves
  authenticated owners, rejects PATs, applies run permissions and offloads file
  I/O. It stores under the resolved user's `goal_contracts` directory. This
  package records lifecycle state; it does not execute tasks or verify evidence
  merely because an attempt is marked `succeeded` or a contract `achieved`.
- Regressions: `backend/tests/test_goal_contracts.py` and
  `backend/tests/test_goal_contracts_routes.py`.
