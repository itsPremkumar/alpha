"""Unit tests for AuditLedgerMod — the wildcard audit trail."""

import pytest

from alpha.mods.audit import AuditLedgerMod, redact, summarize
from alpha.mods.kernel import ModKernel, _register_builtin_enforcers
from alpha.mods.types import (
    AlphaEvent,
    AlphaMod,  # noqa: F401  (protocol re-export convenience)
    CorrelationContext,
    EventOutcome,
    EventResult,
    ModPriority,
)


def _kernel_with_audit():
    kernel = ModKernel()
    _register_builtin_enforcers(kernel)
    return kernel


def _event(name="tool.requested", **payload):
    return AlphaEvent(name=name, payload=payload, correlation=CorrelationContext.create(run_id="run_1"))


@pytest.mark.asyncio
async def test_audit_ledger_records_every_dispatched_event():
    kernel = _kernel_with_audit()
    audit = kernel.get_mod("audit_ledger")

    await kernel.dispatch(_event("tool.requested", tool_name="view_file", tool_args={"path": "a.py"}))
    await kernel.dispatch(_event("some.other.event", value=1))

    entries = audit.get_entries(limit=10)
    names = {e["event"] for e in entries}
    assert "tool.requested" in names
    assert "some.other.event" in names
    assert all(e["outcome"] for e in entries)


@pytest.mark.asyncio
async def test_audit_ledger_sees_a_deny_from_a_higher_priority_mod():
    """The ledger is outermost, so a terminal DENY still reaches it."""
    kernel = _kernel_with_audit()
    audit = kernel.get_mod("audit_ledger")

    class Denier:
        # VERIFICATION sits below USER_EXTENSIONS, so an external mod may hold
        # the seat; the point of the test is the ledger, not the guard.
        name = "denier"
        version = "1.0.0"
        priority = int(ModPriority.VERIFICATION)
        required_capabilities: set[str] = set()
        subscribed_events = {"tool.requested"}

        async def handle(self, ctx, event, next_fn):
            return EventResult.deny(event, reason="TEST_DENY")

    kernel.register_mod(Denier(), granted_capabilities=set())
    result = await kernel.dispatch(_event("tool.requested", tool_name="bash"))

    assert result.outcome == EventOutcome.DENY
    entries = audit.get_entries(event_name="tool.requested")
    assert entries
    assert entries[-1]["outcome"] == "deny"
    assert "denier" in entries[-1]["chain"]


@pytest.mark.asyncio
async def test_audit_ledger_records_the_chain_that_decided():
    kernel = _kernel_with_audit()
    audit = kernel.get_mod("audit_ledger")

    await kernel.dispatch(_event("tool.requested", tool_name="view_file", tool_args={"path": "a.py"}))
    entry = audit.get_entries(event_name="tool.requested")[-1]

    assert "sec_default" in entry["chain"]
    # The guard is outermost, so it is first in the chain.
    assert entry["chain"][0] == "sec_default"


def test_audit_ledger_redacts_credential_shaped_values():
    payload = {
        "api_key": "sk-abcdefghijklmnopqrstuvwxyz012345",
        "Authorization": "Bearer supersecret",
        "nested": {"password": "hunter2", "safe": "keep me"},
        "list": [{"token": "abc"}, "plain"],
    }
    redacted = redact(payload)

    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["nested"]["password"] == "[REDACTED]"
    assert redacted["nested"]["safe"] == "keep me"
    assert redacted["list"][0]["token"] == "[REDACTED]"
    assert redacted["list"][1] == "plain"


def test_audit_ledger_redacts_a_secret_under_a_boring_key():
    assert redact({"note": "my key is sk-abcdefghijklmnopqrstuvwxyz012345"})["note"] == "[REDACTED]"


def test_audit_ledger_bounds_a_long_string():
    out = redact({"body": "x" * 5000})
    assert len(out["body"]) < 2100
    assert out["body"].endswith("[truncated]")


def test_audit_ledger_bounds_its_entry_count():
    audit = AuditLedgerMod(max_entries=10)
    for i in range(25):
        audit._append({"event": f"e{i}", "chain": [], "outcome": "continue"})
    assert len(audit._entries) == 10
    assert audit.get_entries()[0]["event"] == "e15"


def test_audit_ledger_stats_and_clear():
    audit = AuditLedgerMod()
    audit._entries = [
        {"event": "a", "chain": ["m1"], "outcome": "continue"},
        {"event": "b", "chain": ["m1", "m2"], "outcome": "deny"},
    ]
    audit._mod_counters = {"m1": 2, "m2": 1}
    audit._outcome_counters = {"continue": 1, "deny": 1}

    stats = audit.stats()
    assert stats["retained"] == 2
    assert stats["by_outcome"]["deny"] == 1

    assert audit.clear() == 2
    assert audit.stats()["retained"] == 0


def test_summarize_folds_entries():
    rows = [
        {"event": "a", "chain": ["m1"], "outcome": "continue"},
        {"event": "a", "chain": ["m2"], "outcome": "deny"},
    ]
    summary = summarize(rows)
    assert summary["count"] == 2
    assert summary["events"] == ["a"]
    assert summary["outcomes"] == {"continue": 1, "deny": 1}
    assert summary["mods"] == ["m1", "m2"]


def test_export_jsonl_is_valid_json_lines():
    audit = AuditLedgerMod()
    audit._entries = [{"event": "a", "chain": [], "outcome": "continue"}]
    lines = audit.export_jsonl().splitlines()
    assert len(lines) == 1
    assert '"event": "a"' in lines[0]


def test_audit_ledger_filters():
    audit = AuditLedgerMod()
    audit._entries = [
        {"event": "a", "chain": ["m1"], "outcome": "continue"},
        {"event": "b", "chain": ["m2"], "outcome": "deny"},
    ]
    assert len(audit.get_entries(event_name="a")) == 1
    assert len(audit.get_entries(mod_name="m2")) == 1
    assert len(audit.get_entries(outcome="deny")) == 1
    assert len(audit.get_entries(outcome="rewrite")) == 0
