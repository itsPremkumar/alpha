from alpha.models.moa.orchestrator import MoACandidate, MoAOrchestrator
from alpha.models.moa.redact import redact_pii_and_secrets
from alpha.tools.builtins.moa_reasoning_tool import moa_multi_model_reasoning


def test_redact_pii_and_secrets():
    text = "Contact alice@example.com or call 555-123-4567. Key: sk-ant-api03-abcdef1234567890abcdef"
    redacted = redact_pii_and_secrets(text)
    assert "alice@example.com" not in redacted
    assert "[redacted email]" in redacted
    assert "555-123-4567" not in redacted
    assert "[redacted phone]" in redacted
    assert "sk-ant" not in redacted


def test_moa_parallel_orchestrator():
    orchestrator = MoAOrchestrator(max_workers=3)
    models = ["claude", "gpt4", "deepseek"]

    def worker(m: str, prompt: str) -> str:
        return f"Response from {m} for prompt: {prompt}"

    res = orchestrator.execute_moa_round(
        prompt="Explain Rust ownership",
        candidate_models=models,
        worker_fn=worker,
    )

    assert len(res.candidates) == 3
    assert all(c.success for c in res.candidates)
    assert "Synthesized MoA Consensus (3 models)" in res.consensus_response
    assert "Perspective from `claude`" in res.consensus_response
    assert "Perspective from `deepseek`" in res.consensus_response


def test_moa_custom_aggregator_and_worker_error():
    orchestrator = MoAOrchestrator()
    models = ["healthy_model", "failing_model"]

    def worker(m: str, prompt: str) -> str:
        if m == "failing_model":
            raise RuntimeError("API timeout")
        return f"Valid answer from {m}"

    def custom_aggregator(prompt: str, candidates: list[MoACandidate]) -> str:
        succ = [c for c in candidates if c.success]
        return f"Aggregated {len(succ)}/{len(candidates)} answers."

    res = orchestrator.execute_moa_round(
        prompt="Design cache",
        candidate_models=models,
        worker_fn=worker,
        aggregator_fn=custom_aggregator,
    )

    assert res.consensus_response == "Aggregated 1/2 answers."


def test_moa_reasoning_tool():
    out = moa_multi_model_reasoning.invoke({
        "prompt": "Evaluate microservices vs monolith",
        "models_csv": "model-a,model-b",
    })
    assert "Synthesized MoA Consensus" in out
    assert "model-a" in out
    assert "model-b" in out
