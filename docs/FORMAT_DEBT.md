# Format debt tracker

**The number in this document is only meaningful together with the ruff version
that produced it.** `ruff format` changes its output between releases, so a debt
count without a version is not a measurement — it is a number that moves. An
earlier revision of this file reported a single figure ("562 unformatted") with
no version and no way to reproduce it; it was wrong, and wrong in the direction
that made the debt look smaller than it was under the version the project
actually pins. Both versions are recorded below so either can be reproduced.

## How this is measured (reproducible)

Pinned to the two versions that matter: the `backend/pyproject.toml` floor
(`ruff>=0.14.11`) and the version `backend/uv.lock` resolves to, which is what CI
installs. Both are stated per section because the answer differs between them.

```powershell
# --- ruff 0.15.12: the version backend/uv.lock pins, i.e. what CI measures ---
cd <worktree>
& "<main-checkout>\backend\.venv\Scripts\python.exe" -m ruff --version   # -> ruff 0.15.12
& "<main-checkout>\backend\.venv\Scripts\python.exe" -m ruff format --check .      2>&1 | Select-Object -Last 1
& "<main-checkout>\backend\.venv\Scripts\python.exe" -m ruff format --check backend 2>&1 | Select-Object -Last 1

# --- ruff 0.14.11: the floor in backend/pyproject.toml, resolved explicitly ---
uv tool run ruff@0.14.11 --version
uv tool run ruff@0.14.11 format --check .      2>&1 | Select-Object -Last 1
uv tool run ruff@0.14.11 format --check backend 2>&1 | Select-Object -Last 1
```

`ruff format --check` exits 1 when anything would be reformatted, so wrap it in a
`$LASTEXITCODE` check rather than treating a non-zero exit as a measurement
failure. The per-area breakdown below is produced by filtering the
`Would reformat: <path>` lines:

```powershell
$lines = & "…\python.exe" -m ruff format --check . 2>&1 |
  Where-Object { $_ -like "Would reformat:*" } |
  ForEach-Object { ($_ -replace '^Would reformat: ','') -replace '/','\' }
($lines | Where-Object { $_ -like "backend\packages\harness\alpha\*" }).Count
```

The worktree has no provisioned venv and installing is out of scope, so the
read-only `--check` measurement (and `ruff format` itself) runs via the main
checkout's interpreter. No main-tree file is touched; only worktree files are
formatted.

## Measured 2026-09-29 at commit `f404989` (branch `agent/docstruth`)

Two columns because the tool version alone moves the count. The spread is small
here but it is not zero, and it is not stable across ruff upgrades — which is the
whole reason this document pins a version.

| Scope | ruff 0.15.12 (uv.lock / CI) | ruff 0.14.11 (pyproject floor) |
|---|---|---|
| Repo root `.` | **507** would be reformatted, 2825 formatted | **511** would be reformatted, 2821 formatted |
| `backend/` only | **497** would be reformatted, 2754 formatted | **501** would be reformatted, 2750 formatted |

Per-area breakdown of the root count:

| Area | ruff 0.15.12 | ruff 0.14.11 |
|---|---|---|
| `backend/packages/` (of which under `packages/harness/alpha/`) | 282 (279) | 283 (280) |
| `backend/tests/` | 196 | 198 |
| `backend/app/` | 14 | 14 |
| root `scripts/` | 9 | 9 |
| `backend/scripts/` | 2 | 3 |
| `installer/` | 1 | 1 |
| `backend/` root dotfiles (`head_projects`, `probe_import_isolation`, `mutation_evidence`) | 3 | 3 |

**The version spread is exactly four files, and they are named here** so the
difference can be re-derived rather than guessed at:

- `backend/packages/harness/alpha/agents/middlewares/token_budget_middleware.py`
- `backend/scripts/benchmark/sandbox/bench_provider.py`
- `backend/tests/test_e2b_sandbox_provider.py`
- `backend/tests/test_workflow_runtime_correctness.py`

Each is formatted by 0.15.12 and left alone by 0.14.11. Nothing else differs.
Anyone re-running this after a ruff upgrade should expect a *different* delta and
should re-name the files rather than reuse this list.

### Shape

Concentrated, not diffuse. Under both versions roughly 56% of the debt is the
harness core and ~39% is `backend/tests/`: one neglected core plus its test
suite, not a repo-wide habit.

## What CI actually does with this

