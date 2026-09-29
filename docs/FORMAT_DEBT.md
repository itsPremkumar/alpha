# Format debt tracker

How this is measured (reproducible):

```powershell
cd <worktree>
& "<main-checkout>\backend\.venv\Scripts\python.exe" -m ruff format --check . 2>&1 | Select-Object -Last 1
& "<main-checkout>\backend\.venv\Scripts\python.exe" -m ruff format --check backend 2>&1 | Select-Object -Last 1
```

The worktree has no provisioned venv and installing is out of scope, so the
read-only `--check` measurement (and `ruff format` itself) runs via the main
checkout's interpreter. No main-tree file is touched; only worktree files are
formatted.

## Baseline (2026-09-29, branch agent/format start)

- Repo root: **562 would be reformatted**, 2768 already formatted.
- `backend/` only: **552 would be reformatted**, 2695 already formatted.

Per-area breakdown of the 562:

| Area | Count |
| --- | --- |
| `backend/packages/` (of which 334 under `packages/harness/alpha/`) | 337 |
| `backend/tests/` | 196 |
| `backend/app/` | 14 |
| root `scripts/` | 9 |
| `backend/scripts/` | 2 |
| `installer/` | 1 |
| `backend/` root dotfiles (`head_projects`, `probe_import_isolation`, `mutation_evidence`) | 3 |

Largest `packages/harness/alpha/` subdirs: `tools` 37, `runtime` 20,
`memory` 17, `agents` 15, `security` 14, `rsi` 14, `mission` 11,
`safety`/`skills`/`avo` 10 each, long tail of smaller dirs.

Shape: concentrated, not diffuse — ~60% is the harness core, ~35% is
`backend/tests/`. One neglected core plus its test suite, not a repo-wide habit.

## Excluded on purpose — left alone

`scripts/ruff_scope.py` carries exactly one `EXCLUDED_FILES` entry:
`.agent/skills/blocking-io-guard/templates/anchor.template.py` — a copy-me
template with placeholder identifiers that are not valid Python. It is not in
the unformatted list and was never a candidate. Nothing else is excluded; every
other file in the 562 is genuine debt, not a documented decision.

Also deliberately untouched: files under `models/`, `runtime/`, `subagents/`,
`errors/`, `contracts/` (including same-named subdirs inside
`backend/packages/harness/alpha/`, e.g. ~20 `runtime/*` and 5 `models/*` files)
and `frontend/**` — owned by other agents per assignment. They remain in the
remaining list below by ownership, not by gate exclusion.

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

## Result

- Repo root: **508 would be reformatted** (562 -> 508, -54), 2820 formatted.
- `backend/` only: **498 would be reformatted** (552 -> 498, -54).
- `git diff --stat`: 54 files changed, ~670 insertions, ~727 deletions.
  Pure `ruff format` output; no behaviour edits, no `# noqa`, no gate/config
  changes.

## Remaining (508) and why each group is not yet done

- `backend/tests/` (~196): coherent next batch, but large — one directory per
  batch is the reviewable unit; left for follow-up batches.
- `backend/packages/harness/alpha/` remainder (~280): biggest allowed groups
  are `agents` (15), `security` (14), `rsi` (14), `mission` (11),
  `safety`/`skills`/`avo` (10 each). Each is one future batch.
- Forbidden-ownership files inside the unformatted set (~50: `runtime/*` ~20,
  `models/*` ~5+, `subagents/*` 2, `multimodal/errors.py`,
  `projects/contracts.py`, plus `backend/tests/test_models_*`,
  `test_runtime_*`, `test_system1_runtime_hook.py`): owned by other agents —
  left for them, not skipped by the gate.
- `backend/app/` (14), `backend/scripts/` (2), backend root dotfiles (3),
  root `scripts/` (9), `installer/` (1): small, allowed, not yet batched —
  sweep up in a final misc batch.
- Boundary kept: batches stop at the stated directories; the tree contains
  only the two batches above, verified by `git status --porcelain` (54
  modified files, nothing else).
