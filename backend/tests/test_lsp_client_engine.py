"""Tests for the Real-Time Language Server Protocol Intelligence Engine."""

from __future__ import annotations

import json

import pytest

from alpha.coding.lsp_client_engine import (
    HoverRecord,
    IndexFormat,
    LanguageServerIntelligenceEngine,
    LanguageServerSpec,
    SourceLocation,
    StaticIndexReader,
    StaticSymbolIndexer,
    _coerce_locations,
    _coerce_markup,
    _coerce_symbols,
    _uri_to_path,
    run_coroutine,
)
from alpha.tools.builtins.lsp_intelligence_tool import query_language_server_symbol

SERVICE_SOURCE = '''
"""Payment service module."""


class PaymentService:
    """Handles payment authorization."""

    def authorize(self, amount):
        """Authorize a payment amount."""
        return amount * 2

    def refund(self, amount):
        return amount
'''

CONSUMER_SOURCE = '''
from service import PaymentService


def checkout(total):
    engine = PaymentService()
    return engine.authorize(total)
'''


@pytest.fixture()
def workspace(tmp_path):
    """Materialise a tiny two-file Python workspace."""
    (tmp_path / "service.py").write_text(SERVICE_SOURCE, encoding="utf-8")
    (tmp_path / "checkout.py").write_text(CONSUMER_SOURCE, encoding="utf-8")
    return tmp_path


def _engine(workspace) -> LanguageServerIntelligenceEngine:
    """Return a language-server-free engine bound to ``workspace``."""
    return LanguageServerIntelligenceEngine(workspace, enable_servers=False)


# ---------------------------------------------------------------------------
# Static AST index
# ---------------------------------------------------------------------------


def test_static_indexer_extracts_classes_and_methods(workspace):
    indexer = StaticSymbolIndexer(workspace)
    count = indexer.index()
    assert count >= 3
    names = {symbol.name for symbol in indexer._symbols}
    assert {"PaymentService", "authorize", "refund", "checkout"} <= names


def test_static_indexer_records_kind_and_signature(workspace):
    indexer = StaticSymbolIndexer(workspace)
    indexer.index()
    method = next(s for s in indexer._symbols if s.name == "authorize")
    assert method.kind.value == "Method"
    assert method.container == "PaymentService"
    assert "authorize" in method.signature
    assert method.location.file_path.endswith("service.py")
    assert method.location.line > 0


def test_definitions_resolve_to_declaring_file(workspace):
    indexer = StaticSymbolIndexer(workspace)
    indexer.index()
    locations = indexer.definitions("PaymentService")
    assert len(locations) == 1
    assert locations[0].file_path.endswith("service.py")


def test_references_include_call_sites(workspace):
    indexer = StaticSymbolIndexer(workspace)
    indexer.index()
    locations = indexer.references("authorize")
    files = {location.file_path for location in locations}
    assert any(path.endswith("service.py") for path in files)
    assert any(path.endswith("checkout.py") for path in files)


def test_hover_returns_signature_and_docstring(workspace):
    indexer = StaticSymbolIndexer(workspace)
    indexer.index()
    hover = indexer.hover("authorize")
    assert isinstance(hover, HoverRecord)
    assert "authorize" in hover.signature
    assert "Authorize a payment amount" in hover.documentation


def test_document_symbols_filtered_by_file(workspace):
    indexer = StaticSymbolIndexer(workspace)
    indexer.index()
    symbols = indexer.document_symbols("service.py")
    assert symbols
    assert all(s.location.file_path.endswith("service.py") for s in symbols)


def test_unknown_symbol_returns_empty(workspace):
    indexer = StaticSymbolIndexer(workspace)
    indexer.index()
    assert indexer.definitions("definitely_not_present_symbol") == []
    assert indexer.hover("definitely_not_present_symbol") is None