CI does **not** gate on the whole-repository count. `.github/workflows/lint-check.yml`
runs `scripts/check_changed_python_lint.py`, which lints only the Python files a
revision changed, and a separate non-gating `ruff-debt-report` job publishes the
backlog via `scripts/ruff_debt_report.py`. The debt report is measured with
whatever ruff `uv sync --group dev` resolves from `backend/uv.lock` — 0.15.12 at
the time of writing — so **the 0.15.12 column is the one that describes CI**, and
the 0.14.11 column exists because `pyproject.toml` still permits it and a
contributor can legitimately produce the other number.

## Excluded on purpose — left alone

`scripts/ruff_scope.py` carries exactly one `EXCLUDED_FILES` entry:
`.agent/skills/blocking-io-guard/templates/anchor.template.py` — a copy-me
template with placeholder identifiers that are not valid Python. It is not in
the unformatted list and was never a candidate. Nothing else is excluded; every
other file in the counts above is genuine debt, not a documented decision.

Also deliberately untouched at the time of the batches below: files under
`models/`, `runtime/`, `subagents/`, `errors/`, `contracts/` (including
same-named subdirs inside `backend/packages/harness/alpha/`) and `frontend/**` —
owned by other agents per the then-current assignment. They remain in the
remaining list by ownership, not by gate exclusion.

## Batches done (formatting only — "satisfy the formatter", never "fixed")

### Batch 1: `backend/packages/harness/alpha/tools/` — 37 files

All of `builtins/` (32 files, action_transaction through wake_gate),
`code_mode/bridge.py`, `code_mode/tool.py`, `programmatic_calling.py`,
`repair/normalizer.py`, `search/catalog.py`. No forbidden leaf dirs inside
(no `models/`/`runtime/`/`errors/`/`contracts/`/`subagents/` paths).

### Batch 2: `backend/packages/harness/alpha/memory/` — 17 files

`_store_format.py`, `capture_composition.py`, `recall_composition.py`,
`session_search.py`, `kibitzer.py`, `cognitive_memory_tiering.py`,
`cognitive/` (4), `fusion/` (4), `policy/` (3).

Tests (run with main-checkout venv, `PYTHONPATH=backend`, worktree code):

- `backend/tests/test_tool_call_repair.py` (covers `tools/repair/normalizer.py`) — **3 passed**
- `test_memory_policy.py` + `test_memory_recall_composition.py` +
  `test_memory_recall_wiring.py` (cover `memory/policy/`, recall composition) — **70 passed**
- No dedicated test file found for `tools/builtins/*`, `tools/code_mode/*`,
  `tools/programmatic_calling.py`, `tools/search/catalog.py`,
  `memory/cognitive/*`, `memory/fusion/*`, `memory/kibitzer.py`,
  `memory/session_search.py`, `memory/_store_format.py` — stated explicitly,
  coverage for those files is implied only via the suites above, not proven.

## Result of those two batches

Recorded as measured *at the time on branch `agent/format`*, not as a claim about
the current tree: repo root went **562 → 508** and `backend/` went
**552 → 498**, 54 files changed, ~670 insertions, ~727 deletions. Pure
`ruff format` output; no behaviour edits, no `# noqa`, no gate/config changes.

That historical 562 was itself unpinned, and the current tree does not measure
562 (nor 508) under either ruff version. Treat the batch result as provenance for
the 54 files touched, and use the table above for the current count.

## Remaining work, grouped

Grouping only — no count is asserted here, because the counts above are the
authoritative ones and this list goes stale independently of them.

- `backend/tests/` — coherent next batch, but large; one directory per batch is
  the reviewable unit.
- `backend/packages/harness/alpha/` remainder — biggest groups are `agents`,
  `security`, `rsi`, `mission`, `safety`/`skills`/`avo`. Each is one future batch.
- Files owned by other agents (`runtime/*`, `models/*`, `subagents/*`,
  `multimodal/errors.py`, `projects/contracts.py`, and their tests) are in the
  unformatted set by ownership, not by gate exclusion.
- `backend/app/`, `backend/scripts/`, backend root dotfiles, root `scripts/`,
  `installer/` — small, allowed, suitable for a final misc sweep.
- Boundary kept: batches stop at the stated directories; the tree contained only
  the two batches above, verified by `git status --porcelain` at the time.

## When you update this document

Re-run **both** commands above, record the ruff version beside the number, and
re-derive the four-file delta rather than copying it. A debt document whose
number cannot be reproduced from its own text is worse than no debt document,
because it converts an open liability into a settled-looking fact.
