"""Scoped policy overlays: a baseline plus named, stricter-only scopes.

Why this module exists
----------------------
``alpha/bots/authority_ceiling.py`` holds ONE global authority ceiling.  A
single uniform ceiling is either too tight for a constrained agent or too loose
for a powerful one, and OpenClaw's own evaluation notes the same shape of
problem when it says a feature table "does not establish the security model"
(https://docs.openclaw.ai/start/why-openclaw).  A research bot that may read the
web and a computer-use bot that may drive a screen do not belong in the same
envelope.  This module is the mechanism for holding specific agents, channels
and roles to STRICTER policy than the baseline.

What this module IS
-------------------
An in-process POLICY control.  It narrows authority that a caller in this
process would otherwise hold.  It is **not** a security boundary and must never
be described as one: a policy control constrains cooperating code, and code in
the same trust envelope can reach around it.  The one part that genuinely
cannot be reached around is the authority ceiling itself, which is why the
ceiling is intersected in *after* scope resolution and is not expressible as a
scope field at all.

The monotonicity contract
-------------------------
Three invariants, each of which is a hard refusal rather than a clamp:

1. A scope may **DENY** a capability the baseline allows.
2. A scope may **never ENABLE** a capability the baseline denies.  Attempting it
   is refused at load time by :func:`ScopePolicy.from_dict`, which is the
   boundary that keeps a proposal, a model-authored payload or a hand-edited
   policy file from widening anything.
3. The effective policy is the **strictest union** of the matching scopes and
   the baseline, and the resolution is recorded with a reason so composition is
   auditable rather than implicit.

Reuse
-----
The capability lattice is NOT redefined here.  ``CAPABILITY_RANKS`` from
:mod:`alpha.bots.authority_ceiling` is the single totally-ordered source of
truth, and ``GatePosture`` from :mod:`alpha.safety.authority.models` is reused
for the default-deny vocabulary rather than a second enum.  This module is
therefore *wiring over an existing lattice*, not a parallel one.

Reference for the property being implemented:
https://docs.openclaw.ai/start/why-openclaw/policy-as-code
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Final

# ---------------------------------------------------------------------------
# Rule vocabulary. Every decision a scope resolution records names one of these,
# so "why may this agent not do X" is answerable with a stable identifier
# rather than a sentence that has to be re-interpreted.
# ---------------------------------------------------------------------------


class ScopeRule(StrEnum):
    """Reviewable rule ids for scope composition and denial."""

    BASELINE_ALLOWS = "SCOPE-BASE-001"
    BASELINE_DENIES = "SCOPE-BASE-002"
    SCOPE_DENIES_CAPABILITY = "SCOPE-DENY-001"
    SCOPE_TIGHTENS_RANK = "SCOPE-RANK-001"
    SCOPE_NARROWS_CHANNELS = "SCOPE-CHAN-001"
    SCOPE_NARROWS_ROLES = "SCOPE-ROLE-001"
    SCOPE_REQUIRES_REVIEW = "SCOPE-REVIEW-001"
    SCOPE_FORBIDS_VERIFIED_EVIDENCE = "SCOPE-EVID-001"
    SCOPE_SAWES_RAISED_REVIEW = "SCOPE-REVIEW-002"
    SCOPE_SAWES_FORBIDDEN_EVIDENCE = "SCOPE-EVID-002"
    CEILING_CLAMPS = "SCOPE-CEIL-001"
    NO_SCOPE_APPLIES = "SCOPE-MATCH-000"


class SubjectKind(StrEnum):
    """What a scope binds to."""

    AGENT = "agent"
    CHANNEL = "channel"
    ROLE = "role"


#: Keys a policy payload may use to try to *widen* authority.  Their presence is
#: always a refusal, whatever the value, because a scope that can name an
#: enablement is a scope that can be edited into one.
_ENABLE_ATTEMPT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "allow_capabilities",
        "allow_tools",
        "allowed_capabilities",
        "bypass",
        "bypass_ceiling",
        "disable_ceiling",
        "disable_review",
        "enable_capabilities",
        "escalate",
        "grant_capabilities",
        "override_ceiling",
        "permit",
        "require_approval",
        "required_capabilities",
        "unlock",
        "widen",
    }
)

#: Boolean flags whose ``True``/``False`` value determines strictness.  A scope
#: may only ever set them in the stricter direction.
_STRICTER_ONLY_TRUE: Final[frozenset[str]] = frozenset(
    {
        "deny_protected_components",
        "fail_closed",
        "require_human_review",
        "taint_bounds_authority",
    }
)
_STRICTER_ONLY_FALSE: Final[frozenset[str]] = frozenset(
    {
        "allow_verified_evidence",
        "allow_unauthenticated_inbound",
    }
)


class ScopeError(RuntimeError):
    """Base class for scope failures. A ``RuntimeError``, never a ``ValueError``.

    A caller must not be able to swallow a security refusal as ordinary bad
    input.  This mirrors :class:`alpha.bots.authority_ceiling.AuthorityViolation`.
    """


class ScopeEscalationRefused(ScopeError):
    """A scope attempted to be LOOSER than the baseline. Refused, never clamped."""

    def __init__(self, message: str, *, scope: str = "", violations: list[str] | None = None) -> None:
        super().__init__(message)
        self.scope = scope
        self.violations = list(violations or [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "scope_escalation_refused",
            "scope": self.scope,
            "message": str(self),
            "violations": list(self.violations),
        }


class ScopeResolutionError(ScopeError):
    """Scope composition could not produce a determinate result."""


@dataclass(frozen=True, slots=True)
class ScopeMember:
    """One subject a scope binds to."""

    kind: SubjectKind
    name: str

    def __post_init__(self) -> None:
        if not str(self.name or "").strip():
            raise ScopeEscalationRefused("a scope member must have a non-empty name")

    @property
    def key(self) -> str:
        return f"{self.kind.value}:{self.name.strip().lower()}"

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "name": self.name}


def member(kind: str | SubjectKind, name: str) -> ScopeMember:
    """Build a member from a free-text kind, refusing anything unknown."""

    try:
        resolved = SubjectKind(str(kind).strip().lower())
    except ValueError as exc:
        raise ScopeEscalationRefused(
            f"unknown scope subject kind {kind!r}; expected one of "
            f"{[item.value for item in SubjectKind]}",
            violations=[f"unknown_subject_kind:{kind}"],
        ) from exc
    return ScopeMember(kind=resolved, name=str(name).strip())


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BaselinePolicy:
    """The floor every subject starts from, and the widest envelope any scope
    may see.

    This is deliberately *not* the authority ceiling.  The baseline is a policy
    statement about what subjects may do; the ceiling is a server-owned bound on
    the maximum authority any profile may hold.  They are intersected, and
    neither may widen the other.
    """

    max_capability_rank: int
    allowed_capabilities: frozenset[str]
    denied_capabilities: frozenset[str] = frozenset()
    allowed_channels: frozenset[str] | None = None
    allowed_roles: frozenset[str] | None = None
    #: Whether EVERY authority-bearing action for this subject needs a recorded
    #: human review before it may proceed.  Default ``False`` at the baseline
    #: because the baseline already has the authority ceiling behind it, and a
    #: per-grant review token that no approver can produce would refuse every
    #: grant rather than protect any.  A SCOPE may raise it to ``True``; no
    #: scope and no baseline may lower it once raised.
    require_human_review: bool = False
    allow_verified_evidence: bool = True
    max_live_agents: int = 12
    schema_version: int = 1
    source: str = "builtin-default"

    def __post_init__(self) -> None:
        unknown = sorted(set(self.allowed_capabilities) - _capability_names())
        if unknown:
            raise ScopeEscalationRefused(
                f"baseline allows unknown capabilities {unknown}",
                violations=[f"unknown_capability:{name}" for name in unknown],
            )
        overlap = sorted(set(self.allowed_capabilities) & set(self.denied_capabilities))
        if overlap:
            raise ScopeEscalationRefused(
                f"baseline both allows and denies {overlap}; a capability cannot be in both sets",
                violations=[f"contradictory_baseline:{name}" for name in overlap],
            )
        if self.max_live_agents < 1:
            raise ScopeEscalationRefused("baseline max_live_agents must be >= 1")

    def permits_capability(self, capability: str) -> tuple[bool, str]:
        """Is *capability* inside the baseline? Returns ``(ok, rule_id)``."""

        name = str(capability or "").strip().lower()
        if name in self.denied_capabilities:
            return False, ScopeRule.BASELINE_DENIES.value
        if name not in self.allowed_capabilities:
            return False, ScopeRule.BASELINE_DENIES.value
        return True, ScopeRule.BASELINE_ALLOWS.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "max_capability_rank": self.max_capability_rank,
            "allowed_capabilities": sorted(self.allowed_capabilities),
            "denied_capabilities": sorted(self.denied_capabilities),
            "allowed_channels": None if self.allowed_channels is None else sorted(self.allowed_channels),
            "allowed_roles": None if self.allowed_roles is None else sorted(self.allowed_roles),
            "require_human_review": self.require_human_review,
            "allow_verified_evidence": self.allow_verified_evidence,
            "max_live_agents": self.max_live_agents,
            "source": self.source,
        }


def _capability_ranks() -> dict[str, int]:
    """Read the totally-ordered capability lattice from the ceiling module.

    Imported lazily inside the function: :mod:`alpha.safety.authority` is a
    package the static auditor also uses, and a module-level import of
    ``alpha.bots`` here would drag the whole bot registry (and its own import
    cycle through ``alpha.bots.permissions``) into every census import.
    """

    from alpha.bots.authority_ceiling import CAPABILITY_RANKS

    return dict(CAPABILITY_RANKS)


def _capability_names() -> frozenset[str]:
    return frozenset(_capability_ranks())


def baseline_from_ceiling(ceiling: Any = None, **overrides: Any) -> BaselinePolicy:
    """Derive a baseline from a live :class:`AuthorityCeiling`.

    Reuse, not redefinition: the capability set and the maximum rank come
    straight off the server-owned ceiling instance, so a scope can never be
    reasoned about against a lattice that disagrees with the one the ceiling
    enforces.
    """

    if ceiling is None:
        from alpha.bots.authority_ceiling import get_ceiling

        ceiling = get_ceiling()
    payload: dict[str, Any] = {
        "max_capability_rank": ceiling.max_capability_rank,
        "allowed_capabilities": frozenset(ceiling.allowed_capabilities or ()),
        "source": f"ceiling:{ceiling.source}",
    }
    payload.update(overrides)
    return BaselinePolicy(**payload)


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScopePolicy:
    """A named overlay that may only ever be STRICTER than the baseline.

    Frozen with ``slots``: there is no attribute-assignment path, so a scope
    object in memory cannot be widened after the monotonicity check has run.
    """

    name: str
    members: tuple[ScopeMember, ...]
    denied_capabilities: frozenset[str] = frozenset()
    denied_channels: frozenset[str] = frozenset()
    denied_roles: frozenset[str] = frozenset()
    max_capability_rank: int | None = None
    require_human_review: bool = True
    allow_verified_evidence: bool = True
    deny_protected_components: bool = True
    taint_bounds_authority: bool = True
    fail_closed: bool = True
    description: str = ""
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not str(self.name or "").strip():
            raise ScopeEscalationRefused("a scope must have a non-empty name", violations=["empty_scope_name"])
        if not self.members:
            raise ScopeEscalationRefused(
                f"scope {self.name!r} binds no subject; a scope that matches nothing is "
                "an operator belief, not a control",
                scope=self.name,
                violations=["empty_scope_members"],
            )
        unknown = sorted(set(self.denied_capabilities) - _capability_names())
        if unknown:
            raise ScopeEscalationRefused(
                f"scope {self.name!r} names unknown capabilities {unknown}; a deny of an "
                "unknown name is not a control",
                scope=self.name,
                violations=[f"unknown_capability:{name}" for name in unknown],
            )
        if not self.deny_protected_components:
            raise ScopeEscalationRefused(
                f"scope {self.name!r} may not set deny_protected_components=false: a scope "
                "must not be able to reach the components that enforce it",
                scope=self.name,
                violations=["scope_cannot_disable_protected_components"],
            )
        if not self.fail_closed:
            raise ScopeEscalationRefused(
                f"scope {self.name!r} may not set fail_closed=false",
                scope=self.name,
                violations=["scope_cannot_fail_open"],
            )

    # -- subject matching ---------------------------------------------------

    def matches(self, *, agent: str | None = None, channel: str | None = None, role: str | None = None) -> tuple[bool, str]:
        """Does this scope apply to the described subject? Returns ``(ok, member_key)``."""

        offered = {
            SubjectKind.AGENT: agent,
            SubjectKind.CHANNEL: channel,
            SubjectKind.ROLE: role,
        }
        for item in self.members:
            candidate = offered.get(item.kind)
            if candidate is not None and str(candidate).strip().lower() == item.name.strip().lower():
                return True, item.key
        return False, ""

    def protects(self) -> tuple[str, ...]:
        """Names of the components this scope may never reach around."""

        from alpha.bots.authority_ceiling import is_protected_component

        return tuple(sorted(item.name for item in self.members if is_protected_component(item.name)))

    def assert_cannot_reach_enforcer(self) -> None:
        """REFUSE a scope whose subjects or name name the enforcement machinery.

        This is the test-visible half of "the ceiling stays outside every
        scope's reach".  Without it, a scope named after the ceiling would be
        inert-but-legible in configuration while still being a scope the
        operator believes is doing something.
        """

        from alpha.bots.authority_ceiling import is_protected_component

        hits: list[str] = []
        for candidate in (self.name, *(item.name for item in self.members)):
            if is_protected_component(candidate):
                hits.append(str(candidate))
        if hits:
            raise ScopeEscalationRefused(
                f"scope {self.name!r} names enforcement machinery {sorted(set(hits))}: a scope "
                "may not bind the component that enforces it",
                scope=self.name,
                violations=[f"scope_reaches_enforcer:{hit}" for hit in sorted(set(hits))],
            )

    # -- construction from untrusted input ----------------------------------

    @classmethod
    def from_dict(cls, data: Any, *, baseline: BaselinePolicy) -> ScopePolicy:
        """Build a scope from configuration, refusing anything not STRICTER.

        This is the load-time boundary.  It is deliberately hostile: unknown
        keys, enablement keys, and out-of-order strictness are all hard
        refusals, because the failure mode being defended against is an operator
        (or a model-authored proposal) believing a scope widened something.
        """

        if not isinstance(data, dict):
            raise ScopeEscalationRefused(
                f"scope must be a JSON object, got {type(data).__name__}",
                violations=["scope_not_an_object"],
            )
        known = {
            "name",
            "members",
            "denied_capabilities",
            "denied_channels",
            "denied_roles",
            "max_capability_rank",
            "require_human_review",
            "allow_verified_evidence",
            "deny_protected_components",
            "taint_bounds_authority",
            "fail_closed",
            "description",
            "schema_version",
        }
        raw = {str(key): value for key, value in data.items()}
        violations: list[str] = []

        for key in sorted(set(raw) & _ENABLE_ATTEMPT_KEYS):
            violations.append(f"enable_attempt:{key}={raw[key]!r}")
        for key in sorted(set(raw) - known - _ENABLE_ATTEMPT_KEYS):
            violations.append(f"unknown_field:{key}")

        for key in sorted(_STRICTER_ONLY_TRUE & set(raw)):
            if raw[key] is not True:
                violations.append(f"not_stricter:{key}={raw[key]!r}")
        for key in sorted(_STRICTER_ONLY_FALSE & set(raw)):
            if raw[key] is not False:
                violations.append(f"not_stricter:{key}={raw[key]!r}")

        raw_members = raw.get("members")
        members: list[ScopeMember] = []
        if isinstance(raw_members, list):
            for entry in raw_members:
                if isinstance(entry, dict) and "kind" in entry and "name" in entry:
                    try:
                        members.append(member(str(entry["kind"]), str(entry["name"])))
                    except ScopeEscalationRefused as exc:
                        violations.extend(exc.violations or [f"bad_member:{entry!r}"])
                elif isinstance(entry, str) and ":" in entry:
                    kind_text, _, name = entry.partition(":")
                    try:
                        members.append(member(kind_text, name))
                    except ScopeEscalationRefused as exc:
                        violations.extend(exc.violations or [f"bad_member:{entry!r}"])
                else:
                    violations.append(f"bad_member:{entry!r}")
        else:
            violations.append("members_must_be_a_list")

        denied_capabilities = _as_lower_frozenset(raw.get("denied_capabilities"))
        denied_channels = _as_lower_frozenset(raw.get("denied_channels"))
        denied_roles = _as_lower_frozenset(raw.get("denied_roles"))

        # "A scope may never ENABLE what the baseline denies."  A deny list that
        # intersects the baseline's deny set is a no-op and almost always means
        # the author believed it was granting; it is refused as a contradiction
        # rather than silently accepted.
        for name in sorted(denied_capabilities & set(baseline.denied_capabilities)):
            violations.append(f"redundant_deny_of_baseline_denied_capability:{name}")

        rank = raw.get("max_capability_rank")
        if rank is not None:
            if isinstance(rank, bool) or not isinstance(rank, int):
                violations.append(f"max_capability_rank_not_an_integer:{rank!r}")
            elif rank > baseline.max_capability_rank:
                violations.append(
                    f"max_capability_rank {rank} is ABOVE the baseline {baseline.max_capability_rank}"
                )

        if violations:
            raise ScopeEscalationRefused(
                f"scope {raw.get('name', '<unnamed>')!r} refused: {len(violations)} "
                "scope(s) may only be stricter than the baseline",
                scope=str(raw.get("name", "")),
                violations=violations,
            )

        scope = cls(
            name=str(raw["name"]).strip(),
            members=tuple(members),
            denied_capabilities=denied_capabilities,
            denied_channels=denied_channels,
            denied_roles=denied_roles,
            max_capability_rank=rank,
            require_human_review=bool(raw.get("require_human_review", True)),
            allow_verified_evidence=bool(raw.get("allow_verified_evidence", False)),
            deny_protected_components=bool(raw.get("deny_protected_components", True)),
            taint_bounds_authority=bool(raw.get("taint_bounds_authority", True)),
            fail_closed=bool(raw.get("fail_closed", True)),
            description=str(raw.get("description", "")),
            schema_version=int(raw.get("schema_version", 1) or 1),
        )
        scope.assert_cannot_reach_enforcer()
        return scope

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "members": [item.to_dict() for item in self.members],
            "denied_capabilities": sorted(self.denied_capabilities),
            "denied_channels": sorted(self.denied_channels),
            "denied_roles": sorted(self.denied_roles),
            "max_capability_rank": self.max_capability_rank,
            "require_human_review": self.require_human_review,
            "allow_verified_evidence": self.allow_verified_evidence,
            "deny_protected_components": self.deny_protected_components,
            "taint_bounds_authority": self.taint_bounds_authority,
            "fail_closed": self.fail_closed,
            "description": self.description,
        }


def _as_lower_frozenset(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = [str(item) for item in value]
    else:
        raise ScopeEscalationRefused(
            f"expected a list of strings, got {type(value).__name__}",
            violations=[f"not_a_list:{type(value).__name__}"],
        )
    return frozenset(item.strip().lower() for item in items if str(item).strip())


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScopeDecision:
    """One recorded narrowing, with the scope that caused it and the reason."""

    rule_id: str
    scope: str
    dimension: str
    before: str
    after: str
    reason: str
    #: The capabilities this decision removed. Attribution walks THIS rather than
    #: parsing the human-readable before/after strings, so "why may this agent
    #: not do X" cannot silently name the wrong scope when two overlap.
    affected: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "scope": self.scope,
            "dimension": self.dimension,
            "before": self.before,
            "after": self.after,
            "reason": self.reason,
            "affected": list(self.affected),
        }


@dataclass(frozen=True, slots=True)
class EffectivePolicy:
    """The composed policy a subject is actually held to."""

    allowed_capabilities: frozenset[str]
    denied_capabilities: frozenset[str]
    max_capability_rank: int
    denied_channels: frozenset[str]
    denied_roles: frozenset[str]
    require_human_review: bool
    allow_verified_evidence: bool
    taint_bounds_authority: bool
    applied_scopes: tuple[str, ...]
    decisions: tuple[ScopeDecision, ...] = field(default_factory=tuple)
    #: The baseline's own denials, kept SEPARATE from the scopes' denials. Merging
    #: them would make "the scope denied it" indistinguishable from "the
    #: baseline never allowed it", and those are different answers to "why".
    baseline_denied_capabilities: frozenset[str] = frozenset()
    #: The baseline's allowed set, kept so a narrowing can be attributed to the
    #: scope that did the narrowing rather than to the baseline.
    baseline_allowed_capabilities: frozenset[str] = frozenset()

    def permits_capability(self, capability: str) -> tuple[bool, str]:
        name = str(capability or "").strip().lower()
        # Baseline first: a capability the baseline never allowed is not a
        # scope decision, and reporting it as one would send an operator
        # looking at the wrong configuration file.
        if name in self.baseline_denied_capabilities:
            return False, ScopeRule.BASELINE_DENIES.value
        if name in self.denied_capabilities:
            return False, ScopeRule.SCOPE_DENIES_CAPABILITY.value
        if name not in self.allowed_capabilities:
            # A capability the baseline DID allow that is no longer allowed was
            # removed by a scope's rank cap or by the ceiling. Naming the
            # baseline here would be wrong even though the baseline permitted it.
            if name in self.baseline_allowed_capabilities:
                return False, ScopeRule.SCOPE_DENIES_CAPABILITY.value
            return False, ScopeRule.BASELINE_DENIES.value
        return True, ScopeRule.BASELINE_ALLOWS.value

    def permits_channel(self, channel: str) -> tuple[bool, str]:
        if str(channel or "").strip().lower() in self.denied_channels:
            return False, ScopeRule.SCOPE_NARROWS_CHANNELS.value
        return True, ScopeRule.BASELINE_ALLOWS.value

    def permits_role(self, role: str) -> tuple[bool, str]:
        if str(role or "").strip().lower() in self.denied_roles:
            return False, ScopeRule.SCOPE_NARROWS_ROLES.value
        return True, ScopeRule.BASELINE_ALLOWS.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_capabilities": sorted(self.allowed_capabilities),
            "denied_capabilities": sorted(self.denied_capabilities),
            "baseline_denied_capabilities": sorted(self.baseline_denied_capabilities),
            "baseline_allowed_capabilities": sorted(self.baseline_allowed_capabilities),
            "max_capability_rank": self.max_capability_rank,
            "denied_channels": sorted(self.denied_channels),
            "denied_roles": sorted(self.denied_roles),
            "require_human_review": self.require_human_review,
            "allow_verified_evidence": self.allow_verified_evidence,
            "taint_bounds_authority": self.taint_bounds_authority,
            "applied_scopes": list(self.applied_scopes),
            "decisions": [item.to_dict() for item in self.decisions],
        }


@dataclass(frozen=True, slots=True)
class ScopeResolution:
    """The recorded outcome of composing scopes against a subject.

    ``decisions`` is the record.  An operator asking "why may this agent not do
    X" reads this object, and every entry in it names a scope and a rule id.
    """

    subject: dict[str, str]
    baseline: BaselinePolicy
    effective: EffectivePolicy
    resolution_id: str
    composition_rule: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution_id": self.resolution_id,
            "composition_rule": self.composition_rule,
            "subject": dict(self.subject),
            "baseline": self.baseline.to_dict(),
            "effective": self.effective.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)


@dataclass(frozen=True, slots=True)
class DenialExplanation:
    """A direct answer to "why may this subject not do X"."""

    allowed: bool
    subject: str
    capability: str
    scope: str
    rule_id: str
    reason: str
    competing_scopes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "subject": self.subject,
            "capability": self.capability,
            "scope": self.scope,
            "rule_id": self.rule_id,
            "reason": self.reason,
            "competing_scopes": list(self.competing_scopes),
        }

    def explain(self) -> str:
        verdict = "ALLOWED" if self.allowed else "REFUSED"
        return (
            f"{verdict}: subject {self.subject!r} {self.capability!r} -- "
            f"scope={self.scope!r} rule={self.rule_id!r} :: {self.reason}"
        )


#: How overlapping scopes compose. Stated once, here, so an ambiguous
#: precedence is impossible by construction rather than by convention.
COMPOSITION_RULE: Final = "strictest-union: baseline AND every matching scope; the effective policy is the intersection of all of them"


def matching_scopes(
    scopes: tuple[ScopePolicy, ...] | list[ScopePolicy],
    *,
    agent: str | None = None,
    channel: str | None = None,
    role: str | None = None,
    agent_labels: Iterable[str] = (),
) -> tuple[tuple[ScopePolicy, ...], dict[str, str]]:
    """Return the scopes that apply to a subject, plus the member key matched.

    ``agent_labels`` carries additional names for the SAME subject -- the agent
    id, a profile name, the descriptive subject string a call site already
    built.  A scope matching any of them applies, so a caller does not have to
    know which of its labels a scope was written against.

    Sorted by scope name so composition is deterministic: two runs with the
    same inputs produce byte-identical resolutions.
    """

    names = [name for name in (str(agent or ""), *(str(item) for item in agent_labels)) if name.strip()]
    selected: list[ScopePolicy] = []
    keys: dict[str, str] = {}
    for scope in sorted(scopes, key=lambda item: item.name):
        matched_key = ""
        for member_item in scope.members:
            if member_item.kind is not SubjectKind.AGENT:
                continue
            if any(member_item.name.strip().lower() == name.strip().lower() for name in names):
                matched_key = member_item.key
                break
        if not matched_key:
            ok, matched_key = scope.matches(agent=agent, channel=channel, role=role)
        else:
            ok = True
        if ok:
            selected.append(scope)
            keys[scope.name] = matched_key
    return tuple(selected), keys


def compose_scopes(
    scopes: tuple[ScopePolicy, ...] | list[ScopePolicy],
    *,
    baseline: BaselinePolicy,
    ceiling: Any = None,
    agent: str | None = None,
    channel: str | None = None,
    role: str | None = None,
    agent_labels: Iterable[str] = (),
) -> ScopeResolution:
    """Compose the baseline with every matching scope into one effective policy.

    The result is the **strictest union**: for a dimension that is a set, the
    intersection; for a numeric bound, the minimum; for a "stricter" flag, the
    logical AND / OR in the denying direction.  Nothing here can widen the
    baseline, and the authority ceiling is intersected LAST so that no ordering
    accident in scope composition can produce a grant above it.
    """

    if ceiling is None:
        from alpha.bots.authority_ceiling import get_ceiling

        ceiling = get_ceiling()

    applicable, matched_keys = matching_scopes(
        scopes, agent=agent, channel=channel, role=role, agent_labels=agent_labels
    )
    decisions: list[ScopeDecision] = []

    allowed = set(baseline.allowed_capabilities)
    scope_denied: set[str] = set()
    max_rank = baseline.max_capability_rank
    denied_channels: set[str] = set()
    denied_roles: set[str] = set()
    require_review = baseline.require_human_review
    allow_verified = baseline.allow_verified_evidence
    taint_bounds = True

    if not applicable:
        decisions.append(
            ScopeDecision(
                rule_id=ScopeRule.NO_SCOPE_APPLIES.value,
                scope="<none>",
                dimension="scope_match",
                before="baseline",
                after="baseline",
                reason="no scope binds this subject; the baseline applies unchanged",
            )
        )

    for scope in applicable:
        scope.assert_cannot_reach_enforcer()
        key = matched_keys.get(scope.name, "")

        if scope.denied_capabilities:
            # ``before`` is the BASELINE-allowed set, not the running one, so the
            # record stays attributable when two scopes overlap: if an earlier
            # scope already removed the capability, the decision still says which
            # scope named it and that another had already won.
            before = sorted(baseline.allowed_capabilities)
            named = sorted(set(scope.denied_capabilities) & set(baseline.allowed_capabilities))
            already_gone = sorted(set(scope.denied_capabilities) & set(baseline.denied_capabilities))
            removed_now = sorted(set(scope.denied_capabilities) & set(allowed))
            allowed -= set(scope.denied_capabilities)
            scope_denied |= set(named)
            if named:
                decisive = (
                    "this scope's denial removed it"
                    if removed_now
                    else (
                        "a stricter scope had already removed it; this scope names it too, and the "
                        "union of denials is what applies"
                    )
                )
                decisions.append(
                    ScopeDecision(
                        rule_id=ScopeRule.SCOPE_DENIES_CAPABILITY.value,
                        scope=scope.name,
                        dimension="capabilities",
                        before=",".join(before),
                        after=",".join(sorted(allowed)),
                        reason=(
                            f"scope {scope.name!r} matched {key!r} and denies {named}, which the "
                            f"baseline allows; a scope may deny but never enable, so the denial "
                            f"is the whole of this scope's effect here -- {decisive}"
                            + (f"; also names {already_gone}, which the baseline already denied" if already_gone else "")
                        ),
                        affected=tuple(named),
                    )
                )

        if scope.max_capability_rank is not None and scope.max_capability_rank < max_rank:
            before = max_rank
            max_rank = scope.max_capability_rank
            dropped = sorted(name for name in allowed if _rank_of(name) > max_rank)
            allowed = {name for name in allowed if _rank_of(name) <= max_rank}
            decisions.append(
                ScopeDecision(
                    rule_id=ScopeRule.SCOPE_TIGHTENS_RANK.value,
                    scope=scope.name,
                    dimension="max_capability_rank",
                    before=str(before),
                    after=str(max_rank),
                    reason=(
                        f"scope {scope.name!r} matched {key!r} and caps the maximum rank at "
                        f"{max_rank}, below the baseline {before}; the strictest bound wins, and "
                        f"it removed {dropped}"
                    ),
                    affected=tuple(dropped),
                )
            )

        if scope.denied_channels:
            before = sorted(denied_channels)
            denied_channels |= set(scope.denied_channels)
            decisions.append(
                ScopeDecision(
                    rule_id=ScopeRule.SCOPE_NARROWS_CHANNELS.value,
                    scope=scope.name,
                    dimension="channels",
                    before=",".join(before),
                    after=",".join(sorted(denied_channels)),
                    reason=(
                        f"scope {scope.name!r} matched {key!r} and denies channels "
                        f"{sorted(scope.denied_channels)}"
                    ),
                )
            )

        if scope.denied_roles:
            before = sorted(denied_roles)
            denied_roles |= set(scope.denied_roles)
            decisions.append(
                ScopeDecision(
                    rule_id=ScopeRule.SCOPE_NARROWS_ROLES.value,
                    scope=scope.name,
                    dimension="roles",
                    before=",".join(before),
                    after=",".join(sorted(denied_roles)),
                    reason=(
                        f"scope {scope.name!r} matched {key!r} and denies roles "
                        f"{sorted(scope.denied_roles)}"
                    ),
                )
            )

        if scope.require_human_review and not require_review:
            require_review = True
            decisions.append(
                ScopeDecision(
                    rule_id=ScopeRule.SCOPE_SAWES_RAISED_REVIEW.value,
                    scope=scope.name,
                    dimension="require_human_review",
                    before="False",
                    after="True",
                    reason=(
                        f"scope {scope.name!r} matched {key!r} and requires human review; a "
                        "stricter scope raises the bar regardless of order"
                    ),
                )
            )

        if not scope.allow_verified_evidence and allow_verified:
            allow_verified = False
            decisions.append(
                ScopeDecision(
                    rule_id=ScopeRule.SCOPE_SAWES_FORBIDDEN_EVIDENCE.value,
                    scope=scope.name,
                    dimension="allow_verified_evidence",
                    before="True",
                    after="False",
                    reason=(
                        f"scope {scope.name!r} matched {key!r} and forbids recording verified "
                        "evidence; no other scope can re-enable it"
                    ),
                )
            )

        if not scope.taint_bounds_authority:
            # Refused at construction, so unreachable. Asserted rather than
            # trusted: a future edit must not make this a silent no-op.
            raise ScopeResolutionError(
                f"scope {scope.name!r} was constructed with taint_bounds_authority=False, which "
                "ScopePolicy construction refuses; refusing to compose it"
            )

    # The ceiling is intersected LAST, unconditionally, and is not a scope field.
    ceiling_allowed = frozenset(ceiling.allowed_capabilities or ())
    ceiling_rank = int(ceiling.max_capability_rank)
    after_ceiling_allowed = {name for name in allowed if name in ceiling_allowed}
    after_ceiling_allowed = {
        name for name in after_ceiling_allowed if _rank_of(name) <= ceiling_rank
    }
    if after_ceiling_allowed != allowed or ceiling_rank < max_rank:
        decisions.append(
            ScopeDecision(
                rule_id=ScopeRule.CEILING_CLAMPS.value,
                scope="<authority-ceiling>",
                dimension="capabilities",
                before=",".join(sorted(allowed)),
                after=",".join(sorted(after_ceiling_allowed)),
                reason=(
                    "the server-owned authority ceiling is intersected after scope composition "
                    f"and is not expressible as a scope field (max rank {ceiling_rank}, "
                    f"source {ceiling.source!r}); no scope, and no ordering of scopes, can raise it"
                ),
            )
        )
    allowed = after_ceiling_allowed
    max_rank = min(max_rank, ceiling_rank)

    effective = EffectivePolicy(
        allowed_capabilities=frozenset(allowed),
        denied_capabilities=frozenset(scope_denied),
        max_capability_rank=max_rank,
        denied_channels=frozenset(denied_channels),
        denied_roles=frozenset(denied_roles),
        require_human_review=require_review,
        allow_verified_evidence=allow_verified,
        taint_bounds_authority=taint_bounds,
        applied_scopes=tuple(scope.name for scope in applicable),
        decisions=tuple(decisions),
        baseline_denied_capabilities=frozenset(baseline.denied_capabilities),
        baseline_allowed_capabilities=frozenset(baseline.allowed_capabilities),
    )

    subject = {
        "agent": str(agent or ""),
        "channel": str(channel or ""),
        "role": str(role or ""),
    }
    return ScopeResolution(
        subject=subject,
        baseline=baseline,
        effective=effective,
        resolution_id=resolution_id(subject, effective),
        composition_rule=COMPOSITION_RULE,
    )


def resolution_id(subject: dict[str, str], effective: EffectivePolicy) -> str:
    """Deterministic id over the inputs, so the same subject yields the same id."""

    payload = json.dumps(
        {"subject": subject, "effective": effective.to_dict()},
        ensure_ascii=True,
        sort_keys=True,
    )
    import hashlib

    return "scope-resolution:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _rank_of(capability: str) -> int:
    ranks = _capability_ranks()
    return ranks.get(str(capability or "").strip().lower(), 0)


# ---------------------------------------------------------------------------
# The reportable question
# ---------------------------------------------------------------------------


def explain_denial(
    resolution: ScopeResolution,
    capability: str,
    *,
    agent: str | None = None,
    channel: str | None = None,
    role: str | None = None,
) -> DenialExplanation:
    """Answer "why may this subject not do X", naming the scope and the rule.

    For a refusal the ``scope`` field names the specific scope whose denial
    decided the outcome, and ``competing_scopes`` lists the other scopes that
    also applied, so an operator can see the union rather than a single
    arbitrary winner.
    """

    subject_label = str(agent or channel or role or "subject")
    effective = resolution.effective
    allowed, rule_id = effective.permits_capability(capability)

    if allowed:
        return DenialExplanation(
            allowed=True,
            subject=subject_label,
            capability=capability,
            scope=",".join(effective.applied_scopes) or "<baseline>",
            rule_id=rule_id,
            reason=(
                "the capability is inside the effective policy; applied scopes "
                f"[{', '.join(effective.applied_scopes) or 'none'}] did not deny it"
            ),
            competing_scopes=effective.applied_scopes,
        )

    # Attribute the refusal to the scope that actually caused it. A decision is
    # the cause when the capability was present BEFORE it and absent AFTER it;
    # testing only the "after" string would miss every denial, because a denial
    # is defined by what it removed.
    deciding_scope = "<baseline>"
    reason = (
        f"the baseline does not allow {capability!r}, so no scope could grant it; "
        "a scope may only deny"
    )
    if capability.strip().lower() in effective.baseline_denied_capabilities:
        reason = (
            f"the baseline explicitly denies {capability!r}; a scope may only deny further, so "
            "no scope can restore it"
        )
    else:
        # Two passes, deliberately. A scope that NAMES a capability is the more
        # useful answer than a scope that removed it as a side effect of a rank
        # cap: "deny-exec says process_exec" beats "cap-rank removed everything
        # above rank 20" when both are true. The broader one is still reported in
        # ``competing_scopes``, so nothing is hidden.
        for candidate_rules in (
            (ScopeRule.SCOPE_DENIES_CAPABILITY.value,),
            (ScopeRule.SCOPE_TIGHTENS_RANK.value,),
        ):
            for decision in effective.decisions:
                if decision.rule_id not in candidate_rules:
                    continue
                if capability.strip().lower() in decision.affected:
                    deciding_scope = decision.scope
                    reason = decision.reason
                    break
            else:
                continue
            break
        else:
            for decision in effective.decisions:
                if decision.rule_id == ScopeRule.CEILING_CLAMPS.value:
                    deciding_scope = decision.scope
                    reason = decision.reason
                    break

    others = tuple(name for name in effective.applied_scopes if name != deciding_scope)
    return DenialExplanation(
        allowed=False,
        subject=subject_label,
        capability=capability,
        scope=deciding_scope,
        rule_id=rule_id,
        reason=reason,
        competing_scopes=others,
    )


def assert_not_looser(scope: ScopePolicy, baseline: BaselinePolicy) -> None:
    """Re-verify monotonicity for an already-constructed scope.

    ``ScopePolicy.from_dict`` checks on load; this checks for a scope built
    directly in code, which is the path a proposal, a test, or a future caller
    would take.
    """

    violations: list[str] = []
    if scope.max_capability_rank is not None and scope.max_capability_rank > baseline.max_capability_rank:
        violations.append(
            f"scope {scope.name!r} max_capability_rank {scope.max_capability_rank} is above the "
            f"baseline {baseline.max_capability_rank}"
        )
    if not scope.require_human_review and baseline.require_human_review:
        violations.append(f"scope {scope.name!r} turns off human review the baseline requires")
    if scope.allow_verified_evidence and not baseline.allow_verified_evidence:
        violations.append(
            f"scope {scope.name!r} re-enables verified evidence the baseline forbids"
        )
    if not scope.deny_protected_components:
        violations.append(f"scope {scope.name!r} disables the protected-component refusal")
    if not scope.fail_closed:
        violations.append(f"scope {scope.name!r} fails open")
    if scope.protects():
        violations.append(f"scope {scope.name!r} binds a protected component: {list(scope.protects())}")
    if violations:
        raise ScopeEscalationRefused(
            f"scope {scope.name!r} is not strictly tighter than the baseline: {violations}",
            scope=scope.name,
            violations=violations,
        )


def with_baseline(scope: ScopePolicy, baseline: BaselinePolicy) -> ScopePolicy:
    """Return *scope* unchanged, having asserted it is not looser than *baseline*.

    Provided so the call site reads as an explicit narrowing step rather than a
    bare assertion, and so the returned object is the same frozen instance.
    """

    assert_not_looser(scope, baseline)
    return replace(scope)  # a distinct frozen instance; no field is widened


def describe_boundary_honesty() -> str:
    """One sentence naming what this module is and is not. Used by the report."""

    return (
        "alpha.safety.authority.scopes is an in-process POLICY control that narrows authority "
        "cooperating code in this process already holds. It is NOT a security boundary: the "
        "agent loop, channel connections, credential handling and shell run under one OS user in "
        "one trust envelope (https://docs.openclaw.ai/start/why-openclaw). The only component a "
        "scope provably cannot reach is the authority ceiling and the other protected components, "
        "because those are refused at scope load AND at composition."
    )


__all__ = [
    "COMPOSITION_RULE",
    "BaselinePolicy",
    "DenialExplanation",
    "EffectivePolicy",
    "ScopeDecision",
    "ScopeEscalationRefused",
    "ScopeError",
    "ScopeMember",
    "ScopePolicy",
    "ScopeResolution",
    "ScopeResolutionError",
    "ScopeRule",
    "SubjectKind",
    "assert_not_looser",
    "baseline_from_ceiling",
    "compose_scopes",
    "describe_boundary_honesty",
    "explain_denial",
    "matching_scopes",
    "member",
    "resolution_id",
    "with_baseline",
]
