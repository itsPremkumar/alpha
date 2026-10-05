# Orchestration Probes — swarms, subagents, delegation, groups

**Date:** 2026-10-05
**Gateway:** `http://127.0.0.1:8001` (restarted mid-session to load the fix below)
**Driver:** `backend/scripts/orchestration_probe.py`
**Verdict:** **10 probes: 6 PASS, 2 FAIL, 1 BLOCKED (cause recorded), and one CRITICAL defect found, fixed, and re-verified live.**

---

## 1. What was run

Ten probes, each against a *different* subsystem, all concurrent. This follows
`docs/audits/FLEET_VERIFICATION.md` (five bots, single-agent plane); the point
here is the **orchestration plane** — whether Alpha actually decomposes work and
reassembles results, or merely reports that it does.

| Probe | Feature | Verdict |
|---|---|---|
| `swarm_evaluate` | swarm feasibility router | **PASS** |
| `swarm_blackboard` | swarm blackboard message bus | **PASS** |
| `swarm_map_reduce` | swarm v2 DAG + async runner | **BLOCKED** (cause recorded) |
| `subagent_security` | `delegate_to_deep_agent` | **PASS** |
| `subagent_reviewer` | `delegate_to_deep_agent` | **FAIL → FIXED, re-verified** |
| `ralph_loop` | bounded self-improvement loop | **FAIL → FIXED, re-verified** |
| `task_subagent` | `task(subagent_type=…)` | **FAIL** (tool absent — reported honestly) |
| `bot_roster` | roster + forge overlap pre-flight | **PASS** |
| `group_roster` | rule-matched membership projection | **PASS** |
| `group_nesting` | nesting, authority-parent invariant | **PASS** |

---

## 2. The critical defect: the grounding gate decided by prose

**Severity: CRITICAL** — it made the whole delegation surface nondeterministic.

Live refusals, verbatim from the runs:

```
Grounding gate refused this call: irreversible or unclassified call(s) without
confirmation: delegate_to_deep_agent name it in the plan and obtain explicit
confirmation, or choose a reversible tool
```

and for the loop feature:

```
The grounding gate blocked the call — it classified `ralph_loop` as an
unconfirmed irreversible call and requires explicit approval before it can run.
```

**The verdict was not the bug. The nondeterminism was.** Two runs, the *same*
tool, opposite outcomes:

| Run | Tool | Outcome |
|---|---|---|
| `subagent_reviewer` | `delegate_to_deep_agent` | **refused** — reported honestly, no artifact |
| `subagent_security` | `delegate_to_deep_agent` | **allowed** — 4,825 B artifact produced |

**Root cause (one sentence).** `alpha.grounding.gates.DEFAULT_SIDE_EFFECTS` is a
hand-maintained table in which an unlisted tool defaults to
`SideEffectClass.UNKNOWN`, and `check_side_effect` enforces UNKNOWN *identically*
to IRREVERSIBLE — so whether a tool works at all depends on which of the gate's
two branches a run falls through to, and the second branch is satisfied only by
the model naming the tool in plan prose the gate cannot verify.

### The measured size of the gap

| | Count |
|---|---|
| Builtin tools registered (`BUILTIN_TOOLS`) | **132** |
| Entries in the side-effect table | 41 (47 after this fix) |
| Builtin tools with **no classification** | **115 → 110** (85%) |
| Classified | 17 → 22 |

This is the **fifth** occurrence of the identical defect class. The table records
its own history in comments: `ls`, `PROBE_SATISFYING_TOOLS`, `hashline_read`
("observed live on 2026-10-04"), `catalog_tool_search` — each patched one name at
a time. That patching pattern is what produced the 115.

### Why the governance registry could not supply them

`alpha.tools.governance` is the declared owner of risk class and reversibility,
and would be the natural second source. Measured: **0 of 132** builtin tools
carry an explicit `provenance != "default"` declaration. So that registry is
equally unpopulated, and deriving from it would have made every undeclared tool
`read`/`auto`/`reversible` — **weakening the fail-closed guard for all 132.**
Not done.

### The fix, and what it deliberately does not do

Classified the delegation family with the class `task` already had
(`REVERSIBLE_WRITE`), plus its read half:

- `delegate_to_deep_agent`, `batch_task`, `ralph_loop` → `REVERSIBLE_WRITE`
- `list_available_deep_agents`, `inspect_deep_agent_telemetry`, `await_task_event` → `READ_ONLY`

