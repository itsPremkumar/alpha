# Alpha — Master Task List

> Living document, maintained by the main agent and updated as work lands.
> Nothing is marked done without evidence (commit SHA, test log tail, or
> subagent session report). New implementation plans are appended here as
> they are created.

**Last updated:** 2026-09-23 — session `ses_f364c593bffemBW1Ja8InOsVw9`
**Legend:** `[x]` done+verified · `[~]` in progress (owner named) · `[ ]` pending/blocked (blocker named)

---

## 1. Committed & pushed this wave (centralized review → staged commits)

- [x] `e22fb85` — fix(subagents): stage-4c honest delegation contracts (no fabricated offline success; 68 targeted tests)
- [x] `94c4e61` — feat(evolution): repository-awareness phase 1 (manifest loader, identity.json agentId, GitHub release check state machine, JSONL ledger, `{project_identity_section}` in lead prompt, +3 evolution routes, SettingsSection Workspace Profile card; 46 tests)
- [x] `b7e8f3e` — fix(governance): disclosed heuristic confidences (votes carry confidence_method/note, unverified→0.5 neutral, tool stops fabricating test evidence, frontend seeds removed; 22 tests)
- [x] `cac8d0d` — feat(models): keyless free-LLM router (tri-state health, cooldown failover, ChatFreeLLM; 26/26 + regression quartet 160 green)
- [x] `b121ff9` — feat(skills): evolution engine with honest evaluation and gated promotion (15/15 + central re-run)
- [x] `4fa9a79` — feat(skills): scored lexical retrieval + skill relationship graph (21/21 + regressions 47+64+4)
- [x] `f96dad6` — feat(budgets): hierarchical scoped token budgets + HITL workflow gates (35/35 central re-run)
- [x] `ecc4f1a` — feat(experience): FACT/TIP experience bank with evidence gate and hygiene (15/15 central re-run)
- [x] pushed to `origin/main`: `f1c7670..ecc4f1a` (8 commits, in sync); matrix entries **#15–#22** + this file land in the following docs commit and are pushed immediately after.

## 2. In flight right now (parallel)

- [~] **pre-memory secret filter** subagent `ses_f32032dd3ffeCtWF3sNzZcAvcD` — `alpha/security/memory_redaction.py` + `agents/memory/manager.py` edits visible, unreported; note: `test_no_orphan_modules` currently fails on `memory_redaction` until it is wired (belongs to this agent)
- [~] **Voice & multimodal feature** subagent `ses_f31c2a451ffemUDOb17zjh4oPI` — implementing `references/ALPHA_VOICE_MULTIMODAL_PLAN.md` (P1–P6; see §6)
- [ ] **Queue #5 full backend suite — restart fresh** only after the two agents above land + their commits; previous run cancelled by server restart at ~19% (`logs/pytest_full_stage4.log`); expect known F/E clusters 19–24%; then triage → recovery suite → env/prod checks
- [ ] WorkSwarm gap 7 (unified execution-mode switch) — **unassigned**; full design ready in `references/ALPHA_WORKSWARM_GAPS_IMPLEMENTATION_PLAN.md` §gap 7 (`runtime/execution_mode.py`, additive `/api/plan-mode/mode`, `/mode` command row)

## 3. Free-LLM router phase checklist (all code items closed)

- [x] package + endpoint + config (see §1 `cac8d0d`)
- [x] targeted pytest **26/26** + ruff 0 + `ast_dup_gate_dups=0`
- [x] regression quartet **160** green (`test_model_factory`, `test_model_fallbacks`, `test_models_authorization`, `test_feature_manifest_wiring`)
- [ ] **live smoke (after suite completes)** — restart gateway, `GET /api/models/free/catalog` honest view, one real routed chat, observe union-alpha→alpha-free chain failover

## 4. WorkSwarm gap implementation (inventory: 15 EXISTS / 7 PARTIAL / 0 MISSING)

| # | Gap (PARTIAL) | Commit / Owner | Status |
|---|---|---|---|
| 1 | Skill evolution engine | `b121ff9` | [x] done + centrally re-verified |
| 2 | Skill retrieval scoring + relationship graph | `4fa9a79` | [x] done + centrally re-verified |
| 3 | Hierarchical scoped budgets | `f96dad6` | [x] done + centrally re-verified |
| 4 | HITL human-gated workflow steps | `f96dad6` | [x] done (wired to `alpha/projects/approval_queue.py`, not `orchestrator/approvals.py` — see plan row 4b) |
| 5 | FACT/TIP experience bank + hygiene | `ecc4f1a` | [x] done + centrally re-verified |
| 6 | Pre-memory-write secret filter | sub `ses_f32032dd3ffeCtWF3sNzZcAvcD` | [~] running (orphan-module failure pending its wiring) |
| 7 | Unified execution mode switch (plan/code) | **unassigned** | [ ] design ready, spawn when §2 clears |

## 5. RSI features ("implement all possible RSI features")

- [x] `references/RSI_AGENT_ARCHITECTURE.md` present (80,717 B, user-supplied, read-only)
- [x] `references/ALPHA_RSI_IMPLEMENTATION_PLAN.md` — reality-mapped plan complete (27-feature inventory: 11 EXISTS / 13 PARTIAL / 3 MISSING; WP-A1…D3 packages; phases A→B→C; 8 spec↔code mismatches disclosed)
- [ ] main-agent review of RSI plan against real code (verify inventory claims, not assume) — **queued after §2 clears**
- [ ] implement RSI phases per plan with targeted tests + gates, disjoint subagents
- [ ] matrix entry + commit per phase

