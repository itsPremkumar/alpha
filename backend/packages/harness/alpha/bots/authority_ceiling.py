"""THE AUTHORITY CEILING — the single reviewable place that bounds self-extension.

Why this file exists
--------------------
`alpha` can hire, re-scope and retire bot profiles at runtime. That is *self
extension*. Without a hard ceiling it is *self escalation*: one injected
instruction in one fetched web page could, in two hops, bootstrap an agent whose
capabilities exceed anything the operator ever granted. A precedent for exactly
that class of bug already exists in this repository — a prior audit found a
repository-level trust update that could GRANT A CREDENTIAL. Any
self-modification path is therefore treated as an escalation surface.

The boundary this file draws
----------------------------
    alpha may HIRE, RE-SCOPE and RETIRE.
    alpha may NOT promote itself, widen its own authority envelope, or mint a
    profile whose capabilities exceed this ceiling.

The ceiling is SERVER-OWNED. It is:

* a frozen dataclass (:class:`AuthorityCeiling`) with **no mutator** — there is
  deliberately no ``set_ceiling``/``update_ceiling`` in this module;
* loaded from server configuration (a JSON file under the runtime home or the
  project ``config/`` directory, or an env-var path override), never from model
  output, never from a profile, never from a proposal;
* intersected into *every* capability decision, so removing this check from one
  call site does not widen anything, because the other call sites recompute.

Every other dynamic-profile entry point (:mod:`alpha.bots.dynamic_profiles`,
:mod:`alpha.bots.lifecycle_governor`, :mod:`alpha.bots.self_modification`) routes
through :func:`enforce_grant` or :meth:`AuthorityCeiling.assert_may_modify`.
Those are the two chokepoints, and both are covered by tests that attempt the
forbidden action.

What the ceiling protects
-------------------------
* :data:`PROTECTED_COMPONENTS` — the enforcement machinery itself, the approval
  gate, the safety enclave and the updater policy. No automated path may modify
  these, at any rank, for any reason.
* :data:`MAX_CAPABILITY_RANK` — the highest authority tier any created profile
  may hold. Because ranks are totally ordered, a ceiling *tightening* is a
  rank decrease, and every pre-existing profile is re-validated against it on
  next use (see :func:`alpha.bots.dynamic_profiles.revalidate_profile`).
* :data:`MAX_LIVE_PROFILES` — a bound on population, because an unbounded agent
  population is a resource and cost bomb.

What this file IS, and is not
-----------------------------
This file is a **policy control inside one trust envelope**.  It is not a
security boundary and must never be described as one.  The agent loop, the
channel connections, the credential handling and the shell all run under one
OS user in one process; wrapping that in a container isolates it from the host
but does not separate those components from each other, which is the exact
limitation OpenClaw documents at
https://docs.openclaw.ai/start/why-openclaw.  Everything below narrows
authority that cooperating code in this process already holds; none of it
isolates anything, and code inside the envelope can reach around it.

Two things in this repository are genuinely narrower than that, and they are
narrow because a *credential* sits in front of them, not because a check runs
in-process: the Gateway's session-JWT / PAT / internal-token ingress, and the
GitHub webhook's HMAC signature.  The scope overlays, the role rings, the
approval gate and taint are all policy controls over the same envelope.

Separating the agent loop from the credentials it uses needs a separate OS
identity, a separate process holding its own scoped credential, or a sandbox
whose filesystem and network are not the host's.  That is a boundary and it is
not implemented here; see the report's "cannot be enforced in-process" list.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The capability lattice.
#
# Ranks are TOTALLY ORDERED. A profile's authority is the maximum rank of the
# capabilities it holds, so "no more than the ceiling" is a single comparison
# and a ceiling tightening is a rank decrease that demotes everything above it.
# ---------------------------------------------------------------------------

RANK_OBSERVE: Final = 10
RANK_REASON: Final = 20
RANK_DISPATCH: Final = 30
RANK_WORKSPACE_WRITE: Final = 40
RANK_PROCESS_EXEC: Final = 50
RANK_REPOSITORY_MUTATE: Final = 60
RANK_GRANT_AUTHORITY: Final = 70

#: Human labels, so an operator reading a denial knows which tier was hit.
CAPABILITY_RANKS: Final[dict[str, int]] = {
    "observe": RANK_OBSERVE,
    "reason": RANK_REASON,
    "dispatch": RANK_DISPATCH,
    "workspace_write": RANK_WORKSPACE_WRITE,
    "process_exec": RANK_PROCESS_EXEC,
    "repository_mutate": RANK_REPOSITORY_MUTATE,
    "grant_authority": RANK_GRANT_AUTHORITY,
}

#: The named, inspectable capability set. A profile's grant is ALWAYS an
#: intersection with this set, so an unknown or invented capability name can
#: never be granted by accident — it simply cannot appear in a grant.
ALLOWED_CAPABILITIES: Final[frozenset[str]] = frozenset(CAPABILITY_RANKS)

#: The default ceiling. Deliberately excludes ``repository_mutate`` and
#: ``grant_authority``: a profile alpha creates at runtime may write inside the
#: workspace and run bounded processes, but it may not rewrite the repository or
#: mint authority. Those two tiers belong to the operator and the Sentinel only.
DEFAULT_MAX_CAPABILITY_RANK: Final = RANK_PROCESS_EXEC

#: Bound on live (non-retired) dynamic profiles. Population is a cost bomb
#: before it is a capability problem.
DEFAULT_MAX_LIVE_PROFILES: Final = 12

#: Bound on total profiles ever created, including retired ones, so a
#: create/retire loop cannot grow the store without bound.
DEFAULT_MAX_TOTAL_PROFILES: Final = 200

# ---------------------------------------------------------------------------
# Components that are PERMANENTLY off-limits to every automated path.
#
# This list is the blast-radius floor for self-modification. It names the
# enforcement machinery itself: if an automated path could edit these, the
# ceiling would be advisory rather than enforced. Entries are matched as
# substrings of a normalised target path, so both "alpha/bots/authority_ceiling.py"
# and "alpha.bots.authority_ceiling" match.
# ---------------------------------------------------------------------------

PROTECTED_COMPONENTS: Final[tuple[str, ...]] = (
    # The ceiling and its enforcement chokepoints.
    "alpha/bots/authority_ceiling",
    "alpha.bots.authority_ceiling",
    "authority_ceiling.py",
    # Dynamic profile registry + lifecycle governor: the code that applies the
    # ceiling to profiles. Disabling it would disable the ceiling.
    "alpha/bots/dynamic_profiles",
    "alpha.bots.dynamic_profiles",
    "dynamic_profiles.py",
    "alpha/bots/lifecycle_governor",
    "alpha.bots.lifecycle_governor",
    "lifecycle_governor.py",
    "alpha/bots/permissions",
    "alpha.bots.permissions",
    "permissions.py",
    # The approval gate.
    "alpha/projects/approval_queue",
    "alpha.projects.approval_queue",
    "approval_queue.py",
    # The safety enclave.
    "alpha/safety",
    "alpha.safety",
    # The updater's own policy — self-modification may not loosen it.
    "config/update-policy.json",
    "update_policy.py",
    "alpha/evolution/update_policy",
    "alpha.evolution.update_policy",
    # The ledger of record, so history cannot be edited after the fact.
    "alpha/bots/events",
    "alpha.bots.events",
    "governance_ledger",
    # The fleet kill switch.
    "alpha/bots/kill_switch",
    "alpha.bots.kill_switch",
    "kill_switch.py",
)


class AuthorityViolation(RuntimeError):
    """Raised when an action would exceed the server-owned authority ceiling.

    Deliberately a ``RuntimeError`` and not a ``ValueError``: this is a security
    refusal, not a caller mistake, and callers must not be able to swallow it as
    ordinary bad input.
    """

    def __init__(self, message: str, *, violations: list[str] | None = None) -> None:
        super().__init__(message)
        self.violations = list(violations or [])

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": "authority_ceiling_violation",
            "message": str(self),
            "violations": list(self.violations),
        }


def _normalise(target: str) -> str:
    """Lowercase, backslash-normalised form used for protected-target matching."""
    return str(target or "").strip().lower().replace("\\", "/")


#: Bare module stems of the protected components, matched as whole path/dotted
#: SEGMENTS. Substring matching alone would miss a bare ``authority_ceiling``
#: reference (the readable entries above are all longer forms), and would
#: false-positive on unrelated names. Segment matching is precise in both
#: directions.
PROTECTED_MODULE_STEMS: Final[tuple[str, ...]] = (
    "authority_ceiling",
    "dynamic_profiles",
    "lifecycle_governor",
    "self_modification",
    "governance_ledger",
    "update_policy",
    "update-policy",
    "approval_queue",
    "approval-queue",
    "kill_switch",
    "kill-switch",
    "permissions",
    "events",
    "net_policy",
    "safety",
)


def _segments(target: str) -> set[str]:
    """Split a path or dotted reference into comparable segments."""
    cleaned = _normalise(target)
    parts: set[str] = set()
    for chunk in cleaned.replace("\\", "/").replace(":", "/").split("/"):
        if not chunk:
            continue
        parts.add(chunk)
        if "." in chunk:
            parts.update(p for p in chunk.split(".") if p)
    return parts


def is_protected_component(target: str) -> bool:
    """True when *target* names a component no automated path may modify.

    Matched two ways so neither can be evaded: the readable full-path forms in
    :data:`PROTECTED_COMPONENTS` as substrings, and the bare module stems in
    :data:`PROTECTED_MODULE_STEMS` as whole segments. A target that resolves to a
    protected module by ANY of those routes is protected.
    """
    if not target:
        return False
    needle = _normalise(target)
    if any(_normalise(component) in needle for component in PROTECTED_COMPONENTS):
        return True
    segments = _segments(needle)
    return any(stem in segments for stem in PROTECTED_MODULE_STEMS)


def capability_rank(capability: str) -> int | None:
    """Rank of *capability*, or ``None`` when it is not a known capability.

    An unknown name is ``None`` rather than 0 or a large default: granting
    nothing to a name the server does not recognise is the only safe answer, and
    returning a number would let a caller accidentally treat "unknown" as
    "allowed".
    """
    return CAPABILITY_RANKS.get(_normalise(capability))


def highest_rank(capabilities: object) -> int:
    """The maximum rank across *capabilities*; 0 for an empty/invalid set."""
    if not isinstance(capabilities, (set, frozenset, list, tuple)):
        return 0
    ranks = [r for r in (capability_rank(str(c)) for c in capabilities) if r is not None]
    return max(ranks) if ranks else 0


def normalise_capabilities(capabilities: object) -> frozenset[str]:
    """Canonical lowercase set, dropping anything unrecognised."""
    if not isinstance(capabilities, (set, frozenset, list, tuple)):
        return frozenset()
    out: set[str] = set()
    for raw in capabilities:
        name = _normalise(raw)
        if name in CAPABILITY_RANKS:
            out.add(name)
    return frozenset(out)


@dataclass(frozen=True, slots=True)
class AuthorityCeiling:
    """The immutable, server-owned bound on every created profile.

    Frozen with ``slots``: there is no attribute assignment path, so nothing in
    this process — model, tool, agent or test — can widen a live ceiling
    instance. Tightening is done by editing server configuration and reloading,
    which is auditable; widening requires the same.
    """

    max_capability_rank: int = DEFAULT_MAX_CAPABILITY_RANK
    allowed_capabilities: frozenset[str] | None = None
    max_live_profiles: int = DEFAULT_MAX_LIVE_PROFILES
    max_total_profiles: int = DEFAULT_MAX_TOTAL_PROFILES
    schema_version: int = 1
    source: str = "builtin-default"

    # -- construction ------------------------------------------------------
    def __post_init__(self) -> None:
        if self.max_capability_rank not in set(CAPABILITY_RANKS.values()):
            raise ValueError(
                f"max_capability_rank must be one of {sorted(set(CAPABILITY_RANKS.values()))}, "
                f"got {self.max_capability_rank}"
            )
        if self.allowed_capabilities is None:
            # DERIVE the allowed set from the rank rather than defaulting to the
            # full lattice. Defaulting to "everything" and then relying on the
            # rank check would be two sources of truth that can disagree, and the
            # disagreement is exactly the shape of a silent escalation.
            object.__setattr__(
                self,
                "allowed_capabilities",
                frozenset(
                    name
                    for name in ALLOWED_CAPABILITIES
                    if CAPABILITY_RANKS[name] <= self.max_capability_rank
                ),
            )
        if self.max_live_profiles < 1:
            raise ValueError("max_live_profiles must be >= 1")
        if self.max_total_profiles < self.max_live_profiles:
            raise ValueError("max_total_profiles must be >= max_live_profiles")
        unknown = sorted(set(self.allowed_capabilities) - ALLOWED_CAPABILITIES)
        if unknown:
            raise ValueError(f"allowed_capabilities contains unknown names: {unknown}")
        # A capability allowed but ranked above the ceiling is a contradiction
        # that would make "allowed" and "within rank" disagree. Refuse the file.
        too_high = sorted(
            c for c in self.allowed_capabilities
            if CAPABILITY_RANKS[c] > self.max_capability_rank
        )
        if too_high:
            raise ValueError(
                f"allowed_capabilities {too_high} rank above max_capability_rank "
                f"{self.max_capability_rank}"
            )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, source: str) -> AuthorityCeiling:
        """Build from server configuration. Unknown fields are REFUSED.

        Refusing unknown fields matters: a typo in a policy file must not be
        silently ignored, because the operator would believe a limit is in force
        when it is not.
        """
        if not isinstance(data, Mapping):
            raise ValueError(f"authority ceiling {source} must be a JSON object")
        known = {
            "schema_version",
            "max_capability_rank",
            "allowed_capabilities",
            "max_live_profiles",
            "max_total_profiles",
            "description",
        }
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown authority ceiling field(s) in {source}: {unknown}")
        raw_caps = data.get("allowed_capabilities")
        if raw_caps is not None:
            if not isinstance(raw_caps, list) or not all(isinstance(c, str) for c in raw_caps):
                raise ValueError(f"allowed_capabilities in {source} must be a list of strings")
        rank = data.get("max_capability_rank", DEFAULT_MAX_CAPABILITY_RANK)
        if isinstance(rank, bool) or not isinstance(rank, int):
            raise ValueError(f"max_capability_rank in {source} must be an integer")
        live = data.get("max_live_profiles", DEFAULT_MAX_LIVE_PROFILES)
        if isinstance(live, bool) or not isinstance(live, int):
            raise ValueError(f"max_live_profiles in {source} must be an integer")
        total = data.get("max_total_profiles", DEFAULT_MAX_TOTAL_PROFILES)
        if isinstance(total, bool) or not isinstance(total, int):
            raise ValueError(f"max_total_profiles in {source} must be an integer")
        return cls(
            max_capability_rank=rank,
            allowed_capabilities=(
                frozenset(normalise_capabilities(raw_caps))
                if raw_caps is not None
                else None
            ),
            max_live_profiles=live,
            max_total_profiles=total,
            schema_version=int(data.get("schema_version", 1) or 1),
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "max_capability_rank": self.max_capability_rank,
            "allowed_capabilities": sorted(self.allowed_capabilities),
            "max_live_profiles": self.max_live_profiles,
            "max_total_profiles": self.max_total_profiles,
            "source": self.source,
        }

    # -- the two enforcement chokepoints ------------------------------------
    def within_ceiling(self, capabilities: object) -> tuple[bool, list[str]]:
        """Is *capabilities* inside this ceiling? Returns ``(ok, violations)``."""
        violations: list[str] = []
        for raw in capabilities if isinstance(capabilities, (set, frozenset, list, tuple)) else []:
            name = _normalise(raw)
            if name not in CAPABILITY_RANKS:
                violations.append(f"unknown capability {name!r}")
                continue
            if name not in self.allowed_capabilities:
                violations.append(f"capability {name!r} is not in the authority ceiling")
            if CAPABILITY_RANKS[name] > self.max_capability_rank:
                violations.append(
                    f"capability {name!r} ranks {CAPABILITY_RANKS[name]} above "
                    f"max_capability_rank {self.max_capability_rank}"
                )
        return (not violations), violations

    def assert_within_ceiling(self, capabilities: object, *, subject: str = "profile") -> frozenset[str]:
        """Return the normalised grant, or REFUSE. Never silently narrows."""
        ok, violations = self.within_ceiling(capabilities)
        if not ok:
            raise AuthorityViolation(
                f"{subject} refused: capability request exceeds the server-owned authority ceiling",
                violations=violations,
            )
        return normalise_capabilities(capabilities)

    def assert_may_modify(self, target: str, *, actor: str, reason: str = "") -> None:
        """REFUSE any attempt to modify a protected component.

        Called by the self-modification flow and the lifecycle governor, so
        "alpha cannot retire, disable or re-scope the component that enforces the
        ceiling" is enforced in code at both chokepoints, not by convention.

        This is the *scope-facing* half of the same invariant: a scope is also
        refused if it is written against a protected component, so a policy
        overlay cannot be the thing that names the machinery enforcing it.
        """

        if is_protected_component(target):
            raise AuthorityViolation(
                f"actor {actor!r} may not modify protected component {target!r}: "
                f"it enforces the authority ceiling. reason={reason!r}",
                violations=[f"protected_component:{_normalise(target)}"],
            )
        for scope in get_scopes():
            if is_protected_component(scope.name) or scope.protects():
                raise AuthorityViolation(
                    f"actor {actor!r} may not modify {target!r}: registered scope "
                    f"{scope.name!r} binds the enforcement machinery "
                    f"{list(scope.protects()) or [scope.name]}, and a scope may never reach the "
                    "component that enforces it",
                    violations=[
                        f"scope_reaches_enforcer:{item}"
                        for item in (scope.protects() or (scope.name,))
                    ],
                )

    def assert_population_within_ceiling(
        self, *, live: int, total: int, subject: str = "profile"
    ) -> None:
        """REFUSE creation that would exceed the live or total population bound."""
        if live >= self.max_live_profiles:
            raise AuthorityViolation(
                f"{subject} refused: live profile population {live} is at the ceiling "
                f"of {self.max_live_profiles}. Retire a profile before creating another.",
                violations=[f"max_live_profiles:{self.max_live_profiles}"],
            )
        if total >= self.max_total_profiles:
            raise AuthorityViolation(
                f"{subject} refused: total profile population {total} is at the ceiling "
                f"of {self.max_total_profiles}.",
                violations=[f"max_total_profiles:{self.max_total_profiles}"],
            )


# ---------------------------------------------------------------------------
# Server configuration loading. Read-only: there is no writer in this module.
# ---------------------------------------------------------------------------

_CEILING_RELPATH: Final = ("config", "authority-ceiling.json")

#: Highest rank a model-authored or profile-authored field may ever influence.
#: Model input is untrusted; server configuration is not.
_UNTRUSTED_MAX_RANK: Final = RANK_PROCESS_EXEC


def ceiling_path(explicit: str | Path | None = None) -> Path:
    """Resolve the ceiling file: explicit arg, env override, then project config."""
    if explicit is not None:
        return Path(explicit).expanduser()
    override = os.getenv("ALPHA_AUTHORITY_CEILING_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from alpha.config.runtime_paths import project_root

        return project_root().joinpath(*_CEILING_RELPATH)
    except Exception:
        return Path.cwd().joinpath(*_CEILING_RELPATH)


def load_ceiling(path: str | Path | None = None) -> AuthorityCeiling:
    """Load the ceiling from server configuration.

    An ABSENT file yields the safe built-in default. A PRESENT but malformed
    file raises: an operator who wrote a limit and got a typo must be told, not
    silently handed the default.
    """
    resolved = ceiling_path(path)
    try:
        raw = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        return AuthorityCeiling()
    except OSError as exc:
        raise ValueError(f"could not read authority ceiling {resolved}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"authority ceiling {resolved} is not valid JSON: {exc}") from exc
    return AuthorityCeiling.from_dict(data, source=str(resolved))


_cached_ceiling: AuthorityCeiling | None = None


def get_ceiling(*, refresh: bool = False) -> AuthorityCeiling:
    """Process-wide ceiling. ``refresh=True`` re-reads server config.

    Re-reading is how an operator TIGHTENS the ceiling: the file changes, the
    process reloads, and every profile is re-validated against the new bound.
    Nothing in this module can make the bound *looser* than the file says.
    """
    global _cached_ceiling
    if _cached_ceiling is None or refresh:
        _cached_ceiling = load_ceiling()
    return _cached_ceiling


# ---------------------------------------------------------------------------
# Scoped policy overlays.
#
# The ceiling above is ONE uniform bound.  A research bot and a computer-use
# bot do not belong in the same envelope, and a ceiling that applies uniformly
# is either too tight for the constrained agent or too loose for the powerful
# one.  The scopes below bind named agents, channels and roles to a policy that
# may only be STRICTER than the baseline.
#
# They live here, beside the ceiling, because the ceiling is what a scope is
# finally intersected with: composition ORDER is the security property, and the
# only reliable way to guarantee it is to own both in one place.  The scope
# machinery itself lives in :mod:`alpha.safety.authority.scopes`, which is pure
# and independently testable; what is here is the ownership -- the registry,
# the loader, and the intersection performed at the grant chokepoint.
#
# This is a POLICY control, not a boundary.  It narrows authority that
# cooperating code in this process already holds; it isolates nothing.
# ---------------------------------------------------------------------------

_SCOPES_RELPATH: Final = ("config", "authority-scopes.json")

_scope_lock = threading.RLock()
_registered_scopes: tuple[Any, ...] = ()


def scope_path(explicit: str | Path | None = None) -> Path:
    """Resolve the scope file: explicit arg, env override, then project config."""

    if explicit is not None:
        return Path(explicit).expanduser()
    override = os.getenv("ALPHA_AUTHORITY_SCOPES_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    try:
        from alpha.config.runtime_paths import project_root

        return project_root().joinpath(*_SCOPES_RELPATH)
    except Exception:
        return Path.cwd().joinpath(*_SCOPES_RELPATH)


def baseline_policy(ceiling: AuthorityCeiling | None = None) -> Any:
    """The baseline that scopes may only narrow. Derived from the live ceiling.

    Reuse, not redefinition: the capability set and the maximum rank come off
    the server-owned ceiling, so there is one lattice rather than two that can
    disagree about what "authority" means.
    """

    from alpha.safety.authority.scopes import baseline_from_ceiling

    return baseline_from_ceiling(ceiling or get_ceiling())


def load_scopes(
    path: str | Path | None = None,
    *,
    ceiling: AuthorityCeiling | None = None,
) -> tuple[Any, ...]:
    """Load scoped overlays from server configuration.

    An ABSENT file yields no scopes.  A PRESENT but malformed file, or a scope
    that is not strictly tighter than the baseline, RAISES.  A typo in a policy
    file must not become a scope that silently does nothing, and a scope that
    tries to widen anything is refused at load rather than clamped at use where
    nobody is watching.
    """

    from alpha.safety.authority.scopes import ScopePolicy

    resolved = scope_path(path)
    try:
        raw = resolved.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise ValueError(f"could not read authority scopes {resolved}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"authority scopes {resolved} is not valid JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ValueError(f"authority scopes {resolved} must be a JSON object")

    unknown = sorted(set(data) - {"schema_version", "scopes", "description"})
    if unknown:
        raise ValueError(f"unknown authority scope field(s) in {resolved}: {unknown}")

    entries = data.get("scopes") or []
    if not isinstance(entries, list):
        raise ValueError(f"authority scopes {resolved} 'scopes' must be a list")

    baseline = baseline_policy(ceiling)
    return tuple(ScopePolicy.from_dict(entry, baseline=baseline) for entry in entries)


def get_scopes() -> tuple[Any, ...]:
    """The process-wide registered scopes. Empty until an operator loads some."""

    with _scope_lock:
        return _registered_scopes


def register_scopes(scopes: Iterable[Any]) -> tuple[Any, ...]:
    """Install scopes into the process, refusing any that is not STRICTER.

    The monotonicity check runs HERE, at the moment of registration, and again
    inside :func:`compose_scopes` at the moment of use.  Two checks in two
    places is deliberate: a scope object is immutable, but a future caller
    building one in code rather than loading it from a file would otherwise get
    exactly one chance to be wrong.
    """

    from alpha.safety.authority.scopes import assert_not_looser

    baseline = baseline_policy()
    installed = tuple(scopes)
    for scope in installed:
        assert_not_looser(scope, baseline)
        scope.assert_cannot_reach_enforcer()
    global _registered_scopes
    with _scope_lock:
        _registered_scopes = installed
    return installed


def set_scopes(scopes: Iterable[Any]) -> tuple[Any, ...]:
    """Register already-loaded scopes. The atomic form used by operators."""

    return register_scopes(scopes)


def clear_scopes() -> None:
    """Remove every registered scope. Operator/test escape hatch, not a default."""

    global _registered_scopes
    with _scope_lock:
        _registered_scopes = ()


def resolve_effective_policy(
    *,
    subject: str,
    ceiling: AuthorityCeiling | None = None,
    scopes: Iterable[Any] | None = None,
) -> Any:
    """Compose the baseline, every matching scope, and the ceiling.

    The ceiling is intersected LAST inside :func:`compose_scopes` and is not
    expressible as a scope field, so no scope and no ordering of scopes can
    raise it.  The returned :class:`ScopeResolution` carries every decision, so
    "why may this agent not do X" is one query rather than an argument.
    """

    from alpha.safety.authority.scopes import compose_scopes

    bound = ceiling or get_ceiling()
    return compose_scopes(
        tuple(scopes) if scopes is not None else get_scopes(),
        baseline=baseline_policy(bound),
        ceiling=bound,
        agent=subject,
        agent_labels=_scope_candidates(subject),
    )


def _scope_candidates(subject: str) -> tuple[str, ...]:
    """Every label the same subject might be named by in a scope.

    A call site already builds a descriptive subject such as
    ``"hire of 'researcher-3'"``.  Requiring a scope author to guess that exact
    string would make scopes unusable, so the quoted name inside it is offered
    as a candidate as well.
    """

    text = str(subject or "").strip()
    if not text:
        return ()
    labels = [text]
    for single, double in re.findall(r"'([^']*)'|\"([^\"]*)\"", text):
        value = single or double
        if value.strip():
            labels.append(value.strip())
    return tuple(dict.fromkeys(labels))


def explain_effective_policy(
    *,
    subject: str,
    capability: str,
    ceiling: AuthorityCeiling | None = None,
    scopes: Iterable[Any] | None = None,
) -> Any:
    """Answer "why may this subject not do X", naming the scope and the rule."""

    from alpha.safety.authority.scopes import explain_denial

    resolution = resolve_effective_policy(subject=subject, ceiling=ceiling, scopes=scopes)
    return explain_denial(resolution, capability, agent=subject)


def _explain(capability: str, resolution: Any, subject: str) -> Any:
    from alpha.safety.authority.scopes import explain_denial

    return explain_denial(resolution, capability, agent=subject)


def _grant_rule_id(resolution: Any, scope_violations: list[str], taint_violations: list[str]) -> str:
    """Name the rule that decided this grant, for the decision receipt.

    A refusal with no nameable rule is a FINDING, and the receipt writer flags
    it.  The most specific rule wins so the receipt stays reviewable.
    """

    if taint_violations:
        return "TAINT-BOUNDS-AUTHORITY-001"
    if scope_violations:
        return "SCOPE-DENY-001"
    if resolution is not None and resolution.effective.applied_scopes:
        return "SCOPE-MATCH-000"
    return "CEILING-WITHIN-RANK-001"


def _current_taint_turn() -> Any:
    """The turn in scope, or ``None`` outside a turn (CLI, health probe)."""

    try:
        from alpha.safety.authority.taint import current_turn

        return current_turn()
    except Exception:  # pragma: no cover - taint module is always importable
        return None


def _receipt_chain() -> Any:
    try:
        from alpha.safety.authority.receipts import get_receipt_chain

        return get_receipt_chain("authority")
    except Exception:  # pragma: no cover
        return None


def _grant_actor(subject: str) -> Any:
    """The execution identity for a grant decision.

    The grant chokepoint is reached from the registry's self-extension paths,
    whose actor is the leader acting on the server's behalf.  A model-authored
    subject is recorded as such and is never authoritative for its own
    privileges.
    """

    from alpha.safety.authority.receipts import (
        ActorKind,
        automated_system,
        model_asserted_identity,
    )

    turn = _current_taint_turn()
    if turn is not None and getattr(turn, "model_asserted", False):  # pragma: no cover
        return model_asserted_identity(ActorKind.AGENT.value, subject)
    return automated_system(str(subject))


def _record_grant_refusal(
    *,
    subject: str,
    requested: frozenset[str],
    violations: list[str],
    rule_id: str,
    resolution: Any,
    taint_sources: tuple[str, ...] = (),
    rejected: tuple[Any, ...] | None = None,
) -> None:
    """Write the decision receipt for a REFUSED grant. Never raises."""

    try:
        from alpha.safety.authority.receipts import RejectedAlternative

        alternatives = rejected or tuple(
            RejectedAlternative(option=f"grant {sorted(requested)}", reason=violation)
            for violation in violations[:8]
        )
        _receipt_chain().append(
            decision="authority_grant",
            identity=_grant_actor(subject),
            outcome="refused",
            policy_rule=rule_id,
            scope=",".join(resolution.effective.applied_scopes) if resolution is not None else "",
            inputs=[f"requested={sorted(requested)}", f"violation_count={len(violations)}"],
            rejected_alternatives=alternatives,
            work_ref=f"grant:{subject}",
            effective_policy=resolution.effective.to_dict() if resolution is not None else {},
            taint_sources=taint_sources,
        )
    except Exception:  # a ledger failure must not become an allow
        logger.exception("could not record authority grant refusal receipt for %s", subject)


def _record_grant_approval(
    *,
    subject: str,
    granted: frozenset[str],
    rule_id: str,
    resolution: Any,
) -> None:
    """Write the decision receipt for a GRANTED authority grant. Never raises."""

    try:
        from alpha.safety.authority.receipts import RejectedAlternative, automated_system

        alternatives: tuple[RejectedAlternative, ...] = ()
        if resolution is not None:
            denied = sorted(
                set(resolution.baseline.allowed_capabilities)
                - set(resolution.effective.allowed_capabilities)
            )
            if denied:
                alternatives = (
                    RejectedAlternative(
                        option=f"also grant {denied}",
                        reason=(
                            f"the effective policy for {subject!r} does not include {denied}; a "
                            "scope may deny but never enable, so this was never an option"
                        ),
                    ),
                )
        _receipt_chain().append(
            decision="authority_grant",
            identity=automated_system(str(subject)),
            outcome="granted",
            policy_rule=rule_id,
            scope=",".join(resolution.effective.applied_scopes) if resolution is not None else "",
            inputs=[f"requested={sorted(granted)}"],
            rejected_alternatives=alternatives,
            work_ref=f"grant:{subject}",
            effective_policy=resolution.effective.to_dict() if resolution is not None else {},
        )
    except Exception:
        logger.exception("could not record authority grant receipt for %s", subject)


def _resolve_grant_approval(*, subject: str, requested: frozenset[str]) -> Any:
    """Ask the approval gate, failing closed.

    No approver is wired into this process yet, which is exactly the case that
    must REFUSE: an absent gate is not an approving gate.  Wiring one properly
    means a human decision reaching the agent loop out of band; see the report's
    "cannot be enforced in-process" list.
    """

    from alpha.safety.authority.boundaries import resolve_authority_approval

    return resolve_authority_approval(
        None,
        subject=f"{subject} grant {sorted(requested)}",
        approver_name="human_operator",
    )


def enforce_grant(
    requested: object,
    *,
    creator_grant: object,
    subject: str = "profile",
    ceiling: AuthorityCeiling | None = None,
) -> frozenset[str]:
    """THE hard invariant: a child never holds more than its creator or the ceiling.

    Five independent checks, all of which must pass:

    1. every requested capability is a *known* name and inside the ceiling;
    2. every requested capability is held by the creator;
    3. the resulting grant is non-empty;
    4. no registered SCOPE denies a requested capability. An overlay may only
       be stricter, so this is a narrowing check and never an enabling one;
    5. the turn this grant is made on is not TAINTED, and any human review the
       effective policy demands has actually been given. Both fail closed.

    Failure is a REFUSAL, not a narrowing. Silently trimming an over-privileged
    request would let a caller believe it created the agent it asked for, and
    would hide the attempt from the operator.

    Checks 4 and 5 are intersected HERE, at the chokepoint every
    self-extension path already goes through, rather than at one call site, so
    removing them from one caller does not widen anything: every caller
    recomputes.  Each refusal and each grant writes a decision receipt naming
    the actor, the policy rule, the effective scope, the inputs, and the
    rejected alternatives -- an event log cannot answer those after the fact.
    """
    bound = ceiling or get_ceiling()
    violations: list[str] = []

    ok, ceiling_violations = bound.within_ceiling(requested)
    if not ok:
        violations.extend(ceiling_violations)

    creator = normalise_capabilities(creator_grant)
    requested_norm = normalise_capabilities(requested)
    escalated = sorted(requested_norm - creator)
    if escalated:
        violations.append(
            f"capabilities {escalated} are not held by the creator; "
            f"a created profile may never exceed its creator"
        )

    if isinstance(requested, (set, frozenset, list, tuple)):
        unrecognised = sorted(
            _normalise(c) for c in requested if _normalise(c) not in CAPABILITY_RANKS
        )
        if unrecognised:
            violations.append(f"unrecognised capabilities: {unrecognised}")

    if not requested_norm and not violations:
        violations.append("requested capability set is empty or entirely unrecognised")

    # -- check 4: scoped overlays may only narrow ---------------------------
    # The scopes machinery is pure and lives in
    # :mod:`alpha.safety.authority.scopes`; what lives here is the ownership of
    # the registry and the decision to intersect at this chokepoint.  The
    # ceiling is applied LAST inside compose_scopes, so no scope and no
    # ordering of scopes can raise it.
    resolution = None
    scope_violations: list[str] = []
    try:
        resolution = resolve_effective_policy(subject=subject, ceiling=bound)
    except Exception as exc:  # a scope that cannot be resolved refuses, never allows
        scope_violations.append(f"scope resolution failed closed: {type(exc).__name__}: {exc}")
    if resolution is not None:
        for capability in sorted(requested_norm):
            permitted, _rule = resolution.effective.permits_capability(capability)
            if permitted:
                continue
            explanation = _explain(capability, resolution, subject)
            scope_violations.append(
                f"capability {capability!r} is not in the effective policy "
                f"(scope={explanation.scope!r} rule={explanation.rule_id!r}): {explanation.reason}"
            )
    violations.extend(scope_violations)

    # -- check 5a: taint bounds authority -----------------------------------
    taint_sources: tuple[str, ...] = ()
    taint_violations: list[str] = []
    turn = _current_taint_turn()
    if turn is not None and turn.tainted:
        taint_sources = turn.sources()
        taint_violations.append(
            f"turn {turn.turn_id!r} is tainted by {list(taint_sources)}; taint bounds authority, "
            "so an authority grant cannot be taken on a tainted turn"
        )
        if resolution is None or resolution.effective.taint_bounds_authority:
            taint_violations.append(
                "a tainted turn requires human review of the effective policy before any authority "
                "action, and no such review is recorded on this turn"
            )
    violations.extend(taint_violations)

    rule_id = _grant_rule_id(resolution, scope_violations, taint_violations)

    if violations:
        _record_grant_refusal(
            subject=subject,
            requested=requested_norm,
            violations=violations,
            rule_id=rule_id,
            resolution=resolution,
            taint_sources=taint_sources,
        )
        raise AuthorityViolation(
            f"{subject} refused: {len(violations)} authority violation(s)", violations=violations
        )

    if highest_rank(requested_norm) > _UNTRUSTED_MAX_RANK:
        # Defence in depth: even if a ceiling were somehow constructed too high,
        # a request assembled from untrusted input cannot exceed this rank.
        _record_grant_refusal(
            subject=subject,
            requested=requested_norm,
            violations=[f"untrusted_rank_limit:{_UNTRUSTED_MAX_RANK}"],
            rule_id="CEILING-UNTRUSTED-RANK-001",
            resolution=resolution,
            taint_sources=taint_sources,
        )
        raise AuthorityViolation(
            f"{subject} refused: requested rank {highest_rank(requested_norm)} exceeds the "
            f"hard untrusted-input rank limit {_UNTRUSTED_MAX_RANK}",
            violations=[f"untrusted_rank_limit:{_UNTRUSTED_MAX_RANK}"],
        )

    # -- check 5b: a policy that demands review and has none is a refusal --
    if resolution is not None and resolution.effective.require_human_review:
        from alpha.safety.authority.receipts import RejectedAlternative

        result = _resolve_grant_approval(subject=subject, requested=requested_norm)
        if not result.approved:
            _record_grant_refusal(
                subject=subject,
                requested=requested_norm,
                violations=[f"approval:{result.reason}"],
                rule_id="BOUNDARY-APPROVAL-FAIL-CLOSED-001",
                resolution=resolution,
                taint_sources=taint_sources,
                rejected=(
                    RejectedAlternative(
                        option=f"grant {sorted(requested_norm)} without approval",
                        reason=result.reason,
                    ),
                ),
            )
            raise AuthorityViolation(
                f"{subject} refused: the effective policy requires human approval for this grant "
                f"and the gate did not approve ({result.reason})",
                violations=[f"approval:{result.reason}"],
            )

    _record_grant_approval(
        subject=subject,
        granted=requested_norm,
        rule_id=rule_id,
        resolution=resolution,
    )
    return requested_norm


def narrow_to_ceiling(
    granted: object,
    *,
    subject: str = "profile",
    ceiling: AuthorityCeiling | None = None,
) -> tuple[frozenset[str], list[str]]:
    """Intersect an EXISTING grant with the ceiling, reporting what was removed.

    This is the *demotion* path, used when a ceiling is tightened after a
    profile already exists. It is intentionally separate from
    :func:`enforce_grant`: at creation an over-privileged request is refused,
    but for an already-created agent the safe action is to narrow it and say so,
    not to make the agent unusable by refusing to load it.
    """
    bound = ceiling or get_ceiling()
    kept: set[str] = set()
    removed: list[str] = []
    for raw in granted if isinstance(granted, (set, frozenset, list, tuple)) else []:
        name = _normalise(raw)
        if name in CAPABILITY_RANKS and name in bound.allowed_capabilities and CAPABILITY_RANKS[name] <= bound.max_capability_rank:
            kept.add(name)
        else:
            removed.append(name or str(raw))
    return frozenset(kept), removed


__all__ = [
    "ALLOWED_CAPABILITIES",
    "AuthorityCeiling",
    "AuthorityViolation",
    "CAPABILITY_RANKS",
    "DEFAULT_MAX_CAPABILITY_RANK",
    "DEFAULT_MAX_LIVE_PROFILES",
    "DEFAULT_MAX_TOTAL_PROFILES",
    "PROTECTED_COMPONENTS",
    "PROTECTED_MODULE_STEMS",
    "RANK_DISPATCH",
    "RANK_GRANT_AUTHORITY",
    "RANK_OBSERVE",
    "RANK_PROCESS_EXEC",
    "RANK_REASON",
    "RANK_REPOSITORY_MUTATE",
    "RANK_WORKSPACE_WRITE",
    "baseline_policy",
    "capability_rank",
    "ceiling_path",
    "clear_scopes",
    "enforce_grant",
    "explain_effective_policy",
    "get_ceiling",
    "get_scopes",
    "highest_rank",
    "is_protected_component",
    "load_ceiling",
    "load_scopes",
    "narrow_to_ceiling",
    "normalise_capabilities",
    "register_scopes",
    "resolve_effective_policy",
    "scope_path",
    "set_scopes",
]
