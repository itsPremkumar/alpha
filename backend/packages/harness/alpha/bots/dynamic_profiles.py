"""Durable runtime registry for profiles `alpha` creates at runtime.

The gap this closes
-------------------
``alpha.planning.autonomous`` already DRAFTS specialist profiles
(:class:`~alpha.planning.autonomous.NewProfileSpec`, reached through the
``build_autonomous_plan`` tool) and ``alpha.bots.registry.BotRegistry`` already
persists bot profiles to disk. What did not exist was the install path between
them: a drafted spec could not become a working profile. The lead-agent prompt
even advertised ``install_new_profiles=true`` for a parameter that does not
exist in any code path. This module is that path — and it is governed.

Design commitments
------------------
**Durable, and loud when not.** The store is a single JSON document written
atomically (tmp + ``os.replace``). A store that is *unreadable* or that fails
schema validation raises :class:`ProfileStoreUnreadable`. It never falls back to
an empty roster: silently reverting to defaults would delete every profile alpha
had created, and silent loss of a created agent is worse than refusing to create
one. A *missing* file is different — that is a fresh install, not corruption.

**Creation is a proposal, not an authorisation.** Every profile begins life as a
:class:`ProfileProposal` in state ``pending``. It cannot dispatch work, and it is
not in the live roster, until it passes the approval gate. The approval gate is
pluggable and defaults to the existing
:class:`~alpha.projects.approval_queue.ApprovalQueue`, which is already the
human-in-the-loop mechanism for high blast-radius actions.

**A child never exceeds its creator.** Enforced in code by
:func:`alpha.bots.authority_ceiling.enforce_grant`, which intersects the request
with the creator's own grant and the server-owned ceiling, and REFUSES rather
than narrowing.

**Model-authored fields are untrusted input.** A proposal's self-declared
capabilities, description, system prompt and metadata are all attacker-controlled
text: they arrive from a model that has read web pages. The design consequence
is that *none of them participate in authorisation*. The effective grant is
computed server-side from the creator's grant and the ceiling; a profile's
declared capabilities are retained for audit and displayed, but the runtime
authorises against :attr:`RuntimeProfile.granted_capabilities` and nothing else.
Concretely, a proposal cannot smuggle authority through ``metadata``, through
``description``, through ``system_prompt``, or by declaring a capability in a
field the schema does not validate.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from alpha.bots.authority_ceiling import (
    AuthorityCeiling,
    AuthorityViolation,
    enforce_grant,
    get_ceiling,
    narrow_to_ceiling,
    normalise_capabilities,
)
from alpha.bots.governance_ledger import (
    ACTION_PROFILE_DEMOTED,
    ACTION_PROFILE_INSTALLED,
    ACTION_PROFILE_PROPOSED,
    ACTION_PROFILE_REFUSED,
    record_governance_action,
)
from alpha.bots.profile import BotProfile, generate_default_soul

logger = logging.getLogger(__name__)

#: Bump when the on-disk shape changes incompatibly. A store written by a newer
#: version is REFUSED, not guessed at.
PROFILE_STORE_SCHEMA_VERSION: Final = 1

#: Lifecycle states for a proposal. Mirrors the trust-tier vocabulary already in
#: use for skills (pending/active/stale/archived) so there is one lifecycle
#: language in the codebase rather than two.
PROPOSAL_PENDING: Final = "pending"
PROPOSAL_APPROVED: Final = "approved"
PROPOSAL_REJECTED: Final = "rejected"
PROPOSAL_INSTALLED: Final = "installed"
PROPOSAL_STATES: Final[frozenset[str]] = frozenset(
    {PROPOSAL_PENDING, PROPOSAL_APPROVED, PROPOSAL_REJECTED, PROPOSAL_INSTALLED}
)

#: A created profile starts life DISABLED even after approval, so installing one
#: never immediately puts new authority to work before a human has seen it.
PROFILE_DISABLED_STATUS: Final = "disabled"


class ProfileStoreUnreadable(RuntimeError):
    """The profile store exists but could not be read or validated.

    Raised instead of degrading to an empty roster. This is the whole point of
    the class: a loud failure an operator can act on, rather than a silent
    reversion to defaults that would look like "alpha forgot its agents".
    """


class ProfileValidationError(ValueError):
    """A profile document is malformed, unparseable or schema-invalid."""


class ProfileApprovalError(RuntimeError):
    """A profile was used or installed without passing the approval gate."""


_NAME_MAX = 64
_DESCRIPTION_MAX = 2000
_SYSTEM_PROMPT_MAX = 8000
_METADATA_KEYS_MAX = 32
_METADATA_KEY_MAX = 64


def _validate_name(name: Any) -> str:
    if not isinstance(name, str):
        raise ProfileValidationError("profile name must be a string")
    clean = name.strip().lower()
    if not clean:
        raise ProfileValidationError("profile name must be non-empty")
    if len(clean) > _NAME_MAX:
        raise ProfileValidationError(f"profile name must be <= {_NAME_MAX} characters")
    # Path-safe and unambiguous: no separators, no traversal, no option-like
    # names. The same sanitisation the memory-namespace helper does, applied at
    # the door rather than at the point of use.
    if not all(ch.isalnum() or ch in "._-" for ch in clean):
        raise ProfileValidationError(
            f"profile name {name!r} may only contain letters, digits, dot, underscore and dash"
        )
    if clean.startswith(".") or ".." in clean:
        raise ProfileValidationError(f"profile name {name!r} may not contain dot-runs")
    return clean


def _validate_text(value: Any, *, field_name: str, limit: int, required: bool) -> str:
    if value is None:
        if required:
            raise ProfileValidationError(f"{field_name} is required")
        return ""
    if not isinstance(value, str):
        raise ProfileValidationError(f"{field_name} must be a string")
    if len(value) > limit:
        raise ProfileValidationError(f"{field_name} must be <= {limit} characters")
    return value


def _validate_str_list(value: Any, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ProfileValidationError(f"{field_name} must be a list of strings")
    return [v.strip() for v in value if v.strip()]


def _validate_metadata(value: Any) -> dict[str, Any]:
    """Metadata is UNTRUSTED, so it is bounded and JSON-checked, never trusted.

    Bounding it matters: metadata is the natural place to smuggle a grant, and
    an unbounded dict from a model that has read a web page is also a
    denial-of-service and a log-volume problem.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProfileValidationError("metadata must be an object")
    if len(value) > _METADATA_KEYS_MAX:
        raise ProfileValidationError(f"metadata must have <= {_METADATA_KEYS_MAX} keys")
    out: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ProfileValidationError("metadata keys must be non-empty strings")
        if len(key) > _METADATA_KEY_MAX:
            raise ProfileValidationError(f"metadata key {key!r} is too long")
        if not isinstance(item, (str, int, float, bool, list, dict, type(None))):
            raise ProfileValidationError(
                f"metadata[{key!r}] has unsupported type {type(item).__name__}"
            )
        out[key.strip()] = item
    return out


