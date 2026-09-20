"""Comprehensive tests for Persistent Responsibility Engine & Failover."""

from __future__ import annotations

from alpha.company.responsibility import ResponsibilityEngine


def test_responsibility_survival_and_failover_ladder():
    engine = ResponsibilityEngine()

    # 1. Register Responsibility
    binding = engine.register_responsibility(
        name="Zero-Trust Authentication Gateway",
        department="security",
        primary_bot_name="bot-sec-1",
        backup_bot_name="bot-sec-backup",
        recovery_bot_name="bot-sre-recovery",
    )
    resp_id = binding.responsibility_id
    assert binding.active_bot_name == "bot-sec-1"
    assert binding.status == "active"

    # 2. Primary worker fails -> Backup worker assumes duty automatically
    f1 = engine.trigger_failover(resp_id, reason="Primary process crashed")
    assert f1.active_bot_name == "bot-sec-backup"
    assert f1.status == "failed_over"

    # 3. Backup worker also degrades -> Recovery agent assumes duty
    f2 = engine.trigger_failover(resp_id, reason="Backup experiencing high latency")
    assert f2.active_bot_name == "bot-sre-recovery"
    assert f2.status == "degraded"

    # 4. Restore duty back to primary once healthy
    restored = engine.restore_primary(resp_id)
    assert restored.active_bot_name == "bot-sec-1"
    assert restored.status == "active"

    # 5. Verify Failover Audit Log
    history = engine.get_failover_history()
    assert len(history) == 2
    assert history[0]["former_active"] == "bot-sec-1"
    assert history[0]["new_active"] == "bot-sec-backup"
    assert history[1]["former_active"] == "bot-sec-backup"
    assert history[1]["new_active"] == "bot-sre-recovery"
