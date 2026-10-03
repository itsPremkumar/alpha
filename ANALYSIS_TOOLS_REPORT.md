# Advanced Automatic Code Analysis — Full Internet Research Report

Date: 2026-10-02
Scope: every major technique + tool for automatically finding errors in a codebase,
then applied to the Alpha repo (Python 3.12 backend, Next.js 15 frontend, Electron, Docker).

---

## 1. Technique taxonomy (what "automatic error finding" actually means)

| # | Technique | What it finds | When it runs |
|---|---|---|---|
| 1 | **Lint / style** | Readability, smells, obvious bugs | On save / CI |
| 2 | **Type checking** | Wrong types, missing Optional, bad signatures | On save / CI |
| 3 | **Static Application Security Testing (SAST)** | Injection, weak crypto, hardcoded secrets, unsafe calls | CI / nightly |
| 4 | **Software Composition Analysis (SCA)** | CVEs in dependencies | CI / scheduled |
| 5 | **Dead code detection** | Unused modules/functions/imports | Nightly / pre-release |
| 6 | **Complexity / maintainability metrics** | Over-complex functions, deep nesting | CI reports |
| 7 | **Property-based testing (PBT)** | Edge-case bugs from generated inputs | Unit-test suite |
| 8 | **Mutation testing** | Tests that *exist* but don't actually test anything | Nightly / PR gate |
| 9 | **Coverage-guided fuzzing** | Crashes, unhandled inputs, DoS | OSS-Fuzz / scheduled |
| 10 | **Symbolic / concolic execution** | Path-constraint bugs, unreachable paths | Research / targeted |
| 11 | **Dynamic tracing / profiling** | Runtime perf bugs, blocking I/O, leaks | Staging / prod |
| 12 | **AI-assisted review / agentic fuzzing** | Business-logic bugs, IDOR, auth gaps | CI / review bot |
| 13 | **Interactive / exploratory (REPL, REPL breakpoints)** | Logic errors, state inspection | Dev |
| 14 | **Chaos / fault injection** | Failure-handling gaps | Staging / prod |

A single tool never covers all of these. The right posture is **layered**:
lint + types + SAST in the inner loop, SCA + PBT + mutation in CI, fuzzing + symbolic in nightly, chaos in staging.

---

## 2. Tool landscape per layer

### 2.1 Lint / format
- **Ruff** (Rust) — 100+ rules (style, imports, bugbear, security subset). Already in Alpha's stack.
- **Pylint** — slower, deeper correctness/convention checks.
- **dmypy / dlint** — linter plugins for mypy.

### 2.2 Type checking
- **mypy** — reference Python type checker.
- **Pyright** — faster, stricter, Microsoft-backed. Good for incremental typing.
- For Alpha: run `mypy --strict` initially with many waivers, tighten over time.

### 2.3 SAST (security-pattern + taint)
| Tool | Strength | Free? |
|---|---|---|
| **Bandit** | Python-native AST checks: `shell=True`, eval/exec, weak crypto, hardcoded secrets | ✅ |
| **Semgrep** | YAML custom rules, cross-file taint (Pro), FastAPI models | ✅ CE / paid Pro |
| **CodeQL** | Deepest Python data-flow; understands FastAPI/Django/Flask | ✅ public repos / paid private |
| **PyAegis / PySpector / CytoScnPy** | Newer Rust-core Python SAST, low FP, AI-ruleset for LLM agent code | ✅ |
| **SonarQube / Snyk Code** | Commercial, CI gates, quality + security | Paid |

### 2.4 SCA (dependency CVEs)
- **pip-audit** — queries PyPI/OSV.
- **Safety** — commercial SCA.
- **OWASP Dependency-Check** — free.
- **Snyk / GitHub Dependabot** — hosted.

### 2.5 Dead code / complexity
- **Vulture** — unused functions/classes/imports (cross-module).
- **CytoScnPy** — dead code + complexity + Halstead + clone detection.
- **Radon** — cyclomatic complexity, maintainability index.
- **Xenon** — CI gate on complexity thresholds.

### 2.6 Property-based testing
- **Hypothesis** — generative testing; Already in Alpha's venv. Use `hypothesis.write` to fuzz existing functions.
- **PyTest-Gen / CrossHair** — symbolic PBT.

