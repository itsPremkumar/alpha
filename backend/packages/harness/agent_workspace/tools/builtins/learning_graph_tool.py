"""Built-in Learning Graph tool inspired by Hermes Agent."""

from __future__ import annotations

import json

from langchain.tools import tool

from agent_workspace.learning.curator import LearningGraphCurator
from agent_workspace.learning.graph import KnowledgeNode
from agent_workspace.learning.store import get_learning_graph_store


@tool("learning_graph_manage", parse_docstring=True)
def learning_graph_manage(
    action: str = "query",
    text: str = "",
    title: str = "",
    category: str = "general",
    pinned: bool = False,
) -> str:
    """Manage and query the self-evolving Knowledge & Learning Graph.

    Maintains topological connections across learned user preferences, codebase invariants,
    bug mitigations, and skills.

    Args:
        action: Operational action: 'query' (search graph), 'record' (store new knowledge), 'stats' (topological density), or 'curate' (prune decayed nodes).
        text: Query text to search, or content body when recording a new node.
        title: Short title when recording new knowledge.
        category: Category domain (e.g. 'codebase', 'testing', 'architecture', 'user_pref').
        pinned: Whether to pin this node to prevent future decay pruning.
    """
    store = get_learning_graph_store()
    graph = store.get_graph()

    act = action.lower().strip()
    if act == "query":
        results = graph.query(text=text, category=category if category != "general" else None)
        store.save(graph)
        if not results:
            return f"No knowledge nodes found matching '{text}'."
        out = []
        for r in results:
            related_nodes = graph.get_related(r.node_id)
            rel_str = f" [Linked: {', '.join(n.title for n in related_nodes)}]" if related_nodes else ""
            out.append(f"- **[{r.category.upper()}] {r.title}** (uses: {r.use_count}): {r.content}{rel_str}")
        return "\n".join(out)

    elif act == "record":
        if not title:
            title = text[:40] + "..." if len(text) > 40 else text
        node = KnowledgeNode(title=title, content=text, category=category, pinned=pinned)
        graph.add_node(node)
        graph.auto_link_lexical()
        store.save(graph)
        return f"Recorded knowledge node '{node.title}' [ID: {node.node_id}] in category '{category}'."

    elif act == "stats":
        stats = graph.density_stats()
        return json.dumps(stats, indent=2)

    elif act == "curate":
        curator = LearningGraphCurator()
        pruned = curator.prune_decayed(graph)
        merged = curator.merge_duplicates(graph)
        store.save(graph)
        return f"Curator run completed: pruned {pruned} stale nodes, merged {merged} near-duplicates."

    return f"Unknown action '{action}'. Use 'query', 'record', 'stats', or 'curate'."
