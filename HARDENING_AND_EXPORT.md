# Hardening & Export — Self-Critique, Fixes, and Full Export

**Date:** 2026-09-30
**Goal:** critique this session's own work, find vulnerabilities, fix them to a world-class standard, and export everything as one reproducible bundle.

---

## 1. The critic (`alpha/critique/`)

A deterministic, dependency-free, AST-based code critic. Eight rules:

| Rule | Severity | Detects |
|---|---|---|
| `bare-except` | medium | `except Exception/BaseException:` that swallows errors |
| `open-encoding` | medium | text `open()` without an explicit encoding |
| `print-in-library` | low | `print()` in library code (use logging) |
| `todo-marker` | low | TODO/FIXME/XXX/HACK left in comments |
| `mutable-default` | high | mutable default arguments |
| `from-dict-no-unknown-check` | medium | `from_dict` that accepts unknown keys |
| `save-no-fsync` | medium | durable write without `fsync` before the atomic replace |
| `missing-docstring` | low | module / public class / function without a docstring |

Run it: `python critique_scan.py backend/packages/harness/alpha` (exit 1 on any HIGH finding).

---

## 2. Self-critique result (before)

Critiquing the 26 files of this session's own packages:

```
files scanned: 26
findings: 35  (high=0, medium=14, low=21)
by rule: {save-no-fsync: 6, from-dict-no-unknown-check: 8, docstring: 17, todo-marker: 4}
```

---

## 3. Vulnerabilities found & fixed

| Finding | Risk | Fix applied |
|---|---|---|
| **`save-no-fsync` × 6** | A crash between `write` and `os.replace` could lose an acknowledged durable write. | Added `handle.flush()` + `os.fsync(handle.fileno())` before every `os.replace` in all six stores (routines, connectors, egress, experts, skills_market, automations). |
| **`from-dict-no-unknown-check` × 8** | A document with extra/attacker-controlled keys was silently accepted — a smuggling surface for future fields (e.g. a capability grant). | Every `from_dict` now computes `unknown = sorted(set(data) - known)` and raises on a non-empty set. |
| **`todo-marker` × 4** | False positive: the rule flagged its own marker table (string literals, not comments). | Refined the rule to scan `#` comments only. |
| **`missing-docstring` × 17** | Maintainability / API clarity. | Added docstrings to every flagged module, class and public function. |

### Two further world-class hardenings (found by review, not the rules)

- **Leap-year correctness** — `next_run()` for a YEARLY Feb-29 rule scanned only ~5 years; widened the bound to ~10 years (120 months) so a leap day is always reachable.
- **Unbounded error strings** — `ConnectorStore.record_health()` stored the upstream error verbatim; capped at 2000 chars so a chatty or hostile provider cannot grow the store without bound.

---

## 4. Result (after)

```
$ python critique_scan.py <the 10 packages>
files scanned: 26
findings: 0  (high=0, medium=0, low=0)
```

All verification suites still pass after the changes:

| Suite | Result |
|---|---|
| `verify_full_workspace.py` | **24/24** |
| `verify_workbuddy_features.py` | **15/15** |
| `verify_features_live.py` | **26/26** |
| `alpha.scorecard` | 85/85 frontier coverage |

A regression test (`backend/tests/test_critique.py::test_own_packages_are_clean`) fails the build if any finding ever returns.

---

## 5. Export — run everything

```
# 1. capability scorecard (85/85 frontier coverage)
python -c "import sys; sys.path.insert(0,'backend/packages/harness'); \
  from alpha.scorecard import scan; r=scan('backend/packages/harness/alpha'); \
  print(r.present_count, r.total); print(r.render_markdown())"

# 2. the critic (0 findings)
python critique_scan.py backend/packages/harness/alpha

# 3. end-to-end verifications
python verify_full_workspace.py
python verify_workbuddy_features.py
python verify_features_live.py

# 4. pre-existing features (needs deps + config.yaml)
uv run --with pydantic --with langchain --with "sqlalchemy[asyncio]" --with python-dotenv python verify_existing_live.py
```

Everything is committed on branch `feature/grok-gap-impl`.

---

## 6. What is exported (this session's deliverable set)

**10 new harness packages** — `routines`, `connectors`, `egress`, `scorecard`, `modes`, `experts`, `automations`, `skills_market`, `workspace`, `critique`.
**5 test modules** and **6 runnable verification/scanner scripts**.
**7 reports** — capability analysis, WorkBuddy comparison, complete feature set, and this hardening report.

The framework scores **85/85** on the frontier taxonomy, and every new module is now **hardened, documented, and regression-guarded**.
