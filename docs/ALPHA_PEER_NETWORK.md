# Alpha-to-Alpha Free Communication Network

## Purpose and scope

Alpha Network is a separate, local-first communication plane for independently
installed Alpha instances. It is intentionally different from Alpha's existing
bot roster and group-chat planes:

- **Local roster/group chat:** agents inside one Gateway process.
- **Alpha Network:** separately identified Alpha installations, discovered and
  paired over the network.

The implementation is designed around the reference plan in
[`references/ALPHA_TO_ALPHA_COMPLETELY_FREE_COMMUNICATION_OPTIONS.md`](../references/ALPHA_TO_ALPHA_COMPLETELY_FREE_COMMUNICATION_OPTIONS.md).
The important constraint in that plan is still true: arbitrary Internet-wide
automatic discovery cannot be guaranteed without *some* rendezvous, relay, or
public peer infrastructure. This implementation does not pretend that a LAN
broadcast is a global directory.

## What is implemented

| Layer | Implementation | Cost/dependency | Status |
|---|---|---|---|
| Local identity | `alpha.peer_network.identity.LocalIdentity` | Python stdlib | Active |
| Capability discovery | UDP broadcast beacon (`alpha.peer_network.discovery.UdpDiscovery`) | Python stdlib | Active on the LAN |
| Standards-based LAN discovery | Optional mDNS/DNS-SD adapter using `zeroconf` | Free optional package | Available with `peer-discovery` extra |
| Direct transport | JSON over HTTP | Python `httpx` | Active |
| Low-latency transport | JSON over WebSocket | Existing `websockets` dependency | Active fallback |
| Persistence | SQLite WAL database under `runtime_home()/peer_network` | Python stdlib | Active, restart-recoverable |
| Pairing | Explicit out-of-band pairing code, exact public ingress paths | No service | Active |
| Message delivery | Per-recipient queued/delivered/read receipts and retry loop | No broker | Active |
| GitHub bootstrap | Optional public-repository Agent Card directory | Free GitHub API; token only for writes | Opt-in |
| libp2p | External bridge seam/status only | No maintained Python implementation bundled | Not claimed as active |

`zeroconf` is intentionally optional. Install the managed extra when mDNS is
wanted:

```bash
cd backend
uv sync --extra peer-discovery
```

The UDP provider remains dependency-free if that extra is not installed. The UI
shows `available`, `running`, and the provider's real error independently; it
never renders an unconfigured provider as connected.

## Why this is the best completely free default

The practical default is **UDP discovery + direct HTTP/WebSocket + SQLite**:

1. It works on a normal home/office LAN without a broker, VPS, database, or
   paid API.
2. HTTP and WebSocket work across the Internet when the operator can expose a
   Gateway endpoint and pair the two installations.
3. SQLite and the file-backed identity need no hosted service.
4. The protocol boundary is provider-neutral, so a future libp2p, Matrix, or
   GitHub mailbox adapter can be added without changing conversation semantics.

Research checked against current upstream documentation and repositories:

- [A2A Agent Card discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)
  describes Agent Cards, well-known discovery, curated registries, and direct
  configuration. The network exposes a bounded A2A-style card at
  `/.well-known/agent-card.json` and keeps the detailed peer envelope local to
  Alpha.
