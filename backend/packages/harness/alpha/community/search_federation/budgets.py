"""Per-provider credit economics and a persisted budget ledger.

Why this module exists
----------------------
The free tiers this package uses are *metered*, and their units differ:

======================  =========================  ==========================
provider                unit                       allowance
======================  =========================  ==========================
Tavily (keyed)          API credit                 1,000 credits/month, refreshes
Tavily Extract          API credit                 1 credit per 5 successful URLs
Exa                     US dollars                 $10, refreshes to $10 monthly
Serper                  query                      2,500 free queries, one-time
Tavily keyless          (not billed)               separate anonymous rate limit
DuckDuckGo              (not billed)               none
======================  =========================  ==========================

A single "rate limit" notion cannot express that, so the ledger tracks a
*floating-point amount in the provider's own unit* against a documented
allowance, and it is told the cost of each call *before* making it. When the
remaining allowance cannot cover the next call, the provider is **skipped**, not
called-and-failed: paying a guaranteed-error round trip on every search is
exactly the behaviour that burns a user's quota and hides the real problem.

The silent-quota-halving trap
-----------------------------
Tavily costs 1 credit for ``search_depth: basic``/``fast``/``ultra-fast`` and
**2 credits for ``advanced``**, and ``auto_parameters: true`` costs 2 credits
regardless. So leaving the depth to a default, or letting Tavily pick, quietly
doubles the bill. ``providers.tavily_credit_cost`` therefore takes the depth as
a required argument and ``tavily_search`` always sends it explicitly; the cost
is also reported back in the tool payload so a user can see it.
(Verified live against api.tavily.com on 2026-09-26: basic -> usage.credits 1,
advanced -> usage.credits 2, extract of 2 URLs -> usage.credits 0.)

Period handling
---------------
* ``monthly`` allowances roll over on the UTC calendar month. Rollover clears a
  provider-reported exhaustion flag, because that flag described a balance that
  has since been refilled.
* ``one_time`` allowances never roll over; once spent, spent.
* ``unmetered`` providers are always eligible and cost nothing locally.

Persistence is best-effort: state lives in ``runtime_home()/search_budget.json``
(atomic tmp + replace) and a read-only runtime dir degrades to in-memory rather
than breaking search.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alpha.config.runtime_paths import runtime_home

logger = logging.getLogger(__name__)

BUDGET_CACHE_FILENAME = "search_budget.json"

#: Allowance kinds.
UNMETERED = "unmetered"
MONTHLY = "monthly"
ONE_TIME = "one_time"


@dataclass(frozen=True)
class CreditCost:
    """What one call is predicted to cost, and why.

    ``unit`` must match the owning provider's :attr:`BudgetSpec.unit` — the
    ledger refuses to mix units, which is what stops a Tavily credit from being
    subtracted from a Serper query count.
    """

    amount: float
    unit: str
    reason: str

    def __str__(self) -> str:
        return f"{self.amount:g} {self.unit} ({self.reason})"


@dataclass(frozen=True)
class BudgetSpec:
    """Documented economics of one provider's free allowance."""

    provider: str
    kind: str
    unit: str
    allowance: float
    summary: str
    source: str

    @property
    def metered(self) -> bool:
        return self.kind != UNMETERED


@dataclass
class ProviderBudgetState:
    """Ledger row for one provider (JSON-serializable)."""

    provider: str
    #: "YYYY-MM" for monthly allowances; "" for one-time/unmetered.
    period: str = ""
    consumed: float = 0.0
    #: Set when the provider itself told us the balance is gone. Distinct from
    #: "our local estimate says we are close" - this is the provider's word.
    provider_reported_exhausted: bool = False
    provider_reported_at: float | None = None
    provider_reported_note: str | None = None
    last_cost: float | None = None
    last_cost_reason: str | None = None


def _period_key(now: float) -> str:
    return datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m")


