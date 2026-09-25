# Frontend agent guide (`frontend/`)

Scope: this file covers everything under `frontend/`. Deeper, subsystem-specific
rules live in `frontend/src/AGENTS.md` and win where they are stricter.

## What this app is

Alpha's operator UI: a Next.js App Router frontend that talks to the FastAPI
gateway under `/api`. It is a **client of measured gateway state** — it never
invents backend facts.

## Non-negotiables

1. **No fabricated state.** Every number, status, count, and "healthy/ready"
   claim must come from a real API response. If a field is missing, render it as
   unknown/disabled — never as zero, empty, or a green badge.
2. **Failures surface the server's reason.** The shared client
   (`src/lib/api-client.ts`) carries the gateway's `detail` in `ApiClientError`;
   show it. Do not swallow it into a generic "something went wrong".
3. **Defaults that are off must look off.** An autonomy loop that is disabled by
   configuration renders as *disabled* with the reason, not as idle/healthy.
4. **Silence is not success.** A component that is loading, empty, or
   unavailable says which one it is.

## Client rules

- Clients live in `src/lib/*.ts` and wrap the shared `get`/`send` helpers from
  `src/lib/http.ts`. Do not call `fetch` directly from a component.
- A client maps the server payload and preserves honesty: absent optional fields
  become `null` (not `0`, not `""`, not `false`), and unknown enum values are
  rendered verbatim rather than coerced.
- Reject on non-2xx. A failed call must reject with the server's reason rather
  than resolving to an empty list that looks like "nothing was recorded".

## Testing (required for client changes)

```
cd frontend
node node_modules/typescript\bin\tsc --noEmit     # must be 0 errors
node --test src/lib/*.test.mjs                    # pure Node tests, no server
```

Client tests are plain `node --test` files (`*.test.mjs`) that transpile the TS
module and run it against a stubbed HTTP layer. They pin the real paths/verbs
and the honesty inversions (a degraded read must not look like an empty one).

## Style

- Tailwind utility classes; match the density of neighbouring components
  (`text-[11px]`, `rounded-xl`, `border-border/60`) rather than inventing a new
  scale.
- Icons come from `lucide-react`; reuse the icon vocabulary already imported in
  the file you are editing.
- Keep the existing section-component pattern: a lazy-loaded component in
  `src/components/sections/`, wired through the workspace view registry.
