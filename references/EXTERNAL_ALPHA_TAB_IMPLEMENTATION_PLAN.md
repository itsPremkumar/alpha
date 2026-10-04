# External Alpha Tab — Implementation Plan

## 0. What this document is

The Alpha-to-Alpha peer network is already implemented and free
(`docs/ALPHA_PEER_NETWORK.md`). This plan does **not** re-implement it. It
closes the gap between "an Alpha can talk to another Alpha" and "the operator
can read the whole cross-installation conversation in the UI, with every
receipt and every agent-side turn".

Design decisions were taken with the user:

| Decision | Choice |
| --- | --- |
| Tab shape | A **separate `external-alpha` tab**, side by side with `peers` (Alpha Network). Alpha Network stays the pairing/control surface; External Alpha is read-heavy transcript + forensics. |
| Transcript scope | Persist peer turns so the agent's reply is recoverable, and make them readable **only** through the External Alpha tab. Normal chat threads and the existing Runs / Run Inspector surfaces are untouched. |
| Cost | Everything here is stdlib + already-present dependencies. No new package. |

---

## 1. Verified current state

Every claim below was read in the source, not inferred.

### 1.1 What already works

| Concern | Implementation |
| --- | --- |
| Module | `backend/packages/harness/alpha/peer_network/` (11 files) |
| Service | `PeerNetworkService` (`service.py`, 1040 lines) — lifecycle + policy owner |
| Storage | `PeerNetworkStore` (`storage.py`, 671 lines) — synchronous SQLite, `asyncio.to_thread` from async callers |
| Transport | `PeerTransport` (`transport.py`) — HTTP first, WebSocket fallback, `validate_endpoint()` SSRF/metadata blocklist |
| Discovery | `UdpDiscovery` (stdlib) + optional `MdnsDiscovery` (`zeroconf`, free, opt-in extra) |
| Identity | `LocalIdentity` — agent id + 256-bit pairing code, `identity.json`, `chmod 0600` where supported |
| Card | A2A-style bounded envelope at `/.well-known/agent-card.json` (+ 2 aliases) |
| Router | `backend/app/gateway/routers/peer_network.py` — 16 authenticated routes, 6 public routes |
| Model tool | `alpha_peer_network` — `discover`/`list`/`send`/`create`, allowlisted output |
| UI | `peers` view → `PeerNetworkSection.tsx` (470 lines), labelled "Alpha Network" |
| Cost | Stdlib + `httpx` + `websockets` (already required). `zeroconf` optional. No broker, no VPS, no paid API. |

### 1.2 The three blockers

**Blocker 1 — the agent's side of the conversation is discarded.**

`backend/app/gateway/services.py:2129-2133`:

```python
"additional_kwargs": {"hide_from_ui": True},
```

with the comment *"Peer turns are operator-invisible background work; the
operator sees the run in the run list, not a chat bubble they never typed."*

That flag then loses the data three separate times:

- `runtime/journal.py:127` — `_should_persist_human_input_message` returns
  `False` for a hidden `HumanMessage` with no human-input response, so the
  inbound peer text is never written to the run event store.
- `runtime/journal.py:1055` — `_should_reconcile_tool_message` returns `False`,
  so peer-driven tool results are never reconciled in.
- `app/gateway/routers/thread_runs.py:521-532` — `_is_visible_human_message` /
  `_is_visible_ai_message` filter hidden rows out of the message feed.

Net effect: `messages.text` in peer SQLite holds **only the peer envelope**,
never this Alpha's reply to it. A "full conversation" is therefore impossible
from the peer store alone.

> **CORRECTION after implementation.** This was half right, and the half that
> was wrong changed the design. `RunJournal.on_llm_end`
> (`runtime/journal.py:759-771`) persists an AI reply **unconditionally** — it
> never inspects `hide_from_ui`. Tool rows persisted the same way (documented at
> `journal.py:220-223`). So **the local Agent's reply and its tool calls are
> already durable**; only the *inbound* peer prompt is dropped, and the peer
> store already holds that verbatim.
>
> The consequence is that Phase 0 as originally written was unnecessary. No
> `hide_from_ui` change was made, and none should be: widening that flag's
> semantics would ripple across ~15 middleware writers plus memory, channels and
> the runs inspector, to fix a problem that did not exist. What remained was
> Blockers 2 and 3 — the data was already there and unreachable.
>
> `hide_from_ui` therefore stays, doing the job it was written for: keeping
> framed untrusted text out of the ordinary chat feed.

