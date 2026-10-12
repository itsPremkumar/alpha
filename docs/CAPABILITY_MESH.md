# Alpha Capability Mesh

Evidence-backed capability records for every runnable configuration Alpha could
route to — an agent, a model config, a toolset, a skill bundle — and a set of
instruments that turn outcome history into *honest* estimates, plus read and
preview surfaces over all of it.

**The mesh is a measurement and preview plane. It is disabled by default, and
even enabled it dispatches nothing.** No agent run is ever created, routed,
composed or budgeted through it. What it does today is answer three questions
honestly: *what has been measured*, *how confident that measurement is*, and *who
a routing plan would pick*.

The engine contract lives in
[`backend/packages/harness/alpha/capability_mesh/AGENTS.md`](../backend/packages/harness/alpha/capability_mesh/AGENTS.md).
This page is the user/operations view.

---

## 1. Why it exists

"The agent that usually handles this" is a guess, and a guess that reads like a
fact is worse than no answer. The mesh makes the guess *visible and labelled*:
every number it returns travels with the **evidence level** behind it, and an
unmeasured capability reads as `unmeasured`, never as a score that happened to
land badly.

Three rules make that labelling true rather than decorative:

| Rule | Why it is load-bearing |
| --- | --- |
| A passport reports its **worst** evidence level | One unmeasured estimate beside three verified ones makes the answer unmeasured for that capability, so it can never over-claim. |
| A score always carries the **profile version** that produced it | Every aggregation is scoped to exactly one rubric version: a `v1` sample never averages into a `v2` answer. |
| A future-dated timestamp can only **downgrade** evidence | A corrupted or forged stamp cannot buy a capability a better level. |

Evidence levels are derived by one rule and never hand-set: `unmeasured` →
`observed` → `benchmarked` → `outcome_verified` → `recently_revalidated`.

## 2. The 23 library modules, by phase

The package is split so a later phase extends the contract instead of replacing
it. Phases A–G are 23 pure, offline modules; phase H adds the two modules that
give them a tool and a router.

| Phase | Modules | What the phase adds |
| --- | --- | --- |
| **A — records** | `models.py`, `store.py`, `passports.py` | Capability passports, the atomic JSON store, and UNMEASURED derivation from what the registries already know. |
| **B — learning instruments** | `memory.py`, `calibration.py`, `drift.py` | Cross-run outcome memory (`estimate_for`), does stated confidence mean anything (Brier), and has performance moved (drift signals). |
| **C — routing plane** | `fingerprint.py`, `decomposition.py`, `fit.py`, `assignment.py`, `policy.py` | Situation fingerprint, the bounded microtask contract, hard-gate-first fit scoring, roles (PRIMARY / CHALLENGER / VERIFIER / INTEGRATOR), and the user-selectable decision-policy table. |
| **D — structure** | `graph.py`, `complementarity.py`, `team.py`, `gaps.py` | The empirical capability graph, measured pair value, greedy portfolio selection, and capability-gap classification (model / tool / evidence / quality). |
| **E — composition** | `synthesis.py` | Best-per-slot composition over contributed artifacts, with conservative conflict detection. |
| **F — independence and legibility** | `independence.py`, `shadow.py`, `explain.py` | Correlated-error awareness, shadow routing / canary policy (predictions only), and bounded explanation panels. |
| **G — planning** | `scheduler.py`, `joint_routing.py`, `evaluation.py`, `feedback.py` | Value-of-information scheduling, joint model+instructions+skills+toolset resolution, per-category rubric evaluation, and a specialist-check / user-feedback ledger. |
| **H — surfaces** | `service.py`, `config.py` | `CapabilityMeshService` — the single implementation both callers use — and the `capability_mesh` config slice. |

Plus `__init__.py`, which re-exports the package surface.

## 3. The model tool: 12 actions, one write

The lead agent calls one tool, `capability_mesh`, with an `action`. Eleven are
reads; **exactly one** writes.

```
capability_mesh action="passports"      entity_id=""          # every passport + its evidence level
capability_mesh action="memory"         entity_id=""          # outcomes + conservative credit
capability_mesh action="calibration"    profile_version=""    # is stated confidence meaningful?
capability_mesh action="drift"          entity_id=""          # has performance moved?
capability_mesh action="policy"         policy=""             # the resolution table
capability_mesh action="route"          task="..."            # PREVIEW the PRIMARY/CHALLENGER/VERIFIER plan
capability_mesh action="team"           task="..." budget_usd=50   # PREVIEW team selection
capability_mesh action="compose"        task="..." artifacts_json='[...]'   # PREVIEW composition
capability_mesh action="explain"        task="..."            # PREVIEW bounded explanation panels
capability_mesh action="independence"   entity_a="" entity_b=""   # are these two independent?
capability_mesh action="shadow"                              # shadow router self-evaluation
capability_mesh action="record" entity_id="" capability="" outcome="" quality=0.8   # THE ONLY WRITE
```

