"""Built-in Cross-Enterprise Knowledge Graph LangChain Tool."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.knowledge.graph import (
    EntityType,
    KnowledgeGraph,
    RelationType,
)

_GLOBAL_KG = KnowledgeGraph()


@tool("query_knowledge_graph", parse_docstring=True)
def query_knowledge_graph(
    action: str,
    entity_id: str = "",
    entity_name: str = "",
    entity_type: str = "custom",
    source_id: str = "",
    target_id: str = "",
    relation_type: str = "depends_on",
    properties_json: str = "{}",
    max_depth: int = 4,
) -> str:
    """Manage and query the cross-enterprise Knowledge Graph for multi-hop dependencies and blast radius.

    Args:
        action: 'add_entity', 'add_relation', 'query_dependencies', 'impact_analysis', 'find_path', 'get_summary'.
        entity_id: Unique entity identifier (e.g. 'service:payment', 'repo:agent_workspace', 'vendor:stripe').
        entity_name: Human-readable name of entity.
        entity_type: Entity category ('service', 'repository', 'database', 'tool', 'person', 'vendor', 'product').
        source_id: Source entity ID when creating relationships or finding paths.
        target_id: Target entity ID when creating relationships or finding paths.
        relation_type: Type of edge ('depends_on', 'owns', 'uses', 'employs', 'calls', 'contracts_with', 'approved_by').
        properties_json: Optional JSON properties dictionary for node or edge.
        max_depth: Maximum hops to traverse during dependency or impact queries.
    """
    try:
        props = json.loads(properties_json) if properties_json else {}
    except Exception:
        props = {}

    try:
        if action == "add_entity":
            etype = EntityType(entity_type.lower()) if entity_type.lower() in [e.value for e in EntityType] else EntityType.CUSTOM
            node = _GLOBAL_KG.add_entity(
                entity_id=entity_id,
                name=entity_name or entity_id,
                entity_type=etype,
                properties=props,
            )
            return json.dumps({"status": "entity_added", "entity": node.to_dict()}, indent=2)

        elif action == "add_relation":
            rtype = RelationType(relation_type.lower()) if relation_type.lower() in [r.value for r in RelationType] else RelationType.CUSTOM
            edge = _GLOBAL_KG.add_relation(
                source_id=source_id,
                relation_type=rtype,
                target_id=target_id,
                properties=props,
            )
            return json.dumps({"status": "relation_added", "relation": edge.to_dict()}, indent=2)

        elif action == "query_dependencies":
            deps = _GLOBAL_KG.query_dependencies(entity_id=entity_id, max_depth=max_depth)
            return json.dumps(deps, indent=2)

        elif action == "impact_analysis":
            impact = _GLOBAL_KG.impact_analysis(entity_id=entity_id, max_depth=max_depth)
            return json.dumps(impact, indent=2)

        elif action == "find_path":
            path = _GLOBAL_KG.find_path(source_id=source_id, target_id=target_id)
            return json.dumps({"source_id": source_id, "target_id": target_id, "path": path}, indent=2)

        elif action == "get_summary":
            return json.dumps(_GLOBAL_KG.to_dict(), indent=2)

        else:
            return f"Error: Unknown action '{action}'."

    except Exception as exc:
        return f"Error querying knowledge graph: {exc}"
