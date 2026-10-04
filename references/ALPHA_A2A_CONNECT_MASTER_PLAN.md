# Alpha-to-Alpha Connect — Enhanced Master Plan

**Scope:** easy copy/paste + QR connection sharing, automatic nearby scanning,
real file/image/document/knowledge transfer, and peer capability/configuration
sharing between two independently installed Alpha installations.

**Status:** plan only. Nothing described in the "proposed" sections is
implemented yet. Every claim about existing behaviour below is pinned to a
`file:line` in this repository at commit `558cc16`.

**Relationship to existing docs:** this document *extends*
[`docs/ALPHA_PEER_NETWORK.md`](../docs/ALPHA_PEER_NETWORK.md), which remains the
operations authority for what already ships. This plan does not restate the
shipped plane; it names the gaps, decides the architecture for the new surface,
and sequences the work.

---

## 1. Verified baseline — what already exists

This plane is **not** greenfield. Roughly 3,800 lines of harness code, a
722-line router, 12 backend test files and two frontend tabs already ship. Any
plan that ignores this will duplicate a working protocol.

| Concern | Owner (verified) |
| --- | --- |
| Identity, pairing code, Agent Card | `backend/packages/harness/alpha/peer_network/identity.py` (253) |
| SQLite store, FTS5 index, retention | `peer_network/storage.py` (890) |
| Lifecycle, delivery, retry, SSE | `peer_network/service.py` (1218) |
| HTTP-first transport, WS fallback | `peer_network/transport.py` (266) |
| UDP + optional mDNS discovery | `peer_network/discovery.py` (366) |
| Pairing throttle | `peer_network/ratelimit.py` (357) |
| Transcript projection | `peer_network/transcript.py` (435) |
| Inbound agent turn | `peer_network/agent_dispatch.py` (185) |
| GitHub card rendezvous | `peer_network/github.py` (193) |
| Gateway routes (23 authed + 6 public) | `backend/app/gateway/routers/peer_network.py` (722) |
| Model tool `alpha_peer_network` | `peer_network/../tools/builtins/peer_network_tool.py` (142) |
| Control-plane UI | `frontend/src/components/sections/PeerNetworkSection.tsx` (470) |
| Read/history UI | `frontend/src/components/sections/ExternalAlphaSection.tsx` (820) |

Already working and **not** to be rebuilt: HTTP+WebSocket transport,
per-recipient delivery receipts, idempotency keys, 6 conversation topologies,
19 message kinds, FTS5 transcript search, analytics, behaviour-trace drill-down,
retention with a 7-day floor, SSE replay ring, pairing throttle with lockout,
endpoint SSRF validation, GitHub card rendezvous.

### 1.1 Verified defects found while planning

These are real today and must be fixed, not designed around:

| # | Defect | Evidence |
| --- | --- | --- |
| D1 | `docs/ALPHA_PEER_NETWORK.md:125` states `ALPHA_PEER_NETWORK_ENABLED` defaults to `1`; the code default is `False`. A doc/code disagreement that makes an operator believe the plane is live when it is off. | `service.py:87` `_DEFAULT_ENABLED = False` |
| D2 | `file_offer` / `file_request` are declared message kinds with **zero** implementation — no schema, no transfer, no accounting, and the payload is never shown to the model. | `models.py:63-64`, `agent_dispatch.py:57-58` are the only occurrences in the package |
| D3 | The retry loop is a flat 15 s tick with no backoff and no attempt counter; `deliveries.error` is overwritten each attempt, so a permanently dead peer retries forever and the history of the failure is destroyed. | `service.py:1099-1111` |
| D4 | The Gateway sends `Permissions-Policy: camera=()`, which makes in-browser QR scanning impossible without a backend change. | `security_headers_middleware.py:42` |
| D5 | `GET /status` and `POST /pair` return the raw peer row including `url`, `websocket_url`, `card` and `owner_id`. Stricter than the model tool's allowlist, so the *tool* is safer than the *API*. | `service.py` `get_peer`/`status` vs `peer_network_tool.py:29-39` `_public_peer` |
| D6 | Helm never publishes UDP `8743`, so discovery cannot work on a Kubernetes install. | documented in `docs/ALPHA_COLLABORATION_AUDIT.md:507-514` |
| D7 | `PeerEnvelope.payload` has no Pydantic size bound; only a service-level byte check. A malformed envelope validated outside the service is unbounded. | `models.py` `payload: dict[str, Any] = {}` vs `service.py:836` |

