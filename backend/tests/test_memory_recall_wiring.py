"""The lead-agent prompt seam for wave-2 typed memory recall.

`recall_composition` proves the composition rules in isolation. These tests pin
the thing that actually matters for a user: the `<memory>` block the agent
receives. Specifically, that a type's recall reaches the prompt when it is on,
and that leaving every type off leaves the block byte-identical to what the
already-landed backend + L1 path alone produce.

Every collaborator the prompt reads (memory manager, cognitive system, L1
pipeline) is stubbed, so these tests assert the SEAM and not the contents of
any store. The stores have their own suites.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from alpha.agents.lead_agent import prompt as prompt_module
from alpha.config.memory_config import MemoryConfig


class _StubManager:
    def get_context(self, *, user_id: str, agent_name: str | None) -> str:
        return "### Backend memory\n- stub backend fact"


class _StubPipeline:
    def recall(self, *, user_id: str, agent_name: str) -> str:
        return "### L1 working memory\n- stub l1 record"


class _EmptyList:
    def __init__(self, value: list[Any]) -> None:
        self._value = value

    def list_active(self, **_kwargs: Any) -> list[Any]:
        return self._value

    def list_skills(self, **_kwargs: Any) -> list[Any]:
        return self._value


class _StubCognitive:
    working_mem = _EmptyList([])
    procedural_mem = _EmptyList([])


class _StubCognitiveSystem:
    working_mem = _EmptyList([])
    procedural_mem = _EmptyList([])


@pytest.fixture
def collaborators(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub every disk-touching collaborator the prompt reads."""
    import alpha.agents.memory as agents_memory
    import alpha.agents.memory.l1.pipeline as l1_pipeline
    import alpha.memory.cognitive as cognitive

    monkeypatch.setattr(agents_memory, "get_memory_manager", lambda: _StubManager())
    monkeypatch.setattr(cognitive, "get_cognitive_memory_system", lambda: _StubCognitiveSystem())
    monkeypatch.setattr(l1_pipeline, "get_l1_pipeline", lambda: _StubPipeline())
    monkeypatch.setattr(l1_pipeline, "get_bound_l1_pipeline", lambda config: _StubPipeline())


def _config(**sections: Any) -> MemoryConfig:
    payload: dict[str, Any] = {"enabled": True, "injection_enabled": True}
    payload.update(sections)
    return MemoryConfig.model_validate(payload)


def _context(config: MemoryConfig, monkeypatch: pytest.MonkeyPatch) -> str:
    import alpha.config.memory_config as memory_config_module

    monkeypatch.setattr(memory_config_module, "get_memory_config", lambda: config)
    return prompt_module._get_memory_context("lead", user_id="u1")


