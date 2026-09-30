# The verification loop

Alpha has always had the parts of an honest self-verification loop. The
`auto_test_and_repair` and `reproduce_and_verify` tools exist and are named as
`FinishFirstVerifierMiddleware._VERIFY_TOOLS`. The claim-binding acceptance
checker in `subagents/acceptance_checks.py` will accept a *recorded* passing
execution and refuses a self-report about one. `LoopDetectionMiddleware` stops a
thrash. The completion critic withdraws a terminal claim and sends the agent back
with a budgeted retry that cannot loop.

What did not exist is the thing that connects them. The middleware noticed a
missing verification and *asked* for one. Nothing ran the tests, read the
failure, dispatched a fix, re-ran, and decided. That is the gap the coding-agent
survey named as recommendation 3 and called "worth more than the other nine
combined" — the project was behind not on agent design but on *operating the
verification it had already built*.

`packages/harness/alpha/verification/` is that controller. This document is its
design, its evidence, and — deliberately, and at length — what it still cannot
do.

- Module contract: `backend/packages/harness/alpha/verification/AGENTS.md`
- Operator policy: `config.yaml -> verification_loop`
  (`config/verification_loop_config.py`)

---

## 1. What already existed, and the precise seam built on it

**The acceptance checker** (`subagents/acceptance_checks.py`). Its
`tests_passed:<command>` leaf is the best verification design in the field and a
research audit judged no competitor close to it. A self-report is inadmissible:
the leaf anchors to a *specific recorded bash execution* with a recognised
summary shape, matching is shell-structure-aware so `echo "npm test"` cannot
anchor it, and `_TEST_ZERO_SHAPE_RE` vetoes "0 passed". Undecidable renders
UNVERIFIED, never a silent pass. Its leaf booleans are `checked`/`holds` — never
`satisfied`/`verified`/`passed` — so the model cannot conflate evidence with
acceptance.

**The finish-first middleware** (`agents/middlewares/finish_first_verifier_middleware.py`).
It performs `RemoveMessage` plus `jump_to: "model"` with a budgeted retry, and
interrupts when it cannot verify. It *notices and asks. It never drives.*

**The assertion guard** (`selfrepair/assertion_guard.py`). The harness already
had a before/after test-surface comparator: `TestAssertionImmutabilityGuard`,
used by the surgical APR engine. It is the single owner of the
assertion-extraction rule, and it is what makes the weakening check a
composition rather than a fork.

**The loop detector** (`loop_detection_middleware.py`). Anti-thrash, already
present, and not the thing this work adds — a thrash guard is not a loop.

**The side-effect ledger** (`runtime/side_effects/`) was read and **deliberately
not used**. An audit found it has zero production callers, so depending on it
would make this loop's honesty rest on a table that is empty in every real run.
It is not touched here; wiring the ledger is another agent's change and is not
this one.

### The seam

The controller's VERIFIED verdict **is** the acceptance checker's verdict. It
raises a `tests_passed:<command>` criterion, hands the checker one real recorded
execution, and reads the leaf. It contributes no acceptance logic of its own. So
"VERIFIED" means in this loop exactly what it means everywhere else in Alpha, and
a change to the checker's rules changes this loop's behaviour for free — or, put
the other way, a change that made the checker lenient would immediately show up
here, which is one of the mutation proofs in §5.

One deviation is worth naming: the controller passes the checker's file-leaf
probes as *lazy delegates* rather than letting it resolve them. The checker
resolves `read_current_file_content` eagerly, which imports the whole sandbox
provider stack — measured at ~113s of import time in this tree — and the loop
only ever raises a `tests_passed:` criterion, which reads no file. The delegates
are the same production callables, resolved on first use, so a future criterion
family that does read a file gets production behaviour rather than a silent
no-op. The checker's own defaults are unchanged.

---

## 2. The design

### The state machine

`VerificationController.step()` advances exactly one transition and returns a
`LoopDirective`. The caller supplies two things it genuinely owns — a **surface
reader** and a **repair outcome** — and decides nothing else.

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

