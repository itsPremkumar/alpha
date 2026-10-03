# ReasoningBank (`packages/harness/alpha/reasoning_bank/`)

Durable, evidence-gated procedure memory — the harness-level answer to
"what worked for something like this before?" It is Alpha's local face of
the ICLR-2026 "reasoning memory" (ReasoningBank) line of work: strategies
that worked are stored, reinforced by measured outcomes, and recalled
deterministically for similar future tasks.

**What is stored** (`bank.py::ReasoningRecord`, v1):
`strategy_id` (sha1 of scope+trigger+strategy, stable per strategy),
`scope`, `trigger` (bounded ≤240 chars), `strategy` (bounded ≤1600 chars),
`verdict` ∈ `success|partial|failure|unknown`, `attempts`/`wins`/`losses`/
`unknowns` (measured counters), `evidence_ref`, `tags`, `fingerprint`,
`created_at`/`updated_at`.

**Contract**:

- A `"success"` verdict without a non-empty `evidence_ref` is demoted to
  `"unknown"` at record time — a win that cannot be pointed at is not a
  win. Win rate is always `wins / attempts` over measured verdicts, never
  a self-asserted number.
- Recall is deterministic and bounded: score = exact scope match ×3, tag
  overlap, token overlap on (trigger, strategy, scope, tags) ×2; ties
  break by win rate, then recency. Hard `limit`, and `render()` never
  exceeds `max_chars`. No vector store, no network, no model call.
- A failed strategy stays recallable (so future runs can see what not to
  do) unless `include_failures=False`; it never ranks above a winning
  strategy because its win rate is zero.
- Storage follows the `ClaimStore` discipline: `threading.Lock`, atomic
  tmp+replace save, a corrupt file loads as empty with a warning, and
  `prune(max_records=…)` keeps only the best-performing records.
- `get_reasoning_bank()` is a process-wide singleton that rebuilds when
  `runtime_home()` moves; explicit `get_reasoning_bank(path)` seeds an
  isolated store (used by tests).

**Production consumer**: `tools/builtins/self_improvement_tool.py::ralph_loop_tool`
records each round's measured verdict (`success` only when the
completion promise's acceptance verdict fully holds; `partial` when the
round completed unchecked; `failure` otherwise), and seeds round 1 of a
later loop with the bank's bounded top match for the same scope. The same
records are retrievable by future callers — nothing is private to the
ralph tool. A bank failure never fails a ralph round.

**Canonicalization note**: `strategy_id` is a content fingerprint, so the
same `(scope, trigger, strategy)` text upserts instead of duplicating.
`trigger`/`strategy`/`evidence_ref` are length-capped at record time.

Tests: `tests/test_reasoning_bank.py`, `tests/test_self_improvement_tool.py`.
