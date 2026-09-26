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

## First-run setup

### 1. Configure the advertised address

The default is suitable for two Gateways on one LAN. For a remote peer, set an
address reachable from the other machine:

```bash
# PowerShell example
$env:ALPHA_PEER_NETWORK_ADVERTISED_BASE_URL = "http://192.168.1.20:8001"
$env:ALPHA_PEER_NETWORK_ENABLED = "1"
```

Useful environment variables:

| Variable | Default | Meaning |
|---|---:|---|
| `ALPHA_PEER_NETWORK_ENABLED` | `1` | Enable UDP discovery and delivery retry loop |
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
POST   /api/peer-network/discover
POST   /api/peer-network/github/publish (admin-only)
POST   /api/peer-network/pair
POST   /api/peer-network/pair/rotate
PATCH  /api/peer-network/peers/{agent_id}/trust
POST   /api/peer-network/conversations
GET    /api/peer-network/conversations
GET    /api/peer-network/conversations/{id}
GET    /api/peer-network/conversations/{id}/messages
POST   /api/peer-network/conversations/{id}/messages
POST   /api/peer-network/messages
POST   /api/peer-network/messages/{id}/read
GET    /api/peer-network/events       (bounded SSE event stream)
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

It exposes ids, capabilities, trust, and bounded delivery results only. It
strips endpoints, cards, pairing codes, bearer tokens, and local filesystem
paths. Public/model callers cannot self-assert `sender_id`; the Gateway uses
the installation identity. The existing bot `peer/agent` DM grammar is bridged
through the same network service, with the local bot name retained as
attribution/payload data.

## Storage and lifecycle

The default database is:

```text
${AGENT_WORKSPACE_HOME}/peer_network/network.sqlite3
${AGENT_WORKSPACE_HOME}/peer_network/identity.json
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
