"""Tests for Trigger-Based Dynamic MicroAgents."""

from agent_workspace.skills.triggers.keyword_trigger import KeywordTrigger
from agent_workspace.skills.triggers.models import MicroAgent, TriggerContext
from agent_workspace.skills.triggers.path_trigger import PathTrigger
from agent_workspace.skills.triggers.registry import MicroAgentRegistry


def test_path_trigger_glob_matching():
    trigger = PathTrigger(patterns=["test_*.py", "tests/**", "**/*test*.py"])

    # Matches
    assert trigger.should_trigger(TriggerContext(file_paths=["tests/unit/test_auth.py"]))
    assert trigger.should_trigger(TriggerContext(file_paths=["test_main.py"]))
    assert trigger.should_trigger(TriggerContext(file_paths=["src/component_test.py"]))

    # Does not match
    assert not trigger.should_trigger(TriggerContext(file_paths=["src/auth/service.py"]))
    assert not trigger.should_trigger(TriggerContext(file_paths=[]))


def test_keyword_trigger():
    trigger = KeywordTrigger(keywords=["docker", "compose"], regex_patterns=[r"container\w*"])

    assert trigger.should_trigger(TriggerContext(query="Set up a docker container for testing"))
    assert trigger.should_trigger(TriggerContext(query="We need docker-compose setup"))
    assert trigger.should_trigger(TriggerContext(query="Inspect containers in cluster"))
    assert not trigger.should_trigger(TriggerContext(query="Refactor string helper utilities"))


def test_microagent_registry_matching_and_prompt_injection():
    registry = MicroAgentRegistry(load_defaults=True)

    # 1. Trigger python testing agent
    ctx_test = TriggerContext(file_paths=["backend/tests/test_core.py"])
    matched = registry.match(ctx_test)
    assert any(m.agent_id == "python_testing_expert" for m in matched)

    # 2. Trigger docker agent
    ctx_docker = TriggerContext(query="Configure Dockerfile for production")
    matched_docker = registry.match(ctx_docker)
    assert any(m.agent_id == "docker_container_ops" for m in matched_docker)

    # 3. Render prompt instructions
    instructions = registry.render_active_instructions(ctx_docker)
    assert "Active MicroAgent Domain Guidelines" in instructions
    assert "Docker & Container Ops" in instructions
    assert "multi-stage builds" in instructions


def test_custom_microagent_registration():
    registry = MicroAgentRegistry(load_defaults=False)

    custom_agent = MicroAgent(
        agent_id="custom_graphql",
        name="GraphQL Guru",
        priority=99,
        triggers=[PathTrigger(patterns=["**/*.graphql", "**/*.gql"])],
        content="Always write fragments and document query complexity.",
    )
    registry.register(custom_agent)

    ctx = TriggerContext(file_paths=["schema.graphql"])
    matched = registry.match(ctx)
    assert len(matched) == 1
    assert matched[0].agent_id == "custom_graphql"