`IDLE`, `AWAITING_REPAIR` and a settled state are the only states observable
from outside. Reaching an internal state from `step()` **raises**: a transition
that entered an internal state always leaves it before returning, so reaching one
means the machine lost a transition. A loop that silently restarted a transition
would be a loop that could run a command twice without saying so.

`AWAITING_REPAIR` is the one state a caller may poll. `step()` there without a
repair outcome redelivers the same directive and spends nothing, so a polling
caller cannot burn a second attempt by asking twice.

### Where the attempt budget lives

`budget.AttemptBudget`, held by the controller, decremented by `consume()`.

- It bounds **repairs**, not executions. The first verification run is free; a
  loop that cannot verify at all is bounded by its own single execution.
- It is checked **before** every dispatch, so an exhausted budget is a state the
  loop settles into rather than an overrun it notices afterwards. The test driver
  is itself bounded, so a loop that never settles fails by assertion instead of
  hanging.
- `consume()` **raises** on a spent budget. That is the backstop: a future edit
  that forgets the check overdraws loudly rather than quietly.
- The remainder is first-class. It is in the repair prompt ("This was repair
  attempt 2 of 3. 1 remain after it." / "This is the last one.") and in the
  report.

### How exhaustion is represented

As a **state, not an exception and not a give-up**. When the budget runs out the
controller keeps the last determinate reading, settles `UNVERIFIED` with reason
`budget_exhausted`, and reports the failing evidence verbatim so exhaustion never
hides a known failure.

The sharper decision: **exhaustion is not promoted to `FAILED`.** A loop that
stopped trying has not decided anything; reporting `FAILED` there converts an
unanswered question into a recorded conclusion. `FAILED` is reserved for a
*decided* negative — a repair the caller declined, or a verification-only run.
This is stricter than it sounds, and it is the interpretation the assignment's own
requirement list asks for ("UNVERIFIED … including 'budget exhausted'"). The cost
is that `FAILED` is rarer than an operator may expect from a repair loop; the
alternative is worse.

### How the real failure is fed back

`LoopDirective.prompt` for a repair carries the **recorded output of the failing
run, verbatim** — bounded by `failure_evidence_chars` (default 4000) with the
truncation stated in the prompt itself. Not a summary, not a restatement. The
tests assert that the recorded `file:line`, the failing expression, the exit
marker and the passing count all survive into the prompt, because a summary
would drop exactly the parts that identify the defect.

The prompt also states the refusal rule in the model's own terms and gives it a
way out: if the *test* is what is wrong, say so in the next message rather than
editing it. That is a request, not a mechanism — see §7 for why it cannot be a
mechanism.

### The integration point

See §6.

---

## 3. How a weakened test is detected

The requirement is the known failure mode of every self-repairing agent: deleting
an assertion, loosening a matcher to match the broken output, or marking a test
skip. All three produce a green run, and a loop that only asks "did the tests
pass?" will happily finish on any of them.

### The comparison performed

`surface.compare_surfaces(before, after)`, on a per-file basis, for the files the
run actually changed:

| dimension | extracted by | a reduction is |
| --- | --- | --- |
| assertion expressions (multiset) | `TestAssertionImmutabilityGuard.extract_python_assertions` | a canonical expression present before and absent now — deletion, rewriting, or loosening |
| assertion count | the above | a fall that no named expression explains |
| test names (`def test_*`, `*Test`, `Test*`, `*Spec`) | `ast` | a test that no longer exists |
| skip/xfail markers | decorator + in-body regex | `@skip`, `@skipIf`, `@pytest.mark.{skip,skipif,xfail}`, `expectedFailure`, `pytest.skip(`, `pytest.xfail(`, `raise SkipTest`, `xit(`, `xdescribe(` |
| file readable | the reader | a watched test file that cannot be read after a repair |

Strengthening is never a reduction: a new file, a new test, or a grown assertion
set all pass. The comparison is per file, so one file growing cannot mask another
losing.

### The exact sequence

1. **Before** any repair, the surface is captured. Not after — a surface captured
   after the repair is a surface that cannot detect the change.
2. The repair is dispatched with the recorded failure.
3. **After** the repair returns, the surface is captured again through the same
   reader.
4. `compare_surfaces` runs. A reduction settles the loop
   `UNVERIFIED(weakened_test)`; an indeterminate comparison settles
   `UNVERIFIED(test_surface_indeterminate)`. Both block. **Neither is FAILED** —
   nothing was decided.
5. Only a clean comparison lets the loop re-run.

### The two fail-closed directions that matter most

- **No watched test files.** The caller names the run's changed paths; if none of
  them is a test file, the loop settles `UNVERIFIED(test_surface_unavailable)`
  rather than approving a repair it cannot check. "I could not see the tests" is
  never "the tests are fine".
- **A file that cannot be read.** A test file readable before and unreadable after
  is treated as a **reduction**, not as indeterminacy. It is not "we cannot tell"
  — it is a file whose assertions are not running any more, and calling that
  unclear would let a repair delete a suite and be described as merely vague.
  Indeterminacy is reserved for a failed *before*-capture, where there is no
  baseline to compare against at all.

### What is deliberately not configurable

There is no `refuse_weakened_tests` key, and there will not be. A config switch
that turns off the coverage guard is a foot-gun with a flattering default, in a
component whose entire value is that this particular failure is impossible.
`tests/test_verification_honesty.py::test_the_config_model_refuses_a_refusal_switch`
pins the absence.

---

## 4. The three outcomes

| outcome | reached when | requires |
| --- | --- | --- |
| **VERIFIED** | the evidence binder accepted the recorded execution (`checked and holds`) | a real recorded execution, and nothing else |
| **FAILED** | the loop **decided** and the last recorded execution determinately did not pass | `decided=True`: a declined repair, or a verification-only run |
| **UNVERIFIED** | it could not be determined | a reason, always |

`VerificationReport.outcome` is a **derived, read-only property**. It is not a
field, so no consumer can assign a verdict, and the constructor enforces the
invariants: a passing reading cannot carry a failure reason; a decided failure
cannot carry an undetermined reason; an undecided reading **must** carry one; a
budget-exhausted loop cannot be a decided loop; and the budget arithmetic must
balance.

### Every UNVERIFIED reason

`verification_loop_disabled` · `no_recorded_execution` · `execution_error` ·
`evidence_unanchored` · `budget_exhausted` · `weakened_test` ·
`test_surface_unavailable` · `test_surface_indeterminate` ·
`repair_unavailable` · `repair_error` · `internal_error`

### A completed run is never a verified run

The controller never asserts that a run finished — `report.run_finished` is
`False` on every path it constructs, because run completion is `RunManager`'s
fact, not this component's. `to_dict()` keeps `outcome` and `run_finished` in
separate keys. And when a report *is* given a finished run, `render_report` says
it in words that cannot be read as verification:

> The run finished. A finished run is not a verified run; the outcome above is
> the verification answer.

A run that finished UNVERIFIED is **neither a failure nor a success**. It is a
run whose work nobody established. The report says which.

---

## 5. Tests, with the bite proofs

`tests/test_verification_loop.py` (31 tests) and
`tests/test_verification_honesty.py` (38 tests), sharing
`tests/verification_fixtures.py`. Every guard below was broken on
purpose and the matching test watched fail. A guard nobody has seen fail is a
comment, not a guard.

| # | mutation | observed result |
| --- | --- | --- |
| 1 | **A controller that loops forever** — `if self._budget.exhausted:` → `if False:` in `_assess` | `test_budget_bounds_the_loop` and `test_exhausted_budget_is_never_a_pass` **both fail**. The mutation keeps dispatching; `AttemptBudget.consume()` raises `ValueError: attempt budget of 1 is exhausted` from the backstop, and the call-count assertion (`3` executions for a 2-attempt budget) fails. Bounded driver ⇒ failure, not a hang. |
| 2 | **A controller that accepts a deleted assertion** — `compare_surfaces` returns `reduced=False` unconditionally | **6 tests fail**: the three controller-level refusal tests (deleted assertion, added skip, loosened matcher) and the three `TestSurfaceGuard` comparison tests. The two "healthy repair" tests still pass, which is the point — the mutation only makes the guard permissive, and that is exactly what a guard-blind test would miss. |
| 3 | **A controller that reports UNVERIFIED as a pass** — `outcome` returns VERIFIED for any undecided reading carrying a reason | **9 tests fail**, including `test_a_finished_run_renders_as_not_verified`, which then renders `Verification outcome: VERIFIED` directly above the line *A finished run is not a verified run* — the contradiction the whole component exists to make impossible. |
| 4 | **A controller fed a synthesised transcript** — `_seal` given a default so the dataclass can be built by hand | `test_a_transcript_cannot_be_sealed_into_a_recording` **fails** with `Failed: DID NOT RAISE <class 'TypeError'>`. |
| 5 | **Budget exhaustion promoted to a decided failure** | First attempt caught by a *second* guard (`_build_report`'s defensive clamp) and 12 tests still passed — a real finding, reported rather than papered over. Removing all three sites (the clamp, `decided=True` in `_settle_unverified`, and the constructor guard) makes **3 tests fail**. |
| 6 | **`acceptance_checks.py` weakened** — `_check_tests_passed_leaf` returns `checked=True, holds=True` when no execution matches | `test_the_existing_checker_still_rejects_what_it_rejected` and `test_the_leaf_vocabulary_is_still_checked_and_holds` **fail**, and the latter renders `- [holds] tests_passed:make test` where it must render `UNVERIFIED`. |
| 7 | **A second run lifecycle owner** — `from alpha.runtime.runs.manager import RunManager` added to the controller | `test_the_package_imports_no_run_lifecycle_symbol` **fails** with `['controller.py: RunManager']`. |

`acceptance_checks.py` is byte-identical to `origin/main` in this branch. Every
change made while proving mutation 6 was reverted, and `git status` shows the
file unmodified.

### What else the suites hold

Three outcomes each reachable and distinct; budget arithmetic checked at
construction; budget over-draw raises; undecidable output is UNVERIFIED; "0
passed" is not a pass; a persistent-shell recording is not a pass; the recorded
failure reaches the prompt verbatim; a truncated failure says it is truncated; a
strengthening repair is accepted; the production workspace reader refuses
`..` escapes and symlinks out of the tree; a disabled loop reports rather than
vanishes; state names are actions and never verdicts; the config has no refusal
switch; the controller exposes no lifecycle method; a raising step degrades to
`UNVERIFIED(internal_error)` rather than taking the turn down.

---

## 6. The integration point the middleware owner must wire

`agents/middlewares/**` was claimed by another agent, so nothing there was
edited. The seam is one call, in `alpha.verification.integration`, implemented
against and tested independently of the wiring.

Diff-shaped, in `FinishFirstVerifierMiddleware.after_model`, at the point where
the Finish-First notice is produced (`finish_first_verifier_middleware.py:316`,
where `code_write_count and not had_verification` is already computed):

```diff
@@ class FinishFirstVerifierMiddleware:
     _VERIFY_TOOLS = frozenset({...})          # unchanged
+    # one new import, at module scope:
+    from alpha.verification import drive_verification_from_turn
+    from alpha.verification.integration import workspace_surface_reader

@@ def after_model(self, state, runtime):
         if code_write_count and not had_verification:
+            directive = drive_verification_from_turn(
+                turn_messages=turn_messages,
+                runtime=runtime,
+                controller=self._verification_controller(runtime),
+                surface_reader=workspace_surface_reader(<thread workspace>),
+                repair_outcome=repair_outcome,
+                changed_paths=<the run's changed paths>,
+            )
+            if directive is not None and directive.kind == "repair":
+                # Same mechanism the critic path already uses. Not a second one.
+                return {"messages": [RemoveMessage(id=last_ai.id)], "jump_to": "model"}
+            if directive is not None and directive.settled:
+                return self._reject(last_ai, key=key, attempts=attempts,
+                                     prompt=render_report(directive.report))
             content = last_ai.content
             ...
```

Two supporting lines the owner also owns, because they are about the
middleware's own state:

- a per-`(thread_id, run_id)` controller cache, reset in `before_agent` exactly
  as `self._critic_retries` already is, and
- `repair_outcome=RepairOutcome.COMPLETED` on the turn after a repair directive
  (or `DECLINED` when the turn made no change).

### Why the placement is theirs

*Mechanically:* `RemoveMessage` + `jump_to: "model"` is the middleware's
mechanism. It is budgeted there, thread-safe there, and re-implementing it here
would create a second retry path with its own budget — the exact defect this
controller's neighbour in that file was written to prevent. The controller
deliberately cannot inject a message; it returns a directive and waits.

*Ownership:* deciding **when** a turn warrants a verification loop is
agent-behaviour policy, and the turn boundary is the middleware's. This
controller answers a narrower question — given that you want a loop, what
happened — and would be wrong if it decided for itself that every terminal
message needed one. `drive_verification_from_turn` returns `None` when the turn
wrote no code or already verified, so a caller that places the call carelessly
still changes nothing.

### What was deliberately not built

**No `app/gateway/routers/verification.py`.** The assignment listed one as an
option ("new route, if one is needed") and it is not needed: the loop's subject
is a run, not an HTTP request, and a new router would be unregistered by
`app/gateway/app.py` (not owned here), unreferenced by production, and an orphan
under `tests/test_no_orphan_modules.py`. Adding a dead route to satisfy a
checklist is the same category of dishonesty this component is about.

**No `AppConfig` field.** `config/verification_loop_config.py` is standalone and
`resolve_verification_loop_config()` reads the section opportunistically, so it
behaves identically before and after the field exists. The one line the
specialists agent (or the lead) needs, in `config/app_config.py`:

```python
# import block, alphabetical near the other config models
from alpha.config.verification_loop_config import VerificationLoopConfig
# AppConfig field, next to `verification`
verification_loop: VerificationLoopConfig = Field(default_factory=VerificationLoopConfig, description="Bounded verification loop (attempt budget, evidence and failure-evidence bounds)")
```

and in `config.example.yaml`, next to the other sections:

```yaml
verification_loop:
  enabled: true
  max_attempts: 2
  failure_evidence_chars: 4000
  test_surface_max_files: 500
```

Until then the shipped defaults apply, which are the same values. The loop
resolves them through `resolve_verification_loop_config()`, so it is a real
production reference rather than a decorative module.

---

## 7. What this loop still cannot do

Stated as limitations because a flattering summary is worth less than an
accurate gap list. This section is the part of the work most worth reading.

### 7.1 A non-deterministic failure

The loop re-runs the command after each repair and reads the *latest* recorded
execution. It has no notion of a flake:

- A test that fails on attempt 1 and passes on attempt 2 is reported
  **VERIFIED**. The evidence binder takes the latest matching execution, so one
  green run out of two is a pass, and this loop inherits that. It will not
  re-run to confirm, and it will not report "passed once after failing".
- A test that fails on every attempt is repaired against output that may not
  reproduce, and the repair may be chasing a different failure than the one the
  model saw. Nothing in the loop detects that the second failure is not the
  first failure.
- A test that is slow and flaky under load can make the budget look like a
  diagnosis: three attempts, three different failures, and a budget-exhausted
  UNVERIFIED that reads like a hard problem and is actually a race.

**What it does about it:** nothing beyond honesty. The last reading and the
evidence text are reported, so a human can see that attempt 1 and attempt 3
disagree. A flakiness detector — re-run the same commit N times before accepting a
pass, or classify a repeated-different-failure pattern as a flake — is a real
piece of work and is **not** in this change. This is the largest single gap.

### 7.2 The fix is correct and the test is wrong

Sometimes the implementation is right and the assertion encodes the bug. The
right move is to change the test *and say why*, which is a legitimate engineering
outcome. This loop cannot express it:

- Any change to a watched test file that removes or alters a canonical assertion
  is a reduction, and a reduction is refused. A justified test change is refused
  with the same verdict as a cheat, because the comparison is structural and has
  no way to read intent.
- The only escape is the prompt's request: the model is told that if the test is
  what is wrong, it should *say so* instead of editing it. That is a request to
  an agent, not a mechanism. A model that ignores it and edits the assertion is
  caught — the loop settles UNVERIFIED — but the legitimate case is then treated
  the same as the illegitimate one.
- The comparison also cannot see intent in the other direction: a repair that
  keeps every assertion and weakens the *implementation* to match a wrong test
  passes this guard and is genuinely wrong. Nothing here judges whether the
  implementation is correct; see §7.5.

**What it does about it:** the refusal is the safe direction, because the cost of
refusing a justified test change is an honest UNVERIFIED and a human's attention,
while the cost of accepting a cheat is a manufactured green result. But the
"weakened test" reason does not distinguish the two cases, and a caller reading
`weakened_test` should read it as *"someone changed the tests"* and go look.

### 7.3 When it cannot tell the difference

Explicitly, the honest answer is **UNVERIFIED, and it says which of the
undetermined cases it was**. The cases it cannot distinguish *from each other*,
and the ones it cannot distinguish at all:

- **A remote sandbox with no host mirror.** The surface reader reads the thread
  workspace on the host. A provider that does not mirror it reads back empty,
  every watched file is unreadable, and every repair is refused with
  `weakened_test`. That is the correct direction to be wrong in, and it is
  indistinguishable from a repair that genuinely deleted the tests. Fixing it
  needs the sandbox read path (`read_current_file_content`) wired into the reader,
  which the lazy-delegate structure already allows for the checker.
- **A `conftest.py` that skips collection globally**, a `pytest.ini` `addopts`
  that deselects, or a runner option that narrows the selection. The comparison
  watches *files the run changed*; a config file is not a test file by the
  guard's own definition, so changing it is invisible here. The evidence binder
  separately refuses a *recorded* execution whose command carries `--ignore` or
  `--deselect` against the criterion's target — but that only covers the
  criterion's spelling, not a narrowed run the controller itself launched.
- **A weakened assertion helper in production code.** The comparison covers
  assertion text, test names and skip markers inside the watched files. Replacing
  a fixture with one that returns a value that satisfies every assertion changes
  nothing the comparison can see.
- **A test that asserts less without changing its text** — a `for` loop over an
  empty list, a fixture that returns early, a mock that no longer fails. The
  assertion multiset is identical, so the comparison reports "unchanged".
- **A test file the run never changed** that was already weak. Not watched, so
  not this loop's business; worth knowing that the guard's coverage is exactly
  the changed test files.
- **The checker's own accepted boundaries** (`subagents/AGENTS.md`, "Known
  accepted boundaries", pinned by `TestKnownBoundaries`): a Makefile that
  swallows failures, a runner exiting 0 on failure, a bare criterion executable
  trusting PATH, relative criterion targets resolving in the wrapper's cwd. The
  loop routes through the checker and inherits all of them. It does not paper
  over them and it does not add to them.
- **The evidence tail is 1000 characters.** The harvester bounds it. A failure
  whose diagnostic is in line 4000 of the output hands the model a truncated
  failure, and the prompt says so — but "says so" is not the same as solving it.
  `failure_evidence_chars` bounds it again on the way into the prompt.

### 7.4 It is not wired

The controller is complete and tested; the call that starts it lives in a file
another agent owns. Until §6 lands, **nothing in a running Alpha invokes this
loop.** That is the single most important sentence in this document. The
component is a capability the project can operate the moment one line is placed,
and until then it is a capability the project has documented — which is exactly
the condition it was written to end.

### 7.5 It verifies evidence, not correctness

VERIFIED means *a recorded, anchored, passing test execution exists for this
command*. It does not mean the change is right, that the tests are the right
tests, that the command covers the change, or that nothing else broke. A suite
of three assertions can pass over a change that broke four other things. This is
the same boundary `acceptance_checks.py` states in its own `_LIMITATION` line,
and the loop renders it on every report:

> recorded test-run evidence only; it does not validate the correctness of the
> change

### 7.6 Smaller gaps, listed so they are not discovered later

- **The command is the caller's.** The controller will not invent one. A caller
  that supplies a command which does not exercise the change gets a VERIFIED
  verdict about the wrong thing, and nothing here can tell.
- **The watched surface is the caller's changed-path list.** A caller that does
  not wire `watch()` gets `test_surface_unavailable` — safe, but it means the
  coverage guard never runs.
- **No parallelism.** One controller per run; two runs verifying concurrently
  share nothing (tested), but a single run cannot verify two commands at once.
- **No escalation path beyond a report.** There is no "loop is failing, hand this
  to a human" channel; the caller receives an UNVERIFIED and decides.
- **The surface cap is 500 files.** Beyond it the capture is truncated, which is
  treated as indeterminate — safe, and a run that touches more test files than
  that will refuse every repair.
- **Cost.** Each attempt runs the real tool path, so a `max_attempts: 2` loop
  can execute the test command three times inside one run's wall-clock budget.
  The budget bounds attempts, not seconds.
- **`BashToolExecutor` is untested against a live sandbox.** Its correctness
  argument is structural — it calls the real tool and the real harvester — but no
  test in this branch runs it against a real sandbox, because doing so would
  mean starting the stack this work is not permitted to start. The first live
  exercise of that class is a real risk, and it should be the first thing the
  integrator does.

---

## 8. Verification performed

Run from `backend/` in the `alpha-verifyloop` worktree on branch
`agent/verifyloop` (base `43b5fbb`). No stack was started, no port was bound,
and nothing outside this worktree was touched.

```
$ PYTHONPATH=. .venv/Scripts/python.exe -m pytest \
    tests/test_verification_loop.py tests/test_verification_honesty.py \
    tests/test_acceptance_checks.py tests/test_subagent_acceptance_followup.py \
    tests/test_harness_boundary.py tests/test_no_orphan_modules.py \
    tests/test_feature_manifest_wiring.py -q

405 passed, 13 skipped in 489.00s
```

`test_acceptance_checks.py` (the existing checker's own 250+ case suite) is in
that run and is green, which is the direct evidence that nothing was weakened to
make any of this work.

```
$ .venv/Scripts/python.exe -m ruff format --check <every file this change owns>
$ .venv/Scripts/python.exe -m ruff check <every file this change owns>
All checks passed!
```

```
$ backend/.venv/Scripts/python.exe scripts/generate_docs_index.py --check
```

The generator was run against a copy of **`origin/main`'s** generator
(`516ab9b`) rather than this branch's stale base, because this branch is 28
commits behind and the base's generator also lacks a classification a *later*
main commit added. Two runs, differing by exactly the one line this change owns:

| generator | exit | verdict |
| --- | --- | --- |
| `origin/main` as-is | **2** | `unclassified document(s): VERIFICATION_LOOP.md` |
| `origin/main` + the `VERIFICATION_LOOP.md` entry | **0** | `documents=73 skipped=1` — **gate green** |

`docs/INDEX.md` was regenerated into a temp path to read that result and
**restored**: it is owned by the wiring agent and is byte-identical to this
branch's base. On a merged tree it will drift by exactly one line — the
`VERIFICATION_LOOP.md` row — which the index owner regenerates as usual.

---

## 9. Files

**New, this change**

- `backend/packages/harness/alpha/verification/` — `__init__.py`, `AGENTS.md`,
  `budget.py`, `contract.py`, `controller.py`, `execution.py`, `integration.py`,
  `surface.py`
- `backend/packages/harness/alpha/config/verification_loop_config.py`
- `backend/tests/test_verification_loop.py`
- `backend/tests/test_verification_honesty.py`
- `backend/tests/verification_fixtures.py`
- `docs/VERIFICATION_LOOP.md` (this file)

**Modified, one file, four lines**

- `scripts/generate_docs_index.py` — the `VERIFICATION_LOOP.md` `FILE_OVERRIDES`
  entry. Without it the generator **fails closed** on this document and the
  docs-index CI job cannot pass, which is the project's own documented rule. No
  other line in that file was touched.

**Read, not edited** — `agents/middlewares/finish_first_verifier_middleware.py`,
`subagents/acceptance_checks.py`, `subagents/executor.py`,
`selfrepair/assertion_guard.py`, `sandbox/tools.py`, `config/app_config.py`,
`config.example.yaml`, `scripts/generate_docs_index.py`, `contracts/`.

**Deliberately untouched** — `runtime/side_effects/**` (zero production callers;
another agent is wiring it), `alpha/swarm/**`, `alpha/team/**`, `alpha/models/**`,
`alpha/errors/**`, `frontend/**`, `contracts/**`, `config.yaml`, `app/gateway/**`.