class BudgetLedger:
    """Thread-safe, persisted, per-provider allowance accounting."""

    def __init__(
        self,
        *,
        specs: dict[str, BudgetSpec],
        path: Path | str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._specs = specs
        self._clock = clock
        # Accept a str: these paths routinely arrive from YAML config, where every
        # scalar is a string, and a str would otherwise explode on first read.
        self._path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._state: dict[str, ProviderBudgetState] = {name: ProviderBudgetState(provider=name) for name in specs}
        self._load()

    # ------------------------------------------------------------------
    # Persistence (atomic; corrupt state degrades to empty, never to wrong)
    # ------------------------------------------------------------------

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = runtime_home() / BUDGET_CACHE_FILENAME
        return self._path

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("search budget ledger unreadable (%s); starting empty", type(exc).__name__)
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("providers"), dict):
            logger.warning("search budget ledger has an unexpected shape; starting empty")
            return
        for name, entry in payload["providers"].items():
            state = self._state.get(name)
            if state is None or not isinstance(entry, dict):
                continue
            try:
                state.period = str(entry.get("period") or "")
                state.consumed = float(entry.get("consumed") or 0.0)
                state.provider_reported_exhausted = bool(entry.get("provider_reported_exhausted"))
                reported_at = entry.get("provider_reported_at")
                state.provider_reported_at = float(reported_at) if isinstance(reported_at, (int, float)) else None
                state.provider_reported_note = entry.get("provider_reported_note")
                state.last_cost = entry.get("last_cost")
                state.last_cost_reason = entry.get("last_cost_reason")
            except (TypeError, ValueError):
                logger.warning("search budget ledger entry %r malformed; keeping defaults", name)

    def _save(self) -> None:
        with self._lock:
            payload = {
                "updated_at": self._clock(),
                "providers": {name: asdict(state) for name, state in self._state.items()},
            }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self.path)
        except OSError as exc:
            # Accounting is an optimisation; losing it must not break search.
            logger.warning("could not persist search budget ledger: %s", type(exc).__name__)

    # ------------------------------------------------------------------
    # Rollover
    # ------------------------------------------------------------------

    def _rollover(self, state: ProviderBudgetState, spec: BudgetSpec, now: float) -> bool:
        """Reset a monthly allowance when the UTC month changed.

        Returns True when a rollover happened (so the caller can log it). The
        provider-reported exhaustion flag is cleared here and only here: it
        described a balance that the new period has refilled.
        """
        if spec.kind != MONTHLY:
            return False
        current = _period_key(now)
        if state.period == current:
            return False
        if state.period and state.consumed:
            logger.info("search budget: %s monthly allowance refreshed for %s", spec.provider, current)
        state.period = current
        state.consumed = 0.0
        state.provider_reported_exhausted = False
        state.provider_reported_at = None
        state.provider_reported_note = None
        return True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def state(self, name: str) -> ProviderBudgetState:
        """Live row for ``name``, applying any pending monthly rollover."""
        spec = self._specs.get(name)
        rolled = False
        with self._lock:
            state = self._state.setdefault(name, ProviderBudgetState(provider=name))
            if spec is not None:
                rolled = self._rollover(state, spec, self._clock())
        if rolled:
            self._save()
        return state

    def remaining(self, name: str) -> float:
        """Remaining allowance in the provider's own unit.

        ``inf`` for unmetered providers, and for metered providers with no
        documented allowance on record.
        """
        spec = self._specs.get(name)
        if spec is None or not spec.metered:
            return math.inf
        state = self.state(name)
        return max(0.0, spec.allowance - state.consumed)

    def unit_for(self, name: str) -> str | None:
        """The unit this provider's allowance is denominated in."""
        spec = self._specs.get(name)
        return None if spec is None else spec.unit

    def exhausted(self, name: str) -> bool:
        """True when this provider must be skipped for budget reasons."""
        spec = self._specs.get(name)
        if spec is None or not spec.metered:
            return False
        state = self.state(name)
        if state.provider_reported_exhausted:
            return True
        return state.consumed >= spec.allowance

    def can_spend(self, name: str, cost: CreditCost) -> tuple[bool, str]:
        """Can this provider afford ``cost`` right now? Returns (ok, reason)."""
        spec = self._specs.get(name)
        if spec is None:
            return False, "no budget spec registered"
        if not spec.metered:
            return True, "unmetered"
        if cost.unit != spec.unit:
            # Never silently convert between units; that is how a "budget" turns
            # into fiction.
            return False, f"cost unit {cost.unit!r} does not match provider unit {spec.unit!r}"
        if self.exhausted(name):
            state = self.state(name)
            if state.provider_reported_exhausted:
                note = state.provider_reported_note or "provider reported the allowance is spent"
                return False, f"budget exhausted ({note})"
            return False, f"local allowance spent ({state.consumed:g}/{spec.allowance:g} {spec.unit})"
        left = self.remaining(name)
        if cost.amount > left:
            return False, f"cost {cost.amount:g} {cost.unit} exceeds remaining {left:g} {spec.unit}"
        return True, f"{cost.amount:g} {cost.unit} of {left:g} remaining"

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def record(self, name: str, cost: CreditCost) -> None:
        """Charge ``cost`` against the provider's allowance."""
        spec = self._specs.get(name)
        if spec is None or not spec.metered or cost.amount <= 0:
            return
        with self._lock:
            state = self._state.setdefault(name, ProviderBudgetState(provider=name))
            self._rollover(state, spec, self._clock())
            state.consumed += cost.amount
            state.last_cost = cost.amount
            state.last_cost_reason = cost.reason
        self._save()

    def mark_provider_exhausted(self, name: str, note: str) -> None:
        """Record that the provider itself said the balance is gone.

        This is stronger than our local estimate and must not be second-guessed:
        the ledger stops offering the provider until its period rolls over.
        """
        spec = self._specs.get(name)
        if spec is None or not spec.metered:
            return
        with self._lock:
            state = self._state.setdefault(name, ProviderBudgetState(provider=name))
            self._rollover(state, spec, self._clock())
            state.provider_reported_exhausted = True
            state.provider_reported_at = self._clock()
            state.provider_reported_note = note
        self._save()
        logger.info("search budget: %s reported its allowance is spent (%s)", name, note)

    def mark_provider_replenished(self, name: str) -> None:
        """Clear a provider-reported exhaustion (e.g. after a top-up)."""
        with self._lock:
            state = self._state.get(name)
            if state is None or not state.provider_reported_exhausted:
                return
            state.provider_reported_exhausted = False
            state.provider_reported_at = None
            state.provider_reported_note = None
        self._save()

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Honest per-provider allowance view for the tool payload / docs."""
        view: dict[str, Any] = {}
        for name, spec in sorted(self._specs.items()):
            state = self.state(name)
            entry: dict[str, Any] = {
                "kind": spec.kind,
                "unit": spec.unit,
                "allowance": None if not spec.metered else spec.allowance,
                "summary": spec.summary,
                "source": spec.source,
                "consumed": None if not spec.metered else round(state.consumed, 4),
                "remaining": None if not spec.metered else round(self.remaining(name), 4),
                "provider_reported_exhausted": state.provider_reported_exhausted,
                "provider_reported_note": state.provider_reported_note,
                "last_cost": state.last_cost,
                "last_cost_reason": state.last_cost_reason,
                "period": state.period or None,
            }
            view[name] = entry
        return view


# ---------------------------------------------------------------------------
# Documented economics (data, with sources — not magic numbers in call sites)
# ---------------------------------------------------------------------------

#: Tavily's own published credit costs. Verified live 2026-09-26 via
#: ``include_usage: true`` (basic -> 1, fast -> 1, advanced -> 2).
TAVILY_SEARCH_CREDIT_COSTS: dict[str, int] = {"basic": 1, "fast": 1, "ultra-fast": 1, "advanced": 2}

#: ``auto_parameters: true`` is billed as advanced regardless of the depth you ask for.
TAVILY_AUTO_PARAMETERS_CREDIT_COST = 2

#: Tavily Extract: 1 credit per 5 *successful* extractions (basic depth).
TAVILY_EXTRACT_CREDITS_PER_URL_BASIC = 1 / 5

BUDGET_SPECS: dict[str, BudgetSpec] = {
    "tavily_keyless": BudgetSpec(
        provider="tavily_keyless",
        kind=UNMETERED,
        unit="tavily_credit",
        allowance=0.0,
        summary="Anonymous keyless access. Not billed; subject to a separate undocumented-by-number rate limit.",
        source="https://docs.tavily.com/documentation/keyless",
    ),
    "tavily": BudgetSpec(
        provider="tavily",
        kind=MONTHLY,
        unit="tavily_credit",
        allowance=1000.0,
        summary="1,000 credits/month, refreshes on the billing cycle, no credit card. basic search = 1 credit, advanced = 2.",
        source="https://docs.tavily.com/documentation/api-credits",
    ),
    "exa": BudgetSpec(
        provider="exa",
        kind=MONTHLY,
        unit="usd",
        allowance=10.0,
        summary="$10 of credits on signup, refreshing to $10 on the 1st of each month. Search base price $7/1k requests.",
        source="https://exa.ai/pricing",
    ),
    "serper": BudgetSpec(
        provider="serper",
        kind=ONE_TIME,
        unit="query",
        allowance=2500.0,
        summary="2,500 free queries one-time, then a top-up model at $1.00 per 1,000 queries (credits valid 6 months).",
        source="https://serper.dev/",
    ),
    "duckduckgo": BudgetSpec(
        provider="duckduckgo",
        kind=UNMETERED,
        unit="query",
        allowance=0.0,
        summary="Keyless, unmetered, no account.",
        source="https://duckduckgo.com/ (driven by the already-bundled ddgs client)",
    ),
}