**Blocker 2 — peer threads are unreachable from the UI even though the data is there.**

`peer_network/agent_dispatch.py:109-121`:

```python
def peer_thread_id(peer_agent_id: str) -> str:
    digest = hashlib.sha256(f"peer-thread|{peer_agent_id}".encode()).hexdigest()[:24]
    return f"peer_{digest}"
```

Those runs are created with `owner_user_id=NETWORK_OWNER`, and
`peer_network/storage.py:23` pins that constant:

```python
NETWORK_OWNER = "installation"
```

Every run-detail route is gated with `owner_check=True`, e.g.
`thread_runs.py:2000-2001`:

```python
@router.get("/{thread_id}/runs/{run_id}/events")
@require_permission("runs", "read", owner_check=True)
```

and `authz.py:757-782` turns a mismatch into a hard `404`:

```python
allowed = await thread_store.check_access(thread_id, str(auth.user.id), require_existing=require_existing)
...
if not allowed:
    raise HTTPException(status_code=404, detail=f"Thread {thread_id} not found")
```

An operator's session user id is never `"installation"`, so
`GET /threads/peer_<digest>/runs/<run_id>/events` is a 404 for the very person
who owns the installation. The events, usage, tool receipts and artifacts all
exist and are already redacted server-side — the door is simply locked.

**Blocker 3 — no live stream, no correlation, no retention.**

- `GET /api/peer-network/events` (`routers/peer_network.py:259-280`) exists and
  publishes **17** event types, but `frontend/src/lib/peer-network.ts` has **no
  consumer** for it. `peer-network.test.mjs:190-197` pins the route's existence
  on the backend side only.
- The stream is lossy and non-replayable: `service.py:339-352` uses
  `asyncio.Queue(maxsize=256)` and on overflow **silently drops the oldest
  event** with no gap signal; frames carry no SSE `id:` and `Last-Event-ID` is
  never honoured, so a client connecting mid-flight misses everything before it.
- `PeerNetworkSection.tsx:118-122` polls `refresh()` every 7000 ms, and that
  refresh **does not re-read messages** — inbound messages in the open
  conversation are never picked up by the poll at all.
- There is **no correlation** between a peer message and the run it produced:
  `RunCreateRequest.metadata.peer_network` carries `message_id`,
  `conversation_id`, `peer_agent_id`, `kind` (`services.py:2137-2144`), but no
  peer-network read route joins on it.
- **No retention.** There is no `DELETE`, prune, or TTL anywhere in the package
  for `messages`, `conversations`, `conversation_participants` or `deliveries`.
  The only bounded table is `peers` (`storage.py:271-304`). The only clamp is on
  *reads*: `list_messages` caps at `max(1, min(limit, 1000))` (`storage.py:599`).

---

## 2. Target architecture

```
                         ┌──────────────────────────────────────┐
 remote Alpha ──HTTP/WS──▶ inbound/messages  (bearer auth)      │
                         │   └─▶ receive_remote                 │
                         │        └─▶ _schedule_peer_turn        │
                         │             └─▶ build_peer_turn      │  framed untrusted
                         │                  └─▶ dispatcher      │
                         │                       └─▶ start_run │  on peer_<digest>
                         │                            │        │  owner=installation
                         │                            ▼        │
                         │                      RunManager      │
                         │                            │        │
                         │                  RunJournal (NEW:    │
                         │                  peer marker ⇒      │
                         │                  persist)            │
                         │                            ▼        │
                         │                     run events/usage │
                         └────────────────────────────┬────────┘
                                                      │
        ┌─────────────────────────────────────────────┴──────────────┐
        │  /api/peer-network/transcripts/*   (NEW read-only,         │
        │  owner-checked against NETWORK_OWNER, NOT user_id)          │
        └─────────────────────────────────────────────┬──────────────┘
                                                      │
                                    ExternalAlphaSection.tsx (NEW tab)
                                    ├─ Conversations  (cross-conv list)
                                    ├─ Live Timeline  (SSE, replayable)
                                    └─ Forensics      (events/usage/receipts)
```

### 2.1 The central design decision

