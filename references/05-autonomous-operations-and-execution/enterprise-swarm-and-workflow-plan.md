# Plan: OMO + Hermes Integration â€” Definitive Edition

> Scope: ALL portable mechanisms from Oh My OpenAgent v5.0.0-beta.18
> (`oh-my-openagent-ref`) and Hermes Agent v0.20.2 (`hermes-agent-repo`).
> Parent: `plans/ultimate-harness-implementation-plan.md`.
> Status ledger: DONE = [O1-chains, H6-profiles] (fallback chains + provider
> profiles, `tests/test_model_fallbacks.py` 34 passing). Everything else OPEN.
>
> Seams re-verified 2026-09-13: `agents/middlewares/*.py` (49 files, incl.
> `todo_middleware.py`, `read_before_write_middleware.py`,
> `llm/tool_error_handling_middleware.py`), `agents/lead_agent/prompt.py`
> (routing text pinned by 3 suites), `subagents/builtins/{general_purpose,
> bash_agent}.py` + `registry.py`, `tools/builtins/task_tool.py`,
> `agents/memory/manager.py`, `input-box.tsx:500`, Gateway routers.

## 0. Adaptation principles (binding on every item)

1. **Extend, don't duplicate.** Alpha already owns scheduler durability,
   run lifecycle, cost tracking, skill review. Import the missing mechanism,
   never a parallel system.
2. **Fail-closed + additive contracts.** New behavior defaults OFF or
   transparent; JSON/event contracts extend additively (precedent:
   `subagent_status_contract.json` v2, `stop_reason`).
