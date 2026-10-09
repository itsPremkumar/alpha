# Sentinel

The Sentinel is Alpha's autonomous repair loop. It observes faults in the
things Alpha runs on — PowerShell scripts, gateway logs, test output — decides
whether a fix is safe, applies it behind a checkpoint, re-checks it, and either
commits or reverts. What it cannot close, it hands to a human.

This page is the operator's guide. The normative contracts live beside the
code:

| Concern | Owner |
| --- | --- |
| The fold (`analytics.py`) | [`backend/packages/harness/alpha/runtime/sentinel/AGENTS.md`](../backend/packages/harness/alpha/runtime/sentinel/AGENTS.md) |
| The journal (`report_store.py`) | the module docstring in `report_store.py` |
| Signal sources | `sources/scripts.py`, `sources/logs.py` |
| The loop (observe → fix → verify → commit) | `loop.py` |
| The HTTP surface | `backend/app/gateway/routers/autonomy.py` |
| The UI | `frontend/src/components/sections/SentinelSection.tsx`, `frontend/src/lib/sentinel.ts` |

## What it is safe to assume

Three sentences carry most of the contract:

1. **A repair never commits on a red or unknown check.** The loop checkpoints
   the files a fix is about to touch *before* editing them, runs the declared
   verification commands, and reverts if any of them fails. A commit failure
   reverts too. See `loop.py`.
2. **An unknown fault kind is escalated, never guess-fixed.** `SentinelLoop.
   diagnose` routes on a declared kind list; anything else stops at `diagnose`
   with status `escalated` and is published to the human handoff ledger.
3. **A pass is bounded.** `max_fixes_per_run` (default 5) caps how much one
   pass may change, and `SignalTracker` gives up on a fingerprint after
   `max_attempts` (default 3) and holds a cooldown between tries.

Two limits stay true and must stay stated:

- **Auto-push is off.** The committer commits locally and nothing more.
- **The engine records what it did, not whether the system is now healthy.**
  A pass that reports `fixed: 3` says three repairs were verified and
  committed. It does not say the underlying problem is gone — a recurring
  fingerprint is exactly the evidence that it is not.

## The two modes

| Mode | `auto_heal` | What it does |
| --- | --- | --- |
| **Observe** (default) | `false` | Collects signals and reports them. No repair function is registered at all, so **every signal escalates**. |
| **Repair** | `true` | Wires the default verified repair strategies and the verification commands, then runs the loop. |

The observe-mode sentence is the one most often misread. An observe pass that
reports `escalated: 12` has not found twelve unfixable faults — it has found
twelve signals and registered no repair for any of them, because that is what
observe means. The `/sentinel/kinds` route states it in its disclosures for
exactly this reason.

## The HTTP surface

All routes sit behind the Gateway's default `AuthMiddleware`. The two decision
routes add an `is_admin_user` gate on top of it (a Personal Access Token never
qualifies).

| Route | Verb | What it returns |
| --- | --- | --- |
| `/api/autonomy/sentinel/signals` | GET | Observe-only collection. No diagnosis, no fixes, no commits. |
| `/api/autonomy/sentinel/reports?limit=N` | GET | Capped read of the durable journal, oldest first. **500 with the file and line** when the journal is unreadable. |
| `/api/autonomy/sentinel/run` | POST | One real pass. `auto_heal` is always sent explicitly. |
| `/api/autonomy/sentinel/analytics?limit=N` | GET | The journal folded into one reading (see below). Fails closed like `/reports`. |
| `/api/autonomy/sentinel/kinds` | GET | The declared fault-kind registry and which kinds carry a repair strategy. |
| `/api/autonomy/sentinel/escalations?limit=N` | GET | Human handoffs, oldest first. **503 with the reason** when the store is unreadable. |
| `/api/autonomy/sentinel/escalations/{id}/acknowledge` | POST | Admin. Marks a handoff as seen. Resolves nothing. |
| `/api/autonomy/sentinel/escalations/{id}/resolve` | POST | Admin. Records the decision with a note. Changes no engine state. |

`limit` is 1..200 everywhere; an out-of-range value is a 422, never a silent
clamp.

### The aggregate reading

`/sentinel/analytics` answers the four questions a raw journal cannot:

- *Is this getting better or worse?* — totals across the folded window.
- *Which fault keeps coming back?* — a per-kind roll-up with first/last seen.
- *Is the engine repairing anything, or escalating everything?* — a per-kind
  **verdict** derived from the statuses that kind actually reached.
