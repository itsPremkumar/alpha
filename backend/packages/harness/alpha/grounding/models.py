"""Shared vocabulary for the grounding layer.

These types are the layer's whole public contract, so they live alone in a
stdlib-only module: a prompt builder, a middleware, and a Gateway router all
import them, and none of them should pay for a config load or a model factory
to do it.

Three words are load-bearing throughout the package and are defined here once:

* **support** — whether a claim has anything behind it (:class:`SupportStatus`).
  It is not a probability. ``DIRECT`` and ``MISSING`` are facts about a
  trajectory; a score would let an unbacked claim buy its way out with
  confidence.
* **measured** — whether a number was observed rather than asserted. Every
  confidence-carrying verdict in this package carries ``measured`` alongside it,
  because the single most expensive agent failure is a self-reported number read
  as an observation.
* **deterministic** — checkable without a model (:class:`GateLayer`). A
  deterministic gate may block. A model gate may only ever *add* suspicion; that
  asymmetry is enforced in :mod:`alpha.grounding.gates`, not left to convention.

Ordering note: every enum here is a :class:`str` enum so it serialises into run
metadata and event payloads without a custom encoder, and comparisons in caller
code are explicit about rank rather than relying on member order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "ClaimKind",
    "Escalation",
    "GateLayer",
    "GateResult",
    "Groundability",
    "SupportStatus",
    "TrustTier",
]


class SupportStatus(StrEnum):
    """How much stands behind a claim.

    The vocabulary is the four states a trajectory auditor needs and is
    deliberately not a confidence scale. ``DIRECT`` means a cited span in the
    trajectory supports the claim; ``WEAK`` means something related was seen but
    does not establish it; ``MISSING`` means nothing was found; ``CONFLICTING``
    means evidence both supports and contradicts it.

    ``CONFLICTING`` is not the worst state to *detect* but it is the most
    expensive to skip: an unsupported claim is visibly absent, while a
    contradicted one is being actively held, and a synthesiser fed both halves
    produces a confident hybrid that never existed in any source.
    """

    DIRECT = "direct"
    WEAK = "weak"
    MISSING = "missing"
    CONFLICTING = "conflicting"


#: Support states that must stop a consequential claim. ``WEAK`` is included:
#: "something related was seen" is exactly how a plausible wrong answer acquires
#: a citation, and letting it through is how a weak support becomes a load-bearing
#: premise two steps later.
BLOCKING_SUPPORT: frozenset[SupportStatus] = frozenset({SupportStatus.MISSING, SupportStatus.CONFLICTING, SupportStatus.WEAK})


class ClaimKind(StrEnum):
    """What a claim is doing in the trajectory.

    The kind decides how a claim may be discharged, so it is recorded at
    registration rather than inferred later.
    """

    #: Something about the world. Discharged by citing an observed span.
    FACT = "fact"
    #: Derived from other claims. Discharged by citing the claims it rests on.
    INFERENCE = "inference"
    #: A commitment the agent will act on. Always consequential.
    DECISION = "decision"
    #: Something taken as true because nothing proved it. Never auto-discharged.
    ASSUMPTION = "assumption"


class Groundability(StrEnum):
    """How a span of output can be checked.

    This is the L0 classification: the question is not "is this true" but "is
    there any way to find out". A span with no possible check is not a small
    problem, it is a class of output that must not be delivered as fact.
    """

    #: Reducible to a file, a tool result, a row, or a URL. Must cite one.
    VERIFIABLE = "verifiable"
    #: Follows from cited spans by reasoning. Must cite the spans.
    INFERENTIAL = "inferential"
    #: A judgement with no external referent. Allowed, must be labelled.
    JUDGMENT = "judgment"
    #: Looks factual but has no available check. Blocked from delivery as fact.
    UNAUTHORIZED = "unauthorized"


class GateLayer(StrEnum):
    """Who performs a check.

    The layer is what stops the classic failure where a model judges its own
    output. A :attr:`MODEL` check may raise a claim to *suspicious*; it can never
    clear a :attr:`DETERMINISTIC` block.
    """

    #: Pure stdlib, no model. Schema, existence, referential integrity.
    DETERMINISTIC = "deterministic"
    #: An external system answered: a file exists, a query returned, a command exited.
    EXTERNAL = "external"
    #: An LLM judged. Advisory only, by construction.
    MODEL = "model"


class Escalation(StrEnum):
    """What an agent may do instead of guessing.

    Abstention is modelled as an **action**, not a sentiment. This mirrors the
    reliability-alignment result: adding "change tools" and "talk to the user" as
    first-class moves is what cuts tool hallucination, because an agent that can
    only proceed or fabricate will fabricate when the capability is missing.
    """

    #: Preconditions are met; proceed.
    PROCEED = "proceed"
    #: A required capability is absent and cannot be obtained.
    ABSTAIN = "abstain"
    #: Ask the user for the missing decision or credential.
    ASK_USER = "ask_user"
    #: A different existing tool can serve the goal.
    CHANGE_TOOL = "change_tool"
    #: A specialist agent owns this domain.
    DELEGATE = "delegate"
    #: Proceed on the subset whose preconditions hold, and name the rest.
    PROCEED_PARTIAL = "proceed_partial"


class TrustTier(StrEnum):
    """How much a stored fact can be trusted, and by what rule.

    Ordered by *authority of origin*, not by how confident the writer felt. This
    is the axis that makes trust-aware retrieval possible: relevance alone lets a
    low-authority write dominate the context window purely by being on-topic.
    """

    #: Produced by the system itself from a local measurement.
    SYSTEM_MEASURED = "system_measured"
    #: The owner stated it directly.
    USER_STATED = "user_stated"
    #: A tool or query returned it in this session.
    TOOL_OBSERVED = "tool_observed"
    #: The model concluded it. Never promoted by repetition.
    MODEL_DERIVED = "model_derived"
    #: From outside the trust boundary and not structurally screened.
    EXTERNAL_UNVERIFIED = "external_unverified"


#: Numeric authority per tier, used for trust-weighted ranking. Deliberately
#: coarse: this orders evidence, it does not adjudicate it.
TRUST_WEIGHT: dict[TrustTier, float] = {
    TrustTier.SYSTEM_MEASURED: 1.0,
    TrustTier.USER_STATED: 0.9,
    TrustTier.TOOL_OBSERVED: 0.75,
    TrustTier.MODEL_DERIVED: 0.4,
    TrustTier.EXTERNAL_UNVERIFIED: 0.15,
}


@dataclass(frozen=True)
class GateResult:
    """One gate's verdict.

    ``blocked`` is the only field that may stop work. ``advice`` is advisory text
    for the agent and never changes the outcome, which is what lets a model-layer
    gate contribute without being able to clear.
    """

    gate: str
    layer: GateLayer
    blocked: bool
    reason: str = ""
    #: Machine-readable cause, stable across releases. ``""`` when not blocked.
    code: str = ""
    #: Remediation offered to the agent. Present only on a block.
    remediation: str = ""
    #: Arbitrary structured detail for run metadata and tests.
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "layer": self.layer.value,
            "blocked": self.blocked,
            "reason": self.reason,
            "code": self.code,
            "remediation": self.remediation,
            "detail": dict(self.detail),
        }

    @classmethod
    def pass_(cls, gate: str, layer: GateLayer, **detail: Any) -> GateResult:
        """A passing result. Named with a trailing underscore so the verdict and
        the constructor never share a name at a call site."""
        return cls(gate=gate, layer=layer, blocked=False, detail=detail)


def blocked_by(results: tuple[GateResult, ...] | list[GateResult]) -> GateResult | None:
    """The first blocking result, or ``None``.

    First-in-order rather than first-seen so a caller can order gates by cost:
    the cheapest deterministic check must be able to pre-empt an expensive one.
    """
    for result in results:
        if result.blocked:
            return result
    return None
