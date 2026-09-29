# Multi-agent work plan

This is the operating plan for running several AI agents against this repository
at once, what each one owns, and what has to be true before anything is merged.

It exists because the obvious approach — point every agent at the main working
tree — fails in ways that are not obvious until they cost you an afternoon. Every
rule below was learned by hitting the failure it prevents.

## The three rules that matter

**1. One worktree per agent. No exceptions.**

Ten agents sharing one directory is not parallel work, it is a race. It has
already cost real time here:

- A PowerShell `Get-Content | Set-Content` round-trip by one agent corrupted the
  UTF-8 in `frontend/src/components/lion-pet/LionPet.tsx` — 23 non-ASCII
  characters, caught only because a *different* agent's test run happened to cover
  that file.
- An agent committed to `main` while another agent's merge was staged. The merge
  survived, but the commit message was not the one the merging agent wrote, and
  nobody can now say who authored it.
- An agent checked out a branch (`agent/memory`) in the shared tree. `HEAD` moved
  off `main` under a second agent, so a completed merge of three branches sat
  orphaned: `main` never advanced, and `git push` reported
  `Everything up-to-date` while the remote was two merges behind. Diagnosing that
  took several steps, and the only symptom was a push that silently did nothing.
- Four separate live verifications died on `ConnectionRefusedError` because
  another agent restarted the stack mid-measurement.

**2. One owner per file, stated in the assignment.**

State the exact files an agent may edit. If two assignments need the same file,
either sequence them or split the work — do not let them discover the overlap by
producing a conflict.

**3. No agent starts or restarts the application stack.**

One stack, owned by one agent. Every other agent verifies against it read-only,
or does not verify live at all. `make dev` takes about four minutes to become
ready, and a restarting agent invalidates everyone's live measurement.

## Setting up

```bash
cd alpha
git fetch origin
for a in trace skills runui honesty docs memory voice audit-iso; do
  git worktree add ../alpha-$a -b agent/$a origin/main
done
```

Each worktree needs its own backend virtualenv and `frontend/node_modules`:

```bash
# per worktree
cd ../alpha-$a/backend && uv sync
cd ../alpha-$a/frontend && pnpm install
```

## Assignment template

Give every agent this, with the sections filled in:

```
WORKTREE: ../alpha-<name>   BRANCH: agent/<name>
MODEL:    <provider/model#effort>   (see the table below)

HARD CONSTRAINTS
  - Stay inside FILES YOU OWN. Do not edit anything else. If you need a file
    another agent owns, stop and report the conflict instead of editing it.
  - Do NOT run `make dev`, `make dev-daemon`, `scripts/serve.sh`, `uvicorn`, or
    `next dev`. Do NOT bind ports 2026, 3000 or 8001. Another agent owns the
    running stack and two agents have already collided on those ports.
  - Do NOT commit to main and do NOT push.
  - Do NOT `git stash`. It has caused index conflicts in this repository.
  - Do NOT create or remove a git worktree.

FILES YOU OWN
  <exact list, with paths>

SHARED FILES YOU MAY READ BUT NOT EDIT
  <list, with why — e.g. the run-event contract, config.yaml>

VERIFY BEFORE REPORTING DONE
  <the exact commands, and what "green" means>

DELIVERABLE
  <what the report must contain: root causes, evidence, files changed, what you
  deliberately left alone and why>
```

The last line matters more than it looks. An agent that lists the candidates it
investigated and rejected is auditable; one that only lists what it changed is not,
and you cannot tell the difference between a careful audit and a guess.

## Model and effort per task type

All of these are keyless. Do not assign an `openrouter/*` model: the configured
OpenRouter key has no credits and every request answers HTTP 402, which is how
the default model went broken in the first place.

| Task shape | Model | Effort | Why |
| --- | --- | --- | --- |
| **Everything, currently** | `opencode/space-bunny-free` | `low` … `max` | **The only model in the pool that answers.** See the measurement below. |

### Measured 2026-09-29: three of the four models in this table do not work

The table above used to recommend four different models by task shape. That was
wrong, and it was wrong in the exact way this document warns about two
paragraphs further down.

I assigned `nemotron-3-ultra-free`, `muse-spark-1.3-contributor-free` and
`mimo-v2.6-flash-free` to five agents. A research agent independently reported
that the alternatives were failing, and I then measured it directly against the
provider, alternating the order to separate a per-model fault from rate
limiting:

```
  OK    space-bunny-free                      200   3550ms  reply="391"
  FAIL  nemotron-3-ultra-free                 500    345ms  Internal server error
  FAIL  muse-spark-1.3-contributor-free       500    865ms  Internal server error
  FAIL  mimo-v2.6-flash-free                  500    331ms  Internal server error
  FAIL  mimo-v2.6-flash-free                  500    355ms  Internal server error
  FAIL  muse-spark-1.3-contributor-free       500    337ms  Internal server error
  FAIL  nemotron-3-ultra-free                 500    317ms  Internal server error
  OK    space-bunny-free                      200   5220ms  reply="391"
```

