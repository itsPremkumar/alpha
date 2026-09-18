"""Tests for Multi-Repo Semantic Knowledge Lake and Symbol Tracer."""

from agent_workspace.knowledge.codebase_knowledge_lake import (
    CodebaseKnowledgeLake,
    CrossServiceSymbolTracer,
)


def test_ast_code_chunking_and_hybrid_search():
    lake = CodebaseKnowledgeLake()
    sample_code = '''
def calculate_checkpoint_hash(data: str) -> str:
    # Calculates SHA-256 hash for workspace snapshot
    import hashlib
    return hashlib.sha256(data.encode()).hexdigest()

class CheckpointStore:
    # Manages persistent storage for checkpoints
    pass
'''
    chunks_count = lake.ingest_code("core_repo", "checkpoints.py", sample_code)
    assert chunks_count >= 2

    # Query for hash computation
    results = lake.search_knowledge("calculate checkpoint hash sha256", top_k=2)
    assert len(results) >= 1
    top_chunk = results[0]["chunk"]
    assert top_chunk["symbol_name"] == "calculate_checkpoint_hash"
    assert results[0]["relevance_score"] > 0.2


def test_cross_service_symbol_tracer():
    frontend_ts = """
async function loadCheckpoints() {
    const res = await fetch('/api/checkpoints');
    return res.json();
}
"""
    backend_py = """
@router.get('/api/checkpoints')
def get_checkpoints():
    return []
"""
    links = CrossServiceSymbolTracer.trace_endpoint_calls(frontend_ts, backend_py)
    assert len(links) == 1
    assert links[0]["client_endpoint"] == "/api/checkpoints"
    assert links[0]["server_route"] == "/api/checkpoints"
    assert links[0]["status"] == "linked"
