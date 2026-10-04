"""FTS5 behaviour tests for peer message search.

These are the store-level guarantees the UI depends on. The most important one
is the last: a search over an *external-content* FTS table must find a message
that was inserted after the index was created. An external-content index is not
self-populating -- if it is never refreshed or triggered, it silently returns
nothing for new rows, which would look exactly like "search is broken".

FTS5 availability is a property of the interpreter, not of this package, so
`_ensure_fts_index` degrades to a bounded LIKE scan rather than raising. Both
paths must return the same rows for the same needle, and the caller is told which
one ran.
"""

from __future__ import annotations

from alpha.peer_network.storage import PeerNetworkStore


def _store(tmp_path) -> PeerNetworkStore:
    return PeerNetworkStore(tmp_path / "network.sqlite3")


def _seed(store: PeerNetworkStore, *texts: str, direction: str = "inbound") -> None:
    # Idempotent: a test may call this twice to exercise rows written after the
    # index existed. `create_conversation` is a no-op for an existing id, but a
    # re-seed must still produce distinct message ids or the assertions below
    # would read the first batch twice.
    already = store._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    store.create_conversation(conversation_id="conv_1", mode="direct", title="room", participants=["alpha_local", "alpha_remote"])
    for offset, text in enumerate(texts):
        index = already + offset
        store.add_message(
            message_id=f"msg_{index}",
            conversation_id="conv_1",
            sender_id="alpha_remote" if direction == "inbound" else "alpha_local",
            recipients=["alpha_local" if direction == "inbound" else "alpha_remote"],
            kind="chat",
            text=text,
            payload={},
            status="delivered",
            direction=direction,
            delivery_status="delivered",
            delivery_recipients=["alpha_local" if direction == "inbound" else "alpha_remote"],
        )


def test_fts_index_is_created_when_the_interpreter_supports_it(tmp_path):
    store = _store(tmp_path)
    try:
        assert store.fts_available is True
    finally:
        store.close()


def test_search_finds_a_message_inserted_after_the_index_existed(tmp_path):
    """The external-content index must stay current, or search silently breaks."""

    store = _store(tmp_path)
    try:
        _seed(store, "the original message")
        # Seed *after* construction and after an initial search, so an index that
        # was never populated cannot pass this.
        store.search_messages("original")
        _seed(store, "a completely different needle about kubernetes")
        hits = store.search_messages("kubernetes")
        assert [m["message_id"] for m in hits] == ["msg_1"]
    finally:
        store.close()


def test_an_index_created_over_existing_rows_finds_them(tmp_path):
    """The backfill case: a database that already had messages before FTS existed.

    Triggers only cover rows written after they are created, so without the
    one-time `rebuild` an installation upgrading to this feature would have an
    index that looks healthy and matches nothing.
    """

    store = _store(tmp_path)
    try:
        _seed(store, "preexisting kubernetes history")
        # Simulate the upgrade: drop the index and its triggers, exactly as a
        # database written before this feature would have them absent.
        store._conn.executescript(
            """
            DROP TRIGGER IF EXISTS messages_fts_ai;
            DROP TRIGGER IF EXISTS messages_fts_ad;
            DROP TRIGGER IF EXISTS messages_fts_au;
            DROP TABLE IF EXISTS messages_fts;
            """
        )
        store._conn.commit()
        assert store.search_messages("kubernetes") == []  # no index at all yet

        store._ensure_fts_index()
        assert store.fts_available is True
        assert [m["message_id"] for m in store.search_messages("kubernetes")] == ["msg_0"]
    finally:
        store.close()


def test_an_emptied_index_is_repaired_rather_than_left_broken(tmp_path):
    """The backfill probe also self-heals a damaged index.

    Deliberately emptying the index and reopening the store must restore
    findability. A `count(*)`-based guard would have seen a plausible row count
    and skipped the repair, leaving search silently broken -- which is exactly
    why the guard asks the index a question instead of counting it.
    """

    store = _store(tmp_path)
    try:
        _seed(store, "durable needle")
        store._conn.execute("DELETE FROM messages_fts WHERE messages_fts MATCH 'durable'")
        store._conn.commit()
        assert store.search_messages("durable") == []
        # Reopening re-runs setup, which must notice and repair.
        store._ensure_fts_index()
        assert [m["message_id"] for m in store.search_messages("durable")] == ["msg_0"]
    finally:
        store.close()


