"""The APEX invariant set: spec §188, made checkable.

Spec §188 lists twelve invariants. Each was *partly* enforced somewhere in this
repository before APEX existed, but nothing stated which site enforces which one,
so "APEX has twelve invariants" was an aspiration rather than a claim a test
could check. This module is the statement.

**The design rule that keeps this honest.** An :class:`Invariant` row names the
module that enforces it, and :func:`check_invariants` reports ``live=True``
only when that module actually imports *and* exposes the named symbol. A row
whose enforcement site is missing reports ``live=False`` with the reason. It does
not report a pass, and it does not report ``0`` for "could not look" — the same
rule the self-inventory plane applies to an unreadable source, for the same
reason: "I could not check" and "I checked and it is fine" lead to opposite
decisions.

**What this is not.** It is not an enforcement layer. APEX does not wrap these
sites or intercept their calls; it asserts that they exist and reports which
one carries each guarantee. Re-implementing any of them here would be the
twenty-sixth policy engine in a repository that already has five.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "INVARIANTS",
    "InvariantCheck",
    "InvariantReport",
    "InvariantStatus",
    "check_invariants",
    "enforcement_site_of",
    "invariants_by_id",
]


class InvariantStatus(StrEnum):
    """Outcome of probing one invariant's enforcement site."""

    LIVE = "live"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class InvariantCheck:
    """One invariant and the site that enforces it.

    ``symbol`` is the attribute that must exist on ``module``. Naming the symbol
    rather than only the module matters: several of these are packages whose
    ``__init__`` imports nothing (the ``EngineRegistry`` lesson from the
    self-inventory plane), so a module-only probe would report healthy for an
    empty namespace.
    """

    id: str
    statement: str
    module: str
    symbol: str
    spec_section: str
    applies_when: str = "always"


#: Spec §188 I1–I12, in order, each pointing at its real enforcement site.
INVARIANTS: tuple[InvariantCheck, ...] = (
    InvariantCheck(
        id="I1",
        statement="Every action belongs to a mission/task.",
        module="alpha.workflow.leases",
        symbol="LeaseManager",
        spec_section="§188-I1",
    ),
    InvariantCheck(
        id="I2",
        statement="Every side effect has a policy decision.",
        module="alpha.tools.governance",
        symbol="GovernanceRegistry",
        spec_section="§188-I2",
    ),
    InvariantCheck(
        id="I3",
        statement="Every background worker has a lease.",
        module="alpha.subagents.lifecycle",
        symbol="SubagentLifecycleManager",
        spec_section="§188-I3",
    ),
    InvariantCheck(
        id="I4",
        statement="Every complex mission has a durable checkpoint.",
        module="alpha.missions.store",
        symbol="MissionStore",
        spec_section="§188-I4",
    ),
    InvariantCheck(
        id="I5",
        statement="Every failure creates an observable record.",
        module="alpha.workflow.failures",
        symbol="classify_node_failure",
        spec_section="§188-I5",
    ),
    InvariantCheck(
        id="I6",
        statement="Every completion requires verification.",
        module="alpha.mission.acceptance",
        symbol="assert_acceptance_passed",
        spec_section="§188-I6",
    ),
    InvariantCheck(
        id="I7",
        statement="Parent cancellation propagates.",
        module="alpha.runtime.side_effects.ledger",
        symbol="SideEffectReclaimer",
        spec_section="§188-I7",
    ),
    InvariantCheck(
        id="I8",
        statement="A denied action cannot be re-enabled by the model.",
        module="alpha.safety.authority.taint",
        symbol="TaintTurn",
        spec_section="§188-I8",
    ),
    InvariantCheck(
        id="I9",
        statement="APEX cannot disable the emergency stop.",
        module="alpha.runtime.control",
        symbol="read_state",
        spec_section="§188-I9",
        applies_when="always",
    ),
    InvariantCheck(
        id="I10",
        statement="Global policy outranks mission preferences.",
        module="alpha.safety.authority.scopes",
        symbol="compose_scopes",
        spec_section="§188-I10",
    ),
    InvariantCheck(
        id="I11",
        statement="Retrying must be idempotent or state-checked.",
        module="alpha.runtime.side_effects.statuses",
        symbol="validate_transition",
        spec_section="§188-I11",
    ),
    InvariantCheck(
        id="I12",
        statement="External/untrusted content cannot become execution authority.",
        module="alpha.safety.authority.taint",
        symbol="assert_current_turn_clean",
        spec_section="§188-I12",
    ),
)


