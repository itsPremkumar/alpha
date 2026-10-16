"""Normal agent lifecycle integration for explicit prospective reminders."""

from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from alpha.agents.lead_agent import agent as lead_agent_module
from alpha.agents.middlewares.prospective_memory_middleware import (
    ProspectiveMemoryMiddleware,
    extract_explicit_reminder,
)
from alpha.config.app_config import AppConfig
from alpha.config.memory_config import MemoryConfig
from alpha.config.model_config import ModelConfig
from alpha.config.sandbox_config import SandboxConfig
from alpha.memory.prospective.store import ProspectiveStore
from alpha.memory.recall_composition import compose_typed_memory_blocks


def _config(tmp_path, *, enabled: bool = True) -> MemoryConfig:
    return MemoryConfig.model_validate(
        {
            "enabled": True,
            "prospective": {"enabled": enabled, "storage_path": str(tmp_path)},
        }
    )


def test_extracts_only_explicit_latest_human_reminder() -> None:
    assert extract_explicit_reminder([HumanMessage(content="Remind me to review the release"), AIMessage(content="Done")]) == "review the release"
    assert extract_explicit_reminder([HumanMessage(content="We should review the release")]) is None
    assert extract_explicit_reminder([HumanMessage(content="Remind me to\nreview later")]) is None
    assert extract_explicit_reminder([HumanMessage(content="Remind me to " + "x" * 500)]) == "x" * 400


def test_after_agent_captures_idempotently_with_owner_and_agent_scope(tmp_path) -> None:
    config = _config(tmp_path)
    middleware = ProspectiveMemoryMiddleware("researcher", memory_config=config)
    runtime = SimpleNamespace(context={"thread_id": "thread-a", "run_id": "run-a", "user_id": "owner-a"})
    state = {"messages": [HumanMessage(id="message-a", content="Please remind me to check the deployment")]}

    middleware.after_agent(state, runtime)
    middleware.after_agent(state, runtime)

    store = ProspectiveStore(config.prospective)
    items = store.list_items("owner-a", agent_name="researcher")
    assert len(items) == 1
    assert items[0].content == "check the deployment"
    assert items[0].source == {"kind": "explicit_user_request", "thread_id": "thread-a"}
    assert store.list_items("owner-b") == []
    assert store.list_items("owner-a", agent_name="another-agent") == []
    recalled = compose_typed_memory_blocks(config, user_id="owner-a", agent_name="researcher", surfaces=("prospective",))
    assert "check the deployment" in recalled.text
    assert "check the deployment" not in compose_typed_memory_blocks(config, user_id="owner-b", agent_name="researcher", surfaces=("prospective",)).text


def test_capture_stamps_bounded_due_time_metadata(tmp_path) -> None:
    config = _config(tmp_path)
    middleware = ProspectiveMemoryMiddleware(memory_config=config)
    runtime = SimpleNamespace(context={"thread_id": "thread-b", "run_id": "run-b", "user_id": "owner-a"})
    state = {"messages": [HumanMessage(id="message-b", content="Please remind me to check the deployment tomorrow at 5pm")]}

    middleware.after_agent(state, runtime)

    store = ProspectiveStore(config.prospective)
    items = store.list_items("owner-a")
    assert len(items) == 1
    item = items[0]
    assert item.due_at is not None
    assert item.metadata["due_rule"] == "tomorrow_next_day+clock"
    assert item.metadata["due_phrase"] == "tomorrow"
    assert item.metadata["due_at"] == item.due_at
    assert item.metadata["capture_method"] == "explicit_reminder_phrase"


def test_capture_with_ambiguous_due_time_stays_undated(tmp_path) -> None:
    config = _config(tmp_path)
    middleware = ProspectiveMemoryMiddleware(memory_config=config)
    runtime = SimpleNamespace(context={"thread_id": "thread-c", "run_id": "run-c", "user_id": "owner-a"})
    middleware.after_agent(
        {"messages": [HumanMessage(id="message-c", content="Please remind me to call mom")]},
        runtime,
    )

    store = ProspectiveStore(config.prospective)
    items = store.list_items("owner-a")
    assert len(items) == 1
    assert items[0].due_at is None
    assert items[0].metadata == {"capture_method": "explicit_reminder_phrase"}


def test_capture_is_default_off(tmp_path) -> None:
    config = _config(tmp_path, enabled=False)
    middleware = ProspectiveMemoryMiddleware(memory_config=config)
    middleware.after_agent(
        {"messages": [HumanMessage(id="m", content="Remind me to call the team")]},
        SimpleNamespace(context={"thread_id": "t", "run_id": "r", "user_id": "u"}),
    )
    assert ProspectiveStore(config.prospective).list_items("u") == []


def test_lead_middleware_factory_registers_capture_only_when_both_gates_are_enabled(tmp_path, monkeypatch) -> None:
    model = ModelConfig(
        name="test-model",
        display_name="test-model",
        description=None,
        use="langchain_openai:ChatOpenAI",
        model="test-model",
        supports_thinking=False,
        supports_vision=False,
    )
    memory = _config(tmp_path, enabled=True)
    app_config = AppConfig(
        models=[model],
        sandbox=SandboxConfig(use="alpha.sandbox.local:LocalSandboxProvider"),
        memory=memory,
    )
    monkeypatch.setattr(lead_agent_module, "_create_summarization_middleware", lambda **kwargs: None)
    monkeypatch.setattr(lead_agent_module, "_create_todo_list_middleware", lambda is_plan_mode: None)
    monkeypatch.setattr(
        "alpha.extensions.get_agent_build_extensions",
        lambda: SimpleNamespace(has_middleware_contributors=False),
    )

    middleware = lead_agent_module.build_middlewares(
        {"configurable": {"model_name": "test-model", "is_plan_mode": False}},
        model_name="test-model",
        agent_name="specialist",
        app_config=app_config,
    )

    assert sum(isinstance(item, ProspectiveMemoryMiddleware) for item in middleware) == 1

    memory.prospective.enabled = False
    disabled_middleware = lead_agent_module.build_middlewares(
        {"configurable": {"model_name": "test-model", "is_plan_mode": False}},
        model_name="test-model",
        app_config=app_config,
    )
    assert not any(isinstance(item, ProspectiveMemoryMiddleware) for item in disabled_middleware)

    memory.prospective.enabled = True
    memory.enabled = False
    master_disabled_middleware = lead_agent_module.build_middlewares(
        {"configurable": {"model_name": "test-model", "is_plan_mode": False}},
        model_name="test-model",
        app_config=app_config,
    )
    assert not any(isinstance(item, ProspectiveMemoryMiddleware) for item in master_disabled_middleware)