These are the isolated-child, durable-batch and bounded-retry-loop forms of
`task`, plus the registry introspection that decides all three. That is not a new
judgement call — it is the class the family's own entry point has always had.

**Not done, on purpose:** the other 110 tools were *not* mass-classified. Guessing
`read_only` for `request_secure_credential` or `reversible_delete` would be a real
security regression. The fail-closed default is preserved and pinned, so the
remaining gap stays a *disclosed* gap rather than a silently permittable one.

---

## 3. Evidence that the fix works

`backend/tests/test_grounding_side_effect_classification.py` — **7 cases**.

**Negative-controlled:** with the six added entries removed, **5 of the 7 fail**;
restored, **7 pass**. Specifically pinned:

- the delegation family is classified, and keeps its declared class;
- a named `delegate_to_deep_agent` call passes `check_side_effect`;
- **delegation passes on the strict branch too** — this is the exact
  inconsistency the live runs hit, and a classification that only works on the
  lenient branch is a coin flip, not a control;
- `bash` is **still refused** on that same strict branch — the boundary is not
  weakened;
- an unknown tool is still `UNKNOWN` and still blocked;
- the unclassified count is asserted at 110, so it fails loudly when it moves in
  **either** direction.

Surrounding suites: **186 passed** (`test_grounding_layer`,
`test_grounding_wiring`, `test_tool_governance`, `test_governance_guardrail`).

---

## 4. Other findings

| # | Sev | Finding | Evidence |
|---|---|---|---|
| O1 | — | **No specialist roster provider is registered.** A `map_reduce` swarm composes a correct 4-root DAG, picks a leader, and then assigns **0 of 4** tasks to any worker — reported by the coordinator itself in `metrics.team.roster_provenance`. Plan mechanics are real; per-task model work does not run | probe `swarm_map_reduce`, BLOCKED with cause |
| O2 | HIGH | **The `task` tool is not registered in this deployment.** The lead reported it precisely: *"There is no `task` tool in this environment… a tool that isn't registered can't be invoked, so there is no error message to report. Inventing one would be fabrication."* `subagent_batches.enabled: false` and no subagent `use:` entry | probe `task_subagent`, tool list from the run's own frames |
| O3 | LOW | **`model_routing` declares no mappings**, so `researcher → tier:fast`, `code-reviewer → category:deep`, `security-reviewer → category:deep/frontier`, `test-engineer → tier:coding`, `technical-writer`, `operations-engineer` all resolve to **no model** and silently run on `default_model` | gateway boot log, WARNING `alpha.config.app_config` |
| O4 | — | **Self-correction worked twice, unaided.** Both blocked runs declined to fabricate: *"I won't invent any"*, *"Do not fabricate a specialist's findings."* The honesty contract held under a hard failure | verbatim run answers |

O3 is a **pre-existing configuration gap, not a code defect** — the warning is
correct and honest; the declaration is simply absent.

---

## 5. Honest limitations

- **Group rules were not exercised on a room that has any.** All 5 rooms report
  `direct_count == effective_count == 3` and `rule_matched: 0`, so the
  rule-matching path (`GET /{name}/rules/preview`) was **not** probed. The roster
  probe verifies the count-pairing invariant and direct⊆effective; it does not
  verify rule resolution.
- **`swarm_map_reduce` was not re-run after the fix.** The fix addresses the
  grounding gate, and O1 is a *different* cause (no roster provider), so a re-run
  would not have been expected to change it. Not re-run; not claimed.
- **Single probe run per feature.** No flakiness data, and the
  `subagent_reviewer` refusal could in principle have been the lenient branch —
  though the strict-branch test now pins the class on both paths.
- **`task` absence (O2) was not diagnosed further.** Whether subagent delegation
  is *meant* to be off in this deployment is an operator question, not something
  this probe can settle.
- Durations include ~59 s first-token latency on the keyless free gateway, which
  rate-limits.

---

## 6. Evidence index

| Artifact | Path |
|---|---|
| Driver | `backend/scripts/orchestration_probe.py` |
| Full run log | `logs/orchestration_probe.log` |
| Machine-readable (pre-fix) | `logs/orchestration.json` |
| Post-restart re-verification | `logs/orchestration_verify.log`, `logs/orchestration_verify.json` |
| Fix | `backend/packages/harness/alpha/grounding/gates.py` |
| Regression tests | `backend/tests/test_grounding_side_effect_classification.py` (7 cases) |
| Surrounding suites | 186 passed |
| Prior single-agent fleet run | `docs/audits/FLEET_VERIFICATION.md` |