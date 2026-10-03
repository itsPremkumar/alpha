# Advanced Analysis Tooling for Alpha

This worktree (`analysis/advanced-testing`) adds a full battery of automatic
code-quality, security, and correctness scanners on top of the project's
existing ruff + pytest setup.

## One-shot runner

```powershell
# From the repo root of this worktree:
.\scripts\analysis\run_all.ps1
```

That runs, in order:

1. **ruff** — lint + format check (project's configured rules)
2. **bandit** — Python security SAST (AST-based)
3. **semgrep** — pattern + taint SAST (Python rules)
4. **vulture** — dead code detection
5. **mypy** — static type checking (lenient profile, see `mypy.ini`)
6. **pip-audit** — known-vulnerability scan of installed dependencies
7. **pytest** — the backend unit suite (configless, like CI)

Each tool writes its report to `analysis-reports/` and prints a summary.
Non-zero findings are expected — the point is a full inventory.

## Why these tools

| Tool | What it finds that nothing else does |
|---|---|
| ruff | style, imports, obvious bugs — fast first pass |
| bandit | `shell=True`, hardcoded secrets, weak crypto, unsafe deserialization |
| semgrep | taint flows (user input → SQL/exec/SSRF), framework misuse |
| vulture | dead modules, dead functions, unused imports across packages |
| mypy | type mismatches, wrong call signatures, missing Optional handling |
| pip-audit | CVEs in the pinned dependency tree |
| pytest | the actual unit-test contract |

## Notes

- `semgrep` requires network for the default registry rules; use
  `--config=p/python` offline if needed.
- `pip-audit` needs network to query the OSV/PyPI advisory DB.
- `mypy` is run in a deliberately lenient mode (see `mypy.ini`) because the
  codebase is not fully typed yet — it will surface real type errors without
  drowning in strictness noise.
- Everything is local: no source code leaves the machine.