def test_scenario_routing_uses_current_request_and_only_enabled_real_surfaces(
    collaborators: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    import alpha.memory.recall_composition as rc

    original = rc._RENDERERS["narrative"]
    rc._RENDERERS["narrative"] = lambda *a, **k: "### Life story\n- coding preference"
    try:
        config = _config(narrative={"enabled": True}, scenarios={"enabled": True, "storage_path": str(tmp_path)})
        import alpha.config.memory_config as memory_config_module

        monkeypatch.setattr(memory_config_module, "get_memory_config", lambda: config)
        block = prompt_module._get_memory_context(
            "lead",
            user_id="u1",
            declared_intent="Fix this Python error; distinctive phrase stays private",
            thread_id="thread-123",
        )
    finally:
        rc._RENDERERS["narrative"] = original

    assert "scenario=coding" in block
    assert "surfaces=narrative" in block
    assert "### Life story" in block
    assert "unavailable_surfaces" not in block
    entries = [json.loads(line) for path in tmp_path.rglob("*.jsonl") for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["thread_id"] == "thread-123"
    assert "distinctive phrase stays private" not in json.dumps(entries[0])


def test_persisted_narrative_reaches_routed_prompt_only_for_its_owner(
    collaborators: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from alpha.memory.narrative import NarrativeConfig, NarrativeEvent, NarrativeMemory

    storage_path = tmp_path / "narrative"
    narrative_config = NarrativeConfig(
        enabled=True,
        storage_path=str(storage_path),
        min_importance=0,
        lookback_days=10_000,
    )
    memory = NarrativeMemory(config=narrative_config)
    event = NarrativeEvent(
        id="synthetic-memory-e2e-event",
        scope="user",
        scope_id="owner-a",
        period_start=100,
        period_end=100,
        title="Synthetic Alpha memory acceptance",
        summary="Owner A uses an isolated fixture for narrative recall verification.",
        importance=90,
        source_refs=["synthetic-memory-e2e-source"],
        created_at=100,
    )
    assert memory.ingest(event, scope="user", scope_id="owner-a", now=100).stored == 1
    assert memory.regenerate(scope="user", scope_id="owner-a", now=100).status == "ok"

    config = _config(
        narrative=narrative_config.model_dump(mode="python"),
        scenarios={"enabled": True, "storage_path": str(tmp_path / "routing")},
    )
    import alpha.config.memory_config as memory_config_module

    monkeypatch.setattr(memory_config_module, "get_memory_config", lambda: config)
    owner_a = prompt_module._get_memory_context("lead", user_id="owner-a", declared_intent="Fix this Python error")
    owner_b = prompt_module._get_memory_context("lead", user_id="owner-b", declared_intent="Fix this Python error")

    assert "scenario=coding" in owner_a
    assert "Synthetic Alpha memory acceptance" in owner_a
    assert "Owner A uses an isolated fixture" in owner_a
    assert "Synthetic Alpha memory acceptance" not in owner_b


def test_all_types_off_leaves_the_existing_block_untouched(collaborators: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default-OFF must be invisible in the real prompt, not just in isolation."""
    block = _context(_config(l1={"enabled": True}), monkeypatch)
    assert "### Backend memory" in block
    assert "### L1 working memory" in block
    # No heading, notice or placeholder from any disabled type.
    assert "disabled by configuration" not in block
    assert "truncated at the configured cap" not in block
    assert block.count("<memory>") == 1


def test_enabled_type_with_content_reaches_the_prompt(collaborators: None, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The seam is real: an enabled type's own text lands inside <memory>."""
    import alpha.memory.recall_composition as rc

    original = rc._RENDERERS["narrative"]
    rc._RENDERERS["narrative"] = lambda *a, **k: "### Life story\n- stub chapter"
    try:
        config = _config(l1={"enabled": True}, narrative={"enabled": True})
        block = _context(config, monkeypatch)
    finally:
        rc._RENDERERS["narrative"] = original

    assert "### Life story" in block
    assert "### Backend memory" in block
    assert "### L1 working memory" in block
    assert block.index("### Backend memory") < block.index("### Life story")


def test_a_raising_type_does_not_break_the_memory_block(collaborators: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Worst case: every type explodes. The user still gets backend + L1."""
    import alpha.memory.recall_composition as rc

    saved = dict(rc._RENDERERS)

    def boom(*args: object, **kwargs: object) -> str:
        msg = "store unavailable"
        raise OSError(msg)

    for name in rc.SURFACE_ORDER:
        rc._RENDERERS[name] = boom
    try:
        config = _config(l1={"enabled": True}, **{n: {"enabled": True} for n in rc.SURFACE_ORDER})
        block = _context(config, monkeypatch)
    finally:
        rc._RENDERERS.clear()
        rc._RENDERERS.update(saved)

    assert "### Backend memory" in block
    assert "### L1 working memory" in block
    assert "store unavailable" not in block


def test_host_memory_disabled_yields_no_block(collaborators: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The master switch still wins: no host memory, no <memory> block at all."""
    config = MemoryConfig.model_validate({"enabled": False, "narrative": {"enabled": True}})
    assert _context(config, monkeypatch) == ""


def test_injection_disabled_yields_no_block(collaborators: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """`injection_enabled: false` must stay a real off-switch, as before."""
    config = MemoryConfig.model_validate({"enabled": True, "injection_enabled": False, "narrative": {"enabled": True}})
    assert _context(config, monkeypatch) == ""


def test_opt_in_codebase_memory_refreshes_explicit_workspace_and_reaches_prompt(
    collaborators: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = "def calculate_memory_budget(query: str) -> int:\n    return len(query)\n"
    (repo / "memory_budget.py").write_text(source, encoding="utf-8")
    import alpha.config.memory_config as memory_config_module

    config = _config(codebase={"enabled": True, "storage_path": str(tmp_path / "state")})
    monkeypatch.setattr(memory_config_module, "get_memory_config", lambda: config)

    app_config = type("AppConfigStub", (), {"memory": config})()
    kwargs = {
        "app_config": app_config,
        "user_id": "owner-a",
        "declared_intent": "Find calculate_memory_budget in this repository",
        "repo_root": str(repo),
    }
    prompt_module._get_memory_context("lead", **kwargs)

    import time

    snapshot_root = tmp_path / "state"
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not list(snapshot_root.rglob("snapshots.json")):
        time.sleep(0.01)
    block = prompt_module._get_memory_context("lead", **kwargs)

    assert "Current repository structure" in block
    assert "memory_budget.py" in block
    assert "calculate_memory_budget" in block
    assert "return len(query)" not in block
    assert list(snapshot_root.rglob("snapshots.json"))


def test_codebase_memory_never_scans_without_explicit_root(collaborators: None, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    config = _config(codebase={"enabled": True, "storage_path": str(tmp_path / "state")})
    import alpha.config.memory_config as memory_config_module

    monkeypatch.setattr(memory_config_module, "get_memory_config", lambda: config)
    block = prompt_module._get_memory_context(
        "lead",
        app_config=type("AppConfigStub", (), {"memory": config})(),
        user_id="owner-a",
        declared_intent="Find hidden files",
    )

    assert "Current repository structure" not in block
    assert list((tmp_path / "state").rglob("snapshots.json")) == []


def test_composition_import_failure_is_swallowed(collaborators: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even a broken composition module must not cost the backend + L1 block."""
    import builtins

    real_import = builtins.__import__

    def blocked(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "alpha.memory.recall_composition":
            msg = "composition module unavailable"
            raise ImportError(msg)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    config = _config(l1={"enabled": True}, narrative={"enabled": True})
    block = _context(config, monkeypatch)
    assert "### Backend memory" in block
    assert "### L1 working memory" in block