Do **not** loosen `owner_check` and do **not** widen `hide_from_ui` globally.
Instead introduce a **peer-network-specific visibility marker** and a
**peer-network-specific read route** that performs its own owner check against
`NETWORK_OWNER`.

Rationale, in the repo's own terms: `hide_from_ui` is a *display* suppression
flag with broad blast radius (memory `l1/pipeline.py:152`, channels
`manager.py:737`, runs inspector `runs-inspector.ts:443`, and ~15 middleware
writers). Changing its semantics would ripple across every consumer. A separate
marker keeps the existing contract intact and makes the peer's visibility an
explicit, reviewable decision owned by the peer plane.

This also satisfies the "peer-network tab only" constraint: peer transcripts are
readable through `/api/peer-network/transcripts/*`, and the normal thread/run
routes keep their current behaviour.

---

## 3. Backend implementation — AS BUILT

> This section records what was actually implemented, which differs from the
> plan in §3.1/§3.2 below. The correction is called out rather than quietly
> rewritten.

### What shipped

| Concern | Implementation |
| --- | --- |
| Join + projection | `peer_network/transcript.py` — pure functions, no I/O, no authz |
| Four read routes | `GET /transcripts`, `/{id}`, `/{id}/turns/{run_id}`, `/{id}/export` |
| Authorization | `_assert_peer_network_scope` — explicit allow-check, fails closed |
| SSE replay | `ReplayWindow` (named result) + `Last-Event-ID` + `stream.reset` |
| Overflow honesty | `stream.overflow` event replaces the silent oldest-drop |
| Retention | `store.prune_messages` + `service.prune_history`, floored at 7 days, on the existing retry cadence |
| UI | `ExternalAlphaSection.tsx` — Conversations / Live timeline / Forensics |
| Client | `lib/external-alpha.ts` over `get`/`send`/`apiFetch`, never raw `fetch` |
| Tests | 41 backend + 39 frontend, all green; 116 existing peer tests unchanged |

### Deviations from the plan, and why

**The `hide_from_ui` marker was not needed.** §3.1 planned a
`PEER_TRANSCRIPT_MARKER` plus changes to `_should_persist_human_input_message`
and `_should_reconcile_tool_message`. Both were dropped: `on_llm_end` already
persists AI replies and tool rows regardless of that flag, so the marker would
have been a no-op wrapped around a misunderstanding. Touching `hide_from_ui`
would also have put an unrelated display-suppression contract at risk for no
gain.

**`_public_peer` was copied, not extended.** `public_peer_summary` in
`transcript.py` is a separate allowlist rather than a shared import, because
`peer_network_tool.py` deliberately has no `from __future__ import annotations`
(see its docstring: PEP 563 would break LangChain's injected-argument
detection). Importing across that boundary was not worth the coupling.

**A test caught a real bug in the SSE replay.** The first `replay_since`
returned `(events, earliest)` and the route only emitted `stream.reset` when the
replay was *empty*. So a client resuming from a cursor older than the retained
ring received a **partial** replay with no gap signal — events 2-25 silently
missing while 26+ arrived, which reads exactly like continuity. Fixed by
returning a named `ReplayWindow` with an explicit `gap` flag, so a caller cannot
read the wrong element. This is why the flag exists rather than a tuple.

**`internal_auth` import was left inside the function.** It matches the existing
pattern in `authz.py:746`, which imports it lazily to avoid a cycle.

### 3.1 (superseded) Planned marker design

Kept for review history only — **not implemented**, see the correction above.

### 3.1 New module: `peer_network/transcript.py`

Owns peer-turn transcript assembly. Knows nothing about FastAPI or `RunManager`.

```python
PEER_TRANSCRIPT_MARKER = "peer_network_visible"

def is_peer_transcript_message(message: BaseMessage) -> bool: ...
def peer_turn_metadata(turn: PeerTurnRequest) -> dict[str, Any]: ...
def link_message_to_peer_turn(message: BaseMessage, *, message_id: str,
                               conversation_id: str, peer_agent_id: str) -> None: ...
```

Responsibilities:

1. `link_message_to_peer_turn` stamps
   `additional_kwargs[PEER_TRANSCRIPT_MARKER] = {message_id, conversation_id,
   peer_agent_id}` on the peer `HumanMessage` so the persisted event row can be
   joined back to the peer envelope later. It must **replace**
   `hide_from_ui: True` rather than sit beside it, because `hide_from_ui`
   short-circuits persistence at `journal.py:127` before the marker is read.