- *What has a human never looked at?* — the kinds that escalated most, and the
  fingerprints that recurred.

The fold is pure (`aggregate(entries)` performs no I/O) and it discloses its own
window. Four rules make an aggregate trustworthy, and each has a tempting wrong
reading:

| Rule | The failure it stops |
| --- | --- |
| A value nobody measured is `null`, never `0`. | `scanned_total: 0` reads as "the engine saw no signals" when the truth is "nobody measured". |
| A count of rows and a count of measurements are different numbers. | `outcome_count` counts outcome dicts; `passes_without_outcomes` counts passes that carried none. Collapsing them turns "the engine recorded nothing per-signal" into "the engine saw no signals" — opposite findings. |
| A verdict cites evidence. | A kind with one repair and nine escalations is `repaired`, with the nine visible in the counters beside it. The word never does the numbers' work. |
| Every reading names its population. | `disclosures` says how many passes were folded, how many a cap excluded, and which fields were absent. |

Verdict vocabulary, worst-last (the verdict is the *best* thing that ever
happened):

| Verdict | Meaning |
| --- | --- |
| `repaired` | At least one outcome for this kind reached `fixed`. |
| `reverted` | No fix, but a repair was attempted and rolled back. |
| `unrepaired` | Only escalations. The engine gave up. |
| `deferred` | Only skips — the attempt cap or cooldown declined to act. Neither a failure nor a success. |
| `unmeasured` | No outcome row carried a status this fold could read. |

## The UI

The **Sentinel** workspace view (`?view=sentinel`) renders the whole plane: the
folded reading with its disclosures, the fault-intelligence table, recurring
fingerprints, the declared kind registry, the human handoff queue, the pass
controls, and the raw journal.

The frontend client (`frontend/src/lib/sentinel.ts`) holds every sentence the
panel prints, so a KPI card and a table row cannot drift apart. Its rules:

- Absent optional values map to `null`, never `0`, `""` or `false`.
- Unknown enum strings (a verdict from a newer Gateway) render verbatim in a
  neutral badge, never snapped to a value this build knows.
- `failureText` keeps a refusal in the server's own words, because the shared
  `errMsg` paraphrases a 403 — right for an incidental read, wrong for the
  answer to a deliberate write.
- The repair pass is opt-in per click, off by default, with its safety model
  printed beside the control.

## Configuration

The supervisor loop is `sentinel` in `config.yaml`:

```yaml
autonomy:
  enabled: true
  loops:
    sentinel:
      enabled: true
      interval_seconds: 600
```

A loop absent from `autonomy.loops` is **disabled by default**. Flags off means
zero tasks — it does not mean the loop runs less often.

Live counters for every registered loop (including `sentinel`) are at
`GET /api/autonomy/status`.

## What is not implemented

Stated so nobody reads a capability into this plane that it does not have:

- **No auto-push.** The committer commits locally and stops.
- **No cross-process coordination.** The report journal, the escalation store
  and the process-local signal state are atomic and restart-recoverable for one
  Gateway process. They are not a shared multi-worker exactly-once repository.
- **One repair strategy.** `missing_bom` is the only kind with a default repair
  function. Everything else escalates.
- **No automatic evidence collection.** A human records the decision; the API
  does not infer one from a model summary.
- **The kind registry is a declaration, not a scan.** `/sentinel/kinds` reports
  what the code declares, never what a repository walk found.

## Tests

| Suite | Pins |
| --- | --- |
| `backend/tests/test_sentinel_analytics.py` | The fold's honesty rules: null-vs-zero, rows-vs-measurements, verdicts, verbatim unknown statuses, the disclosed window. |
| `backend/tests/test_sentinel_api.py` | The HTTP surface: real payloads, fail-closed reads, the cap, the admin gate, the actor check, the non-Sentinel 404. |
| `backend/tests/test_sentinel_report_store.py` | The journal's read/write fail-closed contract. |
| `backend/tests/test_sentinel_scan_coverage.py` | That the scanner can see the tree it exists to watch. |
| `backend/tests/test_sentinel_log_classifier.py` | That a line mentioning a fault word is not a line reporting a fault. |
| `frontend/src/lib/sentinel.test.mjs` | The client: routes, verbs, the null-preserving mappers, the local refusals, the derived headline verdict. |
| `frontend/src/lib/sentinel-view.test.mjs` | The four-place view wiring and every honesty claim the panel renders. |