3. **Cache-safety.** Nothing reorders the leading SystemMessage or mutates
   mid-session prompts (Hermes' own invariant; our coalescer enforces it).
4. **Owner isolation preserved.** Every store read is owner-scoped; user A's
   recall/proposals/teams never leak to user B (extend existing auth tests).
5. **TDD + docs.** `backend/tests/test_<area>_<behavior>.py`,
   `frontend/tests/` mirror + mock-API E2E for UI; `AGENTS.md` nearest file
   updated in the same commit.

## 1. HERMES TRACK (H1â€“H12)

### H1. Post-turn learning fork â†’ `agents/middlewares/learning_fork.py` (NEW)
Hermes `agent/background_review.py`: cheap aux model replays a bounded digest
with memory+proposal tools whitelisted. Digest cap 8k chars newest-first;
writes via `MemoryManager.add_nowait` + H4 store; exceptions isolated.
Config `learning_fork: {enabled:false, model_name, max_proposals_per_run:3}`
(new `config/learning_fork_config.py`). Tests: whitelist, cap, off-by-default
no-op, failure isolation, no prompt-cache churn (token-delta soak).
Accept: proposals appear without changing any prompt bytes. Effort: M.

### H2. FTS5 session recall â†’ `tools/builtins/session_search_tool.py` (NEW) — DONE 2026-09-13
Hermes `session_search` (discover/scroll/read) + FTS5/trigram store. Alpha:
FTS virtual table over `run_events` (alembic migration; memory/JSONL stores
substring-fallback, same API), `after_seq` cursor (precedent: subtask paging),
cron/background demotion rule, owner-scoped rows. Gateway `GET
/api/threads/search` (confirm no clash with the frontend mock route first);
frontend history-search hook. Tests: backend parity, isolation, cursors.
Accept: cross-thread recall, zero embedding imports in path. Effort: M.

### H3. User-model provider ABC â†’ `agents/memory/user_model.py` (NEW)
Hermes `memory_provider.py` + Honcho user-message injection (never system
prompt). ABC `initialize/system_prompt_block/prefetch/sync_turn/
handle_tool_call/shutdown`; `memory.user_model: {provider:null}`; null
provider = byte-identical prompts (snapshot test). First real provider:
file-backed dialectic, no external dep. Tests: lifecycle order, snapshot
stability. Effort: M.

### H4. Skill proposal queue â†’ `routers/skills.py` + Settings UI — DONE 2026-09-13
Hermes `/learn` + `skill_manage` + Workshop review combined. Agent/user
proposes â†’ SkillScan gate (existing `review_skill_package_tool.py`) â†’
admin approve/reject â†’ `skills/custom/<name>/` (`SKILL.md` +
`references/|templates/|scripts/|assets/`). SQL via existing engine +
alembic; RBAC reuses admin precedent. Frontend `core/skills` API + review
cards (lazy-dialog precedent). Tests: 403s, scan-blocked path, layout exact,
tombstones. Accept: proposeâ†’scanâ†’approveâ†’installâ†’use mocked E2E. Effort: M.

### H5. Programmatic tool-calling â†’ sandbox `execute_python` tool (NEW)
Hermes `code_execution_tool.py`: one program, RPC stubs, single-turn
multi-step. Alpha: new builtin running inside the thread sandbox (acquire
+ command-scope lease precedents); stdio-only returns; read-only+compute
subset first behind `tools.groups`; blocklist mirrors delegation thinking
(no nested `task`, no memory writes, no network unless sandbox allows).
Tests: stub-surface audit, timeout kill, output cap, escape review in
`sandbox/security.py`. Effort: M/L. Risk: mandatory security review.

### H6. Provider profiles â€” DONE (with O1)
`ProviderConfig`, `models[].provider` inheritance, factory merge. Follow-up
(not this track): per-effective-model cost attribution.

### H7. Cron delivery ledger â†’ scheduler + channels
Keep our lease-fenced scheduler; import Hermes gaps: per-occurrence output
artifact capture, delivery ledger (exactly-once), platform routing via
`app/channels` send path. Extend `/workspace/scheduled-tasks` with run
detail. Tests: ledger idempotency, crash-replay delivers once. Effort: M.

### H8. Trajectory export â†’ `scripts/benchmark/` then endpoint
Hermes batch/JSONL/XML + 16k compressor (protected head/tail). Exporter
`run_events` â†’ versioned JSONL; compressor reuses summarization model paths.
Tests: schema round-trip. Effort: S/M. (Full eval plane = parent F1.)

### H9. Unified channel command catalog â†’ `app/channels/commands.py` (NEW)
Hermes 114 `CommandDef`s shared by CLI/Telegram/Slack. Alpha channels
implement per-platform handling; extract shared catalog (`help`, `status`,
`new`, `skills`, `usage`) every adapter serves. Tests: catalog parity per
adapter. Effort: M.

### H10. Delegation spill + depth guard â†’ executor + middleware
Hermes `max_summary_chars` spill-to-disk + `max_spawn_depth` + default-deny
child approvals. Alpha: bounded ordinary-task results with spill file +
read-back handle (batches already bound theirs); `subagents.max_depth`
enforced beside `max_total_per_run` (`subagent_limit_middleware.py`);
child approval default-deny list (extend executor blocklist). Tests: spill
round-trip, depth rejection message, approval prompt. Effort: M.

### H11. Skill trust tiers + quarantine â†’ skill install path
Hermes `builtin|trusted|community` + hub scanning. Alpha HAS SkillScan;
add tier labels to skill metadata + quarantine dir for fresh installs
pending first review (ties H4 queue). Tests: tier gating, quarantine escape
none. Effort: S/M.

### H12. ACP server mode â†’ `app/acp_server/` (NEW, serves IDEs)
We HAVE `invoke_acp_agent_tool` (consume) + `acp_agents` config. Reverse it:
serve Alpha runs over ACP stdio/WS so VS Code/Zed/JetBrains drive us
(Hermes `acp_adapter/` mirror-image). Sessions map to threads; approvals map
to D1 cards. Tests: protocol conformance against ACP fixtures. Effort: L.
High strategic value (every IDE becomes our frontend).

## 2. OMO TRACK (O1â€“O12)

### O1. Categories + chains â€” CHAINS DONE; categories DONE 2026-09-13
Remaining: intent presets `{models[] chain, skills, prompt_append,
tools, requiresModel}` in `subagents/config.py`; `task_tool.py` `category`
schema (default `general` = today); child `task` denial (loop guard);
lead `prompt.py` category table + 3 pinned suites. Reuses DONE chain
machinery for `models[]`. Tests: resolution, gates, guard, snapshots.
Effort: M.

### O2. `/plan` interview mode â†’ composer + roster + artifacts
Metis gap â†’ Momus review (â‰¤3 rounds, read-only via `disallowed_tools`) â†’
plan artifact â†’ approval â†’ execution pins plan hash. Frontend
`builtinSlashCommands` += `/plan`; roster adds `subagents/builtins/
planner_agent.py`, `reviewer_agent.py`; reviewer input = artifact (keeps
context isolation). Tests: round cap, read-only enforcement, mocked E2E.
Effort: L. Depends: O1-categories, H4 persistence patterns.

### O3. Idle continuation + goal chaining â†’ todo middleware + `runtime/goal.py`
Port OMO guards verbatim: cooldowns, exponential backoff, stagnation break,
turn-boundary stop. `todo_middleware.py` emits bounded follow-ups; goal
chaining reuses idempotent run admission (parent C4). Frozen-clock tests.
Effort: M.

### O4. Team mailbox mode â†’ batches extension (PHASE 2)
`teams` + `team_messages` tables, `team_send/team_poll` worker tools, lead
orchestration, batch lease-fencing reuse. Needs O1â€“O3 stable first. Tests:
ordering, acks, caps. Effort: XL.

### O5. Hash-anchored edits â†’ sandbox tools + read path â€” DONE 2026-09-13
`read_file?hashline=true` emits `LINE#ID`; `str_replace`/`write_file`
accept `anchor_hash`, reject stale with re-read hint; reuse
`read_before_write_middleware.py` per-(scope,path) serialization; default
off (`tools.hashline.enabled`) until prompts updated. Race test mandatory
(two same-turn writes â†’ exactly one wins). Effort: M.

### O6. Hook tiers â†’ extensions contract (recovery hooks first)
Port OMO recovery subset as middlewares NOW (`llm/tool_error_handling`
hosts retry policies); full Session/ToolGuard/Transform/Continuation/Skill
tiers extend `extensions/` contribution contract (parent C6) in Phase 2.
Hook ordering + disabled-allowlist + failure-isolation tests. Effort: M now,
L later.

### O7. Wake injection â†’ executor poll path
Stability-gated completion (N unchanged polls) + `task_wake` custom event
(precedent: `task_running` snapshots); RunJournal loop-boundary rules apply
verbatim. Tests: ordering, no double accounting. Effort: M.

### O8. DAG event ledger â†’ contract extension (runner in Phase 2)
New `dag` category in `contracts/run_event_stream_contract.json` +
producers + `RUN_EVENT_STREAM.md` + conformance test (all four together per
gateway contract rules). Runner later. Effort: M now / XL later.

### O9. Cartographer skill + rules settings â†’ skills + settings UI
`skills/public/project-cartographer/` (read-only AGENTS.md drafts â†’
human approval via H4) + conditional-rules store in `core/settings`.
Effort: S/M. Good first-UI slice.
Status (2026-09-13): landed — `skills/public/project-cartographer/SKILL.md` + `test_public_skill_cartographer.py`, `core/rules.ts` + rules settings page wired into settings dialog, 8/8 rstest green.

### O10. Keyword intent routing â†’ composer slash router
Deterministic keywordâ†’mode hints (`ulw `â†’`/plan`) in `builtinSlashCommands`
matching; zero-LLM-cost. Effort: S.
Status (2026-09-13): landed — `KEYWORD_COMMAND_ALIASES` in `input-box-helpers.ts` (exact-match only, no slash-contract change) + 4/4 rstest green.

### O11. Review guards â†’ review pipeline (no new binary)
`read_before_write` already covers write-guards; add comment-density +
role-scoped write policies (planner writes `.md` only) to skill/code review
prompts; ruff keeps real lint. Effort: S.

### O12. LSP + AST-grep MCP pack â†’ opt-in coding MCPs
`lsp` (rename/definition/references/diagnostics) + `ast-grep` servers via
`extensions_config.json`, auto-provision note in setup. Tests: server
lifecycle, tool shape. Effort: M. Biggest coding-agent ROI per line.

## 3. Deliberately NOT ported (with reason)

- **tmux visual panes** â†’ adapted: our subtask cards + run events already
  give live visibility server-side; tmux is terminal-local. No action.
- **Telemetry (PostHog)** â†’ skipped: conflicts with local-first privacy
  posture; console stats cover product needs.
- **TUI parity** â†’ deferred: textual TUI + desktop app suffice; revisit post
  O2/O3.
- **Always-on auto-approve / YOLO presets** â†’ rejected: fail-closed stays.
- **Voice wake-word + TTS voices** â†’ deferred to media phase (STT memo +
  TTS reply ride H-voice item in parent plan).

## 4. Build waves (dependency-gated; each wave shippable + demoable)

- **W1** (done): O1-chains + H6-profiles. âœ… shipped, 34 tests.
- **W2** (next, ~2 wks): O1-categories â†’ O5 â†’ O10. Demo: intent-routed,
  race-safe coding delegation.
- **W3** (~2 wks): H2 â†’ H4 â†’ O9. Demo: remembers everything, proposes own
  skills, maps any repo.
- **W4** (~2 wks): H1 (OFF) â†’ H3 â†’ O11. Demo: learns nightly, knows the user.
- **W5** (~3 wks): O2 â†’ O3 â†’ H7. Demo: interviewed plans that finish
  unattended with ledgered delivery.
- **W6** (~3 wks): H5 (reviewed) â†’ O7 â†’ O12 â†’ H9. Demo: one-shot programs,
  woken parents, IDE-grade refactors.
- **W7** (Phase 2): H10 â†’ H11 â†’ O8-ledger â†’ H12 â†’ D-council. Demo: IDEs drive
  us; skills quarantined; evidence-backed done.
- **W8** (Phase 2): O4 team â†’ O6-full â†’ H8 trajectories â†’ curator B4.

## 5. Cross-cutting gates per wave

`make test` + `test-blocking-io` + `ruff` + `pnpm check`; contract files in
fours; `config_version` bump when schema changes (now 43); nearest
`AGENTS.md` updated; owner-isolation tests extended for every new store;
perf: no new N+1 on hot paths (`get_*_config` O(1) precedent).