2. Provide the reverse index: given a `(thread_id, run_id)` or a
   `conversation_id`, return the peer message ids involved. Backed by the run
   store's existing `metadata.peer_network` — no new persistence format.
3. Enforce the read-side allowlist. The transcript projection must never emit
   `outbound_token`, `token_hash`, `websocket_url`, `url`, `pairing_code`, or
   any filesystem path. Mirror the `_public_peer` allowlist pattern from
   `tools/builtins/peer_network_tool.py:29-43`.

### 3.2 Change: `services.py:2129-2133`

Replace the blanket `hide_from_ui` on the peer turn's human message with the
peer marker, via `link_message_to_peer_turn`. Keep `hide_from_ui: True` **only**
if a follow-up review shows a consumer that would otherwise render the framed
untrusted prompt as a chat bubble — currently `threads.py:172` and
`thread_runs.py:524` are the two such consumers, and both must be checked
against the marker first.

**Invariant to preserve:** the untrusted framing from `build_peer_turn`
(`agent_dispatch.py:154-158`) must survive unchanged. The marker is metadata
about *visibility*, never about *trust*. `frame_untrusted_text` output goes to
the model exactly as today.

### 3.3 Change: `runtime/journal.py`

Teach `_should_persist_human_input_message` (`journal.py:122-130`) and
`_should_reconcile_tool_message` (`journal.py:1042-1066`) that a message
carrying `PEER_TRANSCRIPT_MARKER` persists **even though** `hide_from_ui` is
set. Order matters: check the marker first, then the existing `hide_from_ui`
rule. Add a regression test asserting a marked peer turn survives a
journal round-trip and a non-marked hidden turn still does not.

### 3.4 New read routes (peer-network router)

