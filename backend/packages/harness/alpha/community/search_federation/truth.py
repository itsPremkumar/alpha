"""Provider status truth table — including the ones we deliberately do NOT use.

Search-provider marketing pages and blog posts disagree constantly about which
free tiers exist. The failure mode this table exists to prevent is a codebase
that quietly depends on a dead API and degrades to "no results" months later.
So the status of every provider we considered lives here as data, with the
evidence, and the federation *refuses to select* anything whose status is not
one of the two wireable values (``keyless`` / ``free_tier``). Nothing is
inferred at runtime; if a provider is not in this table it is not usable.

Statuses
--------
``keyless``      No account, no key, real results. Always eligible.
``free_tier``    Needs a key; key is free within a documented allowance.
``retired``      The product is gone. Calling it returns 410/404 forever.
                 :class:`~.errors.ProviderRetiredError` — never selected.
``contested``    Sources disagree about whether the free tier still exists.
                 Refused by default; opt in with ``allow_unverified_providers``.
``not_integrated``Works, but we chose not to wire it (see ``why_not``).

Sources
-------
Statuses are dated on purpose. ``verified_on`` is the date the evidence was
checked; a stale row is a maintenance signal, not a licence to assume it still
holds.
"""

from __future__ import annotations

from dataclasses import dataclass

from alpha.community.search_federation.errors import ProviderRetiredError, ProviderUnverifiedError

__all__ = [
    "PROVIDER_TRUTH",
    "SELECTABLE_STATUSES",
    "TRUTH_SNAPSHOT_DATE",
    "ProviderStatus",
    "assert_selectable",
    "licensing_notes",
    "not_wired_report",
    "status_of",
]

#: Date the non-wired rows below were last checked against vendor docs.
TRUTH_SNAPSHOT_DATE = "2026-09-26"


@dataclass(frozen=True)
class ProviderStatus:
    """One row of the truth table.

    ``availability`` is the wireable/unwireable verdict; ``why_not`` carries the
    licensing or policy reasoning for rows we are not integrating, so that
    decision is auditable rather than folklore.
    """

    name: str
    availability: str
    summary: str
    evidence: str
    verified_on: str = TRUTH_SNAPSHOT_DATE
    why_not: str = ""

    @property
    def selectable(self) -> bool:
        return self.availability in SELECTABLE_STATUSES


#: The only two statuses the federation will ever route a query to.
SELECTABLE_STATUSES = frozenset({"keyless", "free_tier"})


