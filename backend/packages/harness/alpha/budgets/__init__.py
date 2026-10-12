"""``alpha.budgets`` — spend policy: is this model free, and may we call it?

**Additive.** Nothing here changes today's behaviour unless an operator writes
a ``spend:`` section into ``config.yaml``. With no section the mode is
``budgeted``, which is exactly what already happens.

This package is **not** a second cost accountant. The durable, budget-enforcing
view of spend stays with ``alpha.models.cost_governor.CostGovernor``, the
process-local token ledger stays with ``alpha.runtime.token_meter.TokenMeter``,
and the scoped per-task ceiling stays with ``TokenBudgetMiddleware``. What was
missing is the question those three cannot answer: *before this model is
constructed, may this deployment spend money at all?*

Modules:

* :mod:`alpha.budgets.spend` — ``SpendMode``, ``FreeVerdict``,
  ``is_model_free``, ``estimate_cost_usd``, ``PaidCallRefused``,
  ``refuse_paid_model``.

Contract: ``packages/harness/alpha/budgets/AGENTS.md``.
Tests: ``backend/tests/test_spend_policy.py``.
"""

from alpha.budgets.spend import (
    DEFAULT_COST_INPUT_PER_1K,
    DEFAULT_COST_OUTPUT_PER_1K,
    FreeVerdict,
    PaidCallRefused,
    SpendMode,
    SpendPolicyConfig,
    estimate_cost_usd,
    is_model_free,
    refuse_paid_model,
    resolve_spend_policy,
)

__all__ = [
    "DEFAULT_COST_INPUT_PER_1K",
    "DEFAULT_COST_OUTPUT_PER_1K",
    "FreeVerdict",
    "PaidCallRefused",
    "SpendMode",
    "SpendPolicyConfig",
    "estimate_cost_usd",
    "is_model_free",
    "refuse_paid_model",
    "resolve_spend_policy",
]
