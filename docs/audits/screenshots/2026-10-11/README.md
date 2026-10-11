# Messages view — seeded data walkthrough

Four screenshots, taken 2026-10-11 against a running Gateway. They exist to
answer "what does this view look like when it actually has something to show",
which the repository could not answer before: the room store held 211 messages
across 6 rooms, but they were lopsided and featureless.

## What the store actually held, measured before anything was seeded

    store: backend/.alpha/groups/rooms.json   (version 3, 6 rooms)

    sprint-room    40 msgs   discussion, proposal, question
    test-board     80 msgs   card_update
    project-alpha  81 msgs   card_update
    project-…      7 msgs    blocker, discussion, handoff
    company-co-…   3 msgs    card_update
    alpha-team      0 msgs   —

  features: reactions=n threads=n forwards=n pinned=0   ← in every room

So the honest situation was: real messages existed, but three of six rooms were
a single `card_update` intent repeated 80 times, one room was empty, and the
transcript's threading, reaction, forward and pin machinery had **never once
been exercised** by real data. That is why the view looked thin — it had
nothing to render.

## What was added, and how

Everything was written through the real Gateway REST API
(`http://127.0.0.1:8001/api/groups/...`) as ordinary HTTP requests, so the
rows created are genuine records in the real store. No mock, no fixture, no
hardcoded UI data — a reload reads them back through the same routes the
frontend calls. Existing rooms and their 211 original messages were left
untouched.

- **7 rooms, nested.** `alpha-sprint` and `research-lab` as roots; three
  children under the first, one under the second; `api-grind` as a grandchild
  under `backend-core`. Nesting is real `parents`/`authority_parent` scope
  data, set through `POST /{name}/move` (which requires both in one request —
  the authority parent must also be a visibility parent).
- **29 new messages** across those rooms, spread over the intent vocabulary
  `alpha.groups.room.MessageIntent` actually declares: `status`, `proposal`,
  `question`, `answer`, `decision`, `blocker`, `warning`, `approval_request`,
  `escalation`, `task_assignment`, `task_completion`, `handoff`.
- **Threads** — three replies carrying `reply_to`, so the renderer's quote
  strip and thread indentation are exercised on real rows.
- **Reactions** — six `POST /{room}/messages/{id}/reactions` calls.
- **Forwards** — two cross-room forwards, which stamp `forwarded_from` so the
  provenance line renders.
- **Pins** — three, surfaced in the details pane.

Resulting store, measured back:

    alpha-sprint    25 msgs  8 intents  reactions=Y forwards=Y
    research-lab     6 msgs  2 intents
      backend-core  21 msgs  8 intents  reactions=Y threads=Y forwards=Y
      frontend-core 17 msgs  6 intents  threads=Y
      qa-gate       11 msgs  4 intents  reactions=Y threads=Y
        api-grind    9 msgs  3 intents  reactions=Y
      analysis-cell  6 msgs  2 intents

## The screenshots

**messages-01-BEFORE-filter-to-zero.png** — the honesty defect, reproduced
before the fix. The search box reads `zzzznomatch` over six real rooms, and the
screen carries both `Showing 0 of 6 — 6 hidden by this filter or search` *and*
`No chats yet` at once.

**messages-02-AFTER-filter-honest-state.png** — the same filter after the fix.
`Nothing matches this filter`, `6 conversations are hidden — this is the filter
and search, not an empty inbox`, and the clear control.

**messages-03-seeded-forest-and-nesting.png** — the group forest drawn from the
seeded nesting. The sidebar shows `alpha-sprint` with `api-grind`,
`frontend-core`, `qa-gate` and `alpha-team` indented under it, each with its
own member count. This is also the screenshot that proves the bounded-forest
fix: the forest scrolls inside its own region rather than growing the sidebar
header without limit.

**messages-04-composer-and-honest-error.png** — the composer (`discussion`
intent selector, `Message the group…`) beside the honest read failure. The
Gateway had just died, and the sidebar correctly reports `Group list
unavailable — failed to load, not empty` rather than presenting an inbox with
nothing in it.

## What is NOT verified here

The transcript screenshot. The Gateway on this host dies repeatedly under the
memory pressure of several concurrent processes — it reaches
`Application startup complete` and then exits with a native crash code, and
`/api/groups/tree` alone takes ~76s against the frontend's 60s timeout. The
transcript *was* verified by reading the live DOM once the room read completed
(21 bubbles, thread quote strip, reaction pills, `forwarded from` provenance,
`TASK ASSIGNMENT` and `HANDOFF` intent badges, all present), but no screenshot
of it survived — every attempt lost the Gateway between the read and the
capture. Claim it as DOM-verified, not as verified by eye.

To re-run the seed script it lives outside the repository in the session temp
directory; it is idempotent in the sense that it creates new messages and
nests existing rooms, and it makes no destructive call.
