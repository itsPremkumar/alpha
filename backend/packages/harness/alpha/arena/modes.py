"""Budget profiles, run modes, and the adaptive router.

The plan calls for ``arena quick`` / ``arena`` / ``arena deep`` /
``arena compare`` / ``arena synthesize`` rather than one fixed cost.
This module owns:

* **profiles** - named bundles of competitors, wave size and
  ceilings; a profile is refused, never clamped, when it exceeds an
  operator ceiling;
* **modes** - what a run is *for* (decide a winner, compare, build
  the best answer);
* **routing** - a deterministic complexity/risk/budget heuristic that
  picks a profile, plus an explicit override. The router never makes
  a network call and never invents cost: it only chooses between
  profiles the operator has already declared.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class RunMode(StrEnum):
    DECIDE = "decide"  # single-elimination bracket to a champion
    COMPARE = "compare"  # everyone judged, no elimination
    SYNTHESIZE = "synthesize"  # bracket + contribution merge
    PLAN = "plan"  # estimate only, nothing is spent


class Profile(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"
    CUSTOM = "custom"


@dataclass(frozen=True)
class ProfileSpec:
    profile: Profile
    agents: int
    wave: int
    final_check: bool
    repair_cycles: int
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.value,
            "agents": self.agents,
            "wave": self.wave,
            "final_check": self.final_check,
            "repair_cycles": self.repair_cycles,
            "description": self.description,
        }


#: Declared profiles. Numbers are the plan's, not guesses: quick = 3,
#: standard = 5, deep = 8-12 (we take 8 to stay inside a power-of-two
#: friendly bracket and the default sub-agent caps).
PROFILES: dict[Profile, ProfileSpec] = {
    Profile.QUICK: ProfileSpec(
        profile=Profile.QUICK,
        agents=3,
        wave=3,
        final_check=False,
        repair_cycles=0,
        description="Smoke competition: 3 competitors, no final check, no repair loop.",
    ),
    Profile.STANDARD: ProfileSpec(
        profile=Profile.STANDARD,
        agents=5,
        wave=4,
        final_check=True,
        repair_cycles=1,
        description="Default: 5 competitors, one repair cycle, final check on.",
    ),
    Profile.DEEP: ProfileSpec(
        profile=Profile.DEEP,
        agents=8,
        wave=4,
        final_check=True,
        repair_cycles=2,
        description="Full competition: 8 competitors, two repair cycles, final check on.",
    ),
}


def spec_for(profile: Profile | str, fallback: ProfileSpec | None = None) -> ProfileSpec:
    if isinstance(profile, str):
        try:
            profile = Profile(profile.lower())
        except ValueError:
            if fallback is not None:
                return fallback
            raise
    if profile is Profile.CUSTOM:
        if fallback is None:
            raise ValueError("profile 'custom' requires explicit parameters")
        return fallback
    return PROFILES[profile]


# ------------------------------------------------------------------ router


@dataclass(frozen=True)
class RouteInputs:
    """Everything the router is allowed to see.

    Deliberately local and cheap: no model call, no network. If the
    caller cannot supply these, the router falls back to STANDARD
    rather than guessing.
    """

    task_length: int = 0
    requirement_count: int = 0
    safety_level: str = "medium"
    available_budget_calls: int | None = None
    explicit_profile: str | None = None


def route(inputs: RouteInputs) -> tuple[Profile, str]:
    """Pick a profile. Returns the profile and the reason, always both.

    Precedence: explicit choice > budget > complexity > risk >
    STANDARD. The reason is the audit trail for why a run cost what
    it cost.
    """
    if inputs.explicit_profile:
        try:
            return Profile(inputs.explicit_profile.lower()), "explicit profile"
        except ValueError:
            pass  # fall through rather than fail a run over a label

    # Budget: never route to a profile whose call count cannot fit.
    if inputs.available_budget_calls is not None:
        affordable = [p for p in (Profile.QUICK, Profile.STANDARD, Profile.DEEP) if _projected_calls(PROFILES[p]) <= inputs.available_budget_calls]
        if not affordable:
            return Profile.QUICK, "budget cannot fit any profile; cheapest chosen"
        if Profile.DEEP in affordable and inputs.requirement_count > 8:
            return Profile.DEEP, "budget allows deep and requirement count is high"
        if Profile.STANDARD in affordable:
            return Profile.STANDARD, "budget allows standard"
        return affordable[0], "budget only allows quick"

    # Risk: critical safety always gets the most scrutiny it can afford.
    if inputs.safety_level in ("high", "critical"):
        return Profile.DEEP, f"safety level '{inputs.safety_level}'"

    # Complexity: long tasks with many requirements get deep.
    if inputs.task_length > 4000 or inputs.requirement_count > 8:
        return Profile.DEEP, "high complexity (length or requirement count)"

    # Cheap signal: a short, simple task does not need 8 competitors.
    if inputs.task_length and inputs.task_length < 400 and inputs.requirement_count <= 2:
        return Profile.QUICK, "low complexity"

    return Profile.STANDARD, "default"


def _projected_calls(spec: ProfileSpec) -> int:
    """Spawn + 5 calls per match for a single-elimination bracket."""
    import math

    n = spec.agents
    rounds = max(1, math.ceil(math.log2(n))) if n > 1 else 1
    matches = n - 1
    calls = n + 5 * matches
    if spec.final_check:
        calls += 1
    # Repair cycles re-dispatch defense work; bound them as one extra
    # defense+judge pass per cycle per match.
    if spec.repair_cycles:
        calls += spec.repair_cycles * 2 * matches
    _ = rounds
    return calls


def describe_modes() -> list[dict[str, Any]]:
    """The honest mode vocabulary: implemented modes, and refusals with reasons.

    Each row carries ``implemented`` so a caller that renders the table cannot
    present ``compare`` or ``synthesize`` as something it can start — the two are
    declared by the plan and refused by name at ``create``/``estimate``, so a
    table that listed them as available would advertise a run that raises.
    """
    return [{"mode": m.value, "implemented": m in IMPLEMENTED_MODES, "description": _MODE_DESCRIPTIONS[m], "refusal_reason": REFUSED_MODES.get(m, "")} for m in RunMode]


_MODE_DESCRIPTIONS: dict[RunMode, str] = {
    RunMode.DECIDE: "Single-elimination bracket until one solution survives. On completion the run also cites a post-bracket tournament report (requirement coverage, claim ledger, merge plan).",
    RunMode.COMPARE: "DECLARED BUT NOT IMPLEMENTED — refused by name at create/estimate. Every candidate would be judged on the rubric with no eliminations; the engine runs a bracket only.",
    RunMode.SYNTHESIZE: (
        "DECLARED BUT NOT IMPLEMENTED as a run mode — refused by name at create/estimate. A `decide` run DOES produce a contribution merge PLAN in its "
        "tournament report; that plan is a proposal, not an executed merge, so it is not this mode."
    ),
    RunMode.PLAN: "Project cost only; no sub-agent call is made. Refuses start.",
}

#: Modes this engine actually runs, read by ``describe_modes`` callers that must
#: not present a declared-but-refused mode as available. The refusals themselves
#: live in ``service._check_mode`` — this is the *reporting* half, and the two
#: are pinned against each other by ``test_arena_profiles.py``.
IMPLEMENTED_MODES: frozenset[RunMode] = frozenset({RunMode.DECIDE, RunMode.PLAN})
#: Declared, refused by name, and why. Published on ``profiles`` so the model
#: reads the refusal from the vocabulary instead of discovering it by failure.
REFUSED_MODES: dict[RunMode, str] = {
    RunMode.COMPARE: "declared by the plan; the executor runs a single-elimination bracket only, so compare is refused rather than degraded into decide",
    RunMode.SYNTHESIZE: "declared by the plan; a decide run emits a merge PLAN in its tournament report, but no mode executes a merge, so synthesize is refused rather than faked",
}


__all__ = [
    "IMPLEMENTED_MODES",
    "REFUSED_MODES",
    "RunMode",
    "Profile",
    "ProfileSpec",
    "PROFILES",
    "spec_for",
    "RouteInputs",
    "route",
    "describe_modes",
]
