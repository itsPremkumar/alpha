# Chat shell UX: bot, project, conversation navigation

Agent: `agent/chatux` (subagent, worktree `../alpha-chatux`).
Spec: the user-supplied hierarchy — *Alpha → Bot → {Standalone Conversations, Projects → {Conversations, Files / Knowledge / Tasks / Settings}}*.

This file is written incrementally. Sections marked *(pending)* are filled in as
the work lands.

---

## 0. Two rules this work was held to

**RULE 1 — additive only.** `ThreadSidebar` was extended, not replaced. No
existing affordance, prop, or render path was removed. The only edit outside my
own new files is two *insertions* in `ChatView.tsx`, because two elements of the
spec (the project context header and the new-conversation landing state) live in
`<main>`, which no file I own renders. Both insertions are itemised in §6.

**RULE 2 — no invented number, dot, or status.** Every count, presence dot,
relative time and status chip in the new UI is derived in
`frontend/src/lib/chat-shell.ts` from a named API field, and every one of them
has a test for the *absent* case. The table in §4 is the authority.

---

## 1. Baseline measurements taken before any edit

Recorded on the clean worktree at `agent/chatux` = `origin/main`, with
`frontend/node_modules` junctioned read-only to the main tree's copy.

| Check | Command | Result |
| --- | --- | --- |
| Type check | `node node_modules/typescript/bin/tsc --noEmit` | **0 errors** (after creating the gitignored `next-env.d.ts`, which `next typegen` normally writes — see §8) |
| Test suite | `node --test src/lib/*.test.mjs` | **839 tests, 831 pass, 8 fail** |
| The 8 failures | all in `src/lib/system-probe-honesty.test.mjs` | `expected a "voice" probe, got gateway, agentsApi, browser, database, memory, skills, scheduled, channels, mcp, watchdog, company` — a voice-probe expectation that fails on unmodified `origin/main`, before any edit of mine |

No stack was running during this task (TCP 2026 / 3000 / 8001 all refused), so
**no live API payload could be captured**. Field shapes were therefore verified
by reading the Gateway router source read-only; each route is cited with file and
line in §4.

*(pending: §2 what existed / what I added, §3 the hierarchy, §4 the claim table,
§5 blocked elements, §6 the ChatView insertions, §7 tests and bite proofs,
§8 what I could not verify.)*