| Action | Kind | What it answers |
| --- | --- | --- |
| `passports` | read | Every capability passport, each with the evidence level behind it. |
| `memory` | read | Recorded outcomes plus the conservative credit report. `selected` counts fully; `repaired` and `materially_edited` partially; an unmeasured attempt is still an attempt. |
| `calibration` | read | Does a self-stated confidence predict measured quality? Below the sample floor the verdict is **unresolved**, never "well calibrated". |
| `drift` | read | Has an entity's performance moved? A pure reader: it reports and never acts. |
| `policy` | read | The decision-policy resolution table — one inspectable row per policy. The `local` row discloses that it declares a locality intent the mesh cannot enforce. |
| `route` | read (preview) | Fingerprint the task, decompose it, and show the plan a policy would produce. |
| `team` | read (preview) | The small team the task's required capabilities would select, with each member's marginal contribution and why selection stopped. |
| `compose` | read (preview) | Best-per-slot composition over artifacts you supply. Incompatible assumptions are **never** merged into a synthetic "best of both": the composed artifact stays absent while any slot conflicts, and the alternatives travel with it. |
| `explain` | read (preview) | Bounded panels: who was chosen for which step and why, plus derived confidence captions. |
| `independence` | read | Are two entities genuinely independent, or do they share a model family and provider? `known: false` means nothing was measured — neither independent nor dependent. |
| `shadow` | read | The shadow router's self-evaluation. Predictions only: it never calls a model and never spends budget. |
| `record` | **write** | Append one measured outcome. Stores an opaque task fingerprint, **never** task text. |

The decomposition is the **deterministic heuristic baseline** — no model is
consulted, and the result says so. Any store read or config read happens off the
event loop; a mesh-specific behavioural guard fails the suite if one lands on it.

## 4. The HTTP routes

`/api/capability-mesh` mirrors the tool. Every route requires an authenticated
caller (`get_current_user_from_request`), exactly as `/api/arena` does. The mesh
directory is **installation-scoped**, not per-user: a capability measurement
describes the deployment's configuration, not one person's data, so the payloads
are not partitioned per user and nothing served here is per-user private.

| Method | Path | Mirrors |
| --- | --- | --- |
| `GET` | `/api/capability-mesh/profiles` | `passports` (`?entity_id=`, `?entity_type=`) |
| `GET` | `/api/capability-mesh/memory` | `memory` (`?entity_id=`, `?capability=`, `?task_category=`, `?limit=`) |
| `GET` | `/api/capability-mesh/calibration` | `calibration` (`?profile_version=`) |
| `GET` | `/api/capability-mesh/drift` | `drift` |
| `GET` | `/api/capability-mesh/policy` | `policy` |
| `GET` | `/api/capability-mesh/independence` | `independence` |
| `GET` | `/api/capability-mesh/shadow` | `shadow` |
| `POST` | `/api/capability-mesh/route` | `route` |
| `POST` | `/api/capability-mesh/team` | `team` |
| `POST` | `/api/capability-mesh/compose` | `compose` |
| `POST` | `/api/capability-mesh/records` | `record` |

```bash
curl -s http://localhost:2026/api/capability-mesh/policy | jq .
curl -s -X POST http://localhost:2026/api/capability-mesh/team \
  -H 'content-type: application/json' \
  -d '{"task":"audit the auth module","required_capabilities":["security-review"]}'
```

Status codes:

| Situation | Status | Why |
| --- | --- | --- |
| Success | `200` | Includes planning replies, which are previews. |
| Mesh disabled | `503` | Detail names `capability_mesh.enabled`. A disabled plane is not an empty one. |
| Store unreadable | `503` | Detail is the **store's own refusal reason**, never an empty `200` with `count: 0`. |
| Mesh refusal | `400` | Unknown policy, out-of-bounds budget, a record its constructor rejects — the mesh's own words. |

## 5. Configuration and its safe defaults