def _now() -> str:
    from alpha.bots.profile import _now as profile_now

    return profile_now()


@dataclass
class RuntimeProfile:
    """An installed, ceiling-bounded profile created at runtime.

    ``granted_capabilities`` is the AUTHORITATIVE field: it is the only thing the
    runtime authorises against. ``declared_capabilities`` is what the request
    asked for, kept for audit and display. The two differ whenever a request was
    refused or narrowed, and that difference is the interesting part of the
    audit trail.
    """

    name: str
    display_name: str = ""
    role: str = ""
    system_prompt: str = ""
    description: str = ""
    granted_capabilities: frozenset[str] = frozenset()
    declared_capabilities: frozenset[str] = frozenset()
    demoted_from: frozenset[str] = frozenset()
    skills: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    model: str | None = None
    department: str = "engineering"
    status: str = PROFILE_DISABLED_STATUS
    schema_version: int = PROFILE_STORE_SCHEMA_VERSION
    profile_version: int = 1
    creator: str = "alpha"
    creator_grant: frozenset[str] = frozenset()
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    last_useful_work_at: str | None = None
    useful_work_count: int = 0
    total_task_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.name = _validate_name(self.name)
        self.granted_capabilities = normalise_capabilities(self.granted_capabilities)
        self.declared_capabilities = normalise_capabilities(self.declared_capabilities)
        self.demoted_from = normalise_capabilities(self.demoted_from)
        self.creator_grant = normalise_capabilities(self.creator_grant)
        if not self.role:
            self.role = "Autonomous Specialist Teammate"
        if not self.display_name:
            self.display_name = self.name.capitalize()

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["granted_capabilities"] = sorted(self.granted_capabilities)
        data["declared_capabilities"] = sorted(self.declared_capabilities)
        data["demoted_from"] = sorted(self.demoted_from)
        data["creator_grant"] = sorted(self.creator_grant)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RuntimeProfile:
        """Strictly reconstruct. Unknown keys are REFUSED, not dropped.

        Refusing unknown keys is deliberate. Silently dropping a field would
        mean a newer writer's extra field — possibly a grant — could be written
        and then ignored on load, so the operator would believe a capability was
        recorded when it was not. A malformed profile is rejected whole, never
        partially loaded.
        """
        if not isinstance(data, Mapping):
            raise ProfileValidationError("profile document must be an object")
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise ProfileValidationError(f"profile has unknown field(s): {unknown}")
        missing = [k for k in ("name",) if k not in data]
        if missing:
            raise ProfileValidationError(f"profile is missing required field(s): {missing}")
        payload = dict(data)
        for key in ("granted_capabilities", "declared_capabilities", "demoted_from", "creator_grant"):
            if key in payload and payload[key] is not None:
                if not isinstance(payload[key], (list, tuple, set, frozenset)):
                    raise ProfileValidationError(f"profile field {key!r} must be a list")
                payload[key] = [str(v) for v in payload[key]]
        return cls(**payload)

    def to_bot_profile(self) -> BotProfile:
        """Project onto the existing :class:`BotProfile` the runtime already uses.

        Adapting rather than replacing is the point: the rest of bot mode
        (inboxes, DMs, org charts, the task-claim path) keeps working unchanged
        for a runtime-created profile.
        """
        return BotProfile(
            name=self.name,
            display_name=self.display_name,
            role=self.role,
            soul=self.system_prompt or generate_default_soul(self.name, self.role),
            model=self.model,
            skills=list(self.skills),
            mcp_servers=list(self.mcp_servers),
            department=self.department,
            capabilities=sorted(self.granted_capabilities),
            status=self.status,
        )

    def effective_authority(self) -> dict[str, Any]:
        """What an operator should see: granted vs declared vs ceiling."""
        return {
            "name": self.name,
            "granted_capabilities": sorted(self.granted_capabilities),
            "declared_capabilities": sorted(self.declared_capabilities),
            "demoted_from": sorted(self.demoted_from),
            "status": self.status,
            "profile_version": self.profile_version,
            "creator": self.creator,
        }


