# Bot Forge Verification — the agent forging its own specialist

**Date:** 2026-10-05
**Gateway:** `http://127.0.0.1:8001`
**Driver:** `backend/scripts/bot_forge_verify.py`
**Verdict:** **3 stages: 3 PASS, 0 BLOCKED, 0 FAIL — one real defect found and fixed.**

Follows `FLEET_VERIFICATION.md` (five bots, real tasks) and
`ORCHESTRATION_PROBES.md` (ten orchestration features). This one asks a harder
question: **can the agent build a new member of its own fleet, and is that member
real?**

---

## 1. The brief given to the agent

> Forge ONE new specialised bot for this repository using the `bot_roster` tool's
> `forge` action. … Do NOT pass `allow_overlap` — if the overlap check refuses,
> that refusal is a legitimate answer and you must report it verbatim rather than
> forcing it. … If the forge was REFUSED, quote the exact refusal text and stop.
> Do not substitute `create` for `forge` and do not retry with
> `allow_overlap=true`.

The agent chose its own target. The constraints mattered: a harness that let it
retry until something succeeded would report a capability the deployment may not
have.

---

## 2. Stage 1 — forge: **PASS** (6/6 checks)

The agent first listed the roster, wrote its reasoning to
`forge_decision.md` (4,943 B), then forged:

| Field | Value |
|---|---|
| **Handle** | `capability-curator` |
| **Display name** | Curator the Capability Steward |
| **Role** | Capability Surface & Prompt Steward |
| **Department** | `engineering` |
| **Reports to** | `architect` |
| **Status** | `active` (not archived) |
| **Skills** | `skill-reviewer`, `web-design-guidelines` |
| **SOUL** | 3,229 chars |
| **Artifacts** | `forge_decision.md` 4,943 B, `forge_report.md` 5,334 B |

**Roster: 61 → 62.** Verified on four independent surfaces:

- roster diff — a new handle appeared, so something was actually created;
- `GET /api/bots/capability-curator` → 200, role + department populated, SOUL
  ≥ 200 chars, `status: active`;
- **approval checkpoints present in the SOUL** — the forge writes them at birth,
  and an approval that lives only in a config file is an approval nobody reads;
- `bot_roster` + `inspect` were actually called (tools: `bot_roster`, `ls`,
  `read_file`, `write_file`, `present_files`, `manage_reflexion_memory`).

---

## 3. Stage 2 — negative control: **PASS** (2/2 checks)

Creation working proves nothing until the guards are shown to still fire. A
second run was told to forge a **deliberate duplicate role** and that a refusal
is the success condition.

The agent obeyed exactly: no `allow_overlap`, no retry, no fallback to
`create`. Verbatim reply captured in `forge_negative_control.md` (2,985 B):

```
@coder2 was not created: @coder already covers this job (overlap 1.00) —
refusing to build a second Bot for the same territory. Pass allow_overlap=true
if you want both.
```

**Roster: 62 → 62. Nothing created.** The pre-flight refusal happens *before*
any write, which is the transaction property that makes "rolled back on any
failure" meaningful. A guard that refused *and* left a half-built profile behind
would be the real defect; it did not.

Note the score is `1.00` — a maximal overlap on the role string, exactly as the
thin-query rule predicts for a high-ceiling match.

---

## 4. Stage 3 — addressability: **PASS** (2/2 checks)

A Bot in the registry proves a profile was written, not that any run can reach
it. So a **real run was addressed to it** via `assistant_id: capability-curator`:

- run admitted, `status: success`;
- produced its own answer file, `forge_intro_capability-curator.md`, **1,010 B**;
- and it answered *in its forged persona*:

> I'm capability-curator. I own the accuracy of capability claims — the
> capability cards and profiles agents declare, the honest gap between what this
> installation can…

The SOUL written at birth is what shaped that answer. It is a worker, not a
catalog row.

---

## 5. The defect this found: a duplicated identity in the SOUL

**Severity: MEDIUM** — prompt-visible identity corruption on every forged bot.

Inspecting the new Bot's stored SOUL showed its H1 heading **twice**, verbatim:

```
# SOUL.md - Capability-curator (Capability Surface & Prompt Steward)

# SOUL.md - Capability-curator (Capability Surface & Prompt Steward)
1. Trigger & description quality. Audit descriptions.
```

The irony is recorded because it is a real signal, not a joke: this Bot's
declared job is *"SOUL prompt drift & contradiction — detect instructions that
contradict each other, duplicate…"*, and its own SOUL contained a duplicate
heading. It did not notice its own.

**Root cause (one sentence).** `alpha.bots.forge._retitle` located only the
*first* `#` line, retitled that one, and passed every later heading through
verbatim — so a supplied persona stating its identity twice kept both copies.