def test_reopening_a_populated_index_does_not_rebuild_every_time(tmp_path):
    """A healthy index must not be rebuilt on every process start.

    A rebuild is O(history) work per process start, so the guard has to actually
    skip. The probe term is a real word from a seeded body: a rebuild would drop
    any index row that does not correspond to a live content row, so the sentinel
    proves whether a rebuild ran.
    """

    store = _store(tmp_path)
    try:
        _seed(store, "a body containing the word zzzsentinel")
        # A phantom index entry: no such row exists in `messages`.
        store._conn.execute("INSERT INTO messages_fts(rowid, text) VALUES (999999, 'zzzsentinel')")
        store._conn.commit()
        store._ensure_fts_index()
        # The probe found the real row first, so the guarded backfill did NOT run
        # and the phantom survived.
        assert store._conn.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'zzzsentinel'").fetchall()
        # Only the real message resolves, because the join requires a content row.
        assert [m["message_id"] for m in store.search_messages("zzzsentinel")] == ["msg_0"]
    finally:
        store.close()


def test_updating_a_message_body_is_reindexed(tmp_path):
    store = _store(tmp_path)
    try:
        _seed(store, "original elasticsearch wording")
        store._conn.execute("UPDATE messages SET text = 'replaced by terraform' WHERE message_id = 'msg_0'")
        store._conn.commit()
        assert store.search_messages("terraform")
        assert store.search_messages("elasticsearch") == []
    finally:
        store.close()


def test_search_is_case_insensitive(tmp_path):
    store = _store(tmp_path)
    try:
        _seed(store, "Deployment LOG analysis")
        assert store.search_messages("deployment log")
        assert store.search_messages("DEPLOYMENT LOG")
    finally:
        store.close()


def test_search_ignores_diacritics(tmp_path):
    """A peer writing `resume` must be findable by an operator typing `resume`.

    The tokenizer is `unicode61 remove_diacritics 2`, so an accented body folds
    to its ASCII form. Without that, a cross-installation conversation becomes
    unsearchable for anyone not typing the peer's keyboard layout.
    """

    store = _store(tmp_path)
    try:
        _seed(store, "rapport et résumé complet")
        assert store.search_messages("resume")
        assert store.search_messages("rapport")
    finally:
        store.close()


def test_an_empty_needle_returns_nothing(tmp_path):
    """ "Search for nothing" must never become "return the whole mailbox"."""

    store = _store(tmp_path)
    try:
        _seed(store, "one", "two", "three")
        assert store.search_messages("") == []
        assert store.search_messages("   ") == []
        assert store.search_messages(None) == []
    finally:
        store.close()


def test_a_needle_that_matches_nothing_returns_nothing(tmp_path):
    store = _store(tmp_path)
    try:
        _seed(store, "hello")
        assert store.search_messages("absolutely-not-present") == []
    finally:
        store.close()


def test_a_malformed_fts_expression_degrades_instead_of_raising(tmp_path):
    """An unbalanced quote must not 500 the tab.

    FTS5 parses its query as a small expression language, so user input like
    `"unterminated` is a syntax error. The fallback LIKE scan returns rows
    instead of propagating the error.
    """

    store = _store(tmp_path)
    try:
        _seed(store, "quoted text here")
        # Must not raise, whatever it returns.
        assert isinstance(store.search_messages('"unterminated'), list)
    finally:
        store.close()


def test_operators_in_the_needle_do_not_crash_the_search(tmp_path):
    store = _store(tmp_path)
    try:
        _seed(store, "alpha AND beta")
        for needle in ("AND", "NOT", "*", "a OR", "NEAR(", "alpha:beta"):
            assert isinstance(store.search_messages(needle), list), needle
    finally:
        store.close()


