"""Hermes ports: review queue, MoA-lite, insights, kanban stop guard, bot retry."""

from __future__ import annotations

from agent_workspace.deliberation.moa import (
    build_aggregator_prompt,
    gather_references,
    redact_reference_text,
    run_moa_turn,
)
from agent_workspace.kanban.stop_guard import build_stop_nudge, stop_nudge_enabled, terminal_called
from agent_workspace.learning.insights import format_text, summarize
from agent_workspace.learning.review_queue import ReviewQueue
from agent_workspace.recovery import bot_turn_retry_action


def test_review_queue_coalesces_and_flushes():
    queue = ReviewQueue(max_age_seconds=60.0)
    queue.defer("s1", {"turn": 1}, now=1000.0)
    queue.defer("s1", {"turn": 2}, now=1001.0)
    queue.defer("s2", {"turn": 1}, now=1001.0)
    assert len(queue.pending()) == 2

    seen: list[str] = []
    drained = queue.drain(lambda: True, lambda entry: seen.append(f"{entry.session_id}:{entry.snapshot['turn']}"), now=1002.0)
    assert sorted(drained) == ["s1", "s2"]
    assert "s1:2" in seen
    assert queue.pending() == []


def test_review_queue_busy_keeps_fresh_flushes_stale():
    queue = ReviewQueue(max_age_seconds=60.0)
    queue.defer("fresh", {"turn": 1}, now=1000.0)
    queue.defer("stale", {"turn": 1}, now=800.0)
    drained = queue.drain(lambda: False, lambda entry: None, now=1000.0)
    assert drained == ["stale"]
    assert [e.session_id for e in queue.pending()] == ["fresh"]
    assert queue.drain(lambda: False, lambda entry: None, now=1000.0, force=True) == ["fresh"]
    assert queue.drop("missing") is False


def test_review_queue_handler_errors_isolated():
    queue = ReviewQueue()

    def _boom(entry):
        raise RuntimeError("boom")

    queue.defer("s1", {}, now=1.0)
    assert queue.drain(lambda: True, _boom, now=2.0) == []
    assert queue.pending() == []


def test_moa_redaction_and_parallel_gather():
    assert redact_reference_text("mail me at jane@example.com or (555) 123-4567") == "mail me at [email redacted] or [phone redacted]"
    assert redact_reference_text("version 2.6.1 line 42 SHA abc123 10.0.0.1") == "version 2.6.1 line 42 SHA abc123 10.0.0.1"

    calls: list[str] = []

    def _fake(advisor: str, question: str) -> str:
        calls.append(advisor)
        if advisor == "bad":
            raise RuntimeError("down")
        return f"{advisor} says: contact jane@example.com about {question}"

    refs = gather_references("Q?", ["a", "bad", "c"], _fake)
    assert [r.advisor for r in refs] == ["a", "bad", "c"]
    assert "jane@example.com" not in refs[0].text
    assert "failed" in refs[1].text
    assert sorted(calls) == ["a", "bad", "c"]

    prompt = build_aggregator_prompt("Q?", refs)
    assert "Advisor 1 (a)" in prompt and prompt.endswith("Final answer:")

    trace = run_moa_turn("Q?", ["a"], _fake)
    assert trace.trace_id.startswith("moa-") and len(trace.references) == 1
    assert trace.to_dict()["question"] == "Q?"


def test_insights_summary_and_format():
    records = [
        {"timestamp": 1700000000.0, "model": "prov/fast", "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.001, "tools": ["read_file"], "skills": ["ship-it"], "status": "ok"},
        {"timestamp": 1700003600.0, "model": "prov/fast", "input_tokens": 200, "output_tokens": 100, "cost_usd": 0.002, "tools": ["read_file", "patch"], "skills": [], "status": "ok"},
        {"timestamp": 1700007200.0, "model": "prov/strong", "input_tokens": 50, "output_tokens": 50, "cost_usd": 0.01, "tools": [], "skills": ["ship-it"], "status": "error", "days_ago": 99},
    ]
    report = summarize(records, days=30)
    assert report["runs"] == 2
    assert report["input_tokens"] == 300
    assert report["per_model"]["fast"]["runs"] == 2
    assert report["top_tools"][0] == ("read_file", 2)
    assert report["top_skills"][0] == ("ship-it", 1)
    text = format_text(report)
    assert "2 runs" in text and "fast" in text and "read_file" in text
    assert summarize([])["runs"] == 0


def test_kanban_stop_guard(monkeypatch):
    assert terminal_called([{"role": "assistant", "tool_calls": [{"function": {"name": "kanban_complete"}}]}]) is True
    assert terminal_called([{"role": "tool", "name": "kanban_block"}]) is True
    assert terminal_called([{"role": "assistant", "content": "i will do it next"}]) is False
    assert terminal_called(None) is False

    nudge = build_stop_nudge(messages=[{"role": "assistant", "content": "next..."}], attempts=0)
    assert nudge and "kanban_complete" in nudge
    assert build_stop_nudge(messages=[{"role": "assistant", "tool_calls": [{"name": "kanban_block"}]}]) is None
    assert build_stop_nudge(messages=[], attempts=5) is None

    monkeypatch.delenv("AGENT_WORKSPACE_KANBAN_STOP_NUDGE", raising=False)
    monkeypatch.delenv("AGENT_WORKSPACE_KANBAN_TASK", raising=False)
    assert stop_nudge_enabled() is False
    monkeypatch.setenv("AGENT_WORKSPACE_KANBAN_TASK", "task-1")
    assert stop_nudge_enabled() is True
    monkeypatch.setenv("AGENT_WORKSPACE_KANBAN_STOP_NUDGE", "off")
    assert stop_nudge_enabled() is False


def test_bot_turn_retry_policy():
    from agent_workspace.recovery import BOT_RETRY_COMPRESS_THEN_RESUME, BOT_RETRY_NONE, BOT_RETRY_RESUME

    assert bot_turn_retry_action("context length exceeded max tokens") == BOT_RETRY_COMPRESS_THEN_RESUME
    assert bot_turn_retry_action("model timed out", failure_reason="delivery_timeout") == BOT_RETRY_RESUME
    assert bot_turn_retry_action("429 too many requests") == BOT_RETRY_RESUME
    assert bot_turn_retry_action("401 invalid api key") == BOT_RETRY_NONE
    assert bot_turn_retry_action("monthly quota exceeded") == BOT_RETRY_NONE
    assert bot_turn_retry_action("mysterious local bug") == BOT_RETRY_NONE
