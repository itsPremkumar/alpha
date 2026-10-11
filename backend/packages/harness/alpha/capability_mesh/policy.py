"""User-selectable decision policy: a resolution table, not intelligence (Feature N / plan §15).

A :class:`DecisionPolicy` is the user's *task-level goal* — "balanced",
"fast", "code-safe"… — and :func:`resolve_policy` turns it into a
:class:`PolicyProfile`: the challenger/verifier appetite, verification
depth, recency requirement and agent-count preference that routing reads.
The plan's point is that this is a policy choice, not a model dropdown.

Three rules decide the shape of this module:

* **A policy is a resolution table, never intelligence.** Every value comes
  from one explicit, inspectable row; nothing here measures, adapts or
  learns, and an unknown policy name is refused with the valid names — a
  typo must not silently resolve to "balanced".
* **Bounds are refused, never clamped.** ``agent_count`` outside 1..64 is a
  refusal at construction; the caller's ``default_agents`` budget is applied
  as an explicit ``min(policy preference, budget)`` so the resolution stays
  a stated rule rather than a hidden clamp.
* **LOCAL cannot verify locality — and says so.** The mesh has no runtime
  locality constraint to check today, so the LOCAL profile's rationale
  discloses that it *declares an intent* it cannot enforce. A profile whose
  guarantee cannot be checked must never read as one that can.

``merge_fingerprint_policy`` may only RAISE a fingerprint's recency
requirement, never lower it: recency is a user-visible task requirement
("answer with current sources"), and a policy that does not itself demand
recency cannot know the task stops needing it — lowering would silently
discard the requirement it was supposed to shape.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from alpha.capability_mesh.fingerprint import SituationFingerprint


class DecisionPolicy(StrEnum):
    """The user-selectable decision policies (plan §15)."""

    BALANCED = "balanced"
    HIGHEST_CONFIDENCE = "highest_confidence"
    FAST = "fast"
    COST_MINIMAL = "cost_minimal"
    LOCAL = "local"
    DEEP = "deep"
    CODE_SAFE = "code_safe"
    RESEARCH_GRADE = "research_grade"


class Depth(StrEnum):
    """How thoroughly an assignment is verified."""

    NONE = "none"
    SAMPLED = "sampled"
    FULL = "full"


#: The declared agent-count bounds. Refused, never clamped: 0 agents routes
#: nothing and an unbounded count is a cost fan-out nobody chose.
MIN_AGENT_COUNT = 1
MAX_AGENT_COUNT = 64


@dataclass(frozen=True)
class PolicyProfile:
    """One resolved decision policy: the knobs routing actually reads.

    ``rationale`` is one honest sentence stating what the profile optimizes
    — and, for LOCAL, what it *cannot* verify. Bounds are refused, not
    clamped.
    """

    policy: DecisionPolicy
    agent_count: int
    wants_challenger: bool
    wants_verifier: bool
    verification_depth: Depth
    recency_required: bool
    rationale: str

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "policy", DecisionPolicy(str(self.policy)))
        except ValueError as error:
            allowed = ", ".join(member.value for member in DecisionPolicy)
            raise ValueError(f"unknown policy {self.policy!r}; expected one of: {allowed}") from error
        try:
            object.__setattr__(self, "verification_depth", Depth(str(self.verification_depth)))
        except ValueError as error:
            allowed = ", ".join(member.value for member in Depth)
            raise ValueError(f"unknown verification_depth {self.verification_depth!r}; expected one of: {allowed}") from error
        if isinstance(self.agent_count, bool) or not isinstance(self.agent_count, int) or not MIN_AGENT_COUNT <= self.agent_count <= MAX_AGENT_COUNT:
            raise ValueError(f"agent_count must be an int in [{MIN_AGENT_COUNT}, {MAX_AGENT_COUNT}], got {self.agent_count!r} (bounds are refused, never clamped)")
        for field in ("wants_challenger", "wants_verifier", "recency_required"):
            if not isinstance(getattr(self, field), bool):
                raise ValueError(f"{field} must be a bool, got {getattr(self, field)!r}")
        if not self.rationale:
            raise ValueError("rationale must be a non-empty sentence: a profile with no stated reason is an unexplainable decision")

    def to_dict(self) -> dict[str, object]:
        return {
            "policy": self.policy.value,
            "agent_count": self.agent_count,
            "wants_challenger": self.wants_challenger,
            "wants_verifier": self.wants_verifier,
            "verification_depth": self.verification_depth.value,
            "recency_required": self.recency_required,
            "rationale": self.rationale,
        }


#: The resolution table: policy -> (agent_count, wants_challenger,
#: wants_verifier, verification_depth, recency_required, rationale). This is
#: a *declared starting policy*, not a measurement — tune it against
#: measured routing outcomes later, one row at a time.
_POLICY_TABLE: dict[DecisionPolicy, tuple[int, bool, bool, Depth, bool, str]] = {
    DecisionPolicy.BALANCED: (4, True, True, Depth.SAMPLED, False, "best expected outcome with reasonable cost and latency"),
    DecisionPolicy.HIGHEST_CONFIDENCE: (6, True, True, Depth.FULL, True, "additional verification for material uncertainties, still bounded by explicit caps"),
    DecisionPolicy.FAST: (2, False, False, Depth.NONE, False, "minimize latency while still meeting acceptance criteria"),
    DecisionPolicy.COST_MINIMAL: (2, False, False, Depth.SAMPLED, False, "use the lowest-cost configuration that meets the quality floor"),
    DecisionPolicy.LOCAL: (4, False, True, Depth.SAMPLED, False, "prefers approved local resources; the mesh cannot verify locality yet, so this profile declares intent it cannot enforce"),
    DecisionPolicy.DEEP: (8, True, True, Depth.FULL, True, "maximize coverage and verification within declared limits"),
    DecisionPolicy.CODE_SAFE: (5, False, True, Depth.FULL, False, "prioritise tests, isolation, patch review and rollback safety"),
    DecisionPolicy.RESEARCH_GRADE: (6, True, True, Depth.FULL, True, "prioritise current primary sources, attribution and recency"),
}


def resolve_policy(policy: DecisionPolicy | str, *, default_agents: int = 4) -> PolicyProfile:
    """Resolve one policy into its profile via the explicit table above.

    ``default_agents`` is the caller's global agent budget: the resolved
    ``agent_count`` is ``min(policy preference, default_agents)``, so a
    cheap policy never spends the budget and an appetite for more agents
    never exceeds what the caller declared. Both numbers are validated
    against the 1..64 bounds; an unknown policy name is refused with the
    valid names listed.
    """
    if isinstance(default_agents, bool) or not isinstance(default_agents, int) or not MIN_AGENT_COUNT <= default_agents <= MAX_AGENT_COUNT:
        raise ValueError(f"default_agents must be an int in [{MIN_AGENT_COUNT}, {MAX_AGENT_COUNT}], got {default_agents!r}")
    try:
        member = DecisionPolicy(policy)
    except ValueError as error:
        allowed = ", ".join(entry.value for entry in DecisionPolicy)
        raise ValueError(f"unknown policy {policy!r}; expected one of: {allowed}") from error
    preferred, challenger, verifier, depth, recency, rationale = _POLICY_TABLE[member]
    return PolicyProfile(
        policy=member,
        agent_count=min(preferred, default_agents),
        wants_challenger=challenger,
        wants_verifier=verifier,
        verification_depth=depth,
        recency_required=recency,
        rationale=rationale,
    )


def merge_fingerprint_policy(fingerprint: SituationFingerprint, profile: PolicyProfile) -> SituationFingerprint:
    """Return a copy of ``fingerprint`` carrying this profile's policy (Feature N).

    ``user_policy`` becomes the profile's name and ``recency_required`` is
    OR-ed: **recency may only be raised by a policy, never lowered** — it
    is a user-visible task requirement, and a policy that does not itself
    demand recency cannot know the task stops needing it, so lowering would
    silently discard the requirement the policy was meant to shape.
    """
    if not isinstance(fingerprint, SituationFingerprint):
        raise ValueError(f"fingerprint must be a SituationFingerprint, got {fingerprint!r}")
    if not isinstance(profile, PolicyProfile):
        raise ValueError(f"profile must be a PolicyProfile, got {profile!r}")
    return replace(fingerprint, user_policy=profile.policy.value, recency_required=fingerprint.recency_required or profile.recency_required)


__all__ = [
    "MAX_AGENT_COUNT",
    "MIN_AGENT_COUNT",
    "DecisionPolicy",
    "Depth",
    "PolicyProfile",
    "merge_fingerprint_policy",
    "resolve_policy",
]
