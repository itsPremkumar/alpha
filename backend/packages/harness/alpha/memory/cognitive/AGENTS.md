# Cognitive memory owner-scoped contract

- `engine.py:41` resolves production storage through `Paths.user_dir(owner)` to
  `users/{owner}/cognitive_memory`; no owner means failure, not a shared default.
  Gateway and tool entry points must supply trusted server/runtime identity,
  never HTTP/model-provided owner or storage-path overrides.
- `engine.py:342` caches by owner and directory under a process lock. Use
  `system.operation()` for read/mutate/save sequences: its instance `RLock`
  serializes operations and restores in-memory state on exceptions. Explicit
  `storage_dir` calls return independent instances outside the owner cache;
  supplying both `storage_dir` and `user_id` is invalid.
- `engine.py:117` saves via same-directory temporary file, flush, file `fsync`
  and atomic replacement. Write errors propagate; corrupt-state parse/load
  errors fail closed without bootstrapping over the file. This is not exhaustive
  schema validation or a directory-fsync guarantee.
- Persist all retained persistent records, not display pages. Skills, events,
  traces, episodes, edges and associations use complete retained collections;
  semantic persistence still caps at 2,000, matching `semantic_graph.py:23`'s
  default enforced capacity. Raising capacity requires updating that snapshot
  limit. Working memory remains ephemeral and owner-scoped, not thread-isolated.
- Single-process contract only: no cross-worker cache coherence or file locking.
  Independent instances targeting the same directory do not share a lock; atomic
  replacement alone cannot prevent lost updates. Legacy shared snapshots are
  not automatically imported; migration and broader tool exposure remain open.
- Entry points: `backend/app/gateway/routers/memory.py:579` and
  `backend/packages/harness/alpha/tools/builtins/cognitive_memory_tool.py:29`.
  Regressions: `backend/tests/test_cognitive_memory_isolation.py` and
  `backend/tests/test_cognitive_memory.py`.
