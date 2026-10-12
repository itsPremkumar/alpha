"""Typed schema for the ``spend`` section of ``config.yaml``.

A dependency-light module — like ``model_catalog_schema.py`` — so ``AppConfig``
can import it at module scope without cycling through the model factory.

Two rules make it fail closed, both matching the rest of the config surface:

* **An unknown mode is a load error**, not a silent fallback to the permissive
  default. A misspelled ``zero_cost`` would otherwise read as ``budgeted`` and
  the operator would believe a budget was enforced.
* **A ceiling is a non-negative number or ``None``**, and ``0.0`` is a *real*
  ceiling rather than a stand-in for "unknown" — the same distinction the
  source plan draws for ``max_cost_usd``. A ``bool`` is refused because
  ``True`` is an ``int`` in Python and ``spend: {max_cost_usd: yes}`` is a
  typo, not a budget of one dollar.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["SPEND_MODES", "SpendConfig"]


#: The permitted ``spend.mode`` values.
#:
#: ``budgeted`` is the default because it is *today's behaviour*: tokens are
#: metered and ``alpha.models.cost_governor`` trips a daily/scope breaker, but
#: nothing refuses a paid model at construction. Defaulting to ``zero_cost``
#: would break every deployment that legitimately pays for a provider.
SPEND_MODES: tuple[str, ...] = ("zero_cost", "budgeted", "unrestricted_by_budget")


class SpendConfig(BaseModel):
    """The ``spend`` section: how much this deployment may spend."""

    model_config = ConfigDict(extra="forbid")

    mode: str = Field(
        default="budgeted",
        description=(
            "zero_cost refuses a paid model at construction, in code, via alpha.budgets.spend.refuse_paid_model "
            "called from create_chat_model. budgeted (the default) is today's behaviour: metered tokens and a "
            "circuit breaker, no refusal at construction. unrestricted_by_budget declares no ceiling and is "
            "still subject to permissions and safety policy."
        ),
    )
    max_cost_usd_per_run: float | None = Field(
        default=None,
        description=("Declared per-run ceiling in USD. None = unstated. 0.0 is a real ceiling, never 'unknown'. Declared here for operator visibility; the enforced ceilings remain cost_governor.BudgetConfig."),
    )
    max_daily_cost_usd: float | None = Field(
        default=None,
        description="Declared daily ceiling in USD. None = unstated. 0.0 is a real ceiling, never 'unknown'.",
    )

    @field_validator("mode")
    @classmethod
    def _known_mode(cls, value: str) -> str:
        if value not in SPEND_MODES:
            raise ValueError(f"spend.mode {value!r} is not one of: {', '.join(SPEND_MODES)}")
        return value

    @field_validator("max_cost_usd_per_run", "max_daily_cost_usd")
    @classmethod
    def _non_negative_ceiling(cls, value: float | None) -> float | None:
        if value is None:
            return value
        if isinstance(value, bool):
            raise ValueError(f"a spend ceiling must be a number or null, got the boolean {value!r}")
        if value < 0:
            raise ValueError(f"a spend ceiling must be non-negative, got {value!r}")
        return value
