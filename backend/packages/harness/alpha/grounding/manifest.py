"""What the agent can actually do right now, in a form it will use.

## The finding this module implements

An audit of coding agents across 3,000 turns (``RepoReuse``, 2026) measured that
the failure this module targets is not *finding* existing work — self-recall of
the agent's own earlier code sat above 98% in every turn — but *choosing to build
on it anyway*. Repository recall collapsed from 86.3% to 38.8% across turns, and
by turn 5 half the task chains contained a re-implementation while pass rates
barely moved.

That audit also ran the ablation that decides how this module is written. Three
ways of telling the agent about its own prior work were compared:

* no memory — self-reuse 30.0%
* **interface memory** (which functions exist, their names and signatures) — **67.8%**
* full source of the agent's own earlier submissions — 29.2%, *no better than
  nothing*, and it pushed structural duplication to 70.7%

So: a compact, addressable map. Never a source dump. Dumping context here is
measured to be worse than useless, and :meth:`CapabilityManifest.render_for_prompt`
has a regression test that fails if implementation text ever reaches it.

## The other rule: never advertise what is not wired

This repository's own ``alpha.capabilities.honesty`` exists because the recurring
defect was a capability that imports cleanly, passes unit tests, is documented as
a feature, and has no production caller. A manifest that lists such a thing as
available would manufacture exactly the hallucination this package exists to
prevent — so every entry carries a :class:`Availability` derived from a real
check, and :attr:`Availability.UNWIRED` entries are reported *as unwired*, in the
manifest, to the agent. The gap is the useful information.

## Availability is not reachability

:attr:`EntrySource.REGISTRY` means "declared". It does **not** mean "answering
right now". The two are different facts and conflating them is how a manifest
ends up telling an agent to call an MCP server that is down, at which point the
agent invents the response. Entries sourced from a registry say so, and the
manifest keeps a separate section for what was probed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from alpha.capabilities.eligibility import CapabilityMatch, infer_capability_tags, match_capabilities, normalize_tags
from alpha.capabilities.honesty import Claim, WiringState

__all__ = [
    "Availability",
    "CapabilityEntry",
    "CapabilityManifest",
    "CapabilityProbe",
    "EntrySource",
    "build_manifest",
]


class Availability(StrEnum):
    """Whether an entry may be presented to the agent as something to use."""

    #: Verified against a live check this request.
    AVAILABLE = "available"
    #: Declared, but not probed. Usable, and labelled as declared.
    DECLARED = "declared"
    #: Exists in a registry but nothing outside its module calls it.
    UNWIRED = "unwired"
    #: Reachable only through a reference a static walk cannot see.
    UNPROVEN = "unproven"
    #: Configured but known-down or unconfigured right now.
    DOWN = "down"


#: Entries in these states must not be advertised as things to use.
NOT_USABLE: frozenset[Availability] = frozenset({Availability.UNWIRED, Availability.DOWN})


class EntrySource(StrEnum):
    """How one fact about a capability was established.

    Carried on every entry so a reader can tell a measurement from a declaration
    without having to trust the rendering.
    """

    #: Came out of a registry the process actually holds.
    REGISTRY = "registry"
    #: Answered a live probe in this request.
    PROBE = "probe"
    #: Decided by the static advertised-vs-wired audit.
    WIRING_AUDIT = "wiring_audit"
    #: Declared by the caller that assembled the manifest.
    DECLARED = "declared"


@dataclass(frozen=True)
class CapabilityProbe:
    """A liveness result for one named dependency."""

    name: str
    healthy: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "healthy": self.healthy, "detail": self.detail}


@dataclass(frozen=True)
class CapabilityEntry:
    """One thing the agent could use, and how far that claim has been checked.

    ``address`` is where to go next — a dotted path, a route, a file — and is the
    only navigational field. There is deliberately no field for source text.
    """

    name: str
    kind: str
    summary: str
    address: str
    tags: frozenset[str] = frozenset()
    availability: Availability = Availability.DECLARED
    source: EntrySource = EntrySource.REGISTRY
    #: Free-form caveat carried verbatim to the prompt ("auth: none", "sandbox: local").
    note: str = ""

    @property
    def usable(self) -> bool:
        return self.availability not in NOT_USABLE

    def line(self) -> str:
        """One line for the agent: what it is, and where it lives.

        Deliberately name-first and location-second. A summary that leads with
        prose is a summary the model will paraphrase instead of use.
        """
        marker = "" if self.availability in (Availability.AVAILABLE, Availability.DECLARED) else f" [{self.availability.value}]"
        note = f"  # {self.note}" if self.note else ""
        return f"- {self.kind}:{self.name}{marker} — {self.summary} -> {self.address}{note}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "summary": self.summary,
            "address": self.address,
            "tags": sorted(self.tags),
            "availability": self.availability.value,
            "source": self.source.value,
            "note": self.note,
            "usable": self.usable,
        }


@dataclass
class CapabilityManifest:
    """Everything the agent can do, plus what it cannot and why."""

    entries: tuple[CapabilityEntry, ...] = ()
    #: Known limitations, stated as first-class content rather than left implicit.
    gaps: tuple[str, ...] = ()
    probes: tuple[CapabilityProbe, ...] = ()
    #: Wiring verdicts from the static audit, keyed by capability id.
    wiring: dict[str, WiringState] = field(default_factory=dict)
    #: How this manifest was assembled, for the audit trail.
    assembled_from: tuple[str, ...] = ()

    # -- reads -----------------------------------------------------------

    def __len__(self) -> int:
        return len(self.entries)

    def usable(self) -> tuple[CapabilityEntry, ...]:
        return tuple(e for e in self.entries if e.usable)

    def down(self) -> tuple[CapabilityEntry, ...]:
        """Entries that are declared but currently unreachable.

        Named separately rather than folded into :meth:`usable`, because a
        capability that is *down* must be disclosed. Hiding it produces a
        fabricated fallback at exactly the moment the agent needs the truth.
        """
        return tuple(e for e in self.entries if e.availability is Availability.DOWN)

    def unwired(self) -> tuple[CapabilityEntry, ...]:
        return tuple(e for e in self.entries if e.availability is Availability.UNWIRED)

    def by_kind(self, kind: str) -> tuple[CapabilityEntry, ...]:
        return tuple(e for e in self.entries if e.kind == kind)

    def get(self, name: str) -> CapabilityEntry | None:
        for entry in self.entries:
            if entry.name == name:
                return entry
        return None

    def select(self, objective: str) -> CapabilityMatch:
        """Narrow the manifest to what the stated task plausibly needs.

        Delegates tag inference to :func:`alpha.capabilities.eligibility` rather
        than inventing a second vocabulary — "capability match" has to mean one
        thing across dispatch and self-knowledge, or the manifest and the router
        will disagree about the same agent.
        """
        return match_capabilities(infer_capability_tags(objective), _ManifestProfile(self.usable()))

    def relevant(self, objective: str, *, limit: int = 12) -> tuple[CapabilityEntry, ...]:
        """The subset worth putting in front of the model for this task.

        Falls back to the whole usable set when tag inference matches nothing,
        and the caller is expected to say so in the rendering. An empty section
        and a section that says "nothing matched" are very different messages to
        a model: the first reads as "you have no tools".
        """
        match = self.select(objective)
        chosen = [e for e in self.usable() if e.tags & match.matched]
        if not chosen:
            return self.usable()[:limit]
        chosen.sort(key=lambda e: (-len(e.tags & match.matched), e.kind, e.name))
        return tuple(chosen[:limit])

    # -- rendering -------------------------------------------------------

    def render_for_prompt(
        self,
        objective: str = "",
        *,
        limit: int = 12,
        include_gaps: bool = True,
    ) -> str:
        """The manifest block for the agent's context.

        Rules, each of which has cost real duplication elsewhere:

        * **no source text, ever** — addresses only (the ablation above);
        * **gaps are not optional** — a manifest with no gap section reads as
          "nothing is missing", which is a hallucination of the most expensive
          kind because the agent then proceeds confidently into the gap;
        * **down and unwired entries are named**, never dropped;
        * a **tag miss is stated**, so an empty selection is legible.
        """
        if not self.entries and not self.gaps:
            return ""

        lines: list[str] = ["<capability_manifest>"]
        if objective:
            chosen = self.relevant(objective, limit=limit)
            match = self.select(objective)
            lines.append(f"task: {objective[:200]}")
            if match.missing:
                lines.append(f"matched: {sorted(match.matched) or 'none'}; no capability carries {sorted(match.missing)}")
            else:
                lines.append(f"matched: {sorted(match.matched) or 'no tag match — full usable set shown'}")
        else:
            chosen = self.usable()[:limit]

        usable = [e for e in self.usable() if e in chosen]
        others = [e for e in self.usable() if e not in chosen]
        for entry in usable:
            lines.append(entry.line())
        if others:
            lines.append(f"- (+{len(others)} further usable capabilities not shown; ask for a listing)")
        for entry in self.down():
            lines.append(f"- {entry.kind}:{entry.name} [down] — unavailable now: {entry.note or entry.address}")
        for entry in self.unwired():
            lines.append(f"- {entry.kind}:{entry.name} [unwired] — declared but no production caller: {entry.address}")
        if include_gaps and self.gaps:
            lines.append("known limits:")
            lines.extend(f"- {gap}" for gap in self.gaps)
        lines.append("</capability_manifest>")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": len(self.entries),
            "usable": len(self.usable()),
            "down": [e.name for e in self.down()],
            "unwired": [e.name for e in self.unwired()],
            "gaps": list(self.gaps),
            "probes": [p.to_dict() for p in self.probes],
            "wiring": {k: v.value for k, v in self.wiring.items()},
            "assembled_from": list(self.assembled_from),
            "entries": [e.to_dict() for e in self.entries],
        }


@dataclass(frozen=True)
class _ManifestProfile:
    """Adapts manifest entries to the shared eligibility Protocol."""

    _entries: tuple[CapabilityEntry, ...]

    @property
    def name(self) -> str:
        return "manifest"

    @property
    def status(self) -> str:
        return "active"

    @property
    def capabilities(self) -> list[str]:
        return [e.name for e in self._entries]

    @property
    def skills(self) -> list[str]:
        return [e.name for e in self._entries if e.kind == "skill"]


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _wiring_for(wiring: Mapping[str, WiringState], address: str) -> Availability:
    """Translate a wiring verdict on ``address`` into an availability.

    The audit keys claims by module path, so a catalog entry whose module was
    audited is the case this resolves. An entry that was never audited is
    ``DECLARED``, not ``UNWIRED`` — absence of an audit is not a negative
    finding, and reporting it as one would empty the manifest of every
    never-audited capability.
    """
    if not address:
        return Availability.DECLARED
    for key, state in wiring.items():
        if not key or address != key and key not in address:
            continue
        if state is WiringState.UNWIRED:
            return Availability.UNWIRED
        if state is WiringState.UNPROVEN:
            return Availability.UNPROVEN
    return Availability.DECLARED


def _entries_from_mapping(
    source_name: str,
    items: Mapping[str, Any] | None,
    *,
    kind: str,
    address_template: str,
    summary_key: str = "description",
    wiring: Mapping[str, WiringState] | None = None,
    probes: Mapping[str, CapabilityProbe] | None = None,
    tag_source: Iterable[str] = (),
) -> tuple[CapabilityEntry, ...]:
    """Turn a registry mapping into entries.

    ``items`` is a name -> (object | description | mapping) mapping. Anything
    that does not match those shapes is skipped rather than guessed at, so a
    registry gaining a new member type degrades to a smaller manifest instead of
    a manifest full of invented summaries.
    """
    out: list[CapabilityEntry] = []
    wiring = wiring or {}
    probes = probes or {}
    tag_pool = normalize_tags(tag_source)
    for name, value in (items or {}).items():
        summary = ""
        note = ""
        if isinstance(value, str):
            summary = value
        elif isinstance(value, Mapping):
            summary = str(value.get(summary_key) or value.get("summary") or "")
            note = str(value.get("note") or "")
        address = address_template.format(name=name)
        probe = probes.get(name)
        if probe is not None:
            availability = Availability.AVAILABLE if probe.healthy else Availability.DOWN
            if not probe.detail:
                note = ""
        else:
            availability = _wiring_for(wiring, address)
        if probe is not None and probe.detail:
            note = probe.detail
        out.append(
            CapabilityEntry(
                name=name,
                kind=kind,
                summary=summary or f"{source_name} member",
                address=address,
                tags=tag_pool,
                availability=availability,
                source=EntrySource.PROBE if probe is not None else EntrySource.REGISTRY,
                note=note,
            )
        )
    return tuple(out)


def build_manifest(
    *,
    tools: Mapping[str, Any] | None = None,
    skills: Mapping[str, Any] | None = None,
    middlewares: Mapping[str, Any] | None = None,
    plugins: Mapping[str, Any] | None = None,
    subagents: Mapping[str, Any] | None = None,
    mcp_servers: Mapping[str, Any] | None = None,
    catalog: Mapping[str, Any] | None = None,
    probes: Sequence[CapabilityProbe] = (),
    gaps: Sequence[str] = (),
    wiring_claims: Sequence[Claim] = (),
    wiring_reports: Mapping[str, WiringState] | None = None,
    availability_probes: Mapping[str, CapabilityProbe] | None = None,
) -> CapabilityManifest:
    """Assemble the manifest from live registries.

    Every source is an explicit argument rather than an import, for three reasons:
    the middleware that builds this runs on the agent's hot path and must not
    drag in the MCP or plugin loaders; the assembly is testable without any of
    them; and — the point — a source that is *not* passed shows up as absent, so
    an unwired registry cannot be advertised by omission.

    ``catalog`` is the ``alpha.capabilities.catalog.CAPABILITY_CATALOG``. Its
    entries are gated through the static wiring audit, which is what keeps a
    documented-but-uncalled subsystem from being offered as a working feature.
    """
    probe_map = {p.name: p for p in probes}
    probe_map.update(availability_probes or {})

    wiring: dict[str, WiringState] = dict(wiring_reports or {})
    for claim in wiring_claims:
        state = (wiring_reports or {}).get(claim.capability_id)
        if state is not None:
            wiring[claim.symbol.split("::", 1)[0]] = state

    entries: list[CapabilityEntry] = []
    entries += _entries_from_mapping("tool", tools, kind="tool", address_template="tool:{name}", probes=probe_map, tag_source=list(tools or {}))
    entries += _entries_from_mapping("skill", skills, kind="skill", address_template="skills/{name}/SKILL.md", probes=probe_map, tag_source=list(skills or {}))
    entries += _entries_from_mapping("middleware", middlewares, kind="middleware", address_template="middleware:{name}", probes=probe_map, tag_source=list(middlewares or {}))
    entries += _entries_from_mapping("plugin", plugins, kind="plugin", address_template="plugin:{name}", probes=probe_map, tag_source=list(plugins or {}))
    entries += _entries_from_mapping("subagent", subagents, kind="subagent", address_template="subagent:{name}", probes=probe_map, tag_source=list(subagents or {}))
    entries += _entries_from_mapping("mcp", mcp_servers, kind="mcp", address_template="mcp:{name}", probes=probe_map, tag_source=list(mcp_servers or {}))

    for capability_id, spec in (catalog or {}).items():
        module = str(getattr(spec, "module", "") or "")
        target = str(getattr(spec, "target", "") or "")
        address = f"{module}:{target}" if module and target else module or capability_id
        default_enabled = bool(getattr(spec, "default_enabled", False))
        entries.append(
            CapabilityEntry(
                name=capability_id,
                kind="subsystem",
                summary=str(getattr(spec, "description", "") or ""),
                address=address,
                tags=normalize_tags({capability_id, str(getattr(spec, "kind", "") or "")}),
                availability=_wiring_for(wiring, module),
                source=EntrySource.WIRING_AUDIT if module in wiring else EntrySource.REGISTRY,
                note="" if default_enabled else "opt-in: not loaded unless enabled in config",
            )
        )

    assembled = tuple(
        name
        for name, value in (
            ("tools", tools),
            ("skills", skills),
            ("middlewares", middlewares),
            ("plugins", plugins),
            ("subagents", subagents),
            ("mcp_servers", mcp_servers),
            ("catalog", catalog),
            ("probes", probes or ()),
            ("wiring", wiring_claims),
        )
        if value
    )
    return CapabilityManifest(
        entries=tuple(entries),
        gaps=tuple(gaps),
        probes=tuple(probes),
        wiring=wiring,
        assembled_from=assembled,
    )
