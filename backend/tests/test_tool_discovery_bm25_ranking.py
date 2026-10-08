"""Golden relevance proof for the deferred-tool BM25 upgrade."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from alpha.tools.builtins.tool_search import DeferredToolCatalog
from alpha.tools.mcp_metadata import tag_mcp_tool


@dataclass(frozen=True)
class Relevance:
    top1: int
    mean_reciprocal_rank: float


def _tool(name: str, description: str, func=None) -> StructuredTool:
    def invoke(value: str) -> str:
        return value

    return StructuredTool.from_function(
        func=func or invoke,
        name=name,
        description=description,
    )


@pytest.fixture
def golden_catalog(monkeypatch) -> DeferredToolCatalog:
    monkeypatch.setattr("alpha.tools.builtins.tool_search._system_one_tool_order", lambda query, tools: [])
    return DeferredToolCatalog(
        (
            _tool("github_create_issue", "Create a published tracker issue for a software defect."),
            _tool("github_issue_draft", "Prepare an unpublished tracker draft for later review."),
            _tool("currency_converter", "Convert AUD and USD currency amounts using a trusted rate table."),
            _tool("web_page_fetcher", "Retrieve and download a public web page as text."),
            _tool("image_transformer", "Resize and rotate an image without changing its content."),
        )
    )


def _relevance(catalog: DeferredToolCatalog, cases: list[tuple[str, str]], *, smart: bool) -> Relevance:
    reciprocal_ranks: list[float] = []
    top1 = 0
    search = catalog.search_smart if smart else catalog.search
    for query, expected in cases:
        names = [candidate.name for candidate in search(query)]
        if names and names[0] == expected:
            top1 += 1
        rank = names.index(expected) + 1 if expected in names else 0
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
    return Relevance(top1=top1, mean_reciprocal_rank=sum(reciprocal_ranks) / len(reciprocal_ranks))


def test_bm25_beats_legacy_ranking_on_realistic_golden_queries(golden_catalog: DeferredToolCatalog) -> None:
    cases = [
        ("github_create_issue", "github_create_issue"),  # exact name
        ("report a defect", "github_create_issue"),  # deterministic defect -> issue synonym
        ("convert aud usd", "currency_converter"),  # description-only terms
        ("gitub create issue", "github_create_issue"),  # near-miss typo, partial lexical match
    ]

    legacy = _relevance(golden_catalog, cases, smart=False)
    upgraded = _relevance(golden_catalog, cases, smart=True)

    assert legacy.top1 == 1
    assert upgraded.top1 == len(cases)
    assert upgraded.mean_reciprocal_rank == 1.0
    assert upgraded.mean_reciprocal_rank > legacy.mean_reciprocal_rank
    assert [golden_catalog.search_smart(query)[0].name for query, _ in cases] == [expected for _, expected in cases]


def test_legacy_regex_and_system_one_order_remain_the_fallback(monkeypatch) -> None:
    catalog = DeferredToolCatalog(
        (
            _tool("alpha_lookup", "Find alpha records."),
            _tool("beta_lookup", "Find beta records."),
        )
    )
    monkeypatch.setattr("alpha.tools.builtins.tool_search._bm25_tool_order", lambda query, tools: [])
    monkeypatch.setattr("alpha.tools.builtins.tool_search._system_one_tool_order", lambda query, tools: ["beta_lookup"])

    assert [candidate.name for candidate in catalog.search_smart("alpha")] == ["alpha_lookup", "beta_lookup"]


def test_first_party_parameter_text_is_ranked_but_mcp_parameter_schema_is_not(monkeypatch) -> None:
    monkeypatch.setattr("alpha.tools.builtins.tool_search._system_one_tool_order", lambda query, tools: [])

    class TrustedParameters(BaseModel):
        filing_period: str = Field(description="Statutory filings reporting period")

    class UntrustedParameters(BaseModel):
        credential: str = Field(description="LEAKED_UNTRUSTED_CLIENT_SCHEMA")

    trusted = StructuredTool(
        func=lambda filing_period: filing_period,
        name="opaque_exporter",
        description="Export records.",
        args_schema=TrustedParameters,
    )
    untrusted = tag_mcp_tool(
        StructuredTool(
            func=lambda credential: credential,
            name="opaque_remote",
            description="Call a remote provider.",
            args_schema=UntrustedParameters,
        )
    )
    distractor = _tool("weather_lookup", "Read the current weather forecast.")
    catalog = DeferredToolCatalog((trusted, untrusted, distractor))

    assert [candidate.name for candidate in catalog.search_smart("statutory filings reporting period")] == ["opaque_exporter"]
    assert catalog.search_smart("LEAKED_UNTRUSTED_CLIENT_SCHEMA") == []
