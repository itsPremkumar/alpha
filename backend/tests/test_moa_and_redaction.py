"""Tests for Mixture-of-Agents (MoA) and PII Redaction."""

from agent_workspace.deliberation.moa_engine import MoAEngine, PIIFilter


def test_pii_filter_redaction():
    sample = "Contact me at alice@example.com or (555) 123-4567. Key: Bearer sk-ant-api03-abcdef123456789012345678"
    cleaned = PIIFilter.redact(sample)
    assert "alice@example.com" not in cleaned
    assert "[redacted email]" in cleaned
    assert "(555) 123-4567" not in cleaned
    assert "[redacted phone]" in cleaned
    assert "sk-ant-api" not in cleaned
    assert "[redacted credential]" in cleaned


def test_moa_advisory_pass_and_consensus():
    engine = MoAEngine()
    engine.register_advisor("perf_expert", "Performance", lambda q: "Use asynchronous streaming with connection pooling for low latency.")
    engine.register_advisor("security_expert", "Security", lambda q: "Ensure input sanitization and rate limiting with connection pooling.")

    advisories = engine.execute_advisory_pass("How to design the gateway?")
    assert len(advisories) == 2

    consensus = engine.synthesize_consensus("How to design the gateway?", advisories)
    assert consensus.total_advisors == 2
    assert consensus.agreement_score > 0.0
    assert "Synthesized consensus" in consensus.consensus_summary
