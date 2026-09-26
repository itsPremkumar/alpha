"""Federated web search: keyless-first, budget-aware, honestly-failing.

Four providers wired in priority order, all keyless or free-tier, all
env-driven, all automatically skipped when their key is unset:

1. **Tavily keyless** - the documented ``X-Tavily-Access-Mode: keyless`` header.
   No account, no key, LLM-shaped scored results. The zero-setup win.
2. **Tavily** (``TAVILY_API_KEY``) - 1,000 credits/month, refreshes, no card.
3. **Exa** (``EXA_API_KEY``) - neural/semantic search and find-similar.
4. **Serper** (``SERPER_API_KEY``) - raw Google SERP JSON.

Plus keyless **DuckDuckGo** as the last resort, so search still answers when
every HTTP provider is down or out of quota.

Quick start
-----------
Register either tool in ``config.yaml``::

    - name: web_search
      group: web
      use: alpha.community.search_federation.tools:web_search_tool
      max_results: 5
      search_depth: basic

Nothing else is required: with no keys set at all, the chain still works.

What this package will not do
-----------------------------
* Return an empty result list because a call failed. Failures are typed errors
  (:mod:`.errors`); "exhausted everything" is
  :class:`~.errors.SearchExhaustedError`, never ``{"results": []}``.
* Send a request without a timeout, or without an explicit ``search_depth``
  where that changes the price.
* Hide a retired API behind a config flag. See :mod:`.truth`.
* Contain a literal credential, or reach for a package the repo does not
  already depend on (``httpx`` only).
"""

from __future__ import annotations

from alpha.community.search_federation.budgets import (
    BUDGET_SPECS,
    BudgetLedger,
    BudgetSpec,
    CreditCost,
)
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
    ProviderRuntimeState,
    SearchFederation,
    SearchOutcome,
    SearchRequest,
    available_providers,
    get_federation,
    reset_federation,
)
from alpha.community.search_federation.providers import (
    PROVIDER_ORDER,
    PROVIDERS,
    ProviderSearchResult,
    SearchHit,
    SearchProviderSpec,
    exa_credit_cost,
    serper_credit_cost,
    tavily_credit_cost,
    tavily_extract_credit_cost,
)
from alpha.community.search_federation.truth import (
    PROVIDER_TRUTH,
    ProviderStatus,
    assert_selectable,
    licensing_notes,
    not_wired_report,
)

__all__ = [
    "BUDGET_SPECS",
    "COOLDOWN_BASE_SECONDS",
    "COOLDOWN_JITTER",
    "COOLDOWN_MAX_SECONDS",
    "MODE_CHAINS",
    "PROVIDERS",
    "PROVIDER_ORDER",
    "PROVIDER_TRUTH",
    "BudgetLedger",
    "BudgetSpec",
    "CreditCost",
    "ProviderAuthError",
    "ProviderBudgetExhaustedError",
    "ProviderContractError",
    "ProviderNotConfiguredError",
    "ProviderRateLimitedError",
    "ProviderRequestRejectedError",
    "ProviderRetiredError",
    "ProviderRuntimeState",
    "ProviderSearchResult",
    "ProviderStatus",
    "ProviderUnavailableError",
    "ProviderUnverifiedError",
    "SearchExhaustedError",
    "SearchFederation",
    "SearchHit",
    "SearchOutcome",
    "SearchProviderError",
    "SearchProviderSpec",
    "SearchRequest",
    "assert_selectable",
    "available_providers",
    "exa_credit_cost",
    "get_federation",
    "is_retryable",
    "licensing_notes",
    "not_wired_report",
    "reset_federation",
    "serper_credit_cost",
    "tavily_credit_cost",
    "tavily_extract_credit_cost",
]