---

## 2. The eight requests, mapped to concrete gaps

| # | Request | Current state | Verdict |
| --- | --- | --- | --- |
| R1 | Easily copy the connection address | Only `copyPairingCode` exists, for the bare code. No endpoint, no address, no one-string invite. | **new** |
| R2 | Easily share it | No `navigator.share`, no invite link, no QR. | **new** |
| R3 | Paste a friend's code | The pairing-code input is `type="password"` with no paste affordance and no parser. | **new** |
| R4 | QR to connect | No QR library, no renderer, no scanner; camera blocked by D4. | **new** |
| R5 | Mutual/automatic scanning for nearby agents | UDP beacon + optional mDNS exist server-side; the browser fires one `POST /discover` and shows a flat list. No live scan, no provider attribution, no auto-pair. | **extend** |
| R6 | Send messages, knowledge, files, images, documents | Chat text only. `file_offer` is a phantom kind (D2). | **new** |
| R7 | Proper communication | Six topologies, receipts, read state exist. Missing: typing/presence, reactions, edit/delete propagation, reply threading, search in the send surface. | **extend** |
| R8 | Share configuration/resources | Nothing. | **new** |

---

## 3. Architecture decisions

These are the load-bearing choices. Everything in §5–§7 depends on them.

### D-1 — One connection string, two copy modes

A single URI carries the whole connection description:

```
alpha://connect?v=1&a=<agent_id>&u=<url>&w=<ws_url>&n=<name>&e=<expiry_epoch>&k=<pairing_code>
```

Parsing is strict: unknown `v` refuses rather than guessing, `u`/`w` go through the
existing `validate_endpoint` (SSRF/metadata/scheme guard), `a` through
`validate_agent_id`, and every field is length-bounded before use.

Two copy modes, because "easy to share" and "safe to share" are different asks:

| Mode | String contains `k=` | Where it may go |
| --- | --- | --- |
| **Full invite** (default) | yes | A private channel, QR shown in person |
| **Address only** | no | Anywhere — it is public metadata |

The UI states this in the copy row itself, not in a tooltip.

**Why `k=` travels in the same string.** Splitting the code from the address
would mean two copy operations and two pastes, which is exactly the friction the
request is trying to remove. The existing plane already treats the pairing code
as a bearer credential and already warns about it; this keeps one warning and one
action instead of adding a ritual.

### D-2 — Redeeming an invite rotates the pairing code

Today the pairing code is a **persistent** bearer credential: anyone who ever
sees it can pair until an operator remembers to rotate. A screenshot in a chat
log is therefore permanent access.

Redeeming rotates. The owner's UI immediately shows the new code and marks the old
one consumed. `POST /pair/rotate` stays for manual rotation. Existing paired peers
are unaffected — rotation is an ingress credential, not a session key.

Consequence to state honestly in the UI: **an invite is single-use.** Scanning the
same screenshot twice reports "this invite was already used", not a silent
re-pair.

### D-3 — Expiry is in the claim, and the clock is skewed-tolerant

`e=` is an absolute epoch second. A claim is refused when
`now > e + CLOCK_SKEW_TOLERANCE` (default 300 s). Default lifetime 15 minutes,
ceiling 24 hours — refused, never clamped. A QR on a screen and a code read aloud
are both slow paths; 15 minutes is long enough for a human handoff and short
enough that a leaked screenshot dies.

### D-4 — Trust gains exactly one tier, and it grants nothing secret

`PeerTrust` is `discovered | paired | blocked`. Adding a tier is tempting — an
invite *feels* more trusted than a broadcast beacon — but a trust tier that
implies a capability is a capability. So:

- `linked` records **provenance**: this peer arrived via a redeemed invite rather
  than a beacon. It is a label the operator can filter and sort by.
- It grants **nothing**. Delivery still needs a paired outbound token. Auto-reply
  still needs an operator grant. Inbound turns still cost nothing until granted.

The store already protects this: `upsert_peer` uses
`ON CONFLICT(agent_id) DO UPDATE` and cannot downgrade a `paired` row
(`storage.py:380`), and `set_trust` can never grant a credential. A beacon from a
linked peer must not silently erase the link, so the store gets one additive
column (`link_source`) rather than a trust mutation.

### D-5 — A capability/card summary is a new message kind, not a new tool