Declared **before** the existing `/{conversation_id}` catch-alls, following the
route-order lesson the repo already documents for `groups.py` and `skills`.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/peer-network/transcripts` | `threads:read` | Paginated cross-conversation transcript index: conversation, peer, counts, last activity |
| `GET` | `/api/peer-network/transcripts/{conversation_id}` | `threads:read` | One conversation's interleaved peer-envelope + local-turn transcript |
| `GET` | `/api/peer-network/transcripts/{conversation_id}/runs/{run_id}` | `threads:read` | Full detail for one turn: events, usage, tool receipts, timings |
| `GET` | `/api/peer-network/transcripts/{conversation_id}/export` | `threads:read` | Bounded JSON export (honours the existing 256 KiB payload ceiling) |

**Authorization model — the important part.** These routes must not reuse
`@require_permission(..., owner_check=True)`, because that would compare the
operator's session user id against `NETWORK_OWNER = "installation"` and 404,
which is precisely Blocker 2. Instead:

- `@require_permission("threads", "read")` (no `owner_check`).
- A dedicated `_assert_peer_network_scope(request)` dependency that proves the
  caller may read the installation bucket: an authenticated local user with the
  `threads:read` grant **or** the internal system role. It must be written as an
  explicit allow-check with a named constant, never as "skip the check".
- The `run_id` → run lookup must go through the run store, and the response
  must pass the **same redaction** `thread_runs.py:2058+` applies, so no new
  leak surface is introduced by the alternate route.

Admin-gate anything that could let a peer drive spend (`POST
/peers/{id}/auto-reply`, `pair/rotate`, `github/publish`) — those stay as-is
(`routers/peer_network.py:111-205`).

### 3.5 SSE: make the existing stream replayable

Change `routers/peer_network.py:259-280` and `service.py:339-354`:

1. Emit a monotonic SSE `id:` per event and honour `Last-Event-ID` on reconnect.
2. Replace the silent oldest-drop with an explicit `event: stream.overflow`
   marker carrying the dropped count, so the UI can say "N events were dropped"
   rather than pretending continuity (`service.py:350-352`).
3. Add a bounded in-memory ring buffer (e.g. last 500 events) as the replay
   source. This is deliberately **per-process**; document it as such, matching
   the module's existing honesty about SQLite not being a cross-process
   exactly-once coordinator (`docs/ALPHA_PEER_NETWORK.md:237-241`).
4. Keep the 20s `": keepalive"` comment frame and `X-Accel-Buffering: no`.
5. Nginx already forwards `/api/peer-network/ws` and the well-known paths
   (`docker/nginx/nginx.conf:314`); `/api/peer-network/events` rides the generic
   `/api/` proxy, which is fine once buffering is suppressed. Add a case to
   `backend/tests/test_nginx_peer_network.py` pinning that `/events` is not
   buffered.

### 3.6 Retention

Add to `PeerNetworkStore`:

- `prune_messages(older_than_iso: str) -> int` — delete `messages` older than a
  cutoff; `ON DELETE CASCADE` (`storage.py:145-155`) removes their `deliveries`.
- `retention_days` from `ALPHA_PEER_NETWORK_RETENTION_DAYS`, default 90,
  clamped to a sane floor (e.g. 7) so a misconfigured `0` cannot wipe history.
- Run the prune from the existing `_retry_loop` cadence (`service.py:934-941`,
  every 15s) — do **not** add a second background loop. A new supervisor loop
  would need its own lifecycle ownership, which `backend/AGENTS.md` assigns
  explicitly.
- Report real counts in `_status_snapshot` (`service.py:944`) so the UI shows
  measured numbers, not "pruned" as a claim.

### 3.7 Model tool

Leave `alpha_peer_network` actions unchanged (`discover`/`list`/`send`/`create`).
Do **not** add a `transcript` action: remote-envelope text reaching a model is
exactly the surface the existing allowlist was built to prevent. If transcript
search is wanted later, it needs its own bounded, allowlisted action with its
own test — not an extension of this one.

---

## 4. Frontend implementation

### 4.1 Registration (all five places)

| File | Line | Edit |
| --- | --- | --- |
| `components/NavTabs.tsx` | 36-64 | add `"external-alpha"` to `WorkspaceView` |
| `components/NavTabs.tsx` | 77-113 | one `{ id: "external-alpha", label: "External Alpha", icon: <Network …>, blurb: "Read every cross-installation conversation, turn and receipt", category: "collaboration" }` |
| `components/ChatView.tsx` | 113-139 | `const ExternalAlphaSection = lazy(() => import("@/components/sections/ExternalAlphaSection").then((m) => ({ default: m.ExternalAlphaSection })));` |
| `components/ChatView.tsx` | ~2213 | `) : view === "external-alpha" ? (<Suspense fallback={<SectionFallback />}><ExternalAlphaSection /></Suspense>)` |
| `lib/workspace-view.ts` | 1-29 | add `"external-alpha"` to `WORKSPACE_VIEW_IDS` |

Constraints pinned by existing tests:

- Exactly **one** entry in `WORKSPACE_TABS` — the primary row (`NavTabs.tsx:218`)
  and the "More Views" dropdown (`:219`) both derive from that array, so a
  duplicate renders the tab twice (`project-inspector-view.test.mjs:49-53`).
- `category: "collaboration"` has a matching `SECONDARY_GROUPS` entry
  (`NavTabs.tsx:128-133`), or `workspace-nav.test.mjs:64-90` fails — this is the
  historical `deliberation` bug.
- Not `isPrimary`: the tab belongs in the More Views dropdown next to
  Alpha Network.
- Fix the pre-existing drift while in the file: `workspace-view.ts` is missing
  `"run-inspector"` (26 ids vs 28 in `NavTabs.tsx`). Add it. Better: make
  `NavTabs.tsx` derive its union and array from `@/lib/workspace-view`, which is
  the fix `docs/WIRING_AUDIT.md:326-353` already prescribes.

### 4.2 New client: `src/lib/external-alpha.ts`

Typed client over `get`/`send` from `@/lib/http` only. Never raw `fetch` —
`peer-network.test.mjs:28-30` installs a `fetch` that throws, and
`swarm-messages-wiring.test.mjs:69` fails any path literal matching a real
gateway route.

```ts
export interface PeerTranscriptIndexEntry { … }
export interface PeerTranscript { conversation_id; peer; entries: TranscriptEntry[]; … }
export interface PeerTurnForensics { run_id; thread_id; status; usage; events; tool_receipts; timings; … }