PROVIDER_TRUTH: dict[str, ProviderStatus] = {
    # --- Wired: keyless -----------------------------------------------------
    "tavily_keyless": ProviderStatus(
        name="tavily_keyless",
        availability="keyless",
        summary="Tavily Search with the documented keyless access mode; no account, no key, LLM-shaped ranked results.",
        evidence="https://docs.tavily.com/documentation/keyless - send header X-Tavily-Access-Mode: keyless; /search and /extract supported.",
    ),
    "duckduckgo": ProviderStatus(
        name="duckduckgo",
        availability="keyless",
        summary="DuckDuckGo via the already-bundled ddgs client; no account, no quota, weakest ranking of the chain.",
        evidence="Existing alpha.community.ddg_search provider; keyless by construction.",
    ),
    # --- Wired: key required ------------------------------------------------
    "tavily": ProviderStatus(
        name="tavily",
        availability="free_tier",
        summary="Tavily Search/Extract with a free API key: 1,000 credits/month, refreshes monthly, no credit card.",
        evidence="https://docs.tavily.com/documentation/api-credits - Free plan 1,000 credits/month, no credit card required.",
    ),
    "exa": ProviderStatus(
        name="exa",
        availability="free_tier",
        summary="Exa neural/semantic search and find-similar; $10 of credits on signup that refreshes to $10 on the 1st of each month.",
        evidence=(
            "https://exa.ai/pricing - 'The Free Tier gives you $10 in credits (around 1,400 searches) the day you sign up, "
            "and your free balance resets to $10 on the first of every month.' Search base price $7/1k requests."
        ),
    ),
    "serper": ProviderStatus(
        name="serper",
        availability="free_tier",
        summary="Serper Google SERP JSON; 2,500 free queries one-time, then a top-up model at $1.00 per 1,000 queries.",
        evidence="https://serper.dev/ - 'Try 2,500 queries for free'; Starter plan $50 for 50k credits ($1.00/1k), credits valid 6 months.",
    ),
    # --- Known-dead or closed: refused, never routed to ----------------------
    "bing_search": ProviderStatus(
        name="bing_search",
        availability="retired",
        summary="Microsoft Bing Search APIs were RETIRED. The endpoint answers HTTP 410 Gone, permanently.",
        evidence="Microsoft announced retirement with a shutdown date of 2025-08-11; the legacy endpoint has returned 410 since.",
        verified_on="2025-08-11",
        why_not="Retired by the vendor. A 410 is indistinguishable from a routing outage at a glance, so it must not be in the chain.",
    ),
    "google_custom_search": ProviderStatus(
        name="google_custom_search",
        availability="retired",
        summary=(
            "Google Custom Search JSON API still serves existing customers at 100 queries/day, but is CLOSED to new customers; "
            "those who join must migrate before 1 Jan 2027."
        ),
        evidence="Google Custom Search documentation: 100 queries/day free tier, no new customers, migrate-by 2027-01-01.",
        verified_on="2026-09-26",
        why_not="Closed to new signups and on a 100/day cap - unusable for a new deployment and a dead end within the year.",
    ),
    # --- Contested: refused unless explicitly opted in -----------------------
    "brave_search_api": ProviderStatus(
        name="brave_search_api",
        availability="contested",
        summary=(
            "UNVERIFIED. Sources disagree: some document 2,000 queries/month at 1 query/sec, others say the perpetual free tier "
            "was retired and replaced by a $5/month credit. We could not confirm which is current."
        ),
        evidence="Conflicting third-party summaries; Brave's own pricing page did not clearly state a free allowance at check time.",
        why_not="Building on a tier that may not exist is worse than not building on it. Refused unless allow_unverified_providers is set.",
    ),
    # --- Deliberately not integrated ----------------------------------------
    "searxng": ProviderStatus(
        name="searxng",
        availability="not_integrated",
        summary="Self-hosted metasearch. Good option, but AGPL-3.0.",
        evidence="https://github.com/searxng/searxng - GNU AGPL-3.0.",
        why_not=(
            "LICENSING DECISION FOR THE OWNER, NOT OURS: Alpha accepts MIT/Apache-2.0/BSD and treats AGPL as ideas-only, so "
            "SearXNG is neither vendored nor a dependency. It may still be run as an operator's own separate HTTP service and "
            "pointed at via base_url - that keeps the AGPL code out of the repo and out of the process. Taking that decision is "
            "the owner's call, not this package's."
        ),
    ),
    "firecrawl": ProviderStatus(
        name="firecrawl",
        availability="not_integrated",
        summary="Firecrawl scraping/search. Works, but its terms are not a permissive OSS licence and the repo's license gate does not clear it.",
        evidence="Firecrawl is source-available under its own terms, not MIT/Apache-2.0/BSD.",
        why_not=(
            "LICENSE GATE: Alpha accepts MIT/Apache-2.0/BSD only. Firecrawl's terms differ, so it is neither a dependency nor "
            "vendored. The existing alpha.community.firecrawl package predates this decision and is out of this package's scope."
        ),
    ),
}


def status_of(name: str) -> ProviderStatus:
    """Return the truth row for ``name``; refuse unknown providers.

    Unknown is treated as *not selectable* rather than as "probably fine" - the
    whole point of the table is that nothing enters the chain by accident.
    """
    row = PROVIDER_TRUTH.get(name)
    if row is None:
        raise ProviderUnverifiedError(
            name,
            "no status row in the provider truth table; add one with evidence before routing queries to it",
        )
    return row


def assert_selectable(name: str) -> ProviderStatus:
    """Return the row for ``name`` or raise the typed refusal for its status.

    * ``retired``    -> :class:`ProviderRetiredError` (carries the 410 and the
      shutdown date in the message, so the failure explains itself).
    * ``contested``  -> :class:`ProviderUnverifiedError`.
    * ``not_integrated`` -> :class:`ProviderUnverifiedError` (with ``why_not``).
    """
    row = status_of(name)
    if row.availability == "retired":
        raise ProviderRetiredError(name, f"{row.summary} Evidence: {row.evidence}", status_code=410)
    if not row.selectable:
        detail = row.why_not or row.summary
        raise ProviderUnverifiedError(name, f"{row.availability}: {detail}", status_code=None)
    return row


def not_wired_report() -> list[dict[str, str]]:
    """Machine-readable view of everything considered and left out."""
    return [
        {
            "name": row.name,
            "availability": row.availability,
            "summary": row.summary,
            "why_not": row.why_not,
            "evidence": row.evidence,
            "verified_on": row.verified_on,
        }
        for row in sorted(PROVIDER_TRUTH.values(), key=lambda r: r.name)
        if not row.selectable
    ]


def licensing_notes() -> list[dict[str, str]]:
    """Only the rows whose exclusion is a licensing call the owner must confirm."""
    return [
        {"name": row.name, "license": row.evidence, "decision": row.why_not}
        for row in sorted(PROVIDER_TRUTH.values(), key=lambda r: r.name)
        if not row.selectable and row.name in {"searxng", "firecrawl"}
    ]