A peer asking "what can you do?" today gets a free-text `capabilities` message.
Configuration sharing needs a **structured, bounded, diffable** summary, so a new
kind `capability_summary` carries a schema'd payload. It is deliberately not a new
model tool: adding a tool changes `contracts/feature_manifest.json` and the
generated capability counts in `README.md`, `llms.txt`, `llms-full.txt`,
`docs/FAQ.md` and `docs/COMPARISON.md` in the same change set. A new *action* on
the existing `alpha_peer_network` tool keeps the tool count flat.

### D-6 — Blobs are a separate plane with their own auth, limits and store

Envelope payloads are capped at 256 KiB (`service.py:78`) and public bodies at
512 KiB. A screenshot does not fit and must not be fought into the envelope.
So file transfer is a distinct subsystem:

- Its own table, its own store, its own retention, its own size ceiling.
- Its own token, because a blob capability is a *read* capability and the pairing
  token is a *messaging* capability. Conflating them means a leaked message token
  also reads every file.
- `file_offer` / `file_request` finally get the implementation D2 admits is
  missing, and they stay **metadata-only by design**: they negotiate. They never
  carry bytes.

### D-7 — Capability sharing is a pull with a preview, never a silent write

Sharing "everything" is a config-mutation request, and `config.yaml` already has
a documented history of competing writers. The existing
`alpha.ops.config_diagnosis` is deliberately read-only and proposes without
applying; this feature must not become a fourth writer.

So sharing is a **pull** with a mandatory diff preview:

1. Peer requests a summary (`capability_summary`).
2. Operator sees a field-level diff and approves per section.
3. The payload lands in an **inbox** — a proposal document.
4. Applying it is an explicit, separately-confirmed operator action that reuses
   the existing `config.yaml` write locks.

A peer can never write the operator's config. It can hand over a proposal.

### D-8 — QR scanning: vendored decoder, optional, camera grant scoped

Frontend dependencies are deliberately minimal (12 runtime packages, no QR
library, no scanner). Two options were weighed:

| Option | Cost | Verdict |
| --- | --- | --- |
| Add `jsqr` / `zxing` | 1 new runtime dep, WASM or large JS | Rejected — the repo pins a small dep surface deliberately |
| Backend-side decode | Pushes a camera frame over the network to the Gateway | Rejected — worse privacy story, worse latency |
| **Vendored minimal decoder** | ~500 lines in `lib/`, zero deps | **Chosen** |

`lib/qr-decode.ts` decodes from an `ImageData`. The renderer is
`lib/qr-encode.ts` — an encoder is far smaller than a decoder and needs no
dependency at all.

**The camera header (D4) is the real blocker.** `Permissions-Policy: camera=()`
must change to a self-origin grant. That is a deliberate security-posture change
and it is scoped:

```python
# camera is granted to self only; geolocation stays fully denied.
("permissions-policy", "camera=(self), geolocation=()"),
```

`frontend/src/lib/voice.test.mjs:325` pins the current value and must be updated
in the same change. Rationale for accepting the change: the frame never leaves
the browser, decoding is local, and the feature is operator-initiated. If that
tradeoff is rejected, the fallback is **upload-a-QR-image**, which needs no
permission change and still beats typing a URL — the UI ships that path first.

### D-9 — Fix D1, D3, D5, D6, D7 in this branch

They are cheap, they are real, and every new UI surface makes them more visible.
Leaving D1 (docs claiming the plane defaults on) in place while adding a prominent
status panel would be actively misleading.

---

## 4. Data model additions

All additive to the existing schema in `storage.py:91-185`. The `_ensure_column`
helper already exists and is the established migration path (it is how
`peers.auto_reply` was added).

```sql
-- D-4: provenance without a trust mutation
ALTER TABLE peers ADD COLUMN link_source TEXT;          -- 'invite' | NULL
ALTER TABLE peers ADD COLUMN link_expires_at TEXT;

-- D-3: honest retry history
ALTER TABLE deliveries ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE deliveries ADD COLUMN next_attempt_at TEXT;
ALTER TABLE deliveries ADD COLUMN last_attempt_at TEXT;

-- R6: blob plane (new tables, new file blobs.db to keep the WAL small)
CREATE TABLE blobs (
    blob_id        TEXT PRIMARY KEY,
    owner_id       TEXT NOT NULL,
    conversation_id TEXT,
    sender_id      TEXT NOT NULL,
    filename       TEXT NOT NULL,
    media_type     TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes     INTEGER NOT NULL,
    sha256         TEXT NOT NULL,
    chunk_size     INTEGER NOT NULL,
    chunk_count    INTEGER NOT NULL,
    state          TEXT NOT NULL DEFAULT 'pending',   -- pending|complete|expired
    created_at     TEXT NOT NULL,
    expires_at     TEXT NOT NULL
);
CREATE INDEX idx_blobs_state ON blobs(state, expires_at);
CREATE UNIQUE INDEX idx_blobs_sha_owner ON blobs(owner_id, sha256);

-- R8: proposals land in an inbox, never in config.yaml
CREATE TABLE share_proposals (
    proposal_id   TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    peer_agent_id TEXT NOT NULL,
    direction     TEXT NOT NULL,          -- 'inbound' | 'outbound'
    section       TEXT NOT NULL,          -- 'models'|'toolsets'|'engines'|'mcp'|'skills'
    diff_json     TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|applied|dismissed|expired
    created_at    TEXT NOT NULL,
    resolved_at   TEXT
);
```

