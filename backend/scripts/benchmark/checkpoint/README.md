# Checkpoint channel benchmarks

Protocol, commands and operational limits for the checkpoint `full`/`delta`
channel benchmarks in this directory. Run every command from `backend/`.


**Checkpoint channel benchmark**: `scripts/benchmark/checkpoint/bench_channels.py`
runs paired `full`/`delta` message-only StateGraphs in a fresh child process per
case, using sync `InMemorySaver` or `SqliteSaver` so reducer, serialization, and
saver costs stay separate from Gateway/async scheduling. Optional
`AsyncPostgresSaver` cases are enabled only when `TEST_POSTGRES_URI` is set.
Postgres cases use a unique thread and remove only that benchmark thread through
the saver's public `adelete_thread` API after measurement. It reports deterministic
correctness digests, write windows/percentiles, warm and graph-rebuilt cold reads,
backend-neutral checkpoint/blob/write row and byte fields, aggregate logical
checkpoint/write bytes, SQLite DB/WAL/SHM footprint, reducer replay time, and
peak RSS as versioned JSONL. SQLite embeds channel blobs in its checkpoint
payload, so its separate blob metrics are zero; Postgres reports its
`checkpoint_blobs` table separately. Byte fields describe each saver's serialized
representation and should not be treated as identical encodings across backends.
The controller alternates mode order and rejects
performance data when paired modes materialize different state. Its default 1 GiB
estimated cumulative full-payload cap skips both modes of an oversized pair when
`full` is selected, including every delta cadence in a `--snapshot-frequencies`
sweep; intentional `--modes delta` diagnostics bypass this full-payload cap, so
size those runs explicitly. Use `--allow-large-cases` only
on a provisioned machine. Duplicate CSV matrix values are ignored with a warning;
use `--repetitions` for repeated samples. Summarize paired successful repetitions
with `scripts/benchmark/checkpoint/summarize_channels.py` (all ratios are
`delta/full`). `--profile-dir /tmp/checkpoint-profiles` writes one cProfile
artifact per case for attribution. Profiled rows carry `profiled: true`, and the
summarizer automatically excludes them from baseline summaries with a warning.
Storage-size collection relies on saver-specific diagnostic layouts; if those
layouts change, the timing/correctness row remains successful while storage
fields become `null` and `storage_stats_error` records the diagnostic failure.
Example:

```bash
cd backend
PYTHONPATH=. uv run python scripts/benchmark/checkpoint/bench_channels.py \
  --backends sqlite --updates 100,500,999,1000,1001 --payload-bytes 128 \
  --repetitions 7 --output /tmp/checkpoint-bench.jsonl
TEST_POSTGRES_URI=postgresql://... \
PYTHONPATH=. uv run python scripts/benchmark/checkpoint/bench_channels.py \
  --backends sqlite,postgres --updates 100 --payload-bytes 128 \
  --output /tmp/checkpoint-cross-backend.jsonl
PYTHONPATH=. uv run python scripts/benchmark/checkpoint/summarize_channels.py \
  /tmp/checkpoint-bench.jsonl
```

The production-shaped layer lives in
`scripts/benchmark/checkpoint/bench_production.py`: per-case child processes
run graph-level `ainvoke` turns through the real lead-agent graph (scripted
deterministic model, real `AsyncSqliteSaver`), then measure
`GET /threads/{id}/state` and `POST /threads/{id}/history` through the real
Gateway route stack in the same event loop (httpx ASGITransport), split into
cold/warm accessor-graph-cache samples. It sweeps `snapshot_frequency`
(config: `checkpoint_delta.snapshot_frequency`, process-frozen like the mode),
pairs every delta frequency against the same full row, and fails both
rows of a pair when materialized or wire digests diverge. Each case must have
more than the two discarded warm-up turns, and SQLite DB/WAL/SHM sizes are
captured while the saver is still open so they represent the online storage
footprint. Summarize with
`scripts/benchmark/checkpoint/summarize_production.py` (ratios are
`delta/full`; it also emits `snapshot_write_spike` and `cache_effect_ms`,
the decision inputs for the production snapshot-frequency and accessor-cache
defaults). Harness tests live in `tests/test_bench_checkpoint_production.py`
and `tests/test_summarize_checkpoint_production.py`; timing thresholds are
not CI gates. The matrix test pins that every `(repetition, turns)` group
contains both modes and that their execution order flips between consecutive
groups, including across repetition boundaries.

Operational limits learned from the first runs (the default matrix is too
large to run blindly):

- The default `--timeout-seconds 900` is insufficient for delta mode at
  `snapshot_frequency=1000` once turns reach 500 (measured: delta-500 takes
  ~1100-1200s; delta-2000 takes ~45min). Pass an explicit
  `--timeout-seconds` for any large matrix, and treat the turns=2000 corner
  as practical only at small snapshot frequencies.
- Full-mode 2000-turn runs produce a ~33GB sqlite DB. Point `TMPDIR` at real
  disk, not tmpfs (the benchmark uses `tempfile.TemporaryDirectory`, which
  honors `TMPDIR`), or the run dies mid-case.
- The history route clamps `limit` to 100 (`le=100` on
  `ThreadHistoryRequest.limit`), so `--history-limits` values above 100 are
  measured and reported by their effective (clamped) limit.

Example:

```bash
cd backend
PYTHONPATH=. uv run python scripts/benchmark/checkpoint/bench_production.py \
  --turns 10,100,500,1000,2000 --payload-bytes 128 \
  --snapshot-frequencies 10,50,100,500,1000 \
  --repetitions 7 --output /tmp/production-bench.jsonl
PYTHONPATH=. uv run python scripts/benchmark/checkpoint/summarize_production.py \
  /tmp/production-bench.jsonl
```
