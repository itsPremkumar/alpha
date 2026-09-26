# Alpha peer network module

`alpha.peer_network` is an installation-scoped, local-first Alpha-to-Alpha
plane. `service.py` is the lifecycle/policy owner; `storage.py` is synchronous
SQLite and async callers must use `asyncio.to_thread`; `transport.py` is
HTTP-first with WebSocket fallback; `discovery.py` owns UDP and optional mDNS;
`github.py` publishes/discovers public Agent Cards only.

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

Regression coverage: `backend/tests/test_peer_network.py`,
`backend/tests/test_bots_dm.py`, and the frontend client contract test. Full
operations and free/open-source research: `docs/ALPHA_PEER_NETWORK.md`.
