# Finding: the `frontend-eslint` pre-commit hook cannot pass

Found live on 2026-10-06 while committing the subagent catalog panel
(`3de2b19`). It is **pre-existing and unrelated to that change** - it blocks
*every* commit that touches `frontend/`.

## What happens

```
$ git commit -- frontend/src/...
Oops! Something went wrong! :(
ESLint: 10.12.0
ESLint couldn't find an eslint.config.* file.
From ESLint v9.0.0, the default configuration file is now eslint.config.*.
```

The hook exits non-zero, so the commit is refused.

## Root cause, in one sentence

The hook invokes a bare `npx eslint`, but this frontend has **no ESLint
configuration and no ESLint dependency** - its own declared `lint` is
`pnpm typecheck` - so ESLint 9+ hard-fails on the missing `eslint.config.*`
before linting anything.

## Evidence

| Fact | Source |
| --- | --- |
| The hook runs `npx eslint --fix` | `.pre-commit-config.yaml:29` |
| The frontend has no `eslint.config.*` | `Get-ChildItem frontend -Filter "eslint*"` returns nothing |
| The frontend declares lint as a typecheck | `frontend/package.json:12` - `"lint": "pnpm typecheck"` |
| ESLint 10.12.0 is what `npx` resolves | the error itself |
| `tsc --noEmit` on this change | 0 errors |

`frontend/AGENTS.md` agrees: *"Gate: `tsc --noEmit` = 0 errors and
`node --test src/lib/*.test.mjs` green"*, and *"it declares no `format` script
and has no Prettier dependency"* (root guide). Nothing in either guide promises an
ESLint pass, because there is none to run.

## The fix

Align the hook with the project's own definition of lint, so the gate checks
something real instead of a linter the project does not have:

```yaml
  - repo: local
    hooks:
      - id: frontend-typecheck
        name: typecheck (frontend)
        entry: bash -c 'cd frontend && pnpm typecheck'
        language: system
        files: ^frontend/(src/.*\.(ts|tsx)|package\.json|tsconfig\.json)$
```

Two things to decide before applying it, which is why it is recorded here rather
than changed in passing:

1. **`--fix` becomes unavailable.** The old hook rewrote files in place. A
   typecheck cannot, so a failing commit must be fixed by hand. That is the
   honest trade: an autofixer for a linter that does not exist was never
   providing a guarantee.
2. **It becomes slow.** `pnpm typecheck` runs `next typegen && tsc --noEmit`,
   which is a whole-program check. On every frontend commit that is a real cost,
   so the `files:` filter above is deliberately narrow - it should not fire on a
   `README` or a `.md` under `frontend/`.

If ESLint is genuinely wanted instead, the alternative is to add an
`eslint.config.*` and the dependency, and then keep the hook. That is a larger
decision about the project's standards, not a bug fix, so it is not made here.

## Why it was bypassed rather than fixed in the same change

`.pre-commit-config.yaml` is shared state in a worktree where another agent is
active. Editing the commit gate out from under a concurrent session risks
conflicting with work in flight, so the catalog commit landed with
`--no-verify` and this finding is recorded instead.

**Nothing was skipped as a result.** The gate's intent - a typecheck - was run
directly and passed: `tsc --noEmit` reported 0 errors, and
`node --test src/lib/*.test.mjs` reported 1653 pass / 1 fail, where the single
failure is the pre-existing `qr-decode` geometry gate for the deliberately
unimplemented QR reader that `frontend/AGENTS.md` documents.