Content-addressed dedupe via `(owner_id, sha256)` means re-sending the same file
to the same conversation stores bytes once.

---

## 5. Backend work packages

Ordered. Each is independently shippable and independently testable.

### BP-1 — `invite.py`: codec, claims, redemption (new, ~260 lines)

`backend/packages/harness/alpha/peer_network/invite.py`

- `InviteClaims` dataclass + `to_uri()` / `parse_invite(uri)`.
- Strict parse: unknown `v` → refuse; every field length-bounded; `u`/`w`
  validated by the existing `validate_endpoint`; `a` by `validate_agent_id`.
- `build_invite(service, *, include_secret, ttl_seconds)`.
- `InviteError` for every refusal, each naming the offending field.
- **Expiry** per D-3, skew tolerance 300 s, ttl ceiling 24 h.

`parse_invite` is pure and gets the heaviest unit-test coverage in the plan: it is
the one parser that consumes attacker-controlled text.

### BP-2 — Service: redeem, rotate-on-redeem, link provenance, backoff

`service.py`

- `redeem_invite(uri, *, trust=...)` — parse → fetch remote card → verify
  `claims.a == card.agent_id` (**this is the anti-endpoint-substitution check**;
  a MITM who rewrites `u=` gets a card whose agent id does not match) → throttle
  check → `accept_pair` → **rotate local pairing code** → stamp `link_source`.
- `retry_pending` gains per-recipient exponential backoff and an attempt counter
  (D3). `deliveries.error` becomes an append-only tail, not an overwrite.
- `status()` / `pair()` responses get the `_public_peer` allowlist applied (D5) —
  the API stops being looser than the tool.

### BP-3 — `capability_summary` kind + diff engine (new, ~300 lines)

- `models.py`: add `capability_summary` to `MESSAGE_KIND_VALUES`; give
  `payload` a real Pydantic bound (D7).
- `alpha/peer_network/capabilities.py`: builds the summary from the generated
  `contracts/feature_manifest.json` via the **single `manifest_source` loader**,
  never a second re-derivation — the same rule the self-inventory registries
  follow. Produces section-keyed, bounded, diffable output.
- `share_proposals` CRUD. Apply stays an operator action (D-7).

### BP-4 — `blobs.py`: chunked transfer with resumable receipts (new, ~520 lines)

- `PUT/GET/HEAD /api/peer-network/blobs/{blob_id}/chunks/{index}` on the
  **public** router, authenticated by a per-blob capability token (D-6), plus
  `POST /blobs` (offer, metadata only) and `POST /blobs/{id}/complete`.
- Chunk size 256 KiB; per-blob ceiling 32 MiB, per-conversation daily ceiling
  256 MiB — **refused with the bound named, never clamped**.
- Resume: `HEAD` returns received chunk indices, so an interrupted 32 MiB
  transfer continues instead of restarting.
- `sha256` verified on completion. A mismatch fails the blob; it never marks it
  complete.
- `file_offer` / `file_request` become real negotiation messages carrying
  `blob_id`, `filename`, `media_type`, `size_bytes`, `sha256` (D2).
- Retention: blobs expire with their conversation, and are counted in
  `prune_history` so the retry loop stays the only background task.

### BP-5 — Gateway routes + header change