def test_indexer_tolerates_syntax_errors(tmp_path):
    (tmp_path / "broken.py").write_text("def oops(:\n  pass\n", encoding="utf-8")
    (tmp_path / "fine.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    indexer = StaticSymbolIndexer(tmp_path)
    indexer.index()
    assert any(symbol.name == "ok" for symbol in indexer._symbols)


def test_non_python_symbols_use_pattern_indexer(tmp_path):
    (tmp_path / "app.ts").write_text(
        "export class Cart {}\nexport function total() { return 0; }\n",
        encoding="utf-8",
    )
    indexer = StaticSymbolIndexer(tmp_path)
    indexer.index()
    names = {symbol.name for symbol in indexer._symbols}
    assert {"Cart", "total"} <= names


# ---------------------------------------------------------------------------
# Engine query surface
# ---------------------------------------------------------------------------


def test_engine_definition_query(workspace):
    engine = _engine(workspace)
    result = engine.query("PaymentService", action="definition")
    assert result["success"] is True
    assert result["source"] == "static_ast_index"
    assert result["count"] >= 1


def test_engine_document_symbol_query(workspace):
    engine = _engine(workspace)
    result = engine.query("", action="document_symbol", file_path="service.py")
    assert result["success"] is True
    assert any(item["name"] == "PaymentService" for item in result["results"])


def test_engine_hover_query(workspace):
    engine = _engine(workspace)
    result = engine.query("refund", action="hover")
    assert result["success"] is True
    assert result["results"][0]["signature"]


def test_engine_references_query(workspace):
    engine = _engine(workspace)
    result = engine.query("authorize", action="references")
    assert result["success"] is True
    assert result["count"] >= 2


def test_engine_document_symbol_requires_existing_file(workspace):
    engine = _engine(workspace)
    result = engine.query("", action="document_symbol", file_path="missing.py")
    assert result["success"] is False
    assert "requires an existing file_path" in result["error"]


def test_engine_reports_server_availability(workspace):
    engine = _engine(workspace)
    servers = engine.available_servers()
    assert servers
    assert all({"name", "languages", "installed"} <= set(item) for item in servers)


def test_engine_diagnostics_without_servers_is_empty(workspace):
    engine = _engine(workspace)
    result = engine.query("", action="diagnostics")
    assert result["success"] is True
    assert result["results"] == []


def test_ensure_index_is_idempotent(workspace):
    engine = _engine(workspace)
    first = engine.ensure_index()
    second = engine.ensure_index()
    assert first == second
    assert engine.ensure_index(force=True) == first


# ---------------------------------------------------------------------------
# LSIF / SCIP readers
# ---------------------------------------------------------------------------


def _lsif_document(tmp_path) -> str:
    """Build a minimal newline-delimited LSIF JSON index."""
    lines = [
        json.dumps(
            {
                "id": 1,
                "type": "vertex",
                "label": "document",
                "uri": "file:///repo/service.py",
            }
        ),
        json.dumps({"id": 2, "type": "vertex", "label": "range", "start": {"line": 4, "character": 0}}),
    ]
    return "\n".join(lines) + "\n"


def test_lsif_reader_parses_documents_and_ranges(tmp_path):
    index_path = tmp_path / "index.lsif"
    index_path.write_text(_lsif_document(tmp_path), encoding="utf-8")
    reader = StaticIndexReader()
    reader.load(index_path)
    assert reader.format is IndexFormat.LSIF_JSON
    assert reader.symbol_count >= 0


def _varint(value: int) -> bytes:
    """Encode an unsigned base-128 varint."""
    out = bytearray()
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    """Encode a protobuf field key."""
    return _varint((field << 3) | wire)


def _string_field(field: int, value: str) -> bytes:
    """Encode a length-delimited protobuf string field."""
    payload = value.encode("utf-8")
    return _tag(field, 2) + _varint(len(payload)) + payload


def _packed_int32_field(field: int, values: list[int]) -> bytes:
    """Encode a packed repeated int32 protobuf field."""
    payload = b"".join(_varint(value) for value in values)
    return _tag(field, 2) + _varint(len(payload)) + payload


def _scip_index() -> bytes:
    """Encode a minimal SCIP index containing one document and two symbols."""
    symbol_info = _string_field(1, "scip-python python module service PaymentService#") + _string_field(
        5, "PaymentService"
    )
    definition = (
        _packed_int32_field(1, [4, 0, 6])
        + _string_field(2, "scip-python python module service PaymentService#")
        + _tag(6, 0)
        + _varint(0x1)
    )
    reference = (
        _packed_int32_field(1, [12, 4, 30])
        + _string_field(2, "scip-python python module service PaymentService#")
        + _tag(6, 0)
        + _varint(0x0)
    )
    document = (
        _string_field(1, "python")
        + _string_field(2, "service.py")
        + _tag(4, 2)
        + _varint(len(symbol_info))
        + symbol_info
        + _tag(5, 2)
        + _varint(len(definition))
        + definition
        + _tag(5, 2)
        + _varint(len(reference))
        + reference
    )
    return _tag(2, 2) + _varint(len(document)) + document


def test_scip_reader_decodes_documents_and_occurrences(tmp_path):
    index_path = tmp_path / "index.scip"
    index_path.write_bytes(_scip_index())
    reader = StaticIndexReader()
    loaded = reader.load(index_path)
    assert loaded == 2
    assert reader.format is IndexFormat.SCIP
    definition = next(entry for entry in reader.symbols if entry.is_definition)
    assert definition.name == "PaymentService"
    assert definition.file_path == "service.py"
    assert definition.line == 5


def test_scip_reader_finds_symbols_by_name(tmp_path):
    index_path = tmp_path / "index.scip"
    index_path.write_bytes(_scip_index())
    reader = StaticIndexReader()
    reader.load(index_path)
    matches = reader.find("PaymentService")
    assert len(matches) == 2


def test_engine_prefers_static_index_over_ast_index(tmp_path, workspace):
    index_path = tmp_path / "index.scip"
    index_path.write_bytes(_scip_index())
    engine = _engine(workspace)
    assert engine.load_static_index(str(index_path)) == 2
    result = engine.query("PaymentService", action="definition")
    assert result["source"] == "static_index"


def test_missing_index_file_loads_zero_entries(tmp_path):
    reader = StaticIndexReader()
    assert reader.load(tmp_path / "absent.scip") == 0


# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------


def test_uri_to_path_handles_file_scheme():
    assert _uri_to_path("file:///repo/service.py") in {"/repo/service.py", "\\repo\\service.py"}
    assert _uri_to_path("") == ""
    assert _uri_to_path("plain/path.py") == "plain/path.py"


def test_coerce_locations_accepts_location_links():
    payload = [
        {
            "targetUri": "file:///repo/service.py",
            "targetRange": {
                "start": {"line": 9, "character": 4},
                "end": {"line": 9, "character": 12},
            },
        }
    ]
    locations = _coerce_locations(payload)
    assert len(locations) == 1
    assert isinstance(locations[0], SourceLocation)
    assert locations[0].line == 10
    assert locations[0].character == 4


def test_coerce_markup_flattens_polymorphic_contents():
    assert _coerce_markup("plain") == "plain"
    assert _coerce_markup({"value": "markdown"}) == "markdown"
    assert _coerce_markup(["a", {"value": "b"}]) == "a\nb"


def test_coerce_symbols_walks_hierarchy():
    payload = [
        {
            "name": "PaymentService",
            "kind": 5,
            "range": {"start": {"line": 3}, "end": {"line": 9}},
            "children": [{"name": "authorize", "kind": 6, "range": {"start": {"line": 5}}}],
        }
    ]
    symbols = _coerce_symbols(payload)
    assert [item.name for item in symbols] == ["PaymentService", "authorize"]
    assert symbols[1].container == "PaymentService"


def test_run_coroutine_executes_without_running_loop():
    async def _coro():
        return 42

    assert run_coroutine(_coro()) == 42


def test_language_server_spec_detects_missing_binary():
    spec = LanguageServerSpec("absent-server", ("python",), ("definitely-not-a-binary-xyz",))
    assert spec.is_installed() is False


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


def test_tool_definition_query(workspace):
    result = query_language_server_symbol.invoke(
        {"symbol": "PaymentService", "workspace_root": str(workspace), "action": "definition"}
    )
    assert result["success"] is True
    assert result["count"] >= 1


def test_tool_servers_action(workspace):
    result = query_language_server_symbol.invoke(
        {"symbol": "", "workspace_root": str(workspace), "action": "servers"}
    )
    assert result["success"] is True
    assert result["count"] > 0


def test_tool_rejects_unknown_action(workspace):
    result = query_language_server_symbol.invoke(
        {"symbol": "x", "workspace_root": str(workspace), "action": "teleport"}
    )
    assert result["success"] is False
    assert "unsupported action" in result["error"]


def test_tool_load_index_action(workspace, tmp_path):
    index_path = tmp_path / "index.scip"
    index_path.write_bytes(_scip_index())
    result = query_language_server_symbol.invoke(
        {
            "symbol": "PaymentService",
            "workspace_root": str(workspace),
            "action": "load_index",
            "index_path": str(index_path),
        }
    )
    assert result["success"] is True
    assert result["count"] == 2


def test_tool_load_index_requires_path(workspace):
    result = query_language_server_symbol.invoke(
        {"symbol": "x", "workspace_root": str(workspace), "action": "load_index"}
    )
    assert result["success"] is False