## 6. Voice & multimodal (user request: mic, speaker, wake word — completely free with provider→keyless→local fallback)

Research done this session (web): openWakeWord (server-side wake word), edge-tts (keyless network TTS) → Piper/Kokoro (offline neural TTS), faster-whisper already integrated at `alpha/media/stt.py` (local STT — reused, not duplicated), RapidOCR (lightest modern OCR) → Tesseract (system binary), AI Horde anonymous (keyless image gen, already proven in free_router), Pollinations **now requires an API key** → excluded from keyless tier, AI Horde has **no** STT/TTS endpoints → that tier honestly `skipped_no_provider`, EasyOCR/PaddleOCR excluded as not-lightweight.

- [x] plan written: `references/ALPHA_VOICE_MULTIMODAL_PLAN.md` (research + reality-mapped: T1 configured `model.capabilities` → T2 keyless → T3 local; single seam; honest `MultimodalUnavailableError` attempts; WS voice stream on browser.py auth pattern; authorized exceptions: one new router + manifest regen with tools=119 byte-identical gate)
- [~] implementation subagent `ses_f31c2a451ffemUDOb17zjh4oPI` (phases P1–P6, gates: chain/router/wakeword tests + wiring/no-orphan/cold-imports + ruff + dup gate 0 + `tsc --noEmit` + voice.test.mjs)
- [ ] main-agent central review of its diff + gates + staged commit
- [ ] main agent adds `voice:` block to `config.example.yaml` + live `config.yaml` (subagent is forbidden from editing either)
- [ ] live HTTP smoke after suite/gateway restart: `GET /api/multimodal/capabilities`, one TTS + one STT + one OCR + one WS wake session, UI mic→transcript→speaker loop
- [ ] matrix entry + commit

## 7. Documentation deliverables

- [x] `docs/TASK_LIST.md` — this file (update on every wave)
- [x] plan docs (kept uncommitted in `references/`, per standing rule): `ALPHA_WORKSWARM_ONLY_INTEGRATION_PLAN.md`, `ALPHA_SELF_EVOLVING_GITHUB_ARCHITECTURE.md`, `RSI_AGENT_ARCHITECTURE.md` (user-supplied), `ALPHA_RSI_IMPLEMENTATION_PLAN.md` (80,718 B), `ALPHA_WORKSWARM_GAPS_IMPLEMENTATION_PLAN.md` (63,006 B — 7 gaps reality-mapped, gap 7 design included), `ALPHA_VOICE_MULTIMODAL_PLAN.md`
- [x] `docs/IMPLEMENTATION_MATRIX.md` session fix log **#15–#22** written with real SHAs (same docs commit as this file)
- [ ] final honest report (second full audit per 87-section spec)

## 8. Verification backlog (gated on suite completion)

- [ ] queue #5 fresh full-suite run + triage F/E (known clusters 19–24%); record evidence in matrix
- [ ] recovery suite `scripts\verify_recovery.ps1` must end 30/30 (blocked until suite done)
- [ ] env/prod checks re-run (`secrets scan`, env audit, dup gate repo-wide)
- [ ] frontend `tsc --noEmit` + node tests after voice wave lands
- [ ] free-LLM live smoke (§3) + voice live smoke (§6) in one gateway-restart window
- [ ] **user:** reboot verification — `powershell -ExecutionPolicy Bypass -File scripts\verify_reboot.ps1`
- [ ] **user:** tray icon state behind `^` chevron visual confirmation

## 9. Known-issues queue (root-cause fixes only, never disable tests)

- [ ] `backend/app/gateway/routers/enterprise.py:52` — hardcoded `epistemic_confidence 0.85`
- [ ] `backend/packages/harness/alpha/enterprise/models.py:138` — fabricated default
- [ ] `backend/packages/harness/alpha/enterprise/rfc.py:45,52,59,100,107` — fabricated RFC defaults
- [ ] `backend/app/gateway/routers/memory.py:562` — fabricated confidence default
- [ ] `GateRequest.autonomous_mode=True` default (flagged in RSI plan review) — audit callers, make the autonomous path explicit + disclosed
- [ ] `test_no_orphan_modules` failure on `alpha.security.memory_redaction` — resolves when subagent `ses_f32032dd3ffeCtWF3sNzZcAvcD` lands its wiring (verify, don't suppress)

## 10. Standing rules (apply to every item above)

- Centralized commits only (main agent), Conventional Commits subject, no-BOM `git commit -F`; commit-msg hook enforced.
- No auth changes · no hardcoded secrets · no disabling/weakening/skipping tests · root-cause fixes only · honest failed statuses over fabricated success.
- Background full suite owns `logs/pytest_full_stage4.log` — no parallel full-suite runs, no server restarts until it completes.
- Test runs isolate `AGENT_WORKSPACE_HOME` to a tmp dir (test env does NOT isolate it globally).
- grep/glob tools broken in this environment → `git grep` / `Select-String`; quote paths with spaces (`PREM KUMAR`).
- Subagents: disjoint file ownership, no git mutations, targeted tests only, no new builtin tools, no manifest hand-edits (official regen only), `ast_dup_gate_dups=0` + ruff clean before reporting done.
- `references/*.md` (user-supplied and subagent-drafted plans) stay uncommitted; only `docs/**` rides documentation commits.