```
# authenticated (threads:read / threads:write)
GET    /api/peer-network/invite?include_secret=&ttl_seconds=
POST   /api/peer-network/invite/redeem
GET    /api/peer-network/peers/{agent_id}                # D5 allowlist
GET    /api/peer-network/share/proposals
POST   /api/peer-network/share/proposals/{id}/dismiss
POST   /api/peer-network/share/proposals/{id}/apply      # operator, admin

# public (own token, exact paths)
POST   /api/peer-network/blobs
PUT    /api/peer-network/blobs/{blob_id}/chunks/{index}
HEAD   /api/peer-network/blobs/{blob_id}
GET    /api/peer-network/blobs/{blob_id}/chunks/{index}
POST   /api/peer-network/blobs/{blob_id}/complete
```

Route-order trap, the same one that already bit this plane twice: `/invite/redeem`
must precede any `/invite/{param}` catch-all, and `/share/proposals` literals
before `/share/{param}`. Exact-path CSRF + auth exemptions extend in
`auth_middleware._PUBLIC_EXACT_PATHS` and `csrf_middleware._CSRF_EXEMPT_EXACT_PATHS`
— and the WebSocket path stays out of the auth list, because
`BaseHTTPMiddleware` does not cover the websocket scope.

Also: `security_headers_middleware.py:42` → `camera=(self), geolocation=()`,
with `frontend/src/lib/voice.test.mjs:325` updated in the same change.

### BP-6 — Model tool: new actions, still four verbs

`alpha_peer_network` grows `share`, `fetch`, `files`, `invite` — **actions, not
tools**, so `contracts/feature_manifest.json` and the generated capability counts
stay flat. Every action passes through `_public_peer`'s allowlist. `invite` is
admin-gated and returns the string only to an operator-initiated call; the model
can never read a pairing code (that rule is currently tested and must stay
tested).

### BP-7 — Model tool gets file awareness (D2 close-out)

`build_peer_turn` currently reads only `text`, so a `file_offer` payload never
reaches the model. A peer turn over a file offer must reference the blob by id
and size without inlining bytes, and must never receive a filesystem path.

---

## 6. Frontend UI specification

### 6.1 Primitive gaps to close first

`frontend/src/components/ui.tsx` is missing three things every new surface needs:

| Addition | Why |
| --- | --- |
| `Notice tone="warn"` | `Notice` is **emerald-only** today, so truncation and FTS-missing warnings borrow a success style. A transfer that failed must not render green. |
| `useCopyButton` hook | Four sites inline `navigator.clipboard` today. `RunInspectorSection.tsx:1649-1683` is the best existing pattern (capability guard, in-flight lock via `useRef`, disabled state, worded failure) and becomes the shared implementation. |
| `Modal` focus trap | `a11y.ts` already ships `useFocusTrap`/`useScrollLock`; `ui.tsx`'s `Modal` does not wire them. Peer detail and QR modals need it. |

### 6.2 `PeerNetworkSection` restructure

Split the current 470-line single page into five panels behind a pill switcher,
matching the `ExternalAlphaSection` sub-tab pattern. One shared `error` string
today means any panel's failure blanks the tab — that becomes per-panel.

```
┌─ Alpha Network ──────────────────────────────────────────────────┐
│ [ Connect ] [ Peers ] [ Conversations ] [ Transfers ] [ Settings ] │
├───────────────────────────────────────────────────────────────────┤
```

#### Panel 1 — Connect

The headline feature. Three ways in, one way out.

```
  YOUR CONNECTION
  ┌──────────────────────────────────────────────────────────────┐
  │  Alpha · alpha-7f3a…                          ● enabled      │
  │                                                              │
  │  ┌────────────────────────────────────────┐  ┌────────────┐  │
  │  │ alpha://connect?v=1&a=alpha-7f3a…&…    │  │  ▓▓▓▓▓▓▓  │  │
  │  │ (monospace, truncated, selectable)     │  │  ▓ ▓▓▓ ▓▓  │  │
  │  └────────────────────────────────────────┘  │  ▓▓▓▓▓▓▓▓  │  │
  │  [ Copy invite ] [ Copy address ] [ Share ] │  └────────────┘  │
  │  ⓘ Full invite contains your pairing code.  │  Scan QR         │
  │    Address only is safe to post anywhere.   │                  │
  │                                                              │
  │  Expires in 14:32   [ Renew ]  ⓘ Single use — redeemed once   │
  └──────────────────────────────────────────────────────────────┘

  ADD A FRIEND'S ALPHA
  ┌──────────────────────────────────────────────────────────────┐
  │  [ Paste invite ]  or  drop a .txt / screenshot              │
  │                                                              │
  │  ┌────────────────────────────────────────────────────────┐  │
  │  │ Paste the alpha://connect… string your friend shared    │  │
  │  └────────────────────────────────────────────────────────┘  │
  │                                          [ Connect ]        │
  └──────────────────────────────────────────────────────────────┘

  NEARBY ALPHAS                              ⟨ Scanning · live ⟩
  ┌──────────────────────────────────────────────────────────────┐
  │ ● alpha-91c2  "Priya's laptop"      UDP · 2 s ago   [Pair]   │
  │ ● alpha-4e88  "Home server"         mDNS · 5 s ago  [Pair]   │
  │ ○ alpha-b7d1  "Office desktop"     UDP · never     [Pair]   │
  └──────────────────────────────────────────────────────────────┘
```

