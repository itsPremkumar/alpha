"""``config.yaml -> grounding`` — operator policy for the grounding layer.

Read opportunistically off the app config, exactly as
:mod:`alpha.config.verification_loop_config` does: the grounding layer's guards
are unconditional in code, and a config section here chooses *how much* is
switched on, never *whether* a check can be turned off.

## The one setting that must not exist

There is no ``disable_claim_gate``, no ``trust_model_output``, and no
``skip_side_effect_check``. A self-repair loop that can be satisfied by deleting
its assertion is worse than no loop at all, because it manufactures a green
result. ``alpha.config.verification_loop_config`` refuses to make
``refuse_weakened_tests`` configurable for the same reason, and this section
follows that precedent: what can be configured is budget and breadth, never
whether a premise needs evidence.

## Two defaults worth naming

``require_reuse_probe`` defaults **true**. The reuse ablation is unambiguous that
a compact interface map plus a habit of checking it is what stops an agent
re-implementing its own work — and that giving the model more context makes it
*worse*. Defaulting it off would mean every existing run keeps the behaviour the
evidence says is wrong.

``max_claims`` is bounded and consequential claims are never evicted. The cap
exists so a ledger does not become a second unbounded memory store; the
exclusion exists so the load-bearing premises cannot be the ones dropped.
"""

from __future__ import annotations

import contextlib
import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

__all__ = ["GroundingConfig", "resolve_grounding_config"]


class GroundingConfig(BaseModel):
    """Operator policy for the grounding layer."""

    model_config = {"extra": "forbid"}

    enabled: bool = Field(
        default=True,
        description=(
            "Master switch for the manifest injection and the claim ledger. When false the step gates are still enforced, because a gate is a correctness check and not a feature. Disabling this removes context injection, not verification."
        ),
    )
    inject_manifest: bool = Field(
        default=True,
        description=(
            "Put the capability manifest in the agent's context at task start. The ablation behind this shows a "
            "compact interface map more than doubles reuse of the agent's own earlier work (30.0% -> 67.8%) while "
            "handing over the full source achieves nothing (29.2%). Never widen this to source text."
        ),
    )
    require_reuse_probe: bool = Field(
        default=True,
        description=(
            "Refuse a step that produced work without consulting the manifest or existing code. Setting this false "
            "keeps the probe but reports it as advisory, so the run stays measurable; the duplication rate then "
            "reflects a probe that was not enforced."
        ),
    )
    max_claims: int = Field(
        default=500,
        ge=16,
        le=10_000,
        description=("Ledger capacity. Consequential claims are never evicted to make room, so hitting this ceiling stops eviction rather than forgetting a live premise."),
    )
    max_steps: int = Field(
        default=8,
        ge=1,
        le=200,
        description=("Attempt allowance before the effort brake engages. Deliberately small: accuracy against reasoning length is inverted-U, and extended reasoning is associated with abandoning a correct answer."),
    )
    max_consecutive_failures: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Identical failures tolerated before the ladder is told to change approach rather than repeat.",
    )
    max_identical_retries: int = Field(
        default=1,
        ge=0,
        le=5,
        description=("Verbatim retries allowed. One, because a second identical failure is evidence rather than bad luck."),
    )
    memory_require_structural_pass: bool = Field(
        default=True,
        description=(
            "Refuse a memory write whose text trips the deterministic screen. Start strict and relax against "
            "observed false positives; LLM-based memory guards are measured to miss 66% of poisoned entries, so the "
            "model check is the second layer and never the first."
        ),
    )
    decay_window_seconds: float = Field(
        default=2_592_000.0,
        ge=0.0,
        le=31_536_000.0,
        description=("Age window for trust-weighted retrieval decay. Decays toward a floor, never to zero, and is combined with trust rather than used alone — decay alone hands attackers recency bias."),
    )


def resolve_grounding_config() -> GroundingConfig:
    """Read ``grounding`` off the app config when present, else defaults.

    **Absent** falls back to the defaults, because ``AppConfig`` does not
    reference this section yet and an older config file must still start.

    **Present but invalid raises.** A misspelled key or an out-of-range budget is
    an operator error, and silently ignoring it is how a setting looks applied
    while doing nothing — the exact defect class this package removes. Note the
    asymmetry with :func:`alpha.config.verification_loop_config`, which falls back
    on a malformed section too: that section is not wired into ``AppConfig``, so
    a validation error there can only ever be this model's own over-strictness. Once
    ``AppConfig`` carries the field, ``extra="forbid"`` reports the typo at load
    time and this branch becomes unreachable.

    Only breadth and budget are configurable; see the module docstring for the
    settings that deliberately do not exist.
    """
    try:
        from alpha.config.app_config import get_app_config

        section = getattr(get_app_config(), "grounding", None)
    except ImportError:
        # The config package is not importable in a bare/embedded context; the
        # defaults keep the grounding layer usable there. Declared rather than
        # hidden, because this is a real degradation a reader should be able to find.
        with contextlib.suppress(Exception):
            logger.debug("grounding config: app config unavailable; using defaults", exc_info=True)
        return GroundingConfig()
    if section is None:
        return GroundingConfig()
    if isinstance(section, GroundingConfig):
        return section
    if isinstance(section, dict):
        # Raises on an invalid section by design -- see the docstring.
        return GroundingConfig.model_validate(section)
    return GroundingConfig()


def service_config_kwargs(config: GroundingConfig) -> dict[str, object]:
    """Translate policy into :class:`~alpha.grounding.service.GroundingService` kwargs.

    Kept here so the mapping from config field to behaviour lives in one place and
    cannot drift between the config model and the constructor.
    """
    return {
        "inject_manifest": config.enabled and config.inject_manifest,
        "pipeline": None,  # resolved by the caller; see build_pipeline
    }


def build_pipeline(config: GroundingConfig):  # noqa: ANN201 - GatePipeline, annotated loosely to avoid a config->engine import cycle
    """The gate pipeline this policy implies."""
    from alpha.grounding.gates import GatePipeline

    return GatePipeline.with_reuse(required=config.require_reuse_probe)
