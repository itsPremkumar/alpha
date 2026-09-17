
from agent_workspace.runtime.rlm import ContextStore, ContextTransformer, RLMEngine


def test_context_store_registration_and_immutability():
    store = ContextStore()
    sample_text = "line 1\nline 2\nline 3\nline 4\nline 5"

    handle = store.register(content=sample_text, label="test_doc")
    assert handle.handle_id.startswith("ctx_")
    assert handle.line_count == 5
    assert handle.byte_size > 0
    assert handle.estimated_tokens >= 1
    assert store.get_content(handle.handle_id) == sample_text


def test_context_transformer_grep():
    store = ContextStore()
    text = (
        "INFO: system started\n"
        "DEBUG: loading config\n"
        "ERROR: database timeout occurred\n"
        "INFO: retry scheduled\n"
        "ERROR: connection reset by peer"
    )
    handle = store.register(content=text, label="server_logs")
    transformer = ContextTransformer(store)

    grep_handle = transformer.grep(handle.handle_id, query="ERROR")
    assert grep_handle is not None
    assert grep_handle.parent_handle_id == handle.handle_id

    grep_content = store.get_content(grep_handle.handle_id)
    assert "database timeout" in grep_content
    assert "connection reset" in grep_content
    assert "system started" not in grep_content


def test_context_transformer_slice_and_chunk():
    store = ContextStore()
    lines = [f"line_{i}" for i in range(1, 101)]
    text = "\n".join(lines)
    handle = store.register(content=text, label="large_file")
    transformer = ContextTransformer(store)

    # Slice lines 10 to 20
    sliced = transformer.slice_lines(handle.handle_id, start_line=10, end_line=20)
    assert sliced is not None
    assert sliced.line_count == 11
    content_slice = store.get_content(sliced.handle_id)
    assert "line_10" in content_slice
    assert "line_20" in content_slice
    assert "line_9" not in content_slice

    # Chunk into groups of 30
    chunks = transformer.chunk(handle.handle_id, lines_per_chunk=30)
    assert len(chunks) == 4  # 30, 30, 30, 10


def test_rlm_engine_workflow():
    engine = RLMEngine()
    text = "\n".join([f"record_{i}: data_{i*10}" for i in range(1, 51)])
    handle = engine.load_variable(content=text, label="dataset")

    peek_res = engine.peek(handle.handle_id, max_lines=5)
    assert peek_res["total_lines"] == 50
    assert len(peek_res["preview_lines"]) == 5

    grep_h = engine.grep_variable(handle.handle_id, query="record_25")
    assert grep_h is not None
    filtered_text = engine.fetch_text(grep_h.handle_id)
    assert "data_250" in filtered_text

    stats = engine.stats()
    assert stats["total_handles"] >= 2
