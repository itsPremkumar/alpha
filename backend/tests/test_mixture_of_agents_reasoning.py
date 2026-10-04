import pytest
from langgraph.prebuilt import ToolRuntime

from alpha.models.moa.orchestrator import MoACandidate, MoAOrchestrator
from alpha.models.moa.redact import redact_pii_and_secrets
from alpha.models.moa.workers import build_model_worker
from alpha.tools.builtins.moa_reasoning_tool import moa_multi_model_reasoning


def _runtime(context: dict | None = None) -> ToolRuntime:
    """Minimal ``ToolRuntime`` for direct invocation.

    ``runtime`` is injected by ToolNode inside the graph; a direct ``invoke``
    has to pass one or validation fails with "runtime: Field required".
    """
    return ToolRuntime(
        state={},
        context=context or {},
        config={},
        stream_writer=lambda _: None,
        tool_call_id="tool-call-1",
        store=None,
    )


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


def test_moa_reasoning_tool(monkeypatch):
    """The tool must consult real models, and count only the ones that answered."""
    calls: list[str] = []

    class _Response:
        def __init__(self, text: str):
            self.content = text

    def _fake_factory(name=None, thinking_enabled=False, app_config=None, retries_orchestrated=False, **kwargs):
        calls.append(name)

        class _Model:
            def invoke(self, messages):
                return _Response(f"Real answer from {name}")

        return _Model()

    monkeypatch.setattr("alpha.models.factory.create_chat_model", _fake_factory)

    out = moa_multi_model_reasoning.invoke(
        {
            "runtime": _runtime(),
            "prompt": "Evaluate microservices vs monolith",
            "models_csv": "model-a,model-b",
        }
    )
    assert sorted(calls) == ["model-a", "model-b"]
    assert "Synthesized MoA Consensus (2/2 models answered)" in out
    assert "Real answer from model-a" in out
    assert "Real answer from model-b" in out
    # The stub's fixed sentence must be gone for good.
    assert "Evaluated '" not in out


def test_moa_tool_never_fabricates_when_every_model_fails(monkeypatch):
    """All-failed is a refusal, not a consensus. This was the tool's original bug."""

    def _boom(name=None, **kwargs):
        raise RuntimeError(f"no such model: {name}")

    monkeypatch.setattr("alpha.models.factory.create_chat_model", _boom)

    out = moa_multi_model_reasoning.invoke(
        {
            "runtime": _runtime(),
            "prompt": "Anything",
            "models_csv": "missing-a,missing-b",
        }
    )
    assert "no model answers" in out
    assert "Synthesized MoA Consensus" not in out
    assert "no such model" in out


def test_moa_tool_reports_partial_round_honestly(monkeypatch):
    """One model answering must not read as a multi-model consensus."""

    class _Response:
        content = "Only voice available."

    def _factory(name=None, **kwargs):
        if name == "working":

            class _Model:
                def invoke(self, messages):
                    return _Response()

            return _Model()
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr("alpha.models.factory.create_chat_model", _factory)

    out = moa_multi_model_reasoning.invoke(
        {
            "runtime": _runtime(),
            "prompt": "Partial round",
            "models_csv": "working,broken",
        }
    )
    assert "Synthesized MoA Consensus (1/2 models answered)" in out
    assert "Not consulted" in out
    assert "incomplete" in out
    assert "provider unreachable" in out


def test_build_model_worker_asks_a_real_factory(monkeypatch):
    """The worker seam must reach the real model factory with the requested name."""
    seen: dict[str, object] = {}

    class _Response:
        content = "  substantive answer  "

    def _factory(name=None, thinking_enabled=False, app_config=None, retries_orchestrated=False, **kwargs):
        seen["name"] = name
        seen["retries_orchestrated"] = retries_orchestrated

        class _Model:
            def invoke(self, messages):
                seen["messages"] = messages
                return _Response()

        return _Model()

    monkeypatch.setattr("alpha.models.factory.create_chat_model", _factory)
    worker = build_model_worker()
    out = worker("configured-model", "why is this slow?")

    # Whitespace is the orchestrator's concern (it strips when building blocks);
    # the worker returns the model's own text so a caller can see it verbatim.
    assert out.strip() == "substantive answer"
    assert seen["name"] == "configured-model"
    # Nothing above this tool owns a retry, so the provider keeps its own.
    assert seen["retries_orchestrated"] is False
    assert seen["messages"][-1]["content"] == "why is this slow?"


def test_build_model_worker_treats_empty_response_as_failure(monkeypatch):
    """An empty completion is not a perspective; it must not become a blank answer."""

    class _Empty:
        content = "   "

    def _factory(name=None, **kwargs):
        class _Model:
            def invoke(self, messages):
                return _Empty()

        return _Model()

    monkeypatch.setattr("alpha.models.factory.create_chat_model", _factory)
    with pytest.raises(RuntimeError, match="empty response"):
        build_model_worker()("m", "prompt")


def test_build_model_worker_reports_failure_to_observer(monkeypatch):
    """The real cause must reach the caller, not just become an opaque failure."""

    def _factory(name=None, **kwargs):
        raise ValueError("missing provider config")

    seen: list[tuple[str, str]] = []
    monkeypatch.setattr("alpha.models.factory.create_chat_model", _factory)
    worker = build_model_worker(on_error=lambda n, e: seen.append((n, str(e))))
    with pytest.raises(ValueError):
        worker("m1", "prompt")
    assert seen == [("m1", "missing provider config")]