export async function listTranscripts(o?: { peer_id?: string; limit?: number; cursor?: string }): Promise<…>
export async function getTranscript(conversationId: string, o?: { limit?: number; cursor?: string }): Promise<PeerTranscript>
export async function getTurnForensics(conversationId: string, runId: string): Promise<PeerTurnForensics>
export async function exportTranscript(conversationId: string): Promise<Blob>
export function subscribePeerEvents(onEvent: (e: PeerEvent) => void): () => void
```

Follow the existing mapping discipline: coerce every field through
`str`/`nullableStr`/`record`/`asList` (`peer-network.ts:79-94`) rather than
casting. A missing field must render as "not reported", never as an empty
string that reads as a fact.

### 4.3 New section: `src/components/sections/ExternalAlphaSection.tsx` — AS BUILT

Shipped as **one file** with in-file sub-components (`TranscriptRow`,
`TurnForensics`, `DownloadTranscriptButton`) rather than the
`components/sections/external-alpha/` directory sketched below. It came to ~480
lines, comparable to the neighbouring `PeerNetworkSection.tsx`, so splitting it
across a directory would have added indirection without reducing the file. If
it grows past ~700 lines, extract then.

Sub-tabs are **Conversations · Live timeline · Forensics**.

<details>
<summary>Original directory sketch (superseded)</summary>

```
sections/external-alpha/
  ExternalAlphaSection.tsx        # shell: sub-tab strip + shared data fetch
  TranscriptList.tsx              # cross-conversation index
  TranscriptView.tsx              # one conversation, interleaved
  LiveTimeline.tsx                # SSE-driven event feed
  TurnForensics.tsx               # events / usage / tool receipts / timings
  DeliveryReceipts.tsx            # per-recipient receipt table
