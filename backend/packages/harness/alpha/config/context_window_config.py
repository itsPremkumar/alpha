"""Configuration for context-window pressure and the exhaustion ladder.

Everything here is a **fraction of the usable input window**, not of the
declared one. That is deliberate: the same mis-typed ``0.8`` that
``summarization``'s ``ContextSize`` validator refuses ("write 0.8 for 80%, not
80") would resolve differently against a window that already had the response
reserved out of it, and the two configurations would then disagree about when a
thread is full.

Load-time validation is fail-closed, for the same reason
``ContextSize._validate_value_range`` is: a threshold the context can never
reach is worse than no threshold, because it looks like a working guard.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

#: Bands, weakest first, without ``unknown``. The ordering is a *policy* input:
#: the three thresholds must describe three points on the same line.
BAND_ORDER: tuple[str, ...] = ("nominal", "elevated", "critical", "over")

#: Default occupancy fractions of the **usable input** window. The ``over``
#: default is below 1.0 because a window filled to capacity is a window the
#: model reasons badly over, long before the provider refuses the request.
DEFAULT_ELEVATED_FRACTION = 0.60
DEFAULT_CRITICAL_FRACTION = 0.80
DEFAULT_OVER_FRACTION = 0.95

#: Tokens held back across the response and the next turn. The response reserve
#: is what keeps ``usable_input_window`` below the declared window; the
#: next-turn reserve is what keeps a request that fits *now* from leaving no
#: room for the turn after it.
DEFAULT_OUTPUT_RESERVE_TOKENS = 4096
DEFAULT_NEXT_TURN_RESERVE_TOKENS = 2048
DEFAULT_MINIMUM_USABLE_INPUT_WINDOW = 1024


class ContextWindowConfig(BaseModel):
    """The ``context_window`` section of ``config.yaml``.

    Hot-reloadable in full: every value is read per request by the usage route
    and by the pressure projection, and none of them is startup-only, because
    changing a threshold mid-flight moves the moment compaction fires and
    nothing else — it does not reconfigure a channel table or a checkpointer
    schema the way ``database.checkpoint_channel_mode`` does.
    """

    model_config = {"extra": "forbid"}

    enabled: bool = Field(
        default=True,
        description="Whether context-pressure readings and the exhaustion ladder are computed at all.",
    )
    elevated_fraction: float = Field(
        default=DEFAULT_ELEVATED_FRACTION,
        description="Occupancy fraction of the usable input window at which compaction is offered before the next turn.",
    )
    critical_fraction: float = Field(
        default=DEFAULT_CRITICAL_FRACTION,
        description="Occupancy fraction at which a turn no longer fits the headroom and the ladder is declared.",
    )
    over_fraction: float = Field(
        default=DEFAULT_OVER_FRACTION,
        description="Occupancy fraction at which occupancy is reported as exceeding the usable window. Deliberately below 1.0: a full window is a degraded one, not merely a refused one.",
    )
    output_reserve_tokens: int = Field(
        default=DEFAULT_OUTPUT_RESERVE_TOKENS,
        ge=0,
        description="Tokens held back for the model's own response, subtracted from models[].context_window to derive the usable input window.",
    )
    next_turn_reserve_tokens: int = Field(
        default=DEFAULT_NEXT_TURN_RESERVE_TOKENS,
        ge=0,
        description="Tokens held back so a request that fits now still leaves room for the turn after it.",
    )
    minimum_usable_input_window: int = Field(
        default=DEFAULT_MINIMUM_USABLE_INPUT_WINDOW,
        ge=1,
        description="Floor the usable input window is clamped to when the reserves would consume the declared window, so an unusable declaration reads as 'clamped' rather than 'always over'.",
    )
    escalate_to_larger_window: bool = Field(
        default=True,
        description="Whether the ladder may re-route a run to a model with a strictly larger declared context_window. Costs money and changes the serving model, so an operator may turn it off.",
    )

    @model_validator(mode="after")
    def _validate_band_order(self) -> ContextWindowConfig:
        """Refuse a threshold set that is not three ordered points on one line.

        An inverted or duplicated set is worse than a missing one: the caller
        compares the bands in ladder order, so ``critical < elevated`` would
        report a thread as *more* pressured the emptier it gets, and equal
        thresholds would make the middle band unreachable.
        """
        fractions = {
            "elevated_fraction": self.elevated_fraction,
            "critical_fraction": self.critical_fraction,
            "over_fraction": self.over_fraction,
        }
        for name, value in fractions.items():
            if not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0, 1] (got {value!r}) — write 0.8 for 80%, not 80, and never more than the whole window")
        if not self.elevated_fraction < self.critical_fraction < self.over_fraction:
            raise ValueError(f"context_window thresholds must be strictly increasing: elevated_fraction ({self.elevated_fraction}) < critical_fraction ({self.critical_fraction}) < over_fraction ({self.over_fraction})")
        if self.output_reserve_tokens + self.next_turn_reserve_tokens < 0:
            # Unreachable through the field bounds, kept so a future ``ge=-1``
            # edit fails here rather than producing a negative usable window.
            raise ValueError("context_window reserves must not be negative")
        return self

    def band_thresholds(self) -> dict[str, float]:
        """The three thresholds keyed for :func:`classify_context_pressure`."""
        return {
            "elevated_fraction": self.elevated_fraction,
            "critical_fraction": self.critical_fraction,
            "over_fraction": self.over_fraction,
        }

    def window_spec(
        self,
        *,
        declared_input_window: object | None = None,
    ):
        """Build the spec this configuration derives for one declared window.

        Returns a ``ContextWindowSpec`` without importing
        :mod:`alpha.runtime.context_window` at module scope, so the config
        package stays importable by validators that must not pull the runtime
        in.
        """
        from alpha.runtime.context_window import resolve_context_window

        return resolve_context_window(
            declared_input_window=declared_input_window,
            output_reserve=self.output_reserve_tokens,
            next_turn_reserve=self.next_turn_reserve_tokens,
            minimum_usable_input_window=self.minimum_usable_input_window,
        )


#: Which bands the ladder treats as recovery rather than pre-emption. Exported
#: so the runtime module and its tests cannot disagree about the boundary.
RECOVERY_BANDS: frozenset[str] = frozenset({"critical", "over"})


def get_context_window_config() -> ContextWindowConfig:
    """The defaults, for callers outside the ``AppConfig`` load path."""
    return ContextWindowConfig()


__all__ = [
    "BAND_ORDER",
    "DEFAULT_CRITICAL_FRACTION",
    "DEFAULT_ELEVATED_FRACTION",
    "DEFAULT_MINIMUM_USABLE_INPUT_WINDOW",
    "DEFAULT_NEXT_TURN_RESERVE_TOKENS",
    "DEFAULT_OUTPUT_RESERVE_TOKENS",
    "DEFAULT_OVER_FRACTION",
    "RECOVERY_BANDS",
    "ContextWindowConfig",
    "get_context_window_config",
]
