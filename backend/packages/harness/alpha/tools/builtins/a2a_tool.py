"""Built-in LangChain tool for Google A2A Cross-Agent Interoperability."""

from __future__ import annotations

import json

from langchain.tools import tool

from alpha.protocols.a2a import (
    A2ADelegationRequest,
    A2AProtocolAdapter,
    AgentCapabilityCard,
)

_GLOBAL_A2A = A2AProtocolAdapter()

# Pre-populate default system specialist cards
_GLOBAL_A2A.register_card(
    AgentCapabilityCard(
        agent_id="agent-researcher",
        name="Lead Research Specialist",
        description="Deep scientific & web research, citation extraction, source verification.",
        skills=["research", "fact_checking", "summarization"],
        availability="available",
    )
)
_GLOBAL_A2A.register_card(
    AgentCapabilityCard(
        agent_id="agent-security-auditor",
        name="Adversarial Security Auditor",
        description="AST code audit, CVE scanning, secret leakage detection, permission checks.",
        skills=["security", "audit", "vulnerability_scan"],
        availability="available",
    )
)


@tool("a2a_protocol", parse_docstring=True)
def a2a_tool(
    action: str,
    agent_id: str = "",
    target_agent_id: str = "",
    task_objective: str = "",
    skill_filter: str = "",
    card_json: str = "{}",
) -> str:
    """Discover agents, inspect Google A2A capability cards, and delegate tasks.

    Args:
        action: 'list_agents', 'inspect_card', 'delegate', 'register_card'.
        agent_id: Identifier of the agent to inspect or register.
        target_agent_id: Target agent identifier to delegate work to.
        task_objective: High-level goal or task prompt for delegation.
        skill_filter: Optional filter when querying available agents by skill.
        card_json: JSON data representing an AgentCapabilityCard to register.
    """
    try:
        if action == "list_agents":
            cards = _GLOBAL_A2A.list_cards(skill_filter=skill_filter or None)
            return json.dumps([c.model_dump() for c in cards], indent=2)

        elif action == "inspect_card":
            target = agent_id or target_agent_id
            if not target:
                return "Error: 'agent_id' is required for inspect_card."
            card = _GLOBAL_A2A.get_card(target)
            if not card:
                return f"Error: Agent '{target}' not found in A2A registry."
            return json.dumps(card.model_dump(), indent=2)

        elif action == "delegate":
            if not target_agent_id or not task_objective:
                return "Error: 'target_agent_id' and 'task_objective' are required for delegate."
            req = A2ADelegationRequest(
                sender_agent_id=agent_id or "primary-agent",
                target_agent_id=target_agent_id,
                task_objective=task_objective,
            )
            res = _GLOBAL_A2A.delegate(req)
            return json.dumps(res.model_dump(), indent=2)

        elif action == "register_card":
            try:
                data = json.loads(card_json)
                card = AgentCapabilityCard(**data)
                _GLOBAL_A2A.register_card(card)
                return json.dumps({"status": "card_registered", "card": card.model_dump()}, indent=2)
            except Exception as exc:
                return f"Error parsing capability card: {exc}"

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error executing a2a_protocol tool: {exc}"
