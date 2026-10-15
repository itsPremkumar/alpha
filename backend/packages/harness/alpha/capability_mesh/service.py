"""The synchronous read/preview service both the tool and the router call.

One implementation, two surfaces. ``capability_mesh_tool`` renders these
payloads as text for the model and ``app/gateway.routers.capability_mesh``
serves them as JSON — and neither of them is allowed to re-derive an honesty
rule the other one already owns, because "the tool said unmeasured" beside "the
API said measured" is exactly the drift this module exists to prevent.

**Everything here is a local, read-only computation.** The service never calls
a model, never spends budget, never dispatches an agent run and never changes
a live assignment. Every ``preview`` payload carries ``dispatched: false`` and
the reason, so a reader cannot mistake a computed plan for work that ran.
The single write is :meth:`CapabilityMeshService.record`, which appends one
:class:`~alpha.capability_mesh.memory.CapabilityRecord` through the mesh's own
validation — a caller cannot smuggle a score past it.

The service is deliberately synchronous: every method touches the filesystem
(passports, memory, pair estimates, shadow comparisons) and both callers run
it off the event loop (``asyncio.to_thread``) rather than making every call
site remember that rule.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from alpha.capability_mesh.assignment import RoutingPlan, route
from alpha.capability_mesh.calibration import calibration
from alpha.capability_mesh.complementarity import PairMemory
from alpha.capability_mesh.config import CapabilityMeshConfig
from alpha.capability_mesh.decomposition import Decomposition, try_decompose
from alpha.capability_mesh.drift import detect_drift
from alpha.capability_mesh.explain import explain_plan
from alpha.capability_mesh.fingerprint import SituationFingerprint, fingerprint_task
from alpha.capability_mesh.graph import CapabilityGraph, build_from_memory, build_from_passports
from alpha.capability_mesh.independence import independence_score
from alpha.capability_mesh.memory import CapabilityMemory, CapabilityRecord, fingerprint
from alpha.capability_mesh.models import CapabilityPassport, EntityKind, EvidenceLevel, evidence_rank
from alpha.capability_mesh.policy import MAX_AGENT_COUNT, DecisionPolicy, PolicyProfile, merge_fingerprint_policy, resolve_policy
from alpha.capability_mesh.shadow import ShadowRunner
from alpha.capability_mesh.store import PassportStore, StoreState
from alpha.capability_mesh.synthesis import ArtifactRecord, ArtifactStore, compose_deliverable
from alpha.capability_mesh.team import select_team

#: Stamped on every preview payload. A reader who sees this cannot read the
#: payload as work that ran.
PREVIEW_NOTE = "PREVIEW computed locally from stored evidence; nothing was dispatched, no agent run was created, and no budget was spent."
#: Stamped beside every count, because a count without its evidence level is a
#: number nobody can weigh.
EVIDENCE_NOTE = "Every count below is reported with the evidence level behind it; unmeasured is captioned unmeasured, never smoothed into a measurement."

#: The mesh is a measurement plane. It has no executor, so this is a property of
#: the package rather than a setting an operator can turn off.
NO_DISPATCH_NOTE = "This package has no executor wiring: a routing plan, team plan, composition or explanation is a proposal, never a dispatch."

#: The same fact as a *machine-readable* capability declaration, on every payload
#: this service returns.
#:
#: ``dispatched: false`` says one reply did not dispatch. This says the surface
#: as a whole cannot dispatch, which is the claim an operator or a caller
#: actually has to evaluate when deciding whether to use the mesh. Both were
#: previously only prose — ``PREVIEW_NOTE`` and ``NO_DISPATCH_NOTE`` — so a
#: client that skipped the prose saw a payload with ``dispatched: false`` and
#: no way to tell a preview-only mesh from one that simply chose not to act on
#: this call. Stating it as a capability with a named owner and named missing
#: modules makes the absence checkable from the payload alone.
MESH_CAPABILITY = "capability_mesh_executor"
MESH_CAPABILITY_STATE = "absent"
MESH_CAPABILITY_NOTE = (
    "No executor is wired to the capability mesh. route()/select_team()/compose_deliverable()/explain_plan() are pure "
    "functions read locally; no module in alpha.capability_mesh dispatches an agent run through SubagentExecutor or "
    "RunManager. Treat every plan this package returns as a proposal."
)
#: The modules a reader would expect to find dispatch behind. Named so the
#: claim can be checked by reading them rather than taken on trust.
MESH_MISSING_EXECUTOR_MODULES = (
    "alpha.subagents.executor:SubagentExecutor",
    "alpha.runtime.runs:RunManager",
    "alpha.capability_mesh:dispatch",
)


#: Stamped on every payload: the honest capability row, ready to merge.
def mesh_capability_declaration() -> dict[str, Any]:
    """The mesh's own capability row, on every payload it returns."""
    return {
        "capability": MESH_CAPABILITY,
        "state": MESH_CAPABILITY_STATE,
        "dispatches": False,
        "note": MESH_CAPABILITY_NOTE,
        "missing_wiring": list(MESH_MISSING_EXECUTOR_MODULES),
        "owner_of_real_dispatch": "alpha.subagents.executor / alpha.runtime.runs (RunManager); neither reads the mesh",
    }


