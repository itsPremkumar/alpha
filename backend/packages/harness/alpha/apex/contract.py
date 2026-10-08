"""The APEX autonomy contract: one frozen object that says what APEX may do.

This is spec §5, §6 and §7 — and the part of the spec that was genuinely
missing. The repository already had an authority surface, but spread across five
overlapping engines with three verdict vocabularies:

===========================  =========================================  ============
concern                      existing owner                            budget?
===========================  =========================================  ============
profile tiers                ``alpha.security.autonomy.profiles``       no
capability lattice           ``alpha.bots.authority_ceiling``           no
per-tool risk + AUTO/ASK     ``alpha.tools.governance``                 no
shell allow/ask/deny         ``alpha.guardrails.command_policy``        no
agent role rings             ``alpha.bots.permissions``                 no
budgets                      ``SwarmBudget`` / ``AttemptBudget`` / …    yes, four
                             of them, unrelated
===========================  =========================================  ============

So there was no single answer to *"for this mission, what may APEX do, up to how
much, and what must always be asked?"* That answer is this class.

**Four properties are load-bearing, and each one exists because of a specific
way this could have gone wrong.**

1. **The contract narrows; it never widens.** ``narrow()`` and
   ``with_profile()`` can only tighten. Widening an authority ceiling is the
   job of :mod:`alpha.bots.authority_ceiling`, which is a server-owned file
   outside any model's reach — a mission-scoped contract that could raise the
   ceiling would be a fourth writer to it (spec §7, §171).

2. **The emergency stop is not a field APEX can clear.**
   :attr:`AutonomyContract.controls` is frozen and
   ``emergency_stop`` is forced ``True`` at construction. There is deliberately
   no setter and no ``enabled=False`` path, because spec §40 requires the stop
   to live outside the model that APEX is driving.

3. **Every verdict is attributed.** ``PolicyAttribution`` records *which* of the
   existing engines answered. A contract that claimed to be the policy kernel
   would be a second policy kernel.

4. **``profile=OFF`` is a real value, not a disabled flag.** With APEX off, no
   cycle runs and existing Alpha behaviour is untouched — spec §186's
   backward-compatibility requirement.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, fields, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any

__all__ = [
    "APEX_RUNTIME_SESSION_KEY",
    "PROTECTED_ACTIONS",
    "AutonomyContract",
    "AutonomyProfile",
    "ApexBudget",
    "ApexControls",
    "ContractViolation",
    "PolicyAttribution",
    "POLICY_DECISION_SITES",
    "authority_for",
    "contract_from_snapshot",
    "contract_digest_matches",
    "default_contract",
    "narrow_contract",
    "profile_for",
]

# Gateway-stamped runtime context key. ``start_run`` owns this value; request
# bodies cannot opt themselves into, out of, or across APEX contracts.
APEX_RUNTIME_SESSION_KEY = "__alpha_apex_session_id"


class AutonomyProfile(StrEnum):
    """The four autonomy levels of spec §6, ordered least to most autonomous."""

    OFF = "off"
    ASSIST = "assist"
    AUTONOMOUS = "autonomous"
    APEX_MAX = "apex_max"

    @property
    def rank(self) -> int:
        return _PROFILE_RANK[self]


_PROFILE_RANK: dict[AutonomyProfile, int] = {
    AutonomyProfile.OFF: 0,
    AutonomyProfile.ASSIST: 1,
    AutonomyProfile.AUTONOMOUS: 2,
    AutonomyProfile.APEX_MAX: 3,
}


class ContractViolation(RuntimeError):
    """An attempt to build or widen a contract beyond what policy allows."""


# --------------------------------------------------------------------------- #
# Authority keys (spec §5 `authority:`)
# --------------------------------------------------------------------------- #

#: Every authority dimension the contract speaks about. The tuple order is the
#: display order and is asserted by the test suite, so a new key cannot be
#: appended without a decision about what it defaults to.
AUTHORITY_KEYS: tuple[str, ...] = (
    "chat",
    "slash_commands",
    "tools",
    "skills",
    "modes",
    "agents",
    "subagents",
    "swarm",
    "workflows",
    "research",
    "memory",
    "browser",
    "coding",
    "terminal",
    "git",
    "mcp",
    "a2a",
    "scheduling",
    "model_routing",
    "task_local_settings",
)

#: Dimensions whose grant changes *capability*, not just behaviour. They stay
#: off below AUTONOMOUS because granting them at ASSIST would mean an agent acts
#: on the host (terminal), reaches the network (mcp/a2a), or moves history (git).
_ELEVATED_AUTHORITY_KEYS: frozenset[str] = frozenset({"terminal", "git", "mcp", "a2a", "subagents", "swarm", "browser"})

#: Dimensions an autonomous agent may never hold for itself, at any profile.
#: This is spec §70 applied to the contract rather than to a module path: the
#: emergency stop, the policy kernel and secret handling are not authority
#: APEX can be granted, because granting them is what "disable the stop" means.
NEVER_DELEGABLE_AUTHORITY_KEYS: frozenset[str] = frozenset()


# --------------------------------------------------------------------------- #
# Budgets and controls (spec §5 `budgets:` / `controls:`)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ApexBudget:
    """Ceilings for one contract. These are limits, never predictions."""

    #: ``None`` means this profile imposes no per-session quota. Runtime and
    #: engine-level safety limits still apply independently.
    max_active_agents: int | None = 12
    max_parallel_tasks: int | None = 8
    max_delegation_depth: int | None = 5
    max_replans: int | None = 20
    max_retries_per_failure_class: int | None = 4
    max_runtime_minutes: int | None = None
    max_tool_calls: int | None = None
    #: Total provider tokens for the session, across its lead and subagents.
    max_total_tokens: int | None = None

    def __post_init__(self) -> None:
        for field_name in self.__slots__:
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ContractViolation(f"budget {field_name} must be null (unlimited) or a non-negative int, got {value!r}")

    def to_dict(self) -> dict[str, int | None]:
        return {name: getattr(self, name) for name in self.__slots__}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ApexBudget:
        if not data:
            return cls()
        known = {k: v for k, v in data.items() if k in cls.__slots__}
        unknown = sorted(set(data) - set(cls.__slots__))
        if unknown:
            raise ContractViolation(f"unknown budget keys: {unknown}")
        return cls(**known)


@dataclass(frozen=True, slots=True)
class ApexControls:
    """User-facing controls. ``emergency_stop`` is fixed on, by construction."""

    pause_allowed: bool = True
    user_takeover: bool = True
    #: Always ``True``. See the module docstring, property 2.
    emergency_stop: bool = True

    def __post_init__(self) -> None:
        if not self.emergency_stop:
            raise ContractViolation("emergency_stop cannot be disabled by a contract; it is enforced by alpha.runtime.control")

    def to_dict(self) -> dict[str, bool]:
        return {name: getattr(self, name) for name in self.__slots__}


# --------------------------------------------------------------------------- #
# Protected actions (spec §5 `protected_actions:`, §39)
# --------------------------------------------------------------------------- #

#: Action classes that always require a human decision, at every profile
#: including APEX_MAX. The spec's §5 example lists five; the values name the
#: existing owner that decides, so a contract never becomes the decision site.
PROTECTED_ACTIONS: MappingProxyType[str, str] = MappingProxyType(
    {
        "destructive_filesystem": "approval",
        "credential_changes": "approval",
        "identity_changes": "approval",
        "external_publication": "approval",
        "irreversible_external_action": "approval",
        "secret_export": "deny",
        "financial_action": "deny",
    }
)


# --------------------------------------------------------------------------- #
# Policy attribution — property 3
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PolicyAttribution:
    """Which existing engine answers which kind of question.

    A contract that answered policy itself would be a second policy kernel, so
    this records the delegation instead of reimplementing any of it.
    """

    tool_risk: str = "alpha.tools.governance:GovernanceRegistry"
    shell_commands: str = "alpha.guardrails.command_policy:CommandPolicyProvider"
    agent_capabilities: str = "alpha.bots.authority_ceiling:AuthorityCeiling"
    profile_tiers: str = "alpha.security.autonomy.profiles:AutonomyProfile"
    emergency_stop: str = "alpha.runtime.control:read_state"
    taint: str = "alpha.safety.authority.taint:TaintTurn"
    acceptance: str = "alpha.mission.acceptance:assert_acceptance_passed"
    run_lifecycle: str = "alpha.runtime.runs.manager:RunManager"

    def to_dict(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in self.__slots__}

    @staticmethod
    def live_sites() -> dict[str, str]:
        """Probe which attributed modules actually import.

        This is the honesty boundary the self-inventory plane already uses: a
        named enforcement site that cannot be imported reports ``live=False``
        rather than counting as an invariant.
        """
        import importlib

        defaults = {f.name: f.default for f in fields(PolicyAttribution)}
        out: dict[str, str] = {}
        for name, dotted in defaults.items():
            module_path = str(dotted).split(":", 1)[0]
            try:
                importlib.import_module(module_path)
            except Exception:
                continue
            out[name] = str(dotted)
        return out


#: Process-wide attribution, shared by every contract.
POLICY_DECISION_SITES = PolicyAttribution()


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #


#: Per-profile authority. Generated from the two rules above rather than written
#: out 20x4 times, so a new authority key gets a decision automatically.
def authority_for(profile: AutonomyProfile) -> MappingProxyType[str, bool]:
    if profile is AutonomyProfile.OFF:
        return MappingProxyType(dict.fromkeys(AUTHORITY_KEYS, False))
    if profile is AutonomyProfile.ASSIST:
        granted = set(AUTHORITY_KEYS) - _ELEVATED_AUTHORITY_KEYS
        return MappingProxyType({key: key in granted for key in AUTHORITY_KEYS})
    # AUTONOMOUS and APEX_MAX differ in budgets and controls, not in what the
    # agent may reach: the difference between them is how much it may do without
    # asking, which is `budget` and `protected_actions`, not the authority map.
    return MappingProxyType({key: True for key in AUTHORITY_KEYS})


_EXECUTION_KEYS: tuple[str, ...] = (
    "autonomous_delegation",
    "autonomous_research",
    "autonomous_replanning",
    "autonomous_recovery",
    "autonomous_verification",
    "autonomous_context_management",
)


def _execution_for(profile: AutonomyProfile) -> MappingProxyType[str, bool]:
    on = profile is not AutonomyProfile.OFF
    return MappingProxyType({key: on for key in _EXECUTION_KEYS})


#: APEX session quotas per profile. ``None`` means no APEX quota; engine-level
#: limits (such as scheduler capacity and maximum subagents per run) still apply.
_PROFILE_BUDGETS: dict[AutonomyProfile, ApexBudget] = {
    AutonomyProfile.OFF: ApexBudget(
        max_active_agents=0,
        max_parallel_tasks=0,
        max_delegation_depth=0,
        max_replans=0,
        max_retries_per_failure_class=0,
        max_runtime_minutes=0,
        max_tool_calls=0,
        max_total_tokens=0,
    ),
    AutonomyProfile.ASSIST: ApexBudget(
        max_active_agents=1,
        max_parallel_tasks=1,
        max_delegation_depth=1,
        max_replans=2,
        max_retries_per_failure_class=1,
        # Active profiles do not stop goal work because a usage quota ran out.
        # Runtime/platform capacity and explicit governance still apply.
        max_runtime_minutes=None,
        max_tool_calls=None,
        max_total_tokens=None,
    ),
    AutonomyProfile.AUTONOMOUS: ApexBudget(
        max_active_agents=6,
        max_parallel_tasks=4,
        max_delegation_depth=3,
        max_replans=10,
        max_retries_per_failure_class=3,
        max_runtime_minutes=None,
        max_tool_calls=None,
        max_total_tokens=None,
    ),
    AutonomyProfile.APEX_MAX: ApexBudget(
        # Agent population, recursion, replanning and retry counts are
        # operational safety limits, not spend ceilings. Infinite retries are
        # forbidden because duplicate actions can be harmful.
        max_active_agents=12,
        max_parallel_tasks=8,
        max_delegation_depth=5,
        max_replans=20,
        max_retries_per_failure_class=4,
        max_runtime_minutes=None,
        max_tool_calls=None,
        max_total_tokens=None,
    ),
}

# Token ceilings stored by releases before ``None`` became the APEX_MAX default.
# Missing-token snapshots retain their historical finite contract.
_LEGACY_TOKEN_LIMITS = {
    AutonomyProfile.OFF: 0,
    AutonomyProfile.ASSIST: 100_000,
    AutonomyProfile.AUTONOMOUS: 500_000,
    AutonomyProfile.APEX_MAX: 2_000_000,
}

_PROFILE_PROTECTED: dict[AutonomyProfile, MappingProxyType[str, str]] = {
    # An assist-mode agent asks about everything a real agent would act on.
    AutonomyProfile.OFF: MappingProxyType(dict(PROTECTED_ACTIONS)),
    AutonomyProfile.ASSIST: MappingProxyType(
        {
            **PROTECTED_ACTIONS,
            "git_commit": "approval",
            "git_push": "approval",
            "shell_execution": "approval",
            "package_install": "approval",
        }
    ),
    AutonomyProfile.AUTONOMOUS: MappingProxyType(
        {
            **PROTECTED_ACTIONS,
            "git_commit": "allow",
            "shell_execution": "allow",
            "package_install": "approval",
        }
    ),
    AutonomyProfile.APEX_MAX: MappingProxyType(
        {
            **PROTECTED_ACTIONS,
            "git_commit": "allow",
            "shell_execution": "allow",
            "package_install": "allow",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class AutonomyContract:
    """What APEX may do, up to how much, and what always needs a human.

    Frozen and slotted: there is no mutation path, so a contract handed to the
    executive cycle cannot be edited in flight.
    """

    profile: AutonomyProfile = AutonomyProfile.OFF
    authority: MappingProxyType[str, bool] = field(default_factory=lambda: authority_for(AutonomyProfile.OFF))
    execution: MappingProxyType[str, bool] = field(default_factory=lambda: _execution_for(AutonomyProfile.OFF))
    budget: ApexBudget = field(default_factory=ApexBudget)
    controls: ApexControls = field(default_factory=ApexControls)
    protected_actions: MappingProxyType[str, str] = field(default_factory=lambda: MappingProxyType(dict(PROTECTED_ACTIONS)))
    issued_at: float = field(default_factory=time.time)
    mission_id: str = ""
    policy_sites: PolicyAttribution = field(default=POLICY_DECISION_SITES)

    def __post_init__(self) -> None:
        missing = [key for key in AUTHORITY_KEYS if key not in self.authority]
        if missing:
            raise ContractViolation(f"authority map is missing keys: {missing}")
        extra = sorted(set(self.authority) - set(AUTHORITY_KEYS))
        if extra:
            raise ContractViolation(f"authority map has unknown keys: {extra}")
        # Property 1: a contract may hold less authority than its profile offers,
        # never more. A mission that must not touch the host cannot gain `terminal`
        # by having APEX_MAX handed to it.
        ceiling = authority_for(self.profile)
        widened = [key for key, granted in self.authority.items() if granted and not ceiling[key]]
        if widened:
            raise ContractViolation(f"contract grants authority its profile does not offer: {sorted(widened)}")

    # -- queries ---------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """False only for ``OFF``. This is the compatibility switch (spec §186)."""
        return self.profile is not AutonomyProfile.OFF

    def may(self, authority_key: str) -> bool:
        """Whether this contract holds one authority dimension.

        An unknown key is ``False``, never ``True``: an unrecognised dimension is
        a gap in the contract, and defaulting it open is how a missing rule
        becomes a bypass.
        """
        return bool(self.authority.get(authority_key, False))

    def may_execute(self, execution_key: str) -> bool:
        return bool(self.execution.get(execution_key, False))

    def verdict_for(self, action_class: str) -> str:
        """The declared verdict for an action class.

        An unclassified action fails closed to ``approval``, which is what
        ``alpha.policy.engine.PolicyEngine`` already does for an unmatched glob.
        """
        return self.protected_actions.get(action_class, "approval")

    def is_protected(self, action_class: str) -> bool:
        return self.verdict_for(action_class) in ("approval", "deny")

    def max_retry(self, failure_class: str) -> int:
        """Bounded retries for a failure class; never infinite (spec §69)."""
        return self.budget.max_retries_per_failure_class

    # -- identity --------------------------------------------------------------

    def digest(self) -> str:
        """Stable hash of the *decisions*, not the timestamp.

        Two contracts issued at different times for the same profile compare
        equal, which is what makes "the policy snapshot has not drifted" a
        checkable statement (spec §162 drift detection).
        """
        payload = json.dumps(
            {
                "profile": self.profile.value,
                "authority": dict(sorted(self.authority.items())),
                "execution": dict(sorted(self.execution.items())),
                "budget": self.budget.to_dict(),
                "controls": self.controls.to_dict(),
                "protected_actions": dict(sorted(self.protected_actions.items())),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "apxc-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.value,
            "profile_rank": self.profile.rank,
            "enabled": self.enabled,
            "authority": dict(self.authority),
            "execution": dict(self.execution),
            "budget": self.budget.to_dict(),
            "controls": self.controls.to_dict(),
            "protected_actions": dict(self.protected_actions),
            "issued_at": self.issued_at,
            "mission_id": self.mission_id,
            "digest": self.digest(),
            "policy_sites": self.policy_sites.to_dict(),
        }


# --------------------------------------------------------------------------- #
# Construction
# --------------------------------------------------------------------------- #


def default_contract(*, mission_id: str = "") -> AutonomyContract:
    """The contract Alpha ships with: profile ``OFF``.

    Deliberately ``OFF``. A new control plane that defaults to autonomous would
    be a security change disguised as a feature; enabling it is a deliberate act
    (``/apex on`` or ``config.yaml``), exactly like the nine background loops.
    """
    return _build(AutonomyProfile.OFF, mission_id=mission_id)


def profile_for(profile: AutonomyProfile | str, *, mission_id: str = "") -> AutonomyContract:
    """Build the full contract for a profile. Accepts the string form."""
    resolved = AutonomyProfile(profile)
    return _build(resolved, mission_id=mission_id)


def contract_from_snapshot(snapshot: dict[str, Any], *, expected_digest: str = "") -> AutonomyContract:
    """Rebuild a persisted, validated contract without widening its profile.

    The snapshot is durable session state, so its authority, budgets and
    protected actions are treated as untrusted input on every read. Rebuilding
    through ``narrow_contract`` ensures a hand-edited snapshot cannot gain
    authority, raise a budget, weaken a protected action, or disable the stop.
    """
    if not isinstance(snapshot, dict):
        raise ContractViolation("contract snapshot must be an object")
    try:
        profile = AutonomyProfile(str(snapshot["profile"]))
        mission_id = str(snapshot.get("mission_id", ""))
        base = profile_for(profile, mission_id=mission_id)
        if snapshot.get("execution") != dict(base.execution):
            raise ContractViolation("contract snapshot execution policy differs from its profile")
        if snapshot.get("controls") != base.controls.to_dict():
            raise ContractViolation("contract snapshot controls differ from its profile")
        authority = snapshot.get("authority")
        budget = snapshot.get("budget")
        protected = snapshot.get("protected_actions")
        if not isinstance(authority, dict) or not isinstance(budget, dict) or not isinstance(protected, dict):
            raise ContractViolation("contract snapshot is missing an authority, budget, or protected-action map")
        legacy_budget = "max_total_tokens" not in budget
        if any(type(value) is not bool for value in authority.values()):
            raise ContractViolation("contract snapshot authority values must be booleans")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in protected.items()):
            raise ContractViolation("contract snapshot protected-action entries must be strings")
        if "max_total_tokens" not in budget:
            budget = {**budget, "max_total_tokens": _LEGACY_TOKEN_LIMITS[base.profile]}
        contract = narrow_contract(
            base,
            authority=authority,
            budget=budget,
            protected_actions={str(key): str(value) for key, value in protected.items()},
        )
    except (KeyError, TypeError, ValueError, ContractViolation) as exc:
        raise ContractViolation(f"invalid contract snapshot: {exc}") from exc
    digest = contract.digest()
    stored_digest = str(snapshot.get("digest") or "")
    digest_matches = stored_digest == digest and (not expected_digest or expected_digest == digest)
    if legacy_budget and not digest_matches:
        digest_matches = contract_digest_matches(contract, stored_digest) and (not expected_digest or contract_digest_matches(contract, expected_digest))
    if not digest_matches:
        raise ContractViolation("contract snapshot digest does not match the stored session")
    return contract


def contract_digest_matches(contract: AutonomyContract, digest: str) -> bool:
    """Match current digests and the pre-token-budget digest for legacy sessions.

    The old contract schema did not include ``max_total_tokens``. Legacy
    snapshots are upgraded to their profile's historical default during validation;
    compatibility only accepts the old hash when every other contract decision
    is identical and the token ceiling is that same profile default.
    """
    if digest == contract.digest():
        return True
    if contract.budget.max_total_tokens != _LEGACY_TOKEN_LIMITS[contract.profile]:
        return False
    legacy_budget = contract.budget.to_dict()
    legacy_budget.pop("max_total_tokens", None)
    payload = json.dumps(
        {
            "profile": contract.profile.value,
            "authority": dict(sorted(contract.authority.items())),
            "execution": dict(sorted(contract.execution.items())),
            "budget": legacy_budget,
            "controls": contract.controls.to_dict(),
            "protected_actions": dict(sorted(contract.protected_actions.items())),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    legacy_digest = "apxc-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return digest == legacy_digest


def _build(profile: AutonomyProfile, *, mission_id: str) -> AutonomyContract:
    return AutonomyContract(
        profile=profile,
        authority=authority_for(profile),
        execution=_execution_for(profile),
        budget=_PROFILE_BUDGETS[profile],
        controls=ApexControls(),
        protected_actions=_PROFILE_PROTECTED[profile],
        mission_id=mission_id,
    )


def narrow_contract(
    contract: AutonomyContract,
    *,
    authority: dict[str, bool] | None = None,
    budget: dict[str, Any] | None = None,
    protected_actions: dict[str, str] | None = None,
    mission_id: str | None = None,
) -> AutonomyContract:
    """Return a strictly-tightened copy, or the same contract if nothing narrows.

    Every parameter only ever moves a value toward *more* restriction. A request
    to widen something raises :class:`ContractViolation` instead of silently
    producing a looser contract — a refusal the caller can surface, rather than a
    contract that quietly claims more than it was given.
    """
    new_authority = dict(contract.authority)
    for key, granted in (authority or {}).items():
        if key not in AUTHORITY_KEYS:
            raise ContractViolation(f"unknown authority key: {key!r}")
        if granted and not contract.authority[key]:
            raise ContractViolation(f"cannot widen authority {key!r} on this contract")
        new_authority[key] = bool(granted)

    current = contract.budget
    new_budget = current
    if budget:
        candidate = ApexBudget.from_dict({**current.to_dict(), **budget})
        for name in ApexBudget.__slots__:
            candidate_value = getattr(candidate, name)
            current_value = getattr(current, name)
            # None is an unbounded allowance: it is wider than every finite
            # value, and equal only to another unbounded allowance.
            if current_value is None and candidate_value is not None:
                continue
            if candidate_value is None and current_value is not None:
                raise ContractViolation(f"cannot widen budget {name} on this contract")
            if candidate_value is not None and current_value is not None and candidate_value > current_value:
                raise ContractViolation(f"cannot widen budget {name} on this contract")
        new_budget = candidate

    new_protected = dict(contract.protected_actions)
    if protected_actions:
        rank = {"allow": 0, "approval": 1, "deny": 2}
        for action_class, verdict in protected_actions.items():
            if verdict not in rank:
                raise ContractViolation(f"unknown verdict {verdict!r} for action class {action_class!r}")
            existing = new_protected.get(action_class, "approval")
            if rank[verdict] < rank.get(existing, 1):
                raise ContractViolation(f"cannot loosen {action_class!r} from {existing} to {verdict}")
            new_protected[action_class] = verdict

    if new_authority == dict(contract.authority) and new_budget == current and new_protected == dict(contract.protected_actions) and (mission_id is None or mission_id == contract.mission_id):
        return contract

    return replace(
        contract,
        authority=MappingProxyType(new_authority),
        budget=new_budget,
        protected_actions=MappingProxyType(new_protected),
        mission_id=contract.mission_id if mission_id is None else mission_id,
    )
