"""Greedy, complementarity-aware team portfolio selection (plan §4.3–§4.5).

A leaderboard picks one winner; a portfolio picks a *team* that
maximizes expected coverage and useful diversity subject to a budget.
This module is the plan's practical first version — a greedy selector
with five explicit steps:

1. the highest-fit eligible entity becomes PRIMARY (hard gates run
   FIRST via :func:`fit.eligible_entities`, so a disabled or
   tool-less passport is refused before any score exists);
2. compute the required capabilities the team does not yet cover;
3. iteratively add the candidate with the best MARGINAL coverage
   gain, adjusted by measured pair value, correlated-failure risk and
   cost pressure;
4. add a VERIFIER only when the policy wants one and an independent
   candidate exists — one with a DIFFERENT model family or provider
   than the primary. If none exists, the slot is left empty and the
   truth travels in ``TeamPlan.independence_note`` (never faked);
5. stop when the marginal expected value falls below
   ``marginal_threshold``, the budget is exhausted, the team size
   cap is reached, or no candidates remain — and say which.

Six rules are load-bearing:

* **Every member's reason carries its marginal number.** A candidate
  added without a recorded expected marginal value is an unexplained
  decision; ``TeamMember.reason`` always names it (plan §4.5).
* **A speculative pair is half value.** A pair estimate with
  ``sample_count == 0`` contributes at HALF its complementarity value
  and flags the member ``speculative=True`` — an unmeasured hunch can
  never drive an asserted synergy.
* **Correlated failure is a penalty, not a footnote.** The shared
  failure-mode risk (from the capability graph) costs
  ``RISK_PENALTY`` per unit of overlap: two specialists who fail the
  same way are worth less together than their solo scores suggest.
* **Unpriced candidates are never assumed free.** Under a budget, an
  unpriced candidate pays ``UNPRICED_PENALTY`` and the reason says
  so; ``total_predicted_cost`` is ``None`` when ANY member is
  unpriced — never a reassuring 0.0.
* **Strategy-card/role variety is NOT diversity.** Diversity is
  computed on the plan's axes (model family, provider, tool path,
  verification method, entity kind). A second member with a different
  *role* but the same family and provider adds no diversity axis.
* **Independence is disclosed, never assumed.** ``independence_note``
  is REQUIRED: a single member is "single specialist; no independent
  verification", and a team whose members all share a model family or
  provider says independence is reduced and names the shared value.
  A team spanning families/providers must not claim reduced
  independence (no false alarm).

Bounds are refused, never clamped: ``max_team_size`` above
``MAX_TEAM_SIZE`` (64) is a refusal by name.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from alpha.capability_mesh.assignment import Role
from alpha.capability_mesh.complementarity import (
    ComplementarityEvidence,
    PairEstimate,
    PairMemory,
    correlated_failure_risk,
)
from alpha.capability_mesh.decomposition import Microtask
from alpha.capability_mesh.fingerprint import SituationFingerprint
from alpha.capability_mesh.fit import FitScore, rank_entities
from alpha.capability_mesh.graph import CapabilityGraph, NodeKind, node_id_for
from alpha.capability_mesh.memory import CapabilityMemory
from alpha.capability_mesh.models import CapabilityPassport
from alpha.capability_mesh.policy import DecisionPolicy, PolicyProfile, resolve_policy

#: The correlated-failure penalty per unit of shared-failure-mode overlap
#: (plan §4.3: the objective penalizes correlated failure). A documented
#: starting policy, to be tuned against measured routing outcomes.
RISK_PENALTY = 0.5

#: The penalty applied to an UNPRICED candidate when a budget is set:
#: an unpriced candidate cannot be assumed free, so it pays a stated
#: cost instead of an imaginary zero.
UNPRICED_PENALTY = 0.1

#: The hard ceiling on team size (plan §4.5: "team size has a hard
#: maximum"). Refused, never clamped.
MAX_TEAM_SIZE = 64

#: The task category pair estimates are read under: the wildcard entry,
#: because team selection answers a task-relative question and the
#: pair memory's keys are category-scoped.
_TEAM_TASK_CATEGORY = "*"

#: The stop reasons a :class:`TeamPlan` may carry.
STOP_REASONS = frozenset({"budget", "no_candidates", "marginal_below_threshold", "max_size"})


class DiversityAxis(StrEnum):
    """The axes along which a team can be genuinely diverse (plan §4.4).

    ``VERIFICATION_METHOD`` is only computable when the entity's graph
    node carries a ``verification_method`` attribute — a passport
    does not declare how it verifies, so without the attribute the
    axis cannot be derived and is simply not counted (never faked).
    """

    MODEL_FAMILY = "model_family"
    PROVIDER = "provider"
    TOOL_PATH = "tool_path"
    VERIFICATION_METHOD = "verification_method"
    ENTITY_KIND = "entity_kind"


@dataclass(frozen=True)
class TeamMember:
    """One entity's seat on the team, with the number behind it (plan §4.5).

    ``marginal_value`` is the predicted coverage gain when this member
    was added — the fraction of required capabilities it newly
    covered. ``reason`` is the observable justification and MUST
    include that number. ``diversity_axes`` lists the axes this member
    ADDS against the team as it stood (the primary establishes the
    first axes). ``speculative`` is True when the member's fit came
    from UNMEASURED priors only, or when a SPECULATIVE pair estimate
    contributed to its selection.
    """

    passport_id: str
    role: Role
    fit_score: float
    marginal_value: float
    reason: str
    added_cost_usd: float | None = None
    diversity_axes: tuple[DiversityAxis, ...] = ()
    speculative: bool = False

    def __post_init__(self) -> None:
        if not self.passport_id:
            raise ValueError("passport_id must be non-empty")
        try:
            object.__setattr__(self, "role", Role(str(self.role)))
        except ValueError as error:
            allowed = ", ".join(member.value for member in Role)
            raise ValueError(f"unknown role {self.role!r}; expected one of: {allowed}") from error
        if isinstance(self.fit_score, bool) or not isinstance(self.fit_score, (int, float)) or not math.isfinite(float(self.fit_score)) or not 0.0 <= float(self.fit_score) <= 1.0:
            raise ValueError(f"fit_score must be a finite number in [0, 1], got {self.fit_score!r}")
        object.__setattr__(self, "fit_score", float(self.fit_score))
        if isinstance(self.marginal_value, bool) or not isinstance(self.marginal_value, (int, float)) or not math.isfinite(float(self.marginal_value)) or float(self.marginal_value) < 0:
            raise ValueError(f"marginal_value must be a finite number >= 0, got {self.marginal_value!r}")
        object.__setattr__(self, "marginal_value", float(self.marginal_value))
        if not self.reason:
            raise ValueError("reason must be non-empty: a member without its marginal number and justification is an unexplained decision")
        if isinstance(self.added_cost_usd, bool) or (self.added_cost_usd is not None and (not isinstance(self.added_cost_usd, (int, float)) or not math.isfinite(float(self.added_cost_usd)) or float(self.added_cost_usd) < 0)):
            raise ValueError(f"added_cost_usd must be a finite number >= 0 or None, got {self.added_cost_usd!r}")
        axes = tuple(self.diversity_axes)
        for axis in axes:
            if not isinstance(axis, DiversityAxis):
                raise ValueError(f"diversity_axes must contain DiversityAxis members, got {axis!r}")
        object.__setattr__(self, "diversity_axes", axes)
        if not isinstance(self.speculative, bool):
            raise ValueError(f"speculative must be a bool, got {self.speculative!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "passport_id": self.passport_id,
            "role": self.role.value,
            "fit_score": self.fit_score,
            "marginal_value": self.marginal_value,
            "reason": self.reason,
            "added_cost_usd": self.added_cost_usd,
            "diversity_axes": [axis.value for axis in self.diversity_axes],
            "speculative": self.speculative,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeamMember:
        return cls(
            passport_id=str(data["passport_id"]),
            # Coerced (and validated with the friendly message) in __post_init__.
            role=data["role"],
            fit_score=float(data["fit_score"]),
            marginal_value=float(data["marginal_value"]),
            reason=str(data["reason"]),
            added_cost_usd=data.get("added_cost_usd"),
            diversity_axes=tuple(DiversityAxis(axis) for axis in data.get("diversity_axes", ())),
            speculative=bool(data.get("speculative", False)),
        )


@dataclass(frozen=True)
class TeamPlan:
    """The selected team and the honest story of how it was chosen.

    ``independence_note`` is REQUIRED: with one member it is exactly
    "single specialist; no independent verification"; when every
    member shares a model family or provider it says independence is
    reduced and names the shared value; otherwise it states the span.
    ``total_predicted_cost`` is None when any member is unpriced (or
    the team is empty) — never a reassuring 0.0. ``stops`` names why
    selection stopped: one of ``budget``, ``no_candidates``,
    ``marginal_below_threshold``, ``max_size``.
    """

    members: tuple[TeamMember, ...]
    uncovered_capabilities: tuple[str, ...]
    independence_note: str
    stops: str
    total_predicted_cost: float | None = None

    def __post_init__(self) -> None:
        members = tuple(self.members)
        for member in members:
            if not isinstance(member, TeamMember):
                raise ValueError(f"members must contain TeamMember rows, got {member!r}")
        object.__setattr__(self, "members", members)
        primaries = [member for member in members if member.role is Role.PRIMARY]
        if len(primaries) > 1:
            ids = ", ".join(member.passport_id for member in primaries)
            raise ValueError(f"a TeamPlan has at most one PRIMARY, got {len(primaries)} ({ids})")
        uncovered = tuple(self.uncovered_capabilities)
        for capability in uncovered:
            if not capability:
                raise ValueError("uncovered_capabilities entries must be non-empty strings")
        object.__setattr__(self, "uncovered_capabilities", tuple(sorted(uncovered)))
        if not self.independence_note:
            raise ValueError("independence_note must be non-empty: team independence is disclosed, never assumed")
        if self.stops not in STOP_REASONS:
            raise ValueError(f"stops must be one of {sorted(STOP_REASONS)}, got {self.stops!r}")
        if isinstance(self.total_predicted_cost, bool):
            raise ValueError(f"total_predicted_cost must be a finite number >= 0 or None, got {self.total_predicted_cost!r}")
        if self.total_predicted_cost is not None:
            tpc = self.total_predicted_cost
            if not isinstance(tpc, (int, float)) or not math.isfinite(float(tpc)) or float(tpc) < 0:
                raise ValueError(f"total_predicted_cost must be a finite number >= 0 or None, got {self.total_predicted_cost!r}")
        # Validate total_predicted_cost matches member costs
        member_costs = [member.added_cost_usd for member in members]
        if member_costs:
            any_unpriced = any(cost is None for cost in member_costs)
            if any_unpriced and self.total_predicted_cost is not None:
                raise ValueError("total_predicted_cost must be None when any member is unpriced")
            if not any_unpriced:
                expected_total = sum(member_costs)
                if self.total_predicted_cost is None:
                    raise ValueError("total_predicted_cost must be set when all members are priced")
                if abs(self.total_predicted_cost - expected_total) > 1e-9:
                    raise ValueError(f"total_predicted_cost {self.total_predicted_cost} contradicts member costs sum {expected_total}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "members": [member.to_dict() for member in self.members],
            "uncovered_capabilities": list(self.uncovered_capabilities),
            "independence_note": self.independence_note,
            "stops": self.stops,
            "total_predicted_cost": self.total_predicted_cost,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TeamPlan:
        return cls(
            members=tuple(TeamMember.from_dict(entry) for entry in data.get("members", ())),
            uncovered_capabilities=tuple(str(capability) for capability in data.get("uncovered_capabilities", ())),
            independence_note=str(data["independence_note"]),
            stops=str(data["stops"]),
            total_predicted_cost=data.get("total_predicted_cost"),
        )


# ------------------------------------------------------------------- helpers


def _requires_capability(passport: CapabilityPassport, capability: str) -> bool:
    """Does this passport declare the capability (measured or not)?"""
    return passport.estimate_for(capability) is not None


def _capability_quality(passport: CapabilityPassport, capability: str) -> float | None:
    """Get the quality_mean for a capability, or None if not declared/unmeasured."""
    estimate = passport.estimate_for(capability)
    if estimate is None:
        return None
    return estimate.quality_mean


def _marginal_quality_gain(
    candidate: CapabilityPassport,
    team: tuple[CapabilityPassport, ...],
    required: tuple[str, ...],
) -> float:
    """Calculate marginal quality gain for a candidate against the current team.

    For each required capability the candidate covers, compute the improvement
    over the best current team member's quality for that capability.
    Sum improvements and divide by total required capabilities.
    """
    if not required:
        return 0.0
    total_gain = 0.0
    for capability in required:
        candidate_quality = _capability_quality(candidate, capability)
        if candidate_quality is None:
            continue
        # Find best quality in current team for this capability
        best_team_quality = 0.0
        for member in team:
            member_quality = _capability_quality(member, capability)
            if member_quality is not None and member_quality > best_team_quality:
                best_team_quality = member_quality
        gain = candidate_quality - best_team_quality
        if gain > 0:
            total_gain += gain
    return total_gain / len(required)


def _predicted_cost(memory: CapabilityMemory | None, entity_id: str, profile_version: str) -> float | None:
    """The entity's mean recorded cost under this profile version, or None.

    Cost is a *measurement*: with no priced records there is no
    number, and None means "unknown" — never 0.0. Records carrying no
    profile version are rubric-agnostic and always count; records
    from another rubric are excluded by the comparability rule.
    """
    if memory is None:
        return None
    costs = [record.cost_usd for record in memory.records(entity_id=entity_id) if record.cost_usd is not None and record.profile_version in ("", profile_version)]
    return sum(costs) / len(costs) if costs else None


def _diversity_axes(
    passport: CapabilityPassport,
    team: tuple[CapabilityPassport, ...],
    graph: CapabilityGraph | None,
) -> tuple[DiversityAxis, ...]:
    """The diversity axes ``passport`` ADDS against the team so far.

    Strategy-card or role variety is deliberately NOT an axis (plan
    §4.4): only model family, provider, tool path, verification
    method and entity kind count. ``VERIFICATION_METHOD`` is derived
    from the entity's graph node attributes when present — a passport
    does not declare how it verifies, so without the attribute the
    axis is not counted rather than guessed.
    """
    axes: list[DiversityAxis] = []
    if passport.model_family and passport.model_family not in {member.model_family for member in team}:
        axes.append(DiversityAxis.MODEL_FAMILY)
    if passport.model_provider and passport.model_provider not in {member.model_provider for member in team}:
        axes.append(DiversityAxis.PROVIDER)
    team_tools = {tool for member in team for tool in member.required_tools}
    if any(tool not in team_tools for tool in passport.required_tools):
        axes.append(DiversityAxis.TOOL_PATH)
    if passport.entity_type not in {member.entity_type for member in team}:
        axes.append(DiversityAxis.ENTITY_KIND)
    if graph is not None:
        node = graph.node(node_id_for(NodeKind.ENTITY, passport.entity_id))
        method = node.attribute("verification_method") if node is not None else None
        if method:
            team_methods = set()
            for member in team:
                member_node = graph.node(node_id_for(NodeKind.ENTITY, member.entity_id))
                if member_node is not None:
                    team_method = member_node.attribute("verification_method")
                    if team_method:
                        team_methods.add(team_method)
            if method not in team_methods:
                axes.append(DiversityAxis.VERIFICATION_METHOD)
    return tuple(axes)


def _is_independent_of(primary: CapabilityPassport, candidate: CapabilityPassport) -> bool:
    """Can the candidate verify the primary independently?

    Independence is provable only on a known axis: the candidate must
    differ from the primary on model family or on provider, with both
    values known. An unknown family on either side proves nothing.
    """
    if primary.model_family and candidate.model_family and primary.model_family != candidate.model_family:
        return True
    if primary.model_provider and candidate.model_provider and primary.model_provider != candidate.model_provider:
        return True
    return False


def _independence_note(members: tuple[TeamMember, ...], passports_by_id: dict[str, CapabilityPassport], verifier_disclosure: str | None) -> str:
    """The REQUIRED independence disclosure (plan §4.4/§4.5)."""
    member_passports = [passports_by_id[member.passport_id] for member in members]
    if len(member_passports) == 1:
        return "single specialist; no independent verification"
    disclosures: list[str] = []
    families = [passport.model_family for passport in member_passports]
    if all(families) and len(set(families)) == 1:
        disclosures.append(f"independence reduced: all {len(families)} members share model family '{families[0]}'")
    providers = [passport.model_provider for passport in member_passports]
    if all(providers) and len(set(providers)) == 1:
        disclosures.append(f"independence reduced: all {len(providers)} members share provider '{providers[0]}'")
    if disclosures:
        note = "; ".join(disclosures)
    else:
        known_families = sorted({family for family in families if family})
        known_providers = sorted({provider for provider in providers if provider})
        note = (
            f"independence preserved: {len(member_passports)} members span "
            f"{len(known_families)} model family(ies) ({', '.join(known_families) or 'unknown'}) and "
            f"{len(known_providers)} provider(s) ({', '.join(known_providers) or 'unknown'})"
        )
    if verifier_disclosure:
        note = f"{note}; {verifier_disclosure}"
    return note


# -------------------------------------------------------------------- public


def select_team(
    required_capabilities: Iterable[str] | Microtask | SituationFingerprint,
    passports: Iterable[CapabilityPassport],
    *,
    policy: PolicyProfile | DecisionPolicy | str,
    memory: CapabilityMemory | None = None,
    pair_memory: PairMemory | None = None,
    graph: CapabilityGraph | None = None,
    profile_version: str,
    budget_usd: float | None = None,
    max_team_size: int | None = None,
    marginal_threshold: float = 0.05,
    now: datetime | None = None,
) -> TeamPlan:
    """Select a small team for the required capabilities (plan §4.3).

    The greedy selector runs the five steps in the module docstring.
    ``required_capabilities`` names what the task needs; hard-gate
    refusals are applied up front (a disabled or tool-less passport
    never scores). ``policy`` resolves to a :class:`PolicyProfile`
    (the agent-count preference is the default size cap;
    ``wants_verifier`` drives step 4). ``memory`` supplies predicted
    costs (mean recorded cost per entity, scoped to the profile
    version); ``pair_memory`` supplies measured pair values (a
    SPECULATIVE pair contributes at half value and flags the member);
    ``graph`` supplies correlated-failure risk. ``budget_usd`` turns
    cost into pressure (and refuses unpriced-as-free);
    ``max_team_size`` defaults to ``policy.agent_count`` and is
    hard-capped at ``MAX_TEAM_SIZE`` (refused above, never clamped);
    ``marginal_threshold`` is the stop floor for adjusted marginal
    value. Every member's reason carries its marginal number, and the
    plan's ``independence_note`` discloses the team's real independence.
    """
    if not profile_version:
        raise ValueError("select_team requires a non-empty profile_version (no score without its profile version)")

    # Extract required_capabilities and required_tools from Microtask or SituationFingerprint if provided
    required_tools: tuple[str, ...] = ()
    if isinstance(required_capabilities, Microtask):
        required = tuple(dict.fromkeys(cap for cap in required_capabilities.required_capabilities if cap))
        required_tools = tuple(dict.fromkeys(tool for tool in required_capabilities.required_tools if tool))
    elif isinstance(required_capabilities, SituationFingerprint):
        required = tuple(dict.fromkeys(cap for cap in required_capabilities.required_capabilities if cap))
    else:
        required = tuple(dict.fromkeys(capability for capability in required_capabilities if capability))
    if not required:
        raise ValueError("required_capabilities must be non-empty: a team without requirements selects nothing")
    for capability in required:
        if not isinstance(capability, str):
            raise ValueError(f"required_capabilities entries must be strings, got {capability!r}")
    roster = list(passports)
    for passport in roster:
        if not isinstance(passport, CapabilityPassport):
            raise ValueError(f"passports must contain CapabilityPassport rows, got {passport!r}")
    if memory is not None and not isinstance(memory, CapabilityMemory):
        raise ValueError(f"memory must be a CapabilityMemory or None, got {memory!r}")
    if pair_memory is not None and not isinstance(pair_memory, PairMemory):
        raise ValueError(f"pair_memory must be a PairMemory or None, got {pair_memory!r}")
    if graph is not None and not isinstance(graph, CapabilityGraph):
        raise ValueError(f"graph must be a CapabilityGraph or None, got {graph!r}")
    if isinstance(budget_usd, bool) or (budget_usd is not None and (not isinstance(budget_usd, (int, float)) or not math.isfinite(float(budget_usd)) or float(budget_usd) <= 0)):
        raise ValueError(f"budget_usd must be a finite number > 0 or None, got {budget_usd!r}")
    if isinstance(marginal_threshold, bool) or not isinstance(marginal_threshold, (int, float)) or not math.isfinite(float(marginal_threshold)) or not 0.0 <= float(marginal_threshold) <= 1.0:
        raise ValueError(f"marginal_threshold must be a finite number in [0, 1], got {marginal_threshold!r}")
    size_cap = policy.agent_count if max_team_size is None else max_team_size
    if isinstance(size_cap, bool) or not isinstance(size_cap, int) or not 1 <= size_cap <= MAX_TEAM_SIZE:
        raise ValueError(f"max_team_size must be an int in [1, {MAX_TEAM_SIZE}], got {size_cap!r} (bounds are refused, never clamped). MAX_TEAM_SIZE is {MAX_TEAM_SIZE}")
    profile = policy if isinstance(policy, PolicyProfile) else resolve_policy(policy)
    passports_by_id = {passport.entity_id: passport for passport in roster}

    # The team-selection task: one microtask carrying the required
    # capabilities and a generic acceptance check, so the fit plane
    # (hard gates first, then ranking) can be reused as-is.
    microtask = Microtask(
        microtask_id="team-selection",
        title="team portfolio selection",
        summary="Select a team covering the required capabilities.",
        required_capabilities=required,
        required_tools=required_tools,
        acceptance_checks=("the team's combined deliverable satisfies every required capability's acceptance criteria",),
    )
    scores, _refusals = rank_entities(microtask, roster, memory=memory, profile_version=profile_version, now=now)
    if not scores:
        return TeamPlan(
            members=(),
            uncovered_capabilities=required,
            independence_note="no eligible entity passed the hard gates: no team could be formed",
            stops="no_candidates",
            total_predicted_cost=None,
        )

    # --- Step 1: the highest-fit eligible entity becomes the PRIMARY.
    primary_score = scores[0]
    primary = passports_by_id[primary_score.entity_id]
    primary_coverage = {capability for capability in required if _requires_capability(primary, capability)}
    primary_binary_marginal = len(primary_coverage) / len(required)
    primary_cost = _predicted_cost(memory, primary.entity_id, profile_version)
    members: list[TeamMember] = [
        TeamMember(
            passport_id=primary.entity_id,
            role=Role.PRIMARY,
            fit_score=primary_score.fit_score,
            marginal_value=primary_binary_marginal,
            reason=(
                f"primary: highest fit {primary_score.fit_score:.3f} among {len(scores)} eligible candidate(s); "
                f"marginal coverage {primary_binary_marginal:.3f} "
                f"({len(primary_coverage)}/{len(required)} required capabilities: {', '.join(sorted(primary_coverage)) or 'none'})"
            ),
            added_cost_usd=primary_cost,
            diversity_axes=_diversity_axes(primary, (), graph),
            speculative=primary_score.estimated,
        )
    ]
    team: list[CapabilityPassport] = [primary]
    team_coverage = set(primary_coverage)
    cumulative_cost = primary_cost if primary_cost is not None else 0.0
    selected_ids = {primary.entity_id}

    # --- Steps 2-3 and 5: iteratively add the best adjusted marginal gain.
    stops = "no_candidates"
    while True:
        if len(members) >= size_cap:
            stops = "max_size"
            break
        remaining = [score for score in scores if score.entity_id not in selected_ids]
        if not remaining:
            stops = "no_candidates"
            break
        if budget_usd is not None and cumulative_cost >= budget_usd:
            stops = "budget"
            break
        evaluated: list[tuple[float, float, FitScore, CapabilityPassport, float, float, float, float, bool, bool]] = []
        for score in remaining:
            candidate = passports_by_id[score.entity_id]
            new_coverage = {capability for capability in required if _requires_capability(candidate, capability) and capability not in team_coverage}
            marginal = _marginal_quality_gain(candidate, tuple(team), required)
            # Measured pair value: the best pair against any seated member.
            pair_bonus = 0.0
            pair_speculative = False
            if pair_memory is not None:
                best_value: float | None = None
                best_pair: PairEstimate | None = None
                for seated_id in selected_ids:
                    estimate = pair_memory.get(candidate.entity_id, seated_id, _TEAM_TASK_CATEGORY)
                    if estimate is None:
                        continue
                    value = estimate.complementarity_value()
                    if value is None:
                        continue
                    if best_value is None or value > best_value:
                        best_value = value
                        best_pair = estimate
                if best_pair is not None and best_value is not None:
                    if best_pair.evidence is ComplementarityEvidence.SPECULATIVE:
                        # A speculative pair is half value, and the member is flagged.
                        pair_bonus = best_value / 2
                        pair_speculative = True
                    else:
                        pair_bonus = best_value
            risk_penalty = 0.0
            if graph is not None:
                worst_risk = 0.0
                for seated_id in selected_ids:
                    risk = correlated_failure_risk(graph, candidate.entity_id, seated_id)
                    if risk.known:
                        worst_risk = max(worst_risk, risk.risk)
                risk_penalty = RISK_PENALTY * worst_risk
            cost = _predicted_cost(memory, candidate.entity_id, profile_version)
            cost_pressure = 0.0
            unpriced = False
            if budget_usd is not None:
                if cost is None:
                    # Unpriced candidates cannot be assumed free: a stated penalty instead of an imaginary zero.
                    cost_pressure = UNPRICED_PENALTY
                    unpriced = True
                else:
                    cost_pressure = cost / budget_usd
                    if cumulative_cost + cost > budget_usd:
                        continue  # unaffordable under the budget: skip, never overspend
            adjusted = marginal + pair_bonus - risk_penalty - cost_pressure
            evaluated.append((adjusted, marginal, score, candidate, cost, cost_pressure, pair_bonus, risk_penalty, pair_speculative, unpriced))
        if not evaluated:
            stops = "budget"
            break
        evaluated.sort(key=lambda row: (-row[0], -row[1], row[2].entity_id))
        adjusted, marginal, best_score, best_candidate, best_cost, best_cost_pressure, best_pair_bonus, best_risk_penalty, best_pair_speculative, best_unpriced = evaluated[0]
        # Always add the first CHALLENGER (first gap-filler) if candidates remain,
        # regardless of marginal_threshold. Apply threshold only for subsequent members.
        if len(members) > 1 and adjusted < marginal_threshold:
            stops = "marginal_below_threshold"
            break
        new_coverage = {capability for capability in required if _requires_capability(best_candidate, capability) and capability not in team_coverage}
        reason_parts = [
            f"gap-fill: marginal coverage {marginal:.3f} ({len(new_coverage)}/{len(required)} newly covered: {', '.join(sorted(new_coverage)) or 'none'})",
            f"adjusted score {adjusted:.3f} = marginal {marginal:.3f} + pair value {best_pair_bonus:+.3f}",
        ]
        if best_pair_speculative:
            reason_parts.append("pair value halved: the pair estimate is SPECULATIVE (no samples), so it contributes at half value")
        reason_parts.append(f"- correlated-failure risk {RISK_PENALTY * 0:.3f}" if graph is None else f"- correlated-failure risk penalty applied (RISK_PENALTY {RISK_PENALTY})")
        if best_unpriced:
            reason_parts.append(f"- unpriced candidate: cost unknown, UNPRICED_PENALTY {UNPRICED_PENALTY} applied (an unpriced candidate is never assumed free)")
        elif budget_usd is not None:
            reason_parts.append(f"- cost pressure {best_cost_pressure:.3f} (predicted ${best_cost:.2f} against a ${budget_usd:.2f})")
        if best_score.estimated or best_pair_speculative:
            reason_parts.append("speculative: fit came from UNMEASURED priors" + (" and/or a speculative pair" if best_pair_speculative else "") + ", not measurements")
        members.append(
            TeamMember(
                passport_id=best_candidate.entity_id,
                role=Role.CHALLENGER,
                fit_score=best_score.fit_score,
                marginal_value=marginal,
                reason="; ".join(reason_parts),
                added_cost_usd=best_cost,
                diversity_axes=_diversity_axes(best_candidate, tuple(team), graph),
                speculative=best_score.estimated or best_pair_speculative,
            )
        )
        team.append(best_candidate)
        team_coverage.update(new_coverage)
        selected_ids.add(best_candidate.entity_id)
        if best_cost is not None:
            cumulative_cost += best_cost

    # --- Step 4: a verifier only when the policy wants one AND an
    # independent candidate exists. Never faked: an empty verifier
    # slot is disclosed in the independence note.
    verifier_disclosure: str | None = None
    if profile.wants_verifier:
        if len(members) >= size_cap:
            verifier_disclosure = "no verifier added: the team is at its size cap"
        else:
            verifier_score = next(
                (score for score in scores if score.entity_id not in selected_ids and _is_independent_of(primary, passports_by_id[score.entity_id])),
                None,
            )
            if verifier_score is None:
                remaining = [score for score in scores if score.entity_id not in selected_ids]
                if not remaining:
                    verifier_disclosure = "no verifier added: no eligible candidate remains"
                else:
                    verifier_disclosure = "no independent verifier exists: every remaining candidate shares the primary's model family and provider"
            else:
                verifier = passports_by_id[verifier_score.entity_id]
                verifier_new = {capability for capability in required if _requires_capability(verifier, capability) and capability not in team_coverage}
                verifier_marginal = len(verifier_new) / len(required)
                independence_basis = (
                    f"model family '{verifier.model_family}' vs the primary's '{primary.model_family}'"
                    if primary.model_family and verifier.model_family and primary.model_family != verifier.model_family
                    else f"provider '{verifier.model_provider}' vs the primary's '{primary.model_provider}'"
                )
                members.append(
                    TeamMember(
                        passport_id=verifier.entity_id,
                        role=Role.VERIFIER,
                        fit_score=verifier_score.fit_score,
                        marginal_value=verifier_marginal,
                        reason=(f"verifier: marginal coverage {verifier_marginal:.3f} ({len(verifier_new)}/{len(required)} newly covered); independent of the primary by {independence_basis}"),
                        added_cost_usd=_predicted_cost(memory, verifier.entity_id, profile_version),
                        diversity_axes=_diversity_axes(verifier, tuple(team), graph),
                        speculative=verifier_score.estimated,
                    )
                )
                team.append(verifier)
                team_coverage.update(verifier_new)
                selected_ids.add(verifier.entity_id)

    uncovered = tuple(capability for capability in required if capability not in team_coverage)
    member_costs = [member.added_cost_usd for member in members]
    total_cost = None if not member_costs or any(cost is None for cost in member_costs) else sum(member_costs)
    return TeamPlan(
        members=tuple(members),
        uncovered_capabilities=uncovered,
        independence_note=_independence_note(tuple(members), passports_by_id, verifier_disclosure),
        stops=stops,
        total_predicted_cost=total_cost,
    )


__all__ = [
    "MAX_TEAM_SIZE",
    "RISK_PENALTY",
    "STOP_REASONS",
    "UNPRICED_PENALTY",
    "DiversityAxis",
    "TeamMember",
    "TeamPlan",
    "select_team",
]
