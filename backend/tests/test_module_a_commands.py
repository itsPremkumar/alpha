"""Tests for the five Module-A spec commands with real handlers.

Pins, per command: catalog registration, dispatch through the real
``command_registry.execute`` seam, the honest failure paths, and the
no-fabrication rules (a rejected schedule says why, a model failure is
reported as a failure, a corrupt Sentinel journal is not "no passes", the
teamwork preview starts nothing).

The ``/grill-me`` tests stub the model client and are therefore SIMULATED
evidence about model output — they prove the plumbing and the honesty of the
failure paths, not the quality of any model's questions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from alpha.commands.backend_handlers import (
    handle_boost,
    handle_grill_me,
    handle_schedule,
    handle_self_heal,
    handle_teamwork_preview,
)
from alpha.commands.catalog import get_default_catalog_entries
from alpha.commands.registry import command_registry

MODULE_A = ["/boost", "/schedule", "/grill-me", "/teamwork-preview", "/self-heal"]


# ── catalog + dispatch ───────────────────────────────────────────────


def test_catalog_defines_all_five_as_core_commands():
    entries = {row[0]: row for row in get_default_catalog_entries()}
    for name in MODULE_A:
        assert name in entries, f"{name} missing from the catalog"
        row = entries[name]
        assert row[4] is True, f"{name} must be a core command"
        assert row[3].strip(), f"{name} needs usage text"


def test_registry_knows_all_five_and_dispatches_them():
    # Importing backend_handlers binds the handlers on module import.
    import alpha.commands.backend_handlers  # noqa: F401

    for name in MODULE_A:
        assert command_registry.get(name) is not None, f"{name} not resolvable by the registry"


def test_execute_dispatches_boost_through_the_registry():
    import alpha.commands.backend_handlers  # noqa: F401

    result = command_registry.execute("/boost refactor the auth layer across services and prove it with tests")
    assert result.status == "success"
    assert result.command == "/boost"
    assert "tier" in result.output
    assert result.data.get("changed_anything") is False
    assert "not guarantees" in result.output or "not a guarantee" in result.output


# ── /boost ───────────────────────────────────────────────────────────


def test_boost_without_args_is_a_usage_error():
    result = handle_boost("   ")
    assert result.status == "error"
    assert "Usage:" in result.output


def test_boost_reports_the_real_derivation_for_simple_and_complex_tasks():
    simple = handle_boost("say hi")
    complex_task = handle_boost(
        "architect a distributed consensus protocol, prove liveness and safety under partitions, "
        "and migrate 40 services without downtime"
    )
    assert simple.status == complex_task.status == "success"
    assert simple.data["thinking_mode"]["tier"] != complex_task.data["thinking_mode"]["tier"], (
        "a trivially simple task and an architectural migration must not derive the same thinking tier"
    )


def test_boost_surfaces_a_classifier_failure_instead_of_inventing_a_tier(monkeypatch):
    import alpha.orchestration.intent as intent

    def boom(_text: str):
        raise RuntimeError("classifier offline")

    monkeypatch.setattr(intent, "classify_domain", boom)
    result = handle_boost("anything")
    assert result.status == "error"
    assert "classifier offline" in result.output


# ── /schedule ────────────────────────────────────────────────────────


@pytest.fixture()
def temp_cron(monkeypatch, tmp_path: Path):
    """Point the global cron manager at an isolated directory."""
    from alpha.scheduler import cron_manager

    manager = cron_manager.CronManager(tmp_path / "cron")
    monkeypatch.setattr(cron_manager, "_global_cron_manager", manager)
    return manager


def test_schedule_creates_a_real_cron_job_from_a_cron_expression(temp_cron):
    result = handle_schedule("*/15 * * * * /status", context={"thread_id": "t1"})
    assert result.status == "success"
    job = result.data["job"]
    assert job["cron_expression"] == "*/15 * * * *"
    assert job["command_or_prompt"] == "/status"
    assert result.data["cron_expression"] == "*/15 * * * *"
    assert result.data["thread_id"] == "t1"
    # The job really landed in the manager (not just a formatted string).
    assert any(getattr(j, "name", None) == job["name"] for j in temp_cron.list_jobs())


def test_schedule_converts_exact_intervals_deterministically(temp_cron):
    for spec, expected in (("every 30m", "*/30 * * * *"), ("every 6h", "0 */6 * * *"), ("every 1d", "0 0 */1 * *")):
        result = handle_schedule(f"{spec} do the thing")
        assert result.status == "success", result.output
        assert result.data["cron_expression"] == expected


@pytest.mark.parametrize("spec", ["every 7h", "every 90m", "every 45d", "*/15 * * *", "0 9 * *"])
def test_schedule_rejects_inexpressible_schedules_with_the_reason(spec):
    result = handle_schedule(f"{spec} do the thing")
    assert result.status == "error"
    assert "Schedule rejected" in result.output


def test_schedule_requires_both_parts(temp_cron):
    assert handle_schedule("").status == "error"
    assert handle_schedule("*/5 * * * *").status == "error"
    assert handle_schedule("*/5 * * * *    ").status == "error"


def test_schedule_reports_a_manager_failure_instead_of_claiming_success(temp_cron, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("cron store read-only")

    monkeypatch.setattr(temp_cron, "add_job", boom)
    result = handle_schedule("*/5 * * * * /status")
    assert result.status == "error"
    assert "cron store read-only" in result.output


# ── /grill-me (simulated model evidence) ─────────────────────────────


def test_grill_me_requires_a_subject():
    assert handle_grill_me(" ").status == "error"


def test_grill_me_returns_the_model_text_verbatim(monkeypatch):
    import alpha.utils.oneshot_llm as oneshot

    async def stub(**_kwargs):
        return "1. What happens on rollback?\n2. Which assumption is unmeasured?"

    monkeypatch.setattr(oneshot, "run_oneshot_llm", stub)
    result = handle_grill_me("ship the migration on Friday", context={"thread_id": "t9"})
    assert result.status == "success"
    assert "What happens on rollback?" in result.output
    assert result.data["produced"] is True


def test_grill_me_reports_an_empty_model_answer_honestly(monkeypatch):
    import alpha.utils.oneshot_llm as oneshot

    async def empty(**_kwargs):
        return "   "

    monkeypatch.setattr(oneshot, "run_oneshot_llm", empty)
    result = handle_grill_me("anything")
    assert result.status == "ok"
    assert "no questions were produced" in result.output
    assert result.data["produced"] is False


def test_grill_me_reports_a_model_failure_with_the_real_reason(monkeypatch):
    import alpha.utils.oneshot_llm as oneshot

    async def boom(**_kwargs):
        raise RuntimeError("provider 503 no key")

    monkeypatch.setattr(oneshot, "run_oneshot_llm", boom)
    result = handle_grill_me("anything")
    assert result.status == "error"
    assert "provider 503 no key" in result.output


# ── /teamwork-preview ────────────────────────────────────────────────


def test_teamwork_preview_lists_the_real_roster(monkeypatch, tmp_path: Path):
    import alpha.config.runtime_paths as runtime_paths

    roster = tmp_path / "bots" / "roster.json"
    roster.parent.mkdir(parents=True, exist_ok=True)
    roster.write_text(
        json.dumps(
            {
                "bots": [
                    {"name": "scout", "role": "research", "status": "idle"},
                    {"name": "builder", "role": "coding", "status": "busy"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runtime_paths, "runtime_home", lambda *a, **k: tmp_path)
    result = handle_teamwork_preview("")
    assert result.status == "success"
    assert "scout" in result.output and "builder" in result.output
    assert result.data["read_only"] is True
    assert result.data["bots"] == 2


def test_teamwork_preview_discloses_a_missing_roster_instead_of_inventing_one(monkeypatch, tmp_path: Path):
    import alpha.config.runtime_paths as runtime_paths

    monkeypatch.setattr(runtime_paths, "runtime_home", lambda *a, **k: tmp_path / "nothing-here")
    result = handle_teamwork_preview("")
    assert result.status == "success"
    assert "unavailable" in result.output
    assert result.data["bots"] == 0


# ── /self-heal ───────────────────────────────────────────────────────


def test_self_heal_reports_real_journal_state_and_claims_no_fix(monkeypatch, tmp_path: Path):
    import alpha.runtime.sentinel.report_store as report_store

    # The store module binds runtime_home at import time, so the seam to patch
    # is the module-level name the default store actually calls.
    monkeypatch.setattr(report_store, "runtime_home", lambda *a, **k: tmp_path)
    from alpha.runtime.sentinel.report_store import SentinelReportStore
    store = SentinelReportStore(tmp_path / "sentinel-reports")
    store.append(
        {
            "recorded_at": "2026-09-24T10:00:00+00:00",
            "trigger": "api",
            "auto_heal": False,
            "report": {
                "duration_s": 1.0,
                "scanned": 3,
                "summary": "scanned 3 signal(s): 0 fixed, 0 reverted, 0 escalated",
                "fixed": 0,
                "reverted": 0,
                "escalated": 1,
                "errors": ["verify timeout on one candidate"],
                "outcomes": [],
            },
        }
    )
    result = handle_self_heal("")
    assert result.status == "success"
    assert "no fix is applied" in result.output
    assert "Sentinel passes on record: 1" in result.output
    assert "verify timeout on one candidate" in result.output
    assert result.data["applied_fix"] is False


def test_self_heal_reports_a_corrupt_journal_instead_of_no_passes(monkeypatch, tmp_path: Path):
    import alpha.runtime.sentinel.report_store as report_store

    monkeypatch.setattr(report_store, "runtime_home", lambda *a, **k: tmp_path)
    journal_dir = tmp_path / "sentinel-reports"
    journal_dir.mkdir(parents=True, exist_ok=True)
    (journal_dir / "reports.jsonl").write_text("{not json}\n", encoding="utf-8")
    result = handle_self_heal("")
    assert result.status == "success"  # the diagnosis itself succeeded
    assert "Sentinel journal: unreadable" in result.output
    assert "no passes recorded yet" not in result.output