- [A2A specification](https://a2a-protocol.org/latest/specification/) is the
  current protocol reference. The implementation uses the same concepts but
  labels its bounded JSON envelope `alpha-a2a/1.0`; it is not a claim of full
  conformance to every A2A SDK feature.
- [Official A2A Python SDK](https://github.com/a2aproject/a2a-python) is Apache-2.0
  and actively maintained. It is a useful future interoperability adapter, but
  this first vertical slice does not add a second SDK/runtime.
- [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf) provides
  the optional mDNS implementation. Its package is free software; operators
  remain responsible for complying with its license and local network policy.
- [libp2p documentation](https://libp2p.io/docs/) describes DHT, relays, hole
  punching, mDNS, and secure channels. The maintained reference implementations
  are primarily Go/JS/Rust; bundling an unmaintained Python implementation would
  be less reliable than the direct transport. The status endpoint reports this
  limitation honestly.

## Topology support

The conversation API validates the requested topology and stores participants
in SQLite. The UI exposes every currently supported mode:

| Mode | Meaning | Minimum participants |
|---|---|---:|
| `direct` | One-to-one | 2 |
| `one_to_many` | One sender to selected recipients | 2+ |
| `many_to_one` | Multiple senders to one target/session | 2+ |
| `many_to_many` | Shared group session | 2+ |
| `broadcast` | Fan-out to selected peers | 2+ |
| `inbox` | Server-created incoming session | 1+ |

A locally-created conversation always includes the local installation identity
in addition to the selected remote peers. This prevents a UI-created direct
conversation from accidentally becoming a three-party room. Incoming mail that
does not carry a pre-created shared conversation id lands in one installation
inbox, so many independent senders can target one Alpha (many-to-one) without
needing a central room id.

Message kinds include chat, capability/status messages, task request/result
messages, questions/answers, review requests/results, file-offer messages, and
receipts. Structured payloads are bounded; the initial text limit is 20,000
UTF-8 bytes and structured payload limit is 256 KiB.

## Connecting: the connection string

Pairing used to mean copying a bare pairing code and typing a URL into a second
field on the other machine — two actions, in an order you had to get right. It is
now **one string, copied once and pasted once**:

```text
alpha://connect?v=1&a=<agent_id>&u=<url>&w=<ws_url>&n=<name>&e=<expiry>&ep=<number>&k=<pairing_code>
```

### Two copy modes

| Mode | Contains `k=` | Where it may go |
|---|---|---|
| **Copy invite** (default) | yes | A private channel, or shown as a QR code in person |
| **Copy address only** | no | Anywhere — it is public metadata |

The UI labels which one you copied in the copy row itself, not in a tooltip.
`GET /api/peer-network/invite?include_secret=true` is **admin-gated**, because
with the secret it *is* the inbound bearer credential — the same reason
`GET /status` and `POST /pair/rotate` are.

### An invite is single use, enforced by the issuer

`ep=` is a per-installation counter. `accept_pair` records the highest epoch it
has consumed and refuses anything not strictly greater, so the first redeemer
wins and a screenshot replayed afterwards is refused by name.

It is worth recording why the obvious alternative does not work: rotating the
pairing code on redemption protects nothing. Redemption happens on the
*redeemer's* machine, while the credential that was exposed is the *issuer's*
code, and the redeemer has no authority to invalidate it. The guard therefore
lives where the exposure is.

A pairing carrying **no** epoch — the classic manual code entry — bypasses the
check entirely, so that path is unchanged.

### Expiry

`e=` is an absolute epoch second. Default lifetime **15 minutes**, ceiling 24
hours (refused with the bound named, never clamped). A claim is refused once
`now > e + 300s`; the skew tolerance is there because two laptops genuinely
disagree about the clock, and refusing an invite that expired five minutes ago on
a peer whose clock runs slow would be a support ticket, not a security control.

### Endpoint substitution is refused

A string edited in transit to point at an attacker's host yields *that host's*
Agent Card, whose `agent_id` will not match the `a=` the editor left behind, so
the pairing is refused. There is no signature to forge, because the string
carries the secret itself.

### Reading a QR code is not enabled yet

Showing a QR code **works** — the invite renders, and the recipient pastes the
text. Reading one is **not** wired up: `frontend/src/lib/qr-decode.ts` documents
why its finder-location stage is not yet reliable, and the panel disables the
camera and screenshot buttons with that reason rather than reporting "no QR code
found" for a code that is plainly on screen.

A failing round-trip test in `frontend/src/lib/qr-decode.test.mjs` is the gate.
When it passes, delete that test, flip `canDecodeQr()`, and the camera path turns
on.

## First-run setup

### 1. Configure the advertised address

The plane is **off by default**. `remote/pair`, `inbound/messages` and
`api/peer-network/ws` are mounted without a browser session, and UDP/mDNS
discovery puts this installation on the LAN whether or not anyone asks, so
"enabled" is a production exposure decision rather than a convenience toggle.
An operator opts in with `ALPHA_PEER_NETWORK_ENABLED=1`; a disabled plane
refuses the whole public ingress while local management reads keep working, and
`GET /api/peer-network/status` reports `enabled: false` so the UI can say *off*
instead of showing an empty peer list. See
[`docs/ALPHA_COLLABORATION_AUDIT.md`](ALPHA_COLLABORATION_AUDIT.md) and the
ingress throttle in `ratelimit.py`.

The advertised address below is only needed once the plane is enabled. For a
remote peer, set an address reachable from the other machine:

```bash
# PowerShell example
$env:ALPHA_PEER_NETWORK_ADVERTISED_BASE_URL = "http://192.168.1.20:8001"
$env:ALPHA_PEER_NETWORK_ENABLED = "1"
```

Useful environment variables:

| Variable | Default | Meaning |
|---|---:|---|
| `ALPHA_PEER_NETWORK_ENABLED` | `0` (**off**) | Enable UDP discovery, delivery retry loop and the public pairing/message ingress |
| `ALPHA_PEER_NETWORK_ADVERTISED_BASE_URL` | auto | Exact URL other peers should call |
| `ALPHA_PEER_NETWORK_ADVERTISED_HOST` | auto | Host used when no base URL is set |
| `ALPHA_PEER_NETWORK_BIND_HOST` | `0.0.0.0` | UDP discovery bind address |
| `ALPHA_PEER_NETWORK_DISCOVERY_PORT` | `8743` | UDP discovery port |
| `ALPHA_PEER_NETWORK_DISCOVERY_INTERVAL_SECONDS` | `8` | Beacon interval |
| `ALPHA_PEER_NETWORK_HTTP_PORT` | `8001` | Port advertised for HTTP/mDNS |
| `ALPHA_PEER_NETWORK_TIMEOUT_SECONDS` | `8` | Direct transport timeout |
| `ALPHA_PEER_NETWORK_GITHUB_REPO` | unset | Optional `owner/repo` bootstrap directory |
| `ALPHA_PEER_NETWORK_GITHUB_BRANCH` | `main` | GitHub branch |
| `ALPHA_PEER_NETWORK_GITHUB_DIRECTORY` | `.alpha-network/peers` | JSON card directory |
| `ALPHA_PEER_NETWORK_GITHUB_TOKEN` | unset | Required only for publishing cards |

A Docker/NAT deployment must publish the Gateway port and set the advertised
base URL explicitly. The bundled nginx configuration now forwards the Agent
Card paths and `/api/peer-network/ws` upgrade before its generic API/frontend
locations. If a separate proxy is used, preserve the same Upgrade/Connection
headers and the exact well-known paths.

### 2. Pair explicitly

1. Open **Alpha Network** in each installation.
2. Reveal the local pairing code and share it through a trusted channel.
3. On the sending installation, enter the remote Gateway URL and the remote
   pairing code.
4. The service fetches the remote Agent Card, calls the exact public pairing
   endpoint, validates the remote card, and stores the peer as `paired`.
5. Discovery alone never changes a peer to `paired`.

The pairing code is a bearer credential. It is stored locally with restrictive
file permissions where the OS supports them, is never placed in an Agent Card
or discovery beacon, and is never returned by the model-facing tool. Rotate it
with the UI or `POST /api/peer-network/pair/rotate`; existing peers must be
paired again.

### 3. Send

The UI can create a session and send to a direct/group topology. The Gateway
tries HTTP first, then the peer's WebSocket endpoint. If neither is reachable,
the per-recipient delivery remains `queued` and the retry loop tries again.
Every recipient has a separate receipt; a partial fan-out is not reported as a
successful group delivery.

## API surface

Authenticated local routes (normal `threads:read`/`threads:write` permissions):

```text
GET    /api/peer-network/status
GET    /api/peer-network/peers?skill=&trust=
GET    /api/peer-network/peers/{agent_id}
POST   /api/peer-network/discover
POST   /api/peer-network/github/publish (admin-only)
POST   /api/peer-network/pair
POST   /api/peer-network/pair/rotate (admin-only)
GET    /api/peer-network/invite?include_secret=&ttl_seconds=  (admin-only)
POST   /api/peer-network/invite/redeem
PATCH  /api/peer-network/peers/{agent_id}/trust
PATCH  /api/peer-network/peers/{agent_id}/auto-reply (admin-only)
POST   /api/peer-network/conversations
GET    /api/peer-network/conversations
GET    /api/peer-network/conversations/{id}
GET    /api/peer-network/conversations/{id}/messages
POST   /api/peer-network/conversations/{id}/messages
POST   /api/peer-network/messages
POST   /api/peer-network/messages/{id}/read
GET    /api/peer-network/transcripts
GET    /api/peer-network/transcripts/{id}
GET    /api/peer-network/transcripts/{id}/turns/{run_id}
GET    /api/peer-network/transcripts/{id}/export
GET    /api/peer-network/events       (SSE stream; `Last-Event-ID` replay)
```

Narrow public routes:

```text
GET    /.well-known/agent-card.json
GET    /.well-known/agent.json       (compatibility alias)
GET    /api/peer-network/card
POST   /api/peer-network/remote/pair
POST   /api/peer-network/inbound/messages
WS     /api/peer-network/ws
```

The public routes are not browser-session routes. Pairing and inbound delivery
authenticate with the network token; they are not a replacement for Gateway
login on local management routes. CSRF exemptions are exact-path only.

## Model-facing tool

The `alpha_peer_network` built-in tool supports:

- `discover`
- `list`
- `send`
- `create`

Connecting is **absent from this tool on purpose**. Pairing mints a bearer
credential and a connection string embeds one; both belong to the authenticated
API and its UI, where an admin gate and a visible confirmation stand between the
model and the credential. `set_trust`, `set_auto_reply`, `pair/rotate`, and
`build_invite`/`redeem_invite` are absent for the same reason. The model can *use*
an established peer, never create one.

It exposes ids, capabilities, trust, and bounded delivery results only. It
strips endpoints, cards, pairing codes, bearer tokens, and local filesystem
paths. Public/model callers cannot self-assert `sender_id`; the Gateway uses
the installation identity. The existing bot `peer/agent` DM grammar is bridged
through the same network service, with the local bot name retained as
attribution/payload data.

## Storage and lifecycle

The default database is:

```text
${ALPHA_HOME}/peer_network/network.sqlite3
${ALPHA_HOME}/peer_network/identity.json
```

The database contains peer cards, conversation participants, message envelopes,
and per-recipient delivery receipts. It does not contain public-repository
credentials or model secrets. The network is currently **installation-scoped**,
which is appropriate for the normal single-operator Alpha process. A future
multi-tenant deployment must introduce a server-resolved owner key rather than
trusting a request field.

SQLite state is durable across restarts for one installation. It is not a
cross-process lease/exactly-once coordinator. Do not run multiple Gateway
workers against one local SQLite peer database and describe delivery as exactly
once. Use one network owner/process or add a shared SQL/lease adapter before
multi-worker deployment.

## Security and NAT limitations

- Discovery packets and Agent Cards are untrusted public metadata.
- Pairing is explicit and uses a high-entropy out-of-band code.
- Public ingress rejects missing/unknown sender tokens.
- Endpoint validation blocks unsupported schemes, embedded credentials,
  multicast/unspecified addresses, and common cloud metadata addresses.
- The service does not accept arbitrary model-supplied filesystem paths or URLs
  as message payloads.
- Direct Internet discovery still needs a reachable endpoint or a rendezvous
  path. NAT traversal, relay services, and guaranteed global discovery are not
  silently simulated.
- GitHub bootstrap publishes only the Agent Card. It is not a public mailbox;
  pairing credentials and message bodies must never be committed to a public
  repository.
- The optional libp2p status is not a fake active transport. A future external
  bridge must authenticate peers, preserve idempotency, and expose the same
  receipt semantics before it is enabled.

## Verification

Backend:

```bash
cd backend
.venv\\Scripts\\python.exe -m pytest tests/test_peer_network.py -q
.venv\\Scripts\\python.exe -m pytest tests/test_bots_dm.py -q
.venv\\Scripts\\python.exe -m pytest tests/test_feature_manifest_wiring.py -q
.venv\\Scripts\\python.exe scripts/check_tool_schemas.py
```

Frontend:

```bash
cd frontend
pnpm typecheck
pnpm test
```

The UI is a separate **Alpha Network** workspace tab. It has honest loading,
empty, error, provider-state, pairing, topology, and per-recipient delivery
states. It does not turn a failed Gateway request into an empty peer list.

A second workspace tab, **External Alpha**, reads the cross-installation
*history* that Alpha Network does not show. See
[External Alpha transcripts](#external-alpha-transcripts) below.

## External Alpha transcripts

Alpha Network is the control surface (discover, pair, send). **External Alpha**
is the read surface: every conversation this installation exchanged with another
Alpha, including the local Agent turn that answered each remote message.

### Why a second tab

The two sides of a cross-installation conversation live in two stores that have
no join key the UI can use:

| Side | Stored by | Contains |
| --- | --- | --- |
| Remote envelope | `peer_network/network.sqlite3` | what a peer sent, per-recipient receipts |
| Local Agent turn | the run event store | what this installation replied, tool calls, timings, tokens |

`RunCreateRequest.metadata.peer_network` stamps `message_id` /
`conversation_id` / `peer_agent_id` on the run, so the join exists;
`alpha.peer_network.transcript` owns the projection and the Gateway exposes it
under `/api/peer-network/transcripts/*`.

### The local reply is already durable

A peer turn is dispatched with `hide_from_ui` on its triggering human message
(`app.gateway.services.launch_peer_network_agent_turn`), which keeps the framed
untrusted prompt out of the ordinary chat feed. That flag does **not** suppress
the run's events: `RunJournal.on_llm_end` persists AI replies and tool results
unconditionally. The local half of every peer turn is therefore already stored,
and this feature only has to find and project it.

### Why these routes omit `owner_check`

The transcript routes use `@require_permission("threads", "read")` **without**
`owner_check=True`, which would otherwise be mandatory for a run-detail route.
A peer turn runs on `peer_thread_id(peer_agent_id)` owned by
`NETWORK_OWNER = "installation"` — a constant, never a session user id — so
`owner_check` resolves the caller against a thread they do not own and would
404 the operator who owns this very installation, making the history permanently
unreadable.

`_assert_peer_network_scope` is the deliberate substitute: an explicit allow-check
that admits an authenticated local caller with `threads:read` or the internal
system role, and refuses anything else. It fails closed. This is documented at
the call site so the missing decorator reads as a decision rather than an
oversight.

### What is never exposed

Responses are built by an **allowlist**, not a filter — the peer row is readable
with `url`, `websocket_url`, `outbound_token`, `token_hash` and the full Agent
Card, and none of them reach a response body. An allowlist means a field added to
the peer row later cannot leak by omission. This mirrors
`_public_peer` in `alpha.tools.builtins.peer_network_tool`.

Peer text is untrusted data from another machine. It is rendered as escaped text
and is never injected as raw HTML, never executed, and never promoted into a
prompt.

### Honesty rules the tab enforces

- **Two sides, never merged.** A `peer_message` entry and a `local_reply` entry
  render and label differently. A merged stream would let a reader believe a
  remote peer said something the local Agent said.
- **"Disabled" is not "no traffic".** The plane defaults **off**
  (`ALPHA_PEER_NETWORK_ENABLED`); an off plane says so instead of showing an
  empty list.
- **Bounded is not complete.** `truncated` and `events_truncated` are rendered as
  warnings, and delivery receipts stay per-recipient so a partial fan-out never
  renders as a successful group send.
- **A failed request is never an empty list.**

### API surface

```text
GET    /api/peer-network/transcripts
GET    /api/peer-network/transcripts/search?q=&limit=&conversation_id=&direction=
GET    /api/peer-network/transcripts/analytics
GET    /api/peer-network/transcripts/{conversation_id}
GET    /api/peer-network/transcripts/{conversation_id}/turns/{run_id}
GET    /api/peer-network/transcripts/{conversation_id}/turns/{run_id}/trace
GET    /api/peer-network/transcripts/{conversation_id}/export
```

`search` and `analytics` are declared **before** the `{conversation_id}`
catch-all. Unlike the existing `/conversations/{conversation_id}` route, these two
literals share a path segment with a parameterised route, so Starlette would
answer them with "Conversation 'search' not found" if the order slipped — the same
trap as `skills/{skill_name}` and `workflows/{workflow_id}`.

Read-only. The only write routes in this plane stay where they were, and the
admin gates on `auto-reply`, `pair/rotate` and `github/publish` are unchanged —
a peer still cannot grant itself the ability to spend this installation's
budget.

### Search (FTS5, free)

Peer message bodies are searchable across the whole installation's history using
SQLite's built-in FTS5 — no external search service, no extra dependency.

| Property | Behaviour |
|---|---|
| Index type | `fts5`, external-content over `messages.rowid`, `unicode61 remove_diacritics 2` |
| Storage | No second copy of the body; the index resolves through `messages` |
| Ranking | `bm25`, most relevant first |
| Fallback | A bounded, escaped `LIKE` scan when the interpreter has no FTS5 |

Four mechanisms make this correct, and each one is load-bearing:

- **Triggers.** An external-content FTS table is *not* self-populating. A fresh
  `CREATE VIRTUAL TABLE` over an existing table searches nothing, and later
  `INSERT`s are equally invisible — so without insert/update/delete triggers
  search would return nothing forever while looking completely healthy.
- **A one-time rebuild.** Triggers only cover rows written after they exist, so a
  database that already has messages needs one `rebuild` pass.
- **That rebuild commits on its own.** `rebuild` is not ordinary DML: it populates
  an in-memory index that only joins the transaction once it is read, so a
  rollback leaves the shadow table reporting a correct row count while `MATCH`
  finds nothing.
- **The backfill guard asks a question rather than counting.** `SELECT count(*)`
  on an external-content FTS table reads the *shadow* tables, so it reports a
  plausible number for an empty live index. The guard instead runs a probe
  `MATCH`; no hits means nothing is indexed, so the rebuild fires. That also makes
  startup self-healing for a damaged index.

Diacritics fold, so a peer writing `résumé` is findable by an operator typing
`resume`. Malformed FTS expressions (an unbalanced quote, a bare operator) fall
back to the scan rather than raising, and `%`/`_` are escaped in that fallback
so a wildcard cannot return the whole mailbox.

When FTS5 is unavailable the response carries `fts_available: false` and the UI
says "substring scan" rather than implying ranked full-text search.

### Analytics

`GET /transcripts/analytics` returns counted histograms — by direction, status,
topology and kind — plus the effective retention window and FTS availability.
Every number is counted in SQL from the rows that exist. Nothing is inferred from
a peer's *declared* card: a peer advertising fifty kinds of work is counted only
against what actually arrived.

### Behaviour-trace drill-down

`GET /transcripts/{id}/turns/{run_id}/trace` returns the
`alpha.observability` envelopes for one turn — per-layer spans, tool outcomes,
error codes and subagent attribution. Trace payloads are already redacted by the
writer, so they pass through unchanged.

The response carries an explicit `note`, because an empty trace list has a benign
cause: a turn whose writer emitted no envelopes. The UI renders that note instead
of an invented "nothing happened".

### Live events

`GET /api/peer-network/events` now carries a monotonic SSE `id` per event and
honours `Last-Event-ID` on reconnect (header or `?last_event_id=`). A
reconnecting client whose cursor predates the retained ring receives an explicit
`stream.reset` telling it to re-fetch the REST snapshot, and a subscriber that
falls behind receives `stream.overflow` with the dropped count. A silent
dropped event would render a continuous timeline the client never received.

The replay ring is **process-local and bounded** (last 500 events). It is a
reconnect convenience, not a durable event log — the SQLite store is the
authority. This is the same single-process honesty the rest of this plane keeps.

### Retention

Delivered/read history is pruned after
`ALPHA_PEER_NETWORK_RETENTION_DAYS` (default **90**, floored at **7**). The floor
is a safety floor: `0` would otherwise delete an operator's entire
cross-installation transcript on the next tick.

Two things are deliberately **not** pruned:

- **Undelivered messages.** A `queued` or `failed` outbound row is still awaiting
  delivery by the retry loop; deleting it would silently drop a message the
  operator believes was sent.
- **Conversations.** An empty conversation still records which peers ever talked,
  so removing it would erase history instead of bounding it.

Delivery receipts cascade with their message, so a receipt can never outlive the
message it describes. The prune runs on the existing retry loop's cadence rather
than adding a second background task.
