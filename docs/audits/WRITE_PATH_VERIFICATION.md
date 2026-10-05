# Write-Path Verification & The Group Depth Cap

**Date:** 2026-10-05
**Gateway:** `http://127.0.0.1:8001` (restarted mid-session to load the fix)
**Driver:** `backend/scripts/write_path_probe.py`
**Verdict:** **the nesting probes are 17/17 green live after one real defect was found, fixed and negative-controlled.**

Previous rounds graded the orchestration plane (`FLEET_VERIFICATION.md`,
`ORCHESTRATION_PROBES.md`, `BOT_FORGE_VERIFICATION.md`). This one moves to
**write paths** — the mutations no earlier probe touched.

---

## 1. The defect: `POST /subgroups` never enforced `MAX_DEPTH`

**Severity: MEDIUM-HIGH** — a documented, documented-to-be-refused bound was
absent from the route operators actually use.

Measured live, before the fix, by walking `POST /api/groups/{parent}/subgroups`
in a loop against :8001:

```
depth_1  created      depth_5  created
depth_2  created      depth_6  created
depth_3  created      depth_7  created
depth_4  created      -- no refusal at any depth --
```

Eight rooms, no refusal. Meanwhile `DELETE /api/groups/{root}` correctly
returned **409** naming the child, so the tree was real — it was just unbounded.

**Root cause (one sentence).** `alpha.groups.service.create_subgroup` never
called `assert_within_depth`, so the depth cap was enforced only on
`move_room` (the re-parent path) and the creation path could walk a chain down
indefinitely, one level at a time.

`MAX_DEPTH = 4` and `assert_within_depth` were both already correct. The check
existed, was right, and was simply **not wired to the write path an operator
reaches for**. That is why `tests/test_group_nesting.py` was green: it exercises
`move_room`.

**Fix:** call `assert_within_depth` **before** `self._rooms[key] = room`, so a
refusal leaves nothing half-built. `assert_within_depth` reads only
`new_parents`, so passing an unregistered room id is safe.

**Verified live after a Gateway restart:** `refused_at_depth: 5`,
`refusal_status: 409` — depths 1–4 created, depth 5 refused. That matches
`MAX_DEPTH = 4` exactly, since the root sits at depth 0.

---

## 2. Evidence the fix works

`backend/tests/test_group_nesting_depth_enforced.py` — **8 cases**, which
*discover* the boundary by walking until refusal rather than hardcoding it.

**Negative-controlled:** with the `assert_within_depth` call replaced by `pass`,
**6 of the 8 fail**; restored, **8 pass** (3 consecutive clean runs, plus an
isolated run and a `-x` run).

**Surrounding suites: 127 passed** (`test_group_nesting`,
`test_group_nesting_routes`).

The cases are shaped around the directions a depth bug actually takes:

- **legal side pinned too** — a cap that fires *early* is its own defect, so the
  walk asserts the boundary is exactly `MAX_DEPTH`, not merely that *a*
  boundary exists;
- **depth is not breadth** — ten sibling sub-groups at depth 1 are all legal, so
  a guard counting rooms instead of levels is caught;
- **a refusal leaves no half-built room** — `create_subgroup` mutates
  `self._rooms` before `_save()`, so a post-hoc check would leave a room the
  caller was told does not exist;
- **a refusal is surgical** — no existing room goes missing;
- **`move_room` still works** — this is not a replacement for the existing guard;
- **both paths agree** — the live bug in one assertion: the two write paths
  previously disagreed, so a chain buildable by creation exceeded a bound that
  re-parenting enforced.

### Two of my own assertions were wrong, and were corrected rather than bent

Recorded because a test that pins phrasing, or pins arithmetic it never
consulted, is a worse test than none:

1. **Off-by-one in the test's own favour.** The first draft asserted "chain of
   `MAX_DEPTH` is legal, `MAX_DEPTH+1` is refused". The root sits at depth 0, so
   `MAX_DEPTH + 1` rooms is the deepest *legal* chain. The test was wrong about
   the boundary it meant to pin. Now the boundary is discovered by walking.