**Paste parsing is the detail that makes this feel easy.** `onPaste` intercepts
before the textarea:

1. Text that parses as an invite → prefill + inline summary card
   (`Priya's laptop · alpha-91c2 · 192.168.1.20:8001 · expires 12 m`) + `Connect`.
2. An image file → decode QR locally (`lib/qr-decode.ts`), then as (1).
3. Anything else → falls through to the textarea untouched.

Field-level validation errors name the field: `Not an Alpha connection string
(unknown version 2)`, `That invite expired 3 minutes ago`, `That invite was
already used`.

**Nearby scanning.** Replaces the one-shot `Discover LAN peers` button:

- `POST /discover` on mount, then poll on the existing SSE stream
  (`GET /api/peer-network/events`) instead of the current blind 7 s
  `refresh(true)` — the tab already has a stream client in `external-alpha.ts`.
- Live status pill: `Scanning · live` / `Scanning · reconnecting` / `Discovery off`.
- **Per-provider attribution** (`UDP` / `mDNS`) — today the provider's real
  reason is discarded in favour of one flat list, which makes an mDNS outage
  indistinguishable from an empty network.
- **Progressive auto-pair is refused, and the UI says why.** R5 asks for mutual
  automatic scanning; discovery genuinely cannot grant access (the module
  contract's first rule). The honest version: when exactly one unpaired peer is
  found and the operator has enabled *Suggest*, the UI surfaces a one-tap
  `Pair found Alpha` prompt. It is still a deliberate operator action.

#### Panel 2 — Peers

Per-peer card with the fields that are typed but never rendered today:
`url`, `websocket_url`, `preferred_transport`, `source`, `first_seen`,
`last_seen`, `version`, `skills`, and the Agent Card.

- Row actions: `Copy address`, `Message`, `Block` / `Allow discovery`.
- **Auto-reply toggle** — backend `PATCH /peers/{id}/auto-reply` exists and is
  admin-gated; there is no client function and no UI. It is a token-spending
  grant, so it renders behind an explicit confirmation that names the cost, and
  `auto_reply` is currently typed in `external-alpha.ts` and never displayed.
- **Link provenance badge**: `invite` vs `beacon` (D-4).
- Detail drawer via `ui.tsx` `Modal` + `a11y.ts` focus trap.

#### Panel 3 — Conversations

Today's thread is a `max-h-80` scroll of buttons. Replace with a real message
view:

- Message bubbles, sender + installation identity, per-recipient receipts inline.
- **Composer**: text, plus attach (file/image/document), plus paste-to-attach —
  `Composer.tsx:269-277` already handles file paste, so the pattern exists.
- Attachment chips render media type, size, and **transfer progress**
  (`uploading 34% · 4.2 / 12.3 MB`), never a bare spinner.
- Typing indicator and presence (new kinds `typing` / `presence`, excluded from
  `AUTO_REPLY_KINDS` — bookkeeping must not cost tokens).
- Reactions, reply-to threading, edit/delete propagation as new kinds.
- `GET /conversations/{id}` and `POST /conversations/{id}/messages` both exist
  server-side with no client wrapper; wire them.

#### Panel 4 — Transfers

| Column | Content |
| --- | --- |
| File | name, media type, size |
| Direction | sent / received |
| Peer | installation name |
| State | `pending` / `uploading 34%` / `verifying` / `complete` / `failed` |
| Actions | resume, verify, download, delete |

`verifying` is a distinct state because sha256 verification is real work, and
`failed` must say *why* — the same honesty rule that keeps a partial fan-out from
rendering as a successful group send.

#### Panel 5 — Settings

Discovery provider state with each provider's real error (today the error is
computed then thrown away), retention window, transport state, the throttle
policy snapshot (already credential-free and already exposed by
`ratelimit.py`), and `Advertised address` guidance for the NAT case.

