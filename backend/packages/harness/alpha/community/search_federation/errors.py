"""Typed search-provider errors: every failure mode the agent can reason about.

The rule this module exists to enforce: a search provider failure is **never** an
empty result list. ``web_search`` returning ``[]`` must mean "the provider
answered and genuinely had nothing", never "the call blew up". So every adapter
raises one of the classes below, the federation catches it, records an honest
``(provider, label)`` attempt, and either fails over to the next provider or
raises :class:`SearchExhaustedError` saying exactly why nothing could serve.

Error classes mirror ``alpha.models.free_router.providers.ProviderError`` in
discipline:

* every error carries ``provider`` and ``status_code`` when the upstream HTTP
  status is known, so retryable (429/5xx/network) and deterministic
  (400/401/410) are distinguishable;
* ``failure_label`` is the *class name plus status* only — never the response
  body, never a key, never the query. Error text can end up in agent context
  and in logs, so it must not be a covert data exfiltration channel;
* :class:`SearchExhaustedError` subclasses ``ConnectionError`` so any generic
  retry layer in the repo treats "everything is down right now" as retryable
  instead of as a permanent answer.

The taxonomy is intentionally closed. An adapter that catches an unexpected
exception must map it to :class:`ProviderUnavailableError` or
:class:`ProviderContractError` rather than inventing a class at runtime, so the
agent only ever sees kinds it has a documented meaning for.
"""

from __future__ import annotations

__all__ = [
    "ProviderAuthError",
    "ProviderBudgetExhaustedError",
    "ProviderContractError",
    "ProviderNotConfiguredError",
    "ProviderRateLimitedError",
    "ProviderRequestRejectedError",
    "ProviderRetiredError",
    "ProviderUnavailableError",
    "ProviderUnverifiedError",
    "SearchExhaustedError",
    "SearchProviderError",
    "is_retryable",
]


class SearchProviderError(RuntimeError):
    """Base class: one provider failed in a way we can name.

    ``kind`` is the stable, machine-readable discriminator the agent sees in the
    tool's JSON error payload. Subclasses set it; instances never carry a raw
    upstream body, only a short human-readable ``message``.
    """

    kind = "provider_error"

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        #: The short human-readable reason, kept as its own attribute because
        #: ``str(exc)`` is the prefixed form and the tool payload wants the bare
        #: message.
        self.message = message
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds

    @property
    def failure_label(self) -> str:
        """Honest one-token label: class name (+ HTTP status). Never a payload."""
        if self.status_code is None:
            return type(self).__name__
        return f"{type(self).__name__}(status={self.status_code})"

    def to_dict(self) -> dict[str, object]:
        """JSON-safe view for the tool payload the agent actually reads."""
        return {
            "kind": self.kind,
            "provider": self.provider,
            "message": self.message[:400],
            "status_code": self.status_code,
            "retry_after_seconds": self.retry_after_seconds,
        }


class ProviderNotConfiguredError(SearchProviderError):
    """The provider needs a credential and its env var is unset.

    An unset env var is a *skip*, never an exception: the federation records it
    as a skip reason and moves on. This class exists so the skip reason in the
    tool output is a typed, testable object rather than a bare string.
    """

    kind = "not_configured"


class ProviderRetiredError(SearchProviderError):
    """The upstream product no longer exists (e.g. Bing Search API, HTTP 410).

    Raised instead of attempting the call, so a retired API is never wired into
    a live code path by accident.
    """

    kind = "retired"


class ProviderUnverifiedError(SearchProviderError):
    """The provider's free tier is contradicted across sources.

    Refused by default even when a key is present, because building on a tier
    that may not exist is worse than not building on it. Opt in explicitly via
    ``allow_unverified_providers``.
    """

    kind = "unverified"


class ProviderAuthError(SearchProviderError):
    """HTTP 401/403: the key is missing, wrong, or lacks scope.

    Deterministic for this key. The federation puts the provider into cooldown
    (it will keep failing) but does not treat it as budget exhaustion.
    """

    kind = "auth"


class ProviderRateLimitedError(SearchProviderError):
    """HTTP 429, or a provider-specific rate tag. Usually transient."""

    kind = "rate_limited"


class ProviderBudgetExhaustedError(SearchProviderError):
    """The account is out of credits / over plan limit / out of one-time quota.

    This is the one failure the federation handles as a **skip**, not an error:
    the provider is marked exhausted in the budget ledger so later calls route
    around it for the rest of the allowance period, instead of paying a failed
    round trip every single search. See ``budgets.BudgetLedger``.
    """

    kind = "budget_exhausted"


class ProviderRequestRejectedError(SearchProviderError):
    """HTTP 400/422 (or an equivalent 4xx): our request was invalid.

    Deterministic — retrying the identical request cannot help — but it is a bug
    on our side, not a dead provider, so the failure label keeps the status.
    """

    kind = "request_rejected"


class ProviderUnavailableError(SearchProviderError):
    """Transport failure or 5xx: the provider could not be reached or broke.

    Retryable. Covers DNS/connect/TLS errors and read timeouts.
    """

    kind = "unavailable"


class ProviderContractError(SearchProviderError):
    """HTTP 2xx whose body we cannot use (not JSON, or missing the results key).

    Deliberately distinct from "0 results": a response we cannot parse is a
    failure we must not launder into an empty answer.
    """

    kind = "contract"


class SearchExhaustedError(ConnectionError):
    """No configured provider could serve the request.

    Raised instead of returning "no results found". The agent must be able to
    tell "the web genuinely has nothing for this" (a 200 with an empty result
    list, returned normally) apart from "every search backend is unavailable or
    out of quota right now" (this error).

    Attributes:
        attempts: honest ``(provider, label)`` pairs for providers that were
            actually called and failed. Never payloads.
        skipped: honest ``(provider, reason)`` pairs for providers that were
            never called — no credential, retired, unverified, cooling down,
            or out of budget.
        retry_after_seconds: earliest time a skipped provider becomes
            attemptable again, when that is knowable.
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: list[tuple[str, str]] | None = None,
        skipped: list[tuple[str, str]] | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.attempts = list(attempts or [])
        self.skipped = list(skipped or [])
        self.retry_after_seconds = retry_after_seconds

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "search_exhausted",
            "message": str(self),
            "attempts": [{"provider": p, "failure": label} for p, label in self.attempts],
            "skipped": [{"provider": p, "reason": reason} for p, reason in self.skipped],
            "retry_after_seconds": self.retry_after_seconds,
        }


#: Kinds that a retry may plausibly fix. Budget exhaustion and request
#: rejection are excluded on purpose: both are deterministic, and retrying them
#: just burns wall-clock time (and, for the latter, the user's money on Exa).
RETRYABLE_KINDS = frozenset({"rate_limited", "unavailable", "search_exhausted"})


def is_retryable(exc: BaseException) -> bool:
    """True when ``exc`` is a search failure a later attempt could still fix."""
    if isinstance(exc, SearchExhaustedError):
        return True
    if isinstance(exc, SearchProviderError):
        return exc.kind in RETRYABLE_KINDS
    return False
