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


def _build_cards_from_bots() -> list[AgentCapabilityCard]:
    """Build A2A capability cards from the real BotRegistry.

    Each card carries the bot's actual capabilities and skills — never
    invented ones.  Availability is "active" for running bots, "offline"
    for archived ones.
    """
    try:
        from alpha.bots.registry import BotRegistry

        registry = BotRegistry()
        cards = []
        for bot in registry.list_bots(include_archived=False):
            cards.append(
                AgentCapabilityCard(
                    agent_id=f"bot:{bot.name}",
                    name=bot.display_name,
                    description=bot.role[:200] if bot.role else bot.display_name,
                    skills=list(bot.skills)[:20],
                    availability="available" if bot.status == "active" else "offline",
                )
            )
        return cards
    except Exception:
        return []


def _build_cards_from_peers() -> list[AgentCapabilityCard]:
    """Build A2A capability cards from paired PeerNetworkService peers.

    Only paired peers are included — discovered-but-unpaired agents are
    not dispatchable and must not appear as available.
    """
    try:
        from alpha.peer_network.service import PeerNetworkService

        service = PeerNetworkService()
        import asyncio

        peers = asyncio.run(service.list_peers(trust="paired"))
        cards = []
        for peer in peers:
            agent_id = peer.get("agent_id", "")
            if not agent_id:
                continue
            cards.append(
                AgentCapabilityCard(
                    agent_id=agent_id,
                    name=peer.get("name", agent_id),
                    description=(peer.get("description") or "")[:200],
                    skills=[s.get("name", "") for s in peer.get("skills", []) if s.get("name")][:20],
                    availability="available",
                )
            )
        return cards
    except Exception:
        return []


def refresh_cards() -> list[AgentCapabilityCard]:
    """Rebuild the card registry from real sources.

    Called once at tool registration and available as a tool action so an
    operator can refresh after pairing a new peer or registering a bot.
    """
    cards = _build_cards_from_bots() + _build_cards_from_peers()
    for card in cards:
        _GLOBAL_A2A.register_card(card)
    return cards


# Build cards from real sources at module load.
refresh_cards()


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

    Cards are built from the real BotRegistry and paired PeerNetworkService
    peers — never pre-populated with fictional agents.

    Args:
        action: 'list_agents', 'inspect_card', 'delegate', 'register_card', 'refresh_cards'.
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

        elif action == "refresh_cards":
            cards = refresh_cards()
            return json.dumps({"status": "refreshed", "count": len(cards), "cards": [c.model_dump() for c in cards]}, indent=2)

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error executing a2a_protocol tool: {exc}"
