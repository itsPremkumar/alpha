"""MicroAgentRegistry: Discovers, manages, and resolves triggered micro-agents."""

from __future__ import annotations

import logging

from agent_workspace.skills.triggers.keyword_trigger import KeywordTrigger
from agent_workspace.skills.triggers.models import MicroAgent, TriggerContext
from agent_workspace.skills.triggers.path_trigger import PathTrigger

logger = logging.getLogger(__name__)


class MicroAgentRegistry:
    """Registry maintaining MicroAgents and evaluating triggers against execution context."""

    def __init__(self, load_defaults: bool = True):
        self._agents: dict[str, MicroAgent] = {}
        if load_defaults:
            self._register_default_microagents()

    def register(self, agent: MicroAgent) -> None:
        self._agents[agent.agent_id] = agent

    def unregister(self, agent_id: str) -> MicroAgent | None:
        return self._agents.pop(agent_id, None)

    def get(self, agent_id: str) -> MicroAgent | None:
        return self._agents.get(agent_id)

    def list_all(self) -> list[MicroAgent]:
        return list(self._agents.values())

    def match(self, context: TriggerContext) -> list[MicroAgent]:
        """Find all micro-agents triggered by the given context, ordered by priority descending."""
        matched: list[MicroAgent] = []
        for agent in self._agents.values():
            if agent.matches(context):
                matched.append(agent)

        matched.sort(key=lambda a: a.priority, reverse=True)
        return matched

    def render_active_instructions(self, context: TriggerContext) -> str:
        """Render a concatenated prompt section of all active triggered micro-agents."""
        matched = self.match(context)
        if not matched:
            return ""

        sections = ["## Active MicroAgent Domain Guidelines\n"]
        for agent in matched:
            sections.append(f"### MicroAgent: {agent.name}\n{agent.content.strip()}\n")

        return "\n".join(sections)

    def _register_default_microagents(self) -> None:
        """Populate standard engineering micro-agents."""
        # 1. Python Pytest & Quality MicroAgent
        self.register(
            MicroAgent(
                agent_id="python_testing_expert",
                name="Python Testing & Verification",
                priority=100,
                triggers=[
                    PathTrigger(patterns=["test_*.py", "*_test.py", "tests/**", "**/*test*.py"]),
                    KeywordTrigger(keywords=["pytest", "unit test", "coverage", "mock"]),
                ],
                content=(
                    "- Always run pytest using the virtualenv runner.\n"
                    "- Use monkeypatch or unittest.mock rather than altering real environments.\n"
                    "- Test both happy paths and edge/error cases (empty inputs, invalid syntax, boundary limits).\n"
                    "- Assert specific error messages, not just generic exception types."
                ),
            )
        )

        # 2. Docker & Container MicroAgent
        self.register(
            MicroAgent(
                agent_id="docker_container_ops",
                name="Docker & Container Ops",
                priority=90,
                triggers=[
                    PathTrigger(patterns=["Dockerfile*", "docker-compose*.yml", "docker-compose*.yaml"]),
                    KeywordTrigger(keywords=["docker", "container", "dockerfile", "compose"]),
                ],
                content=(
                    "- Leverage multi-stage builds to keep production images minimal.\n"
                    "- Never embed secrets or credentials inside Dockerfiles.\n"
                    "- Pin base image tags to specific digests or stable versions, avoiding ':latest' in production."
                ),
            )
        )

        # 3. Database Migration MicroAgent
        self.register(
            MicroAgent(
                agent_id="database_migrations",
                name="Database Schema & Migrations",
                priority=85,
                triggers=[
                    PathTrigger(patterns=["**/migrations/**", "alembic/**", "**/*migration*.py"]),
                    KeywordTrigger(keywords=["migration", "schema", "alembic", "alter table"]),
                ],
                content=(
                    "- Ensure migrations are backward-compatible (expand before contract).\n"
                    "- Provide both 'upgrade' and 'downgrade' handlers.\n"
                    "- Never execute unindexed table-locks on high-traffic production databases."
                ),
            )
        )

        # 4. Asyncio & Concurrency MicroAgent
        self.register(
            MicroAgent(
                agent_id="asyncio_concurrency",
                name="AsyncIO & Concurrency Best Practices",
                priority=80,
                triggers=[
                    KeywordTrigger(keywords=["asyncio", "async def", "await", "coroutine", "event loop"]),
                ],
                content=(
                    "- Never call blocking I/O (e.g. time.sleep, requests.get) inside async coroutines; use asyncio.sleep or httpx.\n"
                    "- Guard shared mutable state with asyncio.Lock.\n"
                    "- Ensure all spawned Tasks are either awaited or held in a strong reference set to avoid silent garbage collection."
                ),
            )
        )