def test_search_can_be_narrowed_to_a_conversation(tmp_path):
    store = _store(tmp_path)
    try:
        _seed(store, "shared needle")
        store.create_conversation(conversation_id="conv_2", mode="direct", title="other", participants=["alpha_local", "alpha_b"])
        store.add_message(
            message_id="msg_other",
            conversation_id="conv_2",
            sender_id="alpha_b",
            recipients=["alpha_local"],
            kind="chat",
            text="shared needle",
            payload={},
            status="delivered",
            direction="inbound",
            delivery_status="delivered",
            delivery_recipients=["alpha_local"],
        )
        both = store.search_messages("shared needle")
        assert len(both) == 2
        only_first = store.search_messages("shared needle", conversation_id="conv_1")
        assert [m["message_id"] for m in only_first] == ["msg_0"]
    finally:
        store.close()


def test_search_can_be_narrowed_to_a_direction(tmp_path):
    store = _store(tmp_path)
    try:
        _seed(store, "directional needle", direction="inbound")
        _seed(store, "directional needle", direction="outbound")
        inbound = store.search_messages("directional needle", direction="inbound")
        assert inbound and all(m["direction"] == "inbound" for m in inbound)
    finally:
        store.close()


def test_search_limit_is_bounded(tmp_path):
    store = _store(tmp_path)
    try:
        _seed(store, *["repeated body"] * 30)
        assert len(store.search_messages("repeated", limit=100000)) <= 200
        assert len(store.search_messages("repeated", limit=5)) <= 5
    finally:
        store.close()


def test_a_pruned_message_leaves_the_search_index(tmp_path):
    """Search must not resurrect history that retention removed.

    An external-content FTS index resolves through the content table, so a
    deleted content row makes its index entry unresolvable. The prune must not
    leave a hit that then fails to load.
    """

    store = _store(tmp_path)
    try:
        store.create_conversation(conversation_id="conv_1", mode="direct", title="room", participants=["alpha_local", "alpha_remote"])
        store.add_message(
            message_id="msg_old",
            conversation_id="conv_1",
            sender_id="alpha_remote",
            recipients=["alpha_local"],
            kind="chat",
            text="forgettable needle",
            payload={},
            status="delivered",
            direction="inbound",
            delivery_status="delivered",
            delivery_recipients=["alpha_local"],
        )
        store._conn.execute("UPDATE messages SET created_at = '2020-01-01T00:00:00+00:00' WHERE message_id = 'msg_old'")
        store._conn.commit()
        assert store.search_messages("forgettable")
        store.prune_messages("2026-01-01T00:00:00+00:00")
        assert store.search_messages("forgettable") == []
    finally:
        store.close()


def test_wildcard_characters_in_the_fallback_scan_are_escaped(tmp_path):
    """A LIKE needle must be escaped, or `%` returns everything."""

    store = _store(tmp_path)
    try:
        _seed(store, "plain body")
        # If `%` were unescaped this would match every row.
        assert store.search_messages("%") == []
        assert store.search_messages("_") == []
    finally:
        store.close()


def test_message_stats_counts_from_rows_not_from_assumptions(tmp_path):
    store = _store(tmp_path)
    try:
        _seed(store, "a", "b", direction="inbound")
        _seed(store, "c", direction="outbound")
        stats = store.message_stats()
        assert stats["directions"] == {"inbound": 2, "outbound": 1}
        assert stats["kinds"] == {"chat": 3}
        assert stats["statuses"] == {"delivered": 3}
        assert stats["modes"] == {"direct": 1}
    finally:
        store.close()


def test_message_stats_on_an_empty_database_is_empty_not_absent(tmp_path):
    """The UI must be able to tell "no data" from "field missing"."""

    store = _store(tmp_path)
    try:
        stats = store.message_stats()
        for key in ("modes", "kinds", "directions", "statuses"):
            assert stats[key] == {}
    finally:
        store.close()