2. **Pinning phrasing.** The first draft asserted the literal substring
   `"depth"`; the shipped message reads *"would nest 5 levels **deep**"*. Now it
   asserts `"deep"` plus `"maximum is 4"` plus the remediation wording — meaning,
   not spelling.

---

## 3. The nesting probe: 17/17 green

`probe_nesting` verifies inheritance is a **projection, not a copy**, with a
sequence rather than a single read:

1. create parent, create child (`inherit=True`);
2. read the child's roster — its direct list is the parent's membership *as of
   creation*;
3. add a member to the parent who was **not** in the seeded list;
4. re-read the child **with no reconcile step**.

Result: the newcomer appears in the child's `effective`/`inherited` and is
**absent from `direct`**; `direct_count` and `effective_count` both agree with
their lists.

**A first draft of this check was unfixable as written.** It added `coder` — who
was already a seeded member, so the add was a no-op — and then asserted it was
absent from the child's direct list. But `create_subgroup(inherit=True)`
*deliberately copies* the parent's members "so the child starts staffed"; only
**subsequent** changes arrive through the inherited projection. The check now
picks a bot absent from the seeded direct list, which is the only way to tell a
projection from a copy.

`probe_nesting_limits` covers the refusals, where a **refusal is the pass**:

- depth limit refuses at 5, HTTP 409 (was: never);
- `DELETE` refuses while children exist, naming them and naming `?cascade=true`;
- `DELETE ?cascade=true` succeeds.

All probe-created rooms are `wp_`-prefixed and removed in a `finally`, with
cleanup status recorded per room so a probe that leaves state behind is visible
rather than silently accumulated.

### Three more probe defects found and fixed

All three reported the product as broken when it was working:

| Probe check | What I assumed | Reality |
|---|---|---|
| "depth recorded" | `/ancestors` returns `ancestors`/`depth` | returns `{room, breadcrumbs, scope, max_depth}` — read `scope.depth` |
| "authority parent is a visibility parent" | compared `scope.authority_parent` to breadcrumb **names** | it is a **room_id**; breadcrumbs carry both namespaces |
| "depth limit refuses at depth 5" | refusal body contains `"depth"` | it contains `"deep"` |

A probe that misreports a working guard as broken is the same failure class as a
verifier that cannot check — which is why each is recorded rather than quietly
corrected.

---

## 4. Honest limitations

- **Probes not yet run: `lifecycle`, `scheduled`, `workflow`, `skills`,
  `artifacts`.** They are written and lint-clean but **NOT VERIFIED** — this
  round reached the nesting defect and the nesting evidence, and nothing else is
  claimed. In particular the thread-branch history seed, the scheduled-task
  trigger, the dynamic-workflow acceptance honesty and the artifact digest
  conflict guard are all **untested by this harness**.
- **The 2026-10-05 full run reported 3 probes with 4 failures that were *not*
  product defects** — 2 were the group findings above and 2 were the wrong
  request/response shapes listed in §3.
- **One earlier run of the new suite reported 6 failures that have not been
  explained.** The same command had `ruff format` rewrite the test file in the
  same invocation, so pytest may have collected a partially written file; four
  subsequent clean runs (isolated, `-x`, and two full) give no evidence of real
  flakiness. Reported as **likely explained, not confirmed**.
- **Rule matching is still unexercised.** All rooms report `rule_matched: 0`, so
  `GET /{name}/rules/preview` remains **NOT VERIFIED**.
- **No screenshot** of the groups UI — the browser in this environment cannot
  capture, so the UI consequence of the depth fix is unverified visually.

---

## 5. Evidence index

| Artifact | Path |
|---|---|
| Driver | `backend/scripts/write_path_probe.py` |
| Fix | `backend/packages/harness/alpha/groups/service.py` (`create_subgroup`) |
| Regression tests | `backend/tests/test_group_nesting_depth_enforced.py` (8 cases) |
| Surrounding suites | 127 passed |
| Negative control | 6 of 8 fail with the check removed |
| Probe corrections | `backend/scripts/orchestration_probe.py` (`/groups/tree` keys its rows `nodes`, not `rooms`) |