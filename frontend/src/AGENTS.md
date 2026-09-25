# Frontend source agent guide (`frontend/src/`)

Scope: this file covers `frontend/src/`. It is stricter than `frontend/AGENTS.md`
where the two overlap.

## Layout

- `app/` — App Router routes, layouts, and the page shell.
- `components/` — UI. `ChatView.tsx` is the workspace router: each workspace view
  id maps to a lazy-loaded section component.
- `components/sections/` — one component per surface (Workflows, Supervisor,
  Protocols, Forge, …). A new backend surface gets a section here.
- `components/ui.tsx` — shared primitives (`Section`, `Btn`, `Badge`, `Field`,
  `Notice`, `ErrorBox`, `EmptyState`, `SkeletonList`, `inputCls`).
- `lib/` — API clients, one module per backend plane, plus the shared
  `api-client.ts` / `http.ts` transport.
- `content/` — the documentation site content (en/zh).
- `types/` — shared TypeScript types.

## Lion companion boundary

The local lion companion is isolated under `components/lion-pet/`. Keep the SVG
rig, action registry, settings normalization, lifecycle adapter, bounded local
travel, and feature CSS inside that folder. `ChatView.tsx` may pass only
bounded lifecycle state and an optional chat action; it must not receive prompt
content or thread identifiers.
The compatibility exports at `components/LionPet.tsx` and `lib/lion-pet.ts` are
facades only. Regression coverage lives in `src/lib/lion-pet.test.mjs`.

## Adding a backend surface to the UI (the required path)

1. Add `lib/<surface>.ts`: a typed client over the real routes. Map envelopes
   verbatim, keep `owner_id`-style scoping explicit, clamp/validate query bounds
   to what the server accepts, and reject on error with the server's reason.
2. Add `lib/<surface>.test.mjs` pinning the exact paths/verbs, the envelope
   mapping, and the honesty inversions for that plane.
3. Add `components/sections/<Surface>Section.tsx` using the shared primitives.
4. Wire the view: add the id to the workspace-view union, add a tab entry, and
   add the lazy import plus render case in `ChatView.tsx`. **A section that is
   not reachable from the view registry is dead code** — the id must exist in
   all three places or nowhere.
5. Gate: `tsc --noEmit` = 0 errors and `node --test src/lib/*.test.mjs` green.

### Data Flow

```
browser
  -> Next.js app router (app/) serves the shell
  -> components/ChatView.tsx resolves the active workspace view id
  -> components/sections/<Surface>Section.tsx renders that surface
  -> lib/<surface>.ts calls the gateway through lib/http.ts (get/send)
  -> /api/* on the Gateway (app/gateway/routers/*)
  -> harness subsystems (agents, tools, sandbox, memory, scheduler)
  -> storage (LangGraph checkpoints/store, thread/run metadata, files)
```

The return path matters as much as the outbound one:

- **Mutations** go through the client, then the UI re-reads real state. Never
  paint an optimistic result as if the server had confirmed it.
- **Streaming** (`lib/sse-reducer.ts`, `lib/threads-ext.ts`) consumes the
  gateway's SSE frames: `values`, `messages-tuple` chunks, and `task_*` custom
  events for subagent progress. Reducers must tolerate out-of-order/partial
  frames and must not invent a terminal state for a stream that ended early.
- **Bounded payloads are a contract.** The gateway batches `write_file` /
  `str_replace` argument deltas; the frontend must render the batched frames it
  receives rather than assuming one chunk per token.
- **Authorisation state is server-owned.** The UI reflects what the gateway
  reports (scopes, permissions, disabled capabilities); it never decides access
  locally.

When a frame or field is missing, render the honest unknown state. A partially
received stream is not a successful one.

## Honesty patterns to copy

- A control that is off by default renders as off, with the reason it is off.
- A run/report panel shows the server's real counters and timestamps; a
  measurement that could not be taken is shown as unknown, never as zero.
- Destructive or state-changing actions require an explicit opt-in (a checkbox
  that defaults to unchecked) and state the safety model in the label.
- In-process or single-worker state is labelled as such in the UI.
- Never render an optimistic success for an action the server has not confirmed.

## Client honesty rules

- Map absent optional values to `null`; do not coerce `undefined` to `0`/`""`.
- Preserve server enum strings verbatim; do not invent a value the server did
  not send.
- Do not catch-and-empty a failed request. Surface the reason; an empty list must
  mean "the server said there is nothing", not "the call failed".
- When a request is retried or triggered repeatedly, disable the control while
  in flight so a double-click cannot create two records.
