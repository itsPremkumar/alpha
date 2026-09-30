# `alpha.verification` — the bounded verification controller

Scope: this file covers `packages/harness/alpha/verification/` and
`config/verification_loop_config.py`. User-facing map: `docs/VERIFICATION_LOOP.md`.

This is the controller the research audit found missing. Alpha already had the
verify tools (`FinishFirstVerifierMiddleware._VERIFY_TOOLS`), the claim-binding
evidence checker (`subagents/acceptance_checks.py`), a budgeted non-looping
retry, and an anti-thrash loop detector. What it did not have was the component
that *runs* the tests, *reads the failure*, *dispatches a bounded fix*, *re-runs*,
and *decides*. The middleware noticed a missing verification and asked for one.
That is a documented capability, not an operated one.

## The three rules

1. **Evidence is real or it is nothing.** `execution.RecordedExecution` cannot be
   constructed by hand — it is sealed by a module-private token, and
   `record_execution` refuses an empty command or an anonymous call. The
   production `BashToolExecutor` invokes the real `bash` tool and hands the real
   harvester (`subagents/executor._harvest_bash_executions`) a real
   `AIMessage`/`ToolMessage` pair, so the evidence dict is the production
   harvester's own output rather than a second implementation that can drift.
   A synthesised transcript is not a degraded pass; it is a construction error.
2. **A repair may not reduce coverage.** `surface.compare_surfaces` compares the
   test surface before and after every repair and refuses a reduced one. It
   reuses `selfrepair.assertion_guard.TestAssertionImmutabilityGuard` — the
   harness's single owner of the assertion-extraction rule — rather than forking
   it, and adds the two things that guard cannot see: test *names* and
   skip/xfail markers.
3. **Undecidable is UNVERIFIED, never a pass.** Three outcomes and a required
   reason for the third. `VerificationReport.outcome` is a derived, read-only
   property, so no consumer can assign a verdict.

## Vocabulary layering

`acceptance_checks.py` owns `checked`/`holds` for criterion leaves — never
`satisfied`/`verified`/`passed`, so the model never conflates deterministic
execution evidence with task acceptance. The controller is the *runtime hard
gate* that owns the strong words, so `VERIFIED`/`FAILED`/`UNVERIFIED` live here
and nowhere the leaf layer can see them. Do not add a `verified` boolean to
`AcceptanceLeaf`; that is the exact conflation the rule exists to prevent.

## The state machine

`controller.VerificationController.step()` advances exactly one transition and
returns a `LoopDirective`. The caller supplies two things it genuinely owns — a
**surface reader** and a **repair outcome** — and decides nothing else. Only
`IDLE`, `AWAITING_REPAIR` and a settled state are observable from outside;
reaching an internal state from `step()` raises, because a transition that
entered an internal state always leaves it before returning, so reaching one
means the machine lost a transition.

```
IDLE ─execute─▶ ASSESSING ─pass─▶ SETTLED(VERIFIED)
                   │
                   ├undecided─▶ SETTLED(UNVERIFIED / evidence_unanchored)
                   │
                   └fail─▶ repair disabled? ─▶ SETTLED(FAILED, decided)
                            │no
                   budget exhausted? ─▶ SETTLED(UNVERIFIED / budget_exhausted)
                            │no
                   surface watched? ─▶ SETTLED(UNVERIFIED / test_surface_unavailable)
                            │no
                   CAPTURING_SURFACE ─▶ DISPATCHING_REPAIR ─▶ AWAITING_REPAIR
                                                                        │
                       declined ◀──────────────────────────────▶ completed
                           │                                        │
                    SETTLED(FAILED)                          COMPARING_SURFACE
                                                                    │
                                    reduced/indeterminate ─▶ SETTLED(UNVERIFIED)
                                                                    │no
                                                                    ▼
                                                              EXECUTING
```

`AWAITING_REPAIR` is the one state the caller can poll: `step()` there without a
repair outcome redelivers the same directive, so polling cannot spend a second
attempt.

## The budget

`budget.AttemptBudget` bounds **repairs**, not executions; the first
verification run is free. It is a ledger rather than a limit checked at the top
of a loop: `consume()` raises on a spent budget, so a future edit that forgets
the check overdraws loudly instead of quietly. The remainder is first-class —
it is in the repair prompt ("this is the last one") and in the report.

Exhaustion is a state, not an exception and not a give-up, and it settles
UNVERIFIED rather than FAILED. See `contract.py`'s module docstring for why: a
loop that stopped asking has not decided anything. The last determinate reading
is still carried in `last_reading` and in the attached evidence, so exhaustion
never hides a known failure.

## Configuration

`config/verification_loop_config.py` is standalone on purpose: `AppConfig` does
not reference it yet, and `resolve_verification_loop_config()` reads it
opportunistically so the module behaves identically before and after that field
exists. The one-line `AppConfig` change is recorded in `docs/VERIFICATION_LOOP.md`.

**`refuse_weakened_tests` is not a field and never will be.** A self-repair loop
that can be satisfied by deleting an assertion is worse than no loop, because it
manufactures a green result; a config key that turns that check off is a foot-gun
with a flattering default.

## The integration point

`integration.py` is the whole seam. The middleware owner places one call where
the retry already lives — see that module's docstring for the diff shape and for
why the placement is theirs and not ours. Do not add a router: a new
`app/gateway/routers/verification.py` would be unregistered, unreferenced, and
would fail the orphan-module guard, and the loop's subject is a run, not an HTTP
request.

## What it does not own

`RunManager` is the sole run lifecycle owner and `SafeRunRecoveryService` the
only safe-continuation authority. The controller drives a loop *within* a run
that already exists; it does not start, adopt, cancel, or terminate one, and it
holds no thread state. `tests/test_verification_honesty.py::TestNoSecondRunLifecycle`
parses this package's imports and fails on any reach for those symbols, because
that is a failure mode nobody would notice until two code paths tried to own a
run.

The side-effect ledger (`runtime/side_effects/`) is deliberately **not** used. Its
production writer does not exist yet, so depending on it would make the loop's
honesty rest on a table that is empty in every real run.

## Known boundaries

* The surface comparison reads the thread workspace on the host. A remote
  sandbox with no host mirror reads back empty, so every repair is refused and
  the loop settles UNVERIFIED — the correct direction to be wrong in.
* The comparison covers assertion text, test names and skip markers in the
  watched files. It cannot see a `conftest.py` that skips collection globally, a
  runner option that deselects, or a weakened *production* assertion helper.
* The controller reuses the checker's verdicts. Where the checker is
  deliberately text-and-evidence only (`subagents/AGENTS.md`, "Known accepted
  boundaries" — a Makefile that swallows failures, a runner exiting 0 on
  failure), the loop inherits that boundary and does not paper over it.
* `docs/VERIFICATION_LOOP.md` §7 is the full limitation list, including
  non-determinism and the "the fix is right and the test is wrong" case. It is
  not short, and it is the part of this work most worth reading.

Tests: `tests/test_verification_loop.py`, `tests/test_verification_honesty.py`.