### 2.7 Mutation testing (does the test suite actually test?)
- **mutmut** — mature, WSL required on Windows.
- **irradiate** (Rust) — 38+ operators, incremental, CI-native, Stryker-compatible reports.
- **fest** (Rust) — blazing-fast CLI.
- **Atheris + Hypothesis** — can serve as fuzz-harnesses, not mutation, but pairs with PBT.

### 2.8 Coverage-guided fuzzing
- **Atheris** — Python-native libFuzzer wrapper. Run on parsers, config loaders, JSON-schema deserializers.
- **Atheris + Hypothesis polyglot** — same harness runs as pytest test or OSS-Fuzz target.
- **python-afl / AFL++** — alternative engines.
- **OSS-Fuzz** — continuous fuzzing for open source; Alpha qualifies.

### 2.9 Symbolic / concolic execution
- **KLEE** — C/C++ symbolic executor (not Python).
- **angr** — binary analysis; can lift Python via GraalPython in research.
- **Sydr-Fuzz** — hybrid fuzzing + dynamic symbolic execution (C/C++).
- **SAILOR** (2026 research) — LLM-orchestrated symbolic execution; writes harnesses automatically.
- For Python business logic: usually easier via **Hypothesis targeted/PBT** than symbolic engines.

### 2.10 Dynamic / runtime
- **coverage.py + pytest-cov** — line/branch coverage.
- **Dozer / memory_profiler** — memory leaks.
- **py-spy / austin** — sampling profilers for perf.
- **X-Trace-Id + OpenTelemetry** — distributed tracing (Alpha partially has this; noted gap in README).
- **Chaos engineering**: `toxiproxy`, `pumba`, `chaos-mesh` — inject latency/network failures.

### 2.11 AI-assisted / agentic
- **Semgrep AI-Powered Detection** — finds IDOR/auth logic gaps.
- **FuzzingBrain V2** — multi-agent fuzzing that writes harnesses and reproduces crashes.
- **VULCAN** — LLM-guided semantic fuzzer for C.
- **GitHub Security Lab Taskflow Agent** — autonomous fuzzing pipeline (LLM writes harnesses + triages crashes).
- **Code review bots**: CodeRabbit, Greptile, Sourcery, Cursor.
- Note: 2026 research shows LLM-only agentic vulnerability detection finds far less than guided symbolic execution (SAILOR paper: 12 vs 379 vulnerabilities). Use AI to *guide* tools, not replace them.

### 2.12 Interactive
- **pdb / ipdb / bpython** — breakpoints.
- **IPython %pdb, %debug** — post-mortem.
- **dirsync / git clean checks** — not error-finding but hygiene.

---

## 3. Frontend / cross-stack layers

| Layer | Tools |
|---|---|
| TypeScript types | `tsc --noEmit`, ESLint, Biome |
| Next.js build health | `next build`, `next lint`, Playwright E2E |
| Electron security | Electron security checklist, `electron-builder` audit, contextIsolation checks |
| Docker / IaC | `docker scan`, `trivy`, `hadolint`, `checkov`, `kube-linter` |
| API contract | OpenAPI generator, `schemathesis` (property-based API fuzzing!), `dredd` |
| Web perf | Lighthouse CI, Clinic.js |

**Schemathesis is underrated**: point it at your OpenAPI spec and it auto-generates thousands of valid+invalid requests against your FastAPI app.

---

## 4. Applied to Alpha — recommended concrete stack

Your repo already has: ruff, pytest, hypothesis, Playwright E2E, drift gates, CI sharding.

### Inner loop (every save / pre-commit)
- `ruff check` + `ruff format --check` ✅ already
- `mypy --strict` (incremental strictness)
- `pyright` for stricter IDE feedback

### PR / CI gate
- Existing CI suite + block: `ruff`, `bandit`, `vulture --min-confidence 70`
- `pip-audit --strict`
- `schemathesis` against FastAPI OpenAPI spec (API fuzzing)
- Mutation testing on **changed files only** (`irradiate --diff main`)

