# Alpha peer network module

`alpha.peer_network` is an installation-scoped, local-first Alpha-to-Alpha
plane. `service.py` is the lifecycle/policy owner; `storage.py` is synchronous
SQLite and async callers must use `asyncio.to_thread`; `transport.py` is
HTTP-first with WebSocket fallback; `discovery.py` owns UDP and optional mDNS;
`github.py` publishes/discovers public Agent Cards only; `transcript.py` is the
read-only projection that joins the peer store to the run event store for the
External Alpha tab.

Discovery is untrusted. Pairing is explicit, high-entropy, and out-of-band.
Never put pairing codes, bearer tokens, or private message bodies in a public
Agent Card, discovery beacon, GitHub repository, model tool result, or log.
The public sender id is the authenticated installation identity; public callers
cannot self-assert `sender_id`. Locally-created conversations include the local
identity, and every delivery has a per-recipient receipt.

The SQLite store is restart-recoverable for one installation, not a shared
multi-worker exactly-once coordinator. The optional GitHub adapter is a
rendezvous, not a mailbox. libp2p is a future external bridge and must remain
reported unavailable until a real authenticated implementation is wired.

## Transcript read scope (`transcript.py`)

`transcript.py` is pure: it reads already-fetched rows and returns dictionaries.
It performs no I/O and no authorization.

The transcript routes in `app/gateway/routers/peer_network.py` deliberately omit
`@require_permission(..., owner_check=True)`. A peer turn runs on
`peer_thread_id(peer_agent_id)`, owned by `NETWORK_OWNER` (`"installation"`) —
a constant, never a session user id — so `owner_check` would 404 the operator
who owns the installation. `_assert_peer_network_scope` is the substitute: an
explicit allow-check that **fails closed**. Keep that reasoning at the call site;
an unexplained missing decorator is an authorization hole, not a simplification.

Three invariants the projection owns:

1. **Allowlist, never filter.** `PeerNetworkStore.get_peer` returns `url`,
   `websocket_url`, `outbound_token`, `token_hash` and the full card. Read named
   safe keys only, so a field added to the peer row later cannot leak by
   omission. Same rule as `_public_peer` in `tools/builtins/peer_network_tool.py`.
2. **Two sides, never merged.** `peer_message` and `local_reply` are distinct
   roles. Merging them would let a reader believe a remote peer said something
   the local Agent said.
3. **Bounded reports truncation.** `truncated` / `events_truncated` are returned
   and rendered. A partial response must never look complete.

`hide_from_ui` on a peer turn's triggering message is unchanged and is *not* a
blocker: `RunJournal.on_llm_end` persists AI replies and tool results
unconditionally, so the local half is already durable. Do not widen
`hide_from_ui` semantics to "expose" this — that flag has ~15 middleware writers
plus memory, channels and the runs inspector. This surface stays opt-in by route.

Retention prunes delivered/read history only, floored at 7 days
(`ALPHA_PEER_NETWORK_RETENTION_DAYS`, default 90). It never prunes undelivered
messages or conversations, and it rides the existing retry loop rather than
adding a background task.

Regression coverage: `backend/tests/test_peer_network.py`,
`backend/tests/test_bots_dm.py`, `backend/tests/test_external_alpha_*.py`, and
the frontend `external-alpha*.test.mjs` contract pins. Full operations and
free/open-source research: `docs/ALPHA_PEER_NETWORK.md`.