```

</details>

Sub-tab strip pattern: copy the `bots` sub-tab strip at `ChatView.tsx:2177-2191`.

**Interleaving rule (honesty-critical).** Render two visually distinct roles:
*From peer Alpha* (the `messages` envelope) and *This Alpha replied*
(the persisted run turn). Never merge them into one undifferentiated stream — a
merged stream would let a reader believe a remote peer said something the local
agent said.

**Honest states required** (matching `PeerNetworkSection.tsx`'s discipline):

- Loading: `SkeletonList rows={5}` behind the `loading && !data` guard, so a
  refresh does not blank the surface.
- Error: `ErrorBox` with `onRetry`, showing the server's `detail`. A failed
  Gateway request must never render as an empty transcript list.
- Empty: `EmptyState` with a hint that states what it is *not* proof of —
  e.g. "No cross-installation messages recorded. This is not proof that no
  peer is reachable; run discovery in Alpha Network."
- Unsupported: when `ALPHA_PEER_NETWORK_ENABLED` is unset, the peer plane is
  **off by default** (`service.py:83` `_DEFAULT_ENABLED = False`). Say
  "disabled", never "no traffic".
- Admin-gated reads: `GET /status` is admin-gated
  (`routers/peer_network.py:111-124`), and `errMsg` (`http.ts:63-72`) maps 403
  to a generic message. Render an explicit "needs admin rights" state rather
  than an empty panel.
- Delivery: never render a group message as delivered when a receipt says
  otherwise. Per-recipient rows, mirroring `PeerNetworkSection.tsx:457`.

### 4.4 Styling

Match neighbouring density exactly: `text-[11px]`, `rounded-xl`,
`rounded-2xl`, `border-border/60`, `bg-card/40`, and `inputCls` for every input.
Icons from `lucide-react`, reusing the vocabulary already imported.

Markdown: use `react-markdown` + `remark-gfm` inside `.response-prose`, exactly
as `MessageItem.tsx:417-419` does. Do **not** add `@tailwindcss/typography`
(it is absent for a documented reason) and do not add a `prose` class.
`prose-contrast.test.mjs` pins `.response-prose` to ≥ 4.5:1 with no colour
literals in the light block.

There is no Prettier and no `format` script — no formatting gate runs
(`lint-check.yml:227-230`).

### 4.5 Do not reuse `MessageItem` as-is

`MessageItem` is `ChatMessage`-typed and coupled to `branding.assistantLabel`,
TTS, and run-scoped `onRate(…, runId)` actions. It also regex-sniffs DM/group
prefixes out of `message.content` (`MessageItem.tsx:43-44`), which a peer
message would not match. Write a peer-specific bubble; `MessagesSection.tsx`
already set that precedent with its own inline bubbles.

---

## 5. Security review — non-negotiable

The transcript surface shows *remote-supplied text*. Threats and controls:

| Threat | Control |
| --- | --- |
| Endpoint / credential leak | Response allowlist; never project `url`, `websocket_url`, `outbound_token`, `token_hash`, `pairing_code`. Follow `peer_network_tool.py:29-43`. |
| Local filesystem path leak | Run events contain workspace/upload paths. Redact or gate per-field; do not ship raw operator paths into a tab that a remote peer can influence the contents of. |
| Prompt-injection replay into a *new* sink | Peer text is already framed by `frame_untrusted_text` (`agent_dispatch.py:154-158`). The UI must render it as escaped text. **Never** `dangerouslySetInnerHTML` peer content. |
| Cross-installation read | The transcript routes are scoped to `NETWORK_OWNER` by explicit allow-check, not by omitting `owner_check`. Omission must be visibly justified in the decorator's docstring. |
| Spend escalation | No new route may let a peer grant itself auto-reply. `set_auto_reply` stays admin-gated and model-unreachable (`service.py:427-436`). |
| Unbounded memory | Read clamps (`limit` ≤ 1000), the 256 KiB payload ceiling, the 512 KiB public body ceiling, and the new retention prune. |

Run `backend/tests/test_peer_network_security.py` (43 KB, the largest in the
repo) unchanged, plus `test_sec_audit_peer_public_body.py`.

---

## 6. Test plan

### 6.1 Backend — `backend/tests/`

| File | Covers |
| --- | --- |
| `test_external_alpha_transcript.py` | Marker makes a peer turn persist; a non-marked hidden turn still does not; interleaving order; allowlist excludes credentials/paths. |
| `test_external_alpha_routes.py` | Every transcript route: auth, 404s, pagination, bounds, admin gate. |
| `test_external_alpha_stream.py` | SSE `id:` monotonicity, `Last-Event-ID` replay, explicit overflow marker (never a silent drop). |
| `test_external_alpha_retention.py` | Prune deletes old messages + cascades deliveries; `0`/negative configured days cannot wipe history. |
| `test_peer_network_security.py` (extend) | Transcript responses never contain a token, endpoint, or pairing code. |

### 6.2 Frontend

| File | Covers |
| --- | --- |
| `src/lib/external-alpha.test.mjs` | Route/verb pins; the no-raw-`fetch` guard; field coercion (missing ≠ empty string); SSE unsubscribe on unmount. |
| `src/lib/external-alpha-view.test.mjs` | The five registration points; exactly one `WORKSPACE_TABS` entry; category has a `SECONDARY_GROUPS` heading. |
| Backend contract pin | Extend the existing `peer-network.test.mjs:190-197` technique to assert the new routes exist in `routers/peer_network.py`. |

**Layout rule:** `pnpm test` globs `src/lib/*.test.mjs` **only**. Anything under
`frontend/tests/` needs its own script *and* its own CI step or it silently
never runs. Keep new suites in `src/lib/`.

### 6.3 Gates

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests/test_external_alpha_*.py tests/test_peer_network.py tests/test_peer_network_security.py tests/test_feature_manifest_wiring.py -q
.venv\Scripts\python.exe scripts/check_tool_schemas.py
.venv\Scripts\ruff.exe format --check .

cd frontend
pnpm typecheck      # next typegen && tsc --noEmit — must be 0 errors
pnpm test           # node --test src/lib/*.test.mjs
```

Cross-cutting guards that will run over the new files automatically:
`tailwind-class-guard.test.mjs`, `hit-targets.test.mjs`, `ui-legibility.test.mjs`,
`design-tokens.test.mjs`, `prose-contrast.test.mjs`,
`swarm-messages-wiring.test.mjs`.

---

## 7. Documentation and manifest (same change set)

Capability counts are **generated, never hand-typed**. A registry change means
regenerating the manifest *and* fixing the numbers in every listed file.

- `python scripts/generate_feature_manifest.py` — adds
  `alpha.peer_network.transcript` to `contracts/feature_manifest.json`.
- `docs/ALPHA_PEER_NETWORK.md` — new API surface table; **update the UI
  paragraph** that currently ends "The UI is a separate **Alpha Network**
  workspace tab" to name both tabs.
- `docs/PRODUCTION_READINESS_INVENTORY.md` — the authority on what is
  implemented.
- `README.md`, `llms.txt`, `llms-full.txt`, `docs/FAQ.md`, `docs/COMPARISON.md`
  — capability counts.
- `backend/packages/harness/alpha/peer_network/AGENTS.md` — add the transcript
  module to the ownership map and the read-scope rule.
- `docs/DISCOVERABILITY.md` — this plan is an agent-facing artifact in
  `references/`, matching `ALPHA_TO_ALPHA_COMPLETELY_FREE_COMMUNICATION_OPTIONS.md`.
  If it becomes user-facing, `docs/INDEX.md` is generated by
  `scripts/generate_docs_index.py`, which **fails closed on unclassified
  Markdown** — a `FILE_OVERRIDES` entry, never a hand edit.
- `CHANGELOG.md`.

Do **not** edit `CLAUDE.md` (it only contains `@AGENTS.md`).

---

## 8. Execution order

Each phase is independently reviewable and lands green.

**Phase 0 — unblock** *(smallest, highest value)*
1. `transcript.py` with the marker + allowlist.
2. `services.py:2129-2133` → replace `hide_from_ui` with the marker.
3. `journal.py:122-130` and `:1042-1066` honour the marker.
4. `test_external_alpha_transcript.py`.
   → **Peer turns now persist. This alone makes the feature possible.**

**Phase 1 — read surface**
5. The four transcript routes + `_assert_peer_network_scope`.
6. `test_external_alpha_routes.py`; extend the security test.
   → **Full detail readable over HTTP.**

**Phase 2 — the tab**
7. `external-alpha.ts` client + contract test.
8. The five registration points + fix the `workspace-view.ts` drift.
9. `ExternalAlphaSection` + sub-components.
10. `external-alpha-view.test.mjs`; typecheck + full `pnpm test`.
    → **The tab ships, working, honest.**

**Phase 3 — live + bounded**
11. SSE `id:`/replay/overflow + `subscribePeerEvents`.
12. `LiveTimeline`.
13. `test_external_alpha_stream.py` + nginx `/events` case.
14. Retention prune + `test_external_alpha_retention.py`.
    → **Live updates, and no unbounded growth.**

**Phase 5 — docs**
15. Manifest regeneration + all doc counts, in the same change set.

### Sequencing note

Phase 0 is the whole feature's feasibility. If Phase 3 slips, the tab still
works on polling. If Phase 1 slips, there is nothing to show. Keep 0 → 1 → 2
strictly ordered.

---

## 9. Risk register

| Risk | Severity | Mitigation |
| --- | --- | --- |
| Peer text reaches a new unescaped sink | **High** | Render escaped only; no `dangerouslySetInnerHTML`; allowlist; extend `test_peer_network_security.py`. |
| Transcript routes read another installation's data | **High** | Explicit `_assert_peer_network_scope` allow-check with a documented justification, not a silently-missing `owner_check`. |
| Unbounded message growth | Medium | Retention prune on the existing retry cadence; reported counts; negative-test the floor. |
| SSE reconnect storms | Medium | Bounded ring buffer, `Last-Event-ID`, explicit overflow event, single subscription per tab mount with cleanup. |
| `workspace-nav` / tab-duplication test failures | Low | One `WORKSPACE_TABS` entry; `collaboration` category already has a heading. |
| Capability-count drift in docs | Low | Regenerate manifest; the counts are machine-checked. |
| Cross-process SSE gaps under multi-worker | Low | Already documented as a limitation for SQLite; report it rather than simulating continuity. |

---

## 10. Explicitly out of scope

Stated so the boundary is reviewable, not so it can be quietly widened later.

- **Not** implementing NAT traversal, relays, or guaranteed global discovery.
  The doc already refuses to call a LAN broadcast a global directory
  (`docs/ALPHA_PEER_NETWORK.md:14-18`).
- **Not** enabling libp2p. It stays reported unavailable until a real
  authenticated adapter exists (`service.py:961-965`).
- **Not** multi-tenant owner scoping. The peer plane stays installation-scoped;
  a future multi-tenant deployment introduces a server-resolved owner key rather
  than trusting a request field (`service.py:144-151`).
- **Not** cross-process exactly-once delivery. Single-process SQLite only.
- **Not** a new model-tool action for transcripts.
- **Not** adding peer threads to the personal thread list or to the existing
  Runs / Run Inspector surfaces.