### Nightly
- Full `pytest` with `coverage.py` branch mode (print report)
- `semgrep --config=p/python` + custom YAML rules for Alpha's trust boundaries (e.g. `subprocess` with model input, `Path(...)` from user, `json.loads` on thread checkpoints)
- `codeql` security-extended suite
- `monal` / `atheris` fuzzers on: config loader, JSON checkpoint parse, message event feed, IM payload deserialization, OpenAI-compat request schema, MCP tool args

### Release gate
- `mutmut` / `irradiate` full mutation run (score must stay ≥ threshold)
- `vulture` zero new dead modules
- `trivy` image scan on `docker-compose` images
- `kube-linter` on `deploy/helm/alpha`
- Cold-start budget check (already in CI)

### Chaos / staging
- `toxiproxy` to add latency/jitter to Postgres/SQLite/IM webhooks
- `pumba` pause/kill the Gateway container; verify watchdog + recovery + resume
- Network-partition test: disable OpenRouter/OpenAI; verify honest "unverified/parked" status, not a fake success

---

## 5. Honest limitations (what no automatic tool will catch)

1. **Business-logic correctness** — "is this the right plan for the user's goal?"
2. **Honest reporting** — the repo's own `README` admits runs can be "unverified"; automatic tools can't decide if that honesty boundary is well-placed.
3. **Cross-process exact-once semantics** — requires integration test with real Postgres + multiple Gateway workers, not a scanner.
4. **Prompt-injection resistance of agent prompts** — needs red-team evals (e.g., `promptfoo`, `garak`), not static analysis.
5. **UI/UX bugs** — need Playwright E2E + manual QA.
6. **Model-behavior regressions** — need eval harnesses with recorded golden outputs, not code scanners.

---

## 6. Starter kit (concrete commands)

A ready-made runner was created in the analysis worktree:

```powershell
cd C:\Users\PREM KUMAR\Videos\alpha-analysis
.\scripts\analysis\run_all.ps1
```

Which runs: ruff lint, ruff format check, bandit, semgrep, vulture, mypy, pip-audit, pytest (configless) — each writing a report to `analysis-reports/`.

### Extra one-liners you can run manually

```powershell
# API fuzzing against FastAPI (installs schemathesis)
cd backend; .venv/Scripts/python.exe -m pip install schemathesis
.venv/Scripts/python.exe -m schemathesis run --checks all http://localhost:8001/openapi.json

# Atheris fuzz one parser (example: checkpoint JSON)
.venv/Scripts/python.exe -m pip install atheris
python -m atheris fuzz_checkpoint.py

# Mutation testing (WSL)
wsl -e bash -c "cd /mnt/c/Users/PREM\ KUMAR/Videos/alpha-analysis/backend && mutmut run"

# Coverage report
.venv/Scripts/python.exe -m pytest --cov=packages/harness/alpha --cov-branch --cov-report=html
```

---

## 7. Priority backlog for Alpha (highest ROI first)

1. **Wire `schemathesis`** into CI — free, finds real FastAPI contract bugs.
2. **Add `atheris` harnesses** on config loader, JSON-checkpoint parse, OpenAI-compat schema.
3. **Add `irradiate`/`mutmut` nightly** on the `models/` + `sandbox/` packages (the uncommitted worktree touches these).
4. **Add `vulture` gate** — the README already admits unwired libs exist; vulture quantifies dead code.
5. **Add `pip-audit --strict` to PR gate** — dependency CVEs are the most common free-win security issue.
6. **Formalize the chaos drill** — a script that kills the Gateway + cuts OpenRouter and asserts honest "unverified" status.
7. **Prompt-injection red team** with `garak` / `promptfoo` on the deliberation + deep-research agents.

---

## 8. Bottom line

There is **no single "advanced core testing" button**. The best automated posture is a layered matrix: lint/types/SAST in the dev loop, schemathesis API fuzzing + bandit + pip-audit + mutation testing in CI, atheris fuzzers + semgrep/CodeQL + chaos drills nightly, and LLM-guided fuzzing/review in pre-release. For Alpha specifically, the uncommitted `models/free_router` + `provider_manager` fix is exactly the kind of change that `atheris` + `irradiate` + `pytest configless` should cover.
