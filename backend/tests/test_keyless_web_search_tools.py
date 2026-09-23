"""Honesty contract for the AgentEye-ported keyless web-search tools.

Offline by construction: ``httpx.get`` and the optional ``ddgs`` library are
faked so no test touches the network. Covers the three-way status distinction
(ok / no_results / failed), optional-import honesty naming the real missing
package, and the guarantee that failures never fabricate research output.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from alpha.tools.builtins import deep_web_search, keyless_web_search
from alpha.tools.builtins import deep_web_search_tool as deep_mod
from alpha.tools.builtins import keyless_web_search_tool as web_mod

# Two results with distinct URLs so dedup can be observed.
DDG_TWO_RESULTS = """
<html><body>
<a rel="nofollow" class="result__a" href="https://alpha.example/one">Alpha One</a>
<a class="result__snippet" href="https://alpha.example/one">First snippet about the topic</a>
<br>
<a rel="nofollow" class="result__a" href="https://beta.example/two">Beta Two</a>
<a class="result__snippet" href="https://beta.example/two">Second snippet about the topic</a>
</body></html>
"""

DDG_NO_RESULTS = "<html><body><p>No results found.</p></body></html>"


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _httpx_returning(body: str):
    def fake_get(url, params=None, headers=None, timeout=None, follow_redirects=False):
        return _FakeResponse(body)

    return SimpleNamespace(get=fake_get)


def _httpx_always_erroring():
    def fake_get(url, params=None, headers=None, timeout=None, follow_redirects=False):
        raise ConnectionError("network down")

    return SimpleNamespace(get=fake_get)


def _block_ddgs(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "ddgs", None)


class _FakeDDGS:
    def __init__(self, timeout: int | None = None) -> None:
        self._timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def text(self, query: str, max_results: int = 10):
        return iter([{"href": "https://ddgs.example/hit", "title": "DDGS Hit", "body": "b" * 400}])


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path))


def test_ok_results_from_duckduckgo_html(monkeypatch) -> None:
    monkeypatch.setattr(web_mod, "httpx", _httpx_returning(DDG_TWO_RESULTS))
    payload = json.loads(web_mod.keyless_web_search.invoke({"query": "test topic"}))
    assert payload["status"] == "ok"
    assert payload["engine"] == "duckduckgo_html"
    assert len(payload["results"]) == 2
    assert payload["results"][0]["url"] == "https://alpha.example/one"
    assert payload["results"][1]["position"] == 2
    assert payload["attempts"][0]["outcome"] == "ok"


def test_no_results_is_distinct_from_failure(monkeypatch) -> None:
    monkeypatch.setattr(web_mod, "httpx", _httpx_returning(DDG_NO_RESULTS))
    _block_ddgs(monkeypatch)
    payload = json.loads(web_mod.keyless_web_search.invoke({"query": "zz qpxv unreachable"}))
    assert payload["status"] == "no_results"
    assert payload["results"] == []
    outcomes = {entry["outcome"] for entry in payload["attempts"]}
    assert "empty" in outcomes, "clean HTTP 200 fetches must be disclosed as empty"
    assert "unavailable" in outcomes
    assert "ddgs" in json.dumps(payload), "the missing optional package must be named"
    assert "error" not in payload


def test_all_engines_error_reports_failed_and_names_ddgs(monkeypatch) -> None:
    monkeypatch.setattr(web_mod, "httpx", _httpx_always_erroring())
    _block_ddgs(monkeypatch)
    payload = json.loads(web_mod.keyless_web_search.invoke({"query": "anything"}))
    assert payload["status"] == "failed"
    assert payload["results"] == []
    assert payload["missing_package"] == "ddgs"
    assert "ddgs" in payload["error"]
    assert all(entry["outcome"] == "error" for entry in payload["attempts"] if entry["engine"] != "ddgs")
    assert "no_results" not in payload["status"]


def test_missing_httpx_fails_honestly_naming_httpx(monkeypatch) -> None:
    monkeypatch.setattr(web_mod, "httpx", None)
    payload = json.loads(web_mod.keyless_web_search.invoke({"query": "anything"}))
    assert payload["status"] == "failed"
    assert payload["missing_package"] == "httpx"
    assert payload["results"] == []
    assert payload["attempts"] == []


def test_ddgs_fallback_serves_results_when_scrapers_fail(monkeypatch) -> None:
    monkeypatch.setattr(web_mod, "httpx", _httpx_always_erroring())
    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=_FakeDDGS))
    payload = json.loads(web_mod.keyless_web_search.invoke({"query": "fallback check"}))
    assert payload["status"] == "ok"
    assert payload["engine"] == "ddgs"
    assert payload["results"][0]["url"] == "https://ddgs.example/hit"
    assert len(payload["results"][0]["description"]) == 300
    assert any(entry["engine"] == "ddgs" and entry["outcome"] == "ok" for entry in payload["attempts"])


def test_deep_search_failure_emits_no_research_fields(monkeypatch) -> None:
    monkeypatch.setattr(web_mod, "httpx", None)
    payload = json.loads(deep_mod.deep_web_search.invoke({"question": "does anything work"}))
    assert payload["status"] == "failed"
    assert payload["missing_package"] == "httpx"
    for fabricated in ("findings", "citations", "confidence", "summary", "sources_consulted"):
        assert fabricated not in payload, "a failed search must not emit research fields"
    assert payload["failures"]


def test_deep_search_no_results_is_honest(monkeypatch) -> None:
    monkeypatch.setattr(web_mod, "httpx", _httpx_returning(DDG_NO_RESULTS))
    _block_ddgs(monkeypatch)
    payload = json.loads(deep_mod.deep_web_search.invoke({"question": "qpxv zzz nothing"}))
    assert payload["status"] == "no_results"
    assert payload["findings"] == []
    assert payload["citations"] == []
    assert payload["confidence"] == 0.0
    assert payload["sources_consulted"] == 0
    assert "found nothing" in payload["summary"]


def test_deep_search_ok_dedupes_ranks_and_verifies(monkeypatch) -> None:
    monkeypatch.setattr(web_mod, "httpx", _httpx_returning(DDG_TWO_RESULTS))
    payload = json.loads(deep_mod.deep_web_search.invoke({"question": "alpha beta topic"}))
    assert payload["status"] == "ok"
    assert payload["sources_consulted"] == 2, "the same URLs from every expanded query must dedup"
    assert {item["url"] for item in payload["findings"]} == {"https://alpha.example/one", "https://beta.example/two"}
    assert all("relevance_score" in item or "relevance" in item for item in payload["findings"])
    assert 0.0 <= payload["confidence"] <= 1.0
    citation = payload["citations"][0]
    assert citation["id"] == 1
    assert citation["verification"]["domain"] == "alpha.example"
    assert citation["verification"]["category"] == "unknown"
    assert len(payload["query_attempts"]) >= 2, "depth 2 must run expanded queries"
    assert payload["partial_failures"] == []


def test_registered_tools_generate_schemas() -> None:
    for tool_obj in (keyless_web_search, deep_web_search):
        assert tool_obj.name in {"keyless_web_search", "deep_web_search"}
        schema = tool_obj.tool_call_schema
        schema.model_json_schema()
    from alpha.tools.builtins import __all__ as exported

    assert "keyless_web_search" in exported
    assert "deep_web_search" in exported