@dataclass(frozen=True, slots=True)
class InvariantReport:
    """One invariant's declared site availability; runtime enforcement is unverified."""

    id: str
    statement: str
    status: InvariantStatus
    module: str
    symbol: str
    spec_section: str
    reason: str = ""
    probe_scope: str = "module_symbol_presence"
    runtime_enforcement_verified: bool = False

    @property
    def live(self) -> bool:
        return self.status is InvariantStatus.LIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "status": self.status.value,
            "live": self.live,
            "module": self.module,
            "symbol": self.symbol,
            "spec_section": self.spec_section,
            "reason": self.reason,
            "probe_scope": self.probe_scope,
            "runtime_enforcement_verified": self.runtime_enforcement_verified,
        }


def invariants_by_id() -> dict[str, InvariantCheck]:
    return {check.id: check for check in INVARIANTS}


def enforcement_site_of(invariant_id: str) -> InvariantCheck | None:
    """The declared enforcement site for one invariant, or ``None``."""
    return invariants_by_id().get(str(invariant_id).upper())


def _probe(check: InvariantCheck) -> InvariantReport:
    try:
        module = importlib.import_module(check.module)
    except Exception as exc:
        # A site that cannot be imported is MISSING with the real reason. It is
        # never LIVE, and never silently skipped.
        return InvariantReport(
            id=check.id,
            statement=check.statement,
            status=InvariantStatus.MISSING,
            module=check.module,
            symbol=check.symbol,
            spec_section=check.spec_section,
            reason=f"{type(exc).__name__}: {exc}",
        )
    attribute = getattr(module, check.symbol, None)
    if attribute is None:
        return InvariantReport(
            id=check.id,
            statement=check.statement,
            status=InvariantStatus.MISSING,
            module=check.module,
            symbol=check.symbol,
            spec_section=check.spec_section,
            reason=f"{check.module} does not expose {check.symbol!r}",
        )
    return InvariantReport(
        id=check.id,
        statement=check.statement,
        status=InvariantStatus.LIVE,
        module=check.module,
        symbol=check.symbol,
        spec_section=check.spec_section,
    )


def check_invariants(checks: tuple[InvariantCheck, ...] | None = None) -> list[InvariantReport]:
    """Probe module and symbol availability for every declared invariant.

    Returns one report per declared invariant, in declaration order. A caller
    that wants an aggregate should read :attr:`InvariantReport.live` itself —
    ``check_invariants`` deliberately does not collapse "12 declared" into a
    single number, because 12 declared and 12 live are different claims.
    """
    return [_probe(check) for check in (checks or INVARIANTS)]


def invariant_summary(reports: list[InvariantReport] | None = None) -> dict[str, Any]:
    """Aggregate declared-site availability without claiming runtime proof.

    ``live_count`` and ``declared_count`` travel together for the same reason
    ``direct_count`` and ``effective_count`` do in the group roster: a header
    claiming "12 invariants" over 9 live sites is a fabricated count.
    """
    resolved = reports if reports is not None else check_invariants()
    live = [r for r in resolved if r.live]
    missing = [r for r in resolved if not r.live]
    return {
        "declared": len(resolved),
        "live": len(live),
        "live_ids": [r.id for r in live],
        "missing_ids": [r.id for r in missing],
        "all_live": bool(resolved) and not missing,
        "probe_scope": "module_symbol_presence",
        "runtime_enforcement_verified": False,
        "invariants": [r.to_dict() for r in resolved],
    }