@dataclass
class ProfileProposal:
    """A request to create a profile. NOT an authorisation.

    A proposal carries no authority at all: it has no ``granted_capabilities``
    until :func:`DynamicProfileStore.install_approved` runs, and that function
    recomputes the grant from the creator's grant and the ceiling rather than
    trusting anything on the proposal.
    """

    profile_name: str
    requested_capabilities: list[str] = field(default_factory=list)
    role: str = ""
    system_prompt: str = ""
    description: str = ""
    skills: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    model: str | None = None
    department: str = "engineering"
    creator: str = "alpha"
    creator_grant: list[str] = field(default_factory=list)
    state: str = PROPOSAL_PENDING
    rationale: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    resolved_at: str | None = None
    approval_request_id: str | None = None

    def __post_init__(self) -> None:
        self.profile_name = _validate_name(self.profile_name)
        self.state = _validate_state(self.state)
        self.requested_capabilities = _validate_str_list(
            self.requested_capabilities, field_name="requested_capabilities"
        )
        self.skills = _validate_str_list(self.skills, field_name="skills")
        self.mcp_servers = _validate_str_list(self.mcp_servers, field_name="mcp_servers")
        self.role = _validate_text(self.role, field_name="role", limit=512, required=False)
        self.system_prompt = _validate_text(
            self.system_prompt, field_name="system_prompt", limit=_SYSTEM_PROMPT_MAX, required=False
        )
        self.description = _validate_text(
            self.description, field_name="description", limit=_DESCRIPTION_MAX, required=False
        )
        self.rationale = _validate_text(
            self.rationale, field_name="rationale", limit=_DESCRIPTION_MAX, required=False
        )
        self.creator = (self.creator or "alpha").strip().lower() or "alpha"
        self.creator_grant = _validate_str_list(self.creator_grant, field_name="creator_grant")
        self.metadata = _validate_metadata(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ProfileProposal:
        if not isinstance(data, Mapping):
            raise ProfileValidationError("proposal document must be an object")
        known = set(cls.__dataclass_fields__)
        unknown = sorted(set(data) - known)
        if unknown:
            raise ProfileValidationError(f"proposal has unknown field(s): {unknown}")
        missing = [k for k in ("profile_name",) if k not in data]
        if missing:
            raise ProfileValidationError(f"proposal is missing required field(s): {missing}")
        return cls(**dict(data))


def _validate_state(state: Any) -> str:
    if not isinstance(state, str) or state.strip() not in PROPOSAL_STATES:
        raise ProfileValidationError(
            f"proposal state must be one of {sorted(PROPOSAL_STATES)}, got {state!r}"
        )
    return state.strip()


#: Signature of an approval gate: given a proposal, return True to approve.
ApprovalGate = Callable[[ProfileProposal], bool]


def default_approval_gate(proposal: ProfileProposal) -> bool:
    """Default gate: route through the EXISTING human approval queue.

    Uses :class:`~alpha.projects.approval_queue.ApprovalQueue` — the mechanism
    already in place for high blast-radius actions — rather than inventing a
    second approval surface. It only ever *asks*: it creates a pending
    ``ApprovalRequest`` and returns False, so a profile is never installed
    unattended on this path. A caller that genuinely wants unattended hiring
    passes its own gate explicitly, which is then visible in the code that
    called it.
    """
    try:
        from alpha.projects.approval_queue import get_approval_queue

        queue = get_approval_queue("bots")
        queue.request_approval(
            bot_name=proposal.creator,
            action_type="bot_profile_install",
            risk_level="medium",
            details={
                "profile_name": proposal.profile_name,
                "requested_capabilities": list(proposal.requested_capabilities),
                "rationale": proposal.rationale,
            },
        )
    except Exception:
        # A failure to raise an approval request must NOT be a pass. The
        # proposal stays pending and therefore uninstalled.
        logger.warning("Could not raise approval request for profile proposal", exc_info=True)
    return False


def _default_store_path() -> Path:
    try:
        from alpha.config.runtime_paths import runtime_home

        return runtime_home() / "bots" / "runtime_profiles.json"
    except Exception:
        return Path.cwd() / ".alpha" / "bots" / "runtime_profiles.json"


class DynamicProfileStore:
    """Durable, schema-versioned, ceiling-enforced registry of runtime profiles.

    Thread-safe. Durable across restarts. Loud on corruption.
    """

    def __init__(
        self,
        storage_path: str | Path | None = None,
        *,
        ceiling: AuthorityCeiling | None = None,
        approval_gate: ApprovalGate | None = None,
        event_store: Any = None,
    ) -> None:
        self.storage_path = (
            Path(storage_path).resolve() if storage_path else _default_store_path()
        )
        self._ceiling = ceiling
        self._approval_gate = approval_gate
        self._event_store = event_store
        self._lock = threading.RLock()
        self._profiles: dict[str, RuntimeProfile] = {}
        self._proposals: dict[str, ProfileProposal] = {}
        self._load()

    # -- ceiling / gate accessors ------------------------------------------
    def ceiling(self) -> AuthorityCeiling:
        return self._ceiling if self._ceiling is not None else get_ceiling()

    def set_ceiling(self, ceiling: AuthorityCeiling) -> None:
        """Bind a ceiling instance. Server-side only.

        There is deliberately no path here that accepts a *ceiling derived from a
        profile, a proposal or model output*: the argument type is
        :class:`AuthorityCeiling`, which is frozen and constructible only from
        server configuration. Exposing this keeps tests able to simulate a
        tightening without giving the model a way to loosen one.
        """
        self._ceiling = ceiling

    def set_approval_gate(self, gate: ApprovalGate) -> None:
        self._approval_gate = gate

    # -- durability --------------------------------------------------------
    def _load(self) -> None:
        """Load or FAIL LOUDLY. A missing file is a fresh install, not corruption."""
        if not self.storage_path.exists():
            return
        try:
            raw = self.storage_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProfileStoreUnreadable(
                f"runtime profile store {self.storage_path} exists but could not be read: {exc}. "
                f"Refusing to start with an empty roster: that would silently delete every "
                f"profile alpha created. Fix or remove the file deliberately."
            ) from exc
        if not raw.strip():
            raise ProfileStoreUnreadable(
                f"runtime profile store {self.store_label()} is empty. Refusing to treat an "
                f"empty document as 'no profiles' — that is indistinguishable from data loss."
            )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProfileStoreUnreadable(
                f"runtime profile store {self.store_label()} is not valid JSON: {exc}. "
                f"Refusing to revert to defaults."
            ) from exc
        if not isinstance(data, dict):
            raise ProfileStoreUnreadable(
                f"runtime profile store {self.store_label()} must contain an object, "
                f"got {type(data).__name__}."
            )
        version = data.get("schema_version")
        if version != PROFILE_STORE_SCHEMA_VERSION:
            raise ProfileStoreUnreadable(
                f"runtime profile store {self.store_label()} has schema_version {version!r}; "
                f"this build understands {PROFILE_STORE_SCHEMA_VERSION}. Refusing to guess."
            )
        # Any single bad profile fails the WHOLE load. Partially loading a
        # roster would let a malformed record silently disappear.
        try:
            for item in data.get("profiles", []):
                profile = RuntimeProfile.from_dict(item)
                self._profiles[profile.name] = profile
            for item in data.get("proposals", []):
                proposal = ProfileProposal.from_dict(item)
                self._proposals[proposal.profile_name] = proposal
        except ProfileValidationError as exc:
            raise ProfileStoreUnreadable(
                f"runtime profile store {self.store_label()} failed schema validation: {exc}. "
                f"Refusing to partially load it."
            ) from exc

    def store_label(self) -> str:
        return str(self.storage_path)

    def _save(self) -> None:
        """Atomic write. A failed write RAISES — a lost profile must be visible."""
        payload = {
            "schema_version": PROFILE_STORE_SCHEMA_VERSION,
            "profiles": [p.to_dict() for p in self._profiles.values()],
            "proposals": [p.to_dict() for p in self._proposals.values()],
            "updated_at": _now(),
        }
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.storage_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
            os.replace(tmp, self.storage_path)
        except OSError as exc:
            raise ProfileStoreUnreadable(
                f"could not persist runtime profile store {self.store_label()}: {exc}"
            ) from exc

    # -- reads -------------------------------------------------------------
    def get_profile(self, name: str) -> RuntimeProfile | None:
        with self._lock:
            return self._profiles.get((name or "").strip().lower())

    def get_proposal(self, name: str) -> ProfileProposal | None:
        with self._lock:
            return self._proposals.get((name or "").strip().lower())

    def list_profiles(self, *, include_disabled: bool = True) -> list[RuntimeProfile]:
        with self._lock:
            profiles = list(self._profiles.values())
        if not include_disabled:
            profiles = [p for p in profiles if p.status == "active"]
        return profiles

    def live_profile_count(self) -> int:
        with self._lock:
            return sum(1 for p in self._profiles.values() if p.status != "retired")

    def total_profile_count(self) -> int:
        with self._lock:
            return len(self._profiles)

    def list_proposals(self, state: str | None = None) -> list[ProfileProposal]:
        with self._lock:
            proposals = list(self._proposals.values())
        if state:
            proposals = [p for p in proposals if p.state == state]
        return proposals

    # -- Phase 1: propose --------------------------------------------------
    def propose_profile(self, proposal: ProfileProposal) -> ProfileProposal:
        """Record a proposal. Refuses immediately if it already breaks the ceiling.

        The ceiling check happens at PROPOSAL time as well as install time so a
        doomed request is refused at the earliest point and shows up in the
        ledger as a refusal, not as a silent success.
        """
        with self._lock:
            if proposal.profile_name in self._profiles:
                raise ProfileValidationError(
                    f"profile {proposal.profile_name!r} already exists; re-scope or retire it "
                    f"instead of creating a second one"
                )
            if proposal.profile_name in self._proposals and self._proposals[
                proposal.profile_name
            ].state == PROPOSAL_PENDING:
                raise ProfileValidationError(
                    f"a pending proposal for {proposal.profile_name!r} already exists"
                )
            # Refuse early, but do NOT narrow: an over-privileged request is an
            # error the operator must see.
            enforce_grant(
                proposal.requested_capabilities,
                creator_grant=proposal.creator_grant,
                subject=f"proposal {proposal.profile_name!r}",
                ceiling=self.ceiling(),
            )
            self._proposals[proposal.profile_name] = proposal
            self._save()
        record_governance_action(
            ACTION_PROFILE_PROPOSED,
            actor=proposal.creator,
            target=proposal.profile_name,
            reason=proposal.rationale or "specialised profile requested",
            details={"requested_capabilities": list(proposal.requested_capabilities)},
            store=self._event_store,
        )
        return proposal

    # -- Phase 1: approve + install ---------------------------------------
    def approve_proposal(self, name: str) -> ProfileProposal:
        """Run the approval gate. Only an approving gate may install a profile."""
        with self._lock:
            proposal = self._proposals.get((name or "").strip().lower())
            if proposal is None:
                raise ProfileApprovalError(f"no proposal named {name!r}")
            if proposal.state == PROPOSAL_INSTALLED:
                return proposal
        gate = self._approval_gate or default_approval_gate
        approved = bool(gate(proposal))
        with self._lock:
            proposal.state = PROPOSAL_APPROVED if approved else PROPOSAL_PENDING
            proposal.resolved_at = _now()
            self._save()
        return proposal

    def install_approved(self, name: str) -> RuntimeProfile:
        """Install an APPROVED proposal as a (disabled) profile.

        The grant is recomputed here from the creator's grant and the ceiling.
        Nothing on the proposal is trusted as a grant; the proposal's requested
        capabilities are an *input to validation*, never an authorisation.
        """
        key = (name or "").strip().lower()
        with self._lock:
            proposal = self._proposals.get(key)
            if proposal is None:
                raise ProfileApprovalError(f"no proposal named {name!r}")
            if proposal.state != PROPOSAL_APPROVED:
                raise ProfileApprovalError(
                    f"proposal {key!r} is {proposal.state!r}, not approved. "
                    f"Creation is a proposal; it is not an authorisation."
                )
            if key in self._profiles:
                raise ProfileValidationError(f"profile {key!r} already exists")

            grant = enforce_grant(
                proposal.requested_capabilities,
                creator_grant=proposal.creator_grant,
                subject=f"profile {key!r}",
                ceiling=self.ceiling(),
            )
            self.ceiling().assert_population_within_ceiling(
                live=self.live_profile_count(),
                total=self.total_profile_count(),
                subject=f"profile {key!r}",
            )

            profile = RuntimeProfile(
                name=key,
                display_name=proposal.profile_name.capitalize(),
                role=proposal.role or "Autonomous Specialist Teammate",
                system_prompt=proposal.system_prompt
                or generate_default_soul(key, proposal.role or "Autonomous Specialist Teammate"),
                description=proposal.description,
                granted_capabilities=grant,
                declared_capabilities=frozenset(
                    normalise_capabilities(proposal.requested_capabilities)
                ),
                skills=list(proposal.skills),
                mcp_servers=list(proposal.mcp_servers),
                model=proposal.model,
                department=proposal.department,
                status=PROFILE_DISABLED_STATUS,
                creator=proposal.creator,
                creator_grant=normalise_capabilities(proposal.creator_grant),
                metadata=dict(proposal.metadata),
            )
            # Carry declared-vs-granted forensics into the operator-visible view
            # without ever letting it authorise anything.
            profile.metadata.setdefault("declared_only", sorted(
                set(profile.declared_capabilities) - set(grant)
            ))
            self._profiles[key] = profile
            proposal.state = PROPOSAL_INSTALLED
            proposal.resolved_at = _now()
            self._save()

        record_governance_action(
            ACTION_PROFILE_INSTALLED,
            actor=proposal.creator,
            target=key,
            reason=proposal.rationale or "approved profile installed",
            details={
                "granted_capabilities": sorted(grant),
                "declared_capabilities": sorted(profile.declared_capabilities),
                "status": profile.status,
            },
            store=self._event_store,
        )
        return profile

    def reject_proposal(self, name: str, *, reason: str) -> ProfileProposal:
        key = (name or "").strip().lower()
        with self._lock:
            proposal = self._proposals.get(key)
            if proposal is None:
                raise ProfileApprovalError(f"no proposal named {name!r}")
            proposal.state = PROPOSAL_REJECTED
            proposal.resolved_at = _now()
            self._save()
        record_governance_action(
            ACTION_PROFILE_REFUSED,
            actor=proposal.creator,
            target=key,
            reason=reason,
            store=self._event_store,
        )
        return proposal

    # -- Phase 1/2: revalidate on use -------------------------------------
    def revalidate_profile(self, name: str) -> tuple[RuntimeProfile | None, list[str]]:
        """Re-check an existing profile against the CURRENT ceiling on next use.

        This is what makes a ceiling TIGHTENING bite. Without it, lowering
        ``max_capability_rank`` would do nothing to profiles that already exist —
        the exact "silent no-op" failure the requirement calls out. The profile
        is narrowed (not refused: it already exists and refusing to load it would
        break running work) and the demotion is recorded in the ledger with what
        was removed.
        """
        key = (name or "").strip().lower()
        with self._lock:
            profile = self._profiles.get(key)
            if profile is None:
                return None, []
            kept, removed = narrow_to_ceiling(
                profile.granted_capabilities, subject=key, ceiling=self.ceiling()
            )
            if not removed:
                return profile, []
            demoted_from = profile.demoted_from | (profile.granted_capabilities - kept)
            profile.granted_capabilities = kept
            profile.demoted_from = normalise_capabilities(demoted_from)
            profile.profile_version += 1
            profile.updated_at = _now()
            # A demoted profile that can no longer do its job is demoted to
            # disabled rather than left enabled with an empty grant, so it is
            # never dispatched work it cannot perform.
            if not kept and profile.status == "active":
                profile.status = PROFILE_DISABLED_STATUS
            self._save()
            snapshot = profile
        record_governance_action(
            ACTION_PROFILE_DEMOTED,
            actor="server",
            target=key,
            reason="authority ceiling tightened; grant re-validated on use",
            details={
                "removed_capabilities": sorted(removed),
                "remaining_capabilities": sorted(kept),
                "ceiling": self.ceiling().to_dict(),
            },
            store=self._event_store,
        )
        return snapshot, sorted(removed)

    def authorized_profile(self, name: str) -> RuntimeProfile | None:
        """The only read path a dispatcher should use.

        Re-validates against the current ceiling first, then refuses a profile
        that is not enabled. This is the call that makes "in-flight work never
        silently continues under a grant that no longer exists" true for the
        dispatch path: a dispatch that has not gone through here has not been
        authorised.
        """
        profile, _removed = self.revalidate_profile(name)
        if profile is None or profile.status != "active":
            return None
        return profile

    # -- useful-work accounting (feeds idle detection) --------------------
    def record_work_outcome(self, name: str, *, useful: bool) -> RuntimeProfile | None:
        key = (name or "").strip().lower()
        with self._lock:
            profile = self._profiles.get(key)
            if profile is None:
                return None
            profile.total_task_count += 1
            if useful:
                profile.useful_work_count += 1
                profile.last_useful_work_at = _now()
            profile.updated_at = _now()
            self._save()
            return profile


_global_store: DynamicProfileStore | None = None
_global_store_path: str | None = None


def get_dynamic_profile_store(storage_path: str | Path | None = None) -> DynamicProfileStore:
    """Process-wide runtime profile store.

    Rebuilds when the resolved path changes, so a test (or a Gateway request
    with a different ``AGENT_WORKSPACE_HOME``) never reuses a store bound to a
    stale location.
    """
    global _global_store, _global_store_path
    if storage_path is not None:
        resolved = str(Path(storage_path).resolve())
        if _global_store is None or _global_store_path != resolved:
            _global_store = DynamicProfileStore(storage_path=resolved)
            _global_store_path = resolved
        return _global_store
    if _global_store is None:
        _global_store = DynamicProfileStore()
        try:
            _global_store_path = str(_global_store.storage_path)
        except Exception:
            _global_store_path = None
        return _global_store
    # Re-resolve the default location: AGENT_WORKSPACE_HOME may have been set
    # after import, and a store bound to a stale path would read the wrong file.
    try:
        live = str(_default_store_path().resolve())
    except Exception:
        return _global_store
    if _global_store_path != live:
        _global_store = DynamicProfileStore()
        _global_store_path = str(_global_store.storage_path)
    return _global_store


__all__ = [
    "ApprovalGate",
    "DynamicProfileStore",
    "PROFILE_DISABLED_STATUS",
    "PROFILE_STORE_SCHEMA_VERSION",
    "PROPOSAL_APPROVED",
    "PROPOSAL_INSTALLED",
    "PROPOSAL_PENDING",
    "PROPOSAL_REJECTED",
    "PROPOSAL_STATES",
    "ProfileApprovalError",
    "ProfileProposal",
    "ProfileStoreUnreadable",
    "ProfileValidationError",
    "RuntimeProfile",
    "AuthorityViolation",
    "default_approval_gate",
    "get_dynamic_profile_store",
]
