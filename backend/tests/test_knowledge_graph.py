from agent_workspace.knowledge.graph import (
    EntityType,
    KnowledgeGraph,
    RelationType,
)


def test_knowledge_graph_construction_and_multi_hop_query():
    kg = KnowledgeGraph()

    # Entities: Company A owns Product B; Product B uses Tool D; Tool D contracts with Vendor E
    kg.add_entity("company:corp", "Alpha Corp", EntityType.CUSTOM)
    kg.add_entity("product:checkout", "Checkout Service", EntityType.PRODUCT)
    kg.add_entity("tool:stripe_gateway", "Stripe API", EntityType.TOOL)
    kg.add_entity("vendor:stripe", "Stripe Inc", EntityType.VENDOR)
    kg.add_entity("person:alice", "Alice Engineer", EntityType.PERSON)

    # Relations
    kg.add_relation("company:corp", RelationType.OWNS, "product:checkout")
    kg.add_relation("product:checkout", RelationType.USES, "tool:stripe_gateway")
    kg.add_relation("tool:stripe_gateway", RelationType.CONTRACTS_WITH, "vendor:stripe")
    kg.add_relation("company:corp", RelationType.EMPLOYS, "person:alice")

    # Upstream Dependency Query: What does product:checkout depend on?
    deps = kg.query_dependencies("product:checkout", max_depth=3)
    assert deps["total_dependencies"] == 2
    dep_ids = [d["entity"]["entity_id"] for d in deps["dependencies"]]
    assert "tool:stripe_gateway" in dep_ids
    assert "vendor:stripe" in dep_ids

    # Blast-Radius Impact Analysis: If vendor:stripe is down, what is affected?
    impact = kg.impact_analysis("vendor:stripe", max_depth=3)
    assert impact["blast_radius_count"] >= 2
    affected_ids = [a["entity"]["entity_id"] for a in impact["affected_systems"]]
    assert "tool:stripe_gateway" in affected_ids
    assert "product:checkout" in affected_ids

    # Path Finding
    path = kg.find_path("company:corp", "vendor:stripe")
    assert path == ["company:corp", "product:checkout", "tool:stripe_gateway", "vendor:stripe"]