#: The caller-declared agent budget every ``resolve_policy`` is bounded by.
#: ``min(policy preference, default_agents)`` is the mesh's own stated rule, and
#: this surface declares no team-size field of its own, so the ceiling is the
#: policy table's own maximum rather than an invented number.
_POLICY_AGENT_BUDGET = MAX_AGENT_COUNT


def _evidence_summary(levels: list[EvidenceLevel]) -> dict[str, Any]:
    """The strongest level present plus how many capabilities sit at it.

    ``strongest`` is deliberately *not* the number a reader should quote: the
    mesh's own rule is that a passport reports its **worst** level, so a single
    unmeasured capability beside three verified ones is unmeasured for that
    capability. Both are reported so neither can be quoted alone.
    """
    if not levels:
        return {"strongest": None, "unmeasured_count": 0, "total": 0, "ranking": "no capabilities recorded"}
    strongest = max(levels, key=evidence_rank)
    return {
        "strongest": strongest.value,
        "unmeasured_count": sum(1 for level in levels if level is EvidenceLevel.UNMEASURED),
        "total": len(levels),
        "ranking": f"strongest={strongest.value}; read the WORST level per capability (worst_evidence_level) — a strong row never cancels an unmeasured one",
    }


class CapabilityMeshService:
    """Read and preview one mesh directory. Dispatches nothing."""

    def __init__(self, config: CapabilityMeshConfig) -> None:
        self._config = config

    @property
    def config(self) -> CapabilityMeshConfig:
        return self._config

    def capability(self) -> dict[str, Any]:
        """The mesh's own executor-wiring declaration.

        Read-only and constant: it describes the package, not the store. Both
        surfaces call this so the tool and the HTTP router cannot disagree about
        whether the mesh can dispatch — the one drift this module exists to
        prevent.
        """
        return mesh_capability_declaration()

    @property
    def directory(self) -> Path:
        return self._config.resolved_directory()

    # ------------------------------------------------------------------ stores

    def store(self) -> PassportStore:
        return PassportStore(self.directory)

    def memory(self) -> CapabilityMemory:
        return CapabilityMemory(self.directory, max_records=self._config.max_records)

    def shadow(self) -> ShadowRunner:
        return ShadowRunner(self.directory)

    def _store_state(self) -> StoreState:
        """Read the passport store, refusing a corrupt one by name.

        ``read_state()`` never raises: it reports ``unreadable=True`` with the
        reason. Turning that into an exception here is what keeps "the store
        could not be read" from travelling upward as an empty list — a caller
        that only saw ``passports`` would read corruption as "no passports".
        """
        state = self.store().read_state()
        if state.unreadable:
            from alpha.capability_mesh.store import PassportStoreError

            raise PassportStoreError(state.error or f"passport store '{self.directory}' is unreadable")
        return state

    def _passports(self) -> list[CapabilityPassport]:
        return list(self._store_state().passports)

    def _graph(self, passports: list[CapabilityPassport], memory: CapabilityMemory) -> CapabilityGraph:
        """Derive the capability graph from what the mesh already stores.

        Pure derivation over the same passport/memory pair the rest of the
        service reads: there is no second source and no extra persistence.
        """
        graph = build_from_passports(passports, profile_version=self._config.default_profile_version)
        return build_from_memory(memory, profile_version=self._config.default_profile_version, graph=graph)

    def _profile_version(self, requested: str) -> str:
        return requested.strip() or self._config.default_profile_version

    def _plan(
        self, task: str, *, policy: str, profile_version: str, required_capabilities: tuple[str, ...], required_tools: tuple[str, ...], default_agents: int | None = None
    ) -> tuple[SituationFingerprint, Decomposition, RoutingPlan, PolicyProfile]:
        """fingerprint -> try_decompose -> route, the package's own order.

        The decomposition uses the **heuristic baseline**: the model seam is
        injected by the caller and nothing here injects one, so the result
        carries its own ``heuristic_baseline`` provenance and the fallback
        disclosure ``try_decompose`` stamps onto it. No model call is made.
        """
        fingerprint = fingerprint_task(task, required_capabilities=required_capabilities, required_tools=required_tools)
        decomposition = try_decompose(fingerprint, task)
        resolved = resolve_policy(policy or DecisionPolicy.BALANCED, default_agents=default_agents if default_agents is not None else _POLICY_AGENT_BUDGET)
        merged = _merge_policy(fingerprint, resolved)
        plan = route(merged, decomposition, self._passports(), policy=resolved, memory=self.memory(), profile_version=profile_version)
        return merged, decomposition, plan, resolved

    # ------------------------------------------------------------------- reads

    def passports(self, *, entity_id: str = "", entity_type: str = "") -> dict[str, Any]:
        """Every stored passport with its evidence levels.

        A corrupt store raises ``PassportStoreError`` with the store's own
        reason; a genuinely empty store answers "none recorded", which is a
        different fact from "could not read".
        """
        state = self._store_state()
        rows = [passport for passport in state.passports if (not entity_id or passport.entity_id == entity_id) and (not entity_type or passport.entity_type == EntityKind(entity_type))]
        levels = [passport.overall_evidence_level() for passport in rows]
        return {
            "kind": "read",
            "directory": str(self.directory),
            "count": len(rows),
            "store_error": state.error,
            "evidence": _evidence_summary(levels),
            "passports": [passport.summary() for passport in rows],
            "mesh_capability": mesh_capability_declaration(),
            "notes": [EVIDENCE_NOTE, NO_DISPATCH_NOTE, MESH_CAPABILITY_NOTE],
        }

    def memory_report(self, *, entity_id: str = "", capability: str = "", task_category: str = "", limit: int = 50) -> dict[str, Any]:
        """Stored outcome records plus the conservative credit report.

        ``credit_for`` is a *starting policy*, not a measure of worth: an
        attempt nobody measured is still an attempt, and REJECTED/UNSELECTED
        earn nothing. The reply says so rather than letting a credit number
        read as a ranking.
        """
        memory = self.memory()
        rows = memory.records(entity_id=entity_id or None, capability=capability or None, task_category=task_category or None)
        bounded = rows[: max(0, limit)]
        entities = sorted({record.entity_id for record in bounded})
        return {
            "kind": "read",
            "record_count": len(rows),
            "returned": len(bounded),
            "truncated": len(rows) > len(bounded),
            "bound": limit,
            "records": [record.to_dict() for record in bounded],
            "credit": {entity: memory.credit_for(entity).to_dict() for entity in entities},
            "mesh_capability": mesh_capability_declaration(),
            "notes": [
                "credit is a declared starting policy ((selected + 0.5*repaired + 0.25*materially_edited) / attempts), not a measurement of worth: an unmeasured attempt is still an attempt",
                EVIDENCE_NOTE,
                NO_DISPATCH_NOTE,
                MESH_CAPABILITY_NOTE,
            ],
        }

    def calibration_report(self, *, profile_version: str = "") -> dict[str, Any]:
        """Does stated confidence mean anything, under one rubric only."""
        version = self._profile_version(profile_version)
        memory = self.memory()
        report = calibration(memory.records(), profile_version=version)
        payload = report.to_dict()
        verdict = payload["well_calibrated"]
        payload["verdict_note"] = {
            True: "the panel is large enough to answer and every populated bucket is within tolerance",
            False: "the panel is large enough to answer and at least one populated bucket is outside tolerance",
            None: "unresolved: too few comparable pairs to answer — never 'well calibrated'",
        }[verdict]
        payload["kind"] = "read"
        payload["mesh_capability"] = mesh_capability_declaration()
        payload["notes"] = [EVIDENCE_NOTE, NO_DISPATCH_NOTE, MESH_CAPABILITY_NOTE]
        return payload

    def drift_report(self, *, profile_version: str = "") -> dict[str, Any]:
        """Did performance move. Reports; never acts."""
        version = self._profile_version(profile_version)
        memory = self.memory()
        report = detect_drift(memory.records(), profile_version=version).to_dict()
        report["kind"] = "read"
        report["acts"] = False
        report["mesh_capability"] = mesh_capability_declaration()
        report["notes"] = [
            "detect_drift is a pure reader: it marks entities and preserves audit evidence, and mutates no record, passport, config or routing decision",
            EVIDENCE_NOTE,
            NO_DISPATCH_NOTE,
            MESH_CAPABILITY_NOTE,
        ]
        return report

    def policy_table(self, *, policy: str = "", default_agents: int | None = None) -> dict[str, Any]:
        """The explicit ``resolve_policy`` resolution table.

        A TABLE, not intelligence: one inspectable row per policy. ``LOCAL``
        declares a locality intent the mesh cannot verify, and the row says so.
        """
        budget = default_agents if default_agents is not None else _POLICY_AGENT_BUDGET
        rows = [resolve_policy(member.value, default_agents=budget).to_dict() for member in DecisionPolicy]
        requested = resolve_policy(policy or DecisionPolicy.BALANCED, default_agents=budget)
        return {
            "kind": "read",
            "resolved": requested.to_dict(),
            "default_agents": budget,
            "policies": rows,
            "exploration_budget_fraction": self._config.exploration_budget_fraction,
            "exploration_note": "declared, not spent: nothing in this package executes a probe, so this number is reported rather than consumed",
            "mesh_capability": mesh_capability_declaration(),
            "notes": [
                "resolve_policy reads one explicit row per policy; an unknown name is refused listing the valid ones, and bounds are refused, never clamped",
                DecisionPolicy.LOCAL.value + " declares an intent the mesh cannot enforce: it has no runtime locality constraint to check today",
                NO_DISPATCH_NOTE,
                MESH_CAPABILITY_NOTE,
            ],
        }

    # ---------------------------------------------------------------- previews

    def route_preview(self, *, task: str, policy: str = "", profile_version: str = "", required_capabilities: tuple[str, ...] = (), required_tools: tuple[str, ...] = (), default_agents: int | None = None) -> dict[str, Any]:
        """fingerprint -> try_decompose -> route, as a PREVIEW."""
        if not task.strip():
            raise ValueError("route requires non-empty task text")
        version = self._profile_version(profile_version)
        fingerprint, decomposition, plan, resolved = self._plan(task, policy=policy, profile_version=version, required_capabilities=required_capabilities, required_tools=required_tools, default_agents=default_agents)
        return _preview_payload(
            {
                "profile_version": version,
                "policy": resolved.to_dict(),
                "fingerprint": fingerprint.to_dict(),
                "decomposition": decomposition.to_dict(),
                "plan": plan.to_dict(),
            },
            extra_notes=[
                "the decomposition is the heuristic baseline: no model seam is injected here, so the disclosure try_decompose stamps onto it travels with the result",
            ],
        )

    def team_preview(self, *, task: str, policy: str = "", profile_version: str = "", required_capabilities: tuple[str, ...] = (), budget_usd: float | None = None, max_team_size: int | None = None) -> dict[str, Any]:
        """``select_team`` over the stored passports, as a PREVIEW."""
        if not task.strip():
            raise ValueError("team requires non-empty task text")
        version = self._profile_version(profile_version)
        fingerprint = fingerprint_task(task, required_capabilities=required_capabilities)
        resolved = resolve_policy(policy or DecisionPolicy.BALANCED, default_agents=_POLICY_AGENT_BUDGET)
        memory = self.memory()
        passports = self._passports()
        plan = select_team(
            fingerprint,
            passports,
            policy=resolved,
            memory=memory,
            graph=self._graph(passports, memory),
            profile_version=version,
            budget_usd=budget_usd,
            max_team_size=max_team_size,
        )
        return _preview_payload(
            {
                "profile_version": version,
                "policy": resolved.to_dict(),
                "required_capabilities": list(fingerprint.required_capabilities),
                "plan": plan.to_dict(),
            },
            extra_notes=[
                "total_predicted_cost is None whenever any member is unpriced — an unpriced member is never a free one",
            ],
        )

    def compose_preview(self, *, task: str, artifacts: list[dict[str, Any]] | None = None, policy: str = "", profile_version: str = "", required_capabilities: tuple[str, ...] = ()) -> dict[str, Any]:
        """``compose_deliverable`` over caller-supplied artifacts, as a PREVIEW.

        The artifacts are staged into a **scratch** ``ArtifactStore`` inside a
        temporary directory and discarded with it: composition is the mesh's
        one read-only preview that needs an artifact mapping, and the mesh has
        exactly one write (``record``). Nothing here persists an artifact.
        """
        if not task.strip():
            raise ValueError("compose requires non-empty task text")
        version = self._profile_version(profile_version)
        _, _, plan, resolved = self._plan(task, policy=policy, profile_version=version, required_capabilities=required_capabilities, required_tools=())
        with tempfile.TemporaryDirectory(prefix="alpha-mesh-compose-") as scratch:
            store = ArtifactStore(Path(scratch))
            for entry in artifacts or []:
                store.put(ArtifactRecord.from_dict(entry))
            result = compose_deliverable(plan, store, profile_version=version)
        return _preview_payload(
            {
                "profile_version": version,
                "policy": resolved.to_dict(),
                "composition": result.to_dict(),
            },
            extra_notes=[
                "supplied artifacts were staged into a scratch store and discarded; the mesh persists no artifact from a preview",
                "composed_artifact stays null while any slot holds a CONFLICT, and every conflict carries the alternatives it would need",
            ],
        )

    def explain_preview(self, *, task: str, policy: str = "", profile_version: str = "", required_capabilities: tuple[str, ...] = (), calls: int = 0, estimated_cost_usd: float | None = None) -> dict[str, Any]:
        """``explain_plan`` over the same locally computed plan, as a PREVIEW."""
        if not task.strip():
            raise ValueError("explain requires non-empty task text")
        version = self._profile_version(profile_version)
        _, _, plan, _ = self._plan(task, policy=policy, profile_version=version, required_capabilities=required_capabilities, required_tools=())
        bundle = explain_plan(plan, memory=self.memory(), calls=calls, estimated_cost_usd=estimated_cost_usd, profile_version=version)
        return _preview_payload(
            {
                "profile_version": version,
                "bundle": bundle.to_dict(),
            },
            extra_notes=[
                "confidence captions are derived from the evidence level and sample count, so an unmeasured capability reads 'unmeasured (0 samples)'",
            ],
        )

    # ------------------------------------------------------- correlated error

    def independence_report(self, *, entity_a: str, entity_b: str, task_category: str = "", profile_version: str = "") -> dict[str, Any]:
        """Correlated-error awareness for one pair, from what was measured.

        ``known=False`` is a real answer: nothing was measured, which is
        neither independent nor dependent. The mesh never rounds unknown into
        safe.
        """
        if not entity_a.strip() or not entity_b.strip():
            raise ValueError("independence requires both entity_a and entity_b")
        version = self._profile_version(profile_version)
        memory = self.memory()
        passports = self._passports()
        score = independence_score(
            self._graph(passports, memory),
            PairMemory(self.directory),
            entity_a,
            entity_b,
            task_category=task_category or "general",
            profile_version=version,
        )
        payload = score.to_dict()
        payload["kind"] = "read"
        payload["mesh_capability"] = mesh_capability_declaration()
        payload["notes"] = [
            "known=False means nothing was measured, never 'safe': only model_family and model_provider may be marked shared by a documented weak prior",
            NO_DISPATCH_NOTE,
            MESH_CAPABILITY_NOTE,
        ]
        return payload

    def shadow_report(self, *, profile_version: str = "") -> dict[str, Any]:
        """The shadow runner's own report. Predictions, never budget."""
        version = self._profile_version(profile_version)
        report = self.shadow().report(version).to_dict()
        report["kind"] = "read"
        report["spends_budget"] = False
        report["mesh_capability"] = mesh_capability_declaration()
        report["notes"] = [
            "the shadow runner stores comparisons and never calls a model, never spends budget and never changes a live assignment; the candidate router is a caller-injected callable",
            NO_DISPATCH_NOTE,
            MESH_CAPABILITY_NOTE,
        ]
        return report

    # ------------------------------------------------------------------- write

    def record(
        self,
        *,
        entity_id: str,
        capability: str,
        outcome: str,
        task_fingerprint: str = "",
        task_text: str = "",
        task_category: str = "",
        quality: float | None = None,
        confidence: float | None = None,
        passed_hard_gate: bool | None = None,
        latency_ms: int | None = None,
        cost_usd: float | None = None,
        failure_mode: str = "",
        slot: str = "",
        profile_version: str = "",
        record_id: str = "",
    ) -> dict[str, Any]:
        """Append one validated :class:`CapabilityRecord`.

        The **mesh's own constructor** is the only validator: a ``quality``
        without a ``profile_version`` is refused by name here rather than
        accepted and quietly downgraded, because the profile version is what
        makes a score comparable later.

        No task text is ever stored. ``task_text`` is accepted only to derive
        the opaque ``fingerprint(text)`` the memory contract asks for, and the
        raw prompt never reaches a record; prefer passing ``task_fingerprint``
        directly when the caller already has one.
        """
        version = self._profile_version(profile_version)
        opaque = task_fingerprint.strip() or (fingerprint(task_text) if task_text.strip() else "")
        if not opaque:
            raise ValueError("record requires task_fingerprint (or task_text, which is hashed into an opaque fingerprint — raw task text is never stored)")
        record = CapabilityRecord(
            record_id=record_id.strip() or fingerprint(f"{opaque}|{entity_id}|{capability}|{version}"),
            task_fingerprint=opaque,
            task_category=task_category.strip() or "general",
            entity_id=entity_id,
            capability=capability,
            outcome=outcome,
            recorded_at=_now_iso(),
            slot=slot.strip() or None,
            quality=quality,
            passed_hard_gate=passed_hard_gate,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            failure_mode=failure_mode.strip() or None,
            profile_version=version,
            confidence=confidence,
        )
        memory = self.memory()
        memory.record(record)
        estimate = memory.estimate_for(record.entity_id, record.capability, record.task_category, profile_version=version)
        return {
            "kind": "write",
            "recorded": record.to_dict(),
            "estimate_after": estimate.to_dict(),
            "evidence": estimate.evidence_level.value,
            "mesh_capability": mesh_capability_declaration(),
            "notes": [
                "appended through the mesh's own validation: a quality without a profile_version is refused here, never stored",
                "a stated confidence is a calibration signal, not evidence — only a measured quality is",
                "this write records an observation; it does not dispatch, schedule or assign anything",
                MESH_CAPABILITY_NOTE,
            ],
        }


# ------------------------------------------------------------------- helpers


def _merge_policy(fingerprint: SituationFingerprint, profile: PolicyProfile) -> SituationFingerprint:
    return merge_fingerprint_policy(fingerprint, profile)


def _preview_payload(body: dict[str, Any], *, extra_notes: list[str] | None = None) -> dict[str, Any]:
    return {
        "kind": "preview",
        "preview": True,
        "dispatched": False,
        "preview_note": PREVIEW_NOTE,
        "mesh_capability": mesh_capability_declaration(),
        **body,
        "notes": [*(extra_notes or []), EVIDENCE_NOTE, NO_DISPATCH_NOTE, MESH_CAPABILITY_NOTE],
    }


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


__all__ = [
    "EVIDENCE_NOTE",
    "MESH_CAPABILITY",
    "MESH_CAPABILITY_NOTE",
    "MESH_CAPABILITY_STATE",
    "MESH_MISSING_EXECUTOR_MODULES",
    "NO_DISPATCH_NOTE",
    "PREVIEW_NOTE",
    "CapabilityMeshService",
    "mesh_capability_declaration",
]
