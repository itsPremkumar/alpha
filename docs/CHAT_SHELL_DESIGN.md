# Chat shell — Bot → Project → Conversation

The design specification for Alpha's conversation surface. A visual reference was
supplied by the user; it is a **look-and-feel reference, not a layout to clone**.
The authority here is the hierarchy below, and the visual language is borrowed
where it earns its place.

**Rule that governs everything below: additive only.** This adds navigation
structure, a bot switcher, a project switcher and detail panels. It does not
replace `ThreadSidebar`, does not restructure the view registry, and does not
remove any existing affordance.

## The hierarchy

```
Alpha
└── Bot
    ├── Standalone Conversations
    └── Projects
        ├── Project A
        │   ├── Conversation 1
        │   ├── Conversation 2
        │   └── Files / Knowledge / Tasks / Settings
        └── Project B
```

Three questions must be answerable **permanently**, not per-click:

1. Which bot am I talking to?
2. Which project am I working on?
3. Which conversation am I in?

If any of the three is ambiguous on screen, the layout is wrong.

## Layout

```
┌──────────────┬──────────────────────────────────┬────────────────────┐
│  Bot rail    │  Context header                  │  Project context   │
│              │  bot · presence · project ▾ · ⋯  │                    │
│  ───────────  ├──────────────────────────────────┤  Project: <name>   │
│  Current     │  Conversation | Files | Tasks |  │  ────────────────  │
│   agent      │  Knowledge                        │  Overview        n │
│   + New      │  ──────────────────────────────── │  Conversations   n │
│  ───────────  │                                   │  Files           n │
│  Projects  + │  [transcript]                     │  Tasks           n │
│   ▸ proj   n │                                   │  Settings         │
│   ▸ proj   n │                                   │  ────────────────  │
│  ───────────  │                                   │  Recent            │
│  Standalone+ │                                   │   · <title>   2h  │
│   ▸ chat    5 │                                   │  ────────────────  │
│              ├──────────────────────────────────┤  Quick actions     │
│  [New Project]│  [composer]                       │  Switch project ▾  │
└──────────────┴──────────────────────────────────┴────────────────────┘
```

Three columns on a wide viewport. **Two columns below ~1280px**, with the project
context reachable as a drawer. One column on mobile, where the bot rail becomes a
sheet. The hierarchy survives every breakpoint; only the arrangement changes.

## Components

| Component | Responsibility | New or extended |
| --- | --- | --- |
| `BotRail` | Lists bots the Gateway reports; selection scopes everything below | new |
| `CurrentAgentHeader` | Selected bot, reported presence, **New Conversation**, five-item menu | new |
| `ProjectsGroup` | Projects for the selected bot, real counts, **New Project**, per-project menu | new |
| `StandaloneGroup` | Conversations with no project, kept distinct | extends `ThreadSidebar` |
| `ContextHeader` | Bot · presence · project switcher · overflow — the always-visible answer to all three questions | new |
| `ProjectContextPanel` | Overview / Conversations / Files / Tasks / Settings, recent list, quick actions, switch project | new |
| `ConversationLanding` | Welcome state naming bot and project in one line, with starter actions | new |

### The bot menu

New Conversation · New Project · this bot's conversations · this bot's projects ·
bot settings.

### The project menu

Project overview · New conversation · existing conversations · Files/knowledge ·
Tasks · Project settings.

## Honesty — the constraint that shapes every number

This repository's first non-negotiable is that **every number, status, count and
"healthy/ready" claim comes from a real API response, and a missing field renders
as unknown — never as zero, empty, or a green badge.**

The reference design shows a presence dot, per-project conversation counts,
`Files 5`, `Tasks 2`, `Status Active`, and relative times. That is eight
independent factual claims. Each one is specified here:

| Element | Source | When absent | When it is a real zero |
| --- | --- | --- | --- |
| Bot presence dot | reported presence | no dot, and the reason stated | not applicable |
| Conversation count | count for that project | "count not reported" | `0` |
| Files / Tasks count | count from the owning route | "not reported" | `0` |
| Project status | status from the project route | "not reported" | not applicable |
| Relative time (`2h ago`) | measured instant | "time not reported" | not applicable |
| Bot list | bots the Gateway returned | the list is empty **and** it is visibly an answer, not a failure | empty list |
| Failed read | — | visibly a failure with the server's reason | not applicable |

Three further rules:

- **A measured `0` must look different from an unknown.** They are the same
  glyph in the reference design and must not be in the real one.
- **A failed read is never an empty list.** "The server said there is nothing"
  and "the call failed" are opposite claims.
- **No panel over a backend feature with no production callers.** An empty
  `Files` panel reads as "this project has no files", which is false — the feature
  simply never runs. Several backend subsystems currently have zero production
  callers. Where that is true, the shell renders and the panel states that its
  source is unavailable, and the gap is reported.

The visual quality of the reference is not a licence to assert anything.

## Borrowed from the reference, and why

- **Three-region layout** — answers all three questions permanently rather than
  per-click. This is the design's best idea and the reason for the layout.
- **Per-agent avatar glyph and tint** — makes the rail scannable without reading
  names. Mapped onto the existing bot registry, not a parallel one.
- **One-line welcome naming bot and project** — the thesis of the hierarchy, and
  cheap to get right.
- **Right-rail counts on section headers** — orientation at a glance.
- **Prominent primary action** — one per context, not several competing.

## Deliberately not copied

- **Hardcoded counts and statuses.** Typography in a mockup, claims in an app.
- **A second accent colour for one button.** Branding is a single lion mark and an
  existing palette; a new accent system for a single CTA fragments it.
- **Three columns as a hard requirement.** The hierarchy is the requirement; the
  arrangement degrades.
- **Restructuring `ChatView`'s view registry.** Out of scope and additive-only.

## Accessibility

- Every icon-only control has an accessible name naming its action and target.
- Menus open on **Enter and Space**, not Enter only, and close on Escape.
- Counts are in the accessible name, not only in a badge: *"Website Redesign, 3
  conversations"*, never a bare `3`.
- The current bot, project and conversation are exposed via `aria-current`, so
  "where am I" is answerable without sight.
- Focus order follows the hierarchy: rail → context header → tabs → transcript →
  composer.
- No colour-only signalling. The presence dot, status tint and unknown marker each
  carry a text or shape equivalent.

## Verification

- `tsc --noEmit` = 0 errors.
- `node --test src/lib/*.test.mjs` green, and **the count must not drop**. If
  tests disappear, something was deleted.
- `text-encoding.test.mjs` explicitly — this surface is punctuation-heavy and a
  UTF-8 round-trip has corrupted this repository before.
- For every number above, a test proving the **unknown** case. The happy path is
  the one that does not matter.
- No screenshots are possible in this environment, so verification is through
  rendered markup and real API payloads. No visual verification is claimed.
