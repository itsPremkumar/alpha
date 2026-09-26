"""P4 honesty tests: a degraded OpenViking read/search is never silent.

Before wave P4 the configured ``fail_open`` read policy returned ``""`` from
``get_context`` and ``[]`` from ``search`` — indistinguishable from "this user
has no memories". These tests pin the fixed behaviour:

* a degraded context injection carries an explicit unavailability block with
  the real reason (never a bare empty string),
* a degraded search returns one explicit failure row (never an empty result
  set that reads like "nothing matched"),
* the ``raise`` policy still raises ``MemoryReadError`` — fail-closed stays
  fail-closed and fail-open stays non-fatal, they are just no longer
  indistinguishable from emptiness.

Reuses the sibling suite's fixtures/doubles so the fake transport is the same
one the rest of the OpenViking suite already trusts.
"""

from __future__ import annotations

import pytest

# The doubles/fixture live in the sibling suite; importing the module (rather
# than the names) makes pytest collect its `official_integration` fixture for
# this file too, so both suites exercise the exact same fake transport.
from test_openviking_memory_backend import (  # noqa: F401
    _backend_config,
    _manager,
)
from test_openviking_memory_backend import (
    official_integration as official_integration,
)

from alpha.agents.memory.backends.openviking.openviking_manager import (
    DEGRADED_MEMORY_KEY,
    OpenVikingMemoryManager,
)
from alpha.agents.memory.manager import MemoryReadError


def _exploding_manager(tmp_path, monkeypatch, official_integration) -> OpenVikingMemoryManager:
    """A manager whose retriever raises on every call."""
    manager = _manager(tmp_path, monkeypatch, failure_policy={"read": "fail_open"})

    class _Boom:
        def __copy__(self):  # pragma: no cover - trivial
            return self

        def invoke(self, query: str):
            raise ConnectionError("openviking endpoint unreachable")

    manager._retriever = _Boom()
    return manager


def test_degraded_context_injection_discloses_unavailability(
    tmp_path, monkeypatch, official_integration
) -> None:
    manager = _exploding_manager(tmp_path, monkeypatch, official_integration)
    context = manager.get_context("alice")
    assert context != "", "a degraded read must never be an empty string"
    assert DEGRADED_MEMORY_KEY in context
    assert "unavailable" in context
    # The real reason reaches the reader.
    assert "openviking endpoint unreachable" in context
    assert manager._last_degraded_read is not None
    assert "ConnectionError" in manager._last_degraded_read


def test_degraded_search_returns_an_explicit_failure_row(
    tmp_path, monkeypatch, official_integration
) -> None:
    manager = _exploding_manager(tmp_path, monkeypatch, official_integration)
    # The single-user API key is bound to owner_user_id="alice"; the search
    # path resolves the requesting user, so pass alice explicitly.
    results = manager.search("what does alice prefer?", user_id="alice")
    assert len(results) == 1, "a degraded search must not look like zero matches"
    row = results[0]
    assert row[DEGRADED_MEMORY_KEY] is True
    assert row["degraded"] is True
    assert "NOT search results" in row["fact"]
    assert "openviking endpoint unreachable" in row["fact"]


def test_raise_policy_still_raises_memory_read_error(
    tmp_path, monkeypatch, official_integration
) -> None:
    manager = _manager(tmp_path, monkeypatch, failure_policy={"read": "raise"})

    class _Boom:
        def __copy__(self):  # pragma: no cover - trivial
            return self

        def invoke(self, query: str):
            raise ConnectionError("openviking endpoint unreachable")

    manager._retriever = _Boom()
    with pytest.raises(MemoryReadError):
        manager.get_context("alice")
    with pytest.raises(MemoryReadError):
        manager.search("anything")


def test_healthy_reads_are_unchanged_by_the_p4_fix(
    tmp_path, monkeypatch, official_integration
) -> None:
    """The degraded markers must never appear on a healthy read/search."""
    manager = _manager(tmp_path, monkeypatch)
    context = manager.get_context("alice")
    assert DEGRADED_MEMORY_KEY not in context
    assert "Prefers concise answers." in context
    results = manager.search("preferences", user_id="alice")
    assert results and DEGRADED_MEMORY_KEY not in results[0]
    assert _backend_config  # keep the shared config helper referenced for readers