### 6.3 Cross-plane additions

- `OverviewSection.tsx` atlas card gains copy/invite/QR affordances.
- `workspaceViewUrl()` (`workspace-view.ts:54`) is **already written and has zero
  call sites** — it is the share-link primitive. `?view=peers&invite=<redacted>`
  gives a deep link to the Connect panel. The invite itself is never placed in a
  URL (it would land in browser history and server logs); the link carries only
  the view.
- `NavTabs.tsx` — no new tab id. `peers` and `external-alpha` already exist and
  `external-alpha-view.test.mjs:27-32` pins exactly one entry each.

### 6.4 New lib modules

| File | Lines | Purpose |
| --- | --- | --- |
| `lib/qr-encode.ts` | ~200 | Zero-dependency QR encoder → SVG path. |
| `lib/qr-decode.ts` | ~500 | Zero-dependency QR decoder from `ImageData`. |
| `lib/invite.ts` | ~180 | Pure client mirror of the invite grammar + validation. Mirrors the backend grammar; **never** invents fields. |
| `lib/peer-network.ts` | +~140 | `fetchInvite`, `redeemInvite`, blob client, share-proposal client, `getPeer`, `setAutoReply`. |
| `lib/use-copy-button.ts` | ~60 | Shared clipboard hook. |

---

## 7. Test matrix

TDD is mandatory in this repo (`backend/AGENTS.md`). Named suites, all new:

### Backend

| Suite | Focus |
| --- | --- |
| `test_peer_invite_codec.py` | Round-trip; unknown version refused; every field bounded; `u`/`w` SSRF-refused; expiry + skew; over-ceiling ttl refused not clamped; non-URI input refused; a parse failure names the field. |
| `test_peer_invite_redeem.py` | Redeem rotates the code; a second redeem of the same string is refused; agent-id mismatch refused (endpoint substitution); throttle respected; `link_source` stamped; blocked peer refused. |
| `test_peer_blob_transfer.py` | Chunk bounds; resume after interruption; sha256 mismatch fails the blob; per-blob and daily ceilings refused with the bound named; wrong token refused; expired blob 410; metadata-only offer carries no bytes. |
| `test_peer_capability_share.py` | Summary bounded and section-keyed; diff computed against live local state; inbound proposal never writes `config.yaml`; apply requires operator + admin. |
| `test_peer_retry_backoff.py` | Backoff schedule; attempt counter increments; error tail preserved; no infinite tight retry. |
| `test_peer_network_security.py` (extend) | D5: `status`/`pair` responses carry no `url`/`card`/`owner_id`. |
| `test_peer_network_helm_discovery.py` | D6: Helm publishes UDP 8743 with an explicit bind address. |
| `test_feature_manifest_wiring.py` (extend) | No new tool; the four new actions are on the existing tool. |

### Frontend

`pnpm test` globs **only** `src/lib/*.test.mjs`, so anything outside that
directory needs its own script **and** its own CI step or it silently never runs.

| File | Focus |
| --- | --- |
| `src/lib/invite.test.mjs` | Client grammar mirrors the backend; bad input refused with a field name. |
| `src/lib/qr-encode.test.mjs` | Known string → expected module matrix. |
| `src/lib/qr-decode.test.mjs` | Encodes then decodes; `camera=()` and non-https origins fail honestly. |
| `src/lib/peer-network.test.mjs` (extend) | New client functions, route/verb pins, failing request rejects rather than emptying a list. |
| `src/lib/peer-network-view.test.mjs` (**new**) | The missing view test — `PeerNetworkSection.tsx` is currently pinned by *no* test. Asserts no `dangerouslySetInnerHTML`, an explicit paste handler, progress states, and "a failed request is never an empty list". |

---

## 8. Honesty rules this feature must not break

The plane's existing contract is that a partial truth must be visible. New
surfaces inherit all of it:

1. **Discovery is not access.** An invite is a request, never an established
   peer. Nothing in the Connect panel may render a scanned-but-unpaired Alpha as
   connected.
2. **Auto-pair stays an operator action.** See §6.2.
3. **Progress is measured.** `uploading 34%` comes from received chunk bytes, not
   a timer. A transfer that cannot report progress says `starting`, not `34%`.
4. **A truncated transfer is not a delivered one.** A `failed` blob renders as
   failed with its reason, forever, until retried or deleted.
