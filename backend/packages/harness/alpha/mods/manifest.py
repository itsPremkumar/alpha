"""Declarative mod manifests and the :func:`describe_mod` projection.

Claude Code answers "what does this mod do before I install it?" with
``claude plugin validate``: it statically reads the hooks module and prints
``hooks:``, ``calls:``, ``state reads:``, ``state writes:`` and
``env reads:`` / ``env writes:`` — *without running the code*. That report is
the whole trust model, because a mod is code that runs with your permissions.

Alpha's equivalent has two sources of the same fact:

1. a **manifest** the mod can declare (the contract it intends to keep), and
2. the **kernel's own knowledge** — the events it subscribed to and the
   capabilities it was granted.

:func:`describe_mod` returns both, side by side and never merged. A declared
hook the mod does not subscribe to is a claim; a subscription nobody declared
is a surprise. Collapsing them into one list would hide exactly the discrepancy
a reviewer is looking for, so the projection keeps ``declared`` and ``observed``
apart and adds a ``discrepancies`` list naming every divergence.

Nothing here executes mod code. The AST scan lives in
:mod:`alpha.mods.cli`; this module is the data shape both share.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

#: Maps a ``$`` namespace to the capability gate that protects it. This is the
#: single source for both the CLI's static validation and the runtime
#: :class:`~alpha.mods.context.CapabilityContext`, so a namespace added in one
#: place cannot be ungated in the other.
NAMESPACE_CAPABILITIES: dict[str, str] = {
    "tools": "tools:read",
    "models": "models:complete",
    "evidence": "evidence:record",
    "estop": "estop:control",
    "clock": "clock:schedule",
    "storage": "storage:write",
    "ui": "ui:render",
    "commands": "commands:register",
    "fs": "fs:read",
}

#: Capabilities an externally supplied mod may never be granted, regardless of
#: what it declared. Tripping or clearing a fleet-wide emergency stop is a
#: first-party safety authority; handing it to third-party code would let a mod
#: disengage a stop an operator engaged, or halt every run in the fleet.
RESERVED_CAPABILITIES: frozenset[str] = frozenset({"estop:control"})

#: Priorities at or below which only first-party (or explicitly
#: operator-approved) mods may register. A user-installed mod registered here
#: would run *before* the security and emergency triad and could therefore
#: neutralize it — the same property Claude Code gives ``sec-default``, which
#: loads first precisely so nothing installed later can outrank it.
GUARDED_PRIORITY_CEILING = 200  # ModPriority.SECURITY


@dataclass(frozen=True)
class ModManifest:
    """The contract a mod declares about itself.

    Every field is optional; a mod that declares nothing is still describable
    from the kernel's own bookkeeping. Declaring is what makes the *difference*
    between intent and behaviour visible, which is why the fields exist at all.
    """

    name: str = ""
    version: str = ""
    description: str = ""
    hooks: tuple[str, ...] = ()
    calls: tuple[str, ...] = ()
    state_reads: tuple[str, ...] = ()
    state_writes: tuple[str, ...] = ()
    env_reads: tuple[str, ...] = ()
    env_writes: tuple[str, ...] = ()
    gating: bool = False
    public: bool = False

    @classmethod
    def create(
        cls,
        *,
        name: str = "",
        version: str = "",
        description: str = "",
        hooks: Iterable[str] = (),
        calls: Iterable[str] = (),
        state_reads: Iterable[str] = (),
        state_writes: Iterable[str] = (),
        env_reads: Iterable[str] = (),
        env_writes: Iterable[str] = (),
        gating: bool = False,
        public: bool = False,
    ) -> ModManifest:
        return cls(
            name=name,
            version=version,
            description=description,
            hooks=tuple(sorted({str(h) for h in hooks})),
            calls=tuple(sorted({str(c) for c in calls})),
            state_reads=tuple(sorted({str(s) for s in state_reads})),
            state_writes=tuple(sorted({str(s) for s in state_writes})),
            env_reads=tuple(sorted({str(e) for e in env_reads})),
            env_writes=tuple(sorted({str(e) for e in env_writes})),
            gating=bool(gating),
            public=bool(public),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "hooks": list(self.hooks),
            "calls": list(self.calls),
            "state_reads": list(self.state_reads),
            "state_writes": list(self.state_writes),
            "env_reads": list(self.env_reads),
            "env_writes": list(self.env_writes),
            "gating": self.gating,
            "public": self.public,
        }


#: Every mod may declare a ``manifest`` — a class attribute of this type, or a
#: zero-argument callable returning one for late binding.
MANIFEST_ATTR = "manifest"


def _coerce_manifest(mod: Any) -> ModManifest | None:
    raw = getattr(mod, MANIFEST_ATTR, None)
    if raw is None:
        return None
    if callable(raw):
        try:
            raw = raw()
        except Exception:  # pragma: no cover - defensive; describe() must not raise
            return None
    if isinstance(raw, ModManifest):
        return raw
    if isinstance(raw, dict):
        try:
            return ModManifest.create(**raw)
        except TypeError:
            return None
    return None


def _is_first_party(mod: Any) -> bool:
    """True when the mod class lives under Alpha's own ``mods`` package.

    First-party mods earn their reviewed capability declarations by default;
    anything else must be granted capabilities explicitly at registration. This
    is a module-path check, not a security boundary — an operator who drops a
    file into ``alpha/mods/`` has made it first-party by doing so.
    """
    module = getattr(mod.__class__, "__module__", "") or ""
    return module.startswith("alpha.mods.")


def _subscriptions(mod: Any) -> list[str]:
    subs = getattr(mod, "subscribed_events", None)
    if subs is None:
        return ["*"]
    if isinstance(subs, str):
        return [subs]
    return sorted({str(s) for s in subs})


@dataclass
class ModDescription:
    """What Alpha knows about one registered mod, declared beside observed.

    ``discrepancies`` is the field that earns this shape its keep: it names
    every place the mod's declared contract and its live behaviour disagree.
    An empty list means the mod is doing exactly what it said.
    """

    name: str
    version: str
    description: str
    priority: int
    first_party: bool
    granted_capabilities: list[str]
    required_capabilities: list[str]
    subscribed_events: list[str]
    declared: dict[str, Any]
    observed: dict[str, Any]
    discrepancies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "priority": self.priority,
            "first_party": self.first_party,
            "granted_capabilities": list(self.granted_capabilities),
            "required_capabilities": list(self.required_capabilities),
            "subscribed_events": list(self.subscribed_events),
            "declared": dict(self.declared),
            "observed": dict(self.observed),
            "discrepancies": list(self.discrepancies),
        }


def describe_mod(
    mod: Any,
    *,
    granted_capabilities: Iterable[str] = (),
    declared_state: dict[str, list[str]] | None = None,
    observed_state: dict[str, list[str]] | None = None,
) -> ModDescription:
    """Build the review-facing description of one mod.

    This function never registers, dispatches, or executes anything. It answers
    the question an operator asks before trusting a mod: *what does it claim,
    what is it actually wired to, and where do those differ?*
    """
    manifest = _coerce_manifest(mod) or ModManifest.create(
        name=str(getattr(mod, "name", "") or ""),
        version=str(getattr(mod, "version", "") or ""),
        description=str(getattr(mod, "__doc__", "") or "").strip().split("\n")[0],
    )

    name = str(getattr(mod, "name", "") or manifest.name or mod.__class__.__name__)
    required = sorted({str(c) for c in (getattr(mod, "required_capabilities", set()) or set())})
    granted = sorted({str(c) for c in granted_capabilities})
    declared_state = declared_state or {}
    observed_state = observed_state or {}

    declared = manifest.to_dict()
    declared["name"] = name
    observed = {
        "hooks": _subscriptions(mod),
        "calls": granted,
        "state_reads": list(observed_state.get("state_reads", [])),
        "state_writes": list(observed_state.get("state_writes", [])),
        "env_reads": [],
        "env_writes": [],
    }
    if declared_state:
        observed["declared_state"] = {
            "state_reads": list(declared_state.get("state_reads", [])),
            "state_writes": list(declared_state.get("state_writes", [])),
        }

    discrepancies: list[str] = []
    declared_hooks = set(manifest.hooks)
    observed_hooks = set(observed["hooks"])
    for hook in sorted(declared_hooks - observed_hooks):
        discrepancies.append(f"declares hook '{hook}' but does not subscribe to it")
    for hook in sorted(observed_hooks - declared_hooks):
        if observed_hooks != {"*"} and hook != "*":
            discrepancies.append(f"subscribes to '{hook}' without declaring it")
    for cap in sorted(set(granted) - set(required)):
        discrepancies.append(f"granted capability '{cap}' is not declared in required_capabilities")
    for cap in sorted(set(required) - set(granted)):
        discrepancies.append(f"declares capability '{cap}' but was not granted it")
    for cap in sorted(set(granted) & set(RESERVED_CAPABILITIES)):
        if not _is_first_party(mod):
            discrepancies.append(f"holds reserved capability '{cap}'")
    for key in sorted(set(declared.get("state_reads", [])) - set(observed["state_reads"])):
        if observed["state_reads"]:
            discrepancies.append(f"declares state read '{key}' that was never read")
    for key in sorted(set(observed["state_writes"]) - set(declared.get("state_writes", []))):
        if declared.get("state_writes") and observed["state_writes"]:
            discrepancies.append(f"writes state key '{key}' without declaring it")

    return ModDescription(
        name=name,
        version=str(getattr(mod, "version", "") or manifest.version or "1.0.0"),
        description=manifest.description or (mod.__class__.__doc__ or "").strip().split("\n")[0],
        priority=int(getattr(mod, "priority", 2000)),
        first_party=_is_first_party(mod),
        granted_capabilities=granted,
        required_capabilities=required,
        subscribed_events=observed["hooks"],
        declared=declared,
        observed=observed,
        discrepancies=discrepancies,
    )
