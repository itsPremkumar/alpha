"""Unit tests for the kernel-owned, mod-namespaced mod state store."""

import pytest

from alpha.mods.context import CapabilityContext
from alpha.mods.kernel import ModKernel
from alpha.mods.state import DEFAULT_MAX_VALUE_BYTES, ModStateStore, StateQuotaError


@pytest.fixture
def store():
    return ModStateStore()


def test_state_is_shared_across_contexts_from_the_same_kernel():
    """The property that makes cross-handler state possible at all."""
    kernel = ModKernel()

    first = CapabilityContext("mod", {"storage:write"}, kernel)
    first.storage.set("count", 1)

    # A second context for the *same* mod, built for a later event, must see it.
    second = CapabilityContext("mod", {"storage:write"}, kernel)
    assert second.storage.get("count") == 1
    second.storage.set("count", 2)

    third = CapabilityContext("mod", {"storage:write"}, kernel)
    assert third.storage.get("count") == 2


def test_state_is_namespaced_per_mod():
    """One mod's clear() must never erase another mod's state."""
    kernel = ModKernel()
    CapabilityContext("mod_a", {"storage:write"}, kernel).storage.set("k", "a")
    ctx_b = CapabilityContext("mod_b", {"storage:write"}, kernel)
    ctx_b.storage.set("k", "b")

    assert ctx_b.storage.clear() == 1
    assert CapabilityContext("mod_a", {"storage:write"}, kernel).storage.get("k") == "a"


def test_absent_key_returns_default(store):
    assert store.get("m", "missing") is None
    assert store.get("m", "missing", "fallback") == "fallback"


def test_delete_reports_whether_a_key_existed(store):
    store.set("m", "k", 1)
    assert store.delete("m", "k") is True
    assert store.delete("m", "k") is False


def test_over_bound_value_is_refused_named_rather_than_truncated(store):
    store.set("m", "small", "x")
    with pytest.raises(StateQuotaError, match="over the"):
        store.set("m", "huge", "x" * (DEFAULT_MAX_VALUE_BYTES + 10))


def test_over_bound_key_count_is_refused(store):
    small = ModStateStore(max_keys=3)
    for i in range(3):
        small.set("m", f"k{i}", i)
    with pytest.raises(StateQuotaError, match="already holds"):
        small.set("m", "k3", 3)
    # The refused write did not displace an existing key.
    assert small.count("m") == 3


def test_snapshot_is_scoped_to_one_mod(store):
    store.set("a", "k", 1)
    store.set("b", "k", 2)
    assert store.snapshot("a") == {"k": 1}
    assert store.snapshot() == {"a": {"k": 1}, "b": {"k": 2}}


def test_restore_replaces_the_namespace(store):
    store.set("m", "old", 1)
    written = store.restore("m", {"new": 2})
    assert written == 1
    assert store.get("m", "old") is None
    assert store.get("m", "new") == 2


def test_declared_and_observed_keys_are_kept_apart(store):
    store.declare("m", reads={"a"}, writes={"b"})
    assert store.declaration("m") == {"state_reads": ["a"], "state_writes": ["b"]}

    store.set("m", "b", 1)
    store.get("m", "b")

    observed = store.observed("m")
    assert observed == {"state_reads": ["b"], "state_writes": ["b"]}


def test_mod_names_and_total_keys(store):
    store.set("a", "k", 1)
    store.set("b", "k", 1)
    store.set("b", "j", 2)
    assert store.mod_names() == ["a", "b"]
    assert store.total_keys() == 3


def test_context_storage_snapshot_round_trips():
    kernel = ModKernel()
    ctx = CapabilityContext("mod", {"storage:write"}, kernel)
    ctx.storage.set("k", {"nested": [1, 2]})
    assert ctx.storage.snapshot() == {"k": {"nested": [1, 2]}}
