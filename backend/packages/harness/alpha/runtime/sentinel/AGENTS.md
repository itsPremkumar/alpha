# Sentinel (`alpha.runtime.sentinel`)

Alpha's autonomous repair loop: observe a fault, decide whether a fix is safe,
apply it behind a checkpoint, re-check it, then commit or revert. What it
cannot close goes to a human.

This guide owns the module's contracts. The operator-facing page is
[`docs/SENTINEL.md`](../../../../../../docs/SENTINEL.md) — follow the pointer
rather than restating it here.

## Ownership

| File | Owns |
| --- | --- |
| `signals.py` | The `Signal` model, normalisation, fingerprinting, `SignalTracker` (attempt cap + cooldown), dedupe and severity ordering. |
| `sources/scripts.py` | The PowerShell BOM detector, with `SKIP_DIR_NAMES` pruned **during** traversal. |
| `sources/logs.py` | The log scanner: only *structural* reports of a fault count. |
| `runner.py` | The entry point a scheduler, cron routine or CLI drives. Bounded per run. |
| `loop.py` | The five stages, the checkpoint-before-edit ordering, and `KNOWN_KINDS`. |
| `verify.py` | Running the declared verification commands and interpreting them. |
| `commit.py` | Local commit only. There is no auto-push. |
| `checkpoint.py` | The rollback snapshots a fix must take **before** it edits. |
| `scheduler.py` | Executing registered bot routines (`sentinel.run_once`). |
| `report_store.py` | The durable append-only JSONL journal of every pass. |
| `analytics.py` | Folding that journal into one reading. Pure — no I/O. |
| `cli.py` / `__main__.py` | The one-shot command-line entry point. |

## The three load-bearing invariants

1. **A red or unknown verification result reverts and escalates. It never
   commits.** `loop.py::SentinelLoop.handle` restores the checkpoint on any
   failed check and on any failed commit, and reports `reverted` with the
   reason.
2. **A fix snapshots before it edits.** `fix_fn(signal, snapshot)` must call
   `snapshot(paths)` first. A fix that edited without snapshotting is refused —
   the loop will not continue with a repair it may be unable to undo. This
   ordering is the whole point; the earlier code snapshotted *after* the fix,
   which captured the broken content and made "revert" restore the failing
   state.
3. **An unknown kind escalates.** `diagnose` routes on a declared list. A kind
   outside it is never guess-fixed. Adding a repair for a new kind means adding
   it to both `KNOWN_KINDS` and the default fix set.

## The fold (`analytics.py`)

`aggregate(entries, limit=, total_on_disk=)` is **pure and I/O-free**. It takes
the entries a read already produced and returns a frozen
`SentinelAnalytics`. Because it performs no I/O it cannot fail the way a store
read fails, and it is testable without a filesystem — which is why the Gateway
can fold a journal inside `asyncio.to_thread` without adding a failure mode.

Four rules, each with a tempting wrong reading:

- **A value nobody measured is `None`, never `0`.** `scanned_total` is `None`
  when no pass reported one; a partial read sums the reporters and discloses
  how many did not report.
- **A count of rows and a count of measurements are different numbers.**
  `outcome_count` counts outcome dicts that exist; `passes_without_outcomes`
  counts passes that carried none. Collapsing them turns "the engine recorded
  nothing per-signal" into "the engine saw no signals".
- **A verdict cites evidence.** `KindReading.verdict` is the *best* status that
  kind ever reached, so a kind with one fix beside nine escalations is
  `repaired` — with the nine visible in the counters beside it. A kind that only
  ever skipped is `deferred`, which is neither a failure nor a success. A
  missing status is `status_missing` and is kept distinct from an unrecognised
  word in `unknown_statuses`.
- **Every reading names its population.** `build_disclosures` emits the folded
  count, the dropped-by-cap count, and every absent field.

## The journal (`report_store.py`)

One append-only file at `runtime_home()/sentinel-reports/reports.jsonl`. Reads
fail closed (a corrupt line is reported with the file and line, never repaired,
skipped or rewritten); writes fail closed *before* the bytes land (validate,
then append under an advisory byte-range lock with `flush` + `fsync`, so a
returned success means the record is durable). The file is never pruned.

## The Gateway surface

`app/gateway/routers/autonomy.py`. Every read fails closed: `/reports` and
`/analytics` answer 500 naming the file and line, `/escalations` answers 503
naming the store — never an empty body standing in for an unreadable one. The
two decision routes are admin-gated, re-read the record after the write, and
refuse a caller-sent actor that disagrees with the authenticated identity.

The supervisor loop adapter is `app/gateway/autonomy/loops.py::sentinel_tick`,
and `_persist_sentinel_report` is the single choke point every call site
(supervisor tick, manual API pass, CLI) journals through. A write failure is a
logged disclosure, never a fabricated entry.

## What is not implemented

- **No auto-push.** The committer commits locally and stops.
- **No cross-process exactly-once.** The journal, the escalation store and the
  process-local signal state are atomic and restart-recoverable for one Gateway
  process. Multi-worker deployments need a shared store and a recovery protocol.
- **One default repair.** `missing_bom`. Every other kind escalates.
- **The loop is disabled by default.** Absent from `config.yaml ->
  autonomy.loops` means off.
