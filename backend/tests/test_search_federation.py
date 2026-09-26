"""Tests for the federated web-search layer (providers + budgets + failover).

Design under test (single seam): every outbound HTTP call goes through
``alpha.community.search_federation.providers.request``. Tests stub ONLY that
function (plus inject ``clock`` / ``env`` / state paths) and exercise the real
adapters, the real credit maths, the real budget ledger, the real cooldown
policy and the real failover loop.

What these tests assert, in the order the requirements were stated:

* Tavily keyless needs no credential and sends the documented access-mode header.
* ``search_depth`` is always sent explicitly, and ``advanced`` costs 2 credits
  while everything else costs 1 (the silent quota-halving trap).
* A provider with no credential is *skipped*, never called and never an error.
* A provider whose allowance is spent is *skipped*; a provider that reports its
  own exhaustion is recorded so the next search routes around it.
* Every failure mode is a typed error, and a 2xx with zero results is NOT one.
* When nothing can serve the query the caller gets ``SearchExhaustedError``,
  never an empty result list.
* Retired / contested / licence-blocked providers are refused, and are absent
  from the default chain.
* The cooldown policy matches ``alpha/models/free_router/catalog.py``.

Workspace isolation: the autouse fixture points ``AGENT_WORKSPACE_HOME`` at a
per-test tmp dir so the health/budget caches never touch a live workspace.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from alpha.community.ddg_search import tools as ddg_tools
from alpha.community.search_federation import budgets, federation, providers, truth
from alpha.community.search_federation.budgets import BudgetLedger, CreditCost
from alpha.community.search_federation.errors import (
    ProviderAuthError,
    ProviderBudgetExhaustedError,
    ProviderContractError,
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderRequestRejectedError,
    ProviderRetiredError,
    ProviderUnavailableError,
    ProviderUnverifiedError,
    SearchExhaustedError,
    SearchProviderError,
    is_retryable,
)
from alpha.community.search_federation.federation import (
    COOLDOWN_BASE_SECONDS,
    COOLDOWN_JITTER,
    COOLDOWN_MAX_SECONDS,
    MODE_CHAINS,
    SearchFederation,
    SearchRequest,
)
from alpha.community.search_federation.providers import (
    BUDGET_SPECS,
    PROVIDER_ORDER,
    ProviderSearchResult,
    SearchHit,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


#: The real DuckDuckGo adapter, captured before the autouse fixture replaces it,
#: so the adapter's own behaviour (normalization + error mapping) can be tested.
REAL_DDG_ADAPTER = providers.duckduckgo_search


@pytest.fixture(autouse=True)
def isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKSPACE_HOME", str(tmp_path / "agent-workspace"))
    for var in ("TAVILY_API_KEY", "EXA_API_KEY", "SERPER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    # DuckDuckGo's own HTTP seam is the bundled ``ddgs`` client. Replacing the
    # adapter (rather than ``search_web``) keeps the whole suite off the network
    # while leaving ``search_web`` itself testable; the two tests that exercise
    # the adapter put the real one back with ``use_real_ddg_adapter``.
    monkeypatch.setattr(providers, "duckduckgo_search", fake_ddg_ok)
    federation.reset_federation()
    yield
    federation.reset_federation()


def fake_ddg_ok(*args, **kwargs) -> ProviderSearchResult:
    """Stand-in for the keyless DuckDuckGo adapter: one result, no cost.

    Takes ``*args`` because the federation calls adapters with the query
    positionally, exactly as the real ones are called.
    """
    return ProviderSearchResult(
        provider="duckduckgo",
        hits=[SearchHit(title="DDG", url="https://ddg.example/1", content="ddg body", provider="duckduckgo", rank=1)],
        cost=providers.nominal_cost(providers.PROVIDERS["duckduckgo"]),
        latency_ms=1.0,
    )


def use_real_ddg_adapter(monkeypatch):
    """Restore the real adapter so its normalization/error mapping is under test."""
    monkeypatch.setattr(providers, "duckduckgo_search", REAL_DDG_ADAPTER)


def ddg_down(monkeypatch):
    """Make the DuckDuckGo adapter fail, the way a real outage would."""

    def boom(*args, **kwargs) -> ProviderSearchResult:
        raise ProviderUnavailableError("duckduckgo", "ConnectError: ddgs engine unreachable", status_code=500)

    monkeypatch.setattr(providers, "duckduckgo_search", boom)


class _Clock:
    """Deterministic clock so cooldown/period tests never sleep."""

    def __init__(self, start: float = 1_760_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _HTTP:
    """URL-routed stub for ``providers.request`` that records every call.

    Route values may be an ``httpx.Response``, or a callable taking the full
    call record ``(url, body, headers)`` - the latter is needed to tell Tavily's
    keyless and keyed endpoints apart, since they share a host and differ only
    by header.
    """

    def __init__(self, routes: dict[str, Any] | None = None, default: Any | None = None) -> None:
        self.routes = routes or {}
        self.default = default
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method, url, *, headers=None, json_body=None, timeout=None):
        record = {
            "method": method,
            "url": url,
            "headers": dict(headers or {}),
            "json": json_body,
            "timeout": timeout,
        }
        self.calls.append(record)
        assert timeout is not None, "every provider call must pass an explicit timeout"
        for needle, response in self.routes.items():
            if needle in url:
                return response(record) if callable(response) else response
        if self.default is not None:
            return self.default(record) if callable(self.default) else self.default
        raise AssertionError(f"unstubbed URL {url!r}")

    def urls(self) -> list[str]:
        return [c["url"] for c in self.calls]

    def keyed_calls(self) -> list[dict[str, Any]]:
        """Calls that carried a credential (i.e. were not keyless/anonymous)."""
        return [c for c in self.calls if "Authorization" in c["headers"] or any(h.lower() in {"x-api-key", "x-key"} for h in c["headers"])]


def install(monkeypatch, **kwargs) -> _HTTP:
    handler = _HTTP(**kwargs)
    monkeypatch.setattr(providers, "request", handler)
    return handler


def make_federation(
    tmp_path,
    *,
    clock=None,
    env=None,
    allow_unverified=False,
    layer=None,
    chains=None,
) -> SearchFederation:
    return SearchFederation(
        layer=layer or providers,
        clock=clock or (lambda: 1_760_000_000.0),
        health_path=tmp_path / "health.json",
        budget_path=tmp_path / "budget.json",
        env=env if env is not None else {},
        allow_unverified_providers=allow_unverified,
        chains=chains,
    )


# --- canned provider responses (shapes taken from the live APIs) -----------


def tavily_ok(record: dict[str, Any] | None = None):
    """A Tavily 200 whose usage.credits reflects the requested search_depth.

    Mirrors the real API, which bills 1 credit for basic/fast/ultra-fast and 2
    for advanced and reports the figure back in ``usage.credits``.
    """
    body = (record or {}).get("json") or {}
    credits = 2 if body.get("search_depth") == "advanced" else 1
    return httpx.Response(
        200,
        json={
            "query": "q",
            "answer": None,
            "images": [],
            "results": [
                {
                    "url": "https://example.com/a",
                    "title": "A",
                    "content": "alpha",
                    "score": 0.91,
                    "raw_content": None,
                },
                {
                    "url": "https://example.com/b",
                    "title": "B",
                    "content": "beta",
                    "score": 0.72,
                    "raw_content": None,
                },
            ],
            "response_time": 0.9,
            "usage": {"credits": credits},
            "request_id": "req-tavily-1",
        },
    )


def tavily_empty(record: dict[str, Any] | None = None):
    return httpx.Response(
        200,
        json={"query": "q", "answer": None, "images": [], "results": [], "usage": {"credits": 1}},
    )


def exa_ok(record: dict[str, Any] | None = None):
    return httpx.Response(
        200,
        json={
            "requestId": "req-exa-1",
            "results": [
                {
                    "title": "Neural page",
                    "url": "https://exa.example/n",
                    "publishedDate": "2026-01-02T00:00:00.000Z",
                    "highlights": ["<em>highlight</em> one"],
                }
            ],
            "costDollars": {"total": 0.007},
            "resolvedSearchType": "neural",
            "searchTime": 120.0,
        },
    )


def serper_ok(record: dict[str, Any] | None = None):
    return httpx.Response(
        200,
        json={
            "organic": [
                {
                    "title": "Google result",
                    "link": "https://google.example/g",
                    "snippet": "snip",
                    "date": "2 days ago",
                    "position": 1,
                }
            ],
            "credits": 1,
        },
    )


def http_error(status: int, payload: Any = None, *, headers: dict[str, str] | None = None):
    def handler(record: dict[str, Any] | None = None) -> httpx.Response:
        return httpx.Response(
            status, json=payload if payload is not None else {"detail": {"error": "nope"}}, headers=headers
        )

    return handler


#: 2026-01-31T23:00:00Z and 2026-02-01T01:00:00Z straddle the UTC month boundary.
JUST_BEFORE_FEBRUARY = 1_769_900_400
TWO_HOURS_LATER = 1_769_907_600


# ===========================================================================
# 1. Tavily keyless: no credential, documented header, real LLM-shaped results
# ===========================================================================


def test_tavily_keyless_needs_no_credential_and_sends_the_documented_header(monkeypatch) -> None:
    handler = install(monkeypatch, default=tavily_ok)
    result = providers.tavily_search("capital of australia", key=None, max_results=2)

    assert [hit.url for hit in result.hits] == ["https://example.com/a", "https://example.com/b"]
    assert result.provider == "tavily_keyless"
    assert result.reported_cost == 1.0
    assert result.reported_cost_unit == "tavily_credit"
    headers = handler.calls[0]["headers"]
    assert headers[providers.TAVILY_KEYLESS_HEADER] == providers.TAVILY_KEYLESS_VALUE
    # Keyless must never carry an Authorization header: the vendor documents
    # that a supplied key silently takes precedence and spends account credits.
    assert "Authorization" not in headers


def test_tavily_keyless_always_sends_search_depth_and_disables_auto_parameters(monkeypatch) -> None:
    handler = install(monkeypatch, default=tavily_ok)
    providers.tavily_search("q", key=None, search_depth="basic")

    body = handler.calls[0]["json"]
    assert body["search_depth"] == "basic"
    assert body["auto_parameters"] is False
    assert body["include_usage"] is True


def test_tavily_keyless_and_keyed_never_send_both_credential_modes(monkeypatch) -> None:
    handler = install(monkeypatch, default=tavily_ok)
    providers.tavily_search("q", key=None)
    providers.tavily_search("q", key="tvly-secret")

    keyless_headers, keyed_headers = (c["headers"] for c in handler.calls)
    assert providers.TAVILY_KEYLESS_HEADER in keyless_headers
    assert "Authorization" not in keyless_headers
    assert keyed_headers["Authorization"] == "Bearer tvly-secret"
    assert providers.TAVILY_KEYLESS_HEADER not in keyed_headers


def test_credential_never_raises_and_returns_none_when_env_unset(monkeypatch) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    spec = providers.PROVIDERS["exa"]
    assert providers.credential(spec) is None
    assert providers.missing_env(spec) == ("EXA_API_KEY",)

    monkeypatch.setenv("EXA_API_KEY", "  exa-key  ")
    assert providers.credential(spec) == "exa-key"
    assert providers.missing_env(spec) == ()


def test_unset_credential_degrades_to_skip_not_exception(tmp_path) -> None:
    fed = make_federation(tmp_path, env={})
    plan, skipped = fed.plan(SearchRequest(query="q"))

    assert "tavily_keyless" in plan, "keyless must work with no configuration at all"
    assert "duckduckgo" in plan
    for name in ("tavily", "exa", "serper"):
        assert name not in plan
        reason = dict(skipped)[name]
        assert reason.startswith("not configured: set ")
    assert any("TAVILY_API_KEY" in reason for _, reason in skipped)


# ===========================================================================
# 2. Credit cost: explicit search_depth, documented per-call cost
# ===========================================================================


@pytest.mark.parametrize(
    ("depth", "expected"),
    [("basic", 1.0), ("fast", 1.0), ("ultra-fast", 1.0), ("advanced", 2.0)],
)
def test_tavily_credit_cost_matches_the_published_price_table(depth: str, expected: float) -> None:
    cost = providers.tavily_credit_cost(search_depth=depth)
    assert cost.amount == expected
    assert cost.unit == "tavily_credit"
    assert depth in cost.reason


def test_tavily_credit_cost_requires_an_explicit_depth() -> None:
    # A defaulted depth is the quota-halving bug; the signature makes it
    # impossible to express rather than merely discouraged.
    with pytest.raises(TypeError):
        providers.tavily_credit_cost()  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        providers.tavily_credit_cost(search_depth="turbo")


def test_tavily_auto_parameters_is_billed_as_advanced_even_at_basic_depth() -> None:
    cost = providers.tavily_credit_cost(search_depth="basic", auto_parameters=True)
    assert cost.amount == 2.0
    assert "auto_parameters" in cost.reason


def test_tavily_extract_cost_is_one_credit_per_five_successful_urls() -> None:
    assert providers.tavily_extract_credit_cost(5).amount == 1.0
    assert providers.tavily_extract_credit_cost(10).amount == 2.0
    assert providers.tavily_extract_credit_cost(10, extract_depth="advanced").amount == 4.0


def test_exa_credit_cost_charges_base_plus_results_above_ten() -> None:
    assert providers.exa_credit_cost(5).amount == pytest.approx(0.007)
    assert providers.exa_credit_cost(10).amount == pytest.approx(0.007)
    assert providers.exa_credit_cost(20).amount == pytest.approx(0.017)
    assert providers.exa_credit_cost(5, with_summary=True).amount == pytest.approx(0.012)


def test_serper_credit_cost_is_one_query_credit() -> None:
    cost = providers.serper_credit_cost()
    assert cost.amount == 1.0
    assert cost.unit == "query"


def test_search_reports_the_credit_cost_it_charged(tmp_path, monkeypatch) -> None:
    install(monkeypatch, default=tavily_ok)
    fed = make_federation(tmp_path)
    outcome = fed.search(SearchRequest(query="q", search_depth="advanced"))

    # Tavily bills advanced at 2 credits and reports usage.credits = 2. The
    # reported figure is charged, not our prediction, because theirs is the bill.
    assert outcome.provider == "tavily_keyless"
    assert outcome.cost_unit == "tavily_credit"
    assert outcome.cost_amount == 2.0
    assert "provider-reported" in outcome.cost_reason
    assert outcome.per_provider[0]["reported_cost"] == {"amount": 2.0, "unit": "tavily_credit"}
    assert fed.budgets.remaining("tavily_keyless") == float("inf"), "keyless is not billed"


def test_a_provider_that_reports_no_cost_falls_back_to_our_prediction(tmp_path, monkeypatch) -> None:
    # Some deployments strip usage.credits. We must still charge a defensible
    # amount and say that it is our estimate rather than their bill.
    def tavily_route(record: dict[str, Any]) -> httpx.Response:
        if "Authorization" not in record["headers"]:
            return httpx.Response(500, json={"detail": {"error": "keyless down"}})
        return httpx.Response(200, json={"results": [{"url": "https://example.com/a", "title": "A", "content": "x"}]})

    install(monkeypatch, routes={"api.tavily.com": tavily_route})
    fed = make_federation(tmp_path, env={"TAVILY_API_KEY": "tvly-x"})
    outcome = fed.search(SearchRequest(query="q", search_depth="advanced"))

    assert outcome.provider == "tavily"
    assert outcome.cost_unit == "tavily_credit"
    assert outcome.cost_amount == 2.0
    assert "provider-reported" not in outcome.cost_reason
    assert "search_depth=advanced costs 2 credit(s)" in outcome.cost_reason
    assert any("did not report usage.credits" in note for note in outcome.per_provider[0]["notes"])
    assert fed.budgets.remaining("tavily") == 998.0


def test_the_ledger_is_charged_in_the_providers_own_unit(tmp_path, monkeypatch) -> None:
    def tavily_route(record: dict[str, Any]) -> httpx.Response:
        if "Authorization" not in record["headers"]:
            return httpx.Response(500, json={"detail": {"error": "keyless down"}})
        return tavily_ok(record)

    install(monkeypatch, routes={"api.tavily.com": tavily_route})
    fed = make_federation(tmp_path, env={"TAVILY_API_KEY": "tvly-x"})

    fed.search(SearchRequest(query="q", search_depth="basic"))
    assert fed.budgets.remaining("tavily") == 999.0
    assert fed.budgets.remaining("serper") == 2500.0, "one Tavily search must not touch Serper's query count"
    assert fed.budgets.remaining("exa") == 10.0


# ===========================================================================
# 3. Budget ledger: allowances, monthly refresh, one-time grants, unit safety
# ===========================================================================


def test_monthly_allowance_resets_on_the_utc_month_boundary(tmp_path) -> None:
    clock = _Clock(JUST_BEFORE_FEBRUARY)
    ledger = BudgetLedger(specs=dict(BUDGET_SPECS), path=tmp_path / "b.json", clock=clock)
    ledger.record("tavily", providers.tavily_credit_cost(search_depth="advanced"))
    assert ledger.remaining("tavily") == 998.0
    assert ledger.state("tavily").period == "2026-01"

    clock.now = TWO_HOURS_LATER
    assert ledger.state("tavily").period == "2026-02"
    assert ledger.remaining("tavily") == 1000.0


def test_one_time_grant_never_refreshes(tmp_path) -> None:
    clock = _Clock(1_760_000_000.0)
    ledger = BudgetLedger(specs=dict(BUDGET_SPECS), path=tmp_path / "b.json", clock=clock)
    for _ in range(3):
        ledger.record("serper", providers.serper_credit_cost())
    assert ledger.remaining("serper") == 2497.0

    clock.advance(400 * 24 * 3600)  # well past any monthly boundary
    assert ledger.remaining("serper") == 2497.0, "Serper's free grant is one-time, not monthly"
    assert ledger.state("serper").period == ""


def test_spending_past_the_allowance_marks_the_provider_exhausted(tmp_path) -> None:
    ledger = BudgetLedger(specs=dict(BUDGET_SPECS), path=tmp_path / "b.json", clock=lambda: 1.0)
    for _ in range(1000):
        ledger.record("tavily", providers.tavily_credit_cost(search_depth="basic"))

    assert ledger.exhausted("tavily") is True
    ok, reason = ledger.can_spend("tavily", providers.tavily_credit_cost(search_depth="basic"))
    assert ok is False
    assert "local allowance spent" in reason
    assert ledger.remaining("tavily") == 0.0


def test_provider_reported_exhaustion_is_recorded_and_cleared_on_rollover(tmp_path) -> None:
    clock = _Clock(JUST_BEFORE_FEBRUARY)
    ledger = BudgetLedger(specs=dict(BUDGET_SPECS), path=tmp_path / "b.json", clock=clock)
    ledger.mark_provider_exhausted("exa", "NO_MORE_CREDITS")

    assert ledger.exhausted("exa") is True
    ok, reason = ledger.can_spend("exa", providers.exa_credit_cost(5))
    assert ok is False
    assert "NO_MORE_CREDITS" in reason

    clock.now = TWO_HOURS_LATER
    assert ledger.exhausted("exa") is False, "a new month refills the balance the flag described"
    assert ledger.remaining("exa") == 10.0


def test_ledger_refuses_to_mix_units_rather_than_guessing(tmp_path) -> None:
    ledger = BudgetLedger(specs=dict(BUDGET_SPECS), path=tmp_path / "b.json", clock=lambda: 1.0)
    ok, reason = ledger.can_spend("serper", CreditCost(1.0, "tavily_credit", "wrong unit on purpose"))

    assert ok is False
    assert "does not match provider unit" in reason
    assert ledger.remaining("serper") == 2500.0, "a refused charge must not move the ledger"


def test_unmetered_providers_are_always_affordable(tmp_path) -> None:
    ledger = BudgetLedger(specs=dict(BUDGET_SPECS), path=tmp_path / "b.json", clock=lambda: 1.0)
    assert ledger.remaining("duckduckgo") == float("inf")
    assert ledger.exhausted("duckduckgo") is False
    ok, _ = ledger.can_spend("duckduckgo", CreditCost(999.0, "query", "hypothetical"))
    assert ok is True


def test_budget_survives_a_restart(tmp_path) -> None:
    path = tmp_path / "b.json"
    first = BudgetLedger(specs=dict(BUDGET_SPECS), path=path, clock=lambda: 1_760_000_000.0)
    first.record("serper", providers.serper_credit_cost())
    first.record("serper", providers.serper_credit_cost())

    second = BudgetLedger(specs=dict(BUDGET_SPECS), path=path, clock=lambda: 1_760_000_001.0)
    assert second.remaining("serper") == 2498.0


def test_state_paths_accept_strings_because_yaml_config_is_all_strings(tmp_path, monkeypatch) -> None:
    # A config-supplied path arrives as a str; treating it as a Path on the way
    # in is the difference between "reads its cache" and "AttributeError on the
    # first call".
    def tavily_route(record: dict[str, Any]) -> httpx.Response:
        if "Authorization" not in record["headers"]:
            return httpx.Response(500, json={"detail": {"error": "keyless down"}})
        return tavily_ok(record)

    install(monkeypatch, routes={"api.tavily.com": tavily_route})
    fed = SearchFederation(
        clock=lambda: 1_760_000_000.0,
        health_path=str(tmp_path / "health.json"),
        budget_path=str(tmp_path / "budget.json"),
        env={"TAVILY_API_KEY": "tvly-x"},
    )
    outcome = fed.search(SearchRequest(query="q"))

    assert outcome.provider == "tavily", "the string budget path was read and the keyed call charged"
    assert fed.budgets.remaining("tavily") == 999.0
    assert (tmp_path / "budget.json").is_file()
    assert (tmp_path / "health.json").is_file()

    # And a second federation reads that same string-path cache back.
    reloaded = SearchFederation(
        clock=lambda: 1_760_000_001.0,
        health_path=str(tmp_path / "health.json"),
        budget_path=str(tmp_path / "budget.json"),
        env={"TAVILY_API_KEY": "tvly-x"},
    )
    assert reloaded.budgets.remaining("tavily") == 999.0
    assert reloaded._state["tavily"].healthy is True


def test_corrupt_budget_file_degrades_to_empty_rather_than_wrong(tmp_path) -> None:
    path = tmp_path / "b.json"
    path.write_text("{not json", encoding="utf-8")
    ledger = BudgetLedger(specs=dict(BUDGET_SPECS), path=path, clock=lambda: 1.0)

    assert ledger.remaining("serper") == 2500.0
    assert ledger.exhausted("serper") is False


def test_budgeted_provider_is_skipped_rather_than_called(tmp_path, monkeypatch) -> None:
    handler = install(monkeypatch, default=tavily_ok)
    fed = make_federation(tmp_path, env={"TAVILY_API_KEY": "tvly-x"})
    for _ in range(1000):
        fed.budgets.record("tavily", providers.tavily_credit_cost(search_depth="basic"))
    handler.calls.clear()

    outcome = fed.search(SearchRequest(query="q"))

    assert outcome.provider == "tavily_keyless", "the keyless provider is unmetered and still answers"
    assert handler.keyed_calls() == [], "a spent allowance must not be spent again on a guaranteed failure"
    assert "Authorization" not in handler.calls[0]["headers"]
    reason = dict(outcome.skipped)["tavily"]
    assert reason.startswith("budget: ")
    assert "local allowance spent" in reason


def test_exhausted_provider_is_never_called_even_when_it_is_the_only_chain(tmp_path, monkeypatch) -> None:
    handler = install(monkeypatch, default=exa_ok)
    fed = make_federation(tmp_path, env={"EXA_API_KEY": "exa-x"})
    fed.budgets.mark_provider_exhausted("exa", "API_KEY_BUDGET_EXCEEDED")
    handler.calls.clear()

    with pytest.raises(SearchExhaustedError) as excinfo:
        fed.find_similar("https://example.com")

    assert handler.calls == []
    assert "budget" in dict(excinfo.value.skipped)["exa"]
    assert excinfo.value.to_dict()["kind"] == "search_exhausted"


def test_provider_reported_exhaustion_is_learned_and_not_repaid(tmp_path, monkeypatch) -> None:
    # Both Tavily modes share api.tavily.com, so the stub has to look at the
    # headers to tell the anonymous tier from the account-backed one.
    def tavily_route(record: dict[str, Any]) -> httpx.Response:
        if "Authorization" in record["headers"]:
            return httpx.Response(432, json={"detail": {"error": "plan limit exceeded"}})
        return httpx.Response(500, json={"detail": {"error": "anonymous tier is down"}})

    handler = install(monkeypatch, routes={"api.tavily.com": tavily_route, "serper": serper_ok})
    fed = make_federation(
        tmp_path, env={"TAVILY_API_KEY": "tvly-x", "EXA_API_KEY": "exa-x", "SERPER_API_KEY": "serper-x"}
    )
    outcome = fed.search(SearchRequest(query="q", mode="web"))

    assert outcome.provider == "serper"
    assert dict(outcome.attempts)["tavily"] == "ProviderBudgetExhaustedError(status=432)"
    assert dict(outcome.skipped)["tavily"] == "budget exhausted (provider-reported)"
    assert fed.budgets.exhausted("tavily") is True

    calls_after_first = len(handler.calls)
    fed.search(SearchRequest(query="q", mode="web"))
    second_call_urls = handler.urls()[calls_after_first:]
    assert not any("api.tavily.com" in url for url in second_call_urls), "must not repay a known-exhausted key"


# ===========================================================================
# 4. Typed errors: every failure mode, and none of them is "no results"
# ===========================================================================


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (410, ProviderRetiredError),
        (429, ProviderRateLimitedError),
        (432, ProviderBudgetExhaustedError),
        (433, ProviderBudgetExhaustedError),
        (400, ProviderRequestRejectedError),
        (422, ProviderRequestRejectedError),
        (500, ProviderUnavailableError),
        (503, ProviderUnavailableError),
    ],
)
def test_every_http_status_maps_to_a_typed_error(monkeypatch, status: int, expected: type) -> None:
    install(monkeypatch, default=http_error(status, {"detail": {"error": "upstream said no"}}))
    with pytest.raises(expected) as excinfo:
        providers.tavily_search("q", key=None)

    assert excinfo.value.status_code == status
    assert excinfo.value.provider in {"tavily", "tavily_keyless"}


def test_retry_after_header_is_preserved_for_rate_limits(monkeypatch) -> None:
    install(monkeypatch, default=http_error(429, {"detail": {"error": "slow down"}}, headers={"Retry-After": "60"}))
    with pytest.raises(ProviderRateLimitedError) as excinfo:
        providers.tavily_search("q", key=None)

    assert excinfo.value.retry_after_seconds == 60.0


def test_exa_budget_tag_wins_over_a_400_status(monkeypatch) -> None:
    install(
        monkeypatch,
        default=httpx.Response(400, json={"requestId": "r", "error": "no credits", "tag": "NO_MORE_CREDITS"}),
    )
    with pytest.raises(ProviderBudgetExhaustedError) as excinfo:
        providers.exa_search("q", key="exa-x")

    assert "NO_MORE_CREDITS" in str(excinfo.value)


def test_exa_rate_tag_is_typed_as_a_rate_limit(monkeypatch) -> None:
    install(
        monkeypatch,
        default=httpx.Response(429, json={"requestId": "r", "error": "slow", "tag": "RATE_LIMIT_EXCEEDED"}),
    )
    with pytest.raises(ProviderRateLimitedError):
        providers.exa_search("q", key="exa-x")


def test_serper_403_is_an_auth_error_not_an_empty_result(monkeypatch) -> None:
    install(monkeypatch, default=httpx.Response(403, json={"message": "Unauthorized.", "statusCode": 403}))
    with pytest.raises(ProviderAuthError) as excinfo:
        providers.serper_search("q", key="serper-x")

    assert excinfo.value.status_code == 403


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json=["a", "list"]),
        httpx.Response(200, json={"query": "q"}),
    ],
)
def test_a_200_we_cannot_parse_is_a_contract_error_not_an_empty_result(monkeypatch, response) -> None:
    install(monkeypatch, default=response)
    with pytest.raises(ProviderContractError):
        providers.tavily_search("q", key=None)


def test_a_200_with_zero_results_is_a_real_answer(monkeypatch) -> None:
    install(monkeypatch, default=tavily_empty)
    result = providers.tavily_search("q", key=None)

    assert result.hits == []
    assert result.reported_cost == 1.0


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
        httpx.ConnectError("no route"),
    ],
)
def test_transport_failures_become_typed_unavailable_errors(monkeypatch, exc) -> None:
    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(providers, "request", boom)
    with pytest.raises(ProviderUnavailableError) as excinfo:
        providers.tavily_search("q", key=None)

    assert excinfo.value.provider == "tavily_keyless"
    assert is_retryable(excinfo.value) is True


def test_failure_labels_never_leak_the_response_body() -> None:
    exc = ProviderAuthError("exa", "Unauthorized: missing or invalid API key.", status_code=401)
    label = exc.failure_label

    assert label == "ProviderAuthError(status=401)"
    assert "Unauthorized" not in label
    assert exc.to_dict()["kind"] == "auth"


def test_is_retryable_separates_transient_from_deterministic() -> None:
    assert is_retryable(ProviderRateLimitedError("x", "m", status_code=429)) is True
    assert is_retryable(ProviderUnavailableError("x", "m", status_code=503)) is True
    assert is_retryable(SearchExhaustedError("m")) is True
    assert is_retryable(ProviderAuthError("x", "m", status_code=401)) is False
    assert is_retryable(ProviderBudgetExhaustedError("x", "m", status_code=432)) is False
    assert is_retryable(ProviderRequestRejectedError("x", "m", status_code=400)) is False


def test_search_exhausted_is_a_connection_error_so_generic_retry_layers_continue() -> None:
    # alpha.models.fallback treats ConnectionError as retryable; matching that
    # keeps an exhausted search from being mistaken for a final answer.
    assert issubclass(SearchExhaustedError, ConnectionError)
    assert issubclass(ProviderNotConfiguredError, SearchProviderError)
    assert ProviderNotConfiguredError.kind == "not_configured"


# ===========================================================================
# 5. Failover, health, cooldown
# ===========================================================================


def test_first_provider_answers_and_the_rest_are_not_billed(tmp_path, monkeypatch) -> None:
    handler = install(monkeypatch, default=tavily_ok)
    fed = make_federation(tmp_path, env={"TAVILY_API_KEY": "tvly-x", "EXA_API_KEY": "exa-x"})

    outcome = fed.search(SearchRequest(query="q"))

    assert outcome.provider == "tavily_keyless"
    assert len(handler.calls) == 1
    assert dict(outcome.skipped)["tavily"] == "not needed: an earlier provider already answered"


def test_failover_moves_to_the_next_provider_after_a_typed_failure(tmp_path, monkeypatch) -> None:
    install(
        monkeypatch,
        routes={
            "api.tavily.com": http_error(500, {"detail": {"error": "boom"}}),
            "api.exa.ai": exa_ok,
        },
    )
    fed = make_federation(tmp_path, env={"EXA_API_KEY": "exa-x"})

    outcome = fed.search(SearchRequest(query="q"))

    assert outcome.provider == "exa"
    assert outcome.attempts == [("tavily_keyless", "ProviderUnavailableError(status=500)")]
    assert dict(outcome.skipped)["tavily"] == "not configured: set TAVILY_API_KEY"
    assert [hit.url for hit in outcome.hits] == ["https://exa.example/n"]


def test_health_is_tri_state_and_never_inferred(tmp_path, monkeypatch) -> None:
    install(monkeypatch, default=tavily_ok)
    fed = make_federation(tmp_path)

    assert fed._state["tavily_keyless"].healthy is None, "unprobed means unknown, not healthy"
    fed.search(SearchRequest(query="q"))
    assert fed._state["tavily_keyless"].healthy is True
    assert fed._state["exa"].healthy is None


def test_failure_puts_the_provider_into_cooldown_with_backoff_and_jitter(tmp_path, monkeypatch) -> None:
    clock = _Clock()
    install(monkeypatch, default=http_error(503, {"detail": {"error": "down"}}))
    fed = make_federation(tmp_path, clock=clock, env={"EXA_API_KEY": "exa-x"})

    # The jitter is +/-20%, so the *window* is asserted rather than the sampled
    # value: comparing two jittered samples against each other would be a
    # coin flip, not a test.
    windows = []
    for expected_base in (COOLDOWN_BASE_SECONDS, COOLDOWN_BASE_SECONDS * 2, COOLDOWN_BASE_SECONDS * 4):
        fed.mark_failure("exa", "ProviderUnavailableError(status=503)")
        wait = fed._state["exa"].cooldown_until - clock.now
        lo = expected_base * (1 - COOLDOWN_JITTER)
        hi = expected_base * (1 + COOLDOWN_JITTER)
        assert lo <= wait <= hi, f"cooldown {wait:.1f}s outside [{lo:.1f}, {hi:.1f}] for a {expected_base}s window"
        windows.append(wait)

    state = fed._state["exa"]
    assert state.healthy is False
    assert state.consecutive_failures == 3
    # The last window is the largest, and the cap is never exceeded.
    assert windows[-1] == max(windows)
    assert state.cooldown_until - clock.now <= COOLDOWN_MAX_SECONDS * (1 + COOLDOWN_JITTER)


def test_cooldown_is_capped_at_ten_minutes(tmp_path, monkeypatch) -> None:
    clock = _Clock()
    install(monkeypatch, default=http_error(503))
    fed = make_federation(tmp_path, clock=clock)

    for _ in range(20):
        fed.mark_failure("exa", "ProviderUnavailableError(status=503)")

    assert fed._state["exa"].consecutive_failures == 20
    assert fed._state["exa"].cooldown_until - clock.now <= COOLDOWN_MAX_SECONDS * (1 + COOLDOWN_JITTER)


def test_cooling_down_providers_are_skipped_not_called(tmp_path, monkeypatch) -> None:
    clock = _Clock()
    handler = install(
        monkeypatch,
        routes={"api.tavily.com": http_error(500, {"detail": {"error": "down"}}), "api.exa.ai": exa_ok},
    )
    # mode="web" starts at tavily_keyless, so it is the one that fails; mode
    # "semantic" would put Exa first and never exercise tavily_keyless at all.
    fed = make_federation(tmp_path, clock=clock, env={"TAVILY_API_KEY": "tvly-x", "EXA_API_KEY": "exa-x"})
    fed.search(SearchRequest(query="q", mode="web"))
    calls_after_first = len(handler.calls)

    # Same second: the two Tavily modes are still cooling down and must not be retried.
    fed.search(SearchRequest(query="q", mode="web"))
    assert len(handler.calls) == calls_after_first + 1, "only exa should have been called"
    skips = dict(fed.plan(SearchRequest(query="q", mode="web"))[1])
    assert "cooling down" in skips["tavily_keyless"]
    assert "cooling down" in skips["tavily"]


def test_cooldown_expires_and_the_provider_is_retried(tmp_path, monkeypatch) -> None:
    clock = _Clock()
    handler = install(
        monkeypatch,
        routes={"api.tavily.com": http_error(500, {"detail": {"error": "down"}}), "api.exa.ai": exa_ok},
    )
    # mode="web" so the chain starts at tavily_keyless; with TAVILY_API_KEY set
    # the keyed provider fails too, and Exa is the only survivor.
    fed = make_federation(tmp_path, clock=clock, env={"TAVILY_API_KEY": "tvly-x", "EXA_API_KEY": "exa-x"})
    first = fed.search(SearchRequest(query="q", mode="web"))
    assert first.provider == "exa"
    calls_after_first = len(handler.calls)

    clock.advance(COOLDOWN_MAX_SECONDS + 60)
    handler.routes["api.tavily.com"] = tavily_ok
    outcome = fed.search(SearchRequest(query="q", mode="web"))

    assert outcome.provider == "tavily_keyless", "the recovered provider is preferred again"
    assert len(handler.calls) == calls_after_first + 1


def test_success_clears_the_cooldown(tmp_path, monkeypatch) -> None:
    clock = _Clock()
    install(monkeypatch, default=tavily_ok)
    fed = make_federation(tmp_path, clock=clock)

    fed.mark_failure("tavily_keyless", "ProviderUnavailableError(status=503)")
    assert fed._state["tavily_keyless"].consecutive_failures == 1
    fed.mark_success("tavily_keyless", latency_ms=12.0)

    assert fed._state["tavily_keyless"].healthy is True
    assert fed._state["tavily_keyless"].consecutive_failures == 0
    assert fed._state["tavily_keyless"].cooldown_until == 0.0


def test_health_state_survives_a_restart(tmp_path, monkeypatch) -> None:
    clock = _Clock()
    install(monkeypatch, default=tavily_ok)
    first = make_federation(tmp_path, clock=clock)
    first.search(SearchRequest(query="q"))

    second = make_federation(tmp_path, clock=clock)
    assert second._state["tavily_keyless"].healthy is True
    assert second._state["tavily_keyless"].calls == 1


def test_unmapped_adapter_exception_does_not_sink_the_search(tmp_path, monkeypatch) -> None:
    install(monkeypatch, default=tavily_ok)

    def broken(*args, **kwargs):
        raise ZeroDivisionError("adapter bug")

    monkeypatch.setattr(providers, "tavily_search", broken)
    fed = make_federation(tmp_path)

    outcome = fed.search(SearchRequest(query="q"))

    assert outcome.provider == "duckduckgo"
    assert outcome.attempts == [("tavily_keyless", "ZeroDivisionError")]

# ===========================================================================
# 6. Honest exhaustion: never a silent empty result
# ===========================================================================


def test_exhausted_everything_raises_instead_of_returning_no_results(tmp_path, monkeypatch) -> None:
    install(monkeypatch, default=http_error(500, {"detail": {"error": "everything is down"}}))
    ddg_down(monkeypatch)
    fed = make_federation(tmp_path, env={})

    with pytest.raises(SearchExhaustedError) as excinfo:
        fed.search(SearchRequest(query="q"))

    exc = excinfo.value
    assert isinstance(exc, ConnectionError)
    assert "not 'no results on the web'" in str(exc)
    assert ("tavily_keyless", "ProviderUnavailableError(status=500)") in exc.attempts
    assert ("duckduckgo", "ProviderUnavailableError(status=500)") in exc.attempts
    assert dict(exc.to_dict()["attempts"][0]) == {
        "provider": "tavily_keyless",
        "failure": "ProviderUnavailableError(status=500)",
    }


def test_exhausted_everything_names_the_reason_each_provider_was_skipped(tmp_path, monkeypatch) -> None:
    install(monkeypatch, default=http_error(500))
    ddg_down(monkeypatch)
    fed = make_federation(tmp_path, env={})

    with pytest.raises(SearchExhaustedError) as excinfo:
        fed.search(SearchRequest(query="q"))

    skipped = dict(excinfo.value.skipped)
    assert "EXA_API_KEY" in skipped["exa"]
    assert "SERPER_API_KEY" in skipped["serper"]
    assert "TAVILY_API_KEY" in skipped["tavily"]


def test_empty_answer_from_a_healthy_provider_is_kept_and_labelled(tmp_path, monkeypatch) -> None:
    # Tavily answers with zero results; the keyless DDG adapter then fails.
    install(monkeypatch, default=tavily_empty)
    ddg_down(monkeypatch)
    fed = make_federation(tmp_path, env={})

    outcome = fed.search(SearchRequest(query="obscure nonsense query"))

    assert outcome.provider == "tavily_keyless"
    assert outcome.hits == []
    assert any("zero results" in note and "not a provider failure" in note for note in outcome.notes)
    assert any("provider call(s) failed" in note for note in outcome.notes)


def test_empty_answer_is_only_used_after_a_better_provider_failed(tmp_path, monkeypatch) -> None:
    install(
        monkeypatch,
        routes={
            "api.tavily.com": tavily_empty,
            "serper": serper_ok,
        },
    )
    fed = make_federation(tmp_path, env={"SERPER_API_KEY": "serper-x"})

    outcome = fed.search(SearchRequest(query="q", mode="web"))

    assert outcome.provider == "serper", "a provider with real results beats an empty one"
    assert [hit.url for hit in outcome.hits] == ["https://google.example/g"]


def test_unknown_mode_is_a_request_rejection(tmp_path) -> None:
    fed = make_federation(tmp_path)
    with pytest.raises(ProviderRequestRejectedError) as excinfo:
        fed.search(SearchRequest(query="q", mode="telepathy"))

    assert "unknown search mode" in str(excinfo.value)


def test_empty_query_is_rejected_before_any_provider_is_called(tmp_path, monkeypatch) -> None:
    handler = install(monkeypatch, default=tavily_ok)
    fed = make_federation(tmp_path)

    with pytest.raises(ProviderRequestRejectedError):
        fed.search(SearchRequest(query="   "))
    assert handler.calls == []


def test_similar_mode_without_a_url_is_rejected(tmp_path) -> None:
    fed = make_federation(tmp_path, env={"EXA_API_KEY": "exa-x"})
    with pytest.raises(ProviderRequestRejectedError) as excinfo:
        fed.search(SearchRequest(query="", mode="similar", url=None))

    assert "requires a url" in str(excinfo.value)


# ===========================================================================
# 7. Routing: mode chains, merge mode, per-provider capability
# ===========================================================================


def test_mode_chains_rank_providers_by_what_they_are_actually_good_at() -> None:
    assert MODE_CHAINS["auto"][0] == "tavily_keyless"
    assert MODE_CHAINS["semantic"][0] == "exa"
    assert MODE_CHAINS["web"].index("serper") < MODE_CHAINS["web"].index("exa")
    assert MODE_CHAINS["news"][0] == "tavily_keyless"
    assert MODE_CHAINS["similar"] == ("exa",), "find-similar is Exa-only and must not be faked"


def test_default_chain_is_priority_order_and_ends_on_a_keyless_floor() -> None:
    assert PROVIDER_ORDER == ("tavily_keyless", "tavily", "exa", "serper", "duckduckgo")
    assert providers.PROVIDERS["tavily_keyless"].auth_env == ()
    assert providers.PROVIDERS["duckduckgo"].auth_env == ()


def test_can_serve_reflects_each_providers_documented_capabilities() -> None:
    can_serve = SearchFederation.can_serve
    spec = providers.PROVIDERS
    # Find-similar is Exa-only; no other provider has the capability at all.
    assert can_serve(spec["exa"], "similar") is True
    for name in ("tavily_keyless", "tavily", "serper", "duckduckgo"):
        assert can_serve(spec[name], "similar") is False, f"{name} cannot do find-similar"
    # Every wireable provider can do an ordinary web/semantic/news search, so
    # "auto" admits all of them and the capability filter never blocks the
    # default chain by accident.
    for name in PROVIDER_ORDER:
        assert can_serve(spec[name], "auto") is True
        assert can_serve(spec[name], "web") is True
        assert can_serve(spec[name], "news") is True
    # Serper and DuckDuckGo are keyword engines, not semantic ones.
    assert can_serve(spec["serper"], "semantic") is False
    assert can_serve(spec["duckduckgo"], "semantic") is False
    assert can_serve(spec["exa"], "semantic") is True


def test_a_provider_that_cannot_serve_a_mode_is_skipped_with_a_reason(tmp_path) -> None:
    fed = make_federation(
        tmp_path,
        env={"SERPER_API_KEY": "serper-x"},
        chains={"web": ("serper", "duckduckgo"), "semantic": ("serper", "duckduckgo")},
    )
    plan, skipped = fed.plan(SearchRequest(query="q", mode="semantic"))

    assert "serper" not in plan
    assert dict(skipped)["serper"] == "provider cannot serve mode='semantic'"
    # The same provider is fine for the mode it does support.
    web_plan, _ = fed.plan(SearchRequest(query="q", mode="web"))
    assert "serper" in web_plan


def test_merge_mode_deduplicates_and_bills_every_provider_it_called(tmp_path, monkeypatch) -> None:
    install(
        monkeypatch,
        routes={
            "api.tavily.com": tavily_ok,
            "api.exa.ai": exa_ok,
            "serper": serper_ok,
        },
    )
    fed = make_federation(tmp_path, env={"EXA_API_KEY": "exa-x", "SERPER_API_KEY": "serper-x"})

    outcome = fed.search(SearchRequest(query="q", mode="web", merge=True, max_results=10))

    assert [hit.url for hit in outcome.hits] == [
        "https://example.com/a",
        "https://example.com/b",
        "https://google.example/g",
        "https://exa.example/n",
        "https://ddg.example/1",
    ]
    assert outcome.provider == "tavily_keyless"
    assert [p["provider"] for p in outcome.per_provider] == [
        "tavily_keyless",
        "serper",
        "exa",
        "duckduckgo",
    ]
    assert any("merge mode" in note for note in outcome.notes)
    # Each provider is billed in its own unit; the payload reports the first.
    assert outcome.cost_unit == "tavily_credit"
    assert all(name in outcome.cost_reason for name in ("tavily_keyless", "serper", "exa", "duckduckgo"))


def test_merge_mode_never_exceeds_max_results(tmp_path, monkeypatch) -> None:
    install(monkeypatch, routes={"api.tavily.com": tavily_ok, "serper": serper_ok, "api.exa.ai": exa_ok})
    fed = make_federation(tmp_path, env={"EXA_API_KEY": "exa-x", "SERPER_API_KEY": "serper-x"})

    outcome = fed.search(SearchRequest(query="q", mode="web", merge=True, max_results=2))
    assert len(outcome.hits) == 2
    assert [hit.rank for hit in outcome.hits] == [1, 2]
    assert [p["provider"] for p in outcome.per_provider] == ["tavily_keyless"]


def test_merge_mode_deduplicates_identical_urls(tmp_path, monkeypatch) -> None:
    def same(record: dict[str, Any]):
        return httpx.Response(200, json={"organic": [{"title": "G", "link": "https://example.com/a", "snippet": "s"}]})

    install(monkeypatch, routes={"api.tavily.com": tavily_ok, "serper": same})
    fed = make_federation(tmp_path, env={"SERPER_API_KEY": "serper-x"})

    outcome = fed.search(SearchRequest(query="q", mode="web", merge=True, max_results=10))
    assert [hit.url for hit in outcome.hits].count("https://example.com/a") == 1


def test_similar_mode_uses_the_exa_find_similar_endpoint(tmp_path, monkeypatch) -> None:
    handler = install(
        monkeypatch,
        routes={"/findSimilar": exa_ok, "api.tavily.com": tavily_ok},
    )
    fed = make_federation(tmp_path, env={"EXA_API_KEY": "exa-x"})

    outcome = fed.find_similar("https://example.com/article", max_results=3)

    assert outcome.mode == "similar"
    assert outcome.provider == "exa"
    assert handler.urls()[0].endswith("/findSimilar")


def test_similar_mode_fails_loudly_rather_than_substituting_a_keyword_search(tmp_path, monkeypatch) -> None:
    handler = install(monkeypatch, default=tavily_ok)
    fed = make_federation(tmp_path, env={})

    with pytest.raises(SearchExhaustedError) as excinfo:
        fed.find_similar("https://example.com")

    assert handler.calls == [], "a keyword search is a different question; do not answer it instead"
    assert dict(excinfo.value.skipped)["exa"] == "not configured: set EXA_API_KEY"


def test_news_mode_uses_the_tavily_news_topic_and_the_serper_news_endpoint(tmp_path, monkeypatch) -> None:
    handler = install(
        monkeypatch,
        routes={"api.tavily.com": tavily_ok, "serper": serper_ok},
    )
    fed = make_federation(tmp_path, env={"SERPER_API_KEY": "serper-x"})

    fed.search(SearchRequest(query="q", mode="news", merge=True))

    tavily_body = next(c["json"] for c in handler.calls if "api.tavily.com" in c["url"])
    assert tavily_body["topic"] == "news"
    assert any(c["url"].endswith("/news") for c in handler.calls if "serper" in c["url"])


def test_time_range_is_forwarded_to_every_provider_that_accepts_it(tmp_path, monkeypatch) -> None:
    handler = install(
        monkeypatch,
        routes={"api.tavily.com": tavily_ok, "api.exa.ai": exa_ok, "serper": serper_ok},
    )
    fed = make_federation(tmp_path, env={"EXA_API_KEY": "exa-x", "SERPER_API_KEY": "serper-x"})

    fed.search(SearchRequest(query="q", mode="web", time_range="week", merge=True))

    tavily_body = next(c["json"] for c in handler.calls if "api.tavily.com" in c["url"])
    assert tavily_body["time_range"] == "week"
    exa_body = next(c["json"] for c in handler.calls if "api.exa.ai" in c["url"])
    assert "startPublishedDate" in exa_body
    serper_body = next(c["json"] for c in handler.calls if "serper" in c["url"])
    assert serper_body["tbs"] == "qdr:w"


# ===========================================================================
# 8. Adapter request construction
# ===========================================================================


def test_exa_search_asks_for_highlights_and_caps_num_results(tmp_path, monkeypatch) -> None:
    handler = install(monkeypatch, default=exa_ok)
    providers.exa_search("q", key="exa-x", max_results=3)

    body = handler.calls[0]["json"]
    assert body["numResults"] == 3
    assert body["type"] == "auto"
    assert body["contents"] == {"highlights": {"maxCharacters": 1200}}
    assert handler.calls[0]["headers"]["x-api-key"] == "exa-x"


def test_exa_highlights_become_the_normalized_content(tmp_path, monkeypatch) -> None:
    install(monkeypatch, default=exa_ok)
    result = providers.exa_search("q", key="exa-x")

    assert result.hits[0].content == "<em>highlight</em> one"
    assert result.hits[0].published_date == "2026-01-02T00:00:00.000Z"
    assert result.reported_cost == 0.007


def test_serper_uses_the_x_api_key_header_and_maps_organic_results(tmp_path, monkeypatch) -> None:
    handler = install(monkeypatch, default=serper_ok)
    result = providers.serper_search("q", key="serper-x", max_results=4)

    assert handler.calls[0]["headers"]["X-API-KEY"] == "serper-x"
    assert handler.calls[0]["json"] == {"q": "q", "num": 4}
    assert result.hits[0].url == "https://google.example/g"
    assert result.hits[0].published_date == "2 days ago"


def test_serper_reports_when_a_response_also_had_an_answer_box(tmp_path, monkeypatch) -> None:
    install(
        monkeypatch,
        default=httpx.Response(
            200,
            json={
                "organic": [{"title": "G", "link": "https://g.example/1", "snippet": "s"}],
                "answerBox": {"answer": "42", "link": "https://g.example/box"},
            },
        ),
    )
    result = providers.serper_search("q", key="serper-x")

    assert any("answerBox" in note for note in result.notes)


def test_every_provider_call_passes_a_timeout(tmp_path, monkeypatch) -> None:
    def tavily_route(record: dict[str, Any]) -> httpx.Response:
        if record["url"].endswith("/extract"):
            return httpx.Response(200, json={"results": [], "usage": {"credits": 0}})
        return tavily_ok(record)

    handler = install(
        monkeypatch,
        routes={
            "api.tavily.com": tavily_route,
            "api.exa.ai": exa_ok,
            "serper.dev": serper_ok,
        },
    )
    providers.tavily_search("q", key=None)
    providers.tavily_extract(["https://example.com"])
    providers.exa_search("q", key="exa-x")
    providers.serper_search("q", key="serper-x")

    assert [c["timeout"] for c in handler.calls] == [
        providers.DEFAULT_TIMEOUT,
        providers.SLOW_TIMEOUT,
        providers.DEFAULT_TIMEOUT,
        providers.DEFAULT_TIMEOUT,
    ]
    assert all(isinstance(c["timeout"], (int, float)) and c["timeout"] > 0 for c in handler.calls)


def test_duckduckgo_call_is_bounded_and_forwards_its_timeout(monkeypatch) -> None:
    # DuckDuckGo does not use the httpx seam, so its bound is asserted by
    # checking that whatever timeout the caller asked for is what the engine
    # layer actually receives.
    seen: dict[str, object] = {}
    use_real_ddg_adapter(monkeypatch)
    monkeypatch.setattr(
        ddg_tools, "search_web", lambda *a, **k: (seen.update(k), [{"href": "https://ddg.example/1"}])[1]
    )

    providers.duckduckgo_search("q", timeout=7.5)
    assert seen["timeout"] == 7.5
    assert seen["max_results"] == 5


def test_tavily_clamps_max_results_to_the_documented_ceiling(tmp_path, monkeypatch) -> None:
    handler = install(monkeypatch, default=tavily_ok)
    providers.tavily_search("q", key=None, max_results=999)
    providers.tavily_search("q", key=None, max_results=-5)

    assert handler.calls[0]["json"]["max_results"] == 20
    assert handler.calls[1]["json"]["max_results"] == 0


def test_tavily_extract_reports_failed_urls_instead_of_hiding_them(tmp_path, monkeypatch) -> None:
    install(
        monkeypatch,
        default=httpx.Response(
            200,
            json={
                "results": [{"url": "https://example.com", "title": "T", "raw_content": "body"}],
                "failed_results": [{"url": "https://blocked.example", "error": "403 from origin"}],
                "usage": {"credits": 1},
            },
        ),
    )
    result = providers.tavily_extract(["https://example.com", "https://blocked.example"])

    assert len(result.hits) == 1
    assert any("1 URL(s) failed to extract" in note for note in result.notes)
    assert result.cost.amount == pytest.approx(0.4)


def test_tavily_extract_with_no_results_key_is_a_contract_error(tmp_path, monkeypatch) -> None:
    install(monkeypatch, default=httpx.Response(200, json={"usage": {"credits": 0}}))
    with pytest.raises(ProviderContractError):
        providers.tavily_extract(["https://example.com"])


# ===========================================================================
# 9. DuckDuckGo adapter: the honest keyless floor
# ===========================================================================


def test_duckduckgo_adapter_turns_a_ddg_failure_into_a_typed_error(monkeypatch) -> None:
    use_real_ddg_adapter(monkeypatch)
    monkeypatch.setattr(
        ddg_tools,
        "search_web",
        lambda *a, **k: (_ for _ in ()).throw(ddg_tools.DuckDuckGoSearchError("ConnectError: ddgs engine unreachable")),
    )
    with pytest.raises(ProviderUnavailableError) as excinfo:
        providers.duckduckgo_search("q")

    assert "engine unreachable" in str(excinfo.value)
    assert excinfo.value.provider == "duckduckgo"
    assert is_retryable(excinfo.value) is True


def test_duckduckgo_adapter_normalizes_keyless_results(monkeypatch) -> None:
    use_real_ddg_adapter(monkeypatch)
    monkeypatch.setattr(
        ddg_tools,
        "search_web",
        lambda *a, **k: [
            {"title": "T", "href": "https://ddg.example/1", "body": "b"},
            {"title": "no url", "body": "dropped"},
        ],
    )
    result = providers.duckduckgo_search("q")

    assert [hit.url for hit in result.hits] == ["https://ddg.example/1"]
    assert result.cost.amount == 0.0
    assert result.cost.unit == "query"


def test_ddg_search_web_raises_on_failure_and_returns_empty_only_for_real_emptiness(monkeypatch) -> None:
    import sys

    class ExplodingDDGS:
        def __init__(self, timeout: int) -> None:
            self.timeout = timeout

        def text(self, query: str, **kwargs):
            raise RuntimeError("engine unreachable")

    class EmptyDDGS(ExplodingDDGS):
        def text(self, query: str, **kwargs):
            return []

    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=ExplodingDDGS))
    with pytest.raises(ddg_tools.DuckDuckGoSearchError):
        ddg_tools.search_web("q")

    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(DDGS=EmptyDDGS))
    assert ddg_tools.search_web("q") == []


def test_ddg_search_web_raises_when_the_package_is_missing(monkeypatch) -> None:
    import builtins
    import sys

    monkeypatch.setitem(sys.modules, "ddgs", None)
    real_import = builtins.__import__

    def no_ddgs(name, *args, **kwargs):
        if name == "ddgs":
            raise ImportError("no module named ddgs")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_ddgs)
    with pytest.raises(ddg_tools.DuckDuckGoSearchError) as excinfo:
        ddg_tools.search_web("q")

    assert "pip install ddgs" in str(excinfo.value)


# ===========================================================================
# 10. Hardware truth: retired, contested, licence-blocked
# ===========================================================================


def test_bing_search_is_encoded_as_retired_and_refused() -> None:
    row = truth.PROVIDER_TRUTH["bing_search"]
    assert row.availability == "retired"
    assert "410" in row.evidence
    with pytest.raises(ProviderRetiredError) as excinfo:
        truth.assert_selectable("bing_search")

    assert excinfo.value.status_code == 410
    assert "2025-08-11" in row.evidence


def test_google_custom_search_is_encoded_as_retired_with_the_2027_migration_deadline() -> None:
    row = truth.PROVIDER_TRUTH["google_custom_search"]
    assert row.availability == "retired"
    assert "100 queries/day" in row.summary
    assert "2027-01-01" in row.evidence


def test_brave_is_marked_contested_and_refused_by_default() -> None:
    row = truth.PROVIDER_TRUTH["brave_search_api"]
    assert row.availability == "contested"
    assert row.selectable is False
    with pytest.raises(ProviderUnverifiedError) as excinfo:
        truth.assert_selectable("brave_search_api")

    assert "contested" in str(excinfo.value)
    assert "2,000 queries/month" in row.summary and "$5/month" in row.summary


def test_searxng_and_firecrawl_are_documented_as_licensing_decisions_for_the_owner() -> None:
    notes = {entry["name"]: entry for entry in truth.licensing_notes()}
    assert set(notes) == {"searxng", "firecrawl"}
    assert "AGPL" in notes["searxng"]["license"]
    assert "LICENSING DECISION FOR THE OWNER" in notes["searxng"]["decision"]
    assert "LICENSE GATE" in notes["firecrawl"]["decision"]


def test_no_retired_contested_or_licence_blocked_provider_is_in_the_wired_chain() -> None:
    wired = set(PROVIDER_ORDER)
    for blocked in ("bing_search", "google_custom_search", "brave_search_api", "searxng", "firecrawl"):
        assert blocked not in wired
        assert blocked not in set(providers.PROVIDERS)
        assert blocked not in {name for chain in MODE_CHAINS.values() for name in chain}


def test_a_provider_missing_from_the_truth_table_is_refused_not_assumed_fine() -> None:
    with pytest.raises(ProviderUnverifiedError) as excinfo:
        truth.status_of("some_new_search_api")

    assert "no status row" in str(excinfo.value)


def test_contested_provider_in_a_chain_is_skipped_unless_explicitly_opted_in(tmp_path, monkeypatch) -> None:
    handler = install(monkeypatch, default=tavily_ok)
    contested_budget = budgets.BudgetSpec(
        provider="brave_search_api",
        kind=budgets.ONE_TIME,
        unit="query",
        allowance=2000.0,
        summary="contested: 2,000/month or $5/month, sources disagree",
        source="conflicting third-party summaries",
    )
    contested = providers.SearchProviderSpec(
        name="brave_search_api",
        base_url="https://api.search.brave.com",
        search_path="/res/v1/web/search",
        auth_env=("BRAVE_SEARCH_API_KEY",),
        auth_style="x-api-key",
        budget=contested_budget,
        supports=frozenset({providers.MODE_WEB}),
        default_rank=1,
    )
    layer = SimpleNamespace(
        PROVIDERS={**providers.PROVIDERS, "brave_search_api": contested},
        PROVIDER_ORDER=("brave_search_api", "tavily_keyless"),
        BUDGET_SPECS={**BUDGET_SPECS, "brave_search_api": contested_budget},
        tavily_credit_cost=providers.tavily_credit_cost,
        exa_credit_cost=providers.exa_credit_cost,
        serper_credit_cost=providers.serper_credit_cost,
        nominal_cost=providers.nominal_cost,
        credential=providers.credential,
        tavily_search=providers.tavily_search,
        exa_search=providers.exa_search,
        exa_find_similar=providers.exa_find_similar,
        serper_search=providers.serper_search,
        duckduckgo_search=providers.duckduckgo_search,
    )
    env = {"BRAVE_SEARCH_API_KEY": "brave-x"}
    chains = {"auto": ("brave_search_api", "tavily_keyless")}

    refused = SearchFederation(
        layer=layer,  # type: ignore[arg-type]
        clock=lambda: 1_760_000_000.0,
        health_path=tmp_path / "h1.json",
        budget_path=tmp_path / "b1.json",
        env=env,
        chains=chains,
    )
    plan, skipped = refused.plan(SearchRequest(query="q"))
    assert "brave_search_api" not in plan
    assert dict(skipped)["brave_search_api"].startswith("contested: ")

    opted_in = SearchFederation(
        layer=layer,  # type: ignore[arg-type]
        clock=lambda: 1_760_000_000.0,
        health_path=tmp_path / "h2.json",
        budget_path=tmp_path / "b2.json",
        env=env,
        allow_unverified_providers=True,
        chains=chains,
    )
    plan_in, _ = opted_in.plan(SearchRequest(query="q"))
    assert "brave_search_api" in plan_in
    assert handler.calls == [], "planning must not make network calls"


# ===========================================================================
# 11. Cooldown policy stays in sync with the free-LLM router (no import)
# ===========================================================================


def test_cooldown_constants_match_the_free_llm_router_without_importing_it() -> None:
    # Importing alpha.models costs ~50s, so the values are compared by parsing
    # the source instead. That still fails the build if the two drift.
    source = (
        Path(__file__).resolve().parents[1]
        / "packages"
        / "harness"
        / "alpha"
        / "models"
        / "free_router"
        / "catalog.py"
    )
    assert source.is_file(), f"expected the free-LLM router catalog at {source}"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    constants: dict[str, float] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in {"COOLDOWN_BASE_SECONDS", "COOLDOWN_MAX_SECONDS", "COOLDOWN_JITTER"}:
                assert isinstance(node.value, ast.Constant), f"{name} is no longer a literal constant"
                constants[name] = float(node.value.value)

    assert constants == {
        "COOLDOWN_BASE_SECONDS": COOLDOWN_BASE_SECONDS,
        "COOLDOWN_MAX_SECONDS": COOLDOWN_MAX_SECONDS,
        "COOLDOWN_JITTER": COOLDOWN_JITTER,
    }
    assert COOLDOWN_BASE_SECONDS == 15.0
    assert COOLDOWN_MAX_SECONDS == 600.0
    assert COOLDOWN_JITTER == 0.2


# ===========================================================================
# 12. Tool surface
# ===========================================================================


def test_tools_are_registered_with_the_expected_names() -> None:
    from alpha.community.search_federation import tools as sf_tools

    assert sf_tools.web_search_tool.name == "web_search"
    assert sf_tools.web_find_similar_tool.name == "web_find_similar"
    assert sf_tools.web_search_providers_tool.name == "web_search_providers"


def test_web_search_tool_returns_results_json(monkeypatch, tmp_path) -> None:
    from alpha.community.search_federation import tools as sf_tools

    install(monkeypatch, default=tavily_ok)
    monkeypatch.setattr(sf_tools, "_tool_config", lambda: None)
    monkeypatch.setattr(sf_tools, "_build_federation", lambda: make_federation(tmp_path))

    payload = json.loads(sf_tools.web_search_tool.invoke({"query": "capital of australia", "max_results": 2}))
    assert payload["provider"] == "tavily_keyless"
    assert payload["total_results"] == 2
    assert payload["results"][0]["url"] == "https://example.com/a"
    assert payload["cost"]["unit"] == "tavily_credit"


def test_web_search_tool_reports_exhaustion_as_a_typed_error_not_no_results(monkeypatch, tmp_path) -> None:
    from alpha.community.search_federation import tools as sf_tools

    install(monkeypatch, default=http_error(500, {"detail": {"error": "down"}}))
    ddg_down(monkeypatch)
    monkeypatch.setattr(sf_tools, "_tool_config", lambda: None)
    monkeypatch.setattr(sf_tools, "_build_federation", lambda: make_federation(tmp_path))

    payload = json.loads(sf_tools.web_search_tool.invoke({"query": "anything"}))
    assert "results" not in payload
    assert payload["error"]["kind"] == "search_exhausted"
    assert payload["error"]["attempts"]
    assert payload["query"] == "anything"


def test_web_search_tool_docstring_names_every_error_kind(monkeypatch) -> None:
    from alpha.community.search_federation import tools as sf_tools

    doc = sf_tools.web_search_tool.description
    for kind in ("search_exhausted", "budget_exhausted", "request_rejected", "auth", "unavailable"):
        assert kind in doc, f"the agent must be told what {kind!r} means"
    for var in ("TAVILY_API_KEY", "EXA_API_KEY", "SERPER_API_KEY"):
        assert var in doc


def test_web_search_providers_tool_lists_not_wired_providers_and_credit_costs() -> None:
    from alpha.community.search_federation import tools as sf_tools

    payload = json.loads(sf_tools.web_search_providers_tool.invoke({}))
    names = {entry["name"] for entry in payload["not_wired"]}
    assert {"bing_search", "google_custom_search", "brave_search_api", "searxng", "firecrawl"} <= names
    assert payload["credit_costs"]["tavily_search_advanced"] == "2 credits"
    assert payload["credit_costs"]["tavily_search_basic"] == "1 credit (fast/ultra-fast also 1)"
    assert {p["name"] for p in payload["providers"]} == set(PROVIDER_ORDER)


def test_describe_reports_health_budget_and_never_leaks_a_key(monkeypatch, tmp_path) -> None:
    install(monkeypatch, default=tavily_ok)
    fed = make_federation(tmp_path, env={"EXA_API_KEY": "exa-super-secret"})
    fed.search(SearchRequest(query="q"))

    described = json.dumps(fed.describe())
    assert "exa-super-secret" not in described
    view = {entry["name"]: entry for entry in fed.describe()["providers"]}
    assert view["tavily_keyless"]["configured"] is False
    assert view["tavily_keyless"]["keyless"] is True
    assert view["exa"]["configured"] is True
    assert view["exa"]["missing_env"] == []
    assert view["tavily_keyless"]["healthy"] is True
    assert view["exa"]["healthy"] is None


def test_find_similar_tool_surfaces_the_typed_error_when_exa_is_absent(monkeypatch, tmp_path) -> None:
    from alpha.community.search_federation import tools as sf_tools

    install(monkeypatch, default=tavily_ok)
    monkeypatch.setattr(sf_tools, "_tool_config", lambda: None)
    monkeypatch.setattr(sf_tools, "_build_federation", lambda: make_federation(tmp_path))

    payload = json.loads(sf_tools.web_find_similar_tool.invoke({"url": "https://example.com"}))
    assert payload["error"]["kind"] == "search_exhausted"
    assert "EXA_API_KEY" in payload["error"]["skipped"][0]["reason"]


def test_describe_modes_and_chains_match_the_routing_table() -> None:
    described = make_federation(Path("/tmp/does-not-matter")).describe()
    assert described["mode_chains"] == {mode: list(chain) for mode, chain in MODE_CHAINS.items()}
    assert described["cooldown_policy"]["mirrors"] == "alpha/models/free_router/catalog.py"
    assert described["health_values"].startswith("true=call succeeded")


# ---------------------------------------------------------------------------
# 13. Static guarantees: no literal keys, no new dependencies
# ---------------------------------------------------------------------------


def test_no_literal_api_keys_anywhere_in_the_package() -> None:
    """Scan every string literal in the package for credential-shaped text.

    Deliberately narrow: a naive substring scan would trip over ordinary
    identifiers like ``exa_search`` or ``serper_credit_cost``. What must not
    exist is a *value* that looks like a key - a vendor-prefixed token, a bearer
    literal, or an assignment to a name that ends in ``api_key``.
    """
    key_shaped = re.compile(
        r"(?:tvly[-_][A-Za-z0-9]{8,}"  # Tavily
        r"|exa[-_][A-Za-z0-9]{16,}"  # Exa
        r"|\bBearer\s+(?!\{|tvly-secret|key\b)\S{12,}"  # a real bearer token
        r"|sk-[A-Za-z0-9]{16,}"  # OpenAI-style
        r"|AIza[A-Za-z0-9_\-]{20,})"  # Google
    )
    assignment = re.compile(r"""\b\w*api_?key\w*\s*=\s*["'][^"'$]{8,}["']""", re.IGNORECASE)

    package = Path(providers.__file__).parent
    checked_literals = 0
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                checked_literals += 1
                assert not key_shaped.search(node.value), f"{path.name}:{node.lineno} looks like a literal credential"
            elif isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                for target in targets:
                    assert not assignment.search(f"{target} ="), f"{path.name}:{node.lineno} assigns a literal key"
    assert checked_literals > 50, "the scan should have inspected a real number of literals"


def test_package_imports_nothing_beyond_the_repos_existing_dependencies() -> None:
    allowed = frozenset(
        {
            "alpha",
            "httpx",
            "langchain",
            "langchain_core",
            "pydantic",
            "__future__",
            "ast",
            "collections",
            "dataclasses",
            "datetime",
            "json",
            "logging",
            "math",
            "os",
            "pathlib",
            "random",
            "threading",
            "time",
            "typing",
        }
    )
    package = Path(providers.__file__).parent
    modules: set[str] = set()
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module)
    roots = {name.split(".")[0] for name in modules}
    assert roots <= allowed, f"unexpected imports: {sorted(roots - allowed)}"


def test_a_failure_while_building_the_request_is_reported_not_raised(monkeypatch, tmp_path) -> None:
    from alpha.community.search_federation import tools as sf_tools

    def explode(*args, **kwargs):
        raise RuntimeError("total meltdown")

    install(monkeypatch, default=tavily_ok)
    monkeypatch.setattr(sf_tools, "_tool_config", lambda: None)
    monkeypatch.setattr(sf_tools, "_build_federation", lambda: make_federation(tmp_path))
    monkeypatch.setattr(sf_tools, "SearchRequest", explode)

    payload = json.loads(sf_tools.web_search_tool.invoke({"query": "q"}))
    assert payload["error"]["kind"] == "unavailable"
    assert "total meltdown" in payload["error"]["message"]


def test_an_exhausted_chain_surfaces_as_a_typed_error_from_the_tool(monkeypatch, tmp_path) -> None:
    from alpha.community.search_federation import tools as sf_tools

    install(monkeypatch, default=http_error(500, {"detail": {"error": "down"}}))
    ddg_down(monkeypatch)
    monkeypatch.setattr(sf_tools, "_tool_config", lambda: None)
    monkeypatch.setattr(sf_tools, "_build_federation", lambda: make_federation(tmp_path))

    payload = json.loads(sf_tools.web_search_tool.invoke({"query": "q"}))
    assert "results" not in payload
    assert payload["error"]["kind"] == "search_exhausted"
    assert payload["error"]["attempts"]
    reasons = [entry["reason"] for entry in payload["error"]["skipped"]]
    assert any("EXA_API_KEY" in reason for reason in reasons)
    assert any("SERPER_API_KEY" in reason for reason in reasons)

    # find_similar with no Exa key is a typed error too, never a substitute search.
    find_payload = json.loads(sf_tools.web_find_similar_tool.invoke({"url": "https://example.com"}))
    assert find_payload["error"]["kind"] == "search_exhausted"
    assert "EXA_API_KEY" in find_payload["error"]["skipped"][0]["reason"]


def test_search_hit_to_dict_shape_is_stable() -> None:
    hit = SearchHit(title="T", url="https://e.example", content="c", provider="tavily_keyless", rank=1, score=0.5)
    assert hit.to_dict() == {
        "title": "T",
        "url": "https://e.example",
        "content": "c",
        "provider": "tavily_keyless",
        "rank": 1,
        "score": 0.5,
        "published_date": None,
    }


def test_provider_search_result_reports_predicted_and_reported_cost_separately() -> None:
    result = ProviderSearchResult(
        provider="tavily",
        hits=[],
        cost=CreditCost(1.0, "tavily_credit", "predicted"),
        latency_ms=12.3456,
        reported_cost=2.0,
        reported_cost_unit="tavily_credit",
    )
    view = result.to_dict()
    assert view["predicted_cost"] == {"amount": 1.0, "unit": "tavily_credit", "reason": "predicted"}
    assert view["reported_cost"] == {"amount": 2.0, "unit": "tavily_credit"}
    assert view["latency_ms"] == 12.3


def test_budget_specs_declare_documented_allowances_with_sources() -> None:
    assert BUDGET_SPECS["tavily"].allowance == 1000.0
    assert BUDGET_SPECS["tavily"].kind == budgets.MONTHLY
    assert BUDGET_SPECS["tavily"].unit == "tavily_credit"
    assert BUDGET_SPECS["serper"].allowance == 2500.0
    assert BUDGET_SPECS["serper"].kind == budgets.ONE_TIME
    assert BUDGET_SPECS["exa"].allowance == 10.0
    assert BUDGET_SPECS["exa"].unit == "usd"
    assert BUDGET_SPECS["tavily_keyless"].kind == budgets.UNMETERED
    for spec in BUDGET_SPECS.values():
        assert spec.source.startswith("http"), f"{spec.provider} has no source for its allowance"
