# Backend scripts (`backend/scripts/`)

Operator-facing probes and gates. They are development tools: they talk to a
running Gateway over HTTP (or boot a throwaway one), never import `app.*`, and
are not part of the runtime.

## Subagent probes

Both probes are **attach-mode**: they do not boot a Gateway by default. Point
them at a running one (`--base-url`, default `http://127.0.0.1:8001`; optional
`--token` / `ALPHA_SUBAGENT_PROBE_TOKEN` for auth-enabled deployments).

### `subagent_lifecycle_probe.py` — creation lifecycle

Exercises managed-subagent create/run/delete through the Gateway admin API.
Findings and method: [`docs/audits/SUBAGENT_CREATION.md`](../../docs/audits/SUBAGENT_CREATION.md).

### `subagent_delegation_probe.py` — delegation telemetry (W1 of the subagent plan)

One reproducible command that dispatches a pinned scenario catalogue through the
delegation surfaces (`task`, `batch_task`, control spawn) and writes a report
joining: outcome, stop reasons, tokens (per model and per child), wall time,
queue wait, acceptance verdicts, receipts, artifacts, and event persistence —
each field measured or `null`.

```bash
# from backend/
uv run --no-sync python scripts/subagent_delegation_probe.py            # full catalogue
uv run --no-sync python scripts/subagent_delegation_probe.py --list     # scenario catalogue
uv run --no-sync python scripts/subagent_delegation_probe.py --scenarios direct_answer,single_delegation
```

Reports land in `backend/.alpha/subagent_probe/<run-id>/` (`report.json` +
`report.md`); `--out-dir` moves them. Exit codes are the contract: `0` complete,
`1` incomplete (a scenario was never measured, or the Gateway died mid-probe),
`2` schema violations.

**The `autonomous` opt-in is load-bearing.** Every lead-agent run is created
with `autonomous=true` (`RunCreateRequest.autonomous` — the same switch the Web
UI sends when its delegation mode is on). The server applies
`subagent_enabled` only *after* ordinary client context is sanitized; without
the opt-in the `task` tool is absent from the toolset and a zero-delegation
measurement is a property of the request, not of the model. This was measured
the hard way on 2026-10-17: the first probe runs recorded 0 delegations and the
model's own transcript said it searched for `task` and could not find it.

**Honesty rules the report enforces.**

- Unmeasured stays `null`, never `0`/`""`/`[]`; `queue_wait_seconds` is
  `UNINSTRUMENTED_FIELDS` until plan W3.a instruments it, and the report carries
  a note saying so.
- `refused` (batches disabled on this deployment, a managed-spawn refusal) is a
  recorded outcome, not a probe failure — it does not affect the exit code.
- A surface the deployment cannot reach is `incomplete` (an honest "could not
  measure"), not `unmet`.
- The probe measures **plumbing, not intelligence**: it makes no claims about
  model quality.

**A dead target still produces a report.** The scenario loop converts any
transport failure — a request that cannot connect, a stream that breaks
mid-run — into `incomplete` and stops; scenarios never attempted are listed by
name in `incomplete_reason`. The trailing version read
(`GET /api/ops/version`) is best-effort and can never turn a finished run into
a traceback with nothing on disk. Measured 2026-10-17: two full-catalogue runs
were interrupted by host events (host-wide TCP ephemeral-port exhaustion; the
self-healing watchdog restarting the port-8001 tree mid-probe). The first
exposed the then-unguarded version read; the second produced an honest
`incomplete` report naming the transport reason, a behavior now pinned by
`test_d4_a_target_that_dies_mid_probe_still_yields_a_written_report`.

**Contracts.** The report schema is the pure module
`scripts/support/subagent_probe_report.py` (dataclasses, `to_jsonable`,
`validate_report`, `report_exit_code`, `render_markdown`). The probe validates
its own report before writing; schema drift is exit `2`. Offline coverage lives
in `backend/tests/test_subagent_delegation_probe_schema.py` (round-trip,
null-honesty, the delete-a-captured-field negative control, exit-code mapping,
evaluator behavior).