`config.yaml` → `capability_mesh`, declared next to `arena:` in
`config.example.yaml`. `extra="forbid"`, so a typo is a load error.

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | **`false`** | Bind the tool and the router. Off = every surface refuses with the reason named. **On = read and preview surfaces only; nothing is dispatched.** |
| `directory` | `""` | Where `passports.json`, `capability_memory.jsonl`, `pair_estimates.json` and `shadow.json` live. Empty resolves to `runtime_home()/capability_mesh`, resolved **lazily at read time** so a hot reload never stats the filesystem. |
| `max_records` | `DEFAULT_MAX_RECORDS` | Bounded retention for the memory's JSONL tail. Refused below 1, never clamped. |
| `default_profile_version` | `"v1"` | The rubric identity every aggregation is scoped to. An empty value is caught one layer later, where it matters: a record carrying a `quality` with no rubric behind it is refused by name. |
| `exploration_budget_fraction` | `0.1` | The operator's declared share of a budget that *may* be spent probing an under-measured candidate. **Reported, never spent** — nothing in this package executes a probe. |

```yaml
capability_mesh:
  enabled: false
  directory: ""
  default_profile_version: v1
  exploration_budget_fraction: 0.1
```

Durable state is **single-process JSON/JSONL**: atomic against a crash in one
process (temp file + fsync + `os.replace`), **never** cross-process exactly-once.
No lock is taken; a second Gateway worker sharing the directory must serialize
writes itself. A missing file is genuinely empty — absence is not corruption.

## 6. Honesty boundaries

These are the claims you must **not** make about this feature. They are enforced
in code and pinned by tests.

1. **Nothing dispatches a real agent run through the mesh.** There is no executor
   wiring anywhere in the package. `route()`, `select_team()`,
   `compose_deliverable()` and `explain_plan()` are read locally and their
   results are proposals. Saying "Alpha routes work with the capability mesh"
   would be false today.
2. **Every planning reply is a preview.** `route`, `team`, `compose` and
   `explain` all return `preview: true` and `dispatched: false` with a sentence
   saying so. An INTEGRATOR is still never auto-assigned.
3. **There is no measured data in the tree.** No executor writes records from a
   live run, so a fresh install honestly answers *nothing has been measured*
   rather than a fabricated leaderboard.
4. **An unreadable store surfaces an error, never an empty answer.** The tool
   returns `Capability mesh store error: <the store's words>`; the router returns
   `503` with that same text as `detail`. An empty list and a corrupt file drive
   opposite operator decisions, so they must not look alike.
5. **The learning controls are advertised, not implemented.** `LearningControls`
   (rate a result, opt out of routing experiments, disable cross-run performance
   memory, clear learned profile updates) names what a real UI must offer. This
   package has no UI, no route and no runtime; all four flags default to `False`
   and setting one changes nothing.
6. **The heuristics are crude and say so.** Fingerprinting is keyword-window
   matching; assumption extraction and conflict detection are string-level
   matching; the fit weights and the credit formula are documented **starting
   policies**, not truths. A missed conflict is a false `COMPATIBLE`.
7. **Heuristics refuse more than they accept.** Bounds are refused, never clamped.
   An unpriced action is never free. An unpriced team is `null`, not a reassuring
   `0.0`. An agreement rate over zero comparisons is `None`, and a hand-built
   report is refused if it tries to state otherwise.
8. **Routing is never decided by role variety.** Diversity is measured on units —
   model family, provider, tool path, verification method, entity kind. Two
   members differing only by role are not independent, and the mesh names the
   shared value when it finds one.
9. **A capability measure is not a claim verdict.** The mesh measures how well a
   dispatchable configuration performs; Arena's evidence ledger verifies claims
   inside a candidate answer. Never conflate them.

## 7. Where the code and tests live

| Concern | Path |
| --- | --- |
| Engine contract (all invariants) | [`backend/packages/harness/alpha/capability_mesh/AGENTS.md`](../backend/packages/harness/alpha/capability_mesh/AGENTS.md) |
| Model tool | [`backend/packages/harness/alpha/tools/builtins/capability_mesh_tool.py`](../backend/packages/harness/alpha/tools/builtins/capability_mesh_tool.py) |
| HTTP router | [`backend/app/gateway/routers/capability_mesh.py`](../backend/app/gateway/routers/capability_mesh.py) |
| Config slice | `config.yaml` → `capability_mesh` |
| Feature plan this implements | [`alpha-arena-new-capability-mesh-features-plan.md`](alpha-arena-new-capability-mesh-features-plan.md) |

Tests: `backend/tests/test_capability_mesh_*.py` — the per-module suites plus
`test_capability_mesh_tool.py` and `test_capability_mesh_router.py`, which cover
every action, the preview labelling, the store-error surface, the disabled-mesh
refusal, and the no-blocking-I/O guard. All offline, zero model calls.