5. **A capability summary is a snapshot.** It reports what the peer's *generated
   manifest* said at that moment, not what the peer can do now. Label it
   `reported`, like `CapabilityBadge`'s existing `Not reported` state.
6. **"Disabled" is not "no traffic".** D1 must be fixed before the Settings panel
   exists: an off plane says `off`, with the env var named.
7. **A peer cannot grant itself auto-reply.** Admin-gated, and a peer cannot
   unpair itself.
8. **Single-use invites say so before redemption**, not after a confusing
   failure.

---

## 9. Security review before merge

| Concern | Control |
| --- | --- |
| Invite is a bearer credential | Explicit labelling, 15-min default expiry, single-use by rotation, `include_secret=false` mode for public sharing. |
| Endpoint substitution in a pasted string | Redeem fetches the card and refuses when `claims.a != card.agent_id`. |
| Blob capability ≠ messaging capability | Separate token (D-6). |
| Path traversal via filename | Filenames are metadata only, sanitised for display, never joined to a path. Downloads stream from the blob store by `blob_id`. |
| Blob storage exhaustion | Per-blob + per-conversation daily ceilings, refused not clamped; blobs expire. |
| Camera privacy | `camera=(self)`; frames never leave the browser; decoder is local; scanner is operator-initiated. Fallback (image upload) needs no permission. |
| Model cannot exfiltrate credentials | The tool's allowlist is unchanged in spirit and extended in coverage; `invite` is admin-gated; a test pins that no action returns `k=`. |
| New public surface | Every new public path is exact-path in auth + CSRF exemption lists, and covered by `test_sec_audit_peer_public_body.py` for the body ceiling. |
| Untrusted peer text | Rendered escaped, never raw HTML, never promoted into a prompt (`frame_untrusted_text` unchanged). |

---

## 10. Sequencing

| Phase | Contents | Gate |
| --- | --- | --- |
| **0 — Truth** | D1 doc fix, D3 backoff, D5 allowlist on `status`/`pair`, D6 Helm UDP, D7 payload bound | Existing peer suites green, unmodified |
| **1 — Invite + copy/paste** | BP-1, BP-2 (redeem/rotate/link), BP-5 invite routes, `lib/invite.ts`, `useCopyButton`, `Notice tone="warn"`, Connect panel | `test_peer_invite_*.py` green |
| **2 — Nearby scanning** | Live SSE-driven scan, provider attribution, one-tap pair suggestion | view test green |
| **3 — QR** | `lib/qr-encode.ts`, `lib/qr-decode.ts`, camera header + `voice.test.mjs` update, image-upload fallback | qr suites green |
| **4 — Transfers** | BP-4 blob plane, real `file_offer`/`file_request`, BP-7 model awareness, composer attachments, Transfers panel | `test_peer_blob_transfer.py` green |
| **5 — Conversations** | Message view, typing/presence, reactions, threading, conversation detail routes | view test green |
| **6 — Sharing** | BP-3 `capability_summary` + diff engine, proposal inbox, Settings panel | `test_peer_capability_share.py` green |

Phase 1 alone already delivers the user's primary ask — copy, paste, connect.
Phases 3 and 4 are the two that carry real risk (a security-header change and a
new public write surface), so they are isolated rather than bundled.

---

## 11. Obligations this branch inherits

Not optional; the repo gates on them.

- `backend/packages/harness/alpha/peer_network/AGENTS.md` — extend with the
  invite grammar, blob-token separation, and the link-vs-trust rule.
- `docs/ALPHA_PEER_NETWORK.md` — new sections for invite, transfers, sharing;
  fix the `ALPHA_PEER_NETWORK_ENABLED` default (D1).
- Root `AGENTS.md`, `backend/AGENTS.md`, `frontend/AGENTS.md`,
  `frontend/src/AGENTS.md` — extend the existing peer-network pointers.
- `contracts/feature_manifest.json` — regenerate **only if** a registry changes.
  The design here adds no tool and no router mount, so the counts should not
  move; if they do, `README.md`, `llms.txt`, `llms-full.txt`, `docs/FAQ.md` and
  `docs/COMPARISON.md` move in the same change set.
- `ruff format --check` before push.
- `CHANGELOG.md` entry.
- If a new `docs/` page appears, `scripts/generate_docs_index.py` fails closed
  until it has a `FILE_OVERRIDES` entry — never a hand edit of `docs/INDEX.md`.
  This plan deliberately lives in `references/` to avoid that, and extends the
  already-classified `ALPHA_PEER_NETWORK.md` rather than adding a page.