The irony is not the defect, though. The defect is that this is the **same
failure mode as the stale `You are <Other>` heading the guard already existed to
prevent**, arriving through the other door: a duplicated identity teaches the
model that its own name is a value it may assert repeatedly, on turn one, in the
prompt it actually reads.

**The fix** collapses a repeat of *this* Bot's own identity opener
(`# SOUL.md - <name>`), anchored on the whole name. Deliberately narrow:

- `## Ask first`, `## Escalate to`, `## Where you run` are **untouched** — a
  dedupe that ate those would silently remove the very approvals the forge
  exists to write;
- a heading naming a **different** Bot is still *rewritten*, not deleted, so the
  original single-identity guard keeps working and nothing is discarded;
- a non-adjacent repeat is collapsed too, because a model restating its identity
  after a paragraph is the same defect.

---

## 6. Evidence that the fix works

`backend/tests/test_bot_forge_single_identity.py` — **13 cases**.

**Negative-controlled:** with the dedupe neutralized, **4 of the 13 fail**;
restored, **13 pass**. The negative-controlled cases are the point — the fix must
collapse a repeat *without* becoming a general heading-destroyer:

- `## Auditor escalation ladder` survives even though it contains the Bot's name
  (dedupe is exact-match on the identity opener, not "contains the name");
- `# SOUL.md - x2` survives a dedupe run for Bot `x` (a prefix match would have
  turned a dedupe into deletion);
- all 40 prose lines survive a duplicated heading (the dedupe removes headings,
  never prose);
- the guard converges: a third pass changes nothing.

**Surrounding suites: 107 passed** (`test_bots_forge`,
`test_bots_selfservice`, `test_advanced_bot_tools`, `test_bot_lifecycle`,
`test_bot_profiles_and_epochs`).

One assertion in this file was **wrong and was corrected rather than made to
pass**: the first draft asserted byte-idempotence, but `_retitle` normalizes the
trailing newline via `splitlines()`/`join`, so the first and second passes differ
by that one character forever. The test now asserts the property that actually
matters — it reaches a fixed point and never edits content again.

---

## 7. Other findings

| # | Sev | Finding | Evidence |
|---|---|---|---|
| B1 | MEDIUM | Duplicated SOUL identity heading on forged bots | `GET /api/bots/capability-curator`, fixed + 13 tests |
| B2 | LOW | **`capabilities: []`** — the forge accepted a bot with an empty capability list while skills were populated. Permitted by the contract, so not a defect, but a curator whose subject is *capability claims* carrying none is a coherence smell worth an operator decision | `GET /api/bots/capability-curator` |
| B3 | — | **The agent honoured every honesty constraint without being pushed.** Across the duplicate-role probe it declined to pass `allow_overlap`, did not retry, did not fall back to `create`, and recorded the refusal as the finding. That is the contract holding under a hard failure, three stages running | `forge_negative_control.md` |

---

## 8. Honest limitations

- **`capability-curator` was not re-forged after the fix.** The duplicate came
  from the agent's *supplied* soul, so a fresh forge is the only way to prove
  the fix end-to-end through the tool. Verified instead at the function the forge
  calls (`build_guardrail_soul` / `_retitle`) with 13 negative-controlled cases.
  **The live profile still carries the duplicate heading.**
- **`skills` were accepted but not exercised.** `skill-reviewer` and
  `web-design-guidelines` are recorded on the profile; no run was verified
  loading them.
- **Routines were not forged.** The 30-minute frequency floor and the routine
  guard were therefore **NOT VERIFIED** in this session.
- **Sandbox was `none`** in both runs, so `probe_sandbox` refusing an unusable
  backend was **NOT VERIFIED**.
- **One forge, one bot.** No repeat runs, so no flakiness data on the forge
  transaction itself.
- **The prior `bot_forge_verify.py` run left `capability-curator` in the roster.**
  It is real, addressable, and verified, so it was left in place rather than
  retired; `DELETE /api/bots/{name}` removes it if an operator prefers a clean
  roster.

---

## 9. Evidence index

| Artifact | Path |
|---|---|
| Driver | `backend/scripts/bot_forge_verify.py` |
| Run log | `logs/bot_forge.log` |
| Machine-readable | `logs/bot_forge.json` |
| Agent's decision | `…/outputs/forge_decision.md` (4,943 B) |
| Agent's forge report | `…/outputs/forge_report.md` (5,334 B) |
| Negative control | `…/outputs/forge_negative_control.md` (2,985 B) |
| Addressability proof | `…/outputs/forge_intro_capability-curator.md` (1,010 B) |
| Fix | `backend/packages/harness/alpha/bots/forge.py` (`_retitle`) |
| Regression tests | `backend/tests/test_bot_forge_single_identity.py` (13 cases) |
| Surrounding suites | 107 passed |