The same model succeeded at positions 1 and 8, between failures, so this is not
throttling. `space-bunny-free` returns 200, the correct arithmetic answer
(`17*23 = 391`), working `tool_calls`, and all five declared effort rungs — a
deliberately bogus rung correctly returns 400.

The cost was real: one agent dispatched on `nemotron-3-ultra-free` died with an
upstream 504 and produced nothing, and two more were re-dispatched.

**So the model axis is not available for variation right now.** The only axis
left is the effort rung, which `space-bunny-free` declares as
`low, medium, high, xhigh, max`. Use that to separate a mechanical sweep from a
design decision.

Two consequences worth stating:

- The `effort` field is now the whole of the differentiation, so it has to carry
  real weight. Do not assign `max` reflexively; it is slower for a reason.
- This pool is a single point of failure and it is not Alpha's. The pin
  `free:opencode-zen:space-bunny-free` in `config.example.yaml` is likewise a
  single anonymous model of unknown provenance that can be withdrawn without
  notice. The strongest argument for turning on the already-built, fail-closed
  `model_routing:` machinery is that it is the only thing standing between a
  provider-side withdrawal and a dead default.

**Re-probe before you dispatch, every time.** The catalogue listing a model is
not evidence that it works, and this document's own rule — "verify a model
before assigning ten tasks to it" — is the rule I broke.

Effort rungs are only offered by models that declare them — `space-bunny-free`
exposes `low, medium, high, xhigh, max`; `muse-spark-1.3-contributor-free`
exposes `minimal, low, medium, high, xhigh`. Do not ask for `max` on a model that
does not declare it; the run boundary rejects a value that names no rung.

**Verify a model before assigning ten tasks to it.** A model that returns HTTP 200
and a correct answer on one arithmetic question is a candidate; a model that has
answered HTTP 402, HTTP 404 and HTTP 401 on three different questions in one
session is not, however good it looked in the catalogue.

## Merge order

Merge in dependency order, one branch at a time, and run the gate after **each**
merge rather than once at the end. A failure after the third merge is far more
expensive than one after the first.

A workable order for the current backlog:

1. Backend runtime and contracts (`runtime/`, `observability/`, the run-event
   contract) — everything downstream reads it.
2. Backend subsystems (`agents/memory/`, `multimodal/`, `skills/`).
3. Generated contracts and docs — these must be regenerated *after* the code they
   describe, never before.
4. Frontend clients (`lib/*.ts`) and their tests.
5. Frontend components and views.
6. Frontend chrome and design.

Rules for the merge itself:

- `git merge --no-commit --no-ff`, then inspect `git diff --name-only
  --diff-filter=U` before committing anything.
- **Check the overlapping files by hand.** A clean auto-merge on a file two
  agents both edited is not a clean merge; it is one change silently dropped. Read
  the merged file and confirm both edits are present.
- The commit-message hook enforces Conventional Commits, so a merge subject has
  to be `chore(scope): ...` or `feat(scope): ...`. `merge(docs): ...` is rejected.
- Record what you verified **before** the merge, not after. A gate that runs on the
  merged tree cannot tell you which branch broke it.

## Verification is the deliverable

The point of all of this is that the claim "it works" is backed by a measurement.
A green unit test is necessary and not sufficient; several of the worst bugs found
here passed every test that existed:

- A failing `python_repl` call was recorded as `status: "success"`. Nothing
  checked the classifier against the REPL's actual `Error (Name): value` format.
- A config key documented as the cost fallback was read by nobody, so every cost
  number was `null` while the template shipped eight price entries.
- A model slug that the provider had retired 404'd on the first run of every fresh
  install, in two separate namespaces, with no check comparing them.
- `GET /projects/{id}/events` emitted a raw float where every sibling route emitted
  ISO, so a client reading the field as a date showed "time not reported" for
  every row.

So for anything an agent changes, require: the specific tests, a live response or
a live DOM measurement, and the file/line for each defect. "Looks right" is not a
result.

## When two agents were given the same work

It will happen, because two people read the same bug report. The recovery, in
order:

1. Stop one of them immediately. Do not let them both keep going.
2. Ask the further-along one for its root cause as a fact about the code.
3. Commit the other's work on a branch before it is overwritten — even
   uncommitted, `git stash` aside, a patch is cheaper than reconstructing from a
   chat log.
4. Re-verify the survivor against the full suite, not just its own new tests.

## What is deliberately not in this plan

- **No agent runs the stack.** One stack, one owner. It is the single most
  expensive thing to get wrong here.
- **No agent commits to `main`.** The lead merges; agents commit to their branch.
- **No automatic merging on "tests pass".** A passing suite has not caught the
  bugs in this repository that mattered most.
