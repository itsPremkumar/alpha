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

## Connection strings (`invite.py`)

One string carries the whole connection, so connecting is one copy and one
paste. `parse_invite` is the plane's only parser of fully attacker-controlled
text and is written to fail rather than repair: an unknown version or field is
refused, not guessed, and every refusal names the offending field.

**Single use is enforced by the issuer, via a monotonic epoch.** Not by rotating
the pairing code on redemption — that protects nothing, because redemption runs
on the *redeemer's* machine while the exposed credential is the *issuer's*, and
the redeemer cannot invalidate it. `accept_pair` consumes `invite_epoch` only
*after* the code verifies (checking first would let anyone who saw an invite claim
a huge epoch and permanently block every future invite), and a replay is refused
*without* recording a throttle failure (so a third party holding a screenshot
cannot burn the owner's budget). A pairing carrying no epoch bypasses the check,
which is what keeps the classic manual path working.

`storage.PUBLIC_PEER_FIELDS` / `MODEL_PEER_FIELDS` are the **only** peer
projections, in `storage.py` beside the row shape. Two copies had drifted: the
model tool shipped a narrow allowlist while `GET /status` and `POST /pair`
returned the raw row, so the API leaked more than the tool. Always allowlist, so
a column added tomorrow is invisible until somebody publishes it deliberately.

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

## Retry (`storage.py`, `service.py`)

Every delivery attempt is **counted** and the next one **scheduled**
(`RETRY_BACKOFF_SECONDS`, exponential and bounded). `pending_messages` honours
each receipt's `next_attempt_at`. `update_delivery` **appends** to a receipt's
error rather than assigning it.

Two properties, each of which was a real defect:

- A flat 15s tick with no attempt counter meant a peer that was down for a day
  absorbed ~5,700 requests.
- Assigning `error` meant the *first* failure reason — usually the informative
  one, a 401 or an unreachable host — was destroyed by the first retry and
  replaced with a generic timeout.

Nothing is ever dropped from the retry queue. The schedule's job is to stop
hammering an unreachable endpoint, not to prune: an operator who believes they
sent something must never find it silently deleted.

## Retention

Retention prunes delivered/read history only, floored at 7 days
(`ALPHA_PEER_NETWORK_RETENTION_DAYS`, default 90). It never prunes undelivered
messages or conversations, and it rides the existing retry loop rather than
adding a background task.

## Search (`storage.py`)

Message bodies are searchable via SQLite FTS5 — free, in stdlib, no service. Read
`_ensure_fts_index`'s docstring before touching it: an external-content FTS table
is not self-populating, its `rebuild` is not ordinary DML, and `count(*)` on it
reads the shadow tables rather than the live index. All three are load-bearing
and each has a regression test in `tests/test_external_alpha_fts.py`. FTS5
availability is a property of the interpreter, so it is detected, reported as
`fts_available`, and degrades to a bounded escaped `LIKE` scan.

Regression coverage: `backend/tests/test_peer_network.py`,
`backend/tests/test_bots_dm.py`, `backend/tests/test_external_alpha_*.py`, and
the frontend `external-alpha*.test.mjs` contract pins. Full operations and
free/open-source research: `docs/ALPHA_PEER_NETWORK.md`.
