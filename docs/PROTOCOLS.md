# Protocol versions and interop posture

**Verified on:** 2026-09-29 · **Worktree:** `alpha-proto` (branch `agent/proto`)

This document records three things: which protocol versions Alpha actually
speaks, what was checked to establish that, and what Alpha deliberately does not
speak yet.

Every external claim carries its source and the date it was checked. Protocol
dates move, and a wrong date copied from a brief into a document is worse than
no date — so "verified" here always means *read from the live source that day*,
never recalled.

The single most important finding is in §2: **Alpha was speaking MCP
`2025-11-25` while the specification was at `2026-07-28`, and nothing in the
repository recorded that.** That is now a declared, tested contract.

---

## 1. Verification table

Four claims came in from the research brief. All were checked; two hold, one
holds with a correction, and one is wrong about this codebase. Everything else
the brief asserted about A2A, ACP and AG-UI is checked too.

| # | Claim | Verdict | What is actually true | Source |
| --- | --- | --- | --- | --- |
| 1 | Current MCP spec is `2026-07-28`, stateless core, no handshake | **HOLDS** | The current revision is `2026-07-28`, and it *is* stateless: the `initialize` handshake and protocol-level sessions are replaced by a per-request `MCP-Protocol-Version` value, a mandatory `server/discover` RPC, and no `Mcp-Session-Id`. | [spec](https://modelcontextprotocol.io/specification/2026-07-28) ("Stateless, self-contained requests / Per-request capability negotiation"); [versioning](https://modelcontextprotocol.io/docs/2026-07-28/learn/versioning) ("The **current** protocol version is **2026-07-28**"). Fetched 2026-09-29. |
| 2 | Alpha's `mcp/client.py` has zero protocol-version references | **HOLDS** | 80 lines, builds adapter parameter dicts only. No version string anywhere in `alpha/mcp/` — grep for `protocol_version` / `LATEST_PROTOCOL` across the package returns nothing. | `backend/packages/harness/alpha/mcp/client.py`; repo grep 2026-09-29. |
| 3 | Correctness rides entirely on an unbounded `langchain-mcp-adapters>=0.2.2` | **HOLDS, and it understates the problem** | The floor is unbounded — but the real finding is that Alpha does **not** inherit its wire version from the adapter at all. `alpha/mcp/session_pool.py:50-52` imports `mcp.ClientSession` and `mcp.types` directly, and `alpha/mcp/tools.py:237,413,490` imports `mcp.types` directly. The `mcp` SDK is a **transitive** dependency with **no direct declaration anywhere in the repo**, resolved by the lock to `1.28.1`. The protocol version was decided by a package Alpha never named. | `session_pool.py:50-52`, `tools.py:237,413,490`; `backend/packages/harness/pyproject.toml:26`; `backend/uv.lock:3004-3005`. |
| 4 | Silent-drift risk | **HOLDS — it is not hypothetical, it has already happened** | The resolved SDK speaks `2025-11-25`. The specification is at `2026-07-28`. Alpha was one revision behind at the moment of writing, with nothing in code, config, or tests recording it. See §2. | §2 below; `mcp/types.py` at tag `v1.28.1`. |
| 5 | A2A is v1.0 stable, Linux Foundation, 150+ organisations | **HOLDS** | A2A v1.0 released **2026-03-12** (v1.0.1 followed in May 2026); the project is hosted by the Linux Foundation; the one-year release dated **2026-04-09** states 150+ organisations including AWS, Cisco, Google, IBM, Microsoft, Salesforce, SAP and ServiceNow. | [LF release, 2026-04-09](https://www.linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year); [v1.0.0 tag](https://github.com/a2aproject/A2A/releases/tag/v1.0.0). |
| 6 | Native in "Microsoft Agent Framework 1.0" and Google ADK | **UNVERIFIED as worded; substance holds** | The LF release confirms first-party integration across Google, Microsoft and AWS platforms, and LangGraph and CrewAI both ship A2A integrations. I did **not** find support for the specific product name "Microsoft Agent Framework 1.0" — the release names *Microsoft Copilot Studio and Foundry*. Treat the product name as unverified; the claim's substance holds. | LF release 2026-04-09; [LangGraph A2A](https://docs.langchain.com/langsmith/server-a2a). |
| 7 | Alpha's peer network is A2A-*shaped* but wire-incompatible, via an `alpha-a2a` envelope | **HOLDS** | `peer_network/models.py:18-21` declares `PROTOCOL = "alpha-a2a"`, `CARD_TYPE = "application/alpha-peer-card+json"`, `ENVELOPE_MEDIA_TYPE = "application/alpha-a2a+json"`. A custom media type is by construction not the A2A media type, so no A2A client can parse an Alpha envelope. The module docstring already disclaimed full A2A; it now says so precisely. | `backend/packages/harness/alpha/peer_network/models.py`. |
| 8 | "'ACP implemented' means Zed's editor protocol, not a generic agent-interop ACP" | **WRONG as worded** | Alpha depends on the PyPI distribution **`agent-client-protocol`**, whose own summary is *"A Python implement of Agent Client Protocol (ACP, by Zed Industries)"*. `invoke_acp_agent_tool.py:106,125` imports `acp.RequestPermissionResponse` / `DeniedOutcome` from it. So it **is** the Zed-originated ACP, not some lesser editor-only thing. The brief's framing understates what is implemented. | [PyPI `agent-client-protocol`](https://pypi.org/pypi/agent-client-protocol/json); `backend/uv.lock:22-28` (locked `0.9.0`, declared `>=0.4.0`); `alpha/tools/builtins/invoke_acp_agent_tool.py:106,125`. |
| 9 | A sibling agent found a *different* ACP circulating (Agent Client Protocol, JetBrains registry) | **SAME PROTOCOL** | Not a different protocol. ACP was authored at Zed Industries and is stewarded by the `agentclientprotocol` GitHub organisation (`agentclientprotocol.github.io`), with implementations from several vendors. Both descriptions name one protocol; they differ only in whose registry was noticed. | [PyPI project URLs](https://pypi.org/pypi/agent-client-protocol/json) → `github.com/agentclientprotocol/python-sdk`. |
| 10 | AG-UI is absent from the codebase | **HOLDS** | No `ag-ui`, `ag_ui`, `AGUI` or `agui` token under `backend/packages`, `backend/app`, `backend/tests`, `frontend/src`, `docs`, `config.example.yaml`, or either `pyproject.toml`. (Only this document matches, because it names the protocol.) | Repo-wide grep, 2026-09-29. |
| 11 | "AG-UI … the spec was still moving. Establish whether waiting is still defensible." | **PREMISE NO LONGER HOLDS** | AG-UI has a published **1.0** with a *normative behavioural specification* in BCP-14 language, an authoritative JSON Schema (`/spec/1.0/schema.json`), two specified transports (HTTP+SSE, HTTP+Protobuf), first-party TypeScript/Python/.NET SDKs, and explicit versioning and compatibility rules. Waiting is no longer justified by "the spec is moving" — there is a stable target. | [spec](https://docs.ag-ui.com/spec/draft) (served at `/spec/1.0/`, titled "…— 1.0"); [changelog](https://docs.ag-ui.com/spec/1.0/changelog). Checked 2026-09-29. |

One claim was right for a fragile reason. The brief's "`2026-07-28`, stateless,
no handshake" is correct *today* and is only true from that revision onward —
which is exactly why the version Alpha actually speaks had to be established
separately rather than assumed from the headline.

---

## 2. What version does Alpha speak today?

**`2025-11-25`.** The current specification is `2026-07-28`. Alpha is one
revision behind, and had been silently so.

Established from the SDK's runtime values, not from a lockfile, because a
lockfile records one resolution made once and the installed package is what
actually puts bytes on the wire:

```
$ .venv/Scripts/python.exe -c "import mcp.types as t; print(t.LATEST_PROTOCOL_VERSION)"
2025-11-25
$ .venv/Scripts/python.exe -c "from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS as S; print(S)"
['2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25']
```

with `mcp` resolved to `1.28.1` and `langchain-mcp-adapters` to `0.2.2`.

Two further facts make this a structural problem rather than a stale constant:

- Alpha **imports `mcp` directly** (`session_pool.py:50-52`, `tools.py:237,413,490`)
  but **never declares it**. `packages/harness/pyproject.toml:26` names only
  `langchain-mcp-adapters>=0.2.2`; the `mcp` SDK arrives transitively. Alpha's
  wire version is therefore controlled by a package it does not name, at a
  version it does not pin.
- `langchain-mcp-adapters` delegates negotiation entirely to `mcp`, so bumping the
  adapter changes nothing about the wire version. The adapter's own floor cannot
  protect the protocol.

### Why `2025-11-25` and not `2026-07-28`

`2026-07-28` is not a version bump away. It removed the handshake that Alpha's MCP
layer is structurally built on: `MCPSessionPool._run_session` blocks on
`session.initialize()` and promotes a session into the pool *only* once that
returns, and the pool's LRU and lease lifecycle is defined over the handshake.
Migrating to the stateless core means redesigning the session pool — new session
identity, new transport semantics, no `Mcp-Session-Id` to key a lease on.

Alpha therefore stays on the handshake family **and says so**, rather than
inheriting a version by accident. That is the entire point of §3.

---

## 3. The protocol-version contract

### Where it is declared

**`backend/packages/harness/alpha/mcp/protocol_version.py`** — a code declaration,
not a `config.yaml` key.

This is a deliberate departure from the assignment's suggestion to follow the
config house pattern, and the reasoning is worth stating because the repo is
strict about config being the single source of truth.

`config.yaml` is the single source of truth for **operator choices**. A protocol
version is not an operator choice — it is a property of the code and of the SDK
that code imports. Making it settable would let an operator declare a revision
Alpha cannot speak, at which point the config file becomes a source of a
*falsehood* rather than of truth. That is the exact failure mode the contract
exists to remove, and this repository's own precedent cuts the same way: the
second model file was deleted rather than deprecated, precisely because a config
source that could be wrong was worse than no config source.

Three values are declared:

| Constant | Value | Meaning |
| --- | --- | --- |
| `MINIMUM_MCP_PROTOCOL_VERSION` | `2025-06-18` | The floor. Justified by two features Alpha *requires*, not prefers: `tools.py` reads `CallToolResult.structuredContent` and branches on `mcp.types.ResourceLink`, and `mcp/tasks/ordinary.py:112-114` treats a task tool returning no `structuredContent` as a protocol error. A server below the floor connects and is then misread. |
| `DECLARED_MCP_PROTOCOL_VERSION` | `2025-11-25` | What Alpha prefers and asks for; matches the installed SDK's `LATEST_PROTOCOL_VERSION`. |
| `CURRENT_SPEC_MCP_PROTOCOL_VERSION` | `2026-07-28` | Where the specification is. Alpha does **not** speak it. Recorded so the gap is a fact in the repo, and so drift in *either* direction is detectable. |

`SUPPORTED_MCP_PROTOCOL_VERSIONS` is the accepted set, and it deliberately
**starts at the floor** rather than listing every revision the SDK can decode.
The SDK can parse `2024-11-05`; Alpha must not accept it, for the
`structuredContent` reason above. Admitting it would mean admitting servers
Alpha answers incorrectly for.

### Refuse, warn, or adapt — the decision is **refuse (fail-closed)**

An unsupported negotiated version raises `McpProtocolVersionError` and the session
is abandoned. It is enforced at **both** places Alpha talks to a server:

- `session_pool._run_session`, **after** `initialize()` returns and **before** the
  commit point into `_entries`. Placing it before promotion is the important part:
  a refused server never enters the pool, so no caller can ever be handed a
  session speaking a language this release cannot read.
- `task_tool_caller`, on the ephemeral HTTP/SSE path, before `call_tool` is
  issued. An ephemeral session is not a lesser privilege — it runs the same
  result-parsing code, so gating only the pooled route would leave the same
  hazard open on the other path.

**Why refuse, and not warn-and-continue.** Warn-and-continue is the tempting
choice because it cannot break a working setup. It is wrong here for a specific
reason rather than a general one: *the failure is not a crash.* Past the gate,
Alpha's parsing of a tool result is written against one revision's shapes.
A newer server answering with a reshaped payload does not produce an error —
it produces a tool result Alpha misreads, which reaches the model as wrong tool
output, and reaches the user as an agent that quietly got something wrong. A
refusal is loud, names the server and the version, and is actionable at the
`extensions_config.json` entry that caused it. Given that Alpha's contribution
rules require fail-closed behaviour on safety-relevant boundaries, and that MCP
servers are arbitrary third-party code reached over a network, this is a
safety-relevant boundary.

**Why not adapt.** Adapting would mean version-conditional parsing in
`tools.py` — a second, permanently maintained code path selected by a wire
string, for a feature no operator has asked for. That trades a loud, rare
failure for a quiet, permanent maintenance burden. Refuse now; adapt when there
is a real `2026-07-28` server to talk to and the session-pool redesign is on the
agenda.

Note what is *not* refused: a server negotiating any revision in the supported
set connects normally. The gate is not a blanket refusal, and
`test_supported_version_admits_the_session` pins that so it cannot drift into one.

### The test, and proof it bites

**`backend/tests/test_mcp_protocol_version.py`** (19 tests). The load-bearing one
is `test_declared_version_matches_pinned_expectation`: it reads the real constant
out of the module and compares it against a literal written in the test file, so
editing one without the other fails CI and the message says which moved. That is
what separates it from asserting a constant equals itself.

`test_declared_version_agrees_with_installed_sdk` is the drift detector: it
compares Alpha's declaration against the resolved SDK's `LATEST_PROTOCOL_VERSION`
rather than against `uv.lock`, so a `uv sync` that moves `mcp` fails in CI rather
than in production.

**Proof the guard bites** — changing the declared value to `2026-07-28` and
re-running produced:

```
FAILED TestPinnedExpectations::test_declared_version_matches_pinned_expectation
FAILED TestPinnedExpectations::test_declared_version_is_inside_the_supported_set
FAILED TestAgreesWithInstalledSdk::test_declared_version_agrees_with_installed_sdk
FAILED TestPooledSessionRefusesBeforeCommitting::test_supported_version_admits_the_session
4 failed, 15 passed
```

Four independent tests caught one edit. The value was restored and the suite is
green again.

---

## 4. The dependency bound — **not changed**, and here is why

**Recommendation: leave `langchain-mcp-adapters>=0.2.2` unbounded. I could not
determine a real compatible range, and an invented upper pin is worse than no
pin because it can block a security fix.**

What I established:

- Latest `langchain-mcp-adapters` is **0.3.2** (uploaded 2026-08-06). 0.3.0, 0.3.1
  and 0.3.2 are all post-`2026-07-28`. Its own declared dependency moved from
  `mcp>=1.9.2` (0.2.2) to `mcp<2.0.0,>=1.24.0` (0.3.2) — i.e. upstream itself
  capped the SDK below the release carrying the new protocol.
- `mcp` 2.0.0 shipped **2026-07-28**, the same day as the specification, and 2.2.0
  is current. Its `mcp_types.version` module splits the world into
  `HANDSHAKE_PROTOCOL_VERSIONS` (`…` through `2025-11-25`) and
  `MODERN_PROTOCOL_VERSIONS` (`2026-07-28` only).

So the *meaningful* boundary is **`mcp<2`**, because `mcp` 2.x is the stateless
core and Alpha's session pool is handshake-structured. But that boundary is the
adapter's to express, and 0.3.2 already expresses it. Alpha declaring
`langchain-mcp-adapters<0.4` would pin around a series whose end is an artefact
of a library's release cadence, not a compatibility fact, and would silently block
a 0.3.3 security release.

**What I did instead**, which is the diagnostic: `test_declared_version_agrees_with_installed_sdk`
and `test_every_supported_version_is_known_to_the_installed_sdk` compare Alpha's
declaration against the resolved SDK at test time. If a future resolution pulls in
`mcp` 2.x, those tests fail and the reviewer sees exactly why — instead of the
protocol silently changing under a green build.

**One recommendation I am not making but will record:** declare `mcp` directly in
`packages/harness/pyproject.toml`. Alpha imports `mcp` in three modules and
depends on it transitively today. That is a real gap, but it is a dependency
declaration in a file outside this task's ownership, and it interacts with the
managed-extension install path. Flagging rather than doing.

---

## 5. A2A assessment — written, not built

**What Alpha's peer network is:** `alpha-a2a`-shaped and honestly described as
such. Same ideas (Agent Card, Message, Task-like kinds), custom media types, no
A2A SDK.

**What a real v1.0 adapter would require:**

| Area | Today (`alpha-a2a`) | A2A v1.0 requires | Size |
| --- | --- | --- | --- |
| Envelope | `application/alpha-a2a+json`, own `kind` enum | JSON-RPC 2.0 methods (`message/send`, `tasks/send`, `tasks/get`, `tasks/cancel`), request/response correlation | **M** |
| Agent Card | `PeerCard`, Alpha fields + URLs | Spec card: name, description, version, skills, capabilities, `defaultInputModes`/`defaultOutputModes`, plus **signed cards** in v1.0 | **S** |
| Auth | Out-of-band pairing code + bearer token | v1.0 adds signed cards and hardened auth; a public internet-facing adapter needs a real trust story | **M** |
| Task/message model | `task_request` / `task_result` kinds | `Task` (id, contextId, status, artifacts, history), `Message` (role, parts), `Part` (text/file/data) | **L** |
| Discovery | UDP beacon + optional GitHub rendezvous | `/.well-known/agent.json` and/or a registry | **S** |

**Total: L.** The task model is the bulk of it — Alpha's flat `kind` enum maps
loosely onto A2A's `Task`/`Message`/`Part` structure, and that is a data-model
rewrite, not a mapping.

**Should Alpha implement it?** *Not now, and the deciding factor is not
technical.* Two things point the same way:

1. **There is no second party yet.** Interop only pays when someone else is on
   the other end. Alpha's peer network targets Alpha-to-Alpha, where
   `alpha-a2a` is already the better protocol — it carries Alpha's trust model
   (pairing codes, installation-scoped identity) and its task kinds, none of
   which A2A expresses.
2. **The honest cost of a second protocol is permanent.** Not the L to build —
   the L plus a standing obligation to track a specification Alpha does not
   otherwise depend on, for a capability no user has asked for.

If it is ever built, build it **alongside** `alpha-a2a`, behind its own media
type and its own routes, not as a replacement. The two serve genuinely different
trust models: installation-scoped local trust versus zero-trust public
infrastructure. Replacing one with the other would be a downgrade for the case
Alpha actually has.

**Not started, deliberately.** An A2A adapter is far larger than this task, needs
a product decision, and this task's files did not own the peer network's routing
or transport.

---

## 6. ACP disambiguation

Added to the `peer_network/models.py` module docstring, because the peer network
is the worst possible place for the ambiguity — someone reading about agent-to-agent
traffic and hitting "ACP" elsewhere in the codebase should not have to guess.

The verified position, correcting the brief:

- Alpha depends on PyPI `agent-client-protocol`, self-described as *"A Python
  implement of Agent Client Protocol (ACP, by Zed Industries)"*, locked at
  `0.9.0`. This **is** the Zed-originated ACP.
- It is a *client-to-agent* protocol (JSON-RPC over stdio) for driving a coding
  agent from an editor or host. It is **not** an agent-to-agent interoperability
  protocol.
- "ACP" appearing under both Zed and other vendors is one protocol with several
  implementations, not several protocols with one acronym. The note says so.

The note names all three protocols that collide — MCP (host→tools), A2A
(agent→agent), ACP (editor→agent) — with a pointer from each to the others.

---

## 7. AG-UI — waiting is no longer defensible

The brief asked whether waiting was still defensible. **It is not, on the stated
reason.** AG-UI 1.0 exists with a normative behavioural specification and stable
transports (checked 2026-09-29, §1 row 11). "The spec is still moving" no longer
describes it.

That said, adopting it is **not** a small change and not obviously this system's
problem:

- AG-UI governs *agent ↔ user-facing application* streaming. Alpha's frontend
  talks to its own Gateway over its own SSE contract. Replacing that means a new
  event-translation layer between Alpha's run stream and AG-UI's typed events,
  plus frontend changes — and Alpha's run-event contract is owned elsewhere in
  this repository.
- The 1.0 changelog notes are worth reading before committing: 1.0 renames
  `THINKING_*` to a reasoning family, moves subagent attribution to
  `subagentRunId`, and adds `subagentRunId` on events — which overlaps directly
  with Alpha's existing subagent and run-event models.

**Recommendation:** treat AG-UI 1.0 as a real option for the next frontend
contract revision, and revisit it as part of that work rather than as an
independent protocol retrofit. Nothing about it requires a decision now, and
nothing about it should block the MCP work above.

---

## 8. What I deliberately did not do

- **Did not add an MCP protocol version to `config.yaml`.** Rejected deliberately
  and argued above: an operator-settable protocol version is a config source that
  can state a falsehood.
- **Did not pin an upper bound on `langchain-mcp-adapters`.** Could not determine
  a real compatible range; an invented cap can block a security fix. Replaced
  with an SDK-drift diagnostic instead.
- **Did not declare a direct dependency on `mcp`** in the harness `pyproject.toml`.
  It is a real gap — Alpha imports `mcp` in three modules and never names it —
  but that file was outside this task's ownership.
- **Did not build the A2A adapter.** Assessed and sized, per instruction.
- **Did not adopt AG-UI.** Out of scope, and it overlaps a run-event contract
  owned by another agent.
- **Did not touch `config.yaml`, `config.example.yaml`, routers, `models/**`,
  `agents/**`, or `runtime/**`.**
- **Did not run the application stack or bind any port**, and did not commit.
- **Did not migrate Alpha to the `2026-07-28` stateless core.** That is a
  session-pool redesign and a separate, larger decision.

### Note on the worktree

A concurrent stale process repeatedly re-added a previous agent's partial edits
to this worktree during the session (an `McpConfig` with `2026-07-28` defaulted
and a `strict_version: "warn"` escape hatch, a raw pre-flight HTTP `initialize`
probe, and a test file asserting the same unverified version). Those were
reverted each time and are **not** in the final diff. Had they survived they
would have declared a version Alpha cannot speak, and the "probe" would have
double-connected to every MCP server before the adapter did.

---

## 9. Verification performed

All commands run from `backend/` in this worktree, `.venv` built with
`uv sync --frozen`, with `PYTHONPATH="."`. `pytest -p no:randomly` throughout so
ordering could not mask a result.

| Check | Result |
| --- | --- |
| `pytest tests/test_mcp_protocol_version.py` | **19 passed** |
| `pytest tests/test_mcp_session_pool.py` | **66 passed** (includes the compensating gate test) |
| `pytest tests/test_mcp_task_tool_caller.py` | **15 passed** |
| `pytest tests/test_mcp_context_headers.py tests/test_mcp_header_names.py` | **85 passed** |
| `pytest` (remaining MCP files) + `tests/test_peer_network.py` | **151 passed** |
| `pytest tests/test_harness_boundary.py` (touched the harness) | **passed** — included in the 101-test run below |
| `pytest tests/test_harness_boundary.py test_mcp_protocol_version.py test_mcp_session_pool.py test_mcp_task_tool_caller.py` | **101 passed** |
| `ruff format --check` on all 9 changed files | **9 already formatted** |
| **Guard bites** | Changed `DECLARED_MCP_PROTOCOL_VERSION` to `2026-07-28` → **4 failed, 15 passed** across the pinned-expectation, supported-set, SDK-agreement and pooled-session tests. Restored → green. |

### Test doubles that had to change, and why

Four MCP test files needed edits, and the reason is worth recording rather than
hiding: their session doubles were bare `AsyncMock`s, so `initialize()` returned
a Mock whose `protocolVersion` was another Mock. The gate refused them — which
is the gate working correctly, since a real `ClientSession.initialize()` always
returns an `InitializeResult` with a **required** `protocolVersion` field.

- `test_mcp_task_tool_caller.py`, `test_mcp_context_headers.py`,
  `test_mcp_header_names.py`: the doubles now return a declared version via a
  shared `_initialized()` helper. These exercise the ephemeral HTTP/SSE path, so
  the fix belongs in the double.
- `test_mcp_session_pool.py`: ~39 doubles across promotion, eviction and teardown
  tests. Rather than edit all of them (adding noise to tests about something else,
  and asserting the same thing 39 times), the module neutralises the gate via an
  autouse fixture, and `test_pool_calls_the_protocol_gate` asserts that
  `_run_session` still consults it. **A neutralised gate is not a removed gate** —
  that compensating test is what makes the fixture safe, and it fails if the
  gate is ever taken out of the pool.

### Two real bugs the tests caught in the new module

Both were found by the tests written alongside the code, not by review:

1. The supported set initially listed `2024-11-05` and `2025-03-26` — revisions
   *below* the stated floor. The set contradicted the floor it was supposed to
   agree with. Fixed by making the set start at the floor, which is the honest
   statement: Alpha cannot correctly serve results from those revisions.
2. The "is this peer too old or too new?" message compared a position in one
   list against an index into a *different* list, so `2024-11-05` reported
   itself as **"newer than the newest revision Alpha speaks"** — the opposite of
   the truth, and actively misleading to the operator it exists to help.
   Fixed by comparing positions within one coordinate system.

A third latent bug — a peer answering `"zzz"` sorting above every date-shaped
revision, and so being reported as being in the future — was avoided by
designing the comparison against an enumerated list rather than by string
comparison, and `test_unknown_string_is_not_reported_as_being_in_the_future`
pins it.

